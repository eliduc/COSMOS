"""Контур «Здоровье данных»: счёт дефектов, история снимков, динамика и
черновики предложений. Хранилище перенаправлено во временный файл."""
import json

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from webapp import data_health
    monkeypatch.setattr(data_health, "SNAPSHOT_PATH",
                        tmp_path / "data_health.json")
    return data_health


def _flight(fn, dep, arr, dep_ap="ATL", arr_ap="LAS"):
    return {"flightNumber": fn, "departureAirport": dep_ap,
            "arrivalAirport": arr_ap,
            "departureUTCDateTime": dep, "arrivalUTCDateTime": arr,
            "aircraftType": "NARROWBODY"}


def _only(crs, label):
    """Единственное предложение по виду дефекта с таким названием."""
    hits = [c for c in crs if label in c["title"]]
    assert len(hits) == 1, [c["title"] for c in crs]
    return hits[0]


def _namesakes(groups: int, idle: bool = True):
    """Экземпляр с `groups` парами полных тёзок (по строке отчёта на пару).

    Тёзки НАЗНАЧЕНЫ на рейс: иначе их же посчитал бы вид «без назначений»,
    и постоянного вида для проверки вытеснения не осталось бы."""
    p = _payload()
    for i in range(groups):
        for suffix in ("a", "b"):
            eid = f"T{i}{suffix}"
            p["employees"].append({"id": eid, "name": f"Тёзка {i}",
                                   "homeAirport": "ATL",
                                   "skills": ["Captain"]})
            p["flightAssignments"].append(
                {"id": f"TS{i}{suffix}", "flight": "DL1-0800",
                 "indexInFlight": len(p["flightAssignments"]),
                 "requiredSkill": "Captain", "employee": eid})
    if idle:
        p["employees"].append({"id": "IDLE-1", "name": "Спящий С.С.",
                               "homeAirport": "ATL", "skills": ["Captain"]})
    return p


def _bad_home_airports(n: int):
    """n ошибок валидации одного рода (homeAirport нет в аэропортах)."""
    p = _payload()
    for i in range(n):
        p["employees"].append({"id": f"X{i}", "name": f"Икс {i}",
                               "homeAirport": "NOPE", "skills": ["Captain"]})
    return p


def _with_orphan_slot(slot_id="S3", flight="NOPE-0000"):
    p = _payload()
    p["flightAssignments"].append(
        {"id": slot_id, "flight": flight, "indexInFlight": 1,
         "requiredSkill": "Captain"})
    return p


def _payload(**over):
    p = {
        "airports": [{"code": "ATL"}, {"code": "LAS"}],
        "employees": [
            {"id": "E1", "name": "Иванов И.И.", "homeAirport": "ATL",
             "skills": ["Captain"]},
            {"id": "E2", "name": "Петров П.П.", "homeAirport": "ATL",
             "skills": ["Officer"]},
        ],
        "flights": [
            _flight("DL1-0800", "2024-08-15T08:00:00", "2024-08-15T10:00:00"),
            _flight("DL2-0900", "2024-08-15T09:00:00", "2024-08-15T10:30:00"),
        ],
        "flightAssignments": [
            {"id": "S1", "flight": "DL1-0800", "indexInFlight": 0,
             "requiredSkill": "Captain", "employee": "E1"},
            {"id": "S2", "flight": "DL2-0900", "indexInFlight": 0,
             "requiredSkill": "Officer", "employee": "E2"},
        ],
    }
    p.update(over)
    return p


# ------------------------------------------------------------- счёт дефектов

def test_clean_payload_has_no_hard_defects(store):
    m = store.measure(_payload())
    assert m["counts"].get("validation", 0) == 0
    assert m["counts"].get("time_format", 0) == 0
    assert m["counts"].get("orphan_slot", 0) == 0


