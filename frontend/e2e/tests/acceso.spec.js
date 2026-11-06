const { test, expect } = require("@playwright/test");

test("recuperar contraseña avisa que salió bien", async ({ page }) => {
  // Mostraba "Error de red" después de mandar el mail.
  await page.goto("/recuperar-password.html");
  await page.fill("#emailInput", "demo@rotulos.local");
  await page.click("#submitBtn");
  await expect(page.locator("#pageMessage")).toContainText("Si existe una cuenta");
  await expect(page.locator("#pageMessage")).toHaveClass(/success/);
});

test("el registro pide lo que el servidor exige", async ({ page }) => {
  await page.goto("/register.html");
  await expect(page.locator("main")).toContainText("Al menos 8 caracteres");
});

test("sin sesión las pantallas mandan a entrar", async ({ page }) => {
  await page.goto("/pedidos.html");
  await page.waitForURL(/index\.html/);
});
