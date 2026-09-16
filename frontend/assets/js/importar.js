// Importación de rótulos desde una foto (importar.html).
//
// Es el flujo de tres pasos que documenta apps/processing/urls.py:
//   1. POST /documents/documentos/        sube la foto (multipart)
//   2. POST /processing/importaciones/    el modelo la lee y devuelve una propuesta
//   3. el editor carga la propuesta (?importacion=<id>) y el usuario la guarda
//
// Además: GET y DELETE de documentos, GET de importaciones y
// POST /processing/importaciones/<id>/reintentar/.

const EDITOR = "/pedidos/dise%C3%B1orotulos.html";
const MAX_IMAGEN = 5 * 1024 * 1024;
const MAX_PDF = 32 * 1024 * 1024;

const $ = (id) => document.getElementById(id);

let documentos = [];
let siguienteDocumentos = null;
let importaciones = [];
let siguienteImportaciones = null;
// Documento o importación que se está leyendo ahora. Mientras dura (hasta un
// minuto) se bloquean las otras lecturas: cada una cuesta y tiene cupo.
let leyendo = null;

function avisar(texto, tipo = "info") {
  const caja = $("mensaje");
  caja.textContent = texto;
  caja.className = `aviso ${tipo}`;
  caja.hidden = !texto;
  if (texto) caja.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// --- Subida ---------------------------------------------------------------

async function subir(archivo) {
  // El backend valida tipo (por contenido, no por extensión) y tamaño; esto
  // solo evita esperar la subida de un archivo que va a rechazar seguro.
  const esPdf = archivo.type === "application/pdf";
  const maximo = esPdf ? MAX_PDF : MAX_IMAGEN;
  if (archivo.size > maximo) {
    avisar(`«${archivo.name}» pesa ${tamanoLegible(archivo.size)}; el máximo es ${tamanoLegible(maximo)}.`, "error");
    return;
  }

  const texto = $("dropzoneTexto");
  texto.innerHTML = `<span class="spinner"></span> Subiendo ${esc(archivo.name)}...`;
  const r = await Documentos.subir(archivo);
  texto.innerHTML = "<strong>Elegí un archivo</strong> o arrastralo acá";

  if (!r.ok) {
    avisar(`No se pudo subir «${archivo.name}».\n${r.error}`, "error");
    return;
  }
  avisar(`«${r.data.nombre_original}» subido. Ya podés leerlo.`, "ok");
  documentos.unshift(r.data);
  pintarDocumentos();
}

const zona = $("dropzone");
$("archivo").addEventListener("change", (e) => {
  if (e.target.files[0]) subir(e.target.files[0]);
  e.target.value = ""; // permite volver a elegir el mismo archivo
});
zona.addEventListener("dragover", (e) => {
  e.preventDefault();
  zona.classList.add("over");
});
zona.addEventListener("dragleave", () => zona.classList.remove("over"));
zona.addEventListener("drop", (e) => {
  e.preventDefault();
  zona.classList.remove("over");
  if (e.dataTransfer.files[0]) subir(e.dataTransfer.files[0]);
});

// --- Documentos -----------------------------------------------------------

function pintarDocumentos() {
  const cont = $("documentos");
  if (!documentos.length) {
    cont.innerHTML = '<div class="empty">Todavía no subiste archivos.</div>';
    return;
  }
  cont.innerHTML = `<table class="data">
    <thead><tr><th></th><th>Archivo</th><th>Tamaño</th><th>Subido</th><th></th></tr></thead>
    <tbody>${documentos.map(filaDocumento).join("")}</tbody>
  </table>`;
}

function filaDocumento(d) {
  const esImagen = d.tipo_mime.startsWith("image/");
  // La miniatura viene de /media/ del backend: un <img> de otro origen no
  // necesita CORS para mostrarse.
  const miniatura = esImagen
    ? `<img class="mini-thumb" src="${esc(d.archivo_url)}" alt="" loading="lazy" />`
    : '<div class="mini-thumb">PDF</div>';
  const ocupado = leyendo !== null;
  const esteLeyendo = leyendo === `doc-${d.id}`;
  return `<tr>
    <td>${miniatura}</td>
    <td>${esc(d.nombre_original)}</td>
    <td>${tamanoLegible(d.tamano_bytes)}</td>
    <td>${fechaCorta(d.subido_en)}</td>
    <td><div class="acciones">
      <button class="btn small primary" data-accion="leer" data-id="${d.id}" ${ocupado ? "disabled" : ""}>
        ${esteLeyendo ? '<span class="spinner"></span> Leyendo...' : "Leer rótulo"}
      </button>
      <button class="btn small danger" data-accion="borrar" data-id="${d.id}" ${ocupado ? "disabled" : ""}>Eliminar</button>
    </div></td>
  </tr>`;
}

async function cargarDocumentos(ruta) {
  const r = await Documentos.listar(ruta);
  if (!r.ok) {
    $("documentos").innerHTML = "";
    avisar(`No se pudieron cargar tus archivos.\n${r.error}`, "error");
    return;
  }
  documentos = documentos.concat(r.data.results);
  siguienteDocumentos = rutaDeSiguiente(r.data.next);
  $("masDocumentos").hidden = !siguienteDocumentos;
  pintarDocumentos();
}

// Una lectura sale bien (201) o mal (502). En los dos casos el backend
// devuelve la importación registrada, con su `error` si falló.
async function procesarLectura(promesa, clave) {
  leyendo = clave;
  pintarDocumentos();
  pintarImportaciones();
  avisar("Leyendo el rótulo. Puede tardar hasta un minuto; no cierres la página.", "info");

  const r = await promesa;
  leyendo = null;

  if (r.data && r.data.id) {
    importaciones = [r.data, ...importaciones.filter((i) => i.id !== r.data.id)];
  }
  pintarDocumentos();
  pintarImportaciones();

  if (r.ok) {
    avisar("Lectura completada. Abrila en el editor para revisarla.", "ok");
  } else if (r.status === 502 && r.data) {
    avisar(`La lectura falló: ${r.data.error || "error desconocido"}. Podés reintentarla.`, "error");
  } else if (r.status === 429) {
    avisar("Llegaste al máximo de 20 lecturas por hora. Esperá un rato antes de leer otro rótulo.", "warn");
  } else {
    avisar(`No se pudo leer el rótulo.\n${r.error}`, "error");
  }
}

$("documentos").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-accion]");
  if (!b || b.disabled) return;
  const id = Number(b.dataset.id);

  if (b.dataset.accion === "leer") {
    procesarLectura(Importaciones.crear(id), `doc-${id}`);
    return;
  }

  if (b.dataset.accion === "borrar") {
    const d = documentos.find((x) => x.id === id);
    const lecturas = importaciones.filter((i) => i.documento === id).length;
    // Borrar el documento borra en cascada sus lecturas (on_delete=CASCADE):
    // se avisa para que no sea una sorpresa ver desaparecer la tabla de abajo.
    const extra = lecturas ? `\nTambién se borran sus ${lecturas} lectura(s).` : "";
    if (!confirm(`¿Eliminar «${d.nombre_original}»?${extra}`)) return;
    const r = await Documentos.borrar(id);
    if (!r.ok) return avisar(`No se pudo eliminar.\n${r.error}`, "error");
    documentos = documentos.filter((x) => x.id !== id);
    importaciones = importaciones.filter((i) => i.documento !== id);
    pintarDocumentos();
    pintarImportaciones();
    avisar("Archivo eliminado.", "ok");
  }
});
$("masDocumentos").addEventListener("click", () => cargarDocumentos(siguienteDocumentos));

