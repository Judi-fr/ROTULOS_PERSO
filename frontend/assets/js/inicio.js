// Página de presentación (inicio.html). Sin sesión ni API: todo pasa en el
// navegador. Sin JavaScript la página se lee entera; esto suma el rótulo que
// cambia con los datos, los pasos que acompañan al bajar, los interruptores,
// las entradas y el reloj del pie.
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  document.body.classList.add("js");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Con una sesión guardada (auth.js deja "user" en localStorage), "Entrar"
  // lleva directo al panel.
  try {
    if (localStorage.getItem("user")) {
      const signin = document.getElementById("homeSignin");
      signin.textContent = "Ir al panel";
      signin.href = "dashboard.html";
    }
  } catch (error) {
    /* sin almacenamiento: queda "Entrar" */
  }

  // --- Entradas ------------------------------------------------------------
  // Cada parte marcada .reveal sube al entrar en pantalla, en cascada dentro
  // de su sección.
  const revealed = document.querySelectorAll(".reveal");
  const bySection = new Map();
  revealed.forEach((el) => {
    const section = el.closest("section, footer, main") || document.body;
    const index = bySection.get(section) || 0;
    el.style.setProperty("--i", String(Math.min(index, 8)));
    bySection.set(section, index + 1);
  });
  if ("IntersectionObserver" in window && !reduceMotion) {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            io.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px" }
    );
    revealed.forEach((el) => io.observe(el));
  } else {
    revealed.forEach((el) => el.classList.add("is-in"));
  }

  // --- El rótulo de "Probá tu rótulo" ---------------------------------------
  // Números de seguimiento de muestra con la forma de cada transportista.
  const TRACKING = {
    Andreani: "360002611223450",
    "Correo Argentino": "CU081234567AR",
    OCA: "4000123456789012345",
  };
  const form = document.getElementById("demoForm");
  const label = document.getElementById("demoLabel");
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  function drawCodes(tracking) {
    if (window.JsBarcode) {
      try {
        window.JsBarcode("#lbBarcode", tracking, {
          format: "CODE128",
          displayValue: true,
          font: "Helvetica",
          fontSize: 16,
          height: 70,
          margin: 0,
          width: 2,
          textMargin: 4,
        });
      } catch (error) {
        /* un código raro no rompe la vista */
      }
    }
    const qrBox = document.getElementById("lbQr");
    if (window.qrcode && qrBox) {
      const qr = window.qrcode(0, "M");
      // Como el QR de verdad: lleva al seguimiento público (acá, de muestra).
      qr.addData(`${window.location.origin}/seguimiento.html?t=${tracking}`);
      qr.make();
      qrBox.innerHTML = qr.createSvgTag({ cellSize: 2, margin: 0, scalable: true });
    }
  }

  let tick = 0;
  function render() {
    const data = new FormData(form);
    const carrier = data.get("carrier") || "Andreani";
    set("lbStore", (data.get("store") || "").trim() || "Tu tienda");
    set("lbName", (data.get("name") || "").trim() || "Quien recibe");
    set("lbStreet", (data.get("street") || "").trim() || "Dirección");
    set("lbCity", (data.get("city") || "").trim() || "Localidad, provincia");
    set("lbCp", (data.get("cp") || "").replace(/\s+/g, "").toUpperCase() || "0000");
    set("lbCarrier", carrier);
    label.style.setProperty("--band", data.get("band") || "#1f3a8a");
    document.getElementById("lbFragile").hidden = !data.get("fragile");
    drawCodes(TRACKING[carrier] || TRACKING.Andreani);
    if (!reduceMotion) {
      label.classList.remove("is-updating");
      clearTimeout(tick);
      void label.offsetWidth;
      label.classList.add("is-updating");
      tick = setTimeout(() => label.classList.remove("is-updating"), 400);
    }
  }

  if (form && label) {
    form.addEventListener("input", render);
    form.addEventListener("change", render);
    form.addEventListener("submit", (event) => event.preventDefault());
    // Las librerías de códigos cargan con defer, antes que este archivo.
    render();
  }

  // --- Del pedido al correo: el fragmento sigue a la frase del centro -------
  const steps = [...document.querySelectorAll(".flow-step")];
  const flowUi = document.getElementById("flowUi");
  function setFlowState(step) {
    if (!flowUi || flowUi.dataset.state === step) return;
    flowUi.dataset.state = step;
    flowUi.querySelectorAll("[data-s1]").forEach((el) => {
      el.textContent = el.dataset[`s${step}`];
    });
    steps.forEach((el) => el.classList.toggle("is-active", el.dataset.step === step));
  }
  if ("IntersectionObserver" in window && steps.length) {
    const stepIo = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setFlowState(entry.target.dataset.step);
        });
      },
      { rootMargin: "-45% 0px -45% 0px" }
    );
    steps.forEach((el) => stepIo.observe(el));
  }

  // --- Interruptores -------------------------------------------------------
  document.querySelectorAll(".sw-toggle").forEach((button) => {
    const band = button.closest(".sw-band");
    const sync = () => band.classList.toggle("is-on", button.getAttribute("aria-checked") === "true");
    sync();
    button.addEventListener("click", () => {
      button.setAttribute("aria-checked", button.getAttribute("aria-checked") === "true" ? "false" : "true");
      sync();
    });
  });

  // --- El correo de 1876 y el de 1928, uno sobre otro ----------------------
  const photos = [...document.querySelectorAll("#worksPhoto img")];
  if (photos.length > 1 && !reduceMotion) {
    let current = 0;
    let timer = 0;
    const next = () => {
      photos[current].classList.remove("is-on");
      current = (current + 1) % photos.length;
      photos[current].classList.add("is-on");
    };
    const start = () => {
      clearInterval(timer);
      timer = setInterval(next, 5600);
    };
    start();
    document.addEventListener("visibilitychange", () => (document.hidden ? clearInterval(timer) : start()));
  }

  // --- Cierre: el email viaja al registro ----------------------------------
  // (register.html lo lee de ?email= y lo deja escrito.)

  // --- Reloj del pie: la hora de Argentina ---------------------------------
  const clock = document.getElementById("homeClock");
  if (clock) {
    const format = new Intl.DateTimeFormat("es-AR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Argentina/Buenos_Aires" });
    const paint = () => {
      const now = new Date();
      clock.textContent = format.format(now);
      clock.dateTime = now.toISOString();
    };
    paint();
    setInterval(paint, 30000);
  }
})();
