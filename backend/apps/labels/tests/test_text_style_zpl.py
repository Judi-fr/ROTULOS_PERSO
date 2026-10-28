"""Tipografía en ZPL (fase 2): la sans simple sigue con la fuente de la
impresora; lo demás va como gráfico dibujado con la misma .ttf."""

import os
from unittest import skipUnless

from django.test import SimpleTestCase

from ..text_style import pdf_font_name, text_width
from ..zpl import points_to_dots, render_label_zpl


def zpl(design, context=None, dpmm=8):
    return render_label_zpl(design, 10, 15, context=context or {}, dpmm=dpmm)


def field(**style):
    return {"destinatario": {"left": 5, "top": 20, "text": "{{destinatario}}", **style}}


class TypographyZplTests(SimpleTestCase):
    def test_plain_sans_stays_native(self):
        out = zpl(field(font_size=12), {"destinatario": "Mariela"})
        self.assertIn("^A0N", out)
        self.assertNotIn("^GFA", out)

    def test_sans_two_lines_native(self):
        out = zpl(field(font_size=12, max_lines=2), {"destinatario": "Mariela Quiroga de los Santos"})
        self.assertRegex(out, r"\^FB\d+,2,\d+,L,0")
        self.assertNotIn("^GFA", out)

    def test_serif_becomes_graphic(self):
        out = zpl(field(font_family="serif", font_size=12), {"destinatario": "Mariela"})
        self.assertIn("^GFA", out)
        self.assertNotIn("^A0N", out)

    def test_inverted_script_is_reversed_graphic(self):
        out = zpl(field(font_family="script", invert=True, font_size=12), {"destinatario": "Mariela"})
        band = out.index("^GB")
        reverse = out.index("^FR")
        graphic = out.index("^GFA")
        self.assertLess(band, reverse)
        self.assertLess(reverse, graphic)

    def test_light_field_color_is_reversed(self):
        out = zpl({"cp": {"left": 5, "top": 20, "text": "7600", "color": "#ffffff"}})
        self.assertIn("^FR", out)

    def test_accents_render_in_graphic(self):
        from PIL import Image

        from ..text_style import ttf_path, zpl_text_image

        image = zpl_text_image(["Peña ¡Hola!"], ttf_path("script"), points_to_dots(14, 8), 800, "left", 1.15, 0)
        self.assertIsInstance(image, Image.Image)
        bbox = image.point(lambda v: 255 if v else 0).getbbox()
        self.assertIsNotNone(bbox)
        expected = points_to_dots(text_width("Peña ¡Hola!", pdf_font_name("script"), 14), 8)
        self.assertLessEqual(abs((bbox[2] - bbox[0]) - expected), max(4, expected * 0.05))


@skipUnless(os.environ.get("ZPL_LABELARY_TESTS") == "1", "ZPL_LABELARY_TESTS=1 para probar contra Labelary")
class TypographyLabelaryTests(SimpleTestCase):
    def test_all_families_render(self):
        import urllib.request

        design = {"texts": [
            {"left": 5, "top": 5 + 12 * index, "text": f"{family} Peña ¡Hola!", "font_family": family,
             "font_size": 14, "max_lines": 2, "width": 60, "invert": index == 2}
            for index, family in enumerate(("sans", "serif", "script", "condensed", "mono"))
        ]}
        for dpmm, name in ((8, "8dpmm"), (12, "12dpmm")):
            request = urllib.request.Request(
                f"https://api.labelary.com/v1/printers/{name}/labels/3.94x5.91/0/",
                data=zpl(design, dpmm=dpmm).encode(), headers={"Accept": "image/png"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                self.assertEqual(response.status, 200)



class BoxTypographyZplTests(SimpleTestCase):
    def box(self, **text):
        return {"shapes": [{"id": "c", "kind": "rect", "left": 0, "top": 40, "width": 40, "height": 15,
                            "text": {"value": "Mariela Quiroga de los Santos", "font_size": 12, **text}}]}

    def test_box_sans_two_lines_native(self):
        self.assertRegex(zpl(self.box(max_lines=2)), r"\^FB\d+,2,\d+,L,0")

    def test_box_serif_is_graphic(self):
        out = zpl(self.box(font_family="serif"))
        self.assertIn("^GFA", out)
        self.assertNotIn("^A0N", out)
