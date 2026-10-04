# -*- coding: utf-8 -*-
"""Этап 5б: экспорт файла условий расчёта из пакета.

Две обязательные стороны: полный машинный набор СОБИРАЕТСЯ и проходит
живой приёмник решателя (_conditions.Conditions — совместимость форм
проверяется исполнением, не сличением полей), а набор с пробелами
даёт отказ с поимённым перечнем (какой ключ, что искали, чем
закрывается). Ни одного числа из воздуха: все ожидания — из фикстуры.
"""
from __future__ import annotations

import pytest

from vmp import conditions_export as ce


def _rule(rid, kind, statement, params=()):
    return {"id": rid, "kind": kind, "statement": statement,
            "params": [dict(p) for p in params]}


def _p(name, value, unit="duration_minutes", relation="min"):
    return {"name": name, "value": value, "unit": unit,
            "relation": relation}


def _model_full() -> dict:
    return {"rules": [
        _rule("R-BRIEF", "HARD", "Предполётная подготовка не менее 90 минут.",
              [_p("briefing_min", 90)]),
        _rule("R-DEBRIEF", "HARD", "Послеполётный разбор не менее 30 минут.",
              [_p("debriefing_min", 30)]),
        _rule("R-SIT", "HARD", "Стыковка между рейсами не менее 50 минут.",
              [_p("min_turnaround", 50)]),
        _rule("R-REST-H", "HARD",
              "Отдых в базе не менее предыдущей смены и не менее 12 часов.",
              [_p("min_rest_home", 720)]),
        _rule("R-REST-O", "HARD",
              "Отдых вне базы не менее предыдущей смены и не менее 10 часов.",
              [_p("min_rest_outstation", 600)]),
        _rule("R-NIGHT", "DEF", "Ночь — любая часть в окне [22:00, 06:00).",
              [_p("night_duty_start", 1320, "time_of_day_minutes", "eq"),
               _p("night_duty_end", 360, "time_of_day_minutes", "eq")]),
        _rule("R-UNK", "HARD", "Неопределённая акклиматизация: пределы "
                               "по посадкам.",
              [_p("unknown_by_landings", 0, "count", "eq")]),
    ]}


def _forms_full() -> dict:
    rows = []
    for lo, hi, vals in ((360, 900, (780, 750, 720, 540)),
                         (900, 1020, (750, 720, 690, 540)),
                         (1020, 1440, (720, 690, 660, 540)),
                         (0, 360, (690, 660, 630, 540))):
        for (s_f, s_t), v in zip(((1, 2), (3, 3), (4, 4), (5, 10)), vals):
            rows.append({"start_from_min": lo, "start_to_min": hi,
                         "sectors_from": s_f, "sectors_to": s_t,
                         "value_min": v, "as_written": "…",
                         "rule": "R-MATRIX"})
    return {"spec": "1.10",
            "matrices": [{"threshold": "max_duty_span_min",
                          "tz": "base-local", "rows": rows,
                          "source_rules": ["R-MATRIX"]}],
            "conditional_floors": [{
                "threshold": "min_rest_outstation_min",
                "condition": {"kind": "tz_diff_hours",
                              "relation": "min", "value": 4},
                "value_min": 960, "as_written": "…",
                "rule": "R-TRANS"}]}


def _payload() -> dict:
    return {
        "employees": [{"id": "E1", "homeAirport": "HUB"}],
        "flights": [
            {"flightNumber": "F1|2025-10-02T08:00:00",
             "departureUTCDateTime": "2025-10-02T08:00:00",
             "arrivalUTCDateTime": "2025-10-02T10:00:00",
             "aircraftType": "T38"},
            {"flightNumber": "F2|2025-10-20T08:00:00",
             "departureUTCDateTime": "2025-10-20T08:00:00",
             "arrivalUTCDateTime": "2025-10-20T10:00:00",
             "aircraftType": "V95"},
            {"flightNumber": "T1|2025-11-02T08:00:00",
             "departureUTCDateTime": "2025-11-02T08:00:00",
             "arrivalUTCDateTime": "2025-11-02T10:00:00",
             "aircraftType": "A990-1", "transportOnly": True},
        ]}


MANIFEST = {"crew_size": {"value": 2, "rule": "R-CREW"}}

# Пробелы точной формы, которые модуль обязан называть до волны
# миграций (машинных носителей в моделях пока нет).
_EXACT_WAVE_GAPS = ("worktime_exact.unknown_by_landings",
                    "worktime_exact.acclimatization",
                    "worktime_exact.extended_by_class_h",
                    "worktime_exact.rest_class_by_fleet")


