# -*- coding: utf-8 -*-
"""Вид «Каталог» вкладки «Изменения» и ссылки в службу каталога на
экране «База шаблонов» (шаг 6 каталога, дорожка B; ссылки — дорожка D
шага 5 §6.2). Три слоя, образец tests/test_webapp_static_cosmos_runs.py:

  статика: перечень видов, скрытая кнопка запуска агента, формы адресов
    службы (`/ui/#/requests/<id>`, `/ui/#/records/…`, `/ui/#/executors…`,
    `/ui/#/versions/<v>`), снятая ручка заявки на правку;
  ключи: динамические ключи (`t("app.crs.desc." + view)`,
    `t("app.crs.catalog.th." + k)`, `t("flow.gap.form." + form)`) гейт
    паритета НЕ видит — здесь они названы поимённо и проверены во всех
    пяти языках;
  node-стенд: настоящий node исполняет `crsInView` и `crCatalogBlocked`
    — отбор строк вида и ПРИЧИНА, по которой гаснет «Реализовать на
    Тест».
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tests.test_i18n_parity import LANGS, _blocks           # noqa: E402

STATIC = HERE / "webapp" / "static"
APP_JS = STATIC / "app.js"
#: Адрес службы каталога для стендов — вымышленный (зона `.invalid`
#: закреплена RFC 6761 и не разрешается никогда). Стенд судит
#: ФОРМУЛУ ссылки: адрес кладёт в состояние сам тест и из него же
#: выводит ожидание — живого порта здесь быть не должно.
SERVICE_URL = "http://catalog.invalid"

#: Ключи вида «Каталог» и ссылок в службу — поимённо (динамические
#: обращения гейт паритета не видит).
KEYS = [
    "app.crs.view.catalog", "app.crs.desc.catalog",
    "app.crs.agent.catalog-executor", "app.crs.agent.vmp-gap",
    "app.crs.agent.template-base",
    "app.crs.catalog.h", "app.crs.catalog.sync", "app.crs.catalog.deliver",
    "app.crs.catalog.none", "app.crs.catalog.noService",
    "app.crs.catalog.otherCatalog", "app.crs.catalog.keyNotWritten",
    "app.crs.catalog.manualOnly", "app.crs.catalog.outbox",
    "app.crs.catalog.auto", "app.crs.catalog.record",
    "app.crs.catalog.candidate", "app.crs.catalog.gates",
    "app.crs.catalog.synced", "app.crs.catalog.syncFail",
    "app.crs.catalog.delivered",
    "app.crs.catalog.th.request", "app.crs.catalog.th.state",
    "app.crs.catalog.th.record", "app.crs.catalog.th.who",
    "app.crs.catalog.th.source", "app.crs.catalog.th.kinds",
    "app.crs.catalog.th.crKey", "app.crs.catalog.th.crStatus",
    "flow.gap.h", "flow.gap.th.rule", "flow.gap.th.gap",
    "flow.gap.th.form", "flow.gap.th.request", "flow.gap.th.state",
    "flow.gap.form.kind", "flow.gap.form.new", "flow.gap.form.edit",
    "flow.gap.form.defect", "flow.gap.requested", "flow.gap.submit",
    "flow.gap.link", "flow.gap.auto.on", "flow.gap.auto.off",
    "app.tpl.catalogOpen", "app.tpl.catalogNotConfigured",
    "app.tpl.catalogRecord", "app.tpl.catalogExecutors",
    "app.tpl.requestInCatalog", "app.tpl.editInService",
    "app.tpl.removeInService", "app.tpl.requestsLine",
]


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ------------------------------------------------------------- статика

def test_вид_каталога_в_перечне_видов_и_без_кнопки_агента():
    src = _src()
    m = re.search(r"var CRS_VIEWS = \[([^\]]+)\]", src)
    assert m, "перечень видов вкладки «Изменения» не найден"
    views = [v.strip().strip('"') for v in m.group(1).split(",")]
    assert "catalog" in views and views[-1] == "all", views
    m2 = re.search(r"var CATALOG_AGENTS = \[([^\]]+)\]", src)
    agents = [v.strip().strip('"') for v in m2.group(1).split(",")]
    assert agents == ["catalog-executor", "vmp-gap", "template-base"]
    # кнопка «Запустить агента» скрыта: строку CR каталога рождает
    # решение владельца по заявке, а не запуск агента-предложения
    assert 'crsView === "catalog"' in src
    hide = re.search(r'\$\("btn-crs-run"\)\.classList\.toggle\('
                     r'\s*"hidden",([^;]+);', src)
    assert hide and '"catalog"' in hide.group(1), hide
    print(f"видов вкладки рассмотрено: {len(views)}, агентов каталога: "
          f"{len(agents)}")


def test_адреса_службы_каталога_по_контракту():
    """Формы адресов — §6.1 шага 5 и контракт службы: карточка заявки,
    карточка записи, таблица исполнителей, карточка версии."""
    src = _src()
    for form in ('"/ui/#/records/"',
                 '"/ui/#/executors?product=COSMOS&section="',
                 '"/ui/#/versions/"', '"/ui/#/requests?section="',
                 '"/ui/#/requests/new?section=families&record_id="'):
        assert form in src, form
    # Адрес карточки заявки строит сервер (одна формула на ручку вида и
    # на экран базы шаблонов) — статика получает готовую ссылку.
    py = (HERE / "webapp" / "catalog_pipeline.py").read_text(
        encoding="utf-8")
    assert '"/ui/#/requests/"' in py
    assert "/ui/#/records/" in (HERE / "webapp" / "template_base.py"
                                ).read_text(encoding="utf-8")
    # без адреса службы ссылок нет: экран говорит словами, а не ведёт
    # в никуда
    assert "app.tpl.catalogNotConfigured" in src
    assert "dead.disabled = true" in src


def test_заявка_на_правку_снята_с_экрана():
    """Правка и снятие записи — в службе каталога (0.8): прежней ручки
    очереди на экране больше нет."""
    src = _src()
    assert "submitTemplateChange" not in src
    assert '"/template-base/change"' not in src
    assert "app.tpl.editInService" in src and "app.tpl.removeInService" in src


def test_карта_цветов_состояний_одна_на_три_панели():
    """Контракт службы §6: «заявлен» — жёлтый, «вид никем не объявлен» —
    синий (это сведение, а не тревога)."""
    src = _src()
    m = re.search(r"var TPL_STATE_CHIP = \{([^}]+)\}", src)
    assert m
    assert '"заявлен": "chip-warn"' in m.group(1).replace("\n", " ")
    assert 'TPL_STATE_CHIP[best]\n                                        '
    assert '"chip-info"' in src


def test_ключи_вида_каталога_есть_во_всех_пяти_языках():
    blocks = _blocks()
    missing = {lang: sorted(k for k in KEYS if k not in blocks[lang])
               for lang in LANGS}
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, missing
    print(f"ключей проверено: {len(KEYS)} × {len(LANGS)} языков")


def test_подстановки_ключей_каталога_совпадают_с_кодом():
    """Что подставляет код — то и объявляет русский словарь."""
    blocks = _blocks()["ru"]
    expect = {
        "app.crs.catalog.outbox": {"n", "p"},
        "app.crs.catalog.auto": {"v"},
        "app.crs.catalog.record": {"r", "q", "f", "k"},
        "app.crs.catalog.candidate": {"p", "w"},
        "app.crs.catalog.synced": {"o", "k", "r"},
        "app.crs.catalog.delivered": {"n"},
        "app.crs.catalog.gates": {"paths", "files", "chain", "tests",
                                  "stand", "bindings", "validate"},
        "app.tpl.requestsLine": {"n", "s", "k"},
        "flow.gap.requested": {"q", "s"},
    }
    for key, want in expect.items():
        got = set(re.findall(r"\{(\w+)\}", blocks[key]))
        assert got == want, (key, got, want)
    print(f"ключей с подстановками проверено: {len(expect)}")


# --------------------------------------------------------- node-стенд

_HARNESS = r"""
var fs = require("fs");
var vm = require("vm");
var src = fs.readFileSync(process.argv[2], "utf8");
var sandbox = {
  t: function (k) { return k; },
  catalogState: { data: null },
  console: console, JSON: JSON
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
var cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
var out = {};
sandbox.catalogState.data = cases.state;
out.in_catalog = cases.rows.map(function (r) {
  return sandbox.crsInView(r, "catalog");
});
out.in_feedback = cases.rows.map(function (r) {
  return sandbox.crsInView(r, "feedback");
});
out.in_all = cases.rows.map(function (r) {
  return sandbox.crsInView(r, "all");
});
out.blocked = cases.crs.map(function (c) {
  return sandbox.crCatalogBlocked(c);
});
process.stdout.write(JSON.stringify(out));
"""


def _grab(src: str, name: str) -> str:
    """Тело функции верхнего уровня по имени (до строки «}» в нулевой
    колонке) — тот же приём, что в стенде экрана «Прогоны»."""
    m = re.search(r"^function " + re.escape(name) + r"\([^\n]*\n",
                  src, re.M)
    assert m, name
    end = src.index("\n}\n", m.start())
    return src[m.start():end + 3]


def test_node_отбор_строк_вида_и_причина_гашения_кнопки(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node недоступен")
    src = _src()
    m = re.search(r"var CATALOG_AGENTS = \[[^\]]+\];", src)
    helpers = tmp_path / "helpers.js"
    helpers.write_text("\n".join([m.group(0),
                                  _grab(src, "crsInView"),
                                  _grab(src, "crCatalogBlocked")]),
                       encoding="utf-8")
    runner = tmp_path / "run.js"
    runner.write_text(_HARNESS, encoding="utf-8")
    cases = {
        "rows": [{"agent": "catalog-executor"}, {"agent": "vmp-gap"},
                 {"agent": "template-base"}, {"agent": "feedback"}],
        "crs": [
            {"kind": "catalog-executor",
             "spec": {"executors_requested": [{"kind": "check_code"}]}},
            {"kind": "catalog-executor",
             "spec": {"executors_requested": []}},
            {"kind": "feedback"},
        ],
        "state": {"service": {"same_catalog": True}},
    }
    q = tmp_path / "cases.json"
    q.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run([node, str(runner), str(helpers), str(q)],
                         capture_output=True, text=True, encoding="utf-8",
                         check=True)
    out = json.loads(res.stdout)
    assert out["in_catalog"] == [True, True, True, False]
    assert out["in_feedback"] == [False, False, False, True]
    assert out["in_all"] == [True, True, True, True]
    # причина гашения: заявка не называет ни одного вида исполнителя
    # (вид исполнителя кнопку не гасит — виды решателя пишет тот же агент)
    assert out["blocked"] == ["", "app.crs.catalog.manualOnly", ""]
    print(f"строк рассмотрено: {len(cases['rows'])}, "
          f"карточек CR: {len(cases['crs'])}")


_CELL_HARNESS = r"""
var fs = require("fs");
var vm = require("vm");
var src = fs.readFileSync(process.argv[2], "utf8");
var sandbox = {
  t: function (k) { return k; },
  templateBaseState: { data: { catalog_service: {
    url: "__SERVICE_URL__/" } } },
  el: function (tag, attrs, kids) {
    return { tag: tag, attrs: attrs || {}, kids: kids || [] };
  },
  console: console, JSON: JSON
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
var c = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
var cell = sandbox.tplBindingsCell(c.states, null, c.section, c.id,
                                   c.requests);
process.stdout.write(JSON.stringify(cell.kids.map(function (k) {
  return { tag: k.tag, href: k.attrs.href || null,
           title: k.attrs.title || "" };
})));
"""


def test_node_чип_заявлен_ведёт_на_карточку_заявки(tmp_path):
    """Ревью волны 2: чип «заявлен» ведёт на КАРТОЧКУ заявки, как обещают
    руководства 13.10 и 03 §12п.

    Адрес карточки строит сервер (`executor_requests[вид].link`,
    `catalog_pipeline.request_link`); заявка сервером не названа —
    прежний перечень заявок по записи, а не ссылка в никуда."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node недоступен")
    src = _src()
    parts = [re.search(r"var TPL_STATE_%s = \{[^}]+\};" % name,
                       src).group(0)
             for name in ("RANK", "CHIP", "KEY")]
    helpers = tmp_path / "cell.js"
    helpers.write_text("\n".join(parts + [
        _grab(src, "tplBestState"), _grab(src, "tplServiceUrl"),
        _grab(src, "tplBindingsCell")]), encoding="utf-8")
    runner = tmp_path / "cell-run.js"
    runner.write_text(_CELL_HARNESS.replace("__SERVICE_URL__", SERVICE_URL),
                      encoding="utf-8")
    rid = "req-04763385720803207704"
    cases = {
        "section": "families", "id": "duty_limits",
        "states": {"kotlin_constraint": {"COSMOS": "заявлен"},
                   "check_code": {"COSMOS": "есть"},
                   "kotlin_knob": {"COSMOS": "заявлен"}},
        "requests": {"kotlin_constraint": {
            "request_id": rid, "state": "исполнитель заявлен",
            "link": SERVICE_URL + "/ui/#/requests/" + rid}},
    }
    q = tmp_path / "cell-cases.json"
    q.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run([node, str(runner), str(helpers), str(q)],
                         capture_output=True, text=True, encoding="utf-8",
                         check=True)
    got = {row["href"] for row in json.loads(res.stdout)}
    assert SERVICE_URL + "/ui/#/requests/" + rid in got, got
    # вид без названной заявки — прежний перечень по записи
    assert (SERVICE_URL + "/ui/#/requests?section=families"
            "&record_id=duty_limits") in got, got
    # «есть» по-прежнему ведёт в таблицу исполнителей
    assert any("/ui/#/executors?product=COSMOS" in h for h in got), got
    numbered = [row for row in json.loads(res.stdout)
                if rid in (row["title"] or "")]
    assert len(numbered) == 1, "номер заявки — в подсказке чипа"
    print(f"клеток в ячейке: {len(json.loads(res.stdout))}")


def test_node_иной_каталог_службы_гасит_кнопку(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node недоступен")
    src = _src()
    m = re.search(r"var CATALOG_AGENTS = \[[^\]]+\];", src)
    helpers = tmp_path / "helpers.js"
    helpers.write_text("\n".join([m.group(0),
                                  _grab(src, "crsInView"),
                                  _grab(src, "crCatalogBlocked")]),
                       encoding="utf-8")
    runner = tmp_path / "run.js"
    runner.write_text(_HARNESS, encoding="utf-8")
    cases = {
        "rows": [], "crs": [
            {"kind": "catalog-executor",
             "spec": {"executors_requested": [{"kind": "check_code"}]}}],
        "state": {"service": {"same_catalog": False}},
    }
    q = tmp_path / "cases.json"
    q.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run([node, str(runner), str(helpers), str(q)],
                         capture_output=True, text=True, encoding="utf-8",
                         check=True)
    assert json.loads(res.stdout)["blocked"] == \
        ["app.crs.catalog.otherCatalog"]
    print("проверено карточек: 1")
