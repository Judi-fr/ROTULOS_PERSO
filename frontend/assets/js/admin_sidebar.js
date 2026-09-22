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
      icon: `<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" />`,
    },
    {
      id: "users",
      href: "gestionuser.html",
      label: "Usuarios",
      icon: `<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />`,
      visible: () => canViewUsers(),
    },
    {
      id: "roles",
      href: "roles.html",
      label: "Roles",
      icon: `<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6z" />`,
      visible: () => isAdminMode(),
    },
    {
      id: "settings",
      href: "perfil.html",
      label: "Ajustes",
      icon: `<circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 0 1-4 0v-.09A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 0 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.55V3a2 2 0 0 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 0 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z" />`,
    },
    {
      id: "audit",
      href: "auditoria.html",
      label: "Registros de auditoría",
      icon: `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />`,
      visible: () => canUseUserPermission("audit.view"),
    },
    {
      id: "support",
      href: "soporte_admin.html",
      label: "Mensajes de soporte",
      icon: `<circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" />`,
      visible: () => canUseUserPermission("support.view_all"),
    },
    {
      id: "reports",
      href: "reportes.html",
      label: "Reportes",
      icon: `<line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />`,
      visible: () => canViewUsers(),
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
          <a href="${item.href}" class="${item.id === active ? "active" : ""}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              ${item.icon}
            </svg>
            ${item.label}
          </a>
        </li>`
      )
      .join("");

    container.innerHTML = `
      <aside class="sidebar">
        <div class="brand">
          <span class="brand-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#ffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </span>
          Administradores
        </div>
        <ul class="nav">
          ${itemsHtml}
        </ul>
      </aside>
    `;
  }

  document.addEventListener("DOMContentLoaded", renderAdminSidebar);
})();
