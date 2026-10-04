# -*- coding: utf-8 -*-
"""Заявки CDPM в каталог шаблонов из посадок категории C (шаг 6 каталога,
часть CDPM; проект `final-report/KATALOG-SHAG-6-CDPM-ZAYAVKI-PROEKT-CLD.md`,
редакция 3).

Строка канонического свода с посадкой C («правило машинное, но ни одна
семья каталога не покрывает его механику») — кандидат заявки. Черновик
записи семьи предлагает модель-составитель СЕМЬЮ ПОЛЯМИ (`id`, `title`,
`subject`, `nature`, `violation_unit`, `params[]`, `semantics`), всё
прочее ставит код; готовый черновик судит пятислойный гейт обезличивания
(Г1 читатель каталога на каталоге В ПАМЯТИ с добавленными заявками, Г2
маркеры ЭТОГО прогона, Г3 цифры с градацией по полям, Г4 близость к
формулировке документа, Г5 ключ и дубли). Зелёный черновик ложится в
папку прогона и публикуется в исходящую очередь ИСКЛЮЧИТЕЛЬНЫМ созданием
файла; в `catalog/**` CDPM не пишет никогда — единственная точка записи
каталога у COSMOS.

Номер заявки — от СОДЕРЖАНИЯ пробела (раздел + вид + зона +
нормализованная формулировка), не от счётчика: один пробел из разных
прогонов даёт один номер, и повторная подача идемпотентна без службы.

Модуль не импортирует `cdpm.webui`: живые находки реестра обмена (спор
посадки) ручка передаёт параметром.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from template_catalog.receipts import (REPEAT_NOTE, validate_receipt)
from template_catalog.requests import draft_canon as _record_canon
from vmp.data_contract import STANDARD_DATA_SHEETS

from cdpm import catalog_bridge
from cdpm import file_replace as _file_replace
from cdpm import llm as llm_mod
from cdpm import model as m_mod

# ── настройки площадки (у CDPM нет общего хранилища настроек: всё —
# переменные среды, как CREW_CHANNEL_URL и CDPM_RUNS_DIR) ──────────────
ENV_AUTO = "CDPM_CATALOG_REQUESTS_AUTO"
ENV_OUTBOX = "CDPM_CATALOG_OUTBOX"
ENV_SERVICE_URL = "CDPM_CATALOG_SERVICE_URL"
ENV_SERVICE_TOKEN = "CDPM_CATALOG_SERVICE_TOKEN"

#: Папка заявок внутри прогона и папка удержанных черновиков в ней.
RUN_DIR_NAME = "catalog-requests"
HELD_DIR_NAME = "held"
INDEX_NAME = "index.json"

#: Служебные префиксы `gap`, которые ставит САМ КОД CDPM: след брака
#: посадки или пропуска стадии, а не пробел каталога (§1.1 п.2).
SERVICE_PREFIXES = ("модель не выполнила посадку",
                    "посадка модели отброшена:",
                    "посадка не выполнялась:")

#: Класс документа-источника — СЕМЬ значений по §2.1 проекта COSMOS
#: (`final-report/KATALOG-SHAG-6-PROEKT-CLD.md`). Закон один на два
#: продукта: как только читатель выпустит `template_catalog.requests`,
#: перечень берётся ОТТУДА (§8 п.4 того же проекта) — иначе заявки
#: «вручную» и «пробел при нажатии» напишут класс другими словами, и
#: поле станет несравнимым. До него — местная копия.
_LOCAL_DOCUMENT_CLASSES = ("положение о рабочем времени и времени отдыха",
                           "коллективный договор",
                           "руководство по производству полётов",
                           "правила планирования экипажей",
                           "регламент службы",
                           "документ свода правил авиакомпании",
                           "обзор практики авиакомпаний")
try:                                                      # pragma: no cover
    from template_catalog.requests import (DOCUMENT_CLASSES
                                           as _READER_DOCUMENT_CLASSES)
    DOCUMENT_CLASSES = tuple(_READER_DOCUMENT_CLASSES)
except Exception:                                         # noqa: BLE001
    DOCUMENT_CLASSES = _LOCAL_DOCUMENT_CLASSES
#: Умолчание — общий класс, а НЕ последнее значение перечня: с седьмым
#: значением («обзор практики авиакомпаний») хвост перечня перестал быть
#: общим случаем, и умолчание названо поимённо. Если читатель однажды
#: выпустит перечень без него — берётся последнее значение, а не отказ
#: панели: класс документа не тот повод, чтобы гасить экран.
DEFAULT_DOCUMENT_CLASS = "документ свода правил авиакомпании"
if DEFAULT_DOCUMENT_CLASS not in DOCUMENT_CLASSES:        # pragma: no cover
    DEFAULT_DOCUMENT_CLASS = DOCUMENT_CLASSES[-1]

#: Состояния составления в индексе прогона (§1.3).
COMPOSED_SUBMITTED = "подана"
COMPOSED_HELD = "удержана"
COMPOSED_CONFIRMED = "подтверждена и подана"
COMPOSED_FAILED = "не составлена"
COMPOSED_WITHDRAWN = "отозвана аналитиком"
COMPOSED_STATES = (COMPOSED_SUBMITTED, COMPOSED_HELD, COMPOSED_CONFIRMED,
                   COMPOSED_FAILED, COMPOSED_WITHDRAWN)
#: Состояния, при которых заявка считается поданной от этого прогона.
SUBMITTED_STATES = (COMPOSED_SUBMITTED, COMPOSED_CONFIRMED)

#: Поля черновика, где цифра запрещена всегда (Г3, жёсткий слой).
HARD_DIGIT_FIELDS = ("title", "subject", "origin.note", "note")
#: Единицы и слова порога: число рядом с ними — порог заказчика,
#: а не механика семьи (расширенное A06 читателя).
THRESHOLD_UNITS = ("ч", "час", "мин", "сут", "дн", "ноч", "выходн",
                   "процент", "раз", "рейс", "сегмент", "посад")
_THRESH_UNIT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|" + "|".join(THRESHOLD_UNITS) + r")",
    re.IGNORECASE)
_THRESH_CTX_RE = re.compile(
    r"не более|не менее|≤|≥|порог|(?<![\w])до(?![\w])|(?<![\w])от(?![\w])",
    re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")

#: Состояния заявки, при которых её `record_id` уже занят (закон
#: читателя `template_catalog.reader.REQUEST_PENDING_STATES`).
REQUEST_PENDING_STATES = ("подана", "принята", "исполнитель заявлен")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_PNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,50}$")
REQUEST_ID_RE = re.compile(r"^req-\d{20}$")

#: Виды исполнителей черновика — зеркало посева шага 1 каталога.
EXECUTOR_KINDS_BY_TRAITS = {
    "history": ("kotlin_logic_form", "check_code"),
    "*": ("kotlin_constraint",),
}

_WORD_CHARS = "0-9A-Za-zА-Яа-яЁё_"
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")

#: Незначащие слова формулировки (Г4): совпадение по ним дословностью
#: не считается.
_STOPWORDS = {
    "и", "или", "не", "но", "а", "в", "во", "на", "с", "со", "к", "ко",
    "по", "за", "из", "от", "до", "для", "при", "о", "об", "обо", "у",
    "же", "ли", "бы", "то", "как", "что", "чем", "чтобы", "если", "либо",
    "быть", "есть", "может", "должен", "должна", "должно", "должны",
    "это", "этот", "эта", "эти", "тот", "та", "те", "их", "его", "её",
    "также", "более", "менее", "чем", "над", "под", "между", "через",
}


# ────────────────────────────────────────────────── настройки площадки

def auto_enabled() -> bool:
    """Автоподача включена (умолчание — да, как в плане §5); «0» —
    только по кнопке аналитика."""
    return (os.environ.get(ENV_AUTO) or "").strip() != "0"


def outbox_root() -> Path:
    """Корень исходящей очереди: `CDPM_CATALOG_OUTBOX`, иначе — РЯДОМ
    С ПРОГОНАМИ (`runs_root().parent/cdpm-catalog-requests/outbox`),
    чтобы `CDPM_RUNS_DIR` тестов и нестандартных установок уводил и
    очередь, а не только прогоны."""
    env = (os.environ.get(ENV_OUTBOX) or "").strip()
    if env:
        return Path(env) / "outbox"
    from cdpm import webui as _w
    return _w.runs_root().parent / "cdpm-catalog-requests" / "outbox"


def service_configured() -> bool:
    """Ручка службы каталога настроена (адрес и токен). Сама доставка —
    стадия 4 проекта: контракт службы COSMOS ещё не выпущен."""
    return bool((os.environ.get(ENV_SERVICE_URL) or "").strip()
                and (os.environ.get(ENV_SERVICE_TOKEN) or "").strip())


def blocked_words() -> str | None:
    """Почему заявки не подаются вовсе (None — подаются): недоступный
    каталог.

    Шаг 7, стадия 1б: прежней второй причины — режима среза базы
    шаблонов — у CDPM больше нет. Посадки правил судятся каталогом
    независимо от переменной среза (мост её не читает, §3 проекта), и
    пробел, найденный при её задании, — такой же пробел живого
    каталога, как всякий другой. Мост без каталога отказывает СЛОВАМИ
    (`CatalogRequired`), а здесь нужен признак — решение «не подаются»
    принимает эта граница."""
    if not catalog_bridge.catalog_available():
        return "каталог шаблонов недоступен: заявки не подаются"
    return None


# ──────────────────────────────────────────────── ключ, номер, имя

def request_key(kind: str, zone: str, statement: str) -> str:
    """Ключ пробела: раздел + вид + зона + НОРМАЛИЗОВАННАЯ формулировка
    (закон CDPM `normalized_question_text`). Имя семьи, предложенное
    моделью, в ключ намеренно не входит: расхождение имён при одном
    пробеле — главный источник дублей."""
    material = "\n".join(("families", str(kind or ""), str(zone or ""),
                          m_mod.normalized_question_text(str(statement or ""))))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def request_id_of(key: str) -> str:
    """Номер заявки: 20 десятичных цифр от первых 64 бит ключа —
    образец схемы `^req-\\d{4,}$` соблюдён, совпадение номеров у разных
    ключей 2⁻⁶⁴."""
    return "req-" + str(int(str(key)[:16], 16)).zfill(20)


def fallback_id(key: str) -> str:
    """Запасное имя семьи, когда имя модели не годится: детерминировано
    по хвосту ключа, образец `^[a-z][a-z0-9_]{2,40}$` соблюдён."""
    return "family_" + str(key)[:12]


# ──────────────────────────────────────────────────────── кандидаты

def _disputed_rule_ids(live_findings) -> set:
    """Строки с ЖИВЫМ спором посадки: ключ находки реестра несёт класс
    дефекта с суффиксом «+посадка» и идентификатор строки."""
    out: set = set()
    for f in live_findings or ():
        if not isinstance(f, dict):
            continue
        key = f.get("key")
        rid = None
        if isinstance(key, str) and key:
            try:
                parts = json.loads(key)
            except ValueError:
                parts = None
            if isinstance(parts, list) and len(parts) >= 2 \
                    and str(parts[0] or "").endswith("+посадка"):
                rid = parts[1]
        if rid is None and f.get("fit_dispute"):
            rid = (f.get("target") or {}).get("rule")
        if rid:
            out.add(str(rid))
    return out


def _open_question_rules(doc: dict) -> set:
    out: set = set()
    for q in doc.get("questions") or ():
        if not isinstance(q, dict) or not m_mod.is_open_question(q):
            continue
        for rid in q.get("rule_refs") or ():
            out.add(str(rid))
    return out


def candidates(doc: dict, run_dir=None, live_findings=()) -> list[dict]:
    """Строки посадки C прогона с признаком кандидата и причиной отказа.

    Кандидат — содержательная C у машинного правила без живого спора
    посадки и без открытого вопроса анкеты на неё. Служебная C (`gap` с
    префиксом кода) — след брака посадки, не пробел каталога: заявить её
    можно только кнопкой строки с явным подтверждением."""
    blocked = blocked_words()
    disputed = _disputed_rule_ids(live_findings)
    open_q = _open_question_rules(doc)
    rows: list[dict] = []
    for r in doc.get("rules") or ():
        if not isinstance(r, dict):
            continue
        fit = r.get("template_fit")
        if not isinstance(fit, dict) or fit.get("category") != "C":
            continue
        rid = str(r.get("id"))
        kind, zone = r.get("kind"), r.get("zone")
        gap = str(fit.get("gap") or "")
        reason = None
        service = any(gap.startswith(p) for p in SERVICE_PREFIXES)
        if kind not in ("HARD", "SOFT"):
            reason = f"вид {kind!r} — посадки бывают только у машинных правил"
        elif service:
            reason = f"служебная C (брак посадки, не пробел каталога): {gap}"
        elif rid in disputed:
            reason = "по строке идёт спор посадки (живая находка реестра обмена)"
        elif rid in open_q:
            reason = "по строке открыт вопрос анкеты — строка ещё может стать A/B"
        elif blocked:
            reason = blocked
        key = request_key(kind, zone, r.get("statement"))
        rows.append({
            "rule_id": rid,
            "title": str(r.get("title") or ""),
            "statement": str(r.get("statement") or ""),
            "kind": kind, "zone": zone, "gap": gap,
            "params": [p for p in (r.get("params") or ())
                       if isinstance(p, dict)],
            "sources": [s for s in (r.get("sources") or ())
                        if isinstance(s, dict)],
            "key": key, "request_id": request_id_of(key),
            "candidate": reason is None,
            "service_c": service,
            "reason": reason,
        })
    rows.sort(key=lambda x: x["rule_id"])
    return rows


def counts_of(rows: list[dict], index: dict) -> dict:
    """Счётчики напоминания для карточки прогона и ответа канала."""
    idx = (index or {}).get("rows") or {}
    submitted = sum(1 for v in idx.values()
                    if isinstance(v, dict)
                    and v.get("composed") in SUBMITTED_STATES)
    held = sum(1 for v in idx.values()
               if isinstance(v, dict) and v.get("composed") == COMPOSED_HELD)
    cands = [r for r in rows if r["candidate"]]
    pending = [r for r in cands if r["rule_id"] not in idx]
    return {"candidates": len(cands), "submitted": submitted,
            "held": held, "pending": len(pending)}


# ────────────────────────────────────────────────── индекс прогона

def run_requests_dir(run_dir) -> Path:
    return Path(run_dir) / RUN_DIR_NAME


def index_path(run_dir) -> Path:
    return run_requests_dir(run_dir) / INDEX_NAME


def read_index(run_dir) -> dict:
    p = index_path(run_dir)
    if not p.is_file():
        return {"form": 1, "rows": {}}
    try:
        got = json.loads(p.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {"form": 1, "rows": {}}
    if not isinstance(got, dict) or not isinstance(got.get("rows"), dict):
        return {"form": 1, "rows": {}}
    return got


def _dump(doc) -> bytes:
    """Форма файлов заявок и индекса — как у файлов каталога: отступ 1,
    ключи по алфавиту, LF, UTF-8 без BOM (детерминированно — файлы
    входят в сверку повтора байт в байт)."""
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1,
                       allow_nan=False) + "\n").encode("utf-8")


def write_index(run_dir, index: dict) -> None:
    p = index_path(run_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, _dump({"form": 1,
                            "rows": dict(sorted((index.get("rows") or {})
                                                .items()))}))


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    tmp.write_bytes(data)
    _file_replace.replace_with_retry(tmp, path)


def index_row(key: str, request_id: str, composed: str, words,
              document_class: str) -> dict:
    """Строка индекса — РОВНО пять полей (§1.3): ни меток времени, ни
    источника запуска, ни исходов доставки; индекс детерминирован и
    входит в сверку повтора байт в байт."""
    return {"key": key, "request_id": request_id, "composed": composed,
            "words": words, "document_class": document_class}


# ─────────────────────────────────────────── черновик: что ставит код

def _words(dicts: dict, key: str) -> tuple:
    """Слова закрытого словаря каталога кортежем: перечень отдаёт
    значения, КАРТА (с 0.5.0 так записан `violation_units`) — ключи-коды.
    Словаря нет — пусто: судья поля скажет об этом словами, а листинг
    задания не вправе выдумывать перечень."""
    vals = (dicts or {}).get(key)
    if isinstance(vals, dict):
        return tuple(str(k) for k in vals)
    if isinstance(vals, list):
        return tuple(str(v) for v in vals)
    return ()


#: Свидетель природы у семьи, пришедшей КОНВЕЙЕРОМ ПОПОЛНЕНИЯ (правило
#: X18 каталога): природу такой записи подтверждает вердикт судьи заявки,
#: а не рукописные перечни сертификата ростера — их ведёт тот же контур,
#: что завёл запись. Ставит его КОД при сборке заявки; у модели свидетель
#: не спрашивается — назвать себя свидетелем она не вправе.
NATURE_WITNESS_OF_REQUEST = "change_request"
#: Природа записи, обязанная назвать чтение истории (правило X19).
HISTORY_NATURE = "history"


def executor_kinds_for(nature: str, zone: str, has_params: bool) -> list[str]:
    if nature == "history":
        return list(EXECUTOR_KINDS_BY_TRAITS["history"])
    out = list(EXECUTOR_KINDS_BY_TRAITS["*"])
    if has_params:
        out.append("kotlin_knob")
    if zone in ("PAIRING", "BOTH"):
        out.append("builder_method")
    return out


def build_draft(row: dict, proposal: dict, *, draft_id: str,
                document_class: str, origin_date: str) -> dict:
    """Полная запись семьи §1 схемы без `status`, `history`,
    `stand_tasks`: семь полей — от модели, всё прочее ставит код."""
    kind, zone = row["kind"], row["zone"]
    params = []
    for p in proposal.get("params") or ():
        if not isinstance(p, dict):
            continue
        params.append({"name": str(p.get("name") or ""),
                       "unit": str(p.get("unit") or ""),
                       "relation": _nullstr(p.get("relation")),
                       "qualifier": None,
                       "note": _nullstr(p.get("note"))})
    sem = proposal.get("semantics") if isinstance(proposal.get("semantics"),
                                                  dict) else {}
    nature = str(proposal.get("nature") or "")
    reads = proposal.get("reads_history")
    reads = ({"need": " ".join(str(reads.get("need") or "").split()),
              "kind": str(reads.get("kind") or "")}
             if isinstance(reads, dict) else None)
    return {
        "id": draft_id,
        "class": "extension",
        "title": str(proposal.get("title") or ""),
        "subject": str(proposal.get("subject") or ""),
        "subject_words": None,
        "complements": [],
        "kind": kind,
        "zone": zone,
        "nature": nature,
        # Свидетеля природы ставит КОД: запись пришла заявкой, и её природу
        # подтверждает вердикт судьи заявки (X18), а не собственное слово.
        "nature_confirmed_by": NATURE_WITNESS_OF_REQUEST,
        "violation_unit": (_nullstr(proposal.get("violation_unit"))
                           if kind == "SOFT" else None),
        "violation_size": (_nullstr(proposal.get("violation_size"))
                           if kind == "SOFT" else None),
        "reads_history": reads,
        "params": params,
        "param_sets": [],
        "conditions": [],
        "rule_form": None,
        "semantics": {"scope": _nullstr(sem.get("scope")),
                      "unit": _nullstr(sem.get("unit")),
                      "measure": _nullstr(sem.get("measure")),
                      "text": str(sem.get("text") or "")},
        "executor_kinds": executor_kinds_for(nature, zone, bool(params)),
        "check_templates": [],
        "acceptance": None,
        "origin": {"kind": "заявка", "date": origin_date,
                   "note": ("пробел покрытия при посадке правила из "
                            f"документа класса «{document_class}»; механика "
                            "обобщена составителем CDPM, данных "
                            "авиакомпании не содержит"),
                   "confirmed_by": "аналитик"},
        "note": "заявка CDPM из посадки категории C",
    }


def _nullstr(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


class RequestAssemblyError(RuntimeError):
    """Заявку в каталог собрать нельзя: черновик нарушает закон записи,
    пришедшей конвейером пополнения."""


def assemble_request(row: dict, draft: dict, *, document_class: str,
                     run_name: str, submitted_at: str) -> dict:
    """Запись заявки §6 схемы (закрытая форма — лишнее поле ошибка S02).

    Свидетель природы черновика обязан быть вердиктом судьи заявки (X18):
    «свидетеля нет» у записи, пришедшей заявкой, значило бы природу на её
    собственном слове — отказ сборки словами, а не молчаливая подстановка."""
    witness = (draft or {}).get("nature_confirmed_by")
    if witness != NATURE_WITNESS_OF_REQUEST:
        raise RequestAssemblyError(
            f"заявка {row.get('request_id')}: свидетель природы черновика — "
            f"{witness!r}; запись, пришедшая конвейером пополнения, "
            f"подтверждается вердиктом судьи заявки "
            f"({NATURE_WITNESS_OF_REQUEST})")
    return {
        "request_id": row["request_id"],
        "state": "подана",
        "section": "families",
        "record_id": draft["id"],
        "submitted_at": submitted_at,
        "submitted_by": "CDPM",
        "source": {"kind": "посадка категории C",
                   "document_class": document_class,
                   "run_class": "прогон CDPM",
                   "ref": f"{run_name}#{row['key'][:12]}"},
        "draft": draft,
        "executor_kinds_requested": None,
        "decision": {"by": None, "at": None, "words": None},
        "cr_key": None,
    }


def draft_canon(draft) -> str:
    """Канон черновика СОБСТВЕННОЙ формы (`template_catalog.canon`
    принимает только тело канона из трёх разделов): NFC всех строк +
    json.dumps(sort_keys, компактно) → sha256."""
    def _norm(v):
        if isinstance(v, str):
            return unicodedata.normalize("NFC", v)
        if isinstance(v, dict):
            return {_norm(k): _norm(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_norm(x) for x in v]
        return v
    text = json.dumps(_norm(draft), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─────────────────────────────────── каталог в памяти и слой Г1

def _ref_head(ref) -> str:
    """`source.ref` до «#»: хвост ключа — идентификатор, не проза (код
    станции из букв a–f внутри 12-hex хвоста давал бы жёсткий стоп,
    который повтор модели снять не может)."""
    return str(ref or "").split("#", 1)[0]


def memory_catalog(cat, extra_requests: list[dict]):
    """Каталог В ПАМЯТИ из текущего экземпляра моста с добавленными
    заявками: разделы — из `body()` (ровно три раздела без
    `form_version`; `dictionaries()` добавляет вычисленный ключ
    `area_param_names`, и словарь не прошёл бы S02), `single_file=True`
    (H03 не ищет файлы разделов на диске).

    Запись каталога с тем же номером, что у добавляемого черновика,
    ЗАМЕНЯЕТСЯ черновиком (K08): правила читателя судят сам черновик
    (маркеры, форма, занятые имена), а факт общей заявки под известным
    номером — не их дело, его судит Г5; иначе R01 «повтор request_id»
    говорил бы не о черновике, а о том, что номер уже известен."""
    from template_catalog.reader import Catalog
    body = cat.body()
    trimmed = []
    for rq in extra_requests or ():
        rq = json.loads(json.dumps(rq, ensure_ascii=False))
        src = rq.get("source")
        if isinstance(src, dict):
            src["ref"] = _ref_head(src.get("ref"))
        trimmed.append(rq)
    replaced = {str(rq.get("request_id")) for rq in trimmed}
    base = [rq for rq in (cat.requests() or ())
            if not (isinstance(rq, dict)
                    and str(rq.get("request_id")) in replaced)]
    docs = {
        "families": {"form_version": 1, "families": body["families"]},
        "criteria": {"form_version": 1, "criteria": body["criteria"]},
        "dictionaries": {"form_version": 1, **body["dictionaries"]},
        "requests": {"form_version": 1, "requests": base + trimmed},
    }
    return Catalog(path=cat.path, header=cat.header, docs=docs,
                   versions_doc={"form_version": 1, "versions": cat.versions()},
                   single_file=True)


def _marker_patterns():
    """Перечень маркеров заказчиков машины (общий с гейтом паритета);
    None — перечня на машине нет: подача не останавливается, слова об
    этом идут в квитанцию."""
    try:
        from tools import customer_markers as cm
    except Exception:                                     # noqa: BLE001
        return None
    try:
        checks = cm.load_checks()
    except Exception:                                     # noqa: BLE001
        return None
    if not checks:
        return None
    pats: list = []
    for check in (checks.get("checks") or checks.get("markers") or []):
        if isinstance(check, dict):
            try:
                pats += cm._patterns(check)
            except Exception:                             # noqa: BLE001
                continue
    return pats


# ────────────────────────────────────── проза черновика и заявки

def _prose_of_draft(draft: dict) -> list[tuple]:
    """(адрес, текст) всей прозы черновика — для Г2/Г4."""
    out = [("title", draft.get("title")), ("subject", draft.get("subject")),
           ("note", draft.get("note")),
           ("origin.note", (draft.get("origin") or {}).get("note")),
           ("acceptance", draft.get("acceptance"))]
    sem = draft.get("semantics") or {}
    for k in ("scope", "unit", "measure", "text"):
        out.append((f"semantics.{k}", sem.get(k)))
    for i, p in enumerate(draft.get("params") or ()):
        if isinstance(p, dict):
            out.append((f"params[{i}].note", p.get("note")))
    return [(a, t) for a, t in out if isinstance(t, str) and t]


def _prose_of_request(request: dict) -> list[tuple]:
    src = request.get("source") or {}
    out = [("source.kind", src.get("kind")),
           ("source.document_class", src.get("document_class")),
           ("source.run_class", src.get("run_class")),
           ("source.ref", _ref_head(src.get("ref")))]
    return [(a, t) for a, t in out if isinstance(t, str) and t] \
        + _prose_of_draft(request.get("draft") or {})


# ─────────────────────────────────────── контекст прогона (Г2, Г4)

def _token_pattern(value: str):
    return re.compile(f"(?<![{_WORD_CHARS}])" + re.escape(value)
                      + f"(?![{_WORD_CHARS}])", re.IGNORECASE)


_STANDARD_SHEET_TERMS = frozenset(v.casefold() for v in STANDARD_DATA_SHEETS)


def _standard_sheet_pattern(value: str):
    """A public term is a marker only when used as a source reference.

    'дни отпуска' names domain semantics; 'лист Отпуска', a quoted label,
    a cell/path or filename names a carrier. This does not exempt the same
    bytes when separately present as a customer identity, code or anchor.
    """
    token = re.escape(value)
    left, right = f"(?<![{_WORD_CHARS}])", f"(?![{_WORD_CHARS}])"
    quotes = r"[«»\"'“”„`]+"
    carrier = r"(?:лист\w*|вкладк\w*|таблиц\w*|колонк\w*|worksheet|sheet|table|column)"
    naming = r"(?:\s+(?:данных|книги|Excel|с\s+именем|под\s+названием|по\s+имени)){0,2}"
    separator = r"[\s:=—–-]*"
    return re.compile(
        rf"(?:{left}{carrier}{naming}{separator}(?:{quotes}\s*)?{token}{right}"
        rf"|{quotes}\s*{token}\s*{quotes}"
        rf"|\[\s*{token}\s*\]"
        rf"|[/\\]\s*{token}{right}"
        rf"|{left}{token}{right}\s*(?:{quotes}\s*)?(?:[![/\\]|\.[A-Za-z0-9]{{1,16}}\b)"
        rf"|{left}{token}{right}\s*[:=—–-]\s*{carrier})",
        re.IGNORECASE,
    )


def _catalog_prose_words(cat) -> set:
    """Слова прозы самого каталога: они обезличены по построению, и
    совпадение с именем колонки данных отказом быть не может."""
    words: set = set()
    try:
        blob = json.dumps(cat.body(), ensure_ascii=False)
    except Exception:                                     # noqa: BLE001
        return words
    for w in _WORD_RE.findall(blob):
        if len(w) >= 4:
            words.add(w.casefold())
    return words


def run_context(run_dir, doc: dict, status: dict | None = None,
                cat=None) -> dict:
    """Маркеры ЭТОГО прогона (Г2) и тексты формулировок (Г4).

    Общий перечень маркеров знать их не может: имя авиакомпании из
    статуса, имена файлов документов, имена листов и колонок данных
    заказчика, идентификаторы строк и вопросов, якоря и коды сводa."""
    run_dir = Path(run_dir) if run_dir is not None else None
    status = status or {}
    safe = _catalog_prose_words(cat) if cat is not None else set()
    markers: list[tuple] = []

    def _add(klass: str, value, min_len: int = 4, skip_safe: bool = True):
        v = str(value or "").strip()
        if len(v) < min_len:
            return
        if skip_safe and v.casefold() in safe:
            return
        markers.append((klass, v))

    customer = str(status.get("customer") or "")
    _add("название авиакомпании", customer, 3, skip_safe=False)
    for w in _WORD_RE.findall(customer):
        _add("слово из названия авиакомпании", w)
    if run_dir is not None:
        man = run_dir / "freeze" / "manifest.json"
        if man.is_file():
            try:
                mdoc = json.loads(man.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                mdoc = {}
            for f in (mdoc.get("files") or ()):
                if not isinstance(f, dict):
                    continue
                _add("идентификатор источника", f.get("sid"), 2,
                     skip_safe=False)
                name = str(f.get("file") or "")
                _add("имя файла документа", name, 4, skip_safe=False)
                for w in _WORD_RE.findall(name):
                    _add("слово имени файла документа", w)
        ds = run_dir / "data-freeze" / "dataset.json"
        if ds.is_file():
            try:
                ddoc = json.loads(ds.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ddoc = {}
            for sid, tables in (ddoc.get("tables") or {}).items():
                _add("идентификатор источника данных", sid, 2,
                     skip_safe=False)
                if not isinstance(tables, dict):
                    continue
                for sheet, table in tables.items():
                    # Standard sheet labels are public product vocabulary,
                    # but their explicit source-address use remains a marker
                    # even when that word already occurs in catalogue prose.
                    standard = str(sheet).strip().casefold() in _STANDARD_SHEET_TERMS
                    _add("имя листа данных", sheet, skip_safe=not standard)
                    for col in ((table or {}).get("columns") or ()):
                        _add("имя колонки данных", col)
    for r in doc.get("rules") or ():
        if isinstance(r, dict):
            _add("идентификатор строки свода", r.get("id"), 4,
                 skip_safe=False)
            for s in (r.get("sources") or ()):
                if isinstance(s, dict):
                    _add("якорь клаузы", s.get("anchor"), 3, skip_safe=False)
    for q in doc.get("questions") or ():
        if isinstance(q, dict):
            _add("идентификатор вопроса анкеты", q.get("id"), 4,
                 skip_safe=False)
    try:
        for c in m_mod.parameter_codes(doc):
            _add("код из параметров свода", (c or {}).get("code"), 2,
                 skip_safe=False)
    except Exception:                                     # noqa: BLE001
        pass
    try:
        from cdpm import measure as _measure
        for code in _measure.registry_codes(doc):
            _add("код из перечней свода", code, 2, skip_safe=False)
    except Exception:                                     # noqa: BLE001
        pass
    seen: set = set()
    compiled = []
    for klass, value in markers:
        low = value.casefold()
        scoped = klass == "имя листа данных" and low in _STANDARD_SHEET_TERMS
        # A public sheet term must never hide a later STRICT parameter code
        # with identical bytes. Preserve both roles; strict still wins.
        key = (low, scoped)
        if key in seen:
            continue
        seen.add(key)
        pattern = _standard_sheet_pattern(value) if scoped else _token_pattern(value)
        compiled.append((klass, value, pattern))
    forbidden = _marker_patterns()
    return {"markers": compiled,
            "forbidden": forbidden,
            "markers_file_absent": forbidden is None,
            "clauses": _clause_texts(run_dir, doc) if run_dir else {}}


def _clause_texts(run_dir: Path, doc: dict) -> dict:
    """Тексты клауз-источников по строкам свода (Г4): дословность
    судится и против исходной формулировки документа."""
    out: dict = {}
    base = Path(run_dir) / "freeze" / "clauses"
    if not base.is_dir():
        return out
    cache: dict = {}
    for r in doc.get("rules") or ():
        if not isinstance(r, dict):
            continue
        texts = []
        for s in (r.get("sources") or ()):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("sid") or "")
            if sid not in cache:
                p = base / f"{sid}.json"
                try:
                    cache[sid] = json.loads(p.read_text(encoding="utf-8")) \
                        if p.is_file() else {}
                except (OSError, ValueError):
                    cache[sid] = {}
            for c in (cache[sid].get("clauses") or ()):
                if isinstance(c, dict) and c.get("anchor") == s.get("anchor"):
                    texts.append(str(c.get("text") or ""))
        if texts:
            out[str(r.get("id"))] = texts
    return out


# ─────────────────────────────────────────────────────── гейт Г1–Г5

def _content_words(text: str) -> list[str]:
    norm = m_mod.normalized_question_text(str(text or ""))
    return [w for w in norm.split() if w and w not in _STOPWORDS]


def _closeness(statement: str, prose: str) -> str | None:
    """Г4: дословность как БЛИЗОСТЬ текстов — окно из min(8, n−1) подряд
    содержательных слов формулировки в прозе черновика либо доля общих
    содержательных слов ≥ 0.8."""
    src = _content_words(statement)
    n = len(src)
    if n <= 3:
        return None
    dst = _content_words(prose)
    win = min(8, n - 1)
    if win >= 2 and len(dst) >= win:
        dst_join = " " + " ".join(dst) + " "
        for i in range(0, n - win + 1):
            chunk = " " + " ".join(src[i:i + win]) + " "
            if chunk in dst_join:
                return (f"черновик повторяет {win} подряд слов формулировки "
                        f"документа: «{' '.join(src[i:i + win])}»")
    if dst:
        share = len(set(src) & set(dst)) / float(len(set(src)))
        if share >= 0.8:
            return (f"черновик повторяет формулировку документа: общих слов "
                    f"{share:.0%}")
    return None


def _digit_problems(draft: dict) -> tuple[list, list]:
    """Г3: градация по полям. Жёстко — любая цифра в заголовке, предмете
    и примечаниях; в описании механики и примечаниях параметров — число
    с единицей или в контексте порога жёстко, голая цифра мягко."""
    hard, soft = [], []
    for addr, text in _prose_of_draft(draft):
        if not _DIGIT_RE.search(text):
            continue
        if addr in HARD_DIGIT_FIELDS:
            hard.append(f"цифра в поле {addr}: «{text[:120]}» — в заголовке, "
                        f"предмете и примечаниях цифр не бывает")
            continue
        m = _THRESH_UNIT_RE.search(text)
        if m:
            hard.append(f"число с единицей в поле {addr}: {m.group(0)!r} — "
                        f"это порог заказчика, а не механика семьи")
            continue
        near = False
        for d in _DIGIT_RE.finditer(text):
            lo = max(0, d.start() - 40)
            hi = min(len(text), d.end() + 40)
            if _THRESH_CTX_RE.search(text[lo:hi]):
                near = True
                break
        if near:
            hard.append(f"число в контексте порога в поле {addr}: "
                        f"«{text[:120]}» — порог заказчика, не механика")
        else:
            soft.append(f"голая цифра в поле {addr}: «{text[:120]}» — числа "
                        f"суть значения параметров, не механика семьи")
    return hard, soft


def gate(requests: list[dict], cat, run_ctx: dict,
         statements: dict | None = None) -> dict:
    """Пятислойный гейт обезличивания. Возвращает
    {request_id: {"hard": [...], "soft": [...], "warnings": [...],
    "known": None | {...}}} — `known` (K08) заполняет Г5, когда номер уже
    известен каталогу: {request_id, ref, state, same_draft, closed,
    decision}. `validate()` читателя зовётся ОДИН РАЗ на весь пакет
    черновиков."""
    out = {r["request_id"]: {"hard": [], "soft": [], "warnings": [],
                             "known": None}
           for r in requests}
    if not requests:
        return out
    known = _catalog_records(cat)
    # Г1 — правила читателя на каталоге в памяти с добавленными заявками:
    # ВСЕ черновики, и под известным номером тоже (запись с тем же
    # номером в каталоге в памяти заменена черновиком — `memory_catalog`;
    # ложного R01 «повтор request_id» нет, а маркеры и форму читатель
    # судит у каждого черновика, K08).
    forbidden = run_ctx.get("forbidden") or ()
    problems = memory_catalog(cat, requests).validate(forbidden_terms=forbidden)
    for p in problems:
        where = str(getattr(p, "where", ""))
        for r in requests:
            rid = r["request_id"]
            if where == f"requests/{rid}" or where.startswith(f"requests/{rid}."):
                pass
            elif where.startswith(f"requests/{rid}/"):
                pass
            else:
                continue
            words = f"[Г1 {p.code}] {p.where}: {p.message}"
            if getattr(p, "severity", "error") == "error":
                out[rid]["hard"].append(words)
            else:
                out[rid]["warnings"].append(words)
            break
    if run_ctx.get("markers_file_absent"):
        for v in out.values():
            v["warnings"].append(
                "перечень маркеров заказчиков на машине отсутствует — "
                "судились классовые образцы и маркеры прогона")
    for r in requests:
        rid = r["request_id"]
        draft = r.get("draft") or {}
        # Г2 — маркеры ЭТОГО прогона.
        for addr, text in _prose_of_request(r):
            for klass, value, pat in run_ctx.get("markers") or ():
                if pat.search(text):
                    out[rid]["hard"].append(
                        f"[Г2] черновик содержит обозначение из данных "
                        f"прогона ({klass}) в поле {addr}: {value!r}")
        # Г3 — цифры.
        hard, soft = _digit_problems(draft)
        out[rid]["hard"] += [f"[Г3] {w}" for w in hard]
        out[rid]["soft"] += [f"[Г3] {w}" for w in soft]
        # Г4 — близость к формулировке документа.
        prose = " ".join(t for _a, t in _prose_of_draft(draft))
        texts = list((statements or {}).get(rid) or ())
        for text in texts:
            words = _closeness(text, prose)
            if words:
                out[rid]["soft"].append(f"[Г4] {words}")
                break
    # Г5 — ключ и дубли по каталогу (K08, контракт П0 §4.2): закон ОДИН
    # для составления и для «Подтвердить и подать» — `known_verdict`.
    for r in requests:
        rid = r["request_id"]
        other = known.get(rid)
        if other is None:
            continue
        verdict = known_verdict(r, other)
        words = f"[Г5] {verdict['words']}"
        (out[rid]["hard"] if verdict["closed"]
         else out[rid]["warnings"]).append(words)
        out[rid]["known"] = verdict
    return out


def _catalog_records(cat) -> dict:
    """Записи заявок каталога по номеру ({request_id: запись}).

    Запись БЕЗ номера в перечень не входит: номер — идентичность заявки,
    а не любое приведение к строке. Пустой номер слился бы со строкой
    прогона, у которой номера нет (обе дали бы ключ `""`), а `null` стал
    бы строкой «None» — разные сущности под одним ключом.

    Повтор номера в каталоге — нарушение самого каталога (правило
    читателя R01), и разрешает его КАТАЛОГ, а не эта сторона: берётся
    та же запись, что отдаёт `cat.request(rid)`, — первая. Выбирать
    другую значило бы показывать прогону не то решение, которое видно на
    экране службы."""
    out: dict = {}
    for rq in ((cat.requests() or ()) if cat is not None else ()):
        if not isinstance(rq, dict):
            continue
        rid = _nullstr(rq.get("request_id"))
        if rid:
            out.setdefault(rid, rq)
    return out


def known_verdict(request: dict, record: dict) -> dict:
    """Сведение Г5 о номере, уже известном каталогу (K08, §4.2): заявка
    ОБЩАЯ — решение службы по ней видно из этого прогона так же, как из
    подавшего. Отказ — только у записи «отклонена»: словами решения
    владельца и путём пересмотра (служба на повтор отвечает
    `request-closed`, пока владелец не пересмотрит). В любом ином
    состоянии — «уже подана из <ref>» как сведение, не как отказ: строка
    индекса «подана», а в очередь заявка повторно не публикуется — та же
    заявка не подаётся дважды.

    Возвращает {request_id, ref, state, same_draft, closed, decision,
    words}; `words` — слова отказа (closed) либо сведения."""
    rid = str(request.get("request_id") or "")
    same = draft_canon(record.get("draft")) == draft_canon(request.get("draft"))
    ref = _ref_head((record.get("source") or {}).get("ref")) or "?"
    state = str(record.get("state") or "")
    decision = decision_of(record)
    closed = state == REQUEST_REJECTED
    if closed:
        words = (f"заявка {rid} отклонена владельцем каталога"
                 + (f": {decision['words']}" if decision["words"] else "")
                 + (f" ({decision['at']})" if decision["at"] else "")
                 + "; повторная подача — только после пересмотра решения "
                   "на экране «Заявки» каталога шаблонов (служба отвечает "
                   "request-closed)")
    else:
        words = ((f"заявка {rid} уже подана из {ref} (тот же черновик)"
                  if same else
                  f"заявка {rid} уже подана иным черновиком из {ref}")
                 + f"; дубль не заводится — состояние службы «{state}» "
                   f"прогон видит по общей заявке")
    return {"request_id": rid, "ref": ref, "state": state,
            "same_draft": same, "closed": closed, "decision": decision,
            "words": words}


def known_of(request: dict, cat) -> dict | None:
    """Сведение Г5 для ОДНОЙ заявки: None — номер каталогу неизвестен.
    Им судит «Подтвердить и подать» (`webui._catalog_confirm`) на момент
    подтверждения: между удержанием черновика и подтверждением владелец
    каталога мог решить, и удержанный черновик тем же законом, что и
    составленный, повторно в очередь не публикуется."""
    record = _catalog_records(cat).get(str(request.get("request_id") or ""))
    return None if record is None else known_verdict(request, record)


# ──────────────────────────────────────────────── очередь и подача

def _outbox_paths(outbox: Path, request_id: str) -> dict:
    outbox = Path(outbox)
    return {"outbox": outbox, "request_id": str(request_id),
            "live": outbox / f"{request_id}.json",
            "reply": outbox / f"{request_id}.reply.json",
            "sent": outbox / "sent" / f"{request_id}.json",
            "withdrawn": outbox / "withdrawn" / f"{request_id}.json",
            # Живой файл под номером, уже доставленным (`sent/`), —
            # дубль: победителя он не заменяет, а уходит сюда (K08).
            "duplicates": outbox / "duplicates" / f"{request_id}.json"}


def _loads_reply(raw):
    """Read exchange JSON without choosing duplicate keys or nonfinite values.

    Schema, receipt identity and outcome decisions stay with their existing
    judges. This reader does not normalize or rewrite the source bytes.
    """
    return m_mod.loads_strict(raw)


def _read_json(path: Path):
    try:
        return _loads_reply(Path(path).read_bytes())
    except (OSError, ValueError):
        return None


# ── ОДНА координация по номеру заявки (K08, F2) ───────────────────────
#: Каталог замков очереди: `<очередь>/.lock/<номер заявки>`.
QUEUE_LOCK_DIR = ".lock"
#: Окно ожидания чужого писателя, секунд (по времени, как у замены файла
#: `cdpm/file_replace.py`: медленная машина получает столько же секунд,
#: сколько быстрая).
LOCK_WINDOW_S = 10.0
#: Возраст, после которого замок считается брошенным аварийно вышедшим
#: писателем: критический участок — единицы миллисекунд (создать файл,
#: переименовать), и минута перекрывает его с многократным запасом.
LOCK_STALE_S = 60.0

_LOCKS_GUARD = threading.Lock()
#: Замки, удерживаемые ЭТИМ процессом: путь → [идентификатор нити,
#: глубина повторного входа].
_LOCKS_HELD: dict = {}


class QueueBusy(RuntimeError):
    """Замок номера занят дольше окна ожидания — писать по номеру нельзя.

    Отказ словами, а не молчаливая работа без координации: иначе
    вернулась бы та самая гонка, ради которой замок и заведён."""


def _lock_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _drop_stale_lock(path: Path, key: str) -> bool:
    """Снять БРОШЕННЫЙ замок (писатель аварийно вышел): True — сняли.
    Замок, удерживаемый нитью этого процесса, не трогается никогда."""
    with _LOCKS_GUARD:
        if key in _LOCKS_HELD:
            return False
    try:
        if time.time() - path.stat().st_mtime < LOCK_STALE_S:
            return False
        os.rmdir(path)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def queue_lock(outbox, request_id: str, *, window_s: float | None = None):
    """ОДНА координация по номеру заявки: под ней идут и проверка, и
    публикация, и перенос, и отзыв, и повторная подача (K08, F2).

    Примитив — `os.mkdir` каталога `<очередь>/.lock/<номер>`: создание
    каталога неделимо и на Windows, и на POSIX (создать существующий
    нельзя — сразу `FileExistsError`), снятие — `os.rmdir`, и открытых
    описателей замок не держит ВОВСЕ. Файл-замок (`open(path, "xb")`)
    на Windows хуже именно снятием: пока чужой читатель (антивирус,
    индексатор — та самая беда, ради которой живёт
    `cdpm/file_replace.py`) держит файл открытым, удалить его нельзя, и
    замок стал бы вечным. Одна лишь неделимая замена (`os.replace`) от
    гонки не спасает: она молча ЗАМЕНЯЕТ победителя (CWE-367 — разрыв
    между проверкой и действием закрывается координацией ДО проверки, а
    не ещё одной проверкой перед заменой).

    Ожидание — ПО ВРЕМЕНИ (`window_s`, умолчание `LOCK_WINDOW_S`); не
    дождались — `QueueBusy` словами. Замок старше `LOCK_STALE_S`
    считается брошенным и снимается. Повторный вход ТОЙ ЖЕ нити
    разрешён (перенос внутри уже занятого участка); другая нить и другой
    процесс ждут наравне."""
    path = Path(outbox) / QUEUE_LOCK_DIR / str(request_id)
    key, me = _lock_key(path), threading.get_ident()
    with _LOCKS_GUARD:
        held = _LOCKS_HELD.get(key)
        mine = held is not None and held[0] == me
        if mine:
            held[1] += 1
    if mine:
        try:
            yield path
        finally:
            with _LOCKS_GUARD:
                held = _LOCKS_HELD.get(key)
                if held is not None:
                    held[1] -= 1
        return
    window = LOCK_WINDOW_S if window_s is None else float(window_s)
    deadline = time.monotonic() + max(0.0, window)
    attempt = 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:                                  # noqa: BLE001
        raise QueueBusy(f"замок очереди по номеру {request_id} не "
                        f"заводится: {e}") from e
    while True:
        try:
            os.mkdir(path)
            break
        except FileExistsError:
            attempt += 1
            if _drop_stale_lock(path, key):
                continue
            if time.monotonic() >= deadline:
                raise QueueBusy(
                    f"очередь по номеру {request_id} занята другим "
                    f"писателем дольше {window:g} с ({attempt} попыток): "
                    f"замок {path}")
            time.sleep(0.01 * (1 + attempt % 5))
        except OSError as e:                              # noqa: BLE001
            raise QueueBusy(f"замок очереди по номеру {request_id} не "
                            f"заводится: {e}") from e
    with _LOCKS_GUARD:
        _LOCKS_HELD[key] = [me, 1]
    try:
        yield path
    finally:
        with _LOCKS_GUARD:
            held = _LOCKS_HELD.get(key)
            if held is not None and held[0] == me:
                held[1] -= 1
                if held[1] <= 0:
                    _LOCKS_HELD.pop(key, None)
        try:
            os.rmdir(path)
        except OSError:
            pass


def enqueue(request: dict, run_dir, outbox, *, replay: bool = False,
            held: bool = False, known: dict | None = None) -> dict:
    """Запись заявки в папку прогона и публикация в очередь.

    Публикация — ИСКЛЮЧИТЕЛЬНЫМ созданием `open(path, "x")` ПОД ЗАМКОМ
    НОМЕРА (`queue_lock`): два одновременных прогона одного документа
    дают один ключ и один номер, и проигравший судит Г5 повторно по
    файлу-победителю. Замок держит ВЕСЬ участок «проверить — создать»:
    без него проверка `sent/` и создание живого файла — две разные
    операции, и настоящая доставка победителя между ними отправляла его
    в `sent/`, после чего под тем же номером появлялся второй, живой и
    отправляемый, ИНОЙ черновик (K08, F2). При
    `replay=True` очередь не трогается вовсе — потомок повтора пишет
    только свою папку и индекс. `known` — сведение Г5 о номере, уже
    известном каталогу (K08): запись ложится в папку прогона (у прогона
    свой черновик и своя строка индекса — он видит решение по общей
    заявке), а в очередь не публикуется: та же заявка не подаётся
    дважды, доставлять нечего. Публикация считается состоявшейся и
    тогда, когда файл-победитель уже доставлен и лежит в `sent/`: второго
    живого файла под тем же номером не бывает."""
    rid = str(request["request_id"])
    folder = run_requests_dir(run_dir)
    if held:
        folder = folder / HELD_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_write(folder / f"{rid}.json", _dump(request))
    if held:
        return {"queued": False, "words": "черновик удержан — в очередь "
                                          "не подан"}
    if known:
        # Слова сведения — от состояния КАТАЛОГА, не очереди: они одни и
        # у источника, и у потомка повтора (индекс сверяется байт в
        # байт), потому ветка стоит ДО ветки повтора.
        ref = str(known.get("ref") or "?")
        return {"queued": False,
                "words": (f"уже подана из {ref} (тот же черновик)"
                          if known.get("same_draft") else
                          f"подана иным черновиком из {ref}; дубль не "
                          f"заводится")}
    if replay:
        # Повтор очередь не трогает: файлов не создаёт и не переносит,
        # доставки нет — слова об этом идут в квитанцию, НЕ в индекс
        # (индекс сверяется байт в байт).
        return {"queued": False, "words": None}
    paths = _outbox_paths(Path(outbox), rid)
    body = _dump(request)
    try:
        with queue_lock(paths["outbox"], rid):
            if paths["sent"].is_file():
                # Победитель уже доставлен — копия каталога могла ещё не
                # узнать номер, но повторная публикация всё равно дала бы
                # второй файл под тем же номером (закон исключительной
                # публикации, K08).
                return _collision(rid, _read_json(paths["sent"]) or {},
                                  request)
            paths["live"].parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(paths["live"], "xb") as f:
                    f.write(body)
            except FileExistsError:
                return _collision(rid, _read_json(paths["live"]) or {},
                                  request)
            if paths["sent"].is_file():
                # Победитель появился, ПОКА шла публикация: его положил
                # писатель, замка не берущий (читающая сторона, чужой
                # инструмент). Первым принятым остаётся ОН: наш живой
                # файл уходит следом в `duplicates/` — второго живого
                # файла под номером не остаётся, и отправлять его
                # повторно нечему.
                winner = _read_json(paths["sent"]) or {}
                _park_duplicate(paths)
                return _collision(rid, winner, request)
            return {"queued": True, "words": None}
    except QueueBusy as e:
        return {"queued": False,
                "words": f"в очередь не подана — {e}; повторить подачу "
                         f"можно, номер тот же"}


def _collision(rid: str, winner: dict, request: dict) -> dict:
    """Слова столкновения с файлом-победителем очереди (живым либо уже
    доставленным): тот же черновик — «уже подана», иной — «подана иным
    черновиком из <ref>; дубль не заводится»."""
    if draft_canon(winner.get("draft")) == draft_canon(request.get("draft")):
        return {"queued": True, "words": f"заявка {rid} уже подана"}
    ref = _ref_head((winner.get("source") or {}).get("ref")) or "?"
    return {"queued": False,
            "words": f"заявка {rid} подана иным черновиком из {ref}; "
                     f"дубль не заводится"}


def withdraw(request_id: str, run_dir, outbox, cat=None, *,
             replay: bool = False) -> dict:
    """Отзыв заявки аналитиком: файл переносится в `outbox/withdrawn/`,
    пока номер не принят каталогом. У потомка повтора файлы очереди не
    трогаются — меняется только индекс."""
    rid = str(request_id)
    if replay:
        return {"ok": True, "words": None}
    if cat is not None and rid in _catalog_records(cat):
        # Один закон известного номера с Г5 и `resend`: запись без
        # номера в перечень не входит (третье ревью, остаток (c)).
        return {"ok": False,
                "words": "заявка уже принята каталогом — решение на экране "
                         "каталога COSMOS"}
    paths = _outbox_paths(Path(outbox), rid)
    reply = _read_json(paths["reply"])
    if isinstance(reply, dict) and reply.get("accepted") is True:
        sent = _queue_record(run_dir, rid, outbox)
        receipt = _saved_delivery_receipt(run_dir, rid, outbox, sent=sent)
        if receipt_confirmed(receipt, rid, sent=sent) is None:
            return {"ok": False,
                    "words": f"{UNCONFIRMED_WORDS} — перед отзывом нужна "
                             "сверка со службой по тому же номеру"}
        return {"ok": False,
                "words": "заявка уже принята читающей стороной — решение на "
                         "экране каталога COSMOS"}
    # Отзыв — тот же писатель очереди, что подача и перенос: под тем же
    # замком номера (K08, F2), иначе он уводил бы файл из-под доставки.
    try:
        with queue_lock(paths["outbox"], rid):
            if paths["withdrawn"].is_file():
                return {"ok": True, "words": "заявка уже отозвана"}
            if not paths["live"].is_file():
                return {"ok": False, "words": "заявки нет в очереди"}
            paths["withdrawn"].parent.mkdir(parents=True, exist_ok=True)
            _file_replace.replace_with_retry(paths["live"],
                                             paths["withdrawn"])
            return {"ok": True, "words": None}
    except QueueBusy as e:
        return {"ok": False, "words": f"отзыв не выполнен — {e}"}


def resend(request_id: str, run_dir, outbox, cat=None, *,
           replay: bool = False) -> dict:
    """Повторная подача: отозванный файл возвращается в очередь тем же
    номером. У потомка повтора очередь не трогается.

    Номер, уже известный каталогу, повторной подачи не допускает — как
    и отзыва: заявка общая, решение по ней на экране каталога (K08). Не
    допускает её и КОНФЛИКТ КАНОНА (служба ответила `request-exists` /
    `duplicate-request` и назвала существующую заявку): иной черновик
    той же записи повторной отправкой принят не станет (§11.2 п. 6).
    Файл-победитель в `sent/` — заявка доставлена: второй живой файл
    под номером был бы дублем, а состояние читается по квитанции и
    каталогу; сверка со службой — «Доставить» (повтор по тому же
    номеру, без новой заявки)."""
    rid = str(request_id)
    if replay:
        return {"ok": True, "words": None}
    record = _catalog_records(cat).get(rid)
    if record is not None:
        return {"ok": False,
                "words": f"заявка уже известна каталогу (состояние "
                         f"«{record.get('state') or '?'}») — повторная "
                         f"подача не нужна; решение на экране каталога "
                         f"COSMOS"}
    conflict = _canon_conflict(read_receipt(run_dir, rid))
    if conflict:
        # Служба назвала СУЩЕСТВУЮЩУЮ заявку по этой записи: свой
        # черновик ей не заменить, и повторная отправка того же отказа
        # не отменяет (§11.2 п. 6, K08-F3).
        return {"ok": False,
                "words": f"конфликт канона: заявка {conflict} уже открыта "
                         f"по этой записи — повторная подача иного "
                         f"черновика ничего не изменит; решение по общей "
                         f"заявке на экране каталога COSMOS"}
    paths = _outbox_paths(Path(outbox), rid)
    # Повторная подача — писатель очереди: тот же замок номера (K08, F2).
    try:
        with queue_lock(paths["outbox"], rid):
            if paths["live"].is_file():
                return {"ok": True, "words": "заявка уже в очереди"}
            if paths["sent"].is_file():
                return {"ok": False,
                        "words": "заявка уже доставлена (файл очереди в "
                                 "sent/) — повторная подача не нужна: "
                                 "состояние читается по квитанции и "
                                 "каталогу, сверка со службой — "
                                 "«Доставить»"}
            src = paths["withdrawn"] if paths["withdrawn"].is_file() else None
            if src is None:
                rec = run_requests_dir(run_dir) / f"{rid}.json"
                if not rec.is_file():
                    return {"ok": False,
                            "words": "записи заявки в прогоне нет"}
                paths["live"].parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(paths["live"], "xb") as f:
                        f.write(rec.read_bytes())
                except FileExistsError:
                    return {"ok": True, "words": "заявка уже в очереди"}
                return {"ok": True, "words": None}
            _file_replace.replace_with_retry(src, paths["live"])
            return {"ok": True, "words": None}
    except QueueBusy as e:
        return {"ok": False, "words": f"повторная подача не выполнена — {e}"}


def inherited_stamps(source_dir) -> dict:
    """Метки времени и ссылка ИСТОЧНИКА по номерам заявок: потомок
    повтора «из журнала» обязан получить их, иначе байт-идентичности
    нет (образец `package_run_name` производителя пакета)."""
    out: dict = {}
    base = run_requests_dir(source_dir)
    for folder in (base, base / HELD_DIR_NAME):
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("req-*.json")):
            rec = _read_json(p)
            if not isinstance(rec, dict):
                continue
            out.setdefault(str(rec.get("request_id")), {
                "submitted_at": rec.get("submitted_at"),
                "origin_date": ((rec.get("draft") or {}).get("origin") or {})
                .get("date"),
                "ref": (rec.get("source") or {}).get("ref")})
    return out


# ─────────────────────────────────────── доставка в службу каталога
#
# Контракт службы — проект COSMOS `KATALOG-SHAG-6-PROEKT-CLD.md` §1.1
# (доставка), §5.1 (ручки) и §8 (часть CDPM). Локальная запись первична:
# заявка уже лежит в очереди и в папке прогона, доставка — вторая
# половина пути, и её исход НИКОГДА не течёт в `index.json` (тот
# детерминирован и сверяется повтором байт в байт, §1.3): момент, код и
# слова службы ложатся квитанцией в `channel/catalog-requests/`, а
# `channel` вне сверки по построению.

#: Ручки службы: подача заявки и НЕОБЯЗАТЕЛЬНАЯ предпроверка «один
#: каталог на машину» (§8 п.7 проекта COSMOS).
SERVICE_REQUESTS_PATH = "/api/requests"
SERVICE_SESSION_PATH = "/api/session"
#: Заголовок продукта-подателя (перечень `products` службы).
SERVICE_PRODUCT = "CDPM"
#: Три попытки с растущей паузой — как у канала пакета
#: (`cdpm/channel.py`). Таймаут запроса меньше пакетного (там 120 с на
#: zip в полмегабайта): заявка — единицы килобайт JSON, а ручка панели
#: синхронна, и её худший случай (3 × таймаут + паузы) обязан оставаться
#: обозримым для человека у экрана.
DELIVERY_PAUSES_S = (0.0, 2.0, 6.0)
DELIVERY_TIMEOUT_S = 30

#: ЕДИНСТВЕННЫЙ повторяемый 4xx (§8 п.5 проекта COSMOS): каталог занят
#: выпуском — отказ временный, как сетевая ошибка. Прочие 4xx
#: детерминированы, и повтор их не снимет.
RETRY_REFUSALS = ("write-busy",)
#: КОНФЛИКТ КАНОНА: по этой записи каталога у службы уже открыта ДРУГАЯ
#: заявка. Два имени одного отказа — служба нынешнего рубежа отвечает
#: `duplicate-request` и называет ИНОЙ номер (`request_queue.submit`,
#: столкновение `open_for`), служба каталога 0.6.0 — `request-exists`
#: под ТЕМ ЖЕ номером (`_known`, иной канон); читатель понимает оба и
#: берёт номер из `extra.existing`. Файл очереди при них ОСТАЁТСЯ В
#: ОЧЕРЕДИ (П0 §11.2 п. 6: «конфликт канона — не в `sent`, не „подана
#: иным черновиком“»): свой черновик не подан, и уносить его в
#: «отправленные» значило бы называть поданным чужое.
EXISTS_REFUSALS = ("request-exists", "duplicate-request")
#: Отказ, которым служба закрывает номер до пересмотра владельцем: это
#: ФАКТИЧЕСКОЕ состояние «отклонена» с решением, а не «неизвестно».
CLOSED_REFUSALS = ("request-closed",)

#: Папка квитанций доставки внутри прогона (вне сверки повтора, §1.4).
RECEIPTS_DIR = ("channel", "catalog-requests")

# ── исход доставки (K07, контракт П0 §4.1) ─────────────────────────────
#
# Три исхода файла квитанции — закрытый перечень: доставлена (служба
# ответила ПРИКЛАДНОЙ квитанцией с идентичностью этой заявки), отказ
# (служба сказала «нет» определённо) и неизвестен (о судьбе заявки не
# известно ничего: файл остаётся в очереди, повтор идёт ТЕМ ЖЕ номером,
# новой заявки не рождается). Вид случая (`outcome_kind`) различает то,
# что §4.1 велит различать: таймаут, 5xx, два 409, канал, форму.

#: Версия файла квитанции прогона (§4.1): исход, тело квитанции по
#: схеме `receipt/1`, журнал попыток, момент последней сверки.
RECEIPT_FILE_VERSION = "receipt-file/2"
OUTCOME_DELIVERED = "доставлена"
OUTCOME_UNKNOWN = "неизвестен"
OUTCOME_REFUSED = "отказ"
OUTCOMES = (OUTCOME_DELIVERED, OUTCOME_UNKNOWN, OUTCOME_REFUSED)

#: Виды случаев доставки — по таблице §4.1 буквально (слова закрыты).
KIND_DELIVERED = "доставлена"
KIND_TIMEOUT = "таймаут"
KIND_5XX = "5xx"
KIND_EXISTS = "409 request-exists"
KIND_CLOSED = "409 request-closed"
KIND_BUSY = "409 write-busy"
KIND_CHANNEL = "401/403"
KIND_FORM = "422/400"
#: Прочий определённый отказ службы (404 `unknown-request`, иной 409):
#: таблица §4.1 перечисляет случаи, которые обязаны РАЗЛИЧАТЬСЯ, а не
#: делит на них весь мир — остаток назван своим словом, а не выдаётся за
#: «форму» или за «неизвестно».
KIND_OTHER = "отказ службы"
KIND_UNKNOWN = "неизвестен"
DELIVERY_KINDS = (KIND_DELIVERED, KIND_TIMEOUT, KIND_5XX, KIND_EXISTS,
                  KIND_CLOSED, KIND_BUSY, KIND_CHANNEL, KIND_FORM,
                  KIND_OTHER, KIND_UNKNOWN)
#: Вид случая → исход. Отказ — там, где служба сказала «нет»
#: ОПРЕДЕЛЁННО; таймаут и 5xx — «неизвестен»: они не говорят, записана
#: заявка или нет, и повтор тем же номером обязан остаться доступным.
OUTCOME_OF_KIND = {
    KIND_DELIVERED: OUTCOME_DELIVERED,
    KIND_TIMEOUT: OUTCOME_UNKNOWN,
    KIND_5XX: OUTCOME_UNKNOWN,
    KIND_UNKNOWN: OUTCOME_UNKNOWN,
    KIND_EXISTS: OUTCOME_REFUSED,
    KIND_CLOSED: OUTCOME_REFUSED,
    KIND_BUSY: OUTCOME_REFUSED,
    KIND_CHANNEL: OUTCOME_REFUSED,
    KIND_FORM: OUTCOME_REFUSED,
    KIND_OTHER: OUTCOME_REFUSED,
}
#: Сколько попыток держит журнал файла квитанции. Журнал — свидетель
#: («сколько раз стучались и что услышали»), а не архив: панель зовёт
#: сверку сколько угодно раз, и расти без предела файлу нельзя.
ATTEMPTS_KEPT = 20

LINE_REPLAY_WORDS = ("повтор: доставка не выполнялась, очередь не "
                     "тронута; состояние — по каталогу")


def service_url() -> str:
    return (os.environ.get(ENV_SERVICE_URL) or "").strip().rstrip("/")


def service_off_words() -> str:
    """Слова, когда ручка службы не настроена. Они же — слова канала на
    панели (§8 п.6 проекта COSMOS): переходный канал назван поимённо,
    а не «стадия 4»."""
    return ("доставка в службу не настроена: очередь читает инструмент "
            "import-requests --inbox на рабочем месте владельца")


def _header_safe(value: str) -> bool:
    """Значение годится заголовком HTTP (латиница-1 по RFC). Знак вне
    ASCII в токене — не «служба не отвечает», а ошибка настройки: три
    пустые попытки прятали бы её за сетевыми словами."""
    try:
        str(value or "").encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def bad_settings_words() -> str | None:
    """Почему доставка невозможна ДО первого запроса (None — возможна)."""
    if not _header_safe((os.environ.get(ENV_SERVICE_TOKEN) or "").strip()):
        return (f"токен службы каталога ({ENV_SERVICE_TOKEN}) содержит "
                f"знаки вне ASCII — заголовок HTTP их не несёт")
    if not _header_safe(service_url()):
        return (f"адрес службы каталога ({ENV_SERVICE_URL}) содержит знаки "
                f"вне ASCII")
    return None


def secret_free(text) -> str:
    """Слова БЕЗ секретов: токен машинного канала вырезается, как бы он
    ни попал в строку (сообщение библиотеки, эхо службы, адрес с ключом).

    Журнал попыток ложится файлом прогона, а прогон уезжает в пакет
    задачи и в переписку: секрет, однажды попавший в слова, оттуда уже
    не вынуть. Поэтому вырезается ЗНАЧЕНИЕ токена, а не имя заголовка:
    имя безобидно, значение — нет."""
    said = str(text or "")
    token = (os.environ.get(ENV_SERVICE_TOKEN) or "").strip()
    if token and token in said:
        said = said.replace(token, "<токен скрыт>")
    return said


def _attempts_count(receipt) -> int:
    """Сколько раз стучались — ЧИСЛОМ, из любой формы файла квитанции.

    В старой форме `attempts` — счётчик, в форме K07 — журнал попыток
    (§4.1). Панель показывает «попыток N» и до K07, и после: поле строки
    состояния держит прежний смысл, а форма файла идёт за контрактом."""
    got = (receipt or {}).get("attempts") if isinstance(receipt, dict) else 0
    if isinstance(got, list):
        return len(got)
    try:
        return int(got or 0)
    except (TypeError, ValueError):                       # pragma: no cover
        return 0


def _attempt(at: str, http, outcome: str, words) -> dict:
    """Строка журнала попыток — РОВНО четыре поля (§4.1): когда, код,
    исход, слова. Ни заголовков, ни тела, ни адреса: перечень закрыт,
    и секрету в него не попасть даже по недосмотру."""
    return {"at": at, "http": (int(http) if http is not None else None),
            "outcome": outcome, "words": secret_free(words)[:400]}


def receipt_path(run_dir, request_id: str) -> Path:
    return Path(run_dir).joinpath(*RECEIPTS_DIR) / f"{request_id}.receipt.json"


def _scrubbed(value):
    """То же значение, но БЕЗ секретов в строках — на любой глубине.

    Одно место, а не оговорка у каждого поля: слова службы приходят в
    `words`, в `problems[].message`, в `extra` и в теле квитанции, и
    перечислять их поимённо значило бы забыть один при первой же правке
    формы."""
    if isinstance(value, str):
        return secret_free(value)
    if isinstance(value, dict):
        return {k: _scrubbed(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrubbed(v) for v in value]
    return value


def write_receipt(run_dir, receipt: dict) -> Path:
    """Положить файл квитанции прогона. Секреты вырезаются ЗДЕСЬ, на
    единственной двери записи: файл уезжает в пакет задачи и в
    переписку, и вынуть из него токен задним числом уже нельзя."""
    p = receipt_path(run_dir, str(receipt.get("request_id")))
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, _dump(_scrubbed(receipt)))
    return p


def read_receipt(run_dir, request_id: str) -> dict | None:
    got = _read_json(receipt_path(run_dir, request_id))
    return got if isinstance(got, dict) else None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_refusal(body_text: str, code: int) -> dict:
    """Форма отказа службы — `{"detail": {"code", "words", "problems",
    "extra"}}`; `refusal` = `detail.words`, слова проблем —
    `detail.problems[].message`. Чужой ответ (не JSON, иная форма)
    читается честно как текст, а не роняет доставку."""
    detail = None
    try:
        doc = _loads_reply(body_text or "")
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        detail = doc.get("detail")
        if isinstance(detail, str):
            detail = {"words": detail}
    if not isinstance(detail, dict):
        detail = {"words": str(body_text or "").strip()[:400]}
    problems = [p for p in (detail.get("problems") or ())
                if isinstance(p, dict)]
    words = str(detail.get("words") or "").strip()
    if not words and problems:
        words = "; ".join(str(p.get("message") or p) for p in problems)
    if not words:
        words = f"служба отказала: HTTP {code}"
    extra = detail.get("extra")
    return {"code": _nullstr(detail.get("code")), "words": words,
            "problems": problems,
            "extra": extra if isinstance(extra, dict) else {}}


def session_precheck(_urlopen=None, _stamp=None) -> dict:
    """Предпроверка «один каталог на машину» (§8 п.7 проекта COSMOS):
    `GET <URL>/api/session` → `catalog.fingerprint` против отпечатка
    ТОГО экземпляра, которым работает CDPM (`catalog_bridge.stamp()`).

    Расхождение — отказ доставки словами и БЕЗ записи в службу. Нет
    ручки (404) или нет отпечатка — предпроверка пропускается СЛОВАМИ,
    а не молча: пропуск проверки обязан быть виден. Недоступная сеть
    здесь не отказ — о ней честнее скажет сама подача (три попытки)."""
    out = {"ok": True, "checked": False, "words": None, "service": None,
           "cdpm": None}
    url = service_url()
    token = (os.environ.get(ENV_SERVICE_TOKEN) or "").strip()
    if not url or not token:
        out["words"] = service_off_words()
        return out
    bad = bad_settings_words()
    if bad:
        out["words"] = f"предпроверка каталога пропущена: {bad}"
        return out
    try:
        mine = str((_stamp or catalog_bridge.stamp())["fingerprint"])
    except Exception as e:                                # noqa: BLE001
        out["words"] = ("предпроверка каталога пропущена: отпечаток "
                        f"каталога CDPM не получен ({str(e)[:160]})")
        return out
    out["cdpm"] = mine
    opener = _urlopen or urllib.request.urlopen
    req = urllib.request.Request(
        url + SERVICE_SESSION_PATH, method="GET",
        headers={"X-Catalog-Product": SERVICE_PRODUCT,
                 "X-Catalog-Token": token})
    try:
        with opener(req, timeout=DELIVERY_TIMEOUT_S) as resp:
            body = _loads_reply(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            out["words"] = ("предпроверка «один каталог на машину» "
                            "пропущена: у службы нет ручки "
                            f"{SERVICE_SESSION_PATH}")
        else:
            out["words"] = ("предпроверка каталога пропущена: служба "
                            f"ответила HTTP {e.code}")
        return out
    except Exception as e:                                # noqa: BLE001
        out["words"] = ("предпроверка каталога пропущена: служба не "
                        f"ответила ({str(e)[:160]})")
        return out
    theirs = _nullstr(((body or {}).get("catalog") or {}).get("fingerprint")) \
        if isinstance(body, dict) else None
    if not theirs:
        out["words"] = ("предпроверка каталога пропущена: служба не "
                        "назвала отпечаток своего каталога")
        return out
    out["service"] = theirs
    out["checked"] = True
    if theirs != mine:
        out["ok"] = False
        out["words"] = ("служба смотрит в другой каталог: у службы "
                        f"{theirs}, у CDPM {mine}")
    return out


#: Точный тип содержимого квитанции (§4.1 п. «г»). Судится ТОЧНО, а не
#: по началу строки: `application/jsonp` и `application/json-bogus`
#: начинаются теми же буквами, но это ИНЫЕ типы (K07C-N1).
JSON_MEDIA_TYPE = "application/json"


def json_media_type_problem(content_type) -> str | None:
    """Чем тип содержимого ответа НЕ `application/json` — либо None.

    Разбор по RFC 9110 §8.3: сам тип — до первой «;», без окружающих
    пробелов, регистр ничего не значит (`Application/JSON` — тот же
    тип); параметры (`; charset=utf-8`) допустимы и не судятся.
    Совпадение ТОЧНОЕ: `application/jsonp` и `application/json-bogus` —
    другие типы, а прежняя проверка «начинается с application/json»
    принимала их за квитанцию.

    Заголовка нет вовсе (`None`) — тоже беда: ответ не назвал себя JSON,
    и исход «неизвестен». Прежде отсутствие заголовка не судилось «ради
    стенда» — но поблажка стенду меняла ПРОДУКТОВЫЙ закон, а подставной
    ответ обязан давать заголовок ровно так же, как его даёт служба."""
    if content_type is None:
        return f"тип содержимого не назван, а квитанция — {JSON_MEDIA_TYPE}"
    media = str(content_type).split(";")[0].strip().lower()
    if media != JSON_MEDIA_TYPE:
        return f"тип содержимого {content_type!r}, а не {JSON_MEDIA_TYPE}"
    return None


def nested_request_problems(body) -> list[str]:
    """Чем вложенная запись `request` НЕ является свидетельством ТОЙ ЖЕ
    заявки и ТОЙ ЖЕ редакции (пусто — является; вложения нет вовсе —
    тоже пусто, и это законное тело).

    ЗАКОН (K07C-F1). Решение подтверждает либо заменяет известное
    решение ТОЛЬКО вместе с состоянием той же заявки и той же редакции.
    Проверка идентичности ВНЕШНЕЙ квитанции доверия на произвольное
    вложение не распространяет: взять состояние сверху, а доказательство
    старшинства — из другого носителя, не доказав их согласованность,
    нельзя. Инверсия дефекта: тело с внешней квитанцией «принята 09:00»
    и вложенным отклонением 11:00 откатывало известное отклонение 10:00
    — чужие слова заменяли слова решения ЭТОЙ заявки, а флаг устаревания
    ложную атрибуцию не оправдывал.

    Оба носителя служба складывает из ОДНОЙ записи
    (`catalog_service.request_queue.receipt`: `request` — та же запись
    целиком, `state` и `decision` — её же состояние и решение), поэтому
    у настоящего тела согласованы номер, раздел, запись, канон
    черновика, состояние и `by`/`words`/`at` решения. Здесь судится
    СОГЛАСОВАННОСТЬ носителей одного тела, а не идентичность заявки
    (её судит общая функция читателя `validate_receipt` по отправленной
    записи) — потому проверка и живёт у клиента отдельной функцией.

    ЧИСЛОВАЯ версия судится как версия редакции ТОЛЬКО там, где её
    называют ОБА носителя: у канала 0.5 квитанция несёт признак наличия
    решения (`0` — нет, `1` — есть), а запись заявки поля версии не
    имеет вовсе, и принимать признак за номер редакции нельзя — иначе
    законное тело объявлялось бы противоречивым, и закон стал бы
    недостижимым (с каталога 0.6.0 версия появится у обоих)."""
    if not isinstance(body, dict) or "request" not in body:
        return []
    вложено = body.get("request")
    if not isinstance(вложено, dict):
        return [f"вложенная запись заявки не объект JSON: "
                f"{type(вложено).__name__}"]
    беды: list[str] = []
    for поле in ("request_id", "section", "record_id"):
        if вложено.get(поле) != body.get(поле):
            беды.append(f"{поле} вложенной записи {вложено.get(поле)!r} не "
                        f"равен {body.get(поле)!r} самой квитанции")
    if "draft" in вложено:
        # Канон — законом ЧИТАТЕЛЯ (`template_catalog.requests`), тем же,
        # которым его считает служба: черновика нет — канона нет (None),
        # а не хеш от «null».
        канон = _record_canon(вложено.get("draft"))
        if body.get("canon") != канон:
            беды.append(f"канон черновика вложенной записи {канон!r} не равен "
                        f"канону квитанции {body.get('canon')!r}")
    if вложено.get("state") != body.get("state"):
        беды.append(f"состояние вложенной записи {вложено.get('state')!r} не "
                    f"равно состоянию квитанции {body.get('state')!r}")
    своё = body.get("decision")
    её = вложено.get("decision")
    своё = своё if isinstance(своё, dict) else {}
    её = её if isinstance(её, dict) else {}
    for поле, подпись in (("by", "автор"), ("words", "слова"), ("at", "время")):
        if _nullstr(её.get(поле)) != _nullstr(своё.get(поле)):
            беды.append(f"{подпись} решения вложенной записи: {её.get(поле)!r}; "
                        f"в квитанции: {своё.get(поле)!r} — значения не совпадают")
    моя_в = _as_number(своё.get("version"))
    её_в = _as_number(её.get("version"))
    if моя_в is not None and её_в is not None and моя_в != её_в:
        беды.append(f"версия решения вложенной записи {её_в!r} не равна "
                    f"версии решения квитанции {моя_в!r}")
    return беды


def _receipt_status_problems(status, body) -> list[str]:
    """Согласие HTTP-кода и тела — также для сохранённого ответа импорта."""
    problems = []
    if status not in (201, 200):
        problems.append(f"код ответа {status} (квитанция — 201 либо 200)")
    if not isinstance(body, dict):
        return problems
    if status == 200 and body.get("note") != REPEAT_NOTE:
        problems.append(f"200 без слов «{REPEAT_NOTE}» — это не повторная выдача "
                        "той же квитанции")
    if status == 201 and (body.get("note") == REPEAT_NOTE
                          or body.get("repeat") is True):
        problems.append("201 с признаком повторной выдачи — код и тело "
                        "противоречат друг другу")
    return problems


def receipt_problems(status: int, body, sent=None, content_type=None
                     ) -> list[str]:
    """Чем ответ службы НЕ является квитанцией (пусто — является).

    ЗАКОН ОДИН с инструментом импорта COSMOS
    (`catalog_service.import_requests.receipt_problems`) — и буквально
    один в той части, где ошибиться легче всего: ТЕЛО судит общая
    функция читателя каталога `template_catalog.receipts
    .validate_receipt(body, sent=…)` — схема `receipt/1`, закрытый
    перечень состояний, идентичность заявки (номер, раздел, запись,
    канон черновика). Здесь добавляется только ТРАНСПОРТ, о котором
    тело знать не может: код ответа, тип содержимого (ТОЧНЫЙ
    `application/json`, отсутствие заголовка — не квитанция) и согласие
    признака повтора с кодом (§4.1 п. «а» и «г»), — и согласованность
    носителей одного тела между собой (`nested_request_problems`,
    K07C-F1): её общая функция не судит, потому что судит идентичность
    заявки по отправленной записи, а не связь вложения с квитанцией.

    Транспортная половина у двух клиентов своя по замыслу COSMOS (тело
    о своём HTTP-коде не знает, и в общий пакет читателя каталога она не
    входит) — равенство двух перечней судит стенд (раздел 22 тестов
    CDPM): разойдясь, они разошлись бы молча, а этим их расхождение
    становится красным.

    `sent` — отправленная запись очереди; форма записи (черновик семьи
    либо набор видов исполнителя) роли не играет: сверяется то, что в
    записи есть."""
    беды = _receipt_status_problems(status, None)
    беда = json_media_type_problem(content_type)
    if беда:
        беды.append(беда)
    беды.extend(validate_receipt(body, sent=sent))
    if not isinstance(body, dict):
        return беды
    # Согласованность носителей ОДНОГО тела (K07C-F1): необязательная
    # вложенная запись `request` участвует в доказательстве решения, и
    # противоречивое либо чужое вложение — не квитанция этой редакции.
    беды.extend(nested_request_problems(body))
    if status in (200, 201):
        беды.extend(_receipt_status_problems(status, body))
    return беды


def delivery_kind(status, refusal_code=None) -> str:
    """Вид случая доставки по таблице §4.1 — из кода ответа и кода
    отказа службы. Слова закрыты (`DELIVERY_KINDS`): исход, названный
    свободной строкой, панель показать не сможет, а тест — сравнить."""
    if status is None:
        return KIND_TIMEOUT                     # связь не состоялась
    code = int(status)
    if code == 409:
        if refusal_code in EXISTS_REFUSALS:
            return KIND_EXISTS
        if refusal_code in CLOSED_REFUSALS:
            return KIND_CLOSED
        if refusal_code in RETRY_REFUSALS:
            return KIND_BUSY
        return KIND_OTHER
    if code in (401, 403):
        return KIND_CHANNEL
    if code in (400, 422):
        return KIND_FORM
    if 500 <= code <= 599:
        return KIND_5XX
    if 400 <= code <= 499:
        return KIND_OTHER
    return KIND_UNKNOWN                         # 2xx без квитанции, 3xx


def _state_words_of(kind: str, refusal: dict | None = None,
                    note: str | None = None, rid: str | None = None,
                    problems: str | None = None,
                    words: str | None = None) -> str:
    """Слова состояния для экрана по исходу доставки — ОДНО место
    соответствия «ответ службы → что видит аналитик» (§4.1, таблица).

    `words` — слова случая (у отказа службы это её собственные слова, у
    ответа без квитанции — перечень того, чего не хватило); не переданы
    — берутся из отказа."""
    refusal = refusal or {}
    if words is None:
        words = refusal.get("words")
    words = str(words or "")
    extra = refusal.get("extra") or {}
    if kind == KIND_DELIVERED:
        return "подана в службу каталога" + (f": {note}" if note else "")
    if kind == KIND_EXISTS:
        # П0 §11.2 п. 6: «конфликт канона — НЕ в `sent`, не „подана иным
        # черновиком“». Прежние слова выдавали чужой черновик за свою
        # подачу; теперь названы оба номера, и файл остаётся в очереди
        # до решения.
        existing = _nullstr(extra.get("existing"))
        ref = _ref_head(extra.get("ref"))
        чей = f" (иной черновик из {ref})" if ref else " (иной черновик)"
        if existing and rid and existing != rid:
            голова = f"конфликт канона: у службы заявка {existing}{чей}"
            хвост = f"; свой номер {rid} остаётся в очереди до решения"
        elif existing:
            голова = f"конфликт канона: номер {existing} занят{чей}"
            хвост = "; заявка остаётся в очереди до решения"
        else:
            голова = f"конфликт канона: {words}"
            хвост = "; заявка остаётся в очереди до решения"
        return голова + хвост
    if kind == KIND_CLOSED:
        # Фактическое состояние «отклонена» с решением владельца — не
        # успех и не «неизвестно» (§4.1). Слова решения берутся из
        # `extra.decision` службы, и только при их отсутствии — из слов
        # отказа.
        dec = extra.get("decision") if isinstance(extra.get("decision"),
                                                  dict) else {}
        said = _nullstr(dec.get("words")) or words
        return f"{REQUEST_REJECTED}: {said}"
    if kind in (KIND_BUSY, KIND_TIMEOUT):
        return f"в очереди, доставка не удалась: {words}"
    if kind in (KIND_5XX, KIND_UNKNOWN):
        # О судьбе заявки не известно ничего: файл в очереди, повтор —
        # тем же номером.
        return f"{UNCONFIRMED_WORDS}: {words}"
    return f"не принята: {problems or words}"


def last_outcome(receipt) -> str | None:
    """Исход ПОСЛЕДНЕЙ попытки — что сделал ЭТОТ вызов доставки или
    сверки.

    Отличается от `outcome` файла намеренно: `outcome` говорит о судьбе
    ЗАЯВКИ (доказанная доставка переживает неудачную сверку), а счётчики
    ответа панели («доставлено N, отказано M») говорят о том, что
    случилось СЕЙЧАС. Сложить их в одно поле значило бы либо стирать
    доказанное, либо докладывать об успехе, которого в этот раз не
    было."""
    if not isinstance(receipt, dict):
        return None
    attempts = receipt.get("attempts")
    if isinstance(attempts, list) and attempts \
            and isinstance(attempts[-1], dict):
        return attempts[-1].get("outcome")
    return receipt.get("outcome")


def _keep_prior_delivery(receipt: dict, prior, *, sent=None) -> dict:
    """Вернуть в файл ПРЕЖНЮЮ доставку, если связь сегодня не
    состоялась.

    Доказанного доказательство не отменяется неудачей связи: заявка,
    принятая службой вчера, принята и сегодня, когда сеть молчит.
    Прежняя форма файла подтверждения не хранила — первая же неудачная
    сверка стирала его (K08 назвал это границей, закрываемой K07).
    Определённый ответ службы — иное дело: он новее, и его исход
    остаётся."""
    proof = receipt_confirmed(prior, str(receipt.get("request_id")), sent=sent)
    if proof is None:
        return receipt
    receipt.update({"outcome": OUTCOME_DELIVERED,
                    "outcome_kind": KIND_DELIVERED,
                    "receipt": dict(prior["receipt"]), "delivered": True,
                    "state": proof["state"], "note": proof["note"],
                    "state_words": _state_words_of(
                        KIND_DELIVERED, {}, proof["note"],
                        proof["request_id"], None, "")})
    return receipt


def _blank_receipt(rid: str, url: str | None, prior=None) -> dict:
    """Пустой файл квитанции формы `receipt-file/2` (§4.1).

    Журнал попыток ПРОДОЛЖАЕТСЯ с прежнего файла: повтор по тому же
    ключу — не новая заявка, и история «стучались трижды, получили
    таймаут, потом 201» обязана читаться целиком. Прежняя форма файла
    (без версии) журнала не несла — её попытки считались числом, и от
    них остаётся только счёт."""
    старые = []
    if isinstance(prior, dict) and prior.get("schema_version") \
            == RECEIPT_FILE_VERSION:
        старые = [a for a in (prior.get("attempts") or ())
                  if isinstance(a, dict)]
    return {"schema_version": RECEIPT_FILE_VERSION, "request_id": rid,
            "outcome": OUTCOME_UNKNOWN, "outcome_kind": KIND_UNKNOWN,
            "receipt": None, "attempts": list(старые),
            "last_checked_at": None, "known": None, "delivered": False,
            "http": None, "refusal": None, "words": None, "problems": [],
            "extra": {}, "at": None, "url": url, "state": None, "note": None,
            "queue": "outbox", "state_words": None}


def _seal(receipt: dict, kind: str, *, http=None, words: str = "",
          refusal: dict | None = None, body=None, at: str | None = None,
          problems: str | None = None, journal: bool = True) -> dict:
    """Записать в файл квитанции ИСХОД одной попытки: вид случая, исход,
    код, слова, тело квитанции — и строку журнала попыток.

    `journal=False` — итог БЕЗ новой строки журнала: попытка уже
    записана, а слова итога лишь дополняются («до того — отказ …»).
    Иначе три попытки давали бы четыре строки."""
    at = at or _now()
    outcome = OUTCOME_OF_KIND.get(kind, OUTCOME_UNKNOWN)
    refusal = refusal or {}
    said = secret_free(words)
    receipt.update({
        "outcome": outcome, "outcome_kind": kind, "http": http,
        "words": said, "at": at, "last_checked_at": at,
        "refusal": refusal.get("code"),
        "problems": refusal.get("problems") or [],
        "extra": refusal.get("extra") or {},
        "receipt": dict(body) if (kind == KIND_DELIVERED
                                  and isinstance(body, dict)) else None,
        "delivered": outcome == OUTCOME_DELIVERED,
    })
    тело = body if isinstance(body, dict) else {}
    receipt["state"] = _nullstr(тело.get("state")) \
        if kind == KIND_DELIVERED else None
    receipt["note"] = _nullstr(тело.get("note")) \
        if kind == KIND_DELIVERED else None
    receipt["state_words"] = _state_words_of(
        kind, refusal, receipt["note"], receipt.get("request_id"), problems,
        said)
    if journal:
        receipt["attempts"] = ((receipt.get("attempts") or [])
                               + [_attempt(at, http, outcome, said)]
                               )[-ATTEMPTS_KEPT:]
    return receipt


def answer_decision(doc) -> dict | None:
    """Решение, НАЗВАННОЕ САМИМ ответом службы, — либо None (не назвал).

    Служба отдаёт на приём (201) и на повторную выдачу (200) ПОЛНУЮ
    запись заявки в `request` (`catalog_service/request_queue.py`,
    `submit`/`_known`), а квитанция `receipt/1` (§4.1) несёт своё поле
    `decision`. Читаются оба носителя: старшинство доказывает САМО
    решение — его `by`/`words`/`at` и числовая `version`, когда она
    есть, — а не время получения ответа (K08, F1). Ответ, назвавший
    «решения нет» (три `null`), — тоже свидетельство и отличается от
    ответа, не назвавшего решения вовсе.

    Вложение участвует в доказательстве ТОЛЬКО СВЯЗАННЫМ с самой
    квитанцией (`nested_request_problems`, K07C-F1): его номер, раздел,
    запись, канон черновика, состояние и `by`/`words`/`at` решения — те
    же, что у квитанции. Несвязанное вложение безусловного приоритета не
    получает и решения не называет: взять состояние сверху, а
    доказательство старшинства — из другого носителя нельзя.

    Первой читается ЗАПИСЬ (`request`), потом собственное поле
    квитанции, — и вот почему (точка встречи K07 и K08). Решение
    сравнивается с решением ЗАПИСИ КАТАЛОГА, а до каталога 0.6.0 записи
    поля версии не имеют вовсе (схема заявки закрыта тремя полями `by`,
    `at`, `words`), тогда как квитанция `receipt/1` числовую версию
    несёт ВСЕГДА — и служба сама предупреждает, что до 0.6.0 её
    `version` говорит лишь «решение есть либо нет», а РЕДАКЦИИ различает
    момент (`catalog_service/request_queue.decision_of`). Взяв версию-
    признак за номер редакции, читатель сравнивал бы число с моментом —
    разные шкалы, и старшинство не доказывалось бы НИКОГДА. Запись же в
    том же теле — решение в той самой форме, в какой его хранит каталог:
    момент сравнивается моментом, версия — версией. С каталога 0.6.0
    номер редакции появляется у записи, и разницы между носителями не
    останется."""
    doc = doc if isinstance(doc, dict) else {}
    if nested_request_problems(doc):
        # Носители ОДНОГО тела противоречат друг другу либо вложение — о
        # чужой заявке: решения этой редакции такое тело не называет
        # вовсе (K07C-F1). Взять «хоть какое-нибудь» из двух нельзя —
        # неизвестно, который носитель лжёт, а состояние читается сверху.
        return None
    found = None
    for src in (doc.get("request"), doc):
        dec = src.get("decision") if isinstance(src, dict) else None
        if not isinstance(dec, dict):
            continue
        if any(dec.get(k) is not None
               for k in ("by", "words", "at", "version")):
            return dict(dec)
        found = dict(dec) if found is None else found
    return found


def _canon_conflict(receipt) -> str | None:
    """Номер СУЩЕСТВУЮЩЕЙ заявки, названный отказом службы о конфликте
    канона (`request-exists` / `duplicate-request`), — либо None."""
    if not isinstance(receipt, dict):
        return None
    if receipt.get("refusal") not in EXISTS_REFUSALS:
        return None
    return _nullstr((receipt.get("extra") or {}).get("existing"))


def canonical_of(receipt, request_id: str) -> str:
    """Канонический номер ОБЩЕЙ заявки — до чтения решения (K08, F3).

    Служба отказом называет заявку, которая по этой записи уже открыта
    («по записи ... уже есть открытая заявка <номер>»): решение общей
    заявки лежит у НЕЁ, а не у отвергнутого черновика этого прогона.
    Отказа о конфликте канона нет — номер собственный."""
    return _canon_conflict(receipt) or str(request_id)


def deliver_one(request: dict, run_dir, outbox, *, _urlopen=None,
                _sleep=time.sleep) -> dict:
    """Доставка ОДНОЙ заявки: `POST <URL>/api/requests`, тело — запись
    заявки §6 схемы целиком, как она лежит в очереди.

    ЗАКОН ИСХОДА (контракт П0 §4.1, K07-клиент). Исход «доставлена»
    только тогда, когда служба ответила ПРИКЛАДНОЙ КВИТАНЦИЕЙ с
    идентичностью ЭТОЙ заявки: (а) 201 с телом по схеме `receipt/1`
    либо 200 с телом по схеме и словами «уже подана»; (б) номер тела
    равен отправленному; (в) состояние — слово закрытого перечня;
    (г) тип содержимого — ТОЧНО `application/json` (параметры вроде
    `; charset=utf-8` допустимы; заголовка нет вовсе — не квитанция);
    (д) вложенная запись `request`, если она есть, — о ТОЙ ЖЕ заявке и
    ТОЙ ЖЕ редакции, что и сама квитанция: номер, раздел, запись, канон,
    состояние и `by`/`words`/`at` решения согласованы с ней (иначе
    смешанное тело откатывало бы известное решение чужими словами).
    Всё прочее — исход `неизвестен`: файл ОСТАЁТСЯ В ОЧЕРЕДИ, повтор
    идёт ТЕМ ЖЕ ключом (номер содержательный, второго не бывает),
    попытка записывается в журнал. Прежде квитанцией считался любой ответ без `HTTPError` —
    страница входа площадки, пустое тело 204 и ответ с ЧУЖИМ номером
    засчитывались подачей, а файл уезжал в `sent/`.

    Форма записи (черновик семьи либо набор видов исполнителя) закону
    безразлична: тело уходит байт в байт тем, что лежит в очереди, а
    идентичность сверяет общая функция по тем полям, что в записи есть.

    Повторяются только сетевые ошибки и `409 write-busy` (§8 п.5
    контракта); прочие ответы детерминированы. Возвращает файл
    квитанции `receipt-file/2` — он же ложится в
    `channel/catalog-requests/`; индекс доставка не трогает."""
    rid = str(request.get("request_id") or "")
    url = service_url()
    token = (os.environ.get(ENV_SERVICE_TOKEN) or "").strip()
    body = _dump(request)
    req_url = url + SERVICE_REQUESTS_PATH
    prior = _saved_delivery_receipt(run_dir, rid, outbox, sent=request)
    receipt = _blank_receipt(rid, req_url, prior)
    opener = _urlopen or urllib.request.urlopen
    last_err, prior_refusal, сделано = "", None, 0
    for pause in DELIVERY_PAUSES_S:
        if pause:
            _sleep(pause)
        сделано += 1                    # попытки ЭТОГО вызова, не журнала
        http_req = urllib.request.Request(
            req_url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "X-Catalog-Product": SERVICE_PRODUCT,
                     "X-Catalog-Token": token})
        try:
            with opener(http_req, timeout=DELIVERY_TIMEOUT_S) as resp:
                code = int(getattr(resp, "status", 0)
                           or resp.getcode() or 0)
                raw = resp.read().decode("utf-8", "replace")
                ctype = _content_type_of(resp)
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:                             # noqa: BLE001
                raw = ""
            refusal = parse_refusal(raw, e.code)
            kind = delivery_kind(e.code, refusal["code"])
            слова = refusal["words"]
            if kind == KIND_FORM and refusal["problems"]:
                слова = "; ".join(str(p.get("message") or p)
                                  for p in refusal["problems"])
            _seal(receipt, kind, http=int(e.code), words=refusal["words"],
                  refusal=refusal, problems=слова)
            if kind == KIND_BUSY and сделано < len(DELIVERY_PAUSES_S):
                last_err, prior_refusal = refusal["words"], refusal["code"]
                continue
            break
        except Exception as e:                            # noqa: BLE001
            last_err = secret_free(str(e))[:200]
            _seal(receipt, KIND_TIMEOUT, http=None,
                  words=f"служба не отвечает: {last_err}")
            continue
        try:
            doc = _loads_reply(raw) if str(raw).strip() else None
        except ValueError:
            doc = None
        беды = receipt_problems(code, doc, request, ctype)
        if not беды:
            _seal(receipt, KIND_DELIVERED, http=code, words="", body=doc)
        else:
            # Тот же ответ словами, что и у инструмента импорта: чего
            # именно не хватило, и что делать дальше.
            _seal(receipt, delivery_kind(code), http=code,
                  words=(f"ответ службы не является квитанцией "
                         f"(HTTP {code}): " + "; ".join(беды)
                         + " — заявка остаётся в очереди, повтор тем же "
                           "номером"))
        break
    else:
        # Последняя попытка кончилась сетевой ошибкой: три сделаны,
        # заявка остаётся в очереди — «Повторить подачу» её добьёт.
        _seal(receipt, KIND_TIMEOUT, http=None, journal=False,
              words=(f"служба не отвечает: {last_err}"
                     + (f" (до того — отказ {prior_refusal})"
                        if prior_refusal else "")))
        _keep_prior_delivery(receipt, prior, sent=request)
    # Файл очереди уходит в `sent/`, ТОЛЬКО когда заявка доставлена —
    # то есть служба ответила прикладной квитанцией с идентичностью
    # этой заявки. `409 request-exists` в `sent/` файл больше не уводит
    # (П0 §11.2 п. 6): свой черновик не подан, конфликт канона ждёт
    # решения, и уносить файл в «отправленные» значило бы называть
    # поданным чужое.
    paths = _outbox_paths(Path(outbox), rid)
    if receipt_confirmed(receipt, rid, sent=request) is not None:
        # Живой файл уходит в `sent/` (при занятом `sent/` — дубль, в
        # `duplicates/`). Не ушёл — квитанция называет, где файл лежит
        # НА САМОМ ДЕЛЕ: доставка по явному номеру идёт и от записи
        # прогона, и файла в очереди может не быть вовсе (забрала
        # читающая сторона, отозван) — тогда None, а не выдуманное
        # «sent». Квитанция говорит о положении, а не о намерении.
        receipt["queue"] = _move_to_sent(paths) or _queue_where(paths)
    else:
        # Тот же закон и у прочих исходов: квитанция называет
        # фактическое положение файла (outbox / withdrawn / duplicates
        # / нигде), а не намерение «outbox» (третье ревью, N1).
        receipt["queue"] = _queue_where(paths)
    write_receipt(run_dir, receipt)
    return receipt


def _content_type_of(resp) -> str | None:
    """Тип содержимого ответа; заголовка нет — None.

    None не значит «тип не судится»: ответ, не назвавший свой тип, —
    не квитанция (`json_media_type_problem`, K07C-N1), исход
    «неизвестен». Прежде подставному клиенту стенда делалась поблажка
    («стенд подменяет клиента, а не сеть, заголовков с него не
    требуем») — и эта поблажка меняла ПРОДУКТОВЫЙ закон: подставной
    ответ обязан давать заголовок ровно так же, как его даёт служба."""
    headers = getattr(resp, "headers", None)
    try:
        return headers.get("Content-Type") if headers is not None else None
    except Exception:                                     # noqa: BLE001
        return None


def deliverable(run_dir, outbox) -> list[str]:
    """Номера заявок ЭТОГО прогона, которым доставка ещё нужна: поданы
    от него и лежат в очереди живым файлом."""
    out = []
    for row in (read_index(run_dir).get("rows") or {}).values():
        if not isinstance(row, dict) \
                or row.get("composed") not in SUBMITTED_STATES:
            continue
        rid = str(row.get("request_id") or "")
        if rid and rid not in out \
                and _outbox_paths(Path(outbox), rid)["live"].is_file():
            out.append(rid)
    return sorted(out)


def deliver(run_dir, request_ids=None, outbox=None, *, replay: bool = False,
            _urlopen=None, _sleep=time.sleep) -> dict:
    """Доставка заявок прогона в службу каталога: перечисленных номеров
    либо (без номеров) всех, что ждут в очереди.

    Доставка ИДЕМПОТЕНТНА по номеру: та же заявка вторым нажатием даёт
    `200 … "уже подана"`. При повторе прогона (`replay`) доставки нет
    вовсе — очередь и служба остаются нетронутыми, слова об этом идут в
    квитанцию, но не в индекс."""
    run_dir = Path(run_dir)
    outbox = Path(outbox) if outbox is not None else outbox_root()
    ids = [str(x) for x in (request_ids or ()) if str(x or "").strip()]
    if not ids:
        ids = deliverable(run_dir, outbox)
    out = {"refusal": None, "note": None, "receipts": [],
           "delivered": 0, "refused": 0, "requests": ids}
    if replay:
        out["refusal"] = LINE_REPLAY_WORDS
        for rid in ids:
            # Квитанция повтора — не транспорт: потомок в службу не
            # ходил, и попыток у него нет ни одной (журнал пуст, а не
            # унаследован от источника).
            свиток = _blank_receipt(rid, None)
            свиток.update({"refusal": "replay", "words": LINE_REPLAY_WORDS,
                           "at": _now(), "last_checked_at": _now(),
                           "attempts": []})
            write_receipt(run_dir, свиток)
        return out
    if not service_configured():
        out["refusal"] = service_off_words()
        return out
    bad = bad_settings_words()
    if bad:
        out["refusal"] = bad
        return out
    if not ids:
        out["refusal"] = "в очереди нет заявок этого прогона к доставке"
        return out
    pre = session_precheck(_urlopen=_urlopen)
    out["note"] = pre.get("words")
    if not pre.get("ok"):
        # Расхождение отпечатков — отказ ДО записи в службу.
        out["refusal"] = pre.get("words")
        return out
    for rid in ids:
        paths = _outbox_paths(outbox, rid)
        src = paths["live"] if paths["live"].is_file() else \
            (run_requests_dir(run_dir) / f"{rid}.json")
        request = _read_json(src)
        if not isinstance(request, dict):
            out["receipts"].append(
                {"request_id": rid, "delivered": False,
                 "words": "записи заявки нет ни в очереди, ни в прогоне",
                 "state_words": "не доставлена: записи заявки нет"})
            out["refused"] += 1
            continue
        receipt = deliver_one(request, run_dir, outbox, _urlopen=_urlopen,
                              _sleep=_sleep)
        out["receipts"].append(receipt)
        # Счёт — по ИСХОДУ ЭТОГО ВЫЗОВА (последняя попытка), а не по
        # судьбе заявки: доказанная прежде доставка переживает неудачную
        # сверку, но докладывать о ней как о сегодняшнем успехе нельзя.
        if last_outcome(receipt) == OUTCOME_DELIVERED:
            out["delivered"] += 1
        else:
            out["refused"] += 1
    return out


# ───────────────────────────────── сверка карантина со службой (§4.3)
#
# Файл очереди в `sent/` без прикладной квитанции — карантин «подана без
# подтверждения»: подача этого прогона ничем не доказана. Снять карантин
# можно ТОЛЬКО сверкой ПО КЛЮЧУ и БЕЗ НОВОЙ ПОДАЧИ: `GET
# /api/requests/{номер}`, а где ручки карточки нет — предпросмотр
# `POST /api/requests/dry-run` (он не пишет ни записи, ни замка, ни
# строки журнала). Сверка повторяемая и идемпотентная: мир от неё не
# меняется, второй вызов даёт то же самое.

#: Ручка карточки заявки (сверка по ключу) и предпросмотр — запасной
#: путь, когда карточки у службы нет.
SERVICE_REQUEST_PATH = "/api/requests/"
SERVICE_DRY_RUN_PATH = "/api/requests/dry-run"
#: Слова сверки, когда служба заявки не знает: подача не дошла.
UNKNOWN_AT_SERVICE = ("службе заявка неизвестна: подача не дошла — файл "
                      "возвращён в очередь, повтор тем же номером")


def _service_call(url: str, *, data=None, _urlopen=None) -> tuple:
    """Один запрос службе: `(код, текст, тип содержимого, ошибка связи)`.

    Отказ службы — это ОТВЕТ (код и тело), а не исключение: тело отказа
    несёт слова, ради которых всё и затевалось. Исключением остаётся
    только «не дозвонились» — тогда код None и слова ошибки."""
    token = (os.environ.get(ENV_SERVICE_TOKEN) or "").strip()
    headers = {"X-Catalog-Product": SERVICE_PRODUCT, "X-Catalog-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, method=("POST" if data is not None else "GET"),
        headers=headers)
    opener = _urlopen or urllib.request.urlopen
    try:
        with opener(req, timeout=DELIVERY_TIMEOUT_S) as resp:
            code = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            return (code, resp.read().decode("utf-8", "replace"),
                    _content_type_of(resp), None)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:                                 # noqa: BLE001
            raw = ""
        return int(e.code), raw, _content_type_of(e), None
    except Exception as e:                                # noqa: BLE001
        return None, "", None, secret_free(str(e))[:200]


def _queue_record(run_dir, request_id: str, outbox) -> dict | None:
    """Отправленная запись заявки — из очереди (живой файл, `sent/`,
    `duplicates/`, `withdrawn/`) либо из папки прогона. По ней сверяется
    идентичность ответа службы: без неё судится только форма."""
    paths = _outbox_paths(Path(outbox), str(request_id))
    for key in ("live", "sent", "duplicates", "withdrawn"):
        got = _read_json(paths[key])
        if isinstance(got, dict):
            return got
    return _local_record(run_dir, str(request_id))


def card_problems(body, sent=None) -> list[str]:
    """Чем ответ службы НЕ является карточкой ЭТОЙ заявки (пусто —
    является).

    Карточка (`GET /api/requests/{номер}`) — сама запись заявки, а не
    квитанция: своей схемы у неё нет, и «доставлена» она не доказывает
    (тот же номер мог доставить ДРУГОЙ прогон — номер содержательный).
    Судится ровно то же, что и у квитанции: ТА ЛИ это заявка — номер,
    раздел, запись, канон черновика."""
    if not isinstance(body, dict):
        return [f"ответ службы не объект JSON: {type(body).__name__}"]
    беды: list[str] = []
    sent = sent if isinstance(sent, dict) else {}
    отправлен = sent.get("request_id")
    if отправлен is not None \
            and str(body.get("request_id") or "") != str(отправлен):
        беды.append(f"номер в ответе {body.get('request_id')!r} не тот, что "
                    f"отправлен ({отправлен!r})")
    for поле in ("section", "record_id"):
        было = sent.get(поле)
        if было is not None and body.get(поле) != было:
            беды.append(f"{поле} в ответе {body.get(поле)!r}, а в заявке "
                        f"{было!r}")
    if "draft" in sent and draft_canon(body.get("draft")) \
            != draft_canon(sent.get("draft")):
        беды.append("канон черновика в ответе не равен канону отправленного")
    if not _nullstr(body.get("state")):
        беды.append("в ответе нет состояния заявки")
    return беды


def _known_of_card(card: dict) -> dict:
    """Что прогон узнал о заявке из карточки службы: состояние, решение
    и прогон-источник. Это ЗНАНИЕ, а не доказательство своей подачи."""
    return {"state": _nullstr(card.get("state")),
            "decision": decision_of(card),
            "source_run": _ref_head((card.get("source") or {}).get("ref"))
            or None,
            "at": _now()}


def _back_to_queue(paths: dict) -> str | None:
    """Файл из `sent/` — ОБРАТНО в очередь: служба заявки не знает,
    значит подача не дошла, и «отправленным» файл назван по ошибке.
    Живой файл уже есть — не трогаем (он и есть очередь). Возвращает
    «outbox», когда файл вернулся; иначе None.

    Координация — ОДНА и та же на всё изменение состояния очереди
    (K08, F2): замок номера на участке и занятие места «если свободно»
    (`claim_with_retry`), а не замена. Иначе возврат из карантина
    затирал бы живой файл, положенный другим прогоном ровно между
    проверкой и переносом, — тот самый разрыв check/act, который замок
    и закрывает."""
    try:
        with queue_lock(paths["outbox"], paths["request_id"]):
            if paths["live"].is_file() or not paths["sent"].is_file():
                return None
            paths["live"].parent.mkdir(parents=True, exist_ok=True)
            if _file_replace.claim_with_retry(paths["sent"], paths["live"]):
                return "outbox"
            return None
    except QueueBusy:
        return None
    except OSError:
        return None


def quarantined(run_dir, outbox) -> list[str]:
    """Номера заявок ЭТОГО прогона в карантине «подана без
    подтверждения» (§4.3): поданы от него, прикладной квитанции службы
    у прогона нет, а файл очереди уже не живой (ушёл в `sent/` — своей
    доставкой или чужой — либо его забрала читающая сторона). Живой
    файл карантином не считается: он просто ждёт доставки."""
    out: list[str] = []
    for row in (read_index(run_dir).get("rows") or {}).values():
        if not isinstance(row, dict) \
                or row.get("composed") not in SUBMITTED_STATES:
            continue
        rid = str(row.get("request_id") or "")
        if not rid or rid in out:
            continue
        paths = _outbox_paths(Path(outbox), rid)
        if paths["live"].is_file() or paths["withdrawn"].is_file():
            continue
        sent = _queue_record(run_dir, rid, outbox)
        rcpt = _saved_delivery_receipt(run_dir, rid, outbox, sent=sent)
        if isinstance(rcpt, dict) and rcpt.get("refusal") == "replay":
            rcpt = None
        if receipt_confirmed(
                rcpt, rid, sent=sent) is None:
            out.append(rid)
    return sorted(out)


def recheck_one(request_id: str, run_dir, outbox, *, _urlopen=None) -> dict:
    """Сверка ОДНОЙ заявки со службой по ключу — БЕЗ новой подачи.

    Закон сверки (§4.3): спрашивается карточка `GET /api/requests/<номер>`;
    ручки карточки у службы нет (404 без кода `unknown-request`) —
    спрашивается предпросмотр `POST /api/requests/dry-run` телом той же
    записи: он считает номер по закону и говорит, знает ли служба
    заявку, не оставляя следа.

    Что сверка МОЖЕТ: (а) снять карантин, если служба ответила
    прикладной квитанцией с идентичностью этой заявки (исход
    «доставлена»); (б) назвать состояние и решение службы по карточке —
    это ЗНАНИЕ (`known`), а не доказательство своей подачи: тот же номер
    мог доставить другой прогон; (в) вернуть файл из `sent/` в очередь,
    когда служба заявки не знает вовсе, — «отправленным» он назван по
    ошибке, и повтор пойдёт ТЕМ ЖЕ номером.

    Что сверка НЕ делает: не подаёт заявку, не заводит второго номера,
    не отменяет прежнюю доставку и не трогает индекс прогона. Повторный
    вызов с тем же ответом службы меняет только момент сверки."""
    rid = str(request_id)
    sent = _queue_record(run_dir, rid, outbox)
    # Даже без исходного файла известен ключ, который мы спрашиваем.
    # Чужой ответ не заменяет ни прежнюю квитанцию, ни знание заявки.
    identity = sent if sent is not None else {"request_id": rid}
    prior = _saved_delivery_receipt(run_dir, rid, outbox, sent=sent)
    url = service_url()
    receipt = _blank_receipt(rid, url + SERVICE_REQUEST_PATH + rid, prior)
    # Прежнее знание СОХРАНЯЕТСЯ: сверка ничего не отменяет — она лишь
    # добавляет. Прежняя доставка остаётся доставкой и тогда, когда
    # связь сегодня не состоялась.
    _keep_prior_delivery(receipt, prior, sent=sent)
    if isinstance(prior, dict) and isinstance(prior.get("known"), dict):
        receipt["known"] = dict(prior["known"])
    code, raw, ctype, error = _service_call(
        url + SERVICE_REQUEST_PATH + rid, _urlopen=_urlopen)
    # «404» службы двузначен: заявки НЕТ (`unknown-request`) либо нет
    # самой ручки карточки (старая служба). Второе о судьбе заявки не
    # говорит ничего, и принимать его за «подача не дошла» нельзя —
    # иначе сверка вернула бы в очередь доставленный файл.
    спрошено = (code != 404
                or parse_refusal(raw, 404)["code"] == "unknown-request")
    if not спрошено and sent is not None:
        # Ручки карточки у службы нет — спрашиваем предпросмотр: он
        # считает номер по закону и следа не оставляет.
        code, raw, ctype, error = _service_call(
            url + SERVICE_DRY_RUN_PATH, data=_dump(sent), _urlopen=_urlopen)
        if code == 200:
            try:
                предпросмотр = _loads_reply(raw) if str(raw).strip() else None
            except ValueError:
                предпросмотр = None
            if isinstance(предпросмотр, dict):
                известна = предпросмотр.get("known")
                raw = json.dumps(известна, ensure_ascii=False) \
                    if isinstance(известна, dict) else "null"
                code = 200 if isinstance(известна, dict) else 404
                спрошено = True
    if error is not None:
        return _finish_check(receipt, run_dir, outbox, KIND_TIMEOUT,
                             http=None, words=f"служба не отвечает: {error}")
    try:
        body = _loads_reply(raw) if str(raw).strip() else None
    except ValueError:
        body = None
    if code == 404 and спрошено:
        return _finish_check(receipt, run_dir, outbox, KIND_UNKNOWN,
                             http=404, words=UNKNOWN_AT_SERVICE,
                             unknown_at_service=True)
    if code == 404:
        # Спросить было нечем: ручки карточки у службы нет, а записи
        # заявки у прогона нет тоже. Файл не трогаем — «не спросили» и
        # «не знает» это разные вещи.
        return _finish_check(receipt, run_dir, outbox, KIND_UNKNOWN,
                             http=404,
                             words=("сверка не выполнена: у службы нет ручки "
                                    "карточки заявки, а предпросмотр "
                                    "спросить нечем — записи заявки у "
                                    "прогона нет"))
    if code != 200:
        refusal = parse_refusal(raw, code or 0)
        return _finish_check(receipt, run_dir, outbox,
                             delivery_kind(code, refusal["code"]), http=code,
                             words=refusal["words"], refusal=refusal)
    беда = json_media_type_problem(ctype)
    if беда:
        return _finish_check(
            receipt, run_dir, outbox, KIND_UNKNOWN, http=code,
            words=f"ответ службы не разобран: {беда}")
    if isinstance(body, dict) and not validate_receipt(body, sent=identity):
        # Служба ответила на сверку ПРИКЛАДНОЙ КВИТАНЦИЕЙ — карантин
        # снят по тому же закону тела, что и у доставки (общая функция
        # читателя каталога). Согласие `repeat` с кодом здесь не
        # судится: это закон ПОДАЧИ (201 «записано сейчас» против 200
        # «доставлено ранее»), а сверка ничего не записывает.
        смешано = nested_request_problems(body)
        if смешано:
            # …кроме тела, носители которого противоречат друг другу:
            # квитанцией ЭТОЙ редакции оно не является, карантин
            # остаётся, файл очереди не трогается (K07C-F1).
            return _finish_check(
                receipt, run_dir, outbox, KIND_UNKNOWN, http=code,
                words=("ответ службы смешал свидетельства разных записей: "
                       + "; ".join(смешано)))
        return _finish_check(receipt, run_dir, outbox, KIND_DELIVERED,
                             http=code, words="", body=body)
    беды = card_problems(body, identity)
    if беды:
        return _finish_check(
            receipt, run_dir, outbox, KIND_UNKNOWN, http=code,
            words=("ответ службы не о этой заявке: " + "; ".join(беды)))
    receipt["known"] = _known_of_card(body)
    return _finish_check(receipt, run_dir, outbox, KIND_UNKNOWN, http=code,
                         words=_known_words(receipt["known"]))


def _known_words(known: dict) -> str:
    """Слова карантина, когда служба заявку ЗНАЕТ, а квитанции у прогона
    нет: названы и состояние службы, и то, чего не хватает."""
    откуда = known.get("source_run")
    return (f"заявка есть у службы (состояние «{known.get('state') or '?'}»)"
            + (f", подана из {откуда}" if откуда else "")
            + " — прикладной квитанции этого прогона нет")


def _finish_check(receipt: dict, run_dir, outbox, kind: str, *, http=None,
                  words: str = "", refusal=None, body=None,
                  unknown_at_service: bool = False) -> dict:
    """Записать исход сверки: попытка в журнал, момент сверки, положение
    файла очереди. Прежняя доставка сверкой НЕ отменяется."""
    prior = dict(receipt)
    # Повтор ТОЙ ЖЕ сверки журнала не удлиняет: в нём — события (что
    # ответила служба), а не число нажатий. §4.3 требует от проверок
    # идемпотентности, и панель, сверяющая раз в минуту, иначе раздула
    # бы файл без предела. Попытки ДОСТАВКИ пишутся всегда: это
    # действия, и сколько раз мы стучались в ручку записи — важно.
    прежняя = ((receipt.get("attempts") or [None])[-1]) or {}
    новая = _attempt("", http, OUTCOME_OF_KIND.get(kind, OUTCOME_UNKNOWN),
                     words)
    то_же = all(прежняя.get(k) == новая.get(k)
                for k in ("http", "outcome", "words"))
    _seal(receipt, kind, http=http, words=words, refusal=refusal, body=body,
          journal=not то_же)
    if kind != KIND_DELIVERED:
        # Сверка не удалась (связь, отказ канала) — прежняя доставка
        # остаётся: отменять доказанное неудачей сверки нельзя.
        _keep_prior_delivery(receipt, prior)
    paths = _outbox_paths(Path(outbox), str(receipt.get("request_id")))
    if unknown_at_service and receipt["outcome"] != OUTCOME_DELIVERED:
        _back_to_queue(paths)
    receipt["queue"] = _queue_where(paths)
    write_receipt(run_dir, receipt)
    return receipt


def recheck(run_dir, request_ids=None, outbox=None, *, replay: bool = False,
            _urlopen=None) -> dict:
    """Сверка карантина прогона со службой (§4.3): перечисленных номеров
    либо (без номеров) всех, что в карантине «подана без подтверждения».

    Новой подачи не бывает ни при каком исходе: сверка читает. У повтора
    прогона (`replay`) сверки нет вовсе — потомок в службу не ходит."""
    run_dir = Path(run_dir)
    outbox = Path(outbox) if outbox is not None else outbox_root()
    ids = [str(x) for x in (request_ids or ()) if str(x or "").strip()]
    if not ids:
        ids = quarantined(run_dir, outbox)
    out = {"refusal": None, "note": None, "receipts": [], "requests": ids,
           "confirmed": 0, "unconfirmed": 0}
    if replay:
        out["refusal"] = LINE_REPLAY_WORDS
        return out
    if not service_configured():
        out["refusal"] = service_off_words()
        return out
    bad = bad_settings_words()
    if bad:
        out["refusal"] = bad
        return out
    if not ids:
        out["refusal"] = "в карантине нет заявок этого прогона к сверке"
        return out
    pre = session_precheck(_urlopen=_urlopen)
    out["note"] = pre.get("words")
    if not pre.get("ok"):
        out["refusal"] = pre.get("words")
        return out
    for rid in ids:
        rcpt = recheck_one(rid, run_dir, outbox, _urlopen=_urlopen)
        out["receipts"].append(rcpt)
        if receipt_confirmed(rcpt, rid) is not None:
            out["confirmed"] += 1
        else:
            out["unconfirmed"] += 1
    return out


# ────────────────────────────────────────────── состояния для экрана
#
# K08 (контракт П0 §4.2, `request-state/2`): у строки — независимые поля
# состояния ОБЩЕЙ заявки. Регистрация ≠ решение владельца ≠ наличие
# записи ≠ исполнимость; решение службы по общей заявке видно одинаково
# из любого прогона, со своей версией и временем; происхождение —
# подпись, а не замена решения.

SCHEMA_REQUEST_STATE = "request-state/2"

#: Состояние транспорта — из файла квитанции и положения файла очереди.
TRANSPORT_QUEUED = "в очереди"
TRANSPORT_DELIVERED = "доставлена"
TRANSPORT_UNKNOWN = "неизвестно"
TRANSPORT_REFUSED = "отказ"
TRANSPORT_WITHDRAWN = "отозвана"
TRANSPORT_STATES = (TRANSPORT_QUEUED, TRANSPORT_DELIVERED, TRANSPORT_UNKNOWN,
                    TRANSPORT_REFUSED, TRANSPORT_WITHDRAWN)

#: Подпись происхождения записи общей заявки.
ORIGIN_THIS_RUN = "этот прогон"
ORIGIN_OTHER_SAME = "другой прогон (тот же черновик)"
ORIGIN_OTHER_DIFF = "другой прогон (иной черновик)"
ORIGINS = (ORIGIN_THIS_RUN, ORIGIN_OTHER_SAME, ORIGIN_OTHER_DIFF)

#: Состояние записи, при котором служба закрыла номер до пересмотра
#: владельцем (на повтор подачи отвечает `request-closed`).
REQUEST_REJECTED = "отклонена"

#: Слова карантина (миграция, линия 3): подача не подтверждена
#: прикладной квитанцией службы — новой подачи нет, сверка по тому же
#: номеру.
UNCONFIRMED_WORDS = "подана без подтверждения"

#: Слово повторной выдачи квитанции службой (HTTP 200, §4.1 (а)): без
#: него ответ 200 квитанцией не считается. Своей копии строки у клиента
#: НЕТ — она берётся из общего пакета читателя каталога
#: (`template_catalog.receipts.REPEAT_NOTE`, импорт в шапке модуля):
#: две копии одного слова разошлись бы при первой же правке службы.

#: Пометка источника слов решения, когда записи каталога у прогона нет,
#: а служба ответила `409 request-closed`: слова взяты из отказа службы
#: (момента и версии решения у них нет).
DECISION_FROM_REFUSAL = ("слова отказа службы (request-closed); записи "
                         "каталога у прогона нет")

#: ПРИЧИНЫ, по которым решение показано последним известным (`stale`) —
#: закрытый перечень слов для панели. Устаревание без причины читателю
#: ничего не говорит, а причины разные: квитанции у прогона нет — одно
#: дело, копия каталога отстала от свежего ответа службы — прямо
#: противоположное (прежняя единственная подпись «свежей квитанции
#: службы у прогона нет» во втором случае была ложной).
STALE_NO_RECEIPT = "прикладной квитанции службы у прогона нет"
STALE_RECEIPT_OLDER = "ответ службы старше последнего решения записи"
STALE_NOT_PROVEN = ("старшинство ответа службы над решением записи не "
                    "доказано")
STALE_CATALOG_BEHIND = "копия каталога отстала от ответа службы"
STALE_FROM_REFUSAL = ("состояние известно из отказа службы, записи "
                      "каталога у прогона нет")
STALE_LAST_CHECK = "последняя проверка не подтвердила актуальность решения службы"
STALE_REASONS = (STALE_NO_RECEIPT, STALE_RECEIPT_OLDER, STALE_NOT_PROVEN,
                 STALE_CATALOG_BEHIND, STALE_FROM_REFUSAL, STALE_LAST_CHECK)

#: Запасной перечень слов состояния службы — на случай каталога без
#: словаря `request_states` (перечень закрытый: слово вне него
#: квитанцией не считается).
_FALLBACK_REQUEST_STATES = ("есть", "исполнитель заявлен", "отклонена",
                            "подана", "принята")

#: Версии файла квитанции, которые читает эта сторона: без поля — старая
#: форма (`delivered`/`http`/`state`), `receipt-file/2` — форма K07
#: (§4.1: исход `outcome`, тело `receipt` по схеме `receipt/1`).
#: Иная версия — не квитанция (общий закон 5: неизвестная версия —
#: отказ, не откат на старую). Старая форма ЧИТАЕТСЯ и после K07:
#: прогоны, сделанные до волны 17а, свои квитанции не переписывают
#: (§4.3 — индексы и файлы прогонов не перештамповываются).
RECEIPT_FILE_VERSIONS = (None, RECEIPT_FILE_VERSION)


def service_request_states(cat) -> tuple:
    """Слова состояний заявки службы — из словаря каталога
    `request_states`; каталога или словаря нет — запасной перечень тех
    же слов."""
    try:
        words = _words(cat.dictionaries() if cat is not None else {},
                       "request_states")
    except Exception:                                     # noqa: BLE001
        words = ()
    return tuple(words) or _FALLBACK_REQUEST_STATES


def decision_of(record) -> dict:
    """Решение владельца по записи заявки — `{words, version, at}`.

    У записи каталога 0.5.0 поля версии решения нет: версией служит
    момент решения `at` — служба ставит его заново при каждом решении
    (`now_words()`), и пересмотр меняет его вместе со словами. Явное
    поле `version` (квитанция `receipt/1`, каталог будущих версий) — в
    приоритете. Решения нет — три `null`.

    Счёт версий службы (служебный неизменник, подтверждён COSMOS
    12.09.2026): `0` — решения НЕТ вовсе (три `null`, и до версии дело
    не доходит), первое решение — `1`, версии не убывают. Поэтому `0`
    особым случаем тут не считается: число сравнивается числом, как
    всякое другое. Прежде `0` подменялся моментом и делал версию
    несравнимой с числовой шкалой ответа службы — расхождение с самой
    же шкалой службы."""
    dec = (record or {}).get("decision") if isinstance(record, dict) else None
    dec = dec if isinstance(dec, dict) else {}
    words = _nullstr(dec.get("words"))
    at = _nullstr(dec.get("at"))
    if not (words or at):
        return {"words": None, "version": None, "at": None}
    version = dec.get("version")
    if version is None:
        version = at
    return {"words": words, "version": version, "at": at}


def receipt_confirmed(receipt, request_id: str, states=None, *, sent=None) -> dict | None:
    """Доказанная доставка из сохранённой прикладной квитанции.

    Старые transport-флаги остаются читаемыми, но без тела receipt/1
    не снимают карантин (П0 §4.3). Проверка тела и идентичности едина
    с доставкой; при наличии исходной заявки сверяется и её содержимое.
    Последний HTTP-код не судит прежнюю доставку: позже могла не
    состояться сверка. Решение и время берутся из самого тела службы,
    не из кешированных слов обёртки.
    """
    if not isinstance(receipt, dict) \
            or receipt.get("schema_version") != RECEIPT_FILE_VERSION \
            or receipt.get("outcome") != OUTCOME_DELIVERED:
        return None
    body = receipt.get("receipt")
    if not isinstance(body, dict) \
            or str(body.get("request_id") or "") != str(request_id):
        return None
    if validate_receipt(body, sent=sent) or nested_request_problems(body):
        return None
    state = body.get("state")
    if state not in tuple(states or _FALLBACK_REQUEST_STATES):
        return None
    return {"request_id": str(request_id), "state": state,
            "note": _nullstr(body.get("note")),
            "at": _nullstr(body.get("issued_at")),
            "decision": answer_decision(body)}


def _saved_delivery_receipt(run_dir, request_id: str, outbox, *, sent=None):
    """Собственная квитанция, иначе доказанный ответ инструмента импорта.

    Файл .reply.json — другой носитель ТОГО ЖЕ прикладного тела, а не
    разрешение верить одному accepted. Адаптация только в памяти: ни
    индекс, ни прежние файлы не мигрируются записью. Своя квитанция
    (включая более поздний отказ) имеет прежний приоритет; при повторной
    доставке/сверке обычный закон сохранения доказанного действует и
    для квитанции, полученной импортёром.
    """
    own = read_receipt(run_dir, request_id)
    if own is not None or receipt_path(run_dir, request_id).is_file():
        # Нечитаемый файл — тоже собственное свидетельство, которое
        # нельзя молча заместить более старым ответом импортёра.
        return own
    reply = _read_json(_outbox_paths(Path(outbox), request_id)["reply"])
    if not isinstance(reply, dict) or reply.get("accepted") is not True \
            or reply.get("request_id") != request_id:
        return None
    # Импортёр сохраняет код, но не заголовки ответа: Content-Type
    # проверен при записи accepted. Не выдумываем отсутствующий
    # заголовок; повторно судим сохранённые код и тело тем же законом.
    if _receipt_status_problems(reply.get("status"), reply.get("receipt")):
        return None
    receipt = {"schema_version": RECEIPT_FILE_VERSION,
               "request_id": request_id, "outcome": OUTCOME_DELIVERED,
               "outcome_kind": KIND_DELIVERED, "receipt": reply.get("receipt"),
               "delivered": True, "http": reply.get("status"),
               "at": reply.get("at"), "last_checked_at": reply.get("at"),
               "words": reply.get("words"), "attempts": [], "refusal": None}
    proof = receipt_confirmed(receipt, request_id, sent=sent)
    if proof is None:
        return None
    receipt.update(state=proof["state"], note=proof["note"],
                   state_words=_state_words_of(
                       KIND_DELIVERED, {}, proof["note"], request_id, None, ""))
    return receipt


def _parse_ts(value):
    """Момент из строки ISO 8601 (со смещением пояса — так пишут служба
    и квитанция); не читается — None: сравнение свежести тогда не
    делается, и решение считается неподтверждённым."""
    s = _nullstr(value)
    if not s:
        return None
    try:
        got = datetime.fromisoformat(s)
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.astimezone()
    return got


def _as_number(value):
    """Число из значения версии решения — либо None: строка из цифр
    считается числом (`"10"`), пустое и нечисловое — нет. Различаются
    `0`, отсутствие, null и строка: `0` — число, `None` и `""` — нет;
    `True`/`False` — не число. Бесконечность и NaN числом версии не
    считаются: в JSON-числах их нет (RFC 8259 §6), и сравнение с ними
    молча давало бы «не старше»."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        # ЦЕЛОЕ остаётся целым: у Python целые точны без предела, а
        # `float` теряет разряды уже на 2^53 (и на очень больших числах
        # вовсе отказывает) — две соседние версии решения слились бы в
        # одну, и старшинство исчезло бы.
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    s = _nullstr(value)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        got = float(s)
    except (ValueError, OverflowError):
        return None
    return got if math.isfinite(got) else None


