/**
 * <app-sidebar></app-sidebar>
 *
 * Web Component reutilizable que encapsula el menú lateral (sidebar).
 * Incluí este archivo con <script src="sidebar-component.js"></script>
 * y usá <app-sidebar active="dashboard"></app-sidebar> en cualquier página.
 *
 * El atributo "active" te permite indicar qué item del menú debe
 * mostrarse resaltado en cada página (ver lista de valores más abajo).
 */

class AppSidebar extends HTMLElement {
  connectedCallback() {
    const active = this.getAttribute("active") || "";

    // Definimos los items del menú en un array para no repetir código
    const items = [
      {
        id: "dashboard",
        label: "Dashboard",
        href: "index.html",
        icon: `<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" />`,
      },
      {
        id: "users",
        label: "Users",
        href: "gestionuser.html",
        icon: `<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />`,
      },
      {
        id: "roles",
        label: "Roles",
        href: "roles.html",
        icon: `<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6z" />`,
      },
      {
        id: "permissions",
        label: "Permissions",
        href: "permissions.html",
        icon: `<rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />`,
      },
      {
        id: "settings",
        label: "Settings",
        href: "settings.html",
        icon: `<circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 0 1-4 0v-.09A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 0 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.55V3a2 2 0 0 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 0 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z" />`,
      },
      {
        id: "rotulos",
        label: "Diseño de Rótulos",
        href: "plantillas_rotulos.html",
        icon: `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />`,
      },
      {
        id: "reports",
        label: "Reports",
        href: "reports.html",
        icon: `<line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />`,
      },
    ];

    const navHtml = items
      .map(
        (item) => `
        <li>
          <a href="${item.href}" class="${item.id === active ? "active" : ""}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              ${item.icon}
            </svg>
            ${item.label}
          </a>
        </li>`,
      )
      .join("");

    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
        }
        .sidebar {
          display: flex;
          flex-direction: column;
          width: 240px;
          height: 100vh;
          background: #1e1b2e;
          color: #cfcbe0;
          padding: 20px 0;
          box-sizing: border-box;
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 0 20px 20px;
          font-weight: 700;
          font-size: 18px;
          color: #fff;
          border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        .brand-icon svg { width: 24px; height: 24px; }
        .nav {
          list-style: none;
          margin: 0;
          padding: 12px 0;
          flex: 1;
        }
        .nav li a {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 20px;
          color: #cfcbe0;
          text-decoration: none;
          font-size: 14px;
          border-left: 3px solid transparent;
          transition: background 0.15s, color 0.15s;
        }
        .nav li a svg { width: 18px; height: 18px; flex-shrink: 0; }
        .nav li a:hover {
          background: rgba(255,255,255,0.06);
          color: #fff;
        }
        .nav li a.active {
          background: rgba(124, 92, 255, 0.15);
          color: #fff;
          border-left-color: #7c5cff;
        }
        .sidebar-footer {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 16px 20px 0;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        .avatar-round {
          width: 34px;
          height: 34px;
          border-radius: 50%;
        }
        .who { flex: 1; min-width: 0; }
        .who .name { font-size: 13px; font-weight: 600; color: #fff; }
        .who .mail {
          font-size: 11px;
          color: #8f89a8;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .sidebar-footer svg { width: 16px; height: 16px; }
      </style>

      <aside class="sidebar">
        <div class="brand">
          <span class="brand-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </span>
          Admintradores
        </div>

        <ul class="nav">
          ${navHtml}
        </ul>

        <div class="sidebar-footer">
          <img class="avatar-round" src="https://api.dicebear.com/7.x/avataaars/svg?seed=admin" alt="" />
          <div class="who">
            <div class="name">Admin</div>
            <div class="mail">admin@site.com</div>
          </div>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>
      </aside>
    `;
  }
}

customElements.define("app-sidebar", AppSidebar);
