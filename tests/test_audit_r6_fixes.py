# -*- coding: utf-8 -*-
"""Негативные тесты исправлений внешнего аудита R6 (2026-07-30).

Каждый тест воспроизводит мутацию, которой не хватало штатной батарее
(раздел 8.2 аудита): повторный номер рейса, смещение зоны, ослабленное
покрытие, чужой пакет правил, кратность в круговой сверке, подмена файла
правил источника."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------- P0-03: время

def test_parse_utc_recalculates_offset_instead_of_truncating():
    from _canonical_time import parse_utc
    assert parse_utc("2026-08-01T10:00:00+02:00") == \
        datetime(2026, 8, 1, 8, 0, 0)
    assert parse_utc("2026-08-01T10:00:00Z") == \
        datetime(2026, 8, 1, 10, 0, 0)
    assert parse_utc("2026-08-01T10:00:00") == \
        datetime(2026, 8, 1, 10, 0, 0)


def test_parse_utc_rejects_garbage_and_fractions():
    from _canonical_time import parse_utc
    with pytest.raises(ValueError):
        parse_utc("not-a-date")
    with pytest.raises(ValueError):
        parse_utc("2026-08-01T10:00:00.500")


def test_registry_rejects_reversed_chronology():
    from webapp import registry
    payload = {
        "formatVersion": "1",
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA"}],
        "flights": [{"flightNumber": "F1", "departureAirport": "AAA",
                     "departureUTCDateTime": "2026-08-01T10:00:00",
                     "arrivalAirport": "BBB",
                     "arrivalUTCDateTime": "2026-08-01T08:00:00"}],
        "flightAssignments": [],
    }
    res = registry.validate_payload(payload)
    assert res["ok"] is False
    assert any("прилёт" in e for e in res["errors"])


def test_registry_rejects_malformed_time():
    from webapp import registry
    payload = {
        "formatVersion": "1",
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA"}],
        "flights": [{"flightNumber": "F1", "departureAirport": "AAA",
                     "departureUTCDateTime": "not-a-date",
                     "arrivalAirport": "BBB",
                     "arrivalUTCDateTime": "also-bad"}],
        "flightAssignments": [],
    }
    res = registry.validate_payload(payload)
    assert res["ok"] is False


# ------------------------------------------- P0-01: идентичность участка

def _dup_payload():
    """Два реальных участка под одним flightNumber, различимых только
    flightLegId, — синтетика из воспроизведения аудита."""
    return {
        "formatVersion": "1",
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA"}],
        "flights": [
            {"flightNumber": "F100", "flightLegId": "F100|a",
             "departureAirport": "AAA",
             "departureUTCDateTime": "2026-08-01T08:00:00",
             "arrivalAirport": "BBB",
             "arrivalUTCDateTime": "2026-08-01T10:00:00"},
            {"flightNumber": "F100", "flightLegId": "F100|b",
             "departureAirport": "BBB",
             "departureUTCDateTime": "2026-08-02T08:00:00",
             "arrivalAirport": "AAA",
             "arrivalUTCDateTime": "2026-08-02T10:00:00"},
        ],
        "flightAssignments": [
            {"flight": "F100", "requiredSkill": "CA", "indexInFlight": 0,
             "flightLegId": "F100|a"},
            {"flight": "F100", "requiredSkill": "CA", "indexInFlight": 0,
             "flightLegId": "F100|b"},
        ],
    }


def test_rekey_preserves_both_legs_with_duplicate_flight_number():
    from _leg_rekey import rekey_or_passthrough
    payload, wire_map = rekey_or_passthrough(_dup_payload())
    fns = [f["flightNumber"] for f in payload["flights"]]
    assert len(set(fns)) == 2, "оба участка обязаны выжить под разными именами"
    assert len(wire_map) == 2


def test_rekey_passthrough_keeps_object_identity_without_duplicates():
    from _leg_rekey import rekey_or_passthrough
    p = _dup_payload()
    p["flights"][1]["flightNumber"] = "F101"
    p["flightAssignments"][1]["flight"] = "F101"
    out, wire_map = rekey_or_passthrough(p)
    assert out is p and wire_map == {}


# ------------------------------------------------- P0-05: честное покрытие

def test_stage2_gate_refuses_relaxed_cover(tmp_path):
    import _gerad_stage2_roster as st2
    doc = {"coverage": {"verdict": "RELAXED_COVER", "uncovered": 0},
           "selected": []}
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(st2.RulesMismatch):
        st2._assert_publishable(doc, p)


def test_stage2_gate_refuses_unknown_verdict(tmp_path):
    import _gerad_stage2_roster as st2
    doc = {"coverage": {"verdict": "TOTALLY_NEW"}, "selected": []}
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(st2.RulesMismatch):
        st2._assert_publishable(doc, p)


# --------------------------------------- P0-02: подмена файла правил

def test_builder_refuses_source_rules_fingerprint_mismatch(tmp_path):
    import _pairing_builder as pb
    rules_p = tmp_path / "source_rules.json"
    rules_p.write_text(json.dumps({"parameters": {}}), encoding="utf-8")
    payload_p = tmp_path / "payload.json"
    payload_p.write_text(json.dumps(_dup_payload()), encoding="utf-8")
    rc = pb.main(["--payload", str(payload_p), "--label", "t",
                  "--level", "3", "--source-rules", str(rules_p),
                  "--source-rules-fp", "0" * 64,
                  "--out", str(tmp_path / "out.json")])
    assert rc == 2


# ------------------------------------------------ P1-03: круговая сверка

def test_round_trip_counts_multiplicity(tmp_path, monkeypatch):
    """Две одинаковые командировки не должны схлопываться в одну."""
    import _pairing_to_solver as pts

    def fake_read_back(doc, name, check_name):
        unit = {"active_fns": ["X1"], "fns": ["X1"], "kind": "chain"}
        return None, None, [], [unit]           # вернулась ОДНА
    monkeypatch.setattr("_solver_to_payload.convert", fake_read_back)
    out = tmp_path / "f5.json"
    out.write_text("{}", encoding="utf-8")
    catalog = {"selected": [
        {"active_fns": ["X1"], "fns": ["X1"]},
        {"active_fns": ["X1"], "fns": ["X1"]},   # дубль
    ]}
    rt = pts.verify_round_trip(out, catalog)
    assert rt["потеряно"] == 1, "кратность обязана считаться"


# ----------------------------------------------- P0-06: чужой пакет правил

def test_rule_pack_visibility_helper():
    from webapp import app as webapp_app
    user = {"email": "user@example.com", "role": "user"}
    admin = {"email": "root@example.com", "role": "admin"}
    assert webapp_app._rule_pack_visible("regulatory", user) is True
    assert webapp_app._rule_pack_visible("instance:no-such-inst", user) \
        is False
    assert webapp_app._rule_pack_visible("instance:no-such-inst", admin) \
        is True
