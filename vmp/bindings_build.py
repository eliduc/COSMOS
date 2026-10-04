# -*- coding: utf-8 -*-
"""Генератор привязок COSMOS к записям каталога шаблонов —
`vmp/bindings.json` (шаг 4 каталога, §1.4 проекта
final-report/KATALOG-SHAG-4-PROEKT-CLD.md; форма — `$defs/bindings_file`
схемы читателя). Зеркало генератора CDPM `cdpm/bindings_build.py`.

Входы — три и только три:

* курируемый посев `vmp/bindings_seed.json` (таблица §1.3 проекта:
  какая запись какими символами кода исполняется);
* каталог двери (`vmp.catalog_access.current()`) — он решает, какие
  записи существуют и какие виды они объявили; версия и отпечаток
  каталога попадают в шапку файла;
* таблицы решателя `vmp/kotlin_tables.py` — у ручек с символами
  `RunThresholds.KNOBS` и `RunThresholds.SIT_KNOBS` порог, единица,
  отношение и признак семейства ключей берутся ИЗ Kotlin (в посеве их
  нет: два места для одного числа рано или поздно разойдутся), а у
  метрик — ограничение, задачи, отказ и приближение дословно из
  `TaskModes.METRIC_EXECUTORS`.

Плюс `vmp/stand_results.json` (§5.5), если он есть: его строки
становятся полем `stand_verified` записей. Место у него одно и то же и
для сборки, и для сверки — иначе `--check` расходился бы с `--out` на
ровном месте. Устаревшие результаты (другой отпечаток каталога, изменённый
код стенда либо НЕПОЛНАЯ карта `code`) — отказ, а не тихое подмешивание:
свежесть судит один закон — `vmp.stand_tasks.stale_reasons`, — и он
проверяет карту в обе стороны, чтобы файл результатов не мог объявить
себя свежим умолчанием.

Файл детерминирован: одинаковые входы дают байт-в-байт тот же файл
(LF, отступ 1, ключи по алфавиту — форма `template_catalog.export`).
Порядок строк внутри записи задан ключом §1.1 (позиция вида в словаре,
ранг состояния, символ, ключевое поле вида): сортировка «по символу»
была бы либо отказом типов (у строк «нет» символа нет), либо
недетерминизмом (три ручки `duty_limits` делят символ `KNOBS`).

    python -m vmp.bindings_build --out vmp/bindings.json
        [--commit <hex>] [--generated-at <ISO 8601 с поясом>]
        [--stand-results vmp/stand_results.json]
    python -m vmp.bindings_build --check vmp/bindings.json

`--check` собирает в памяти и сравнивает с файлом БЕЗ изменчивых полей
(`generated_at`, `generated_from` шапки и `at`/`commit` строк стенда) —
тот же канон, что у `canon()`/`fingerprint()`, которыми живут сводка
двери, сторож дрейфа реестра и гейт. Коды возврата: 0 — совпало, 1 —
расхождения словами, 2 — отказ.

Посев берётся с обычного места (`vmp/bindings_seed.json`), из ключа
`--seed` либо из двери `CREW_BINDINGS_SEED`. Дверь нужна тому, кто
судит КОПИЮ каталога: посев дерева не знает записей, которых в дереве
ещё нет, и отказ «запись каталога в посеве не названа» приходил бы по
построению стенда (руководство 13.14).

Инструмент, не читатель: импорты из `vmp` ему разрешены.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from template_catalog.export import dump_json                       # noqa: E402
from template_catalog.requests import CR_KEY_RE                     # noqa: E402
from vmp import bindings_gate as gate                               # noqa: E402
from vmp.version import PRODUCT, PRODUCT_VERSION                    # noqa: E402

#: Курируемая таблица «запись → исполнители» (правится руками).
SEED_FILE = REPO / "vmp" / "bindings_seed.json"
#: Дверь посева: путь к посеву вместо дерева. Каталог подменяется
#: переменной CREW_TEMPLATE_CATALOG, привязки — CREW_COSMOS_BINDINGS, а
#: посев до 10.09.2026 двери не имел: стенд, судивший КОПИЮ каталога,
#: пересобирал привязки из посева ДЕРЕВА и отказывал «запись каталога в
#: посеве не названа» на любой записи, которой в дереве ещё нет. Третий
#: носитель одного знания без своей двери — то самое допущение «каталог
#: не растёт»: подменить два носителя из трёх нельзя.
SEED_ENV = "CREW_BINDINGS_SEED"
#: Умолчания команд.
OUT_FILE = REPO / "vmp" / "bindings.json"
STAND_RESULTS_FILE = REPO / "vmp" / "stand_results.json"
CDPM_BINDINGS_FILE = REPO / "cdpm" / "bindings.json"

#: Виды исполнителей на Kotlin (язык строки «есть» выводится по виду:
#: два места для одного факта разошлись бы).
KOTLIN_KINDS = gate.KOTLIN_KINDS
#: Символы таблиц ручек, поля которых заполняются из Kotlin.
KNOB_TABLES = ("RunThresholds.KNOBS", "RunThresholds.SIT_KNOBS")
#: Поля ручки, которые у табличных символов В ПОСЕВЕ НЕ ПИШУТСЯ.
KNOB_FROM_KOTLIN = ("threshold", "unit", "relation", "keyed", "accepts")

#: Ключевое поле вида — четвёртая часть ключа порядка строк и вторая
#: часть пары, повтор которой генератор отвергает.
KEY_FIELD = {"kotlin_constraint": "constraint", "kotlin_knob": "param",
             "kotlin_form": "form", "kotlin_logic_form": "form",
             "kotlin_metric": "metric", "check_template": "template"}
#: Ранг состояния в ключе порядка («есть» раньше «нет»).
STATE_ORDER = {"есть": 0, "заявлен": 1, "нет": 2}

#: Поля шапки, сравниваемые `--check`.
HEADER = ("product", "product_version", "catalog_version",
          "catalog_fingerprint", "executor_kinds")
#: Поля, которые от запуска к запуску меняются законно и в канон не
#: входят: по ним файл не «отстаёт», по ним он лишь датирован.
VOLATILE = ("generated_at", "generated_from")
#: То же у строк стендовых подтверждений.
VOLATILE_STAND = ("at", "commit")

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")


class BindingsBuildError(Exception):
    """Честный отказ генератора: причина всегда названа словами, файл
    при отказе не пишется."""


# --------------------------------------------------------------- посев
def seed_path() -> Path:
    """Путь посева: переменная двери `CREW_BINDINGS_SEED`, иначе посев
    дерева. Пустое значение переменной — как её отсутствие."""
    return Path(os.environ.get(SEED_ENV) or SEED_FILE)


def load_seed(path=None) -> dict:
    """Посев с диска; кривая форма — отказ словами. Без явного пути
    берётся `seed_path()` — посев двери либо посев дерева."""
    p = Path(path) if path else seed_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise BindingsBuildError(f"посев {p} не читается: {e}") from None
    except ValueError as e:
        raise BindingsBuildError(f"посев {p} не JSON: {e}") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), dict):
        raise BindingsBuildError(
            f"посев {p}: нужен объект с разделом records "
            f"{{\"<раздел>/<id>\": [строки…]}}")
    for addr, rows in doc["records"].items():
        if not isinstance(rows, list) or not rows or \
                not all(isinstance(r, dict) for r in rows):
            raise BindingsBuildError(
                f"посев {p}: у записи {addr} строки не список объектов")
    return doc


def _lang_of(kind: str) -> str:
    return "kotlin" if kind in KOTLIN_KINDS else "python"


def _key_value(ex: dict) -> str:
    field = KEY_FIELD.get(str(ex.get("kind")))
    value = ex.get(field) if field else None
    return "" if value is None else str(value)


def _sort_key(ex: dict, kinds_order: list) -> tuple:
    kind = str(ex.get("kind"))
    pos = kinds_order.index(kind) if kind in kinds_order else len(kinds_order)
    return (pos, STATE_ORDER.get(str(ex.get("state")), 9),
            str(ex.get("symbol") or ""), _key_value(ex))


def _knob_fields(row: dict, addr: str, tbl) -> dict:
    """Поля ручки: у табличных символов — из Kotlin, у именованных
    констант — из посева (их же сверяет гейт)."""
    symbol, param = str(row.get("symbol") or ""), str(row.get("param") or "")
    if not param:
        raise BindingsBuildError(f"{addr}: у ручки не назван параметр")
    if symbol in KNOB_TABLES:
        extra = [f for f in KNOB_FROM_KOTLIN if f in row]
        if extra:
            raise BindingsBuildError(
                f"{addr}: у ручки {param!r} с символом {symbol} поля "
                f"{extra} в посеве не пишутся — их берёт из Kotlin "
                f"генератор (иначе одно число живёт в двух местах)")
        known = (tbl.knobs() if symbol.endswith("KNOBS")
                 and not symbol.endswith("SIT_KNOBS") else None)
        if symbol.endswith("SIT_KNOBS"):
            sit = list(tbl.sit_knobs().values())
            if param != tbl.sit_param() or not sit:
                raise BindingsBuildError(
                    f"{addr}: параметр стыковки решателя — "
                    f"{tbl.sit_param()!r}, а в посеве {param!r}")
            return {"threshold": sorted(v["threshold"] for v in sit),
                    "unit": sit[0]["unit"], "relation": sit[0]["relation"],
                    "keyed": any(v["keyed"] for v in sit),
                    "accepts": sorted({a for v in sit for a in v["accepts"]})}
        knob = (known or {}).get(param)
        if knob is None:
            raise BindingsBuildError(
                f"{addr}: ручки {param!r} нет в таблице RunThresholds.KNOBS "
                f"решателя — привязка обещает порог, которого решатель не "
                f"знает")
        return {"threshold": knob["threshold"], "unit": knob["unit"],
                "relation": knob["relation"], "keyed": bool(knob["keyed"]),
                "accepts": sorted(knob["accepts"])}
    missing = [f for f in ("threshold", "unit", "relation") if not row.get(f)]
    if missing:
        raise BindingsBuildError(
            f"{addr}: у ручки {param!r} с символом {symbol} посев не назвал "
            f"поля {missing} (символ не таблица — из Kotlin их не взять)")
    out = {"threshold": row["threshold"], "unit": row["unit"],
           "relation": row["relation"], "keyed": bool(row.get("keyed")),
           "accepts": list(row.get("accepts") or [])}
    return out


def _metric_fields(row: dict, addr: str, tbl) -> dict:
    """Поля метрики — из `TaskModes.METRIC_EXECUTORS` дословно; посев
    даёт приближение только там, где у Kotlin его нет."""
    metric = str(row.get("metric") or "")
    known = tbl.metrics().get(metric)
    if known is None:
        raise BindingsBuildError(
            f"{addr}: метрики {metric!r} нет в TaskModes.METRIC_EXECUTORS — "
            f"привязка обещает то, чего решатель не считает")
    seed_appr = row.get("approximation")
    if known["approximation"] and seed_appr and \
            seed_appr != known["approximation"]:
        raise BindingsBuildError(
            f"{addr}: приближение метрики {metric!r} в посеве не дословно "
            f"тексту METRIC_EXECUTORS")
    out = {"metric": metric,
           "approximation": known["approximation"] or seed_appr or None,
           "refusal": known["refusal"]}
    if str(row.get("state")) == "есть":
        out["constraint"] = known["constraint"]
        out["tasks"] = list(known["tasks"])
    return out


def _executor(row: dict, addr: str, tbl) -> dict:
    """Одна строка файла привязок из строки посева."""
    kind, state = str(row.get("kind") or ""), str(row.get("state") or "")
    if not kind or not state:
        raise BindingsBuildError(f"{addr}: у строки посева нет вида или "
                                 f"состояния")
    ex = {"kind": kind, "state": state, "lang": None, "file": None,
          "symbol": None, "note": row.get("note"),
          "cr_key": row.get("cr_key")}
    if state == "есть":
        file, symbol = row.get("file"), row.get("symbol")
        if not (isinstance(file, str) and file and isinstance(symbol, str)
                and symbol):
            raise BindingsBuildError(
                f"{addr}: у строки «есть» вида {kind} нет файла или символа")
        ex.update(lang=_lang_of(kind), file=file, symbol=symbol)
    if kind == "kotlin_constraint":
        ex["constraint"] = row.get("constraint")
        ex["label"] = row.get("label")
    elif kind == "kotlin_knob":
        ex["param"] = row.get("param")
        if state == "есть":
            ex.update(_knob_fields(row, addr, tbl))
    elif kind in ("kotlin_form", "kotlin_logic_form"):
        ex["form"] = row.get("form")
        ex["params"] = list(row.get("params") or [])
        if kind == "kotlin_logic_form":
            ex["constraint_id_rule"] = row.get("constraint_id_rule")
            ex["param_set"] = row.get("param_set")
    elif kind == "kotlin_metric":
        ex.update(_metric_fields(row, addr, tbl))
    elif kind == "check_template":
        ex["template"] = row.get("template")
    else:
        ex["params"] = list(row.get("params") or [])
    if state != "есть" and not ex.get("note"):
        if kind == "kotlin_metric" and ex.get("refusal"):
            # Отказ решателя — дословно его словами: пересказ разошёлся бы.
            ex["note"] = ex["refusal"]
        else:
            raise BindingsBuildError(
                f"{addr}: у строки «{state}» вида {kind} нет заметки — "
                f"молчаливого отказа не бывает")
    if state == "заявлен" and not CR_KEY_RE.match(str(ex.get("cr_key") or "")):
        # «Заявлен» значит «исполнитель обещан строкой очереди изменений»:
        # без ключа CR обещание не проверить — клетка красила бы экран
        # словом «заявлен» без единого следа работы. Сегодня это ловил
        # только читатель (B03) уже на готовом файле; отказ переносится к
        # генератору — там, где строка рождается (шаг 6, §5.4).
        raise BindingsBuildError(
            f"{addr}: у строки «заявлен» вида {kind} нет ключа CR по "
            f"образцу cr<8 знаков hex> (дано {ex.get('cr_key')!r}) — "
            f"ключ считается законом заявки (template_catalog.requests."
            f"cr_key_of) и записывается сверкой заявок каталога")
    for field, value in list(ex.items()):
        if field in ("note", "cr_key", "lang", "file", "symbol", "label",
                     "approximation", "refusal", "param_set", "keyed"):
            continue
        if value is None or (field in KEY_FIELD.values() and value == ""):
            raise BindingsBuildError(
                f"{addr}: у строки вида {kind} пусто поле {field}")
    return ex


# ------------------------------------------------------ стенд (§5.5)
def default_stand_results() -> dict | None:
    """Результаты стенда с обычного места (`vmp/stand_results.json`), если
    они там лежат. Место одно, как и у посева: иначе сборка и сверка
    брали бы разные входы, и `--check` расходился бы с `--out` на ровном
    месте. Файла нет — None (стендовых подтверждений в файле не будет)."""
    return (load_stand_results(STAND_RESULTS_FILE)
            if STAND_RESULTS_FILE.is_file() else None)


def load_stand_results(path) -> dict:
    """Файл результатов стенда; нет файла — отказ словами (ключ дан —
    значит результаты обещаны)."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise BindingsBuildError(f"результаты стенда {p} не читаются: "
                                 f"{e}") from None
    except ValueError as e:
        raise BindingsBuildError(f"результаты стенда {p} не JSON: "
                                 f"{e}") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        raise BindingsBuildError(
            f"результаты стенда {p}: нужен объект со списком results")
    return doc


