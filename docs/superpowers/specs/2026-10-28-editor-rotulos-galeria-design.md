# Editor de rótulos: personalización completa y galería de diseños

Fecha: 2026-10-28. Conversación de brainstorming con el usuario (bocetos A–H en
`.superpowers/brainstorm/`, archivos `disenios-base.html` y `disenios-mas.html`).

## Intención y por qué

El usuario quiere que quien diseña un rótulo tenga "la mayor cantidad de opciones de diseño y
personalización posible": colores, tipografía, tamaños, imágenes propias y armar el rótulo en forma de
celdas (como la etiqueta de OCA). Además quiere una galería de diseños predeterminados para elegir: no
espera que un comerciante use todos, pero tener opciones suma. El costo en tiempo no es un problema:
"mientras tengamos el mejor producto que se pueda hacer".

**Criterio de aceptación (lo propuso Claude, el usuario lo aprobó: "perfecta"):** *el editor tiene que
poder construir, por sí solo, cada diseño de la galería.* Una plantilla que el editor no pueda rehacer
"no tiene valor alguno" (palabras del usuario). Las plantillas de la galería se arman con las mismas
piezas que el comerciante tiene en el editor; no hay nada dibujado "a mano" en el código.

## La galería (decidido por el usuario: los ocho entran)

Orden de preferencia del usuario: H se destacó; luego D, C, B, A; E, F y G le gustaron todos. Orden de la
galería (decisión de Claude a partir de eso): **H, D, C, E, F, G, B, A**.

| | Nombre | Qué lo define |
|---|---|---|
| H | Moderno | Barra superior oscura con logo, nombre enorme en mayúsculas, secciones numeradas (círculos con número), dos columnas con línea vertical (CP / servicio), acento de color (verde lima) en una pastilla "EXPRESS". |
| D | Con la marca | Franja superior con fondo de color/imagen de la tienda y logo circular, tipografía con serifa, borde redondeado de color, CP en pastilla redondeada, mensaje propio ("¡Gracias por tu compra!"). |
| C | Destacado | Banda de color arriba con la tienda y el número, CP enorme en banda negra a todo el ancho, sello "FRÁGIL" (borde rojo redondeado). |
| E | Editorial | Blanco y negro, serifa, mucho aire, títulos chicos en mayúsculas espaciadas y gris, nombre en dos renglones, CP grande en serifa. |
| F | Banda lateral | Franja vertical de color a todo el alto con el nombre de la tienda rotado, CP en recuadro redondeado, íconos de manipulación (este lado arriba, no mojar, frágil). |
| G | Postal | Fondo crema, marco punteado redondeado, estampilla (imagen/recuadro punteado) con la marca, saludo manuscrito con el nombre de pila ("¡Hola, Mariela!"), renglones bajo la dirección. |
| B | Celdas | Grilla al estilo OCA: celdas con borde, encabezados en celda gris y valor en celda blanca; bulto, peso, servicio; CP grande. |
| A | Estándar | La plantilla del sistema que ya existe ("Etiqueta de envío estándar"). Se mantiene tal cual. |

Todas en 10×15 cm. Los bocetos HTML son la referencia visual; el builder los traduce a diseños del
editor (los datos de muestra los pone `build_preview_context`).

## Lo que el editor tiene que poder hacer (lo nuevo)

Hoy el editor tiene: campos de texto con tamaño, negrita, alineación, ancho, etiqueta delante, invertido,
recuadro y rotación 90/270; líneas solo horizontales; borde del rótulo (liso/punteado); textos fijos;
un logo; QR; código de barras; todo en Helvetica y en negro. Para construir H–B hace falta:

1. **Formas libres:** rectángulos (con o sin relleno, borde de grosor elegible, esquinas redondeadas),
   círculos/elipses, y líneas horizontales **y verticales** con grosor y estilo (lisa, punteada,
   discontinua). Posición y tamaño en % del rótulo, como el resto del diseño.
2. **Cajas con contenido (celdas):** una forma que contiene un texto fijo o un dato (`{{...}}`), con
   relleno, borde, márgenes internos y alineación horizontal y vertical. Una grilla tipo OCA se arma con
   cajas alineadas (con el imán y "distribuir"); la galería trae B como estructura lista. *(Decisión de
   Claude: no hay un elemento "tabla" con filas/columnas; las celdas son cajas. El usuario pidió las dos
   formas —dibujar libre y elegir una estructura armada— y esto cubre ambas con una sola pieza.)*
3. **Color** en texto, relleno y borde (paleta de la app + selector libre). Ver "Impresora térmica".
4. **Tipografía:** familias empaquetadas en el repo (licencia OFL, sin CDN): sans (Helvetica, la de hoy),
   serif, manuscrita, condensada y monoespaciada. *(Elección concreta de archivos: del builder; tienen que
   tener acentos y ñ.)* Además: itálica, mayúsculas, espaciado entre letras, interlineado y **texto en
   varios renglones** (con un máximo de renglones; hoy se corta en uno).
5. **Íconos** de un set fijo dibujado en vectores: frágil, este lado arriba, no mojar, no apilar,
   teléfono, casa, corazón, estrella, camión, caja. Con color y tamaño.
6. **Imágenes propias**, varias por plantilla (además del logo de la tienda), subidas por el comerciante,
   con ajuste (contener/cubrir), opción de recorte circular y esquinas redondeadas. Se guardan con la
   plantilla.
7. **Orden de capas** (traer al frente / enviar atrás). Hoy no existe a propósito porque el texto siempre
   queda arriba; con fondos y cajas hace falta. Un diseño viejo sin orden explícito se dibuja como hoy.
