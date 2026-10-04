// K02/T02: execute the real browser API functions without network or a server.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startExchange, startFullCycle } from './api.ts'

const calls = []
globalThis.fetch = async (...args) => {
  calls.push(args)
  return new Response(JSON.stringify({ run: 'unit-run', state: 'started' }))
}

const badRounds = [0, -1, 21, 25, 1.5, NaN, Infinity, -Infinity, '5', true]
for (const value of badRounds) {
  for (const door of ['exchange', 'full-cycle']) {
    test(`${door} refuses rounds ${String(value)} (${typeof value}) before fetch`, async () => {
      calls.length = 0
      const launch = door === 'exchange'
        ? () => startExchange('unit-run', 'cloud', 'local', value, true)
        : () => startFullCycle('unit-run', 'cloud', 'local', value, 3)
      await assert.rejects(launch, /лимит обменов.*1.*20/)
      assert.equal(calls.length, 0)
    })
  }
}

for (const value of [0, -1, 11, 25, 1.5, NaN, Infinity, -Infinity, '3', true]) {
  test(`full-cycle refuses cycles ${String(value)} (${typeof value}) before fetch`, async () => {
    calls.length = 0
    await assert.rejects(
      () => startFullCycle('unit-run', 'cloud', 'local', 5, value),
      /лимит кругов.*1.*10/)
    assert.equal(calls.length, 0)
  })
}

for (const maxRounds of [1, 20]) {
  for (const continueFromStop of [false, true]) {
    test(`exchange preserves rounds=${maxRounds}, resume=${continueFromStop}`, async () => {
      calls.length = 0
      await startExchange('unit/run', 'cloud', 'local', maxRounds, continueFromStop)
      assert.equal(calls.length, 1)
      const [url, options] = calls[0]
      assert.equal(options.method, 'POST')
      const parsed = new URL(url, 'https://unit.invalid')
      assert.equal(parsed.pathname, '/api/runs/unit%2Frun/exchange')
      assert.equal(parsed.searchParams.get('max_rounds'), String(maxRounds))
      assert.equal(parsed.searchParams.get('continue_from_stop'), continueFromStop ? 'true' : null)
    })
  }
  for (const maxMegacycles of [1, 10]) {
    test(`full-cycle preserves rounds=${maxRounds}, cycles=${maxMegacycles}`, async () => {
      calls.length = 0
      await startFullCycle('unit-run', 'cloud', 'local', maxRounds, maxMegacycles)
      assert.equal(calls.length, 1)
      const [url, options] = calls[0]
      assert.equal(options.method, 'POST')
      const parsed = new URL(url, 'https://unit.invalid')
      assert.equal(parsed.pathname, '/api/runs/unit-run/full-cycle')
      assert.equal(parsed.searchParams.get('max_rounds'), String(maxRounds))
      assert.equal(parsed.searchParams.get('max_megacycles'), String(maxMegacycles))
    })
  }
}
