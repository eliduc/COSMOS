# -*- coding: utf-8 -*-
"""Карточка «Запись о прогоне» в статике COSMOS (проект
final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md, разделы 2, 6, 10 пп. 3–4 и
11.4; словарь полей — `vmp/run_record.py`).

Стенд судит ТЕКСТ файлов статики (образец tests/test_webapp_static_cosmos_runs.py):
браузера здесь нет, живых служб — тоже.

  1. Секция стоит в двух местах и собирается одним рисовальщиком: в панели
     постановки НАД блоком пакета и расчёта и на карточке прогона во
     вкладке «Прогоны».
  2. Статика зовёт ровно три ручки записи (`/run/current`, `/run/declare`,
     `/run/stitch`) и ручку карточки `/cosmos-runs/{id}/record`; прочие
     пути `/run…` — только ручки дерева прогонов шага 3, и они судятся
     своим стендом (tests/test_webapp_static_run_tree.py).
  3. Голова, журнал, итоги, сшивка, скачивание и форма объявления читают
     поля контракта; расход моделей показан В ТОКЕНАХ, канал без учёта —
     словами «не считается», а не нулём.
  4. Отказ нажатия `run-not-declared` — словами и ссылкой «объявить
     прогон», а не безымянной строкой ошибки.
  5. Слова целей, исходов и состояний покрывают ВЕСЬ словарь
     `vmp/run_record.py`: разойдись они — запись говорила бы одно, а
     карточка другое.
  6. Ключи `flow.vmp.record.*` и `cruns.record.*` есть во всех пяти
     словарях с одинаковыми подстановками; шаг тура о карточке — тоже.
  7. Классы `.run-rec-*` объявлены в style.css.
"""
from __future__ import annotations

import copy
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

from tests.test_i18n_parity import LANGS, _blocks  # noqa: E402
from vmp import run_record as rr  # noqa: E402

STATIC = HERE / "webapp" / "static"
APP_JS = STATIC / "app.js"
I18N = STATIC / "i18n.js"
STYLE = STATIC / "style.css"
ONBOARDING = STATIC / "onboarding.js"

PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Ручки записи по контракту раздела 10 проекта. Обёртка api() сама
# добавляет /api.
RUN_ENDPOINTS = {"/run/current", "/run/declare", "/run/stitch"}

# Ручки дерева прогонов (раздел 12.2 проекта, шаг 3): их адреса тоже
# начинаются на /run, поэтому перечень путей области /run с сентября
# 2026 года — объединение двух наборов. Состав ручек дерева судит
# tests/test_webapp_static_run_tree.py; здесь они названы, чтобы страж
# «никаких лишних путей /run» остался стражем, а не был снят.
TREE_ENDPOINTS = {"/run/current/tree", "/run-tree", "/run-tree/compare",
                  "/run-tree/rebuild", "/runs-all"}

# Коды отказов ручки объявления (раздел 10, п. 1 проекта) и код гейта.
DECLARE_CODES = ["already-declared", "package-missing", "declaration-invalid"]
GATE_CODE = "run-not-declared"

# Имена систем журнала: чей этап показан строкой таблицы.
SYSTEMS = ["cdpm", "runner", "cosmos"]

# Площадки объявления — те же слова, что в словаре записи.
CONTOURS = list(rr.CONTOURS)

# Ключи карточки (пространство проекта) — явный перечень: контракт экрана.
PROJECT_KEYS = {
    "flow.vmp.record.h", "flow.vmp.record.title", "flow.vmp.record.loading",
    "flow.vmp.record.fail", "flow.vmp.record.nopkg", "flow.vmp.record.norecord",
    "flow.vmp.record.head", "flow.vmp.record.run_id", "flow.vmp.record.owner",
    "flow.vmp.record.owner.cosmos", "flow.vmp.record.owner.holder",
    "flow.vmp.record.by", "flow.vmp.record.purpose", "flow.vmp.record.contour",
    "flow.vmp.record.airline", "flow.vmp.record.pkg", "flow.vmp.record.root",
    "flow.vmp.record.root.words", "flow.vmp.record.lineage",
    "flow.vmp.record.lineage.words", "flow.vmp.record.catalog",
    "flow.vmp.record.bindings", "flow.vmp.record.models",
    "flow.vmp.record.models_note",
    "flow.vmp.record.solvers", "flow.vmp.record.build", "flow.vmp.record.limit",
    "flow.vmp.record.code", "flow.vmp.record.code.words",
    "flow.vmp.record.stages", "flow.vmp.record.stop_after",
    "flow.vmp.record.journal", "flow.vmp.record.journal.none",
    "flow.vmp.record.j.actor", "flow.vmp.record.dur.words",
    "flow.vmp.record.tokens.words", "flow.vmp.record.tokens.none",
    "flow.vmp.record.solver_s", "flow.vmp.record.results",
    "flow.vmp.record.state", "flow.vmp.record.verdicts",
    "flow.vmp.record.deliveries", "flow.vmp.record.timing",
    "flow.vmp.record.timing.words", "flow.vmp.record.golden",
    "flow.vmp.record.golden.yes", "flow.vmp.record.golden.no",
    "flow.vmp.record.stitch", "flow.vmp.record.stitch.mode",
    "flow.vmp.record.stitch.mode.remote", "flow.vmp.record.stitch.mode.local",
    "flow.vmp.record.stitch.mode.none", "flow.vmp.record.stitch.counts",
    "flow.vmp.record.stitch.words", "flow.vmp.record.stitch.err",
    "flow.vmp.record.stitch.retry", "flow.vmp.record.stitch.done",
    "flow.vmp.record.download", "flow.vmp.record.declare.h",
    "flow.vmp.record.declare.by", "flow.vmp.record.declare.by.ph",
    "flow.vmp.record.declare.models", "flow.vmp.record.declare.solvers",
    "flow.vmp.record.declare.confirm", "flow.vmp.record.declare.btn",
    "flow.vmp.record.declare.ok", "flow.vmp.record.declare.need_by",
    "flow.vmp.record.declare.need_confirm",
    "flow.vmp.record.declare.only_panel", "flow.vmp.record.goto",
    "cruns.record.h", "cruns.record.title", "cruns.record.fail",
    "tour.gflow.p2",
}
PROJECT_KEYS |= {"flow.vmp.record.j." + k for k in
                 ("stage", "substage", "started", "duration", "outcome",
                  "words", "tokens", "system")}
