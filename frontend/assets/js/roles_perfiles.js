// Pestaña "Perfiles de los comercios" de roles.html (admin): los perfiles que
// cada comercio arma para su equipo y qué permisos de rol habilita cada
// acceso. Datos de GET /api/v1/teams/profiles/ (team_views.py,
// AdminTeamProfilesView). "Editar perfiles" abre createProfileEditor
// (utils.js, el mismo editor que la pestaña Perfiles de equipo.html) para ese
// comercio, contra PATCH/DELETE teams/profiles/<id>/ y POST
// teams/<id>/profiles/; el admin puede dar cualquier acceso. Las pestañas las
// maneja roles.js, que llama a window.TeamProfilesTab.open() al mostrar esta.

(function () {
  const TEAM_PROFILES_URL = `${window.APP_CONFIG.API_BASE}/teams/profiles/`;
  let loadedOnce = false;
  let searchTimer = null;
  let accessLabels = {};
  let teams = [];
  let editingTeamId = null;
  let editor = null;

  function renderAccessCatalog(accesses) {
    const box = document.getElementById("accessCatalog");
    if (!box) return;
    box.innerHTML = `
      <p class="roles-intro">Un empleado necesita las dos cosas: el permiso en su rol y el acceso en su perfil.</p>
      <table class="access-table">
        <thead><tr><th>Acceso del perfil</th><th>Permisos de rol que habilita</th></tr></thead>
        <tbody>
          ${accesses
            .map(
              (access) => `<tr>
                <td><strong>${escapeHtml(access.label)}</strong></td>
                <td>${access.permissions.map((code) => `<code>${escapeHtml(code)}</code>`).join(" ") || "—"}</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderTeams(teams) {
    const list = document.getElementById("teamProfilesList");
    if (!list) return;
    if (!teams.length) {
      list.innerHTML = appEmptyState({ icon: "search", title: "No hay comercios", text: "Probá con otro nombre o email." });
      return;
    }
    list.innerHTML = teams
      .map(
        (team) => `
        <article class="roles-card team-profiles-card">
          <div class="team-profiles-head">
            <div>
              <strong>${escapeHtml(team.name)}</strong>
              <span class="roles-count">${escapeHtml(team.owner_email)} · ${team.member_count} ${team.member_count === 1 ? "persona" : "personas"}</span>
            </div>
            <button type="button" class="link-btn" data-edit-team="${team.id}">Editar perfiles</button>
          </div>
          <div class="team-profiles-body">
            ${
              team.profiles.length
                ? team.profiles
                    .map(
                      (profile) => `
                <div class="team-profile">
                  <div class="team-profile-name">${escapeHtml(profile.name)}
                    <span class="roles-count">${profile.member_count} ${profile.member_count === 1 ? "empleado" : "empleados"}</span>
                  </div>
                  <div class="team-profile-accesses">
                    ${profile.accesses
                      .map((key) => `<span class="access-chip">${escapeHtml(accessLabels[key] || key)}</span>`)
                      .join("") || '<span class="roles-count">Sin accesos</span>'}
                  </div>
                </div>`
                    )
                    .join("")
                : '<p class="roles-count">Este comercio no tiene perfiles.</p>'
            }
          </div>
        </article>`
      )
      .join("");
  }

  async function load() {
    const list = document.getElementById("teamProfilesList");
    const search = (document.getElementById("teamProfilesSearch")?.value || "").trim();
    if (list) list.innerHTML = appSkeleton();
    try {
      const url = search ? `${TEAM_PROFILES_URL}?search=${encodeURIComponent(search)}` : TEAM_PROFILES_URL;
      const response = await apiFetch(url);
      const data = await response.json().catch(() => ({}));
      if (response.status === 403) throw new Error("No tenés permisos para ver los comercios.");
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los perfiles de los comercios."));
      accessLabels = Object.fromEntries((data.accesses || []).map((access) => [access.key, access.label]));
      teams = data.teams || [];
      renderAccessCatalog(data.accesses || []);
      renderTeams(teams);
      if (editingTeamId) refreshEditor();
    } catch (err) {
      if (err.isSessionExpired) return;
      if (list) list.innerHTML = `<p class="roles-loading">${escapeHtml(err.message)}</p>`;
    }
  }

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

  function refreshEditor() {
    const team = teams.find((item) => item.id === editingTeamId);
    if (team) editor.update(team.profiles);
  }

  function openEditor(teamId) {
    const team = teams.find((item) => item.id === teamId);
    if (!team) return;
    editingTeamId = teamId;
    if (!editor) {
      editor = createProfileEditor(document.getElementById("adminProfileEditor"), {
        catalog: accessLabels,
        allowed: null,
        async save(profile, body) {
          const saved = await send(
            profile ? `${window.APP_CONFIG.API_BASE}/teams/profiles/${profile.id}/` : `${window.APP_CONFIG.API_BASE}/teams/${editingTeamId}/profiles/`,
            profile ? "PATCH" : "POST",
            body,
            "No se pudo guardar el perfil."
          );
          await load();
          return saved;
        },
        async remove(profile) {
          await send(`${window.APP_CONFIG.API_BASE}/teams/profiles/${profile.id}/`, "DELETE", null, "No se pudo borrar el perfil.");
          await load();
        },
      });
    }
    document.getElementById("teamProfileEditingName").textContent = `Perfiles de ${team.name}`;
    document.getElementById("teamProfileEditing").hidden = false;
    document.getElementById("teamProfilesList").hidden = true;
    document.querySelector(".team-profiles-toolbar").hidden = true;
    refreshEditor();
  }

  async function closeEditor() {
    if (editor && editor.hasUnsavedChanges() && !(await appConfirm("¿Descartar los cambios sin guardar?", { confirmLabel: "Descartar" }))) return;
    editingTeamId = null;
    document.getElementById("teamProfileEditing").hidden = true;
    document.getElementById("teamProfilesList").hidden = false;
    document.querySelector(".team-profiles-toolbar").hidden = false;
  }

  document.getElementById("teamProfilesList")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-team]");
    if (button) openEditor(Number(button.dataset.editTeam));
  });
  document.getElementById("teamProfileBack")?.addEventListener("click", closeEditor);

  document.getElementById("teamProfilesSearch")?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, 300);
  });

  window.TeamProfilesTab = {
    open() {
      if (loadedOnce) return;
      loadedOnce = true;
      load();
    },
  };

  // Si la página abrió directo en #perfiles, roles.js mostró la pestaña antes
  // de que este archivo existiera.
  const panel = document.querySelector('.roles-panel[data-panel="perfiles"]');
  if (panel && !panel.hidden) window.TeamProfilesTab.open();
})();
