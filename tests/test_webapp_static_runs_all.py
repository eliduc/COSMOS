# -*- coding: utf-8 -*-
"""Отбор прогонов и общий экран «Все прогоны» обеих систем в статике
COSMOS (слово владельца 11.09.2026; серверная половина —
`webapp/run_tree_api.py` и `webapp/runs_all_api.py`).

Стенд судит ТЕКСТ файлов статики и исполняет её рисовальщики настоящим
node в песочнице (образец tests/test_webapp_static_run_tree.py):
браузера здесь нет, живых служб — тоже.

  1. Полоса отбора ОДНА на оба места — блок «Прогоны дерева» и общий
     экран; имена параметров, площадки, состояния и виды объявившего
     взяты у сервера (`webapp/run_tree_api.py`, `vmp/run_record.py`):
     разойдись перечни — интерфейс просил бы то, чего ручка не знает.
  2. Отбирает СЕРВЕР: статика строит строку запроса и зовёт те же ручки
     (`/run-tree`, `/runs-all`); своего отбора списка в браузере нет.
  3. Выбранный отбор запоминается в браузере, кнопка «Сбросить» чистит
     все поля.
  4. Общий экран стоит подразделом вкладки «Прогоны» рядом со списком
     прогонов COSMOS; столбцы названы, слова сервера (недоступная
     панель) показываются, «показать ещё» приходит числом с сервера.
  5. Ключи `flow.runs.*`, `flow.vmp.tree.index*` и `cruns.all.*` есть во
     всех пяти словарях с одинаковыми подстановками; шаг тура о вкладке
     «Прогоны» называет новый подраздел.
  6. Классы оформления объявлены в style.css.

Литералов среды в файле нет: ни адреса площадки, ни номера живого
прогона; служб не поднимается.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_i18n_parity import LANGS, _blocks            # noqa: E402
from tests.test_webapp_static_run_tree import (_const, _fn,  # noqa: E402
                                               _grab, _src, _values)
from vmp import run_record as rr                             # noqa: E402

STATIC = HERE / "webapp" / "static"
INDEX = STATIC / "index.html"
STYLE = STATIC / "style.css"
I18N = STATIC / "i18n.js"

PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Ключи отбора и общего экрана — явный перечень: контракт экрана.
PROJECT_KEYS = {
    "flow.runs.filters", "flow.runs.filters.title", "flow.runs.f.customer",
    "flow.runs.f.customer.ph", "flow.runs.f.created", "flow.runs.f.since",
    "flow.runs.f.until", "flow.runs.f.root", "flow.runs.f.root.ph",
    "flow.runs.f.q", "flow.runs.f.q.ph", "flow.runs.f.any",
    "flow.runs.f.reset", "flow.runs.f.on", "flow.runs.golden.yes",
    "flow.runs.golden.no", "flow.runs.by.none",
    "flow.vmp.tree.index", "flow.vmp.tree.index.title",
    "flow.vmp.tree.index.fail", "flow.vmp.tree.index.none",
    "flow.vmp.tree.index.counts", "flow.vmp.tree.index.root",
    "flow.vmp.tree.index.orphans",
    "cruns.view.runs", "cruns.view.all", "cruns.all.h", "cruns.all.sub",
    "cruns.all.loading", "cruns.all.fail", "cruns.all.none",
    "cruns.all.empty", "cruns.all.counts", "cruns.all.more",
    "cruns.all.a.cdpm", "cruns.all.a.cosmos", "cruns.all.a.systems",
    "cruns.all.link.cdpm", "cruns.all.link.cosmos", "cruns.all.src.cdpm",
}

PLACEHOLDERS = {
    "flow.runs.f.on": {"n"},
    "flow.vmp.tree.index.counts": {"roots", "runs"},
    "flow.vmp.tree.index.root": {"root", "airline"},
    "cruns.all.counts": {"shown", "total"},
}


def _js_list(name: str) -> list:
    """Перечень строк из объявления `var <name> = [...]` в app.js."""
    return re.findall(r'"([^"]*)"', _const(_src(), name))


# ------------------------------------------------- словари сервера
def test_filter_parameters_are_the_ones_the_server_knows():
    """Имена параметров отбора взяты у ручки: разойдись перечни —
    интерфейс просил бы то, чего сервер не знает."""
    from webapp import run_tree_api as rta
    assert _js_list("RUN_FILTER_FIELDS") == list(rta.FILTER_PARAMS)
    assert _js_list("RUN_FILTER_DECLARED") == list(rta.DECLARED_BY_KINDS)


def test_contours_and_states_are_the_words_of_the_record_dictionary():
    """Площадки и состояния — слова словаря записи (`vmp/run_record.py`):
    их же принимает сервер, а человеку они показываются переводом."""
    assert _js_list("RUN_FILTER_CONTOURS") == list(rr.CONTOURS)
    assert _js_list("RUN_FILTER_STATES") == list(rr.STATES)


# ------------------------------------------------------ одно место отбора
def test_the_filter_is_applied_by_the_server_only():
    """Статика строит СТРОКУ ЗАПРОСА и зовёт те же ручки; своего отбора
    списка в браузере нет — второй разбор тех же слов разошёлся бы с
    первым на первой же правке."""
    source = _src()
    query = _grab(source, "runFiltersQuery")
    assert "RUN_FILTER_FIELDS.forEach" in query
    assert "encodeURIComponent" in query
    # пустое поле не шлётся вовсе: «все» и «параметра нет» для сервера
    # одно и то же
    assert "if (v) {" in query
    index = _fn(source, "runTreeIndexBlock")
    assert 'api("/run-tree" + runFiltersQuery(f))' in index
    all_block = _fn(source, "refreshRunsAll")
    assert 'api("/runs-all" + runFiltersQuery(_runsAll.filters, extra))' \
        in all_block
    # рисовальщики берут строки КАК ЕСТЬ: ни одного .filter( по строкам
    for name in ("_runTreeIndexDraw", "renderRunsAll"):
        body = _grab(source, name)
        assert ".filter(" not in body, name


def test_the_chosen_filter_is_remembered_in_the_browser():
    source = _src()
    assert 'var RUN_FILTER_KEY = "cosmos.runs.filters.v1";' in source
    load = _grab(source, "runFiltersLoad")
    save = _grab(source, "runFiltersSave")
    assert "localStorage.getItem(RUN_FILTER_KEY)" in load
    assert "localStorage.setItem(RUN_FILTER_KEY" in save
    # хранилище недоступно — экран не рушится
    assert "catch (e)" in load and "catch (e)" in save
    bar = _grab(source, "runFiltersBar")
    assert 'text: t("flow.runs.f.reset")' in bar
    assert "RUN_FILTER_FIELDS.forEach(function (k) { f[k] = \"\"; });" in bar
    assert "runFiltersSave(f);" in bar


# --------------------------------------------------- блок «Прогоны дерева»
def test_the_tree_index_block_stands_in_both_places():
    """Блок с отбором стоит там же, где секция дерева: на панели
    постановки и на карточке прогона — один рисовальщик."""
    source = _src()
    panel = _fn(source, "vmpTreeBlock")
    assert "runTreeIndexBlock(box)" in panel
    card = _fn(source, "_crunsRenderTree")
    assert "runTreeIndexBlock(host)" in card
    draw = _grab(source, "_runTreeIndexDraw")
    assert "runFiltersBar(body, f, reload)" in draw
    # пусто по отбору и пусто вовсе — РАЗНЫЕ слова
    assert '"flow.vmp.tree.index.none"' in draw
    assert '"flow.vmp.tree.empty"' in draw
    assert "runFiltersCount(f)" in draw


def test_the_tree_index_row_reads_the_head_of_the_record():
    row = _grab(_src(), "_runTreeIndexRow")
    for field in ("node.run_id", "node.airline", "node.contour",
                  "node.created_at", "node.state", "node.created_by"):
        assert field in row, field
    assert "_runTreeGoldenMark(node)" in row


# ------------------------------------------------ подраздел «Все прогоны»
def test_the_subsection_stands_next_to_the_list_of_cosmos_runs():
    html = INDEX.read_text(encoding="utf-8")
    panel = html[html.index('id="panel-cruns"'):]
    panel = panel[:panel.index("</section>")]
    for marker in ('id="cruns-views"', 'id="btn-cruns-view-runs"',
                   'id="btn-cruns-view-all"', 'id="runs-all"',
                   'data-i18n="cruns.view.runs"', 'data-i18n="cruns.view.all"'):
        assert marker in panel, marker
    assert panel.index('id="cruns-views"') < panel.index('id="cruns-list"')
    source = _src()
    show = _grab(source, "crunsShowView")
    assert "CRUNS_LIST_NODES.forEach" in show
    assert 'refreshRunsAll()' in show and "renderCosmosRuns()" in show
    init = _fn(source, "_crunsInit")
    assert 'on("btn-cruns-view-runs", "click"' in init
    assert 'on("btn-cruns-view-all", "click"' in init


def test_the_common_screen_names_its_columns_and_the_words_of_the_server():
    source = _src()
    render = _grab(source, "renderRunsAll")
    for key in ("cruns.all.h", "cruns.all.sub", "cruns.all.a.cdpm",
                "cruns.all.a.cosmos", "cruns.all.a.systems",
                "cruns.all.counts", "cruns.all.more", "cruns.all.fail"):
        assert '"%s"' % key in render, key
    # слова сервера (недоступная панель) показываются, а не проглатываются
    assert "(doc.words || [])" in render
    # пусто по отбору и пусто вовсе — разные слова
    assert '"cruns.all.none"' in render and '"cruns.all.empty"' in render
    # «показать ещё» приходит числом с сервера, своего счёта нет
    assert "doc.total" in render and "items.length < total" in render


def test_the_row_of_the_common_screen_links_to_both_systems():
    source = _src()
    row = _grab(source, "_runsAllRow")
    for field in ("item.record_id", "item.customer", "item.contour",
                  "item.declared_by", "item.created_at", "item.state",
                  "item.golden"):
        assert field in row, field
    cdpm = _grab(source, "_runsAllCdpmCell")
    assert "item.cdpm_url" in cdpm and "_crunsLink(" in cdpm
    cosmos = _grab(source, "_runsAllCosmosCell")
    assert "openCosmosRunCard(item.cosmos_run)" in cosmos
    # удаления и правок на общем экране нет: только чтение
    for name in ("renderRunsAll", "_runsAllRow", "refreshRunsAll"):
        body = _grab(source, name)
        assert 'method: "POST"' not in body and 'method: "PUT"' not in body
        assert 'method: "DELETE"' not in body, name


# ----------------------------------------------------------------- слова
def test_keys_stand_in_five_languages_with_the_same_placeholders():
    blocks = _blocks()
    for lang in LANGS:
        vals = _values(blocks, lang)
        missing = sorted(k for k in PROJECT_KEYS if k not in vals)
        assert not missing, "%s: нет ключей %s" % (lang, missing)
        for key, names in PLACEHOLDERS.items():
            assert set(PLACEHOLDER.findall(vals[key])) == names, (lang, key)
        # слова видов объявившего — по коду сервера
        from webapp import run_tree_api as rta
        for kind in rta.DECLARED_BY_KINDS:
            assert "flow.runs.by." + kind in vals, (lang, kind)


def test_every_used_key_of_the_screen_stands_in_the_russian_block():
    used = set(re.findall(
        r'\bt[f]?\("((?:flow\.runs|flow\.vmp\.tree\.index|cruns\.all|'
        r'cruns\.view)[\w.\-]*)"', _src()))
    ru = set(_blocks()["ru"])
    assert used, "экран не зовёт ни одного своего ключа"
    # ключи, собираемые из кода сервера (`t("flow.runs.by." + kind)`),
    # судятся перечнем видов выше, а не этой строкой
    assert not sorted(k for k in used if not k.endswith(".") and k not in ru)


def test_tour_names_the_new_subsection():
    """Чек-лист синхронизации витрин, пункт «тур»: шаг о вкладке
    «Прогоны» называет подраздел обеих систем — одной фразой, без
    нового шага."""
    blocks = _blocks()
    for lang in LANGS:
        text = _values(blocks, lang)["tour.gcruns.p2"]
        assert "CDPM" in text, lang
        # новый подраздел назван словом своего же словаря экрана
        name = _values(blocks, lang)["cruns.view.all"]
        assert name in text, (lang, name)


def test_style_declares_the_classes_of_the_filter_and_of_the_screen():
    css = STYLE.read_text(encoding="utf-8")
    for cls in (".run-filters", ".run-filters-h", ".run-filters-on",
                ".run-filter-field", ".run-filter-input", ".run-filter-reset",
                ".run-tree-index-box", ".run-tree-index", ".cruns-views",
                ".cruns-off", ".runs-all", ".runs-all-table",
                ".runs-all-more"):
        assert cls in css, cls
    tail = css[css.index("/* Отбор прогонов"):]
    assert "padding-left" not in tail and "margin-left" not in tail


# ------------------------------------------------------------------ node
# Чистые функции экрана исполняются настоящим node в песочнице: строка
# запроса, счёт полей отбора, вид объявившего и рисование строки общего
# экрана — по разу на каждый из пяти языков.

_CONSTS = ("RUN_REC_PURPOSES", "RUN_REC_OUTCOMES", "RUN_REC_STATES",
           "RUN_REC_OWNERS", "RUN_FILTER_KEY", "RUN_FILTER_FIELDS",
           "RUN_FILTER_CONTOURS", "RUN_FILTER_STATES", "RUN_FILTER_DECLARED")

_FUNCS = ("el", "fmt", "localeTag", "fmtTime", "_crunsErrWords",
          "_crunsLink", "_runRecWords", "_runRecStateWords",
          "_runRecKeyWords", "_runTreeGoldenMark", "runFiltersEmpty",
          "runFiltersLoad", "runFiltersSave", "runFiltersCount",
          "runFiltersQuery", "_runFilterByWords", "_runFilterField",
          "_runFilterSelect", "_runFilterText", "runFiltersBar",
          "_runTreeDeclaredBy", "_runTreeIndexRow", "_runTreeIndexTable",
          "_runTreeIndexDraw", "_runsAllCosmosCell", "_runsAllCdpmCell",
          "_runsAllSystems", "_runsAllRow")

_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const [i18nPath, helpers, lang] = process.argv.slice(2);
function node(tag) {
  return {
    tag: tag, children: [], attrs: {}, classList: { add() {}, remove() {},
      toggle() {} },
    set textContent(v) { this._text = v; this.children.length = 0; },
    get textContent() { return this._text || ''; },
    appendChild(c) {
      if (!c) { throw new Error('appendChild(null) в узле ' + tag); }
      this.children.push(c); return c;
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {}, click() {}, remove() {},
    scrollIntoView() {}, focus() {}, select() {}
  };
}
const store = {};
const sandbox = {
  console, setTimeout, clearTimeout,
  localStorage: {
    getItem: (k) => (k === 'cosmos.lang' ? lang : (store[k] || null)),
    setItem: (k, v) => { store[k] = v; }
  },
  document: {
    createElement: node, createTextNode: (t) => ({ textContent: t }),
    addEventListener() {}, querySelectorAll: () => [], querySelector: () => null,
    dispatchEvent() {}, documentElement: { setAttribute() {} }, body: node('body')
  },
  api: function () { return Promise.resolve({ ok: true }); },
  toast: function () {},
  openCosmosRunCard: function () {}
};
sandbox.globalThis = sandbox; sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(i18nPath, 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(helpers, 'utf8'), sandbox);
function walk(n, out) {
  if (n && n.textContent) { out.push(String(n.textContent)); }
  (n && n.children || []).forEach(function (c) { walk(c, out); });
  return out;
}
const q = JSON.parse(fs.readFileSync(process.env.CREW_Q, 'utf8'));
const out = {};
out.query_empty = sandbox.runFiltersQuery(sandbox.runFiltersEmpty(), {});
out.query = sandbox.runFiltersQuery(q.filters, { limit: '7' });
out.count = sandbox.runFiltersCount(q.filters);
out.by = sandbox.RUN_FILTER_DECLARED.map(sandbox._runFilterByWords)
  .concat([sandbox._runFilterByWords(null)]);
try {
  const host = node('div');
  sandbox.runFiltersBar(host, q.filters, function () {});
  out.bar = walk(host, []).filter(Boolean);
  out.saved = store['cosmos.runs.filters.v1'] || null;
} catch (e) { out.bar = { error: String(e && e.stack || e) }; }
try {
  const host = node('div');
  sandbox._runTreeIndexDraw(host, q.tree, sandbox.runFiltersEmpty(),
                            function () {});
  out.index = walk(host, []).filter(Boolean);
} catch (e) { out.index = { error: String(e && e.stack || e) }; }
try {
  const host = node('div');
  q.items.forEach(function (i) { host.appendChild(sandbox._runsAllRow(i)); });
  out.rows = walk(host, []).filter(Boolean);
} catch (e) { out.rows = { error: String(e && e.stack || e) }; }
process.stdout.write(JSON.stringify(out));
"""


