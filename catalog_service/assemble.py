# -*- coding: utf-8 -*-
"""«Собрать черновик» — фиксация черновиков рабочей папки в каталоге.

Сборка не «применяет правки по одной»: она строит кандидата целиком
(`store.assemble_in_memory`), судит его правилами читателя, называет
версию по ОДНОЙ формуле и отдаёт всё писателю одним вызовом. Ничего
половинчатого на диск не ложится: отказ любой проверки означает, что
папка каталога не тронута.

Название действия — «собрать черновик», а не «применить»: по
терминологии схемы записи каталог — это собранный срез, а не журнал
применённых правок.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import template_catalog as tc
from template_catalog import diff as tdiff
from template_catalog import writer
from template_catalog.manifest import parse_version

from . import executors, identity, markers
from .errors import ServiceError

#: Первая команда цепочки (решение 0.14 пакета паритета): посев и дельта
#: перехода — ПРОЕКЦИЯ каталога, и после каждой сборки их пересчитывает
#: инструмент, а не рука. До `--refresh` правило свежести носителей L23
#: красно словами; после — зелено.
REFRESH_COMMAND = (r".venv\Scripts\python.exe catalog\tools\build_from_code.py"
                   r" --refresh --out catalog")
#: Цепочка стенда и привязок после каждой сборки (§5 проекта шага 5;
#: порядок обязателен).
CHAIN_COMMANDS = (
    r".venv\Scripts\python.exe -m vmp.stand_tasks "
    r"--write-results vmp\stand_results.json",
    # `--prune-pending`: запись, ставшая записью каталога, перестаёт быть
    # «ожидающей» в посеве привязок — отметку снимает цепочка, а не рука
    # (выпуск 0.4.0, 09.09.2026: duty_series_limit осталась ожидающей и
    # красила гейт волны).
    r".venv\Scripts\python.exe -m vmp.bindings_build "
    r"--out vmp\bindings.json --stand-results vmp\stand_results.json "
    r"--prune-pending",
    # Сторона CDPM — те же два шага её генераторами (просьба CDPM
    # 09.09.2026 после выпуска 0.3.0: cdpm/stand_results.json остался с
    # отпечатком 0.2.0, тест T10 краснел): стенд таблиц и привязки CDPM
    # перевыпускаются в ТОМ ЖЕ коммите, до гейта, который читает обе
    # стороны.
    r".venv\Scripts\python.exe -m cdpm.stand_tables "
    r"--out cdpm\stand_results.json --tasks catalog",
    r".venv\Scripts\python.exe cdpm\bindings_build.py "
    r"--out cdpm\bindings.json --stand-results cdpm\stand_results.json",
    r".venv\Scripts\python.exe -m vmp.bindings_gate "
    r"vmp\bindings.json cdpm\bindings.json",
)
#: Последняя команда цепочки: закон паритета судит живой каталог против
#: кода COSMOS. Он же — двенадцатая предпроверка выпуска.
PARITY_COMMAND = r".venv\Scripts\python.exe catalog\tools\parity.py"
#: Цепочка после каждой сборки целиком: `--refresh` первой, `parity.py`
#: последней (0.14).
NEXT_COMMANDS = (REFRESH_COMMAND, *CHAIN_COMMANDS, PARITY_COMMAND)


# ─────────────────────────────────────────────── версия и подсказки

def base_of(cat) -> tuple[dict | None, dict | None]:
    """(тело последнего выпуска | None, запись журнала | None).

    Тело берётся из папки выпуска `releases/<версия>`: именно она —
    неизменяемый свидетель того, ЧТО было выпущено. Папки нет (журнал
    перенесён, площадка без выпусков) — базы нет, и подсказка честно
    считает всё «добавленным»."""
    versions = cat.versions()
    if not versions:
        return None, None
    last = versions[-1]
    folder = Path(cat.path) / "releases" / str(last.get("version"))
    if folder.is_dir():
        try:
            return tc.load(folder).body(), last
        except tc.CatalogError:
            return None, last
    return None, last


def suggest_assemble(cat, cand) -> dict:
    """Подсказка номера для сборки черновика."""
    version = choose_version(cat, cand, None)
    base_body, base = base_of(cat)
    level = tdiff.suggest_level(tdiff.diff_bodies(base_body, cand.body()))
    return {"level": level, "version": version,
            "base": str(base.get("version")) if base else None}


def suggest_release(cat) -> dict:
    """Подсказка номера выпуска: уровень правок с последнего выпуска."""
    base_body, base = base_of(cat)
    changes = tdiff.diff_bodies(base_body, cat.body())
    level = tdiff.suggest_level(changes)
    if base is None:
        version = "0.1.0"
    else:
        version = tdiff.next_version(str(base.get("version")), level)
    floor = _triple(version)
    cur = _triple(cat.version())
    if cur > floor:
        version = "%d.%d.%d" % cur
    counts = {"major": 0, "minor": 0, "patch": 0}
    for c in changes:
        if c.level in counts:
            counts[c.level] += 1
    return {"level": level, "version": version, "changes": len(changes),
            "by_level": counts,
            "base": str(base.get("version")) if base else None}


def _triple(version: str) -> tuple:
    return parse_version(str(version))[0]


def _draft_n(version: str) -> int:
    """Номер черновика; у выпуска — 0 (выпуск старше любого черновика
    своей тройки только по тройке, номер черновика тут не при чём)."""
    return parse_version(str(version))[1] or 0


def choose_version(cat, cand, explicit: str | None) -> str:
    """Одна формула номера сборки (§2.4 проекта).

    База — последняя запись журнала версий; уровень — подсказка диффа от
    неё к кандидату; пол (`floor`) — следующий номер по уровню. Тройка
    кандидата не ниже пола и не ниже текущей; номер черновика растёт,
    пока тройка не сменилась."""
    base_body, base = base_of(cat)
    level = tdiff.suggest_level(tdiff.diff_bodies(base_body, cand.body()))
    if base is None:
        floor = (0, 1, 0)
    else:
        floor = _triple(tdiff.next_version(str(base.get("version")), level))
    cur_triple = _triple(cat.version())
    triple = max(cur_triple, floor)
    if explicit is None:
        if triple == cur_triple and cat.is_draft():
            n = _draft_n(cat.version()) + 1
        else:
            n = 1
        return "%d.%d.%d-draft.%d" % (*triple, n)
    return _check_explicit(cat, explicit, floor, triple)


def _check_explicit(cat, explicit: str, floor: tuple, triple: tuple) -> str:
    try:
        et, en = parse_version(str(explicit))
    except ValueError as e:
        raise ServiceError("bad-form", str(e), 400) from None
    suggestion = "%d.%d.%d-draft.%d" % (
        *triple, (_draft_n(cat.version()) + 1
                  if triple == _triple(cat.version()) and cat.is_draft()
                  else 1))
    if en is None:
        raise ServiceError(
            "version-not-newer",
            f"сборка называет черновик, а не выпуск: {explicit} без "
            f"-draft.N; предложено {suggestion}", 409)
    if et < floor:
        # §2.4 «Правило версии сборки»: любой отказ явной версии сборки —
        # 409 `version-not-newer`. `version-too-low` — код ВЫПУСКА, и
        # ветвление интерфейса и документов по кодам не должно видеть его
        # там, где его нет в контракте сборки.
        raise ServiceError(
            "version-not-newer",
            f"{explicit} ниже подсказки уровня правок "
            f"({'%d.%d.%d' % floor}); предложено {suggestion}", 409)
    cur_triple, cur_n = _triple(cat.version()), _draft_n(cat.version())
    if (et, en) <= (cur_triple, cur_n):
        raise ServiceError(
            "version-not-newer",
            f"{explicit} не новее текущей {cat.version()}; предложено "
            f"{suggestion}", 409)
    return str(explicit)


# ──────────────────────────────────────────────────────── сборка

def stamp_candidate(cat, cand, actor: str | None, note: str = "") -> str:
    """Поставить кандидату ту шапку, с которой он ляжет на диск, и
    вернуть номер версии.

    Судить надо ровно то, что будет записано: кандидат с ЧУЖОЙ шапкой
    (например, с номером уже сделанного выпуска) ловил бы H03 «отпечаток
    записи журнала не равен шапке» — правило о выпуске, а не о правке."""
    version = choose_version(cat, cand, None)
    cand.header["version"] = version
    cand.header["released_by"] = actor or cand.header.get("released_by")
    cand.header["released_at"] = date.today().isoformat()
    if note:
        cand.header["note"] = note
    return version


def changes_json(changes) -> list[dict]:
    return [{"section": c.section, "id": c.id, "kind": c.kind,
             "note": c.note, "level": c.level} for c in changes]


def candidate_changes(cat, cand) -> list:
    """Что изменится в каталоге, если собрать черновики сейчас."""
    return tdiff.diff_bodies(cat.body(), cand.body())


def assemble(store, journal, *, note: str, version: str | None = None,
             confirm_stop: bool = False) -> dict:
    """Собрать черновики в каталог. Ничего половинчатого: любая красная
    проверка — папка каталога не тронута."""
    settings = store.settings
    identity.require(settings, "assemble")
    note = str(note or "").strip()
    if not note:
        raise ServiceError("bad-form",
                           "заметка сборки обязательна: чем эта версия "
                           "отличается от предыдущей", 400)
    with store.writing():
        cat = store.catalog()
        drafts = store.drafts()
        if not drafts:
            raise ServiceError("nothing-to-assemble",
                               "черновиков нет — собирать нечего", 409)
        cand = store.assemble_in_memory(cat, drafts, actor=settings.actor)
        if not cand.changes_canon:
            raise ServiceError("nothing-to-assemble",
                               "черновики не меняют канон каталога", 409)
        terms = markers.terms(settings)
        warnings = []
        if not terms:
            warnings.append({"where": "catalog", "code": "markers-missing",
                             "message": markers.missing_words(settings),
                             "severity": "warning"})
        # Номер версии выбирается ДО суда: судится ровно то, что ляжет на
        # диск, вместе со своей шапкой.
        new_version = choose_version(cat, cand, version)
        cand.header["version"] = new_version
        cand.header["released_by"] = settings.actor
        cand.header["released_at"] = date.today().isoformat()
        cand.header["note"] = note
        candidate = cand.catalog(settings.catalog_dir)
        problems = candidate.validate(forbidden_terms=terms)
        errors = [p for p in problems if p.severity == "error"]
        if errors:
            journal.append(actor=settings.actor, op="assemble",
                           target="catalog", outcome="refused",
                           words=f"проверка дала {len(errors)} ошибок")
            raise ServiceError(
                "invalid-catalog",
                f"собранный каталог не проходит проверку: ошибок "
                f"{len(errors)} — ничего не записано", 422, problems=problems)
        stop = executors.bindings_stop(cat, settings, new_version)
        if stop is not None and not confirm_stop:
            journal.append(actor=settings.actor, op="assemble",
                           target="catalog", outcome="refused",
                           words=stop.words)
            raise stop
        changes = candidate_changes(cat, cand)
        header = writer.build_header(
            cat.header, version=new_version, released_by=settings.actor,
            released_at=date.today().isoformat(), note=note,
            body=cand.body(),
            legacy_registry_rev=writer.legacy_registry_rev(
                candidate.families()))
        try:
            written = writer.write_catalog(
                settings.catalog_dir, header=header,
                families=cand.families, criteria=cand.criteria,
                dictionaries=cand.dictionaries, requests=cand.requests,
                versions=cand.versions,
                families_order=cand.families_order,
                forbidden_terms=terms)
        except writer.CatalogWriteError as e:
            journal.append(actor=settings.actor, op="assemble",
                           target="catalog", outcome="refused", words=str(e))
            raise ServiceError("invalid-catalog", str(e), 422,
                               problems=getattr(e, "problems", ())) from None
        store.clear_drafts()
        store.forget()
        journal.append(
            actor=settings.actor, op="assemble", target="catalog",
            outcome="warning" if (stop is not None or warnings) else "ok",
            words=f"собрана {new_version} ({cand.applied} черновиков"
                  + (", осознанное продолжение при стопе сторожа"
                     if stop is not None else "") + f"): {note}")
        return {"version": written.version(),
                "fingerprint": written.fingerprint(),
                "applied": cand.applied,
                "derived": list(cand.derived),
                "changes": changes_json(changes),
                "problems": [_p(p) for p in problems] + warnings,
                "confirmed_stop": stop is not None,
                "next": list(NEXT_COMMANDS)}


def _p(problem) -> dict:
    return {"where": problem.where, "code": problem.code,
            "message": problem.message, "severity": problem.severity}


def preview(store, cat=None, drafts=None) -> tuple:
    """Кандидат и его проблемы без записи — общее вычисление для
    сохранения черновика и для экрана «Версии»."""
    settings = store.settings
    cat = cat if cat is not None else store.catalog()
    drafts = drafts if drafts is not None else store.drafts()
    actor = settings.actor or "аналитик"
    cand = store.assemble_in_memory(cat, drafts, actor=actor)
    if drafts:
        stamp_candidate(cat, cand, actor)
    terms = markers.terms(settings)
    problems = cand.catalog(settings.catalog_dir).validate(
        forbidden_terms=terms)
    return cand, problems, bool(terms)


def new_problems(before: list, after: list) -> list:
    """Проблемы, которых у живого каталога не было: тройка
    (адрес, код, слова). Адрес может указывать на ДРУГУЮ запись — X01
    сообщает асимметрию дополнения по адресу второй стороны, и это
    ровно то, что нужно видеть."""
    known = {(p.where, p.code, p.message) for p in before}
    return [p for p in after if (p.where, p.code, p.message) not in known]

