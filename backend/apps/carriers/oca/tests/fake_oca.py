"""La OCA simulada de los tests: atiende ``requests.post`` a los web services de
e-Pak con las mismas formas XML que devolvió el QA de OCA el 2026-10-14
(``DataSet`` de .NET con las filas dentro de ``diffgr:diffgram``)."""

import base64
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from apps.carriers.models import CarrierAccount

QA = "https://integraciones.ocadev.com.ar"
USER, PASSWORD = "test@oca.com.ar", "123456"
PDF = b"%PDF-1.4 etiqueta OCA"


def response(text, status_code=200):
    fake = MagicMock()
    fake.status_code = status_code
    fake.content = text.encode("utf-8")
    fake.text = text
    return fake


def dataset(tables):
    """``tables``: ``[(nombre, {campo: valor})]`` -> el XML de un DataSet."""
    rows = "".join(
        f'<{name} diffgr:id="{name}{index}" msdata:rowOrder="{index}">'
        + "".join(f"<{key}>{escape(str(value))}</{key}>" for key, value in values.items())
        + f"</{name}>"
        for index, (name, values) in enumerate(tables)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?><DataSet xmlns="#Oca_e_Pak"><xs:schema id="NewDataSet" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" /><diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata" '
        f'xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1"><NewDataSet xmlns="">{rows}</NewDataSet></diffgr:diffgram></DataSet>'
    )


class FakeOca:
    def __init__(self):
        self.calls = []
        self.orders = []
        self.cancelled = []
        self.traces = {}
        self.next_number = 1217400000000340000
        self.next_order = 20910000
        self.price = "591.0000"

    def post(self, url, data=None, timeout=None):
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == QA, url
        method = parsed.path.rsplit("/", 1)[-1]
        self.calls.append({"method": method, "data": dict(data or {})})
        data = data or {}
        authed = data.get("usr") == USER and data.get("psw") == PASSWORD

        if method == "GetOperativasByUsuario":
            if not authed:
                return response(dataset([]))
            return response(
                dataset(
                    [
                        ("Table", {"IdOperativa": "64665", "Descripcion": "64665 - ePak - Estandar PaP", "ASucursal": "false", "OrigenSucursal": "false"}),
                        ("Table", {"IdOperativa": "62342", "Descripcion": "62342 - ePak - Estandar PaS", "ASucursal": "true", "OrigenSucursal": "false"}),
                        ("Table", {"IdOperativa": "94584", "Descripcion": "94584 - ePak - Estandar SaP", "ASucursal": "false", "OrigenSucursal": "true"}),
                    ]
                )
            )
        if method == "Tarifar_Envio_Corporativo":
            if data.get("Cuit") != "30-53625919-4" or data.get("Operativa") not in ("64665", "62342"):
                return response(dataset([("Table1", {"Error": "El CUIT o la operativa son inválidos."})]))
            return response(dataset([("Table", {"Tarifador": "15", "Precio": self.price, "Ambito": "Nacional 1", "PlazoEntrega": "3", "Total": self.price})]))
        if method == "IngresoORMultiplesRetiros_v2":
            assert "compatMode" in data, "OCA exige compatMode"
            if not authed:
                return response(dataset([("Error", {"Descripcion": "Usuario o contraseña incorrectos"})]))
            self.orders.append(data["xml_Datos"])
            self.next_number += 2
            self.next_order += 1
            return response(
                dataset(
                    [
                        ("Resumen", {"CodigoOperacion": "12358557", "CantidadIngresados": "1", "CantidadRechazados": "0"}),
                        (
                            "DetalleIngresos",
                            {
                                "Operativa": "64665",
                                "OrdenRetiro": str(self.next_order),
                                "NumeroEnvio": str(self.next_number),
                                "Estado": "OR Pendiente de Impresion",
                                "QR": "iVBORw0KGgo=",
                            },
                        ),
                    ]
                )
            )
        if method == "GetPdfDeEtiquetasPorOrdenOrNumeroEnvioParaEtiquetadora":
            return response(f'<?xml version="1.0" encoding="utf-8"?><string xmlns="#Oca_e_Pak">{base64.b64encode(PDF).decode()}</string>')
        if method == "ObtenerEtiquetasZPL":
            return response(f'<?xml version="1.0"?><ArrayOfString xmlns="#Oca_e_Pak"><string>^XA^FD{data["numeroEnvio"]}^FS^XZ</string></ArrayOfString>')
        if method == "Tracking_Pieza_ConIdEstado":
            return response(dataset([("Table", row) for row in self.traces.get(data["NumeroEnvio"], [])]))
        if method == "AnularOrdenGenerada":
            self.cancelled.append(data["IdOrdenRetiro"])
            return response(dataset([("Table", {"IdResult": "100", "Mensaje": f"La Orden {data['IdOrdenRetiro']} se anuló con éxito!"})]))
        if method == "GetCentrosImposicionConServiciosByCP":
            return response(
                '<?xml version="1.0" encoding="utf-8"?><CentrosDeImposicion>'
                "<Centro><IdCentroImposicion>41</IdCentroImposicion><Sigla>COR</Sigla><Sucursal>CORDOBA II  </Sucursal>"
                "<Calle>La Rioja</Calle><Numero>1142</Numero><Localidad>Córdoba</Localidad><CodigoPostal>5000 </CodigoPostal>"
                "<HorarioAtencion>LUN A VIE 9 A 18 HS.</HorarioAtencion><Servicios><Servicio><IdTipoServicio>2</IdTipoServicio>"
                "<ServicioDesc>Entrega de paquetes</ServicioDesc></Servicio></Servicios></Centro>"
                "<Centro><IdCentroImposicion>308</IdCentroImposicion><Sigla>A51</Sigla><Sucursal>AGENTE OFICIAL</Sucursal>"
                "<Servicios><Servicio><IdTipoServicio>1</IdTipoServicio><ServicioDesc>Admisión de paquetes</ServicioDesc></Servicio></Servicios></Centro>"
                "</CentrosDeImposicion>"
            )
        raise AssertionError(f"Método no simulado: {method}")

    def calls_to(self, method):
        return [call for call in self.calls if call["method"] == method]


class OcaTestMixin:
    def setUp(self):
        super().setUp()
        self.oca = FakeOca()
        patcher = patch("apps.carriers.oca.client.requests.post", side_effect=self.oca.post)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_oca_account(self, owner, **overrides):
        values = {
            "owner": owner,
            "carrier": "oca",
            "environment": "qa",
            "username": USER,
            "client_code": "111757/001",
            "contracts": [
                {"code": "64665", "label": "Puerta a puerta", "kind": "home"},
                {"code": "62342", "label": "Puerta a sucursal", "kind": "branch"},
            ],
            "sender_name": "Mi Tienda SRL",
            "sender_document": "30536259194",
            "sender_email": "envios@mitienda.com.ar",
            "origin_street": "La Rioja",
            "origin_number": "300",
            "origin_postal_code": "1215",
            "origin_city": "Capital Federal",
            "origin_state": "CABA",
        }
        values.update(overrides)
        account = CarrierAccount(**values)
        account.password = PASSWORD
        account.save()
        return account
