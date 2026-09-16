// Listado de plantillas (plantillas_rotulos.html).
//
// Endpoints: GET /labels/plantillas/ (paginado), DELETE /labels/plantillas/<id>/
// y POST /labels/plantillas/<id>/renderizar/ para las miniaturas, la vista
// previa y el PDF. Las miniaturas salen del mismo motor que imprime, así que
// lo que se ve en la tarjeta es lo que va a salir en el papel.

const EDITOR = "/pedidos/dise%C3%B1orotulos.html";

// DPI bajo para las miniaturas: una tarjeta de 200 px no necesita más, y a
// 300 DPI cada miniatura tardaría y pesaría varias veces lo mismo.
const DPI_MINIATURA = 40;
const DPI_VISTA_PREVIA = 150;

// Cuántas miniaturas se piden a la vez. El servidor de desarrollo atiende
// pocas peticiones en paralelo; pedirlas todas juntas demora también el resto
// de la pantalla.
const MINIATURAS_EN_PARALELO = 3;

const contenido = document.getElementById("content");
const buscador = document.getElementById("search");
const contador = document.getElementById("contador");
const botonMas = document.getElementById("cargarMas");
const mensaje = document.getElementById("mensaje");

let plantillas = [];
let siguiente = null;
const miniaturas = new Map(); // id -> object URL, para no renderizar dos veces

function avisar(texto) {
  mensaje.textContent = texto;
  mensaje.hidden = !texto;
}

function coincide(p, filtro) {
  if (!filtro) return true;
  const texto = `${p.nombre} ${p.descripcion}`.toLowerCase();
  return texto.includes(filtro);
}

function pintar() {
  const filtro = buscador.value.trim().toLowerCase();
  const visibles = plantillas.filter((p) => coincide(p, filtro));

  contador.textContent = plantillas.length
    ? `${visibles.length} de ${plantillas.length}${siguiente ? "+" : ""}`
    : "";

  if (!plantillas.length) {
    contenido.innerHTML = `<div class="empty">Todavía no hay plantillas. Creá una nueva o importala desde una foto.</div>`;
    return;
  }
  if (!visibles.length) {
    // La búsqueda filtra lo ya cargado: el backend no tiene un parámetro de
    // búsqueda, así que si hay más páginas se avisa para no confundir.
    contenido.innerHTML = `<div class="empty">Nada coincide con «${esc(buscador.value)}»${siguiente ? " entre las plantillas cargadas. Probá «Cargar más»." : "."}</div>`;
    return;
  }

  contenido.innerHTML = `<div class="grid">${visibles.map(tarjeta).join("")}</div>`;
  visibles.forEach((p) => {
    if (miniaturas.has(p.id)) mostrarMiniatura(p.id, miniaturas.get(p.id));
  });
  pedirMiniaturas(visibles.filter((p) => !miniaturas.has(p.id)));
}

function tarjeta(p) {
  const elementos = p.elementos.length;
  return `
    <article class="card" data-id="${p.id}">
      <div class="card-thumb" data-accion="ver" data-id="${p.id}">
        <span class="thumb-msg" id="thumb-${p.id}"><span class="spinner"></span></span>
      </div>
      <div class="card-body">
        <h3>${esc(p.nombre)} ${p.activa ? "" : '<span class="badge warn">inactiva</span>'}</h3>
        <div class="card-meta">
          ${Number(p.ancho_mm)} × ${Number(p.alto_mm)} mm · ${elementos} elemento${elementos === 1 ? "" : "s"}<br />
          ${p.creada_por_email ? esc(p.creada_por_email) + " · " : ""}${fechaCorta(p.actualizada_en)}
        </div>
      </div>
      <div class="card-actions">
        <a class="btn small primary" href="${EDITOR}?id=${p.id}">Editar</a>
        <button class="btn small" data-accion="pdf" data-id="${p.id}">PDF</button>
        <button class="btn small danger" data-accion="borrar" data-id="${p.id}">Eliminar</button>
      </div>
    </article>`;
}

function mostrarMiniatura(id, url) {
  const hueco = document.getElementById(`thumb-${id}`);
  if (!hueco) return;
  if (url) {
    hueco.outerHTML = `<img src="${url}" alt="Miniatura" id="thumb-${id}" />`;
  } else {
    hueco.innerHTML = "Sin vista previa";
  }
}

