"""A05 numeric threshold forms consume landing values, without mutating source.

Synthetic dictionaries and temporary packages; no live LLM/server/JVM. Package
checks use the existing deterministic translator stub and local SMT verification.
"""
import copy
import hashlib
import json

import pytest

from cdpm import answers, family_params as fp, model as m, threshold_forms as tf
from test_cdpm_task_package import _ready_doc, _run_dir
from test_cdpm_threshold_forms import _doc_with_matrix


PARAM = "min_rest_outstation"


def _floor():
    return {"id": "R-TZ", "title": "Отдых", "kind": "HARD", "zone": "ROSTERING",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "statement": "Дома отдых 12 часов. Вне базы при разнице часовых поясов >= 4 часов отдых задаётся ответом.",
            "template_fit": {"category": "A", "family": "rest_floors",
                             "registry_rev": m.registry_fingerprint(),
                             "params": {"min_rest_home": {"value": 720, "quote": "12 часов"},
                                        PARAM: {"value": 600, "quote": "10 часов"}}}}


@pytest.mark.parametrize("value,unit", [(600, None), (10, "ч"), ("600", "duration_minutes")])
def test_conditional_floor_reads_and_canonicalizes_landing_only(value, unit):
    rule = _floor()
    cell = rule["template_fit"]["params"][PARAM]
    cell["value"] = value
    if unit is not None:
        cell["unit"] = unit
    before = copy.deepcopy(rule)
    floors = tf.assemble_conditional_floors({"rules": [rule]})
    assert len(floors) == 1
    assert floors[0]["value_min"] == 600
    assert floors[0]["condition"] == {"kind": "tz_diff_hours", "relation": "min", "value": 4}
    assert rule == before


def test_matrix_reads_landing_only_without_changing_its_shape():
    doc = _doc_with_matrix()
    expected = tf.assemble_matrices(doc)
    for rule in doc["rules"]:
        p = rule.pop("params")[0]
        rule["template_fit"] = {"category": "A", "family": "duty_limits",
                                "registry_rev": m.registry_fingerprint(),
                                "params": {p["name"]: {"value": p["value"], "quote": p["as_written"]}}}
    before = copy.deepcopy(doc)
    assert tf.assemble_matrices(doc) == expected
    assert doc == before


def test_wocl_reads_all_three_landing_thresholds():
    rules = []
    for index, (text, value) in enumerate([
        ("циркадное окно отсутствует", 6),
        ("попадание в циркадное окно более чем на 2 часа", 4),
        ("попадание в циркадное окно на 2 часа или меньше", 5),
    ]):
        rules.append({"id": f"R-W{index}", "kind": "HARD", "statement": text,
                      "template_fit": {"category": "A", "family": "duty_limits",
                                       "registry_rev": m.registry_fingerprint(),
                                       "params": {"max_landings_per_duty": {"value": value, "quote": str(value)}}}})
    form = tf.assemble_wocl_sector_limits({"rules": rules})
    assert form and form["limits"] == {"no_touch": 6, "touch_gt_threshold": 4, "touch_le_threshold": 5}


@pytest.mark.parametrize("value,relation", [(900, "min"), (600, "max")])
def test_forms_refuse_original_divergence_without_rewriting_evidence(value, relation):
    rule = _floor()
    rule["params"] = [{"name": PARAM, "value": value, "unit": "duration_minutes",
                       "relation": relation, "as_written": f"{value} минут"}]
    before = copy.deepcopy(rule)
    with pytest.raises(ValueError, match="два носителя"):
        tf.forms_bytes({"rules": [rule]})
    assert rule == before


@pytest.mark.parametrize("change", [{"unit": "unknown"}, {"value": True},
                                    {"condition": "только для базы SVO"},
                                    {"relation": "max"}])
def test_unrepresentable_landing_is_refused_not_silently_omitted_or_broadened(change):
    rule = _floor()
    rule["template_fit"]["params"][PARAM].update(change)
    before = copy.deepcopy(rule)
    with pytest.raises(ValueError):
        tf.forms_bytes({"rules": [rule]})
    assert rule == before