def _decision_rank(decision) -> tuple | None:
    """Старшинство решения: ЧИСЛОВАЯ версия — числом (v10 после v9, не
    лексикографически), иначе момент решения — моментом. Нечитаемое —
    None: сравнивать нечем."""
    dec = decision if isinstance(decision, dict) else {}
    num = _as_number(dec.get("version"))
    if num is not None:
        return ("число", num)
    got = _parse_ts(dec.get("at") or dec.get("version"))
    return ("момент", got) if got is not None else None


def _newer_decision(left, right):
    """`left` СТРОГО старше `right`? None — сравнить нечем: у одной из
    сторон старшинства нет либо виды разные (число против момента).
    Числа с моментами не смешиваются — это разные шкалы."""
    a, b = _decision_rank(left), _decision_rank(right)
    if a is None or b is None or a[0] != b[0]:
        return None
    return a[1] > b[1]


def decision_identity(decision) -> tuple:
    """ИДЕНТИЧНОСТЬ решения — то, чем одна его редакция отличается от
    другой: кто решил (`by`), слова, момент (`at`) и числовая версия,
    когда она есть (общий закон 3 П0 — «точная идентичность»; так же
    различает точную и слабую эквивалентность RFC 9110 §8.8.3.2).

    Совпадение ОДНОГО слова состояния редакции не доказывает: пересмотр
    «принята → отклонена → принята» возвращает то же слово, а решение за
    ним стоит другое. Решения нет вовсе (ни слов, ни момента) — пустая
    идентичность: две таких стороны равны между собой и не равны
    никакому решению."""
    dec = decision if isinstance(decision, dict) else {}
    words, at = _nullstr(dec.get("words")), _nullstr(dec.get("at"))
    if not (words or at):
        return ()
    num = _as_number(dec.get("version"))
    return (_nullstr(dec.get("by")), words, at,
            num if num is not None else _nullstr(dec.get("version")))


