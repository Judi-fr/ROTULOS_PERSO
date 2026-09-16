// Editor de plantillas de rótulos (pedidos/diseñorotulos.html).
//
// Trabaja con el modelo del backend tal cual: una plantilla con ancho/alto en
// milímetros y una lista de elementos (variable del catálogo, texto fijo,
// línea o recuadro), cada uno con su caja en mm y su `estilo`.
//
// Endpoints:
//   GET  /labels/variables/                 catálogo para el panel "Agregar"
//   GET  /labels/fuentes/                   familias tipográficas
//   GET  /labels/plantillas/<id>/           abrir una plantilla (?id=)
//   POST /labels/plantillas/                guardar una nueva
//   PUT  /labels/plantillas/<id>/           guardar cambios
//   POST /labels/plantillas/<id>/renderizar/  vista previa PNG y PDF
//   GET  /processing/importaciones/<id>/    abrir una lectura de foto (?importacion=)
//   GET  /documents/documentos/             imágenes para variables de tipo imagen
//
// Se cargan: auth-api.js (sesión) y rotulos-api.js (cliente y utilidades).

const PT_A_MM = 25.4 / 72;
const PASO_ARRASTRE_MM = 0.5;
const MINIMO_MM = 1;
const DPI_VISTA_PREVIA = 150;

// Espejo de ESTILO_POR_DEFECTO en apps/labels/estilos.py. Solo se guardan en
// `estilo` las claves que el usuario cambió; el resto las completa el backend.
const ESTILO_POR_DEFECTO = {
  tamano_pt: 10,
  alineacion: "izquierda",
  negrita: false,
  cursiva: false,
  fuente: null,
  color: "#000000",
  grosor_mm: 0.3,
};
const ALINEACION_CSS = {
  izquierda: "left",
  centro: "center",
  derecha: "right",
  justificado: "justify",
};
const FAMILIA_CSS = {
  helvetica: "Helvetica, Arial, sans-serif",
  times: "'Times New Roman', Times, serif",
  courier: "'Courier New', Courier, monospace",
};
const NOMBRE_TIPO = {
  variable: "Variable",
  texto_estatico: "Texto fijo",
  linea: "Línea",
  recuadro: "Recuadro",
};

// --- Estado ---------------------------------------------------------------

const estado = {
  id: null,
  nombre: "",
  descripcion: "",
  ancho_mm: 100,
  alto_mm: 150,
  dpi: 300,
  metadatos: {},
  elementos: [], // cada uno lleva `_clave`, que es solo del front
};

let catalogo = []; // variables activas
let fuentes = [];
let seleccionada = null; // _clave del elemento seleccionado
let sucio = false;
let escala = 4; // píxeles de pantalla por milímetro
let proximaClave = 1;
let datosEnvio = {}; // codigo -> valor, para imprimir con datos reales
let documentosImagen = null; // se piden la primera vez que hacen falta
let ultimoPng = null;

const $ = (id) => document.getElementById(id);
const lienzo = $("label");
const redondear = (n) => Math.round(Number(n) * 100) / 100;
const acotar = (n, min, max) => Math.min(Math.max(n, min), max);

function tipoDato(el) {
  if (el.tipo !== "variable") return null;
  const v = catalogo.find((x) => x.codigo === el.variable);
  // Una variable desactivada ya no viene en el catálogo, pero la plantilla
  // que la usa sigue siendo válida: el backend manda su tipo en el elemento.
  return v ? v.tipo_dato : el.variable_tipo_dato || "texto";
}

function nombreDe(el) {
  if (el.tipo === "variable") {
    const v = catalogo.find((x) => x.codigo === el.variable);
    return v ? v.etiqueta : el.variable_display || el.variable;
  }
  if (el.tipo === "texto_estatico") return el.contenido || "(texto vacío)";
  return NOMBRE_TIPO[el.tipo];
}

const lleva = (el) => {
  const td = tipoDato(el);
  return {
    texto: el.tipo === "texto_estatico" || td === "texto",
    trazo: el.tipo === "linea" || el.tipo === "recuadro",
  };
};

const estiloDe = (el) => ({ ...ESTILO_POR_DEFECTO, ...(el.estilo || {}) });

function marcarSucio() {
  sucio = true;
  $("saveBtn").textContent = "Guardar •";
}

function marcarLimpio() {
  sucio = false;
  $("saveBtn").textContent = "Guardar";
}

function avisar(texto, tipo = "info") {
  const caja = $("estado");
  caja.textContent = texto;
  caja.className = `aviso ${tipo}`;
  caja.hidden = !texto;
}

// --- Lienzo ---------------------------------------------------------------

function calcularEscala() {
  const disponible = $("canvasWrap").clientWidth - 48;
  // Se achica para que un rótulo grande entre en pantalla, y se limita hacia
  // arriba para que uno chico no se vea gigante.
  escala = acotar(Math.min(disponible / estado.ancho_mm, 680 / estado.alto_mm), 1, 6);
}

