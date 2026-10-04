"""Контур «Надёжность стенда»: запись наблюдений, агрегаты по типам задач
и черновики предложений. Хранилище перенаправлено в tmp-файл."""
import json

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from webapp import run_profile
    monkeypatch.setattr(run_profile, "PROFILE_PATH",
                        tmp_path / "run_profile.json")
    return run_profile


def _rec(store, jtype="pairing", outcome="succeeded", budget=100.0,
         wall=50.0, **kw):
    return store.record_run(jtype=jtype, label="gerad-1", legs=1078,
                            budget_s=budget, wall_s=wall, outcome=outcome,
                            **kw)


# ------------------------------------------------------------- record_run

def test_record_run_shape_and_persistence(store):
    row = store.record_run(jtype="pairing", label="gerad-1", legs=1078,
                           budget_s=600, wall_s=612.5, outcome="failed",
                           exit_code=1, gate_nonzero=True)
    assert row["jtype"] == "pairing" and row["label"] == "gerad-1"
    assert row["legs"] == 1078 and row["budget_s"] == 600.0
    assert row["wall_s"] == 612.5 and row["outcome"] == "failed"
    assert row["exit_code"] == 1 and row["gate_nonzero"] is True
    # UTC ISO с секундами
    assert row["at"].endswith("+00:00") and len(row["at"]) == 25
    assert store.runs() == [row]
    on_disk = json.loads(store.PROFILE_PATH.read_text(encoding="utf-8"))
    assert on_disk == [row]


def test_unknown_outcome_and_bad_numbers_rejected(store):
    with pytest.raises(store.RunProfileError):
        store.record_run(jtype="pairing", label="x", outcome="exploded")
    with pytest.raises(store.RunProfileError):
        _rec(store, wall="долго")
    with pytest.raises(store.RunProfileError):
        _rec(store, wall=-5)
    with pytest.raises(store.RunProfileError):
        store.record_run(jtype="", label="x", outcome="succeeded")
    assert store.runs() == []


def test_optional_fields_default_to_none(store):
    row = store.record_run(jtype="rostering", label="x", outcome="stopped")
    assert row["legs"] is None and row["budget_s"] is None
    assert row["wall_s"] is None and row["exit_code"] is None
    assert row["gate_nonzero"] is False


def test_runs_filtered_by_type(store):
    _rec(store, jtype="pairing")
    _rec(store, jtype="rostering")
    _rec(store, jtype="pairing")
    assert len(store.runs("pairing")) == 2
    assert len(store.runs("rostering")) == 1
    assert store.runs("нет такого") == []
    assert len(store.runs()) == 3


def test_trimmed_to_max_rows(store, monkeypatch):
    monkeypatch.setattr(store, "MAX_ROWS", 5)
    for i in range(9):
        _rec(store, wall=float(i))
    rows = store.runs()
    assert len(rows) == 5
    # обрезается ХВОСТ: остаются последние наблюдения
    assert [r["wall_s"] for r in rows] == [4.0, 5.0, 6.0, 7.0, 8.0]


def test_corrupt_store_degrades_to_empty(store):
    # телеметрия: битый файл не роняет вызывающего, а читается как «пусто»
    store.PROFILE_PATH.write_text("{not json", encoding="utf-8")
    assert store.runs() == []
    assert store.profile() == {"by_type": {}, "total_runs": 0}
    # и запись поверх битого файла продолжает работать
    _rec(store)
    assert len(store.runs()) == 1


# --------------------------------------------------------------- profile

def test_profile_aggregates_by_type(store):
    for wall in (10.0, 20.0, 30.0, 40.0, 200.0):
        _rec(store, jtype="pairing", budget=100.0, wall=wall)
    _rec(store, jtype="pairing", outcome="failed", budget=100.0, wall=5.0)
    _rec(store, jtype="rostering", outcome="stopped", budget=None, wall=None)
    p = store.profile()
    assert p["total_runs"] == 7
    a = p["by_type"]["pairing"]
    assert a["runs"] == 6 and a["succeeded"] == 5 and a["failed"] == 1
    assert a["stopped"] == 0
    # времена и укладываемость — только по 5 успешным (упавший на 5 с не в счёт)
    assert a["within_budget_pct"] == 80.0    # уложились все, кроме 200 с
    assert a["median_wall_s"] == 30.0        # медиана 10,20,30,40,200
    assert a["p90_wall_s"] == 200.0          # ближайший ранг ceil(0.9*5)=5
    assert a["failure_pct"] == 16.7
    b = p["by_type"]["rostering"]
    assert b["stopped"] == 1 and b["failure_pct"] == 0.0
    # без бюджета/факта укладываемость и времена честно неизвестны
    assert b["within_budget_pct"] is None
    assert b["median_wall_s"] is None and b["p90_wall_s"] is None


def test_within_budget_ignores_rows_without_both_numbers(store):
    _rec(store, budget=100.0, wall=50.0)             # учитывается
    _rec(store, budget=None, wall=5000.0)            # нет бюджета
    _rec(store, budget=100.0, wall=None)             # нет факта
    a = store.profile()["by_type"]["pairing"]
    assert a["within_budget_pct"] == 100.0
    assert a["runs"] == 3
    # времена считаются по всем строкам, где факт есть
    assert a["median_wall_s"] == 2525.0


def test_profile_survives_foreign_rows(store):
    _rec(store, budget=100.0, wall=50.0)
    rows = json.loads(store.PROFILE_PATH.read_text(encoding="utf-8"))
    rows.append({"jtype": "pairing", "outcome": "succeeded",
                 "budget_s": "n/a", "wall_s": None})
    rows.append("мусор")
    store.PROFILE_PATH.write_text(json.dumps(rows), encoding="utf-8")
    a = store.profile()["by_type"]["pairing"]
    assert a["runs"] == 2                    # строка-не-словарь отброшена
    assert a["within_budget_pct"] == 100.0   # нечисловой бюджет не в паре


