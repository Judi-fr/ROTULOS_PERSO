"""Cliente de OCA e-Pak (web services ``Oep_TrackEPak.asmx``).

Sale de la documentación oficial (https://developers.oca.com.ar/epak.html) y del
WSDL, y se probó el 2026-10-14 contra el QA público de OCA con sus datos de
prueba: alta, etiqueta PDF y ZPL, seguimiento y anulación responden con las
formas que se leen acá.

- **Sin sesión:** cada método lleva lo suyo — usuario y clave de e-Pak para dar
  de alta y anular, CUIT + operativa para cotizar, nada para etiquetas,
  seguimiento y sucursales.
- **HTTP POST** con formulario (el "binding" HTTP del web service, más simple
  que SOAP): la respuesta es XML, casi siempre un ``DataSet`` de .NET cuyas
  filas están dentro de ``diffgr:diffgram``.
- ``IngresoORMultiplesRetiros_v2`` exige el parámetro ``compatMode`` (vacío),
  que la documentación no menciona: sin él OCA contesta "Missing parameter".
- Usuario o clave incorrectos -> fila ``Error/Descripcion`` "Usuario o
  contraseña incorrectos" con HTTP 200; ``GetOperativasByUsuario`` con una
  clave mala devuelve el diffgram vacío.
"""

from __future__ import annotations

import base64
import logging
import xml.etree.ElementTree as ET

import requests
from django.conf import settings

from ..base import CarrierAuthError, CarrierError, CarrierRejectedError

logger = logging.getLogger(__name__)

DIFFGRAM = "{urn:schemas-microsoft-com:xml-diffgram-v1}diffgram"


class OcaError(CarrierError):
    """Falla hablando con OCA (red, 5xx, respuesta ilegible)."""


class OcaAuthError(OcaError, CarrierAuthError):
    """OCA rechazó el usuario o la clave de e-Pak."""


class OcaRejectedError(OcaError, CarrierRejectedError):
    """OCA rechazó el pedido; el mensaje es suyo."""


def base_url(environment):
    if environment == "qa":
        return str(getattr(settings, "OCA_API_BASE_QA", "https://integraciones.ocadev.com.ar/epak_tracking_test/Oep_TrackEPak.asmx")).rstrip("/")
    return str(getattr(settings, "OCA_API_BASE_PRODUCTION", "https://webservice.oca.com.ar/ePak_tracking/Oep_TrackEPak.asmx")).rstrip("/")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_xml(content):
    """El XML de OCA. Se rechaza cualquier DOCTYPE: OCA nunca lo manda y es la
    puerta de las entidades XML."""
    if b"<!DOCTYPE" in content[:2048].upper():
        raise OcaError("OCA devolvió una respuesta inesperada.")
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise OcaError("OCA devolvió una respuesta ilegible.") from exc


def dataset_rows(root):
    """Las filas de un ``DataSet`` de .NET: ``[(nombre de tabla, {campo: texto})]``."""
    gram = root.find(f".//{DIFFGRAM}")
    if gram is None:
        return []
    rows = []
    for dataset in gram:
        for row in dataset:
            rows.append((_local(row.tag), {_local(child.tag): (child.text or "").strip() for child in row}))
    return rows


def rows_named(rows, *names):
    return [values for table, values in rows if table in names]


def row_errors(rows):
    """Los mensajes de error que OCA manda como filas (``Error/Descripcion``,
    ``Table1/Error``)."""
    messages = []
    for table, values in rows:
        if table == "Error" and values.get("Descripcion"):
            messages.append(values["Descripcion"])
        elif values.get("Error"):
            messages.append(values["Error"])
    return messages


