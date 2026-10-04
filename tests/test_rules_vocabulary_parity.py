"""Паритет словаря правил между Python и Kotlin.

Словарь правил существует в ДВУХ независимых копиях: `rules_config.DEFAULTS`
на стороне Python и `RulesConfig.KNOWN_KEYS` в солвере. Обе стороны падают
на незнакомом ключе — и это правильно, но означает, что расхождение
словарей не «немного портит результат», а **убивает солвер на старте JVM**:
`IllegalStateException` при инициализации класса, то есть каждый расчёт
второго этапа умирает до начала работы, а выглядит это как отказ
инфраструктуры, а не как ошибка конфигурации.

Ни один тест этого не ловил. Проверка идёт ПО ИСХОДНИКУ Kotlin, а не по
рукописной копии списка: копия разошлась бы ровно так же, как разошлись
сами словари.

Значения по умолчанию сверяются тоже: одинаковые ключи с разными
умолчаниями дают Python и солверу разное представление о правилах, и
расхождение всплывёт как необъяснимая разница результатов, а не как отказ.
"""
import re

import pytest

import rules_config
from webapp import registry

KOTLIN_RULES = (registry.REPO_ROOT / "kotlin-solver" / "src" / "main" /
                "kotlin" / "org" / "acme" / "flighcrewscheduling" / "solver" /
                "RulesConfig.kt")

# camelCase поля Kotlin -> snake_case ключ конфигурации. Соответствие
# задано явно: вывести его автоматом нельзя (maxLandingsPerDuty ->
# max_landings_per_duty, но compNoTwoReserves -> comp_no_two_reserves),
# а угадывать в тесте, который защищает от расхождения, — плохая идея.
FIELD_TO_KEY = {
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


def _kotlin_source() -> str:
    """Исходник солвера БЕЗ комментариев.

    Комментарии убираются до разбора не для красоты: в них встречаются
    скобки («multirole increment 4»), и нежадный поиск закрывающей скобки
    обрывался на них — тест «находил» три ключа из девяти и обвинял в
    расхождении исправный словарь. Разборщик, ошибающийся молча, хуже
    отсутствующего теста."""
    if not KOTLIN_RULES.exists():
        pytest.skip(f"нет исходника солвера {KOTLIN_RULES}")
    src = KOTLIN_RULES.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)      # блочные
    src = re.sub(r"//[^\n]*", "", src)                   # строчные
    return src


def _kotlin_known_keys(src: str) -> set:
    m = re.search(r"KNOWN_KEYS\s*=\s*setOf\((.*?)\)", src, re.S)
    assert m, "не найден KNOWN_KEYS в RulesConfig.kt"
    keys = set(re.findall(r'"([a-z0-9_]+)"', m.group(1)))
    # страховка от повторения той же ошибки разбора: словарь заведомо не
    # состоит из пары ключей, и пустой список — тоже сигнал о поломке
    assert len(keys) >= 6, f"разбор KNOWN_KEYS дал подозрительно мало: {keys}"
    return keys


def _kotlin_defaults(src: str) -> dict:
    """Умолчания из `data class Values(...)`.

    Значения бывают арифметическими (`14 * 60`) — считаем их, но только
    из цифр и умножения: eval произвольного выражения из исходника в
    тесте не нужен никому."""
    m = re.search(r"data class Values\((.*?)\n    \)", src, re.S)
    assert m, "не найден data class Values в RulesConfig.kt"
    out = {}
    for field, expr in re.findall(
            r"val\s+(\w+)\s*:\s*Int\s*=\s*([0-9\s*]+?)\s*(?:,|$)",
            m.group(1), re.M):
        parts = [int(p) for p in re.findall(r"\d+", expr)]
        value = 1
        for p in parts:
            value *= p
        out[field] = value
    return out


def test_key_sets_match():
    """Один и тот же набор ключей с обеих сторон.

    Лишний ключ в Python = солвер падает на старте JVM. Лишний ключ в
    Kotlin = конфиг, который солвер примет, а Python отвергнет."""
    kotlin = _kotlin_known_keys(_kotlin_source())
    python = set(rules_config.DEFAULTS)
    assert kotlin == python, (
        f"словари разошлись: только в Kotlin {sorted(kotlin - python)}, "
        f"только в Python {sorted(python - kotlin)}")


def test_default_values_match():
    """Одинаковые ключи обязаны иметь одинаковые умолчания.

    Расхождение здесь не роняет ничего — оно тихо даёт двум половинам
    системы разное представление о правилах, и это хуже отказа."""
    src = _kotlin_source()
    kotlin_fields = _kotlin_defaults(src)
    missing = set(FIELD_TO_KEY) - set(kotlin_fields)
    assert not missing, f"поля Values не разобраны: {sorted(missing)}"

    mismatched = {}
    for field, key in FIELD_TO_KEY.items():
        kv, pv = kotlin_fields[field], rules_config.DEFAULTS[key]
        if kv != pv:
            mismatched[key] = {"kotlin": kv, "python": pv}
    assert not mismatched, f"умолчания разошлись: {mismatched}"


def test_mapping_covers_the_whole_vocabulary():
    """Новый ключ обязан появиться и в карте соответствия — иначе тесты
    выше молча перестанут его проверять."""
    assert set(FIELD_TO_KEY.values()) == set(rules_config.DEFAULTS)


def test_int32_ceiling_is_mirrored_on_both_sides():
    """Потолок Int32 — контракт солвера: значение больше него убивает JVM
    на старте (аудит P1-12). Python обязан отказать раньше."""
    src = _kotlin_source()
    assert "toInt()" in src, "изменился способ приведения к Int в солвере"
    assert rules_config.INT32_MAX == 2_147_483_647
    assert rules_config.validate_value("min_sit_min",
                                       rules_config.INT32_MAX + 1)


def test_composition_weight_cap_is_mirrored():
    """Потолок веса пары задан числом в обеих половинах — Kotlin хранит
    его литералом, поэтому сверяем именно литерал."""
    src = _kotlin_source()
    assert rules_config.COMP_MAX_PAIR_WEIGHT == 22
    assert re.search(r"0\.\.22|\b22\b", src), (
        "в солвере не найден потолок веса пары 22")
