// Chequeo del frontend para el pipeline (azure-pipelines.yml) y uso local:
//   node frontend/tools/check_frontend.mjs
//
// No hay build ni framework, así que no hay compilador que avise de nada: un
// error de sintaxis en un .js o un <script src> a un archivo que no existe
// recién se ve al abrir esa página. Esto revisa, sin dependencias:
//   1. que cada .js de assets/js compile (`node --check`, que detecta solo si
//      es módulo ES o script clásico);
//   2. que cada <script> en línea de los .html compile;
//   3. que cada src/href local de los .html apunte a un archivo que existe;
//   4. que un script no use directamente un elemento que su página ya no tiene
//      (getElementById("x").algo, querySelector(".x").algo, closest(".x").algo):
//      revienta recién al ejecutarse esa línea. Con ?. o comprobando antes, pasa.
// Sale con código 1 si encuentra algo.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// El prototipo PHP (ver CLAUDE.md): no es parte de la app ni entra a la imagen.
const SKIP_DIRS = new Set(["apis", "e2e", "node_modules", "tools"]);
// Restos del prototipo de Google que se conservan a propósito (CLAUDE.md).
const SKIP_HTML = new Set(["index_test.html"]);

const problems = [];
const tmp = mkdtempSync(join(tmpdir(), "check-frontend-"));

// Compartidos por muchas páginas: cada una tiene solo parte de lo que usan.
const SHARED_SCRIPTS = new Set(["utils.js", "auth.js", "topbar.js", "command_palette.js", "admin_sidebar.js", "admin_common.js", "print.js", "config.js"]);

function checkElementRefs(name, html) {
  const ids = new Set([...html.matchAll(/\bid=["']([\w-]+)["']/g)].map((m) => m[1]));
  const classes = new Set([...html.matchAll(/class=["']([^"']+)["']/g)].flatMap((m) => m[1].split(/\s+/)));
  const scripts = [...html.matchAll(/<script[^>]+src=["'](assets\/js\/[^"'?]+)/g)]
    .map((m) => join(ROOT, m[1]))
    .filter((path) => existsSync(path));
  const inline = [...html.matchAll(SCRIPT_RE)].filter((m) => !/\bsrc\s*=/.test(m[1])).map((m) => m[2]);
  const sources = [
    ...scripts.map((path) => ({ label: relative(ROOT, path), shared: SHARED_SCRIPTS.has(path.split("/").pop()), text: readFileSync(path, "utf8") })),
    ...inline.map((text) => ({ label: name, shared: false, text })),
  ];
  // Lo que arma el propio JS (templates, .id =, className) cuenta como existente.
  const all = sources.map((source) => source.text).join("\n");
  for (const m of all.matchAll(/\bid=\\?["']([\w-]+)/g)) ids.add(m[1]);
  for (const m of all.matchAll(/\.id\s*=\s*["'`]([\w-]+)/g)) ids.add(m[1]);
  // Ids que se le pasan a un armador de campos ({ id: "x" }, campo("x", ...)); sin
  // contar las búsquedas, que son justamente lo que se está revisando.
  const withoutLookups = all.replace(/getElementById\(["'][\w-]+["']\)/g, "");
  for (const m of withoutLookups.matchAll(/["'`]([a-zA-Z][\w-]*)["'`]/g)) ids.add(m[1]);
  // Clases armadas por partes: `shape-swatch-${cls}`.
  const classPrefixes = [...all.matchAll(/([a-zA-Z][\w-]*-)\$\{/g)].map((m) => m[1]);
  // Las clases, solo de la página y sus scripts propios: los compartidos mencionan
  // clases de otras páginas, y así un `.profile-card` que acá ya no existe pasaba.
  const own = sources.filter((source) => !source.shared).map((source) => source.text).join("\n");
  for (const m of own.matchAll(/["'`]([a-zA-Z][\w -]*)["'`]/g)) m[1].split(/\s+/).forEach((word) => classes.add(word));
  for (const m of own.matchAll(/class(?:Name)?\s*[=:]\s*\\?["'`]([^"'`]*)/g)) m[1].split(/\s+/).forEach((word) => classes.add(word));
  for (const source of sources) {
    if (source.shared) continue;
    const report = (index, what) => {
      const line = source.text.slice(0, index).split("\n").length;
      problems.push(`${source.label}:${line}: en ${name} usa ${what}, que esa página no tiene`);
    };
    for (const m of source.text.matchAll(/getElementById\(["']([\w-]+)["']\)\s*\.\s*\w/g)) {
      if (!ids.has(m[1])) report(m.index, `#${m[1]}`);
    }
    for (const m of source.text.matchAll(/querySelector\(["']#([\w-]+)["']\)\s*\.\s*\w/g)) {
      if (!ids.has(m[1])) report(m.index, `#${m[1]}`);
    }
    for (const m of source.text.matchAll(/(?:querySelector|closest)\(["']\.([\w-]+)["']\)\s*\.\s*\w/g)) {
      if (!classes.has(m[1]) && !classPrefixes.some((prefix) => m[1].startsWith(prefix))) report(m.index, `.${m[1]}`);
    }
  }
}

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

  checkElementRefs(name, html);

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
