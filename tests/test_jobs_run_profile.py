"""Связка задач с контуром «Надёжность стенда»: какой бюджет задачи вообще
сопоставим с её полным временем. Подпроцессы не запускаются — проверяется
чистая функция и наблюдения, записанные в tmp-хранилище."""
import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from webapp import run_profile
    monkeypatch.setattr(run_profile, "PROFILE_PATH",
                        tmp_path / "run_profile.json")
    return run_profile


@pytest.fixture()
def jobs():
    from webapp import jobs as jobs_mod
    return jobs_mod


def _job(jtype, params, status="succeeded"):
    return {"id": f"{jtype}-x-000000", "type": jtype, "params": params,
            "status": status, "exit_code": 0,
            "_inst": {"label": "inst9"}, "_started_ts": 0.0}


# ------------------------------------------------------- _budget_seconds

def test_budget_seconds_per_stage_types(jobs):
    # stage-1 меряет бюджет в минутах, stage-2 — в секундах
    assert jobs._budget_seconds(_job("stage1", {"budget_min": 45})) == 2700.0
    assert jobs._budget_seconds(_job("stage2", {"budget_s": 600})) == 600.0
    # параметра нет — сравнивать не с чем
    assert jobs._budget_seconds(_job("stage1", {})) is None
    assert jobs._budget_seconds(_job("stage2", {"budget_s": "быстро"})) is None


def test_budget_seconds_none_for_multi_stage_types(jobs):
    # У multirole в params ЛЕЖАТ ОБА бюджета, и каждый — на одну ролевую
    # полосу: полное время процесса не сопоставимо ни с одним из них.
    # До правки здесь возвращалось 600.0 (бюджет одного этапа).
    assert jobs._budget_seconds(
        _job("multirole", {"budget_min": 45, "budget_s": 600, "seed": 7})
    ) is None
    # чужие типы бюджета времени не несут даже при постороннем параметре
    for jtype in ("complete", "validate", "repair"):
        assert jobs._budget_seconds(_job(jtype, {"budget_s": 600})) is None


# --------------------------------------- запись наблюдения и ложный CR

def test_multirole_records_no_budget_and_raises_no_false_cr(jobs, store,
                                                            monkeypatch):
    # полное время многополосного конвейера кратно больше бюджета этапа
    monkeypatch.setattr(jobs.time, "time", lambda: 5000.0)
    for _ in range(12):
        jobs._record_run_profile(
            _job("multirole", {"budget_min": 45, "budget_s": 600}))
    agg = store.profile()["by_type"]["multirole"]
    assert agg["runs"] == 12 and agg["succeeded"] == 12
    assert agg["median_wall_s"] == 5000.0
    # укладываемость к многоэтапной задаче неприменима -> нет и ложного CR
    assert agg["within_budget_pct"] is None
    titles = [c["title"] for c in store.build_crs(min_runs=10)]
    assert not any("бюджет" in t.lower() for t in titles)


def test_stage2_still_measures_within_budget(jobs, store, monkeypatch):
    # инвариант: одноэтапная задача метрику не потеряла
    monkeypatch.setattr(jobs.time, "time", lambda: 700.0)
    jobs._record_run_profile(_job("stage2", {"budget_s": 600}))
    monkeypatch.setattr(jobs.time, "time", lambda: 100.0)
    jobs._record_run_profile(_job("stage2", {"budget_s": 600}))
    agg = store.profile()["by_type"]["stage2"]
    assert agg["within_budget_pct"] == 50.0


def test_recording_never_raises_on_broken_store(jobs, store):
    # инвариант волны 1: сбор статистики не роняет задачу
    store.PROFILE_PATH.write_text("{битый", encoding="utf-8")
    jobs._record_run_profile(_job("stage2", {"budget_s": "мусор"},
                                  status="какой-то"))
    assert store.runs() == []


# --- четвёртый аудит 2026-07-27 --------------------------------------------

