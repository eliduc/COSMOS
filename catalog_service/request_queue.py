# -*- coding: utf-8 -*-
"""Заявки на пополнение каталога (`requests.json`).

Имя модуля — `request_queue`, а не `requests`: пакет `requests` в
экосистеме Python занят, и совпадение имён однажды подменило бы импорт.

Заявка проходит состояния словаря `request_states`: подана → принята |
отклонена; принята → исполнитель заявлен; принята | исполнитель заявлен →
есть; принята | исполнитель заявлен → отклонена и отклонена → принята
(пересмотр решения владельцем, шаг 6, решение 0.15). Переход в «есть»
служба делает ТОЛЬКО сборкой: состояние «есть» без записи в разделе —
негодный каталог для обоих продуктов (R02), поэтому «перевести в записи»
(`adopt`) пишет лишь черновик записи, а «есть» и сама запись появляются
одним вызовом писателя.

Номер заявки служба НЕ выдумывает: закон номера один на все три входа и
живёт в читателе (`template_catalog.requests`, §2.1 проекта шага 6) —
содержательный `req-<20 цифр>` от sha256 материала заявки. Так один и тот
же пробел, увиденный CDPM и COSMOS на разных машинах и без связи со
службой, получает один номер, а повтор подачи идемпотентен.

Заявки живут ВНЕ отпечатка каталога: их запись не меняет ни версии, ни
отпечатка — `writer.write_requests` трогает один файл.
"""
from __future__ import annotations

import copy
import importlib
import json
import re
from pathlib import Path

from template_catalog import errors as tc_errors
from template_catalog import receipts as tc_receipts
from template_catalog import writer
from template_catalog.reader import Catalog

from . import executors, identity, markers
from .errors import ServiceError, relative_name
from .journal import now_words
from .store import ID_RE, draft_doc

#: Состояния, при которых заявка считается открытой.
OPEN_STATES = ("подана", "принята", "исполнитель заявлен")
#: Версия схемы квитанции и слова повторной выдачи — НЕ свои, а общего
#: закона квитанции (`template_catalog.receipts`): пишет квитанцию
#: служба, а судят её оба клиента (COSMOS и CDPM) той же функцией по той
#: же схеме. Своя копия строки «receipt/1» у писателя и у читателя
#: разошлась бы в день первой смены версии.
RECEIPT_SCHEMA_VERSION = tc_receipts.RECEIPT_SCHEMA_VERSION
REPEAT_NOTE = tc_receipts.REPEAT_NOTE
#: Кто вправе подать заявку с экрана: только роли рабочего места.
SCREEN_SUBMITTERS = ("владелец", "аналитик")
DECISIONS = ("принята", "отклонена")
#: Материал ключа заявки в ссылке продукта: 64 hex закона §2.1.
KEY_RE = re.compile(r"^[0-9a-f]{64}$")
#: Папка файлов-кандидатов конвейера пополнения (§3.5 проекта шага 6):
#: вне отпечатка, читает её только служба при `adopt`.
CANDIDATES_DIR = "candidates"

#: Переходы, разрешённые решением владельца (§2.2 шага 6). Ключ — из
#: чего, значение — во что. Таблица закрыта: незаконный переход
#: отказывает словами, а не «получается сам собой».
DECIDE_FROM = {
    "подана": ("принята", "отклонена"),
    "принята": ("отклонена",),
    "исполнитель заявлен": ("отклонена",),
    "отклонена": ("принята",),
}


# ────────────────────────────────────────────── закон номера (читатель)

def law():
    """Модуль закона номера и ключа CR — `template_catalog.requests`.

    Импорт ленивый и в одном месте: закон принадлежит ЧИТАТЕЛЮ (его
    применяют и служба, и оба продукта), а служба на читателя опирается,
    не копируя формулу. Читателя без закона (срез до шага 6) служба
    называет словами, а не считает номера сама."""
    try:
        return importlib.import_module("template_catalog.requests")
    except ImportError as e:                                # pragma: no cover
        raise ServiceError(
            "catalog-unavailable",
            "закон номера заявки живёт в читателе "
            "(template_catalog/requests.py), а в этом срезе читателя его "
            f"нет: {e} — обновите срез, служба номера не выдумывает",
            503) from None


def request_id_of(key: str) -> str:
    return law().request_id_of(key)


def cr_key_of(request_id: str) -> str:
    return law().cr_key_of(request_id)


def id_ok(request_id) -> bool:
    return bool(law().REQUEST_ID_RE.match(str(request_id or "")))


def key_of(section: str, record_id: str, draft, kinds,
           key: str | None = None) -> str:
    """Материал-ключ заявки по её содержанию (§2.1).

    Три случая закона: явный ключ из ссылки продукта (`key=` в адресе
    формы), заявка на вид исполнителя (раздел, запись, набор видов) и
    ручной черновик (раздел, запись, канон черновика)."""
    mod = law()
    if key:
        return str(key)
    if draft is None and kinds:
        return mod.kind_request_key(section, record_id, kinds)
    return mod.manual_key(section, record_id, mod.draft_canon(draft))


def decision_of(rq: dict) -> dict:
    """Решение владельца в форме квитанции: `{by, at, words, version}`.

    `version` — номер решения: 0, пока решения нет, и 1 у записанного.
    Считать пересмотры служба пока не вправе: носителем счётчика была бы
    запись заявки (`$defs/request.decision`), а он закрыт схемой каталога
    0.5.0 тремя полями. Отсюда закон: версия НЕ УБЫВАЕТ и говорит ровно
    то, что каталог доказывает, — «решения нет» либо «решение есть»;
    счётчик пересмотров вводится полем записи в 0.6.0 (открытый вопрос
    сдачи K06+K07).

    ВАЖНО читателю (K08): ДО каталога 0.6.0 на `version` при ПЕРЕСМОТРЕ
    решения полагаться нельзя — «отклонена → принята» оставляет
    `version: 1` и меняет только `at`. Отличать новое решение от старого
    обязано время `at`, а не номер версии; как только у записи заявки
    появится поле `decision.version`, его будет отдавать эта же функция,
    а 0/1 останутся только для записей без поля."""
    d = rq.get("decision") if isinstance(rq.get("decision"), dict) else {}
    return {"by": d.get("by"), "at": d.get("at"), "words": d.get("words"),
            "version": 1 if d.get("by") else 0}


