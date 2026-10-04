"""Whole rejection evidence must survive collection, persistence and replay."""
import copy
import json

import pytest

from cdpm import exchange as ex, model as m
from cdpm.sample import sample_model
from test_cdpm_exchange import _pack, _remark
from test_cdpm_coverage_gap import _doc_with_gap


NIGHT = {
    "max_consecutive_night_duties": (2, "не более 2 ночных смен подряд"),
    "max_night_duties_in_window": (8, "не более 8 ночных смен в окне"),
    "night_duty_end": (360, "конец ночного окна 06:00"),
    "night_duty_start": (1320, "начало ночного окна 22:00"),
    "night_window_days": (14, "окно 14 суток"),
}


def _quote_case(count):
    doc = sample_model()
    rule = next(r for r in doc["rules"] if r["kind"] == "HARD")
    rule["zone"] = "ROSTERING"
    rule["template_fit"] = {
        "category": "A", "family": "night_series", "registry_rev": m.registry_fingerprint(),
        "params": {name: {"value": value, "quote": quote} for name, (value, quote) in NIGHT.items()}}
    # Real catalogue names, valid schema/model, and genuine quotation controls.
    assert set(NIGHT) == set(m.template_families()["night_series"])
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    clauses = {(s["sid"], s["anchor"]): "; ".join(q for _, q in NIGHT.values())
               for s in rule["sources"]}
    assert m.validate_fit_quotes({"rules": [rule]}, clauses) == []
    changed = copy.deepcopy(rule)
    changed["statement"] += " Уточнение формулировки."
    for name in list(NIGHT)[:count]:
        changed["template_fit"]["params"][name]["quote"] = "выдуманная цитата " + name
    expected = m.validate_fit_quotes({"rules": [changed]}, clauses)
    assert len(expected) == count
    pack = _pack(_remark(target={"rule": rule["id"]}, source_refs=copy.deepcopy(rule["sources"])))
    resp = {"round": 1, "answers": [{"remark": "A-001", "verdict": "ПРИНЯТО",
        "basis": "Уточнение по источнику", "patch": {"rule_id": rule["id"], "rule": changed}}]}
    return doc, pack, resp, clauses, expected


@pytest.mark.parametrize("count", [0, 1, 2, 3, 5])
def test_real_quote_reasons_survive_round_registry_feedback_and_replay(count):
    doc, pack, resp, clauses, expected = _quote_case(count)
    original = copy.deepcopy(doc)
    run = ex.apply_round_pure(doc, {"findings": []}, pack, copy.deepcopy(resp), 1,
                              context={"clause_texts": clauses})
    assert doc == original
    if not count:
        assert run["rejections"] == [] and run["executed"] == {"A-001"}
        assert run["model"] != doc
        return
    assert len(run["rejections"]) == 1
    assert run["rejections"][0]["reasons"] == expected
    assert run["model"] == doc and run["executed"] == set()
    reason = run["registry"]["findings"][0]["patch_reason"]
    assert reason == "; ".join(expected)
    assert reason in ex._registry_listings(run["registry"])[2]
    # Replay consumes the same full persisted list and yields the same registry.
    replay_registry = {"findings": []}
    ex.registry_update(replay_registry, 1, pack, resp,
                       patch_rejected=json.loads(json.dumps(run["rejections"])))
    # registry_post_round adds a separate touch ledger; compare the replayed findings.
    assert replay_registry["findings"] == run["registry"]["findings"]


@pytest.mark.parametrize("count", [1, 2, 3, 8, 20])
def test_long_reasons_are_preserved_on_new_and_repeated_registry_entries(count):
    reasons = [f"R-{i:02}: параметр 'предел_{i}' не подтверждён дословной цитатой источника; "
               f"это полная причина номер {i}." for i in range(count)]
    pack = _pack(_remark())
    resp = {"answers": [{"remark": "A-001", "verdict": "ПРИНЯТО", "basis": "основание"}]}
    reg = {"findings": []}
    for round_no in (1, 2):
        ex.registry_update(reg, round_no, pack, resp,
            patch_rejected=[{"answer": "A-001", "reasons": reasons}])
        assert len(reg["findings"]) == 1
        finding = reg["findings"][0]
        assert finding["state"] == "патч отбракован"
        assert finding["patch_reason"] == "; ".join(reasons)


