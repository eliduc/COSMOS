# -*- coding: utf-8 -*-
"""Привязки продукта к записям каталога (§7 схемы) — структурная часть
гейта: B01 версия/отпечаток и шапка файла, B02 существование записи,
B03 вид и поля по состоянию, B04 полнота по executor_kinds /
computable_by, B05 стендовые подтверждения; закрытость и типы полей —
схемой $defs/bindings_file (без jsonschema — предупреждение S06).
Существование символов кода — гейт продукта.

B04 судится по ЗОНЕ ОТВЕТСТВЕННОСТИ продукта (шаг 4, договорённость
сессий COSMOS и CDPM 07.09): необязательное поле шапки `executor_kinds`
называет виды, за которые продукт отвечает; без поля зона выводится из
самих строк файла по ВСЕМ состояниям (продукт с одними «нет» иначе
оказался бы вне зоны). Продукт вне зоны вида не должен по нему ничего.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .errors import CatalogError, CatalogFormatError
from .manifest import compare_versions, is_stop
from .reader import read_json_file
from .validate import (Problem, _import_jsonschema, _load_schema,
                       _schema_where, schema_messages)

_KIND_FIELDS = {
    "kotlin_constraint": ("constraint",),
    "kotlin_knob": ("param", "threshold", "unit", "relation"),
    "kotlin_form": ("form", "params"),
    "kotlin_logic_form": ("form", "constraint_id_rule", "params", "param_set"),
    "kotlin_metric": ("metric", "constraint", "tasks"),
    "check_template": ("template",),
}
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
#: Ранг состояния клетки: лучшее состояние продукта побеждает в таблице
#: B04 (у одного вида записи законно несколько строк — «есть» ручки и
#: «нет» ручки-синонима). Имя публичное: тем же рангом пользуется служба
#: каталога (подробности клетки экрана «Исполнители») — закон «есть >
#: заявлен > нет» живёт в одном месте, копий не бывает.
STATE_RANK = {"есть": 2, "заявлен": 1, "нет": 0}


def zone_of(doc: dict) -> set:
    """Зона ответственности продукта: поле шапки `executor_kinds`, а без
    поля — виды строк файла по ВСЕМ состояниям (включая «нет»)."""
    declared = doc.get("executor_kinds") if isinstance(doc, dict) else None
    if isinstance(declared, list) and declared:
        return {str(k) for k in declared}
    out = set()
    for row in (doc.get("bindings") or []) if isinstance(doc, dict) else []:
        if not isinstance(row, dict):
            continue
        for ex in row.get("executors") or []:
            if isinstance(ex, dict) and ex.get("kind"):
                out.add(str(ex["kind"]))
    return out


def load_bindings(path) -> dict:
    doc = read_json_file(Path(path))
    if not isinstance(doc, dict) or not isinstance(doc.get("bindings"), list):
        raise CatalogFormatError(f"{path}: файл привязок без списка bindings")
    return doc


def _record(catalog, section: str, rid: str) -> dict | None:
    if section == "families":
        return next((r for r in catalog.families_full(status=None)
                     if r.get("id") == rid), None)
    if section == "criteria":
        return catalog.criteria(status=None).get(rid)
    return None


def _kinds_of(record: dict, section: str) -> list:
    key = "executor_kinds" if section == "families" else "computable_by"
    return list(record.get(key) or [])


def _where_bindings(where: str, doc: dict, path) -> str:
    """Адрес ошибки схемы привязок в форме адресов check_bindings:
    bindings[<продукт>].bindings/<id>.executors[j].<поле>."""
    parts = list(path)
    if parts and parts[0] == "bindings":
        rows = doc.get("bindings") if isinstance(doc.get("bindings"), list) else []
        if len(parts) >= 2 and isinstance(parts[1], int) and parts[1] < len(rows):
            row = rows[parts[1]]
            rid = row.get("id") if isinstance(row, dict) else None
            where += f".bindings/{rid if rid is not None else parts[1]}"
            parts = parts[2:]
        else:
            where += ".bindings"
            parts = parts[1:]
    for p in parts:
        where += f"[{p}]" if isinstance(p, int) else f".{p}"
    return where


def _schema_code(path) -> str:
    """Код правила по месту ошибки схемы: исполнители — B03, стендовые
    подтверждения — B05, строка привязки — B02, шапка файла — B01."""
    parts = list(path)
    if "executors" in parts:
        return "B03"
    if "stand_verified" in parts:
        return "B05"
    if parts and parts[0] == "bindings":
        return "B02"
    return "B01"


def _schema_bindings(problems: list, where: str, doc: dict) -> None:
    """Закрытая схема привязок (§7): $defs/bindings_file — лишние и
    опечатанные поля, обязательные поля шапки, образцы datetime/commit/
    semver, if/then по состоянию и виду исполнителя. Без jsonschema —
    одно предупреждение S06, структурная часть судится кодом."""
    schema = _load_schema()
    if schema is None or "bindings_file" not in schema.get("$defs", {}):
        problems.append(Problem(where, "S06", "схема привязок не читается — "
                                "судится структурный минимум", "warning"))
        return
    jsonschema = _import_jsonschema()
    if jsonschema is None:
        problems.append(Problem(where, "S06", "пакет jsonschema не установлен — "
                                "схема привязок не судится", "warning"))
        return
    sub = {"$schema": schema.get("$schema"), "$defs": schema["$defs"],
           "$ref": "#/$defs/bindings_file"}
    try:
        errors = sorted(jsonschema.Draft202012Validator(sub).iter_errors(doc),
                        key=lambda e: list(map(str, e.absolute_path)))
    except Exception as e:                                   # noqa: BLE001
        problems.append(Problem(where, "S06", f"проверка схемы привязок не "
                                f"выполнена: {e}", "warning"))
        return
    seen = {p.where for p in problems}
    for e in errors:
        w = _schema_where(_where_bindings(where, doc, e.absolute_path), e)
        if w in seen:
            continue                     # код уже назвал это место словами
        seen.add(w)
        problems.append(Problem(w, _schema_code(e.absolute_path),
                                f"не по схеме привязок: {e.message[:200]}",
                                "error"))


def check_bindings(catalog, *bindings: dict) -> list[Problem]:
    problems: list[Problem] = []
    err = lambda w, c, m: problems.append(Problem(w, c, m, "error"))       # noqa: E731
    warn = lambda w, c, m: problems.append(Problem(w, c, m, "warning"))    # noqa: E731
    dicts = catalog.dictionaries()
    kinds_dict = set(dicts.get("executor_kinds") or [])
    states = set(dicts.get("executor_states") or [])
    langs = set(dicts.get("languages") or [])
    results = set(dicts.get("stand_results") or [])
    tasks_dict = set(dicts.get("task_kinds") or [])
    check_templates = set(dicts.get("check_templates") or {})
    unbound = set(dicts.get("unbound_params") or {})
    # состояние «запись × вид × продукт» для B04 и зоны продуктов
    table: dict = {}
    notes: dict = {}
    zones: dict = {}
    for b in bindings:
        product = str(b.get("product") or "?")
        where = f"bindings[{product}]"
        zones[product] = zone_of(b)
        # Принадлежность словарю судится у ОБЪЯВЛЕННОЙ зоны: виды строк
        # файла и так судит B03, и второй доклад о том же был бы шумом.
        declared_zone = b.get("executor_kinds")
        if isinstance(declared_zone, list):
            for k in sorted({str(x) for x in declared_zone} - kinds_dict):
                err(f"{where}.executor_kinds", "B01",
                    f"вид {k!r} в шапке executor_kinds вне словаря")
        # B01
        bv, bf = b.get("catalog_version"), b.get("catalog_fingerprint")
        try:
            diff = compare_versions(catalog.version(), str(bv))
        except ValueError as e:
            err(f"{where}.catalog_version", "B01", str(e))
            diff = None
        if diff is not None:
            if is_stop(diff):
                err(f"{where}.catalog_version", "B01",
                    f"привязки к каталогу {bv}, читается {catalog.version()}: "
                    f"старшая разница версий")
            elif diff.level == "minor" and diff.zero_major:
                warn(f"{where}.catalog_version", "B01",
                     f"привязки к черновику каталога {bv} (отпечаток "
                     f"{str(bf)[:12]}…), читается черновик "
                     f"{catalog.version()} (отпечаток "
                     f"{catalog.fingerprint()[:12]}…): младшая разница версий "
                     f"на 0.x между двумя черновиками — продолжаем с "
                     f"предупреждением; выпуск любой из сторон сделал бы это "
                     f"остановкой")
            elif diff.level != "same":
                warn(f"{where}.catalog_version", "B01",
                     f"привязки к каталогу {bv}, читается {catalog.version()}: "
                     f"{diff.level}")
            elif bf != catalog.fingerprint():
                if diff.draft_a or diff.draft_b:
                    warn(f"{where}.catalog_fingerprint", "B01",
                         "каталог в черновике: отпечаток привязок отстал")
                else:
                    err(f"{where}.catalog_fingerprint", "B01",
                        "одна версия каталога — разные данные (отпечаток "
                        "привязок не равен отпечатку каталога)")
        for i, row in enumerate(b.get("bindings") or []):
            if not isinstance(row, dict):
                err(f"{where}.bindings[{i}]", "B02", "строка привязки не объект")
                continue
            section, rid = row.get("section"), row.get("id")
            rw = f"{where}.bindings/{rid}"
            record = _record(catalog, str(section), str(rid))
            if record is None:
                err(rw, "B02", f"записи {section}/{rid} в каталоге нет")
                continue
            declared = _kinds_of(record, str(section))
            for j, ex in enumerate(row.get("executors") or []):
                ew = f"{rw}.executors[{j}]"
                if not isinstance(ex, dict):
                    err(ew, "B03", "исполнитель не объект")
                    continue
                kind, state = ex.get("kind"), ex.get("state")
                if kind not in kinds_dict:
                    err(f"{ew}.kind", "B03", f"вид {kind!r} вне словаря")
                    continue
                if kind not in declared:
                    warn(f"{ew}.kind", "B03",
                         f"вид {kind!r} записью {rid} не объявлен")
                if state not in states:
                    err(f"{ew}.state", "B03", f"состояние {state!r} вне словаря")
                    continue
                cell = table.setdefault((section, rid), {}).setdefault(kind, {})
                # Лучшее состояние продукта: «есть» > «заявлен» > «нет».
                # У одного вида записи законно несколько строк одного
                # продукта (ручка «есть» и ручка-синоним «нет») —
                # последняя строка не должна решать за клетку.
                if STATE_RANK.get(str(state), 0) > \
                        STATE_RANK.get(str(cell.get(product)), -1):
                    cell[product] = state
                    notes.setdefault((section, rid), {}) \
                         .setdefault(kind, {})[product] = ex.get("note")
                if state == "есть":
                    for k in ("lang", "file", "symbol"):
                        if not (isinstance(ex.get(k), str) and ex.get(k)):
                            err(f"{ew}.{k}", "B03",
                                f"у исполнителя «есть» обязательно поле {k}")
                    if ex.get("lang") is not None and ex.get("lang") not in langs:
                        err(f"{ew}.lang", "B03", f"язык {ex.get('lang')!r} вне словаря")
                    if isinstance(ex.get("symbol"), str) and \
                            not _SYMBOL_RE.match(ex["symbol"]):
                        err(f"{ew}.symbol", "B03", "символ не точечный путь")
                    for k in _KIND_FIELDS.get(kind, ()):
                        if k not in ex:
                            err(f"{ew}.{k}", "B03",
                                f"у вида {kind} обязательно поле {k}")
                    if kind == "check_template":
                        t = ex.get("template")
                        if t not in check_templates:
                            err(f"{ew}.template", "B03",
                                f"шаблон {t!r} вне словаря check_templates")
                        elif t not in (record.get("check_templates") or []):
                            err(f"{ew}.template", "B03",
                                f"шаблон {t!r} записью {rid} не объявлен")
                    if kind == "kotlin_knob":
                        p = ex.get("param")
                        names = {x.get("name") for x in record.get("params") or []}
                        if p in unbound and p not in names:
                            warn(f"{ew}.param", "B03",
                                 f"ручка семьи без параметра в записи: {p!r}")
                        elif p not in names and p is not None:
                            err(f"{ew}.param", "B03",
                                f"параметр {p!r} не из записи и не из "
                                f"unbound_params")
                    if kind == "kotlin_metric":
                        bad = [t for t in (ex.get("tasks") or []) if t not in tasks_dict]
                        if bad:
                            err(f"{ew}.tasks", "B03", f"задачи {bad} вне словаря")
                else:
                    for k in ("lang", "file", "symbol"):
                        if ex.get(k) is not None:
                            err(f"{ew}.{k}", "B03",
                                f"у исполнителя «{state}» поле {k} обязано быть null")
                    if not (isinstance(ex.get("note"), str) and ex.get("note")):
                        err(f"{ew}.note", "B03",
                            f"у исполнителя «{state}» обязательна заметка")
                    if state == "заявлен" and not ex.get("cr_key"):
                        err(f"{ew}.cr_key", "B03",
                            "у исполнителя «заявлен» обязателен ключ CR")
            # B05
            task_ids = {t.get("id") for t in record.get("stand_tasks") or []}
            for j, sv in enumerate(row.get("stand_verified") or []):
                sw = f"{rw}.stand_verified[{j}]"
                sv = sv if isinstance(sv, dict) else {}
                if sv.get("task") not in task_ids:
                    err(f"{sw}.task", "B05",
                        f"стендовой задачи {sv.get('task')!r} у записи нет")
                if sv.get("executor_kind") not in declared:
                    err(f"{sw}.executor_kind", "B05",
                        f"вид {sv.get('executor_kind')!r} записью не объявлен")
                if sv.get("result") not in results:
                    err(f"{sw}.result", "B05",
                        f"исход {sv.get('result')!r} вне словаря stand_results")
        # закрытость и типы полей — схемой (после правил кода: их адреса
        # точнее, схема добавляет только не названные места)
        _schema_bindings(problems, where, b)
    # B04: полнота по видам записи — по зоне ответственности продукта
    products = [str(b.get("product") or "?") for b in bindings]
    if products:
        records = [("families", r) for r in catalog.families_full()] + \
                  [("criteria", r) for r in catalog.criteria().values()]
        for section, rec in records:
            rid = rec.get("id")
            for kind in _kinds_of(rec, section):
                cell = table.get((section, rid), {}).get(kind, {})
                if any(s == "есть" for s in cell.values()):
                    continue                       # исполнитель найден
                resp = [p for p in products if kind in zones.get(p, set())]
                if not resp:
                    zw = "; ".join(f"{p} — [{', '.join(sorted(zones.get(p, ())))}]"
                                   for p in products)
                    err(f"{section}/{rid}.executor_kinds[{kind}]", "B04",
                        f"вид {kind} у записи {rid} никем не исполняется и ни "
                        f"в чьей зоне (зоны: {zw})")
                    continue
                missing = [p for p in resp if p not in cell]
                if not missing:
                    continue                       # каждый ответственный сказал
                cnotes = notes.get((section, rid), {}).get(kind, {})
                said = ", ".join(
                    f"{p} — " + (
                        (f"{cell[p]}" + (f" ({cnotes[p]})"
                                         if cnotes.get(p) else ""))
                        if p in cell else
                        ("в зоне, но не объявил"
                         if kind in zones.get(p, set()) else "вне зоны"))
                    for p in products)
                err(f"{section}/{rid}.executor_kinds[{kind}]", "B04",
                    f"вид {kind} у записи {rid}: {said}")
    return problems


def executor_states(catalog, *bindings: dict) -> dict:
    """{section: {id: {kind: {product: состояние}}}} — таблица
    «запись × вид × продукт»; заявки «исполнитель заявлен» дают
    состояние «заявлен» под именем подавшего."""
    out: dict = {}
    for b in bindings:
        product = str(b.get("product") or "?")
        for row in b.get("bindings") or []:
            if not isinstance(row, dict):
                continue
            sec, rid = str(row.get("section")), str(row.get("id"))
            for ex in row.get("executors") or []:
                if isinstance(ex, dict) and ex.get("kind"):
                    out.setdefault(sec, {}).setdefault(rid, {}) \
                       .setdefault(str(ex["kind"]), {})[product] = ex.get("state")
    for rq in catalog.requests():
        if rq.get("state") != "исполнитель заявлен":
            continue
        for kind in rq.get("executor_kinds_requested") or []:
            out.setdefault(str(rq.get("section")), {}) \
               .setdefault(str(rq.get("record_id")), {}) \
               .setdefault(str(kind), {})[str(rq.get("submitted_by"))] = "заявлен"
    return out


def better_state(state, than) -> bool:
    """Лучше ли состояние `state` того, что уже выбрано (`than`).

    Одна таблица рангов и одно правило выбора на всех, кто выбирает
    «лучшее» из нескольких строк: клетка таблицы (`best_states`), строка
    ячейки свидетельства (`best_executor`), подробности экрана службы.
    Ничего ещё не выбрано (`than is None`) — годится любое."""
    if than is None:
        return True
    return STATE_RANK.get(str(state), -1) > STATE_RANK.get(str(than), -1)


def best_executor(row: dict | None, kind: str) -> dict | None:
    """Лучшая по рангу строка исполнителя ВИДА в записи одного продукта.

    У вида записи законно несколько строк одного продукта («есть» у
    ручки и «нет» у ручки-синонима), и строка, оказавшаяся в файле
    последней, за клетку не решает."""
    best = None
    for ex in (row or {}).get("executors") or ():
        if not isinstance(ex, dict) or str(ex.get("kind")) != str(kind):
            continue
        if best is None or better_state(ex.get("state"), best.get("state")):
            best = ex
    return best


def best_states(catalog, *bindings: dict) -> dict:
    """{раздел: {id: {вид: {продукт: состояние}}}} — ЛУЧШЕЕ состояние
    клетки по строкам файлов привязок: у одного вида записи законно
    несколько строк одного продукта (ручка «есть» и ручка-синоним
    «нет»), и строка, оказавшаяся в файле последней, не должна решать за
    клетку. Ранг — «есть» > «заявлен» > «нет» (STATE_RANK); состояние
    вне словаря не побеждает ни одного известного, но клетку занимает,
    если других строк нет. Заявки каталога в состоянии «исполнитель
    заявлен» дают «заявлен» под именем подавшего.

    Тот же закон живёт в двери COSMOS (`vmp/catalog_access._merge_states`)
    и на экране базы шаблонов (`app.js tplBestState`); служба каталога
    зовёт эту функцию, третьей копии закона не появляется.

    `executor_states()` этой правкой НЕ МЕНЯЕТСЯ (договорённость с
    сессией CDPM, проект шага 4 §2.1 п.3): он по-прежнему оставляет в
    клетке последнюю строку файла."""
    out: dict = {}
    for b in bindings:
        product = str(b.get("product") or "?")
        for row in (b.get("bindings") or []) if isinstance(b, dict) else []:
            if not isinstance(row, dict):
                continue
            cell = out.setdefault(str(row.get("section")), {}) \
                      .setdefault(str(row.get("id")), {})
            for ex in row.get("executors") or []:
                if not isinstance(ex, dict) or not ex.get("kind"):
                    continue
                by_product = cell.setdefault(str(ex["kind"]), {})
                if better_state(ex.get("state"), by_product.get(product)):
                    by_product[product] = ex.get("state")
    for rq in catalog.requests():
        if rq.get("state") != "исполнитель заявлен":
            continue
        for kind in rq.get("executor_kinds_requested") or []:
            out.setdefault(str(rq.get("section")), {}) \
               .setdefault(str(rq.get("record_id")), {}) \
               .setdefault(str(kind), {})[str(rq.get("submitted_by"))] = \
                "заявлен"
    return out


# ──────────────────────────────── свидетельство поддержки (K06)

#: Форма файла `catalog/support.json` и версия его схемы (П0 §0.1).
SUPPORT_FORM_VERSION = 1
SUPPORT_SCHEMA_VERSION = "support/1"
#: Версии схемы свидетельства, которые читатель умеет читать — ЗАКРЫТЫЙ
#: перечень (общий закон 5 П0: «неизвестная мажорная версия — отказ»).
#: Файл иной версии не показывается как годное свидетельство: читателей
#: у него трое (служба, дверь COSMOS, CDPM), и fail-open здесь унаследуют
#: все трое.
SUPPORT_SCHEMA_VERSIONS = ("support/1",)
#: Носитель схемы свидетельства; у службы каталога — байт-в-байт копия.
SUPPORT_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" \
    / "support.schema.json"
#: Состояния ячейки свидетельства — закрытый перечень трёх слов
#: (П0 §3.5; сторона COSMOS §4.2). «Есть» — подтверждённая поддержка;
#: «нет» — продукт сказал, что исполнителя не будет (или не сказал
#: ничего); «не проверено» — сказал «есть», но подтверждения на этом
#: отпечатке нет. Для готовности «не проверено» = не поддержано.
SUPPORT_STATES = ("есть", "нет", "не проверено")
#: Кто вычисляет таблицу: одна функция читателя, зовёт её служба.
SUPPORT_COMPUTED_BY = "служба"
#: Поля строки привязки в ячейке (сторона COSMOS, приложение Г).
_BINDING_FIELDS = ("state", "file", "symbol", "cr_key", "note")


def matches_catalog(catalog, doc) -> bool:
    """Объявлен ли файл привязок для ЭТОГО каталога: та же тройка версии
    и тот же отпечаток (исход «match» свежести службы, B01).

    Закон один: свежесть на экране «Исполнители по продуктам»
    (`catalog_service.executors.freshness`) и годность ячейки
    свидетельства судят ОДНОЙ функцией — иначе экран и свидетельство
    однажды разошлись бы в том, для какого каталога написан файл."""
    if not isinstance(doc, dict):
        return False
    try:
        d = compare_versions(catalog.version(),
                             str(doc.get("catalog_version")))
    except ValueError:
        return False
    return d.level == "same" \
        and doc.get("catalog_fingerprint") == catalog.fingerprint()


def _support_schema() -> dict:
    """Схема свидетельства с диска; файла нет либо он не читается — `{}`
    (структурный минимум ниже судится кодом и без схемы)."""
    try:
        doc = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):                           # pragma: no cover
        return {}
    return doc if isinstance(doc, dict) else {}


def support_form_problems(doc) -> list[str]:
    """Чем документ НЕ является свидетельством поддержки; пусто — является.

    Судится НОСИТЕЛЬ: объект ли это, знакома ли версия схемы (закрытый
    перечень `SUPPORT_SCHEMA_VERSIONS`) и сходится ли форма со схемой
    `support/1`. Каталог здесь ни при чём: файл, написанный для ДРУГОГО
    отпечатка, — годный носитель с негодным содержанием, и об этом
    говорит `support_of`, а не эта функция.

    Одна функция на всех читателей: `Catalog.support()` поднимает по ней
    отказ читателя, `support_of` — признак негодности. Двух перечней
    версий в дереве не бывает."""
    if not isinstance(doc, dict):
        return [f"свидетельство поддержки не объект JSON: "
                f"{type(doc).__name__}"]
    беды: list[str] = []
    version = doc.get("schema_version")
    if version not in SUPPORT_SCHEMA_VERSIONS:
        беды.append(f"свидетельство неизвестной версии схемы {version!r} "
                    f"(читатель знает "
                    f"{', '.join(SUPPORT_SCHEMA_VERSIONS)})")
    if not isinstance(doc.get("cells"), list):
        беды.append("свидетельство поддержки без списка cells")
    if беды:                    # дальше схемой судить нечего — форма не та
        return беды
    return schema_messages(doc, _support_schema(), "свидетельство")


def support_of(catalog, doc) -> dict:
    """Годность свидетельства для ЭТОГО каталога — одной функцией:
    `{"valid": bool, "why": str | None, "cells": [...]}`.

    Зовут её все читатели свидетельства: ручка службы
    (`catalog_service.executors.support_view`), дверь COSMOS и CDPM.
    Иначе каждый писал бы сверку заново, и однажды один счёл бы годным
    то, что другой отверг (общий закон 4 П0: один расчёт у производителя
    и приёмника).

    Годно, когда носитель читается (`support_form_problems`) И написан
    для этого каталога: та же тройка версии И тот же отпечаток — тем же
    законом, каким судится файл привязок (`matches_catalog`). Судить
    один отпечаток мало: версия и отпечаток — разные вопросы, и шапка
    свидетельства несёт оба.

    `cells` отдаются ВСЕГДА, даже когда свидетельство негодно: ячейки
    показывают человеку («вот что записано и почему это не считается»),
    исполнимостью их при `valid: false` не считает никто."""
    cells = list((doc or {}).get("cells") or ()) \
        if isinstance(doc, dict) else []
    if doc is None:
        return {"valid": False, "cells": [],
                "why": "свидетельство поддержки не вычислено"}
    беды = support_form_problems(doc)
    if беды:
        return {"valid": False, "cells": cells, "why": "; ".join(беды)}
    stamp = doc.get("catalog") if isinstance(doc.get("catalog"), dict) else {}
    try:
        d = compare_versions(catalog.version(), str(stamp.get("version")))
        same = d.level == "same"
    except ValueError:
        same = False
    if not same or stamp.get("fingerprint") != catalog.fingerprint():
        return {"valid": False, "cells": cells,
                "why": f"свидетельство написано для каталога "
                       f"{stamp.get('version')} "
                       f"{str(stamp.get('fingerprint') or '—')[:12]}, "
                       f"а этот — {catalog.version()} "
                       f"{catalog.fingerprint()[:12]}"}
    return {"valid": True, "cells": cells, "why": None}


def file_sha256(path) -> str | None:
    """sha256 байт файла привязок; файла нет либо не читается — None."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _bindings_identity(path, doc: dict | None) -> dict:
    """Идентичность файла привязок в ячейке (сторона COSMOS §4.2): шесть
    полей, и все шесть есть всегда — у продукта без файла они `null`."""
    doc = doc if isinstance(doc, dict) else {}
    return {"sha256": file_sha256(path) if path else None,
            "product_version": doc.get("product_version"),
            "generated_from": doc.get("generated_from"),
            "catalog_version": doc.get("catalog_version"),
            "catalog_fingerprint": doc.get("catalog_fingerprint"),
            "generated_at": doc.get("generated_at")}


