"""Salida ZPL para impresoras térmicas Zebra (``apps.labels.zpl``).

Se afirma sobre los comandos generados y no sobre una imagen: el ZPL lo
rasteriza el firmware de la impresora, así que lo que está bajo nuestro
control —y lo único que podemos romper— es qué comandos salen y con qué
números. La verificación visual contra un motor ZPL real está más abajo
(``LabelaryRenderTests``), apagada por defecto porque necesita red.
"""

import io
import os
import unittest

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase
from PIL import Image
from rest_framework.test import APITestCase

from apps.orders.models import Address, Order

from ..models import LabelTemplate

from ..zpl import (
    DEFAULT_DPMM,
    cm_to_dots,
    escape_field_data,
    percent_to_dots,
    points_to_dots,
    render_label_zpl,
    render_labels_zpl,
)


User = get_user_model()


def comandos(zpl_text):
    return zpl_text.splitlines()


def campos_fd(zpl_text):
    """Los datos de cada ``^FD`` del ZPL, en orden."""
    return [
        linea[len("^FD") : -len("^FS")]
        for linea in comandos(zpl_text)
        if linea.startswith("^FD") and linea.endswith("^FS")
    ]


class UnidadesTests(SimpleTestCase):
    def test_centimetros_a_dots(self):
        """8 dots/mm son 80 por centímetro: un rótulo de 10 cm son 800."""
        self.assertEqual(cm_to_dots(10, 8), 800)
        self.assertEqual(cm_to_dots(10, 12), 1200)

    def test_puntos_a_dots_mantiene_el_tamanio_fisico(self):
        """72 pt es una pulgada: a 8 dots/mm tiene que dar 203 dots."""
        self.assertEqual(points_to_dots(72, 8), 203)
        self.assertEqual(points_to_dots(72, 12), 305)

    def test_el_eje_y_no_se_invierte(self):
        """A diferencia del PDF, el origen de ZPL ya es arriba a la
        izquierda: top=0 es el borde de arriba, no el de abajo."""
        x, y = percent_to_dots(0, 0, 10, 15, 8)
        self.assertEqual((x, y), (0, 0))
        _, y_abajo = percent_to_dots(0, 100, 10, 15, 8)
        self.assertEqual(y_abajo, cm_to_dots(15, 8))


class EstructuraTests(SimpleTestCase):
    DESIGN = {"destinatario": {"left": 10, "top": 20, "text": "Alguien"}}

    def test_abre_y_cierra_el_formato(self):
        salida = render_label_zpl(self.DESIGN, 10, 15)
        self.assertTrue(salida.startswith("^XA"))
        self.assertTrue(salida.rstrip().endswith("^XZ"))

    def test_declara_utf8(self):
        """Sin ^CI28 los acentos salen como basura, y es el error que no
        aparece si se prueba con datos en inglés."""
        self.assertIn("^CI28", comandos(render_label_zpl(self.DESIGN, 10, 15)))

    def test_declara_el_tamanio_de_la_etiqueta(self):
        salida = comandos(render_label_zpl(self.DESIGN, 10, 15))
        self.assertIn("^PW800", salida)
        self.assertIn("^LL1200", salida)

    def test_fija_el_origen(self):
        """Sin ^LH0,0 un trabajo anterior puede dejar todo corrido."""
        self.assertIn("^LH0,0", comandos(render_label_zpl(self.DESIGN, 10, 15)))

    def test_la_densidad_escala_todo_el_formato(self):
        """Mismo diseño, misma etiqueta física, más dots."""
        a_203 = comandos(render_label_zpl(self.DESIGN, 10, 15, dpmm=8))
        a_300 = comandos(render_label_zpl(self.DESIGN, 10, 15, dpmm=12))
        self.assertIn("^PW800", a_203)
        self.assertIn("^PW1200", a_300)

    def test_una_densidad_no_soportada_se_rechaza(self):
        with self.assertRaises(ValueError):
            render_label_zpl(self.DESIGN, 10, 15, dpmm=7)

    def test_varias_etiquetas_van_una_atras_de_otra(self):
        """Una térmica no tiene hoja: cada ^XA..^XZ es una etiqueta y el
        rollo avanza. No hay nada que 'acomodar' como en el A4 del PDF."""
        salida = render_labels_zpl(
            [
                {"design": self.DESIGN, "width_cm": 10, "height_cm": 15},
                {"design": self.DESIGN, "width_cm": 10, "height_cm": 15},
            ]
        )
        self.assertEqual(salida.count("^XA"), 2)
        self.assertEqual(salida.count("^XZ"), 2)


