// Sesión compartida del frontend: quién está logueado, apiFetch con
// renovación automática del access token vía refresh (en vez de expulsar al
// usuario en cualquier 401) y logout. Se carga como script CLÁSICO antes que
// el script propio de cada página, porque casi todo el frontend son scripts
// clásicos (no módulos) — expone su API en window.Auth.
// assets/js/labels_api.js es un módulo ES y no puede "cargarse antes" en el
// mismo sentido, pero lee window.Auth igual (ver ese archivo).
//
// Requiere que assets/js/config.js se haya cargado antes (window.APP_CONFIG).
(function () {
  "use strict";

  // Rutas de redirección resueltas relativas a la ubicación de ESTE
  // archivo (no a la página que lo carga): así funciona igual sin importar
  // desde qué profundidad de frontend/ se cargue la página.
  const SCRIPT_URL = document.currentScript && document.currentScript.src;
  const INDEX_URL = SCRIPT_URL
    ? new URL("../../index.html", SCRIPT_URL).href
    : "index.html";
  const CHANGE_PASSWORD_URL = SCRIPT_URL
    ? new URL("../../cambiar-password.html", SCRIPT_URL).href
    : "cambiar-password.html";

  const AUTH_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
  const REFRESH_URL = `${AUTH_BASE}/refresh/`;
  const LOGOUT_URL = `${AUTH_BASE}/logout/`;
  const ME_URL = `${AUTH_BASE}/me/`;

  // La sesión viaja en cookies HttpOnly que pone el backend
  // (backend/apps/accounts/auth_cookies.py): este código nunca ve los tokens.
  // Cada request a la API va con `credentials: "include"` (la API es otro
  // origen en desarrollo) y con X-Auth-Mode: cookie, que le dice al backend
  // que use cookies en vez de devolver los tokens en el JSON — y que además
  // es la defensa contra requests falsificados desde otro sitio.
  // En localStorage solo queda `user` (nombre, rol, permisos: no es secreto),
  // que es también la marca de "hay sesión" para las páginas.
  const AUTH_MODE_HEADERS = { "X-Auth-Mode": "cookie" };

  function withSession(options = {}) {
    return {
      ...options,
      credentials: "include",
      headers: { ...(options.headers || {}), ...AUTH_MODE_HEADERS },
    };
  }

  function getCurrentUser() {
    try {
      return JSON.parse(localStorage.getItem("user") || "{}");
    } catch {
      return {};
    }
  }

  // Si hay sesión, del lado de esta página: el backend la confirma en cada
  // request (y apiFetch manda al login si no).
  function hasSession() {
    return Boolean(getCurrentUser().id);
  }

  // Lo que hace cada login (index.html, register.html) con la respuesta: las
  // cookies ya las dejó el navegador; acá solo se guarda quién es.
  function saveSession(user) {
    localStorage.setItem("user", JSON.stringify(user || {}));
  }

  function clearSession() {
    localStorage.removeItem("user");
    // Restos de cuando los tokens vivían acá.
    localStorage.removeItem("access");
    localStorage.removeItem("refresh");
  }

  function redirectToLogin() {
    window.location.replace(INDEX_URL);
  }

  // Una sola renovación concurrente: si varias llamadas reciben 401 al mismo
  // tiempo, todas esperan esta misma promesa en vez de disparar un refresh
  // cada una. El refresh viaja en su cookie; el backend devuelve las dos
  // cookies nuevas (rotación).
  let refreshPromise = null;

  function refreshSession(body = {}) {
    if (refreshPromise) return refreshPromise;

    refreshPromise = fetch(
      REFRESH_URL,
      withSession({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    )
      .then((response) => response.ok)
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });

    return refreshPromise;
  }

  // Una sesión de antes del cambio tiene los tokens en localStorage: se
  // canjea UNA vez su refresh por las cookies y se borran de acá, así nadie
  // tiene que volver a loguearse.
  const legacyRefresh = localStorage.getItem("refresh");
  const migration = legacyRefresh
    ? refreshSession({ refresh: legacyRefresh }).finally(() => {
        localStorage.removeItem("access");
        localStorage.removeItem("refresh");
      })
    : Promise.resolve();

  // apiFetch(url, options):
  // - Manda las cookies de sesión (withSession).
  // - Ante un 401, intenta UNA renovación y reintenta la request original
  //   una sola vez. Si el refresh falla, recién ahí limpia la sesión y
  //   redirige al login. Un 401 en la propia llamada de refresh nunca
  //   reintenta (evita el bucle).
  // - Ante un 403 con must_change_password, redirige a cambiar-password.html.
  async function apiFetch(url, options = {}) {
    await migration;
    const isRefreshCall = url === REFRESH_URL;
    const doFetch = () => fetch(url, withSession(options));

    let response = await doFetch();

    if (response.status === 401 && !isRefreshCall) {
      const refreshed = await refreshSession();
      if (refreshed) {
        response = await doFetch();
      }
      if (!refreshed || response.status === 401) {
        clearSession();
        redirectToLogin();
        const sessionError = new Error("Sesión expirada");
        sessionError.isSessionExpired = true;
        throw sessionError;
      }
    }

    if (response.status === 403) {
      const body = await response.clone().json().catch(() => ({}));
      if (body && body.must_change_password) {
        window.location.replace(CHANGE_PASSWORD_URL);
        const pendingError = new Error("Cambio de contraseña pendiente");
        pendingError.isSessionExpired = true;
        throw pendingError;
      }
    }

    return response;
  }

  // Revoca el refresh en el backend y borra las cookies (best-effort), limpia
  // lo local y redirige al login.
  async function logout() {
    try {
      // Por apiFetch: si el access ya venció, renueva y revoca igual.
      await apiFetch(LOGOUT_URL, { method: "POST" });
    } catch (err) {
      if (!err.isSessionExpired) console.warn("No se pudo cerrar la sesión en el backend:", err);
    }
    clearSession();
    redirectToLogin();
  }

  // Los permisos se guardan al iniciar sesión: si el admin cambia un rol en
  // roles.html (o una migración agrega un permiso), la sesión no se enteraba
  // hasta volver a entrar y la pantalla ofrecía de más o de menos. Al abrir
  // una página se releen de me/ en segundo plano, como mucho cada 2 minutos;
  // si cambiaron se avisa con "auth:user-updated" (la barra del admin se
  // redibuja). En el login y el registro no: ahí no hay sesión que refrescar.
  const USER_REFRESH_MS = 2 * 60 * 1000;
  const NO_REFRESH_PAGES = /\/(index|register|logoutpage)\.html$|\/$/;

  function refreshStoredUser() {
    if (!hasSession() || NO_REFRESH_PAGES.test(window.location.pathname)) return;
    let last = 0;
    try {
      last = Number(sessionStorage.getItem("userCheckedAt") || 0);
      if (Date.now() - last < USER_REFRESH_MS) return;
      sessionStorage.setItem("userCheckedAt", String(Date.now()));
    } catch {
      // Sin sessionStorage se relee en cada página: sigue funcionando.
    }
    apiFetch(ME_URL)
      .then((response) => (response.ok ? response.json() : null))
      .then((fresh) => {
        const stored = getCurrentUser();
        if (!fresh || String(fresh.id) !== String(stored.id)) return;
        const merged = { ...stored, ...fresh };
        if (JSON.stringify(merged) === JSON.stringify(stored)) return;
        saveSession(merged);
        window.dispatchEvent(new CustomEvent("auth:user-updated", { detail: merged }));
      })
      .catch(() => {});
  }

  window.Auth = {
    hasSession,
    saveSession,
    withSession,
    getCurrentUser,
    clearSession,
    logout,
    apiFetch,
  };

  refreshStoredUser();
})();
