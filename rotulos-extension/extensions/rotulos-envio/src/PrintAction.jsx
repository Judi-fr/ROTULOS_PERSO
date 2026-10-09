import {useEffect, useState} from "preact/hooks";

import {PRINT_LINK_URL, requestLink} from "./printLink.js";

// Una opción del menú "Imprimir" de la lista de pedidos: rótulos
// (PrintActionExtension.jsx) o planilla de retiro (ManifestPrintAction.jsx).
// ``prefix`` elige los textos en locales/ ("" o "manifest_").
export function PrintAction({action, prefix = ""}) {
  const {i18n, data} = shopify;
  const t = (key, options) => i18n.translate(`${prefix}${key}`, options);
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

    requestLink(ids, action)
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
        {state.status === "loading" && <s-text>{t("loading")}</s-text>}
        {state.status === "error" && (
          <s-banner tone="critical" heading={t("errorTitle")}>
            {state.message}
          </s-banner>
        )}
        {state.status === "ready" && (
          <s-text>{state.count === 1 ? t("readyOne") : t("ready", {count: state.count})}</s-text>
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
