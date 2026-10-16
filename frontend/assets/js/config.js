// Config central del frontend. Cargar SIEMPRE antes que cualquier otro
// script del frontend (assets/js/auth.js incluido): todos los demás
// archivos arman sus URLs sobre window.APP_CONFIG.API_BASE en vez de
// hardcodear el host/puerto del backend.
//
// Abierto desde esta máquina (localhost o el archivo directo) la API es la
// del runserver local. Abierto desde otro dominio (el túnel, que publica el
// frontend y la API bajo el mismo host) la API es la del mismo origen: un
// 127.0.0.1 ahí haría que el navegador de otra persona llame a SU máquina.
//
// En local la API usa el MISMO nombre de host que la página (localhost con
// localhost, 127.0.0.1 con 127.0.0.1): la sesión viaja en cookies
// (backend/apps/accounts/auth_cookies.py) y el navegador trata a
// localhost:8001 y 127.0.0.1:8000 como sitios distintos, así que no las
// mandaría. Abierto como archivo (file://) no hay host: se usa 127.0.0.1,
// pero el login no anda así — hay que servir frontend/ (ver CLAUDE.md).
(function () {
  const host = window.location.hostname;
  const isLocal = !host || host === "localhost" || host === "127.0.0.1" || host === "[::1]";
  const localHost = host || "127.0.0.1";
  window.APP_CONFIG = {
    API_BASE: isLocal ? `http://${localHost}:8000/api/v1` : `${window.location.origin}/api/v1`,
  };
})();
