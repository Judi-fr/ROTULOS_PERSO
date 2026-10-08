#!/usr/bin/env python3
"""Crea pedidos de prueba en el Magento del Codespace, como si los hiciera un
comprador: carrito de invitado, dirección argentina, envío "flatrate" y pago
"checkmo" (los dos vienen activos en una instalación nueva).

    python3 crear_pedidos.py            # 3 pedidos
    python3 crear_pedidos.py 10         # 10 pedidos
    python3 crear_pedidos.py --cancelar 000000002   # cancela ese pedido

Usa el usuario admin de credenciales-admin.txt (lo crea instalar.sh). Habla con
Magento por localhost: no necesita que el puerto sea público.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "http://localhost:8080/rest/V1"
# Magento está instalado con HTTPS: sin este header redirige cada pedido.
HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "X-Forwarded-Proto": "https"}
SKU = "ROT-REMERA"

ADDRESSES = [
    {"firstname": "María", "lastname": "Gómez", "street": ["Av. Colón 1234", "Piso 3 B"], "city": "Córdoba", "region": "Córdoba", "postcode": "5000"},
    {"firstname": "Juan", "lastname": "Pérez", "street": ["Rivadavia 4500"], "city": "Ciudad Autónoma de Buenos Aires", "region": "Ciudad Autónoma de Buenos Aires", "postcode": "1406"},
    {"firstname": "Lucía", "lastname": "Fernández", "street": ["Calle 12 1500", "Depto 2"], "city": "La Plata", "region": "Buenos Aires", "postcode": "1900"},
    {"firstname": "Carlos", "lastname": "Sosa", "street": ["San Martín 250"], "city": "Mendoza", "region": "Mendoza", "postcode": "5500"},
]


def call(method, path, body=None, token=None):
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}/{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Magento respondió {exc.code} en {method} {path}: {exc.read().decode()[:500]}") from None
    return json.loads(raw) if raw else None


def admin_token():
    credentials = Path(__file__).with_name("credenciales-admin.txt").read_text()
    password = re.search(r"Contraseña: (\S+)", credentials).group(1)
    return call("POST", "integration/admin/token", {"username": "admin", "password": password})


def ensure_product(token):
    try:
        return call("GET", f"products/{SKU}", token=token)
    except SystemExit:
        pass
    return call(
        "POST",
        "products",
        {
            "product": {
                "sku": SKU,
                "name": "Remera de prueba",
                "attribute_set_id": 4,
                "price": 15000,
                "status": 1,
                "visibility": 4,
                "type_id": "simple",
                "weight": 0.3,
                "extension_attributes": {"stock_item": {"qty": 10000, "is_in_stock": True}},
            }
        },
        token=token,
    )


def region_ids(token):
    """Las provincias argentinas que conoce Magento (nombre -> id). Si no las
    tiene cargadas, la provincia va como texto."""
    country = call("GET", "directory/countries/AR", token=token)
    return {region["name"]: int(region["id"]) for region in country.get("available_regions") or []}


def place_order(address, regions, quantity):
    full = dict(address, country_id="AR", telephone="1155550000", email="comprador@example.com")
    if full["region"] in regions:
        full["region_id"] = regions[full["region"]]
    cart = call("POST", "guest-carts")
    call("POST", f"guest-carts/{cart}/items", {"cartItem": {"sku": SKU, "qty": quantity, "quote_id": cart}})
    call(
        "POST",
        f"guest-carts/{cart}/shipping-information",
        {
            "addressInformation": {
                "shipping_address": full,
                "billing_address": full,
                "shipping_carrier_code": "flatrate",
                "shipping_method_code": "flatrate",
            }
        },
    )
    return call("PUT", f"guest-carts/{cart}/order", {"paymentMethod": {"method": "checkmo"}})


def cancel(token, increment_id):
    found = call(
        "GET",
        "orders?searchCriteria[filter_groups][0][filters][0][field]=increment_id"
        f"&searchCriteria[filter_groups][0][filters][0][value]={increment_id}",
        token=token,
    )
    if not found["items"]:
        raise SystemExit(f"No existe el pedido {increment_id}.")
    order_id = found["items"][0]["entity_id"]
    call("POST", f"orders/{order_id}/cancel", token=token)
    print(f"Pedido {increment_id} cancelado.")


def main():
    token = admin_token()
    if len(sys.argv) == 3 and sys.argv[1] == "--cancelar":
        cancel(token, sys.argv[2])
        return
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    ensure_product(token)
    regions = region_ids(token)
    for index in range(count):
        order_id = place_order(ADDRESSES[index % len(ADDRESSES)], regions, quantity=1 + index % 3)
        order = call("GET", f"orders/{order_id}", token=token)
        print(f"Pedido {order['increment_id']} creado (entity_id {order_id}).")


if __name__ == "__main__":
    main()