def receipt(cat, rq: dict, *, note: str | None = None, repeat: bool = False,
            journal_at=None, journal_actor=None, submitted_by=None,
            words: str = "", problems=()) -> dict:
    """Квитанция заявки — тело 201 и 200 по схеме `receipt/1`.

    Квитанция отвечает на четыре вопроса клиента сразу: ТА ЛИ заявка
    (`request_id`, `section`, `record_id`, `canon`), В КАКОМ она
    состоянии (`state`, `decision`), НА КАКОМ каталоге выдана (`catalog`)
    и ГДЕ след приёма (`journal_ref`, `issued_at`). Прежние поля тела
    (`request`, `submitted_by`, `words`, `problems`) остаются: тело
    только ДОПОЛНЯЕТСЯ, и старый клиент его не отвергает."""
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "request_id": str(rq.get("request_id")),
        "state": rq.get("state"),
        "note": note,
        "repeat": bool(repeat),
        "catalog": {"version": cat.version(),
                    "fingerprint": cat.fingerprint()},
        "section": rq.get("section"),
        "record_id": rq.get("record_id"),
        "canon": law().draft_canon(rq.get("draft")),
        "decision": decision_of(rq),
        "journal_ref": {"op": "request-submit", "at": journal_at,
                        "actor": journal_actor},
        "issued_at": now_words(),
        "request": copy.deepcopy(rq),
        "submitted_by": submitted_by or rq.get("submitted_by"),
        "words": words,
        "problems": [_p(p) for p in problems or ()],
    }


def canon_of(rq: dict) -> str:
    """Канон содержания заявки — им судится повтор подачи.

    У черновика это канон записи (закон Г5 CDPM), у заявки на вид —
    набор видов. Один номер с ИНЫМ каноном — не повтор, а столкновение:
    служба отказывает `request-exists` и называет прежний источник."""
    draft = rq.get("draft")
    if isinstance(draft, dict):
        return "draft:" + str(law().draft_canon(draft) or "")
    kinds = rq.get("executor_kinds_requested") or []
    return "kinds:" + ",".join(sorted({str(k) for k in kinds}))


# ───────────────────────────────────────────────────────── поиск

def find(requests: list, rid: str) -> dict:
    for rq in requests or []:
        if str(rq.get("request_id")) == str(rid):
            return rq
    raise ServiceError("unknown-request",
                       f"заявки {rid} в каталоге нет", 404)


def get(requests: list, rid: str) -> dict | None:
    for rq in requests or []:
        if str(rq.get("request_id")) == str(rid):
            return rq
    return None


def open_for(requests: list, section: str, record_id: str,
             *, other_than: str | None = None) -> dict | None:
    for rq in requests or []:
        if other_than is not None \
                and str(rq.get("request_id")) == str(other_than):
            continue
        if str(rq.get("section")) == section \
                and str(rq.get("record_id")) == record_id \
                and rq.get("state") in OPEN_STATES:
            return rq
    return None


def open_count(requests: list) -> int:
    return sum(1 for rq in requests or [] if rq.get("state") in OPEN_STATES)


def in_work(requests: list) -> list[dict]:
    """Заявки «исполнитель заявлен» — те, по которым идёт CR продукта."""
    return [rq for rq in requests or []
            if rq.get("state") == "исполнитель заявлен"]


# ─────────────────────────────────────────────────── суд кандидата

def catalog_with(cat, requests: list) -> Catalog:
    """Каталог с другим списком заявок — в памяти, для суда правилами
    читателя (R01–R03, A-правила по `source.*` и `decision.words`)."""
    body = cat.body()
    docs = {"families": {"form_version": 1, "families": body["families"]},
            "criteria": {"form_version": 1, "criteria": body["criteria"]},
            "dictionaries": {"form_version": 1, **body["dictionaries"]},
            "requests": {"form_version": 1,
                         "requests": copy.deepcopy(requests)}}
    return Catalog(path=Path(cat.path), header=copy.deepcopy(cat.header),
                   docs=docs,
                   versions_doc={"form_version": 1, "versions": cat.versions()},
                   single_file=True)


def _mine(problems, rid: str) -> list:
    return [p for p in problems
            if p.where == f"requests/{rid}"
            or p.where.startswith(f"requests/{rid}.")
            or p.where.startswith(f"requests/{rid}/")]


def _judge_words(errors: list) -> str:
    """Слова отказа 422 — по тому, ЧТО именно не по правилам.

    Слова решения владельца судятся теми же A-правилами, что и черновик
    (обезличенность), но чинит их человек иначе: не форму заявки, а свою
    фразу. Поэтому у них свои слова (§5.1 проекта шага 6, буквально), а
    не общее «заявка не по правилам каталога»."""
    said = [p for p in errors if str(p.where).endswith(".decision.words")]
    if said:
        return ("слова решения содержат данные заказчика: "
                + "; ".join(p.message for p in said[:3])
                + " — перепишите словами о механике")
    return ("заявка не по правилам каталога: "
            + "; ".join(f"{p.where}: {p.message}" for p in errors[:3])
            + (f" (и ещё {len(errors) - 3})" if len(errors) > 3 else ""))


def _judge(settings, cat, requests: list, rid: str) -> list:
    """Проблемы кандидата по адресу ЭТОЙ заявки; ошибки — отказ 422."""
    mine = _mine(catalog_with(cat, requests).validate(
        forbidden_terms=markers.terms(settings)), rid)
    errors = [p for p in mine if p.severity == "error"]
    if errors:
        raise ServiceError("invalid-request", _judge_words(errors), 422,
                           problems=mine)
    return mine


def _save(store, cat, requests: list) -> None:
    writer.write_requests(store.settings.catalog_dir, requests)
    store.forget()


