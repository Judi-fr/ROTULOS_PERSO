// Dashboard (menú) para usuarios no administradores.
// El menú se arma en el BACKEND según el rol efectivo del usuario: este
// archivo solo pide el endpoint y renderiza lo que devuelve, sin decidir
// visibilidad por rol acá. Sesión y apiFetch salen de assets/js/auth.js
// (window.Auth), compartido con el resto del frontend.

const API_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
const DASHBOARD_URL = `${API_BASE}/users/me/dashboard/`;
const ORDERS_URL = `${window.APP_CONFIG.API_BASE}/orders/`;
const MY_INVITATIONS_URL = `${window.APP_CONFIG.API_BASE}/team/my-invitations/`;

// Las secciones del panel (cada una con sus links internos) y sus íconos
// vienen armadas del backend (apps/accounts/dashboard_sections.py): acá solo
// se dibujan.
const NOTICES_URL = `${window.APP_CONFIG.API_BASE}/notices/`;
const ONBOARDING_HIDDEN_KEY = "onboardingHidden";

const hasSession = () => window.Auth.hasSession();
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

// Todas las tarjetas tienen la misma forma (cabecera, lista de herramientas
// con lugar para tres, "Abrir" abajo a todo el ancho) para que la grilla
// quede pareja aunque cada sección tenga distinta cantidad de herramientas.
function buildSectionCard(section, { wide = false } = {}) {
  const card = document.createElement("article");
  card.className = wide ? "section-card section-card-wide" : "section-card";

  const head = document.createElement("div");
  head.className = "section-card-head";
  const icon = document.createElement("span");
  icon.className = "section-card-icon";
  icon.innerHTML = `<i class="${escapeHtml(section.icon || "mdi mdi-view-grid-outline")}" aria-hidden="true"></i>`;
  const text = document.createElement("div");
  text.className = "section-card-text";
  const title = document.createElement("h2");
  title.className = "section-card-title";
  title.textContent = section.label;
  const description = document.createElement("p");
  description.className = "section-card-description";
  description.textContent = section.description || "";
  text.append(title, description);
  head.append(icon, text);

  const tools = document.createElement("ul");
  tools.className = "section-card-tools";
  tools.setAttribute("aria-label", `Herramientas de ${section.label}`);
  (section.links || []).forEach((link) => {
    const item = document.createElement("li");
    const anchor = document.createElement("a");
    anchor.href = link.url;
    anchor.innerHTML = '<span></span><i class="mdi mdi-chevron-right" aria-hidden="true"></i>';
    anchor.querySelector("span").textContent = link.label;
    item.appendChild(anchor);
    tools.appendChild(item);
  });

  const open = document.createElement("a");
  open.className = "section-btn section-btn-primary section-card-open";
  open.href = section.url;
  open.innerHTML = `<span></span> <i class="mdi mdi-arrow-right" aria-hidden="true"></i>`;
  open.querySelector("span").textContent = `Abrir ${section.label}`;

  card.append(head, tools, open);
  return card;
}

// Columnas según cuántas secciones hay, para que no quede una suelta: 4 en
// 2×2, 6 en 3×2; una fila incompleta va centrada. Administración (solo el
// admin) va aparte, a todo el ancho, para no romper la grilla.
function columnsFor(count) {
  if (count === 4) return 2;
  return Math.min(Math.max(count, 1), 3);
}

function renderSections(sections) {
  const container = document.getElementById("menuGroups");
  if (!container) return;
  container.innerHTML = "";
  if (!Array.isArray(sections) || sections.length === 0) {
    container.innerHTML = '<p class="menu-loading">No hay secciones disponibles.</p>';
    return;
  }
  const regular = sections.filter((section) => section.key !== "admin");
  const admin = sections.find((section) => section.key === "admin");
  const grid = document.createElement("div");
  grid.className = "section-grid";
  grid.dataset.columns = String(columnsFor(regular.length));
  regular.forEach((section) => grid.appendChild(buildSectionCard(section)));
  container.appendChild(grid);
  if (admin) container.appendChild(buildSectionCard(admin, { wide: true }));
}

// ---------------------------------------------------------------------------
// AVISOS: lo que se rompió en el comercio (apps/monitoring/notices.py). Los
// tres más nuevos acá; todos en notificaciones.html.
// ---------------------------------------------------------------------------
async function loadNotices() {
  const box = document.getElementById("noticesBox");
  if (!box) return;
  try {
    const response = await apiFetch(NOTICES_URL);
    if (!response.ok) return;
    const notices = await response.json();
    box.innerHTML = "";
    notices.slice(0, 3).forEach((notice) => box.appendChild(buildNotice(notice)));
    if (notices.length > 3) {
      const more = document.createElement("a");
      more.className = "notice-more";
      more.href = "notificaciones.html";
      more.textContent = `Ver los ${notices.length} avisos`;
      box.appendChild(more);
    }
    box.hidden = notices.length === 0;
  } catch (err) {
    if (!err || !err.isSessionExpired) console.error("Error al leer los avisos:", err);
  }
}

