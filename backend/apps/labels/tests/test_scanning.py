"""Pruebas de escaneo (T#241, T#296): los QR y códigos de barras que salen
impresos se pueden LEER, no solo dibujar.

Un lector simulado: el rótulo real se rasteriza (pypdfium2) a la resolución de
una térmica — 203 dpi, en blanco y negro puro, sin grises, como imprime una
Zebra — y se decodifica con zxing-cpp, la misma biblioteca de muchos lectores
y apps de celular. Si un código no se lee acá, en el depósito tampoco.

zxing-cpp es solo de desarrollo (``requirements-dev.txt``): sin él, estos
tests se saltean en vez de fallar.
"""

import unittest
from decimal import Decimal
from io import BytesIO

import pypdfium2
from django.contrib.auth import get_user_model
from django.test import TestCase
from PIL import Image

from ..element_layout_render import render_element_layout_png
from ..label_rendering import build_label_context, render_label_pdf
from ..models import ElementLayout, LabelTemplate, LayoutElement, LayoutVariable

try:
    import zxingcpp
except ImportError:  # pragma: no cover - depende del entorno
    zxingcpp = None

User = get_user_model()
THERMAL_DPI = 203
# Un número de envío largo, como los de Andreani (15 dígitos) u OCA (19).
TRACKING = "3600001016516001234"
LONG_TRACKING_URL = (
    "https://www.correoargentino.com.ar/formularios/e-commerce?id=000500076393019A3G0C701&tipo=domicilio&ref=AB-12345"
)


def ean13_with_check(first12):
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return first12 + str((10 - total % 10) % 10)


def rasterize(pdf_bytes, dpi=THERMAL_DPI, thermal=True):
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        image = document[0].render(scale=dpi / 72).to_pil().convert("L")
    finally:
        document.close()
    if thermal:
        # Una térmica pone el punto o no lo pone: nada de grises.
        image = image.point(lambda value: 255 if value > 128 else 0, mode="1").convert("L")
    return image


def decode(image):
    return {(result.format.name, result.text) for result in zxingcpp.read_barcodes(image)}


