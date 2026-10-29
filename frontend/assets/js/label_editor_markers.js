// ---------------------------------------------------------------------------
// Editor de rótulos: datos que se pueden insertar en un texto ({{...}}) y la
// numeración de {{secuencia}}.
//
// Los marcadores son los del backend: los datos del envío salen de
// apps/labels/label_rendering.build_label_context y los calculados de
// build_computed_context. Si allá se agrega uno, se agrega acá.
//
// Lo usa editor_rotulos.html (script clásico, expone window.LabelEditorMarkers).
// Necesita auth.js (window.Auth) y config.js.
// ---------------------------------------------------------------------------
(function () {
  const SHIPMENT_MARKERS = [
    ["remitente", "Remitente"],
    ["remitente_domicilio", "Domicilio del remitente"],
    ["remitente_telefono", "Teléfono del remitente"],
    ["destinatario", "Destinatario"],
    ["domicilio", "Domicilio"],
    ["referencia", "Referencia del domicilio"],
    ["localidad", "Localidad/Provincia"],
    ["cp", "CP"],
    ["pais", "País"],
    ["pedido", "N° de pedido"],
    ["fecha_pedido", "Fecha del pedido"],
    ["envio", "Forma de envío"],
    ["tracking", "Código de seguimiento"],
    ["tracking_url", "URL de seguimiento"],
    ["nombre", "Nombre de pila"],
    ["peso", "Peso"],
    ["bultos_pedido", "Bultos del pedido"],
    ["transportista", "Transportista"],
  ];

  // Se calculan al imprimir, no salen del pedido.
  const COMPUTED_MARKERS = [
    ["fecha", "Fecha de impresión (dd/mm/aaaa)"],
    ["hora", "Hora de impresión"],
    ["fecha_hora", "Fecha y hora de impresión"],
    ["secuencia", "Número correlativo"],
    ["bulto", "N° de bulto"],
    ["bultos", "Total de bultos"],
    ["bulto_de_bultos", "Bulto \"1 de 3\""],
  ];

  const ALL_MARKERS = [...SHIPMENT_MARKERS, ...COMPUTED_MARKERS];

  function markerLabel(key) {
    const found = ALL_MARKERS.find(([value]) => value === key);
    return found ? found[1] : key;
  }

  // Llena un <select> con los marcadores agrupados, más una primera opción
  // vacía ("— elegir —") para que elegir sea siempre un cambio.
  function fillMarkerSelect(select, placeholder = "— elegir —") {
    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = placeholder;
    select.appendChild(empty);
    [
      ["Datos del envío", SHIPMENT_MARKERS],
      ["Calculados al imprimir", COMPUTED_MARKERS],
    ].forEach(([title, markers]) => {
      const group = document.createElement("optgroup");
      group.label = title;
      markers.forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        group.appendChild(option);
      });
      select.appendChild(group);
    });
  }

  // --- Insertar en un campo contenteditable ------------------------------
  // Al abrir el select el campo pierde el foco y con él el cursor, así que
  // se guarda la última posición del cursor DENTRO de cada campo.
  // Se guarda el desplazamiento en caracteres del texto real (con marcadores),
  // no el rango: con los datos de ejemplo el campo se redibuja al perder el
  // foco y los nodos del rango quedan sueltos.
  const savedOffsets = new WeakMap();

  document.addEventListener("selectionchange", () => {
    const selection = document.getSelection();
    if (!selection || !selection.rangeCount) return;
    const node = selection.anchorNode;
    const field = node && (node.nodeType === 1 ? node : node.parentElement)?.closest?.(".field[contenteditable]");
    if (field) {
      // Ambos extremos del rango (en caracteres): así una selección se
      // reemplaza y una hecha hacia atrás no se toma por su ancla.
      const range = selection.getRangeAt(0);
      const measure = (container, offset) => {
        const probe = document.createRange();
        probe.selectNodeContents(field);
        probe.setEnd(container, offset);
        return probe.toString().length;
      };
      if (!field.contains(range.startContainer) || !field.contains(range.endContainer)) return;
      savedOffsets.set(field, {
        start: measure(range.startContainer, range.startOffset),
        end: measure(range.endContainer, range.endOffset),
      });
    }
  });

  // Inserta {{key}} donde estaba el cursor en ese campo, o al final si nunca
  // se escribió en él.
  function insertIntoEditable(el, key) {
    // Con los datos de ejemplo puestos el campo muestra valores: se vuelve al
    // texto con marcadores antes de insertar.
    restoreField(el);
    const snippet = `{{${key}}}`;
    const raw = el.textContent || "";
    const saved = savedOffsets.get(el);
    if (saved && saved.end <= raw.length) {
      el.textContent = raw.slice(0, saved.start) + snippet + raw.slice(saved.end);
      const caret = saved.start + snippet.length;
      savedOffsets.set(el, { start: caret, end: caret });
    } else {
      el.textContent = raw + snippet;
    }
    el.normalize();
    if (typeof sampleHooks.onChange === "function") sampleHooks.onChange();
  }

  // --- Ver con datos de ejemplo --------------------------------------------
  // Solo presentación: el lienzo muestra el valor de muestra en lugar de
  // {{marcador}}. El texto real de un campo queda en data-raw mientras se ve
  // la muestra; collectFields y todo lo que edita lo leen con rawText().
  const PREVIEW_CONTEXT_URL = `${window.APP_CONFIG.API_BASE}/labels/preview-context/`;
  const SAMPLES_PREF_KEY = "labelEditorSamples";
  const sampleHooks = { onChange: null };
  let sampleValues = null;
  let samplesOn = false;
  try {
    samplesOn = localStorage.getItem(SAMPLES_PREF_KEY) === "1";
  } catch (err) {
    samplesOn = false;
  }

  let samplesSuspended = false;

  // Corre fn con el lienzo mostrando los marcadores (miniatura, exportar): lo
  // que se guarda o se baja no depende de una preferencia de vista.
  async function withoutSamples(fn, rerender) {
    const wasOn = samplesActive();
    if (wasOn) {
      samplesSuspended = true;
      rerender();
    }
    try {
      return await fn();
    } finally {
      if (wasOn) {
        samplesSuspended = false;
        rerender();
      }
    }
  }

  function samplesActive() {
    return samplesOn && !samplesSuspended && Boolean(sampleValues);
  }

  // Cambia {{marcador}} por su valor de muestra; uno sin valor queda como está.
  function sample(text) {
    if (!samplesActive() || !text) return text;
    return String(text).replace(/\{\{\s*(\w+)\s*\}\}/g, (whole, key) =>
      Object.prototype.hasOwnProperty.call(sampleValues, key) && sampleValues[key] !== ""
        ? sampleValues[key]
        : whole,
    );
  }

  function rawText(el) {
    return el.dataset.raw !== undefined ? el.dataset.raw : el.textContent;
  }

  function restoreField(el) {
    if (el.dataset.raw === undefined) return;
    el.textContent = el.dataset.raw;
    delete el.dataset.raw;
  }

  // Cambia el texto de un campo (carga de diseño, autocompletado).
  function setFieldText(el, text) {
    delete el.dataset.raw;
    savedOffsets.delete(el);
    el.textContent = text;
  }

  // Pone o saca la muestra en los campos de texto; el que se está editando
  // siempre muestra los marcadores.
  function syncFields(fields) {
    fields.forEach((el) => {
      if (document.activeElement === el) {
        restoreField(el);
        return;
      }
      const raw = rawText(el);
      if (samplesActive()) {
        const shown = sample(raw);
        if (shown !== raw) {
          el.dataset.raw = raw;
          el.textContent = shown;
        } else {
          restoreField(el);
        }
      } else {
        restoreField(el);
      }
    });
  }

  async function loadSamples() {
    try {
      const response = await window.Auth.apiFetch(PREVIEW_CONTEXT_URL);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      sampleValues = { ...(data.context || {}), ...(data.computed || {}) };
      return true;
    } catch (err) {
      sampleValues = null;
      return false;
    }
  }

  // Casilla de la barra: la pide una vez al abrir y avisa si no hay muestra.
  async function initSamplesToggle(input, onChange) {
    sampleHooks.onChange = onChange;
    if (!input) return;
    input.checked = samplesOn;
    input.disabled = true;
    const ok = await loadSamples();
    if (!ok) {
      input.checked = false;
      input.title = "No se pudieron cargar los datos de ejemplo.";
      input.closest("label")?.setAttribute("title", input.title);
      return;
    }
    input.disabled = false;
    if (samplesOn) onChange();
    input.addEventListener("change", () => {
      samplesOn = input.checked;
      try {
        localStorage.setItem(SAMPLES_PREF_KEY, samplesOn ? "1" : "0");
      } catch (err) {
        /* sin almacenamiento: vale solo para esta sesión */
      }
      onChange();
    });
  }

  // Inserta {{key}} en un <input> en la posición del cursor.
  function insertIntoInput(input, key) {
    const snippet = `{{${key}}}`;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.value = input.value.slice(0, start) + snippet + input.value.slice(end);
    const caret = start + snippet.length;
    input.setSelectionRange(caret, caret);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // --- Numeración de {{secuencia}} ----------------------------------------
  const SEQUENCE_URL = `${window.APP_CONFIG.API_BASE}/labels/sequence/`;

  function sequenceExample(prefix, padding, next) {
    const number = String(Math.max(1, next || 1)).padStart(Math.max(0, padding || 0), "0");
    return `${prefix || ""}${number}`;
  }

  function initSequencePanel(root) {
    if (!root) return;
    const prefix = root.querySelector("#seqPrefix");
    const padding = root.querySelector("#seqPadding");
    const next = root.querySelector("#seqNext");
    const example = root.querySelector("#seqExample");
    const message = root.querySelector("#seqMessage");
    const saveButton = root.querySelector("#seqSaveBtn");

    function showSeqMessage(text, ok) {
      message.textContent = text;
      message.className = `rules-message ${ok ? "success" : "error"}`;
      message.hidden = false;
    }

    function refreshExample() {
      example.textContent = sequenceExample(
        prefix.value.trim(),
        parseInt(padding.value, 10),
        parseInt(next.value, 10),
      );
    }
    [prefix, padding, next].forEach((input) => input.addEventListener("input", refreshExample));

    let loaded = false;
    async function load() {
      try {
        const response = await window.Auth.apiFetch(SEQUENCE_URL);
        if (!response.ok) throw new Error("No se pudo leer la numeración.");
        const data = await response.json();
        prefix.value = data.prefix || "";
        padding.value = data.padding;
        next.value = data.next;
        refreshExample();
        loaded = true;
      } catch (err) {
        if (err.isSessionExpired) return;
        showSeqMessage(err.message || "No se pudo leer la numeración.", false);
      }
    }

    // Se pide recién cuando el usuario abre la sección: la mayoría de los
    // rótulos no usan {{secuencia}}.
    root.addEventListener("toggle", () => {
      if (root.open && !loaded) load();
    });

    saveButton.addEventListener("click", async () => {
      saveButton.disabled = true;
      message.hidden = true;
      try {
        const response = await window.Auth.apiFetch(SEQUENCE_URL, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prefix: prefix.value.trim(),
            padding: parseInt(padding.value, 10),
            next: parseInt(next.value, 10),
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(
            typeof getErrorMessage === "function"
              ? getErrorMessage(data, "No se pudo guardar la numeración.")
              : "No se pudo guardar la numeración.",
          );
        }
        next.value = data.next;
        refreshExample();
        showSeqMessage(`Guardado. El próximo rótulo impreso va a llevar ${data.example}.`, true);
      } catch (err) {
        if (err.isSessionExpired) return;
        showSeqMessage(err.message, false);
      } finally {
        saveButton.disabled = false;
      }
    });
  }

  window.LabelEditorMarkers = {
    SHIPMENT_MARKERS,
    COMPUTED_MARKERS,
    ALL_MARKERS,
    markerLabel,
    fillMarkerSelect,
    insertIntoEditable,
    insertIntoInput,
    initSequencePanel,
    sample,
    rawText,
    restoreField,
    setFieldText,
    syncFields,
    initSamplesToggle,
    withoutSamples,
  };
})();