def test_исключённое_до_получения_данных_не_носитель_условий():
    """Структура ВМП (владелец 2026-09-02): правило с пометкой
    «исключено до получения данных по решению авиакомпании» не читается
    как носитель машинных условий расчёта — ключ остаётся пробелом,
    а не тихо берётся из исключённого правила."""
    model = _model_full()
    found = ce._params(model, "min_turnaround",
                       unit="duration_minutes", relation="min")
    assert [r["id"] for r, _p in found] == ["R-SIT"]
    sit = next(r for r in model["rules"] if r["id"] == "R-SIT")
    sit["excluded_until_data"] = {"question": "Q-3b-нет-данных-sit",
                                  "revision": 1}
    assert [r["id"] for r in ce._active_rules(model)] == [
        "R-BRIEF", "R-DEBRIEF", "R-REST-H", "R-REST-O", "R-NIGHT", "R-UNK"]
    assert ce._params(model, "min_turnaround",
                      unit="duration_minutes", relation="min") == []
    # Пустая пометка не исключает.
    sit["excluded_until_data"] = {}
    assert [r["id"] for r, _p in ce._params(
        model, "min_turnaround", unit="duration_minutes",
        relation="min")] == ["R-SIT"]


def test_полный_машинный_набор_собирается_со_следом():
    """До волны миграций точная форма не собирается ЦЕЛИКОМ — отказ
    перечисляет ровно её недостающие носители, а вся плоская половина
    уже собрана (видно по отсутствию иных пробелов в перечне)."""
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(_model_full(), _forms_full(), MANIFEST,
                            _payload())
    keys = {g["key"] for g in e.value.gaps}
    assert keys == set(_EXACT_WAVE_GAPS), (
        "плоская половина обязана собираться без пробелов")


def test_перечень_пробелов_поимённый_на_бедной_модели():
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions({"rules": []}, None, {}, _payload())
    keys = {g["key"] for g in e.value.gaps}
    for k in ("crew_min_by_fleet", "briefing_base_min",
              "debrief_offbase_min", "sit_offbase_min",
              "rest_base_by_work_h", "rest_offbase_by_work_h",
              "standard_night", "b3_worktime_table",
              "worktime_exact.acclimatized_by_period"):
        assert k in keys, k
    text = str(e.value)
    assert "миграция класса «норма прозой»" in text


def _exact_stub(conds: dict) -> dict:
    """Недостающие носители точной формы — заглушкой ВНЕ модуля:
    форма приёмника требует их целиком, а до волны миграций пакет их
    не несёт. Заглушка живёт в тесте совместимости, не в модуле."""
    we = conds["A"].setdefault("worktime_exact",
                               {"values": {}, "source": "тест"})
    v = we["values"]
    v.setdefault("acclimatized_by_period", [
        {"from": "06:00", "to": "14:59",
         "by_landings": {"1-2": 13.0, "3": 12.5, "4": 12.0, "5+": 9.0}}])
    v["unknown_by_landings"] = {"1-2": 11.5, "3": 11.0, "4": 10.5,
                                "5+": 9.5}
    v["acclimatization"] = {"elapsed_b_h": 48, "tz_from_h": 2}
    v["extended_by_class_h"] = {"2": 17.0}
    v["rest_class_by_fleet"] = {"T38": 2}
    v.setdefault("period_semantics", "per_duty")
    return conds


def test_совместимость_с_живым_приёмником_решателя():
    """Словарь формы условий ест _conditions.Conditions решателя —
    совместимость проверяется исполнением, не сличением полей.
    Плоская половина собрана помощниками модуля, точная форма —
    заглушкой теста (до волны миграций пакет её не несёт)."""
    import importlib.util as _iu
    from pathlib import Path
    spec = _iu.spec_from_file_location(
        "_conditions",
        Path(__file__).resolve().parents[2] / "_conditions.py")
    mod = _iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conds = _exact_stub(_flat_half())
    c = mod.Conditions(conds)
    assert c.crew_std("T38") == 2
    assert c.briefing(True) == 90 and c.debriefing(False) == 30
    assert c.sit_min(False) == 50 and c.sit_min(True) == 50
    # Лестница: смена 13 ч -> отдых 13 ч (max(смена, пол)).
    assert c.required_layover(13 * 60, on_base=True, night=False,
                              trans=False) == 13 * 60
    # Пол: короткая смена -> 12 ч в базе.
    assert c.required_layover(8 * 60, on_base=True, night=False,
                              trans=False) == 12 * 60
    assert c.has_exact_worktime and c.limit_per_duty


