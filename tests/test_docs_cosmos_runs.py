# -*- coding: utf-8 -*-
"""Документация системы прогонов COSMOS — страж дорожки «Документация»
(проект final-report/COSMOS-PROGONY-PROEKT-CLD.md, раздел 14).

Проверяются только тексты четырёх документов дорожки:
  docs/manual/03-optimizaciya-RU.md, docs/manual/09-spravochnik-RU.md,
  webapp/API-CONTRACT.md, final-report/COSMOS-OVERVIEW-AUDIT-RU.md —
их структура (разделы и маркеры, на которые ссылаются другие стенды),
документы final-report — только в рабочем репозитории (в публичном срезе
их нет по правилу выгрузки — объявленный пропуск словами),
согласованность между собой и с проектом (адреса ручек, переменные
площадки, коды отказов и пробелов паспорта, файлы хранилища, состояния
прогона) и принципы: деловой русский без привязки к набору данных
(отпечатки живых пакетов в примерах не встречаются), легаси-вкладка
«Прогоны» переименована в «История задач» везде, где документы её
называют. Кода приложения стенд не трогает: документы описывают проект
как единственный источник истины, а расхождение реализации с проектом
ловят стенды других дорожек.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
MANUAL_03 = HERE / "docs" / "manual" / "03-optimizaciya-RU.md"
MANUAL_09 = HERE / "docs" / "manual" / "09-spravochnik-RU.md"
CONTRACT = HERE / "webapp" / "API-CONTRACT.md"
OVERVIEW = HERE / "final-report" / "COSMOS-OVERVIEW-AUDIT-RU.md"
PROJECT = HERE / "final-report" / "COSMOS-PROGONY-PROEKT-CLD.md"

# Ручки прогонов по проекту (разделы 9.2 и 17.4) — литеральные строки,
# которые обязаны встречаться в таблице 12р руководства, в разделе 2.3
# справочника и в разделе «COSMOS runs» контракта.
RUN_ENDPOINTS = [
    "GET /api/cosmos-runs",
    "POST /api/cosmos-runs",
    "POST /api/cosmos-runs/init",
    "GET /api/cosmos-runs/current",
    "PUT /api/cosmos-runs/current",
    "GET /api/cosmos-runs/order",
    "PUT /api/cosmos-runs/order",
    "POST /api/cosmos-runs/archive",
    "PUT /api/cosmos-runs/{id}/title",
    "GET /api/cosmos-runs/{id}",
    "GET /api/cosmos-runs/{id}/attempts/{attempt}",
    "/attempts/{attempt}/file/{alias}",
    "/attempts/{attempt}/exchanges",
    "/attempts/{attempt}/export",
    "POST /api/cosmos-runs/{id}/attempts/{attempt}/replay",
    "GET /api/cosmos-runs/{id}/attempts/{attempt}/replay-report",
]
LEGACY_ENDPOINTS_WITH_RUN = [
    "POST /api/task-package/receive",
    "POST /api/task-package/upload",
    "GET /api/task-package/current",
    "GET /api/task-package/current/press",
    "POST /api/task-package/current/press",
    "GET /api/flow",
]
ENV_VARS = ["CREW_COSMOS_RUNS_DIR", "CREW_ATTEMPTS_KEEP",
            "CREW_LLM_EXCHANGES", "CREW_CALL_JOURNAL"]
CLI_INIT = "python -m webapp.cosmos_runs init"
RUN_REFUSAL_CODES = ["storage-off", "env-package", "current-busy"]
REPLAY_REFUSAL_CODES = [
    "run-busy", "attempt-running", "journal-incomplete", "pre-journal",
    "disabled-by-site", "replay-of-replay", "snapshot-missing",
    "package-bytes-missing", "params-not-recorded",
    "pairing-source-missing", "source-would-be-trimmed",
    "solver-url-missing", "certifier-unavailable", "storage-off",
    "journal-mismatch", "workdir-not-prepared", "press-refused",
]
REPORT_404_CODES = ["not-a-replay", "report-missing"]
PASSPORT_CODES = [
    "pre-passport", "no-git", "catalog-unavailable",
    "not-declared-by-package", "no-snapshot-copy", "latest-press-registry",
    "no-build-files", "no-version-endpoint", "solver-default-seed-0",
    "profile-d1", "channel-does-not-set", "ollama-default",
    "none-in-cosmos", "no-model-calls", "disabled-by-site",
    "snapshot-of-other-package", "package-bytes-missing", "pre-journal",
    "engine-only-timefold", "not-solved",
]
STATE_KINDS = ["new", "accepted", "blocked", "lost", "working", "done",
               "stalled", "refused", "interrupted"]
HINT_CODES = ["no-current-run", "storage-not-initialized", "storage-off",
              "env-package", "run-unreadable"]
# ревью реализации 07.09: отказы словами у любого сбоя, коды квитанции
# и пропусков оформления названы в справочнике и контракте
REVIEW_CODES = ["storage-busy", "storage-io", "bad-mode", "run-not-written",
                "receipt-incomplete", "registry-unreadable"]
STORAGE_FILES = [
    "cosmos-runs/", "storage.json", "counter.json", "order.json",
    "current.json", ".lock", "adoption.jsonl", "run.json", "holder.json",
    "cosmos-runs-archive", "llm-exchanges", "external-calls",
    "replay/report.json",
]
REPORT_VERDICTS = ["same", "differs", "not-comparable", "same-outcome",
                   "wall-clock-legit-difference",
                   "deterministic-expected-same", "not-run-for-replay",
                   "artifact-missing"]
# Отпечатки живых пакетов площадок (проект, раздел 12: только чтение) и
# номер прогона CDPM живых данных — в документах их быть не должно:
# принцип «ничего не привязано к набору данных».
DATASET_BOUND_MARKERS = ["989c11c8", "43024ccf", "run-0087", "run-0068"]

LEGACY_TAB = "История задач"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


def _flat(text: str) -> str:
    """Markdown переносит фразы по строкам — для поиска фраз пробелы и
    переводы строк схлопываются в один пробел."""
    return re.sub(r"\s+", " ", text)


def _unescaped_pipes(line: str) -> int:
    return len(re.findall(r"(?<!\\)\|", line))


def _cells(row: str) -> list[str]:
    """Ячейки строки таблицы: разделитель — только неэкранированный `|`
    (внутри ячеек встречается `\\|`)."""
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def _table_rows(block: str) -> list[str]:
    return [ln for ln in block.splitlines() if ln.startswith("|")]


def _assert_all(haystack: str, needles, where: str) -> None:
    flat = _flat(haystack)
    missing = [n for n in needles if n not in haystack and n not in flat]
    assert not missing, "%s: нет %s" % (where, missing)


@pytest.fixture(scope="module")
def m03() -> str:
    return _read(MANUAL_03)


@pytest.fixture(scope="module")
def m09() -> str:
    return _read(MANUAL_09)


@pytest.fixture(scope="module")
def contract() -> str:
    return _read(CONTRACT)


NOT_PUBLISHED = ("документы дорожки (final-report/%s) не входят в публичный "
                 "срез — правило выгрузки: final-report не публикуется; "
                 "стенд судит их только в рабочем репозитории")


def _read_track_document(path: Path) -> str:
    """Документы дорожки в final-report: в рабочем репозитории читаются
    жёстко, в публичном срезе (GitHub, CI) их нет по правилу выгрузки —
    объявленный пропуск словами, не падение (образец — conftest
    «артефакты не опубликованы»)."""
    if not path.is_file():
        pytest.skip(NOT_PUBLISHED % path.name)
    return _read(path)


@pytest.fixture(scope="module")
def overview() -> str:
    return _read_track_document(OVERVIEW)


@pytest.fixture(scope="module")
def project() -> str:
    return _read_track_document(PROJECT)


# ------------------------------------------------------------ руководство 03


def test_manual_03_history_tab_renamed_in_4_3(m03):
    sec = _between(m03, "### 4.3 ", "## 5. ")
    head = sec.splitlines()[0]
    assert LEGACY_TAB in head, head
    assert "страница «Прогоны»" not in head
    assert "12я" in sec  # куда ушло прежнее имя — названо


def test_manual_03_current_run_paragraph_in_12l(m03):
    sec = _between(m03, "## 12л. ", "## 12н. ")
    assert "**Текущий прогон.**" in sec
    para = sec[sec.index("**Текущий прогон.**"):]
    para = para[:para.index("\n\n")]
    _assert_all(para, ["PUT /api/cosmos-runs/current", "runs_hint_code",
                       "GET /api/flow", "CREW_TASK_PACKAGE", "holder.json",
                       "implicit", "busy_previous", "Новый прогон",
                       "Все прогоны"] + HINT_CODES, "12л «Текущий прогон»")
    # абзац о текущем прогоне стоит до «Работы со схемой», а якоря
    # стража перетаскивания (tests/test_webapp_static_dnd.py) сохранены
    i = sec.index("**Текущий прогон.**")
    j = sec.index("**Работа со схемой.**")
    k = sec.index("**Перетаскивание файлов на блоки.**")
    assert i < j < k


def test_manual_03_keeps_dnd_guard_anchors(m03):
    """Якоря чужого стража (test_webapp_static_dnd.py) не сломаны правкой."""
    sec = _between(m03, "## 12р. ", "## 12с. ")
    rows = [ln for ln in sec.splitlines()
            if ln.startswith("| Постановка (ВМП) |")]
    assert len(rows) == 1
    assert "перетаскивание" in rows[0]


def test_manual_03_source_check_binding_in_12o(m03):
    sec = _between(m03, "## 12о. ", "## 12п. ")
    assert "**Привязка снимка к прогону COSMOS" in sec
    para = sec[sec.index("**Привязка снимка к прогону COSMOS"):]
    para = para[:para.index("\n\n")]
    _assert_all(para, ["cosmos_run", "workdir", "package_sha", "source_run",
                       "source_check", "snapshot-of-other-package",
                       "POST /api/source-check/run", "utf-8-sig"],
                "12о «Привязка снимка»")


def test_manual_03_endpoint_table_in_12r(m03, project):
    sec = _between(m03, "## 12р. ", "## 12с. ")
    table = sec[sec.index("**Ручки** (вход — обычная сессия COSMOS):"):]
    table = table[:table.index("\n\n", table.index("| Ручка |"))]
    rows = _table_rows(table)
    assert len(rows) >= 14, len(rows)
    for row in rows:
        assert _unescaped_pipes(row) == 3, row[:80]  # две колонки
    _assert_all(table, RUN_ENDPOINTS + LEGACY_ENDPOINTS_WITH_RUN,
                "12р таблица ручек")
    _assert_all(table, ["cosmos_run", "run_note_code", "recipe",
                        "deterministic=", "seed", "stepLimit",
                        "unimprovedStepLimit", "deterministicTime",
                        "journal", "fresh", "только сессия"]
                + REPORT_404_CODES, "12р таблица ручек")
    # документ не выдумывает ручек: каждая названа проектом
    for ep in RUN_ENDPOINTS:
        assert ep in project, ep
    # загрузка кладёт пакет в текущий прогон
    assert "пакет ложится в ТЕКУЩИЙ" in sec
    # артефакты: держатель и второй рабочий каталог
    assert "holder.json" in sec and "runs/<run>/" in sec


def test_manual_03_attempt_record_in_12c(m03):
    sec = _between(m03, "## 12ц. ", "## 12ч. ")
    assert "**Запись попытки как часть паспорта прогона" in sec
    _assert_all(sec, ["params", "reproducibility", "wall-clock",
                      "deterministic", "certifier", "template_catalog",
                      "engine_versions", "registry", "exchanges",
                      "source_check", "journal", "replay", "pre-passport",
                      "pre-journal", "params_reconstructed",
                      "llm-exchanges", "external-calls",
                      "replay/report.json", "solver-status.json",
                      "applied-rules.json", "CREW_ATTEMPTS_KEEP",
                      "attempts_trimmed"], "12ц запись попытки")


def test_manual_03_section_12ya_structure(m03):
    assert m03.index("## 12ю. ") < m03.index("## 12я. ") < m03.index("## 13. ")
    sec = _between(m03, "## 12я. Прогоны COSMOS", "## 13. ")
    markers = ["**Задача.**", "**Что такое прогон.**", "**Где.**",
               "**Хранение**", "**Правила.**",
               "**Передача из CDPM в текущий прогон",
               "**Паспорт**", "**Классы воспроизводимости.**",
               "**Повтор**", "*Как читать отчёт сравнения*",
               "**Миграция старых данных", "**Ручки**",
               "**Как проверить.**"]
    _assert_all(sec, markers, "12я маркеры")
    order = [sec.index(x) for x in markers]
    assert order == sorted(order), "порядок абзацев 12я"
    # слова владельца — дословно
    _assert_all(sec, ["А почему в COSMOS нет системы", "однозначно воспроизвести",
                      "повтор из журнала» и «повтор заново"], "12я слова владельца")
    # раскладка хранилища и код блока закрыт
    _assert_all(sec, STORAGE_FILES + ["runs/run-NNNN/", "task-inbox/",
                                      "package-runs/"], "12я хранение")
    assert sec.count("```") % 2 == 0
    # состояния, подсказки, коды, переменные, ручки, повтор
    _assert_all(sec, ["`%s`" % k for k in STATE_KINDS], "12я состояния")
    _assert_all(sec, ENV_VARS + [CLI_INIT], "12я переменные и команда")
    _assert_all(sec, RUN_REFUSAL_CODES + REPLAY_REFUSAL_CODES,
                "12я коды отказов")
    _assert_all(sec, PASSPORT_CODES, "12я коды паспорта")
    # ревью реализации 07.09: отказы словами при любом сбое, причина
    # storage-off, лёгкая проверка без хеша, формат писателя, повтор как
    # первое пишущее действие
    _assert_all(sec, ["storage-busy", "storage-io", "bad-run-id", "bad-attempt-id",
                      "bad-mode", "run-unreadable", "inbox-off", "runs-dir-empty",
                      "существование файла пакета без хеша", "форматом",
                      "по прогону на каждый", "не пишут и прежние данные не оформляют"],
                "12я уточнения ревью")
    assert "на диск не пишут никогда" not in sec
    _assert_all(sec, REPORT_VERDICTS[4:], "12я пояснения отчёта")
    _assert_all(sec, ["Повтор из журнала", "Повтор заново", "байт в байт",
                      "по итогам", "Первое различие",
                      "САМЫЙ РАННИЙ по конвейеру", "среда: без различий",
                      "GET /api/cosmos-runs/{id}/attempts/{attempt}/replay-report",
                      "cosmos_run", "auto-receive", "auto-upload",
                      "adoption", "origin"], "12я повтор и правила")
    # паспортная таблица — четыре колонки, строки на все моменты
    table = sec[sec.index("| Поле | Источник | Момент |"):]
    table = table[:table.index("\n\n")]
    rows = _table_rows(table)
    assert len(rows) >= 20, len(rows)
    for row in rows:
        assert _unescaped_pipes(row) == 5, row[:80]
    # как проверить — шаги 13.6 проекта и авто-стенды
    tail = sec[sec.index("**Как проверить.**"):]
    _assert_all(tail, ["Оформить прогоны", "создан и сделан текущим",
                       "runs/run-00MM", "cosmos-runs-archive",
                       "Повтор из журнала", "Повтор заново",
                       "storage-not-initialized",
                       "tests/test_cosmos_runs.py",
                       "tests/test_cosmos_runs_migration.py",
                       "tests/test_cosmos_runs_passport.py",
                       "tests/test_cosmos_runs_replay.py",
                       "tests/test_webapp_static_cosmos_runs.py",
                       "tests/test_docs_cosmos_runs.py"], "12я как проверить")


def test_manual_03_section_13_unchanged(m03):
    tail = m03[m03.index("## 13. Связанные части"):]
    bullets = [ln for ln in tail.splitlines() if ln.startswith("- [")]
    assert len(bullets) == 4, bullets
    assert [b[:12] for b in bullets] == ["- [Часть 2 —", "- [Часть 4 —",
                                         "- [Часть 6 —", "- [Часть 9 —"]


# ------------------------------------------------------------ справочник 09


def test_manual_09_api_rows_with_entry_column(m09, project):
    sec = _between(m09, "### 2.3 ", "### 2.4 ")
    assert "**Прогоны COSMOS**" in sec
    block = sec[sec.index("| Метод и путь | Вход | Назначение и форма ответа |"):]
    block = block[:block.index("\n\n")]
    rows = _table_rows(block)
    assert len(rows) >= 16, len(rows)
    for row in rows:
        assert _unescaped_pipes(row) == 4, row[:80]  # три колонки
    _assert_all(block, RUN_ENDPOINTS, "09 §2.3 ручки прогонов")
    entries = {_cells(row)[2] for row in rows[2:]}
    assert entries <= {"сессия", "сессия или токен"}, entries
    session_only = [row for row in rows if _cells(row)[2] == "сессия"]
    assert any("/replay`" in row and "replay-report" not in row
               for row in session_only), "POST …/replay — только сессия"
    assert any("PUT /api/cosmos-runs/current" in row for row in session_only)
    _assert_all(sec, LEGACY_ENDPOINTS_WITH_RUN + ["cosmos_run", "run_note_code",
                                                   "recipe", "deterministic=1",
                                                   "runs_hint_code", "busy_previous"]
                + HINT_CODES, "09 §2.3 прежние ручки")
    for ep in RUN_ENDPOINTS:
        assert ep in project, ep


def test_manual_09_storage_map_and_passport_fields(m09):
    sec = _between(m09, "### 3.1 ", "### 3.2 ")
    assert "**Прогоны COSMOS (`cosmos-runs`)**" in sec
    # storage.json.by и current.json.by — значения, которые пишет код
    # (ensure_storage(by) / _write_current_pointer), не выдуманные
    assert "`user:<email>` — создание прогона" in sec
    for bad in ("`create \\| receive", "`create | receive"):
        assert bad not in sec
    assert "`init` — кнопка «Оформить прогоны», `cli` — команда" in sec
    assert 'user:<email> \\| channel \\| upload \\| init \\| cli' in sec
    # сводка паспорта списка: решатели — записями с кодами, не фразами
    assert "{task, engine, version, version_of, version_code}" in sec
    _assert_all(sec, STORAGE_FILES + ["runs/run-NNNN/", "receipts.jsonl",
                                      "cosmos-runs/v1", "cosmos-run/v1",
                                      "cosmos-workdir-holder/v1",
                                      "cosmos-call-journal/v1",
                                      "cosmos-replay-report/v1",
                                      "solver-status.json",
                                      "applied-rules.json",
                                      "*Запись попытки*",
                                      "*Поля паспорта прогона*",
                                      "passport_summary"],
                "09 §3.1 хранилище")
    _assert_all(sec, PASSPORT_CODES + ["replayed-from-journal",
                                       "artifact-missing"],
                "09 §3.1 коды паспорта")
    for header in ("| Файл или каталог | Что хранит |",
                   "| Поле | Источник | Момент |"):
        block = sec[sec.index(header):]
        block = block[:block.index("\n\n")]
        rows = _table_rows(block)
        width = _unescaped_pipes(rows[0])
        assert len(rows) >= 12, header
        for row in rows:
            assert _unescaped_pipes(row) == width, row[:80]


def test_manual_09_env_vars_and_cli(m09):
    sec = _between(m09, "## 4. Переменные окружения", "## 4-бис. ")
    rows = {_cells(ln)[1].strip("`"): ln
            for ln in sec.splitlines() if ln.startswith("| `")}
    for var in ENV_VARS:
        assert var in rows, var
        assert _unescaped_pipes(rows[var]) == 3, rows[var][:80]
    assert "прогоны" in rows["CREW_TASK_INBOX"].lower()
    assert "умолчание 20" in rows["CREW_ATTEMPTS_KEEP"]
    assert "`0` — без предела" in rows["CREW_ATTEMPTS_KEEP"]
    assert "пусто = выключено" in rows["CREW_COSMOS_RUNS_DIR"]
    assert CLI_INIT in sec and "--dry-run" in sec and "--data-dir" in sec


def test_manual_09_codes_and_legacy_tab(m09):
    sec = _between(m09, "## 5. Коды вердиктов и статусов", "## 6. Глоссарий")
    assert "**Статусы задач** («%s»" % LEGACY_TAB in sec
    assert "**Статусы задач** («Прогоны»)" not in sec
    assert "**Прогоны COSMOS**" in sec
    para = sec[sec.index("**Прогоны COSMOS**"):]
    para = para[:para.index("\n\n")]
    _assert_all(para, ["`%s`" % k for k in STATE_KINDS], "09 §5 состояния")
    _assert_all(para, RUN_REFUSAL_CODES + REPLAY_REFUSAL_CODES
                + REPORT_404_CODES + PASSPORT_CODES + REPORT_VERDICTS
                + HINT_CODES + REVIEW_CODES
                + ["run-unreadable", "bytes-differ",
                   "json-differs", "text-differs",
                   "only-in-source", "only-in-replay",
                   "reason_code", "run_note_code", "CREW_COSMOS_RUNS_DIR"],
                "09 §5 коды")


def test_manual_09_glossary_terms(m09):
    sec = _between(m09, "## 6. Глоссарий", "## 6а. ")
    terms = ["**Журнал внешних вызовов попытки**", "**Класс воспроизводимости**",
             "**Обмены с моделью**", "**Повтор из журнала**",
             "**Повтор заново**", "**Попытка нажатия**", "**Прогон COSMOS**",
             "**Прогон CDPM (source_run)**", "**Прогон решателя (runId)**"]
    _assert_all(sec, terms, "09 §6 глоссарий")
    for term in terms:
        line = [ln for ln in sec.splitlines() if ln.startswith("- " + term)]
        assert len(line) == 1, term
    assert "«%s»" % LEGACY_TAB in sec[sec.index("**Прогон COSMOS**"):
                                       sec.index("**Прогон CDPM")]


# ------------------------------------------------------------------ контракт


def test_contract_channel_receipt_and_current_card(contract):
    sec = _between(contract, "### Task-package channel", "### Выгрузки решений пакета")
    _assert_all(sec, ["cosmos_run", "run_note_code", "cdpm/channel.py",
                      "verdict.json", "прогон создаётся автоматически",
                      "run: {run, title,", "file | channel | upload",
                      "holder.json", "cosmos-runs/current.json",
                      "package-bytes-missing"], "контракт, канал")


def test_contract_legacy_dashboard_heading_renamed(contract):
    assert "### «%s (расширенный вид)» dashboard" % LEGACY_TAB in contract
    assert "### «Прогоны» dashboard" not in contract


def test_contract_runs_section(contract, project):
    head = "### COSMOS runs (2026-09-07, owner's word: runs like CDPM; passport = reproduction recipe; replay from journal / replay fresh — this queue, owner's word 11:4x)"
    assert head in contract
    # заголовок — дословно по проекту (раздел 14; там он перенесён по строкам)
    assert head in _flat(project)
    sec = contract[contract.index(head):]
    _assert_all(sec, RUN_ENDPOINTS + LEGACY_ENDPOINTS_WITH_RUN,
                "контракт, ручки прогонов")
    _assert_all(sec, ENV_VARS + [CLI_INIT] + STORAGE_FILES,
                "контракт, настройки и хранилище")
    _assert_all(sec, RUN_REFUSAL_CODES + REPLAY_REFUSAL_CODES
                + REPORT_404_CODES + HINT_CODES + STATE_KINDS
                + ["_MACHINE_ROUTES", "401/403", "run-unreadable",
                   "cosmos-replay-report/v1", "same-outcome",
                   "not-comparable", "wall-clock-legit-difference",
                   "deterministic-expected-same", "not-run-for-replay",
                   "served_from", "ТОЛЬКО сессия", "replay={mode",
                   "{present: false}", "pre-passport", "pre-journal",
                   # ревью реализации 07.09
                   "storage-busy", "storage-io", "bad-mode", "cause",
                   'state: "кнопка: идёт"', "attempt_id",
                   "показания-решателя-на-пейринге",
                   "статус-прогона-решателя-на-пейринге",
                   "форматом писателя", "run-not-written"],
                "контракт, коды и формы")
    # «на диск не пишут никогда» снято: читающие пути ведут кэш реестра
    # страницы, обещание сужено до хранилища прогонов
    assert "никогда не пишет на диск" not in sec and "никогда не пишут на диск" not in sec
    # «из журнала» и «заново» описаны, отчёт назван схемой
    assert "«Из журнала» (`journal`)" in sec and "«Заново» (`fresh`)" in sec


# -------------------------------------------------------------------- обзор


def test_overview_sections_and_legacy_mentions(overview):
    assert "Витрина «Прогоны»" not in overview
    assert "Panel «Прогоны»" not in overview
    assert "Панель «Прогоны»" not in overview
    assert overview.count("«%s (расширенный вид)»" % LEGACY_TAB) >= 3
    orch = _between(overview, "### Оркестрация вычислений",
                    "### Хранение: файлы, а не СУБД")
    _assert_all(orch, ["webapp/cosmos_runs.py", "текущий прогон", "паспорт",
                       "external-calls", "повтор из журнала",
                       "повтор заново", "12я"], "обзор, оркестрация")
    store = _between(overview, "### Хранение: файлы, а не СУБД",
                     "### Паттерн «оверлей")
    _assert_all(store, ["cosmos-runs/", "run-NNNN/run.json", "task-inbox/",
                        "package-runs/", "cosmos-runs-archive/"],
                "обзор, хранение")
    client = _between(overview, "### Клиентская часть и интернационализация",
                      "### Ограничения, задокументированные в коде")
    # Числа обзора — из кода, не из головы: секции панели (TAB_GROUPS,
    # с cruns) и шаги тура (TOUR_STEPS, с gcruns).
    app_js = _read(HERE / "webapp" / "static" / "app.js")
    groups = app_js[app_js.index("TAB_GROUPS = ["):]
    groups = groups[:groups.index("];")]
    n_groups = len(re.findall(r'\{ id: "[a-z]+"', groups))
    n_admin = len(re.findall(r"admin: true", groups))
    assert '{ id: "cruns"' in groups
    assert ("%d секций, из них %d admin-секции" % (n_groups, n_admin)) in client
    assert "«Прогоны»" in client
    onboarding = _read(HERE / "webapp" / "static" / "onboarding.js")
    steps = onboarding[onboarding.index("TOUR_STEPS"):]
    steps = steps[:steps.index("];")]
    keys = re.findall(r'key: "([a-z]+)"', steps)
    assert "gcruns" in keys
    tour = _between(overview, "### Вводный тур", "### Интерактивное обучение")
    assert ("**%d шагов**" % len(keys)) in tour
    assert "«Прогоны»" in tour and "«Конвейер»" in tour


# ---------------------------------------------------------- согласованность


def test_docs_agree_on_endpoints_env_and_codes(m03, m09, contract):
    sec03 = _between(m03, "## 12я. Прогоны COSMOS", "## 13. ")
    sec09 = _between(m09, "### 2.3 ", "### 2.4 ")
    sec_c = contract[contract.index("### COSMOS runs (2026-09-07"):]
    for ep in RUN_ENDPOINTS:
        assert ep in m03 and ep in sec09 and ep in sec_c, ep
    for var in ENV_VARS:
        assert var in sec03 and var in m09 and var in sec_c, var
    codes09 = _between(m09, "## 5. Коды вердиктов и статусов", "## 6. Глоссарий")
    for code in RUN_REFUSAL_CODES + REPLAY_REFUSAL_CODES + REPORT_404_CODES:
        assert code in sec03 or code in m03, code
        assert code in codes09 and code in sec_c, code
    for kind in STATE_KINDS:
        assert "`%s`" % kind in sec03 and "`%s`" % kind in codes09, kind
        assert kind in sec_c, kind


def test_docs_carry_no_dataset_bound_examples(m03, m09, contract, overview):
    for name, text in (("03", m03), ("09", m09), ("контракт", contract),
                       ("обзор", overview)):
        found = [m for m in DATASET_BOUND_MARKERS if m in text]
        assert not found, "%s: примеры привязаны к набору данных: %s" % (name, found)


def test_docs_do_not_call_new_tab_by_legacy_dashboard_name(m03, m09):
    """Слово «Прогоны» в руководстве и справочнике теперь означает только
    вкладку прогонов COSMOS (раздел 12я); легаси-страница названа
    «История задач» везде, где на неё ссылаются."""
    for text in (m03, m09):
        assert "страница «Прогоны»" not in text
        assert "(«Прогоны»)" not in text
    assert "«История задач»" in m03 and "«История задач»" in m09
