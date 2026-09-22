"""Estilos del diseño de rótulo: tamaño, negrita, alineación, textos extra,
líneas separadoras y borde (el estilo "resumen de pedido")."""

from django.test import SimpleTestCase, TestCase
from rest_framework import serializers

from ..models import LabelTemplate
from ..label_rendering import CM_TO_POINTS, draw_label_page
from ..serializers import validate_design


class FakeCanvas:
    """Registra las llamadas de dibujo que hace ``draw_label_page``."""

    def __init__(self):
        self.font = None
        self.texts, self.lines, self.rects, self.dashes = [], [], [], []

    def setFont(self, name, size):
        self.font = (name, size)

    def drawString(self, x, y, text):
        self.texts.append({"x": x, "y": y, "text": text, "font": self.font})

    def line(self, x1, y1, x2, y2):
        self.lines.append((x1, y1, x2, y2))

    def rect(self, x, y, w, h, stroke=1, fill=0):
        self.rects.append((x, y, w, h))

    def setDash(self, *args):
        self.dashes.append(args)

    def setLineWidth(self, width):
        pass

    def saveState(self):
        pass

    def restoreState(self):
        pass


def draw(design, context=None):
    pdf = FakeCanvas()
    draw_label_page(pdf, design, 10, 15, context=context or {})
    return pdf


class ValidateStyledDesignTests(SimpleTestCase):
    def test_acepta_estilos_textos_lineas_y_borde(self):
        design = {
            "pedido": {"left": 6, "top": 4, "text": "{{pedido}}", "font_size": 14, "bold": True},
            "remitente": {"left": 50, "top": 4, "text": "{{remitente}}", "align": "right", "width": 44},
            "texts": [{"left": 6, "top": 20, "text": "Enviar a:", "font_size": 8, "hide_if_empty": False}],
            "lines": [{"top": 15}, {"top": 40, "left": 4, "right": 96, "dashed": True}],
            "border": {"dashed": True},
        }

        self.assertEqual(validate_design(design), design)

    def test_rechaza_valores_de_estilo_invalidos(self):
        invalid = [
            {"pedido": {"left": 6, "top": 4, "font_size": 100}},
            {"pedido": {"left": 6, "top": 4, "bold": "si"}},
            {"pedido": {"left": 6, "top": 4, "align": "arriba"}},
            {"pedido": {"left": 6, "top": 4, "width": 0}},
            {"texts": {"left": 6, "top": 4}},
            {"texts": [{"left": 6, "top": 4}]},
            {"lines": [{"top": 120}]},
            {"lines": [{"top": 10, "left": 80, "right": 20}]},
            {"border": True},
        ]
        for design in invalid:
            with self.subTest(design=design), self.assertRaises(serializers.ValidationError):
                validate_design(design)

    def test_una_regla_no_puede_apuntar_a_lineas_textos_o_borde(self):
        for target in ("lines", "texts", "border"):
            design = {
                "lines": [{"top": 10}],
                "texts": [{"left": 1, "top": 1, "text": "x"}],
                "border": {},
                "rules": [{"when": {"field": "cp", "op": "empty"}, "then": {"action": "hide", "target": target}}],
            }
            with self.subTest(target=target), self.assertRaises(serializers.ValidationError):
                validate_design(design)


class DrawStyledDesignTests(SimpleTestCase):
    def test_diseno_sin_estilos_se_dibuja_igual_que_antes(self):
        pdf = draw({"pedido": {"left": 10, "top": 10, "text": "{{pedido}}"}}, {"pedido": "Pedido #1"})

        [text] = pdf.texts
        self.assertEqual(text["text"], "Pedido #1")
        self.assertEqual(text["font"][0], "Helvetica")
        self.assertAlmostEqual(text["x"], 1 * CM_TO_POINTS)
        self.assertEqual(pdf.lines, [])
        self.assertEqual(pdf.rects, [])

    def test_negrita_y_tamano(self):
        pdf = draw({"pedido": {"left": 10, "top": 10, "text": "X", "bold": True, "font_size": 16}})

        self.assertEqual(pdf.texts[0]["font"], ("Helvetica-Bold", 16))

    def test_alineacion_derecha_termina_en_el_borde_de_su_caja(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth

        pdf = draw({"remitente": {"left": 50, "top": 5, "text": "Tienda", "align": "right", "width": 44, "font_size": 10}})

        right_edge = (50 + 44) / 100 * 10 * CM_TO_POINTS
        self.assertAlmostEqual(pdf.texts[0]["x"] + stringWidth("Tienda", "Helvetica", 10), right_edge)

    def test_ancho_limita_el_texto(self):
        pdf = draw({"destinatario": {"left": 6, "top": 20, "text": "Nombre " * 30, "width": 30, "font_size": 10}})

        self.assertTrue(pdf.texts[0]["text"].endswith("…"))

    def test_shrink_to_fit_achica_la_letra_en_vez_de_cortar(self):
        text = "Avenida Presidente Figueroa Alcorta 3450"
        pdf = draw({"domicilio": {"left": 6, "top": 20, "text": text, "width": 40, "font_size": 12, "shrink_to_fit": True}})

        self.assertEqual(pdf.texts[0]["text"], text)
        self.assertLess(pdf.texts[0]["font"][1], 12)

    def test_shrink_to_fit_invalido(self):
        with self.assertRaises(serializers.ValidationError):
            validate_design({"domicilio": {"left": 6, "top": 20, "shrink_to_fit": "si"}})

    def test_hide_if_empty_no_dibuja_la_etiqueta_si_el_dato_falta(self):
        design = {"texts": [{"left": 6, "top": 50, "text": "Envío: {{envio}}", "hide_if_empty": True}]}

        self.assertEqual(draw(design, {"envio": ""}).texts, [])
        self.assertEqual(draw(design, {"envio": "Andreani"}).texts[0]["text"], "Envío: Andreani")

    def test_textos_extra_lineas_y_borde(self):
        pdf = draw(
            {
                "texts": [{"left": 6, "top": 20, "text": "Enviar a:"}],
                "lines": [{"top": 15, "dashed": True}],
                "border": {"dashed": True},
            }
        )

        self.assertEqual([t["text"] for t in pdf.texts], ["Enviar a:"])
        [(x1, y1, x2, y2)] = pdf.lines
        self.assertAlmostEqual(y1, 15 * CM_TO_POINTS * (1 - 0.15))
        self.assertEqual(y1, y2)
        self.assertLess(x1, x2)
        self.assertEqual(len(pdf.rects), 1)


class DefaultTemplateStyleTests(TestCase):
    def test_la_plantilla_estandar_usa_el_estilo_resumen_sin_datos_sensibles(self):
        template = LabelTemplate.objects.get(owner=None, name="Etiqueta de envío estándar")

        self.assertEqual((float(template.width_cm), float(template.height_cm)), (10.0, 15.0))
        self.assertIn("border", template.design)
        self.assertTrue(template.design["lines"])
        self.assertIn("qr", template.design)
        self.assertIn("barcode", template.design)
        self.assertEqual(validate_design(template.design), template.design)
        design_text = str(template.design)
        for marker in ("{{referencia}}", "{{pais}}", "{{pedido}}", "{{destinatario}}", "{{remitente}}"):
            self.assertIn(marker, design_text)