function pintarLienzo() {
  lienzo.style.width = `${estado.ancho_mm * escala}px`;
  lienzo.style.height = `${estado.alto_mm * escala}px`;
  lienzo.innerHTML = estado.elementos.map(htmlElemento).join("");
}

function htmlElemento(el) {
  const e = estiloDe(el);
  const afuera =
    el.x_mm + el.ancho_mm > estado.ancho_mm + 0.01 || el.y_mm + el.alto_mm > estado.alto_mm + 0.01;
  const clases = ["elem", el._clave === seleccionada ? "sel" : "", afuera ? "afuera" : ""].join(" ");
  const caja = `left:${el.x_mm * escala}px;top:${el.y_mm * escala}px;width:${el.ancho_mm * escala}px;height:${el.alto_mm * escala}px`;
  const tirador = el._clave === seleccionada ? '<div class="tirador" data-tirador="1"></div>' : "";
  return `<div class="${clases}" data-clave="${el._clave}" style="${caja}" title="${esc(nombreDe(el))}">${contenidoElemento(el, e)}${tirador}</div>`;
}

function contenidoElemento(el, e) {
  const td = tipoDato(el);

  if (el.tipo === "linea") {
    const grosor = Math.max(1, e.grosor_mm * escala);
    const horizontal = el.alto_mm < el.ancho_mm;
    // El backend dibuja la línea por el centro de la caja (ver primitivas.Linea).
    const css = horizontal
      ? `left:0;right:0;top:50%;height:${grosor}px;transform:translateY(-50%)`
      : `top:0;bottom:0;left:50%;width:${grosor}px;transform:translateX(-50%)`;
    return `<div class="linea" style="${css};background:${e.color}"></div>`;
  }
  if (el.tipo === "recuadro") {
    const grosor = Math.max(1, e.grosor_mm * escala);
    return `<div class="linea" style="inset:0;border:${grosor}px solid ${e.color}"></div>`;
  }
  if (td === "qr" || td === "qr_envio") {
    return `<div class="ph"><div class="cuadro-qr"></div>${esc(nombreDe(el))}</div>`;
  }
  if (td === "codigo_barras") {
    return `<div class="ph"><div class="barras"></div>${esc(nombreDe(el))}</div>`;
  }
  if (td === "imagen") {
    return `<div class="ph imagen">🖼<br />${esc(nombreDe(el))}</div>`;
  }

  // Texto (variable de texto o texto fijo). El tamaño en pantalla sale de los
  // puntos reales convertidos a mm y a la escala del lienzo, para que la
  // proporción texto/caja se parezca a la impresa.
  const px = e.tamano_pt * PT_A_MM * escala;
  const familia = FAMILIA_CSS[e.fuente] || FAMILIA_CSS.helvetica;
  const css = [
    `font-size:${px}px`,
    `font-family:${familia}`,
    `font-weight:${e.negrita ? 700 : 400}`,
    `font-style:${e.cursiva ? "italic" : "normal"}`,
    `text-align:${ALINEACION_CSS[e.alineacion] || "left"}`,
    `color:${e.color}`,
  ].join(";");
  const texto = el.tipo === "variable" ? `{${nombreDe(el)}}` : el.contenido;
  return `<span class="texto" style="${css}">${esc(texto)}</span>`;
}

// --- Arrastrar y redimensionar --------------------------------------------

let gesto = null;

lienzo.addEventListener("pointerdown", (e) => {
  const div = e.target.closest(".elem");
  if (!div) {
    seleccionar(null);
    return;
  }
  const clave = Number(div.dataset.clave);
  const el = estado.elementos.find((x) => x._clave === clave);
  if (seleccionada !== clave) seleccionar(clave);

  const divActual = lienzo.querySelector(`[data-clave="${clave}"]`);
  gesto = {
    el,
    div: divActual,
    modo: e.target.dataset.tirador ? "redimensionar" : "mover",
    x0: e.clientX,
    y0: e.clientY,
    orig: { x: el.x_mm, y: el.y_mm, w: el.ancho_mm, h: el.alto_mm },
    movio: false,
  };
  try {
    // Con la captura, el arrastre sigue aunque el puntero salga del
    // elemento. Si el navegador la rechaza, se sigue igual sin ella.
    divActual.setPointerCapture(e.pointerId);
  } catch {
    /* sin captura */
  }
  e.preventDefault();
});

