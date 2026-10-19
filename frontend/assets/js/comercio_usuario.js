// Comercio de un usuario (comercio_usuario.html?user=<id>, admin): ver en qué
// comercio está y moverlo a otro (GET/POST /users/<id>/team/, GET /teams/).
// El backend aplica la regla del comercio vacío y audita el cambio.

// apiFetch viene de admin_common.js, como en las demás páginas de admin.
const API_BASE = window.APP_CONFIG.API_BASE;
const userId = new URLSearchParams(window.location.search).get("user");

async function loadCurrent() {
  const target = document.getElementById("currentTeam");
  try {
    const response = await apiFetch(`${API_BASE}/users/${userId}/team/`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer el comercio del usuario."));
    target.textContent =
      `${data.user.email} es ${data.role === "owner" ? "dueño" : "empleado"} de «${data.team.name}» ` +
      `(dueño: ${data.team.owner_email}).`;
  } catch (err) {
    if (err.isSessionExpired) return;
    target.textContent = err.message;
  }
}

async function search(event) {
  if (event) event.preventDefault();
  const term = document.getElementById("teamSearch").value.trim();
  const select = document.getElementById("teamSelect");
  try {
    const response = await apiFetch(`${API_BASE}/teams/?search=${encodeURIComponent(term)}`);
    const data = await response.json().catch(() => []);
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron buscar comercios."));
    select.innerHTML = data.length ? "" : '<option value="">No hay comercios con esa búsqueda</option>';
    data.forEach((team) => {
      const option = document.createElement("option");
      option.value = team.id;
      option.textContent = `${team.name} — ${team.owner_email} (${team.member_count} miembro/s)`;
      select.appendChild(option);
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function assign(event) {
  event.preventDefault();
  const teamId = document.getElementById("teamSelect").value;
  const role = document.getElementById("roleSelect").value;
  if (!teamId) {
    showMessage("Elegí un comercio.");
    return;
  }
  const label = document.getElementById("teamSelect").selectedOptions[0].textContent;
  if (!window.confirm(`¿Mover al usuario a ${label} como ${role === "owner" ? "dueño" : "empleado"}?`)) return;
  try {
    const response = await apiFetch(`${API_BASE}/users/${userId}/team/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id: Number(teamId), role }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo mover al usuario."));
    showMessage(`Listo: ahora es ${data.role === "owner" ? "dueño" : "empleado"} de «${data.team.name}».`, "success");
    loadCurrent();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function bootstrap() {
  if (!canUseUserPermission("users.edit") || !userId) {
    window.location.replace("gestionuser.html");
    return;
  }
  document.getElementById("searchForm").addEventListener("submit", search);
  document.getElementById("assignForm").addEventListener("submit", assign);
  loadCurrent();
  search();
}

bootstrap();
