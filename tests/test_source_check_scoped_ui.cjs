// Offline JS execution of actual app functions: fake DOM, deferred API, no browser/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../webapp/static/app.js'), 'utf8');
function node(tag, attrs = {}, children = []) {
  let text = attrs.text || '';
  return Object.assign({tag, children, isConnected: true, classList: {toggle(){}},
    appendChild(n) { this.children.push(n); return n; },
    get textContent() { return text; },
    set textContent(value) { text = value; this.children = []; }
  }, attrs);
}
function all(n) { return [n, ...n.children.flatMap(all)]; }
function make(api) {
  const timers = [], s = {api, Promise, encodeURIComponent, String, Number, Object,
    API: '/api', _flowRunCur: 'run-B', _flowSrcEpoch: 0, _flowSrcStarted: {},
    el: node, t: x => x, toast(){}, setTimeout: fn => timers.push(fn), clearTimeout(){}};
  vm.createContext(s);
  for (const name of ['_flowSrcStamp', '_flowSrcFetch', '_flowSrcStart', '_flowSrcCheckBlock', 'flowActionWait']) {
    const match = source.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'));
    if (match) vm.runInContext(match[0], s);
  }
  return {s, timers};
}
const own = {status: 'done', context_key: 'B-key', context: {task: 'rostering', cosmos_run: 'run-B', solver_run_id: 'uuid-B'},
  counts: {ok: 1}, rows: [{id: 'B_SOURCE', kind: 'rest_min', verdict: 'ok'}]};
const old = {...own, context_key: 'H0-key', context: {task: 'rostering', cosmos_run: 'run-H0'}, rows: [{id: 'H0_SOURCE'}]};
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
const tests = [];
function test(name, fn) { tests.push([name, fn]); }
test('fresh B panel never renders H0 done', async () => {
  const {s} = make(() => Promise.resolve(old)), host = node('host');
  s._flowSrcCheckBlock(host, 'rostering'); await settle();
  assert(!all(host).some(n => n.textContent === 'H0_SOURCE'));
  assert(!all(host).some(n => n.tag === 'a'));
});
test('healthy panel renders B with pinned protocol link', async () => {
  const {s} = make(() => Promise.resolve(own)), host = node('host');
  s._flowSrcCheckBlock(host, 'rostering'); await settle();
  assert(all(host).some(n => n.textContent === 'B_SOURCE'));
  assert(all(host).some(n => n.tag === 'a' && n.href.includes('context_key=B-key')));
});
test('late response after A to B switch cannot render A', async () => {
  let finish;
  const {s} = make(() => new Promise(r => finish = r)), host = node('host');
  s._flowRunCur = 'run-H0'; s._flowSrcCheckBlock(host, 'rostering');
  s._flowRunCur = 'run-B'; s._flowSrcEpoch++; finish(old); await settle();
  assert(!all(host).some(n => n.textContent === 'H0_SOURCE'));
});
test('same-run late response after new attempt epoch is rejected', async () => {
  let finish;
  const {s} = make(() => new Promise(r => finish = r)), host = node('host');
  s._flowSrcCheckBlock(host, 'rostering'); s._flowSrcEpoch++;
  finish(own); await settle();
  assert(!all(host).some(n => n.textContent === 'B_SOURCE'));
});
test('B wait never completes successfully on H0 done', async () => {
  const {s, timers} = make(() => Promise.resolve(old));
  s._flowSrcStarted.rostering = {key: 'B-key', stamp: 'run-B:0'};
  const waiting = s.flowActionWait('roster.src'); timers.shift()();
  assert.equal((await waiting).ok, false);
});
test('healthy wait succeeds and request pins identity', async () => {
  const calls = [], {s, timers} = make(url => {calls.push(url); return Promise.resolve(own);});
  s._flowSrcStarted.rostering = {key: 'B-key', stamp: 'run-B:0'};
  const waiting = s.flowActionWait('roster.src'); timers.shift()();
  assert.equal((await waiting).ok, true);
  assert(calls[0].includes('context_key=B-key'));
});
test('start sends pinned context and exact UUID', async () => {
  const calls = [], {s} = make((url, options) => {
    calls.push([url, options]); return Promise.resolve(own);
  });
  await s._flowSrcStart('rostering');
  const body = JSON.parse(calls[1][1].body);
  assert.equal(body.context_key, 'B-key'); assert.equal(body.solverRunId, 'uuid-B');
  assert.equal(s._flowSrcStarted.rostering.key, 'B-key');
});
test('pairing panel cannot render roster response', async () => {
  const {s} = make(() => Promise.resolve(own)), host = node('host');
  s._flowSrcCheckBlock(host, 'pairing'); await settle();
  assert(!all(host).some(n => n.textContent === 'B_SOURCE'));
});
test('closed panel ignores late response', async () => {
  let finish;
  const {s} = make(() => new Promise(r => finish = r)), host = node('host');
  s._flowSrcCheckBlock(host, 'rostering'); host.children[0].isConnected = false;
  finish(own); await settle();
  assert(!all(host).some(n => n.textContent === 'B_SOURCE'));
});
(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('PASS', name); }
    catch (e) { failed++; console.log('FAIL', name, String(e)); }
  }
  console.log(`${tests.length-failed} passed, ${failed} failed`);
  process.exitCode = failed ? 1 : 0;
})();
