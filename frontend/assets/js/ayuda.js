// Ayuda / Soporte: form de contacto simple. Reutiliza el mismo wrapper de
// fetch / manejo de 401 y 403 (cambio de contraseña pendiente) que el resto
// del frontend (ver dashboard.js / perfil.js / admingestion_test.js).

const API_BASE = "http://127.0.0.1:8000/api/v1/auth";
const ME_URL = `${API_BASE}/me/`;
const SUPPORT_URL = `${API_BASE}/support/`;
const LOGOUT_URL = `${API_BASE}/logout/`;

function getAccessToken() {
  return localStorage.getItem("access") || "";
}

async function apiFetch(url, options = {}) {
  const headers = {
    ...(options.headers || {}),
    Authorization: `Bearer ${getAccessToken()}`,
  };

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    handleSessionExpired();
    const sessionError = new Error("Sesión expirada");
    sessionError.isSessionExpired = true;
    throw sessionError;
  }

  if (response.status === 403) {
    const body = await response.clone().json().catch(() => ({}));
    if (body && body.must_change_password) {
      window.location.replace("cambiar-password.html");
      const pendingError = new Error("Cambio de contraseña pendiente");
      pendingError.isSessionExpired = true;
      throw pendingError;
    }
  }

  return response;
}

function handleSessionExpired() {
  alert("Tu sesión expiró. Volvé a iniciar sesión.");
  localStorage.removeItem("access");
  localStorage.removeItem("refresh");
  localStorage.removeItem("user");
  window.location.replace("index.html");
}

function getCurrentUser() {
  try {
    return JSON.parse(localStorage.getItem("user") || "{}");
  } catch {
    return {};
  }
}

// Topbar: mismos datos que ya trae localStorage (evita otro round-trip solo
// para pintar nombre/email/avatar), igual criterio que dashboard.html usa
// antes del primer fetch.
function renderTopbar() {
  const user = getCurrentUser();
  const nameEl = document.getElementById("userName");
  const emailEl = document.getElementById("userEmail");
  const avatarEl = document.getElementById("userAvatar");
  const email = user.email || "";
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ") || email || "Usuario";

  if (nameEl) nameEl.textContent = name;
  if (emailEl) emailEl.textContent = email || "—";
  if (avatarEl && email) {
    avatarEl.src = user.picture
      ? user.picture
      : `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(email)}`;
  }
}

const msg = document.getElementById("supportMsg");
function showMsg(text, ok) {
  if (!msg) return;
  msg.textContent = text;
  msg.style.color = ok ? "green" : "red";
  msg.style.display = "block";
}

// ---------------------------------------------------------------------------
// MIS MENSAJES: lista los mensajes propios (GET /auth/support/, recortado a
// request.user en el backend) con su estado y, cuando el admin ya respondió,
// la respuesta.
// ---------------------------------------------------------------------------
const STATUS_LABELS = { pending: "Pendiente", in_progress: "En curso", resolved: "Resuelto" };

function formatMessageDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("es-AR", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function renderMyMessages(messages) {
  const container = document.getElementById("myMessagesList");
  if (!container) return;

  if (!messages.length) {
    container.innerHTML = '<p class="loading-text">Todavía no enviaste ningún mensaje.</p>';
    return;
  }

  container.innerHTML = messages
    .map((message) => {
      const statusKey = message.status || "pending";
      const statusLabel = STATUS_LABELS[statusKey] || statusKey;
      const responseBlock = message.response
        ? `<div class="support-msg-response">
             <div class="support-msg-response-label">Respuesta</div>
             ${message.response}
           </div>`
        : "";
      return `
        <div class="support-msg-item">
          <div class="support-msg-head">
            <span class="support-msg-subject">${message.subject}</span>
            <span class="support-msg-status ${statusKey}">${statusLabel}</span>
          </div>
          <div class="support-msg-date">${formatMessageDate(message.created_at)}</div>
          <p class="support-msg-body">${message.message}</p>
          ${responseBlock}
        </div>
      `;
    })
    .join("");
}

async function loadMyMessages() {
  try {
    const response = await apiFetch(SUPPORT_URL);
    if (!response.ok) return; // no rompe la pantalla si falla: el form sigue funcionando
    const data = await response.json();
    const messages = Array.isArray(data) ? data : data.results || [];
    renderMyMessages(messages);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar mis mensajes:", err);
    const container = document.getElementById("myMessagesList");
    if (container) {
      container.innerHTML = '<p class="loading-text">No se pudieron cargar tus mensajes.</p>';
    }
  }
}

document.getElementById("sendSupportBtn")?.addEventListener("click", async () => {
  const subjectInput = document.getElementById("subjectInput");
  const messageInput = document.getElementById("messageInput");
  const subject = subjectInput?.value.trim() || "";
  const message = messageInput?.value.trim() || "";

  if (!subject || !message) {
    showMsg("Completá el asunto y el mensaje.", false);
    return;
  }

  const button = document.getElementById("sendSupportBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Enviando...";

  try {
    const response = await apiFetch(SUPPORT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject, message }),
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      const err =
        data?.subject?.[0] || data?.message?.[0] || data?.detail || "No se pudo enviar el mensaje.";
      showMsg(err, false);
      return;
    }

    showMsg("¡Gracias! Recibimos tu mensaje y te vamos a responder a la brevedad.", true);
    if (subjectInput) subjectInput.value = "";
    if (messageInput) messageInput.value = "";
    loadMyMessages();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al enviar el mensaje de soporte:", err);
    showMsg("Error de red. Revisá tu conexión.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    const refresh = localStorage.getItem("refresh") || "";
    if (refresh) {
      try {
        await apiFetch(LOGOUT_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh }),
        });
      } catch {
        // Igual se limpia la sesión local abajo aunque falle el backend.
      }
    }
    localStorage.removeItem("access");
    localStorage.removeItem("refresh");
    localStorage.removeItem("user");
    window.location.replace("index.html");
  });
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  renderTopbar();
  loadMyMessages();
}
