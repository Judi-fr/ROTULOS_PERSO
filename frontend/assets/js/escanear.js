// Escanear para despachar (escanear.html): la cámara del celular o una pistola
// lectora leen el rótulo, GET /orders/scan/?code= encuentra el pedido y, en el
// modo "despachar", POST /orders/<id>/ship/ lo marca despachado — con unos
// segundos para deshacer (appUndoable), porque el cambio avisa a la tienda.
//
// La cámara usa el lector del navegador (BarcodeDetector: Chrome en Android y
// en escritorio); donde no existe (Safari del iPhone, Firefox) se carga
// @zxing/browser de jsDelivr. Una pistola lectora "tipea" el código y Enter,
// así que el campo de texto sirve para las dos cosas.

(function () {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const ORDERS_URL = `${API_BASE}/orders/`;
  const ZXING_URL = "https://cdn.jsdelivr.net/npm/@zxing/browser@0.1.5/umd/zxing-browser.min.js";
  // La cámara lee el mismo código muchas veces por segundo: uno repetido se
  // ignora durante este tiempo.
  const SAME_CODE_MS = 4000;
  const DISPATCH_UNDO_SECONDS = 5;
  const SHIPPED = ["dispatched", "in_transit", "delivered"];
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

  const video = document.getElementById("scanVideo");
  const results = document.getElementById("scanResults");
  let stream = null;
  let zxingControls = null;
  let scanning = false;
  let lastCode = "";
  let lastCodeAt = 0;
  let busy = false;
  let count = 0;

  const mode = () => document.querySelector("input[name=scanMode]:checked")?.value || "dispatch";

  function orderNumber(order) {
    return order.external_number || order.id;
  }

  function orderSummary(order) {
    const address = order.address || {};
    return [address.recipient_name, [address.city, address.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ");
  }

  // Un pitido corto (y una vibración en el celular): con el rótulo en la mano
  // no se mira la pantalla en cada lectura.
  function feedback(ok) {
    try {
      const context = new (window.AudioContext || window.webkitAudioContext)();
      const tone = context.createOscillator();
      tone.frequency.value = ok ? 880 : 220;
      tone.connect(context.destination);
      tone.start();
      tone.stop(context.currentTime + (ok ? 0.08 : 0.25));
    } catch (err) {
      // Sin sonido no pasa nada.
    }
    if (navigator.vibrate) navigator.vibrate(ok ? 60 : [80, 60, 80]);
  }

  function addResult({ tone, title, detail, order }) {
    count += 1;
    document.getElementById("scanEmpty").hidden = true;
    document.getElementById("scanCount").textContent = count === 1 ? "1 lectura" : `${count} lecturas`;
    const item = document.createElement("li");
    item.className = `scan-result ${tone}`;
    const icon = { ok: "check-circle", warning: "warning", error: "x-circle", info: "package" }[tone] || "package";
    item.innerHTML = `<i class="ph ph-${icon}" aria-hidden="true"></i><div class="scan-result-text"><strong></strong><span></span></div>`;
    item.querySelector("strong").textContent = title;
    item.querySelector("span").textContent = detail;
    if (order) {
      const link = document.createElement("a");
      link.className = "scan-result-link";
      link.href = `pedidos.html?buscar=${encodeURIComponent(orderNumber(order))}`;
      link.textContent = "Ver en Mis pedidos";
      item.appendChild(link);
    }
    results.prepend(item);
    return item;
  }

  async function chooseOrder(matches) {
    const lines = matches.map((order, index) => `${index + 1}. #${orderNumber(order)} · ${order.store_name || "Cargado a mano"} · ${orderSummary(order)}`);
    const answer = await appPrompt(`Ese número está en ${matches.length} pedidos. ¿Cuál es?\n\n${lines.join("\n")}`, "1", {
      confirmLabel: "Elegir",
    });
    const index = Number(answer) - 1;
    return matches[index] || null;
  }

  async function dispatch(order) {
    const number = orderNumber(order);
    if (SHIPPED.includes(order.status)) {
      feedback(false);
      addResult({ tone: "warning", title: `#${number} ya estaba ${order.status_label.toLowerCase()}`, detail: orderSummary(order), order });
      return;
    }
    if (!order.is_shippable) {
      feedback(false);
      addResult({ tone: "error", title: `#${number} no se puede despachar`, detail: `Está ${order.status_label.toLowerCase()}.`, order });
      return;
    }
    feedback(true);
    const item = addResult({ tone: "info", title: `#${number}: se marca como despachado…`, detail: orderSummary(order), order });
    try {
      const done = await appUndoable(`Pedido #${number}: despachado`, async () => {
        const response = await apiFetch(`${ORDERS_URL}${order.id}/ship/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "dispatched" }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo despachar."));
        return true;
      }, { seconds: DISPATCH_UNDO_SECONDS });
      item.className = `scan-result ${done ? "ok" : "warning"}`;
      item.querySelector("i").className = `ph ph-${done ? "check-circle" : "arrow-counter-clockwise"}`;
      item.querySelector("strong").textContent = done ? `#${number} despachado` : `#${number}: deshecho, sigue sin despachar`;
    } catch (err) {
      if (err.isSessionExpired) return;
      item.className = "scan-result error";
      item.querySelector("i").className = "ph ph-x-circle";
      item.querySelector("strong").textContent = `#${number}: ${err.message}`;
    }
  }

  async function handleCode(raw) {
    const code = String(raw || "").trim();
    if (!code || busy) return;
    const now = Date.now();
    if (code === lastCode && now - lastCodeAt < SAME_CODE_MS) return;
    lastCode = code;
    lastCodeAt = now;
    busy = true;
    try {
      const response = await apiFetch(`${ORDERS_URL}scan/?${new URLSearchParams({ code })}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo buscar el pedido."));
      let order = data.matches[0];
      if (!data.matches.length) {
        feedback(false);
        addResult({ tone: "error", title: "No es un pedido de este comercio", detail: `Se leyó «${code}».` });
        return;
      }
      if (data.matches.length > 1) order = await chooseOrder(data.matches);
      if (!order) return;
      if (mode() === "find") {
        feedback(true);
        addResult({ tone: "info", title: `#${orderNumber(order)} · ${order.status_label}`, detail: orderSummary(order), order });
        return;
      }
      await dispatch(order);
    } catch (err) {
      if (err.isSessionExpired) return;
      feedback(false);
      addResult({ tone: "error", title: "No se pudo leer", detail: err.message });
    } finally {
      busy = false;
    }
  }

  // --- Cámara -----------------------------------------------------------------
  function showCamera(on) {
    video.hidden = !on;
    document.getElementById("scanFrame").hidden = !on;
    document.getElementById("scanIdle").hidden = on;
    document.getElementById("stopCameraBtn").hidden = !on;
  }

  async function nativeDetector() {
    if (!("BarcodeDetector" in window)) return null;
    try {
      const wanted = ["code_128", "qr_code", "ean_13", "code_39"];
      const supported = await window.BarcodeDetector.getSupportedFormats();
      const formats = wanted.filter((format) => supported.includes(format));
      return formats.length ? new window.BarcodeDetector({ formats }) : null;
    } catch (err) {
      return null;
    }
  }

  function loadZxing() {
    if (window.ZXingBrowser) return Promise.resolve(window.ZXingBrowser);
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = ZXING_URL;
      script.onload = () => resolve(window.ZXingBrowser);
      script.onerror = () => reject(new Error("No se pudo cargar el lector de códigos."));
      document.head.appendChild(script);
    });
  }

  async function startCamera() {
    const hint = document.getElementById("cameraHint");
    if (!navigator.mediaDevices?.getUserMedia) {
      hint.textContent = "Este navegador no deja usar la cámara. Usá una pistola lectora o escribí el número.";
      return;
    }
    try {
      const detector = await nativeDetector();
      if (detector) {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
        video.srcObject = stream;
        await video.play();
        showCamera(true);
        scanning = true;
        const tick = async () => {
          if (!scanning) return;
          try {
            const codes = await detector.detect(video);
            if (codes.length) handleCode(codes[0].rawValue);
          } catch (err) {
            // Un cuadro que no se pudo leer: se sigue con el próximo.
          }
          setTimeout(tick, 200);
        };
        tick();
        return;
      }
      const ZXing = await loadZxing();
      const reader = new ZXing.BrowserMultiFormatReader();
      showCamera(true);
      zxingControls = await reader.decodeFromConstraints(
        { video: { facingMode: "environment" } },
        video,
        (result) => {
          if (result) handleCode(result.getText());
        }
      );
    } catch (err) {
      showCamera(false);
      hint.textContent =
        err && err.name === "NotAllowedError"
          ? "No diste permiso para usar la cámara. Podés darlo en los ajustes del navegador, o usar una pistola lectora."
          : `No se pudo usar la cámara (${err.message || err}). Usá una pistola lectora o escribí el número.`;
    }
  }

  function stopCamera() {
    scanning = false;
    if (zxingControls) zxingControls.stop();
    zxingControls = null;
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    video.srcObject = null;
    showCamera(false);
  }

  // --- Arranque ---------------------------------------------------------------
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  if (!sessionHasPermission("orders.dispatch")) {
    document.querySelector("input[name=scanMode][value=find]").checked = true;
    document.querySelector("input[name=scanMode][value=dispatch]").disabled = true;
  }

  document.getElementById("startCameraBtn").addEventListener("click", startCamera);
  document.getElementById("stopCameraBtn").addEventListener("click", stopCamera);
  document.getElementById("scanForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.getElementById("scanInput");
    lastCode = "";
    handleCode(input.value);
    input.value = "";
    input.focus();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && (stream || zxingControls)) stopCamera();
  });
  document.getElementById("scanInput").focus();
})();
