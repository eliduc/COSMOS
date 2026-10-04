"""CDPM-417: unproven is a computed fact, not an escape hatch."""
import hashlib
import json

import pytest

from cdpm import logic_proof as lp
from test_cdpm_a10_proof_subject import _files, _gate, _minimum
from test_cdpm_logic_proof import _rule_duration


def declare(files, declarations, *, remove_pair=False):
    manifest = json.loads(files["manifest.json"])
    manifest["logic_unproven"] = declarations
    if remove_pair:
        examples = json.loads(files["logic-examples.json"])
        del examples["R-D"]
        files["logic-examples.json"] = json.dumps(examples).encode("utf-8")
        for entry in manifest["files"]:
            if entry["name"] == "logic-examples.json":
                entry["sha256"] = hashlib.sha256(files["logic-examples.json"]).hexdigest()
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")


@pytest.mark.parametrize("reason", ["not proven", "", None])
def test_provable_rule_cannot_escape_by_claiming_unproven(tmp_path, reason):
    rule = _rule_duration()
    assert lp.generate_pair(rule)[0] is not None
    files = _files(tmp_path, rule)
    declare(files, [{"rule": "R-D", "reason": reason}], remove_pair=True)
    problems = _gate(tmp_path, files)
    assert any("R-D" in p and "logic_unproven" in p for p in problems), problems


@pytest.mark.parametrize("reason", ["", " ", None, 0, {}])
def test_real_unproven_rule_still_needs_an_actual_reason(tmp_path, reason):
    files = _files(tmp_path, _minimum())
    assert lp.generate_pair(_minimum())[0] is None
    declare(files, [{"rule": "R-D", "reason": reason}])
    assert any("logic_unproven" in p for p in _gate(tmp_path, files))


def test_present_pair_does_not_make_a_false_unproven_claim_true(tmp_path):
    files = _files(tmp_path, _rule_duration())
    declare(files, [{"rule": "R-D", "reason": "not proven"}])
    assert any("logic_unproven" in p for p in _gate(tmp_path, files))


@pytest.mark.parametrize("corruption", ["duplicate", "unknown"])
def test_unproven_identities_are_checked(tmp_path, corruption):
    files = _files(tmp_path, _minimum())
    entries = json.loads(files["manifest.json"])["logic_unproven"]
    if corruption == "duplicate":
        entries.append(dict(entries[0]))
    else:
        entries.append({"rule": "NO-SUCH-RULE", "reason": "not proven"})
    declare(files, entries)
    assert any("logic_unproven" in p for p in _gate(tmp_path, files))


@pytest.mark.parametrize("declarations", [None, "R-D", {"rule": "R-D"}])
def test_unproven_container_must_be_a_list(tmp_path, declarations):
    files = _files(tmp_path, _minimum())
    declare(files, declarations)
    assert any("logic_unproven" in p for p in _gate(tmp_path, files))


@pytest.mark.parametrize("minimum", [False, True])
def test_honest_proven_and_unproven_packages_remain_healthy(tmp_path, minimum):
    files = _files(tmp_path, _minimum() if minimum else _rule_duration())
    assert _gate(tmp_path, files) == []