class OcaClient:
    def __init__(self, account, *, timeout=None):
        self.account = account
        self.base = base_url(account.environment)
        self.timeout = timeout or getattr(settings, "OCA_HTTP_TIMEOUT_SECONDS", 30)

    def call(self, method, data):
        try:
            response = requests.post(f"{self.base}/{method}", data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise OcaError(f"No se pudo contactar a OCA: {exc.__class__.__name__}.") from exc
        if response.status_code >= 500:
            # El web service de .NET contesta 500 con el detalle en texto ("Missing parameter...").
            detail = response.text[:200].strip() if response.text else ""
            raise OcaError(f"OCA respondió HTTP {response.status_code} en {method}" + (f": {detail}" if detail else "."))
        if response.status_code >= 400:
            raise OcaRejectedError(f"OCA rechazó {method} (HTTP {response.status_code}).")
        return parse_xml(response.content)

    # --- Operaciones -------------------------------------------------------

    def operativas(self):
        """Las operativas del usuario de e-Pak; ``OcaAuthError`` si usuario o
        clave no sirven (OCA devuelve la lista vacía)."""
        root = self.call("GetOperativasByUsuario", {"usr": self.account.username, "psw": self.account.password})
        rows = rows_named(dataset_rows(root), "Table")
        if not rows:
            raise OcaAuthError("OCA rechazó el usuario o la clave de e-Pak (o el usuario no tiene operativas).")
        return rows

    def quote(self, *, cuit, operativa, weight_kg, volume_m3, origin_postal_code, postal_code, packages, declared_value=0):
        """``Tarifar_Envio_Corporativo`` -> la fila con ``Total``, ``Precio``,
        ``PlazoEntrega``, ``Ambito``."""
        root = self.call(
            "Tarifar_Envio_Corporativo",
            {
                "PesoTotal": f"{weight_kg:.3f}",
                "VolumenTotal": f"{volume_m3:.6f}",
                "CodigoPostalOrigen": str(origin_postal_code),
                "CodigoPostalDestino": str(postal_code),
                "CantidadPaquetes": str(packages),
                "ValorDeclarado": str(int(declared_value or 0)),
                "Cuit": cuit,
                "Operativa": str(operativa),
            },
        )
        rows = dataset_rows(root)
        errors = row_errors(rows)
        if errors:
            raise OcaRejectedError(f"OCA no cotizó: {errors[0]}")
        found = rows_named(rows, "Table")
        if not found or not found[0].get("Total"):
            raise OcaError("OCA no devolvió la tarifa.")
        return found[0]

    def create(self, xml_data):
        """``IngresoORMultiplesRetiros_v2`` confirmado -> ``{"summary": {...},
        "accepted": [filas DetalleIngresos], "rejected": [mensajes]}``."""
        root = self.call(
            "IngresoORMultiplesRetiros_v2",
            {
                "usr": self.account.username,
                "psw": self.account.password,
                "xml_Datos": xml_data,
                "ConfirmarRetiro": "true",
                "ArchivoCliente": "",
                "ArchivoProceso": "",
                "compatMode": "",
            },
        )
        rows = dataset_rows(root)
        errors = row_errors(rows)
        if errors:
            message = errors[0]
            if "usuario o contrase" in message.lower():
                raise OcaAuthError(f"OCA rechazó el alta: {message}. Revisá el usuario, la clave y que la operativa sea de ese usuario.")
            raise OcaRejectedError(f"OCA rechazó el alta: {message}")
        summary = (rows_named(rows, "Resumen") or [{}])[0]
        accepted = rows_named(rows, "DetalleIngresos")
        # A CONFIRMAR: el nombre de la tabla de rechazos (no se vio en QA).
        rejected = [
            values.get("Motivo") or values.get("Descripcion") or values.get("Error") or str(values)
            for values in rows_named(rows, "DetalleRechazos", "Rechazos")
        ]
        return {"summary": summary, "accepted": accepted, "rejected": rejected}

    def label(self, *, pickup_order, number, fmt="pdf"):
        """La etiqueta 10x15 (bytes): PDF o ZPL."""
        if fmt == "zpl":
            root = self.call("ObtenerEtiquetasZPL", {"ordenRetiro": str(pickup_order or ""), "numeroEnvio": str(number), "numeroBulto": ""})
            parts = [(item.text or "").strip() for item in root.iter() if _local(item.tag) == "string" and (item.text or "").strip()]
            if not parts:
                raise OcaError(f"OCA no devolvió la etiqueta ZPL del envío {number}.")
            return "\n".join(parts).encode()
        root = self.call(
            "GetPdfDeEtiquetasPorOrdenOrNumeroEnvioParaEtiquetadora",
            {"ordenRetiro": str(pickup_order or ""), "numeroEnvio": str(number), "logisticaInversa": "false"},
        )
        try:
            content = base64.b64decode((root.text or "").strip(), validate=True)
        except (ValueError, TypeError) as exc:
            raise OcaError(f"OCA devolvió una etiqueta ilegible para el envío {number}.") from exc
        if not content.startswith(b"%PDF"):
            raise OcaError(f"OCA devolvió una etiqueta ilegible para el envío {number}.")
        return content

    def traces(self, number):
        """``Tracking_Pieza_ConIdEstado`` -> filas con ``Estado``, ``IdEstado``,
        ``Motivo``, ``Sucursal``, ``Fecha``."""
        return rows_named(dataset_rows(self.call("Tracking_Pieza_ConIdEstado", {"NumeroEnvio": str(number)})), "Table")

    def cancel(self, pickup_order):
        """``AnularOrdenGenerada``: 100 = anulada; 110 usuario inválido; 120 la
        orden no es de ese usuario; 130 ya no se puede anular."""
        root = self.call(
            "AnularOrdenGenerada",
            {"usr": self.account.username, "psw": self.account.password, "IdOrdenRetiro": str(pickup_order)},
        )
        row = (rows_named(dataset_rows(root), "Table") or [{}])[0]
        code, message = str(row.get("IdResult") or ""), row.get("Mensaje") or ""
        if code == "100":
            return message
        if code == "110":
            raise OcaAuthError(message or "OCA rechazó el usuario de e-Pak.")
        raise OcaRejectedError(message or "OCA no anuló la orden.")

    def branches(self, postal_code):
        """Centros de imposición y agentes oficiales que atienden ese CP."""
        root = self.call("GetCentrosImposicionConServiciosByCP", {"CodigoPostal": str(postal_code)})
        centers = []
        for center in root:
            if _local(center.tag) != "Centro":
                continue
            values = {_local(child.tag): (child.text or "").strip() for child in center}
            values["services"] = [
                (service.findtext("{*}ServicioDesc") or service.findtext("ServicioDesc") or "").strip()
                for services in center
                if _local(services.tag) == "Servicios"
                for service in services
            ]
            centers.append(values)
        return centers
