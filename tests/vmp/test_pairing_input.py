# -*- coding: utf-8 -*-
"""Г-П4: транслятор «пакет → вход ortools-построителя».

Охраняемое: пороги ТОЛЬКО из applied (недостающий — честный отказ с
перечнем, не молчаливое умолчание); min_sit — строже из применённых
(дом/вне); базы — из данных payload, не из констант; след
происхождения каждого ключа."""
import pytest

from vmp import pairing_input as pi

APPLIED = {"min_sit_home_min": 45, "min_sit_outstation_min": 60,
           "max_duty_span_min": 720, "min_rest_home_min": 720,
           "min_rest_outstation_min": 600}
PAYLOAD = {
    "employees": [{"id": "E1", "base": "SVG", "skills": ["CP"]},
                  {"id": "E2", "base": "PLY", "skills": ["CP"]},
                  {"id": "E3", "base": "PLY", "skills": ["FO"]}],
    # Горизонт 5 календарных дней (1–5 сентября включительно).
    "flights": [{"departureUTCDateTime": "2026-09-01T08:00:00Z",
                 "arrivalUTCDateTime": "2026-09-01T10:00:00Z"},
                {"departureUTCDateTime": "2026-09-05T08:00:00Z",
                 "arrivalUTCDateTime": "2026-09-05T21:00:00Z"}],
}


def test_full_translation_with_provenance():
    args, prov = pi.builder_args(APPLIED, PAYLOAD, budget_s=120)
    s = " ".join(args)
    assert "--sit-min 60" in s          # строже из 45/60
    assert prov["--sit-min"]["from"] == "min_sit_outstation_min"
    assert "--duty-span 720" in s
    assert "--rest-base 720" in s
    assert "--rest-off 600" in s
    assert "--bases PLY,SVG" in s       # из данных, сортировано
    assert "--budget-s 120" in s
    assert "--method columns" in s
    assert prov["--bases"]["from"] == "payload.employees.homeAirport"


def test_ярус_отдыха_после_длинной_смены_общей_нормой():
    """Построитель судит отдых двумя ярусами и без полной таблицы
    отказывается (живой отказ на наборе H0 2026-09-03: два флага из
    четырёх). ВМП отдельного пола после смены сверх базового предела
    не устанавливает — действует ОБЩАЯ норма той же стороны, и это
    названо в следе происхождения."""
    args, prov = pi.builder_args(APPLIED, PAYLOAD, budget_s=120)
    s = " ".join(args)
    assert "--rest-base-long 720" in s
    assert "--rest-off-long 600" in s
    assert "общая норма" in prov["--rest-base-long"]["rule"]
    assert prov["--rest-off-long"]["from"] == "min_rest_outstation_min"


def test_ярус_отдыха_после_длинной_смены_своей_нормой():
    """Норма для длинной смены в ВМП есть — подаётся она, а не общая."""
    applied = dict(APPLIED, min_rest_home_long_min=900,
                   min_rest_outstation_long_min=780)
    args, prov = pi.builder_args(applied, PAYLOAD, budget_s=120)
    s = " ".join(args)
    assert "--rest-base-long 900" in s and "--rest-off-long 780" in s
    assert prov["--rest-base-long"]["from"] == "min_rest_home_long_min"
    assert "rule" not in prov["--rest-off-long"]


def test_без_обычного_яруса_длинный_не_подаётся():
    """Обычного пола нет — построитель и так остановится перечнем
    недостающих порогов; выдумывать длинный ярус не из чего."""
    applied = {k: v for k, v in APPLIED.items()
               if k != "min_rest_home_min"}
    with pytest.raises(pi.PairingInputError) as e:
        pi.builder_args(applied, PAYLOAD, budget_s=120)
    assert "rest-base" in str(e.value)


