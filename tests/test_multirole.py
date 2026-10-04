"""Фаза 2b (multirole): экстрактор ролевых представлений, слияние, вердикт,
зеркальный инвариант (Captain-представление зеркала == исходная одночленная
задача) — всё без JVM."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
import _gerad_multirole as mr  # noqa: E402
import _gerad_role_views as rv  # noqa: E402
from _coverage_validator import check as coverage_check  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _payload():
    return {
        "airports": [{"code": "BASE"}, {"code": "DEST"}],
        "employees": [
            {"id": "C1", "homeAirport": "BASE", "skills": ["Captain"]},
            {"id": "C2", "homeAirport": "BASE", "skills": ["Captain"]},
            {"id": "F1", "homeAirport": "BASE", "skills": ["FirstOfficer"]},
            {"id": "F2", "homeAirport": "BASE", "skills": ["FirstOfficer"]}],
        "flights": [
            {"flightNumber": "L1", "departureAirport": "BASE",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "DEST",
             "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "L2", "departureAirport": "DEST",
             "departureUTCDateTime": "2026-03-01T12:00:00",
             "arrivalAirport": "BASE",
             "arrivalUTCDateTime": "2026-03-01T14:00:00"}],
        "flightAssignments": [
            {"id": "1", "flight": "L1", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"id": "2", "flight": "L1", "indexInFlight": 1,
             "requiredSkill": "FirstOfficer"},
            {"id": "3", "flight": "L2", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"id": "4", "flight": "L2", "indexInFlight": 1,
             "requiredSkill": "FirstOfficer"}]}


# ------------------------------------------------------------- role views

def test_role_views_split():
    views = rv.role_views(_payload())
    assert set(views) == {"Captain", "FirstOfficer"}
    cap = views["Captain"]
    assert [e["id"] for e in cap["employees"]] == ["C1", "C2"]
    assert len(cap["flightAssignments"]) == 2
    assert all(a["indexInFlight"] == 0 and a["requiredSkill"] == "Captain"
               and a["employee"] is None
               for a in cap["flightAssignments"])
    fo = views["FirstOfficer"]
    assert [e["id"] for e in fo["employees"]] == ["F1", "F2"]
    assert all(a["requiredSkill"] == "FirstOfficer"
               for a in fo["flightAssignments"])
    # представление байт-в-байт одночленно: гард конвейера его принимает
    import _leg_identity as li
    assert li.pipeline_representable(cap) == []
    assert li.seat_signature(cap) == ("Captain",)


def test_role_views_rejects_overlapping_pools():
    p = _payload()
    p["employees"][0]["skills"] = ["Captain", "FirstOfficer"]
    with pytest.raises(rv.MultiroleUnsupported, match="пересекаются"):
        rv.role_views(p)


def test_role_views_rejects_empty_pool():
    p = _payload()
    p["employees"] = [e for e in p["employees"]
                      if "FirstOfficer" not in e["skills"]]
    with pytest.raises(rv.MultiroleUnsupported, match="пуст"):
        rv.role_views(p)


def test_role_views_rejects_single_crew_and_repeated_role():
    single = _payload()
    single["flightAssignments"] = [a for a in single["flightAssignments"]
                                   if a["requiredSkill"] == "Captain"]
    with pytest.raises(rv.MultiroleUnsupported, match="одночленная"):
        rv.role_views(single)
    rep = _payload()
    for a in rep["flightAssignments"]:
        a["requiredSkill"] = "Captain"
    rep["flightAssignments"][1]["indexInFlight"] = 1
    rep["flightAssignments"][3]["indexInFlight"] = 1
    with pytest.raises(rv.MultiroleUnsupported, match="овторная роль"):
        rv.role_views(rep)


# ------------------------------------------------------------------ merge

def _lane_solution(view, assign):
    sol = copy.deepcopy(view)
    for a in sol["flightAssignments"]:
        a["employee"] = assign[a["flight"]]
    return sol


def _lane_traj(role, emp):
    return {"pairings": [{"pid": "C_000000", "employee": emp,
                          "crew": [{"indexInFlight": 0,
                                    "requiredSkill": role,
                                    "employee": emp}],
                          "fns": ["L1", "L2"], "active_fns": ["L1", "L2"]}]}


def test_merge_restores_ordinals_and_coverage_joins():
    p = _payload()
    views = rv.role_views(p)
    sols = {"Captain": _lane_solution(views["Captain"],
                                      {"L1": "C1", "L2": "C1"}),
            "FirstOfficer": _lane_solution(views["FirstOfficer"],
                                           {"L1": "F1", "L2": "F1"})}
    merged = mr.merge_solutions(p, sols)
    rows = merged["flightAssignments"]
    assert len(rows) == 4
    assert {(r["flight"], r["indexInFlight"], r["requiredSkill"])
            for r in rows} == {("L1", 0, "Captain"), ("L1", 1, "FirstOfficer"),
                               ("L2", 0, "Captain"), ("L2", 1, "FirstOfficer")}
    cov = coverage_check(p, merged)
    assert cov["fully_covered"] and cov["skill_mismatches"] == 0


def test_merge_mirrors_demand_identity_stamping():
    # Фаза-3 урок: идентичности зеркалят СПРОС — штамп только там, где спрос
    # сам нёс поле. flightLegId материализуем ОДНОРОДНО (по-местная частичная
    # материализация разводит ключи мест одного участка -> честный fail-closed
    # отказ гарда, см. test_partial_per_seat_materialization_fails_closed);
    # частичность проверяем на crewSlotId (не влияет на идентичность участка).
    p = _payload()
    for f in p["flights"]:
        f["flightLegId"] = f"{f['flightNumber']}|{f['departureUTCDateTime']}"
    for a in p["flightAssignments"]:
        a["flightLegId"] = next(
            f["flightLegId"] for f in p["flights"]
            if f["flightNumber"] == a["flight"])
        if a["requiredSkill"] == "Captain":       # только Captain-спрос несёт
            a["crewSlotId"] = "s" + a["id"]
    views = rv.role_views(p)
    sols = {"Captain": _lane_solution(views["Captain"],
                                      {"L1": "C1", "L2": "C1"}),
            "FirstOfficer": _lane_solution(views["FirstOfficer"],
                                           {"L1": "F1", "L2": "F1"})}
    merged = mr.merge_solutions(p, sols)
    caps = [r for r in merged["flightAssignments"]
            if r["requiredSkill"] == "Captain"]
    fos = [r for r in merged["flightAssignments"]
           if r["requiredSkill"] == "FirstOfficer"]
    assert all("flightLegId" in r and "crewSlotId" in r for r in caps)
    assert all("flightLegId" in r and "crewSlotId" not in r for r in fos)
    cov = coverage_check(p, merged)
    assert cov["fully_covered"]


def test_partial_per_seat_materialization_fails_closed():
    # flightLegId только у части мест участка -> ключи мест расходятся ->
    # сигнатура нечитаема -> громкий отказ (не тихий неверный результат).
    p = _payload()
    for a in p["flightAssignments"]:
        if a["requiredSkill"] == "Captain":
            a["flightLegId"] = f"{a['flight']}|x"
    p["flights"][0]["flightLegId"] = "L1|x"
    p["flights"][1]["flightLegId"] = "L2|x"
    with pytest.raises(rv.MultiroleUnsupported):
        rv.role_views(p)


def test_merged_physics_clean_and_verdict_ok():
    p = _payload()
    trajs = {"Captain": _lane_traj("Captain", "C1"),
             "FirstOfficer": _lane_traj("FirstOfficer", "F1")}
    phys = mr.merged_physics(p, trajs)
    assert phys["violations_total"] == 0 and phys["employees"] == 2
    cov = {"fully_covered": True}
    assert mr.decide_verdict({"Captain": "OK", "FirstOfficer": "OK"},
                             cov, phys) == "OK"


def test_merged_physics_catches_cross_role_double_booking():
    # Защита в глубину: один человек в обоих lane'ах (будущие пересекающиеся
    # пулы) — слитая физика видит наложение, которого lane'ы не видят.
    p = _payload()
    trajs = {"Captain": _lane_traj("Captain", "X1"),
             "FirstOfficer": _lane_traj("FirstOfficer", "X1")}
    phys = mr.merged_physics(p, trajs)
    assert phys["violations_total"] > 0
    assert "overlap" in phys["violations"]


def test_verdict_fail_closed():
    ok_phys = {"violations_total": 0}
    assert mr.decide_verdict({"Captain": "OK", "FirstOfficer":
                              "INFEASIBLE_INCUMBENT"},
                             {"fully_covered": True}, ok_phys) \
        == "INFEASIBLE_INCUMBENT"
    assert mr.decide_verdict({"Captain": "OK", "FirstOfficer": "OK"},
                             {"fully_covered": False}, ok_phys) \
        == "INFEASIBLE_INCUMBENT"
    assert mr.decide_verdict({"Captain": "OK", "FirstOfficer": "OK"},
                             {"fully_covered": True},
                             {"violations_total": 3}) \
        == "INFEASIBLE_INCUMBENT"
    assert mr.decide_verdict({"Captain": "WEIRD_STATUS"},
                             {"fully_covered": True}, ok_phys) \
        == "INFEASIBLE_INCUMBENT"


# ---------------------------------------------------- фиксы ревью 2026-07-19

def test_lane_separator_injective_vs_upload_labels():
    """Ревью (medium): '-role-' достижим в пользовательских слагах — метка
    lane обязана использовать '--role--': в upload-метке '--' встречается
    ровно один раз (user--name, слагифай схлопывает серии дефисов), поэтому
    lane-метка с двумя '--' не совпадает ни с одной пользовательской."""
    import inspect
    src = inspect.getsource(mr.run_multirole)
    assert '--role--' in src and '}-role-{' not in src
    from webapp import registry
    # даже провокационное имя не даёт двух '--'
    label = registry.upload_target("u@x.com", "demo-role-captain.json")[1]
    assert label.count("--") == 1
    lane = "u--demo--role--captain"
    assert lane.count("--") == 3            # никогда не равно upload-метке


def test_role_views_rejects_lossy_slug_collision():
    p = _payload()
    for e in p["employees"]:
        if "FirstOfficer" in e["skills"]:
            e["skills"] = ["CAPTAIN"]
    for a in p["flightAssignments"]:
        if a["requiredSkill"] == "FirstOfficer":
            a["requiredSkill"] = "CAPTAIN"
    with pytest.raises(rv.MultiroleUnsupported, match="одинаковую lane-метку"):
        rv.role_views(p)


def test_role_views_rejects_pool_employee_without_id():
    p = _payload()
    del p["employees"][0]["id"]
    with pytest.raises(rv.MultiroleUnsupported, match="без id"):
        rv.role_views(p)


def test_demand_identity_key_accepts_flight_number_field():
    # Ревью: спрос с flightNumber вместо flight-ссылки не должен схлопывать
    # identity-карту в (None, role).
    p = _payload()
    for f in p["flights"]:
        f["flightLegId"] = f"{f['flightNumber']}|{f['departureUTCDateTime']}"
    for a in p["flightAssignments"]:
        a["flightNumber"] = a.pop("flight")
        a["flightLegId"] = next(
            f["flightLegId"] for f in p["flights"]
            if f["flightNumber"] == a["flightNumber"])
    views = None  # role_views требует flight-ссылку? проверим identity-карту
    sols = {"Captain": {"flightAssignments": [
                {"flight": "L1", "employee": "C1"},
                {"flight": "L2", "employee": "C1"}]},
            "FirstOfficer": {"flightAssignments": [
                {"flight": "L1", "employee": "F1"},
                {"flight": "L2", "employee": "F1"}]}}
    merged = mr.merge_solutions(p, sols)
    stamped = [r for r in merged["flightAssignments"] if "flightLegId" in r]
    assert len(stamped) == 4                # карта не схлопнулась


def test_jobs_multirole_bad_params_raise_joberror():
    from webapp import jobs
    inst = {"id": "upload-u--x", "label": "x", "source": "upload",
            "payload_path": "data/x.json"}
    with pytest.raises(jobs.JobError, match="целым числом"):
        jobs._build_commands("multirole", inst, {"seed": "7; del *"})
    with pytest.raises(jobs.JobError, match="целым числом"):
        jobs._build_commands("stage1", inst, {"budget_min": "мусор"})


# ------------------------------------- инкремент 3: совместный режим (overlap)

def test_overlapping_ids_and_allow_overlap():
    p = _payload()
    assert rv.overlapping_ids(p) == set()
    p["employees"][0]["skills"] = ["Captain", "FirstOfficer"]
    assert rv.overlapping_ids(p) == {"C1"}
    with pytest.raises(rv.MultiroleUnsupported):      # дефолт строг
        rv.role_views(p)
    views = rv.role_views(p, allow_overlap=True)      # совместный режим
    assert "C1" in {e["id"] for e in views["Captain"]["employees"]}
    assert "C1" in {e["id"] for e in views["FirstOfficer"]["employees"]}


def test_build_joint_payload_prefixes_and_full_pool():
    p = _payload()
    p["employees"][0]["skills"] = ["Captain", "FirstOfficer"]
    views = rv.role_views(p, allow_overlap=True)
    catalog = [{"fns": ["L1", "L2"], "active_fns": ["L1", "L2"], "cost": 1}]
    roles = list(views)
    joint = mr.build_joint_payload(
        p, views, {r: catalog for r in roles}, roles)
    # по одной синтетической паре на роль, pid'ы префиксованы и уникальны
    fns = [f["flightNumber"] for f in joint["flights"]]
    assert len(fns) == 2 and len(set(fns)) == 2
    assert all(fn.startswith("MRJ") for fn in fns)
    # слоты ссылаются на префиксованные пары; навык = роль каталога
    skills = sorted(a["requiredSkill"] for a in joint["flightAssignments"])
    assert skills == ["Captain", "FirstOfficer"]
    ids = [a["id"] for a in joint["flightAssignments"]]
    assert len(set(ids)) == len(ids)
    # экипаж — ПОЛНЫЙ исходный пул (двойная квалификация в одном экземпляре)
    assert [e["id"] for e in joint["employees"]] == \
        [e["id"] for e in p["employees"]]


def test_split_joint_solved_round_trip():
    p = _payload()
    p["employees"][0]["skills"] = ["Captain", "FirstOfficer"]
    views = rv.role_views(p, allow_overlap=True)
    catalog = [{"fns": ["L1", "L2"], "active_fns": ["L1", "L2"], "cost": 1}]
    roles = list(views)
    joint = mr.build_joint_payload(
        p, views, {r: catalog for r in roles}, roles)
    solved = copy.deepcopy(joint)
    for a in solved["flightAssignments"]:
        a["employee"] = "C1" if a["requiredSkill"] == "Captain" else "F1"
    parts = mr.split_joint_solved(solved, roles)
    for role in roles:
        part = parts[role]
        assert [f["flightNumber"] for f in part["flights"]] == ["C_000000"]
        assert part["flights"][0].get("legIds") == ["L1", "L2"]
        a = part["flightAssignments"][0]
        assert a["flight"] == "C_000000"          # префикс отрезан
        assert a["requiredSkill"] == role
    # разрез в связке с expand + coverage замыкается на представлении
    cap = parts["Captain"]
    expanded = __import__("_gerad_stage2_roster").expand_to_flight_level(
        views["Captain"], cap)
    cov = coverage_check(views["Captain"], expanded)
    assert cov["fully_covered"]


# --------------------------------------------- инкремент 2: сводка результатов

def test_results_multirole_summary(tmp_path, monkeypatch):
    from webapp import results
    monkeypatch.setattr(results, "FINAL_REPORT", tmp_path)
    (tmp_path / "gerad_multirole_x.json").write_text(json.dumps({
        "label": "x", "roles": ["Captain", "FirstOfficer"],
        "lanes": {"Captain": {"lane": "x--role--captain",
                              "result_status": "OK"},
                  "FirstOfficer": {"lane": "x--role--firstofficer",
                                   "result_status": "OK"}},
        "merged": {"coverage": {"fully_covered": True, "missing": 0},
                   "physics": {"violations_total": 0}},
        "result_status": "OK", "checked_at": "2026-07-19 01:00:00"}))
    (tmp_path / "gerad_phase2_two_stage.json").write_text(json.dumps([
        {"label": "x", "result_status": "OK", "pref_hits": 5,
         "vacation_violations": 1, "unassigned": 0, "wall_s": 100,
         "stage2_score": "0hard/-10soft"},
        {"label": "x--role--captain", "result_status": "OK", "pref_hits": 4,
         "vacation_violations": 0, "unassigned": 0, "wall_s": 60,
         "stage2_score": "0hard/-4soft"}]))
    inst = {"id": "upload-u--x", "label": "x", "source": "upload"}
    mr = results._multirole(inst)
    assert mr["result_status"] == "OK"
    assert mr["colocation"]["pref_hits"] == 5          # со-локация в сравнении
    assert mr["lanes"]["Captain"]["metrics"]["pref_hits"] == 4
    assert mr["lanes"]["FirstOfficer"]["metrics"] is None   # lane без строки
    assert mr["merged"]["coverage"]["fully_covered"] is True
    # мультироль не запускалась -> None (карточка скрыта)
    inst2 = {"id": "upload-u--y", "label": "y", "source": "upload"}
    assert results._multirole(inst2) is None


def test_results_multirole_stale_journal_metrics_dropped(tmp_path,
                                                         monkeypatch):
    """Ревью (medium): упавший lane не переписывает журнальную строку —
    метрики предыдущего OK-прогона нельзя показывать под свежим INFEASIBLE:
    расхождение вердиктов = метрики отбрасываются."""
    from webapp import results
    monkeypatch.setattr(results, "FINAL_REPORT", tmp_path)
    (tmp_path / "gerad_multirole_x.json").write_text(json.dumps({
        "label": "x", "roles": ["Captain"],
        "lanes": {"Captain": {"lane": "x--role--captain",
                              "result_status": "INFEASIBLE_INCUMBENT"}},
        "merged": {}, "result_status": "INFEASIBLE_INCUMBENT"}))
    (tmp_path / "gerad_phase2_two_stage.json").write_text(json.dumps([
        {"label": "x--role--captain", "result_status": "OK",
         "pref_hits": 9}]))                         # строка ПРОШЛОГО прогона
    inst = {"id": "upload-u--x", "label": "x", "source": "upload"}
    mr = results._multirole(inst)
    assert mr["lanes"]["Captain"]["result_status"] == "INFEASIBLE_INCUMBENT"
    assert mr["lanes"]["Captain"]["metrics"] is None    # stale отброшены


def test_results_multirole_malformed_artifact_no_crash(tmp_path, monkeypatch):
    # Ревью: lanes-список/не-list roles в битом артефакте не роняют
    # /api/results целиком.
    from webapp import results
    monkeypatch.setattr(results, "FINAL_REPORT", tmp_path)
    (tmp_path / "gerad_multirole_x.json").write_text(json.dumps({
        "label": "x", "roles": "Captain", "lanes": ["broken"],
        "result_status": "OK"}))
    inst = {"id": "upload-u--x", "label": "x", "source": "upload"}
    mr = results._multirole(inst)                   # не бросает
    assert mr["lanes"] == {} and mr["roles"] == []


# ------------------------------------------------- зеркальный инвариант

def test_mirror_captain_view_is_verbatim_original_inst1():
    """Зеркальный инструмент: Captain-представление зеркала inst1 обязано
    БАЙТ-В-БАЙТ совпадать с исходной одночленной задачей (flights, employees,
    спрос). Это валидационный якорь фазы 2b без реального бенчмарка."""
    src = REPO / "data" / "gerad" / "payloads" / "inst1_p2.json"
    orig = json.loads(src.read_text(encoding="utf-8"))
    mirror = copy.deepcopy(orig)
    fo_emps = []
    for e in mirror["employees"]:
        fo = copy.deepcopy(e)
        fo["id"] = "FO_" + e["id"]
        fo["skills"] = ["FirstOfficer"]
        fo_emps.append(fo)
    mirror["employees"] = mirror["employees"] + fo_emps
    extra = []
    n = len(mirror["flightAssignments"])
    for a in list(mirror["flightAssignments"]):
        b = copy.deepcopy(a)
        n += 1
        b["id"] = str(n)
        b["indexInFlight"] = 1
        b["requiredSkill"] = "FirstOfficer"
        b["employee"] = None
        extra.append(b)
    mirror["flightAssignments"] = mirror["flightAssignments"] + extra

    views = rv.role_views(mirror)
    cap = views["Captain"]
    assert cap["flights"] == orig["flights"]
    assert cap["employees"] == orig["employees"]
    # спрос: те же места (в исходнике employee уже null, индекс уже 0)
    assert cap["flightAssignments"] == orig["flightAssignments"]
    # FO-представление изоморфно: та же структура, переименованный пул
    fo = views["FirstOfficer"]
    assert len(fo["flightAssignments"]) == len(orig["flightAssignments"])
    assert [e["id"] for e in fo["employees"]] == \
        ["FO_" + e["id"] for e in orig["employees"]]


# --- Аудит 2026-07-19 (P1-09): run-id вместо mtime ---------------------------


def test_fresh_rejects_prior_run_artifact_within_second(tmp_path, monkeypatch):
    # Старый допуск -1 c: артефакт ПРЕДЫДУЩЕГО прогона возрастом 0.75 c
    # принимался как свежий, и новый крах наследовал старый OK.
    import time as _t
    from _gerad_multirole import _fresh
    monkeypatch.setenv("CREW_RUN_ID", "run-new-0001")
    art = tmp_path / "gerad_stage2_assignment_x.json"
    art.write_text(json.dumps({"result_status": "OK",
                               "run_id": "run-old-9999"}), encoding="utf-8")
    since = _t.time() + 0.75          # «новый прогон стартовал позже записи»
    assert _fresh(art, since) is False


def test_fresh_accepts_own_run_id_regardless_of_mtime(tmp_path, monkeypatch):
    import os as _os
    import time as _t
    from _gerad_multirole import _fresh
    monkeypatch.setenv("CREW_RUN_ID", "run-new-0001")
    art = tmp_path / "a.json"
    art.write_text(json.dumps({"run_id": "run-new-0001"}), encoding="utf-8")
    old = _t.time() - 3600
    _os.utime(art, (old, old))        # время файла не доказательство
    assert _fresh(art, _t.time()) is True


def test_fresh_unstamped_artifact_strict_mtime(tmp_path, monkeypatch):
    # Фолбэк без штампа: строгий mtime >= since, допуска -1 c больше нет.
    import os as _os
    import time as _t
    from _gerad_multirole import _fresh
    monkeypatch.delenv("CREW_RUN_ID", raising=False)
    art = tmp_path / "a.json"
    art.write_text(json.dumps({"x": 1}), encoding="utf-8")
    now = _t.time()
    _os.utime(art, (now - 0.75, now - 0.75))
    assert _fresh(art, now) is False          # раньше проходил (допуск -1 c)
    assert _fresh(art, now - 2.0) is True


def test_jobs_artifact_completed_by_run_id(tmp_path, monkeypatch):
    from webapp import jobs, registry
    monkeypatch.setattr(registry, "FINAL_REPORT", tmp_path)
    inst = {"label": "ridtest"}
    art = tmp_path / "gerad_stage2_assignment_ridtest.json"
    art.write_text(json.dumps({"result_status": "OK",
                               "run_id": "run-a"}), encoding="utf-8")
    import time as _t
    started = _t.time() + 0.5   # артефакт записан ДО старта — mtime бы принял
    assert jobs._stage2_artifact_completed(inst, started, "stage2",
                                           run_id="run-a") is True
    assert jobs._stage2_artifact_completed(inst, started, "stage2",
                                           run_id="run-b") is False


def test_jobs_artifact_completed_by_run_manifest(tmp_path, monkeypatch):
    # P0-1: не-OK прогон не переписывает канонический артефакт — признак
    # завершения гейта = per-run манифест <run_id>.json
    import _roster_manifest as rm
    from webapp import jobs, registry
    monkeypatch.setattr(registry, "FINAL_REPORT", tmp_path)
    monkeypatch.setattr(rm, "FINAL_REPORT", tmp_path)
    inst = {"label": "mfsttest"}
    mdir = rm.run_manifest_dir("mfsttest")
    mdir.mkdir(parents=True)
    (mdir / "run-x.json").write_text(
        json.dumps({"result_status": "INFEASIBLE_INCUMBENT"}),
        encoding="utf-8")
    import time as _t
    started = _t.time() + 5.0            # канонического артефакта нет вовсе
    assert jobs._stage2_artifact_completed(inst, started, "stage2",
                                           run_id="run-x") is True
    assert jobs._stage2_artifact_completed(inst, started, "stage2",
                                           run_id="run-y") is False
    # multirole-jtype манифестом НЕ доказывается (читает свой сводный файл)
    assert jobs._stage2_artifact_completed(inst, started, "multirole",
                                           run_id="run-x") is False