lienzo.addEventListener("pointermove", (e) => {
  if (!gesto) return;
  const dx = (e.clientX - gesto.x0) / escala;
  const dy = (e.clientY - gesto.y0) / escala;
  if (!gesto.movio && Math.hypot(dx * escala, dy * escala) < 3) return;
  gesto.movio = true;
  gesto.div.classList.add("arrastrando");

  const paso = (n) => Math.round(n / PASO_ARRASTRE_MM) * PASO_ARRASTRE_MM;
  const { el, orig } = gesto;
  if (gesto.modo === "mover") {
    el.x_mm = redondear(acotar(paso(orig.x + dx), 0, Math.max(0, estado.ancho_mm - el.ancho_mm)));
    el.y_mm = redondear(acotar(paso(orig.y + dy), 0, Math.max(0, estado.alto_mm - el.alto_mm)));
  } else {
    el.ancho_mm = redondear(acotar(paso(orig.w + dx), MINIMO_MM, estado.ancho_mm - el.x_mm));
    el.alto_mm = redondear(acotar(paso(orig.h + dy), MINIMO_MM, estado.alto_mm - el.y_mm));
  }
  // Durante el gesto se toca solo la caja del elemento: repintar todo el
  // lienzo en cada movimiento del mouse haría que el arrastre tironee.
  gesto.div.style.left = `${el.x_mm * escala}px`;
  gesto.div.style.top = `${el.y_mm * escala}px`;
  gesto.div.style.width = `${el.ancho_mm * escala}px`;
  gesto.div.style.height = `${el.alto_mm * escala}px`;
});

function terminarGesto() {
  if (!gesto) return;
  const movio = gesto.movio;
  gesto = null;
  if (movio) {
    marcarSucio();
    pintarLienzo();
    pintarPropiedades();
  }
}
lienzo.addEventListener("pointerup", terminarGesto);
lienzo.addEventListener("pointercancel", terminarGesto);

function seleccionar(clave) {
  seleccionada = clave;
  pintarLienzo();
  pintarPropiedades();
}

// --- Panel "Agregar" ------------------------------------------------------

function pintarAgregar() {
  const usadas = new Set(estado.elementos.filter((e) => e.tipo === "variable").map((e) => e.variable));
  const boton = (atributos, texto, marca = "") =>
    `<button class="btn" ${atributos}><span>${texto}</span>${marca}</button>`;

  const grupos = [
    ["Datos del envío", catalogo.filter((v) => v.tipo_dato === "texto")],
    ["Códigos", catalogo.filter((v) => ["qr", "qr_envio", "codigo_barras"].includes(v.tipo_dato))],
    ["Imágenes", catalogo.filter((v) => v.tipo_dato === "imagen")],
  ];

  let html = "";
  for (const [titulo, vars] of grupos) {
    if (!vars.length) continue;
    html += `<div class="grupo">${titulo}</div>`;
    html += vars
      .map((v) =>
        boton(
          `data-variable="${esc(v.codigo)}" title="${esc(v.descripcion || v.etiqueta)}"`,
          esc(v.etiqueta),
          usadas.has(v.codigo) ? '<span class="usada">✓</span>' : ""
        )
      )
      .join("");
  }
  html += `<div class="grupo">Diseño</div>`;
  html += boton('data-tipo="texto_estatico"', "Texto fijo");
  html += boton('data-tipo="linea"', "Línea");
  html += boton('data-tipo="recuadro"', "Recuadro");
  $("agregar").innerHTML = html;
}

// Medidas iniciales pensadas para que el elemento aparezca usable sin tener
// que redimensionarlo: un QR cuadrado y grande, un texto de una línea, etc.
function medidasIniciales(tipo, td) {
  if (tipo === "linea") return { w: estado.ancho_mm - 10, h: 2 };
  if (tipo === "recuadro") return { w: 40, h: 20 };
  if (tipo === "texto_estatico") return { w: 40, h: 7 };
  if (td === "qr" || td === "qr_envio") return { w: 28, h: 28 };
  if (td === "codigo_barras") return { w: 60, h: 15 };
  if (td === "imagen") return { w: 22, h: 22 };
  return { w: Math.min(70, estado.ancho_mm - 10), h: 7 };
}

function agregar(tipo, codigo = null) {
  const td = codigo ? (catalogo.find((v) => v.codigo === codigo) || {}).tipo_dato : null;
  const { w, h } = medidasIniciales(tipo, td);
  // Cada elemento nuevo aparece un poco corrido del anterior, para que no
  // queden todos apilados en el mismo punto.
  const desfase = (estado.elementos.length % 10) * 4;
  const ancho = Math.min(w, estado.ancho_mm);
  const alto = Math.min(h, estado.alto_mm);
  const el = {
    _clave: proximaClave++,
    tipo,
    variable: codigo,
    contenido: tipo === "texto_estatico" ? "Texto" : "",
    x_mm: redondear(acotar(5 + desfase, 0, estado.ancho_mm - ancho)),
    y_mm: redondear(acotar(5 + desfase, 0, estado.alto_mm - alto)),
    ancho_mm: redondear(ancho),
    alto_mm: redondear(alto),
    estilo: {},
  };
  estado.elementos.push(el);
  marcarSucio();
  seleccionar(el._clave);
  pintarAgregar();
  pintarDatos();
}

$("agregar").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.variable) agregar("variable", b.dataset.variable);
  else agregar(b.dataset.tipo);
});

// --- Panel "Propiedades" --------------------------------------------------

