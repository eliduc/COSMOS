"""Приём дельт (Ф9): разбор ASM-подмножества с громкими отказами,
применение к канону (CNL/TIM/EQT/NEW), кадровый диф без молчаливых
удалений, идемпотентность по SHA-256, откат при невалидном результате."""
import json

import pytest

from webapp import registry, schedule_deltas as sd
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL


def _payload():
    return {
        "airports": [{"code": "SVO"}, {"code": "LED"}, {"code": "KZN"}],
        "employees": [{"id": "P1", "homeAirport": "SVO",
                       "skills": ["Captain"], "name": "One"}],
        "flights": [
            {"flightNumber": "SU100", "departureAirport": "SVO",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "LED",
             "arrivalUTCDateTime": "2026-03-01T09:20:00",
             "aircraftType": "A320"},
            {"flightNumber": "SU100", "departureAirport": "SVO",
             "departureUTCDateTime": "2026-03-02T08:00:00",
             "arrivalAirport": "LED",
             "arrivalUTCDateTime": "2026-03-02T09:20:00",
             "aircraftType": "A320"}],
        "flightAssignments": [
            {"id": "a1", "flight": "SU100", "indexInFlight": 0,
             "requiredSkill": "Captain", "employee": None}],
    }


ASM = """\
ASM
CNL
SU100/01MAR26

ASM
TIM
SU100/02MAR26
0900 2330

NEW
SU200/05MAR
SVO KZN 1000 1130 A321

EQT
SU100/02MAR26
A321
"""


def test_parse_asm_subset():
    acts = sd.parse_asm(ASM, _payload())
    assert [a["action"] for a in acts] == ["CNL", "TIM", "NEW", "EQT"]
    assert acts[0]["date"] == "2026-03-01"
    assert acts[2]["date"] == "2026-03-05"        # год из расписания
    assert acts[2]["aircraftType"] == "A321"


def test_parse_asm_loud_on_garbage():
    with pytest.raises(sd.DeltaError, match="неизвестное действие"):
        sd.parse_asm("XXX\nSU100/01MAR26", _payload())
    with pytest.raises(sd.DeltaError, match="не разобралась"):
        sd.parse_asm("CNL\nмусор", _payload())
    with pytest.raises(sd.DeltaError, match="ни одного действия"):
        sd.parse_asm("\n\n", _payload())


def test_apply_schedule_semantics():
    p = _payload()
    acts = sd.parse_asm(ASM, p)
    out, summary = sd.apply_schedule(p, acts)
    assert summary["cancelled"] == 1
    # SU100 flies on TWO dates; cancelling 01MAR must NOT strip the shared
    # SU100 slot (02MAR still flies it) — slots key on number only.
    assert summary["assignments_removed"] == 0
    assert summary["retimed"] == 1 and summary["equipment"] == 1
    assert summary["created"] == 1
    fns = [(f["flightNumber"], f["departureUTCDateTime"][:10])
           for f in out["flights"]]
    assert ("SU100", "2026-03-01") not in fns      # отменён
    su2 = next(f for f in out["flights"]
               if f["departureUTCDateTime"].startswith("2026-03-02"))
    assert su2["departureUTCDateTime"] == "2026-03-02T09:00:00"
    # прилёт 23:30 позже вылета — та же дата
    assert su2["arrivalUTCDateTime"] == "2026-03-02T23:30:00"
    assert su2["aircraftType"] == "A321"
    new = next(f for f in out["flights"] if f["flightNumber"] == "SU200")
    assert new["departureAirport"] == "SVO"
    # исходный payload не мутирован
    assert len(p["flights"]) == 2


def test_cnl_removes_slots_only_for_last_occurrence():
    """A unique flight number: cancelling it DOES free its crew slots.
    A daily number keeps its slot until the last date is cancelled."""
    p = _payload()                              # SU100 on 01 and 02 MAR
    p["flightAssignments"] = [
        {"id": "a1", "flight": "SU100", "indexInFlight": 0,
         "requiredSkill": "Captain", "employee": "P1"}]
    # cancel only 01MAR -> SU100 still flies 02MAR -> slot kept
    out1, s1 = sd.apply_schedule(p, sd.parse_asm("CNL\nSU100/01MAR26", p))
    assert s1["assignments_removed"] == 0
    assert len(out1["flightAssignments"]) == 1
    # now cancel the remaining 02MAR occurrence -> last one -> slot freed
    out2, s2 = sd.apply_schedule(
        out1, sd.parse_asm("CNL\nSU100/02MAR26", out1))
    assert s2["assignments_removed"] == 1
    assert out2["flightAssignments"] == []