class TextoTests(SimpleTestCase):
    def test_posicion_tamanio_y_caja(self):
        salida = comandos(
            render_label_zpl({"a": {"left": 10, "top": 20, "text": "Hola"}}, 10, 15)
        )
        self.assertIn("^FO80,240", salida)
        # ^FB con 1 línea: el firmware recorta lo que no entra en vez de
        # escribir fuera del rótulo.
        self.assertTrue(any(linea.startswith("^FB720,1,0,L,0") for linea in salida))

    def test_alineacion(self):
        for align, esperado in (("left", "L"), ("center", "C"), ("right", "R")):
            with self.subTest(align=align):
                salida = render_label_zpl(
                    {"a": {"left": 0, "top": 0, "text": "Hola", "align": align}}, 10, 15
                )
                self.assertIn(f",{esperado},0", salida)

    def test_negrita_imprime_el_campo_dos_veces(self):
        """ZPL no trae una variante negrita de la fuente 0: se superpone el
        mismo texto corrido un dot. Sin esto, `bold` se ignoraría en
        silencio y el rótulo perdería su jerarquía."""
        normal = render_label_zpl({"a": {"left": 0, "top": 0, "text": "Hola"}}, 10, 15)
        negrita = render_label_zpl(
            {"a": {"left": 0, "top": 0, "text": "Hola", "bold": True}}, 10, 15
        )
        self.assertEqual(campos_fd(normal).count("Hola"), 1)
        self.assertEqual(campos_fd(negrita).count("Hola"), 2)

    def test_marcadores_resueltos(self):
        salida = render_label_zpl(
            {"a": {"left": 0, "top": 0, "text": "Para {{destinatario}}"}},
            10,
            15,
            context={"destinatario": "Ana"},
        )
        self.assertIn("Para Ana", campos_fd(salida))

    def test_hide_if_empty_omite_el_campo_entero(self):
        salida = render_label_zpl(
            {"a": {"left": 0, "top": 0, "text": "Tel. {{telefono}}", "hide_if_empty": True}},
            10,
            15,
            context={"telefono": ""},
        )
        self.assertEqual(campos_fd(salida), [])

    def test_un_texto_vacio_no_emite_nada(self):
        salida = render_label_zpl({"a": {"left": 0, "top": 0, "text": ""}}, 10, 15)
        self.assertEqual(campos_fd(salida), [])


class EscapeTests(SimpleTestCase):
    def test_los_prefijos_de_comando_se_escapan(self):
        """Un ^ o un ~ sin escapar cortan el texto y la impresora empieza a
        leer comandos: una URL de seguimiento con ~ rompe la etiqueta."""
        self.assertEqual(escape_field_data("a^b~c\\d"), "a\\5Eb\\7Ec\\5Cd")

    def test_el_texto_normal_no_se_toca(self):
        self.assertEqual(escape_field_data("María Gutiérrez 1602"), "María Gutiérrez 1602")

    def test_el_escape_llega_al_zpl_y_declara_FH(self):
        salida = render_label_zpl({"a": {"left": 0, "top": 0, "text": "x~y"}}, 10, 15)
        self.assertIn("^FH\\", comandos(salida))
        self.assertIn("x\\7Ey", campos_fd(salida))


