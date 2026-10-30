// Barra superior compartida: volver al panel y el comercio a la izquierda, las
// acciones de la página en el medio y el menú de la cuenta (Mi cuenta, Mi
// actividad, Ayuda, Cerrar sesión) a la derecha.
//
// Estaba copiada en cada página: el mismo bloque HTML en 12 archivos y la
// misma función renderTopbar en 11 .js. Acá se escribe una sola vez; cada
// página solo declara sus links propios:
//
//   <header class="topbar" id="appTopbar"
//           data-links='[{"href":"dashboard.html","label":"Volver al panel"}]'></header>
//
// Muestra el selector de comercio (una persona puede estar en varios).
//
// El botón "Cerrar sesión" lo pone esta barra siempre, dentro del menú de la
// cuenta, con el id logoutBtn de antes: cada página le sigue enganchando su
// listener (window.Auth.logout). Un link a dashboard.html o a lo que ya está en
// el menú de la cuenta no se repite entre las acciones.
//
// Los datos del usuario salen de la sesión guardada (window.Auth) para que la
// barra aparezca completa de entrada; cuando la página trae el usuario fresco
// del backend llama a window.AppTopbar.render(user) y se actualiza.
//
// Cargar DESPUÉS de config.js/auth.js y ANTES del script propio de la página.