def _rows_by_record(doc: dict | None) -> dict:
    """{(раздел, id): строка файла привязок} — по записи."""
    out: dict = {}
    for row in (doc or {}).get("bindings") or []:
        if isinstance(row, dict):
            out[(str(row.get("section")), str(row.get("id")))] = row
    return out


def _best_binding(row: dict | None, kind: str) -> dict | None:
    """Поля лучшей строки исполнителя вида для ячейки свидетельства.

    Выбор строки — не свой, а общий (`best_executor`): вторая формула
    «лучшая строка» рядом с первой однажды выбрала бы другую строку, и
    клетка экрана разошлась бы с ячейкой свидетельства."""
    best = best_executor(row, kind)
    if best is None:
        return None
    return {k: best.get(k) for k in _BINDING_FIELDS}


def _stand_cell(rows, kind: str) -> dict | None:
    """Стенд вида у записи: задачи, счётчики, последний коммит и момент.

    Строки стенда называют вид полем `executor_kind`; строка без вида
    ни одной ячейке не засчитывается — стенд обязан сказать, ЧТО он
    подтвердил."""
    mine = [r for r in rows or ()
            if isinstance(r, dict) and str(r.get("executor_kind")) == str(kind)]
    if not mine:
        return None
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for r in mine:
        res = str(r.get("result"))
        if res in counts:
            counts[res] += 1
    last = max(mine, key=lambda r: str(r.get("at") or ""))
    return {"tasks": sorted({str(r.get("task")) for r in mine
                             if r.get("task")}),
            **counts, "commit": last.get("commit"), "at": last.get("at")}


