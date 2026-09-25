// Paso 2 de "olvidé mi contraseña": confirma el enlace del correo y guarda la
// contraseña nueva. Pública (sin JWT): la credencial es el par uid+token que
// viene en la query string del link. El paso 1 es recuperar_password.js.
//
// El token lo genera default_token_generator de Django y no se guarda en la
// base: se deriva del hash de la contraseña actual, así que se invalida solo
// al cambiarla. Por eso un enlace ya usado deja de servir, y por eso el error
// de "inválido" y el de "vencido" son el mismo mensaje.

const PASSWORD_RESET_CONFIRM_URL = `${window.APP_CONFIG.API_BASE}/auth/password-reset/confirm/`;

const form = document.getElementById("resetConfirmForm");
const newPasswordInput = document.getElementById("newPasswordInput");
const confirmPasswordInput = document.getElementById("confirmPasswordInput");
const submitBtn = document.getElementById("submitBtn");

const params = new URLSearchParams(window.location.search);
const uid = params.get("uid") || "";
const token = params.get("token") || "";

// Sin uid/token no hay nada que hacer: se avisa y se esconde el formulario en
// vez de dejar al usuario escribir una contraseña que no se va a poder guardar.
if (!uid || !token) {
  showMessage(
    "El enlace está incompleto. Abrilo tal cual te llegó al correo, o pedí uno nuevo."
  );
  form.closest(".profile-card").hidden = true;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const newPassword = newPasswordInput.value;
  const confirmPassword = confirmPasswordInput.value;

  if (!newPassword) {
    showMessage("Escribí la contraseña nueva.");
    newPasswordInput.focus();
    return;
  }
  // Se compara acá y no en el backend porque el backend no recibe la
  // repetición: es una ayuda para no guardar un error de tipeo, no una regla
  // de negocio. Las reglas de composición las valida el servidor.
  if (newPassword !== confirmPassword) {
    showMessage("Las dos contraseñas no coinciden.");
    confirmPasswordInput.focus();
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Guardando...";

  try {
    const response = await fetch(PASSWORD_RESET_CONFIRM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uid, token, new_password: newPassword }),
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      showMessage(
        getErrorMessage(data, "No pudimos guardar la contraseña. Probá de nuevo.")
      );
      return;
    }

    showMessage(
      data.detail || "Listo, ya podés entrar con tu contraseña nueva.",
      "success"
    );
    form.closest(".profile-card").hidden = true;
  } catch (error) {
    showMessage("Error de red. Revisá tu conexión.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Guardar contraseña";
  }
});