PROJECT_KEYS |= {"flow.vmp.record.sys." + s for s in SYSTEMS}
PROJECT_KEYS |= {"flow.vmp.record.contour." + c for c in CONTOURS}
PROJECT_KEYS |= {"flow.vmp.record.err." + c for c in DECLARE_CODES}
PROJECT_KEYS |= {"flow.vmp.record.reason." + GATE_CODE}

# Подстановки строк со значениями: набор имён обязан совпадать в пяти
# языках (общий страж — tests/test_i18n_parity), здесь назван состав.
PLACEHOLDERS = {
    "flow.vmp.record.root.words": {"rules", "answers"},
    "flow.vmp.record.lineage.words": {"pkg", "src"},
    "flow.vmp.record.code.words": {"cdpm", "cosmos"},
    "flow.vmp.record.limit": {"n"},
    "flow.vmp.record.stop_after": {"stage"},
    "flow.vmp.record.dur.words": {"m", "h"},
    "flow.vmp.record.tokens.words": {"role", "model", "input", "output", "calls"},
    "flow.vmp.record.solver_s": {"n"},
    "flow.vmp.record.timing.words": {"m", "h", "total"},
    "flow.vmp.record.golden.yes": {"by", "at"},
    "flow.vmp.record.stitch.mode.remote": {"api"},
    "flow.vmp.record.stitch.words": {"sent", "waiting", "refused"},
    "flow.vmp.record.stitch.done": {"sent", "waiting", "refused"},
    "flow.vmp.record.declare.ok": {"run"},
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
    return {k: json.loads(v) for k, v in blocks[lang].items()}


# ------------------------------------------------------------- разметка

def test_section_stands_above_the_package_block_of_the_vmp_panel():
    """Проект, раздел 10, п. 4: карточка прогона — НАД блоком пакета и
    попыток. Сначала видно, какой прогон объявлен и чем, потом — что в
    нём нажимали."""
    panel = _fn(_src(), "flowOpenVmpPanel")
    assert "vmpRecordBlock(dlg);" in panel
    assert panel.index("vmpRecordBlock(dlg);") < panel.index('t("flow.vmp.pkg")')
    # блок расчёта живёт в карточке пакета — значит и он ниже записи
    assert panel.index("vmpRecordBlock(dlg);") < panel.index("vmpRunBlock(pkgBody")
    # закрытие панели снимает живой блок: сигналы не идут в снятую карточку
    assert "_vmpRecord = null;" in panel


def test_runs_card_carries_the_same_section():
    """Раздел 11.4: «Запись о прогоне» рядом с паспортом на карточке
    прогона; ошибка чтения — строкой карточки, а не тостом на опрос."""
    source = _src()
    card = _fn(source, "_crunsCardRender")
    assert 't("cruns.record.h")' in card and 't("cruns.record.title")' in card
    assert "_crunsRenderRecord(ctx, secRec);" in card
    assert card.index("_crunsRenderRecord") < card.index("_crunsRenderPassport")
    load = _fn(source, "_crunsRecordLoad")
    assert 't("cruns.record.fail")' in load
    assert "_crunsCardErrorLine(ctx, words)" in load
    assert "toast(" not in load
    # объявлять прогон с карточки нельзя, повтор сшивки — только текущему
    assert "declare: false" in load
    assert "stitch: !!(ctx.doc && ctx.doc.current)" in load


def test_one_painter_serves_both_places():
    source = _src()
    render = _fn(source, "runRecordRender")
    for part in ("_runRecHead", "_runRecJournal", "_runRecResults",
                 "_runRecStitch", "_runRecFoot"):
        assert part + "(" in render, part
    # пакета нет / прогон не объявлен / записи нет — три разных ответа
    assert 't("flow.vmp.record.nopkg")' in render
    assert "_runRecDeclare(host, doc, opts)" in render
    assert 't("flow.vmp.record.norecord")' in render
    # слова сервера показываются, а не проглатываются
    assert "(doc.words || [])" in render
    block = _fn(source, "vmpRecordBlock")
    assert "declare: true, stitch: true" in block


# --------------------------------------------------------------- ручки

def test_static_calls_only_the_three_run_endpoints():
    source = _src()
    calls = set(re.findall(r'\bapi\("(/run[^"]*)"', source))
    assert calls == RUN_ENDPOINTS | TREE_ENDPOINTS, calls
    block = _fn(source, "vmpRecordBlock")
    assert 'api("/run/current")' in block
    declare = _fn(source, "_runRecDeclare")
    assert 'api("/run/declare", {' in declare and 'method: "POST"' in declare
    for field in ("purpose:", "contour:", "created_by:", "models:", "solvers:",
                  "planned_stages:"):
        assert field in declare, field
    stitch = _fn(source, "_runRecStitch")
    assert 'api("/run/stitch", { method: "POST" })' in stitch
    card = _fn(source, "_crunsRecordLoad")
    assert 'api("/cosmos-runs/" + encodeURIComponent(ctx.run) + "/record")' in card
    assert "fetch(" not in _fn(source, "runRecordRender")


def test_head_journal_results_read_contract_fields():
    source = _src()
    head = _fn(source, "_runRecHead")
    for field in ("d.run_id", "head.record_id", "head.owner", "d.created_by",
                  "d.created_at", "d.purpose", "d.contour", "d.airline",
                  "doc.package_sha", "ln.root_sha256", "ln.root_answers_sha256",
                  "ln.package_sha256", "ln.source_run", "d.catalog",
                  "d.bindings", "d.models", "d.models_note", "d.solvers",
                  "code.cdpm_commit",
                  "code.cosmos_commit", "d.planned_stages", "d.stop_after"):
        assert field in head, field
    # Система модели (`declaration.models[].system`) читается ТЕМ ЖЕ
    # словарём, что система единицы журнала: второго перечня имён систем
    # в интерфейсе нет.
    model = _fn(source, "_runRecModelWords")
    assert "m.system" in model and "_runRecSysWords(m.system)" in model
    # Неполнота раскладки (`declaration.models_note`) — ПОМЕТКОЙ
    # предупреждения, тем же оформлением, что прочие предупреждения
    # карточки (.vmp-note.run-rec-bad).
    assert 't("flow.vmp.record.models_note")' in head
    assert 'class: "vmp-note run-rec-bad"' in head
    entries = _fn(source, "_runRecEntries")
    # журнал обеих систем: голова хозяина + ещё не сшитый фрагмент COSMOS,
    # повтор по ключу не задваивается, порядок — по времени
    assert "(rec && rec.journal)" in entries and "doc.cosmos" in entries
    assert "entry.system" in entries and "seen[key]" in entries
    assert "Date.parse" in entries
    journal = _fn(source, "_runRecJournal")
    for field in ("e.stage", "e.substage", "e.started_at", "e.outcome",
                  "e.reason_code", "e.words", "e.actor"):
        assert field in journal, field
    assert "_runRecDuration(e)" in journal and "_runRecTokens(e)" in journal
    dur = _fn(source, "_runRecDuration")
    assert "duration_machine_s" in dur and "duration_waiting_human_s" in dur
    res = _fn(source, "_runRecResults")
    for field in ("r.state", "r.verdicts", "r.deliveries", "r.timing",
                  "r.golden", "tm.machine_s", "tm.waiting_human_s",
                  "tm.total_s", "g.confirmed", "g.by", "g.at", "g.words"):
        assert field in res, field
    # признак образца — ТОЛЬКО показ: кнопки, ставящей его, здесь нет
    assert "api(" not in res


def test_model_spend_is_shown_in_tokens_and_uncounted_channel_says_so():
    """Решение владельца № 1 (10.09.2026): расход — в токенах по каждой
    модели, денег нет; канал без учёта говорит «не считается» с причиной,
    а не показывает нуль."""
    tokens = _fn(_src(), "_runRecTokens")
    for field in ("m.input_tokens", "m.output_tokens", "m.calls",
                  "m.counted === false", "m.note", "res.solver_seconds"):
        assert field in tokens, field
    assert 't("flow.vmp.record.tokens.none")' in tokens
    for money in ("cost", "price", "usd", "$"):
        assert money not in tokens.lower(), money
    # Причина считается ОДИН раз и приписывается ОБЕИМ веткам строки:
    # сосчитанной и несосчитанной. Две развилки печатали бы причину
    # только у одной из них — так и было у сосчитанного нуля из кэша.
    assert tokens.count("m.note ?") == 1, "причина считается не одним местом"
    tail = tokens.split("m.counted === false", 1)[1]
    assert tail.count("+ why") == 2, "причина не у обеих веток строки"


def test_stitch_state_and_download_of_the_record():
    source = _src()
    stitch = _fn(source, "_runRecStitch")
    for field in ("s.mode", "s.record_api", "s.sent", "s.waiting", "s.refused",
                  "s.last_error", "s.last_wait"):
        assert field in stitch, field
    # Ждёт и отказано — РАЗНЫЕ строки карточки: несостоявшаяся отправка
    # не окрашивается красным (класс отказа — только у last_error).
    assert 't("flow.vmp.record.stitch.wait")' in stitch
    assert 'run-rec-bad' in stitch.split('s.last_wait')[0]
    assert 't("flow.vmp.record.stitch.retry")' in stitch
    # кнопка повтора — только там, где ручка действует
    assert "opts.stitch && mode === \"remote\"" in stitch
    dl = _fn(source, "_runRecDownload")
    assert "JSON.stringify(rec, null, 2)" in dl
    assert 'download: "run-record-"' in dl
    assert "URL.revokeObjectURL" in dl


def test_declaration_form_confirms_environment_and_says_refusals_in_words():
    source = _src()
    form = _fn(source, "_runRecDeclare")
    assert "_runRecReasonWords(doc.declare_reason_code" in form
    # карточка чужого прогона называет, где кнопка, а не рисует вторую
    assert 't("flow.vmp.record.declare.only_panel")' in form
    for field in ("form.purposes", "form.contours", "form.models",
                  "form.solvers", "form.planned_stages"):
        assert field in form, field
    # имя объявляющего и подтверждение среды обязательны
    assert 't("flow.vmp.record.declare.need_by")' in form
    assert 't("flow.vmp.record.declare.need_confirm")' in form
    assert "check.checked" in form
    assert 'tf("flow.vmp.record.declare.ok"' in form
    assert "_runRecErrLines(errBox, e)" in form
    err = _fn(source, "_runRecErrLines")
    assert 'b.detail' in err and "b.code" in err and "b.words" in err
    assert '_runRecKeyWords("flow.vmp.record.err.", code)' in err


def test_press_refusal_of_undeclared_run_is_words_and_a_link():
    """Гейт от пакета: 409 с кодом `run-not-declared` — слова отказа и
    ссылка «объявить прогон», а не безымянная строка."""
    source = _src()
    run_block = _fn(source, "vmpRunBlock")
    press = run_block[run_block.index("function press(task, pairingRunId)"):
                      run_block.index("btnP.onclick =")]
    assert "_runRecRefusal(e)" in press
    assert 't("flow.vmp.record.goto")' in press
    assert "_runRecScrollTo(false)" in press
    assert "_crunsErrWords(e)" in press
    # прежняя строка «[object Object]» из detail-объекта здесь не пишется
    assert "String((e && e.body && e.body.detail)" not in press
    refusal = _fn(source, "_runRecRefusal")
    assert '"%s"' % GATE_CODE in refusal
    assert "b.detail" in refusal and "b.words" in refusal
    scroll = _fn(source, "_runRecScrollTo")
    assert '.run-rec-sec' in scroll and "flowOpenVmpPanel()" in scroll


def test_record_block_is_reread_after_each_press_and_outcome():
    """Обновление блока идёт вместе с опросом блока расчёта, но по
    ИЗМЕНЕНИЮ подписи: иначе карточка перечитывалась бы каждые три
    секунды опроса."""
    source = _src()
    assert "vmpRecordSignal(st);" in _fn(source, "vmpRunBlock")
    signal = _fn(source, "vmpRecordSignal")
    assert "st.attempts" in signal and "_vmpRecord.sig" in signal
    assert "_vmpRecord.reload()" in signal


# ---------------------------------------------------- словарь значений

def test_words_cover_the_whole_dictionary_of_the_record_module():
    """Слова целей, исходов и состояний берутся из `vmp/run_record.py`:
    разойдись перечни — файл записи говорил бы одно, а карточка другое."""
    source = _src()
    for name, values in (("RUN_REC_PURPOSES", rr.PURPOSES),
                         ("RUN_REC_OUTCOMES", rr.OUTCOMES),
                         ("RUN_REC_STATES", rr.STATES)):
        block = re.search(r"var %s = \{(.*?)\};" % name, source, re.S).group(1)
        named = set(re.findall(r'"([^"]+)":\s*"', block))
        missing = set(values) - named
        assert not missing, "%s: нет слов %s" % (name, sorted(missing))
    # состояние «не объявлен» — своё, его в словаре записи нет: запись
    # ещё не родилась, а карточка обязана это сказать
    states = re.search(r"var RUN_REC_STATES = \{(.*?)\};", source, re.S).group(1)
    assert '"не объявлен"' in states
    ru = _values(_blocks(), "ru")
    for value in rr.PURPOSES:
        code = re.search(r'"%s": "(\w+)"' % re.escape(value), source).group(1)
        assert ru["flow.vmp.record.purpose." + code] == value, value
    for value in rr.OUTCOMES:
        code = re.search(r'"%s": "(\w+)"' % re.escape(value), source).group(1)
        assert ru["flow.vmp.record.outcome." + code] == value, value


# ----------------------------------------------------------- пять языков

def test_keys_exist_in_all_five_languages_with_the_same_placeholders():
    blocks = _blocks()
    for lang in LANGS:
        vals = _values(blocks, lang)
        missing = sorted(k for k in PROJECT_KEYS if k not in vals)
        assert not missing, "%s: нет ключей %s" % (lang, missing[:10])
        for key, names in PLACEHOLDERS.items():
            assert set(PLACEHOLDER.findall(vals[key])) == names, (lang, key)
        for key in PROJECT_KEYS:
            assert vals[key].strip(), "%s/%s: пустой перевод" % (lang, key)


def test_every_key_used_by_the_card_exists_in_russian():
    source = _src()
    ru = _blocks()["ru"]
    used = set(re.findall(r'\bt\("((?:flow\.vmp\.record|cruns\.record)[^"]*)"',
                          source))
    used |= set(re.findall(r'\btf\("((?:flow\.vmp\.record|cruns\.record)[^"]*)"',
                           source))
    # ключи семейств собираются кодом («…j.» + имя колонки) — судится не
    # приставка, а сами семейства (перечнем PROJECT_KEYS выше)
    used = {k for k in used if not k.endswith(".")}
    assert used, "карточка не зовёт ни одного ключа записи"
    missing = sorted(k for k in used if k not in ru)
    assert not missing, missing


def test_tour_tells_about_the_record_card():
    """Чек-лист синхронизации витрин, пункт «тур»: тур уже описывает
    панель постановки и кнопки расчёта, значит обязан назвать и карточку
    записи (пять языков)."""
    steps = ONBOARDING.read_text(encoding="utf-8")
    m = re.search(r'\{ key: "gflow", group: "flow", paras: \[([^\]]*)\] \}', steps)
    assert m, "шаг gflow тура не найден"
    assert '"p1", "p2"' in m.group(1), m.group(1)
    blocks = _blocks()
    for lang in LANGS:
        text = _values(blocks, lang)["tour.gflow.p2"]
        assert len(text) > 80, lang
        assert "COSMOS" in text and "CDPM" in text, lang
        # прежний абзац не тронут: страж перетаскивания судит p1
        assert "zip" in _values(blocks, lang)["tour.gflow.p1"], lang


# ------------------------------------------------------------ оформление

def test_style_declares_the_record_classes():
    css = STYLE.read_text(encoding="utf-8")
    for cls in (".run-rec-sub", ".run-rec-list", ".run-rec-table",
                ".run-rec-bad", ".run-rec-reason", ".run-rec-input",
                ".run-rec-confirm", ".run-rec-goto", ".run-rec-flash",
                ".cruns-record"):
        assert cls in css, cls
    # арабский справа налево держится логическими свойствами, а не
    # отдельными правилами [dir="rtl"] (образец соседних секций)
    tail = css[css.index("/* Запись о прогоне"):]
    assert "padding-inline-start" in tail
    assert "padding-left" not in tail and "margin-left" not in tail


# ---------------------------------------------------------------- node-часть
# Слова карточки собираются НЕ шаблоном страницы, а функциями app.js, и
# судить их текстом файла мало: перепутанный словарь или потерянный
# «не считается» текстом не видны. Функции исполняются настоящим node в
# песочнице с живым i18n.js — по разу на каждый из пяти языков (образец
# node-стенда tests/test_webapp_static_cosmos_runs.py). Разметки здесь
# нет: берутся только те функции, которым DOM не нужен.

def _grab(src: str, name: str) -> str:
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


def _const(src: str, name: str) -> str:
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

_FUNCS = ("fmt", "cosmosRoleWords", "_crunsTaskLabel", "_runRecWords",
          "_runRecPurposeWords", "_runRecOutcomeWords", "_runRecStateWords",
          "_runRecOwnerWords", "_runRecKeyWords", "_runRecContourWords",
          "_runRecSysWords", "_runRecReasonWords", "_runRecShort",
          "_runRecNum", "_runRecModelWords", "_runRecSolverWords",
          "_runRecEntries", "_runRecDuration", "_runRecTokens",
          "_runRecRefusal")
_CONSTS = ("RUN_REC_PURPOSES", "RUN_REC_OUTCOMES", "RUN_REC_STATES",
           "RUN_REC_OWNERS", "RUN_REC_OUTCOME_CLS")


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
        env={**os.environ, "CREW_Q": str(q)}, check=True)
    return json.loads(out.stdout)


