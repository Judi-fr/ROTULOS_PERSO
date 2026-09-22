// Barra superior compartida (avatar, nombre, email y acciones).
//
// Estaba copiada en cada página: el mismo bloque HTML en 12 archivos y la
// misma función renderTopbar en 11 .js. Acá se escribe una sola vez; cada
// página solo declara sus links propios:
//
//   <header class="topbar" id="appTopbar"
//           data-links='[{"href":"dashboard.html","label":"Volver al panel"}]'></header>
//
// El botón "Cerrar sesión" lo pone esta barra siempre, con el id logoutBtn
// de antes: cada página le sigue enganchando su listener (window.Auth.logout).
//
// Los datos del usuario salen de la sesión guardada (window.Auth) para que la
// barra aparezca completa de entrada; cuando la página trae el usuario fresco
// del backend llama a window.AppTopbar.render(user) y se actualiza.
//
// Cargar DESPUÉS de config.js/auth.js y ANTES del script propio de la página.

(function () {
  const AVATAR_FALLBACK = "https://api.dicebear.com/7.x/avataaars/svg?seed=";

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

  function build(node, user) {
    const email = (user && user.email) || "—";
    const name = (user && (user.display_name || user.full_name)) || (user && user.email) || "Usuario";
    const picture = user && user.picture;

    node.replaceChildren();

    const userBox = document.createElement("div");
    userBox.className = "topbar-user";

    const avatar = document.createElement("img");
    avatar.className = "avatar";
    avatar.id = "userAvatar";
    avatar.alt = "";
    avatar.src = picture || `${AVATAR_FALLBACK}${encodeURIComponent(email)}`;
    userBox.appendChild(avatar);

    const info = document.createElement("div");
    info.className = "topbar-user-info";
    const nameEl = document.createElement("span");
    nameEl.className = "topbar-user-name";
    nameEl.id = "userName";
    nameEl.textContent = name;
    const emailEl = document.createElement("span");
    emailEl.className = "topbar-user-email";
    emailEl.id = "userEmail";
    emailEl.textContent = email;
    info.appendChild(nameEl);
    info.appendChild(emailEl);
    userBox.appendChild(info);
    node.appendChild(userBox);

    const actions = document.createElement("div");
    actions.className = "topbar-actions";
    // Los links vienen del HTML de la página (data-links), no del backend:
    // igual se insertan con textContent y href directo, nunca con innerHTML.
    readLinks(node).forEach((link) => {
      if (!link || !link.href) return;
      const anchor = document.createElement("a");
      anchor.className = "btn btn-outline";
      anchor.href = link.href;
      anchor.textContent = link.label || link.href;
      if (link.id) anchor.id = link.id;
      actions.appendChild(anchor);
    });

    const logout = document.createElement("button");
    logout.className = "btn btn-outline";
    logout.id = "logoutBtn";
    logout.type = "button";
    logout.textContent = "Cerrar sesión";
    actions.appendChild(logout);
    node.appendChild(actions);
  }

  function render(user) {
    const node = container();
    if (!node) return;
    build(node, user || (window.Auth && window.Auth.getCurrentUser()) || {});
  }

  window.AppTopbar = { render };
  render();
})();
