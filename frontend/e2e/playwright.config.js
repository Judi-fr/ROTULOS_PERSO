// Pruebas de navegador. Levantan la API con una base de datos propia (no la de
// desarrollo) y sin mandar mails reales. Ver README.md.
const { defineConfig } = require("@playwright/test");

// Para usar un Chromium ya instalado en vez de bajar el de Playwright.
const executablePath = process.env.E2E_CHROME || undefined;

// Puertos propios: las pruebas corren aunque la app de desarrollo esté levantada.
const WEB_PORT = 8101;
const API_PORT = 8100;

module.exports = defineConfig({
  testDir: "./tests",
  // De a una: comparten la base, y el límite de intentos de login es por IP.
  workers: 1,
  timeout: 45000,
  expect: { timeout: 8000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    locale: "es-AR",
    launchOptions: { executablePath },
    trace: "retain-on-failure",
  },
  globalSetup: require.resolve("./global-setup.js"),
  webServer: [
    {
      command: `sh start_backend.sh ${API_PORT}`,
      url: `http://localhost:${API_PORT}/api/v1/health/`,
      timeout: 120000,
      reuseExistingServer: false,
    },
    {
      command: `node static_server.js ${WEB_PORT} ${API_PORT}`,
      url: `http://localhost:${WEB_PORT}/index.html`,
      reuseExistingServer: false,
    },
  ],
});
