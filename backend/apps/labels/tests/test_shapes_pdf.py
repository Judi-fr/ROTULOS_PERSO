"""Formas y cajas con contenido en el PDF (shapes_pdf.draw_shapes, llamado
desde draw_label_page)."""

import hashlib
import importlib
from io import BytesIO

from django.test import SimpleTestCase
from pypdf import PdfReader

from ..label_rendering import CM_TO_POINTS, SAMPLE_LABEL_CONTEXT, draw_label_page, render_label_pdf

MM_TO_POINTS = CM_TO_POINTS / 10
WIDTH_PT, HEIGHT_PT = 10 * CM_TO_POINTS, 15 * CM_TO_POINTS

# Huella del contenido de la página de la "Etiqueta de envío estándar" con
# SAMPLE_LABEL_CONTEXT, tomada ANTES de sumar las formas (2026-10-28): un
# diseño sin formas tiene que dibujar exactamente lo mismo.
STANDARD_TEMPLATE_PDF_SHA256 = "f37da300f64969b9b92d69f494ce25be4d5a22016701f5f4acf29b18cde065fe"


class RecordingCanvas:
    """Anota lo que se dibuja, con los colores y el punteado vigentes."""

    def __init__(self):
        self.fill = (0, 0, 0)
        self.stroke = (0, 0, 0)
        self.dash = None
        self.line_width = 1
        self.font = None
        self.calls = []
        self._stack = []

    def _shape(self, kind, *args, **kwargs):
        self.calls.append((kind, args, kwargs, self.fill, self.stroke, self.dash, self.line_width))

    def rect(self, *args, **kwargs):
        self._shape("rect", *args, **kwargs)

    def roundRect(self, *args, **kwargs):
        self._shape("roundRect", *args, **kwargs)

    def ellipse(self, *args, **kwargs):
        self._shape("ellipse", *args, **kwargs)

    def line(self, *args):
        self._shape("line", *args)

    def drawString(self, x, y, text):
        self.calls.append(("text", x, y, text, self.font, self.fill, "left"))

    def drawCentredString(self, x, y, text):
        self.calls.append(("text", x, y, text, self.font, self.fill, "center"))

    def drawRightString(self, x, y, text):
        self.calls.append(("text", x, y, text, self.font, self.fill, "right"))

    def setFont(self, name, size):
        self.font = (name, size)

    def setFillColorRGB(self, r, g, b):
        self.fill = (r, g, b)

    def setStrokeColorRGB(self, r, g, b):
        self.stroke = (r, g, b)

    def setDash(self, *args):
        self.dash = args or None

    def setLineWidth(self, width):
        self.line_width = width

    def saveState(self):
        self._stack.append((self.fill, self.stroke, self.dash, self.line_width, self.font))

    def restoreState(self):
        self.fill, self.stroke, self.dash, self.line_width, self.font = self._stack.pop()

    def translate(self, *args):
        pass

    def rotate(self, *args):
        pass

    def of(self, *kinds):
        return [call for call in self.calls if call[0] in kinds]


def draw(shapes, palette=None, context=None):
    pdf = RecordingCanvas()
    design = {"shapes": shapes}
    if palette:
        design["palette"] = palette
    draw_label_page(pdf, design, 10, 15, context=context or {})
    return pdf


