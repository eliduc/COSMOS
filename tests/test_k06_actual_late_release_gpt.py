"""Late release executor gate on an actual retained TEST catalogue.

Reuse A08_RELEASE_INPUT_ROOT / A08_RELEASE_SOURCE_COMMIT and its protected
retained-release fixture. Only temporary copies of raw bindings are mutated.
No release act, approvals, service, solver, network or subprocess is invoked.
The late gate judges named executors, NOT all-green stand support.
"""
import copy
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import template_catalog as tc
from catalog_service import executors, releases
from catalog_service.settings import Settings
from test_a08_actual_release_sc3_gpt import released, _census, _dump


RECORDS = ("duty_series_limit", "crew_unavailability")
CELLS = (("duty_series_limit", "check_code"),
         ("duty_series_limit", "kotlin_logic_form"),
         ("crew_unavailability", "check_code"),
         ("crew_unavailability", "kotlin_constraint"))


@pytest.fixture
def late_copy(released, tmp_path):
    local = tmp_path / "late-gate-copy"
    folder = local / "catalog"
    shutil.copytree(released.cat.path, folder)
    paths = {}
    for product, env in (("COSMOS", "CREW_COSMOS_BINDINGS"), ("CDPM", "CDPM_BINDINGS_FILE")):
        paths[product] = local / f"{product}.json"
        shutil.copyfile(Path(os.environ[env]), paths[product])
    before_catalog = _census(folder)
    before_raw = {name: path.read_bytes() for name, path in paths.items()}
    cat = tc.load(folder)
    assert cat.fingerprint() == released.cat.fingerprint()
    assert cat.version() == released.cat.version() == "0.7.0"
    requests = [r for r in cat.requests() if r.get("state") == "есть"]
    assert {(r["section"], r["record_id"]) for r in requests} == {
        ("families", rid) for rid in RECORDS}
    settings = Settings(catalog_dir=folder, work_dir=local / "never-created-work",
                        read_only=True, cosmos_bindings=paths["COSMOS"], cdpm_bindings=paths["CDPM"])
    # _check_executors only reads store.settings. No service/store writer is created.
    store = SimpleNamespace(settings=settings)
    evidence = {"scope": "late executor naming gate only; TEST copy; no act or launch",
                "source_commit": released.evidence["source_commit"],
                "catalog": released.evidence["catalog"], "requests": requests,
                "original_raw_sha256": released.evidence["protected_before"]["raw_bindings"],
                "observations": []}

    def check(label):
        raw = {name: path.read_bytes() for name, path in paths.items()}
        result = releases._check_executors(store, cat)
        assert {name: path.read_bytes() for name, path in paths.items()} == raw
        assert _census(folder) == before_catalog
        assert not settings.work_dir.exists()
        evidence["observations"].append({"label": label,
            "result": None if result is None else result.detail(),
            "cells": {rid: executors.record_cells(cat, settings, "families", rid) for rid in RECORDS},
            "stand": {rid: executors.record_stand(cat, settings, "families", rid) for rid in RECORDS}})
        return result

    def restore():
        for name, path in paths.items():
            path.write_bytes(before_raw[name])
        assert check("restored-actual-raw-bindings") is None

    try:
        assert check("actual-unmodified-healthy") is None
        # The actual crew record has no stand rows. The real late gate still
        # correctly accepts its named implementations; this is not support PASS.
        assert executors.record_stand(cat, settings, "families", "crew_unavailability") == {
            "passed": 0, "failed": 0, "skipped": 0, "rows": []}
        yield SimpleNamespace(cat=cat, settings=settings, paths=paths, check=check,
                              restore=restore, evidence=evidence, original=before_raw)
    finally:
        restore()
        assert _census(folder) == before_catalog
        assert {name: path.read_bytes() for name, path in paths.items()} == before_raw
        _dump(tmp_path / "late-gate-evidence.json", evidence)


def _remove_own_kind(context, record, kind):
    docs = {name: json.loads(path.read_bytes()) for name, path in context.paths.items()}
    foreign = {name: copy.deepcopy([row for row in doc["bindings"]
                                   if (row["section"], row["id"]) != ("families", record)])
               for name, doc in docs.items()}
    assert any(ex.get("kind") == kind and ex.get("state") == "есть"
               for rows in foreign.values() for row in rows for ex in row.get("executors", [])), kind
    removed = []
    for product, doc in docs.items():
        for row in doc["bindings"]:
            if (row["section"], row["id"]) == ("families", record):
                removed.extend((product, copy.deepcopy(ex)) for ex in row.get("executors", []) if ex.get("kind") == kind)
                row["executors"] = [ex for ex in row.get("executors", []) if ex.get("kind") != kind]
        assert [row for row in doc["bindings"] if (row["section"], row["id"]) != ("families", record)] == foreign[product]
        _dump(context.paths[product], doc)
    assert removed and any(ex["state"] == "есть" for _product, ex in removed)
    assert kind not in executors.record_cells(context.cat, context.settings, "families", record)
    # A global kind census remains positive. It must NOT fill this record's gap.
    assert kind in executors.present_kinds(context.cat, context.settings)
    return docs, removed, foreign


def _assert_exact_refusal(result, record, kind):
    assert result is not None
    assert result.code == "executors-incomplete" and result.status == 412
    row, = result.extra["rows"]
    assert row["record"] == "families/" + record
    assert kind in row["words"] and row["record"] in result.words


@pytest.mark.parametrize("record,kind", CELLS)
def test_late_gate_cannot_borrow_same_kind_from_foreign_record(late_copy, record, kind):
    context = late_copy
    docs, removed, foreign = _remove_own_kind(context, record, kind)
    _assert_exact_refusal(context.check("own-kind-removed-foreign-kind-retained"), record, kind)
    context.evidence.update(case="actual-record-kind-removal", record=record, kind=kind,
                            removed=removed, foreign_same_kind_preserved=[
                                {"product": product, "section": row["section"], "record": row["id"]}
                                for product, rows in foreign.items() for row in rows
                                if any(ex.get("kind") == kind and ex.get("state") == "есть"
                                       for ex in row.get("executors", []))])
    context.restore()


@pytest.mark.parametrize("record", RECORDS)
@pytest.mark.parametrize("state,cr_key,note,allowed", [
    ("заявлен", "cr0123abcd", None, True),
    ("заявлен", "crNOTVALID", None, False),
    ("нет", None, "Synthetic naming-only control: implementation explicitly not provided", True),
    ("нет", None, "   ", False),
])
def test_late_gate_preserves_honest_declared_and_absent_states(
        late_copy, record, state, cr_key, note, allowed):
    context, kind = late_copy, "check_code"
    docs, _removed, _foreign = _remove_own_kind(context, record, kind)
    # Explicit synthetic copy only: no new request, executor, stand or approval
    # is represented as real. This exercises the exact accepted naming grammar.
    row = next(r for r in docs["COSMOS"]["bindings"] if
               (r["section"], r["id"]) == ("families", record))
    row["executors"].append({"kind": kind, "state": state, "cr_key": cr_key,
                             "note": note, "file": None, "symbol": None, "lang": "python"})
    _dump(context.paths["COSMOS"], docs["COSMOS"])
    result = context.check("explicit-synthetic-naming-control")
    if allowed:
        assert result is None
    else:
        _assert_exact_refusal(result, record, kind)
    context.evidence.update(case="synthetic-naming-control-not-product-support", record=record,
                            state=state, cr_key=cr_key, note=note, accepted_by_naming_gate=allowed)
    context.restore()
