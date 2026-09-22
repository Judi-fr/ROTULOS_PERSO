// ---------------------------------------------------------------------------
// BANDEJA DE SOPORTE (support.view_all para ver, support.manage para
// responder/cambiar estado).
// (Antes era una sección de gestionuser.html; ver soporte_admin.html.)
// ---------------------------------------------------------------------------
const SUPPORT_MESSAGES_URL = `${window.APP_CONFIG.API_BASE}/support-messages/`;
const SUPPORT_STATUS_LABELS = { pending: "Pendiente", in_progress: "En curso", resolved: "Resuelto" };

let supportPage = 1;
let supportMessages = [];
let selectedSupportMessageId = null;

function supportQueryString() {
  const params = new URLSearchParams();
  const search = document.getElementById("supportSearchInput")?.value.trim();
  const statusFilter = document.getElementById("supportStatusFilter")?.value;
  const dateFrom = document.getElementById("supportDateFrom")?.value;
  const dateTo = document.getElementById("supportDateTo")?.value;
  if (search) params.set("search", search);
  if (statusFilter) params.set("status", statusFilter);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (supportPage > 1) params.set("page", supportPage);
  const qs = params.toString();
  return qs ? `${SUPPORT_MESSAGES_URL}?${qs}` : SUPPORT_MESSAGES_URL;
}

function renderSupportCounts(counts) {
  if (!counts) return;
  const pendingEl = document.getElementById("supportPendingCount");
  const inProgressEl = document.getElementById("supportInProgressCount");
  const resolvedEl = document.getElementById("supportResolvedCount");
  if (pendingEl) pendingEl.textContent = counts.pending ?? 0;
  if (inProgressEl) inProgressEl.textContent = counts.in_progress ?? 0;
  if (resolvedEl) resolvedEl.textContent = counts.resolved ?? 0;
}

function renderSupportRows(messages) {
  const body = document.getElementById("supportBody");
  if (!body) return;
  if (!messages.length) {
    body.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:20px;">No hay mensajes para mostrar.</td></tr>`;
    return;
  }
  body.innerHTML = "";
  messages.forEach((message) => {
    const tr = document.createElement("tr");
    tr.dataset.supportId = message.id;
    if (Number(message.id) === Number(selectedSupportMessageId)) tr.classList.add("active");
    tr.innerHTML = `
      <td title="${escapeHtml(message.user_email || "")}">${escapeHtml(message.user_email || "-")}</td>
      <td class="cell-muted">${escapeHtml(message.subject)}</td>
      <td><span class="support-status ${message.status}">${escapeHtml(message.status_label || SUPPORT_STATUS_LABELS[message.status] || message.status)}</span></td>
      <td class="cell-muted">${formatDateTime(message.created_at)}</td>
    `;
    body.appendChild(tr);
  });
}

async function loadSupportMessages() {
  const body = document.getElementById("supportBody");
  if (body) body.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:20px;">Cargando...</td></tr>`;
  try {
    const response = await apiFetch(supportQueryString());
    if (response.status === 403) throw new Error("No tenés permisos para ver la bandeja de soporte.");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los mensajes."));
    supportMessages = data.results || [];
    renderSupportRows(supportMessages);
    renderSupportCounts(data.counts);
    renderSimplePager("supportPaginationControls", "supportPaginationInfo", data.pagination, "mensajes", (page) => {
      supportPage = page;
      loadSupportMessages();
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar la bandeja de soporte:", err);
    if (body) body.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:20px; color:red;">${escapeHtml(err.message)}</td></tr>`;
  }
}

function showSupportDetailMsg(text, ok) {
  const el = document.getElementById("supportDetailMsg");
  if (!el) return;
  el.textContent = text;
  el.className = `roles-message ${ok ? "success" : "error"}`;
  el.style.display = "block";
}

function renderSupportDetail(message) {
  const empty = document.getElementById("supportDetailEmpty");
  const content = document.getElementById("supportDetailContent");
  if (!message) {
    if (empty) empty.style.display = "";
    if (content) content.style.display = "none";
    return;
  }
  if (empty) empty.style.display = "none";
  if (content) content.style.display = "";

  document.getElementById("supportDetailUser").textContent = message.user_email || "";
  document.getElementById("supportDetailSubject").textContent = message.subject || "";
  document.getElementById("supportDetailMessage").textContent = message.message || "";
  document.getElementById("supportDetailStatus").value = message.status;
  document.getElementById("supportDetailResponse").value = message.response || "";

  const handledEl = document.getElementById("supportDetailHandled");
  if (handledEl) {
    if (message.responded_at) {
      handledEl.style.display = "";
      handledEl.textContent = `Respondido el ${formatDateTime(message.responded_at)} por ${message.handled_by_email || "-"}`;
    } else {
      handledEl.style.display = "none";
    }
  }

  const canManage = canUseUserPermission("support.manage");
  document.getElementById("supportDetailStatus").disabled = !canManage;
  document.getElementById("supportDetailResponse").disabled = !canManage;
  document.getElementById("saveSupportDetailBtn").style.display = canManage ? "" : "none";

  const msg = document.getElementById("supportDetailMsg");
  if (msg) msg.style.display = "none";
}

function selectSupportMessage(id) {
  selectedSupportMessageId = id;
  renderSupportRows(supportMessages);
  const message = supportMessages.find((m) => Number(m.id) === Number(id));
  renderSupportDetail(message);
}

document.getElementById("supportBody")?.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-support-id]");
  if (!row) return;
  selectSupportMessage(row.dataset.supportId);
});

document.getElementById("saveSupportDetailBtn")?.addEventListener("click", async () => {
  if (!selectedSupportMessageId) return;
  const button = document.getElementById("saveSupportDetailBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Guardando...";
  try {
    const payload = {
      status: document.getElementById("supportDetailStatus").value,
      response: document.getElementById("supportDetailResponse").value,
    };
    const response = await apiFetch(`${SUPPORT_MESSAGES_URL}${selectedSupportMessageId}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo guardar el mensaje."));

    supportMessages = supportMessages.map((m) => (Number(m.id) === Number(selectedSupportMessageId) ? data : m));
    renderSupportRows(supportMessages);
    renderSupportDetail(data);
    showSupportDetailMsg("Guardado correctamente.", true);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al guardar el mensaje de soporte:", err);
    showSupportDetailMsg(err.message || "No se pudo guardar el mensaje.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

document.getElementById("applySupportFiltersBtn")?.addEventListener("click", () => {
  supportPage = 1;
  loadSupportMessages();
});
document.getElementById("clearSupportFiltersBtn")?.addEventListener("click", () => {
  ["supportSearchInput", "supportStatusFilter", "supportDateFrom", "supportDateTo"]
    .forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
  supportPage = 1;
  loadSupportMessages();
});

function bootstrap() {
  if (!canUseUserPermission("support.view_all")) {
    window.location.replace("dashboard.html");
    return;
  }
  selectedSupportMessageId = null;
  renderSupportDetail(null);
  supportPage = 1;
  loadSupportMessages();
}

bootstrap();
