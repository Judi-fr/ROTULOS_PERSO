// Cliente de las APIs de rótulos: plantillas y catálogo (apps.labels),
// archivos subidos (apps.documents) y lectura de fotos (apps.processing).
//
// Se apoya en apiFetch() de auth-api.js, que pone el token y renueva la
// sesión vencida: la página tiene que cargar auth-api.js antes que este.
//
// Todas las funciones devuelven { ok, status, data, error } en vez de tirar
// excepciones. Así cada pantalla decide qué mostrar sin llenarse de try/catch,
// y un fallo de red o una sesión muerta se tratan igual que un 4xx.

// --- Base -----------------------------------------------------------------

// Convierte una respuesta de error en un texto para mostrar.
//
// DRF devuelve los errores de dos formas: {"detail": "..."} para los rechazos
// de la vista, y {"campo": ["..."]} para las validaciones del serializer. En
// las plantillas además vienen anidados por elemento:
// {"elementos": [{}, {"x_mm": ["..."]}]}. Se aplanan todos a renglones legibles.
function aplanarErrores(data, prefijo = "") {
  if (data == null) return [];
  if (typeof data === "string") return [prefijo + data];
  if (Array.isArray(data)) {
    // Una lista de strings son mensajes del mismo campo; una lista de objetos
    // son errores por posición (cada elemento de la plantilla).
    if (data.every((d) => typeof d === "string")) {
      return [prefijo + data.join(" ")];
    }
    return data.flatMap((d, i) =>
      d && Object.keys(d).length
        ? aplanarErrores(d, `${prefijo}elemento ${i + 1} › `)
        : []
    );
  }
  if (typeof data === "object") {
    if (data.detail) return [prefijo + data.detail];
    return Object.entries(data).flatMap(([campo, valor]) =>
      aplanarErrores(valor, campo === "non_field_errors" ? prefijo : `${prefijo}${campo}: `)
    );
  }
  return [prefijo + String(data)];
}

async function pedir(ruta, opciones = {}) {
  const res = await apiFetch(ruta, opciones);
  if (!res) {
    // apiFetch devuelve null si la sesión murió (ya redirigió al login) o si
    // no hubo respuesta del servidor.
    return { ok: false, status: 0, data: null, error: "No se pudo conectar con el servidor." };
  }
  if (res.status === 204) return { ok: true, status: 204, data: null, error: null };

  const data = await res.json().catch(() => null);
  if (res.ok) return { ok: true, status: res.status, data, error: null };

  let error;
  if (res.status === 429) {
    error = "Demasiados pedidos seguidos. Esperá un rato y volvé a intentar.";
  } else if (res.status === 403) {
    error = (data && data.detail) || "No tenés permisos para esta acción.";
  } else {
    error = aplanarErrores(data).join("\n") || `Error ${res.status}.`;
  }
  return { ok: false, status: res.status, data, error };
}

const json = (metodo, cuerpo) => ({ method: metodo, body: JSON.stringify(cuerpo) });

// Las listas paginadas de DRF vienen como {count, next, previous, results}.
// `next` es una URL absoluta; para seguir paginando con apiFetch hace falta
// solo la ruta relativa a la API.
function rutaDeSiguiente(next) {
  if (!next) return null;
  const url = new URL(next);
  return url.pathname.replace(/^\/api\/v1/, "") + url.search;
}

// --- Plantillas -----------------------------------------------------------

const Plantillas = {
  listar: (ruta = "/labels/plantillas/") => pedir(ruta),
  obtener: (id) => pedir(`/labels/plantillas/${id}/`),
  crear: (cuerpo) => pedir("/labels/plantillas/", json("POST", cuerpo)),
  // PUT y no PATCH: los elementos se reemplazan completos en cada guardado,
  // que es lo que hace el serializer cuando la clave viene en el cuerpo.
  guardar: (id, cuerpo) => pedir(`/labels/plantillas/${id}/`, json("PUT", cuerpo)),
  borrar: (id) => pedir(`/labels/plantillas/${id}/`, { method: "DELETE" }),
};

