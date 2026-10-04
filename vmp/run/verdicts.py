# -*- coding: utf-8 -*-
"""Трёхчастный результат выдачи кнопки и сертификата — общий контракт
обеих сторон (план исправлений внешнего аудита 04.09.2026, раздел 5,
пункт 1; находка A8 «нарушений не найдено ≠ допустимость установлена»).

Класс ошибки, ради которого контракт заведён: итог сильнее
доказательства. Прежний однословный вердикт «допустимо» рождался из
флага certified = «есть прогнанные проверки и нет нарушений», а
непроверенные жёсткие правила лишь перечислялись. Теперь у результата
три независимые части с закрытыми словарями:

  feasibility — допустимость:
      confirmed        подтверждена: покрытие полное, нарушений нет,
                       решатель и показания не спорят;
      violated         нарушена: есть нарушение жёсткого правила либо
                       решатель сам назвал решение недопустимым;
      not_established  не установлена: есть непроверенные правила /
                       нескомпилированные проверки / ни одной прогнанной
                       проверки / красный гейт показаний — сертификат
                       НИЧЕГО не удостоверяет, и это третий исход,
                       а не «допустимо».
  cost — корректность стоимости (штрафная сверка / сверка целевой):
      confirmed | mismatch | not_checked.
  optimality — качество оптимизации:
      feasible_found            найдено допустимое решение (режим
                                допустимости либо поиск без доказательства
                                качества при подтверждённой допустимости);
      catalog_optimum           оптимум каталога (IP отбора цепочек
                                доказан оптимальным на своём каталоге);
      proven_optimum_or_bound   доказанный оптимум либо граница;
      not_proven                качество не доказано (в т.ч. всегда,
                                когда допустимость не подтверждена).
  Отдельно: coverage_complete (полнота обязательного покрытия,
  bool | None) и search_incomplete («поиск не завершён ≠ невозможно»,
  bool).

Единая версия 06.09.2026 (план §3.10.2, строки C-1..C-3, решение
GPT-011 «без blanket-downgrade»):
  * стоимость «partial» — сверка сошлась на области учёта, но полная
    стоимость не установлена (неопределённый вклад либо оценка области
    учёта cost_assessment без complete = True); «confirmed» только при
    полном покрытии всех членов цены;
  * неизвестные окна проверок (window_coverage.unknown > 0) — пробел
    покрытия: допустимость не установлена, покрытие неполное — вторая
    защита, независимая от перечня частично покрытых правил;
  * оптимум конкретного каталога по объявленной числовой цели
    доказывается независимо от неизвестного остатка цены: optimality
    остаётся catalog_optimum / proven_optimum_or_bound, а рядом
    full_price_optimality = not_proven и optimality_scope называют,
    ЧТО именно доказано; оптимальность по полной цене не обещается.

Формат — COSMOS; отражение в карточке серии и панели — CDPM (по тому
же словарю). Слова вердикта кнопки выводятся из feasibility:
«допустимо» только при confirmed, иначе «недопустимо» либо
«не удостоверено: …» с причиной и перечнем.
"""
from __future__ import annotations

FEASIBILITY = ("confirmed", "violated", "not_established")
COST = ("confirmed", "partial", "mismatch", "not_checked")
OPTIMALITY = ("feasible_found", "catalog_optimum",
              "proven_optimum_or_bound", "not_proven")
#: Оптимальность по ПОЛНОЙ цене (C-3): доказана только при подтверждённой
#: полной стоимости; при partial/not_checked — не доказана, словами.
FULL_PRICE_OPTIMALITY = ("proven", "not_proven")
#: Область доказанного оптимума (C-3, GPT-011): объявленная числовая
#: цель каталога — не глобальная оптимальность задачи.
OPTIMALITY_SCOPE_CATALOG = "объявленная числовая цель каталога (известная часть цены)"

#: Слова для страницы и сертификата (деловой русский, без жаргона).
WORDS = {
    "feasibility": {"confirmed": "подтверждена", "violated": "нарушена",
                    "not_established": "не установлена"},
    "cost": {"confirmed": "подтверждена",
             "partial": "подтверждена на области учёта (доказанные внутренние "
                        "события); полная стоимость не установлена — есть "
                        "неопределённый вклад",
             "mismatch": "расхождение",
             "not_checked": "не проверена"},
    "optimality": {"feasible_found": "допустимое решение",
                   "catalog_optimum": "оптимум каталога",
                   "proven_optimum_or_bound": "доказанный оптимум или граница",
                   "not_proven": "не доказано"},
}

