# -*- coding: utf-8 -*-
"""Помощник тестов шага 2 каталога шаблонов: tmp-копии каталога.

Правило обёртки (vmp/template_registry): подмена констант в тестах —
только каталогом, не setattr. Помощник даёт tmp-копию catalog/ (разделы и
transition/, без tools/ и __pycache__) с правками записей по id,
пересчитывает lexicon_order (перестановка «есть»-семей со словами-
носителями), симметрию complements (X01) и отпечаток шапки (иначе любая
правка доказывала бы H02, а не проверяемое правило), пишет файлы
template_catalog.export.dump_json (LF). Один tmp-каталог на тест — новый
экземпляр читателя и одна проверка двери.

Со шага 4 копия несёт рядом с разделами и привязки продукта
(<dst>/vmp-bindings.json из живого vmp/bindings.json: строки записей,
оставшихся в копии, «нет» у пробных записей, шапка с версией и
отпечатком копии), а use_catalog переключает на них дверь переменной
CREW_COSMOS_BINDINGS. Опись transition/executors_legacy.json копии не
нужна: исполнителей называет файл привязок.

РЕЖИМ РОСТА КАТАЛОГА (COSMOS_TEST_CATALOG_GROWN=extension|clone|1 либо
путь к файлу заготовки): в КАЖДУЮ копию добавляются синтетическая семья
и синтетический критерий (template_catalog.growth), строки их привязок и
посев привязок копии (SEED_FILE рядом с разделами, дверь
CREW_BINDINGS_SEED). Так весь набор тестов прогоняется на каталоге,
который ВЫРОС, — и любое допущение «каталог не растёт» (жёсткое число
записей, рукописный перечень семей у судьи) краснеет до живого прогона,
а не после четырёх сорванных попыток агента. Без переменной поведение
прежнее. Судья, который называет пробел синтетической записи словами,
работает верно: probe_only / verdict_ok — договор режима.

    folder = copy_catalog(tmp_path, families={"duty_limits": {"violation_unit": "duty"}})
    use_catalog(monkeypatch, folder)     # CREW_TEMPLATE_CATALOG + CREW_COSMOS_BINDINGS
    bump_mtime(folder / "catalog.json")  # +2 с: грубые отметки времени ФС
    calls = count_reads(monkeypatch)     # вызовы reader.read_json_file
    n = count_validate(monkeypatch)      # вызовы Catalog.validate
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                     # noqa: E402
from template_catalog import growth                               # noqa: E402
from template_catalog.export import dump_json                     # noqa: E402
from template_catalog.writer import (legacy_registry_rev,        # noqa: E402
                                     refresh_lexicon_order)

CATALOG = ROOT / "catalog"
SECTION_FILES = ("catalog.json", "families.json", "criteria.json",
                 "dictionaries.json", "requests.json", "versions.json")
#: Переходные носители, которые копия каталога несёт рядом с разделами.
#: Со шага 4 исполнителей называет файл привязок (BINDINGS_FILE), а
#: опись executors_legacy.json — свидетель шага 1, который шаг 7-Б
#: ОСТАВИЛ на диске: она левая сторона гейта «ничего не потеряно»
#: (tests/vmp/test_bindings.py, п. 13; руководство 13.12). Дверь её не
#: читает, копии она не нужна.
TRANSITION_FILES = ("families_order.json",)
#: Привязки продукта в копии каталога: дверь читает их по переменной
#: CREW_COSMOS_BINDINGS (файл рядом с разделами, а не vmp/bindings.json).
BINDINGS_FILE = "vmp-bindings.json"
#: Переменная двери, подменяющая путь к привязкам продукта (§3 проекта
#: шага 4). Имя — контракт, а не догадка: дверь читает ровно её.
BINDINGS_ENV = "CREW_COSMOS_BINDINGS"
LIVE_BINDINGS = ROOT / "vmp" / "bindings.json"
#: Посев привязок копии — только в РЕЖИМЕ РОСТА: судьи, которые
#: пересобирают привязки сами (закон паритета, `bindings_build --check`),
#: обязаны читать посев, знающий синтетические записи копии, а не посев
#: дерева. Без режима файла нет, и дверь посева не трогается — обычный
#: гейт продукта остаётся прежним.
SEED_FILE = growth.BESIDE_SEED
LIVE_SEED = ROOT / "vmp" / "bindings_seed.json"


def rmtree_writable(path) -> None:
    """shutil.rmtree, который снимает «только чтение» с того, что мешает
    удалению, и повторяет шаг.

    Клиенты синхронизации рабочего стола (Google Drive и подобные)
    выставляют папкам дерева атрибут «только чтение»; copytree переносит
    его на tmp-копию каталога (copystat), а rmdir такой папки на Windows
    отвечает «Access is denied» — 09.09.2026 так покраснели девять строк
    полного набора при нетронутом коде. На POSIX та же беда выглядит как
    папка без права записи: её записи нельзя удалить. Помощник снимает
    режим с самого пути и с его папки-родителя и повторяет операцию;
    вторая неудача — наружу, как у обычного rmtree."""
    def _onexc(func, p, exc):
        if not isinstance(exc, PermissionError):
            raise exc
        for q in (p, os.path.dirname(p)):
            try:
                os.chmod(q, stat.S_IRWXU)
            except OSError:
                pass
        func(p)
    shutil.rmtree(path, onexc=_onexc)


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _write(p: Path, doc) -> None:
    p.write_bytes(dump_json(doc).encode("utf-8"))


def _apply(rec: dict, patch) -> None:
    if callable(patch):
        patch(rec)
    else:
        rec.update(patch)


def tree_candidate_docs() -> list:
    """Документы кандидатов конвейера рядом с ЖИВЫМ каталогом — целиком,
    как их читает закон паритета (`candidates/<раздел>/<id>.json`).

    Нужны стендам, которые не правят копию разделов, а МАТЕРИАЛИЗУЮТ
    временный каталог (`template_catalog.candidates.materialize`): такой
    стенд обязан класть кандидатов дерева первыми, иначе посев дерева
    называет записи, которых в его папке нет. Пустой список на чистом
    дереве — папки `catalog/candidates/` там просто нет."""
    root = CATALOG / "candidates"
    if not root.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8-sig"))
            for p in sorted(root.rglob("*.json")) if p.is_file()]


def tree_candidates() -> tuple[dict, dict]:
    """Записи кандидатов конвейера рядом с ЖИВЫМ каталогом:
    ({семья: запись}, {критерий: запись}).

    Пустой словарь на чистом дереве — там папки `catalog/candidates/`
    просто нет. На ветке правки класса «рост таблиц решателя» она есть, и
    судимый каталог дерева — «живой ⊕ кандидаты» (0.3 проекта паритета):
    таблицы решателя ветки знают новую ручку, а живая запись — ещё нет.
    Копия, собранная без кандидата, была бы красна по L05 у КАЖДОГО
    стенда — по построению, а не по делу."""
    root = CATALOG / "candidates"
    fams: dict = {}
    crits: dict = {}
    if not root.is_dir():
        return fams, crits
    for p in sorted(root.rglob("*.json")):
        if not p.is_file():
            continue
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
        rec = copy.deepcopy(doc.get("record") or {})
        rec.pop("history", None)                  # историю пишет сборка
        rid = str(doc.get("id"))
        (fams if doc.get("section") == "families" else crits)[rid] = rec
    return fams, crits


def _new_family(template: dict, fid: str) -> dict:
    # stand_tasks образца пробной записи не наследуются: их ожидания
    # названы по видам ОБРАЗЦА, и у пробной записи с другими видами
    # X05 сказал бы «вид не объявлен записью» вместо проверяемого правила.
    rec = copy.deepcopy(template)
    rec.update({"id": fid, "params": [], "param_sets": [], "conditions": [],
                "complements": [], "subject_words": None, "rule_form": None,
                "stand_tasks": []})
    return rec


#: Свидетель природы по её слову (правило X17 валидатора каталога).
_WITNESS_BY_NATURE = {"history": "solver_tables",
                      "people": "certifier_registry",
                      "world_param": "certifier_registry",
                      "schedule": "none"}


def _align_050(rec: dict, patch: dict) -> None:
    """Три поля каталога 0.5.0 у записи ПО ОБРАЗЦУ — по её собственному
    виду и природе, а не по образцу (тот же закон, что у клона роста,
    template_catalog.growth): образец мягкой семьи истории иначе приносил
    бы пробной жёсткой записи размер нарушения (X16 «задан ⇔ SOFT»), а
    правка природы — чужого свидетеля (X17). Поле, названное правкой
    теста явно, остаётся как названо."""
    if "violation_size" not in patch:
        if rec.get("kind") != "SOFT":
            rec["violation_size"] = None
        elif not rec.get("violation_size"):
            rec["violation_size"] = "count"
    if "nature_confirmed_by" not in patch:
        rec["nature_confirmed_by"] = _WITNESS_BY_NATURE.get(
            str(rec.get("nature")), rec.get("nature_confirmed_by"))


def _whole_record(known: dict, rid: str) -> dict | None:
    """Запись ЦЕЛИКОМ из перечня кандидатов дерева и синтетических
    записей режима роста — как её судит временный каталог конвейера.

    Пробная запись по образцу здесь не годится: образец тащит историю
    базовой семьи с отметкой шага 1, и правило L17 говорило «запись вне
    базы заморозки несёт отметку шага 1» (09.09.2026, первый кандидат
    новой семьи на ветке CR). Записи без истории она дописывается
    строкой о заявке — так её пишет сборка службы при переводе заявки в
    записи (история обязана быть непустой по схеме; дата сегодняшняя,
    после заморозки)."""
    rec = (known or {}).get(rid)
    if not isinstance(rec, dict) or not rec.get("id"):
        return None
    rec = copy.deepcopy(rec)
    if not rec.get("history"):
        import datetime as _dt
        rec["history"] = [{
            "by": "контур самоулучшения",
            "date": _dt.date.today().isoformat(),
            "kind": "заявка",
            "note": "кандидат конвейера в копии стенда «живой ⊕ кандидаты»",
            "ref": None}]
    rec.setdefault("stand_tasks", [])
    rec["status"] = rec.get("status") or "есть"
    return rec


def _new_criterion(crits: list, cid: str) -> dict:
    base = next((r for r in crits
                 if (r.get("semantics") or {}).get("rates_form") is None),
                crits[0])
    rec = copy.deepcopy(base)
    rec["id"] = cid
    rec["stand_tasks"] = []
    return rec


def copy_catalog(tmp_path, *, families: dict | None = None,
                 criteria: dict | None = None, drop=(),
                 transition: bool = True, refresh: bool = True,
                 bindings: bool = True, name: str = "catalog",
                 releases: bool = False, version: str | None = None,
                 candidates: bool = True, requests: bool = True) -> Path:
    """tmp-копия catalog/ с правками. families / criteria — {id: правка},
    правка — словарь полей (обновление записи) либо функция(запись);
    неизвестный id — новая запись по образцу существующей. drop — семьи,
    снимаемые из каталога (и из transition/). transition=False — без
    переходных носителей и без привязок. refresh=False — отпечаток шапки
    НЕ пересчитывается (проверка H02). bindings=False — копия без файла
    привязок (проверка отказа двери «файла привязок нет»).

    releases=False (умолчание): копия — ЧЕРНОВИК. Журнал версий и папки
    `releases/` живого каталога в копию не переносятся, потому что копия
    правится: отпечаток пересчитывается, и запись журнала о выпуске
    указывала бы на тело, которого в копии уже нет (H03 «отпечаток
    записи журнала не равен шапке»). Тесты правил проверяли бы вместо
    правила эту рассинхронизацию. Кому нужен каталог с выпусками —
    releases=True либо выпуск писателем прямо в тесте.

    candidates=True (умолчание): копия повторяет СУДИМЫЙ каталог дерева —
    «живой ⊕ кандидаты `catalog/candidates/**`». На чистом дереве это
    ничего не меняет, а на ветке правки класса «рост таблиц решателя»
    без кандидата копия была бы красна по L05 у каждого стенда — по
    построению, а не по делу. candidates=False нужен там, где копия
    обязана равняться каталогу, который описывает ПОСЕВ инструмента
    (`--check` без `--from-catalog`).

    version — номер версии в шапке копии (привязки копии получают его
    же). Нужен ветвям правил, которые бывают ТОЛЬКО у черновика: живой
    каталог с 08.09 — выпуск 0.2.0, и «предупреждение вместо остановки»
    на нём уже не воспроизводится.

    requests=False — очередь заявок копии пуста (`requests.json` без
    заявок): стенды службы подают заявки сами, а живая очередь дерева (с
    09.09.2026 — заявка duty_series_limit в работе) красила бы счёт
    заявок и предпроверку выпуска «заявок в работе» по построению.
    Умолчание True — очередь как в дереве (заявки — вне отпечатка)."""
    dst = Path(tmp_path) / name
    if dst.exists():
        rmtree_writable(dst)
    # `candidates/` в копию не переносится НИКОГДА: кандидаты конвейера —
    # свойство ветки, а не каталога, и стенд, кладущий рядом своего
    # кандидата, судил бы заодно чужого. Их ЗАПИСИ копия получает
    # слиянием ниже (`tree_candidates`), а не файлами.
    ignore = ["tools", "__pycache__", "candidates"] \
        + ([] if transition else ["transition"]) \
        + ([] if releases else ["releases"])
    shutil.copytree(CATALOG, dst, ignore=shutil.ignore_patterns(*ignore))
    docs = {n: _read(dst / n) for n in SECTION_FILES}
    if not releases:
        docs["versions.json"] = {"form_version": 1, "versions": []}
    # Кандидаты дерева читаются ДО фильтра очереди: их заявки-свидетели
    # обязаны остаться в копии и при requests=False.
    cand_f, cand_c = tree_candidates() if candidates else ({}, {})
    # Режим роста каталога: синтетические записи ложатся в копию рядом с
    # кандидатами дерева — тем же путём и с теми же правами, и тем же
    # ключом `candidates` выключаются. Копия, которая обязана РАВНЯТЬСЯ
    # выпуску или посеву инструмента (`candidates=False`), синтетических
    # записей не несёт: её отпечаток — предмет суда, и рост каталога
    # красил бы H03 «отпечаток записи журнала не равен шапке» по
    # построению режима.
    grown = growth.mode() if candidates else None
    if grown:
        probe_f, probe_c = growth.probe_records(
            grown, docs["families.json"]["families"])
        cand_f = {**cand_f, **probe_f}
        cand_c = {**cand_c, **probe_c}
        # Заявки-свидетели синтетических записей — в очередь копии до
        # фильтра `requests=False`. Сегодня список пуст: имён параметров
        # у синтетической записи нет, и подтверждать нечего. Ход оставлен
        # закономерным, а не выброшенным: запись с ИМЕНЕМ без свидетеля
        # красила бы копию «единицу не подтверждает ни словарь CDPM, ни
        # таблицы решателя, ни заявка» по построению (L05/L05b).
        own = {str(r.get("request_id"))
               for r in (docs["requests.json"].get("requests") or [])
               if isinstance(r, dict)}
        docs["requests.json"]["requests"] = (
            list(docs["requests.json"].get("requests") or [])
            + [rq for rq in growth.probe_requests(
                grown, docs["families.json"]["families"])
               if str(rq.get("request_id")) not in own])
    if not requests:
        # Пустая очередь — кроме заявок-СВИДЕТЕЛЕЙ кандидатов дерева,
        # которым свидетель НУЖЕН (`witness_ids`): единицы параметров
        # записи кандидата, неизвестные словарю CDPM и таблицам решателя,
        # подтверждает черновик её принятой заявки (L05 by_request), без
        # неё копия «не собрана» («единицу параметра не подтверждает ни
        # словарь CDPM, ни таблицы решателя, ни заявка» — ветка CR
        # duty_series_limit, попытка #3, 09.09.2026). Кандидату, чьи имена
        # решатель знает (попытка #4: ручки и единицы истории объявлены в
        # Kotlin), свидетель не нужен, и очередь остаётся пустой — как её
        # ждут стенды службы (счёт заявок, «отказ не оставляет заявки»).
        cand_ids = witness_ids(cand_f, cand_c) if candidates else set()
        docs["requests.json"] = {
            "form_version": 1,
            "requests": [rq for rq in (docs["requests.json"].get("requests") or [])
                         if isinstance(rq, dict)
                         and str(rq.get("record_id")) in cand_ids]}
    if version is not None:
        docs["catalog.json"]["version"] = str(version)
    fams: list = docs["families.json"]["families"]
    crits: list = docs["criteria.json"]["criteria"]
    drop = set(drop)
    fams[:] = [r for r in fams if r.get("id") not in drop]
    # Кандидаты конвейера — ПЕРВЫМИ, правки теста поверх них: копия
    # повторяет судимый каталог дерева «живой ⊕ кандидаты». На чистом
    # дереве словари пусты и не меняют ничего.
    families = {**cand_f, **(families or {})}
    criteria = {**cand_c, **(criteria or {})}
    for fid, patch in (families or {}).items():
        rec = next((r for r in fams if r.get("id") == fid), None)
        by_sample = False
        if rec is None:
            rec = _whole_record(cand_f, fid)
            if rec is None:
                rec = _new_family(fams[0], fid)
                by_sample = True
            fams.append(rec)
        _apply(rec, patch)
        if by_sample:
            _align_050(rec, patch if isinstance(patch, dict) else {})
    for cid, patch in (criteria or {}).items():
        rec = next((r for r in crits if r.get("id") == cid), None)
        if rec is None:
            # Тот же закон, что у семей: кандидат (и синтетическая
            # запись режима роста) кладётся ЗАПИСЬЮ ЦЕЛИКОМ. Прежде
            # критерий-кандидат собирался по образцу существующего и
            # тащил его историю с отметкой шага 1 — правило L17 называло
            # бы «запись вне базы заморозки несёт отметку шага 1» у
            # первого же критерия конвейера.
            rec = _whole_record(cand_c, cid) or _new_criterion(crits, cid)
            crits.append(rec)
        _apply(rec, patch)
    fams.sort(key=lambda r: str(r.get("id")))
    crits.sort(key=lambda r: str(r.get("id")))
    # complements: только существующие семьи, без самоссылок, симметрично
    by_id = {r["id"]: r for r in fams}
    for r in fams:
        r["complements"] = sorted(c for c in (r.get("complements") or [])
                                  if c in by_id and c != r["id"])
    for r in fams:
        for c in list(r["complements"]):
            other = by_id[c]
            if r["id"] not in other["complements"]:
                other["complements"] = sorted(other["complements"] + [r["id"]])
    # lexicon_order: перестановка «есть»-семей со словами-носителями.
    # Правило живёт в писателе читателя (шаг 5, §2.7 проекта): помощник
    # копий и сборка службы обязаны поддерживать инвариант D02 ОДНОЙ
    # формулой, иначе копия теста и живой каталог расходились бы.
    order = list(docs["dictionaries.json"].get("lexicon_order") or [])
    docs["dictionaries.json"]["lexicon_order"] = refresh_lexicon_order(
        order, fams)
    if refresh:
        body = {"families": fams, "criteria": crits,
                "dictionaries": {k: v for k, v
                                 in docs["dictionaries.json"].items()
                                 if k != "form_version"}}
        docs["catalog.json"]["fingerprint"] = tc.fingerprint_of(body)
    for n, doc in docs.items():
        _write(dst / n, doc)
    if refresh:
        # Старая ревизия шапки — та же формула, что у писателя и у
        # службы: правка ПАРАМЕТРОВ записи меняет карту «семья → имена»,
        # и без пересчёта копия краснила бы правило L10 («старая ревизия
        # шапки ≠ формуле над семьями») вместо проверяемого правила.
        docs["catalog.json"]["legacy_registry_rev"] = legacy_registry_rev(
            tc.load(dst).families())
        _write(dst / "catalog.json", docs["catalog.json"])
    if transition:
        # Порядок семей — переходный носитель ЖИВОГО каталога: семьи,
        # пришедшие в копию кандидатами дерева (или новыми правками
        # теста), в нём ещё нет, и читатель отказывает «порядок семей не
        # совпадает с записями «есть»» (живой случай 09.09.2026: первый
        # кандидат новой семьи duty_series_limit на ветке CR красил
        # копии всех стендов доступа к каталогу); снятые семьи — долой.
        or_p = dst / "transition" / "families_order.json"
        if or_p.is_file():
            order = [f for f in _read(or_p) if f not in drop]
            present = {str(r.get("id")) for r in fams
                       if r.get("status", "есть") == "есть"}
            order += sorted(fid for fid in present if fid not in order)
            _write(or_p, order)
    if transition and bindings and LIVE_BINDINGS.is_file():
        _copy_bindings(dst, docs, fams, crits)
    if grown and LIVE_SEED.is_file():
        _copy_seed(dst, fams, crits, grown)
    return dst


def copy_tools(folder, *, kotlin=None) -> Path:
    """Папка `tools/` рядом с копией каталога, а в режиме роста — с
    ОСВЕЖЁННЫМИ носителями инструмента (посев `tools/catalog_seed.json`
    и дельта `transition/step4_additions.json`).

    Посев инструмента и дельта приколочены к отпечатку каталога: копия,
    которая ВЫРОСЛА, обязана нести их пересчитанными, иначе правило L23
    закона паритета отвечает «посев отстал от каталога» — по построению
    режима, а не по делу. Формула пересчёта — та же, что у ключа
    `--refresh` инструмента и у самого правила L23
    (`build_from_code.refreshed`): второй формулы свежести не заводится."""
    dst = Path(folder) / "tools"
    shutil.copytree(CATALOG / "tools", dst, dirs_exist_ok=True)
    if not growth.mode():
        return dst
    tools = str(CATALOG / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import build_from_code as bfc                    # noqa: PLC0415
    seed_text, delta_text = bfc.refreshed(Path(folder), kt=kotlin)
    for rel, text in ((bfc.SEED_REL, seed_text), (bfc.DELTA_REL, delta_text)):
        p = Path(folder) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return dst


def probe_only(words) -> bool:
    """Красное объясняется ТОЛЬКО синтетическими записями режима роста?

    Заявленные пробелы режима: у синтетической записи нет исполнителя и
    нет стендовых задач — их пишет агент, а помощник не вправе выдумать
    ни судью символа, ни ожидания мира. Судья, назвавший эти пробелы
    СЛОВАМИ с именем записи, работает верно; тест такого судьи в режиме
    роста красен по заявленной причине, а не по допущению.

    Без режима роста — всегда False: красное остаётся красным."""
    if not growth.mode():
        return False
    text = str(words or "").strip()
    if not text:
        return False
    ids = growth.probe_ids()
    return all(any(i in part for i in ids)
               for part in re.split(r";\s*", text) if part.strip())


def verdict_ok(verdict) -> bool:
    """Вердикт закона паритета зелен — либо красен ТОЛЬКО заявленными
    пробелами синтетических записей режима роста (`probe_only`)."""
    return bool(getattr(verdict, "ok", False)) \
        or probe_only(getattr(verdict, "words", None))


def seed_with_probe(seed=None) -> dict:
    """Посев привязок ⊕ строки синтетических записей режима роста.

    Тестам, которые читают посев САМИ (`bindings_build.load_seed()`) и
    правят его под свой случай: без строк синтетических записей
    генератор откажет «запись каталога в посеве не названа» — по
    построению режима, а не по проверяемому правилу. Без режима роста
    посев возвращается как есть."""
    from vmp import bindings_build as bb
    doc = copy.deepcopy(seed if seed is not None else bb.load_seed())
    grown = growth.mode()
    if grown:
        doc["records"] = {
            **(doc.get("records") or {}),
            **growth.probe_seed_records(grown, doc.get("records") or {})}
    return doc


def _copy_seed(dst: Path, fams: list, crits: list, grown: str) -> None:
    """Посев привязок копии: строки посева дерева у записей, оставшихся
    в копии, ⊕ строки синтетических записей режима роста.

    Строки записей, которых в копии нет (`drop=`), в посев не идут:
    генератор отказал бы «запись посева, которой нет в каталоге», и
    копия краснела бы по построению помощника, а не по делу."""
    doc = _read(LIVE_SEED)
    present = {f"families/{r['id']}" for r in fams} \
        | {f"criteria/{r['id']}" for r in crits}
    records = {a: rows for a, rows in (doc.get("records") or {}).items()
               if a in present}
    records.update(growth.probe_seed_records(grown, doc.get("records") or {}))
    doc["records"] = dict(sorted(records.items()))
    doc["pending_records"] = [a for a in (doc.get("pending_records") or [])
                              if a not in present]
    _write(dst / SEED_FILE, doc)


def _known_param_names() -> set | None:
    """Имена параметров, единицу которых подтверждают словарь CDPM (посев
    инструмента) либо таблицы решателя дерева — те же свидетели, что у
    `unit_of` сборки и правил L05/L05b. None — свидетели не прочитаны
    (тогда заявки-свидетели оставляются: копия не вправе оказаться «не
    собранной» из-за молчания помощника)."""
    try:
        seed = json.loads((CATALOG / "tools" / "catalog_seed.json")
                          .read_text(encoding="utf-8-sig"))
        known = set((seed.get("cdpm_dictionaries") or {}).get("params") or {})
        from vmp import kotlin_tables as ktab
        t = ktab.tables()
        known |= set(t.get("knobs") or {}) | set(t.get("history_units") or {})
        if t.get("sit_param"):
            known.add(str(t["sit_param"]))
        return known
    except Exception:                                    # noqa: BLE001
        return None


def witness_ids(cand_f: dict | None = None, cand_c: dict | None = None) -> set:
    """Записи кандидатов дерева, которым НУЖНА заявка-свидетель: хотя бы
    одно имя параметра записи не подтверждают ни словарь CDPM, ни таблицы
    решателя. Кандидат, чьи имена решатель уже знает (класс «рост таблиц
    решателя»: ручки и единицы истории объявлены в Kotlin), свидетеля в
    очереди не требует — и очередь копии остаётся такой, какой её ждут
    стенды (пустой при requests=False).

    В режиме роста каталога сюда же попадают синтетические записи: их
    имён не знает никто, и без свидетеля копия «не собрана» по
    построению."""
    if cand_f is None or cand_c is None:
        cand_f, cand_c = tree_candidates()
        grown = growth.mode()
        if grown:
            probe_f, probe_c = growth.probe_records(
                grown, _read(CATALOG / "families.json")["families"])
            cand_f = {**cand_f, **probe_f}
            cand_c = {**cand_c, **probe_c}
    recs = {**{k: v for k, v in cand_f.items()}, **{k: v for k, v in cand_c.items()}}
    if not recs:
        return set()
    known = _known_param_names()
    out = set()
    for rid, rec in recs.items():
        names = {str(p.get("name")) for p in ((rec or {}).get("params") or [])
                 if isinstance(p, dict) and p.get("name")}
        if known is None or (names - known):
            out.add(str(rid))
    return out


def witness_requests(folder) -> list:
    """Заявки-СВИДЕТЕЛИ кандидатов дерева в очереди копии `folder`:
    заявки на записи кандидатов `catalog/candidates/**`, которым свидетель
    нужен (`witness_ids`).

    Единицы имён записи кандидата, которых не знают ни словарь CDPM, ни
    таблицы решателя, подтверждает черновик её принятой заявки (решение
    0.6 сборки, правила L05/L05b и `unit_of` инструмента). Без этих
    заявок копия «не собрана» — по построению, а не по делу. На чистом
    дереве — и у кандидата, чьи имена знает решатель, — список пуст."""
    ids = witness_ids()
    p = Path(folder) / "requests.json"
    if not ids or not p.is_file():
        return []
    doc = _read(p)
    return [rq for rq in (doc.get("requests") or [])
            if isinstance(rq, dict) and str(rq.get("record_id")) in ids]


def write_requests(folder, rows, *, keep_witnesses: bool = True) -> list:
    """Записать очередь заявок копии: строки теста ⊕ заявки-свидетели
    кандидатов дерева (`witness_requests`, впереди, без дублей по
    request_id). Возвращает записанный список — по его длине тест
    называет ожидаемый счёт «заявок N», а не литерал.

    Стенд, пишущий очередь целиком (`{"requests": [своя заявка]}`),
    на ветке правки с кандидатом новой записи стирал свидетеля её
    имён, и копия краснела «единицу не подтверждает … ни заявка»
    (09.09.2026, CR duty_series_limit). keep_witnesses=False — очередь
    ровно из строк теста (стенды негодной формы очереди)."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    out: list = []
    if keep_witnesses:
        own = {str(r.get("request_id")) for r in rows}
        out = [w for w in witness_requests(folder)
               if str(w.get("request_id")) not in own]
    out += rows
    _write(Path(folder) / "requests.json",
           {"form_version": 1, "requests": out})
    return out


