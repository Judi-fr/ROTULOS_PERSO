// ---------------------------------------------------------------------------
// Tipografía del editor de rótulos (fase 2). Un solo lugar para:
//  - aplicar a un elemento del lienzo las claves de estilo de texto
//    (font_family, italic, uppercase, letter_spacing, max_lines, line_height),
//    igual que el PDF (backend/apps/labels/text_style.py);
//  - armar el bloque "Tipografía" que usan los tres paneles (campos, textos
//    fijos y texto de cajas).
// Expone window.LabelEditorType = { FAMILIES, cssFamily, supportsItalic,
// applyToElement, renderControls }.
// ---------------------------------------------------------------------------
(function () {
  const FAMILIES = [
    ["sans", "Sans (Helvetica)"],
    ["serif", "Serif"],
    ["script", "Manuscrita"],
    ["condensed", "Condensada"],
    ["mono", "Máquina de escribir"],
  ];
  // Los @font-face de editor_rotulos.html llevan estos nombres.
  const CSS_FAMILIES = {
    sans: "Helvetica, Arial, sans-serif",
    serif: '"RotulosSerif", Georgia, serif',
    script: '"RotulosScript", cursive',
    condensed: '"RotulosCondensed", "Arial Narrow", sans-serif',
    mono: '"RotulosMono", "Courier New", monospace',
  };
  // Manuscrita y condensada no tienen itálica: el PDF cae a la normal.
  const NO_ITALIC = ["script", "condensed"];
  const DEFAULT_LINE_HEIGHT = 1.15;
  const ITALIC_HINT = "Esta tipografía no tiene itálica";

  function cssFamily(family) {
    return CSS_FAMILIES[family] || CSS_FAMILIES.sans;
  }

  function supportsItalic(family) {
    return !NO_ITALIC.includes(family || "sans");
  }

  function ptToPx(pt, perCm) {
    return (pt / 72) * 2.54 * perCm;
  }

  function num(value, fallback) {
    const n = parseFloat(value);
    return Number.isFinite(n) ? n : fallback;
  }

  // Aplica el estilo a `el`. Lo que el diseño no trae se deja como estaba en
  // el CSS (se borra el estilo en línea): un diseño sin claves nuevas se ve
  // exactamente igual que antes. `options.rotated`: texto vertical, siempre
  // un renglón; `options.hasPrefix`: el campo lleva etiqueta delante;
  // `options.inverted`: blanco sobre negro, el color del diseño no se usa.
  function applyToElement(el, style, perCm, options) {
    style = style || {};
    const shapes = window.LabelEditorShapes;
    const color = !(options && options.inverted) && shapes ? shapes.resolveColor(style.color) : null;
    el.style.color = color || "";
    const family = style.font_family && CSS_FAMILIES[style.font_family] ? style.font_family : "";
    el.style.fontFamily = family && family !== "sans" ? cssFamily(family) : "";
    el.style.fontStyle = style.italic && supportsItalic(family || "sans") ? "italic" : "";
    el.style.textTransform = style.uppercase ? "uppercase" : "";
    const spacing = num(style.letter_spacing, 0);
    el.style.letterSpacing = spacing ? ptToPx(spacing, perCm) + "px" : "";

    const lines = options && options.rotated ? 1 : Math.max(1, Math.min(10, Math.round(num(style.max_lines, 1))));
    if (lines > 1) {
      el.style.whiteSpace = "normal";
      el.style.overflowWrap = "anywhere";
      const lineHeight = num(style.line_height, DEFAULT_LINE_HEIGHT);
      el.style.lineHeight = String(lineHeight);
      if (options && options.hasPrefix) {
        // El ::before de la etiqueta fija sería un renglón aparte dentro de
        // un -webkit-box: se recorta por altura, sin los puntos suspensivos.
        el.style.display = "";
        el.style.webkitBoxOrient = "";
        el.style.webkitLineClamp = "";
        const sizePx = parseFloat(el.style.fontSize) || 0;
        el.style.maxHeight = sizePx ? sizePx * lineHeight * lines + "px" : "";
      } else {
        el.style.display = "-webkit-box";
        el.style.webkitBoxOrient = "vertical";
        el.style.webkitLineClamp = String(lines);
      }
    } else {
      el.style.whiteSpace = "";
      el.style.overflowWrap = "";
      el.style.display = "";
      el.style.webkitBoxOrient = "";
      el.style.webkitLineClamp = "";
      el.style.lineHeight = "";
    }
    return lines;
  }

  // Bloque "Tipografía". getStyle() devuelve el objeto de estilo actual;
  // setKey(key, value) lo cambia (value "" = borrar la clave: como en el
  // panel de campos, un control en su valor por defecto no guarda nada).
  // Devuelve { refresh() } para volver a leer los valores al cambiar la
  // selección.
  function renderControls(container, getStyle, setKey) {
    const block = document.createElement("div");
    block.className = "type-block";
    block.innerHTML = `
      <span class="type-block-title">Tipografía</span>
      <label class="type-wide">Fuente
        <select data-type="font_family">
          ${FAMILIES.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
        </select></label>
      <label data-hint="italic"><input type="checkbox" data-type="italic" /> Itálica</label>
      <label><input type="checkbox" data-type="uppercase" /> Mayúsculas</label>
      <label>Espaciado (pt)
        <input type="number" data-type="letter_spacing" min="-1" max="10" step="0.5" placeholder="0" /></label>
      <label>Renglones
        <input type="number" data-type="max_lines" min="1" max="10" step="1" placeholder="1" /></label>
      <label class="type-wide" data-hint="line_height">Interlineado
        <input type="number" data-type="line_height" min="0.8" max="3" step="0.05" placeholder="1.15" /></label>`;
    container.appendChild(block);

    const input = (key) => block.querySelector(`[data-type="${key}"]`);

    function refresh() {
      const style = getStyle() || {};
      const family = style.font_family && CSS_FAMILIES[style.font_family] ? style.font_family : "sans";
      input("font_family").value = family;
      const italic = input("italic");
      const canItalic = supportsItalic(family);
      italic.checked = Boolean(style.italic) && canItalic;
      italic.disabled = !canItalic;
      block.querySelector('[data-hint="italic"]').title = canItalic ? "" : ITALIC_HINT;
      input("uppercase").checked = Boolean(style.uppercase);
      input("letter_spacing").value = style.letter_spacing != null ? style.letter_spacing : "";
      input("max_lines").value = style.max_lines != null ? style.max_lines : "";
      const lineHeight = input("line_height");
      lineHeight.value = style.line_height != null ? style.line_height : "";
      const multi = num(style.max_lines, 1) > 1;
      lineHeight.disabled = !multi;
      block.querySelector('[data-hint="line_height"]').title = multi
        ? ""
        : "Se usa cuando hay más de un renglón";
    }

    block.addEventListener("change", (event) => {
      const key = event.target.dataset && event.target.dataset.type;
      if (key === "font_family") {
        const family = event.target.value;
        setKey("font_family", family === "sans" ? "" : family);
        // Pasar a una fuente sin itálica no deja la clave colgada.
        if (!supportsItalic(family) && (getStyle() || {}).italic) setKey("italic", "");
        refresh();
      } else if (key === "italic" || key === "uppercase") {
        setKey(key, event.target.checked ? true : "");
      }
    });

    block.addEventListener("input", (event) => {
      const key = event.target.dataset && event.target.dataset.type;
      if (!["letter_spacing", "max_lines", "line_height"].includes(key)) return;
      let value = parseFloat(event.target.value);
      if (!Number.isFinite(value)) {
        setKey(key, "");
      } else {
        const limits = { letter_spacing: [-1, 10], max_lines: [1, 10], line_height: [0.8, 3] }[key];
        value = Math.max(limits[0], Math.min(limits[1], value));
        if (key === "max_lines") value = Math.round(value);
        const isDefault =
          (key === "letter_spacing" && value === 0) ||
          (key === "max_lines" && value === 1) ||
          (key === "line_height" && value === DEFAULT_LINE_HEIGHT);
        setKey(key, isDefault ? "" : value);
      }
      if (key === "max_lines") {
        const multi = num((getStyle() || {}).max_lines, 1) > 1;
        input("line_height").disabled = !multi;
      }
    });

    refresh();
    return { refresh, element: block };
  }

  window.LabelEditorType = {
    FAMILIES,
    cssFamily,
    supportsItalic,
    applyToElement,
    renderControls,
  };
})();