# ──────────────────────────────────────────────────────── подача

def _source_of(body: dict) -> dict:
    src = body.get("source") if isinstance(body.get("source"), dict) else {}
    return {"kind": src.get("kind") or "вручную",
            "document_class": src.get("document_class"),
            "run_class": src.get("run_class"), "ref": src.get("ref")}


def _kinds_of(body: dict):
    kinds = body.get("executor_kinds_requested")
    return [str(k) for k in kinds] if isinstance(kinds, list) and kinds \
        else None


def _section_of(body: dict) -> str:
    section = str(body.get("section") or "")
    if section not in ("families", "criteria"):
        raise ServiceError("bad-form",
                           "раздел заявки — families либо criteria", 400)
    return section


def _record_id_of(body: dict) -> str:
    record_id = str(body.get("record_id") or "")
    if not ID_RE.match(record_id):
        raise ServiceError("bad-form",
                           f"record_id не по образцу id: {record_id!r}", 400)
    return record_id


def _screen_request(settings, body: dict) -> dict:
    """Заявка с экрана: форма Ш5 §3 плюс `key` и `submitted_at` шага 6."""
    section = _section_of(body)
    record_id = _record_id_of(body)
    said = body.get("submitted_by")
    if said is not None and str(said) not in SCREEN_SUBMITTERS:
        raise ServiceError(
            "submitter-not-allowed",
            f"подать от имени {said!r} может только продукт (заголовки "
            f"{identity.PRODUCT_HEADER} и {identity.TOKEN_HEADER}): с экрана "
            f"заявку подаёт роль рабочего места "
            f"({', '.join(SCREEN_SUBMITTERS)})", 400)
    draft = body.get("draft") if isinstance(body.get("draft"), dict) else None
    kinds = _kinds_of(body)
    if draft is None and not kinds:
        raise ServiceError("bad-form",
                           "заявка без черновика записи и без видов "
                           "исполнителя — предмета решения нет", 400)
    key = body.get("key")
    if key is not None and not KEY_RE.match(str(key)):
        raise ServiceError(
            "bad-request-id",
            f"ключ материала заявки — 64 hex закона читателя; дано {key!r}",
            422)
    rid = request_id_of(key_of(section, record_id, draft, kinds,
                               str(key) if key else None))
    return {"request_id": rid, "state": "подана", "section": section,
            "record_id": record_id,
            "submitted_at": str(body.get("submitted_at") or now_words()),
            "submitted_by": settings.actor, "source": _source_of(body),
            "draft": copy.deepcopy(draft),
            "executor_kinds_requested": kinds,
            "decision": {"by": None, "at": None, "words": None},
            "cr_key": None}


def _machine_request(body: dict, product: str) -> dict:
    """Заявка от продукта: запись §6 схемы целиком, номер — по закону.

    Номер черновика новой записи служба пересчитать не может (текста
    формулировки у неё нет) и принимает по образцу; номер заявки на вид
    она пересчитывает и судит — это её единственная защита от «продукт
    посчитал не тем законом»."""
    section = _section_of(body)
    record_id = _record_id_of(body)
    said = str(body.get("submitted_by") or "")
    if said != product:
        raise ServiceError(
            "bad-form",
            f"заголовок {identity.PRODUCT_HEADER} называет {product!r}, а "
            f"submitted_by заявки — {said!r}: подать от чужого имени нельзя",
            400)
    if str(body.get("state") or "") != "подана":
        raise ServiceError(
            "bad-form",
            "продукт подаёт заявку в состоянии «подана»: решение — за "
            f"владельцем; дано {body.get('state')!r}", 400)
    dec = body.get("decision") if isinstance(body.get("decision"), dict) else {}
    if any(dec.get(k) for k in ("by", "at", "words")):
        raise ServiceError("bad-form",
                           "решение владельца продукт не подаёт: decision — "
                           "три нуля", 400)
    if body.get("cr_key") is not None:
        raise ServiceError("bad-form",
                           "ключ CR продукт не подаёт: он пишется после "
                           "решения владельца (PUT /api/requests/{id}/cr_key)",
                           400)
    rid = str(body.get("request_id") or "")
    if not id_ok(rid):
        raise ServiceError(
            "bad-form",
            f"номер заявки не по образцу req-<20 цифр>: {rid!r}", 400)
    draft = body.get("draft") if isinstance(body.get("draft"), dict) else None
    kinds = _kinds_of(body)
    if draft is None and not kinds:
        raise ServiceError("bad-form",
                           "заявка без черновика записи и без видов "
                           "исполнителя — предмета решения нет", 400)
    if draft is None:
        want = request_id_of(law().kind_request_key(section, record_id, kinds))
        if want != rid:
            raise ServiceError(
                "bad-request-id",
                f"номер заявки не по закону читателя: ожидался {want} для "
                f"{section}/{record_id} и видов {', '.join(sorted(kinds))}; "
                f"дано {rid}", 422, extra={"expected": want})
    return {"request_id": rid, "state": "подана", "section": section,
            "record_id": record_id,
            "submitted_at": str(body.get("submitted_at") or now_words()),
            "submitted_by": product, "source": _source_of(body),
            "draft": copy.deepcopy(draft),
            "executor_kinds_requested": kinds,
            "decision": {"by": None, "at": None, "words": None},
            "cr_key": None}


def build(settings, body: dict, *, product: str | None = None) -> dict:
    """Запись заявки из тела запроса — общее вычисление подачи и `dry-run`."""
    body = body if isinstance(body, dict) else {}
    return _machine_request(body, product) if product \
        else _screen_request(settings, body)