def _same_redaction(left, right) -> bool:
    """ТА ЖЕ редакция решения у ответа службы и у записи каталога?

    Опознают редакцию `by`, слова и момент; ЧИСЛОВАЯ версия судит
    наравне с ними ТОЛЬКО там, где её называют ОБЕ стороны — ровно как
    сказано законом K08: «кто, слова, момент, а с каталога 0.6.0 —
    числовая версия». До 0.6.0 версию называет одна сторона: квитанция
    `receipt/1` несёт её всегда (`0` — решения нет, `1` — решение есть),
    а запись заявки каталога 0.5.0 поля версии не имеет вовсе (схема
    закрыта тремя полями). Считать это расхождением редакций нельзя:
    тогда подтверждения не случилось бы ни разу, и всякое решение вечно
    показывалось бы «последним известным» — закон обессмыслился бы.
    Ослабления тут нет: где версия есть у обеих сторон, разные версии
    одной редакцией не становятся."""
    a, b = decision_identity(left), decision_identity(right)
    if not a or not b:
        return a == b                      # решения нет — у обеих сторон
    if a[3] is None or b[3] is None:
        return a[:3] == b[:3]
    return a == b


def _answer_supersedes(rcpt_dec, rec_dec) -> bool:
    """Ответ службы ЗАМЕНЯЕТ последнее известное решение записи каталога?

    Только доказав старшинство САМИМ решением: его версия строго старше
    на ТОЙ ЖЕ шкале (число с числом, момент с моментом) либо известного
    решения у записи нет вовсе, а ответ своё решение назвал — откатывать
    тогда нечего.

    Время получения ответа старшинством НЕ является (K08, F1):
    запоздавший ответ приходит позже нового решения, и порядок прихода
    ответов решения владельца не меняет. Равенство, несравнимое и
    нечитаемое сохраняют последнее достоверное решение записи."""
    # «Откатывать нечего» = у записи НЕТ РЕШЕНИЯ (пустая идентичность:
    # ни слов, ни момента), а не «нет ранга»: известное решение с
    # нечитаемым моментом («вчера») — всё равно решение, и старый ответ
    # его не заменяет (четвёртое неавтор-ревью K08, K1).
    if not decision_identity(rec_dec):
        return _decision_rank(rcpt_dec) is not None
    return _newer_decision(rcpt_dec, rec_dec) is True


