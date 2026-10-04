# -*- coding: utf-8 -*-
"""Стандартный состав экипажа обязан быть в условиях, а не подразумеваться.

История. Стандартный состав читался из группы B прямым обращением, и
файл без него падал ГРОМКО. Пакет обезличивания добавил запасное чтение
из группы A с откатом к пустому словарю — отказ стал тихим. Цена тишины
велика: `crew_std()` при пустой таблице отдаёт МИНИМАЛЬНЫЙ состав вместо
стандартного, а построитель цепочек берёт его базой планирования. План
получился бы недокомплектованным и совершенно правдоподобным.

Проверяется поэтому не наличие ключа, а поведение: нет таблицы — отказ
с названной причиной; есть в любом из двух мест — работает.
"""

import copy

import pytest

_conditions = pytest.importorskip("_conditions")

from test_conditions_mode import SPEC                        # noqa: E402


def _spec_without_std():
    spec = copy.deepcopy(SPEC)
    spec["A"].pop("crew_std_by_fleet", None)
    spec["B"].pop("b1_crew_std_by_fleet", None)
    return spec


def test_нет_стандартного_состава_ни_в_одной_группе_отказ():
    with pytest.raises(KeyError) as e:
        _conditions.Conditions(_spec_without_std())
    assert "стандартного состава" in str(e.value)
    # Причина названа вместе с ценой тишины — иначе отказ не объясняет,
    # почему он важнее удобства.
    assert "минимальному составу" in str(e.value)


def test_пустая_таблица_состава_тоже_отказ():
    spec = _spec_without_std()
    spec["B"]["b1_crew_std_by_fleet"] = {"values": {}}
    with pytest.raises(KeyError):
        _conditions.Conditions(spec)


def test_старое_место_в_группе_B_по_прежнему_читается():
    """Совместимость с прежними файлами условий — предмет проверки."""
    c = _conditions.Conditions(copy.deepcopy(SPEC))
    assert c.crew_std("A320") == 5


def test_новое_место_в_группе_A_побеждает_старое():
    spec = copy.deepcopy(SPEC)
    spec["A"]["crew_std_by_fleet"] = {"values": {"A320": 6}}
    c = _conditions.Conditions(spec)
    assert c.crew_std("A320") == 6


# ---------- снятая особая форма b2 (нормы по типу ВС) ----------
# Пакет «умолчания-в-данные»: нормы по типу живут в общих таблицах
# crew_min_by_fleet / crew_std_by_fleet, где имя типа — ключ таблицы
# В ДАННЫХ. Особая ветвь с зашитым именем типа удалена; страж
# fail-closed не даёт архивному файлу с values потерять минимум
# состава тихо.

def test_особая_форма_b2_со_значениями_отказ():
    spec = copy.deepcopy(SPEC)
    spec["B"]["b2_b787_9_norms"] = {"values": {"min": 6}}
    with pytest.raises(_conditions.ConditionsError) as e:
        _conditions.Conditions(spec)
    assert "crew_min_by_fleet" in str(e.value)


def test_снятая_форма_b2_живёт_и_составов_не_меняет():
    """Форма живого файла UZ: ключ снят аудитом (withdrawn, без
    values) — конструирование проходит, составы только из общих
    таблиц, особая ветвь ничего не дописывает."""
    spec = copy.deepcopy(SPEC)
    spec["B"]["b2_b787_9_norms"] = {"withdrawn": True}
    c = _conditions.Conditions(spec)
    assert c.crew_std("A320") == 5
    assert c.crew_min("A320") == 4
    assert c.crew_min("B787-9") == 0