def test_time_format_catches_zone_offset_and_garbage(store):
    payload = _payload(flights=[
        _flight("OK-1", "2024-08-15T08:00:00", "2024-08-15T10:00:00"),
        # смещение зоны: движок отбрасывает его без пересчёта
        _flight("TZ-1", "2024-08-15T08:00:00+03:00", "2024-08-15T10:00:00"),
        _flight("TZ-2", "2024-08-15T08:00:00", "2024-08-15T10:00:00Z"),
        _flight("NOSEC", "2024-08-15T08:00", "2024-08-15T10:00:00"),
        _flight("BAD", None, "2024-08-15T10:00:00"),
    ])
    m = store.measure(payload)
    assert m["counts"]["time_format"] == 4          # рейс считается один раз
    assert len(m["details"]["time_format"]) <= store.MAX_DETAILS
    assert any("TZ-1" in d for d in m["details"]["time_format"])


def test_orphan_slot_counts_missing_flights_and_legs(store):
    payload = _payload()
    payload["flights"][0]["flightLegId"] = "LEG-1"
    payload["flightAssignments"] += [
        {"id": "S3", "flight": "NOPE-0000", "indexInFlight": 1,
         "requiredSkill": "Captain"},
        {"id": "S4", "flight": "DL1-0800", "flightLegId": "LEG-404",
         "indexInFlight": 2, "requiredSkill": "Captain"},
        {"id": "S5", "flight": "DL1-0800", "flightLegId": "LEG-1",
         "indexInFlight": 3, "requiredSkill": "Captain"},   # валидное плечо
    ]
    m = store.measure(payload)
    assert m["counts"]["orphan_slot"] == 2
    assert any("NOPE-0000" in d for d in m["details"]["orphan_slot"])


def test_validation_errors_counted(store):
    payload = _payload()
    payload["employees"][0]["homeAirport"] = "XXX"      # нет в airports
    m = store.measure(payload)
    assert m["counts"]["validation"] >= 1


def test_anomaly_kinds_reused_not_invented(store):
    """Дубликаты табельных и «мёртвые души» приходят из anomaly_report."""
    payload = _payload()
    payload["employees"].append(
        {"id": "E1", "name": "Иванов И.И.", "homeAirport": "ATL",
         "skills": ["Captain"]})                        # дубль табельного
    payload["employees"].append(
        {"id": "E9", "name": "Сидоров С.С.", "homeAirport": "ATL",
         "skills": ["Officer"]})                        # без назначений
    m = store.measure(payload)
    assert m["counts"]["dup_employee_id"] == 1
    assert m["counts"]["idle_employee"] == 1            # «1 сотрудник(ов) …»
    assert all(k in store.KIND_LABELS for k in m["counts"])


def test_zero_kinds_are_absent_from_snapshot(store):
    m = store.measure(_payload())
    assert all(v > 0 for v in m["counts"].values())


# ---------------------------------------------------------- история и тренд

def test_history_grows_and_latest_is_last(store):
    store.take_snapshot("i1", _payload())
    bad = _payload()
    bad["flights"][0]["departureUTCDateTime"] = "2024-08-15T08:00:00+03:00"
    snap2 = store.take_snapshot("i1", bad)
    rows = store.history("i1")
    assert len(rows) == 2
    assert [r["at"] for r in rows] == sorted(r["at"] for r in rows)
    assert store.latest("i1") == rows[-1] == snap2
    assert store.latest("i-unknown") is None
    assert store.history("i-unknown") == []


def test_instances_are_isolated(store):
    store.take_snapshot("i1", _payload())
    store.take_snapshot("i2", _payload())
    store.take_snapshot("i1", _payload())
    assert len(store.history("i1")) == 2 and len(store.history("i2")) == 1


def test_trend_without_baseline_treats_past_as_zero(store):
    bad = _payload()
    bad["flights"][0]["departureUTCDateTime"] = "2024-08-15T08:00:00Z"
    store.take_snapshot("i1", bad)
    t = store.trend("i1")
    assert t["hasBaseline"] is False
    assert t["kinds"]["time_format"] == {"now": 1, "prev": 0, "delta": 1}
    assert t["total_prev"] == 0 and t["total_now"] >= 1


