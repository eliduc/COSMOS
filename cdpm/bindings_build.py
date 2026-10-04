# -*- coding: utf-8 -*-
"""Генератор привязок CDPM к записям каталога шаблонов — `cdpm/bindings.json`
(шаг 4 каталога, часть CDPM; проект
final-report/KATALOG-SHAG4-CDPM-PRIVYAZKI-CLD.md, схема §7
final-report/KATALOG-SHABLONOV-SHEMA-ZAPISI-CLD.md и `$defs/bindings_file`
в template_catalog/schemas/catalog.schema.json).

Запись каталога объявляет виды исполнителей; вид `check_template` —
«шаблон проверки данных», и его исполнитель у CDPM — код проверок
данных: спецификацию `{template, params}` судит `validate_spec`,
исполняет `run_spec_ex` (`cdpm/data_checks.py`); шаблоны листа истории
и горизонта («история-…», «горизонт-…») — `history_section.run_check`.
Шаблоны группы `certificate` («мера-мягкого-правила»,
«параметр-мира-применён») исполняет COSMOS (`vmp/run/roster_certify.py`)
— у CDPM для них честное «нет» с заметкой. Запись без вида
`check_template` строки не даёт: полнота по прочим видам — дело COSMOS.

Файл собирается ровно по `$defs/bindings_file`, без своих полей, и
детерминированно: одинаковые входы дают байт-в-байт тот же файл (LF,
`ensure_ascii=False`, отступ 1, ключи по алфавиту — форма
`template_catalog.export.dump_json`). Каталог берётся мостом
`cdpm.catalog_bridge` (один экземпляр на процесс, путь — настройка
продукта `CREW_TEMPLATE_CATALOG`), поэтому версия и отпечаток в шапке —
отметка ТОГО каталога, которым файл сделан.

    python cdpm/bindings_build.py --out cdpm/bindings.json
        [--commit <hex>] [--generated-at <ISO 8601 с поясом>]
        [--stand-results cdpm/stand_results.json]
    python cdpm/bindings_build.py --check cdpm/bindings.json
        [--stand-results cdpm/stand_results.json]

Строки `stand_verified` записи берутся из файла результатов стенда
шаблонов проверок данных (`cdpm/stand_tables.py`) — только по задачам,
которые стоят в самой записи каталога, и только по виду
`check_template`; без ключа `--stand-results` строки пусты. Устаревшие
результаты (иная отметка каталога либо иные отпечатки кода) — отказ
словами: файл привязок с чужими подтверждениями не выпускается.

`--check` собирает в памяти и сравнивает с файлом БЕЗ полей
`generated_at` и `generated_from` (они меняются от запуска к запуску;
сборка сверки берёт их у проверяемого файла, поэтому git для сверки не
нужен). Коды возврата: 0 — совпало, 1 — расхождения (названы словами:
запись, исполнитель, поле), 2 — отказ. Отказы всегда словами: каталог
недоступен; у каталога нет словаря `check_templates`; имя шаблона
записи неизвестно CDPM; символ исполнителя «есть» не импортируется.

Каждый выпуск каталога (новый отпечаток) требует перегенерации файла —
это же судит гейт продукта `tests/test_cdpm_bindings.py`.

Инструмент, не читатель: импорты из `cdpm` ему разрешены.
"""
from __future__ import annotations

import argparse
import importlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from cdpm import catalog_bridge as bridge                            # noqa: E402
from cdpm.data_checks import TEMPLATES                               # noqa: E402
from cdpm import export_task_package as task_package                 # noqa: E402
from template_catalog.export import dump_json                        # noqa: E402

#: Имя продукта в шапке привязок (словарь `products` каталога).
PRODUCT = "CDPM"
#: Вид исполнителя, за который отвечает CDPM (прочие виды — COSMOS).
KIND = "check_template"
#: Исполнитель шаблонов перечня `data_checks.TEMPLATES`.
DATA_CHECKS_FILE = "cdpm/data_checks.py"
DATA_CHECKS_SYMBOL = "run_spec_ex"
#: Исполнитель шаблонов листа истории и горизонта.
HISTORY_FILE = "cdpm/history_section.py"
HISTORY_SYMBOL = "run_check"
#: Префиксы встроенных шаблонов истории/горизонта (словарь каталога
#: называет их «история-*» и «горизонт-*», группа `builtin_prefix`).
HISTORY_PREFIXES = ("история-", "горизонт-")
#: Группа словаря `check_templates`, исполнитель которой — COSMOS.
CERTIFICATE_GROUP = "certificate"
NOTE_PRESENT = "спецификацию судит validate_spec"
NOTE_CERTIFICATE = ("шаблон сертификата — исполнитель COSMOS "
                    "(vmp/run/roster_certify.py)")