class CodigosTests(SimpleTestCase):
    def test_qr_nativo(self):
        salida = comandos(
            render_label_zpl(
                {"qr": {"left": 50, "top": 50, "data": "ABC123", "size": 3}}, 10, 15
            )
        )
        self.assertTrue(any(linea.startswith("^BQN,2,") for linea in salida))

    def test_la_magnificacion_del_qr_baja_cuando_hay_mas_datos(self):
        """Un QR con más datos tiene más módulos: para ocupar el mismo lado,
        cada módulo tiene que medir menos."""
        def magnificacion(data):
            salida = render_label_zpl(
                {"qr": {"left": 0, "top": 0, "data": data, "size": 3}}, 10, 15
            )
            linea = next(l for l in comandos(salida) if l.startswith("^BQN"))
            return int(linea.split(",")[2])

        self.assertGreater(magnificacion("AB12"), magnificacion("x" * 200))

    def test_un_qr_sin_datos_no_se_dibuja(self):
        salida = render_label_zpl({"qr": {"left": 0, "top": 0, "data": ""}}, 10, 15)
        self.assertNotIn("^BQ", salida)

    def test_code128(self):
        salida = comandos(
            render_label_zpl({"barcode": {"left": 0, "top": 50, "data": "ABC123"}}, 10, 15)
        )
        self.assertTrue(any(linea.startswith("^BCN,") for linea in salida))
        self.assertTrue(any(linea.startswith("^BY") for linea in salida))

    def test_ean13(self):
        salida = comandos(
            render_label_zpl(
                {"barcode": {"left": 0, "top": 50, "data": "7790001234567", "symbology": "ean13"}},
                10,
                15,
            )
        )
        self.assertTrue(any(linea.startswith("^BEN,") for linea in salida))

    def test_un_ean13_invalido_se_corta_como_en_el_pdf(self):
        """Mismo criterio que build_barcode_drawing: un EAN-13 de otro largo
        se imprimiría como un código que ningún lector reconoce."""
        with self.assertRaises(ValueError):
            render_label_zpl(
                {"barcode": {"left": 0, "top": 0, "data": "123", "symbology": "ean13"}}, 10, 15
            )

    def test_simbologia_desconocida(self):
        with self.assertRaises(ValueError):
            render_label_zpl(
                {"barcode": {"left": 0, "top": 0, "data": "X", "symbology": "pdf417"}}, 10, 15
            )

    def test_show_text(self):
        con = render_label_zpl(
            {"barcode": {"left": 0, "top": 0, "data": "ABC", "show_text": True}}, 10, 15
        )
        sin = render_label_zpl(
            {"barcode": {"left": 0, "top": 0, "data": "ABC", "show_text": False}}, 10, 15
        )
        self.assertIn(",Y,N,N", con)
        self.assertIn(",N,N,N", sin)


class DecoracionesTests(SimpleTestCase):
    def test_recuadro_con_el_mismo_margen_que_el_pdf(self):
        """BORDER_INSET_CM = 0,2 cm, que a 8 dots/mm son 16 dots."""
        salida = comandos(render_label_zpl({"border": {}}, 10, 15))
        self.assertTrue(any(l.startswith("^FO16,16^GB768,1168,") for l in salida))

    def test_recuadro_punteado_se_dibuja_a_pedazos(self):
        """ZPL no tiene trazo punteado: un ^GB siempre sale sólido."""
        solido = render_label_zpl({"border": {}}, 10, 15)
        punteado = render_label_zpl({"border": {"dashed": True}}, 10, 15)
        self.assertEqual(solido.count("^GB"), 1)
        self.assertGreater(punteado.count("^GB"), 50)

    def test_linea_separadora(self):
        salida = comandos(render_label_zpl({"lines": [{"top": 50}]}, 10, 15))
        # left/right por defecto: 4% y 96% de 10 cm = 32 y 768 dots.
        self.assertTrue(any(l.startswith("^FO32,600^GB736,0,") for l in salida))

    def test_un_diseno_sin_decoraciones_no_emite_nada(self):
        self.assertNotIn("^GB", render_label_zpl({"a": {"left": 0, "top": 0, "text": "x"}}, 10, 15))


