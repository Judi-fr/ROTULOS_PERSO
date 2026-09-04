"""Serializers de la app labels (catálogo de variables y plantillas).

La plantilla se expone junto con sus elementos de forma anidada: en una sola
petición el frontend recibe el lienzo (dimensiones/DPI/metadatos) y todos los
elementos ya posicionados. Al crear o editar, los elementos vienen dentro del
mismo cuerpo y se reemplazan de forma atómica (ver ``PlantillaSerializer``).

Las variables se referencian **por su código**, no por su id. Es más legible
en los cuerpos JSON, mantiene el mismo formato que tenía la API cuando el
catálogo era un enum del código, y coincide con lo que produce el servicio que
lee rótulos desde una foto, que trabaja con códigos y no conoce ids.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .estilos import validar_estilo
from .models import ElementoPlantilla, Plantilla, VariableRotulo


class VariableRotuloSerializer(serializers.ModelSerializer):
    """Una variable del catálogo.

    ``es_sistema`` es de solo lectura: las variables del sistema las siembra
    una migración, no se crean por API. ``creada_por`` lo fija la vista con el
    usuario autenticado.
    """

    creada_por_email = serializers.EmailField(
        source="creada_por.email", read_only=True, default=None
    )

    class Meta:
        model = VariableRotulo
        fields = [
            "id",
            "codigo",
            "etiqueta",
            "descripcion",
            "tipo_dato",
            "activa",
            "es_sistema",
            "orden",
            "creada_por",
            "creada_por_email",
            "creada_en",
        ]
        read_only_fields = [
            "id",
            "es_sistema",
            "creada_por",
            "creada_por_email",
            "creada_en",
        ]

    def validate(self, attrs):
        # El código de una variable del sistema es parte del contrato con el
        # motor de impresión y con el importador; renombrarlo rompería los
        # datos que llegan al imprimir. Las creadas por un usuario sí se
        # pueden corregir.
        if self.instance and self.instance.es_sistema:
            nuevo = attrs.get("codigo", self.instance.codigo)
            if nuevo != self.instance.codigo:
                raise serializers.ValidationError(
                    {
                        "codigo": "No se puede cambiar el código de una "
                        "variable del sistema."
                    }
                )
        return attrs


class ElementoPlantillaSerializer(serializers.ModelSerializer):
    """Un elemento posicionado dentro de una plantilla.

    Según ``tipo``, el contenido viene de ``variable`` (una referencia al
    catálogo) o de ``contenido`` (texto literal). Las líneas y los recuadros no
    llevan ninguno de los dos.

    El campo ``variable`` acepta y devuelve el **código** de la variable, no su
    id. El queryset no filtra por ``activa`` a propósito: desactivar una
    variable la retira de los selectores, pero una plantilla que ya la usaba
    tiene que poder seguir guardándose.
    """

    variable = serializers.SlugRelatedField(
        slug_field="codigo",
        queryset=VariableRotulo.objects.all(),
        allow_null=True,
        required=False,
    )
    variable_display = serializers.SerializerMethodField()
    variable_tipo_dato = serializers.SerializerMethodField()

    class Meta:
        model = ElementoPlantilla
        fields = [
            "id",
            "tipo",
            "variable",
            "variable_display",
            "variable_tipo_dato",
            "contenido",
            "x_mm",
            "y_mm",
            "ancho_mm",
            "alto_mm",
            "estilo",
            "orden",
        ]

    def get_variable_display(self, obj):
        """Etiqueta legible de la variable, o ``None`` si el elemento no usa una."""
        return obj.variable.etiqueta if obj.variable_id else None

    def get_variable_tipo_dato(self, obj):
        """Cómo debe dibujarse la variable (texto, qr, imagen…)."""
        return obj.variable.tipo_dato if obj.variable_id else None

    def validate_estilo(self, value):
        try:
            validar_estilo(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value

    def validate(self, attrs):
        # La coherencia entre tipo/variable/contenido vive en el modelo, para
        # que valga por cualquier vía de escritura (API, admin, shell). Acá se
        # reutiliza y se traduce al formato de errores de DRF.
        try:
            ElementoPlantilla(**attrs).clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs


class PlantillaSerializer(serializers.ModelSerializer):
    """Plantilla con sus elementos anidados.

    - ``creada_por`` es de solo lectura: lo fija la vista con el usuario
      autenticado, no se acepta desde el cuerpo.
    - ``elementos`` es escribible: al crear/editar se reemplaza el conjunto
      completo de elementos por el que llega en el cuerpo (semántica de PUT
      sobre la colección). Si no se manda la clave en un PATCH, los elementos
      quedan intactos.
    - ``ancho_px`` / ``alto_px`` son derivados (mm + DPI) y de solo lectura.
    """

    elementos = ElementoPlantillaSerializer(many=True, required=False)
    # default=None: 'creada_por' es SET_NULL, así que si se elimina el usuario
    # que creó la plantilla la fila sobrevive con el campo vacío. Sin el
    # default, serializarla lanzaría AttributeError al recorrer la relación.
    creada_por_email = serializers.EmailField(
        source="creada_por.email", read_only=True, default=None
    )
    ancho_px = serializers.IntegerField(read_only=True)
    alto_px = serializers.IntegerField(read_only=True)

    class Meta:
        model = Plantilla
        fields = [
            "id",
            "nombre",
            "descripcion",
            "ancho_mm",
            "alto_mm",
            "dpi",
            "orientacion",
            "metadatos",
            "activa",
            "elementos",
            "ancho_px",
            "alto_px",
            "creada_por",
            "creada_por_email",
            "creada_en",
            "actualizada_en",
        ]
        read_only_fields = [
            "id",
            "creada_por",
            "creada_por_email",
            "ancho_px",
            "alto_px",
            "creada_en",
            "actualizada_en",
        ]

    def _crear_elementos(self, plantilla, elementos):
        ElementoPlantilla.objects.bulk_create(
            [ElementoPlantilla(plantilla=plantilla, **elem) for elem in elementos]
        )

    @transaction.atomic
    def create(self, validated_data):
        elementos = validated_data.pop("elementos", [])
        plantilla = Plantilla.objects.create(**validated_data)
        self._crear_elementos(plantilla, elementos)
        return plantilla

    @transaction.atomic
    def update(self, instance, validated_data):
        # Si 'elementos' no viene (PATCH parcial), no se tocan los existentes.
        elementos = validated_data.pop("elementos", None)

        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        instance.save()

        # Reemplazo completo de la colección: borrar y recrear es simple y
        # deja el conjunto exactamente como lo mandó el cliente.
        if elementos is not None:
            instance.elementos.all().delete()
            self._crear_elementos(instance, elementos)

        return instance


class RenderizarSerializer(serializers.Serializer):
    """Cuerpo de ``POST /plantillas/<id>/renderizar/``.

    Los datos del envío viajan en el cuerpo y no salen de la base: no hay
    modelo de envío en el sistema, y quien los tenga —un ERP, un CSV, el
    editor— los manda y listo. Si más adelante aparece ese modelo, se agrega
    una variante que los lee por id sin romper este contrato.
    """

    formato = serializers.ChoiceField(choices=["pdf", "png"], default="pdf")
    # Sin datos sale la vista previa: cada variable se dibuja con su etiqueta.
    # Es lo que necesita el editor para mostrar el diseño sin un envío real.
    datos = serializers.DictField(required=False, allow_null=True)
    # Un rótulo por página, para despachar un lote completo de una vez.
    lote = serializers.ListField(
        child=serializers.DictField(), required=False, allow_empty=False
    )
    # Solo para PNG: permite una vista previa liviana sin tocar la plantilla.
    dpi = serializers.IntegerField(required=False, min_value=10, max_value=1200)

    def validate(self, attrs):
        if attrs.get("lote") and attrs.get("datos"):
            raise serializers.ValidationError(
                "Mandá 'datos' para un rótulo o 'lote' para varios, no ambos."
            )
        if attrs.get("lote") and attrs.get("formato", "pdf") != "pdf":
            raise serializers.ValidationError(
                {"lote": "Un lote solo se puede generar en PDF: un PNG es una "
                         "sola imagen y no tiene páginas."}
            )
        return attrs
