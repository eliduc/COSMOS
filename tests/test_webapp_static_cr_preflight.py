# -*- coding: utf-8 -*-
"""Блок «Предполётная проверка» в карточке заявки на изменение (шаг 0-П
конвейера CR каталога, руководство 13.14). Три слоя, образец
tests/test_webapp_static_catalog_view.py:

  статика: блок стоит ПЕРЕД итогом работы агента (проверка идёт до него),
    рисуется по наличию поля, а слова вердиктов и видов находок — те же,
    что у сервера (`webapp/change_requests.py`);
  ключи: динамические ключи (`t("app.crs.pf.v." + verdict)`,
    `t("app.crs.pf.kind." + kind)`, `t("app.crs.pf.th." + k)`) гейт
    паритета НЕ видит — здесь они названы поимённо и проверены во всех
    пяти языках;
  node-стенд: настоящий node рисует блок по подставным строкам CR —
    три вердикта, строка прежней версии без поля `preflight`, срез
    длинного перечня пробелов и незнакомое слово вердикта.
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
CRQ_PY = HERE / "webapp" / "change_requests.py"

#: Ключи блока — поимённо: обращения `t("app.crs.pf." + …)` собираются в
#: коде из частей, и гейт паритета их не видит.
KEYS = [
    "app.crs.pf.h",
    "app.crs.pf.v.ok", "app.crs.pf.v.expected-gaps",
    "app.crs.pf.v.judge-assumption",
    "app.crs.pf.th.judge", "app.crs.pf.th.kind", "app.crs.pf.th.words",
    "app.crs.pf.kind.gap", "app.crs.pf.kind.draft",
    "app.crs.pf.kind.assumption",
    "app.crs.pf.gaps", "app.crs.pf.more",
]


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ------------------------------------------------------------- статика

def test_блок_стоит_перед_итогом_работы_агента():
    """Предполётная проверка идёт ДО агента — и в карточке читается
    раньше, чем итог его работы (`cr.impl`)."""
    src = _src()
    # вызов ищется ЖИВОЙ строкой: закомментированный `// crPreflightBlock`
    # прошёл бы поиск подстроки молча (поймано мутацией на себе)
    m = re.search(r"^\s*crPreflightBlock\(card, cr\);\s*$", src, re.M)
    assert m, "живого вызова crPreflightBlock в отрисовке карточки нет"
    impl = src.index("if (cr.impl) {", m.start())
    assert m.start() < impl, "блок предполётной проверки нарисован после impl"
    # рисуется по НАЛИЧИЮ поля, а не по виду строки: судьи добавляются
    assert 'cr.kind === "catalog-executor"' in src[:m.start()]
    assert re.search(r"crPreflightBlock\(card, cr\);\s*\n\s*if \(cr\.impl",
                     src), "между блоками появилось что-то ещё"


def test_строка_без_поля_блока_не_получает():
    """Строки прежних версий поля `preflight` не имеют: «неизвестно» не
    выдумываем — блок им не рисуется вовсе."""
    src = _src()
    body = re.search(r"function crPreflightBlock\([^\n]*\n(.*?)\n\}\n",
                     src, re.S).group(1)
    assert 'if (!pf || typeof pf !== "object" || !pf.verdict) { return; }' \
        in body
    # чтение только полей записи сервера — своих умолчаний блок не сочиняет
    assert "неизвестно" not in body and "unknown" not in body


def test_слова_вердиктов_и_видов_находок_совпадают_с_сервером():
    """Словарь цветов карточки и словарь сервера — об одном и том же:
    расхождение здесь тихо красит вердикт нейтральным чипом."""
    src = _src()
    py = CRQ_PY.read_text(encoding="utf-8")
    verdicts = {re.search(r'PREFLIGHT_%s = "([^"]+)"' % name, py).group(1)
                for name in ("OK", "GAPS", "ASSUMPTION")}
    chip = re.search(r"var PREFLIGHT_CHIP = \{([^}]+)\}", src).group(1)
    got = set(re.findall(r'"([^"]+)":', chip))
    assert got == verdicts, (got, verdicts)
    # семантика цвета: ожидаемые пробелы — предупреждение, допущение — стоп
    one = chip.replace("\n", " ")
    assert '"ok": "chip-ok"' in one
    assert '"expected-gaps": "chip-warn"' in one
    assert '"judge-assumption": "chip-bad"' in one
    kinds = re.search(r"var PREFLIGHT_KIND_CHIP = \{([^}]+)\}",
                      src).group(1).replace("\n", " ")
    # виды находок сервер называет в `_pf_kind`
    for kind in ("gap", "draft", "assumption"):
        assert f'"{kind}"' in py, kind
        assert kind + ":" in kinds, kind
    assert "gap: \"chip-warn\"" in kinds
    assert 'draft: "chip-bad"' in kinds and 'assumption: "chip-bad"' in kinds
    print(f"вердиктов сверено: {len(verdicts)}, видов находок: 3")


def test_ключи_блока_есть_во_всех_пяти_языках():
    blocks = _blocks()
    missing = {lang: sorted(k for k in KEYS if k not in blocks[lang])
               for lang in LANGS}
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, missing
    print(f"ключей проверено: {len(KEYS)} × {len(LANGS)} языков")


def test_подстановки_ключей_блока_совпадают_с_кодом():
    """Что подставляет код — то и объявляет русский словарь."""
    blocks = _blocks()["ru"]
    expect = {"app.crs.pf.v.expected-gaps": {"n"}, "app.crs.pf.more": {"n"}}
    for key in KEYS:
        want = expect.get(key, set())
        got = set(re.findall(r"\{(\w+)\}", blocks[key]))
        assert got == want, (key, got, want)
    print(f"ключей с подстановками проверено: {len(expect)}")


def test_стиль_блока_объявлен():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".cr-preflight" in css


# --------------------------------------------------------- node-стенд

_HARNESS = r"""
var fs = require("fs");
var vm = require("vm");
var src = fs.readFileSync(process.argv[2], "utf8");
function mk(tag, attrs, kids) {
  return { tag: tag, attrs: attrs || {}, kids: (kids || []).slice(),
           appendChild: function (n) { this.kids.push(n); return n; } };
}
var sandbox = {
  el: mk,
  t: function (k) { return k; },
  tf: function (k, p) {
    return k + "(" + Object.keys(p || {}).map(function (x) {
      return x + "=" + p[x];
    }).join(",") + ")";
  },
  uiLang: function () { return "ru"; },
  console: console, JSON: JSON
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
function flat(node, out) {
  out.push({ tag: node.tag, cls: node.attrs["class"] || "",
             text: node.attrs.text === undefined
                   ? "" : String(node.attrs.text) });
  (node.kids || []).forEach(function (k) { flat(k, out); });
  return out;
}
var out = JSON.parse(fs.readFileSync(process.argv[3], "utf8"))
  .map(function (cr) {
    var card = mk("div", { class: "card cr-card" });
    sandbox.crPreflightBlock(card, cr);
    return flat(card, []).slice(1);      // саму карточку не считаем
  });
process.stdout.write(JSON.stringify(out));
"""


def _grab(src: str, name: str) -> str:
    """Тело функции верхнего уровня по имени (до строки «}» в нулевой
    колонке) — тот же приём, что в стендах «Каталог» и «Прогоны»."""
    m = re.search(r"^function " + re.escape(name) + r"\([^\n]*\n", src, re.M)
    assert m, name
    end = src.index("\n}\n", m.start())
    return src[m.start():end + 3]


def _helpers(tmp_path: Path) -> Path:
    """Ровно то, что нужно блоку: три словаря, сам блок и НАСТОЯЩИЕ
    `fmtTime`/`localeTag` (`uiLang` подставлен стендом) — время в шапке
    считает тот же код, что и в браузере."""
    src = _src()
    parts = [re.search(r"var PREFLIGHT_CHIP = \{[^}]+\};", src).group(0),
             re.search(r"var PREFLIGHT_KIND_CHIP = \{[^}]+\};",
                       src).group(0),
             re.search(r"var PREFLIGHT_GAPS_SHOWN = \d+;", src).group(0),
             _grab(src, "localeTag"), _grab(src, "fmtTime"),
             _grab(src, "crPreflightBlock")]
    path = tmp_path / "preflight.js"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _render(tmp_path: Path, cases: list) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("node недоступен")
    runner = tmp_path / "run.js"
    runner.write_text(_HARNESS, encoding="utf-8")
    q = tmp_path / "cases.json"
    q.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run([node, str(runner), str(_helpers(tmp_path)),
                          str(q)],
                         capture_output=True, text=True, encoding="utf-8",
                         check=True)
    return json.loads(res.stdout)


AT = "2026-09-10T07:41:12+00:00"
GAP_EXEC = ("исполнителя нет: вид check_code объявлен записью, строки "
            "«есть» в привязках нет")
GAP_STAND = ("задач стенда нет: запись «есть» без стендовых задач "
             "(предупреждение X07)")


def _chips(rows: list) -> list:
    return [(r["cls"], r["text"]) for r in rows if "chip" in r["cls"]]


def test_node_три_вердикта_и_строка_без_предполётной_проверки(tmp_path):
    """Три вердикта сервера и строка прежней версии — в одном прогоне."""
    cases = [
        # 1. судьи молчат
        {"preflight": {"at": AT, "verdict": "ok", "findings": [],
                       "gaps": []}},
        # 2. ожидаемые пробелы: их закрывает агент, реализация идёт
        {"preflight": {"at": AT, "verdict": "expected-gaps", "findings": [
            {"judge": "bindings", "kind": "gap", "words": GAP_EXEC},
            {"judge": "stand", "kind": "gap", "words": GAP_STAND}],
            "gaps": [GAP_EXEC, GAP_STAND]}},
        # 3. допущение судей: строка уходит «нужен человек» ДО агента
        {"preflight": {"at": AT, "verdict": "judge-assumption",
                       "findings": [
                           {"judge": "catalog", "kind": "assumption",
                            "words": "судья сломался: KeyError: families"},
                           {"judge": "reader", "kind": "draft",
                            "words": "error families/zz: раздела нет"},
                           {"judge": "stand", "kind": "gap",
                            "words": GAP_STAND}],
                       "gaps": [GAP_STAND]}},
        # 4. строка прежней версии — поля нет
        {"id": "cr-7", "title": "без предполётной проверки"},
    ]
    ok, gaps, stop, old = _render(tmp_path, cases)

    # 1. «судьи молчат» — зелёный чип, ни таблицы находок, ни перечня
    assert _chips(ok) == [("chip chip-ok small", "app.crs.pf.v.ok(n=0)")]
    assert [r["tag"] for r in ok if r["tag"] in ("table", "ul")] == []
    assert any(r["tag"] == "strong" and r["text"] == "app.crs.pf.h"
               for r in ok), "заголовок блока"
    время = [r["text"] for r in ok if r["cls"] == "muted small"]
    assert время and время[0] not in ("", "—", AT), время

    # 2. предупреждение, число — из НАХОДОК судей; таблица и перечень
    assert _chips(gaps)[0] == ("chip chip-warn small",
                               "app.crs.pf.v.expected-gaps(n=2)")
    assert [c[0] for c in _chips(gaps)[1:]] == ["chip chip-warn small"] * 2
    assert [r["text"] for r in gaps if r["tag"] == "th"] == [
        "app.crs.pf.th.judge", "app.crs.pf.th.kind", "app.crs.pf.th.words"]
    assert len([r for r in gaps if r["tag"] == "tr"]) == 3   # шапка + двое
    судьи = [r["text"] for r in gaps if r["cls"] == "mono small"]
    assert судьи == ["bindings", "stand"]
    assert GAP_EXEC in [r["text"] for r in gaps if r["tag"] == "td"]
    assert [r["text"] for r in gaps if r["tag"] == "li"] == [GAP_EXEC,
                                                             GAP_STAND]
    assert any(r["text"] == "app.crs.pf.gaps" for r in gaps)

    # 3. стоп: и допущение судьи, и негодная заготовка — красные,
    #    ожидаемый пробел рядом остаётся жёлтым
    assert _chips(stop)[0] == ("chip chip-bad small",
                               "app.crs.pf.v.judge-assumption(n=1)")
    assert [c[0] for c in _chips(stop)[1:]] == [
        "chip chip-bad small", "chip chip-bad small", "chip chip-warn small"]
    assert [c[1] for c in _chips(stop)[1:]] == [
        "app.crs.pf.kind.assumption", "app.crs.pf.kind.draft",
        "app.crs.pf.kind.gap"]

    # 4. поля нет — блока нет вовсе
    assert old == [], old
    print(f"строк CR отрисовано: {len(cases)}, "
          f"узлов в самой полной карточке: {max(map(len, (ok, gaps, stop)))}")


def test_node_длинный_перечень_срезан_и_незнакомый_вердикт_показан(tmp_path):
    """Карточка не разрастается перечнем (остаток назван числом), а
    незнакомое слово вердикта показано как есть: судьи ДОБАВЛЯЮТСЯ, и
    молчать о новом слове хуже, чем показать сырое."""
    src = _src()
    shown = int(re.search(r"var PREFLIGHT_GAPS_SHOWN = (\d+);",
                          src).group(1))
    long_gaps = [f"пробел {i}" for i in range(shown + 3)]
    cases = [
        {"preflight": {"at": AT, "verdict": "expected-gaps", "findings": [],
                       "gaps": long_gaps}},
        {"preflight": {"at": AT, "verdict": "судья-которого-ещё-нет",
                       "findings": [{"judge": "новый", "kind": "новый-вид",
                                     "words": "слова нового судьи"}],
                       "gaps": []}},
    ]
    long_row, unknown = _render(tmp_path, cases)

    items = [r["text"] for r in long_row if r["tag"] == "li"]
    assert items == long_gaps[:shown], items
    assert any(r["text"] == "app.crs.pf.more(n=3)" for r in long_row), \
        "остаток перечня не назван числом"

    assert _chips(unknown)[0] == ("chip chip-neutral small",
                                  "судья-которого-ещё-нет")
    assert _chips(unknown)[1] == ("chip chip-neutral small", "новый-вид")
    assert "слова нового судьи" in [r["text"] for r in unknown]
    print(f"пробелов показано {shown} из {len(long_gaps)}; "
          f"незнакомых слов показано сырыми: 2")
