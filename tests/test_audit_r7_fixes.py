# -*- coding: utf-8 -*-
"""Негативные тесты исправлений внешнего аудита R7 (2026-07-31).

Каждый тест воспроизводит адверсарный путь аудита: схлопывание номера в
мигрирующем инструменте, календарно невозможное время, ослабленный вердикт у
писателя, грязное дерево кандидата, диапазоны правил источника,
отрицательная круговая сверка, повреждённый реестр аудитов."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ------------------------------------------------ P0-01: мигратор каталогов

def _dup_payload():
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
        "flightAssignments": [],
    }


def _run_migrator(tmp_path, payload, catalog):
    pp = tmp_path / "p.json"
    cp = tmp_path / "c.json"
    pp.write_text(json.dumps(payload), encoding="utf-8")
    cp.write_text(json.dumps(catalog), encoding="utf-8")
    import tools.migrate_catalog_coverage as m
    rc = m.main(["--payload", str(pp), "--catalog", str(cp)])
    return rc, json.loads(cp.read_text(encoding="utf-8"))


def test_migrator_refuses_bare_number_on_duplicate(tmp_path):
    """Одна запись «F100» при двух вхождениях номера — неоднозначность,
    а не покрытие обоих (R7, P0-01)."""
    rc, doc = _run_migrator(tmp_path, _dup_payload(),
                            {"selected": [{"active_fns": ["F100"]}]})
    assert rc == 1
    assert "coverage" not in doc


def test_migrator_refuses_foreign_catalog(tmp_path):
    p = _dup_payload()
    p["flights"] = [p["flights"][0]]
    p["flights"][0]["flightNumber"] = "F1"
    p["flights"][0].pop("flightLegId")
    rc, doc = _run_migrator(tmp_path, p,
                            {"selected": [{"active_fns": ["NO-SUCH"]}]})
    assert rc == 1 and "coverage" not in doc


def test_migrator_stamps_provenance_on_full_coverage(tmp_path):
    p = _dup_payload()
    p["flights"] = [p["flights"][0]]
    p["flights"][0]["flightNumber"] = "F1"
    p["flights"][0].pop("flightLegId")
    rc, doc = _run_migrator(tmp_path, p,
                            {"selected": [{"active_fns": ["F1"]}]})
    assert rc == 0
    cov = doc["coverage"]
    assert cov["verdict"] == "OK" and cov["legs_total"] == 1
    assert len(cov["payload_sha256"]) == 64
    assert len(cov["catalog_sha256_before"]) == 64


# ------------------------------------------------ P0-03: семантика времени

def test_registry_rejects_impossible_calendar_dates():
    from webapp import registry
    base = {
        "formatVersion": "1",
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA"}],
        "flightAssignments": [],
    }
    for dep in ("2026-02-30T10:00:00", "2026-08-01T25:00:00"):
        payload = dict(base)
        payload["flights"] = [{
            "flightNumber": "F1", "departureAirport": "AAA",
            "departureUTCDateTime": dep, "arrivalAirport": "BBB",
            "arrivalUTCDateTime": "2026-08-01T12:00:00"}]
        res = registry.validate_payload(payload)
        assert res["ok"] is False, dep


# ----------------------------------------- P0-05: белый список у писателя

def test_producer_defers_relaxed_cover(tmp_path, monkeypatch):
    """RELAXED_COVER не публикуется под каноническим именем и даёт
    ненулевой код (R7, P0-05)."""
    import _pairing_builder as pb

    def fake_build(payload, args, base_catalog=None, pin_catalog=None, **_kw):
        cov = {"legs_total": 2, "uncovered": 0, "no_pair_in_horizon": 0,
               "not_built": 0, "complete": True,
               "verdict": "RELAXED_COVER", "exact_cover": False,
               "units_truncated": 1,
               "units_truncated_with_station_break": 0}
        return ({"label": args.label, "coverage": cov, "selected": []},
                {"total_cost": 0, "duties": 0, "candidates": 0, "units": 0,
                 "uncovered_legs": 0, "person_nights": 0, "person_rides": 0,
                 "uncovered_no_pair_in_horizon": 0, "uncovered_not_built": 0})
    monkeypatch.setattr(pb, "build", fake_build)
    plain = _dup_payload()
    plain["flights"] = [plain["flights"][0]]
    plain["flights"][0]["flightNumber"] = "F1"
    plain["flights"][0].pop("flightLegId")
    payload_p = tmp_path / "p.json"
    payload_p.write_text(json.dumps(plain), encoding="utf-8")
    out = tmp_path / "cat.json"
    rc = pb.main(["--payload", str(payload_p), "--label", "r7",
                  "--level", "3", "--out", str(out)])
    assert rc == 3
    assert not out.exists(), "канонического файла быть не должно"
    assert out.with_name(out.stem + ".pending.json").exists()


# --------------------------------------- P0-06а: чистота дерева кандидата

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True)


def test_worktree_hazards_flags_untracked_conftest(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "base")
    from webapp.evolution_runner import _worktree_hazards
    assert _worktree_hazards(repo) == []
    (repo / "junk.txt").write_text("ok", encoding="utf-8")
    assert _worktree_hazards(repo) == [], "безвредный мусор не судится"
    (repo / "conftest.py").write_text("import os\n", encoding="utf-8")
    assert any("conftest.py" in h for h in _worktree_hazards(repo))
    (repo / "conftest.py").unlink()
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert any("a.py" in h for h in _worktree_hazards(repo)), \
        "изменённый отслеживаемый файл — дерево не равно ревизии"


# ------------------------------------------- P1-05: диапазоны правил

def test_source_rules_reject_out_of_range_time_and_tiers():
    import _cos_rules
    with pytest.raises(_cos_rules.SourceRulesError):
        _cos_rules.from_parameters({"nightStart": "99:99"})
    with pytest.raises(_cos_rules.SourceRulesError):
        _cos_rules.from_parameters({"layoverRules": {"thresholds": [
            {"thresholdDuration": "PT12H", "onBaseMinimum": "-PT1H"}]}})


# ------------------------------------------- P1-03: сверка как гейт

def test_round_trip_failure_pulls_the_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tests"))
    import test_pairing_to_solver as T
    import _pairing_to_solver as pts
    payload, catalog, source = T._fixture()

    def lossy_read_back(doc, name, check_name):
        return None, None, [], []               # всё «потерялось»
    monkeypatch.setattr("_solver_to_payload.convert", lossy_read_back)
    pp, cp, sp = (tmp_path / n for n in ("p.json", "c.json", "s.json"))
    pp.write_text(json.dumps(payload), encoding="utf-8")
    cp.write_text(json.dumps(catalog), encoding="utf-8")
    sp.write_text(json.dumps(source), encoding="utf-8")
    out = tmp_path / "f5.json"
    rc = pts.main(["--payload", str(pp), "--catalog", str(cp),
                   "--source", str(sp), "--out", str(out), "--verify",
                   "--check", str(tmp_path / "chk.json")])
    assert rc == 1
    assert not out.exists(), "файл с потерями обязан быть снят"
    chk = json.loads((tmp_path / "chk.json").read_text(encoding="utf-8"))
    assert chk["ok"] is False


# --------------------------------------- P1-18а: повреждённый реестр

def test_audit_inbox_fails_closed_on_corrupt_registry(tmp_path, monkeypatch):
    from webapp import audit_inbox as ai
    monkeypatch.setattr(ai, "REGISTRY_PATH", tmp_path / "reg.json")
    (tmp_path / "reg.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ai.AuditInboxError):
        ai._load()

# ================== Дополнения пакета R8 (2026-07-31, вечер) ==================

def test_r8_migrator_refuses_indistinguishable_legs(tmp_path):
    """Два рейса с одинаковыми flightNumber И flightLegId неразличимы —
    отказ, а не «один адрес покрывает оба» (R8, P1-01)."""
    p = _dup_payload()
    p["flights"][1]["flightLegId"] = "F100|a"       # дубль идентичности
    rc, doc = _run_migrator(tmp_path, p,
                            {"selected": [{"active_fns": ["F100|a"]}]})
    assert rc == 1 and "coverage" not in doc


def test_r8_resolve_rel_rejects_parent_traversal():
    from webapp import registry
    with pytest.raises(ValueError):
        registry.resolve_rel("a/../../etc/passwd")


def test_r8_branch_duty_cap_needs_a_witnessed_extra_successor():
    """Кап пространства смен пишется ТОЛЬКО при наблюдении (k+1)-го
    допустимого продолжения (R10, P2-01): прежний тест закреплял
    опровергнутую семантику — фикстура с единственным преемником при
    branch_duty=1 требовала кап, хотя множество смен было полным, и
    достижимая нижняя граница подавлялась незаслуженно."""
    sys.path.insert(0, str(REPO / "tests"))
    import test_pairing_builder as T
    import _pairing_builder as pb
    # ровно один преемник == limit: капа нет, граница не подавлена
    args = T._args(method="columns", branch_duty=1)
    _cat, report = pb.build(T._simple_round_trip(), args)
    assert "branch_duty" not in (report.get("pricing_caps") or []),         report.get("pricing_caps")
    rows = report.get("pricing_truncations") or []
    assert all(r.get("name") != "branch_duty" for r in rows), rows


def test_r8_branch_duty_cap_written_when_extra_successor_exists():
    """Два допустимых продолжения при branch_duty=1 — (k+1)-й наблюдён,
    кап записан, полнота и граница подавлены (R8 P0-03 + R10 P2-01)."""
    sys.path.insert(0, str(REPO / "tests"))
    import test_pairing_builder as T
    import _pairing_builder as pb
    payload = T._payload([
        T._leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        T._leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
        T._leg("F3", "AAA", "BASE", "2026-08-01T11:00:00", 120)])
    args = T._args(method="columns", branch_duty=1)
    _cat, report = pb.build(payload, args)
    assert "branch_duty" in (report.get("pricing_caps") or []), report.get(
        "pricing_caps")
    assert report.get("pricing_complete") is False
    assert report.get("lower_bound") is None


def test_r8_stage2_requires_fp_with_source_rules(tmp_path):
    import _gerad_stage2_roster as st2
    with pytest.raises(SystemExit):
        st2.parse_args(["--payload", "p", "--catalog", "c",
                        "--label", "x", "--source-rules", "r.json"])
