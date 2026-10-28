"""Tipografía y varios renglones en el PDF (fase 2 del editor)."""

from django.test import SimpleTestCase

from ..label_rendering import CM_TO_POINTS, draw_label_page
from ..text_style import pdf_font_name
from .test_shapes_pdf import RecordingCanvas


class TextObject:
    def __init__(self, canvas, x, y):
        self.canvas, self.x, self.y = canvas, x, y
        self.font = None
        self.char_space = 0

    def setFont(self, name, size):
        self.font = (name, size)

    def setCharSpace(self, value):
        self.char_space = value

    def textOut(self, text):
        self.canvas.calls.append(("text", self.x, self.y, text, self.font, self.canvas.fill, "spaced",
                                  self.char_space))


class TypoCanvas(RecordingCanvas):
    def beginText(self, x, y):
        return TextObject(self, x, y)

    def drawText(self, text_object):
        pass


def draw(design, context=None):
    pdf = TypoCanvas()
    draw_label_page(pdf, design, 10, 15, context=context or {})
    return pdf


WIDTH_PT = 10 * CM_TO_POINTS


class TypographyPdfTests(SimpleTestCase):
    def test_serif_italic_uppercase_field(self):
        pdf = draw({"destinatario": {"left": 5, "top": 20, "text": "{{destinatario}}", "font_family": "serif",
                                     "italic": True, "uppercase": True, "font_size": 14}},
                   context={"destinatario": "María Gómez"})
        [text] = pdf.of("text")
        self.assertEqual(text[3], "MARÍA GÓMEZ")
        self.assertEqual(text[4], (pdf_font_name("serif", False, True), 14))

    def test_two_lines_wrap(self):
        pdf = draw({"destinatario": {"left": 5, "top": 20, "text": "{{destinatario}}", "max_lines": 2,
                                     "width": 30, "font_size": 12}},
                   context={"destinatario": "Mariela Quiroga de los Santos"})
        first, second = pdf.of("text")
        self.assertAlmostEqual(first[2] - second[2], 12 * 1.15, places=3)
        self.assertEqual(first[1], second[1])
        self.assertTrue(first[3].startswith("Mariela"))

    def test_long_word_is_cut_with_ellipsis(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth

        pdf = draw({"destinatario": {"left": 5, "top": 20, "text": "{{destinatario}}", "max_lines": 2,
                                     "width": 30, "font_size": 12}},
                   context={"destinatario": "A" * 80})
        lines = pdf.of("text")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1][3].endswith("…"))
        for line in lines:
            self.assertLessEqual(stringWidth(line[3], line[4][0], 12), WIDTH_PT * 0.30 + 0.01)

    def test_field_color_from_palette(self):
        pdf = draw({"palette": {"primary": "#7c2d12"},
                    "cp": {"left": 5, "top": 20, "text": "CP", "color": "primary"}})
        [text] = pdf.of("text")
        self.assertEqual(text[5], (124 / 255, 45 / 255, 18 / 255))

    def test_letter_spacing_uses_char_space(self):
        pdf = draw({"texts": [{"left": 5, "top": 10, "text": "DESTINATARIA", "letter_spacing": 2}]})
        [text] = pdf.of("text")
        self.assertEqual((text[6], text[7]), ("spaced", 2))

    def test_box_text_two_lines_middle(self):
        pdf = draw({"shapes": [{"id": "c", "kind": "rect", "left": 0, "top": 40, "width": 30, "height": 20,
                                "text": {"value": "Mariela Quiroga de los Santos", "max_lines": 2,
                                         "valign": "middle", "font_size": 12}}]})
        first, second = pdf.of("text")
        box_mid = 15 * CM_TO_POINTS * (1 - 0.50)
        block_mid = (first[2] + second[2]) / 2 + 12 * 0.35
        self.assertLess(abs(block_mid - box_mid), 2)