def test_answer_only_threshold_reaches_package_and_gate(tmp_path):
    from cdpm import export_task_package as tp
    from cdpm.gates import gate_task_package as gate
    doc = _ready_doc()
    rule = _floor()
    del rule["template_fit"]["params"][PARAM]
    doc["rules"].append(rule)
    qid = fp.ensure_family_param_questions(doc)[0]
    assert answers.apply_answers(doc, {qid: "10 часов"})["applied"] == 1
    assert "params" not in rule
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    before = copy.deepcopy(doc)
    run = _run_dir(tmp_path, doc)
    files = tp.build_package(run)
    assert "threshold-forms.json" in files
    forms = json.loads(files["threshold-forms.json"])
    assert forms["conditional_floors"][0]["value_min"] == 600
    assert json.loads((run / "extract/model.json").read_text(encoding="utf-8")) == before
    path = tmp_path / "package.zip"
    path.write_bytes(tp.write_zip(files))
    assert gate.check(path)[0] == []
    assert tp.build_package(run) == files
    # Rebuild the digest too: rejection must not depend on a stale checksum.
    forged = json.loads(files["model.json"])
    forged_rule = next(r for r in forged["rules"] if r["id"] == rule["id"])
    forged_rule["template_fit"]["params"][PARAM]["condition"] = "только для базы SVO"
    files["model.json"] = json.dumps(forged, ensure_ascii=False).encode("utf-8")
    manifest = json.loads(files["manifest.json"])
    entry = next(e for e in manifest["files"] if e["name"] == "model.json")
    entry.update(sha256=hashlib.sha256(files["model.json"]).hexdigest(), bytes=len(files["model.json"]))
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    path.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(path)
    assert any("threshold-forms.json" in p and "область" in p for p in problems)


def test_package_refuses_unrepresentable_scope_with_words(tmp_path):
    from cdpm import export_task_package as tp
    doc = _ready_doc()
    rule = _floor()
    rule["template_fit"]["params"][PARAM]["condition"] = "только для базы SVO"
    doc["rules"].append(rule)
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    with pytest.raises(tp.TaskPackageError, match="область") as error:
        tp.build_package(_run_dir(tmp_path, doc))
    assert error.value.items == [f"R-TZ/{PARAM}"]


@pytest.mark.parametrize("landed", [False, True])
@pytest.mark.parametrize("scope,accepted", [("вне базы", True), ("в базе", False),
                                          ("только для базы SVO", False)])
def test_scope_is_preserved_or_refused_for_both_carrier_formats(landed, scope, accepted):
    rule = _floor()
    if landed:
        rule["template_fit"]["params"][PARAM]["condition"] = scope
    else:
        rule.pop("template_fit")
        rule["params"] = [{"name": PARAM, "value": 600, "unit": "duration_minutes",
                           "relation": "min", "as_written": "10 часов", "qualifier": scope}]
    before = copy.deepcopy(rule)
    if accepted:
        assert tf.assemble_conditional_floors({"rules": [rule]})[0]["value_min"] == 600
    else:
        with pytest.raises(ValueError, match="область"):
            tf.assemble_conditional_floors({"rules": [rule]})
    assert rule == before


def test_scope_refusal_explains_supported_boundary_without_erasing_restriction():
    rule = _floor()
    rule["template_fit"]["params"][PARAM]["condition"] = "только для базы SVO"
    before = copy.deepcopy(rule)
    with pytest.raises(tf.ThresholdFormError) as caught:
        tf.assemble_conditional_floors({"rules": [rule]})
    words = str(caught.value)
    assert "представима область «вне базы»" in words
    assert "не снимайте ограничение ради выпуска" in words
    assert "только для базы SVO" in words
    assert caught.value.items == [f"R-TZ/{PARAM}"]
    assert rule == before