#: Стендовые подтверждения — из файла результатов стенда формы `tables`
#: (`cdpm/stand_results.json`, ключ `--stand-results`). Без файла строка
#: записи пуста, как и до стенда: подтверждать нечем.

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")
#: Поля шапки, которые от запуска к запуску меняются законно и в сверке
#: `--check` не участвуют.
VOLATILE = ("generated_at", "generated_from")
HEADER = ("product", "product_version", "catalog_version",
          "catalog_fingerprint", "executor_kinds")
#: Зона ответственности продукта в шапке (правило B04, согласовано с
#: COSMOS 07.09: «есть» у кого-то закрывает вид; иначе явную строку
#: обязан дать каждый продукт, в чьей зоне вид). Поле необязательное и
#: закрытой схемой допускается с каталога 0.2.0-draft.1 — пишется только
#: когда схема читателя его знает; без поля читатель выводит зону из
#: строк файла (у CDPM она та же: один вид).
EXECUTOR_KINDS = (KIND,)


class BindingsBuildError(Exception):
    """Честный отказ генератора: причина всегда названа словами."""


# ------------------------------------------------------------- сборка
def _schema_allows(field: str) -> bool:
    """Допускает ли закрытая схема привязок читателя (`$defs/bindings_file`)
    поле шапки `field`. Схема не читается — считается, что нет: файл не
    по схеме хуже файла без необязательного поля."""
    try:
        from template_catalog.validate import _load_schema
        schema = _load_schema() or {}
        props = (schema.get("$defs", {}).get("bindings_file", {})
                 .get("properties", {}))
        return field in props
    except Exception:                                         # noqa: BLE001
        return False


def _module_name(file: str) -> str:
    """Путь файла от корня репозитория → имя модуля: разделители в
    точки, без «.py» (`cdpm/data_checks.py` → `cdpm.data_checks`)."""
    return file.replace("\\", "/").removesuffix(".py").replace("/", ".")


def resolve_symbol(file: str, symbol: str):
    """Объект исполнителя: импорт модуля по `file` и `getattr` по
    точечному пути `symbol` (`Класс.метод` тоже). Не нашлось — отказ
    словами: привязка обещает код, которого нет."""
    try:
        obj = importlib.import_module(_module_name(file))
    except Exception as e:                                    # noqa: BLE001
        raise BindingsBuildError(
            f"модуль {file} не импортируется: {e}") from None
    for part in symbol.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise BindingsBuildError(
                f"в {file} нет символа {symbol!r} (не нашлось "
                f"{part!r})") from None
    return obj


def _present(name: str, file: str, symbol: str) -> dict:
    return {"kind": KIND, "state": "есть", "lang": "python", "file": file,
            "symbol": symbol, "template": name, "note": NOTE_PRESENT,
            "cr_key": None}


def executor_of(name: str, groups: dict) -> dict:
    """Исполнитель CDPM одного имени шаблона записи. Один разрешитель на
    генератор привязок и стенд (`cdpm/stand_tables.py`): перечень
    `data_checks.TEMPLATES` → префиксы «история-»/«горизонт-» → группа
    `certificate` словаря → отказ словами. Стенд читает его исход: «есть»
    — прогон, «нет» — `skipped` со словами заметки, отказ — `skipped` со
    словами генератора; так у обоих законов одни слова."""
    if name in TEMPLATES:
        return _present(name, DATA_CHECKS_FILE, DATA_CHECKS_SYMBOL)
    if name.startswith(HISTORY_PREFIXES):
        return _present(name, HISTORY_FILE, HISTORY_SYMBOL)
    if groups.get(name) == CERTIFICATE_GROUP:
        return {"kind": KIND, "state": "нет", "lang": None, "file": None,
                "symbol": None, "template": name, "note": NOTE_CERTIFICATE,
                "cr_key": None}
    raise BindingsBuildError(
        f"шаблон {name!r} каталогу известен, а CDPM — нет: его нет ни в "
        f"data_checks.TEMPLATES, ни среди префиксов "
        f"{'/'.join(HISTORY_PREFIXES)}, ни в группе {CERTIFICATE_GROUP!r} "
        f"словаря check_templates. Это заявка на исполнителя, а не "
        f"молчание: либо шаблон исполняется кодом CDPM, либо его группа "
        f"называет другого исполнителя")


#: Прежнее имя разрешителя (совместимость вызовов внутри продукта).
_executor = executor_of


