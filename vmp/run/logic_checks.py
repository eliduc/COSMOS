# -*- coding: utf-8 -*-
"""Детерминированные проверки сертификата для правил с блоком `logic`
по ФОРМАМ решателя — с историей сотрудника (ревизия 1.17; решение
владельца 05.09.2026 «нужно учитывать»).

Класс ошибки, который это закрывает: проверки, скомпилированные
моделью из текста правила, историю сотрудника не видят — серия ночных,
начатая до горизонта, окно отдыха, открытое концом последней смены,
и отработанные до среза сутки месяца для них не существуют. Ложных
нарушений это не даёт, но перенесённое из истории нарушение сертификат
пропустил бы, а сертификат — доказательство, не показание.

Форма правила берётся из показаний решателя того же прогона
(lastRun.logic, корзина generated, план порождённого ограничения: форма,
граница, окно, область). Показания ступени 3 сверяет гейт
(webapp.logic_gate); здесь по той же форме нарушения пересчитываются
НЕЗАВИСИМО — по листам решения «Назначения», «Смены» и «История», то
же прочтение, что у решателя (kotlin LogicForms) и оракула
(webapp.logic_oracle). Правило, взятое кодом, компилятору-модели не
подаётся. Без показаний решателя (нет lastRun.logic) правило остаётся
компилятору, как прежде.

Единая версия 06.09.2026 (план §3.8, §3.10.3; строки C-25..C-34):

  * каждая проверка окон несёт адресное покрытие `window_coverage`
    {expected = evaluated + unknown, windows[] с сотрудником, границами
    [start_inclusive, end_exclusive), статусом evaluated|unknown,
    причиной и исходом pass|violation}; неизвестное окно — не молчание
    и не положительный итог: при unknown > 0 проверка получает третий
    статус «частично прогнана» (когда контракт отчёта данных его знает,
    см. partial_status), а правило считается покрытым частично;
  * горизонт проверок объявлен пакетом (`window_horizon`, полные сутки,
    [start_inclusive, end_exclusive)); без объявленного горизонта
    (пакеты до раздела manifest.horizon) он выводится по назначениям и
    так и называется (source = inferred, частичная диагностика — не
    pass: причина в проверке, правило покрыто частично, C-27b);
    объявленный, но нечитаемый горизонт — «не прогнана» с причиной;
    форма без единого окна в горизонте — «не применима» (правило
    покрыто, в прогнанные проверка не входит, C-31);
  * универсум сотрудников — все люди листов «Назначения», «Смены»,
    «История», «Календарь истории» и объявленного состава
    `window_employees`, включая незадействованных; сотрудник без
    однозначной базы при области правила — окно unknown с причиной;
  * отдых в скользящем окне доказывается точным непрерывным покрытием
    всех моментов окончания окна: область окончаний разбивается на
    конечные критические ячейки (точки и открытые интервалы) по
    объединению доказанных отдыхов и известной занятости
    (granularity = continuous_critical_cells); занятость доказывается
    нормативными сменами листа «Смены», проекция назначений — только
    нижняя граница (доказанное нарушение сохраняется, pass не
    доказывается); явный выходной календаря истории — доказанный отдых;
    суточная форма main (старты по календарным суткам, cells/1) остаётся
    legacy-диагностикой daily_diagnostic — не статус и не доказательство
    (план §3.10.3 K-15, C-30);
  * счётные окна (выходные, ночные) — один движок: месяцы, кончающиеся
    в горизонте, либо суточные окна с окончанием в каждом дне
    горизонта; сутки до горизонта — по календарю истории, точный
    агрегат «Рабочих дней с начала месяца» при срезе = начало горизонта
    закрывает префикс месяца, противоречие с календарём → unknown;
  * пропавший перенос серии (ночных, рабочих дней) — неизвестность, не
    ноль; известное нарушение сохраняется; пропущенный порог плана —
    «не прогнана», не ноль;
  * политика раздела history 1.17.2 (main): continue_zero /
    assume_zero_when_missing — сотрудник БЕЗ записи в истории судится по
    явно принятому допущению нулевых остатков (до горизонта свободен,
    серии и префиксы нулевые), допущение названо в проверке
    (windows_assumed_employees / windows_assumed_basis); запись с
    пустым остатком — неизвестно при любом решении.
"""
from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta, timezone

from vmp.run.fit_checks import (SOLUTION_SID, SEVERITY, _Counter, _finding,
                                _history_by_employee, _sheet)

COMPILED_BY = "код (форма решателя из показаний lastRun.logic)"

FORM_NIGHT = "CONSECUTIVE_NIGHT_DUTIES_MAX"
FORM_ROLLING = "REST_IN_ROLLING_WINDOW"
FORM_DAYS_OFF = "DAYS_OFF_PER_MONTH_MIN"
FORM_CONSECUTIVE = "CONSECUTIVE_DAYS_MAX"
FORM_DAYS_OFF_ROLLING = "DAYS_OFF_IN_ROLLING_WINDOW_MIN"
FORM_NIGHT_ROLLING = "NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX"
FORM_DUTY_SERIES = "DUTY_SERIES_LIMIT"
FORMS = (FORM_NIGHT, FORM_ROLLING, FORM_DAYS_OFF, FORM_CONSECUTIVE,
         FORM_DAYS_OFF_ROLLING, FORM_NIGHT_ROLLING, FORM_DUTY_SERIES)

EMP = "Табельный номер"
BASE = "База приписки"

#: Третий статус проверки окон (план §3.8): проверка с неизвестными
#: обязательными окнами. Выдаётся, когда контракт отчёта данных
#: (cdpm.data_report.CHECK_STATUSES) его знает; до расширения контракта
#: проверка остаётся «прогнана» с тем же адресным window_coverage и
#: производными полями (windows_unjudged) — правило покрыто частично,
#: сертификат допустимости не выдаётся (vmp.run.verdicts).
PARTIAL_RUN_STATUS = "частично прогнана"
#: Статус проверки без единого окна в горизонте (форма не применима).
NOT_APPLICABLE_STATUS = "не применима"
_PARTIAL_STATUS_CACHE: list[str] = []

#: Источник горизонта проверок, выведенного по назначениям решения
#: (пакет без объявленного window_horizon) — частичная диагностика.
HORIZON_INFERRED = "inferred"
#: Причина в проверке при выведенном горизонте (C-27b): не pass —
#: сертификат относит правило к покрытым частично.
HORIZON_INFERRED_REASON = ("горизонт выведен по назначениям (объявленного "
                           "window_horizon нет) — частичная диагностика, не pass")
#: Сколько несудимых окон называется в производном поле unjudged_sample
#: (полный адресный перечень — window_coverage.windows).
_UNJUDGED_SAMPLE = 20


def partial_status() -> str:
    """Статус проверки с неизвестными окнами по действующему контракту
    отчёта данных: «частично прогнана», если словарь статусов его
    содержит, иначе «прогнана» (окна остаются в window_coverage)."""
    if not _PARTIAL_STATUS_CACHE:
        try:
            from cdpm import data_report as _dr
            known = PARTIAL_RUN_STATUS in tuple(getattr(_dr, "CHECK_STATUSES", ()))
        except Exception:                                # noqa: BLE001
            known = False
        _PARTIAL_STATUS_CACHE.append(PARTIAL_RUN_STATUS if known else "прогнана")
    return _PARTIAL_STATUS_CACHE[0]


def plans_by_rule(logic_report) -> dict[str, dict]:
    """Планы порождённых ограничений по идентификатору правила (только
    формы с исполнителем здесь)."""
    out: dict[str, dict] = {}
    if not isinstance(logic_report, dict):
        return out
    for e in logic_report.get("generated") or []:
        if not isinstance(e, dict):
            continue
        plan = e.get("plan")
        if not isinstance(plan, dict) or plan.get("form") not in FORMS:
            continue
        for rid in list(plan.get("rules") or []) + [e.get("rule")]:
            if rid is not None:
                out.setdefault(str(rid), plan)
    return out


#: Семьи базы шаблонов (путь А, 05.09.2026) → формы: посадка правила на
#: семью даёт ту же форму, что и план решателя, — сертификат судит по
#: посадке и без показаний решателя.
FAMILY_FORMS = {"night_series": FORM_NIGHT, "rolling_rest": FORM_ROLLING,
                "days_off_floor": FORM_DAYS_OFF,
                "duty_series_limit": FORM_DUTY_SERIES}


def _check(rid: str, rule: dict, template: str, unit: str, statement: str,
           params: dict, plan: dict | None = None) -> dict:
    plan = plan or {}
    return {"id": f"C-{rid}-{plan.get('check_suffix') or 'форма'}",
            "basis": {"rule": rid},
            "template": template, "params": params,
            "statement": statement, "unit": unit, "severity": SEVERITY,
            "compiled_by": plan.get("compiled_by") or COMPILED_BY}


def _base_scope_of(rule: dict):
    scope = (rule.get("logic") or {}).get("scope") if isinstance(rule.get("logic"), dict) else None
    s = str(scope or "").strip().upper()
    return s if 2 <= len(s) <= 4 and s.isalnum() else None


