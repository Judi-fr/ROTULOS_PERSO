const { test, expect } = require("@playwright/test");

test.use({ storageState: ".auth-comerciante.json" });

test("el panel muestra lo pendiente de hoy", async ({ page }) => {
  await page.goto("/dashboard.html");
  await expect(page.locator("h1")).toContainText("Hoy en");
  await expect(page.getByText("Para imprimir", { exact: true })).toBeVisible();
});

test("el buscador de Mis pedidos encuentra y dice cuando no hay nada", async ({ page }) => {
  await page.goto("/pedidos.html");
  await expect(page.locator(".order-item").first()).toBeVisible();
  await page.fill("#orderSearch", "zzzz-no-existe");
  await expect(page.getByText("Ningún pedido coincide")).toBeVisible();
});

test("un pedido a mano con HTML en el nombre no lo ejecuta", async ({ page }) => {
  await page.goto("/pedidos.html#nuevo");
  await page.selectOption("#orderAddressSelect", { label: "+ Una dirección nueva…" });
  await page.fill("#newAddrRecipient", '<img src=x onerror="window.__xss=1">Prueba');
  await page.fill("#newAddrStreet", "Mitre");
  await page.fill("#newAddrCity", "Rosario");
  await page.fill("#newAddrPostalCode", "2000");
  await page.click("#createOrderBtn");
  await expect(page.locator("#pageMessage")).toContainText("Pedido creado");
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
});

test("imprimir genera un lote con los pedidos sin rótulo", async ({ page }) => {
  await page.goto("/imprimir_rotulos.html?rotulo=sin_imprimir");
  await expect(page.locator("#selectionCount")).not.toHaveText(/^0 /);
  await page.click("#printBtn");
  await page.waitForURL(/documentos\.html/);
  await expect(page.locator("main")).toContainText(/Listo|Procesando/);
});

test("conectar una tienda muestra solo la plataforma elegida", async ({ page }) => {
  await page.goto("/tiendas.html");
  const card = page.locator("#connectCard");
  // Con una tienda ya conectada la tarjeta se cierra sola al cargar la lista.
  await expect(card).not.toHaveAttribute("open", "");
  await card.locator("> summary").click();
  await expect(page.locator("#connectShopifyBtn")).toBeHidden();
  await page.click('[data-platform-pick="shopify"]');
  await expect(page.locator("#connectShopifyBtn")).toBeVisible();
  await expect(page.locator("#connectTiendanubeBtn")).toBeHidden();
});

test("una plantilla de cero parte de la estándar, guarda marcadores y no se duplica", async ({ page, request }) => {
  await page.goto("/editor_rotulos.html?nueva=plantilla");
  const field = page.locator('.field[data-field="destinatario"]');
  await expect(page.locator("#samplesToggle")).toBeChecked();
  await expect(page.locator(".label")).toContainText("Enviar a:");
  await expect(field).not.toContainText("{{");
  await page.click("#saveNewTemplateBtn");
  const name = `E2E ${Date.now()}`;
  await page.locator("dialog[open] input").fill(name);
  await page.locator("dialog[open] button").last().click();
  await page.waitForURL(/template=\d+/);
  await expect(page.locator("h1")).toContainText(name);
  await expect(page.locator("#saveNewTemplateBtn")).toBeHidden();
  await expect(page.locator("#saveTemplateVersionBtn")).toBeVisible();
  const design = await page.evaluate(async (wanted) => {
    const data = await window.Auth.apiFetch(`${window.APP_CONFIG.API_BASE}/labels/templates/`).then((r) => r.json());
    return (data.results || data).find((t) => t.name === wanted).design;
  }, name);
  // Lo que distingue a la estándar: el recuadro punteado y el "Enviar a:".
  expect(design.destinatario.text).toBe("{{destinatario}}");
  expect(design.border).toEqual({ dashed: true });
  expect(design.texts.map((t) => t.text)).toContain("Enviar a:");
});

test("restaurar una versión ofrece dos botones distintos", async ({ page }) => {
  // Los dos decían "Volver".
  await page.goto("/mis_plantillas.html");
  const id = await page.evaluate(async () => {
    const send = (path, method, body) =>
      window.Auth.apiFetch(`${window.APP_CONFIG.API_BASE}/labels/templates/${path}`, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then((r) => r.json());
    const design = { destinatario: { left: 5, top: 5, text: "{{destinatario}}" } };
    const created = await send("", "POST", { name: `Versiones ${Date.now()}`, width_cm: 10, height_cm: 15, design });
    await send(`${created.id}/`, "PATCH", { width_cm: 11 });
    return created.id;
  });
  await page.goto(`/versiones_plantilla.html?template=${id}`);
  await page.getByRole("button", { name: "Restaurar" }).first().click();
  await expect(page.locator("dialog[open] button")).toHaveText(["Volver", "Restaurar"]);
});

test("elegir la plataforma mientras carga la lista no cierra la tarjeta", async ({ page }) => {
  // La tarjeta se cerraba al llegar la lista, con el formulario recién elegido.
  await page.route("**/integrations/stores/**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });
  await page.goto("/tiendas.html");
  await page.click('[data-platform-pick="shopify"]');
  await page.waitForTimeout(2500);
  await expect(page.locator("#connectShopifyBtn")).toBeVisible();
});

test("una dirección vieja del editor de rótulos abre una plantilla nueva", async ({ page }) => {
  await page.goto("/editor_rotulos.html?id=1");
  await page.waitForURL(/nueva=plantilla/);
  await expect(page.locator("h1")).toHaveText("Nueva plantilla");
});

test("la galería ofrece De cero y también la predeterminada para elegirla", async ({ page }) => {
  await page.goto("/galeria_plantillas.html");
  const first = page.locator(".gallery-card").first();
  await expect(first).toContainText("De cero");
  await expect(first).toHaveAttribute("href", "editor_rotulos.html?nueva=plantilla");
  const standard = page.locator(".gallery-card", { hasText: "Etiqueta de envío estándar" });
  await expect(standard).toHaveCount(1);
  await standard.click();
  await page.waitForURL(/nueva=plantilla&template=\d+/);
  await expect(page.locator(".label")).toContainText("Enviar a:");
});