// --- Importaciones --------------------------------------------------------

const BADGE_ESTADO = {
  completada: "ok",
  error: "danger",
  procesando: "warn",
  pendiente: "",
};

function pintarImportaciones() {
  const cont = $("importaciones");
  if (!importaciones.length) {
    cont.innerHTML = '<div class="empty">Todavía no hay lecturas.</div>';
    return;
  }
  cont.innerHTML = `<table class="data">
    <thead><tr><th>Archivo</th><th>Estado</th><th>Confianza</th><th>Fecha</th><th></th></tr></thead>
    <tbody>${importaciones.map(filaImportacion).join("")}</tbody>
  </table>`;
}

function filaImportacion(i) {
  const confianza = i.confianza != null ? `${Math.round(i.confianza * 100)} %` : "—";
  const detalle = i.estado === "error" && i.error
    ? `<div class="subtitle" style="font-size:12px;margin-top:4px">${esc(i.error)}</div>`
    : "";
  const ocupado = leyendo !== null;
  const acciones = [];
  if (i.estado === "completada") {
    acciones.push(`<a class="btn small primary" href="${EDITOR}?importacion=${i.id}">Abrir en el editor</a>`);
  }
  acciones.push(
    `<button class="btn small" data-accion="reintentar" data-id="${i.id}" ${ocupado ? "disabled" : ""}>
      ${leyendo === `imp-${i.id}` ? '<span class="spinner"></span> Leyendo...' : "Reintentar"}
    </button>`
  );
  return `<tr>
    <td>${esc(i.documento_nombre)}${detalle}</td>
    <td><span class="badge ${BADGE_ESTADO[i.estado] || ""}">${esc(i.estado)}</span></td>
    <td>${confianza}</td>
    <td>${fechaCorta(i.creada_en)}</td>
    <td><div class="acciones">${acciones.join("")}</div></td>
  </tr>`;
}

async function cargarImportaciones(ruta) {
  const r = await Importaciones.listar(ruta);
  if (!r.ok) {
    $("importaciones").innerHTML = "";
    avisar(`No se pudieron cargar las lecturas.\n${r.error}`, "error");
    return;
  }
  importaciones = importaciones.concat(r.data.results);
  siguienteImportaciones = rutaDeSiguiente(r.data.next);
  $("masImportaciones").hidden = !siguienteImportaciones;
  pintarImportaciones();
}

$("importaciones").addEventListener("click", (e) => {
  const b = e.target.closest('[data-accion="reintentar"]');
  if (!b || b.disabled) return;
  const id = Number(b.dataset.id);
  if (!confirm("Reintentar vuelve a enviar el archivo al modelo y cuenta como una lectura nueva. ¿Continuar?")) return;
  // Reintentar crea otra importación (no pisa la anterior), así se pueden
  // comparar las dos lecturas.
  procesarLectura(Importaciones.reintentar(id), `imp-${id}`);
});
$("masImportaciones").addEventListener("click", () => cargarImportaciones(siguienteImportaciones));

window.addEventListener("beforeunload", (e) => {
  if (leyendo === null) return;
  e.preventDefault();
  e.returnValue = "";
});

if (exigirSesion()) {
  cargarDocumentos();
  cargarImportaciones();
}