#: Заметка строк «нет» у пробных записей помощника: копия каталога
#: остаётся полной для гейта B04, и слова говорят, откуда взялась строка.
PROBE_NOTE = "пробная запись помощника копий тестов — исполнителя нет"


def _candidate_rows(section: str, rid: str) -> list | None:
    """Строки привязок записи КАНДИДАТА — из посева дерева ТЕМ ЖЕ
    построителем, что у генератора (`vmp.bindings_build._executor`).

    Записи кандидата в живых привязках ещё нет, а пробная строка «нет»
    помощника её не описывает: у метрики решателя она вышла бы без поля
    `metric`, и закон паритета сказал бы «строка вида kotlin_metric без
    поля metric» — по построению помощника, а не по делу. Посев дерева
    такую запись уже называет (её дописывает агент вместе с
    исполнителем), и строки строятся из него — второй формулы строки в
    дереве не заводится. Отказ построителя (посев записи не называет,
    таблицы решателя не читаются) — не беда: копия получит пробную
    строку, как прежде."""
    try:
        from vmp import bindings_build as bb
        from vmp import bindings_gate as gate
        addr = f"{section}/{rid}"
        rows = (bb.load_seed().get("records") or {}).get(addr)
        if not rows:
            return None
        tbl = gate.tables()          # тот же свидетель, что у генератора
        return [bb._executor(r, addr, tbl) for r in rows]
    except Exception:                                    # noqa: BLE001
        return None


