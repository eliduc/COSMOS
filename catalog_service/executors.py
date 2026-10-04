# -*- coding: utf-8 -*-
"""Экран «Исполнители по продуктам» и предпроверка сторожа привязок.

Служба НЕ считает состояние клетки сама: закон «есть > заявлен > нет»
живёт у читателя (`template_catalog.bindings.best_states`), тот же, что у
двери COSMOS и у экрана базы шаблонов. Третьей копии закона не бывает —
иначе три панели однажды покажут три разных ответа на один вопрос.

Здесь же — предпроверка B01 перед сборкой и выпуском: если после записи
дверь продукта остановится («стоп сторожа защищает выпуски»), служба
говорит это ДО записи и требует осознанного продолжения.
"""
from __future__ import annotations

from pathlib import Path

import template_catalog as tc
from template_catalog import bindings as tb
from template_catalog import writer
from template_catalog.export import dump_json
from template_catalog.manifest import compare_versions, is_stop

from . import identity
from .errors import ServiceError, catalog_unavailable, problem_json

#: Продукты и переменные их файлов привязок. Порядок закреплён: COSMOS
#: первым (свой файл), CDPM вторым (чужая зона).
PRODUCTS = ("COSMOS", "CDPM")
FRESHNESS_WORDS = {
    "match": "сошлось",
    "stale-draft": "черновик отстал",
    "stop": "остановка сторожа",
    "missing": "не объявлены",
}
#: Поля исполнителя, общие для всех видов; остальное — поля вида.
_COMMON_FIELDS = ("kind", "state", "lang", "file", "symbol", "note", "cr_key")
#: Ранг состояния — таблица ЧИТАТЕЛЯ, не своя: закон «есть > заявлен >
#: нет» живёт в одном месте (`template_catalog.bindings.STATE_RANK`),
#: служба им только пользуется — выбрать лучшее среди продуктов и лучшую
#: строку продукта в подробностях клетки.
_RANK = tb.STATE_RANK


def product_paths(settings) -> list[tuple[str, Path]]:
    return [("COSMOS", Path(settings.cosmos_bindings)),
            ("CDPM", Path(settings.cdpm_bindings))]


def read_docs(settings) -> tuple[list[tuple[str, dict]], dict]:
    """([(продукт, его файл привязок)], {продукт: отказ чтения}).

    Продукт здесь — та сторона, чью переменную читали (`CREW_COSMOS_
    BINDINGS` / `CREW_CDPM_BINDINGS`); имя внутри файла может и не
    совпасть с ней — адреса замечаний строятся по имени из файла.
    Чужой нечитаемый файл службу не останавливает: он назовётся в
    `problems` своей карточки."""
    pairs: list[tuple[str, dict]] = []
    failed: dict = {}
    for product, path in product_paths(settings):
        if not path.is_file():
            continue
        try:
            pairs.append((product, tb.load_bindings(path)))
        except tc.CatalogError as e:
            failed[product] = e
    return pairs, failed


def load_docs(settings) -> list[dict]:
    """Файлы привязок, которые прочитались."""
    return [doc for _product, doc in read_docs(settings)[0]]


def split_problems(cat, docs: list[dict]) -> tuple[dict, list]:
    """Замечания привязок, посчитанные ОДИН раз по всем файлам сразу, и
    разложенные по адресу: `bindings[<продукт>]…` — в карточку продукта,
    остальные (B04 по записи) — общие.

    Правило шага 4 (§2.1 п.2 его проекта): полнота по видам записи (B04)
    судится ЗОНАМИ ВСЕХ переданных файлов. Позвать `check_bindings` по
    одному файлу продукта значило бы объявить чужую зону несуществующей
    и получить полсотни ложных «вид никем не исполняется»."""
    by_product: dict = {}
    common: list = []
    for p in tb.check_bindings(cat, *docs):
        where = str(p.where)
        product = None
        if where.startswith("bindings[") and "]" in where:
            product = where[len("bindings["):where.index("]")]
        if product is not None:
            by_product.setdefault(product, []).append(problem_json(p))
        else:
            common.append(problem_json(p))
    return by_product, common


