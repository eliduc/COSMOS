"""P0 §3.1: whole typed identifiers are not free-text answers to re-parse."""
import pytest

from cdpm import family_params as fp, model


NAMES = ["Boeing 737 MAX", "A320/321", "A.320", "Полярис–Р200",
         "Не A320", "Кроме—A320", "Тип с пробелом", "A" * 40]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("unit", ["code", "code_list"])
def test_typed_identifier_is_preserved_in_full(name, unit):
    assert fp.canonical_value(name, "", unit) == (name, None)


def test_typed_list_preserves_names_and_only_canonicalizes_list_structure():
    values = ["Boeing 737 MAX", "A320/321", "Boeing 737 MAX"]
    want = "A320/321, Boeing 737 MAX"
    assert fp.canonical_value(values, "code_list", "code_list") == (want, None)
    assert fp.canonical_value(", ".join(values), "", "code_list") == (want, None)


@pytest.mark.parametrize("raw", ["Не A320", "Кроме—A320", "A320 не используем"])
def test_typed_identifier_does_not_relax_free_answer_grammar(raw):
    assert fp.canonical_value(raw, "", "code_list") == (raw, None)
    assert fp.parse_value(raw, "code_list") is None


@pytest.mark.parametrize("name", ["Boeing 737 MAX", "A320/321", "A.320"])
def test_typed_name_is_given_by_family_coverage_without_fragmentation(name):
    from test_cdpm_k02_number_grammar import _prepared_fleet
    doc, rule, _ = _prepared_fleet()
    rule["template_fit"]["params"]["fleet_types"] = {"value": [name], "quote": name}
    coverage = fp.family_coverage(doc)["fleet_exclusions"]
    assert "fleet_types" in coverage["given"]
    assert "fleet_types" not in coverage["unreadable"]
    assert rule["template_fit"]["params"]["fleet_types"]["value"] == [name]


@pytest.mark.parametrize("name", ["Boeing 737 MAX", "A320/321", "A.320"])
def test_reconciliation_compares_whole_typed_names(name):
    from test_cdpm_a05_carriers_barrier import _landed, _legacy

    def rule(other):
        return _landed(family="fleet_exclusions", cells={"fleet_types": {
            "value": [name], "unit": "code_list", "quote": "тип"}},
            legacy=[_legacy("fleet_types", other, unit="code_list",
                            relation="in", as_written="тип")])

    assert model.reconcile_carriers(rule(name)) == []
    assert model.reconcile_carriers(rule("B738"))


@pytest.mark.parametrize("raw", ["A320,B738", ["A320", "B738"], "A320,,B738"])
def test_single_code_still_refuses_a_list(raw):
    value, error = fp.canonical_value(raw, "", "code")
    assert value is None and error
