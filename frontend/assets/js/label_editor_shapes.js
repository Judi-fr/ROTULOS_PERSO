// ---------------------------------------------------------------------------
// Editor de rótulos: formas, cajas con contenido, paleta y capas
// (design["shapes"] / design["palette"], fase 1 del editor:
// docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md).
//
// El lienzo es un ESPEJO del PDF (backend/apps/labels/shapes_pdf.py) y del
// ZPL (shapes_zpl.py): mismos números (grosor por defecto 0,6 pt, punteados
// 4/3 y 1/2 pt, margen interno 1 mm, recorte al borde del rótulo, orden por
// `z`). Si cambian allá, cambian acá. Las formas son la capa de fondo: quedan
// debajo de los campos (logo, códigos, textos) y encima del recuadro y las
// líneas de "Decoraciones", igual que en el PDF.
//
// El dato manda: las posiciones viven en `shapes` (en % del rótulo) y la capa
// se vuelve a dibujar en cada render, como las decoraciones.
//
// Expone window.LabelEditorShapes = { init, load, collect, render, select,
// selectedId, ruleTargets, usesColor }.
// ---------------------------------------------------------------------------
(function () {
  const DEFAULT_STROKE_WIDTH_PT = 0.6;
  const DEFAULT_PADDING_MM = 1;
  const DASH_PATTERNS_PT = { dashed: [4, 3], dotted: [1, 2] };
  const PALETTE_DEFAULTS = { primary: "#1e3a8a", accent: "#a3e635" };
  const SWATCHES = ["#000000", "#ffffff", "#e5e7eb", "#9ca3af", "#7c2d12", "#1e3a8a", "#166534", "#dc2626"];
  const KIND_LABELS = { rect: "Rectángulo", ellipse: "Círculo", line: "Línea" };

  let shapes = [];
  let palette = {};
  let selected = null;
  let hooks = { label: null, rerender() {}, clearOthers() {}, pxPerCm: () => 37.8 };
  let lastMetrics = null;

  // --- Colores (espejo de design_shapes.resolve_color/thermal_tone) -------
  function resolveColor(value) {
    if (value === "primary" || value === "accent") value = palette[value];
    if (typeof value !== "string" || !/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)) return null;
    let digits = value.slice(1).toLowerCase();
    if (digits.length === 3) digits = digits.split("").map((ch) => ch + ch).join("");
    return "#" + digits;
  }

  function isBlackOrWhite(hex) {
    return hex === "#000000" || hex === "#ffffff";
  }

  // ¿El diseño usa algún color que en una térmica no sale igual?
  function usesColor() {
    const all = [];
    shapes.forEach((shape) => {
      all.push(shape.fill, shape.stroke, shape.text && shape.text.color);
    });
    return all.map(resolveColor).some((hex) => hex && !isBlackOrWhite(hex));
  }

  // --- Geometría (espejo de shapes_pdf.shape_box_pct) ---------------------
  function num(value, fallback) {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function boxPct(shape) {
    const left = num(shape.left, 0);
    const top = num(shape.top, 0);
    const width = Math.max(0, num(shape.width, 0));
    const height = Math.max(0, num(shape.height, 0));
    const clamp = (v) => Math.min(Math.max(v, 0), 100);
    return { left: clamp(left), top: clamp(top), right: clamp(left + width), bottom: clamp(top + height) };
  }

  function ordered() {
    return shapes
      .map((shape, index) => ({ shape, index }))
      .sort((a, b) => num(a.shape.z, 0) - num(b.shape.z, 0) || a.index - b.index)
      .map((item) => item.shape);
  }

  function ptToPx(pt, perCm) {
    return (pt / 72) * 2.54 * perCm;
  }

  // --- Dibujo ---------------------------------------------------------------
  const SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value !== null && value !== undefined) el.setAttribute(key, value);
    });
    return el;
  }

  function strokeAttrs(shape, color, perCm) {
    if (!color) return { stroke: "none" };
    const width = ptToPx(num(shape.stroke_width, DEFAULT_STROKE_WIDTH_PT), perCm);
    const pattern = DASH_PATTERNS_PT[shape.stroke_style];
    return {
      stroke: color,
      "stroke-width": width,
      "stroke-dasharray": pattern ? pattern.map((pt) => ptToPx(pt, perCm)).join(" ") : null,
    };
  }

  function render(perCm, widthCm, heightCm, defaultFontPt) {
    lastMetrics = { perCm, widthCm, heightCm, defaultFontPt };
    const label = hooks.label;
    if (!label) return;
    label.querySelector("#shapeLayer")?.remove();
    const layer = document.createElement("div");
    layer.id = "shapeLayer";
    layer.className = "shape-layer";
    const widthPx = widthCm * perCm;
    const heightPx = heightCm * perCm;

    ordered().forEach((shape) => {
      const box = boxPct(shape);
      const el = document.createElement("div");
      el.className = "shape-el";
      el.dataset.shapeId = shape.id;
      el.style.left = box.left + "%";
      el.style.top = box.top + "%";
      const isLine = shape.kind === "line";
      const vertical = isLine && num(shape.height, 0) > 0;
      el.style.width = (isLine && vertical ? 0 : box.right - box.left) + "%";
      el.style.height = (isLine && !vertical ? 0 : box.bottom - box.top) + "%";
      const w = ((box.right - box.left) / 100) * widthPx;
      const h = ((box.bottom - box.top) / 100) * heightPx;

      const svg = svgEl("svg", { class: "shape-svg", width: Math.max(w, 1), height: Math.max(h, 1), overflow: "visible" });
      if (isLine) {
        const color = resolveColor(shape.stroke) || "#000000";
        svg.appendChild(
          svgEl("line", { x1: 0, y1: 0, x2: vertical ? 0 : w, y2: vertical ? h : 0, ...strokeAttrs(shape, color, perCm) }),
        );
        el.classList.add(vertical ? "shape-line-v" : "shape-line-h");
      } else {
        const fill = resolveColor(shape.fill);
        const stroke = resolveColor(shape.stroke);
        const common = { fill: fill || "none", ...strokeAttrs(shape, stroke, perCm) };
        if (shape.kind === "ellipse") {
          svg.appendChild(svgEl("ellipse", { cx: w / 2, cy: h / 2, rx: w / 2, ry: h / 2, ...common }));
        } else {
          const radius = Math.min((num(shape.radius, 0) * perCm) / 10, w / 2, h / 2);
          svg.appendChild(svgEl("rect", { x: 0, y: 0, width: w, height: h, rx: radius, ry: radius, ...common }));
        }
        if (!fill && !stroke) el.classList.add("shape-invisible");
      }
      el.appendChild(svg);
      if (shape.text && shape.kind !== "line") el.appendChild(textEl(shape, perCm, defaultFontPt));
      if (shape.id === selected) el.classList.add("selected");
      el.classList.toggle("locked", Boolean(shape.locked));
      if (shape.id === selected && !shape.locked) el.appendChild(resizeHandle(shape));
      attachPointer(el, shape);
      layer.appendChild(el);
    });
    label.appendChild(layer);
    refreshPanel();
    refreshThermalNote();
  }

  // Texto de una caja: misma alineación, margen y tamaño que shapes_pdf.
  function textEl(shape, perCm, defaultFontPt) {
    const text = shape.text || {};
    const el = document.createElement("div");
    el.className = "shape-text";
    el.style.padding = (num(text.padding, DEFAULT_PADDING_MM) * perCm) / 10 + "px";
    el.style.fontSize = ptToPx(num(text.font_size, defaultFontPt), perCm) + "px";
    el.style.fontWeight = text.bold ? "700" : "400";
    el.style.color = resolveColor(text.color) || "#000000";
    el.style.justifyContent = { center: "center", right: "flex-end" }[text.align] || "flex-start";
    el.style.alignItems = { top: "flex-start", bottom: "flex-end" }[text.valign] || "center";
    const span = document.createElement("span");
    span.textContent = text.value || "";
    el.appendChild(span);
    return el;
  }

  // --- Elegir, mover y cambiar el tamaño ----------------------------------
  function find(id) {
    return shapes.find((shape) => shape.id === id) || null;
  }

  function select(id) {
    selected = id && find(id) ? id : null;
    if (selected) hooks.clearOthers();
    hooks.rerender();
  }

  function attachPointer(el, shape) {
    el.addEventListener("pointerdown", (event) => {
      if (event.target.classList.contains("shape-resize")) return;
      event.stopPropagation();
      if (selected !== shape.id) select(shape.id);
      if (shape.locked) return;
      const labelRect = hooks.label.getBoundingClientRect();
      const start = { x: event.clientX, y: event.clientY, left: num(shape.left, 0), top: num(shape.top, 0) };
      const target = hooks.label.querySelector(`.shape-el[data-shape-id="${CSS.escape(shape.id)}"]`) || el;
      target.setPointerCapture?.(event.pointerId);
      const move = (ev) => {
        let left = start.left + ((ev.clientX - start.x) / labelRect.width) * 100;
        let top = start.top + ((ev.clientY - start.y) / labelRect.height) * 100;
        const moving = hooks.label.querySelector(`.shape-el[data-shape-id="${CSS.escape(shape.id)}"]`);
        if (moving && window.LabelEditorSnap) {
          const snapped = window.LabelEditorSnap.snap(hooks.label, moving, left, top, ev.altKey);
          left = snapped.left;
          top = snapped.top;
        }
        shape.left = round(Math.max(0, Math.min(100, left)));
        shape.top = round(Math.max(0, Math.min(100, top)));
        hooks.rerender();
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        window.LabelEditorSnap?.clearGuides(hooks.label);
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }

  function resizeHandle(shape) {
    const handle = document.createElement("span");
    handle.className = "shape-resize";
    handle.title = "Arrastrá para cambiar el tamaño";
    handle.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      event.preventDefault();
      const labelRect = hooks.label.getBoundingClientRect();
      const start = { x: event.clientX, y: event.clientY, width: num(shape.width, 0), height: num(shape.height, 0) };
      const vertical = shape.kind === "line" && start.height > 0;
      const horizontal = shape.kind === "line" && !vertical;
      const move = (ev) => {
        const dw = ((ev.clientX - start.x) / labelRect.width) * 100;
        const dh = ((ev.clientY - start.y) / labelRect.height) * 100;
        if (!vertical) shape.width = round(Math.max(1, Math.min(100, start.width + dw)));
        if (!horizontal) shape.height = round(Math.max(1, Math.min(100, start.height + dh)));
        hooks.rerender();
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
    return handle;
  }

  function round(value) {
    return Math.round(value * 100) / 100;
  }

  // Flechas: 0,5 % (con Mayúscula, 5 %), igual que los campos. Suprimir borra.
  document.addEventListener("keydown", (event) => {
    if (!selected) return;
    const active = document.activeElement;
    const typing = active && (active.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName));
    if (typing) return;
    const shape = find(selected);
    if (!shape) return;
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      removeSelected();
      return;
    }
    const deltas = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const delta = deltas[event.key];
    if (!delta || shape.locked) return;
    event.preventDefault();
    const step = event.shiftKey ? 5 : 0.5;
    shape.left = round(Math.max(0, Math.min(100, num(shape.left, 0) + delta[0] * step)));
    shape.top = round(Math.max(0, Math.min(100, num(shape.top, 0) + delta[1] * step)));
    hooks.rerender();
  });

  // --- Altas, capas y baja --------------------------------------------------
  function newId(kind) {
    const base = { rect: "rect", ellipse: "circulo", line: "linea" }[kind] || "forma";
    let n = 1;
    while (find(`${base}${n}`)) n += 1;
    return `${base}${n}`;
  }

  function maxZ() {
    return shapes.reduce((max, shape) => Math.max(max, num(shape.z, 0)), 0);
  }

  const PRESETS = {
    rect: () => ({ kind: "rect", left: 10, top: 10, width: 40, height: 12, fill: "#e5e7eb" }),
    ellipse: () => {
      // Un círculo de verdad: el rótulo no es cuadrado, así que el alto en %
      // se ajusta a la proporción.
      const ratio = lastMetrics ? lastMetrics.widthCm / lastMetrics.heightCm : 2 / 3;
      return { kind: "ellipse", left: 40, top: 10, width: 20, height: round(20 * ratio), fill: "#000000" };
    },
    lineH: () => ({ kind: "line", left: 4, top: 50, width: 92, stroke: "#000000", stroke_width: 1 }),
    lineV: () => ({ kind: "line", left: 50, top: 20, height: 30, stroke: "#000000", stroke_width: 1 }),
    box: () => ({
      kind: "rect", left: 10, top: 40, width: 50, height: 10, stroke: "#000000", stroke_width: 1,
      text: { value: "{{cp}}", align: "center", valign: "middle", bold: true },
    }),
  };

  function add(preset) {
    const shape = PRESETS[preset]();
    shape.id = newId(shape.kind);
    shape.z = maxZ() + 1;
    shapes.push(shape);
    select(shape.id);
  }

  function removeSelected() {
    shapes = shapes.filter((shape) => shape.id !== selected);
    selected = null;
    hooks.rerender();
  }

  function toFront() {
    const shape = find(selected);
    if (shape) shape.z = maxZ() + 1;
    hooks.rerender();
  }

  function toBack() {
    const shape = find(selected);
    if (!shape) return;
    shape.z = shapes.reduce((min, other) => Math.min(min, num(other.z, 0)), 0) - 1;
    hooks.rerender();
  }

  // --- Panel "Forma seleccionada" ----------------------------------------
  // Agrupado y progresivo: lo que no aplica a la forma elegida no se muestra.
  function colorPicker(id, labelText, allowNone) {
    const wrap = document.createElement("div");
    wrap.className = "shape-color";
    wrap.innerHTML = `<span class="shape-color-label">${labelText}</span>
      <div class="shape-swatches" id="${id}"></div>`;
    const box = wrap.querySelector(".shape-swatches");
    const options = [];
    if (allowNone) options.push({ value: null, title: "Sin color", cls: "none" });
    options.push({ value: "primary", title: "Color principal del diseño", cls: "palette" });
    options.push({ value: "accent", title: "Color de acento del diseño", cls: "palette" });
    SWATCHES.forEach((hex) => options.push({ value: hex, title: hex }));
    options.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "shape-swatch" + (option.cls ? ` shape-swatch-${option.cls}` : "");
      button.title = option.title;
      button.dataset.value = option.value === null ? "" : option.value;
      button.setAttribute("aria-label", option.title);
      if (option.value === "primary") button.textContent = "P";
      if (option.value === "accent") button.textContent = "A";
      if (!option.cls) button.style.background = option.value;
      box.appendChild(button);
    });
    const custom = document.createElement("input");
    custom.type = "color";
    custom.className = "shape-swatch-custom";
    custom.title = "Otro color";
    box.appendChild(custom);
    return wrap;
  }

  function buildPanel() {
    const group = document.getElementById("shapePropsGroup");
    if (!group || group.dataset.built) return;
    group.dataset.built = "1";
    group.innerHTML = `
      <span class="code-config-label">Seleccionado: <strong id="shapePropsTitle"></strong></span>
      <div class="shape-geometry shape-wide">
        <label>Izquierda % <input type="number" id="shapeLeft" min="0" max="100" step="0.5" /></label>
        <label>Arriba % <input type="number" id="shapeTop" min="0" max="100" step="0.5" /></label>
        <label id="shapeWidthLabel">Ancho % <input type="number" id="shapeWidth" min="0" max="100" step="0.5" /></label>
        <label id="shapeHeightLabel">Alto % <input type="number" id="shapeHeight" min="0" max="100" step="0.5" /></label>
      </div>
      <div class="shape-section" id="shapeFillSection"></div>
      <div class="shape-section" id="shapeStrokeSection"></div>
      <label id="shapeStrokeWidthLabel">Grosor del borde (pt)
        <input type="number" id="shapeStrokeWidth" min="0" max="6" step="0.1" placeholder="0,6" /></label>
      <label id="shapeStrokeStyleLabel">Trazo
        <select id="shapeStrokeStyle">
          <option value="">Liso</option><option value="dashed">Discontinuo</option><option value="dotted">Punteado</option>
        </select></label>
      <label id="shapeRadiusLabel">Esquinas (mm)
        <input type="number" id="shapeRadius" min="0" max="20" step="0.5" placeholder="0" /></label>
      <label class="shape-wide" id="shapeHasTextLabel"><input type="checkbox" id="shapeHasText" /> Con texto adentro</label>
      <div class="shape-text-fields" id="shapeTextFields">
        <label class="shape-wide">Texto o dato
          <input type="text" id="shapeTextValue" placeholder="Ej.: CP {{cp}}" /></label>
        <label class="shape-wide">Insertar dato <select id="shapeMarkerSelect"></select></label>
        <label>Tamaño (pt) <input type="number" id="shapeFontSize" min="5" max="72" step="0.5" placeholder="auto" /></label>
        <label>Margen (mm) <input type="number" id="shapePadding" min="0" max="20" step="0.5" placeholder="1" /></label>
        <label>Horizontal
          <select id="shapeAlign"><option value="left">Izquierda</option><option value="center">Centro</option><option value="right">Derecha</option></select></label>
        <label>Vertical
          <select id="shapeValign"><option value="top">Arriba</option><option value="middle">Centro</option><option value="bottom">Abajo</option></select></label>
        <label><input type="checkbox" id="shapeBold" /> Negrita</label>
        <label><input type="checkbox" id="shapeHideEmpty" /> Ocultar si queda vacío</label>
        <div class="shape-section shape-wide" id="shapeTextColorSection"></div>
      </div>
      <div class="shape-actions shape-wide">
        <button type="button" class="btn btn-outline" id="shapeToFront">Traer al frente</button>
        <button type="button" class="btn btn-outline" id="shapeToBack">Enviar atrás</button>
      </div>
      <label class="shape-wide"><input type="checkbox" id="shapeLocked" /> Bloquear posición</label>
      <button type="button" class="btn-danger-text shape-wide" id="shapeDelete">Eliminar forma</button>`;

    group.querySelector("#shapeFillSection").appendChild(colorPicker("shapeFill", "Relleno", true));
    group.querySelector("#shapeStrokeSection").appendChild(colorPicker("shapeStroke", "Borde", true));
    group.querySelector("#shapeTextColorSection").appendChild(colorPicker("shapeTextColor", "Color del texto", false));
    window.LabelEditorMarkers?.fillMarkerSelect(group.querySelector("#shapeMarkerSelect"));

    const on = (id, type, fn) => group.querySelector("#" + id).addEventListener(type, fn);
    const current = () => find(selected);
    const setOrDelete = (obj, key, value) => {
      if (value === "" || value === null || value === undefined || Number.isNaN(value)) delete obj[key];
      else obj[key] = value;
    };

    function wireColor(id, apply) {
      const box = group.querySelector("#" + id);
      box.addEventListener("click", (event) => {
        const button = event.target.closest(".shape-swatch");
        if (!button || !current()) return;
        apply(current(), button.dataset.value || null);
        hooks.rerender();
      });
      box.querySelector(".shape-swatch-custom").addEventListener("input", (event) => {
        if (!current()) return;
        apply(current(), event.target.value);
        hooks.rerender();
      });
    }
    wireColor("shapeFill", (shape, value) => setOrDelete(shape, "fill", value));
    wireColor("shapeStroke", (shape, value) => setOrDelete(shape, "stroke", value));
    wireColor("shapeTextColor", (shape, value) => {
      if (shape.text) setOrDelete(shape.text, "color", value);
    });

    ["Left", "Top", "Width", "Height"].forEach((name) => {
      on("shape" + name, "input", (e) => {
        const value = parseFloat(e.target.value);
        if (!Number.isFinite(value)) return;
        current()[name.toLowerCase()] = Math.max(0, Math.min(100, value));
        hooks.rerender();
      });
    });
    on("shapeStrokeWidth", "input", (e) => {
      setOrDelete(current(), "stroke_width", parseFloat(e.target.value));
      hooks.rerender();
    });
    on("shapeStrokeStyle", "change", (e) => {
      setOrDelete(current(), "stroke_style", e.target.value);
      hooks.rerender();
    });
    on("shapeRadius", "input", (e) => {
      setOrDelete(current(), "radius", parseFloat(e.target.value));
      hooks.rerender();
    });
    on("shapeHasText", "change", (e) => {
      const shape = current();
      if (e.target.checked) shape.text = shape.text || { value: "", align: "center", valign: "middle" };
      else delete shape.text;
      hooks.rerender();
    });
    on("shapeTextValue", "input", (e) => {
      current().text.value = e.target.value;
      hooks.rerender();
    });
    on("shapeMarkerSelect", "change", (e) => {
      if (!e.target.value) return;
      const input = group.querySelector("#shapeTextValue");
      window.LabelEditorMarkers.insertIntoInput(input, e.target.value);
      e.target.value = "";
    });
    on("shapeFontSize", "input", (e) => {
      setOrDelete(current().text, "font_size", parseFloat(e.target.value));
      hooks.rerender();
    });
    on("shapePadding", "input", (e) => {
      setOrDelete(current().text, "padding", parseFloat(e.target.value));
      hooks.rerender();
    });
    on("shapeAlign", "change", (e) => {
      current().text.align = e.target.value;
      hooks.rerender();
    });
    on("shapeValign", "change", (e) => {
      current().text.valign = e.target.value;
      hooks.rerender();
    });
    on("shapeBold", "change", (e) => {
      setOrDelete(current().text, "bold", e.target.checked || null);
      hooks.rerender();
    });
    on("shapeHideEmpty", "change", (e) => {
      setOrDelete(current().text, "hide_if_empty", e.target.checked || null);
      hooks.rerender();
    });
    on("shapeLocked", "change", (e) => {
      setOrDelete(current(), "locked", e.target.checked || null);
      hooks.rerender();
    });
    on("shapeToFront", "click", toFront);
    on("shapeToBack", "click", toBack);
    on("shapeDelete", "click", removeSelected);
  }

  function markSwatches(id, value) {
    const box = document.getElementById(id);
    if (!box) return;
    // "P" y "A" muestran el color que tienen hoy en la paleta.
    box.querySelectorAll(".shape-swatch-palette").forEach((button) => {
      const key = button.dataset.value;
      button.style.background = palette[key] || PALETTE_DEFAULTS[key];
    });
    box.querySelectorAll(".shape-swatch").forEach((button) => {
      button.classList.toggle("active", (button.dataset.value || null) === (value || null));
    });
    const custom = box.querySelector(".shape-swatch-custom");
    const hex = resolveColor(value);
    if (hex) custom.value = hex;
  }

  function refreshPanel() {
    const group = document.getElementById("shapePropsGroup");
    if (!group) return;
    const shape = find(selected);
    group.hidden = !shape;
    if (!shape) return;
    buildPanel();
    const isLine = shape.kind === "line";
    const show = (id, visible) => {
      const el = document.getElementById(id);
      if (el) el.hidden = !visible;
    };
    document.getElementById("shapePropsTitle").textContent =
      shape.text ? "Caja con texto" : (isLine ? (num(shape.height, 0) > 0 ? "Línea vertical" : "Línea horizontal") : KIND_LABELS[shape.kind]);
    show("shapeFillSection", !isLine);
    show("shapeRadiusLabel", shape.kind === "rect");
    show("shapeHasTextLabel", !isLine);
    show("shapeTextFields", Boolean(shape.text) && !isLine);
    markSwatches("shapeFill", shape.fill);
    markSwatches("shapeStroke", shape.stroke);
    const setValue = (id, value) => {
      const input = document.getElementById(id);
      if (input && document.activeElement !== input) input.value = value ?? "";
    };
    setValue("shapeLeft", shape.left);
    setValue("shapeTop", shape.top);
    setValue("shapeWidth", shape.width);
    setValue("shapeHeight", shape.height);
    // Una línea solo tiene largo: horizontal, el ancho; vertical, el alto.
    show("shapeWidthLabel", !isLine || num(shape.width, 0) > 0);
    show("shapeHeightLabel", !isLine || num(shape.height, 0) > 0);
    setValue("shapeStrokeWidth", shape.stroke_width);
    setValue("shapeStrokeStyle", shape.stroke_style || "");
    setValue("shapeRadius", shape.radius);
    document.getElementById("shapeHasText").checked = Boolean(shape.text);
    document.getElementById("shapeLocked").checked = Boolean(shape.locked);
    if (shape.text) {
      setValue("shapeTextValue", shape.text.value);
      setValue("shapeFontSize", shape.text.font_size);
      setValue("shapePadding", shape.text.padding);
      setValue("shapeAlign", shape.text.align || "left");
      setValue("shapeValign", shape.text.valign || "middle");
      document.getElementById("shapeBold").checked = Boolean(shape.text.bold);
      document.getElementById("shapeHideEmpty").checked = Boolean(shape.text.hide_if_empty);
      markSwatches("shapeTextColor", shape.text.color || "#000000");
    }
  }

  // --- Paleta del diseño y aviso de térmica -------------------------------
  function initPalettePanel() {
    ["primary", "accent"].forEach((key) => {
      const input = document.getElementById(key === "primary" ? "palettePrimary" : "paletteAccent");
      if (!input) return;
      input.addEventListener("input", () => {
        palette[key] = input.value;
        hooks.rerender();
      });
    });
    document.querySelectorAll("[data-add-shape]").forEach((button) => {
      button.addEventListener("click", () => add(button.dataset.addShape));
    });
  }

  function syncPaletteInputs() {
    const primary = document.getElementById("palettePrimary");
    const accent = document.getElementById("paletteAccent");
    if (primary) primary.value = palette.primary || PALETTE_DEFAULTS.primary;
    if (accent) accent.value = palette.accent || PALETTE_DEFAULTS.accent;
  }

  function refreshThermalNote() {
    const note = document.getElementById("thermalColorNote");
    if (note) note.hidden = !usesColor();
  }

  // --- Entrada / salida -----------------------------------------------------
  function init(options) {
    hooks = { ...hooks, ...options };
    initPalettePanel();
  }

  function load(design) {
    const source = design && typeof design === "object" ? design : {};
    shapes = Array.isArray(source.shapes) ? JSON.parse(JSON.stringify(source.shapes)) : [];
    palette = source.palette && typeof source.palette === "object" ? { ...source.palette } : {};
    selected = null;
    syncPaletteInputs();
  }

  // Lo que va al diseño. La paleta solo si algo la usa o el usuario la
  // eligió; sin formas ni paleta, ninguna clave (un diseño viejo sigue igual).
  function collect() {
    const out = {};
    if (shapes.length) out.shapes = JSON.parse(JSON.stringify(shapes));
    const refs = JSON.stringify(shapes);
    const used = {};
    ["primary", "accent"].forEach((key) => {
      if (palette[key] || refs.includes(`"${key}"`)) used[key] = palette[key] || PALETTE_DEFAULTS[key];
    });
    if (Object.keys(used).length) out.palette = used;
    return out;
  }

  // Para las reglas ("según los datos del envío"): cada forma es un blanco.
  function ruleTargets() {
    return shapes.map((shape) => [
      `shape:${shape.id}`,
      `${shape.text ? "Caja" : KIND_LABELS[shape.kind]} «${shape.text?.value || shape.id}»`,
    ]);
  }

  window.LabelEditorShapes = {
    init,
    load,
    collect,
    render,
    select,
    selectedId: () => selected,
    ruleTargets,
    usesColor,
  };
})();
