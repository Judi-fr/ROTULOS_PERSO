// Mi equipo (equipo.html, GET/PATCH /team/ y sus subrutas): miembros,
// invitaciones y los accesos de cada uno. El backend decide y valida todo
// (no se puede dar lo que uno no tiene, ni tocar al dueño ni a uno mismo);
// acá se arma la pantalla y se muestra cómo terminó cada acción.

const API_BASE = window.APP_CONFIG.API_BASE;
const TEAM_URL = `${API_BASE}/team/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let state = null; // la última respuesta de GET /team/
let editing = null; // el miembro abierto en el diálogo
let inviteEditor = null;
let memberEditor = null;

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

// "Qué puede hacer": un perfil del comercio o "Personalizado" (tildes
// sueltas). Al pasar a personalizado se arranca de lo del perfil elegido.
function accessEditor(container, { profileId = null, custom = [], isNew = false } = {}) {
  const selectId = `${container.id}Profile`;
  container.innerHTML = `
    <div class="field">
      <label for="${selectId}">Perfil</label>
      <select class="profile-select" id="${selectId}"></select>
    </div>
    <div class="access-boxes"></div>`;
  const select = container.querySelector(".profile-select");
  const boxes = container.querySelector(".access-boxes");
  state.profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.name;
    select.appendChild(option);
  });
  const customOption = document.createElement("option");
  customOption.value = "";
  customOption.textContent = "Personalizado";
  select.appendChild(customOption);

  const profileAccesses = (id) => (state.profiles.find((p) => String(p.id) === String(id)) || {}).accesses || [];
  const draw = (selected, isCustom) =>
    renderAccessCheckboxes(boxes, state.accesses, selected, { disabled: !isCustom, allowed: state.me.accesses });

  select.value = profileId === null || profileId === undefined ? "" : String(profileId);
  // Una invitación nueva arranca con el perfil de MENOS accesos: dar todo
  // tiene que ser una elección. Un miembro sin perfil es "Personalizado"
  // aunque no tenga accesos.
  if (isNew && state.profiles.length) {
    const smallest = [...state.profiles].sort((a, b) => a.accesses.length - b.accesses.length)[0];
    select.value = String(smallest.id);
  }
  draw(select.value ? profileAccesses(select.value) : custom, !select.value);
  let last = select.value;
  select.addEventListener("change", () => {
    if (select.value) {
      draw(profileAccesses(select.value), false);
    } else {
      draw(last ? profileAccesses(last) : custom, true);
    }
    last = select.value || last;
  });
  return {
    read: () => ({
      profile: select.value ? Number(select.value) : null,
      accesses: select.value ? [] : readAccessCheckboxes(boxes),
    }),
  };
}

function accessSummary(member) {
  if (member.role === "owner") return "Dueño: todos los accesos";
  // En el orden del catálogo, como las tildes.
  const names = Object.keys(state.accesses)
    .filter((key) => member.accesses.includes(key))
    .map((key) => state.accesses[key]);
  const what = member.profile_name ? `Perfil «${member.profile_name}»` : "Personalizado";
  return `${what}: ${names.length ? names.join(", ") : "sin accesos"}`;
}

function storeSummary(member) {
  if (member.all_stores) return "Todas las tiendas";
  const names = member.store_ids.map((id) => (state.stores.find((s) => s.id === id) || {}).name || `#${id}`);
  return names.length ? `Tiendas: ${names.join(", ")}` : "Ninguna tienda (solo pedidos cargados a mano)";
}

function button(text, kind, onClick) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = `btn ${kind} btn-small`;
  element.textContent = text;
  element.addEventListener("click", onClick);
  return element;
}

function renderMembers() {
  const list = document.getElementById("memberList");
  list.innerHTML = "";
  const meIsOwner = state.me.role === "owner";
  const myId = (window.Auth.getCurrentUser() || {}).id;
  state.members.forEach((member) => {
    const item = document.createElement("li");
    item.className = "team-item";
    const info = document.createElement("div");
    info.innerHTML = `
      <strong></strong> <span class="team-role team-role-${member.role}"></span>
      <div class="access-muted access-summary"></div>
      <div class="access-muted store-summary"></div>`;
    info.querySelector("strong").textContent = member.name || member.email;
    info.querySelector(".team-role").textContent = member.role === "owner" ? "Dueño" : "Empleado";
    info.querySelector(".access-summary").textContent = `${member.name ? member.email + " · " : ""}${accessSummary(member)}`;
    info.querySelector(".store-summary").textContent = storeSummary(member);
    const actions = document.createElement("div");
    actions.className = "team-actions";
    if (member.role !== "owner" && member.user_id !== myId) {
      actions.append(
        button("Cambiar accesos", "btn-outline", () => openMember(member)),
        button("Quitar", "btn-outline", () => removeMember(member))
      );
      if (meIsOwner) actions.append(button("Hacerlo dueño", "btn-outline", () => transfer(member)));
    }
    item.append(info, actions);
    list.appendChild(item);
  });
}

