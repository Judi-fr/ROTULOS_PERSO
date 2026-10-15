"""Andreani en la capa común de transportistas (``apps.carriers.base``).

Andreani existía antes que la capa: su lógica sigue en ``shipments.py`` y
``client.py`` (y sus rutas propias en ``/carriers/andreani/``), y este proveedor
solo la expone con la interfaz común para que Mis pedidos la trate igual que a
Correo Argentino y OCA.
"""

from __future__ import annotations

from ..base import AccountField, CarrierProvider
from . import shipments
from .client import AndreaniClient


class AndreaniProvider(CarrierProvider):
    code = "andreani"
    name = "Andreani"
    account_help = (
        "Las credenciales se generan en Andreani.com → Integraciones; los números de contrato y de "
        "cliente te los da tu ejecutivo de cuenta."
    )
    account_fields = (
        AccountField("username", "Usuario", storage="column", required=True),
        AccountField("password", "Contraseña", storage="password", required=True),
        AccountField(
            "client_code",
            "Código de cliente",
            storage="column",
            required=True,
            required_for="quote",
            max_length=50,
            help="Te lo da tu ejecutivo de Andreani: sin él no se pueden cotizar los envíos.",
        ),
    )
    contract_help = "Un contrato por servicio. Los de sucursal sirven también para los puntos HOP."
    uses_volume = True
    uses_dimensions = False
    branch_label = "sucursal o punto HOP"
    label_formats = ("pdf", "zpl")
    label_name = "etiqueta de Andreani"
    supports_push = True
    # Su precio en el checkout es ``andreani/checkout.py`` (anterior a la capa común).
    supports_checkout = True
    checkout_default_name = "Andreani a domicilio"
    push_help = (
        "Andreani puede avisarnos cada movimiento al instante (\u201cNovedades Push\u201d) en vez de que lo "
        "consultemos cada media hora. Es un servicio que se le pide a tu ejecutivo comercial (tiene costo): "
        "pasale la dirección, el usuario y la contraseña que se generan acá, para QA y producción, y la "
        "lista de tus contratos."
    )

    def tracking_url(self, number):
        return shipments.tracking_url_for(number)

    def account_problems(self, account):
        return shipments.account_problems(account)

    def test_credentials(self, account):
        AndreaniClient(account).login()
        return {"detail": "Andreani aceptó el usuario y la contraseña."}

    def quote(self, account, order, contract, *, package_count=1):
        return shipments.quote_order(account, order, contract["code"], package_count=package_count)

    def quote_order(self, account, order, contract_code, *, package_count=1):
        return shipments.quote_order(account, order, contract_code, package_count=package_count)

    def create_shipment(self, request, account, order, contract_code, *, branch_id="", branch_name="", package_count=1):
        return shipments.create_shipment(
            request, account, order, contract_code, branch_id=branch_id, branch_name=branch_name, package_count=package_count
        )

    def push_number(self, payload):
        return str(payload.get("NumeroDeEnvio") or "").strip()

    def receive_push(self, shipment, payload):
        """Una novedad de Andreani (``{IdCliente, NumeroDeEnvio, Contrato,
        Estado, FechaEstado, Motivo, SubMotivo, Ciclo}``, su documento de
        Novedades Push v1.6) tiene lo mismo que una traza: se aplica igual."""
        trace = {
            "Evento": payload.get("Estado"),
            "Fecha": payload.get("FechaEstado"),
            "Motivo": payload.get("Motivo"),
            "Submotivo": payload.get("SubMotivo"),
            "Ciclo": payload.get("Ciclo"),
            "Estado": payload.get("Estado"),
        }
        return shipments.apply_traces(shipment, [trace])

    def label(self, shipment, fmt="pdf"):
        return AndreaniClient(shipment.account).label(shipment.group_number or shipment.tracking_number, fmt=fmt)

    def sync_shipment(self, shipment):
        return shipments.sync_shipment(shipment)

    def due_shipments(self, now=None, limit=50):
        return shipments.due_shipments(now=now, limit=limit)

    def cancel_shipment(self, request, shipment):
        return shipments.cancel_shipment(request, shipment)

    def branches(self, account, postal_code, state=""):
        results = []
        for branch in AndreaniClient(account).branches(shipments.postal_code_digits(postal_code)):
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
        return [branch for branch in results if branch["id"]]
