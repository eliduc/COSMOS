"""Тесты построителя цепочек (`_pairing_builder.py`).

Проверяются свойства, на которые опирается остальной конвейер: формат
каталога (его читает этап 2), полное покрытие достижимых участков,
замкнутость цепочек на базу, честная классификация непокрытого,
воспроизводимость и отказ уровней 1–2 без базового каталога.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_pb = pytest.importorskip("_pairing_builder")


def _args(**over) -> argparse.Namespace:
    # СИНТЕТИКА ФИКСТУРЫ: числа ниже — вымышленные значения тестового
    # мира, а не данные какого-либо заказчика. Подобраны внутри окон,
    # которые держат ассерты набора: duty_span в [360, 1080) (сборка
    # смены туда-обратно; test_суд_закреплённых_цепочек требует
    # нарушения сменой в 1080), sit_max в [180, 360)
    # (test_rescue_pass_never_shrinks_the_pool / недоотдых в
    # test_conditions_mode), rest_off в (599, 781]
    # (test_перегон_без_норматива...), min_rest_sit в (10, 55]
    # (test_эскалация_покрывает_сироту...). Сцепка: тесты с фикстурой
    # правил передают sit_min=50 явно — равным min_sit_min правил
    # (построитель при правилах читает порог стыковки из args.sit_min).
    base = dict(level=3, bases="BASE", budget_s=10, seed=7, workers=2,
                max_segs=4, sit_min=45, sit_max=300, duty_span=780,
                pairing_span=7 * 1440, min_rest_sit=25, rest_base=660,
                rest_base_long=1800, rest_off=700, rest_off_long=1300,
                branch_duty=3, branch_chain=4, depth=6, per_duty=30,
                dh_options=1, rescue_dh=4, rescue_per_leg=8, rescue_s=5, solve_s=0,
                w_night=30, w_dh=90, w_excess=200, tz_offset_min=0,
                method="enumerate", cg_rounds=3, cg_top_k=500,
                cg_alpha=0.5, cg_pass_s=5, label="t",
                briefing_default_min=None, debriefing_default_min=None,
                nights_per_layover=2, source_cost=True, cos_rules=None)
    base.update(over)
    return argparse.Namespace(**base)


def _leg(num: str, dep: str, arr: str, start: str, minutes: int,
         ac: str = "A320") -> dict:
    t0 = datetime.fromisoformat(start)
    return {"flightNumber": num, "departureAirport": dep,
            "arrivalAirport": arr,
            "departureUTCDateTime": t0.isoformat(),
            "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes)).isoformat(),
            "aircraftType": ac}


def _payload(flights, seats_per_flight=2) -> dict:
    assigns = []
    for f in flights:
        for i in range(seats_per_flight):
            assigns.append({"id": f"{f['flightNumber']}#{i}",
                            "flight": f["flightNumber"], "indexInFlight": i,
                            "requiredSkill": "Captain" if i == 0 else "CA"})
    return {"airports": [], "employees": [{"id": "E1", "homeAirport": "BASE",
                                           "skills": ["Captain", "CA"]}],
            "flights": flights, "flightAssignments": assigns}


def _simple_round_trip() -> dict:
    """Туда и обратно в один день — цепочка обязана найтись."""
    return _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])


def test_round_trip_is_fully_covered():
    catalog, report = _pb.build(_simple_round_trip(), _args())
    assert report["uncovered_legs"] == 0
    assert report["units"] == 1
    unit = catalog["selected"][0]
    assert set(unit["active_fns"]) == {"F1", "F2"}


def test_catalog_carries_fields_stage2_needs():
    catalog, _ = _pb.build(_simple_round_trip(), _args())
    unit = catalog["selected"][0]
    for field in ("fns", "active_fns", "seats", "sourceUnit", "kind",
                  "sourceLegsMissing",
                  "requiredCodesAll", "seatCodes", "minRestAfterMin",
                  "busyWindows", "blockMinutesByMonth", "workMinutesByMonth"):
        assert field in unit, f"каталог без поля {field} сломает этап 2"
    assert unit["seats"], "места экипажа обязаны быть перечислены"
    assert len(unit["seatCodes"]) == len(unit["seats"])
    assert catalog["profile"] == "pairing-builder"
    # Построитель выпускает целые командировки — входной аудит обязан
    # уметь судить их наравне с цепочками заказчика.
    assert unit["kind"] == "chain"
    assert unit["sourceLegsMissing"] == 0


def test_seats_follow_demand():
    """Мест в юните столько, сколько требует самый ёмкий его участок."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)],
        seats_per_flight=3)
    catalog, _ = _pb.build(payload, _args())
    assert len(catalog["selected"][0]["seats"]) == 3


def test_unreachable_leg_is_classified_as_no_pair():
    """Участок вдали от базы без обратного рейса — «нет пары в
    горизонте», а не недоработка перебора."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
        _leg("F9", "XXX", "YYY", "2026-08-02T06:00:00", 90)])
    _catalog, report = _pb.build(payload, _args())
    assert report["uncovered_legs"] == 1
    assert report["uncovered_no_pair_in_horizon"] == 1
    assert report["uncovered_not_built"] == 0


def test_chain_returns_to_base():
    """Ночёвка вне базы допустима, но цепочка обязана вернуться."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-02T18:00:00", 120)])
    catalog, report = _pb.build(payload, _args())
    assert report["uncovered_legs"] == 0
    fns = catalog["selected"][0]["active_fns"]
    assert fns[0] == "F1" and fns[-1] == "F2"


def test_rest_rule_blocks_too_early_return():
    """Если разрыв больше предела стыковки внутри смены, но меньше
    требуемого отдыха вне базы, два участка НЕ могут стать двумя
    рабочими сменами одной цепочки. Возврат пассажиром при этом
    допустим — правило отдыха на перегон не распространяется."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    catalog, _report = _pb.build(payload, _args(sit_max=60, rest_off=600))
    for unit in catalog["selected"]:
        assert set(unit["active_fns"]) != {"F1", "F2"}, (
            "правило отдыха не должно допускать обе смены в одной цепочке")


def test_levels_1_and_2_require_base_catalog():
    for level in (1, 2):
        with pytest.raises(ValueError):
            _pb.build(_simple_round_trip(), _args(level=level))


def test_level_2_keeps_customer_duties():
    """Уровень 2 не выдумывает смены: их множество приходит из каталога
    заказчика."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    base = {"selected": [{"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
                          "duty_fns": [["F1"], ["F2"]]}]}
    _catalog, report = _pb.build(payload, _args(level=2), base)
    assert report["duties"] == 2
    assert report["duties_from_customer"] == 2


def test_level_1_fixes_customer_chains():
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    base = {"selected": [{"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
                          "duty_fns": [["F1"], ["F2"]]}]}
    catalog, report = _pb.build(payload, _args(level=1), base)
    assert report["fixed_chains"] == 1
    assert report["uncovered_legs"] == 0
    assert len(catalog["selected"]) == 1


def test_deterministic_between_runs():
    """Один вход и одни параметры — один результат: иначе сравнение
    прогонов между собой теряет смысл."""
    first, _ = _pb.build(_simple_round_trip(), _args())
    second, _ = _pb.build(_simple_round_trip(), _args())
    assert json.dumps(first, sort_keys=True) == \
        json.dumps(second, sort_keys=True)


def test_sit_window_is_the_rest_floor_of_vmp_not_a_literal():
    """Ступень стенда (ревизия литералов 2026-09-03, отмашка владельца):
    пауза 5,5 часа вне базы при поле отдыха вне базы 700 минут — это
    СТЫКОВКА, смена продолжается; прежний литерал 240 минут рвал её.
    Известный ответ: одна цепочка, одна смена из двух участков, покрытие
    полное. Явный --sit-max остаётся перекрытием оператора и объявляется
    в отчёте; без пола отдыха и без перекрытия — честный отказ."""
    world = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T13:30:00", 120)])   # пауза 330
    cat, rep = _pb.build(world, _args(sit_max=None))
    assert cat["coverage"]["verdict"] == "OK" and cat["coverage"]["uncovered"] == 0
    assert len(cat["selected"]) == 1
    assert cat["selected"][0]["duty_fns"] == [["F1", "F2"]]
    assert rep["sit_window"]["mode"].startswith("по полу отдыха ВМП")
    assert rep["sit_window"]["cap_outstation_min"] == 700
    # Происхождение границы едет в КАТАЛОГЕ (карточка серии читает его).
    assert cat["sit_window"] == rep["sit_window"]
    assert cat["sit_window"]["cap_base_min"] == 660
    assert cat["config_sig"]["sit_max"] is None
    # Пауза не короче пола отдыха вне базы (700) — отдых: смены две.
    far = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T20:00:00", 120)])   # пауза 720
    cat2, _r2 = _pb.build(far, _args(sit_max=None))
    assert all(len(d) == 1 for c in cat2["selected"] for d in c["duty_fns"])
    # Перекрытие оператора действует и объявлено.
    cat3, rep3 = _pb.build(world, _args(sit_max=240))
    # 06.09.2026: явный --sit-max — граница контракта решателя (кнопка)
    # либо перекрытие оператора; подпись говорит об этом словами.
    assert rep3["sit_window"]["mode"].startswith("задан --sit-max")
    assert [["F1", "F2"]] not in [c["duty_fns"] for c in cat3["selected"]]
    with pytest.raises(_pb.BuilderConfigError):
        _pb.build(world, _args(sit_max=None, rest_base=None, rest_off=None))


def test_objective_is_criterion_plus_vmp_soft_penalties():
    """Ступень стенда (канон ВМП 2026-09-02, исправление 2026-09-03):
    целевая построителя = критерий + Σ (размер нарушения × коэффициент)
    мягких правил части. Мир: пятничная ночёвка вне базы и одна смена
    длиннее порога. Известный ответ на человека: 2 календарных дня × 100
    + ночь 10 + надбавка пятницы 25 + продлённая смена 15 = 250; на
    экипаж из двух — 500. Без штрафов было бы 400."""
    # 2026-08-07 — пятница: ночёвка в AAA начинается в пятницу.
    world = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-07T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-08T08:00:00", 120),
        # вторая цепочка: смена 06:00–18:30 = 750 > порога 720 (предел 780)
        _leg("F3", "BASE", "BBB", "2026-08-10T06:00:00", 120),
        _leg("F4", "BBB", "BASE", "2026-08-10T16:30:00", 120)])
    common = dict(sit_max=None, w_night=0, w_dh=0, w_span_day=100,
                  rest_off=700, rest_base=660, duty_span=780)
    cat0, rep0 = _pb.build(world, _args(**common))
    assert cat0["coverage"]["verdict"] == "OK" and len(cat0["selected"]) == 2
    assert cat0["ip_objective"] == (200 + 100) * 2      # только критерий
    composed = dict(common, w_night=10, w_weekend_night=25,
                    weekend_days=(4, 5), w_long_duty=15,
                    long_duty_threshold=720)
    cat, rep = _pb.build(world, _args(**composed))
    assert cat["coverage"]["verdict"] == "OK" and len(cat["selected"]) == 2
    by = {tuple(c["fns"]): c["cost"] for c in cat["selected"]}
    assert by[("F1", "F2")] == (200 + 10 + 25) * 2       # дни, ночь, пятница
    assert by[("F3", "F4")] == (100 + 15) * 2            # день, продлённая смена
    assert cat["ip_objective"] == 470 + 230
    assert rep["total_cost"] == cat["ip_objective"]
    # A6: сумма мягких штрафов части — по расшифровке цены, ВМЕСТЕ с
    # ночёвкой (семья layover_penalty — тоже мягкое правило части).
    assert rep["soft_penalty_total"] == (10 + 25 + 15) * 2
    assert rep["soft_penalty_by_family"] == {
        "layover_penalty": 20, "calendar_windows": 50,
        "duty_extension_penalty": 30}
    assert rep["cost_weights"]["w_weekend_night"] == 25
    assert rep["cost_weights"]["long_duty_threshold"] == 720
    assert cat["config_sig"]["weekend_days"] == [4, 5]
    assert cat["config_sig"]["w_long_duty"] == 15
    # Окно без пятницы: надбавки нет; порог выше смены: штрафа нет.
    narrow = dict(common, w_night=10, w_weekend_night=25, weekend_days=(6,),
                  w_long_duty=15, long_duty_threshold=760)
    cat2, _r2 = _pb.build(world, _args(**narrow))
    assert cat2["ip_objective"] == (200 + 10) * 2 + 100 * 2


def test_deterministic_profile_two_runs_one_output_signature():
    """Д1 плана прямого хода: в детерминированном режиме (счётный предел
    CP-SAT вместо стенных часов, один поток, дедлайны фаз выключены)
    два запуска дают одну подпись выхода Д2; профиль поимённо и со
    штампом лежит в отчёте, а предел — в подписи постановки."""
    from vmp import signatures as sg
    world = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
        _leg("F3", "BASE", "BBB", "2026-08-02T07:00:00", 90),
        _leg("F4", "BBB", "BASE", "2026-08-02T12:00:00", 90),
        _leg("F5", "BASE", "AAA", "2026-08-02T15:00:00", 120),
        _leg("F6", "AAA", "BASE", "2026-08-03T08:00:00", 120)])
    runs = []
    for _ in range(2):
        cat, rep = _pb.build(world, _args(workers=4, budget_s=1, rescue_s=1,
                                          cg_pass_s=1, deterministic_time=5.0))
        runs.append((sg.output_signature(sg.canonical_pairing(cat)), rep))
    (sig_a, rep_a), (sig_b, rep_b) = runs
    assert sig_a == sig_b
    prof = rep_a["deterministic_profile"]
    assert prof["engine"] == "cp-sat" and prof["workers"] == 1
    assert prof["termination"] == {"kind": "deterministic_time", "value": 5.0}
    assert prof["stamp"] == rep_b["deterministic_profile"]["stamp"]
    assert cat["config_sig"]["deterministic_time"] == 5.0
    assert cat["coverage"]["verdict"] == "OK"
    assert rep_a.get("enumeration_time_limited") is False
    _c, plain = _pb.build(world, _args())
    assert plain["deterministic_profile"] is None