def _cases() -> dict:
    """Случаи для песочницы: отбор, кусок индекса и строки экрана.
    Номера, отпечатки и адреса — вымышленные."""
    return {
        "filters": {"customer": "Проба", "contour": rr.CONTOURS[3],
                    "state": rr.STATE_DONE, "declared_by": "runner",
                    "golden": "1", "since": "2026-09-01", "until": "",
                    "root": "1" * 12, "q": "зап"},
        "tree": {
            "roots": [{"root_sha256": "1" * 64, "airline": "а/к",
                       "runs": [{"run_id": "зап-1", "airline": "а/к",
                                 "contour": rr.CONTOURS[0],
                                 "created_by": "раннер наборов пробы",
                                 "created_at": "2026-09-10T09:00:00+03:00",
                                 "state": rr.STATE_DONE,
                                 "golden": {"confirmed": True}}]}],
            "orphans": [], "refusals": []},
        "items": [
            {"record_id": "зап-1", "cdpm_run": "прогон-панели",
             "cosmos_run": "прогон-косм", "customer": "а/к",
             "contour": rr.CONTOURS[3], "declared_by": "panel",
             "created_by": "панель CDPM",
             "created_at": "2026-09-10T09:00:00+03:00",
             "state": rr.STATE_DONE, "systems": list(rr.SYSTEMS),
             "golden": True, "source": "cosmos", "cdpm_url": None},
            {"record_id": None, "cdpm_run": "прогон-только-панели",
             "cosmos_run": None, "customer": "а/к-другая", "contour": None,
             "declared_by": None, "created_by": None,
             "created_at": "2026-09-09T09:00:00+03:00",
             "state": "оформление", "systems": ["cdpm"], "golden": False,
             "source": "cdpm", "cdpm_url": None}],
    }