def submit(store, journal, body: dict, *,
           product: str | None = None) -> tuple[dict, int]:
    """Подать заявку с экрана (аналитик+) либо машинно от продукта.

    Возврат — (тело ответа, код HTTP): 201 у новой заявки, 200 у повтора
    той же заявки с тем же каноном (идемпотентность закона номера). Тело
    в обоих случаях — КВИТАНЦИЯ по схеме `receipt/1`: клиент считает
    заявку поданной только по ней, и никакой иной 2xx приёмом не
    является (П0 §4.1, K07)."""
    settings = store.settings
    identity.require(settings, "request-submit")
    rq = build(settings, body, product=product)
    rid = rq["request_id"]
    with store.writing():
        cat = store.catalog()
        requests = cat.requests()
        known = get(requests, rid)
        if known is not None:
            return _known(cat, journal, known, rq), 200
        clash = open_for(requests, rq["section"], rq["record_id"],
                         other_than=rid)
        if clash is not None:
            # Иной черновик по той же записи — столкновение СОДЕРЖАНИЯ, и
            # код у него тот же, что у иного канона под тем же номером:
            # `request-exists` (П0 §4.1, редакция «0-бис»). Прежний
            # отдельный код `duplicate-request` снят — клиенту важно одно:
            # «заявка по этой записи уже есть, и она не ваша».
            raise ServiceError(
                "request-exists",
                f"по записи {rq['section']}/{rq['record_id']} уже есть "
                f"открытая заявка {clash.get('request_id')} (состояние "
                f"«{clash.get('state')}») иным черновиком; дубль не "
                f"заводится", 409,
                extra={"existing": clash.get("request_id"),
                       "state": clash.get("state"),
                       "ref": str((clash.get("source") or {}).get("ref")
                                  or "") or None})
        candidate = list(requests) + [rq]
        warnings = _judge(settings, cat, candidate, rid)
        _save(store, cat, candidate)
        who = product or settings.actor
        row = journal.append(actor=settings.actor, op="request-submit",
                             target=f"{rq['section']}/{rq['record_id']}",
                             outcome="ok",
                             words=f"заявка {rid} подана "
                                   + (f"по вызову {product}" if product
                                      else "экран")
                                   + f": {rq['source']['kind']}")
        row = row or {}
        return receipt(cat, rq, note=None, repeat=False,
                       journal_at=row.get("at") or rq.get("submitted_at"),
                       journal_actor=row.get("actor") or settings.actor,
                       submitted_by=who,
                       words=accepted_words(rid, product),
                       problems=warnings), 201


def accepted_words(rid: str, product: str | None) -> str:
    """Слова приёма заявки (§5.1 проекта шага 6, буквально).

    Приём службы говорит словами так же, как отказ: продукту и человеку
    важно одно и то же — заявка записана, но решения ещё нет. Инструмент
    импорта кладёт эту строку в файл-ответ, и она же уходит в подсказку
    экрана."""
    return (f"заявка от продукта {product} принята: {rid} (подана); "
            f"решение — за владельцем" if product else
            f"заявка {rid} подана с экрана: решение — за владельцем")


def _known(cat, journal, known: dict, rq: dict) -> dict:
    """Ответ на повтор подачи: тот же номер — тот ли предмет.

    Тот же канон — та же КВИТАНЦИЯ со словами «уже подана» (200, продукт
    спокоен; `repeat: true`). Иной канон под тем же номером —
    столкновение содержания: отказ со ссылкой на прежний источник.
    Закрытая заявка отказывает отдельно и НАЗЫВАЕТ РЕШЕНИЕ (`extra.state`
    и `extra.decision` — просьба CDPM, K08): «отклонена» без слов
    владельца, его версии и времени прогон показать не может, и строка
    панели стала бы догадкой."""
    rid = str(known.get("request_id"))
    if known.get("state") == "отклонена":
        decision = decision_of(known)
        words = str(decision.get("words") or "")
        raise ServiceError(
            "request-closed",
            f"заявка {rid} отклонена владельцем: {words}; повторная подача — "
            f"только после пересмотра решения на экране «Заявки»", 409,
            extra={"existing": rid, "state": "отклонена",
                   "decision": {"words": decision.get("words"),
                                "version": decision.get("version"),
                                "at": decision.get("at")}})
    if canon_of(known) != canon_of(rq):
        ref = str((known.get("source") or {}).get("ref") or "") or "—"
        raise ServiceError(
            "request-exists",
            f"заявка {rid} уже подана из {ref} иным черновиком; дубль не "
            f"заводится", 409,
            extra={"existing": rid, "ref": ref,
                   "state": known.get("state")})
    # Ссылка на журнал — ИЗ ЖУРНАЛА: у повторной выдачи это строка о
    # ПЕРВОМ приёме. Прежде сюда клались поля заявки (`submitted_at`,
    # `submitted_by`), и `journal_ref` называл момент составления у
    # продукта, а не момент записи службой — ссылкой на след приёма это
    # не было. Строки нет (журнал выключен либо копия каталога пришла
    # без него) — честные null, а не догадка.
    row = journal.last(op="request-submit", contains=rid) or {}
    return receipt(cat, known, note=REPEAT_NOTE, repeat=True,
                   journal_at=row.get("at"),
                   journal_actor=row.get("actor"),
                   words=f"заявка {rid} уже подана: состояние "
                         f"«{known.get('state')}» — дубль не заводится")


def dry_run(store, body: dict, *, product: str | None = None) -> dict:
    """Предпросмотр отказа до подачи: проблемы и номер по закону.

    Ни записи, ни замка, ни строки журнала — форма экрана обязана уметь
    спросить «что скажет служба», не оставляя следа."""
    settings = store.settings
    rq = build(settings, body, product=product)
    rid = rq["request_id"]
    cat = store.catalog()
    known = get(cat.requests(), rid)
    problems = _mine(catalog_with(cat, list(cat.requests()) + [rq]).validate(
        forbidden_terms=markers.terms(settings)), rid) if known is None else []
    section, record_id = rq["section"], rq["record_id"]
    ids = ({str(r.get("id")) for r in cat.families_full(status=None)}
           if section == "families" else set(cat.criteria(status=None)))
    return {"request_id": rid, "cr_key_expected": cr_key_of(rid),
            "known": None if known is None else copy.deepcopy(known),
            # Форма обязана знать, что предлагать: у существующей записи
            # законна ТОЛЬКО заявка на вид исполнителя (R02), и рисовать
            # ей карточку черновика — обещать невозможное.
            "record_exists": record_id in ids,
            "open_request": copy.deepcopy(
                open_for(cat.requests(), section, record_id, other_than=rid)),
            "problems": [_p(p) for p in problems]}