@unittest.skipIf(zxingcpp is None, "zxing-cpp no está instalado (pip install -r requirements-dev.txt)")
class LabelScanTests(TestCase):
    """Sistema 1: el rótulo de una plantilla (render_label_pdf)."""

    def context(self, **overrides):
        values = {"tracking": TRACKING, "tracking_url": f"https://app.example.com/seguimiento.html?codigo={TRACKING}"}
        values.update(overrides)
        return values

    def design(self, **extra):
        return {
            "qr": {"left": 60, "top": 5, "size": 3, "data": "{{tracking_url}}"},
            "barcode": {"left": 5, "top": 70, "width": 8, "height": 1.5, "data": "{{tracking}}"},
            **extra,
        }

    def scan(self, design, width_cm=10, height_cm=15, context=None, dpi=THERMAL_DPI):
        pdf = render_label_pdf(design, width_cm, height_cm, context=context or self.context())
        return decode(rasterize(pdf, dpi=dpi))

    def test_qr_and_code128_read_back_at_203_dpi(self):
        found = self.scan(self.design())
        self.assertIn(("QRCode", self.context()["tracking_url"]), found)
        self.assertIn(("Code128", TRACKING), found)

    def test_the_seeded_standard_template_reads_back_with_a_real_order_context(self):
        from apps.orders.models import Address, Order

        user = User.objects.create_user(username="escaneo@example.com", email="escaneo@example.com", password="x")
        address = Address.objects.create(user=user, recipient_name="María Gómez", street="Corrientes", number="1000", city="CABA")
        order = Order.objects.create(user=user, address=address, tracking_number=TRACKING)
        context = build_label_context(order=order)
        template = LabelTemplate.objects.filter(is_public=True, is_active=True).order_by("id").first()

        pdf = render_label_pdf(template.design, template.width_cm, template.height_cm, context=context)
        found = decode(rasterize(pdf))
        self.assertIn(("Code128", TRACKING), found)
        self.assertIn(("QRCode", context["tracking_url"]), found)

    def test_ean13(self):
        code = ean13_with_check("779000123456")
        design = {"barcode": {"left": 5, "top": 40, "width": 6, "height": 2, "symbology": "ean13", "data": code}}
        self.assertIn(("EAN13", code), self.scan(design))

    def test_a_long_url_still_reads_in_the_qr(self):
        design = self.design()
        found = self.scan(design, context=self.context(tracking_url=LONG_TRACKING_URL))
        self.assertIn(("QRCode", LONG_TRACKING_URL), found)

    def test_codes_shrunk_to_fit_a_small_label_still_read(self):
        # 6 × 10: se piden un QR de 4 cm y barras de 8 cm, que no entran
        # (_fit_size_cm los achica a 3 cm y a ~5,8 cm). Achicados, se leen.
        design = {
            "qr": {"left": 50, "top": 3, "size": 4, "data": "{{tracking_url}}"},
            "barcode": {"left": 3, "top": 60, "width": 8, "height": 1.5, "data": "{{tracking}}"},
        }
        found = self.scan(design, width_cm=6, height_cm=10)
        self.assertIn(("Code128", TRACKING), found)
        self.assertIn(("QRCode", self.context()["tracking_url"]), found)

    def test_minimum_readable_barcode_width_at_203_dpi(self):
        """El límite conocido: un número de 19 dígitos en Code128 necesita
        unos 5 cm de ancho para leerse impreso a 203 dpi (más angosto, cada
        barra queda en ~2 puntos de la impresora y el redondeo la deforma;
        a 300 dpi sí se lee). Si el renderer mejora y lo angosto empieza a
        leerse, este test avisa para actualizar el límite."""
        design = lambda width: {"barcode": {"left": 3, "top": 40, "width": width, "height": 1.5, "data": "{{tracking}}"}}  # noqa: E731
        self.assertIn(("Code128", TRACKING), self.scan(design(5)))
        self.assertNotIn(("Code128", TRACKING), self.scan(design(4)))
        self.assertIn(("Code128", TRACKING), self.scan(design(4), dpi=300))

    def test_also_at_300_dpi(self):
        found = self.scan(self.design(), dpi=300)
        self.assertEqual(len(found), 2)


@unittest.skipIf(zxingcpp is None, "zxing-cpp no está instalado (pip install -r requirements-dev.txt)")
class ElementLayoutScanTests(TestCase):
    """Sistema 2: los diseños por elementos (render_element_layout_png)."""

    def test_qr_and_barcode_variables_read_back(self):
        user = User.objects.create_user(username="ana@example.com", email="ana@example.com", password="x")
        layout = ElementLayout.objects.create(
            name="Escaneo", width_mm=Decimal("100"), height_mm=Decimal("150"), dpi=THERMAL_DPI, created_by=user
        )
        qr = LayoutVariable.objects.get(code="qr")
        barcode = LayoutVariable.objects.create(code="codigo_envio", label="Código de envío", data_type="codigo_barras")
        LayoutElement.objects.create(
            layout=layout, element_type="variable", variable=qr,
            x_mm=Decimal("60"), y_mm=Decimal("10"), width_mm=Decimal("30"), height_mm=Decimal("30"),
        )
        LayoutElement.objects.create(
            layout=layout, element_type="variable", variable=barcode,
            x_mm=Decimal("10"), y_mm=Decimal("100"), width_mm=Decimal("80"), height_mm=Decimal("20"),
        )
        png, _report = render_element_layout_png(layout, {"qr": "AR-2026-0001", "codigo_envio": TRACKING}, user=user)
        image = Image.open(BytesIO(png)).convert("L")
        found = decode(image)
        self.assertIn(("QRCode", "AR-2026-0001"), found)
        self.assertIn(("Code128", TRACKING), found)