def _flat_half() -> dict:
    """Плоская половина словаря условий — помощниками модуля (числа
    только из фикстур); модуль целиком её не отдаёт, пока точная
    форма не собирается (отказ целиком — намеренный fail-closed)."""
    import vmp.conditions_export as m
    model = _model_full()
    A: dict = {}
    hit = m._strictest_min(model, "briefing_min")
    A["briefing_base_min"] = {"value": hit[1]["value"],
                              "source": hit[0]["id"]}
    A["briefing_offbase_min"] = dict(A["briefing_base_min"])
    hit = m._strictest_min(model, "debriefing_min")
    A["debrief_base_min"] = {"value": hit[1]["value"],
                             "source": hit[0]["id"]}
    A["debrief_offbase_min"] = dict(A["debrief_base_min"])
    hit = m._strictest_min(model, "min_turnaround")
    A["sit_offbase_min"] = {"value": hit[1]["value"],
                            "source": hit[0]["id"]}
    A["rest_base_by_work_h"] = m._ladder(720, 780, "тест")
    A["rest_offbase_by_work_h"] = m._ladder(600, 780, "тест")
    for k in ("rest_base_by_work_h", "rest_offbase_by_work_h"):
        A[k]["transmeridian"] = m._ladder(960, 960, "тест")["values"]
    A["chain_max_days"] = {"value": 31, "source": "тест"}
    A["crew_min_by_fleet"] = {"values": {"T38": 2, "V95": 2},
                              "source": "R-CREW"}
    A["crew_std_by_fleet"] = {"values": {"T38": 2, "V95": 2},
                              "source": "R-CREW"}
    A["standard_night"] = {"values": {"start": "22:00", "end": "06:00",
                                      "share": 1e-9},
                           "source": "R-NIGHT"}
    A["estafeta_rest_bonus_min"] = {"value": 0, "source": "тест"}
    A["deadhead_credit_to_work_pct"] = {
        "values": {"without_rest_12h_before": 100}, "source": "тест"}
    A["c_class_attendant_required"] = {"c_class_required_from_h": 999,
                                       "source": "тест"}
    A["trans_tz_from_h"] = {"value": 4, "source": "R-TRANS"}
    B = {
        "b3_worktime_table": {
            "day_by_landings_h": {"1-2": 11.5, "3": 11.0, "4": 10.5,
                                  "5+": 9.0},
            "night_by_landings_h": {"1-2": 11.5, "3": 11.0, "4": 10.5,
                                    "5+": 9.0},
            "source": "тест"},
        "b6_sit_base_min": {"value": 50, "source": "R-SIT"},
        "b12_rest_base_upto_12h": {
            "values": {"work_upto_h": 12, "rest_h_if_ambiguous": 12},
            "source": "тест"},
        "b13_transmeridian_unknown": {"value": True, "source": "тест"},
        "b7_lowcost_vs_charter": {
            "values": {"lowcost_prefix": "§", "charter_digits": 7},
            "source": "тест"},
        "b8_airport_group": {
            "values": {"c_class_required_from_h": 999},
            "source": "тест"},
    }
    return {"base": "HUB", "A": A, "B": B}


def test_лестница_отдыха_точная_запись_правила():
    lad = ce._ladder(720, 780, "тест")
    assert lad["values"][0] == {"work_upto_h": 12.0, "rest_h": 12.0}
    assert lad["values"][-1]["work_upto_h"] == 13.0
    assert len(lad["values"]) == 3          # 12.0, 12.5, 13.0


def test_многобазовый_мир_законен_ноль_баз_отказ():
    """Постановка 2026-08-30 (вопрос линии нано): многобазовый мир
    ЗАКОНЕН — суды «в базе/вне базы» ведёт построитель своим
    множеством из данных; поле base формы — трассировка: одна база
    строкой (байт-паритет однобазовых линий), несколько — списком.
    Отказ остаётся только при НУЛЕ баз. Модель — с ответами анкеты
    (без них полный сбор честно падает пробелами точной формы,
    к базам это отношения не имеет)."""
    def _model():
        m = _model_full()
        m["questions"] = [
            _q("Q-3a-условия-прил4", _ANS4),
            _q("Q-3a-условия-прил6", _ANS6),
            _q("Q-3a-условия-акклиматизация", _ANSA)]
        return m
    payload = _payload()
    payload["employees"].append({"id": "E2", "homeAirport": "VKO"})
    conds = ce.build_conditions(_model(), _forms_full(), MANIFEST,
                                payload)
    assert isinstance(conds["base"], list)
    assert sorted(conds["base"]) == conds["base"]
    assert "VKO" in conds["base"]
    single = ce.build_conditions(_model(), _forms_full(), MANIFEST,
                                 _payload())
    assert isinstance(single["base"], str)   # байт-паритет однобазовых
    empty = _payload()
    for e in empty["employees"]:
        e.pop("homeAirport", None)
        e.pop("base", None)
    with pytest.raises(ce.ConditionsExportError) as err:
        ce.build_conditions(_model(), _forms_full(), MANIFEST,
                            empty)
    assert "ни одной базы" in str(err.value)