def test_cnl_precise_removal_with_first_class_leg_identity():
    """C01/C02 фаза 3: payload с материализованным flightLegId снимает слоты
    РОВНО отменённого участка — экипаж других дат того же номера цел, и
    хак «держать до последней даты» больше не нужен."""
    p = _payload()
    for f in p["flights"]:
        f["flightLegId"] = (f"{f['flightNumber']}|"
                            f"{f['departureUTCDateTime']}")
    p["flightAssignments"] = [
        {"id": "a1", "crewSlotId": "a1", "flight": "SU100",
         "flightLegId": "SU100|2026-03-01T08:00:00",
         "indexInFlight": 0, "requiredSkill": "Captain", "employee": "P1"},
        {"id": "a2", "crewSlotId": "a2", "flight": "SU100",
         "flightLegId": "SU100|2026-03-02T08:00:00",
         "indexInFlight": 0, "requiredSkill": "Captain", "employee": "P1"}]
    out, s = sd.apply_schedule(p, sd.parse_asm("CNL\nSU100/01MAR26", p))
    # ровно один слот снят — слот отменённого 01MAR; слот 02MAR жив
    assert s["assignments_removed"] == 1
    assert [a["flightLegId"] for a in out["flightAssignments"]] == \
        ["SU100|2026-03-02T08:00:00"]
    # NEW в материализованном payload'е получает flightLegId той же формулы
    out2, s2 = sd.apply_schedule(
        out, sd.parse_asm("NEW\nSU300/05MAR26\nSVO LED 1000 1130 A320", out))
    new = next(f for f in out2["flights"] if f["flightNumber"] == "SU300")
    assert new["flightLegId"] == "SU300|2026-03-05T10:00:00"


def test_cnl_mixed_materialization_falls_back_to_number_rule():
    """Ревью (находки 2/6/10/14): рейсы с flightLegId, слоты БЕЗ него
    (легальный смешанный payload импортёра при повторных номерах) — CNL не
    должен ни бросать слоты висячими, ни ломать применение дельты: слот без
    flightLegId живёт по числовому правилу (до последней даты номера)."""
    p = _payload()
    # третий, не затрагиваемый рейс — чтобы после обеих отмен flights не был
    # пуст (схема требует minItems 1; к CNL-семантике это не относится)
    p["flights"].append(
        {"flightNumber": "SU999", "departureAirport": "SVO",
         "departureUTCDateTime": "2026-03-03T08:00:00",
         "arrivalAirport": "LED",
         "arrivalUTCDateTime": "2026-03-03T09:20:00",
         "aircraftType": "A320"})
    for f in p["flights"]:
        f["flightLegId"] = (f"{f['flightNumber']}|"
                            f"{f['departureUTCDateTime']}")
    # слоты НЕ материализованы (как у импортёра при повторном номере)
    out1, s1 = sd.apply_schedule(p, sd.parse_asm("CNL\nSU100/01MAR26", p))
    assert s1["assignments_removed"] == 0          # 02MAR ещё летает SU100
    assert len(out1["flightAssignments"]) == 1
    out2, s2 = sd.apply_schedule(
        out1, sd.parse_asm("CNL\nSU100/02MAR26", out1))
    assert s2["assignments_removed"] == 1          # последняя дата — слот снят
    assert out2["flightAssignments"] == []         # ничего не висит
    # результат валиден — дельта применima без 422
    assert registry.validate_payload(out2)["ok"] is True


