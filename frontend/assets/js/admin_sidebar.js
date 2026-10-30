// Sidebar compartido por las páginas de administración. Renderiza el <aside>
// dentro de <div id="adminSidebar" data-active="..."></div>, con links reales
// a cada página (no javascript:void(0)) y ocultando ítems según permisos con
// la misma lógica que antes tenía bootstrap() en gestionuser.html.
// Requiere admin_common.js (canViewUsers/isAdminMode/canUseUserPermission)
// cargado antes.
(function () {
  "use strict";

  const NAV_ITEMS = [
    {
      id: "dashboard",
      href: "dashboard.html",
      label: "Panel",
      icon: "house-simple",
    },
    {
      id: "users",
      href: "gestionuser.html",
      label: "Usuarios",
      icon: "users",
      visible: () => canViewUsers(),
    },
    {
      id: "roles",
      href: "roles.html",
      label: "Roles",
      icon: "shield-check",
      visible: () => isAdminMode(),
    },
    {
      id: "settings",
      href: "organizacion.html",
      label: "Ajustes",
      icon: "gear-six",
    },
    {
      id: "audit",
      href: "auditoria.html",
      label: "Registros de auditoría",
      icon: "file-text",
      visible: () => canUseUserPermission("audit.view"),
    },
    {
      id: "support",
      href: "soporte_admin.html",
      label: "Mensajes de soporte",
      icon: "chats",
      visible: () => canUseUserPermission("support.view_all"),
    },
    {
      id: "closed_teams",
      href: "comercios_baja.html",
      label: "Comercios dados de baja",
      icon: "archive",
      visible: () => canViewUsers(),
    },
    {
      id: "catalogs",
      href: "catalogos.html",
      label: "Catálogos",
      icon: "book-open-text",
      visible: () => canUseUserPermission("catalogs.manage"),
    },
    {
      id: "monitoring",
      href: "estado_sistema.html",
      label: "Estado del sistema",
      icon: "pulse",
      visible: () => canUseUserPermission("monitoring.view"),
    },
    {
      id: "reports",
      href: "reportes.html",
      label: "Reportes",
      icon: "chart-bar",
      visible: () => canUseUserPermission("reports.view"),
    },
  ];

  function renderAdminSidebar() {
    const container = document.getElementById("adminSidebar");
    if (!container) return;
    const active = container.dataset.active || "";

    const itemsHtml = NAV_ITEMS
      .filter((item) => (item.visible ? item.visible() : true))
      .map(
        (item) => `
        <li>
          <a href="${item.href}" class="${item.id === active ? "active" : ""}" data-nav="${item.id}">
            <i class="ph ph-${item.icon}" aria-hidden="true"></i>
            ${item.label}
          </a>
        </li>`
      )
      .join("");

    container.innerHTML = `
      <aside class="sidebar">
        <div class="brand">
          <img class="brand-mark" src="assets/images/favicon.svg" alt="" width="30" height="30" />
          <span class="brand-text">Rótulos<small>Administración</small></span>
        </div>
        <ul class="nav">
          ${itemsHtml}
        </ul>
      </aside>
    `;
  }

  // Un punto rojo (error) o ámbar (advertencia) en "Estado del sistema" cuando
  // algo falla, para que el admin no dependa solo del mail de alerta. Lee la
  // sonda pública /health/system/ (sin detalles), a lo sumo una vez por minuto
  // por pestaña.
  const HEALTH_CACHE_KEY = "adminSystemHealth";
  const HEALTH_CACHE_MS = 60 * 1000;

  async function systemHealth() {
    try {
      const cached = JSON.parse(sessionStorage.getItem(HEALTH_CACHE_KEY) || "null");
      if (cached && Date.now() - cached.at < HEALTH_CACHE_MS) return cached.data;
    } catch {
      // Sin sessionStorage se pregunta cada vez.
    }
    const response = await fetch(`${window.APP_CONFIG.API_BASE}/health/system/`);
    const data = await response.json();
    try {
      sessionStorage.setItem(HEALTH_CACHE_KEY, JSON.stringify({ at: Date.now(), data }));
    } catch {
      // Idem.
    }
    return data;
  }

  async function markSystemHealth() {
    const link = document.querySelector('#adminSidebar a[data-nav="monitoring"]');
    if (!link) return;
    let data;
    try {
      data = await systemHealth();
    } catch {
      return;
    }
    link.querySelector(".nav-alert")?.remove();
    if (!data || !["error", "warning"].includes(data.status)) return;
    const failing = Object.values(data.checks || {}).filter((value) => value === data.status).length;
    const dot = document.createElement("span");
    dot.className = `nav-alert nav-alert-${data.status}`;
    const label =
      data.status === "error"
        ? `${failing || "Un"} chequeo${failing === 1 ? "" : "s"} con error`
        : `${failing || "Un"} chequeo${failing === 1 ? "" : "s"} con advertencia`;
    dot.title = label;
    dot.setAttribute("aria-label", label);
    link.appendChild(dot);
  }

  function renderAndMark() {
    renderAdminSidebar();
    markSystemHealth();
  }

  document.addEventListener("DOMContentLoaded", renderAndMark);
  // auth.js releyó los permisos y cambiaron: la barra muestra lo que corresponde.
  window.addEventListener("auth:user-updated", renderAndMark);
})();
