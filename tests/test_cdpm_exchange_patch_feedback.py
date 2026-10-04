"""Local N0 run-0175: malformed full patches must get receiver feedback.

The two synthetic source clauses and malformed carrier shape reproduce the
browser failure on ff823330. No live model, service, saved run, or oracle is
modified. Scripted model output tests transport/validation, not text quality.
"""
import copy
import hashlib
import json

import pytest

from cdpm import catalog_bridge as cb, exchange as ex, model as m
from template_catalog import family_contract
from test_k03_subject_vocabulary import selected_catalogue


CLAUSES = [
    "Минимальная стыковка между рейсами — 45 минут (в базе и вне базы одинаково).",
    "VN-050. Разброс кредитных часов. Штраф 3 условные единицы за каждый "
    "кредитный час разброса сверх порога существенности 10 кредитных часов "
    "за месяц. Шаблон: balanced_workload (категория A)",
]


@pytest.fixture
def case(tmp_path, monkeypatch):
    # A validated modern catalogue is explicit test input. The shipped 0.6.0
    # has no unit vocabulary; neither it nor the live rollout is rewritten.
    try:
        cat = selected_catalogue(tmp_path / "catalogue", monkeypatch)
        assert cat.version() == "0.7.0"
        doc = {"meta": {"customer": "Synthetic N0 patch feedback", "mode": 1,
                        "cdpm_version": "0.1"},
               "sources": [], "rules": [], "questions": [], "objectives": []}
        data = [
            ("R-S1-абз-15", "абз.15", "PAIRING", "HARD", "connection_windows",
             "min_turnaround", 45, "duration_minutes", "min",
             "Минимальная стыковка между рейсами — 45 минут",
             "Между рейсами внутри смены — минимум 45 минут стыковки, одинаково в базе и вне базы.",
             "Между рейсами — минимум 45 минут стыковки, одинаково в базе и вне базы."),
            ("R-S2-лист-«VMP-H0-2»-строка-6", "лист «VMP-H0-2», строка 6",
             "ROSTERING", "SOFT", "balanced_workload", "workload_spread_threshold",
             10, "credit_hours", "max", "сверх порога существенности 10 кредитных часов за месяц",
             "Штраф 3 условные единицы за каждый кредитный час разброса кредитных часов по экипажу сверх порога существенности 10 кредитных часов за месяц.",
             "Штраф 3 условные единицы за каждый кредитный час разброса кредитных часов сверх порога существенности 10 кредитных часов за месяц."),
        ]
        pack = {"round": 3, "remarks": []}
        healthy, malformed, clauses = [], [], {}
        for i, fields in enumerate(data, 1):
            rid, anchor, zone, kind, family, name, value, unit, relation, quote, before, after = fields
            sid, clause = f"S{i}", CLAUSES[i - 1]
            doc["sources"].append({"sid": sid, "file": f"synthetic-{i}.txt",
                                   "sha256": hashlib.sha256(clause.encode()).hexdigest()})
            clauses[(sid, anchor)] = clause
            cell = {"value": value, "unit": unit, "relation": relation,
                    "condition": None, "quote": quote, "source": {"kind": "rule", "rule_id": rid}}
            fit = {"category": "A", "family": family, "params": {name: cell},
                   "schema_version": "landing/2", "registry_rev": cat.fingerprint(),
                   "family_contract": family_contract.snapshot(cat, family, [name])}
            row = {"id": rid, "title": family, "statement": before,
                   "kind": kind, "zone": zone, "sources": [{"sid": sid, "anchor": anchor}],
                   "template_fit": fit}
            if kind == "SOFT":
                row.update(weight=3, weight_quote="Штраф 3 условные единицы за каждый кредитный час разброса",
                           export_solver={"family": family, "group": "crew"})
            doc["rules"].append(row)
            rm = f"A3-{i:03d}"
            pack["remarks"].append({"id": rm, "severity": "КРИТИЧЕСКОЕ",
                "defect_class": "искажение", "target": {"rule": rid},
                "source_refs": [{"sid": sid, "anchor": anchor}], "text": "Убрать необоснованное сужение",
                "evidence": clause, "proposal": after})
            correction = copy.deepcopy(row)
            correction["statement"] = after
            answer = {"remark": rm, "verdict": "ПРИНЯТО", "basis": clause,
                      "change": "Убрано сужение", "patch": {"rule_id": rid, "rule": correction}}
            healthy.append(answer)
            bad = copy.deepcopy(answer)
            bad_row = bad["patch"]["rule"]
            bad_row["schema_version"] = bad_row["template_fit"].pop("schema_version")
            bad_row["template_fit"].pop("registry_rev")
            bad_row["template_fit"].pop("family_contract")
            malformed.append(bad)
        assert m.validate_schema(doc) == m.validate_model(doc) == []
        assert ex.validate_remarks(pack, doc) == []
        yield doc, pack, clauses, healthy, malformed
    finally:
        cb.forget()


