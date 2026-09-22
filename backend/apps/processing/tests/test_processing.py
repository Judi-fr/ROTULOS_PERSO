"""Tests de la app processing (lectura de rótulos con el modelo).

Ninguno llama a la API de Claude: eso costaría dinero, sería lento y volvería
los tests dependientes de la red y de lo que el modelo decida hoy. Lo que se
prueba es todo lo que rodea a esa llamada, que es donde de verdad se puede
romper algo:

- que el esquema se arme a partir del catálogo de la base;
- que la conversión de porcentajes a milímetros dé lo que corresponde;
- que la propuesta resultante sea un cuerpo que la API de element-layouts acepta.

Ese último test es el que importa: comprueba de punta a punta que lo que
produce el importador es exactamente lo que consume la API de plantillas, que
es el acople más fácil de romper entre las dos apps.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documents.models import UploadedLabelFile
from apps.labels.models import ElementLayout, LayoutVariable

from ..agent import build_proposal
from ..schema import build_schema, build_system_prompt
from ..models import LabelImportStatus, LabelImport

User = get_user_model()


def respuesta_del_modelo(**extra):
    """Una respuesta como la que devolvería el modelo, ya validada por el esquema."""
    base = {
        "width_mm": 100,
        "height_mm": 150,
        "orientation": "vertical",
        "confidence": 0.9,
        "notes": "",
        "elements": [
            {
                "element_type": "texto_estatico",
                "variable": None,
                "content": "DESTINATARIO:",
                "detected_value": None,
                "x_pct": 10, "y_pct": 20, "width_pct": 40, "height_pct": 4,
                "tamano_pt": 8, "alineacion": "izquierda",
                "negrita": False, "color": "#000000",
            },
            {
                "element_type": "variable",
                "variable": "destinatario",
                "content": None,
                "detected_value": "María Gómez",
                "x_pct": 10, "y_pct": 26, "width_pct": 80, "height_pct": 6,
                "tamano_pt": 16, "alineacion": "izquierda",
                "negrita": True, "color": "#000000",
            },
            {
                "element_type": "linea",
                "variable": None, "content": None, "detected_value": None,
                "x_pct": 10, "y_pct": 18, "width_pct": 80, "height_pct": 0.2,
                "tamano_pt": None, "alineacion": None,
                "negrita": False, "color": "#000000",
            },
        ],
    }
    base.update(extra)
    return base


def codigos_ofrecidos(esquema):
    """Los códigos de variable que el esquema le ofrece al modelo.

    El campo es un ``anyOf`` (enum de strings, o null) y no un enum plano: ver
    ``schema._enum_or_null`` para por qué. Se busca la rama con enum en lugar
    de indexarla por posición, así el test no se rompe si se reordena.
    """
    campo = esquema["properties"]["elements"]["items"]["properties"]["variable"]
    return next(rama["enum"] for rama in campo["anyOf"] if "enum" in rama)


def propiedades(esquema):
    """Todas las propiedades del esquema, las del objeto raíz y las de un elemento."""
    del_elemento = esquema["properties"]["elements"]["items"]["properties"]
    return list(esquema["properties"].items()) + list(del_elemento.items())


class EsquemaDinamicoTests(APITestCase):
    """El esquema y el prompt salen del catálogo, no de constantes."""

    def test_el_enum_de_variables_sale_de_la_base(self):
        variables = LayoutVariable.objects.filter(is_active=True)
        esquema = build_schema(variables)
        enum = codigos_ofrecidos(esquema)

        for codigo in variables.values_list("code", flat=True):
            self.assertIn(codigo, enum)

    def test_un_elemento_que_no_es_variable_puede_dejarla_vacia(self):
        """Una línea o un texto estático mandan null en 'variable'."""
        esquema = build_schema(LayoutVariable.objects.filter(is_active=True))
        campo = esquema["properties"]["elements"]["items"]["properties"]["variable"]
        self.assertIn({"type": "null"}, campo["anyOf"])

    def test_ninguna_propiedad_mezcla_type_lista_con_enum(self):
        """El validador de structured outputs rechaza esa combinación.

        Es válida como JSON Schema, así que no salta en ninguna revisión local:
        el error aparece recién en la primera llamada real, como un 400 con
        «Enum value 'qr' does not match declared type '['string', 'null']'».
        Un enum que además admite null va como ``anyOf`` (ver
        ``schema._enum_or_null``).
        """
        esquema = build_schema(LayoutVariable.objects.filter(is_active=True))
        for nombre, propiedad in propiedades(esquema):
            with self.subTest(propiedad=nombre):
                if isinstance(propiedad.get("type"), list):
                    self.assertNotIn(
                        "enum",
                        propiedad,
                        f"«{nombre}» combina un type de lista con enum: "
                        "partilo en un anyOf con la rama null aparte.",
                    )

    def test_una_variable_nueva_aparece_sin_tocar_codigo(self):
        """Es la razón de ser del catálogo dinámico."""
        LayoutVariable.objects.create(
            code="numero_bulto", label="Número de bulto", data_type="texto"
        )
        esquema = build_schema(LayoutVariable.objects.filter(is_active=True))
        self.assertIn("numero_bulto", codigos_ofrecidos(esquema))

    def test_una_variable_inactiva_no_se_ofrece(self):
        LayoutVariable.objects.filter(code="qr").update(is_active=False)
        esquema = build_schema(LayoutVariable.objects.filter(is_active=True))
        self.assertNotIn("qr", codigos_ofrecidos(esquema))

    def test_el_prompt_incluye_las_descripciones(self):
        """Las descripciones son lo que distingue remitente de destinatario."""
        prompt = build_system_prompt(LayoutVariable.objects.filter(is_active=True))
        self.assertIn("ENVÍA", prompt)   # de la descripción de remitente
        self.assertIn("RECIBE", prompt)  # de la de destinatario


class FirmaDelSdkTests(APITestCase):
    """El SDK acepta los argumentos con los que se lo llama.

    Los tests de la vista mockean ``_request_reading``, así que un argumento
    mal escrito o que el SDK no expone no se detectaría hasta la primera
    llamada real —o sea, en producción—. Acá se compara contra la firma de
    verdad.

    Fue un problema concreto: ``fallbacks`` todavía no está en la firma del SDK
    0.76 y pasarlo como argumento nombrado da TypeError. Por eso va dentro de
    ``extra_body``.
    """

    def test_los_argumentos_nombrados_existen_en_el_sdk(self):
        import inspect

        import anthropic

        cliente = anthropic.Anthropic(api_key="no-se-usa")
        firma = inspect.signature(cliente.beta.messages.create).parameters

        for argumento in (
            "model", "max_tokens", "thinking", "system",
            "output_config", "betas", "messages", "extra_body",
        ):
            with self.subTest(argumento=argumento):
                self.assertIn(argumento, firma)

    def test_fallbacks_sigue_necesitando_extra_body(self):
        """Cuando este test falle, el SDK ya lo expone: mover al argumento nombrado."""
        import inspect

        import anthropic

        cliente = anthropic.Anthropic(api_key="no-se-usa")
        firma = inspect.signature(cliente.beta.messages.create).parameters
        self.assertNotIn(
            "fallbacks",
            firma,
            "El SDK ya expone `fallbacks`: sacalo de extra_body en agent.py.",
        )


class ConversionDeLaPropuestaTests(APITestCase):
    """De porcentajes a milímetros, y de la respuesta cruda al cuerpo de la API."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="ana", email="ana@test.com", password="x"
        )
        self.documento = UploadedLabelFile.objects.create(
            file="rotulos/r.png",
            original_filename="r.png",
            mime_type="image/png",
            size_bytes=100,
            uploaded_by=self.usuario,
        )

    def test_los_porcentajes_se_convierten_a_milimetros(self):
        propuesta = build_proposal(respuesta_del_modelo(), self.documento)
        # 10% de 100 mm de ancho = 10 mm; 26% de 150 mm de alto = 39 mm.
        destinatario = next(
            e for e in propuesta["elements"] if e["variable"] == "destinatario"
        )
        self.assertEqual(destinatario["x_mm"], 10.0)
        self.assertEqual(destinatario["y_mm"], 39.0)
        self.assertEqual(destinatario["width_mm"], 80.0)
        self.assertEqual(destinatario["height_mm"], 9.0)

    def test_las_lineas_van_al_fondo(self):
        propuesta = build_proposal(respuesta_del_modelo(), self.documento)
        linea = next(e for e in propuesta["elements"] if e["element_type"] == "linea")
        texto = next(e for e in propuesta["elements"] if e["element_type"] == "texto_estatico")
        self.assertLess(linea["order"], texto["order"])

    def test_una_linea_nunca_queda_con_alto_cero(self):
        """height_mm tiene MinValueValidator(0.01): un 0 haría fallar el POST."""
        datos = respuesta_del_modelo()
        datos["elements"] = [dict(datos["elements"][2], height_pct=0)]
        propuesta = build_proposal(datos, self.documento)
        self.assertGreaterEqual(propuesta["elements"][0]["height_mm"], 0.01)

    def test_se_descarta_un_elemento_incoherente(self):
        """Un elemento malo no debe invalidar la propuesta entera."""
        datos = respuesta_del_modelo()
        datos["elements"].append({
            "element_type": "variable", "variable": None, "content": None,
            "detected_value": None,
            "x_pct": 0, "y_pct": 0, "width_pct": 10, "height_pct": 10,
            "tamano_pt": 10, "alineacion": None, "negrita": False, "color": "#000000",
        })
        propuesta = build_proposal(datos, self.documento)

        self.assertEqual(len(propuesta["elements"]), 3)  # los tres buenos
        self.assertEqual(len(propuesta["_revision"]["discarded"]), 1)
        self.assertIn("sin variable", propuesta["_revision"]["discarded"][0]["reason"])

    def test_un_texto_estatico_vacio_se_descarta(self):
        datos = respuesta_del_modelo()
        datos["elements"] = [dict(datos["elements"][0], content="   ")]
        propuesta = build_proposal(datos, self.documento)
        self.assertEqual(propuesta["elements"], [])
        self.assertEqual(len(propuesta["_revision"]["discarded"]), 1)

    def test_dimensiones_invalidas_caen_al_valor_por_defecto(self):
        propuesta = build_proposal(
            respuesta_del_modelo(width_mm=0, height_mm=None), self.documento
        )
        self.assertEqual(propuesta["width_mm"], 100.0)
        self.assertEqual(propuesta["height_mm"], 150.0)

    def test_el_estilo_solo_lleva_las_claves_que_aplican(self):
        propuesta = build_proposal(respuesta_del_modelo(), self.documento)
        linea = next(e for e in propuesta["elements"] if e["element_type"] == "linea")
        destinatario = next(
            e for e in propuesta["elements"] if e["variable"] == "destinatario"
        )
        self.assertIn("grosor_mm", linea["style"])
        self.assertNotIn("tamano_pt", linea["style"])
        self.assertEqual(destinatario["style"]["tamano_pt"], 16.0)
        self.assertTrue(destinatario["style"]["negrita"])
        # El color negro es el default: no se guarda.
        self.assertNotIn("color", destinatario["style"])

    def test_se_conservan_los_valores_leidos_para_revisar(self):
        propuesta = build_proposal(respuesta_del_modelo(), self.documento)
        detectados = propuesta["_revision"]["detected_values"]
        self.assertEqual(
            detectados, [{"variable": "destinatario", "value": "María Gómez"}]
        )

    def test_la_propuesta_es_aceptada_por_la_api_de_plantillas(self):
        """El test que ata las dos apps: lo que produce una es lo que consume la otra."""
        propuesta = build_proposal(respuesta_del_modelo(), self.documento)
        cuerpo = {k: v for k, v in propuesta.items() if not k.startswith("_")}

        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            reverse("element-layout-list"), cuerpo, format="json"
        )

        self.assertEqual(
            respuesta.status_code, status.HTTP_201_CREATED, respuesta.data
        )
        layout = ElementLayout.objects.get(pk=respuesta.data["id"])
        self.assertEqual(layout.elements.count(), 3)


class ImportacionAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("label-import-list")
        self.usuario = User.objects.create_user(
            username="ana", email="ana@test.com", password="x"
        )
        self.otro = User.objects.create_user(
            username="beto", email="beto@test.com", password="x"
        )
        self.documento = UploadedLabelFile.objects.create(
            file="rotulos/r.png",
            original_filename="r.png",
            mime_type="image/png",
            size_bytes=100,
            uploaded_by=self.usuario,
        )

    def test_requiere_autenticacion(self):
        respuesta = self.client.post(self.url, {"uploaded_file": self.documento.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_se_puede_importar_el_documento_de_otro(self):
        """Procesar cuesta tokens: nadie debería poder gastarlos sobre fotos ajenas."""
        ajeno = UploadedLabelFile.objects.create(
            file="rotulos/x.png",
            original_filename="x.png",
            mime_type="image/png",
            size_bytes=100,
            uploaded_by=self.otro,
        )
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"uploaded_file": ajeno.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("apps.processing.agent._request_reading")
    def test_una_lectura_exitosa_devuelve_la_propuesta(self, pedir):
        pedir.return_value = (
            respuesta_del_modelo(),
            {"input": 1500, "output": 800},
            "req_abc123",
        )
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"uploaded_file": self.documento.pk})

        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.assertEqual(respuesta.data["status"], LabelImportStatus.COMPLETED)
        self.assertEqual(respuesta.data["confidence"], 0.9)
        self.assertEqual(respuesta.data["input_tokens"], 1500)
        self.assertEqual(respuesta.data["request_id"], "req_abc123")
        self.assertEqual(len(respuesta.data["proposal"]["elements"]), 3)

    @patch("apps.processing.agent._request_reading")
    def test_la_importacion_no_guarda_la_plantilla(self, pedir):
        """La propuesta se revisa antes de guardarse: nada se crea solo."""
        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        self.client.post(self.url, {"uploaded_file": self.documento.pk})
        self.assertEqual(ElementLayout.objects.count(), 0)

    @patch("apps.processing.agent._request_reading")
    def test_un_fallo_del_modelo_queda_registrado(self, pedir):
        from ..agent import AgentError

        pedir.side_effect = AgentError("La ANTHROPIC_API_KEY no es válida.")
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"uploaded_file": self.documento.pk})

        # 502: el fallo es de un servicio de arriba, no de lo que mandó el cliente.
        self.assertEqual(respuesta.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(respuesta.data["status"], LabelImportStatus.ERROR)
        self.assertIn("ANTHROPIC_API_KEY", respuesta.data["error"])
        # Queda la fila, para saber qué se intentó y por qué falló.
        self.assertEqual(LabelImport.objects.count(), 1)

    @patch("apps.processing.agent._request_reading")
    def test_reintentar_crea_una_importacion_nueva(self, pedir):
        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        primera = self.client.post(self.url, {"uploaded_file": self.documento.pk})

        respuesta = self.client.post(
            reverse("label-import-retry", args=[primera.data["id"]])
        )
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(respuesta.data["id"], primera.data["id"])
        self.assertEqual(LabelImport.objects.count(), 2)

    @patch("apps.processing.agent._request_reading")
    def test_cada_usuario_solo_ve_sus_importaciones(self, pedir):
        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        self.client.post(self.url, {"uploaded_file": self.documento.pk})

        self.client.force_authenticate(self.otro)
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.data["count"], 0)

    @patch("apps.processing.agent._request_reading")
    def test_lanzar_una_importacion_deja_auditlog(self, pedir):
        from apps.audit.models import AuditLog

        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"uploaded_file": self.documento.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)

        log = AuditLog.objects.filter(action="importacion_rotulo.create").latest(
            "created_at"
        )
        self.assertEqual(log.actor_id, self.usuario.id)
        self.assertEqual(log.target_id, str(respuesta.data["id"]))

    @patch("apps.processing.agent._request_reading")
    def test_un_fallo_del_modelo_tambien_deja_auditlog(self, pedir):
        """Se paga el token aunque la lectura falle: igual queda registrada."""
        from apps.audit.models import AuditLog
        from ..agent import AgentError

        pedir.side_effect = AgentError("La ANTHROPIC_API_KEY no es válida.")
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"uploaded_file": self.documento.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_502_BAD_GATEWAY)

        log = AuditLog.objects.filter(action="importacion_rotulo.create").latest(
            "created_at"
        )
        self.assertEqual(log.actor_id, self.usuario.id)
        self.assertEqual(log.target_id, str(respuesta.data["id"]))

    @patch("apps.processing.agent._request_reading")
    def test_reintentar_deja_un_segundo_auditlog(self, pedir):
        from apps.audit.models import AuditLog

        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        primera = self.client.post(self.url, {"uploaded_file": self.documento.pk})

        respuesta = self.client.post(
            reverse("label-import-retry", args=[primera.data["id"]])
        )
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)

        logs = AuditLog.objects.filter(action="importacion_rotulo.create").order_by(
            "created_at"
        )
        self.assertEqual(logs.count(), 2)
        segundo = logs.last()
        self.assertEqual(segundo.target_id, str(respuesta.data["id"]))
        self.assertEqual(segundo.changes["reintento_de"]["to"], primera.data["id"])
