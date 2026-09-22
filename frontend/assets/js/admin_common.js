// Helpers compartidos por las páginas de administración (gestionuser.html,
// roles.html, reportes.html, auditoria.html, pedidos_admin.html,
// soporte_admin.html). Se carga como script clásico, después de config.js,
// auth.js y assets/js/utils.js (getErrorMessage sale de ahí), y antes de
// assets/js/admin_sidebar.js y del script propio de cada página — todas
// comparten el mismo scope global, así que estas funciones quedan
// disponibles sin necesidad de exportarlas por window.

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

// ---------------------------------------------------------------------------
// MODO DE INTERFAZ: se detecta desde el objeto user guardado en localStorage
// (window.Auth.getCurrentUser, ver assets/js/auth.js). El backend es la
// autoridad real de permisos; esto solo elige la UI.
// ---------------------------------------------------------------------------
const getCurrentUser = () => window.Auth.getCurrentUser();

function isAdminMode() {
  const u = getCurrentUser();
  const role = String(u.role || "").toLowerCase();

  return Boolean(
    u.can_manage_users ||
    u.is_staff ||
    role === "admin" ||
    role === "administrador"
  );
}

function getCurrentPermissions() {
  const permissions = getCurrentUser().permissions;
  return Array.isArray(permissions) ? new Set(permissions) : new Set();
}

function canUseUserPermission(permission) {
  const currentUser = getCurrentUser();
  const permissions = getCurrentPermissions();
  // Compatibilidad de UI con sesiones antiguas: el backend continúa siendo
  // la autoridad y siempre decide la respuesta final.
  return permissions.has(permission) || (currentUser.is_staff && permissions.size === 0);
}

function canViewUsers() {
  return canUseUserPermission("users.view");
}

// Clave de la clase CSS del badge de rol: SIEMPRE el texto original que
// manda el backend (role_label, en inglés). No traducir estas claves — solo
// se traduce el texto que se muestra (ver roleLabelEs / translateRole).
const roleLabelEs = {
  Admin: "Administrador",
  Designer: "Diseñador",
  Operator: "Operador",
  Subscriber: "Suscriptor",
  User: "Usuario",
};
function translateRole(role) {
  return roleLabelEs[role] || role;
}

// getErrorMessage vive en assets/js/utils.js (cargado antes que este
// script en todas las páginas que usan admin_common.js).

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("es-AR", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

// Pager simple (Anterior/Siguiente + "Página X de Y"), reusado por
// auditoria.js, pedidos_admin.js y soporte_admin.js para que se vea
// consistente sin duplicar el paginador numerado completo de usuarios.js
// (atado a currentPage/loadUsers, ese no se comparte).
function renderSimplePager(containerId, infoId, pagination, noun, onPageChange) {
  const container = document.getElementById(containerId);
  const info = document.getElementById(infoId);
  if (!pagination) {
    if (container) container.innerHTML = "";
    if (info) info.textContent = `Mostrando 0 ${noun}`;
    return;
  }
  if (info) {
    info.textContent = pagination.count
      ? `Mostrando ${pagination.from}-${pagination.to} de ${pagination.count} ${noun}`
      : `Mostrando 0 ${noun}`;
  }
  if (!container) return;
  container.innerHTML = "";
  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.textContent = "Anterior";
  prevBtn.disabled = !pagination.has_previous;
  prevBtn.addEventListener("click", () => onPageChange(pagination.previous_page));
  const info2 = document.createElement("span");
  info2.className = "dots";
  info2.textContent = `Página ${pagination.page} de ${pagination.total_pages || 1}`;
  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.textContent = "Siguiente";
  nextBtn.disabled = !pagination.has_next;
  nextBtn.addEventListener("click", () => onPageChange(pagination.next_page));
  container.append(prevBtn, info2, nextBtn);
}

// ---------------------------------------------------------------------------
// Catálogo de acciones/categorías de auditoría: lo usan tanto auditoria.js
// (para poblar sus <select> de filtro) como reportes.js (para traducir
// action -> label en "Actividad por tipo de acción", sin selects propios).
// Por eso la carga del catálogo y el poblado de los <select> están separados:
// poblar es un extra que solo pasa si esos <select> existen en la página.
// ---------------------------------------------------------------------------
const AUDIT_ACTIONS_URL = `${window.APP_CONFIG.API_BASE}/audit/actions/`;
let auditActionsCatalog = [];
let auditCategoriesCatalog = [];
let auditActionsCatalogLoaded = false;

async function loadAuditActionsCatalog() {
  if (auditActionsCatalogLoaded) return;
  try {
    const response = await apiFetch(AUDIT_ACTIONS_URL);
    if (!response.ok) return;
    const data = await response.json();
    auditActionsCatalog = Array.isArray(data.actions) ? data.actions : [];
    auditCategoriesCatalog = Array.isArray(data.categories) ? data.categories : [];
    auditActionsCatalogLoaded = true;

    const categorySelect = document.getElementById("auditCategoryFilter");
    const actionSelect = document.getElementById("auditActionFilter");
    if (categorySelect) {
      auditCategoriesCatalog.forEach((cat) => {
        const opt = document.createElement("option");
        opt.value = cat.key;
        opt.textContent = cat.label;
        categorySelect.appendChild(opt);
      });
    }
    if (actionSelect) {
      auditActionsCatalog.forEach((action) => {
        const opt = document.createElement("option");
        opt.value = action.key;
        opt.textContent = action.label;
        actionSelect.appendChild(opt);
      });
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar el catálogo de auditoría:", err);
  }
}

function actionLabel(key) {
  return auditActionsCatalog.find((a) => a.key === key)?.label || key;
}

// Logout: invalida el refresh token en el backend, limpia tokens y redirige
// (window.Auth.logout). El botón #logoutAdminBtn está en el topbar de todas
// las páginas de administración, así que se conecta acá una sola vez.
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("logoutAdminBtn")?.addEventListener("click", () => window.Auth.logout());
});
