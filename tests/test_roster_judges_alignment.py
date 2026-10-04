# -*- coding: utf-8 -*-
"""Судьи ростеринга — по одной постановке (уроки первого боевого
ростеринга).

1. Коды допуска места (`seat_codes_for`): роль требует код только на
   типах участков, где она есть в составе, — требование «бизнес-класс на
   узкофюзеляжном» не существует у авиакомпании и делало часть мест
   невыполнимыми в принципе.
2. Месяц налёта в проверке траекторий — в поясе базы, а не в UTC:
   вечерние участки последнего числа месяца по UTC уезжали в другой
   месяц и давали ложные «block>limit».
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from _pairing_builder import seat_codes_for  # noqa: E402
from _gerad_trajectory_check import check_employee_sequence  # noqa: E402


# --- seat_codes_for ----------------------------------------------------

FLEET = {"L1": "A321neo", "L2": "B787-8"}
ROLES = {"L1": Counter({"PURS": 1, "CA": 3}),
         "L2": Counter({"PURS": 1, "CA C-cl": 1, "CA": 9})}


def _codes(role, active=("L1", "L2")):
    return seat_codes_for(role, active, FLEET.__getitem__,
                          lambda x: ROLES[x])


def test_role_requires_code_only_where_present():
    # бизнес-класс есть только в составе широкофюзеляжного участка
    assert _codes("CA C-cl") == ["B787-8_CA C-cl"]


def test_common_role_requires_all_types():
    assert _codes("CA") == ["A321neo_CA", "B787-8_CA"]
    assert _codes("PURS") == ["A321neo_PURS", "B787-8_PURS"]


def test_unknown_role_falls_back_to_all_types():
    # роль сверх спроса (усиление) — прежнее требование, без выдумки
    assert _codes("TRI") == ["A321neo_TRI", "B787-8_TRI"]


# --- месяц налёта в поясе базы ----------------------------------------

def _leg(dep_min, arr_min):
    # (dep, arr, dep_st, arr_st, is_tdh, pid, duty_key)
    return (dep_min, arr_min, "BSE", "BSE", False, "C_0", ("C_0", 0))


# EPOCH проверки = 2000-01-01; 31.08.2026 19:30 UTC в минутах от эпохи
AUG31_1930 = (26 * 365 + 7 + 242) * 1440 + 19 * 60 + 30


def test_block_month_in_base_timezone():
    """История ложных «block>limit» первого боевого ростеринга:
    90 мин днём 31.08 + 70 мин в 19:30 UTC 31.08 (= 00:30 1.09 по
    местному времени базы). По UTC оба в августе: 160 > 100 — ложное
    превышение. В поясе базы: август 90, сентябрь 70 — превышения нет."""
    day = _leg(AUG31_1930 - 400, AUG31_1930 - 310)      # 90 мин днём
    eve = _leg(AUG31_1930, AUG31_1930 + 70)             # 70 мин вечером
    v_utc = defaultdict(int)
    check_employee_sequence([day, eve], v_utc,
                            block_limit=100, tz_offset_min=0)
    assert v_utc["block>limit"] == 1        # прежний ложный вердикт
    v_base = defaultdict(int)
    check_employee_sequence([day, eve], v_base,
                            block_limit=100, tz_offset_min=300)
    assert v_base["block>limit"] == 0       # в поясе базы — законно
    # превышение внутри одного местного месяца по-прежнему ловится
    v_real = defaultdict(int)
    check_employee_sequence([day, _leg(AUG31_1930 - 200, AUG31_1930 - 180)],
                            v_real, block_limit=100, tz_offset_min=300)
    assert v_real["block>limit"] == 1       # 90+20=110 > 100 в августе
