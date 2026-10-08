"""La Andreani simulada de los tests: atiende ``requests.get``/``requests.request``
con las formas de su documentación oficial (login, orden de envío v2,
etiquetas, trazas v3, sucursales v2, nueva-acción). Nunca sale un pedido real."""

from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from apps.carriers.models import CarrierAccount

QA = "https://apisqa.andreani.com"
USER, PASSWORD = "cliente-prueba", "Clave-Andreani-1"


def response(status_code=200, json_data=None, content=b"", headers=None):
    fake = MagicMock()
    fake.status_code = status_code
    fake.json.return_value = {} if json_data is None else json_data
    fake.content = content
    fake.headers = headers or {}
    return fake


class FakeAndreani:
    def __init__(self):
        self.calls = []
        self.logins = 0
        self.valid_token = "token-1"
        self.orders = []
        self.traces = {}
        self.cancelled = []
        self.next_number = 360000101651600
        self.branches = [
            {"id": 96, "codigo": "TIG", "descripcion": "TIGRE (AV PRES J D PERON)", "direccion": {"calle": "Av. Perón", "numero": "100", "localidad": "Tigre", "codigoPostal": "1648"}, "horarioDeAtencion": "Lunes a Viernes 8 a 18"},
            {"id": 4327, "codigo": "HOP4327", "descripcion": "PUNTO ANDREANI HOP CNEL. APOLINARIO FIGUEROA 100", "direccion": {"calle": "Cnel. Apolinario Figueroa", "numero": "100", "localidad": "CABA", "codigoPostal": "1414"}},
        ]

    def get(self, url, auth=None, timeout=None, **kwargs):
        path = urlparse(url).path
        self.calls.append({"method": "GET", "path": path, "auth": auth})
        assert path == "/login", path
        if tuple(auth or ()) != (USER, PASSWORD):
            return response(401, {"message": "Unauthorized"})
        self.logins += 1
        self.valid_token = f"token-{self.logins}"
        return response(200, {}, headers={"x-authorization-token": self.valid_token})

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == QA, url
        path = parsed.path
        self.calls.append({"method": method, "path": path, "params": params, "json": json, "headers": headers})
        if path == "/v2/sucursales":
            assert "x-authorization-token" not in headers
            return response(200, self.branches)
        if headers.get("x-authorization-token") != self.valid_token:
            return response(401, {"message": "Token inválido"})

        if method == "POST" and path == "/v2/ordenes-de-envio":
            if json["contrato"] == "000":
                return response(400, {"detail": "El contrato es incorrecto."})
            self.orders.append(json)
            packages = []
            for index, _package in enumerate(json["bultos"], start=1):
                self.next_number += 1
                packages.append({"numeroDeBulto": str(index), "numeroDeEnvio": str(self.next_number), "totalizador": f"{index}/{len(json['bultos'])}"})
            group = f"API{len(self.orders):013d}"
            return response(202, {"estado": "Pendiente", "tipo": "B2C", "bultos": packages, "agrupadorDeBultos": group, "etiquetasPorAgrupador": f"{QA}/v2/ordenes-de-envio/{group}/etiquetas"})
        if method == "GET" and path.endswith("/etiquetas"):
            number = path.split("/")[3]
            if headers.get("Accept") == "application/zpl":
                return response(200, content=f"^XA^FD{number}^FS^XZ".encode())
            return response(200, content=b"%PDF-1.4 " + number.encode())
        if method == "GET" and path.endswith("/trazas"):
            number = path.split("/")[3]
            return response(200, {"eventos": self.traces.get(number, [])})
        if method == "POST" and path == "/v2/nueva-accion":
            self.cancelled.append(json)
            return response(200, {"mensaje": "Solicitud de Acción: cancelación ejecutada correctamente."})
        raise AssertionError(f"Pedido no simulado: {method} {path}")

    def calls_to(self, method, path):
        return [call for call in self.calls if call["method"] == method and call["path"] == path]


def event(name, moment, cycle="Distribution", **extra):
    data = {"Fecha": moment, "Ciclo": cycle, "Evento": name}
    data.update(extra)
    return data


class AndreaniTestMixin:
    def setUp(self):
        super().setUp()
        self.andreani = FakeAndreani()
        for target in ("get", "request"):
            patcher = patch(f"apps.carriers.andreani.client.requests.{target}", side_effect=getattr(self.andreani, target))
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_account(self, owner, **overrides):
        values = {
            "owner": owner,
            "carrier": "andreani",
            "environment": "qa",
            "username": USER,
            "client_code": "CL0001",
            "contracts": [
                {"code": "400006709", "label": "Domicilio", "kind": "home"},
                {"code": "400006710", "label": "Sucursal", "kind": "branch"},
            ],
            "sender_name": "Mi Tienda SRL",
            "sender_email": "envios@mitienda.com.ar",
            "sender_phone": "1144445555",
            "origin_street": "Av. Siempre Viva",
            "origin_number": "742",
            "origin_postal_code": "1405",
            "origin_city": "CABA",
        }
        values.update(overrides)
        account = CarrierAccount(**values)
        account.password = PASSWORD
        account.save()
        return account


