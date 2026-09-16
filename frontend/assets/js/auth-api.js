// Cliente de la API de autenticación (apps.accounts).
//
// Lo comparten index.html, register.html, recuperar-password.html y
// reset-password.html: las cuatro hablan con los mismos endpoints y traducen
// los mismos errores, así que el helper vive una sola vez acá.
//
// La base va con la IP y no con "localhost" a propósito: el navegador
// resuelve localhost a ::1 (IPv6) y runserver escucha solo en IPv4, así que
// con el nombre la llamada muere en ERR_CONNECTION_REFUSED aunque el backend
// esté levantado.
const API = "http://127.0.0.1:8000/api/v1";

// A dónde se entra después de loguear o registrarse. Las rutas de navegación
// son absolutas (desde la raíz que sirve frontend/) porque hay páginas en
// subcarpetas, como pedidos/: una ruta relativa las mandaría a
// pedidos/index.html, que no existe.
const DESTINO = "/plantillas_rotulos.html";
const PAGINA_LOGIN = "/index.html";

// El access token se vence en minutos; el refresh es el que después permite
// renovarlo sin volver a pedir la clave. Por eso se guardan los dos.
function guardarSesion(data) {
  localStorage.setItem("access", data.access);
  localStorage.setItem("refresh", data.refresh);
  localStorage.setItem("user", JSON.stringify(data.user));
}

// Resultado de la última operación, en la caja #mensaje de la página. Verde
// para lo que salió bien, rojo para lo que no: pedir el link de recuperación
// responde 200 con un texto que hay que leer, y no es un error.
function mostrarMensaje(texto, ok = false) {
  const caja = document.getElementById("mensaje");
  caja.textContent = texto;
  caja.className = ok ? "alert alert-success" : "alert alert-danger";
  caja.hidden = false;
}

// El backend manda el motivo en `detail`. El 429 se traduce aparte porque
// login y password-reset están limitados a 5 por minuto: sin este mensaje
// parece que la contraseña está mal cuando en realidad te frenó el throttle.
async function motivo(res) {
  if (res.status === 429) return "Demasiados intentos. Esperá un minuto.";
  const cuerpo = await res.json().catch(() => ({}));
  return cuerpo.detail || `Error ${res.status}.`;
}

// POST contra la API. Devuelve los datos si salió bien; si no, muestra el
// motivo y devuelve null, para que cada página decida sola qué hacer después.
async function postear(ruta, cuerpo) {
  try {
    const res = await fetch(API + ruta, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpo),
    });
    if (!res.ok) {
      mostrarMensaje(await motivo(res));
      return null;
    }
    return await res.json();
  } catch {
    // fetch solo tira excepción si no hubo respuesta: backend caído o CORS.
    mostrarMensaje("No se pudo conectar con el servidor.");
    return null;
  }
}

// Deshabilita el botón mientras la petición está en vuelo. Sin esto, dos
// clics seguidos gastan dos de los 5 intentos por minuto que permite el
// throttle, y el segundo suele dar un 429 confuso.
async function conBoton(boton, textoEnEspera, tarea) {
  const original = boton.value || boton.textContent;
  boton.disabled = true;
  if (boton.value) boton.value = textoEnEspera;
  else boton.textContent = textoEnEspera;
  try {
    await tarea();
  } finally {
    boton.disabled = false;
    if (boton.value) boton.value = original;
    else boton.textContent = original;
  }
}

// Escapa texto que viene del servidor antes de meterlo con innerHTML. Nombres
// de usuario, de archivo y de plantilla los escribe cualquiera que tenga
// cuenta: sin esto, un nombre con <img onerror=...> ejecutaría código en el
// navegador de quien lo mira (un administrador, con su token en localStorage).
function esc(texto) {
  return String(texto ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// --- Sesión ---------------------------------------------------------------
//
// Todo lo de acá abajo es para las pantallas que ya requieren estar adentro.
// Las cuatro de autenticación (login, registro, recuperar, reset) no lo usan.

function sesion() {
  const dato = localStorage.getItem("user");
  return dato ? JSON.parse(dato) : null;
}

function borrarSesion() {
  localStorage.removeItem("access");
  localStorage.removeItem("refresh");
  localStorage.removeItem("user");
}

function alLogin() {
  borrarSesion();
  window.location.replace(PAGINA_LOGIN);
}

// Se llama al principio de una página que no tiene sentido sin sesión. Evita
// el parpadeo de ver la pantalla armada y recién después el rebote al login.
function exigirSesion() {
  if (!localStorage.getItem("access")) {
    alLogin();
    return false;
  }
  return true;
}

// Pide un access nuevo usando el refresh. Devuelve true si lo consiguió.
//
// El backend tiene ROTATE_REFRESH_TOKENS activo: la respuesta trae también un
// refresh nuevo y revoca el anterior. Si no se guarda el nuevo, la renovación
// siguiente falla con un token que ya está en la blacklist.
async function renovarAcceso() {
  const refresh = localStorage.getItem("refresh");
  if (!refresh) return false;
  try {
    const res = await fetch(API + "/auth/refresh/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: refresh }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    localStorage.setItem("access", data.access);
    if (data.refresh) localStorage.setItem("refresh", data.refresh);
    return true;
  } catch {
    return false;
  }
}

// fetch con el token puesto. Si el access venció (401), lo renueva UNA vez y
// reintenta; si el segundo intento vuelve a dar 401, el problema no es el
// vencimiento y reintentar sería un bucle, así que se cierra la sesión.
//
// Devuelve la respuesta, o null si la sesión murió o no hubo red: quien llama
// tiene que contemplar el null antes de mirar res.ok.
async function apiFetch(ruta, opciones = {}) {
  const enviar = () =>
    fetch(API + ruta, {
      ...opciones,
      headers: {
        // Solo los cuerpos de texto son JSON. Un FormData (subir una foto) no
        // lleva Content-Type a mano: el navegador lo pone con el boundary del
        // multipart, y pisarlo hace que el backend no encuentre el archivo.
        ...(typeof opciones.body === "string"
          ? { "Content-Type": "application/json" }
          : {}),
        ...(opciones.headers || {}),
        Authorization: "Bearer " + localStorage.getItem("access"),
      },
    });

  try {
    let res = await enviar();
    if (res.status === 401) {
      if (!(await renovarAcceso())) {
        alLogin();
        return null;
      }
      res = await enviar();
    }
    return res;
  } catch {
    return null;
  }
}

// Datos del usuario de la sesión. Es la única fuente que dice si es
// administrador: la respuesta del login trae solo id/email/nombre, sin
// is_staff ni groups.
async function perfil() {
  const res = await apiFetch("/auth/me/");
  if (!res || !res.ok) return null;
  return await res.json();
}

// Cierra la sesión revocando el refresh (el backend lo manda a la blacklist y
// responde 205). El access que ya está en circulación sigue valiendo hasta
// que expire: es inevitable con JWT, por eso su vida es corta.
async function cerrarSesion() {
  const refresh = localStorage.getItem("refresh");
  if (refresh) {
    await apiFetch("/auth/logout/", {
      method: "POST",
      body: JSON.stringify({ refresh: refresh }),
    });
  }
  alLogin();
}
