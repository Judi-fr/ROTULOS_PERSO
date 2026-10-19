"""Novedades push: el transportista nos avisa cada movimiento de un envío en vez
de que lo consultemos (hoy Andreani, "Novedades Push", su documento v1.6).

Del lado del transportista es una configuración manual: el cliente le pide el
servicio a su ejecutivo y le pasa una dirección y credenciales. Por eso:

- ``GET/POST accounts/<carrier>/push/`` (el comerciante, ``orders.create``):
  ver o generar SU dirección y sus credenciales — una dirección por cuenta
  (``/api/v1/carriers/push/<carrier>/<key>/``) con usuario y contraseña para
  Basic auth, que es uno de los dos esquemas que Andreani acepta. La contraseña
  se guarda como hash y se muestra una sola vez; generar otra la reemplaza.
- ``POST push/<carrier>/<key>/`` (público, Basic): recibe una novedad (o una
  lista) y la aplica al envío de ESA cuenta con el mismo número. Contesta
  ``{"status": "OK"|"ERROR", "message", "error"}`` con 200: Andreani reintenta
  solo ante un 5xx, y una novedad de un envío que no conocemos no se arregla
  reintentando. Credenciales malas -> 401.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record

from .base import CarrierError
from .models import CarrierPushToken, CarrierShipment
from .providers import get_provider
from .views import _CarrierView

logger = logging.getLogger(__name__)


def push_url(request, provider, token):
    path = reverse("carrier-push", kwargs={"carrier": provider.code, "key": token.key})
    base = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else request.build_absolute_uri(path)


def token_data(request, provider, token, password=None):
    if token is None:
        return {"configured": False}
    data = {
        "configured": True,
        "url": push_url(request, provider, token),
        "username": token.username,
        "last_received_at": token.last_received_at,
        "received_count": token.received_count,
        "created_at": token.created_at,
    }
    if password:
        data["password"] = password
    return data


class PushTokenView(_CarrierView):
    read_permission = "carriers.manage"
    write_permission = "carriers.manage"

    def _context(self, request, carrier):
        provider = self.provider_or_404(carrier)
        if provider is None or not provider.supports_push:
            return None, None
        return provider, self.account(request, provider)

    def get(self, request, carrier):
        provider, account = self._context(request, carrier)
        if provider is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        token = CarrierPushToken.objects.filter(account=account).first() if account else None
        return Response(token_data(request, provider, token))

    def post(self, request, carrier):
        provider, account = self._context(request, carrier)
        if provider is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if account is None:
            return Response({"detail": f"Primero guardá tu cuenta de {provider.name}."}, status=status.HTTP_400_BAD_REQUEST)
        password = secrets.token_urlsafe(24)
        token, _created = CarrierPushToken.objects.update_or_create(
            account=account,
            defaults={
                "key": secrets.token_urlsafe(24),
                "username": f"rotulos-{account.pk}",
                "password_hash": make_password(password),
            },
        )
        record(
            request,
            category="integrations",
            action="store.update",
            target=account,
            target_type="carrieraccount",
            target_repr=str(account),
            changes={f"{provider.code}.push": {"from": None, "to": "credenciales generadas"}},
        )
        return Response(token_data(request, provider, token, password=password), status=status.HTTP_201_CREATED)


def _basic_credentials(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None, None
    username, _sep, password = decoded.partition(":")
    return username, password


class PushReceiveView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, carrier, key):
        provider = get_provider(carrier)
        token = (
            CarrierPushToken.objects.filter(key=key, account__carrier=carrier).select_related("account").first()
            if provider is not None and provider.supports_push
            else None
        )
        username, password = _basic_credentials(request)
        # Se compara aunque no exista, para no revelar qué direcciones existen.
        valid = check_password(password or "", token.password_hash if token else make_password(None))
        if token is None or not valid or not secrets.compare_digest(str(username or ""), token.username):
            return Response({"status": "ERROR", "message": "", "error": "Credenciales inválidas."}, status=status.HTTP_401_UNAUTHORIZED)

        payloads = request.data if isinstance(request.data, list) else [request.data]
        applied, unknown = 0, []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            number = provider.push_number(payload)
            shipment = (
                CarrierShipment.objects.filter(account=token.account, carrier=provider.code, tracking_number=number)
                .select_related("account", "order")
                .first()
                if number
                else None
            )
            if shipment is None:
                unknown.append(number or "(sin número)")
                continue
            try:
                provider.receive_push(shipment, payload)
            except CarrierError as exc:
                logger.warning("Novedad push de %s sin aplicar (envío %s): %s", provider.name, number, exc)
                return Response({"status": "ERROR", "message": "", "error": "No se pudo procesar."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            applied += 1
        CarrierPushToken.objects.filter(pk=token.pk).update(last_received_at=timezone.now(), received_count=F("received_count") + len(payloads))
        if unknown and not applied:
            return Response({"status": "ERROR", "message": "", "error": f"Envío desconocido: {', '.join(unknown[:5])}."})
        return Response({"status": "OK", "message": f"{applied} novedad{'es' if applied != 1 else ''} aplicada{'s' if applied != 1 else ''}.", "error": ""})