#: Сколько непроверенных правил называется в словах вердикта поимённо;
#: полный перечень всегда в поле uncovered.
_NAMED_LIMIT = 12


def validate_result(res: dict) -> dict:
    """Результат вне словарей — отказ, не догадка."""
    if not isinstance(res, dict):
        raise ValueError("result: не объект")
    for key, vocab in (("feasibility", FEASIBILITY), ("cost", COST),
                       ("optimality", OPTIMALITY)):
        if res.get(key) not in vocab:
            raise ValueError(f"result.{key} = {res.get(key)!r} вне словаря "
                             f"{vocab}")
    if res.get("coverage_complete") not in (True, False, None):
        raise ValueError("result.coverage_complete: не bool|None")
    if not isinstance(res.get("search_incomplete"), bool):
        raise ValueError("result.search_incomplete: не bool")
    if not isinstance(res.get("uncovered"), list):
        raise ValueError("result.uncovered: не список")
    if "full_price_optimality" in res \
            and res.get("full_price_optimality") not in FULL_PRICE_OPTIMALITY:
        raise ValueError(f"result.full_price_optimality = "
                         f"{res.get('full_price_optimality')!r} вне словаря "
                         f"{FULL_PRICE_OPTIMALITY}")
    return res


def _cost_assessment_incomplete(cert: dict) -> bool:
    """Оценка области учёта стоимости (cost_assessment, контракт Б) есть,
    но полноты не заявляет: complete не True — полная стоимость не
    установлена (C-1: защита GPT; отсутствие поля — не признак)."""
    assessment = cert.get("cost_assessment")
    return isinstance(assessment, dict) and assessment.get("complete") is not True


def _cost_of(cert: dict) -> str:
    """Стоимость — по разбору сверки в теле, не по флагу шапки: сначала
    штрафная сверка ростеринга, затем сверка целевой пейринга."""
    from vmp import penalty_reconcile as _pr
    for key in ("penalty_reconciliation", "pairing_objective_reconciliation"):
        rec = cert.get(key)
        if not isinstance(rec, dict):
            continue
        verdict = rec.get("verdict")
        if verdict == _pr.OK:
            # Контракт Б (05.09.2026): сверка сошлась на области учёта
            # «доказанные внутренние события»; при неопределённом вкладе
            # (отдых на базе, открытый конец, граница горизонта) полная
            # стоимость не установлена — это называется словами, а не
            # выдаётся за «подтверждена». Та же защита от оценки области
            # учёта (cost_assessment без complete = True, C-1): совпавшая
            # известная часть цены — не подтверждённая полная стоимость.
            if cert.get("cost_certainty") == "partial" \
                    or _cost_assessment_incomplete(cert):
                return "partial"
            # Политика P-417 (единая версия 06.09.2026): сверка несёт
            # полноту области учёта — известная часть при неизвестном
            # остатке подтверждена лишь на области учёта.
            if rec.get("price_complete") is False:
                return "partial"
            # H11-R1 (GPT-022): «подтверждена» — только при
            # full_cost_confirmed = True (полное независимое покрытие
            # ВСЕХ членов объявленной цены). Полнота одних ночёвок
            # (price_complete по области событий, night_cost_confirmed)
            # при непокрытых членах цены (price_members_uncovered) —
            # partial с показанием (cost_note_of), не «подтверждена».
            # Запись без притязаний на область (price_complete None —
            # сверка до единой версии, сертификат без мер ночёвок)
            # остаётся числовым подтверждением, как прежде.
            if rec.get("full_cost_confirmed") is True:
                return "confirmed"
            if rec.get("price_complete") is True or rec.get("night_cost_confirmed") \
                    or rec.get("price_members_uncovered"):
                return "partial"
            return "confirmed"
        if verdict == _pr.DIVERGE:
            return "mismatch"
        return "not_checked"          # несравнимо = не проверена
    return "not_checked"


