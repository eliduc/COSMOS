"""K06 request/adopt on a copy of a genuinely released TEST catalogue.

The retained source is authenticated and protected by the existing A08
fixture. Only local request_queue entrypoints run: no HTTP, socket, process,
service start, build or release. TEST owner confirmation authorizes a draft,
not executor support or execution. Existing support/bindings are never forged.
The later releases._check_executors law remains a separate, uninvoked gate.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from catalog_service import executors, request_queue
from catalog_service.errors import ServiceError
from catalog_service.journal import Journal
from catalog_service.settings import Settings
from catalog_service.store import Store
from template_catalog import bindings as tb
from test_a08_actual_release_sc3_gpt import released, _census, _dump, _sha


RECORD = "proba_k06_actual_adopt"
KIND = "builder_method"
HEALTHY = "duty_limits"


@pytest.fixture
def local_service(released, tmp_path, record_property):
    """Independent writable copy; the original release remains read-only."""
    root = Path(os.environ["A08_RELEASE_INPUT_ROOT"]).resolve(strict=True)
    folder = tmp_path / "adopt-catalog"
    shutil.copytree(root / "postrelease-working/catalog", folder)
    paths = {}
    for product, name in (("COSMOS", "cosmos-bindings.json"),
                          ("CDPM", "cdpm-bindings.json")):
        destination = tmp_path / name
        shutil.copy2(root / "repo" / name, destination)
        paths[product] = destination
    settings = Settings(catalog_dir=folder, work_dir=tmp_path / "adopt-work",
                        actor="owner", cosmos_bindings=paths["COSMOS"],
                        cdpm_bindings=paths["CDPM"],
                        markers_file=tmp_path / "absent-test-markers.json")
    store = Store(settings)
    journal = Journal(settings.journal_path)
    cat = store.catalog()
    assert cat.version() == released.cat.version() == "0.7.0"
    assert cat.fingerprint() == released.cat.fingerprint()
    before = _census(folder)
    binding_hashes = {product: _sha(path) for product, path in paths.items()}
    original_support = (folder / "support.json").read_bytes()
    judged = tb.support_of(cat, cat.support())
    assert judged["valid"], judged
    matching = [cell for cell in judged["cells"]
                if (cell["section"], cell["record_id"], cell["kind"])
                == ("families", HEALTHY, KIND)]
    assert any(cell["state"] == "есть" and cell["approved"] for cell in matching)
    # Recompute from the actual byte-identical bindings too: a stale stored
    # support file must not be the only healthy witness for adopt.
    healthy = executors.support_cells_of_record(cat, settings, "families", HEALTHY)
    assert KIND in executors.supported_kinds(healthy)
    assert KIND in executors.present_kinds(cat, settings)
    assert RECORD not in {row["id"] for row in cat.families_full(status=None)}
    assert executors.support_cells_of_record(cat, settings, "families", RECORD) == []
    evidence = {"schema": "k06-actual-adopt/1", "scope": "isolated TEST draft only",
                "source_commit": os.environ["A08_RELEASE_SOURCE_COMMIT"],
                "test_source_sha256": _sha(__file__),
                "catalog_version": cat.version(), "catalog_fingerprint": cat.fingerprint(),
                "catalog_before": before, "bindings_before": binding_hashes,
                "healthy_cells": healthy, "events": []}
    evidence_path = tmp_path / "k06-adopt-evidence.json"
    record_property("k06_adopt_evidence", str(evidence_path))
    try:
        yield SimpleNamespace(store=store, journal=journal, cat=cat, folder=folder,
                              evidence=evidence, settings=settings)
    finally:
        after = _census(folder)
        evidence["catalog_after"] = after
        evidence["bindings_after"] = {product: _sha(path) for product, path in paths.items()}
        evidence["support_unchanged"] = (folder / "support.json").read_bytes() == original_support
        _dump(evidence_path, evidence)
        assert set(after) == set(before), "Request/adopt unexpectedly created catalogue files"
        assert {key: value for key, value in after.items() if key != "requests.json"} == {
            key: value for key, value in before.items() if key != "requests.json"}
        assert evidence["bindings_after"] == binding_hashes
        assert evidence["support_unchanged"]
        store.forget()


def new_draft(cat):
    # Same explicit new-record shape as the primary service support tests,
    # but the source catalogue/support/bindings are a genuine TEST release.
    sample = next(record for record in cat.families_full()
                  if isinstance(record.get("subject_words"), list))
    draft = {key: copy.deepcopy(value) for key, value in sample.items()
             if key not in {"status", "history", "stand_tasks"}}
    draft.update(id=RECORD, title="Проба новой записи без исполнителя",
                 params=[], param_sets=[], conditions=[], complements=[],
                 subject_words=None, rule_form=None, executor_kinds=[KIND],
                 check_templates=[])
    return draft


def submit_accept(context, record, draft):
    body = {"section": "families", "record_id": record,
            "source": {"kind": "вручную", "document_class": None,
                       "run_class": None, "ref": None}, "draft": draft}
    if draft is None:
        body["executor_kinds_requested"] = [KIND]
    receipt, status = request_queue.submit(context.store, context.journal, body)
    assert status == 201 and receipt["state"] == "подана"
    request = receipt["request_id"]
    assert receipt["record_id"] == record
    repeated, status = request_queue.submit(context.store, context.journal, body)
    assert status == 200 and repeated["repeat"] is True
    assert repeated["request_id"] == request
    decision = request_queue.decide(context.store, context.journal, request,
                                   {"decision": "принята", "words": "Проверка механики в TEST"})
    assert decision["state"] == "принята"
    context.evidence["events"].append({"submit": receipt, "repeat": repeated,
                                       "decision": decision})
    return request


@pytest.mark.parametrize("confirm", [False, True], ids=["refuse", "test-confirm-draft-only"])
def test_actual_foreign_kind_cannot_approve_a_new_record(local_service, confirm):
    context = local_service
    request = submit_accept(context, RECORD, new_draft(context.cat))
    catalog_before = _census(context.folder)
    with pytest.raises(ServiceError) as refused:
        request_queue.adopt(context.store, context.journal, request, {})
    error = refused.value
    assert error.status == 412 and error.code == "executor-not-present"
    assert error.extra["record"] == "families/" + RECORD
    assert error.extra["kinds"] == [KIND] and error.extra["support"] == []
    assert KIND in error.words and RECORD in error.words
    assert context.store.draft("families", RECORD) is None
    assert _census(context.folder) == catalog_before
    context.evidence["events"].append({"refusal": error.detail(), "status": error.status})
    if confirm:
        answer = request_queue.adopt(context.store, context.journal, request,
                                     {"confirm": "TEST: только черновик, исполнитель не подтверждён"})
        assert answer["draft_written"] is True and answer["executors_missing"] == [KIND]
        assert answer["candidate"] is None
        draft = context.store.draft("families", RECORD)
        assert draft == answer["draft"] and draft["request_id"] == request
        assert draft["record"]["executor_kinds"] == [KIND]
        assert draft["record"]["stand_tasks"] == []
        assert draft["from_candidate"] is False
        rows = context.journal.read()
        assert rows[0]["op"] == "request-adopt" and rows[0]["outcome"] == "warning"
        context.evidence["events"].append({"confirmed_draft": answer})
    # Neither refusal nor TEST confirmation inserts a catalogue record or
    # advances the request to 'есть'. No late build/release gate is invoked.
    current = context.store.catalog()
    assert RECORD not in {row["id"] for row in current.families_full(status=None)}
    assert request_queue.find(current.requests(), request)["state"] == "принята"
    assert executors.support_cells_of_record(current, context.settings, "families", RECORD) == []
    assert executors.supported_kinds_of_record(current, context.settings, "families", RECORD) == set()
    assert _census(context.folder) == catalog_before


def test_actual_own_supported_kind_allows_draft_without_confirm(local_service):
    context = local_service
    request = submit_accept(context, HEALTHY, None)
    catalog_before = _census(context.folder)
    answer = request_queue.adopt(context.store, context.journal, request, {})
    assert answer["draft_written"] is True and answer["executors_missing"] == []
    assert context.store.draft("families", HEALTHY) == answer["draft"]
    assert request_queue.find(context.store.catalog().requests(), request)["state"] == "принята"
    assert _census(context.folder) == catalog_before
    assert context.journal.read()[0]["outcome"] == "ok"
    context.evidence["events"].append({"healthy_own_record_draft": answer})