class LogoTests(SimpleTestCase):
    def _logo(self, mode="RGB", size=(200, 120)):
        image = Image.new(mode, size, "white" if mode != "RGBA" else (0, 0, 0, 0))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        buffer.seek(0)
        return buffer

    def test_se_emite_como_grafico_monocromo(self):
        salida = render_label_zpl(
            {"logo": {"left": 0, "top": 0}}, 10, 15, logo_file=self._logo()
        )
        self.assertIn("^GFA,", salida)

    def test_la_cabecera_declara_el_largo_real(self):
        """^GFA,<total>,<total>,<bytes por fila>,<hex>: si los números no
        coinciden con el dato, la impresora dibuja basura."""
        salida = render_label_zpl(
            {"logo": {"left": 0, "top": 0}}, 10, 15, logo_file=self._logo()
        )
        linea = next(l for l in comandos(salida) if l.startswith("^GFA,"))
        cabecera, _, resto = linea.partition(",")
        total, _, bytes_por_fila, payload = resto.split(",", 3)
        payload = payload[: -len("^FS")]
        self.assertEqual(len(payload), int(total) * 2)
        self.assertEqual(int(total) % int(bytes_por_fila), 0)

    def test_una_imagen_ilegible_no_rompe_la_etiqueta(self):
        """Igual que el PDF: sin logo se imprime el resto, no se pierde el
        envío por una imagen rota."""
        salida = render_label_zpl(
            {"logo": {"left": 0, "top": 0}, "a": {"left": 0, "top": 50, "text": "Hola"}},
            10,
            15,
            logo_file=io.BytesIO(b"esto no es una imagen"),
        )
        self.assertNotIn("^GFA,", salida)
        self.assertIn("Hola", campos_fd(salida))

    def test_un_png_transparente_no_sale_como_un_rectangulo_negro(self):
        """Aplanar un RGBA a secas deja el fondo en negro y la térmica quema
        el recuadro entero."""
        salida = render_label_zpl(
            {"logo": {"left": 0, "top": 0}}, 10, 15, logo_file=self._logo("RGBA")
        )
        linea = next(l for l in comandos(salida) if l.startswith("^GFA,"))
        payload = linea.rsplit(",", 1)[1][: -len("^FS")]
        self.assertEqual(set(payload), {"0"})


class ReglasCondicionalesTests(SimpleTestCase):
    def test_se_aplican_igual_que_en_el_pdf(self):
        design = {
            "a": {"left": 0, "top": 0, "text": "Pagado"},
            "rules": [
                {
                    "when": {"field": "estado", "op": "equals", "value": "debe"},
                    "then": {"action": "hide", "target": "a"},
                }
            ],
        }
        visible = render_label_zpl(design, 10, 15, context={"estado": "ok"})
        oculto = render_label_zpl(design, 10, 15, context={"estado": "debe"})
        self.assertIn("Pagado", campos_fd(visible))
        self.assertEqual(campos_fd(oculto), [])


