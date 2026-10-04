"""Owner's 2026-09-18 threshold=base contract, isolated synthetic inputs.

Actual CLI translation, catalogue-signature price reader and certificate
measure. No solver, HTTP, model channel, release gate or catalogue mutation.
"""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from vmp import chain_price as cp, pairing_input as pi
from vmp.run import roster_certify as rc


MISSING = object()
APPLIED = {"min_sit_home_min": 45, "min_sit_outstation_min": 60,
           "max_duty_span_min": 720, "min_rest_home_min": 720,
           "min_rest_outstation_min": 600}
PAYLOAD = {"employees": [{"id": "E1", "base": "AAA", "skills": ["CP"]}],
           "flights": [{"departureUTCDateTime": "2026-10-01T06:00:00Z",
                        "arrivalUTCDateTime": "2026-10-01T18:30:00Z"}]}


def penalty(alias=MISSING):
    params = {"duty_extension_cap": 960}
    if alias is not MISSING:
        params["long_duty_threshold"] = alias
    return {"rule_id": "R-EXT", "family": "duty_extension_penalty",
            "weight": 3, "params": params, "days": []}


def signature(base, alias=MISSING):
    out = {"price_model": cp.VERSION,
           "price_scope": cp.scope_template(["AAA"], eligible_bases_complete=True),
           "duty_span": base, "w_long_duty": 3,
           "soft_rule_ids": "duty_extension_penalty=R-EXT"}
    if alias is not MISSING:
        out["long_duty_threshold"] = alias
    return out


def rule(rid, family, kind, params):
    return {"id": rid, "kind": kind, "zone": "BOTH", "statement": "synthetic",
            "template_fit": {"category": "A", "family": family,
                             "params": {key: {"value": value} for key, value in params.items()}}}


def certificate_input(base, alias=MISSING, *, base_kind="HARD", excluded=False):
    rules = [rule("R-EXT", "duty_extension_penalty", "SOFT", penalty(alias)["params"])]
    if base is not None:
        hard = rule("R-BASE", "duty_limits", base_kind, {"max_duty_duration": base})
        if excluded:
            hard["excluded_until_data"] = {"question": "Q-BASE"}
        rules.insert(0, hard)
    data = {"tables": {rc.SOLUTION_SID: {"Смены": {
        "columns": ["Табельный номер", "Идентификатор", "Начало", "Конец",
                    "База приписки", "Место отдыха"],
        "rows": [{"Табельный номер": "E1", "Идентификатор": "D1",
                  "Начало": "2026-10-01T06:00", "Конец": "2026-10-01T18:30",
                  "База приписки": "AAA", "Место отдыха": "AAA"}]}}}}
    return {"rules": rules}, data


@pytest.mark.parametrize("base,alias", [(720, 720), (780, 780), (720, MISSING)])
def test_pairing_uses_applied_base_and_never_changes_input(base, alias):
    applied = dict(APPLIED, max_duty_span_min=base)
    penalties = [penalty(alias)]
    before = deepcopy((applied, penalties))
    args, provenance = pi.builder_args(applied, PAYLOAD, 60, soft_penalties=penalties)
    assert args[args.index("--long-duty-threshold") + 1] == str(base)
    assert provenance["--long-duty-threshold"]["value"] == base
    assert (applied, penalties) == before


@pytest.mark.parametrize("base,alias", [(720, 780), (780, 720), (720, True), (720, 720.5)])
def test_pairing_refuses_different_or_invalid_alias(base, alias):
    with pytest.raises(pi.PairingInputError) as error:
        pi.builder_args(dict(APPLIED, max_duty_span_min=base), PAYLOAD, 60,
                        soft_penalties=[penalty(alias)])
    assert "R-EXT" in str(error.value)
    assert "long_duty_threshold" in str(error.value)


@pytest.mark.parametrize("base,alias,measure", [(720, 720, 1), (780, 780, 0), (720, MISSING, 1)])
def test_signature_price_measure_uses_exact_base(base, alias, measure):
    sig = signature(base, alias)
    before = deepcopy(sig)
    extension = next(r for r in cp.soft_rules_from_signature(sig)
                     if r["family"] == "duty_extension_penalty")
    start = datetime(2026, 10, 1, 6)
    got = cp.measure(extension, [cp.Period(start, start + timedelta(minutes=750), "AAA")],
                     units={"duty": "смена"})
    assert got["measure"] == measure
    if measure:
        assert got["evidence"][0]["threshold_min"] == base
    assert sig == before


@pytest.mark.parametrize("base,alias", [(720, 780), (780, 720), (None, 720)])
def test_signature_refuses_mismatch_or_missing_base(base, alias):
    with pytest.raises(cp.ChainPriceError) as error:
        cp.soft_rules_from_signature(signature(base, alias))
    assert "R-EXT" in str(error.value)
    assert "long_duty_threshold" in str(error.value) or "баз" in str(error.value)


@pytest.mark.parametrize("base,alias,measure", [(720, 720, 1), (780, 780, 0), (720, MISSING, 1)])
def test_certificate_measures_the_base_not_an_independent_soft_threshold(base, alias, measure):
    svod, dataset = certificate_input(base, alias)
    before = deepcopy(svod)
    checks, findings = rc.measure_soft_rules(svod, dataset)
    own = next(c for c in checks if c["basis"]["rule"] == "R-EXT")
    assert own["status"] == "прогнана", own
    assert len([f for f in findings if f["rule_refs"] == ["R-EXT"]]) == measure
    assert svod == before


