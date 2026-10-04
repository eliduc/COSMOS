"""Закрепление величин, от которых зависят переданные заказчику числа.

Аудит R9 требует классовых исправлений, и часть из них меняет рабочий
каталог расчёта. Внешняя проверка показала конкретную угрозу: файл
действующих правил `webapp/config/airline_default.json` НЕ отслеживается
системой контроля версий (`.gitignore`, строка 126), а окружение дочернего
процесса получает переменную `CREW_RULES_CONFIG` ТОЛЬКО когда этот файл
существует (`_evolution_bench.build_env`). Значит любой прогон в дереве,
собранном из ревизии, читал бы встроенные умолчания вместо действующих
правил — и делал бы это молча.

Сегодня расхождение замаскировано совпадением трёх независимых таблиц
умолчаний (Python, Kotlin, отслеживаемый образец). Аудит от 14 июля назвал
это «риском дрейфа, хотя сейчас идентичны». Настоящий файл превращает
совпадение в проверяемый контракт: как только одна из таблиц уедет,
покраснеет тест, а не расчёт заказчика.

Тесты намеренно опираются только на содержимое репозитория и не читают
данные заказчика: ни строки расписания, ни экипажа здесь нет.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import rules_config

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "webapp" / "config" / "airline_default.json.example"
LIVE = REPO / "webapp" / "config" / "airline_default.json"
KOTLIN_RULES = (REPO / "kotlin-solver" / "src" / "main" / "kotlin" / "org"
                / "acme" / "flighcrewscheduling" / "solver" / "RulesConfig.kt")

# Контракт воспроизводимости: шесть правил яруса 1 плюс три правила состава
# экипажа (выключены нулём). Значения продублированы здесь НАМЕРЕННО —
# закрепление обязано быть независимым от источника, иначе тест сверяет
# модуль сам с собой и остаётся зелёным при любой правке.
PINNED = {
    "min_sit_min": 30,
    "max_landings_per_duty": 5,
    "max_duty_span_min": 840,
    "min_rest_min": 600,
    "max_monthly_block_min": 5100,
    "max_pairing_span_min": 5760,
    "comp_no_two_reserves": 0,
    "comp_min_pair_seniority_weight": 0,
    "comp_language_mix": 0,
}

# Имя поля Kotlin -> ключ Python. Раскладка camelCase против snake_case —
# единственная причина, по которой две таблицы нельзя сверить механически
# без карты.
KOTLIN_TO_PY = {
    "minSitMin": "min_sit_min",
    "maxLandingsPerDuty": "max_landings_per_duty",
    "maxDutySpanMin": "max_duty_span_min",
    "minRestMin": "min_rest_min",
    "maxMonthlyBlockMin": "max_monthly_block_min",
    "maxPairingSpanMin": "max_pairing_span_min",
    "compNoTwoReserves": "comp_no_two_reserves",
    "compMinPairSeniorityWeight": "comp_min_pair_seniority_weight",
    "compLanguageMix": "comp_language_mix",
}

_KOTLIN_FIELD = re.compile(
    r"val\s+(\w+)\s*:\s*Int\s*=\s*([0-9]+(?:\s*\*\s*[0-9]+)*)\s*,")


def _kotlin_defaults() -> dict:
    """Значения по умолчанию из data class Values решателя.

    Значения записаны выражениями (`14 * 60`), поэтому произведение
    вычисляется здесь — но ТОЛЬКО произведение целых, без обращения к
    интерпретатору выражений: разбор чужого файла не должен уметь
    исполнять код.
    """
    src = KOTLIN_RULES.read_text(encoding="utf-8")
    start = src.index("data class Values(")
    body = src[start:src.index(")", start)]
    out: dict = {}
    for name, expr in _KOTLIN_FIELD.findall(body):
        key = KOTLIN_TO_PY.get(name)
        if key is None:
            continue
        value = 1
        for part in expr.split("*"):
            value *= int(part.strip())
        out[key] = value
    return out


def test_tier1_rule_contract_is_pinned():
    """Контракт воспроизводимости не меняется молча."""
    assert rules_config.DEFAULTS == PINNED, (
        "изменение умолчаний правил меняет ЧИСЛА заказчика; правка "
        "допустима только вместе с пересчётом переданных величин")


def test_kotlin_defaults_match_python_defaults():
    """Третья таблица умолчаний (решатель) совпадает с питоновской.

    Расхождение означало бы, что этап 1 и этап 2 решают разные задачи —
    и заметить это можно было бы только по числам.
    """
    assert _kotlin_defaults() == PINNED


def test_tracked_example_resolves_to_the_pinned_contract():
    """Отслеживаемый образец даёт тот же набор правил, что и умолчания.

    Именно образец окажется в дереве, собранном из ревизии. Пока он
    совпадает с контрактом, материализация не меняет постановку задачи;
    как только разойдётся — тест покраснеет ДО того, как разойдутся числа.
    """
    assert EXAMPLE.exists(), "отслеживаемый образец настроек обязан быть в репозитории"
    assert rules_config.load_from(EXAMPLE) == PINNED


def test_live_airline_config_is_carried_into_the_child_environment(
        tmp_path, monkeypatch):
    """Действующие правила доезжают до дочернего процесса — и только так.

    Вторая половина теста фиксирует СЕГОДНЯШНЮЮ опасность в явном виде:
    без файла переменная не выставляется вовсе, и движки уходят на
    встроенные умолчания молча. Отказ вместо молчания вводится вместе с
    укреплением контура (коммит 2); до тех пор поведение обязано быть
    хотя бы названным, а не подразумеваемым.
    """
    import _evolution_bench

    monkeypatch.delenv(rules_config.ENV_VAR, raising=False)

    with_cfg = tmp_path / "with"
    (with_cfg / "webapp" / "config").mkdir(parents=True)
    cfg = with_cfg / "webapp" / "config" / "airline_default.json"
    cfg.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    env = _evolution_bench.build_env(with_cfg)
    assert env.get(rules_config.ENV_VAR) == str(cfg.resolve())

    without = tmp_path / "without"
    (without / "webapp" / "config").mkdir(parents=True)
    env2 = _evolution_bench.build_env(without)
    assert rules_config.ENV_VAR not in env2, (
        "дерево без действующих настроек обязано быть отличимо от дерева "
        "с ними — иначе расхождение правил становится незаметным")


@pytest.mark.parametrize("path", [None, "no-such-file.json"])
def test_absent_rules_path_falls_back_to_defaults_today(path):
    """Питоновская половина повторяет поведение решателя: нет файла — тихие
    умолчания. Тест закрепляет это КАК ИЗВЕСТНЫЙ ДЕФЕКТ, чтобы правка,
    вводящая отказ, была видна как осознанная смена контракта, а не как
    случайная регрессия."""
    assert rules_config.load_from(path) == PINNED