def freshness(cat, doc: dict | None) -> str:
    """Свежесть файла привязок относительно каталога: `match` |
    `stale-draft` | `stop` | `missing`. Закон — тот же `is_stop`, что у
    сторожа перед нажатием: разные ответы у службы и у продукта были бы
    двумя разными правдами."""
    if doc is None:
        return "missing"
    bv = str(doc.get("catalog_version"))
    try:
        d = compare_versions(cat.version(), bv)
    except ValueError:
        return "stop"
    if is_stop(d):
        return "stop"
    if d.level == "same":
        # «Сошлось» судит читатель (`bindings.matches_catalog`) — тем же
        # законом, что и ячейка свидетельства поддержки: экран и
        # свидетельство не вправе разойтись в том, для какого каталога
        # написан файл продукта.
        if tb.matches_catalog(cat, doc):
            return "match"
        return "stale-draft" if (d.draft_a or d.draft_b) else "stop"
    return "stale-draft"


def products_view(cat, settings, pairs=None, failed=None) -> tuple[list[dict], list]:
    """Карточки файлов привязок и ОБЩИЕ замечания.

    Карточка продукта: шапка файла, свежесть, замечания по его адресу.
    Нет файла — `found: false`, не отказ. Замечания считаются один раз по
    всем прочитанным файлам (`split_problems`): полнота по видам (B04) —
    суждение о зонах ВСЕХ продуктов сразу, и его место — общий перечень
    экрана, а не карточка одного продукта."""
    if pairs is None:
        pairs, failed = read_docs(settings)
    failed = failed or {}
    by_name, common = split_problems(cat, [d for _p, d in pairs])
    docs_by_product = dict(pairs)
    out = []
    for product, path in product_paths(settings):
        card = {"product": product, "file_name": path.name, "found": False,
                "product_version": None, "catalog_version": None,
                "catalog_fingerprint": None, "generated_at": None,
                "generated_from": None, "freshness": "missing",
                "freshness_words": FRESHNESS_WORDS["missing"],
                "problems": []}
        if not path.is_file():
            out.append(card)
            continue
        if product in failed:
            e = failed[product]
            card["found"] = True
            card["freshness"] = "stop"
            card["freshness_words"] = FRESHNESS_WORDS["stop"]
            card["problems"] = [{"where": f"bindings[{product}]",
                                 "code": "B01",
                                 "message": f"файл привязок не читается: "
                                            f"{type(e).__name__}",
                                 "severity": "error"}]
            out.append(card)
            continue
        doc = docs_by_product.get(product) or {}
        card.update({
            "found": True,
            "product_version": doc.get("product_version"),
            "catalog_version": doc.get("catalog_version"),
            "catalog_fingerprint": doc.get("catalog_fingerprint"),
            "generated_at": doc.get("generated_at"),
            "generated_from": doc.get("generated_from"),
        })
        card["freshness"] = freshness(cat, doc)
        card["freshness_words"] = FRESHNESS_WORDS[card["freshness"]]
        card["problems"] = by_name.get(str(doc.get("product") or "?"), [])
        out.append(card)
    return out, common


def _entry_fields(ex: dict) -> dict:
    return {k: v for k, v in ex.items() if k not in _COMMON_FIELDS}