@pytest.mark.parametrize("count", [1, 4, 8, 9, 20])
def test_all_parts_of_one_rejection_are_retained(count, monkeypatch):
    reasons = [f"часть {i}: причина целиком" for i in range(count)]
    # Isolate the aggregation boundary; actual quotation validation is exercised above.
    def reject(*args, **kwargs):
        return {"rejections": [{"reasons": reasons[:2]}, {"reasons": reasons[2:]}],
                "touch_targets": set(), "touch_tokens": []}
    monkeypatch.setattr(ex, "_apply_one_answer", reject)
    doc = sample_model()
    before = copy.deepcopy(doc)
    got, accepted, rejected, tokens = ex._apply_patches(doc, _pack(_remark()),
        {"answers": [{"remark": "A-001", "verdict": "ПРИНЯТО"}]}, 1)
    assert rejected == [{"answer": "A-001", "reasons": reasons}]
    assert got == before and doc == before and not accepted and not tokens


@pytest.mark.parametrize("count", [1, 3, 4, 5, 9, 20])
def test_coverage_label_states_the_number_of_omitted_rules(count):
    doc, rule = _doc_with_gap()
    doc["rules"].remove(rule)
    for i in range(count):
        item = copy.deepcopy(rule)
        item["id"] = f"R-GAP-{i:02}"
        doc["rules"].append(item)
    before = copy.deepcopy(doc)
    got = m.readiness(doc)
    assert doc == before
    assert not got["rostering_ready"] and got["pairing_ready"]
    assert got["rostering_open"][m.COVERAGE_GAP_TAG] == count
    names = ", ".join(f"R-GAP-{i:02}" for i in range(min(count, 4)))
    expected = ("Данных достаточно для пейринга; для ростеринга — недостаточно; "
                "у правил нет шаблона в каталоге (" + names)
    if count > 4:
        assert f"и ещё {count - 4}" in got["label"]
    else:
        assert got["label"] == expected + ") — пакет не выдаётся до выпуска шаблона"


@pytest.mark.parametrize("stage", ["integrity", "origin", "schema", "model"])
@pytest.mark.parametrize("count", [0, 1, 4, 6, 9])
def test_validator_reason_lists_are_not_cut_by_the_receiver(stage, count, monkeypatch):
    doc, pack, resp, clauses, _ = _quote_case(0)
    expected = [f"R-1: причина {stage} номер {i} целиком" for i in range(count)]
    if stage == "integrity":
        monkeypatch.setattr(ex, "fit_integrity_problems", lambda *a, **k: expected)
    elif stage == "origin":
        from cdpm import provenance
        monkeypatch.setattr(provenance, "patch_problems", lambda *a, **k: expected)
    else:
        monkeypatch.setattr(m, "validate_" + stage, lambda *a, **k: expected)
    got, targets, rejected, _ = ex._apply_patches(doc, pack, resp, 1, clause_texts=clauses)
    if count:
        assert rejected == [{"answer": "A-001", "reasons": expected}]
        assert got == doc and not targets
    else:
        assert rejected == [] and targets and got != doc


@pytest.mark.parametrize("state,index", [("отклонено", 0), ("арбитр:принято", 1),
                                        ("патч отбракован", 2)])
@pytest.mark.parametrize("count", [0, 1, 30, 31, 40])
def test_registry_listings_keep_tail_order_and_name_the_omitted_count(state, index, count):
    reg = {"findings": [{"key": f"K-{i:03}", "state": state, "text": f"Текст {i}.",
                        "patch_reason": "Причина целиком."} for i in range(count)]}
    before = copy.deepcopy(reg)
    listing = ex._registry_listings(reg)[index]
    assert reg == before
    if not count:
        assert listing == "(нет)"
    else:
        assert f"K-{count - 1:03}" in listing
        assert len([line for line in listing.splitlines() if line.startswith("[K-")]) == min(count, 30)
        if count > 30:
            assert f"и ещё {count - 30}" in listing and "показаны последние 30" in listing
            assert "K-000" not in listing
        else:
            assert "и ещё" not in listing


