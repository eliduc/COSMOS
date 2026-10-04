# -*- coding: utf-8 -*-
"""Документация секции «Дерево прогонов» — страж дорожки «Документация»
(проект final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md, разделы 7 и 12;
образец tests/test_docs_run_record.py).

Судятся только ТЕКСТЫ документов:

  * `docs/manual/09-spravochnik-RU.md` — новый раздел 6ж: какие ручки
    питают секцию, какие поля ответа читаются, какими словами названы
    рёбра и различия, чем отказывает ручка золотого прогона и как
    проверить это в интерфейсе шаг за шагом. Разделы 6г, 6д и 6е —
    чужие, стенд только проверяет, что 6ж встал ПОСЛЕ них и до части 7;
  * `final-report/COSMOS-OVERVIEW-AUDIT-RU.md` — абзац о дереве прогонов
    (документы `final-report` не входят в публичный срез: в нём стенд
    объявляет пропуск словами, а не падает).

Слова рёбер и различий берутся из `vmp/run_tree.py`: разойдись перечни —
модуль говорил бы одно, а руководство другое.

Принципы: деловой русский, ни одного отпечатка живого пакета и ни одного
номера прогона живой площадки — привязки к набору данных в документах не
бывает. Кода стенд не трогает: его судит
tests/test_webapp_static_run_tree.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp import run_record as rr  # noqa: E402
from vmp import run_tree as rt  # noqa: E402

MANUAL_09 = HERE / "docs" / "manual" / "09-spravochnik-RU.md"
OVERVIEW = HERE / "final-report" / "COSMOS-OVERVIEW-AUDIT-RU.md"
PROJECT = HERE / "final-report" / "PROEKT-ZAPIS-O-PROGONE-CLD.md"

SECTION = "## 6ж. "
#: Раздел 6ж кончается там, где начинается общий экран обеих систем
#: (6з, слово владельца 11.09.2026), а весь их куст — до части 7.
SECTION_ALL = "## 6з. "
NEXT_SECTION = "## 7. Связанные части"

#: Параметры отбора прогонов (`GET /api/run-tree`) и коды его отказов.
FILTER_PARAMS = ["customer", "contour", "state", "declared_by", "golden",
                 "since", "until", "root", "q"]
FILTER_CODES = ["filter-unknown", "filter-value"]

#: Ручка общего экрана и поля её строки (раздел 6з).
RUNS_ALL_ENDPOINT = "GET /api/runs-all"
RUNS_ALL_FIELDS = ["items", "total", "words", "keeper", "reachable",
                   "record_id", "cdpm_run", "cosmos_run", "customer",
                   "contour", "declared_by", "created_at", "state",
                   "systems", "golden", "source", "cdpm_url",
                   "limit", "offset"]

# Ручки дерева по контракту раздела 12.2 проекта.
ENDPOINTS = [
    "GET /api/run-tree",
    "GET /api/run/current/tree",
    "GET /api/cosmos-runs/{id}/tree",
    "GET /api/run-tree/compare?a={run}&b={run}",
    "POST /api/cosmos-runs/{id}/golden",
    "POST /api/cosmos-runs/{id}/golden/unmark",
    "POST /api/run-tree/rebuild",
]
# Поля ответа ручки дерева и индекса.
ANSWER_FIELDS = [
    "node", "path", "siblings", "golden", "compare_golden",
    "compare_previous", "expectations", "roots", "orphans", "refusals",
    "built_at", "cosmos-run-tree/v1", "documents", "rules", "data",
]
# Поля узла и сравнения, названные в разделе.
NODE_FIELDS = [
    "run_id", "created_at", "created_by", "purpose", "contour", "state",
    "root_sha256", "root_answers_sha256", "airline", "catalog", "bindings",
    "code", "libraries", "models", "solvers", "planned_stages", "parent_run",
    "branch_of", "source_run", "package_sha256", "edge", "kind", "differs",
    "verdicts", "timing", "machine_s", "waiting_human_s", "total_s", "tokens",
    "input_tokens", "output_tokens", "calls", "counted", "counters",
    "confirmed",
]
COMPARE_FIELDS = ["same_root", "declaration", "changed", "same",
                  "results.comparison.expectations"]
# Коды отказов ручки золотого прогона.
REFUSAL_CODES = ["golden-not-prod", "golden-not-finished",
                 "golden-words-empty", "golden-forwarded-refused"]
# Носители словаря дерева.
MODULES = ["vmp/run_tree.py", "vmp/run_tree.schema.json"]
# Отпечатки живых пакетов площадок и номера прогонов живых данных: в
# документах их быть не должно (принцип «ничего не привязано к набору»).
DATASET_BOUND_MARKERS = ["989c11c8", "43024ccf", "run-0087", "run-0120"]

NOT_PUBLISHED = ("документы дорожки (final-report/%s) не входят в публичный "
                 "срез — правило выгрузки: final-report не публикуется; "
                 "стенд судит их только в рабочем репозитории")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Markdown переносит фразы по строкам — для поиска фраз пробелы и
    переводы строк схлопываются в один пробел."""
    return re.sub(r"\s+", " ", text)


