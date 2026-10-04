# -*- coding: utf-8 -*-
"""Документация карточки «Запись о прогоне» — страж дорожки
«Документация» (проект final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md,
раздел 10, п. 6; образец tests/test_docs_cosmos_runs.py).

Судятся только ТЕКСТЫ документов:

  * `docs/manual/09-spravochnik-RU.md` — новый раздел 6е: где карточка
    открывается, какие ручки её питают, какие поля ответа читаются, чем
    отказывает гейт от пакета и как проверить это в интерфейсе шаг за
    шагом. Раздел 6д (словарь полей) и 6г (паспорт раннера) — чужие,
    стенд только проверяет, что 6е встал ПОСЛЕ них и до части 7;
  * `final-report/COSMOS-OVERVIEW-AUDIT-RU.md` — абзац о записи прогона
    (документы `final-report` не входят в публичный срез: в нём стенд
    объявляет пропуск словами, а не падает).

Принципы: деловой русский, ни одного отпечатка живого пакета и ни одного
номера прогона живой площадки — привязки к набору данных в документах не
бывает. Кода стенд не трогает: его судит tests/test_webapp_static_run_record.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp import run_record as rr  # noqa: E402

MANUAL_09 = HERE / "docs" / "manual" / "09-spravochnik-RU.md"
OVERVIEW = HERE / "final-report" / "COSMOS-OVERVIEW-AUDIT-RU.md"
PROJECT = HERE / "final-report" / "PROEKT-ZAPIS-O-PROGONE-CLD.md"

SECTION = "## 6е. "
NEXT_SECTION = "## 7. Связанные части"

# Ручки карточки по контракту раздела 10 проекта.
ENDPOINTS = [
    "GET /api/run/current",
    "POST /api/run/declare",
    "POST /api/run/stitch",
    "GET /api/cosmos-runs/{id}/record",
]
# Поля ответа, которые карточка читает.
ANSWER_FIELDS = [
    "present", "package_sha", "head", "record_id", "declaration_sha256",
    "record_api", "owner", "source_run", "customer", "declared",
    "declare_reason_code", "words", "record", "declaration", "journal",
    "results", "cosmos", "environment", "entries", "stitch", "mode", "sent",
    "waiting", "refused", "last_error", "last_wait", "state", "form",
    "purposes",
    "contours", "models", "solvers", "planned_stages",
]
# Поля головы, журнала и итогов, названные в карточке.
RECORD_FIELDS = [
    "package_sha256", "cdpm_commit", "cosmos_commit", "stop_after",
    "duration_machine_s", "duration_waiting_human_s", "input_tokens",
    "output_tokens", "calls", "counted", "machine_s", "waiting_human_s",
    "total_s", "golden.confirmed",
]
# Тело ручки объявления.
DECLARE_BODY = ["purpose", "contour", "created_by"]
# Коды отказов.
REFUSAL_CODES = ["run-not-declared", "already-declared", "package-missing",
                 "declaration-invalid"]
# Имена систем журнала.
SYSTEMS = ["cdpm", "runner", "cosmos"]
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
    assert SECTION in m09, "раздела 6е в руководстве 09 нет"
    i = m09.index(SECTION)
    j = m09.index(NEXT_SECTION, i)
    return m09[i:j]


@pytest.fixture(scope="module")
def overview() -> str:
    return _read_track_document(OVERVIEW)


@pytest.fixture(scope="module")
def project() -> str:
    return _read_track_document(PROJECT)


# ------------------------------------------------------- руководство 09

def test_section_6e_stands_after_the_field_dictionary(m09):
    """Порядок главы 6: паспорт раннера (6г), словарь полей (6д), затем
    карточка (6е) — сначала что записано, потом что видно."""
    assert m09.index("## 6г. ") < m09.index("## 6д. ") < m09.index(SECTION)
    assert m09.index(SECTION) < m09.index(NEXT_SECTION)
    head = m09[m09.index(SECTION):].splitlines()[0]
    for word in ("Запись о прогоне", "гейт", "карточка", "объявления",
                 "сшивка"):
        assert word in head, head


def test_section_names_the_endpoints_of_the_card(section):
    _assert_all(section, ENDPOINTS, "раздел 6е: ручки")
    # обе ручки показа отдают ОДИН ответ — это сказано словами
    assert "Ответ обеих ручек одинаков" in _flat(section)


def test_section_names_the_fields_of_the_answer_and_of_the_record(section):
    _assert_all(section, ANSWER_FIELDS, "раздел 6е: поля ответа")
    _assert_all(section, RECORD_FIELDS, "раздел 6е: поля записи")
    _assert_all(section, DECLARE_BODY, "раздел 6е: тело объявления")
    _assert_all(section, SYSTEMS, "раздел 6е: системы журнала")


def test_section_names_refusal_codes_in_words(section):
    _assert_all(section, REFUSAL_CODES, "раздел 6е: коды отказов")
    # у каждого кода в таблице отказов есть строка со словами
    for code in REFUSAL_CODES:
        row = [ln for ln in section.splitlines()
               if ln.startswith("|") and "`%s`" % code in ln]
        assert row, "код %s назван без строки словами" % code
        assert len(row[0].split("|")) >= 4, row[0]
    # HTTP-код отказа гейта назван: 409, а не «ошибка»
    assert "409" in section


def test_section_tells_how_to_check_it_in_the_interface(section):
    assert "### Как проверить в интерфейсе" in section
    steps = section[section.index("### Как проверить в интерфейсе"):]
    numbered = [ln for ln in steps.splitlines() if re.match(r"^\d+\. ", ln)]
    assert len(numbered) >= 8, len(numbered)
    _assert_all(steps, ["«ВМП»", "Объявить прогон", "Повторить сшивку",
                        "Скачать запись", "Прогоны"],
                "раздел 6е: как проверить")
    # проверка на пяти языках названа отдельным шагом
    assert "пять языков" in _flat(steps)


def test_section_keeps_the_owner_decisions_of_the_record(section):
    """Решения владельца 10.09.2026: расход — в токенах, признак образца
    ставится только его словом, умолчаний «из дерева» у прогона нет."""
    flat = _flat(section)
    assert "не считается" in flat
    assert "словом владельца после приёмки на Prod" in flat
    assert "объявляются как есть" in flat or "объявляется как есть" in flat


def test_section_uses_the_words_of_the_record_dictionary(section):
    """Слова карточки — слова словаря `vmp/run_record.py`; площадки
    названы теми же кодами."""
    flat = _flat(section)
    for contour in rr.CONTOURS:
        assert contour in flat, contour
    for state in ("объявлен", "не объявлен"):
        assert state in flat, state


def test_section_is_not_bound_to_a_dataset(section):
    for marker in DATASET_BOUND_MARKERS:
        assert marker not in section, marker
    # длинных отпечатков в разделе нет: их место — в живой карточке
    assert not re.search(r"\b[0-9a-f]{16,}\b", section)


# ------------------------------------------------------------ обзор

def test_overview_has_one_paragraph_about_the_run_record(overview):
    marker = "*Запись о прогоне (10.09.2026).*"
    assert marker in overview, "в обзоре нет абзаца о записи прогона"
    para = overview[overview.index(marker):]
    para = para[:para.index("\n\n")]
    assert "\n" not in para, "абзац обзора — одной строкой, как соседние"
    _assert_all(para, ["vmp/run_record.py", "cosmos-run-record/v1",
                       "declaration_sha256", "GET /api/run/current",
                       "GET /api/cosmos-runs/{id}/record",
                       "POST /api/run/declare", "run-not-declared",
                       "пяти языках"], "обзор: абзац о записи")
    # честность: серверная половина названа как выпускаемая тем же шагом
    assert "серверная половина" in para
    for marker in DATASET_BOUND_MARKERS:
        assert marker not in para, marker


def test_overview_paragraph_follows_the_package_path_paragraph(overview):
    """Абзац стоит рядом с рассказом о пакетном пути и прогонах COSMOS —
    там, где читатель уже знает про попытки и паспорт."""
    assert overview.index("*Пакетный путь") < overview.index(
        "*Запись о прогоне (10.09.2026).*")
    assert overview.index("*Запись о прогоне (10.09.2026).*") < overview.index(
        "### Хранение: файлы, а не СУБД")


# ------------------------------------------------------------- проект

def test_documents_agree_with_the_project(section, project):
    """Проект — единственный источник истины: ручки показа и объявления и
    код гейта руководства обязаны стоять и в нём. Ручка повтора сшивки
    (`POST /api/run/stitch`) в проекте отдельным адресом не названа — там
    сшивка описана очередью с повтором «при следующем нажатии и при
    открытии карточки»; её адрес пришёл контрактом серверной половины и
    судится стендом статики, а не этим."""
    for endpoint in ("/api/run/current", "/api/run/declare"):
        assert endpoint in project, endpoint
    assert "run-not-declared" in project
    # карточка проекта названа теми же словами, что раздел руководства
    assert "Запись о прогоне" in section and "Запись о прогоне" in project