def _records(catalog) -> list:
    """Записи каталога со статусом «есть», у которых объявлен вид
    `check_template`: сперва `families` по id, затем `criteria` по id
    (у критериев виды живут в `computable_by`)."""
    out = []
    for rec in catalog.families_full():
        if KIND in (rec.get("executor_kinds") or []):
            out.append(("families", rec))
    for rec in catalog.criteria().values():
        if KIND in (rec.get("computable_by") or []):
            out.append(("criteria", rec))
    return out


#: Поля строки `stand_verified`, которые меняются от прогона к прогону
#: законно и в сверке `--check` не участвуют (правило COSMOS §1.4).
STAND_VOLATILE = ("at", "commit")


def stand_rows(record: dict, address: str,
               stand_results: dict | None) -> list[dict]:
    """Строки `stand_verified` записи по файлу результатов стенда:
    только по задачам, СТОЯЩИМ в этой записи каталога, и только по виду
    `check_template`. Результаты по задачам, которых у записи ещё нет
    (задачи живут в посеве стенда до переноса), в файл не входят — иначе
    читатель скажет B05 «стендовой задачи у записи нет»."""
    if not isinstance(stand_results, dict):
        return []
    known = {str(t.get("id")) for t in (record.get("stand_tasks") or [])
             if isinstance(t, dict)}
    at = stand_results.get("at")
    commit = stand_results.get("commit")
    rows = []
    for r in stand_results.get("results") or []:
        if not isinstance(r, dict) or str(r.get("record")) != address:
            continue
        if r.get("executor_kind") != KIND or str(r.get("task")) not in known:
            continue
        rows.append({"task": str(r.get("task")), "executor_kind": KIND,
                     "result": r.get("result"), "at": at, "commit": commit,
                     "note": r.get("note")})
    rows.sort(key=lambda x: x["task"])
    return rows


def stand_summary(catalog, stand_results: dict | None) -> dict:
    """Сводка стенда для печати: исходы результатов и сколько из них
    осталось вне привязок (задачи посева до переноса в каталог)."""
    out = {"passed": 0, "failed": 0, "skipped": 0, "outside": 0}
    if not isinstance(stand_results, dict):
        return out
    known: dict[str, set] = {}
    for section, rec in _records(catalog):
        known[f"{section}/{rec.get('id')}"] = {
            str(t.get("id")) for t in (rec.get("stand_tasks") or [])
            if isinstance(t, dict)}
    for r in stand_results.get("results") or []:
        if not isinstance(r, dict):
            continue
        if r.get("result") in out:
            out[r["result"]] += 1
        if str(r.get("task")) not in known.get(str(r.get("record")), set()):
            out["outside"] += 1
    return out


def build(catalog, *, commit: str, generated_at: str,
          stand_results: dict | None = None) -> dict:
    """Документ привязок CDPM по каталогу — чистая сборка, ничего не
    пишет. Символы исполнителей «есть» проверяются здесь же: файл с
    обещанием несуществующего кода не выпускается. `stand_results` —
    документ результатов стенда формы `tables`
    (`cdpm/stand_tables.py`); устаревшие результаты — отказ словами, без
    файла результатов строки `stand_verified` пусты."""
    if not _COMMIT_RE.match(commit or ""):
        raise BindingsBuildError(
            f"коммит {commit!r} не похож на хеш git (7–40 знаков 0-9a-f)")
    if not _DATETIME_RE.match(generated_at or ""):
        raise BindingsBuildError(
            f"момент {generated_at!r} не ISO 8601 с поясом "
            f"(2026-09-07T21:00:00+03:00)")
    try:
        dicts = catalog.dictionaries() or {}
    except Exception as e:                                    # noqa: BLE001
        raise BindingsBuildError(
            f"каталог не отдал словари: {e}") from None
    templates = dicts.get("check_templates")
    if not isinstance(templates, dict) or not templates:
        raise BindingsBuildError(
            "у каталога нет словаря check_templates — привязывать нечего; "
            "проверьте catalog/dictionaries.json")
    groups = {str(n): (spec.get("group") if isinstance(spec, dict) else None)
              for n, spec in templates.items()}
    if stand_results is not None:
        from cdpm import stand_tables as stand
        why = stand.stale_reason(stand_results, catalog)
        if why:
            raise BindingsBuildError(
                f"результаты стенда устарели: {why} — прогоните "
                f"python -m cdpm.stand_tables --out cdpm/stand_results.json")
    rows = []
    for section, rec in _records(catalog):
        rid = str(rec.get("id"))
        names = [str(n) for n in (rec.get("check_templates") or [])]
        if not names:
            raise BindingsBuildError(
                f"запись {section}/{rid} объявила вид {KIND}, а имён "
                f"шаблонов не назвала: исполнителя не к чему привязать")
        executors = []
        for name in names:
            try:
                executors.append(executor_of(name, groups))
            except BindingsBuildError as e:
                raise BindingsBuildError(f"{section}/{rid}: {e}") from None
        rows.append({"section": section, "id": rid, "executors": executors,
                     "stand_verified": stand_rows(rec, f"{section}/{rid}",
                                                  stand_results)})
    for row in rows:
        for ex in row["executors"]:
            if ex["state"] == "есть":
                resolve_symbol(ex["file"], ex["symbol"])
    doc = {"product": PRODUCT,
           # Producer identity, not the SPEC selected for an individual ZIP.
           "product_version": str(task_package.SPEC_VERSION),
           "catalog_version": str(catalog.version()),
           "catalog_fingerprint": str(catalog.fingerprint()),
           "generated_at": generated_at,
           "generated_from": commit,
           "bindings": rows}
    if _schema_allows("executor_kinds"):
        doc["executor_kinds"] = list(EXECUTOR_KINDS)
    return doc


