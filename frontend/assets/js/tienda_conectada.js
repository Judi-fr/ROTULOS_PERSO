// Resultado de una instalación hecha con un link para compartir. El backend
// redirige acá con ?store_connected=<id> o ?store_error=<código> (ver
// StoreOAuthCallbackView / StoreInstallShareView). Quien llega no tiene
// sesión: solo se le dice cómo terminó.

const SHARED_ERROR_MESSAGES = {
  share_link_invalid:
    "El link venció o no es válido. Pedile a quien te lo mandó que genere uno nuevo.",
  authorization_cancelled: "Se canceló la autorización: la tienda no se conectó.",
  missing_code: "Tiendanube no devolvió el código de autorización. Volvé a abrir el link.",
  invalid_state: "La autorización tardó demasiado y venció. Volvé a abrir el link.",
  invalid_signature: "No pudimos verificar que la autorización venga de Tiendanube. Volvé a abrir el link.",
  authorization_rejected: "Tiendanube rechazó la autorización. Volvé a abrir el link.",
  provider_unavailable: "No pudimos comunicarnos con Tiendanube. Probá de nuevo en unos minutos.",
  owned_by_other_account:
    "Esa tienda ya está conectada a otra cuenta. Avisale a quien te mandó el link.",
};

(function showSharedInstallResult() {
  const params = new URLSearchParams(window.location.search);
  const connected = params.get("store_connected");
  const error = params.get("store_error");
  window.history.replaceState(null, "", window.location.pathname);

  const title = document.getElementById("resultTitle");
  const text = document.getElementById("resultText");
  if (connected) {
    title.textContent = "¡Listo! La tienda quedó conectada";
    text.textContent =
      "La app ya tiene acceso a la tienda y los pedidos van a aparecer en la cuenta de quien te mandó el link. Podés cerrar esta ventana.";
    showMessage("Tienda conectada.", "success");
  } else if (error) {
    title.textContent = "La tienda no se conectó";
    text.textContent = SHARED_ERROR_MESSAGES[error] || "No se pudo conectar la tienda. Volvé a abrir el link.";
    showMessage(error === "authorization_cancelled" ? "Autorización cancelada." : `Error: ${error}`);
  } else {
    text.textContent = "Para conectar una tienda, abrí el link que te mandaron.";
  }
})();
