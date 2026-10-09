import "@shopify/ui-extensions/preact";
import {render} from "preact";
import {useState} from "preact/hooks";

import {PRINT_LINK_URL, requestLink} from "./printLink.js";

// "Despachar con Andreani" en las acciones masivas de la lista de pedidos.
// Despachar cambia cosas (crea los envíos en Andreani con la cuenta del
// comerciante y les carga el seguimiento a los pedidos), así que no arranca
// solo al abrir: se confirma con el botón. Después ofrece abrir el PDF con
// cada rótulo y la etiqueta de Andreani.
export default async () => {
  render(<Extension />, document.body);
};

function Extension() {
  const {i18n, data, close} = shopify;
  const ids = (data.selected || []).map((item) => item.id);
  const [state, setState] = useState({status: "idle"});

  async function dispatch() {
    setState({status: "working"});
    try {
      const body = await requestLink(ids, "dispatch");
      setState({status: "done", url: body.url, created: body.created || 0, failed: body.failed || [], missing: (body.missing || []).length});
    } catch (error) {
      console.error("rotulos-envio: fallo despachando con", PRINT_LINK_URL, error);
      setState({status: "error", message: error.message || i18n.translate("genericError")});
    }
  }

  return (
    <s-admin-action heading={i18n.translate("dispatch_heading")}>
      <s-stack direction="block" gap="base">
        {state.status === "idle" && (
          <s-text>
            {ids.length === 1 ? i18n.translate("dispatch_confirmOne") : i18n.translate("dispatch_confirm", {count: ids.length})}
          </s-text>
        )}
        {state.status === "working" && <s-text>{i18n.translate("dispatch_working")}</s-text>}
        {state.status === "error" && (
          <s-banner tone="critical" heading={i18n.translate("dispatch_errorTitle")}>
            {state.message}
          </s-banner>
        )}
        {state.status === "done" && (
          <s-banner tone="success" heading={i18n.translate("dispatch_done", {count: state.created})}>
            {i18n.translate("dispatch_doneBody")}
          </s-banner>
        )}
        {state.status === "done" && state.failed.length > 0 && (
          <s-banner tone="warning" heading={i18n.translate("dispatch_failedTitle")}>
            {state.failed.map((item) => `#${item.number}: ${item.detail}`).join(" · ")}
          </s-banner>
        )}
        {state.status === "done" && state.missing > 0 && (
          <s-banner tone="warning" heading={i18n.translate("missingTitle")}>
            {i18n.translate("missingBody", {count: state.missing})}
          </s-banner>
        )}
      </s-stack>
      {state.status === "done" ? (
        <s-button slot="primary-action" href={state.url} target="_blank">
          {i18n.translate("dispatch_open")}
        </s-button>
      ) : (
        <s-button slot="primary-action" onClick={dispatch} disabled={state.status === "working"} loading={state.status === "working"}>
          {i18n.translate("dispatch_button")}
        </s-button>
      )}
      <s-button slot="secondary-actions" onClick={() => close()}>
        {i18n.translate("close")}
      </s-button>
    </s-admin-action>
  );
}
