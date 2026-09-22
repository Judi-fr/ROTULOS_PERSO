"""Tests de la app labels: catálogo de variables, plantillas y su API."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ..models import (
    ElementLayout,
    LayoutElement,
    LayoutElementType,
    LayoutVariable,
    mm_to_px,
)

User = get_user_model()

URL_ELEMENT_LAYOUTS = "/api/v1/labels/element-layouts/"
URL_LAYOUT_VARIABLES = "/api/v1/labels/layout-variables/"


class ConversionMmPxTests(APITestCase):
    def test_mm_to_px_a_300_dpi(self):
        # 25.4 mm = 1 pulgada -> a 300 DPI son 300 px.
        self.assertEqual(mm_to_px(25.4, 300), 300)

    def test_propiedades_px_de_la_plantilla(self):
        p = ElementLayout(name="x", width_mm=25.4, height_mm=50.8, dpi=300)
        self.assertEqual(p.width_px, 300)
        self.assertEqual(p.height_px, 600)


class BaseLabelsTests(APITestCase):
    """Usuarios y helpers compartidos.

    Las ocho variables del sistema ya existen: las siembra la migración de
    datos, que corre al crear la base de tests.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="user@example.com", email="user@example.com", password="Abc123def"
        )
        self.admin = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="Abc123def",
            is_staff=True,
        )
        self.client.force_authenticate(self.user)

    def _payload(self):
        return {
            "name": "Rótulo 10x15",
            "width_mm": "100.00",
            "height_mm": "150.00",
            "dpi": 300,
            "metadata": {"fondo": "#ffffff"},
            "elements": [
                {
                    # Sin 'element_type': por defecto es variable, así que los
                    # cuerpos que ya usaba el frontend siguen siendo válidos.
                    "variable": "qr",
                    "x_mm": "5.00",
                    "y_mm": "5.00",
                    "width_mm": "30.00",
                    "height_mm": "30.00",
                    "style": {},
                    "order": 0,
                },
                {
                    "element_type": LayoutElementType.STATIC_TEXT,
                    "content": "DESTINATARIO:",
                    "x_mm": "40.00",
                    "y_mm": "5.00",
                    "width_mm": "55.00",
                    "height_mm": "6.00",
                    "style": {"negrita": True, "tamano_pt": 8},
                    "order": 1,
                },
                {
                    "element_type": LayoutElementType.VARIABLE,
                    "variable": "destinatario",
                    "x_mm": "40.00",
                    "y_mm": "12.00",
                    "width_mm": "55.00",
                    "height_mm": "20.00",
                    "style": {"tamano_pt": 12},
                    "order": 2,
                },
            ],
        }