def test_флоты_обязательств_без_транспорта():
    assert ce._fleets_of(_payload()) == ["T38", "V95"], (
        "транспортный рейс не приносит флот в состав")


# ------------------- регрессии ревью 2026-08-27 по модулю экспорта

def test_стыковка_берётся_словарным_именем_с_областями():
    model = _model_full()
    model["rules"].append(_rule(
        "R-SIT-BASE", "HARD", "Стыковка в базе 1,5 часа.",
        [dict(_p("min_turnaround", 90), qualifier="база")]))
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(model, _forms_full(), MANIFEST, _payload())
    assert not any(g["key"] == "sit_offbase_min"
                   for g in e.value.gaps), (
        "носители min_turnaround есть — пробела стыковки быть не "
        "должно")


def test_стыковка_только_базовая_наихудшая_трактовка_вне_базы():
    base, off, anyq = ce._sit_carriers({"rules": [_rule(
        "R-B", "HARD", "…",
        [dict(_p("min_turnaround", 90), qualifier="база")])]})
    assert base and not off and not anyq


def test_носитель_в_чужой_единице_не_берётся():
    model = {"rules": [_rule(
        "R-H", "HARD", "Подготовка 2 часа.",
        [{"name": "briefing_min", "value": 2, "unit": "hours",
          "relation": "min"}])]}
    assert ce._strictest_min(model, "briefing_min") is None, (
        "часы вместо минут — чужая единица, пол 2 мин был бы ложью")


def test_носитель_с_отношением_потолка_не_пол():
    model = {"rules": [_rule(
        "R-H", "HARD", "Не более 45 минут.",
        [{"name": "briefing_min", "value": 45,
          "unit": "duration_minutes", "relation": "max"}])]}
    assert ce._strictest_min(model, "briefing_min") is None


def test_окно_матрицы_через_полночь_честный_отказ():
    forms = _forms_full()
    forms["matrices"][0]["rows"][0]["start_from_min"] = 1020
    forms["matrices"][0]["rows"][0]["start_to_min"] = 300
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(_model_full(), forms, MANIFEST, _payload())
    assert any("через полночь" in g["missing"] for g in e.value.gaps)


def test_чужой_условный_порог_не_строит_дальнюю_лестницу_базы():
    """Условный пол привязан к min_rest_outstation_min — дальняя
    колонка БАЗЫ обязана быть «нет ужесточения» (обычные значения),
    а не чужим полом."""
    with pytest.raises(ce.ConditionsExportError):
        try:
            ce.build_conditions(_model_full(), _forms_full(),
                                MANIFEST, _payload())
        except ce.ConditionsExportError as err:
            raise err
    # Сборка лестниц идёт до отказа — проверяем помощником напрямую.
    lad = ce._ladder(720, 780, "т")
    assert lad["values"] == [
        {"work_upto_h": 12.0, "rest_h": 12.0},
        {"work_upto_h": 12.5, "rest_h": 12.5},
        {"work_upto_h": 13.0, "rest_h": 13.0}]


# ------------- ответы анкеты как носители таблиц (2026-08-27, рев. 9)

_ANS4 = ("Пределы служебного полётного времени при неопределённой "
         "акклиматизации (прил. 4), по числу посадок: 1-2 сектора = "
         "11:30 (690 минут); 3 сектора = 11:00 (660 минут); 4 сектора "
         "= 10:30 (630 минут); 5 и более секторов = 9:30 (570 минут).")
_ANS6 = ("Классы мест отдыха по типам ВС: T31 = класс 2; T38 = "
         "класс 2; T8M = класс 2; A990-1 = класс 3. Продлённые "
         "пределы по классам: класс 1 = 18:00 (1080 минут); класс 2 "
         "= 17:00 (1020 минут); класс 3 = 16:00 (960 минут). Типы "
         "без класса (V75, V95, WJX) — продление запрещено.")
