// Comercios de un usuario (comercio_usuario.html?user=<id>, admin): ver en
// qué comercios está y sumarlo a otro (GET/POST /users/<id>/team/, GET
// /teams/). Sumarlo no lo saca de los que ya tiene; el backend audita.

// apiFetch viene de admin_common.js, como en las demás páginas de admin.
const API_BASE = window.APP_CONFIG.API_BASE;
const userId = new URLSearchParams(window.location.search).get("user");

async function loadCurrent() {
  const target = document.getElementById("currentTeam");
  try {
    const response = await apiFetch(`${API_BASE}/users/${userId}/team/`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer el comercio del usuario."));
    const teams = data.teams || [];
    target.textContent = teams.length
      ? `${data.user.email} está en: ` +
        teams
          .map((team) => `«${team.name}» como ${team.role === "owner" ? "dueño" : "empleado"} (dueño: ${team.owner_email})`)
          .join("; ") + "."
      : `${data.user.email} todavía no está en ningún comercio.`;
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
  if (!await appConfirm(`¿Sumar al usuario a ${label} como ${role === "owner" ? "dueño" : "empleado"}?`, { confirmLabel: "Sumar" })) return;
  try {
    const response = await apiFetch(`${API_BASE}/users/${userId}/team/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id: Number(teamId), role }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo sumar al usuario."));
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
