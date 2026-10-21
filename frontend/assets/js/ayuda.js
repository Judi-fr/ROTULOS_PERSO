// Ayuda / Soporte: form de contacto simple. Sesión y apiFetch salen de
// assets/js/auth.js (window.Auth), compartido con el resto del frontend
// (ver dashboard.js / perfil.js / usuarios.js).

const API_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
const ME_URL = `${API_BASE}/me/`;
const SUPPORT_URL = `${API_BASE}/support/`;

const hasSession = () => window.Auth.hasSession();
const getCurrentUser = () => window.Auth.getCurrentUser();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);


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
             ${escapeHtml(message.response)}
           </div>`
        : "";
      return `
        <div class="support-msg-item">
          <div class="support-msg-head">
            <span class="support-msg-subject">${escapeHtml(message.subject)}</span>
            <span class="support-msg-status ${statusKey}">${escapeHtml(statusLabel)}</span>
          </div>
          <div class="support-msg-date">${formatMessageDate(message.created_at)}</div>
          <p class="support-msg-body">${escapeHtml(message.message)}</p>
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
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

if (!hasSession()) {
  window.location.replace("index.html");
} else {
  window.AppTopbar.render();
  loadMyMessages();
}

// Preguntas frecuentes: "Abrir todas" / "Cerrar todas". El texto del botón
// sigue lo que hay abierto, también cuando se abre o cierra una a mano.
const faqToggleAll = document.getElementById("faqToggleAll");
const faqItems = Array.from(document.querySelectorAll(".faq-list details"));

function syncFaqToggleAll() {
  if (!faqToggleAll) return;
  faqToggleAll.textContent = faqItems.every((item) => item.open) ? "Cerrar todas" : "Abrir todas";
}

if (faqToggleAll) {
  faqToggleAll.addEventListener("click", () => {
    const open = !faqItems.every((item) => item.open);
    faqItems.forEach((item) => (item.open = open));
    syncFaqToggleAll();
  });
  faqItems.forEach((item) => item.addEventListener("toggle", syncFaqToggleAll));
}

// El link "preguntas frecuentes" de arriba abre el bloque si estaba plegado.
document.querySelectorAll('a[href="#preguntas"]').forEach((link) => {
  link.addEventListener("click", () => {
    const faq = document.getElementById("preguntas");
    if (faq) faq.open = true;
  });
});
