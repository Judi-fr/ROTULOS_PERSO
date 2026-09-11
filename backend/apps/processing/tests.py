"""Tests de la app processing (lectura de rótulos con el modelo).

Ninguno llama a la API de Claude: eso costaría dinero, sería lento y volvería
los tests dependientes de la red y de lo que el modelo decida hoy. Lo que se
prueba es todo lo que rodea a esa llamada, que es donde de verdad se puede
romper algo:

- que el esquema se arme a partir del catálogo de la base;
- que la conversión de porcentajes a milímetros dé lo que corresponde;
- que la propuesta resultante sea un cuerpo que la API de plantillas acepta.

Ese último test es el que importa: comprueba de punta a punta que lo que
produce el importador es exactamente lo que consume la API de plantillas, que
es el acople más fácil de romper entre las dos apps.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documents.models import Documento
from apps.labels.models import Plantilla, VariableRotulo

from .agente import construir_propuesta
from .esquema import construir_esquema, construir_system_prompt
from .models import EstadoImportacion, ImportacionRotulo

User = get_user_model()


def respuesta_del_modelo(**extra):
    """Una respuesta como la que devolvería el modelo, ya validada por el esquema."""
    base = {
        "ancho_mm": 100,
        "alto_mm": 150,
        "orientacion": "vertical",
        "confianza": 0.9,
        "notas": "",
        "elementos": [
            {
                "tipo": "texto_estatico",
                "variable": None,
                "contenido": "DESTINATARIO:",
                "valor_detectado": None,
                "x_pct": 10, "y_pct": 20, "ancho_pct": 40, "alto_pct": 4,
                "tamano_pt": 8, "alineacion": "izquierda",
                "negrita": False, "color": "#000000",
            },
            {
                "tipo": "variable",
                "variable": "destinatario",
                "contenido": None,
                "valor_detectado": "María Gómez",
                "x_pct": 10, "y_pct": 26, "ancho_pct": 80, "alto_pct": 6,
                "tamano_pt": 16, "alineacion": "izquierda",
                "negrita": True, "color": "#000000",
            },
            {
                "tipo": "linea",
                "variable": None, "contenido": None, "valor_detectado": None,
                "x_pct": 10, "y_pct": 18, "ancho_pct": 80, "alto_pct": 0.2,
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
    ``esquema._enum_o_nulo`` para por qué. Se busca la rama con enum en lugar
    de indexarla por posición, así el test no se rompe si se reordena.
    """
    campo = esquema["properties"]["elementos"]["items"]["properties"]["variable"]
    return next(rama["enum"] for rama in campo["anyOf"] if "enum" in rama)


def propiedades(esquema):
    """Todas las propiedades del esquema, las del objeto raíz y las de un elemento."""
    del_elemento = esquema["properties"]["elementos"]["items"]["properties"]
    return list(esquema["properties"].items()) + list(del_elemento.items())


class EsquemaDinamicoTests(APITestCase):
    """El esquema y el prompt salen del catálogo, no de constantes."""

    def test_el_enum_de_variables_sale_de_la_base(self):
        variables = VariableRotulo.objects.filter(activa=True)
        esquema = construir_esquema(variables)
        enum = codigos_ofrecidos(esquema)

        for codigo in variables.values_list("codigo", flat=True):
            self.assertIn(codigo, enum)

    def test_un_elemento_que_no_es_variable_puede_dejarla_vacia(self):
        """Una línea o un texto estático mandan null en 'variable'."""
        esquema = construir_esquema(VariableRotulo.objects.filter(activa=True))
        campo = esquema["properties"]["elementos"]["items"]["properties"]["variable"]
        self.assertIn({"type": "null"}, campo["anyOf"])

    def test_ninguna_propiedad_mezcla_type_lista_con_enum(self):
        """El validador de structured outputs rechaza esa combinación.

        Es válida como JSON Schema, así que no salta en ninguna revisión local:
        el error aparece recién en la primera llamada real, como un 400 con
        «Enum value 'qr' does not match declared type '['string', 'null']'».
        Un enum que además admite null va como ``anyOf`` (ver
        ``esquema._enum_o_nulo``).
        """
        esquema = construir_esquema(VariableRotulo.objects.filter(activa=True))
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
        VariableRotulo.objects.create(
            codigo="numero_bulto", etiqueta="Número de bulto", tipo_dato="texto"
        )
        esquema = construir_esquema(VariableRotulo.objects.filter(activa=True))
        self.assertIn("numero_bulto", codigos_ofrecidos(esquema))

    def test_una_variable_inactiva_no_se_ofrece(self):
        VariableRotulo.objects.filter(codigo="qr").update(activa=False)
        esquema = construir_esquema(VariableRotulo.objects.filter(activa=True))
        self.assertNotIn("qr", codigos_ofrecidos(esquema))

    def test_el_prompt_incluye_las_descripciones(self):
        """Las descripciones son lo que distingue remitente de destinatario."""
        prompt = construir_system_prompt(VariableRotulo.objects.filter(activa=True))
        self.assertIn("ENVÍA", prompt)   # de la descripción de remitente
        self.assertIn("RECIBE", prompt)  # de la de destinatario


