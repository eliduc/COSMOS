"""Real comparison of exact provider labels, not a solver/runtime acceptance."""
import copy

import pytest

from webapp import threshold_gate as tg


def evidence(value="Airbus A320, B737", *, unit="code_list", count=2):
    name = "fleet_type" if unit == "code" else "fleet_types"
    pkg = {"model": {"rules": [{"id": "R-FLEET", "kind": "HARD", "params": [
        {"name": name, "value": value, "unit": unit, "relation": "eq"}]}]}}
    applied = [{"threshold": "fleet_types", "value": count,
                "codes": ["Airbus A320", "B737"], "fromParams": [name],
                "rules": ["R-FLEET"]}]
    return pkg, {"fleet_types": count}, {name: "fleet_types"}, applied


def test_matching_count_without_actual_labels_is_not_code_evidence():
    pkg, readings, mapping, applied = evidence()
    result = tg.compare(pkg, readings, mapping=mapping, applied=applied)
    assert any(r["verdict"] == tg.DIVERGE for r in result), result
    assert not any(r["verdict"] == tg.MATCH for r in result), result


def compare_labels(pkg, readings, mapping, applied, labels):
    before = copy.deepcopy((pkg, readings, mapping, applied, labels))
    result = tg.compare(pkg, readings, mapping=mapping, applied=applied,
                        code_readings={"fleet_types": labels})
    assert (pkg, readings, mapping, applied, labels) == before
    return result


def test_exact_labels_match_independently_of_order():
    result = compare_labels(*evidence(), ["B737", "Airbus A320"])
    assert len(result) == 1 and result[0]["verdict"] == tg.MATCH


def test_equal_count_but_wrong_labels_diverges():
    result = compare_labels(*evidence(), ["B737", "Airbus A321"])
    assert result and all(r["verdict"] == tg.DIVERGE for r in result)


def test_equal_claim_and_provider_cannot_replace_original_package():
    pkg, readings, mapping, applied = evidence()
    applied[0]["codes"] = ["A", "B"]
    result = compare_labels(pkg, readings, mapping, applied, ["A", "B"])
    row, = result
    assert row["verdict"] == tg.DIVERGE and row["package_values"] == ["Airbus A320", "B737"]


@pytest.mark.parametrize("label", ["Airbus A320, special", "null", "MiXeD / 42", "А320"])
def test_single_opaque_code_is_not_split_or_case_folded(label):
    pkg, readings, mapping, applied = evidence(label, unit="code", count=1)
    applied[0]["codes"] = [label]
    row, = compare_labels(pkg, readings, mapping, applied, [label])
    assert row["verdict"] == tg.MATCH and row["solver_value"] == [label]


def test_case_and_similar_looking_letters_are_not_equated():
    pkg, readings, mapping, applied = evidence("А320", unit="code", count=1)
    applied[0]["codes"] = ["А320"]
    assert all(r["verdict"] == tg.DIVERGE for r in
               compare_labels(pkg, readings, mapping, applied, ["A320"]))


@pytest.mark.parametrize("labels", ["Airbus A320, B737", [None], [True], [""], ["B737", "B737"], {}])
def test_malformed_actual_code_carrier_is_an_addressed_refusal(labels):
    with pytest.raises(tg.ThresholdGateError, match="fleet_types"):
        compare_labels(*evidence(), labels)


def test_empty_measured_set_is_distinct_from_missing_and_from_original_requirement():
    pkg, readings, mapping, applied = evidence()
    readings["fleet_types"] = 0
    result = compare_labels(pkg, readings, mapping, applied, [])
    assert result and all(r["verdict"] == tg.DIVERGE for r in result)
    assert any(r["solver_value"] == [] for r in result)


def test_count_and_code_carriers_must_agree():
    pkg, readings, mapping, applied = evidence(count=1)
    result = compare_labels(pkg, readings, mapping, applied, ["Airbus A320", "B737"])
    assert result and all(r["verdict"] == tg.DIVERGE for r in result)


def test_missing_mapping_is_not_invented_from_labels():
    pkg, readings, mapping, applied = evidence()
    result = tg.compare(pkg, readings, mapping={"other": "other"}, applied=applied,
                        code_readings={"fleet_types": ["Airbus A320", "B737"]})
    assert any(r["verdict"] == tg.INCOMPARABLE for r in result)
