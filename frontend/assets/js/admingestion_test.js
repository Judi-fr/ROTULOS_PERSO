// Administración de usuarios: conecta la tabla de gestionuser.html con
// /api/v1/users/ (UserAdminViewSet). Toda la pantalla exige rol administrador.
//
// El token, la renovación del access vencido y el rebote al login salen de
// assets/js/auth-api.js, que la página carga antes que este archivo.

// Las clases de color del badge están escritas para los nombres de rol del
// maquetado original (admin/editor/author/subscriber). Se mapean los roles
// reales del proyecto —los Groups que siembra la migración 0001_seed_roles—
// a esas clases para no tener que tocar el CSS.
const roleClass = {
  administradores: "admin",
  diseñadores: "editor",
  operadores: "author",
};

const statusLabel = {
  active: "Activo",
  inactive: "Inactivo",
};

const tbody = document.getElementById("usersBody");

function aviso(texto) {
  tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px;">${texto}</td></tr>`;
}

// El backend habla de is_staff / is_active / groups / date_joined, y la tabla
// fue maquetada con name / role / status / created. La traducción vive acá
// para no tener que tocar el render, que es puro HTML.
function aFila(u) {
  const nombre = [u.first_name, u.last_name].filter(Boolean).join(" ");
  return {
    id: u.id,
    // Sin nombre cargado se muestra el email: una fila en blanco no sirve
    // para identificar a nadie.
    name: nombre || u.email,
    email: u.email,
    // is_staff manda sobre los grupos: un administrador puede además estar en
    // otro grupo, y lo que importa mostrar es el permiso más alto.
    role: u.is_staff ? "administradores" : u.groups[0] || "sin rol",
    status: u.is_active ? "active" : "inactive",
    created: new Date(u.date_joined).toLocaleDateString("es-AR"),
    seed: u.email,
  };
}

async function cargarUsuarios() {
  aviso("Cargando usuarios...");

  const res = await apiFetch("/users/");
  // null = la sesión murió (auth-api ya redirigió) o no hubo red.
  if (!res) return aviso("No se pudo conectar con el servidor.");

  if (res.status === 403) {
    return aviso(
      "Tu cuenta no tiene permisos de administrador para ver esta lista."
    );
  }
  if (!res.ok) {
    return aviso(`No se pudieron cargar los usuarios (${res.status}).`);
  }

  // DRF pagina de a 20 y devuelve {count, next, previous, results}. Se
  // contempla también la lista pelada por si algún día se saca la paginación.
  const data = await res.json();
  const usuarios = Array.isArray(data) ? data : data.results || [];

  if (usuarios.length === 0) return aviso("No hay usuarios para mostrar.");
  renderUsers(usuarios.map(aFila));
}

function renderUsers(users) {
  tbody.innerHTML = "";

  users.forEach((u, i) => {
    const activo = u.status === "active";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox"></td>
      <td>
        <div class="user-cell">
          <img class="user-avatar" src="https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(u.seed || u.name)}" alt="">
          <span class="user-name">${esc(u.name)}</span>
        </div>
      </td>
      <td class="cell-muted">${esc(u.email)}</td>
      <td><span class="badge ${roleClass[u.role] || ""}">${esc(u.role)}</span></td>
      <td><span class="status ${u.status}"><span class="dot"></span>${statusLabel[u.status] || u.status}</span></td>
      <td class="cell-muted">${u.created}</td>
      <td class="actions-col">
        <div class="row-actions">
          <button class="more-btn" data-idx="${i}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
          </button>
          <div class="dropdown" id="dd-${i}" style="display:none;">
            <a href="#" data-accion="estado" data-id="${u.id}" data-activo="${activo}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>${activo ? "Desactivar" : "Activar"}</a>
            <div class="sep"></div>
            <a href="#" class="danger" data-accion="borrar" data-id="${u.id}" data-nombre="${esc(u.name)}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>Eliminar</a>
          </div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });

  attachDropdownListeners();
}

function attachDropdownListeners() {
  document.querySelectorAll(".more-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = btn.dataset.idx;
      document.querySelectorAll(".dropdown").forEach((dd) => {
        if (dd.id !== `dd-${idx}`) dd.style.display = "none";
      });
      const dd = document.getElementById(`dd-${idx}`);
      dd.style.display = dd.style.display === "block" ? "none" : "block";
    });
  });
}

