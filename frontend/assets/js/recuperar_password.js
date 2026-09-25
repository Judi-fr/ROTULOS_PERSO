// Paso 1 de "olvidé mi contraseña": pide el enlace de restablecimiento.
// Pública (sin JWT): no usa auth.js ni apiFetch, porque justamente el usuario
// no puede iniciar sesión. El paso 2 es reset-password.js.
//
// El backend responde 200 SIEMPRE, exista o no la cuenta, para no dejar
// averiguar qué emails están registrados. Esta pantalla respeta eso: muestra
// el mismo mensaje en los dos casos y nunca dice "ese email no existe".

const PASSWORD_RESET_URL = `${window.APP_CONFIG.API_BASE}/auth/password-reset/`;

const form = document.getElementById("resetRequestForm");
const emailInput = document.getElementById("emailInput");
const submitBtn = document.getElementById("submitBtn");

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const email = emailInput.value.trim();
  if (!email) {
    showMessage("Escribí el email de tu cuenta.");
    emailInput.focus();
    return;
  }

  // Se deshabilita mientras viaja: el endpoint está limitado por throttling
  // (scope password_reset) para frenar el envío masivo de correos, y no tiene
  // sentido que el usuario se coma ese límite a fuerza de clicks.
  submitBtn.disabled = true;
  submitBtn.textContent = "Enviando...";

  try {
    const response = await fetch(PASSWORD_RESET_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      showMessage(getErrorMessage(data, "No pudimos procesar el pedido. Probá de nuevo."));
      return;
    }

    showMessage(
      data.detail ||
        "Si existe una cuenta con ese email, te enviamos un enlace para restablecer la contraseña.",
      "success"
    );
    // La tarjeta entera se esconde tras el éxito: reenviar otra vez desde la
    // misma pantalla solo sirve para chocar con el throttling, y dejar solo el
    // formulario oculto deja el título colgado sobre un cuerpo vacío.
    form.closest(".profile-card").hidden = true;
  } catch (error) {
    showMessage("Error de red. Revisá tu conexión.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Enviarme el enlace";
  }
});