_CASES = r"""
(function () {
  var out = {};
  out.purposes = %(purposes)s.map(_runRecPurposeWords);
  out.outcomes = %(outcomes)s.map(_runRecOutcomeWords);
  out.states = %(states)s.map(_runRecStateWords);
  out.contours = %(contours)s.map(_runRecContourWords);
  out.systems = ["cdpm", "runner", "cosmos"].map(_runRecSysWords);
  out.owners = ["cosmos", "панель/раннер по манифесту", "кто-то ещё", null]
    .map(_runRecOwnerWords);
  out.unknown = [_runRecPurposeWords("шалость"), _runRecOutcomeWords(""),
                 _runRecStateWords(null), _runRecContourWords("остров")];
  out.reason = _runRecReasonWords("run-not-declared");
  out.reason_unknown = _runRecReasonWords("такого-кода-нет");
  out.short = [_runRecShort("0123456789abcdef0123"), _runRecShort(null),
               _runRecNum(0), _runRecNum(null)];
  out.tokens_counted = _runRecTokens({ resources: { models: [
    { role: "certifier", model: "м-1", calls: 3,
      input_tokens: 100, output_tokens: 20 }], solver_seconds: 42 } });
  out.tokens_uncounted = _runRecTokens({ resources: { models: [
    { role: "source_check_reader", model: "м-2", counted: false,
      note: "канал usage не отдаёт" }] } });
  out.tokens_cached = _runRecTokens({ resources: { models: [
    { role: "source_check_reader", model: "м-3", counted: true, calls: 0,
      input_tokens: 0, output_tokens: 0,
      note: "чтение из кэша: модель не вызывалась" }] } });
  out.tokens_none = _runRecTokens({});
  out.duration = [_runRecDuration({ duration_machine_s: 7,
                                    duration_waiting_human_s: 900 }),
                  _runRecDuration({})];
  out.model = _runRecModelWords({ role: "certifier", provider: "п",
                                  model: "м", channel: "к", effort: "у" });
  out.model_systems = ["cdpm", "runner", "cosmos"].map(function (s) {
    return _runRecModelWords({ role: "certifier", provider: "п", model: "м",
                               channel: "к", effort: "у", system: s });
  });
  out.solver = _runRecSolverWords({ task: "pairing", engine: "дв",
    version: "1.2", build_sha256: "abcdef0123456789", spent_limit_s: 60,
    moves: "chain-seed", seed: 7 });
  var doc = { cosmos: { entries: [
    { stage: "нажатие", started_at: "2026-09-10T10:00:00+03:00" },
    { stage: "сверка", started_at: "2026-09-10T12:00:00+03:00" }] } };
  var rec = { journal: [
    { stage: "заморозка", started_at: "2026-09-10T09:00:00+03:00",
      system: "cdpm" },
    { stage: "нажатие", started_at: "2026-09-10T10:00:00+03:00",
      system: "cosmos" }] };
  out.entries = _runRecEntries(doc, rec).map(function (r) {
    return [r.e.stage, r.system].join("/");
  });
  out.refusal = [
    (_runRecRefusal({ body: { detail: { code: "run-not-declared",
                                        words: "слова гейта" } } }) || {}).words,
    (_runRecRefusal({ body: { code: "run-not-declared",
                              words: ["а", "б"] } }) || {}).words,
    _runRecRefusal({ body: { code: "already-declared" } }),
    _runRecRefusal({ status: 0, message: "нет связи" })];
  return out;
})()
"""


