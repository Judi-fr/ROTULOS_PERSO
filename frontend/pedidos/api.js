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
  return payload;
}

function fromApiLabel(label) {
  return {
    id: label.id,
    nombre: label.name,
    cliente: label.client,
    order: label.order,
    thumbnail: label.thumbnail || null,
    logo: label.logo || null,
    size: { widthCm: Number(label.width_cm), heightCm: Number(label.height_cm) },
    fields: label.design || {},
    isActive: label.is_active,
    createdAt: label.created_at,
    updatedAt: label.updated_at,
  };
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