def test_new_does_not_stamp_colliding_authored_leg_id():
    """Ревью (находки 7/11/17): flightLegId — идентичность, не формула;
    авторский id, текстуально равный fn|dep будущего NEW, не должен ломать
    легитимное добавление рейса."""
    p = _payload()
    p["flights"][0]["flightLegId"] = "SU300|2026-03-05T10:00:00"   # авторский
    p["flights"][1]["flightLegId"] = "SU100|2026-03-02T08:00:00"
    # P0-3: слот атрибутируем конкретному вхождению — иначе шаблон мест
    # повторного номера без идентичности честно отвергается
    p["flightAssignments"][0]["flightLegId"] = "SU300|2026-03-05T10:00:00"
    out, s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU300/05MAR26\nSVO LED 1000 1130 A320", p))
    assert s["created"] == 1
    new = next(f for f in out["flights"] if f["flightNumber"] == "SU300")
    # формульный id совпал бы с авторским — NEW остаётся без штампа
    assert "flightLegId" not in new
    assert registry.validate_payload(out)["ok"] is True


def test_apply_is_idempotent_per_action():
    p = _payload()
    out, s1 = sd.apply_schedule(p, sd.parse_asm("CNL\nSU100/01MAR26", p))
    out2, s2 = sd.apply_schedule(out, sd.parse_asm("CNL\nSU100/01MAR26", p))
    assert s2["cancelled"] == 0 and len(s2["skipped"]) == 1


def test_overnight_arrival_rolls_to_next_day():
    p = _payload()
    out, _ = sd.apply_schedule(
        p, sd.parse_asm("TIM\nSU100/02MAR26\n2300 0130", p))
    f = next(x for x in out["flights"]
             if x["departureUTCDateTime"].startswith("2026-03-02"))
    assert f["arrivalUTCDateTime"] == "2026-03-03T01:30:00"


# ------------------------------------------------------------- crew deltas

CREW_CSV = ("id,name,homeAirport,skills,seniority\n"
            "P1,Renamed One,SVO,Captain;FirstOfficer,7\n"
            "P9,New Nine,LED,Captain,1\n")


def test_crew_feed_parse_and_diff():
    recs = sd.parse_crew_feed("crew.csv", CREW_CSV.encode())
    assert recs[0]["skills"] == ["Captain", "FirstOfficer"]
    assert recs[0]["seniority"] == 7.0
    diff = sd.crew_diff(_payload(), recs)
    assert diff["added"] == ["P9"]
    assert diff["updated"][0]["id"] == "P1"
    assert set(diff["updated"][0]["fields"]) == {"name", "skills",
                                                 "seniority"}
    assert diff["missing_in_feed"] == []


def test_crew_feed_rejects_unknown_columns():
    with pytest.raises(sd.DeltaError, match="неизвестны"):
        sd.parse_crew_feed("x.csv", b"id,fio\nP1,Ivanov\n")


def test_crew_feed_json_preserves_lists():
    """JSON list fields (skills, typeRatings) must stay lists, not be
    stringified into garbage (audit HIGH)."""
    body = json.dumps([
        {"id": "P9", "name": "Nine", "homeAirport": "LED",
         "skills": ["Captain", "FirstOfficer"],
         "typeRatings": ["A320", "A321"], "seniority": 12, "isReserve": True}
    ]).encode("utf-8")
    recs = sd.parse_crew_feed("crew.json", body)
    assert recs[0]["skills"] == ["Captain", "FirstOfficer"]
    assert recs[0]["typeRatings"] == ["A320", "A321"]
    assert recs[0]["seniority"] == 12.0
    assert recs[0]["isReserve"] is True


def test_crew_feed_json_rejects_unknown_field():
    body = b'[{"id": "P1", "fio": "Ivanov"}]'
    with pytest.raises(sd.DeltaError, match="неизвестны"):
        sd.parse_crew_feed("crew.json", body)


def test_hhmm_validation_rejects_bad_times():
    p = _payload()
    with pytest.raises(sd.DeltaError, match="недопустимое время"):
        sd.parse_asm("TIM\nSU100/01MAR26\n2500 0130", p)
    with pytest.raises(sd.DeltaError, match="недопустимое время"):
        sd.parse_asm("NEW\nSU300/05MAR26\nSVO LED 0800 0990", p)


