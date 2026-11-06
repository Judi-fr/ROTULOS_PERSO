// Pruebas de navegador. Levantan la API con una base de datos propia (no la de
// desarrollo) y sin mandar mails reales. Ver README.md.
const { defineConfig } = require("@playwright/test");

// Para usar un Chromium ya instalado en vez de bajar el de Playwright.
const executablePath = process.env.E2E_CHROME || undefined;

module.exports = defineConfig({
  testDir: "./tests",
  // De a una: comparten la base, y el límite de intentos de login es por IP.
  workers: 1,
  timeout: 45000,
  expect: { timeout: 8000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:8001",
    locale: "es-AR",
    launchOptions: { executablePath },
    trace: "retain-on-failure",
  },
  globalSetup: require.resolve("./global-setup.js"),
  webServer: [
    { command: "sh start_backend.sh", url: "http://localhost:8000/api/v1/health/", timeout: 120000, reuseExistingServer: false },
    { command: "python3 -m http.server 8001 --bind 127.0.0.1 --directory ..", url: "http://localhost:8001/index.html", reuseExistingServer: false, stdout: "ignore", stderr: "ignore" },
  ],
});
