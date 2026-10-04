"""A10 consumer surface and CDPM-418: report the actual unproven reason."""
import json

import pytest

from cdpm import export_task_package as tp, logic_proof as lp, model
from cdpm.gates import gate_task_package as gate
from test_cdpm_a10_proof_subject import _minimum, _files, _gate
from test_cdpm_a10_unproven_integrity import declare
from test_cdpm_logic_proof import _rule_duration
from test_cdpm_task_package import _ready_doc, _run_dir


@pytest.mark.parametrize("subject", ["duty", "assignment"])
def test_reason_from_another_form_is_refused(tmp_path, subject):
    files = _files(tmp_path, _minimum(subject))
    declare(files, [{"rule": "R-D", "reason": "count_within: предмет вне первой волны"}])
    problems = _gate(tmp_path, files)
    assert any("logic_unproven" in p and "причин" in p for p in problems), problems


def mixed_files(tmp_path, minimum):
    doc = _ready_doc()
    other = _rule_duration()
    other["id"] = "R-OTHER"
    doc["rules"] += [other, _minimum() if minimum else _rule_duration()]
    assert model.validate_model(doc) == []
    return tp.build_package(_run_dir(tmp_path, doc))


@pytest.mark.parametrize("minimum", [False, True])
def test_human_form_names_count_rule_and_complete_reason(tmp_path, minimum):
    files = mixed_files(tmp_path, minimum)
    unproven = json.loads(files["manifest.json"]).get("logic_unproven", [])
    assert len(unproven) == int(minimum)
    text = files["vmp-form.md"].decode("utf-8")
    assert f"Недоказанных правил: {len(unproven)}" in text
    for row in unproven:
        assert row["rule"] in text and row["reason"] in text


def test_gate_words_distinguish_equal_rule_counts(tmp_path, capsys):
    outputs = []
    counts = []
    for name, minimum in [("mixed", True), ("proven", False)]:
        files = mixed_files(tmp_path / name, minimum)
        manifest = json.loads(files["manifest.json"])
        counts.append((len(manifest["files"]), len(json.loads(files["model.json"])["rules"])))
        path = tmp_path / (name + ".zip")
        path.write_bytes(tp.write_zip(files))
        assert gate.run(str(path)) == 0
        outputs.append(capsys.readouterr().out)
    assert counts[0] == counts[1], "selection control: same files/rules"
    assert "Недоказанных правил: 1" in outputs[0]
    assert "Недоказанных правил: 0" in outputs[1]
    why = lp.generate_pair(_minimum())[1]
    assert "R-D" in outputs[0] and why in outputs[0]
    assert outputs[0] != outputs[1]


def test_invalid_manifest_reason_is_not_published_as_fact(tmp_path, capsys):
    files = mixed_files(tmp_path, True)
    fake = "count_within: FAKE-REASON-FROM-ANOTHER-RULE"
    declare(files, [{"rule": "R-D", "reason": fake}])
    path = tmp_path / "fake-reason.zip"
    path.write_bytes(tp.write_zip(files))
    assert gate.run(str(path)) == 1
    words = capsys.readouterr().out
    assert "Недоказанных правил: 1" in words
    assert lp.generate_pair(_minimum())[1] in words
    assert fake not in words


def test_unfinished_check_does_not_claim_zero_unproven_rules():
    words = lp.render_unproven_summary(None)
    assert "не установлено" in words
    assert "Недоказанных правил: 0" not in words