def test_year_default_single_year_ok_multi_year_demands_it():
    p = _payload()                              # all 2026 flights
    acts = sd.parse_asm("CNL\nSU100/01MAR", p)  # no year -> takes 2026
    assert acts[0]["date"] == "2026-03-01"
    # add a 2025 flight -> schedule now spans two years -> year required
    p2 = _payload()
    p2["flights"].append({
        "flightNumber": "SU900", "departureAirport": "SVO",
        "departureUTCDateTime": "2025-12-31T22:00:00",
        "arrivalAirport": "LED",
        "arrivalUTCDateTime": "2025-12-31T23:20:00"})
    with pytest.raises(sd.DeltaError, match="несколько лет"):
        sd.parse_asm("CNL\nSU100/01MAR", p2)


def test_concurrent_applies_serialized(inst):
    """Two apply calls to the same instance must not lose an update — the
    per-instance lock serializes read-modify-write."""
    import threading
    barrier = threading.Barrier(2)
    results = []

    def worker(text):
        barrier.wait()
        try:
            results.append(sd.receive_schedule(inst, text, ADMIN,
                                               apply=True))
        except sd.DeltaError as exc:
            results.append(exc)

    t1 = threading.Thread(target=worker, args=("CNL\nSU100/01MAR26",))
    t2 = threading.Thread(target=worker, args=("TIM\nSU100/02MAR26\n0900 1030",))
    t1.start(); t2.start(); t1.join(); t2.join()
    # both applied against a consistent payload; journal has both entries
    assert len(sd.journal_for(inst["id"])) == 2
    payload = json.loads(
        registry.instance_payload_path(inst).read_text(encoding="utf-8"))
    fns = {(f["flightNumber"], f["departureUTCDateTime"][:10])
           for f in payload["flights"]}
    assert ("SU100", "2026-03-01") not in fns             # CNL applied
    su2 = next(f for f in payload["flights"]
               if f["departureUTCDateTime"].startswith("2026-03-02"))
    assert su2["departureUTCDateTime"] == "2026-03-02T09:00:00"  # TIM applied


def test_apply_crew_never_deletes():
    p = _payload()
    recs = sd.parse_crew_feed("crew.csv",
                              b"id,name,homeAirport,skills\n"
                              b"P9,New Nine,LED,Captain\n")
    out, diff = sd.apply_crew(p, recs)
    ids = {e["id"] for e in out["employees"]}
    assert ids == {"P1", "P9"}                    # P1 не удалён
    assert diff["missing_in_feed"] == ["P1"]      # но честно перечислен


# ---------------------------------------------------- receive_* discipline

@pytest.fixture()
def inst(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "JOURNAL_PATH", tmp_path / "journal.json")
    pfile = tmp_path / "payload.json"
    pfile.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setattr(registry, "instance_payload_path",
                        lambda inst: pfile)
    return {"id": "upload-u--x", "label": "x", "source": "upload",
            "payload_path": "data/x.json"}


def test_receive_schedule_preview_then_apply(inst):
    prev = sd.receive_schedule(inst, "CNL\nSU100/01MAR26", ADMIN)
    assert prev["applied"] is False
    assert sd.journal_for(inst["id"]) == []        # preview не журналится
    out = sd.receive_schedule(inst, "CNL\nSU100/01MAR26", ADMIN, apply=True)
    assert out["applied"] is True
    j = sd.journal_for(inst["id"])
    assert len(j) == 1 and j[0]["kind"] == "schedule"
    # повторная идентичная дельта — громкий отказ
    with pytest.raises(sd.DeltaError, match="уже была применена"):
        sd.receive_schedule(inst, "CNL\nSU100/01MAR26", ADMIN, apply=True)


def test_receive_rejects_seed_instances(inst):
    seed = dict(inst, source="gerad")
    with pytest.raises(sd.DeltaError, match="загруженным"):
        sd.receive_schedule(seed, "CNL\nSU100/01MAR26", ADMIN)