@unittest.skipUnless(
    os.environ.get("ZPL_LABELARY_TESTS") == "1",
    "Necesita red: exportá ZPL_LABELARY_TESTS=1 para correrlo contra api.labelary.com",
)
class LabelaryRenderTests(SimpleTestCase):
    """Verificación contra un motor ZPL de verdad.

    Apagado por defecto a propósito: la suite no puede depender de un
    servicio ajeno. Sirve para comprobar de tanto en tanto que lo que
    generamos es ZPL válido y sale como se espera, sin tener la impresora.
    """

    URL = "https://api.labelary.com/v1/printers/{dpmm}dpmm/labels/3.94x5.91/0/"

    def test_una_etiqueta_completa_se_rasteriza(self):
        import requests

        design = {
            "border": {"dashed": True},
            "lines": [{"top": 40}],
            "destinatario": {"left": 5, "top": 10, "text": "María Gutiérrez", "bold": True},
            "qr": {"left": 60, "top": 60, "data": "https://example.com/x", "size": 3},
            "barcode": {"left": 5, "top": 60, "data": "010452"},
        }
        for dpmm in (8, 12):
            with self.subTest(dpmm=dpmm):
                respuesta = requests.post(
                    self.URL.format(dpmm=dpmm),
                    data=render_label_zpl(design, 10, 15, dpmm=dpmm).encode("utf-8"),
                    headers={"Accept": "image/png"},
                    timeout=30,
                )
                self.assertEqual(respuesta.status_code, 200, respuesta.text[:300])
                imagen = Image.open(io.BytesIO(respuesta.content))
                # La etiqueta mide lo mismo en centímetros con las dos
                # densidades; lo que cambia es cuántos dots la describen.
                self.assertAlmostEqual(imagen.width / imagen.height, 800 / 1200, places=2)


class RenderEndpointZplTests(APITestCase):
    """POST /api/v1/labels/render/ con ``format: "zpl"``.

    Es el mismo endpoint que devuelve el PDF: lo que cambia es la salida,
    no el diseño ni los permisos. Ver ``views.RenderLabelView``.
    """

    URL = "/api/v1/labels/render/"

    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="zebra@example.com", email="zebra@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        self.template = LabelTemplate.objects.create(
            name="Para térmica",
            owner=self.user,
            width_cm=10,
            height_cm=15,
            design={"destinatario": {"left": 10, "top": 20, "text": "{{destinatario}}"}},
        )
        self.address = Address.objects.create(
            user=self.user,
            origin=Address.Origin.SHIPMENT,
            recipient_name="María Gutiérrez",
            street="Cabildo",
            number="4781",
            city="CABA",
        )
        self.order = Order.objects.create(user=self.user, address=self.address)
        self.client.force_authenticate(user=self.user)

    def _post(self, **extra):
        payload = {"template_id": self.template.pk, "order_id": self.order.pk}
        payload.update(extra)
        return self.client.post(self.URL, payload, format="json")

    def test_por_defecto_sigue_siendo_pdf(self):
        """El formato nuevo no puede cambiarle la salida a quien ya usaba
        este endpoint."""
        respuesta = self._post()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/pdf")

    def test_format_zpl_devuelve_el_programa(self):
        respuesta = self._post(format="zpl")
        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.content.decode("utf-8")
        self.assertTrue(cuerpo.startswith("^XA"))
        self.assertIn("^CI28", cuerpo)
        self.assertIn("María Gutiérrez", cuerpo)

    def test_el_archivo_se_baja_con_extension_zpl(self):
        respuesta = self._post(format="zpl")
        self.assertIn(".zpl", respuesta["Content-Disposition"])

    def test_la_densidad_por_defecto_es_la_de_203_dpi(self):
        """8 dots/mm es lo que trae la enorme mayoría del parque Zebra; el
        comerciante que no configura nada tiene que imprimir bien."""
        cuerpo = self._post(format="zpl").content.decode("utf-8")
        self.assertIn(f"^PW{cm_to_dots(10, DEFAULT_DPMM)}", cuerpo)

    def test_se_puede_pedir_otra_densidad(self):
        cuerpo = self._post(format="zpl", dpmm=12).content.decode("utf-8")
        self.assertIn("^PW1200", cuerpo)

    def test_una_densidad_invalida_es_400(self):
        respuesta = self._post(format="zpl", dpmm=7)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("dpmm", respuesta.data)

    def test_un_formato_desconocido_es_400(self):
        respuesta = self._post(format="epl")
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("format", respuesta.data)

    def test_no_se_puede_renderizar_el_pedido_de_otro(self):
        """El ZPL no abre una puerta nueva: la identidad sigue saliendo de
        request.user, igual que para el PDF."""
        otro = User.objects.create_user(
            username="ajeno@example.com", email="ajeno@example.com", password="Clave123!"
        )
        otro.groups.add(Group.objects.get(name="subscriber"))
        self.client.force_authenticate(user=otro)
        self.assertEqual(self._post(format="zpl").status_code, 404)