def dump(doc: dict) -> str:
    """Текст файла привязок: форма файлов каталога (LF, отступ 1,
    ключи по алфавиту, ensure_ascii=False)."""
    return dump_json(doc)


# ------------------------------------------------------------- сверка
def _executor_label(ex: dict, j: int) -> str:
    name = ex.get("template") if isinstance(ex, dict) else None
    return f"исполнитель {j} ({name})" if name else f"исполнитель {j}"


def _diff_executors(diff: list, addr: str, built: list, doc: list) -> None:
    if len(built) != len(doc):
        diff.append(f"{addr}: исполнителей в файле {len(doc)}, "
                    f"собрано {len(built)}")
    for j in range(min(len(built), len(doc))):
        a, b = built[j], doc[j]
        if not isinstance(b, dict):
            diff.append(f"{addr} {_executor_label(a, j)}: в файле не объект")
            continue
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                diff.append(f"{addr} {_executor_label(a, j)}: поле {key} — "
                            f"в файле {b.get(key)!r}, собрано {a.get(key)!r}")


def _stable(rows) -> list:
    """Строки `stand_verified` без полей прогона: перезапуск стенда без
    изменений не делает файл привязок «отставшим»."""
    return [{k: v for k, v in r.items() if k not in STAND_VOLATILE}
            if isinstance(r, dict) else r for r in (rows or [])]


def differences(built: dict, doc: dict) -> list[str]:
    """Отличия файла от собранного, словами; поля `generated_at` и
    `generated_from` шапки, а также `at`/`commit` строк стенда не
    сравниваются."""
    diff: list[str] = []
    for key in HEADER:
        if built.get(key) != doc.get(key):
            diff.append(f"шапка: поле {key} — в файле {doc.get(key)!r}, "
                        f"собрано {built.get(key)!r}")
    extra = sorted(set(doc) - set(built) - set(VOLATILE))
    if extra:
        diff.append(f"шапка: лишние поля файла {extra}")
    missing = sorted(set(built) - set(doc) - set(VOLATILE))
    if missing:
        diff.append(f"шапка: нет полей {missing}")
    rows_doc = {}
    for i, row in enumerate(doc.get("bindings") or []):
        if not isinstance(row, dict):
            diff.append(f"строка привязки {i} в файле не объект")
            continue
        rows_doc[(str(row.get("section")), str(row.get("id")))] = row
    seen = set()
    for row in built.get("bindings") or []:
        addr = f"{row['section']}/{row['id']}"
        key = (row["section"], row["id"])
        seen.add(key)
        other = rows_doc.get(key)
        if other is None:
            diff.append(f"{addr}: записи в файле нет")
            continue
        _diff_executors(diff, addr, row["executors"],
                        list(other.get("executors") or []))
        if _stable(other.get("stand_verified")) != \
                _stable(row["stand_verified"]):
            diff.append(f"{addr}: стендовые подтверждения — в файле "
                        f"{other.get('stand_verified')!r}, собрано "
                        f"{row['stand_verified']!r}")
    for key in rows_doc:
        if key not in seen:
            diff.append(f"{key[0]}/{key[1]}: записи в файле лишняя "
                        f"(каталог её к CDPM не привязывает)")
    return diff


