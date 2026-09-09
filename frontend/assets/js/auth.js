// Sesión compartida del frontend: acceso a los tokens, apiFetch con
// renovación automática del access token vía refresh (en vez de expulsar al
// usuario en cualquier 401) y logout. Se carga como script CLÁSICO antes que
// el script propio de cada página, porque casi todo el frontend son scripts
// clásicos (no módulos) — expone su API en window.Auth.
// pedidos/api.js es un módulo ES y no puede "cargarse antes" en el mismo
// sentido, pero lee window.Auth igual (ver ese archivo).
//
// Requiere que assets/js/config.js se haya cargado antes (window.APP_CONFIG).
(function () {
  "use strict";

  // Rutas de redirección resueltas relativas a la ubicación de ESTE
  // archivo (no a la página que lo carga), mismo criterio que
  // pedidos/api.js con import.meta.url: así funciona igual sin importar si
  // la página está en la raíz de frontend/ o un nivel más abajo (pedidos/).
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

  function getAccessToken() {
    return localStorage.getItem("access") || "";
  }

  function getRefreshToken() {
    return localStorage.getItem("refresh") || "";
  }

  function getCurrentUser() {
    try {
      return JSON.parse(localStorage.getItem("user") || "{}");
    } catch {
      return {};
    }
  }

  function clearSession() {
    localStorage.removeItem("access");
    localStorage.removeItem("refresh");
    localStorage.removeItem("user");
  }

  function redirectToLogin() {
    window.location.replace(INDEX_URL);
  }

  // Una sola renovación concurrente: si varias llamadas reciben 401 al mismo
  // tiempo, todas esperan esta misma promesa en vez de disparar un refresh
  // cada una. Se guarda en el módulo (closure) y se limpia al terminar.
  let refreshPromise = null;

  function refreshSession() {
    if (refreshPromise) return refreshPromise;

    const refresh = getRefreshToken();
    if (!refresh) return Promise.resolve(false);

    refreshPromise = fetch(REFRESH_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
    })
      .then(async (response) => {
        if (!response.ok) return false;
        const data = await response.json().catch(() => ({}));
        if (!data.access) return false;
        localStorage.setItem("access", data.access);
        // Con ROTATE_REFRESH_TOKENS el backend manda un refresh nuevo.
        if (data.refresh) localStorage.setItem("refresh", data.refresh);
        return true;
      })
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });

    return refreshPromise;
  }

  // apiFetch(url, options):
  // - Agrega Authorization: Bearer <access>.
  // - Ante un 401, intenta UNA renovación del access token y reintenta la
  //   request original una sola vez. Si el refresh falla (o no hay refresh
  //   guardado), recién ahí limpia la sesión y redirige al login. Un 401 en
  //   la propia llamada de refresh nunca reintenta (evita el bucle).
  // - Ante un 403 con must_change_password, redirige a cambiar-password.html
  //   (comportamiento previo, sin cambios).
  async function apiFetch(url, options = {}) {
    const isRefreshCall = url === REFRESH_URL;

    const doFetch = () => {
      const headers = {
        ...(options.headers || {}),
        Authorization: `Bearer ${getAccessToken()}`,
      };
      return fetch(url, { ...options, headers });
    };

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

  // Invalida el refresh token en el backend (best-effort), limpia la sesión
  // local y redirige al login. Mismo flujo que estaba duplicado en cada
  // página (dashboard.js, pedidos.js, admingestion_test.js, ...).
  async function logout() {
    const refresh = getRefreshToken();
    if (refresh) {
      try {
        await fetch(LOGOUT_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${getAccessToken()}`,
          },
          body: JSON.stringify({ refresh }),
        });
      } catch (err) {
        console.warn("No se pudo invalidar el refresh token en el backend:", err);
      }
    }
    clearSession();
    redirectToLogin();
  }

  window.Auth = {
    getAccessToken,
    getRefreshToken,
    getCurrentUser,
    clearSession,
    logout,
    apiFetch,
  };
})();
