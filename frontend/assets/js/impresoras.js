// Impresoras (impresoras.html): los agentes de impresión del comerciante y
// sus impresoras (apps.printing). El token de un agente se muestra UNA vez, al
// crearlo, junto con el comando para correrlo. Las reglas (qué campos pide
// cada tipo de conexión, una sola predeterminada) las aplica el backend; acá
// solo se muestra lo que hace falta según lo elegido.

const API_BASE = window.APP_CONFIG.API_BASE;
const AGENTS_URL = `${API_BASE}/printing/agents/`;
const PRINTERS_URL = `${API_BASE}/printing/printers/`;
const DOWNLOAD_URL = `${API_BASE}/printing/agent/download/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let agents = [];
let printers = [];

async function readJson(response, fallback) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(getErrorMessage(data, fallback));
  return data;
}

// --- Agentes -----------------------------------------------------------------

function renderAgents() {
  const body = document.getElementById("agentsBody");
  if (!agents.length) {
    body.innerHTML = `<tr><td colspan="4">${appEmptyState({ icon: "printer", title: "Todavía no creaste ningún agente", text: "El agente es un programa chico que corre en una PC de tu local y le manda los rótulos a la impresora." })}</td></tr>`;
  } else {
    body.innerHTML = "";
    agents.forEach((agent) => {
      const row = document.createElement("tr");
      const status = agent.is_online
        ? '<span class="print-badge print-badge-online">En línea</span>'
        : '<span class="print-badge print-badge-offline">Sin conexión</span>';
      row.innerHTML = `
        <td><strong>${escapeHtml(agent.name)}</strong><span class="print-detail">Token rpa_${escapeHtml(agent.token_prefix)}_…</span></td>
        <td>${status}${agent.last_version ? `<span class="print-detail">${escapeHtml(agent.last_version)}</span>` : ""}</td>
        <td>${escapeHtml(formatDateTime(agent.last_seen_at, "Nunca"))}</td>
        <td><div class="print-actions"></div></td>`;
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "btn btn-outline";
      revoke.textContent = "Revocar";
      revoke.addEventListener("click", () => revokeAgent(agent));
      row.querySelector(".print-actions").appendChild(revoke);
      body.appendChild(row);
    });
  }

  const select = document.getElementById("printerAgent");
  const current = select.value;
  select.innerHTML =
    '<option value="">Ninguno todavía</option>' +
    agents.map((agent) => `<option value="${agent.id}">${escapeHtml(agent.name)}</option>`).join("");
  if (current) select.value = current;
  else if (agents.length === 1) select.value = String(agents[0].id);
}

function showToken(agent) {
  const box = document.getElementById("tokenBox");
  const server = API_BASE.startsWith("http") ? API_BASE : `${window.location.origin}${API_BASE}`;
  const command = `python3 rotulos_print_agent.py --server ${server} --token ${agent.token}`;
  box.innerHTML = `
    <strong>Agente «${escapeHtml(agent.name)}» creado.</strong>
    Copiá este token ahora: <strong>no se vuelve a mostrar</strong>.
    <code>${escapeHtml(agent.token)}</code>
    <strong>Windows:</strong> descargá la configuración y guardala en la misma carpeta que
    RotulosAgente.exe; después abrí el .exe con doble clic (la primera vez te ofrece arrancar solo al prender la PC).
    <div class="form-actions">
      <button type="button" class="btn btn-primary" id="downloadConfig">Descargar la configuración</button>
    </div>
    <strong>Mac o Linux:</strong> con el programa de Python descargado, corré:
    <pre>${escapeHtml(command)}</pre>
    <button type="button" class="btn btn-outline" id="copyCommand">Copiar el comando</button>`;
  box.hidden = false;
  // rotulos_agent.json: lo que el agente lee al abrirse sin parámetros. Se
  // arma acá porque el token solo existe en esta respuesta.
  document.getElementById("downloadConfig").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify({ server, token: agent.token }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "rotulos_agent.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.getElementById("copyCommand").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(command);
      showMessage("Comando copiado.", "success");
    } catch {
      showMessage("No se pudo copiar: seleccioná el texto y copialo a mano.");
    }
  });
}

async function createAgent(event) {
  event.preventDefault();
  const input = document.getElementById("agentName");
  try {
    const agent = await readJson(
      await apiFetch(AGENTS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: input.value.trim() }),
      }),
      "No se pudo crear el agente."
    );
    input.value = "";
    showToken(agent);
    await loadAll();
    showMessage(`Agente «${agent.name}» creado. Copiá el token antes de salir de esta página.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function revokeAgent(agent) {
  const ok = await appConfirm(
    `¿Revocar «${agent.name}»? Ese agente deja de poder imprimir y sus impresoras quedan sin agente hasta que elijas otro.`
  );
  if (!ok) return;
  try {
    const response = await apiFetch(`${AGENTS_URL}${agent.id}/`, { method: "DELETE" });
    if (!response.ok) await readJson(response, "No se pudo revocar el agente.");
    document.getElementById("tokenBox").hidden = true;
    await loadAll();
    showMessage(`Agente «${agent.name}» revocado.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function downloadAgent(event) {
  event.preventDefault();
  // El mismo programa: como .exe para Windows (si el servidor lo tiene) o el .py.
  const windows = event.currentTarget.id === "downloadAgentWindows";
  try {
    const response = await apiFetch(windows ? `${DOWNLOAD_URL}?platform=windows` : DOWNLOAD_URL);
    if (!response.ok) await readJson(response, "No se pudo descargar el programa.");
    await downloadResponse(response, windows ? "RotulosAgente.exe" : "rotulos_print_agent.py");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

// --- Impresoras --------------------------------------------------------------

function describeConnection(printer) {
  return printer.connection === "network"
    ? `Red · ${printer.host}:${printer.port}`
    : `En la PC del agente · ${printer.queue}`;
}

function describeFormat(printer) {
  if (printer.format !== "zpl") return "PDF";
  const dpi = { 6: 152, 8: 203, 12: 300, 24: 600 }[printer.dpmm] || "";
  return `ZPL ${dpi} dpi`;
}

function renderPrinters() {
  const body = document.getElementById("printersBody");
  if (!printers.length) {
    body.innerHTML = `<tr><td colspan="5">${appEmptyState({ icon: "printer", title: "Todavía no agregaste impresoras" })}</td></tr>`;
    return;
  }
  body.innerHTML = "";
  printers.forEach((printer) => {
    const row = document.createElement("tr");
    const defaultBadge = printer.is_default ? ' <span class="print-badge print-badge-online">Por defecto</span>' : "";
    const agentText = printer.agent_name
      ? escapeHtml(printer.agent_name)
      : '<span class="print-error">Sin agente: no puede imprimir</span>';
    row.innerHTML = `
      <td><strong>${escapeHtml(printer.name)}</strong>${defaultBadge}${printer.location ? `<span class="print-detail">${escapeHtml(printer.location)}</span>` : ""}</td>
      <td>${escapeHtml(describeFormat(printer))}</td>
      <td>${escapeHtml(describeConnection(printer))}</td>
      <td>${agentText}</td>
      <td><div class="print-actions"></div></td>`;
    const actions = row.querySelector(".print-actions");
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn btn-outline";
    edit.textContent = "Editar";
    edit.addEventListener("click", () => fillPrinterForm(printer));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-outline";
    remove.textContent = "Quitar";
    remove.addEventListener("click", () => removePrinter(printer));
    actions.append(edit, remove);
    body.appendChild(row);
  });
}

function syncPrinterFields() {
  const isZpl = document.getElementById("printerFormat").value === "zpl";
  const isNetwork = document.getElementById("printerConnection").value === "network";
  document.getElementById("dpmmField").hidden = !isZpl;
  document.getElementById("hostField").hidden = !isNetwork;
  document.getElementById("portField").hidden = !isNetwork;
  document.getElementById("queueField").hidden = isNetwork;
}

function resetPrinterForm() {
  document.getElementById("printerForm").reset();
  document.getElementById("printerId").value = "";
  document.getElementById("printerSubmit").textContent = "Agregar impresora";
  document.getElementById("printerCancel").hidden = true;
  renderAgents();
  syncPrinterFields();
}

function fillPrinterForm(printer) {
  document.getElementById("printerId").value = printer.id;
  document.getElementById("printerName").value = printer.name;
  document.getElementById("printerLocation").value = printer.location || "";
  document.getElementById("printerFormat").value = printer.format;
  document.getElementById("printerDpmm").value = String(printer.dpmm || 8);
  document.getElementById("printerConnection").value = printer.connection;
  document.getElementById("printerHost").value = printer.host || "";
  document.getElementById("printerPort").value = printer.port || 9100;
  document.getElementById("printerQueue").value = printer.queue || "";
  document.getElementById("printerAgent").value = printer.agent ? String(printer.agent) : "";
  document.getElementById("printerDefault").checked = printer.is_default;
  document.getElementById("printerSubmit").textContent = "Guardar cambios";
  document.getElementById("printerCancel").hidden = false;
  syncPrinterFields();
  document.getElementById("printerName").focus();
}

async function savePrinter(event) {
  event.preventDefault();
  const id = document.getElementById("printerId").value;
  const format = document.getElementById("printerFormat").value;
  const connection = document.getElementById("printerConnection").value;
  const agent = document.getElementById("printerAgent").value;
  const payload = {
    name: document.getElementById("printerName").value.trim(),
    location: document.getElementById("printerLocation").value.trim(),
    format,
    dpmm: format === "zpl" ? Number(document.getElementById("printerDpmm").value) : null,
    connection,
    host: connection === "network" ? document.getElementById("printerHost").value.trim() : "",
    port: Number(document.getElementById("printerPort").value) || 9100,
    queue: connection === "system" ? document.getElementById("printerQueue").value.trim() : "",
    agent: agent ? Number(agent) : null,
    is_default: document.getElementById("printerDefault").checked,
  };
  try {
    const saved = await readJson(
      await apiFetch(id ? `${PRINTERS_URL}${id}/` : PRINTERS_URL, {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
      "No se pudo guardar la impresora."
    );
    resetPrinterForm();
    await loadAll();
    showMessage(id ? `Impresora «${saved.name}» guardada.` : `Impresora «${saved.name}» agregada.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function removePrinter(printer) {
  const ok = await appConfirm(`¿Quitar «${printer.name}»? Lo que tenga en cola se cancela.`);
  if (!ok) return;
  try {
    const response = await apiFetch(`${PRINTERS_URL}${printer.id}/`, { method: "DELETE" });
    if (!response.ok) await readJson(response, "No se pudo quitar la impresora.");
    await loadAll();
    showMessage(`Impresora «${printer.name}» quitada.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

// --- Arranque ----------------------------------------------------------------

async function loadAll() {
  const [agentsData, printersData] = await Promise.all([
    readJson(await apiFetch(AGENTS_URL), "No se pudieron leer los agentes."),
    readJson(await apiFetch(PRINTERS_URL), "No se pudieron leer las impresoras."),
  ]);
  agents = extractResults(agentsData);
  printers = extractResults(printersData);
  renderAgents();
  renderPrinters();
}

async function init() {
  window.AppTopbar.render();
  document.getElementById("agentForm").addEventListener("submit", createAgent);
  document.getElementById("printerForm").addEventListener("submit", savePrinter);
  document.getElementById("printerCancel").addEventListener("click", resetPrinterForm);
  document.getElementById("downloadAgent").addEventListener("click", downloadAgent);
  document.getElementById("downloadAgentWindows").addEventListener("click", downloadAgent);
  ["printerFormat", "printerConnection"].forEach((id) =>
    document.getElementById(id).addEventListener("change", syncPrinterFields)
  );
  syncPrinterFields();
  try {
    await loadAll();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  init();
}