(function () {

  function container() {
    return document.getElementById("appTopbar");
  }

  function readLinks(node) {
    try {
      const links = JSON.parse(node.dataset.links || "[]");
      return Array.isArray(links) ? links : [];
    } catch {
      console.error("topbar: data-links no es JSON válido");
      return [];
    }
  }

  // El menú de la cuenta: lo personal, fuera de las acciones de la página.
  const ACCOUNT_LINKS = [
    { href: "organizacion.html", label: "Mi cuenta" },
    { href: "mi_actividad.html", label: "Mi actividad" },
    { href: "ayuda.html", label: "Ayuda" },
  ];
  const ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V20h14V9.5" /><path d="M10 20v-6h4v6" />',
    chevron: '<polyline points="6 9 12 15 18 9" />',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" />',
  };

  function icon(name) {
    return `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
  }

  const here = () => location.pathname.split("/").pop() || "index.html";
  const samePage = (href) => href.split("#")[0] === here();

  function build(node, user) {
    const email = (user && user.email) || "—";
    const name = (user && (user.display_name || user.full_name)) || (user && user.email) || "Usuario";
    // La foto que subió (perfil personal) gana sobre la de Google.
    const picture = user && (user.photo || user.picture);

    node.replaceChildren();
    node.classList.add("app-topbar");

    // Izquierda: volver al panel y el comercio en el que trabaja ESTA pestaña
    // (renderTeamSwitcher, utils.js; apps/accounts/teams.py). Algunas páginas
    // cargan esta barra antes que utils.js: entonces se dibuja al terminar
    // de cargar.
    const left = document.createElement("div");
    left.className = "topbar-left";
    const home = document.createElement("a");
    home.className = "topbar-home";
    home.href = "dashboard.html";
    // La marca del producto (Human Log · Rótulos) es también el camino al panel.
    home.title = "Human Log · Rótulos — volver al panel";
    home.setAttribute("aria-label", "Rótulos: volver al panel");
    home.innerHTML = `<img src="assets/images/favicon.svg" alt="" width="26" height="26"><span>Rótulos</span>`;
    left.appendChild(home);
    const teamBox = document.createElement("div");
    teamBox.className = "topbar-team";
    left.appendChild(teamBox);
    node.appendChild(left);
    const drawSwitcher = () => {
      if (typeof renderTeamSwitcher === "function") renderTeamSwitcher(teamBox, user);
    };
    if (typeof renderTeamSwitcher === "function") drawSwitcher();
    else document.addEventListener("DOMContentLoaded", drawSwitcher, { once: true });

    // Medio: las acciones de la página (data-links). El panel ya tiene su
    // botón y lo de la cuenta está en su menú, así que esos no se repiten.
    // Los links vienen del HTML de la página, no del backend: igual se
    // insertan con textContent y href directo, nunca con innerHTML. Un link
    // con "perm" solo aparece si la sesión tiene ese permiso (el backend
    // vuelve a chequear al entrar; esto solo evita ofrecer de más).
    const permissions = (user && Array.isArray(user.permissions) && user.permissions) || null;
    const skip = new Set(["dashboard.html", ...ACCOUNT_LINKS.map((link) => link.href)]);
    const actions = document.createElement("nav");
    actions.className = "topbar-actions";
    actions.setAttribute("aria-label", "Acciones de esta página");
    readLinks(node).forEach((link) => {
      if (!link || !link.href || skip.has(link.href.split("#")[0])) return;
      if (link.perm && permissions && !permissions.includes(link.perm)) return;
      const anchor = document.createElement("a");
      anchor.className = "topbar-link";
      anchor.href = link.href;
      anchor.textContent = link.label || link.href;
      if (link.id) anchor.id = link.id;
      actions.appendChild(anchor);
    });
    node.appendChild(actions);

    // Derecha: la cuenta. "Cerrar sesión" conserva el id logoutBtn: cada
    // página le sigue enganchando su listener (window.Auth.logout).
    const account = document.createElement("div");
    account.className = "topbar-account";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "topbar-account-btn";
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.title = `${name} · ${email}`;
    const avatar = document.createElement("img");
    avatar.className = "avatar";
    avatar.id = "userAvatar";
    avatar.alt = "";
    // Sin foto, las iniciales de personAvatarUrl (utils.js). En muchas páginas
    // utils.js carga DESPUÉS que esta barra: entonces se pone al terminar de leer la página.
    const drawAvatar = () => { avatar.src = picture || personAvatarUrl({ ...(user || {}), display_name: name }); };
    if (picture || typeof personAvatarUrl === "function") drawAvatar();
    else document.addEventListener("DOMContentLoaded", drawAvatar, { once: true });
    const nameEl = document.createElement("span");
    nameEl.className = "topbar-user-name";
    nameEl.id = "userName";
    nameEl.textContent = name;
    trigger.append(avatar, nameEl);
    trigger.insertAdjacentHTML("beforeend", `<span class="topbar-chevron">${icon("chevron")}</span>`);

    const menu = document.createElement("div");
    menu.className = "topbar-menu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    const who = document.createElement("div");
    who.className = "topbar-menu-who";
    const whoName = document.createElement("strong");
    whoName.textContent = name;
    const emailEl = document.createElement("span");
    emailEl.id = "userEmail";
    emailEl.textContent = email;
    who.append(whoName, emailEl);
    menu.appendChild(who);
    ACCOUNT_LINKS.forEach((link) => {
      const anchor = document.createElement("a");
      anchor.href = link.href;
      anchor.textContent = link.label;
      anchor.setAttribute("role", "menuitem");
      if (samePage(link.href)) anchor.setAttribute("aria-current", "page");
      menu.appendChild(anchor);
    });
    const logout = document.createElement("button");
    logout.className = "topbar-menu-logout";
    logout.id = "logoutBtn";
    logout.type = "button";
    logout.setAttribute("role", "menuitem");
    logout.innerHTML = `${icon("logout")}<span>Cerrar sesión</span>`;
    // La barra se vuelve a dibujar cuando la página trae el usuario fresco, y
    // el listener que la página le había puesto al botón viejo se perdía: el
    // botón cerraba la sesión solo a veces. Ahora lo hace la barra misma (el
    // listener de la página, si quedó en este botón, no se ejecuta además).
    logout.addEventListener("click", (event) => {
      event.stopImmediatePropagation();
      if (window.Auth && typeof window.Auth.logout === "function") window.Auth.logout();
    });
    menu.appendChild(logout);

    const setOpen = (open) => {
      menu.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
      account.classList.toggle("is-open", open);
    };
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      setOpen(menu.hidden);
      if (!menu.hidden) menu.querySelector("a, button")?.focus();
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!account.contains(event.target)) setOpen(false);
    });
    account.append(trigger, menu);
    node.appendChild(account);
  }

  function render(user) {
    const node = container();
    if (!node) return;
    build(node, user || (window.Auth && window.Auth.getCurrentUser()) || {});
  }

  window.AppTopbar = { render };
  // La marca en la pestaña: "Mis pedidos · Human Log Rótulos".
  if (!document.title.includes("Human Log")) document.title = `${document.title} · Human Log Rótulos`;
  render();
})();
