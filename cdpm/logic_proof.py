# -*- coding: utf-8 -*-
"""Доказательства машинной логики (ступень 3, глава 2 дизайна).

Три рубежа для правила с блоком logic:

1. ОБРАТНЫЙ ПЕРЕСКАЗ (m.render_logic) сверяется независимой моделью
   с формулировкой свода; вердикт — материал человеку, расхождение
   блокирует правило вопросом анкеты (retell_check).
2. КОНТРПРИМЕРНАЯ ПАРА: мини-набор + нарушающее и соблюдающее
   решения, проверяемые ОБЕИМИ сторонами на одном входе
   (generate_pair); гейт пары — механический (check_pair).
3. ПЕРЕКРЁСТНАЯ СВЕРКА: перевод logic в спецификацию проверки
   сертификатора ДЕТЕРМИНИРОВАН (logic_to_check_spec) — прицел
   модели-компилятора для правил с logic не нужен, расходиться
   нечему.

Первая волна переводов — виды требований duration_within и
count_within (большинство правил Лайта); вид без перевода — честный
None с причиной, правило остаётся тексту и сертификатору обычным
путём. Fail-closed: «половина доказательства» не выдаётся.

Формы истории сотрудника (серия ночных смен подряд, отдых в скользящем
окне) переводятся в шаблоны «серия-ночных-смен» и
«отдых-в-скользящем-окне» по листу смен — очертания те же, что
распознаёт решатель (LogicForms.formOf), поэтому правило с такой
логикой получает пару и не попадает в manifest.logic_unproven.
"""
from __future__ import annotations

import json

from cdpm import data_checks as dc
from cdpm import llm as llm_mod
from cdpm import model as m

SOLUTION_SID = "SOL"

# Предмет требования → (лист решения, колонки началo/конец,
# группировка по человеку). Первая волна: смены и назначения.
_DURATION_SHEETS = {
    "duty": ("Смены", "Начало", "Конец", "Табельный номер"),
    "assignment": ("Назначения", "Время вылета", "Время прилёта",
                   "Табельный номер"),
}
_COUNT_GROUPS = {
    "flight": ("Назначения", "Рейс"),
    "duty": ("Назначения", "Смена"),
    "base": ("Резерв", "База"),
}


