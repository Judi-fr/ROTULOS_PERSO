"""Forma imagen (fase 3) en ZPL."""

import os
import re
from io import BytesIO
from unittest import skipUnless

from django.test import SimpleTestCase, TestCase
from PIL import Image

from ..zpl import render_label_zpl


def png(size=(50, 50), color=(0, 0, 0, 255)):
    output = BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    output.seek(0)
    return output


def zpl(shapes, dpmm=8, logo_file=None, warnings=None):
    return render_label_zpl({"shapes": shapes}, 10, 15, context={}, logo_file=logo_file, dpmm=dpmm,
                            **({"warnings": warnings} if warnings is not None else {}))


LOGO_SHAPE = {"id": "im", "kind": "image", "image": "logo", "left": 10, "top": 10, "width": 40, "height": 20,
              "fit": "cover"}


class ImageShapeZplTests(TestCase):
    def test_graphic_size_matches_the_box_in_dots(self):
        for dpmm in (8, 12):
            with self.subTest(dpmm=dpmm):
                out = zpl([LOGO_SHAPE], dpmm=dpmm, logo_file=png())
                match = re.search(r"\^GFA,(\d+),(\d+),(\d+),", out)
                self.assertIsNotNone(match, out)
                width = round(0.5 * 100 * dpmm) - round(0.1 * 100 * dpmm)
                height = round(0.30 * 150 * dpmm) - round(0.10 * 150 * dpmm)
                per_row = (width + 7) // 8
                self.assertEqual(int(match.group(3)), per_row)
                self.assertEqual(int(match.group(1)), per_row * height)

    def test_missing_image_draws_nothing_and_warns(self):
        warnings = []
        out = zpl([{**LOGO_SHAPE, "image": 999999}], warnings=warnings)
        self.assertNotIn("^GF", out)
        self.assertEqual(warnings, [{"code": "image_missing", "field": "im"}])

    def test_logo_without_file_warns(self):
        warnings = []
        out = zpl([LOGO_SHAPE], warnings=warnings)
        self.assertNotIn("^GF", out)
        self.assertEqual(len(warnings), 1)

    def test_logo_file_is_not_closed(self):
        logo = png()
        zpl([LOGO_SHAPE], logo_file=logo)
        self.assertFalse(logo.closed)

    def test_stroke_adds_a_border(self):
        out = zpl([{**LOGO_SHAPE, "stroke": "#000000"}], logo_file=png())
        self.assertRegex(out, r"\^GB\d+,\d+,\d+,B,0\^FS")
        out = zpl([{**LOGO_SHAPE, "stroke": "#000000", "circle": True}], logo_file=png())
        self.assertRegex(out, r"\^GE\d+,\d+,\d+,B\^FS")

    def test_transparent_image_prints_white(self):
        out = zpl([LOGO_SHAPE], logo_file=png(color=(0, 0, 0, 0)))
        total = int(re.search(r"\^GFA,(\d+),", out).group(1))
        self.assertGreater(total, 0)
        self.assertNotRegex(out, r"\^GFA,\d+,\d+,\d+,[0-9A-F]*[1-9A-F]")


@skipUnless(os.environ.get("ZPL_LABELARY_TESTS") == "1", "ZPL_LABELARY_TESTS=1 para probar contra Labelary")
class ImageLabelaryTests(SimpleTestCase):
    def test_photo_cover_circle_renders(self):
        import urllib.request

        photo = BytesIO()
        gradient = Image.linear_gradient("L").resize((300, 200)).convert("RGBA")
        gradient.save(photo, format="PNG")
        photo.seek(0)
        shape = {**LOGO_SHAPE, "width": 40, "height": 27, "circle": True, "stroke": "#000000"}
        for dpmm, name in ((8, "8dpmm"), (12, "12dpmm")):
            photo.seek(0)
            request = urllib.request.Request(
                f"https://api.labelary.com/v1/printers/{name}/labels/3.94x5.91/0/",
                data=zpl([shape], dpmm=dpmm, logo_file=photo).encode(), headers={"Accept": "image/png"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                self.assertEqual(response.status, 200)
