// Tiendanube en tiendas.html: el botón de conectar y el link de instalación
// para compartir con quien administra la tienda. Se carga DESPUÉS de tiendas.js y usa sus funciones y constantes
// (apiFetch, INTEGRATIONS_API, showConnectResult, connectError, connectStore,
// loadStores, createElement...); tiendas.js llama a lo de acá recién al dibujar
// las tiendas, cuando este archivo ya cargó.
//
// Backend: apps/integrations/providers/tiendanube/.

// Link de instalación para compartir con quien administra la tienda: lo abre
// sin cuenta nuestra y la tienda queda en la cuenta de quien lo generó.
async function createShareLink(platform, button) {
  const label = PLATFORM_LABELS[platform] || platform;
  button.disabled = true;
  try {
    const response = await apiFetch(`${INTEGRATIONS_API}/${platform}/install-share-link/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.share_url) {
      throw connectError(getErrorMessage(data, `No se pudo generar el link de ${label}.`), response.status);
    }
    const input = document.getElementById("shareTiendanubeUrl");
    input.value = data.share_url;
    document.getElementById("shareTiendanubeResult").hidden = false;
    input.select();
    showConnectResult(
      `Link generado. Vale ${data.expires_in_hours} horas: mandáselo a quien administra la tienda en ${label}.`,
      "success"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showConnectResult(
      err.code ? err.message : "No pudimos comunicarnos con el servidor. Revisá tu conexión y probá de nuevo.",
      "error",
      err.code || null
    );
  } finally {
    button.disabled = false;
  }
}

async function copyShareLink() {
  const input = document.getElementById("shareTiendanubeUrl");
  try {
    await navigator.clipboard.writeText(input.value);
    showConnectResult("Link copiado.", "success");
  } catch {
    // Sin permiso de portapapeles (o fuera de HTTPS/localhost): queda
    // seleccionado para copiarlo a mano.
    input.select();
    showConnectResult("No se pudo copiar solo: el link quedó seleccionado, copialo con Ctrl+C.");
  }
}

document
  .getElementById("connectTiendanubeBtn")
  ?.addEventListener("click", (event) => connectStore("tiendanube", { button: event.currentTarget }));
document.getElementById("shareTiendanubeBtn")?.addEventListener("click", (event) =>
  createShareLink("tiendanube", event.currentTarget)
);
document.getElementById("shareTiendanubeCopyBtn")?.addEventListener("click", copyShareLink);