def test_trend_reports_worsened_and_improved(store):
    bad = _payload()
    bad["flights"][0]["departureUTCDateTime"] = "2024-08-15T08:00:00Z"
    bad["flights"][1]["departureUTCDateTime"] = "2024-08-15T09:00:00Z"
    store.take_snapshot("i1", bad)                       # time_format = 2

    mixed = _payload()
    mixed["flightAssignments"].append(
        {"id": "S3", "flight": "NOPE-0000", "indexInFlight": 1,
         "requiredSkill": "Captain"})                    # orphan_slot = 1
    store.take_snapshot("i1", mixed)                     # time_format = 0

    t = store.trend("i1")
    assert t["hasBaseline"] is True
    assert t["kinds"]["time_format"] == {"now": 0, "prev": 2, "delta": -2}
    assert "orphan_slot" in t["worsened"]
    assert "time_format" in t["improved"]


def test_corrupt_store_degrades_to_empty(store):
    store.SNAPSHOT_PATH.write_text("{ это не json", encoding="utf-8")
    assert store.history("i1") == []
    assert store.latest("i1") is None
    assert store.trend("i1")["total_now"] == 0
    # и следующий снимок восстанавливает историю, а не падает
    store.take_snapshot("i1", _payload())
    assert len(store.history("i1")) == 1


def test_history_is_capped(store, monkeypatch):
    monkeypatch.setattr(store, "MAX_HISTORY", 3)
    for _ in range(5):
        store.take_snapshot("i1", _payload())
    assert len(store.history("i1")) == 3


# ------------------------------------------------------------- черновики CR

def test_first_snapshot_yields_no_crs(store):
    bad = _payload()
    bad["flights"][0]["departureUTCDateTime"] = "2024-08-15T08:00:00Z"
    assert store.build_crs({"i1": bad}) == []
    assert len(store.history("i1")) == 1                 # снимок всё же снят


def test_cr_on_new_kind_and_on_growth(store):
    store.build_crs({"i1": _payload()})                  # база

    bad = _payload()
    bad["flights"][0]["departureUTCDateTime"] = "2024-08-15T08:00:00Z"
    crs = store.build_crs({"i1": bad})
    # сломанное время рейса даёт И нарушение формата, И (следом) потерю
    # налёта у назначенного на него сотрудника — предложение на каждый вид
    cr = _only(crs, "нарушения формата времени")
    assert cr["agent"] == "datahealth" and cr["status"] == "draft"
    assert cr["priority"] == 1 and cr["effort"] == 3
    assert cr["source"] is None and cr["country"] is None
    assert cr["id"].startswith("cr") and len(cr["id"]) == 10
    assert len(cr["title"]) <= 90 and len(cr["summary"]) <= 600
    assert "i1" in cr["title"] and "0 → 1" in cr["title"]
    assert cr["created_at"] == cr["updated_at"]
    assert cr["created_at"].endswith("+00:00") and \
        len(cr["created_at"]) == len("2026-07-20T00:00:00+00:00")

    worse = _payload()
    for f in worse["flights"]:
        f["departureUTCDateTime"] = f["departureUTCDateTime"] + "Z"
    cr2 = _only(store.build_crs({"i1": worse}), "нарушения формата времени")
    assert cr2["priority"] == 1                          # прирост 2 - 1 = 1
    assert "1 → 2" in cr2["title"]


def test_no_cr_when_defects_shrink_or_hold(store):
    bad = _payload()
    bad["flights"][0]["departureUTCDateTime"] = "2024-08-15T08:00:00Z"
    store.build_crs({"i1": bad})
    assert store.build_crs({"i1": bad}) == []            # не изменилось
    assert store.build_crs({"i1": _payload()}) == []     # стало лучше


def test_priority_is_growth_capped_at_ten(store):
    store.build_crs({"i1": _payload()})
    many = _payload()
    many["flightAssignments"] += [
        {"id": f"S{i}", "flight": "NOPE-0000", "indexInFlight": i,
         "requiredSkill": "Captain"} for i in range(20)]
    cr = store.build_crs({"i1": many})[0]
    assert cr["priority"] == 10


def test_build_crs_accepts_pairs_and_skips_bad_input(store):
    store.build_crs([("i1", _payload())])
    bad = _payload()
    bad["flights"][0]["departureUTCDateTime"] = "2024-08-15T08:00:00Z"
    crs = store.build_crs([("i1", bad), ("i2", "not a payload"), None])
    assert crs and all("i1" in c["title"] for c in crs)   # i2 отброшен
    assert store.history("i2") == []