def _soft_pkg():
    """Пакет с мягкими правилами части «Пейринг» трёх семей (как H0)."""
    return {"model": {"rules": [
        {"id": "S-EXT", "kind": "SOFT", "zone": "PAIRING", "weight": 15,
         "template_fit": {"family": "duty_extension_penalty", "category": "A",
                          "params": {"long_duty_threshold": {"value": 720},
                                     "duty_extension_cap": {"value": 780}}}},
        {"id": "S-NIGHT", "kind": "SOFT", "zone": "PAIRING", "weight": 10,
         "export_solver": {"family": "layover_penalty"}},
        {"id": "S-WEEK", "kind": "SOFT", "zone": "PAIRING", "weight": 25,
         "export_solver": {"family": "calendar_windows",
                           "days": ["friday", "saturday"], "tz": "base-local"}},
        {"id": "S-ROST", "kind": "SOFT", "zone": "ROSTERING", "weight": 3,
         "export_solver": {"family": "balanced_workload"}},
        {"id": "H-1", "kind": "HARD", "zone": "PAIRING"},
    ]}}


def test_композиция_целевой_штрафы_части_в_ключи_построителя():
    """Канон ВМП: целевая = критерий + Σ штрафов мягких правил части.
    Ночёвка → --w-night из пакета (перекрывает ноль исполнителя
    критерия), окно дней → --w-weekend-night/--weekend-days, продление
    → --w-long-duty/--long-duty-threshold; ростеринг не берётся."""
    pens = pi.pairing_soft_penalties(_soft_pkg())
    assert [p["rule_id"] for p in pens] == ["S-EXT", "S-NIGHT", "S-WEEK"]
    crit = {"kind": "min_person_days", "rate_per_duty": 100,
            "source_rule": "O-X"}
    args, prov = pi.builder_args(APPLIED, PAYLOAD, budget_s=60,
                                 criterion=crit, soft_penalties=pens)
    s = " ".join(args)
    assert "--w-span-day 100" in s
    # последний --w-night побеждает: 10 из пакета после нуля критерия
    last = len(args) - 1 - args[::-1].index("--w-night")
    assert args[last + 1] == "10"
    assert "--w-weekend-night 25 --weekend-days 4,5" in s
    assert "--w-long-duty 15 --long-duty-threshold 720" in s
    assert prov["--w-night"]["value"] == 10 and "S-NIGHT" in prov["--w-night"]["from"]
    assert prov["--weekend-days"]["value"] == "4,5"
    assert prov["--long-duty-threshold"]["value"] == 720
    # Без правила ночёвки вес явно 0; чужая семья — отказ.
    args0, prov0 = pi.builder_args(APPLIED, PAYLOAD, budget_s=60,
                                   soft_penalties=[])
    assert args0[-2:] == ["--w-night", "0"] and "нет правила" in prov0["--w-night"]["from"]
    with pytest.raises(pi.PairingInputError) as e:
        pi.builder_args(APPLIED, PAYLOAD, budget_s=60, soft_penalties=[
            {"rule_id": "S-X", "family": "нет_такой", "weight": 1}])
    assert "исполнителя" in str(e.value)
    with pytest.raises(pi.PairingInputError):
        pi.builder_args(APPLIED, PAYLOAD, budget_s=60, soft_penalties=[
            {"rule_id": "S-W", "family": "calendar_windows", "weight": 5,
             "days": ["friday"], "tz": "UTC"}])


def test_deterministic_profile_flags_and_provenance():
    """Д1 плана прямого хода: профиль даёт --deterministic-time и один
    поток; без него ни того, ни другого; нулевой предел — отказ."""
    args, prov = pi.builder_args(APPLIED, PAYLOAD, budget_s=120,
                                 deterministic_time=30)
    assert args[-4:] == ["--deterministic-time", "30.0", "--workers", "1"]
    assert prov["--deterministic-time"]["value"] == 30.0
    assert prov["--workers"]["value"] == 1
    plain, prov0 = pi.builder_args(APPLIED, PAYLOAD, budget_s=120)
    assert "--deterministic-time" not in plain and "--workers" not in plain
    assert "--deterministic-time" not in prov0
    with pytest.raises(pi.PairingInputError):
        pi.builder_args(APPLIED, PAYLOAD, budget_s=120, deterministic_time=0)


def test_base_day_cap_computed_but_inactive_by_default():
    """Живой урок v5 (2026-08-26): жёсткая ёмкость утяжеляет точное
    покрытие до отказа — по умолчанию значение ВЫЧИСЛЯЕТСЯ и пишется
    в след с пометкой, но флаг в запуск не подаётся (до тёплого
    старта точной фазы построителя)."""
    args, prov = pi.builder_args(APPLIED, PAYLOAD, budget_s=120)
    assert "--base-day-cap" not in " ".join(args)
    cap = prov["--base-day-cap"]
    assert cap["value"] == "PLY=5,SVG=0"
    assert "не подано" in cap["note"]


