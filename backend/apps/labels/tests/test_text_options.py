"""Opciones de texto del editor (2026-10-21): etiqueta fija delante del dato
(``prefix``/``prefix_bold``), texto invertido (``invert``), recuadro propio
(``frame``) y texto vertical (``rotate``). Cada una en el PDF y en ZPL."""

import re
from io import BytesIO

from django.test import SimpleTestCase
from pypdf import PdfReader
from rest_framework import serializers

from ..label_rendering import BOLD_FONT_NAME, FONT_NAME, draw_label_page, render_label_pdf
from ..serializers import validate_design
from ..zpl import render_label_zpl


class RecordingCanvas:
    """Anota lo que dibuja ``draw_label_page``, incluidos giros y colores."""

    def __init__(self):
        self.font = None
        self.fill = (0, 0, 0)
        self.calls = []

    def setFont(self, name, size):
        self.font = (name, size)

    def drawString(self, x, y, text):
        self.calls.append(("text", x, y, text, self.font, self.fill))

    def rect(self, x, y, w, h, stroke=1, fill=0):
        self.calls.append(("rect", x, y, w, h, stroke, fill))

    def setFillColorRGB(self, r, g, b):
        self.fill = (r, g, b)

    def translate(self, x, y):
        self.calls.append(("translate", x, y))

    def rotate(self, angle):
        self.calls.append(("rotate", angle))

    def line(self, *args):
        pass

    def setDash(self, *args):
        pass

    def setLineWidth(self, width):
        pass

    def setStrokeColorRGB(self, *args):
        pass

    def saveState(self):
        pass

    def restoreState(self):
        self.fill = (0, 0, 0)

    def of(self, kind):
        return [call for call in self.calls if call[0] == kind]


def draw(entry, context=None):
    pdf = RecordingCanvas()
    draw_label_page(pdf, {"cp": {"left": 10, "top": 10, "text": "{{cp}}", "font_size": 12, **entry}}, 10, 15,
                    context=context or {"cp": "1602"})
    return pdf


class ValidateTextOptionsTests(SimpleTestCase):
    def test_accepts_the_new_options(self):
        design = {
            "cp": {"left": 10, "top": 10, "text": "{{cp}}", "prefix": "CP:", "prefix_bold": True,
                   "invert": True, "frame": True, "rotate": 90},
            "texts": [{"left": 2, "top": 50, "text": "FRÁGIL", "rotate": 270, "invert": True}],
        }
        self.assertEqual(validate_design(design), design)

    def test_rejects_bad_values(self):
        for bad in ({"rotate": 45}, {"rotate": True}, {"invert": "sí"}, {"frame": 1},
                    {"prefix": 5}, {"prefix": "x" * 41}, {"prefix_bold": "no"}):
            with self.subTest(bad=bad), self.assertRaises(serializers.ValidationError):
                validate_design({"cp": {"left": 10, "top": 10, "text": "{{cp}}", **bad}})