_ANSA = ("Экипаж неакклиматизирован при разнице часовых поясов БОЛЕЕ "
         "2 часов (порог tz_from_h = 2 часа). Акклиматизация "
         "сохраняется до 48 часов после прибытия (elapsed_b_h = 48).")


def _q(qid, raw):
    return {"id": qid, "part": "3a", "title": qid, "text": "…",
            "answer_format": {"type": "value"},
            "answer": {"raw": raw, "status": "применён",
                       "revision": 9}}


def test_разборы_ответов_условий():
    assert ce._parse_app4(_ANS4) == {"1-2": 11.5, "3": 11.0,
                                     "4": 10.5, "5+": 9.5}
    classes, ext = ce._parse_app6(_ANS6)
    assert classes == {"T31": 2, "T38": 2, "T8M": 2, "A990-1": 3}
    assert ext == {"1": 18.0, "2": 17.0, "3": 16.0}
    assert ce._parse_acclim(_ANSA) == {"elapsed_b_h": 48,
                                       "tz_from_h": 2}


def test_условия_собираются_целиком_с_ответами():
    model = _model_full()
    model["questions"] = [
        _q("Q-3a-условия-прил4", _ANS4),
        _q("Q-3a-условия-прил6", _ANS6),
        _q("Q-3a-условия-акклиматизация", _ANSA)]
    conds = ce.build_conditions(model, _forms_full(), MANIFEST,
                                _payload())
    we = conds["A"]["worktime_exact"]["values"]
    assert we["unknown_by_landings"]["5+"] == 9.5
    assert we["extended_by_class_h"]["1"] == 18.0
    assert we["acclimatization"] == {"elapsed_b_h": 48,
                                     "tz_from_h": 2}
    # Верх лестницы поднялся до продлённого предела (18 ч).
    assert conds["A"]["rest_base_by_work_h"]["values"][-1] \
        == {"work_upto_h": 18.0, "rest_h": 18.0}
    # Живой приёмник ест собранный целиком словарь.
    import importlib.util as _iu
    from pathlib import Path
    spec = _iu.spec_from_file_location(
        "_conditions",
        Path(__file__).resolve().parents[2] / "_conditions.py")
    mod = _iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c = mod.Conditions(conds)
    assert c.has_exact_worktime and c.limit_per_duty


def test_current_question_edition_reaches_complete_conditions_export():
    import copy
    from cdpm import answers, question_editions as qe

    model = _model_with_answers()
    model["meta"] = {"revision": 10}
    q = next(q for q in model["questions"] if q["id"] == ce._Q_APP4)
    qe.freeze(q)
    before = copy.deepcopy(q)
    new = qe.expand(model, q, [], content={"text": "Уточните пределы по секторам."})
    # A pending successor does not silently restore the old normative answer.
    with pytest.raises(ce.ConditionsExportError) as error:
        ce.build_conditions(model, _forms_full(), MANIFEST, _payload())
    assert any(g["key"] == "worktime_exact.unknown_by_landings" for g in error.value.gaps)
    raw = "1-2 сектора = 10:00; 3 сектора = 9:30; 4 сектора = 9:00; 5+ секторов = 8:00"
    assert answers.apply_answers(model, {new["id"]: raw})["applied"] == 1
    conds = ce.build_conditions(model, _forms_full(), MANIFEST, _payload())
    assert conds["A"]["worktime_exact"]["values"]["unknown_by_landings"] == {
        "1-2": 10.0, "3": 9.5, "4": 9.0, "5+": 8.0}
    assert q == before


def test_условный_пол_после_продления_не_становится_общим():
    model = _model_full()
    model["rules"].append(_rule(
        "R-EXT-REST", "HARD",
        "После продления мин. отдых >= max(предыдущее служебное "
        "время, 14 ч).", [_p("min_rest_home", 840)]))
    model["questions"] = [
        _q("Q-3a-условия-прил4", _ANS4),
        _q("Q-3a-условия-прил6", _ANS6),
        _q("Q-3a-условия-акклиматизация", _ANSA)]
    conds = ce.build_conditions(model, _forms_full(), MANIFEST,
                                _payload())
    assert conds["A"]["rest_base_by_work_h"]["values"][0] \
        == {"work_upto_h": 12.0, "rest_h": 12.0}, (
        "условный пол 14 ч не должен подменять общий 12 ч")


