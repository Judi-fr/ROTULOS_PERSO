// Chequeo del frontend para el pipeline (azure-pipelines.yml) y uso local:
//   node frontend/tools/check_frontend.mjs
//
// No hay build ni framework, así que no hay compilador que avise de nada: un
// error de sintaxis en un .js o un <script src> a un archivo que no existe
// recién se ve al abrir esa página. Esto revisa, sin dependencias:
//   1. que cada .js de assets/js compile (`node --check`, que detecta solo si
//      es módulo ES o script clásico);
//   2. que cada <script> en línea de los .html compile;
//   3. que cada src/href local de los .html apunte a un archivo que existe.
// Sale con código 1 si encuentra algo.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// El prototipo PHP (ver CLAUDE.md): no es parte de la app ni entra a la imagen.
const SKIP_DIRS = new Set(["apis", "node_modules", "tools"]);
// Restos del prototipo de Google que se conservan a propósito (CLAUDE.md).
const SKIP_HTML = new Set(["index_test.html"]);

const problems = [];
const tmp = mkdtempSync(join(tmpdir(), "check-frontend-"));

function walk(dir, ext, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (!SKIP_DIRS.has(name)) walk(path, ext, out);
    } else if (path.endsWith(ext)) {
      out.push(path);
    }
  }
  return out;
}

function nodeCheck(file, label) {
  try {
    execFileSync(process.execPath, ["--check", file], { stdio: "pipe" });
  } catch (err) {
    const message = String(err.stderr || err.message).split("\n").filter(Boolean).slice(0, 4).join("\n    ");
    problems.push(`${label}: no compila\n    ${message}`);
  }
}

// 1. Archivos .js
const jsFiles = walk(join(ROOT, "assets"), ".js");
jsFiles.forEach((file) => nodeCheck(file, relative(ROOT, file)));

// 2 y 3. Páginas .html
const htmlFiles = readdirSync(ROOT).filter((name) => name.endsWith(".html") && !SKIP_HTML.has(name));
const SCRIPT_RE = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
const REF_RE = /\b(?:src|href)\s*=\s*"([^"]*)"/gi;
let inlineCount = 0;

for (const name of htmlFiles) {
  const html = readFileSync(join(ROOT, name), "utf8");

  for (const match of html.matchAll(SCRIPT_RE)) {
    const [, attrs, body] = match;
    if (/\bsrc\s*=/.test(attrs) || !body.trim()) continue;
    if (/type\s*=\s*"(?!module|text\/javascript)/i.test(attrs)) continue; // JSON, plantillas
    const isModule = /type\s*=\s*"module"/i.test(attrs);
    const line = html.slice(0, match.index).split("\n").length;
    const file = join(tmp, `${name}-${line}.${isModule ? "mjs" : "cjs"}`);
    writeFileSync(file, body);
    nodeCheck(file, `${name}:${line} (<script> en línea)`);
    inlineCount += 1;
  }

  for (const match of html.matchAll(REF_RE)) {
    const ref = match[1].trim();
    if (!ref || /^(?:[a-z]+:|\/\/|#|\$\{|\{\{)/i.test(ref) || ref.includes("${")) continue;
    const path = ref.split(/[?#]/)[0];
    if (!path) continue;
    // Las rutas absolutas (/api, /media) las sirve el backend, no esta carpeta.
    if (path.startsWith("/")) continue;
    if (!existsSync(join(ROOT, path))) {
      const line = html.slice(0, match.index).split("\n").length;
      problems.push(`${name}:${line}: apunta a "${ref}", que no existe`);
    }
  }
}

rmSync(tmp, { recursive: true, force: true });

console.log(
  `Revisados ${jsFiles.length} archivos .js, ${htmlFiles.length} páginas y ${inlineCount} scripts en línea.`
);
if (problems.length) {
  console.error(`\n${problems.length} problema(s):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log("Sin problemas.");