class FirmaDelSdkTests(APITestCase):
    """El SDK acepta los argumentos con los que se lo llama.

    Los tests de la vista mockean ``_pedir_lectura``, así que un argumento mal
    escrito o que el SDK no expone no se detectaría hasta la primera llamada
    real —o sea, en producción—. Acá se compara contra la firma de verdad.

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
            "El SDK ya expone `fallbacks`: sacalo de extra_body en agente.py.",
        )


class ConversionDeLaPropuestaTests(APITestCase):
    """De porcentajes a milímetros, y de la respuesta cruda al cuerpo de la API."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="ana", email="ana@test.com", password="x"
        )
        self.documento = Documento.objects.create(
            archivo="rotulos/r.png",
            nombre_original="r.png",
            tipo_mime="image/png",
            tamano_bytes=100,
            subido_por=self.usuario,
        )

    def test_los_porcentajes_se_convierten_a_milimetros(self):
        propuesta = construir_propuesta(respuesta_del_modelo(), self.documento)
        # 10% de 100 mm de ancho = 10 mm; 26% de 150 mm de alto = 39 mm.
        destinatario = next(
            e for e in propuesta["elementos"] if e["variable"] == "destinatario"
        )
        self.assertEqual(destinatario["x_mm"], 10.0)
        self.assertEqual(destinatario["y_mm"], 39.0)
        self.assertEqual(destinatario["ancho_mm"], 80.0)
        self.assertEqual(destinatario["alto_mm"], 9.0)

    def test_las_lineas_van_al_fondo(self):
        propuesta = construir_propuesta(respuesta_del_modelo(), self.documento)
        linea = next(e for e in propuesta["elementos"] if e["tipo"] == "linea")
        texto = next(e for e in propuesta["elementos"] if e["tipo"] == "texto_estatico")
        self.assertLess(linea["orden"], texto["orden"])

    def test_una_linea_nunca_queda_con_alto_cero(self):
        """alto_mm tiene MinValueValidator(0.01): un 0 haría fallar el POST."""
        datos = respuesta_del_modelo()
        datos["elementos"] = [dict(datos["elementos"][2], alto_pct=0)]
        propuesta = construir_propuesta(datos, self.documento)
        self.assertGreaterEqual(propuesta["elementos"][0]["alto_mm"], 0.01)

    def test_se_descarta_un_elemento_incoherente(self):
        """Un elemento malo no debe invalidar la propuesta entera."""
        datos = respuesta_del_modelo()
        datos["elementos"].append({
            "tipo": "variable", "variable": None, "contenido": None,
            "valor_detectado": None,
            "x_pct": 0, "y_pct": 0, "ancho_pct": 10, "alto_pct": 10,
            "tamano_pt": 10, "alineacion": None, "negrita": False, "color": "#000000",
        })
        propuesta = construir_propuesta(datos, self.documento)

        self.assertEqual(len(propuesta["elementos"]), 3)  # los tres buenos
        self.assertEqual(len(propuesta["_revision"]["descartados"]), 1)
        self.assertIn("sin variable", propuesta["_revision"]["descartados"][0]["motivo"])

    def test_un_texto_estatico_vacio_se_descarta(self):
        datos = respuesta_del_modelo()
        datos["elementos"] = [dict(datos["elementos"][0], contenido="   ")]
        propuesta = construir_propuesta(datos, self.documento)
        self.assertEqual(propuesta["elementos"], [])
        self.assertEqual(len(propuesta["_revision"]["descartados"]), 1)

    def test_dimensiones_invalidas_caen_al_valor_por_defecto(self):
        propuesta = construir_propuesta(
            respuesta_del_modelo(ancho_mm=0, alto_mm=None), self.documento
        )
        self.assertEqual(propuesta["ancho_mm"], 100.0)
        self.assertEqual(propuesta["alto_mm"], 150.0)

    def test_el_estilo_solo_lleva_las_claves_que_aplican(self):
        propuesta = construir_propuesta(respuesta_del_modelo(), self.documento)
        linea = next(e for e in propuesta["elementos"] if e["tipo"] == "linea")
        destinatario = next(
            e for e in propuesta["elementos"] if e["variable"] == "destinatario"
        )
        self.assertIn("grosor_mm", linea["estilo"])
        self.assertNotIn("tamano_pt", linea["estilo"])
        self.assertEqual(destinatario["estilo"]["tamano_pt"], 16.0)
        self.assertTrue(destinatario["estilo"]["negrita"])
        # El color negro es el default: no se guarda.
        self.assertNotIn("color", destinatario["estilo"])

    def test_se_conservan_los_valores_leidos_para_revisar(self):
        propuesta = construir_propuesta(respuesta_del_modelo(), self.documento)
        detectados = propuesta["_revision"]["valores_detectados"]
        self.assertEqual(
            detectados, [{"variable": "destinatario", "valor": "María Gómez"}]
        )

    def test_la_propuesta_es_aceptada_por_la_api_de_plantillas(self):
        """El test que ata las dos apps: lo que produce una es lo que consume la otra."""
        propuesta = construir_propuesta(respuesta_del_modelo(), self.documento)
        cuerpo = {k: v for k, v in propuesta.items() if not k.startswith("_")}

        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            reverse("plantilla-list"), cuerpo, format="json"
        )

        self.assertEqual(
            respuesta.status_code, status.HTTP_201_CREATED, respuesta.data
        )
        plantilla = Plantilla.objects.get(pk=respuesta.data["id"])
        self.assertEqual(plantilla.elementos.count(), 3)


class ImportacionAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("importacion-rotulo-list")
        self.usuario = User.objects.create_user(
            username="ana", email="ana@test.com", password="x"
        )
        self.otro = User.objects.create_user(
            username="beto", email="beto@test.com", password="x"
        )
        self.documento = Documento.objects.create(
            archivo="rotulos/r.png",
            nombre_original="r.png",
            tipo_mime="image/png",
            tamano_bytes=100,
            subido_por=self.usuario,
        )

    def test_requiere_autenticacion(self):
        respuesta = self.client.post(self.url, {"documento": self.documento.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_se_puede_importar_el_documento_de_otro(self):
        """Procesar cuesta tokens: nadie debería poder gastarlos sobre fotos ajenas."""
        ajeno = Documento.objects.create(
            archivo="rotulos/x.png",
            nombre_original="x.png",
            tipo_mime="image/png",
            tamano_bytes=100,
            subido_por=self.otro,
        )
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"documento": ajeno.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("apps.processing.agente._pedir_lectura")
    def test_una_lectura_exitosa_devuelve_la_propuesta(self, pedir):
        pedir.return_value = (
            respuesta_del_modelo(),
            {"entrada": 1500, "salida": 800},
            "req_abc123",
        )
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"documento": self.documento.pk})

        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.assertEqual(respuesta.data["estado"], EstadoImportacion.COMPLETADA)
        self.assertEqual(respuesta.data["confianza"], 0.9)
        self.assertEqual(respuesta.data["tokens_entrada"], 1500)
        self.assertEqual(respuesta.data["request_id"], "req_abc123")
        self.assertEqual(len(respuesta.data["propuesta"]["elementos"]), 3)

    @patch("apps.processing.agente._pedir_lectura")
    def test_la_importacion_no_guarda_la_plantilla(self, pedir):
        """La propuesta se revisa antes de guardarse: nada se crea solo."""
        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        self.client.post(self.url, {"documento": self.documento.pk})
        self.assertEqual(Plantilla.objects.count(), 0)

    @patch("apps.processing.agente._pedir_lectura")
    def test_un_fallo_del_modelo_queda_registrado(self, pedir):
        from .agente import ErrorDeAgente

        pedir.side_effect = ErrorDeAgente("La ANTHROPIC_API_KEY no es válida.")
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"documento": self.documento.pk})

        # 502: el fallo es de un servicio de arriba, no de lo que mandó el cliente.
        self.assertEqual(respuesta.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(respuesta.data["estado"], EstadoImportacion.ERROR)
        self.assertIn("ANTHROPIC_API_KEY", respuesta.data["error"])
        # Queda la fila, para saber qué se intentó y por qué falló.
        self.assertEqual(ImportacionRotulo.objects.count(), 1)

    @patch("apps.processing.agente._pedir_lectura")
    def test_reintentar_crea_una_importacion_nueva(self, pedir):
        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        primera = self.client.post(self.url, {"documento": self.documento.pk})

        respuesta = self.client.post(
            reverse("importacion-rotulo-reintentar", args=[primera.data["id"]])
        )
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(respuesta.data["id"], primera.data["id"])
        self.assertEqual(ImportacionRotulo.objects.count(), 2)

    @patch("apps.processing.agente._pedir_lectura")
    def test_cada_usuario_solo_ve_sus_importaciones(self, pedir):
        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        self.client.post(self.url, {"documento": self.documento.pk})

        self.client.force_authenticate(self.otro)
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.data["count"], 0)

    @patch("apps.processing.agente._pedir_lectura")
    def test_lanzar_una_importacion_deja_auditlog(self, pedir):
        from apps.audit.models import AuditLog

        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"documento": self.documento.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)

        log = AuditLog.objects.filter(action="importacion_rotulo.create").latest(
            "created_at"
        )
        self.assertEqual(log.actor_id, self.usuario.id)
        self.assertEqual(log.target_id, str(respuesta.data["id"]))

    @patch("apps.processing.agente._pedir_lectura")
    def test_un_fallo_del_modelo_tambien_deja_auditlog(self, pedir):
        """Se paga el token aunque la lectura falle: igual queda registrada."""
        from apps.audit.models import AuditLog
        from .agente import ErrorDeAgente

        pedir.side_effect = ErrorDeAgente("La ANTHROPIC_API_KEY no es válida.")
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"documento": self.documento.pk})
        self.assertEqual(respuesta.status_code, status.HTTP_502_BAD_GATEWAY)

        log = AuditLog.objects.filter(action="importacion_rotulo.create").latest(
            "created_at"
        )
        self.assertEqual(log.actor_id, self.usuario.id)
        self.assertEqual(log.target_id, str(respuesta.data["id"]))

    @patch("apps.processing.agente._pedir_lectura")
    def test_reintentar_deja_un_segundo_auditlog(self, pedir):
        from apps.audit.models import AuditLog

        pedir.return_value = (respuesta_del_modelo(), {}, "req_1")
        self.client.force_authenticate(self.usuario)
        primera = self.client.post(self.url, {"documento": self.documento.pk})

        respuesta = self.client.post(
            reverse("importacion-rotulo-reintentar", args=[primera.data["id"]])
        )
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)

        logs = AuditLog.objects.filter(action="importacion_rotulo.create").order_by(
            "created_at"
        )
        self.assertEqual(logs.count(), 2)
        segundo = logs.last()
        self.assertEqual(segundo.target_id, str(respuesta.data["id"]))
        self.assertEqual(segundo.changes["reintento_de"]["to"], primera.data["id"])
