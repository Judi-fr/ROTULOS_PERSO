// ---------------------------------------------------------------------------
// Editor de rótulos: reglas condicionales ("si <dato> <condición>, entonces
// <acción> sobre <campo>"), guardadas en design.rules.
//
// El backend ya las valida (apps/labels/serializers._validate_design_rule) y
// las aplica al imprimir (label_rendering.apply_design_rules); esto es la
// pantalla para armarlas. Se evalúan en orden y, si dos tocan el mismo campo,
// gana la última — por eso se pueden reordenar.
//
// Expone window.LabelEditorRules = { load(rules), collect() }. Necesita
// label_editor_markers.js (lista de datos) cargado antes.
// ---------------------------------------------------------------------------
(function () {
  const MAX_RULES = 50; // MAX_DESIGN_RULES del backend

  const OPS = [
    ["equals", "es igual a"],
    ["not_equals", "no es igual a"],
    ["contains", "contiene"],
    ["not_contains", "no contiene"],
    ["empty", "está vacío"],
    ["not_empty", "no está vacío"],
    ["in", "es uno de (separados por coma)"],
    ["gt", "es mayor que"],
    ["lt", "es menor que"],
  ];
  const OPS_WITHOUT_VALUE = new Set(["empty", "not_empty"]);

  const ACTIONS = [
    ["hide", "ocultar"],
    ["show", "mostrar"],
    ["set_text", "cambiar el texto de"],
    ["move", "mover"],
  ];

  // Lo que una regla puede tocar: los elementos posicionados del diseño
  // (las decoraciones no, el backend lo rechaza).
  const TARGETS = [
    ["remitente", "Remitente"],
    ["destinatario", "Destinatario"],
    ["domicilio", "Domicilio"],
    ["cp", "CP"],
    ["localidad", "Localidad/Provincia"],
    ["pedido", "N° de pedido"],
    ["logo", "Logo"],
    ["qr", "QR"],
    ["barcode", "Código de barras"],
  ];

  let rules = [];
  let list, addButton, countLabel;

  function option(value, text, selected) {
    const el = document.createElement("option");
    el.value = value;
    el.textContent = text;
    if (selected) el.selected = true;
    return el;
  }

  function select(className, options, value) {
    const el = document.createElement("select");
    el.className = className;
    options.forEach(([optValue, text]) => el.appendChild(option(optValue, text, optValue === value)));
    return el;
  }

  function input(className, type, value, placeholder) {
    const el = document.createElement("input");
    el.type = type;
    el.className = className;
    el.value = value == null ? "" : value;
    if (placeholder) el.placeholder = placeholder;
    return el;
  }

  function newRule() {
    return {
      when: { field: "localidad", op: "contains", value: "" },
      then: { action: "hide", target: "qr" },
    };
  }

  function render() {
    list.innerHTML = "";
    if (!rules.length) {
      const empty = document.createElement("p");
      empty.className = "rules-empty";
      empty.textContent = "Todavía no hay reglas. El rótulo se imprime siempre igual.";
      list.appendChild(empty);
    }
    rules.forEach((rule, index) => list.appendChild(renderRule(rule, index)));
    addButton.disabled = rules.length >= MAX_RULES;
    countLabel.textContent = rules.length ? `(${rules.length})` : "";
  }

  function renderRule(rule, index) {
    const row = document.createElement("div");
    row.className = "rule-row";

    const when = rule.when;
    const then = rule.then;

    const ifLabel = document.createElement("span");
    ifLabel.className = "rule-word";
    ifLabel.textContent = "Si";

    const fieldSelect = select("rule-field", window.LabelEditorMarkers.ALL_MARKERS, when.field);
    const opSelect = select("rule-op", OPS, when.op);
    const valueInput = input(
      "rule-value",
      "text",
      Array.isArray(when.value) ? when.value.join(", ") : when.value,
      when.op === "in" ? "CABA, Córdoba" : "valor",
    );
    valueInput.hidden = OPS_WITHOUT_VALUE.has(when.op);

    const thenLabel = document.createElement("span");
    thenLabel.className = "rule-word";
    thenLabel.textContent = "→";

    const actionSelect = select("rule-action", ACTIONS, then.action);
    const targetSelect = select("rule-target", TARGETS, then.target);
    const textInput = input("rule-text", "text", then.value, "texto nuevo (admite {{datos}})");
    textInput.hidden = then.action !== "set_text";
    const leftInput = input("rule-num", "number", then.left, "izq. %");
    const topInput = input("rule-num", "number", then.top, "arriba %");
    [leftInput, topInput].forEach((el) => {
      el.min = "0";
      el.max = "100";
      el.step = "1";
      el.hidden = then.action !== "move";
    });

    fieldSelect.addEventListener("change", () => (when.field = fieldSelect.value));
    opSelect.addEventListener("change", () => {
      when.op = opSelect.value;
      valueInput.hidden = OPS_WITHOUT_VALUE.has(when.op);
      valueInput.placeholder = when.op === "in" ? "CABA, Córdoba" : "valor";
    });
    valueInput.addEventListener("input", () => (when.value = valueInput.value));
    actionSelect.addEventListener("change", () => {
      then.action = actionSelect.value;
      textInput.hidden = then.action !== "set_text";
      leftInput.hidden = topInput.hidden = then.action !== "move";
    });
    targetSelect.addEventListener("change", () => (then.target = targetSelect.value));
    textInput.addEventListener("input", () => (then.value = textInput.value));
    leftInput.addEventListener("input", () => (then.left = leftInput.value));
    topInput.addEventListener("input", () => (then.top = topInput.value));

    const tools = document.createElement("span");
    tools.className = "rule-tools";
    const up = button("↑", "Subir (se evalúa antes)", index === 0, () => move(index, -1));
    const down = button("↓", "Bajar (se evalúa después)", index === rules.length - 1, () => move(index, 1));
    const remove = button("Eliminar", "Eliminar esta regla", false, () => {
      rules.splice(index, 1);
      render();
    });
    tools.append(up, down, remove);

    row.append(ifLabel, fieldSelect, opSelect, valueInput, thenLabel, actionSelect, targetSelect, textInput, leftInput, topInput, tools);
    return row;
  }

  function button(text, title, disabled, onClick) {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = text;
    el.title = title;
    el.disabled = disabled;
    el.addEventListener("click", onClick);
    return el;
  }

  function move(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= rules.length) return;
    [rules[index], rules[target]] = [rules[target], rules[index]];
    render();
  }

  function clampPercent(value) {
    const number = parseFloat(value);
    if (!Number.isFinite(number)) return null;
    return Math.max(0, Math.min(100, number));
  }

  // Lo que se guarda: solo las claves que el backend acepta, con los tipos
  // que espera. Un "mover" sin las dos coordenadas no se guarda (el backend
  // lo rechazaría y el usuario perdería el resto del diseño).
  function collect() {
    const out = [];
    rules.forEach((rule) => {
      const when = { field: rule.when.field, op: rule.when.op };
      if (rule.when.op === "in") {
        when.value = String(rule.when.value || "")
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean);
      } else if (!OPS_WITHOUT_VALUE.has(rule.when.op)) {
        when.value = String(rule.when.value ?? "");
      }
      const then = { action: rule.then.action, target: rule.then.target };
      if (then.action === "set_text") then.value = String(rule.then.value ?? "");
      if (then.action === "move") {
        const left = clampPercent(rule.then.left);
        const top = clampPercent(rule.then.top);
        if (left === null || top === null) return;
        then.left = left;
        then.top = top;
      }
      out.push({ when, then });
    });
    return out;
  }

  function load(saved) {
    rules = (Array.isArray(saved) ? saved : [])
      .filter((rule) => rule && rule.when && rule.then)
      .map((rule) => ({ when: { ...rule.when }, then: { ...rule.then } }));
    if (list) render();
  }

  // Reglas incompletas que collect() va a descartar, para avisar antes de
  // guardar en vez de perderlas en silencio.
  function incompleteCount() {
    return rules.filter(
      (rule) =>
        rule.then.action === "move" &&
        (clampPercent(rule.then.left) === null || clampPercent(rule.then.top) === null),
    ).length;
  }

  function init(root) {
    if (!root) return;
    list = root.querySelector("#rulesList");
    addButton = root.querySelector("#addRuleBtn");
    countLabel = root.querySelector("#rulesCount");
    addButton.addEventListener("click", () => {
      if (rules.length >= MAX_RULES) return;
      rules.push(newRule());
      render();
    });
    render();
  }

  window.LabelEditorRules = { init, load, collect, incompleteCount };
})();