def test_summary_keeps_complete_items_and_input_untouched():
    from cdpm.diagnostics import summarize_items
    values = ["длинная причина " * 30, "вторая", "третья"]
    before = values[:]
    assert summarize_items(values, limit=1) == values[0] + " (и ещё 2)"
    assert summarize_items(values, limit=3, separator="; ") == "; ".join(values)
    assert summarize_items([], limit=1) == ""
    assert values == before


@pytest.mark.parametrize("stage", ["integrity", "patch_origin", "rule_origin"])
@pytest.mark.parametrize("count", [0, 1, 3, 5])
def test_worker_answer_receiver_keeps_all_rejection_reasons(stage, count, monkeypatch):
    from cdpm import answers, provenance
    from test_cdpm_a03m_provenance import _doc_with_open_question, _rule, _worker_reply
    doc = _doc_with_open_question()
    original_rule = copy.deepcopy(_rule(doc))
    candidate = copy.deepcopy(original_rule)
    candidate.pop("blockers", None)
    expected = [f"R-R: причина {stage} номер {i} целиком" for i in range(count)]
    if stage == "integrity":
        monkeypatch.setattr(ex, "fit_integrity_problems", lambda *a, **k: expected)
    elif stage == "patch_origin":
        monkeypatch.setattr(provenance, "patch_problems", lambda *a, **k: expected)
    else:
        monkeypatch.setattr(provenance, "rule_problems", lambda *a, **k: expected)
    summary = answers.apply_worker_patches(doc,
        lambda prompt: _worker_reply("Q-3b-уточнение", [candidate]))
    if count:
        assert summary["worker_applied"] == 0
        assert "; ".join(expected) in summary["worker_rejected"][0]["reasons"]
        assert _rule(doc) == original_rule
    else:
        assert summary["worker_applied"] == 1 and summary["worker_rejected"] == []


def test_terminal_code_reason_is_not_truncated_and_late_marker_is_seen(monkeypatch):
    # Complete evidence includes the terminal ground even after the former cutoffs.
    reasons = [f"неточная цитата параметра {i}: подробное полное объяснение" for i in range(9)]
    reasons.append("смена семьи без основания запрещена")
    def reject(*args, **kwargs):
        return {"rejections": [{"reasons": reasons}],
                "touch_targets": set(), "touch_tokens": []}
    monkeypatch.setattr(ex, "_apply_one_answer", reject)
    pack = _pack(_remark())
    _, _, rejected, _ = ex._apply_patches(sample_model(), pack,
        {"answers": [{"remark": "A-001", "verdict": "ПРИНЯТО"}]}, 1)
    closed = ex.code_terminal_closures(rejected, pack)
    assert len(closed) == 1
    words = next(iter(closed.values()))
    assert all(reason in words for reason in reasons)
    assert ex.code_terminal_closures([{"answer": "A-001", "reasons": reasons[:-1]}], pack) == {}


@pytest.mark.parametrize("count", [1, 4, 7])
def test_readiness_barrier_does_not_hide_the_number_of_related_rules(count, monkeypatch):
    doc, _ = _doc_with_gap()
    rule_ids = [f"R-B-{i}" for i in range(count)]
    monkeypatch.setattr(m, "readiness_barriers", lambda d: [
        {"words": "Нет параметра", "rules": rule_ids, "zones": ["ROSTERING"], "hard": True}])
    label = m.readiness(doc)["label"]
    shown = ", ".join(rule_ids[:4])
    if count > 4:
        assert shown + f" (и ещё {count - 4})" in label
    else:
        assert f"(правила {shown})" in label