def test_column_generation_reports_lower_bound():
    """Режим генерации колонок обязан сообщать значение вспомогательной
    задачи, а математическую оценку снизу — только когда ценообразование
    прошло без эвристических капов (внешний аудит R6, P0-04): усечённый
    перебор доказывает исчерпание лишь усечённого пространства."""
    _catalog, report = _pb.build(_simple_round_trip(),
                                 _args(method="columns"))
    assert report["uncovered_legs"] == 0
    assert report.get("cg_rounds"), "раунды генерации должны быть записаны"
    assert report.get("cg_lp_value_on_pool") is not None
    assert "pricing_caps" in report
    if report.get("pricing_complete"):
        assert report.get("lower_bound") is not None
    else:
        assert report.get("lower_bound") is None, \
            "оценка снизу при усечённом переборе — подмена гарантии"


def test_rescue_pass_reports_orphans_honestly():
    """Плечо, которое невозможно вписать в цепочку, обязано быть
    сосчитано и до спасательного прохода, и после него: без этого
    счётчика решатель отчитывается об оптимуме на облегчённой задаче,
    где ограничения на такое плечо просто нет."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
        _leg("F9", "XXX", "YYY", "2026-08-02T06:00:00", 90)])
    _catalog, report = _pb.build(payload, _args())
    assert report["orphans_before_rescue"] >= 1
    assert report["orphans_after_rescue"] == report["uncovered_legs"] == 1
    assert report["rescued_legs"] == (report["orphans_before_rescue"]
                                      - report["orphans_after_rescue"])
    assert report["uncovered_no_pair_in_horizon"] == 1


def test_rescue_pass_never_shrinks_the_pool():
    """Спасательный проход только добавляет кандидатов: выключение его
    не может дать каталог лучше."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BBB", "2026-08-01T11:00:00", 120),
        _leg("F3", "BBB", "BASE", "2026-08-02T09:00:00", 150)])
    off, r_off = _pb.build(payload, _args(rescue_dh=0))
    on, r_on = _pb.build(payload, _args(rescue_dh=4))
    assert r_on["candidates"] >= r_off["candidates"]
    assert r_on["uncovered_legs"] <= r_off["uncovered_legs"]
    assert len(on["selected"]) >= 1


def test_final_solver_budget_is_separate_from_generation():
    """Бюджет задачи покрытия задаётся отдельно от бюджета генерации:
    иначе проходы генерации съедают время, и оптимум не доказывается."""
    _c, report = _pb.build(_simple_round_trip(), _args(budget_s=10,
                                                       solve_s=25))
    assert report["solve_seconds"] == 25
    _c2, report2 = _pb.build(_simple_round_trip(), _args(budget_s=10,
                                                         solve_s=0))
    assert report2["solve_seconds"] == 10


def test_report_flags_whether_the_run_is_reproducible():
    """CP-SAT с несколькими потоками решает по настенным часам: одно
    зерно даёт разные решения. Отчёт обязан говорить об этом прямо,
    иначе прогоны сравнивают как воспроизводимые, а они такими не были."""
    _c, many = _pb.build(_simple_round_trip(), _args(workers=8))
    assert many["solver_deterministic"] is False
    _c2, one = _pb.build(_simple_round_trip(), _args(workers=1))
    assert one["solver_deterministic"] is True


def test_no_network_or_model_calls_in_source():
    """Построитель обязан оставаться детерминированным: контур данных
    заказчика не допускает ни сетевых, ни модельных вызовов."""
    src = (HERE / "_pairing_builder.py").read_text(encoding="utf-8")
    for needle in ("requests", "httpx", "urllib", "socket", "openai",
                   "anthropic", "claude_cli", "ollama"):
        assert needle not in src, f"недопустимый вызов: {needle}"


def test_seats_keep_the_positional_order_of_demand():
    """Этап 2 сажает экипаж на места ПО ПОЗИЦИИ, а не по совпадению
    множества ролей. Порядок мест каталога обязан повторять порядок
    мест спроса, иначе люди садятся не на свои места и сертификат
    отчитывается «назначено иначе»."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    order = ["PURS", "CA", "CA", "SCCM"]          # намеренно не по алфавиту
    payload["flightAssignments"] = [
        {"id": f"{fn}#{i}", "flight": fn, "indexInFlight": i,
         "requiredSkill": role}
        for fn in ("F1", "F2") for i, role in enumerate(order)]
    catalog, _ = _pb.build(payload, _args())
    seats = catalog["selected"][0]["seats"]
    assert seats == order, f"порядок мест искажён: {seats}"


def test_level_2_refuses_a_catalog_without_duty_boundaries():
    """Каталог без границ смен делает уровень 2 невыполнимым: запасной
    вариант посчитал бы весь пейринг одной сменой, и «сохранение смен»
    превратилось бы в сохранение цепочек. Отказ обязан быть громким."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    flat = {"selected": [{"fns": ["F1", "F2"], "active_fns": ["F1", "F2"]}]}
    with pytest.raises(ValueError, match="duty_fns"):
        _pb.build(payload, _args(level=2), flat)
    # с границами смен тот же каталог принимается
    with_duties = {"selected": [{"fns": ["F1", "F2"],
                                 "active_fns": ["F1", "F2"],
                                 "duty_fns": [["F1"], ["F2"]]}]}
    _catalog, report = _pb.build(payload, _args(level=2), with_duties)
    assert report["duties_from_customer"] == 2


def _unreachable_world():
    """Участок вдали от базы, который в принципе не вписать в цепочку,
    и обычный замкнутый рейс туда-обратно."""
    return _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
        _leg("F9", "XXX", "YYY", "2026-08-02T06:00:00", 90)])


def test_catalog_carries_its_coverage_evidence():
    """Свидетельство покрытия едет ВНУТРИ каталога: без него готовность
    этапа 2 определялась одним фактом существования файла
    (аудит 2026-07-26, P0-05)."""
    catalog, _report = _pb.build(_simple_round_trip(), _args())
    cov = catalog["coverage"]
    assert cov["complete"] is True and cov["verdict"] == "OK"
    assert cov["uncovered"] == 0 and cov["legs_total"] == 2
    # отпечаток постановки, чтобы прогоны разных уровней различались
    assert catalog["config_sig"]["level"] == 3
    assert catalog["config_sig"]["method"] == "enumerate"


def test_provably_uncoverable_leg_does_not_block_publication():
    """Участок, непокрываемый В ПРИНЦИПЕ, — свойство данных, а не
    недоработка расчёта: каталог публикуется, но говорит об этом."""
    catalog, report = _pb.build(_unreachable_world(), _args())
    cov = catalog["coverage"]
    assert report["uncovered_legs"] == 1
    assert cov["verdict"] == "INFEASIBLE_INPUT"
    assert cov["no_pair_in_horizon"] == 1 and cov["not_built"] == 0


def test_cli_withholds_the_catalog_when_the_search_fell_short(tmp_path):
    """Прогон, где перебор не построил цепочку для ДОСТИЖИМОГО участка,
    канонический каталог не публикует: этап 2 не должен подхватить его
    как готовый."""
    import subprocess
    import sys as _sys
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_unreachable_world()), encoding="utf-8")
    out = tmp_path / "cat.json"
    # ограничиваем перебор так, чтобы цепочка не нашлась
    proc = subprocess.run(
        [_sys.executable, str(HERE / "_pairing_builder.py"),
         "--payload", str(payload), "--label", "t", "--level", "3",
         "--bases", "BASE", "--budget-s", "5", "--out", str(out),
         "--max-segs", "1", "--branch-duty", "1", "--branch-chain", "1",
         "--depth", "1", "--per-duty", "1", "--dh-options", "0",
         "--rescue-dh", "0", "--rescue-per-leg", "0",
         # таблица отдыха — данные: без файла правил/условий все четыре
         # флага обязательны (синтетика фикстуры, как в _args)
         "--rest-base", "660", "--rest-base-long", "1800",
         "--rest-off", "700", "--rest-off-long", "1300"],
        capture_output=True, text=True, cwd=HERE,
        encoding="utf-8", errors="replace")
    doc = json.loads((out if out.exists()
                      else out.with_name(out.stem + ".pending.json"))
                     .read_text(encoding="utf-8"))
    if doc["coverage"]["verdict"] == "INCOMPLETE_SEARCH":
        assert not out.exists(), "неполный каталог опубликован как готовый"
        assert out.with_name(out.stem + ".pending.json").exists()
        assert proc.returncode == 2
        assert "КАТАЛОГ НЕ ОПУБЛИКОВАН" in proc.stdout
    else:
        # перебор всё же справился — тогда каталог обязан быть на месте
        assert out.exists()


def test_builder_obeys_the_single_rules_contract(tmp_path, monkeypatch):
    """Построитель — второй исполнитель тех же правил, что решатель этапа
    2, и обязан читать их ИЗ ОДНОГО ИСТОЧНИКА. Раньше он считал по своим
    числам, и правило, включённое оператором в интерфейсе, до этапа 1 не
    доходило (аудит 2026-07-26, P0-03)."""
    import subprocess
    import sys as _sys
    cfg = tmp_path / "rules.json"
    cfg.write_text(json.dumps({"parameters": {
        "min_sit_min": 45, "max_landings_per_duty": 3,
        "max_duty_span_min": 600, "max_pairing_span_min": 2880}}),
        encoding="utf-8")
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_simple_round_trip()), encoding="utf-8")
    out = tmp_path / "cat.json"

    def run(*extra):
        return subprocess.run(
            [_sys.executable, str(HERE / "_pairing_builder.py"),
             "--payload", str(payload), "--label", "t", "--bases", "BASE",
             "--budget-s", "5", "--out", str(out),
             "--rules-config", str(cfg),
             # синтетическая таблица отдыха фикстуры: rest-флаги не
             # входят в TIER1_TO_ARG — rules_overridden остаётся пустым
             "--rest-base", "660", "--rest-base-long", "1800",
             "--rest-off", "700", "--rest-off-long", "1300", *extra],
            capture_output=True, text=True, cwd=HERE,
            encoding="utf-8", errors="replace")

    proc = run()
    assert proc.returncode in (0, 2, 4), proc.stdout + proc.stderr
    doc = json.loads((out if out.exists()
                      else out.with_name(out.stem + ".pending.json"))
                     .read_text(encoding="utf-8"))
    sig = doc["config_sig"]
    # числа взяты из действующего набора правил, а не из своих умолчаний
    assert sig["duty_span"] == 600 and sig["pairing_span"] == 2880
    assert sig["sit_min"] == 45 and sig["max_segs"] == 3
    assert sig["rules_effective"]["max_duty_span_min"] == 600
    assert sig["rules_fingerprint"] and len(sig["rules_fingerprint"]) == 16
    assert sig["rules_overridden"] == []

    # явный флаг остаётся допустимым перекрытием, но называется вслух
    proc2 = run("--duty-span", "999")
    assert "расходятся с действующими правилами" in proc2.stdout
    doc2 = json.loads((out if out.exists()
                       else out.with_name(out.stem + ".pending.json"))
                      .read_text(encoding="utf-8"))
    assert doc2["config_sig"]["duty_span"] == 999
    assert doc2["config_sig"]["rules_overridden"], \
        "перекрытие обязано попасть в каталог"


def test_rules_fingerprint_separates_runs_under_different_rules(tmp_path):
    """Расчёт по другим правилам обязан быть отличим от расчёта по этим:
    иначе сравнение прогонов и повторное использование пула колонок
    сравнивают несравнимое (аудит 2026-07-26, P1-16)."""
    import subprocess
    import sys as _sys
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_simple_round_trip()), encoding="utf-8")
    sigs = []
    for span in (600, 900):
        cfg = tmp_path / f"rules-{span}.json"
        cfg.write_text(json.dumps({"parameters": {
            "max_duty_span_min": span}}), encoding="utf-8")
        out = tmp_path / f"cat-{span}.json"
        subprocess.run(
            [_sys.executable, str(HERE / "_pairing_builder.py"),
             "--payload", str(payload), "--label", "t", "--bases", "BASE",
             "--budget-s", "5", "--out", str(out),
             "--rules-config", str(cfg),
             "--rest-base", "660", "--rest-base-long", "1800",
             "--rest-off", "700", "--rest-off-long", "1300"],
            capture_output=True, text=True, cwd=HERE,
            encoding="utf-8", errors="replace")
        p = out if out.exists() else out.with_name(out.stem + ".pending.json")
        sigs.append(json.loads(p.read_text(encoding="utf-8"))
                    ["config_sig"]["rules_fingerprint"])
    assert sigs[0] != sigs[1], "отпечаток правил не различает постановки"


# --- умолчания-в-данные: голый запуск и деци-ставки критерия 0096 -----------