function pintarPropiedades() {
  const cont = $("propiedades");
  const el = estado.elementos.find((x) => x._clave === seleccionada);
  if (!el) {
    cont.innerHTML = '<p class="vacio">Seleccioná un elemento del rótulo para ajustarlo.</p>';
    return;
  }
  const e = estiloDe(el);
  const { texto, trazo } = lleva(el);
  const num = (prop, etiqueta, valor, paso = 0.5, min = 0) =>
    `<div><label>${etiqueta}</label><input class="input" type="number" step="${paso}" min="${min}" data-prop="${prop}" value="${valor}" /></div>`;

  let html = `<div class="tipo"><span class="badge accent">${NOMBRE_TIPO[el.tipo]}</span> ${el.tipo === "variable" ? esc(nombreDe(el)) : ""}</div>`;

  if (el.tipo === "texto_estatico") {
    html += `<div style="margin-bottom:8px"><label>Texto</label><textarea class="input" rows="2" data-prop="contenido">${esc(el.contenido)}</textarea></div>`;
  }

  html += `<div class="fila">${num("x_mm", "X (mm)", el.x_mm)}${num("y_mm", "Y (mm)", el.y_mm)}</div>`;
  html += `<div class="fila">${num("ancho_mm", "Ancho (mm)", el.ancho_mm, 0.5, MINIMO_MM)}${num("alto_mm", "Alto (mm)", el.alto_mm, 0.5, MINIMO_MM)}</div>`;

  if (texto) {
    const opcionesFuente = [`<option value="">Por defecto</option>`]
      .concat(
        fuentes.map(
          (f) =>
            `<option value="${esc(f.codigo)}" ${e.fuente === f.codigo ? "selected" : ""}>${esc(f.etiqueta)}${f.png_disponible ? "" : " (sin vista PNG)"}</option>`
        )
      )
      .join("");
    const opcionesAlineacion = Object.keys(ALINEACION_CSS)
      .map((a) => `<option value="${a}" ${e.alineacion === a ? "selected" : ""}>${a}</option>`)
      .join("");
    html += `<div class="fila">
        <div><label>Tamaño (pt)</label><input class="input" type="number" min="1" step="0.5" data-estilo="tamano_pt" value="${e.tamano_pt}" /></div>
        <div><label>Color</label><input class="input" type="color" data-estilo="color" value="${e.color}" style="padding:2px;height:31px" /></div>
      </div>
      <div class="fila">
        <div><label>Fuente</label><select class="input" data-estilo="fuente">${opcionesFuente}</select></div>
        <div><label>Alineación</label><select class="input" data-estilo="alineacion">${opcionesAlineacion}</select></div>
      </div>
      <div class="check">
        <label><input type="checkbox" data-estilo="negrita" ${e.negrita ? "checked" : ""} /> Negrita</label>
        <label><input type="checkbox" data-estilo="cursiva" ${e.cursiva ? "checked" : ""} /> Cursiva</label>
      </div>`;
  }

  if (trazo) {
    html += `<div class="fila">
        <div><label>Grosor (mm)</label><input class="input" type="number" min="0" step="0.1" data-estilo="grosor_mm" value="${e.grosor_mm}" /></div>
        <div><label>Color</label><input class="input" type="color" data-estilo="color" value="${e.color}" style="padding:2px;height:31px" /></div>
      </div>`;
  }

  if (tipoDato(el) === "qr_envio") {
    html += `<p class="vacio">El contenido de este QR lo arma el servidor con todos los datos del envío; no hace falta cargarle un valor.</p>`;
  }

  html += `<div class="acciones-elem">
      <button class="btn small" data-accion="frente">Al frente</button>
      <button class="btn small" data-accion="atras">Atrás</button>
      <button class="btn small" data-accion="duplicar">Duplicar</button>
      <button class="btn small danger" data-accion="eliminar">Eliminar</button>
    </div>`;

  cont.innerHTML = html;
}

$("propiedades").addEventListener("input", (e) => {
  const el = estado.elementos.find((x) => x._clave === seleccionada);
  if (!el) return;
  const campo = e.target;

  if (campo.dataset.prop === "contenido") {
    el.contenido = campo.value;
  } else if (campo.dataset.prop) {
    const valor = Number(campo.value);
    if (!Number.isFinite(valor)) return;
    const minimo = campo.dataset.prop.startsWith("x") || campo.dataset.prop.startsWith("y") ? 0 : MINIMO_MM;
    el[campo.dataset.prop] = redondear(Math.max(minimo, valor));
  } else if (campo.dataset.estilo) {
    const clave = campo.dataset.estilo;
    let valor;
    if (campo.type === "checkbox") valor = campo.checked;
    else if (campo.type === "number") valor = Number(campo.value);
    else valor = campo.value || null;

    if (campo.type === "number" && !(valor > 0 || (clave === "grosor_mm" && valor === 0))) return;

    el.estilo = { ...(el.estilo || {}) };
    // Volver al valor por defecto borra la clave: el estilo guardado queda
    // con solo lo que se aparta de lo normal, que es lo que pide el contrato.
    if (valor === ESTILO_POR_DEFECTO[clave] || valor === null) delete el.estilo[clave];
    else el.estilo[clave] = valor;
  }
  marcarSucio();
  pintarLienzo();
});

