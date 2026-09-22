// Despacho y seguimiento de UN pedido propio (despachar.html?pedido=<id>).
//
// Backend: GET /api/v1/orders/<id>/ y POST /api/v1/orders/<id>/ship/
// (OrderViewSet.ship). El estado del envío solo avanza; repetir el estado
// actual sirve para cargar o corregir el seguimiento. Si el pedido vino de
// una tienda conectada, el backend le avisa a la tienda y al comprador.
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth). Todo texto
// del pedido se inserta con textContent (puede venir de una tienda online).

const ORDERS_URL = `${window.APP_CONFIG.API_BASE}/orders/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const STATUS_LABELS = {
  created: "Creado",
  preparing: "En preparación",
  dispatched: "Despachado",
  in_transit: "En tránsito",
  delivered: "Entregado",
  cancelled: "Cancelado",
};
const STATUS_PROGRESS = ["created", "preparing", "dispatched", "in_transit", "delivered"];
const SHIP_STATUSES = ["dispatched", "in_transit", "delivered"];

let currentOrder = null;

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------

// showMessage y getErrorMessage salen de assets/js/utils.js.

function showFormMessage(text) {
  const box = document.getElementById("formMessage");
  box.textContent = text;
  box.hidden = !text;
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value || "—";
}

function getOrderId() {
  const id = new URLSearchParams(window.location.search).get("pedido") || "";
  return /^\d+$/.test(id) ? id : null;
}

// ---------------------------------------------------------------------------
// Pedido
// ---------------------------------------------------------------------------

function addressText(address) {
  if (!address) return "";
  const street = [address.street, address.number].filter(Boolean).join(" ");
  return [street, address.reference, address.city, address.state, address.postal_code]
    .filter(Boolean)
    .join(", ");
}

function renderSummary(order) {
  const number = order.external_number ? `#${order.external_number}` : `#${order.id}`;
  setText("pageTitle", `Despachar pedido ${number}`);

  const badge = document.getElementById("summaryStatus");
  badge.textContent = order.status_label || STATUS_LABELS[order.status] || order.status;
  badge.className = `status-badge ${order.status}`;

  setText("summaryRecipient", order.address && order.address.recipient_name);
  setText("summaryAddress", addressText(order.address));
  setText("summaryDescription", order.description);
  setText("summaryShippingOption", order.shipping_option);

  const notice = document.getElementById("storeNotice");
  if (order.store_connection) {
    notice.textContent =
      `Este pedido viene de la tienda ${order.store_name || "conectada"}. ` +
      "Al guardar le avisamos a la tienda y el comprador recibe el seguimiento.";
    notice.hidden = false;
  } else {
    notice.hidden = true;
  }

  document.getElementById("summaryCard").hidden = false;
}

function renderForm(order) {
  const select = document.getElementById("statusSelect");
  const currentRank = STATUS_PROGRESS.indexOf(order.status);
  select.replaceChildren();
  SHIP_STATUSES.filter((status) => STATUS_PROGRESS.indexOf(status) >= currentRank).forEach((status) => {
    const option = document.createElement("option");
    option.value = status;
    option.textContent = STATUS_LABELS[status];
    select.appendChild(option);
  });
  select.value = SHIP_STATUSES.includes(order.status) ? order.status : "dispatched";

  document.getElementById("carrierInput").value = order.carrier || "";
  document.getElementById("trackingNumberInput").value = order.tracking_number || "";
  document.getElementById("trackingUrlInput").value = order.tracking_url || "";

  const form = document.getElementById("shipForm");
  const disabled = !order.is_shippable;
  form.querySelectorAll("input, select, button").forEach((control) => {
    control.disabled = disabled;
  });
  if (disabled) {
    showMessage(
      `Este pedido está «${STATUS_LABELS[order.status] || order.status}»: ya no se puede actualizar su envío.`,
      "info"
    );
  }

  document.getElementById("shipCard").hidden = false;
}

async function loadOrder(id) {
  try {
    const response = await apiFetch(`${ORDERS_URL}${id}/`);
    if (response.status === 404) throw new Error("No encontramos ese pedido entre los tuyos.");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cargar el pedido."));
    currentOrder = data;
    renderSummary(data);
    renderForm(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo cargar el pedido.");
  }
}

async function saveShipment(event) {
  event.preventDefault();
  if (!currentOrder) return;
  showFormMessage("");

  const trackingUrl = document.getElementById("trackingUrlInput").value.trim();
  if (trackingUrl && !/^https?:\/\//i.test(trackingUrl)) {
    showFormMessage("El link de seguimiento tiene que empezar con http:// o https://.");
    return;
  }

  const button = document.getElementById("saveShipBtn");
  button.disabled = true;
  try {
    const response = await apiFetch(`${ORDERS_URL}${currentOrder.id}/ship/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: document.getElementById("statusSelect").value,
        carrier: document.getElementById("carrierInput").value.trim(),
        tracking_number: document.getElementById("trackingNumberInput").value.trim(),
        tracking_url: trackingUrl,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo guardar el envío."));

    currentOrder = data;
    renderSummary(data);
    renderForm(data);
    showMessage(
      data.store_connection
        ? "Envío guardado. Le estamos avisando a la tienda."
        : "Envío guardado.",
      "success"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showFormMessage(err.message || "No se pudo guardar el envío.");
  } finally {
    if (currentOrder && currentOrder.is_shippable) button.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Inicio
// ---------------------------------------------------------------------------

document.getElementById("shipForm")?.addEventListener("submit", saveShipment);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  window.AppTopbar.render();
  const orderId = getOrderId();
  if (orderId) {
    loadOrder(orderId);
  } else {
    showMessage("Falta indicar qué pedido despachar. Elegilo desde Mis pedidos.");
  }
}