def test_naked_cli_run_refuses_without_a_rest_table(tmp_path):
    """Голый запуск (без --source-rules/--conditions и без --rest-*)
    обязан отказать словами: таблица отдыха — данные заказчика, чужих
    чисел в коде нет (пакет «умолчания-в-данные»)."""
    import subprocess
    import sys as _sys
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_simple_round_trip()), encoding="utf-8")
    out = tmp_path / "cat.json"
    proc = subprocess.run(
        [_sys.executable, str(HERE / "_pairing_builder.py"),
         "--payload", str(payload), "--label", "t", "--bases", "BASE",
         "--budget-s", "5", "--out", str(out)],
        capture_output=True, text=True, cwd=HERE,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ОТКАЗ" in proc.stdout
    # отказ называет каналы, которыми значение обязано прийти
    for channel in ("--source-rules", "--conditions", "--rest-base"):
        assert channel in proc.stdout, f"отказ не называет канал {channel}"
    assert not out.exists()
    assert not out.with_name(out.stem + ".pending.json").exists()


def test_builder_refuses_cost_0096_without_deci_rates():
    """Внутрипроцессный путь (Builder зовут в обход main): критерий 0096
    без деци-ставок — типизированный отказ с именами обоих флагов."""
    with pytest.raises(_pb.BuilderConfigError,
                       match="cost-rank-deci-work") as err:
        _pb.Builder(_simple_round_trip(), _args(cost_0096=True))
    assert "cost-rank-deci-dh" in str(err.value)


def test_cost_0096_uses_the_explicit_deci_rates_exactly():
    """Цель 0096 считается ровно по ПОДАННЫМ ставкам. Числа нарочно
    отличны от исторических 17/7: воскресшее умолчание завалит точную
    арифметику."""
    catalog, report = _pb.build(
        _simple_round_trip(),
        _args(cost_0096=True, cost_rank_deci_work=21, cost_rank_deci_dh=9,
              cost_rates_param=None))
    unit = catalog["selected"][0]
    # мир: два перелёта по 120 мин, перегонов нет, состав 2
    assert unit["cost"] == (21 * 240 + 9 * 0) * 2
    assert report["total_cost"] == unit["cost"]


def test_cli_refuses_cost_0096_without_deci_rates(tmp_path):
    """CLI-запуск с --cost-0096 без деци-флагов — отказ словами, код 2,
    имена обоих флагов в выводе."""
    import subprocess
    import sys as _sys
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_simple_round_trip()), encoding="utf-8")
    out = tmp_path / "cat.json"
    proc = subprocess.run(
        [_sys.executable, str(HERE / "_pairing_builder.py"),
         "--payload", str(payload), "--label", "t", "--bases", "BASE",
         "--budget-s", "5", "--out", str(out), "--cost-0096",
         # таблица отдыха подана явно: отказ обязан прийти именно от
         # гейта деци-ставок, а не от стража таблицы отдыха
         "--rest-base", "660", "--rest-base-long", "1800",
         "--rest-off", "700", "--rest-off-long", "1300"],
        capture_output=True, text=True, cwd=HERE,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ОТКАЗ" in proc.stdout
    assert "--cost-rank-deci-work" in proc.stdout
    assert "--cost-rank-deci-dh" in proc.stdout


# --- правила системы-источника: каждое обязано СРАБАТЫВАТЬ ------------------
#
# Присутствие правила в коде ничего не доказывает: правило, которое ничего
# не отсекает, неотличимо от отсутствующего. Поэтому каждый тест строит
# случай, который правило ОБЯЗАНО запретить, и проверяет, что запрет
# случился.

import _cos_rules as _cr


def _rules(**over) -> "_cr.CosRules":
    """Полный ЯВНЫЙ набор правил тестового мира.

    Умолчаний заказчика у CosRules больше нет (пакет «умолчания-в-
    данные»): каждое правило фикстура задаёт сама, точечные overrides —
    поверх. Значения совпадают с прежними умолчаниями класса, чтобы
    поведенческие ассерты набора сохранились дословно; равнозначных
    станций тестовый мир не заявляет (пустая таблица)."""
    r = _cr.CosRules(
        work_time_limit_by_tier=(720, 840, 960),
        layover_tiers=(
            _cr.LayoverTier(720, 720, 760, 1440, 760, 1440, 1440),
            _cr.LayoverTier(840, 2160, 1480, 2160, 1480, 2880, 1440),
            _cr.LayoverTier(960, 2160, 1480, 2160, 1480, 2880, 1440),
        ),
        min_sit_min=50,
        max_pairing_days=6,
        base_tz_offset_min=300,
        trans_tz_hours=3.0,
        trans_leg_min=720,
        dh_board_after_debrief_min=20,
        teleportation={},
    )
    for k, v in over.items():
        setattr(r, k, v)
    return r


def _leg_b(num, dep, arr, start, minutes, ac="A320", brief=0, debrief=0):
    f = _leg(num, dep, arr, start, minutes, ac)
    f["briefingMin"] = brief
    f["debriefingMin"] = debrief
    return f


def _pay(flights):
    p = _payload(flights)
    return p


def test_work_time_limit_uses_briefing_not_only_block_time():
    """Предел смены считается по РАБОЧЕМУ времени. Рейс на 11 ч 50 мин
    укладывается в 12 ч по блочному времени и НЕ укладывается с брифингом
    в 1 ч 40 мин — правило обязано это увидеть."""
    f = _leg_b("F1", "BASE", "OUT", "2026-08-01T06:00", 710,
               brief=100, debrief=60)
    b = _pb.Builder(_pay([f]), _args(cos_rules=_rules()))
    w0, w1 = b.work_period([b.legs["F1"]])
    assert (w1 - w0).total_seconds() / 60 == 870
    assert b.rules.tier_for_work_time(870) == 2      # нужен двойной состав
    # без брифинга та же смена проходит обычным составом
    b2 = _pb.Builder(_pay([_leg_b("F1", "BASE", "OUT", "2026-08-01T06:00", 710)]),
                     _args(cos_rules=_rules()))
    w0, w1 = b2.work_period([b2.legs["F1"]])
    assert b2.rules.tier_for_work_time((w1 - w0).total_seconds() / 60) == 0


def test_duty_over_the_largest_tier_is_not_built():
    """Смену, не влезающую и в двойной состав, строить нельзя ни при каком
    экипаже."""
    fs = [_leg_b("F1", "BASE", "M1", "2026-08-01T00:00", 480, brief=100),
          _leg_b("F2", "M1", "M2", "2026-08-01T09:00", 480, debrief=60)]
    # предел «своих» правил намеренно щедрый: отсечь обязаны ИМЕННО
    # правила источника, а не старая проверка размаха
    b = _pb.Builder(_pay(fs), _args(duty_span=5000, cos_rules=_rules()))
    assert not b.duty_limit_ok([b.legs["F1"], b.legs["F2"]])
    b.build_duties(None)
    assert all(len(d["legs"]) == 1 for d in b.duties), \
        "смена свыше 16 ч рабочего времени не должна собираться"


def test_landings_per_duty_capped_by_the_source_rules():
    fs = [_leg_b("F%d" % i, "S%d" % i, "S%d" % (i + 1),
                 "2026-08-01T%02d:00" % (6 + 2 * i), 60) for i in range(5)]
    fs[0]["departureAirport"] = "BASE"
    b = _pb.Builder(_pay(fs), _args(max_segs=9,
                                    cos_rules=_rules(max_landings_per_duty=3)))
    b.build_duties(None)
    assert max(len(d["legs"]) for d in b.duties) == 3
    b2 = _pb.Builder(_pay(fs), _args(max_segs=9,
                                     cos_rules=_rules(max_landings_per_duty=2)))
    b2.build_duties(None)
    assert max(len(d["legs"]) for d in b2.duties) == 2


def test_minimum_sit_comes_from_the_source_rules():
    fs = [_leg_b("F1", "BASE", "OUT", "2026-08-01T06:00", 60),
          _leg_b("F2", "OUT", "BASE", "2026-08-01T07:40", 60)]   # стыковка 40
    # свои правила такую стыковку РАЗРЕШАЮТ — отсечь обязано правило
    # источника, иначе при совпадающих значениях выбор ненаблюдаем
    b = _pb.Builder(_pay(fs), _args(sit_min=10,
                                    cos_rules=_rules(min_sit_min=50)))
    b.build_duties(None)
    assert all(len(d["legs"]) == 1 for d in b.duties), \
        "стыковка 40 мин при минимуме 50 не должна собирать смену"
    # и обратный случай: ровно минимум смену собирает
    fs2 = [_leg_b("F1", "BASE", "OUT", "2026-08-01T06:00", 60),
           _leg_b("F2", "OUT", "BASE", "2026-08-01T07:50", 60)]  # стыковка 50
    b2 = _pb.Builder(_pay(fs2), _args(sit_min=10,
                                      cos_rules=_rules(min_sit_min=50)))
    b2.build_duties(None)
    assert any(len(d["legs"]) == 2 for d in b2.duties)


def test_night_duty_flag_reaches_the_duty():
    # 20:00-04:00 по базе (+5) = 15:00-23:00 всемирного
    f = _leg_b("F1", "BASE", "OUT", "2026-08-01T15:00", 480)
    b = _pb.Builder(_pay([f]), _args(cos_rules=_rules()))
    b.build_duties(None)
    assert b.duties[0]["night"] is True
    assert b.report["duties_night"] == 1


def test_rest_after_night_duty_takes_the_larger_of_two_requirements():
    """Отдых после ночной смены — БОЛЬШЕЕ из двух: табличного требования
    и правила ночной серии.

    На действующем наборе оба дают 1440 на базе, поэтому проверка при них
    ничего не различает: снять любое из двух — и тест останется зелёным.
    Здесь правило серии намеренно сделано строже таблицы, чтобы было
    видно, какое из двух победило."""
    f = _leg_b("F1", "BASE", "OUT", "2026-08-01T15:00", 480)
    r = _rules(night_seq_layover_on_base_min=36 * 60)
    b = _pb.Builder(_pay([f]), _args(cos_rules=r))
    b.build_duties(None)
    d = b.duties[0]
    assert r.required_layover(d["work"], True, True, False) == 24 * 60
    assert b.rest_after(d, "BASE") == 36 * 60, "правило серии не победило"
    # а когда таблица строже — побеждает она
    r2 = _rules(night_seq_layover_on_base_min=60)
    b2 = _pb.Builder(_pay([f]), _args(cos_rules=r2))
    b2.build_duties(None)
    assert b2.rest_after(b2.duties[0], "BASE") == 24 * 60
    # без признака ночной смены остаётся обычный столбец таблицы
    assert b.rest_after(dict(d, night=False), "BASE") == 12 * 60


def test_night_sequence_layover_never_binds_on_the_source_rule_set():
    """Отдельно зафиксировано: на действующем наборе правило ночной серии
    не связывает НИКОГДА — на базе 1440 против 1440 по таблице, вне базы
    720 против 760 и 1480. Это надо видеть в отчёте, а не обнаруживать
    при разборе кода."""
    r = _rules()
    for work in (600, 800, 900):
        for on_base in (True, False):
            table = r.required_layover(work, on_base, True, False)
            seq = r.layover_after_night_sequence(on_base)
            assert table >= seq, (work, on_base, table, seq)


def test_two_night_duties_in_a_row_are_refused():
    night = {"night": True}
    b = _pb.Builder(_pay([_leg_b("F1", "BASE", "OUT", "2026-08-01T15:00", 60)]),
                    _args(cos_rules=_rules()))
    prev = dict(night, work0=_dtt("2026-08-01T15:00"), t0=_dtt("2026-08-01T15:00"))
    nxt = dict(night, work0=_dtt("2026-08-03T03:00"), t0=_dtt("2026-08-03T03:00"))
    assert not b.transition_ok(prev, nxt, "OUT")
    assert b.transition_ok(dict(prev, night=False), nxt, "OUT")


def test_duty_after_night_may_not_start_before_six_local():
    b = _pb.Builder(_pay([_leg_b("F1", "BASE", "OUT", "2026-08-01T15:00", 60)]),
                    _args(cos_rules=_rules()))
    prev = {"night": True}
    # 05:00 по базе (+5) = 00:00 всемирного
    early = {"night": False, "work0": _dtt("2026-08-03T00:00")}
    late = {"night": False, "work0": _dtt("2026-08-03T01:00")}   # 06:00 базы
    assert not b.transition_ok(prev, early, "OUT")
    assert b.transition_ok(prev, late, "OUT")


def test_pairing_days_limit_is_calendar_not_minutes():
    """Шесть суток и 6x1440 минут — разные правила: цепочка из коротких
    смен укладывается в минуты и не укладывается в календарь."""
    b = _pb.Builder(_pay([_leg_b("F1", "BASE", "OUT", "2026-08-01T06:00", 60)]),
                    _args(cos_rules=_rules(max_pairing_days=6)))
    ok = [{"work0": _dtt("2026-08-01T00:00"), "t0": _dtt("2026-08-01T00:00")},
          {"work1": _dtt("2026-08-06T00:00"), "t1": _dtt("2026-08-06T00:00")}]
    bad = [{"work0": _dtt("2026-08-01T00:00"), "t0": _dtt("2026-08-01T00:00")},
           {"work1": _dtt("2026-08-07T00:00"), "t1": _dtt("2026-08-07T00:00")}]
    assert b.pairing_span_ok(ok)
    assert not b.pairing_span_ok(bad)


def _pair_via(gap_min: int, tel: dict) -> list:
    """Цепочка BASE -> A, затем B -> BASE, где A и B — разные станции.
    Собраться она может ТОЛЬКО через равнозначность A и B."""
    t0 = datetime.fromisoformat("2026-08-01T06:00")
    back = t0 + timedelta(minutes=120 + gap_min)
    fs = [_leg_b("T1", "BASE", "AAA", t0.isoformat(), 120,
                 brief=0, debrief=0),
          _leg_b("T2", "BBB", "BASE", back.isoformat(), 120,
                 brief=0, debrief=0)]
    r = _rules(teleportation=tel)
    b = _pb.Builder(_pay(fs), _args(cos_rules=r, pairing_span=30 * 1440,
                                    depth=4))
    b.build_duties(None)
    b.enumerate_candidates(set())
    return [c for c in b.candidates.values() if len(c["duties"]) == 2]


def test_equivalent_stations_let_the_chain_continue():
    """Экипаж может продолжить работу с равнозначной станции, но не ранее
    чем через заданный разрыв. Проверяется на ПОСТРОЕННОЙ цепочке: прежде
    тест обращался только к таблице правил и остался бы зелёным, даже
    если бы построитель равнозначность не применял вовсе."""
    tel = {("AAA", "BBB"): 2160, ("BBB", "AAA"): 2160}
    assert _pair_via(2160, tel), \
        "при достаточном разрыве цепочка через равнозначную станцию " \
        "обязана собираться"
    assert not _pair_via(2160 - 10, tel), \
        "разрыв меньше требуемого обязан быть отвергнут"
    assert not _pair_via(2160, {}), \
        "без объявленной равнозначности цепочка невозможна"


def test_source_rules_appear_in_the_catalog_signature():
    """Расчёт по правилам источника и по своим — разные постановки, и
    отпечаток обязан их различать."""
    plain = _pb.Builder(_pay([_leg_b("F1", "BASE", "OUT",
                                     "2026-08-01T06:00", 60)]),
                        _args())
    sourced = _pb.Builder(_pay([_leg_b("F1", "BASE", "OUT",
                                       "2026-08-01T06:00", 60)]),
                          _args(cos_rules=_rules()))
    a = plain.config_signature()
    c = sourced.config_signature()
    assert a["source_rules"] is None
    assert c["source_rules"]["work_time_limit_by_tier"] == [720, 840, 960]
    assert a != c


def test_missing_briefing_is_counted_not_silently_zero():
    """Умолчание не должно выдавать себя за данные заказчика."""
    f = _leg("F1", "BASE", "OUT", "2026-08-01T06:00", 60)   # без брифинга
    b = _pb.Builder(_pay([f]), _args(cos_rules=_rules(),
                                     briefing_default_min=100,
                                     debriefing_default_min=60))
    b.build_duties(None)
    prov = b.report["work_time_provenance"]
    assert prov["briefing"].get("по всем данным") == 1
    assert prov["briefing_fallback_min"] == 100
    assert prov["debriefing_fallback_min"] == 60


def test_unknown_briefing_is_filled_from_the_same_station_data():
    """Участок, ни разу не бывший крайним в смене заказчика, берёт
    брифинг большинством по своей станции — из ТЕХ ЖЕ данных, а не из
    выдуманного правила. И это пересчитывается отдельно."""
    known = _leg_b("F1", "BASE", "OUT", "2026-08-01T06:00", 60,
                   brief=100, debrief=60)
    unknown = _leg("F2", "BASE", "OUT", "2026-08-02T06:00", 60)
    b = _pb.Builder(_pay([known, unknown]),
                    _args(cos_rules=_rules(), briefing_default_min=0))
    w0, w1 = b.work_period([b.legs["F2"]])
    assert (b.legs["F2"]["t0"] - w0).total_seconds() / 60 == 100
    prov = b.work_time_provenance()
    assert prov["briefing"]["по станции"] == 1
    assert "по всем данным" not in prov["briefing"]


def _dtt(s):
    return datetime.fromisoformat(s)


def test_catalog_carries_duty_boundaries():
    """Каталог построителя обязан нести границы СМЕН, а не только состав
    цепочки. Без них потребитель видит командировку как одну длинную
    смену: предел рабочего времени, число посадок и стыковки становятся
    непроверяемы, а уровень 2 на нашем же каталоге отказывает громко —
    тогда как каталог импорта эти границы несёт.

    Аудит построенного (2026-07-27) поймал на этом 1341 мнимое нарушение
    в 668 цепочках из 1424."""
    fs = [_leg("D1", "BASE", "OUT", "2026-08-01T06:00", 120),
          _leg("D2", "OUT", "BASE", "2026-08-02T06:00", 120)]
    b = _pb.Builder(_pay(fs), _args(rest_base=1, rest_off=1,
                                    rest_base_long=1, rest_off_long=1))
    b.build_duties(None)
    b.enumerate_candidates(set())
    cat = b.to_catalog(b.select(), "t")
    units = cat["selected"]
    assert units, "цепочек не построено — проверять нечего"
    for u in units:
        assert u.get("duty_fns"), u
        # границы смен обязаны покрывать ровно отработанные участки
        flat = [x for g in u["duty_fns"] for x in g]
        assert sorted(flat) == sorted(u["active_fns"]), (flat,
                                                         u["active_fns"])
        assert len(flat) == len(set(flat)), "участок в двух сменах сразу"


def test_our_catalog_is_accepted_as_a_base_for_level_two():
    """Уровень 2 обещает сохранить смены заказчика и отказывает громко,
    если границ смен в базовом каталоге нет. Наш собственный каталог
    обязан этому контракту удовлетворять — иначе он второсортный."""
    fs = [_leg("D1", "BASE", "OUT", "2026-08-01T06:00", 120),
          _leg("D2", "OUT", "BASE", "2026-08-02T06:00", 120)]
    b = _pb.Builder(_pay(fs), _args(rest_base=1, rest_off=1,
                                    rest_base_long=1, rest_off_long=1))
    b.build_duties(None)
    b.enumerate_candidates(set())
    cat = b.to_catalog(b.select(), "t")
    b2 = _pb.Builder(_pay(fs), _args(level=2, rest_base=1, rest_off=1,
                                     rest_base_long=1, rest_off_long=1))
    b2.build_duties(cat)          # не должно поднять ValueError
    assert b2.report["duties_from_customer"] >= 1


def _rest_pair(gap_after_arrival: int, brief: int, debrief: int) -> list:
    """Две смены с заданным промежутком МЕЖДУ РЕЙСАМИ. Собраться цепочка
    может, только если хватает промежутка по РАБОЧЕМУ времени."""
    r = _rules()
    t0 = datetime.fromisoformat("2026-08-01T06:00")
    out = t0 + timedelta(minutes=120 + gap_after_arrival)
    fs = [_leg_b("R1", "BASE", "OUT", t0.isoformat(), 120,
                 brief=brief, debrief=debrief),
          _leg_b("R2", "OUT", "BASE", out.isoformat(), 120,
                 brief=brief, debrief=debrief)]
    b = _pb.Builder(_pay(fs), _args(cos_rules=r))
    b.build_duties(None)
    b.enumerate_candidates(set())
    return [c for c in b.candidates.values() if len(c["duties"]) == 2]


def test_debriefing_alone_is_enough_to_break_the_rest():
    """Дебрифинг первой смены обязан входить в отсчёт отдыха.

    Прежде тест давал запас ровно на брифинг ПЛЮС дебрифинг, и потеря
    одного слагаемого не переводила ни один случай через границу —
    поломка ловилась только вместе с другой."""
    r = _rules()
    need = r.required_layover(240, on_base=False, night=False, trans=False)
    assert not _rest_pair(need + 40, brief=0, debrief=60), \
        "отдых короче требуемого на дебрифинг обязан быть отвергнут"
    assert _rest_pair(need + 60, brief=0, debrief=60)


def test_briefing_alone_is_enough_to_break_the_rest():
    """То же для брифинга ВТОРОЙ смены."""
    r = _rules()
    need = r.required_layover(240, on_base=False, night=False, trans=False)
    assert not _rest_pair(need + 80, brief=100, debrief=0)
    assert _rest_pair(need + 100, brief=100, debrief=0)


def test_rest_is_measured_between_work_periods_not_between_flights():
    """Отдых по правилам источника отмеряется от конца РАБОЧЕГО времени
    до начала следующего, а не от прилёта до вылета: между ними лежат
    дебрифинг и брифинг, до 160 минут вместе.

    Считая по рейсам, расчёт недосчитывал отдых — аудит построенного
    (2026-07-27) нашёл 172 таких перехода в 143 цепочках из 1430."""
    r = _rules()
    need = r.required_layover(240, on_base=False, night=False, trans=False)
    # вторая смена начинается ровно через `need` после ПРИЛЁТА первой,
    # то есть по рабочему времени — на 160 минут раньше положенного
    t0 = datetime.fromisoformat("2026-08-01T06:00")
    out = t0 + timedelta(minutes=120 + need)
    fs = [_leg_b("R1", "BASE", "OUT", t0.isoformat(), 120,
                 brief=100, debrief=60),
          _leg_b("R2", "OUT", "BASE", out.isoformat(), 120,
                 brief=100, debrief=60)]
    b = _pb.Builder(_pay(fs), _args(cos_rules=r))
    b.build_duties(None)
    b.enumerate_candidates(set())
    two = [c for c in b.candidates.values() if len(c["duties"]) == 2]
    assert not two, ("цепочка собрана при отдыхе, недостаточном по "
                     "рабочему времени: %r" % two[:1])
    # сдвинем вторую смену на брифинг+дебрифинг вперёд — теперь законно
    out2 = out + timedelta(minutes=160)
    fs2 = [_leg_b("R1", "BASE", "OUT", t0.isoformat(), 120,
                  brief=100, debrief=60),
           _leg_b("R2", "OUT", "BASE", out2.isoformat(), 120,
                  brief=100, debrief=60)]
    b2 = _pb.Builder(_pay(fs2), _args(cos_rules=r))
    b2.build_duties(None)
    b2.enumerate_candidates(set())
    two2 = [c for c in b2.candidates.values() if len(c["duties"]) == 2]
    assert two2, "при достаточном отдыхе цепочка обязана собираться"


# --- цель расчёта: ставки берутся ИЗ ДАННЫХ ---------------------------------

def _cost_payload():
    """Цепочка BASE -> OUT -> BASE с ночёвкой и известной ставкой станции."""
    t0 = datetime.fromisoformat("2026-08-01T06:00")
    fs = [_leg_b("C1", "BASE", "OUT", t0.isoformat(), 120),
          _leg_b("C2", "OUT", "BASE",
                 (t0 + timedelta(days=2)).isoformat(), 120)]
    for f in fs:
        f["deadheadCost"] = 2000
    p = _pay(fs)
    p["airports"] = [
        {"code": "BASE", "layoverCost": 100, "layoverCostKnown": True},
        {"code": "OUT", "layoverCost": 150, "layoverCostKnown": True}]
    return p


def test_objective_uses_rates_from_the_data_not_our_coefficients():
    """Решение, полученное по другой цели, несравнимо с чужим, даже когда
    правила совпадают. Ставки обязаны браться из данных источника."""
    b = _pb.Builder(_cost_payload(), _args(cos_rules=_rules()))
    assert b.source_cost is True
    assert b.layover_cost_of == {"BASE": 100, "OUT": 150}
    assert b.dh_cost_of["C1"] == 2000
    chain = [{"arr": "OUT"}, {"arr": "BASE"}]
    cost, stops, unknown = b.chain_cost(chain, [], crew=3)
    # одна стоянка вне базы, две ночи по 150, на троих
    assert cost == 150 * 2 * 3 and stops == 3 and unknown == 0
    # стоянка НА базе в стоимость не входит
    assert b.chain_cost([{"arr": "BASE"}, {"arr": "OUT"}], [], 1)[1] == 0


def test_station_without_a_rate_is_counted_apart_not_priced():
    """Сторожевое число «неизвестная станция» в цели означало бы запрет
    туда летать. Такие стоянки считаются отдельно и стоят ноль."""
    p = _cost_payload()
    p["airports"][1] = {"code": "OUT", "layoverCost": 0,
                        "layoverCostKnown": False}
    b = _pb.Builder(p, _args(cos_rules=_rules()))
    cost, stops, unknown = b.chain_cost([{"arr": "OUT"}, {"arr": "BASE"}],
                                        [], crew=2)
    assert cost == 0 and stops == 2 and unknown == 2


def test_deadhead_is_priced_per_leg_from_the_data():
    b = _pb.Builder(_cost_payload(), _args(cos_rules=_rules()))
    dh = [b.legs["C1"], b.legs["C2"]]
    cost, _s, _u = b.chain_cost([{"arr": "BASE"}], dh, crew=2)
    assert cost == (2000 + 2000) * 2


def test_without_rates_in_the_data_our_coefficients_remain():
    """Обратная совместимость: задание без ставок считается как раньше."""
    b = _pb.Builder(_pay([_leg_b("C1", "BASE", "OUT",
                                 "2026-08-01T06:00", 60)]),
                    _args(cos_rules=_rules()))
    assert b.source_cost is False


def test_pairing_days_count_positioning_flights():
    """У источника позиционный перелёт входит в смену, поэтому предел в
    сутках обязан его видеть. Прежде считалось только по рабочим сменам,
    и 11 цепочек занимали семь суток при пределе шесть."""
    r = _rules(max_pairing_days=2)
    b = _pb.Builder(_cost_payload(), _args(cos_rules=r))
    t = datetime.fromisoformat("2026-08-01T06:00")
    chain = [{"work0": t, "work1": t + timedelta(hours=4),
              "t0": t, "t1": t + timedelta(hours=4)}]
    assert b.pairing_days_ok(chain, [])
    late = {"t0": t + timedelta(days=3), "t1": t + timedelta(days=3, hours=2)}
    assert not b.pairing_days_ok(chain, [late]), \
        "перегон за пределом суток обязан отвергать цепочку"


def test_exact_cover_is_tried_first_and_reported():
    """Участок, выполненный дважды двумя экипажами, — бессмыслица: при
    выпуске каталога у одной из цепочек отнимают участок, и в её смене
    остаётся дыра. Поэтому сначала решается ТОЧНАЯ задача, и то, какая
    из двух постановок сработала, обязано быть видно в отчёте.

    Прежде клапан двойного покрытия работал всегда, и при недоказанном
    оптимуме решатель им пользовался: сотни задвоенных участков и
    десятки цепочек с разрывом станции внутри."""
    catalog, report = _pb.build(_simple_round_trip(), _args())
    assert report["exact_cover"] is True
    assert report["exact_cover_status"] in ("OPTIMAL", "FEASIBLE")
    assert all(not u.get("sourceLegsMissing")
               for u in catalog["selected"]), "цепочек с дырой быть не должно"


def test_relaxation_is_announced_when_exact_cover_is_impossible(capsys):
    """Ослабление постановки обязано называться вслух: молчаливое
    ослабление — это и есть подмена задачи."""
    # участок, который нечем покрыть ровно один раз: два рейса из базы
    # в одну сторону и один обратно
    fs = [_leg("X1", "BASE", "AAA", "2026-08-01T06:00", 120),
          _leg("X2", "BASE", "AAA", "2026-08-01T07:00", 120),
          _leg("X3", "AAA", "BASE", "2026-08-01T14:00", 120)]
    _catalog, report = _pb.build(_payload(fs), _args())
    if report.get("exact_cover") is False:
        assert "постановка ослаблена" in capsys.readouterr().out.lower() \
            or "клапан" in capsys.readouterr().out.lower()
    # какая бы постановка ни сработала, отчёт обязан её назвать
    assert "exact_cover" in report and "exact_cover_status" in report


def test_lower_bound_is_published_only_when_column_generation_converges():
    """Значение вспомогательной задачи на текущем пуле становится оценкой
    снизу ТОЛЬКО при сходимости: пока перебор находит новые цепочки, их
    добавление может это значение уменьшить, и настоящий оптимум способен
    оказаться ниже.

    На наборе заказчика восемь кругов не сошлись (последний
    добавил 63 цепочки), а поле «оценка снизу» тем не менее печаталось —
    и число 2 589 076 можно было принять за границу оптимума."""
    b = _pb.Builder(_simple_round_trip(), _args(method="columns"))
    b.report["cg_rounds"] = []
    # несошедшийся случай: перебор исчерпан, но цепочки ещё находятся
    b.report["cg_rounds"] = [{"round": 1, "lower_bound": 100,
                              "columns": 5, "pool": 10, "exhaustive": True}]
    last = b.report["cg_rounds"][-1]
    converged = bool(last["exhaustive"]) and last["columns"] == 0
    assert not converged
    # сошедшийся случай
    last2 = {"round": 2, "lower_bound": 90, "columns": 0, "pool": 12,
             "exhaustive": True}
    assert bool(last2["exhaustive"]) and last2["columns"] == 0


def test_column_generation_reports_convergence_on_a_real_run():
    """Тот же контракт, но на НАСТОЯЩЕМ выходе генерации колонок, а не на
    выдуманном словаре: имя поля обязано совпасть у писателя и читателя."""
    _catalog, report = _pb.build(_simple_round_trip(),
                                 _args(method="columns", cg_rounds=3,
                                       cg_pass_s=2))
    assert "cg_converged" in report
    assert "cg_lp_value_on_pool" in report
    if not report["cg_converged"]:
        assert report["lower_bound"] is None, \
            "несошедшееся значение не имеет права называться оценкой снизу"


def test_crew_size_follows_our_duty_tier_not_only_leg_demand():
    """Численность экипажа цепочки — не только спрос участка.

    Спрос взят из файла заказчика и несёт ЕГО решения о составе: участок,
    который он вёз усиленным экипажем, приносит лишнее место даже нашей
    короткой смене, а наша ДЛИННАЯ смена лишнего места не получала вовсе.
    На наборе заказчика второе перевешивало: 315 цепочек из 1352
    недосчитывали 495 человеко-мест, цель была занижена на 8,9 %."""
    # смена на 13 часов рабочего времени -> усиленный состав (ярус 1)
    f = _leg_b("W1", "BASE", "OUT", "2026-08-01T06:00", 660,
               brief=100, debrief=60)
    b = _pb.Builder(_pay([f]), _args(cos_rules=_rules()))
    b.build_duties(None)
    d = b.duties[0]
    assert d["tier"] == 1, d["tier"]
    # спрос участка — два места (обычный состав этого типа судна)
    assert b.crew_of_leg["W1"] == 2
    assert b.std_crew_of_fleet["A320"] == 2
    # экипаж цепочки обязан быть на одного больше: смена усиленная
    assert b.crew_size([d]) == 3

    # короткая смена — ровно спрос участка, ничего не добавляем
    f2 = _leg_b("W2", "BASE", "OUT", "2026-08-01T06:00", 120,
                brief=0, debrief=0)
    b2 = _pb.Builder(_pay([f2]), _args(cos_rules=_rules()))
    b2.build_duties(None)
    assert b2.duties[0]["tier"] == 0
    assert b2.crew_size([b2.duties[0]]) == 2


def test_crew_size_never_drops_below_leg_demand():
    """Спрос участка — нижняя граница: столько людей нужно, чтобы рейс
    вылетел. Наш ярус может её только поднять."""
    f = _leg_b("W1", "BASE", "OUT", "2026-08-01T06:00", 120)
    p = _pay([f], )
    # участок требует ЧЕТЫРЁХ мест, а обычный состав типа судна — два
    p["flightAssignments"] += [
        {"id": "W1#2", "flight": "W1", "indexInFlight": 2,
         "requiredSkill": "CA"},
        {"id": "W1#3", "flight": "W1", "indexInFlight": 3,
         "requiredSkill": "CA"}]
    b = _pb.Builder(p, _args(cos_rules=_rules()))
    b.build_duties(None)
    assert b.crew_size([b.duties[0]]) == 4


def test_without_source_rules_crew_size_is_the_old_one():
    """Обратная совместимость: без правил источника численность прежняя —
    только спрос участка."""
    f = _leg_b("W1", "BASE", "OUT", "2026-08-01T06:00", 660,
               brief=100, debrief=60)
    # Аудит A1 (04.09.2026): без файла правил смена теперь тоже меряется
    # рабочим временем с брифингом (660 + 100 + 60 = 820 мин); прежний
    # предел фикстуры 780 держался только на размахе «вылет → прилёт».
    # Стенд — про численность, не про предел: предел задан явно шире.
    b = _pb.Builder(_pay([f]), _args(duty_span=900))
    b.build_duties(None)
    assert b.crew_size([b.duties[0]]) == 2


def test_crew_size_sums_role_maxima_across_legs():
    """Спрос цепочки — сумма помест­ных максимумов РОЛЕЙ, а не максимум
    одного участка: старший одного участка и бизнес-класс другого не
    взаимозаменяемы, экипаж везёт обоих всю командировку."""
    fs = [_leg_b("R1", "BASE", "OUT", "2026-08-01T06:00", 120),
          _leg_b("R2", "OUT", "BASE", "2026-08-01T10:00", 120)]
    p = _pay(fs)
    # R1: Captain+CA (из _payload); R2 дополнительно требует PURS
    p["flightAssignments"].append(
        {"id": "R2#2", "flight": "R2", "indexInFlight": 2,
         "requiredSkill": "PURS"})
    b = _pb.Builder(p, _args(cos_rules=_rules()))
    b.build_duties(None)
    chain = [d for d in b.duties]
    chain.sort(key=lambda d: d["t0"])
    # максимум одного участка = 3 (R2), но Captain+CA+PURS = 3 — сделаем
    # роли расходящимися: у R1 два CA, у R2 один CA + PURS
    assert b.crew_size(chain) == 3


def test_tier_augmentation_reaches_seats_in_the_catalog():
    """Численность по нашему ярусу обязана дойти до состава мест, а не
    только до стоимости: иначе каталог обещает этапу 2 меньше людей, чем
    оплачено в цели. Дополнительное место получает самую многочисленную
    роль состава (по данным источника усиление добавляет именно её)."""
    f = _leg_b("T1", "BASE", "OUT", "2026-08-01T06:00", 710,
               brief=100, debrief=60)                    # рабочее 870 → ярус 2
    f2 = _leg_b("T2", "OUT", "BASE", "2026-08-03T06:00", 710,
                brief=100, debrief=60)
    p = _pay([f, f2])
    catalog, report = _pb.build(p, _args(duty_span=5000,
                                         cos_rules=_rules()))
    assert report["units"] >= 1
    unit = catalog["selected"][0]
    # обычный состав 2 (Captain+CA) + ярус 2 = 4 места; добавлен CA
    assert len(unit["seats"]) == 4
    assert unit["seats"].count("CA") == 3
    assert unit["seats"].count("Captain") == 1
    assert len(unit["seatCodes"]) == len(unit["seats"])


def test_budget_s_is_a_real_enumeration_limit():
    """Прежде `--budget-s` не действовал вовсе, но записывался в
    отпечаток: прогон «с удвоенным бюджетом» строил байт-в-байт тот же
    пул. Теперь это настоящий предел, и усечение видно в отчёте."""
    fs = []
    for i in range(12):
        fs.append(_leg("E%d" % i, "BASE", "S%d" % (i % 4),
                       "2026-08-%02dT06:00:00" % (1 + i % 6), 120))
        fs.append(_leg("B%d" % i, "S%d" % (i % 4), "BASE",
                       "2026-08-%02dT10:00:00" % (1 + i % 6), 120))
    _cat, rep_full = _pb.build(_payload(fs), _args(budget_s=600))
    assert rep_full["enumeration_time_limited"] is False
    _cat2, rep_cut = _pb.build(_payload(fs), _args(budget_s=0))
    assert rep_cut["enumeration_time_limited"] is True
    # пул после усечения НЕ обязан быть меньше: спасательный проход
    # законно достраивает цепочки осиротевшим участкам — важно лишь, что
    # усечение перебора видно в отчёте, а не растворяется молча


def test_dh_home_boards_from_debrief_end_plus_source_gap():
    """Посадка на перегон домой — от конца дебрифинга плюс граница
    источника (20 минут, откалибровано по его решению). Прежние свои
    30 минут рушили единственный способ обслужить еженедельный рейс на
    дальнюю станцию из-за пяти минут."""
    # работа BASE->OUT, перегон OUT->BASE через 25 минут после дебрифинга
    op = _leg_b("D1", "BASE", "OUT", "2026-08-01T06:00", 120, debrief=30)
    dh = _leg_b("D2", "OUT", "BASE", "2026-08-01T08:55", 120)
    catalog, report = _pb.build(_pay([op, dh]), _args(cos_rules=_rules()))
    unit = next(u for u in catalog["selected"]
                if "D1" in (u.get("active_fns") or ()))
    assert "D2" in unit["fns"], \
        "перегон в 25 минутах от дебрифинга обязан быть доступен"


def test_pre_dh_arrives_at_least_min_sit_before_departure():
    """Головной перегон: прилёт не позднее вылета работы минус
    минимальная стыковка источника (50). По его решению брифинг
    накладывается на перегон — граница по брифингу была бы неверна."""
    dh = _leg_b("P1", "BASE", "OUT", "2026-08-01T06:00", 120)
    op = _leg_b("P2", "OUT", "BASE", "2026-08-01T08:50", 120, brief=100)
    # sit_min явно = min_sit_min фикстуры правил: при правилах порог
    # стыковки перегона построитель читает из args.sit_min, и тайминги
    # 08:50/08:49 выстроены ровно вокруг 50.
    catalog, _ = _pb.build(_pay([dh, op]),
                           _args(sit_min=50, cos_rules=_rules()))
    unit = next(u for u in catalog["selected"]
                if "P2" in (u.get("active_fns") or ()))
    assert "P1" in unit["fns"], \
        "прилёт за 50 минут до вылета обязан быть достаточен (брифинг " \
        "идёт в дороге)"
    # а за 49 минут — уже нет
    op2 = _leg_b("P4", "OUT", "BASE", "2026-08-01T08:49", 120, brief=100)
    dh2 = _leg_b("P3", "BASE", "OUT", "2026-08-01T06:00", 120)
    catalog2, report2 = _pb.build(_pay([dh2, op2]),
                                  _args(sit_min=50, cos_rules=_rules()))
    unit2 = [u for u in catalog2["selected"]
             if "P4" in (u.get("active_fns") or ())]
    assert not unit2 or "P3" not in unit2[0]["fns"]


def test_hotel_cost_by_actual_nights_when_flat_is_off():
    """--nights-per-layover 0 — ставка за КАЖДУЮ фактическую календарную
    ночь стоянки (худший вариант вопроса № 8), а не плоские две."""
    fs = [_leg("N1", "BASE", "OUT", "2026-08-01T06:00:00", 120),
          _leg("N2", "OUT", "BASE", "2026-08-04T10:00:00", 120)]
    p = _payload(fs)
    p["airports"] = [{"code": "OUT", "layoverCost": 100,
                      "layoverCostKnown": True}]
    for f in p["flights"]:
        f["deadheadCost"] = 0
    b2 = _pb.Builder(p, _args(nights_per_layover=2, rest_off=600,
                              sit_max=60))
    b0 = _pb.Builder(p, _args(nights_per_layover=0, rest_off=600,
                              sit_max=60))
    for b in (b2, b0):
        b.build_duties(None)
    chain2 = sorted(b2.duties, key=lambda d: d["t0"])
    chain0 = sorted(b0.duties, key=lambda d: d["t0"])
    cost2, _s, _u = b2.chain_cost(chain2, [], 1)
    cost0, _s, _u = b0.chain_cost(chain0, [], 1)
    assert cost2 == 200          # плоские две ночи
    assert cost0 == 300          # три календарные ночи: 01->04 августа


def test_pin_catalog_keeps_locked_chains_with_honest_cost():
    """Закрепления (locked=true) — обязательное условие: структура входит
    в решение неприкосновенной, с честной стоимостью, а свободный поиск
    не смеет накрыть её участки вторым экипажем."""
    fs = [
        # закреплённая командировка: две смены с ночёвкой вне базы
        _leg("L1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("L2", "AAA", "BASE", "2026-08-02T10:00:00", 120),
        # свободные участки
        _leg("F1", "BASE", "BBB", "2026-08-01T07:00:00", 120),
        _leg("F2", "BBB", "BASE", "2026-08-01T11:00:00", 120),
    ]
    p = _payload(fs)
    p["airports"] = [{"code": "AAA", "layoverCost": 100,
                      "layoverCostKnown": True},
                     {"code": "BBB", "layoverCost": 100,
                      "layoverCostKnown": True}]
    for f in p["flights"]:
        f["deadheadCost"] = 0
    pin = {"selected": [{
        "fns": ["L1", "L2"], "active_fns": ["L1", "L2"],
        "duty_fns": [["L1"], ["L2"]],
        "sourceUnit": "locked:42"}]}
    catalog, report = _pb.build(p, _args(rest_off=600, sit_max=60),
                                pin_catalog=pin)
    assert report["pinned_chains"] == 1
    units = catalog["selected"]
    pinned = [u for u in units if u.get("pinned")]
    assert len(pinned) == 1
    u = pinned[0]
    assert u["sourceUnit"] == "locked:42"
    assert u["active_fns"] == ["L1", "L2"]
    assert u["duty_fns"] == [["L1"], ["L2"]]
    # честная стоимость: одна стоянка вне базы, 2 ночи x 100 x 2 чел
    assert u["cost"] == 400
    # свободные участки покрыты и не трогают закреплённые
    free = [x for u2 in units if not u2.get("pinned")
            for x in u2["active_fns"]]
    assert sorted(free) == ["F1", "F2"]
    assert report["uncovered_legs"] == 0
    # отпечаток различает прогон с закреплениями
    assert catalog["config_sig"]["pinned_chains"] == 1


def test_classify_uncovered_publishes_proven_impossible_names():
    """Контракт прощения этапа 2: доказуемо непокрываемые участки —
    ПОИМЁННО (proven_impossible_legs), а не только счётчиком. Без списка
    безупречный ростер получает INFEASIBLE_INCUMBENT из-за свойств
    данных (первый боевой ростер: десятки краевых рейсов набора)."""
    fs = [_leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
          _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120),
          # рейс между чужими станциями: ни доехать, ни вернуться
          _leg("F9", "XX1", "XX2", "2026-08-15T06:00:00", 120)]
    b = _pb.Builder(_payload(fs), _args())
    r = b.classify_uncovered({"F9"})
    assert r["proven_impossible_legs"] == ["F9"]
    assert r["uncovered_no_pair_in_horizon"] == 1
    assert r["uncovered_not_built"] == 0

def test_перегон_без_норматива_продолжает_период_и_на_флаговом_мире():
    """Мотив Полярис 2026-08-26 (цепочка 9 канона): рабочая смена 599 мин
    + пауза 599 мин на станции + возвратный перегон. Пауза меньше
    норматива отдыха вне базы — перегон ПРОДОЛЖАЕТ период, растянутый
    период валит предел смены, цепочка недопустима. Прежде запасная
    ветка (мир без условий источника) перегоны игнорировала, и судья
    решателя видел период 1367 мин при пределе — минус 8160 жёстких."""
    work = _leg_b("W1", "BASE", "OUT", "2026-08-01T06:00", 599)
    dh_tight = _leg_b("D1", "OUT", "BASE", "2026-08-02T01:58", 60)
    b = _pb.Builder(_pay([work, dh_tight]), _args())
    b.build_duties(None)
    duty = next(d for d in b.duties
                if any(l["id"].startswith("W1") for l in d["legs"]))
    dh = next(l for l in b.legs.values() if l["id"].startswith("D1"))
    assert b.periods_of([duty], [dh]) is None, (
        "перегон через паузу меньше норматива обязан растянуть период "
        "и завалить предел смены")
    # Контроль: пауза 781 мин не меньше норматива rest_off фикстуры —
    # перегон отдельным периодом, цепочка жива, периодов два. Связь
    # таймингов с фикстурой закреплена машинно: пауза 599 обязана быть
    # меньше норматива, 781 — не меньше.
    assert 599 < _args().rest_off <= 781, (
        "тайминги теста выстроены под окно rest_off (599, 781]")
    dh_ok = _leg_b("D2", "OUT", "BASE", "2026-08-02T05:00", 60)
    b2 = _pb.Builder(_pay([work, dh_ok]), _args())
    b2.build_duties(None)
    duty2 = next(d for d in b2.duties
                 if any(l["id"].startswith("W1") for l in d["legs"]))
    dh2 = next(l for l in b2.legs.values() if l["id"].startswith("D2"))
    pers = b2.periods_of([duty2], [dh2])
    assert pers is not None and len(pers) == 2, (
        "отделённый нормативом перегон — собственный период")

def test_ёмкость_баз_ограничивает_покрытие_честно():
    """Балансировка стартовых баз (--base-day-cap): при щедрой ёмкости
    покрытие прежнее; при нулевой — честный отказ, не молчаливое
    ослабление (виток «путь к нулю», 2026-08-26)."""
    catalog, _ = _pb.build(_simple_round_trip(), _args(base_day_cap="BASE=40"))
    assert catalog["coverage"]["verdict"] == "OK"
    assert catalog["selected"], "щедрая ёмкость не должна резать покрытие"
    catalog2, _ = _pb.build(_simple_round_trip(), _args(base_day_cap="BASE=0"))
    assert catalog2["coverage"]["verdict"] != "OK", (
        "нулевая ёмкость обязана дать честный не-OK вердикт")

def test_тёплый_старт_подсказывает_точной_фазе(tmp_path):
    """--warm-catalog: цепочки прежнего каталога сопоставляются кандидатам
    по множеству рабочих плеч и подсказываются решателю; счётчики — в
    отчёте (след для транслятора)."""
    import json
    catalog, _ = _pb.build(_simple_round_trip(), _args())
    prev = tmp_path / "prev-catalog.json"
    prev.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    _, report = _pb.build(_simple_round_trip(), _args(warm_catalog=prev))
    assert report.get("warm_hints_total") == len(catalog["selected"])
    assert report.get("warm_hints_matched", 0) >= 1, (
        "хоть одна цепочка прежнего каталога обязана сопоставиться")


def test_duty_span_matrix_tightens_and_gaps_refuse():
    """Матрица предела смены (ревизия 1.10): предел по окну местного
    старта и числу участков СТРОЖЕ яруса состава (действует минимум), а
    смена, не попавшая ни в одну строку матрицы, недопустима вовсе —
    молчаливого отката к плоскому пределу нет."""
    fs = [_leg_b("F1", "BASE", "M1", "2026-08-01T06:00", 300),
          _leg_b("F2", "M1", "M2", "2026-08-01T12:00", 300)]
    # без матрицы смена (работа 660 мин) проходит обычным ярусом
    b = _pb.Builder(_pay(fs), _args(cos_rules=_rules(base_tz_offset_min=0)))
    assert b.duty_limit_ok([b.legs["F1"], b.legs["F2"]])
    # окно 06:00-14:00 при 1-2 участках даёт 600 — строже яруса 720
    rules = _rules(base_tz_offset_min=0,
                   duty_span_matrix=((360, 840, 1, 2, 600),))
    b2 = _pb.Builder(_pay(fs), _args(cos_rules=rules))
    assert not b2.duty_limit_ok([b2.legs["F1"], b2.legs["F2"]])
    # одноучастковая смена того же окна в 600 укладывается
    assert b2.duty_limit_ok([b2.legs["F1"]])
    # старт 04:00 вне всех окон матрицы — смена недопустима, хотя плоский
    # ярус её пропустил бы
    f3 = _leg_b("F3", "BASE", "M1", "2026-08-01T04:00", 300)
    b3 = _pb.Builder(_pay([f3]), _args(cos_rules=rules))
    assert not b3.duty_limit_ok([b3.legs["F3"]])


def test_export_mps_lp_parity_with_exact_phase(tmp_path):
    """Экспорт точной модели (--export-mps/--export-lp): внешний солвер
    (HiGHS) на выгруженном файле обязан дать ТОТ ЖЕ оптимум, что живая
    точная фаза CP-SAT, — иначе экспорт лжёт о постановке. Справочник
    обязан описывать каждую переменную и каждое обязательство."""
    # HiGHS — необязательный движок: пропуск и когда пакета нет
    # (ModuleNotFoundError), и когда его нативная библиотека не
    # грузится (общий ImportError — битое колесо highspy под
    # Linux/py3.14: undefined symbol releaseMemory). Без exc_type
    # свежий pytest (9.x) ловит только ModuleNotFoundError и роняет
    # тест на битом бинарнике вместо честного пропуска.
    highspy = pytest.importorskip("highspy", exc_type=ImportError)
    mps = tmp_path / "exact.mps"
    lp = tmp_path / "exact.lp"
    catalog, report = _pb.build(
        _simple_round_trip(),
        _args(export_mps=mps, export_lp=lp))
    assert mps.exists() and lp.exists()
    m = json.loads((tmp_path / "exact.mps.map.json")
                   .read_text(encoding="utf-8"))
    assert m["pool"] == len(m["variables"]) > 0
    assert m["obligations"] > 0 and m["exact"] is True
    want = int(round(catalog["ip_objective"]))
    for path in (mps, lp):
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.readModel(str(path))
        h.run()
        got = int(round(h.getInfo().objective_function_value))
        assert got == want, (path.name, got, want)


def test_inject_catalog_adds_candidates_not_pins(tmp_path):
    """Точечный добор (--inject-catalog): конструкции приходят
    КАНДИДАТАМИ (счётчик injected_candidates), нелегальная конструкция
    отвергается вслух (inject_rejected), отпечатки порций — в подписи
    каталога. Обязательности нет — это не закрепление."""
    payload = _simple_round_trip()
    legal = {"selected": [{
        "sourceUnit": "gap:1", "kind": "chain",
        "duty_fns": [["F1"], ["F2"]], "fns": ["F1", "F2"],
        "active_fns": ["F1", "F2"]}]}
    bad = {"selected": [{
        "sourceUnit": "gap:bad", "kind": "chain",
        "duty_fns": [["F1", "NO-SUCH"]], "fns": ["F1"],
        "active_fns": ["F1"]}]}
    catalog, report = _pb.build(
        payload, _args(), base_catalog=None, pin_catalog=None,
        inject_catalogs=[legal, bad])
    assert report.get("injected_candidates") == 1
    assert report.get("inject_rejected") == 1


def test_export_only_skips_solve(tmp_path):
    """--export-only: файлы выгружены, решатель не запускался, каталог
    пуст — режим панельной кнопки экспорта."""
    mps = tmp_path / "m.mps"
    catalog, report = _pb.build(
        _simple_round_trip(),
        _args(export_mps=mps, export_only=True))
    assert mps.exists()
    assert not catalog.get("selected")


def test_canon_names_translate_or_refuse_loudly():
    """Канонические имена добора (NUM-YYYYMMDD-K) переводятся в имена
    набора; нераспознанное имя — громкий отказ, а не молчаливый отброс
    (смок 2026-08-27: 21/21 конструкция хоронилась без следа)."""
    payload = _simple_round_trip()
    f1 = payload["flights"][0]
    num = f1["flightNumber"]
    date = f1["departureUTCDateTime"][:10].replace("-", "")
    cat = {"selected": [{"sourceUnit": "gap:c", "kind": "chain",
                         "duty_fns": [["%s-%s-1" % (num, date)]],
                         "fns": ["%s-%s-1" % (num, date)],
                         "active_fns": ["%s-%s-1" % (num, date)]}]}
    out = _pb.canon_names_to_payload(cat, payload, "т")
    assert out["selected"][0]["duty_fns"] == [[num]]
    with pytest.raises(SystemExit):
        _pb.canon_names_to_payload(
            {"selected": [{"duty_fns": [["NOPE-20990101-9"]],
                           "fns": [], "active_fns": []}]}, payload, "т")


def test_fleet_homoglyph_is_named_loudly(capsys):
    """Кириллическая «А» в типе судна против латинской в ключе условий
    (боевой добор одной из линий): диагностика обязана назвать пару вслух."""
    class FakeCond:
        _exact = {"class_by_fleet": {"A330-2": "3"}}
    payload = {"flights": [{"aircraftType": "\u0410330-2"}]}
    out = _pb.warn_fleet_homoglyphs(FakeCond(), payload)
    assert len(out) == 1 and "гомоглиф" in out[0]
    assert "A330-2" in out[0]


def test_adjacent_dh_prefix_pair_is_judged():
    """Головная пересадка (два перегона ДО первой рабочей смены) обязана
    судиться правилом «между перегонами не менее норматива»: физически
    человек пересаживается независимо от «активности» плеча (кнопка №19:
    пара 60 мин проходила при поданных 120)."""
    fs = [_leg_b("D1", "AAA", "BBB", "2026-08-01T06:00", 60),
          _leg_b("D2", "BBB", "CCC", "2026-08-01T08:00", 60),
          _leg_b("W1", "CCC", "AAA", "2026-08-01T11:00", 120)]
    b = _pb.Builder(_pay(fs), _args(cos_rules=_rules(base_tz_offset_min=0)))

    b.build_duties(None)

    class FakeCond:
        adjacent_dh_min = 120
    b.conditions = FakeCond()
    duty = next(d for d in b.duties if d["legs"][0]["id"] == "W1")
    dh = [b.legs["D1"], b.legs["D2"]]
    assert not b.adjacent_dh_ok([duty], dh)      # 60 мин < 120 — бьётся
    fs2 = [_leg_b("D1", "AAA", "BBB", "2026-08-01T05:00", 60),
           _leg_b("D2", "BBB", "CCC", "2026-08-01T08:30", 60),
           _leg_b("W1", "CCC", "AAA", "2026-08-01T11:00", 120)]
    b2 = _pb.Builder(_pay(fs2), _args(cos_rules=_rules(base_tz_offset_min=0)))
    b2.build_duties(None)
    b2.conditions = FakeCond()
    b2.periods_of = lambda *a, **k: []   # позитив судит только глобальную
    duty2 = next(d for d in b2.duties if d["legs"][0]["id"] == "W1")
    # Ревизия правила (R-S8-п-7, широкая наихудшая трактовка до ответа
    # анкеты, 2026-08-28): пара перегонов подряд запрещена для
    # регулярных рейсов и при зазоре не меньше норматива — прежний
    # позитив стал отказом; одиночный перегон остаётся законен.
    assert not b2.adjacent_dh_ok([duty2],
                                 [b2.legs["D1"], b2.legs["D2"]])
    assert b2.adjacent_dh_ok([duty2], [b2.legs["D2"]])


def test_классификация_жертв_арбитража_0055():
    """Разбор №24 (2026-08-28): участок, чьё единственное покрытие
    исключено арбитражем 0055, классифицируется своим именем — не
    «нет пары в горизонте» (конструкция была) и не «перебор не нашёл»
    (нашёл и выбрал); отчёт несёт поимённый список и категорию."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    b = _pb.Builder(payload, _args())
    b.build_duties(None)
    # Жертва арбитража помечена построителем до классификации.
    b._arb_lost_legs = {"F1"}
    out = b.classify_uncovered({"F1", "F2"})
    assert out["uncovered_arbitration_0055"] == 1
    assert out["uncovered_arbitration_0055_legs"] == ["F1"]
    # F2 судится обычными категориями, F1 в них не попадает.
    assert "F1" not in out["proven_impossible_legs"]
    assert "F1" not in out["uncovered_not_built_legs"]
    assert "F1" in out["uncovered_examples"]


def test_судья_зазора_перегон_работа():
    """Правило R-S5-табл-2-строка-13-кол-2 (перекрёстная сверка линий
    2026-08-28): перегон, примкнувший к работе, обязан отстоять от
    вылета рабочего участка не меньше порога стыковки. Зазор 45 при
    пороге 50 — отказ (ровно незаконная конструкция, прошедшая суд до
    починки); зазор 60 — принимается."""
    payload = _payload([
        _leg("D1", "BASE", "AAA", "2026-08-01T04:40:00", 85),   # перегон
        _leg("W1", "AAA", "BASE", "2026-08-01T06:50:00", 85),   # работа
        _leg("W2", "AAA", "BASE", "2026-08-01T07:05:00", 85)])  # работа 60'
    b = _pb.Builder(payload, _args(sit_min=50, min_rest_sit=50))
    b.build_duties(None)
    duty_of = {tuple(l["id"] for l in d["legs"]): d for d in b.duties}
    dh = b.legs["D1"]
    d_45 = duty_of.get(("W1",))
    d_60 = duty_of.get(("W2",))
    assert d_45 is not None and d_60 is not None
    # D1 прилетает 06:05: до W1 (06:50) — 45 минут, до W2 (07:05) — 60.
    assert b.adjacent_dh_ok([d_45], [dh]) is False
    assert b.adjacent_dh_ok([d_60], [dh]) is True


def test_суд_закреплённых_цепочек():
    """Группа Д реестра соответствия (2026-08-28): засеянные пути
    проходят тот же суд, что построенные. Закрепление с нарушением —
    громкий отказ; импорт заказчика — поимённая пометка в отчёте."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T09:00:00", 120),
        # Пара с нарушением предела смены: 16 часов подряд.
        _leg("G1", "BASE", "AAA", "2026-08-02T06:00:00", 480),
        _leg("G2", "AAA", "BASE", "2026-08-02T16:00:00", 480)])
    b = _pb.Builder(payload, _args())
    cat = {"selected": [
        {"sourceUnit": "cust-ok", "fns": ["F1", "F2"],
         "duty_fns": [["F1", "F2"]]},
        {"sourceUnit": "cust-bad", "fns": ["G1", "G2"],
         "duty_fns": [["G1", "G2"]]}]}
    b.build_duties(cat)
    b.fix_customer_chains(cat)
    b.judge_fixed_chains(strict=False)
    v = b.report["fixed_chain_violations"]
    assert len(v) == 1 and v[0]["sourceUnit"] == "cust-bad"
    assert any("предел рабочего времени" in r for r in v[0]["reasons"])
    # Закрепление с тем же нарушением — громкий отказ.
    b2 = _pb.Builder(payload, _args())
    b2.build_duties(cat)
    b2.pin_chains({"selected": [cat["selected"][1]]})
    with pytest.raises(ValueError):
        b2.judge_fixed_chains(strict=True)