def test_base_day_cap_min_role_times_horizon():
    """Виток «путь к нулю» (отмашка владельца 2026-08-26): ёмкость
    базы = мин. штат по ролям × дни горизонта; PLY несёт пару CP+FO
    (1×5), SVG — только CP (мин по ролям 0 → ёмкость 0). Нормы
    выходных в порогах нет → полный горизонт с честной пометкой."""
    args, prov = pi.builder_args(APPLIED, PAYLOAD, budget_s=120,
                                 base_cap_active=True)
    s = " ".join(args)
    assert "--base-day-cap PLY=5,SVG=0" in s
    assert "безнормовая" in prov["--base-day-cap"]["rule"]


def test_base_day_cap_subtracts_days_off_norm_from_package():
    """Норма выходных — правило ПАКЕТА (порог min_days_off), не
    константа транслятора: рабочие дни = горизонт − норма."""
    applied = dict(APPLIED, min_days_off=2)
    args, prov = pi.builder_args(applied, PAYLOAD, budget_s=120,
                                 base_cap_active=True)
    # 5 дней горизонта − 2 нормы = 3 рабочих дня на человека роли.
    assert "--base-day-cap PLY=3,SVG=0" in " ".join(args)
    rule = prov["--base-day-cap"]["rule"]
    assert "min_days_off" in rule and "×3" in rule


def test_base_day_cap_without_roles_uses_full_headcount():
    payload = {
        "employees": [{"id": "E1", "base": "SVG"},
                      {"id": "E2", "base": "PLY"},
                      {"id": "E3", "base": "PLY"}],
        "flights": [{"departureUTCDateTime": "2026-09-01T08:00:00Z",
                     "arrivalUTCDateTime": "2026-09-02T10:00:00Z"}],
    }
    args, prov = pi.builder_args(APPLIED, payload, budget_s=60,
                                 base_cap_active=True)
    assert "--base-day-cap PLY=4,SVG=2" in " ".join(args)
    assert "ролей в данных нет" in prov["--base-day-cap"]["rule"]


def test_no_flights_refused():
    payload = {"employees": PAYLOAD["employees"]}
    with pytest.raises(pi.PairingInputError) as e:
        pi.builder_args(APPLIED, payload, budget_s=60)
    assert "горизонт" in str(e.value)


def test_missing_threshold_fails_closed_with_list():
    bad = dict(APPLIED)
    del bad["max_duty_span_min"]
    with pytest.raises(pi.PairingInputError) as e:
        pi.builder_args(bad, PAYLOAD, budget_s=60)
    assert "duty-span" in str(e.value)


def test_empty_applied_refused():
    with pytest.raises(pi.PairingInputError) as e:
        pi.builder_args({}, PAYLOAD, budget_s=60)
    assert "отчёт покрытия" in str(e.value)


def test_no_bases_refused():
    with pytest.raises(pi.PairingInputError) as e:
        pi.builder_args(APPLIED, {"employees": []}, budget_s=60)
    assert "базы" in str(e.value).lower()


def test_bool_values_are_not_numbers():
    bad = dict(APPLIED, max_duty_span_min=True)
    with pytest.raises(pi.PairingInputError):
        pi.builder_args(bad, PAYLOAD, budget_s=60)


