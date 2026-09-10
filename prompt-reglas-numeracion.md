# Stories 28 y 29 — Datos calculados y reglas condicionales en el render

Implementá las dos cosas en `backend/apps/labels`. Van juntas porque ambas tocan el motor de
render, y las reglas de la 28 tienen que poder leer los valores calculados de la 29.

No hace falta que releas todo el proyecto ni que audites lo ya hecho: `rendering.py`, los
serializers de `apps.labels` y el endpoint de batch ya están escritos y funcionan. Escribí el
código directamente. Las pruebas manuales las hago yo.

---

## Parte 1 — Story 29: numeración secuencial, fechas y datos calculados

Hoy los marcadores `{{...}}` de una plantilla se resuelven contra los datos del pedido/rótulo.
Agregá una capa de **variables calculadas** que se resuelven en el mismo paso.

### Variables a soportar

| Marcador | Valor |
|---|---|
| `{{fecha}}` | fecha de emisión, `dd/mm/aaaa` |
| `{{hora}}` | `HH:MM` |
| `{{fecha_hora}}` | `dd/mm/aaaa HH:MM` |
| `{{fecha:<formato>}}` | fecha con formato `strftime` explícito, ej. `{{fecha:%Y-%m-%d}}` |
| `{{secuencia}}` | número secuencial, ver abajo |
| `{{bulto}}` | índice del rótulo dentro del lote, base 1 |
| `{{bultos}}` | cantidad total de rótulos del lote |
| `{{bulto_de_bultos}}` | `"2 de 5"` (si el lote es de 1, devuelve `"1 de 1"`) |

Reglas de resolución:

- Las variables calculadas tienen **prioridad menor** que los datos reales: si el pedido ya trae
  un campo con ese nombre, gana el dato del pedido. Así no rompemos plantillas existentes.
- Un marcador desconocido se resuelve a string vacío, igual que ahora — no lances excepción ni
  dejes el `{{...}}` crudo en el PDF.
- Zona horaria: usá `django.utils.timezone.localtime` para que la fecha salga en horario local,
  no UTC.

### Numeración secuencial

Modelo nuevo en `apps/labels/models.py`:

```python
class LabelSequence(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=50, default="default")
    prefix = models.CharField(max_length=20, blank=True)
    padding = models.PositiveSmallIntegerField(default=6)
    current = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("owner", "key")
```

- Método de clase `next_value(owner, key="default")` que incrementa y devuelve el valor formateado
  (`prefix` + número con ceros a la izquierda según `padding`). Tiene que ser atómico:
  `transaction.atomic()` + `select_for_update()` sobre la fila, o `F("current") + 1` seguido de
  `refresh_from_db()`. Nada de leer-sumar-guardar en Python sin lock.
- `get_or_create` de la fila si no existe, con los defaults del modelo.
- En un lote se pide **un valor por rótulo**, no uno por lote.
- Si el render es una vista previa (el endpoint `/labels/render/` sin persistir), **no** consumas
  secuencia: devolvé un valor de muestra (`prefix` + ceros + `1`). Pasá esto como flag explícito
  al resolver, no lo adivines desde el request.

Exponé la administración de secuencias como parte del template/label solamente si ya hay un lugar
natural; si no, con el modelo y el `next_value` alcanza — no armes endpoints CRUD para esto ahora.

### Contexto de lote

El endpoint de batch tiene que pasar al render, por cada ítem, su `bulto` (1..n) y `bultos` (n).
Si el render es de un rótulo suelto, ambos valen 1.

---

## Parte 2 — Story 28: reglas condicionales de contenido

Agregá al `design` una clave nueva `rules` (opcional, lista, default `[]`). El resto del `design`
no cambia: sigue siendo el dict con las claves del editor (`logo`, `qr`, `remitente`,
`destinatario`, `domicilio`, `cp`, `localidad`, `pedido`) y sus `left`/`top`/`text` en porcentaje.

### Forma de una regla

```json
{
  "when": { "field": "provincia", "op": "equals", "value": "Buenos Aires" },
  "then": { "action": "hide", "target": "qr" }
}
```

- `when.field` — nombre de un campo de datos del pedido/rótulo **o** de una variable calculada de
  la Parte 1.
- `when.op` — uno de: `equals`, `not_equals`, `contains`, `not_contains`, `empty`, `not_empty`,
  `in`, `gt`, `lt`. Para `empty`/`not_empty` el `value` se ignora. Para `in`, `value` es una lista.
  Comparación de strings: case-insensitive y con `strip()`. Para `gt`/`lt`, si ambos lados
  convierten a número comparás numérico, si no, comparás string.
- `then.action` — uno de:
  - `hide` — no dibujar `target` en el PDF.
  - `show` — dibujar `target` aunque una regla anterior lo haya ocultado.
  - `set_text` — reemplazar el texto de `target` por `then.value` (que puede contener marcadores
    `{{...}}`, se resuelven después).
  - `move` — reposicionar `target` a `then.left` / `then.top` (mismo rango 0-100).
- `then.target` — una clave existente del `design`.

### Evaluación

- Las reglas se evalúan **en orden**, todas, acumulando efectos sobre una copia del `design`. La
  última que toca un mismo atributo gana. No cortes en la primera que matchea.
- La evaluación produce un `design` efectivo que se le pasa al dibujado; el `design` guardado en
  la base **no se modifica**.
- Una regla que referencia un `target` inexistente o un campo que no existe se ignora en silencio
  (el render nunca puede fallar por una regla mal armada).
- Tope de 50 reglas por diseño.

### Validación

En `serializers.validate_design`, sumá la validación de `rules`: que sea lista, que cada ítem
tenga `when` y `then` con las claves obligatorias, que `op` y `action` estén dentro de los valores
permitidos, que `target` sea una clave conocida del `design`, y que `left`/`top` de un `move`
estén en 0-100. Errores → 400, igual que la validación que ya existe.

---

## Tests

Solo lo puntual, en `backend/apps/labels/tests.py`:

- `next_value` devuelve valores consecutivos y formateados con prefijo y padding.
- Una vista previa no consume secuencia (el `current` no cambia).
- Un batch de 3 ítems resuelve `{{bulto_de_bultos}}` como `"1 de 3"`, `"2 de 3"`, `"3 de 3"`.
- Un dato real del pedido pisa a una variable calculada del mismo nombre.
- Una regla `hide` saca el campo del design efectivo y no toca el guardado.
- Dos reglas sobre el mismo target: gana la última.
- Un `design` con una regla inválida (`op` desconocido) devuelve 400.

Correr `python manage.py test apps.labels` alcanza.

---

## Recordatorios del proyecto

- Migraciones nuevas para `LabelSequence`.
- Sin `git pull` / `push` / `commit`.
- Validación y reglas de negocio en el backend; el frontend no evalúa nada de esto.
- Identidad del owner de la secuencia siempre desde `request.user`, nunca desde un id del cliente.
- No toques el frontend en este prompt. La UI para cargar reglas y elegir secuencia va después.
