"""Клиентские подписи контуров автокоррекции (ревью волны 1).

Три MEDIUM-дефекта: интерфейс рендерил СЫРЫЕ ключи i18n вместо человеческих
названий — у карточек CR трёх новых контуров (datahealth/override/runtime),
у среза кластеров "systemic", и у видов дефектов/причин переделок, которые
сервер отдаёт КОДАМИ. Плюс у строки «Здоровье данных» не было кнопки
«Запустить агента», к которой отсылает таблица частых ошибок в мануале.

Проверки идут по двум осям: наличие ключей во ВСЕХ пяти локалях (иначе при
переключении языка вылезет русский или сырой ключ) и поведение клиентских
функций — их гоняем настоящим node в песочнице, чтобы тест ловил регрессию
рендера, а не только словарь.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"
I18N = STATIC / "i18n.js"
APP = STATIC / "app.js"
LANGS = ("ru", "en", "uz", "kk", "ar")

# Коды берём из ИСТОЧНИКА (сервер), а не переписываем списком: добавят вид
# дефекта или причину — тест сразу потребует подписи на пяти языках.
from webapp import app as app_mod, data_health, planner_feedback  # noqa: E402

KIND_CODES = tuple(data_health.KIND_LABELS)
REASON_CODES = tuple(planner_feedback.REASONS)
FACETS = tuple(planner_feedback.FACETS)
LOOP_KEYS = tuple(app_mod.ALL_LOOP_KEYS)
HEAVY_KEYS = tuple(app_mod.HEAVY_LOOP_KEYS)


def _client_loop_keys():
    """Ключи строк из LOOP_ROWS в app.js — что таблица реально рисует."""
    src = APP.read_text(encoding="utf-8")
    m = re.search(r"var LOOP_ROWS = \[(.*?)\n\];", src, re.S)
    assert m, "LOOP_ROWS не найден"
    return tuple(re.findall(r'\{\s*key:\s*"([^"]+)"', m.group(1)))


def _blocks():
    """{lang: {key: value}} — тот же разбор, что и в тесте паритета."""
    out = {lang: {} for lang in LANGS}
    cur = None
    key_re = re.compile(r'^\s*"([^"]+)":\s*"(.*)"\s*,?\s*$')
    open_re = re.compile(r"^(" + "|".join(LANGS) + r"):\s*\{\s*$")
    for ln in I18N.read_text(encoding="utf-8").split("\n"):
        m = open_re.match(ln)
        if m:
            cur = m.group(1)
            continue
        if cur and re.match(r"^\}\s*,?\s*$", ln):
            cur = None
            continue
        if cur:
            km = key_re.match(ln)
            if km:
                out[cur][km.group(1)] = km.group(2)
    return out


def _assert_present(blocks, keys):
    for lang in LANGS:
        for key in keys:
            val = blocks[lang].get(key)
            assert val, f"{lang}: нет подписи для {key}"
            # подпись, равная ключу, — это тот же сырой ключ на экране
            assert val != key, f"{lang}: подпись {key} совпадает с ключом"


def test_cr_agent_names_for_three_new_loops():
    """Карточка CR берёт название контура как app.crs.agent.<agent>."""
    blocks = _blocks()
    _assert_present(blocks, ["app.crs.agent." + a
                             for a in ("datahealth", "override", "runtime")])


def test_cluster_facet_names_cover_every_server_facet():
    """Срез "systemic" существует на сервере с самого начала — подпись
    обязана быть у КАЖДОГО среза, иначе в таблице виден сырой ключ."""
    blocks = _blocks()
    _assert_present(blocks, ["app.loops.ov.f." + f for f in FACETS])


def test_data_health_kind_labels_cover_every_server_kind():
    blocks = _blocks()
    _assert_present(blocks, ["app.loops.dh.k." + k for k in KIND_CODES])


def test_override_reason_labels_cover_every_server_reason():
    blocks = _blocks()
    _assert_present(blocks, ["app.loops.reason." + r for r in REASON_CODES])


def test_table_renders_every_server_loop():
    """Сводка обещает ВСЕ контуры группы «Автоулучшение». Список строк —
    клиентский, и до 2026-07-20 он молча терял «Алгоритмы» и «Базу знаний»
    только потому, что те не пишут в очередь CR."""
    assert _client_loop_keys() == LOOP_KEYS


def test_every_loop_has_all_four_labels_in_five_languages():
    """У строки четыре текста: название, «что наблюдает», метрика и
    подробности. Отсутствие любого = сырой ключ на экране."""
    blocks = _blocks()
    for prefix in ("app.loops.t.", "app.loops.o.", "app.loops.m.",
                   "app.loops.d."):
        _assert_present(blocks, [prefix + k for k in LOOP_KEYS])


def test_heavy_loops_navigate_instead_of_running_an_agent():
    """У «Алгоритмов» и «Базы знаний» нет одноимённого CR-агента: кнопка
    действия обязана уводить на их вкладку, а не звать loopRunAgent."""
    src = APP.read_text(encoding="utf-8")
    m = re.search(r"var LOOP_OWN_TAB = \{(.*?)\};", src, re.S)
    assert m, "LOOP_OWN_TAB не найден"
    mapped = dict(re.findall(r'(\w+):\s*"([^"]+)"', m.group(1)))
    assert tuple(mapped) == HEAVY_KEYS
    for key, tab in mapped.items():
        assert tab == key, "ключ строки = ключ вкладки"
    _assert_present(_blocks(), ["app.loops.open." + k for k in HEAVY_KEYS])
    body = _grab(src, "loopActionButton")
    assert body.index("LOOP_OWN_TAB[key]") < body.index('key === "datahealth"')


def test_data_health_row_offers_both_snapshot_and_agent_run():
    """У «Здоровья данных» две кнопки: снимок И запуск агента (мануал в
    таблице частых ошибок отсылает именно к «Запустить агента»)."""
    src = APP.read_text(encoding="utf-8")
    m = re.search(r"function loopActionButton\(key\) \{(.*?)\n\}\n", src,
                  re.S)
    assert m, "loopActionButton не найдена"
    dh = m.group(1).split('if (key === "audit")')[0]
    assert 't("app.loops.snapshot")' in dh
    assert 't("app.loops.run")' in dh, "нет кнопки «Запустить агента»"
    assert 'loopRunAgent("datahealth"' in dh


def test_detail_tables_translate_codes_not_server_labels():
    """Подписи видов/причин НЕ берутся напрямую из серверных карт: иначе
    интерфейс говорил бы по-русски при любом выбранном языке."""
    src = APP.read_text(encoding="utf-8")
    assert "(d.kinds || {})[k] || k" not in src
    assert "(d.reasons || {})[c.reason] || c.reason" not in src
    assert 'loopCodeLabel("app.loops.dh.k."' in src
    assert 'loopCodeLabel("app.loops.reason."' in src


# ---------------------------------------------------------------- node-часть

def _grab(src, name):
    """Вырезать функцию `name` из app.js по балансу фигурных скобок."""
    start = src.index("function " + name + "(")
    depth = 0
    i = src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("не закрыта функция " + name)


_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const [i18nPath, helpers, lang] = process.argv.slice(2);
const sandbox = {
  console,
  localStorage: { getItem: () => lang, setItem: () => {} },
  document: { addEventListener() {}, querySelectorAll: () => [],
              dispatchEvent() {}, documentElement: { setAttribute() {} } }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(i18nPath, 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(helpers, 'utf8'), sandbox);
const res = vm.runInContext(JSON.parse(fs.readFileSync(process.env.CREW_Q,
                                                       'utf8')), sandbox);
process.stdout.write(JSON.stringify(res));
"""


