# -*- coding: utf-8 -*-
"""Экран «Прогоны» COSMOS (слово владельца 07.09.2026; проект
final-report/COSMOS-PROGONY-PROEKT-CLD.md, разделы 10.3, 11, 13.1, 17.5).

Три слоя проверок (образец tests/test_webapp_static_dnd.py):
  статика (app.js, index.html, i18n.js, style.css, onboarding.js текстом):
  1. Вкладка «Прогоны» — группа cruns сразу после flow (порядок панели =
     порядок тура, единообразие с панелью CDPM); showTab читает список и
     ведёт опрос только на экране; смена языка перерисовывает список и чип
     шапки; init подключает обработчики до первого showTab.
  2. Разметка: панель #panel-cruns со строкой хранилища, формой нового
     прогона, поиском, пилюлями, сортировкой, отметкой; шапка конвейера —
     чип #flow-run-chip, переключатель #flow-run-select, кнопка
     #btn-flow-run-new, полоса-подсказка #flow-run-hint.
  3. Статика зовёт ручки строго по формам проекта (раздел 9.2, 17.4): список,
     создание, текущий, порядок, архив, оформление, название, повтор, отчёт
     повтора — и никаких других путей /cosmos-runs.
  4. Ключи i18n cruns.*, flow.p.run, flow.run.*, tour.gcruns.* — во всех
     пяти языках с одинаковыми подстановками (паритет — tests/test_i18n_parity);
     семейства кодов (состояния, корзины, коды пробелов и отказов, вердикты
     повтора) названы явно; ключ cruns.note.next-queue снят.
  5. Страж переименования легаси-вкладки «Прогоны» → «История задач»
     (раздел 11.6): в каждом языке html.tab.runs != html.tab.cruns, пять
     переписанных ключей не содержат новой подписи, learn.c_opt.s5 содержит
     подпись вкладки истории.
  6. Тур: шаг gcruns сразу после gflow; у каждого шага tour.<key>.title в
     пяти языках. Классы .cruns-* и .flow-run-* в style.css.
  node-стенд (функции app.js исполняются настоящим node в vm с живым
  i18n.js, параметризация по пяти языкам):
  7. Сортировка order/created/name × asc/desc с NaN-датами и localeCompare
     по языку интерфейса; девять состояний → четыре корзины; подписи
     состояний и слова к кодам пробелов (известный код — слова, неизвестный
     — как есть, уточнение сервера — после тире); фильтр по корзине и
     подстроке; чип вердикта повтора (90 знаков), причина недоступности
     кнопок повтора, сводка назначений, слова о различиях среды.
  документы:
  8. Маркеры мануалов 03 и 09 — дорожка «Документы»; до их появления тест
     называет недостающий маркер и пропускается словами, а не молчит.
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

from tests.test_i18n_parity import LANGS, _blocks  # noqa: E402

STATIC = HERE / "webapp" / "static"
APP_JS = STATIC / "app.js"
I18N = STATIC / "i18n.js"
STYLE = STATIC / "style.css"
INDEX = STATIC / "index.html"
ONBOARDING = STATIC / "onboarding.js"
MANUAL_03 = HERE / "docs" / "manual" / "03-optimizaciya-RU.md"
MANUAL_09 = HERE / "docs" / "manual" / "09-spravochnik-RU.md"

STATE_KINDS = ["new", "accepted", "blocked", "lost", "working", "done",
               "stalled", "refused", "interrupted"]
BUCKET_OF = {"new": "new", "accepted": "new", "blocked": "new",
             "lost": "fail", "working": "work", "done": "done",
             "stalled": "done", "refused": "fail", "interrupted": "fail"}
NOTE_CODES = [
    # раздел 11.5
    "pre-passport", "disabled-by-site", "no-model-calls",
    "channel-does-not-set", "ollama-default", "none-in-cosmos",
    "no-version-endpoint", "build-files", "no-build-files",
    "solver-default-seed-0", "profile-d1", "not-declared-by-package",
    "catalog-unavailable", "package-bytes-missing", "no-snapshot-copy",
    "snapshot-of-other-package", "engine-only-timefold",
    # раздел 17.5
    "pre-journal", "journal-incomplete", "journal-mismatch",
    "snapshot-missing", "run-busy", "attempt-running",
    "params-not-recorded", "pairing-source-missing",
    "source-would-be-trimmed", "replay-of-replay", "solver-url-missing",
    "certifier-unavailable", "workdir-not-prepared", "not-a-replay",
    "report-missing", "replayed-from-journal", "not-run-for-replay",
    "artifact-missing",
    # ревью реализации 07.09: коды, показывавшиеся сырыми
    "no-git", "not-solved", "latest-press-registry", "registry-unreadable",
    "json-differs", "text-differs", "bytes-differ", "only-in-source",
    "only-in-replay", "run-unreadable",
]
ERR_CODES = ["storage-off", "env-package", "current-busy",
             "storage-busy", "storage-io", "storage-off.runs-dir"]
ORIGINS = ["manual", "auto-receive", "auto-upload", "adoption"]
ROLES = ["certifier", "source_check_reader", "cdpm"]
REPLAY_VERDICTS = ["same", "same-outcome", "differs", "not-comparable"]
ENV_CATS = ["none", "code", "catalog", "engines", "solver", "certifier",
            "registry", "data_root", "platform", "python", "class"]
REPLAY_CLASSES = ["wall-clock", "deterministic", "deterministic-failed"]
FLOW_RUN_KEYS = ["chip", "none", "uninit", "off", "file", "select", "new",
                 "busy_prev", "all", "implicit"]

# Ключи раздела 11.5 / 17.5 проекта (явный перечень — контракт экрана).
PROJECT_KEYS = {
    "html.tab.cruns", "cruns.h", "cruns.sub", "cruns.help.s", "cruns.help.p",
    "cruns.refresh", "cruns.new", "cruns.new.title", "cruns.new.ph",
    "cruns.create", "cruns.created", "cruns.search.ph", "cruns.search.aria",
    "cruns.sort.title", "cruns.mark", "cruns.mark.off", "cruns.mark.all",
    "cruns.mark.none", "cruns.mark.delete", "cruns.mark.delete.title.none",
    "cruns.mark.delete.title.busy", "cruns.mark.delete.title.current",
    "cruns.dlg.title", "cruns.dlg.msg", "cruns.dlg.ok", "cruns.dlg.cancel",
    "cruns.archived", "cruns.busy", "cruns.current", "cruns.setCurrent",
    "cruns.open", "cruns.card", "cruns.pkg", "cruns.pkg.none",
    "cruns.pkg.lost", "cruns.pkg.launch", "cruns.attempts",
    "cruns.attempts.none", "cruns.checks", "cruns.pass.models",
    "cruns.pass.solvers", "cruns.pass.code", "cruns.pass.catalog",
    "cruns.pass.none", "cruns.pass.old", "cruns.pass.h", "cruns.pass.title",
    "cruns.pass.inputs", "cruns.pass.journals", "cruns.pass.gaps",
    "cruns.pass.trimmed", "cruns.up", "cruns.down", "cruns.footer",
    "cruns.empty", "cruns.empty.btn", "cruns.filtered", "cruns.filtered.hint",
    "cruns.title.edit", "cruns.title.ph", "cruns.storage.uninit",
    "cruns.storage.init", "cruns.storage.done", "cruns.inbox_off", "cruns.env",
    "cruns.att.d1", "cruns.att.wallclock",
    "cruns.replay.journal", "cruns.replay.fresh", "cruns.replay.journal.hint",
    "cruns.replay.dlg.journal.title", "cruns.replay.dlg.journal.msg",
    "cruns.replay.dlg.fresh.title", "cruns.replay.dlg.fresh.msg",
    "cruns.replay.dlg.fresh.d1", "cruns.replay.dlg.ok", "cruns.replay.started",
    "cruns.replay.of.journal", "cruns.replay.of.fresh", "cruns.replay.report.h",
    "cruns.replay.roster.summary", "flow.p.run", "tour.gcruns.title",
    "tour.gcruns.p1",
    # ревью реализации 07.09
    "cruns.runs_dir_off", "cruns.pass.files", "cruns.replay.pairing.cost",
}
PROJECT_KEYS |= {"cruns.origin." + o for o in ORIGINS}
PROJECT_KEYS |= {"cruns.role." + r for r in ROLES}
PROJECT_KEYS |= {"cruns.filter." + b for b in ("all", "new", "work", "done", "fail")}
PROJECT_KEYS |= {"cruns.sort." + k for k in ("order", "created", "name")}
PROJECT_KEYS |= {"cruns.dir." + d for d in ("asc", "desc")}
PROJECT_KEYS |= {"cruns.pkg.src." + s for s in ("channel", "upload", "file", "adoption")}
PROJECT_KEYS |= {"cruns.state." + k for k in STATE_KINDS}
PROJECT_KEYS |= {"cruns.att." + k for k in ("id", "task", "engine", "params", "verdict",
                                              "vmp", "idak", "files", "exchanges", "passport",
                                              "actor")}
PROJECT_KEYS |= {"cruns.note." + c for c in NOTE_CODES}
PROJECT_KEYS |= {"cruns.err." + c for c in ERR_CODES}
PROJECT_KEYS |= {"cruns.replay.verdict." + v for v in REPLAY_VERDICTS}
PROJECT_KEYS |= {"cruns.replay.report." + k for k in ("first", "outcomes", "roster",
                                                        "pairing", "env", "links")}
PROJECT_KEYS |= {"cruns.replay.class." + c for c in REPLAY_CLASSES}
PROJECT_KEYS |= {"cruns.replay.env." + c for c in ENV_CATS}
PROJECT_KEYS |= {"flow.run." + k for k in FLOW_RUN_KEYS}

PLACEHOLDERS = {
    "cruns.filter.all": {"n"}, "cruns.filter.new": {"n"}, "cruns.filter.work": {"n"},
    "cruns.filter.done": {"n"}, "cruns.filter.fail": {"n"},
    "cruns.mark.delete": {"n"}, "cruns.dlg.title": {"n"}, "cruns.pass.trimmed": {"n", "keep"},
    "cruns.storage.uninit": {"n", "sha"}, "cruns.storage.done": {"n"},
    "cruns.title.ph": {"run"}, "cruns.up": {"run"}, "cruns.down": {"run"},
    "cruns.replay.dlg.journal.title": {"id"}, "cruns.replay.dlg.journal.msg": {"run"},
    "cruns.replay.dlg.fresh.title": {"id"}, "cruns.replay.dlg.fresh.msg": {"run"},
    "cruns.replay.started": {"run", "id", "new", "mode"},
    "cruns.replay.of.journal": {"id"}, "cruns.replay.of.fresh": {"id"},
    "cruns.replay.report.h": {"id"},
    "cruns.replay.roster.summary": {"n", "same", "changed", "lost", "gained", "seats",
                                    "seats_same", "seats_other", "un_before", "un_after"},
    "flow.run.busy_prev": {"sha", "task", "progress"},
    "cruns.pass.files": {"n"}, "cruns.replay.pairing.cost": {"delta"},
}


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _fn(source: str, name: str) -> str:
    """Тело функции верхнего уровня: от `function <name>(` до следующего
    объявления функции верхнего уровня."""
    start = source.index("function " + name + "(")
    nxt = re.compile(r"^function \w+\(", re.M).search(source, start + 1)
    return source[start:nxt.start() if nxt else len(source)]


def _values(blocks, lang):
    """Словарь языка со снятыми кавычками JSON."""
    return {k: json.loads(v) for k, v in blocks[lang].items()}


# ------------------------------------------------------------- статика

def test_tab_group_cruns_right_after_flow():
    source = _src()
    m = re.search(r"^var TAB_GROUPS = \[(.*?)^\];", source, re.S | re.M)
    assert m, "TAB_GROUPS не объявлен"
    ids = re.findall(r'\{ id: "(\w+)"', m.group(1))
    assert ids[:2] == ["flow", "cruns"], ids
    assert '{ id: "cruns", i18n: "html.tab.cruns", tabs: ["cruns"] }' in m.group(1)
    labels = re.search(r"^var TAB_LABEL = \{(.*?)^\};", source, re.S | re.M).group(1)
    assert 'cruns: "html.tab.cruns"' in labels
    # стартовый экран остаётся «Конвейер» (слово владельца 28.08)
    init_fn = _fn(source, "init")
    assert 'showTab("flow")' in init_fn
    # обработчики экрана подключаются до первого showTab
    assert "_crunsInit();" in init_fn
    assert init_fn.index("_crunsInit();") < init_fn.index('showTab("flow")')


def test_show_tab_reads_list_and_polls_only_on_screen():
    show = _fn(_src(), "showTab")
    m = re.search(r'if \(name === "cruns"\) \{(.*?)\} else \{(.*?)\}', show, re.S)
    assert m, "ветка cruns в showTab не найдена"
    assert "refreshCosmosRuns()" in m.group(1)
    assert "startCosmosRunsPolling()" in m.group(1)
    assert "stopCosmosRunsPolling()" in m.group(2)
    start = _fn(_src(), "startCosmosRunsPolling")
    assert 'if (_nav.tab !== "cruns") { return; }' in start
    assert "_CRUNS_POLL_MS" in start
    # опрос — только пока у какого-то прогона идёт расчёт (busy)
    assert "some(_crunsRunBusy)" in start


def test_language_switch_redraws_list_and_header_chip():
    source = _src()
    start = source.index('document.addEventListener("crew:lang"')
    handler = source[start:source.index("\n});", start)]
    assert "renderCosmosRuns();" in handler
    assert "_flowRunHeader(_flowState)" in handler


def test_flow_header_carries_run_chip_select_and_hint():
    source = _src()
    render = _fn(source, "renderFlow")
    assert "_flowRunHeader(stateFlow);" in render
    refresh = _fn(source, "refreshFlow")
    assert "_flowRunSelectRefresh();" in refresh
    head = _fn(source, "_flowRunHeader")
    # подсказка по коду сервера, слова — i18n
    for code, key in (("no-current-run", "none"), ("storage-not-initialized", "uninit"),
                      ("storage-off", "off"), ("env-package", "file")):
        assert '"%s": "%s"' % (code, key) in head, code
    assert 't("flow.run." + key)' in head
    # сервер без ключей run/runs_hint_code — ничего не утверждается
    assert 'hasOwnProperty.call(st, "run")' in head
    assert 'hasOwnProperty.call(st, "runs_hint_code")' in head
    # полоса над схемой: «Новый прогон» и «Все прогоны →»; схема не прячется
    assert 't("flow.run.new")' in head and 't("flow.run.all")' in head
    assert 'showTab("cruns")' in head
    assert "busy_previous" in head and 'tf("flow.run.busy_prev"' in head
    # переключение текущего из шапки → схема обновляется сразу, минуя демпфер
    setcur = _fn(source, "_crunsSetCurrent")
    assert "_flowBusyUntil = 0" in setcur and "refreshFlow()" in setcur
    # подпись параметра «прогон COSMOS» у блока ВМП
    labels = re.search(r"^var FLOW_PARAM_LABEL = \{(.*?)^\};", source, re.S | re.M).group(1)
    assert 'run: "flow.p.run"' in labels


def test_index_markup_for_runs_screen_and_flow_header():
    html = INDEX.read_text(encoding="utf-8")
    for el_id in ("panel-cruns", "cruns-storage", "cruns-new-form", "cruns-new-title",
                  "btn-cruns-create", "btn-cruns-refresh", "btn-cruns-new",
                  "cruns-search", "cruns-filters", "btn-cruns-sort", "btn-cruns-dir",
                  "btn-cruns-mark", "btn-cruns-mark-all", "btn-cruns-mark-none",
                  "btn-cruns-delete", "cruns-list", "cruns-footer",
                  "flow-run-chip", "flow-run-select", "btn-flow-run-new", "flow-run-hint"):
        assert 'id="%s"' % el_id in html, el_id
    # панель прогонов — сразу после конвейера, до «Данных»
    assert html.index('id="panel-flow"') < html.index('id="panel-cruns"') \
        < html.index('id="panel-data"')
    # чип и переключатель — в шапке конвейера рядом с чипом активного этапа
    head = html[html.index('id="flow-active-chip"'):html.index('id="btn-flow-all"')]
    assert 'id="flow-run-chip"' in head and 'id="flow-run-select"' in head \
        and 'id="btn-flow-run-new"' in head
    # полоса-подсказка над холстом схемы
    assert html.index('id="flow-run-hint"') < html.index('id="flow-canvas"')
    # ключи статической разметки существуют в ru
    ru = _blocks()["ru"]
    panel = html[html.index('id="panel-cruns"'):html.index('id="panel-data"')]
    for key in re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', panel):
        assert key in ru, key
    # по единообразию — те же кнопки, что в списке CDPM: обновить, новый прогон
    assert 'data-i18n="cruns.refresh"' in panel and 'data-i18n="cruns.new"' in panel


def test_static_calls_project_routes_only():
    source = _src()
    block = source[source.index("================= Прогоны COSMOS (слово владельца 07.09.2026)"):]
    literal = set(re.findall(r'api\("(/cosmos-runs[^"]*)"', block))
    assert literal == {"/cosmos-runs", "/cosmos-runs/current", "/cosmos-runs/order",
                       "/cosmos-runs/archive", "/cosmos-runs/init", "/cosmos-runs/"}, literal
    # список: GET; создание: POST с make_current
    create = _fn(source, "_crunsCreate")
    assert 'api("/cosmos-runs", {' in create and 'method: "POST"' in create
    assert "make_current: !!makeCurrent" in create
    # текущий: PUT {run}
    setcur = _fn(source, "_crunsSetCurrent")
    assert 'api("/cosmos-runs/current", {' in setcur and 'method: "PUT"' in setcur
    assert "JSON.stringify({ run: run })" in setcur
    # порядок: PUT {order}
    move = _fn(source, "_crunsMove")
    assert 'api("/cosmos-runs/order", {' in move and 'method: "PUT"' in move
    assert "JSON.stringify({ order: next })" in move
    # архив: POST {runs}; перед архивом список перечитывается
    delete = _fn(source, "_crunsDelete")
    assert 'api("/cosmos-runs/archive", {' in delete and 'method: "POST"' in delete
    assert "JSON.stringify({ runs: ids })" in delete
    assert delete.index("refreshCosmosRuns()") < delete.index('api("/cosmos-runs/archive"')
    assert 'tf("cruns.archived"' in delete
    # оформление: POST init
    init_st = _fn(source, "_crunsInitStorage")
    assert 'api("/cosmos-runs/init", { method: "POST" })' in init_st
    # название: PUT {title}
    title = _fn(source, "_crunsTitleRow")
    assert '"/title"' in title and 'method: "PUT"' in title
    assert "JSON.stringify({ title: val })" in title
    # повтор: POST {mode}; отчёт: GET replay-report
    replay = _fn(source, "_crunsReplay")
    assert '"/replay"' in replay and 'method: "POST"' in replay
    assert "JSON.stringify({ mode: mode })" in replay
    report = _fn(source, "openCosmosReplayReport")
    assert '"/replay-report"' in report
    # карточка: GET /cosmos-runs/{id}
    load = _fn(source, "_crunsCardLoad")
    assert 'api("/cosmos-runs/" + encodeURIComponent(ctx.run))' in load
    # ошибка ответа (ручка ещё не готова, 404/500, обрыв) — словами, не молча
    refresh = _fn(source, "refreshCosmosRuns")
    assert "_cruns.error = err" in refresh
    storage = _fn(source, "_crunsRenderStorage")
    assert 't("cruns.load.fail")' in storage and "_crunsErrWords(_cruns.error)" in storage
    # никакой другой ручки статика в этом блоке не зовёт: все вызовы api()
    # идут в область /cosmos-runs (обёртка api() сама добавляет /api)
    calls = re.findall(r'\bapi\("([^"]*)"', block)
    # Общий экран «Все прогоны» обеих систем (слово владельца 11.09.2026)
    # стоит подразделом этой же вкладки и зовёт свою ручку — она названа
    # здесь ЯВНО, чтобы страж не пропустил чужой ручки по соседству.
    assert calls and all(c.startswith("/cosmos-runs") or c == "/runs-all"
                         for c in calls), calls
    assert "fetch(" not in block


def test_replay_buttons_dialogs_and_report_window():
    source = _src()
    atts = _fn(source, "_crunsRenderAttempts")
    assert 'cruns-replay-" + mode' in atts
    assert 'cosmosReplayReason(av, runBusy, mode)' in atts
    assert 'tf("cruns.replay.of." + mode' in atts
    assert '"cruns-replay-verdict " + v.cls' in atts
    replay = _fn(source, "_crunsReplay")
    assert 'tf("cruns.replay.dlg." + mode + ".title"' in replay
    assert 'tf("cruns.replay.dlg." + mode + ".msg"' in replay
    assert '"cruns.replay.dlg.fresh.d1" : "cruns.replay.dlg.fresh.wc"' in replay
    assert 't("cruns.replay.dlg.ok")' in replay
    assert 'tf("cruns.replay.started"' in replay
    # 409 — словами сервера, кнопки не трогаются
    assert "_crunsErrWords(e)" in replay
    report = _fn(source, "openCosmosReplayReport")
    for key in ("cruns.replay.report.first", "cruns.replay.report.outcomes",
                "cruns.replay.report.roster", "cruns.replay.report.pairing",
                "cruns.replay.report.env", "cruns.replay.report.links"):
        assert 't("%s")' % key in report, key
    assert "cosmosReplayRosterWords(rep.roster_diff)" in report
    assert "cosmosReplayEnvWords(" in report
    assert 't("cruns.replay.class." + ck)' in report
    # карточка: пока прогон busy — перечитывается раз в 5 с
    card = _fn(source, "openCosmosRunCard")
    assert "_CRUNS_CARD_POLL_MS" in card and "_crunsCardBusy(ctx.doc)" in card
    assert "var _CRUNS_CARD_POLL_MS = 5000;" in source


def test_card_and_report_read_server_forms_and_escape_scopes():
    """Ревью реализации 07.09: карточка читает live_mg.columns (форма
    readiness_state), Escape в поле названия и в верхнем окне стопки не
    закрывает карточку, отчёт читает legit_difference / deterministic_gate.same
    / pairing_diff.total_cost, коды и роли идут через слова i18n, ошибка
    опроса карточки — строкой, не тостом; aria-label полей не зашиты
    по-русски."""
    source = _src()
    pk = _fn(source, "_crunsRenderPackages")
    assert "p.live_mg.columns" in pk and "_crunsLaunchWords(mg, !cols)" in pk
    title = _fn(source, "_crunsTitleRow")
    assert title.count("e.stopPropagation()") >= 2
    modal = _fn(source, "_flowModal")
    assert 'querySelectorAll(".confirm-overlay")' in modal
    report = _fn(source, "openCosmosReplayReport")
    assert "o.legit_difference" in report and "det.same === false" in report
    assert "det.verdict" not in report and "total_cost_delta" not in report
    assert 'tf("cruns.replay.pairing.cost"' in report and "pd.total_cost" in report
    assert "cosmosNoteWords(fd.reason_code)" in report
    passport = _fn(source, "_crunsRenderPassport")
    assert 'tf("cruns.pass.files"' in passport and "cosmosRoleWords(m.role)" in passport
    assert '" f."' not in passport
    card = _fn(source, "_crunsCardRender")
    assert "cosmosOriginWords(doc.origin)" in card
    load = _fn(source, "_crunsCardLoad")
    assert "_crunsCardErrorLine(ctx" in load and "toast(" not in load
    head = _fn(source, "_flowRunHeader")
    assert 't("flow.run.implicit")' in head and "run.error_code" in head
    init = _fn(source, "_crunsInit")
    assert 'else { showTab("cruns"); }' in init
    line = _fn(source, "_crunsPassLine")
    assert "cosmosNoteWords(s.version_code)" in line and "_crunsTaskLabel(s.task)" in line
    assert 'ps.solvers.join' not in line
    storage = _fn(source, "_crunsRenderStorage")
    assert '"cruns.runs_dir_off"' in storage
    err = _fn(source, "_crunsErrToast")
    assert '"cruns.err.storage-off.runs-dir"' in err
    vmp = _fn(source, "flowOpenVmpPanel")
    assert "cosmosNoteWords(cd.note_code, cd.error)" in vmp
    html = INDEX.read_text(encoding="utf-8")
    assert 'aria-label="Название прогона"' not in html
    assert 'aria-label="Поиск прогонов"' not in html


def test_list_card_sort_arrows_marks_and_dialog():
    source = _src()
    card = _fn(source, "_crunsCardNode")
    for key in ("cruns.current", "cruns.open", "cruns.setCurrent", "cruns.card",
                "cruns.busy"):
        assert 't("%s")' % key in card, key
    assert 'tf("cruns.mark.one"' in card
    assert 'tf("cruns.up"' in card and 'tf("cruns.down"' in card
    assert 'class: "cruns-pill " + bucket' in card and '" stalled"' in card
    assert "openCosmosRunCard(r.run)" in card
    # стрелки — только ручной порядок по возрастанию без фильтра и поиска
    can = _fn(source, "_crunsCanReorder")
    assert 's.key === "order" && s.dir === "asc" && _cruns.bucket === "all"' in can
    # порядок сортировки хранится в localStorage под ключом проекта
    assert 'var _CRUNS_SORT_KEY = "cosmos.cruns.sort.v1";' in source
    load = _fn(source, "_crunsLoadSort")
    assert 'return { key: "order", dir: "asc" };' in load
    # диалог удаления — .confirm-overlay через appConfirm, кнопка «В архив»
    delete = _fn(source, "_crunsDelete")
    assert "appConfirm(" in delete and 'ok: t("cruns.dlg.ok")' in delete
    assert "danger: true" in delete
    # кнопка удаления недоступна с причиной: нет отметок / занят / текущий
    tb = _fn(source, "_crunsRenderToolbar")
    for key in ("cruns.mark.delete.title.none", "cruns.mark.delete.title.busy",
                "cruns.mark.delete.title.current"):
        assert key in tb, key
    # создание: успех → тост и переход на «Конвейер»; 409 current-busy →
    # предложение создать без переключения
    create = _fn(source, "_crunsCreate")
    assert 'showTab("flow")' in create
    assert '_crunsErrCode(e) === "current-busy"' in create
    assert 't("cruns.create.keep")' in create


def test_i18n_keys_present_in_all_five_languages():
    blocks = _blocks()
    used = set(re.findall(r'\bt[f]?\("((?:cruns|flow\.run|tour\.gcruns)\.[\w.\-]+)"', _src()))
    used |= set(re.findall(r'\bt[f]?\("(flow\.p\.run)"', _src()))
    # префиксы динамических ключей (t("cruns.state." + kind)) — не ключи
    used = {k for k in used if not k.endswith(".")}
    missing_ru = {k for k in used if k not in blocks["ru"]}
    assert not missing_ru, missing_ru
    for lang in LANGS:
        missing = PROJECT_KEYS - set(blocks[lang])
        assert not missing, f"{lang}: нет ключей {sorted(missing)}"
        assert "cruns.note.next-queue" not in blocks[lang], lang
    ph = re.compile(r"\{(\w+)\}")
    own = {k for k in blocks["ru"] if k.startswith(("cruns.", "flow.run.", "tour.gcruns."))
           or k == "flow.p.run"}
    for key in own | PROJECT_KEYS:
        ru = set(ph.findall(blocks["ru"][key]))
        if key in PLACEHOLDERS:
            assert ru == PLACEHOLDERS[key], (key, ru)
        for lang in LANGS:
            assert key in blocks[lang], (lang, key)
            assert set(ph.findall(blocks[lang][key])) == ru, (lang, key)
    # подписи вкладок внутри языка не повторяются (страж паритета делает то
    # же — здесь названо для новой вкладки явно)
    for lang in LANGS:
        vals = _values(blocks, lang)
        tabs = [v for k, v in vals.items() if k.startswith("html.tab.")]
        assert tabs.count(vals["html.tab.cruns"]) == 1, lang


def test_legacy_tab_renamed_to_job_history_in_five_languages():
    blocks = _blocks()
    for lang in LANGS:
        vals = _values(blocks, lang)
        new_label = vals["html.tab.cruns"]
        old_label = vals["html.tab.runs"]
        assert old_label != new_label, lang
        assert old_label == vals["html.history.h"], lang
        for key in ("learn.c_opt.s5", "learn.c_opt.s8", "learn.ch.c_opt.title",
                    "learn.tp.s5", "tour.gopt.i2"):
            assert new_label not in vals[key], (lang, key)
        assert old_label in vals["learn.c_opt.s5"], lang


def test_tour_step_gcruns_after_gflow_with_titles_in_five_languages():
    src = ONBOARDING.read_text(encoding="utf-8")
    m = re.search(r"^var TOUR_STEPS = \[(.*?)^\];", src, re.S | re.M)
    assert m
    keys = re.findall(r'\{ key: "(\w+)"', m.group(1))
    assert keys.index("gcruns") == keys.index("gflow") + 1, keys
    # 10.09.2026 (СХУП, шаг 3): второй абзац шага — секция «Дерево
    # прогонов» на карточке прогона; его содержание судит
    # tests/test_webapp_static_run_tree.py, здесь — только состав шага
    assert '{ key: "gcruns", group: "cruns", paras: ["p1", "p2"] }' in m.group(1)
    blocks = _blocks()
    for lang in LANGS:
        for key in keys:
            assert "tour.%s.title" % key in blocks[lang], (lang, key)
        vals = _values(blocks, lang)
        # тур называет обе возможности повтора
        p1 = vals["tour.gcruns.p1"]
        assert "CDPM" in p1, lang


def test_actor_shown_under_attempt_id_and_in_package_state_line():
    """Кто нажал (10.09.2026) — в двух местах статики.

    (1) Таблица попыток: актор идёт строкой ПОД идентификатором, а не
    одиннадцатой колонкой. Шапка таблицы не переносится по словам
    (правило .cruns-attempts th в style.css), десять колонок уже тесны,
    а ячейка попытки и без того собирает подстроки — время, «повтор
    чего», чип вердикта повтора. Записи прежних версий актора не несут:
    строки тогда просто нет, прочерк не выдумывается.

    (2) Строка состояния страницы пакета: рядом с состоянием стоят
    актор и пометка «линию держит другой живой процесс службы»
    (`holder_note` ответа `GET /api/task-package/current/press`) —
    иначе при чужом живом держателе страница показывала бы просто
    «идёт», не называя, чьё нажатие идёт.
    """
    source = _src()
    atts = _fn(source, "_crunsRenderAttempts")
    # (1) строка живёт в ячейке попытки
    assert re.search(r"if \(rec\.actor\) \{\s*idCell\.appendChild\(", atts), atts[:200]
    assert 't("cruns.att.actor") + ": " + String(rec.actor)' in atts
    # шапка осталась о десяти колонках — новой колонки таблица не получила
    head = re.search(r"\[([^\]]*)\]\.forEach", atts, re.S)
    assert head, "массив шапки таблицы попыток не найден"
    assert "cruns.att.actor" not in head.group(1), "актор — не колонка"
    assert head.group(1).count("cruns.att.") == 10, head.group(1)
    # (2) строка состояния страницы пакета
    st_line = source[source.index('(st.progress ? " · " + String(st.progress)'):]
    st_line = st_line[:st_line.index(";")]
    assert '(st.state && st.actor ? " · " + String(st.actor) : "")' in st_line, st_line
    assert '(st.holder_note ? " — " + String(st.holder_note) : "")' in st_line, st_line


def test_stylesheet_classes():
    css = STYLE.read_text(encoding="utf-8")
    for sel in (".cruns-list", ".cruns-card", ".cruns-card.current", ".cruns-pill",
                ".cruns-pill.stalled", ".cruns-arrows", ".cruns-check",
                ".cruns-attempts", ".cruns-passport", ".cruns-storage",
                ".cruns-replay-journal", ".cruns-replay-fresh", ".cruns-replay-of",
                ".cruns-replay-verdict.same", ".cruns-replay-verdict.differs",
                ".cruns-replay-verdict.none", ".cruns-replay-report",
                ".flow-run-chip", ".flow-run-select", ".flow-run-hint"):
        assert sel in css, sel
    # янтарная пилюля «остановился», не зелёная
    m = re.search(r"\.cruns-pill\.stalled\s*\{([^}]*)\}", css)
    assert m and "--warn" in m.group(1)


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


def _const(src, name):
    m = re.search(r"^var %s = .*?;\s*$" % re.escape(name), src, re.S | re.M)
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
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(i18nPath, 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(helpers, 'utf8'), sandbox);
const res = vm.runInContext(JSON.parse(fs.readFileSync(process.env.CREW_Q,
                                                       'utf8')), sandbox);
process.stdout.write(JSON.stringify(res));
"""