def _cell_state(binding: dict | None, stand: dict | None,
                fresh: bool) -> str:
    """Состояние ячейки по строке привязок, стенду и свежести файла.

    «Есть» — строка «есть» И стенд по виду на этом отпечатке без
    провалов (≥ 1 `passed`, 0 `failed`); «не проверено» — строка «есть»
    без такого подтверждения (нет стенда, один `skipped`, провал либо
    файл продукта написан не для этого каталога); «нет» — строки нет,
    «нет» либо «заявлен» (обещание исполнителем не делает)."""
    if binding is None or str(binding.get("state")) != "есть":
        return "нет"
    if not fresh:
        return "не проверено"
    passed = int((stand or {}).get("passed") or 0)
    failed = int((stand or {}).get("failed") or 0)
    return "есть" if passed >= 1 and failed == 0 else "не проверено"


def support_table(catalog, bindings_by_product, stand=None, approved_by=None,
                  *, approved_how=None, computed_at=None,
                  computed_by=SUPPORT_COMPUTED_BY) -> dict:
    """Свидетельство поддержки записей каталога — ОДНА функция на всех.

    Ячейка `запись × раздел × продукт × вид` (сторона COSMOS §4.2 и
    приложение Г): состояние тремя словами, строка привязок, идентичность
    файла привязок, стенд и акт владельца. Заявляет продукт (файл
    привязок и стендовые подтверждения), ВЫЧИСЛЯЕТ эта функция (служба
    каталога её зовёт и кладёт ответ в `catalog/support.json`),
    утверждает владелец актом выпуска. Второй формулы нет ни в службе,
    ни в CDPM: «исполнимо» обязано выводиться одинаково у обоих.

    `bindings_by_product` — `{продукт: путь к файлу привязок | None}`;
    продукты берутся из словаря `products` каталога, и ячейка заводится
    даже там, где файла продукта нет (честное «нет», а не пробел).
    `stand` — необязательная подмена стендовых подтверждений
    `{продукт: {(раздел, id): [строки stand_verified]}}` для случая,
    когда стенд приходит не из файла привязок; `None` — брать
    `stand_verified` самих файлов.
    `approved_by` / `approved_how` — акт владельца (выпуск): заполняются
    ТОЛЬКО у подтверждённых ячеек; между выпусками `approved: null`
    («не проверено» в терминах готовности)."""
    computed_at = computed_at or _now_words()
    products = [str(p) for p in
                (catalog.dictionaries().get("products")
                 or sorted(bindings_by_product or {}))]
    docs: dict = {}
    for product in products:
        path = (bindings_by_product or {}).get(product)
        doc = None
        if path is not None and Path(path).is_file():
            try:
                doc = load_bindings(path)
            except CatalogError:
                doc = None          # файл есть, но не читается — судить нечем
        docs[product] = {"path": path, "doc": doc,
                         "rows": _rows_by_record(doc),
                         "fresh": matches_catalog(catalog, doc),
                         "identity": _bindings_identity(path, doc)}
    plan = [("families", r, "executor_kinds") for r in catalog.families_full()]
    plan += [("criteria", r, "computable_by")
             for r in catalog.criteria().values()]
    cells: list = []
    for section, record, kinds_field in plan:
        rid = str(record.get("id"))
        for kind in [str(k) for k in (record.get(kinds_field) or [])]:
            for product in products:
                side = docs[product]
                row = side["rows"].get((section, rid))
                binding = _best_binding(row, kind)
                rows = (stand or {}).get(product, {}).get((section, rid)) \
                    if stand is not None and product in stand \
                    else (row or {}).get("stand_verified")
                stand_cell = _stand_cell(rows, kind)
                state = _cell_state(binding, stand_cell, side["fresh"])
                approved = None
                if state == "есть" and approved_by:
                    approved = {"by": str(approved_by),
                                "at": str(computed_at)[:10],
                                "how": str(approved_how or "")}
                cells.append({"section": section, "record_id": rid,
                              "kind": kind, "product": product,
                              "state": state, "binding": binding,
                              "bindings_file": dict(side["identity"]),
                              "stand": stand_cell, "approved": approved})
    cells.sort(key=lambda c: (c["section"], c["record_id"], c["kind"],
                              c["product"]))
    return {"schema_version": SUPPORT_SCHEMA_VERSION,
            "form_version": SUPPORT_FORM_VERSION,
            "catalog": {"version": catalog.version(),
                        "fingerprint": catalog.fingerprint()},
            "computed_at": str(computed_at),
            "computed_by": str(computed_by),
            "cells": cells}


def _now_words() -> str:
    """Момент с точностью до секунды и смещением — образец
    `$defs/datetime` схемы каталога."""
    from datetime import datetime                        # noqa: PLC0415
    return datetime.now().astimezone().isoformat(timespec="seconds")
