"""Correcciones de la revisión final de la fase 2: el ZPL imita al PDF en
``shrink_to_fit`` con varios renglones y en el alto de la franja de
``invert``."""

import re

from django.test import SimpleTestCase

from ..zpl import render_label_zpl

LONG_NAME = "Maria Fernanda Gonzalez Rodriguez"


def _zpl(**overrides):
    name = overrides.pop("_name", LONG_NAME)
    entry = {"left": 5, "top": 10, "width": 60, "font_size": 14, "text": "{{destinatario}}"}
    entry.update(overrides)
    return render_label_zpl({"destinatario": entry}, 10, 15, context={"destinatario": name})


def _band_height(zpl):
    return int(re.search(r"\^GB(\d+),(\d+),", zpl).group(2))


class ShrinkWithManyLinesTests(SimpleTestCase):
    def test_native_no_shrink_when_max_lines_over_one(self):
        base = _zpl(max_lines=2, width=30)
        shrunk = _zpl(max_lines=2, width=30, shrink_to_fit=True)
        self.assertEqual(re.search(r"\^A0N,(\d+),0", shrunk).group(1), re.search(r"\^A0N,(\d+),0", base).group(1))

    def test_graphic_no_shrink_when_max_lines_over_one(self):
        base = _zpl(max_lines=2, width=30, font_family="serif")
        shrunk = _zpl(max_lines=2, width=30, font_family="serif", shrink_to_fit=True)
        self.assertEqual(shrunk, base)

    def test_still_shrinks_with_one_line(self):
        base = _zpl(width=30)
        shrunk = _zpl(width=30, shrink_to_fit=True)
        self.assertNotEqual(shrunk, base)


class InvertBandLinesTests(SimpleTestCase):
    def _check(self, **extra):
        one = _band_height(_zpl(invert=True, max_lines=1, **extra))
        short = _band_height(_zpl(invert=True, max_lines=3, _name="Ana", **extra))
        self.assertEqual(short, one)
        two = _band_height(_zpl(invert=True, max_lines=2, **extra))
        roomy = _band_height(_zpl(invert=True, max_lines=5, **extra))
        self.assertGreater(two, one)
        self.assertEqual(roomy, two)

    def test_native_band_follows_real_line_count(self):
        self._check()

    def test_graphic_band_follows_real_line_count(self):
        self._check(font_family="serif")


class NativeOverflowTests(SimpleTestCase):
    """Con varios renglones la Zebra encima sobre el último lo que no entra
    (visto en Labelary): el texto se corta antes, con "…", como el PDF."""

    def _payload(self, zpl):
        return re.search(r"\^FD(.*?)\^FS", zpl).group(1)

    def test_long_text_is_cut_with_ellipsis(self):
        name = LONG_NAME + " de la Santisima Trinidad"
        payload = self._payload(_zpl(_name=name, max_lines=2, width=30))
        self.assertTrue(payload.endswith("…"))
        self.assertLess(len(payload), len(name))

    def test_text_that_fits_is_untouched(self):
        self.assertEqual(self._payload(_zpl(_name="Ana Paz", max_lines=2, width=30)), "Ana Paz")