def logic_to_check_spec(rule: dict) -> tuple[dict | None, str]:
    """(спецификация проверки, причина-отказ). Детерминированный
    перевод: правило с logic компилируется в проверку сертификатора
    БЕЗ модели — рубеж 3 главы 2. Неподдержанный вид — (None,
    причина), правило проверяется обычным путём компилятора."""
    logic = rule.get("logic")
    if not isinstance(logic, dict):
        return None, "правило без logic"
    param_problems = m.logic_unit_problems(logic, rule)
    if param_problems:
        return None, "; ".join(param_problems)
    dem = logic.get("demand") or {}
    kind = dem.get("kind")
    subj = logic.get("subject")
    # Первая волна переводов НЕ выражает условия-фильтры: пара,
    # построенная с игнорированием condition, доказывает более
    # широкое правило, чем поставлено (находка прогона run-0046:
    # «ночных подряд» без starts_in читалось «любых смен подряд»).
    conds = logic.get("condition") or []
    # Формы истории сотрудника (серия ночных смен, отдых в скользящем
    # окне): их condition-атомы ВЫРАЖЕНЫ шаблонами «серия-ночных-смен»
    # и «отдых-в-скользящем-окне» — очертание то же, что распознаёт
    # решатель (LogicForms.formOf); почти-форма — честный отказ с
    # именованной причиной, не догадка.
    hist, why_hist = _history_form_spec(rule, logic)
    if why_hist:
        return None, why_hist
    if hist is None:
        # Условие выражено шаблоном — не препятствие: месячное окно у
        # формы выходных судится самим шаблоном «выходных-в-месяце»
        # (полные календарные месяцы); прочие условия первая волна не
        # выражает — отказ (пара доказывала бы более широкое правило).
        unexpressed = [
            c for c in conds
            if not (isinstance(c, dict)
                    and c.get("predicate") == "in_window"
                    and isinstance(c.get("window"), dict)
                    and c["window"].get("kind") == "month"
                    and subj == "day_off"
                    and kind == "count_within")]
        if unexpressed:
            preds = sorted({str(c.get("predicate"))
                            for c in unexpressed if isinstance(c, dict)})
            return None, (f"condition-атомы {preds} вне первой волны "
                          f"переводов — пара доказывала бы более "
                          f"широкое правило")

    def _num(pref):
        # 1.6: ссылка-объект {name, qualifier} разрешается по
        # ОБЕИМ координатам — строка (одна запись) по имени.
        entry = _param_entry(rule, pref)
        return None if entry is None else entry.get("value")

    if hist is not None:
        template, params = hist
    elif kind == "duration_within":
        # 1.6: measure обязателен; первая волна переводов —
        # только span (размах); block (налёт) — суммой плеч,
        # следующей волной.
        if dem.get("measure") != "span":
            return None, (f"duration_within: measure "
                          f"{dem.get('measure')!r} вне "
                          f"первой волны переводов (только "
                          f"span)")
        place = _DURATION_SHEETS.get(subj)
        if place is None:
            return None, (f"duration_within: предмет {subj!r} вне "
                          f"первой волны переводов")
        sheet, c_start, c_end, c_group = place
        minutes = _num(dem.get("param"))
        if not isinstance(minutes, int):
            return None, (f"параметр {dem.get('param')!r} не даёт "
                          f"целых минут")
        rel = dem.get("relation")
        if rel == "max":
            template = "максимальная-длительность-часов"
            params = {"sheet": f"{SOLUTION_SID}/{sheet}",
                      "start_column": c_start, "end_column": c_end,
                      "max_hours": minutes / 60}
        elif rel == "min":
            # A minimum span measures EACH record, not the gap between two
            # records. The existing gap checker cannot prove this predicate.
            return None, ("duration_within: min/span не доказан — "
                          "минимальная длительность записи не выражена "
                          "шаблоном разрыва между соседними записями")
        else:
            return None, f"duration_within: relation {rel!r} вне " \
                         f"первой волны"
    elif kind == "count_within" and subj == "day_off":
        # 12а: универсумная форма «выходных в месяц не менее N» —
        # шаблон по клеткам сотрудник×полный месяц (подстановка
        # нуля: сотрудник без записей тривиально удовлетворяет min,
        # его отсутствие на листе счёт нарушений не прячет).
        if dem.get("relation") != "min":
            return None, ("выходные-в-месяце: relation "
                          f"{dem.get('relation')!r} вне первой "
                          "волны (только min)")
        if dem.get("universe") != "employee\u00d7month":
            return None, ("выходные-в-месяце: универсум "
                          f"{dem.get('universe')!r} вне первой "
                          "волны (только employee\u00d7month)")
        n = _num(dem.get("param"))
        if not isinstance(n, int) or n < 1:
            return None, (f"параметр {dem.get('param')!r} не даёт "
                          f"целого ≥ 1")
        sheet, cs, ce, gc = _DURATION_SHEETS["duty"]
        return {"template": "выходных-в-месяце",
                "params": {"sheet": f"{SOLUTION_SID}/{sheet}",
                           "group_column": gc,
                           "start_column": cs, "end_column": ce,
                           "min_days_off": n},
                "basis": {"rule": rule.get("id")}}, ""
    elif kind == "count_within":
        place = _COUNT_GROUPS.get(subj)
        if place is None:
            return None, (f"count_within: предмет {subj!r} вне "
                          f"первой волны переводов")
        sheet, group_col = place
        n = _num(dem.get("param"))
        if not isinstance(n, int):
            return None, (f"параметр {dem.get('param')!r} не даёт "
                          f"целого счёта")
        rel = dem.get("relation")
        if rel not in ("eq", "max", "min"):
            return None, f"count_within: relation {rel!r} вне перечня"
        template = "счёт-состава-группы"
        params = {"sheet": f"{SOLUTION_SID}/{sheet}",
                  "group_column": group_col, "count": n,
                  "relation": rel}
    elif kind in m.LOGIC_SOFT_KINDS:
        # Мягкие формы волны 1 (П-2 этап 2). Условия у мягких форм
        # волна не выражает — их отсекает ОБЩИЙ гейт невыраженных
        # condition-атомов выше (принцип «пара не должна доказывать
        # более широкое правило»); окно Ф-3 едет РОЛЯМИ ссылок.
        asg = f"{SOLUTION_SID}/Назначения"
        fl = f"{SOLUTION_SID}/Рейсы"
        if kind == "штраф-ступенчатый-за-стыковку":
            thr = _num(dem.get("threshold_param"))
            step = _num(dem.get("step_param"))
            if not isinstance(thr, int) or not isinstance(step, int) \
                    or step < 1:
                return None, ("роли threshold/step не дают целых "
                              "минут (шаг ≥ 1)")
            template = kind
            params = {"sheet": asg, "flights_sheet": fl,
                      "threshold_min": thr, "step_min": step}
        elif kind == "штраф-за-разрыв-смены":
            thr = _num(dem.get("threshold_param"))
            if not isinstance(thr, int):
                return None, "роль threshold не даёт целых минут"
            template = kind
            params = {"sheet": asg, "flights_sheet": fl,
                      "duties_sheet": f"{SOLUTION_SID}/Смены",
                      "threshold_min": thr}
        elif kind == "однородность-цепочки-ночь-день":
            w0 = _num(dem.get("window_from_param"))
            w1 = _num(dem.get("window_to_param"))
            if not isinstance(w0, int) or not isinstance(w1, int):
                return None, ("роли окна не дают целых минут от "
                              "полуночи")
            template = kind
            params = {"sheet": asg, "flights_sheet": fl,
                      "crews_sheet": f"{SOLUTION_SID}/Экипажи",
                      "window_from_min": w0, "window_to_min": w1}
        elif kind == "мягкий-предел-дней-подряд":
            bound = _num(dem.get("bound_param"))
            if not isinstance(bound, int) or bound < 1:
                return None, "роль bound не даёт целого ≥ 1"
            template = kind
            params = {"sheet": asg, "flights_sheet": fl,
                      "bound": bound}
        else:                    # неудовлетворённая-выполнимая-заявка
            template = kind
            params = {"sheet": asg, "flights_sheet": fl,
                      "crews_sheet": f"{SOLUTION_SID}/Экипажи",
                      "requests_sheet": f"{SOLUTION_SID}/Заявки"}
    else:
        return None, f"вид требования {kind!r} вне первой волны " \
                     f"переводов"
    return ({"id": f"C-logic-{rule.get('id')}",
             "basis": {"rule": rule.get("id")},
             "template": template, "params": params,
             "statement": m.render_logic(logic, rule),
             "unit": "строка решения", "severity": "КРИТИЧЕСКОЕ",
             "status": "скомпилирована"}, "")