// El backend devuelve el motivo en `detail`, y tiene dos guardas propias: un
// admin no puede eliminarse a sí mismo ni quitarse su is_staff. Esos 403
// llegan con un texto explicativo que conviene mostrar tal cual.
async function motivoApi(res) {
  const cuerpo = await res.json().catch(() => ({}));
  return cuerpo.detail || `Error ${res.status}.`;
}

// Un solo listener para toda la tabla: las filas se redibujan en cada carga y
// enganchar listeners fila por fila obligaría a re-engancharlos cada vez.
tbody.addEventListener("click", async (e) => {
  const accion = e.target.closest("[data-accion]");
  if (!accion) return;
  e.preventDefault();
  e.stopPropagation();

  const id = accion.dataset.id;

  if (accion.dataset.accion === "borrar") {
    if (!confirm(`¿Eliminar a ${accion.dataset.nombre}? No se puede deshacer.`)) {
      return;
    }
    const res = await apiFetch(`/users/${id}/`, { method: "DELETE" });
    if (!res) return;
    if (!res.ok) return alert(await motivoApi(res));
    cargarUsuarios();
    return;
  }

  if (accion.dataset.accion === "estado") {
    // PATCH y no PUT: así solo viaja is_active y el resto del usuario queda
    // como está, sin riesgo de pisar campos que nadie tocó.
    const activar = accion.dataset.activo !== "true";
    const res = await apiFetch(`/users/${id}/`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: activar }),
    });
    if (!res) return;
    if (!res.ok) return alert(await motivoApi(res));
    cargarUsuarios();
  }
});

document.addEventListener("click", () => {
  document
    .querySelectorAll(".dropdown")
    .forEach((dd) => (dd.style.display = "none"));
});

// --- Panel de alta --------------------------------------------------------

const panel = document.getElementById("createPanel");
document
  .getElementById("openCreatePanel")
  .addEventListener("click", () => (panel.style.display = "block"));
document
  .getElementById("closeCreatePanel")
  .addEventListener("click", () => (panel.style.display = "none"));
document
  .getElementById("cancelCreate")
  .addEventListener("click", () => (panel.style.display = "none"));

document.getElementById("crearUsuario").addEventListener("click", async () => {
  const nombreCompleto = document.getElementById("nuevoNombre").value.trim();
  // El backend guarda nombre y apellido por separado: se corta en el primer
  // espacio y el resto va como apellido ("Ana María Pérez" -> "Ana" / "María
  // Pérez"), que es lo menos malo con un solo campo en el maquetado.
  const [first, ...resto] = nombreCompleto.split(" ");
  const rol = document.getElementById("nuevoRol").value;

  const cuerpo = {
    email: document.getElementById("nuevoEmail").value.trim(),
    password: document.getElementById("nuevaClave").value,
    first_name: first || "",
    last_name: resto.join(" "),
    is_active: document.getElementById("nuevoEstado").value === "activo",
    // "administradores" no es un Group que se asigne: es el flag is_staff.
    is_staff: rol === "administradores",
    groups: rol && rol !== "administradores" ? [rol] : [],
  };

  const res = await apiFetch("/users/", {
    method: "POST",
    body: JSON.stringify(cuerpo),
  });
  if (!res) return;
  if (!res.ok) {
    // En el alta los errores vienen por campo ({"password": [...]}) y no en
    // `detail`, así que el texto se arma con lo que haya llegado.
    const err = await res.json().catch(() => ({}));
    const texto =
      err.detail ||
      Object.entries(err)
        .map(([campo, msgs]) => `${campo}: ${[].concat(msgs).join(" ")}`)
        .join("\n");
    return alert(texto || `Error ${res.status}.`);
  }

  panel.style.display = "none";
  document.getElementById("nuevoNombre").value = "";
  document.getElementById("nuevoEmail").value = "";
  document.getElementById("nuevaClave").value = "";
  cargarUsuarios();
});

// --- Arranque -------------------------------------------------------------

if (exigirSesion()) {
  const salir = document.getElementById("cerrarSesion");
  if (salir) salir.addEventListener("click", cerrarSesion);
  cargarUsuarios();
}