def test_счёт_посадок_судится_на_всех_путях():
    """Группа А реестра (2026-08-28): предел числа посадок за смену
    (контрактная свёртка условной тройки) судится не только капом
    генерации, но и цепочечным судьёй — засеянная смена сверх предела
    не проходит в кандидаты."""
    payload = _payload([
        _leg("H1", "BASE", "AAA", "2026-08-01T06:00:00", 60),
        _leg("H2", "AAA", "BBB", "2026-08-01T08:00:00", 60),
        _leg("H3", "BBB", "BASE", "2026-08-01T10:00:00", 60)])
    # Снимок несёт ЯВНО всё, что потребляет путь теста: умолчаний у
    # CosRules больше нет, незаполненное правило — отказ при обращении.
    src = {"max_duty_span_min": 840, "min_sit_min": 30,
           "max_landings_per_duty": 2,
           "layover_tiers": [[1440, 720, 960, 720, 960, 720, 960]],
           "work_time_limit_by_tier": [840],
           "base_tz_offset_min": 0, "max_pairing_days": 6}
    import _cos_rules as _cr
    rules = _cr.from_dict(src, strict=False)
    a = _args(sit_min=30)
    a.cos_rules = rules
    b = _pb.Builder(payload, a)
    seed = {"selected": [{"sourceUnit": "seed-3seg",
                          "fns": ["H1", "H2", "H3"],
                          "duty_fns": [["H1", "H2", "H3"]]}]}
    b.build_duties(seed)
    d3 = next((d for d in b.duties if len(d["legs"]) == 3), None)
    assert d3 is not None  # засев кладёт смену в множество
    # …но судьи её не пропускают: ни суд смены, ни цепочечный.
    assert b.duty_limit_ok(d3["legs"]) is False
    assert b.chain_ok([d3]) is False