# ------------------------------------------------------------ запуск
def head_commit() -> str:
    """Полный хеш HEAD репозитория. Без git — отказ словами: выдумывать
    происхождение файла нельзя, для сборки вне git есть `--commit`."""
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                           capture_output=True, text=True, timeout=30)
    except Exception as e:                                    # noqa: BLE001
        raise BindingsBuildError(
            f"git не запускается ({e}) — задайте коммит ключом "
            f"--commit") from None
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not _COMMIT_RE.match(out):
        why = (r.stderr or "").strip() or out or "пустой ответ"
        raise BindingsBuildError(
            f"git rev-parse HEAD в {REPO} не дал хеша ({why[:160]}) — "
            f"задайте коммит ключом --commit")
    return out


def _catalog():
    """Каталог моста CDPM (один экземпляр на процесс) с его отметкой;
    без каталога — отказ словами: файла привязок без отметки не бывает.
    Шаг 7: мост поднимает `CatalogRequired`, и рубеж команды переводит
    его в свой отказ — код возврата 2, а не трассировка."""
    try:
        bridge.stamp()
        return bridge.catalog()
    except bridge.CATALOG_ERRORS as e:
        raise BindingsBuildError(
            f"каталог шаблонов недоступен: {bridge.words_of(e)} — привязки "
            f"без версии и отпечатка каталога не выпускаются") from None


def _read(path: Path) -> dict:
    """Файл привязок читателем каталога; нет файла или он не читается —
    отказ словами."""
    if not path.is_file():
        raise BindingsBuildError(f"файла {path} нет")
    try:
        from template_catalog.bindings import load_bindings
        return load_bindings(path)
    except BindingsBuildError:
        raise
    except Exception as e:                                    # noqa: BLE001
        raise BindingsBuildError(f"{path} не читается ({e})") from None


def read_stand_results(path) -> dict:
    """Файл результатов стенда формы `tables`; нет файла или он не
    читается — отказ словами (молчаливой сборки без стенда по заданному
    ключу быть не должно)."""
    from cdpm import stand_tables as stand
    try:
        return stand.read_results(path)
    except stand.StandError as e:
        raise BindingsBuildError(str(e)) from None


def _volatile(doc: dict, key: str, shape) -> str | None:
    """Значение поля, которое в сверке не участвует, — у проверяемого
    файла: сверке они безразличны, а git при `--check` тогда не нужен."""
    v = doc.get(key)
    return v if isinstance(v, str) and shape.match(v) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", help="куда записать файл привязок")
    mode.add_argument("--check", help="сверить файл с кодом и каталогом, "
                                      "ничего не писать")
    ap.add_argument("--commit", help="хеш происхождения вместо git HEAD")
    ap.add_argument("--generated-at", help="момент генерации (ISO 8601 "
                                           "с поясом) вместо текущего")
    ap.add_argument("--stand-results", help="файл результатов стенда "
                                            "шаблонов проверок данных")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    other = None
    stand_doc = None
    try:
        catalog = _catalog()
        if a.stand_results:
            stand_doc = read_stand_results(a.stand_results)
        if a.check:
            other = _read(Path(a.check))
            commit = a.commit or _volatile(other, "generated_from",
                                           _COMMIT_RE) or head_commit()
            at = a.generated_at or _volatile(other, "generated_at",
                                             _DATETIME_RE) or now
        else:
            commit = a.commit or head_commit()
            at = a.generated_at or now
        doc = build(catalog, commit=commit, generated_at=at,
                    stand_results=stand_doc)
    except BindingsBuildError as e:
        print(f"привязки CDPM {'не сверены' if a.check else 'не собраны'}: "
              f"{e}")
        return 2
    rows = len(doc["bindings"])
    present = sum(1 for r in doc["bindings"] for x in r["executors"]
                  if x["state"] == "есть")
    total = sum(len(r["executors"]) for r in doc["bindings"])
    stamp = (f"каталог {doc['catalog_version']} | отпечаток "
             f"{doc['catalog_fingerprint']} | записей {rows} | "
             f"исполнителей {total} (из них «есть» {present})")
    if stand_doc is not None:
        s = stand_summary(catalog, stand_doc)
        print(f"стенд: passed {s['passed']}, failed {s['failed']}, skipped "
              f"{s['skipped']}, вне каталога {s['outside']}"
              + (" (задачи посева до переноса в каталог) — в привязки не "
                 "слиты" if s["outside"] else ""))
    if a.check:
        diff = differences(doc, other)
        print(f"сверка {a.check}: {stamp} | расхождений {len(diff)}")
        for d in diff:
            print("  " + d)
        return 1 if diff else 0
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Байтами, не текстом: write_text на Windows перевёл бы «\n» в
    # «\r\n», и форма файла зависела бы от ОС.
    path.write_bytes(dump(doc).encode("utf-8"))
    print(f"записаны привязки {path}: {stamp} | момент "
          f"{doc['generated_at']} | коммит {doc['generated_from']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