def test_интервал_между_перегонами_из_носителя():
    """Живой урок боевого набора: без ключа adjacent_deadhead_min
    построитель добирал рейс парой перегонов со стыковкой 60 мин."""
    model = _model_full()
    model["rules"].append(_rule(
        "R-DH-SIT", "SOFT",
        "Практическая минимальная стыковка между DH составляет не "
        "менее 2 часов.",
        [_p("min_turnaround", 120)]))
    model["questions"] = [
        _q("Q-3a-условия-прил4", _ANS4),
        _q("Q-3a-условия-прил6", _ANS6),
        _q("Q-3a-условия-акклиматизация", _ANSA)]
    conds = ce.build_conditions(model, _forms_full(), MANIFEST,
                                _payload())
    adj = conds["A"]["adjacent_deadhead_min"]
    assert adj["value"] == 120 and "R-DH-SIT" in adj["source"]
    # Обычная стыковка мягким DH-носителем не подменяется.
    assert conds["A"]["sit_offbase_min"]["value"] == 50


def test_гомоглифы_флотов_канонизируются_к_типам_набора():
    """Живой брак одного из прогонов добора: ключ класса «A990-1»
    латиницей против типа «А990-1» кириллицей в данных — класс молча
    не находился, продлённый предел исчезал."""
    fleets = ["T38", "\u0410990-1"]      # А990-1 с кириллической А
    canon, notes = ce._canon_fleet_keys(
        {"A990-1": 3, "T38": 2, "XX9": 1}, fleets)
    assert canon["\u0410990-1"] == 3, (
        "латинский ключ обязан лечь на кириллический тип набора")
    assert canon["T38"] == 2
    assert canon["XX9"] == 1 and any("не найден" in n for n in notes)
    assert any("канонизирован" in n for n in notes)


def test_сборка_канонизирует_классы_к_payload():
    model = _model_full()
    model["questions"] = [
        _q("Q-3a-условия-прил4", _ANS4),
        _q("Q-3a-условия-прил6", _ANS6),
        _q("Q-3a-условия-акклиматизация", _ANSA)]
    payload = _payload()
    payload["flights"][2]["transportOnly"] = None
    payload["flights"][2]["aircraftType"] = "\u0410990-1"  # кир. А
    conds = ce.build_conditions(model, _forms_full(), MANIFEST,
                                payload)
    classes = conds["A"]["worktime_exact"]["values"][
        "rest_class_by_fleet"]
    assert classes.get("\u0410990-1") == 3, (
        "класс обязан лечь на фактический тип набора байт-в-байт")
    assert "A990-1" not in classes


def _ladder_form() -> dict:
    return {"unit": "minutes",
            "steps": [
                {"extra_crew": 1,
                 "by_class_min": {"1": 960, "2": 900, "3": 840},
                 "rule": "R-EXT-1"},
                {"extra_crew": 2,
                 "by_class_min": {"1": 1020, "2": 960, "3": 900},
                 "rule": "R-EXT-2"}],
            "long_sector_bonus": {
                "add_min": 60,
                "condition": {"kind": "one_sector_over_h", "value": 9,
                              "max_sectors": 2},
                "rule": "R-EXT-B"}}


def _model_with_answers() -> dict:
    model = _model_full()
    model["questions"] = [
        _q("Q-3a-условия-прил4", _ANS4),
        _q("Q-3a-условия-прил6", _ANS6),
        _q("Q-3a-условия-акклиматизация", _ANSA)]
    return model


def test_лестница_продления_из_машинной_формы():
    """Форма extended_ladder пакета: ступени по возрастанию состава
    едут в условия со следом правил-носителей, верхняя ступень
    замещает плоский предел ответа прил. 6; немонотонная лестница —
    поимённый пробел, а не молчаливое применение (fail-closed)."""
    forms = _forms_full()
    forms["extended_ladder"] = _ladder_form()
    conds = ce.build_conditions(_model_with_answers(), forms,
                                MANIFEST, _payload())
    we = conds["A"]["worktime_exact"]["values"]
    assert we["extended_ladder"]["steps"] == [
        {"extra_crew": 1,
         "by_class_min": {"1": 960, "2": 900, "3": 840}},
        {"extra_crew": 2,
         "by_class_min": {"1": 1020, "2": 960, "3": 900}}]
    assert we["extended_ladder"]["long_sector_bonus"]["add_min"] == 60
    # верхняя ступень замещает плоский предел прил. 6 (там 18/17/16)
    assert we["extended_by_class_h"] == {"1": 17.0, "2": 16.0,
                                         "3": 15.0}
    assert "R-EXT-1" in we["_sources"]["extended_ladder"]
    assert "R-EXT-B" in we["_sources"]["extended_ladder"]
    # немонотонная по классу лестница — пробел
    bad = _forms_full()
    bad["extended_ladder"] = _ladder_form()
    bad["extended_ladder"]["steps"][1]["by_class_min"]["2"] = 800
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(_model_with_answers(), bad, MANIFEST,
                            _payload())
    assert any(g["key"] == "worktime_exact.extended_ladder"
               for g in e.value.gaps)
    # ступень без правила-носителя — пробел (число без носителя)
    bad2 = _forms_full()
    bad2["extended_ladder"] = _ladder_form()
    del bad2["extended_ladder"]["steps"][0]["rule"]
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(_model_with_answers(), bad2, MANIFEST,
                            _payload())
    assert any(g["key"] == "worktime_exact.extended_ladder"
               for g in e.value.gaps)


