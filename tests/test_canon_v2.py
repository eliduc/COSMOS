"""Канон v2 (дорожная карта №2 документа формализации): новые сущности
qualifications / contracts / dutyHistory / costComponents — схема,
ссылочные проверки, обратная совместимость v1 и чтение канонических
квалификаций календарём соответствия."""
import pytest

from webapp import qualifications as q
from webapp import registry


def _v1():
    return {
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [
            {"id": "P1", "homeAirport": "AAA", "skills": ["Captain"]},
            {"id": "P2", "homeAirport": "AAA", "skills": ["Captain"]}],
        "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "BBB",
             "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "F2", "departureAirport": "BBB",
             "departureUTCDateTime": "2026-03-20T08:00:00",
             "arrivalAirport": "AAA",
             "arrivalUTCDateTime": "2026-03-20T10:00:00"}],
        "flightAssignments": [
            {"id": "a1", "flight": "F1", "indexInFlight": 0,
             "requiredSkill": "Captain"}],
    }


def _v2():
    p = _v1()
    p["formatVersion"] = "2"
    p["qualifications"] = [
        {"employee": "P1", "kind": "medical", "code": "Class-1",
         "validTo": "2026-03-15"},
        {"employee": "P2", "kind": "recency", "code": "3-ldg-90d",
         "requiredEvents": 3, "recentEvents": 1, "withinDays": 90}]
    p["contracts"] = [
        {"employee": "P1", "type": "lineholder",
         "monthlyGuaranteeHours": 75, "blockHourRate": 120}]
    p["dutyHistory"] = [
        {"employee": "P1", "month": "2026-02", "blockMinutes": 4200,
         "carryInMinutes": 90}]
    p["costComponents"] = [
        {"code": "block", "amount": 120, "unit": "per_block_hour"}]
    return p


# ------------------------------------------------------------ schema level

def test_v1_payload_still_valid():
    assert registry.validate_payload(_v1())["ok"] is True


def test_v2_payload_valid():
    out = registry.validate_payload(_v2())
    assert out["ok"] is True, out


def test_dated_qualification_requires_valid_to():
    p = _v2()
    del p["qualifications"][0]["validTo"]
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("qualifications" in e and "validTo" in e
               for e in out["errors"])


def test_recency_qualification_requires_counters():
    p = _v2()
    del p["qualifications"][1]["withinDays"]
    out = registry.validate_payload(p)
    assert out["ok"] is False


def test_dangling_employee_reference_rejected():
    for key, row in (
            ("qualifications", {"employee": "NOPE", "kind": "medical",
                                "code": "C", "validTo": "2026-03-10"}),
            ("contracts", {"employee": "NOPE"}),
            ("dutyHistory", {"employee": "NOPE", "month": "2026-02"})):
        p = _v2()
        p[key].append(row)
        out = registry.validate_payload(p)
        assert out["ok"] is False, key
        assert any("NOPE" in e and "not in employees" in e
                   for e in out["errors"]), (key, out["errors"])


def test_duplicate_contract_rejected():
    p = _v2()
    p["contracts"].append({"employee": "P1", "type": "reserve"})
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("duplicate contract" in e for e in out["errors"])


def test_duplicate_duty_history_rejected():
    p = _v2()
    p["dutyHistory"].append({"employee": "P1", "month": "2026-02"})
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("duplicate entry" in e for e in out["errors"])


def test_bad_month_and_unit_rejected():
    p = _v2()
    p["dutyHistory"][0]["month"] = "2026-13"
    out = registry.validate_payload(p)
    assert out["ok"] is False
    p = _v2()
    p["costComponents"][0]["unit"] = "per_smile"
    assert registry.validate_payload(p)["ok"] is False


# --------------------------------------- qualifications feature integration

def test_records_from_payload_maps_both_shapes():
    recs = q.records_from_payload(_v2())
    assert recs["P1"] == [{"kind": "medical", "code": "Class-1",
                           "expires": "2026-03-15"}]
    assert recs["P2"][0]["required_events"] == 3
    assert recs["P2"][0]["recent_events"] == 1
    assert recs["P2"][0]["within_days"] == 90


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "QUALS_PATH", tmp_path / "quals.json")
    yield tmp_path


def _fake_inst(monkeypatch, payload):
    monkeypatch.setattr(registry, "load_payload", lambda p: payload)
    return {"id": "canon-x", "payload_path": "data/none.json",
            "llm_scope": "local-only"}


def test_compliance_reads_canonical_when_store_empty(iso, monkeypatch):
    inst = _fake_inst(monkeypatch, _v2())
    comp = q.compliance("canon-x", inst)
    assert comp["records_source"] == "canonical"
    st = {r["employee_id"]: r for r in comp["employees"]}
    # month 2026-03-01..2026-03-20; medical valid through 03-15 -> grounded
    # from 03-16; recency 1/3 -> not current
    assert st["P1"]["status"] == "expires_during"
    assert st["P1"]["first_off"] == "2026-03-16"
    assert st["P2"]["status"] == "not_current"


def test_store_wins_over_canonical(iso, monkeypatch):
    inst = _fake_inst(monkeypatch, _v2())
    q.submit_records("canon-x", inst,
                     {"P1": [{"kind": "licence", "code": "ATPL",
                              "expires": "2027-01-01"}]})
    comp = q.compliance("canon-x", inst)
    assert comp["records_source"] == "store"
    st = {r["employee_id"]: r for r in comp["employees"]}
    assert st["P1"]["status"] == "ok"
    assert "P2" not in st                       # store fully replaces canon


def test_no_records_anywhere(iso, monkeypatch):
    inst = _fake_inst(monkeypatch, _v1())
    comp = q.compliance("canon-x", inst)
    assert comp["records_source"] == "none"
    assert comp["employees"] == []
