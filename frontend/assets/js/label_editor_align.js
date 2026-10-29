// Selección múltiple, alinear y repartir (fase 4).
//
// Shift+clic suma o saca un elemento: campos de datos, textos fijos, líneas y
// formas. Las cajas se leen del DOM, relativas al rótulo (en %), así todos los
// tipos se alinean igual; cada tipo se mueve con su adaptador (los campos en
// su style, las decoraciones y las formas en el diseño). Un elemento
// bloqueado no se mueve y, si hay uno, es la referencia.
(function () {
  let label = null;
  let adapters = {};
  let onMoved = () => {};
  let clearSingle = () => {};
  let currentSingle = () => null;
  let items = [];

  const keyOf = (item) => `${item.type}:${item.key}`;

  function itemFromTarget(target) {
    const field = target.closest(".field[data-field]");
    if (field) return { type: "field", key: field.dataset.field };
    const line = target.closest(".deco-line[data-deco-index]");
    if (line) return { type: "line", key: Number(line.dataset.decoIndex) };
    const text = target.closest(".deco-text[data-deco-index]");
    if (text) return { type: "text", key: Number(text.dataset.decoIndex) };
    const shape = target.closest(".shape-el[data-shape-id]");
    if (shape) return { type: "shape", key: shape.dataset.shapeId };
    return null;
  }

  function boxOf(item) {
    const el = adapters[item.type]?.element(item.key);
    if (!el || !label) return null;
    const outer = label.getBoundingClientRect();
    let rect = el.getBoundingClientRect();
    // Un campo de texto sin ancho propio llega hasta el borde derecho: para
    // alinear cuenta el texto que se ve, no esa caja (si no, "a la derecha"
    // nunca lo movía y repartir superponía).
    if (item.type === "field" && el.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(el);
      const text = range.getBoundingClientRect();
      if (text.width > 0) rect = { left: rect.left, top: rect.top, width: text.right - rect.left, height: rect.height };
    }
    if (!outer.width || !outer.height) return null;
    const left = ((rect.left - outer.left) / outer.width) * 100;
    const top = ((rect.top - outer.top) / outer.height) * 100;
    const width = (rect.width / outer.width) * 100;
    const height = (rect.height / outer.height) * 100;
    return { left, top, right: left + width, bottom: top + height, width, height };
  }

  function isLocked(item) {
    return Boolean(adapters[item.type]?.locked(item.key));
  }

  function paint() {
    if (!label) return;
    label.querySelectorAll(".multi-selected").forEach((el) => el.classList.remove("multi-selected"));
    // Un índice de decoración que ya no existe (se borró) sale de la lista.
    items = items.filter((item) => adapters[item.type]?.element(item.key));
    items.forEach((item) => adapters[item.type].element(item.key).classList.add("multi-selected"));
    const group = document.getElementById("alignGroup");
    const hint = document.getElementById("alignHint");
    if (group) group.hidden = items.length < 2;
    if (hint) hint.hidden = items.length >= 2;
    const count = document.getElementById("alignCount");
    if (count) count.textContent = items.length >= 2 ? `${items.length} elegidos` : "";
    document.querySelectorAll("[data-align$='distribute']").forEach((button) => {
      button.disabled = items.length < 3;
    });
  }

  function toggle(item) {
    // El primer Shift+clic suma también lo que ya estaba elegido solo.
    if (!items.length) {
      const single = currentSingle();
      if (single && keyOf(single) !== keyOf(item)) items.push(single);
    }
    const index = items.findIndex((other) => keyOf(other) === keyOf(item));
    if (index >= 0) items.splice(index, 1);
    else items.push(item);
    clearSingle();
    paint();
  }

  function clear() {
    if (!items.length) return;
    items = [];
    paint();
  }

  function union(boxes) {
    return {
      left: Math.min(...boxes.map((b) => b.left)),
      top: Math.min(...boxes.map((b) => b.top)),
      right: Math.max(...boxes.map((b) => b.right)),
      bottom: Math.max(...boxes.map((b) => b.bottom)),
    };
  }

  function align(mode) {
    const entries = items.map((item) => ({ item, box: boxOf(item), locked: isLocked(item) })).filter((e) => e.box);
    if (entries.length < 2) return;
    const movable = entries.filter((e) => !e.locked);
    if (!movable.length) return;
    const moves = new Map();

    if (mode === "hdistribute" || mode === "vdistribute") {
      if (entries.length < 3) return;
      const horizontal = mode === "hdistribute";
      const start = horizontal ? "left" : "top";
      const end = horizontal ? "right" : "bottom";
      const size = horizontal ? "width" : "height";
      const sorted = [...entries].sort((a, b) => a.box[start] - b.box[start]);
      const first = sorted[0].box[start];
      const last = Math.max(...sorted.map((e) => e.box[end]));
      const total = sorted.reduce((sum, e) => sum + e.box[size], 0);
      const gap = (last - first - total) / (sorted.length - 1);
      let cursor = first;
      sorted.forEach((entry) => {
        if (!entry.locked) moves.set(entry, horizontal ? [cursor - entry.box.left, 0] : [0, cursor - entry.box.top]);
        cursor += entry.box[size] + gap;
      });
    } else {
      const lockedBoxes = entries.filter((e) => e.locked).map((e) => e.box);
      const ref = lockedBoxes.length ? lockedBoxes[0] : union(entries.map((e) => e.box));
      movable.forEach((entry) => {
        const b = entry.box;
        const d = {
          left: [ref.left - b.left, 0],
          right: [ref.right - b.right, 0],
          hcenter: [(ref.left + ref.right) / 2 - (b.left + b.right) / 2, 0],
          top: [0, ref.top - b.top],
          bottom: [0, ref.bottom - b.bottom],
          vcenter: [0, (ref.top + ref.bottom) / 2 - (b.top + b.bottom) / 2],
        }[mode];
        if (d) moves.set(entry, d);
      });
    }

    moves.forEach(([dx, dy], entry) => {
      if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) return;
      adapters[entry.item.type].move(entry.item.key, dx, dy);
    });
    onMoved();
  }

  function init(options) {
    label = options.label;
    adapters = options.adapters || {};
    onMoved = options.onMoved || onMoved;
    clearSingle = options.clearSingle || clearSingle;
    currentSingle = options.currentSingle || currentSingle;

    // Captura: corre antes que el pointerdown de cada elemento (que elige uno
    // solo y empieza a arrastrar).
    label.addEventListener(
      "pointerdown",
      (event) => {
        // Un clic sin Shift vuelve a la selección de uno solo.
        if (!event.shiftKey) {
          clear();
          return;
        }
        const item = itemFromTarget(event.target);
        if (!item) return;
        event.preventDefault();
        event.stopPropagation();
        toggle(item);
        // Un texto no queda en modo escritura con Shift+clic.
        if (document.activeElement && label.contains(document.activeElement)) document.activeElement.blur();
      },
      true,
    );
    // El click que sigue al Shift+clic tampoco elige el elemento solo (los
    // campos abren su panel en "click").
    label.addEventListener(
      "click",
      (event) => {
        if (event.shiftKey && itemFromTarget(event.target)) {
          event.preventDefault();
          event.stopPropagation();
        }
      },
      true,
    );
    // Shift+clic pone el foco en el texto en "focus", después del pointerdown.
    label.addEventListener(
      "focusin",
      (event) => {
        if (items.length && event.target.closest?.(".field")) event.target.blur();
      },
      true,
    );

    document.querySelectorAll("[data-align]").forEach((button) => {
      button.addEventListener("click", () => align(button.dataset.align));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") clear();
    });
    paint();
  }

  window.LabelEditorAlign = { init, paint, toggle, clear, items: () => items.slice(), align };
})();
