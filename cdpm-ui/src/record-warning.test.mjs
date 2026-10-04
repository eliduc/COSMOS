import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFile } from 'node:fs/promises'
import { listRuns, refreshRunRecordOnly, RECORD_WRITE_WARNING_EVENT } from './api.ts'

const realFetch = globalThis.fetch
const realWindow = globalThis.window
afterEach(() => {
  globalThis.fetch = realFetch
  if (realWindow === undefined) delete globalThis.window
  else globalThis.window = realWindow
})

function receiver() {
  const events = []
  globalThis.window = new EventTarget()
  window.addEventListener(RECORD_WRITE_WARNING_EVENT, event => events.push(event.detail))
  return events
}

test('busy derived record preserves success and emits a visible warning once', async () => {
  const events = receiver()
  globalThis.fetch = async () => new Response(JSON.stringify([{ run: 'run-0042' }]), {
    headers: { 'X-CDPM-Record-Warning': 'record-refresh-busy',
               'X-CDPM-Record-Runs': 'run-0042,not-a-run,run-0001' } })
  assert.deepEqual(await listRuns(), [{ run: 'run-0042' }])
  assert.deepEqual(events, [{ runs: ['run-0042', 'run-0001'] }])
})

test('healthy response is quiet', async () => {
  const events = receiver()
  globalThis.fetch = async () => new Response('[]')
  assert.deepEqual(await listRuns(), [])
  assert.deepEqual(events, [])
})

test('failed mandatory write keeps human error, not a success warning', async () => {
  const events = receiver()
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: {
    code: 'record-refresh-busy', error: 'Файл записи занят. Обновите только запись.'
  } }), { status: 503, headers: { 'X-CDPM-Record-Warning': 'record-refresh-busy' } })
  await assert.rejects(refreshRunRecordOnly('run-0042'), /занят.*только запись/)
  assert.deepEqual(events, [])
})

test('retry uses only record refresh, never append or a processing action', async () => {
  const calls = []
  globalThis.fetch = async (...args) => {
    calls.push(args)
    return new Response('{"ok":true}')
  }
  assert.deepEqual(await refreshRunRecordOnly('run/0042'), { ok: true })
  assert.deepEqual(calls, [['/api/runs/run%2F0042/record/refresh', { method: 'POST' }]])
})

test('all API fetches pass through the shared warning reader', async () => {
  const source = await readFile(new URL('./api.ts', import.meta.url), 'utf8')
  assert.equal((source.match(/\bfetch\(/g) || []).length, 1)
  assert.match(source, /await globalThis\.fetch\(input, init\)/)
})

test('app wiring keeps warning visible and guards against concurrent replacement', async () => {
  const source = await readFile(new URL('./App.tsx', import.meta.url), 'utf8')
  assert.match(source, /addEventListener\(RECORD_WRITE_WARNING_EVENT, warn\)/)
  assert.match(source, /removeEventListener\(RECORD_WRITE_WARNING_EVENT, warn\)/)
  assert.match(source, /role="status" aria-live="polite"/)
  assert.match(source, /Обновить только запись/)
  assert.match(source, /Не повторяйте само действие/)
  assert.match(source, /recordWarningGeneration\.current === generation/)
  assert.match(source, /for \(const run of runs\) await refreshRunRecordOnly\(run\)/)
})
