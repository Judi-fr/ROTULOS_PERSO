"""Modelos de la app documents: archivos subidos por el usuario.

Un ``Documento`` es la foto o el PDF de un rótulo que alguien sube para que el
sistema lo interprete. Se guarda como entidad propia y no como un campo dentro
de la importación por dos razones:

- El mismo archivo puede procesarse varias veces (si el primer intento salió
  mal, o si más adelante mejora el prompt del agente) sin volver a subirlo.
- Separar "tener el archivo" de "haberlo interpretado" permite que la subida
  sea rápida y la interpretación —que tarda entre 10 y 60 segundos— sea un
  paso aparte que se puede reintentar.

El tipo de archivo se valida por su contenido y no por el ``Content-Type`` que
declara el cliente, que es un dato que el cliente controla y por lo tanto no
es de fiar. Ver ``detectar_tipo``.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

# Formatos que la API de Claude acepta como imagen. No es una lista arbitraria:
# mandar cualquier otro formato hace fallar la llamada al modelo, así que se
# rechaza en la subida y no cuando ya es tarde.
TIPOS_IMAGEN = ("image/jpeg", "image/png", "image/gif", "image/webp")
TIPO_PDF = "application/pdf"
TIPOS_ACEPTADOS = TIPOS_IMAGEN + (TIPO_PDF,)

# Límite de la API para una imagen (se manda en base64, que agranda ~33%).
# Se valida acá para dar un error claro en la subida en vez de un 400 del
# modelo a los treinta segundos.
MAX_BYTES_IMAGEN = 5 * 1024 * 1024
MAX_BYTES_PDF = 32 * 1024 * 1024

# Firmas de los primeros bytes de cada formato. Se mira el contenido porque el
# Content-Type de un multipart lo escribe el cliente: renombrar un .exe a .jpg
# es suficiente para engañar a una validación por extensión o por cabecera.
_FIRMAS = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)


def detectar_tipo(archivo):
    """Devuelve el tipo MIME real de un archivo subido, o ``None``.

    ``archivo`` es un ``UploadedFile``. Se leen los primeros bytes y se deja el
    cursor donde estaba, para que quien llame pueda seguir usando el archivo.
    """
    posicion = archivo.tell()
    archivo.seek(0)
    cabecera = archivo.read(12)
    archivo.seek(posicion)

    for firma, tipo in _FIRMAS:
        if cabecera.startswith(firma):
            return tipo

    # WebP: "RIFF" + 4 bytes de tamaño + "WEBP".
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "image/webp"

    return None


def validar_archivo(archivo):
    """Valida tipo y tamaño de un archivo subido. Devuelve el tipo MIME real."""
    tipo = detectar_tipo(archivo)

    if tipo is None:
        raise ValidationError(
            "No se reconoce el formato del archivo. Se aceptan imágenes "
            "JPEG, PNG, GIF o WebP, y PDF."
        )
    if tipo not in TIPOS_ACEPTADOS:
        raise ValidationError(f"El formato {tipo} no está soportado.")

    maximo = MAX_BYTES_PDF if tipo == TIPO_PDF else MAX_BYTES_IMAGEN
    if archivo.size > maximo:
        raise ValidationError(
            f"El archivo pesa {archivo.size / 1024 / 1024:.1f} MB y el máximo "
            f"para este formato es {maximo // 1024 // 1024} MB."
        )

    return tipo


class Documento(models.Model):
    """Una foto o un PDF de rótulo subido por un usuario.

    ``tipo_mime`` se guarda detectado del contenido, no copiado de la petición,
    porque es lo que después decide cómo se le manda el archivo al modelo: las
    imágenes viajan como bloque ``image`` y los PDF como bloque ``document``.
    """

    archivo = models.FileField("archivo", upload_to="rotulos/%Y/%m/")
    nombre_original = models.CharField(
        "nombre original",
        max_length=255,
        help_text="Nombre que tenía el archivo en la máquina del usuario.",
    )
    tipo_mime = models.CharField("tipo MIME", max_length=50, choices=[
        (t, t) for t in TIPOS_ACEPTADOS
    ])
    tamano_bytes = models.PositiveIntegerField("tamaño (bytes)")

    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documentos",
        verbose_name="subido por",
    )
    subido_en = models.DateTimeField("subido en", auto_now_add=True)

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        ordering = ["-subido_en"]

    def __str__(self):
        return f"{self.nombre_original} ({self.tipo_mime})"

    @property
    def es_imagen(self):
        """¿Va como bloque ``image`` (True) o como bloque ``document`` (False)?"""
        return self.tipo_mime in TIPOS_IMAGEN
