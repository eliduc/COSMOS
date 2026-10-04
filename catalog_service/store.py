# -*- coding: utf-8 -*-
"""Экземпляр каталога, черновики рабочей папки и сборка в памяти.

Три слоя (§2.1 проекта):

* `catalog/` — пакет в репозитории, единственное, что читают продукты;
  служба читает его через `template_catalog.current()` и пишет только
  писателем читателя;
* `<CATALOG_WORK_DIR>/` — рабочее место: черновики и журнал действий;
  вне репозитория, вне отпечатка, вне отметки кэша читателя;
* `<temp>/cosmos-catalog-<хеш пути>.lock` — замок папки каталога.

«Одно вычисление для всех сторон»: кандидат «текущее тело ⊕ черновики»
строит ОДНА функция `assemble_in_memory` — её зовёт и сохранение
черновика (чтобы судить его теми же правилами), и сборка (чтобы писать
ровно то, что судили).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import template_catalog as tc
from template_catalog import canon, writer
from template_catalog.reader import Catalog, read_json_file

from .errors import ServiceError, catalog_unavailable
from .journal import now_words

#: Разделы записей и файл черновика ключа словаря.
RECORD_SECTIONS = ("families", "criteria")
DICT_SECTION = "dictionaries"
#: Имя id записи и ключа словаря — один образец на оба.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,50}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
#: Кто подал заявку → слово словаря `actors` (в `actors` нет «CDPM» и
#: «COSMOS» — D01 судит `history[].by` по этому словарю).
ACTOR_OF = {"CDPM": "сессия CDPM", "COSMOS": "сессия COSMOS",
            "аналитик": "аналитик", "владелец": "владелец"}
#: Служебные перечни словарей: их состав — код читателя, не данные.
CLOSED_LISTS = ("request_states", "executor_states", "record_statuses",
                "products", "languages", "stand_results")
#: Вычисляемый читателем ключ: в файле его нет и быть не должно.
DERIVED_KEYS = ("area_param_names",)
#: Переходный носитель порядка семей (его читает дверь COSMOS).
TRANSITION_DIR = "transition"
ORDER_FILE = "families_order.json"
#: Сколько ждать освобождения замка записи внутри процесса.
WRITE_LOCK_TIMEOUT_S = 10.0


# ─────────────────────────────────────────────────────── кандидат

@dataclass
class Candidate:
    """Тело каталога «текущее ⊕ черновики» в памяти."""

    families: list
    criteria: list
    dictionaries: dict
    requests: list
    versions: list
    families_order: list
    header: dict
    derived: list = field(default_factory=list)
    applied: int = 0
    touched: set = field(default_factory=set)
    changes_canon: bool = True

    def body(self) -> dict:
        return {"families": copy.deepcopy(self.families),
                "criteria": copy.deepcopy(self.criteria),
                "dictionaries": copy.deepcopy(self.dictionaries)}

    def fingerprint(self) -> str:
        return canon.fingerprint_of(self.body())

    def catalog(self, path) -> Catalog:
        """Кандидат как экземпляр читателя: `single_file=True` — файлов
        на диске у кандидата нет, судится СОДЕРЖАНИЕ. Тело — канон: ровно
        то, что ляжет на диск (иначе S05 «порядок в файле не канонический»
        считался бы новой находкой черновика)."""
        normed = canon.normalize(self.body())
        docs = {
            "families": {"form_version": 1, "families": normed["families"]},
            "criteria": {"form_version": 1, "criteria": normed["criteria"]},
            "dictionaries": {"form_version": 1, **normed["dictionaries"]},
            "requests": {"form_version": 1,
                         "requests": copy.deepcopy(self.requests)},
        }
        header = copy.deepcopy(self.header)
        header["fingerprint"] = canon.fingerprint_of(normed)
        return Catalog(path=Path(path), header=header, docs=docs,
                       versions_doc={"form_version": 1,
                                     "versions": copy.deepcopy(self.versions)},
                       single_file=True)


# ─────────────────────────────────────────────────────────── склад

class Store:
    """Каталог, черновики и замки одного процесса службы."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._write_lock = threading.Lock()
        self._lock_fd: int | None = None
        self._lock_path: Path | None = None

    # ------------------------------------------------------- каталог
    def catalog(self) -> Catalog:
        """Текущий экземпляр каталога; отказ читателя — 503 словами."""
        try:
            return tc.current(self.settings.catalog_dir)
        except tc.CatalogError as e:
            raise catalog_unavailable(e, self.settings.catalog_dir) from None

    def forget(self) -> None:
        tc.forget_current()

    def catalog_words(self) -> str:
        try:
            cat = self.catalog()
        except ServiceError:
            return "не читается"
        return f"{cat.version()} {cat.fingerprint()[:12]}"

    def families_order(self, cat=None) -> list:
        """Порядок семей переходного носителя; нет файла — пустой список
        (носитель заводит сборка)."""
        cat = cat if cat is not None else self.catalog()
        path = Path(cat.path) / TRANSITION_DIR / ORDER_FILE
        if not path.is_file():
            return []
        try:
            order = read_json_file(path)
        except tc.CatalogError as e:
            raise catalog_unavailable(e, self.settings.catalog_dir) from None
        return [str(x) for x in order] if isinstance(order, list) else []

    # ----------------------------------------------------- черновики
    def draft_path(self, section: str, key: str) -> Path:
        if section in RECORD_SECTIONS:
            if not ID_RE.match(str(key)):
                raise ServiceError(
                    "bad-form", f"id записи не по образцу "
                    f"^[a-z][a-z0-9_]{{2,40}}$: {key!r}", 400)
        elif section == DICT_SECTION:
            if not KEY_RE.match(str(key)):
                raise ServiceError(
                    "bad-form", f"ключ словаря не по образцу "
                    f"^[a-z][a-z0-9_]{{2,50}}$: {key!r}", 400)
        else:
            raise ServiceError("bad-form",
                               f"раздел {section!r} вне перечня: families, "
                               f"criteria, dictionaries", 400)
        return self.settings.drafts_dir / section / f"{key}.json"

    def drafts(self) -> list[dict]:
        """Все черновики рабочей папки, по разделам и именам."""
        base = self.settings.drafts_dir
        if not self.settings.is_edit() or not base.is_dir():
            return []
        out: list[dict] = []
        for section in RECORD_SECTIONS + (DICT_SECTION,):
            folder = base / section
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                doc = self._read_draft(path)
                if doc is not None:
                    out.append(doc)
        return out

    def draft(self, section: str, key: str) -> dict | None:
        """Один черновик рабочей папки. Витрина черновиков не видит — та
        же ветка, что у `drafts()`: рабочая папка на машине владельца
        одна на оба запуска, и служба просмотра над ней не должна
        показывать в карточках то, чего её же счётчики не считают."""
        if not self.settings.is_edit():
            return None
        path = self.draft_path(section, key)
        return self._read_draft(path) if path.is_file() else None

    def _read_draft(self, path: Path) -> dict | None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict):
            return None
        return doc

    def write_draft(self, doc: dict) -> Path:
        section = str(doc.get("section"))
        key = str(doc.get("id") if section in RECORD_SECTIONS
                  else doc.get("key"))
        path = self.draft_path(section, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(doc, ensure_ascii=False, sort_keys=True,
                          indent=1) + "\n"
        tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)
        return path

    def delete_draft(self, section: str, key: str) -> bool:
        path = self.draft_path(section, key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def clear_drafts(self) -> int:
        """Все черновики разом — сборка их фиксирует, «отменить все»
        выбрасывает."""
        n = 0
        base = self.settings.drafts_dir
        if not base.is_dir():
            return 0
        for section in RECORD_SECTIONS + (DICT_SECTION,):
            folder = base / section
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                path.unlink()
                n += 1
        return n

    def draft_counts(self) -> dict:
        counts = {"families": 0, "criteria": 0, "dictionaries": 0}
        for d in self.drafts():
            sec = str(d.get("section"))
            if sec in counts:
                counts[sec] += 1
        return counts

    # ------------------------------------------------- сборка в памяти
    def assemble_in_memory(self, cat, drafts, *, actor: str,
                           today: str | None = None) -> Candidate:
        return assemble_in_memory(cat, drafts, actor=actor, today=today,
                                  order=self.families_order(cat))

    # --------------------------------------------------------- замки
    def lock_path(self) -> Path:
        """Замок папки каталога — во ВРЕМЕННОЙ папке машины, не в
        `catalog/` (помощник тестов копирует папку целиком, и на Windows
        заблокированный файл дал бы PermissionError) и не в рабочей
        папке (два рабочих места с разными рабочими папками не увидели
        бы друг друга над одной папкой каталога)."""
        key = hashlib.sha256(
            str(Path(self.settings.catalog_dir).resolve()).encode("utf-8")
        ).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / f"cosmos-catalog-{key}.lock"

    def acquire_catalog_lock(self) -> Path | None:
        """Исключительная блокировка первого байта файла замка на весь
        срок жизни процесса (режим правки). Занят — отказ старта
        словами: две службы над одной папкой каталога писали бы поверх
        друг друга."""
        if not self.settings.is_edit():
            return None
        path = self.lock_path()
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise ServiceError(
                "write-busy",
                f"папку каталога {Path(self.settings.catalog_dir).name} уже "
                f"правит другая служба (замок {path.name})", 409) from None
        self._lock_fd, self._lock_path = fd, path
        return path

    def release_catalog_lock(self) -> None:
        fd = self._lock_fd
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self._lock_fd = None

    class _Writing:
        def __init__(self, store: "Store") -> None:
            self._store = store

        def __enter__(self):
            if not self._store._write_lock.acquire(
                    timeout=WRITE_LOCK_TIMEOUT_S):
                raise ServiceError(
                    "write-busy",
                    f"другая запись каталога идёт дольше "
                    f"{WRITE_LOCK_TIMEOUT_S:g} с — повторите", 409)
            return self._store

        def __exit__(self, *exc):
            self._store._write_lock.release()
            return False

    def writing(self) -> "Store._Writing":
        """Сериализация записи внутри процесса: параллельный запрос ждёт
        не дольше 10 с, затем 409 `write-busy`."""
        return Store._Writing(self)


# ────────────────────────────────────────── сборка тела в памяти

def assemble_in_memory(cat, drafts, *, actor: str, today: str | None = None,
                       order: list | None = None) -> Candidate:
    """Тело «текущее ⊕ черновики» с производными инвариантами и историей.

    Порядок намеренный: сначала записи и словари, потом производные
    (лексикон, симметрия дополнений, `unbound_params`, порядок семей),
    потом — сверка канона с текущим («черновики не меняют канон»), и
    только после неё дописывается история: строка «изменение» у записи,
    которая на самом деле не изменилась, была бы неправдой."""
    today = today or date.today().isoformat()
    families = [copy.deepcopy(r) for r in cat.families_full(status=None)]
    criteria = [copy.deepcopy(r) for r in cat.criteria(status=None).values()]
    body = cat.body()
    dictionaries = copy.deepcopy(body["dictionaries"])
    requests = cat.requests()
    versions = cat.versions()
    derived: list = []
    order = list(order or [])

    before = {("families", str(r.get("id"))): copy.deepcopy(r)
              for r in families}
    before.update({("criteria", str(r.get("id"))): copy.deepcopy(r)
                   for r in criteria})

    record_drafts = [d for d in drafts
                     if str(d.get("section")) in RECORD_SECTIONS]
    dict_drafts = [d for d in drafts
                   if str(d.get("section")) == DICT_SECTION]

    lists = {"families": families, "criteria": criteria}
    for d in record_drafts:
        section = str(d.get("section"))
        rid = str(d.get("id"))
        record = copy.deepcopy(d.get("record") or {})
        record["id"] = rid
        record.pop("history", None)
        target = lists[section]
        idx = next((i for i, r in enumerate(target)
                    if str(r.get("id")) == rid), None)
        old = before.get((section, rid))
        record["history"] = list((old or {}).get("history") or [])
        if idx is None:
            target.append(record)
        else:
            target[idx] = record

    for d in dict_drafts:
        key = str(d.get("key"))
        dictionaries[key] = copy.deepcopy(d.get("value"))
    for k in DERIVED_KEYS:
        dictionaries.pop(k, None)

    # Производные инварианты тела — ПИСАТЕЛЕМ (решение 0.8 пакета
    # паритета): симметрия дополнений, уход связанного имени из
    # `unbound_params` и порядок лексикона живут в одном месте, и
    # временный каталог кандидата (`candidates.materialize`) собирается
    # ровно тем же законом, что сборка службы.
    derived += writer.refresh_body(
        families, dictionaries,
        before={fid: rec for (sec, fid), rec in before.items()
                if sec == "families"})
    present = [str(r.get("id")) for r in families if r.get("status") == "есть"]
    families_order = writer.refresh_families_order(order, present)
    if families_order != order:
        derived.append("transition/families_order.json приведён к семьям "
                       "«есть» каталога")

    # заявки, чьи черновики стали записями, переходят в «есть» — одной
    # записью с материализацией записи (R02).
    adopted = {str(d.get("request_id")) for d in record_drafts
               if d.get("request_id")}
    for rq in requests:
        if str(rq.get("request_id")) in adopted and \
                rq.get("state") in ("принята", "исполнитель заявлен"):
            rq["state"] = "есть"

    cand = Candidate(families=families, criteria=criteria,
                     dictionaries=dictionaries, requests=requests,
                     versions=versions, families_order=families_order,
                     header=copy.deepcopy(cat.header), derived=derived,
                     applied=len(record_drafts) + len(dict_drafts))

    # «черновики не меняют канон» — сверяется ДО дописывания истории.
    try:
        cand.changes_canon = cand.fingerprint() != cat.fingerprint()
    except ValueError:
        cand.changes_canon = True

    _append_history(cand, record_drafts, before, requests, actor, today)
    return cand


def _append_history(cand: Candidate, record_drafts, before, requests,
                    actor: str, today: str) -> None:
    """Строка истории — только у записи, которая на самом деле изменилась
    (или появилась). Пять полей `$defs/history_item` обязательны."""
    by_request = {str(rq.get("request_id")): rq for rq in requests
                  if isinstance(rq, dict)}
    lists = {"families": cand.families, "criteria": cand.criteria}
    for d in record_drafts:
        section = str(d.get("section"))
        rid = str(d.get("id"))
        rec = next((r for r in lists[section] if str(r.get("id")) == rid), None)
        if rec is None:
            continue
        old = before.get((section, rid))
        note = str(d.get("note") or "правка службой каталога")
        ref = d.get("request_id") or None
        if old is not None and _same_record(old, rec):
            continue
        if old is None:
            rq = by_request.get(str(ref)) if ref else None
            if rq is not None:
                rec["history"] = _history_from_request(rq, actor, today, note)
            else:
                rec["history"] = [{"date": today, "kind": "создание",
                                   "by": actor, "note": note, "ref": None}]
            cand.touched.add((section, rid))
            continue
        kind = "изменение"
        if old.get("status") != rec.get("status") and \
                rec.get("status") == "снята":
            kind = "решение владельца"
        rec["history"] = list(rec.get("history") or []) + [
            {"date": today, "kind": kind, "by": actor, "note": note,
             "ref": ref}]
        cand.touched.add((section, rid))


def _history_from_request(rq: dict, actor: str, today: str,
                          note: str) -> list[dict]:
    """Три строки истории записи, пришедшей заявкой (§2.5): заявка,
    решение владельца, создание. `by` — только слова словаря `actors`."""
    rid = str(rq.get("request_id"))
    src = rq.get("source") if isinstance(rq.get("source"), dict) else {}
    dec = rq.get("decision") if isinstance(rq.get("decision"), dict) else {}
    submitted = str(rq.get("submitted_at") or "")[:10] or today
    decided = str(dec.get("at") or "")[:10] or submitted
    who = ACTOR_OF.get(str(rq.get("submitted_by")), "аналитик")
    classes = f"{src.get('document_class') or '—'} / {src.get('run_class') or '—'}"
    return [
        {"date": submitted, "kind": "заявка", "by": who,
         "note": f"заявка {rid}: {src.get('kind') or 'вручную'}; {classes}",
         "ref": rid},
        {"date": decided, "kind": "решение владельца", "by": "владелец",
         "note": str(dec.get("words") or "решение владельца"), "ref": rid},
        {"date": today, "kind": "создание", "by": actor, "note": note,
         "ref": rid},
    ]


def _same_record(a: dict, b: dict) -> bool:
    """Равенство записей без учёта истории (её дописывает сборка)."""
    def strip(r):
        out = {k: v for k, v in r.items() if k != "history"}
        return json.dumps(out, ensure_ascii=False, sort_keys=True)
    return strip(a) == strip(b)


def draft_doc(section: str, key: str, *, record=None, value=None,
              note: str, by: str, request_id: str | None = None,
              from_candidate: bool = False) -> dict:
    """Файл черновика по форме `schemas/draft.schema.json`.

    `from_candidate` — признак «тело черновика взято из файла-кандидата
    конвейера»: по нему и ТОЛЬКО по нему сборка удаляет
    `catalog/candidates/<раздел>/<id>.json`. Без признака удаление
    сносило бы кандидата и тогда, когда владелец взял в записи не его
    (ревью волны 2), а предпроверка выпуска 9 об этом молчала бы."""
    if section == DICT_SECTION:
        return {"form_version": 1, "section": DICT_SECTION, "key": key,
                "value": value, "note": note, "by": by,
                "saved_at": now_words()}
    return {"form_version": 1, "section": section, "id": key,
            "record": record, "note": note, "by": by,
            "saved_at": now_words(), "request_id": request_id,
            "from_candidate": bool(from_candidate)}


def draft_kind(draft: dict, cat) -> str:
    """Как показать черновик в списке: новая | изменена | снята | словарь."""
    section = str(draft.get("section"))
    if section == DICT_SECTION:
        return "словарь"
    rid = str(draft.get("id"))
    known = {str(r.get("id")) for r in (cat.families_full(status=None)
                                        if section == "families"
                                        else cat.criteria(status=None).values())}
    if rid not in known:
        return "новая"
    if (draft.get("record") or {}).get("status") == "снята":
        return "снята"
    return "изменена"
