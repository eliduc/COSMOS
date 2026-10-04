# -*- coding: utf-8 -*-
"""Перетаскивание файлов на блоки схемы конвейера COSMOS (слово владельца
06.09.2026, образец — единообразная загрузка на блок в панели CDPM).

Три слоя проверок:
  статика (образец tests/test_h0_selected_pairing_gpt.py — разбор
  webapp/static/app.js текстом):
  1. Приёмники объявлены данными — таблицей FLOW_DROP; сегодня в ней
     только «Постановка (ВМП)»: один zip пакета постановки.
  2. У блока-приёмника есть обработчики dragenter/dragover/dragleave/drop;
     блок без записи в FLOW_DROP получает только drop (отказ с именем
     блока, без реакции на наведение); запрет обработки файла браузером —
     на window, но штатные поля <input type="file"> форм исключены.
  3. Один вход загрузки: кнопка панели ВМП, строка панели и блок зовут
     flowUploadPackage; ручка POST /api/task-package/upload упоминается в
     app.js один раз.
  4. Ключи i18n flow.drop.* — во всех пяти языках с одинаковыми
     подстановками; классы подсветки — в style.css; мануал 03 описывает
     перетаскивание в «Работе со схемой»; смена языка пересобирает схему.
  node-стенд (образец tests/test_loops_client_i18n.py — функции app.js
  исполняются настоящим node в vm с живым i18n.js):
  5. Каждая ветка _flowDropProblem достижима и даёт текст ключа: два
     файла, не zip, .ZIP в верхнем регистре, размер выше предела, размер
     на байт выше предела (байтами, а не «64.0 больше 64.0»), предел не
     известен (размер судит сервер), нет файлов, блок без приёмника.
  6. _flowHasFiles: массив и DOMStringList без includes; тексты отказов
     сервера: brief 422, HTTPException, обрыв связи, 413 с пределом и
     без; итог MISMATCH — причины из brief.mismatch без повтора слов
     сервера; повтор пакета — пометка.
  сервер (живой TestClient):
  7. GET /api/flow несёт upload_max_bytes == CHANNEL_BODY_MAX_BYTES, а
     CREW_CHANNEL_MAX_MB задаёт это число.
  8. POST /api/task-package/upload: 413 словами ДО разбора формы по
     Content-Length и после чтения по байтам; 400 — нет файла / не zip.
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tests.test_i18n_parity import LANGS, _blocks  # noqa: E402

APP_JS = HERE / "webapp" / "static" / "app.js"
I18N = HERE / "webapp" / "static" / "i18n.js"
STYLE = HERE / "webapp" / "static" / "style.css"
INDEX = HERE / "webapp" / "static" / "index.html"
APP_PY = HERE / "webapp" / "app.py"
MANUAL = HERE / "docs" / "manual" / "03-optimizaciya-RU.md"

EXPECTED_KEYS = {
    "flow.drop.hint", "flow.drop.uploading", "flow.drop.busy",
    "flow.drop.nofile", "flow.drop.one", "flow.drop.notzip",
    "flow.drop.big", "flow.drop.big.bytes", "flow.drop.refuse",
    "flow.drop.canvas", "flow.drop.form", "flow.drop.panel",
    "flow.drop.ok", "flow.drop.mismatch", "flow.drop.fail",
    "flow.drop.limit",
}


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _fn(source: str, name: str) -> str:
    """Тело функции верхнего уровня: от `function <name>(` до следующего
    объявления функции верхнего уровня."""
    start = source.index("function " + name + "(")
    nxt = re.compile(r"^function \w+\(", re.M).search(source, start + 1)
    return source[start:nxt.start() if nxt else len(source)]


# ------------------------------------------------------------- статика

def test_flow_drop_table_declares_only_vmp():
    source = _src()
    m = re.search(r"^var FLOW_DROP = \{(.*?)^\};", source, re.S | re.M)
    assert m, "таблица приёмников FLOW_DROP не объявлена"
    body = m.group(1)
    keys = re.findall(r'^\s*"?([\w.]+)"?:\s*\{', body, re.M)
    assert keys == ["vmp"], keys
    assert 'accept: [".zip"]' in body
    assert "single: true" in body
    # список «что могло бы принимать файлы позже» записан рядом
    assert "Что могло бы принимать файлы позже" in source


def test_receiver_handlers_and_window_guard():
    source = _src()
    wire = _fn(source, "_flowWireDrop")
    # блок без записи в FLOW_DROP: только drop — отказ с именем блока
    # через ту же _flowDropProblem; наведение (dragenter/dragover) не
    # обрабатывается — блок на файл не реагирует
    m = re.search(r"if \(!FLOW_DROP\[id\]\) \{(.*?)\n    return;\n  \}",
                  wire, re.S)
    assert m, "ветка блока без приёмника не найдена"
    branch = m.group(1)
    assert 'addEventListener("drop"' in branch
    assert "_flowDropProblem(id, ev.dataTransfer.files)" in branch
    assert 'addEventListener("dragenter"' not in branch
    assert 'addEventListener("dragover"' not in branch
    for ev in ("dragenter", "dragover", "dragleave", "drop"):
        assert 'addEventListener("%s"' % ev in wire, ev
    # подсветка приёмника и индикация загрузки на элементе
    assert 'classList.add("drop-ok")' in wire
    assert 'classList.add("uploading")' in wire
    assert 't("flow.drop.uploading")' in wire
    # только файлы: перетаскивание текста блок не подсвечивает
    assert "_flowHasFiles(ev)" in wire
    assert 'dropEffect = "copy"' in wire
    # обратный вызов после успешного приёма — панель обновляет карточку
    assert "if (onDone) { onDone(brief); }" in wire
    # запрет на window: штатные поля выбора файла форм исключены в обоих
    # обработчиках — иначе файл, брошенный на <input type="file"> экрана
    # данных, не попал бы в поле (замечание критика 07.09.2026)
    assert '<input type="file"' in INDEX.read_text(encoding="utf-8"), \
        "утверждение об исключении полей без полей в index.html пусто"
    on_input = _fn(source, "_flowDropOnFileInput")
    assert "closest('input[type=\"file\"]')" in on_input
    guard = _fn(source, "_flowWireWindowDrop")
    assert 'window.addEventListener("dragover"' in guard
    assert 'window.addEventListener("drop"' in guard
    assert guard.count("_flowDropOnFileInput(ev)") == 2
    assert "_flowStrayDropText(ev)" in guard
    # текст отказа — по месту броска: панель со строкой загрузки, экран
    # с полем формы, холст схемы
    stray = _fn(source, "_flowStrayDropText")
    assert 't("flow.drop.panel")' in stray and ".vmp-droprow" in stray
    assert 't("flow.drop.form")' in stray and '_nav.tab !== "flow"' in stray
    assert 'tf("flow.drop.canvas"' in stray
    node = _fn(source, "_flowMakeNode")
    assert "_flowWireDrop(id, root, hint)" in node
    assert 't("flow.drop.hint")' in node
    # страж ставится при старте страницы — до первого ответа /api/flow
    # (ревизия 07.09.2026: раньше — только после успешной сборки схемы,
    # и до неё файл открывался вместо интерфейса); в _flowEnsureDom
    # вызов остаётся — повтор безвреден
    init_fn = _fn(source, "init")
    assert "_flowWireWindowDrop();" in init_fn
    assert init_fn.index("_flowWireWindowDrop();") \
        < init_fn.index('showTab("flow")')
    assert init_fn.index("_flowWireWindowDrop();") \
        < init_fn.index("refreshInstances();")
    ensure = _fn(source, "_flowEnsureDom")
    assert "_flowWireWindowDrop();" in ensure
    assert "if (_flowDropGuardWired) { return; }" in guard


def test_single_upload_entry_for_panel_row_and_block():
    source = _src()
    assert source.count('"/task-package/upload"') == 1
    upload = _fn(source, "flowUploadPackage")
    assert '"/task-package/upload"' in upload
    assert 'method: "POST"' in upload
    assert 'fd.append("file", file, file.name)' in upload
    # повтор во время загрузки — отказ словами, не второй запрос
    assert "_flowUploadBusy" in upload and 't("flow.drop.busy")' in upload
    # после ответа схема обновляется сразу, минуя демпфер
    assert "_flowBusyUntil = 0" in upload and "refreshFlow()" in upload
    panel = source[source.index("function vmpRunBlock("):
                   source.index("function vmpChainsGantt(")]
    assert "flowUploadPackage(up.files[0])" in panel
    assert '_flowDropProblem("vmp", up.files)' in panel
    # строка загрузки панели — тот же приёмник с обновлением карточки
    assert '_flowWireDrop("vmp", upRow, upNote, onPkgChange)' in panel
    assert 'class: "vmp-row vmp-droprow"' in panel
    assert 'text: t("flow.drop.hint")' in panel
    wire = _fn(source, "_flowWireDrop")
    assert "flowUploadPackage(files[0])" in wire
    assert "_flowDropProblem(id, files)" in wire


def test_client_side_refusals_before_server():
    source = _src()
    check = _fn(source, "_flowDropProblem")
    assert 'tf("flow.drop.one"' in check
    assert 'tf("flow.drop.notzip"' in check
    assert 'tf("flow.drop.big"' in check
    assert 'tf("flow.drop.big.bytes"' in check
    assert 'tf("flow.drop.refuse"' in check
    assert "_flowUploadMax" in check and "f.size > _flowUploadMax" in check
    render = _fn(source, "renderFlow")
    assert "stateFlow.upload_max_bytes" in render
    # итог гейта словами: OK / MISMATCH (причины из brief.mismatch) / отказ
    report = _fn(source, "_flowDropReport")
    assert 'tf("flow.drop.ok"' in report
    assert 'tf("flow.drop.mismatch"' in report
    assert "_flowMismatchReasons(brief)" in report
    assert 't("flow.vmp.cur.repeat")' in report
    reasons = _fn(source, "_flowMismatchReasons")
    assert "brief.mismatch" in reasons
    wire = _fn(source, "_flowWireDrop")
    assert 'tf("flow.drop.fail"' in wire
    # 413 сервера — на языке интерфейса с пределом из /api/flow
    err = _fn(source, "_flowUploadErrorText")
    assert "e.status === 413" in err and 'tf("flow.drop.limit"' in err


def test_language_switch_rebuilds_flow_diagram():
    """Схема строится один раз; смена языка пересобирает её на месте —
    иначе заголовки блоков и строка «⤓ …» остались бы на прежнем языке."""
    source = _src()
    start = source.index('document.addEventListener("crew:lang"')
    handler = source[start:source.index("\n});", start)]
    assert "_flowDom = null;" in handler
    assert "_flowEnsureDom()" in handler
    assert "renderFlow(_flowState)" in handler


def test_i18n_keys_present_in_all_five_languages():
    source = _src()
    used = set(re.findall(r'\bt\("(flow\.drop\.[\w.]+)"', source))
    used |= set(re.findall(r'\btf\("(flow\.drop\.[\w.]+)"', source))
    assert used == EXPECTED_KEYS, used ^ EXPECTED_KEYS
    blocks = _blocks()
    for lang in LANGS:
        missing = EXPECTED_KEYS - set(blocks[lang])
        assert not missing, f"{lang}: нет ключей {sorted(missing)}"
    ph = re.compile(r"\{(\w+)\}")
    for key in EXPECTED_KEYS:
        ru = set(ph.findall(blocks["ru"][key]))
        for lang in LANGS:
            assert set(ph.findall(blocks[lang][key])) == ru, (lang, key)
    # тур обязан упоминать новую возможность существующего экрана
    # (чек-лист витрин, пункт а) — во всех пяти языках
    for lang in LANGS:
        assert "zip" in blocks[lang]["tour.gflow.p1"], lang


def test_highlight_classes_in_stylesheet():
    css = STYLE.read_text(encoding="utf-8")
    for sel in (".flow-node.drop-ok", ".flow-node.uploading",
                ".flow-node-drophint", ".vmp-droprow.drop-ok",
                ".vmp-droprow.uploading"):
        assert sel in css, sel
    # outline, а не border: граница .done/.working занята !important
    m = re.search(r"\.flow-node\.drop-ok[^{]*\{([^}]*)\}", css)
    assert m and "outline:" in m.group(1)


def test_server_publishes_upload_limit_in_flow_state():
    py = APP_PY.read_text(encoding="utf-8")
    start = py.index('@app.get("/api/flow")')
    end = py.index("@app.", start + 1)
    handler = py[start:end]
    assert 'st["upload_max_bytes"] = CHANNEL_BODY_MAX_BYTES' in handler
    # ручка загрузки: отсев по Content-Length до разбора формы, файл — из
    # request.form(), а не параметром UploadFile (тот разобрал бы всё тело
    # до входа в обработчик); отказ 413 — деловым русским
    start = py.index('@app.post("/api/task-package/upload")')
    up = py[start:py.index("@app.", start + 1)]
    assert "UploadFile" not in up.split('"""')[2].split("await request.form()")[0]
    assert 'request.headers.get("content-length")' in up
    assert up.index('request.headers.get("content-length")') \
        < up.index("await request.form()")
    assert "тело запроса больше предела" in up
    assert "request body over" not in up


