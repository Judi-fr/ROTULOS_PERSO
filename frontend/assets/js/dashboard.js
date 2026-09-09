// Dashboard (menú) para usuarios no administradores.
// El menú se arma en el BACKEND según el rol efectivo del usuario: este
// archivo solo pide el endpoint y renderiza lo que devuelve, sin decidir
// visibilidad por rol acá. Sesión y apiFetch salen de assets/js/auth.js
// (window.Auth), compartido con el resto del frontend.

const API_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
const DASHBOARD_URL = `${API_BASE}/users/me/dashboard/`;
const ORDERS_URL = `${window.APP_CONFIG.API_BASE}/orders/`;

// Íconos MDI por key de menú. Un key sin ícono cae en el genérico.
const MENU_ICONS = {
  users: "mdi mdi-account-group",
  orders: "mdi mdi-truck-delivery-outline",
  addresses: "mdi mdi-map-marker-outline",
  labels: "mdi mdi-tag-outline",
  documents: "mdi mdi-file-document-outline",
  processing: "mdi mdi-cog-outline",
  profile: "mdi mdi-account-circle-outline",
  support: "mdi mdi-lifebuoy",
  audit: "mdi mdi-shield-search-outline",
  support_inbox: "mdi mdi-email-alert-outline",
};

// Agrupación puramente visual (el backend decide qué ítems existen y si
// están habilitados; acá solo se los organiza en categorías, igual que
// MENU_ICONS es un mapeo cliente-side por key). Un key sin grupo cae en
// "Otros".
const MENU_GROUPS = {
  orders: "Mis cosas",
  addresses: "Mis cosas",
  labels: "Mis cosas",
  documents: "Mis cosas",
  processing: "Mis cosas",
  users: "Cuenta",
  profile: "Cuenta",
  support: "Ayuda",
  audit: "Administración",
  support_inbox: "Administración",
};
const GROUP_ORDER = ["Mis cosas", "Cuenta", "Ayuda", "Administración", "Otros"];

const getAccessToken = () => window.Auth.getAccessToken();
const getCurrentUser = () => window.Auth.getCurrentUser();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

function hasPermission(permission) {
  const permissions = getCurrentUser().permissions;
  return Array.isArray(permissions) && permissions.includes(permission);
}

function showMessage(text, type = "error") {
  const el = document.getElementById("dashboardMessage");
  if (!el) return;
  el.textContent = text;
  el.className = `dashboard-message ${type}`;
  el.style.display = "block";
}

// El dato ya viene resuelto por el backend (DashboardView): no se decide acá
// por rol/permissions locales, solo se muestra u oculta según is_admin.
function renderBackToAdminLink(user) {
  const link = document.getElementById("backToAdminLink");
  if (!link) return;
  link.style.display = user && user.is_admin ? "" : "none";
}

function renderUser(user) {
  const nameEl = document.getElementById("userName");
  const emailEl = document.getElementById("userEmail");
  const avatarEl = document.getElementById("userAvatar");

  const name = (user && user.full_name) || (user && user.email) || "Usuario";
  const email = (user && user.email) || "—";

  if (nameEl) nameEl.textContent = name;
  if (emailEl) emailEl.textContent = email;
  if (avatarEl) {
    const picture = getCurrentUser().picture;
    avatarEl.src = picture
      ? picture
      : `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(email)}`;
  }
}

function buildMenuCard(item) {
  const enabled = Boolean(item.enabled) && item.url;
  const iconClass = MENU_ICONS[item.key] || "mdi mdi-view-grid-outline";

  // Deshabilitado: <span>, no clickeable. Habilitado: <a> con el href real.
  const el = document.createElement(enabled ? "a" : "span");
  el.className = `menu-card${enabled ? "" : " disabled"}`;
  if (enabled) {
    el.href = item.url;
  } else {
    el.setAttribute("aria-disabled", "true");
  }

  el.innerHTML = `
    <span class="menu-card-icon"><i class="${iconClass}"></i></span>
    <span class="menu-card-label">${item.label || item.key}</span>
    <span class="menu-card-status">${enabled ? "Disponible" : "Próximamente"}</span>
  `;
  return el;
}