def test_rescue_s_survives_the_whole_tract(tmp_path, monkeypatch):
    """R4 §4.1: rescue_s был проведён в команду и подпись, но санитайзер
    вырезал его ДО обеих точек — две разные постановки давали одинаковые
    argv и одинаковую подпись задачи, а «исправление» было мёртвым кодом.
    Тест идёт через НАСТОЯЩИЙ тракт: sanitize -> команда -> подпись."""
    from webapp import jobs, registry, solver_advisor

    # Набор обязан существовать: с 2026-08-13 путь входа разрешается до
    # передачи в расчёт, а пропавший вход — отказ при постановке задачи.
    pay = tmp_path / "x.json"
    pay.write_text("{}", encoding="utf-8")
    real = registry.resolve_rel
    monkeypatch.setattr(
        registry, "resolve_rel",
        lambda rel: pay if str(rel).endswith("uploads/x.json") else real(rel))

    inst = {"id": "upload-x", "label": "x", "source": "upload",
            "payload_path": "data/uploads/x.json",
            "catalog_path": "final-report/pairing_base_catalog_x.json",
            "stage2_available": False}

    def argv_for(value):
        knobs = solver_advisor.sanitize_params(
            {"pairing_method": "columns", "pairing_level": 3,
             "rescue_s": value})
        assert knobs.get("rescue_s") == value, \
            "санитайзер обязан пропускать rescue_s — иначе тракта нет"
        cmds = jobs._build_commands("stage1", dict(inst),
                                    {"budget_min": 5, **knobs})
        return [str(a) for a in cmds[0]]

    a17, a99 = argv_for(17), argv_for(99)
    assert "--rescue-s" in a17 and "17" in a17
    assert "--rescue-s" in a99 and "99" in a99
    sig17 = jobs.config_signature(jobs.resolved_config([a17]))
    sig99 = jobs.config_signature(jobs.resolved_config([a99]))
    assert sig17 != sig99, \
        "разные бюджеты спасательного прохода обязаны давать разные подписи"


def test_resource_rules_conflict_is_a_loud_409(tmp_path, monkeypatch):
    """R4 §4.2 + R3 §5.2: одинаковое имя ресурса из двух пакетов с разными
    значениями — отказ 409 с именами обоих пакетов, не тихий выбор."""
    import json as _json

    from webapp import jobs, rule_packs

    def rule(name, lo):
        return {"id": name, "status": "approved",
                "constraint": {"kind": "resource", "resource": name,
                               "unit": "minutes", "level": "week",
                               "window": {"min": lo}}}
    packs = {"packs": [
        {"id": "pa", "name": "Пакет А", "status": "approved",
         "country": None, "rules": [rule("weeklyRest", 720)]},
        {"id": "pb", "name": "Пакет Б", "status": "approved",
         "country": None, "rules": [rule("weeklyRest", 999)]}]}
    pp = tmp_path / "rule_packs.json"
    pp.write_text(_json.dumps(packs, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rule_packs, "PACKS_PATH", pp)
    monkeypatch.setattr(jobs, "RESOURCE_RULES_DIR", tmp_path / "rr")
    import pytest as _pytest
    with _pytest.raises(jobs.JobError) as exc:
        jobs._resource_rules_file({"id": "x", "label": "x",
                                   "country": "INTL"}, "run-conflict")
    assert exc.value.http_status == 409
    assert "Пакет А" in str(exc.value) and "Пакет Б" in str(exc.value)


def test_resource_rules_file_is_unique_per_call(tmp_path, monkeypatch):
    """R4 §4.2: файл по метке набора перезаписывался параллельным (даже
    отклонённым) запросом до чтения работающей задачей. Имя уникально на
    вызов, отпечаток содержимого возвращается вызывающему."""
    import json as _json

    from webapp import jobs, rule_packs

    packs = {"packs": [
        {"id": "pa", "name": "А", "status": "approved", "country": None,
         "rules": [{"id": "weeklyRest", "status": "approved",
                    "constraint": {"kind": "resource",
                                   "resource": "weeklyRest",
                                   "unit": "minutes", "level": "week",
                                   "window": {"min": 720}}}]}]}
    pp = tmp_path / "rule_packs.json"
    pp.write_text(_json.dumps(packs), encoding="utf-8")
    monkeypatch.setattr(rule_packs, "PACKS_PATH", pp)
    monkeypatch.setattr(jobs, "RESOURCE_RULES_DIR", tmp_path / "rr")
    inst = {"id": "x", "label": "x", "country": "INTL"}
    # R5-02: файл ПРОГОНА с исключительным созданием — разные прогоны не
    # пересекаются, а повтор того же прогона падает, не переписывая
    import pytest as _pytest
    p1, fp1 = jobs._resource_rules_file(inst, "run-a")
    p2, fp2 = jobs._resource_rules_file(inst, "run-b")
    assert p1 != p2, "разные прогоны обязаны получить разные файлы"
    assert fp1 == fp2, "одинаковое содержимое — одинаковый отпечаток"
    import hashlib as _hl
    assert fp1 == _hl.sha256(
        p1.read_text(encoding="utf-8").encode()).hexdigest()
    with _pytest.raises(FileExistsError):
        jobs._resource_rules_file(inst, "run-a")   # O_EXCL: не перезапись
    body_before = p1.read_text(encoding="utf-8")
    assert body_before, "файл первого прогона не тронут"
    # и уборка жизненного цикла
    jobs.drop_resource_rules("run-a")
    assert not p1.exists()
