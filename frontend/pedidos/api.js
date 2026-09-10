// Módulo de acceso a la API de rótulos (apps.labels), usado por
// diseñorotulos.html y assets/js/dashboard_rotulos.js. La sesión y el
// apiFetch (manejo de 401 con refresh y de 403 con cambio de contraseña
// pendiente) salen de assets/js/auth.js: este módulo ES no puede "cargarse
// antes" como un script clásico, pero lee su API en window.Auth igual — la
// página que importa este módulo debe cargar assets/js/config.js y
// assets/js/auth.js antes (ver diseñorotulos.html / dashboard_rotulos.js).
//
// El editor (diseñorotulos.html) arma/consume el payload con SU propio
// formato ({nombre, cliente, thumbnail, size:{widthCm,heightCm}, logo,
// fields, order}) — acá se adapta ese formato al que espera el backend
// ({name, client, thumbnail, width_cm, height_cm, logo, design, order}) en
// vez de tocar el editor.

const API_BASE = `${window.APP_CONFIG.API_BASE}/labels`;
const LABELS_URL = `${API_BASE}/labels/`;
const TEMPLATES_URL = `${API_BASE}/templates/`;
const ORDERS_URL = `${window.APP_CONFIG.API_BASE}/orders/`;
const BARCODE_URL = `${API_BASE}/barcode/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

// --- Adaptadores entre el formato del editor y el de la API ----------------

function toApiPayload(rotulo) {
  const payload = {
    name: rotulo.nombre,
    client: rotulo.cliente,
    width_cm: rotulo.size?.widthCm,
    height_cm: rotulo.size?.heightCm,
    design: rotulo.fields || {},
  };
  if (rotulo.thumbnail) payload.thumbnail = rotulo.thumbnail;
  if (rotulo.logo) payload.logo = rotulo.logo;
  if (rotulo.order) payload.order = rotulo.order;
  // La plantilla de origen (si el rótulo se creó a partir de una, ver
  // diseñorotulos.html?template=<id>): sin esto Label.template nunca se
  // completa y no queda rastro de qué plantilla generó qué rótulo.
  if (rotulo.template) payload.template = rotulo.template;
  return payload;
}

function fromApiLabel(label) {
  return {
    id: label.id,
    nombre: label.name,
    cliente: label.client,
    order: label.order,
    template: label.template || null,
    thumbnail: label.thumbnail || null,
    logo: label.logo || null,
    size: { widthCm: Number(label.width_cm), heightCm: Number(label.height_cm) },
    fields: label.design || {},
    isActive: label.is_active,
    createdAt: label.created_at,
    updatedAt: label.updated_at,
  };
}

// --- Plantillas (apps.labels.LabelTemplate) --------------------------------
//
// Mismo adaptador del formato del editor ({nombre, size:{widthCm,
// heightCm}, fields}) al de la API ({name, width_cm, height_cm, design}),
// para que frontend/pedidos/diseñorotulos.html y frontend/plantillas.html
// compartan una sola fuente de verdad.

// Para crear (desde "Guardar como plantilla" del editor) SIEMPRE vienen
// nombre/tamaño/diseño completos. Para actualizar, en cambio, solo se
// manda lo que efectivamente vino en `plantilla` (p. ej. renombrar desde
// plantillas.html no debe pisar el diseño con {} por no traerlo).
function toApiTemplateCreatePayload(plantilla) {
  return {
    name: plantilla.nombre,
    width_cm: plantilla.size?.widthCm,
    height_cm: plantilla.size?.heightCm,
    design: plantilla.fields || {},
    ...(plantilla.description != null ? { description: plantilla.description } : {}),
    ...(plantilla.isPublic != null ? { is_public: plantilla.isPublic } : {}),
  };
}

function toApiTemplateUpdatePayload(plantilla) {
  const payload = {};
  if (plantilla.nombre != null) payload.name = plantilla.nombre;
  if (plantilla.description != null) payload.description = plantilla.description;
  if (plantilla.isPublic != null) payload.is_public = plantilla.isPublic;
  if (plantilla.size?.widthCm != null) payload.width_cm = plantilla.size.widthCm;
  if (plantilla.size?.heightCm != null) payload.height_cm = plantilla.size.heightCm;
  if (plantilla.fields != null) payload.design = plantilla.fields;
  return payload;
}

function fromApiTemplate(template) {
  return {
    id: template.id,
    nombre: template.name,
    description: template.description || "",
    owner: template.owner,
    ownerEmail: template.owner_email || null,
    isPublic: Boolean(template.is_public),
    size: { widthCm: Number(template.width_cm), heightCm: Number(template.height_cm) },
    fields: template.design || {},
    preview: template.preview || null,
    createdAt: template.created_at,
    updatedAt: template.updated_at,
  };
}

// La API pagina (PageNumberPagination): {count, next, previous, results}.
export async function listTemplates(params = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await apiFetch(`${TEMPLATES_URL}${query ? `?${query}` : ""}`);
  if (!response.ok) return [];
  const data = await response.json().catch(() => ({}));
  const results = Array.isArray(data) ? data : data.results || [];
  return results.map(fromApiTemplate);
}

export async function getTemplate(id) {
  try {
    const response = await apiFetch(`${TEMPLATES_URL}${id}/`);
    if (!response.ok) return null;
    return fromApiTemplate(await response.json());
  } catch (err) {
    if (err.isSessionExpired) throw err;
    console.error("Error al obtener la plantilla:", err);
    return null;
  }
}

export async function createTemplate(plantilla) {
  const response = await apiFetch(TEMPLATES_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toApiTemplateCreatePayload(plantilla)),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(getErrorMessage(data, "No se pudo guardar la plantilla."));
  }
  return fromApiTemplate(data);
}

export async function updateTemplate(id, plantilla) {
  const response = await apiFetch(`${TEMPLATES_URL}${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toApiTemplateUpdatePayload(plantilla)),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(getErrorMessage(data, "No se pudo actualizar la plantilla."));
  }
  return fromApiTemplate(data);
}

