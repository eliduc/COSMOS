# -*- coding: utf-8 -*-
"""База приписки в приёмке цепочек берётся ИЗ ДАННЫХ, а не из литерала.

История дефекта. В приёмке стояла константа с кодом аэропорта, и с ней
сравнивались первый вылет и последний прилёт каждой цепочки. Пакет
обезличивания поменял эту константу на другой код, а вызывающая сторона
(`webapp/app.py`) базу не передаёт. На данных, где база другая, проверки
«цепочка начинается на базе» и «цепочка заканчивается на базе»
перевернулись бы почти на всех цепочках — и выглядели бы совершенно
правдоподобно, потому что числа остались бы числами.

Проверяется поэтому не форма, а суть: литерала в коде больше нет,
источник базы назван в отчёте, а отсутствие данных даёт отказ, а не
подстановку.
"""

import pytest

import pairing_audit as pa


def _pay(homes):
    return {"employees": [{"homeAirport": h} for h in homes],
            "flights": []}


def test_база_выводится_из_сотрудников():
    base, how = pa.base_of(_pay(["BSE", "BSE", "BSE"]))
    assert base == "BSE"
    assert "выведена из набора" in how
    assert "3" in how


def test_смешанные_базы_дают_преобладающую_и_называют_остальные():
    base, how = pa.base_of(_pay(["BSE", "BSE", "ALT"]))
    assert base == "BSE"
    # Прочие базы обязаны быть НАЗВАНЫ: молчаливое большинство скрыло бы
    # набор, который на самом деле многобазовый.
    assert "ALT" in how


def test_без_данных_отказ_а_не_литерал():
    with pytest.raises(pa.AuditError) as e:
        pa.base_of({"employees": [], "flights": []})
    assert "не выводится" in str(e.value)
    # Пустые и пробельные значения не считаются базой.
    with pytest.raises(pa.AuditError):
        pa.base_of(_pay(["", "  ", None]))


def test_явная_база_побеждает_вывод():
    base, how = pa.base_of(_pay(["BSE", "BSE"]), base="ALT")
    assert base == "ALT"
    assert how == "задана явно"


def test_литерала_кода_аэропорта_в_модуле_больше_нет():
    """Контроль от возврата дефекта тем же путём.

    Именно константа модуля делала правильность зависимой от строки в
    коде; её отсутствие — предмет проверки, а не стиль.
    """
    assert not hasattr(pa, "BASE_DEFAULT")
    import inspect
    src = inspect.getsource(pa.audit)
    assert "base=None" in src.replace(" ", "")


class _Rules:
    """Минимальные правила: приёмке нужны только эти величины."""
    base_tz_offset_min = 0
    min_sit_min = 30
    max_dh_per_day = 9
    max_dh_per_flight = 9
    max_pairing_days = 9
    no_relay_rules = True

    def relay_forbidden(self, fns, types):
        return False

    def equivalent_station_gap(self, a, b):
        return None

    def min_off_base_rest(self):
        return 600

    def pairing_days(self, *a, **k):
        return 1


def _catalog_and_payload(dep, arr):
    """Одна цепочка из одного рейса: вылет dep, прилёт arr."""
    flight = {"flightNumber": "F1", "flightLegId": "F1@1",
              "departureAirport": dep, "arrivalAirport": arr,
              "departureUTCDateTime": "2026-08-01T06:00:00",
              "arrivalUTCDateTime": "2026-08-01T08:00:00",
              "aircraftType": "T1"}
    catalog = {"selected": [{"fns": ["F1"], "active_fns": ["F1"]}]}
    payload = {"flights": [flight],
               "employees": [{"homeAirport": "BSE"}]}
    return catalog, payload


def test_отчёт_называет_базу_и_способ_её_получения():
    cat, pay = _catalog_and_payload("BSE", "BSE")
    res = pa.audit(cat, pay, _Rules())
    assert res["base"] == "BSE"
    assert "выведена из набора" in res["base_source"]
    named = {c["name"]: c for c in res["checks"]}
    # Цепочка начинается и заканчивается на выведенной базе — нарушений нет.
    assert named["цепочка начинается на базе"]["violations"] == 0
    assert named["цепочка заканчивается на базе"]["violations"] == 0


def test_чужая_база_переворачивает_проверки_и_это_видно():
    """Суть дефекта: с чужой базой числа остаются числами.

    Проверка нужна, чтобы «ноль нарушений» нельзя было получить
    случайно: при базе не из данных нарушением становится КАЖДАЯ
    цепочка, и отчёт честно говорит, откуда база взялась.
    """
    cat, pay = _catalog_and_payload("BSE", "BSE")
    res = pa.audit(cat, pay, _Rules(), base="FAR")
    assert res["base"] == "FAR"
    assert res["base_source"] == "задана явно"
    named = {c["name"]: c for c in res["checks"]}
    assert named["цепочка начинается на базе"]["violations"] == 1
    assert named["цепочка заканчивается на базе"]["violations"] == 1
