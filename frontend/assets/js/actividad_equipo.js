// Actividad del equipo (pestaña Actividad de equipo.html, GET /team/activity/): qué hizo
// cada persona del comercio. El backend ya recorta a los miembros del
// comercio y a lo del comercio (nunca logins ni cosas de la plataforma).

// Encerrado en una función: comparte la página con equipo.js, que declara
// los mismos nombres (API_BASE, load, send...).
(function () {
const API_BASE = window.APP_CONFIG.API_BASE;
const TEAM_URL = `${API_BASE}/team/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

function query(page) {
  const params = new URLSearchParams();
  const actor = document.getElementById("filterActor").value;
  const search = document.getElementById("filterSearch").value.trim();
  const from = document.getElementById("filterFrom").value;
  const to = document.getElementById("filterTo").value;
  if (actor) params.set("actor", actor);
  if (search) params.set("search", search);
  if (from) params.set("date_from", from);
  if (to) params.set("date_to", to);
  if (page > 1) params.set("page", page);
  return params.toString();
}

// Quién lo hizo: su foto y su nombre para mostrar (row.actor, el perfil
// personal de un miembro actual); de alguien que ya no está, solo el email.
function actorCell(row) {
  const wrap = document.createElement("span");
  wrap.className = "activity-actor";
  const actor = row.actor;
  if (!actor) {
    wrap.textContent = row.actor_email || "—";
    return wrap;
  }
  const name = document.createElement("span");
  name.textContent = actor.display_name || row.actor_email;
  name.title = [row.actor_email, actor.job_title].filter(Boolean).join(" · ");
  wrap.append(personAvatar({ ...actor, email: row.actor_email }, "person-avatar person-avatar-small"), name);
  return wrap;
}

async function load(page = 1) {
  const body = document.getElementById("activityBody");
  try {
    const response = await apiFetch(`${TEAM_URL}activity/?${query(page)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer la actividad."));
    const rows = extractResults(data);
    body.innerHTML = rows.length ? "" : '<tr><td colspan="4" class="access-muted">No hay movimientos con esos filtros.</td></tr>';
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      [formatDateTimeShort(row.created_at), null, row.action_label || row.action, row.target_repr || ""].forEach(
        (text) => {
          const td = document.createElement("td");
          if (text === null) td.appendChild(actorCell(row));
          else td.textContent = text;
          tr.appendChild(td);
        }
      );
      body.appendChild(tr);
    });
    renderPrevNextPager(document.getElementById("pager"), data, page, load);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function formatDateTimeShort(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("es-AR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function loadMembers() {
  // Las personas del filtro salen del equipo (GET /team/, mismo acceso).
  const response = await apiFetch(TEAM_URL);
  if (!response.ok) return;
  const data = await response.json().catch(() => ({ members: [] }));
  const select = document.getElementById("filterActor");
  data.members.forEach((member) => {
    const option = document.createElement("option");
    option.value = member.user_id;
    const shown = member.display_name || member.name;
    option.textContent = shown && shown !== member.email ? `${shown} (${member.email})` : member.email;
    select.appendChild(option);
  });
}

function bootstrap() {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  // Pestaña solo para quien maneja el equipo (data-perm en equipo.html).
  if (!sessionHasPermission("team.manage")) return;
  document.getElementById("filterForm").addEventListener("submit", (event) => {
    event.preventDefault();
    load(1);
  });
  loadMembers();
  load(1);
}

bootstrap();
})();
