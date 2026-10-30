// Estadísticas del comercio (pestaña Estadísticas de Mi comercio, GET
// /orders/stats/?days=7|30|90). Tres bloques, cada uno con su acceso de
// equipo: el backend manda solo los que la persona puede ver (``allowed``).
//   - Despacho: cuánto tarda un pedido en salir (números sueltos).
//   - Transportistas: una tabla, porque compara varias medidas a la vez.
//   - Compradores: provincias y ciudades como barras horizontales de una sola
//     serie (sin leyenda: el título dice qué se mide).
// Se carga la primera vez que se abre la pestaña y al cambiar el período.
// Todo lo que viene de pedidos (transportista, ciudad) es texto de terceros:
// va con textContent.

(function () {
  const STATS_URL = `${window.APP_CONFIG.API_BASE}/orders/stats/`;
  const PERIOD_KEY = "statsPeriodDays";
  const panel = document.querySelector('[data-panel="estadisticas"]');
  const content = document.getElementById("statsContent");
  if (!panel || !content) return;

  let days = readPeriod();
  let loaded = false;

  function readPeriod() {
    try {
      const saved = Number(localStorage.getItem(PERIOD_KEY));
      return [7, 30, 90].includes(saved) ? saved : 30;
    } catch (err) {
      return 30;
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function card(title, help) {
    const section = el("section", "profile-card stats-card");
    section.appendChild(el("div", "profile-card-title", title));
    const body = el("div", "profile-card-body");
    if (help) body.appendChild(el("p", "stats-help", help));
    section.appendChild(body);
    return { section, body };
  }

  // 30 → "30 h"; 50 → "2 d 2 h"; 0.4 → "menos de 1 h".
  function duration(hours) {
    if (hours === null || hours === undefined) return "—";
    if (hours < 1) return "menos de 1 h";
    const total = Math.round(hours);
    if (total < 48) return `${total} h`;
    const d = Math.floor(total / 24);
    const h = total % 24;
    return h ? `${d} d ${h} h` : `${d} d`;
  }

  function money(value) {
    if (value === null || value === undefined) return "—";
    return Number(value).toLocaleString("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 0 });
  }

  function tile(label, value, note) {
    const box = el("div", "stats-tile");
    box.append(el("span", "stats-tile-label", label), el("span", "stats-tile-value", value));
    if (note) box.appendChild(el("span", "stats-tile-note", note));
    return box;
  }

  function empty(body, text) {
    body.appendChild(el("p", "stats-empty", text));
  }

  function renderDispatch(data) {
    const { section, body } = card(
      "Tiempo de despacho",
      "Desde que entra un pedido hasta que sale, de los que entraron en el período."
    );
    if (!data.shipped) {
      empty(body, data.waiting ? `Todavía no salió ninguno; ${data.waiting} esperan.` : "No entraron pedidos en este período.");
      return section;
    }
    const grid = el("div", "stats-tiles");
    grid.append(
      tile("Promedio", duration(data.avg_hours)),
      tile("La mitad sale en menos de", duration(data.median_hours)),
      tile("Despachados en 24 h", `${data.within_24h}%`),
      tile("Despachados en 48 h", `${data.within_48h}%`)
    );
    body.appendChild(grid);
    body.appendChild(
      el(
        "p",
        "stats-foot",
        `${data.shipped} ${data.shipped === 1 ? "pedido salió" : "pedidos salieron"}` +
          (data.waiting ? ` · ${data.waiting} ${data.waiting === 1 ? "sigue esperando" : "siguen esperando"}` : "")
      )
    );
    return section;
  }

  function renderCarriers(rows) {
    const { section, body } = card(
      "Transportistas",
      "De los pedidos que salieron en el período. El costo es la cotización de los envíos creados desde la app."
    );
    if (!rows.length) {
      empty(body, "No salieron pedidos en este período.");
      return section;
    }
    const wrap = el("div", "stats-table-wrap");
    const table = el("table", "stats-table");
    const head = el("tr");
    ["Transportista", "Despachados", "Entregados", "Días hasta la entrega", "Con problemas", "Costo total", "Costo por envío"].forEach(
      (label) => head.appendChild(el("th", "", label))
    );
    table.appendChild(el("thead")).appendChild(head);
    const tbody = table.appendChild(el("tbody"));
    rows.forEach((row) => {
      const tr = el("tr");
      const problems = row.problems ? `${row.problems} (${row.problem_rate}%)` : "0";
      [
        row.carrier,
        row.shipped,
        row.delivered,
        row.avg_delivery_days === null ? "—" : `${row.avg_delivery_days.toLocaleString("es-AR")} d`,
        problems,
        money(row.cost_total),
        money(row.cost_avg),
      ].forEach((value, index) => {
        const cell = el(index ? "td" : "th", index === 4 && row.problems ? "is-alert" : "", value);
        if (!index) cell.scope = "row";
        tr.appendChild(cell);
      });
      tbody.appendChild(tr);
    });
    wrap.appendChild(table);
    body.appendChild(wrap);
    return section;
  }

  // Barras horizontales de una serie: el largo es la cantidad, el número va
  // al final de la barra y el nombre a la izquierda (texto, nunca color).
  function bars(items, label) {
    const list = el("ol", "stats-bars");
    list.setAttribute("aria-label", label);
    const max = Math.max(...items.map((item) => item.count), 1);
    items.forEach((item) => {
      const row = el("li", "stats-bar-row");
      const name = el("span", "stats-bar-name", item.name);
      if (item.state) name.title = item.state;
      const track = el("span", "stats-bar-track");
      const bar = el("span", "stats-bar");
      bar.style.width = `${Math.max(2, (item.count / max) * 100)}%`;
      track.append(bar, el("span", "stats-bar-value", item.count));
      row.append(name, track);
      list.appendChild(row);
    });
    return list;
  }

  function renderBuyers(data) {
    const { section, body } = card("Dónde están tus compradores", "Pedidos del período, sin los cancelados.");
    if (!data.total) {
      empty(body, "No entraron pedidos en este período.");
      return section;
    }
    const columns = el("div", "stats-columns");
    const states = el("div");
    states.appendChild(el("h3", "stats-subtitle", "Provincias"));
    if (data.states.length) states.appendChild(bars(data.states, "Pedidos por provincia"));
    else empty(states, "Los pedidos no tienen provincia.");
    const cities = el("div");
    cities.appendChild(el("h3", "stats-subtitle", "Ciudades"));
    if (data.cities.length) cities.appendChild(bars(data.cities, "Pedidos por ciudad"));
    else empty(cities, "Los pedidos no tienen ciudad.");
    columns.append(states, cities);
    body.appendChild(columns);
    body.appendChild(el("p", "stats-foot", `${data.total} ${data.total === 1 ? "pedido" : "pedidos"} en total`));
    return section;
  }

  async function load() {
    loaded = true;
    content.classList.add("is-loading");
    try {
      const response = await window.Auth.apiFetch(`${STATS_URL}?days=${days}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron leer las estadísticas."));
      const blocks = [];
      if (data.dispatch) blocks.push(renderDispatch(data.dispatch));
      if (data.carriers) blocks.push(renderCarriers(data.carriers));
      if (data.buyers) blocks.push(renderBuyers(data.buyers));
      content.replaceChildren(...blocks);
    } catch (err) {
      if (err.isSessionExpired) return;
      content.replaceChildren(el("p", "stats-empty", err.message));
    } finally {
      content.classList.remove("is-loading");
    }
  }

  function syncPeriodButtons() {
    panel.querySelectorAll(".stats-period-btn").forEach((button) => {
      button.setAttribute("aria-pressed", String(Number(button.dataset.days) === days));
    });
  }

  panel.querySelectorAll(".stats-period-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const chosen = Number(button.dataset.days);
      if (chosen === days) return;
      days = chosen;
      try {
        localStorage.setItem(PERIOD_KEY, String(days));
      } catch (err) {
        /* sin almacenamiento: el período vale para esta visita */
      }
      syncPeriodButtons();
      load();
    });
  });
  syncPeriodButtons();

  document.addEventListener("team:tab-shown", (event) => {
    if (event.detail === "estadisticas" && !loaded) load();
  });
  // equipo.js ya eligió la pestaña antes de que cargue este script.
  if (!panel.hidden) load();
})();
