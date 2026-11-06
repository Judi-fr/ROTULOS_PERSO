"""Lo que el frontend le pide al backend por nombre: una clave de permiso mal
escrita no da error, solo esconde una pantalla o un botón para todos."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from apps.accounts.permissions_map import PERMISSIONS

FRONTEND = Path(settings.BASE_DIR).parent / "frontend"


def pages():
    return [path for path in sorted(FRONTEND.glob("*.html")) if path.name != "index_test.html"]


class StandardTemplateNameTests(SimpleTestCase):
    def test_el_frontend_busca_la_estandar_por_su_nombre_real(self):
        # "De cero" abre esta plantilla buscándola por nombre: otro nombre no da
        # error, solo deja al editor con los campos por defecto.
        from apps.labels.gallery import STANDARD_TEMPLATE_NAME

        source = (FRONTEND / "editor_rotulos.html").read_text(encoding="utf-8")
        self.assertIn(f'STANDARD_TEMPLATE_NAME = "{STANDARD_TEMPLATE_NAME}"', source)


class PermissionKeysTests(SimpleTestCase):
    def test_las_pantallas_piden_permisos_que_existen(self):
        for page in pages():
            match = re.search(r'<body[^>]*data-requires="([^"]+)"', page.read_text(encoding="utf-8"))
            if match:
                for key in match.group(1).split(","):
                    self.assertIn(key.strip(), PERMISSIONS, page.name)

    def test_los_links_de_la_barra_piden_permisos_que_existen(self):
        for page in pages():
            for key in re.findall(r'"perm":\s*"([^"]+)"', page.read_text(encoding="utf-8")):
                self.assertIn(key, PERMISSIONS, page.name)

    def test_el_buscador_pide_permisos_que_existen(self):
        utils = (FRONTEND / "assets" / "js" / "utils.js").read_text(encoding="utf-8")
        keys = re.findall(r'\bperm:\s*"([^"]+)"', utils)
        self.assertTrue(keys)
        for key in keys:
            self.assertIn(key, PERMISSIONS)


class PlatformPickerTests(SimpleTestCase):
    def test_cada_boton_de_plataforma_muestra_algo(self):
        html = (FRONTEND / "tiendas.html").read_text(encoding="utf-8")
        picks = set(re.findall(r'data-platform-pick="([\w-]+)"', html))
        blocks = set(re.findall(r'\bdata-platform="([\w-]+)"', html))
        self.assertTrue(picks)
        self.assertEqual(picks, blocks)
