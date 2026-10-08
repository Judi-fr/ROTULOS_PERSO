// VTEX en tarifas_envio.html: la tarjeta "Publicar en el checkout de VTEX" (la
// tabla de tarifas publicada como tablas de flete). Se carga DESPUÉS de
// tarifas_envio.js y usa su estado (storeId, storesById) y sus helpers;
// tarifas_envio.js llama a renderPublishCard y refreshStore recién al elegir
// una tienda o guardar una tarifa, cuando este archivo ya cargó.
//
// Backend: apps/integrations/providers/vtex/freight.py y
// POST /integrations/stores/<id>/publish-rates/.

// ---------------------------------------------------------------------------
// Publicar la tabla (plataformas que cotizan con una tabla propia: VTEX)
// ---------------------------------------------------------------------------
const publishCard = document.getElementById("publishCard");
const publishStatus = document.getElementById("publishStatus");
const publishDockSteps = document.getElementById("publishDockSteps");
const publishBtn = document.getElementById("publishBtn");

function publishStatusText(state) {
  if (!state || !state.enabled) {
    return state && state.status === "pending" ? "Retirando la tabla de VTEX..." : "La tabla no está publicada: tu checkout de VTEX no ofrece este envío.";
  }
  if (state.status === "pending") return "Publicando la tabla en VTEX... puede tardar unos minutos.";
  if (state.status === "failed") return `No se pudo publicar: ${state.error || "error desconocido"}`;
  const when = state.published_at ? ` el ${formatDate(state.published_at)}` : "";
  const unlinked = state.unlinked_policies || [];
  if (unlinked.length) {
    return `Publicada${when} (${state.rows} filas), pero falta asociar a un muelle: ${unlinked.join(", ")}. Hasta entonces VTEX no la ofrece.`;
  }
  return `Publicada${when} (${state.rows} filas). Se actualiza sola cada vez que cambiás una tarifa.`;
}

function renderPublishCard() {
  const store = storesById.get(storeId);
  publishCard.style.display = store && store.supports_rates_push ? "" : "none";
  if (!store || !store.supports_rates_push) return;
  const state = store.rates_push || null;
  publishStatus.textContent = publishStatusText(state);
  publishStatus.className = `publish-status ${state && state.status === "failed" ? "error" : ""}`;
  publishDockSteps.hidden = !(state && state.enabled && (state.unlinked_policies || []).length);
  publishBtn.textContent = state && state.enabled ? "Dejar de publicar" : "Publicar tabla";
  publishBtn.className = `btn ${state && state.enabled ? "btn-outline" : "btn-primary"} btn-small`;
}

async function refreshStore() {
  if (!storeId) return;
  try {
    const response = await apiFetch(`${STORES_URL}${encodeURIComponent(storeId)}/`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    storesById.set(storeId, await response.json());
    renderPublishCard();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al leer la tienda:", err);
  }
}

async function togglePublish() {
  const store = storesById.get(storeId);
  if (!store) return;
  const enable = !(store.rates_push && store.rates_push.enabled);
  if (!enable && !window.confirm("¿Dejar de publicar la tabla? Tu checkout de VTEX deja de ofrecer este envío.")) return;
  publishBtn.disabled = true;
  try {
    const response = await apiFetch(`${STORES_URL}${encodeURIComponent(storeId)}/publish-rates/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: enable }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cambiar la publicación."));
    storesById.set(storeId, data);
    renderPublishCard();
    showMessage(
      enable
        ? "Estamos publicando la tabla en VTEX. Tocá “Actualizar estado” en unos minutos para ver cómo quedó."
        : "Estamos retirando la tabla de VTEX.",
      "success"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo cambiar la publicación.");
  } finally {
    publishBtn.disabled = false;
  }
}

publishBtn.addEventListener("click", togglePublish);
document.getElementById("publishRefreshBtn").addEventListener("click", refreshStore);
