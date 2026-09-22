// Funciones compartidas por varios scripts del frontend (antes copiadas y
// pegadas en cada uno, con pequeñas diferencias entre copias). Se carga
// como script CLÁSICO, siempre después de config.js/auth.js y antes del
// script propio de cada página: sus `function` de nivel superior quedan
// como globales (propiedades de `window`), igual que auth.js.
//
// Un módulo ES (p. ej. assets/js/labels_api.js) no puede recibir un script
// clásico "por delante" en el sentido de import, pero sí lee estas
// funciones desde `window` una vez cargado este archivo (mismo criterio
// que ese módulo ya usa con `window.Auth`, ver auth.js).

// Escapa texto para insertarlo en HTML (texto de nodo O valor de atributo:
// por eso escapa también comillas). Antes había una versión más corta
// (armaba un <div>, le asignaba textContent y leía innerHTML) en un par de
// páginas que NO escapaba comillas/apóstrofes — funcionaba para texto de
// nodo, pero al usarse dentro de un atributo (p. ej. `title="${escapeHtml(x)}"`
// en integraciones.js, con texto de terceros como el cuerpo de un webhook)
// una comilla sin escapar podía cerrar el atributo. Esta versión escapa
// las cinco entidades y es segura en ambos contextos.
function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]
  );
}

// Arma un mensaje de error legible a partir de una respuesta de error de la
// API: string plano, {detail: "..."}, {detail: ["...", "..."]} (varios
// errores) o el primer error de un serializer ({campo: ["error"]}).
function getErrorMessage(data, fallback) {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return fallback;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.join(" ");
  for (const value of Object.values(data)) {
    if (Array.isArray(value) && value.length) return String(value[0]);
    if (typeof value === "string") return value;
  }
  return fallback;
}

// Muestra un mensaje en el <p id="pageMessage" class="page-message"> de la
// página (el mismo elemento/clase en todas las páginas que lo usan; solo
// cambia si arrancan ocultas con `hidden` o con `style="display:none"` —
// esta función cubre ambos casos). dashboard.html tiene su propio elemento
// (id/clase distintos) y su propio showMessage local: no lo reemplaza este.
function showMessage(text, type = "error") {
  const el = document.getElementById("pageMessage");
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${type}`;
  el.hidden = false;
  el.style.display = "block";
}

// Formatea una fecha ISO como dd/mm/aaaa hh:mm (es-AR). "-" si no hay
// fecha o no se puede parsear. No es la única función de fecha del
// frontend a propósito: pedidos.js necesita solo la fecha (sin hora) y
// tiendas.js usa un formato corto de Intl distinto — moverlas acá les
// cambiaría lo que ve el usuario, así que quedaron como funciones propias
// de esas páginas. admin_common.js expone su propia formatDateTime (con
// guarda NaN en vez de try/catch) para las páginas de administración.
function formatDate(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString("es-AR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "-";
  }
}

// La API pagina (PageNumberPagination): {count, next, previous, results}.
// Esta función normaliza tanto una respuesta paginada como un array plano.
function extractResults(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.results)) return data.results;
  return [];
}