def _node(tmp_path, expr, lang="en"):
    node = shutil.which("node")
    if not node:
        pytest.skip("node недоступен")
    src = APP.read_text(encoding="utf-8")
    helpers = tmp_path / "helpers.js"
    helpers.write_text("\n".join(_grab(src, n) for n in
                                 ("loopCodeLabel", "loopFacetLabel")),
                       encoding="utf-8")
    runner = tmp_path / "run.js"
    runner.write_text(_HARNESS, encoding="utf-8")
    q = tmp_path / "q.json"
    q.write_text(json.dumps(expr), encoding="utf-8")
    out = subprocess.run(
        [node, str(runner), str(I18N), str(helpers), lang],
        capture_output=True, text=True, encoding="utf-8",
        env={**__import__("os").environ, "CREW_Q": str(q)}, check=True)
    return json.loads(out.stdout)


def test_node_kind_labels_are_localised(tmp_path):
    """Английский интерфейс не должен показывать ни код, ни русскую
    подпись сервера: до правки в ячейку клали серверную строку как есть."""
    expr = ("[" + ",".join(
        'loopCodeLabel("app.loops.dh.k.", %r, {%r: "нарушения формата"})'
        % (k, k) for k in KIND_CODES).replace("'", '"') + "]")
    labels = _node(tmp_path, expr, lang="en")
    assert len(labels) == len(KIND_CODES)
    for code, label in zip(KIND_CODES, labels):
        assert label != code, f"{code}: на экране сырой код"
        assert not re.search(r"[А-Яа-я]", label), (
            f"{code}: в EN-интерфейсе русская подпись сервера")


def test_node_reason_labels_are_localised(tmp_path):
    expr = ("[" + ",".join(
        'loopCodeLabel("app.loops.reason.", %r, null)' % (r,)
        for r in REASON_CODES).replace("'", '"') + "]")
    labels = _node(tmp_path, expr, lang="en")
    for code, label in zip(REASON_CODES, labels):
        assert label != code
        assert not re.search(r"[А-Яа-я]", label)


def test_node_unknown_kind_falls_back_to_code(tmp_path):
    """Вид, добавленный на сервере раньше клиента, не должен исчезать."""
    got = _node(tmp_path,
                'loopCodeLabel("app.loops.dh.k.", "brand_new_kind", null)')
    assert got == "brand_new_kind"


def test_node_systemic_facet_has_no_dangling_colon(tmp_path):
    """У среза "systemic" значение пустое по построению: «Systemic: » с
    висящим двоеточием — тот же дефект рендера."""
    got = _node(tmp_path,
                '[loopFacetLabel({facet: "systemic", value: ""}),'
                ' loopFacetLabel({facet: "employee", value: "1042"})]',
                lang="en")
    assert got[0] and "app.loops" not in got[0], "сырой ключ среза"
    assert not got[0].endswith(":") and ": " not in got[0]
    assert got[1].endswith(": 1042")