class BatchZplTests(APITestCase):
    """POST /api/v1/labels/batch/ con ``output: "zpl"``.

    Es donde ZPL rinde de verdad: un lote de 50 etiquetas en un archivo de
    texto que la impresora consume directo, en vez de un PDF que tiene que
    rasterizar página por página.
    """

    URL = "/api/v1/labels/batch/"

    def setUp(self):
        from apps.documents.models import Document

        self.Document = Document
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="lotezpl@example.com", email="lotezpl@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        self.template = LabelTemplate.objects.create(
            name="Plantilla térmica",
            owner=self.user,
            width_cm=10,
            height_cm=15,
            design={"destinatario": {"left": 10, "top": 20, "text": "{{destinatario}}"}},
        )
        self.orders = []
        for numero in range(3):
            address = Address.objects.create(
                user=self.user,
                origin=Address.Origin.SHIPMENT,
                recipient_name=f"Cliente {numero}",
                street="Cabildo",
                number=str(1000 + numero),
                city="CABA",
            )
            self.orders.append(Order.objects.create(user=self.user, address=address))
        self.client.force_authenticate(user=self.user)

    def _post(self, **extra):
        payload = {
            "template_id": self.template.pk,
            "order_ids": [order.pk for order in self.orders],
            "output": "zpl",
        }
        payload.update(extra)
        return self.client.post(self.URL, payload, format="json")

    def _contenido(self, respuesta):
        documento = self.Document.objects.get(pk=respuesta.data["id"])
        documento.file.open("rb")
        try:
            return documento.file.read().decode("utf-8"), documento
        finally:
            documento.file.close()

    def test_genera_un_archivo_con_una_etiqueta_por_pedido(self):
        respuesta = self._post()
        self.assertEqual(respuesta.status_code, 202, respuesta.data)

        contenido, documento = self._contenido(respuesta)
        self.assertEqual(documento.status, self.Document.Status.READY)
        self.assertEqual(documento.item_count, 3)
        self.assertEqual(contenido.count("^XA"), 3)
        self.assertEqual(contenido.count("^XZ"), 3)

    def test_el_archivo_se_llama_zpl(self):
        _, documento = self._contenido(self._post())
        self.assertTrue(documento.file.name.endswith(".zpl"))

    def test_cada_etiqueta_lleva_los_datos_de_su_pedido(self):
        contenido, _ = self._contenido(self._post())
        for numero in range(3):
            self.assertIn(f"Cliente {numero}", contenido)

    def test_la_densidad_se_respeta(self):
        contenido, _ = self._contenido(self._post(dpmm=12))
        self.assertIn("^PW1200", contenido)
        self.assertNotIn("^PW800", contenido)

    def test_una_densidad_invalida_es_400_y_no_crea_documento(self):
        respuesta = self._post(dpmm=7)
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(self.Document.objects.count(), 0)

    def test_page_layout_se_ignora(self):
        """Una térmica no tiene hoja: pedir el layout A4 con salida ZPL no
        es un error del usuario, simplemente no aplica."""
        respuesta = self._post(page_layout="a4")
        self.assertEqual(respuesta.status_code, 202, respuesta.data)
        contenido, _ = self._contenido(respuesta)
        self.assertEqual(contenido.count("^XA"), 3)

    def test_un_output_desconocido_sigue_siendo_400(self):
        respuesta = self._post(output="epl")
        self.assertEqual(respuesta.status_code, 400)


