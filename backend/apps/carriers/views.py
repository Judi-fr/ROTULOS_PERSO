"""La API común de transportistas para el comerciante, bajo ``/api/v1/carriers/``.
Lo propio de cada uno lo resuelve su ``CarrierProvider`` (``providers.py``); acá
no se nombra a ninguno.

- ``GET carriers/``: los transportistas, qué pide cada cuenta y el estado de
  la del usuario (Mis pedidos arma con esto el diálogo Despachar).
- ``accounts/<carrier>/`` GET/PUT, ``accounts/<carrier>/test/`` POST: la
  cuenta (las claves nunca vuelven: ``has_<campo>``).
- ``branches/<carrier>/?cp=&state=`` GET: sucursales que entregan ahí.
- ``shipments/`` GET (los envíos propios de todos los transportistas,
  ``?order=``/``?status=``/``?carrier=``) y POST ``{carrier, order_ids,
  contract, branches?, package_count?, packages?}``; ``shipments/quote/`` POST
  (lo mismo, sin crear nada). ``packages`` = ``{order_id: bultos}`` para los
  pedidos que no llevan los mismos bultos que el resto (``package_count``).
- ``shipments/print/`` POST ``{shipment_ids}``: un PDF con nuestro rótulo y la
  etiqueta del transportista de cada envío, de cualquier transportista.
- ``shipments/<id>/`` GET, ``.../label/?file_type=pdf|zpl``, ``.../refresh/``,
  ``.../cancel/`` POST.

Andreani conserva además sus rutas propias (``/carriers/andreani/...``,
``andreani/urls.py``: su checkout y su API original). Por eso las comunes van
con el transportista DESPUÉS de un prefijo (``accounts/andreani/``) y no antes
(``andreani/account/`` es la vista vieja).

Todo con ``orders.create`` (es despachar) y siempre sobre lo del usuario.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.orders.models import Order
from apps.accounts.teams import data_owner, order_scope_q

from . import common, printing
from .base import CarrierError, ShipmentError
from .models import CarrierAccount, CarrierShipment
from .providers import all_providers, get_provider

MAX_ORDERS = 100
SENDER_FIELDS = (
    "sender_name", "sender_email", "sender_phone", "sender_document",
    "origin_street", "origin_number", "origin_floor", "origin_apartment", "origin_postal_code", "origin_city", "origin_state",
)
POSITIVE_INT_FIELDS = {
    "default_volume_cm3": (1, 10_000_000, "Poné un volumen en cm³ mayor a cero."),
    "default_length_cm": (1, 300, "Las medidas van de 1 a 300 cm."),
    "default_width_cm": (1, 300, "Las medidas van de 1 a 300 cm."),
    "default_height_cm": (1, 300, "Las medidas van de 1 a 300 cm."),
}


class _CarrierView(APIView):
    # Qué exige cada vista (comercios con equipo, apps/accounts/teams.py):
    # despachar es una cosa y administrar las cuentas y el checkout otra, y
    # leer (el estado de los envíos en Mis pedidos) alcanza con ver pedidos.
    read_permission = "orders.dispatch"
    write_permission = "orders.dispatch"

    def get_permissions(self):
        permission = self.read_permission if self.request.method in SAFE_METHODS else self.write_permission
        return [IsAuthenticated(), HasRolePermission(permission)]

    def provider_or_404(self, code):
        return get_provider(code)

    def account(self, request, provider):
        return CarrierAccount.objects.filter(owner=data_owner(request.user), carrier=provider.code).first()


def account_data(provider, account):
    data = {name: getattr(account, name) for name in SENDER_FIELDS}
    for item in provider.account_fields:
        if item.is_secret:
            data[f"has_{item.name}"] = provider.has_field(account, item)
        else:
            data[item.name] = provider.field_value(account, item)
    missing = provider.account_problems(account)
    quote_missing = provider.credential_problems(account, "quote") if provider.supports_quote else []
    data.update(
        {
            "carrier": provider.code,
            "environment": account.environment,
            "contracts": account.contracts or [],
            "default_weight_kg": str(account.default_weight_kg),
            "default_volume_cm3": account.default_volume_cm3,
            "default_length_cm": account.default_length_cm,
            "default_width_cm": account.default_width_cm,
            "default_height_cm": account.default_height_cm,
            "is_active": account.is_active,
            "last_error": account.last_error,
            "missing": missing,
            "quote_missing": quote_missing,
            "ready": bool(account.pk) and account.is_active and not missing and not account.last_error,
            "exists": bool(account.pk),
        }
    )
    return data


def shipment_data(shipment, *, events=False):
    provider = get_provider(shipment.carrier)
    data = {
        "id": shipment.pk,
        "carrier": shipment.carrier,
        "carrier_name": shipment.get_carrier_display(),
        "order_id": shipment.order_id,
        "order_number": common.order_number(shipment.order),
        "recipient": shipment.order.address.recipient_name if shipment.order.address_id else "",
        "tracking_number": shipment.tracking_number,
        "tracking_url": provider.tracking_url(shipment.tracking_number) if provider else "",
        "contract": shipment.contract,
        "delivery_kind": shipment.delivery_kind,
        "branch_name": shipment.branch_name,
        "package_count": shipment.package_count,
        "quoted_price": str(shipment.quoted_price) if shipment.quoted_price is not None else None,
        "status": shipment.status,
        "status_label": shipment.get_status_display(),
        "carrier_status": shipment.carrier_status,
        "last_event_at": shipment.last_event_at,
        "last_checked_at": shipment.last_checked_at,
        "last_error": shipment.last_error,
        "label_formats": list(provider.label_formats) if provider else ["pdf"],
        "can_cancel": bool(provider and provider.supports_cancel and shipment.status == CarrierShipment.Status.PENDING),
        "created_at": shipment.created_at,
    }
    if events:
        data["events"] = [
            {
                "occurred_at": event.occurred_at,
                "event": event.event,
                "status_text": event.status_text,
                "reason": event.reason,
                "sub_reason": event.sub_reason,
                "branch": event.branch,
                "comment": event.comment,
            }
            for event in shipment.events.all()
        ]
    return data


class CarrierListView(_CarrierView):
    read_permission = "orders.view"
    write_permission = "orders.view"

    def get(self, request):
        accounts = {account.carrier: account for account in CarrierAccount.objects.filter(owner=data_owner(request.user))}
        results = []
        for provider in all_providers():
            account = accounts.get(provider.code) or CarrierAccount(owner=data_owner(request.user), carrier=provider.code)
            results.append(dict(provider.describe(), account=account_data(provider, account)))
        return Response({"results": results})


class AccountView(_CarrierView):
    read_permission = "orders.dispatch"
    write_permission = "carriers.manage"

    def get(self, request, carrier):
        provider = self.provider_or_404(carrier)
        if provider is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        account = self.account(request, provider) or CarrierAccount(
            owner=data_owner(request.user), carrier=provider.code, contracts=[dict(item) for item in provider.normalize_contracts(provider.default_contracts)]
        )
        return Response(dict(account_data(provider, account), carrier_info=provider.describe()))

    def put(self, request, carrier):
        provider = self.provider_or_404(carrier)
        if provider is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        account = self.account(request, provider) or CarrierAccount(owner=data_owner(request.user), carrier=provider.code)
        errors = {}
        credentials_changed = False

        for name in SENDER_FIELDS:
            if name in data:
                limit = CarrierAccount._meta.get_field(name).max_length
                setattr(account, name, str(data.get(name) or "").strip()[:limit])
        extra = dict(account.extra or {})
        for item in provider.account_fields:
            if item.name not in data:
                continue
            value = str(data.get(item.name) or "").strip()
            if item.choices and value and value not in {choice for choice, _label in item.choices}:
                errors[item.name] = ["Elegí una de las opciones."]
                continue
            if item.storage == "password":
                if value:
                    account.password = value
                    credentials_changed = True
            elif item.storage == "secret":
                if value:
                    account.set_secret(item.name, value)
                    credentials_changed = True
            elif item.storage == "column":
                limit = CarrierAccount._meta.get_field(item.name).max_length
                if getattr(account, item.name) != value[:limit]:
                    credentials_changed = True
                setattr(account, item.name, value[:limit])
            else:
                extra[item.name] = value[: item.max_length]
        account.extra = extra
        if "environment" in data:
            if data["environment"] not in CarrierAccount.Environment.values:
                errors["environment"] = ["Elegí producción o pruebas."]
            else:
                credentials_changed = credentials_changed or account.environment != data["environment"]
                account.environment = data["environment"]
        if "contracts" in data:
            try:
                account.contracts = provider.normalize_contracts(data.get("contracts") or [])
            except ValueError as exc:
                errors["contracts"] = [str(exc)]
        if "default_weight_kg" in data:
            try:
                weight = Decimal(str(data["default_weight_kg"]).replace(",", "."))
                if not weight.is_finite() or weight <= 0 or weight > 1000:
                    raise InvalidOperation
                account.default_weight_kg = weight
            except (InvalidOperation, ValueError):
                errors["default_weight_kg"] = ["Poné un peso en kilos mayor a cero."]
        for name, (minimum, maximum, message) in POSITIVE_INT_FIELDS.items():
            if name in data:
                try:
                    number = int(data[name])
                    if number < minimum or number > maximum:
                        raise ValueError
                    setattr(account, name, number)
                except (TypeError, ValueError):
                    errors[name] = [message]
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        created = account.pk is None
        # Guardar la cuenta la reactiva: es lo que hace el comerciante después
        # de corregir unas credenciales que el transportista rechazó.
        account.is_active = True
        account.last_error = ""
        if credentials_changed:
            account.token = ""
            account.token_expires_at = None
        account.save()
        record(
            request,
            category="integrations",
            action="store.update",
            target=account,
            target_type="carrieraccount",
            target_repr=str(account),
            changes={provider.code: {"from": None, "to": "cuenta creada" if created else "cuenta actualizada"}},
        )
        return Response(
            dict(account_data(provider, account), carrier_info=provider.describe()),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AccountTestView(_CarrierView):
    read_permission = "carriers.manage"
    write_permission = "carriers.manage"

    def post(self, request, carrier):
        provider = self.provider_or_404(carrier)
        if provider is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        account = self.account(request, provider)
        if account is None:
            return Response({"detail": f"Primero guardá tu cuenta de {provider.name}."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = provider.test_credentials(account)
        except (CarrierError, ShipmentError) as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(dict(result, ok=True))


class BranchesView(_CarrierView):
    def get(self, request, carrier):
        provider = self.provider_or_404(carrier)
        if provider is None or not provider.supports_branches:
            return Response(status=status.HTTP_404_NOT_FOUND)
        raw_postal_code = str(request.query_params.get("cp") or "")
        if not common.postal_code_digits(raw_postal_code):
            return Response({"cp": ["Indicá un código postal."]}, status=status.HTTP_400_BAD_REQUEST)
        account = self.account(request, provider) or CarrierAccount(owner=data_owner(request.user), carrier=provider.code)
        try:
            # El CP va como vino: un CPA (``C1602ABC``) trae la provincia.
            branches = provider.branches(account, raw_postal_code, state=str(request.query_params.get("state") or ""))
        except ShipmentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except CarrierError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"results": branches})


def _requested_orders(request, data):
    """``(provider, account, ids, orders_by_id, package_count)`` del cuerpo, o
    una ``Response`` 400."""
    provider = get_provider(data.get("carrier"))
    if provider is None:
        return Response({"carrier": ["Elegí el transportista."]}, status=status.HTTP_400_BAD_REQUEST)
    account = CarrierAccount.objects.filter(owner=data_owner(request.user), carrier=provider.code).first()
    if account is None:
        return Response({"detail": f"Primero cargá tu cuenta de {provider.name}."}, status=status.HTTP_400_BAD_REQUEST)
    ids = data.get("order_ids")
    if not isinstance(ids, list) or not ids:
        return Response({"order_ids": ["Elegí al menos un pedido."]}, status=status.HTTP_400_BAD_REQUEST)
    if len(ids) > MAX_ORDERS:
        return Response({"order_ids": [f"Se pueden procesar hasta {MAX_ORDERS} pedidos por vez."]}, status=status.HTTP_400_BAD_REQUEST)
    package_count = _clamp_packages(data.get("package_count"))
    orders = {
        order.pk: order
        for order in Order.objects.filter(order_scope_q(request.user), pk__in=[i for i in ids if str(i).isdigit()], user=data_owner(request.user)).select_related("address")
    }
    return provider, account, ids, orders, package_count


MAX_PACKAGES = 20


def _clamp_packages(value, default=1):
    try:
        return min(max(int(value or default), 1), MAX_PACKAGES)
    except (TypeError, ValueError):
        return default


def _packages_for(order, data, default):
    """Los bultos de ``order``: los suyos de ``packages`` si vinieron (cada
    pedido puede llevar distintos), si no los de todos (``package_count``)."""
    packages = data.get("packages") if isinstance(data.get("packages"), dict) else {}
    own = packages.get(str(order.pk), packages.get(order.pk))
    return _clamp_packages(own, default) if own not in (None, "") else default


def _money(value):
    return str(value) if value is not None else None


class ShipmentQuoteView(_CarrierView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        parsed = _requested_orders(request, data)
        if isinstance(parsed, Response):
            return parsed
        provider, account, ids, orders, package_count = parsed
        if not provider.supports_quote:
            return Response({"detail": f"{provider.name} no cotiza desde acá."}, status=status.HTTP_400_BAD_REQUEST)
        results, total, quoted = [], Decimal("0"), 0
        for raw_id in ids:
            order = orders.get(int(raw_id)) if str(raw_id).isdigit() else None
            if order is None:
                results.append({"order_id": raw_id, "ok": False, "detail": "El pedido no existe o no es tuyo."})
                continue
            number = common.order_number(order)
            try:
                quote = provider.quote_order(
                    account, order, data.get("contract"), package_count=_packages_for(order, data, package_count)
                )
            except ShipmentError as exc:
                results.append({"order_id": order.pk, "number": number, "ok": False, "detail": str(exc)})
                continue
            quoted += 1
            total += quote["price"]
            results.append(
                {
                    "order_id": order.pk,
                    "number": number,
                    "ok": True,
                    "detail": f"$ {quote['price']}",
                    "price": _money(quote["price"]),
                    "price_without_tax": _money(quote.get("price_without_tax")),
                    "delivery_days": quote.get("delivery_days"),
                }
            )
        return Response({"quoted": quoted, "failed": len(ids) - quoted, "total": _money(total), "results": results})


class ShipmentCollectionView(_CarrierView):
    read_permission = "orders.view"
    write_permission = "orders.dispatch"

    def get(self, request):
        queryset = CarrierShipment.objects.filter(order_scope_q(request.user, "order__"), account__owner=data_owner(request.user)).select_related("order", "order__address")
        carrier = str(request.query_params.get("carrier") or "")
        if carrier:
            queryset = queryset.filter(carrier=carrier)
        order = str(request.query_params.get("order") or "")
        if order.isdigit():
            queryset = queryset.filter(order_id=int(order))
        ids = [value for value in str(request.query_params.get("orders") or "").split(",") if value.isdigit()]
        if ids:
            queryset = queryset.filter(order_id__in=ids[:200])
        statuses = [value for value in str(request.query_params.get("status") or "").split(",") if value in CarrierShipment.Status.values]
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        return Response({"results": [shipment_data(shipment) for shipment in queryset[:200]]})

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        parsed = _requested_orders(request, data)
        if isinstance(parsed, Response):
            return parsed
        provider, account, ids, orders, package_count = parsed
        branches = data.get("branches") if isinstance(data.get("branches"), dict) else {}
        results, created = [], 0
        for raw_id in ids:
            order = orders.get(int(raw_id)) if str(raw_id).isdigit() else None
            if order is None:
                results.append({"order_id": raw_id, "ok": False, "detail": "El pedido no existe o no es tuyo."})
                continue
            branch = branches.get(str(order.pk)) or branches.get(order.pk) or {}
            try:
                shipment = provider.create_shipment(
                    request,
                    account,
                    order,
                    data.get("contract"),
                    branch_id=str(branch.get("id") or ""),
                    branch_name=str(branch.get("name") or ""),
                    package_count=_packages_for(order, data, package_count),
                )
            except ShipmentError as exc:
                results.append({"order_id": order.pk, "number": common.order_number(order), "ok": False, "detail": str(exc)})
                continue
            created += 1
            results.append(dict(shipment_data(shipment), order_id=order.pk, number=common.order_number(order), ok=True, detail="Envío creado."))
        return Response({"created": created, "failed": len(ids) - created, "results": results})


class _OwnShipmentView(_CarrierView):
    def shipment(self, request, pk):
        return (
            CarrierShipment.objects.filter(order_scope_q(request.user, "order__"), pk=pk, account__owner=data_owner(request.user))
            .select_related("account", "order", "order__address")
            .first()
        )


class ShipmentDetailView(_OwnShipmentView):
    read_permission = "orders.view"
    write_permission = "orders.dispatch"

    def get(self, request, pk):
        shipment = self.shipment(request, pk)
        if shipment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(shipment_data(shipment, events=True))


def file_response(content, fmt, filename):
    content_type = "application/zpl" if fmt == "zpl" else "application/pdf"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


class ShipmentLabelView(_OwnShipmentView):
    def get(self, request, pk):
        shipment = self.shipment(request, pk)
        if shipment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        provider = get_provider(shipment.carrier)
        fmt = "zpl" if request.query_params.get("file_type") == "zpl" else "pdf"
        if fmt not in provider.label_formats:
            return Response({"file_type": [f"{provider.name} no da la etiqueta en ese formato."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            content = provider.label(shipment, fmt)
        except (CarrierError, ShipmentError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return file_response(content, fmt, f"{shipment.carrier}-{shipment.tracking_number}.{fmt}")


class ShipmentPrintView(_CarrierView):
    """Un PDF para imprimir de una vez: el rótulo de cada pedido seguido de la
    etiqueta de su transportista (``printing.bundle``)."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        ids = [int(i) for i in data.get("shipment_ids") or [] if str(i).isdigit()][:MAX_ORDERS]
        found = {
            shipment.pk: shipment
            for shipment in CarrierShipment.objects.filter(order_scope_q(request.user, "order__"), pk__in=ids, account__owner=data_owner(request.user))
            .exclude(status=CarrierShipment.Status.CANCELLED)
            .select_related("account", "order", "order__address", "order__store_connection", "order__user")
        }
        # En el orden en que se pidieron: es el orden de las hojas.
        shipments = [found[pk] for pk in dict.fromkeys(ids) if pk in found]
        if not shipments:
            return Response({"shipment_ids": ["Elegí al menos un envío."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            content = printing.bundle(shipments)
        except (CarrierError, ShipmentError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        name = f"envio-{shipments[0].tracking_number}.pdf" if len(shipments) == 1 else f"envios-{len(shipments)}.pdf"
        return file_response(content, "pdf", name)


class ShipmentRefreshView(_OwnShipmentView):
    def post(self, request, pk):
        shipment = self.shipment(request, pk)
        if shipment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            get_provider(shipment.carrier).sync_shipment(shipment)
        except (CarrierError, ShipmentError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        shipment.refresh_from_db()
        return Response(shipment_data(shipment, events=True))


class ShipmentCancelView(_OwnShipmentView):
    def post(self, request, pk):
        shipment = self.shipment(request, pk)
        if shipment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        provider = get_provider(shipment.carrier)
        if not provider.supports_cancel:
            return Response({"detail": f"{provider.name} no permite cancelar desde acá."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            provider.cancel_shipment(request, shipment)
        except ShipmentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(shipment_data(shipment))