$("propiedades").addEventListener("change", (e) => {
  // Al terminar de editar un texto fijo cambia su nombre en el panel Agregar
  // y en los avisos; no hace falta hacerlo en cada tecla.
  if (e.target.dataset.prop === "contenido") pintarDatos();
});

$("propiedades").addEventListener("click", (e) => {
  const accion = e.target.closest("[data-accion]");
  if (!accion) return;
  const i = estado.elementos.findIndex((x) => x._clave === seleccionada);
  if (i < 0) return;
  const el = estado.elementos[i];

  // El orden en la lista es el orden de pintado: el último queda arriba.
  if (accion.dataset.accion === "frente") {
    estado.elementos.splice(i, 1);
    estado.elementos.push(el);
  } else if (accion.dataset.accion === "atras") {
    estado.elementos.splice(i, 1);
    estado.elementos.unshift(el);
  } else if (accion.dataset.accion === "duplicar") {
    const copia = {
      ...el,
      id: undefined,
      _clave: proximaClave++,
      estilo: { ...el.estilo },
      x_mm: redondear(Math.min(el.x_mm + 3, Math.max(0, estado.ancho_mm - el.ancho_mm))),
      y_mm: redondear(Math.min(el.y_mm + 3, Math.max(0, estado.alto_mm - el.alto_mm))),
    };
    estado.elementos.push(copia);
    seleccionada = copia._clave;
  } else if (accion.dataset.accion === "eliminar") {
    eliminarSeleccionado();
    return;
  }
  marcarSucio();
  pintarLienzo();
  pintarPropiedades();
});

function eliminarSeleccionado() {
  const i = estado.elementos.findIndex((x) => x._clave === seleccionada);
  if (i < 0) return;
  estado.elementos.splice(i, 1);
  seleccionada = null;
  marcarSucio();
  pintarLienzo();
  pintarPropiedades();
  pintarAgregar();
  pintarDatos();
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") return cerrarModal();
  const escribiendo = e.target.closest("input, textarea, select, [contenteditable]");
  if (escribiendo || seleccionada == null) return;
  const el = estado.elementos.find((x) => x._clave === seleccionada);
  if (!el) return;

  if (e.key === "Delete" || e.key === "Backspace") {
    e.preventDefault();
    eliminarSeleccionado();
    return;
  }
  const paso = e.shiftKey ? 5 : PASO_ARRASTRE_MM;
  const mover = { ArrowLeft: [-paso, 0], ArrowRight: [paso, 0], ArrowUp: [0, -paso], ArrowDown: [0, paso] }[e.key];
  if (!mover) return;
  e.preventDefault();
  el.x_mm = redondear(acotar(el.x_mm + mover[0], 0, Math.max(0, estado.ancho_mm - el.ancho_mm)));
  el.y_mm = redondear(acotar(el.y_mm + mover[1], 0, Math.max(0, estado.alto_mm - el.alto_mm)));
  marcarSucio();
  pintarLienzo();
  pintarPropiedades();
});

// --- Panel "Datos para imprimir" ------------------------------------------

async function pintarDatos() {
  const cont = $("datos");
  // Una variable colocada dos veces se carga una sola vez. El QR del envío no
  // tiene dato propio: se arma con todos los demás.
  const codigos = [
    ...new Set(
      estado.elementos
        .filter((e) => e.tipo === "variable" && tipoDato(e) !== "qr_envio")
        .map((e) => e.variable)
    ),
  ];
  if (!codigos.length) {
    cont.innerHTML = '<p class="vacio">La plantilla no tiene campos variables.</p>';
    return;
  }

  const necesitaImagenes = codigos.some((c) => tipoDato({ tipo: "variable", variable: c }) === "imagen");
  if (necesitaImagenes && documentosImagen === null) {
    documentosImagen = [];
    const r = await Documentos.listar();
    if (r.ok) documentosImagen = r.data.results.filter((d) => d.tipo_mime.startsWith("image/"));
  }

  cont.innerHTML = codigos
    .map((codigo) => {
      const el = { tipo: "variable", variable: codigo };
      const td = tipoDato(el);
      const valor = datosEnvio[codigo] ?? "";
      let control;
      if (td === "imagen") {
        // El backend espera el id de un documento propio que sea imagen.
        const opciones = (documentosImagen || [])
          .map((d) => `<option value="${d.id}" ${String(valor) === String(d.id) ? "selected" : ""}>${esc(d.nombre_original)}</option>`)
          .join("");
        control = `<select class="input" data-dato="${esc(codigo)}"><option value="">— sin imagen —</option>${opciones}</select>`;
        if (!(documentosImagen || []).length) {
          control += `<p class="vacio">Subí una imagen en <a href="/importar.html">Importar rótulo</a> para usarla acá.</p>`;
        }
      } else {
        control = `<input class="input" data-dato="${esc(codigo)}" value="${esc(valor)}" placeholder="${td === "texto" ? "" : "contenido del código"}" />`;
      }
      return `<div class="campo"><label>${esc(nombreDe(el))}</label>${control}</div>`;
    })
    .join("");
}