def plan_from_fit(family: str, rule: dict, base_tz_offset_min: int = 0) -> dict | None:
    """План формы из ПОСАДКИ правила (категории A/B семьи истории):
    границы — из параметров посадки (единицы словаря CDPM: count,
    time_of_day_minutes местного времени базы, duration_minutes). Окно
    ночи переводится в шкалу меток тем же сдвигом, что у решателя
    (местное время базы − base_tz_offset_min). Параметра не хватает —
    None (правило остаётся показаниям решателя либо компилятору)."""
    from vmp.run.fit_checks import COMPILED_BY as FIT_COMPILED_BY, fit_param
    form = FAMILY_FORMS.get(family)
    if form is None:
        return None
    base = {"form": form, "rules": [str(rule.get("id"))],
            "baseScope": _base_scope_of(rule),
            "compiled_by": FIT_COMPILED_BY, "check_suffix": "посадка"}
    if form == FORM_NIGHT:
        a = fit_param(rule, "night_duty_start")
        b = fit_param(rule, "night_duty_end")
        if a is None or b is None:
            return None
        shift = int(base_tz_offset_min or 0)
        night = dict(nightFrom=(int(a) - shift) % 1440, nightTo=(int(b) - shift) % 1440)
        # Второй набор параметров семьи — счётчик скользящего окна.
        k = fit_param(rule, "max_night_duties_in_window")
        wd = fit_param(rule, "night_window_days")
        if k is not None and wd is not None and k >= 0 and wd > 0:
            base.update(form=FORM_NIGHT_ROLLING, bound=int(k), condBound=int(wd), **night)
            return base
        n = fit_param(rule, "max_consecutive_night_duties")
        if n is None or n < 0:
            return None
        base.update(bound=int(n), **night)
        return base
    if form == FORM_ROLLING:
        w = fit_param(rule, "weekly_rest_required")
        p = fit_param(rule, "weekly_rest_period")
        if w is None or p is None or w <= 0 or p <= 0:
            return None
        p = int(p)
        days = p // 1440 if p >= 1440 and p % 1440 == 0 else (p if p < 100 else None)
        if not days:
            return None
        base.update(bound=int(w), condBound=int(days))
        return base
    if form == FORM_DUTY_SERIES:
        # Две границы посадки: длина серии (condBound) и свободные сутки
        # после неё (bound) — та же раскладка, что у плана решателя.
        n = fit_param(rule, "max_duty_series_length")
        m = fit_param(rule, "post_series_days_off")
        if n is None or m is None or n <= 0 or m < 0:
            return None
        base.update(bound=int(m), condBound=int(n))
        return base
    k = fit_param(rule, "min_days_off_in_window")
    wd = fit_param(rule, "days_off_window_days")
    if k is not None and wd is not None and k >= 0 and wd > 0:
        base.update(form=FORM_DAYS_OFF_ROLLING, bound=int(k), condBound=int(wd))
        return base
    n = fit_param(rule, "min_days_off")
    if n is None or n < 0:
        return None
    base.update(bound=int(n))
    return base


def run_plan(rid: str, rule: dict, plan: dict, dataset: dict, counter):
    """Исполнитель формы плана — (проверка, находки)."""
    return _EXECUTORS[plan["form"]](rid, rule, plan, dataset, counter)


# ---------------------------------------------------------------- чтение