def _stand_stale(results: dict, catalog) -> list:
    """Почему файл результатов сливать нельзя — словами.

    Закон свежести ОДИН и живёт у стенда (`vmp.stand_tasks.stale_reasons`):
    он судит карту `code` в обе стороны — и «названные отпечатки сошлись
    с диском», и «названы ровно те файлы, которыми стенд свеж». Прежний
    суд здесь сверял только то, что файл САМ о себе сказал, поэтому
    укороченная карта (в пределе пустая) проходила за свежую, и чужие
    исходы уходили бы в `stand_verified` без единого прогона исполнителя.
    Стенда нет в дереве (обрезанная поставка) — сверять нечем, и это
    отказ, а не молчаливое доверие файлу."""
    try:
        from vmp.stand_tasks import stale_reasons
    except ImportError as e:                                  # pragma: no cover
        raise BindingsBuildError(
            f"результаты стенда есть, а стенда (vmp/stand_tasks.py) нет: "
            f"{e} — свежесть результатов подтвердить нечем") from None
    return list(stale_reasons(results, catalog, REPO))


def _stand_by_record(results: dict | None, catalog) -> dict:
    """{(раздел, id): [строки stand_verified]} из файла результатов;
    устаревший файл — отказ, а не тихое подмешивание."""
    if results is None:
        return {}
    stale = _stand_stale(results, catalog)
    if stale:
        raise BindingsBuildError(
            "результаты стенда сняты не на этом дереве — перепрогоните "
            "стенд (python -m vmp.stand_tasks --write-results "
            "vmp/stand_results.json): " + "; ".join(stale))
    at, commit = results.get("at"), results.get("commit")
    out: dict = {}
    for row in results["results"]:
        if not isinstance(row, dict):
            continue
        section, _, rid = str(row.get("record") or "").partition("/")
        out.setdefault((section, rid), []).append(
            {"task": row.get("task"), "executor_kind": row.get("executor_kind"),
             "result": row.get("result"), "at": row.get("at") or at,
             "commit": row.get("commit") or commit,
             "note": row.get("note")})
    for rows in out.values():
        rows.sort(key=lambda r: (str(r["task"]), str(r["executor_kind"])))
    return out


