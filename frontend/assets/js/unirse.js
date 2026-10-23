// Link para sumarse a un comercio (unirse.html?code=...): el dueño lo comparte
// desde Equipo → Miembros. Sin sesión, el código queda guardado y se vuelve
// acá después de entrar (index.html); con sesión se pide sumarse
// (POST /team/join/) y queda pendiente hasta que lo aprueben.

(function () {
  const PENDING_CODE_KEY = "pendingJoinCode";
  const code = new URLSearchParams(window.location.search).get("code") || "";

  function remember(value) {
    try {
      if (value) localStorage.setItem(PENDING_CODE_KEY, value);
      else localStorage.removeItem(PENDING_CODE_KEY);
    } catch {
      // Sin almacenamiento: después de entrar hay que volver a abrir el link.
    }
  }

  if (!window.Auth.hasSession()) {
    remember(code);
    window.location.replace("index.html");
    return;
  }

  const input = document.getElementById("joinCode");
  input.value = code;

  document.getElementById("joinForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await window.Auth.apiFetch(`${window.APP_CONFIG.API_BASE}/team/join/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: input.value }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo pedir sumarte."));
      remember("");
      document.getElementById("joinForm").hidden = true;
      document.getElementById("doneBox").hidden = false;
      showMessage(
        `Le pediste sumarte a «${data.team_name}». Cuando lo aprueben, lo vas a ver en el selector de arriba.`,
        "success"
      );
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
    }
  });
})();
