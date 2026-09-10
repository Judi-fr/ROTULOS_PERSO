# Prompt para Claude Code — Conectar los webhooks salientes

ROTULOS_PERSO (Buspack). Respetá `CLAUDE.md`. **No `git pull/push/commit`.** Andá directo al código.

## El problema

`apps/integrations/webhooks.py` está completo: `dispatch_event`, `send_webhook`, la firma HMAC y el
registro en `WebhookDelivery`. Pero **`dispatch_event` no lo llama nadie** — solo aparece en su
propia definición y en comentarios. Su docstring dice que se llama desde `Order.save()`, y ahí no
está. Resultado: se puede configurar un webhook saliente, guardarlo y verlo en la lista, y no se
dispara nunca.

## Qué hacer

Enchufalo desde `apps/orders/models.py`, en `Order.save()`, justo donde ya se crea el
`OrderStatusEvent`:

- Alta de pedido → evento `order.created`.
- Cambio de estado → evento `order.status_change`.
- El payload lleva: `id`, `external_id`, `status`, `status_anterior` (en el cambio de estado),
  `tracking_number`, `created_at`, y los datos de la dirección (destinatario, calle, número,
  ciudad, provincia, CP).
- Importá `dispatch_event` **dentro** de la función, no arriba del módulo: `apps.integrations`
  importa de `apps.orders` y al revés daría un import circular.

## Lo importante: que el lote no dispare 1000 webhooks en fila

El envío es síncrono con timeout de 3 segundos. Una importación de 1000 filas haría 1000 llamadas
HTTP encadenadas — hasta 50 minutos de request. Inaceptable.

Agregá una forma de **silenciar los webhooks para una operación**: un flag en la instancia (por
ejemplo `order._skip_webhooks = True`) que `save()` respete, o un context manager
`webhooks_suspended()` en `apps.integrations.webhooks` que use un `contextvar`. Elegí lo que quede
más limpio, pero que sea **una sola forma**.

Usalo en:

- La importación CSV/Excel (`apps/orders/import_views.py`).
- La ingesta por API y por webhook entrante (`apps/integrations/views.py`), cuando llegan varios
  pedidos en una sola llamada.

En esos casos, después de terminar, mandá **un solo** evento `orders.imported` con el resumen
(`{count, source, order_ids}`) en vez de uno por pedido.

El alta manual, la creación desde `pedidos.html` y los cambios de estado sueltos sí disparan
normal: ahí es un pedido por vez y no hay problema.

## Coherencia

Corregí el docstring de `webhooks.py` para que describa de dónde se llama de verdad, y sacá la
mención a `Order.save()` si el punto de llamada termina siendo otro.

## Test

Uno: que crear un pedido con un `WebhookEndpoint` activo genere un `WebhookDelivery`, y que
importar un archivo con la supresión activada **no** genere uno por fila. Mockeá `requests.post`
para no salir a la red.
