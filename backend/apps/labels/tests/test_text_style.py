"""Tipografías del rótulo (fase 2 del editor): registro de fuentes,
variantes, acentos y que el editor use los mismos archivos."""

import hashlib
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from ..text_style import FONT_FAMILIES, FONTS_DIR, pdf_font_name, ttf_path

ACCENTS = "áéíóúÁÉÍÓÚñÑ¡¿°"


class FontRegistryTests(SimpleTestCase):
    def test_every_family_variant_resolves(self):
        for family in FONT_FAMILIES:
            for bold in (False, True):
                for italic in (False, True):
                    with self.subTest(family=family, bold=bold, italic=italic):
                        name = pdf_font_name(family, bold, italic)
                        self.assertIsNotNone(pdfmetrics.getFont(name))
                        self.assertTrue(ttf_path(family, bold, italic).exists())

    def test_missing_variant_falls_back(self):
        self.assertEqual(pdf_font_name("script", False, True), pdf_font_name("script", False, False))
        self.assertEqual(pdf_font_name("condensed", True, True), pdf_font_name("condensed", True, False))

    def test_unknown_family_is_sans(self):
        self.assertEqual(pdf_font_name("comic", False, False), "Helvetica")
        self.assertEqual(pdf_font_name(None, True, False), "Helvetica-Bold")

    def test_accents_have_glyphs(self):
        for path in sorted(FONTS_DIR.glob("*.ttf")):
            with self.subTest(font=path.name):
                face = TTFont(path.stem, str(path)).face
                missing = [char for char in ACCENTS if ord(char) not in face.charToGlyph]
                self.assertEqual(missing, [])

    def test_frontend_copies_are_identical(self):
        frontend = Path(settings.BASE_DIR).parent / "frontend" / "assets" / "fonts"
        for path in sorted(FONTS_DIR.glob("*.ttf")):
            if path.name.startswith("LiberationSans"):
                continue  # solo la usa el backend (ZPL como gráfico)
            with self.subTest(font=path.name):
                copy = frontend / path.name
                self.assertTrue(copy.exists(), f"falta frontend/assets/fonts/{path.name}")
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    hashlib.sha256(copy.read_bytes()).hexdigest(),
                )


class TypographyValidationTests(SimpleTestCase):
    STYLE = {"font_family": "serif", "italic": True, "uppercase": True, "letter_spacing": 1.5,
             "max_lines": 2, "line_height": 1.2}

    def test_typography_keys_accepted(self):
        from ..serializers import validate_design

        validate_design({
            "palette": {"primary": "#7c2d12"},
            "destinatario": {"left": 5, "top": 20, "text": "{{destinatario}}", "color": "primary", **self.STYLE},
            "texts": [{"left": 5, "top": 10, "text": "DESTINATARIA", "color": "#78716c", **self.STYLE}],
            "shapes": [{"id": "c", "kind": "rect", "left": 0, "top": 50, "width": 50, "height": 10,
                        "text": {"value": "{{cp}}", **self.STYLE}}],
        })

    def test_typography_rejections(self):
        from rest_framework import serializers

        from ..serializers import validate_design

        for bad in ({"font_family": "comic"}, {"letter_spacing": 11}, {"max_lines": 0}, {"max_lines": 11},
                    {"line_height": 0.5}, {"italic": "sí"}, {"uppercase": 1}, {"color": "gris"},
                    {"color": "accent"}):
            for design in (
                {"cp": {"left": 1, "top": 1, "text": "{{cp}}", **bad}},
                {"texts": [{"left": 1, "top": 1, "text": "x", **bad}]},
                {"shapes": [{"id": "c", "kind": "rect", "left": 0, "top": 0, "text": {"value": "x", **bad}}]},
            ):
                with self.subTest(bad=bad, where=next(iter(design))), self.assertRaises(serializers.ValidationError):
                    validate_design(design)