# ─────────────────────────────────────────────────────── решения

def decide(store, journal, rid: str, body: dict) -> dict:
    """Принять, отклонить или пересмотреть решение (владелец)."""
    settings = store.settings
    identity.require(settings, "request-decide")
    body = body if isinstance(body, dict) else {}
    decision = str(body.get("decision") or "")
    if decision not in DECISIONS:
        raise ServiceError("bad-form",
                           "решение — «принята» либо «отклонена»", 400)
    words = str(body.get("words") or "").strip()
    if not words:
        raise ServiceError("words-required",
                           "решение владельца без слов не записывается", 422)
    with store.writing():
        cat = store.catalog()
        requests = cat.requests()
        rq = find(requests, rid)
        was = str(rq.get("state"))
        if decision not in DECIDE_FROM.get(was, ()):
            raise ServiceError(
                "bad-transition",
                f"заявка {rid} в состоянии «{was}» — решение «{decision}» "
                f"не принимается"
                + (f"; законно: {', '.join(DECIDE_FROM[was])}"
                   if DECIDE_FROM.get(was) else " (решать нечего)"), 409)
        rq["state"] = decision
        rq["decision"] = {"by": identity.ACTORS["owner"], "at": now_words(),
                          "words": words}
        _judge(settings, cat, requests, rid)
        _save(store, cat, requests)
        again = "пересмотр: " if was == "отклонена" else ""
        journal.append(actor=settings.actor, op="request-decide",
                       target=f"requests/{rid}", outcome="ok",
                       words=f"{again}{was} → {decision}: {words}")
        return rq


def set_cr_key(store, journal, rid: str, body: dict,
               *, by: str | None = None) -> dict:
    """Заявить исполнителя: ключ CR по закону читателя.

    Ключ не приносится «какой сложился в очереди», а сверяется с
    `cr_key_of(request_id)`: CR — проекция заявки, и тот же CR обязан
    открыться в любом экземпляре COSMOS без переноса очереди."""
    settings = store.settings
    identity.require(settings, "request-cr-key")
    key = str((body or {}).get("cr_key") or "")
    with store.writing():
        cat = store.catalog()
        requests = cat.requests()
        rq = find(requests, rid)
        want = cr_key_of(rid)
        if key != want:
            raise ServiceError(
                "bad-cr-key",
                f"ключ CR обязан быть {want} (закон читателя от номера "
                f"заявки); дано {key!r}", 422, extra={"expected": want})
        if rq.get("state") == "исполнитель заявлен" and rq.get("cr_key") == key:
            # Повтор сверки COSMOS — тот же ответ без второй записи.
            return {**rq, "words": cr_key_words(rid, key, by)}
        if rq.get("state") != "принята":
            raise ServiceError(
                "bad-transition",
                f"ключ CR принимается только у принятой заявки; {rid} — "
                f"«{rq.get('state')}»", 409)
        rq["state"] = "исполнитель заявлен"
        rq["cr_key"] = key
        _judge(settings, cat, requests, rid)
        _save(store, cat, requests)
        journal.append(actor=settings.actor, op="request-cr-key",
                       target=f"requests/{rid}", outcome="ok",
                       words=f"исполнитель заявлен: {key}"
                             + (f" (по вызову {by})" if by else ""))
        return {**rq, "words": cr_key_words(rid, key, by)}


def cr_key_words(rid: str, key: str, by: str | None) -> str:
    """Слова записи ключа CR (§5.1 проекта шага 6, буквально).

    Кто позвал — часть слов: ключ пишет либо владелец с экрана, либо
    COSMOS при сверке своей очереди с каталогом, и по строке ответа
    видно, чей это след."""
    return (f"ключ CR {key} записан "
            + (f"по вызову {by}" if by else "с экрана владельцем")
            + f": заявка {rid} — «исполнитель заявлен»")


# ────────────────────────────────────────────── кандидат конвейера

def candidate_path(settings, section: str, record_id: str) -> Path:
    """Адрес файла-кандидата строит СЛУЖБА, а не запрос.

    Путь снаружи не принимается ни в каком виде: `adopt` называет
    заявку, а файл ищется по её разделу и записи под `catalog/candidates/`
    — иначе «импортируйте вот этот файл» стало бы чтением любого файла
    машины."""
    return (Path(settings.catalog_dir) / CANDIDATES_DIR / str(section)
            / f"{record_id}.json")


def candidate_meta(settings, rq: dict) -> tuple[str | None, dict | None]:
    """Имя файла кандидата этой заявки и его свидетельство — для списка.

    Битый или чужой файл не роняет список заявок: имя не отдаётся, и
    экран честно показывает «кандидата нет»."""
    path = candidate_path(settings, str(rq.get("section")),
                          str(rq.get("record_id")))
    if not path.is_file():
        return None, None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(doc, dict) \
            or str(doc.get("request_id")) != str(rq.get("request_id")):
        return None, None
    evidence = doc.get("evidence")
    return (relative_name(path, settings.catalog_dir),
            evidence if isinstance(evidence, dict) else None)


