// ---------------------------------------------------------------------------
// Editor de rótulos: imán y guías al arrastrar un campo.
//
// Mientras se arrastra, el borde izquierdo (y en las imágenes también el
// centro y el borde derecho) se pega a los de los otros campos y al centro
// del rótulo si pasa a menos de SNAP_PX píxeles, y se dibuja la guía. Lo
// mismo en vertical con los bordes superiores (en imágenes, también centro e
// inferior) y las líneas separadoras. Con Alt apretado no hay imán.
//
// Los textos solo aportan su borde izquierdo y su borde superior: su caja
// llega hasta el borde derecho del rótulo (así los mide el render), así que
// su "derecha" y su "centro" no son algo que se vea.
//
// Expone window.LabelEditorSnap = { snap, clearGuides }.
// ---------------------------------------------------------------------------
(function () {
  const SNAP_PX = 6;

  // Caja de un elemento en % del rótulo. La posición sale del estilo (el %
  // exacto que se guarda) cuando lo tiene: medirla en píxeles mete
  // redondeos y el imán dejaba un campo en 5,998 % en vez de 6 %.
  function box(el, labelRect) {
    const rect = el.getBoundingClientRect();
    const styleLeft = parseFloat(el.style.left);
    const styleTop = parseFloat(el.style.top);
    return {
      left: el.style.left.endsWith("%") && Number.isFinite(styleLeft)
        ? styleLeft
        : ((rect.left - labelRect.left) / labelRect.width) * 100,
      top: el.style.top.endsWith("%") && Number.isFinite(styleTop)
        ? styleTop
        : ((rect.top - labelRect.top) / labelRect.height) * 100,
      width: (rect.width / labelRect.width) * 100,
      height: (rect.height / labelRect.height) * 100,
    };
  }

  function isText(el) {
    return el.getAttribute("contenteditable") === "true";
  }

  // Posiciones (en %) contra las que se puede alinear.
  function targets(label, moving, labelRect) {
    const xs = [50];
    const ys = [50];
    label.querySelectorAll(".field").forEach((el) => {
      if (el === moving || el.offsetParent === null) return;
      const b = box(el, labelRect);
      xs.push(b.left);
      ys.push(b.top);
      if (!isText(el)) {
        xs.push(b.left + b.width / 2, b.left + b.width);
        ys.push(b.top + b.height / 2, b.top + b.height);
      }
    });
    label.querySelectorAll(".deco-line").forEach((el) => {
      ys.push(box(el, labelRect).top);
    });
    // Formas (label_editor_shapes.js): todos sus bordes y su centro.
    label.querySelectorAll(".shape-el").forEach((el) => {
      if (el === moving) return;
      const b = box(el, labelRect);
      xs.push(b.left, b.left + b.width / 2, b.left + b.width);
      ys.push(b.top, b.top + b.height / 2, b.top + b.height);
    });
    label.querySelectorAll(".deco-text").forEach((el) => {
      const b = box(el, labelRect);
      xs.push(b.left);
      ys.push(b.top);
    });
    return { xs, ys };
  }

  // Busca el ajuste más chico que alinea alguno de los `edges` (offsets
  // dentro del elemento) con alguno de los `lines`.
  function nearest(position, edges, lines, thresholdPct) {
    let best = null;
    edges.forEach((offset) => {
      lines.forEach((line) => {
        const delta = line - (position + offset);
        if (Math.abs(delta) <= thresholdPct && (!best || Math.abs(delta) < Math.abs(best.delta))) {
          best = { delta, line };
        }
      });
    });
    return best;
  }

  // Devuelve la posición ajustada {left, top} (en %) y las guías a dibujar.
  function snap(label, moving, left, top, disabled) {
    if (disabled) return { left, top, guides: [] };
    const labelRect = label.getBoundingClientRect();
    const size = box(moving, labelRect);
    const { xs, ys } = targets(label, moving, labelRect);
    const text = isText(moving);
    const xEdges = text ? [0] : [0, size.width / 2, size.width];
    const yEdges = text ? [0] : [0, size.height / 2, size.height];
    const guides = [];

    const bestX = nearest(left, xEdges, xs, (SNAP_PX / labelRect.width) * 100);
    if (bestX) {
      left += bestX.delta;
      guides.push({ axis: "x", at: bestX.line });
    }
    const bestY = nearest(top, yEdges, ys, (SNAP_PX / labelRect.height) * 100);
    if (bestY) {
      top += bestY.delta;
      guides.push({ axis: "y", at: bestY.line });
    }
    drawGuides(label, guides);
    return { left, top, guides };
  }

  function drawGuides(label, guides) {
    clearGuides(label);
    guides.forEach(({ axis, at }) => {
      const guide = document.createElement("div");
      guide.className = `snap-guide snap-guide-${axis}`;
      if (axis === "x") guide.style.left = at + "%";
      else guide.style.top = at + "%";
      label.appendChild(guide);
    });
  }

  function clearGuides(label) {
    label.querySelectorAll(".snap-guide").forEach((el) => el.remove());
  }

  window.LabelEditorSnap = { snap, clearGuides };
})();