function renderInvitations() {
  const list = document.getElementById("invitationList");
  list.innerHTML = "";
  if (!state.invitations.length) {
    list.innerHTML = '<li class="access-muted">No hay invitaciones pendientes.</li>';
    return;
  }
  state.invitations.forEach((invitation) => {
    const item = document.createElement("li");
    item.className = "team-item";
    const info = document.createElement("div");
    info.innerHTML = "<strong></strong><div class='access-muted'></div>";
    info.querySelector("strong").textContent = invitation.email;
    info.querySelector(".access-muted").textContent = `${
      invitation.profile_name ? `Perfil «${invitation.profile_name}»` : "Personalizado"
    } · vence ${formatDate(invitation.expires_at)}`;
    const actions = document.createElement("div");
    actions.className = "team-actions";
    actions.append(
      button("Reenviar", "btn-outline", () => resend(invitation)),
      button("Cancelar", "btn-outline", () => cancelInvitation(invitation))
    );
    item.append(info, actions);
    list.appendChild(item);
  });
}

async function load() {
  try {
    const response = await apiFetch(TEAM_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer el equipo."));
    state = data;
    document.getElementById("teamName").value = data.team.name || "";
    renderMembers();
    renderInvitations();
    if (!inviteEditor) {
      inviteEditor = accessEditor(document.getElementById("inviteAccess"), { isNew: true });
      renderStoreChoice(document.getElementById("inviteStores"), data.stores, { allStores: true });
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function openMember(member) {
  editing = member;
  document.getElementById("memberDialogTitle").textContent = `Accesos de ${member.email}`;
  memberEditor = accessEditor(document.getElementById("memberAccess"), {
    profileId: member.profile,
    custom: member.custom_accesses,
  });
  renderStoreChoice(document.getElementById("memberStores"), state.stores, {
    allStores: member.all_stores,
    storeIds: member.store_ids,
  });
  document.getElementById("memberDialog").showModal();
}

async function saveMember(event) {
  event.preventDefault();
  const body = { ...memberEditor.read(), ...readStoreChoice(document.getElementById("memberStores")) };
  try {
    await send(`${TEAM_URL}members/${editing.id}/`, "PATCH", body, "No se pudieron guardar los accesos.");
    document.getElementById("memberDialog").close();
    await load();
    showMessage(`Accesos de ${editing.email} guardados.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    document.getElementById("memberDialog").close();
    showMessage(err.message);
  }
}

async function removeMember(member) {
  if (!await appConfirm(`¿Quitar a ${member.email} del comercio? Lo que hizo queda en el comercio; deja de ver todo.`)) return;
  try {
    await send(`${TEAM_URL}members/${member.id}/`, "DELETE", null, "No se pudo quitar.");
    await load();
    showMessage(`${member.email} ya no es parte del comercio.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function transfer(member) {
  const question =
    `¿Pasarle la titularidad del comercio a ${member.email}?\n\n` +
    "Pasa a ser el dueño, con todos los datos del comercio. Vos quedás como empleado con el perfil «Todo». " +
    "Solo el nuevo dueño puede devolvértela.";
  if (!await appConfirm(question)) return;
  try {
    await send(`${TEAM_URL}transfer/`, "POST", { member_id: member.id }, "No se pudo pasar la titularidad.");
    showMessage(`${member.email} es el nuevo dueño del comercio.`, "success");
    await load();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function invite(event) {
  event.preventDefault();
  const email = document.getElementById("inviteEmail").value.trim();
  const body = { email, ...inviteEditor.read(), ...readStoreChoice(document.getElementById("inviteStores")) };
  const btn = document.getElementById("inviteBtn");
  btn.disabled = true;
  try {
    await send(`${TEAM_URL}invitations/`, "POST", body, "No se pudo mandar la invitación.");
    document.getElementById("inviteEmail").value = "";
    await load();
    showMessage(`Invitación enviada a ${email}.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  } finally {
    btn.disabled = false;
  }
}

async function resend(invitation) {
  try {
    await send(`${TEAM_URL}invitations/${invitation.id}/resend/`, "POST", null, "No se pudo reenviar.");
    await load();
    showMessage(`Invitación reenviada a ${invitation.email}.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function cancelInvitation(invitation) {
  if (!await appConfirm(`¿Cancelar la invitación a ${invitation.email}?`)) return;
  try {
    await send(`${TEAM_URL}invitations/${invitation.id}/`, "DELETE", null, "No se pudo cancelar.");
    await load();
    showMessage(`Invitación a ${invitation.email} cancelada.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function saveName(event) {
  event.preventDefault();
  const name = document.getElementById("teamName").value.trim();
  try {
    await send(TEAM_URL, "PATCH", { name }, "No se pudo guardar el nombre.");
    showMessage("Nombre del comercio guardado.", "success");
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
  document.getElementById("teamNameForm").addEventListener("submit", saveName);
  document.getElementById("inviteForm").addEventListener("submit", invite);
  document.getElementById("memberForm").addEventListener("submit", saveMember);
  document.getElementById("memberCancel").addEventListener("click", () => document.getElementById("memberDialog").close());
  load();
}

bootstrap();