def _param_entry(rule: dict, pref) -> dict | None:
    """Запись params правила по ссылке: строка — имя (одна запись),
    объект 1.6 {name, qualifier} — по обеим координатам; нет — None."""
    name, qual = pref, None
    if isinstance(pref, dict):
        name, qual = pref.get("name"), pref.get("qualifier")
    records, problems = m.logic_param_records(rule, selected_names={name} if isinstance(name, str) else set())
    if problems:
        return None
    for p in records:
        if isinstance(p, dict) and p.get("name") == name \
                and (not isinstance(pref, dict)
                     or p.get("qualifier") == qual):
            return p
    return None


def _ref_name(ref):
    return ref.get("name") if isinstance(ref, dict) else ref


def _int_value(rule: dict, pref):
    """Целое значение параметра по ссылке либо None (bool — не целое)."""
    entry = _param_entry(rule, pref)
    v = None if entry is None else entry.get("value")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _history_form_spec(rule: dict, logic: dict) \
        -> tuple[tuple[str, dict] | None, str]:
    """Перевод форм истории сотрудника в шаблоны проверок листа смен.
    Возвращает ((шаблон, параметры), "") — форма распознана;
    (None, "") — блок не этой формы (решают прочие ветви перевода);
    (None, причина) — почти-форма: очертание задето, но отклоняется
    от канона (fail-closed, пара доказывала бы другое правило).

    Очертания — word-в-word с LogicForms.formOf решателя:
    CONSECUTIVE_NIGHT_DUTIES_MAX — count_within/max над duty без
    универсума и окна, ровно два условия starts_in(от, до) и
    consecutive(unit=duty, param = param требования);
    REST_IN_ROLLING_WINDOW — exists_universe над rest по
    employee×rolling_window, окно rolling в сутках, ровно одно условие
    longer_than(порог). Числа — из params правила: минуты суток окна
    ночи 0..1439 (переход через полночь при от > до), предел серии —
    целое ≥ 0; порог отдыха — минуты ≥ 1, окно — сутки (count) либо
    минуты, кратные 1440."""
    dem = logic.get("demand") or {}
    conds = [c for c in (logic.get("condition") or [])
             if isinstance(c, dict)]
    preds = [c.get("predicate") for c in conds]
    window = logic.get("window")
    sheet, cs, ce, ec = _DURATION_SHEETS["duty"]
    place = {"sheet": f"{SOLUTION_SID}/{sheet}", "employee_column": ec,
             "start_column": cs, "end_column": ce}

    if "starts_in" in preds:
        tag = "серия ночных смен"
        if dem.get("kind") != "count_within" \
                or dem.get("relation") != "max":
            return None, (f"{tag}: требование {dem.get('kind')!r}/"
                          f"{dem.get('relation')!r} вне формы (только "
                          f"count_within с relation max)")
        if logic.get("subject") != "duty":
            return None, (f"{tag}: предмет {logic.get('subject')!r} "
                          f"вне формы (только duty)")
        if dem.get("universe") is not None or window is not None:
            return None, (f"{tag}: универсум или окно у формы не "
                          f"предусмотрены")
        if len(conds) != 2 or "consecutive" not in preds:
            return None, (f"{tag}: условия {sorted(map(str, preds))} — "
                          f"форма требует ровно starts_in и consecutive")
        starts = next(c for c in conds if c.get("predicate") == "starts_in")
        consec = next(c for c in conds
                      if c.get("predicate") == "consecutive")
        if consec.get("unit") != "duty":
            return None, (f"{tag}: consecutive по {consec.get('unit')!r}"
                          f" — форма считает по сменам (duty)")
        if _ref_name(consec.get("param")) != _ref_name(dem.get("param")):
            return None, (f"{tag}: число «подряд» и граница требования "
                          f"— разные параметры "
                          f"({_ref_name(consec.get('param'))!r} и "
                          f"{_ref_name(dem.get('param'))!r})")
        nf = _int_value(rule, starts.get("from_param"))
        nt = _int_value(rule, starts.get("to_param"))
        if nf is None or nt is None \
                or not (0 <= nf < 1440 and 0 <= nt < 1440):
            return None, (f"{tag}: окно ночи "
                          f"({starts.get('from_param')!r}, "
                          f"{starts.get('to_param')!r}) не даёт целых "
                          f"минут суток 0..1439")
        if nf == nt:
            return None, f"{tag}: окно ночи пусто (от = до = {nf})"
        bound = _int_value(rule, dem.get("param"))
        if bound is None or bound < 0:
            return None, (f"{tag}: параметр {dem.get('param')!r} не "
                          f"даёт целого ≥ 0")
        return ("серия-ночных-смен",
                dict(place, night_from_min=nf, night_to_min=nt,
                     max_consecutive=bound)), ""

    rolling_like = (dem.get("universe") == "employee×rolling_window"
                    or (isinstance(window, dict)
                        and window.get("kind") == "rolling"))
    if rolling_like:
        tag = "отдых в скользящем окне"
        if dem.get("kind") != "exists_universe":
            return None, (f"{tag}: требование {dem.get('kind')!r} вне "
                          f"формы (только exists_universe)")
        if logic.get("subject") != "rest" or dem.get("subject") != "rest":
            return None, (f"{tag}: предмет {logic.get('subject')!r}/"
                          f"{dem.get('subject')!r} вне формы (только "
                          f"rest)")
        if dem.get("universe") != "employee×rolling_window":
            return None, (f"{tag}: универсум {dem.get('universe')!r} "
                          f"вне формы (только employee×rolling_window)")
        if not isinstance(window, dict) or window.get("kind") != "rolling" \
                or window.get("unit") != "day":
            return None, (f"{tag}: окно {window!r} — форма требует "
                          f"kind=rolling, unit=day")
        if preds != ["longer_than"]:
            return None, (f"{tag}: условия {sorted(map(str, preds))} — "
                          f"форма требует ровно одно longer_than")
        need = _int_value(rule, conds[0].get("param"))
        if need is None or need < 1:
            return None, (f"{tag}: порог отдыха {conds[0].get('param')!r}"
                          f" не даёт целых минут ≥ 1")
        wp = _param_entry(rule, window.get("param"))
        wv = None if wp is None else wp.get("value")
        if not isinstance(wv, int) or isinstance(wv, bool) or wv < 1:
            return None, (f"{tag}: параметр окна {window.get('param')!r} "
                          f"не даёт целого ≥ 1")
        unit = wp.get("unit")
        if unit == "count":
            days = wv
        elif unit == "duration_minutes":
            if wv % 1440:
                return None, (f"{tag}: окно {wv} мин не кратно суткам "
                              f"(1440) — окно в сутках не читается")
            days = wv // 1440
        else:
            return None, (f"{tag}: единица окна {unit!r} — ожидаются "
                          f"count (сутки) либо duration_minutes, "
                          f"кратные 1440")
        if need > days * 1440:
            return None, (f"{tag}: порог отдыха {need} мин больше окна "
                          f"{days} суток — требование невыполнимо")
        return ("отдых-в-скользящем-окне",
                dict(place, window_days=days, min_rest_min=need)), ""
    return None, ""


