// Empezar en un comercio (comercio_inicio.html): es lo primero que ve quien
// todavía no está en ninguno, y adonde lleva "Crear o sumarme a otro
// comercio…" del selector de arriba. Tres caminos (apps/accounts/team_views.py):
// - crear uno propio (POST /team/create/),
// - aceptar una invitación (renderMyInvitations, utils.js),
// - pedir sumarse con el código del comercio (POST /team/join/): queda
//   pendiente hasta que lo aprueben; acá se ve y se puede cancelar.
// Al crear o aceptar, la pestaña pasa a trabajar en ese comercio y va al panel.

(function () {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
  const PENDING_CODE_KEY = "pendingJoinCode";

  function goToPanel() {
    window.location.href = "dashboard.html";
  }

  async function postJson(url, body) {
    const response = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo completar."));
    return data;
  }

  document.getElementById("createForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("teamName").value.trim();
    try {
      const data = await postJson(`${API_BASE}/team/create/`, { name });
      window.Auth.saveSession(data.user);
      goToPanel();
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
    }
  });

  document.getElementById("joinForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("joinCode");
    try {
      const data = await postJson(`${API_BASE}/team/join/`, { code: input.value });
      input.value = "";
      try { localStorage.removeItem(PENDING_CODE_KEY); } catch { /* sin almacenamiento */ }
      showMessage(
        `Le pediste sumarte a «${data.team_name}». Cuando lo aprueben, lo vas a ver en el selector de arriba.`,
        "success"
      );
      loadRequests();
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
    }
  });

  async function cancelRequest(request) {
    if (!await appConfirm(`¿Cancelar el pedido para sumarte a «${request.team_name}»?`)) return;
    try {
      const response = await apiFetch(`${API_BASE}/team/my-join-requests/${request.id}/`, { method: "DELETE" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getErrorMessage(data, "No se pudo cancelar el pedido."));
      }
      showMessage("Pedido cancelado.", "success");
      loadRequests();
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
    }
  }

  async function loadRequests() {
    try {
      const response = await apiFetch(`${API_BASE}/team/my-join-requests/`);
      const requests = await response.json().catch(() => []);
      if (!response.ok) throw new Error(getErrorMessage(requests, "No se pudieron leer tus pedidos."));
      const box = document.getElementById("requestsBox");
      const list = document.getElementById("requestList");
      list.replaceChildren();
      box.hidden = !requests.length;
      requests.forEach((request) => {
        const item = document.createElement("li");
        item.className = "team-item";
        const info = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = request.team_name;
        const when = document.createElement("div");
        when.className = "access-muted";
        when.textContent = `Pedido el ${formatDate(request.created_at)} · esperando aprobación`;
        info.append(name, when);
        const actions = document.createElement("div");
        actions.className = "team-actions";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "btn btn-outline btn-small";
        cancel.textContent = "Cancelar pedido";
        cancel.addEventListener("click", () => cancelRequest(request));
        actions.appendChild(cancel);
        item.append(info, actions);
        list.appendChild(item);
      });
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
    }
  }

  // Un código que llegó por el link (unirse.html) o que quedó guardado antes
  // de iniciar sesión.
  function prefillCode() {
    let code = new URLSearchParams(window.location.search).get("code") || "";
    if (!code) {
      try { code = localStorage.getItem(PENDING_CODE_KEY) || ""; } catch { code = ""; }
    }
    if (code) document.getElementById("joinCode").value = code;
  }

  function bootstrap() {
    if (!window.Auth.hasSession()) {
      window.location.replace("index.html");
      return;
    }
    const user = window.Auth.getCurrentUser();
    if (user.teams && user.teams.length) {
      document.getElementById("startTitle").textContent = "Otro comercio";
      document.getElementById("backToPanel").hidden = false;
    }
    prefillCode();
    renderMyInvitations(document.getElementById("invitationList"), {
      emptyText: "No tenés invitaciones pendientes.",
      onAccepted: goToPanel,
    }).load();
    loadRequests();
  }

  bootstrap();
})();
