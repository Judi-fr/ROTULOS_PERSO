// Tu actividad (mi_actividad.html, GET /team/activity/mine/): lo que hizo la
// persona que mira, para cualquier miembro del comercio. La de todo el equipo
// es la pestaña Actividad de equipo.html (acceso "Equipo").

const API_BASE = window.APP_CONFIG.API_BASE;
const MY_ACTIVITY_URL = `${API_BASE}/team/activity/mine/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

function query(page) {
  const params = new URLSearchParams();
  const search = document.getElementById("filterSearch").value.trim();
  const from = document.getElementById("filterFrom").value;
  const to = document.getElementById("filterTo").value;
  if (search) params.set("search", search);
  if (from) params.set("date_from", from);
  if (to) params.set("date_to", to);
  if (page > 1) params.set("page", page);
  return params.toString();
}

function formatDateTimeShort(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("es-AR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function load(page = 1) {
  const body = document.getElementById("activityBody");
  try {
    const response = await apiFetch(`${MY_ACTIVITY_URL}?${query(page)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer tu actividad."));
    const rows = extractResults(data);
    body.innerHTML = rows.length ? "" : '<tr><td colspan="3" class="access-muted">No hay movimientos con esos filtros.</td></tr>';
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      [formatDateTimeShort(row.created_at), row.action_label || row.action, row.target_repr || ""].forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    renderPrevNextPager(document.getElementById("pager"), data, page, load);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  document.getElementById("filterForm").addEventListener("submit", (event) => {
    event.preventDefault();
    load(1);
  });
  document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());
  load(1);
}