class BatchZplDensidadDeLaTiendaTests(APITestCase):
    """De dónde sale la densidad cuando el usuario no la pide.

    Misma regla que la plantilla preferida (``_resolve_template``): lo que
    configuró la tienda, siempre que todos los pedidos del lote sean de esa
    misma tienda.
    """

    URL = "/api/v1/labels/batch/"

    def setUp(self):
        from apps.documents.models import Document
        from apps.integrations.models import StoreConnection

        self.Document = Document
        self.StoreConnection = StoreConnection
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="densidad@example.com", email="densidad@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        self.template = LabelTemplate.objects.create(
            name="Térmica", owner=self.user, width_cm=10, height_cm=15,
            design={"destinatario": {"left": 10, "top": 20, "text": "{{destinatario}}"}},
        )
        self.client.force_authenticate(user=self.user)

    def _tienda(self, external_id, dpmm=None):
        return self.StoreConnection.objects.create(
            owner=self.user,
            platform=self.StoreConnection.Platform.TIENDANUBE,
            external_store_id=external_id,
            name=f"Tienda {external_id}",
            label_printer_dpmm=dpmm,
        )

    def _pedido(self, store=None, sufijo="1"):
        address = Address.objects.create(
            user=self.user, origin=Address.Origin.SHIPMENT,
            recipient_name=f"Cliente {sufijo}", street="Cabildo", number=sufijo, city="CABA",
        )
        return Order.objects.create(
            user=self.user, address=address, store_connection=store,
            external_id=f"e{sufijo}" if store else None,
        )

    def _imprimir(self, pedidos, **extra):
        payload = {
            "template_id": self.template.pk,
            "order_ids": [p.pk for p in pedidos],
            "output": "zpl",
        }
        payload.update(extra)
        respuesta = self.client.post(self.URL, payload, format="json")
        self.assertEqual(respuesta.status_code, 202, respuesta.data)
        documento = self.Document.objects.get(pk=respuesta.data["id"])
        documento.file.open("rb")
        try:
            return documento.file.read().decode("utf-8")
        finally:
            documento.file.close()

    def test_usa_la_impresora_configurada_en_la_tienda(self):
        tienda = self._tienda("1", dpmm=12)
        contenido = self._imprimir([self._pedido(tienda, "1"), self._pedido(tienda, "2")])
        self.assertIn("^PW1200", contenido)

    def test_una_tienda_sin_configurar_cae_a_203_dpi(self):
        tienda = self._tienda("2")
        contenido = self._imprimir([self._pedido(tienda, "3")])
        self.assertIn("^PW800", contenido)

    def test_un_pedido_sin_tienda_cae_a_203_dpi(self):
        contenido = self._imprimir([self._pedido(None, "4")])
        self.assertIn("^PW800", contenido)

    def test_lo_que_pide_el_usuario_le_gana_a_la_tienda(self):
        tienda = self._tienda("3", dpmm=12)
        contenido = self._imprimir([self._pedido(tienda, "5")], dpmm=8)
        self.assertIn("^PW800", contenido)

    def test_dos_tiendas_con_densidades_distintas_caen_al_default(self):
        """No hay respuesta correcta para un lote mezclado: antes que elegir
        una al azar y sacar la mitad de las etiquetas a otra escala, se usa
        la densidad por defecto."""
        una = self._tienda("4", dpmm=12)
        otra = self._tienda("5", dpmm=24)
        contenido = self._imprimir([self._pedido(una, "6"), self._pedido(otra, "7")])
        self.assertIn("^PW800", contenido)

    def test_mezclar_un_pedido_sin_tienda_cae_al_default(self):
        tienda = self._tienda("6", dpmm=12)
        contenido = self._imprimir([self._pedido(tienda, "8"), self._pedido(None, "9")])
        self.assertIn("^PW800", contenido)
