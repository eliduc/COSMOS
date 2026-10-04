# -*- coding: utf-8 -*-
"""Стенд шаблонов проверок данных CDPM — форма мира `tables` (шаг 4
каталога, часть CDPM; проект
final-report/KATALOG-SHAG4-CDPM-STEND-TABLES-PROEKT-CLD.md).

Стендовая задача записи каталога — синтетический мир (книга «набор/лист»
с колонками и строками), спецификация шаблона проверки данных и ожидание
«столько-то находок». Стенд переводит мир в набор данных линии данных,
судит и исполняет спецификацию ТЕМ ЖЕ путём, что отчёт данных продукта
(`validate_spec` → `build_data_report`, свёртка лавин выключена), и
сравнивает число находок с ожиданием.

Спецификация стенда стоит на строке свода `R-STAND`, собранной из
заметки задачи (§0.7–0.8 проекта): встроенное основание «стенд» вне
перечня `data_report.BUILTIN_BASES`, и отчёт с ним не проходил бы
контракт отчёта данных. Отчёт стенда обязан проходить
`data_report.validate_report` — иначе это дефект стенда, а не мира.

Исходы задачи: `passed` / `failed` / `skipped`; `skipped` — только когда
исполнителя у CDPM нет (шаблон группы `certificate` либо имя, которого
не знает разрешитель генератора привязок), всё прочее — `failed`
словами. Имя шаблона разрешает ОДИН код на стенд и генератор —
`cdpm.bindings_build.executor_of`.

Задачи живут в записях каталога (`stand_tasks[]` формы `tables`); пока
их там нет — в посеве `cdpm/stand_tables_seed.json` (зона CDPM), и
источник задач честно назван в результатах (`tasks_source`).

    python -m cdpm.stand_tables
    python -m cdpm.stand_tables --out cdpm/stand_results.json [--tasks seed]
    python -m cdpm.stand_tables --check [cdpm/stand_results.json]

Коды возврата (как у генератора привязок): 0 — ни одного `failed` вне
`KNOWN_FAILED` (для `--check` — файл ещё и сходится с повторным
прогоном), 1 — есть, 2 — отказ словами (каталог недоступен, посев не
читается, коммит не похож на хеш, файла результатов нет).

Инструмент, не читатель: импорты из `cdpm` ему разрешены.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from cdpm import bindings_build as bb                          # noqa: E402
from cdpm import catalog_bridge as bridge                      # noqa: E402
from cdpm import data_checks as dc                             # noqa: E402
from cdpm import data_report as dr                             # noqa: E402
from template_catalog.export import dump_json                  # noqa: E402

#: Вид исполнителя, который судит стенд CDPM (прочие виды — COSMOS).
KIND = "check_template"
#: Форма мира, за которую отвечает этот стенд.
WORLD_FORM = "tables"
#: Идентификатор строки свода, на которой стоит спецификация стенда.
RULE_ID = "R-STAND"
#: Форма файла результатов.
FORM_VERSION = 1
#: Запас перечня синтетических наборов: до появления `dataset_ids` в
#: словаре `synthetic_codes` каталога перечень берётся отсюда.
DATASET_IDS = tuple(f"S{i}" for i in range(1, 10))
EMPLOYEE_PATTERN = r"^P[1-9][0-9]*$"
#: Колонки положительной проверки кодов (правило Ф5, зона CDPM):
#: имена колонок — знание линии данных, каталогу они не нужны.
STATION_COLUMNS = ("База приписки", "Место отдыха")
FLEET_COLUMNS = ("Тип ВС",)
EMPLOYEE_COLUMN_PREFIX = "Табельн"
#: Ячейка-код: латинские заглавные без цифр (отрицательное правило A03
#: каталога — синтетику оно подтвердить не может, поэтому рядом живёт
#: положительная проверка по колонкам).
CODE_RE = re.compile(r"^[A-Z]{3,4}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")

SEED_FILE = REPO / "cdpm" / "stand_tables_seed.json"
RESULTS_FILE = REPO / "cdpm" / "stand_results.json"
#: Отпечатки свежести результатов — все модули, чей код читает
#: исполнитель при прогоне задач формы `tables`: `data_checks` —
#: исполнители и закон статусов; `model` — канон уточнителя области;
#: `history_section` — срез и раздел истории; `history_journal` — имена
#: листов журнала в отказе `validate_spec`; `data_report` — классы,
#: счётчики и контракт отчёта; плюс сам стенд и посев. Истина свежести
#: всё равно повторный прогон, отпечатки — паспорт прогона.
CODE_FILES = ("cdpm/data_checks.py", "cdpm/model.py",
              "cdpm/history_section.py", "cdpm/history_journal.py",
              "cdpm/data_report.py", "cdpm/stand_tables.py",
              "cdpm/stand_tables_seed.json")
#: Оговорка записи: дописывается к заметке КАЖДОГО `passed` по ней и
#: уходит в строку `stand_verified` привязок. Снимается по слову
#: владельца вместе с появлением шаблона составного ключа.
RESERVATIONS: dict[str, str] = {
    "families/fleet_exclusions":
        "подтверждает исполнителя шаблона по одному ключу; пара табельный "
        "× тип шаблоном не выражается",
}
#: Разобранные словами `failed`, которые гейт пропускает: (запись,
#: задача) → причина. Пополняется ТОЛЬКО по слову владельца после
#: разбора «дефект мира → правка мира; дефект исполнителя → доклад».
KNOWN_FAILED: dict[tuple[str, str], str] = {}
#: Заметка исхода `skipped` у шаблонов сертификата — слова генератора
#: привязок (один разрешитель, одни слова).
NOTE_CERTIFICATE = bb.NOTE_CERTIFICATE
NOTE_NO_KIND = ("ожидания по виду check_template у задачи нет — стенд "
                "CDPM судит только этот вид")
#: Названия источника задач для печати.
SOURCE_WORDS = {"catalog": "каталог", "seed": "посев", "none": "нет задач"}

_NOTED: dict = {}


class StandError(ValueError):
    """Честный отказ стенда: причина всегда названа словами."""


class StandWorldError(StandError):
    """Мир задачи не по форме `tables`: адрес и причина словами."""


# --------------------------------------------------------------- посев
def load_seed(path=SEED_FILE) -> dict:
    """Задачи посева: {«families/<id>»: [задача, …]}. Отказ словами —
    файл не читается, форма не та, задачи записи не отсортированы по
    `id`, `id` повторяется или не по образцу."""
    p = Path(path)
    if not p.is_file():
        raise StandError(f"посева стендовых задач {p} нет — стенду нечего "
                         f"прогонять, пока задач нет и в каталоге")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        raise StandError(f"{p} не читается ({e})") from None
    if not isinstance(doc, dict):
        raise StandError(f"{p}: файл посева — не объект")
    if doc.get("form_version") != FORM_VERSION:
        raise StandError(f"{p}: форма посева {doc.get('form_version')!r}, "
                         f"стенд знает {FORM_VERSION}")
    if doc.get("world_form") != WORLD_FORM:
        raise StandError(f"{p}: форма мира {doc.get('world_form')!r}, стенд "
                         f"судит {WORLD_FORM!r}")
    records = doc.get("records")
    if not isinstance(records, dict) or not records:
        raise StandError(f"{p}: в посеве нет ни одной записи с задачами")
    out: dict[str, list] = {}
    for addr, tasks in records.items():
        if not isinstance(tasks, list) or not tasks:
            raise StandError(f"{p}: у записи {addr} задачи не списком либо "
                             f"их нет")
        ids = []
        for t in tasks:
            if not isinstance(t, dict):
                raise StandError(f"{p}: {addr}: задача не объект")
            tid = t.get("id")
            if not isinstance(tid, str) or not ID_RE.match(tid):
                raise StandError(f"{p}: {addr}: id задачи {tid!r} не по "
                                 f"образцу {ID_RE.pattern}")
            if tid in ids:
                raise StandError(f"{p}: {addr}: повтор id задачи {tid!r}")
            for field in ("world", "expected"):
                if not isinstance(t.get(field), dict) or not t[field]:
                    raise StandError(f"{p}: {addr}/{tid}: нет поля {field!r}")
            if not isinstance(t.get("note"), str) or not t["note"].strip():
                raise StandError(f"{p}: {addr}/{tid}: у задачи нет заметки")
            ids.append(tid)
        if ids != sorted(ids):
            raise StandError(f"{p}: {addr}: задачи не отсортированы по id "
                             f"({ids}) — каталог требует канонического "
                             f"порядка")
        out[str(addr)] = list(tasks)
    return out


def tasks_of(record: dict, section: str, seed: dict,
             *, source: str = "auto") -> tuple[list, str]:
    """Задачи формы `tables` записи и откуда они взяты: «catalog» —
    задачи стоят в записи каталога, «seed» — в посеве, «none» — их нет.
    Задачи иных форм стенду CDPM не принадлежат и молча пропускаются."""
    addr = f"{section}/{record.get('id')}"
    in_catalog = [t for t in (record.get("stand_tasks") or [])
                  if isinstance(t, dict)
                  and (t.get("world") or {}).get("form") == WORLD_FORM]
    in_seed = [t for t in (seed.get(addr) or []) if isinstance(t, dict)]
    if source == "catalog":
        return (in_catalog, "catalog" if in_catalog else "none")
    if source == "seed":
        return (in_seed, "seed" if in_seed else "none")
    if in_catalog:
        return in_catalog, "catalog"
    if in_seed:
        return in_seed, "seed"
    return [], "none"


# ---------------------------------------------------------- переводчик
def _history_aliases() -> set:
    from cdpm import history_section as hs
    return (set(hs.HISTORY_SHEETS) | set(hs.APPROVED_SHEETS)
            | set(hs.JOURNAL_SHEETS) | set(hs.CALENDAR_SHEETS)
            | set(hs.PRESENCE_SHEETS))


def _codes(dicts: dict) -> dict:
    """Синтетические коды словаря каталога; без ключа `dataset_ids` —
    перечень из константы стенда (с честной печатью один раз)."""
    sc = (dicts or {}).get("synthetic_codes") or {}
    ids = tuple(str(x) for x in (sc.get("dataset_ids") or ()))
    if not ids:
        ids = DATASET_IDS
        if not _NOTED.get("dataset_ids"):
            _NOTED["dataset_ids"] = True
            print("стенд CDPM: перечень наборов из константы стенда — "
                  "словарь каталога dataset_ids ещё не несёт", flush=True)
    try:
        emp = re.compile(str(sc.get("employee_pattern") or EMPLOYEE_PATTERN))
    except re.error:
        emp = re.compile(EMPLOYEE_PATTERN)
    return {"dataset_ids": ids,
            "stations": set(str(x) for x in (sc.get("stations") or ())),
            "fleet_types": set(str(x) for x in (sc.get("fleet_types") or ())),
            "employee": emp}


def world_to_dataset(world: dict, dicts: dict | None = None) -> dict:
    """Мир формы `tables` → набор данных линии данных (ДЛ-3). Чистая
    функция; отказ — `StandWorldError` словами с адресом (ключ таблицы,
    номер строки, колонка). Правила Ф1–Ф6 проекта §1.1."""
    codes = _codes(dicts or {})
    if not isinstance(world, dict) or world.get("form") != WORLD_FORM \
            or set(world) != {"form", "tables"}:
        raise StandWorldError(
            f"Ф1: мир формы {WORLD_FORM!r} — ровно поля 'form' и 'tables'; "
            f"дано {sorted(world) if isinstance(world, dict) else type(world).__name__}")
    tables_in = world.get("tables")
    if not isinstance(tables_in, dict) or not tables_in:
        raise StandWorldError("Ф6: в мире нет ни одной таблицы")
    aliases = _history_aliases()
    tables: dict = {}
    for key, t in tables_in.items():
        key = str(key)
        if key.count("/") != 1:
            raise StandWorldError(
                f"Ф2: ключ таблицы {key!r} не вида «набор/лист» (ровно одна "
                f"косая черта)")
        sid, sheet = key.split("/")
        if sid not in codes["dataset_ids"]:
            raise StandWorldError(
                f"Ф2: {key}: идентификатор набора {sid!r} не синтетический "
                f"(перечень {list(codes['dataset_ids'])})")
        if not sheet:
            raise StandWorldError(f"Ф2: {key}: имя листа пустое")
        if sheet.strip().lower() in aliases:
            raise StandWorldError(
                f"Ф2: {key}: имя листа — псевдоним раздела истории "
                f"сотрудника; такой лист исполнитель целью проверок правил "
                f"не принимает")
        if not isinstance(t, dict):
            raise StandWorldError(f"Ф3: {key}: таблица не объект")
        cols = t.get("columns")
        if not isinstance(cols, list) or not cols \
                or any(not isinstance(c, str) or not c for c in cols) \
                or len(set(cols)) != len(cols):
            raise StandWorldError(
                f"Ф3: {key}: columns — непустой список неповторяющихся "
                f"непустых имён колонок; дано {cols!r}")
        rows_in = t.get("rows")
        if not isinstance(rows_in, list):
            raise StandWorldError(f"Ф4: {key}: rows — не список")
        rows_out = []
        for i, row in enumerate(rows_in, start=2):
            if not isinstance(row, dict):
                raise StandWorldError(
                    f"Ф4: {key} строка {i} — не объект «колонка: значение» "
                    f"(список ячеек стенд не принимает: форма мира одна)")
            if set(row) != set(cols):
                raise StandWorldError(
                    f"Ф4: {key} строка {i}: ключи строки {sorted(row)} не "
                    f"совпадают с колонками {sorted(cols)}")
            for col in cols:
                val = row[col]
                if not isinstance(val, str):
                    raise StandWorldError(
                        f"Ф4: {key} строка {i} колонка {col!r}: ячейка не "
                        f"строка ({type(val).__name__}); пустая ячейка — «»")
                _cell_codes(codes, key, i, col, val)
            rows_out.append({c: row[c] for c in cols})
        if sheet in tables.setdefault(sid, {}):
            raise StandWorldError(f"повтор листа {key!r} в мире")
        tables[sid][sheet] = {"columns": list(cols), "rows": rows_out}
    sources = []
    for sid in sorted(tables):
        blob = json.dumps(tables[sid], ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        sources.append({"sid": sid, "file": f"stand-{sid}.json",
                        "sha256": hashlib.sha256(blob).hexdigest(),
                        "rows_seen": sum(len(s["rows"])
                                         for s in tables[sid].values())})
    return {"sources": sources, "tables": tables}


def _cell_codes(codes: dict, key: str, row: int, col: str, val: str) -> None:
    """Правило Ф5: отрицательная часть (правило каталога A03) и
    положительная (только гейт стенда) — отказ с адресом ячейки."""
    where = f"{key} строка {row} колонка {col!r}"
    if col.startswith(EMPLOYEE_COLUMN_PREFIX):
        if not codes["employee"].match(val):
            raise StandWorldError(
                f"Ф5: {where}: табельный {val!r} не по синтетическому "
                f"образцу {codes['employee'].pattern}")
        return
    if CODE_RE.match(val) and val not in codes["stations"] \
            and val not in codes["fleet_types"]:
        raise StandWorldError(
            f"Ф5: {where}: код {val!r} не синтетический (ни станция, ни тип "
            f"судна)")
    if col in STATION_COLUMNS and val and val not in codes["stations"]:
        raise StandWorldError(
            f"Ф5: {where}: код станции {val!r} не из синтетического перечня "
            f"{sorted(codes['stations'])}")
    if col in FLEET_COLUMNS and val and val not in codes["fleet_types"]:
        raise StandWorldError(
            f"Ф5: {where}: тип судна {val!r} не из синтетического перечня "
            f"{sorted(codes['fleet_types'])}")


# ------------------------------------------------------- судья и прогон
def _statement(task: dict) -> str:
    note = task.get("note")
    return note if isinstance(note, str) and note.strip() \
        else f"стендовая задача {task.get('id')}"


def svod_of(task: dict) -> dict:
    """Односторочный свод задачи: спецификация стенда стоит на строке
    `R-STAND` — ровно то, что читают `validate_spec` (строка есть, вид
    проверяемый, блокеров нет), `build_data_report` (класс находки и
    `rule_refs`) и `validate_report` (трассировка основания)."""
    return {"rules": [{"id": RULE_ID, "kind": "HARD",
                       "statement": _statement(task), "blockers": []}]}


def svod_rules_of(svod: dict) -> dict:
    """Строки свода по `id` — форма, которую ждёт `validate_spec`."""
    return {r["id"]: r for r in (svod or {}).get("rules", [])}


def spec_of(task: dict, exp: dict) -> dict:
    """Спецификация проверки данных стендовой задачи."""
    return {"id": f"C-стенд-{task.get('id')}",
            "basis": {"rule": RULE_ID},
            "template": exp.get("template"),
            "params": dict(exp.get("params") or {}),
            "statement": _statement(task),
            "unit": "строка",
            "severity": "КРИТИЧЕСКОЕ",
            "status": "скомпилирована"}


def template_fields(dicts: dict, name: str) -> list:
    """Обязательные поля шаблона по словарю каталога; у имён истории и
    горизонта запись словаря — ключ-образец («история-*»)."""
    table = (dicts or {}).get("check_templates") or {}
    spec = table.get(name)
    if not isinstance(spec, dict):
        for key, other in table.items():
            if str(key).endswith("*") and str(name).startswith(str(key)[:-1]):
                spec = other
                break
    return [str(f) for f in ((spec or {}).get("fields") or [])]


def _outcome(result: str, note: str, *, expected=None, got=None) -> dict:
    return {"result": result,
            "got": got or {"findings": None, "status": None,
                           "rows_checked": None},
            "expected": expected, "note": note}


def run_task(record: dict, task: dict, dicts: dict,
             *, address: str | None = None) -> dict:
    """Исход одной стендовой задачи: {result, got, expected, note}.
    `address` — адрес записи («families/<id>») для оговорок
    `RESERVATIONS`; не задан — выводится из id записи раздела `families`.
    Функция ничего не пишет и не печатает; исключения вне перечисленных
    не ловятся — дефект стенда должен быть виден целиком."""
    addr = address or f"families/{record.get('id')}"
    exp = (task.get("expected") or {}).get(KIND)
    if not isinstance(exp, dict):
        return _outcome("skipped", NOTE_NO_KIND)
    template = exp.get("template")
    names = [str(n) for n in (record.get("check_templates") or [])]
    if str(template) not in names:
        return _outcome("failed", f"шаблон {template!r} записью не объявлен "
                                  f"(её шаблоны: {names})", expected=exp)
    groups = {str(n): (spec.get("group") if isinstance(spec, dict) else None)
              for n, spec in ((dicts or {}).get("check_templates")
                              or {}).items()}
    try:
        executor = bb.executor_of(str(template), groups)
    except bb.BindingsBuildError as e:
        return _outcome("skipped", str(e), expected=exp)
    if executor.get("state") != "есть":
        return _outcome("skipped", executor.get("note") or NOTE_CERTIFICATE,
                        expected=exp)
    missing = [f for f in template_fields(dicts, str(template))
               if f not in (exp.get("params") or {})]
    if missing:
        return _outcome("failed", f"в ожидании нет полей {missing} — они "
                                  f"обязательны у шаблона {template} по "
                                  f"словарю каталога", expected=exp)
    try:
        dataset = world_to_dataset(task.get("world") or {}, dicts)
    except StandWorldError as e:
        return _outcome("failed", str(e), expected=exp)
    svod = svod_of(task)
    spec = spec_of(task, exp)
    reason = dc.validate_spec(spec, dataset, svod_rules_of(svod))
    if reason:
        return _outcome("failed", f"спецификация отбракована: {reason}",
                        expected=exp)
    report = dc.build_data_report("стенд", dataset, [spec],
                                  sanity_threshold=False, svod=svod)
    problems = dr.validate_report(report, svod)
    if problems:
        return _outcome("failed", "отчёт стенда не проходит контракт отчёта "
                                  "данных: " + "; ".join(str(p) for p in
                                                         problems[:5]),
                        expected=exp)
    check = report["checks"][0]
    got = {"findings": sum(1 for f in report["findings"]
                           if f.get("check") == spec["id"]),
           "status": check.get("status"),
           "rows_checked": check.get("rows_checked")}
    if check.get("status") != "прогнана":
        return _outcome("failed",
                        f"{check.get('status')}: {check.get('reason')}",
                        expected=exp, got=got)
    note = (f"получено {got['findings']}, ожидалось {exp.get('findings')} "
            f"(run_spec_ex)")
    if got["findings"] != exp.get("findings"):
        return _outcome("failed", note, expected=exp, got=got)
    reservation = RESERVATIONS.get(addr)
    if reservation:
        note = f"{note}; {reservation}"
    return _outcome("passed", note, expected=exp, got=got)


def run_all(catalog, *, seed: dict, records=None,
            source: str = "auto") -> tuple[list[dict], dict]:
    """Прогон всех стендовых задач формы `tables`: (результаты, источник
    задач по записям). Записи — те же, что у генератора привязок: со
    статусом «есть» и объявленным видом `check_template`. Порядок —
    записи каталога, внутри записи задачи по `id`.

    Отступление от §3.1 проекта (там `-> list[dict]` «плюс сводка»):
    сводка `tasks_source` возвращается второй частью пары — она нужна и
    файлу результатов, и итоговой строке печати, а считать её вторым
    проходом значило бы разойтись с прогоном."""
    dicts = catalog.dictionaries() or {}
    only = set(records or ())
    out: list[dict] = []
    sources: dict[str, str] = {}
    for section, rec in bb._records(catalog):
        addr = f"{section}/{rec.get('id')}"
        if only and addr not in only:
            continue
        tasks, src = tasks_of(rec, section, seed, source=source)
        if src != "none":
            sources[addr] = src
        for task in sorted(tasks, key=lambda t: str(t.get("id"))):
            outcome = run_task(rec, task, dicts, address=addr)
            out.append({"record": addr, "task": str(task.get("id")),
                        "executor_kind": KIND, "run_id": None, **outcome})
    return out, sources


# ------------------------------------------------------- файл и свежесть
def code_fingerprints() -> dict:
    """sha256 файлов `CODE_FILES` — паспорт прогона стенда."""
    out = {}
    for name in CODE_FILES:
        p = REPO / name
        if not p.is_file():
            raise StandError(f"файла {name} нет — отпечатки прогона неполны")
        # Перевод строки не входит в отпечаток: чистый worktree на Windows
        # материализует файлы с CRLF (autocrlf), рабочая копия — с LF, а
        # код от этого не меняется (тот же закон, что у сверки инструмента
        # каталога и гейта паритета; красный полный pytest COSMOS 08.09 —
        # «результаты стенда устарели» при равном коде).
        out[name] = hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return out


def results_doc(catalog, results: list, *, tasks_source: dict,
                commit: str, at: str) -> dict:
    """Документ результатов стенда (форма §3.2 проекта — надмножество
    формы COSMOS: плюс `tasks_source`)."""
    if not _COMMIT_RE.match(commit or ""):
        raise StandError(f"коммит {commit!r} не похож на хеш git "
                         f"(7–40 знаков 0-9a-f)")
    if not _DATETIME_RE.match(at or ""):
        raise StandError(f"момент {at!r} не ISO 8601 с поясом "
                         f"(2026-09-08T12:00:00+03:00)")
    return {"form_version": FORM_VERSION,
            "catalog_version": str(catalog.version()),
            "catalog_fingerprint": str(catalog.fingerprint()),
            "code": code_fingerprints(),
            "at": at,
            "commit": commit,
            # Решатель стенду CDPM не нужен; ключ — ради единой формы
            # файла результатов с COSMOS.
            "solver": None,
            "tasks_source": dict(tasks_source),
            "results": list(results)}


def write_results(path, doc: dict) -> Path:
    """Файл результатов байтами (LF, форма файлов каталога)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(dump_json(doc).encode("utf-8"))
    return p


