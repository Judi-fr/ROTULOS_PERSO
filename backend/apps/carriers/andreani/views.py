"""API de Andreani para el comerciante. Se monta en ``/api/v1/carriers/andreani/``.

- ``account/`` GET/PUT: su cuenta de Andreani (credenciales, contratos,
  remitente, origen, paquete por defecto). La contraseña nunca se devuelve.
- ``account/test/`` POST: prueba el usuario y la contraseña contra Andreani.
- ``branches/?cp=`` GET: sucursales y puntos HOP que atienden ese código postal.
- ``shipments/`` GET (los envíos propios, ``?order=``/``?status=``) y POST
  ``{order_ids, contract, branches?: {order_id: {id, name}}, package_count?}``:
  crea un envío por pedido; uno que falla no frena a los demás.
- ``shipments/quote/`` POST ``{order_ids, contract, package_count?}``: cuánto
  cobra Andreani por cada pedido (y el total), sin crear nada. Lo mismo que
  después se manda al crear.
- ``shipments/<id>/`` GET (con sus movimientos), ``.../label/?file_type=pdf|zpl``,
  ``.../refresh/`` POST (seguimiento ya), ``.../cancel/`` POST.
- ``shipments/labels/`` POST ``{shipment_ids, file_type}``: las etiquetas de varios
  envíos (ZPL en un solo archivo; PDF en un zip, uno por envío).

- ``checkout/`` GET (las tiendas propias cuyo checkout nos pregunta el precio,
  con su configuración) y PUT ``{store, enabled, contract, name,
  delivery_days_min?, delivery_days_max?}``: ofrecer Andreani en el checkout
  de esa tienda (``checkout.py``).
- ``checkout/test/`` POST ``{store, postal_code, weight_kg?}``: lo que cotizaría.

Todo con ``orders.create`` (es despachar) y siempre sobre lo del usuario.
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal, InvalidOperation

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.orders.models import Order

from ..models import CarrierAccount, CarrierShipment
from apps.integrations.models import StoreConnection
from apps.integrations.providers import get_provider

from . import checkout as checkout_service
from . import printing
from . import shipments as service
from .client import AndreaniClient, AndreaniError

PERMISSION = "orders.create"
MAX_ORDERS = 100
ANDREANI = CarrierAccount.Carrier.ANDREANI
ACCOUNT_TEXT_FIELDS = (
    "username", "client_code", "sender_name", "sender_email", "sender_phone", "sender_document",
    "origin_street", "origin_number", "origin_floor", "origin_apartment", "origin_postal_code", "origin_city",
)
CONTRACT_KINDS = {"home", "branch"}


class _AndreaniView(APIView):
    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(PERMISSION)]

    def account(self, request):
        return CarrierAccount.objects.filter(owner=request.user, carrier=ANDREANI).first()


def account_data(account):
    data = {field: getattr(account, field) for field in ACCOUNT_TEXT_FIELDS}
    data.update(
        {
            "environment": account.environment,
            "contracts": account.contracts or [],
            "default_weight_kg": str(account.default_weight_kg),
            "default_volume_cm3": account.default_volume_cm3,
            "has_password": bool(account.password_encrypted),
            "is_active": account.is_active,
            "last_error": account.last_error,
            "missing": service.account_problems(account),
        }
    )
    return data


def shipment_data(shipment, *, events=False):
    data = {
        "id": shipment.pk,
        "order_id": shipment.order_id,
        "order_number": shipment.order.external_number or str(shipment.order_id),
        "recipient": shipment.order.address.recipient_name if shipment.order.address_id else "",
        "tracking_number": shipment.tracking_number,
        "tracking_url": service.tracking_url_for(shipment.tracking_number),
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


class AccountView(_AndreaniView):
    def get(self, request):
        account = self.account(request) or CarrierAccount(owner=request.user, carrier=ANDREANI)
        return Response(dict(account_data(account), exists=bool(account.pk)))

    def put(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        account = self.account(request) or CarrierAccount(owner=request.user, carrier=ANDREANI)
        errors = {}

        for field in ACCOUNT_TEXT_FIELDS:
            if field in data:
                limit = CarrierAccount._meta.get_field(field).max_length
                setattr(account, field, str(data.get(field) or "").strip()[:limit])
        if data.get("password"):
            account.password = str(data["password"])
        if "environment" in data:
            if data["environment"] not in CarrierAccount.Environment.values:
                errors["environment"] = ["Elegí producción o pruebas."]
            else:
                account.environment = data["environment"]
        if "contracts" in data:
            contracts = []
            for item in data.get("contracts") or []:
                code = str((item or {}).get("code") or "").strip()
                kind = str((item or {}).get("kind") or "home")
                if not code:
                    continue
                if kind not in CONTRACT_KINDS:
                    errors["contracts"] = ["Cada contrato es de entrega a domicilio o en sucursal."]
                    break
                contracts.append({"code": code[:50], "label": str(item.get("label") or "").strip()[:80], "kind": kind})
            account.contracts = contracts
        if "default_weight_kg" in data:
            try:
                weight = Decimal(str(data["default_weight_kg"]))
                if weight <= 0:
                    raise InvalidOperation
                account.default_weight_kg = weight
            except (InvalidOperation, ValueError):
                errors["default_weight_kg"] = ["Poné un peso en kilos mayor a cero."]
        if "default_volume_cm3" in data:
            try:
                volume = int(data["default_volume_cm3"])
                if volume <= 0:
                    raise ValueError
                account.default_volume_cm3 = volume
            except (TypeError, ValueError):
                errors["default_volume_cm3"] = ["Poné un volumen en cm³ mayor a cero."]
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        created = account.pk is None
        # Guardar la cuenta la reactiva: es lo que hace el comerciante después
        # de corregir unas credenciales que Andreani rechazó.
        account.is_active = True
        account.last_error = ""
        if "password" in data or "username" in data or "environment" in data:
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
            changes={"andreani": {"from": None, "to": "cuenta creada" if created else "cuenta actualizada"}},
        )
        return Response(dict(account_data(account), exists=True), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AccountTestView(_AndreaniView):
    def post(self, request):
        account = self.account(request)
        if account is None:
            return Response({"detail": "Primero guardá tu cuenta de Andreani."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            AndreaniClient(account).login()
        except AndreaniError as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "detail": "Andreani aceptó el usuario y la contraseña."})


class BranchesView(_AndreaniView):
    def get(self, request):
        postal_code = service.postal_code_digits(request.query_params.get("cp"))
        if not postal_code:
            return Response({"cp": ["Indicá un código postal."]}, status=status.HTTP_400_BAD_REQUEST)
        account = self.account(request) or CarrierAccount(owner=request.user, carrier=ANDREANI)
        try:
            branches = AndreaniClient(account).branches(postal_code)
        except AndreaniError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        results = []
        for branch in branches:
            address = branch.get("direccion") or {}
            code = str(branch.get("codigo") or branch.get("nomenclatura") or "")
            name = str(branch.get("descripcion") or "")
            results.append(
                {
                    "id": str(branch.get("id") or ""),
                    "code": code,
                    "name": name,
                    "address": " ".join(str(address.get(key) or "") for key in ("calle", "numero")).strip(),
                    "city": str(address.get("localidad") or ""),
                    "postal_code": str(address.get("codigoPostal") or ""),
                    "hours": str(branch.get("horarioDeAtencion") or ""),
                    # Los puntos HOP son sucursales más para Andreani.
                    "is_hop": code.upper().startswith("HOP") or "HOP" in name.upper(),
                }
            )
        return Response({"results": [branch for branch in results if branch["id"]]})


def _requested_orders(request, data):
    """``(ids, orders_by_id, package_count)`` del cuerpo, o una ``Response`` 400."""
    ids = data.get("order_ids")
    if not isinstance(ids, list) or not ids:
        return Response({"order_ids": ["Elegí al menos un pedido."]}, status=status.HTTP_400_BAD_REQUEST)
    if len(ids) > MAX_ORDERS:
        return Response({"order_ids": [f"Se pueden procesar hasta {MAX_ORDERS} pedidos por vez."]}, status=status.HTTP_400_BAD_REQUEST)
    try:
        package_count = min(max(int(data.get("package_count") or 1), 1), 20)
    except (TypeError, ValueError):
        package_count = 1
    orders = {
        order.pk: order
        for order in Order.objects.filter(pk__in=[i for i in ids if str(i).isdigit()], user=request.user).select_related("address")
    }
    return ids, orders, package_count


def _money(value):
    return str(value) if value is not None else None


class ShipmentQuoteView(_AndreaniView):
    def post(self, request):
        account = self.account(request)
        if account is None:
            return Response({"detail": "Primero cargá tu cuenta de Andreani."}, status=status.HTTP_400_BAD_REQUEST)
        data = request.data if isinstance(request.data, dict) else {}
        parsed = _requested_orders(request, data)
        if isinstance(parsed, Response):
            return parsed
        ids, orders, package_count = parsed
        client = AndreaniClient(account)
        results, total, quoted = [], Decimal("0"), 0
        for raw_id in ids:
            order = orders.get(int(raw_id)) if str(raw_id).isdigit() else None
            if order is None:
                results.append({"order_id": raw_id, "ok": False, "detail": "El pedido no existe o no es tuyo."})
                continue
            number = order.external_number or str(order.pk)
            try:
                quote = service.quote_order(account, order, data.get("contract"), package_count=package_count, client=client)
            except service.ShipmentError as exc:
                results.append({"order_id": order.pk, "number": number, "ok": False, "detail": str(exc)})
                continue
            quoted += 1
            total += quote["price"]
            results.append(
                {
                    "order_id": order.pk,
                    "number": number,
                    "recipient": order.address.recipient_name if order.address_id else "",
                    "postal_code": order.address.postal_code if order.address_id else "",
                    "ok": True,
                    "detail": f"$ {quote['price']} con IVA",
                    "price": _money(quote["price"]),
                    "price_without_tax": _money(quote["price_without_tax"]),
                    "insurance": _money(quote["insurance"]),
                    "chargeable_weight_kg": _money(quote["chargeable_weight_kg"]),
                }
            )
        return Response({"quoted": quoted, "failed": len(ids) - quoted, "total": _money(total), "results": results})


class ShipmentCollectionView(_AndreaniView):
    def get(self, request):
        queryset = CarrierShipment.objects.filter(account__owner=request.user, carrier=ANDREANI).select_related(
            "order", "order__address"
        )
        order = str(request.query_params.get("order") or "")
        if order.isdigit():
            queryset = queryset.filter(order_id=int(order))
        statuses = [value for value in str(request.query_params.get("status") or "").split(",") if value in CarrierShipment.Status.values]
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        return Response({"results": [shipment_data(shipment) for shipment in queryset[:200]]})

    def post(self, request):
        account = self.account(request)
        if account is None:
            return Response({"detail": "Primero cargá tu cuenta de Andreani."}, status=status.HTTP_400_BAD_REQUEST)
        data = request.data if isinstance(request.data, dict) else {}
        parsed = _requested_orders(request, data)
        if isinstance(parsed, Response):
            return parsed
        ids, orders, package_count = parsed
        branches = data.get("branches") if isinstance(data.get("branches"), dict) else {}
        results, created = [], 0
        for raw_id in ids:
            order = orders.get(int(raw_id)) if str(raw_id).isdigit() else None
            if order is None:
                results.append({"order_id": raw_id, "ok": False, "detail": "El pedido no existe o no es tuyo."})
                continue
            branch = branches.get(str(order.pk)) or branches.get(order.pk) or {}
            try:
                shipment = service.create_shipment(
                    request,
                    account,
                    order,
                    data.get("contract"),
                    branch_id=str(branch.get("id") or ""),
                    branch_name=str(branch.get("name") or ""),
                    package_count=package_count,
                )
            except service.ShipmentError as exc:
                results.append({"order_id": order.pk, "number": order.external_number or str(order.pk), "ok": False, "detail": str(exc)})
                continue
            created += 1
            results.append(
                dict(shipment_data(shipment), order_id=order.pk, number=order.external_number or str(order.pk), ok=True, detail="Envío creado.")
            )
        return Response({"created": created, "failed": len(ids) - created, "results": results})


class _OwnShipmentView(_AndreaniView):
    def shipment(self, request, pk):
        return (
            CarrierShipment.objects.filter(pk=pk, account__owner=request.user, carrier=ANDREANI)
            .select_related("account", "order", "order__address")
            .first()
        )


class ShipmentDetailView(_OwnShipmentView):
    def get(self, request, pk):
        shipment = self.shipment(request, pk)
        if shipment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(shipment_data(shipment, events=True))


def _label_response(content, fmt, filename):
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
        fmt = "zpl" if request.query_params.get("file_type") == "zpl" else "pdf"
        try:
            content = AndreaniClient(shipment.account).label(shipment.group_number or shipment.tracking_number, fmt=fmt)
        except AndreaniError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return _label_response(content, fmt, f"andreani-{shipment.tracking_number}.{fmt}")


class ShipmentLabelsView(_AndreaniView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        ids = [i for i in data.get("shipment_ids") or [] if str(i).isdigit()][:MAX_ORDERS]
        fmt = "zpl" if data.get("file_type") == "zpl" else "pdf"
        shipments = list(
            CarrierShipment.objects.filter(pk__in=ids, account__owner=request.user, carrier=ANDREANI)
            .exclude(status=CarrierShipment.Status.CANCELLED)
            .select_related("account")
        )
        if not shipments:
            return Response({"shipment_ids": ["Elegí al menos un envío."]}, status=status.HTTP_400_BAD_REQUEST)
        labels = []
        try:
            for shipment in shipments:
                number = shipment.group_number or shipment.tracking_number
                labels.append((shipment, AndreaniClient(shipment.account).label(number, fmt=fmt)))
        except AndreaniError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        if fmt == "zpl":
            # Una térmica imprime ZPL concatenado sin más: un solo archivo.
            return _label_response(b"\n".join(content for _shipment, content in labels), fmt, "andreani-etiquetas.zpl")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for shipment, content in labels:
                archive.writestr(f"andreani-{shipment.tracking_number}.pdf", content)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="andreani-etiquetas.zip"'
        response["Cache-Control"] = "no-store"
        return response


class ShipmentPrintView(_AndreaniView):
    """``POST shipments/print/ {shipment_ids}``: un PDF para imprimir de una
    vez, con el rótulo de cada pedido seguido de su etiqueta de Andreani
    (``printing.bundle``)."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        ids = [int(i) for i in data.get("shipment_ids") or [] if str(i).isdigit()][:MAX_ORDERS]
        found = {
            shipment.pk: shipment
            for shipment in CarrierShipment.objects.filter(pk__in=ids, account__owner=request.user, carrier=ANDREANI)
            .exclude(status=CarrierShipment.Status.CANCELLED)
            .select_related("account", "order", "order__address", "order__store_connection", "order__user")
        }
        # En el orden en que se pidieron: es el orden de las hojas.
        shipments = [found[pk] for pk in dict.fromkeys(ids) if pk in found]
        if not shipments:
            return Response({"shipment_ids": ["Elegí al menos un envío."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            content = printing.bundle(shipments)
        except AndreaniError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        name = f"envio-{shipments[0].tracking_number}.pdf" if len(shipments) == 1 else f"envios-{len(shipments)}.pdf"
        return _label_response(content, "pdf", name)


class ShipmentRefreshView(_OwnShipmentView):
    def post(self, request, pk):
        shipment = self.shipment(request, pk)
        if shipment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            service.sync_shipment(shipment)
        except AndreaniError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        shipment.refresh_from_db()
        return Response(shipment_data(shipment, events=True))


class ShipmentCancelView(_OwnShipmentView):
    def post(self, request, pk):
        shipment = self.shipment(request, pk)
        if shipment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            service.cancel_shipment(request, shipment)
        except service.ShipmentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(shipment_data(shipment))


# --- Precio de Andreani en el checkout de las tiendas -----------------------


def _checkout_stores(user):
    """Las tiendas activas del usuario cuyo checkout nos pregunta el precio."""
    stores = StoreConnection.objects.filter(owner=user, status=StoreConnection.Status.ACTIVE).order_by("pk")
    return [store for store in stores if getattr(get_provider(store.platform), "quotes_at_checkout", False)]


def _checkout_data(store, account):
    values = checkout_service.config(store)
    return {
        "store": store.pk,
        "store_name": store.name or store.external_store_id,
        "platform": store.platform,
        "platform_label": store.get_platform_display(),
        "enabled": bool(values.get("enabled")),
        "contract": values.get("contract") or "",
        "name": values.get("name") or checkout_service.DEFAULT_NAME,
        "delivery_days_min": values.get("delivery_days_min"),
        "delivery_days_max": values.get("delivery_days_max"),
        "surcharge_percent": values.get("surcharge_percent"),
        "surcharge_amount": values.get("surcharge_amount"),
        "free_shipping_from": values.get("free_shipping_from"),
        "problems": checkout_service.problems(store, account, values) if values.get("enabled") else [],
    }


def _optional_days(value):
    if value in (None, ""):
        return None
    days = int(value)
    if days < 0 or days > 90:
        raise ValueError
    return days


def _optional_money(value, maximum):
    """``None`` si viene vacío; si no, un ``Decimal`` entre 0 y ``maximum``
    como string con dos decimales. ``ValueError`` si no sirve."""
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError from exc
    if not number.is_finite() or number < 0 or number > maximum:
        raise ValueError
    return str(number.quantize(Decimal("0.01")))


class CheckoutView(_AndreaniView):
    def get(self, request):
        account = self.account(request)
        return Response({"results": [_checkout_data(store, account) for store in _checkout_stores(request.user)]})

    def put(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        store = next((item for item in _checkout_stores(request.user) if str(item.pk) == str(data.get("store"))), None)
        if store is None:
            return Response({"store": ["Esa tienda no es tuya o su checkout no nos pregunta el precio."]}, status=status.HTTP_400_BAD_REQUEST)
        account = self.account(request)
        try:
            days_min = _optional_days(data.get("delivery_days_min"))
            days_max = _optional_days(data.get("delivery_days_max"))
        except (TypeError, ValueError):
            return Response({"delivery_days_min": ["Los días van de 0 a 90."]}, status=status.HTTP_400_BAD_REQUEST)
        if days_min is not None and days_max is not None and days_min > days_max:
            return Response({"delivery_days_max": ["El máximo no puede ser menor que el mínimo."]}, status=status.HTTP_400_BAD_REQUEST)
        money_fields = {}
        for name, maximum, message in (
            ("surcharge_percent", Decimal("300"), "El recargo va de 0 a 300 %."),
            ("surcharge_amount", Decimal("10000000"), "El recargo fijo tiene que ser un monto en pesos, cero o más."),
            ("free_shipping_from", Decimal("1000000000"), "El monto para el envío gratis tiene que ser cero o más."),
        ):
            try:
                money_fields[name] = _optional_money(data.get(name), maximum)
            except ValueError:
                return Response({name: [message]}, status=status.HTTP_400_BAD_REQUEST)
        values = {
            "enabled": bool(data.get("enabled")),
            "contract": str(data.get("contract") or "").strip()[:50],
            "name": str(data.get("name") or "").strip()[:100] or checkout_service.DEFAULT_NAME,
            "delivery_days_min": days_min,
            "delivery_days_max": days_max,
            **money_fields,
        }
        if values["enabled"]:
            found = checkout_service.problems(store, account, values)
            if found:
                return Response({"detail": "No se puede activar: " + "; ".join(found) + "."}, status=status.HTTP_400_BAD_REQUEST)
        before = checkout_service.config(store)
        checkout_service.save_config(store, values)
        if before.get("enabled") != values["enabled"] or before.get("name") != values["name"]:
            # Cambió qué opciones cotizamos en el checkout (Tiendanube necesita
            # una opción del carrier con ese código para mostrar la tarifa).
            get_provider(store.platform).checkout_prices_changed(store)
        # Qué cambió (habilitado, contrato, recargo, envío gratis...), para la auditoría.
        changes = {
            f"andreani_checkout.{key}": {"from": before.get(key), "to": value}
            for key, value in values.items()
            if before.get(key) != value
        }
        record(
            request,
            category="integrations",
            action="store.update",
            target=store,
            target_type="storeconnection",
            target_repr=str(store.name or store.pk),
            changes=changes or None,
        )
        return Response(_checkout_data(store, account))


class CheckoutTestView(_AndreaniView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        store = next((item for item in _checkout_stores(request.user) if str(item.pk) == str(data.get("store"))), None)
        if store is None:
            return Response({"store": ["Esa tienda no es tuya o su checkout no nos pregunta el precio."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            weight = Decimal(str(data.get("weight_kg") or 0))
        except (InvalidOperation, ValueError):
            weight = Decimal("0")
        try:
            cart_total = Decimal(str(data["cart_total"])) if data.get("cart_total") not in (None, "") else None
            if cart_total is not None and not cart_total.is_finite():
                cart_total = None
        except (InvalidOperation, ValueError):
            cart_total = None
        try:
            result = checkout_service.test_quote(
                store, data.get("postal_code"), weight if weight.is_finite() else Decimal("0"), cart_total
            )
        except service.ShipmentError as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(dict(result, ok=True))
