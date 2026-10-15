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
  const savedRanges = new WeakMap();

  document.addEventListener("selectionchange", () => {
    const selection = document.getSelection();
    if (!selection || !selection.rangeCount) return;
    const node = selection.anchorNode;
    const field = node && (node.nodeType === 1 ? node : node.parentElement)?.closest?.(".field[contenteditable]");
    if (field) savedRanges.set(field, selection.getRangeAt(0).cloneRange());
  });

  // Inserta {{key}} donde estaba el cursor en ese campo, o al final si nunca
  // se escribió en él.
  function insertIntoEditable(el, key) {
    const snippet = `{{${key}}}`;
    const range = savedRanges.get(el);
    if (range && el.contains(range.startContainer)) {
      range.deleteContents();
      const node = document.createTextNode(snippet);
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
      savedRanges.set(el, range);
    } else {
      el.textContent = (el.textContent || "") + snippet;
    }
    // Quedan nodos de texto partidos: se juntan para que textContent (lo que
    // se guarda) sea un solo texto limpio.
    el.normalize();
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
  };
})();