def test_source_rules_contract_from_forms():
    """Г-П5 (словарь решателя 2026-08-27, заморожен): матрица форм →
    duty_span_matrix плоскими пятёрками целых; условный пол →
    trans_tz_hours; плоские пороги applied → min_sit_min (строже из
    двух) и max_landings_per_duty; без форм — контракта нет."""
    forms = {"matrices": [{
        "threshold": "max_duty_span_min", "tz": "base-local",
        "rows": [
            {"start_from_min": 360, "start_to_min": 900,
             "sectors_from": 1, "sectors_to": 2, "value_min": 780,
             "as_written": "x", "rule": "R-1"},
            {"start_from_min": 360, "start_to_min": 900,
             "sectors_from": 3, "sectors_to": 3, "value_min": 750,
             "as_written": "x", "rule": "R-2"}]}],
        "conditional_floors": [{
            "threshold": "min_rest_outstation_min",
            "condition": {"kind": "tz_diff_hours",
                          "relation": "min", "value": 4},
            "value_min": 960, "as_written": "x", "rule": "R-3"}]}
    c = pi.source_rules_contract(
        forms, dict(APPLIED, max_landings_per_duty=3))
    assert c["duty_span_matrix"] == [[360, 900, 1, 2, 780],
                                     [360, 900, 3, 3, 750]]
    assert c["trans_tz_hours"] == 4.0
    assert c["min_sit_min"] == 60          # строже из 45/60
    assert c["max_landings_per_duty"] == 3
    # Таблица отдыха: одна универсальная строка по раскладке
    # LayoverTier; «нет ужесточения» = обычная колонка той же
    # стороны; вне-дальняя — условный пол 960.
    assert c["layover_tiers"] == [[1440, 720, 600, 720, 600,
                                   720, 960]]
    # Ловушка 24 ч: явный предел рабочего времени обязателен.
    assert c["work_time_limit_by_tier"] == [720]
    # Сшитый вердикт владельца словаря: незаданный ключ — чужая
    # калибровка класса; отсутствующие правила гасятся ЯВНЫМИ
    # нейтрализаторами, «нет предела» — null у трёх ключей.
    assert c["night_ratio"] == 2.0
    assert c["dark_seq_max"] == 999
    assert c["no_relay_rules"] == [] and c["teleportation"] == []
    assert c["max_dh_per_flight"] is None
    assert c["max_dh_per_day"] is None
    assert c["monthly_flight_time_limit_min"] is None
    # Условный пол есть → trans_tz_hours из форм, не нейтрализатор.
    assert c["trans_tz_hours"] == 4.0
    # Без условного пола — нейтрализатор 99.
    c2 = pi.source_rules_contract(
        {"matrices": forms["matrices"], "conditional_floors": []},
        APPLIED)
    assert c2["trans_tz_hours"] == 99.0
    # Мир без машинных форм (чистый ВМП: пороги живут только в
    # посадках; находка аудита H0 2026-09-01): контракт строится из
    # applied — он и есть носитель layover-таблицы построителя.
    # Без applied контракта нет.
    c3 = pi.source_rules_contract(None, APPLIED)
    assert c3["layover_tiers"] == [[1440, 720, 600, 720, 600,
                                    720, 600]]
    assert c3["work_time_limit_by_tier"] == [720]
    assert "duty_span_matrix" not in c3
    assert c3["trans_tz_hours"] == 99.0
    assert pi.source_rules_contract(None, {}) is None
    # Без max_duty_span в applied таблица отдыха — честный отказ.
    bad = {k: v for k, v in APPLIED.items()
           if not k.startswith("max_duty")}
    import pytest as _pt
    with _pt.raises(pi.PairingInputError):
        pi.source_rules_contract(forms, bad)


def test_criterion_min_person_days_zeroes_way_weights():
    """Спецификация 1.9 + чистота цели (Г-П4 п. 7.1): ставка критерия
    уходит в --w-duty, собственные коэффициенты способа явно
    обнуляются; след называет источник-правило."""
    crit = {"kind": "min_person_days", "rate_per_duty": 100,
            "as_written": "минимум человеко-дней … 100",
            "source_rule": "O-X"}
    args, prov = pi.builder_args(APPLIED, PAYLOAD, budget_s=60,
                                 criterion=crit)
    s = " ".join(args)
    # Семантика: человеко-день = календарный день в отъезде →
    # --w-span-day; цена за смену обнуляется вместе с прочими.
    assert "--w-span-day 100" in s
    for k in ("--w-duty 0", "--w-night 0", "--w-dh 0",
              "--w-chain 0"):
        assert k in s, s
    assert prov["--w-span-day"]["from"] == "критерий пакета: O-X"
    assert "чистота цели" in prov["--w-chain"]["from"]
    # Источник цены в отчёте построителя — критерий, не умолчания
    # (карточка серии печатает это поле).
    assert "--criterion-source критерий пакета: O-X" in s
    assert prov["--criterion-source"]["value"] == "критерий пакета: O-X"


