// Catálogo de variables de rótulos (variables.html).
//
// Endpoints:
//   GET    /labels/variables/?incluir_inactivas=1   listado completo
//   POST   /labels/variables/                        crear       (admin)
//   PATCH  /labels/variables/<id>/                   editar      (admin)
//   DELETE /labels/variables/<id>/                   eliminar    (admin)
//   GET    /auth/me/                                 saber si el usuario es admin
//
// Leer el catálogo lo puede cualquier usuario; modificarlo, solo un
// administrador. La pantalla se adapta en vez de mostrar botones que después
// el backend va a rechazar con 403.

const $ = (id) => document.getElementById(id);

let variables = [];
let esAdmin = false;
let editando = null; // la variable que se está editando, o null si es alta

function avisar(texto, tipo = "info") {
  const caja = $("mensaje");
  caja.textContent = texto;
  caja.className = `aviso ${tipo}`;
  caja.hidden = !texto;
}

function pintar() {
  const verInactivas = $("verInactivas").checked;
  const visibles = variables.filter((v) => verInactivas || v.activa);
  if (!visibles.length) {
    $("lista").innerHTML = '<div class="empty">No hay variables para mostrar.</div>';
    return;
  }

  $("lista").innerHTML = `<table class="data">
    <thead><tr>
      <th>Código</th><th>Etiqueta</th><th>Tipo</th><th>Orden</th><th>Estado</th><th></th>
    </tr></thead>
    <tbody>${visibles.map(fila).join("")}</tbody>
  </table>`;
}

function fila(v) {
  const badges = [
    v.activa ? '<span class="badge ok">activa</span>' : '<span class="badge warn">inactiva</span>',
    v.es_sistema ? '<span class="badge accent">sistema</span>' : "",
  ].join(" ");
  const acciones = esAdmin
    ? `<div class="acciones">
        <button class="btn small" data-accion="editar" data-id="${v.id}">Editar</button>
        <button class="btn small" data-accion="estado" data-id="${v.id}">${v.activa ? "Desactivar" : "Activar"}</button>
        ${v.es_sistema ? "" : `<button class="btn small danger" data-accion="borrar" data-id="${v.id}">Eliminar</button>`}
      </div>`
    : "";
  return `<tr>
    <td><code class="codigo">${esc(v.codigo)}</code></td>
    <td>${esc(v.etiqueta)}${v.descripcion ? `<div class="subtitle" style="font-size:12px">${esc(v.descripcion.slice(0, 120))}${v.descripcion.length > 120 ? "…" : ""}</div>` : ""}</td>
    <td>${esc(TIPOS_DATO[v.tipo_dato] || v.tipo_dato)}</td>
    <td>${v.orden}</td>
    <td>${badges}</td>
    <td>${acciones}</td>
  </tr>`;
}

async function cargar() {
  const r = await Variables.listar(true);
  if (!r.ok) {
    $("lista").innerHTML = "";
    avisar(`No se pudo cargar el catálogo.\n${r.error}`, "error");
    return;
  }
  variables = r.data;
  pintar();
}

// --- Formulario -----------------------------------------------------------

$("tipo_dato").innerHTML = Object.entries(TIPOS_DATO)
  .map(([valor, nombre]) => `<option value="${valor}">${nombre}</option>`)
  .join("");

function abrirFormulario(variable = null) {
  editando = variable;
  $("formTitulo").textContent = variable ? `Editar «${variable.etiqueta}»` : "Nueva variable";
  $("codigo").value = variable ? variable.codigo : "";
  $("etiqueta").value = variable ? variable.etiqueta : "";
  $("tipo_dato").value = variable ? variable.tipo_dato : "texto";
  $("orden").value = variable ? variable.orden : 100;
  $("descripcion").value = variable ? variable.descripcion : "";
  $("activa").checked = variable ? variable.activa : true;

  // El código de una variable del sistema es parte del contrato con el motor
  // de impresión y con el importador: el backend no deja cambiarlo, así que
  // ni se ofrece. Tampoco el tipo, que cambiaría cómo se dibuja en plantillas
  // que ya la usan.
  const sistema = Boolean(variable && variable.es_sistema);
  $("codigo").disabled = sistema;
  $("tipo_dato").disabled = sistema;
  $("formSub").hidden = sistema;

  $("formulario").hidden = false;
  $("formulario").scrollIntoView({ behavior: "smooth", block: "start" });
  $(sistema ? "etiqueta" : "codigo").focus();
}

function cerrarFormulario() {
  $("formulario").hidden = true;
  editando = null;
}

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const cuerpo = {
    etiqueta: $("etiqueta").value.trim(),
    descripcion: $("descripcion").value.trim(),
    orden: Number($("orden").value) || 0,
    activa: $("activa").checked,
  };
  if (!editando || !editando.es_sistema) {
    cuerpo.codigo = $("codigo").value.trim();
    cuerpo.tipo_dato = $("tipo_dato").value;
  }

  const boton = $("guardarBtn");
  boton.disabled = true;
  const r = editando ? await Variables.editar(editando.id, cuerpo) : await Variables.crear(cuerpo);
  boton.disabled = false;

  if (!r.ok) return avisar(`No se pudo guardar.\n${r.error}`, "error");
  avisar(editando ? "Variable actualizada." : `Variable «${r.data.codigo}» creada.`, "ok");
  cerrarFormulario();
  cargar();
});

$("cancelarBtn").addEventListener("click", cerrarFormulario);
$("nuevaBtn").addEventListener("click", () => abrirFormulario());
$("verInactivas").addEventListener("change", pintar);

// --- Acciones de la tabla -------------------------------------------------

$("lista").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-accion]");
  if (!b) return;
  const v = variables.find((x) => x.id === Number(b.dataset.id));
  if (!v) return;

  if (b.dataset.accion === "editar") return abrirFormulario(v);

  if (b.dataset.accion === "estado") {
    const r = await Variables.editar(v.id, { activa: !v.activa });
    if (!r.ok) return avisar(`No se pudo cambiar el estado.\n${r.error}`, "error");
    avisar(
      v.activa
        ? `«${v.etiqueta}» ya no se ofrece en el editor. Las plantillas que la usan siguen funcionando.`
        : `«${v.etiqueta}» vuelve a ofrecerse en el editor.`,
      "ok"
    );
    return cargar();
  }

  if (b.dataset.accion === "borrar") {
    if (!confirm(`¿Eliminar la variable «${v.etiqueta}»?`)) return;
    const r = await Variables.borrar(v.id);
    // 409: está en uso en alguna plantilla. El backend explica que conviene
    // desactivarla en vez de borrarla; se muestra ese texto tal cual.
    if (!r.ok) return avisar(r.error, r.status === 409 ? "warn" : "error");
    avisar(`Variable «${v.codigo}» eliminada.`, "ok");
    cargar();
  }
});

// --- Arranque -------------------------------------------------------------

async function iniciar() {
  const yo = await perfil();
  esAdmin = Boolean(yo && yo.is_staff);
  $("nuevaBtn").hidden = !esAdmin;
  if (!esAdmin) {
    avisar("Podés consultar el catálogo; modificarlo queda reservado a administradores.", "info");
  }
  await cargar();
}

if (exigirSesion()) iniciar();
