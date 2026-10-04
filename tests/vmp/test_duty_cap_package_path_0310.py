# -*- coding: utf-8 -*-
"""Потолок продления смены на ПУТИ ПАКЕТА ВМП (Stage 03.10, РВ-98/107).

Мера 100 (6d1998a3) подала потолок построителю ключом --duty-cap, но в
профиле пакета ВМП предел смены судится контрактом правил
(source-rules.json, work_time_limit_by_tier), и контракт нёс базу. Итог
на Stage: продление, разрешённое правилом, терялось (Н0: 12 цепочек
вместо 9). Здесь — контракт с потолком и профиль построителя, где ключ
--duty-cap сверяется с контрактом, а база --duty-span остаётся порогом
штрафа продления.
"""
import json

import pytest

import _pairing_builder as _pb
from vmp import pairing_input as pi
from tests.test_pairing_builder import _leg, _payload


_APPLIED = {"max_duty_span_min": 720, "min_rest_home_min": 720,
            "min_rest_outstation_min": 600, "min_sit_min": 30}


def test_contract_carries_cap_as_work_time_limit():
    c = pi.source_rules_contract(None, dict(_APPLIED), duty_cap=780)
    assert c["work_time_limit_by_tier"] == [780]


def test_contract_without_cap_keeps_base():
    c = pi.source_rules_contract(None, dict(_APPLIED))
    assert c["work_time_limit_by_tier"] == [720]


@pytest.mark.parametrize("cap", [720, 700, True, "780", None])
def test_contract_ignores_cap_not_above_base_or_not_int(cap):
    c = pi.source_rules_contract(None, dict(_APPLIED), duty_cap=cap)
    assert c["work_time_limit_by_tier"] == [720]


def _world():
    """Одна смена BASE→AAA→BASE: участки 350 + 355 мин, стыковка 45 —
    рабочее время 750 мин (брифинга в этом мире нет): длиннее базы 720,
    не длиннее потолка 780. Без продления обратного участка в тот же
    день нет — его не покрыть."""
    return _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 350),
        _leg("F2", "AAA", "BASE", "2026-08-01T12:35:00", 355)])


def _run(tmp_path, monkeypatch, limit, *extra):
    import contextlib
    import hashlib
    import io
    bad_cfg = tmp_path / "airline-broken.json"
    bad_cfg.write_text("{ это не json", encoding="utf-8")
    monkeypatch.setenv("CREW_RULES_CONFIG", str(bad_cfg))
    contract = {"min_sit_min": 30, "work_time_limit_by_tier": [limit],
                "layover_tiers": [[1440, 720, 600, 720, 600, 720, 600]],
                "base_tz_offset_min": 0, "max_pairing_days": 3,
                "max_landings_per_duty": 2,
                "dh_board_after_debrief_min": 0, "trans_tz_hours": 99.0}
    raw = json.dumps(contract).encode("utf-8")
    rules_p = tmp_path / "source-rules.json"
    rules_p.write_bytes(raw)
    payload_p = tmp_path / "payload.json"
    payload_p.write_text(json.dumps(_world()), encoding="utf-8")
    out = tmp_path / "cat.json"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _pb.main([
            "--payload", str(payload_p), "--label", "cap", "--level", "3",
            "--bases", "BASE", "--budget-s", "5", "--out", str(out),
            "--method", "enumerate", "--dh-options", "0",
            "--rescue-dh", "0", "--rescue-per-leg", "0",
            "--rest-base", "720", "--rest-base-long", "720",
            "--rest-off", "600", "--rest-off-long", "600",
            "--vmp-package", "--source-rules", str(rules_p),
            "--source-rules-fp", hashlib.sha256(raw).hexdigest(), *extra])
    doc = None
    for p in (out, out.with_name(out.stem + ".pending.json")):
        if p.exists():
            doc = json.loads(p.read_text(encoding="utf-8"))
            break
    return rc, buf.getvalue(), doc


def test_package_profile_cap_admits_extended_duty(tmp_path, monkeypatch):
    rc, out, doc = _run(tmp_path, monkeypatch, 780,
                        "--duty-span", "720", "--duty-cap", "780",
                        "--w-long-duty", "15", "--long-duty-threshold", "720")
    assert "ОТКАЗ" not in out, out
    assert doc is not None, out
    assert doc["coverage"]["uncovered"] == 0, (rc, out)
    sig = doc["config_sig"]
    assert sig["duty_span"] == 720, sig          # база — порог штрафа
    assert sig["duty_cap"] == 780, sig
    assert sig["rules_effective"]["max_duty_span_min"] == 780, sig


def test_package_profile_base_only_loses_extension(tmp_path, monkeypatch):
    rc, out, doc = _run(tmp_path, monkeypatch, 720, "--duty-span", "720")
    assert "ОТКАЗ" not in out, out
    assert doc is not None, out
    assert doc["coverage"]["uncovered"] > 0, (rc, out)


def test_package_profile_cap_against_base_contract_is_two_truths(
        tmp_path, monkeypatch):
    rc, out, _ = _run(tmp_path, monkeypatch, 720,
                      "--duty-span", "720", "--duty-cap", "780")
    assert rc == 2 and "две правды" in out, out


def test_package_profile_base_key_against_cap_contract_is_two_truths(
        tmp_path, monkeypatch):
    rc, out, _ = _run(tmp_path, monkeypatch, 780, "--duty-span", "720")
    assert rc == 2 and "две правды" in out, out
