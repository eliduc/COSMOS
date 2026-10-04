# -*- coding: utf-8 -*-
"""Контракт приёмника выгрузки авиакомпании (airline_ingest).

Главный класс проверок — РАСХОЖДЕНИЕ ИМЁН на шве «приёмник — решатель»
(исправление 2026-08-12): условие, попавшее не в то поле, не вызывает
ошибки — оно просто не действует. Проверка фактом на живом решателе
показала, что решатель применяет stationsDenied / qualificationWindows /
requiredQualifications (через requiredCodesAll каталога), поэтому
приёмник обязан писать именно эти имена. Тесты держат обе стороны шва:
что пишется каноническое имя и что устаревшее имя НЕ пишется.
"""
import json
from datetime import datetime

import pytest

import airline_ingest

EPOCH0 = datetime(1970, 1, 1)


def _min(dt: datetime) -> int:
    return int((dt - EPOCH0).total_seconds() // 60)


def _raw(**over):
    """Минимальная выгрузка в форме источника: два участка августа-2026,
    два сотрудника. Все правки — через over."""
    d = {
        "legs": [
            {
                "id": "L1", "flightCode": "ZZ101",
                "start": "2026-08-05T11:00:00+05:00[Asia/BaseCity]",
                "end": "2026-08-05T13:00:00+05:00[Asia/BaseCity]",
                "departureStation": "BSE", "arrivalStation": "FRU",
                "acType": "A320", "allocable": True,
                "requirements": [["Допуск СНГ"]],
            },
            {
                "id": "L2", "flightCode": "ZZ102",
                "start": "2026-08-05T14:00:00+05:00[Asia/BaseCity]",
                "end": "2026-08-05T16:00:00+05:00[Asia/BaseCity]",
                "departureStation": "FRU", "arrivalStation": "BSE",
                "acType": "A320", "allocable": True,
                "requirements": [],
            },
        ],
        "stations": [
            {"iata": "BSE", "name": "BaseCity", "latitude": 10.01,
             "longitude": 20.02, "timeZone": "Asia/BaseCity",
             "countryCode": "QQ", "cost": 60},
            {"iata": "FRU", "name": "Bishkek", "latitude": 43.06,
             "longitude": 74.48, "timeZone": "Asia/Bishkek",
             "countryCode": "KG", "cost": 1000000},
        ],
        "employees": [
            {
                "id": 1, "base": "BSE", "allocable": True,
                "ranks": {"A320": "CA"},
                "features": [
                    {"code": "Допуск СНГ",
                     "activeSince": "2020-01-01T00:00:00Z[UTC]",
                     "activeUntil": "2027-01-01T00:00:00Z[UTC]"},
                    {"code": "A320_CA",
                     "activeSince": "2019-01-01T00:00:00Z[UTC]",
                     "activeUntil": "2027-06-01T00:00:00Z[UTC]"},
                    {"code": "Неспрошенный код",
                     "activeSince": "2020-01-01T00:00:00Z[UTC]",
                     "activeUntil": "9999-12-31T00:00:00Z[UTC]"},
                ],
                "stationsDenied": [
                    {"code": "SSH",
                     "activeSince": "1940-01-01T00:00:00Z[UTC]",
                     "activeUntil": "2200-12-31T00:00:00Z[UTC]"},
                    {"code": "JED",
                     "activeSince": "2020-01-01T00:00:00Z[UTC]",
                     "activeUntil": "2026-07-01T00:00:00Z[UTC]"},
                    {"code": "=MD",
                     "activeSince": "1940-01-01T00:00:00Z[UTC]",
                     "activeUntil": "2200-12-31T00:00:00Z[UTC]"},
                    {"code": "001",
                     "activeSince": "1940-01-01T00:00:00Z[UTC]",
                     "activeUntil": "2200-12-31T00:00:00Z[UTC]"},
                ],
                "doNotFlyWith": ["2"],
            },
            {
                "id": 2, "base": "BSE", "allocable": True,
                "ranks": {"A320": "PURS"},
                "features": [
                    {"code": "Допуск СНГ",
                     "activeSince": "2020-01-01T00:00:00Z[UTC]"},
                    {"code": "A320_PURS",
                     "activeSince": "2020-01-01T00:00:00Z[UTC]"},
                ],
                "doNotFlyWith": [
                    {"code": "1",
                     "activeSince": "2020-01-01T00:00:00Z[UTC]",
                     "activeUntil": "2026-05-01T00:00:00Z[UTC]"},
                ],
            },
        ],
        "duties": [
            {"id": "D1", "ground": True,
             "start": "2026-08-07T09:00:00+05:00[Asia/BaseCity]",
             "end": "2026-08-07T18:00:00+05:00[Asia/BaseCity]"},
        ],
        "employeeAssignments": [
            {"duty": "D1", "employee": 1},
        ],
    }
    d.update(over)
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


# Синтетический носитель норм: значения НАМЕРЕННО ДРУГИЕ, чем у
# заказчика (CA — 2 места, тариф 777), — тест приёмки обезличенности
# шаблона: набор зелёный на числах, которых в коде нет и не было.
SYN_NORMS = {
    "form_version": 1,
    "customer": "TestAir",
    "crew_norms": {"A320": [["PURS", 1], ["CA", 2]]},
    "dh_tariff_assumed": 777,
}


@pytest.fixture()
def norms_file(tmp_path, monkeypatch):
    """Пишет носитель норм в tmp_path и подставляет его через env
    (autouse-изоляция conftest держит переменную пустой — «носителя
    нет», тест механизма задаёт свой файл сам)."""
    def write(doc=None, text=None):
        p = tmp_path / "airline-ingest-norms.json"
        if text is not None:
            p.write_text(text, encoding="utf-8")
        else:
            p.write_text(json.dumps(SYN_NORMS if doc is None else doc,
                                    ensure_ascii=False),
                         encoding="utf-8")
        monkeypatch.setenv("AIRLINE_INGEST_NORMS", str(p))
        return p
    return write


@pytest.fixture()
def converted(norms_file):
    norms_file()
    payload, rep = airline_ingest.convert(_raw())
    return payload, rep


# --- канонические имена на шве -----------------------------------------

def test_flight_required_qualifications_canonical_name(converted):
    payload, _ = converted
    by_fn = {f["commercialFlightNumber"]: f for f in payload["flights"]}
    assert by_fn["ZZ101"]["requiredQualifications"] == ["Допуск СНГ"]
    assert by_fn["ZZ102"]["requiredQualifications"] == []
    # устаревшее имя не пишется: условие в нём не действовало ни в одном
    # потребителе (построитель ждёт requiredQualifications)
    assert all("requirements" not in f for f in payload["flights"])


def test_stations_denied_executable_field(converted):
    payload, _ = converted
    e1 = next(e for e in payload["employees"] if e["id"] == "1")
    # действующий запрет перенесён; просроченный (JED, до 2026-07-01) —
    # нет; неинтерпретируемые коды (страновой, цифровой) сохранены
    assert e1["stationsDenied"] == ["SSH", "=MD", "001"]
    # запрет — не предпочтение: undesiredAirports не пишется никому
    assert all("undesiredAirports" not in e for e in payload["employees"])


def test_do_not_fly_with_windows(converted):
    payload, _ = converted
    e1 = next(e for e in payload["employees"] if e["id"] == "1")
    e2 = next(e for e in payload["employees"] if e["id"] == "2")
    assert e1["doNotFlyWith"] == ["2"]          # голая строка — всегда
    # запись со сроком, истёкшим до горизонта, отброшена со счётом
    assert "doNotFlyWith" not in e2


def test_qualification_windows_referenced_only(converted):
    payload, _ = converted
    e1 = next(e for e in payload["employees"] if e["id"] == "1")
    w = e1["qualificationWindows"]
    # коды спроса: требование участка И местовой код «тип_роль»
    assert set(w) == {"Допуск СНГ", "A320_CA"}
    # минуты эпохи, посчитанные независимо от реализации
    assert w["Допуск СНГ"] == [[_min(datetime(2020, 1, 1)),
                               _min(datetime(2027, 1, 1))]]


def test_qualification_windows_seat_codes(converted):
    """Местовые коды «тип_роль» — окна из тех же датированных допусков:
    их требует построитель на местах каталога, и без окон решатель
    блокирует любую посадку (урок первого прогона ростеринга)."""
    payload, _ = converted
    e1 = next(e for e in payload["employees"] if e["id"] == "1")
    assert e1["qualificationWindows"]["A320_CA"] == [
        [_min(datetime(2019, 1, 1)), _min(datetime(2027, 6, 1))]]
    # сотрудник 2 держит PURS-код — окно построено и ему
    e2 = next(e for e in payload["employees"] if e["id"] == "2")
    assert "A320_PURS" in e2["qualificationWindows"]


def test_qualification_window_open_end(converted):
    payload, _ = converted
    e2 = next(e for e in payload["employees"] if e["id"] == "2")
    # запись без activeUntil — открытое окно (верх = общий предел)
    assert e2["qualificationWindows"]["Допуск СНГ"] == [
        [_min(datetime(2020, 1, 1)), 4_000_000_000 // 60]]


def test_unavailable_days_kept(converted):
    payload, _ = converted
    e1 = next(e for e in payload["employees"] if e["id"] == "1")
    assert e1["unavailableDays"] == ["2026-08-07"]


# --- отчёт приёма ------------------------------------------------------

def test_report_counts(converted):
    _, rep = converted
    sd = rep["stations_denied"]
    assert sd["сотрудников"] == 1
    assert sd["станция"] == 1          # SSH
    assert sd["страновой"] == 1        # =MD
    assert sd["цифровой"] == 1         # 001
    assert sd["вне срока"] == 1        # JED
    qw = rep["qualification_windows"]
    # спрос: «Допуск СНГ» (рейс) + местовые A320_CA и A320_PURS
    assert qw["кодов в требованиях"] == 3
    assert qw["сотрудников с окнами"] == 2
    assert qw["требования без носителей"] == []
    assert rep["do_not_fly_with"]["сотрудников"] == 1
    assert rep["do_not_fly_with"]["вне срока"] == 1
    # роли носителя (PURS, CA) держат оба сотрудника — рассинхрона нет
    assert rep["роли норм без единого держателя ранга"] == []


def test_report_requirement_without_holder(norms_file):
    norms_file()
    raw = _raw()
    d = json.loads(raw.decode("utf-8"))
    d["legs"][0]["requirements"] = [["Допуск МВЛ"]]
    _, rep = airline_ingest.convert(json.dumps(d, ensure_ascii=False)
                               .encode("utf-8"))
    # спрос без единого носителя — виден в отчёте, а не потерян молча
    assert rep["qualification_windows"]["требования без носителей"] == [
        "Допуск МВЛ"]


# --- прежние гейты не ослабли ------------------------------------------

def test_duplicate_route_time_still_refused(norms_file):
    norms_file()
    raw = _raw()
    d = json.loads(raw.decode("utf-8"))
    dup = dict(d["legs"][0], id="L3", flightCode="ZZ999")
    d["legs"].append(dup)
    with pytest.raises(airline_ingest.IngestError):
        airline_ingest.convert(json.dumps(d, ensure_ascii=False).encode("utf-8"))


def test_unknown_aircraft_type_still_refused(norms_file):
    # судит по синтетическому носителю: TU-154 в нём нет — отказ
    norms_file()
    raw = _raw()
    d = json.loads(raw.decode("utf-8"))
    for leg in d["legs"]:
        leg["acType"] = "TU-154"
    with pytest.raises(airline_ingest.IngestError):
        airline_ingest.convert(json.dumps(d, ensure_ascii=False).encode("utf-8"))


def test_not_airline_export_refused(norms_file):
    # носитель есть — отказ именно о форме выгрузки, не о носителе
    norms_file()
    with pytest.raises(airline_ingest.IngestError, match="legs"):
        airline_ingest.convert(b'{"airports": [], "employees": []}')


# --- цены цели (решение владельца 2026-08-12) --------------------------

def test_deadhead_price_from_integrator_row(norms_file):
    norms_file()
    raw = _raw()
    d = json.loads(raw.decode("utf-8"))
    # dh-строка интегратора: дубликат L1 по маршруту и времени, с ценой
    d["legs"].append(dict(d["legs"][0], id="dh%L1", cost=5000))
    payload, rep = airline_ingest.convert(json.dumps(d, ensure_ascii=False)
                                     .encode("utf-8"))
    by_leg = {f["flightLegId"]: f for f in payload["flights"]}
    # оригинал получил ЦЕНУ dh-строки; сама строка отклонена
    assert by_leg["L1"]["deadheadCost"] == 5000
    assert rep["rejected"]["перегон из решения интегратора"] == 1
    # рейс без своей цены — тариф-допущение ИЗ НОСИТЕЛЯ: явное
    # синтетическое число, а не самоссылка на константу модуля
    assert by_leg["L2"]["deadheadCost"] == 777
    assert rep["цены"]["перегоны с ценой интегратора"] == 1
    assert rep["цены"]["перегоны по тарифу-допущению"] == 1
    assert rep["цены"]["тариф-допущение"] == 777


def test_layover_rates_with_sentinel(converted):
    payload, rep = converted
    ap = {a["code"]: a for a in payload["airports"]}
    # настоящая ставка переносится с признаком known
    assert ap["BSE"]["layoverCost"] == 60
    assert ap["BSE"]["layoverCostKnown"] is True
    # сторожевое 1 000 000 — не ставка: known=false, стоимость 0
    assert ap["FRU"]["layoverCost"] == 0
    assert ap["FRU"]["layoverCostKnown"] is False
    assert rep["цены"]["ставки станций известны"] == 1
    assert rep["цены"]["ставки неизвестны (сторожевое)"] == 1


# --- носитель норм контура («умолчания-в-данные», 2026-08-29) ----------

def test_norms_carrier_missing_refused(tmp_path, monkeypatch):
    # env пуст (autouse-изоляция conftest) — «носителя нет»: отказ
    # словами до любого счёта, а не расчёт чужими числами
    with pytest.raises(airline_ingest.IngestError, match="носитель"):
        airline_ingest.convert(_raw())
    # env указывает на несуществующий файл — в тексте полный путь и форма
    absent = tmp_path / "net-takogo.json"
    monkeypatch.setenv("AIRLINE_INGEST_NORMS", str(absent))
    with pytest.raises(airline_ingest.IngestError) as ei:
        airline_ingest.convert(_raw())
    assert str(absent) in str(ei.value)
    assert "crew_norms" in str(ei.value)


def test_norms_carrier_malformed_refused(norms_file):
    # не-JSON
    norms_file(text="{кривой json")
    with pytest.raises(airline_ingest.IngestError, match="JSON"):
        airline_ingest.convert(_raw())
    # без customer
    norms_file({k: v for k, v in SYN_NORMS.items() if k != "customer"})
    with pytest.raises(airline_ingest.IngestError, match="customer"):
        airline_ingest.convert(_raw())
    # число мест 0 — не положительное целое
    norms_file(dict(SYN_NORMS, crew_norms={"A320": [["PURS", 0]]}))
    with pytest.raises(airline_ingest.IngestError,
                       match="положительное целое"):
        airline_ingest.convert(_raw())


def test_no_tariff_and_unpriced_flights_refused(norms_file):
    # тариф-допущение опционален, но не молчалив: без него рейсы без
    # цены интегратора дают отказ СО СЧЁТОМ
    norms_file({k: v for k, v in SYN_NORMS.items()
                if k != "dh_tariff_assumed"})
    with pytest.raises(airline_ingest.IngestError) as ei:
        airline_ingest.convert(_raw())
    assert "у 2 рейсов" in str(ei.value)
    assert "dh_tariff_assumed" in str(ei.value)


def test_no_tariff_all_prices_from_data_ok(norms_file):
    # тарифа нет, но у каждого рейса цена интегратора — ветка спит,
    # конверсия проходит, отчёт говорит «не задан» явно
    norms_file({k: v for k, v in SYN_NORMS.items()
                if k != "dh_tariff_assumed"})
    d = json.loads(_raw().decode("utf-8"))
    d["legs"].append(dict(d["legs"][0], id="dh%L1", cost=5000))
    d["legs"].append(dict(d["legs"][1], id="dh%L2", cost=4200))
    payload, rep = airline_ingest.convert(
        json.dumps(d, ensure_ascii=False).encode("utf-8"))
    by_leg = {f["flightLegId"]: f for f in payload["flights"]}
    assert by_leg["L1"]["deadheadCost"] == 5000
    assert by_leg["L2"]["deadheadCost"] == 4200
    assert rep["цены"]["перегоны по тарифу-допущению"] == 0
    assert rep["цены"]["тариф-допущение"] == "не задан (все цены из данных)"


def test_template_generic_other_airline(norms_file):
    """Приёмка обезличенности шаблона: носитель «другой авиакомпании»
    (другие типы судов, другие роли) с согласованной выгрузкой
    исполняется БЕЗ правки кода — тип правила в коде, конкретика в
    контуре данных."""
    norms_file({"form_version": 1, "customer": "OtherAir",
                "crew_norms": {"Q400": [["SCC", 1], ["FA", 2]]},
                "dh_tariff_assumed": 55})
    d = json.loads(_raw().decode("utf-8"))
    for leg in d["legs"]:
        leg["acType"] = "Q400"
    d["employees"][0]["ranks"] = {"Q400": "SCC"}
    d["employees"][1]["ranks"] = {"Q400": "FA"}
    payload, rep = airline_ingest.convert(
        json.dumps(d, ensure_ascii=False).encode("utf-8"))
    # точные места: 2 рейса x (1 SCC + 2 FA)
    assert rep["seats"] == 6
    assert rep["seats_by_role"] == {"SCC": 2, "FA": 4}
    # порядок пар носителя задаёт indexInFlight мест
    first = sorted((s for s in payload["flightAssignments"]
                    if s["flight"].startswith("ZZ101")),
                   key=lambda s: s["indexInFlight"])
    assert [s["requiredSkill"] for s in first] == ["SCC", "FA", "FA"]
    # тариф-допущение — из носителя «другой авиакомпании»
    assert all(f["deadheadCost"] == 55 for f in payload["flights"])
    assert rep["роли норм без единого держателя ранга"] == []


def test_prefix_order_longest_first(norms_file):
    """Носитель с B787-9 и B787 с РАЗНЫМИ нормами: участок B787-9
    обязан получить норму ДЛИННОГО префикса (перечень префиксов —
    сортировка ключей носителя по длине убыванию), иначе применилась
    бы чужая норма МОЛЧА. Короткий тип в файле стоит ПЕРВЫМ — порядок
    записи носителя решать не должен."""
    norms_file({"form_version": 1, "customer": "TestAir",
                "crew_norms": {"B787": [["CA", 5]],
                               "B787-9": [["CA", 2]]},
                "dh_tariff_assumed": 777})
    d = json.loads(_raw().decode("utf-8"))
    for leg in d["legs"]:
        leg["acType"] = "B787-9(*)"     # плюс зачистка суффикса формата
    for e in d["employees"]:
        e["ranks"] = {"B787-9": "CA"}
    payload, rep = airline_ingest.convert(
        json.dumps(d, ensure_ascii=False).encode("utf-8"))
    # 2 рейса x 2 места (норма B787-9), а НЕ x 5 (норма B787)
    assert rep["seats"] == 4
    assert rep["seats_by_role"] == {"CA": 4}


def test_norm_roles_without_rank_holder_reported(norms_file):
    """Рассинхрон написания роли носителя со словарём рангов выгрузки
    (вплоть до гомоглифов: «СА» с кириллическими С и А против
    латинского «CA») виден в отчёте, но расчёта НЕ запрещает —
    правило «не ограничивать сверх ВМП»."""
    cyr_ca = "СА"             # СА кириллицей
    norms_file(dict(SYN_NORMS,
                    crew_norms={"A320": [["PURS", 1], [cyr_ca, 2]]}))
    payload, rep = airline_ingest.convert(_raw())
    assert payload["flightAssignments"]          # конверсия прошла
    assert rep["роли норм без единого держателя ранга"] == [cyr_ca]