def cost_note_of(cert: dict) -> str | None:
    """Показание к стоимости «partial» (H11-R1): что именно подтверждено
    и какие члены цены не покрыты независимой сверкой. None — сказать
    нечего (неопределённый вклад назван словарём WORDS)."""
    from vmp import penalty_reconcile as _pr
    for key in ("penalty_reconciliation", "pairing_objective_reconciliation"):
        rec = cert.get(key)
        if not isinstance(rec, dict) or rec.get("verdict") != _pr.OK:
            continue
        if rec.get("full_cost_confirmed") is True:
            return None
        uncovered = [str(x) for x in (rec.get("price_members_uncovered") or [])]
        parts: list[str] = []
        if rec.get("night_cost_confirmed") is True:
            parts.append("ночёвки подтверждены (паритет событий с решателем)")
        elif rec.get("price_complete") is True:
            parts.append("полнота области ночёвок заявлена, независимой сверки "
                         "событий с решателем нет")
        if uncovered:
            parts.append("члены цены %s не покрыты независимой сверкой области"
                         % ", ".join(uncovered))
        if not parts:
            return None
        return "; ".join(parts) + " — полная стоимость не подтверждена"
    return None


def window_unknown_of(cert: dict) -> int:
    """Неизвестных окон проверок по шапке (window_coverage.unknown,
    C-2): неизвестное окно — не молчание и не положительный итог."""
    cov = cert.get("window_coverage") if isinstance(cert, dict) else None
    if not isinstance(cov, dict):
        return 0
    try:
        n = int(cov.get("unknown") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def uncovered_of(cert: dict) -> list[str]:
    """Перечень правил, на которые сертификат не распространяется:
    непокрытые (в т.ч. составные, покрытые не всеми проверками)."""
    seen: list[str] = []
    for key in ("rules_uncovered", "rules_partially_covered"):
        for rid in cert.get(key) or []:
            rid = str(rid)
            if rid not in seen:
                seen.append(rid)
    return seen


def feasibility_of(cert: dict) -> str:
    """Допустимость выводится ИЗ ПОКРЫТИЯ И ТЕЛА, а не из флага:
    нарушение сильнее пробела; флаг certified — производная, не
    источник. Стоимость (сверки) судится отдельной частью и на
    допустимость не влияет."""
    hard = cert.get("solver_hard")
    violated = (int(cert.get("findings") or 0) > 0
                or bool(cert.get("violated_rules"))
                or (isinstance(hard, int) and not isinstance(hard, bool)
                    and hard < 0))
    if violated:
        return "violated"
    gap = (int(cert.get("checks_run") or 0) == 0
           or bool(uncovered_of(cert))
           # C-2: неизвестные окна проверок — допустимость не установлена
           # (вторая защита, независимая от перечня правил).
           or window_unknown_of(cert) > 0
           or int(cert.get("checks_not_compiled") or 0) > 0
           or cert.get("logic_gate_green") is False)
    return "not_established" if gap else "confirmed"


def certified_of(cert: dict, res: dict) -> bool:
    """Флаг «сертификат чист» — производная трёх частей: допустимость
    подтверждена И сверка стоимости (если проводилась) сошлась. Тот же
    порядок, что у гейта cdpm.gates.gate_certify (несошедшаяся либо
    несравнимая сверка — самостоятельное основание «не сертифицирован»)."""
    return (res.get("feasibility") == "confirmed"
            and cert.get("penalty_reconciliation_clean") is not False
            and cert.get("pairing_objective_clean") is not False)


def coverage_complete_of(cert: dict) -> bool | None:
    if not isinstance(cert, dict) or "checks_run" not in cert:
        return None
    return (int(cert.get("checks_run") or 0) > 0
            and not uncovered_of(cert)
            and window_unknown_of(cert) == 0
            and int(cert.get("checks_not_compiled") or 0) == 0)


def result_of_certificate(cert: dict | None, *, optimality: str | None = None,
                          search_incomplete: bool = False) -> dict:
    """Трёхчастный результат из шапки сертификата. optimality —
    показание вызывающей стороны (режим задачи, статус IP отбора);
    без подтверждённой допустимости качество не доказано.

    C-3 (GPT-011): доказанный оптимум каталога / граница НЕ понижается
    из-за неустановленной полной стоимости — это оптимум по объявленной
    числовой цели каталога; рядом full_price_optimality говорит, доказана
    ли оптимальность по полной цене (только при cost = confirmed), а
    optimality_scope называет область доказанного."""
    cert = cert if isinstance(cert, dict) else {}
    feas = feasibility_of(cert) if cert else "not_established"
    opt = optimality if optimality in OPTIMALITY else "not_proven"
    if feas != "confirmed":
        opt = "not_proven"
    res = {
        "feasibility": feas,
        "cost": _cost_of(cert),
        "optimality": opt,
        "coverage_complete": coverage_complete_of(cert),
        "search_incomplete": bool(search_incomplete),
        "uncovered": uncovered_of(cert),
    }
    if res["cost"] == "partial":
        note = cost_note_of(cert)
        if note:
            res["cost_note"] = note
    if opt in ("catalog_optimum", "proven_optimum_or_bound"):
        res["full_price_optimality"] = ("proven" if res["cost"] == "confirmed"
                                        else "not_proven")
        res["optimality_scope"] = OPTIMALITY_SCOPE_CATALOG
        signature = cert.get("objective_signature")
        if signature is not None:
            res["objective_signature"] = signature
    return validate_result(res)


def rostering_optimality(mode: str | None) -> str:
    """Ростеринг: режим допустимости даёт «допустимое решение»;
    локальный поиск без границы качества не доказывает."""
    if str(mode or "").startswith("feasibility"):
        return "feasible_found"
    return "not_proven"


def pairing_optimality(ip_status: str | None) -> str:
    """Пейринг: статус IP отбора цепочек построителя. OPTIMAL — оптимум
    каталога (не задачи в целом); FEASIBLE — допустимое; иное — не
    доказано."""
    s = str(ip_status or "").upper()
    if s == "OPTIMAL":
        return "catalog_optimum"
    if s == "FEASIBLE":
        return "feasible_found"
    return "not_proven"


def verdict_words(res: dict, cert: dict | None = None) -> str:
    """Однострочный вердикт кнопки. «допустимо» — только при
    подтверждённой допустимости; третий исход называет причину."""
    validate_result(res)
    feas = res["feasibility"]
    if feas == "confirmed":
        # Стоимость — отдельная часть: расхождение называется рядом,
        # а не превращает подтверждённую допустимость в третий исход.
        return ("допустимо" if res["cost"] != "mismatch"
                else "допустимо; стоимость: расхождение")
    if feas == "violated":
        return "недопустимо"
    unc = list(res.get("uncovered") or [])
    if unc:
        names = ", ".join(unc[:_NAMED_LIMIT]) + \
            (f", … ещё {len(unc) - _NAMED_LIMIT}" if len(unc) > _NAMED_LIMIT else "")
        return f"не удостоверено: непроверено {len(unc)} правил ({names})"
    cert = cert or {}
    if int(cert.get("checks_not_compiled") or 0) > 0:
        return (f"не удостоверено: проверок не скомпилировано "
                f"{int(cert['checks_not_compiled'])}")
    if cert.get("logic_gate_green") is False:
        return "не удостоверено: показания решателя расходятся с пакетом"
    if not cert or int(cert.get("checks_run") or 0) == 0:
        return "не удостоверено: ни одна проверка не прогнана"
    return "не удостоверено: сертификат допустимости не выдан"


def result_line(res: dict) -> str:
    """Три статуса словами — строка страницы и шапки сертификата."""
    validate_result(res)
    cost_words = WORDS["cost"][res["cost"]]
    if res.get("cost_note"):
        # H11-R1: показание, что подтверждено и какие члены не покрыты.
        cost_words += "; " + str(res["cost_note"])
    parts = [f"допустимость: {WORDS['feasibility'][res['feasibility']]}",
             f"стоимость: {cost_words}",
             f"оптимизация: {WORDS['optimality'][res['optimality']]}"]
    if res.get("full_price_optimality") == "not_proven":
        # C-3: оптимум — по объявленной числовой цели каталога; полная
        # цена не подтверждена, оптимальность по ней не доказана.
        parts.append("оптимум — по " + str(res.get("optimality_scope")
                                          or OPTIMALITY_SCOPE_CATALOG)
                     + ", оптимальность по полной цене не доказана")
    cov = res.get("coverage_complete")
    if cov is not None:
        parts.append("покрытие: " + ("полное" if cov else
                                     f"неполное ({len(res.get('uncovered') or [])})"))
    if res.get("search_incomplete"):
        parts.append("поиск не завершён (≠ невозможно)")
    return "; ".join(parts)