def _detail_rows(docs: list[dict]) -> dict:
    """{(раздел, id, вид): {продукт: лучшая строка}} — подробности клетки.
    Лучшую строку продукта выбираем тем же рангом, что и `best_states`:
    у вида законно несколько строк одного продукта."""
    out: dict = {}
    for doc in docs:
        product = str(doc.get("product") or "?")
        for row in doc.get("bindings") or []:
            if not isinstance(row, dict):
                continue
            key0 = (str(row.get("section")), str(row.get("id")))
            for ex in row.get("executors") or []:
                if not isinstance(ex, dict) or not ex.get("kind"):
                    continue
                key = key0 + (str(ex["kind"]),)
                cell = out.setdefault(key, {})
                cur = cell.get(product)
                if cur is None or _RANK.get(str(ex.get("state")), -1) > \
                        _RANK.get(str(cur.get("state")), -1):
                    cell[product] = {
                        "state": ex.get("state"), "lang": ex.get("lang"),
                        "file": ex.get("file"), "symbol": ex.get("symbol"),
                        "note": ex.get("note"), "cr_key": ex.get("cr_key"),
                        "fields": _entry_fields(ex)}
    return out


def _stand_rows(docs: list[dict]) -> dict:
    out: dict = {}
    for doc in docs:
        product = str(doc.get("product") or "?")
        for row in doc.get("bindings") or []:
            if not isinstance(row, dict):
                continue
            rows = [r for r in (row.get("stand_verified") or [])
                    if isinstance(r, dict)]
            if rows:
                key = (str(row.get("section")), str(row.get("id")))
                out.setdefault(key, {})[product] = rows
    return out


def table(cat, settings, *, product: str = "all", section: str | None = None,
          record_id: str | None = None, kind: str | None = None,
          state: str | None = None) -> dict:
    """Матрица «запись × вид × продукт», карточки файлов и итоги.

    Фильтр `product` сужает ПОКАЗ (какие строки видны), а не суждение:
    замечания привязок считаются по всем прочитанным файлам сразу —
    иначе фильтр экрана менял бы правду о полноте (B04)."""
    pairs, failed = read_docs(settings)
    cards, common = products_view(cat, settings, pairs, failed)
    docs = [d for _p, d in pairs]
    if product and product != "all":
        docs = [d for d in docs if str(d.get("product")) == product]
    best = tb.best_states(cat, *docs)
    details = _detail_rows(docs)
    stands = _stand_rows(docs)
    kinds = list(cat.dictionaries().get("executor_kinds") or [])
    rows: list = []
    totals = {"cells": 0, "есть": 0, "заявлен": 0, "нет": 0,
              "не объявлено": 0}
    plan = []
    if section in (None, "families"):
        plan += [("families", r, "executor_kinds")
                 for r in cat.families_full()]
    if section in (None, "criteria"):
        plan += [("criteria", r, "computable_by")
                 for r in cat.criteria().values()]
    filtered = bool(kind) or bool(state) or bool(record_id)
    for sec, rec, kinds_field in plan:
        rid = str(rec.get("id"))
        if record_id and rid != record_id:
            continue
        declared = [str(k) for k in (rec.get(kinds_field) or [])]
        cells: dict = {}
        for k in declared:
            if kind and k != kind:
                continue
            by = dict(best.get(sec, {}).get(rid, {}).get(k, {}))
            b = best_word(by.values())
            word = b if b in ("есть", "заявлен", "нет") else "не объявлено"
            if state and word != state:
                continue
            totals["cells"] += 1
            totals[word] += 1
            cells[k] = {"best": b,
                        "by": _cell_by(by, details.get((sec, rid, k), {}))}
        if filtered and not cells:
            continue
        rows.append({"section": sec, "id": rid, "title": rec.get("title"),
                     "cells": cells,
                     "stand": stands.get((sec, rid), {})})
    return {"kinds": kinds, "products": cards, "rows": rows,
            "totals": totals, "problems": common}


def best_word(states) -> str | None:
    best, best_rank = None, -1
    for s in states:
        r = _RANK.get(str(s), -1)
        if r > best_rank:
            best, best_rank = str(s), r
    return best


