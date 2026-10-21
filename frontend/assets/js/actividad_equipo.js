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

function renderPager(data, page) {
  const pager = document.getElementById("pager");
  pager.innerHTML = "";
  const add = (text, target) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-outline btn-small";
    btn.textContent = text;
    btn.addEventListener("click", () => load(target));
    pager.appendChild(btn);
  };
  if (data.previous) add("Anterior", page - 1);
  if (data.next) add("Siguiente", page + 1);
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
      [formatDateTimeShort(row.created_at), row.actor_email, row.action_label || row.action, row.target_repr || ""].forEach(
        (text) => {
          const td = document.createElement("td");
          td.textContent = text;
          tr.appendChild(td);
        }
      );
      body.appendChild(tr);
    });
    renderPager(data, page);
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
    option.textContent = member.name ? `${member.name} (${member.email})` : member.email;
    select.appendChild(option);
  });
}

function bootstrap() {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  document.getElementById("filterForm").addEventListener("submit", (event) => {
    event.preventDefault();
    load(1);
  });
  loadMembers();
  load(1);
}

bootstrap();
})();
