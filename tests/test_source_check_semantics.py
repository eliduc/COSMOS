# -*- coding: utf-8 -*-
"""Source-only semantic guard: no live reader, VMP, solver or catalogue.

VN-050 text is the synthetic frozen H0-B S2 row 5, not candidate VMP.
The reader's wrong answer is replayed as data, never as a live call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp import llm_backend, source_check_compile as scc
from vmp import source_check as sc, source_check_run as scr


VN050 = {
    "sid": "S2", "anchor": "лист «VMP-H0-2», строка 5",
    "text": "VN-050. Разброс кредитных часов. Штраф 3 условные единицы за "
            "каждый кредитный час разброса сверх порога существенности 10 "
            "кредитных часов за месяц. Шаблон: balanced_workload (категория A); "
            "носитель нормы: «Штраф 3 условные единицы за каждый кредитный час "
            "разброса сверх порога существенности 10 кредитных часов за месяц». "
            "Параметры: workload_spread_threshold = 10 credit_hours "
            "(«сверх порога существенности 10 кредитных часов за месяц»). "
            "Штраф: 3 за час.",
}
OLD_FINGERPRINT = "7707499fc76b92e9b650aa09e252e90db7b3fe95137bda901cd9d9bd592fe16b"


def _row(event="duty_over_threshold", threshold=600, **fields):
    row = {"id": "untrusted-reader-id", "anchor": "S1:p1",
           "kind": "soft_event_count",
           "params": {"event": event, "threshold_min": threshold},
           "note": "Untrusted reader explanation is not source evidence."}
    row.update(fields)
    return row


@pytest.fixture(autouse=True)
def no_live_reader(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("live model/transport is forbidden")
    for name in ("complete", "_api_generate", "_cli_generate", "_ollama_generate"):
        monkeypatch.setattr(llm_backend, name, forbidden)


def _compile(monkeypatch, text, row=None, clauses=None):
    row = row or _row()
    clauses = clauses or [{"sid": "S1", "anchor": "p1", "text": text}]
    def fake(prompt, **kwargs):
        assert text in prompt
        return {"ok": True, "text": json.dumps([row], ensure_ascii=False),
                "backend": "cloud", "model": "test-reader"}
    monkeypatch.setattr(llm_backend, "complete", fake)
    return scc.compile_checks(clauses, scope="any", requested="cloud")


@pytest.mark.parametrize("event", sc.SOFT_EVENTS)
def test_frozen_vn050_cannot_be_any_supported_event(monkeypatch, event):
    row = _row(event, id="VN-050-credit-spread",
               anchor=f"S2:{VN050['anchor']}")
    got = _compile(monkeypatch, VN050["text"], row, [VN050])
    spec, = got["specs"]
    assert spec["id"] == row["id"] and spec["anchor"] == row["anchor"]
    assert spec["kind"] == "not_machine_checkable"
    assert "кредитные часы" in spec["reason"]
    assert len(got["problems"]) == 1
    empty = {"flights": [], "employees": [], "flightAssignments": []}
    result, = sc.run_checks([spec], empty)
    assert result.verdict == "not_checkable" and result.objects_seen == 0
    protocol = sc.render_protocol([result])
    assert "МАШИННО НЕ ПРОВЕРЯЕТСЯ" in protocol
    assert "фактов события" not in protocol


@pytest.mark.parametrize("text,threshold", [
    ("Базовый предел полётной смены — 12:00 (720 минут); превышение — "
     "только продлением по правилу VN-011.", 720),
    ("Штраф 3 единицы за смену свыше 10 часов.", 600),
    ("Штраф за смену свыше 600 минут.", 600),
    ("Штраф за смену свыше 12,5 часа.", 750),
    ("Штраф за смену свыше 12 ч 30 мин.", 750),
    ("Penalty for a duty exceeding 12.5 hours.", 750),
    ("Penalty for a duty exceeding 12 hours 30 minutes.", 750),
    ("Penalty for a duty exceeding 600 minutes.", 600),
])
def test_supported_duty_duration_units_remain_usable(monkeypatch, text, threshold):
    got = _compile(monkeypatch, text, _row(threshold=threshold))
    spec, = got["specs"]
    assert spec["kind"] == "soft_event_count" and not got["problems"]
    assert spec["params"]["threshold_min"] == threshold
    # Actual deterministic executor: a 14-hour duty is one excess event.
    solution = {
        "flights": [{"flightNumber": "F1", "departureAirport": "AAA",
                     "arrivalAirport": "BBB", "departureUTCDateTime": "2026-09-01T00:00:00",
                     "arrivalUTCDateTime": "2026-09-01T14:00:00"}],
        "employees": [{"id": "E1", "homeAirport": "AAA"}],
        "flightAssignments": [{"flight": "F1", "employee": "E1"}],
    }
    result, = sc.run_checks([spec], solution, rest_boundary={
        "rest_boundary_min": 600, "rest_boundary_basis": "knob",
        "rest_boundary_source": "synthetic test"})
    assert result.verdict == "soft_info" and result.objects_seen == 1


@pytest.mark.parametrize("text,event,days", [
    ("Штраф 10 единиц за каждую ночёвку вне базы приписки.", "layover_nights", None),
    ("Penalty for each layover night away from home base.", "layover_nights", None),
    ("Надбавка 25 за ночёвку вне базы, день начала — пятница или суббота.",
     "layover_nights_in_window", ["friday", "saturday"]),
    ("Penalty for overnight outside base on weekends.",
     "layover_nights_in_window", ["saturday", "sunday"]),
])
def test_supported_overnight_events(monkeypatch, text, event, days):
    row = _row(event, params={"event": event})
    if days:
        row["params"]["days"] = days
    got = _compile(monkeypatch, text, row)
    assert got["specs"][0]["kind"] == "soft_event_count"
    assert not got["problems"]


@pytest.mark.parametrize("text,row", [
    ("Penalty for monthly credit-hour spread above 10 hours.", _row()),
    ("Штраф за разброс смен свыше 10 часов.", _row()),
    ("Штраф за месячную нагрузку: смены свыше 10 часов.", _row()),
    ("Штраф 600 единиц за смену. Отдых не менее 10 часов.", _row()),
    ("Штраф за полёт свыше 10 часов.", _row()),
    ("Штраф за смену свыше 10 часов.", _row(threshold=10)),
    ("Штраф за смену свыше 12 ч 30 мин.", _row(threshold=30)),
    ("Штраф за смену свыше 10 часов.", _row(threshold=True)),
    ("Штраф за смену свыше 10 часов.", _row(threshold="600")),
    ("Штраф за смену свыше -10:00.", _row()),
    ("Штраф за смену свыше 10 часов.", _row(params=[])),
    ("Штраф за смену свыше 10 часов.", _row("invented")),
    ("Штраф за смену свыше 10 часов.", _row("layover_nights")),
    ("Штраф за ночевку дома.", _row("layover_nights")),
    ("Штраф за ночевку вне базы в пятницу.", _row("layover_nights")),
    ("Штраф за ночевку вне базы в пятницу.",
     _row(params={"event": "layover_nights_in_window", "days": ["saturday"]})),
    ("Штраф за ночевку вне базы.", _row("layover_nights_in_window")),
])
def test_wrong_domain_units_or_window_fail_closed(monkeypatch, text, row):
    got = _compile(monkeypatch, text, row)
    assert got["specs"][0]["kind"] == "not_machine_checkable"
    assert got["specs"][0]["reason"] and got["problems"]


@pytest.mark.parametrize("anchor,sid,allowed", [
    ("S1:p1", None, True), ("p1", "S1", True),
    ("p1", None, False), ("unknown", None, False), ("S1:p1", "S2", False),
])
def test_anchor_resolution_never_borrows_another_clause(monkeypatch, anchor, sid, allowed):
    text = "Штраф за смену свыше 10 часов."
    clauses = [{"sid": "S1", "anchor": "p1", "text": text},
               {"sid": "S2", "anchor": "p1", "text": VN050["text"]}]
    row = _row(anchor=anchor, note=text, id="VN-010-duty-over-threshold")
    if sid is not None:
        row["sid"] = sid
    got = _compile(monkeypatch, text, row, clauses)
    assert (got["specs"][0]["kind"] == "soft_event_count") is allowed


def test_reader_note_and_id_cannot_override_credit_source(monkeypatch):
    text = "Штраф за кредитные часы сверх 10 часов за месяц."
    row = _row(id="VN-010-duty-over-threshold", note="Штраф за смену свыше 10 часов.")
    assert _compile(monkeypatch, text, row)["specs"][0]["kind"] == "not_machine_checkable"


def test_unique_legacy_bare_anchor_remains_usable(monkeypatch):
    got = _compile(monkeypatch, "Штраф за смену свыше 10 часов.", _row(anchor="p1"))
    assert got["specs"][0]["kind"] == "soft_event_count"


def test_compiler_and_guard_versions_invalidate_fingerprint(monkeypatch):
    assert scc.COMPILER_VERSION != "2026-09-06.1"
    current = scc.compiler_fingerprint()
    assert current != OLD_FINGERPRINT
    monkeypatch.setattr(scc, "SOFT_EVENT_GUARD_VERSION", "test-next-guard")
    assert scc.compiler_fingerprint() != current


def test_old_wrong_cache_remains_but_is_not_used(tmp_path, monkeypatch):
    import _data_paths
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    wrong = _row(anchor=f"S2:{VN050['anchor']}")
    old_key = scr._compile_key([VN050], "any", "cloud", "test-reader", "medium",
                              channel=None, backend="cloud", fingerprint=OLD_FINGERPRINT)
    cache = tmp_path / "source-check" / "compiled"
    cache.mkdir(parents=True)
    old = cache / f"{old_key}.json"
    old.write_text(json.dumps({"key": old_key, "specs": [wrong], "channel": None,
                               "compiler_version": "2026-09-06.1",
                               "fingerprint": OLD_FINGERPRINT}), encoding="utf-8")
    original = old.read_bytes()
    calls = []
    def fake(prompt, **kwargs):
        calls.append(prompt)
        return {"ok": True, "text": json.dumps([wrong], ensure_ascii=False),
                "backend": "cloud", "model": "test-reader"}
    monkeypatch.setattr(llm_backend, "complete", fake)
    def run():
        return scr._cached_compile([VN050], scope="any", requested="cloud",
                                   model="test-reader", timeout_s=1, effort="medium")
    first, second = run(), run()
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1
    assert first["specs"] == second["specs"]
    assert first["specs"][0]["kind"] == "not_machine_checkable"
    assert old.read_bytes() == original
    assert len(list(cache.glob("*.json"))) == 2