def _cell_by(by_state: dict, by_detail: dict) -> dict:
    """Строки продуктов клетки: подробности из файла привязок; «заявлен»
    из заявки идёт под именем подавшего и подробностей не имеет.

    Состояние берётся у читателя (`best_states`), а подробности
    показываются, ТОЛЬКО если они относятся к тому же состоянию: имя
    подавшего заявку совпадает с именем продукта, и файл со строкой
    «нет» иначе подписал бы своим файлом и символом чужое «заявлен»."""
    out: dict = {}
    for who, state in by_state.items():
        row = dict(by_detail.get(who) or {})
        if str(row.get("state")) != str(state):
            row = {}
        row["state"] = state
        row.setdefault("lang", None)
        row.setdefault("file", None)
        row.setdefault("symbol", None)
        row.setdefault("note", None)
        row.setdefault("cr_key", None)
        row.setdefault("fields", {})
        out[who] = row
    return out


def record_states(cat, settings) -> dict:
    """Таблица «запись × вид × продукт» целиком — лучшее состояние клетки
    по обоим файлам привязок и заявкам «исполнитель заявлен»."""
    return tb.best_states(cat, *load_docs(settings))


def record_cells(cat, settings, section: str, rid: str) -> dict:
    """Клетки одной записи для её карточки: {вид: {продукт: строка}}."""
    docs = load_docs(settings)
    best = tb.best_states(cat, *docs)
    details = _detail_rows(docs)
    out: dict = {}
    for kind, by in (best.get(section, {}).get(rid, {}) or {}).items():
        out[kind] = _cell_by(by, details.get((section, rid, kind), {}))
    return out


def record_stand(cat, settings, section: str, rid: str) -> dict:
    """Стендовые подтверждения записи по продуктам плюс счётчики."""
    rows = _stand_rows(load_docs(settings)).get((section, rid), {})
    flat = [dict(r, product=p) for p, rs in rows.items() for r in rs]
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for r in flat:
        res = str(r.get("result"))
        if res in counts:
            counts[res] += 1
    return {**counts, "rows": flat}


def present_kinds(cat, settings) -> set:
    """Виды, у которых есть строка «есть» хотя бы у одной записи любого
    продукта, — ДИАГНОСТИКА экрана «Исполнители по продуктам».

    Решения по этой таблице не принимаются (П0 §3.5, сторона COSMOS
    §4.3): вид, написанный у ОДНОЙ записи, ничего не говорит об
    исполнимости ДРУГОЙ — «duty_series_limit умеет kotlin_knob» не
    значит, что ручка написана и для новой семьи. Кто спрашивает «а эта
    запись исполнима?», зовёт `supported_kinds_of_record` —
    свидетельство поддержки, и ничего другого."""
    best = tb.best_states(cat, *load_docs(settings))
    out: set = set()
    for by_id in best.values():
        for by_kind in by_id.values():
            for kind, by_product in by_kind.items():
                if any(str(s) == "есть" for s in by_product.values()):
                    out.add(str(kind))
    return out


# ────────────────────────────── свидетельство поддержки (K06)

#: Носитель свидетельства: файл рядом с разделами, ВНЕ отпечатка (как
#: `requests.json`). Пишет его только служба; читают все —
#: `Catalog.support()`.
SUPPORT_FILE = tc.reader.SUPPORT_FILE


def support_path(settings) -> Path:
    return Path(settings.catalog_dir) / SUPPORT_FILE


def support_table(cat, settings, *, approved_by: str | None = None,
                  approved_how: str | None = None) -> dict:
    """Таблица свидетельства по текущим привязкам — ОДНОЙ функцией
    читателя. Своей формулы у службы нет: её ответ обязан совпасть с
    тем, что посчитает CDPM по тому же файлу."""
    return tb.support_table(cat, dict(product_paths(settings)),
                            approved_by=approved_by,
                            approved_how=approved_how)