def _moment(value):
    """Момент времени без усечения секунд и срезов истории (C-25): ISO
    (с секундами, Z либо смещением → UTC без пояса; голая дата —
    полночь) и формы книг «дд.мм.гггг [ЧЧ:ММ[:СС]]» — те же формы, что
    у cdpm.data_checks.parse_moment, плюс секунды и пояс. Нечитаемое —
    None (не ноль и не «сегодня»)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            result = None
            for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
                try:
                    result = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    pass
            if result is None:
                return None
    return result.astimezone(timezone.utc).replace(tzinfo=None) if result.tzinfo else result


def _num(v):
    """Число либо None: bool, inf, nan и пустое — не числа (C-25)."""
    try:
        n = None if v is None or isinstance(v, bool) or str(v).strip() == "" else float(v)
        return n if n is not None and math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _whole(v):
    """Целое ≥ 0 либо None — порог плана / перенос серии; пропуск — не
    ноль (C-25)."""
    n = _num(v)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def _in_scope(row: dict, scope) -> bool:
    return not scope or str(row.get(BASE) or "").strip().upper() == str(scope).upper()


def _rows_by_emp(rows: list[dict], scope) -> dict[str, list[tuple[int, dict]]]:
    out: dict[str, list[tuple[int, dict]]] = {}
    for i, r in enumerate(rows, start=2):
        if _in_scope(r, scope):
            out.setdefault(str(r.get(EMP) or "").strip(), []).append((i, r))
    return out


def _need(check: dict, got, sheet: str, need: tuple[str, ...]):
    """(rows, columns) либо None с честным «не прогнана»."""
    if got is None:
        check.update(status="не прогнана", rows_checked=0,
                     reason=f"в решении нет листа «{sheet}»")
        return None
    rows, columns = got
    missing = [c for c in need if c not in columns]
    if missing:
        check.update(status="не прогнана", rows_checked=0,
                     reason=f"у листа «{sheet}» нет колонок: " + ", ".join(missing))
        return None
    return rows, columns


def _as_of_date(h: dict | None):
    if not h:
        return None
    m = _moment(h.get("Срез"))
    return m.date() if m is not None else None


def _in_night(minute_of_day: int, nf: int, nt: int) -> bool:
    return (nf <= minute_of_day < nt) if nf <= nt \
        else (minute_of_day >= nf or minute_of_day < nt)


# ---------------------------------------------------------------- политика истории

def _zero_policy(dataset: dict) -> dict:
    """Политика окон через границу горизонта (раздел history 1.17.2,
    семантика согласована с CDPM 05.09.2026): continue_zero /
    assume_zero_when_missing → сотрудник БЕЗ записи в истории судится по
    явно принятому допущению нулевых остатков; continue_partial либо
    решения нет → его окна через границу неизвестны (адресно)."""
    policy = dataset.get("history_policy") if isinstance(dataset.get("history_policy"), dict) else {}
    decision = str(((policy.get("user_decision") or {}).get("decision")) or "")
    zero_assumed = bool(policy.get("assume_zero_when_missing")) or decision == "continue_zero"
    basis = ("решение пользователя %s: continue_zero" % (
        (policy.get("user_decision") or {}).get("question") or "по вопросу истории")
        if decision == "continue_zero" else "признак производителя assume_zero_when_missing")
    as_of_m = _moment(policy.get("as_of")) if policy else None
    history_sheet = _sheet(dataset, "История")
    return {"zero_assumed": zero_assumed, "basis": basis, "decision": decision,
            "as_of": as_of_m.date() if as_of_m is not None else None,
            "passed": history_sheet is not None or bool(policy)}


def _missing_record_reason(policy: dict) -> str:
    """Причина неизвестности у сотрудника без записи в истории — словами
    main: история не передана / нет записи (решение пользователя)."""
    if not policy.get("passed"):
        return "история не передана"
    d = policy.get("decision")
    return ("нет записи сотрудника в истории (решение пользователя: %s)" % d if d
            else "нет записи сотрудника в истории (решения по истории нет)")


def _assumed_zero(policy: dict, lo: date, h) -> bool:
    """Допущение нулевых остатков применимо: сотрудник без записи,
    политика continue_zero, срез политики (если назван) = начало
    горизонта."""
    return (h is None and policy["zero_assumed"]
            and (policy["as_of"] is None or policy["as_of"] == lo))


# ---------------------------------------------------------------- покрытие окон

#: Виды покрытия окон контракта отчёта данных 1.18.3 (CDPM
#: data_report.COVERAGE_GRANULARITIES, spec-postanovka-cosmos-v1 §1.18.3):
#: адресные окна либо критические ячейки области окончаний (K-15).
COVERAGE_WINDOWS = "windows"
COVERAGE_CONTINUOUS = "continuous_critical_cells"


def _coverage(check: dict, window_kind: str = "day"):
    """Пустое покрытие окон проверки. `granularity` и `expected_unit` —
    строго словарь контракта 1.18.3 (гейт сертификата CDPM отвергает
    другие слова); вид окна исполнителя (day — календарные сутки,
    duty_sequence — серия смен, employee_rest_sequence — отдых между
    сменами сотрудника, calendar_month — календарный месяц) — отдельным
    полем window_kind, чтобы адрес окна читался по смыслу."""
    continuous = window_kind == COVERAGE_CONTINUOUS
    check["window_coverage"] = {
        "expected": 0, "evaluated": 0, "unknown": 0, "windows": [],
        "granularity": COVERAGE_CONTINUOUS if continuous else COVERAGE_WINDOWS,
        "expected_unit": "critical_cells" if continuous else "windows",
        "window_kind": window_kind,
        "boundary": "[start_inclusive,end_exclusive)",
    }


def _window(check, emp, start, end, reason="", outcome=None, assumed_basis=None):
    c = check["window_coverage"]
    entry = {"employee": emp or "(не указан)",
             "start_inclusive": start if isinstance(start, str) else start.isoformat(),
             "end_exclusive": end if isinstance(end, str) else end.isoformat(),
             "status": "unknown" if reason else "evaluated",
             "reason": reason or "все необходимые данные окна известны"}
    if outcome is not None:
        entry["outcome"] = outcome
    if assumed_basis and not reason:
        # Окно судилось по явно принятому допущению нулевых остатков
        # (continue_zero) — это названо, а не спрятано в «известно».
        entry["assumed"] = True
        entry["assumed_basis"] = assumed_basis
        entry["reason"] = ("до горизонта сотрудник свободен по допущению нулевых "
                           "остатков (%s)" % assumed_basis)
    c["windows"].append(entry)
    c["expected"] += 1
    c["unknown" if reason else "evaluated"] += 1
    return entry


def _finish_windows(check, rows_checked):
    """Статус и производные поля проверки окон: «не применима» без окон,
    «частично прогнана» (partial_status) при неизвестных окнах, иначе
    «прогнана». Поля main (windows_judged / windows_violated /
    windows_unjudged, unjudged_sample, unjudged_by_reason) — производные
    от window_coverage, чтобы прежние читатели (сверка с ИДАК, панель)
    видели те же три исхода."""
    c = check["window_coverage"]
    # C-27b: горизонт, выведенный по назначениям (пакет без раздела
    # manifest.horizon), — частичная диагностика, не pass; причина
    # называется в самой проверке, а сертификат относит правило к
    # покрытым частично (roster_certify.certify).
    inferred_reason = (HORIZON_INFERRED_REASON if check.get("horizon_inferred") else "")
    if not c["expected"]:
        check.update(status=NOT_APPLICABLE_STATUS, rows_checked=0,
                     reason=("в выведенном горизонте нет окон этой формы; "
                             + inferred_reason if inferred_reason
                             else "в объявленном горизонте нет окон этой формы"))
        return check
    # rows_checked — строки листа, честным числом: у стенда без строк
    # (только состав и история) — 0; окна адресованы в window_coverage
    # (expected/evaluated/unknown), и контракт отчёта данных CDPM
    # (§1.18.3) допускает rows_checked = 0 при адресных окнах — прежняя
    # подмена нулём числа окон («чтобы «прогнана» не была пустой») снята
    # при интеграции C+P 06.09.2026 (тест GPT
    # test_full_window_proof_with_no_assignment_rows_is_a_valid_report).
    check.update(status=partial_status() if c["unknown"] else "прогнана",
                 rows_checked=int(rows_checked or 0))
    if inferred_reason:
        check["reason"] = inferred_reason
    unknown = [w for w in c["windows"] if w["status"] == "unknown"]
    violated = sum(1 for w in c["windows"] if w.get("outcome") == "violation")
    check["windows_judged"] = int(c["evaluated"])
    check["windows_violated"] = int(violated)
    check["windows_unjudged"] = len(unknown)
    assumed = sorted({w["employee"] for w in c["windows"] if w.get("assumed")})
    if assumed:
        check["windows_assumed_employees"] = len(assumed)
        check["windows_assumed_basis"] = next(
            w["assumed_basis"] for w in c["windows"] if w.get("assumed"))
        check["note"] = ((check.get("note") + "; ") if check.get("note") else "") + (
            "у %d сотрудников без записи в истории окна через границу судились по "
            "допущению нулевых остатков (%s)" % (len(assumed), check["windows_assumed_basis"]))
    if unknown:
        check["reason"] = (f"неизвестных обязательных окон: {c['unknown']} из {c['expected']}"
                           + (f"; {inferred_reason}" if inferred_reason else ""))
        check["unjudged_windows"] = len(unknown)
        check["unjudged_sample"] = [
            {"employee": w["employee"], "window_from": w["start_inclusive"],
             "window_to": w["end_exclusive"], "reason": w["reason"]}
            for w in unknown[:_UNJUDGED_SAMPLE]]
        reasons: dict[str, int] = {}
        for w in unknown:
            reasons[w["reason"]] = reasons.get(w["reason"], 0) + 1
        check["unjudged_by_reason"] = reasons
        check["note"] = ((check.get("note") + "; ") if check.get("note") else "") + (
            "окон не судилось: %d (%s); судилось %d, из них нарушено %d — "
            "несудимое окно не считается выдержанным"
            % (len(unknown), ", ".join("%s: %d" % kv for kv in sorted(reasons.items())),
               c["evaluated"], violated))
    return check


def _inferred_horizon(dataset: dict, rows: list[dict], fields: tuple[str, str]):
    """Горизонт по назначениям решения (C-27b, вариант (б)): от суток
    первого начала до суток последнего конца включительно — по листу
    проверки и листу «Назначения»; нечитаемых строк — нет горизонта."""
    lo = hi = None
    seen = False
    sources = [rows]
    other = _sheet(dataset, "Назначения")
    if other is not None:
        sources.append(other[0])
    for src, (fa, fb) in zip(sources, (fields, ("Время вылета", "Время прилёта"))):
        for row in src:
            a, b = _moment(row.get(fa)), _moment(row.get(fb))
            if a is None or b is None:
                continue
            seen = True
            lo = a if lo is None or a < lo else lo
            hi = b if hi is None or b > hi else hi
    if not seen:
        return None
    return lo.date(), hi.date() + timedelta(days=1)


def _horizon_is_inferred(horizon: dict) -> bool:
    """Переданный объект window_horizon несёт выведенное происхождение:
    явный признак inferred=True либо строка source, содержащая
    «inferred» (C-27b-R1)."""
    if horizon.get("inferred") is True:
        return True
    src = horizon.get("source")
    return isinstance(src, str) and HORIZON_INFERRED in src.lower()


def _window_context(check, dataset, rows, scope, fields=("Время вылета", "Время прилёта")):
    """Горизонт и универсум проверки окон.

    Горизонт объявлен пакетом (`window_horizon` с start_inclusive /
    end_exclusive в полных сутках, start < end); объявленный, но
    нечитаемый — «не прогнана» с причиной; не объявленный вовсе
    (пакеты до раздела manifest.horizon) — выводится по назначениям
    решения и назван source = inferred (частичная диагностика).
    Универсум — сотрудники листов «Назначения», «Смены», «История»,
    «Календарь истории» и объявленного состава window_employees (в том
    числе без назначений); база — из любого листа; при области правила
    сотрудник с неоднозначной либо неизвестной базой — окно unknown с
    причиной, строка без табельного — unknown."""
    horizon = dataset.get("window_horizon")
    if isinstance(horizon, dict):
        a = _moment(horizon.get("start_inclusive"))
        b = _moment(horizon.get("end_exclusive"))
        if (a is None or b is None or a.time() != datetime.min.time()
                or b.time() != datetime.min.time() or a >= b):
            check.update(status="не прогнана", rows_checked=0,
                         reason="нет корректного объявленного горизонта window_horizon "
                                "[start_inclusive, end_exclusive) в полных сутках")
            return None
        lo, end = a.date(), b.date()
        # C-27b-R1 (ревью GPT-029): объявленным считается ТОЛЬКО горизонт,
        # чьё происхождение не «выведено»: переданный объект с source,
        # содержащим «inferred», либо с явным признаком inferred=True —
        # та же частичная диагностика, что и при отсутствии объекта;
        # молча повышать такой горизонт до объявленного нельзя.
        if _horizon_is_inferred(horizon):
            check["window_coverage"]["horizon"] = {
                "start_inclusive": lo.isoformat(), "end_exclusive": end.isoformat(),
                "source": HORIZON_INFERRED,
                "basis": ("переданный window_horizon помечен как выведенный "
                          f"(source = {horizon.get('source')!r}) — частичная диагностика")}
            check["horizon_inferred"] = True
        else:
            check["window_coverage"]["horizon"] = {
                "start_inclusive": lo.isoformat(), "end_exclusive": end.isoformat(),
                "source": horizon.get("source") or "объявленный горизонт прогона"}
    else:
        inferred = _inferred_horizon(dataset, rows, fields)
        if inferred is None:
            check.update(status="не прогнана", rows_checked=0,
                         reason="нет объявленного горизонта window_horizon и назначений с "
                                "читаемыми временами — горизонт не определён")
            return None
        lo, end = inferred
        check["window_coverage"]["horizon"] = {
            "start_inclusive": lo.isoformat(), "end_exclusive": end.isoformat(),
            "source": HORIZON_INFERRED,
            "basis": "горизонт выведен по назначениям решения (объявленного "
                     "window_horizon нет) — частичная диагностика"}
        check["horizon_inferred"] = True
    bases: dict[str, set[str]] = {}
    for name in ("Назначения", "Смены", "История", "Календарь истории"):
        got = _sheet(dataset, name)
        for row in got[0] if got else []:
            emp = str(row.get(EMP) or "").strip()
            base = str(row.get(BASE) or "").strip().upper()
            bases.setdefault(emp, set()).update([base] if base else [])
    for row in dataset.get("window_employees") or []:
        if isinstance(row, dict):
            emp = str(row.get("employee") or "").strip()
            base = str(row.get("base") or "").strip().upper()
            bases.setdefault(emp, set()).update([base] if base else [])
    grouped = _rows_by_emp(rows, None)
    by_emp, unknown_scope = {}, {}
    for emp in sorted(bases):
        bs = bases[emp]
        if scope and len(bs) == 1 and str(scope).upper() not in bs:
            continue
        by_emp[emp] = grouped.get(emp, [])
        if not emp:
            unknown_scope[emp] = "у строки нет табельного номера сотрудника"
        elif scope and (len(bs) != 1):
            unknown_scope[emp] = "база приписки не установлена однозначно для области правила"
    if not by_emp:
        check.update(status="не прогнана", rows_checked=0,
                     reason="не установлен ни один сотрудник в области проверки")
        return None
    return lo, end, by_emp, unknown_scope


def _daily_windows(lo, end, days):
    """По окну на каждые сутки горизонта (окно кончается этими сутками);
    первые окна неизбежно пересекают историю. Это суточная сетка
    счётчика, а не утверждение обо всех непрерывных стартах."""
    day = lo
    while day < end:
        w_end = day + timedelta(days=1)
        yield w_end - timedelta(days=days), w_end
        day = w_end


def _history_missing_reason(days):
    return "нет достоверных посуточных данных истории: " + ", ".join(d.isoformat() for d in days)


# ---------------------------------------------------------------- исполнители

def _night_series(rid, rule, plan, dataset, counter):
    """НОЧНЫХ СМЕН ПОДРЯД НЕ БОЛЕЕ N — по листу «Смены» (ночная — начало
    смены в окне минут суток [от, до) шкалы меток); серия из истории
    («Ночных смен подряд») продолжается первой сменой горизонта, если та
    ночная — только при срезе истории = начало горизонта; без известной
    серии окно сотрудника неизвестно (не ноль), известное нарушение
    сохраняется; без записи при continue_zero — перенос 0 по допущению."""
    bound = _whole(plan.get("bound"))
    nf, nt = _whole(plan.get("nightFrom")), _whole(plan.get("nightTo"))
    check = _check(rid, rule, "серия-ночных-смен-подряд", "смена",
                   f"ночных смен подряд не более {bound} (ночная — начало смены в "
                   f"окне [{nf}, {nt}) минут суток; серия из истории продолжается "
                   f"первой сменой горизонта) — {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Смены", "bound": bound,
                    "night_from": nf, "night_to": nt, "base_scope": plan.get("baseScope")}, plan)
    _coverage(check, "duty_sequence")
    if bound is None or nf is None or nt is None or nf >= 1440 or nt >= 1440 or nf == nt:
        check.update(status="не прогнана", rows_checked=0,
                     reason="план решателя не несёт порога либо окна ночи (nightFrom/nightTo)")
        return check, []
    got = _need(check, _sheet(dataset, "Смены"), "Смены", (EMP, "Начало"))
    if got is None:
        return check, []
    rows, _cols = got
    history = _history_by_employee(dataset)
    policy = _zero_policy(dataset)
    found: list[dict] = []
    carried_emps = 0
    context = _window_context(check, dataset, rows, plan.get("baseScope"), ("Начало", "Конец"))
    if context is None:
        return check, []
    lo, end, by_emp, unknown_scope = context
    for emp, lst in by_emp.items():
        reason = unknown_scope.get(emp, "")
        if reason:
            _window(check, emp, lo, end, reason)
            continue
        lst.sort(key=lambda x: str(x[1].get("Начало") or ""))

        def night(row) -> bool | None:
            m = _moment(row.get("Начало"))
            return None if m is None or not lo <= m.date() < end else _in_night(m.hour * 60 + m.minute, int(nf), int(nt))
        carry = 0
        assumed_basis = None
        h = history.get(emp)
        if lst and night(lst[0][1]):
            value = _whole(h.get("Ночных смен подряд")) if h else None
            if _assumed_zero(policy, lo, h):
                assumed_basis = policy["basis"]
            elif value is None or _moment(h.get("Срез")) != datetime.combine(lo, datetime.min.time()):
                reason = ("нет известной серии ночных смен на начало объявленного горизонта"
                          + ("" if h else " (" + _missing_record_reason(policy) + ")"))
            else:
                carry = value
            if carry:
                carried_emps += 1
        if any(night(row) is None for _, row in lst):
            reason = "у смены нечитаемое начало либо начало вне объявленного горизонта"
        finding_start = len(found)
        run, start_row, run_carry = 0, None, 0

        def flush():
            if run > bound:
                found.append(_finding(
                    counter, check, "Смены", start_row,
                    f"ночных смен подряд {run} больше предела {bound}"
                    + (f" (из них {run_carry} из истории)" if run_carry else ""),
                    f"{SOLUTION_SID}!Смены: Табельный номер=«{emp}», серия ночных смен "
                    f"со строки {start_row}"
                    + (f"; История: Ночных смен подряд=«{run_carry}»" if run_carry else ""),
                    rid, "Начало"))
        for i, row in lst:
            n = night(row)
            if n:
                if run == 0:
                    start_row = i
                    run_carry = carry
                    run = carry
                    carry = 0
                run += 1
            else:
                flush()
                run, start_row, run_carry, carry = 0, None, 0, 0
        flush()
        _window(check, emp, lo, end, reason,
                "violation" if len(found) > finding_start else (None if reason else "pass"),
                assumed_basis)
    if history:
        check["note"] = (f"история передана: перенос серии ночных применён у "
                         f"{carried_emps} сотрудников (первая смена горизонта ночная и "
                         f"серия в истории не пуста)")
    return _finish_windows(check, len(rows)), found


def _intervals(rows_of_emp: list[tuple[int, dict]], fields=("Время вылета", "Время прилёта")):
    """Интервалы строк сотрудника по паре колонок; нечитаемый либо
    неположительный (конец ≤ начало) интервал — None целиком (C-29)."""
    ivs = []
    for i, r in rows_of_emp:
        a, b = _moment(r.get(fields[0])), _moment(r.get(fields[1]))
        if a is None or b is None or b <= a:
            return None
        ivs.append((a, b, i))
    ivs.sort(key=lambda x: (x[0], x[1]))
    return ivs


def _normative_rest_intervals(dataset, emp, assignments):
    """Занятость по НОРМАТИВНЫМ сменам (C-29): полный лист «Смены» с
    идентификаторами и временами, каждое назначение покрыто своей сменой
    по идентификатору и времени — иначе основание занятости неполно
    («проекция назначений неполна»), и промежутки между рейсами отдых не
    доказывают."""
    got = _sheet(dataset, "Смены")
    if got is None:
        return None, "нет листа «Смены»; интервалы назначений не доказывают нормативный отдых"
    rows, columns = got
    if any(c not in columns for c in (EMP, "Идентификатор", "Начало", "Конец")):
        return None, "нет полной разметки нормативных смен с идентификаторами и временами"
    duty_rows = [(i, row) for i, row in enumerate(rows, 2) if str(row.get(EMP) or "").strip() == emp]
    intervals = _intervals(duty_rows, ("Начало", "Конец"))
    if intervals is None:
        return None, "нормативная смена имеет нечитаемый либо неположительный интервал"
    by_id = {}
    for i, row in duty_rows:
        duty_id = str(row.get("Идентификатор") or "").strip()
        value = (_moment(row.get("Начало")), _moment(row.get("Конец")), i)
        if not duty_id or (duty_id in by_id and by_id[duty_id][:2] != value[:2]):
            return None, "идентификатор нормативной смены отсутствует либо неоднозначен"
        by_id[duty_id] = value
    for _i, row in assignments:
        duty = by_id.get(str(row.get("Смена") or "").strip())
        a, b = _moment(row.get("Время вылета")), _moment(row.get("Время прилёта"))
        if duty is None or a is None or b is None or not duty[0] <= a < b <= duty[1]:
            return None, "назначение не покрыто своей нормативной сменой по идентификатору и времени"
    return intervals, ""


def _rolling_rest(rid, rule, plan, dataset, counter):
    """В КАЖДЫЕ N СУТОК НЕПРЕРЫВНЫЙ ОТДЫХ НЕ МЕНЕЕ W — точное непрерывное
    покрытие всех моментов окончания окна в горизонте (C-30, план
    §3.10.3 K-15): область окончаний [начало, конец горизонта]
    разбивается на критические ячейки (точки и открытые интервалы) по
    объединению доказанных отдыхов [a+R, b+W−R] и известной занятости
    [k+W, l]. Достаточный известный отдых доказывает pass и при частично
    неизвестном окне; нарушение требует полностью известной занятости
    окна (нормативные смены) — по проекции назначений нарушение
    доказывается как нижняя граница, pass — нет. Конец последней смены
    истории (при срезе = начало горизонта) открывает известный отдых
    только ПОСЛЕ него; явный выходной календаря истории до горизонта —
    доказанный отдых (с защитой от противоречий). Сотрудник без записи
    при continue_zero — свободен до горизонта по названному допущению."""
    days = _whole(plan.get("condBound"))
    rest_min = _whole(plan.get("bound"))
    check = _check(rid, rule, "отдых-в-скользящем-окне", "окно",
                   f"в каждые {days} суток непрерывный отдых не менее {rest_min} мин "
                   f"(все непрерывные моменты окончания, включая границу истории) — "
                   f"{str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Смены", "window_days": days,
                    "rest_min": rest_min, "base_scope": plan.get("baseScope")}, plan)
    _coverage(check, "continuous_critical_cells")
    check["window_coverage"]["expected_unit"] = "critical_cells"
    if not days or not rest_min:
        check.update(status="не прогнана", rows_checked=0,
                     reason="план решателя не несёт длины окна либо порога отдыха")
        return check, []
    got = _need(check, _sheet(dataset, "Назначения"), "Назначения",
                (EMP, "Время вылета", "Время прилёта"))
    if got is None:
        return check, []
    rows, _cols = got
    context = _window_context(check, dataset, rows, plan.get("baseScope"))
    if context is None:
        return check, []
    lo, end, by_emp, unknown_scope = context
    horizon_start = datetime.combine(lo, datetime.min.time())
    horizon_end = datetime.combine(end, datetime.min.time())
    width, required = timedelta(days=days), timedelta(minutes=rest_min)
    check["window_coverage"]["ending_domain"] = {
        "start_inclusive": horizon_start.isoformat(), "end_inclusive": horizon_end.isoformat()}
    check["window_coverage"]["employee_proofs"] = {}
    history = _history_by_employee(dataset)
    policy = _zero_policy(dataset)
    calendars = _calendar_by_emp(dataset)
    found: list[dict] = []
    opened = 0
    for emp, lst in by_emp.items():
        assignment_intervals = _intervals(lst)
        invalid = "назначения сотрудника содержат нечитаемый либо неположительный интервал" if assignment_intervals is None else ""
        assignment_intervals = assignment_intervals or []
        if any(not horizon_start <= a < horizon_end for a, _b, _ in assignment_intervals):
            invalid = "начало назначения сотрудника вне объявленного горизонта"
        duties, duty_problem = _normative_rest_intervals(dataset, emp, lst)
        normative = duties is not None and not invalid
        ivs = duties if normative else assignment_intervals
        h = history.get(emp)
        last_end = _moment(h.get("Конец последней смены")) if h else None
        as_of = _moment(h.get("Срез")) if h else None
        known_start = horizon_start
        assumed_basis = None
        if last_end is not None and last_end <= horizon_start and as_of == horizon_start:
            known_start = last_end
            opened += 1
        elif _assumed_zero(policy, lo, h):
            # continue_zero (main): сотрудник без записи свободен до
            # горизонта по принятому допущению — занятость известна.
            known_start = horizon_start - width
            assumed_basis = policy["basis"]
        known = [] if invalid else [(known_start, horizon_end)]
        rests = []
        if not invalid:
            cursor = known_start
            for a, b, _ in ivs:
                if a > cursor and cursor < horizon_end:
                    rests.append((cursor, min(a, horizon_end)))
                cursor = max(cursor, b)
            if cursor < horizon_end:
                rests.append((cursor, horizon_end))
        # Явный выходной календаря истории доказывает отдых; рабочие /
        # резервные сутки календаря точного интервала занятости не дают.
        for day, value in calendars.get(emp, {}).items():
            if day < lo and value[0] == "day_off" and value[1] is not True:
                a = datetime.combine(day, datetime.min.time())
                b = a + timedelta(days=1)
                # Противоречивая история не рождает выдуманный отдых.
                if ((last_end is not None and a < last_end <= b)
                        or any(d_start < b and d_end > a for d_start, d_end, _ in ivs)):
                    continue
                known.append((a, b))
                rests.append((a, b))
        known, rests = _merge_intervals(known), _merge_intervals(rests)
        cells, proof = _continuous_rest_cells(horizon_start, horizon_end, width, required, known, rests)
        proof["occupancy_basis"] = "normative_duties" if normative else "assignment_projection_incomplete"
        if assumed_basis:
            proof["assumed_free_before_horizon"] = assumed_basis
        check["window_coverage"]["employee_proofs"][emp or "(не указан)"] = proof
        for left, right, is_point, passed, full in cells:
            rep_start, rep_end, rep_floor = _representative_window(left, right, is_point, width)
            reason = unknown_scope.get(emp, "")
            if not reason and not passed and not full:
                reason = invalid or ("нет полного интервала истории до горизонта; конец последней "
                                     "смены подтверждает отдых только после него"
                                     + ("" if h else " (" + _missing_record_reason(policy) + ")"))
            if not reason and not normative:
                reason = duty_problem or invalid
            # Занятость рейсами — подмножество нормативной занятости: если
            # даже её оптимистичный отдых не удовлетворяет норме, нарушение
            # уже доказано; pass и полноту покрытия так не доказать.
            lower_bound_violation = (not normative and not invalid and not unknown_scope.get(emp)
                                     and full and not passed)
            outcome = ("violation" if lower_bound_violation else None if reason
                       else "pass" if passed else "violation")
            entry = _window(check, emp, rep_start, rep_end, reason, outcome,
                            assumed_basis if not reason else None)
            entry["ending_domain"] = {"start": left.isoformat(), "end": right.isoformat(),
                                      "start_closed": is_point, "end_closed": is_point,
                                      "kind": "point" if is_point else "open_interval"}
            entry["representative_window"] = {"start_inclusive": rep_start, "end_exclusive": rep_end}
            entry["occupancy_complete"] = full and normative
            entry["occupancy_basis"] = proof["occupancy_basis"]
            if lower_bound_violation:
                entry["lower_bound_violation"] = True
            if not reason and passed and not assumed_basis:
                entry["reason"] = ("вся область окончаний ячейки покрыта объединением доказанных "
                                   "интервалов отдыха; неизвестная часть окна не нужна для доказательства")
            if outcome == "violation":
                row_i = next((i for a, _, i in ivs if a >= rep_floor - width), ivs[0][2] if ivs else 2)
                address = (f"момент {left.isoformat()}" if is_point else
                           f"интервал ({left.isoformat()}, {right.isoformat()})")
                source_sheet = "Смены" if normative else "Назначения"
                found.append(_finding(
                    counter, check, source_sheet, row_i,
                    f"окна длиной {days} суток с окончанием в {address}: "
                    f"нет непрерывного отдыха {rest_min} мин; "
                    + ("доказано уже по занятости рейсами" if lower_bound_violation else "занятость всех этих окон известна"),
                    f"{SOLUTION_SID}!{source_sheet}: Табельный номер=«{emp}», интервалы в окне со "
                    f"строки {row_i}; представитель [{rep_start}, {rep_end})"
                    + (f"; История: Конец последней смены=«{h.get('Конец последней смены')}»"
                       if h and last_end is not None else ""),
                    rid, "Начало" if normative else "Время вылета"))
    check["note"] = ("точное покрытие всех непрерывных окончаний окна: конечные критические точки "
                     "и открытые интервалы между ними; expected считает ячейки доказательства, "
                     "не суточные выборки и не бесконечное число окон")
    if history:
        check["note"] += (f"; история передана: окна до горизонта открыты концом последней "
                          f"смены у {opened} сотрудников")
    # K-15 / C-30: суточная форма main — legacy-диагностика рядом с
    # доказательством; статус, покрытие и находки она не меняет.
    check["daily_diagnostic"] = _rolling_rest_daily(plan, dataset, rows, plan.get("baseScope"))
    return _finish_windows(check, len(rows)), found


#: Версия показаний суточной формы (план §3.10.3 K-15): старты окон по
#: календарным суткам, единица — окно суток; legacy-диагностика, её
#: показания полным доказательством не называются.
DAILY_DIAGNOSTIC_VERSION = "cells/1"
DAILY_DIAGNOSTIC_NOTE = ("legacy-диагностика суточной формы main (план §3.10.3 K-15): старты "
                         "окон по календарным суткам, отдых — промежуток без назначений; "
                         "не статус проверки и не доказательство — доказательство правила "
                         "даёт непрерывное покрытие window_coverage (критические ячейки); "
                         "единицы форм различны, числовое неравенство — не расхождение")


def _rolling_rest_daily(plan, dataset, rows, scope) -> dict:
    """Суточная форма main (4d206b1) правила «в каждые N суток отдых не
    менее W» — LEGACY-ДИАГНОСТИКА (план §3.10.3 K-15, ревью группы C
    06.09.2026): старты окон по календарным суткам от первого вылета
    сотрудника (либо от первых полных суток после конца последней смены
    истории; при continue_zero сотрудник без записи свободен до
    горизонта по допущению — assumedFreeFrom), отдых — промежуток без
    назначений листа «Назначения», края присутствия — отдых; окна через
    границу без известной истории не судятся (адресно). Её единица (окно
    суток, версия cells/1) и домен (сутки, где окно помещается в
    назначения сотрудника) отличаются от непрерывного покрытия
    (критические ячейки области окончаний): числовое неравенство — не
    расхождение результатов. Показание едет в check["daily_diagnostic"],
    статус проверки, покрытие, находки и вердикт оно не определяет."""
    days = _whole(plan.get("condBound")) or 0
    rest_min = _whole(plan.get("bound")) or 0
    out = {"version": DAILY_DIAGNOSTIC_VERSION, "form": "daily_window_starts",
           "unit": "окно суток (старт по календарным суткам)",
           "window_days": days, "rest_min": rest_min,
           "windows_judged": 0, "windows_violated": 0, "windows_unjudged": 0,
           "windows_assumed_employees": 0, "windows_opened_by_history": 0,
           "violations_sample": [], "unjudged_sample": [],
           "note": DAILY_DIAGNOSTIC_NOTE}
    if days <= 0 or rest_min <= 0:
        return out
    history = _history_by_employee(dataset)
    hist_sheet = _sheet(dataset, "История")
    policy = _zero_policy(dataset)
    no_record = {
        str(r.get(EMP) or "").strip() for r in (hist_sheet[0] if hist_sheet else [])
        if str(r.get("Есть история") or "").strip().lower() != "да"}
    for emp, lst in _rows_by_emp(rows, scope).items():
        ivs = _intervals(lst)
        if not ivs:
            continue
        first_dep = ivs[0][0]
        lo = first_dep.date()
        hi = max(b for _, b, _ in ivs).date()
        cursor = datetime.combine(lo, datetime.min.time())
        h = history.get(emp)
        last_end = _moment(h.get("Конец последней смены")) if h else None
        missing_record = h is None and (not policy["passed"] or emp in no_record
                                        or emp not in history)
        if last_end is None and missing_record and policy["zero_assumed"]:
            # assumedFreeFrom: сотрудник без записи при continue_zero —
            # окна через границу судятся по допущению и так называются.
            out["windows_assumed_employees"] += 1
            lo = lo - timedelta(days=days - 1)
            cursor = datetime.combine(lo, datetime.min.time())
        elif last_end is None:
            reason = (_missing_record_reason(policy) if missing_record
                      else "запись истории без конца последней смены (неизвестно, не ноль)")
            for j in range(1, days):
                wd = lo - timedelta(days=j)
                out["windows_unjudged"] += 1
                if len(out["unjudged_sample"]) < _UNJUDGED_SAMPLE:
                    out["unjudged_sample"].append({
                        "employee": emp, "window_from": wd.isoformat(),
                        "window_to": (wd + timedelta(days=days - 1)).isoformat(),
                        "reason": reason})
        if last_end is not None and last_end < first_dep:
            cursor = last_end
            end_day = last_end.date()
            first_full = (end_day if last_end == datetime.combine(end_day, datetime.min.time())
                          else end_day + timedelta(days=1))
            if first_full < lo:
                lo = first_full
                out["windows_opened_by_history"] += 1
        rests = []
        for a, b, _ in ivs:
            if a > cursor:
                rests.append((cursor, a))
            if b > cursor:
                cursor = b
        rests.append((cursor, datetime.combine(hi + timedelta(days=1), datetime.min.time())))
        d = lo
        while d + timedelta(days=days - 1) <= hi:
            w_from = datetime.combine(d, datetime.min.time())
            w_to = w_from + timedelta(days=days)
            best = max((int((min(r_to, w_to) - max(r_from, w_from)).total_seconds() // 60)
                        for r_from, r_to in rests), default=0)
            out["windows_judged"] += 1
            if best < rest_min:
                out["windows_violated"] += 1
                if len(out["violations_sample"]) < _UNJUDGED_SAMPLE:
                    out["violations_sample"].append({
                        "employee": emp, "window_from": d.isoformat(),
                        "window_to": (d + timedelta(days=days - 1)).isoformat(),
                        "best_rest_min": best})
            d += timedelta(days=1)
    if out["windows_assumed_employees"]:
        out["windows_assumed_basis"] = policy["basis"]
    return out


def _closed_spans(spans):
    """Объединение ЗАМКНУТЫХ отрезков с сохранением одноточечных."""
    merged = []
    for a, b in sorted(spans):
        if b < a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def _continuous_rest_cells(lo, hi, width, required, known, rests):
    """Конечное точное доказательство для несчётного множества моментов
    окончания окна x: [x−W, x] пересекает доказанный отдых [a, b] не
    менее чем на R ⇔ x ∈ [a+R, b+W−R] (при b−a ≥ R и W ≥ R); окно
    целиком известно ⇔ x ∈ [k+W, l] для известной занятости [k, l]. Оба
    множества — объединения ЗАМКНУТЫХ отрезков; разбиение их концов на
    одноточечные ячейки и открытые интервалы между ними сохраняет
    случаи равенства. Выборка для классификации ячейки не используется."""
    passing = _closed_spans((max(lo, a + required), min(hi, b + width - required))
                            for a, b in rests if b - a >= required and width >= required)
    complete = _closed_spans((max(lo, a + width), min(hi, b))
                             for a, b in known if b - a >= width)
    cuts = sorted({lo, hi} | {x for span in passing + complete for x in span})
    cells = []
    for i, left in enumerate(cuts):
        cells.append((left, left, True,
                      any(a <= left <= b for a, b in passing),
                      any(a <= left <= b for a, b in complete)))
        if i + 1 < len(cuts):
            right = cuts[i + 1]
            cells.append((left, right, False,
                          any(a < right and b > left for a, b in passing),
                          any(a < right and b > left for a, b in complete)))

    def spans(values):
        return [{"start_inclusive": a.isoformat(), "end_inclusive": b.isoformat()} for a, b in values]

    proof = {"window_minutes": int(width.total_seconds() // 60),
             "required_rest_minutes": int(required.total_seconds() // 60),
             "known_occupancy_intervals": [{"start_inclusive": a.isoformat(), "end_exclusive": b.isoformat()}
                                           for a, b in known],
             "proven_rest_intervals": [{"start_inclusive": a.isoformat(), "end_exclusive": b.isoformat()}
                                       for a, b in rests],
             "passing_window_end_spans": spans(passing),
             "fully_known_window_end_spans": spans(complete),
             "equivalence": "rest[a,b]>=R and W>=R: x in [a+R,b+W-R]; known[k,l]: x in [k+W,l]"}
    return cells, proof


def _representative_window(left, right, is_point, width):
    # Классификация аналитическая, не по этому представителю. Середина в
    # половину микросекунды (нечётное число микросекунд между точками)
    # сохраняется текстом, а не сдвигается на конец ячейки.
    micros = 0 if is_point else (right - left) // timedelta(microseconds=1)
    floor = left + timedelta(microseconds=micros // 2)

    def stamp(value):
        if micros % 2:
            return value.isoformat(timespec="microseconds") + "5"
        return value.isoformat()

    return stamp(floor - width), stamp(floor), floor


def _merge_intervals(intervals):
    merged = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def _touched_days(ivs) -> set[date]:
    out: set[date] = set()
    for a, b, _ in ivs:
        d = a.date()
        while d <= b.date():
            out.add(d)
            d += timedelta(days=1)
    return out


def _days_off(rid, rule, plan, dataset, counter):
    """ВЫХОДНЫХ В МЕСЯЦ НЕ МЕНЕЕ N — календарные месяцы, кончающиеся в
    горизонте; сутки до горизонта — по календарю истории либо точному
    агрегату полного префикса («Рабочих дней с начала месяца» при срезе
    = начало горизонта); без записи при continue_zero — префикс нулевой
    по допущению."""
    bound = _whole(plan.get("bound"))
    check = _check(rid, rule, "выходных-в-месяц", "месяц",
                   f"выходных в календарном месяце не менее {bound} (месяцы, кончающиеся "
                   f"в горизонте; история — посуточно либо точный агрегат полного префикса) — "
                   f"{str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Назначения", "bound": bound,
                    "base_scope": plan.get("baseScope")}, plan)
    return _count_windows(rid, plan, dataset, counter, check, bound, monthly=True)


def _consecutive_days(rid, rule, plan, dataset, counter):
    """РАБОЧИХ ДНЕЙ ПОДРЯД НЕ БОЛЕЕ N — по листу «Назначения» (сутки
    заняты, если их касается назначение); серия, начатая в первый день
    горизонта, продолжает «Рабочих дней подряд» истории при срезе =
    начало горизонта; пропавший перенос — неизвестность, не ноль
    (без записи при continue_zero — 0 по допущению)."""
    bound = _whole(plan.get("bound"))
    check = _check(rid, rule, "серия-рабочих-дней-подряд", "день",
                   f"рабочих дней подряд не более {bound} (серия на начало горизонта "
                   f"продолжает известную серию истории) — {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Назначения", "bound": bound,
                    "base_scope": plan.get("baseScope")}, plan)
    _coverage(check, "day_sequence")
    if bound is None:
        check.update(status="не прогнана", rows_checked=0, reason="нет корректного целого порога серии")
        return check, []
    got = _need(check, _sheet(dataset, "Назначения"), "Назначения",
                (EMP, "Время вылета", "Время прилёта"))
    if got is None:
        return check, []
    rows, _cols = got
    context = _window_context(check, dataset, rows, plan.get("baseScope"))
    if context is None:
        return check, []
    lo, end, by_emp, unknown_scope = context
    history = _history_by_employee(dataset)
    policy = _zero_policy(dataset)
    found: list[dict] = []
    carried = 0
    for emp, lst in by_emp.items():
        ivs = _intervals(lst)
        reason = unknown_scope.get(emp, "")
        if ivs is None or any(not lo <= a.date() < end for a, _b, _ in ivs):
            reason = "нечитаемый, неположительный либо внегоризонтный интервал сотрудника"
        if reason:
            _window(check, emp, lo, end, reason)
            continue
        days = sorted(d for d in _touched_days(ivs) if lo <= d < end)
        h = history.get(emp)
        initial_carry = _whole(h.get("Рабочих дней подряд")) if h else None
        if not h or _moment(h.get("Срез")) != datetime.combine(lo, datetime.min.time()):
            initial_carry = None
        assumed_basis = None
        if _assumed_zero(policy, lo, h):
            initial_carry, assumed_basis = 0, policy["basis"]
        finding_start = len(found)
        run: list[date] = []
        for d in days + [None]:
            if run and (d is None or (d - run[-1]).days != 1):
                carry = 0
                if run[0] == lo:
                    if initial_carry is None:
                        reason = ("нет известной серии рабочих дней на начало объявленного горизонта"
                                  + ("" if h else " (" + _missing_record_reason(policy) + ")"))
                    else:
                        carry = initial_carry
                    if carry:
                        carried += 1
                total = len(run) + carry
                if total > bound:
                    row_i = next((i for a, _, i in ivs if a.date() == run[0]), ivs[0][2])
                    found.append(_finding(
                        counter, check, "Назначения", row_i,
                        f"рабочих дней подряд {total} больше предела {bound}"
                        + (f" (из них {carry} из истории)" if carry else ""),
                        f"{SOLUTION_SID}!Назначения: Табельный номер=«{emp}», серия с "
                        f"{run[0].isoformat()} по {run[-1].isoformat()}"
                        + (f"; История: Рабочих дней подряд=«{carry}», Срез=«{h.get('Срез')}»"
                           if carry else ""),
                        rid, "Время вылета"))
                run = []
            if d is not None:
                run.append(d)
        _window(check, emp, lo, end, reason,
                "violation" if len(found) > finding_start else (None if reason else "pass"),
                assumed_basis if (assumed_basis and days and days[0] == lo) else None)
    if history:
        check["note"] = (f"история передана: перенос серии рабочих дней применён у "
                         f"{carried} сотрудников")
    return _finish_windows(check, len(rows)), found


CALENDAR_NOTE = ("окна через границу горизонта сохранены как неизвестные: посуточного "
                 "календаря до горизонта (лист «Календарь истории», ревизия 1.17.2) в "
                 "решении нет")

_KIND_RU = {"работа": "work", "резерв": "reserve", "выходной": "day_off",
            "отпуск": "leave", "болезнь": "sick"}


def _calendar_by_emp(dataset: dict) -> dict[str, dict]:
    """Лист «Календарь истории» (ревизия 1.17.2): {табельный: {дата: (вид,
    ночная)}}; вид — словарь work|reserve|day_off|leave|sick|unknown (русские
    значения листа интегратора переводятся), ночная — да/нет/None.
    Противоречивые дубли одного дня — («unknown», None), не последняя
    строка (C-33)."""
    got = _sheet(dataset, "Календарь истории")
    if got is None:
        return {}
    rows, _cols = got
    out: dict[str, dict] = {}
    for r in rows:
        m = _moment(r.get("Дата"))
        if m is not None:
            day = m.date()
        else:
            try:
                day = date.fromisoformat(str(r.get("Дата") or "")[:10])
            except ValueError:
                continue
        kind = str(r.get("Вид") or "unknown").strip().lower()
        kind = _KIND_RU.get(kind, kind)
        n = r.get("Ночная")
        s = str(n).strip().lower() if n is not None else ""
        night = True if (n is True or s in ("да", "true", "1", "yes")) else \
            (False if (n is False or s in ("нет", "false", "0", "no")) else None)
        cal = out.setdefault(str(r.get(EMP) or "").strip(), {})
        value = (kind, night)
        cal[day] = value if day not in cal or cal[day] == value else ("unknown", None)
    return out


def _cal_day_off(cal: dict, x) -> int | None:
    """Выходной календаря: явный день отдыха (выходной/отпуск/больничный)
    без признака ночной; с признаком ночной = да — противоречие,
    неизвестно (C-34)."""
    c = cal.get(x)
    if c is None:
        return None
    if c[0] in ("day_off", "leave", "sick"):
        return None if c[1] is True else 1
    return 0 if c[0] in ("work", "reserve") else None


def _cal_nights(cal: dict, x) -> int | None:
    """Ночных суток календаря: работа/резерв — по признаку ночной (без
    признака — неизвестно, не ноль); выходной без ночи — 0 (C-34)."""
    c = cal.get(x)
    if c is None:
        return None
    kind, night = c
    if kind in ("work", "reserve"):
        return None if night is None else int(night)
    if kind in ("day_off", "leave", "sick") and night is not True:
        return 0
    return None


def _calendar_note(cal_all: dict, opened: int) -> str:
    if not cal_all:
        return CALENDAR_NOTE
    return (f"календарь истории передан: окон через границу горизонта судимо {opened} "
            f"(сутки без записи сохраняются как неизвестные обязательные окна)")


def _count_windows(rid, plan, dataset, counter, check, bound, *, days=None,
                   monthly=False, night=False):
    """Единый движок счётных окон (C-31): месяцы, кончающиеся в
    горизонте, либо суточные окна с окончанием в каждом дне горизонта;
    сутки до горизонта — по календарю истории; точный агрегат полного
    префикса месяца при срезе = начало горизонта; противоречие агрегата
    с календарём → unknown; ночные окна требуют нормативных смен;
    находки у сотрудников без назначений — в листы истории; сотрудник
    без записи при continue_zero — префикс по допущению нулевой."""
    _coverage(check, "calendar_month" if monthly else "day")
    if bound is None or (not monthly and not days):
        check.update(status="не прогнана", rows_checked=0,
                     reason="нет корректного целого порога либо длины окна")
        return check, []
    sheet = "Смены" if night else "Назначения"
    fields = ("Начало", "Конец") if night else ("Время вылета", "Время прилёта")
    got = _need(check, _sheet(dataset, sheet), sheet, (EMP, *fields))
    if got is None:
        return check, []
    rows, _cols = got
    context = _window_context(check, dataset, rows, plan.get("baseScope"), fields)
    if context is None:
        return check, []
    lo, end, by_emp, unknown_scope = context
    if monthly:
        windows = []
        first = lo.replace(day=1)
        while first < end:
            following = first + timedelta(days=calendar.monthrange(first.year, first.month)[1])
            if following <= end:
                windows.append((first, following))
            first = following
    else:
        windows = list(_daily_windows(lo, end, days))
    cal_all = _calendar_by_emp(dataset)
    history = _history_by_employee(dataset)
    policy = _zero_policy(dataset)
    assignment_sheet = _sheet(dataset, "Назначения")
    assignment_groups = _rows_by_emp(assignment_sheet[0], None) if assignment_sheet else {}
    found, opened, hist_cells = [], 0, 0
    for emp, lst in by_emp.items():
        known_rows, invalid = [], False
        duty_problem = ""
        if night:
            _duty_intervals, duty_problem = _normative_rest_intervals(dataset, emp, assignment_groups.get(emp, []))
            invalid = bool(duty_problem)
        for i, row in lst:
            a, b = _moment(row.get(fields[0])), _moment(row.get(fields[1]))
            if a is None or b is None or b <= a or not lo <= a.date() < end:
                invalid = True
            else:
                known_rows.append((a, b, i))
        busy = _touched_days(known_rows)
        nights = {}
        if night:
            for a, _b, i in known_rows:
                if _in_night(a.hour * 60 + a.minute, int(plan["nightFrom"]), int(plan["nightTo"])):
                    nights.setdefault(a.date(), []).append(i)
        cal = cal_all.get(emp, {})
        h = history.get(emp)
        assumed = _assumed_zero(policy, lo, h)
        for start, finish in windows:
            total, history_off, missing = 0, 0, []
            day = start
            while day < finish:
                if day < lo:
                    value = _cal_nights(cal, day) if night else _cal_day_off(cal, day)
                    if value is None:
                        missing.append(day)
                    else:
                        total += value
                        history_off += value
                elif not invalid:
                    total += len(nights.get(day, [])) if night else int(day not in busy)
                day += timedelta(days=1)
            aggregate_proof, aggregate_conflict = None, ""
            assumed_here = None
            if (monthly and start < lo and start == lo.replace(day=1) and h
                    and _moment(h.get("Срез")) == datetime.combine(lo, datetime.min.time())):
                # Месячному итогу посуточные позиции не нужны, когда агрегат
                # покрывает ровно весь недостающий префикс месяца; более
                # короткое скользящее окно он не устанавливает.
                worked = _whole(h.get("Рабочих дней с начала месяца"))
                prefix_days = (lo - start).days
                if worked is not None and worked <= prefix_days:
                    prefix_off = prefix_days - worked
                    if history_off <= prefix_off <= history_off + len(missing):
                        if missing:
                            total += prefix_off - history_off
                            missing = []
                            aggregate_proof = {"kind": "exact_month_prefix_aggregate",
                                "start_inclusive": start.isoformat(), "end_exclusive": lo.isoformat(),
                                "days_worked": worked, "days_off": prefix_off}
                            hist_cells += 1
                    else:
                        aggregate_conflict = "точный агрегат месяца противоречит известному посуточному календарю"
            if missing and assumed and not aggregate_conflict:
                # continue_zero (main): сотрудник без записи — до горизонта
                # не работал: сутки без записи — выходные / без ночных, по
                # названному допущению.
                total += (0 if night else len(missing))
                assumed_here = policy["basis"]
                missing = []
            reason = unknown_scope.get(emp, "")
            if not reason and invalid:
                reason = duty_problem or "нечитаемый, неположительный либо внегоризонтный интервал сотрудника"
            if not reason and aggregate_conflict:
                reason = aggregate_conflict
            if not reason and missing:
                reason = _history_missing_reason(missing)
                if h is None:
                    reason = _missing_record_reason(policy) + "; " + reason
            violated = total > bound if night else total < bound
            outcome = None if reason else ("violation" if violated else "pass")
            entry = _window(check, emp, start, finish, reason, outcome, assumed_here)
            entry["observed_count"] = total
            if aggregate_proof is not None:
                entry["history_proof"] = aggregate_proof
            if reason:
                continue
            if start < lo:
                opened += 1
            if not violated:
                continue
            row_i = next((i for a, _, i in known_rows if start <= a.date() < finish),
                         known_rows[0][2] if known_rows else None)
            target_sheet = sheet
            if row_i is None:
                # Чисто историческое доказательство — у сотрудника без
                # назначений: адрес в листе истории.
                target_sheet = "История" if (aggregate_proof or assumed_here) else "Календарь истории"
                history_rows = _sheet(dataset, target_sheet)
                row_i = next((i for i, row in enumerate(history_rows[0] if history_rows else [], 2)
                              if str(row.get(EMP) or "").strip() == emp), 2)
            if night:
                label = f"ночных смен {total} больше предела {bound}"
            elif monthly:
                label = f"выходных в {start.strftime('%Y-%m')}: {total} меньше нормы {bound}"
            else:
                label = f"выходных {total} меньше нормы {bound}"
            found.append(_finding(
                counter, check, target_sheet, row_i,
                f"окно {start.isoformat()}–{(finish - timedelta(days=1)).isoformat()}: {label}"
                + (" (часть окна до горизонта — по точному агрегату полного префикса месяца)"
                   if aggregate_proof else " (часть окна до горизонта — по допущению нулевых остатков)"
                   if assumed_here and start < lo else " (часть окна до горизонта — по календарю истории)"
                   if start < lo else ""),
                f"{SOLUTION_SID}!{target_sheet}: Табельный номер=«{emp}», "
                f"окно [{start.isoformat()}, {finish.isoformat()}), строка {row_i}"
                + (f"; История: Рабочих дней с начала месяца=«{aggregate_proof['days_worked']}»"
                   f", Срез=«{h.get('Срез')}»" if aggregate_proof else ""),
                rid, fields[0] if target_sheet == sheet else "Рабочих дней с начала месяца"
                if aggregate_proof else ("Табельный номер" if assumed_here else "Дата")))
    if monthly:
        notes = [f"месяцев горизонта: {len(windows)}",
                 "история месяца: посуточный календарь либо точный агрегат полного префикса "
                 "месяца при совпадении среза с началом горизонта"]
        if history:
            notes.append(f"история передана: месяц среза судится по агрегату у {hist_cells} "
                         f"сотрудников с историей")
        check["note"] = "; ".join(notes)
    else:
        check["note"] = _calendar_note(cal_all, opened)
    return _finish_windows(check, len(rows)), found


def _days_off_window(rid, rule, plan, dataset, counter):
    """ВЫХОДНЫХ В СКОЛЬЗЯЩЕМ ОКНЕ НЕ МЕНЕЕ K — по листу «Назначения»:
    выходной — сутки горизонта без назначений сотрудника; окна — с
    окончанием в каждых сутках горизонта (часть до горизонта — по листу
    «Календарь истории»: выходной только явный день отдыха, сутки без
    записи делают окно неизвестным); нехватка — K минус выходных в окне.
    То же прочтение, что у решателя (LogicForms.daysOffWindowShortfall)."""
    days = _whole(plan.get("condBound"))
    min_off = _whole(plan.get("bound"))
    check = _check(rid, rule, "выходных-в-скользящем-окне", "окно",
                   f"в каждые {days} суток выходных не менее {min_off} (окна горизонта и "
                   f"окна через его границу по календарю истории) — "
                   f"{str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Назначения", "window_days": days,
                    "min_days_off": min_off, "base_scope": plan.get("baseScope")}, plan)
    return _count_windows(rid, plan, dataset, counter, check, min_off, days=days)


def _night_window(rid, rule, plan, dataset, counter):
    """НОЧНЫХ СМЕН В СКОЛЬЗЯЩЕМ ОКНЕ НЕ БОЛЕЕ K — по листу «Смены»:
    ночная — начало смены в окне минут суток [от, до); смена относится к
    суткам начала; окна — с окончанием в каждых сутках горизонта (часть
    до горизонта — по листу «Календарь истории»: ночные сутки; сутки без
    записи или рабочие без признака ночи делают окно неизвестным)."""
    days = _whole(plan.get("condBound"))
    max_n = _whole(plan.get("bound"))
    nf, nt = _whole(plan.get("nightFrom")), _whole(plan.get("nightTo"))
    check = _check(rid, rule, "ночных-в-скользящем-окне", "окно",
                   f"в каждые {days} суток ночных смен не более {max_n} (ночная — начало "
                   f"смены в окне [{nf}, {nt}) минут суток; окна горизонта и окна через его "
                   f"границу по календарю истории) — {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Смены", "window_days": days, "bound": max_n,
                    "night_from": nf, "night_to": nt, "base_scope": plan.get("baseScope")}, plan)
    if nf is None or nt is None or nf >= 1440 or nt >= 1440 or nf == nt:
        _coverage(check)
        check.update(status="не прогнана", rows_checked=0,
                     reason="план решателя не несёт окна ночи")
        return check, []
    plan = dict(plan, nightFrom=nf, nightTo=nt)
    return _count_windows(rid, plan, dataset, counter, check, max_n, days=days, night=True)


def _duty_series(rid, rule, plan, dataset, counter):
    """СЕРИЯ РАБОЧИХ СУТОК ПОДРЯД НЕ БОЛЕЕ N, И ПОСЛЕ НЕЁ НЕ МЕНЕЕ M
    ПОЛНОСТЬЮ СВОБОДНЫХ СУТОК — по листу «Назначения» (сутки заняты, если
    их касается назначение; две смены в одни сутки — один рабочий день).

    Серия, начатая в первый день горизонта, продолжает «Рабочих дней
    подряд» истории при срезе = начало горизонта; пропавший перенос —
    неизвестность, не ноль (без записи при continue_zero — 0 по
    допущению). Свободные сутки после серии — разрыв до начала следующей
    серии минус одни сутки; ПРИБЛИЖЕНИЕ: хвост ПОСЛЕДНЕЙ серии не
    судится — следующей серии нет, и свободные сутки после неё
    неизвестны. Нарушение двойное и считается одной находкой на серию:
    серия длиннее предела и/или серия, ДОСТИГШАЯ предела, после которой
    свободных суток меньше требуемого. То же прочтение, что у решателя
    (LogicForms.seriesPenalty)."""
    limit = _whole(plan.get("condBound"))
    required = _whole(plan.get("bound"))
    check = _check(rid, rule, "серия-рабочих-смен-и-отдых-после", "серия",
                   f"рабочих суток подряд не более {limit}, и после серии, достигшей "
                   f"предела, не менее {required} полностью свободных суток (серия на "
                   f"начало горизонта продолжает известную серию истории; хвост "
                   f"последней серии не судится) — {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Назначения", "bound": limit,
                    "days_off_after": required,
                    "base_scope": plan.get("baseScope")}, plan)
    _coverage(check, "day_sequence")
    if not limit or required is None:
        check.update(status="не прогнана", rows_checked=0,
                     reason="нет корректных целых границ: длины серии и числа "
                            "свободных суток после неё")
        return check, []
    got = _need(check, _sheet(dataset, "Назначения"), "Назначения",
                (EMP, "Время вылета", "Время прилёта"))
    if got is None:
        return check, []
    rows, _cols = got
    context = _window_context(check, dataset, rows, plan.get("baseScope"))
    if context is None:
        return check, []
    lo, end, by_emp, unknown_scope = context
    history = _history_by_employee(dataset)
    policy = _zero_policy(dataset)
    found: list[dict] = []
    carried = 0
    for emp, lst in by_emp.items():
        ivs = _intervals(lst)
        reason = unknown_scope.get(emp, "")
        if ivs is None or any(not lo <= a.date() < end for a, _b, _ in ivs):
            reason = "нечитаемый, неположительный либо внегоризонтный интервал сотрудника"
        if reason:
            _window(check, emp, lo, end, reason)
            continue
        days = sorted(d for d in _touched_days(ivs) if lo <= d < end)
        runs: list[list[date]] = []
        for d in days:
            if runs and (d - runs[-1][-1]).days == 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        h = history.get(emp)
        initial_carry = _whole(h.get("Рабочих дней подряд")) if h else None
        if not h or _moment(h.get("Срез")) != datetime.combine(lo, datetime.min.time()):
            initial_carry = None
        assumed_basis = None
        if _assumed_zero(policy, lo, h):
            initial_carry, assumed_basis = 0, policy["basis"]
        finding_start = len(found)
        for i, run in enumerate(runs):
            carry = 0
            if run[0] == lo:
                if initial_carry is None:
                    reason = ("нет известной серии рабочих дней на начало объявленного горизонта"
                              + ("" if h else " (" + _missing_record_reason(policy) + ")"))
                else:
                    carry = initial_carry
                if carry:
                    carried += 1
            total = len(run) + carry
            over = total - limit if total > limit else 0
            free = (runs[i + 1][0] - run[-1]).days - 1 if i + 1 < len(runs) else None
            short = (required - free
                     if free is not None and total >= limit and free < required else 0)
            if not over and not short:
                continue
            row_i = next((k for a, _b, k in ivs if a.date() == run[0]), ivs[0][2])
            parts = []
            if over:
                parts.append(f"рабочих суток подряд {total} больше предела {limit}"
                             + (f" (из них {carry} из истории)" if carry else ""))
            if short:
                parts.append(f"после серии свободных суток {free}, требуется {required}")
            found.append(_finding(
                counter, check, "Назначения", row_i, "; ".join(parts),
                f"{SOLUTION_SID}!Назначения: Табельный номер=«{emp}», серия с "
                f"{run[0].isoformat()} по {run[-1].isoformat()}"
                + (f"; следующая серия с {runs[i + 1][0].isoformat()}"
                   if free is not None else "")
                + (f"; История: Рабочих дней подряд=«{carry}», Срез=«{h.get('Срез')}»"
                   if carry else ""),
                rid, "Время вылета"))
        _window(check, emp, lo, end, reason,
                "violation" if len(found) > finding_start else (None if reason else "pass"),
                assumed_basis if (assumed_basis and runs and runs[0][0] == lo) else None)
    if history:
        check["note"] = (f"история передана: перенос серии рабочих дней применён у "
                         f"{carried} сотрудников")
    return _finish_windows(check, len(rows)), found


_EXECUTORS = {FORM_NIGHT: _night_series, FORM_ROLLING: _rolling_rest,
              FORM_DAYS_OFF: _days_off, FORM_CONSECUTIVE: _consecutive_days,
              FORM_DAYS_OFF_ROLLING: _days_off_window, FORM_NIGHT_ROLLING: _night_window,
              FORM_DUTY_SERIES: _duty_series}


def logic_code_checks(svod: dict, dataset: dict, logic_report) \
        -> tuple[list[dict], list[dict], dict]:
    """(проверки кодом, находки, свод без взятых правил). Берётся жёсткое
    правило с блоком logic, для которого показания решателя несут план
    формы с исполнителем здесь; остальные — компилятору."""
    plans = plans_by_rule(logic_report)
    if not plans:
        return [], [], svod
    checks: list[dict] = []
    findings: list[dict] = []
    taken: set[str] = set()
    counter = _Counter()
    for r in svod.get("rules") or []:
        if not isinstance(r, dict) or r.get("kind") != "HARD":
            continue
        rid = str(r.get("id"))
        if not isinstance(r.get("logic"), dict) or rid not in plans:
            continue
        plan = plans[rid]
        c, f = _EXECUTORS[plan["form"]](rid, r, plan, dataset, counter)
        checks.append(c)
        findings.extend(f)
        taken.add(rid)
    if not taken:
        return [], [], svod
    out = dict(svod)
    out["rules"] = [r for r in svod.get("rules") or []
                    if str(r.get("id")) not in taken]
    return checks, findings, out
