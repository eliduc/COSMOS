"""Reserved machine-reader versions fail closed; authored ledgers stay usable.

Synthetic exact source bytes and canned channels only. Actual parser, sealing
and evaluator remain unchanged by the fixtures; no live or expert claim.
"""
import copy

import pytest

from cdpm import obligations as ob
from test_a12_obligation_readers import run
from test_a12_quote_address_recovery_agent import QUOTE, UNICODE_TEXT, _case_inputs_channels
from test_cdpm_a12_obligations import _fixture


@pytest.mark.parametrize("version", [
    "obligation-readings/3", "obligation-readings/0", "obligation-readings/02",
    "obligation-readings/2 ", "obligation-readings/",
])
@pytest.mark.parametrize("reader_index", [0, 1])
@pytest.mark.parametrize("retain_address", [True, False])
def test_unknown_machine_version_is_not_validated_as_known_schema(
        version, reader_index, retain_address):
    inputs, left, right, _ = _case_inputs_channels(
        UNICODE_TEXT, {"start": 6, "end": 30, "quote": QUOTE})
    result, _, _ = run(inputs, left, right)
    healthy = copy.deepcopy(result.ledger)
    assert ob.evaluate(healthy, **inputs)["form"] == "valid"
    candidate = copy.deepcopy(healthy)
    reader = candidate["readers"][reader_index]
    reader["version"] = version
    if not retain_address:
        for item in reader["obligations"]:
            for ref in item["sources"]:
                for fragment in ref["fragments"]:
                    fragment.pop("address")
    candidate["readers"][reader_index] = ob.seal_reader(reader)
    before = copy.deepcopy(candidate)
    report = ob.evaluate(candidate, **inputs)
    assert report["form"] == "invalid", report
    expected = f"readers/{reader['id']}.version: unsupported machine reading version"
    assert any(expected in issue.get("detail", "") for issue in report["issues"]), report
    assert report["readers"] == [] and report["meaning"] == "not_proven"
    assert candidate == before and result.ledger == healthy


@pytest.mark.parametrize("reader_index", [0, 1])
def test_unknown_machine_version_is_rejected_even_without_obligations(reader_index):
    ledger, inputs = _fixture()
    reader = ledger["readers"][reader_index]
    reader["method"] = "isolated exact-unit reading"
    reader["version"] = "obligation-readings/3"
    reader["obligations"] = []
    ledger["readers"][reader_index] = ob.seal_reader(reader)
    before = copy.deepcopy(ledger)
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "invalid", report
    assert any("unsupported machine reading version" in issue.get("detail", "")
               for issue in report["issues"]), report
    assert report["readers"] == [] and report["meaning"] == "not_proven"
    assert ledger == before


@pytest.mark.parametrize("version", ["1", "2", "manual-review/3"])
@pytest.mark.parametrize("reader_index", [0, 1])
def test_authored_version_names_are_not_machine_schema_versions(version, reader_index):
    ledger, inputs = _fixture()
    reader = ledger["readers"][reader_index]
    reader["version"] = version
    ledger["readers"][reader_index] = ob.seal_reader(reader)
    before = copy.deepcopy(ledger)
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "valid" and report["freshness"] == "current", report
    assert report["meaning"] == "not_proven" and ledger == before
