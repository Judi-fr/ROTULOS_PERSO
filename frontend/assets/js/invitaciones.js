// Invitaciones (invitaciones.html, /team/my-invitations/): los comercios que
// invitaron al usuario. Aceptar lo pasa a trabajar en ese comercio; el
// backend lo rechaza (y lo explica) si el usuario ya tiene un comercio
// propio con datos o no verificó su mail.

const API_BASE = window.APP_CONFIG.API_BASE;
const MINE_URL = `${API_BASE}/team/my-invitations/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

async function answer(invitation, decision) {
  if (decision === "accept") {
    const question =
      `¿Sumarte al comercio «${invitation.team_name}»?\n\n` +
      "Vas a trabajar sobre sus tiendas, pedidos y plantillas, con los accesos que te dieron.";
    if (!await appConfirm(question)) return;
  }
  try {
    const response = await apiFetch(`${MINE_URL}${invitation.id}/${decision}/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo responder la invitación."));
    if (decision === "accept") {
      // Los permisos cambiaron: el backend devuelve el usuario actualizado.
      if (data.user) {
        window.Auth.saveSession(data.user);
        if (window.AppTopbar) window.AppTopbar.render(data.user);
      }
      showMessage(`Ya sos parte de «${data.team_name}». Volvé al panel para ver lo que podés hacer.`, "success");
    } else {
      showMessage(`Rechazaste la invitación de «${invitation.team_name}».`, "success");
    }
    await load();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function render(invitations) {
  const list = document.getElementById("invitationList");
  list.innerHTML = "";
  if (!invitations.length) {
    list.innerHTML = '<li class="access-muted">No tenés invitaciones pendientes.</li>';
    return;
  }
  invitations.forEach((invitation) => {
    const item = document.createElement("li");
    item.className = "team-item";
    const info = document.createElement("div");
    info.innerHTML = "<strong></strong><div class='access-muted'></div><div class='access-muted'></div>";
    info.querySelector("strong").textContent = invitation.team_name;
    const [who, what] = info.querySelectorAll(".access-muted");
    who.textContent = `Te invitó ${invitation.invited_by || "el dueño"} · vence ${formatDate(invitation.expires_at)}`;
    const names = invitation.access_labels || invitation.accesses;
    what.textContent = `${invitation.profile_name ? `Perfil «${invitation.profile_name}»: ` : ""}${names.join(", ") || "sin accesos"}`;
    const actions = document.createElement("div");
    actions.className = "team-actions";
    const accept = document.createElement("button");
    accept.type = "button";
    accept.className = "btn btn-primary btn-small";
    accept.textContent = "Aceptar";
    accept.addEventListener("click", () => answer(invitation, "accept"));
    const decline = document.createElement("button");
    decline.type = "button";
    decline.className = "btn btn-outline btn-small";
    decline.textContent = "Rechazar";
    decline.addEventListener("click", () => answer(invitation, "decline"));
    actions.append(accept, decline);
    item.append(info, actions);
    list.appendChild(item);
  });
}

async function load() {
  try {
    const response = await apiFetch(MINE_URL);
    const data = await response.json().catch(() => []);
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron leer tus invitaciones."));
    render(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function bootstrap() {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  load();
}

bootstrap();