def _load_candidate(settings, rq: dict) -> dict:
    """Кандидат по схеме читателя (`template_catalog.candidates`)."""
    section, record_id = str(rq.get("section")), str(rq.get("record_id"))
    path = candidate_path(settings, section, record_id)
    if not path.is_file():
        raise ServiceError(
            "no-candidate",
            f"файла кандидата {CANDIDATES_DIR}/{section}/{record_id}.json "
            f"нет: ветку CR слейте в дерево — конвейер кладёт кандидата "
            f"коммитом свидетельства", 404)
    try:
        mod = importlib.import_module("template_catalog.candidates")
    except ImportError as e:                                # pragma: no cover
        raise ServiceError(
            "catalog-unavailable",
            "правила файла-кандидата живут в читателе "
            f"(template_catalog/candidates.py), а в этом срезе их нет: {e}",
            503) from None
    try:
        doc = mod.load_candidate(path)
    except tc_errors.CatalogError as e:
        raise ServiceError(
            "candidate-mismatch",
            f"файл кандидата не по схеме candidate.schema.json: {e}",
            422) from None
    except OSError as e:                                    # pragma: no cover
        raise ServiceError(
            "no-candidate",
            f"файл кандидата не читается: {e}", 404) from None
    if str(doc.get("request_id")) != str(rq.get("request_id")):
        raise ServiceError(
            "candidate-mismatch",
            f"кандидат назван заявкой {doc.get('request_id')}, а импорт идёт "
            f"по заявке {rq.get('request_id')}", 422)
    want = cr_key_of(str(rq.get("request_id")))
    if str(doc.get("cr_key")) != want:
        raise ServiceError(
            "candidate-mismatch",
            f"ключ CR кандидата {doc.get('cr_key')!r} не равен ключу заявки "
            f"{want}", 422)
    if str(doc.get("section")) != section or str(doc.get("id")) != record_id:
        raise ServiceError(
            "candidate-mismatch",
            f"кандидат назван {doc.get('section')}/{doc.get('id')}, а заявка "
            f"— {section}/{record_id}", 422)
    return doc


def _no_executor_words(cat, section: str, record_id: str, missing: list,
                       *, candidate: bool) -> str:
    """Слова отказа `executor-not-present` (сторона COSMOS, приложение Г).

    Отказ называет ЗАПИСЬ, а не только вид: раньше он говорил «вида нет
    ни в одном файле привязок» — правду о каталоге целиком и неправду об
    этой записи, потому что вид, написанный у соседней семьи, к новой
    отношения не имеет.

    Называется и то, ЧЕМ судили: свидетельство поддержки на этом
    каталоге. Строка привязок без стендового подтверждения по виду —
    «не проверено», и поддержкой она не считается; про это стоит сказать
    прямо, иначе владелец будет искать в файле строку, которая там
    есть."""
    return (f"у записи {section}/{record_id} не подтверждена поддержка видов "
            f"{', '.join(missing)} ни одним продуктом: свидетельство "
            f"поддержки на каталоге {cat.version()} "
            f"{cat.fingerprint()[:12]} — «есть» нет (строка привязок без "
            f"стенда по виду и файл привязок иного каталога дают «не "
            f"проверено»)"
            + ("; кандидат свидетельства не несёт" if candidate else "")
            + "; продолжить осознанно — полем confirm со словами")


def _candidate_present_kinds(doc: dict) -> set:
    """Виды, которые свидетельство кандидата объявляет «есть».

    У НОВОЙ записи строки «есть» в файле привязок дерева нет по
    построению (B02: привязки не знают записи, которой ещё нет), поэтому
    предпроверка `adopt` смотрит на свидетельство гейтов конвейера."""
    evidence = doc.get("evidence")
    rows = (evidence or {}).get("executors") if isinstance(evidence, dict) \
        else None
    out: set = set()
    for row in rows or ():
        if isinstance(row, dict) and str(row.get("state")) == "есть":
            out.add(str(row.get("kind")))
    return out


def _candidate_words(doc: dict) -> str:
    """Слова гейтов, ветки и коммита — в заметку черновика и в журнал."""
    evidence = doc.get("evidence") if isinstance(doc.get("evidence"), dict) \
        else {}
    gates = evidence.get("gates") if isinstance(evidence.get("gates"), dict) \
        else {}
    stand = gates.get("stand") if isinstance(gates.get("stand"), dict) else {}
    parts = []
    if evidence.get("branch"):
        parts.append(f"ветка {evidence['branch']}")
    if evidence.get("commit"):
        parts.append(f"коммит {str(evidence['commit'])[:12]}")
    if stand:
        parts.append(f"стенд {stand.get('passed', 0)}/{stand.get('failed', 0)}"
                     f"/{stand.get('skipped', 0)}")
    if gates.get("bindings_gate"):
        parts.append(f"привязки {gates['bindings_gate']}")
    # Паритет на временном каталоге (гейт CR, §2.4 проекта паритета):
    # заметка черновика и журнал службы обязаны называть и его — им
    # владелец судит, сверена ли запись кандидата с кодом решателя.
    parity = gates.get("parity") if isinstance(gates.get("parity"), dict) \
        else None
    if parity is not None:
        ok = parity.get("ok")
        parts.append("паритет " + ("ok" if ok is True else
                                   "красен" if ok is False else "не судился"))
    if gates.get("pytest"):
        parts.append(f"pytest {gates['pytest']}")
    return ", ".join(parts) or "свидетельства гейтов в кандидате нет"


# ─────────────────────────────────────────────── перевод в записи