def receive(case, answers, *, enforce=False):
    doc, pack, clauses, _, _ = case
    before = copy.deepcopy(doc)
    response = {"round": pack["round"], "answers": copy.deepcopy(answers)}
    executed = set()
    result = ex._apply_patches(doc, pack, response, 99 if enforce else pack["round"],
        valid_anchors=set(clauses), clause_texts=clauses,
        convert_reading_choice=not enforce, executed_out=executed)
    assert doc == before
    return result, executed


@pytest.mark.parametrize("enforce", [False, True])
def test_recorded_malformed_shape_remains_rejected(case, enforce):
    doc, _, _, _, bad = case
    (new, targets, rejected, tokens), executed = receive(case, bad, enforce=enforce)
    assert new == doc and not targets and not tokens and not executed
    assert {x["answer"] for x in rejected} == {"A3-001", "A3-002"}
    assert all("schema_version" in str(x) and "condition" in str(x) for x in rejected)


@pytest.mark.parametrize("omit_fit", [False, True])
def test_healthy_typed_text_correction_preserves_pin_and_cells(case, omit_fit):
    doc, _, _, good, _ = case
    good = copy.deepcopy(good)
    if omit_fit:
        for answer in good:
            answer["patch"]["rule"].pop("template_fit")
    (new, _, rejected, tokens), executed = receive(case, good)
    assert not rejected and tokens and executed == {"A3-001", "A3-002"}
    assert [r["template_fit"] for r in new["rules"]] == [r["template_fit"] for r in doc["rules"]]
    assert m.validate_schema(new) == m.validate_model(new) == []


@pytest.mark.parametrize("enforce", [False, True])
def test_malformed_patch_gets_exact_feedback_in_existing_retry(case, tmp_path, enforce):
    doc, pack, clauses, good, bad = case
    seen = []

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": bad if len(seen) == 1 else good}, ensure_ascii=False)

    before = copy.deepcopy((doc, pack, good, bad))
    kwargs = {"template": ex._PROMPT_ENFORCE} if enforce else {}
    resp = ex._respond_round(worker, doc, pack, clauses, cache_dir=tmp_path, **kwargs)
    assert len(seen) == 2, "A valid envelope must not hide a rejected patch from the retry"
    assert "schema_version" in seen[1] and "None is not of type 'string'" in seen[1]
    assert "A3-001" in seen[1] and "A3-002" in seen[1]
    assert (doc, pack, good, bad) == before, "Preflight is read-only, including conversion annotations"
    assert resp["answers"] == good
    (_, _, rejected, _), executed = receive(case, resp["answers"], enforce=enforce)
    assert not rejected and executed == {"A3-001", "A3-002"}
    attempts = list((tmp_path / "attempts").glob("*.json"))
    assert len(attempts) == 2
    saved = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(attempts)]
    assert saved[0]["answers"] == bad and len(saved[0]["patch_rejections"]) == 2
    assert saved[1]["answers"] == good and saved[1]["patch_rejections"] == []
    assert ex._respond_round(worker, doc, pack, clauses, cache_dir=tmp_path, **kwargs) == resp
    assert len(seen) == 2, "Completed chunk is reused without a third request"