def render_unproven_summary(records: list[dict] | None) -> str:
    """Human summary of recomputed proof facts, not execution permission."""
    if records is None:
        return "Недоказанных правил: не установлено (проверка не завершена)."
    lines = ["Контрпримерные пары для правил с logic.",
             f"Недоказанных правил: {len(records)}"]
    lines.extend(f"- {row['rule']}: {row['reason']}" for row in records)
    lines.append("Эта сводка не является допуском к исполнению.")
    return "\n".join(lines)


def generate_pair(rule: dict) -> tuple[dict | None, str]:
    """Контрпримерная пара: {dataset_bad, dataset_good, spec} либо
    (None, причина). Оба мини-решения — НА ОДНОМ листе-схеме, гейт
    пары механический (замечание COSMOS: пара проверяется обеими
    сторонами на одном входе)."""
    spec, why = logic_to_check_spec(rule)
    if spec is None:
        return None, why
    p = spec["params"]
    sheet = p["sheet"].split("/", 1)[1]

    def _ds(rows, columns):
        return {"sources": [{"sid": SOLUTION_SID,
                             "file": "logic-example.json",
                             "sha256": "0" * 64,
                             "rows_seen": len(rows)}],
                "tables": {SOLUTION_SID: {sheet: {
                    "columns": columns, "rows": rows}}}}

    if spec["template"] == "максимальная-длительность-часов":
        cs, ce = p["start_column"], p["end_column"]
        limit_min = int(p["max_hours"] * 60)
        base = "2030-01-01T06:00"
        good_end = _plus(base, limit_min - 30)
        bad_end = _plus(base, limit_min + 60)
        cols = ["Табельный номер", cs, ce]
        row = {"Табельный номер": "EX-1", cs: base}
        good = [dict(row, **{ce: good_end})]
        bad = [dict(row, **{ce: bad_end})]
        return {"dataset_bad": _ds(bad, cols),
                "dataset_good": _ds(good, cols), "spec": spec}, ""
    if spec["template"] == "минимальный-разрыв-часов":
        cs, ce, cg = (p["start_column"], p["end_column"],
                      p["group_column"])
        limit_min = int(p["min_hours"] * 60)
        cols = [cg, cs, ce]
        a_end = "2030-01-01T10:00"
        good = [{cg: "EX-1", cs: "2030-01-01T06:00", ce: a_end},
                {cg: "EX-1", cs: _plus(a_end, limit_min + 60),
                 ce: _plus(a_end, limit_min + 180)}]
        bad = [{cg: "EX-1", cs: "2030-01-01T06:00", ce: a_end},
               {cg: "EX-1", cs: _plus(a_end, limit_min - 30),
                ce: _plus(a_end, limit_min + 90)}]
        return {"dataset_bad": _ds(bad, cols),
                "dataset_good": _ds(good, cols), "spec": spec}, ""
    if spec["template"] == "счёт-состава-группы":
        cg, n, rel = p["group_column"], p["count"], p["relation"]
        cols = [cg]
        good_n = n
        bad_n = n + 1 if rel in ("eq", "max") else max(0, n - 1)
        if bad_n == good_n:
            return None, "пара неразличима при count=0"
        good = [{cg: "G-1"} for _ in range(good_n)] or [{cg: ""}]
        bad = [{cg: "G-1"} for _ in range(bad_n)] or [{cg: ""}]
        # Пустая группа не судится счётом — при bad_n=0 кладём одну
        # строку другой группы, чтобы универсум был непуст.
        if bad_n == 0:
            bad = [{cg: "G-2"}]
            return None, ("пара для min с нулевым нарушителем "
                          "требует универсума — вне первой волны")
        return {"dataset_bad": _ds(bad, cols),
                "dataset_good": _ds(good, cols), "spec": spec}, ""
    if spec["template"] == "выходных-в-месяце":
        gc, cs, ce = (p["group_column"], p["start_column"],
                      p["end_column"])
        need = int(p["min_days_off"])
        # Январь 2030: 31 сутки; смены — однодневные 08:00–12:00.
        # good: занято 31−need суток (выходных ровно need);
        # bad: занято 32−need (выходных need−1) — ровно одна клетка
        # нарушает. Границы месяца заняты в обоих, чтобы диапазон
        # данных накрывал месяц целиком.
        if need > 29:
            return None, ("выходные-в-месяце: норма выше 29 не "
                          "оставляет занятых границ месяца — пара "
                          "не строится")
        cols = [gc, cs, ce]

        def _rows(busy: int):
            days = [1, 31] + list(range(2, busy))
            return [{gc: "EX-1",
                     cs: f"2030-01-{d:02d}T08:00",
                     ce: f"2030-01-{d:02d}T12:00"}
                    for d in sorted(set(days))]

        return {"dataset_bad": _ds(_rows(32 - need), cols),
                "dataset_good": _ds(_rows(31 - need), cols),
                "spec": spec}, ""
    if spec["template"] == "серия-ночных-смен":
        ec, cs, ce = (p["employee_column"], p["start_column"],
                      p["end_column"])
        nf, nt, n = (p["night_from_min"], p["night_to_min"],
                     p["max_consecutive"])
        cols = [ec, cs, ce]

        def _duty(day: int, minute: int) -> dict:
            # Смена 4 часа одного сотрудника, по одной в сутки. Ночная
            # начинается ровно на границе «от» (принадлежит окну
            # [от, до)), дневная — в «до» (окну не принадлежит, в том
            # числе при переходе окна через полночь).
            start = _plus("2030-01-01T00:00", (day - 1) * 1440 + minute)
            return {ec: "EX-1", cs: start, ce: _plus(start, 240)}

        # bad: n+1 ночных подряд — ровно одна серия сверх предела.
        bad = [_duty(d, nf) for d in range(1, n + 2)]
        # good: n ночных, затем дневная; при n > 0 — ещё одна ночная
        # после дневной: ночных столько же, сколько в bad, но серия
        # прервана — пара различает «подряд» и «всего».
        good = [_duty(d, nf) for d in range(1, n + 1)] + [_duty(n + 1, nt)]
        if n > 0:
            good.append(_duty(n + 2, nf))
        return {"dataset_bad": _ds(bad, cols),
                "dataset_good": _ds(good, cols), "spec": spec}, ""
    if spec["template"] == "отдых-в-скользящем-окне":
        ec, cs, ce = (p["employee_column"], p["start_column"],
                      p["end_column"])
        days, need = int(p["window_days"]), int(p["min_rest_min"])
        cols = [ec, cs, ce]
        if need < 2:
            return None, ("отдых-в-скользящем-окне: порог отдыха меньше "
                          "2 минут — нарушающее решение не строится "
                          "(промежуток между сменами не короче минуты)")
        # bad: диапазон данных ровно window_days суток (одно окно);
        # смена каждые сутки одной длины L, выбранной так, что каждый
        # промежуток отдыха — между сменами и от границ суток —
        # короче порога: L ≥ 1441 − порог (и не короче 12 часов).
        length = min(1439, max(720, 1441 - need))
        s = (1440 - length) // 2

        def _duty(day: int, minute: int, dur: int) -> dict:
            start = _plus("2030-01-01T00:00", (day - 1) * 1440 + minute)
            return {ec: "EX-1", cs: start, ce: _plus(start, dur)}

        bad = [_duty(d, s, length) for d in range(1, days + 1)]
        # good: крайние сутки заняты (окно то же одно), середина
        # свободна — один непрерывный отдых не короче порога.
        if days >= 2 and (days - 1) * 1440 - length >= need:
            good = [_duty(1, s, length), _duty(days, s, length)]
        elif days >= 2 and days * 1440 - 120 >= need:
            good = [_duty(1, 0, 60), _duty(days, 23 * 60, 59)]
        elif days == 1 and 1440 - 60 >= need:
            good = [_duty(1, 0, 60)]
        else:
            return None, (f"отдых-в-скользящем-окне: порог {need} мин "
                          f"почти равен окну {days} суток — соблюдающее "
                          f"решение с занятыми крайними сутками не "
                          f"строится")
        return {"dataset_bad": _ds(bad, cols),
                "dataset_good": _ds(good, cols), "spec": spec}, ""
    if spec["template"] in dc.WAVE_SOFT_TEMPLATES:
        return _wave_pair(spec)
    return None, f"генератор пары не умеет шаблон {spec['template']!r}"


