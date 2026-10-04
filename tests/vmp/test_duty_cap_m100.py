"""Мера 100 (НАДЗОР 11:2x): потолок продления смены (duty_extension_cap ВМП) — жёсткий предел построителя
пейринга. Смена длиннее базы и не длиннее потолка допустима (штраф продления — от базы); длиннее потолка —
нет; без потолка предел — база. Адресная политика (базы / согласие) в пейринге не исполняется."""
import copy

import pytest

import _pairing_builder as _pb
from vmp import execution_manifest as xm
from vmp import pairing_input as pi
from vmp import stand_adapters as sa
from vmp import stand_tasks as ST

PARAMS = {"base_tz_offset_min": 0, "max_duty_duration": 720, "max_pairing_days": 7,
          "min_rest_home": 660, "min_rest_outstation": 700, "min_turnaround": 45}


def _world(arr, params=None, conditions=None):
    w = {"crews": [{"base": "ST1", "id": "P1", "rank": "CP", "ratings": ["T1"]}],
         "flights": [{"id": "F1", "dep": "2026-10-01T06:00", "arr": "2026-10-01T" + arr,
                      "from": "ST1", "to": "ST2", "fleet_type": "T1"}],
         "form": "schedule", "history": None, "horizon": {"from": "2026-10-01", "to": "2026-10-31"},
         "params": dict(PARAMS, **(params or {})), "stand": {"w_night": 1}}
    if conditions:
        w["conditions"] = conditions
    return w


def _duty_ok(world):
    b = ST._builder(copy.deepcopy(world))
    chain, _dh = sa.chain_of(b, world)
    return b.duty_limit_ok(chain[0]["legs"])


def test_extension_within_cap_admissible_beyond_cap_not():
    cap = {"duty_extension_cap": 780, "long_duty_threshold": 720}
    assert _duty_ok(_world("18:30", cap)) is True          # 750: база 720 < 750 ≤ потолок 780
    assert _duty_ok(_world("19:00", cap)) is True          # ровно потолок
    assert _duty_ok(_world("19:01", cap)) is False         # 781 > потолка


def test_without_cap_limit_is_base():
    assert _duty_ok(_world("18:00")) is True               # 720 — база
    assert _duty_ok(_world("18:30")) is False              # 750 > базы, потолка нет


def test_addressed_cap_is_not_executed_by_pairing_builder():
    cap = {"duty_extension_cap": 780, "long_duty_threshold": 720}
    w = _world("18:30", cap, conditions={"extension_bases": ["ST1"], "extension_consent": "yes"})
    assert _duty_ok(w) is False                            # цепочка безымянна: предел — база


def test_builder_refuses_cap_below_base():
    payload, args = sa.world_to_builder(_world("18:00", {"duty_extension_cap": 700}))
    with pytest.raises(_pb.BuilderConfigError, match="ниже базового предела"):
        _pb.Builder(payload, args)


def test_extension_penalty_threshold_stays_base_with_cap():
    """Потолок не сдвигает порог штрафа: мера продления — от базы (смена 750 при базе 720 — единица)."""
    w = _world("18:30", {"duty_extension_cap": 780, "long_duty_threshold": 720})
    b = ST._builder(copy.deepcopy(w))
    chain, dh = sa.chain_of(b, w)
    row = next(r for r in ST._price(b, chain, dh)["soft"] if r["family"] == "duty_extension_penalty")
    assert row["measure"] == 1


def _args(policies, base=720):
    args, prov = ["--duty-span", str(base)], {"--duty-span": {"value": base, "from": "max_duty_span_min"}}
    pi._apply_duty_cap(policies, args, prov)
    return args, prov


def test_translator_free_policy_goes_as_duty_cap():
    args, prov = _args([{"rule": "R-S1-абз.16-1", "cap_min": 780, "extension_bases": None,
                         "extension_consent": False}])
    assert args[-2:] == ["--duty-cap", "780"]
    assert "R-S1-абз.16-1" in prov["--duty-cap"]["from"]


def test_translator_addressed_policy_is_named_not_executed():
    args, prov = _args([{"rule": "R-X", "cap_min": 780, "extension_bases": ["PLY"], "extension_consent": False}])
    assert "--duty-cap" not in args
    assert "R-X" in prov["--duty-span"]["note"] and "не исполняются" in prov["--duty-span"]["note"]


@pytest.mark.parametrize("policies, words", [
    ([{"rule": "A", "cap_min": 780}, {"rule": "B", "cap_min": 800}], "разными потолками"),
    ([{"rule": "A", "cap_min": 700}], "ниже базового предела"),
    ([{"rule": "A"}], "без целого потолка"),
    ([{"rule": "A", "cap_min": True}], "без целого потолка"),
    ({"rule": "A", "cap_min": 780}, "не список"),
])
def test_translator_refuses_ambiguous_or_broken_policies(policies, words):
    with pytest.raises(pi.PairingInputError, match=words):
        _args(policies)


def test_translator_without_policies_changes_nothing():
    assert _args(None)[0] == ["--duty-span", "720"] and _args([])[0] == ["--duty-span", "720"]


def test_execution_manifest_names_duty_cap_key():
    """Ведомость исполнения: потолок продления исполнен лишь ключом --duty-cap (прежде «исполненным» считался
    ключ веса штрафа --w-long-duty — ложное «исполняется»)."""
    assert xm.PARAM_EXECUTION["duty_extension_cap"]["cli"] == ("--duty-cap",)
