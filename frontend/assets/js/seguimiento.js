// Seguimiento público (seguimiento.html?t=<token>): GET /api/v1/tracking/<t>/
// sin sesión. Todo lo que llega se inserta con textContent: el nombre del
// comercio y el transportista son texto de terceros.

(function () {
  const API = window.APP_CONFIG.API_BASE;
  // Los pasos que ve el comprador, en orden; "Cancelado" va aparte.
  const STEPS = [
    { status: "created", label: "Recibido" },
    { status: "preparing", label: "En preparación" },
    { status: "dispatched", label: "Despachado" },
    { status: "in_transit", label: "En camino" },
    { status: "delivered", label: "Entregado" },
  ];
  const HEADLINES = {
    created: "El comercio recibió tu pedido.",
    preparing: "El comercio está preparando tu pedido.",
    dispatched: "Tu pedido ya salió.",
    in_transit: "Tu pedido está en camino.",
    delivered: "Tu pedido fue entregado.",
    cancelled: "Este pedido fue cancelado.",
  };

  const body = document.getElementById("trackingBody");

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function when(value) {
    if (!value) return "";
    const date = new Date(value);
    return date.toLocaleDateString("es-AR", { day: "numeric", month: "long" }) +
      " · " + date.toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
  }

  function showProblem(title, text) {
    document.getElementById("trackingCard").setAttribute("aria-busy", "false");
    document.getElementById("orderTitle").textContent = title;
    body.replaceChildren(el("p", "tracking-problem", text));
  }

  function render(data) {
    document.getElementById("trackingCard").setAttribute("aria-busy", "false");
    document.title = `Pedido #${data.number} · ${data.business || "Seguimiento"}`;
    document.getElementById("businessName").textContent = data.business || "";
    document.getElementById("orderTitle").textContent = `Pedido #${data.number}`;
    if (data.logo) {
      const logo = document.getElementById("businessLogo");
      logo.src = data.logo;
      logo.hidden = false;
    }

    const parts = [];
    const cancelled = data.status === "cancelled";
    const headline = el("p", `tracking-headline ${cancelled ? "cancelled" : ""}`, HEADLINES[data.status] || data.status_label);
    parts.push(headline);
    if (data.destination && !cancelled) parts.push(el("p", "tracking-destination", `Va a ${data.destination}.`));

    // Los pasos: hecho / actual / por venir, con la fecha de cada uno.
    const reached = new Map(data.events.map((event) => [event.status, event.at]));
    if (!cancelled) {
      const current = STEPS.findIndex((step) => step.status === data.status);
      const list = el("ol", "tracking-steps");
      STEPS.forEach((step, index) => {
        const item = el("li", index < current ? "done" : index === current ? "current" : "");
        item.appendChild(el("span", "tracking-dot"));
        const text = el("div", "tracking-step-text");
        text.appendChild(el("strong", "", step.label));
        if (index <= current && reached.get(step.status)) text.appendChild(el("small", "", when(reached.get(step.status))));
        item.appendChild(text);
        list.appendChild(item);
      });
      parts.push(list);
    } else if (reached.get("cancelled")) {
      parts.push(el("p", "tracking-destination", when(reached.get("cancelled"))));
    }

    if (data.carrier || data.tracking_number) {
      const box = el("section", "tracking-carrier");
      box.appendChild(el("p", "tracking-carrier-label", "Lo lleva"));
      box.appendChild(el("p", "tracking-carrier-name", data.carrier || "El transportista"));
      if (data.tracking_number) {
        const number = el("p", "tracking-number");
        number.append("Número de seguimiento: ", el("code", "", data.tracking_number));
        box.appendChild(number);
      }
      if (data.tracking_url) {
        const link = el("a", "btn btn-primary", `Ver el envío en ${data.carrier || "el transportista"}`);
        link.href = data.tracking_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        box.appendChild(link);
      }
      parts.push(box);
    }

    parts.push(el("p", "tracking-note", "Si tenés una consulta sobre tu compra, escribile al comercio donde la hiciste."));
    body.replaceChildren(...parts);
  }

  async function load() {
    const token = new URLSearchParams(location.search).get("t");
    if (!token) {
      showProblem("Link incompleto", "Este link no tiene los datos del pedido. Pedile al comercio el link de seguimiento.");
      return;
    }
    try {
      const response = await fetch(`${API}/tracking/${encodeURIComponent(token)}/`);
      if (response.status === 404) {
        showProblem("No encontramos el pedido", "El link no es válido o el pedido ya no está. Pedile al comercio que te lo mande de nuevo.");
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      render(await response.json());
    } catch (err) {
      showProblem("No pudimos cargar el seguimiento", "Probá de nuevo en un rato.");
    }
  }

  load();
})();