def test_two_bad_attempts_remain_bad_and_keep_receiver_reasons(case, tmp_path):
    doc, pack, clauses, _, bad = case
    seen = []

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": bad}, ensure_ascii=False)

    resp = ex._respond_round(worker, doc, pack, clauses, cache_dir=tmp_path)
    assert len(seen) == 2 and resp["answers"] == bad
    (new, _, rejected, _), executed = receive(case, resp["answers"])
    assert new == doc and len(rejected) == 2 and not executed
    assert len(list((tmp_path / "attempts").glob("*.json"))) == 2


def test_healthy_first_attempt_needs_no_extra_model_call(case, tmp_path):
    doc, pack, clauses, good, _ = case
    seen = []

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": good}, ensure_ascii=False)

    assert ex._respond_round(worker, doc, pack, clauses, cache_dir=tmp_path)["answers"] == good
    assert len(seen) == 1


def test_cache_is_bound_to_the_checked_input_and_prompt(case, tmp_path):
    doc, pack, clauses, good, _ = case
    seen = []

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": good}, ensure_ascii=False)

    ex._respond_round(worker, doc, pack, clauses, cache_dir=tmp_path)
    ex._respond_round(worker, doc, pack, clauses, cache_dir=tmp_path)
    assert len(seen) == 1
    changed = copy.deepcopy(doc)
    changed["meta"]["note"] = "Another checked model, not another identity for the old cache"
    ex._respond_round(worker, changed, pack, clauses, cache_dir=tmp_path)
    assert len(seen) == 2
    ex._respond_round(worker, changed, pack, clauses, cache_dir=tmp_path,
                      template=ex._PROMPT_ENFORCE)
    assert len(seen) == 3
    assert len(list(tmp_path.glob("chunk-*.json"))) == 3


def test_failed_retry_does_not_lose_the_first_rejected_patch(case, tmp_path):
    doc, pack, clauses, _, bad = case
    seen = []

    def worker(prompt):
        seen.append(prompt)
        return json.dumps({"answers": bad}, ensure_ascii=False) if len(seen) == 1 else "not JSON"

    response = ex._respond_round(worker, doc, pack, clauses, cache_dir=tmp_path)
    assert len(seen) == 2 and response["answers"] == bad
    (new, _, rejected, _), executed = receive(case, response["answers"])
    assert new == doc and len(rejected) == 2 and not executed
    assert any("parse_error" in json.loads(p.read_text(encoding="utf-8"))
               for p in (tmp_path / "attempts").glob("*.json"))


@pytest.mark.parametrize("damage", ["pin-missing", "pin-forged", "source-forged", "value-forged"])
def test_preflight_never_fills_or_repairs_untrusted_typed_fields(case, damage):
    doc, _, _, good, _ = case
    changed = copy.deepcopy(good)
    fit = changed[0]["patch"]["rule"]["template_fit"]
    if damage == "pin-missing":
        del fit["family_contract"]
    elif damage == "pin-forged":
        fit["family_contract"]["record_fingerprint"] = "0" * 64
    elif damage == "source-forged":
        fit["params"]["min_turnaround"]["source"]["rule_id"] = "R-FOREIGN"
    else:
        fit["params"]["min_turnaround"]["value"] = 90
    before = copy.deepcopy(changed)
    (new, _, rejected, _), executed = receive(case, changed)
    assert changed == before
    assert "A3-001" not in executed and "A3-002" in executed
    assert any(r["answer"] == "A3-001" for r in rejected)
    assert new["rules"][0] == doc["rules"][0]


@pytest.mark.parametrize("name", ["_PROMPT_RESPOND", "_PROMPT_ENFORCE"])
def test_both_producer_prompts_explain_typed_carrier_location(name):
    rendered = getattr(ex, name).format(remarks="REMARKS", rules="RULES", clauses="CLAUSES", dictionary="DICTIONARY")
    assert "template_fit.schema_version" in rendered
    assert "template_fit.family_contract" in rendered
    assert "на уровне строки" in rendered
    assert "не пересоздавай" in rendered