def _copy_bindings(dst: Path, docs: dict, fams: list, crits: list) -> None:
    """Привязки копии: строки записей, оставшихся в копии, шапка — с
    версией и отпечатком КОПИИ (иначе дверь скажет B01 «отпечаток
    привязок отстал» вместо проверяемого правила). У пробных записей,
    которых в живых привязках нет, пишутся строки «нет» по объявленным
    видам — иначе копия ловила бы B04 «в зоне, но не объявил» вместо
    проверяемого правила."""
    doc = _read(LIVE_BINDINGS)
    rows = {(row.get("section"), row.get("id")): row
            for row in doc.get("bindings") or []}
    grown = growth.mode()
    out = []
    for section, recs, kinds_field in (("families", fams, "executor_kinds"),
                                       ("criteria", crits, "computable_by")):
        for rec in recs:
            row = rows.get((section, rec["id"]))
            if row is None:
                # Запись КАНДИДАТА описывается посевом дерева, а не
                # пробной строкой: иначе копия несёт строку, которой
                # закон паритета законно недоволен.
                probe = [{"kind": k, "state": "нет", "lang": None,
                          "file": None, "symbol": None,
                          "note": PROBE_NOTE, "cr_key": None}
                         for k in rec.get(kinds_field) or []]
                # Режим клона: синтетическая семья получает строки
                # ОБРАЗЦА (символы существуют, гейт привязок их
                # находит) — покрытие у клона есть. Расширение остаётся
                # со строками «нет»: судья обязан сказать «исполнителя
                # нет», а не сломаться.
                clone = (growth.probe_executor_rows(grown, section,
                                                    rec["id"], rows)
                         if grown else None)
                row = {"section": section, "id": rec["id"],
                       "stand_verified": [],
                       "executors": (clone
                                     or _candidate_rows(section, rec["id"])
                                     or probe)}
            out.append(row)
    doc["bindings"] = out
    doc["catalog_version"] = docs["catalog.json"]["version"]
    doc["catalog_fingerprint"] = docs["catalog.json"]["fingerprint"]
    _write(dst / BINDINGS_FILE, doc)