// Agrupa los ítems del menú por categoría (ver MENU_GROUPS) y arma una
// sección por grupo, cada una con su propio .menu-grid. Con pocos ítems
// esto es visualmente equivalente a una grilla plana; con muchos, evita que
// quede todo mezclado sin distinción.
function renderMenu(menu) {
  const container = document.getElementById("menuGroups");
  if (!container) return;

  if (!Array.isArray(menu) || menu.length === 0) {
    container.innerHTML = '<p class="menu-loading">No hay secciones disponibles.</p>';
    return;
  }

  const byGroup = new Map();
  menu.forEach((item) => {
    const groupName = MENU_GROUPS[item.key] || "Otros";
    if (!byGroup.has(groupName)) byGroup.set(groupName, []);
    byGroup.get(groupName).push(item);
  });

  container.innerHTML = "";
  GROUP_ORDER.forEach((groupName) => {
    const items = byGroup.get(groupName);
    if (!items || items.length === 0) return;

    const section = document.createElement("section");
    section.className = "menu-group";

    const title = document.createElement("h2");
    title.className = "menu-group-title";
    title.textContent = groupName;
    section.appendChild(title);

    const grid = document.createElement("div");
    grid.className = "menu-grid";
    items.forEach((item) => grid.appendChild(buildMenuCard(item)));
    section.appendChild(grid);

    container.appendChild(section);
  });
}

// ---------------------------------------------------------------------------
// RESUMEN RÁPIDO: último pedido propio (reusa /api/v1/orders/, ya ordenado
// -created_at por Order.Meta.ordering — no hace falta un endpoint nuevo).
// Si el usuario no tiene el permiso orders.view, ni se intenta pedir.
// No hay bloque de "último rótulo": todavía no existe backend de generación
// de rótulos (apps.labels sigue sin modelos/urls, ver dashboard_views.py),
// así que no hay de dónde traer ese dato sin inventarlo.
// ---------------------------------------------------------------------------
function formatOrderDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("es-AR", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function renderLastOrderSummary(order) {
  const grid = document.getElementById("summaryGrid");
  if (!grid) return;

  if (!order) {
    grid.style.display = "none";
    return;
  }

  const card = document.createElement("div");
  card.className = "summary-card";
  card.innerHTML = `
    <div class="summary-card-title">Último pedido</div>
    <div class="summary-card-main">${order.description || `Pedido #${order.id}`} — ${order.status_label}</div>
    <div class="summary-card-sub">Creado el ${formatOrderDate(order.created_at)}</div>
    <a class="summary-card-link" href="pedidos.html">Ver mis pedidos →</a>
  `;
  grid.innerHTML = "";
  grid.appendChild(card);
  grid.style.display = "grid";
}

// Si el último evento de estado del pedido es reciente (últimas 48hs) y no
// es el evento inicial de creación, avisa en #dashboardMessage.
function maybeShowOrderStatusNotice(order) {
  const events = Array.isArray(order && order.status_events) ? order.status_events : [];
  if (events.length < 2) return; // solo el evento de creación: nada que avisar

  const lastEvent = events[events.length - 1];
  const changedAt = new Date(lastEvent.created_at);
  if (Number.isNaN(changedAt.getTime())) return;

  const hoursSince = (Date.now() - changedAt.getTime()) / (1000 * 60 * 60);
  if (hoursSince < 0 || hoursSince > 48) return;

  showMessage(
    `Tu pedido "${order.description || `#${order.id}`}" cambió a "${lastEvent.status_label}" el ${formatOrderDate(lastEvent.created_at)}.`,
    "info"
  );
}

async function loadOrderSummary() {
  if (!hasPermission("orders.view")) {
    const grid = document.getElementById("summaryGrid");
    if (grid) grid.style.display = "none";
    return;
  }

  try {
    const response = await apiFetch(ORDERS_URL);
    if (!response.ok) return; // resumen es "nice to have": no rompe el dashboard si falla
    const data = await response.json();
    const orders = Array.isArray(data) ? data : data.results || [];
    const lastOrder = orders[0] || null;
    renderLastOrderSummary(lastOrder);
    if (lastOrder) maybeShowOrderStatusNotice(lastOrder);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar el resumen de pedidos:", err);
  }
}

async function loadDashboard() {
  try {
    const response = await apiFetch(DASHBOARD_URL);
    if (!response.ok) {
      showMessage("No se pudo cargar el panel. Intentá de nuevo más tarde.");
      return;
    }
    const data = await response.json();
    renderUser(data.user || {});
    renderBackToAdminLink(data.user || {});
    renderMenu(data.menu || []);
    loadOrderSummary();
  } catch (err) {
    if (!err || !err.isSessionExpired) {
      console.error("Error al cargar el dashboard:", err);
      showMessage("No se pudo cargar el panel. Intentá de nuevo más tarde.");
    }
  }
}

// ---------------------------------------------------------------------------
// Logout: misma lógica que admingestion_test.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  loadDashboard();
}
