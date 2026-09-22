// Perfil propio (self-service) para usuarios no administradores.
// Carga y guarda siempre contra /api/v1/auth/me/ (nunca localStorage como
// fuente de verdad). Sesión y apiFetch salen de assets/js/auth.js
// (window.Auth), compartido con el resto del frontend (ver dashboard.js /
// usuarios.js).

const API_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
const ME_URL = `${API_BASE}/me/`;
const CHANGE_PASSWORD_URL = `${API_BASE}/me/change-password/`;
const VERIFY_EMAIL_RESEND_URL = `${API_BASE}/verify-email/resend/`;

const getAccessToken = () => window.Auth.getAccessToken();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

// showMessage y getErrorMessage salen de assets/js/utils.js.

function showFieldMessage(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = ok ? "#16a34a" : "#dc2626";
  el.style.display = "block";
}

const ROLE_LABELS = {
  admin: "Administrador",
  designer: "Diseñador",
  operator: "Operador",
  subscriber: "Suscriptor",
};

function roleLabel(role) {
  if (!role) return "—";
  return ROLE_LABELS[role] || (role.charAt(0).toUpperCase() + role.slice(1));
}

const getCurrentUser = () => window.Auth.getCurrentUser();

// Aviso de verificación suave: solo se muestra cuando el backend informa
// email_verified === false (ver EMAIL_VERIFICATION_URL / RegisterView en
// backend). No hay badge de "verificado" — si está todo bien, no se avisa nada.
function renderVerificationNotice(user) {
  const notice = document.getElementById("emailVerificationNotice");
  if (!notice) return;
  notice.style.display = user.email_verified === false ? "block" : "none";
}

function renderProfile(user) {
  const v = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val ?? "";
  };
  v("firstNameInput", user.first_name);
  v("lastNameInput", user.last_name);

  const emailValue = document.getElementById("emailValue");
  const roleValue = document.getElementById("roleValue");
  const statusValue = document.getElementById("statusValue");
  if (emailValue) emailValue.textContent = user.email || "—";
  if (roleValue) roleValue.textContent = roleLabel(user.role);
  if (statusValue) statusValue.textContent = user.is_active === false ? "Inactivo" : "Activo";
}

async function loadProfile() {
  try {
    const response = await apiFetch(ME_URL);
    if (!response.ok) {
      throw new Error(`Error HTTP: ${response.status}`);
    }
    const user = await response.json();
    window.AppTopbar.render(user);
    renderProfile(user);
    renderVerificationNotice(user);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar el perfil:", err);
    showMessage("No se pudo cargar tu perfil. Intentá de nuevo más tarde.");
  }
}

async function saveProfile() {
  const firstName = document.getElementById("firstNameInput")?.value?.trim() || "";
  const lastName = document.getElementById("lastNameInput")?.value?.trim() || "";
  const button = document.getElementById("saveProfileBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Guardando...";

  try {
    const response = await apiFetch(ME_URL, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ first_name: firstName, last_name: lastName }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo actualizar tu perfil."));
    }
    window.AppTopbar.render(data);
    renderProfile(data);
    renderVerificationNotice(data);
    showFieldMessage("profileMsg", "Perfil actualizado correctamente.", true);
  } catch (err) {
    if (err.isSessionExpired) return;
    showFieldMessage("profileMsg", err.message || "No se pudo actualizar tu perfil.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function savePassword() {
  const current = document.getElementById("currentPasswordInput")?.value?.trim() || "";
  const nuevo = document.getElementById("newPasswordInput")?.value?.trim() || "";
  const confirm = document.getElementById("confirmPasswordInput")?.value?.trim() || "";

  if (!current || !nuevo || !confirm) {
    showFieldMessage("passwordMsg", "Completá los tres campos para cambiar la contraseña.", false);
    return;
  }

  const button = document.getElementById("savePasswordBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Cambiando...";

  try {
    const response = await apiFetch(CHANGE_PASSWORD_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: current,
        new_password: nuevo,
        confirm_password: confirm,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo cambiar la contraseña."));
    }
    showFieldMessage("passwordMsg", data.detail || "Contraseña actualizada correctamente.", true);
    ["currentPasswordInput", "newPasswordInput", "confirmPasswordInput"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    showFieldMessage("passwordMsg", err.message || "No se pudo cambiar la contraseña.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.getElementById("saveProfileBtn")?.addEventListener("click", saveProfile);
document.getElementById("savePasswordBtn")?.addEventListener("click", savePassword);

document.getElementById("resendVerificationBtn")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Enviando...";

  try {
    const response = await apiFetch(VERIFY_EMAIL_RESEND_URL, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo reenviar el email de verificación."));
    }
    showMessage(data.detail || "Te reenviamos el email de verificación.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo reenviar el email de verificación.", "error");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

// ---------------------------------------------------------------------------
// Logout: misma lógica que dashboard.js / admin_common.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  loadProfile();
}
