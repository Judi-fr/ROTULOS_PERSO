"""Direcciones y pedidos del usuario final (cliente).

El seguimiento en tiempo real con el courier todavía no está integrado:
no tenemos la API de la empresa de logística. Los campos de esa
integración (``carrier``, ``tracking_number``, ``tracking_url``) quedan
opcionales a la espera de esa conexión; mientras tanto el estado del
pedido se actualiza a mano (p. ej. desde el admin de Django) recorriendo
las etapas de ``Order.Status``. Cuando la API esté disponible, el punto
de integración es justamente completar esos tres campos.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Address(models.Model):
    """Dirección de envío guardada por un usuario para sus pedidos.

    Dos orígenes distintos conviven acá (``origin``): las que el usuario
    guarda a mano en su agenda (``own``) y las que se crean solas al entrar
    un pedido de una tienda, de una importación o de la API (``shipment``),
    que son la dirección de UN COMPRADOR. Solo las primeras se listan como
    "Mis direcciones" (ver ``AddressViewSet``): si no, un comerciante con
    cien pedidos de su tienda tendría cien direcciones ajenas en su agenda.
    """

    class Origin(models.TextChoices):
        OWN = "own", "Agenda del usuario"
        SHIPMENT = "shipment", "Dirección de un envío"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    label = models.CharField(max_length=50, blank=True, default="")
    # Nombre de quien recibe el envío en esta dirección. En el self-service
    # (el cliente guarda SU PROPIA dirección) queda vacío porque el
    # destinatario es implícitamente el dueño de la cuenta; lo completan la
    # carga manual, la importación y las entradas automáticas (apps.orders.
    # ingestion), donde la dirección es la de UN TERCERO.
    recipient_name = models.CharField(max_length=150, blank=True, default="")
    street = models.CharField(max_length=255)
    number = models.CharField(max_length=20, blank=True, default="")
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    country = models.CharField(max_length=100, default="Argentina")
    # Ej: "timbre azul", "portón negro" — ayuda al repartidor a encontrar el lugar.
    reference = models.CharField(max_length=255, blank=True, default="")
    is_default = models.BooleanField(default=False)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.OWN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "dirección"
        verbose_name_plural = "direcciones"
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.label or self.street} ({self.user.email})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Solo puede haber UNA dirección predeterminada por usuario: al marcar
        # esta como default, se desmarcan todas las demás del mismo usuario.
        if self.is_default:
            Address.objects.filter(user_id=self.user_id).exclude(pk=self.pk).update(
                is_default=False
            )


class Order(models.Model):
    """Pedido/envío de un usuario sobre una de sus direcciones guardadas."""

    class Status(models.TextChoices):
        CREATED = "created", "Creado"
        PREPARING = "preparing", "En preparación"
        DISPATCHED = "dispatched", "Despachado"
        IN_TRANSIT = "in_transit", "En tránsito"
        DELIVERED = "delivered", "Entregado"
        CANCELLED = "cancelled", "Cancelado"

    # Estados en los que el cliente todavía puede cancelar el pedido por sí
    # mismo (ver OrderViewSet.cancel). Una vez despachado, la cancelación
    # queda fuera de su alcance.
    CANCELLABLE_STATUSES = {Status.CREATED, Status.PREPARING}

    # Avance normal de un envío. Todo cambio de estado de envío (despacho
    # desde la web, sincronización con la tienda online) solo avanza en esta
    # lista; "cancelled" queda afuera a propósito.
    STATUS_PROGRESS = [Status.CREATED, Status.PREPARING, Status.DISPATCHED, Status.IN_TRANSIT, Status.DELIVERED]
    SHIPPING_STATUSES = {Status.DISPATCHED, Status.IN_TRANSIT, Status.DELIVERED}
    # Estados desde los que todavía se puede cargar/actualizar el envío.
    SHIPPABLE_STATUSES = {Status.CREATED, Status.PREPARING, Status.DISPATCHED, Status.IN_TRANSIT}

    @classmethod
    def status_rank(cls, status):
        try:
            return cls.STATUS_PROGRESS.index(status)
        except ValueError:
            return -1

    @property
    def is_shippable(self):
        return self.status in self.SHIPPABLE_STATUSES

    class Source(models.TextChoices):
        WEB = "web", "Autoservicio (web)"
        MANUAL = "manual", "Carga manual"
        IMPORT = "import", "Importación de archivo"
        API = "api", "API de ingesta"
        WEBHOOK = "webhook", "Webhook entrante"
        STORE = "store", "Tienda online conectada"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    # PROTECT: no se puede borrar una dirección que ya tiene pedidos, para no
    # perder el historial de a dónde se envió cada uno (mismo criterio que el
    # soft-delete de usuarios: no se destruye información con dependencias).
    address = models.ForeignKey(
        Address,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    description = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)

    # Identificador del pedido en el sistema del CLIENTE (su ERP, su
    # tienda) — la pieza que sostiene la idempotencia de toda entrada
    # operativa (importación, API, webhook): sin esto, reimportar el mismo
    # archivo o reintentar una llamada de API duplica el pedido. NULL (no
    # "") cuando no aplica, para que la unicidad por usuario no choque
    # entre múltiples pedidos sin external_id (ver Meta.constraints: NULL
    # nunca es igual a otro NULL, pero "" sí sería igual a otra "").
    external_id = models.CharField(max_length=100, null=True, blank=True)
    # Por dónde entró el pedido (reportes/depuración): default "web" porque
    # es el único origen que existía antes de esta migración.
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.WEB)

    # --- Integración con el courier (pendiente hasta tener la API) ---
    carrier = models.CharField(max_length=100, blank=True, default="")
    tracking_number = models.CharField(max_length=100, blank=True, default="")
    tracking_url = models.URLField(blank=True, default="")

    # --- Pedidos de una tienda online conectada (apps.integrations) ---
    # RESTRICT: una conexión no se borra, se desconecta; si se pudiera
    # borrar, sus pedidos perderían la clave de idempotencia (ver
    # Meta.constraints). A diferencia de PROTECT, deja borrar en cascada al
    # usuario dueño de ambos.
    store_connection = models.ForeignKey(
        "integrations.StoreConnection",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="orders",
    )
    # Número visible del pedido en la tienda (#1001): es lo que reconoce el
    # comerciante, distinto de su id interno (external_id).
    external_number = models.CharField(max_length=50, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=50, blank=True, default="")
    shipping_option = models.CharField(max_length=150, blank=True, default="")
    package_count = models.PositiveIntegerField(default=1)
    total_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    # [{"name", "sku", "quantity", "weight_kg"}] tal como vino de la tienda.
    items = models.JSONField(default=list, blank=True)
    # Pedido crudo de la tienda, para depurar un mapeo sin volver a pedirlo.
    raw_payload = models.JSONField(default=dict, blank=True)
    # Última modificación EN LA TIENDA: un aviso viejo que llega tarde no
    # pisa datos más nuevos (ver ingestion.upsert_store_order).
    external_updated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        ordering = ["-created_at"]
        constraints = [
            # Idempotencia por external_id. Pedidos cargados a mano,
            # importados o de la API/webhook genéricos: dentro del usuario.
            # Pedidos de una tienda conectada: dentro de ESA tienda (dos
            # tiendas del mismo cliente pueden tener las dos un "1001").
            models.UniqueConstraint(
                fields=["user", "external_id"],
                condition=models.Q(store_connection__isnull=True),
                name="unique_order_external_id_per_user",
            ),
            models.UniqueConstraint(
                fields=["store_connection", "external_id"],
                condition=models.Q(store_connection__isnull=False),
                name="unique_order_external_id_per_store",
            ),
        ]

    def __str__(self):
        return f"Pedido #{self.pk or '?'} ({self.user.email})"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._previous_status = self.status
        # Con campos diferidos (.only(), p. ej. el Collector al borrar una
        # dirección) leer el atributo dispara una query que crea otra
        # instancia, que vuelve a leerlo: recursión. Sin el dato cargado no se
        # puede saber si cambió, así que se registra como "desconocido".
        if {"tracking_number", "tracking_url"} & self.get_deferred_fields():
            self._previous_tracking = None
        else:
            self._previous_tracking = (self.tracking_number, self.tracking_url)

    @property
    def is_cancellable(self):
        return self.status in self.CANCELLABLE_STATUSES

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        # Cada alta o cambio de estado queda registrado en el timeline
        # (OrderStatusEvent), sin importar si lo disparó el cliente
        # (cancelar) o un cambio manual desde el admin.
        if is_new or self.status != self._previous_status:
            self.status_events.create(status=self.status)
            # Auditoría de cambios de estado hechos desde el admin de Django
            # o a mano (sin request, así que sin actor). Las vistas que ya
            # tienen actor (crear, cancelar) registran su propia entrada más
            # específica y marcan ``_skip_status_audit`` para no duplicarla.
            if not is_new and not getattr(self, "_skip_status_audit", False):
                from apps.audit.services import record

                record(
                    None,
                    category="orders",
                    action="order.status_change",
                    target=self,
                    target_type="order",
                    target_repr=str(self),
                    changes={"status": {"from": self._previous_status, "to": self.status}},
                )
            # Notifica al sistema del cliente (webhooks salientes, ver
            # apps.integrations) sin importar quién disparó el cambio. Un
            # pedido a la vez acá: las entradas por lote (importación,
            # API/webhook de ingesta) envuelven la suya en
            # webhooks_suspended() y mandan un único evento resumen, ver
            # el docstring de apps.integrations.webhooks.
            # Import local (mismo criterio que apps.audit.services acá
            # arriba) para no crear un ciclo de imports en tiempo de carga
            # de módulos: apps.orders no depende de apps.integrations
            # salvo en este punto de uso.
            from apps.integrations.webhooks import dispatch_event

            address = self.address
            dispatch_event(
                self.user,
                "order.created" if is_new else "order.status_change",
                {
                    "id": self.pk,
                    "external_id": self.external_id,
                    "status": self.status,
                    "status_anterior": None if is_new else self._previous_status,
                    "tracking_number": self.tracking_number,
                    # isoformat: WebhookDelivery.payload es un JSONField sin
                    # encoder especial (a diferencia de send_webhook, que
                    # serializa el body con json.dumps(default=str)), así
                    # que el datetime crudo no se puede persistir tal cual.
                    "created_at": self.created_at.isoformat() if self.created_at else None,
                    "destinatario": address.recipient_name,
                    "calle": address.street,
                    "numero": address.number,
                    "ciudad": address.city,
                    "provincia": address.state,
                    "cp": address.postal_code,
                },
            )
        tracking = (self.tracking_number, self.tracking_url)
        if not is_new and self.store_connection_id:
            # Pedido de una tienda online que se despachó/entregó (o al que se
            # le cargó el tracking): avisarle a la tienda. Solo encola, nunca
            # rompe el save (ver apps.integrations.fulfillment).
            from apps.integrations.fulfillment import notify_store_shipping_change

            notify_store_shipping_change(
                self,
                status_changed=self.status != self._previous_status,
                tracking_changed=self._previous_tracking is not None and tracking != self._previous_tracking,
            )
        self._previous_status = self.status
        self._previous_tracking = tracking
        self._skip_status_audit = False
        self._skip_store_notification = False


class OrderStatusEvent(models.Model):
    """Registro histórico de cada cambio de estado de un pedido (timeline)."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_events")
    status = models.CharField(max_length=20, choices=Order.Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "evento de estado de pedido"
        verbose_name_plural = "eventos de estado de pedido"
        ordering = ["created_at"]

    def __str__(self):
        return f"Pedido #{self.order_id}: {self.status}"


class OrderImport(models.Model):
    """Una importación de pedidos desde un archivo CSV/Excel (story 21),
    en tres pasos: subir (``pending``, ver ``import_views.OrderImportUploadView``),
    mapear columnas (``mapping``, ``OrderImportValidateView`` — valida sin
    escribir nada) y confirmar (``processing`` -> ``done``/``failed``,
    ``OrderImportConfirmView`` — recién ahí crea los pedidos).

    Se guarda el archivo original completo (no solo la vista previa): tanto
    "mapear" como "confirmar" lo vuelven a leer entero, así el usuario
    puede iterar el mapeo sin volver a subir nada.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente de mapeo"
        MAPPING = "mapping", "Mapeado, pendiente de confirmar"
        PROCESSING = "processing", "Procesando"
        DONE = "done", "Terminado"
        FAILED = "failed", "Falló"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="order_imports",
    )
    file = models.FileField(upload_to="orders/imports/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_rows = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    # Lista de {"fila": <int>, "columna": <str|None>, "mensaje": <str>} — el
    # número de fila es 1-based contando el encabezado, así coincide con lo
    # que el usuario ve si abre el archivo en una planilla de cálculo.
    errors = models.JSONField(default=list, blank=True)
    # Mapeo columna_origen -> campo_destino usado (o propuesto) para esta
    # importación puntual; ver ImportMapping para uno guardado y reutilizable.
    mapping = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "importación de pedidos"
        verbose_name_plural = "importaciones de pedidos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Importación #{self.pk or '?'} ({self.user.email})"


class ImportMapping(models.Model):
    """Plantilla de mapeo de importación guardada (story 22): "la próxima
    vez que llegue el mismo formato de archivo se aplica de una", en vez de
    volver a mapear columna por columna cada vez."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_mappings",
    )
    name = models.CharField(max_length=100)
    mapping = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "plantilla de mapeo de importación"
        verbose_name_plural = "plantillas de mapeo de importación"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_import_mapping_name_per_user")
        ]

    def __str__(self):
        return f"{self.name} ({self.user.email})"
