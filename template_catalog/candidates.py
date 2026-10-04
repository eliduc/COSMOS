# -*- coding: utf-8 -*-
"""Файл-кандидат записи каталога: форма, каталог в памяти, временный
каталог гейтов (шаг 6, конвейер пополнения).

Кандидат — это запись, которую конвейер самоулучшения предлагает по
принятой владельцем заявке (`catalog/candidates/<раздел>/<id>.json`).
Он лежит ВНЕ отпечатка и вне отметки читателя: каталог ведёт служба, а
кандидат — предложение, которое владелец либо принимает (`adopt` →
«Собрать черновик»), либо выбрасывает.

Здесь — три работы и ни одной лишней:

1. `load_candidate` — форма файла (схема `schemas/candidate.schema.json`
   плюс структурный минимум кодом, если `jsonschema` не установлен).
2. `validate_with_candidate` — СМЫСЛ записи: каталог в памяти = живой
   каталог ⊕ запись кандидата, судимый обычным `validate()` читателя.
   Одно вычисление на всех: его зовут и гейты конвейера, и служба при
   `adopt`.
3. `materialize` — папка временного каталога для гейтов ветки CR:
   стенд, генератор привязок и гейт привязок обязаны видеть запись как
   настоящую («есть»), иначе они красны по построению, а красный гейт,
   неизбежный по построению, ничего не судит.

Модуль — часть читателя: только стандартная библиотека (и, необязательно,
`jsonschema` через `validate.py`); импортов из cdpm, vmp, webapp,
catalog_service — ни одного; при импорте ни одного файла.

    python -m template_catalog.candidates <файл> [--catalog <папка>]
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

# `argparse` и `shutil` — ЛЕНИВО, внутри функций: при импорте пакета
# читатель не трогает ни файла, ни переменной среды, а эти модули тянут
# за собой `gettext` и `fnmatch` (гейт
# tests/test_template_catalog_reader.py::test_package_import_reads_no_
# files_and_no_env).

from . import canon
from .errors import CatalogError, CatalogFormatError
from .export import dump_json
from .reader import SEMVER_RE, Catalog, load, read_json_file

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "candidate.schema.json"
FORM_VERSION = 1
SECTIONS = ("families", "criteria")
#: Кто пишет временную строку истории у записи временного каталога.
GATE_HISTORY_BY = "контур самоулучшения"
GATE_HISTORY_NOTE = "временная запись гейта CR"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_REQ_ID_RE = re.compile(r"^req-\d{20}$")
_CR_KEY_RE = re.compile(r"^cr[0-9a-f]{8}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CandidateError(CatalogError):
    """Файл-кандидат не по форме — словами, с адресом поля."""


# ------------------------------------------------------------ форма
def _minimum(doc, label: str) -> None:
    """Структурный минимум кодом: он работает и без `jsonschema`, и
    отвечает адресом поля, а не путём схемы."""
    if not isinstance(doc, dict):
        raise CandidateError(f"{label}: корень файла не объект JSON")
    allowed = {"form_version", "section", "id", "record", "note", "by",
               "saved_at", "request_id", "cr_key", "evidence"}
    extra = sorted(set(doc) - allowed)
    if extra:
        raise CandidateError(f"{label}: лишние поля {extra} — форма файла "
                             f"закрыта")
    missing = sorted(allowed - {"evidence"} - set(doc))
    if missing:
        raise CandidateError(f"{label}: нет полей {missing}")
    if doc.get("form_version") != FORM_VERSION \
            or isinstance(doc.get("form_version"), bool):
        raise CandidateError(f"{label}/form_version: ожидалась форма "
                             f"{FORM_VERSION}, дано "
                             f"{doc.get('form_version')!r}")
    if doc.get("section") not in SECTIONS:
        raise CandidateError(f"{label}/section: раздел "
                             f"{doc.get('section')!r} не families и не "
                             f"criteria")
    if not isinstance(doc.get("id"), str) or not _ID_RE.match(doc["id"]):
        raise CandidateError(f"{label}/id: {doc.get('id')!r} не по образцу "
                             f"идентификатора записи")
    for key, pattern, what in (("request_id", _REQ_ID_RE, "req-<20 цифр>"),
                               ("cr_key", _CR_KEY_RE, "cr<8 hex>"),
                               ("saved_at", _DATETIME_RE, "ISO с зоной")):
        v = doc.get(key)
        if not isinstance(v, str) or not pattern.match(v):
            raise CandidateError(f"{label}/{key}: {v!r} не по образцу "
                                 f"{what}")
    for key in ("note", "by"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise CandidateError(f"{label}/{key}: непустая строка обязательна")
    rec = doc.get("record")
    if not isinstance(rec, dict):
        raise CandidateError(f"{label}/record: запись не объект JSON")
    if "history" in rec:
        raise CandidateError(
            f"{label}/record.history: историю пишет сборка службой, а не "
            f"конвейер — поле у кандидата запрещено")
    if rec.get("id") != doc["id"]:
        raise CandidateError(f"{label}/record.id: {rec.get('id')!r} ≠ "
                             f"id кандидата {doc['id']!r}")
    if not isinstance(rec.get("status"), str) or not rec["status"].strip():
        raise CandidateError(f"{label}/record.status: состояние записи "
                             f"обязательно")
    if not isinstance(rec.get("stand_tasks"), list):
        raise CandidateError(f"{label}/record.stand_tasks: перечень задач "
                             f"стенда обязателен (пустой — тоже перечень)")
    ev = doc.get("evidence")
    if ev is not None and not isinstance(ev, dict):
        raise CandidateError(f"{label}/evidence: свидетельство не объект JSON")


def _schema_problems(doc, label: str) -> list[str]:
    """Проверка схемой через единственную точку импорта `jsonschema` в
    читателе (`validate.schema_messages`); схема не читается либо пакета
    нет — пустой перечень (структурный минимум уже отработал)."""
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    from .validate import schema_messages
    return schema_messages(doc, schema, label)


def load_candidate(path) -> dict:
    """Файл-кандидат по форме; иначе `CandidateError` словами."""
    p = Path(path)
    label = p.name
    doc = read_json_file(p)
    _minimum(doc, label)
    problems = _schema_problems(doc, label)
    if problems:
        raise CandidateError("; ".join(problems[:5])
                             + (f" (и ещё {len(problems) - 5})"
                                if len(problems) > 5 else ""))
    return doc


def record_of(candidate: dict) -> dict:
    """Запись кандидата копией (без истории — её пишет сборка)."""
    rec = copy.deepcopy(_dict(candidate).get("record") or {})
    rec.pop("history", None)
    return rec


def record_in_tree(catalog_dir, section: str, rid: str) -> tuple[dict, str]:
    """Запись по адресу так, как её несёт ДЕРЕВО: из каталога, если он её
    уже назвал, иначе из файла кандидата `candidates/<раздел>/<id>.json`.
    Возвращает (запись, источник: «каталог» | «кандидат»).

    Тестам исполнителей (в том числе написанным агентом конвейера) нужна
    запись и ДО сборки (кандидат рядом с каталогом), и ПОСЛЕ (сборка
    службой удаляет файл кандидата, запись живёт в каталоге). Чтение по
    пути файла кандидата краснело первым же выпуском (0.4.0, 09.09.2026:
    тест семьи duty_series_limit искал исчезнувший файл)."""
    from . import load as _load
    from .errors import UnknownRecord
    root = Path(catalog_dir)
    cat = _load(root)
    try:
        rec = (cat.family(rid) if section == "families"
               else cat.criterion(rid))
        if rec is not None:
            return copy.deepcopy(rec), "каталог"
    except UnknownRecord:
        pass
    path = root / "candidates" / section / f"{rid}.json"
    if not path.is_file():
        raise CandidateError(
            f"записи {section}/{rid} нет ни в каталоге {cat.version()}, ни "
            f"кандидатом {path}")
    return record_of(load_candidate(path)), "кандидат"


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


# -------------------------------------------------- каталог в памяти
def _docs_with(cat, candidate: dict) -> tuple[dict, dict, str]:
    """(документы разделов ⊕ запись, шапка с новым отпечатком, «новая» |
    «изменена»). Запись кладётся со статусом кандидата и временной
    строкой истории: без истории её не пропустит X08 читателя.

    После слияния записи применяются ПРОИЗВОДНЫЕ ИНВАРИАНТЫ СБОРКИ
    (`writer.refresh_body`: симметрия дополнений X01, уход связанного
    имени из `unbound_params` D03, порядок лексикона D02) и
    пересчитывается старая ревизия шапки — решение 0.8 пакета паритета:
    временный каталог кандидата обязан быть равен тому, что позже
    соберёт из этого кандидата служба. Иначе кандидат, связавший имя из
    словаря `unbound_params`, красен D03 на временном каталоге и зелен у
    службы — два закона на один переход."""
    from . import writer          # ленивый импорт: см. шапку модуля
    section = str(candidate.get("section"))
    if section not in SECTIONS:
        raise CandidateError(f"раздел {section!r} не families и не criteria")
    rid = str(candidate.get("id"))
    rec = record_of(candidate)
    rec["history"] = [{
        "date": _date_of(candidate),
        "kind": "создание",
        "by": GATE_HISTORY_BY,
        "note": GATE_HISTORY_NOTE,
        "ref": str(candidate.get("request_id") or "")}]
    docs = {name: copy.deepcopy(cat._docs[name])
            for name in ("families", "criteria", "dictionaries", "requests")}
    # Прежние дополнения — СНИМКОМ (перечнями, не ссылками на записи):
    # инварианты правят записи на месте, и ссылка показала бы писателю
    # уже исправленное вместо прежнего.
    before = {str(r.get("id")): list(r.get("complements") or [])
              for r in docs["families"]["families"] if isinstance(r, dict)}
    key = "families" if section == "families" else "criteria"
    records = docs[key][key]
    kind = "изменена"
    for i, r in enumerate(records):
        if isinstance(r, dict) and r.get("id") == rid:
            history = [h for h in (r.get("history") or [])
                       if isinstance(h, dict)]
            rec["history"] = copy.deepcopy(history) + rec["history"]
            records[i] = rec
            break
    else:
        kind = "новая"
        records.append(rec)
    records.sort(key=lambda r: str(_dict(r).get("id")))
    header = copy.deepcopy(cat.header)
    dictionaries = {k: v for k, v in docs["dictionaries"].items()
                    if k != "form_version"}
    writer.refresh_body(docs["families"]["families"], dictionaries,
                        before=before)
    body = canon.normalize({"families": docs["families"]["families"],
                            "criteria": docs["criteria"]["criteria"],
                            "dictionaries": dictionaries})
    header["fingerprint"] = canon.fingerprint_of(body)
    docs["families"]["families"] = body["families"]
    docs["criteria"]["criteria"] = body["criteria"]
    docs["dictionaries"] = {"form_version": 1, **body["dictionaries"]}
    # Старая ревизия шапки — той же формулой, что у службы (`assemble`,
    # `release`): гейт паритета L10 сверяет её с формулой над семьями
    # каталога, и старая ревизия ЖИВОГО каталога краснила бы правило по
    # построению, а не по делу.
    probe = Catalog(path=cat.path, header=header, docs=docs,
                    versions_doc=None, single_file=True)
    header["legacy_registry_rev"] = writer.legacy_registry_rev(
        probe.families())
    return docs, header, kind


def _date_of(candidate: dict) -> str:
    saved = str(candidate.get("saved_at") or "")
    head = saved.split("T", 1)[0]
    return head if _DATE_RE.match(head) else "1970-01-01"


def catalog_with_candidate(cat, candidate: dict):
    """Каталог В ПАМЯТИ: живой каталог ⊕ запись кандидата. Отпечаток
    пересчитан, файлы на диске не тронуты (`single_file=True` — раздел
    на диске у этого экземпляра не ищется)."""
    docs, header, _kind = _docs_with(cat, candidate)
    # Версия — та же, что у временного каталога гейтов (`materialize`):
    # на ВЫПУЩЕННОМ каталоге любая запись меняет тело и отпечаток шапки,
    # и с номером выпуска в шапке читатель отвечал бы H03 «отпечаток
    # записи журнала не равен шапке» на любом кандидате — по построению,
    # а не по делу (сквозная проверка 09.09.2026 на 0.2.0). Служба при
    # приёме записи тоже собирает следующий черновик, а не правит выпуск.
    header["version"] = gate_version(cat.version())
    return Catalog(path=cat.path, header=header, docs=docs,
                   versions_doc=copy.deepcopy(cat._docs.get("versions")),
                   single_file=True)


def validate_with_candidate(cat, candidate: dict, forbidden_terms=()) -> list:
    """Проблемы `validate()` каталога с записью кандидата. ОДНО
    вычисление: его зовут гейты конвейера и служба при `adopt`."""
    mem = catalog_with_candidate(cat, candidate)
    return mem.validate(forbidden_terms=forbidden_terms)


# ------------------------------------------- временный каталог гейтов
def gate_version(current: str) -> str:
    """Версия временного каталога гейтов. Черновик остаётся собой
    (черновики в журнал версий не пишутся — столкнуться не с чем);
    выпущенная версия получает следующий ПАТЧ с суффиксом черновика:
    иначе временный каталог с иным отпечатком спорил бы с записью
    журнала о своей версии (H03).

    Отступление от буквы проекта («<v>-draft.gate»): такой суффикс не
    semver, и H01 читателя отказал бы — временный каталог обязан быть
    годным, иначе гейт судит не запись, а собственный ярлык."""
    m = SEMVER_RE.match(str(current or ""))
    if not m:
        raise CandidateError(f"версия каталога {current!r} не semver — "
                             f"временный каталог не строится")
    if m.group(4):
        return str(current)
    from . import diff
    return diff.next_version(str(current), "patch") + "-draft.1"


def materialize(catalog_dir, candidate: dict, out_dir) -> Path:
    """Папка временного каталога: живой каталог ⊕ запись кандидата.

    Пишутся шесть файлов разделов формой `export.dump_json`; папки
    `transition/` и `schema/` копируются, а порядок семей приводится к
    записям «есть» ФОРМУЛОЙ ПИСАТЕЛЯ (`refresh_families_order`, та же,
    что у сборки службы) — без него дверь COSMOS отказывает словами
    «порядок семей не совпадает с записями «есть»», и гейт красен по
    построению, а не по существу."""
    from . import writer          # ленивый импорт: см. шапку модуля
    src = Path(catalog_dir)
    out = Path(out_dir)
    cat = load(src)
    docs, header, kind = _docs_with(cat, candidate)
    header["version"] = gate_version(cat.version())
    header["note"] = (
        f"временный каталог гейта CR {candidate.get('cr_key')} по заявке "
        f"{candidate.get('request_id')}: запись "
        f"{candidate.get('section')}/{candidate.get('id')} из кандидата "
        f"({kind}); выпуску не подлежит")
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "catalog.json": header,
        "families.json": {"form_version": 1,
                          "families": docs["families"]["families"]},
        "criteria.json": {"form_version": 1,
                          "criteria": docs["criteria"]["criteria"]},
        "dictionaries.json": docs["dictionaries"],
        "requests.json": {"form_version": 1,
                          "requests": docs["requests"]["requests"]},
        "versions.json": {"form_version": 1, "versions": cat.versions()},
    }
    for name, body in files.items():
        (out / name).write_bytes(dump_json(body).encode("utf-8"))
    import shutil
    for folder in ("transition", "schema"):
        s = src / folder
        if s.is_dir():
            shutil.copytree(s, out / folder, dirs_exist_ok=True)
    order_path = out / "transition" / "families_order.json"
    if order_path.is_file():
        try:
            order = read_json_file(order_path)
        except CatalogError:
            order = None
        if isinstance(order, list):
            present = [str(r.get("id"))
                       for r in docs["families"]["families"]
                       if isinstance(r, dict) and r.get("status") == "есть"]
            fresh = writer.refresh_families_order(order, present)
            if fresh != list(order):
                order_path.write_bytes(dump_json(fresh).encode("utf-8"))
    return out


# ------------------------------------------------------------ команда
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Проверка файла-кандидата записи каталога шаблонов")
    ap.add_argument("candidate", help="путь к файлу catalog/candidates/…")
    ap.add_argument("--catalog", default=None,
                    help="папка каталога (умолчание — каталог читателя)")
    ap.add_argument("--markers", default=None,
                    help="файл перечня маркеров заказчика (по строке)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        candidate = load_candidate(a.candidate)
    except CatalogError as e:
        print(f"кандидат не по форме: {e}")
        return 1
    terms: tuple = ()
    if a.markers:
        try:
            terms = tuple(x.strip() for x in
                          Path(a.markers).read_text(encoding="utf-8-sig")
                          .splitlines() if x.strip())
        except OSError as e:
            print(f"перечень маркеров не читается: {e}")
            return 1
    try:
        cat = load(a.catalog)
    except CatalogError as e:
        print(f"каталог недоступен: {e}")
        return 1
    try:
        problems = validate_with_candidate(cat, candidate,
                                           forbidden_terms=terms)
    except (CatalogError, ValueError) as e:
        print(f"проверка не выполнена: {e}")
        return 1
    errors = [p for p in problems if p.severity == "error"]
    warnings = [p for p in problems if p.severity == "warning"]
    known = {str(r.get("request_id")) for r in cat.requests()
             if isinstance(r, dict)}
    note = ""
    if str(candidate.get("request_id")) not in known:
        note = (f" | предупреждение: заявки "
                f"{candidate.get('request_id')} в каталоге нет")
    print(f"кандидат {candidate.get('section')}/{candidate.get('id')} на "
          f"каталоге {cat.version()} ({cat.path}): ошибок {len(errors)} | "
          f"предупреждений {len(warnings)}{note}")
    # Код правила печатается рядом со словами: по нему человек находит
    # правило в приложении схемы, а `Problem.__str__` его не несёт.
    for p in errors[:20]:
        print(f"  {p.code} {p}")
    return 1 if errors else 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