// Soft-delete (archivar) en el backend: la plantilla deja de listarse
// pero los rótulos ya generados con ella la siguen referenciando.
export async function archiveTemplate(id) {
  const response = await apiFetch(`${TEMPLATES_URL}${id}/`, { method: "DELETE" });
  if (!response.ok && response.status !== 204) {
    const data = await response.json().catch(() => ({}));
    throw new Error(getErrorMessage(data, "No se pudo archivar la plantilla."));
  }
  return true;
}

export async function duplicateTemplate(id) {
  const response = await apiFetch(`${TEMPLATES_URL}${id}/duplicate/`, { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(getErrorMessage(data, "No se pudo duplicar la plantilla."));
  }
  return fromApiTemplate(data);
}

// PDF de la plantilla con datos de EJEMPLO (no un pedido real): vista
// previa real para frontend/plantillas.html, mismo render del servidor
// que arma el rótulo definitivo.
export async function fetchTemplatePreviewPdf(id) {
  const response = await apiFetch(`${TEMPLATES_URL}${id}/preview/`);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(getErrorMessage(data, "No se pudo generar la vista previa."));
  }
  return response.blob();
}

function getErrorMessage(data, fallback) {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return fallback;
  if (typeof data.detail === "string") return data.detail;
  for (const value of Object.values(data)) {
    if (Array.isArray(value) && value.length) return String(value[0]);
    if (typeof value === "string") return value;
  }
  return fallback;
}

// --- API pública -------------------------------------------------------

// La API pagina (PageNumberPagination): {count, next, previous, results}.
export async function listRotulos(params = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await apiFetch(`${LABELS_URL}${query ? `?${query}` : ""}`);
  if (!response.ok) return [];
  const data = await response.json().catch(() => ({}));
  const results = Array.isArray(data) ? data : data.results || [];
  return results.map(fromApiLabel);
}

export async function getRotulo(id) {
  try {
    const response = await apiFetch(`${LABELS_URL}${id}/`);
    if (!response.ok) return null;
    const data = await response.json();
    return fromApiLabel(data);
  } catch (err) {
    if (err.isSessionExpired) throw err;
    console.error("Error al obtener el rótulo:", err);
    return null;
  }
}

export async function createRotulo(rotulo) {
  const response = await apiFetch(LABELS_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toApiPayload(rotulo)),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(getErrorMessage(data, "No se pudo guardar el rótulo."));
  }
  return fromApiLabel(data);
}

export async function updateRotulo(id, rotulo) {
  const response = await apiFetch(`${LABELS_URL}${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toApiPayload(rotulo)),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(getErrorMessage(data, "No se pudo actualizar el rótulo."));
  }
  return fromApiLabel(data);
}

// Soft-delete en el backend: el rótulo deja de listarse pero no se destruye.
export async function deleteRotulo(id) {
  const response = await apiFetch(`${LABELS_URL}${id}/`, { method: "DELETE" });
  if (!response.ok && response.status !== 204) {
    const data = await response.json().catch(() => ({}));
    throw new Error(getErrorMessage(data, "No se pudo eliminar el rótulo."));
  }
  return true;
}

// Pedido propio (apps.orders), para autocompletar domicilio/CP/localidad en
// el editor cuando el rótulo se crea a partir de un envío concreto
// (?order=<id> en diseñorotulos.html). Remitente/destinatario los sigue
// completando el usuario: la dirección del pedido no guarda un nombre de
// destinatario.
export async function getOrderInfo(orderId) {
  try {
    const response = await apiFetch(`${ORDERS_URL}${orderId}/`);
    if (!response.ok) return null;
    return await response.json();
  } catch (err) {
    if (err.isSessionExpired) throw err;
    console.error("Error al obtener el pedido:", err);
    return null;
  }
}

// Rótulo definitivo, armado en el servidor (apps.labels.rendering) en vez
// del PNG/PDF que arma html2canvas/jsPDF sobre lo que se ve en el editor.
// Requiere un rótulo YA guardado (usa su id), a diferencia de "Exportar
// PDF" que exporta lo que esté en pantalla en ese momento.
export async function downloadRotuloPdfServer(id) {
  const response = await apiFetch(`${LABELS_URL}${id}/pdf/`);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(getErrorMessage(data, "No se pudo generar el PDF del servidor."));
  }
  return response.blob();
}

// SVG de un QR/código de barras suelto (GET /api/v1/labels/barcode/), para
// la vista previa del editor: el mismo código que arma el PDF final, en vez
// de una librería JS aparte que podría no coincidir. Requiere auth (por eso
// no es un <img src="..."> directo: un <img> no puede mandar el Bearer), así
// que se pide como blob (el navegador ya lo etiqueta image/svg+xml por el
// Content-Type de la respuesta) y se asigna a la <img> como object URL.
export async function fetchCodePreview(params) {
  try {
    const query = new URLSearchParams(params).toString();
    const response = await apiFetch(`${BARCODE_URL}?${query}`);
    if (!response.ok) return null;
    return await response.blob();
  } catch (err) {
    if (err.isSessionExpired) throw err;
    console.error("Error al generar la vista previa del código:", err);
    return null;
  }
}

export async function duplicateRotulo(id) {
  const response = await apiFetch(`${LABELS_URL}${id}/duplicate/`, { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(getErrorMessage(data, "No se pudo duplicar el rótulo."));
  }
  return fromApiLabel(data);
}
