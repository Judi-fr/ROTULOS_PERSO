// ---------------------------------------------------------------------------
// Editor de rótulos: biblioteca de imágenes del comercio (fase 3).
//
// Lista, sube y oculta las imágenes (API /labels/images/) y ofrece el
// diálogo "Imágenes del comercio" para elegir una para una forma imagen.
// El logo de la tienda (image: "logo") sale del que muestra el editor o, si
// no hay, del de "Mi cuenta" (/auth/me/account/).
//
// Expone window.LabelEditorImages = { list, upload, remove, urlFor, logoUrl,
// loadLogo, isReady, listed, openPicker, onChange }.
// ---------------------------------------------------------------------------
(function () {
  const API = `${window.APP_CONFIG.API_BASE}/labels/images/`;
  const ACCOUNT_URL = `${window.APP_CONFIG.API_BASE}/auth/me/account/`;
  const MAX_MB = 4;

  let cache = null; // Array<{id,url,name,width_px,height_px}> una vez cargada
  let loading = null;
  let failed = false; // la lista no se pudo leer: se dibuja "no disponible" y no se reintenta sola
  let autoTried = false; // la carga automática (al dibujar) se intenta una sola vez
  const extra = new Map(); // id -> fila: imágenes ocultas o de plantillas públicas que el diseño usa
  const requested = new Set(); // ids ya pedidos con ?ids= (una vez por carga)
  let extraPending = 0;
  let accountLogo = null; // url del logo de "Mi cuenta" ("" si no tiene)
  let logoLoad = null;
  const listeners = [];

  function notify() {
    listeners.forEach((fn) => {
      try { fn(); } catch (error) { console.error(error); }
    });
  }

  async function errorOf(response, fallback) {
    const data = await response.json().catch(() => ({}));
    return getErrorMessage(data, fallback);
  }

  function list(force) {
    if (cache && !force) return Promise.resolve(cache);
    if (!loading) {
      loading = window.Auth.apiFetch(API)
        .then(async (response) => {
          if (!response.ok) throw new Error(await errorOf(response, "No se pudieron cargar las imágenes."));
          const data = await response.json();
          cache = Array.isArray(data) ? data : extractResults(data);
          failed = false;
          return cache;
        })
        .catch((error) => {
          failed = true;
          throw error;
        })
        .finally(() => { loading = null; });
    }
    return loading;
  }

  async function upload(file) {
    const body = new FormData();
    body.append("image", file);
    body.append("name", (file.name || "").replace(/\.[^.]+$/, "").slice(0, 80));
    const response = await window.Auth.apiFetch(API, { method: "POST", body });
    if (!response.ok) throw new Error(await errorOf(response, "No se pudo subir la imagen."));
    const created = await response.json();
    // La subida ya salió bien: la lista se actualiza sin volver a pedirla.
    if (cache) cache = [created, ...cache.filter((item) => item.id !== created.id)];
    notify();
    return created;
  }

  async function remove(id) {
    const response = await window.Auth.apiFetch(`${API}${id}/`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) throw new Error(await errorOf(response, "No se pudo borrar la imagen."));
    if (cache) cache = cache.filter((item) => item.id !== id);
    notify();
  }

  // Logo de "Mi cuenta": se pide una vez; sin él (o sin permiso) queda "".
  function loadLogo() {
    if (accountLogo !== null) return Promise.resolve(accountLogo);
    if (!logoLoad) {
      logoLoad = window.Auth.apiFetch(ACCOUNT_URL)
        .then((response) => (response.ok ? response.json() : {}))
        .then((data) => { accountLogo = (data && data.logo) || ""; notify(); return accountLogo; })
        .catch(() => { accountLogo = ""; return ""; });
    }
    return logoLoad;
  }

  // El logo que se ve ahora en el editor (lo subido en esta sesión manda).
  function logoUrl() {
    const img = document.getElementById("logoImg");
    if (img && img.style.display !== "none" && img.getAttribute("src")) return img.src;
    if (accountLogo === null) loadLogo();
    return accountLogo || null;
  }

  function listed() {
    return cache ? cache.concat([...extra.values()]) : null;
  }

  // ¿Ya se sabe qué hay? También cuando falló: ahí se dibuja "no disponible".
  function isReady() {
    return (cache !== null || failed) && extraPending === 0;
  }

  function findRow(id) {
    return (cache && cache.find((item) => item.id === id)) || extra.get(id) || null;
  }

  // Imágenes que el diseño usa y no están en la lista activa (borradas de la
  // biblioteca, o de una plantilla pública): se piden una vez con ?ids=. El
  // servidor solo devuelve las que el usuario puede usar.
  function loadMissing(ids) {
    const want = ids.filter((id) => typeof id === "number" && !findRow(id) && !requested.has(id));
    if (!want.length) return;
    want.forEach((id) => requested.add(id));
    const template = new URLSearchParams(window.location.search).get("template");
    const query = `?ids=${want.join(",")}` + (template && /^\d+$/.test(template) ? `&template=${template}` : "");
    extraPending += 1;
    window.Auth.apiFetch(API + query)
      .then(async (response) => {
        if (!response.ok) return;
        const rows = await response.json();
        (Array.isArray(rows) ? rows : []).forEach((row) => extra.set(row.id, row));
      })
      .catch(() => {})
      .finally(() => {
        extraPending -= 1;
        notify();
      });
  }

  // Al cargar un diseño: la lista y, después, lo que le falte.
  function prepare(ids) {
    autoTried = true;
    list().catch(() => {}).then(() => {
      notify();
      if (cache) loadMissing(ids);
    });
  }

  // URL para dibujar una forma imagen, o null si no está disponible.
  function urlFor(image, logo) {
    if (image === "logo") return logo || logoUrl();
    if (!cache && !failed) {
      // Una sola carga automática por página; mientras tanto no se dibuja nada.
      if (!autoTried) prepare([]);
      return null;
    }
    const found = findRow(image);
    return found ? found.url : null;
  }

  function onChange(fn) {
    listeners.push(fn);
  }

  // --- Diálogo ---------------------------------------------------------------
  function injectStyles() {
    if (document.getElementById("imgPickerStyles")) return;
    const style = document.createElement("style");
    style.id = "imgPickerStyles";
    style.textContent = `
      .img-picker { width: min(640px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); }
      .img-picker-body { display: flex; flex-direction: column; gap: 14px; padding: 22px; }
      .img-picker-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
      .img-picker-title { margin: 0; font-size: 18px; font-weight: 700; }
      .img-picker-sub { margin: 4px 0 0; font-size: 13px; color: #64748b; }
      .img-picker-close { border: 0; background: none; font-size: 22px; line-height: 1; color: #64748b; cursor: pointer; padding: 2px 6px; border-radius: 8px; }
      .img-picker-close:hover { background: #f1f5f9; }
      .img-drop { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding: 14px 16px;
        border: 1.5px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; transition: border-color .15s, background .15s; }
      .img-drop.over { border-color: #f97316; background: #fff7ed; }
      .img-drop-text { flex: 1 1 200px; font-size: 13px; color: #475569; }
      .img-picker-actions { display: flex; gap: 8px; flex-wrap: wrap; }
      .img-picker-msg { margin: 0; font-size: 13px; color: #b91c1c; }
      .img-picker-msg[hidden] { display: none; }
      .img-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 10px;
        overflow-y: auto; min-height: 120px; max-height: 340px; padding: 2px; }
      .img-tile { position: relative; display: flex; flex-direction: column; gap: 6px; padding: 8px; text-align: left;
        border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; cursor: pointer; font: inherit;
        transition: transform .15s, border-color .15s; }
      .img-tile:hover { border-color: #f97316; }
      .img-tile:active { transform: scale(0.98); }
      .img-tile:focus-visible { outline: 2px solid #f97316; outline-offset: 2px; }
      .img-thumb { aspect-ratio: 1; border-radius: 8px; overflow: hidden; display: grid; place-items: center;
        background: repeating-conic-gradient(#f1f5f9 0 25%, #fff 0 50%) 0 0 / 14px 14px; }
      .img-thumb img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
      .img-tile-name { font-size: 12px; color: #334155; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .img-tile-size { font-size: 11px; color: #94a3b8; }
      .img-tile-del { position: absolute; top: 4px; right: 4px; width: 24px; height: 24px; padding: 0; border: 0; border-radius: 50%;
        background: rgba(15, 23, 42, .72); color: #fff; font-size: 14px; line-height: 24px; cursor: pointer; opacity: 0; transition: opacity .15s; }
      .img-tile:hover .img-tile-del, .img-tile:focus-within .img-tile-del, .img-tile-del:focus-visible { opacity: 1; }
      .img-tile-del:hover { background: #dc2626; }
      .img-skel { aspect-ratio: .85; border-radius: 12px; background: linear-gradient(90deg, #f1f5f9, #e2e8f0, #f1f5f9);
        background-size: 200% 100%; animation: img-shimmer 1.2s linear infinite; }
      @keyframes img-shimmer { to { background-position: -200% 0; } }
      .img-empty { grid-column: 1 / -1; align-self: center; text-align: center; padding: 18px 8px; color: #64748b; font-size: 13px; }
      @media (hover: none) { .img-tile-del { opacity: 1; } }
    `;
    document.head.appendChild(style);
  }

  function openPicker(onPick) {
    injectStyles();
    const dialog = document.createElement("dialog");
    dialog.className = "app-dialog img-picker";
    dialog.setAttribute("aria-labelledby", "imgPickerTitle");
    dialog.innerHTML = `
      <div class="img-picker-body">
        <div class="img-picker-head">
          <div>
            <h2 class="img-picker-title" id="imgPickerTitle">Imágenes del comercio</h2>
            <p class="img-picker-sub">Elegí una para usarla en el rótulo o subí una nueva.</p>
          </div>
          <button type="button" class="img-picker-close" aria-label="Cerrar">&times;</button>
        </div>
        <div class="img-drop" id="imgDrop">
          <span class="img-drop-text">Arrastrá una imagen hasta acá o buscala en tu equipo. PNG, JPG o WebP, hasta ${MAX_MB} MB.</span>
          <div class="img-picker-actions">
            <button type="button" class="app-dialog-btn app-dialog-btn-primary" data-act="upload">Subir imagen</button>
            <button type="button" class="app-dialog-btn app-dialog-btn-secondary" data-act="logo">Usar el logo de la tienda</button>
          </div>
          <input type="file" accept="image/*" hidden />
        </div>
        <p class="img-picker-msg" role="alert" hidden></p>
        <div class="img-grid" aria-live="polite"></div>
      </div>`;
    document.body.appendChild(dialog);

    const grid = dialog.querySelector(".img-grid");
    const msg = dialog.querySelector(".img-picker-msg");
    const input = dialog.querySelector("input[type=file]");
    const drop = dialog.querySelector("#imgDrop");
    const uploadBtn = dialog.querySelector("[data-act=upload]");

    let busy = false; // subiendo: se ignoran más subidas y elecciones
    const say = (text) => { msg.textContent = text || ""; msg.hidden = !text; };
    const finish = (value) => {
      dialog.close();
      onPick(value);
    };

    function renderGrid(items) {
      grid.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "img-empty";
        empty.textContent = "Todavía no subiste imágenes. Subí una foto o un diseño y queda guardada para todos tus rótulos.";
        grid.appendChild(empty);
        return;
      }
      items.forEach((item) => {
        const tile = document.createElement("div");
        tile.className = "img-tile";
        tile.tabIndex = 0;
        tile.setAttribute("role", "button");
        const name = item.name || `Imagen ${item.id}`;
        tile.setAttribute("aria-label", `Usar ${name}`);
        const thumb = document.createElement("div");
        thumb.className = "img-thumb";
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = "";
        img.loading = "lazy";
        thumb.appendChild(img);
        const label = document.createElement("span");
        label.className = "img-tile-name";
        label.textContent = name;
        label.title = name;
        const size = document.createElement("span");
        size.className = "img-tile-size";
        size.textContent = `${item.width_px} × ${item.height_px} px`;
        const del = document.createElement("button");
        del.type = "button";
        del.className = "img-tile-del";
        del.title = "Borrar de la biblioteca";
        del.setAttribute("aria-label", `Borrar ${name}`);
        del.textContent = "×";
        tile.append(thumb, label, size, del);
        tile.addEventListener("click", (event) => {
          if (event.target === del || busy) return;
          finish(item.id);
        });
        tile.addEventListener("keydown", (event) => {
          if (event.target === tile && !busy && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            finish(item.id);
          }
        });
        del.addEventListener("click", async (event) => {
          event.stopPropagation();
          const ok = await appConfirm(
            `¿Borrar «${name}» de la biblioteca?\nLos rótulos y plantillas que ya la usan siguen imprimiéndola.`,
            { confirmLabel: "Borrar" },
          );
          if (!ok) return;
          try {
            await remove(item.id);
            say("");
            renderGrid(cache);
          } catch (error) {
            say(error.message);
          }
        });
        grid.appendChild(tile);
      });
    }

    function showSkeleton() {
      grid.replaceChildren();
      for (let i = 0; i < 4; i += 1) {
        const skel = document.createElement("div");
        skel.className = "img-skel";
        grid.appendChild(skel);
      }
    }

    async function load() {
      showSkeleton();
      try {
        renderGrid(await list(true));
        notify(); // el lienzo se redibuja con lo recién cargado
      } catch (error) {
        grid.replaceChildren();
        say(error.message);
      }
    }

    async function handleFile(file) {
      if (!file || busy) return;
      say("");
      if (!file.type.startsWith("image/")) return say("Elegí un archivo de imagen (PNG, JPG o WebP).");
      if (file.size > MAX_MB * 1024 * 1024) return say(`La imagen supera los ${MAX_MB} MB. Probá con una más liviana.`);
      busy = true;
      uploadBtn.disabled = true;
      uploadBtn.textContent = "Subiendo…";
      try {
        const created = await upload(file);
        // Si se cerró el diálogo mientras subía, la imagen queda en la
        // biblioteca pero no se pone en el rótulo.
        if (dialog.open) finish(created.id);
      } catch (error) {
        if (dialog.open) say(error.message);
      } finally {
        busy = false;
        uploadBtn.disabled = false;
        uploadBtn.textContent = "Subir imagen";
      }
    }

    uploadBtn.addEventListener("click", () => { if (!busy) input.click(); });
    input.addEventListener("change", () => {
      handleFile(input.files[0]);
      input.value = "";
    });
    dialog.querySelector("[data-act=logo]").addEventListener("click", async () => {
      if (busy) return;
      const url = await loadLogo().then(() => logoUrl());
      if (!url) return say("La tienda todavía no tiene logo. Cargalo en Mi cuenta o subilo con «Subir logo» en este editor.");
      finish("logo");
    });
    ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("over");
    }));
    ["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.remove("over");
    }));
    drop.addEventListener("drop", (event) => handleFile(event.dataTransfer.files[0]));
    // Un archivo soltado fuera de la zona no debe abrirse en el navegador.
    ["dragover", "drop"].forEach((name) => dialog.addEventListener(name, (event) => event.preventDefault()));
    dialog.querySelector(".img-picker-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => dialog.remove());

    dialog.showModal();
    load();
  }

  window.LabelEditorImages = { list, upload, remove, urlFor, logoUrl, loadLogo, isReady, listed, prepare, openPicker, onChange };
})();