def test_invalid_result_not_saved(inst, monkeypatch):
    # NEW с аэропортом вне справочника валидацию не пройдёт.
    # P0-3: слотам инстанса даём идентичности, чтобы дельта дошла именно
    # до валидации (а не отказала раньше на шаблоне состава).
    pfile = registry.instance_payload_path(inst)
    pl = json.loads(pfile.read_text(encoding="utf-8"))
    for i, f in enumerate(pl["flights"]):
        f["flightLegId"] = f"LEG{i}"
    pl["flightAssignments"][0]["flightLegId"] = "LEG0"
    pfile.write_text(json.dumps(pl), encoding="utf-8")
    bad = "NEW\nSU300/07MAR26\nAAA BBB 1000 1100"
    with pytest.raises(sd.DeltaError, match="невалиден"):
        sd.receive_schedule(inst, bad, ADMIN, apply=True)
    # payload на диске не тронут, журнал пуст
    payload = json.loads(pfile.read_text(encoding="utf-8"))
    assert not any(f["flightNumber"] == "SU300" for f in payload["flights"])
    assert sd.journal_for(inst["id"]) == []


def test_crew_delta_invalid_result_not_saved(inst):
    """Valid parse but a result that fails validation (new crew whose
    homeAirport is not in the airport catalog) must NOT be persisted."""
    before = registry.instance_payload_path(inst).read_text(encoding="utf-8")
    with pytest.raises(sd.DeltaError, match="невалиден"):
        sd.receive_crew(inst, "crew.csv",
                        b"id,name,homeAirport,skills\nP9,Nine,ZZZ,Captain\n",
                        ADMIN, apply=True)
    after = registry.instance_payload_path(inst).read_text(encoding="utf-8")
    assert before == after and sd.journal_for(inst["id"]) == []


def test_receive_crew_apply(inst):
    out = sd.receive_crew(inst, "crew.csv", CREW_CSV.encode(), ADMIN,
                          apply=True)
    assert out["applied"] is True
    payload = json.loads(
        registry.instance_payload_path(inst).read_text(encoding="utf-8"))
    ids = {e["id"] for e in payload["employees"]}
    assert "P9" in ids
    j = sd.journal_for(inst["id"])
    assert j[0]["kind"] == "crew" and j[0]["summary"]["added"] == 1


# --- P0-3 (аудит документации 2026-07-20): NEW создаёт места экипажа --------


def _mat_payload():
    """Полностью материализованный payload: SU100 дважды, legId у рейсов и
    слотов, по одному месту Captain на вхождение."""
    return {
        "airports": [{"code": "SVO"}, {"code": "LED"}],
        "employees": [{"id": "P1", "homeAirport": "SVO",
                       "skills": ["Captain"], "name": "One"}],
        "flights": [
            {"flightNumber": "SU100", "flightLegId": "L1",
             "departureAirport": "SVO",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "LED",
             "arrivalUTCDateTime": "2026-03-01T09:20:00"},
            {"flightNumber": "SU100", "flightLegId": "L2",
             "departureAirport": "SVO",
             "departureUTCDateTime": "2026-03-02T08:00:00",
             "arrivalAirport": "LED",
             "arrivalUTCDateTime": "2026-03-02T09:20:00"}],
        "flightAssignments": [
            {"id": "a1", "flight": "SU100", "flightLegId": "L1",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": None},
            {"id": "a2", "flight": "SU100", "flightLegId": "L2",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": None}],
    }


def test_new_on_materialized_number_creates_seats_and_identity():
    from _leg_rekey import rekey_blockers
    p = _mat_payload()
    out, s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))
    assert s["created"] == 1 and s["seats_created"] == 1
    new_f = [f for f in out["flights"]
             if f["departureUTCDateTime"].startswith("2026-03-03")][0]
    assert new_f["flightLegId"] == "SU100|2026-03-03T08:00:00"
    seats = [x for x in out["flightAssignments"]
             if x.get("flightLegId") == new_f["flightLegId"]]
    assert len(seats) == 1
    assert seats[0]["requiredSkill"] == "Captain"
    assert seats[0]["indexInFlight"] == 0
    ids = [x["id"] for x in out["flightAssignments"]]
    assert len(ids) == len(set(ids))
    assert rekey_blockers(out) == []


def test_new_multiplying_unique_number_stamps_existing():
    from _leg_rekey import rekey_blockers
    p = _mat_payload()
    # один SU100 без legId (номер уникален) + слот по номеру
    p["flights"] = [dict(p["flights"][0])]
    del p["flights"][0]["flightLegId"]
    p["flightAssignments"] = [{"id": "a1", "flight": "SU100",
                               "indexInFlight": 0,
                               "requiredSkill": "Captain",
                               "employee": None}]
    out, s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))
    assert s["leg_ids_stamped"] == 2          # рейс + его слот
    assert s["seats_created"] == 1
    old_f = [f for f in out["flights"]
             if f["departureUTCDateTime"].startswith("2026-03-01")][0]
    assert old_f["flightLegId"] == "SU100|2026-03-01T08:00:00"
    old_slot = [x for x in out["flightAssignments"] if x["id"] == "a1"][0]
    assert old_slot["flightLegId"] == old_f["flightLegId"]
    assert rekey_blockers(out) == []


