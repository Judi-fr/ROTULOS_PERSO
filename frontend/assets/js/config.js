// Config central del frontend. Cargar SIEMPRE antes que cualquier otro
// script del frontend (assets/js/auth.js incluido): todos los demás
// archivos arman sus URLs sobre window.APP_CONFIG.API_BASE en vez de
// hardcodear el host/puerto del backend.
window.APP_CONFIG = {
  API_BASE: "http://127.0.0.1:8000/api/v1",
};
