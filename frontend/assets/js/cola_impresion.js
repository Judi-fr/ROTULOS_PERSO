// Cola de impresión (cola_impresion.html): los trabajos mandados directo a
// una impresora (apps.printing.PrintJob), con reintentar y cancelar. Mientras
// haya algo en cola o imprimiéndose la lista se refresca sola; además avisa si
// ningún agente está en línea, que es la razón más común de "no sale nada".

const API_BASE = window.APP_CONFIG.API_BASE;
const JOBS_URL = `${API_BASE}/printing/jobs/`;
const AGENTS_URL = `${API_BASE}/printing/agents/`;
// Seguido mientras algo está en cola o imprimiéndose; más espaciado si no,
// para que aparezca lo que se mande desde otra pestaña.
const REFRESH_MS = 5000;
const IDLE_REFRESH_MS = 20000;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let currentPage = 1;
let refreshTimer = null;

function jobRow(job) {
  const row = document.createElement("tr");
  const labels = job.label_count === 1 ? "1 rótulo" : `${job.label_count} rótulos`;
  const attempts = job.attempts > 1 ? `<span class="print-detail">${job.attempts} intentos</span>` : "";
  const error = job.error_message ? `<span class="print-error">${escapeHtml(job.error_message)}</span>` : "";
  const when = job.finished_at || job.claimed_at || job.created_at;
  row.innerHTML = `
    <td><strong>#${job.id}</strong> · ${escapeHtml(labels)}<span class="print-detail">${escapeHtml(job.document_name || "Documento borrado")}</span></td>
    <td>${escapeHtml(job.printer_name || "Impresora quitada")}</td>
    <td><span class="print-badge print-badge-${escapeHtml(job.status)}">${escapeHtml(job.status_display)}</span>${attempts}${error}</td>
    <td>${escapeHtml(formatDateTime(when))}</td>
    <td><div class="print-actions"></div></td>`;
  const actions = row.querySelector(".print-actions");
  if (job.status === "error" || job.status === "cancelled") {
    actions.appendChild(actionButton("Reintentar", "btn-primary", () => transition(job, "retry", "vuelve a la cola")));
  }
  if (job.status === "pending" || job.status === "error") {
    actions.appendChild(actionButton("Cancelar", "btn-outline", () => transition(job, "cancel", "cancelado")));
  }
  return row;
}

function actionButton(text, kind, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `btn ${kind}`;
  button.textContent = text;
  button.addEventListener("click", onClick);
  return button;
}

async function transition(job, verb, done) {
  try {
    const response = await apiFetch(`${JOBS_URL}${job.id}/${verb}/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cambiar el trabajo."));
    showMessage(`Trabajo #${job.id} ${done}.`, "success");
    await loadJobs();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function loadAgentsSummary() {
  const summary = document.getElementById("agentsSummary");
  try {
    const response = await apiFetch(AGENTS_URL);
    if (!response.ok) return;
    const agents = extractResults(await response.json());
    if (!agents.length) {
      summary.innerHTML = 'No hay agentes: <a href="impresoras.html">configurá uno</a> para que esto imprima.';
    } else if (!agents.some((agent) => agent.is_online)) {
      summary.innerHTML =
        '<span class="print-error">Ningún agente está en línea: los trabajos esperan hasta que abras el programa en la PC.</span>';
    } else {
      const online = agents.filter((agent) => agent.is_online).map((agent) => agent.name);
      summary.textContent = `En línea: ${online.join(", ")}.`;
    }
  } catch (err) {
    if (err.isSessionExpired) throw err;
  }
}

async function loadJobs() {
  const params = new URLSearchParams({ page: String(currentPage) });
  const status = document.getElementById("statusFilter").value;
  if (status) params.set("status", status);
  const body = document.getElementById("jobsBody");
  try {
    const response = await apiFetch(`${JOBS_URL}?${params}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer la cola."));
    const jobs = extractResults(data);
    body.innerHTML = "";
    if (!jobs.length) {
      body.innerHTML = `<tr><td colspan="5">${appEmptyState({ icon: "printer", title: "No hay trabajos para mostrar", actionLabel: "Imprimir rótulos", actionHref: "imprimir_rotulos.html" })}</td></tr>`;
    }
    jobs.forEach((job) => body.appendChild(jobRow(job)));
    renderPrevNextPager(document.getElementById("jobsPager"), data, currentPage, goToPage);
    await loadAgentsSummary();
    scheduleRefresh(jobs.some((job) => job.status === "pending" || job.status === "printing"));
  } catch (err) {
    if (err.isSessionExpired) return;
    body.innerHTML = `<tr><td colspan="5" class="empty-state">${escapeHtml(err.message)}</td></tr>`;
  }
}

function goToPage(page) {
  currentPage = page;
  loadJobs();
}

function scheduleRefresh(active) {
  clearTimeout(refreshTimer);
  if (document.visibilityState === "visible") {
    refreshTimer = setTimeout(loadJobs, active ? REFRESH_MS : IDLE_REFRESH_MS);
  }
}

// Viene de imprimir_rotulos.html (?sent=<id>&labels=N[&dispatched=M]):
// confirma lo que se mandó y limpia la URL.
function showSentResult() {
  const params = new URLSearchParams(window.location.search);
  const sent = params.get("sent");
  const generating = params.get("generating");
  if (!sent && !generating) return;
  const labels = Number(params.get("labels") || 0);
  const dispatched = Number(params.get("dispatched") || 0);
  // Un lote grande (apps.labels.batch_jobs) se genera en segundo plano: el
  // trabajo aparece en esta lista cuando está listo.
  let text = generating
    ? `Se están generando ${labels} rótulo(s); el trabajo aparece acá apenas estén listos y se manda solo a la impresora.`
    : `Listo: mandaste ${labels} rótulo(s) a la impresora (trabajo #${sent}).`;
  if (dispatched) text += ` Se marcaron ${dispatched} pedido(s) como despachados.`;
  showMessage(text, "success");
  window.history.replaceState(null, "", window.location.pathname);
}

async function init() {
  window.AppTopbar.render();
  showSentResult();
  document.getElementById("statusFilter").addEventListener("change", () => goToPage(1));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadJobs();
  });
  await loadJobs();
}

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  init();
}