def test_new_on_repeated_number_without_identity_refuses():
    p = _payload()                             # SU100 дважды, без legId
    with pytest.raises(sd.DeltaError, match="материализуйте"):
        sd.apply_schedule(
            p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))


def test_new_identity_collision_refuses_loudly():
    p = _mat_payload()
    p["flights"][0]["flightLegId"] = "SU100|2026-03-03T08:00:00"
    p["flightAssignments"][0]["flightLegId"] = "SU100|2026-03-03T08:00:00"
    with pytest.raises(sd.DeltaError, match="конфликт идентичности"):
        sd.apply_schedule(
            p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))


def test_new_number_uses_dominant_pattern():
    p = _mat_payload()
    # конкурирующие паттерны: L3=CP (один голос, ПЕРВЫМ в списке — ловит
    # мутанта «первый попавшийся»), L1/L2=CP+FO (два голоса) -> побеждает
    # CP+FO как более частый
    p["flights"].append(
        {"flightNumber": "SU101", "flightLegId": "L3",
         "departureAirport": "SVO",
         "departureUTCDateTime": "2026-03-03T08:00:00",
         "arrivalAirport": "LED",
         "arrivalUTCDateTime": "2026-03-03T09:20:00"})
    p["flightAssignments"] = [
        {"id": "c1", "flight": "SU101", "flightLegId": "L3",
         "indexInFlight": 0, "requiredSkill": "Captain", "employee": None},
        {"id": "a1", "flight": "SU100", "flightLegId": "L1",
         "indexInFlight": 0, "requiredSkill": "Captain", "employee": None},
        {"id": "b1", "flight": "SU100", "flightLegId": "L1",
         "indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": None},
        {"id": "a2", "flight": "SU100", "flightLegId": "L2",
         "indexInFlight": 0, "requiredSkill": "Captain", "employee": None},
        {"id": "b2", "flight": "SU100", "flightLegId": "L2",
         "indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": None},
    ]
    out, s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU500/05MAR26\nSVO LED 1000 1130", p))
    seats = [x for x in out["flightAssignments"] if x["flight"] == "SU500"]
    assert [x["requiredSkill"] for x in seats] == ["Captain", "FirstOfficer"]
    assert [x["indexInFlight"] for x in seats] == [0, 1]
    assert s["seats_created"] == 2


def test_new_without_any_assignments_refuses():
    p = _mat_payload()
    p["flightAssignments"] = []
    with pytest.raises(sd.DeltaError, match="нет ни одного места"):
        sd.apply_schedule(
            p, sd.parse_asm("NEW\nSU500/05MAR26\nSVO LED 1000 1130", p))


def test_new_with_diverging_occurrence_templates_refuses():
    p = _mat_payload()
    p["flightAssignments"].append(
        {"id": "b2", "flight": "SU100", "flightLegId": "L2",
         "indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": None})
    with pytest.raises(sd.DeltaError, match="неоднозначен"):
        sd.apply_schedule(
            p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))


