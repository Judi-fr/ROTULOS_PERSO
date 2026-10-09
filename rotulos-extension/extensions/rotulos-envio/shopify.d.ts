import '@shopify/ui-extensions';

//@ts-ignore
declare module './src/PrintActionExtension.jsx' {
  const shopify: import('@shopify/ui-extensions/admin.order-index.selection-print-action.render').Api;
  const globalThis: { shopify: typeof shopify };
}

//@ts-ignore
declare module './src/PrintAction.jsx' {
  const shopify: import('@shopify/ui-extensions/admin.order-index.selection-print-action.render').Api;
  const globalThis: { shopify: typeof shopify };
}

//@ts-ignore
declare module './src/ManifestPrintAction.jsx' {
  const shopify: import('@shopify/ui-extensions/admin.order-index.selection-print-action.render').Api;
  const globalThis: { shopify: typeof shopify };
}

//@ts-ignore
declare module './src/DispatchAction.jsx' {
  const shopify: import('@shopify/ui-extensions/admin.order-index.selection-action.render').Api;
  const globalThis: { shopify: typeof shopify };
}
