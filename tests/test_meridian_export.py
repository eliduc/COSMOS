

# --- состав экипажа для граничных участков (2026-07-27) --------------------

def test_duty_without_any_crew_seat_is_refused():
    """Рабочий наряд без единого места экипажа — утверждение «рейс
    выполняет никто». Именно такой наряд делал выгрузку нечитаемой
    обратным адаптером: решения трёх уровней свободы нельзя было вернуть
    в конвейер для расстановки людей."""
    from _payload_to_meridian import export
    payload = {"flights": [
        {"flightNumber": "L1", "departureAirport": "AAA",
         "departureUTCDateTime": "2026-08-01T06:00:00",
         "arrivalAirport": "BBB",
         "arrivalUTCDateTime": "2026-08-01T09:00:00"}],
        "flightAssignments": []}          # спроса нет вовсе
    catalog = {"selected": [{"fns": ["L1"], "active_fns": ["L1"]}]}
    _doc, _check, errors = export(payload, catalog, 3)
    assert any("без единого места экипажа" in e for e in errors), errors


def test_crew_seats_fall_back_to_the_source_file():
    """Спроса в каноне нет, но исходный файл заказчика про этот рейс
    знает — берём его состав, а не выдумываем."""
    from _payload_to_meridian import export
    payload = {"flights": [
        {"flightNumber": "L1", "sourceLegId": "SRC1",
         "departureAirport": "AAA",
         "departureUTCDateTime": "2026-08-01T06:00:00",
         "arrivalAirport": "BBB",
         "arrivalUTCDateTime": "2026-08-01T09:00:00"}],
        "flightAssignments": []}
    catalog = {"selected": [{"fns": ["L1"], "active_fns": ["L1"]}]}
    source = {"legs": [{"legId": "SRC1", "dutyCrewTypes": {"D1": "Р"}}],
              "duties": [{"dutyId": "D1", "complements": [
                  {"complementRank": "CA"}, {"complementRank": "FO"}]}]}
    doc, check, errors = export(payload, catalog, 3, source)
    assert not errors, errors
    assert check["complements_taken_from_source"] == 1
    ranks = sorted(c["complementRank"]
                   for c in doc["duties"][0]["complements"])
    assert ranks == ["CA", "FO"]


def test_canonical_demand_still_wins_over_the_source():
    """Запасной источник — именно запасной: там, где канон спрос знает,
    берётся канон."""
    from _payload_to_meridian import export
    payload = {"flights": [
        {"flightNumber": "L1", "sourceLegId": "SRC1",
         "departureAirport": "AAA",
         "departureUTCDateTime": "2026-08-01T06:00:00",
         "arrivalAirport": "BBB",
         "arrivalUTCDateTime": "2026-08-01T09:00:00"}],
        "flightAssignments": [
            {"flight": "L1", "requiredSkill": "CA", "indexInFlight": 0}]}
    catalog = {"selected": [{"fns": ["L1"], "active_fns": ["L1"]}]}
    source = {"legs": [{"legId": "SRC1", "dutyCrewTypes": {"D1": "Р"}}],
              "duties": [{"dutyId": "D1", "complements": [
                  {"complementRank": "CA"}, {"complementRank": "FO"}]}]}
    doc, check, errors = export(payload, catalog, 3, source)
    assert not errors and check["complements_taken_from_source"] == 0
    assert len(doc["duties"][0]["complements"]) == 1
