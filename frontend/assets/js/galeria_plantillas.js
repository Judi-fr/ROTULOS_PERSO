// Galería de "+ Nueva plantilla" (fase 5): "En blanco" y los diseños del
// sistema que se ofrecen como punto de partida, en su orden
// (GET /labels/templates/?gallery=1, ver apps/labels/gallery.py). Elegir uno
// abre el editor con una copia: editor_rotulos.html?nueva=plantilla&template=<id>
// guarda una plantilla NUEVA del comercio, nunca la del sistema.
(function () {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }

  const API_BASE = window.APP_CONFIG.API_BASE;
  const grid = document.getElementById("galleryGrid");
  const EDITOR_URL = "editor_rotulos.html?nueva=plantilla";

  function item(index) {
    const li = document.createElement("li");
    li.className = "gallery-item";
    li.style.setProperty("--index", index);
    return li;
  }

  function blankCard() {
    const li = item(0);
    const link = document.createElement("a");
    link.className = "gallery-card gallery-card-blank";
    link.href = EDITOR_URL;
    link.innerHTML =
      '<span class="gallery-sheet gallery-sheet-blank" aria-hidden="true">' +
      '<i class="ph ph-plus" aria-hidden="true"></i></span>';
    const name = document.createElement("span");
    name.className = "gallery-name";
    name.textContent = "En blanco";
    const text = document.createElement("span");
    text.className = "gallery-text";
    text.textContent = "Empezá de cero, con los datos en su lugar de siempre.";
    link.append(name, text);
    li.appendChild(link);
    return li;
  }

  function templateCard(template, index) {
    const li = item(index);
    const link = document.createElement("a");
    link.className = "gallery-card";
    link.href = `${EDITOR_URL}&template=${encodeURIComponent(template.id)}`;
    const sheet = document.createElement("span");
    sheet.className = "gallery-sheet";
    if (template.preview) {
      const img = document.createElement("img");
      img.src = template.preview;
      img.alt = `Rótulo de muestra con el diseño ${template.name}`;
      img.loading = "lazy";
      sheet.appendChild(img);
    } else {
      sheet.classList.add("gallery-sheet-missing");
      sheet.textContent = "Sin vista previa";
    }
    const name = document.createElement("span");
    name.className = "gallery-name";
    name.textContent = template.name;
    const text = document.createElement("span");
    text.className = "gallery-text";
    text.textContent = template.description || "";
    link.append(sheet, name, text);
    li.appendChild(link);
    return li;
  }

  function skeleton() {
    grid.setAttribute("aria-busy", "true");
    grid.innerHTML = "";
    for (let index = 0; index < 6; index += 1) {
      const li = item(index);
      li.innerHTML = '<span class="gallery-card gallery-card-loading"><span class="gallery-sheet"></span>' +
        '<span class="gallery-bar"></span><span class="gallery-bar short"></span></span>';
      grid.appendChild(li);
    }
  }

  async function load() {
    skeleton();
    try {
      const response = await window.Auth.apiFetch(`${API_BASE}/labels/templates/?gallery=1`);
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los diseños."));
      const templates = Array.isArray(data) ? data : data.results || [];
      grid.innerHTML = "";
      grid.appendChild(blankCard());
      templates.forEach((template, index) => grid.appendChild(templateCard(template, index + 1)));
      // Sin diseños en la galería (una base sin migrar) igual se puede
      // empezar en blanco: se avisa, no se bloquea.
      if (!templates.length) showMessage("Todavía no hay diseños para elegir. Podés empezar en blanco.");
    } catch (error) {
      if (error && error.isSessionExpired) return;
      console.error("Error al cargar la galería:", error);
      grid.innerHTML = "";
      grid.appendChild(blankCard());
      showMessage("No se pudieron cargar los diseños. Podés empezar en blanco o probar de nuevo en un rato.");
    } finally {
      grid.removeAttribute("aria-busy");
    }
  }

  load();
})();
