// Baja del comercio (Mi comercio → Datos del comercio, equipo.html#datos; POST /team/close/): solo el dueño.
// Nada se borra en el backend; la persona sigue con sus otros comercios (o
// sin ninguno: entonces va a la pantalla para crear uno o sumarse).
(function () {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const CONFIRM_WORD = "BAJA";

  const section = document.getElementById("closeTeamSection");
  const form = document.getElementById("closeTeamForm");
  const reason = document.getElementById("closeTeamReason");
  const confirmInput = document.getElementById("closeTeamConfirm");
  const button = document.getElementById("closeTeamBtn");
  if (!section || !form) return;

  const user = window.Auth.getCurrentUser();
  if (!user.team || user.team.role !== "owner") return;
  section.hidden = false;

  confirmInput.addEventListener("input", () => {
    button.disabled = confirmInput.value.trim().toUpperCase() !== CONFIRM_WORD;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = user.team.display_name || user.team.name || "tu comercio";
    const question =
      `¿Dar de baja «${name}»?\n\n` +
      "Vos y tu equipo dejan de verlo y sus tiendas se desconectan. Solo el administrador lo puede reactivar.";
    if (!(await appConfirm(question, { confirmLabel: "Dar de baja", danger: true }))) return;
    button.disabled = true;
    try {
      const response = await window.Auth.apiFetch(`${API_BASE}/team/close/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: confirmInput.value, reason: reason.value }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo dar de baja el comercio."));
      if (data.user) window.Auth.saveSession(data.user);
      await appAlert(`«${data.team_name}» quedó dado de baja.\n\nTu cuenta sigue activa.`, { tone: "success" });
      window.location.href = data.user && data.user.team ? "dashboard.html" : "comercio_inicio.html";
    } catch (err) {
      if (err.isSessionExpired) return;
      button.disabled = false;
      showMessage(err.message, "error");
    }
  });
})();
