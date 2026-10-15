import {useEffect, useState} from "preact/hooks";

import {PRINT_LINK_URL, requestLink} from "./printLink.js";

// La opción de la app en el menú "Imprimir" de la lista de pedidos. Shopify
// admite UNA sola extensión de impresión por app (el deploy rechaza una
// segunda), así que acá se elige qué documento sale: los rótulos o la
// planilla de retiro. Cada documento tiene sus textos en locales/ con su
// prefijo ("" o "manifest_").
const DOCUMENTS = [
  {action: "labels", prefix: ""},
  {action: "manifest", prefix: "manifest_"},
];

export function PrintAction() {
  const {i18n, data} = shopify;
  const ids = (data.selected || []).map((item) => item.id);
  const selectionKey = ids.join(",");
  const [action, setAction] = useState("labels");
  const prefix = DOCUMENTS.find((doc) => doc.action === action).prefix;
  const t = (key, options) => i18n.translate(`${prefix}${key}`, options);
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
  }, [selectionKey, action]);

  function onDocumentChange(event) {
    const [value] = event.currentTarget.values || [];
    if (value) setAction(value);
  }

  return (
    <s-admin-print-action src={src}>
      <s-stack direction="block" gap="base">
        <s-choice-list label={i18n.translate("documentLabel")} name="document" onChange={onDocumentChange}>
          <s-choice value="labels" selected={action === "labels"}>{i18n.translate("documentLabels")}</s-choice>
          <s-choice value="manifest" selected={action === "manifest"}>{i18n.translate("documentManifest")}</s-choice>
        </s-choice-list>
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
