// Editor de diseños por elementos (editor_layout.html): la pantalla que le
// faltaba a ElementLayout/LayoutElement (apps.labels, "sistema 2").
//
// Una decisión de diseño acá, a propósito: el lienzo que se arrastra NO
// pretende ser la vista definitiva. La vista previa la genera el SERVIDOR
// (POST /element-layouts/<id>/render/ con format=png), con el mismo código
// que imprime. Es la diferencia con editor_rotulos.html, donde el lienzo
// dibuja con CSS en píxeles fijos y el backend renderiza con medidas
// proporcionales, y por eso lo impreso no coincide con lo diseñado.
//
// Acá el lienzo sirve para UBICAR (todo en mm, escalado por un único
// factor) y el render del servidor para CONFIRMAR.
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth); escapeHtml,
// showMessage y getErrorMessage, de assets/js/utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const LAYOUTS_URL = `${API_BASE}/labels/element-layouts/`;
const VARIABLES_URL = `${API_BASE}/labels/layout-variables/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const layoutSelect = document.getElementById("layoutSelect");
const workspace = document.getElementById("workspace");
const canvas = document.getElementById("canvas");
const canvasHint = document.getElementById("canvasHint");
const noSelection = document.getElementById("noSelection");
const elementForm = document.getElementById("elementForm");
const previewCard = document.getElementById("previewCard");
const serverPreview = document.getElementById("serverPreview");
const previewWarnings = document.getElementById("previewWarnings");

const fields = {
  type: document.getElementById("elType"),
  variable: document.getElementById("elVariable"),
  content: document.getElementById("elContent"),
  x: document.getElementById("elX"),
  y: document.getElementById("elY"),
  w: document.getElementById("elW"),
  h: document.getElementById("elH"),
  name: document.getElementById("layoutName"),
  width: document.getElementById("layoutWidth"),
  height: document.getElementById("layoutHeight"),
};

// El diseño que se está editando, en memoria. Se manda entero al guardar:
// el serializer acepta "elements" anidado y reemplaza la lista.
let layout = null;
let variables = [];
let selectedIndex = null;
// mm -> px. Un único factor para todo, que es justamente lo que evita que
// el lienzo y el papel se separen.
let scale = 1;

// ---------------------------------------------------------------------------
// Carga
// ---------------------------------------------------------------------------
async function loadVariables() {
  try {
    const response = await apiFetch(VARIABLES_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    variables = extractResults(await response.json());
    fields.variable.innerHTML = variables
      .map(
        (variable) =>
          `<option value="${escapeHtml(variable.code)}">${escapeHtml(variable.display_name || variable.code)}</option>`
      )
      .join("");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar las variables:", err);
  }
}

async function loadLayouts() {
  try {
    const response = await apiFetch(LAYOUTS_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    extractResults(await response.json()).forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = `${item.name} (${item.width_mm} × ${item.height_mm} mm)`;
      layoutSelect.appendChild(option);
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar los diseños:", err);
    showMessage("No se pudieron cargar tus diseños.");
  }
}

async function openLayout(id) {
  try {
    const response = await apiFetch(`${LAYOUTS_URL}${id}/`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    layout = await response.json();
    layout.elements = layout.elements || [];
    fields.name.value = layout.name || "";
    fields.width.value = layout.width_mm;
    fields.height.value = layout.height_mm;
    selectedIndex = null;
    workspace.style.display = "";
    previewCard.style.display = "none";
    renderCanvas();
    showSelection();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al abrir el diseño:", err);
    showMessage("No se pudo abrir ese diseño.");
  }
}

// ---------------------------------------------------------------------------
// Lienzo
// ---------------------------------------------------------------------------
function elementLabel(element) {
  if (element.element_type === "variable") {
    const found = variables.find((v) => v.code === element.variable);
    return found ? found.display_name || found.code : element.variable || "dato";
  }
  if (element.element_type === "texto_estatico") return element.content || "(texto vacío)";
  if (element.element_type === "linea") return "línea";
  return "recuadro";
}

function renderCanvas() {
  const widthMm = Number(fields.width.value) || layout.width_mm;
  const heightMm = Number(fields.height.value) || layout.height_mm;
  // Se elige el factor para que entre en el ancho disponible sin pasar de
  // 460 px; todo lo demás se deriva de acá.
  scale = Math.min((canvas.parentElement.clientWidth || 460) - 8, 460) / widthMm;

  canvas.style.width = `${widthMm * scale}px`;
  canvas.style.height = `${heightMm * scale}px`;
  canvas.innerHTML = "";
  canvasHint.textContent = `${widthMm} × ${heightMm} mm · escala ${(scale).toFixed(2)} px/mm`;

  layout.elements.forEach((element, index) => {
    const box = document.createElement("div");
    box.className = `el el-${escapeHtml(element.element_type)}`;
    if (index === selectedIndex) box.classList.add("selected");
    box.style.left = `${element.x_mm * scale}px`;
    box.style.top = `${element.y_mm * scale}px`;
    box.style.width = `${element.width_mm * scale}px`;
    box.style.height = `${element.height_mm * scale}px`;
    box.textContent = elementLabel(element);
    box.title = elementLabel(element);
    box.dataset.index = String(index);
    attachDrag(box, index);
    canvas.appendChild(box);
  });
}

// Arrastre en píxeles, guardado en mm: la conversión pasa por el mismo
// factor que dibuja, así que lo que se ve es lo que se guarda.
function attachDrag(box, index) {
  let startX = 0;
  let startY = 0;
  let originX = 0;
  let originY = 0;
  let moved = false;

  const onMove = (event) => {
    const element = layout.elements[index];
    const widthMm = Number(fields.width.value) || layout.width_mm;
    const heightMm = Number(fields.height.value) || layout.height_mm;
    const dxMm = (event.clientX - startX) / scale;
    const dyMm = (event.clientY - startY) / scale;
    if (Math.abs(event.clientX - startX) > 2 || Math.abs(event.clientY - startY) > 2) {
      moved = true;
    }
    // No se deja salir del rótulo: un elemento fuera del papel no se imprime.
    element.x_mm = Math.max(0, Math.min(widthMm - element.width_mm, originX + dxMm));
    element.y_mm = Math.max(0, Math.min(heightMm - element.height_mm, originY + dyMm));
    element.x_mm = Math.round(element.x_mm * 10) / 10;
    element.y_mm = Math.round(element.y_mm * 10) / 10;
    box.style.left = `${element.x_mm * scale}px`;
    box.style.top = `${element.y_mm * scale}px`;
    if (index === selectedIndex) {
      fields.x.value = element.x_mm;
      fields.y.value = element.y_mm;
    }
  };

  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    box.classList.remove("dragging");
    if (!moved) select(index);
  };

  box.addEventListener("mousedown", (event) => {
    event.preventDefault();
    const element = layout.elements[index];
    startX = event.clientX;
    startY = event.clientY;
    originX = element.x_mm;
    originY = element.y_mm;
    moved = false;
    box.classList.add("dragging");
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

// ---------------------------------------------------------------------------
// Selección y propiedades
// ---------------------------------------------------------------------------
function showSelection() {
  const hasSelection = selectedIndex !== null && layout.elements[selectedIndex];
  noSelection.style.display = hasSelection ? "none" : "";
  elementForm.style.display = hasSelection ? "" : "none";
  if (!hasSelection) return;

  const element = layout.elements[selectedIndex];
  fields.type.value = element.element_type;
  fields.variable.value = element.variable || "";
  fields.content.value = element.content || "";
  fields.x.value = element.x_mm;
  fields.y.value = element.y_mm;
  fields.w.value = element.width_mm;
  fields.h.value = element.height_mm;
  syncTypeFields();
}

// Un dato lleva variable y un texto fijo lleva contenido; el backend
// rechaza la mezcla (LayoutElement.clean), así que no se ofrece.
function syncTypeFields() {
  const type = fields.type.value;
  document.getElementById("variableField").style.display = type === "variable" ? "" : "none";
  document.getElementById("contentField").style.display =
    type === "texto_estatico" ? "" : "none";
}

function select(index) {
  selectedIndex = index;
  renderCanvas();
  showSelection();
}

fields.type.addEventListener("change", () => {
  const element = layout.elements[selectedIndex];
  element.element_type = fields.type.value;
  if (element.element_type === "variable") {
    element.content = "";
    element.variable = fields.variable.value || (variables[0] && variables[0].code) || null;
  } else {
    element.variable = null;
    if (element.element_type !== "texto_estatico") element.content = "";
  }
  syncTypeFields();
  renderCanvas();
});

fields.variable.addEventListener("change", () => {
  layout.elements[selectedIndex].variable = fields.variable.value;
  renderCanvas();
});

fields.content.addEventListener("input", () => {
  layout.elements[selectedIndex].content = fields.content.value;
  renderCanvas();
});

[["x", "x_mm"], ["y", "y_mm"], ["w", "width_mm"], ["h", "height_mm"]].forEach(
  ([field, key]) => {
    fields[field].addEventListener("input", () => {
      const value = Number(fields[field].value);
      if (Number.isFinite(value)) {
        layout.elements[selectedIndex][key] = value;
        renderCanvas();
      }
    });
  }
);

document.getElementById("deleteElementBtn").addEventListener("click", () => {
  layout.elements.splice(selectedIndex, 1);
  selectedIndex = null;
  renderCanvas();
  showSelection();
});

document.querySelectorAll("[data-add]").forEach((button) => {
  button.addEventListener("click", () => {
    const type = button.dataset.add;
    layout.elements.push({
      element_type: type,
      variable: type === "variable" ? (variables[0] && variables[0].code) || null : null,
      content: type === "texto_estatico" ? "Texto" : "",
      x_mm: 5,
      y_mm: 5,
      width_mm: 40,
      height_mm: 8,
      style: {},
      // Las decoraciones van detrás del contenido, igual que en la propuesta
      // que arma apps.processing.
      order: type === "linea" || type === "recuadro" ? 0 : 10,
    });
    select(layout.elements.length - 1);
  });
});

[fields.width, fields.height].forEach((input) => {
  input.addEventListener("input", () => layout && renderCanvas());
});

// ---------------------------------------------------------------------------
// Guardar y ver el render real
// ---------------------------------------------------------------------------
document.getElementById("saveBtn").addEventListener("click", async () => {
  const button = document.getElementById("saveBtn");
  button.disabled = true;
  try {
    const response = await apiFetch(`${LAYOUTS_URL}${layout.id}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: fields.name.value.trim(),
        width_mm: Number(fields.width.value),
        height_mm: Number(fields.height.value),
        elements: layout.elements,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo guardar el diseño."));
    }
    layout = data;
    layout.elements = layout.elements || [];
    showMessage("Diseño guardado.", "success");
    renderCanvas();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al guardar el diseño:", err);
    showMessage(err.message || "No se pudo guardar el diseño.");
  } finally {
    button.disabled = false;
  }
});

