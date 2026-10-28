"""Formas, cajas con contenido, paleta y capas del diseño (fase 1 del editor,
docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md):
validación, colores, tono en térmica y reglas sobre formas."""

from django.test import SimpleTestCase
from rest_framework import serializers

from ..design_shapes import resolve_color, thermal_tone
from ..serializers import validate_design


class ShapeColorTests(SimpleTestCase):
    def test_resolve_palette_and_hex(self):
        palette = {"primary": "#1E3A8A"}
        self.assertEqual(resolve_color("primary", palette), "#1e3a8a")
        self.assertEqual(resolve_color("#abc", None), "#aabbcc")
        self.assertIsNone(resolve_color("accent", palette))
        self.assertIsNone(resolve_color(None, palette))

    def test_thermal_tone_thresholds(self):
        self.assertEqual(thermal_tone("#000000"), ("black", 0))
        self.assertEqual(thermal_tone("#ffffff"), ("white", 0))
        tone, level = thermal_tone("#9ca3af")
        self.assertEqual(tone, "gray")
        self.assertTrue(0 < level < 1)

    def test_light_header_gray_still_prints_as_a_light_screen(self):
        # Los encabezados de celda tipo OCA son grises claros: en térmica
        # tienen que verse (trama suave), no desaparecer.
        tone, level = thermal_tone("#e5e7eb")
        self.assertEqual(tone, "gray")
        self.assertTrue(0 < level < 0.25)
        self.assertEqual(thermal_tone("#fafafa"), ("white", 0))


class ShapeValidationTests(SimpleTestCase):
    def test_valid_design_with_shapes_passes(self):
        validate_design(
            {
                "palette": {"primary": "#7c2d12"},
                "shapes": [
                    {
                        "id": "band",
                        "kind": "rect",
                        "left": 0,
                        "top": 0,
                        "width": 18,
                        "height": 100,
                        "fill": "primary",
                        "text": {"value": "LAS LILAS", "color": "#ffffff"},
                    }
                ],
            }
        )

    def test_rejections(self):
        bad = [
            {"shapes": [{"id": "a", "kind": "star", "left": 0, "top": 0}]},
            # "accent" sin palette.accent
            {"shapes": [{"id": "a", "kind": "rect", "left": 0, "top": 0, "fill": "accent"}]},
            {"shapes": [{"id": "a", "kind": "rect", "left": 0, "top": 0, "fill": "rojo"}]},
            {
                "shapes": [
                    {"id": "a", "kind": "rect", "left": 0, "top": 0},
                    {"id": "a", "kind": "rect", "left": 1, "top": 1},
                ]
            },
            # una línea es horizontal (height 0) o vertical (width 0), no las dos
            {"shapes": [{"id": "a", "kind": "line", "left": 0, "top": 0, "width": 50, "height": 20}]},
            {"shapes": [{"id": "a", "kind": "rect", "left": 0, "top": 0, "radius": 25}]},
            {"shapes": [{"id": f"s{i}", "kind": "rect", "left": 0, "top": 0} for i in range(61)]},
            {"palette": {"primary": "azul"}},
        ]
        for design in bad:
            with self.subTest(design=design):
                with self.assertRaises(serializers.ValidationError):
                    validate_design(design)

    def test_rule_can_target_shape(self):
        validate_design(
            {
                "shapes": [{"id": "fragil", "kind": "rect", "left": 0, "top": 0, "width": 10, "height": 5}],
                "rules": [
                    {
                        "when": {"field": "envio", "op": "contains", "value": "frágil"},
                        "then": {"action": "hide", "target": "shape:fragil"},
                    }
                ],
            }
        )


class PaletteIsNotDrawnTests(SimpleTestCase):
    def test_palette_alone_changes_nothing_in_zpl(self):
        from ..zpl import render_label_zpl

        self.assertEqual(
            render_label_zpl({"palette": {"primary": "#111111"}}, 10, 15),
            render_label_zpl({}, 10, 15),
        )


class ShapeRuleTests(SimpleTestCase):
    def test_rules_hide_move_and_set_text_on_shapes(self):
        from ..label_rendering import apply_design_rules

        design = {
            "shapes": [
                {"id": "sello", "kind": "rect", "left": 5, "top": 5, "width": 20, "height": 6,
                 "text": {"value": "FRÁGIL"}}
            ],
            "rules": [
                {"when": {"field": "envio", "op": "equals", "value": "A"},
                 "then": {"action": "hide", "target": "shape:sello"}},
                {"when": {"field": "envio", "op": "equals", "value": "B"},
                 "then": {"action": "move", "target": "shape:sello", "left": 50, "top": 60}},
                {"when": {"field": "envio", "op": "equals", "value": "C"},
                 "then": {"action": "set_text", "target": "shape:sello", "value": "URGENTE"}},
            ],
        }
        self.assertEqual(apply_design_rules(design, {"envio": "A"}, {})["shapes"], [])
        moved = apply_design_rules(design, {"envio": "B"}, {})["shapes"][0]
        self.assertEqual((moved["left"], moved["top"]), (50, 60))
        self.assertEqual(apply_design_rules(design, {"envio": "C"}, {})["shapes"][0]["text"]["value"], "URGENTE")
        # el diseño original no se toca
        self.assertEqual(design["shapes"][0]["left"], 5)
        self.assertEqual(design["shapes"][0]["text"]["value"], "FRÁGIL")
