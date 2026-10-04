# -*- coding: utf-8 -*-
"""Ревизия 1.10: машинные формы порогов пакета (threshold-forms.json).

Живой урок шва №6 одной из линий (2026-08-27): реальный предел
смены — МАТРИЦА приложения 3 (окно времени старта × число секторов),
а пол отдыха вне базы — УСЛОВНЫЙ (по разнице часовых поясов); плоское
«строжайшее из всех» делало точное покрытие доказуемо неразрешимым
(часть участков оставалась без кандидатов). Предписание решателя:
пакет несёт
матрицу МАШИННО (норма прозой не существует — урок r3), транслятор
передаёт построителю контракт правил вместо сплющивания.

Формы собираются ДЕТЕРМИНИРОВАННО из машинных данных модели. Числовые
пороги читаются из посадки после сверки исходных носителей; старые
params совместимы. Якоря таблиц и условия в формулировке сохраняются —
формат model.json не расширяется. Файл пакета объявляется манифестом со суммой (железное
правило приёмника решателя); гейт пакета сверяет файл пересборкой из
model.json. Словарь согласован с решателем 2026-08-27 и заморожен:
минуты везде; окна — минуты от полуночи, ПОЛУИНТЕРВАЛ [from, to);
пояс окон — словарное base-local (ложится на base_tz_offset_min
контракта 1:1); границы секторов явные (sectors_from/sectors_to);
as_written у каждой строки."""
from __future__ import annotations

import copy
import json
import re
from decimal import Decimal

# Ячейка матрицы: окно старта в формулировке. Табличные окна печатаются
# ВКЛЮЧИТЕЛЬНО («06:00–14:59») — полуинтервал = конец + 1 минута.
_WINDOW_RE = re.compile(r"окн[ае]\s*(?:начала\s*)?"
                        r"(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})")
# Калибровка колонок: ячейка, называющая сектора явно.
_SECTORS_RE = re.compile(r"(\d+)\s*(?:[–—-]\s*(\d+)\s*)?сектор")
_COL_RE = re.compile(r"строка-(\d+)-кол-(\d+)$")
# Условие условного пола: разница часовых поясов ≥ N часов.
_TZ_COND_RE = re.compile(
    r"разниц\w*\s+(?:в\s+)?час\w*\s+пояс\w*\s*(?:>=|≥)?\s*(\d+)\s*ч",
    re.IGNORECASE)

_ACTIVE_KINDS = ("HARD", "DATA", "DEF")


class ThresholdFormError(ValueError):
    """Форму нельзя построить без потери или подмены смысла параметра."""

    def __init__(self, words: str, code=None, items=()):
        super().__init__(words)
        self.code = code
        self.items = list(items)


def _check_scope(scope, name: str, address: str) -> None:
    """Не терять область ни старого параметра, ни ячейки посадки."""
    from cdpm import model as m
    if scope in (None, ""):
        return
    # Вне-базовая область уже является смыслом самого порога. Более
    # узкая область (например, конкретный аэропорт) в форме не выражена.
    if (name in ("min_rest_outstation", "min_rest_outstation_min")
            and m.canon_area_qualifier(scope) == "вне базы"):
        return
    boundary = ("Для этого порога представима область «вне базы». "
                if name in ("min_rest_outstation", "min_rest_outstation_min")
                else "Дополнительная область этой формой порога не выражается. ")
    raise ThresholdFormError(f"{address}: область {scope!r} не представима "
                             "этой формой порога — расширять её нельзя. "
                             + boundary + "Сохраните смысл условия: не снимайте ограничение ради выпуска; "
                             "для него нужна поддерживаемая машинная форма.", items=[address])


