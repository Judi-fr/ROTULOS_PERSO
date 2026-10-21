// Avisos del comercio (notificaciones.html, GET /notices/ y
// POST /notices/<id>/dismiss/; apps/monitoring/notices.py). El panel muestra
// los tres más nuevos; acá están todos.

const NOTICES_URL = `${window.APP_CONFIG.API_BASE}/notices/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

function render(notices) {
  const list = document.getElementById("noticeList");
  list.innerHTML = "";
  if (!notices.length) {
    list.innerHTML = `<li>${appEmptyState({ icon: "check", title: "No hay avisos", text: "Todo anda: si algo de tu comercio se rompe, te lo contamos acá." })}</li>`;
    return;
  }
  notices.forEach((notice) => {
    const item = document.createElement("li");
    item.className = "team-item";
    const info = document.createElement("div");
    info.innerHTML = "<strong></strong><div class='access-muted'></div><div class='access-muted'></div>";
    info.querySelector("strong").textContent = notice.title;
    const [detail, when] = info.querySelectorAll(".access-muted");
    detail.textContent = notice.detail || "";
    when.textContent = `Desde ${formatDate(notice.created_at)}`;
    const actions = document.createElement("div");
    actions.className = "team-actions";
    if (notice.url) {
      const review = document.createElement("a");
      review.className = "btn btn-primary btn-small";
      review.href = notice.url;
      review.textContent = "Revisar";
      actions.appendChild(review);
    }
    const hide = document.createElement("button");
    hide.type = "button";
    hide.className = "btn btn-outline btn-small";
    hide.textContent = "Ocultar";
    hide.addEventListener("click", () => dismiss(notice));
    actions.appendChild(hide);
    item.append(info, actions);
    list.appendChild(item);
  });
}

async function load() {
  try {
    const response = await apiFetch(NOTICES_URL);
    const data = await response.json().catch(() => []);
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron leer los avisos."));
    render(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function dismiss(notice) {
  try {
    const response = await apiFetch(`${NOTICES_URL}${notice.id}/dismiss/`, { method: "POST" });
    if (!response.ok && response.status !== 404) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudo ocultar el aviso."));
    }
    await load();
    showMessage("Aviso ocultado. Si el problema vuelve a pasar, va a aparecer de nuevo.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function bootstrap() {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  load();
}

bootstrap();
