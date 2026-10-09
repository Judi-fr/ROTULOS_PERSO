import "@shopify/ui-extensions/preact";
import {render} from "preact";

import {PrintAction} from "./PrintAction.jsx";

// "Planilla de retiro" en el menú Imprimir: la lista de los pedidos elegidos
// que firma el transportista al llevárselos, con su número de seguimiento.
export default async () => {
  render(<PrintAction action="manifest" prefix="manifest_" />, document.body);
};
