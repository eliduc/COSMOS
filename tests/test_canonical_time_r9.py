"""Канонизация времени как свойство границы записи (аудит R9, P0-03).

Класс дефекта: «предикат вместо разборщика». В точке приёма стоял тест
подстроки — «есть ли в строке плюс или зет», — и он не видел
отрицательного смещения. Метка `-05:00` сохранялась зонированной, а затем
роняла расчёт стоимости и буфер стыковки на вычитании наивного и
зонированного времени.

Здесь закрывается не экземпляр (`-05:00`), а класс: разбор вызывается
безусловно, а хранимая форма проверяется постусловием. Тесты идут по всем
соседям формы, а не по одному найденному.
"""
from __future__ import annotations

import pytest

import _canonical_time as ct


def _flight(num: str, dep: str, arr: str) -> dict:
    return {"flightNumber": num, "departureAirport": "AAA",
            "arrivalAirport": "BBB",
            "departureUTCDateTime": dep, "arrivalUTCDateTime": arr}


def _payload(*flights) -> dict:
    return {"airports": [], "employees": [], "flights": list(flights),
            "flightAssignments": []}


@pytest.mark.parametrize("given,expected", [
    ("2026-08-01T10:00:00-05:00", "2026-08-01T15:00:00"),   # корень находки
    ("2026-08-01T10:00:00+03:00", "2026-08-01T07:00:00"),
    ("2026-08-01T10:00:00Z", "2026-08-01T10:00:00"),
    ("2026-08-01T10:00:00+00:00", "2026-08-01T10:00:00"),
    ("2026-08-01T10:00:00-05:30", "2026-08-01T15:30:00"),   # получасовой пояс
    ("2026-08-01T10:00:00", "2026-08-01T10:00:00"),         # уже канон
])
def test_every_offset_form_is_recalculated(given, expected):
    """Все формы смещения приводятся к одной хранимой, а не только те, что
    содержат плюс или зет."""
    assert ct.canonical_str(given) == expected


def test_negative_offset_reaches_the_store_canonical():
    """Корневой сценарий находки: отрицательное смещение на приёме."""
    p = _payload(_flight("F1", "2026-08-01T10:00:00-05:00",
                         "2026-08-01T12:00:00-05:00"))
    assert ct.canonicalize_payload(p) == 2
    assert p["flights"][0]["departureUTCDateTime"] == "2026-08-01T15:00:00"
    assert p["flights"][0]["arrivalUTCDateTime"] == "2026-08-01T17:00:00"
    ct.assert_canonical(p, "тест")


def test_canonicalization_is_idempotent():
    """Повторный прогон ничего не меняет — иначе миграция и повторная
    запись расходились бы между собой."""
    p = _payload(_flight("F1", "2026-08-01T10:00:00-05:00",
                         "2026-08-01T12:00:00Z"))
    ct.canonicalize_payload(p)
    snapshot = [dict(f) for f in p["flights"]]
    assert ct.canonicalize_payload(p) == 0
    assert p["flights"] == snapshot


def test_mixed_set_becomes_one_form():
    """Смешанный набор (зет, плюс, минус, наивная) после приёма имеет
    ЕДИНСТВЕННУЮ форму — именно смешение роняло активные обходы."""
    p = _payload(
        _flight("F1", "2026-08-01T06:00:00Z", "2026-08-01T08:00:00Z"),
        _flight("F2", "2026-08-01T11:00:00+03:00", "2026-08-01T13:00:00+03:00"),
        _flight("F3", "2026-08-01T05:00:00-05:00", "2026-08-01T07:00:00-05:00"),
        _flight("F4", "2026-08-01T20:00:00", "2026-08-01T22:00:00"))
    ct.canonicalize_payload(p)
    assert ct.non_canonical_marks(p) == []
    marks = [f["departureUTCDateTime"] for f in p["flights"]]
    assert all(ct.is_canonical(m) for m in marks)
    # и хронология сортируется строкой так же, как разобранным временем —
    # свойство, на которое опираются пять сортировок расчётного слоя
    assert sorted(marks) == sorted(marks, key=ct.parse_utc)


def test_leg_identity_is_not_rewritten_by_canonization():
    """Идентификатор участка содержит метку внутри себя, но канонизации НЕ
    подлежит: он личность, а не формула, и переживает перенос рейса. Его
    переписывание порвало бы ссылки слотов на участки."""
    f = _flight("F1", "2026-08-01T10:00:00-05:00", "2026-08-01T12:00:00-05:00")
    f["flightLegId"] = "F1|2026-08-01T10:00:00-05:00"
    p = _payload(f)
    ct.canonicalize_payload(p)
    assert p["flights"][0]["flightLegId"] == "F1|2026-08-01T10:00:00-05:00"