# -------------------------------------------------------------- сборка
def _schema_allows(field: str) -> bool:
    """Допускает ли закрытая схема привязок читателя (`$defs/bindings_file`)
    поле шапки `field`. Схема не читается — считается, что нет: файл не
    по схеме хуже файла без необязательного поля."""
    try:
        from template_catalog.validate import _load_schema
        schema = _load_schema() or {}
        return field in (schema.get("$defs", {}).get("bindings_file", {})
                         .get("properties", {}))
    except Exception:                                         # noqa: BLE001
        return False


def _records(catalog) -> list:
    """Записи каталога со статусом «есть»: семьи по id, затем критерии."""
    return ([("families", r) for r in catalog.families_full()]
            + [("criteria", r) for r in catalog.criteria().values()])


#: Папка кандидатов рядом с каталогом: `catalog/candidates/<раздел>/<id>.json`.
CANDIDATES_DIR = "candidates"


def candidate_claims(catalog) -> dict:
    """{адрес записи: имена параметров, которые объявляет кандидат} —
    по файлам-кандидатам, лежащим РЯДОМ с судимым каталогом.

    Решение 0.10 пакета паритета (08.09.2026): у правки класса «рост
    таблиц решателя» посев привязок опережает живой каталог — новая ручка
    решателя уже названа строкой посева, а параметр записи появится
    только после сборки службой. Пока правку несёт кандидат, такая строка
    ЗАКОННА: генератор пропускает её словами, и цепочка на живом каталоге
    клона зелена по существу, а не по перечню ожидаемых красных. После
    сборки кандидата нет, запись имя называет — строка входит в файл
    сама, и править перечни рукой не нужно.

    Записи без кандидата это не касается: там отказ, как и был."""
    root = Path(getattr(catalog, "path", "") or "") / CANDIDATES_DIR
    out: dict = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue                      # форму кандидата судит свой гейт
        if not isinstance(doc, dict):
            continue
        section, rid = doc.get("section"), doc.get("id")
        if not section or not rid:
            continue
        rec = doc.get("record") if isinstance(doc.get("record"), dict) else {}
        names = {str(x.get("name")) for x in (rec.get("params") or [])
                 if isinstance(x, dict) and x.get("name")}
        out[f"{section}/{rid}"] = names
    return out


