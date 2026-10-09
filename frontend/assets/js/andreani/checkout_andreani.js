// El precio de Andreani en el checkout de las tiendas (checkout_andreani.html).
// Backend: /api/v1/carriers/andreani/checkout/ (apps/carriers/andreani/checkout.py).
// Una tarjeta por tienda cuyo checkout nos pregunta el precio: activar, contrato
// (solo a domicilio), nombre de la opción, plazo, recargo, envío gratis desde un
// monto, y "Probar" con un CP, un peso y un total de carrito.
//
// Los nombres de tienda vienen de la plataforma: se insertan con textContent.
// Sesión y apiFetch salen de auth.js; showMessage y getErrorMessage, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ANDREANI_API = `${API_BASE}/carriers/andreani`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" });
let homeContracts = [];

function el(id) {
  return document.getElementById(id);
}

function field(labelText, input) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("label");
  label.textContent = labelText;
  label.htmlFor = input.id;
  wrap.append(label, input);
  return wrap;
}

function input(id, type, value, attrs = {}) {
  const node = document.createElement("input");
  node.id = id;
  node.type = type;
  if (value !== null && value !== undefined) node.value = value;
  Object.entries(attrs).forEach(([key, val]) => node.setAttribute(key, val));
  return node;
}

function cardMessage(card, text, type = "error") {
  const box = card.querySelector(".card-message");
  box.textContent = text;
  box.className = `card-message page-message ${type}`;
  box.style.display = text ? "" : "none";
}

