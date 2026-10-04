// Штамп свежести сборки: dist/build-stamp.json несёт отпечаток
// исходных файлов интерфейса. Гейт T12 (tests/catalog_service/
// test_ui_static.py) пересчитывает ту же формулу и краснеет, если
// dist/ отстал от src/ — «сборка коммитится вместе с исходником»
// перестаёт быть обещанием на словах.
//
// ФОРМУЛА (одна и та же здесь, в тесте T12 и в README интерфейса):
//   входы  — src/**, index.html, public/**, package.json,
//            package-lock.json, vite.config.ts, tsconfig*.json,
//            tailwind.config.js, postcss.config.js, .oxlintrc.json;
//   пути   — относительные от catalog-ui/, разделитель «/» (POSIX),
//            отсортированы по возрастанию;
//   тело   — байты файла с заменой CRLF на LF (клон на Windows и на
//            Linux даёт один штамп);
//   вклад  — «путь\0тело\0» в порядке отсортированных путей;
//   штамп  — sha256 всех вкладов подряд, шестнадцатеричной строкой.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync, writeFileSync, existsSync }
  from 'node:fs'
import { join, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

const DIRS = ['src', 'public']
const FILES = [
  'index.html', 'package.json', 'package-lock.json', 'vite.config.ts',
  'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json',
  'tailwind.config.js', 'postcss.config.js', '.oxlintrc.json',
]

function walk(dir, out) {
  for (const name of readdirSync(dir).sort()) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else out.push(full)
  }
}

function inputs() {
  const found = []
  for (const d of DIRS) {
    const full = join(ROOT, d)
    if (existsSync(full)) walk(full, found)
  }
  for (const f of FILES) {
    const full = join(ROOT, f)
    if (existsSync(full)) found.push(full)
  }
  return found
    .map(p => relative(ROOT, p).split('\\').join('/'))
    .sort()
}

function stampOf(paths) {
  const hash = createHash('sha256')
  const NUL = Buffer.from([0])
  for (const rel of paths) {
    const body = readFileSync(join(ROOT, rel))
    const normalized = Buffer.from(
      body.toString('binary').split('\r\n').join('\n'), 'binary')
    hash.update(Buffer.from(rel, 'utf8'))
    hash.update(NUL)
    hash.update(normalized)
    hash.update(NUL)
  }
  return hash.digest('hex')
}

const paths = inputs()
const stamp = { form_version: 1, files: paths.length, src_sha256: stampOf(paths) }
const out = join(ROOT, 'dist', 'build-stamp.json')
writeFileSync(out, JSON.stringify(stamp, null, 1) + '\n', 'utf8')
console.log(`штамп свежести: файлов ${paths.length}, src_sha256 `
  + `${stamp.src_sha256.slice(0, 12)}… → dist/build-stamp.json`)
