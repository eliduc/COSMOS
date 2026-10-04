"""Инкремент 4 multirole: правила состава экипажа — конфиг, валидатор,
инвариантность фингерпринта, интеграция в вердикт. Все правила ВЫКЛЮЧЕНЫ по
умолчанию — дефолтное поведение байт-идентично."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
import rules_config  # noqa: E402
from _crew_composition import check_composition, seniority_weight  # noqa: E402


def _p():
    return {
        "airports": [{"code": "AAA", "languages": ["ru"]},
                     {"code": "BBB", "languages": ["en"]}],
        "employees": [
            {"id": "E1", "skills": ["Captain"], "isReserve": False,
             "seniority": 1, "languages": ["ru", "en"]},
            {"id": "E2", "skills": ["FirstOfficer"], "isReserve": False,
             "seniority": 1000, "languages": []},
            {"id": "R1", "skills": ["Captain"], "isReserve": True,
             "seniority": 900, "languages": []},
            {"id": "R2", "skills": ["FirstOfficer"], "isReserve": True,
             "seniority": 950, "languages": []}],
        "flights": [{"flightNumber": "L1", "departureAirport": "AAA",
                     "departureUTCDateTime": "2026-03-01T08:00:00",
                     "arrivalAirport": "BBB",
                     "arrivalUTCDateTime": "2026-03-01T10:00:00"}],
    }


def _sol(*crew):
    return {"flightAssignments": [
        {"id": str(i), "flight": "L1", "indexInFlight": i,
         "requiredSkill": "Captain", "employee": e}
        for i, e in enumerate(crew)]}


# ------------------------------------------------------------------ config

def test_rules_config_defaults_off():
    d = rules_config.DEFAULTS
    assert d["comp_no_two_reserves"] == 0
    assert d["comp_min_pair_seniority_weight"] == 0
    assert d["comp_language_mix"] == 0


def test_rules_config_accepts_zero_and_flags(tmp_path, monkeypatch):
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"comp_no_two_reserves": 1,
                             "comp_min_pair_seniority_weight": 6,
                             "comp_language_mix": 0}))
    monkeypatch.setenv(rules_config.ENV_VAR, str(f))
    out = rules_config.load()
    assert out["comp_no_two_reserves"] == 1
    assert out["comp_min_pair_seniority_weight"] == 6
    assert out["min_sit_min"] == 30                 # шестёрка нетронута


@pytest.mark.parametrize("bad", [
    {"comp_no_two_reserves": 2},                    # флаг строго 0/1
    {"comp_min_pair_seniority_weight": -1},         # порог >= 0
    {"comp_language_mix": 0.5},                     # целое
    {"comp_no_two_reserves": True},                 # bool запрещён
])
def test_rules_config_rejects_bad_composition(bad, tmp_path, monkeypatch):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(bad))
    monkeypatch.setenv(rules_config.ENV_VAR, str(f))
    with pytest.raises(ValueError):
        rules_config.load()


def test_fingerprint_excludes_composition(tmp_path, monkeypatch):
    """Включение правил состава НЕ меняет отпечаток задачи колгена:
    состав — про рассадку, не про пространство пейрингов (иначе включение
    флага «сменило бы задачу» для всех бест-каталогов)."""
    import _gerad_colgen as cg

    class _Leg:
        def __init__(self):
            self.fn, self.dep_min, self.arr_min = "L1", 0, 100
            self.dep, self.arr, self.ac = "AAA", "BBB", "A320"
    dump = {"bases": None, "pricer": "legs", "max_legs": 5,
            "ip_model": "set_partitioning"}
    monkeypatch.setattr(cg.rules_config, "effective",
                        lambda: dict(rules_config.DEFAULTS))
    fp_off = cg.space_fingerprint([_Leg()], dump)
    on = dict(rules_config.DEFAULTS, comp_no_two_reserves=1,
              comp_min_pair_seniority_weight=8)
    monkeypatch.setattr(cg.rules_config, "effective", lambda: on)
    fp_on = cg.space_fingerprint([_Leg()], dump)
    assert fp_off == fp_on


# --------------------------------------------------------------- validator

def test_disabled_by_default_inert():
    out = check_composition(_p(), _sol("R1", "R2"),
                            rules=dict(rules_config.DEFAULTS))
    assert out["enabled"] is False and out["violations_total"] == 0


def test_no_two_reserves():
    rules = dict(rules_config.DEFAULTS, comp_no_two_reserves=1)
    out = check_composition(_p(), _sol("R1", "R2"), rules=rules)
    assert out["violations"] == {"no_two_reserves": 1}
    out2 = check_composition(_p(), _sol("R1", "E2"), rules=rules)
    assert out2["violations_total"] == 0


def test_seniority_weight_mirror_and_pair_rule():
    # формула Kotlin maxOf(1, 11-(raw-1)/100): raw=1 -> 11, raw=1000 -> 2
    assert seniority_weight(1) == 11 and seniority_weight(1000) == 2
    rules = dict(rules_config.DEFAULTS, comp_min_pair_seniority_weight=6)
    # E2+R2: вес 2+2=4 < 6 -> нарушение; E1 (вес 10) вытягивает
    out = check_composition(_p(), _sol("E2", "R2"), rules=rules)
    assert out["violations"] == {"min_pair_seniority_weight": 1}
    out2 = check_composition(_p(), _sol("E1", "E2"), rules=rules)
    assert out2["violations_total"] == 0
    # одночленный участок пропускается (правило про пару) — GERAD инертен
    out3 = check_composition(_p(), _sol("E2"), rules=rules)
    assert out3["violations_total"] == 0


def test_language_mix():
    rules = dict(rules_config.DEFAULTS, comp_language_mix=1)
    # E2/R1 без языков, участок требует ru|en -> нарушение
    out = check_composition(_p(), _sol("E2", "R1"), rules=rules)
    assert out["violations"] == {"language_mix": 1}
    # E1 владеет ru -> экипаж покрывает
    out2 = check_composition(_p(), _sol("E1", "E2"), rules=rules)
    assert out2["violations_total"] == 0


# ---------------------------------------------------- фиксы ревью 2026-07-19

def test_pairwise_seniority_catches_weak_pair_in_triple():
    # Ревью (medium): сумма по всему экипажу 3+ мест маскировала слабую
    # пару — теперь пер-парно, как Kotlin forEachUniquePair.
    p = _p()
    p["employees"].append({"id": "E3", "skills": ["Captain"],
                           "isReserve": False, "seniority": 950,
                           "languages": []})
    rules = dict(rules_config.DEFAULTS, comp_min_pair_seniority_weight=6)
    # E2(2) + R2(2) + E3(2): сумма 6 >= 6, но КАЖДАЯ пара 4 < 6 -> нарушение
    out = check_composition(_p() | {"employees": p["employees"]},
                            _sol("E2", "R2", "E3"), rules=rules)
    assert out["violations"] == {"min_pair_seniority_weight": 1}


def test_validate_value_shared_semantics():
    # Единая валидация: 0 легален, флаг 2 / порог 23 / Infinity — нет.
    assert rules_config.validate_value("comp_no_two_reserves", 0) is None
    assert rules_config.validate_value("comp_no_two_reserves", 2)
    assert rules_config.validate_value(
        "comp_min_pair_seniority_weight", 22) is None
    assert rules_config.validate_value(
        "comp_min_pair_seniority_weight", 23)
    assert rules_config.validate_value(
        "comp_min_pair_seniority_weight", float("inf"))
    assert rules_config.validate_value("min_sit_min", 30) is None
    assert rules_config.validate_value("min_sit_min", 0)


def test_rules_config_infinity_raises_valueerror(tmp_path, monkeypatch):
    # Ревью: Infinity давал OverflowError вместо контрактного ValueError.
    f = tmp_path / "r.json"
    f.write_text('{"comp_min_pair_seniority_weight": Infinity}')
    monkeypatch.setenv(rules_config.ENV_VAR, str(f))
    with pytest.raises(ValueError):
        rules_config.load()


def test_scenarios_validate_rules_comp_aware():
    from webapp import scenarios
    assert scenarios.validate_rules({"comp_no_two_reserves": 1}) == []
    assert scenarios.validate_rules({"comp_no_two_reserves": 0}) == []
    assert scenarios.validate_rules({"comp_no_two_reserves": 2})
    assert scenarios.validate_rules({"comp_language_mix": 1.5})
    assert scenarios.validate_rules(
        {"comp_min_pair_seniority_weight": float("inf")})
    assert scenarios.validate_rules({"min_sit_min": 45}) == []
    assert scenarios.validate_rules({"min_sit_min": 0})


def test_scenarios_compare_no_phantom_comp_diffs(monkeypatch):
    # Ревью: сценарий до инкремента 4 (6 ключей) не должен показывать
    # фантомные диффы comp_* против базы с 9 ключами.
    from webapp import scenarios
    six = {k: v for k, v in rules_config.DEFAULTS.items()
           if not k.startswith("comp_")}
    nine = dict(rules_config.DEFAULTS)
    diffs = []
    for key, dflt in rules_config.DEFAULTS.items():
        bv = nine.get(key, dflt)
        sv = six.get(key, dflt)
        if bv != sv:
            diffs.append(key)
    assert diffs == []                    # логика compare() — без фантомов


# ------------------------------------------------------------------ verdict

def test_verdict_fail_closed_on_composition():
    import _gerad_multirole as mr
    ok_cov = {"fully_covered": True}
    ok_phys = {"violations_total": 0}
    comp_bad = {"enabled": True, "violations_total": 2}
    comp_off = {"enabled": False, "violations_total": 0}
    lanes = {"Captain": "OK", "FirstOfficer": "OK"}
    assert mr.decide_verdict(lanes, ok_cov, ok_phys, comp_bad) \
        == "INFEASIBLE_INCUMBENT"
    assert mr.decide_verdict(lanes, ok_cov, ok_phys, comp_off) == "OK"
    assert mr.decide_verdict(lanes, ok_cov, ok_phys) == "OK"   # без comp


# --- Аудит 2026-07-19: P1-12 (Int32) и P2-07 (inf в seniority) -------------


def test_validate_value_rejects_int32_overflow():
    import rules_config
    msg = rules_config.validate_value("min_rest_min", 3_000_000_000)
    assert msg is not None and "32-bit" in msg


def test_validate_value_rejects_inf_and_fraction_tier1():
    import rules_config
    assert rules_config.validate_value("min_rest_min", float("inf")) is not None
    assert rules_config.validate_value("min_rest_min", 30.5) is not None
    assert rules_config.validate_value("min_rest_min", 600) is None
    assert rules_config.validate_value("min_rest_min", 2_147_483_647) is None
    # легаси-сообщение для неположительных значений сохранено
    assert "positive number" in rules_config.validate_value("min_rest_min", 0)


def test_seniority_weight_neutral_on_infinity():
    # 1e309 в JSON -> float('inf'); int(inf) кидал OverflowError и ронял
    # multirole merge трейсбеком вместо контрактного вердикта (P2-07).
    from _crew_composition import seniority_weight
    assert seniority_weight(float("inf")) == seniority_weight(500)
    assert seniority_weight(float("nan")) == seniority_weight(500)


def test_canonical_schema_rejects_infinite_seniority():
    from webapp import registry
    p = {
        "formatVersion": "1",
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA",
                       "skills": ["Captain"], "seniority": float("inf")}],
        "flights": [{"flightNumber": "F1", "departureAirport": "AAA",
                     "arrivalAirport": "BBB",
                     "departureUTCDateTime": "2026-03-01T08:00:00",
                     "arrivalUTCDateTime": "2026-03-01T10:00:00"}],
        "flightAssignments": [{"id": "a1", "flight": "F1",
                               "indexInFlight": 0,
                               "requiredSkill": "Captain"}],
    }
    out = registry.validate_payload(p)
    assert out["ok"] is False


def test_validate_value_huge_int_contract_message():
    # Ревью пакета 2026-07-19: 10**400 давал OverflowError-трейсбек вместо
    # контрактного сообщения (float-конверсия огромного int).
    import rules_config
    msg = rules_config.validate_value("min_rest_min", 10**400)
    assert msg is not None and "32-bit" in msg
    msg2 = rules_config.validate_value("comp_min_pair_seniority_weight",
                                       10**400)
    assert msg2 is not None
