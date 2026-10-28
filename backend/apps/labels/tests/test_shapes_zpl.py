"""Formas y cajas con contenido en ZPL (shapes_zpl.shape_commands, llamado
desde render_label_zpl)."""

import hashlib
import importlib
import os
import re
from unittest import skipUnless

from django.test import SimpleTestCase

from ..label_rendering import SAMPLE_LABEL_CONTEXT
from ..zpl import render_label_zpl

# Huellas del ZPL de la "Etiqueta de envío estándar" con SAMPLE_LABEL_CONTEXT,
# tomadas ANTES de sumar las formas (2026-10-28).
STANDARD_TEMPLATE_ZPL_SHA256 = {
    8: "6d13f25936a3238b512a7b321381302ff38f7b7c928c9a4a990370d4fd3831bb",
    12: "4cb51952614ffdb2bf887346cd4a1dec1a889c86e5686d5dbd35587c544ccfe4",
}


def zpl(shapes, context=None, dpmm=8, palette=None):
    design = {"shapes": shapes}
    if palette:
        design["palette"] = palette
    return render_label_zpl(design, 10, 15, context=context or {}, dpmm=dpmm)


class ShapesZplTests(SimpleTestCase):
    def test_black_rounded_rect_uses_gb_with_rounding(self):
        out = zpl([{"id": "a", "kind": "rect", "left": 10, "top": 10, "width": 40, "height": 10, "radius": 2,
                    "fill": "#000000"}])
        match = re.search(r"\^GB(\d+),(\d+),(\d+),B,(\d)\^FS", out)
        self.assertIsNotNone(match, out)
        self.assertTrue(0 < int(match.group(4)) <= 8)

    def test_vertical_line(self):
        out = zpl([{"id": "v", "kind": "line", "left": 50, "top": 10, "height": 80, "stroke": "#000000"}])
        self.assertRegex(out, r"\^GB0,\d+,\d+")

    def test_gray_fill_becomes_graphic(self):
        out = zpl([{"id": "g", "kind": "rect", "left": 0, "top": 0, "width": 50, "height": 20, "fill": "#9ca3af"}])
        self.assertIn("^GFA", out)
        self.assertNotRegex(out, r"\^GB\d+,\d+,\d+,B")

    def test_light_text_on_dark_box_is_reversed(self):
        out = zpl([{"id": "c", "kind": "rect", "left": 0, "top": 50, "width": 100, "height": 10, "fill": "#000000",
                    "text": {"value": "CP {{cp}}", "color": "#ffffff", "align": "center"}}], context={"cp": "7600"})
        field = out[out.index("CP 7600") - 80: out.index("CP 7600")]
        self.assertIn("^FR", field)
        self.assertIn(",C,", field)

    def test_full_label_gray_is_bounded(self):
        out = zpl([{"id": "g", "kind": "rect", "left": 0, "top": 0, "width": 100, "height": 100, "fill": "#9ca3af"}],
                  dpmm=12)
        self.assertLess(len(out), 400_000)

    def test_hide_if_empty_box(self):
        out = zpl([{"id": "r", "kind": "rect", "left": 0, "top": 0, "width": 50, "height": 10, "fill": "#000000",
                    "text": {"value": "{{referencia}}", "hide_if_empty": True}}], context={"referencia": ""})
        self.assertEqual(out, render_label_zpl({}, 10, 15))

    def test_shape_clipped_to_label(self):
        out = zpl([{"id": "a", "kind": "rect", "left": 90, "top": 0, "width": 30, "height": 10, "fill": "#000000"}])
        x, w = re.search(r"\^FO(\d+),\d+\^GB(\d+),", out).groups()
        self.assertEqual(int(x) + int(w), 800)  # 10 cm a 8 dpmm

    def test_existing_standard_template_zpl_unchanged(self):
        design = importlib.import_module("apps.labels.migrations.0010_default_template_sender_contact").NEW_DESIGN
        for dpmm, expected in STANDARD_TEMPLATE_ZPL_SHA256.items():
            out = render_label_zpl(design, 10, 15, context=SAMPLE_LABEL_CONTEXT, dpmm=dpmm)
            self.assertEqual(hashlib.sha256(out.encode()).hexdigest(), expected, dpmm)


@skipUnless(os.environ.get("ZPL_LABELARY_TESTS") == "1", "ZPL_LABELARY_TESTS=1 para probar contra Labelary")
class ShapesLabelaryTests(SimpleTestCase):
    def test_shapes_render_in_a_real_zpl_engine(self):
        import urllib.request

        shapes = [
            {"id": "band", "kind": "rect", "left": 0, "top": 0, "width": 18, "height": 100, "fill": "#7c2d12"},
            {"id": "gray", "kind": "rect", "left": 25, "top": 5, "width": 60, "height": 10, "radius": 2,
             "fill": "#9ca3af", "stroke": "#000000", "stroke_style": "dashed"},
            {"id": "e", "kind": "ellipse", "left": 70, "top": 30, "width": 20, "height": 13, "fill": "#000000"},
            {"id": "v", "kind": "line", "left": 50, "top": 40, "height": 30},
            {"id": "cp", "kind": "rect", "left": 25, "top": 75, "width": 50, "height": 10, "fill": "#000000",
             "text": {"value": "7600", "color": "#ffffff", "align": "center", "font_size": 24}},
        ]
        for dpmm, dpmm_name in ((8, "8dpmm"), (12, "12dpmm")):
            request = urllib.request.Request(
                f"https://api.labelary.com/v1/printers/{dpmm_name}/labels/3.94x5.91/0/",
                data=zpl(shapes, dpmm=dpmm).encode(), headers={"Accept": "image/png"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                self.assertEqual(response.status, 200)


class ZeroStrokeZplTests(SimpleTestCase):
    def test_zero_stroke_width_draws_no_border(self):
        out = zpl([{"id": "a", "kind": "rect", "left": 0, "top": 0, "width": 50, "height": 10,
                    "stroke": "#000000", "stroke_width": 0}])
        self.assertEqual(out, render_label_zpl({}, 10, 15))