@pytest.fixture(scope="module")
def _cases() -> str:
    return _CASES % {
        "purposes": json.dumps(list(rr.PURPOSES), ensure_ascii=False),
        "outcomes": json.dumps(list(rr.OUTCOMES), ensure_ascii=False),
        "states": json.dumps(list(rr.STATES), ensure_ascii=False),
        "contours": json.dumps(CONTOURS, ensure_ascii=False),
    }


@pytest.mark.parametrize("lang", LANGS)
def test_node_words_of_the_record_card(tmp_path, _cases, lang):
    got = _node(tmp_path, _cases, lang)
    vals = _values(_blocks(), lang)
    src = _src()

    def code_of(value):
        return re.search(r'"%s": "(\w+)"' % re.escape(value), src).group(1)

    assert got["purposes"] == [vals["flow.vmp.record.purpose." + code_of(p)]
                               for p in rr.PURPOSES], lang
    assert got["outcomes"] == [vals["flow.vmp.record.outcome." + code_of(o)]
                               for o in rr.OUTCOMES], lang
    assert got["states"] == [vals["flow.vmp.record.state." + code_of(s)]
                             for s in rr.STATES], lang
    assert got["contours"] == [vals["flow.vmp.record.contour." + c]
                               for c in CONTOURS], lang
    assert got["systems"] == [vals["flow.vmp.record.sys." + s] for s in SYSTEMS]
    assert got["owners"][0] == vals["flow.vmp.record.owner.cosmos"], lang
    assert got["owners"][1] == vals["flow.vmp.record.owner.holder"], lang
    # незнакомое слово сервера показывается как есть, пустое — прочерком
    assert got["owners"][2] == "кто-то ещё" and got["owners"][3] == "—"
    assert got["unknown"] == ["шалость", "—", "—", "остров"], lang
    assert got["reason"] == vals["flow.vmp.record.reason.run-not-declared"]
    assert got["reason_unknown"] == "такого-кода-нет"
    assert got["short"] == ["0123456789ab", "—", "0", "—"]
    # расход — в токенах; канал без учёта говорит «не считается» с причиной
    assert "100" in got["tokens_counted"] and "20" in got["tokens_counted"]
    assert "42" in got["tokens_counted"]
    assert vals["flow.vmp.record.tokens.none"] in got["tokens_uncounted"], lang
    assert "канал usage не отдаёт" in got["tokens_uncounted"]
    # Сосчитанный НОЛЬ (читатель ИДАК на проверках из кэша) — числами и
    # с причиной: «вход 0 → выход 0, вызовов 0» без причины читалось бы
    # «расход потеряли», а модель просто не звали.
    assert "(чтение из кэша: модель не вызывалась)" in got["tokens_cached"], \
        got["tokens_cached"]
    assert vals["flow.vmp.record.tokens.none"] \
        not in got["tokens_cached"], lang
    assert "0" in got["tokens_cached"]
    assert got["tokens_none"] == "—"
    # время машины и время ожидания человека — порознь; ни одного нет — прочерк
    assert "7" in got["duration"][0] and "900" in got["duration"][0]
    assert got["duration"][1] == "—"
    assert got["model"].endswith("п/м · к · у"), got["model"]
    # Система модели — словом того же словаря, что система единицы
    # журнала; поля нет — строка выглядит как прежде (записи, объявленные
    # до договора двух систем, остаются годными).
    assert got["model_systems"] == [
        got["model"] + " [" + vals["flow.vmp.record.sys." + s] + "]"
        for s in SYSTEMS], (lang, got["model_systems"])
    assert vals["flow.vmp.record.build"] in got["solver"], lang
    assert "abcdef012345" in got["solver"] and "seed 7" in got["solver"]
    # журнал обеих систем: повтор по ключу не задваивается, порядок — по
    # времени, у записи фрагмента система проставляется «cosmos»
    assert got["entries"] == ["заморозка/cdpm", "нажатие/cosmos",
                              "сверка/cosmos"], got["entries"]
    assert got["refusal"][0] == "слова гейта"
    assert got["refusal"][1] == "а б"
    assert got["refusal"][2] is None and got["refusal"][3] is None


