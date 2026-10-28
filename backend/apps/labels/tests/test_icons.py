"""Íconos del rótulo (fase 2): forma ``kind: "icon"`` con su geometría en
icons.json, dibujada igual en PDF y ZPL."""

import hashlib
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from rest_framework import serializers

from ..icons import ICON_NAMES, ICONS_FILE, icon_bitmap
from ..label_rendering import CM_TO_POINTS, draw_label_page, render_label_pdf
from ..serializers import validate_design
from ..zpl import render_label_zpl
from .test_shapes_pdf import RecordingCanvas


class PathRecorder:
    def __init__(self):
        self.points = []

    def moveTo(self, x, y):
        self.points.append((x, y))

    def lineTo(self, x, y):
        self.points.append((x, y))

    def close(self):
        pass


class IconCanvas(RecordingCanvas):
    def beginPath(self):
        return PathRecorder()

    def drawPath(self, path, stroke=1, fill=0):
        self.calls.append(("path", path.points, {"stroke": stroke, "fill": fill}, self.fill, self.stroke))

    def circle(self, x, y, r, stroke=1, fill=0):
        self.calls.append(("circle", (x, y, r), {"stroke": stroke, "fill": fill}, self.fill, self.stroke))

    def setLineJoin(self, value):
        pass

    def setLineCap(self, value):
        pass


def icon(name="fragil", **extra):
    return {"id": "i", "kind": "icon", "icon": name, "left": 0, "top": 0, "width": 40, "height": 10, **extra}


class IconTests(SimpleTestCase):
    def test_icon_validation(self):
        validate_design({"shapes": [icon()]})
        for bad in (icon("bomba"), {k: v for k, v in icon().items() if k != "icon"}, icon(text={"value": "x"})):
            with self.subTest(bad=bad), self.assertRaises(serializers.ValidationError):
                validate_design({"shapes": [bad]})

    def test_icon_pdf_is_square_and_centered(self):
        pdf = IconCanvas()
        draw_label_page(pdf, {"shapes": [icon()]}, 10, 15)
        points = [point for call in pdf.of("path", "circle") for point in
                  (call[1] if call[0] == "path" else [call[1][:2]])]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        width_pt, height_pt = 10 * CM_TO_POINTS, 15 * CM_TO_POINTS
        side = 0.10 * height_pt  # el lado menor de la caja (40 % × 10 %)
        center_x = 0.20 * width_pt
        self.assertLessEqual(max(xs) - min(xs), side + 0.5)
        self.assertLess(abs((max(xs) + min(xs)) / 2 - center_x), side / 2)
        self.assertGreaterEqual(min(ys), height_pt * 0.90 - 0.5)

    def test_icon_color_uses_fill(self):
        pdf = IconCanvas()
        draw_label_page(pdf, {"shapes": [icon("corazon", fill="#dc2626")]}, 10, 15)
        [heart] = pdf.of("path")
        self.assertEqual(heart[3], (220 / 255, 38 / 255, 38 / 255))

    def test_icon_zpl_is_graphic(self):
        out = render_label_zpl({"shapes": [icon()]}, 10, 15)
        self.assertIn("^GFA", out)

    def test_every_icon_draws(self):
        for name in ICON_NAMES:
            with self.subTest(icon=name):
                design = {"shapes": [icon(name)]}
                self.assertTrue(render_label_pdf(design, 10, 15).startswith(b"%PDF"))
                self.assertIn("^GFA", render_label_zpl(design, 10, 15))
                bitmap = icon_bitmap(name, 80)
                self.assertIsNotNone(bitmap.point(lambda v: 255 if v else 0).getbbox())

    def test_icons_json_copies_identical(self):
        frontend = Path(settings.BASE_DIR).parent / "frontend" / "assets" / "data" / "label_icons.json"
        self.assertEqual(hashlib.sha256(ICONS_FILE.read_bytes()).hexdigest(),
                         hashlib.sha256(frontend.read_bytes()).hexdigest())