def build(catalog, *, commit: str, generated_at: str,
          stand_results: dict | None = None, seed: dict | None = None) -> dict:
    """Документ привязок COSMOS по каталогу — чистая сборка, ничего не
    пишет и ничего не печатает. Отказы словами: посев не по форме;
    запись посева, которой нет в каталоге и нет в перечне ожидающих;
    запись каталога, которой нет в посеве; объявленный вид без строки;
    повтор пары «вид + ключ»; пустое поле вида; неизвестная ручка или
    метрика решателя; устаревшие результаты стенда."""
    if not _COMMIT_RE.match(commit or ""):
        raise BindingsBuildError(
            f"коммит {commit!r} не похож на хеш git (7–40 знаков 0-9a-f)")
    if not _DATETIME_RE.match(generated_at or ""):
        raise BindingsBuildError(
            f"момент {generated_at!r} не ISO 8601 с поясом "
            f"(2026-09-08T12:00:00+03:00)")
    seed = seed if seed is not None else load_seed()
    tbl = gate.tables()
    try:
        dicts = catalog.dictionaries() or {}
    except Exception as e:                                    # noqa: BLE001
        raise BindingsBuildError(f"каталог не отдал словари: {e}") from None
    kinds_order = [str(k) for k in (dicts.get("executor_kinds") or [])]
    if not kinds_order:
        raise BindingsBuildError(
            "у каталога нет словаря executor_kinds — порядок строк и зона "
            "продукта неоткуда взять")
    pending = {str(a) for a in (seed.get("pending_records") or [])}
    stand = _stand_by_record(stand_results, catalog)
    claims = candidate_claims(catalog)
    known_names = set(dicts.get("unbound_params") or {})

    rows, seen_addr = [], set()
    for section, rec in _records(catalog):
        rid = str(rec.get("id"))
        addr = f"{section}/{rid}"
        seen_addr.add(addr)
        seed_rows = seed["records"].get(addr)
        if not seed_rows:
            raise BindingsBuildError(
                f"запись каталога {addr} в посеве не названа: у неё не будет "
                f"ни одной строки привязок, и вид записи закрыть нечем — "
                f"допишите её в {SEED_FILE.name}")
        names = known_names | {str(p.get("name"))
                               for p in (rec.get("params") or [])
                               if isinstance(p, dict)}
        claimed = claims.get(addr) or set()
        executors, pairs, awaited = [], set(), set()
        for row in seed_rows:
            if str(row.get("kind")) == "kotlin_knob":
                param = str(row.get("param") or "")
                # Имя, которого нет и у решателя, — не «строка впереди
                # каталога», а ошибка посева: её называет _executor
                # словами о таблице RunThresholds.KNOBS.
                solver_knows = param in tbl.knobs() or param == tbl.sit_param()
                if param and solver_knows and param not in names:
                    if param in claimed:
                        # Строка опережает живой каталог, но её параметр
                        # объявляет кандидат рядом: пропуск словами.
                        awaited.add("kotlin_knob")
                        continue
                    raise BindingsBuildError(
                        f"{addr}: параметра {param!r} строки ручки запись "
                        f"каталога {catalog.version()} не называет и в "
                        f"unbound_params его нет — либо имя переименовано, "
                        f"либо его обязан объявить кандидат "
                        f"{CANDIDATES_DIR}/{addr}.json")
            ex = _executor(row, addr, tbl)
            pair = (str(ex["kind"]), str(ex.get("symbol") or ""),
                    _key_value(ex))
            if pair in pairs:
                raise BindingsBuildError(
                    f"{addr}: пара «вид {pair[0]} + {pair[1] or '—'} + "
                    f"{pair[2] or '—'}» встречается дважды — одна строка на "
                    f"пару")
            pairs.add(pair)
            executors.append(ex)
        declared = gate.declared_kinds(rec, section)
        have = {str(ex["kind"]) for ex in executors} | awaited
        silent = [k for k in declared if k not in have]
        if silent:
            raise BindingsBuildError(
                f"{addr}: запись объявила виды {silent}, а строк по ним в "
                f"посеве нет — вид записи остался бы без ответа продукта")
        executors.sort(key=lambda ex: _sort_key(ex, kinds_order))
        rows.append({"section": section, "id": rid, "executors": executors,
                     "stand_verified": stand.get((section, rid), [])})

    ahead = sorted(set(seed["records"]) - seen_addr)
    unknown = [a for a in ahead if a not in pending and a not in claims]
    if unknown:
        raise BindingsBuildError(
            f"посев называет записи, которых в каталоге {catalog.version()} "
            f"нет: {unknown} — либо запись переименована, либо её объявляет "
            f"кандидат {CANDIDATES_DIR}/<раздел>/<id>.json, либо её надо "
            f"объявить ожидающей (pending_records)")

    doc = {"product": PRODUCT, "product_version": PRODUCT_VERSION,
           "catalog_version": str(catalog.version()),
           "catalog_fingerprint": str(catalog.fingerprint()),
           "generated_at": generated_at, "generated_from": commit,
           "bindings": rows}
    if _schema_allows("executor_kinds"):
        # Зона COSMOS — весь словарь: продукт отвечает за все десять
        # видов, поэтому один его файл закрывает полноту B04, а
        # отсутствие привязок CDPM — слова, а не остановка.
        doc["executor_kinds"] = list(kinds_order)
    return doc