def test_manual_describes_drop_on_blocks():
    text = MANUAL.read_text(encoding="utf-8")
    i = text.index("**Работа со схемой.**")
    j = text.index("**Перетаскивание файлов на блоки.**", i)
    para = text[j:text.index("\n\n", j)]
    assert "Постановка (ВМП)" in para
    assert "/api/task-package/upload" in para
    assert "upload_max_bytes" in para
    assert "с именем" in para          # отказ на чужом блоке — по имени
    assert "413" in para               # отказ сервера — на языке интерфейса
    assert "поля выбора файла" in para  # формы других экранов не сломаны
    row = [ln for ln in text.splitlines()
           if ln.startswith("| Постановка (ВМП) |")]
    assert row and "перетаскивание zip на блок" in row[0]


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


def _table(src, name):
    m = re.search(r"^var %s = \{.*?^\};" % re.escape(name), src, re.S | re.M)
    assert m, name
    return m.group(0)


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

_FUNCS = ("_fmtMb", "_fmtInt", "_flowDropTargets", "_flowDropProblem",
          "_flowUploadErrorText", "_flowMismatchReasons", "_flowDropReport",
          "_flowHasFiles")

_STUBS = """
var _flowUploadMax = null;
var _toasts = [];
function toast(m, k) { _toasts.push([m, k || "error"]); }
"""