class PdfTextOptionsTests(SimpleTestCase):
    def test_prefix_goes_before_the_value_with_its_own_font(self):
        pdf = draw({"prefix": "CP:", "prefix_bold": True})
        prefix, value = pdf.of("text")
        self.assertEqual(prefix[3], "CP: ")
        self.assertEqual(prefix[4][0], BOLD_FONT_NAME)
        self.assertEqual(value[3], "1602")
        self.assertEqual(value[4][0], FONT_NAME)
        self.assertGreater(value[1], prefix[1])
        self.assertEqual(value[2], prefix[2])

    def test_prefix_is_hidden_with_the_field(self):
        pdf = draw({"prefix": "CP:", "hide_if_empty": True}, context={"cp": ""})
        self.assertEqual(pdf.of("text"), [])

    def test_inverted_text_is_white_on_a_black_band_behind_it(self):
        pdf = draw({"invert": True})
        kinds = [call[0] for call in pdf.calls]
        self.assertLess(kinds.index("rect"), kinds.index("text"))
        [band] = pdf.of("rect")
        self.assertEqual(band[6], 1)  # lleno
        [text] = pdf.of("text")
        self.assertEqual(text[5], (1, 1, 1))
        # La franja envuelve la línea del texto.
        self.assertLessEqual(band[2], text[2])
        self.assertGreater(band[2] + band[4], text[2] + 12 * 0.7)

    def test_frame_is_an_outline_and_the_text_stays_black(self):
        pdf = draw({"frame": True})
        [frame] = pdf.of("rect")
        self.assertEqual((frame[5], frame[6]), (1, 0))
        self.assertEqual(pdf.of("text")[0][5], (0, 0, 0))

    def test_vertical_text_turns_the_canvas(self):
        down = draw({"rotate": 90})
        self.assertIn(("rotate", -90), down.calls)
        up = draw({"rotate": 270})
        self.assertIn(("rotate", 90), up.calls)
        # Sin giro, nada de translate: se dibuja como siempre.
        self.assertEqual(draw({}).of("translate"), [])

    def test_vertical_text_is_cut_at_the_bottom_edge(self):
        pdf = draw({"rotate": 90, "top": 95}, context={"cp": "Una dirección larguísima " * 5})
        [text] = pdf.of("text")
        self.assertTrue(text[3].endswith("…"))

    def test_real_pdf_prints_prefix_and_value(self):
        design = {
            "cp": {"left": 10, "top": 10, "text": "{{cp}}", "prefix": "CP:", "invert": True, "frame": True},
            "localidad": {"left": 2, "top": 30, "text": "{{localidad}}", "rotate": 270, "prefix": "Destino"},
        }
        pdf = render_label_pdf(design, 10, 15, context={"cp": "1602", "localidad": "Florida"})
        text = PdfReader(BytesIO(pdf)).pages[0].extract_text()
        self.assertIn("CP:", text)
        self.assertIn("1602", text)
        self.assertIn("Florida", text)


class ZplTextOptionsTests(SimpleTestCase):
    def zpl(self, entry, context=None):
        design = {"cp": {"left": 10, "top": 10, "text": "{{cp}}", "font_size": 12, **entry}}
        return render_label_zpl(design, 10, 15, context=context or {"cp": "1602"})

    def test_prefix_and_value_go_in_one_field(self):
        self.assertIn("^FDCP: 1602^FS", self.zpl({"prefix": "CP:"}))

    def test_bold_prefix_is_printed_again_alone(self):
        zpl = self.zpl({"prefix": "CP:", "prefix_bold": True})
        self.assertIn("^FDCP:^FS", zpl)
        self.assertEqual(zpl.count("^FDCP: 1602^FS"), 1)

    def test_inverted_text_is_a_filled_box_and_reversed_field(self):
        zpl = self.zpl({"invert": True, "bold": True})
        self.assertIn("^GB", zpl)
        self.assertIn("^FR", zpl)
        # Invertido no se imprime dos veces (^FR la volvería a negro).
        self.assertEqual(zpl.count("^FD1602^FS"), 1)

    def test_frame_is_an_outline_box(self):
        zpl = self.zpl({"frame": True})
        self.assertIn("^GB", zpl)
        self.assertNotIn("^FR", zpl)

    def test_rotation_uses_the_font_orientation(self):
        self.assertIn("^A0R,", self.zpl({"rotate": 90}))
        down_up = self.zpl({"rotate": 270, "align": "center"})
        self.assertIn("^A0B,", down_up)
        # De abajo hacia arriba arranca arriba: justificado al final de la caja.
        self.assertRegex(down_up, r"\^FB\d+,1,0,R,0")
        self.assertIn("^A0N,", self.zpl({}))


class BandInsideTheLabelTests(SimpleTestCase):
    """La caja por defecto llega al borde derecho: la franja y el recuadro no
    pueden pasarse, o el lado derecho se pierde al cortar."""

    def test_pdf_frame_stays_inside_the_label(self):
        [frame] = draw({"frame": True, "left": 0}).of("rect")
        self.assertGreaterEqual(frame[1], 0)
        self.assertLessEqual(frame[1] + frame[3], 10 * 72 / 2.54)

    def test_zpl_band_stays_inside_the_label(self):
        zpl = render_label_zpl({"cp": {"left": 6, "top": 10, "text": "{{cp}}", "invert": True}}, 10, 15,
                               context={"cp": "1602"}, dpmm=8)
        x, w = (int(v) for v in re.search(r"\^FO(\d+),\d+\^GB(\d+),", zpl).groups())
        self.assertLessEqual(x + w, 800)