def _param_records(rule: dict, names) -> list[dict]:
    """Числовая проекция для форм: посадка первична, старый путь совместим.

    Исходный документ не меняется. Это читатель текущей формы, не
    миграция model.json и не выведение всех params[] пакета landing/2.
    Единицы и отношения читаются из каталога; явно заданная область,
    которую эти формы не умеют нести, отказывает, а не исчезает.
    """
    from cdpm import family_params as fp, model as m
    rid = str(rule.get("id"))
    legacy = rule.get("params")
    if legacy is None:
        legacy = []
    if not isinstance(legacy, list) or any(not isinstance(p, dict) for p in legacy):
        raise ThresholdFormError(f"{rid}: params не список записей — форма порога не построена", items=[rid])
    selected = [p for p in legacy if p.get("name") in names]
    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        for p in selected:
            _check_scope(p.get("qualifier"), p["name"], f"{rid}/{p['name']}")
        return copy.deepcopy(selected)
    cells = fit.get("params", {})
    if not isinstance(cells, dict):
        raise ThresholdFormError(f"{rid}: params посадки не объект — форма порога не построена", items=[rid])
    problems = m.carrier_divergences(rule)
    if problems:
        raise ThresholdFormError("; ".join(p["words"] for p in problems),
                                 m.PARAM_CARRIERS_DIVERGE,
                                 [f"{p['rule']}/{p['param']}" for p in problems])
    for p in selected:
        _check_scope(p.get("qualifier"), p["name"], f"{rid}/{p['name']}")
    out = [copy.deepcopy(p) for p in selected if p.get("name") not in cells]
    for name in names:
        if name not in cells:
            continue
        cell = cells[name]
        spec = fp._param_spec(str(fit.get("family") or ""), name)
        address = f"{rid}/{name}"
        if not spec or not isinstance(cell, dict):
            raise ThresholdFormError(f"{address}: ячейка или параметр семьи не читается", items=[address])
        _check_scope(cell.get("condition"), name, address)
        relation = cell.get("relation") or spec.get("relation")
        if relation != spec.get("relation"):
            raise ThresholdFormError(f"{address}: отношение ячейки {relation!r} "
                                     f"не совпадает с отношением семьи {spec.get('relation')!r}", items=[address])
        unit = spec.get("unit")
        value, problem = fp.canonical_value(cell.get("value"), cell.get("unit"), unit)
        if problem:
            raise ThresholdFormError(f"{address}: {problem}", items=[address])
        if isinstance(value, Decimal) and value == value.to_integral_value():
            value = int(value)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ThresholdFormError(f"{address}: форма требует целое неотрицательное значение в {unit}", items=[address])
        # Сохраняем кратность и метаданные согласованных legacy-записей.
        # Новый ответ только в посадке получает такую же проекцию.
        same = [p for p in selected if p.get("name") == name]
        for previous in same or [{"name": name, "as_written": cell.get("quote", "")}]:
            projected = copy.deepcopy(previous)
            projected.update(value=value, unit=unit, relation=relation)
            out.append(projected)
    return out


def _cell_of(rule):
    """(родитель, строка, колонка, окно, минуты, след) либо None."""
    rid = str(rule.get("id") or "")
    if "-вл-табл-" not in rid or rule.get("kind") not in _ACTIVE_KINDS:
        return None
    mm = _COL_RE.search(rid)
    if not mm:
        return None
    params = [p for p in _param_records(rule, ("max_duty_duration", "max_duty_span_min"))
              if p.get("name") in ("max_duty_duration",
                                   "max_duty_span_min")
              and p.get("unit") == "duration_minutes"
              and p.get("relation") == "max"
              and isinstance(p.get("value"), int)
              and not isinstance(p.get("value"), bool)]
    if len(params) != 1:
        return None
    w = _WINDOW_RE.search(str(rule.get("statement") or ""))
    if not w:
        return None
    f_h, f_m, t_h, t_m = (int(w.group(i)) for i in range(1, 5))
    return {"parent": rid[:rid.rindex("-строка-")],
            "row": int(mm.group(1)), "col": int(mm.group(2)),
            "from_min": f_h * 60 + f_m,
            "to_min": t_h * 60 + t_m + 1,        # полуинтервал
            "value": params[0]["value"],
            "as_written": str(rule.get("statement") or ""),
            "rule": rid}