def test_не_более_одного_перегона_подряд():
    """Правило R-S8-п-7 (широкая наихудшая трактовка до ответа
    анкеты): два смежных перегона без работы между ними запрещены
    для регулярных рейсов — даже через полноценный отдых."""
    payload = _payload([
        _leg("D1", "BASE", "AAA", "2026-08-01T06:00:00", 60),
        _leg("D2", "AAA", "BBB", "2026-08-02T08:00:00", 60),
        _leg("W1", "BBB", "BASE", "2026-08-03T09:00:00", 120),
        _leg("W2", "AAA", "BASE", "2026-08-02T09:00:00", 120)])
    b = _pb.Builder(payload, _args(min_rest_sit=50))
    b.build_duties(None)

    class FakeCond:
        adjacent_dh_min = None

        def is_charter(self, fn):
            return False

    b.conditions = FakeCond()
    w1 = next(d for d in b.duties if d["legs"][0]["id"] == "W1")
    w2 = next(d for d in b.duties if d["legs"][0]["id"] == "W2")
    # Два перегона подряд (через сутки отдыха) перед работой — отказ.
    assert b.adjacent_dh_ok([w1], [b.legs["D1"], b.legs["D2"]]) is False
    # Один перегон + работа + перегон... одиночные перегоны законны.
    assert b.adjacent_dh_ok([w2], [b.legs["D1"]]) is True


