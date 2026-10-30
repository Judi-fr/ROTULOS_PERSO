// "La semana" del panel: por día, cuántos pedidos entraron y cuántos salieron
// (summary.week de GET /orders/summary/). Columnas agrupadas en SVG, sin
// librerías: dos series con leyenda, una línea de base y grilla tenue, el
// detalle de cada día al pasar el mouse o con el teclado, y la misma tabla
// plegada debajo para quien no ve el gráfico. Lo llama dashboard.js
// (loadToday) en cada actualización.

(function () {
  const SERIES = [
    { key: "entered", label: "Entraron", className: "week-entered" },
    { key: "dispatched", label: "Despachados", className: "week-dispatched" },
  ];
  const HEIGHT = 168;
  const PAD = { top: 12, right: 8, bottom: 26, left: 28 };
  const BAR_MAX = 24;
  const BAR_GAP = 2;
  const RADIUS = 4;
  const SVG_NS = "http://www.w3.org/2000/svg";

  let lastWeek = null;
  let observer = null;

  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  function dayOf(iso) {
    const [year, month, day] = iso.split("-").map(Number);
    return new Date(year, month - 1, day);
  }

  function shortDay(iso) {
    const date = dayOf(iso);
    const name = date.toLocaleDateString("es-AR", { weekday: "short" }).replace(".", "");
    return `${name} ${date.getDate()}`;
  }

  function longDay(iso) {
    const date = dayOf(iso);
    const name = date.toLocaleDateString("es-AR", { weekday: "long" });
    return `${name.charAt(0).toUpperCase()}${name.slice(1)} ${date.getDate()}/${date.getMonth() + 1}`;
  }

  // Tope del eje en un número redondo: 1, 2, 5, 10, 20, 50...
  function niceMax(value) {
    if (value <= 4) return Math.max(value, 1);
    const power = 10 ** Math.floor(Math.log10(value));
    const step = [1, 2, 5, 10].find((candidate) => candidate * power >= value);
    return step * power;
  }

  // Columna con la punta redondeada y la base recta, apoyada en la base.
  function barPath(x, y, width, height) {
    const r = Math.min(RADIUS, width / 2, height);
    return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
  }

  function renderChart(box, week) {
    const chart = box.querySelector("#weekChart");
    const tooltip = box.querySelector("#weekTooltip");
    const width = chart.clientWidth || 600;
    const top = niceMax(Math.max(...week.flatMap((day) => SERIES.map((serie) => day[serie.key]))));
    const plotW = width - PAD.left - PAD.right;
    const plotH = HEIGHT - PAD.top - PAD.bottom;
    const band = plotW / week.length;
    const barW = Math.max(4, Math.min(BAR_MAX, (band * 0.6 - BAR_GAP) / 2));
    const y = (value) => PAD.top + plotH - (value / top) * plotH;

    const root = svg("svg", { width, height: HEIGHT, viewBox: `0 0 ${width} ${HEIGHT}`, role: "img" });
    root.setAttribute("aria-label", "Pedidos que entraron y que se despacharon por día en los últimos 7 días");

    // Grilla: base, mitad y tope, con sus valores a la izquierda.
    const ticks = top <= 1 ? [0, top] : [0, top / 2, top];
    ticks.forEach((tick) => {
      const ty = y(tick);
      root.appendChild(svg("line", { x1: PAD.left, x2: width - PAD.right, y1: ty, y2: ty, class: tick ? "week-grid" : "week-base" }));
      const label = svg("text", { x: PAD.left - 6, y: ty + 4, class: "week-axis", "text-anchor": "end" });
      label.textContent = Number.isInteger(tick) ? String(tick) : "";
      root.appendChild(label);
    });

    week.forEach((day, index) => {
      const center = PAD.left + band * index + band / 2;
      const group = svg("g", { class: "week-day", tabindex: "0" });
      group.setAttribute("aria-label", `${longDay(day.date)}: entraron ${day.entered}, se despacharon ${day.dispatched}`);
      // Zona sensible: toda la franja del día, no solo las columnas.
      group.appendChild(svg("rect", { x: center - band / 2, y: PAD.top, width: band, height: plotH, class: "week-hit" }));
      SERIES.forEach((serie, serieIndex) => {
        const value = day[serie.key];
        if (!value) return;
        const x = center - barW - BAR_GAP / 2 + serieIndex * (barW + BAR_GAP);
        group.appendChild(svg("path", { d: barPath(x, y(value), barW, y(0) - y(value)), class: `week-bar ${serie.className}` }));
      });
      const label = svg("text", { x: center, y: HEIGHT - 8, class: `week-axis${index === week.length - 1 ? " is-today" : ""}`, "text-anchor": "middle" });
      // Angosto (celular): solo el número del día; el nombre sigue en el detalle y la tabla.
      const dayLabel = band < 48 ? String(dayOf(day.date).getDate()) : shortDay(day.date);
      label.textContent = index === week.length - 1 ? "hoy" : dayLabel;
      group.appendChild(label);

      const show = () => showTooltip(chart, tooltip, day, center, band);
      group.addEventListener("pointerenter", show);
      group.addEventListener("focus", show);
      group.addEventListener("pointerleave", () => (tooltip.hidden = true));
      group.addEventListener("blur", () => (tooltip.hidden = true));
      root.appendChild(group);
    });

    chart.replaceChildren(root);
  }

  // El detalle va al costado de la franja del día (a la derecha en la primera
  // mitad, a la izquierda en la segunda), para no tapar sus columnas.
  function showTooltip(chart, tooltip, day, center, band) {
    const title = document.createElement("strong");
    title.textContent = longDay(day.date);
    const rows = SERIES.map((serie) => {
      const row = document.createElement("span");
      row.className = "week-tip-row";
      const key = document.createElement("i");
      key.className = `week-key-line ${serie.className}`;
      const value = document.createElement("b");
      value.textContent = String(day[serie.key]);
      row.append(key, value, document.createTextNode(` ${serie.label.toLowerCase()}`));
      return row;
    });
    tooltip.replaceChildren(title, ...rows);
    tooltip.hidden = false;
    const toRight = center < chart.clientWidth / 2;
    const left = toRight ? center + band / 2 + 4 : center - band / 2 - 4 - tooltip.offsetWidth;
    tooltip.style.left = `${Math.max(0, Math.min(left, chart.clientWidth - tooltip.offsetWidth))}px`;
  }

  function renderTable(box, week) {
    const body = box.querySelector("#weekTableBody");
    body.replaceChildren(
      ...week.map((day) => {
        const row = document.createElement("tr");
        [longDay(day.date), day.entered, day.dispatched].forEach((value) => {
          const cell = document.createElement("td");
          cell.textContent = String(value);
          row.appendChild(cell);
        });
        return row;
      })
    );
  }

  function render(week) {
    const box = document.getElementById("weekBox");
    if (!box || !Array.isArray(week) || !week.length) return;
    const entered = week.reduce((sum, day) => sum + day.entered, 0);
    const dispatched = week.reduce((sum, day) => sum + day.dispatched, 0);
    // Sin movimiento en toda la semana no hay nada que mirar.
    box.hidden = !entered && !dispatched;
    if (box.hidden) return;
    box.querySelector("#weekSummary").textContent =
      `En los últimos 7 días entraron ${entered} y se despacharon ${dispatched}.`;
    lastWeek = week;
    renderChart(box, week);
    renderTable(box, week);
    if (!observer && window.ResizeObserver) {
      let lastWidth = box.querySelector("#weekChart").clientWidth;
      observer = new ResizeObserver(() => {
        const chartWidth = box.querySelector("#weekChart").clientWidth;
        if (chartWidth === lastWidth || !lastWeek) return;
        lastWidth = chartWidth;
        renderChart(box, lastWeek);
      });
      observer.observe(box.querySelector("#weekChart"));
    }
  }

  window.DashboardWeek = { render };
})();
