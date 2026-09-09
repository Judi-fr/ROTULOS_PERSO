# Prompt para Claude Code — Generación del rótulo en el servidor (datos + plantilla → PDF)

ROTULOS_PERSO (Buspack — paquetería liviana; un rótulo es la etiqueta de encomienda que se pega
al paquete y viaja en el micro). Respetá `CLAUDE.md`. **No `git pull/push/commit`.**
Escribí el código directo: no hace falta auditar el proyecto.

## Qué existe hoy y qué falta

En `apps.labels` ya están los modelos `LabelTemplate` y `Label`. El diseño se guarda en el campo
`design` (JSON) con esta forma, que define el editor y **no hay que cambiar**:

```json
{
  "logo":         {"left": 6,  "top": 5},
  "qr":           {"left": 68, "top": 5},
  "remitente":    {"left": 6,  "top": 22, "text": "Remitente: Juan Pérez"},
  "destinatario": {"left": 6,  "top": 38, "text": "..."},
  "domicilio":    {"left": 6,  "top": 47, "text": "..."},
  "cp":           {"left": 6,  "top": 56, "text": "..."},
  "localidad":    {"left": 6,  "top": 63, "text": "..."},
  "pedido":       {"left": 6,  "top": 88, "text": "..."}
}
```

`left`/`top` son **porcentajes** del rótulo, medidos desde la esquina **superior izquierda**.
El tamaño está en `width_cm` / `height_cm`.

El problema: hoy el PDF y el PNG los dibuja el navegador con `html2canvas`/`jsPDF` sobre lo que se
ve en el editor. O sea que un rótulo solo existe si hay una persona mirando la pantalla. Esta
tarea mueve el render al backend, que es lo que después habilita el lote, la API de ingesta y la
salida ZPL.

## 1. Dependencia

Agregá **`reportlab`** a `backend/requirements.txt`. Es Python puro, no necesita librerías del
sistema (a diferencia de WeasyPrint, que pide cairo/pango), trabaja nativamente en centímetros y
es sobre lo que después se apoyan los códigos de barras y QR de la próxima etapa.

## 2. `apps/labels/rendering.py`

El módulo de render. Nada de lógica de HTTP acá — funciones puras que reciben datos y devuelven
bytes, para que después las pueda llamar tanto una vista como un proceso por lote.

```python
def percent_to_canvas_xy(left_pct, top_pct, width_cm, height_cm): ...
def build_label_context(order=None, label=None) -> dict: ...
def render_label_pdf(design, width_cm, height_cm, context=None, logo_file=None) -> bytes: ...
```

- **`percent_to_canvas_xy`**: convierte el porcentaje del editor a coordenadas de ReportLab.
  Dos conversiones en una: porcentaje → centímetros → puntos, y **el eje Y se invierte**, porque
  en el editor el 0 está arriba y en un PDF el origen está abajo a la izquierda. Es el punto donde
  más fácil se cuelan los errores, así que aislalo en esta función.
- **`build_label_context`**: arma el diccionario de valores reales del envío. Si recibe un `Order`,
  saca de él y de su `Address`: destinatario (nombre del usuario del pedido), domicilio (calle,
  número), CP, localidad y provincia, y el número de pedido (`Pedido #<id>`). El remitente sale de
  `settings` (agregá `LABEL_SENDER_NAME` y `LABEL_SENDER_ADDRESS` al `.env` y a `.env.example`,
  con defaults de Buspack). Sin `Order`, devuelve el contexto vacío.
- **`render_label_pdf`**: crea un canvas del tamaño exacto del rótulo (una sola página, sin
  márgenes), recorre `design` y dibuja cada campo en su posición:
  - Campos de texto: el `text` guardado en el diseño, pero **con sustitución de marcadores** —
    ver el punto 3.
  - `logo`: si viene un archivo de imagen, lo dibuja escalado dentro de una caja razonable
    manteniendo la proporción. Si no hay logo, no dibuja nada (no dejes el placeholder "+ Logo").
  - `qr`: por ahora **dejá un recuadro vacío reservado** en su posición, con un TODO. El QR y el
    código de barras reales son la etapa siguiente; no improvises una implementación acá.
  - Texto que no entra en el ancho del rótulo: cortarlo con elipsis, nunca dejar que se derrame
    fuera del área imprimible.
- Fuente Helvetica (built-in de ReportLab, sin archivos externos), tamaño proporcional a la altura
  del rótulo.

## 3. Marcadores en el texto

Para que **la misma plantilla sirva para envíos distintos**, el texto de un campo puede contener
marcadores `{{clave}}` que se reemplazan con el contexto: `{{destinatario}}`, `{{domicilio}}`,
`{{cp}}`, `{{localidad}}`, `{{pedido}}`, `{{remitente}}`.

- Un marcador desconocido o sin valor se reemplaza por cadena vacía, nunca deja el `{{...}}` a la
  vista en un rótulo impreso.
- El texto sin marcadores se dibuja tal cual (así los rótulos que ya existen siguen funcionando).

## 4. Endpoints

En `apps/labels`:

- `GET /api/v1/labels/labels/<id>/pdf/` — devuelve el PDF del rótulo propio.
  Permiso `labels.view`, queryset recortado a `request.user` como el resto del viewset.
  `Content-Type: application/pdf` y `Content-Disposition: attachment; filename="rotulo-<id>.pdf"`.
  Si el rótulo tiene `order`, usa ese contexto para resolver los marcadores.
- `POST /api/v1/labels/render/` — render **sin persistir**: recibe `{"template_id": N, "order_id": M}`
  y devuelve el PDF armado con el diseño de la plantilla y los datos de ese pedido.
  Valida que el pedido sea del usuario (un admin con `labels.view_all` puede usar cualquiera).
  Este endpoint es la semilla del lote: dejalo simple y sin estado.

Permiso nuevo `labels.render`, sembrado por migración idempotente (mismo patrón que las anteriores)
para los cuatro roles canónicos.

Registrá el render en auditoría con `apps.audit` si esa app existe (`label.render`).

## 5. Frontend

En el editor (`pedidos/diseñorotulos.html`), agregá un botón **"Descargar PDF (servidor)"** al lado
de los de export actuales, que pide el PDF al backend y lo baja.

**No saques los botones de export actuales**: el PNG y el PDF del navegador siguen siendo la vista
previa rápida de lo que estás diseñando. El del servidor es el rótulo definitivo, el que va a
imprenta y el que después va a salir por lote.

## Test

Escribí **un solo test**, sobre `percent_to_canvas_xy`: que un campo al 0% de arriba caiga en el
borde superior del PDF y uno al 100% en el inferior. Es la conversión que si sale mal imprime todo
al revés y no se nota hasta que ves el papel. El resto no hace falta testearlo.