class CatalogoVariablesTests(BaseLabelsTests):
    def test_requiere_autenticacion(self):
        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get(URL_LAYOUT_VARIABLES).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_listado_trae_las_variables_del_sistema(self):
        resp = self.client.get(URL_LAYOUT_VARIABLES)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Sin paginar: la respuesta es la lista completa, no un objeto con
        # 'results'. El editor la consume entera para poblar su selector.
        codes = {v["code"] for v in resp.data}
        # El total se compara contra la base y no contra un número escrito acá:
        # las variables del sistema las siembran las migraciones, y agregar una
        # es algo que va a volver a pasar. Lo que importa es que el listado
        # traiga todas y que no se cuele ninguna que no sea del sistema.
        self.assertEqual(
            len(resp.data),
            LayoutVariable.objects.filter(is_system=True, is_active=True).count(),
        )
        self.assertIn("qr", codes)
        self.assertIn("codigo_postal", codes)
        self.assertTrue(all(v["is_system"] for v in resp.data))

    def test_listado_oculta_las_inactivas(self):
        LayoutVariable.objects.filter(code="qr").update(is_active=False)

        codes = {v["code"] for v in self.client.get(URL_LAYOUT_VARIABLES).data}
        self.assertNotIn("qr", codes)

        con_inactivas = self.client.get(URL_LAYOUT_VARIABLES, {"incluir_inactivas": "1"})
        self.assertIn("qr", {v["code"] for v in con_inactivas.data})

    def test_un_usuario_comun_no_puede_crear_variables(self):
        resp = self.client.post(
            URL_LAYOUT_VARIABLES, {"code": "numero_bulto", "label": "Bulto"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(LayoutVariable.objects.filter(code="numero_bulto").exists())

    def test_un_administrador_puede_crear_variables(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            URL_LAYOUT_VARIABLES,
            {
                "code": "numero_bulto",
                "label": "Número de Bulto",
                "description": "Cuál de los bultos del envío es este.",
                "data_type": "texto",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        variable = LayoutVariable.objects.get(code="numero_bulto")
        # Trazabilidad: la fija la vista, no el cuerpo.
        self.assertEqual(variable.created_by, self.admin)
        # Las creadas por API nunca son del sistema.
        self.assertFalse(variable.is_system)

    def test_el_grupo_administradores_tambien_habilita(self):
        # is_staff no es la única vía: el proyecto modela los roles como Groups.
        disenador = User.objects.create_user(
            username="d@example.com", email="d@example.com", password="Abc123def"
        )
        disenador.groups.add(Group.objects.get(name="admin"))
        self.client.force_authenticate(disenador)

        resp = self.client.post(
            URL_LAYOUT_VARIABLES,
            {"code": "transportista", "label": "Transportista"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_codigo_invalido_da_400(self):
        self.client.force_authenticate(self.admin)
        for codigo in ("Número Bulto", "numero-bulto", "1bulto", ""):
            resp = self.client.post(
                URL_LAYOUT_VARIABLES, {"code": codigo, "label": "x"}, format="json"
            )
            self.assertEqual(
                resp.status_code, status.HTTP_400_BAD_REQUEST, f"código: {codigo!r}"
            )

    def test_codigo_duplicado_da_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            URL_LAYOUT_VARIABLES, {"code": "qr", "label": "Otro QR"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_se_puede_renombrar_el_codigo_de_una_del_sistema(self):
        self.client.force_authenticate(self.admin)
        qr = LayoutVariable.objects.get(code="qr")
        resp = self.client.patch(
            f"{URL_LAYOUT_VARIABLES}{qr.id}/", {"code": "qr_nuevo"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # La etiqueta sí se puede corregir.
        resp = self.client.patch(
            f"{URL_LAYOUT_VARIABLES}{qr.id}/", {"label": "Código QR"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_no_se_puede_eliminar_una_variable_del_sistema(self):
        self.client.force_authenticate(self.admin)
        qr = LayoutVariable.objects.get(code="qr")
        resp = self.client.delete(f"{URL_LAYOUT_VARIABLES}{qr.id}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(LayoutVariable.objects.filter(pk=qr.pk).exists())

    def test_no_se_puede_eliminar_una_variable_en_uso(self):
        self.client.force_authenticate(self.admin)
        variable = LayoutVariable.objects.create(code="temporal", label="Temporal")
        layout = ElementLayout.objects.create(
            name="p", width_mm=10, height_mm=10, dpi=300
        )
        LayoutElement.objects.create(
            layout=layout,
            element_type=LayoutElementType.VARIABLE,
            variable=variable,
            x_mm=0,
            y_mm=0,
            width_mm=5,
            height_mm=5,
        )

        resp = self.client.delete(f"{URL_LAYOUT_VARIABLES}{variable.id}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(LayoutVariable.objects.filter(pk=variable.pk).exists())

    def test_se_puede_eliminar_una_variable_sin_uso(self):
        self.client.force_authenticate(self.admin)
        variable = LayoutVariable.objects.create(code="temporal", label="Temporal")
        resp = self.client.delete(f"{URL_LAYOUT_VARIABLES}{variable.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(LayoutVariable.objects.filter(pk=variable.pk).exists())

    def test_crear_variable_deja_auditlog(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            URL_LAYOUT_VARIABLES,
            {"code": "numero_bulto", "label": "Número de Bulto"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        log = AuditLog.objects.filter(action="variable_rotulo.create").latest("created_at")
        self.assertEqual(log.actor_id, self.admin.id)
        self.assertEqual(log.target_id, str(resp.data["id"]))

    def test_editar_variable_deja_auditlog(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(self.admin)
        qr = LayoutVariable.objects.get(code="qr")
        resp = self.client.patch(
            f"{URL_LAYOUT_VARIABLES}{qr.id}/", {"label": "Código QR"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        log = AuditLog.objects.filter(action="variable_rotulo.update").latest("created_at")
        self.assertEqual(log.actor_id, self.admin.id)
        self.assertEqual(log.target_id, str(qr.pk))

    def test_eliminar_variable_deja_auditlog(self):
        from apps.audit.models import AuditLog

        self.client.force_authenticate(self.admin)
        variable = LayoutVariable.objects.create(code="temporal2", label="Temporal 2")
        resp = self.client.delete(f"{URL_LAYOUT_VARIABLES}{variable.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        log = AuditLog.objects.filter(action="variable_rotulo.delete").latest("created_at")
        self.assertEqual(log.actor_id, self.admin.id)
        self.assertEqual(log.target_id, str(variable.pk))


class PlantillaAPITests(BaseLabelsTests):
    def test_requiere_autenticacion(self):
        self.client.force_authenticate(None)
        resp = self.client.get(URL_ELEMENT_LAYOUTS)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_crear_plantilla_con_elementos(self):
        resp = self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        layout = ElementLayout.objects.get()
        # created_by se fija con el usuario autenticado, no desde el body.
        self.assertEqual(layout.created_by, self.user)
        self.assertEqual(layout.elements.count(), 3)
        # px derivados: 100 mm @ 300 DPI ≈ 1181 px.
        self.assertEqual(resp.data["width_px"], mm_to_px(100, 300))

        # El texto estático se guardó como tal, sin variable asociada.
        static = layout.elements.get(element_type=LayoutElementType.STATIC_TEXT)
        self.assertEqual(static.content, "DESTINATARIO:")
        self.assertIsNone(static.variable)

    def test_la_variable_viaja_por_codigo_con_su_etiqueta(self):
        self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        layout = ElementLayout.objects.get()

        resp = self.client.get(f"{URL_ELEMENT_LAYOUTS}{layout.id}/")
        qr = next(e for e in resp.data["elements"] if e["variable"] == "qr")
        self.assertEqual(qr["variable_display"], "QR")
        self.assertEqual(qr["variable_data_type"], "qr")

        # Un texto estático no tiene variable ni etiqueta.
        static = next(
            e for e in resp.data["elements"] if e["element_type"] == LayoutElementType.STATIC_TEXT
        )
        self.assertIsNone(static["variable"])
        self.assertIsNone(static["variable_display"])

    def test_editar_reemplaza_elementos(self):
        self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        layout = ElementLayout.objects.get()
        resp = self.client.patch(
            f"{URL_ELEMENT_LAYOUTS}{layout.id}/",
            {
                "elements": [
                    {
                        "variable": "numero_pedido",
                        "x_mm": "0.00",
                        "y_mm": "0.00",
                        "width_mm": "50.00",
                        "height_mm": "10.00",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(layout.elements.count(), 1)
        self.assertEqual(layout.elements.get().variable.code, "numero_pedido")

    def test_patch_sin_elementos_no_los_toca(self):
        self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        layout = ElementLayout.objects.get()
        resp = self.client.patch(
            f"{URL_ELEMENT_LAYOUTS}{layout.id}/", {"name": "Nuevo nombre"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(layout.elements.count(), 3)

    def test_variable_inexistente_da_400(self):
        payload = self._payload()
        payload["elements"][0]["variable"] = "inexistente"
        resp = self.client.post(URL_ELEMENT_LAYOUTS, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_listar_no_escala_las_consultas_con_los_elementos(self):
        # Verifica que el Prefetch evita el N+1. No se fija un número concreto
        # de consultas —eso se rompería con cualquier cambio inocuo— sino que
        # agregar plantillas y elementos no agregue consultas al listado.
        self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        with CaptureQueriesContext(connection) as con_una:
            self.client.get(URL_ELEMENT_LAYOUTS)

        for i in range(3):
            payload = self._payload()
            payload["name"] = f"Otra {i}"
            self.client.post(URL_ELEMENT_LAYOUTS, payload, format="json")

        with CaptureQueriesContext(connection) as con_cuatro:
            self.client.get(URL_ELEMENT_LAYOUTS)

        self.assertEqual(len(con_cuatro), len(con_una))

    def test_crear_plantilla_deja_auditlog(self):
        from apps.audit.models import AuditLog

        resp = self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        log = AuditLog.objects.filter(action="plantilla.create").latest("created_at")
        self.assertEqual(log.actor_id, self.user.id)
        self.assertEqual(log.target_id, str(resp.data["id"]))

    def test_editar_plantilla_deja_auditlog(self):
        from apps.audit.models import AuditLog

        self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        layout = ElementLayout.objects.get()
        resp = self.client.patch(
            f"{URL_ELEMENT_LAYOUTS}{layout.id}/", {"name": "Nuevo nombre"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        log = AuditLog.objects.filter(action="plantilla.update").latest("created_at")
        self.assertEqual(log.actor_id, self.user.id)
        self.assertEqual(log.target_id, str(layout.pk))

    def test_eliminar_plantilla_deja_auditlog(self):
        from apps.audit.models import AuditLog

        self.client.post(URL_ELEMENT_LAYOUTS, self._payload(), format="json")
        layout = ElementLayout.objects.get()
        resp = self.client.delete(f"{URL_ELEMENT_LAYOUTS}{layout.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        log = AuditLog.objects.filter(action="plantilla.delete").latest("created_at")
        self.assertEqual(log.actor_id, self.user.id)
        self.assertEqual(log.target_id, str(layout.pk))
        self.assertFalse(ElementLayout.objects.filter(pk=layout.pk).exists())


class CoherenciaElementosTests(BaseLabelsTests):
    """El tipo del elemento manda qué campos puede traer."""

    def _post_elemento(self, elemento):
        payload = self._payload()
        payload["elements"] = [elemento]
        return self.client.post(URL_ELEMENT_LAYOUTS, payload, format="json")

    def _base(self, **extra):
        return {
            "x_mm": "0.00",
            "y_mm": "0.00",
            "width_mm": "10.00",
            "height_mm": "10.00",
            **extra,
        }

    def test_variable_sin_variable_da_400(self):
        resp = self._post_elemento(self._base(element_type=LayoutElementType.VARIABLE))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_variable_con_contenido_da_400(self):
        resp = self._post_elemento(
            self._base(
                element_type=LayoutElementType.VARIABLE, variable="qr", content="no corresponde"
            )
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_texto_estatico_sin_contenido_da_400(self):
        resp = self._post_elemento(self._base(element_type=LayoutElementType.STATIC_TEXT))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_texto_estatico_con_variable_da_400(self):
        resp = self._post_elemento(
            self._base(
                element_type=LayoutElementType.STATIC_TEXT, content="Hola", variable="qr"
            )
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_linea_con_variable_da_400(self):
        resp = self._post_elemento(self._base(element_type=LayoutElementType.LINE, variable="qr"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_linea_sin_variable_ni_contenido_es_valida(self):
        resp = self._post_elemento(self._base(element_type=LayoutElementType.LINE))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_la_base_tambien_rechaza_filas_incoherentes(self):
        # La restricción de base cubre las vías que no pasan por el serializer
        # (shell, scripts, importadores futuros).
        layout = ElementLayout.objects.create(
            name="p", width_mm=10, height_mm=10, dpi=300
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            LayoutElement.objects.create(
                layout=layout,
                element_type=LayoutElementType.VARIABLE,
                variable=None,
                x_mm=0,
                y_mm=0,
                width_mm=5,
                height_mm=5,
            )


class EstiloTests(BaseLabelsTests):
    def _post_con_estilo(self, estilo):
        payload = self._payload()
        payload["elements"] = [
            {
                "variable": "qr",
                "x_mm": "0.00",
                "y_mm": "0.00",
                "width_mm": "10.00",
                "height_mm": "10.00",
                "style": estilo,
            }
        ]
        return self.client.post(URL_ELEMENT_LAYOUTS, payload, format="json")

    def test_estilo_valido(self):
        resp = self._post_con_estilo(
            {
                "tamano_pt": 12,
                "alineacion": "centro",
                "negrita": True,
                "color": "#ff0000",
            }
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_estilo_vacio_es_valido(self):
        self.assertEqual(
            self._post_con_estilo({}).status_code, status.HTTP_201_CREATED
        )

    def test_clave_desconocida_da_400(self):
        # Casi siempre es un typo que si no, se descubriría recién al imprimir.
        resp = self._post_con_estilo({"font_size": 12})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_alineacion_invalida_da_400(self):
        resp = self._post_con_estilo({"alineacion": "arriba"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tamano_no_numerico_da_400(self):
        resp = self._post_con_estilo({"tamano_pt": "grande"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_color_mal_formado_da_400(self):
        resp = self._post_con_estilo({"color": "rojo"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_una_familia_del_catalogo_es_valida(self):
        self.assertEqual(
            self._post_con_estilo({"fuente": "courier"}).status_code,
            status.HTTP_201_CREATED,
        )

    def test_fuente_nula_es_valida(self):
        """Nulo significa «la familia por defecto», que es un caso normal."""
        self.assertEqual(
            self._post_con_estilo({"fuente": None}).status_code,
            status.HTTP_201_CREATED,
        )

    def test_una_familia_desconocida_da_400(self):
        """Guardarla no fallaría; imprimirla saldría con otra tipografía."""
        resp = self._post_con_estilo({"fuente": "Comic Sans MS"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class FuentesAPITests(BaseLabelsTests):
    """El editor consulta al servidor qué familias puede ofrecer."""

    def test_lista_las_familias(self):
        resp = self.client.get(reverse("fonts"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        codes = [f["code"] for f in resp.data]
        self.assertIn("helvetica", codes)
        self.assertIn("times", codes)
        self.assertIn("courier", codes)
        # Cada una trae lo que la interfaz necesita para armar el selector y
        # para avisar si la vista previa en PNG no va a estar disponible.
        for family in resp.data:
            self.assertIn("label", family)
            self.assertIn("png_available", family)

    def test_requiere_autenticacion(self):
        # force_authenticate lo dejó logueado en setUp; se deshace pasando None.
        self.client.force_authenticate(None)
        resp = self.client.get(reverse("fonts"))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