# Рисовальщики карточки под ЗАГЛУШКОЙ DOM: текст файла не видит ни
# `appendChild` от потерянного узла, ни забытую зависимость — а живой
# службы у стенда нет и быть не должно. Узлы подменены объектами, `api`
# и `Blob` — пустышками; судится, что каждый путь ответа рисуется без
# исключения и даёт непустой текст.
_RENDER_FUNCS = ("el", "keyActivate", "fmt", "localeTag", "fmtTime", "toast",
                 "cosmosRoleWords", "_crunsTaskLabel", "_crunsErrWords",
                 "_runRecWords", "_runRecPurposeWords", "_runRecOutcomeWords",
                 "_runRecStateWords", "_runRecOwnerWords", "_runRecKeyWords",
                 "_runRecContourWords", "_runRecSysWords", "_runRecReasonWords",
                 "_runRecShort", "_runRecNum", "_runRecRow", "_runRecRows",
                 "_runRecModelWords", "_runRecSolverWords", "_runRecHead",
                 "_runRecEntries", "_runRecDuration", "_runRecTokens",
                 "_runRecJournal", "_runRecResults", "_runRecStitch",
                 "_runRecDownload", "_runRecFoot", "_runRecErrLines",
                 "_runRecDeclare", "runRecordRender")