def test_assert_names_the_place_not_only_the_fact():
    """Отказ обязан называть адрес метки: иначе на наборе в тысячи рейсов
    он бесполезен."""
    p = _payload(_flight("F1", "2026-08-01T10:00:00-05:00",
                         "2026-08-01T12:00:00"))
    with pytest.raises(ct.NonCanonicalPayload) as exc:
        ct.assert_canonical(p, "граница расчёта")
    assert "flights[0].departureUTCDateTime" in str(exc.value)
    assert "граница расчёта" in str(exc.value)


def test_zero_offset_is_not_storable():
    """`+00:00` разбирается прекрасно и потому особенно опасен: значение
    верное, форма — зонированная, и читатель, сравнивающий её с наивной,
    падает так же, как на `-05:00`."""
    assert not ct.is_canonical("2026-08-01T10:00:00+00:00")
    assert ct.is_canonical("2026-08-01T10:00:00")


def test_compute_boundary_refuses_a_non_canonical_payload():
    """Общий вход расчётного слоя отказывает набору со смещением.

    Одной границы ЗАПИСИ для инварианта недостаточно: восемь из девяти
    базовых наборов реестра пишут корневые скрипты, а не веб-приложение,
    и такой набор попадает на диск мимо приложения. Заградитель на входе
    решателя закрывает и их, и прямой запуск движка из командной строки.
    """
    from _leg_identity import assert_representable, GeradPipelineUnsupported

    p = _payload(_flight("F1", "2026-08-01T10:00:00-05:00",
                         "2026-08-01T12:00:00"))
    with pytest.raises(GeradPipelineUnsupported) as exc:
        assert_representable(p, "тест.границы")
    assert "не канонизован" in str(exc.value)


def test_identity_kill_switch_does_not_disable_the_time_check(monkeypatch):
    """Аварийный выключатель идентичности гасит СВОЙ предмет и только его.

    Одна переменная, гасящая два разных контроля, — это тот же класс
    дефекта: механизм есть, но обходится одним движением.
    """
    from _leg_identity import assert_representable, GeradPipelineUnsupported

    monkeypatch.setenv("CREW_GERAD_STRICT_IDENTITY", "0")
    p = _payload(_flight("F1", "2026-08-01T10:00:00-05:00",
                         "2026-08-01T12:00:00"))
    with pytest.raises(GeradPipelineUnsupported):
        assert_representable(p, "тест.выключатель")

    # тот же выключатель по своему предмету продолжает работать
    dup = _payload(_flight("F1", "2026-08-01T06:00:00", "2026-08-01T08:00:00"),
                   _flight("F1", "2026-08-01T09:00:00", "2026-08-01T11:00:00"))
    assert_representable(dup, "тест.выключатель")


def test_claim_upload_canonizes_and_refuses_nothing_valid(tmp_path, monkeypatch):
    """Сквозная проверка точки приёма: набор со смещением ложится на диск
    уже каноническим. Теста на канонизацию приёма не существовало вовсе."""
    from webapp import registry

    monkeypatch.setattr(registry, "OWNERSHIP_PATH", tmp_path / "ownership.json")
    target = tmp_path / "payload.json"
    payload = _payload(_flight("F1", "2026-08-01T10:00:00-05:00",
                               "2026-08-01T12:00:00-05:00"))
    registry.claim_upload("inst-r9", target, payload, "user@example.com")

    import json
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["flights"][0]["departureUTCDateTime"] == "2026-08-01T15:00:00"
    assert ct.non_canonical_marks(stored) == []


# ---------------- точный контракт is_canonical (аудит R10, P1-01) --------

import pytest as _pytest


@_pytest.mark.parametrize("bad", [
    "2026-08-01T10:00:00+0300",      # компактное смещение
    "2026-08-01T10:00:00+05:30:15",  # смещение с секундами
    "2026-08-01T10:00:00.123",       # дробные секунды
    "garbage",                        # мусор
    "2026-08-01T10:00",              # без секунд
    "2026-08-01 10:00:00",           # пробел-разделитель
    "2026-08-01T10:00:00+00:00",     # смещение
    "2026-08-01T10:00:00Z",          # Z
    1722500000,                       # не строка
    None,
])
def test_is_canonical_exact_contract_rejects(bad):
    """`v == canonical_str(v)`: прежний хвостовой предикат принимал
    компактные смещения, дробные секунды и мусор."""
    from _canonical_time import is_canonical
    assert is_canonical(bad) is False


def test_is_canonical_accepts_only_stored_form():
    from _canonical_time import is_canonical
    assert is_canonical("2026-08-01T10:00:00") is True
