"""Stage-2 generalization to uploaded instances (unit level, NO solver runs).

Covers, per the wave-2 scope:
  - argparse round-trip of the generic flags (--payload/--catalog/--label)
    and 100% backward compatibility of the numeric positional CLI;
  - artifact-name derivation by label (numeric labels 'instN' keep the
    historical names byte-for-byte);
  - journal merge keyed by label-or-inst (generic rows carry
    {"label": slug, "inst": null});
  - webapp/jobs.py command builder: generic stage2/validate commands for an
    upload instance and the CREW_RULES_CONFIG env wiring for EVERY job;
  - webapp/registry.py: stage2_available flips for an upload once its label
    has a catalog (best preferred over the canonical selected dump).
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from webapp import config_scope

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from _gerad_stage2_roster import (  # noqa: E402
    artifact_paths, journal_key, merge_journal, parse_args)
from webapp import jobs, registry  # noqa: E402

MINIMAL_PAYLOAD = {
    "formatVersion": "1",
    "airports": [{"code": "AAA"}, {"code": "BBB"}],
    "employees": [{"id": "E1", "homeAirport": "AAA", "skills": ["Pilot"]}],
    "flights": [{
        "flightNumber": "F1",
        "departureAirport": "AAA",
        "departureUTCDateTime": "2026-07-01T06:00:00Z",
        "arrivalAirport": "BBB",
        "arrivalUTCDateTime": "2026-07-01T08:00:00Z",
    }],
    "flightAssignments": [{"id": "F1-0", "flight": "F1", "indexInFlight": 0,
                           "requiredSkill": "Pilot"}],
}


# --- argparse round-trip ------------------------------------------------------

def test_parse_args_generic_full():
    cfg = parse_args(["--payload", "data/uploads/acme.json",
                      "--catalog", "final-report/gerad_colgen_selected_acme.json",
                      "--catalog-fp", "a" * 64,
                      "--label", "acme", "900", "3"])
    assert cfg["mode"] == "generic"
    assert cfg["payload_path"] == Path("data/uploads/acme.json")
    assert cfg["catalog_path"] == \
        Path("final-report/gerad_colgen_selected_acme.json")
    assert cfg["catalog_fp"] == "a" * 64
    assert cfg["label"] == "acme"
    assert cfg["budget_s"] == 900
    assert cfg["seed"] == 3


def test_parse_args_generic_default_budget_seed():
    cfg = parse_args(["--payload", "p.json", "--catalog", "c.json",
                      "--catalog-fp", "b" * 64,
                      "--label", "my-airline-2"])
    assert cfg["mode"] == "generic"
    assert (cfg["budget_s"], cfg["seed"]) == (600, 7)


def test_parse_args_catalog_without_fp_rejected():
    """Аудит R10 (отсрочка №3): каталог без контент-отпечатка не
    принимается — подмена файла между постановкой и чтением была бы
    невидима."""
    with pytest.raises(SystemExit):
        parse_args(["--payload", "p.json", "--catalog", "c.json",
                    "--label", "my-airline-2"])


def test_parse_args_numeric_backward_compatible():
    # The historical positional CLI, unchanged.
    cfg = parse_args(["2", "5", "300", "9"])
    assert cfg == {"mode": "numeric", "lo": 2, "hi": 5,
                   "source_rules_path": None,
                   "source_rules_fp": None,
                   "source_rules_strict": False,
                   "budget_s": 300, "seed": 9}


def test_parse_args_numeric_defaults():
    assert parse_args([]) == {"mode": "numeric", "lo": 1, "hi": 7,
                              "source_rules_path": None,
                              "source_rules_fp": None,
                              "source_rules_strict": False,
                              "budget_s": 600, "seed": 7}
    assert parse_args(["4"])["lo"] == 4
    assert parse_args(["4"])["hi"] == 7


def test_parse_args_partial_generic_flags_rejected():
    # Loud failure: generic mode is all-or-nothing.
    for argv in (["--payload", "p.json"],
                 ["--payload", "p.json", "--catalog", "c.json"],
                 ["--label", "acme"]):
        with pytest.raises(SystemExit):
            parse_args(argv)


def test_parse_args_bad_labels_rejected():
    base = ["--payload", "p.json", "--catalog", "c.json", "--label"]
    for label in ("My Airline", "acme_1", "", "inst3"):
        with pytest.raises(SystemExit):
            parse_args(base + [label])


def test_parse_args_generic_extra_positionals_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--payload", "p.json", "--catalog", "c.json",
                    "--label", "acme", "600", "7", "1"])


# --- artifact-name derivation by label ---------------------------------------

def test_artifact_paths_by_label():
    # №50 этап 2: артефакты живут в каталоге данных, не в репозитории
    import _data_paths
    p = artifact_paths("acme")
    assert p["assignment"].name == "gerad_stage2_assignment_acme.json"
    assert p["assignment"].parent == _data_paths.results_dir(create=False)
    assert p["solution"].name == "p2_two_stage_solution_acme.json"
    assert p["solution"].parent == _data_paths.gerad_data_dir(create=False)


def test_artifact_paths_numeric_label_keeps_historical_names():
    import _data_paths
    p = artifact_paths("inst3")
    assert p["assignment"] == _data_paths.results_dir(create=False) \
        / "gerad_stage2_assignment_inst3.json"
    assert p["solution"] == _data_paths.gerad_data_dir(create=False) \
        / "p2_two_stage_solution_inst3.json"


# --- journal merge keyed by label-or-inst -------------------------------------

def test_journal_keys_are_disjoint_namespaces():
    numeric = {"inst": 3, "result_status": "OK"}
    generic = {"inst": None, "label": "acme", "result_status": "OK"}
    assert journal_key(numeric) == ("inst", 3)
    assert journal_key(generic) == ("label", "acme")
    assert journal_key(numeric) != journal_key(generic)


def test_merge_journal_replaces_by_label_and_keeps_inst_rows():
    prior = [{"inst": 3, "result_status": "OK"},
             {"inst": None, "label": "acme", "result_status": "OK"}]
    new_generic = {"inst": None, "label": "acme",
                   "result_status": "INFEASIBLE_INCUMBENT"}
    merged = merge_journal(prior, new_generic)
    assert len(merged) == 2
    assert {"inst": 3, "result_status": "OK"} in merged
    assert merged[-1] is new_generic
    acme_rows = [r for r in merged if r.get("label") == "acme"]
    assert acme_rows == [new_generic]


def test_merge_journal_replaces_by_inst_and_keeps_label_rows():
    prior = [{"inst": 3, "result_status": "OK"},
             {"inst": None, "label": "acme", "result_status": "OK"}]
    new_numeric = {"inst": 3, "result_status": "INFEASIBLE_INPUT_OR_MODEL"}
    merged = merge_journal(prior, new_numeric)
    assert len(merged) == 2
    assert {"inst": None, "label": "acme", "result_status": "OK"} in merged
    inst_rows = [r for r in merged if r.get("inst") == 3]
    assert inst_rows == [new_numeric]


def test_merge_journal_tolerates_non_list_prior():
    row = {"inst": None, "label": "acme", "result_status": "OK"}
    assert merge_journal(None, row) == [row]


# --- registry gate status keyed by label --------------------------------------

def test_gate_status_by_label_handles_generic_rows(monkeypatch):
    rows = [{"inst": 1, "result_status": "OK"},
            {"inst": None, "label": "acme",
             "result_status": "INFEASIBLE_INCUMBENT"},
            {"inst": 1, "result_status": "INFEASIBLE_INPUT_OR_MODEL"},
            {"inst": None, "label": None},          # malformed: ignored
            "not-a-dict"]
    monkeypatch.setattr(
        registry, "_load_json_cached",
        lambda p: rows if p == registry.TWO_STAGE_JOURNAL else None)
    gate = registry._gate_status_by_label()
    assert gate["inst1"] == "INFEASIBLE_INPUT_OR_MODEL"   # later rows win
    assert gate["acme"] == "INFEASIBLE_INCUMBENT"
    assert None not in gate


# --- jobs.py: command builder + env -------------------------------------------

UPLOAD_INST = {
    "id": "upload-acme", "label": "acme", "source": "upload",
    "payload_path": "data/uploads/acme.json",
    "catalog_path": "final-report/gerad_colgen_selected_acme.json",
    "stage2_available": True,
}


def test_stage2_command_generic_for_upload(tmp_path, monkeypatch):
    # Каталог обязан существовать: команда несёт его контент-отпечаток
    # (аудит R10, отсрочка №3), и jobs хэширует файл при постановке.
    # Пути входов уходят в решателя РАЗРЕШЁННЫМИ (2026-08-13): логическая
    # запись реестра развернулась бы у дочернего процесса от его рабочего
    # каталога и на контуре с вынесенными данными не нашла бы файл —
    # см. tests/test_jobs_child_paths.py.
    cat = tmp_path / "gerad_colgen_selected_acme.json"
    cat.write_text('{"selected": []}', encoding="utf-8")
    pay = tmp_path / "acme.json"
    pay.write_text("{}", encoding="utf-8")
    real = registry.resolve_rel

    def fake_resolve(rel):
        if str(rel).endswith("gerad_colgen_selected_acme.json"):
            return cat
        if str(rel).endswith("uploads/acme.json"):
            return pay
        return real(rel)

    monkeypatch.setattr(registry, "resolve_rel", fake_resolve)
    cmds = jobs._build_commands("stage2", dict(UPLOAD_INST),
                                {"budget_s": 120, "seed": 3})
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[1] == "_gerad_stage2_roster.py"
    for flag, value in (("--payload", str(pay)),
                        ("--catalog", str(cat)),
                        ("--label", "acme")):
        assert cmd[cmd.index(flag) + 1] == value
    import hashlib as _hl
    assert cmd[cmd.index("--catalog-fp") + 1] == _hl.sha256(
        cat.read_bytes()).hexdigest()
    # budget_s and seed ride as the generic positionals.
    assert cmd[-2:] == ["120", "3"]


def test_stage2_command_numeric_for_gerad_unchanged():
    inst = {"id": "gerad-3", "label": "inst3", "source": "gerad",
            "payload_path": "data/gerad/payloads/inst3_p2.json",
            "catalog_path": "final-report/best/gerad_best_catalog_inst3.json"}
    cmds = jobs._build_commands("stage2", inst, {"budget_s": 60, "seed": 5})
    assert cmds == [[str(jobs.VENV_PYTHON), "_gerad_stage2_roster.py",
                     "3", "3", "60", "5"]]


def test_stage2_command_rejected_without_catalog():
    inst = dict(UPLOAD_INST, catalog_path=None, stage2_available=False)
    with pytest.raises(jobs.JobError) as exc:
        jobs._build_commands("stage2", inst, {})
    assert exc.value.http_status == 400
    assert "catalog" in str(exc.value)


def test_validate_command_rejected_without_stage2_artifacts():
    # Honest 400: nothing to validate before stage 2 has published artifacts.
    inst = dict(UPLOAD_INST, label="pytest-never-ran")
    inst["catalog_path"] = None
    with pytest.raises(jobs.JobError) as exc:
        jobs._build_commands("validate", inst, {})
    assert exc.value.http_status == 400
    assert "no stage-2 artifacts yet" in str(exc.value)


def test_validate_command_generic_with_artifacts(tmp_path):
    label = "pytest-s2gen-val"
    import _data_paths
    traj_p = registry.FINAL_REPORT / f"gerad_stage2_assignment_{label}.json"
    sol_p = _data_paths.gerad_data_dir() \
        / f"p2_two_stage_solution_{label}.json"
    cat_p = registry.FINAL_REPORT / f"gerad_colgen_selected_{label}.json"
    inst = {"id": f"upload-{label}", "label": label, "source": "upload",
            "payload_path": f"data/uploads/{label}.json",
            "catalog_path": f"final-report/gerad_colgen_selected_{label}.json"}
    try:
        traj_p.write_text("{}", encoding="utf-8")
        sol_p.write_text("{}", encoding="utf-8")
        cat_p.write_text("{}", encoding="utf-8")
        cmds = jobs._build_commands("validate", inst, {})
        # Аудит 2026-07-19 (P2-01): generic validate обязан запускать ОБА
        # сертификата — coverage (членство/навыки/призраки) + trajectory
        # linkage, как числовая ветка.
        assert len(cmds) == 2
        cov_inline = cmds[0][2]
        assert cmds[0][1] == "-c"
        assert "from _coverage_validator import check" in cov_inline
        # Доказанно невозможные участки каталога — свойство входа, не дефект
        # ростера (ревью пакета): INFEASIBLE_INPUT_OR_MODEL не валит validate.
        # Аудит A4 (04.09.2026): прощение только по доказательству с
        # отпечатком данных (proven_exclusions), не по голому списку имён.
        assert "proven_exclusions" in cov_inline
        assert "excluded_proven" in cov_inline
        assert "skill_mismatches" in cov_inline
        assert "unknown_employees" in cov_inline
        assert cmds[1][1] == "-c"
        inline = cmds[1][2]
        # The inline runner must call check_inst with ALL four explicit paths.
        assert "check_inst" in inline
        assert repr(label) in inline
        for kw in ("traj_path=", "sol_path=", "catalog_path=",
                   "payload_path="):
            assert kw in inline
    finally:
        for p in (traj_p, sol_p, cat_p):
            p.unlink(missing_ok=True)


def test_every_job_env_gets_rules_config(monkeypatch, tmp_path):
    # (a) the repo config exists -> the variable points at its abs path;
    cfg = tmp_path / "airline_default.json"
    cfg.write_text(json.dumps({"parameters": {"min_sit_min": 30}}),
                   encoding="utf-8")
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_RULES, cfg)
    env = jobs._base_env()
    assert env["CREW_RULES_CONFIG"] == str(cfg.resolve())
    # (b) no config file -> no variable (engines keep their built-ins).
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_RULES,
                        tmp_path / "absent.json")
    env = jobs._base_env()
    assert "CREW_RULES_CONFIG" not in env \
        or env["CREW_RULES_CONFIG"] == os.environ.get("CREW_RULES_CONFIG")


# --- registry: stage2_available flips with a catalog ---------------------------

def _isolate_registry_dirs(monkeypatch, tmp_path):
    """Каталоги реестра — во временную папку.

    Прежде эти тесты писали выгрузку в НАСТОЯЩИЙ `data/uploads` и удаляли
    её в `finally`. Пока прогон один, это незаметно; но два прогона на
    одном рабочем дереве — и соседний процесс, перечисляя экземпляры,
    ловит файл ровно в момент удаления: `list_instances()` падал
    `FileNotFoundError`, а вместе с ним витрина показателей
    (`test_dashboard_kpi_tab_builds`). Причина доказана воспроизведением
    2026-07-26. Теперь тест не касается рабочих каталогов вовсе.
    """
    root = tmp_path / "repo"
    uploads = root / "data" / "uploads"
    final_report = root / "final-report"
    best = final_report / "best"
    for d in (uploads, best):
        d.mkdir(parents=True, exist_ok=True)
    # REPO_ROOT задаёт корень для путей, которые реестр отдаёт наружу
    # (`payload_path`, `catalog_path`), поэтому переносим и его; вместе с
    # ним отключаем эталонные наборы — иначе их настоящие пути окажутся
    # вне нового корня. В поле зрения теста остаётся только его выгрузка.
    monkeypatch.setattr(registry, "REPO_ROOT", root)
    monkeypatch.setattr(registry, "GERAD_PAYLOADS", root / "data" / "gerad")
    monkeypatch.setattr(registry, "BTS_PAYLOAD", root / "data" / "bts.json")
    monkeypatch.setattr(registry, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(registry, "FINAL_REPORT", final_report)
    monkeypatch.setattr(registry, "BEST_DIR", best)
    # Плоский файл выгрузки — ПРЕЖНЯЯ раскладка: обход дописывает строку
    # владения (переезд на арендаторов). Карту владения тоже во временную
    # папку, чтобы тест не писал в настоящий webapp/config.
    monkeypatch.setattr(registry, "OWNERSHIP_PATH",
                        tmp_path / "ownership.json")
    return uploads, final_report, best


def test_registry_upload_stage2_flips_with_catalog(monkeypatch, tmp_path):
    label = "pytest-s2gen-reg"
    uploads, final_report, best = _isolate_registry_dirs(monkeypatch, tmp_path)
    upload_p = uploads / f"{label}.json"
    selected_p = final_report / f"gerad_colgen_selected_{label}.json"
    best_p = best / f"gerad_best_catalog_{label}.json"
    upload_p.write_text(json.dumps(MINIMAL_PAYLOAD), encoding="utf-8")

    def find():
        matches = [i for i in registry.list_instances()
                   if i["id"] == f"upload-{label}"]
        assert matches, f"upload-{label} missing from the registry"
        return matches[0]

    # No catalog yet -> stage 2 not available.
    inst = find()
    assert inst["stage2_available"] is False
    assert inst["catalog_path"] is None

    # Stage-1 promotion writes the canonical selected dump (современное
    # продвижение всегда несёт блок coverage — R6, P1-15) -> flips.
    ok_doc = {"selected": [], "coverage": {"verdict": "OK", "uncovered": 0,
                                           "not_built": 0}}
    selected_p.write_text(json.dumps(ok_doc), encoding="utf-8")
    inst = find()
    assert inst["stage2_available"] is True
    assert inst["catalog_path"] == \
        f"final-report/gerad_colgen_selected_{label}.json"

    # A best catalog is preferred over the canonical dump.
    best_p.write_text(json.dumps(ok_doc), encoding="utf-8")
    inst = find()
    assert inst["stage2_available"] is True
    assert inst["catalog_path"] == \
        f"final-report/best/gerad_best_catalog_{label}.json"
    assert inst["has_best_catalog"] is True


def test_registry_refuses_a_catalog_whose_search_fell_short(monkeypatch,
                                                            tmp_path):
    """Готовность этапа 2 определялась одним фактом существования файла,
    и частичный каталог молча становился рабочим (аудит 2026-07-26,
    P0-05). Каталог со свидетельством «перебор не достроил» готовности не
    даёт; каталог БЕЗ блока покрытия годен только с meta продвижения с тем
    же отпечатком (внешний аудит R6, P1-15) — произвольный файл без
    свидетельства больше не публикуется."""
    label = "pytest-s2gen-cov"
    uploads, final_report, _ = _isolate_registry_dirs(monkeypatch, tmp_path)
    upload_p = uploads / f"{label}.json"
    selected_p = final_report / f"gerad_colgen_selected_{label}.json"
    upload_p.write_text(json.dumps(MINIMAL_PAYLOAD), encoding="utf-8")

    def find():
        return next(i for i in registry.list_instances()
                    if i["id"] == f"upload-{label}")

    # перебор не достроил цепочку для достижимого участка
    selected_p.write_text(json.dumps(
        {"selected": [], "coverage": {"verdict": "INCOMPLETE_SEARCH",
                                      "uncovered": 3, "not_built": 3}}),
        encoding="utf-8")
    inst = find()
    assert inst["stage2_available"] is False
    assert inst["catalog_path"] is None

    # непокрываемое В ПРИНЦИПЕ публикацию не блокирует
    selected_p.write_text(json.dumps(
        {"selected": [], "coverage": {"verdict": "INFEASIBLE_INPUT",
                                      "uncovered": 3, "not_built": 0}}),
        encoding="utf-8")
    assert find()["stage2_available"] is True

    # каталог прежнего выпуска БЕЗ свидетельства — больше не публикуется
    selected_p.write_text(json.dumps({"selected": []}), encoding="utf-8")
    assert find()["stage2_available"] is False

    # ...а с meta продвижения с тем же отпечатком — публикуется: это
    # настоящий прежний best, прошедший гейт продвижения
    selected_p.write_text(json.dumps(
        {"selected": [], "label": label, "fingerprint": "fp-1",
         "ip_objective": 12345}),
        encoding="utf-8")
    best_dir = selected_p.parent / "best"
    best_dir.mkdir(exist_ok=True)
    (best_dir / f"meta_{label}.json").write_text(json.dumps(
        {"label": label, "fingerprint": "fp-1", "ip_objective": 12345,
         "covered_legs_semantics": "all except proven-impossible"}),
        encoding="utf-8")
    assert find()["stage2_available"] is True

    # чужой отпечаток в meta свидетельством не является
    (best_dir / f"meta_{label}.json").write_text(json.dumps(
        {"label": label, "fingerprint": "fp-OTHER",
         "ip_objective": 12345,
         "covered_legs_semantics": "all except proven-impossible"}),
        encoding="utf-8")
    assert find()["stage2_available"] is False

    # НЕИЗВЕСТНЫЙ вердикт не имеет права сходить за годный (повторный
    # аудит 2026-07-26): прежнее `!= INCOMPLETE_SEARCH` пропускало
    # опечатку, будущее значение и повреждённое поле — любой мусор в
    # свидетельстве автоматически получал привилегию годного
    selected_p.write_text(json.dumps(
        {"selected": [], "coverage": {"verdict": "ОПЕЧАТКА"}}),
        encoding="utf-8")
    assert find()["stage2_available"] is False
    selected_p.write_text(json.dumps(
        {"selected": [], "coverage": {"verdict": None}}),
        encoding="utf-8")
    assert find()["stage2_available"] is False


def test_list_instances_survives_an_upload_deleted_mid_scan(monkeypatch,
                                                            tmp_path):
    """Выгрузка исчезла между обходом каталога и чтением файла.

    Так бывает при удалении набора параллельно с открытым экраном списка
    (и ровно так падал тест витрины показателей при двух прогонах на
    одном дереве). Пропасть должен ТОЛЬКО исчезнувший экземпляр —
    остальные обязаны дойти до вызывающего."""
    uploads, _, _ = _isolate_registry_dirs(monkeypatch, tmp_path)
    (uploads / "vanishing.json").write_text(json.dumps(MINIMAL_PAYLOAD),
                                            encoding="utf-8")
    (uploads / "surviving.json").write_text(json.dumps(MINIMAL_PAYLOAD),
                                            encoding="utf-8")
    real_load = registry.load_payload

    def load_or_vanish(path):
        if path.name == "vanishing.json":
            path.unlink(missing_ok=True)      # соседний процесс удалил
        return real_load(path)
    monkeypatch.setattr(registry, "load_payload", load_or_vanish)

    ids = {i["id"] for i in registry.list_instances()}
    assert "upload-surviving" in ids
    assert "upload-vanishing" not in ids


def test_journal_write_no_lost_updates_threads(tmp_path):
    # Аудит 2026-07-19 (P1-08): read->merge->replace без лока терял строки
    # (1 из 40 в стресс-репро) и падал PermissionError под read-хэндлом.
    import threading as th
    from _gerad_stage2_roster import journal_write
    jp = tmp_path / "journal.json"
    rows = [{"inst": None, "label": f"lane-{i}", "result_status": "OK"}
            for i in range(24)]
    errs = []

    def w(r):
        try:
            journal_write(jp, r)
        except Exception as exc:          # noqa: BLE001
            errs.append(exc)
    ts = [th.Thread(target=w, args=(r,)) for r in rows]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs
    got = {r["label"] for r in json.loads(jp.read_text())}
    assert got == {f"lane-{i}" for i in range(24)}
    # ни лок-файла, ни tmp-мусора не осталось
    assert not list(tmp_path.glob("*.lock"))
    assert not list(tmp_path.glob("*.tmp-*"))


def test_journal_write_no_lost_updates_processes(tmp_path):
    # Межпроцессный случай (реальный: jobs гоняет параллельные экземпляры).
    import subprocess
    jp = tmp_path / "journal.json"
    script = (
        "import sys; sys.path.insert(0, {root!r}); "
        "from pathlib import Path; "
        "from _gerad_stage2_roster import journal_write; "
        "journal_write(Path({jp!r}), "
        "{{'inst': None, 'label': sys.argv[1], 'result_status': 'OK'}})"
    ).format(root=str(REPO_ROOT), jp=str(jp))
    procs = [subprocess.Popen([sys.executable, "-c", script, f"proc-{i}"],
                              cwd=str(REPO_ROOT))
             for i in range(6)]
    codes = [p.wait(timeout=120) for p in procs]
    assert codes == [0] * 6
    got = {r["label"] for r in json.loads(jp.read_text())}
    assert got == {f"proc-{i}" for i in range(6)}


def test_journal_write_breaks_stale_lock(tmp_path):
    # Лок упавшего писателя (mtime старше 120 с) не блокирует навсегда.
    from _gerad_stage2_roster import journal_write
    jp = tmp_path / "journal.json"
    lock = jp.with_suffix(jp.suffix + ".lock")
    lock.write_text("dead")
    old = time.time() - 300
    os.utime(lock, (old, old))
    journal_write(jp, {"inst": None, "label": "after-crash",
                       "result_status": "OK"})
    assert json.loads(jp.read_text())[0]["label"] == "after-crash"
    assert not lock.exists()


def test_publishable_verdict_survives_a_same_mtime_swap(monkeypatch, tmp_path):
    """R4 §4.5 (закреплено tracked-тестом по требованию аудита): подмена
    содержимого свидетельства с возвратом прежней отметки времени не
    имеет права оставить закэшированный «годен»."""
    import os as _os
    p = tmp_path / "gerad_colgen_selected_x.json"
    p.write_text(json.dumps({"selected": [],
                             "coverage": {"verdict": "OK"}}),
                 encoding="utf-8")
    assert registry._catalog_is_publishable(p) is True
    st = p.stat()
    p.write_text(json.dumps({"selected": [],
                             "coverage": {"verdict": "ОПЕЧАТКА"}}),
                 encoding="utf-8")
    _os.utime(p, (st.st_atime, st.st_mtime))       # вернуть прежний mtime
    assert registry._catalog_is_publishable(p) is False, \
        "подмена с прежним mtime не должна оставаться годной"
