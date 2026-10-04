import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

const require = createRequire(import.meta.url)
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const file = new URL('./components/PackageReadinessProblems.tsx', import.meta.url)
const compiled = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023,
  jsx: ts.JsxEmit.ReactJSX,
} })
const loaded = { exports: {} }
vm.runInNewContext(compiled.outputText, {
  module: loaded, exports: loaded.exports, require,
}, { filename: file.pathname })
const render = problems => renderToStaticMarkup(React.createElement(
  loaded.exports.PackageReadinessProblems, { problems }))

test('run card renders package readiness reasons from P4', () => {
  const page = readFileSync(new URL('./pages/RunPage.tsx', import.meta.url), 'utf8')
  assert.ok(/import \{ PackageReadinessProblems \} from '\.\.\/components\/PackageReadinessProblems'/.test(page))
  assert.ok(/<PackageReadinessProblems\s+problems=\{status\.principles!\.P4\.problems\}\s*\/>/.test(page))
})

test('actual React output shows the full addressed timezone reason', () => {
  const words = 'R-SCOPE/night_duty_start: time_of_day требует tz, получено None'
  const html = render([words])
  assert.ok(html.includes(words))
  assert.match(html, /Причины неготовности пакета/)
  assert.match(html, /role="status" aria-live="polite"/)
})

test('healthy or older response adds no warning', () => {
  assert.equal(render([]), '')
  assert.equal(render(undefined), '')
})

test('all reasons and long parameter names remain visible without truncation', () => {
  const reasons = Array.from({ length: 8 }, (_, i) => `R-${i}: ` + 'x'.repeat(500) + ` END-${i}`)
  const html = render(reasons)
  for (const reason of reasons) assert.ok(html.includes(reason))
  assert.equal((html.match(/<li /g) || []).length, 8)
  assert.doesNotMatch(html, /line-clamp|truncate|hidden|<details/)
})

test('source text is escaped, not inserted as HTML', () => {
  const html = render(['<script>bad()</script>', '<img src=x onerror=bad()>'])
  assert.doesNotMatch(html, /<script>|<img /)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /&lt;img /)
})