def use_catalog(monkeypatch, path) -> Path:
    """Переключить дверь на каталог path: CREW_TEMPLATE_CATALOG (и
    CREW_COSMOS_BINDINGS, если копия несёт привязки; и CREW_BINDINGS_SEED,
    если копия несёт посев режима роста) + сброс кэша двери и читателя.
    Снятие переменных — monkeypatch; сброс кэша после теста —
    autouse-фикстура файла теста (forget())."""
    from vmp import bindings_build as bb
    from vmp import catalog_access as ca
    monkeypatch.setenv(ca.CATALOG_ENV, str(path))
    b = Path(path) / BINDINGS_FILE
    if b.is_file():
        monkeypatch.setenv(BINDINGS_ENV, str(b))
    else:
        monkeypatch.delenv(BINDINGS_ENV, raising=False)
    # Третий носитель — посев: судья, пересобирающий привязки сам, читает
    # его, и без двери он судил бы копию посевом дерева.
    s = Path(path) / SEED_FILE
    if s.is_file():
        monkeypatch.setenv(bb.SEED_ENV, str(s))
    else:
        monkeypatch.delenv(bb.SEED_ENV, raising=False)
    ca.forget()
    return Path(path)


def bump_mtime(path, seconds: int = 2) -> None:
    """mtime с запасом — файловые системы с грубой отметкой времени
    (образец tests/test_template_catalog_current.py)."""
    p = Path(path)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def count_reads(monkeypatch) -> list:
    """Список путей, прочитанных template_catalog.reader.read_json_file
    (разделы каталога у читателя и переходные носители у двери)."""
    from template_catalog import reader
    calls: list = []
    orig = reader.read_json_file

    def counting(path):
        calls.append(str(path))
        return orig(path)
    monkeypatch.setattr(reader, "read_json_file", counting)
    return calls