_RENDER_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const [i18nPath, helpers, lang] = process.argv.slice(2);
function node(tag) {
  return {
    tag: tag, children: [], attrs: {}, classList: { add() {}, remove() {} },
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
const sandbox = {
  console, setTimeout, clearTimeout,
  localStorage: { getItem: () => lang, setItem() {} },
  Blob: function () {},
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  document: {
    createElement: node, createTextNode: (t) => ({ textContent: t }),
    addEventListener() {}, querySelectorAll: () => [], querySelector: () => null,
    dispatchEvent() {}, documentElement: { setAttribute() {} }, body: node('body')
  },
  $: () => node('div'),
  api: () => Promise.resolve({})
};
sandbox.globalThis = sandbox; sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(i18nPath, 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(helpers, 'utf8'), sandbox);
const cases = JSON.parse(fs.readFileSync(process.env.CREW_Q, 'utf8'));
function walk(n, out) {
  if (n && n.textContent) { out.push(String(n.textContent)); }
  (n && n.children || []).forEach(function (c) { walk(c, out); });
  return out;
}
const out = {};
Object.keys(cases).forEach(function (name) {
  const host = node('div');
  try {
    sandbox.runRecordRender(host, cases[name].doc, cases[name].opts);
    out[name] = walk(host, []).filter(Boolean);
  } catch (e) { out[name] = { error: String(e && e.stack || e) }; }
});
try { sandbox._runRecDownload(cases['полная запись'].doc); out._download = 'ok'; }
catch (e) { out._download = String(e); }
process.stdout.write(JSON.stringify(out));
"""

_FULL_DOC = {
    "present": True, "package_sha": "a" * 64, "declared": True,
    "head": {"record_id": "20260910-120000-abcd",
             "owner": "панель/раннер по манифесту", "customer": "а/к",
             "record_api": "https://панель.invalid/api"},
    "words": ["слово сервера"],
    "record": {
        "schema": "cosmos-run-record/v1",
        "declaration": {
            "run_id": "run-0001", "created_at": "2026-09-10T09:00:00+03:00",
            "created_by": "кто-то", "purpose": "приёмка",
            "contour": {"name": "local"}, "airline": {"name": "а/к"},
            "catalog": {"name": "кат", "version": "0.4.0",
                        "fingerprint": "b" * 64},
            "bindings": {"catalog_version": "0.4.0",
                         "catalog_fingerprint": "c" * 64},
            "models": [{"role": "certifier", "model": "м", "provider": "п",
                        "channel": "к", "effort": "у"}],
            "solvers": [{"task": "pairing", "engine": "дв",
                         "spent_limit_s": 60, "build_sha256": "d" * 64}],
            "code": {"cdpm_commit": "e" * 40, "cosmos_commit": "f" * 40},
            "planned_stages": ["пейринг", "ростер"], "stop_after": "ростер",
            "lineage": {"root_sha256": "1" * 64, "root_answers_sha256": "2" * 64,
                        "package_sha256": "3" * 64, "source_run": "run-0120"}},
        "declaration_sha256": "4" * 64,
        "journal": [{"stage": "нажатие", "substage": "att-1",
                     "started_at": "2026-09-10T10:00:00+03:00",
                     "duration_machine_s": 61, "duration_waiting_human_s": 0,
                     "outcome": "успех", "reason_code": None, "words": "готово",
                     "actor": "user:кто-то", "system": "cosmos",
                     "artifacts": [], "counters": {},
                     "resources": {"models": [
                         {"role": "certifier", "model": "м", "calls": 1,
                          "input_tokens": 10, "output_tokens": 2},
                         {"role": "source_check_reader", "model": "м2",
                          "counted": False, "note": "канал не отдаёт"},
                         {"role": "source_check_reader", "model": "м3",
                          "counted": True, "calls": 0, "input_tokens": 0,
                          "output_tokens": 0, "note": "чтение из кэша"}],
                         "solver_seconds": 60}}],
        "results": {"verdicts": {"pairing": "OK"},
                    "deliveries": [{"kind": "csv", "path": "p",
                                    "sha256": "5" * 64}],
                    "comparison": {}, "golden": {"confirmed": False},
                    "state": "идёт",
                    "timing": {"machine_s": 61, "waiting_human_s": 0,
                               "total_s": 61}}},
    "cosmos": {"environment": {},
               "entries": [{"stage": "сверка",
                            "started_at": "2026-09-10T11:00:00+03:00",
                            "outcome": "предупреждение", "resources": {}}]},
    "stitch": {"mode": "remote", "record_api": "https://панель.invalid/api",
               "sent": 2, "waiting": 1, "refused": 0, "last_error": None,
               "last_wait": "хозяин записи недоступен — запись ждёт повтора"},
    "state": "идёт", "form": {},
}

_UNDECLARED_DOC = {
    "present": True, "declared": False,
    "declare_reason_code": "run-not-declared", "words": ["слова гейта"],
    "form": {"purposes": ["рабочий", "приёмка"],
             "contours": ["local", "test"],
             "models": _FULL_DOC["record"]["declaration"]["models"],
             "solvers": _FULL_DOC["record"]["declaration"]["solvers"],
             "planned_stages": ["пейринг"]},
}

# Голова, чеканенная по договору двух систем (11.09.2026): строка модели
# называет СВОЮ систему (`system`), а неполнота раскладки — словами
# (`models_note`). Прежняя голова (`_FULL_DOC`) этих полей не несёт и
# выглядит как раньше — оба вида судятся одним прогоном.
_SYSTEMS_DOC = copy.deepcopy(_FULL_DOC)
_SYSTEMS_DOC["record"]["declaration"]["models"] = [
    {"role": "certifier", "model": "м", "provider": "п", "channel": "к",
     "effort": "у", "system": "cosmos"},
    {"role": "compiler", "model": "м2", "provider": "п", "channel": "к",
     "system": "cdpm"}]
_SYSTEMS_DOC["record"]["declaration"]["models_note"] = \
    "COSMOS не опрошен при объявлении"

_RENDER_CASES = {
    "полная запись": {"doc": _FULL_DOC,
                      "opts": {"declare": True, "stitch": True}},
    "голова двух систем": {"doc": _SYSTEMS_DOC,
                           "opts": {"declare": False, "stitch": False}},
    "карточка прогона": {"doc": _FULL_DOC,
                         "opts": {"declare": False, "stitch": False}},
    "пакета нет": {"doc": {"present": False, "words": []},
                   "opts": {"declare": True}},
    "не объявлен (панель)": {"doc": _UNDECLARED_DOC, "opts": {"declare": True}},
    "не объявлен (карточка)": {"doc": {"present": True, "declared": False,
                                       "form": {}},
                               "opts": {"declare": False}},
    "объявлен, записи нет": {"doc": {"present": True, "declared": True,
                                     "record": None, "stitch": {"mode": "none"}},
                             "opts": {"declare": True, "stitch": True}},
    "пустой ответ": {"doc": {"present": True, "declared": True, "record": {},
                             "stitch": {}},
                     "opts": {"declare": True, "stitch": True}},
}


def _node_render(tmp_path, lang):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node недоступен")
    src = _src()
    helpers = tmp_path / "render-helpers.js"
    helpers.write_text(
        "\n".join([_const(src, c) for c in _CONSTS] +
                  [_grab(src, n) for n in _RENDER_FUNCS]),
        encoding="utf-8")
    runner = tmp_path / "render.js"
    runner.write_text(_RENDER_HARNESS, encoding="utf-8")
    q = tmp_path / "cases.json"
    q.write_text(json.dumps(_RENDER_CASES, ensure_ascii=False), encoding="utf-8")
    out = subprocess.run(
        [node_bin, str(runner), str(I18N), str(helpers), lang],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CREW_Q": str(q)}, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("lang", LANGS)
def test_node_card_renders_every_answer_of_the_contract(tmp_path, lang):
    got = _node_render(tmp_path, lang)
    vals = _values(_blocks(), lang)
    for name in _RENDER_CASES:
        assert isinstance(got[name], list), (name, got[name])
        assert got[name], "%s: карточка пуста" % name
    assert got["_download"] == "ok", got["_download"]
    full = " · ".join(got["полная запись"])
    # слово сервера, голова, журнал, итоги и сшивка — все на месте
    assert "слово сервера" in full
    assert "run-0001" in full and "run-0120" in full
    assert vals["flow.vmp.record.head"] in full, lang
    assert vals["flow.vmp.record.journal"] in full, lang
    assert vals["flow.vmp.record.results"] in full, lang
    assert vals["flow.vmp.record.stitch"] in full, lang
    assert vals["flow.vmp.record.download"] in full, lang
    assert vals["flow.vmp.record.golden.no"] in full, lang
    assert vals["flow.vmp.record.tokens.none"] in full, lang
    # причина стоит у ОБЕИХ строк расхода: и у несосчитанной, и у
    # сосчитанного нуля читателя ИДАК (проверки взяты из кэша)
    assert "(канал не отдаёт)" in full and "(чтение из кэша)" in full, lang
    # отпечатки коротки, полные шестидесятичетырёхзначные не печатаются
    assert "b" * 64 not in full and "1" * 64 not in full
    # Голова по договору двух систем: у КАЖДОЙ модели названа её система
    # словом того же словаря, что у единицы журнала, а неполнота
    # раскладки стоит отдельной строкой под перечнем моделей.
    both = " · ".join(got["голова двух систем"])
    for s in ("cosmos", "cdpm"):
        assert "[" + vals["flow.vmp.record.sys." + s] + "]" in both, (lang, s)
    assert vals["flow.vmp.record.models_note"] in both, lang
    assert "COSMOS не опрошен при объявлении" in both
    # Голова БЕЗ этих полей — прежний вид: ни системы у модели, ни строки
    # оговорки (записи, объявленные до договора, остаются годными).
    assert vals["flow.vmp.record.models_note"] not in full, lang
    assert "[" + vals["flow.vmp.record.sys.cosmos"] + "]" not in full, lang
    # кнопка повтора сшивки — только там, где ручка действует
    assert vals["flow.vmp.record.stitch.retry"] in full, lang
    card = " · ".join(got["карточка прогона"])
    assert vals["flow.vmp.record.stitch.retry"] not in card, lang
    # пакета нет — одна честная строка, без головы
    nopkg = " · ".join(got["пакета нет"])
    assert nopkg == vals["flow.vmp.record.nopkg"], lang
    # не объявлен: причина словами и форма; на карточке — только причина
    form = " · ".join(got["не объявлен (панель)"])
    assert vals["flow.vmp.record.reason.run-not-declared"] in form, lang
    assert vals["flow.vmp.record.declare.h"] in form, lang
    assert vals["flow.vmp.record.declare.btn"] in form, lang
    other = " · ".join(got["не объявлен (карточка)"])
    assert vals["flow.vmp.record.declare.only_panel"] in other, lang
    # второй формы на карточке нет — названо, где кнопка (подпись кнопки
    # входит в эти самые слова, поэтому судятся заголовок и отметка)
    assert vals["flow.vmp.record.declare.h"] not in other, lang
    assert vals["flow.vmp.record.declare.confirm"] not in other, lang
    # объявление есть, а записи нет — сказано словами, и видна сшивка
    norec = " · ".join(got["объявлен, записи нет"])
    assert vals["flow.vmp.record.norecord"] in norec, lang
    assert vals["flow.vmp.record.stitch.mode.none"] in norec, lang