def _local_record(run_dir, request_id: str) -> dict | None:
    """Запись заявки в папке прогона (поданная либо удержанная)."""
    for folder in (run_requests_dir(run_dir),
                   run_requests_dir(run_dir) / HELD_DIR_NAME):
        p = folder / f"{request_id}.json"
        if p.is_file():
            rec = _read_json(p)
            return rec if isinstance(rec, dict) else {}
    return None


def _last_known_words(last_known, remote) -> str:
    """Подпись последнего известного решения ЗАПИСИ каталога, когда
    показано не оно. Состояние записи называется только там, где
    отличается от показанного, — иначе слово состояния повторилось бы
    дважды подряд."""
    if not last_known:
        return ""
    dec = last_known.get("decision") or {}
    said = dec.get("words") or dec.get("at") or "без слов"
    state = last_known.get("state")
    head = f"{state} — " if state and state != remote else ""
    return f" · последнее известное решение записи: {head}{said}"


def _canon_words(canonical, rid) -> str:
    """Подпись конфликта канона: ОБА номера названы, когда они разные
    (K08, F3) — свой черновик не выдаётся за принятый, а решение читается
    по существующей заявке. Номер один — пусто."""
    return ("" if str(canonical) == str(rid) else
            f" · конфликт канона: общая заявка {canonical}, номер этого "
            f"прогона {rid}")