$("datos").addEventListener("input", (e) => {
  if (!e.target.dataset.dato) return;
  datosEnvio[e.target.dataset.dato] = e.target.value;
});

// Solo se mandan los datos cargados. Si no hay ninguno, no se manda `datos`
// y el backend genera la vista previa con el nombre de cada campo.
function datosParaRender() {
  const cargados = Object.fromEntries(
    Object.entries(datosEnvio).filter(([, v]) => String(v).trim() !== "")
  );
  return Object.keys(cargados).length ? cargados : null;
}

// --- Guardar --------------------------------------------------------------

function cuerpoParaGuardar() {
  return {
    nombre: estado.nombre.trim() || "Plantilla sin nombre",
    descripcion: estado.descripcion,
    ancho_mm: redondear(estado.ancho_mm),
    alto_mm: redondear(estado.alto_mm),
    dpi: estado.dpi,
    orientacion: estado.alto_mm >= estado.ancho_mm ? "vertical" : "horizontal",
    metadatos: estado.metadatos,
    elementos: estado.elementos.map((el, i) => ({
      tipo: el.tipo,
      variable: el.tipo === "variable" ? el.variable : null,
      contenido: el.tipo === "texto_estatico" ? el.contenido : "",
      x_mm: redondear(el.x_mm),
      y_mm: redondear(el.y_mm),
      ancho_mm: redondear(el.ancho_mm),
      alto_mm: redondear(el.alto_mm),
      estilo: el.estilo || {},
      orden: i,
    })),
  };
}

async function guardar() {
  const vacios = estado.elementos.filter((e) => e.tipo === "texto_estatico" && !e.contenido.trim());
  if (vacios.length) {
    // El backend lo rechazaría igual; se frena antes para señalar cuál es.
    seleccionar(vacios[0]._clave);
    avisar("Hay un texto fijo vacío. Escribile algo o eliminalo antes de guardar.", "error");
    return false;
  }

  const boton = $("saveBtn");
  boton.disabled = true;
  boton.textContent = "Guardando...";
  const cuerpo = cuerpoParaGuardar();
  const r = estado.id ? await Plantillas.guardar(estado.id, cuerpo) : await Plantillas.crear(cuerpo);
  boton.disabled = false;

  if (!r.ok) {
    marcarSucio();
    avisar(`No se pudo guardar.\n${r.error}`, "error");
    return false;
  }

  const eraNueva = !estado.id;
  // Se recarga desde la respuesta: el PUT recrea los elementos con ids
  // nuevos, y los avisos de truncado de un texto fijo se informan por ese id.
  const indiceSeleccion = estado.elementos.findIndex((x) => x._clave === seleccionada);
  cargarPlantilla(r.data);
  seleccionada = indiceSeleccion >= 0 ? estado.elementos[indiceSeleccion]?._clave ?? null : null;
  pintarTodo();
  marcarLimpio();
  if (eraNueva) history.replaceState(null, "", `?id=${estado.id}`);
  avisar(`Plantilla guardada (#${estado.id}).`, "ok");
  return true;
}

// --- Vista previa y PDF ---------------------------------------------------

