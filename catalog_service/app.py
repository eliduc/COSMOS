# -*- coding: utf-8 -*-
"""Приложение службы каталога: ручки §3 контракта и раздача интерфейса.

`create_app(settings)` строит приложение ЯВНО по настройкам — глобального
приложения, читающего среду при импорте, у службы нет: так тест поднимает
службу с любой ролью над любой копией каталога, а `__main__` — ровно одну
службу по ключам запуска.

Читать вправе кто угодно (служба слушает 127.0.0.1); писать — по роли
рабочего места (`identity`). Любой отказ уходит одной формой
`{"detail": {"code", "words", "problems"?, "extra"?}}`; абсолютных путей
машины наружу не бывает.
"""
from __future__ import annotations

import copy
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import template_catalog as tc
from template_catalog import diff as tdiff
from template_catalog import export as texport
from template_catalog import writer
from template_catalog.errors import UnknownRecord
from template_catalog.manifest import parse_version

from . import __version__ as SERVICE_VERSION
from . import assemble as asm
from . import executors, identity, markers, releases
from . import request_queue as rq
from .errors import ServiceError, problem_json
from .journal import Journal
from .settings import REPO, Settings
from .store import (CLOSED_LISTS, DERIVED_KEYS, DICT_SECTION, RECORD_SECTIONS,
                    Store, draft_doc, draft_kind)

DIST_DIR = REPO / "catalog-ui" / "dist"
#: Состояния исполнителя в фильтрах адресов. В адресе пробел заменяется
#: дефисом («не-объявлено»), внутри — слово с пробелом: одно правило на
#: оба экрана, а не два написания одного состояния.
STATE_WORDS = ("есть", "заявлен", "нет", "не-объявлено")
STATE_IN_URL = {"не-объявлено": "не объявлено"}
#: Виды значений ключа словаря на экране.
DICT_KINDS = {"lexicon_order": "order", "acceptance_default": "text"}
#: Ключи словарей, которых в каталоге ещё может не быть, но завести их
#: черновиком служба обязана (шаг 6, решение 0.14): схема читателя знает
#: их как необязательные свойства, а каталог получает их первой сборкой.
#: Перечень закрыт — «любой новый ключ словарём» превратил бы словари в
#: свалку, и D01 судил бы значения по ключу, которого никто не заводил.
OPTIONAL_DICT_KEYS = ("document_classes",)