function renderStore(store) {
  const prefix = `store${store.store}`;
  const card = document.createElement("section");
  card.className = "profile-card";

  const title = document.createElement("div");
  title.className = "profile-card-title";
  const name = document.createElement("span");
  name.textContent = `${store.store_name} (${store.platform_label})`;
  const state = document.createElement("span");
  state.className = "selection-count";
  state.textContent = store.enabled ? "Activado" : "Desactivado";
  title.append(name, state);

  const body = document.createElement("div");
  body.className = "profile-card-body";

  const enabledRow = document.createElement("label");
  enabledRow.style.display = "flex";
  enabledRow.style.gap = "8px";
  enabledRow.style.alignItems = "center";
  const enabled = input(`${prefix}Enabled`, "checkbox");
  enabled.checked = store.enabled;
  enabledRow.append(enabled, document.createTextNode("Ofrecer Andreani en el checkout de esta tienda"));

  const contract = document.createElement("select");
  contract.id = `${prefix}Contract`;
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = homeContracts.length ? "— elegí un contrato —" : "Tu cuenta no tiene contratos a domicilio";
  contract.appendChild(empty);
  homeContracts.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.code;
    option.textContent = item.label ? `${item.label} (${item.code})` : item.code;
    contract.appendChild(option);
  });
  contract.value = store.contract || (homeContracts.length === 1 ? homeContracts[0].code : "");

  const optionName = input(`${prefix}Name`, "text", store.name, { maxlength: "100" });
  const daysMin = input(`${prefix}DaysMin`, "number", store.delivery_days_min, { min: "0", max: "90" });
  const daysMax = input(`${prefix}DaysMax`, "number", store.delivery_days_max, { min: "0", max: "90" });

  const row1 = document.createElement("div");
  row1.className = "filters-row";
  row1.append(field("Contrato", contract), field("Nombre que ve el comprador", optionName));
  const row2 = document.createElement("div");
  row2.className = "filters-row";
  row2.append(field("Llega en (días, mínimo)", daysMin), field("Llega en (días, máximo)", daysMax));

  const surchargePercent = input(`${prefix}SurchargePercent`, "number", store.surcharge_percent, { min: "0", max: "300", step: "0.01", placeholder: "0" });
  const surchargeAmount = input(`${prefix}SurchargeAmount`, "number", store.surcharge_amount, { min: "0", step: "0.01", placeholder: "0" });
  const freeFrom = input(`${prefix}FreeFrom`, "number", store.free_shipping_from, { min: "0", step: "0.01", placeholder: "Nunca" });
  const row3 = document.createElement("div");
  row3.className = "filters-row";
  row3.append(
    field("Recargo (%)", surchargePercent),
    field("Recargo fijo ($)", surchargeAmount),
    field("Envío gratis desde ($ del carrito)", freeFrom)
  );
  const pricingHint = document.createElement("p");
  pricingHint.className = "filters-hint";
  pricingHint.textContent =
    "El comprador paga lo que cotiza Andreani más el recargo; desde el monto de envío gratis no paga nada y el envío " +
    "lo pagás vos. Vacío = sin recargo / sin envío gratis. En Tiendanube, el recargo y el envío gratis que configures " +
    "en su panel para este medio de envío se aplican además de estos.";

  const problems = document.createElement("p");
  problems.className = "filters-hint";
  problems.textContent = store.problems?.length ? `Ahora no cotiza: ${store.problems.join("; ")}.` : "";
  problems.style.display = store.problems?.length ? "" : "none";

  const saveActions = document.createElement("div");
  saveActions.className = "form-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "btn btn-primary";
  save.textContent = "Guardar";
  saveActions.appendChild(save);

  const testRow = document.createElement("div");
  testRow.className = "filters-row";
  const testCp = input(`${prefix}TestCp`, "text", "", { maxlength: "8", placeholder: "Ej.: 5000" });
  const testKg = input(`${prefix}TestKg`, "number", "1", { min: "0", step: "0.1" });
  const testTotal = input(`${prefix}TestTotal`, "number", "", { min: "0", step: "0.01", placeholder: "Opcional" });
  testRow.append(field("Probar: código postal", testCp), field("Peso del carrito (kg)", testKg), field("Total del carrito ($)", testTotal));
  const testActions = document.createElement("div");
  testActions.className = "form-actions";
  testActions.style.justifyContent = "flex-start";
  const test = document.createElement("button");
  test.type = "button";
  test.className = "btn btn-outline btn-small";
  test.textContent = "Probar cotización";
  testActions.appendChild(test);

  const message = document.createElement("p");
  message.className = "card-message page-message";
  message.style.display = "none";

  body.append(enabledRow, row1, row2, row3, pricingHint, problems, saveActions, testRow, testActions, message);
  card.append(title, body);

  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const response = await apiFetch(`${ANDREANI_API}/checkout/`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          store: store.store,
          enabled: enabled.checked,
          contract: contract.value,
          name: optionName.value,
          delivery_days_min: daysMin.value === "" ? null : Number(daysMin.value),
          delivery_days_max: daysMax.value === "" ? null : Number(daysMax.value),
          surcharge_percent: surchargePercent.value,
          surcharge_amount: surchargeAmount.value,
          free_shipping_from: freeFrom.value,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo guardar."));
      state.textContent = data.enabled ? "Activado" : "Desactivado";
      cardMessage(
        card,
        data.enabled ? "Guardado: Andreani se ofrece en el checkout de esta tienda." : "Guardado: Andreani no se ofrece en esta tienda.",
        "success"
      );
    } catch (err) {
      if (err.isSessionExpired) return;
      cardMessage(card, err.message || "No se pudo guardar.");
    } finally {
      save.disabled = false;
    }
  });

  test.addEventListener("click", async () => {
    test.disabled = true;
    try {
      const response = await apiFetch(`${ANDREANI_API}/checkout/test/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ store: store.store, postal_code: testCp.value, weight_kg: testKg.value, cart_total: testTotal.value }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cotizar."));
      cardMessage(
        card,
        `El comprador pagaría ${Number(data.price) === 0 ? "nada (envío gratis)" : money.format(Number(data.price))}; ` +
          `a vos Andreani te cobra ${money.format(Number(data.cost))} (CP ${data.postal_code}, ${data.weight_kg} kg). ` +
          "Se prueba con lo guardado: si cambiaste algo, guardalo primero.",
        "success"
      );
    } catch (err) {
      if (err.isSessionExpired) return;
      cardMessage(card, err.message || "No se pudo cotizar.");
    } finally {
      test.disabled = false;
    }
  });
  return card;
}

async function load() {
  try {
    const [accountResponse, storesResponse] = await Promise.all([
      apiFetch(`${ANDREANI_API}/account/`),
      apiFetch(`${ANDREANI_API}/checkout/`),
    ]);
    if (!accountResponse.ok || !storesResponse.ok) throw new Error("load");
    const account = await accountResponse.json();
    const stores = (await storesResponse.json()).results || [];
    homeContracts = (account.contracts || []).filter((item) => item.kind === "home");

    const notice = el("accountNotice");
    const problems = [];
    if (!account.exists) problems.push("Todavía no cargaste tu cuenta de Andreani.");
    else if (!account.client_code) problems.push("A tu cuenta de Andreani le falta el código de cliente, que hace falta para cotizar.");
    if (account.last_error) problems.push(`Andreani rechazó tus credenciales: ${account.last_error}`);
    notice.textContent = problems.length ? `${problems.join(" ")} Completala en «Cuenta de Andreani».` : "";
    notice.style.display = problems.length ? "" : "none";

    const container = el("storeCards");
    container.innerHTML = "";
    if (!stores.length) {
      const empty = document.createElement("p");
      empty.className = "filters-hint";
      empty.textContent = "No tenés tiendas de Tiendanube o WooCommerce conectadas. Conectalas en «Mis tiendas».";
      container.appendChild(empty);
    }
    stores.forEach((store) => container.appendChild(renderStore(store)));
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No se pudo cargar la configuración.");
  }
}

document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  load();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
