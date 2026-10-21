// Perfiles del equipo (pestaña Perfiles de equipo.html, /team/profiles/): conjuntos de
// accesos con nombre. Cambiar uno cambia los accesos de todos los que lo
// tienen; un perfil en uso no se puede borrar (lo dice el backend). Desde
// 2026-10-21 se editan con createProfileEditor (utils.js), el mismo diseño
// que roles.html; acá solo se ponen los datos y las llamadas a la API.

// Encerrado en una función: comparte la página con equipo.js, que declara
// los mismos nombres (API_BASE, load, send...).
(function () {
const API_BASE = window.APP_CONFIG.API_BASE;
const TEAM_URL = `${API_BASE}/team/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let editor = null;

async function send(url, method, body, fallback) {
  const response = await apiFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(getErrorMessage(data, fallback));
  return data;
}

async function load() {
  try {
    const response = await apiFetch(TEAM_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron leer los perfiles."));
    if (!editor) {
      // Quien no es el dueño solo puede dar los accesos que tiene: el resto
      // aparece sin poder tildarse (el backend lo rechaza igual).
      editor = createProfileEditor(document.getElementById("profileEditor"), {
        catalog: data.accesses,
        allowed: data.me.accesses,
        save,
        remove,
      });
    }
    editor.update(data.profiles);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function save(profile, body) {
  const saved = await send(
    profile ? `${TEAM_URL}profiles/${profile.id}/` : `${TEAM_URL}profiles/`,
    profile ? "PATCH" : "POST",
    body,
    "No se pudo guardar el perfil."
  );
  await load();
  document.dispatchEvent(new CustomEvent("team:profiles-changed"));
  return saved;
}

async function remove(profile) {
  await send(`${TEAM_URL}profiles/${profile.id}/`, "DELETE", null, "No se pudo borrar el perfil.");
  await load();
  document.dispatchEvent(new CustomEvent("team:profiles-changed"));
}

function bootstrap() {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  load();
}

bootstrap();
})();
