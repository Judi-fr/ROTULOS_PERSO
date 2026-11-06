// Entra una sola vez con cada cuenta y guarda la sesión: el login tiene un límite
// de 5 intentos por minuto y por IP.
const { chromium } = require("@playwright/test");

const ACCOUNTS = {
  comerciante: ["demo@rotulos.local", "Demo2026"],
  empleado: ["e2e-empleado@rotulos.local", "E2eRotulos2026"],
  admin: ["e2e-admin@rotulos.local", "E2eRotulos2026"],
};

module.exports = async () => {
  const browser = await chromium.launch({ executablePath: process.env.E2E_CHROME || undefined });
  for (const [name, [email, password]] of Object.entries(ACCOUNTS)) {
    const context = await browser.newContext({ baseURL: "http://localhost:8101" });
    const page = await context.newPage();
    await page.goto("/index.html");
    await page.fill("#user", email);
    await page.fill("#password", password);
    await page.click('button[type="submit"]');
    await page.waitForURL(/(dashboard|gestionuser)\.html/);
    await context.storageState({ path: `.auth-${name}.json` });
    await context.close();
  }
  await browser.close();
};