def _assert_all(haystack: str, needles, where: str) -> None:
    flat = _flat(haystack)
    missing = [n for n in needles if n not in haystack and n not in flat]
    assert not missing, "%s: нет %s" % (where, missing)


def _read_track_document(path: Path) -> str:
    """Документы дорожки в final-report: в рабочем репозитории читаются
    жёстко, в публичном срезе их нет по правилу выгрузки — объявленный
    пропуск словами, а не падение."""
    if not path.is_file():
        pytest.skip(NOT_PUBLISHED % path.name)
    return _read(path)


@pytest.fixture(scope="module")
def m09() -> str:
    return _read(MANUAL_09)


@pytest.fixture(scope="module")
def section(m09: str) -> str:
    assert SECTION in m09, "раздела 6ж в руководстве 09 нет"
    i = m09.index(SECTION)
    j = m09.index(SECTION_ALL, i)
    return m09[i:j]


@pytest.fixture(scope="module")
def section_all(m09: str) -> str:
    assert SECTION_ALL in m09, "раздела 6з в руководстве 09 нет"
    i = m09.index(SECTION_ALL)
    j = m09.index(NEXT_SECTION, i)
    return m09[i:j]


@pytest.fixture(scope="module")
def overview() -> str:
    return _read_track_document(OVERVIEW)


@pytest.fixture(scope="module")
def project() -> str:
    return _read_track_document(PROJECT)


# ------------------------------------------------------- руководство 09

def test_section_6zh_stands_after_the_card_of_one_run(m09):
    """Порядок главы 6: паспорт раннера (6г), словарь полей (6д),
    карточка одного прогона (6е), затем дерево (6ж) — сначала прогон,
    потом место прогона среди своих."""
    assert m09.index("## 6д. ") < m09.index("## 6е. ") < m09.index(SECTION)
    assert m09.index(SECTION) < m09.index(NEXT_SECTION)
    head = m09[m09.index(SECTION):].splitlines()[0]
    for word in ("Дерево прогонов", "путь от корня", "соседние ветви",
                 "сравнение", "золотой прогон"):
        assert word in head, head


def test_section_names_the_endpoints_of_the_tree(section):
    _assert_all(section, ENDPOINTS, "раздел 6ж: ручки")
    # обе ручки показа отдают ОДИН ответ — это сказано словами
    assert "Ответ обеих ручек одинаков" in _flat(section)


def test_section_names_the_fields_of_the_answer_and_of_the_node(section):
    _assert_all(section, ANSWER_FIELDS, "раздел 6ж: поля ответа")
    _assert_all(section, NODE_FIELDS, "раздел 6ж: поля узла")
    _assert_all(section, COMPARE_FIELDS, "раздел 6ж: поля сравнения")
    _assert_all(section, MODULES, "раздел 6ж: носители словаря")


