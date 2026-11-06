const { test, expect } = require("@playwright/test");

test.use({ storageState: ".auth-empleado.json" });

test("una pantalla sin acceso lo dice, sin errores debajo", async ({ page }) => {
  await page.goto("/impresoras.html");
  await expect(page.getByText("No tenés acceso a esta pantalla")).toBeVisible();
  await expect(page.locator("#agentName")).toBeHidden();
  await expect(page.locator("#pageMessage")).toBeHidden();
});

test("lo que su perfil permite abre normal", async ({ page }) => {
  await page.goto("/pedidos.html");
  await expect(page.getByText("No tenés acceso a esta pantalla")).toBeHidden();
  await expect(page.locator(".order-item").first()).toBeVisible();
});