def read_results(path) -> dict:
    """Файл результатов стенда; нет файла или не читается — отказ
    словами."""
    p = Path(path)
    if not p.is_file():
        raise StandError(f"файла результатов стенда {p} нет — прогоните "
                         f"python -m cdpm.stand_tables --out {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        raise StandError(f"{p} не читается ({e})") from None
    if not isinstance(doc, dict):
        raise StandError(f"{p}: файл результатов — не объект")
    return doc


def stale_reason(doc: dict, catalog) -> str | None:
    """Почему результаты стенда устарели, либо None. Судится отметка
    каталога и отпечатки кода — паспорт прогона; истина всё равно
    повторный прогон (гейт судит и его)."""
    if not isinstance(doc, dict):
        return "результаты не объект"
    if doc.get("form_version") != FORM_VERSION:
        return (f"форма результатов {doc.get('form_version')!r}, стенд "
                f"знает {FORM_VERSION}")
    try:
        fingerprint = str(catalog.fingerprint())
        version = str(catalog.version())
    except Exception as e:                                    # noqa: BLE001
        return f"каталог не отдал отметку ({e})"
    if doc.get("catalog_fingerprint") != fingerprint:
        return (f"отпечаток каталога {doc.get('catalog_fingerprint')!r}, у "
                f"каталога {version} — {fingerprint!r}")
    # The fingerprint covers the canonical body, not its version. Keep
    # legal draft/final suffix transitions, but never carry stand evidence
    # across a different numeric version or an unreadable identity.
    from template_catalog.manifest import compare_versions
    try:
        same_version = compare_versions(version, doc.get("catalog_version")).level == "same"
    except ValueError as e:
        return f"версия каталога в результатах стенда негодна: {e}"
    if not same_version:
        return (f"версия каталога в результатах {doc.get('catalog_version')!r}, "
                f"у каталога {version!r} — другая числовая тройка")
    code = doc.get("code") or {}
    try:
        now = code_fingerprints()
    except StandError as e:
        return str(e)
    for name, digest in now.items():
        if code.get(name) != digest:
            return (f"отпечаток {name} в результатах "
                    f"{code.get(name)!r}, у файла {digest!r}")
    extra = sorted(set(code) - set(now))
    if extra:
        return f"в результатах отпечатки лишних файлов {extra}"
    return None


# ------------------------------------------------------------- запуск
def _catalog():
    """Каталог моста CDPM с его отметкой; без каталога — отказ словами:
    результатов стенда без отметки каталога не бывает. Шаг 7: мост
    поднимает `CatalogRequired`, рубеж команды переводит его словами."""
    try:
        bridge.stamp()
        return bridge.catalog()
    except bridge.CATALOG_ERRORS as e:
        raise StandError(
            f"каталог шаблонов недоступен: {bridge.words_of(e)} — результаты "
            f"стенда без версии и отпечатка каталога не выпускаются") \
            from None


def counts(results: list) -> dict:
    out = {"passed": 0, "failed": 0, "skipped": 0}
    for r in results:
        if r.get("result") in out:
            out[r["result"]] += 1
    return out


def unexpected_failures(results: list) -> list:
    """`failed` вне разобранного перечня `KNOWN_FAILED` — красный гейт."""
    return [r for r in results if r.get("result") == "failed"
            and (r.get("record"), r.get("task")) not in KNOWN_FAILED]


def _source_word(sources: dict) -> str:
    kinds = set(sources.values())
    if not kinds:
        return SOURCE_WORDS["none"]
    if kinds == {"catalog"}:
        return SOURCE_WORDS["catalog"]
    if kinds == {"seed"}:
        return SOURCE_WORDS["seed"]
    return "смешанный"


def print_results(results: list, sources: dict) -> None:
    head = (f"{'запись':26} {'задача':20} {'шаблон':32} {'исход':8} "
            f"{'получено/ожидалось':20} заметка")
    print(head)
    print("-" * len(head))
    for r in results:
        exp = r.get("expected") or {}
        got = (r.get("got") or {}).get("findings")
        pair = f"{got}/{exp.get('findings')}"
        print(f"{r['record']:26} {r['task']:20} "
              f"{str(exp.get('template') or '—'):32} {r['result']:8} "
              f"{pair:20} {r.get('note') or ''}")
    c = counts(results)
    print(f"стенд CDPM: задач {len(results)} | passed {c['passed']} | "
          f"failed {c['failed']} | skipped {c['skipped']} | источник задач: "
          f"{_source_word(sources)}")


def results_differences(built: list, doc_results: list) -> list[str]:
    """Отличия результатов файла от повторного прогона, словами."""
    diff: list[str] = []
    by_key = {(str(r.get("record")), str(r.get("task"))): r
              for r in doc_results if isinstance(r, dict)}
    seen = set()
    for r in built:
        key = (r["record"], r["task"])
        seen.add(key)
        other = by_key.get(key)
        if other is None:
            diff.append(f"{key[0]}/{key[1]}: результата в файле нет")
            continue
        for field in ("result", "executor_kind", "got", "expected", "note"):
            if other.get(field) != r.get(field):
                diff.append(f"{key[0]}/{key[1]}: поле {field} — в файле "
                            f"{other.get(field)!r}, прогон дал "
                            f"{r.get(field)!r}")
    for key in by_key:
        if key not in seen:
            diff.append(f"{key[0]}/{key[1]}: результат в файле лишний "
                        f"(прогон такой задачи не давал)")
    return diff


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--out", "--write-results", dest="out",
                      help="куда записать файл результатов стенда")
    mode.add_argument("--check", nargs="?", const=str(RESULTS_FILE),
                      help="сверить файл результатов с повторным прогоном, "
                           "ничего не писать")
    ap.add_argument("--tasks", choices=("auto", "seed", "catalog"),
                    default="auto",
                    help="источник задач: каталог, посев или auto "
                         "(каталог, если он их несёт)")
    ap.add_argument("--record", action="append", default=None,
                    help="прогнать только эти записи (families/<id>)")
    ap.add_argument("--seed", default=str(SEED_FILE),
                    help="файл посева стендовых задач")
    ap.add_argument("--commit", help="хеш происхождения вместо git HEAD")
    ap.add_argument("--at", help="момент прогона (ISO 8601 с поясом) "
                                 "вместо текущего")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        catalog = _catalog()
        seed = load_seed(a.seed)
        results, sources = run_all(catalog, seed=seed, records=a.record,
                                   source=a.tasks)
        doc_file = read_results(a.check) if a.check else None
        # В сверке происхождение берётся у проверяемого файла (сверке
        # они безразличны, а git для неё тогда не нужен).
        commit = a.commit
        at = a.at
        if a.check:
            commit = commit or doc_file.get("commit")
            at = at or doc_file.get("at")
        commit = commit or bb.head_commit()
        at = at or datetime.now().astimezone().isoformat(timespec="seconds")
        doc = results_doc(catalog, results, tasks_source=sources,
                          commit=commit, at=at)
    except (StandError, bb.BindingsBuildError) as e:
        print(f"стенд CDPM {'не сверен' if a.check else 'не прогнан'}: {e}")
        return 2
    print_results(results, sources)
    bad = unexpected_failures(results)
    code = 1 if bad else 0
    for r in bad:
        print(f"  ! {r['record']}/{r['task']}: {r.get('note')}")
    if a.check:
        why = stale_reason(doc_file, catalog)
        # При отборе записей сверяется тот же отбор: не прогнанные сейчас
        # результаты файла «лишними» не называются.
        only = set(a.record or ())
        diff = results_differences(
            results, [r for r in (doc_file.get("results") or [])
                      if not only or str(r.get("record")) in only])
        if why:
            print(f"сверка {a.check}: результаты устарели: {why}")
            code = 1
        if diff:
            print(f"сверка {a.check}: расхождений {len(diff)}")
            for d in diff:
                print("  " + d)
            code = 1
        if not why and not diff:
            print(f"сверка {a.check}: расхождений 0 | каталог "
                  f"{doc['catalog_version']} | отпечаток "
                  f"{doc['catalog_fingerprint']}")
        return code
    if a.out:
        path = write_results(a.out, doc)
        print(f"записаны результаты стенда {path}: каталог "
              f"{doc['catalog_version']} | отпечаток "
              f"{doc['catalog_fingerprint']} | момент {doc['at']} | коммит "
              f"{doc['commit']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