def _node(tmp_path, expr, lang):
    node = shutil.which("node")
    if not node:
        pytest.skip("node недоступен")
    src = _src()
    helpers = tmp_path / "helpers.js"
    helpers.write_text(
        "\n".join([_table(src, "FLOW_BLOCKS"), _table(src, "FLOW_DROP"),
                   _STUBS] + [_grab(src, n) for n in _FUNCS]),
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


_CASES = r"""
(function () {
  var out = {};
  var f = function (name, size) { return { name: name, size: size }; };
  _flowUploadMax = 1048576;
  out.two = [_flowDropProblem("vmp", [f("a.zip", 1), f("b.zip", 1)]),
             tf("flow.drop.one", { n: 2 })];
  out.txt = [_flowDropProblem("vmp", [f("a.txt", 1)]),
             tf("flow.drop.notzip", { name: "a.txt" })];
  out.upper = _flowDropProblem("vmp", [f("A.ZIP", 1)]);
  out.big = [_flowDropProblem("vmp", [f("p.zip", 2097152)]),
             tf("flow.drop.big", { name: "p.zip", size: "2.0", max: "1.0" })];
  out.bytes = [_flowDropProblem("vmp", [f("p.zip", 1048577)]),
               tf("flow.drop.big.bytes", { name: "p.zip",
                                           size: "1 048 577",
                                           max: "1 048 576" })];
  out.none = [_flowDropProblem("vmp", []), t("flow.drop.nofile")];
  out.refuse = [_flowDropProblem("conform", [f("x.zip", 1)]),
                tf("flow.drop.refuse", { block: t("flow.b.conform"),
                                         target: "«" + t("flow.b.vmp") + "»" })];
  _flowUploadMax = null;
  out.unknown = _flowDropProblem("vmp", [f("huge.zip", 1e12)]);
  out.has = [_flowHasFiles({ dataTransfer: { types: ["Files"] } }),
             _flowHasFiles({ dataTransfer: { types: { length: 1, 0: "Files" } } }),
             _flowHasFiles({ dataTransfer: { types: ["text/plain"] } }),
             _flowHasFiles({}), _flowHasFiles(null)];
  out.err422 = _flowUploadErrorText({ status: 422,
    body: { verdict: "REFUSED", detail: "красный гейт" } });
  out.err400 = _flowUploadErrorText({ status: 400, body: { detail: "не zip" } });
  out.err0 = _flowUploadErrorText({ status: 0, message: "нет связи" });
  out.err413unknown = _flowUploadErrorText({ status: 413,
    body: { detail: "тело запроса больше предела 64 МБ" } });
  _flowUploadMax = 1048576;
  out.err413 = [_flowUploadErrorText({ status: 413, body: { detail: "srv" } }),
                tf("flow.drop.limit", { max: "1.0" })];
  _toasts.length = 0;
  _flowDropReport({ verdict: "MISMATCH", mismatch: ["a", "b"],
    detail: "пакет принят, но матрица готовности: MISMATCH — запуск запрещён" });
  out.mm = _toasts.slice();
  _toasts.length = 0;
  _flowDropReport({ verdict: "MISMATCH", detail: "d",
                    mismatch: ["1", "2", "3", "4", "5"] });
  out.mm5 = _toasts.slice();
  _toasts.length = 0;
  _flowDropReport({ verdict: "MISMATCH", detail: "только слова сервера",
                    mismatch: [] });
  out.mmfallback = _toasts.slice();
  _toasts.length = 0;
  _flowDropReport({ verdict: "OK", already_accepted: true });
  out.ok = [_toasts.slice(),
            tf("flow.drop.ok", { verdict: "OK" }) + " · " + t("flow.vmp.cur.repeat")];
  out.mmkey = t("flow.drop.mismatch");
  out.block = t("flow.b.conform");
  return out;
})()
"""


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_node_client_branches_are_reachable_and_localised(tmp_path, lang):
    r = _node(tmp_path, _CASES, lang)
    # отказы до сервера — текст ключа с подстановкой, каждая ветка живая
    assert r["two"][0] == r["two"][1] and "2" in r["two"][0]
    assert r["txt"][0] == r["txt"][1] and "a.txt" in r["txt"][0]
    assert r["upper"] is None, "расширение судится без учёта регистра"
    assert r["big"][0] == r["big"][1]
    assert r["bytes"][0] == r["bytes"][1]
    assert "1 048 577" in r["bytes"][0] and "1 048 576" in r["bytes"][0]
    assert r["none"][0] == r["none"][1]
    # блок без приёмника — отказ с именем блока и приёмника
    assert r["refuse"][0] == r["refuse"][1]
    assert r["block"] in r["refuse"][0]
    assert r["unknown"] is None, "предел не известен — размер судит сервер"
    assert r["has"] == [True, True, False, False, False]
    # тексты отказов сервера
    assert r["err422"] == "красный гейт"
    assert r["err400"] == "не zip"
    assert r["err0"] == "нет связи"
    assert r["err413unknown"] == "тело запроса больше предела 64 МБ"
    assert r["err413"][0] == r["err413"][1]
    # MISMATCH: причины из brief.mismatch, слова сервера не повторяются
    assert len(r["mm"]) == 1 and r["mm"][0][1] == "warn"
    mm = r["mm"][0][0]
    assert "a; b" in mm
    assert "матрица готовности: MISMATCH" not in mm
    prefix = r["mmkey"].split("{detail}")[0]
    assert mm.count(prefix) == 1
    assert "1; 2; 3; …" in r["mm5"][0][0] and "4" not in r["mm5"][0][0]
    assert "только слова сервера" in r["mmfallback"][0][0]
    # OK с пометкой повтора
    assert r["ok"][0] == [[r["ok"][1], "ok"]]


# ------------------------------------------------------------------ сервер

@contextlib.contextmanager
def _app_client(tmp_path, max_mb=None):
    """TestClient в dev-режиме (образец фикстур test_static_version);
    max_mb — CREW_CHANNEL_MAX_MB на время загрузки модуля; после теста
    предел возвращается к прежнему значению модуля."""
    from webapp import users
    from webapp import app as app_module
    saved = app_module.CHANNEL_BODY_MAX_BYTES
    mp = pytest.MonkeyPatch()
    from _stand_identity import ADMIN_EMAIL
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    mp.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    if max_mb is None:
        mp.delenv("CREW_CHANNEL_MAX_MB", raising=False)
    else:
        mp.setenv("CREW_CHANNEL_MAX_MB", str(max_mb))
    mp.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    import importlib
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    try:
        with TestClient(app_module.app) as c:
            yield c, app_module
    finally:
        mp.undo()
        app_module.CHANNEL_BODY_MAX_BYTES = saved


@pytest.fixture()
def client(tmp_path):
    with _app_client(tmp_path) as (c, _):
        yield c


UPLOAD = "/api/task-package/upload"


def test_api_flow_carries_upload_max_bytes(client, monkeypatch):
    from webapp import app as app_module
    from webapp import flow
    monkeypatch.setattr(flow, "build_state", lambda: {"blocks": []})
    r = client.get("/api/flow")
    assert r.status_code == 200
    body = r.json()
    assert body["blocks"] == []
    assert body["upload_max_bytes"] == app_module.CHANNEL_BODY_MAX_BYTES
    assert body["upload_max_bytes"] > 0


def test_api_flow_limit_follows_env(tmp_path, monkeypatch):
    """CREW_CHANNEL_MAX_MB — одно число на обе стороны: клиент видит в
    /api/flow ровно то, что сервер применит к загрузке."""
    from webapp import flow
    monkeypatch.setattr(flow, "build_state", lambda: {"blocks": []})
    with _app_client(tmp_path, max_mb=1) as (c, app_module):
        assert app_module.CHANNEL_BODY_MAX_BYTES == 1048576
        assert c.get("/api/flow").json()["upload_max_bytes"] == 1048576


def test_upload_413_in_words_before_form_parse(client, monkeypatch):
    """Файл больше предела отклоняется по Content-Length ДО разбора формы:
    разбор формы подменён на падение — если бы ручка дошла до него, тест
    упал бы; текст отказа — деловой русский."""
    import starlette.requests as sr
    from webapp import app as app_module
    monkeypatch.setattr(app_module, "CHANNEL_BODY_MAX_BYTES", 64)

    async def _no_form(self, *a, **k):
        raise AssertionError("форма разобрана до отсева по Content-Length")
    monkeypatch.setattr(sr.Request, "form", _no_form)
    r = client.post(UPLOAD, files={"file": ("big.zip", b"x" * 200_000,
                                            "application/zip")})
    assert r.status_code == 413, r.text
    detail = r.json()["detail"]
    assert "тело запроса больше предела" in detail and "64 байт" in detail
    assert "request body" not in detail


def test_upload_413_in_words_after_read(client, monkeypatch):
    """Тело в пределах запаса на обрамление multipart, но файл больше
    предела — 413 после чтения, теми же словами."""
    from webapp import app as app_module
    monkeypatch.setattr(app_module, "CHANNEL_BODY_MAX_BYTES", 64)
    r = client.post(UPLOAD, files={"file": ("small.zip", b"x" * 200,
                                            "application/zip")})
    assert r.status_code == 413, r.text
    assert "64 байт" in r.json()["detail"]


def test_upload_413_without_content_length_before_read(client, monkeypatch):
    """Тело без Content-Length (Transfer-Encoding: chunked — машинный
    клиент по токену, curl): отсев по заголовку не срабатывает, форму
    разбирает Starlette; отказ 413 обязан идти по размеру части ДО чтения
    файла в память — чтение подменено на падение (ревизия 07.09.2026)."""
    from starlette.datastructures import UploadFile
    from webapp import app as app_module
    monkeypatch.setattr(app_module, "CHANNEL_BODY_MAX_BYTES", 64)

    async def _no_read(self, *a, **k):
        raise AssertionError("файл прочитан в память до проверки размера")
    monkeypatch.setattr(UploadFile, "read", _no_read)
    body = (b"--crewdnd\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="big.zip"\r\n'
            b"Content-Type: application/zip\r\n\r\n"
            + b"x" * 200_000 + b"\r\n--crewdnd--\r\n")

    def _chunks():
        for i in range(0, len(body), 65536):
            yield body[i:i + 65536]
    req = client.build_request(
        "POST", UPLOAD, content=_chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=crewdnd"})
    assert "content-length" not in req.headers
    assert req.headers.get("transfer-encoding") == "chunked"
    r = client.send(req)
    assert r.status_code == 413, r.text
    assert "64 байт" in r.json()["detail"]


def test_upload_reads_no_more_than_limit_and_closes_form(client, monkeypatch):
    """Разборщик без поля size (старший Starlette): чтение ограничено
    пределом плюс байт — лишний байт даёт 413, весь файл в память не
    поднимается; форма закрывается и на ветке отказа (finally)."""
    import starlette.datastructures as sd
    from webapp import app as app_module
    monkeypatch.setattr(app_module, "CHANNEL_BODY_MAX_BYTES", 64)
    seen = {}
    orig_init = sd.UploadFile.__init__
    orig_read = sd.UploadFile.read
    orig_close = sd.FormData.close

    def _init(self, *a, **k):
        k["size"] = None            # разборщик, не знающий размера
        orig_init(self, *a, **k)

    async def _read(self, size=-1):
        seen["size_arg"] = size
        return await orig_read(self, size)

    async def _close(self):
        seen["closed"] = seen.get("closed", 0) + 1
        return await orig_close(self)
    monkeypatch.setattr(sd.UploadFile, "__init__", _init)
    monkeypatch.setattr(sd.UploadFile, "read", _read)
    monkeypatch.setattr(sd.FormData, "close", _close)
    r = client.post(UPLOAD, files={"file": ("small.zip", b"x" * 200,
                                            "application/zip")})
    assert r.status_code == 413, r.text
    assert seen["size_arg"] == 65, seen
    assert seen["closed"] == 1, seen
    # и на ветке 400 форма тоже закрыта
    seen.clear()
    r = client.post(UPLOAD, files={"file": ("x.txt", b"nope", "text/plain")})
    assert r.status_code == 400
    assert seen.get("closed") == 1, seen


def test_upload_400_no_file_or_not_zip(client):
    r = client.post(UPLOAD, data={"note": "без файла"})
    assert r.status_code == 400 and "поле file" in r.json()["detail"]
    r = client.post(UPLOAD, files={"file": ("x.txt", b"nope", "text/plain")})
    assert r.status_code == 400 and "zip" in r.json()["detail"]
