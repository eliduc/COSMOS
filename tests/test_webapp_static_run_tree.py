# -*- coding: utf-8 -*-
"""Секция «Дерево прогонов» в статике COSMOS (проект
final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md, разделы 7, 6 п. 3 и 12;
ядро дерева — `vmp/run_tree.py`, форма индекса — `vmp/run_tree.schema.json`).

Стенд судит ТЕКСТ файлов статики и исполняет её рисовальщики настоящим
node в песочнице (образец tests/test_webapp_static_run_record.py):
браузера здесь нет, живых служб — тоже.

  1. Секция стоит в двух местах и собирается одним рисовальщиком: в
     панели постановки ПОД записью о прогоне и на карточке прогона во
     вкладке «Прогоны» рядом с записью.
  2. Статика зовёт ровно ручки дерева по контракту раздела 12.2
     (`/run/current/tree`, `/cosmos-runs/{id}/tree`, `/run-tree/compare`,
     `/run-tree/rebuild`, `/cosmos-runs/{id}/golden` и снятие признака) —
     и никаких других.
  3. Слова рёбер и различий взяты из `vmp/run_tree.py` (`EDGES` —
     4 слова, `DIFFERS` — 10 слов, `COMPARE_*` — 3 слова): разойдись
     перечни — индекс говорил бы одно, а карточка другое.
  4. Путь от корня, таблица соседних ветвей и сравнение читают поля
     контракта; расход моделей показан В ТОКЕНАХ, канал без учёта —
     словами «не считается»; счётчики помечены «справочно».
  5. Кнопка «Золотой прогон» есть ТОЛЬКО у завершённого прогона площадки
     `prod` без признака (решение владельца 6.3 от 10.09.2026: признак
     ставится словом владельца после приёмки на Prod); слова
     подтверждения обязательны, личность подтвердившего ставит сервер.
  6. Пустой индекс и необъявленная запись — разные честные слова.
  7. Ключи `flow.vmp.tree.*` и `cruns.tree.*` есть во всех пяти словарях
     с одинаковыми подстановками; шаг тура о вкладке «Прогоны» — тоже.
  8. Классы `.run-tree-*` объявлены в style.css.
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

from tests.test_i18n_parity import LANGS, _blocks  # noqa: E402
from vmp import run_record as rr  # noqa: E402
from vmp import run_tree as rt  # noqa: E402

STATIC = HERE / "webapp" / "static"
APP_JS = STATIC / "app.js"
I18N = STATIC / "i18n.js"
STYLE = STATIC / "style.css"
ONBOARDING = STATIC / "onboarding.js"

PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Ручки дерева по контракту раздела 12.2 проекта. Обёртка api() сама
# добавляет /api; ручки прогона зовутся со вставленным номером, поэтому
# их адреса судятся отдельно (по телу функций).
TREE_ENDPOINTS = {"/run/current/tree", "/run-tree", "/run-tree/compare",
                  "/run-tree/rebuild"}

# Ручка общего экрана «Все прогоны» обеих систем (слово владельца
# 11.09.2026): её адрес тоже начинается на /run, и страж области обязан
# знать о ней.
RUNS_ALL_ENDPOINT = "/runs-all"

# Коды отказов ручки золотого прогона (раздел 12.2 проекта).
GOLDEN_CODES = ["golden-not-prod", "golden-not-finished",
                "golden-words-empty", "golden-forwarded-refused"]

# Колонки таблицы соседних ветвей (раздел 12.3 проекта).
SIB_COLUMNS = ["run", "who", "contour", "differs", "verdicts", "timing",
               "tokens", "golden"]

# Колонки таблиц сравнения.
CMP_COLUMNS = ["field", "this", "other", "delta"]

# Номера прогонов стенда — вымышленные: живого номера площадки в тесте
# быть не должно.
RUN_SELF, RUN_SIB, RUN_GOLD = "run-0001", "run-0002", "run-0003"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _fn(source: str, name: str) -> str:
    """Тело функции верхнего уровня: от `function <name>(` до следующего
    объявления функции верхнего уровня."""
    start = source.index("function " + name + "(")
    nxt = re.compile(r"^function \w+\(", re.M).search(source, start + 1)
    return source[start:nxt.start() if nxt else len(source)]


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


def _map(name: str) -> dict:
    """Словарь «слово сервера → код перевода» из app.js."""
    block = re.search(r"var %s = \{(.*?)\};" % re.escape(name), _src(),
                      re.S).group(1)
    return dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))


def _values(blocks, lang):
    return {k: json.loads(v) for k, v in blocks[lang].items()}


EDGE_CODES = sorted(set(_map("RUN_TREE_EDGES").values()))
DIFFER_CODES = sorted(set(_map("RUN_TREE_DIFFERS").values()))
KIND_CODES = sorted(set(_map("RUN_TREE_KINDS").values()))

# Ключи секции (пространство проекта) — явный перечень: контракт экрана.
PROJECT_KEYS = {
    "flow.vmp.tree.h", "flow.vmp.tree.title", "flow.vmp.tree.loading",
    "flow.vmp.tree.fail", "flow.vmp.tree.empty", "flow.vmp.tree.norecord",
    "flow.vmp.tree.path", "flow.vmp.tree.path.none", "flow.vmp.tree.root",
    "flow.vmp.tree.root.words", "flow.vmp.tree.root.docs",
    "flow.vmp.tree.root.docs.words", "flow.vmp.tree.node.words",
    "flow.vmp.tree.self", "flow.vmp.tree.differs.none",
    "flow.vmp.tree.siblings", "flow.vmp.tree.siblings.none",
    "flow.vmp.tree.tok.words", "flow.vmp.tree.tok.row",
    "flow.vmp.tree.tok.partial", "flow.vmp.tree.time.words",
    "flow.vmp.tree.cmp", "flow.vmp.tree.cmp.head",
    "flow.vmp.tree.cmp.other_root", "flow.vmp.tree.cmp.decl",
    "flow.vmp.tree.cmp.decl.none", "flow.vmp.tree.cmp.same",
    "flow.vmp.tree.cmp.results", "flow.vmp.tree.cmp.verdicts",
    "flow.vmp.tree.cmp.timing", "flow.vmp.tree.cmp.tokens",
    "flow.vmp.tree.cmp.counters", "flow.vmp.tree.cmp.counters.note",
    "flow.vmp.tree.cmp.ref", "flow.vmp.tree.cmp.equal",
    "flow.vmp.tree.cmp.differs", "flow.vmp.tree.cmp.only",
    "flow.vmp.tree.cmp.none.golden", "flow.vmp.tree.cmp.none.previous",
    "flow.vmp.tree.pick.golden", "flow.vmp.tree.pick.previous",
    "flow.vmp.tree.pick.sibling", "flow.vmp.tree.pick.need",
    "flow.vmp.tree.expect", "flow.vmp.tree.expect.none",
    "flow.vmp.tree.golden", "flow.vmp.tree.golden.state",
    "flow.vmp.tree.golden.yes", "flow.vmp.tree.golden.no",
    "flow.vmp.tree.golden.btn", "flow.vmp.tree.golden.h",
    "flow.vmp.tree.golden.words", "flow.vmp.tree.golden.words.ph",
    "flow.vmp.tree.golden.need_words", "flow.vmp.tree.golden.send",
    "flow.vmp.tree.golden.ok", "flow.vmp.tree.golden.unmark",
    "flow.vmp.tree.golden.unmarked", "flow.vmp.tree.golden.only_prod",
    "flow.vmp.tree.rebuild", "flow.vmp.tree.rebuild.done",
    "cruns.tree.h", "cruns.tree.title", "cruns.tree.fail",
    "tour.gcruns.p2",
}
PROJECT_KEYS |= {"flow.vmp.tree.s." + k for k in SIB_COLUMNS}
PROJECT_KEYS |= {"flow.vmp.tree.cmp." + k for k in CMP_COLUMNS}
PROJECT_KEYS |= {"flow.vmp.tree.edge." + c for c in EDGE_CODES}
PROJECT_KEYS |= {"flow.vmp.tree.differs." + c for c in DIFFER_CODES}
PROJECT_KEYS |= {"flow.vmp.tree.cmp.kind." + c for c in KIND_CODES}
PROJECT_KEYS |= {"flow.vmp.tree.t." + f
                 for f in rr.NESTED_FIELDS["results.timing"]}
PROJECT_KEYS |= {"flow.vmp.tree.err." + c for c in GOLDEN_CODES}

# Подстановки строк со значениями: набор имён обязан совпадать в пяти
# языках (общий страж — tests/test_i18n_parity), здесь назван состав.
PLACEHOLDERS = {
    "flow.vmp.tree.root.words": {"airline", "root", "answers"},
    "flow.vmp.tree.root.docs.words": {"rules", "data"},
    "flow.vmp.tree.node.words": {"run", "purpose", "contour", "state"},
    "flow.vmp.tree.tok.words": {"input", "output"},
    "flow.vmp.tree.tok.row": {"input", "output", "calls"},
    "flow.vmp.tree.time.words": {"m"},
    "flow.vmp.tree.cmp.head": {"kind", "a", "b"},
    "flow.vmp.tree.cmp.same": {"n"},
    "flow.vmp.tree.golden.yes": {"by", "at"},
    "flow.vmp.tree.golden.ok": {"run"},
    "flow.vmp.tree.golden.unmarked": {"run"},
    "flow.vmp.tree.rebuild.done": {"roots", "runs", "refusals"},
}


# ------------------------------------------------------------- разметка

def test_section_stands_under_the_record_of_the_vmp_panel():
    """Раздел 12.3: секция дерева — на панели постановки рядом с записью.
    Порядок: запись говорит, ЧТО объявлено в этом прогоне, дерево — ГДЕ
    он среди прогонов того же корня; обе стоят выше блока пакета."""
    panel = _fn(_src(), "flowOpenVmpPanel")
    assert "vmpTreeBlock(dlg);" in panel
    assert panel.index("vmpRecordBlock(dlg);") < panel.index("vmpTreeBlock(dlg);")
    assert panel.index("vmpTreeBlock(dlg);") < panel.index('t("flow.vmp.pkg")')
    # закрытие панели снимает живой блок: сигналы не идут в снятую карточку
    assert "_vmpTree = null;" in panel


def test_runs_card_carries_the_same_section():
    """Раздел 12.3: «Дерево прогонов» рядом с «Записью о прогоне» на
    карточке прогона; ошибка чтения — строкой карточки, а не тостом на
    каждый опрос."""
    source = _src()
    card = _fn(source, "_crunsCardRender")
    assert 't("cruns.tree.h")' in card and 't("cruns.tree.title")' in card
    assert "_crunsRenderTree(ctx, secTree);" in card
    assert card.index("_crunsRenderRecord") < card.index("_crunsRenderTree")
    assert card.index("_crunsRenderTree") < card.index("_crunsRenderPassport")
    load = _fn(source, "_crunsTreeLoad")
    assert 't("cruns.tree.fail")' in load
    assert "_crunsCardErrorLine(ctx, words)" in load
    assert "toast(" not in load


def test_one_painter_serves_both_places():
    source = _src()
    render = _fn(source, "runTreeRender")
    assert "_runTreeDraw(host, doc, state, opts)" in render
    draw = _fn(source, "_runTreeDraw")
    for part in ("_runTreePath", "_runTreeSiblings", "_runTreeCompareBox",
                 "_runTreeExpect", "_runTreeGolden", "_runTreeFoot"):
        assert part + "(" in draw, part
    # узла нет — честные слова и всё равно кнопка перестройки
    assert "_runTreeEmptyKey(doc)" in draw
    # слова сервера показываются, а не проглатываются
    assert "(doc.words || [])" in draw
    block = _fn(source, "vmpTreeBlock")
    assert 'api("/run/current/tree")' in block
    assert "runTreeRender(body, doc" in block
    # блок перечитывается вместе с записью — по изменению подписи опроса
    signal = _fn(source, "vmpRecordSignal")
    assert "_vmpTree.reload();" in signal


# --------------------------------------------------------------- ручки

def test_static_calls_only_the_tree_endpoints():
    source = _src()
    calls = set(re.findall(r'\bapi\("(/run[^"]*)"', source))
    # ручки записи шага 2 живут в своём стенде; здесь судится, что в
    # области /run не появилось ничего сверх контракта дерева
    assert TREE_ENDPOINTS <= calls, sorted(calls)
    assert not (calls - TREE_ENDPOINTS - {RUNS_ALL_ENDPOINT} -
                {"/run/current", "/run/declare", "/run/stitch"}), sorted(calls)
    # отбор идёт ПАРАМЕТРАМИ той же ручки индекса: второго адреса у
    # отбора нет (одно место правды — сервер)
    index = _fn(source, "runTreeIndexBlock")
    assert 'api("/run-tree" + runFiltersQuery(f))' in index
    card = _fn(source, "_crunsTreeLoad")
    assert 'api("/cosmos-runs/" + encodeURIComponent(ctx.run) + "/tree")' in card
    box = _fn(source, "_runTreeCompareBox")
    assert '"?a=" + encodeURIComponent(state.sibling)' in box
    assert '"&b=" + encodeURIComponent(state.run)' in box
    assert 'api("/run-tree/compare" + query)' in box
    foot = _fn(source, "_runTreeFoot")
    assert 'api("/run-tree/rebuild", { method: "POST" })' in foot
    call = _fn(source, "_runTreeGoldenCall")
    assert 'api("/cosmos-runs/" + encodeURIComponent(run) + tail, options)' in call
    golden = _fn(source, "_runTreeGolden")
    assert '"/golden/unmark"' in golden
    assert '"/golden"' in _fn(source, "_runTreeGoldenForm")
    # рисовальщики сами по сети не ходят
    assert "fetch(" not in _fn(source, "_runTreeDraw")


# ---------------------------------------------------- словарь дерева

def test_edge_and_differ_words_come_from_the_tree_module():
    """Слова рёбер и различий берутся из `vmp/run_tree.py`: разойдись
    перечни — индекс говорил бы одно, а карточка другое."""
    ru = _values(_blocks(), "ru")
    for name, values, prefix in (
            ("RUN_TREE_EDGES", rt.EDGES, "flow.vmp.tree.edge."),
            ("RUN_TREE_DIFFERS", rt.DIFFERS, "flow.vmp.tree.differs.")):
        named = _map(name)
        assert set(named) == set(values), "%s: %s" % (name, sorted(named))
        for word, code in named.items():
            # русский перевод — то же слово, что в модуле дерева
            assert ru[prefix + code] == word, (name, word)
    assert len(rt.EDGES) == 4 and len(rt.DIFFERS) == 10
    # Вид сравнения назван в модуле дважды: коротким словом (`COMPARE_*`,
    # оно приходит полем `kind`) и фразой заголовка сравнения — карточка
    # печатает фразу, и она берётся оттуда же.
    kinds = _map("RUN_TREE_KINDS")
    assert set(kinds) == {rt.COMPARE_PAIR, rt.COMPARE_GOLDEN,
                          rt.COMPARE_PREVIOUS}, sorted(kinds)
    for word, code in kinds.items():
        assert ru["flow.vmp.tree.cmp.kind." + code] == rt._KIND_WORDS[word], word


def test_declaration_field_words_reuse_the_same_ten_words():
    """Крупные части объявления в таблице сравнения названы теми же
    словами, что колонка «чем отличается»: второго словаря частей в
    интерфейсе быть не должно."""
    fields = _map("RUN_TREE_FIELDS")
    assert set(fields) == set(rt.COMPARED_DECLARATION), sorted(fields)
    assert set(fields.values()) <= set(_map("RUN_TREE_DIFFERS").values())
    words = _fn(_src(), "_runTreeFieldWords")
    assert 't("flow.vmp.tree.differs." + code)' in words


# ------------------------------------------------------- поля контракта

def test_path_and_siblings_read_the_contract_fields():
    source = _src()
    path = _fn(source, "_runTreePath")
    for field in ("doc.path", "p.root", "root.airline", "root.root_sha256",
                  "root.root_answers_sha256", "root.documents", "docs.rules",
                  "docs.data", "p.runs", "n.edge", "edge.kind", "edge.differs"):
        assert field in path, field
    assert 't("flow.vmp.tree.self")' in path
    # отпечатки коротки: полный шестидесятичетырёхзначный не печатается
    assert "_runRecShort(root.root_sha256)" in path
    sib = _fn(source, "_runTreeSiblings")
    for field in ("doc.siblings", "n.run_id", "n.created_by", "n.purpose",
                  "n.contour", "(n.edge || {}).differs"):
        assert field in sib, field
    for part in ("_runTreeVerdicts(n)", "_runTreeMachine(n)",
                 "_runTreeTokens(n)", "_runTreeGoldenMark(n)"):
        assert part in sib, part
    assert 't("flow.vmp.tree.siblings.none")' in sib
    # выбранная строка — что сравнивать кнопкой «Сравнить с соседом»
    assert "state.sibling = n.run_id" in sib


def test_model_spend_is_shown_in_tokens_and_uncounted_channel_says_so():
    """Решение владельца № 1 (10.09.2026): расход — в токенах по каждой
    модели, денег нет; канал без учёта говорит «не считается», а не
    показывает половину суммы за целое."""
    tokens = _fn(_src(), "_runTreeTokens")
    for field in ("n.tokens", "row.input_tokens", "row.output_tokens",
                  "row.counted === false"):
        assert field in tokens, field
    assert 't("flow.vmp.tree.tok.partial")' in tokens
    for money in ("cost", "price", "usd", "$"):
        assert money not in tokens.lower(), money


def test_comparison_reads_the_contract_and_marks_counters_as_reference():
    source = _src()
    decl = _fn(source, "_runTreeCmpDecl")
    for field in ("cmp.declaration", "d.changed", "d.same", "row.field",
                  "row.a", "row.b"):
        assert field in decl, field
    assert 't("flow.vmp.tree.cmp.decl.none")' in decl
    res = _fn(source, "_runTreeCmpResults")
    for field in ("cmp.results", "r.verdicts", "r.timing", "r.tokens",
                  "r.counters", "v.same", "v.delta", "v.only_in",
                  "v.counted === false"):
        assert field in res, field
    # счётчики — «справочно» и в строке, и отдельным словом под таблицей
    assert 't("flow.vmp.tree.cmp.ref")' in res
    assert 't("flow.vmp.tree.cmp.counters.note")' in res
    box = _fn(source, "_runTreeCompareBox")
    # по умолчанию — с золотым; с предыдущим — по выбору
    assert 'mode: "golden"' in _fn(source, "runTreeRender")
    assert "doc.compare_previous" in box and "doc.compare_golden" in box
    # сравнивать не с чем — сказано, с чем именно (золотого нет либо
    # ветвь на корне первая), а не общим «нет данных»
    assert '"flow.vmp.tree.cmp.none." +' in box
    head = _fn(source, "_runTreeCompare")
    assert "cmp.same_root === false" in head
    assert "(cmp.words || [])" in head


def test_expectations_are_shown_as_they_come():
    """Раздел 12.4: состав ожиданий объявляет раннер при закрытии записи
    — интерфейс второго словаря ожиданий не заводит."""
    expect = _fn(_src(), "_runTreeExpect")
    assert "doc.expectations" in expect
    assert 't("flow.vmp.tree.expect.none")' in expect


def test_empty_index_and_undeclared_record_are_different_words():
    empty = _fn(_src(), "_runTreeEmptyKey")
    assert "doc.declared === false" in empty
    assert '"flow.vmp.tree.empty"' in empty and '"flow.vmp.tree.norecord"' in empty
    counts = _fn(_src(), "_runTreeCounts")
    for field in ("res.tree", "src.roots", "src.orphans", "src.refusals"):
        assert field in counts, field


# ------------------------------------------------------ золотой прогон

def test_golden_button_only_for_a_finished_prod_run():
    """Решение владельца 6.3 (10.09.2026): признак ставится ТОЛЬКО
    подтверждением владельца после приёмки на Prod. Кнопка живёт лишь у
    завершённого прогона площадки `prod` без признака; у прочих сказано
    словами, почему её нет."""
    source = _src()
    golden = _fn(source, "_runTreeGolden")
    assert 'node.contour === "prod"' in golden
    # состояние судится словарём записи, а не вторым перечнем слов
    assert 'RUN_REC_STATES[String(node.state || "")] === "done"' in golden
    assert "g.confirmed" in golden
    assert 't("flow.vmp.tree.golden.btn")' in golden
    assert 't("flow.vmp.tree.golden.unmark")' in golden
    assert 't("flow.vmp.tree.golden.only_prod")' in golden
    form = _fn(source, "_runTreeGoldenForm")
    assert 't("flow.vmp.tree.golden.need_words")' in form
    assert "{ words: words }" in form
    call = _fn(source, "_runTreeGoldenCall")
    # личность подтвердившего ставит сервер: поля `by` интерфейс не шлёт
    assert "by:" not in form and "by:" not in call
    assert "_runTreeErrLines(errBox, e)" in call
    err = _fn(source, "_runTreeErrLines")
    assert "b.detail" in err and "b.code" in err and "b.words" in err
    assert '_runRecKeyWords("flow.vmp.tree.err.", code)' in err


# ---------------------------------------------------------- пять языков

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


def test_every_key_used_by_the_section_exists_in_russian():
    source = _src()
    ru = _blocks()["ru"]
    used = set(re.findall(r'\bt\("((?:flow\.vmp\.tree|cruns\.tree)[^"]*)"',
                          source))
    used |= set(re.findall(r'\btf\("((?:flow\.vmp\.tree|cruns\.tree)[^"]*)"',
                           source))
    used = {k for k in used if not k.endswith(".")}
    assert used, "секция не зовёт ни одного ключа дерева"
    missing = sorted(k for k in used if k not in ru)
    assert not missing, missing


def test_tour_tells_about_the_run_tree():
    """Чек-лист синхронизации витрин, пункт «тур»: шаг тура о вкладке
    «Прогоны» обязан назвать дерево прогонов (пять языков)."""
    steps = ONBOARDING.read_text(encoding="utf-8")
    m = re.search(r'\{ key: "gcruns", group: "cruns", paras: \[([^\]]*)\] \}',
                  steps)
    assert m, "шаг gcruns тура не найден"
    assert '"p1", "p2"' in m.group(1), m.group(1)
    blocks = _blocks()
    for lang in LANGS:
        vals = _values(blocks, lang)
        text = vals["tour.gcruns.p2"]
        assert len(text) > 120, lang
        assert "Prod" in text, lang
        # прежний абзац не тронут
        assert "CDPM" in vals["tour.gcruns.p1"], lang


# ------------------------------------------------------------ оформление

def test_style_declares_the_tree_classes():
    css = STYLE.read_text(encoding="utf-8")
    for cls in (".run-tree-sub", ".run-tree-path", ".run-tree-table",
                ".run-tree-bad", ".run-tree-warn", ".run-tree-actions",
                ".run-tree-input", ".run-tree-err", ".cruns-tree"):
        assert cls in css, cls
    # арабский справа налево держится логическими свойствами, а не
    # отдельными правилами [dir="rtl"] (образец соседних секций)
    tail = css[css.index("/* Дерево прогонов"):]
    assert "padding-inline-start" in tail
    assert "padding-left" not in tail and "margin-left" not in tail


# ---------------------------------------------------------------- node
# Слова секции собираются НЕ шаблоном страницы, а функциями app.js.
# Функции исполняются настоящим node в песочнице с живым i18n.js — по
# разу на каждый из пяти языков; узлы подменены объектами, `api` и
# `toast` — записывающими пустышками.

_CONSTS = ("RUN_REC_PURPOSES", "RUN_REC_OUTCOMES", "RUN_REC_STATES",
           "RUN_REC_OWNERS", "RUN_TREE_EDGES", "RUN_TREE_DIFFERS",
           "RUN_TREE_KINDS", "RUN_TREE_FIELDS")

_FUNCS = ("el", "fmt", "localeTag", "fmtTime", "cosmosRoleWords",
          "_crunsTaskLabel", "_crunsErrWords", "_runRecWords",
          "_runRecPurposeWords", "_runRecOutcomeWords", "_runRecStateWords",
          "_runRecKeyWords", "_runRecContourWords", "_runRecShort",
          "_runRecNum", "_runTreeEdgeWords", "_runTreeDifferWords",
          "_runTreeKindWords", "_runTreeDiffersList", "_runTreeFieldWords",
          "_runTreeNodeWords", "_runTreeVal", "_runTreeDelta",
          "_runTreeTokens", "_runTreeMachine", "_runTreeVerdicts",
          "_runTreeGoldenMark", "_runTreeCounts", "_runTreeEmptyKey",
          "_runTreeSub", "_runTreeRow", "_runTreeErrLines", "_runTreeErrLine",
          "_runTreePath", "_runTreeSiblings", "_runTreeCmpDecl",
          "_runTreeCmpResults", "_runTreeCompare", "_runTreeCompareBox",
          "_runTreeExpect", "_runTreeGoldenCall", "_runTreeGoldenForm",
          "_runTreeGolden", "_runTreeFoot", "_runTreeDraw", "runTreeRender")

_HARNESS = r"""
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
const calls = [], toasts = [];
const sandbox = {
  console, setTimeout, clearTimeout,
  localStorage: { getItem: () => lang, setItem() {} },
  document: {
    createElement: node, createTextNode: (t) => ({ textContent: t }),
    addEventListener() {}, querySelectorAll: () => [], querySelector: () => null,
    dispatchEvent() {}, documentElement: { setAttribute() {} }, body: node('body')
  },
  api: function (path, options) {
    calls.push({ path: path, method: (options || {}).method || 'GET',
                 body: (options || {}).body || null });
    return Promise.resolve({ ok: true });
  },
  toast: function (words) { toasts.push(String(words)); }
};
sandbox.globalThis = sandbox; sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(i18nPath, 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(helpers, 'utf8'), sandbox);
const T = sandbox.t;
function walk(n, out) {
  if (n && n.textContent) { out.push(String(n.textContent)); }
  (n && n.children || []).forEach(function (c) { walk(c, out); });
  return out;
}
function collect(n, tag, out) {
  if (n && n.tag === tag) { out.push(n); }
  (n && n.children || []).forEach(function (c) { collect(c, tag, out); });
  return out;
}
function press(host, text) {
  const found = collect(host, 'button', []).filter(function (b) {
    return String(b.textContent) === text; });
  if (!found.length) { throw new Error('кнопки нет: ' + text); }
  if (found[0].onclick) { found[0].onclick(); }
  return found[0];
}
const cases = JSON.parse(fs.readFileSync(process.env.CREW_Q, 'utf8'));
const out = {};
Object.keys(cases).forEach(function (name) {
  const host = node('div');
  try {
    sandbox.runTreeRender(host, cases[name], { reload() {} });
    out[name] = { text: walk(host, []).filter(Boolean),
                  buttons: collect(host, 'button', []).map(function (b) {
                    return String(b.textContent); }) };
  } catch (e) { out[name] = { error: String(e && e.stack || e) }; }
});
// сценарий 1: подтверждение золотого прогона — форма, пустые слова, отправка
try {
  const host = node('div');
  sandbox.runTreeRender(host, cases['prod завершён'], { reload() {} });
  press(host, T('flow.vmp.tree.golden.btn'));
  out._form = walk(host, []).filter(Boolean);
  press(host, T('flow.vmp.tree.golden.send'));
  out._empty_words = walk(host, []).filter(Boolean);
  const inputs = collect(host, 'input', []).filter(function (x) {
    return x.attrs.type === 'text'; });
  inputs[0].value = '  образец приёмки  ';
  calls.length = 0;
  press(host, T('flow.vmp.tree.golden.send'));
  out._golden_calls = calls.slice();
} catch (e) { out._form = { error: String(e && e.stack || e) }; }
// сценарий 2: снятие признака у золотого прогона
try {
  const host = node('div');
  sandbox.runTreeRender(host, cases['золотой'], { reload() {} });
  calls.length = 0;
  press(host, T('flow.vmp.tree.golden.unmark'));
  out._unmark_calls = calls.slice();
} catch (e) { out._unmark_calls = { error: String(e && e.stack || e) }; }
// сценарий 3: перестройка дерева у пустого индекса
try {
  const host = node('div');
  sandbox.runTreeRender(host, cases['дерево пусто'], { reload() {} });
  calls.length = 0;
  press(host, T('flow.vmp.tree.rebuild'));
  out._rebuild_calls = calls.slice();
} catch (e) { out._rebuild_calls = { error: String(e && e.stack || e) }; }
// сценарий 4: сравнение с соседом и переключатель «с предыдущим»
try {
  const host = node('div');
  const st = sandbox.runTreeRender(host, cases['полное дерево'], { reload() {} });
  toasts.length = 0; calls.length = 0;
  press(host, T('flow.vmp.tree.pick.sibling'));
  out._pick_toast = toasts.slice();
  st.sibling = %(sib)s;
  press(host, T('flow.vmp.tree.pick.sibling'));
  out._compare_calls = calls.slice();
  press(host, T('flow.vmp.tree.pick.previous'));
  out._previous = walk(host, []).filter(Boolean);
} catch (e) { out._previous = { error: String(e && e.stack || e) }; }
process.stdout.write(JSON.stringify(out));
"""


def _node_cases(tmp_path, cases, lang):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node недоступен")
    src = _src()
    helpers = tmp_path / "tree-helpers.js"
    helpers.write_text(
        "\n".join([_const(src, c) for c in _CONSTS] +
                  [_grab(src, n) for n in _FUNCS]),
        encoding="utf-8")
    runner = tmp_path / "tree.js"
    runner.write_text(_HARNESS % {"sib": json.dumps(RUN_SIB)},
                      encoding="utf-8")
    q = tmp_path / "cases.json"
    q.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run(
        [node_bin, str(runner), str(I18N), str(helpers), lang],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CREW_Q": str(q)}, check=True)
    return json.loads(res.stdout)


def _node(run_id, contour, state, **extra):
    """Узел дерева стенда: поля — по `vmp/run_tree.NODE_FIELDS`."""
    doc = {
        "run_id": run_id, "created_at": "2026-09-10T09:00:00+03:00",
        "created_by": "кто-то", "purpose": rr.PURPOSES[0],
        "contour": contour, "state": state, "airline": "а/к",
        "root_sha256": "1" * 64, "root_answers_sha256": "2" * 64,
        "catalog": {"name": "кат", "version": "0.4.0", "fingerprint": "b" * 64},
        "bindings": {"catalog_version": "0.4.0",
                     "catalog_fingerprint": "c" * 64},
        "code": {"cdpm_commit": "e" * 40, "cosmos_commit": "f" * 40},
        "libraries": {"python": "3.14", "packages_sha256": "d" * 64},
        "models": {"certifier": {"model": "м", "provider": "п"}},
        "solvers": {"pairing": {"engine": "дв", "spent_limit_s": 60}},
        "planned_stages": ["пейринг"], "stop_after": None,
        "parent_run": None, "branch_of": None, "source_run": None,
        "package_sha256": "3" * 64,
        "edge": {"kind": rt.EDGE_ROOT, "to": None, "differs": [],
                 "words": None},
        "verdicts": {"pairing": "OK"},
        "timing": {"machine_s": 61, "waiting_human_s": 0, "total_s": 61},
        "tokens": {"certifier/м": {"input_tokens": 100, "output_tokens": 20,
                                   "calls": 3, "counted": True}},
        "counters": {"обменов с моделью": 4},
        "golden": {"confirmed": False, "by": None, "at": None, "words": None},
    }
    doc.update(extra)
    return doc


def _compare(a, b, kind):
    return {
        "kind": kind, "a": a, "b": b, "same_root": True,
        "declaration": {
            "changed": [{"field": "solvers.pairing.spent_limit_s",
                         "a": 60, "b": 120},
                        {"field": "models.certifier.model", "a": "м", "b": "м2"}],
            "same": ["purpose", "contour"]},
        "results": {
            "verdicts": {"pairing": {"a": "OK", "b": "OK", "same": True}},
            "timing": {"machine_s": {"a": 61, "b": 70, "delta": 9},
                       "waiting_human_s": {"a": 0, "b": 0, "delta": 0},
                       "total_s": {"a": 61, "b": 70, "delta": 9}},
            "tokens": {"certifier/м": {
                "a": {"input_tokens": 100, "output_tokens": 20, "calls": 3,
                      "counted": True},
                "b": {"input_tokens": 140, "output_tokens": 25, "calls": 4,
                      "counted": False},
                "delta": {"input_tokens": 40, "output_tokens": 5, "calls": 1},
                "counted": False, "only_in": None}},
            "counters": {"обменов с моделью": {"a": 4, "b": 5, "delta": 1,
                                               "note": "справочно"}}},
        "words": ["слово сравнения"],
    }


def _cases():
    self_node = _node(RUN_SELF, "local", rr.STATE_RUNNING)
    gold_node = _node(RUN_GOLD, rr.CONTOURS[3], rr.STATE_DONE,
                      golden={"confirmed": True, "by": "владелец",
                              "at": "2026-09-10T10:00:00+03:00",
                              "words": "образец приёмки"})
    sib_node = _node(RUN_SIB, "test", rr.STATE_DONE,
                     edge={"kind": rt.EDGE_SIBLING, "to": RUN_SELF,
                           "differs": list(rt.DIFFERS[:3]), "words": None},
                     tokens={"certifier/м": {"input_tokens": 90,
                                             "output_tokens": 10, "calls": 2,
                                             "counted": False}})
    root = {"root_sha256": "1" * 64, "root_answers_sha256": "2" * 64,
            "airline": "а/к",
            "documents": {"rules": [{"name": "правила", "sha256": "9" * 64}],
                          "data": [{"name": "книга", "sha256": "8" * 64}]}}
    full = {
        "node": self_node,
        "path": {"root": root, "runs": [gold_node, self_node]},
        "siblings": [sib_node, gold_node],
        "golden": gold_node,
        "compare_golden": _compare(RUN_GOLD, RUN_SELF, rt.COMPARE_GOLDEN),
        "compare_previous": _compare(RUN_SIB, RUN_SELF, rt.COMPARE_PREVIOUS),
        "expectations": {"судимые ключи": ["вердикт пейринга"],
                         "расхождения": []},
        "words": ["слово сервера"],
    }
    lone = {"node": self_node, "path": {"root": root, "runs": [self_node]},
            "siblings": [], "golden": None, "compare_golden": None,
            "compare_previous": None, "expectations": None}
    prod_done = dict(lone, node=_node(RUN_SELF, rr.CONTOURS[3], rr.STATE_DONE))
    return {
        "полное дерево": full,
        "без золотого": lone,
        "prod завершён": prod_done,
        "золотой": dict(lone, node=gold_node),
        "дерево пусто": {"node": None, "roots": [], "orphans": [],
                         "refusals": []},
        "запись не объявлена": {"node": None, "declared": False},
        "пустой ответ": {},
    }


@pytest.fixture(scope="module")
def cases() -> dict:
    return _cases()


@pytest.mark.parametrize("lang", LANGS)
def test_node_section_renders_every_answer_of_the_contract(tmp_path, cases,
                                                           lang):
    got = _node_cases(tmp_path, cases, lang)
    vals = _values(_blocks(), lang)
    for name in cases:
        assert "error" not in got[name], (name, got[name])
        assert got[name]["text"], "%s: секция пуста" % name

    full = " · ".join(got["полное дерево"]["text"])
    # слово сервера, путь от корня, соседи и сравнение — все на месте
    assert "слово сервера" in full
    assert vals["flow.vmp.tree.path"] in full, lang
    assert vals["flow.vmp.tree.siblings"] in full, lang
    assert vals["flow.vmp.tree.cmp"] in full, lang
    assert vals["flow.vmp.tree.self"] in full, lang
    assert RUN_SELF in full and RUN_SIB in full and RUN_GOLD in full
    # слова рёбер и различий — переводом
    assert vals["flow.vmp.tree.edge." + _map("RUN_TREE_EDGES")[rt.EDGE_ROOT]] \
        in full, lang
    for word in rt.DIFFERS[:3]:
        assert vals["flow.vmp.tree.differs." + _map("RUN_TREE_DIFFERS")[word]] \
            in full, (lang, word)
    # отпечатки коротки, полные шестидесятичетырёхзначные не печатаются
    assert "1" * 64 not in full and "9" * 64 not in full
    # расход в токенах: сумма соседа без учёта помечена «не считается»
    assert vals["flow.vmp.tree.tok.partial"] in full, lang
    # счётчики — справочно
    assert vals["flow.vmp.tree.cmp.counters.note"] in full, lang
    # сравнение по умолчанию — с золотым, и это сказано словами вида
    assert vals["flow.vmp.tree.cmp.kind." +
                _map("RUN_TREE_KINDS")[rt.COMPARE_GOLDEN]] in full, lang
    assert "слово сравнения" in full
    # ожидания набора показаны как есть
    assert vals["flow.vmp.tree.expect"] in full, lang
    assert "судимые ключи" in full

    lone = " · ".join(got["без золотого"]["text"])
    assert vals["flow.vmp.tree.siblings.none"] in lone, lang
    assert vals["flow.vmp.tree.cmp.none.golden"] in lone, lang
    assert vals["flow.vmp.tree.expect.none"] in lone, lang
    # площадка не prod — кнопки образца нет, и сказано почему
    assert vals["flow.vmp.tree.golden.btn"] not in got["без золотого"]["buttons"]
    assert vals["flow.vmp.tree.golden.only_prod"] in lone, lang

    prod = got["prod завершён"]
    assert vals["flow.vmp.tree.golden.btn"] in prod["buttons"], lang
    gold = got["золотой"]
    assert vals["flow.vmp.tree.golden.unmark"] in gold["buttons"], lang
    assert vals["flow.vmp.tree.golden.btn"] not in gold["buttons"], lang
    assert "владелец" in " · ".join(gold["text"])

    # пустой индекс и необъявленная запись — разные честные слова, и у
    # обоих остаётся кнопка перестройки
    empty = got["дерево пусто"]
    assert vals["flow.vmp.tree.empty"] in " · ".join(empty["text"]), lang
    assert vals["flow.vmp.tree.rebuild"] in empty["buttons"], lang
    none = got["запись не объявлена"]
    assert vals["flow.vmp.tree.norecord"] in " · ".join(none["text"]), lang
    assert vals["flow.vmp.tree.empty"] not in " · ".join(none["text"]), lang


@pytest.mark.parametrize("lang", LANGS)
def test_node_buttons_call_the_handles_of_the_contract(tmp_path, cases, lang):
    got = _node_cases(tmp_path, cases, lang)
    vals = _values(_blocks(), lang)
    # форма подтверждения: слова обязательны, пустые — отказ словами
    assert "error" not in got["_form"], got["_form"]
    assert vals["flow.vmp.tree.golden.h"] in " · ".join(got["_form"]), lang
    assert vals["flow.vmp.tree.golden.need_words"] in \
        " · ".join(got["_empty_words"]), lang
    calls = got["_golden_calls"]
    assert len(calls) == 1, calls
    assert calls[0]["path"] == "/cosmos-runs/%s/golden" % RUN_SELF
    assert calls[0]["method"] == "POST"
    body = json.loads(calls[0]["body"])
    # слова обрезаны по краям, личность подтвердившего ставит сервер
    assert body == {"words": "образец приёмки"}, body
    # снятие признака — своей ручкой, без тела
    unmark = got["_unmark_calls"]
    assert unmark[0]["path"] == "/cosmos-runs/%s/golden/unmark" % RUN_GOLD
    assert unmark[0]["method"] == "POST" and unmark[0]["body"] is None
    # перестройка дерева
    rebuild = got["_rebuild_calls"]
    assert rebuild[0] == {"path": "/run-tree/rebuild", "method": "POST",
                          "body": None}, rebuild
    # «Сравнить с соседом» без выбора — слова, а не молчание
    assert got["_pick_toast"] == [vals["flow.vmp.tree.pick.need"]], lang
    compare = got["_compare_calls"]
    assert len(compare) == 1, compare
    assert compare[0]["path"] == "/run-tree/compare?a=%s&b=%s" % (RUN_SIB,
                                                                  RUN_SELF)
    assert compare[0]["method"] == "GET"
    # переключатель «с предыдущим» перерисовывает сравнение своими силами
    previous = " · ".join(got["_previous"])
    assert vals["flow.vmp.tree.cmp.kind." +
                _map("RUN_TREE_KINDS")[rt.COMPARE_PREVIOUS]] in previous, lang