_FUNCS = ("fmt", "cosmosRunsBucket", "cosmosRunsStateLabel", "cosmosNoteWords",
          "cosmosOriginWords", "cosmosRoleWords",
          "cosmosRunsSortCompare", "cosmosRunsHay", "cosmosRunsFilter",
          "cosmosReplayVerdictLabel", "cosmosReplayReason",
          "cosmosReplayRosterWords", "cosmosReplayEnvWords")
_CONSTS = ("CRUNS_STATE_KINDS", "CRUNS_BUCKETS", "CRUNS_SORT_KEYS",
           "CRUNS_REPLAY_VERDICTS")


def _node(tmp_path, expr, lang):
    node = shutil.which("node")
    if not node:
        pytest.skip("node недоступен")
    src = _src()
    helpers = tmp_path / "helpers.js"
    helpers.write_text(
        "\n".join([_const(src, c) for c in _CONSTS] +
                  [_grab(src, n) for n in _FUNCS]),
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
  var runs = [
    { run: "run-0001", title: "Бета", position: 2,
      created_at: "2026-09-07T10:00:00+03:00", state_kind: "done",
      package: { sha12: "989c11c8b0bc", source_run: "run-0087" } },
    { run: "run-0002", title: null, position: 0, created_at: "bad-date",
      state_kind: "working" },
    { run: "run-0003", title: "альфа", position: 1,
      created_at: "2026-09-06T10:00:00+03:00", state_kind: "stalled" }
  ];
  function sorted(key, dir) {
    return runs.slice().sort(function (a, b) {
      return cosmosRunsSortCompare(a, b, { key: key, dir: dir });
    }).map(function (r) { return r.run; });
  }
  out.order_asc = sorted("order", "asc");
  out.order_desc = sorted("order", "desc");
  out.created_asc = sorted("created", "asc");
  out.created_desc = sorted("created", "desc");
  out.name_asc = sorted("name", "asc");
  out.name_desc = sorted("name", "desc");
  out.default_sort = sorted(undefined, undefined);
  out.tie = cosmosRunsSortCompare({ run: "run-0002", position: 0 },
                                  { run: "run-0001", position: 0 },
                                  { key: "order", dir: "asc" }) > 0;
  out.buckets = CRUNS_STATE_KINDS.map(cosmosRunsBucket);
  out.bucket_unknown = cosmosRunsBucket("weird");
  out.labels = CRUNS_STATE_KINDS.map(cosmosRunsStateLabel);
  out.label_unknown = [cosmosRunsStateLabel("weird"), cosmosRunsStateLabel(null)];
  out.note_known = [cosmosNoteWords("run-busy"), t("cruns.note.run-busy")];
  out.note_words = cosmosNoteWords("params-not-recorded", "spent_limit_s, moves");
  out.note_unknown = cosmosNoteWords("no-such-code");
  out.note_empty = cosmosNoteWords(null);
  out.note_only_words = cosmosNoteWords(null, "слова сервера");
  out.filter_bucket = cosmosRunsFilter(runs, "", "done").map(function (r) { return r.run; });
  out.filter_query = cosmosRunsFilter(runs, "  БЕТА ", "all").map(function (r) { return r.run; });
  out.filter_sha = cosmosRunsFilter(runs, "989c11c8", "all").map(function (r) { return r.run; });
  out.filter_state = cosmosRunsFilter(runs, t("cruns.state.working"), "all")
    .map(function (r) { return r.run; });
  out.filter_none = cosmosRunsFilter(runs, "нет такого", "all").length;
  out.filter_all = cosmosRunsFilter(runs, "", "all").length;
  out.filter_null = cosmosRunsFilter(null, "", "all").length;
  out.verdict_same = cosmosReplayVerdictLabel({ verdict: "same" });
  out.verdict_outcome = cosmosReplayVerdictLabel({ verdict: "same-outcome" });
  var long = new Array(121).join("x");
  out.verdict_differs = cosmosReplayVerdictLabel({ verdict: "differs", words: long });
  out.verdict_short = cosmosReplayVerdictLabel({ verdict: "differs", words: "abc" });
  out.verdict_none = cosmosReplayVerdictLabel(null);
  out.verdict_nc = cosmosReplayVerdictLabel({ verdict: "not-comparable", words: "w" });
  out.reason_busy = cosmosReplayReason({ available: true }, true, "journal");
  out.reason_ok_journal = cosmosReplayReason({ available: true }, false, "journal");
  out.reason_ok_fresh = cosmosReplayReason({ available: true }, false, "fresh");
  out.reason_no = cosmosReplayReason({ available: false,
    reason_code: "source-would-be-trimmed", reason_words: "CREW_ATTEMPTS_KEEP=20" },
    false, "fresh");
  out.reason_unknown_code = cosmosReplayReason({ available: false, reason_code: "zzz" },
    false, "journal");
  out.reason_missing = cosmosReplayReason(null, false, "journal");
  // форма roster_diff отчёта (vmp/run/replay_compare.roster_diff)
  out.roster = cosmosReplayRosterWords({ employees_total: 42, unchanged: 30,
    changed: 12, lost: 18, gained: 18, only_in_source: 0, only_in_replay: 0,
    seats: { total: 120, same_employee: 96, other_employee: 24, filled_now: 0,
             emptied_now: 0, deadhead_changed: 0, unassigned_before: 0,
             unassigned_after: 0 },
    words: "слова сервера",
    employees: [{ employee: "E1", status: "changed", lost: 2, gained: 2 }] });
  out.roster_expect = tf("cruns.replay.roster.summary", { n: 42, same: 30, changed: 12,
    lost: 18, gained: 18, seats: 120, seats_same: 96, seats_other: 24,
    un_before: 0, un_after: 0 });
  out.roster_missing = cosmosReplayRosterWords({ employees_total: 3, employees: [] });
  out.roster_null = cosmosReplayRosterWords(null);
  out.note_dup = cosmosNoteWords("params-not-recorded",
    t("cruns.note.params-not-recorded") + ": mode, moves");
  out.origin = ["manual", "auto-receive", "auto-upload", "adoption", "weird", null]
    .map(cosmosOriginWords);
  out.role = ["certifier", "source_check_reader", "cdpm:extractor", "other", ""]
    .map(cosmosRoleWords);
  out.env_none = cosmosReplayEnvWords([]);
  out.env_undef = cosmosReplayEnvWords(undefined);
  out.env = cosmosReplayEnvWords(["code.commit",
    { key: "panel.data_root", source: "C:/a", replay: "D:/b" },
    "certifier.model", "journal.versions.solver.build.app_jar_sha256",
    "reproducibility.class", "something.else"]);
  out.words = {
    same: t("cruns.replay.verdict.same"), differs: t("cruns.replay.verdict.differs"),
    nc: t("cruns.replay.verdict.not-comparable"), hint: t("cruns.replay.journal.hint"),
    busy: t("cruns.note.run-busy"), noavail: t("cruns.note.no-availability"),
    trimmed: t("cruns.note.source-would-be-trimmed"),
    env_none: t("cruns.replay.env.none"), env_code: t("cruns.replay.env.code"),
    env_root: t("cruns.replay.env.data_root"), env_cert: t("cruns.replay.env.certifier"),
    env_solver: t("cruns.replay.env.solver"), env_class: t("cruns.replay.env.class")
  };
  return out;
})()
"""


@pytest.mark.parametrize("lang", list(LANGS))
def test_node_sort_buckets_labels_notes_and_replay_words(tmp_path, lang):
    r = _node(tmp_path, _CASES, lang)
    vals = _values(_blocks(), lang)
    # ручной порядок: position; NaN-дата → 0 (самая ранняя); обратный порядок
    # — зеркало; при равенстве — по номеру
    assert r["order_asc"] == ["run-0002", "run-0003", "run-0001"]
    assert r["order_desc"] == list(reversed(r["order_asc"]))
    assert r["created_asc"] == ["run-0002", "run-0003", "run-0001"]
    assert r["created_desc"] == list(reversed(r["created_asc"]))
    assert r["default_sort"] == r["order_asc"]
    assert r["tie"] is True
    # имя: title || run, без регистра, по правилам языка: «альфа» раньше «Бета»
    assert set(r["name_asc"]) == {"run-0001", "run-0002", "run-0003"}
    assert r["name_asc"].index("run-0003") < r["name_asc"].index("run-0001")
    assert r["name_desc"] == list(reversed(r["name_asc"]))
    # девять состояний → четыре корзины
    assert r["buckets"] == [BUCKET_OF[k] for k in STATE_KINDS]
    assert r["bucket_unknown"] is None
    assert r["labels"] == [vals["cruns.state." + k] for k in STATE_KINDS]
    assert r["label_unknown"] == ["weird", "—"]
    # слова к кодам пробелов
    assert r["note_known"][0] == r["note_known"][1]
    assert r["note_words"] == vals["cruns.note.params-not-recorded"] + " — spent_limit_s, moves"
    assert r["note_unknown"] == "no-such-code"
    assert r["note_empty"] == ""
    assert r["note_only_words"] == "слова сервера"
    # фильтр: корзина, подстрока без регистра, sha пакета, слова состояния
    assert r["filter_bucket"] == ["run-0001", "run-0003"]
    assert r["filter_query"] == ["run-0001"]
    assert r["filter_sha"] == ["run-0001"]
    assert "run-0002" in r["filter_state"]
    assert r["filter_none"] == 0 and r["filter_all"] == 3 and r["filter_null"] == 0
    # чип вердикта повтора
    assert r["verdict_same"] == {"cls": "same", "text": r["words"]["same"], "title": ""}
    assert r["verdict_outcome"]["cls"] == "same"
    assert r["verdict_outcome"]["text"] == vals["cruns.replay.verdict.same-outcome"]
    assert r["verdict_differs"]["cls"] == "differs"
    assert r["verdict_differs"]["text"] == r["words"]["differs"] + ": " + "x" * 90 + "…"
    assert r["verdict_differs"]["title"] == "x" * 120
    assert r["verdict_short"]["text"] == r["words"]["differs"] + ": abc"
    assert r["verdict_none"]["cls"] == "none" and r["verdict_none"]["text"] == r["words"]["nc"]
    assert r["verdict_nc"]["text"] == r["words"]["nc"] + ": w"
    # кнопки повтора: занятый прогон гасит обе; доступная «из журнала»
    # предупреждает о проверке полноты; недоступная — код словами плюс
    # уточнение сервера; без данных сервера — честная причина
    assert r["reason_busy"] == {"enabled": False, "title": r["words"]["busy"]}
    assert r["reason_ok_journal"] == {"enabled": True, "title": r["words"]["hint"]}
    assert r["reason_ok_fresh"] == {"enabled": True, "title": ""}
    assert r["reason_no"]["enabled"] is False
    assert r["reason_no"]["title"] == r["words"]["trimmed"] + " — CREW_ATTEMPTS_KEEP=20"
    assert r["reason_unknown_code"] == {"enabled": False, "title": "zzz"}
    assert r["reason_missing"] == {"enabled": False, "title": r["words"]["noavail"]}
    # сводка назначений по сотрудникам — числа подставлены из формы
    # roster_diff сервера; массив employees и объект seats не печатаются
    assert r["roster"] == r["roster_expect"] and "42" in r["roster"] and "96" in r["roster"]
    assert "[object Object]" not in r["roster"]
    assert "3" in r["roster_missing"] and "—" in r["roster_missing"]
    assert "[object Object]" not in r["roster_missing"]
    assert r["roster_null"] == ""
    # уточнение сервера, начинающееся словами кода, не дублируется
    assert r["note_dup"] == vals["cruns.note.params-not-recorded"] + ": mode, moves"
    # происхождение прогона и роли моделей — словами; неизвестное — как есть
    assert r["origin"] == [vals["cruns.origin." + o] for o in ORIGINS] + ["weird", "—"]
    assert r["role"] == [vals["cruns.role.certifier"], vals["cruns.role.source_check_reader"],
                         vals["cruns.role.cdpm"] + " (extractor)", "other", "?"]
    # среда
    assert r["env_none"] == [r["words"]["env_none"]] and r["env_undef"] == r["env_none"]
    env = r["env"]
    assert env[0].startswith(r["words"]["env_code"]) and "code.commit" in env[0]
    assert env[1].startswith(r["words"]["env_root"]) and env[1].endswith("C:/a → D:/b")
    assert env[2].startswith(r["words"]["env_cert"])
    assert env[3].startswith(r["words"]["env_solver"])
    assert env[4].startswith(r["words"]["env_class"])
    assert env[5].startswith("something.else")


# ------------------------------------------------------------- документы

def _need(text: str, marker: str, doc: Path):
    if marker not in text:
        pytest.skip("маркер «%s» в %s ещё не написан (дорожка «Документы», "
                    "раздел 14 проекта)" % (marker, doc.name))


def test_manual_03_names_current_run_section_and_job_history():
    text = MANUAL_03.read_text(encoding="utf-8")
    for marker in ("**Текущий прогон.**", "12я", "История задач"):
        _need(text, marker, MANUAL_03)


def test_manual_09_names_storage_layout_and_env_vars():
    text = MANUAL_09.read_text(encoding="utf-8")
    for marker in ("cosmos-runs", "CREW_COSMOS_RUNS_DIR", "CREW_ATTEMPTS_KEEP",
                   "CREW_LLM_EXCHANGES"):
        _need(text, marker, MANUAL_09)