def _run_node(tmp_path, lang: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node недоступен")
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = _src()
    helpers = tmp_path / "runs-all-helpers.js"
    helpers.write_text(
        "\n".join([_const(src, c) for c in _CONSTS] +
                  [_grab(src, n) for n in _FUNCS]), encoding="utf-8")
    runner = tmp_path / "runs-all.js"
    runner.write_text(_HARNESS, encoding="utf-8")
    q = tmp_path / "cases.json"
    q.write_text(json.dumps(_cases(), ensure_ascii=False), encoding="utf-8")
    res = subprocess.run(
        [node_bin, str(runner), str(I18N), str(helpers), lang],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CREW_Q": str(q)}, check=True)
    return json.loads(res.stdout)


def test_node_builds_the_query_and_draws_the_rows(tmp_path):
    """Строка запроса, счёт полей, вид объявившего и строки экрана — на
    настоящем node, по разу на каждый из пяти языков."""
    from webapp import run_tree_api as rta
    for lang in LANGS:
        got = _run_node(tmp_path / lang, lang)
        assert got["query_empty"] == "", "пустой отбор шлёт параметры"
        assert got["query"].startswith("?")
        pairs = dict(p.split("=", 1) for p in got["query"][1:].split("&"))
        # пустое поле не ушло вовсе, чужое ничего не прибавилось
        assert "until" not in pairs, lang
        assert set(pairs) - {"limit"} <= set(rta.FILTER_PARAMS), lang
        assert pairs["contour"] == rr.CONTOURS[3] and pairs["limit"] == "7"
        assert got["count"] == 8, lang
        assert len(set(got["by"])) == 4, (lang, got["by"])
        assert not isinstance(got["bar"], dict), got["bar"]
        assert got["saved"] is None, "полоса отбора пишет в хранилище сама"
        for words in ("index", "rows"):
            assert not isinstance(got[words], dict), (lang, got[words])
        joined = " ".join(got["index"])
        assert "зап-1" in joined and "а/к" in joined
        rows = " ".join(got["rows"])
        assert "прогон-панели" in rows and "прогон-косм" in rows
        assert "прогон-только-панели" in rows
        # состояние, которого словарь записи не знает, показано КАК ЕСТЬ
        assert "оформление" in rows, lang