def count_validate(monkeypatch) -> list:
    """Список вызовов Catalog.validate (один элемент на вызов)."""
    calls: list = []
    orig = tc.Catalog.validate

    def counting(self, **kw):
        calls.append(id(self))
        return orig(self, **kw)
    monkeypatch.setattr(tc.Catalog, "validate", counting)
    return calls


def without_executors(tmp_path, monkeypatch, *families) -> dict:
    """Копия каталога, где у названных семей НЕТ ни одного исполнителя:
    строки привязок «нет» со словами по всем объявленным записью видам.

    Один стенд на обе стороны шва Ш-8 («код и перечень отказа рубежа
    выпуска»): производитель отказывает по этому каталогу, потребитель
    по нему же подаёт заявку. Второй такой же стенд был бы вторым
    местом истины — состояние клетки решает, выпускается ли пакет, и
    две его подготовки однажды разошлись бы.

    Правится КОПИЯ: живой каталог тесты не трогают. Все семьи снимаются
    в ОДНОЙ копии — вторая копия затёрла бы первую.

    Возврат: {семья: [виды, которых не исполняет ни один продукт]}.
    Ожидание считает ДВЕРЬ (`catalog_access.bindings_states`), а не
    вызывающий: вид, который исполняет другой продукт своим файлом
    привязок, — тоже исполнитель, и заявка его не просит (иначе стенд
    доказывал бы сам себя)."""
    folder = copy_catalog(tmp_path)
    записи = json.loads(
        (folder / "families.json").read_text(encoding="utf-8"))["families"]
    виды = {}
    for f in families:
        строка = next((r for r in записи if r.get("id") == f), None)
        if строка is None:
            raise AssertionError(
                f"семьи {f} нет в копии каталога: стенд подготовлен не о том")
        виды[f] = list(строка.get("executor_kinds") or [])
    path = folder / BINDINGS_FILE
    doc = json.loads(path.read_text(encoding="utf-8"))
    снято = set()
    for row in doc["bindings"]:
        if row.get("section") == "families" and row.get("id") in виды:
            row["executors"] = [
                {"kind": k, "state": "нет", "lang": None, "file": None,
                 "symbol": None, "cr_key": None,
                 "note": "стенд теста: исполнителя нет"}
                for k in виды[row["id"]]]
            row["stand_verified"] = []
            снято.add(row["id"])
    пропущены = set(виды) - снято
    if пропущены:
        raise AssertionError(
            f"строк привязок о семьях {sorted(пропущены)} в копии нет: "
            f"снимать исполнителей не у чего")
    path.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                 indent=1) + "\n").encode("utf-8"))
    use_catalog(monkeypatch, folder)
    from vmp import catalog_access as _ca
    состояния = (_ca.bindings_states().get("families") or {})
    return {f: [k for k in виды[f]
                if "есть" not in ((состояния.get(f) or {}).get(k) or {}).values()]
            for f in families}
