"""Ступень 3, шаг 0 — живая приёмка раскладки logic на решателе.

Эталоны (нано/мини/Лайт) блоков logic пока не несут — производитель
перевыпустит их реэкстракцией. До того приёмка шла бы «зелёной на
нуле»: новый код не запускался бы ни разу. Поэтому фикстура строится
ЗДЕСЬ из эталона нано: в model.json добавляются блоки logic к правилам
с ручками (и один нарочно непригодный, post-check), контрольные суммы
манифеста пересчитываются, версия спецификации поднимается до 1.5.
Фикстура пишется во временный каталог, не в handoff.

Что проверяется живьём (числа — из машинного отчёта, не из лога):
  1. прогон по фикстуре стартует (ОТВЕРГНУТО пусто), lastRun.logic
     несёт корзины и registered от фабрики;
  2. гейт webapp/logic_gate.py сходится по каждому правилу с logic;
  3. POST /schedules под пакетом — отказ 400 с причиной (путь мимо окна);
  4. POST /schedules/score того же ростера под пакетом считается
     (второй вход судит те же корзины и тот же fit).

Часть 2 — ПОРОЖДЁННАЯ ФОРМА «выходных в месяц не менее N» (шаг 1):
  5. нано (7 суток, ни одного полного месяца): правило required —
     отказ прогона с ПУСТО; post-check — «не исполняется» с ПУСТО;
  6. Лайт (полный март): два правила свода о выходных сводятся в ОДИН
     исполнитель (СВЕДЕНО), имя зарегистрировано фабрикой, клеток =
     сотрудников × 1;
  7. доказательство вклада на ОДНОМ ростере тремя пакетами:
     вклад порождённого по имени (POST /schedules/score-breakdown) равен
     −вес × Σ нехватки независимого оракула (webapp/logic_oracle.py) при
     норме 8 и при норме 31 (вторая заставляет нарушения быть);
     пакет БЕЗ блока logic даёт жёсткий счёт ровно на вклад больше
     (изоляция), и имени logic у его фабрики нет.

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_v_logic
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from tools import most_v_thresholds as mv                      # noqa: E402
from webapp import logic_gate as lg                            # noqa: E402
from webapp import logic_oracle as lo                          # noqa: E402
from webapp import rule_maps as rmaps                          # noqa: E402
from webapp import solution_export as se                       # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

NANO = mv.HANDOFF / "etalon-nano" / "run-0027-task-package.zip"
LIGHT = mv.HANDOFF.parent / "2026-08-23-LIGHT" / "light-task-package.zip"
LIGHT_BUDGET_S = 30

#: Два правила Лайта о минимуме выходных: у производителя параметра
#: для них пока нет (словарь PARAMS без имени), поэтому фикстура несёт и
#: параметр, и блок logic. Два правила — одна форма — один исполнитель.
#: ЛЕГАСИ-константы (эта и четыре ниже): пакет ревизии 1.14 несёт
#: правила приёмки СЕКЦИЕЙ rule_maps.acceptance_rules манифеста, и
#: тогда берутся её значения (см. part2); константы остаются для
#: пакетов без секции — эталон LIGHT заморожен на спецификации до 1.14.
DAYS_OFF_RULES = ("R-S1-табл-4-строка-5-кол-3", "R-S3-стр-1-абз-8")
#: Правило Лайта о пределе подряд идущих рабочих дней.
CONSEC_RULE = "R-S1-табл-3-строка-18-кол-3"
#: Правило Лайта об отдыхе после продлённой смены.
REST_RULE = "R-S1-табл-3-строка-15-кол-3"
#: Правило Лайта о непрерывном отдыхе в скользящем окне.
ROLL_RULE = "R-S1-табл-4-строка-4-кол-3"
#: Правило Лайта о пределе подряд идущих ночных смен.
NIGHT_RULE = "R-S1-табл-3-строка-9-кол-3"


def days_off_logic(bound: int, enforce: str = "required") -> dict:
    return {
        "subject": "day_off",
        "demand": {"kind": "count_within", "relation": "min", "param": "min_days_off",
                   "universe": "employee×month", "as_written": "не менее %d" % bound},
        "window": {"kind": "month"},
        "source_params": ["min_days_off"],
        "enforce": enforce,
    }


def days_off_param(bound: int) -> dict:
    return {"name": "min_days_off", "value": bound, "unit": "count",
            "relation": "min", "as_written": str(bound)}


def consec_logic(bound: int, enforce: str = "required") -> dict:
    return {
        "subject": "day",
        "condition": [{"predicate": "consecutive", "unit": "day",
                       "param": "max_consecutive_days", "as_written": "подряд"}],
        "demand": {"kind": "count_within", "relation": "max", "param": "max_consecutive_days",
                   "as_written": "не более %d подряд" % bound},
        "source_params": ["max_consecutive_days"],
        "enforce": enforce,
    }


def consec_param(bound: int) -> dict:
    return {"name": "max_consecutive_days", "value": bound, "unit": "count",
            "relation": "max", "as_written": str(bound)}


def rest_logic(long_min: int, rest_min: int, enforce: str = "required") -> dict:
    return {
        "subject": "rest",
        "condition": [
            {"predicate": "follows", "subject": "duty", "as_written": "после смены"},
            {"predicate": "longer_than", "param": "long_duty_threshold",
             "as_written": "свыше %d мин" % long_min}],
        "demand": {"kind": "duration_within", "relation": "min", "param": "min_rest_after_long",
                   "as_written": "не менее %d мин" % rest_min},
        "source_params": ["long_duty_threshold", "min_rest_after_long"],
        "enforce": enforce,
    }


def roll_logic(rest_min: int, enforce: str = "required") -> dict:
    return {
        "subject": "rest",
        "condition": [{"predicate": "longer_than", "param": "weekly_rest_required",
                       "as_written": "не менее %d мин" % rest_min}],
        "demand": {"kind": "exists_universe", "subject": "rest",
                   "universe": "employee×rolling_window", "as_written": "в каждом окне"},
        "window": {"kind": "rolling", "unit": "day", "param": "weekly_rest_period"},
        "source_params": ["weekly_rest_required", "weekly_rest_period"],
        "enforce": enforce,
    }


def roll_params(rest_min: int) -> list[dict]:
    # Живой Лайт несёт weekly_rest_period МИНУТАМИ (10080 = 7 суток).
    return [{"name": "weekly_rest_required", "value": rest_min, "unit": "duration_minutes",
             "relation": "min", "as_written": str(rest_min)},
            {"name": "weekly_rest_period", "value": 10080, "unit": "duration_minutes",
             "relation": "window", "as_written": "10080"}]


def night_logic(bound: int, enforce: str = "required") -> dict:
    return {
        "subject": "duty",
        "condition": [
            {"predicate": "starts_in", "from_param": "night_duty_start",
             "to_param": "night_duty_end", "as_written": "с 22:00 до 06:00"},
            {"predicate": "consecutive", "unit": "duty",
             "param": "max_consecutive_night_duties", "as_written": "подряд"}],
        "demand": {"kind": "count_within", "relation": "max",
                   "param": "max_consecutive_night_duties", "as_written": "не более"},
        "source_params": ["night_duty_start", "night_duty_end", "max_consecutive_night_duties"],
        "enforce": enforce,
    }


def night_params(bound: int) -> list[dict]:
    return [{"name": "night_duty_start", "value": 1320, "unit": "time_of_day_minutes",
             "relation": "min", "as_written": "22:00"},
            {"name": "night_duty_end", "value": 360, "unit": "time_of_day_minutes",
             "relation": "max", "as_written": "06:00"},
            {"name": "max_consecutive_night_duties", "value": bound, "unit": "count",
             "relation": "max", "as_written": str(bound)}]


def rest_params(long_min: int, rest_min: int) -> list[dict]:
    return [{"name": "long_duty_threshold", "value": long_min, "unit": "duration_minutes",
             "relation": "max", "as_written": str(long_min)},
            {"name": "min_rest_after_long", "value": rest_min, "unit": "duration_minutes",
             "relation": "min", "as_written": str(rest_min)}]

#: Блоки logic для фикстуры: правило эталона -> блок. Все числа — ссылками
#: на params СВОЕГО правила; четвёртый блок нарочно просит НАЛЁТ у ручки
#: размаха и объявлен post-check — обязан лечь в «не исполняется».
LOGIC_BLOCKS: dict[str, dict] = {
    "R-S1-табл-3-строка-2-кол-3": {
        "subject": "duty",
        "demand": {"kind": "duration_within", "relation": "max",
                   "param": "max_duty_duration", "as_written": "не более 12:00"},
        "source_params": ["max_duty_duration"],
        "enforce": "required",
    },
    "R-S1-табл-2-строка-3-кол-3": {
        "subject": "flight",
        "demand": {"kind": "count_within", "relation": "eq",
                   "param": "crew_size", "as_written": "ровно 2"},
        "source_params": ["crew_size"],
        "enforce": "required",
    },
    "R-S1-табл-3-строка-4-кол-3": {
        "subject": "rest",
        "condition": [{"predicate": "at_base", "as_written": "в базе"}],
        "demand": {"kind": "duration_within", "relation": "min",
                   "param": "min_rest_home", "as_written": "не менее 12:00"},
        "source_params": ["min_rest_home"],
        "enforce": "required",
    },
    "R-S1-табл-3-строка-2-кол-5": {
        "subject": "duty",
        "demand": {"kind": "duration_within", "relation": "max",
                   "param": "max_duty_duration", "measure": "block",
                   "as_written": "налёт не более 12:00"},
        "source_params": ["max_duty_duration"],
        "enforce": "post-check",
    },
}


def build_fixture(src: Path, dst: Path, blocks: dict[str, dict] = LOGIC_BLOCKS,
                  params: dict[str, list[dict]] | None = None,
                  extra_rules: list[dict] | None = None) -> Path:
    """Эталон + блоки logic (+ параметры, + добавленные правила) -> подписанный пакет."""
    with zipfile.ZipFile(src) as z:
        files = {n: z.read(n) for n in z.namelist()}
    model = json.loads(files["model.json"].decode("utf-8"))
    for extra in (extra_rules or []):
        # Копия первого жёсткого правила модели с новыми полями: структура
        # правила — производителя, нам нужны только id, вид, params и logic.
        base = next(r for r in model.get("rules", []) if r.get("kind") == "HARD")
        r = json.loads(json.dumps(base, ensure_ascii=False))
        r.update(extra)
        model.setdefault("rules", []).append(r)
    seen = set()
    for r in model.get("rules", []):
        extra = (params or {}).get(r.get("id"))
        if extra:
            # Запись фикстуры ЗАМЕЩАЕТ родную запись того же имени: иначе
            # выкрученная граница молча проигрывает существующей.
            fresh = json.loads(json.dumps(extra, ensure_ascii=False))
            names = {p_.get("name") for p_ in fresh}
            kept = [p_ for p_ in (r.get("params") or [])
                    if not (isinstance(p_, dict) and p_.get("name") in names)]
            r["params"] = kept + fresh
        b = blocks.get(r.get("id"))
        if b is not None:
            r["logic"] = json.loads(json.dumps(b, ensure_ascii=False))
            seen.add(r["id"])
    missing = sorted(set(blocks) - seen)
    if missing:
        raise SystemExit("в эталоне нет правил для блоков logic: %s" % missing)
    files["model.json"] = json.dumps(model, ensure_ascii=False, indent=1).encode("utf-8")
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    manifest["spec_version"] = "1.5"
    manifest["logic_unproven"] = []
    for f in manifest.get("files", []):
        if f["name"] in files:
            f["sha256"] = hashlib.sha256(files[f["name"]]).hexdigest()
            f["bytes"] = len(files[f["name"]])
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
    return dst


def _post(path: str, payload: dict, timeout: int) -> tuple[int, str]:
    req = urllib.request.Request(
        mv.BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _breakdown(solved: dict) -> dict:
    code, body = _post("/schedules/score-breakdown", solved, 300)
    if code != 200:
        raise SystemExit("score-breakdown: HTTP %d %s" % (code, body[:400]))
    return json.loads(body)


def _outcome(results: list, name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print("[%s] %-44s %s" % ("ГОДЕН " if ok else "ДЕФЕКТ", name, detail))


def part2(tmp: Path, results: list) -> None:
    """Порождённая форма: нано (ПУСТО) и Лайт (доказательство вклада)."""
    # 5. Нано: ни одного полного месяца.
    nano_pkg = tp.load(NANO, run_gate=False)
    nano_payload = {k: v for k, v in tp.to_payload(nano_pkg).items() if not k.startswith("_")}
    nano_rule = "R-FIX-выходные"
    for enforce, expect in (("required", "отказ"), ("post-check", "ignored")):
        fx = build_fixture(NANO, tmp / ("nano-daysoff-%s.zip" % enforce), blocks={}, params={},
                           extra_rules=[{"id": nano_rule, "title": "Минимум выходных в месяц (фикстура)",
                                         "statement": "Количество выходных дней в месяце не менее 8.",
                                         "kind": "HARD", "params": [days_off_param(8)],
                                         "logic": days_off_logic(8, enforce)}])
        proc = mv.start_jvm(str(fx))
        try:
            code, body = _post("/schedules/single?mode=feasibility-soft&spentLimitSeconds=3",
                               nano_payload, 120)
            if expect == "отказ":
                _outcome(results, "5а) нано required: отказ ПУСТО",
                         code == 400 and "ПУСТО" in body, "HTTP %d: %s" % (code, body[:160]))
            else:
                logic = (mv.applied().get("lastRun") or {}).get("logic") or {} if code == 200 else {}
                ign = [e for e in logic.get("ignored") or [] if e.get("rule") == nano_rule]
                ok = code == 200 and len(ign) == 1 and str(ign[0].get("reason", "")).startswith("ПУСТО") \
                    and not logic.get("generated")
                _outcome(results, "5б) нано post-check: НЕ ИСПОЛНЯЕТСЯ ПУСТО", ok,
                         "HTTP %d, ignored=%d, generated=%d" % (
                             code, len(logic.get("ignored") or []), len(logic.get("generated") or [])))
        finally:
            mv.stop_jvm(proc)

    # 6–7. Лайт: три пакета, один ростер.
    light_pkg = tp.load(LIGHT, run_gate=False)
    # Правила приёмки: подсекция rule_maps.acceptance_rules манифеста
    # (ревизия 1.14, карта пакета) — когда она есть; нет подсекции —
    # легаси-константы модуля (эталон LIGHT заморожен на спецификации
    # до 1.14). Кривая форма — отказ словами (SystemExit инструмента).
    acc = rmaps.acceptance_rules(light_pkg["manifest"], error=SystemExit)
    days_off_rules = tuple(acc["days_off"]) if acc else DAYS_OFF_RULES
    consec_rule = acc["consecutive"] if acc else CONSEC_RULE
    rest_rule = acc["rest"] if acc else REST_RULE
    roll_rule = acc["rolling"] if acc else ROLL_RULE
    night_rule = acc["night"] if acc else NIGHT_RULE
    payload = {k: v for k, v in tp.to_payload(light_pkg).items() if not k.startswith("_")}
    blocks8 = {r: days_off_logic(8) for r in days_off_rules}
    blocks8[consec_rule] = consec_logic(5)
    blocks8[rest_rule] = rest_logic(720, 840)
    blocks8[roll_rule] = roll_logic(2160)
    blocks8[night_rule] = night_logic(2)
    params8 = {r: [days_off_param(8)] for r in days_off_rules}
    params8[consec_rule] = [consec_param(5)]
    params8[rest_rule] = rest_params(720, 840)
    params8[roll_rule] = roll_params(2160)
    params8[night_rule] = night_params(2)
    fx8 = build_fixture(LIGHT, tmp / "light-daysoff-8.zip", blocks=blocks8, params=params8)
    blocks31 = {r: days_off_logic(31) for r in days_off_rules}
    blocks31[consec_rule] = consec_logic(2)
    blocks31[rest_rule] = rest_logic(300, 2000)
    blocks31[roll_rule] = roll_logic(9000)
    blocks31[night_rule] = night_logic(1)
    params31 = {r: [days_off_param(31)] for r in days_off_rules}
    params31[consec_rule] = [consec_param(2)]
    params31[rest_rule] = rest_params(300, 2000)
    params31[roll_rule] = roll_params(9000)
    params31[night_rule] = night_params(1)
    fx31 = build_fixture(LIGHT, tmp / "light-daysoff-31.zip", blocks=blocks31, params=params31)
    fx0 = build_fixture(LIGHT, tmp / "light-nologic.zip", blocks={}, params={})
    pkg8 = tp.load(fx8, run_gate=False)

    proc = mv.start_jvm(str(fx8))
    try:
        code, body = _post("/schedules/single?mode=feasibility-soft&spentLimitSeconds=%d"
                           % LIGHT_BUDGET_S, payload, LIGHT_BUDGET_S + 300)
        if code != 200:
            _outcome(results, "6) Лайт: прогон с порождённой формой", False, "HTTP %d %s" % (code, body[:300]))
            return
        solved = json.loads(body)
        rep = mv.applied()
        logic = (rep.get("lastRun") or {}).get("logic") or {}
        verdicts = lg.check(pkg8, logic)
        print(lg.report(verdicts, considered=len(lg.logic_rules(pkg8))))
        plans = logic.get("plans") or []
        gen = logic.get("generated") or []
        reg = logic.get("registered") or []
        by_form = {p_["form"]: p_ for p_ in plans}
        do_plan = by_form.get("DAYS_OFF_PER_MONTH_MIN")
        run_plan = by_form.get("CONSECUTIVE_DAYS_MAX")
        rest_plan = by_form.get("REST_AFTER_LONG_DUTY_MIN")
        roll_plan = by_form.get("REST_IN_ROLLING_WINDOW")
        night_plan = by_form.get("CONSECUTIVE_NIGHT_DUTIES_MAX")
        one_plan = (len(plans) == 5 and do_plan and run_plan and rest_plan and roll_plan and
                    night_plan and
                    sorted(do_plan.get("rules") or []) == sorted(days_off_rules) and
                    (run_plan.get("rules") or []) == [consec_rule] and
                    (rest_plan.get("rules") or []) == [rest_rule] and
                    (roll_plan.get("rules") or []) == [roll_rule] and
                    (night_plan.get("rules") or []) == [night_rule] and
                    int(roll_plan.get("condBound") or 0) == 7 and
                    int(night_plan.get("nightFrom") or 0) == 1320)
        cid = do_plan["constraint"] if do_plan else None
        cidr = run_plan["constraint"] if run_plan else None
        cidt = rest_plan["constraint"] if rest_plan else None
        cidw = roll_plan["constraint"] if roll_plan else None
        cidn = night_plan["constraint"] if night_plan else None
        employees = len(payload.get("employees") or [])
        cells_ok = len(gen) == 6 and all(int(e.get("considered") or 0) >= employees for e in gen)
        _outcome(results, "6) Лайт: выходные + серии + отдых + окно + ночные, имена у фабрики",
                 lg.is_green(verdicts) and bool(one_plan) and cid in reg and cidr in reg and
                 cidt in reg and cidw in reg and cidn in reg and cells_ok,
                 "планов %d, generated %d, в registered: %s %s %s %s %s, клеток %s (сотрудников %d)" % (
                     len(plans), len(gen), cid in reg, cidr in reg, cidt in reg, cidw in reg,
                     cidn in reg, [e.get("considered") for e in gen], employees))
        rest_thr = se.min_rest_from_applied(rep)
        bd8 = _breakdown(solved)
        contrib8 = bd8["constraints"].get(cid, {}).get("hard")
        contrib_r5 = bd8["constraints"].get(cidr, {}).get("hard")
        weight = int(do_plan["weight"]) if do_plan else 1
        rows8 = lo.days_off_per_month(payload, solved, 8)
        want8 = lo.expected_hard(rows8, weight)
        _outcome(results, "7а) выходные, норма 8: вклад == −вес×Σ оракула", contrib8 == want8,
                 "вклад %s, оракул %d (нехватка %d по %d клеткам)" % (
                     contrib8, want8, lo.shortfall_total(rows8), len(rows8)))
        runs5 = lo.consecutive_runs(payload, solved, 5)
        want_r5 = -int(run_plan["weight"] if run_plan else 1) * lo.overage_total(runs5)
        _outcome(results, "7г) серии, граница 5: вклад == −вес×Σ оракула", contrib_r5 == want_r5,
                 "вклад %s, оракул %d (превышение %d по %d сериям)" % (
                     contrib_r5, want_r5, lo.overage_total(runs5), len(runs5)))
        contrib_t = bd8["constraints"].get(cidt, {}).get("hard")
        rest_rows = lo.rest_after_long_duty(payload, solved, 720, 840, rest_thr)
        want_t = -int(rest_plan["weight"] if rest_plan else 1) * lo.rest_shortfall_total(rest_rows)
        _outcome(results, "7е) отдых после продлённой (720/840): вклад == оракулу",
                 contrib_t == want_t,
                 "вклад %s, оракул %d (недобор %d мин по %d длинным сменам)" % (
                     contrib_t, want_t, lo.rest_shortfall_total(rest_rows), len(rest_rows)))
        contrib_w = bd8["constraints"].get(cidw, {}).get("hard")
        roll_rows = lo.rest_in_rolling_windows(payload, solved, 7, 2160)
        want_w = -int(roll_plan["weight"] if roll_plan else 1) * lo.violated_total(roll_rows)
        _outcome(results, "7ж) окно 7×36ч: вклад == −вес×Σ оракула", contrib_w == want_w,
                 "вклад %s, оракул %d (нарушено окон %d из %d)" % (
                     contrib_w, want_w, lo.violated_total(roll_rows),
                     sum(r["windows"] for r in roll_rows)))
        contrib_n = bd8["constraints"].get(cidn, {}).get("hard")
        night_rows = lo.night_runs_overage(payload, solved, 1320, 360, 2, rest_thr)
        want_n = -int(night_plan["weight"] if night_plan else 1) * lo.night_overage_total(night_rows)
        _outcome(results, "7з) ночные подряд (граница 2): вклад == оракулу", contrib_n == want_n,
                 "вклад %s, оракул %d (серий ночных %d)" % (
                     contrib_n, want_n, len(night_rows)))
        hard8 = int(bd8["hard"])
    finally:
        mv.stop_jvm(proc)

    proc = mv.start_jvm(str(fx31))
    try:
        bd31 = _breakdown(solved)
        by31 = {c: v for c, v in bd31["constraints"].items() if c.startswith("logic ")}
        rows31 = lo.days_off_per_month(payload, solved, 31)
        runs2 = lo.consecutive_runs(payload, solved, 2)
        rest2 = lo.rest_after_long_duty(payload, solved, 300, 2000, rest_thr)
        roll2 = lo.rest_in_rolling_windows(payload, solved, 7, 9000)
        night0 = lo.night_runs_overage(payload, solved, 1320, 360, 1, rest_thr)
        want31 = lo.expected_hard(rows31, weight)
        want_r2 = -lo.overage_total(runs2)
        want_t2 = -lo.rest_shortfall_total(rest2)
        want_w2 = -lo.violated_total(roll2)
        want_n0 = -lo.night_overage_total(night0)
        got = sorted(int(v.get("hard") or 0) for v in by31.values())
        _outcome(results, "7б) выкрученные границы: все пять вкладов == оракулам и < 0",
                 got == sorted([want31, want_r2, want_t2, want_w2, want_n0]) and
                 max(want31, want_r2, want_t2, want_w2, want_n0) < 0,
                 "вклады %s, оракулы выходных %d, серий %d, отдыха %d, окна %d, ночных %d" % (
                     got, want31, want_r2, want_t2, want_w2, want_n0))
    finally:
        mv.stop_jvm(proc)

    proc = mv.start_jvm(str(fx0))
    try:
        bd0 = _breakdown(solved)
        hard0 = int(bd0["hard"])
        stray = [c for c in bd0["constraints"] if c.startswith("logic ")]
        both = (int(contrib8 or 0) + int(contrib_r5 or 0) + int(contrib_t or 0) +
                int(contrib_w or 0) + int(contrib_n or 0))
        _outcome(results, "7в) без logic: жёсткий счёт больше ровно на сумму вкладов",
                 not stray and not bd0.get("generated") and hard0 == hard8 - both,
                 "без logic %d, с logic %d, вклады %s + %s + %s + %s + %s, имён logic у фабрики %d" % (
                     hard0, hard8, contrib8, contrib_r5, contrib_t, contrib_w, contrib_n, len(stray)))
    finally:
        mv.stop_jvm(proc)


def main() -> int:
    if not mv.JAR.exists():
        print("нет сборки решателя: %s" % mv.JAR)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="most-v-logic-"))
    fixture = build_fixture(NANO, tmp / "nano-logic-task-package.zip")
    print("фикстура: %s (блоков logic: %d)" % (fixture, len(LOGIC_BLOCKS)))
    pkg = tp.load(fixture, run_gate=False)
    payload = {k: v for k, v in tp.to_payload(pkg).items() if not k.startswith("_")}
    assert len(lg.logic_rules(pkg)) == len(LOGIC_BLOCKS), "пакет потерял блоки logic"

    defects = 0
    proc = mv.start_jvm(str(fixture))
    try:
        # 1. Прогон стартует; отчёт несёт корзины.
        code, body = _post("/schedules/single?mode=feasibility-soft&spentLimitSeconds=%d"
                           % mv.BUDGET_S, payload, mv.BUDGET_S + 120)
        print("прогон /single: HTTP %d" % code)
        if code != 200:
            print("  тело: %s" % body[:600])
            return 1
        solved = json.loads(body)
        rep = mv.applied()
        logic = (rep.get("lastRun") or {}).get("logic")
        # 2. Гейт.
        verdicts = lg.check(pkg, logic)
        print(lg.report(verdicts, considered=len(lg.logic_rules(pkg))))
        if not lg.is_green(verdicts):
            defects += 1
        reg = (logic or {}).get("registered") or []
        print("зарегистрировано фабрикой имён: %d; корзины: %s" % (
            len(reg), ", ".join("%s=%d" % (b, len((logic or {}).get(b) or [])) for b in lg.BASKETS)))
        # 3. Путь мимо окна под пакетом — отказ.
        # Отказы постановки — 400 с названной причиной (контракт этапа А,
        # TaskModeRefusedMapper); причина обязана указать годный путь.
        code3, body3 = _post("/schedules?spentLimitSeconds=1", payload, 30)
        refused = code3 == 400 and "/schedules/single" in body3
        print("POST /schedules под пакетом: HTTP %d — %s" % (
            code3, "отказ с причиной, как и должно" if refused else "ДЕФЕКТ: путь мимо окна открыт"))
        if not refused:
            defects += 1
        # 4. Второй вход — тот же ростер, те же корзины.
        code4, body4 = _post("/schedules/score", solved, 120)
        print("POST /schedules/score того же ростера: HTTP %d, счёт %s" % (code4, body4[:40]))
        if code4 != 200:
            defects += 1
    finally:
        mv.stop_jvm(proc)

    results: list = []
    part2(tmp, results)
    defects += sum(1 for _, ok, _ in results if not ok)
    print("=" * 72)
    print("исходов части 2 проверено: %d, незелёных: %d" % (
        len(results), sum(1 for _, ok, _ in results if not ok)))
    print("итог: %s" % ("сходится" if defects == 0 else "ДЕФЕКТОВ %d" % defects))
    return 0 if defects == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
