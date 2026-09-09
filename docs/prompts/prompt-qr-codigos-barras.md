# Prompt para Claude Code — Códigos QR y de barras reales

ROTULOS_PERSO (Buspack — paquetería liviana). Respetá `CLAUDE.md`. **No `git pull/push/commit`.**
Escribí el código directo: no hace falta auditar el proyecto.

Continúa el punto anterior (render server-side con ReportLab en `apps/labels/rendering.py`), donde
el campo `qr` quedó como un recuadro reservado con un TODO. Ahora se implementa de verdad.

## Sin dependencias nuevas

ReportLab ya trae generación de códigos: `reportlab.graphics.barcode.createBarcodeDrawing`
soporta **Code128**, **EAN-13** y **QR**. No agregues `qrcode`, `python-barcode` ni ninguna otra
librería — todo sale del ReportLab que ya está en `requirements.txt`.

## 1. Formato en el `design`

Extendé el JSON del diseño para que los dos campos de código acepten configuración, **manteniendo
compatibilidad**: un diseño viejo que solo tenga `{"left": 68, "top": 5}` en `qr` tiene que seguir
renderizando, tomando los valores por defecto.

```json
"qr":      {"left": 68, "top": 5,  "size": 3.0, "data": "{{tracking_url}}"},
"barcode": {"left": 6,  "top": 78, "width": 8.0, "height": 1.5,
            "symbology": "code128", "data": "{{tracking}}", "show_text": true}
```

- `size`, `width`, `height` en **centímetros** (no en porcentaje: un código escaneable necesita un
  tamaño físico mínimo, y estirarlo con el rótulo lo vuelve ilegible).
- Defaults si no vienen: QR de 3 cm; barcode de 8 × 1.5 cm, `code128`, con texto debajo.
- `symbology` acepta `code128` (default) y `ean13`. Cualquier otro valor → 400 con mensaje claro.
- `data` admite los mismos marcadores `{{...}}` que los campos de texto.

Actualizá la validación del `design` en el serializer para aceptar estas claves nuevas y rechazar
tamaños fuera de rango (QR entre 1 y 10 cm; barcode entre 2 y 20 cm de ancho y 0.5 a 5 de alto).

## 2. Qué contiene el código

Agregá al contexto de `build_label_context` dos claves nuevas:

- `{{tracking}}` — el identificador del envío. Si el pedido tiene `tracking_number`, ese; si no,
  un código derivado del id con prefijo, tipo `BP-000123`. Nunca vacío: un rótulo sin código
  escaneable no sirve.
- `{{tracking_url}}` — la URL de seguimiento. `Order.tracking_url` si existe; si no, la armada
  sobre `FRONTEND_URL` con el código anterior.

Esto deja el punto de integración listo para cuando llegue la API del courier: se completan esos
campos del pedido y el rótulo empieza a imprimir el código real del partner sin tocar el render.

## 3. Render

En `rendering.py`, dos funciones nuevas que devuelven un `Drawing` de ReportLab listo para dibujar
en el canvas, y su uso desde `render_label_pdf`:

```python
def build_qr_drawing(data, size_cm): ...
def build_barcode_drawing(data, symbology, width_cm, height_cm, show_text): ...
```

Reglas que importan para que el código se pueda escanear de verdad:

- **Zona de silencio**: dejá un margen blanco alrededor del código (unos 2 mm en el QR, y en el
  barcode el ancho de 10 módulos a cada lado). Sin eso un lector no engancha.
- **Nada de estirar**: el QR siempre cuadrado. Si el espacio disponible no da, achicá
  proporcionalmente en vez de deformar.
- **Fondo blanco sólido** debajo del código, aunque el rótulo tenga otro fondo.
- Si `data` queda vacío después de resolver los marcadores, **no dibujes un código inválido**:
  dejá el espacio en blanco y seguí con el resto del rótulo.
- EAN-13 exige exactamente 13 dígitos: si el dato no cumple, devolvé 400 con un mensaje que diga
  qué se esperaba, en vez de generar un código roto que nadie va a poder leer.

## 4. Endpoint de imagen de código

`GET /api/v1/labels/barcode/` con query params `type` (`qr` | `code128` | `ean13`), `data`, y las
medidas opcionales. Devuelve un **PNG**.

Sirve para dos cosas: que el editor muestre el código real en la vista previa, y para depurar sin
tener que generar un PDF entero. Requiere autenticación y el permiso `labels.view`.

## 5. Editor

En `frontend/pedidos/diseñorotulos.html`, reemplazá el `<div id="qrcode">QR</div>` (que hoy es un
cartelito de texto) por una `<img>` que pida la imagen a ese endpoint, y agregá un campo de código
de barras arrastrable igual que los demás.

**No agregues una librería JS de códigos por CDN**: pedir la imagen al backend deja una sola
implementación de cómo se generan los códigos, y garantiza que lo que ves en el editor es
exactamente lo que se va a imprimir.

Sumá a la barra de herramientas del editor los controles mínimos para configurar el código
seleccionado: tamaño, simbología y el contenido (`data`), con los marcadores disponibles listados
para que se puedan elegir sin escribirlos de memoria.

## Test

Uno solo: que `build_barcode_drawing` con simbología `ean13` y un dato que no sea de 13 dígitos
falle de forma explícita en vez de devolver un dibujo inválido. Es el caso que se cuela silencioso
y termina en una etiqueta impresa que ningún lector reconoce.
