import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import { getEffectiveParameters } from './api.ts'
import * as unicodeConfusables from './unicodeConfusables.ts'

const require = createRequire(import.meta.url)
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const file = new URL('./components/EffectiveParameters.tsx', import.meta.url)
const source = readFileSync(file, 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023,
  jsx: ts.JsxEmit.ReactJSX,
} })
const loaded = { exports: {} }
vm.runInNewContext(compiled.outputText, {
  module: loaded, exports: loaded.exports,
  require: name => name === '../api' ? { getEffectiveParameters }
    : name === '../unicodeConfusables' ? unicodeConfusables : require(name),
}, { filename: file.pathname })
const { EffectiveParameterSnapshot } = loaded.exports

function snapshot(params = [], rules) {
  return { run: 'run-0042', snapshot_at: '2026-09-13T19:00:00+02:00',
    model_revision: 7, model_sha256: 'a'.repeat(64),
    rules: rules || [{ id: 'R-1', title: 'Предел', kind: 'HARD', zone: 'BOTH', params }] }
}
function param(value, quote = null, from = 'template_fit') {
  return { name: 'limit', value, quote, from, unit: null, relation: null, condition: null }
}
function render(data) {
  return renderToStaticMarkup(React.createElement(EffectiveParameterSnapshot, { snapshot: data }))
}

test('actual React rendering shows normalized value and quote without inventing answer history', () => {
  const html = render(snapshot([{ ...param(720, 'не более 12:00'),
    unit: 'minutes', relation: 'le', condition: 'с согласия — до 13:00' }]))
  for (const word of ['720', 'не более 12:00', 'template_fit', 'minutes', 'le', 'до 13:00']) {
    assert.ok(html.includes(word), word)
  }
  assert.doesNotMatch(html, /изменено ответом|ответ изменил/)
  assert.match(html, /нормализацией единиц/)
})

test('identical string quote is not repeated', () => {
  const html = render(snapshot([param('A320', 'A320')]))
  assert.equal((html.match(/A320/g) || []).length, 1)
  assert.doesNotMatch(html, /Цитата в модели/)
})

test('legacy parameter without quote keeps its value and source', () => {
  const html = render(snapshot([param(45, null, 'params')]))
  assert.match(html, /45/)
  assert.match(html, /параметры правила \(params\)/)
  assert.doesNotMatch(html, /Цитата в модели/)
})

test('zero, false and structured values are not discarded or truncated', () => {
  const html = render(snapshot([
    { ...param(0), name: 'zero' }, { ...param(false), name: 'false' },
    { ...param(['A320', 'B738']), name: 'types' },
    { ...param({ note: 'x'.repeat(1500) + 'END-OF-VALUE' }), name: 'object' },
  ]))
  for (const value of ['>0<', '>false<', 'A320', 'B738', 'END-OF-VALUE']) {
    assert.ok(html.includes(value), value)
  }
})

test('empty rule and empty model are explicit healthy states', () => {
  assert.match(render(snapshot()), /Параметров нет/)
  assert.match(render(snapshot([], [])), /В модели нет правил/)
})

test('missing and empty string values stay distinguishable in actual rendering', () => {
  assert.match(render(snapshot([param(null)])), /не задано/)
  assert.match(render(snapshot([param('')])), /&quot;&quot;/)
  assert.match(render(snapshot([param('  ')])), /&quot;  &quot;/)
})

test('model text is escaped by React, not inserted as HTML', () => {
  const html = render(snapshot([{ ...param('<script>bad()</script>', '<img src=x onerror=bad()>'),
    name: '<script>name()</script>', condition: '<iframe>bad</iframe>' }]))
  assert.doesNotMatch(html, /<script>|<img |<iframe>/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /&lt;img /)
})

test('the rendered view names its exact snapshot and does not claim execution', () => {
  const html = render(snapshot())
  assert.ok(html.includes('a'.repeat(64)))
  assert.ok(html.includes('2026-09-13T19:00:00+02:00'))
  assert.match(html, /не подтверждение передачи параметров в расчёт/)
})

test('visually similar unequal text is visibly distinguished by code points', () => {
  const html = render(snapshot([param('B210', 'В210')]))
  assert.match(html, /Строки отличаются посимвольно/)
  assert.match(html, /U\+0042/)
  assert.match(html, /U\+0412/)
  assert.doesNotMatch(html, /ошибка ответа|изменено ответом/)
})

test('equal text has no character difference warning', () => {
  const html = render(snapshot([param('В210', 'В210')]))
  assert.doesNotMatch(html, /Строки отличаются посимвольно|U\+0412/)
})

test('astral confusables use code points, not surrogate halves', () => {
  const html = render(snapshot([param('𝐀320', 'A320')]))
  assert.match(html, /U\+1D400/)
  assert.match(html, /U\+0041/)
  assert.doesNotMatch(html, /U\+D835|U\+DC00/)
})

test('missing quotes and normalized non-text values have no character warning', () => {
  for (const input of [param('A320'), param('A320', ''), param(720, '12:00')]) {
    assert.doesNotMatch(render(snapshot([input])), /Строки отличаются посимвольно|кодовые точки/)
  }
})

test('text inspection preserves whitespace and never normalizes identity', () => {
  const html = render(snapshot([param(' B210', 'В210')]))
  assert.doesNotMatch(html, /Строки отличаются посимвольно|кодовые точки/)
  assert.match(html, /> B210<\/pre>/)
  assert.doesNotMatch(html, /ошибка ответа|изменено ответом/)
})

const originalFetch = globalThis.fetch

