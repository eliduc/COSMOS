"""Audit 2026-07-12, rounds 5-6: the stage-2 promotion gate.

Round 6 semantics: OK only for a COMPLETE clean roster. Missing legs that
are individually proven impossible give INFEASIBLE_INPUT_OR_MODEL (a
property of the input/space, never a valid roster). Everything else —
hard violations, unassigned pairings, unexplained missing legs,
duplicates, trajectory violations, unparseable score — fails closed to
INFEASIBLE_INCUMBENT."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _gerad_stage2_roster import gate_result_status  # noqa: E402


def _cov(missing=(), dups=0, unknown=0):
    return {"missing_legs": list(missing), "duplicates": dups,
            "unknown_flights": unknown}


def test_clean_complete_incumbent_is_ok():
    assert gate_result_status("0hard/-5soft", 0, _cov(), set(),
                              trajectory_violations=0) == "OK"


def test_hard_violations_fail_closed():
    assert gate_result_status("-3hard/-5soft", 0, _cov(), set(),
                              0) == "INFEASIBLE_INCUMBENT"


def test_unassigned_fails_closed():
    assert gate_result_status("0hard/-5soft", 2, _cov(), set(),
                              0) == "INFEASIBLE_INCUMBENT"


def test_unparseable_score_fails_closed():
    assert gate_result_status(None, 0, _cov(), set(),
                              0) == "INFEASIBLE_INCUMBENT"


def test_trajectory_violations_fail_closed():
    # Round 6: the gate must consume the trajectory validator.
    assert gate_result_status("0hard/0soft", 0, _cov(), set(),
                              trajectory_violations=5) == \
        "INFEASIBLE_INCUMBENT"


def test_trajectory_certificate_error_fails_closed():
    # A failed artifact certificate is reported as -1 violations.
    assert gate_result_status("0hard/0soft", 0, _cov(), set(),
                              trajectory_violations=-1) == \
        "INFEASIBLE_INCUMBENT"


def test_missing_beyond_proven_impossible_fails_closed():
    assert gate_result_status("0hard/0soft", 0, _cov(missing=["LEG_X"]),
                              {"LEG_Y"}, 0) == "INFEASIBLE_INCUMBENT"


def test_missing_proven_impossible_is_input_or_model_not_ok():
    # Round 6: proven-impossible legs mean the input or the chosen space
    # is incompatible with the rules — NEVER a valid full roster.
    assert gate_result_status("0hard/0soft", 0, _cov(missing=["LEG_X"]),
                              {"LEG_X"}, 0) == "INFEASIBLE_INPUT_OR_MODEL"


def test_missing_proven_impossible_with_dirty_part_fails_closed():
    # Proven-impossible missing legs do not excuse a dirty assigned part.
    assert gate_result_status("-1hard/0soft", 0, _cov(missing=["LEG_X"]),
                              {"LEG_X"}, 0) == "INFEASIBLE_INCUMBENT"


def test_duplicates_fail_closed():
    assert gate_result_status("0hard/0soft", 0, _cov(dups=1), set(),
                              0) == "INFEASIBLE_INCUMBENT"


def test_composition_violations_fail_closed():
    # Аудит 2026-07-19 (P0-02): включённое правило состава — вход гейта,
    # предупреждение несовместимо с семантикой OK.
    assert gate_result_status("0hard/0soft", 0, _cov(), set(),
                              trajectory_violations=0,
                              composition_violations=1) == \
        "INFEASIBLE_INCUMBENT"


def test_composition_certificate_error_fails_closed():
    # Не построился сертификат состава (-1) — тоже брак, не OK.
    assert gate_result_status("0hard/0soft", 0, _cov(), set(),
                              trajectory_violations=0,
                              composition_violations=-1) == \
        "INFEASIBLE_INCUMBENT"


def test_composition_disabled_keeps_ok():
    # None = правила выключены/неприменимы — вердикт как раньше.
    assert gate_result_status("0hard/0soft", 0, _cov(), set(),
                              trajectory_violations=0,
                              composition_violations=None) == "OK"


# --- P0-1 (аудит документации 2026-07-20): гейт публикации ------------------


import json                                      # noqa: E402

import _gerad_stage2_roster as s2                # noqa: E402


def _pendings(tmp_path):
    ap_final = tmp_path / "gerad_stage2_assignment_x.json"
    sol_final = tmp_path / "p2_two_stage_solution_x.json"
    ap_p = ap_final.with_suffix(".pending.json")
    sol_p = sol_final.with_suffix(".pending.json")
    ap_p.write_text(json.dumps({"result_status": "?", "pairings": []}))
    sol_p.write_text("{}", encoding="utf-8")
    return ap_p, sol_p, ap_final, sol_final


def test_publish_ok_replaces_canonical_and_promotes(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "HERE", tmp_path)
    ap_p, sol_p, ap_f, sol_f = _pendings(tmp_path)
    pa, ps, promote = s2.publish_stage2_artifacts(
        "inst9", "OK", ap_p, sol_p, ap_f, sol_f)
    assert (pa, ps, promote) == (ap_f, sol_f, True)
    assert ap_f.exists() and sol_f.exists()
    assert not ap_p.exists() and not sol_p.exists()
    # OK-путь диагностику не пишет
    assert not (tmp_path / "final-report" / "runs"
                / "stage2-diagnostics").exists()


def test_publish_nonok_keeps_canonical_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "HERE", tmp_path)
    ap_p, sol_p, ap_f, sol_f = _pendings(tmp_path)
    ap_f.write_text('{"old": true}', encoding="utf-8")   # прежний годный
    pa, ps, promote = s2.publish_stage2_artifacts(
        "inst9", "INFEASIBLE_INCUMBENT", ap_p, sol_p, ap_f, sol_f)
    assert promote is False
    assert pa != ap_f and pa.exists() and ps.exists()
    assert "stage2-diagnostics" in str(pa)
    # канонический файл НЕ тронут, pending-огрызков нет
    assert json.loads(ap_f.read_text(encoding="utf-8")) == {"old": True}
    assert not ap_p.exists() and not sol_p.exists()


def test_publish_nonok_without_previous_roster_stays_diagnostic(
        tmp_path, monkeypatch):
    # fail-closed: первый прогон derived-метки с не-OK НЕ создаёт
    # канонического файла — доска честно «absent», подмены нет
    monkeypatch.setattr(s2, "HERE", tmp_path)
    ap_p, sol_p, ap_f, sol_f = _pendings(tmp_path)
    pa, ps, promote = s2.publish_stage2_artifacts(
        "derived7", "REPAIR_GREEDY", ap_p, sol_p, ap_f, sol_f)
    assert promote is False
    assert not ap_f.exists() and not sol_f.exists()
    assert pa.exists()


def test_publish_lane_writes_canonical_but_never_promotes(
        tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "HERE", tmp_path)
    ap_p, sol_p, ap_f, sol_f = _pendings(tmp_path)
    pa, ps, promote = s2.publish_stage2_artifacts(
        "acme--role--captain", "INFEASIBLE_INCUMBENT",
        ap_p, sol_p, ap_f, sol_f)
    # lane: канонические файлы = IPC оркестратора — пишутся при любом
    # вердикте, промоции нет
    assert (pa, ps, promote) == (ap_f, sol_f, False)
    assert ap_f.exists()
    ap_p2, sol_p2, ap_f2, sol_f2 = _pendings(tmp_path)
    pa2, ps2, promote2 = s2.publish_stage2_artifacts(
        "acme--role--captain", "OK", ap_p2, sol_p2, ap_f2, sol_f2)
    assert promote2 is False


def test_declared_rule_that_never_reached_the_engine_blocks_ok():
    """Правило, объявленное в Пакете правил, но не дошедшее до движка,
    делает «ноль жёстких нарушений» утверждением о ДРУГОЙ задаче: решатель
    о правиле не знал. Такой прогон не может быть OK
    (аудит 2026-07-26, P0-04)."""
    from _gerad_stage2_roster import gate_result_status
    clean = {"missing_legs": [], "duplicates": 0, "unknown_flights": 0}
    assert gate_result_status("0hard/0soft", 0, clean, set()) == "OK"
    assert gate_result_status("0hard/0soft", 0, clean, set(),
                              rules_not_applied=["weeklyRest"]) \
        == "INFEASIBLE_INCUMBENT"
    # пустой список — правил не объявлено, поведение прежнее
    assert gate_result_status("0hard/0soft", 0, clean, set(),
                              rules_not_applied=[]) == "OK"


def test_resource_rules_compiler_maps_tier2b_to_the_engine_document():
    """Ярус 2б -> документ CREW_RESOURCE_RULES. Заявленное, но
    неисполнимое перечисляется поимённо: молчаливое «правило есть в
    пакете, но не в движке» — это подмена постановки."""
    from webapp import rule_packs
    pack = {"rules": [
        {"title": "еженедельный отдых",
         "constraint": {"kind": "resource", "resource": "weeklyRest",
                        "window": {"min": 2880, "max": None},
                        "unit": "minutes", "level": "week"}},
        {"title": "посадки за смену",
         "constraint": {"kind": "resource", "resource": "landings",
                        "window": {"min": None, "max": 5},
                        "unit": "count", "level": "duty"}},
    ]}
    comp = rule_packs.compile_resource_rules(pack)
    assert comp["document"] == {"weeklyRest": {"requiredMin": 2880,
                                               "periodMin": 7 * 1440}}
    assert comp["declared"] == ["landings", "weeklyRest"]
    assert comp["not_executable"] == ["landings"]
    # пакет без ресурсных правил документа не даёт
    assert rule_packs.compile_resource_rules({"rules": []})["document"] is None


def test_declared_minus_applied_is_per_rule_not_per_file(tmp_path):
    """Повторный аудит 2026-07-26 (P0-04): наличие ЛЮБОГО файла ресурсных
    правил обнуляло весь список объявленных — «еженедельный отдых +
    посадки» при файле с одним отдыхом давали OK, а посадки исчезали из
    проверки молча. Сверка обязана быть поэлементной."""
    from _gerad_stage2_roster import _declared_minus_applied

    rules = tmp_path / "rules.json"
    rules.write_text('{"weeklyRest": {"requiredMin": 720}}', encoding="utf-8")
    # файл закрывает ровно то правило, что в нём лежит
    assert _declared_minus_applied("weeklyRest,landings", str(rules)) \
        == ["landings"]
    assert _declared_minus_applied("weeklyRest", str(rules)) == []
    # нет файла = не применено ничего
    assert _declared_minus_applied("weeklyRest,landings", None) \
        == ["landings", "weeklyRest"]
    # битый файл считается пустым, а не «каким-то»
    broken = tmp_path / "broken.json"
    broken.write_text("{оборвано", encoding="utf-8")
    assert _declared_minus_applied("weeklyRest", str(broken)) \
        == ["weeklyRest"]
    # ничего не объявлено — нечего и требовать
    assert _declared_minus_applied("", str(rules)) == []
    assert _declared_minus_applied(None, None) == []


def test_declared_minus_applied_through_the_real_transport(monkeypatch):
    """ТРЕТИЙ аудит 2026-07-26 (§5.1): продуктовый поток кладёт в
    CREW_RESOURCE_RULES сериализованный JSON (`run_one`:
    os.environ[...] = json.dumps(rr)), а сверка читала значение как путь —
    реально применённое правило объявлялось неприменённым, и корректный
    прогон не мог получить OK. Тест-предшественник был зелёным, потому что
    подавал путь руками. Этот — повторяет НАСТОЯЩИЙ транспорт."""
    import json as _json
    import os as _os

    from _gerad_stage2_roster import _declared_minus_applied

    # ровно то, что делает run_one перед запуском решателя
    rr = {"weeklyRest": {"requiredMin": 720, "periodMin": 10080}}
    monkeypatch.setenv("CREW_RESOURCE_RULES",
                       _json.dumps(rr, ensure_ascii=False))
    monkeypatch.setenv("CREW_RULES_DECLARED_RESOURCES", "weeklyRest,landings")
    left = _declared_minus_applied(
        _os.environ.get("CREW_RULES_DECLARED_RESOURCES"),
        _os.environ.get("CREW_RESOURCE_RULES"))
    assert left == ["landings"], \
        "применённый через продуктовый транспорт weeklyRest не должен " \
        "числиться неприменённым"


def test_publish_input_or_model_replaces_canonical_and_promotes(
        tmp_path, monkeypatch):
    """Решение владельца 2026-08-12 (вечер): INFEASIBLE_INPUT_OR_MODEL
    публикуется и промотируется — по образцу каталога пейринга
    (OK | INFEASIBLE_INPUT). Рассаженная часть безупречна, непокрыто
    только доказуемо непокрываемое: без публикации этого вердикта на
    данных с непокрываемыми рейсами активный ростер недостижим вовсе.
    Имя вердикта в артефакте остаётся честным."""
    monkeypatch.setattr(s2, "HERE", tmp_path)
    ap_p, sol_p, ap_f, sol_f = _pendings(tmp_path)
    pa, ps, promote = s2.publish_stage2_artifacts(
        "inst9", "INFEASIBLE_INPUT_OR_MODEL", ap_p, sol_p, ap_f, sol_f)
    assert (pa, ps, promote) == (ap_f, sol_f, True)
    assert ap_f.exists() and sol_f.exists()
    assert not ap_p.exists() and not sol_p.exists()
    # публикуемый путь диагностику не дублирует
    assert not (tmp_path / "final-report" / "runs"
                / "stage2-diagnostics").exists()


def test_publish_input_or_model_lane_never_promotes(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "HERE", tmp_path)
    ap_p, sol_p, ap_f, sol_f = _pendings(tmp_path)
    _pa, _ps, promote = s2.publish_stage2_artifacts(
        "inst9--role--ca", "INFEASIBLE_INPUT_OR_MODEL",
        ap_p, sol_p, ap_f, sol_f)
    assert promote is False
