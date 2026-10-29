// Deshacer / rehacer del editor de rótulos (fase 4).
//
// Guarda instantáneas del diseño entero (lo mismo que se guardaría) en vez de
// un "comando" por operación: hay decenas de maneras de cambiar el diseño
// (arrastrar, el panel, las reglas, la paleta...) y todas terminan en el mismo
// collectFields(). Se graba solo cuando la instantánea cambió, al terminar
// cada gesto; quien llama decide cuándo (record / scheduleRecord).
(function () {
  const MAX_STEPS = 100;

  let capture = () => "";
  let restore = () => {};
  let onChange = () => {};
  let past = [];
  let future = [];
  let current = null;
  let restoring = false;
  let timer = null;

  function notify() {
    try {
      onChange({ canUndo: past.length > 0, canRedo: future.length > 0 });
    } catch (error) {
      console.error(error);
    }
  }

  function snapshot() {
    try {
      return capture();
    } catch (error) {
      console.error(error);
      return current;
    }
  }

  // La instantánea actual pasa a ser la base: al abrir otro rótulo o
  // plantilla no se puede "deshacer" hasta el anterior.
  function reset() {
    clearTimeout(timer);
    past = [];
    future = [];
    current = snapshot();
    notify();
  }

  function record() {
    clearTimeout(timer);
    if (restoring) return;
    const next = snapshot();
    if (next == null || next === current) return;
    if (current != null) past.push(current);
    if (past.length > MAX_STEPS) past.shift();
    future = [];
    current = next;
    notify();
  }

  // Para lo que llega de a poco (escribir en un campo del panel, flechas):
  // un solo paso por ráfaga.
  function scheduleRecord(ms = 500) {
    clearTimeout(timer);
    timer = setTimeout(record, ms);
  }

  function apply(state) {
    restoring = true;
    try {
      restore(state);
    } finally {
      restoring = false;
    }
    current = state;
    notify();
  }

  function undo() {
    record(); // lo que quedaba sin grabar (una ráfaga a medias) es un paso
    if (!past.length) return false;
    future.push(current);
    apply(past.pop());
    return true;
  }

  function redo() {
    clearTimeout(timer);
    if (!future.length) return false;
    past.push(current);
    apply(future.pop());
    return true;
  }

  // ¿El foco está donde se escribe texto? Ahí Ctrl+Z, Supr y las flechas
  // son del navegador. Una casilla, un select o un selector de color no tienen
  // deshacer propio: esos no cuentan.
  const TEXT_INPUT_TYPES = new Set(["text", "number", "search", "email", "url", "tel", "password", ""]);
  function isTypingTarget(el) {
    if (!el) return false;
    if (el.isContentEditable || el.tagName === "TEXTAREA") return true;
    return el.tagName === "INPUT" && TEXT_INPUT_TYPES.has((el.getAttribute("type") || "").toLowerCase());
  }

  function init(options) {
    capture = options.capture || capture;
    restore = options.restore || restore;
    onChange = options.onChange || onChange;
  }

  window.LabelEditorHistory = {
    init,
    reset,
    record,
    scheduleRecord,
    undo,
    redo,
    canUndo: () => past.length > 0,
    canRedo: () => future.length > 0,
    isRestoring: () => restoring,
    isTypingTarget,
  };
})();