def test_типы_вне_автоматического_планирования_по_семантике_формы():
    """Исключение по типам: ключ собирается ТОЛЬКО при явной семантике
    формы apply_by_type. Описательная семантика (носитель истины —
    разметка данных) и отсутствие формы — ключа НЕТ: консенсус
    выпуска ВМП 2026-08-28 «сплошное применение перечня запрещено»
    (живой урок боевого прогона: сплошная разметка перечнем перевела
    бы в перегоны заметную долю обязательств покрытия)."""
    forms = _forms_full()
    forms["fleet_dh_marked"] = {
        "types": ["T31", "WJX"], "action": "deadhead",
        "semantics": "apply_by_type", "rule": "R-2AE"}
    conds = ce.build_conditions(_model_with_answers(), forms,
                                MANIFEST, _payload())
    excl = conds["A"]["fleet_auto_plan_excluded"]
    assert excl["types"] == ["T31", "WJX"]
    assert "R-2AE" in excl["source"]
    # описательная семантика — ключа нет (правило исполняют данные)
    forms2 = _forms_full()
    forms2["fleet_dh_marked"] = {
        "types": ["T31", "WJX"], "action": "deadhead",
        "semantics": "master_data_marking_description",
        "rule": "R-2AE"}
    conds2 = ce.build_conditions(_model_with_answers(), forms2,
                                 MANIFEST, _payload())
    assert "fleet_auto_plan_excluded" not in conds2["A"]
    # формы нет — ключа нет, даже при правиле-носителе в модели
    model3 = _model_with_answers()
    model3["rules"].append(_rule(
        "R-2AE", "DATA",
        "Все леги на воздушных судах типов 'T31|WJX' маркируются "
        "как deadhead (перегон/заброска).", []))
    conds3 = ce.build_conditions(model3, _forms_full(), MANIFEST,
                                 _payload())
    assert "fleet_auto_plan_excluded" not in conds3["A"]