def test_section_uses_the_words_of_the_tree_module(section):
    """Четыре слова вида ребра и десять слов «чем отличается» — из
    `vmp/run_tree.py`: разойдись перечни, руководство обещало бы не то,
    что показывает карточка."""
    flat = _flat(section)
    for word in rt.EDGES:
        assert word in flat, word
    for word in rt.DIFFERS:
        assert word in flat, word
    assert len(rt.EDGES) == 4 and len(rt.DIFFERS) == 10
    # площадки и состояния названы словами словаря записи
    for contour in rr.CONTOURS:
        assert contour in flat, contour
    assert rr.STATE_DONE in flat


def test_section_names_refusal_codes_in_words(section):
    _assert_all(section, REFUSAL_CODES, "раздел 6ж: коды отказов")
    for code in REFUSAL_CODES:
        row = [ln for ln in section.splitlines()
               if ln.startswith("|") and "`%s`" % code in ln]
        assert row, "код %s назван без строки словами" % code
        assert len(row[0].split("|")) >= 5, row[0]
    # коды отказа названы числами, а не словом «ошибка»
    assert "409" in section and "422" in section


def test_section_names_the_filter_of_runs(section):
    """Отбор прогонов (слово владельца 11.09.2026): параметры названы в
    разделе 6ж все до одного, значения — словами словаря записи, а
    отказы — кодами с числом ответа."""
    _assert_all(section, ["`%s`" % p for p in FILTER_PARAMS],
                "раздел 6ж: параметры отбора")
    _assert_all(section, ["`%s`" % c for c in FILTER_CODES],
                "раздел 6ж: коды отказа отбора")
    flat = _flat(section)
    # отбирает сервер, а не браузер: это сказано словами
    assert "Отбирает СЕРВЕР" in flat
    assert "одно место правды" in flat
    # три вида объявившего названы своими кодами
    for kind in ("runner", "panel", "human"):
        assert "`%s`" % kind in section, kind
    # отбор запоминается в браузере и сбрасывается кнопкой
    assert "cosmos.runs.filters.v1" in section
    assert "Сбросить" in flat


def test_section_6z_stands_after_the_tree_and_names_its_handle(m09,
                                                               section_all):
    """Порядок главы 6: карточка одного прогона (6е), дерево (6ж), общий
    экран обеих систем (6з) — и всё это до части 7."""
    assert m09.index(SECTION) < m09.index(SECTION_ALL) < m09.index(NEXT_SECTION)
    head = m09[m09.index(SECTION_ALL):].splitlines()[0]
    for word in ("Все прогоны", "обеих систем"):
        assert word in head, head
    _assert_all(section_all, [RUNS_ALL_ENDPOINT], "раздел 6з: ручка")
    # поля названы именами (часть — внутри одной строки формы ответа)
    _assert_all(section_all, RUNS_ALL_FIELDS, "раздел 6з: поля ответа")
    _assert_all(section_all, ["`%s`" % p for p in FILTER_PARAMS[:4]],
                "раздел 6з: отбор тот же")


def test_section_6z_names_the_two_sources_and_the_borders(section_all):
    """Два источника строк, один прогон одной строкой, только чтение и
    честные слова о недоступной панели — ради этого экран и заведён."""
    flat = _flat(section_all)
    assert "GET /api/runs-all" in flat
    assert "/api/runs" in flat, "ручка списка прогонов панели не названа"
    assert "CREW_CDPM_RECORD_API" in flat and "CDPM_PUBLIC_URL" in flat
    assert "в списке ОДИН" in flat, "про удвоение строки не сказано"
    assert "Удаления и правок на общем экране НЕТ" in flat
    assert "архивирование остаётся у каждой системы" in flat
    assert "панель недоступна: показаны только записи COSMOS" in flat
    assert "Показать ещё" in flat and "200" in flat
    # чего панель не отдаёт — пусто, а не выдумано
    assert "а не выдумано" in flat
    # никаких отпечатков и номеров живой площадки
    for marker in DATASET_BOUND_MARKERS:
        assert marker not in section_all, marker
    assert not re.search(r"\b[0-9a-f]{16,}\b", section_all)


