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
from unittest.mock import patch

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
from .render import fuentes
from .render.resolucion import ancho_texto_mm, payload_envio

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

    def test_tambien_se_reporta_el_recorte_de_un_texto_fijo(self):
        """Un texto fijo cortado tampoco puede pasar inadvertido.

        Cuando la plantilla la dibujó una persona, un rótulo de campo que no
        entra se ve en pantalla. Cuando la propuso el importador a partir de
        una foto, el ancho es una estimación del modelo y nadie la miró: el
        nombre de la empresa sale cortado en la impresión y no avisa nadie.
        """
        elemento = self.elemento(
            "texto_estatico", contenido="EXPRESO PAMPEANA", ancho_mm=Decimal("15")
        )
        lienzo = resolver(self.plantilla, {})
        self.assertEqual(lienzo.truncados, [f"texto_estatico#{elemento.pk}"])

    def test_un_texto_fijo_que_entra_no_se_reporta(self):
        self.elemento("texto_estatico", contenido="CP", ancho_mm=Decimal("40"))
        lienzo = resolver(self.plantilla, {})
        self.assertEqual(lienzo.truncados, [])

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


class QrDelEnvioTests(BaseRenderTests):
    """El QR que lleva adentro el envío entero, no un número suelto."""

    def setUp(self):
        super().setUp()
        self.qr_envio = VariableRotulo.objects.get(codigo="qr_envio")
        self.datos = {
            "destinatario": "Laura Fernández",
            "domicilio": "Belgrano 1847",
            "numero_pedido": "2888",
        }

    def test_el_payload_es_json_compacto_y_ordenado(self):
        """Ordenado para que el mismo envío dé siempre el mismo código."""
        payload = payload_envio({"b": "2", "a": "1"})
        self.assertEqual(payload, '{"a":"1","b":"2"}')

    def test_el_payload_no_escapa_los_acentos(self):
        """Escapar solo agranda el QR: el código va en bytes UTF-8."""
        payload = payload_envio({"destinatario": "Gómez"})
        self.assertIn("Gómez", payload)
        # Los seis caracteres de la secuencia de escape, no la o ya
        # decodificada: lo que se quiere comprobar es que json.dumps no
        # la escribio escapada, porque escapar solo agranda el QR.
        self.assertNotIn(chr(92) + "u00f3", payload)

    def test_el_payload_deja_afuera_lo_excluido_y_lo_vacio(self):
        payload = payload_envio(
            {"a": "1", "qr_envio": "x", "logo_empresa": "7", "vacio": ""},
            excluir={"qr_envio", "logo_empresa"},
        )
        self.assertEqual(payload, '{"a":"1"}')

    def test_se_dibuja_sin_necesitar_un_valor_propio(self):
        """Es la diferencia con `qr`: su contenido lo arma con el resto."""
        self.elemento("variable", variable=self.qr_envio,
                      ancho_mm=Decimal("34"), alto_mm=Decimal("34"))
        lienzo = resolver(self.plantilla, self.datos)

        self.assertIsInstance(lienzo.primitivas[0], Imagen)
        self.assertEqual(lienzo.faltantes, [])

    def test_el_contenido_lleva_los_datos_del_envio(self):
        excluidos = {"qr_envio", "logo_empresa"}
        payload = payload_envio(self.datos, excluidos)
        for valor in self.datos.values():
            self.assertIn(valor, payload)

    def test_una_imagen_no_entra_en_el_qr(self):
        """En los datos es un id de documento: no le dice nada a quien escanea."""
        self.elemento("variable", variable=self.logo, ancho_mm=Decimal("20"),
                      alto_mm=Decimal("20"))
        self.elemento("variable", variable=self.qr_envio,
                      ancho_mm=Decimal("34"), alto_mm=Decimal("34"))

        with patch("apps.labels.render.resolucion.codigos.generar_qr") as generar:
            generar.return_value = (PNG_MINIMO, 25)
            resolver(self.plantilla, dict(self.datos, logo_empresa="7"))

        contenido = generar.call_args[0][0]
        self.assertNotIn("logo_empresa", contenido)
        self.assertIn("destinatario", contenido)

    def test_sin_ningun_dato_se_reporta_como_faltante(self):
        """Un QR con "{}" adentro es un QR que no dice nada."""
        self.elemento("variable", variable=self.qr_envio,
                      ancho_mm=Decimal("34"), alto_mm=Decimal("34"))
        lienzo = resolver(self.plantilla, {})

        self.assertEqual(lienzo.primitivas, [])
        self.assertEqual(lienzo.faltantes, ["qr_envio"])

    def test_una_caja_chica_para_tantos_datos_se_avisa(self):
        """Entrar en la caja no es lo mismo que poder leerse.

        Con el envío entero adentro el QR pasa de los 60 módulos de lado; en
        una caja de 20 mm cada módulo queda en 0.33 mm y ningún lector de mano
        lo levanta. El rótulo sale igual —no romper la impresión de un lote es
        más importante— pero el aviso viaja.
        """
        self.elemento("variable", variable=self.qr_envio,
                      ancho_mm=Decimal("20"), alto_mm=Decimal("20"))
        lienzo = resolver(self.plantilla, {
            "destinatario": "Laura Fernández",
            "domicilio": "Belgrano 1847, Piso 2 Dto A",
            "localidad_provincia": "Rosario, Santa Fe",
            "codigo_postal": "S2000",
            "remitente": "Textiles del Litoral S.A.",
            "numero_pedido": "EP-44120",
        })

        self.assertEqual(lienzo.avisos, ["qr_denso:qr_envio"])
        # Se dibuja igual: el aviso informa, no cancela.
        self.assertIsInstance(lienzo.primitivas[0], Imagen)

    def test_una_caja_holgada_no_avisa(self):
        self.elemento("variable", variable=self.qr_envio,
                      ancho_mm=Decimal("40"), alto_mm=Decimal("40"))
        lienzo = resolver(self.plantilla, {"numero_pedido": "2888"})
        self.assertEqual(lienzo.avisos, [])

    def test_en_vista_previa_se_dibuja_como_marco_con_etiqueta(self):
        self.elemento("variable", variable=self.qr_envio,
                      ancho_mm=Decimal("34"), alto_mm=Decimal("34"))
        primitivas = resolver(self.plantilla, None).primitivas

        self.assertIsInstance(primitivas[0], Rectangulo)
        # La etiqueta se centra en el marco y se trunca si no entra, que
        # es lo que pasa aca: importa que salga de la variable, no que
        # entre entera.
        self.assertTrue(
            primitivas[1].texto.startswith("QR del env"), primitivas[1].texto
        )

    def test_el_informe_del_lote_junta_los_avisos_sin_repetir(self):
        self.elemento("variable", variable=self.qr_envio,
                      ancho_mm=Decimal("20"), alto_mm=Decimal("20"))
        envio = {
            "destinatario": "Laura Fernández",
            "domicilio": "Belgrano 1847, Piso 2 Dto A",
            "localidad_provincia": "Rosario, Santa Fe",
            "remitente": "Textiles del Litoral S.A.",
        }
        _, informe = renderizar_pdf(self.plantilla, lote=[envio, envio, envio])
        self.assertEqual(informe["avisos"], ["qr_denso:qr_envio"])