class ShapesPdfTests(SimpleTestCase):
    def test_rect_fill_stroke_radius_and_z(self):
        pdf = draw([
            {"id": "top", "kind": "rect", "left": 0, "top": 0, "width": 50, "height": 10, "fill": "#1e3a8a", "z": 2},
            {"id": "bottom", "kind": "rect", "left": 0, "top": 0, "width": 100, "height": 20, "radius": 3,
             "fill": "#000000", "z": 1},
        ])
        first, second = pdf.of("rect", "roundRect")
        # z ascendente: la de z 1 primero, aunque esté segunda en la lista.
        self.assertEqual(first[0], "roundRect")
        self.assertAlmostEqual(first[1][4], 3 * MM_TO_POINTS, places=3)
        self.assertEqual(second[0], "rect")
        self.assertEqual(second[3], (30 / 255, 58 / 255, 138 / 255))
        # rect/roundRect con relleno y sin borde: fill=1, stroke=0
        self.assertEqual((second[2].get("fill"), second[2].get("stroke")), (1, 0))

    def test_vertical_and_dotted_line(self):
        pdf = draw([{"id": "v", "kind": "line", "left": 50, "top": 10, "height": 80, "stroke": "#000000",
                     "stroke_style": "dotted"}])
        [line] = pdf.of("line")
        x1, y1, x2, y2 = line[1]
        self.assertAlmostEqual(x1, WIDTH_PT / 2, places=3)
        self.assertAlmostEqual(x2, WIDTH_PT / 2, places=3)
        self.assertAlmostEqual(y1, HEIGHT_PT * 0.9, places=3)
        self.assertAlmostEqual(y2, HEIGHT_PT * 0.1, places=3)
        self.assertIsNotNone(line[5])

    def test_box_text_alignment_and_color(self):
        pdf = draw([{"id": "cp", "kind": "rect", "left": 0, "top": 50, "width": 100, "height": 10, "fill": "#000000",
                     "text": {"value": "CP {{cp}}", "align": "center", "valign": "middle", "color": "#ffffff",
                              "font_size": 20}}], context={"cp": "7600"})
        [text] = pdf.of("text")
        self.assertEqual(text[3], "CP 7600")
        self.assertEqual(text[6], "center")
        self.assertAlmostEqual(text[1], WIDTH_PT / 2, places=3)
        box_mid_y = HEIGHT_PT * (1 - 0.55)
        self.assertLess(abs(text[2] - (box_mid_y - 20 * 0.35)), 2)
        self.assertEqual(text[5], (1, 1, 1))

    def test_shape_clipped_to_label(self):
        pdf = draw([{"id": "a", "kind": "rect", "left": 90, "top": 0, "width": 30, "height": 10, "fill": "#000000"}])
        [rect] = pdf.of("rect")
        x, y, w, h = rect[1][:4]
        self.assertAlmostEqual(x + w, WIDTH_PT, places=3)

    def test_hide_if_empty_box(self):
        pdf = draw([{"id": "ref", "kind": "rect", "left": 0, "top": 0, "width": 50, "height": 10, "fill": "#000000",
                     "text": {"value": "{{referencia}}", "hide_if_empty": True}}], context={"referencia": ""})
        self.assertEqual(pdf.of("rect", "roundRect", "text"), [])

    def test_missing_palette_color_draws_nothing(self):
        pdf = draw([{"id": "a", "kind": "rect", "left": 0, "top": 0, "width": 50, "height": 10, "fill": "accent"}])
        self.assertEqual(pdf.of("rect", "roundRect"), [])

    def test_shapes_go_under_the_regular_texts(self):
        pdf = RecordingCanvas()
        draw_label_page(pdf, {"cp": {"left": 10, "top": 10, "text": "{{cp}}"},
                              "shapes": [{"id": "bg", "kind": "rect", "left": 0, "top": 0, "width": 100,
                                          "height": 30, "fill": "#e5e7eb"}]}, 10, 15, context={"cp": "7600"})
        kinds = [call[0] for call in pdf.calls]
        self.assertLess(kinds.index("rect"), kinds.index("text"))

    def test_existing_standard_template_unchanged(self):
        design = importlib.import_module("apps.labels.migrations.0010_default_template_sender_contact").NEW_DESIGN
        pdf = render_label_pdf(design, 10, 15, context=SAMPLE_LABEL_CONTEXT)
        content = PdfReader(BytesIO(pdf)).pages[0].get_contents().get_data()
        self.assertEqual(hashlib.sha256(content).hexdigest(), STANDARD_TEMPLATE_PDF_SHA256)

    def test_real_pdf_with_shapes_renders(self):
        pdf = render_label_pdf(
            {"palette": {"primary": "#7c2d12"},
             "shapes": [{"id": "band", "kind": "rect", "left": 0, "top": 0, "width": 18, "height": 100,
                         "fill": "primary"},
                        {"id": "cp", "kind": "rect", "left": 25, "top": 40, "width": 40, "height": 12, "radius": 2,
                         "stroke": "primary", "stroke_width": 2, "text": {"value": "{{cp}}", "font_size": 26,
                                                                          "bold": True, "align": "center",
                                                                          "valign": "middle"}},
                        {"id": "e", "kind": "ellipse", "left": 70, "top": 5, "width": 20, "height": 13,
                         "fill": "#a3e635"}]},
            10, 15, context={"cp": "7600"})
        self.assertIn("7600", PdfReader(BytesIO(pdf)).pages[0].extract_text())


class ZeroStrokeTests(SimpleTestCase):
    def test_zero_stroke_width_draws_no_border(self):
        # Grosor 0 = sin borde (ReportLab dibujaría la línea más fina posible
        # y el ZPL un punto: los dos tienen que coincidir en "nada").
        pdf = draw([{"id": "a", "kind": "rect", "left": 0, "top": 0, "width": 50, "height": 10,
                     "stroke": "#000000", "stroke_width": 0}])
        self.assertEqual(pdf.of("rect", "roundRect"), [])
