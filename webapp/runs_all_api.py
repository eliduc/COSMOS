# -*- coding: utf-8 -*-
"""Общий экран «Все прогоны»: прогоны обеих систем одним списком.

Основание — слово владельца 11.09.2026 («Нужно») и проект
`final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, разделы 4 (хозяин и
хранитель записи) и 12 (дерево прогонов). Ручка одна:
`GET /api/runs-all`.

Откуда берутся строки.

  * Из ИНДЕКСА ДЕРЕВА (`run-tree.json`, модуль `webapp/run_tree_api.py`)
    — все записи о прогоне, которые COSMOS знает: и свои частные
    прогоны, и записи панели CDPM, лежащие на этой же машине. У такой
    строки есть всё, что несёт голова записи: заказчик, площадка, кто
    объявил, дата объявления, состояние, системы-летописцы, золотой.
  * Из ХРАНИТЕЛЯ-ПАНЕЛИ (`GET <record_api>/api/runs`) — только те
    прогоны панели, которых в индексе ЕЩЁ НЕТ: панель заводит прогон
    раньше, чем у него появляется запись на этой машине. Такая строка
    честно неполна: панель отдаёт номер, заказчика, дату и состояние
    своими словами, а площадки, номера записи и признака золотого в её
    списке нет — эти клетки остаются пустыми, а не выдуманными.

Правила, которым подчинён модуль.

  * ТОЛЬКО ЧТЕНИЕ. Удаления и правок здесь нет: архивирование остаётся
    у каждой системы в своём списке (у COSMOS — вкладка «Прогоны», у
    панели — её список), и общий экран не заводит второго места, где
    прогон можно потерять.
  * ХРАНИТЕЛЬ НЕ ЛОМАЕТ ЭКРАН. Срок обращения короткий; панель
    недоступна — список собирается из своих записей, а сверху встаёт
    строка словами «панель недоступна: показаны только записи COSMOS».
    Молчать о недоступности нельзя: человек решил бы, что прогонов
    панели нет.
  * ОТБОР — ОДИН на обе ручки: `webapp/run_tree_api.filters_from` и
    `matches`. Второго разбора тех же слов здесь нет.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from urllib import request as _urlrequest

from vmp import run_record as rr

from . import run_tree_api as rta

#: Публичный адрес панели CDPM — им строятся ССЫЛКИ для человека. Нет
#: переменной — берётся хост адреса записи (машинные ручки и страница
#: панели стоят на одной службе).
PANEL_URL_ENV = "CDPM_PUBLIC_URL"

#: Хвост списка прогонов у хранителя и путь страницы прогона в панели.
#: `brief=1` (РВ-145): быстрый список — поля записи и состояние без
#: пересчёта готовности каждого прогона; полный список панели не меняется.
#: Панель без этой ручки параметр не знает и отдаёт полный список.
KEEPER_RUNS_TAIL = "/api/runs?brief=1"
PANEL_RUN_PATH = "#/runs/%s"

#: Хвост адреса записи: `<база>/api/runs/<прогон>/record` — из него
#: добывается база, когда переменная площадки не задана.
RECORD_MIDDLE = "/api/runs/"

#: Ожидание хранителя — короткое: экран не ждёт занятую панель.
TIMEOUT_S = 3.0

#: Срок годности собранного списка: экран открывают и перечитывают
#: часто, а обход записей и поход к панели стоят дороже строки.
CACHE_S = 20.0

#: Сколько строк отдаётся за раз и сколько можно попросить кнопкой
#: «показать ещё». Полный счёт строк отдаётся всегда — по нему человек
#: видит, сколько осталось.
DEFAULT_LIMIT = 200
MAX_LIMIT = 5000

#: Откуда строка: запись знает COSMOS либо прогон назвал только хранитель.
SOURCE_COSMOS = "cosmos"
SOURCE_KEEPER = "cdpm"

#: Параметры сверх отбора, которые ручка принимает.
EXTRA_PARAMS = ("limit", "offset", "refresh")

#: Код отказа: число страницы — не число.
CODE_PAGE = "page-not-a-number"


# ------------------------------------------------------------- мелочи
def _text(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int(name: str, value, default: int, low: int, high: int) -> int:
    raw = _text(value)
    if raw is None:
        return default
    try:
        got = int(raw)
    except ValueError:
        raise rta.RunTreeApiError(
            CODE_PAGE,
            ["«%s» ждёт целое число, а пришло «%s»" % (name, raw)],
            status=422) from None
    return max(low, min(high, got))


# ------------------------------------------------------------ адреса
def keeper_base(links: dict | None = None) -> str | None:
    """База ручек хранителя записи: переменная площадки
    (`CREW_CDPM_RECORD_API`), а без неё — адрес записи из пакета прогона,
    обрезанный до базы. Ни того ни другого — адреса нет, и это состояние
    называется словами, а не пустым списком.

    `links` — уже прочитанная связка прогонов (`_cosmos_links`): сборка
    списка читает её один раз и передаёт сюда, чтобы поиск адреса не
    обходил прогоны второй раз."""
    from . import run_gate
    base = _text(run_gate.record_api_base())
    if base:
        return base.rstrip("/")
    got = _cosmos_links() if links is None else links
    for link in (got or {}).values():
        api = _text(link.get("record_api"))
        if api and RECORD_MIDDLE in api:
            return api[:api.index(RECORD_MIDDLE)].rstrip("/")
    return None


def keeper_url(links: dict | None = None) -> str | None:
    """Адрес списка прогонов у хранителя: `<база>/api/runs?brief=1`."""
    base = keeper_base(links)
    return (base + KEEPER_RUNS_TAIL) if base else None


def panel_base(links: dict | None = None) -> str | None:
    """База СТРАНИЦ панели для человека: `CDPM_PUBLIC_URL`, иначе хост
    машинных ручек (панель и её страницы стоят на одной службе)."""
    return _text(os.environ.get(PANEL_URL_ENV)) or keeper_base(links)


def panel_run_url(run, base: str | None) -> str | None:
    """Ссылка на страницу прогона в панели CDPM; базы нет — нет и
    ссылки: выдумывать адрес чужой службы нельзя."""
    run = _text(run)
    if not base or not run:
        return None
    return "%s/%s" % (str(base).rstrip("/"), PANEL_RUN_PATH % run)


# ------------------------------------------------- свои номера прогонов
def _cosmos_links() -> dict:
    """Связка «номер записи → номера прогонов у обеих систем».

    Читается перечень прогонов COSMOS: у каждого пакета записаны номер
    записи (`record_id`), номер прогона панели, из которого пакет вышел
    (`source_run`), заказчик и адрес ручек записи. Хранилище выключено
    или запись не читается — связки просто нет: экран от этого не
    рушится, а клетка остаётся пустой."""
    out: dict = {}
    try:
        from . import cosmos_runs as cr
        if cr.storage_state() != "ready":
            return out
        order = cr.effective_order()
    except Exception:                                      # noqa: BLE001
        return out
    for rid in order:
        try:
            doc = cr.read_run(rid)
        except Exception:                                  # noqa: BLE001
            continue
        for pkg in (doc.get("packages") or []):
            if not isinstance(pkg, dict):
                continue
            record_id = _text(pkg.get("record_id"))
            if not record_id or record_id in out:
                continue
            out[record_id] = {"cosmos_run": _text(doc.get("run")),
                              "cdpm_run": _text(pkg.get("source_run")),
                              "customer": _text(pkg.get("customer")),
                              "record_api": _text(pkg.get("record_api"))}
    return out


def _holder_run(path) -> str | None:
    """Номер прогона COSMOS по маркеру держателя рабочего каталога
    (`package-runs/<отпечаток>/holder.json`): у частного прогона номера
    записи в манифесте пакета нет, и связка по нему не строится."""
    if not path:
        return None
    try:
        doc = json.loads((Path(path).parent / "holder.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return _text(doc.get("run")) if isinstance(doc, dict) else None


def _panel_run_of_path(path, panel_roots) -> str | None:
    """Номер прогона панели по месту записи: запись панели лежит в
    `<каталог прогонов панели>/<номер>/run-record.json`."""
    if not path:
        return None
    folder = Path(path).parent
    for root in panel_roots:
        try:
            if folder.parent == Path(root):
                return folder.name
        except (OSError, ValueError):
            continue
    return None


# -------------------------------------------------------------- строки
def _node_item(node: dict, links: dict, panel_roots,
               panel: str | None) -> dict:
    """Строка общего экрана из узла дерева: всё, что несёт голова
    записи, плюс номера прогонов у обеих систем и ссылки."""
    record_id = _text(node.get("run_id"))
    link = links.get(record_id) or {}
    path = node.get("path")
    cdpm_run = link.get("cdpm_run") or _panel_run_of_path(path, panel_roots)
    cosmos_run = link.get("cosmos_run") or _holder_run(path)
    return {"record_id": record_id,
            "cdpm_run": cdpm_run,
            "cosmos_run": cosmos_run,
            "customer": _text(node.get("airline")) or link.get("customer"),
            "contour": _text(node.get("contour")),
            "declared_by": rta.declared_by(node),
            "created_by": _text(node.get("created_by")),
            "created_at": _text(node.get("created_at")),
            "state": _text(node.get("state")),
            "systems": list(node.get("systems") or []),
            "golden": bool((node.get("golden") or {}).get("confirmed")),
            "root_sha256": _text(node.get("root_sha256")),
            "source": SOURCE_COSMOS,
            "cdpm_url": panel_run_url(cdpm_run, panel)}


def _keeper_item(row: dict, panel: str | None) -> dict:
    """Строка из списка хранителя: только то, что панель отдала.

    С волны 15 панель отдаёт в `GET /api/runs` поля записи о прогоне —
    номер записи, площадку, кто объявил, признак золотого (объект
    {confirmed, by, at, words} либо null) и номер прогона COSMOS из
    квитанции последней доставки. Носителя нет — панель шлёт null, и
    клетка остаётся ПУСТОЙ: заполнить её догадкой значило бы показать на
    общем экране то, чего ни одна система не говорила."""
    run = _text(row.get("run"))
    created_by = _text(row.get("created_by"))
    golden = row.get("golden") if isinstance(row.get("golden"), dict) else {}
    return {"record_id": _text(row.get("record_id")),
            "cdpm_run": run,
            "cosmos_run": _text(row.get("cosmos_run")),
            "customer": _text(row.get("customer")),
            "contour": _text(row.get("contour")),
            "declared_by": rta.declared_by({"created_by": created_by}) if created_by else None,
            "created_by": created_by,
            "created_at": _text(row.get("created_at")),
            "state": _text(row.get("state")),
            "systems": [rr.SYSTEM_CDPM],
            "golden": bool(golden.get("confirmed")),
            "root_sha256": None,
            "source": SOURCE_KEEPER,
            "title": _text(row.get("title")),
            "cdpm_url": panel_run_url(run, panel)}


def _row_for_filter(item: dict) -> dict:
    """Строка экрана в виде, который судит отбор (та же форма, что у
    узла дерева): правило отбора одно на обе половины списка."""
    return {"customer": item.get("customer"),
            "contour": item.get("contour"),
            "state": item.get("state"),
            "declared_by": item.get("declared_by"),
            "golden": bool(item.get("golden")),
            "created_at": item.get("created_at"),
            "root": item.get("root_sha256"),
            "search": [item.get("record_id"), item.get("cdpm_run"),
                       item.get("cosmos_run"), item.get("title")]}


def _order_key(item: dict):
    """Порядок экрана: новые сверху. Без даты прогон уходит вниз — он
    объявлен раньше, чем часы научились ставить пояс."""
    return (str(item.get("created_at") or ""),
            str(item.get("cdpm_run") or item.get("record_id") or ""))


# ------------------------------------------------------------ хранитель
def fetch_keeper(url: str, timeout: float = TIMEOUT_S) -> dict:
    """Список прогонов у хранителя: `GET <база>/api/runs`.

    Только чтение и короткий срок. Ни сеть, ни отказ панели исключением
    наружу не выходят: возврат несёт `ok`, строки и слова беды."""
    try:
        req = _urlrequest.Request(str(url), method="GET")
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "runs": [],
                "words": "панель недоступна (%s): %s" % (url, str(e)[:200])}
    rows = doc.get("runs") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        return {"ok": False, "runs": [],
                "words": "панель ответила не перечнем прогонов (%s)" % url}
    return {"ok": True, "runs": [r for r in rows if isinstance(r, dict)],
            "words": None}


# ------------------------------------------------------------ сборка
_LOCK = threading.RLock()
#: Последний собранный список: `{"at": часы хода, "doc": строки и слова}`.
_CACHE: dict = {"at": None, "doc": None}


def forget() -> None:
    """Забыть собранный список (стенды: у каждого теста свой каталог
    данных, и срок годности не имеет права протянуться из соседнего)."""
    with _LOCK:
        _CACHE.update(at=None, doc=None)


def collect(timeout: float = TIMEOUT_S) -> dict:
    """Строки обеих систем и слова: `{items, words, keeper}`.

    Отбора здесь нет — он идёт поверх собранного: один поход к панели
    обслуживает любой отбор, и переключение пилюль не гоняет сеть."""
    # Связка прогонов и адреса читаются ОДИН раз на сборку: иначе поиск
    # адреса панели обошёл бы прогоны по разу на каждую строку.
    links = _cosmos_links()
    url, panel = keeper_url(links), panel_base(links)
    panel_roots = [Path(r["path"]) for r in rta.roots()
                   if r.get("kind") != "package-runs"]
    try:
        from vmp import run_tree as rt
        nodes = rt.nodes(rta.tree())
    except Exception as e:                                 # noqa: BLE001
        nodes = []
        broken = "индекс дерева не прочитан: %s" % str(e)[:200]
    else:
        broken = None
    items = [_node_item(n, links, panel_roots, panel) for n in nodes]
    known = {str(i["cdpm_run"]) for i in items if i.get("cdpm_run")}
    known |= {str(i["record_id"]) for i in items if i.get("record_id")}
    words = [broken] if broken else []

    keeper = {"reachable": False, "url": url, "panel": panel, "words": None}
    if not url:
        keeper["words"] = ("адрес панели не объявлен (переменная площадки "
                           "%s): показаны только записи COSMOS"
                           % run_gate_env())
        words.append(keeper["words"])
    else:
        got = fetch_keeper(url, timeout=timeout)
        keeper["reachable"] = bool(got.get("ok"))
        if got.get("ok"):
            for row in got["runs"]:
                if _text(row.get("run")) in known:
                    continue
                items.append(_keeper_item(row, panel))
        else:
            keeper["words"] = ("панель недоступна: показаны только записи "
                               "COSMOS — %s" % got.get("words"))
            words.append(keeper["words"])
    items.sort(key=_order_key, reverse=True)
    return {"items": items, "words": words, "keeper": keeper}


def run_gate_env() -> str:
    """Имя переменной площадки с адресом ручек записи — одно место
    правды (`webapp/run_gate.py`)."""
    from . import run_gate
    return run_gate.RECORD_API_ENV


def collected(refresh: bool = False, timeout: float = TIMEOUT_S) -> dict:
    """Собранный список из срока годности либо заново."""
    with _LOCK:
        at, doc = _CACHE.get("at"), _CACHE.get("doc")
    fresh = (not refresh and doc is not None and at is not None
             and (time.monotonic() - at) < CACHE_S)
    if fresh:
        return doc
    doc = collect(timeout=timeout)
    with _LOCK:
        _CACHE.update(at=time.monotonic(), doc=doc)
    return doc


def view(params=None, timeout: float = TIMEOUT_S) -> dict:
    """Тело ручки `GET /api/runs-all`.

    `{items, total, words, keeper}`: `total` — сколько строк подошло под
    отбор ВСЕГО, `items` — страница по `limit`/`offset` (кнопка
    «показать ещё»). Отбор — общий с ручкой дерева."""
    params = params or {}
    rta.check_params(params.keys(), EXTRA_PARAMS)
    flt = rta.filters_from(params)
    limit = _int("limit", params.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _int("offset", params.get("offset"), 0, 0, 10 ** 9)
    refresh = _text(params.get("refresh")) not in (None, "0", "нет", "no",
                                                   "false")
    doc = collected(refresh=refresh, timeout=timeout)
    chosen = [i for i in doc["items"] if rta.matches(_row_for_filter(i), flt)]
    return {"items": chosen[offset:offset + limit],
            "total": len(chosen),
            "offset": offset,
            "limit": limit,
            "words": list(doc["words"]),
            "keeper": dict(doc["keeper"])}
