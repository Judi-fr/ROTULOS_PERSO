"""Sondas de salud para el balanceador y el monitoreo.

Son DOS y no una a propósito, y la diferencia importa el día que algo se
caiga de verdad:

- **liveness** (``/api/v1/health/``) contesta "el proceso está vivo". No
  toca la base ni nada externo. Es la que mira el orquestador para decidir
  si reinicia el contenedor: si acá metiéramos una consulta a la base, un
  hipo de Postgres haría reiniciar la API una y otra vez, justo cuando el
  problema no es la API.
- **readiness** (``/api/v1/health/ready/``) contesta "puedo atender
  pedidos", y para eso sí consulta la base. Es la que mira el balanceador
  para decidir si mandarle tráfico, y la que conviene monitorear.

Hasta ahora había una sola sonda que devolvía ``{"status": "ok"}`` sin
tocar nada: con la base caída seguía diciendo que todo estaba bien, que es
exactamente el momento en que un health check tiene que servir para algo.
"""

from __future__ import annotations

import logging

from django.db import connections
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

SERVICE_NAME = "rotulos-perso-api"


class LivenessView(APIView):
    """GET /api/v1/health/ — ¿está vivo el proceso?"""

    # Público y sin autenticación: lo consulta el balanceador, que no tiene
    # ni token ni sesión.
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": SERVICE_NAME})


class ReadinessView(APIView):
    """GET /api/v1/health/ready/ — ¿puede atender pedidos?

    ``200`` con el detalle de cada chequeo, o ``503`` si alguno falla. El
    código de estado es lo que mira un monitor, así que un fallo tiene que
    ser un 5xx y no un 200 con mal humor adentro.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        checks = {"database": self._check_database()}
        todo_bien = all(estado == "ok" for estado in checks.values())
        return Response(
            {
                "status": "ok" if todo_bien else "error",
                "service": SERVICE_NAME,
                "checks": checks,
            },
            status=status.HTTP_200_OK if todo_bien else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    def _check_database(self):
        """``"ok"`` o el motivo del fallo.

        Se ejecuta un ``SELECT 1`` de verdad: que exista la conexión no
        alcanza, porque Django la abre de forma perezosa y una conexión
        rota se nota recién al usarla.
        """
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return "ok"
        except Exception as exc:  # noqa: BLE001 - la sonda informa, no revienta
            logger.exception("La sonda de readiness no pudo consultar la base.")
            return f"error: {exc}"
