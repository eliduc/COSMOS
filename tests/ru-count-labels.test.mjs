// Exact lesser CDPM finding: Russian variable count captions, not a
// multilingual semantic audit or a translation of the CDPM question UI.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../webapp/static/i18n.js', import.meta.url), 'utf8');
const russian = source.split(/^ru: \{\s*$/m)[1].split(/^\},?\s*$/m)[0];
const dictionary = Object.fromEntries([...russian.matchAll(/^\s*"([^"]+)":\s*(".*")\s*,?\s*$/gm)]
  .map(([, key, literal]) => [key, JSON.parse(literal)]));
// Execute the ACTUAL t/tf bodies; the remaining file is translation data,
// not UI setup, DOM, network, a browser or a reimplementation of tf.
const functions = [...source.matchAll(/^function (?:t|tf)\([^\n]*\) \{[\s\S]*?^\}/gm)];
assert.equal(functions.length, 2);
const context = vm.createContext({I18N: {ru: dictionary}, uiLang: () => 'ru'});
vm.runInContext(functions.map(x => x[0]).join('\n'), context);

const cases = [
  ['app.settings.models.cached', n => `Моделей в кэше: ${n} (обновлено T)`, {at: 'T'}],
  ['app.fs.proposal', n => `План перебросок: ${n} → экономия 7 ч/мес (C); до теоретического минимума останется 2 ч (ограничения легальности).`, {h: 7, cost: 'C', left: 2}],
  ['app.fs.applied', n => `Перебросок применено: ${n}; экономия 7 ч/мес (C).`, {h: 7, cost: 'C'}],
  ['app.cab.legsAc', n => `Плеч: ${n} · A320`, {ac: 'A320'}],
  ['app.cab.vacMonth', n => `Плановый месяц F — T. Квота: 3 чел./день. Число окон — не более ${n}; длительность каждого — до 7 дн.; порядок окон — ваш приоритет.`, {from: 'F', to: 'T', quota: 3, maxd: 7}, 'maxw'],
  ['app.cab.vacTooMany', n => `Максимальное число окон: ${n}.`, {}, 'max'],
  ['app.bases.flag.non_base', n => `Пейрингов с привязкой к станциям вне баз: ${n} (7 ч) — требуется покрытие экипажами.`, {h: 7}],
  ['app.board.truncated', n => `Показано экипажей: 0; всего: ${n} — доска частичная.`, {crew: 0}, 'total'],
  ['app.crs.fromN', n => `исходных предложений: ${n}`, {}],
  ['app.pviz.truncated', n => `Цепочек в ответе сервера: ${n}; карта сети учитывает полный набор.`, {}],
  ['cruns.pass.dirty', n => `рабочее дерево с правками (изменённых путей: ${n})`, {}],
];

for (const [key, expected, extra, countKey = 'n'] of cases) {
  test(`${key}: count-independent grammar preserves numbers and other placeholders`, () => {
    for (const n of [1, 0, 2, 5, 11, 21, 101]) {
      assert.equal(context.tf(key, {...extra, [countKey]: n}), expected(n));
    }
  });
}

test('unrelated interpolation, units, fallback and missing key are unchanged', () => {
  assert.equal(context.tf('absent-{n}', {n: 2}), 'absent-2');
  context.I18N.ru.control = '{value} МБ · {other}';
  assert.equal(context.tf('control', {value: 2, other: 'X'}), '2 МБ · X');
  assert.equal(context.tf('control', {value: 0}), '0 МБ · {other}');
});

test('byte-limit caption preserves both independently varying byte counts', () => {
  for (const [size, max] of [[2, 1], [21, 2], [102, 101], [1001, 1000]]) {
    assert.equal(context.tf('flow.drop.big.bytes', {name: 'a.zip', size, max}),
      `Файл «a.zip»: размер в байтах — ${size}, предел — ${max}; файл больше предела и не отправлен`);
  }
});

test('storyboard count wording preserves all-baselines and fixed measurement', () => {
  for (const n of [1, 2, 3, 5, 11, 21]) {
    assert.equal(context.tf('dc.s4.b727.l1', {n}),
      `стандартных решателей проверено: ${n}; ни один не решил даже LP за 10–20 минут`);
  }
});