def _origin_words(origin: str, source_run) -> str:
    """Подпись происхождения для слов панели; у этого прогона — пусто."""
    if origin == ORIGIN_OTHER_SAME:
        return f" · подана из {source_run or '?'}"
    if origin == ORIGIN_OTHER_DIFF:
        return f" · подана иным черновиком из {source_run or '?'}"
    return ""


def states(run_dir, cat, outbox, rows: list[dict] | None = None) -> list[dict]:
    """Состояние каждой заявки прогона (`request-state/2`, K08): перечень
    — ОТ ИНДЕКСА (заявка живёт дольше строки C); состояние службы — из
    записи каталога и прикладной квитанции прогона; транспорт — из
    файла квитанции и положения файла очереди; происхождение —
    подписью.

    ЧТЕНИЕ НИЧЕГО НЕ ПИШЕТ (§4.3 — проверки повторяемые и
    идемпотентные): два чтения подряд оставляют очередь байт в байт той
    же. Прежде чтение переносило живой файл очереди в `sent/` при
    появлении записи в каталоге — и у ОБЩЕЙ заявки это чтение из
    второго прогона подменяло файл-победитель первого. Перенос —
    дело доставки (`deliver_one`) и разбора очереди читающей стороной.

    Поля строки (все прежние сохранены):
    - `canonical_request_id` — номер ОБЩЕЙ заявки; разрешается ПЕРВЫМ,
      до чтения решения и происхождения (K08, F3): отказ службы о
      конфликте канона (`request-exists` / `duplicate-request`) называет
      заявку, уже открытую по этой записи, и решение читается по НЕЙ, а
      свой номер остаётся в `request_id`. Панель ведёт по каноническому
      номеру — к существующей заявке, а не к отвергнутому черновику;
    - `remote_state` — слово состояния службы; происхождением НИКОГДА
      не подменяется; неизвестно — None;
    - `decision` — `{words, version, at}` (см. `decision_of`) ПОКАЗАННОГО
      состояния `remote_state`, и только его: ответ службы назвал иное
      состояние, а своего решения не назвал — три `null` (решения для
      нового состояния не знает никто), и последнее известное решение
      записи уходит в `last_known`, а не приписывается чужому состоянию
      («принята: нет, механика спорна» — не слова службы);
    - `stale` — решение показано как ПОСЛЕДНЕЕ ИЗВЕСТНОЕ, а не
      подтверждённое ответом службы этому прогону. Подтверждение — это
      ТА ЖЕ РЕДАКЦИЯ решения (`decision_identity`: кто, слова, момент,
      числовая версия) при том же слове состояния; одно совпавшее слово
      состояния редакции не доказывает. Замена показанного решения
      требует СТРОГОГО старшинства САМОГО решения (`_answer_supersedes`:
      число с числом, момент с моментом) либо отсутствия известного
      решения у записи (откатывать нечего). Время получения ответа
      старшинством НЕ является (K08, F1): запоздавший ответ приходит
      позже нового решения владельца и не откатывает его; равенство,
      несравнимое, нечитаемое и молчащий о решении ответ сохраняют
      последнее достоверное решение записи;
    - `stale_reason` — ПРИЧИНА устаревания словами (закрытый перечень
      `STALE_REASONS`): квитанции у прогона нет | ответ службы старше
      решения | старшинство не доказано | копия каталога отстала |
      состояние из отказа службы. `stale: false` — None;
    - `last_known` — `{state, decision}` записи каталога, названные
      ОТДЕЛЬНО везде, где показано не её решение (копия каталога
      отстала): ни подмены решения, ни молчания о нём. Решения у записи
      нет либо показано именно оно — None;
    - `transport_state` — в очереди | доставлена | неизвестно | отказ |
      отозвана; None — черновик не подан (удержан или не составлен).
      Судит СНАЧАЛА собственный ответ службы ЭТОМУ прогону, и лишь
      потом запись каталога: запись говорит, что заявка у службы есть,
      а не что дошла подача этого прогона, — «доставлена» по ней только
      там, где своего ответа службы у прогона нет вовсе;
    - `source_run`, `origin` — прогон-источник записи и подпись
      происхождения; у строки без собственного черновика при чужой
      записи — «иной черновик» (сравнить нечего — не «тот же»);
    - `state` — слова для панели: состояние службы + решение + подпись
      происхождения («отклонена: <слова> · подана из run-0040»); без
      записи каталога — слова транспорта."""
    index = read_index(run_dir)
    run_name = Path(run_dir).name
    by_rule = {r["rule_id"]: r for r in (rows or ())}
    # Тот же перечень записей, которым судит Г5 (`_catalog_records`):
    # одна заявка — один номер, запись без номера чужой строке не
    # достаётся.
    known = _catalog_records(cat)
    svc_states = service_request_states(cat)
    out: list[dict] = []
    for rule_id, row in sorted((index.get("rows") or {}).items()):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("request_id") or "")
        composed = row.get("composed")
        # Файл очереди — под СВОИМ номером: очередь этого прогона.
        paths = _outbox_paths(Path(outbox), rid)
        reply = _read_json(paths["reply"])
        sent = _queue_record(run_dir, rid, outbox)
        receipt = _saved_delivery_receipt(run_dir, rid, outbox, sent=sent)
        # Квитанция повтора — не транспорт: потомок в службу не ходил.
        transport_receipt = (receipt if isinstance(receipt, dict)
                             and receipt.get("refusal") != "replay"
                             else None)
        # Обе формы читаются, но подтверждение требует тела receipt/1:
        # старый транспортный флаг сам по себе доставку не доказывает.
        rv2 = (transport_receipt if isinstance(transport_receipt, dict)
               and transport_receipt.get("schema_version")
               == RECEIPT_FILE_VERSION else None)
        confirmed = receipt_confirmed(
            transport_receipt, rid, svc_states,
            sent=sent)
        # ── КАНОНИЧЕСКИЙ номер — ДО чтения решения и происхождения
        # (K08, F3): служба отказом назвала заявку, уже открытую по этой
        # записи, и решение ОБЩЕЙ заявки читается по НЕЙ. Иначе строка
        # показывала бы канонический номер, а решение искала по своему —
        # и не находила: «отказ» без состояния, ссылка панели вела бы на
        # несуществующий черновик.
        canonical = canonical_of(transport_receipt, rid)
        rec = known.get(canonical)
        if rec is None and canonical != rid:
            rec = known.get(rid)
        local = _local_record(run_dir, rid)
        local_canon = draft_canon(local.get("draft")) if local else ""
        words = row.get("words")

        # ── происхождение: запись каталога, иначе файл-победитель
        # очереди, иначе собственная запись прогона ──
        winner = None
        if rec is None:
            for key in ("live", "sent"):
                if paths[key].is_file():
                    got = _read_json(paths[key])
                    winner = got if isinstance(got, dict) else None
                    break
        external = rec if rec is not None else winner
        source_run = _ref_head(((external or local or {}).get("source") or {})
                               .get("ref")) or None
        if external is not None and source_run and source_run != run_name:
            same = draft_canon(external.get("draft")) == local_canon
            origin = ORIGIN_OTHER_SAME if same else ORIGIN_OTHER_DIFF
        elif source_run and source_run != run_name:
            # Только собственная запись со ссылкой на иной прогон —
            # потомок повтора: черновик унаследован, он тот же.
            origin = ORIGIN_OTHER_SAME
        else:
            origin = ORIGIN_THIS_RUN
            source_run = source_run or (run_name if local else None)

        # ── состояние службы: запись каталога против прикладной
        # квитанции — свежее побеждает, старый ответ решения не
        # откатывает ──
        remote, decision, stale = None, decision_of(None), False
        stale_why, last_known = None, None
        if rec is not None:
            rec_state = _nullstr(rec.get("state"))
            rec_dec = decision_of(rec)
            rcpt_raw = confirmed.get("decision") if confirmed else None
            rcpt_dec = (decision_of({"decision": rcpt_raw})
                        if isinstance(rcpt_raw, dict) else None)
            if confirmed is None:
                # Прикладной квитанции у прогона нет вовсе — показано
                # последнее известное решение записи.
                remote, decision, stale = rec_state, rec_dec, True
                stale_why = STALE_NO_RECEIPT
            elif _same_redaction(rcpt_raw, rec.get("decision")):
                # ТА ЖЕ редакция решения у ответа и у записи —
                # подтверждение, но лишь вместе с тем же словом
                # состояния: слово, за которым не стоит та же редакция,
                # ничего не доказывает, и наоборот — одна редакция при
                # разных словах состояния не говорит, чьё слово свежее.
                remote, decision = rec_state, rec_dec
                stale = confirmed["state"] != rec_state
                stale_why = STALE_NOT_PROVEN if stale else None
            elif _answer_supersedes(rcpt_dec, rec_dec):
                # Ответ службы доказал старшинство САМИМ решением —
                # копия каталога отстала: показан ответ. Решение
                # принадлежит СОСТОЯНИЮ: ответ назвал своё — оно и
                # показано; не назвал — решения для НОВОГО состояния не
                # знает никто, три `null`, а решение записи уходит в
                # `last_known` отдельной строкой. Иначе строка
                # складывала бы слово одного состояния со словами
                # решения другого («принята: нет, механика спорна» —
                # обратный смысл).
                remote = confirmed["state"]
                decision = (rcpt_dec if rcpt_dec is not None
                            else decision_of(None))
                stale, stale_why = True, STALE_CATALOG_BEHIND
            else:
                # Старшинства ответ не доказал: решение записи —
                # последнее достоверное, и запоздавший ответ его не
                # откатывает, когда бы он ни пришёл (K08, F1).
                remote, decision, stale = rec_state, rec_dec, True
                stale_why = (STALE_RECEIPT_OLDER
                             if _newer_decision(rec_dec, rcpt_dec) is True
                             else STALE_NOT_PROVEN)
            # Последнее известное решение ЗАПИСИ — отдельным полем всюду,
            # где показано не оно: ни подмены, ни молчания.
            if decision != rec_dec and (rec_dec["words"] or rec_dec["at"]):
                last_known = {"state": rec_state, "decision": rec_dec}
        elif confirmed is not None:
            remote = confirmed["state"]
            decision = decision_of({"decision": confirmed["decision"]})
            stale = False
        elif transport_receipt is not None \
                and transport_receipt.get("refusal") == "request-closed":
            # Служба закрыла номер до пересмотра владельцем: состояние
            # «отклонена» известно из отказа, записи у прогона нет —
            # последнее известное. Решение берётся СТРУКТУРНО из
            # `extra.decision` службы (слова, версия, момент) — по нему
            # старшинство сравнимо с решением записи; слов решения в
            # `extra` нет — остаются слова отказа с ПОМЕТКОЙ источника:
            # выдавать пересказ за запись каталога нельзя.
            remote, stale = REQUEST_REJECTED, True
            stale_why = STALE_FROM_REFUSAL
            извне = (transport_receipt.get("extra") or {}).get("decision")
            said = _nullstr(transport_receipt.get("words"))
            if isinstance(извне, dict) and _nullstr(извне.get("words")):
                decision = decision_of({"decision": извне})
            elif said:
                decision = {"words": f"{said} — {DECISION_FROM_REFUSAL}",
                            "version": None, "at": None}

        if confirmed is not None \
                and last_outcome(transport_receipt) != OUTCOME_DELIVERED:
            # Доказанная доставка переживает сбой связи, но последнее
            # известное решение после него не становится свежим (§4.4).
            stale = True
            stale_why = stale_why or STALE_LAST_CHECK

        # ── транспорт: СНАЧАЛА собственная квитанция прогона (§4.2 —
        # «из файла квитанции»), и только потом запись каталога ──
        # Ответ службы ЭТОМУ прогону — свидетельство о его подаче:
        # прикладная квитанция — «доставлена», отказ (в том числе
        # `409 request-closed` — по §4.1 не успех) — «отказ», ответ без
        # прикладной квитанции — «неизвестно». Запись каталога говорит,
        # что заявка у службы есть, но НЕ о том, дошла ли подача этого
        # прогона, — «доставлена» по ней только там, где своего ответа
        # службы у прогона нет вовсе (случай run-0041). Квитанция, в
        # которой лишь несостоявшаяся связь (три попытки, кода нет), о
        # судьбе заявки не говорит ничего — тогда судят очередь и запись,
        # как прежде.
        if composed == COMPOSED_WITHDRAWN:
            transport = TRANSPORT_WITHDRAWN
        elif composed in (COMPOSED_FAILED, COMPOSED_HELD):
            transport = None
        elif confirmed is not None:
            transport = TRANSPORT_DELIVERED
        elif rv2 is not None and rv2.get("outcome") == OUTCOME_REFUSED:
            # Форма K07: служба сказала «нет» ОПРЕДЕЛЁННО (конфликт
            # канона, отклонена, канал, форма) — транспорт «отказ» и без
            # машинного кода отказа в теле.
            transport = TRANSPORT_REFUSED
        elif rv2 is not None and (rv2.get("outcome") == OUTCOME_DELIVERED
                                  or rv2.get("http") is not None
                                  or rv2.get("known")):
            # Ответ службы БЫЛ, а прикладной квитанции в нём нет (иной
            # 2xx, страница вместо JSON, пустое тело, чужой номер, 5xx),
            # либо сверка нашла заявку у службы без своей квитанции —
            # «неизвестно». Одна лишь несостоявшаяся связь (кода нет,
            # знания нет) о судьбе заявки не говорит ничего: тогда судят
            # очередь и запись, как прежде.
            transport = TRANSPORT_UNKNOWN
        elif transport_receipt is not None \
                and transport_receipt.get("refusal"):
            transport = TRANSPORT_REFUSED
        elif transport_receipt is not None \
                and transport_receipt.get("delivered"):
            # Ответ службы был, прикладной квитанции в нём нет (страница
            # вместо JSON, пустое тело, чужой номер, слово вне словаря).
            transport = TRANSPORT_UNKNOWN
        elif isinstance(reply, dict) and reply.get("accepted") is True:
            # Валидное тело уже учтено выше; один флаг импорта ничего
            # не доказывает, как и старый delivered файла channel/.
            transport = TRANSPORT_UNKNOWN
        elif isinstance(reply, dict) and reply.get("accepted") is False:
            transport = TRANSPORT_REFUSED
        elif rec is not None and transport_receipt is None:
            # Своего ответа службы у прогона нет ВОВСЕ — заявка у службы
            # есть по записи (доставил другой прогон либо её забрала
            # читающая сторона). Ответ БЫЛ, но о судьбе заявки не сказал
            # (связь не состоялась: три попытки, кода нет) — запись его
            # не перекрывает: собственная доставка не подтверждена, и
            # сверка со службой («Доставить», повтор идемпотентен по
            # номеру) обязана остаться доступной. Форма K07
            # `receipt-file/2` держит прикладную квитанцию рядом с
            # попытками — там неудачная сверка подтверждения уже не
            # стирает (волна 17б).
            transport = TRANSPORT_DELIVERED
        elif paths["live"].is_file():
            transport = TRANSPORT_QUEUED
        elif paths["withdrawn"].is_file():
            transport = TRANSPORT_WITHDRAWN
        else:
            # В `sent/` без квитанции — карантин; файла нет нигде —
            # тоже неизвестно.
            transport = TRANSPORT_UNKNOWN

        # ── слова для панели ──
        if composed == COMPOSED_FAILED:
            state = f"черновик не составлен: {words or '—'}"
        elif composed == COMPOSED_HELD:
            state = f"ждёт подтверждения аналитика: {words or '—'}"
        elif composed == COMPOSED_WITHDRAWN:
            state = "отозвана аналитиком"
        elif rec is not None:
            state = (str(remote or "")
                     + (f": {decision['words']}" if decision["words"] else "")
                     + _last_known_words(last_known, remote)
                     + _origin_words(origin, source_run)
                     + _canon_words(canonical, rid))
        elif confirmed is not None:
            # Квитанция — единственный источник: слова доставки, как
            # прежде; состояние службы, отличное от «подана», названо
            # впереди — «уже подана» повторной выдачи решения не скрывает.
            said = (str(transport_receipt.get("state_words") or "")
                    or "подана в службу каталога")
            state = said if remote == "подана" else f"{remote} · {said}"
        elif isinstance(reply, dict) and reply.get("accepted") is False:
            probs = "; ".join(
                str(p.get("message") or p) for p in (reply.get("problems") or ())
                if p) or str(reply.get("state") or "без слов")
            state = f"не принята: {probs}"
        elif transport_receipt is None \
                and isinstance(reply, dict) and reply.get("accepted") is True:
            state = (f"{UNCONFIRMED_WORDS}: ответ инструмента импорта "
                     "не несёт прикладной квитанции этой заявки")
        elif rv2 is not None and rv2.get("outcome") == OUTCOME_DELIVERED:
            # Кешированные слова «подана» не заменяют негодное тело.
            state = (f"{UNCONFIRMED_WORDS}: сохранённый ответ службы "
                     "не является прикладной квитанцией")
        elif rv2 is not None and rv2.get("state_words"):
            # Форма K07 несёт слова исхода целиком (§4.1, таблица):
            # пересказывать их строкой панели незачем — пересказ и
            # разошёлся бы с ними при первой же правке.
            state = str(rv2["state_words"])
        elif transport_receipt is not None \
                and transport_receipt.get("delivered"):
            state = (f"{UNCONFIRMED_WORDS}: ответ службы без прикладной "
                     f"квитанции (HTTP {transport_receipt.get('http')})")
        elif transport_receipt is not None \
                and transport_receipt.get("state_words"):
            # Отказ службы или сеть: слова квитанции (`channel/`, не
            # индекс — тот детерминирован и сверяется повтором байт в
            # байт, §1.3).
            state = str(transport_receipt["state_words"])
        elif paths["live"].is_file():
            state = ("в очереди, службой не принята"
                     + _origin_words(origin, source_run))
        elif paths["sent"].is_file():
            state = (f"{UNCONFIRMED_WORDS}: файл очереди в sent/, квитанции "
                     f"службы у прогона нет")
        elif paths["withdrawn"].is_file():
            state = "отозвана: файл очереди в withdrawn/"
        else:
            state = f"{UNCONFIRMED_WORDS}: файла заявки в очереди нет"
        cand = by_rule.get(rule_id)
        if composed in SUBMITTED_STATES and cand is None and rows is not None:
            state += " · строка уже не C (посадка A/B) — отозвать?"
        elif cand is not None and cand["key"] != row.get("key"):
            state += (" · формулировка строки изменилась — прежняя заявка "
                      "остаётся поданной")
        out.append({"schema_version": SCHEMA_REQUEST_STATE,
                    "rule_id": rule_id, "request_id": rid,
                    "canonical_request_id": canonical,
                    "key": row.get("key"), "composed": composed,
                    "words": words,
                    "document_class": row.get("document_class"),
                    "state": state,
                    "remote_state": remote,
                    "decision": decision,
                    "stale": stale,
                    "stale_reason": stale_why,
                    "last_known": last_known,
                    "transport_state": transport,
                    "source_run": source_run,
                    "origin": origin,
                    "record_id": _record_id(run_dir, rid),
                    "submitted_at": _submitted_at(run_dir, rid),
                    "held": (run_requests_dir(run_dir) / HELD_DIR_NAME
                             / f"{rid}.json").is_file(),
                    "withdrawn": paths["withdrawn"].is_file(),
                    "delivered": transport == TRANSPORT_DELIVERED,
                    "delivery": ({"at": receipt.get("at"),
                                  "http": receipt.get("http"),
                                  "refusal": receipt.get("refusal"),
                                  "words": receipt.get("words"),
                                  "attempts": _attempts_count(receipt),
                                  "outcome": receipt.get("outcome"),
                                  "outcome_kind": receipt.get("outcome_kind"),
                                  "last_checked_at":
                                      receipt.get("last_checked_at"),
                                  "problems": receipt.get("problems") or []}
                                 if isinstance(receipt, dict) else None)})
    return out