def assemble_matrices(doc: dict) -> list[dict]:
    """Матрицы порога смены из действующих ячеек вложенных таблиц.

    Калибровка колонок — ТОЛЬКО по ячейке, называющей сектора явно
    («1–2 сектора» на колонке K0): колонка K0 → (s_from, s_to),
    следующие колонки — последовательные одиночные значения секторов.
    Группа без калибровочной ячейки честно НЕ собирается (норма
    позицией без якоря — угадывание)."""
    groups: dict[str, list] = {}
    for r in doc.get("rules", []):
        cell = _cell_of(r)
        if cell:
            groups.setdefault(cell["parent"], []).append(cell)
    matrices: list[dict] = []
    for parent in sorted(groups):
        cells = groups[parent]
        if len(cells) < 4:
            continue
        calib = None
        for c in cells:
            sm = _SECTORS_RE.search(c["as_written"])
            if sm:
                s_from = int(sm.group(1))
                s_to = int(sm.group(2) or sm.group(1))
                calib = (c["col"], s_from, s_to)
                break
        if calib is None:
            continue
        k0, s_from0, s_to0 = calib
        rows = []
        for c in sorted(cells, key=lambda x: (x["row"], x["col"])):
            if c["col"] == k0:
                s_f, s_t = s_from0, s_to0
            elif c["col"] > k0:
                s_f = s_t = s_to0 + (c["col"] - k0)
            else:
                continue          # колонок левее калибровочной нет
            rows.append({"start_from_min": c["from_min"],
                         "start_to_min": c["to_min"],
                         "sectors_from": s_f, "sectors_to": s_t,
                         "value_min": c["value"],
                         "as_written": c["as_written"],
                         "rule": c["rule"]})
        if rows:
            matrices.append({
                "threshold": "max_duty_span_min",
                "tz": "base-local",
                "rows": rows,
                "source_rules": [c["rule"] for c in
                                 sorted(cells, key=lambda x:
                                        (x["row"], x["col"]))],
            })
    return matrices


def assemble_conditional_floors(doc: dict) -> list[dict]:
    """Условные полы отдыха из ДЕЙСТВУЮЩИХ правил: параметр
    min_rest_outstation (минуты, relation min) при формулировке с
    условием «разница часовых поясов ≥ N ч». Снятые носители (OUT)
    честно не участвуют — возврат правила в действие только ответом
    авиакомпании."""
    floors: list[dict] = []
    for r in doc.get("rules", []):
        if r.get("kind") not in _ACTIVE_KINDS:
            continue
        cond = _TZ_COND_RE.search(str(r.get("statement") or ""))
        if not cond:
            continue
        for p in _param_records(r, ("min_rest_outstation", "min_rest_outstation_min")):
            if p.get("name") in ("min_rest_outstation",
                                 "min_rest_outstation_min") \
                    and p.get("unit") == "duration_minutes" \
                    and p.get("relation") == "min" \
                    and isinstance(p.get("value"), int) \
                    and not isinstance(p.get("value"), bool):
                floors.append({
                    "threshold": "min_rest_outstation_min",
                    "condition": {"kind": "tz_diff_hours",
                                  "relation": "min",
                                  "value": int(cond.group(1))},
                    "value_min": p["value"],
                    "as_written": str(r.get("statement") or ""),
                    "rule": str(r.get("id")),
                })
    return sorted(floors, key=lambda f: f["rule"])


# Лестница продления при отдыхе в полёте (запрос владельца 2026-08-28,
# предмет «лестница усиления»): ступень «+1 член экипажа» — правило с
# явными классами «до N мин (класс K)»; ступень «расширенный экипаж» —
# тройка в скобках «(или A/B/C для расширенного…)». Порядок троек в
# документах — по классам 3/2/1 (значение растёт к классу 1).
_LADDER_CLASS_RE = re.compile(r"до\s+(\d{3,4})\s+мин\w*\s*"
                              r"\(класс\s+(\d)\)")