def dump(doc: dict) -> str:
    """Текст файла привязок: форма файлов каталога (LF, отступ 1, ключи
    по алфавиту, ensure_ascii=False)."""
    return dump_json(doc)


def canon(doc: dict) -> str:
    """Канон файла привязок — то, что от него не зависит от момента
    запуска: без `generated_at`/`generated_from` шапки и без `at`/`commit`
    строк стенда. Один канон на всех: `--check`, сводка двери
    (`bindings_summary()["fingerprint"]`), сторож дрейфа реестра."""
    out = {k: v for k, v in (doc or {}).items() if k not in VOLATILE}
    rows = []
    for row in out.get("bindings") or []:
        if not isinstance(row, dict):
            rows.append(row)
            continue
        rows.append({**row, "stand_verified": [
            {k: v for k, v in (sv or {}).items() if k not in VOLATILE_STAND}
            for sv in (row.get("stand_verified") or [])]})
    out["bindings"] = rows
    return dump_json(out)


def fingerprint(doc: dict) -> str:
    """sha256 канона файла привязок."""
    return hashlib.sha256(canon(doc).encode("utf-8")).hexdigest()


# -------------------------------------------------------------- сверка
def _executor_label(ex: dict, j: int) -> str:
    if not isinstance(ex, dict):
        return f"исполнитель {j}"
    name = ex.get("symbol") or _key_value(ex) or ex.get("kind")
    return f"исполнитель {j} ({name})"