class _NoCacheIndexStatic(StaticFiles):
    """`index.html` не кэшируется браузером (иначе после пересборки
    пользователь видит старый интерфейс до жёсткой перезагрузки);
    хэшированные ресурсы `assets/` кэшировать можно и нужно."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        if str(getattr(resp, "path", "") or "").endswith("index.html"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Каталог шаблонов", version=SERVICE_VERSION,
                  docs_url=None, redoc_url=None)
    store = Store(settings)
    journal = Journal(settings.journal_path, enabled=settings.is_edit())
    app.state.settings = settings
    app.state.store = store
    app.state.journal = journal

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.detail()}, status_code=exc.status)

    # ─────────────────────────────────────────────── наблюдение
    @app.get("/healthz")
    def healthz():
        """Жив ли процесс. Ничего не читает: наблюдатель не должен быть
        источником нагрузки, а живость — зависеть от диска."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        checks = {"catalog": False, "ui_built": (DIST_DIR / "index.html").is_file(),
                  "work_dir": True}
        out: dict = {"ready": False, "checks": checks, "mode": settings.mode}
        try:
            cat = store.catalog()
        except ServiceError as e:
            out["reason"] = e.words
            return JSONResponse(out, status_code=503)
        problems = cat.validate()
        errors = [p for p in problems if p.severity == "error"]
        checks["catalog"] = not errors
        out["catalog"] = {"version": cat.version(),
                          "fingerprint": cat.fingerprint(),
                          "errors": len(errors),
                          "warnings": len(problems) - len(errors)}
        if settings.is_edit():
            checks["work_dir"] = _work_dir_ready(settings)
        if errors:
            out["reason"] = (f"каталог не проходит проверку: ошибок "
                             f"{len(errors)}")
        elif not checks["work_dir"]:
            out["reason"] = "рабочая папка службы недоступна для записи"
        out["ready"] = all(v for k, v in checks.items() if k != "ui_built")
        return JSONResponse(out, status_code=200 if out["ready"] else 503)

    @app.get("/versionz")
    def versionz():
        out = {"service": "catalog_service",
               "service_version": SERVICE_VERSION,
               "reader_version": tc.__version__,
               "code": _short_head(settings),
               "mode": settings.mode, "actor": settings.actor,
               "catalog": None}
        try:
            cat = store.catalog()
        except ServiceError as e:
            out["catalog_error"] = e.words
            return out
        out["catalog"] = {"version": cat.version(),
                          "fingerprint": cat.fingerprint(),
                          "is_draft": cat.is_draft()}
        return out

    # ───────────────────────────────────────────────── сессия
    @app.get("/api/session")
    def session():
        counts = store.draft_counts()
        out = {"mode": settings.mode, "actor": settings.actor,
               "read_only": not settings.is_edit(),
               "drafts": counts, "requests_open": 0,
               "rights": identity.rights(settings), "catalog": None}
        cat = store.catalog()
        out["catalog"] = {"version": cat.version(),
                          "fingerprint": cat.fingerprint(),
                          "is_draft": cat.is_draft(),
                          "dir_name": Path(settings.catalog_dir).name}
        out["requests_open"] = rq.open_count(cat.requests())
        out["requests_in_work"] = len(rq.in_work(cat.requests()))
        out["machine_channel"] = settings.machine_channel_on()
        # РВ-141 (слово владельца 04.10): номер версии продукта внизу
        # экрана — одна настройка площадки на все компоненты; литерала в
        # коде нет, не задана — null и подписи нет.
        out["product_version"] = (os.environ.get("CREW_PRODUCT_VERSION")
                                  or "").strip() or None
        # Классы документа — ОДИН перечень: словарь каталога, когда он
        # заведён, иначе перечень читателя (шаг 6, решение 0.14). Экран
        # своей копии семи значений не держит: копия разошлась бы.
        out["document_classes"] = list(
            cat.dictionaries().get("document_classes")
            or rq.law().DOCUMENT_CLASSES)
        return out

    @app.get("/api/catalog")
    def catalog_head():
        cat = store.catalog()
        problems = cat.validate(forbidden_terms=markers.terms(settings))
        errors = [p for p in problems if p.severity == "error"]
        counts = store.draft_counts()
        return {**copy.deepcopy(cat.header), "is_draft": cat.is_draft(),
                "counts": {
                    "families": len(cat.families_full(status=None)),
                    "criteria": len(cat.criteria(status=None)),
                    "dictionaries_keys": len(
                        [k for k in cat.dictionaries()
                         if k not in DERIVED_KEYS]),
                    "requests_open": rq.open_count(cat.requests()),
                    "versions": len(cat.versions()),
                    "drafts": sum(counts.values())},
                "problems": {"errors": len(errors),
                             "warnings": len(problems) - len(errors)},
                "releases_ok": releases.releases_ok(cat)}

    @app.get("/api/validate")
    def validate_catalog():
        cat = store.catalog()
        problems = cat.validate(forbidden_terms=markers.terms(settings))
        errors = [p for p in problems if p.severity == "error"]
        return {"problems": [problem_json(p) for p in problems],
                "errors": len(errors),
                "warnings": len(problems) - len(errors),
                "releases": releases.releases_gate(cat)}

    # ───────────────────────────────────────────────── записи
    @app.get("/api/records")
    def records(section: str = Query("families"),
                status: str = Query("есть"), kind: str | None = None,
                zone: str | None = None,
                record_class: str | None = Query(None, alias="class"),
                state: str | None = None, q: str | None = None,
                drafts: int = 0, requests: int = 0):
        cat = store.catalog()
        if section not in RECORD_SECTIONS:
            raise ServiceError("bad-filter",
                               f"раздел {section!r} вне перечня: "
                               f"{', '.join(RECORD_SECTIONS)}", 400)
        _check_token(cat, "статус", status, "record_statuses", extra=("all",))
        _check_token(cat, "вид", kind, "kinds")
        _check_token(cat, "зона", zone, "zones")
        _check_token(cat, "класс", record_class, "classes")
        state = _state_word(state)
        items = _record_rows(store, cat, section, status,
                             include_requests=bool(requests),
                             include_drafts=bool(drafts))
        out = []
        needle = (q or "").strip().lower()
        for row in items:
            if kind and row.get("kind") != kind:
                continue
            if zone and row.get("zone") != zone:
                continue
            if record_class and row.get("class") != record_class:
                continue
            if state and _worst_state(row) != state:
                continue
            if needle and needle not in row["_haystack"]:
                continue
            out.append({k: v for k, v in row.items() if not k.startswith("_")})
        return {"items": out, "total": len(out)}

    @app.get("/api/records/{section}/{record_id}")
    def record(section: str, record_id: str, version: str | None = None,
               fingerprint: str | None = None):
        if section not in RECORD_SECTIONS:
            raise ServiceError("bad-filter",
                               f"раздел {section!r} вне перечня", 400)
        live = store.catalog()
        cat = _catalog_of_version(store, live, version)
        try:
            draft = store.draft(section, record_id)
        except ServiceError:
            # Имя не по образцу id: черновика с таким именем не бывает, а
            # о самой записи скажет правило ниже — 404 с перечнем
            # известных, а не 400 о форме имени.
            draft = None
        try:
            rec = cat.family(record_id) if section == "families" \
                else cat.criterion(record_id)
        except UnknownRecord as e:
            # Черновик НОВОЙ записи: в каталоге её ещё нет, но на рабочем
            # месте она уже заведена — карточка обязана открыться. Иначе
            # поток «Новая запись» умирает после первого же сохранения:
            # отменить такой черновик, посмотреть его «что изменилось» и
            # дойти до него по ссылке списка было бы нечем.
            if draft is None or version:
                raise ServiceError("unknown-record", str(e), 404) from None
            rec = None
        shown = rec if rec is not None \
            else copy.deepcopy(draft.get("record") or {})
        changes = []
        if draft is not None:
            ch = tdiff.diff_record(section,
                                   _no_history(rec) if rec is not None else None,
                                   _no_history(draft.get("record") or {}))
            changes = asm.changes_json([ch]) if ch is not None else []
        where = f"{section}/{record_id}"
        problems = [problem_json(p) for p in
                    cat.validate(forbidden_terms=markers.terms(settings))
                    if p.where == where or p.where.startswith(where + ".")]
        open_rq = rq.open_for(cat.requests(), section, record_id)
        return {"record": shown, "draft": draft, "changes": changes,
                "in_catalog": rec is not None,
                "executors": executors.record_cells(live, settings, section,
                                                    record_id),
                "stand": executors.record_stand(live, settings, section,
                                                record_id),
                "request": rq.view(cat, open_rq, settings) if open_rq else None,
                "problems": problems,
                "shown": {"version": cat.version(),
                          "fingerprint": cat.fingerprint()},
                "pinned_words": _pinned_words(cat, version, fingerprint),
                # Есть ли папка выпуска, на которую закреплён продукт:
                # только тогда ссылка «#/versions/<v>» рядом со строкой
                # расхождения ведёт на что-то читаемое.
                "pinned_release_ok": _release_dir_ok(live, version)}

    # ─────────────────────────────────────────────── черновики
    @app.get("/api/drafts")
    def drafts_list():
        cat = store.catalog()
        items = []
        for d in store.drafts():
            items.append({
                "section": d.get("section"),
                "id": d.get("id"), "key": d.get("key"),
                "kind": draft_kind(d, cat), "saved_at": d.get("saved_at"),
                "by": d.get("by"), "note": d.get("note"),
                "request_id": d.get("request_id"),
                "changes": _draft_changes(cat, d)})
        suggested = None
        stop = None
        if items:
            cand, _p, _m = asm.preview(store, cat=cat)
            suggested = asm.suggest_assemble(cat, cand)
            # Предупреждение о стопе сторожа — ДО кнопки (§4.5 проекта):
            # экран не умеет считать B01 сам, а узнать об остановке двери
            # продукта из отказа 412 после нажатия — узнать поздно.
            refusal = executors.bindings_stop(cat, settings,
                                              str(suggested["version"]))
            if refusal is not None:
                stop = {"code": refusal.code, "words": refusal.words,
                        "extra": refusal.extra}
        return {"items": items, "suggested": suggested, "bindings_stop": stop}

    @app.put("/api/drafts/dictionaries/{key}")
    def save_dictionary_draft(key: str, body: dict):
        identity.require(settings, "draft-save")
        if key in CLOSED_LISTS:
            raise ServiceError(
                "closed-list",
                f"служебный перечень {key}: изменение — правка кода "
                f"читателя, а не данных", 422)
        if key in DERIVED_KEYS:
            raise ServiceError(
                "closed-list",
                f"{key} вычисляется читателем и в файле словарей не "
                f"хранится", 422)
        cat = store.catalog()
        if key not in cat.dictionaries() and key not in OPTIONAL_DICT_KEYS:
            raise ServiceError("unknown-key",
                               f"ключа словарей {key!r} нет; есть: "
                               + ", ".join(sorted(
                                   k for k in cat.dictionaries()))
                               + "; завести черновиком можно только "
                               + ", ".join(OPTIONAL_DICT_KEYS), 404)
        if "value" not in (body or {}):
            raise ServiceError("bad-form", "тело правки: value и note", 400)
        note = str((body or {}).get("note") or "").strip()
        if not note:
            raise ServiceError("bad-form",
                               "заметка правки обязательна: что и зачем "
                               "меняется", 400)
        doc = draft_doc(DICT_SECTION, key, value=body.get("value"),
                        note=note, by=settings.actor)
        return _save_draft(store, journal, cat, doc, target=f"dictionaries/{key}")

    @app.put("/api/drafts/{section}/{record_id}")
    def save_record_draft(section: str, record_id: str, body: dict):
        identity.require(settings, "draft-save")
        if section not in RECORD_SECTIONS:
            raise ServiceError("bad-form",
                               f"раздел {section!r} вне перечня", 400)
        record = (body or {}).get("record")
        if not isinstance(record, dict):
            raise ServiceError("bad-form",
                               "тело правки: record (запись целиком) и note",
                               400)
        note = str((body or {}).get("note") or "").strip()
        if not note:
            raise ServiceError("bad-form",
                               "заметка правки обязательна: что и зачем "
                               "меняется", 400)
        cat = store.catalog()
        old = _existing(cat, section, record_id)
        if old is not None and record.get("status") != old.get("status") \
                and not identity.can(settings, "draft-status"):
            raise identity.refuse(settings, "draft-status")
        if old is None and record.get("status") == "снята" \
                and not identity.can(settings, "draft-status"):
            raise identity.refuse(settings, "draft-status")
        open_rq = rq.open_for(cat.requests(), section, record_id)
        if open_rq is not None and (body or {}).get("request_id") is None \
                and store.draft(section, record_id) is None:
            raise ServiceError(
                "id-taken-by-request",
                f"на id {record_id} есть открытая заявка "
                f"{open_rq.get('request_id')} — правьте её черновик "
                f"(«перевести в записи»)", 409)
        prev = store.draft(section, record_id) or {}
        kept = prev.get("request_id")
        doc = draft_doc(section, record_id, record=record, note=note,
                        by=settings.actor,
                        request_id=(body or {}).get("request_id") or kept,
                        # Правка черновика происхождения не меняет: тело
                        # пришло из кандидата, и сборке по-прежнему
                        # убирать его файл.
                        from_candidate=bool(prev.get("from_candidate")))
        return _save_draft(store, journal, cat, doc,
                           target=f"{section}/{record_id}")

    @app.delete("/api/drafts/dictionaries/{key}")
    def discard_dictionary_draft(key: str):
        identity.require(settings, "draft-discard")
        if not store.delete_draft(DICT_SECTION, key):
            raise ServiceError("no-draft",
                               f"черновика ключа {key} нет", 404)
        journal.append(actor=settings.actor, op="draft-discard",
                       target=f"dictionaries/{key}", outcome="ok",
                       words="черновик отменён")
        return {"discarded": True}

    @app.delete("/api/drafts/{section}/{record_id}")
    def discard_record_draft(section: str, record_id: str):
        identity.require(settings, "draft-discard")
        if not store.delete_draft(section, record_id):
            raise ServiceError("no-draft",
                               f"черновика {section}/{record_id} нет", 404)
        journal.append(actor=settings.actor, op="draft-discard",
                       target=f"{section}/{record_id}", outcome="ok",
                       words="черновик отменён")
        return {"discarded": True}

    # ────────────────────────────────────────────────── словари
    @app.get("/api/dictionaries")
    def dictionaries():
        cat = store.catalog()
        dicts = cat.dictionaries()
        drafts = {str(d.get("key")) for d in store.drafts()
                  if d.get("section") == DICT_SECTION}
        keys = []
        for key in sorted(k for k in dicts if k not in DERIVED_KEYS):
            value = dicts[key]
            keys.append({"key": key, "kind": _dict_kind(key, value),
                         "size": _dict_size(value),
                         "closed": key in CLOSED_LISTS,
                         "draft": key in drafts,
                         "usages": len(_usages(cat, value))})
        return {"keys": keys, "derived": list(DERIVED_KEYS)}

    @app.get("/api/dictionaries/{key}")
    def dictionary(key: str):
        cat = store.catalog()
        dicts = cat.dictionaries()
        if key not in dicts:
            raise ServiceError("unknown-key",
                               f"ключа словарей {key!r} нет", 404)
        draft = store.draft(DICT_SECTION, key)
        journal_rows = journal.for_key(key)
        for v in cat.versions():
            for ch in v.get("changes") or []:
                if ch.get("section") == "dictionaries" and ch.get("id") == key:
                    journal_rows.append({"version": v.get("version"),
                                         "kind": ch.get("kind"),
                                         "note": ch.get("note"),
                                         "at": v.get("released_at")})
        return {"key": key, "kind": _dict_kind(key, dicts[key]),
                "value": dicts[key], "closed": key in CLOSED_LISTS,
                "derived": key in DERIVED_KEYS,
                "draft": draft, "usages": _usages(cat, dicts[key]),
                "journal": journal_rows}

    # ─────────────────────────────────────────────────── заявки
    def _product_of(request: Request) -> str | None:
        """Кто зовёт: продукт машинного канала или экран.

        Признак канала — ЗАГОЛОВКИ, а не тело: тело у экрана и у продукта
        разной формы, и решать по нему значило бы угадывать. Ни одного
        заголовка — экран (роль рабочего места)."""
        head = request.headers.get(identity.PRODUCT_HEADER)
        token = request.headers.get(identity.TOKEN_HEADER)
        if head is None and token is None:
            return None
        return identity.machine_channel(
            settings, head, token,
            store.catalog().dictionaries().get("products") or ())

    @app.get("/api/requests")
    def requests_list(state: str | None = None,
                      submitted_by: str | None = None,
                      source_kind: str | None = None,
                      section: str | None = None,
                      record_id: str | None = None,
                      cr_key: str | None = None, ref: str | None = None,
                      q: str | None = None):
        cat = store.catalog()
        _check_token(cat, "состояние заявки", state, "request_states")
        _check_token(cat, "кто подал", submitted_by, "request_submitters")
        _check_token(cat, "происхождение", source_kind, "request_source_kinds")
        items = rq.listing(cat, state=state, submitted_by=submitted_by,
                           source_kind=source_kind, section=section,
                           record_id=record_id, cr_key=cr_key, ref=ref, q=q,
                           settings=settings)
        return {"items": items, "total": len(items)}

    @app.post("/api/requests/dry-run")
    def dry_run_request(body: dict, request: Request):
        """Предпросмотр отказа до подачи: ни записи, ни замка, ни журнала."""
        return rq.dry_run(store, body, product=_product_of(request))

    @app.get("/api/requests/{request_id}")
    def request_card(request_id: str):
        cat = store.catalog()
        one = rq.find(cat.requests(), request_id)
        record = None
        section = str(one.get("section"))
        rid = str(one.get("record_id"))
        try:
            record = cat.family(rid) if section == "families" \
                else cat.criterion(rid)
        except UnknownRecord:
            record = None
        return {**rq.view(cat, one, settings), "record": record,
                "draft_problems": rq.draft_problems(settings, cat, request_id),
                "has_service_draft": store.draft(section, rid) is not None}

    @app.post("/api/requests", status_code=201)
    def submit_request(body: dict, request: Request):
        out, status = rq.submit(store, journal, body,
                                product=_product_of(request))
        return JSONResponse(out, status_code=status)

    @app.post("/api/requests/{request_id}/decide")
    def decide_request(request_id: str, body: dict):
        return rq.decide(store, journal, request_id, body)

    @app.put("/api/requests/{request_id}/cr_key")
    def cr_key(request_id: str, body: dict, request: Request):
        """Ключ CR пишет владелец с экрана либо COSMOS машинно при сверке.

        Право одно и то же (`request-cr-key`, владелец): машинный канал
        не расширяет прав, он лишь называет вызывающего в журнале."""
        return rq.set_cr_key(store, journal, request_id, body,
                             by=_product_of(request))

    @app.post("/api/requests/{request_id}/adopt")
    def adopt_request(request_id: str, body: dict | None = None):
        return rq.adopt(store, journal, request_id, body or {})

    # ──────────────────────────── свидетельство поддержки (K06)
    @app.post("/api/support/refresh")
    def refresh_support(body: dict | None = None):
        """Пересчитать свидетельство поддержки и положить файлом.

        Носитель один — `catalog/support.json`, и пишет его только
        служба: между выпусками `approved` у ячеек пуст («не проверено»
        в терминах готовности), заполняет его выпуск."""
        doc = executors.refresh_support(store, journal)
        return {"catalog": doc["catalog"], "computed_at": doc["computed_at"],
                "computed_by": doc["computed_by"],
                "schema_version": doc["schema_version"],
                "cells": len(doc["cells"]),
                "totals": executors.support_counts(doc),
                "file": executors.SUPPORT_FILE}

    @app.get("/api/records/{section}/{record_id}/support")
    def record_support(section: str, record_id: str):
        """Свидетельство поддержки записи — ПРЕДСТАВЛЕНИЕ файла.

        Второго носителя у свидетельства нет: ручка показывает ячейки,
        записанные `POST /api/support/refresh` (или выпуском), и ничего
        не пересчитывает — иначе экран и файл разошлись бы ровно в тот
        день, когда привязки поменялись, а свидетельство ещё нет.

        Запись судится, как у остальных ручек записи: чужой id — 404
        `unknown-record`, а не «вычислено, ячеек нет». Иначе опечатка в
        имени молча давала бы пустую поддержку — тот же fail-open, от
        которого закрывались K06."""
        if section not in RECORD_SECTIONS:
            raise ServiceError("bad-filter",
                               f"раздел {section!r} вне перечня", 400)
        cat = store.catalog()
        rq.existing_record(cat, section, record_id)
        return executors.support_view(cat, section, record_id)

    # ────────────────────────────────────────────── исполнители
    @app.get("/api/executors")
    def executors_table(product: str = Query("all"),
                        section: str | None = None,
                        record_id: str | None = Query(None, alias="id"),
                        kind: str | None = None,
                        state: str | None = None):
        cat = store.catalog()
        if product not in ("all",) + executors.PRODUCTS:
            raise ServiceError("bad-filter",
                               f"продукт {product!r} вне перечня: all, "
                               + ", ".join(executors.PRODUCTS), 400)
        if section is not None and section not in RECORD_SECTIONS:
            raise ServiceError("bad-filter",
                               f"раздел {section!r} вне перечня", 400)
        state = _state_word(state)
        return executors.table(cat, settings, product=product,
                               section=section, record_id=record_id,
                               kind=kind, state=state)

    # ──────────────────────────────────────── версии и выпуски
    @app.get("/api/versions")
    def versions():
        cat = store.catalog()
        gate = {r["version"]: r for r in releases.releases_gate(cat)}
        rows = []
        for v in cat.versions():
            row = dict(v)
            g = gate.get(str(v.get("version")), {})
            folder = Path(cat.path) / "releases" / str(v.get("version"))
            row["release_dir_ok"] = folder.is_dir()
            row["release_fingerprint_ok"] = bool(g.get("ok"))
            row["release_words"] = g.get("words")
            rows.append(row)
        counts = store.draft_counts()
        return {"current": {**copy.deepcopy(cat.header),
                            "is_draft": cat.is_draft()},
                "versions": rows, "suggested": asm.suggest_release(cat),
                "drafts": sum(counts.values())}

    @app.get("/api/versions/compare")
    def compare(a: str = Query("current"), b: str = Query("drafts")):
        cat = store.catalog()
        body_a, words_a = _side(store, cat, a)
        body_b, words_b = _side(store, cat, b)
        changes = tdiff.diff_bodies(body_a, body_b)
        return {"a": words_a, "b": words_b,
                "changes": asm.changes_json(changes),
                "level": tdiff.suggest_level(changes)}

    @app.get("/api/versions/release/checks")
    def release_checks(version: str | None = None):
        """Восемь предпроверок выпуска словами, без единого байта на
        диск: диалог «Выпустить версию» обязан показать чек-лист ДО
        нажатия (§4.5 проекта), а маркеры, чистоту дерева git и остановку
        сторожа знает только служба."""
        return releases.checks(store, version)

    @app.get("/api/versions/{version}")
    def version_card(version: str):
        cat = store.catalog()
        _check_version_form(cat, version)
        entry = next((v for v in cat.versions()
                      if str(v.get("version")) == version), None)
        if version == cat.version():
            # Текущая версия — тоже адрес карточки: до первого выпуска
            # ссылка паспорта CDPM ведёт именно сюда, и отказ «версии
            # 0.2.0-draft.1 нет: текущая 0.2.0-draft.1» был бы неправдой
            # о самом себе. Правило то же, что у `?version=` записи.
            base_body, _base = asm.base_of(cat)
            changes = (entry or {}).get("changes") or asm.changes_json(
                tdiff.diff_bodies(base_body, cat.body()))
            return {"version": version, "entry": entry, "is_current": True,
                    "header": {**copy.deepcopy(cat.header),
                               "is_draft": cat.is_draft()},
                    "changes": changes, "loadable": True}
        folder = Path(cat.path) / "releases" / version
        loadable = False
        header = None
        if folder.is_dir():
            try:
                snap = tc.load(folder)
                header, loadable = copy.deepcopy(snap.header), True
                header["is_draft"] = snap.is_draft()
            except tc.CatalogError:
                loadable = False
        if entry is None and header is None:
            raise ServiceError("unknown-version", _no_version_words(cat,
                                                                    version),
                               404)
        return {"version": version, "entry": entry, "is_current": False,
                "header": header,
                "changes": (entry or {}).get("changes") or [],
                "loadable": loadable}

    @app.post("/api/versions/assemble")
    def assemble_drafts(body: dict | None = None):
        body = body or {}
        # Кандидаты собираемых черновиков запоминаются ДО сборки: она
        # черновики стирает, и после неё уже не сказать, чей файл в
        # `catalog/candidates/` только что стал записью. Удаляется файл
        # ТОЛЬКО у черновика, рождённого из кандидата (`from_candidate`):
        # перевод заявки в записи БЕЗ кандидата чужой файл не трогает —
        # иначе сборка сносила бы содержимое, которого в записи нет, а
        # предпроверка выпуска 9 об этом молчала бы (ревью волны 2).
        used = [(str(d.get("section")), str(d.get("id")),
                 str(d.get("request_id") or "") or None)
                for d in store.drafts()
                if str(d.get("section")) in RECORD_SECTIONS
                and d.get("request_id") and d.get("from_candidate")]
        out = asm.assemble(store, journal, note=body.get("note"),
                           version=body.get("version"),
                           confirm_stop=bool(body.get("confirm_stop")))
        dropped = [name for name in
                   (rq.drop_candidate(settings, section, rid, request_id)
                    for section, rid, request_id in used) if name]
        if dropped:
            out["next"] = list(out.get("next") or []) + [
                f"удалён {', '.join(dropped)}: включите удаление в коммит "
                f"сборки, иначе выпуск даст 412 git-dirty"]
            out["candidates_removed"] = dropped
            journal.append(actor=settings.actor, op="assemble",
                           target="catalog", outcome="ok",
                           words=f"кандидаты убраны: {', '.join(dropped)}")
        return out

    @app.post("/api/versions/release")
    def release_version(body: dict | None = None):
        body = body or {}
        # `confirm_parity` — осознанное продолжение владельца при
        # двенадцатой предпроверке «паритет с кодом COSMOS» (пакет
        # «паритет на временном каталоге», 0.13): судья рядом с
        # каталогом не найден, не уложился в предел либо предупредил
        # пропуском. Без этой строки отказ `parity-unconfirmed` по HTTP
        # снять было бы нечем, и на машине без `catalog/tools/parity.py`
        # кнопка выпуска встала бы намертво. Красный паритет
        # (`parity-red`) подтверждением НЕ снимается — это решает
        # `releases._check_parity`, а не ручка.
        return releases.release(store, journal, version=body.get("version"),
                                note=body.get("note"),
                                confirm_stop=bool(body.get("confirm_stop")),
                                confirm_parity=bool(
                                    body.get("confirm_parity")))

    @app.delete("/api/drafts")
    def discard_all():
        identity.require(settings, "draft-discard")
        n = store.clear_drafts()
        journal.append(actor=settings.actor, op="draft-discard",
                       target="catalog", outcome="ok",
                       words=f"отменены все черновики: {n}")
        return {"discarded": n}

    # ─────────────────────────────────── выгрузка и загрузка
    @app.get("/api/export")
    def export_single():
        cat = store.catalog()
        name = (f"template-catalog-{cat.version()}-"
                f"{cat.fingerprint()[:12]}.json")
        return Response(content=texport.export_single(cat),
                        media_type="application/json",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{name}"'})

    @app.post("/api/import")
    def import_single(file: UploadFile = File(...), force: int = 0):
        identity.require(settings, "import")
        return _import(store, journal, file, force=bool(force))

    @app.get("/api/journal")
    def journal_rows(limit: int = 200):
        if not settings.is_edit():
            return {"items": [],
                    "words": "журнал ведётся только на рабочем месте"}
        return {"items": journal.read(limit), "words": None}

    # ────────────────────────────────────────────── интерфейс
    if (DIST_DIR / "index.html").is_file():
        app.mount("/ui", _NoCacheIndexStatic(directory=str(DIST_DIR),
                                             html=True), name="ui")

    @app.get("/")
    def root():
        if (DIST_DIR / "index.html").is_file():
            return RedirectResponse("/ui/", status_code=302)
        raise ServiceError(
            "ui-not-built",
            "интерфейс не собран: catalog-ui/dist/index.html нет — "
            "npm run build в catalog-ui/; служба отвечает по /api", 503)

    return app


# ─────────────────────────────────────────────────────── помощники

def _work_dir_ready(settings) -> bool:
    try:
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        return settings.work_dir.is_dir()
    except OSError:
        return False


def _short_head(settings) -> str | None:
    try:
        res = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(releases.repo_of(settings.catalog_dir)),
                             capture_output=True, text=True, encoding="utf-8",
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (res.stdout or "").strip()
    return out if res.returncode == 0 and out else None


def _check_token(cat, what: str, value, key: str, extra=()) -> None:
    if value is None:
        return
    known = [str(x) for x in (cat.dictionaries().get(key) or [])] + list(extra)
    if str(value) not in known:
        raise ServiceError("bad-filter",
                           f"{what} {value!r} вне словаря {key}: "
                           + ", ".join(known), 400)


def _state_word(state: str | None) -> str | None:
    """Состояние исполнителя из адреса словом каталога; чужое значение —
    отказ с перечнем."""
    if state is None:
        return None
    if state in STATE_IN_URL:
        return STATE_IN_URL[state]
    if state in ("есть", "заявлен", "нет", "не объявлено"):
        return state
    raise ServiceError("bad-filter",
                       f"состояние исполнителя {state!r} вне перечня: "
                       + ", ".join(STATE_WORDS), 400)


def _existing(cat, section: str, record_id: str):
    try:
        return cat.family(record_id) if section == "families" \
            else cat.criterion(record_id)
    except UnknownRecord:
        return None


def _no_history(rec: dict) -> dict:
    return {k: v for k, v in (rec or {}).items() if k != "history"}


def _worst_state(row: dict) -> str:
    """ХУДШЕЕ состояние исполнителей записи — по нему фильтрует список:
    запись, у которой хоть один вид не исполнен, ищут именно по этому
    виду, а не по лучшему."""
    summary = row.get("executor_summary") or {}
    for key in ("не объявлено", "нет", "заявлен", "есть"):
        if summary.get(key):
            return key
    return "не объявлено"


def _record_rows(store, cat, section: str, status: str, *,
                 include_requests: bool, include_drafts: bool) -> list[dict]:
    wanted = None if status == "all" else (status,)
    if section == "families":
        records = cat.families_full(status=wanted)
        kinds_field = "executor_kinds"
    else:
        records = list(cat.criteria(status=wanted).values())
        kinds_field = "computable_by"
    if include_requests:
        records = records + _request_records(cat, section)
    drafts = {str(d.get("id")): d for d in store.drafts()
              if d.get("section") == section}
    if include_drafts:
        known = {str(r.get("id")) for r in records}
        for rid, d in drafts.items():
            if rid not in known:
                rec = dict(d.get("record") or {})
                rec.setdefault("id", rid)
                records = records + [rec]
    best = executors.record_states(cat, store.settings)
    open_requests = {str(r.get("record_id")): r for r in cat.requests()
                     if r.get("state") in rq.OPEN_STATES
                     and str(r.get("section")) == section}
    out = []
    for rec in records:
        rid = str(rec.get("id"))
        summary = {"есть": 0, "заявлен": 0, "нет": 0, "не объявлено": 0}
        for kind in rec.get(kinds_field) or []:
            by = best.get(section, {}).get(rid, {}).get(str(kind), {})
            word = executors.best_word(by.values())
            summary[word if word in summary else "не объявлено"] += 1
        d = drafts.get(rid)
        row = {
            "section": section, "id": rid, "title": rec.get("title"),
            "class": rec.get("class"), "kind": rec.get("kind"),
            "zone": rec.get("zone"), "nature": rec.get("nature"),
            "violation_unit": rec.get("violation_unit"),
            "status": rec.get("status"),
            "params": len(rec.get("params") or []),
            "stand_tasks": len(rec.get("stand_tasks") or []),
            "executor_summary": summary,
            "draft": draft_kind(d, cat) if d is not None else None,
            "request_id": rec.get("request_id")
            or (open_requests.get(rid) or {}).get("request_id"),
            "_haystack": _haystack(rec)}
        out.append(row)
    return out


def _request_records(cat, section: str) -> list[dict]:
    """Черновики открытых заявок как записи «заявлен». У читателя
    `families_full(include_requests=True)` это умеет, у `criteria()`
    такого параметра нет (`reader.py:368–371`) — служба подмешивает
    критерии сама, тем же законом."""
    if section == "families":
        full = cat.families_full(status=None, include_requests=True)
        known = {str(r.get("id")) for r in cat.families_full(status=None)}
        return [r for r in full if r.get("request_id")
                and str(r.get("id")) not in known]
    out = []
    known = set(cat.criteria(status=None))
    for r in cat.requests():
        if str(r.get("section")) != "criteria":
            continue
        if r.get("state") not in ("подана", "принята", "исполнитель заявлен"):
            continue
        draft = r.get("draft")
        if not isinstance(draft, dict) or str(draft.get("id")) in known:
            continue
        rec = copy.deepcopy(draft)
        rec.update({"status": "заявлен", "history": [],
                    "request_id": r.get("request_id")})
        rec.setdefault("stand_tasks", [])
        out.append(rec)
    return out


def _haystack(rec: dict) -> str:
    parts = [str(rec.get("id")), str(rec.get("title") or ""),
             str(rec.get("subject") or "")]
    parts += [str(w) for w in (rec.get("subject_words") or [])]
    parts += [str(p.get("name")) for p in (rec.get("params") or [])
              if isinstance(p, dict)]
    return " ".join(parts).lower()


def _draft_changes(cat, draft: dict) -> list[dict]:
    section = str(draft.get("section"))
    if section == DICT_SECTION:
        key = str(draft.get("key"))
        changes = tdiff.diff_bodies(
            _dict_body(key, cat.dictionaries().get(key)),
            _dict_body(key, draft.get("value")))
        return asm.changes_json(changes)
    old = _existing(cat, section, str(draft.get("id")))
    ch = tdiff.diff_record(section, _no_history(old) if old else None,
                           _no_history(draft.get("record") or {}))
    return asm.changes_json([ch]) if ch is not None else []


def _dict_body(key: str, value) -> dict:
    """Тело канона с одним ключом словарей — чтобы «что изменилось» у
    ключа считал тот же публичный дифф, что и у всего каталога."""
    return {"families": [], "criteria": [], "dictionaries": {key: value}}


def _save_draft(store, journal, cat, doc: dict, *, target: str) -> dict:
    """Сохранение черновика: кандидат судится ЦЕЛИКОМ, отказ — только по
    НОВЫМ проблемам (адрес может указывать на другую запись — X01
    сообщает асимметрию дополнения по адресу второй стороны)."""
    settings = store.settings
    section = str(doc.get("section"))
    key = str(doc.get("id") if section in RECORD_SECTIONS else doc.get("key"))
    others = [d for d in store.drafts()
              if not (str(d.get("section")) == section
                      and str(d.get("id") or d.get("key")) == key)]
    before = cat.validate(forbidden_terms=markers.terms(settings))
    cand, after, has_markers = asm.preview(store, cat=cat,
                                           drafts=others + [doc])
    fresh = asm.new_problems(before, after)
    errors = [p for p in fresh if p.severity == "error"]
    if errors:
        journal.append(actor=settings.actor, op="draft-save", target=target,
                       outcome="refused",
                       words=f"новых ошибок {len(errors)}")
        raise ServiceError(
            "invalid-draft",
            "черновик даёт новые ошибки каталога: "
            + "; ".join(f"{p.where}: {p.message}" for p in errors[:3])
            + (f" (и ещё {len(errors) - 3})" if len(errors) > 3 else ""),
            422, problems=[problem_json(p) for p in fresh])
    with store.writing():
        store.write_draft(doc)
    problems = [problem_json(p) for p in fresh]
    if not has_markers:
        problems.append({"where": "catalog", "code": "markers-missing",
                         "message": markers.missing_words(settings),
                         "severity": "warning"})
    journal.append(actor=settings.actor, op="draft-save", target=target,
                   outcome="warning" if problems else "ok",
                   words=str(doc.get("note")))
    return {"saved": True, "draft": doc,
            "changes": _draft_changes(cat, doc),
            "derived": list(cand.derived), "problems": problems}


def _catalog_of_version(store, live, version: str | None):
    """Экземпляр показанной версии: текущая папка либо `releases/<v>`.

    Путь строит СЛУЖБА: `reader.load("<версия>")` ищет выпуски только под
    папкой каталога по умолчанию, а служба работает над той папкой, что
    ей задали."""
    if not version or version == live.version():
        return live
    _check_version_form(live, version)
    folder = Path(live.path) / "releases" / version
    if not folder.is_dir():
        raise ServiceError("unknown-version", _no_version_words(live, version),
                           404)
    try:
        return tc.load(folder)
    except tc.CatalogError as e:
        raise ServiceError("unknown-version",
                           f"папка releases/{version} не читается: "
                           f"{type(e).__name__}", 404) from None


def _no_version_words(cat, version: str) -> str:
    return (f"версии {version} нет: текущая {cat.version()}, папки "
            f"releases/{version} нет")


def _check_version_form(cat, version: str) -> None:
    """Номер версии в адресе судится образцом semver ДО построения пути.

    Иначе строка пользователя подставлялась бы в `releases/<строка>` как
    есть: `?version=..` открыл бы саму папку каталога «версией», а любой
    относительный путь пробовался бы как папка выпуска. Отказ — тот же
    `unknown-version`, только слова называют причину."""
    try:
        parse_version(str(version))
    except ValueError:
        raise ServiceError(
            "unknown-version",
            f"версии {version!r} нет: это не номер вида X.Y.Z[-draft.N]; "
            f"текущая {cat.version()}", 404) from None


def _release_dir_ok(cat, version: str | None) -> bool:
    """Откроется ли карточка версии `#/versions/<v>`: это текущая версия
    каталога либо есть папка выпуска `releases/<v>`."""
    if not version:
        return False
    if str(version) == cat.version():
        return True
    try:
        parse_version(str(version))
    except ValueError:
        return False
    return (Path(cat.path) / "releases" / str(version)).is_dir()


def _pinned_words(cat, version: str | None, fingerprint: str | None):
    if not fingerprint or fingerprint == cat.fingerprint():
        return None
    return (f"продукт закреплён на {version or 'другой версии'} · "
            f"{str(fingerprint)[:12]}, показана {cat.version()} · "
            f"{cat.fingerprint()[:12]}")


def _side(store, cat, name: str) -> tuple:
    """Сторона сравнения: `current`, `drafts` либо номер версии."""
    if name == "current":
        return cat.body(), {"version": cat.version(),
                            "fingerprint": cat.fingerprint()}
    if name == "drafts":
        cand, _p, _m = asm.preview(store, cat=cat)
        return cand.body(), {"version": "черновики службы",
                             "fingerprint": cand.fingerprint()}
    snap = _catalog_of_version(store, cat, name)
    return snap.body(), {"version": snap.version(),
                         "fingerprint": snap.fingerprint()}


def _dict_kind(key: str, value) -> str:
    if key in DICT_KINDS:
        return DICT_KINDS[key]
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "text"


def _dict_size(value) -> int:
    return len(value) if isinstance(value, (list, dict, str)) else 0


def _usages(cat, value) -> list[dict]:
    """Где значения ключа встречаются в записях каталога. Общее правило,
    а не таблица «ключ → поле»: таблица разошлась бы со схемой при первом
    новом поле, а обход записей верен всегда."""
    if isinstance(value, dict):
        wanted = {str(k) for k in value}
    elif isinstance(value, list):
        wanted = {str(v) for v in value if isinstance(v, str)}
    else:
        return []
    if not wanted:
        return []
    out: list[dict] = []
    plan = [("families", r) for r in cat.families_full(status=None)]
    plan += [("criteria", r) for r in cat.criteria(status=None).values()]
    for section, rec in plan:
        rid = str(rec.get("id"))
        for where in _walk(rec, ""):
            path, leaf = where
            if leaf in wanted:
                out.append({"section": section, "id": rid,
                            "where": path or "id", "value": leaf})
    return out


def _walk(value, path: str):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield (path, value)


def _import(store, journal, file, *, force: bool) -> dict:
    settings = store.settings
    counts = store.draft_counts()
    if sum(counts.values()):
        raise ServiceError(
            "drafts-pending",
            f"сначала соберите или отмените черновики: записей "
            f"{counts['families'] + counts['criteria']}, словарей "
            f"{counts['dictionaries']}", 409)
    raw = file.file.read()
    with tempfile.TemporaryDirectory(prefix="catalog-import-") as tmp:
        path = Path(tmp) / "single.json"
        path.write_bytes(raw)
        try:
            loaded = tc.load(path)
        except tc.CatalogError as e:
            raise ServiceError("invalid-single-file",
                               f"единый файл выгрузки не читается: {e}"
                               .replace(str(path), path.name), 422) from None
        terms = markers.terms(settings)
        problems = loaded.validate(forbidden_terms=terms)
        errors = [p for p in problems if p.severity == "error"]
        if any(p.code == "H02" for p in errors):
            raise ServiceError(
                "fingerprint-mismatch",
                "отпечаток шапки файла не равен вычисленному — файл испорчен "
                "или правлен мимо инструмента", 422,
                problems=[problem_json(p) for p in problems])
        if errors:
            raise ServiceError(
                "invalid-catalog",
                f"загруженный каталог не проходит проверку: ошибок "
                f"{len(errors)}", 422,
                problems=[problem_json(p) for p in problems])
        with store.writing():
            cat = store.catalog()
            if not force and _older(loaded.version(), cat.version()):
                raise ServiceError(
                    "version-downgrade",
                    f"загрузка понизила бы версию с {cat.version()} до "
                    f"{loaded.version()} — повторите с ?force=1", 409)
            body = loaded.body()
            present = [str(r.get("id")) for r in body["families"]
                       if r.get("status") == "есть"]
            order = writer.refresh_families_order(store.families_order(cat),
                                                  present)
            writer.write_catalog(
                settings.catalog_dir, header=copy.deepcopy(loaded.header),
                families=body["families"], criteria=body["criteria"],
                dictionaries=body["dictionaries"],
                requests=loaded.requests(), versions=loaded.versions(),
                families_order=order, forbidden_terms=terms)
            store.forget()
    fresh = store.catalog()
    warnings = [f"в журнале версий {v.get('version')}, папки "
                f"releases/{v.get('version')} нет"
                for v in fresh.versions()
                if not (Path(fresh.path) / "releases"
                        / str(v.get("version"))).is_dir()]
    journal.append(actor=settings.actor, op="import", target="catalog",
                   outcome="warning" if warnings else "ok",
                   words=f"загружен каталог {fresh.version()}")
    return {"replaced": True, "version": fresh.version(),
            "fingerprint": fresh.fingerprint(), "warnings": warnings}


def _older(a: str, b: str) -> bool:
    ta, na = parse_version(a)
    tb, nb = parse_version(b)
    return (ta, na or 0) < (tb, nb or 0)