def _local_canon(run_dir, request_id: str) -> str:
    rec = _local_record(run_dir, request_id)
    return draft_canon(rec.get("draft")) if rec else ""


def _record_id(run_dir, request_id: str):
    """Имя семьи-черновика заявки (`draft.id`, он же `record_id` записи
    §6) — для экрана: по нему заявка ищется в каталоге шаблонов. Файла
    заявки в прогоне нет (подана иным прогоном) — None."""
    for folder in (run_requests_dir(run_dir),
                   run_requests_dir(run_dir) / HELD_DIR_NAME):
        p = folder / f"{request_id}.json"
        if p.is_file():
            rec = _read_json(p) or {}
            return ((rec.get("draft") or {}).get("id")
                    or rec.get("record_id"))
    return None


def _submitted_at(run_dir, request_id: str):
    for folder in (run_requests_dir(run_dir),
                   run_requests_dir(run_dir) / HELD_DIR_NAME):
        p = folder / f"{request_id}.json"
        if p.is_file():
            return (_read_json(p) or {}).get("submitted_at")
    return None


def _park_duplicate(paths: dict) -> str | None:
    """Живой файл под ЗАНЯТЫМ номером — в `duplicates/` (второй и дальше
    — с порядковым хвостом): победителя он не заменяет, а след остаётся.
    Зовётся только ПОД ЗАМКОМ номера."""
    base = paths["duplicates"]
    base.parent.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        target = base if n == 1 else base.with_name(f"{base.stem}.{n}.json")
        if _file_replace.claim_with_retry(paths["live"], target):
            return "duplicates"
    return None


