"""Tests de la app documents (subida de fotos y PDF de rótulos)."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import MAX_BYTES_IMAGEN, Documento, detectar_tipo

User = get_user_model()

# Cabeceras reales de cada formato. Un archivo con la firma correcta alcanza
# para que la detección funcione: no se abre ni se decodifica la imagen.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64


def archivo(nombre, contenido, content_type="image/png"):
    return SimpleUploadedFile(nombre, contenido, content_type=content_type)


class DeteccionDeTipoTests(APITestCase):
    """La detección mira el contenido, no lo que declara el cliente."""

    def test_reconoce_los_formatos_soportados(self):
        casos = [
            (PNG, "image/png"),
            (JPEG, "image/jpeg"),
            (WEBP, "image/webp"),
            (PDF, "application/pdf"),
            (b"GIF89a" + b"\x00" * 32, "image/gif"),
        ]
        for contenido, esperado in casos:
            with self.subTest(tipo=esperado):
                self.assertEqual(detectar_tipo(io.BytesIO(contenido)), esperado)

    def test_un_formato_desconocido_da_none(self):
        self.assertIsNone(detectar_tipo(io.BytesIO(b"MZ\x90\x00 esto es un .exe")))

    def test_no_mueve_el_cursor_del_archivo(self):
        f = io.BytesIO(PNG)
        f.seek(3)
        detectar_tipo(f)
        self.assertEqual(f.tell(), 3)


class SubidaDeDocumentosTests(APITestCase):
    def setUp(self):
        self.url = reverse("documento-list")
        self.usuario = User.objects.create_user(
            username="ana", email="ana@test.com", password="x"
        )
        self.otro = User.objects.create_user(
            username="beto", email="beto@test.com", password="x"
        )

    def test_requiere_autenticacion(self):
        respuesta = self.client.post(self.url, {"archivo": archivo("r.png", PNG)})
        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sube_una_imagen(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url, {"archivo": archivo("rotulo.png", PNG)}, format="multipart"
        )
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.assertEqual(respuesta.data["tipo_mime"], "image/png")
        self.assertEqual(respuesta.data["nombre_original"], "rotulo.png")
        self.assertEqual(respuesta.data["subido_por"], self.usuario.pk)

    def test_el_tipo_sale_del_contenido_y_no_de_la_peticion(self):
        """Declarar image/png en un .exe no lo convierte en imagen."""
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"archivo": archivo("virus.png", b"MZ\x90\x00...", "image/png")},
            format="multipart",
        )
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("archivo", respuesta.data)

    def test_un_jpeg_disfrazado_de_png_se_guarda_como_jpeg(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"archivo": archivo("foto.png", JPEG, "image/png")},
            format="multipart",
        )
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.assertEqual(respuesta.data["tipo_mime"], "image/jpeg")

    def test_una_imagen_demasiado_grande_da_400(self):
        self.client.force_authenticate(self.usuario)
        gigante = PNG + b"\x00" * (MAX_BYTES_IMAGEN + 1)
        respuesta = self.client.post(
            self.url, {"archivo": archivo("grande.png", gigante)}, format="multipart"
        )
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)

    def test_acepta_pdf(self):
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.post(
            self.url,
            {"archivo": archivo("rotulo.pdf", PDF, "application/pdf")},
            format="multipart",
        )
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.assertEqual(respuesta.data["tipo_mime"], "application/pdf")
        self.assertFalse(Documento.objects.get(pk=respuesta.data["id"]).es_imagen)

    def test_cada_usuario_solo_ve_sus_documentos(self):
        Documento.objects.create(
            archivo="rotulos/a.png",
            nombre_original="a.png",
            tipo_mime="image/png",
            tamano_bytes=10,
            subido_por=self.otro,
        )
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.data["count"], 0)

    def test_no_se_puede_acceder_al_documento_de_otro(self):
        ajeno = Documento.objects.create(
            archivo="rotulos/a.png",
            nombre_original="a.png",
            tipo_mime="image/png",
            tamano_bytes=10,
            subido_por=self.otro,
        )
        self.client.force_authenticate(self.usuario)
        respuesta = self.client.get(
            reverse("documento-detail", args=[ajeno.pk])
        )
        self.assertEqual(respuesta.status_code, status.HTTP_404_NOT_FOUND)
