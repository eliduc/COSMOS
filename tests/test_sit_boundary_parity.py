# -*- coding: utf-8 -*-
"""Паритет границы «стыковка / отдых» (решение владельца 06.09.2026;
уточнения GPT-053 / CDPM-038): на одной линии времени построитель
цепочек (без правила максимальной стыковки — по полу отдыха станции, с
правилом — --sit-max = T), проверка по ИДАК (source_check:
split_duties_by_place / split_duties и исполнитель rest_min) и ожидаемое
поведение решателя (нормативные смены по полу отдыха по месту; «Rest
between duty periods» при T > 0 и T ≤ разрыв < R — зеркало Kotlin,
удостоверенное текстом исходников) дают одни границы смен и одно число
интервалов отдыха — И сходятся с НЕЗАВИСИМЫМИ ожидаемыми ответами на
ручных примерах (короче пола — стыковка; равен полу — отдых; длиннее
max sit, но короче пола — запрет при объявленном max sit / стыковка при
необъявленном), а не только друг с другом.

Случай 1 — пакет polyaris-h0kw (полы отдыха 720 дома / 600 вне базы,
ручки max_sit_duration нет) → показанием T = 600 у всех, критерий
границы — пол по месту (модельное упрощение, заявлено).
Случай 2 — пакет с ручкой max_sit_duration = 240 → T = 240 у всех.
Случай 3 — правил нет → значения нет: остановка словами / not_checkable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import _pairing_builder as _pb
from tests.test_pairing_builder import _args, _leg, _payload
from vmp import sit_boundary as sb
from vmp import source_check as sc
from vmp import source_check_run as scr

REPO = Path(__file__).resolve().parents[1]
KT = (REPO / "kotlin-solver" / "src" / "main" / "kotlin" / "org" / "acme"
      / "flighcrewscheduling" / "solver")
H0KW = REPO / "open-bench" / "polyaris-h0kw" / "etalon" / "VMP-H0KW.json"

#: Полы отдыха набора H0-КИ-окна (VN-023 / VN-024) — сверяются с эталоном.
R_HOME, R_OUT = 720, 600
HOME = "BASE"
BY_PLACE = {"home": R_HOME, "outstation": R_OUT}


# ------------------------------------------------------------ оснастка

def _h0kw_readings() -> tuple[dict, dict]:
    """Применённые пороги решателя для пакета h0kw в именах ручек
    (RunThresholds.KNOBS: min_rest_home → min_rest_home_min и т. д.) и
    правила-источники — из эталонной модели ВМП комплекта."""
    doc = json.loads(H0KW.read_text(encoding="utf-8"))
    names = {"min_rest_home": "min_rest_home_min",
             "min_rest_outstation": "min_rest_outstation_min",
             "max_sit_duration": sb.KNOB}
    readings: dict = {}
    rules: dict = {}
    for r in doc["rules"]:
        params = (r.get("template_fit") or {}).get("params") or {}
        for name, cell in params.items():
            if name in names:
                readings[names[name]] = int(cell["value"])
                rules.setdefault(names[name], []).append(str(r["id"]))
    return readings, rules


def _roster(legs: list[dict]) -> dict:
    return {"employees": [{"id": "E1", "homeAirport": HOME}],
            "flights": legs,
            "flightAssignments": [{"flight": f["flightNumber"],
                                   "employee": "E1"} for f in legs]}


def _rows(legs: list[dict]) -> list[dict]:
    """Линия времени одного сотрудника (база HOME) по участкам — той же
    формы, что строит source_check.build_timelines."""
    return sc.build_timelines(_roster(legs))["by_employee"]["E1"]


def _rest_spec() -> dict:
    return {"id": "rest", "kind": "rest_min", "anchor": "п.1",
            "params": {"home_min": R_HOME, "outstation_min": R_OUT}}


def _boundary(knob: int | None) -> dict:
    """Показание прогона о границе — как его отдаёт rest_boundary: с
    ручкой — порог (basis knob), без ручки — пол по месту (rest_floor,
    упрощение заявлено)."""
    if knob:
        return {"rest_boundary_min": knob, "rest_boundary_source": "стенд: ручка",
                "rest_boundary_basis": "knob",
                "rest_boundary_by_place": dict(BY_PLACE),
                "rest_boundary_assumption": None}
    return {"rest_boundary_min": min(BY_PLACE.values()),
            "rest_boundary_source": "стенд: пол по месту",
            "rest_boundary_basis": "rest_floor",
            "rest_boundary_by_place": dict(BY_PLACE),
            "rest_boundary_assumption": sb.ASSUMPTION}


def _need(row: dict) -> int:
    """Пол отдыха по месту прилёта участка (Kotlin minRestAt: atHome →
    minRestHome, иначе minRestOut)."""
    return R_HOME if row["arr"] == HOME else R_OUT


def _gap(a: dict, b: dict) -> float:
    return (b["t0"] - a["t1"]).total_seconds() / 60


def solver_normative_duties(rows: list[dict]) -> list[list[dict]]:
    """Зеркало NormativeDuties.slice: разрез там, где разрыв до следующего
    вылета не меньше законного отдыха В ЭТОМ МЕСТЕ (по последнему участку
    текущей смены); порог T здесь не участвует."""
    out: list[list[dict]] = []
    cur: list[dict] = []
    duty_end = None
    for r in rows:
        if cur:
            gap = (r["t0"] - duty_end).total_seconds() / 60
            if gap >= _need(cur[-1]):
                out.append(cur)
                cur = []
                duty_end = r["t1"]
            elif r["t1"] > duty_end:
                duty_end = r["t1"]
        else:
            duty_end = r["t1"]
        cur.append(r)
    if cur:
        out.append(cur)
    return out


def solver_rest_penalty(rows: list[dict], t: int) -> int:
    """Зеркало restBetweenDutyPeriods: штраф при T > 0 и T ≤ разрыв < R
    по месту (соседние назначения сотрудника)."""
    pen = 0
    for a, b in zip(rows, rows[1:]):
        gap = _gap(a, b)
        if t > 0 and t <= gap < _need(a):
            pen += int(_need(a) - gap)
    return pen


def solver_duty_span_overage(rows: list[dict], max_span: int) -> float:
    """Зеркало maxDutyPeriodRestSegmented: размах каждой нормативной
    смены сверх предела."""
    over = 0.0
    for duty in solver_normative_duties(rows):
        span = (max(r["t1"] for r in duty) - duty[0]["t0"]).total_seconds() / 60
        over += max(0.0, span - max_span)
    return over


def _builder(legs: list[dict], knob: int | None,
             duty_span: int = 600) -> tuple[set, list, dict]:
    """Каталог построителя при полах отдыха пакета h0kw: без ручки
    --sit-max не подаётся (как кнопка), с ручкой --sit-max = T.
    Возврат: ({кортежи смен}, [кортежи рейсов по цепочкам], отчёт)."""
    cat, rep = _pb.build(_payload(legs), _args(
        sit_max=knob, rest_base=R_HOME, rest_off=R_OUT,
        rest_base_long=R_HOME, rest_off_long=R_OUT,
        duty_span=duty_span, sit_min=45, max_segs=6, dh_options=1))
    duties = {tuple(d) for c in cat["selected"] for d in c["duty_fns"]}
    chains = [tuple(f for d in c["duty_fns"] for f in d)
              for c in cat["selected"]]
    return duties, chains, {"catalog": cat, "report": rep}


def _check_duties(legs: list[dict], knob: int | None) -> set:
    """Смены проверки по ИДАК тем же критерием границы, что применяет
    rest_min: ручка — порог, без ручки — пол по месту."""
    rows = _rows(legs)
    duties = (sc.split_duties(rows, knob) if knob
              else sc.split_duties_by_place(rows, HOME, BY_PLACE))
    return {tuple(r["flight"] for r in d) for d in duties}


def _rest_check(legs: list[dict], knob: int | None):
    return sc.run_checks([_rest_spec()], _roster(legs),
                         rest_boundary=_boundary(knob))[0]


# ------------------------------------------------ свидетель Kotlin

def test_зеркало_решателя_удостоверено_текстом_исходников():
    """Формулы, которые повторяют функции-зеркала выше, стоят в Kotlin
    дословно: нормативные смены режутся по полу отдыха по месту,
    «Rest between duty periods» судит T ≤ разрыв < R только при T > 0,
    ручка пакета max_sit_duration → day_break_threshold_min, а в пакетном
    окне ручка, о которой пакет молчит, читается как 0."""
    prov = (KT / "FlightCrewSchedulingConstraintProvider.kt").read_text(
        encoding="utf-8")
    assert "if (gap >= restMinimum(cur.last()))" in prov
    assert ("if (dayBreakThreshold > 0 && gap in dayBreakThreshold "
            "until minRestAt(fa1))") in prov
    assert re.search(r"fun minRestAt\(fa1: FlightAssignment\): Int = "
                     r"if \(atHome\(fa1\)\) minRestHome else minRestOut", prov)
    rt = (KT / "RunThresholds.kt").read_text(encoding="utf-8")
    assert '"max_sit_duration" to Knob("day_break_threshold_min"' in rt
    assert "current[threshold]?.value ?: (if (packagedWindow) 0 else fallback)" in rt


# ------------------------------------------------ случай 1: h0kw, T = 600

def test_h0kw_T_выводится_из_пола_отдыха_600_у_всех():
    """Комплект polyaris-h0kw: полы 720 / 600, ручки max_sit_duration нет
    → одно и то же показание T = 600 и критерий «пол по месту» у кнопки
    (derive по applied), у проверки ростера (derive по показаниям прогона)
    и у проверки пейринга (ведомость кнопки без --sit-max); упрощение
    заявлено одними словами."""
    readings, rules = _h0kw_readings()
    assert readings == {"min_rest_home_min": R_HOME,
                        "min_rest_outstation_min": R_OUT}, readings
    assert sb.KNOB not in readings                  # ручки в пакете нет
    packaged = dict(readings, **{sb.KNOB: 0})       # так читает решатель
    got = sb.derive(packaged, rules_of=rules)
    assert got["value"] == 600 and got["basis"] == "rest_floor"
    assert got["by_place"] == BY_PLACE
    assert got["reading"] == "min_rest_outstation_min"
    assert "VN-024" in got["source"] and "выведено из пола" in got["source"]
    assert got["assumption"] == sb.ASSUMPTION
    # проверка ростера — из показаний прогона того же вида
    rb = scr.rest_boundary("roster", run_id="r1", applied={
        "runId": "r1", "lastRun": {"profileReadings": packaged,
                                   "thresholds": {"applied": [
                                       {"threshold": k, "value": v,
                                        "rules": rules[k]}
                                       for k, v in readings.items()]}}})
    assert rb["rest_boundary_min"] == 600 and "VN-024" in rb["rest_boundary_source"]
    assert rb["rest_boundary_basis"] == "rest_floor" \
        and rb["rest_boundary_by_place"] == BY_PLACE \
        and rb["rest_boundary_assumption"] == sb.ASSUMPTION
    # проверка пейринга — из ведомости кнопки (ключа --sit-max нет,
    # полы отдыха построителя те же)
    pb = scr.rest_boundary("pairing", builder_input={
        "args": ["--rest-base", str(R_HOME), "--rest-off", str(R_OUT)],
        "provenance": {"--sit-max": {"value": None, "passed": False,
                                     "from": got["source"]}}})
    assert pb["rest_boundary_min"] == 600 and "VN-024" in pb["rest_boundary_source"]
    assert pb["rest_boundary_basis"] == "rest_floor" \
        and pb["rest_boundary_by_place"] == BY_PLACE \
        and pb["rest_boundary_assumption"] == sb.ASSUMPTION


def _legs_case1() -> list[dict]:
    """Стыковка 300 вне базы (< 600), отдых ровно 600 вне базы, отдых
    ровно 720 дома, стыковка 120 вне базы: смены [F1 F2] [F3] [F4 F5]."""
    return [
        _leg("F1", HOME, "AAA", "2026-08-01T06:00:00", 120),   # 06:00–08:00
        _leg("F2", "AAA", "BBB", "2026-08-01T13:00:00", 120),  # +300 → 15:00
        _leg("F3", "BBB", HOME, "2026-08-02T01:00:00", 120),   # +600 → 03:00
        _leg("F4", HOME, "AAA", "2026-08-02T15:00:00", 120),   # +720 → 17:00
        _leg("F5", "AAA", HOME, "2026-08-02T19:00:00", 120),   # +120 → 21:00
    ]


def test_h0kw_границы_смен_и_интервалы_отдыха_одни_у_трёх_судей():
    """Без ручки: построитель (пол отдыха станции), проверка по ИДАК
    (пол по месту) и решатель (нормативные смены по полу отдыха по месту)
    режут линию времени одинаково — три смены, два интервала отдыха,
    нарушений нет; проверка называет упрощение словами."""
    legs = _legs_case1()
    want = {("F1", "F2"), ("F3",), ("F4", "F5")}
    duties, _chains, got = _builder(legs, None)
    assert got["catalog"]["coverage"]["verdict"] == "OK", got["catalog"]["coverage"]
    assert got["catalog"]["coverage"]["uncovered"] == 0
    assert duties == want, duties
    assert got["report"]["sit_window"]["mode"].startswith("по полу отдыха")
    assert got["report"]["sit_window"]["cap_base_min"] == R_HOME
    assert got["report"]["sit_window"]["cap_outstation_min"] == R_OUT
    assert _check_duties(legs, None) == want
    rows = _rows(legs)
    assert {tuple(r["flight"] for r in d)
            for d in solver_normative_duties(rows)} == want
    # интервалы отдыха: 2 у проверки (ok — разрыв не короче пола по
    # построению) и 2 у решателя (штрафа нет: T=0 без ручки)
    res = _rest_check(legs, None)
    assert res.verdict == "ok" and res.objects_seen == 2, res.detail
    assert res.violations == []
    assert any("модельное упрощение" in n for n in res.notes)
    assert any(sb.ASSUMPTION == n for n in res.notes)
    assert len(solver_normative_duties(rows)) - 1 == 2
    assert solver_rest_penalty(rows, 0) == 0
    assert solver_duty_span_overage(rows, 600) == 0


def test_h0kw_разрыв_короче_пола_дома_стыковка_а_не_отдых():
    """Независимое ожидание: разрыв 650 дома короче пола дома 720 —
    СТЫКОВКА (смена продолжается), не отдых и не «недоотдых»; ни у кого
    нет ложного нарушения отдыха. Что смена стала длиннее предела 600 —
    судит правило предела смены (обязательное правило, отдельно):
    решатель — размах нормативной смены сверх предела, построитель — такую
    смену не собирает (F3 из базы идёт отдельной цепочкой), проверка
    отдыха — интервалов отдыха 0, нарушений 0."""
    legs = [
        _leg("F1", HOME, "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", HOME, "2026-08-01T10:00:00", 120),   # → 12:00
        _leg("F3", HOME, "AAA", "2026-08-01T22:50:00", 120),   # +650 дома
        _leg("F4", "AAA", HOME, "2026-08-02T03:00:00", 120),
    ]
    res = _rest_check(legs, None)
    assert res.verdict == "ok" and res.objects_seen == 0, res.detail
    rows = _rows(legs)
    assert len(solver_normative_duties(rows)) == 1
    assert solver_rest_penalty(rows, 0) == 0
    assert solver_duty_span_overage(rows, 600) > 0
    duties, chains, _got = _builder(legs, None)
    assert not any("F2" in c and "F3" in c for c in chains), chains
    assert ("F1", "F2") in duties and ("F3", "F4") in duties


# ------------------------------------------------ случай 2: ручка 240

def test_ручка_max_sit_duration_240_T_у_всех_и_одни_границы():
    """Пакет с правилом максимальной стыковки: T = 240 у кнопки (derive),
    у проверок (показания / ведомость) и у построителя (--sit-max);
    линия времени со стыковкой 200 и отдыхом 720 дома режется одинаково."""
    readings = {"min_rest_home_min": R_HOME, "min_rest_outstation_min": R_OUT,
                sb.KNOB: 240}
    rules = {sb.KNOB: ["VN-030"]}
    got = sb.derive(readings, rules_of=rules)
    assert got["value"] == 240 and got["basis"] == "knob"
    assert got["assumption"] is None and got["by_place"] == BY_PLACE
    assert "правило пакета (max_sit_duration)" in got["source"] \
        and "VN-030" in got["source"]
    rb = scr.rest_boundary("roster", run_id="r1", applied={
        "runId": "r1", "lastRun": {"profileReadings": readings,
                                   "thresholds": {"applied": [
                                       {"threshold": sb.KNOB, "value": 240,
                                        "rules": ["VN-030"]}]}}})
    assert rb["rest_boundary_min"] == 240 and "VN-030" in rb["rest_boundary_source"]
    assert rb["rest_boundary_basis"] == "knob"
    pb = scr.rest_boundary("pairing", builder_input={
        "args": ["--sit-max", "240", "--rest-base", str(R_HOME),
                 "--rest-off", str(R_OUT)],
        "provenance": {"--sit-max": {"value": 240, "passed": True,
                                     "from": got["source"]}}})
    assert pb["rest_boundary_min"] == 240 and pb["rest_boundary_basis"] == "knob"
    legs = [
        _leg("F1", HOME, "AAA", "2026-08-01T06:00:00", 120),   # → 08:00
        _leg("F2", "AAA", HOME, "2026-08-01T11:20:00", 120),   # +200 → 13:20
        _leg("F3", HOME, "AAA", "2026-08-02T01:20:00", 120),   # +720 дома
        _leg("F4", "AAA", HOME, "2026-08-02T05:00:00", 120),   # +120
    ]
    want = {("F1", "F2"), ("F3", "F4")}
    duties, _chains, bgot = _builder(legs, 240)
    assert bgot["catalog"]["coverage"]["uncovered"] == 0
    assert duties == want, duties
    assert bgot["report"]["sit_window"]["cap_min"] == 240
    assert _check_duties(legs, 240) == want
    rows = _rows(legs)
    assert {tuple(r["flight"] for r in d)
            for d in solver_normative_duties(rows)} == want
    res = _rest_check(legs, 240)
    assert res.verdict == "ok" and res.objects_seen == 1, res.detail
    assert not any("упрощение" in n for n in res.notes)
    assert solver_rest_penalty(rows, 240) == 0


def test_ручка_240_разрыв_в_мёртвой_зоне_отвергают_все():
    """T = 240: разрыв 300 вне базы (240 ≤ 300 < 600) — у проверки
    нарушение отдыха, у решателя штраф «Rest between duty periods», у
    построителя смена кончилась без полного отдыха — участок после разрыва
    не в одной смене с предыдущим (стыковкой 300 быть не может)."""
    legs = [
        _leg("F1", HOME, "AAA", "2026-08-01T06:00:00", 120),   # → 08:00
        _leg("F2", "AAA", HOME, "2026-08-01T13:00:00", 120),   # +300
    ]
    res = _rest_check(legs, 240)
    assert res.verdict == "violated" and len(res.violations) == 1, res.detail
    rows = _rows(legs)
    assert solver_rest_penalty(rows, 240) == R_OUT - 300
    assert solver_rest_penalty(rows, 0) == 0          # без ручки — стыковка
    duties, _c, _got = _builder(legs, 240)
    assert ("F1", "F2") not in duties
    duties0, _c0, _g0 = _builder(legs, None)
    assert ("F1", "F2") in duties0


# ------------------------ независимые ожидания на ручных примерах (GPT-053)

#: (место отдыха, разрыв, ручка max_sit_duration | None) → ожидаемый смысл:
#: короче пола — стыковка; равен / не короче пола — отдых; длиннее max sit,
#: но короче пола — запрет при объявленном max sit и стыковка при
#: необъявленном. Ожидания записаны ДО прогона судей и не выводятся из них.
EXPECT = [
    ("outstation", 300, None, "стыковка"),
    ("outstation", 599, None, "стыковка"),
    ("outstation", 600, None, "отдых"),
    ("outstation", 900, None, "отдых"),
    ("home", 650, None, "стыковка"),
    ("home", 719, None, "стыковка"),
    ("home", 720, None, "отдых"),
    ("outstation", 200, 240, "стыковка"),
    ("outstation", 239, 240, "стыковка"),
    ("outstation", 240, 240, "запрет"),
    ("outstation", 300, 240, "запрет"),
    ("outstation", 599, 240, "запрет"),
    ("outstation", 600, 240, "отдых"),
    ("home", 650, 240, "запрет"),
    ("home", 720, 240, "отдых"),
]


def _legs_for(place: str, gap: int) -> list[dict]:
    """Вне базы: F1 BASE→AAA, разрыв в AAA, F2 AAA→BASE. Дома: F1/F2 туда
    и обратно (стыковка 60), разрыв в BASE, F3/F4 туда и обратно."""
    if place == "outstation":
        f1 = _leg("F1", HOME, "AAA", "2026-08-01T06:00:00", 120)
        t = "2026-08-01T08:00:00"
        return [f1, _leg("F2", "AAA", HOME, _shift(t, gap), 120)]
    return [
        _leg("F1", HOME, "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", HOME, "2026-08-01T09:00:00", 120),      # → 11:00
        _leg("F3", HOME, "AAA", _shift("2026-08-01T11:00:00", gap), 120),
        _leg("F4", "AAA", HOME, _shift("2026-08-01T11:00:00", gap + 180), 120),
    ]


def _shift(iso: str, minutes: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat()


def _classify_check(res) -> str:
    if res.verdict == "violated":
        return "запрет"
    return "отдых" if res.objects_seen >= 1 else "стыковка"


def _classify_solver(rows: list[dict], knob: int | None) -> str:
    if solver_rest_penalty(rows, knob or 0) > 0:
        return "запрет"
    return "отдых" if len(solver_normative_duties(rows)) >= 2 else "стыковка"


def _classify_builder(chains: list, a: str, b: str, duties: set) -> str:
    if any(a in d and b in d for d in duties):
        return "стыковка"
    if any(a in c and b in c for c in chains):
        return "отдых"
    return "запрет"


@pytest.mark.parametrize("place,gap,knob,expect", EXPECT)
def test_независимые_ожидания_ручных_примеров(place, gap, knob, expect):
    """Каждый судья отвечает на ручной пример независимо от других, и все
    ответы равны ожиданию, записанному заранее. Построитель судится на
    примерах вне базы (там продолжение цепочки принудительно: участок из
    AAA новой цепочки не начинает); дома F3 законно начинает новую цепочку
    из базы, и структура каталога стыковку от отдыха не отличает — там
    судят проверка и решатель."""
    legs = _legs_for(place, gap)
    assert _classify_check(_rest_check(legs, knob)) == expect
    assert _classify_solver(_rows(legs), knob) == expect
    if place == "outstation":
        duties, chains, _got = _builder(legs, knob, duty_span=900)
        assert _classify_builder(chains, "F1", "F2", duties) == expect


# ------------------------------------------------ случай 3: правил нет

def test_без_правил_значения_нет_у_всех():
    """Ни ручки, ни пола отдыха: источник границы отвечает «не
    определена» (кнопка останавливается этими словами —
    tests/test_button_sit_max.py), проверка по ИДАК — not_checkable с
    причиной, построитель без --sit-max и полов отдыха отказывает."""
    for readings in ({}, {sb.KNOB: 0}, {"max_duty_span_min": 600}):
        got = sb.derive(readings)
        assert got["value"] is None and got["basis"] is None
        assert sb.UNDEFINED in got["source"] and "360" not in got["source"]
    rb = scr.rest_boundary("roster", run_id="r1", applied={
        "runId": "r1", "lastRun": {"profileReadings": {sb.KNOB: 0}}})
    assert rb["rest_boundary_min"] is None and sb.UNDEFINED in rb["rest_boundary_source"]
    assert rb["rest_boundary_basis"] is None and rb["rest_boundary_assumption"] is None
    res = sc.run_checks([_rest_spec()], _roster(_legs_case1()), rest_boundary=rb)[0]
    assert res.verdict == "not_checkable" and sb.UNDEFINED in res.detail
    assert res.objects_seen == 0 and res.violations == []
    assert sb.derive_from_builder_args(["--duty-span", "600"])["value"] is None
    with pytest.raises(_pb.BuilderConfigError):
        _pb.build(_payload(_legs_case1()[:2]),
                  _args(sit_max=None, rest_base=None, rest_off=None))