function buildNotice(notice) {
  const item = document.createElement("div");
  item.className = "notice";
  item.setAttribute("role", "status");
  const text = document.createElement("span");
  text.className = "notice-text";
  text.textContent = notice.title;
  item.innerHTML = '<i class="mdi mdi-alert-outline" aria-hidden="true"></i>';
  item.appendChild(text);
  if (notice.url) {
    const review = document.createElement("a");
    review.href = notice.url;
    review.textContent = "Revisar";
    item.appendChild(review);
  }
  const hide = document.createElement("button");
  hide.type = "button";
  hide.className = "notice-hide";
  hide.textContent = "Ocultar";
  hide.addEventListener("click", async () => {
    hide.disabled = true;
    try {
      const response = await apiFetch(`${NOTICES_URL}${notice.id}/dismiss/`, { method: "POST" });
      if (response.ok || response.status === 404) {
        await loadNotices();
        return;
      }
      showMessage("No se pudo ocultar el aviso.");
    } catch (err) {
      if (!err || !err.isSessionExpired) showMessage("No se pudo ocultar el aviso.");
    }
    hide.disabled = false;
  });
  item.appendChild(hide);
  return item;
}

// ---------------------------------------------------------------------------
// PRIMEROS PASOS: solo para el dueño de un comercio que no terminó de
// armarlo. "Ocultar" queda en este navegador (es una comodidad, no un dato).
// ---------------------------------------------------------------------------
function onboardingHidden() {
  try {
    return localStorage.getItem(ONBOARDING_HIDDEN_KEY) === "1";
  } catch (err) {
    return false;
  }
}

function renderOnboarding(onboarding) {
  const box = document.getElementById("onboardingBox");
  if (!box) return;
  if (!onboarding || onboarding.done >= onboarding.total || onboardingHidden()) {
    box.hidden = true;
    return;
  }
  box.innerHTML = "";
  const head = document.createElement("div");
  head.className = "onboarding-head";
  const title = document.createElement("h2");
  title.textContent = `Primeros pasos · ${onboarding.done} de ${onboarding.total}`;
  const hide = document.createElement("button");
  hide.type = "button";
  hide.className = "onboarding-hide";
  hide.textContent = "Ocultar";
  hide.addEventListener("click", () => {
    try {
      localStorage.setItem(ONBOARDING_HIDDEN_KEY, "1");
    } catch (err) {
      /* sin almacenamiento: solo se oculta por ahora */
    }
    box.hidden = true;
  });
  head.append(title, hide);

  const progress = document.createElement("div");
  progress.className = "onboarding-progress";
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", String(onboarding.total));
  progress.setAttribute("aria-valuenow", String(onboarding.done));
  progress.innerHTML = `<span style="width:${Math.round((onboarding.done / onboarding.total) * 100)}%"></span>`;

  const list = document.createElement("ol");
  list.className = "onboarding-steps";
  onboarding.steps.forEach((step) => {
    const item = document.createElement("li");
    item.className = step.done ? "done" : "";
    const icon = step.done ? "mdi-check-circle" : "mdi-circle-outline";
    item.innerHTML = `<i class="mdi ${icon}" aria-hidden="true"></i>`;
    if (step.done) {
      const label = document.createElement("span");
      label.textContent = step.label;
      item.appendChild(label);
    } else {
      const button = document.createElement("a");
      button.className = "section-btn";
      button.href = step.url;
      button.textContent = step.label;
      item.appendChild(button);
    }
    list.appendChild(item);
  });
  box.append(head, progress, list);
  box.hidden = false;
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
    <div class="summary-card-main">${escapeHtml(order.description || `Pedido #${order.id}`)} — ${escapeHtml(order.status_label)}</div>
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

// Si alguien invitó a este usuario a su comercio, se lo dice acá: es lo
// primero que ve al entrar (ver invitaciones.html).
async function loadInvitationNotice() {
  try {
    const response = await apiFetch(MY_INVITATIONS_URL);
    if (!response.ok) return;
    const invitations = await response.json();
    if (!Array.isArray(invitations) || !invitations.length) return;
    const notice = document.getElementById("invitationNotice");
    const names = invitations.map((item) => `«${item.team_name}»`).join(", ");
    notice.textContent = `Te invitaron a trabajar en ${names}. `;
    const link = document.createElement("a");
    link.href = "invitaciones.html";
    link.textContent = invitations.length === 1 ? "Ver la invitación" : "Ver las invitaciones";
    notice.appendChild(link);
    notice.hidden = false;
  } catch (err) {
    if (!err || !err.isSessionExpired) console.error("Error al leer las invitaciones:", err);
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
    renderOnboarding(data.onboarding);
    renderSections(data.sections || []);
    loadOrderSummary();
    loadInvitationNotice();
    loadNotices();
  } catch (err) {
    if (!err || !err.isSessionExpired) {
      console.error("Error al cargar el dashboard:", err);
      showMessage("No se pudo cargar el panel. Intentá de nuevo más tarde.");
    }
  }
}

// ---------------------------------------------------------------------------
// Logout: misma lógica que admin_common.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

if (!hasSession()) {
  window.location.replace("index.html");
} else {
  loadDashboard();
}
