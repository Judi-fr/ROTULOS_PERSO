// Medidas del editor de rótulos (editor_rotulos.html): el selector "Medida"
// con el catálogo global de tamaños (GET /labels/sizes/, lo arma el admin en
// catalogos.html). Elegir una completa ancho y alto y avisa con un "input",
// así el editor se redibuja por el mismo camino que si se tipearan; cuando
// los números no coinciden con ninguna (tipeados, o de una plantilla
// abierta) queda en "Otra medida". updateLabelSize llama a sync().
// Se carga en el <head> con las demás piezas del editor: arranca cuando el
// DOM está listo.
document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  const select = document.getElementById("sizePreset");
  const widthInput = document.getElementById("widthCm");
  const heightInput = document.getElementById("heightCm");
  if (!select || !widthInput || !heightInput) return;

  let sizes = [];

  function sync() {
    const width = parseFloat(widthInput.value);
    const height = parseFloat(heightInput.value);
    const match = sizes.find((size) => Number(size.width_cm) === width && Number(size.height_cm) === height);
    select.value = match ? String(match.id) : "";
  }

  select.addEventListener("change", () => {
    const size = sizes.find((item) => String(item.id) === select.value);
    if (!size) return;
    widthInput.value = Number(size.width_cm);
    heightInput.value = Number(size.height_cm);
    widthInput.dispatchEvent(new Event("input"));
    heightInput.dispatchEvent(new Event("input"));
  });

  async function load() {
    try {
      const response = await window.Auth.apiFetch(`${window.APP_CONFIG.API_BASE}/labels/sizes/`);
      if (!response.ok) return;
      sizes = extractResults(await response.json());
      select.innerHTML =
        '<option value="">Otra medida</option>' +
        sizes
          .map((size) => `<option value="${size.id}">${escapeHtml(size.name)}</option>`)
          .join("");
      sync();
    } catch (err) {
      if (err.isSessionExpired) return;
    }
  }

  window.LabelEditorSizes = { sync };
  load();
});