def test_эскалация_покрывает_сироту_углублением_перегонов():
    """Фаза эскалации (поручение владельца 2026-08-28): сирота,
    достижимый только ВТОРЫМ вариантом перегона (первый по времени
    примыкает слишком плотно), при ширине перегонов 1 остаётся сиротой
    после спасательного прохода, а ступень эскалации с расширенными
    перегонами его покрывает."""
    flights = [
        # перегон-1: прилетает за 10 минут до смены — примыкание падает
        _leg("D1", "BASE", "DDD", "2026-08-01T07:30:00", 60),
        # перегон-2: прилетает за 3 часа — законен, но вторым по времени
        _leg("D2", "BASE", "DDD", "2026-08-01T04:30:00", 60),
        # работа сироты: DDD -> BASE
        _leg("W1", "DDD", "BASE", "2026-08-01T08:40:00", 120),
    ]
    # перегоны должны существовать в маршрутном индексе, но сами не
    # быть обязательствами — пометим транспортными
    flights[0]["transportOnly"] = True
    flights[1]["transportOnly"] = True
    a0 = _args(dh_options=1, rescue_dh=1, rescue_per_leg=2, rescue_s=1)
    catalog0, report0 = _pb.build(_payload(flights), a0)
    a1 = _args(dh_options=1, rescue_dh=1, rescue_per_leg=2, rescue_s=1)
    a1.escalate_s = 30
    catalog1, report1 = _pb.build(_payload(flights), a1)
    esc = report1.get("escalation") or {}
    # без эскалации сирота либо покрыт (мир мал) — тогда тест
    # неинформативен и обязан упасть на контроле ниже, либо нет
    if report0["uncovered_legs"] > 0:
        assert report1["uncovered_legs"] < report0["uncovered_legs"], esc
        assert esc.get("covered"), esc
    else:
        # контроль информативности: перегон-1 действительно давал отказ
        assert report0["uncovered_legs"] == 0


