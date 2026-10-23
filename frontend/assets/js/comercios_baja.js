// Comercios dados de baja (comercios_baja.html, admin con users.view): los
// que su dueño dio de baja desde Mi cuenta. Reactivar (users.edit) se lo
// devuelve al dueño, si el comercio que tiene ahora está vacío.

const API_BASE = window.APP_CONFIG.API_BASE;

function describeData(data) {
  const parts = Object.entries(data || {}).map(([name, count]) => `${count} ${name}`);
  return parts.length ? parts.join(", ") : "sin datos";
}

function renderList(teams) {
  const list = document.getElementById("closedList");
  list.innerHTML = "";
  if (!teams.length) {
    list.innerHTML = appEmptyState({
      title: "No hay comercios dados de baja",
      text: "Cuando un dueño dé de baja su comercio desde Mi cuenta, va a aparecer acá.",
    });
    return;
  }
  const canReopen = canUseUserPermission("users.edit");
  teams.forEach((team) => {
    const item = document.createElement("div");
    item.className = "closed-item";
    const by = team.closed_by && team.closed_by !== team.owner_email ? ` por ${team.closed_by}` : "";
    item.innerHTML = `
      <div class="closed-body">
        <strong>${escapeHtml(team.name)}</strong>
        <span>Dueño: ${escapeHtml(team.owner_email || "(usuario borrado)")}</span>
        <span class="closed-muted">Dado de baja el ${escapeHtml(formatDateTime(team.closed_at))}${escapeHtml(by)}</span>
        <span class="closed-muted">Guardado: ${escapeHtml(describeData(team.data))}</span>
        ${team.reason ? `<span class="closed-muted closed-reason">«${escapeHtml(team.reason)}»</span>` : ""}
      </div>`;
    if (canReopen && team.owner_email) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-outline";
      button.textContent = "Reactivar";
      button.addEventListener("click", () => reopen(team, button));
      item.appendChild(button);
    }
    list.appendChild(item);
  });
}

async function load() {
  const list = document.getElementById("closedList");
  const search = document.getElementById("closedSearch").value.trim();
  if (!list.children.length) list.innerHTML = appSkeleton();
  try {
    const query = search ? `?search=${encodeURIComponent(search)}` : "";
    const response = await apiFetch(`${API_BASE}/teams/closed/${query}`);
    const data = await response.json().catch(() => []);
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los comercios."));
    renderList(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    list.innerHTML = "";
    showMessage(err.message);
  }
}

async function reopen(team, button) {
  const question =
    `¿Reactivar «${team.name}»?\n\n` +
    `Vuelve a ser de ${team.owner_email} con todos sus datos. Sus empleados no vuelven solos (los invita el dueño) ` +
    "y las tiendas hay que conectarlas de nuevo.";
  if (!(await appConfirm(question, { confirmLabel: "Reactivar" }))) return;
  button.disabled = true;
  try {
    const response = await apiFetch(`${API_BASE}/teams/${team.id}/reopen/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo reactivar el comercio."));
    const extra = data.warnings && data.warnings.length ? ` ${data.warnings.join(" ")}` : "";
    showMessage(`«${data.name}» volvió a ser de ${data.owner_email}.${extra}`, "success");
    await load();
  } catch (err) {
    if (err.isSessionExpired) return;
    button.disabled = false;
    showMessage(err.message);
  }
}

function bootstrap() {
  if (!canUseUserPermission("users.view")) {
    window.location.replace("dashboard.html");
    return;
  }
  let timer = null;
  document.getElementById("closedSearch").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(load, 300);
  });
  load();
}

bootstrap();