def _wave_pair(spec: dict) -> tuple[dict | None, str]:
    """Пары мягких форм волны 1: самодостаточные микромиры (все
    листы внутри), параметризованные значениями правила; ожидание
    единиц кладётся в spec.expected_units — гейт пары судит ЕДИНИЦЫ,
    не «ровно одно нарушение» (мягкая форма считает литералом 1)."""
    p = dict(spec["params"])
    t = spec["template"]

    def _fl(no, day, dep, td, arr, ta):
        return {"Номер рейса": no, "Дата": f"2030-01-{day:02d}",
                "Вылет": dep, "Время вылета": td,
                "Прилёт": arr, "Время прилёта": ta}

    def _hm(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    def _ds(flights, assigns, crews=None, duties=None, reqs=None):
        tabs = {
            "Рейсы": {"columns": ["Номер рейса", "Дата", "Вылет",
                                  "Время вылета", "Прилёт",
                                  "Время прилёта"], "rows": flights},
            "Назначения": {"columns": ["Табельный номер",
                                       "Номер рейса"],
                           "rows": assigns},
            "Экипажи": {"columns": ["Табельный номер", "База"],
                        "rows": crews or [
                            {"Табельный номер": "EX-1",
                             "База": "BAZ"}]},
        }
        if duties is not None:
            tabs["Смены"] = {"columns": ["Табельный номер",
                                         "Начало", "Конец"],
                             "rows": duties}
        if reqs is not None:
            tabs["Заявки"] = {"columns": ["Табельный номер",
                                          "Дата"], "rows": reqs}
        total = sum(len(v["rows"]) for v in tabs.values())
        return {"sources": [{"sid": SOLUTION_SID,
                             "file": "logic-example.json",
                             "sha256": "0" * 64,
                             "rows_seen": total}],
                "tables": {SOLUTION_SID: tabs}}

    def _asg(emp, *nos):
        return [{"Табельный номер": emp, "Номер рейса": n}
                for n in nos]

    if t == "штраф-ступенчатый-за-стыковку":
        thr, step = p["threshold_min"], p["step_min"]
        sit_bad = thr + 2 * step + step // 2      # 2 единицы
        if sit_bad + 120 >= 600:
            return None, ("порог+ступени упираются в границу отдыха "
                          "(600 мин) — пара не строится")
        dep1, arr1 = 8 * 60, 9 * 60
        b2 = arr1 + sit_bad
        g2 = arr1 + max(1, thr - 10)
        bad = [_fl("F1", 1, "BAZ", _hm(dep1), "P01", _hm(arr1)),
               _fl("F2", 1, "P01", _hm(b2), "BAZ", _hm(b2 + 60))]
        good = [_fl("F1", 1, "BAZ", _hm(dep1), "P01", _hm(arr1)),
                _fl("F2", 1, "P01", _hm(g2), "BAZ", _hm(g2 + 60))]
        pair = {"dataset_bad": _ds(bad, _asg("EX-1", "F1", "F2")),
                "dataset_good": _ds(good, _asg("EX-1", "F1", "F2")),
                "spec": dict(spec, expected_units={"bad": 2,
                                                   "good": 0})}
        return pair, ""
    if t == "штраф-за-разрыв-смены":
        thr = p["threshold_min"]
        if thr + 120 >= 24 * 60:
            return None, "порог разрыва не влезает в сутки — пары нет"
        arr1 = 9 * 60
        b2 = arr1 + thr + 30
        bad_fl = [_fl("F1", 1, "BAZ", _hm(8 * 60), "P01", _hm(arr1)),
                  _fl("F2", 1, "P01", _hm(b2), "BAZ", _hm(b2 + 60))]
        bad_du = [{"Табельный номер": "EX-1",
                   "Начало": "2030-01-01 07:00",
                   "Конец": f"2030-01-01 {_hm(b2 + 120)}"}]
        good_fl = [_fl("F1", 1, "BAZ", _hm(8 * 60), "P01",
                       _hm(arr1)),
                   _fl("F2", 2, "P01", "09:30", "BAZ", "10:30")]
        good_du = [{"Табельный номер": "EX-1",
                    "Начало": "2030-01-01 07:00",
                    "Конец": "2030-01-01 10:00"},
                   {"Табельный номер": "EX-1",
                    "Начало": "2030-01-02 08:30",
                    "Конец": "2030-01-02 11:30"}]
        pair = {"dataset_bad": _ds(bad_fl,
                                   _asg("EX-1", "F1", "F2"),
                                   duties=bad_du),
                "dataset_good": _ds(good_fl,
                                    _asg("EX-1", "F1", "F2"),
                                    duties=good_du),
                "spec": dict(spec, expected_units={"bad": 1,
                                                   "good": 0})}
        return pair, ""
    if t == "однородность-цепочки-ночь-день":
        w0 = p["window_from_min"]
        day_dep = (p["window_to_min"] + 4 * 60) % (24 * 60)
        if not (0 <= w0 < 24 * 60):
            return None, "окно вне суток — пары нет"
        night_dep = w0 + 30
        bad = [_fl("F1", 1, "BAZ", _hm(day_dep), "P01",
                   _hm(day_dep + 60)),
               _fl("F2", 2, "P01", _hm(night_dep), "BAZ",
                   _hm(night_dep + 50))]
        good = [_fl("F1", 1, "BAZ", _hm(day_dep), "P01",
                    _hm(day_dep + 60)),
                _fl("F3", 1, "P01", _hm(day_dep + 120), "BAZ",
                    _hm(day_dep + 180)),
                _fl("F4", 2, "BAZ", _hm(night_dep), "P01",
                    _hm(night_dep + 50)),
                _fl("F5", 2, "P01", _hm(night_dep + 70), "BAZ",
                    _hm(night_dep + 120))]
        pair = {"dataset_bad": _ds(bad, _asg("EX-1", "F1", "F2")),
                "dataset_good": _ds(good, _asg("EX-1", "F1", "F3",
                                               "F4", "F5")),
                "spec": dict(spec, expected_units={"bad": 1,
                                                   "good": 0})}
        return pair, ""
    if t == "мягкий-предел-дней-подряд":
        bound = p["bound"]
        if bound > 25:
            return None, "предел дней выше 25 не влезает в месяц пары"
        def _days(skip: int | None, total: int):
            fls, asgs = [], []
            for d in range(1, total + 1):
                if skip is not None and d == skip:
                    continue
                fls.append(_fl(f"F{d}", d, "BAZ", "10:00", "P01",
                               "11:00"))
                asgs += _asg("EX-1", f"F{d}")
            return fls, asgs
        bf, ba = _days(None, bound + 2)          # 2 единицы сверх
        gf, ga = _days(bound + 1, bound + 2)     # разрыв после bound
        pair = {"dataset_bad": _ds(bf, ba),
                "dataset_good": _ds(gf, ga),
                "spec": dict(spec, expected_units={"bad": 2,
                                                   "good": 0})}
        return pair, ""
    if t == "неудовлетворённая-выполнимая-заявка":
        crews = [{"Табельный номер": "EX-1", "База": "BAZ"},
                 {"Табельный номер": "EX-2", "База": "BAZ"}]
        fls = [_fl("F1", 5, "BAZ", "10:00", "P01", "11:00"),
               _fl("F2", 5, "P01", "13:00", "BAZ", "14:00")]
        rq = [{"Табельный номер": "EX-1", "Дата": "2030-01-05"}]
        pair = {"dataset_bad": _ds(fls, _asg("EX-1", "F1", "F2"),
                                   crews=crews, reqs=rq),
                "dataset_good": _ds(fls, _asg("EX-2", "F1", "F2"),
                                    crews=crews, reqs=rq),
                "spec": dict(spec, expected_units={"bad": 1,
                                                   "good": 0})}
        return pair, ""
    return None, f"мягкая пара не умеет шаблон {t!r}"


def _plus(iso_minute: str, minutes: int) -> str:
    from datetime import datetime, timedelta
    dt = datetime.fromisoformat(iso_minute) + timedelta(
        minutes=minutes)
    return dt.isoformat(timespec="minutes")


def check_pair(pair: dict, rule_id: str, *, rule: dict | None = None) -> list[str]:
    """Механический гейт пары (глава 2, рубеж 2, формула COSMOS):
    нарушающее решение даёт РОВНО ОДНО нарушение и именно этого
    правила; соблюдающее — ноль. Возвращает нарушения гейта.

    Без rule проверяется только внутренняя согласованность пары. Выпуск и
    приём пакета передают полное правило: чужой предикат или ожидаемый счёт
    нарушений не становится доказательством от совпадения одного rule_id.
    """
    problems: list[str] = []
    spec = pair.get("spec") or {}
    if rule is not None:
        expected_pair, why = generate_pair(rule)
        if expected_pair is None:
            return [f"пара {rule_id}: логика правила не доказана — {why}"]
        try:
            actual = json.dumps(spec, sort_keys=True, ensure_ascii=False,
                                allow_nan=False)
            expected = json.dumps(expected_pair["spec"], sort_keys=True,
                                  ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return [f"пара {rule_id}: спецификация не является конечным JSON"]
        if actual != expected:
            return [f"пара {rule_id}: спецификация не соответствует "
                    "логике и параметрам правила пакета"]
    if (spec.get("basis") or {}).get("rule") != rule_id:
        problems.append(f"пара {rule_id}: спецификация ссылается на "
                        f"{(spec.get('basis') or {}).get('rule')!r}")
        return problems
    # Мягкие формы волны считают ЕДИНИЦАМИ (литерал 1): ожидание
    # пары — из spec.expected_units; жёсткие пары — прежнее «ровно
    # одно нарушение».
    exp = spec.get("expected_units") or {"bad": 1, "good": 0}
    try:
        _n, bad = dc.run_spec(spec, pair["dataset_bad"])
    except Exception as e:                                # noqa: BLE001
        return [f"пара {rule_id}: нарушающее не прогналось: {e}"]
    if len(bad) != exp["bad"]:
        problems.append(f"пара {rule_id}: нарушающее дало "
                        f"{len(bad)} единиц вместо {exp['bad']}")
    try:
        _n, good = dc.run_spec(spec, pair["dataset_good"])
    except Exception as e:                                # noqa: BLE001
        return [f"пара {rule_id}: соблюдающее не прогналось: {e}"]
    if good:
        problems.append(f"пара {rule_id}: соблюдающее дало "
                        f"{len(good)} нарушений вместо нуля")
    return problems


_PROMPT_RETELL = """Ты — сверщик эквивалентности. Ниже пары: строка
свода правил авиакомпании и машинный пересказ её логики. По КАЖДОЙ
паре ответь, эквивалентны ли они ПО СМЫСЛУ (одно и то же правило,
те же числа, та же область действия). ОСОБО: если формулировка свода несёт УСЛОВИЕ или ОГРАНИЧИТЕЛЬ («ночных», «вне базы», «после продлённой», «в месяц»), которого в пересказе НЕТ, — это расхождение (пересказ шире правила), даже если числа совпадают: пересказ строится только из машинных полей, потерянный ограничитель = потерянный машинный атом. Верни СТРОГО один JSON-объект:

{{"verdicts": [{{"rule": "<id>", "equivalent": true}} |
  {{"rule": "<id>", "equivalent": false,
   "discrepancy": "<цитатой: что в своде и что в пересказе разошлось>"
  }}, …]}}

Пары ({n}):
{items}""" + m.ID_IMMUTABILITY_RULE


def retell_check(doc: dict, llm, control=None) -> dict:
    """Рубеж 1: обратный пересказ против формулировки — независимой
    моделью. Вердикт — МАТЕРИАЛ ЧЕЛОВЕКУ, не гейт («сводка погоды»
    запрещена): расхождение блокирует правило вопросом анкеты
    (часть 2, развилка «свод против пересказа»), совпадение лишь
    допускает к рубежу 2. Возвращает сводку.

    Invalid input is not a competing interpretation of the airline's rule.
    It is returned as addressed ``unreadable`` problems, never sent to the
    verifier or offered as a business choice. The input is not repaired or
    approved here: model/package validation still rejects the same logic.
    ``checked`` counts only readable rules actually sent to the verifier.
    """
    # Правило, чей вопрос «свод против пересказа» уже отвечен и
    # применён, рассужено ЧЕЛОВЕКОМ: повторный пересказ переблокировал
    # бы его тем же вопросом и уронил инвариант «применён, но
    # заблокирован» (живой урок run-0050).
    adjudicated = {
        rid
        for q in doc.get("questions", [])
        if (q.get("answer") or {}).get("status") == "применён"
        for rid in (q.get("rule_refs") or [])
        if q.get("id") == m.stable_question_id("2", f"лог-{rid}")}
    candidates = [r for r in doc.get("rules", [])
                  if isinstance(r.get("logic"), dict)
                  and not r.get("blockers")
                  and r["id"] not in adjudicated]
    targets, retellings, unreadable = [], [], []
    for r in candidates:
        problems = m.validate_logic(r["logic"], r)
        if problems:
            unreadable.append({"rule": r["id"], "problems": problems})
        else:
            targets.append(r)
            retellings.append(m.render_logic(r["logic"], r))
    # Optional only on an actual input failure: healthy-call compatibility.
    extra = {"unreadable": unreadable} if unreadable else {}
    if not targets:
        return {"checked": 0, "equivalent": 0, "blocked": [],
                "adjudicated": sorted(adjudicated), **extra}
    items = []
    for r, retelling in zip(targets, retellings):
        items.append(f"{r['id']}\nСВОД: {r.get('statement', '')}\n"
                     f"ПЕРЕСКАЗ: {retelling}")
    prompt = _PROMPT_RETELL.format(n=len(targets),
                                   items="\n\n".join(items))
    reply = None
    for attempt in (1, 2):
        ask = prompt if attempt == 1 else (
            prompt + "\n\nПредыдущий ответ не разобрался. Верни "
                     "СТРОГО один JSON-объект.")
        try:
            reply = llm_mod.parse_json_reply(llm(ask))
            break
        except llm_mod.LLMReplyError:
            continue
    got = {v.get("rule"): v
           for v in (reply or {}).get("verdicts", [])}
    blocked: list[str] = []
    equivalent = 0
    for r, retelling in zip(targets, retellings):
        v = got.get(r["id"])
        if v is None or v.get("equivalent") is not True:
            # Молчание сверщика = расхождение (fail-closed).
            disc = (v or {}).get("discrepancy") or \
                "сверщик не дал вердикта"
            qid = m.stable_question_id("2", f"лог-{r['id']}")
            if not any(q.get("id") == qid
                       for q in doc.get("questions", [])):
                doc.setdefault("questions", []).append({
                    "id": qid, "part": "2",
                    "title": f"Логика правила {r['id']}: свод "
                             f"против пересказа",
                    "text": (f"Машинный пересказ логики разошёлся с "
                             f"формулировкой свода: {disc}. Какое "
                             f"чтение верно?"),
                    "answer_format": {"type": "choice", "options": [
                        {"key": "а", "text": f"формулировка свода: "
                         f"{r.get('statement') or ''}"},
                        {"key": "б", "text": f"машинный пересказ: "
                         f"{retelling}"},
                    ]},
                    "rule_refs": [r["id"]],
                    "source_refs": [dict(s) for s in
                                    (r.get("sources") or [])[:1]],
                })
            bl = r.setdefault("blockers", [])
            if qid not in bl:
                bl.append(qid)
            blocked.append(r["id"])
        else:
            equivalent += 1
    return {"checked": len(targets), "equivalent": equivalent,
            "blocked": blocked, "adjudicated": sorted(adjudicated), **extra}
