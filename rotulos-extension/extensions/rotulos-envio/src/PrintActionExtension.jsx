import "@shopify/ui-extensions/preact";
import {render} from "preact";
import {useEffect, useState} from "preact/hooks";

// Ruta relativa: según la documentación, Shopify la resuelve contra la App
// URL de shopify.app.rotulos-perso.toml, así que la extensión no lleva el
// dominio del servidor escrito y no hay que tocarla al cambiar de servidor.
//
// Probando desde la máquina que corre el túnel de Tailscale, el dominio
// resuelve a su IP privada (100.x) y el navegador bloquea el pedido
// ("local network access": NetworkError, sin código de estado, nada en el
// servidor). Hay que desactivar el DNS de Tailscale o probar desde otro
// dispositivo.
const PRINT_LINK_URL = "/api/v1/integrations/shopify/print-link/";

// El ID token de Shopify se agrega a mano en vez de esperar que Shopify lo
// sume solo: no dependemos de qué considera "el dominio de la app".
async function requestPrintLink(ids) {
  const token = await shopify.auth.idToken();
  const response = await fetch(PRINT_LINK_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? {Authorization: `Bearer ${token}`} : {}),
    },
    body: JSON.stringify({ids}),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return body;
}

export default async () => {
  render(<Extension />, document.body);
};

function Extension() {
  const {i18n, data} = shopify;
  const ids = (data.selected || []).map((item) => item.id);
  const selectionKey = ids.join(",");
  const [src, setSrc] = useState(null);
  const [state, setState] = useState({status: "loading"});

  // El backend devuelve un enlace corto al PDF (no el PDF): la vista previa
  // de impresión lo carga como documento, donde no viaja el ID token.
  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    setState({status: "loading"});

    requestPrintLink(ids)
      .then((body) => {
        if (cancelled) return;
        setSrc(body.url);
        setState({status: "ready", count: body.count, missing: (body.missing || []).length});
      })
      .catch((error) => {
        // Para diagnosticar desde la consola del navegador (F12).
        console.error("rotulos-envio: fallo pidiendo el enlace a", PRINT_LINK_URL, error);
        if (cancelled) return;
        setState({status: "error", message: error.message || i18n.translate("genericError")});
      });

    return () => {
      cancelled = true;
    };
  }, [selectionKey]);

  return (
    <s-admin-print-action src={src}>
      <s-stack direction="block">
        {state.status === "loading" && <s-text>{i18n.translate("loading")}</s-text>}
        {state.status === "error" && (
          <s-banner tone="critical" heading={i18n.translate("errorTitle")}>
            {state.message}
          </s-banner>
        )}
        {state.status === "ready" && (
          <s-text>
            {state.count === 1 ? i18n.translate("readyOne") : i18n.translate("ready", {count: state.count})}
          </s-text>
        )}
        {state.status === "ready" && state.missing > 0 && (
          <s-banner tone="warning" heading={i18n.translate("missingTitle")}>
            {i18n.translate("missingBody", {count: state.missing})}
          </s-banner>
        )}
      </s-stack>
    </s-admin-print-action>
  );
}