_LADDER_EXT_RE = re.compile(r"(\d{3,4})/(\d{3,4})/(\d{3,4})\s*минут"
                            r".{0,10}\(или\s+(\d{3,4})/(\d{3,4})/"
                            r"(\d{3,4})\s+для\s+расширенн")
_LADDER_BONUS_RE = re.compile(r"увеличива\w+\s+на\s+1\s*ч.*"
                              r"сектор\w*\s*>\s*9\s*ч", re.DOTALL)

# Пометка временности: ответ анкеты (одна лестница без ступеней
# состава) противоречит документам авиакомпании — до пересмотра
# ответа форма несёт лестницу ДОКУМЕНТОВ (вопрос-пересмотр
# ставит ensure_pairing_clarification_questions).
def _ladder_note(spec_version: str | None) -> str:
    """Ревизия 1.14: примечание лестницы обезличено в новых байтах
    (числа и ссылка на приложение документа заказчика ушли);
    легаси-текст пакетов ≤1.13 — из носителя контура (тот же закон
    версии, что warning формы флота в 1.13)."""
    if _spec_at_least(spec_version, "1.14"):
        return ("временная форма лестницы ДОКУМЕНТОВ (две ступени "
                "состава): ответ анкеты дал одну лестницу без "
                "ступеней и противоречит документам — до ответа на "
                "вопрос-пересмотр не применяется")
    return _legacy_form_text("extended_ladder_note")


def assemble_extended_ladder(doc: dict,
                             spec_version: str | None = None
                             ) -> dict | None:
    """Лестница продления служебного времени при отдыхе в полёте:
    ступени по числу дополнительных членов экипажа, значения по
    классам места отдыха (минуты). Детерминированно из действующих
    правил; нет носителей — нет формы (не выдумывается)."""
    step1 = None
    step2 = None
    bonus = None
    for r in doc.get("rules", []):
        if r.get("kind") not in _ACTIVE_KINDS:
            continue
        stmt = str(r.get("statement") or "")
        if step1 is None and "отдых" in stmt and "полет" in stmt:
            hits = _LADDER_CLASS_RE.findall(stmt)
            if len(hits) >= 3:
                by_class = {cls: int(val) for val, cls in hits}
                if len(by_class) >= 3:
                    step1 = {"extra_crew": 1,
                             "by_class_min": {k: by_class[k]
                                              for k in sorted(by_class)},
                             "as_written": stmt,
                             "rule": str(r.get("id"))}
        if step2 is None:
            mm = _LADDER_EXT_RE.search(stmt)
            if mm:
                a3, a2, a1, b3, b2, b1 = (int(mm.group(i))
                                          for i in range(1, 7))
                if a3 < a2 < a1 and b3 < b2 < b1:
                    if step1 is None:
                        step1 = {"extra_crew": 1,
                                 "by_class_min": {"1": a1, "2": a2,
                                                  "3": a3},
                                 "as_written": stmt,
                                 "rule": str(r.get("id"))}
                    step2 = {"extra_crew": 2,
                             "by_class_min": {"1": b1, "2": b2,
                                              "3": b3},
                             "as_written": stmt,
                             "rule": str(r.get("id"))}
        if bonus is None and _LADDER_BONUS_RE.search(stmt):
            bonus = {"add_min": 60,
                     "condition": {"kind": "one_sector_over_h",
                                   "value": 9, "max_sectors": 2},
                     "as_written": stmt,
                     "rule": str(r.get("id"))}
    if step1 is None and step2 is None:
        return None
    out = {"unit": "minutes",
           "steps": [s for s in (step1, step2) if s],
           "note": _ladder_note(spec_version)}
    if bonus is not None:
        out["long_sector_bonus"] = bonus
    return out