def test_section_keeps_the_owner_decisions(section):
    """Решения владельца 10.09.2026: расход — в токенах, счётчики —
    справочно, признак образца ставится только его словом после приёмки
    на Prod, а пустой ответ называется своими словами."""
    flat = _flat(section)
    assert "не считается" in flat
    assert "справочно" in flat
    assert "подтверждением владельца после приёмки на Prod" in flat
    assert "подпись без слов не подпись" in flat
    # личность подтвердившего ставит сервер, а не интерфейс
    assert "ставит СЕРВЕР по сессии" in flat
    # пусто и не объявлено — разные слова
    assert "Дерево пусто" in flat and "Запись не объявлена" in flat


def test_section_tells_how_to_check_it_in_the_interface(section):
    assert "### Как проверить в интерфейсе" in section
    steps = section[section.index("### Как проверить в интерфейсе"):]
    numbered = [ln for ln in steps.splitlines() if re.match(r"^\d+\. ", ln)]
    assert len(numbered) >= 8, len(numbered)
    _assert_all(steps, ["«ВМП»", "Прогоны", "Сравнить с соседом",
                        "Перестроить дерево", "Золотой прогон",
                        "Снять признак"],
                "раздел 6ж: как проверить")
    # проверка на пяти языках названа отдельным шагом
    assert "пять языков" in _flat(steps)


def test_section_is_not_bound_to_a_dataset(section):
    for marker in DATASET_BOUND_MARKERS:
        assert marker not in section, marker
    # длинных отпечатков в разделе нет: их место — в живой карточке
    assert not re.search(r"\b[0-9a-f]{16,}\b", section)


# ------------------------------------------------------------ обзор

def test_overview_has_one_paragraph_about_the_run_tree(overview):
    marker = "*Дерево прогонов (10.09.2026).*"
    assert marker in overview, "в обзоре нет абзаца о дереве прогонов"
    para = overview[overview.index(marker):]
    para = para[:para.index("\n\n")]
    assert "\n" not in para, "абзац обзора — одной строкой, как соседние"
    _assert_all(para, ["vmp/run_tree.py", "cosmos-run-tree/v1",
                       "GET /api/run/current/tree",
                       "GET /api/cosmos-runs/{id}/tree",
                       "GET /api/run-tree/compare",
                       "POST /api/run-tree/rebuild",
                       "POST /api/cosmos-runs/{id}/golden",
                       "golden-not-prod", "пяти языках"],
                "обзор: абзац о дереве")
    # честность: серверная половина названа как выпускаемая тем же шагом
    assert "серверная половина" in para
    for marker in DATASET_BOUND_MARKERS:
        assert marker not in para, marker


def test_overview_paragraph_follows_the_run_record_paragraph(overview):
    """Абзац стоит следом за рассказом о записи прогона: сначала прогон
    как сущность, потом его место среди своих."""
    assert overview.index("*Запись о прогоне (10.09.2026).*") < overview.index(
        "*Дерево прогонов (10.09.2026).*")
    assert overview.index("*Дерево прогонов (10.09.2026).*") < overview.index(
        "### Хранение: файлы, а не СУБД")


# ------------------------------------------------------------- проект

def test_documents_agree_with_the_project(section, project):
    """Проект — единственный источник истины: ручки дерева и правило
    золотого прогона руководства обязаны стоять и в нём."""
    for endpoint in ("/api/run-tree", "/api/run-tree/compare",
                     "/api/run-tree/rebuild"):
        assert endpoint in project, endpoint
    assert "золотой" in project.lower()
    # раздел руководства назван теми же словами, что карточка проекта
    assert "Дерево прогонов" in section and "Дерево прогонов" in project