def _move_to_sent(paths: dict) -> str | None:
    """Живой файл очереди — в `sent/`: доставлять больше нечего. Зовёт
    только доставка (`deliver_one`): чтение состояний очередь не трогает
    (§4.3 — проверки повторяемые и идемпотентные).

    Проверка и перенос — ОДНА операция: замок номера (`queue_lock`)
    держится на всём участке, а само занятие места идёт «занять, если
    свободно» (`claim_with_retry`), а не заменой. Неделимая замена
    (`os.replace`) победителя молча ЗАТИРАЕТ, и ещё одна проверка перед
    ней разрыва check/act не закрывает (CWE-367): победитель, положенный
    писателем без замка (читающая сторона, чужой инструмент), уцелеет
    только так. Файл в `sent/` уже есть — живой файл под тем же номером
    дубль (K08) и уходит в `duplicates/`. Возвращает, куда ушёл файл:
    "sent" | "duplicates"; None — не ушёл (файла нет, замок занят или
    отказ файловой системы)."""
    try:
        with queue_lock(paths["outbox"], paths["request_id"]):
            if not paths["live"].is_file():
                return None
            paths["sent"].parent.mkdir(parents=True, exist_ok=True)
            if _file_replace.claim_with_retry(paths["live"], paths["sent"]):
                return "sent"
            return _park_duplicate(paths)
    except QueueBusy:
        return None
    except OSError:
        return None