def _detectors_file(tmp_path, monkeypatch):
    """Герметичный носитель детекторов контура (принцип шаблона
    2026-08-29: формулировки — данные, механика — код); тест не
    зависит от живого файла машины."""
    import json
    p = tmp_path / "detectors.json"
    p.write_text(json.dumps({
        "form_version": 1, "customer": "Т", "detectors": [
            {"key": "base_sit_dep_utc_before_min",
             "pattern": "стыковк\\w*\\s+в\\s+базовом\\s+аэропорту"
                        "\\s+не\\s+планируются"},
            {"key": "base_turn_dep_utc_before_min",
             "pattern": "не\\s+могут\\s+содержать\\s+разворот\\s+в"
                        "\\s+базовом\\s+аэропорту"}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CREW_VMP_DETECTORS", str(p))


def test_пороги_ранних_вылетов_из_базы_из_формулировок(
        tmp_path, monkeypatch):
    """Пороги «до HH:MM UTC» ранних вылетов из базы — из замороженных
    формулировок правил-носителей; ДЕТЕКТОРЫ формулировок — файлом
    контура данных (конвенция 2026-08-29); порог без разбираемого
    времени — честный пробел; правил нет — ключей нет."""
    _detectors_file(tmp_path, monkeypatch)
    model = _model_with_answers()
    model["rules"].append(_rule(
        "R-SIT-04", "HARD",
        "Стыковки в базовом аэропорту не планируются для рейсов с "
        "вылетом до 04:00 UTC.", []))
    model["rules"].append(_rule(
        "R-TURN-04", "HARD",
        "Пейринги с вылетом из HUB до 04:00 UTC не могут содержать "
        "разворот в базовом аэропорту; завершение должно происходить "
        "при возвращении на HUB", []))
    conds = ce.build_conditions(model, _forms_full(), MANIFEST,
                                _payload())
    sit = conds["A"]["base_sit_dep_utc_before_min"]
    assert sit["value"] == 240 and "R-SIT-04" in sit["source"]
    turn = conds["A"]["base_turn_dep_utc_before_min"]
    assert turn["value"] == 240 and "R-TURN-04" in turn["source"]
    # порог прозой без «до HH:MM UTC» — пробел, а не молчание
    model2 = _model_with_answers()
    model2["rules"].append(_rule(
        "R-SIT-X", "HARD",
        "Стыковки в базовом аэропорту не планируются для ранних "
        "рейсов.", []))
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(model2, _forms_full(), MANIFEST,
                            _payload())
    assert any(g["key"] == "base_sit_dep_utc_before_min"
               for g in e.value.gaps)
    # без правил-носителей ключей нет
    conds3 = ce.build_conditions(_model_with_answers(), _forms_full(),
                                 MANIFEST, _payload())
    assert "base_sit_dep_utc_before_min" not in conds3["A"]
    assert "base_turn_dep_utc_before_min" not in conds3["A"]


def test_размах_командировки_из_ответа():
    """Живой брак прогона добора: без машинного предела размаха
    построитель судил умолчанием чужого набора."""
    assert ce._parse_span("предел 11520 минут (8 суток)") == 11520
    assert ce._parse_span("8 суток непрерывно") == 11520
    model = _model_full()
    model["questions"] = [
        _q("Q-3a-условия-прил4", _ANS4),
        _q("Q-3a-условия-прил6", _ANS6),
        _q("Q-3a-условия-акклиматизация", _ANSA),
        _q("Q-3a-условия-размах-командировки",
           "Максимальный размах командировки с перегонами = 11520 "
           "минут (8 непрерывных суток).")]
    conds = ce.build_conditions(model, _forms_full(), MANIFEST,
                                _payload())
    assert conds["A"]["max_pairing_span_min"]["value"] == 11520
    assert conds["A"]["chain_max_days"]["value"] == 9, (
        "календарная оболочка 8 непрерывных суток = 9 дат")


def test_b12_несёт_машинный_порог_work_upto_h():
    """Пакет «умолчания-в-данные» 2026-08-29: порог «смена до 12
    часов» ветви b12 едет в файле условий ключом work_upto_h тем же
    носителем (машинный пол лестницы базы, min_rest_home=720), тем же
    source — приёмник этапа 3 начнёт судить порогом из данных."""
    conds = ce.build_conditions(_model_with_answers(), _forms_full(),
                                MANIFEST, _payload())
    b12 = conds["B"]["b12_rest_base_upto_12h"]
    assert b12["values"]["work_upto_h"] == 12
    assert b12["values"]["rest_h_if_ambiguous"] == 12
    assert b12["source"] == conds["A"]["rest_base_by_work_h"]["source"]


def test_b12_пол_не_12_часов_прежний_именованный_пробел():
    """Контракт находки 16 закреплён и НЕ ослаблен переносом порога:
    машинный пол базы не 12 ч — ветвь b12 не собирается, пробел
    называется поимённо (снятие проверки — отдельное решение
    владельца, не этот пакет)."""
    model = _model_with_answers()
    for r in model["rules"]:
        if r["id"] == "R-REST-H":
            r["params"][0]["value"] = 660          # пол 11 ч
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(model, _forms_full(), MANIFEST,
                            _payload())
    gaps = [g for g in e.value.gaps
            if g["key"] == "b12_rest_base_upto_12h"]
    assert gaps and "несовместимы" in gaps[0]["missing"]


def test_спящие_детекторы_не_молчат_о_пороге(tmp_path, monkeypatch):
    """Страж молчаливого ослабления: правило с порогом «до HH:MM
    UTC» при отсутствующем носителе детекторов — громкий пробел;
    кривой носитель — честный отказ словами."""
    monkeypatch.setenv("CREW_VMP_DETECTORS",
                       str(tmp_path / "нет.json"))
    model = _model_with_answers()
    model["rules"].append(_rule(
        "R-EARLY", "HARD",
        "Ранние вылеты до 04:00 UTC ограничены.", []))
    with pytest.raises(ce.ConditionsExportError) as e:
        ce.build_conditions(model, _forms_full(), MANIFEST,
                            _payload())
    assert any(g["key"] == "condition_detectors"
               for g in e.value.gaps)
    bad = tmp_path / "кривой.json"
    bad.write_text("{", encoding="utf-8")
    monkeypatch.setenv("CREW_VMP_DETECTORS", str(bad))
    with pytest.raises(ce.ConditionsExportError, match="кривой"):
        ce.build_conditions(model, _forms_full(), MANIFEST,
                            _payload())
