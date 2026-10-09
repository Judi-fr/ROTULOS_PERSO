import "@shopify/ui-extensions/preact";
import {render} from "preact";

import {PrintAction} from "./PrintAction.jsx";

// "Rótulos de envío" en el menú Imprimir: un rótulo por pedido elegido.
export default async () => {
  render(<PrintAction action="labels" />, document.body);
};