def _queue_where(paths: dict) -> str | None:
    """Где файл заявки ЛЕЖИТ в очереди: "outbox" | "sent" | "withdrawn"
    | "duplicates"; None — нигде. Читает, ничего не двигая."""
    for key, word in (("live", "outbox"), ("sent", "sent"),
                      ("withdrawn", "withdrawn"),
                      ("duplicates", "duplicates")):
        if paths[key].is_file():
            return word
    return None


# ──────────────────────────────────────────────── итог побочной линии

LINE_RUNNING = "заявки в каталог идут"
LINE_ORPHAN = "остановлено: заявки в каталог"


def line_outcome(counts: dict | None = None, refusal: str | None = None) -> str:
    """Итог побочной линии — СЛОВАМИ, без времени и без чисел среды:
    строку `end` журнала водитель повтора сравнивает с итогом потомка."""
    if refusal:
        return f"отказ заявок в каталог: {refusal}"
    c = counts or {}
    return (f"заявки в каталог: подано {int(c.get('submitted') or 0)}, "
            f"удержано {int(c.get('held') or 0)}, "
            f"не составлено {int(c.get('failed') or 0)}")


# ──────────────────────────────────────────── составитель черновика

ROLE = "catalog_composer"


def compose_prompt(row: dict, cat, refusals: list | None = None) -> str:
    """Задание составителю: перечень семей каталога с параметрами (чтобы
    не предлагать существующее), словари, занятые имена, строка свода —
    БЕЗ значений, цитат и источников."""
    dicts = cat.dictionaries() if cat is not None else {}
    meta = cat.families_meta() if cat is not None else {}
    taken = sorted(set(cat.params() or {}) | set(cat.conditions() or {})) \
        if cat is not None else []
    lines = [
        "ЗАДАНИЕ: предложить ОБЕЗЛИЧЕННЫЙ черновик записи новой семьи "
        "правил каталога шаблонов по строке свода, которая ни на одну "
        "семью каталога не легла.",
        "",
        "СЕМЬИ КАТАЛОГА (id — вид/зона; природа; параметры; это не заголовки):",
    ]
    for fid, spec in sorted(meta.items()):
        ps = ", ".join(spec.get("params") or ()) or "—"
        lines.append(f"- {fid} — {spec.get('kind')}/{spec.get('zone')}; "
                     f"природа {spec.get('nature')}; параметры: {ps}")
    lines += [
        "",
        "СЛОВАРИ (закрытые перечни; единицы нарушения — КОДЫ словаря):",
        f"- family_natures: {', '.join(_words(dicts, 'family_natures'))}",
        f"- violation_units: {', '.join(_words(dicts, 'violation_units'))}",
        f"- violation_sizes: {', '.join(_words(dicts, 'violation_sizes'))}",
        f"- history_read_kinds: {', '.join(_words(dicts, 'history_read_kinds'))}",
        f"- param_units: {', '.join(_words(dicts, 'param_units'))}",
        f"- param_relations: {', '.join(_words(dicts, 'param_relations'))}",
        "",
        "ЗАНЯТЫЕ ИМЕНА параметров и условий (новое имя обязано отличаться): "
        + (", ".join(taken) or "—"),
        "",
        "СТРОКА СВОДА:",
        f"- формулировка: {row.get('statement')}",
        f"- вид: {row.get('kind')}; зона: {row.get('zone')}",
        f"- чего не хватает каталогу: {row.get('gap')}",
    ]
    names = [f"{p.get('name')} ({p.get('unit')})"
             for p in (row.get("params") or ()) if p.get("name")]
    lines.append("- имена и единицы параметров строки (БЕЗ значений): "
                 + (", ".join(names) or "—"))
    lines += [
        "",
        "ТРЕБОВАНИЯ:",
        "- русский деловой язык; ни кодов, ни чисел, ни названий "
        "авиакомпании, ни цитат, ни ссылок на пункты документа;",
        "- описывать МЕХАНИКУ семьи, а не значения порогов заказчика;",
        "- title, subject и semantics — предметная проза, не коды вида "
        "и зоны; HARD/ROSTERING и подобные технические обозначения в "
        "эти текстовые поля не переносить;",
        "- сохранять область исходного запрета: назначение на рейс не "
        "расширять до дежурств, резерва и любых иных работ;",
        "- params может быть пустым списком. Операционные данные "
        "(календарь отсутствия, список сотрудников и рейсов) не становятся "
        "параметрами нормы лишь потому, что нужны её исполнителю; не "
        "придумывать параметр-календарь, ссылку или код без основания в норме;",
        "- id — латиница snake_case по-английски, как у существующих семей;",
        "- violation_unit и violation_size — только при виде SOFT, иначе "
        "null; violation_unit — ЧЕМ меряется нарушение (код словаря), "
        "violation_size — растёт ли штраф с ЧИСЛОМ единиц (count) или с "
        "ВЕЛИЧИНОЙ превышения (magnitude);",
        "- reads_history — что семья читает из истории сотрудника ДО "
        "начала горизонта: объект {need, kind} (need — потребность "
        "словами, ею анкета спрашивает данные у авиакомпании; kind — "
        "вид чтения из словаря history_read_kinds) либо null, если "
        "семья историей не пользуется; при природе history он "
        "обязателен.",
        "- календарь недоступности внутри горизонта сам по себе не является "
        "историей работы ДО горизонта; для него reads_history не добавлять.",
        "",
        "ОТВЕТ — один JSON-объект РОВНО с этими ключами:",
        '{"id": "...", "title": "...", "subject": "...", "nature": "...", '
        '"violation_unit": null, "violation_size": null, '
        '"reads_history": null, "params": [{"name": "...", "unit": "...", '
        '"relation": null, "note": "..."}], "semantics": {"scope": "...", '
        '"unit": "...", "measure": "...", "text": "..."}}',
    ]
    if refusals:
        lines += ["", "ПРЕДЫДУЩЕЕ ПРЕДЛОЖЕНИЕ ОТКЛОНЕНО — исправьте:"]
        lines += [f"- {w}" for w in refusals]
    return "\n".join(lines)


def _reads_history_judged(reads, nature: str, dicts: dict,
                          words: list) -> dict | None:
    """Судья поля «что семья читает из истории» (X15/X19): либо null,
    либо объект {need, kind} — потребность непустым текстом (ею анкета
    спрашивает данные у авиакомпании) и вид чтения словом словаря
    `history_read_kinds`. Семья природы «history» обязана его назвать."""
    if reads is None:
        if nature == HISTORY_NATURE:
            words.append("reads_history: семья истории обязана назвать, что "
                         "читает из истории до горизонта (объект {need, kind})")
        return None
    if not isinstance(reads, dict):
        words.append("reads_history: чтение истории — объект {need, kind} "
                     "либо null")
        return None
    need = " ".join(str(reads.get("need") or "").split())
    kind = str(reads.get("kind") or "").strip()
    if not need:
        words.append("reads_history.need: потребность в истории называется "
                     "непустым текстом — по ней анкета спрашивает данные")
    if kind not in _words(dicts, "history_read_kinds"):
        words.append(f"reads_history.kind: {kind!r} вне словаря "
                     f"history_read_kinds "
                     f"({', '.join(_words(dicts, 'history_read_kinds'))})")
    return {"need": need, "kind": kind}


def judge_proposal(proposal: dict, row: dict, cat) -> tuple[dict, list, str]:
    """Судьи кода по каждому предложенному полю (§2.3). Возвращает
    (очищенное предложение, слова отказа, имя записи)."""
    words: list[str] = []
    dicts = cat.dictionaries() if cat is not None else {}
    if cat is not None:
        # Имя семьи не должно совпадать ни с записью каталога ЛЮБОГО
        # статуса, ни с record_id ожидающей заявки, ни с именем
        # параметра или условия.
        fams = {str(r.get("id"))
                for r in cat.families_full(status=None)}
        pending = {str(rq.get("record_id")) for rq in (cat.requests() or ())
                   if isinstance(rq, dict)
                   and rq.get("state") in REQUEST_PENDING_STATES}
        taken_names = set(cat.params(status=None) or {}) \
            | set(cat.conditions(status=None) or {})
    else:
        fams, pending, taken_names = set(), set(), set()
    unbound = set((dicts.get("unbound_params") or {}))

    draft_id = str(proposal.get("id") or "").strip()
    if not _ID_RE.match(draft_id) or draft_id in fams or draft_id in pending \
            or draft_id in taken_names:
        draft_id = fallback_id(row["key"])

    title = str(proposal.get("title") or "").strip()
    if len(title) < 3:
        words.append("title: нужен заголовок семьи от трёх знаков")
    subject = str(proposal.get("subject") or "").strip()
    if not subject:
        words.append("subject: предмет семьи не назван")
    nature = str(proposal.get("nature") or "").strip()
    if nature not in _words(dicts, "family_natures"):
        words.append(f"nature: {nature!r} вне словаря family_natures "
                     f"({', '.join(_words(dicts, 'family_natures'))})")
    # Единица размера нарушения — КОД словаря (с 0.5.0 словарь записан
    # картой «код → слово»; слово единицы у модели не спрашивается).
    vu = _nullstr(proposal.get("violation_unit"))
    vs = _nullstr(proposal.get("violation_size"))
    if row["kind"] == "SOFT":
        if vu not in _words(dicts, "violation_units"):
            words.append(f"violation_unit: {vu!r} вне словаря "
                         f"violation_units — у мягкого правила он обязателен")
        if vs not in _words(dicts, "violation_sizes"):
            words.append(f"violation_size: {vs!r} вне словаря "
                         f"violation_sizes — у мягкого правила обязано быть "
                         f"названо, растёт ли штраф с числом единиц или с "
                         f"величиной превышения")
    else:
        vu = vs = None
    reads = _reads_history_judged(proposal.get("reads_history"), nature,
                                  dicts, words)
    params = []
    seen: set = set()
    for p in (proposal.get("params") or ()):
        if not isinstance(p, dict):
            words.append("params: элемент не объект")
            continue
        name = str(p.get("name") or "").strip()
        unit = str(p.get("unit") or "").strip()
        rel = _nullstr(p.get("relation"))
        if not _PNAME_RE.match(name):
            words.append(f"params.name: {name!r} не по образцу имени параметра")
            continue
        if name in seen:
            words.append(f"params.name: повтор имени {name!r} в черновике")
            continue
        seen.add(name)
        if name in taken_names and name not in unbound:
            words.append(f"params.name: имя {name!r} уже занято в каталоге")
            continue
        if unit not in _words(dicts, "param_units"):
            words.append(f"params.unit: {unit!r} вне словаря param_units")
            continue
        if rel is not None and rel not in _words(dicts, "param_relations"):
            words.append(f"params.relation: {rel!r} вне словаря "
                         f"param_relations")
            continue
        params.append({"name": name, "unit": unit, "relation": rel,
                       "note": _nullstr(p.get("note"))})
    sem = proposal.get("semantics")
    if not isinstance(sem, dict) or not str(sem.get("text") or "").strip():
        words.append("semantics: нужен объект {scope, unit, measure, text} "
                     "с непустым text")
        sem = {}
    clean = {"id": draft_id, "title": title, "subject": subject,
             "nature": nature, "violation_unit": vu, "violation_size": vs,
             "reads_history": reads, "params": params,
             "semantics": {k: _nullstr(sem.get(k))
                           for k in ("scope", "unit", "measure")}
             | {"text": str(sem.get("text") or "").strip()}}
    return clean, words, draft_id


def call_composer(llm, row: dict, cat, refusals: list | None = None
                  ) -> tuple[dict | None, list]:
    """Один вызов модели-составителя: задание, разбор, судьи полей."""
    prompt = compose_prompt(row, cat, refusals)
    try:
        reply = llm(prompt)
    except Exception as e:                                # noqa: BLE001
        return None, [f"канал составителя отказал: {str(e)[:200]}"]
    try:
        proposal = llm_mod.parse_json_reply(reply)
    except Exception as e:                                # noqa: BLE001
        return None, [f"ответ модели не разобран: {str(e)[:200]}"]
    clean, words, _rid = judge_proposal(proposal, row, cat)
    if words:
        return None, words
    return clean, []
