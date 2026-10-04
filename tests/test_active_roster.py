"""Active-roster manifest + promotion (аудит-2, P1-06/P1-13): порядок
публикации, проверка отпечатков, legacy-фолбэк, явная промоция multirole."""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import _roster_manifest as rm                     # noqa: E402
from webapp import active_roster, registry        # noqa: E402

RULES = {"min_rest_min": 600}


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Изолировать final-report и полностью зафиксировать правила."""
    fr = tmp_path / "final-report"
    fr.mkdir()
    monkeypatch.setattr(rm, "FINAL_REPORT", fr)
    monkeypatch.setattr(registry, "FINAL_REPORT", fr)
    monkeypatch.setattr(active_roster, "effective_rules_for",
                        lambda inst: dict(RULES))
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"flights": []}), encoding="utf-8")
    monkeypatch.setattr(registry, "instance_payload_path",
                        lambda inst: payload)
    return {"fr": fr, "payload": payload, "tmp": tmp_path}


def _publish(iso_env, label="acme", status="OK", mode="stage2",
             auto=True) -> dict:
    fr = iso_env["fr"]
    assignment = fr / f"gerad_stage2_assignment_{label}.json"
    assignment.write_text(json.dumps(
        {"result_status": status, "pairings": [
            {"pid": "p0", "solver_employee": "E1",
             "fns": ["F1"], "active_fns": ["F1"]}]}), encoding="utf-8")
    catalog = iso_env["tmp"] / f"catalog_{label}.json"
    catalog.write_text("{}", encoding="utf-8")
    m = rm.build_run_manifest(
        label=label, mode=mode, result_status=status,
        payload_path=iso_env["payload"], rules_snapshot=RULES,
        assignment_path=assignment, solution_path=None,
        catalog_path=catalog)
    rm.publish(m, auto_promote=auto)
    return m


def test_publish_and_resolve_active(iso):
    _publish(iso)
    inst = {"label": "acme"}
    res = active_roster.resolve(inst)
    assert res["status"] == "active"
    assert res["assignment"]["result_status"] == "OK"
    assert res["meta"]["mode"] == "stage2"
    assert res["warnings"] == [] and res["mismatches"] == []
    # per-run манифест неизменяем и лежит в runs/roster-manifests
    runs = list((iso["fr"] / "runs" / "roster-manifests" / "acme")
                .glob("*.json"))
    assert len(runs) == 1
    # ни одного tmp-огрызка
    assert not list(iso["fr"].glob("*.tmp-*"))


def test_legacy_fallback_without_manifest(iso):
    (iso["fr"] / "gerad_stage2_assignment_old.json").write_text(
        json.dumps({"result_status": "OK", "pairings": []}),
        encoding="utf-8")
    res = active_roster.resolve({"label": "old"})
    assert res["status"] == "legacy"
    assert "legacy_no_manifest" in res["warnings"]
    assert res["assignment"]["result_status"] == "OK"


def test_absent_when_nothing_published(iso):
    res = active_roster.resolve({"label": "nothing"})
    assert res["status"] == "absent" and res["assignment"] is None


@pytest.mark.parametrize("what", ["payload", "rules", "assignment"])
def test_any_fingerprint_mismatch_is_stale(iso, monkeypatch, what):
    _publish(iso)
    inst = {"label": "acme"}
    if what == "payload":
        iso["payload"].write_text(json.dumps({"flights": [1]}),
                                  encoding="utf-8")
    elif what == "rules":
        monkeypatch.setattr(active_roster, "effective_rules_for",
                            lambda inst: {"min_rest_min": 601})
    else:
        (iso["fr"] / "gerad_stage2_assignment_acme.json").write_text(
            json.dumps({"result_status": "OK", "pairings": [],
                        "tampered": 1}), encoding="utf-8")
    res = active_roster.resolve(inst)
    assert res["status"] == "stale"
    assert res["assignment"] is None
    assert what in " ".join(res["mismatches"])


def test_unpromoted_multirole_ok_is_flagged(iso):
    _publish(iso)                                    # активный stage2
    (iso["fr"] / "gerad_multirole_acme.json").write_text(
        json.dumps({"result_status": "OK"}), encoding="utf-8")
    res = active_roster.resolve({"label": "acme"})
    assert res["status"] == "active"
    assert "unpromoted_multirole_ok" in res["warnings"]


def test_promote_multirole_materializes_pairing_doc(iso):
    from _gerad_role_views import role_slug
    label = "acme"
    fr = iso["fr"]
    # lane-траектории двух ролей
    for role, emp in (("Captain", "C1"), ("FirstOfficer", "F1")):
        (fr / f"gerad_stage2_assignment_{label}--role--"
              f"{role_slug(role)}.json").write_text(json.dumps({
                  "result_status": "OK",
                  "pairings": [{"pid": "p0", "solver_employee": emp,
                                "fns": ["F1"], "active_fns": ["F1"]}]}),
              encoding="utf-8")
    summary = fr / f"gerad_multirole_{label}.json"
    summary.write_text(json.dumps({"result_status": "OK"}), encoding="utf-8")
    m = rm.build_run_manifest(
        label=label, mode="multirole", result_status="OK",
        payload_path=iso["payload"], rules_snapshot=RULES,
        assignment_path=summary, solution_path=None,
        multirole={"mode": "independent",
                   "roles": ["Captain", "FirstOfficer"]},
        limitations=["score_non_comparable"])
    rm.publish(m, auto_promote=False)
    out = active_roster.promote_multirole({"label": label}, "admin@x.ru")
    assert out["promoted"] is True and out["pairings"] == 2
    res = active_roster.resolve({"label": label})
    assert res["status"] == "active"
    assert res["meta"]["mode"] == "multirole"
    pids = {p["pid"] for p in res["assignment"]["pairings"]}
    assert pids == {"captain--p0", "firstofficer--p0"}
    crew_roles = {p["crew"][0]["requiredSkill"]
                  for p in res["assignment"]["pairings"]}
    assert crew_roles == {"Captain", "FirstOfficer"}


def test_promote_rejects_non_ok_and_missing(iso):
    with pytest.raises(active_roster.PromoteError):
        active_roster.promote_multirole({"label": "acme"}, "a@x.ru")
    label = "acme"
    summary = iso["fr"] / f"gerad_multirole_{label}.json"
    summary.write_text(json.dumps({"result_status": "INFEASIBLE_INCUMBENT"}),
                       encoding="utf-8")
    m = rm.build_run_manifest(
        label=label, mode="multirole",
        result_status="INFEASIBLE_INCUMBENT",
        payload_path=iso["payload"], rules_snapshot=RULES,
        assignment_path=summary, solution_path=None,
        multirole={"mode": "independent", "roles": ["Captain"]})
    rm.publish(m, auto_promote=False)
    with pytest.raises(active_roster.PromoteError, match="OK"):
        active_roster.promote_multirole({"label": label}, "a@x.ru")


# --- P0-1 (аудит документации 2026-07-20): last_run_not_ok ------------------


def test_nonok_run_warns_but_previous_roster_stays_active(iso):
    _publish(iso, status="OK")
    # не-OK прогон: манифест публикуется БЕЗ промоции, артефакт — диагностика
    diag = iso["fr"] / "runs" / "stage2-diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    bad = diag / "acme-20990101-000000-assignment.json"
    bad.write_text(json.dumps({"result_status": "INFEASIBLE_INCUMBENT",
                               "pairings": []}), encoding="utf-8")
    catalog = iso["tmp"] / "catalog_acme.json"
    m = rm.build_run_manifest(
        label="acme", mode="stage2", result_status="INFEASIBLE_INCUMBENT",
        payload_path=iso["payload"], rules_snapshot=RULES,
        assignment_path=bad, solution_path=None, catalog_path=catalog)
    m["published_at"] = "2099-01-01 00:00:00"      # заведомо новее промоции
    rm.publish(m, auto_promote=False)

    res = active_roster.resolve({"label": "acme"})
    assert res["status"] == "active"
    assert res["assignment"]["result_status"] == "OK"     # прежний ростер цел
    assert "last_run_not_ok" in res["warnings"]
    assert res["meta"]["last_run"]["result_status"] == "INFEASIBLE_INCUMBENT"


def test_nonok_run_older_than_promotion_is_silent(iso):
    diag = iso["fr"] / "runs" / "stage2-diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    bad = diag / "acme-19990101-000000-assignment.json"
    bad.write_text(json.dumps({"result_status": "REPAIR_GREEDY",
                               "pairings": []}), encoding="utf-8")
    catalog = iso["tmp"] / "catalog_acme.json"
    catalog.write_text("{}", encoding="utf-8")
    m = rm.build_run_manifest(
        label="acme", mode="repair", result_status="REPAIR_GREEDY",
        payload_path=iso["payload"], rules_snapshot=RULES,
        assignment_path=bad, solution_path=None, catalog_path=catalog)
    m["published_at"] = "1999-01-01 00:00:00"      # заведомо старее
    rm.publish(m, auto_promote=False)
    _publish(iso, status="OK")                     # свежая OK-промоция

    res = active_roster.resolve({"label": "acme"})
    assert res["status"] == "active"
    assert "last_run_not_ok" not in res["warnings"]


def test_absent_label_still_reports_nonok_run(iso):
    # Ревью P0: первый прогон свежей метки с не-OK вердиктом — канонического
    # файла нет, но предупреждение и мета обязаны быть видны
    diag = iso["fr"] / "runs" / "stage2-diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    bad = diag / "fresh-20990101-000000-assignment.json"
    bad.write_text(json.dumps({"result_status": "INFEASIBLE_INCUMBENT",
                               "pairings": []}), encoding="utf-8")
    catalog = iso["tmp"] / "catalog_fresh.json"
    catalog.write_text("{}", encoding="utf-8")
    m = rm.build_run_manifest(
        label="fresh", mode="stage2", result_status="INFEASIBLE_INCUMBENT",
        payload_path=iso["payload"], rules_snapshot=RULES,
        assignment_path=bad, solution_path=None, catalog_path=catalog)
    rm.publish(m, auto_promote=False)
    res = active_roster.resolve({"label": "fresh"})
    assert res["status"] == "absent"
    assert "last_run_not_ok" in res["warnings"]
    assert res["meta"]["last_run"]["result_status"] == "INFEASIBLE_INCUMBENT"


def test_killer_nonok_repair_mode_warns(iso):
    # Мутант mode=None -> mode='stage2' в _last_run_not_ok гасил бы
    # предупреждение о не-OK РЕМОНТЕ (REPAIR_GREEDY, mode='repair')
    _publish(iso, status="OK")
    diag = iso["fr"] / "runs" / "stage2-diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    bad = diag / "acme-20990101-000001-assignment.json"
    bad.write_text(json.dumps({"result_status": "REPAIR_GREEDY",
                               "pairings": []}), encoding="utf-8")
    m = rm.build_run_manifest(
        label="acme", mode="repair", result_status="REPAIR_GREEDY",
        payload_path=iso["payload"], rules_snapshot=RULES,
        assignment_path=bad, solution_path=None,
        catalog_path=iso["tmp"] / "catalog_acme.json")
    m["published_at"] = "2099-01-01 00:00:00"
    rm.publish(m, auto_promote=False)
    res = active_roster.resolve({"label": "acme"})
    assert "last_run_not_ok" in res["warnings"]
    assert res["meta"]["last_run"]["mode"] == "repair"
