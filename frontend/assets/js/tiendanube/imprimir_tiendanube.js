// "Imprimir rótulos" desde las acciones masivas de Ventas del admin de
// Tiendanube. Es un link de app (Portal de Partners → Links → Acciones
// masivas) que abre esta página con la tienda y los pedidos elegidos en la
// query string. Tiendanube no firma ese link: quién imprime lo decide la
// sesión de nuestra app (JWT), y el backend exige que la tienda sea suya
// (POST integrations/tiendanube/print-link/).
//
// Tiendanube no documenta el nombre de los parámetros, así que se aceptan
// los habituales (id[]=1&id[]=2, ids=1,2, ...). Si no se reconoce ninguno,
// la página muestra lo que llegó para poder ajustarlo.

const PRINT_LINK_URL = `${window.APP_CONFIG.API_BASE}/integrations/tiendanube/print-link/`;
// Sin sesión se guardan los pedidos y se manda a iniciar sesión; index.html
// vuelve acá al terminar (ver handleAuthSuccess).
const PENDING_PRINT_KEY = "pendingTiendanubePrint";
const STORE_PARAMS = ["store", "store_id", "storeId"];
const IDS_PARAM = /^(id|ids|order|orders|order_id|order_ids|orderIds)(\[\d*\])?$/;

function readPendingQuery() {
  try {
    const pending = localStorage.getItem(PENDING_PRINT_KEY);
    localStorage.removeItem(PENDING_PRINT_KEY);
    return pending || "";
  } catch {
    return "";
  }
}

function parseSelection(query) {
  const params = new URLSearchParams(query);
  const store = STORE_PARAMS.map((name) => params.get(name)).find(Boolean) || "";
  const ids = [];
  for (const [name, value] of params) {
    if (!IDS_PARAM.test(name)) continue;
    for (const part of value.split(",")) {
      const id = part.trim();
      if (id && !ids.includes(id)) ids.push(id);
    }
  }
  return { store, ids };
}

function showReceived(query) {
  const pre = document.getElementById("printReceived");
  const lines = [...new URLSearchParams(query)].map(([name, value]) => `${name} = ${value}`);
  pre.textContent = `Lo que llegó en el link:\n${lines.join("\n") || "(nada)"}`;
  pre.hidden = false;
}

function finish(title, text, type = "error") {
  document.getElementById("printTitle").textContent = title;
  document.getElementById("printText").textContent = text;
  showMessage(title, type);
}

async function printSelection() {
  const query = window.location.search.slice(1) || readPendingQuery();
  if (window.location.search) window.history.replaceState(null, "", window.location.pathname);

  if (!window.Auth.getAccessToken()) {
    try {
      localStorage.setItem(PENDING_PRINT_KEY, query);
    } catch {
      // Sin almacenamiento no se puede volver solo: habrá que repetir la acción.
    }
    window.location.replace("index.html");
    return;
  }

  const { store, ids } = parseSelection(query);
  if (!ids.length) {
    finish(
      "No llegaron pedidos para imprimir",
      "Elegí los pedidos en Ventas de Tiendanube y volvé a usar \"Imprimir rótulos\". Si ya lo hiciste, pasale a soporte lo que aparece abajo."
    );
    showReceived(query);
    return;
  }

  try {
    const response = await window.Auth.apiFetch(PRINT_LINK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store, ids }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.url) {
      throw new Error(getErrorMessage(data, "No se pudieron preparar los rótulos."));
    }
    if (!data.missing || !data.missing.length) {
      window.location.replace(data.url);
      return;
    }
    // Algunos pedidos ya no existen en la tienda: se avisa antes de abrir.
    document.getElementById("printOpenLink").href = data.url;
    document.getElementById("printActions").hidden = false;
    finish(
      `${data.count} rótulos listos`,
      `Estos pedidos ya no existen en Tiendanube y no se incluyeron: ${data.missing.join(", ")}.`,
      "success"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    finish("No se pudieron preparar los rótulos", err.message || "Probá de nuevo en unos minutos.");
    showReceived(query);
  }
}

printSelection();