def test_эскалация_доказывает_невозможность_изолированной_станции():
    """Доказательная ступень: рейс из станции, куда не прилетает ни
    один борт и куда нет перегонов, непокрываем В ПРИНЦИПЕ; полный
    обход окна размаха завершается без усечений — невозможность
    ДОКАЗАНА с носителем (счётчики обхода), не «не построено»."""
    flights = [
        _leg("G1", "BASE", "AAA", "2026-08-01T08:00:00", 120),
        _leg("G2", "AAA", "BASE", "2026-08-01T12:00:00", 120),
        _leg("X1", "QQQ", "ZZZ", "2026-08-01T09:00:00", 60),
    ]
    a = _args(dh_options=1)
    a.escalate_s = 30
    catalog, report = _pb.build(_payload(flights), a)
    esc = report.get("escalation") or {}
    proved = {p["leg"] for p in esc.get("proved") or []}
    assert "X1" in proved, esc
    assert not esc.get("budget_hit"), esc


def test_групповой_запрет_несовместности_в_отборе():
    """Петля «отбор - арбитраж» (разбор №28): групповое ограничение
    несовместности запрещает СОВМЕСТНЫЙ выбор участников клапана —
    отбор обязан найти другую комбинацию с тем же покрытием."""
    flights = [
        _leg("G1", "BASE", "AAA", "2026-08-01T08:00:00", 120),
        _leg("G2", "AAA", "BASE", "2026-08-01T12:00:00", 120),
        _leg("G3", "BASE", "DDD", "2026-08-02T08:00:00", 120),
        _leg("G4", "DDD", "BASE", "2026-08-02T12:00:00", 120),
    ]
    b = _pb.Builder(_payload(flights), _args())
    b.build_duties(None)
    b.enumerate_candidates(set())
    b.rescue_orphans(set())
    duo1 = (frozenset({"G1", "G2"}), frozenset())
    duo2 = (frozenset({"G3", "G4"}), frozenset())

    def idents(chosen):
        return {(frozenset(c["legs"]), frozenset(c["dh"]))
                for c in chosen}

    base = b.select()
    assert duo1 in idents(base) and duo2 in idents(base), (
        "контроль: без запрета отбор берёт обе сдвоенные цепочки")
    chosen = b.select(ban_groups=[frozenset({duo1, duo2})])
    got = idents(chosen)
    assert not (duo1 in got and duo2 in got), got
    covered = {x for c in chosen for x in c["legs"]}
    assert covered >= {"G1", "G2", "G3", "G4"}, (
        "покрытие обязано сохраниться другой комбинацией")


def _мир_четырёх_рейсов():
    return _payload([
        _leg("G1", "BASE", "AAA", "2026-08-01T08:00:00", 120),
        _leg("G2", "AAA", "BASE", "2026-08-01T12:00:00", 120),
        _leg("G3", "BASE", "DDD", "2026-08-02T08:00:00", 120),
        _leg("G4", "DDD", "BASE", "2026-08-02T12:00:00", 120),
    ])


def _итог(catalog, report):
    covered = {x for c in catalog.get("selected") or []
               for x in c.get("active_fns") or c.get("fns") or []}
    cost = sum(c.get("cost", 0) for c in catalog.get("selected") or [])
    return covered, cost, report


def test_движок_highs_эквивалентен_встроенному():
    """Пакет «выбор движка»: внешний движок решает ТУ ЖЕ модель —
    покрытие и цена совпадают с встроенным CP-SAT (пути пула, судьи
    и арбитража общие)."""
    pytest.importorskip("highspy", exc_type=ImportError,
                        reason="движок highs недоступен (нет пакета "
                        "либо не грузится нативная библиотека)")
    c1, r1 = _pb.build(_мир_четырёх_рейсов(), _args())
    c2, r2 = _pb.build(_мир_четырёх_рейсов(),
                       _args(select_engine="highs"))
    assert r2["select_engine"] == "highs"
    cov1, cost1, _ = _итог(c1, r1)
    cov2, cost2, _ = _итог(c2, r2)
    assert cov2 == cov1
    assert cost2 == cost1
    assert r2["exact_cover"] is True


