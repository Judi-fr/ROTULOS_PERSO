// Empretienda en tiendas.html: agregar la tienda (no tiene API: no hay
// credenciales) y, en su tarjeta, el acceso a importar su planilla de ventas.
// Se carga DESPUÉS de tiendas.js y usa sus funciones y constantes (apiFetch,
// INTEGRATIONS_API, showConnectResult, connectError, loadStores,
// setConnectCardOpen, createElement); tiendas.js llama a
// renderEmpretiendaImport y openEmpretiendaReconnect recién al dibujar las
// tiendas, cuando este archivo ya cargó.
//
// Backend: apps/integrations/providers/empretienda/.

const EMPRETIENDA_CONNECT_URL = `${INTEGRATIONS_API}/empretienda/connect/`;

async function connectEmpretienda(event) {
  event.preventDefault();
  const urlInput = document.getElementById("empretiendaUrlInput");
  const nameInput = document.getElementById("empretiendaNameInput");
  const button = document.getElementById("empretiendaConnectBtn");
  if (!urlInput.value.trim()) {
    showConnectResult("Escribí la dirección de tu tienda Empretienda (por ejemplo, https://mitienda.empretienda.com.ar).");
    urlInput.focus();
    return;
  }
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Agregando...";
  try {
    const response = await apiFetch(EMPRETIENDA_CONNECT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store_url: urlInput.value.trim(), name: nameInput.value.trim() }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw connectError(getErrorMessage(data, "No se pudo agregar la tienda Empretienda."), response.status);
    }
    urlInput.value = "";
    nameInput.value = "";
    document.getElementById("empretiendaConnect").open = false;
    showConnectResult(
      `¡Listo! ${data.name || "Tu tienda"} quedó agregada. Ahora importá su planilla de ventas desde su tarjeta, en Mis tiendas.`,
      "success"
    );
    await loadStores();
  } catch (err) {
    if (err.isSessionExpired) return;
    showConnectResult(
      err.code ? err.message : "No pudimos comunicarnos con el servidor. Revisá tu conexión y probá de nuevo.",
      "error",
      err.code || null
    );
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

// En la tarjeta de la tienda: a importar su planilla.
function renderEmpretiendaImport(store) {
  const wrapper = createElement(
    "p",
    "connect-help store-empretienda-import",
    "Empretienda no se conecta sola: cada vez que tengas ventas nuevas, exportá la planilla desde su admin e "
  );
  const link = createElement("a", "", "importala acá");
  link.href = `importar_empretienda.html?store=${encodeURIComponent(store.id)}`;
  wrapper.appendChild(link);
  wrapper.appendChild(document.createTextNode("."));
  return wrapper;
}

// Una tienda desconectada se vuelve a agregar con la misma dirección.
function openEmpretiendaReconnect(store) {
  setConnectCardOpen(true);
  const form = document.getElementById("empretiendaConnect");
  form.open = true;
  document.getElementById("empretiendaUrlInput").value = store.store_url;
  document.getElementById("empretiendaNameInput").value = store.name || "";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.getElementById("empretiendaConnectForm")?.addEventListener("submit", connectEmpretienda);
