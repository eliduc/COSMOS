# -*- coding: utf-8 -*-
"""Контракт отчёта 3.3 — качество операционных данных (линия данных,
пакет ДЛ-1; решение владельца 2026-08-18: отдельный конвейер данных,
контракты раньше конвейера — как П-1 у свода).

Машинная модель отчёта = единый источник истины; md-отчёт рендерится
из неё детерминированно — расхождение исключено по построению, правка
Markdown мимо модели ловится побайтной сверкой в гейте.

Контракт:

- **проверка** объявляет основание: строку свода (HARD / DATA / CASE —
  проверки допустимости генерируются из формализованных правил) либо
  встроенный класс качества (формат, дубль, …). Статус проверки
  честный: «прогнана» — с числом рассмотренных записей (У-05),
  «частично прогнана» (ревизия 1.18.3) — с адресным покрытием окон
  `window_coverage` и названной причиной неизвестности,
  «не применима» / «не прогнана» — с названной причиной (fail-closed);
  проверка по блокированной строке свода НЕ прогоняется до ответа
  авиакомпании — это ловится машинно;
- **покрытие окон** (`window_coverage`) — адресный список окон
  «сотрудник × окно»: `expected = evaluated + unknown = len(windows)`,
  адрес окна уникален, у каждого окна причина (доказательство либо
  причина неизвестности); `unknown > 0` ⇔ «частично прогнана».
  Непрерывное доказательство (`granularity = continuous_critical_cells`)
  судится пересчётом объединений интервалов: ячейки покрывают область
  окончаний ровно один раз;
- **находка** ссылается на прогнанную или частично прогнанную проверку
  (доказанное нарушение сохраняется при неизвестности других окон),
  несёт адрес (источник, лист, строка), серьёзность, класс дефекта и
  доказательство-значение; класс находки согласован с основанием
  проверки; «нарушение правила» обязано нести ссылку на строку свода;
- **счётчики** отчёта сходятся с пересчётом по спискам — гейт
  проверяет повторным счётом.

Запуск отдельно от приложения:
    python -m cdpm.data_report ОТЧЁТ.json --svod MODEL.json \
        --out-dir КАТАЛОГ            # рендер md из машинной модели
    python -m cdpm.data_report --sample КАТАЛОГ   # образец «Полярис»
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

SEVERITIES = ("БЛОКИРУЮЩЕЕ", "КРИТИЧЕСКОЕ", "СЕРЬЁЗНОЕ",
              "РЕДАКЦИОННОЕ")
# Вердикты сверки спорного (ДЛ-7): модель раскладывает находки на три
# корзины; решение по «неясно» — у владельца, не у модели.
REVIEW_VERDICTS = ("похоже на настоящее нарушение",
                   "законное исключение",
                   "неясно — к владельцу")
# Классы дефектов данных; «нарушение правила» — единственный класс с
# основанием-строкой свода, остальные — встроенные проверки качества.
CLASSES = ("нарушение правила", "пропуск данных",
           "противоречие записей", "формат", "дубль",
           "ссылочная целостность", "аномалия",
           # Спецификация 1.17: встроенные проверки листов раздела
           # истории сотрудника («История», «Утверждённые назначения»)
           # — табельные, значения, срез, полнота.
           "история")
BUILTIN_BASES = tuple(c for c in CLASSES if c != "нарушение правила")
# Ревизия 1.18.3: «частично прогнана» — часть окон доказана, часть
# неизвестна; обязана нести reason и адресное покрытие window_coverage.
CHECK_STATUSES = ("прогнана", "частично прогнана", "не применима",
                  "не прогнана")
#: Статусы, при которых у проверки есть доказательства (по ним возможна
#: находка); строка свода с блокером не даёт ни одного из них.
RUN_STATUSES = ("прогнана", "частично прогнана")
#: Виды покрытия окон (1.18.3): адресные окна либо критические ячейки
#: области окончаний (непрерывное доказательство правил «отдых ≥ R в
#: любом окне W», K-15).
COVERAGE_GRANULARITIES = ("windows", "continuous_critical_cells")
WINDOW_STATUSES = ("evaluated", "unknown")
WINDOW_OUTCOMES = ("pass", "violation", None)
# Строки свода, из которых генерируются проверки допустимости данных.
CHECKABLE_KINDS = ("HARD", "DATA", "CASE")


class DataReportError(ValueError):
    """Честный отказ контракта: причина всегда названа."""


# ---------------------------------------------------------------------------
# Валидация
# ---------------------------------------------------------------------------

def metrics_of(doc: dict) -> dict:
    """Пересчёт счётчиков отчёта по спискам — единственный источник
    чисел для сводки и гейта."""
    findings = doc.get("findings", [])
    checks = doc.get("checks", [])
    by_class = {c: 0 for c in CLASSES}
    by_severity = {s: 0 for s in SEVERITIES}
    for f in findings:
        if f.get("class") in by_class:
            by_class[f["class"]] += 1
        if f.get("severity") in by_severity:
            by_severity[f["severity"]] += 1
    out = {
        "files": len(doc.get("sources", [])),
        "rows_total": sum(s.get("rows_seen", 0)
                          for s in doc.get("sources", [])),
        "checks_run": sum(1 for c in checks
                          if c.get("status") == "прогнана"),
        "checks_skipped": sum(1 for c in checks
                              if c.get("status") != "прогнана"),
        "by_class": {k: v for k, v in by_class.items() if v},
        "by_severity": {k: v for k, v in by_severity.items() if v},
    }
    # Счётчик сверки появляется только у сверенных отчётов: отчёты до
    # ДЛ-7 сохраняют прежние счётчики байт-в-байт (обратная
    # совместимость гейтов).
    by_review = {v: 0 for v in REVIEW_VERDICTS}
    for f in findings:
        verdict = (f.get("review") or {}).get("verdict")
        if verdict in by_review:
            by_review[verdict] += 1
    if any(by_review.values()):
        out["review_by_verdict"] = {k: v for k, v in by_review.items()
                                    if v}
    return out


def union_spans(spans) -> list[tuple[datetime, datetime]]:
    """Объединение интервалов: пересекающиеся и смежные сливаются,
    обратные (начало позже конца) отбрасываются."""
    result: list[tuple[datetime, datetime]] = []
    for a, b in sorted(spans):
        if a > b:
            continue
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a, b))
    return result


def _read_spans(rows, end_key: str) -> list[tuple[datetime, datetime]]:
    """Список интервалов {start_inclusive, <end_key>} → пары дат;
    обратный интервал — отказ."""
    if not isinstance(rows, list):
        raise ValueError("нет списка интервалов")
    result = [(datetime.fromisoformat(row["start_inclusive"]),
               datetime.fromisoformat(row[end_key])) for row in rows]
    if any(a > b for a, b in result):
        raise ValueError("обратный интервал")
    return result


def _continuous_coverage_problems(cov: dict) -> list[str]:
    """Непрерывное доказательство (1.18.3, K-15): арифметика
    доказательства по сотруднику пересчитывается из интервалов, и
    критические ячейки обязаны покрывать всю область окончаний окна
    ровно один раз. Возвращает нарушения (пусто = чисто).

    Прочтение: окно ширины W, оканчивающееся в t, накрывает [t−W, t);
    доказанный отдых [a, b) длиной ≥ R даёт проходящие окончания
    [a+R, b+W−R]; полностью известный интервал [a, b) ширины ≥ W даёт
    полностью известные окончания [a+W, b]. Точки разбиения — границы
    области и концы этих объединений; ячейка — точка либо открытый
    интервал между соседними точками."""
    problems: list[str] = []
    try:
        if cov.get("expected_unit") != "critical_cells":
            raise ValueError("единица непрерывного покрытия не "
                             "critical_cells")
        domain = cov["ending_domain"]
        lo = datetime.fromisoformat(domain["start_inclusive"])
        hi = datetime.fromisoformat(domain["end_inclusive"])
        if lo >= hi:
            raise ValueError("пустая область окончаний")
        proofs = cov["employee_proofs"]
        if not isinstance(proofs, dict) or not proofs:
            raise ValueError("нет employee_proofs")
        employees = {w.get("employee") for w in cov["windows"]
                     if isinstance(w, dict)}
        if employees != set(proofs):
            raise ValueError("сотрудники в ячейках и employee_proofs "
                             "расходятся")
        for emp, proof in proofs.items():
            wm, rm = proof["window_minutes"], proof["required_rest_minutes"]
            if type(wm) is not int or type(rm) is not int \
                    or wm <= 0 or rm <= 0:
                raise ValueError("неверная длина окна/отдыха в "
                                 "доказательстве")
            width = timedelta(minutes=wm)
            required = timedelta(minutes=rm)
            known = union_spans(_read_spans(proof["known_occupancy_intervals"],
                                       "end_exclusive"))
            rests = union_spans(_read_spans(proof["proven_rest_intervals"],
                                       "end_exclusive"))
            if any(not any(ka <= a <= b <= kb for ka, kb in known)
                   for a, b in rests):
                raise ValueError("доказанный отдых вне известных "
                                 "интервалов")
            passing = union_spans((max(lo, a + required),
                              min(hi, b + width - required))
                             for a, b in rests
                             if b - a >= required and width >= required)
            complete = union_spans((max(lo, a + width), min(hi, b))
                              for a, b in known if b - a >= width)
            if _read_spans(proof["passing_window_end_spans"],
                           "end_inclusive") != passing \
                    or _read_spans(proof["fully_known_window_end_spans"],
                                   "end_inclusive") != complete:
                raise ValueError("объединения окон не равны пересчёту "
                                 "по интервалам")
            cuts = sorted({lo, hi}
                          | {x for span in passing + complete for x in span})
            want = ({(x, x, "point") for x in cuts}
                    | {(a, b, "open_interval")
                       for a, b in zip(cuts, cuts[1:])})
            got = []
            normative = proof.get("occupancy_basis") == "normative_duties"
            for win in (w for w in cov["windows"]
                        if w.get("employee") == emp):
                cell = win["ending_domain"]
                a = datetime.fromisoformat(cell["start"])
                b = datetime.fromisoformat(cell["end"])
                kind = cell["kind"]
                point = kind == "point"
                if cell.get("start_closed") is not point \
                        or cell.get("end_closed") is not point:
                    raise ValueError("неверная открытость критической "
                                     "ячейки")
                got.append((a, b, kind))
                passed = (any(x <= a <= y for x, y in passing) if point
                          else any(x < b and y > a for x, y in passing))
                full = (any(x <= a <= y for x, y in complete) if point
                        else any(x < b and y > a for x, y in complete))
                if win.get("status") == "evaluated":
                    want_outcome = "pass" if passed else "violation"
                    if not normative or win.get("outcome") != want_outcome \
                            or (not passed and not full):
                        raise ValueError("исход ячейки не доказан "
                                         "непрерывными интервалами")
                elif win.get("outcome") == "violation" and not (
                        win.get("lower_bound_violation") is True
                        and not normative and full and not passed):
                    raise ValueError("неизвестная ячейка не несёт "
                                     "доказанной нижней границы "
                                     "нарушения")
            if len(got) != len(want) or set(got) != want:
                raise ValueError("критические ячейки не покрывают всю "
                                 "область окончаний точно один раз")
    except (KeyError, TypeError, ValueError, OverflowError,
            AttributeError) as exc:
        problems.append(f"window_coverage: непрерывное доказательство "
                        f"некорректно: {exc}")
    return problems


def _window_coverage_problems(check: dict) -> list[str]:
    """Покрытие окон проверки (1.18.3): счётчикам не верят на слово —
    они сверяются с адресным списком окон; «частично прогнана» без
    покрытия или без неизвестных окон — дефект, неизвестные окна при
    статусе «прогнана» — дефект. Возвращает нарушения (пусто = чисто)."""
    cov = check.get("window_coverage")
    partial = check.get("status") == "частично прогнана"
    if cov is None:
        return ["частичная проверка без window_coverage"] if partial else []
    if not isinstance(cov, dict):
        return ["window_coverage не объект"]
    counts = [cov.get(k) for k in ("expected", "evaluated", "unknown")]
    if any(type(n) is not int or n < 0 for n in counts):
        return ["window_coverage: счётчики не целые неотрицательные "
                "числа"]
    expected, evaluated, unknown = counts
    problems: list[str] = []
    if expected != evaluated + unknown:
        problems.append("window_coverage: expected не равен "
                        "evaluated + unknown")
    granularity = cov.get("granularity")
    if granularity is not None and granularity not in COVERAGE_GRANULARITIES:
        problems.append(f"window_coverage: granularity {granularity!r} "
                        f"вне перечня {COVERAGE_GRANULARITIES}")
    windows = cov.get("windows")
    if not isinstance(windows, list):
        return problems + ["window_coverage: нет адресного списка windows"]
    if len(windows) != expected:
        problems.append("window_coverage: expected не равен числу "
                        "адресов windows")
    actual = {"evaluated": 0, "unknown": 0}
    addresses: set[tuple[str, str, str]] = set()
    for win in windows:
        if not isinstance(win, dict):
            problems.append("window_coverage: окно не объект")
            continue
        status = win.get("status")
        if status not in actual:
            problems.append("window_coverage: неизвестный статус окна")
        else:
            actual[status] += 1
        if win.get("outcome") not in WINDOW_OUTCOMES:
            problems.append(f"window_coverage: исход окна "
                            f"{win.get('outcome')!r} вне перечня")
        employee = win.get("employee")
        if not isinstance(employee, str) or not employee.strip():
            problems.append("window_coverage: окно без сотрудника")
        try:
            start = datetime.fromisoformat(win["start_inclusive"])
            end = datetime.fromisoformat(win["end_exclusive"])
            if start >= end:
                raise ValueError("пустое или обратное окно")
        except (KeyError, TypeError, ValueError):
            problems.append("window_coverage: неверные границы "
                            "[start_inclusive, end_exclusive)")
        address = (str(employee), str(win.get("start_inclusive")),
                   str(win.get("end_exclusive")))
        if address in addresses:
            problems.append("window_coverage: повтор адреса окна")
        addresses.add(address)
        if not isinstance(win.get("reason"), str) \
                or not win["reason"].strip():
            problems.append("window_coverage: окно без "
                            "причины/доказательства")
    if actual != {"evaluated": evaluated, "unknown": unknown}:
        problems.append("window_coverage: счётчики evaluated/unknown "
                        "расходятся с адресами")
    if partial and unknown == 0:
        problems.append("частичная проверка без неизвестных окон")
    if unknown and not partial:
        problems.append("неизвестные окна требуют статуса «частично "
                        "прогнана»")
    if granularity == "continuous_critical_cells" \
            and check.get("status") in RUN_STATUSES:
        problems.extend(_continuous_coverage_problems(cov))
    return problems


def check_is_incomplete(check: dict) -> bool:
    """Проверка не даёт полного доказательства: «частично прогнана»,
    «не прогнана» либо покрытие окон с неизвестными окнами. При любой
    такой проверке сертификат `certified = true` невозможен (1.18.3)."""
    if check.get("status") in ("частично прогнана", "не прогнана"):
        return True
    cov = check.get("window_coverage")
    return isinstance(cov, dict) and cov.get("unknown") != 0


def rule_needs_continuous_proof(rule: dict | None) -> bool:
    """Правило «отдых ≥ R в любом окне W» (семья rolling_rest базы
    шаблонов, K-15): доказательство обязано быть непрерывным — раздел
    покрытия с `granularity = continuous_critical_cells`."""
    if not isinstance(rule, dict):
        return False
    fit = rule.get("template_fit")
    fam = fit.get("family") if isinstance(fit, dict) else None
    if not fam:
        exp = rule.get("export_solver")
        fam = exp.get("family") if isinstance(exp, dict) else None
    return fam == "rolling_rest"


def has_continuous_proof(check: dict) -> bool:
    cov = check.get("window_coverage")
    return isinstance(cov, dict) \
        and cov.get("granularity") == "continuous_critical_cells"


def validate_report(doc: dict, svod: dict | None = None) -> list[str]:
    """Инварианты контракта; при переданном своде — трассировка
    оснований к его строкам. Возвращает нарушения (пусто = чисто)."""
    problems: list[str] = []
    if not (doc.get("customer") or "").strip():
        problems.append("нет названия авиакомпании (customer)")

    sids: set[str] = set()
    for s in doc.get("sources", []):
        sid = s.get("sid") or ""
        if sid in sids:
            problems.append(f"источник {sid!r}: повторный SID")
        sids.add(sid)
        if not sid or not (s.get("file") or "").strip():
            problems.append(f"источник {sid!r}: нет SID или имени файла")
        if not (s.get("sha256") or "").strip():
            problems.append(f"источник {sid!r}: нет SHA-256 — данные "
                            f"не заморожены")
        if not isinstance(s.get("rows_seen"), int) \
                or s.get("rows_seen", -1) < 0:
            problems.append(f"источник {sid!r}: rows_seen не число")

    svod_rules = {r["id"]: r for r in (svod or {}).get("rules", [])}
    checks: dict[str, dict] = {}
    for c in doc.get("checks", []):
        cid = c.get("id") or ""
        if cid in checks:
            problems.append(f"проверка {cid!r}: повторный ID")
        checks[cid] = c
        basis = c.get("basis") or {}
        has_rule = bool(basis.get("rule"))
        has_builtin = bool(basis.get("builtin"))
        if has_rule == has_builtin:
            problems.append(f"проверка {cid}: основание — РОВНО одно "
                            f"из rule / builtin")
        if has_builtin and basis["builtin"] not in BUILTIN_BASES:
            problems.append(f"проверка {cid}: встроенное основание "
                            f"{basis['builtin']!r} вне перечня "
                            f"{BUILTIN_BASES}")
        if not (c.get("statement") or "").strip() \
                or not (c.get("unit") or "").strip():
            problems.append(f"проверка {cid}: нет формулировки или "
                            f"единицы счёта")
        status = c.get("status")
        if status not in CHECK_STATUSES:
            problems.append(f"проверка {cid}: статус {status!r} вне "
                            f"перечня {CHECK_STATUSES}")
        elif status in RUN_STATUSES:
            # У-05: доказательство — число рассмотренных записей; при
            # адресном покрытии окон (1.18.3) записей может не быть
            # вовсе (окна судятся по интервалам), тогда ноль честен.
            cov = c.get("window_coverage")
            addressed = (isinstance(cov, dict)
                         and isinstance(cov.get("windows"), list)
                         and bool(cov["windows"]))
            rows_checked = c.get("rows_checked")
            if type(rows_checked) is not int or rows_checked < 0 \
                    or (rows_checked == 0 and not addressed):
                problems.append(f"проверка {cid}: «{status}» без числа "
                                f"рассмотренных записей/адресных окон "
                                f"(У-05)")
            if status == "частично прогнана" \
                    and not (c.get("reason") or "").strip():
                problems.append(f"проверка {cid}: «{status}» без "
                                f"названной причины (fail-closed)")
        else:
            if not (c.get("reason") or "").strip():
                problems.append(f"проверка {cid}: «{status}» без "
                                f"названной причины (fail-closed)")
            if c.get("rows_checked"):
                problems.append(f"проверка {cid}: «{status}», но "
                                f"rows_checked не ноль")
        problems.extend(f"проверка {cid}: {p}"
                        for p in _window_coverage_problems(c))
        if has_rule and svod is not None:
            rule = svod_rules.get(basis["rule"])
            if rule is None:
                problems.append(f"проверка {cid}: строки "
                                f"{basis['rule']!r} нет в своде")
            else:
                if rule.get("kind") not in CHECKABLE_KINDS:
                    problems.append(
                        f"проверка {cid}: строка {basis['rule']} типа "
                        f"{rule.get('kind')!r} — проверки данных "
                        f"генерируются из {CHECKABLE_KINDS}")
                if rule.get("blockers") and status in RUN_STATUSES:
                    problems.append(
                        f"проверка {cid}: строка {basis['rule']} "
                        f"блокирована вопросом — проверка не может "
                        f"быть «{status}» до ответа авиакомпании")

    fids: set[str] = set()
    for f in doc.get("findings", []):
        fid = f.get("id") or ""
        if fid in fids:
            problems.append(f"находка {fid!r}: повторный ID")
        fids.add(fid)
        check = checks.get(f.get("check") or "")
        if check is None:
            problems.append(f"находка {fid}: ссылка на несуществующую "
                            f"проверку {f.get('check')!r}")
        elif check.get("status") not in RUN_STATUSES:
            # Находка возможна по прогнанной и по частично прогнанной
            # проверке: доказанное нарушение сохраняется при
            # неизвестности других окон (1.18.3).
            problems.append(f"находка {fid}: проверка {check.get('id')} "
                            f"не «прогнана» — находка невозможна")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"находка {fid}: серьёзность "
                            f"{f.get('severity')!r} вне перечня")
        klass = f.get("class")
        if klass not in CLASSES:
            problems.append(f"находка {fid}: класс {klass!r} вне "
                            f"перечня")
        elif check is not None:
            basis = check.get("basis") or {}
            expected = ("нарушение правила" if basis.get("rule")
                        else basis.get("builtin"))
            if expected and klass != expected:
                problems.append(f"находка {fid}: класс {klass!r} не "
                                f"согласован с основанием проверки "
                                f"({expected!r})")
        target = f.get("target") or {}
        if target.get("sid") not in sids:
            problems.append(f"находка {fid}: источник "
                            f"{target.get('sid')!r} не объявлен")
        if not (target.get("sheet") or "").strip():
            problems.append(f"находка {fid}: не назван лист")
        if not isinstance(target.get("row"), int) or target["row"] < 1:
            problems.append(f"находка {fid}: нет номера строки")
        if not (f.get("text") or "").strip():
            problems.append(f"находка {fid}: пустая формулировка")
        if not (f.get("evidence") or "").strip():
            problems.append(f"находка {fid}: без "
                            f"доказательства-значения")
        if klass == "нарушение правила":
            refs = f.get("rule_refs") or []
            if not refs:
                problems.append(f"находка {fid}: «нарушение правила» "
                                f"без ссылки на строку свода")
            elif svod is not None:
                for rid in refs:
                    if rid not in svod_rules:
                        problems.append(f"находка {fid}: строки "
                                        f"{rid!r} нет в своде")
        review = f.get("review")
        if review is not None:
            if review.get("verdict") not in REVIEW_VERDICTS:
                problems.append(f"находка {fid}: вердикт сверки "
                                f"{review.get('verdict')!r} вне перечня "
                                f"{REVIEW_VERDICTS}")
            if not (review.get("basis") or "").strip():
                problems.append(f"находка {fid}: вердикт сверки без "
                                f"обоснования")
            if klass != "нарушение правила":
                problems.append(f"находка {fid}: сверка спорного — "
                                f"только для находок «нарушение "
                                f"правила» (спор о чтении правила)")

    recomputed = metrics_of(doc)
    if doc.get("counters") != recomputed:
        problems.append("счётчики отчёта не сходятся с пересчётом по "
                        "спискам")
    return problems


# ---------------------------------------------------------------------------
# Детерминированный рендер
# ---------------------------------------------------------------------------

def _basis_label(check: dict) -> str:
    basis = check.get("basis") or {}
    return basis.get("rule") or f"встроенная: {basis.get('builtin')}"


def render_data_report(doc: dict) -> str:
    """md-отчёт — чистая функция машинной модели (побайтно
    воспроизводим)."""
    m = metrics_of(doc)
    lines: list[str] = []
    out = lines.append
    out(f"# Отчёт о качестве операционных данных — {doc['customer']}")
    out("")
    out("Издание CDPM (модуль обработки данных авиакомпании). Источник "
        "истины — машинная модель отчёта; этот файл отрендерен из неё "
        "детерминированно.")
    out("")
    out("## Источники данных")
    out("")
    out("| SID | Файл | SHA-256 | Записей рассмотрено |")
    out("|---|---|---|---|")
    for s in doc.get("sources", []):
        out(f"| {s['sid']} | {s['file']} | `{s['sha256']}` | "
            f"{s['rows_seen']} |")
    out("")
    out("## Сводка")
    out("")
    out(f"- источников: {m['files']}; записей рассмотрено: "
        f"{m['rows_total']}")
    n_partial = sum(1 for c in doc.get("checks", [])
                    if c.get("status") == "частично прогнана")
    if n_partial:
        # Ревизия 1.18.3: частично прогнанные названы отдельно; отчёты
        # без них воспроизводятся байт-в-байт прежней строкой.
        out(f"- проверок прогнано: {m['checks_run']}; частично прогнано: "
            f"{n_partial}; не применимо или не прогнано: "
            f"{m['checks_skipped'] - n_partial} (причины и покрытие окон "
            f"— в таблице проверок)")
    else:
        out(f"- проверок прогнано: {m['checks_run']}; не применимо или не "
            f"прогнано: {m['checks_skipped']} (причины — в таблице "
            f"проверок)")
    total = sum(m["by_severity"].values())
    sev = ", ".join(f"{k.lower()} {v}"
                    for k, v in m["by_severity"].items())
    cls = ", ".join(f"{k} {v}" for k, v in m["by_class"].items())
    out(f"- находок: {total}" + (f" — по серьёзности: {sev}" if sev
                                 else ""))
    if cls:
        out(f"- по классам: {cls}")
    if m.get("review_by_verdict"):
        rev = ", ".join(f"{k} {v}"
                        for k, v in m["review_by_verdict"].items())
        out(f"- сверка спорного: {rev}")
    out("")
    out("## Проверки")
    out("")
    out("| ID | Основание | Проверка | Единица счёта | Статус | "
        "Записей | Находок |")
    out("|---|---|---|---|---|---|---|")
    per_check: dict[str, int] = {}
    for f in doc.get("findings", []):
        per_check[f["check"]] = per_check.get(f["check"], 0) + 1
    for c in doc.get("checks", []):
        status = c["status"]
        if status != "прогнана":
            status = f"{status} — {c.get('reason', '')}"
        cov = c.get("window_coverage")
        if isinstance(cov, dict) and c["status"] == "частично прогнана":
            status += (f" (окон: всего {cov.get('expected')}, доказано "
                       f"{cov.get('evaluated')}, неизвестно "
                       f"{cov.get('unknown')})")
        out(f"| {c['id']} | {_basis_label(c)} | {c['statement']} | "
            f"{c['unit']} | {status} | {c.get('rows_checked', 0)} | "
            f"{per_check.get(c['id'], 0)} |")
    out("")
    out("## Находки")
    out("")
    if not doc.get("findings"):
        out("Находок нет — все прогнанные проверки чисты.")
    else:
        # Колонки сверки появляются только у сверенных отчётов —
        # md отчётов до ДЛ-7 воспроизводится байт-в-байт.
        reviewed_any = any(f.get("review")
                           for f in doc.get("findings", []))
        head = ("| ID | Серьёзность | Класс | Где | Что | "
                "Доказательство | Основание |")
        rule = "|---|---|---|---|---|---|---|"
        if reviewed_any:
            head = head[:-1] + " Вердикт сверки | Обоснование сверки |"
            rule += "---|---|"
        out(head)
        out(rule)
        checks_by_id = {c["id"]: c for c in doc.get("checks", [])}
        for f in doc.get("findings", []):
            t = f["target"]
            where = (f"{t['sid']} · лист «{t['sheet']}» · строка "
                     f"{t['row']}"
                     + (f" · колонка {t['column']}" if t.get("column")
                        else ""))
            basis = _basis_label(checks_by_id.get(f["check"], {}))
            row = (f"| {f['id']} | {f['severity']} | {f['class']} | "
                   f"{where} | {f['text']} | {f['evidence']} | "
                   f"{basis} |")
            if reviewed_any:
                review = f.get("review") or {}
                row = (row + f" {review.get('verdict') or '—'} | "
                       + f"{review.get('basis') or ''} |")
            out(row)
    out("")
    return "\n".join(lines)


def dump_report(doc: dict, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def load_report(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Образец («Полярис» — согласован с cdpm.sample.sample_model)
# ---------------------------------------------------------------------------

def sample_data_report() -> dict:
    doc = {
        "customer": "Полярис (образец)",
        "sources": [
            {"sid": "D1", "file": "POLARIS-ROSTER.xlsx",
             "sha256": "e3b0c44298fc1c149afbf4c8996fb924"
                       "27ae41e4649b934ca495991b7852b855",
             "rows_seen": 40},
            {"sid": "D2", "file": "POLARIS-QUAL.xlsx",
             "sha256": "2c26b46b68ffc68ff99b453c1d304134"
                       "13422d706483bfa0f98a5e886266e7ae",
             "rows_seen": 12},
        ],
        "checks": [
            {"id": "C-R-S2-DATA-QUAL-0",
             "basis": {"rule": "R-S2-DATA-QUAL"},
             "statement": "Допуск каждого члена экипажа, назначенного "
                          "в ростере, действителен на дату полёта",
             "unit": "строка ростера",
             "status": "прогнана", "rows_checked": 40},
            {"id": "C-R-S1-П-1-1-0",
             "basis": {"rule": "R-S1-П-1-1"},
             "statement": "Суточная полётная нагрузка в ростере не "
                          "превышает лимита строки свода",
             "unit": "служебный день",
             "status": "не прогнана", "rows_checked": 0,
             "reason": "строка свода блокирована вопросом анкеты — до "
                       "ответа авиакомпании проверка не прогоняется"},
            {"id": "C-формат-0",
             "basis": {"builtin": "формат"},
             "statement": "Даты и времена всех колонок разобраны по "
                          "объявленному формату",
             "unit": "ячейка",
             "status": "прогнана", "rows_checked": 92},
            {"id": "C-ссылочная целостность-0",
             "basis": {"builtin": "ссылочная целостность"},
             "statement": "Каждый табельный номер ростера существует "
                          "в реестре допусков",
             "unit": "строка ростера",
             "status": "не применима", "rows_checked": 0,
             "reason": "реестр сотрудников в набор не передан — "
                       "сверять не с чем"},
        ],
        "findings": [
            {"id": "F-0001", "check": "C-R-S2-DATA-QUAL-0",
             "severity": "КРИТИЧЕСКОЕ", "class": "нарушение правила",
             "target": {"sid": "D1", "sheet": "Ростер", "row": 17,
                        "column": "C"},
             "text": "Член экипажа EMP-014 назначен на рейс PX-102 "
                     "07.03.2026, а его допуск CAT-C истёк 01.03.2026",
             "evidence": "D1!C17 = «EMP-014»; D2: CAT-C до 01.03.2026",
             "rule_refs": ["R-S2-DATA-QUAL"]},
            {"id": "F-0002", "check": "C-формат-0",
             "severity": "СЕРЬЁЗНОЕ", "class": "формат",
             "target": {"sid": "D1", "sheet": "Ростер", "row": 23,
                        "column": "E"},
             "text": "Время вылета записано свободным текстом и не "
                     "разбирается как время",
             "evidence": "D1!E23 = «около 9 утра»"},
        ],
    }
    doc["counters"] = metrics_of(doc)
    return doc


# ---------------------------------------------------------------------------
# Командная строка
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Отчёт 3.3 CDPM: рендер md из машинной модели")
    ap.add_argument("report", nargs="?",
                    help="машинная модель отчёта (json)")
    ap.add_argument("--svod", help="model.json свода для трассировки")
    ap.add_argument("--out-dir", help="куда писать data-report.md")
    ap.add_argument("--sample", metavar="КАТАЛОГ",
                    help="записать образец «Полярис» (json + md)")
    args = ap.parse_args(argv)

    if args.sample:
        out = Path(args.sample)
        out.mkdir(parents=True, exist_ok=True)
        doc = sample_data_report()
        dump_report(doc, out / "data-report.json")
        (out / "data-report.md").write_text(
            render_data_report(doc), encoding="utf-8")
        print(f"образец: {out / 'data-report.json'}")
        print(f"образец: {out / 'data-report.md'}")
        return 0

    if not args.report or not args.out_dir:
        ap.error("нужны ОТЧЁТ.json и --out-dir (или --sample)")
    doc = load_report(args.report)
    svod = json.loads(Path(args.svod).read_text(encoding="utf-8")) \
        if args.svod else None
    problems = validate_report(doc, svod)
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        return 1
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = out / "data-report.md"
    md.write_text(render_data_report(doc), encoding="utf-8")
    print(f"отчёт: {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
