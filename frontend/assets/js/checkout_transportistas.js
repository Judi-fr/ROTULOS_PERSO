// El precio de los transportistas en el checkout de las tiendas
// (checkout_transportistas.html). Backend: /api/v1/carriers/checkout/
// (apps/carriers/checkout_views.py). Una tarjeta por tienda cuyo checkout nos
// pregunta el precio y, adentro, un bloque por transportista con cuenta:
// activar, servicio (solo a domicilio), nombre de la opción, plazo, recargo,
// envío gratis desde un monto, y "Probar" con un CP, un peso y un total de carrito.
//
// Los nombres de tienda vienen de la plataforma: se insertan con textContent.
// Sesión y apiFetch salen de auth.js; showMessage y getErrorMessage, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const CHECKOUT_API = `${API_BASE}/carriers/checkout`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" });

function el(id) {
  return document.getElementById(id);
}

function make(tag, { className = "", text = "", attrs = {} } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function field(labelText, input) {
  const wrap = make("div", { className: "field" });
  const label = make("label", { text: labelText });
  label.htmlFor = input.id;
  wrap.append(label, input);
  return wrap;
}

function input(id, type, value, attrs = {}) {
  const node = make("input", { attrs: { id, type, ...attrs } });
  if (value !== null && value !== undefined) node.value = value;
  return node;
}

function blockMessage(block, text, type = "error") {
  const box = block.querySelector(".card-message");
  box.textContent = text;
  box.className = `card-message page-message ${type}`;
  box.style.display = text ? "" : "none";
}

// Un transportista dentro de la tarjeta de una tienda.
function renderCarrier(store, carrier, settings) {
  const prefix = `store${store.store}${carrier.code}`;
  const block = make("details", { className: "carrier-checkout" });
  block.open = settings.enabled;
  const summary = make("summary", { className: "carrier-checkout-summary" });
  const title = make("strong", { text: carrier.name });
  const state = make("span", { className: "selection-count", text: settings.enabled ? "Activado" : "Desactivado" });
  summary.append(title, state);
  block.appendChild(summary);

  const enabledRow = make("label");
  enabledRow.style.display = "flex";
  enabledRow.style.gap = "8px";
  enabledRow.style.alignItems = "center";
  const enabled = input(`${prefix}Enabled`, "checkbox");
  enabled.checked = settings.enabled;
  enabledRow.append(enabled, document.createTextNode(`Ofrecer ${carrier.name} en el checkout de esta tienda`));

  const contract = make("select", { attrs: { id: `${prefix}Contract` } });
  const empty = make("option", {
    text: carrier.home_contracts.length ? "— elegí un servicio —" : "Tu cuenta no tiene servicios a domicilio",
    attrs: { value: "" },
  });
  contract.appendChild(empty);
  carrier.home_contracts.forEach((item) => {
    contract.appendChild(make("option", { text: item.label ? `${item.label} (${item.service || item.code})` : item.code, attrs: { value: item.code } }));
  });
  contract.value = settings.contract || (carrier.home_contracts.length === 1 ? carrier.home_contracts[0].code : "");

  const optionName = input(`${prefix}Name`, "text", settings.name, { maxlength: "100" });
  const daysMin = input(`${prefix}DaysMin`, "number", settings.delivery_days_min, { min: "0", max: "90" });
  const daysMax = input(`${prefix}DaysMax`, "number", settings.delivery_days_max, { min: "0", max: "90" });
  const row1 = make("div", { className: "filters-row" });
  row1.append(field("Servicio", contract), field("Nombre que ve el comprador", optionName));
  const row2 = make("div", { className: "filters-row" });
  row2.append(field("Llega en (días, mínimo)", daysMin), field("Llega en (días, máximo)", daysMax));

  const surchargePercent = input(`${prefix}SurchargePercent`, "number", settings.surcharge_percent, { min: "0", max: "300", step: "0.01", placeholder: "0" });
  const surchargeAmount = input(`${prefix}SurchargeAmount`, "number", settings.surcharge_amount, { min: "0", step: "0.01", placeholder: "0" });
  const freeFrom = input(`${prefix}FreeFrom`, "number", settings.free_shipping_from, { min: "0", step: "0.01", placeholder: "Nunca" });
  const row3 = make("div", { className: "filters-row" });
  row3.append(field("Recargo (%)", surchargePercent), field("Recargo fijo ($)", surchargeAmount), field("Envío gratis desde ($ del carrito)", freeFrom));
  const pricingHint = make("p", {
    className: "filters-hint",
    text:
      `El comprador paga lo que cotiza ${carrier.name} más el recargo; desde el monto de envío gratis no paga nada y el ` +
      "envío lo pagás vos. Vacío = sin recargo / sin envío gratis. En Tiendanube, el recargo y el envío gratis que " +
      "configures en su panel para este medio de envío se aplican además de estos.",
  });

  const problems = make("p", { className: "filters-hint", text: settings.problems?.length ? `Ahora no cotiza: ${settings.problems.join("; ")}.` : "" });
  problems.style.display = settings.problems?.length ? "" : "none";

  const saveActions = make("div", { className: "form-actions" });
  const save = make("button", { className: "btn btn-primary", text: "Guardar", attrs: { type: "button" } });
  saveActions.appendChild(save);

  const testRow = make("div", { className: "filters-row" });
  const testCp = input(`${prefix}TestCp`, "text", "", { maxlength: "8", placeholder: "Ej.: 5000" });
  const testKg = input(`${prefix}TestKg`, "number", "1", { min: "0", step: "0.1" });
  const testTotal = input(`${prefix}TestTotal`, "number", "", { min: "0", step: "0.01", placeholder: "Opcional" });
  testRow.append(field("Probar: código postal", testCp), field("Peso del carrito (kg)", testKg), field("Total del carrito ($)", testTotal));
  const testActions = make("div", { className: "form-actions" });
  testActions.style.justifyContent = "flex-start";
  const test = make("button", { className: "btn btn-outline btn-small", text: "Probar cotización", attrs: { type: "button" } });
  testActions.appendChild(test);

  const message = make("p", { className: "card-message page-message" });
  message.style.display = "none";

  block.append(enabledRow, row1, row2, row3, pricingHint, problems, saveActions, testRow, testActions, message);

  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const response = await apiFetch(`${CHECKOUT_API}/`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          store: store.store,
          carrier: carrier.code,
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
      problems.style.display = "none";
      blockMessage(
        block,
        data.enabled
          ? `Guardado: ${carrier.name} se ofrece en el checkout de esta tienda.`
          : `Guardado: ${carrier.name} no se ofrece en esta tienda.`,
        "success"
      );
    } catch (err) {
      if (err.isSessionExpired) return;
      blockMessage(block, err.message || "No se pudo guardar.");
    } finally {
      save.disabled = false;
    }
  });

  test.addEventListener("click", async () => {
    test.disabled = true;
    try {
      const response = await apiFetch(`${CHECKOUT_API}/test/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          store: store.store,
          carrier: carrier.code,
          postal_code: testCp.value,
          weight_kg: testKg.value,
          cart_total: testTotal.value,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cotizar."));
      blockMessage(
        block,
        `El comprador pagaría ${Number(data.price) === 0 ? "nada (envío gratis)" : money.format(Number(data.price))}; ` +
          `a vos ${carrier.name} te cobra ${money.format(Number(data.cost))} (CP ${data.postal_code}, ${data.weight_kg} kg). ` +
          "Se prueba con lo guardado: si cambiaste algo, guardalo primero.",
        "success"
      );
    } catch (err) {
      if (err.isSessionExpired) return;
      blockMessage(block, err.message || "No se pudo cotizar.");
    } finally {
      test.disabled = false;
    }
  });
  return block;
}

function renderStore(store, carriers) {
  const card = make("section", { className: "profile-card" });
  const title = make("div", { className: "profile-card-title", text: `${store.store_name} (${store.platform_label})` });
  const body = make("div", { className: "profile-card-body" });
  carriers.forEach((carrier) => body.appendChild(renderCarrier(store, carrier, store.carriers[carrier.code] || {})));
  card.append(title, body);
  return card;
}

async function load() {
  try {
    const response = await apiFetch(`${CHECKOUT_API}/`);
    if (!response.ok) throw new Error("load");
    const data = await response.json();
    // Solo los transportistas con cuenta: el resto no tiene con qué cotizar.
    const carriers = (data.carriers || []).filter((carrier) => carrier.account_exists);
    const stores = data.stores || [];

    const notice = el("accountNotice");
    // Sin ninguna cuenta lo dice el aviso de abajo (missingCarriers), con los links.
    const problems = [];
    carriers
      .filter((carrier) => carrier.account_problems.length)
      .forEach((carrier) => problems.push(`${carrier.name}: ${carrier.account_problems.join("; ")}.`));
    notice.textContent = problems.join(" ");
    notice.style.display = problems.length ? "" : "none";

    // Sin esto, un transportista sin cuenta (p. ej. Correo Argentino) no aparecía
    // en ningún lado de esta página y parecía que no existía.
    const missing = (data.carriers || []).filter((carrier) => !carrier.account_exists);
    const missingNotice = el("missingCarriers");
    missingNotice.replaceChildren();
    if (missing.length) {
      missingNotice.append(carriers.length ? "También podés cotizar con " : "Podés cotizar con ");
      missing.forEach((carrier, index) => {
        if (index) missingNotice.append(index === missing.length - 1 ? " y " : ", ");
        missingNotice.appendChild(
          make("a", { text: carrier.name, attrs: { href: `transportista.html?carrier=${encodeURIComponent(carrier.code)}` } })
        );
      });
      missingNotice.append(": primero conectá tu cuenta.");
    }
    missingNotice.hidden = !missing.length;

    const container = el("storeCards");
    container.replaceChildren();
    if (!stores.length) {
      container.appendChild(
        make("p", { className: "filters-hint", text: "No tenés tiendas de Tiendanube o WooCommerce conectadas. Conectalas en «Mis tiendas»." })
      );
    }
    if (carriers.length) stores.forEach((store) => container.appendChild(renderStore(store, carriers)));
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No se pudo cargar la configuración.");
  }
}

document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  load();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