async function pedirMiniaturas(lista) {
  const cola = [...lista];
  const trabajador = async () => {
    while (cola.length) {
      const p = cola.shift();
      const r = await renderizarPlantilla(p.id, { formato: "png", dpi: DPI_MINIATURA });
      // Si falla (p. ej. 501: falta una fuente TTF en esta máquina) la tarjeta
      // queda sin imagen, pero el PDF puede salir igual: no es un error fatal.
      const url = r.ok ? URL.createObjectURL(r.blob) : null;
      miniaturas.set(p.id, url);
      mostrarMiniatura(p.id, url);
    }
  };
  await Promise.all(Array.from({ length: MINIATURAS_EN_PARALELO }, trabajador));
}

async function cargar(ruta) {
  const r = await Plantillas.listar(ruta);
  if (!r.ok) {
    contenido.innerHTML = "";
    avisar(`No se pudieron cargar las plantillas. ${r.error}`);
    return;
  }
  avisar("");
  plantillas = plantillas.concat(r.data.results);
  siguiente = rutaDeSiguiente(r.data.next);
  botonMas.hidden = !siguiente;
  pintar();
}

// --- Vista previa ---------------------------------------------------------

const overlay = document.getElementById("modalOverlay");
let enModal = null;

async function abrirVistaPrevia(id) {
  const p = plantillas.find((x) => x.id === id);
  if (!p) return;
  enModal = p;
  document.getElementById("modalNombre").textContent = p.nombre;
  document.getElementById("modalTamano").textContent = `${Number(p.ancho_mm)} × ${Number(p.alto_mm)} mm (${p.orientacion})`;
  document.getElementById("modalElementos").textContent = p.elementos.length;
  document.getElementById("modalAutor").textContent = p.creada_por_email || "—";
  document.getElementById("modalFecha").textContent = fechaCorta(p.actualizada_en);
  const img = document.getElementById("modalImg");
  img.removeAttribute("src");
  img.alt = "Generando vista previa...";
  overlay.classList.add("open");

  // Vista previa sin datos: cada variable se dibuja con su etiqueta.
  const r = await renderizarPlantilla(id, { formato: "png", dpi: DPI_VISTA_PREVIA });
  if (enModal !== p) return; // se cerró o se abrió otra mientras tanto
  if (r.ok) {
    img.src = URL.createObjectURL(r.blob);
  } else {
    img.alt = `No se pudo generar la vista previa: ${r.error}`;
  }
}

function cerrarModal() {
  overlay.classList.remove("open");
  enModal = null;
}

async function descargarPdf(id, boton) {
  const original = boton.textContent;
  boton.disabled = true;
  boton.textContent = "Generando...";
  const r = await renderizarPlantilla(id, { formato: "pdf" });
  boton.disabled = false;
  boton.textContent = original;
  if (!r.ok) return alert(`No se pudo generar el PDF.\n${r.error}`);
  descargarBlob(r.blob, `rotulo-${id}.pdf`);
}

// --- Eventos --------------------------------------------------------------

contenido.addEventListener("click", async (e) => {
  const objetivo = e.target.closest("[data-accion]");
  if (!objetivo) return;
  const id = Number(objetivo.dataset.id);

  if (objetivo.dataset.accion === "ver") return abrirVistaPrevia(id);
  if (objetivo.dataset.accion === "pdf") return descargarPdf(id, objetivo);

  if (objetivo.dataset.accion === "borrar") {
    const p = plantillas.find((x) => x.id === id);
    if (!confirm(`¿Eliminar la plantilla «${p.nombre}»? No se puede deshacer.`)) return;
    const r = await Plantillas.borrar(id);
    if (!r.ok) return alert(`No se pudo eliminar.\n${r.error}`);
    plantillas = plantillas.filter((x) => x.id !== id);
    pintar();
  }
});

buscador.addEventListener("input", pintar);
botonMas.addEventListener("click", () => cargar(siguiente));
document.getElementById("modalCloseBtn").addEventListener("click", cerrarModal);
document.getElementById("modalPdfBtn").addEventListener("click", (e) => {
  if (enModal) descargarPdf(enModal.id, e.currentTarget);
});
overlay.addEventListener("click", (e) => {
  if (e.target === overlay) cerrarModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") cerrarModal();
});

if (exigirSesion()) cargar();