def _diff_executors(diff: list, addr: str, built: list, doc: list) -> None:
    if len(built) != len(doc):
        diff.append(f"{addr}: исполнителей в файле {len(doc)}, собрано "
                    f"{len(built)}")
    for j in range(min(len(built), len(doc))):
        a, b = built[j], doc[j]
        if not isinstance(b, dict):
            diff.append(f"{addr} {_executor_label(a, j)}: в файле не объект")
            continue
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                diff.append(f"{addr} {_executor_label(a, j)}: поле {key} — в "
                            f"файле {b.get(key)!r}, собрано {a.get(key)!r}")


def differences(built: dict, doc: dict) -> list[str]:
    """Отличия файла от собранного, словами; изменчивые поля не
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
    rows_doc, order_doc = {}, []
    for i, row in enumerate(doc.get("bindings") or []):
        if not isinstance(row, dict):
            diff.append(f"строка привязки {i} в файле не объект")
            continue
        key = (str(row.get("section")), str(row.get("id")))
        rows_doc[key] = row
        order_doc.append(key)
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
        built_sv = [{k: v for k, v in sv.items() if k not in VOLATILE_STAND}
                    for sv in row["stand_verified"]]
        doc_sv = [{k: v for k, v in (sv or {}).items()
                   if k not in VOLATILE_STAND}
                  for sv in (other.get("stand_verified") or [])]
        if built_sv != doc_sv:
            diff.append(f"{addr}: стендовые подтверждения — в файле "
                        f"{doc_sv!r}, собрано {built_sv!r}")
    for key in rows_doc:
        if key not in seen:
            diff.append(f"{key[0]}/{key[1]}: запись в файле лишняя (каталог "
                        f"её к COSMOS не привязывает)")
    built_order = [(r["section"], r["id"]) for r in built.get("bindings") or []]
    if seen == set(order_doc) and built_order != order_doc:
        diff.append("порядок записей в файле не тот, что у сборки")
    return diff


# ------------------------------------------------------------- запуск
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
    """Каталог двери (один экземпляр на процесс, тот же, что у моста
    CDPM); без каталога — отказ словами: привязок без отметки каталога
    не бывает."""
    from vmp import catalog_access as ca
    try:
        return ca.current()
    except Exception as e:                                    # noqa: BLE001
        raise BindingsBuildError(
            f"каталог шаблонов недоступен: {e} — привязки без версии и "
            f"отпечатка каталога не выпускаются") from None


def _read(path: Path) -> dict:
    from template_catalog.bindings import load_bindings
    if not path.is_file():
        raise BindingsBuildError(f"файла {path} нет")
    try:
        return load_bindings(path)
    except Exception as e:                                    # noqa: BLE001
        raise BindingsBuildError(f"{path} не читается ({e})") from None


def _volatile(doc: dict, key: str, shape) -> str | None:
    """Значение изменчивого поля берётся у проверяемого файла: сверке
    оно безразлично, а git при `--check` тогда не нужен."""
    v = doc.get(key)
    return v if isinstance(v, str) and shape.match(v) else None


def _cdpm_word() -> str:
    """Слово о чужих привязках в печати генератора — то же и от того же
    судьи, что у сводки двери (закрытый перечень из трёх слов): «файл
    лежит» ещё не значит «объявлены»."""
    try:
        from vmp.catalog_access import cdpm_bindings_state
        return cdpm_bindings_state()[1]
    except Exception:                                         # noqa: BLE001
        return "не объявлены" if not CDPM_BINDINGS_FILE.is_file() \
            else "не читаются"


def _stamp(doc: dict, catalog) -> str:
    rows = doc["bindings"]
    present = sum(1 for r in rows for x in r["executors"]
                  if x["state"] == "есть")
    cells = 0
    for section, rec in _records(catalog):
        cells += len(gate.declared_kinds(rec, section))
    stand = {"passed": 0, "failed": 0, "skipped": 0}
    for r in rows:
        for sv in r["stand_verified"]:
            if sv.get("result") in stand:
                stand[sv["result"]] += 1
    return (f"каталог {doc['catalog_version']} | отпечаток "
            f"{doc['catalog_fingerprint']} | записей {len(rows)} | клеток "
            f"{cells} | строк «есть» {present} | стенд: passed "
            f"{stand['passed']}, failed {stand['failed']}, skipped "
            f"{stand['skipped']} | CDPM: " + _cdpm_word())


def awaiting_words(catalog, seed: dict) -> list[str]:
    """Слова о строках посева, которые ждут записи кандидата (0.10).

    Тот же счёт, что и у `build`: строка ручки, чей параметр называет не
    каталог, а кандидат рядом, и адрес записи, которой в каталоге ещё
    нет, но кандидат её несёт. Молчаливого пропуска не бывает — иначе
    зелёная цепочка ничего не значила бы."""
    claims = candidate_claims(catalog)
    if not claims:
        return []
    known = set((catalog.dictionaries() or {}).get("unbound_params") or {})
    out = []
    seen = set()
    for section, rec in _records(catalog):
        addr = f"{section}/{rec.get('id')}"
        seen.add(addr)
        claimed = claims.get(addr) or set()
        if not claimed:
            continue
        names = known | {str(p.get("name")) for p in (rec.get("params") or [])
                         if isinstance(p, dict)}
        waiting = sorted({str(r.get("param")) for r in seed["records"].get(addr) or []
                          if str(r.get("kind")) == "kotlin_knob"
                          and str(r.get("param") or "") not in names
                          and str(r.get("param") or "") in claimed})
        if waiting:
            out.append(f"{addr}: {', '.join(waiting)}")
    for addr in sorted(set(seed["records"]) - seen):
        if addr in claims:
            out.append(f"{addr}: запись целиком")
    return out


def _notes(doc: dict, seed: dict, catalog) -> list[str]:
    """Слова о том, чего файл ещё не может: записи посева впереди
    каталога и виды строк, записью пока не объявленные (предупреждения
    B03 читателя — ожидаемые, названные)."""
    out = []
    present = {f"{r['section']}/{r['id']}" for r in doc["bindings"]}
    ahead = sorted(set(seed["records"]) - present)
    if ahead:
        out.append(f"записи посева впереди каталога {catalog.version()} "
                   f"(строк по ним нет): {', '.join(ahead)}")
    waiting = awaiting_words(catalog, seed)
    if waiting:
        out.append("ожидают записи кандидата: " + "; ".join(waiting))
    stale = sorted(set(seed.get("pending_records") or []) & present)
    if stale:
        out.append(f"записи, объявленные ожидающими, каталог уже назвал — "
                   f"уберите их из pending_records посева, иначе перечень "
                   f"спрячет переименование записи: {', '.join(stale)}")
    declared = {f"{s}/{r.get('id')}": set(gate.declared_kinds(r, s))
                for s, r in _records(catalog)}
    extra = []
    for row in doc["bindings"]:
        addr = f"{row['section']}/{row['id']}"
        for kind in sorted({str(x["kind"]) for x in row["executors"]}
                           - declared.get(addr, set())):
            extra.append(f"{addr}/{kind}")
    if extra:
        out.append(f"виды строк, записью каталога пока не объявленные "
                   f"(предупреждения B03 читателя): {', '.join(extra)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", help="куда записать файл привязок")
    mode.add_argument("--check", help="сверить файл с посевом, кодом и "
                                      "каталогом, ничего не писать")
    ap.add_argument("--commit", help="хеш происхождения вместо git HEAD")
    ap.add_argument("--generated-at", help="момент генерации (ISO 8601 с "
                                           "поясом) вместо текущего")
    ap.add_argument("--stand-results",
                    help="файл результатов стенда (§5.5) — поле "
                         "stand_verified; без ключа берётся "
                         "vmp/stand_results.json, если он есть")
    ap.add_argument("--seed", help="посев вместо vmp/bindings_seed.json")
    ap.add_argument("--prune-pending", action="store_true",
                    help="снять с посева отметки ожидания (pending_records) у "
                         "записей, которые каталог уже назвал, и переписать "
                         "посев — шаг цепочки после сборки записи службой; "
                         "у гейтов CR на временном каталоге не применяется")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        catalog = _catalog()
        seed = load_seed(a.seed)
        results = (load_stand_results(a.stand_results) if a.stand_results
                   else default_stand_results())
        other = None
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
                    stand_results=results, seed=seed)
    except BindingsBuildError as e:
        print(f"привязки COSMOS {'не сверены' if a.check else 'не собраны'}: "
              f"{e}")
        return 2
    stamp = _stamp(doc, catalog)
    if a.check:
        diff = differences(doc, other)
        print(f"сверка {a.check}: {stamp} | расхождений {len(diff)}")
        for d in diff:
            print("  " + d)
        return 1 if diff else 0
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Байтами, не текстом: write_text на Windows перевёл бы «\n» в
    # «\r\n», и форма файла зависела бы от операционной системы.
    path.write_bytes(dump(doc).encode("utf-8"))
    print(f"записаны привязки {path}: {stamp} | момент {doc['generated_at']} "
          f"| коммит {doc['generated_from']}")
    if a.prune_pending:
        # Отметка ожидания — состояние, выводимое из каталога: запись
        # либо уже названа каталогом (сборка записи службой), либо ещё
        # ждёт как кандидат. Хранить её после сборки — второй носитель
        # одного знания, и он ЛЖЁТ: перечень ожидающих прятал бы
        # переименование записи вместо отказа (тест перечня
        # ожидающих). Снятие — шагом цепочки после сборки (0.4.0,
        # 09.09.2026: duty_series_limit осталась «ожидающей» после
        # выпуска и красила гейт волны).
        present = {f"{r['section']}/{r['id']}" for r in doc["bindings"]}
        stale = sorted(set(seed.get("pending_records") or []) & present)
        if stale:
            # Переписывается ТОТ ЖЕ посев, который был прочитан: ключ
            # команды, дверь `CREW_BINDINGS_SEED` либо посев дерева.
            target = Path(a.seed) if a.seed else seed_path()
            seed["pending_records"] = [x for x in seed["pending_records"]
                                       if x not in stale]
            target.write_bytes(dump_json(seed).encode("utf-8"))
            print(f"  сняты отметки ожидания в {target}: "
                  f"{', '.join(stale)} — каталог их назвал")
    for note in _notes(doc, seed, catalog):
        print("  " + note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