@pytest.mark.parametrize("base,alias,kind,excluded", [
    (720, 780, "HARD", False), (780, 720, "HARD", False),
    (None, 720, "HARD", False), (720, 720, "SOFT", False),
    (720, 720, "OUT", False), (720, 720, "HARD", True),
])
def test_certificate_names_unavailable_or_conflicting_base(base, alias, kind, excluded):
    svod, dataset = certificate_input(base, alias, base_kind=kind, excluded=excluded)
    checks, findings = rc.measure_soft_rules(svod, dataset)
    own = next(c for c in checks if c["basis"]["rule"] == "R-EXT")
    assert own["status"] == "не прогнана", own
    assert "баз" in own.get("reason", "").lower(), own
    assert not [f for f in findings if f["rule_refs"] == ["R-EXT"]]


@pytest.mark.parametrize("extra", [
    {"unit": "hours"}, {"relation": "min"},
    {"condition": {"kind": "base_code", "value": "AAA"}},
    {"qualifier": "reserve"}, {"pending_revision": 2},
])
def test_certificate_does_not_invent_unconditional_base_from_unusable_cell(extra):
    svod, dataset = certificate_input(720, 720)
    svod["rules"][0]["template_fit"]["params"]["max_duty_duration"].update(extra)
    before = deepcopy(svod)
    checks, findings = rc.measure_soft_rules(svod, dataset)
    own = next(c for c in checks if c["basis"]["rule"] == "R-EXT")
    assert own["status"] == "не прогнана"
    assert "баз" in own["reason"] and "R-BASE" in own["reason"]
    assert not [f for f in findings if f["rule_refs"] == ["R-EXT"]]
    assert svod == before


def test_certificate_uses_legacy_primary_base_and_preserves_both_input_carriers():
    svod, dataset = certificate_input(780, 720)
    svod["rules"][0]["params"] = [{"name": "max_duty_duration", "value": 720,
                                    "unit": "duration_minutes", "relation": "max"}]
    before = deepcopy(svod)
    checks, findings = rc.measure_soft_rules(svod, dataset)
    own = next(c for c in checks if c["basis"]["rule"] == "R-EXT")
    assert own["status"] == "прогнана"
    assert len([f for f in findings if f["rule_refs"] == ["R-EXT"]]) == 1
    assert svod == before


def test_certificate_uses_stricter_of_actual_hard_maxima():
    svod, dataset = certificate_input(780, 720)
    svod["rules"].insert(0, rule("R-BASE-STRICT", "duty_limits", "HARD",
                                {"max_duty_duration": 720}))
    checks, findings = rc.measure_soft_rules(svod, dataset)
    own = next(c for c in checks if c["basis"]["rule"] == "R-EXT")
    assert own["status"] == "прогнана"
    assert len([f for f in findings if f["rule_refs"] == ["R-EXT"]]) == 1


@pytest.mark.parametrize("alias,valid", [(720, True), (780, False)])
def test_pairing_legacy_alias_reaches_the_same_base_guard(alias, valid):
    extension = rule("R-EXT", "duty_extension_penalty", "SOFT", {"duty_extension_cap": 960})
    extension["weight"] = 3
    extension["params"] = [{"name": "long_duty_threshold", "value": alias}]
    pkg = {"model": {"rules": [extension]}}
    before = deepcopy(pkg)
    penalties = pi.pairing_soft_penalties(pkg)
    if valid:
        args, _ = pi.builder_args(APPLIED, PAYLOAD, 60, soft_penalties=penalties)
        assert args[args.index("--long-duty-threshold") + 1] == "720"
    else:
        with pytest.raises(pi.PairingInputError, match="R-EXT.*long_duty_threshold"):
            pi.builder_args(APPLIED, PAYLOAD, 60, soft_penalties=penalties)
    assert pkg == before


@pytest.mark.parametrize("alias,valid", [(720, True), (780, False)])
def test_certificate_legacy_alias_is_not_silently_dropped(alias, valid):
    svod, dataset = certificate_input(720)
    svod["rules"][1]["params"] = [{"name": "long_duty_threshold", "value": alias}]
    before = deepcopy(svod)
    checks, findings = rc.measure_soft_rules(svod, dataset)
    own = next(c for c in checks if c["basis"]["rule"] == "R-EXT")
    assert own["status"] == ("прогнана" if valid else "не прогнана")
    assert len([f for f in findings if f["rule_refs"] == ["R-EXT"]]) == (1 if valid else 0)
    if not valid:
        assert "R-EXT" in own["reason"] and "long_duty_threshold" in own["reason"]
    assert svod == before


def test_fit_legacy_alias_conflict_is_named_in_both_python_readers():
    svod, dataset = certificate_input(720, 720)
    extension = svod["rules"][1]
    extension["weight"] = 3
    extension["params"] = [{"name": "long_duty_threshold", "value": 780}]
    before = deepcopy(svod)
    with pytest.raises(pi.PairingInputError, match="R-EXT.*long_duty_threshold"):
        pi.pairing_soft_penalties({"model": svod})
    checks, findings = rc.measure_soft_rules(svod, dataset)
    own = next(c for c in checks if c["basis"]["rule"] == "R-EXT")
    assert own["status"] == "не прогнана" and "носители" in own["reason"]
    assert not [f for f in findings if f["rule_refs"] == ["R-EXT"]]
    assert svod == before