def support_cells_of_record(cat, settings, section: str, rid: str) -> list:
    """Ячейки свидетельства ЭТОЙ записи — ПЕРЕСЧЁТОМ по привязкам.

    Считает та же функция читателя (`support_table`), что пишет файл: у
    «исполнимо» один закон, и он судит и строку привязок, и стенд по
    ЭТОМУ виду, и свежесть файла привязок (`matches_catalog` — версия и
    отпечаток каталога). Первого попавшегося «есть» в файле мало: файл,
    написанный для ДРУГОГО каталога, знает о другой записи.

    Почему пересчёт, а не чтение `catalog/support.json`: файл — снимок
    последнего пересчёта, и между ним и вопросом привязки могли
    поменяться. Кто спрашивает «что записано», читает файл
    (`record_support`); кто судит «исполнимо ли сейчас» — считает."""
    doc = support_table(cat, settings)
    return [c for c in doc.get("cells") or ()
            if str(c.get("section")) == str(section)
            and str(c.get("record_id")) == str(rid)]


def supported_kinds(cells) -> set:
    """Виды ячеек со словом «есть» — подтверждённая поддержка.

    «Не проверено» поддержкой не считается ни здесь, ни у двери, ни у
    CDPM: неизвестность не заменяется успехом (общий закон 1 П0)."""
    return {str(c.get("kind")) for c in cells or ()
            if str(c.get("state")) == "есть"}


def supported_kinds_of_record(cat, settings, section: str, rid: str) -> set:
    """Виды, подтверждённые свидетельством У ЭТОЙ ЗАПИСИ, — один закон
    «исполнимо» службы (П0 §3.5, сторона COSMOS §4.3)."""
    return supported_kinds(support_cells_of_record(cat, settings, section,
                                                   rid))


def write_support(settings, doc: dict, folder=None) -> Path:
    """Записать свидетельство в `<каталог>/support.json`.

    Заявки и свидетельство — два файла вне отпечатка, и пишутся они
    одинаково: атомарной заменой в форме читателя (LF, сортировка
    ключей). Отпечаток и версия каталога этой записью не меняются.

    `folder` — иная папка: так выпуск кладёт НЕИЗМЕНЯЕМУЮ копию рядом с
    телом версии (`releases/<версия>/support.json`), чтобы «чем была
    подтверждена исполнимость на этом выпуске» читалось и через год."""
    path = (Path(folder) / SUPPORT_FILE) if folder is not None \
        else support_path(settings)
    writer.replace_json(path, dump_json(doc))
    return path


def refresh_support(store, journal, *, approved_by: str | None = None,
                    approved_how: str | None = None) -> dict:
    """Пересчитать свидетельство и положить файлом (ручка и выпуск).

    Право — то же, что у правки каталога: в режиме просмотра служба не
    пишет на диск ни байта, и свидетельство не исключение."""
    settings = store.settings
    identity.require(settings, "support-refresh")
    with store.writing():
        cat = store.catalog()
        doc = support_table(cat, settings, approved_by=approved_by,
                            approved_how=approved_how)
        write_support(settings, doc)
        store.forget()
    counts = support_counts(doc)
    journal.append(actor=settings.actor, op="support-refresh",
                   target=f"catalog/{SUPPORT_FILE}", outcome="ok",
                   words=f"свидетельство поддержки пересчитано на каталоге "
                         f"{cat.version()} {cat.fingerprint()[:12]}: "
                         + ", ".join(f"{k} — {v}" for k, v in counts.items()))
    return doc


def support_counts(doc: dict) -> dict:
    """Итоги таблицы по состояниям — тремя словами и числами."""
    out = {word: 0 for word in tb.SUPPORT_STATES}
    for cell in (doc or {}).get("cells") or ():
        word = str(cell.get("state"))
        if word in out:
            out[word] += 1
    return out


def read_support(cat) -> dict | None:
    """Свидетельство с диска; нечитаемый файл — 503 словами читателя.

    Испорченный носитель не вправе ронять службу пятисотым: он такой же
    файл каталога, как и разделы, и говорит о себе теми же словами."""
    try:
        return cat.support()
    except tc.CatalogError as e:
        raise catalog_unavailable(e, cat.path) from None