# Консенсус CDPM/COSMOS 2026-08-28 (счёт наихудших трактовок):
# пределы секторов по окну минимальной циркадной активности —
# машинной формой из определения документов (границы окна — из
# закреплённого определения «02:00–05:59 в поясе акклиматизации»;
# оговорка «как правило» — вопрос анкеты, не блокер формы).
def _wocl_condition_key(stmt_low: str) -> str | None:
    """Классификация условия касания окна по формулировке (обе
    редакции документов: «≤ 120 мин» и «на 2 часа или меньше»)."""
    if "отсутств" in stmt_low:
        return "no_touch"
    if "попадани" not in stmt_low:
        return None
    if "более чем на 2" in stmt_low or "> 120" in stmt_low \
            or "больше 120" in stmt_low:
        return "touch_gt_threshold"
    if "или меньше" in stmt_low or "≤ 120" in stmt_low \
            or "<= 120" in stmt_low or "≤120" in stmt_low:
        return "touch_le_threshold"
    return None


def assemble_wocl_sector_limits(doc: dict) -> dict | None:
    """Пределы числа секторов по касанию окна минимальной циркадной
    активности: значение — из согласованной числовой проекции посадки
    или старых params, условие — классификацией формулировки.
    Нет полной тройки носителей — нет формы."""
    limits: dict = {}
    rules: list[str] = []
    for r in doc.get("rules", []):
        if r.get("kind") not in _ACTIVE_KINDS:
            continue
        stmt = str(r.get("statement") or "")
        low = stmt.lower()
        if "циркадн" not in low:
            continue
        key = _wocl_condition_key(low)
        if key is None or key in limits:
            continue
        vals = [p.get("value") for p in _param_records(r, ("max_landings_per_duty",))
                if p.get("name") == "max_landings_per_duty"
                and isinstance(p.get("value"), int)
                and not isinstance(p.get("value"), bool)]
        if len(vals) != 1:
            continue
        limits[key] = vals[0]
        rules.append(str(r.get("id")))
    if len(limits) < 3:
        return None
    return {
        "threshold": "max_landings_per_duty",
        "window": {"from_min": 120, "to_min": 360,
                   "tz": "acclimatized"},
        "touch_threshold_min": 120,
        "limits": limits,
        "source_rules": sorted(set(rules)),
        "note": ("границы окна — из определения документов («как "
                 "правило, 02:00–05:59 в поясе акклиматизации»); "
                 "оговорка «как правило» — вопрос анкеты "
                 "(подтверждение границ)"),
    }


_FLEET_LIST_RE = re.compile(r"'([\w\-|]+\|[\w\-|]+)'")


