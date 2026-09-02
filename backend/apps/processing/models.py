"""Modelos de la app processing: lecturas de rótulos hechas por el agente.

Una ``ImportacionRotulo`` es el registro de haberle pedido a Claude que
interprete un ``Documento``. Existe como fila y no como una simple llamada de
ida y vuelta por tres motivos concretos:

- **Trazabilidad y costo.** Cada lectura consume tokens que se pagan. Guardar
  qué modelo se usó y cuántos tokens costó es lo que permite después saber
  cuánto sale importar un rótulo.
- **Depuración.** Cuando una lectura sale mal, lo único que sirve es ver qué
  devolvió el modelo exactamente. Por eso se guarda la respuesta cruda además
  de la propuesta ya convertida.
- **Poder reintentar.** El documento queda separado de su lectura, así que
  volver a procesar la misma foto —con un prompt mejorado, o con el catálogo
  ampliado— es crear otra importación, sin volver a subir nada.

La importación **no crea la plantilla**. Devuelve una *propuesta*: el mismo
cuerpo JSON que aceptaría ``POST /api/v1/labels/plantillas/``, para que la
persona lo revise y corrija en el editor antes de guardarlo. Un modelo se
equivoca, y una plantilla mal leída que se guarda sola es basura que alguien
tiene que salir a borrar.
"""

from django.conf import settings
from django.db import models


class EstadoImportacion(models.TextChoices):
    """En qué punto del proceso está una lectura."""

    PENDIENTE = "pendiente", "Pendiente"
    PROCESANDO = "procesando", "Procesando"
    COMPLETADA = "completada", "Completada"
    ERROR = "error", "Error"


class ImportacionRotulo(models.Model):
    """Una lectura de un rótulo hecha por el modelo.

    ``propuesta`` es el resultado útil: un diccionario con la forma que espera
    ``PlantillaSerializer``, listo para que el frontend lo mande a
    ``/api/v1/labels/plantillas/`` una vez que el usuario lo revisó.
    """

    documento = models.ForeignKey(
        "documents.Documento",
        on_delete=models.CASCADE,
        related_name="importaciones",
        verbose_name="documento",
    )
    estado = models.CharField(
        "estado",
        max_length=20,
        choices=EstadoImportacion.choices,
        default=EstadoImportacion.PENDIENTE,
    )

    # Qué modelo produjo esta lectura. Se guarda porque los resultados cambian
    # entre modelos: comparar dos importaciones sin saber cuál las hizo no
    # dice nada.
    modelo = models.CharField("modelo", max_length=60, blank=True, default="")

    # La propuesta ya convertida al formato de la API de plantillas.
    propuesta = models.JSONField("propuesta", blank=True, null=True)

    # Lo que devolvió el modelo, tal cual. Cuando una lectura sale rara, esto
    # es lo único que permite distinguir un error del modelo de un error de
    # nuestra conversión.
    respuesta_cruda = models.JSONField("respuesta cruda", blank=True, null=True)

    error = models.TextField("error", blank=True, default="")

    tokens_entrada = models.PositiveIntegerField("tokens de entrada", default=0)
    tokens_salida = models.PositiveIntegerField("tokens de salida", default=0)
    # Identificador de la petición del lado de Anthropic. Es lo que hay que
    # citar para reportar una respuesta anómala.
    request_id = models.CharField("request id", max_length=100, blank=True, default="")

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="importaciones_rotulo",
        verbose_name="creada por",
    )
    creada_en = models.DateTimeField("creada en", auto_now_add=True)
    finalizada_en = models.DateTimeField("finalizada en", null=True, blank=True)

    class Meta:
        verbose_name = "importación de rótulo"
        verbose_name_plural = "importaciones de rótulo"
        ordering = ["-creada_en"]

    def __str__(self):
        return f"Importación {self.pk} ({self.get_estado_display()})"

    @property
    def confianza(self):
        """Qué tan segura dijo estar la lectura, de 0 a 1, o ``None``."""
        if not self.respuesta_cruda:
            return None
        return self.respuesta_cruda.get("confianza")