// Genera el rótulo imprimible. No usa pedir() porque la respuesta es un
// archivo (PDF o PNG), no JSON.
//
// Devuelve { ok, blob, faltantes, truncados, avisos, error }. Las tres listas
// salen de las cabeceras X-Rotulo-*, que el backend expone por CORS
// justamente para esto: el rótulo se imprime igual, pero el usuario tiene que
// enterarse de que a un campo le faltó el dato o se cortó.
async function renderizarPlantilla(id, opciones = {}) {
  const res = await apiFetch(`/labels/plantillas/${id}/renderizar/`, json("POST", opciones));
  if (!res) {
    return { ok: false, error: "No se pudo conectar con el servidor." };
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    return { ok: false, error: aplanarErrores(data).join("\n") || `Error ${res.status}.` };
  }
  const lista = (nombre) =>
    (res.headers.get(nombre) || "").split(",").map((s) => s.trim()).filter(Boolean);
  return {
    ok: true,
    blob: await res.blob(),
    faltantes: lista("X-Rotulo-Faltantes"),
    truncados: lista("X-Rotulo-Truncados"),
    avisos: lista("X-Rotulo-Avisos"),
    error: null,
  };
}

// Descarga un blob como archivo. Es una app local (no un iframe con sandbox),
// así que el enlace con `download` funciona.
function descargarBlob(blob, nombre) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Se libera después: si se revoca en el acto, algunos navegadores cancelan
  // la descarga antes de empezarla.
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// --- Catálogo de variables y fuentes --------------------------------------

const TIPOS_DATO = {
  texto: "Texto",
  qr: "Código QR",
  qr_envio: "QR con el envío completo",
  codigo_barras: "Código de barras",
  imagen: "Imagen",
};

const Variables = {
  // Sin paginación: el backend devuelve la lista entera a propósito.
  listar: (incluirInactivas = false) =>
    pedir(`/labels/variables/${incluirInactivas ? "?incluir_inactivas=1" : ""}`),
  crear: (cuerpo) => pedir("/labels/variables/", json("POST", cuerpo)),
  editar: (id, cuerpo) => pedir(`/labels/variables/${id}/`, json("PATCH", cuerpo)),
  borrar: (id) => pedir(`/labels/variables/${id}/`, { method: "DELETE" }),
};

const listarFuentes = () => pedir("/labels/fuentes/");

// --- Documentos -----------------------------------------------------------

const Documentos = {
  listar: (ruta = "/documents/documentos/") => pedir(ruta),
  subir: (archivo) => {
    const cuerpo = new FormData();
    cuerpo.append("archivo", archivo);
    return pedir("/documents/documentos/", { method: "POST", body: cuerpo });
  },
  borrar: (id) => pedir(`/documents/documentos/${id}/`, { method: "DELETE" }),
};

// --- Importación desde foto -----------------------------------------------

const Importaciones = {
  listar: (ruta = "/processing/importaciones/") => pedir(ruta),
  obtener: (id) => pedir(`/processing/importaciones/${id}/`),
  // Síncrono: la respuesta tarda entre 10 y 60 segundos. Si la lectura falla,
  // el backend responde 502 pero igual devuelve la importación con su campo
  // `error`, por eso quien llama tiene que mirar `data` también en ese caso.
  crear: (documentoId) =>
    pedir("/processing/importaciones/", json("POST", { documento: documentoId })),
  reintentar: (id) => pedir(`/processing/importaciones/${id}/reintentar/`, { method: "POST" }),
};

// --- Utilidades de presentación -------------------------------------------

function fechaCorta(iso) {
  return iso ? new Date(iso).toLocaleDateString("es-AR") : "";
}

function tamanoLegible(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// esc() vive en auth-api.js: lo usan también páginas que no cargan este archivo.