def assemble_fleet_dh_marked(doc: dict,
                             spec_version: str | None = None
                             ) -> dict | None:
    """Перечень типов ВС из правила о маркировке засылок (всё, что не
    относится к внутреннему подразделению из правила-носителя):
    машинная форма перечня из правила-носителя (консенсус,
    пункт 13). СЕМАНТИКА ОПИСАТЕЛЬНАЯ (живой факт конвейера
    2026-08-28): правило kind DATA описывает разметку мастер-системы,
    а фактическая разметка книги — ТОЧЕЧНАЯ (транспортом помечена
    лишь часть рейсов перечисленных типов); буквальное сплошное
    применение по типам вывело бы из обязательств больше половины
    собственного плана авиакомпании, покрытого эталонами. Носитель
    истины — разметка данных (поле «Перегон»); граница множества
    типов, исключённых из автоматического планирования, — вопрос
    анкеты."""
    for r in doc.get("rules", []):
        if r.get("kind") not in _ACTIVE_KINDS:
            continue
        stmt = str(r.get("statement") or "")
        if "deadhead" not in stmt.lower() or "тип" not in stmt.lower():
            continue
        mm = _FLEET_LIST_RE.search(stmt)
        if mm:
            # Ревизия 1.13 (зачистка 2026-08-29): текст warning
            # обезличен — термин конкретного заказчика ушёл из новых
            # байтов. Пакеты ≤1.12 заморожены со СТАРЫМИ байтами;
            # легаси-текст — сам данные заказчика, поэтому живёт в
            # КОНТУРЕ (_legacy_form_text), а не литералом кода:
            # пересборочный гейт замороженного пакета работает только
            # там, где живёт контур этого заказчика — что честно,
            # ибо и сам замороженный пакет несёт его данные.
            if _spec_at_least(spec_version, "1.13"):
                warning = ("сплошное применение по типам ЗАПРЕЩЕНО "
                           "до ответа анкеты о границе множества "
                           "типов, исключённых из автоматического "
                           "планирования: противоречит фактической "
                           "разметке данных и плану авиакомпании "
                           "(вывело бы из обязательств больше "
                           "половины рейсов); носитель истины — "
                           "поле «Перегон» книги данных")
            else:
                warning = _legacy_form_text("fleet_dh_marked_warning")
            return {"types": mm.group(1).split("|"),
                    "action": "deadhead",
                    "semantics": "master_data_marking_description",
                    "warning": warning,
                    "as_written": stmt,
                    "rule": str(r.get("id"))}
    return None


_EARLY_BASE_RE = re.compile(
    r"вылет\w*\s+из\s+(\w{3})\s+до\s+0?(\d)(?::00)?\s*\(?UTC",
    re.IGNORECASE)


def assemble_early_base_departure(doc: dict) -> dict | None:
    """Порог раннего вылета из базы (запрет разворота в базовом):
    число из формулировки — машинным полем (консенсус, пункт 12б).
    Статус строки-источника спорен («отклонено» в диалоге
    интегратора) — вопрос анкеты; форма несёт след и предупреждение."""
    for r in doc.get("rules", []):
        if r.get("kind") not in _ACTIVE_KINDS:
            continue
        stmt = str(r.get("statement") or "")
        mm = _EARLY_BASE_RE.search(stmt)
        if mm and "разворот" in stmt.lower():
            return {"base": mm.group(1).upper(),
                    "cutoff_min": int(mm.group(2)) * 60,
                    "tz": "UTC",
                    "scope": "per_leg",
                    "effect": "no_base_turnaround",
                    "as_written": stmt,
                    "rule": str(r.get("id")),
                    "note": ("строка-источник помечена «отклонено» в "
                             "диалоге интегратора — статус уточняется "
                             "вопросом анкеты; до ответа действует")}
    return None


def _legacy_form_text(key: str) -> str:
    """Легаси-тексты форм замороженных пакетов (≤1.12) — данные
    заказчика, носитель — контур: cdpm-legacy-form-texts.json в
    каталоге данных (переопределение пути — переменная окружения
    CDPM_LEGACY_FORM_TEXTS). Отсутствие носителя = честный отказ:
    замороженный пакет заказчика пересобирается только там, где
    живёт его контур."""
    import os
    from pathlib import Path
    override = os.environ.get("CDPM_LEGACY_FORM_TEXTS")
    if override is not None:
        # Пустое значение = носитель не задан (изоляция тестов:
        # случайная пересборка легаси в наборе падает громко).
        if not override.strip():
            raise ValueError(
                f"легаси-текст формы {key!r} недоступен: носитель "
                f"контура не задан (CDPM_LEGACY_FORM_TEXTS пуст)")
        path = Path(override)
    else:
        import _data_paths
        path = _data_paths.data_root() / "cdpm-legacy-form-texts.json"
    if not path.is_file():
        raise ValueError(
            f"легаси-текст формы {key!r} недоступен: носитель "
            f"контура {path.name} отсутствует — пересборка "
            f"замороженного пакета (≤1.12) возможна только на "
            f"машине его контура")
    data = json.loads(path.read_text(encoding="utf-8"))
    v = data.get(key)
    if not isinstance(v, str) or not v:
        raise ValueError(
            f"легаси-текст формы {key!r} в носителе {path.name} "
            f"пуст или не строка")
    return v