// Traduce los códigos de las cabeceras X-Rotulo-* a nombres legibles.
function nombreDeCodigo(codigo) {
  const suelto = codigo.match(/^texto_estatico#(\d+)$/);
  if (suelto) {
    const el = estado.elementos.find((e) => String(e.id) === suelto[1]);
    return el ? `texto fijo «${el.contenido}»` : "un texto fijo";
  }
  const v = catalogo.find((x) => x.codigo === codigo);
  if (v) return v.etiqueta;
  const el = estado.elementos.find((e) => e.variable === codigo);
  return el ? nombreDe(el) : codigo;
}

function informe(r, conDatos) {
  const renglones = [];
  if (conDatos && r.faltantes.length) {
    renglones.push(`Sin dato: ${r.faltantes.map(nombreDeCodigo).join(", ")}.`);
  }
  if (r.truncados.length) {
    renglones.push(`Se cortaron por no entrar en su caja: ${r.truncados.map(nombreDeCodigo).join(", ")}.`);
  }
  for (const aviso of r.avisos) {
    const qr = aviso.match(/^qr_denso:(.+)$/);
    renglones.push(
      qr
        ? `El QR «${nombreDeCodigo(qr[1])}» quedó demasiado denso para su tamaño: agrandá la caja o un lector no lo va a poder leer.`
        : aviso
    );
  }
  return renglones;
}

async function asegurarGuardada() {
  // El render trabaja sobre la versión guardada en el servidor, así que los
  // cambios sin guardar se guardan antes; si no, la vista previa mostraría el
  // diseño anterior.
  if (estado.id && !sucio) return true;
  avisar("Guardando antes de generar la vista previa...", "info");
  return guardar();
}

async function vistaPrevia() {
  if (!(await asegurarGuardada())) return;
  const datos = datosParaRender();
  const img = $("previewImg");
  img.removeAttribute("src");
  img.alt = "Generando vista previa...";
  $("informe").hidden = true;
  $("modalOverlay").classList.add("open");

  const opciones = { formato: "png", dpi: DPI_VISTA_PREVIA };
  if (datos) opciones.datos = datos;
  const r = await renderizarPlantilla(estado.id, opciones);
  if (!r.ok) {
    img.alt = `No se pudo generar la vista previa: ${r.error}`;
    return;
  }
  ultimoPng = r.blob;
  img.src = URL.createObjectURL(r.blob);
  mostrarInforme(informe(r, Boolean(datos)));
}

function mostrarInforme(renglones) {
  const lista = $("informe");
  lista.innerHTML = renglones.map((t) => `<li>${esc(t)}</li>`).join("");
  lista.hidden = !renglones.length;
}

async function descargarPdf() {
  if (!(await asegurarGuardada())) return;
  const boton = $("pdfBtn");
  boton.disabled = true;
  boton.textContent = "Generando...";
  const datos = datosParaRender();
  const opciones = { formato: "pdf" };
  if (datos) opciones.datos = datos;
  const r = await renderizarPlantilla(estado.id, opciones);
  boton.disabled = false;
  boton.textContent = "Descargar PDF";
  if (!r.ok) return avisar(`No se pudo generar el PDF.\n${r.error}`, "error");

  descargarBlob(r.blob, `rotulo-${estado.id}.pdf`);
  const renglones = informe(r, Boolean(datos));
  avisar(
    renglones.length ? `PDF descargado, con avisos:\n${renglones.join("\n")}` : "PDF descargado.",
    renglones.length ? "warn" : "ok"
  );
}

function cerrarModal() {
  $("modalOverlay").classList.remove("open");
}

// --- Cargar ---------------------------------------------------------------

function cargarPlantilla(p) {
  estado.id = p.id ?? null;
  estado.nombre = p.nombre || "";
  estado.descripcion = p.descripcion || "";
  // Los DecimalField llegan como texto ("100.00"): se pasan a número acá
  // para no arrastrar strings a las cuentas del lienzo.
  estado.ancho_mm = Number(p.ancho_mm);
  estado.alto_mm = Number(p.alto_mm);
  estado.dpi = p.dpi || 300;
  estado.metadatos = p.metadatos || {};
  estado.elementos = (p.elementos || []).map((e) => ({
    ...e,
    variable: e.variable ?? null,
    contenido: e.contenido || "",
    x_mm: Number(e.x_mm),
    y_mm: Number(e.y_mm),
    ancho_mm: Number(e.ancho_mm),
    alto_mm: Number(e.alto_mm),
    estilo: e.estilo || {},
    _clave: proximaClave++,
  }));
}

// Diseño equivalente al del editor anterior (10 × 15 cm, logo y QR arriba,
// datos del envío abajo), armado con las variables del sistema.
function disenoBase() {
  const existe = (codigo) => catalogo.some((v) => v.codigo === codigo);
  const piezas = [
    ["logo_empresa", 6, 7, 20, 20],
    ["qr", 68, 7, 26, 26],
    ["remitente", 6, 33, 88, 8],
    ["destinatario", 6, 57, 88, 8, { tamano_pt: 12, negrita: true }],
    ["domicilio", 6, 69, 88, 8],
    ["codigo_postal", 6, 80, 40, 8],
    ["localidad_provincia", 6, 91, 88, 8],
    ["numero_pedido", 6, 132, 88, 8, { negrita: true }],
  ];
  estado.ancho_mm = 100;
  estado.alto_mm = 150;
  estado.elementos = piezas
    .filter(([codigo]) => existe(codigo))
    .map(([codigo, x, y, w, h, estilo]) => ({
      _clave: proximaClave++,
      tipo: "variable",
      variable: codigo,
      contenido: "",
      x_mm: x,
      y_mm: y,
      ancho_mm: w,
      alto_mm: h,
      estilo: estilo || {},
    }));
  estado.elementos.push({
    _clave: proximaClave++,
    tipo: "linea",
    variable: null,
    contenido: "",
    x_mm: 6,
    y_mm: 48,
    ancho_mm: 88,
    alto_mm: 2,
    estilo: {},
  });
}

function mostrarRevision(revision) {
  const caja = $("revision");
  if (!revision) {
    caja.hidden = true;
    return;
  }
  const renglones = ["Plantilla propuesta a partir de una foto: revisá posiciones y campos antes de guardarla."];
  if (revision.confianza != null) {
    renglones.push(`Confianza de la lectura: ${Math.round(revision.confianza * 100)} %.`);
  }
  if (revision.notas) renglones.push(`Notas: ${revision.notas}`);
  if (revision.descartados?.length) {
    renglones.push(
      `${revision.descartados.length} elemento(s) no se pudieron convertir: ${revision.descartados.map((d) => d.motivo).join("; ")}.`
    );
  }
  if (revision.valores_detectados?.length) {
    renglones.push("Los valores leídos en la foto se cargaron en «Datos para imprimir».");
  }
  caja.textContent = renglones.join("\n");
  caja.hidden = false;
}

function pintarTodo() {
  $("nombreInput").value = estado.nombre;
  $("anchoMm").value = estado.ancho_mm;
  $("altoMm").value = estado.alto_mm;
  $("titulo").textContent = estado.id ? `Editar: ${estado.nombre}` : "Nueva plantilla";
  calcularEscala();
  pintarLienzo();
  pintarPropiedades();
  pintarAgregar();
  pintarDatos();
}

// --- Barra de herramientas ------------------------------------------------

$("nombreInput").addEventListener("input", (e) => {
  estado.nombre = e.target.value;
  marcarSucio();
});

function cambiarTamano() {
  const ancho = Number($("anchoMm").value);
  const alto = Number($("altoMm").value);
  if (!(ancho >= 10 && alto >= 10)) return;
  estado.ancho_mm = redondear(ancho);
  estado.alto_mm = redondear(alto);
  marcarSucio();
  calcularEscala();
  pintarLienzo();

  const afuera = estado.elementos.filter(
    (el) => el.x_mm + el.ancho_mm > estado.ancho_mm || el.y_mm + el.alto_mm > estado.alto_mm
  );
  // No se mueven solos: acomodarlos sin avisar podría desarmar un diseño que
  // solo iba a achicarse un momento. Se marcan en rojo y se avisa.
  if (afuera.length) {
    avisar(`${afuera.length} elemento(s) quedaron fuera del rótulo (marcados en rojo). Movelos o achicalos antes de imprimir.`, "warn");
  } else {
    avisar("");
  }
}
$("anchoMm").addEventListener("change", cambiarTamano);
$("altoMm").addEventListener("change", cambiarTamano);

$("baseBtn").addEventListener("click", () => {
  if (estado.elementos.length && !confirm("El diseño base reemplaza los elementos actuales. ¿Continuar?")) return;
  disenoBase();
  seleccionada = null;
  marcarSucio();
  pintarTodo();
});
$("saveBtn").addEventListener("click", guardar);
$("previewBtn").addEventListener("click", vistaPrevia);
$("pdfBtn").addEventListener("click", descargarPdf);
$("modalCloseBtn").addEventListener("click", cerrarModal);
$("modalOverlay").addEventListener("click", (e) => {
  if (e.target.id === "modalOverlay") cerrarModal();
});
$("previewPngBtn").addEventListener("click", () => {
  if (ultimoPng) descargarBlob(ultimoPng, `rotulo-${estado.id}.png`);
});

window.addEventListener("resize", () => {
  calcularEscala();
  pintarLienzo();
});
window.addEventListener("beforeunload", (e) => {
  if (!sucio) return;
  e.preventDefault();
  e.returnValue = "";
});

// --- Arranque -------------------------------------------------------------

async function iniciar() {
  const [rv, rf] = await Promise.all([Variables.listar(), listarFuentes()]);
  if (!rv.ok) {
    avisar(`No se pudo cargar el catálogo de variables.\n${rv.error}`, "error");
    return;
  }
  catalogo = rv.data;
  fuentes = rf.ok ? rf.data : [];

  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  const importacion = params.get("importacion");

  if (id) {
    const r = await Plantillas.obtener(id);
    if (!r.ok) {
      avisar(`No se pudo abrir la plantilla #${id}.\n${r.error}`, "error");
      disenoBase();
    } else {
      cargarPlantilla(r.data);
    }
    marcarLimpio();
  } else if (importacion) {
    const r = await Importaciones.obtener(importacion);
    if (!r.ok) {
      avisar(`No se pudo abrir la lectura #${importacion}.\n${r.error}`, "error");
      disenoBase();
    } else if (r.data.estado !== "completada" || !r.data.propuesta) {
      avisar(`La lectura #${importacion} no terminó bien (${r.data.estado}). ${r.data.error || ""}`, "error");
      disenoBase();
    } else {
      // `_revision` es información para esta pantalla, no parte del cuerpo
      // que acepta la API de plantillas: se separa antes de cargar.
      const { _revision, ...propuesta } = r.data.propuesta;
      cargarPlantilla({ ...propuesta, id: null });
      for (const d of _revision?.valores_detectados || []) {
        if (!d.variable) continue;
        // Lo que el modelo "lee" de un logo es una descripción ("buspack"),
        // no el id de un documento, que es lo que espera una variable de
        // imagen al imprimir: ese valor se descarta en vez de mandarlo.
        const td = tipoDato({ tipo: "variable", variable: d.variable });
        if (td === "imagen" || td === "qr_envio") continue;
        datosEnvio[d.variable] = d.valor;
      }
      mostrarRevision(_revision);
      marcarSucio(); // es una propuesta: todavía no existe en el servidor
    }
  } else {
    disenoBase();
    marcarLimpio();
  }

  pintarTodo();
}

if (exigirSesion()) iniciar();