8. **Datos nuevos para insertar** (propuesta de Claude): bultos ("1/2", de `Order.package_count`), peso
   (`total_weight_kg`), servicio (`shipping_option`/servicio del transportista) y nombre de pila del
   destinatario (para "¡Hola, Mariela!"). Siguen la regla del rótulo: nada de productos, precios ni
   contacto del comprador.
9. **Comodidades de edición** (propuesta de Claude, necesarias con tantas opciones): deshacer/rehacer,
   duplicar, selección múltiple con alinear y distribuir, zoom del lienzo.
10. **Vista "así sale en térmica"** (propuesta de Claude): ver el diseño como lo imprime una Zebra (blanco
    y negro con tramas).
11. **Galería al crear plantilla:** "+ Nueva plantilla" abre la galería (los ocho diseños + "en blanco");
    elegir uno abre el editor con una copia de ese diseño.

## Impresora térmica (decidido)

Las opciones son "para todo el mundo" (usuario): el mismo diseño tiene que servir en impresora común
(PDF, color real) y en térmica Zebra (ZPL, sin color). En térmica el color se convierte solo: lo oscuro a
negro, lo claro a blanco, los tonos medios a trama de gris; el editor avisa "en impresora térmica esto
sale en blanco y negro". **Fidelidad en ZPL** (propuesta de Claude): lo que la Zebra no dibuja sola
(tipografías que no son la suya, íconos, círculos con relleno parcial, imágenes, tramas) se manda como
gráfico (`^GF`), para que salga como en el PDF y no como una aproximación. Rectángulos, líneas y
esquinas redondeadas usan los comandos propios (`^GB` admite redondeo, `^GE`/`^GC` elipses).

## Restricciones

- **Regla del espejo (CLAUDE.md):** cada opción se dibuja igual en el lienzo del editor, en el PDF
  (`label_rendering.py`) y en ZPL (`zpl.py`); el PDF tiene la última palabra y "Ver cómo sale impreso"
  sigue siendo la red de seguridad.
- **Compatibilidad:** toda plantilla y rótulo guardados hoy imprimen exactamente igual después del
  cambio (tests de regresión sobre el PDF y el ZPL de los diseños existentes). Las claves guardadas del
  JSON `design` no se renombran.
- Los códigos de barras siguen las reglas de legibilidad actuales (módulos en puntos enteros a 203 dpi,
  ancho mínimo); ningún diseño de la galería puede producir un código ilegible (tests de escaneo).
- Validación en el backend (`serializers.py`): tipos, rangos, colores, familias e íconos conocidos,
  tamaño de imágenes.
- Las miniaturas de las plantillas (`thumbnails.py`) salen del mismo PDF.
- Frontend: el editor ya es grande (`editor_rotulos.html`, 2700 líneas); las piezas nuevas van en sus
  propios archivos `assets/js/label_editor_*.js`, como las que ya existen. La galería es su propia
  página o diálogo.

## Objetivos, no-objetivos y anti-objetivos

- **Objetivo:** que el editor construya los ocho diseños y que estén en la galería como plantillas del
  sistema, editables (se usan como punto de partida: se duplican y se cambian).
- **No-objetivos:** color en térmica (es físico); rotación libre en cualquier ángulo (se mantiene en
  pasos de 90°); que el comerciante suba sus propias tipografías; tocar el sistema 2 (`ElementLayout`).
- **Anti-objetivos (sería un fracaso aunque funcione):** que una plantilla vieja cambie al imprimir; que
  el editor muestre algo distinto de lo que sale impreso; que el panel de opciones se vuelva una pared de
  casillas incomprensible (aplicar design-taste-frontend-v1 al inspector: agrupado y progresivo); que un
  diseño de la galería no se pueda rehacer con el editor.

## Lo que queda para el builder

- El formato exacto de los elementos nuevos dentro de `design` (por ejemplo una lista `shapes` con `id`,
  `kind`, posición, tamaño, estilo y `z`), siempre que lo viejo siga valiendo.
- Qué archivos de fuente usar y cómo registrarlos en ReportLab.
- El orden de entrega por fases. Sugerencia de Claude: (1) formas, cajas, color, capas y líneas
  verticales con su PDF/ZPL/lienzo; (2) tipografía, varios renglones e íconos; (3) imágenes y datos
  nuevos; (4) comodidades de edición y vista térmica; (5) los ocho diseños como plantillas + galería.
  Cada fase deja todo andando.

## Decisiones que tomó Claude (para que el usuario las revise)

- Las celdas son "cajas con contenido", no un elemento tabla.
- Orden de la galería: H, D, C, E, F, G, B, A.
- Las cinco familias tipográficas (sans, serif, manuscrita, condensada, mono) y el set de diez íconos.
- En térmica, lo que la Zebra no sabe dibujar va como gráfico `^GF`.
- Los datos nuevos (bultos, peso, servicio, nombre de pila) y las comodidades de edición (deshacer,
  duplicar, alinear, zoom, vista térmica).
- La "Etiqueta de envío estándar" (A) sigue siendo la plantilla por defecto cuando el comerciante no eligió
  otra.
- **Colores del diseño:** cada plantilla tiene un color principal y uno de acento a los que los elementos
  pueden remitir; cambiarlos recolorea todo el diseño de una vez (así un comerciante adapta D o H a su
  marca sin tocar seis elementos). Un elemento también puede tener su color propio.
- **Las plantillas de la galería no traen imágenes de stock:** donde un boceto muestra una imagen de marca
  (la franja de D, la estampilla de G) va el logo de la tienda, y el comerciante puede reemplazarlo por
  una imagen propia.
- **Las reglas según los datos del envío** (`label_editor_rules.js`: ocultar/mover) también se aplican a
  los elementos nuevos.
- Las plantillas de la galería son del sistema (no se editan); elegir una crea una copia del comerciante,
  que sí se edita y tiene su historial de versiones.