def adopt(store, journal, rid: str, body: dict) -> dict:
    """Перевести заявку в записи (владелец): черновик записи и только он.

    `requests.json` не трогается, и между `adopt` и сборкой живой каталог
    остаётся годным для обоих продуктов. Состояние «есть» и сама запись
    появятся ОДНИМ вызовом писателя при сборке (R02).

    С кандидатом (`{"candidate": true}`) запись берётся из файла
    конвейера — целиком, вместе со стендовыми задачами: схема запрещает
    `stand_tasks` у черновика заявки, и другого пути принести задачи в
    запись нет."""
    settings = store.settings
    identity.require(settings, "request-adopt")
    body = body if isinstance(body, dict) else {}
    confirm = body.get("confirm")
    confirm = str(confirm).strip() if confirm else ""
    want_candidate = bool(body.get("candidate"))
    cat = store.catalog()
    requests = cat.requests()
    rq = find(requests, rid)
    if rq.get("state") not in ("принята", "исполнитель заявлен"):
        raise ServiceError(
            "bad-transition",
            f"в записи переводится принятая заявка; {rid} — "
            f"«{rq.get('state')}»", 409)
    section = str(rq.get("section"))
    record_id = str(rq.get("record_id"))
    doc = None
    if want_candidate:
        doc = _load_candidate(settings, rq)
        record, wanted = _record_of_candidate(doc, section, record_id)
        _judge_candidate(settings, cat, doc, rid)
    else:
        # Кандидат по этой заявке есть, а берут не его — это не «ещё
        # один путь», а расхождение: в записи ляжет черновик заявки без
        # стендовых задач и свидетельства, а файл конвейера останется
        # лежать. Отказ словами; осознанный обход — тем же полем
        # `confirm`, что и у «исполнителя нет» (ревью волны 2).
        name, _ = candidate_meta(settings, rq)
        if name and not confirm:
            raise ServiceError(
                "candidate-ignored",
                f"кандидат по этой заявке есть, а взят не он: {name} — "
                f"переводите с {{\"candidate\": true}} (запись придёт со "
                f"стендовыми задачами и свидетельством) либо продолжите "
                f"осознанно полем confirm со словами; файл кандидата "
                f"сборка тогда не удалит", 412,
                extra={"candidate": name})
        record, wanted = _record_of(cat, rq, section, record_id)
    # Один закон «исполнимо»: ячейки СВИДЕТЕЛЬСТВА этой записи (строка
    # привязок + стенд по виду + файл привязок ТОГО ЖЕ каталога), а не
    # первое попавшееся «есть» в файле. Второй, более слабый закон рядом
    # со свидетельством означал бы, что служба и CDPM отвечают по-разному
    # на один вопрос (П0 §3.7, общий закон 4).
    support = executors.support_cells_of_record(cat, settings, section,
                                                record_id)
    have = executors.supported_kinds(support)
    if doc is not None:
        # Свидетельство гейтов конвейера: у НОВОЙ записи строки привязок
        # нет по построению (B02), и кандидат — единственное, чем она
        # доказывается до первой сборки.
        have = have | _candidate_present_kinds(doc)
    missing = [k for k in wanted if k not in have]
    if missing and not confirm:
        raise ServiceError(
            "executor-not-present",
            _no_executor_words(cat, section, record_id, missing,
                               candidate=doc is not None), 412,
            extra={"kinds": missing, "record": f"{section}/{record_id}",
                   "support": support})
    note = confirm or (
        f"кандидат конвейера по заявке {rid} ({_candidate_words(doc)})"
        if doc else
        f"перевод заявки {rid} в записи: "
        f"{(rq.get('source') or {}).get('kind') or 'вручную'}")
    draft = draft_doc(section, record_id, record=record, note=note,
                      by=settings.actor, request_id=rid,
                      from_candidate=doc is not None)
    with store.writing():
        store.write_draft(draft)
    words = f"черновик записи по заявке {rid}"
    if doc:
        words += f" из кандидата: {_candidate_words(doc)}"
    if missing:
        words += f"; исполнителей нет: {', '.join(missing)}"
    journal.append(actor=settings.actor, op="request-adopt",
                   target=f"{section}/{record_id}",
                   outcome="warning" if missing else "ok", words=words)
    nxt = ("соберите черновик — заявка перейдёт в «есть» той же записью"
           + ("; сборка удалит файл кандидата — включите удаление в коммит"
              if doc else ""))
    # Слова §5.1 проекта шага 6 (буквально): что импортировано, откуда и
    # что делать дальше — одной строкой для экрана и для доклада.
    said = (f"кандидат записи из CR {doc.get('cr_key')} "
            f"({_candidate_words(doc)}) импортирован в черновики; {nxt}"
            if doc else f"черновик записи по заявке {rid} записан; {nxt}")
    return {"draft_written": True, "draft": draft,
            "executors_missing": missing,
            "candidate": (relative_name(
                candidate_path(settings, section, record_id),
                settings.catalog_dir) if doc else None),
            "words": said,
            "next": nxt}


def _judge_candidate(settings, cat, doc: dict, rid: str) -> None:
    """Суд кандидата правилами читателя — одно вычисление со стендом CR.

    Гейты конвейера и служба судят кандидата ОДНОЙ функцией читателя
    (`validate_with_candidate`): расхождение «в ветке зелено, у владельца
    красно» было бы расхождением двух копий правила.

    Судится то, что ЛЯЖЕТ НА ДИСК, а не промежуток: `adopt` и сборка —
    один переход «принята | исполнитель заявлен → есть», и в конце его
    заявка уже «есть». Если оставить её открытой, R02 честно скажет
    «запись с таким id уже есть: допустима только заявка на вид» — но
    скажет о состоянии, которого не будет ни секунды на диске."""
    try:
        mod = importlib.import_module("template_catalog.candidates")
    except ImportError:                                     # pragma: no cover
        return
    future = copy.deepcopy(cat.requests())
    for one in future:
        if str(one.get("request_id")) == str(rid) \
                and one.get("state") in ("принята", "исполнитель заявлен"):
            one["state"] = "есть"
    problems = mod.validate_with_candidate(
        catalog_with(cat, future), doc,
        forbidden_terms=markers.terms(settings))
    errors = [p for p in problems if getattr(p, "severity", "error") == "error"]
    if errors:
        raise ServiceError(
            "invalid-draft",
            f"кандидат не проходит проверку каталога: ошибок {len(errors)} — "
            "черновик не записан", 422, problems=problems)


def _record_of_candidate(doc: dict, section: str, record_id: str) -> tuple:
    """Запись из кандидата: целиком, со `stand_tasks`, без истории.

    `cr_key` и `evidence` в запись не переносятся: запись каталога — о
    механике, а не о том, каким CR её писали; след остаётся в заметке
    черновика и в журнале действий."""
    try:
        mod = importlib.import_module("template_catalog.candidates")
        record = copy.deepcopy(mod.record_of(doc))
    except ImportError:                                     # pragma: no cover
        record = copy.deepcopy(doc.get("record") or {})
    record["id"] = record_id
    record.setdefault("status", "есть")
    record.pop("history", None)
    kinds_field = "executor_kinds" if section == "families" else "computable_by"
    return record, [str(k) for k in (record.get(kinds_field) or [])]


