import "@shopify/ui-extensions/preact";
import {render} from "preact";

import {PrintAction} from "./PrintAction.jsx";

// "Rótulos de envío" en el menú Imprimir: los rótulos o la planilla de
// retiro de los pedidos elegidos (se elige en PrintAction.jsx).
export default async () => {
  render(<PrintAction />, document.body);
};
