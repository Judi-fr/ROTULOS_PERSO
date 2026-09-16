"""Tests de la app labels: catálogo de variables, plantillas y su API."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    ElementoPlantilla,
    Plantilla,
    TipoElemento,
    VariableRotulo,
    mm_a_px,
)

User = get_user_model()

URL_PLANTILLAS = "/api/v1/labels/plantillas/"
URL_VARIABLES = "/api/v1/labels/variables/"


class ConversionMmPxTests(APITestCase):
    def test_mm_a_px_a_300_dpi(self):
        # 25.4 mm = 1 pulgada -> a 300 DPI son 300 px.
        self.assertEqual(mm_a_px(25.4, 300), 300)

    def test_propiedades_px_de_la_plantilla(self):
        p = Plantilla(nombre="x", ancho_mm=25.4, alto_mm=50.8, dpi=300)
        self.assertEqual(p.ancho_px, 300)
        self.assertEqual(p.alto_px, 600)


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
            "nombre": "Rótulo 10x15",
            "ancho_mm": "100.00",
            "alto_mm": "150.00",
            "dpi": 300,
            "metadatos": {"fondo": "#ffffff"},
            "elementos": [
                {
                    # Sin 'tipo': por defecto es variable, así que los cuerpos
                    # que ya usaba el frontend siguen siendo válidos.
                    "variable": "qr",
                    "x_mm": "5.00",
                    "y_mm": "5.00",
                    "ancho_mm": "30.00",
                    "alto_mm": "30.00",
                    "estilo": {},
                    "orden": 0,
                },
                {
                    "tipo": TipoElemento.TEXTO_ESTATICO,
                    "contenido": "DESTINATARIO:",
                    "x_mm": "40.00",
                    "y_mm": "5.00",
                    "ancho_mm": "55.00",
                    "alto_mm": "6.00",
                    "estilo": {"negrita": True, "tamano_pt": 8},
                    "orden": 1,
                },
                {
                    "tipo": TipoElemento.VARIABLE,
                    "variable": "destinatario",
                    "x_mm": "40.00",
                    "y_mm": "12.00",
                    "ancho_mm": "55.00",
                    "alto_mm": "20.00",
                    "estilo": {"tamano_pt": 12},
                    "orden": 2,
                },
            ],
        }


class CatalogoVariablesTests(BaseLabelsTests):
    def test_requiere_autenticacion(self):
        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get(URL_VARIABLES).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_listado_trae_las_variables_del_sistema(self):
        resp = self.client.get(URL_VARIABLES)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Sin paginar: la respuesta es la lista completa, no un objeto con
        # 'results'. El editor la consume entera para poblar su selector.
        codigos = {v["codigo"] for v in resp.data}
        # El total se compara contra la base y no contra un número escrito acá:
        # las variables del sistema las siembran las migraciones, y agregar una
        # es algo que va a volver a pasar. Lo que importa es que el listado
        # traiga todas y que no se cuele ninguna que no sea del sistema.
        self.assertEqual(
            len(resp.data),
            VariableRotulo.objects.filter(es_sistema=True, activa=True).count(),
        )
        self.assertIn("qr", codigos)
        self.assertIn("codigo_postal", codigos)
        self.assertTrue(all(v["es_sistema"] for v in resp.data))

    def test_listado_oculta_las_inactivas(self):
        VariableRotulo.objects.filter(codigo="qr").update(activa=False)

        codigos = {v["codigo"] for v in self.client.get(URL_VARIABLES).data}
        self.assertNotIn("qr", codigos)

        con_inactivas = self.client.get(URL_VARIABLES, {"incluir_inactivas": "1"})
        self.assertIn("qr", {v["codigo"] for v in con_inactivas.data})

    def test_un_usuario_comun_no_puede_crear_variables(self):
        resp = self.client.post(
            URL_VARIABLES, {"codigo": "numero_bulto", "etiqueta": "Bulto"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(VariableRotulo.objects.filter(codigo="numero_bulto").exists())

    def test_un_administrador_puede_crear_variables(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            URL_VARIABLES,
            {
                "codigo": "numero_bulto",
                "etiqueta": "Número de Bulto",
                "descripcion": "Cuál de los bultos del envío es este.",
                "tipo_dato": "texto",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        variable = VariableRotulo.objects.get(codigo="numero_bulto")
        # Trazabilidad: la fija la vista, no el cuerpo.
        self.assertEqual(variable.creada_por, self.admin)
        # Las creadas por API nunca son del sistema.
        self.assertFalse(variable.es_sistema)

    def test_el_grupo_administradores_tambien_habilita(self):
        # is_staff no es la única vía: el proyecto modela los roles como Groups.
        disenador = User.objects.create_user(
            username="d@example.com", email="d@example.com", password="Abc123def"
        )
        disenador.groups.add(Group.objects.get(name="administradores"))
        self.client.force_authenticate(disenador)

        resp = self.client.post(
            URL_VARIABLES,
            {"codigo": "transportista", "etiqueta": "Transportista"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_codigo_invalido_da_400(self):
        self.client.force_authenticate(self.admin)
        for codigo in ("Número Bulto", "numero-bulto", "1bulto", ""):
            resp = self.client.post(
                URL_VARIABLES, {"codigo": codigo, "etiqueta": "x"}, format="json"
            )
            self.assertEqual(
                resp.status_code, status.HTTP_400_BAD_REQUEST, f"código: {codigo!r}"
            )

    def test_codigo_duplicado_da_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            URL_VARIABLES, {"codigo": "qr", "etiqueta": "Otro QR"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_se_puede_renombrar_el_codigo_de_una_del_sistema(self):
        self.client.force_authenticate(self.admin)
        qr = VariableRotulo.objects.get(codigo="qr")
        resp = self.client.patch(
            f"{URL_VARIABLES}{qr.id}/", {"codigo": "qr_nuevo"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # La etiqueta sí se puede corregir.
        resp = self.client.patch(
            f"{URL_VARIABLES}{qr.id}/", {"etiqueta": "Código QR"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_no_se_puede_eliminar_una_variable_del_sistema(self):
        self.client.force_authenticate(self.admin)
        qr = VariableRotulo.objects.get(codigo="qr")
        resp = self.client.delete(f"{URL_VARIABLES}{qr.id}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(VariableRotulo.objects.filter(pk=qr.pk).exists())

    def test_no_se_puede_eliminar_una_variable_en_uso(self):
        self.client.force_authenticate(self.admin)
        variable = VariableRotulo.objects.create(codigo="temporal", etiqueta="Temporal")
        plantilla = Plantilla.objects.create(
            nombre="p", ancho_mm=10, alto_mm=10, dpi=300
        )
        ElementoPlantilla.objects.create(
            plantilla=plantilla,
            tipo=TipoElemento.VARIABLE,
            variable=variable,
            x_mm=0,
            y_mm=0,
            ancho_mm=5,
            alto_mm=5,
        )

        resp = self.client.delete(f"{URL_VARIABLES}{variable.id}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(VariableRotulo.objects.filter(pk=variable.pk).exists())

    def test_se_puede_eliminar_una_variable_sin_uso(self):
        self.client.force_authenticate(self.admin)
        variable = VariableRotulo.objects.create(codigo="temporal", etiqueta="Temporal")
        resp = self.client.delete(f"{URL_VARIABLES}{variable.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(VariableRotulo.objects.filter(pk=variable.pk).exists())


class PlantillaAPITests(BaseLabelsTests):
    def test_requiere_autenticacion(self):
        self.client.force_authenticate(None)
        resp = self.client.get(URL_PLANTILLAS)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_crear_plantilla_con_elementos(self):
        resp = self.client.post(URL_PLANTILLAS, self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        plantilla = Plantilla.objects.get()
        # creada_por se fija con el usuario autenticado, no desde el body.
        self.assertEqual(plantilla.creada_por, self.user)
        self.assertEqual(plantilla.elementos.count(), 3)
        # px derivados: 100 mm @ 300 DPI ≈ 1181 px.
        self.assertEqual(resp.data["ancho_px"], mm_a_px(100, 300))

        # El texto estático se guardó como tal, sin variable asociada.
        estatico = plantilla.elementos.get(tipo=TipoElemento.TEXTO_ESTATICO)
        self.assertEqual(estatico.contenido, "DESTINATARIO:")
        self.assertIsNone(estatico.variable)

    def test_la_variable_viaja_por_codigo_con_su_etiqueta(self):
        self.client.post(URL_PLANTILLAS, self._payload(), format="json")
        plantilla = Plantilla.objects.get()

        resp = self.client.get(f"{URL_PLANTILLAS}{plantilla.id}/")
        qr = next(e for e in resp.data["elementos"] if e["variable"] == "qr")
        self.assertEqual(qr["variable_display"], "QR")
        self.assertEqual(qr["variable_tipo_dato"], "qr")

        # Un texto estático no tiene variable ni etiqueta.
        estatico = next(
            e for e in resp.data["elementos"] if e["tipo"] == TipoElemento.TEXTO_ESTATICO
        )
        self.assertIsNone(estatico["variable"])
        self.assertIsNone(estatico["variable_display"])

    def test_editar_reemplaza_elementos(self):
        self.client.post(URL_PLANTILLAS, self._payload(), format="json")
        plantilla = Plantilla.objects.get()
        resp = self.client.patch(
            f"{URL_PLANTILLAS}{plantilla.id}/",
            {
                "elementos": [
                    {
                        "variable": "numero_pedido",
                        "x_mm": "0.00",
                        "y_mm": "0.00",
                        "ancho_mm": "50.00",
                        "alto_mm": "10.00",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(plantilla.elementos.count(), 1)
        self.assertEqual(plantilla.elementos.get().variable.codigo, "numero_pedido")

    def test_patch_sin_elementos_no_los_toca(self):
        self.client.post(URL_PLANTILLAS, self._payload(), format="json")
        plantilla = Plantilla.objects.get()
        resp = self.client.patch(
            f"{URL_PLANTILLAS}{plantilla.id}/", {"nombre": "Nuevo nombre"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(plantilla.elementos.count(), 3)

    def test_variable_inexistente_da_400(self):
        payload = self._payload()
        payload["elementos"][0]["variable"] = "inexistente"
        resp = self.client.post(URL_PLANTILLAS, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_listar_no_escala_las_consultas_con_los_elementos(self):
        # Verifica que el Prefetch evita el N+1. No se fija un número concreto
        # de consultas —eso se rompería con cualquier cambio inocuo— sino que
        # agregar plantillas y elementos no agregue consultas al listado.
        self.client.post(URL_PLANTILLAS, self._payload(), format="json")
        with CaptureQueriesContext(connection) as con_una:
            self.client.get(URL_PLANTILLAS)

        for i in range(3):
            payload = self._payload()
            payload["nombre"] = f"Otra {i}"
            self.client.post(URL_PLANTILLAS, payload, format="json")

        with CaptureQueriesContext(connection) as con_cuatro:
            self.client.get(URL_PLANTILLAS)

        self.assertEqual(len(con_cuatro), len(con_una))


class CoherenciaElementosTests(BaseLabelsTests):
    """El tipo del elemento manda qué campos puede traer."""

    def _post_elemento(self, elemento):
        payload = self._payload()
        payload["elementos"] = [elemento]
        return self.client.post(URL_PLANTILLAS, payload, format="json")

    def _base(self, **extra):
        return {
            "x_mm": "0.00",
            "y_mm": "0.00",
            "ancho_mm": "10.00",
            "alto_mm": "10.00",
            **extra,
        }

    def test_variable_sin_variable_da_400(self):
        resp = self._post_elemento(self._base(tipo=TipoElemento.VARIABLE))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_variable_con_contenido_da_400(self):
        resp = self._post_elemento(
            self._base(
                tipo=TipoElemento.VARIABLE, variable="qr", contenido="no corresponde"
            )
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_texto_estatico_sin_contenido_da_400(self):
        resp = self._post_elemento(self._base(tipo=TipoElemento.TEXTO_ESTATICO))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_texto_estatico_con_variable_da_400(self):
        resp = self._post_elemento(
            self._base(
                tipo=TipoElemento.TEXTO_ESTATICO, contenido="Hola", variable="qr"
            )
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_linea_con_variable_da_400(self):
        resp = self._post_elemento(self._base(tipo=TipoElemento.LINEA, variable="qr"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_linea_sin_variable_ni_contenido_es_valida(self):
        resp = self._post_elemento(self._base(tipo=TipoElemento.LINEA))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_la_base_tambien_rechaza_filas_incoherentes(self):
        # La restricción de base cubre las vías que no pasan por el serializer
        # (shell, scripts, importadores futuros).
        plantilla = Plantilla.objects.create(
            nombre="p", ancho_mm=10, alto_mm=10, dpi=300
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ElementoPlantilla.objects.create(
                plantilla=plantilla,
                tipo=TipoElemento.VARIABLE,
                variable=None,
                x_mm=0,
                y_mm=0,
                ancho_mm=5,
                alto_mm=5,
            )


class EstiloTests(BaseLabelsTests):
    def _post_con_estilo(self, estilo):
        payload = self._payload()
        payload["elementos"] = [
            {
                "variable": "qr",
                "x_mm": "0.00",
                "y_mm": "0.00",
                "ancho_mm": "10.00",
                "alto_mm": "10.00",
                "estilo": estilo,
            }
        ]
        return self.client.post(URL_PLANTILLAS, payload, format="json")

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
        resp = self.client.get(reverse("fuentes"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        codigos = [f["codigo"] for f in resp.data]
        self.assertIn("helvetica", codigos)
        self.assertIn("times", codigos)
        self.assertIn("courier", codigos)
        # Cada una trae lo que la interfaz necesita para armar el selector y
        # para avisar si la vista previa en PNG no va a estar disponible.
        for familia in resp.data:
            self.assertIn("etiqueta", familia)
            self.assertIn("png_disponible", familia)

    def test_requiere_autenticacion(self):
        # force_authenticate lo dejó logueado en setUp; se deshace pasando None.
        self.client.force_authenticate(None)
        resp = self.client.get(reverse("fuentes"))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