def _record_of(cat, rq: dict, section: str, record_id: str) -> tuple:
    """Запись черновика для `adopt` без кандидата и виды, которых она
    требует."""
    kinds_field = "executor_kinds" if section == "families" else "computable_by"
    draft = rq.get("draft")
    asked = [str(k) for k in (rq.get("executor_kinds_requested") or [])]
    if isinstance(draft, dict):
        record = copy.deepcopy(draft)
        record["id"] = record_id
        record["status"] = "есть"
        record["stand_tasks"] = []
        record.pop("history", None)
        src = rq.get("source") if isinstance(rq.get("source"), dict) else {}
        old_note = str((record.get("origin") or {}).get("note") or "").strip()
        record["origin"] = {
            "kind": "заявка",
            "date": str(rq.get("submitted_at") or "")[:10],
            "note": old_note or f"заявка {rq.get('request_id')}: "
                                f"{src.get('kind') or 'вручную'}",
            "confirmed_by": identity.ACTORS["owner"]}
        if asked:
            record[kinds_field] = sorted(
                set(record.get(kinds_field) or []) | set(asked))
        return record, [str(k) for k in (record.get(kinds_field) or [])]
    existing = existing_record(cat, section, record_id)
    record = copy.deepcopy(existing)
    record.pop("history", None)
    record[kinds_field] = sorted(set(record.get(kinds_field) or []) | set(asked))
    return record, asked


def existing_record(cat, section: str, record_id: str) -> dict:
    """Запись раздела либо 404 `unknown-record` — ОДИН закон на все ручки
    записи (карточка, заявка, свидетельство поддержки).

    Имя публичное: ручка свидетельства (`app.py`) судит идентичность
    записи тем же вызовом, а не своей копией — иначе один экран отвечал
    бы «такой записи нет», а другой «вычислено, ячеек нет»."""
    from template_catalog.errors import UnknownRecord
    try:
        return cat.family(record_id) if section == "families" \
            else cat.criterion(record_id)
    except UnknownRecord as e:
        raise ServiceError("unknown-record", str(e), 404) from None


def drop_candidate(settings, section: str, record_id: str,
                   request_id: str | None = None) -> str | None:
    """Убрать файл кандидата после сборки — имя убранного либо None.

    Сборка вносит кандидата в записи; оставленный файл краснит
    предпроверку выпуска `candidate-pending`, поэтому его удаление —
    часть той же работы, а не отдельная просьба к владельцу."""
    path = candidate_path(settings, section, record_id)
    if not path.is_file():
        return None
    if request_id is not None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        if isinstance(doc, dict) and doc.get("request_id") \
                and str(doc.get("request_id")) != str(request_id):
            return None
    name = relative_name(path, settings.catalog_dir)
    try:
        path.unlink()
    except OSError:                                         # pragma: no cover
        return None
    return name


def pending_candidates(settings, cat) -> list[dict]:
    """Кандидаты, которых сборка не убрала: предпроверка выпуска 9."""
    out = []
    for rq in cat.requests():
        if rq.get("state") not in ("есть", "исполнитель заявлен"):
            continue
        name, _ = candidate_meta(settings, rq)
        if name:
            out.append({"file": name, "request_id": rq.get("request_id"),
                        "cr_key": rq.get("cr_key"),
                        "record": f"{rq.get('section')}/{rq.get('record_id')}"})
    return out


# ───────────────────────────────────────────────────────── чтение

def view(cat, rq: dict, settings=None) -> dict:
    """Заявка для списка: сама заявка плюс признаки экрана.

    `cr_key_expected` — ключ CR по закону: экран предзаполняет им кнопку
    «Заявить исполнителя», а COSMOS сверяет с ним свою очередь."""
    section = str(rq.get("section"))
    rid = str(rq.get("record_id"))
    known = ({str(r.get("id")) for r in cat.families_full(status=None)}
             if section == "families"
             else set(cat.criteria(status=None)))
    out = {**copy.deepcopy(rq), "record_exists": rid in known,
           "has_draft": isinstance(rq.get("draft"), dict),
           "cr_key_expected": None, "candidate": None,
           "candidate_evidence": None}
    if id_ok(rq.get("request_id")):
        out["cr_key_expected"] = cr_key_of(str(rq.get("request_id")))
    if settings is not None:
        out["candidate"], out["candidate_evidence"] = \
            candidate_meta(settings, rq)
    return out


def listing(cat, *, state=None, submitted_by=None, source_kind=None,
            section=None, record_id=None, cr_key=None, ref=None, q=None,
            settings=None) -> list[dict]:
    out = []
    needle = str(q).strip().lower() if q else ""
    prefix = str(ref).strip() if ref else ""
    for rq in cat.requests():
        if state and str(rq.get("state")) != state:
            continue
        if submitted_by and str(rq.get("submitted_by")) != submitted_by:
            continue
        if source_kind and str((rq.get("source") or {}).get("kind")) != source_kind:
            continue
        if section and str(rq.get("section")) != section:
            continue
        if record_id and str(rq.get("record_id")) != record_id:
            continue
        if cr_key and str(rq.get("cr_key") or "") != str(cr_key):
            continue
        if prefix and not str((rq.get("source") or {}).get("ref") or "") \
                .startswith(prefix):
            continue
        if needle:
            hay = " ".join(str(x) for x in (
                rq.get("request_id"), rq.get("record_id"), rq.get("cr_key"),
                (rq.get("source") or {}).get("ref"),
                (rq.get("decision") or {}).get("words"))).lower()
            if needle not in hay:
                continue
        out.append(view(cat, rq, settings))
    return out


def draft_problems(settings, cat, rid: str) -> list:
    """Проблемы читателя по адресу заявки — для её карточки."""
    return [_p(p) for p in _mine(
        cat.validate(forbidden_terms=markers.terms(settings)), rid)]


def _p(problem) -> dict:
    return {"where": problem.where, "code": problem.code,
            "message": problem.message, "severity": problem.severity}