def record_support(cat, section: str, rid: str, doc=None) -> list:
    """Ячейки свидетельства ОДНОЙ записи — из файла, не пересчётом.

    Ручка записи обязана быть ПРЕДСТАВЛЕНИЕМ носителя: посчитать на
    лету значило бы завести второй источник правды, который разойдётся
    с файлом ровно в тот день, когда привязки поменяются, а
    свидетельство ещё нет."""
    doc = read_support(cat) if doc is None else doc
    return [c for c in (doc or {}).get("cells") or ()
            if str(c.get("section")) == str(section)
            and str(c.get("record_id")) == str(rid)]


def support_view(cat, section: str, rid: str) -> dict:
    """Представление свидетельства записи: ячейки файла и его шапка.

    Годность судит НЕ эта функция, а один закон читателя
    (`bindings.support_of`: версия схемы, версия и отпечаток каталога) —
    тот же, которым свидетельство читают дверь COSMOS и CDPM. Ручка лишь
    ПОКАЗЫВАЕТ: `stale: true` — свидетельство к этому каталогу не
    относится, ячейки видны, исполнимостью не считаются (для готовности
    «не проверено» = не поддержано); `why` называет причину словами."""
    doc = read_support(cat)
    judged = tb.support_of(cat, doc)
    stamp = (doc or {}).get("catalog") or {}
    computed = doc is not None
    stale = computed and not judged["valid"]
    return {"record": f"{section}/{rid}",
            "computed": computed,
            "computed_at": (doc or {}).get("computed_at"),
            "computed_by": (doc or {}).get("computed_by"),
            "schema_version": (doc or {}).get("schema_version"),
            "catalog": {"version": stamp.get("version"),
                        "fingerprint": stamp.get("fingerprint")},
            "stale": bool(stale),
            "why": judged["why"],
            "cells": record_support(cat, section, rid, doc),
            "words": _support_words(computed, judged, cat)}


def _support_words(computed: bool, judged: dict, cat) -> str:
    if not computed:
        return ("свидетельство поддержки не вычислено: пересчитайте его "
                "на рабочем месте владельца (POST /api/support/refresh) — "
                "до этого исполнимость записи не подтверждена")
    if not judged["valid"]:
        return (f"{judged['why']}: ячейки показаны, исполнимостью не "
                f"считаются")
    return (f"свидетельство поддержки на каталоге {cat.version()} "
            f"{cat.fingerprint()[:12]}")


def bindings_stop(cat, settings, new_version: str,
                  *, check_fingerprint: bool = False) -> ServiceError | None:
    """B01 ДО записи: остановится ли дверь продукта после неё.

    «Стоп сторожа защищает выпуски» (`manifest.is_stop`): старшая разница
    всегда; младшая на 0.x — когда хотя бы одна сторона выпуск. При
    выпуске проверяется и «одна версия — разные данные» (равная тройка,
    разные отпечатки)."""
    for product, path in product_paths(settings):
        if not path.is_file():
            continue
        try:
            doc = tb.load_bindings(path)
        except tc.CatalogError:
            continue
        bv = str(doc.get("catalog_version"))
        try:
            d = compare_versions(new_version, bv)
        except ValueError:
            continue
        stop = is_stop(d)
        why = "старшая разница версий" if d.level == "major" else \
            "младшая разница версий на 0.x с выпуском на одной из сторон"
        if not stop and check_fingerprint and d.level == "same" \
                and not (d.draft_a or d.draft_b) \
                and doc.get("catalog_fingerprint") != cat.fingerprint():
            stop, why = True, "одна версия каталога — разные данные"
        if stop:
            return ServiceError(
                "bindings-stop",
                f"после записи дверь {product} остановится по B01: привязки "
                f"{product} к {bv}, записывается {new_version} — {why}; "
                f"перегенерируйте привязки в том же коммите", 412,
                extra={"product": product, "catalog_version": bv,
                       "catalog_fingerprint": doc.get("catalog_fingerprint")})
    return None
