// Reproducible local generation only; no network or product mutation.
import { readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import assert from 'node:assert/strict'
const sources = {
  'confusables-17.0.0.txt': '091C7F82FC39EF208FAF8F94D29C244DE99254675E09DE163160C810D13EF22A',
  'DerivedCoreProperties-17.0.0.txt': '24C7FED1195C482FAAEFD5C1E7EB821C5EE1FB6DE07ECDBAA64B56A99DA22C08',
  'LICENSE.txt': 'E7A93B009565CFCE55919A381437AC4DB883E9DA2126FA28B91D12732BC53D96',
}
function source(name) {
  const raw = readFileSync(new URL(name, import.meta.url))
  assert.equal(createHash('sha256').update(raw).digest('hex').toUpperCase(), sources[name], name)
  return raw.toString('utf8')
}
source('LICENSE.txt')
const mappings = {}
for (const line of source('confusables-17.0.0.txt').split(/\r?\n/)) {
  const body = line.split('#')[0].trim()
  if (!body) continue
  const fields = body.split(';').map(s => s.trim())
  assert.equal(fields.length, 3)
  assert.match(fields[0], /^[0-9A-F]{4,6}$/)
  assert.equal(fields[2], 'MA')
  const cp = parseInt(fields[0], 16)
  assert.equal(mappings[cp], undefined)
  const target = fields[1].split(/\s+/).map(hex => {
    assert.match(hex, /^[0-9A-F]{4,6}$/)
    return String.fromCodePoint(parseInt(hex, 16))
  }).join('')
  mappings[cp] = target
}
const ranges = []
for (const line of source('DerivedCoreProperties-17.0.0.txt').split(/\r?\n/)) {
  const fields = line.split('#')[0].trim().split(';').map(s => s.trim())
  if (fields[1] !== 'Default_Ignorable_Code_Point') continue
  const [start, end = start] = fields[0].split('..').map(s => parseInt(s, 16))
  assert.ok(Number.isInteger(start) && start <= end && end <= 0x10ffff)
  ranges.push([start, end])
}
ranges.sort((a, b) => a[0] - b[0])
const merged = []
for (const [start, end] of ranges) {
  const prev = merged.at(-1)
  if (prev && start <= prev[1] + 1) prev[1] = Math.max(prev[1], end)
  else merged.push([start, end])
}
assert.ok(Object.keys(mappings).length > 6000 && merged.length > 10)
const result = JSON.stringify({
  unicodeVersion: '17.0.0', algorithm: 'UTS39-internalSkeleton',
  sources, mappingCount: Object.keys(mappings).length,
  defaultIgnorableRanges: merged, mappings,
}, null, 2) + '\n'
const target = new URL('confusables.json', import.meta.url)
if (process.argv.includes('--check')) assert.equal(readFileSync(target, 'utf8'), result)
else writeFileSync(target, result)
process.stdout.write('Unicode 17.0.0: ' + Object.keys(mappings).length + ' mappings; reproducible dataset OK\n')
