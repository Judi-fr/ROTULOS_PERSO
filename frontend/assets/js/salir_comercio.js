// Salir del comercio (organizacion.html, POST /team/leave/): solo un empleado.
// El dueño no puede irse así: pasa la titularidad o da de baja el comercio.
// Lo que hizo queda en el comercio; la pestaña sigue en otro de sus
// comercios, o va a la pantalla para crear uno o sumarse.
(function () {
  const section = document.getElementById("leaveTeamSection");
  const button = document.getElementById("leaveTeamBtn");
  if (!section || !button) return;

  const user = window.Auth.getCurrentUser();
  if (!user.team || user.team.role !== "employee") return;
  const name = user.team.display_name || user.team.name || "este comercio";
  section.querySelector(".profile-card-title").textContent = `Salir de «${name}»`;
  section.hidden = false;

  button.addEventListener("click", async () => {
    if (!(await appConfirm(`¿Salir de «${name}»?\n\nPara volver te tienen que invitar o aprobar de nuevo.`, { confirmLabel: "Salir", danger: true }))) return;
    button.disabled = true;
    try {
      const response = await window.Auth.apiFetch(`${window.APP_CONFIG.API_BASE}/team/leave/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo salir del comercio."));
      if (data.user) window.Auth.saveSession(data.user);
      await appAlert(`Saliste de «${data.team_name || name}».`, { tone: "success" });
      window.location.href = data.user && data.user.team ? "dashboard.html" : "comercio_inicio.html";
    } catch (err) {
      if (err.isSessionExpired) return;
      button.disabled = false;
      showMessage(err.message, "error");
    }
  });
})();