def test_stopped_and_failed_out_of_budget_and_times(store):
    # эталон: два успешных прогона, оба в бюджете
    _rec(store, budget=100.0, wall=10.0)
    _rec(store, budget=100.0, wall=20.0)
    clean = store.profile()["by_type"]["pairing"]
    assert clean["within_budget_pct"] == 100.0
    assert clean["median_wall_s"] == 15.0 and clean["p90_wall_s"] == 20.0
    # оборванный человеком и упавший НЕ дошли до конца: об укладываемости
    # и о типичном времени они не говорят ничего
    _rec(store, budget=100.0, wall=900.0, outcome="stopped")
    _rec(store, budget=100.0, wall=0.5, outcome="failed", exit_code=1)
    a = store.profile()["by_type"]["pairing"]
    assert a["within_budget_pct"] == 100.0
    assert a["median_wall_s"] == 15.0 and a["p90_wall_s"] == 20.0
    # но в счётчиках исходов и в доле отказов они остаются
    assert a["runs"] == 4 and a["stopped"] == 1 and a["failed"] == 1
    assert a["failure_pct"] == 25.0


def test_only_stopped_runs_leave_budget_unknown(store):
    for _ in range(3):
        _rec(store, budget=10.0, wall=900.0, outcome="stopped")
    a = store.profile()["by_type"]["pairing"]
    # ни одного завершённого прогона -> сказать о бюджете нечего
    assert a["within_budget_pct"] is None
    assert a["median_wall_s"] is None and a["p90_wall_s"] is None
    assert a["runs"] == 3 and a["failure_pct"] == 0.0


def test_stopped_runs_do_not_raise_budget_cr(store):
    # 10 успешных прогонов внутри бюджета + 10 оборванных человеком:
    # до правки оборванные топили укладываемость до 50% и рождали CR
    for _ in range(10):
        _rec(store, budget=100.0, wall=10.0)
        _rec(store, budget=100.0, wall=5000.0, outcome="stopped")
    assert store.build_crs(min_runs=10, within_budget_floor=80.0) == []


# -------------------------------------------------------------- build_crs

def test_no_crs_below_min_runs(store):
    for _ in range(4):
        _rec(store, budget=10.0, wall=900.0, outcome="failed")
    assert store.build_crs(min_runs=10) == []


def test_budget_cr_when_within_budget_below_floor(store):
    for _ in range(8):
        _rec(store, budget=10.0, wall=100.0)     # не уложились
    for _ in range(2):
        _rec(store, budget=10.0, wall=5.0)       # уложились
    crs = store.build_crs(min_runs=10, within_budget_floor=80.0)
    assert len(crs) == 1
    cr = crs[0]
    assert cr["agent"] == "runtime" and cr["status"] == "draft"
    assert cr["effort"] == 2 and cr["source"] is None and cr["country"] is None
    assert cr["id"].startswith("cr") and len(cr["id"]) == 10
    assert cr["created_at"] == cr["updated_at"]
    assert len(cr["title"]) <= 90 and len(cr["summary"]) <= 600
    assert "бюджет" in cr["title"].lower()
    # отклонение 80 - 20 = 60 п.п. -> приоритет 6
    assert cr["priority"] == 6
    # summary обязан нести числа выборки и оговорку
    assert "10 прогонов" in cr["summary"]
    # медиана считается по успешным — их число названо явно
    assert "успешно завершились 10" in cr["summary"]
    assert "медиана" in cr["summary"] and "перцентиль" in cr["summary"]
    assert "Один прогон ничего не доказывает" in cr["summary"]


def test_failure_cr_and_both_at_once(store):
    for _ in range(6):
        _rec(store, budget=10.0, wall=100.0, outcome="failed", exit_code=1)
    for _ in range(6):
        _rec(store, budget=10.0, wall=100.0)
    crs = store.build_crs(min_runs=10, within_budget_floor=80.0,
                          failure_ceiling=10.0)
    assert len(crs) == 2                        # и бюджет, и отказы
    titles = " | ".join(c["title"] for c in crs)
    assert "бюджет" in titles.lower() and "тказ" in titles
    fail_cr = [c for c in crs if "тказ" in c["title"]][0]
    assert "50.0%" in fail_cr["summary"] and "6 из 12" in fail_cr["summary"]
    # отклонение 50 - 10 = 40 п.п. -> приоритет 4
    assert fail_cr["priority"] == 4
    assert all(c["agent"] == "runtime" for c in crs)


def test_healthy_type_produces_nothing(store):
    for _ in range(20):
        _rec(store, budget=100.0, wall=10.0)
    assert store.build_crs(min_runs=10) == []


def test_priority_capped_at_ten(store):
    for _ in range(12):
        _rec(store, budget=1.0, wall=900.0, outcome="failed")
    crs = store.build_crs(min_runs=10, within_budget_floor=100.0,
                          failure_ceiling=0.0)
    assert crs and all(c["priority"] == 10 for c in crs)


def test_crs_are_deterministic_per_type_order(store):
    for _ in range(12):
        _rec(store, jtype="rostering", budget=1.0, wall=900.0)
        _rec(store, jtype="pairing", budget=1.0, wall=900.0)
    crs = store.build_crs(min_runs=10)
    assert [c["title"] for c in crs] == \
        sorted(c["title"] for c in crs if "pairing" in c["title"]) + \
        [c["title"] for c in crs if "rostering" in c["title"]]
    assert len(crs) == 2
