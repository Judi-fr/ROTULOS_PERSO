// Sirve el frontend para las pruebas, en un puerto que no choca con el de desarrollo.
// config.js sale apuntando a la API de las pruebas (el real usa siempre el 8000).
const fs = require("fs");
const http = require("http");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const [webPort, apiPort] = process.argv.slice(2);
const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json",
  ".ttf": "font/ttf", ".woff2": "font/woff2",
};

http
  .createServer((request, response) => {
    const url = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    if (url === "/assets/js/config.js") {
      response.writeHead(200, { "Content-Type": TYPES[".js"] });
      response.end(`window.APP_CONFIG = { API_BASE: "http://localhost:${apiPort}/api/v1" };`);
      return;
    }
    const file = path.join(ROOT, url);
    if (!file.startsWith(ROOT + path.sep)) {
      response.writeHead(403).end();
      return;
    }
    fs.readFile(file, (error, content) => {
      if (error) {
        response.writeHead(404).end();
        return;
      }
      response.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
      response.end(content);
    });
  })
  .listen(Number(webPort), "127.0.0.1");
