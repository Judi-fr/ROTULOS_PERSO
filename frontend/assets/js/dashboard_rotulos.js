// Listado de rótulos (plantillas_rotulos.html): grilla de tarjetas con
// miniatura, cliente/destinatario y fecha; buscador; modal de vista previa;
// acciones Editar / Duplicar / Eliminar. Los datos salen de
// frontend/pedidos/api.js (apps.labels), que ya trae su propio manejo de
// sesión (401/403) — acá solo se decide cuándo redirigir si no hay token.

import { listRotulos, deleteRotulo, duplicateRotulo } from "../../pedidos/api.js";

const EDITOR_URL = "pedidos/diseñorotulos.html";

const contentEl = document.getElementById("content");
const searchInput = document.getElementById("search");

const modalOverlay = document.getElementById("modalOverlay");
const modalImg = document.getElementById("modalImg");
const modalNombre = document.getElementById("modalNombre");
const modalCliente = document.getElementById("modalCliente");
const modalFecha = document.getElementById("modalFecha");
const modalCloseBtn = document.getElementById("modalCloseBtn");

let rotulos = [];
let searchTerm = "";

function formatDate(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString("es-AR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  } catch {
    return "-";
  }
}

function matchesSearch(rotulo, term) {
  if (!term) return true;
  const haystack = `${rotulo.nombre || ""} ${rotulo.cliente || ""}`.toLowerCase();
  return haystack.includes(term);
}

function openModal(rotulo) {
  modalImg.src = rotulo.thumbnail || "";
  modalImg.alt = `Vista previa de ${rotulo.nombre || "rótulo"}`;
  modalNombre.textContent = rotulo.nombre || "Rótulo sin nombre";
  modalCliente.textContent = rotulo.cliente || "-";
  modalFecha.textContent = formatDate(rotulo.updatedAt);
  modalOverlay.classList.add("open");
}

function closeModal() {
  modalOverlay.classList.remove("open");
}

modalCloseBtn?.addEventListener("click", closeModal);
modalOverlay?.addEventListener("click", (e) => {
  if (e.target === modalOverlay) closeModal();
});

function render() {
  const filtered = rotulos.filter((r) => matchesSearch(r, searchTerm));

  if (!rotulos.length) {
    contentEl.innerHTML =
      '<div class="empty-state">Todavía no creaste ningún rótulo. Usá "+ Nuevo rótulo" para empezar.</div>';
    return;
  }
  if (!filtered.length) {
    contentEl.innerHTML = '<div class="empty-state">No hay rótulos que coincidan con la búsqueda.</div>';
    return;
  }

  const grid = document.createElement("div");
  grid.className = "rotulos-grid";

  filtered.forEach((rotulo) => {
    const card = document.createElement("div");
    card.className = "rotulo-card";

    const thumb = document.createElement("div");
    thumb.className = "rotulo-thumb";
    if (rotulo.thumbnail) {
      const img = document.createElement("img");
      img.src = rotulo.thumbnail;
      img.alt = rotulo.nombre || "Rótulo";
      thumb.appendChild(img);
    } else {
      thumb.classList.add("rotulo-thumb-empty");
      thumb.textContent = "Sin vista previa";
    }
    thumb.addEventListener("click", () => openModal(rotulo));

    const body = document.createElement("div");
    body.className = "rotulo-card-body";
    body.innerHTML = `
      <h3 class="rotulo-card-title">${rotulo.nombre || "Rótulo sin nombre"}</h3>
      <p class="rotulo-card-client">${rotulo.cliente || "Sin destinatario"}</p>
      <p class="rotulo-card-date">${formatDate(rotulo.updatedAt)}</p>
    `;

    const actions = document.createElement("div");
    actions.className = "rotulo-card-actions";

    const editBtn = document.createElement("a");
    editBtn.className = "btn";
    editBtn.textContent = "Editar";
    editBtn.href = `${EDITOR_URL}?id=${rotulo.id}`;

    const duplicateBtn = document.createElement("button");
    duplicateBtn.className = "btn";
    duplicateBtn.textContent = "Duplicar";
    duplicateBtn.addEventListener("click", () => handleDuplicate(rotulo));

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn btn-danger";
    deleteBtn.textContent = "Eliminar";
    deleteBtn.addEventListener("click", () => handleDelete(rotulo));

    actions.append(editBtn, duplicateBtn, deleteBtn);
    card.append(thumb, body, actions);
    grid.appendChild(card);
  });

  contentEl.innerHTML = "";
  contentEl.appendChild(grid);
}

async function loadRotulos() {
  contentEl.innerHTML = '<div class="loading">Cargando rótulos...</div>';
  try {
    rotulos = await listRotulos();
    render();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar los rótulos:", err);
    contentEl.innerHTML = '<div class="empty-state">No se pudieron cargar los rótulos.</div>';
  }
}

async function handleDuplicate(rotulo) {
  try {
    await duplicateRotulo(rotulo.id);
    await loadRotulos();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al duplicar el rótulo:", err);
    alert(err.message || "No se pudo duplicar el rótulo.");
  }
}

async function handleDelete(rotulo) {
  if (!confirm(`¿Eliminar "${rotulo.nombre || "este rótulo"}"?`)) return;
  try {
    await deleteRotulo(rotulo.id);
    await loadRotulos();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al eliminar el rótulo:", err);
    alert(err.message || "No se pudo eliminar el rótulo.");
  }
}

searchInput?.addEventListener("input", (e) => {
  searchTerm = e.target.value.trim().toLowerCase();
  render();
});

if (!localStorage.getItem("access")) {
  window.location.replace("index.html");
} else {
  loadRotulos();
}
