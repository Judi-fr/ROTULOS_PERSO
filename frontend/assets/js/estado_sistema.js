// Estado del sistema (estado_sistema.html, admin con monitoring.view):
// los chequeos de apps.monitoring, a quién llegan las alertas y un mail de
// prueba. Se refresca solo cada 30 segundos.

const API_BASE = window.APP_CONFIG.API_BASE;
const REFRESH_MS = 30000;

const STATUS_TEXT = {
  ok: "Todo funciona",
  warning: "Funciona, con avisos",
  error: "Hay problemas",
};
const CHECK_TEXT = { ok: "Bien", warning: "Aviso", error: "Falla" };

function renderChecks(checks) {
  const list = document.getElementById("checksList");
  list.innerHTML = "";
  checks.forEach((check) => {
    const item = document.createElement("li");
    item.className = `status-item status-${check.status}`;
    let since = "";
    if (check.failing_since) {
      since = `Falla desde ${formatDateTime(check.failing_since)}`;
      since += check.notified_at ? ` · avisado ${formatDateTime(check.notified_at)}` : " · todavía sin avisar";
    }
    item.innerHTML = `
      <span class="status-dot"></span>
      <div class="status-body">
        <strong>${escapeHtml(check.label)}</strong>
        <span>${escapeHtml(check.detail)}</span>
        ${since ? `<span class="status-muted">${escapeHtml(since)}</span>` : ""}
      </div>
      <span class="status-tag">${CHECK_TEXT[check.status] || check.status}</span>`;
    list.appendChild(item);
  });
}

function renderAlerts(alerts) {
  const minutes = Math.round(alerts.after_seconds / 60);
  document.getElementById("alertsRule").textContent =
    `Se manda un mail cuando un chequeo pasa a «Falla» por más de ${minutes} minuto(s), ` +
    `un recordatorio cada ${alerts.reminder_hours} horas si sigue, y otro cuando se resuelve. ` +
    "Los avisos no mandan mail.";
  const recipients = alerts.recipients.length ? alerts.recipients.join(", ") : "nadie (ningún admin tiene email)";
  document.getElementById("alertsRecipients").textContent =
    recipients + (alerts.configured_recipients ? " (MONITORING_ALERT_EMAILS)" : " (los administradores)");
  const last = alerts.last_mail;
  document.getElementById("alertsLastMail").textContent = last
    ? `${formatDateTime(last.at)} — «${last.subject || ""}» ${last.ok ? "salió bien" : `NO salió: ${last.error || ""}`}`
    : "todavía ninguno";
}

async function load() {
  try {
    const response = await apiFetch(`${API_BASE}/monitoring/status/`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer el estado del sistema."));
    const overall = document.getElementById("overall");
    overall.className = `status-overall status-${data.status}`;
    document.getElementById("overallText").textContent = STATUS_TEXT[data.status] || data.status;
    document.getElementById("checkedAt").textContent = `Revisado ${formatDateTime(data.checked_at)}`;
    renderChecks(data.checks);
    renderAlerts(data.alerts);
    return true;
  } catch (err) {
    if (err.isSessionExpired) return false;
    showMessage(err.message);
    return false;
  }
}

// "Actualizar" a mano: si nada cambió la pantalla quedaba igual y no se sabía
// si el botón había hecho algo. Ahora dice que está consultando y, al
// terminar, a qué hora quedó actualizado (el aviso se va solo).
let refreshNoticeTimer = null;

async function refreshNow() {
  const button = document.getElementById("refreshBtn");
  const panel = document.querySelector(".main");
  button.disabled = true;
  button.textContent = "Actualizando…";
  panel.classList.add("is-refreshing");
  const ok = await load();
  panel.classList.remove("is-refreshing");
  button.disabled = false;
  button.textContent = "Actualizar";
  if (!ok) return;
  const time = new Date().toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  showMessage(`Listo: estado actualizado a las ${time}.`, "success");
  clearTimeout(refreshNoticeTimer);
  refreshNoticeTimer = setTimeout(() => {
    const el = document.getElementById("pageMessage");
    if (el && el.classList.contains("success")) el.style.display = "none";
  }, 4000);
}

async function sendTestMail() {
  const button = document.getElementById("testMailBtn");
  button.disabled = true;
  try {
    const response = await apiFetch(`${API_BASE}/monitoring/test-mail/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo mandar el mail de prueba."));
    showMessage(`Mail de prueba enviado a ${data.sent_to.join(", ")}.`, "success");
    await load();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
    await load();
  } finally {
    button.disabled = false;
  }
}

function bootstrap() {
  if (!canUseUserPermission("monitoring.view")) {
    window.location.replace("dashboard.html");
    return;
  }
  document.getElementById("refreshBtn").addEventListener("click", refreshNow);
  document.getElementById("testMailBtn").addEventListener("click", sendTestMail);
  load();
  setInterval(() => {
    if (!document.hidden) load();
  }, REFRESH_MS);
}

bootstrap();
