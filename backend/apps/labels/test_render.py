"""Tests del motor de renderizado.

La mayoría afirma sobre la **lista de primitivas** y no sobre los archivos
generados. Comparar PDFs o imágenes byte a byte da tests frágiles —cambia la
versión de una librería y fallan sin que nada esté roto— y cuando fallan no
dicen qué salió mal. Sobre primitivas se puede afirmar "el destinatario quedó
en x=10mm, en negrita, truncado", que es exactamente lo que se quiere saber.

Los backends sí tienen tests de humo: que el archivo salga con la cabecera
correcta y las dimensiones esperadas. Eso alcanza para detectar que reportlab
o Pillow dejaron de funcionar, que es lo único que un test automático puede
decir sin ojos humanos.
"""

import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documents.models import Documento

from .models import ElementoPlantilla, Plantilla, VariableRotulo, mm_a_px
from .render import renderizar_pdf, renderizar_png, resolver
from .render.pdf import _y
from .render.primitivas import Imagen, Lienzo, Linea, Rectangulo, Texto
from .render.resolucion import ancho_texto_mm

User = get_user_model()

PNG_MINIMO = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class BaseRenderTests(APITestCase):
    """Plantilla de 100x150 con un elemento de cada tipo."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="ana", email="ana@test.com", password="x"
        )
        self.plantilla = Plantilla.objects.create(
            nombre="Prueba",
            ancho_mm=Decimal("100"),
            alto_mm=Decimal("150"),
            dpi=300,
            creada_por=self.usuario,
        )
        self.destinatario = VariableRotulo.objects.get(codigo="destinatario")
        self.qr = VariableRotulo.objects.get(codigo="qr")
        self.logo = VariableRotulo.objects.get(codigo="logo_empresa")

    def elemento(self, tipo, **kwargs):
        campos = dict(
            plantilla=self.plantilla,
            tipo=tipo,
            x_mm=Decimal("10"),
            y_mm=Decimal("20"),
            ancho_mm=Decimal("80"),
            alto_mm=Decimal("10"),
        )
        campos.update(kwargs)
        return ElementoPlantilla.objects.create(**campos)


class ResolucionTests(BaseRenderTests):
    def test_cada_tipo_produce_su_primitiva(self):
        self.elemento("linea", alto_mm=Decimal("0.3"), orden=1)
        self.elemento("recuadro", orden=2)
        self.elemento("texto_estatico", contenido="DESTINATARIO:", orden=3)

        primitivas = resolver(self.plantilla, {}).primitivas
        self.assertIsInstance(primitivas[0], Linea)
        self.assertIsInstance(primitivas[1], Rectangulo)
        self.assertIsInstance(primitivas[2], Texto)

    def test_una_variable_toma_su_valor_de_los_datos(self):
        self.elemento("variable", variable=self.destinatario)
        lienzo = resolver(self.plantilla, {"destinatario": "Juan Pérez"})
        self.assertEqual(lienzo.primitivas[0].texto, "Juan Pérez")

    def test_un_dato_faltante_no_dibuja_pero_se_reporta(self):
        """No es un error: una plantilla puede tener campos opcionales."""
        self.elemento("variable", variable=self.destinatario)
        lienzo = resolver(self.plantilla, {})
        self.assertEqual(lienzo.primitivas, [])
        self.assertEqual(lienzo.faltantes, ["destinatario"])

    def test_un_qr_se_convierte_en_imagen(self):
        self.elemento("variable", variable=self.qr, ancho_mm=Decimal("25"),
                      alto_mm=Decimal("25"))
        lienzo = resolver(self.plantilla, {"qr": "AR-2026-0001"})

        imagen = lienzo.primitivas[0]
        self.assertIsInstance(imagen, Imagen)
        self.assertTrue(imagen.png.startswith(b"\x89PNG"))

    def test_el_estilo_se_traslada_a_la_primitiva(self):
        self.elemento(
            "variable",
            variable=self.destinatario,
            estilo={"tamano_pt": 16, "negrita": True, "color": "#cc0000",
                    "alineacion": "centro"},
        )
        texto = resolver(self.plantilla, {"destinatario": "Ana"}).primitivas[0]
        self.assertEqual(texto.tamano_pt, 16)
        self.assertTrue(texto.negrita)
        self.assertEqual(texto.color, "#cc0000")
        self.assertEqual(texto.alineacion, "centro")

    def test_el_estilo_vacio_usa_los_valores_por_defecto(self):
        self.elemento("texto_estatico", contenido="Hola", estilo={})
        texto = resolver(self.plantilla, {}).primitivas[0]
        self.assertEqual(texto.tamano_pt, 10)
        self.assertFalse(texto.negrita)
        self.assertEqual(texto.color, "#000000")

    def test_las_primitivas_salen_en_orden_de_dibujado(self):
        """El fondo primero: si no, el recuadro taparía el texto."""
        self.elemento("texto_estatico", contenido="Encima", orden=20)
        self.elemento("recuadro", orden=5)

        primitivas = resolver(self.plantilla, {}).primitivas
        self.assertIsInstance(primitivas[0], Rectangulo)
        self.assertIsInstance(primitivas[1], Texto)

    def test_el_color_de_fondo_sale_de_metadatos(self):
        self.plantilla.metadatos = {"color_fondo": "#cfe8f7"}
        self.plantilla.save()
        self.assertEqual(resolver(self.plantilla, {}).color_fondo, "#cfe8f7")

    def test_las_medidas_pasan_a_float(self):
        """Los backends hacen aritmética: un Decimal mezclado explota."""
        self.elemento("texto_estatico", contenido="Hola")
        texto = resolver(self.plantilla, {}).primitivas[0]
        for valor in (texto.x_mm, texto.y_mm, texto.ancho_mm, texto.alto_mm):
            self.assertIsInstance(valor, float)


class VistaPreviaTests(BaseRenderTests):
    """Sin datos, cada variable se dibuja como su etiqueta."""

    def test_una_variable_de_texto_se_dibuja_entre_comillas(self):
        self.elemento("variable", variable=self.destinatario)
        texto = resolver(self.plantilla, None).primitivas[0]
        self.assertEqual(texto.texto, "«Destinatario»")

    def test_un_qr_se_dibuja_como_marco_con_etiqueta(self):
        self.elemento("variable", variable=self.qr, ancho_mm=Decimal("25"),
                      alto_mm=Decimal("25"))
        primitivas = resolver(self.plantilla, None).primitivas
        self.assertIsInstance(primitivas[0], Rectangulo)
        self.assertEqual(primitivas[1].texto, "QR")

    def test_la_vista_previa_no_reporta_faltantes(self):
        """Que no haya datos es lo esperado acá, no un problema."""
        self.elemento("variable", variable=self.destinatario)
        self.assertEqual(resolver(self.plantilla, None).faltantes, [])


class TruncadoTests(BaseRenderTests):
    def test_un_texto_que_entra_no_se_toca(self):
        self.elemento("variable", variable=self.destinatario,
                      ancho_mm=Decimal("80"))
        texto = resolver(self.plantilla, {"destinatario": "Ana"}).primitivas[0]
        self.assertEqual(texto.texto, "Ana")
        self.assertFalse(texto.truncado)

    def test_un_texto_que_no_entra_se_corta(self):
        self.elemento("variable", variable=self.destinatario,
                      ancho_mm=Decimal("20"))
        lienzo = resolver(
            self.plantilla,
            {"destinatario": "María Fernanda González de Etchegaray"},
        )
        texto = lienzo.primitivas[0]

        self.assertTrue(texto.texto.endswith("…"))
        self.assertTrue(texto.truncado)
        self.assertLess(len(texto.texto), len("María Fernanda González de Etchegaray"))

    def test_el_recorte_se_reporta_por_su_variable(self):
        """Cortar un domicilio en silencio termina en un paquete que no llega."""
        self.elemento("variable", variable=self.destinatario,
                      ancho_mm=Decimal("15"))
        lienzo = resolver(self.plantilla, {"destinatario": "Nombre larguísimo acá"})
        self.assertEqual(lienzo.truncados, ["destinatario"])

    def test_el_texto_recortado_entra_en_la_caja(self):
        ancho = 20.0
        self.elemento("variable", variable=self.destinatario,
                      ancho_mm=Decimal("20"))
        texto = resolver(
            self.plantilla, {"destinatario": "María Fernanda González"}
        ).primitivas[0]
        self.assertLessEqual(
            ancho_texto_mm(texto.texto, texto.tamano_pt, texto.negrita), ancho
        )

    def test_una_caja_ridiculamente_angosta_deja_solo_los_puntos(self):
        self.elemento("variable", variable=self.destinatario,
                      ancho_mm=Decimal("0.5"))
        texto = resolver(self.plantilla, {"destinatario": "Ana"}).primitivas[0]
        self.assertEqual(texto.texto, "…")

    def test_se_mide_con_las_metricas_de_la_fuente(self):
        """Contar caracteres haría que las mayúsculas se desborden."""
        self.assertGreater(
            ancho_texto_mm("MMMM", 10), ancho_texto_mm("iiii", 10) * 2
        )

    def test_la_negrita_ocupa_mas(self):
        self.assertGreater(
            ancho_texto_mm("Ancho", 10, negrita=True),
            ancho_texto_mm("Ancho", 10, negrita=False),
        )


class CoordenadasPdfTests(BaseRenderTests):
    """La inversión del eje Y, que es donde más fácil se cuela un bug.

    reportlab tiene el origen abajo a la izquierda; el modelo lo tiene arriba.
    Sin convertir, el rótulo sale en espejo vertical — y no falla nada, así que
    se descubre recién mirando el PDF.
    """

    def setUp(self):
        super().setUp()
        self.lienzo = Lienzo(ancho_mm=100, alto_mm=150, dpi=300)

    def test_el_borde_superior_del_modelo_es_el_tope_del_pdf(self):
        from reportlab.lib.units import mm

        self.assertAlmostEqual(_y(self.lienzo, 0), 150 * mm)

    def test_el_borde_inferior_del_modelo_es_el_cero_del_pdf(self):
        self.assertAlmostEqual(_y(self.lienzo, 150), 0)

    def test_una_caja_se_referencia_por_su_borde_inferior(self):
        from reportlab.lib.units import mm

        # Caja que empieza en y=20 y mide 10 de alto: su borde inferior está a
        # 30 del tope, o sea a 120 del piso.
        self.assertAlmostEqual(_y(self.lienzo, 20, 10), 120 * mm)

    def test_lo_que_esta_mas_arriba_tiene_mayor_y_en_el_pdf(self):
        self.assertGreater(_y(self.lienzo, 10), _y(self.lienzo, 100))


class ImagenesTests(BaseRenderTests):
    def setUp(self):
        super().setUp()
        self.elemento("variable", variable=self.logo, ancho_mm=Decimal("40"),
                      alto_mm=Decimal("18"))
        self.documento = Documento.objects.create(
            archivo=SimpleUploadedFile("logo.png", PNG_MINIMO, "image/png"),
            nombre_original="logo.png",
            tipo_mime="image/png",
            tamano_bytes=len(PNG_MINIMO),
            subido_por=self.usuario,
        )

    def test_un_documento_propio_se_incrusta(self):
        lienzo = resolver(
            self.plantilla, {"logo_empresa": self.documento.pk}, usuario=self.usuario
        )
        self.assertIsInstance(lienzo.primitivas[0], Imagen)

    def test_no_se_puede_incrustar_el_documento_de_otro(self):
        """Un id ajeno no debería meter el archivo de otra cuenta en mi rótulo."""
        otro = User.objects.create_user(username="beto", email="b@t.com", password="x")
        lienzo = resolver(
            self.plantilla, {"logo_empresa": self.documento.pk}, usuario=otro
        )
        self.assertEqual(lienzo.primitivas, [])
        self.assertEqual(lienzo.faltantes, ["logo_empresa"])

    def test_un_id_inexistente_se_reporta_como_faltante(self):
        lienzo = resolver(self.plantilla, {"logo_empresa": 99999},
                          usuario=self.usuario)
        self.assertEqual(lienzo.faltantes, ["logo_empresa"])

    def test_un_pdf_no_sirve_como_logo(self):
        pdf = Documento.objects.create(
            archivo=SimpleUploadedFile("x.pdf", b"%PDF-1.7", "application/pdf"),
            nombre_original="x.pdf",
            tipo_mime="application/pdf",
            tamano_bytes=8,
            subido_por=self.usuario,
        )
        lienzo = resolver(self.plantilla, {"logo_empresa": pdf.pk},
                          usuario=self.usuario)
        self.assertEqual(lienzo.faltantes, ["logo_empresa"])


class BackendsTests(BaseRenderTests):
    """Humo: que los archivos salgan con la forma correcta."""

    def setUp(self):
        super().setUp()
        self.elemento("texto_estatico", contenido="DESTINATARIO:", orden=10)
        self.elemento("variable", variable=self.destinatario, y_mm=Decimal("30"),
                      orden=20)
        self.elemento("linea", y_mm=Decimal("28"), alto_mm=Decimal("0.3"), orden=5)
        self.datos = {"destinatario": "Ana"}

    def test_el_pdf_sale_con_su_cabecera(self):
        contenido, _ = renderizar_pdf(self.plantilla, self.datos)
        self.assertTrue(contenido.startswith(b"%PDF-"))
        self.assertGreater(len(contenido), 500)

    def test_el_png_sale_con_su_cabecera(self):
        contenido, _ = renderizar_png(self.plantilla, self.datos)
        self.assertTrue(contenido.startswith(b"\x89PNG"))

    def test_el_png_tiene_el_tamano_que_dicta_el_dpi(self):
        contenido, _ = renderizar_png(self.plantilla, self.datos, dpi=150)
        with Image.open(io.BytesIO(contenido)) as imagen:
            self.assertEqual(imagen.size, (mm_a_px(100, 150), mm_a_px(150, 150)))

    def test_el_dpi_de_la_plantilla_es_el_valor_por_defecto(self):
        contenido, _ = renderizar_png(self.plantilla, self.datos)
        with Image.open(io.BytesIO(contenido)) as imagen:
            self.assertEqual(imagen.size, (self.plantilla.ancho_px,
                                           self.plantilla.alto_px))

    def test_un_lote_genera_una_pagina_por_rotulo(self):
        contenido, _ = renderizar_pdf(
            self.plantilla,
            lote=[{"destinatario": "Ana"}, {"destinatario": "Beto"},
                  {"destinatario": "Carla"}],
        )
        self.assertIn(b"/Count 3", contenido)

    def test_el_informe_viaja_con_el_archivo(self):
        contenido, informe = renderizar_pdf(self.plantilla, {})
        self.assertEqual(informe["faltantes"], ["destinatario"])

    def test_la_vista_previa_no_necesita_datos(self):
        contenido, _ = renderizar_png(self.plantilla, None)
        self.assertTrue(contenido.startswith(b"\x89PNG"))


class RenderizarAPITests(BaseRenderTests):
    def setUp(self):
        super().setUp()
        self.elemento("variable", variable=self.destinatario)
        self.url = reverse("plantilla-renderizar", args=[self.plantilla.pk])

    def test_requiere_autenticacion(self):
        respuesta = self.client.post(self.url, {}, format="json")
        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_devuelve_un_pdf(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url, {"datos": {"destinatario": "Ana"}}, format="json"
        )
        self.assertEqual(respuesta.status_code, status.HTTP_200_OK)
        self.assertEqual(respuesta["Content-Type"], "application/pdf")
        self.assertTrue(respuesta.content.startswith(b"%PDF-"))
        self.assertIn("attachment", respuesta["Content-Disposition"])

    def test_devuelve_un_png(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"formato": "png", "datos": {"destinatario": "Ana"}, "dpi": 150},
            format="json",
        )
        self.assertEqual(respuesta["Content-Type"], "image/png")
        self.assertTrue(respuesta.content.startswith(b"\x89PNG"))

    def test_un_cuerpo_vacio_da_la_vista_previa(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {}, format="json")
        self.assertEqual(respuesta.status_code, status.HTTP_200_OK)

    def test_avisa_por_cabecera_lo_que_falto(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"datos": {}}, format="json")
        self.assertEqual(respuesta["X-Rotulo-Faltantes"], "destinatario")

    def test_avisa_por_cabecera_lo_que_se_corto(self):
        """El recorte no puede ser silencioso."""
        ElementoPlantilla.objects.all().update(ancho_mm=Decimal("15"))
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"datos": {"destinatario": "María Fernanda González de Etchegaray"}},
            format="json",
        )
        self.assertEqual(respuesta["X-Rotulo-Truncados"], "destinatario")

    def test_sin_problemas_no_hay_cabeceras(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url, {"datos": {"destinatario": "Ana"}}, format="json"
        )
        self.assertNotIn("X-Rotulo-Faltantes", respuesta)
        self.assertNotIn("X-Rotulo-Truncados", respuesta)

    def test_un_lote_devuelve_un_pdf_de_varias_paginas(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"lote": [{"destinatario": "Ana"}, {"destinatario": "Beto"}]},
            format="json",
        )
        self.assertIn(b"/Count 2", respuesta.content)

    def test_no_se_puede_pedir_un_lote_en_png(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"formato": "png", "lote": [{"destinatario": "Ana"}]},
            format="json",
        )
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_se_puede_mandar_datos_y_lote_a_la_vez(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"datos": {"destinatario": "Ana"}, "lote": [{"destinatario": "B"}]},
            format="json",
        )
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)

    def test_un_formato_invalido_da_400(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(self.url, {"formato": "svg"}, format="json")
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