for (const [value, quote] of [
  ['A320', 'Airbus A320'], ['Полярис-Р200', 'на воздушном судне типа Полярис-Р200'],
  ['A320', 'A321'], ['A320,B738', 'типы: A320 и B738'], ['😀', '😀\u0301'],
]) {
  test('ordinary quote difference is not a visual-confusion warning: ' + value + '/' + quote, () => {
    const html = render(snapshot([param(value, quote)]))
    assert.match(html, /Цитата в модели/)
    assert.doesNotMatch(html, /Строки отличаются посимвольно|кодовые точки|могут выглядеть похоже/)
  })
}
test('Greek and canonical combining pairs use the same full Unicode transform', () => {
  for (const [a, b] of [['A320', 'Α320'], ['Å', 'A\u030a'], ['m', 'rn']]) {
    assert.match(render(snapshot([param(a, b)])), /Строки отличаются посимвольно/)
  }
})
test('blocked rule exposes open blockers without hiding its old parameter', () => {
  const rule = { id: 'R', title: '', kind: 'HARD', zone: 'BOTH',
    blockers: ['Q-UNRESOLVED'], params: [param(720, '720 минут для базы SVO', 'params')] }
  const html = render(snapshot([], [rule]))
  assert.match(html, /Q-UNRESOLVED/)
  assert.match(html, /не сняты блокировки/)
  assert.match(html, /720/)
})
afterEach(() => { globalThis.fetch = originalFetch })
test('API reads only the encoded snapshot endpoint without starting actions', async () => {
  const calls = []
  globalThis.fetch = async (...args) => {
    calls.push(args)
    return new Response(JSON.stringify(snapshot()))
  }
  assert.deepEqual(await getEffectiveParameters('run/0042'), snapshot())
  assert.deepEqual(calls, [['/api/runs/run%2F0042/effective-parameters', undefined]])
})

test('failed snapshot read is an explicit error, not a successful empty table', async () => {
  globalThis.fetch = async () => new Response('{"detail":"модели нет"}', { status: 404 })
  await assert.rejects(getEffectiveParameters('run-0042'), /модели нет/)
})

test('screen wiring supports refresh and prevents an older request from replacing a new run', () => {
  assert.match(source, /return \(\) => \{ active = false \}/)
  assert.match(source, /if \(active\) setSnapshot\(value\)/)
  assert.match(source, /snapshot\?\.run === runId/)
  assert.match(source, /Обновить снимок параметров/)
  assert.match(source, /role="alert"/)
  const page = readFileSync(new URL('./pages/RunPage.tsx', import.meta.url), 'utf8')
  assert.match(page, /<EffectiveParameters runId=\{runId\} \/>/)
})

test('family transitions, missing values and catalogue identity reach actual HTML', () => {
  const data = snapshot()
  data.family_review = { judged: true, catalog: { version: '0.6.1', fingerprint: 'b'.repeat(64) },
    families: [{ family: 'days_off_floor', verdict: 'неполна', contract_state: 'совместимо',
      transitions: ['R-PIN: набор monthly расширен', 'R-OTHER: набор rolling_window сужен'],
      missing: ['days_off_window_days'], pending: ['min_days_off'],
      problems: ['У набора отсутствует состав <script>bad()</script>'] }] }
  const html = render(data)
  for (const text of ['R-PIN', 'monthly расширен', 'rolling_window сужен', 'days_off_window_days',
    'Ожидают пересмотра', 'min_days_off', '0.6.1', 'b'.repeat(64), 'У набора отсутствует состав']) {
    assert.ok(html.includes(text), text)
  }
  assert.doesNotMatch(html, /<script>/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /не означает допуска к исполнению/)
})

test('unavailable catalogue is visible and does not hide model parameters', () => {
  const data = snapshot([param(720)])
  data.family_review = { judged: false, families: [], words: 'Каталог отсутствует' }
  const html = render(data)
  assert.match(html, /Полнота семей не проверена: Каталог отсутствует/)
  assert.match(html, /720/)
  assert.doesNotMatch(html, /Посадок на семьи нет/)
})

test('legacy absent family review is unjudged, not a green empty family list', () => {
  assert.match(render(snapshot()), /Полнота семей не проверена/)
  assert.doesNotMatch(render(snapshot()), /Посадок на семьи нет/)
})

test('migration history names exact evidence and all states without claiming current readiness', () => {
  const data = snapshot([param(600, '10 часов')])
  data.landing_migrations = [{ input_fingerprint: 'b'.repeat(64), output_fingerprint: 'c'.repeat(64),
    catalog: { version: '0.6.1', fingerprint: 'd'.repeat(64) },
    items: ['confirmed', 'required', 'unconfirmed'].map((state, index) => ({
      index, rule: `R-${index}`, state, words: ['<script>не исполнять</script>'],
      changes: [{ param: 'limit', fields: ['unit', 'relation'],
        source: { kind: 'code', note: 'Метаданные исходного контракта' } }],
    })) }]
  const html = render(data)
  for (const text of ['История миграции параметров', 'форма landing/2 подтверждена',
    'перепосадка требуется', 'перепосадка не подтверждена', 'R-0', 'R-1', 'R-2',
    'b'.repeat(64), 'c'.repeat(64), 'd'.repeat(64), 'unit, relation',
    'не текущая готовность или разрешение исполнения', 'не перезаписывается']) {
    assert.ok(html.includes(text), text)
  }
  assert.doesNotMatch(html, /<script>/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /600/)
})

test('missing migration journal does not invent a successful migration', () => {
  assert.doesNotMatch(render(snapshot()), /История миграции параметров|форма landing\/2 подтверждена/)
})