def _spec_at_least(spec_version: str | None, floor: str) -> bool:
    """Закон версии (как у гейта пакета): формы судятся версией
    СВОЕГО пакета; None = текущая сборка (новейший закон). Байты
    замороженных пакетов неприкосновенны — пересборочный гейт обязан
    получать их же."""
    if spec_version is None:
        return True
    try:
        parts = [int(x) for x in str(spec_version).split(".")[:2]]
        return parts >= [int(x) for x in floor.split(".")[:2]]
    except ValueError:
        return True


# Календарные окна (семья calendar_windows потребителя). Контракт
# P-N-SHD 2026-08-30: форма — ПРОЕКЦИЯ family-правила, отбор и дни
# читаются из export_solver (проставляет классификатор
# model.assign_soft_families), не из регэкспов по формулировке —
# двойной разбор текста расходился бы с классификатором молча.


def assemble_calendar_window_penalties(doc: dict) -> list[dict]:
    """Мягкие штрафы календарного окна дней недели: SOFT-правило
    семьи calendar_windows (export_solver.family). Дни и пояс — из
    записи семьи; вес — из применённого ответа анкеты
    (export_solver.soft_weight); без веса форма несёт null (вес
    приедет ответом, потребитель судит блокеры пакета)."""
    from cdpm import export_solver as _es
    out: list[dict] = []
    for r in doc.get("rules", []):
        if r.get("kind") != "SOFT":
            continue
        fam = r.get("export_solver") or {}
        if not isinstance(fam, dict) \
                or fam.get("family") != "calendar_windows":
            continue
        days = fam.get("days")
        if not isinstance(days, list) or not days:
            continue         # fail-closed: запись семьи без дней
        out.append({"days": list(days),
                    "tz": fam.get("tz") or "base-local",
                    "action": "layover_penalty",
                    "weight": _es.soft_weight(r),
                    "rule": str(r.get("id")),
                    "as_written": str(r.get("statement") or "")})
    return sorted(out, key=lambda f: f["rule"])


def build_forms(doc: dict,
                spec_version: str | None = None) -> dict | None:
    """Содержимое threshold-forms.json либо None (форм нет).
    spec_version — версия пакета, чьи байты собираются (ревизия
    1.13: текст warning формы fleet_dh_marked зависит от версии)."""
    matrices = assemble_matrices(doc)
    floors = assemble_conditional_floors(doc)
    ladder = assemble_extended_ladder(doc, spec_version)
    wocl = assemble_wocl_sector_limits(doc)
    fleet = assemble_fleet_dh_marked(doc, spec_version)
    early = assemble_early_base_departure(doc)
    calendar = assemble_calendar_window_penalties(doc)
    if not matrices and not floors and not ladder \
            and not wocl and not fleet and not early and not calendar:
        return None
    out = {"spec": "1.10", "matrices": matrices,
           "conditional_floors": floors}
    # Добавления ключей: потребители 1.10 неизвестные ключи игнорируют
    # (принцип ревизии 1.10, п. 7); парсеры — приёмная часть COSMOS
    # (согласования 2026-08-28).
    if ladder is not None:
        out["extended_ladder"] = ladder
    if wocl is not None:
        out["wocl_sector_limits"] = wocl
    if fleet is not None:
        out["fleet_dh_marked"] = fleet
    if early is not None:
        out["early_base_departure"] = early
    if calendar:
        out["calendar_window_penalties"] = calendar
    return out


def forms_bytes(doc: dict,
                spec_version: str | None = None) -> bytes | None:
    forms = build_forms(doc, spec_version)
    if forms is None:
        return None
    return (json.dumps(forms, ensure_ascii=False, indent=1)
            + "\n").encode("utf-8")
