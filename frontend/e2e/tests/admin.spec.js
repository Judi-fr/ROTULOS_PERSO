const { test, expect } = require("@playwright/test");

test.use({ storageState: ".auth-admin.json" });

test("el botón Actualizar de Usuarios vuelve a pedir la lista", async ({ page }) => {
  // No tenía nada conectado.
  await page.goto("/gestionuser.html");
  await expect(page.locator("tbody tr").first()).toContainText("@");
  const request = page.waitForRequest((r) => /\/api\/v1\/users\//.test(r.url()) && r.method() === "GET");
  await page.click("#refreshUsersBtn");
  await request;
});

test("en celular el admin tiene menú", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/gestionuser.html");
  await expect(page.locator(".sidebar .nav a").first()).toBeVisible();
});

test("no ve el usuario interno de los comercios", async ({ page }) => {
  await page.goto("/gestionuser.html");
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await expect(page.locator("main")).not.toContainText("datos.invalid");
});