def test_new_prefers_occurrence_template_over_dominant():
    # Мутант «всегда доминирующий паттерн» должен падать: у номера SU100
    # свой шаблон (CP), а экземпляр доминируется (CP, FO) на SU3xx
    p = _mat_payload()
    for i in (1, 2, 3):
        p["flights"].append(
            {"flightNumber": f"SU30{i}", "flightLegId": f"M{i}",
             "departureAirport": "SVO",
             "departureUTCDateTime": f"2026-03-0{i}T12:00:00",
             "arrivalAirport": "LED",
             "arrivalUTCDateTime": f"2026-03-0{i}T13:00:00"})
        p["flightAssignments"] += [
            {"id": f"m{i}a", "flight": f"SU30{i}", "flightLegId": f"M{i}",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": None},
            {"id": f"m{i}b", "flight": f"SU30{i}", "flightLegId": f"M{i}",
             "indexInFlight": 1, "requiredSkill": "FirstOfficer",
             "employee": None}]
    out, s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))
    seats = [x for x in out["flightAssignments"]
             if x.get("flightLegId") == "SU100|2026-03-03T08:00:00"]
    assert [x["requiredSkill"] for x in seats] == ["Captain"]


def test_new_propagates_crew_slot_id_only_when_used():
    p = _mat_payload()
    p["flightAssignments"][0]["crewSlotId"] = "a1"
    p["flightAssignments"][1]["crewSlotId"] = "a2"
    out, _s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))
    seat = [x for x in out["flightAssignments"]
            if x.get("flightLegId") == "SU100|2026-03-03T08:00:00"][0]
    assert seat["crewSlotId"] == seat["id"]
    # без конвенции crewSlotId в payload ключ не ставится
    p2 = _mat_payload()
    out2, _ = sd.apply_schedule(
        p2, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p2))
    seat2 = [x for x in out2["flightAssignments"]
             if x.get("flightLegId") == "SU100|2026-03-03T08:00:00"][0]
    assert "crewSlotId" not in seat2


def test_new_seat_id_collision_uniquified():
    p = _mat_payload()
    p["flightAssignments"][0]["id"] = "SU100|2026-03-03T08:00:00#0"
    out, _s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU100/03MAR26\nSVO LED 0800 0920", p))
    ids = [x["id"] for x in out["flightAssignments"]]
    assert len(ids) == len(set(ids))
    assert "SU100|2026-03-03T08:00:00#0+" in ids


def test_new_dominant_pattern_ignores_unstamped_repeated_numbers():
    # Ревью P0 (HIGH): слоты повторного номера БЕЗ flightLegId раньше
    # схлопывались в одну мега-группу — NEW нового номера синтезировал
    # конкатенированный шаблон-монстр
    p = {
        "airports": [{"code": "SVO"}, {"code": "LED"}],
        "employees": [{"id": "P1", "homeAirport": "SVO",
                       "skills": ["Captain"]}],
        "flights": [
            # повторный номер без идентичностей: 3 даты × 2 места
            *({"flightNumber": "SU100", "departureAirport": "SVO",
               "departureUTCDateTime": f"2026-03-0{i}T08:00:00",
               "arrivalAirport": "LED",
               "arrivalUTCDateTime": f"2026-03-0{i}T09:20:00"}
              for i in (1, 2, 3)),
            # уникальный материализованный рейс — единственный честный голос
            {"flightNumber": "SU200", "flightLegId": "U1",
             "departureAirport": "SVO",
             "departureUTCDateTime": "2026-03-01T12:00:00",
             "arrivalAirport": "LED",
             "arrivalUTCDateTime": "2026-03-01T13:00:00"}],
        "flightAssignments": [
            *({"id": f"s{i}{j}", "flight": "SU100", "indexInFlight": j,
               "requiredSkill": "Captain", "employee": None}
              for i in (1, 2, 3) for j in (0, 1)),
            {"id": "u1", "flight": "SU200", "flightLegId": "U1",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": None}],
    }
    out, s = sd.apply_schedule(
        p, sd.parse_asm("NEW\nSU500/05MAR26\nSVO LED 1000 1130", p))
    seats = [x for x in out["flightAssignments"] if x["flight"] == "SU500"]
    # шаблон — от честного U1 (1 место), а не 6-местный монстр от SU100
    assert len(seats) == 1
    assert s["seats_created"] == 1
