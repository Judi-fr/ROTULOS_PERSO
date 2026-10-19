// Perfiles del equipo (perfiles_equipo.html, /team/profiles/): conjuntos de
// accesos con nombre. Cambiar uno cambia los accesos de todos los que lo
// tienen; un perfil en uso no se puede borrar (lo dice el backend).

const API_BASE = window.APP_CONFIG.API_BASE;
const TEAM_URL = `${API_BASE}/team/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let catalog = {};
let myAccesses = [];

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

function resetForm() {
  document.getElementById("profileForm").reset();
  document.getElementById("profileId").value = "";
  document.getElementById("profileFormTitle").textContent = "Nuevo perfil";
  document.getElementById("profileSubmit").textContent = "Crear perfil";
  document.getElementById("profileCancel").hidden = true;
  renderAccessCheckboxes(document.getElementById("profileAccess"), catalog, [], { allowed: myAccesses });
}

function startEdit(profile) {
  document.getElementById("profileId").value = profile.id;
  document.getElementById("profileName").value = profile.name;
  document.getElementById("profileFormTitle").textContent = `Editar «${profile.name}»`;
  document.getElementById("profileSubmit").textContent = "Guardar";
  document.getElementById("profileCancel").hidden = false;
  renderAccessCheckboxes(document.getElementById("profileAccess"), catalog, profile.accesses, { allowed: myAccesses });
  document.getElementById("profileName").focus();
}

function render(profiles) {
  const list = document.getElementById("profileList");
  list.innerHTML = "";
  profiles.forEach((profile) => {
    const item = document.createElement("li");
    item.className = "team-item";
    const info = document.createElement("div");
    info.innerHTML = "<strong></strong><div class='access-muted'></div><div class='access-muted'></div>";
    info.querySelector("strong").textContent = profile.name;
    const [accessLine, countLine] = info.querySelectorAll(".access-muted");
    accessLine.textContent = profile.accesses.map((key) => catalog[key] || key).join(", ") || "Sin accesos";
    countLine.textContent =
      profile.member_count === 1 ? "1 persona lo tiene" : `${profile.member_count} personas lo tienen`;
    const actions = document.createElement("div");
    actions.className = "team-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn btn-outline btn-small";
    edit.textContent = "Editar";
    edit.addEventListener("click", () => startEdit(profile));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-outline btn-small";
    remove.textContent = "Borrar";
    remove.addEventListener("click", () => removeProfile(profile));
    actions.append(edit, remove);
    item.append(info, actions);
    list.appendChild(item);
  });
}

async function load() {
  try {
    const response = await apiFetch(TEAM_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron leer los perfiles."));
    catalog = data.accesses;
    myAccesses = data.me.accesses;
    render(data.profiles);
    if (!document.getElementById("profileId").value) resetForm();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function save(event) {
  event.preventDefault();
  const id = document.getElementById("profileId").value;
  const body = {
    name: document.getElementById("profileName").value.trim(),
    accesses: readAccessCheckboxes(document.getElementById("profileAccess")),
  };
  try {
    const saved = await send(
      id ? `${TEAM_URL}profiles/${id}/` : `${TEAM_URL}profiles/`,
      id ? "PATCH" : "POST",
      body,
      "No se pudo guardar el perfil."
    );
    document.getElementById("profileId").value = "";
    await load();
    showMessage(`Perfil «${saved.name}» ${id ? "guardado" : "creado"}.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function removeProfile(profile) {
  if (!window.confirm(`¿Borrar el perfil «${profile.name}»?`)) return;
  try {
    await send(`${TEAM_URL}profiles/${profile.id}/`, "DELETE", null, "No se pudo borrar el perfil.");
    await load();
    showMessage(`Perfil «${profile.name}» borrado.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function bootstrap() {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  document.getElementById("profileForm").addEventListener("submit", save);
  document.getElementById("profileCancel").addEventListener("click", resetForm);
  load();
}

bootstrap();
