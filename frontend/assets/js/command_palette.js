// Buscador de la barra superior (Ctrl+K / ⌘K, o el botón "Buscar"): desde
// cualquier pantalla, una función de la app o un pedido por número,
// destinatario o seguimiento. Lo carga topbar.js; el índice de funciones es
// el mismo del panel (collectTools en utils.js, con el menú de
// GET /auth/users/me/dashboard/), y los pedidos salen de GET /orders/?search=.
// Un pedido abre Mis pedidos con esa búsqueda hecha (pedidos.html?buscar=).

(function () {
  if (window.AppPalette) return;
  const API = window.APP_CONFIG.API_BASE;
  const DASHBOARD_URL = `${API}/auth/users/me/dashboard/`;
  const ORDERS_URL = `${API}/orders/`;
  const MAX_TOOLS = 6;
  const MAX_ORDERS = 5;
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

  let dialog = null;
  let input = null;
  let list = null;
  let tools = null;
  let entries = [];
  let active = 0;
  let orderTimer = null;
  let orderRequest = 0;

  async function loadTools() {
    if (tools) return tools;
    try {
      const response = await apiFetch(DASHBOARD_URL);
      const data = response.ok ? await response.json() : {};
      tools = collectTools(data.sections || [], data.menu || []);
    } catch (err) {
      tools = [];
    }
    return tools;
  }

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "app-palette";
    dialog.setAttribute("aria-label", "Buscar");
    dialog.innerHTML = `
      <div class="app-palette-box">
        <div class="app-palette-field">
          <i class="ph ph-magnifying-glass" aria-hidden="true"></i>
          <input type="search" placeholder="Buscar una función o un pedido…" aria-label="Buscar una función o un pedido" autocomplete="off" role="combobox" aria-expanded="true" aria-controls="appPaletteList" />
          <kbd>Esc</kbd>
        </div>
        <ul class="app-palette-list" id="appPaletteList" role="listbox"></ul>
        <p class="app-palette-foot"><kbd>↑</kbd><kbd>↓</kbd> moverse · <kbd>Enter</kbd> abrir</p>
      </div>`;
    document.body.appendChild(dialog);
    input = dialog.querySelector("input");
    list = dialog.querySelector(".app-palette-list");
    input.addEventListener("input", search);
    input.addEventListener("keydown", onKey);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  function words() {
    return normalizeSearch(input.value).split(/\s+/).filter(Boolean);
  }

  function paint(orders = null) {
    const query = words();
    const toolMatches = query.length
      ? (tools || []).filter((tool) => query.every((word) => tool.haystack.includes(word))).slice(0, MAX_TOOLS)
      : [];
    entries = toolMatches.map((tool) => ({ kind: "tool", label: tool.label, hint: tool.section, url: tool.url, icon: "arrow-square-out" }));
    if (orders) {
      orders.slice(0, MAX_ORDERS).forEach((order) => {
        const number = order.external_number || order.id;
        const who = order.address?.recipient_name || "";
        const city = order.address?.city || "";
        entries.push({
          kind: "order",
          label: `Pedido #${number}${who ? ` · ${who}` : ""}`,
          hint: [order.status_label, city].filter(Boolean).join(" · "),
          url: `pedidos.html?buscar=${encodeURIComponent(number)}`,
          icon: "package",
        });
      });
    }
    active = Math.min(active, Math.max(entries.length - 1, 0));
    list.innerHTML = "";
    if (!query.length) {
      list.appendChild(note("Escribí el nombre de una función (planilla, impresora, tarifas…) o un número de pedido."));
      return;
    }
    let lastKind = "";
    entries.forEach((entry, index) => {
      if (entry.kind !== lastKind) {
        lastKind = entry.kind;
        const head = document.createElement("li");
        head.className = "app-palette-group";
        head.textContent = entry.kind === "tool" ? "Funciones" : "Pedidos";
        head.setAttribute("role", "presentation");
        list.appendChild(head);
      }
      const item = document.createElement("li");
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(index === active));
      item.className = index === active ? "active" : "";
      const link = document.createElement("a");
      link.href = entry.url;
      link.innerHTML = `<i class="ph ph-${entry.icon}" aria-hidden="true"></i><span></span><small></small>`;
      link.querySelector("span").textContent = entry.label;
      link.querySelector("small").textContent = entry.hint || "";
      item.appendChild(link);
      item.addEventListener("mousemove", () => {
        if (active === index) return;
        active = index;
        paint(orders);
      });
      list.appendChild(item);
    });
    if (!entries.length) {
      list.appendChild(note(orders === null && canSearchOrders() ? "Buscando…" : "No encontramos nada con eso. Probá con otra palabra."));
    }
    list.querySelector("li.active")?.scrollIntoView({ block: "nearest" });
    lastOrders = orders;
  }

  let lastOrders = null;

  function note(text) {
    const item = document.createElement("li");
    item.className = "app-palette-note";
    item.textContent = text;
    return item;
  }

  function canSearchOrders() {
    return sessionHasPermission("orders.view") && Boolean(window.Auth.getCurrentUser()?.team);
  }

  function search() {
    active = 0;
    paint(null);
    clearTimeout(orderTimer);
    const text = input.value.trim();
    if (!text || !canSearchOrders()) return;
    const request = ++orderRequest;
    orderTimer = setTimeout(async () => {
      try {
        const response = await apiFetch(`${ORDERS_URL}?${new URLSearchParams({ search: text })}`);
        const data = response.ok ? await response.json() : {};
        if (request === orderRequest) paint(extractResults(data));
      } catch (err) {
        if (request === orderRequest) paint([]);
      }
    }, 250);
  }

  function onKey(event) {
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && entries.length) {
      event.preventDefault();
      active = (active + (event.key === "ArrowDown" ? 1 : entries.length - 1)) % entries.length;
      paint(lastOrders);
    } else if (event.key === "Enter" && entries[active]) {
      event.preventDefault();
      window.location.href = entries[active].url;
    }
  }

  async function open() {
    if (!window.Auth?.hasSession?.()) return;
    if (!dialog) build();
    if (!dialog.open) dialog.showModal();
    input.select();
    paint(lastOrders);
    await loadTools();
    if (input.value.trim()) search();
  }

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      open();
    }
  });

  window.AppPalette = { open };
})();
