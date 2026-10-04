"""THE single source of the multi-airline tier-1 rule parameters.

The three engine copies (the Python engines/validators in this repo, the
Kotlin solver, and the webapp) read the SAME six rule parameters; on the
Python side every consumer imports this module instead of hardcoding the
numbers.

Resolution order:
  1. env CREW_RULES_CONFIG -> path to a JSON config file, either flat
       {"min_sit_min": 30, ...}
     or the webapp layout (webapp/config/airline_default.json):
       {"parameters": {"min_sit_min": 30, ...}}
  2. env absent, or no file at that path -> built-in DEFAULTS below.

Hard reproducibility contract: the DEFAULTS are exactly the historical
hardcoded values, so with no config the engines behave byte-for-byte as
they always did and all artifacts (fingerprints aside where explicitly
recomputed) are unchanged.

Loud failures: a config file that EXISTS but is malformed JSON, has
unknown keys, or has non-positive/non-numeric values raises ValueError —
never a silent fallback to defaults.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

ENV_VAR = "CREW_RULES_CONFIG"

# The six tier-1 rule parameters (minutes, except the landings count).
# These values ARE the reproducibility contract — do not edit casually.
DEFAULTS = {
    "min_sit_min": 30,                # min ground connection inside a duty
    "max_landings_per_duty": 5,       # rule 6: landings cap per duty
    "max_duty_span_min": 840,         # rule 7: duty elapsed span <= 14 h
    "min_rest_min": 600,              # rule 5: rest >= 10 h splits duties
    "max_monthly_block_min": 5100,    # monthly operating block <= 85 h
    "max_pairing_span_min": 5760,     # rule 2: pairing elapsed <= 4 days
    # --- Правила состава экипажа (multirole инкремент 4, 2026-07-19). ---
    # ВСЕ ВЫКЛЮЧЕНЫ по умолчанию (0) — поведение и артефакты байт-идентичны;
    # semantics: 0/1-флаги и порог, где 0 = правило не применяется. Состав
    # НЕ входит в пространство пейрингов, поэтому colgen-фингерпринт эти
    # ключи исключает (см. _gerad_colgen.space_fingerprint).
    "comp_no_two_reserves": 0,        # 1: не два резервиста в одном экипаже
    "comp_min_pair_seniority_weight": 0,  # >0: мин. сумма seniorityWeight пары
    "comp_language_mix": 0,           # 1: экипаж покрывает языки участка
}

# Ключи состава: допускают 0 (= выключено); флаги ограничены 0/1; порог
# пары весов ограничен максимумом 22 (два веса по 11) — паритет с Kotlin,
# который отверг бы значения вне Int (ревью 2026-07-19).
COMPOSITION_KEYS = frozenset({
    "comp_no_two_reserves", "comp_min_pair_seniority_weight",
    "comp_language_mix"})

# КАК правило добывается — солвером или только проверкой постфактум.
#
# Различие операционное, а не academic. Правило из ENFORCED_BY_SOLVER
# участвует в поиске: солвер знает о нём и ищет решение, которое ему
# удовлетворяет. Правило из CERTIFIED_ONLY солверу НЕИЗВЕСТНО — оно
# проверяется сертификатом после расчёта и, при нарушении, отправляет
# вердикт мимо OK. Значит включение такого правила может приводить к
# прогонам, которые падают раз за разом, и автоматического пути к
# легальному ростеру у системы НЕТ: солвер не подозревает, чего от него
# хотят.
#
# Ровно это и произошло с comp_language_mix: он разбирается обеими
# половинами, выставлен в интерфейсе и проверяется питоновским
# сертификатом (_crew_composition), но НИ ОДНО ограничение солвера его не
# использует — в отличие от двух других правил состава. Оператор, включив
# его, видел бы повторяющийся отказ и читал бы его как поломку солвера.
# Пока ограничения в солвере нет, честный ответ — назвать это вслух.
# Кто ИЩЕТ решение по каждому правилу. Движка два, и они отвечают за
# разные вопросы: "pairing" — питоновский конвейер первого этапа, он
# решает, какие пейринги вообще существуют; "roster" — Kotlin-солвер
# второго этапа, он рассаживает людей. Правило может добываться обоими.
# Пустой кортеж = не ищет НИКТО.
ENFORCED_BY: dict[str, tuple[str, ...]] = {
    "min_sit_min": ("pairing", "roster"),
    "max_landings_per_duty": ("pairing", "roster"),
    "max_duty_span_min": ("pairing", "roster"),
    "min_rest_min": ("pairing", "roster"),
    "max_monthly_block_min": ("pairing", "roster"),
    # длительность пейринга — правило ПОСТРОЕНИЯ пейринга, солверу
    # второго этапа знать о нём нечего: к его приходу пейринги уже собраны
    "max_pairing_span_min": ("pairing",),
    "comp_no_two_reserves": ("roster",),
    "comp_min_pair_seniority_weight": ("roster",),
    # НИКТО. Разбирается обеими половинами, выставлено в интерфейсе,
    # проверяется сертификатом — и ни одного ограничения в солвере.
    "comp_language_mix": (),
}


def enforcement(key: str) -> str:
    """'solver' | 'certificate' | 'unknown' — добывается ли правило поиском.

    Для экрана важно ровно это различие: «система ищет решение, где
    правило соблюдено» против «система лишь бракует нарушающие»."""
    engines = ENFORCED_BY.get(key)
    if engines is None:
        return "unknown"
    return "solver" if engines else "certificate"


ENFORCED_BY_SOLVER = frozenset(k for k, v in ENFORCED_BY.items() if v)
CERTIFIED_ONLY = frozenset(k for k, v in ENFORCED_BY.items() if not v)
_COMP_FLAGS = frozenset({"comp_no_two_reserves", "comp_language_mix"})
COMP_MAX_PAIR_WEIGHT = 22
# Kotlin RulesConfig.intOf() отвергает всё вне Int32 (аудит 2026-07-19
# P1-12: 3_000_000_000 проходил все Python-валидаторы и убивал JVM на
# старте) — режем на входе, до approve/apply.
INT32_MAX = 2_147_483_647


def validate_value(k: str, v) -> str | None:
    """Сообщение об ошибке для значения ИЗВЕСТНОГО ключа или None. ЕДИНАЯ
    семантика для загрузчика и всех вебапп-писателей (сценарии what-if) —
    ревью 2026-07-19: сценарный валидатор пропускал значения, на которых
    затем падал каждый запуск. Infinity/NaN режутся здесь же (isfinite),
    чтобы контрактный ValueError не подменялся OverflowError."""
    # isfinite/целостность проверяем БЕЗ конверсии во float для int-значений:
    # math.isfinite(10**400) и float(10**400) кидают OverflowError, подменяя
    # контрактное сообщение трейсбеком (ревью пакета 2026-07-19).
    if k in COMPOSITION_KEYS:
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or (isinstance(v, float)
                    and (not math.isfinite(v) or v != int(v)))
                or v < 0
                or (k in _COMP_FLAGS and v > 1)
                or (k == "comp_min_pair_seniority_weight"
                    and v > COMP_MAX_PAIR_WEIGHT)):
            return (f"{k} must be "
                    + ("0 or 1" if k in _COMP_FLAGS
                       else f"an integer 0..{COMP_MAX_PAIR_WEIGHT}")
                    + f", got {v!r}")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return f"{k} must be a positive number, got {v!r}"
    # inf/nan проходят «> 0», дробные и >Int32 роняют Kotlin intOf() при
    # старте солвера — контрактный отказ здесь, а не крах JVM (P1-12).
    if isinstance(v, float) and (not math.isfinite(v) or v != int(v)):
        return f"{k} must be a finite whole number, got {v!r}"
    if int(v) > INT32_MAX:
        return (f"{k} must fit a 32-bit integer (Kotlin solver contract, "
                f"max {INT32_MAX}), got {v!r}")
    return None

_cache: dict | None = None


def load() -> dict:
    """Resolve the effective rule set from env CREW_RULES_CONFIG (uncached).

    Missing env or missing file -> a copy of DEFAULTS. An existing but
    invalid file raises ValueError (loud failure).
    """
    return load_from(os.environ.get(ENV_VAR))


def load_from(path) -> dict:
    """Тот же разбор/нормализация, что и load(), но по явному пути —
    читатели манифестов (webapp/active_roster) обязаны нормализовать
    значения ТОЧНО как писатель (ревью пакета: сырой float 600.0 из файла
    против int 600 писателя делал все манифесты вечно stale)."""
    if not path:
        return dict(DEFAULTS)
    p = Path(path)
    if not p.exists():
        return dict(DEFAULTS)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"malformed rules config {p}: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"rules config {p}: top level must be a JSON object")
    params = raw
    if set(raw) == {"parameters"}:  # the webapp storage layout
        params = raw["parameters"]
        if not isinstance(params, dict):
            raise ValueError(
                f"rules config {p}: 'parameters' must be a JSON object")
    unknown = sorted(set(params) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"rules config {p}: unknown keys {unknown} "
                         f"(known: {sorted(DEFAULTS)})")
    out = dict(DEFAULTS)
    for k, v in params.items():
        err = validate_value(k, v)
        if err:
            raise ValueError(f"rules config {p}: {err}")
        if k in COMPOSITION_KEYS:
            out[k] = int(v)
        else:
            out[k] = int(v) if float(v).is_integer() else float(v)
    return out


def effective() -> dict:
    """Per-process cached effective rule set. Returns a copy so callers
    cannot mutate the cache."""
    global _cache
    if _cache is None:
        _cache = load()
    return dict(_cache)


def is_default() -> bool:
    """True iff the effective rule set equals the built-in DEFAULTS."""
    return effective() == DEFAULTS


def reset_cache() -> None:
    """Test hook: forget the cached rule set (call after changing the
    CREW_RULES_CONFIG env, then reload dependent modules)."""
    global _cache
    _cache = None