document.getElementById("previewBtn").addEventListener("click", async () => {
  const button = document.getElementById("previewBtn");
  button.disabled = true;
  try {
    // Sin "data": el backend rellena con valores de muestra. Se pide PNG
    // porque es para mirar, no para imprimir.
    const response = await apiFetch(`${LAYOUTS_URL}${layout.id}/render/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: "png", dpi: 150 }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudo generar la vista previa."));
    }

    // El render avisa por headers qué datos faltaron o se recortaron: es
    // justo lo que el usuario necesita saber antes de mandar a imprimir.
    const missing = response.headers.get("X-Layout-Missing");
    const truncated = response.headers.get("X-Layout-Truncated");
    const notes = [];
    if (missing) notes.push(`Sin dato: ${missing}`);
    if (truncated) notes.push(`No entraba y se recortó: ${truncated}`);
    previewWarnings.textContent = notes.join(" · ");
    previewWarnings.style.display = notes.length ? "block" : "none";

    const blob = await response.blob();
    serverPreview.innerHTML = "";
    const image = document.createElement("img");
    image.src = URL.createObjectURL(blob);
    image.alt = "Vista previa del rótulo generada por el servidor";
    image.addEventListener("load", () => URL.revokeObjectURL(image.src));
    serverPreview.appendChild(image);
    previewCard.style.display = "";
    previewCard.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al generar la vista previa:", err);
    showMessage(err.message || "No se pudo generar la vista previa.");
  } finally {
    button.disabled = false;
  }
});

layoutSelect.addEventListener("change", (event) => {
  if (!event.target.value) {
    workspace.style.display = "none";
    return;
  }
  openLayout(event.target.value);
});

const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) {
      const user = await response.json();
      window.AppTopbar.render(user);
      // Gateo de UI nada más: el backend revalida plantillas.edit.
      if (!(user.permissions || []).includes("plantillas.edit")) {
        window.location.replace("dashboard.html");
        return;
      }
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar el perfil:", err);
  }
  await loadVariables();
  await loadLayouts();
  // Se puede llegar con ?id= desde la pantalla de importar.
  const wanted = new URLSearchParams(location.search).get("id");
  if (wanted) {
    layoutSelect.value = wanted;
    if (layoutSelect.value) openLayout(wanted);
  }
}

init();