class FamiliaTipograficaTests(BaseRenderTests):
    """La familia elegida en el estilo llega hasta el dibujo y hasta la medida."""

    def test_la_familia_viaja_del_estilo_a_la_primitiva(self):
        self.elemento("texto_estatico", contenido="ABC",
                      estilo={"fuente": "courier"})
        texto = resolver(self.plantilla, {}).primitivas[0]
        self.assertEqual(texto.fuente, "courier")

    def test_cada_familia_usa_su_fuente_pdf(self):
        self.assertEqual(fuentes.nombre_pdf(familia="times"), "Times-Roman")
        self.assertEqual(
            fuentes.nombre_pdf(negrita=True, familia="courier"), "Courier-Bold")
        self.assertEqual(
            fuentes.nombre_pdf(negrita=True, cursiva=True, familia="helvetica"),
            "Helvetica-BoldOblique")

    def test_una_familia_desconocida_cae_en_la_por_defecto(self):
        """Salir con otra tipografía es mejor que no salir."""
        self.assertEqual(
            fuentes.nombre_pdf(familia="comic-sans-inexistente"),
            fuentes.nombre_pdf(familia=fuentes.FAMILIA_POR_DEFECTO),
        )

    def test_se_mide_con_la_familia_elegida(self):
        """Courier es más ancha que Helvetica al mismo cuerpo.

        Si se midiera todo con una sola familia, un texto en monoespaciada se
        desbordaría de su caja sin que el informe de truncados lo reporte.
        """
        self.assertGreater(
            ancho_texto_mm("1234567890", 10, fuente="courier"),
            ancho_texto_mm("1234567890", 10, fuente="helvetica"),
        )

    def test_el_truncado_tiene_en_cuenta_la_familia(self):
        """La misma caja y el mismo texto: entra en una familia y en otra no.

        El ancho de la caja se calcula en vez de fijarlo a mano: se elige uno
        que queda justo entre lo que mide el texto en cada familia, así el
        test dice lo que quiere decir sin depender de las métricas exactas de
        una versión de reportlab.
        """
        texto = "1234567890"
        angosta = ancho_texto_mm(texto, 10, fuente="helvetica")
        ancha = ancho_texto_mm(texto, 10, fuente="courier")
        self.assertLess(angosta, ancha)
        caja = Decimal(str(round((angosta + ancha) / 2, 2)))

        self.elemento("texto_estatico", contenido=texto, ancho_mm=caja,
                      estilo={"tamano_pt": 10, "fuente": "helvetica"})
        self.assertFalse(resolver(self.plantilla, {}).primitivas[0].truncado)

        self.plantilla.elementos.all().delete()
        self.elemento("texto_estatico", contenido=texto, ancho_mm=caja,
                      estilo={"tamano_pt": 10, "fuente": "courier"})
        self.assertTrue(resolver(self.plantilla, {}).primitivas[0].truncado)

    def test_el_catalogo_dice_que_hay_disponible(self):
        catalogo = {f["codigo"]: f for f in fuentes.familias_disponibles()}
        self.assertEqual(set(catalogo), set(fuentes.CODIGOS))
        self.assertTrue(catalogo[fuentes.FAMILIA_POR_DEFECTO]["por_defecto"])
        for datos in catalogo.values():
            self.assertIn("png_disponible", datos)


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