def test_unknown_criterion_kind_refused():
    with pytest.raises(pi.PairingInputError) as e:
        pi.builder_args(APPLIED, PAYLOAD, budget_s=60,
                        criterion={"kind": "min_cost",
                                   "rate_per_duty": 1})
    assert "вне перечня" in str(e.value)


def test_bad_criterion_rate_refused():
    with pytest.raises(pi.PairingInputError):
        pi.builder_args(APPLIED, PAYLOAD, budget_s=60,
                        criterion={"kind": "min_person_days",
                                   "rate_per_duty": 0})


def test_календарная_оболочка_минутного_размаха():
    from vmp import pairing_input as pin
    assert pin.calendar_days_hull(11520) == 9
    assert pin.calendar_days_hull(7445) == 7
    assert pin.calendar_days_hull(1440) == 2
    out = pin.source_rules_contract(
        {"matrices": [], "conditional_floors": []}, {},
        span_min=11520)
    assert out["max_pairing_days"] == 9
    # applied строже словаря — оболочка не затирает явное значение.
    out2 = pin.source_rules_contract(
        {"matrices": [], "conditional_floors": []},
        {"max_pairing_days": 6}, span_min=11520)
    assert out2["max_pairing_days"] == 6


def test_горизонт_данных_как_безнормовая_граница_командировки():
    """Мир без нормы границы командировки и без файла условий (линия
    нано 2026-08-29): весь горизонт рейсов — безнормовая верхняя
    граница (семантика chain_max_days). Приоритет: applied > оболочка
    размаха > горизонт; без всех трёх ключ не пишется."""
    from vmp import pairing_input as pin
    forms = {"matrices": [], "conditional_floors": []}
    out = pin.source_rules_contract(forms, {}, horizon_days=5)
    assert out["max_pairing_days"] == 5
    # applied и оболочка размаха главнее горизонта.
    assert pin.source_rules_contract(
        forms, {"max_pairing_days": 6},
        horizon_days=5)["max_pairing_days"] == 6
    assert pin.source_rules_contract(
        forms, {}, span_min=1440,
        horizon_days=5)["max_pairing_days"] == 2
    # Ни одного источника — ключ честно отсутствует (страж dobor2
    # у построителя остаётся хозяином отказа).
    assert "max_pairing_days" not in pin.source_rules_contract(
        forms, {})


def test_сдвиг_шкалы_времени_в_контракт_ноль_законен():
    """Линия нано 2026-08-29: base_tz_offset_min приходит параметром
    правила через applied-rules; НОЛЬ — значение шкалы, не пустота
    (суд по типу, не по истинности); без параметра ключа нет."""
    from vmp import pairing_input as pin
    forms = {"matrices": [], "conditional_floors": []}
    out = pin.source_rules_contract(forms, {"base_tz_offset_min": 0})
    assert out["base_tz_offset_min"] == 0
    assert "base_tz_offset_min" not in pin.source_rules_contract(
        forms, {})
    # Булево True — не «1 минута»: чужой тип не подхватывается.
    assert "base_tz_offset_min" not in pin.source_rules_contract(
        forms, {"base_tz_offset_min": True})


def test_зазор_разбора_нейтрализатор_и_приоритет_applied():
    """Мир без норм подготовки/разбора (нано): зазор «разбор → перегон»
    гасится явным нейтрализатором 0 — но ТОЛЬКО в мире без файла
    условий (маркер horizon_days); applied-норма главнее; байты миров
    с условиями (BA) не трогаются."""
    from vmp import pairing_input as pin
    forms = {"matrices": [], "conditional_floors": []}
    assert pin.source_rules_contract(
        forms, {}, horizon_days=5)["dh_board_after_debrief_min"] == 0
    assert pin.source_rules_contract(
        forms, {"dh_board_after_debrief_min": 45}, horizon_days=5
    )["dh_board_after_debrief_min"] == 45
    # Мир с файлом условий (horizon не передан) — ключа нет вовсе.
    assert "dh_board_after_debrief_min" not in pin.source_rules_contract(
        forms, {})
    # Пересадочные перегоны: мир без условий не запрещал — включены
    # (подавление сужало бы пространство сверх ВМП); байты BA не трогаем.
    assert pin.source_rules_contract(
        forms, {}, horizon_days=5)["transfer_deadheads"] is True
    assert "transfer_deadheads" not in pin.source_rules_contract(
        forms, {})