def test_summary_carries_only_report_examples(store):
    store.build_crs({"i1": _payload()})
    bad = _payload()
    bad["flightAssignments"].append(
        {"id": "S3", "flight": "NOPE-0000", "indexInFlight": 1,
         "requiredSkill": "Captain"})
    cr = store.build_crs({"i1": bad})[0]
    snap = store.latest("i1")
    for example in snap["details"]["orphan_slot"]:
        assert example[:60] in cr["summary"]


def test_store_file_is_valid_json_object(store):
    store.take_snapshot("i1", _payload())
    data = json.loads(store.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert list(data) == ["i1"] and isinstance(data["i1"], list)
    assert set(data["i1"][0]) == {"at", "by", "counts", "details", "saturated"}


# ------------------------------- база предложений против базы показа (A)

def test_manual_snapshot_between_agent_runs_does_not_swallow_growth(store):
    """Кнопка «Снять снимок» не должна гасить ухудшение НАВСЕГДА.

    До правки агент сравнивал два ПОСЛЕДНИХ снимка, поэтому ручной снимок
    после порчи данных становился базой и рост исчезал бесследно."""
    store.build_crs({"i1": _payload()})                  # агентская база
    bad = _with_orphan_slot()
    store.take_snapshot("i1", bad)                       # кнопка на экране
    crs = store.build_crs({"i1": bad})
    cr = _only(crs, "места экипажа без рейса")
    assert "0 → 1" in cr["title"]


def test_intermediate_snapshots_give_exactly_one_cr(store):
    store.build_crs({"i1": _payload()})
    bad = _with_orphan_slot()
    store.take_snapshot("i1", bad)
    store.take_snapshot("i1", bad)
    _only(store.build_crs({"i1": bad}), "места экипажа без рейса")
    # следующий прогон отсчитывает уже от СВОЕГО снимка — дублей нет
    assert store.build_crs({"i1": bad}) == []


def test_manual_baseline_then_damage_yields_cr(store):
    """Сценарий мануала: чистый снимок кнопкой → порча → запуск агента."""
    store.take_snapshot("i1", _payload())
    _only(store.build_crs({"i1": _with_orphan_slot()}),
          "места экипажа без рейса")


def test_snapshot_records_its_source(store):
    store.take_snapshot("i1", _payload())
    store.build_crs({"i1": _payload()})
    assert [r["by"] for r in store.history("i1")] == ["manual", "agent"]


# ------------------------------------------- три состояния хранилища (B)

class _Unreadable:
    """Путь, чтение которого всегда падает вводом-выводом: шаринг-нарушение
    под Windows, антивирус, второй воркер. Остальное делегируется живому
    пути — запись, если она случится, испортит НАСТОЯЩИЙ файл."""

    def __init__(self, real):
        self._real = real

    def read_text(self, *args, **kwargs):
        raise OSError(32, "sharing violation")

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_missing_store_is_legitimately_empty(store):
    assert not store.SNAPSHOT_PATH.exists()
    assert store.history("i1") == [] and store.latest("i1") is None
    store.take_snapshot("i1", _payload())                # запись разрешена
    assert len(store.history("i1")) == 1


def test_undecodable_store_degrades_and_write_recovers(store):
    store.SNAPSHOT_PATH.write_bytes(b"\xff\xfe\x00 not utf-8")
    assert store.history("i1") == []
    store.take_snapshot("i1", _payload())                # ретрай не поможет
    assert len(store.history("i1")) == 1


def test_unreadable_store_refuses_to_write_and_keeps_history(store,
                                                             monkeypatch):
    store.take_snapshot("i1", _payload())
    store.take_snapshot("i2", _payload())
    real = store.SNAPSHOT_PATH
    before = real.read_text(encoding="utf-8")
    monkeypatch.setattr(store, "READ_RETRY_PAUSE", 0)
    monkeypatch.setattr(store, "SNAPSHOT_PATH", _Unreadable(real))

    assert store.history("i1") == []                     # показ деградирует
    with pytest.raises(store.SnapshotStoreUnreadable):   # запись отказывает
        store.take_snapshot("i1", _payload())

    monkeypatch.setattr(store, "SNAPSHOT_PATH", real)
    assert real.read_text(encoding="utf-8") == before
    assert len(store.history("i1")) == 1 and len(store.history("i2")) == 1


# --------------------------------------- потолок отчёта аномалий (D)

def test_report_cap_hides_neither_counts_nor_kinds(store):
    from webapp import anomaly_report

    store.take_snapshot("i1", _namesakes(5))             # 6 строк, без среза
    big = _namesakes(35)                                 # 36 строк, срез 30
    m = store.measure(big)
    assert m["counts"]["namesakes"] == 35                # счётчик не занижен
    # вид «без назначений» печатается ПОСЛЕ тёзок и первым вылетал за потолок
    assert m["counts"]["idle_employee"] == 1

    store.take_snapshot("i1", big)
    t = store.trend("i1")
    assert "idle_employee" not in t["improved"]          # ложного улучшения нет
    assert t["kinds"]["idle_employee"]["delta"] == 0
    assert anomaly_report.MAX_ITEMS == 30                # чужая настройка цела


# ------------------------------------------------ двойной счёт мест (K)

def test_orphan_slots_are_not_counted_twice(store):
    payload = _payload()
    payload["flightAssignments"] += [
        {"id": "S3", "flight": "NOPE-1", "indexInFlight": 1,
         "requiredSkill": "Captain"},
        {"id": "S4", "flight": "NOPE-2", "indexInFlight": 2,
         "requiredSkill": "Captain"},
    ]
    m = store.measure(payload)
    assert m["counts"]["orphan_slot"] == 2
    assert m["counts"].get("validation", 0) == 0         # те же два дефекта


def test_validation_keeps_slot_errors_that_are_not_orphans(store):
    payload = _payload()
    payload["flights"][0]["flightLegId"] = "LEG-1"
    payload["flights"][1]["flightLegId"] = "LEG-2"
    payload["flightAssignments"].append(
        {"id": "S3", "flight": "DL2-0900", "flightLegId": "LEG-1",
         "indexInFlight": 1, "requiredSkill": "Captain"})   # чужое плечо
    m = store.measure(payload)
    assert m["counts"].get("orphan_slot", 0) == 0
    assert m["counts"]["validation"] == 1                # вычтено не лишнее


# --------------------------------------- насыщение счётчика валидации (L)

def test_validation_saturation_is_flagged(store):
    m = store.measure(_bad_home_airports(60))
    assert m["counts"]["validation"] == store.registry.MAX_VALIDATION_ERRORS
    assert "validation" in m["saturated"]
    small = store.measure(_bad_home_airports(3))
    assert small["counts"]["validation"] == 3
    assert small["saturated"] == []


def test_no_cr_conclusions_from_saturated_validation(store):
    store.build_crs({"i1": _bad_home_airports(25)})      # база: упёрлись в 20
    crs = store.build_crs({"i1": _bad_home_airports(60)})
    assert not [c for c in crs if "ошибки валидации" in c["title"]]


# ------------------------------------------------- вес строки отчёта (Q)

def test_warning_weight_reads_leading_count(store):
    assert store._warning_weight("12 рейс(ов) вне основного месяца 2024-08") \
        == 12
    assert store._warning_weight("3 сотрудник(ов) без назначения: E1") == 3
    assert store._warning_weight("дубликаты табельных номеров: E1, E2") == 1
    assert store._warning_weight("") == 1


def test_stray_month_counts_flights_not_report_lines(store):
    payload = _payload(flightAssignments=[], flights=[
        _flight("A-1", "2024-08-15T08:00:00", "2024-08-15T10:00:00"),
        _flight("A-2", "2024-08-16T08:00:00", "2024-08-16T10:00:00"),
        _flight("B-1", "2024-09-01T08:00:00", "2024-09-01T10:00:00"),
        _flight("B-2", "2024-09-02T08:00:00", "2024-09-02T10:00:00"),
        _flight("B-3", "2024-09-03T08:00:00", "2024-09-03T10:00:00"),
    ])
    # одна строка отчёта, но дефектов в ней два — счётчик считает рейсы
    assert store.measure(payload)["counts"]["stray_month"] == 2