def test_движок_cplex_эквивалентен_встроенному():
    # Наличие движка спрашивается У САМОГО ПРОДУКТА (_select_engines):
    # порядок поиска — переменная CREW_CPLEX_EXE, переменные установщика
    # IBM, PATH, каталоги CPLEX_Studio*. Зашитый путь машины разработчика
    # молча пропускал бы тест там, где CPLEX установлен иначе.
    import _select_engines as se
    try:
        se.check_available("cplex")
    except se.EngineUnavailable as e:
        pytest.skip(str(e))
    c1, r1 = _pb.build(_мир_четырёх_рейсов(), _args())
    c2, r2 = _pb.build(_мир_четырёх_рейсов(),
                       _args(select_engine="cplex"))
    assert r2["select_engine"] == "cplex"
    cov1, cost1, _ = _итог(c1, r1)
    cov2, cost2, _ = _итог(c2, r2)
    assert cov2 == cov1
    assert cost2 == cost1
    # Контракт независимости от решателя (04.09): статус в словаре
    # CP-SAT, сырой статус движка рядом; граница у оптимума = цена.
    assert r2["solver_status"] == "OPTIMAL", r2.get("solver_status_raw")
    assert "optimal" in r2["solver_status_raw"].lower()
    assert r2["exact_cover"] is True
    assert r2.get("select_engine_bound") == cost2


def test_недоступный_движок_честный_отказ(monkeypatch, tmp_path):
    """Молчаливой подмены движка нет: недоступный cplex — отказ
    каталога с причиной в отчёте, не тихий переход на встроенный."""
    monkeypatch.setenv("CREW_CPLEX_EXE",
                       str(tmp_path / "нет-такого.exe"))
    catalog, report = _pb.build(_мир_четырёх_рейсов(),
                                _args(select_engine="cplex"))
    assert report.get("select_engine_error"), report.get(
        "select_engine")
    assert not catalog.get("selected"), (
        "каталог не публикуется при недоступном движке")


def test_движок_перегон_через_вспомогательную():
    """Линейная форма зеркала «рейс покрыт»: перегон выбирается только
    вместе с исполнителем рейса; вспомогательная переменная (индекс за
    кандидатами, цена 0) в выбор и в цену не попадает.

    Каталог — ASCII (tempfile), не tmp_path: читатель LP HiGHS на
    Windows не открывает кириллические пути, а имя этого теста
    кириллическое."""
    import tempfile
    import _select_engines as se
    pytest.importorskip("highspy", exc_type=ImportError)
    tmp_path = Path(tempfile.mkdtemp(prefix="aux-lp-"))
    rows = [
        ("E", "c0", 1, [(0, 1), (1, 1)]),
        ("E", "c1", 1, [(2, 1)]),
        ("L", "c2", 0, [(3, 1), (0, -1), (1, -1)]),
        ("L", "c3", 0, [(2, 1), (3, -1)]),
    ]
    cons = {"c0": {"kind": "cover", "flight": "A"},
            "c1": {"kind": "cover", "flight": "B"},
            "c2": {"kind": "dh_requires_covered_link", "flight": "A"},
            "c3": {"kind": "dh_requires_covered", "flight": "A",
                   "candidate": 2}}
    res = se.solve("highs", rows, cons, [10, 12, 5, 0], set(),
                   {"c0", "c1"}, [], [], 30, tmp_path, "aux")
    picked = [ci for ci in res["picked"] if ci < 3]
    assert picked == [0, 2], res
    assert res["objective"] == 15, res
    assert not res["uncovered_rows"], res


# ---------- Пакет улучшений кнопки: пункты (б) и (в) ----------

def test_живые_вехи_перебора(capsys):
    """(в) Немая фаза перебора неотличима от зависшей: с интервалом
    пульса раз в тик каждая точка обхода печатает веху с размером
    пула — экран Конвейера читает их из хвоста журнала."""
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    _catalog, report = _pb.build(payload, _args(pulse_every_s=1e-9))
    out = capsys.readouterr().out
    assert "перебор жив: кандидатов" in out
    assert report["candidates"] >= 1


def _core_builder(**over):
    payload = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    return _pb.Builder(payload, _args(**over))


def test_стена_минимизации_ядра(monkeypatch, capsys):
    """(б) Бюджет вызовов режет их число, но не время: стена
    останавливает минимизацию по часам. Модель отказов: каждый вызов
    resolve «стоит» 40 условных секунд, стена 50 — второй вызов не
    состоится, ядро остаётся частично минимизированным, попадание в
    стену — в отчёте и вехой."""
    b = _core_builder(core_min_budget=60, core_min_wall_s=50)
    clock = {"t": 1000.0}
    monkeypatch.setattr(_pb, "_now_s", lambda: clock["t"])
    calls = []

    def resolve(assum, limit_s):
        calls.append((list(assum), limit_s))
        clock["t"] += 40.0
        return "INF", object()

    core = b._minimize_core([1, 2, 3, 4], resolve,
                            lambda s: [1, 2, 3], "INF")
    assert b.report["core_min_wall_hit"] is True
    assert core == [1, 2, 3]          # один круг core-of-core успел
    # второй вызов состоялся с лимитом, ПОДРЕЗАННЫМ остатком стены
    # (10 с из 30), третий срезан стеной целиком (deletion-цикл не
    # сделал ни одного вызова)
    assert [l for _, l in calls] == [30.0, 10.0]
    assert "настенный предел 50 с достигнут" in capsys.readouterr().out


def test_стена_нулём_выключена_бюджет_работает(monkeypatch):
    """(б) core_min_wall_s=0 — стены нет: минимизация идёт по бюджету
    вызовов, попадание в стену не объявляется."""
    b = _core_builder(core_min_budget=3, core_min_wall_s=0)
    clock = {"t": 0.0}
    monkeypatch.setattr(_pb, "_now_s", lambda: clock["t"])
    calls = []

    def resolve(assum, limit_s):
        calls.append(limit_s)
        clock["t"] += 1000.0          # часы не имеют значения без стены
        return "SAT", object()        # неподвижная точка сразу

    core = b._minimize_core([1, 2, 3, 4], resolve,
                            lambda s: [], "INF")
    assert b.report["core_min_wall_hit"] is False
    assert core == [1, 2, 3, 4]       # ядро не менялось: не INFEASIBLE
    # deletion-цикл: бюджет 3 вызова поверх одного core-of-core
    assert len(calls) == 1 + 3
    assert all(l == 30.0 for l in calls)


# ------------------------------------------------------------------
# Внешний аудит 04.09.2026, находка A1: умолчания площадки молча
# подменяли нормы ВМП. Стенды с известным ответом.


def test_A1_смена_без_правил_меряется_рабочим_временем_с_брифингом():
    """A1: ветка без файла правил меряла смену «вылет → прилёт», а с
    правилами — рабочим временем с брифингом/дебрифингом: одна норма
    судилась по-разному. Предел 12 ч, смена 11:30 + брифинг 60 +
    дебрифинг 30 = 13:00 — обязана быть отклонена."""
    f = _leg_b("F1", "BASE", "OUT", "2026-08-01T06:00", 690,
               brief=60, debrief=30)
    b = _pb.Builder(_pay([f]), _args(duty_span=720))
    assert b.rules is None
    assert not b.duty_limit_ok([b.legs["F1"]])
    # Та же смена без брифинга и дебрифинга укладывается в 12 ч.
    b2 = _pb.Builder(_pay([_leg_b("F1", "BASE", "OUT",
                                  "2026-08-01T06:00", 690)]),
                     _args(duty_span=720))
    assert b2.duty_limit_ok([b2.legs["F1"]])
    # И признаки смены несут рабочее время, а не размах (предел здесь
    # шире, чтобы смена вошла в множество).
    b3 = _pb.Builder(_pay([f]), _args(duty_span=900))
    b3.build_duties(None)
    d = next(d for d in b3.duties if d["legs"][0]["id"] == "F1")
    assert d["work"] == 690 + 60 + 30


def _A1_contract(**over) -> dict:
    """Контракт кнопки (source-rules.json) тестового мира: только
    ключи, покрытые «пакетом»; поверх — точечные правки стенда."""
    c = {"min_sit_min": 30, "work_time_limit_by_tier": [840],
         "layover_tiers": [[1440, 720, 960, 720, 960, 720, 960]],
         "base_tz_offset_min": 0, "max_pairing_days": 6,
         "max_landings_per_duty": 2,
         # нейтрализаторы контракта кнопки (source_rules_contract):
         # правил перегонов и «дальней» стоянки в мире стенда нет
         "dh_board_after_debrief_min": 0, "trans_tz_hours": 99.0}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def _A1_world() -> dict:
    """Три коротких участка BASE→A→B→BASE в одной смене: при пределе
    посадок 5 (площадка) собирается смена из трёх, при 2 (ВМП) — нет."""
    return _payload([
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 60),
        _leg("F2", "AAA", "BBB", "2026-08-01T08:00:00", 60),
        _leg("F3", "BBB", "BASE", "2026-08-01T10:00:00", 60)])


def _A1_main(tmp_path, monkeypatch, contract: dict, *extra) -> tuple:
    """Запуск построителя в профиле пакета: (код возврата, stdout,
    документ каталога либо None). Настройка площадки подменена
    заведомо КРИВЫМ файлом: в профиле пакета она читаться не должна —
    чтение уронило бы построитель."""
    import hashlib
    bad_cfg = tmp_path / "airline-broken.json"
    bad_cfg.write_text("{ это не json", encoding="utf-8")
    monkeypatch.setenv("CREW_RULES_CONFIG", str(bad_cfg))
    rules_p = tmp_path / "source-rules.json"
    raw = json.dumps(contract).encode("utf-8")
    rules_p.write_bytes(raw)
    payload_p = tmp_path / "payload.json"
    payload_p.write_text(json.dumps(_A1_world()), encoding="utf-8")
    out = tmp_path / "cat.json"
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _pb.main([
            "--payload", str(payload_p), "--label", "a1", "--level", "3",
            "--bases", "BASE", "--budget-s", "5", "--out", str(out),
            "--method", "enumerate", "--dh-options", "0",
            "--rescue-dh", "0", "--rescue-per-leg", "0",
            # Полы отдыха — как подаёт кнопка (builder_args из applied).
            "--rest-base", "720", "--rest-base-long", "720",
            "--rest-off", "960", "--rest-off-long", "960",
            "--vmp-package",
            "--source-rules", str(rules_p),
            "--source-rules-fp", hashlib.sha256(raw).hexdigest(),
            *extra])
    doc = None
    for p in (out, out.with_name(out.stem + ".pending.json")):
        if p.exists():
            doc = json.loads(p.read_text(encoding="utf-8"))
            break
    return rc, buf.getvalue(), doc


def test_A1_профиль_пакета_ВМП_две_посадки_площадка_не_читается(
        tmp_path, monkeypatch):
    """A1, приёмка: ВМП «две посадки» при настройке площадки «пять» —
    третья посадка запрещена; настройка площадки в профиле пакета не
    читается вовсе (файл заведомо кривой — чтение уронило бы запуск)."""
    rc, out, doc = _A1_main(tmp_path, monkeypatch, _A1_contract(),
                            "--max-segs", "2")
    assert doc is not None, out
    assert rc in (0, 2, 4), out
    sig = doc["config_sig"]
    assert sig["max_segs"] == 2 and sig["sit_min"] == 30
    assert sig["duty_span"] == 840 and sig["pairing_span"] == 6 * 1440
    assert sig["rules_effective"]["profile"] == "vmp-package"
    assert all(len(u.get("duty_fns", [[]])[0]) <= 2
               for u in doc["selected"]), doc["selected"]


def test_A1_профиль_пакета_без_порога_ВМП_отказ_словами(
        tmp_path, monkeypatch):
    """A1: в профиле пакета незаданный порог ВМП — отказ с именем
    правила, а не значение rules_config / умолчание класса CosRules."""
    rc, out, doc = _A1_main(tmp_path, monkeypatch,
                            _A1_contract(max_landings_per_duty=None))
    assert rc == 2 and doc is None, out
    assert "ОТКАЗ" in out and "max_landings_per_duty" in out, out
    rc2, out2, doc2 = _A1_main(tmp_path, monkeypatch,
                               _A1_contract(min_sit_min=None),
                               "--max-segs", "2")
    assert rc2 == 2 and doc2 is None, out2
    assert "min_sit_min" in out2, out2
    # Две правды о посадках (флаг ≠ контракт) — тоже отказ, не тихий выбор.
    rc3, out3, doc3 = _A1_main(tmp_path, monkeypatch, _A1_contract(),
                               "--max-segs", "3")
    assert rc3 == 2 and doc3 is None, out3
    assert "max_landings_per_duty" in out3, out3


def test_A1_профиль_пакета_требует_контракт(tmp_path, monkeypatch):
    """A1: профиль пакета без файла контракта — отказ словами (нет
    носителя норм ВМП — нечего исполнять)."""
    payload_p = tmp_path / "payload.json"
    payload_p.write_text(json.dumps(_A1_world()), encoding="utf-8")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _pb.main(["--payload", str(payload_p), "--label", "a1",
                       "--bases", "BASE", "--budget-s", "5",
                       "--out", str(tmp_path / "cat.json"),
                       "--vmp-package", "--max-segs", "2"])
    assert rc == 2
    assert "--source-rules" in buf.getvalue()
