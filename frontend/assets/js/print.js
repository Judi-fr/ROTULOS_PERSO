// Imprimir un PDF servido por el backend (rótulo, plantilla o documento
// de lote). El endpoint exige Authorization, así que no se puede apuntar
// una ventana/pestaña directo a su URL: hay que pedirlo con apiFetch,
// quedarse con el blob, y desde ahí imprimir.
//
// ÚNICA implementación compartida por diseñorotulos.html, rotulos.html y
// documentos.html (ver assets/js/rotulos.js, assets/js/documentos.js y el
// script de diseñorotulos.html): todos cargan este archivo como script
// clásico (después de config.js/auth.js) y llaman a
// window.PrintHelper.printFromBlobFn.
window.PrintHelper = (function () {
  "use strict";

  // Carga el PDF en un <iframe> oculto y dispara window.print() sobre él
  // en su onload. Si eso falla (algunos navegadores/móviles no imprimen
  // un PDF embebido), cae a abrir el blob en una pestaña nueva para que
  // el usuario imprima desde el visor nativo del navegador.
  function printBlob(blob) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      let settled = false;

      const openInNewTab = () => {
        if (settled) return;
        settled = true;
        const win = window.open(url, "_blank");
        if (!win) {
          alert(
            "El navegador bloqueó la ventana de impresión. Habilitá las ventanas " +
              "emergentes para este sitio e intentá de nuevo."
          );
        }
        // La pestaña nueva necesita el object URL vivo mientras el
        // usuario la tenga abierta: se libera recién cuando esta promesa
        // se resuelve más abajo, no acá.
        resolve();
      };

      const iframe = document.createElement("iframe");
      iframe.style.position = "fixed";
      iframe.style.right = "0";
      iframe.style.bottom = "0";
      iframe.style.width = "0";
      iframe.style.height = "0";
      iframe.style.border = "0";
      iframe.setAttribute("aria-hidden", "true");

      // Si el iframe ni siquiera termina de cargar en un tiempo razonable
      // (bloqueado, PDF no soportado embebido), no se deja al usuario sin
      // feedback: se cae al fallback.
      const timeoutId = setTimeout(openInNewTab, 4000);

      const cleanupIframe = () => {
        URL.revokeObjectURL(url);
        iframe.remove();
        if (!settled) {
          settled = true;
          resolve();
        }
      };

      iframe.onload = () => {
        clearTimeout(timeoutId);
        try {
          iframe.contentWindow.focus();
          iframe.contentWindow.print();
          // El iframe tiene que seguir vivo mientras el diálogo de
          // impresión del navegador está abierto; se saca recién después.
          setTimeout(cleanupIframe, 1000);
        } catch (err) {
          console.warn(
            "No se pudo imprimir el PDF embebido, se abre en una pestaña nueva:",
            err
          );
          iframe.remove();
          openInNewTab();
        }
      };

      iframe.src = url;
      document.body.appendChild(iframe);
    });
  }

  // getBlobFn: () => Promise<Blob> (ya autenticado, vía apiFetch/api.js).
  // Deshabilita `button` con "Preparando…" mientras se pide el PDF, y lo
  // vuelve a dejar como estaba al terminar (éxito o error).
  async function printFromBlobFn(getBlobFn, button) {
    const originalText = button ? button.textContent : null;
    if (button) {
      button.disabled = true;
      button.textContent = "Preparando…";
    }
    try {
      const blob = await getBlobFn();
      await printBlob(blob);
    } catch (err) {
      if (err && err.isSessionExpired) return;
      console.error("Error al imprimir:", err);
      alert((err && err.message) || "No se pudo preparar la impresión.");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
      }
    }
  }

  return { printBlob, printFromBlobFn };
})();
