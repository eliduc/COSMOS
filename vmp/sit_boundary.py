# -*- coding: utf-8 -*-
"""Граница «стыковка / отдых» — из правил пакета, одна у построителя
цепочек, решателя ростеринга и проверки решений по ИДАК (решение
владельца 06.09.2026, P3-1 / GPT-049; уточнения GPT-053, принятые
CDPM-038; только локально).

ТРИ РАЗНЫЕ ВЕЩИ (различаются словами везде — в провенансе, протоколе
ИДАК, снимке блока):
- МАКСИМАЛЬНАЯ СТЫКОВКА — правило пакета `max_sit_duration` (ручка
  решателя → порог `day_break_threshold_min`): ограничение построения,
  необязательное;
- КРИТЕРИЙ ГРАНИЦЫ СМЕНЫ — где смена кончается: при объявленном правиле
  максимальной стыковки — разрыв не короче его порога; без него — разрыв
  не короче пола минимального отдыха ПО МЕСТУ (база / вне базы) — это
  ЯВНОЕ МОДЕЛЬНОЕ УПРОЩЕНИЕ (см. ASSUMPTION), а не нормативная проверка
  EASA/FAA;
- ДОСТАТОЧНОСТЬ ОТДЫХА — обязательное правило: пол минимального отдыха
  по месту. Отсутствие необязательного правила максимальной стыковки
  обязательных правил отдыха не отменяет.

ОПРЕДЕЛЕНИЕ (вместо параметра). Отдых — перерыв не короче минимального
отдыха по правилам пакета (пол по месту). Перерыв короче пола — внутри
смены. Отдельный промежуточный порог допустим только когда в пакете есть
правило максимальной стыковки / разделённой смены — тогда порог из него.

ОДИН ИСТОЧНИК ЗНАЧЕНИЯ. Порог вычисляется из применённых порогов решателя
для ЭТОГО пакета — имена ручек одни и те же в отчёте покрытия
(`tasks.<задача>.thresholds.applied`, кнопка «Пейринг») и в показаниях
прогона (`lastRun.profileReadings`, проверка ростера по ИДАК):

- `day_break_threshold_min` > 0 → T = ручка (правило пакета
  `max_sit_duration`); построитель получает `--sit-max` = T;
- иначе граница — пол минимального отдыха по месту (`min_rest_home_min`
  / `min_rest_outstation_min`); показанием T — меньший из полов;
  построитель `--sit-max` НЕ получает и режет смену по полу отдыха
  станции сам (те же --rest-base / --rest-off из applied);
- ни ручки, ни пола → значение НЕ ОПРЕДЕЛЕНО: кнопка останавливается
  словами UNDEFINED, проверка по ИДАК отвечает not_checkable с причиной.
  Подстановок нет: литерал 360 (`webapp.chain_seed.SOLVER_DAY_BREAK_MIN`)
  — контракт Kotlin по умолчанию профиля ВНЕ пакета, для пакетных
  прогонов он не читается.

ПАРИТЕТ. Решатель без ручки режет нормативные смены по полу отдыха по
месту и «Rest between duty periods» не судит (порог 0) — то есть по
построению судит границей = пол отдыха по месту; с ручкой — порогом из
неё. Построитель без --sit-max режет по полу отдыха станции
(sit_cap_at); проверка по ИДАК режет линию времени тем же критерием
(source_check.split_duties_by_place / split_duties) и судит достаточность
отдыха полом клаузы по месту. Разрыв не короче пола — отдых не короче
пола: нарушений по построению быть не может; тест паритета с
независимыми ожиданиями — tests/test_sit_boundary_parity.py.
"""
from __future__ import annotations

#: Порог решателя, в который транслируется ручка пакета (RunThresholds.KNOBS).
KNOB = "day_break_threshold_min"
#: Имя параметра пакета — правило максимальной стыковки / разделённой смены.
KNOB_PARAM = "max_sit_duration"
#: Имена полов отдыха в applied / показаниях (первое найденное — действует).
REST_HOME = ("min_rest_home_min", "min_rest_home")
REST_OUT = ("min_rest_outstation_min", "min_rest_outstation")
#: Единый пол без разделения по месту (старые профили).
REST_ANY = ("min_rest_min",)
#: Слова остановки кнопки и причины not_checkable.
UNDEFINED = "граница смены не определена правилами пакета"
#: Явное модельное упрощение запасного критерия границы смены
#: (GPT-053 / CDPM-038): звучит одинаково в провенансе, протоколе, снимке.
ASSUMPTION = (
    "МОДЕЛЬНОЕ УПРОЩЕНИЕ (не нормативная проверка EASA/FAA): правило "
    "максимальной стыковки в пакете не объявлено, и критерием границы "
    "смены принят пол минимального отдыха по месту — длительность разрыва "
    "между рейсами принята за квалифицируемый отдых; условия размещения и "
    "свободы от обязанностей не проверяются; разделённая смена в "
    "межсменный отдых не превращается. Достаточность отдыха (пол по месту) "
    "— обязательное правило и судится отдельно; отсутствие необязательного "
    "правила максимальной стыковки его не отменяет.")
#: Словарь терминов — одной строкой для протокола и снимка.
TERMS = ("максимальная стыковка = правило пакета max_sit_duration "
         "(порог day_break_threshold_min); критерий границы смены = порог "
         "правила, без него — пол минимального отдыха по месту (модельное "
         "упрощение); достаточность отдыха = пол минимального отдыха по "
         "месту (обязательное правило)")


def _int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def _pick(readings: dict, names: tuple[str, ...]):
    for n in names:
        v = _int(readings.get(n))
        if v is not None:
            return n, v
    return None, None


def rules_by_threshold(entries) -> dict[str, list[str]]:
    """{порог → [id правил]} из списка записей applied решателя
    (`{threshold, value, rules, …}` — отчёт покрытия и
    `lastRun.thresholds.applied` показаний прогона). Не список — пусто."""
    out: dict[str, list[str]] = {}
    for e in entries if isinstance(entries, list) else ():
        if not isinstance(e, dict) or e.get("threshold") is None:
            continue
        rules = e.get("rules")
        if isinstance(rules, list) and rules:
            out[str(e["threshold"])] = [str(r) for r in rules]
    return out


def _rule_ids(rules_of: dict | None, *names: str) -> list[str]:
    ids: list[str] = []
    for n in names:
        for r in (rules_of or {}).get(n) or ():
            if r not in ids:
                ids.append(r)
    return ids


def _rule_words(ids: list[str]) -> str:
    if not ids:
        return "правило пакета показаниями не названо"
    return "правил" + ("о " if len(ids) == 1 else "а ") + ", ".join(ids)


def _by_place(floors: dict) -> dict:
    """Пол отдыха по месту из прочитанных полов: дома — min_rest_home*,
    вне базы — min_rest_outstation*, единый пол — на обе стороны; сторона
    без своего пола берёт известный (пол одной стороны прочитан)."""
    home = next((v for n, v in floors.items() if n in REST_HOME), None)
    out = next((v for n, v in floors.items() if n in REST_OUT), None)
    any_ = next((v for n, v in floors.items() if n in REST_ANY), None)
    home = home if home is not None else any_
    out = out if out is not None else any_
    if home is None and out is not None:
        home = out
    if out is None and home is not None:
        out = home
    return {"home": home, "outstation": out}


def _place_words(by_place: dict) -> str:
    return (f"дома {by_place.get('home')} / вне базы "
            f"{by_place.get('outstation')}")


def derive(readings: dict | None, *, rules_of: dict | None = None) -> dict:
    """Граница «стыковка / отдых» по применённым порогам решателя (см.
    докстринг модуля).

    readings — плоский словарь имя порога → значение (applied отчёта
    покрытия либо lastRun.profileReadings показаний прогона);
    rules_of — {порог → [id правил]} для провенанса (rules_by_threshold).

    Возврат: {"value": int | None — T показанием (ручка либо меньший пол),
              "basis": "knob" | "rest_floor" | None,
              "reading": имя порога-источника | None,
              "floors": {имя: значение} — полы отдыха, что прочитаны,
              "by_place": {"home": R | None, "outstation": R | None},
              "rules": [id правил], "assumption": str | None (модельное
              упрощение при basis rest_floor), "source": провенанс словами}."""
    r = readings if isinstance(readings, dict) else {}
    knob = _int(r.get(KNOB))
    floors: dict[str, int] = {}
    for names in (REST_HOME, REST_OUT, REST_ANY):
        n, v = _pick(r, names)
        if n is not None and v is not None and v > 0:
            floors[n] = v
    by_place = _by_place(floors)
    floor_ids = _rule_ids(rules_of, *floors)
    if knob is not None and knob > 0:
        ids = _rule_ids(rules_of, KNOB)
        return {
            "value": knob, "basis": "knob", "reading": KNOB,
            "floors": floors, "by_place": by_place, "rules": ids,
            "assumption": None,
            "source": (f"правило пакета ({KNOB_PARAM}): {_rule_words(ids)}; "
                       f"{KNOB}={knob} — максимальная стыковка, критерий "
                       f"границы смены; достаточность отдыха — пол по месту"
                       + (f" ({_place_words(by_place)}; "
                          f"{_rule_words(floor_ids)})" if floors
                          else " (полов отдыха в применённых порогах нет)"))}
    if floors:
        reading, value = min(floors.items(), key=lambda kv: kv[1])
        return {
            "value": value, "basis": "rest_floor", "reading": reading,
            "floors": floors, "by_place": by_place, "rules": floor_ids,
            "assumption": ASSUMPTION,
            "source": (f"выведено из пола минимального отдыха {value} мин "
                       f"({_rule_words(floor_ids)}; "
                       + ", ".join(f"{k}={v}" for k, v in floors.items())
                       + (f"; ручки {KNOB_PARAM} в пакете нет — {KNOB}=0"
                          if knob == 0 else
                          f"; показания {KNOB} нет")
                       + "): критерий границы смены — пол минимального "
                       f"отдыха по месту ({_place_words(by_place)}) — "
                       "модельное упрощение; достаточность отдыха — тот же "
                       "пол по месту, обязательное правило")}
    return {
        "value": None, "basis": None, "reading": None,
        "floors": {}, "by_place": by_place, "rules": [],
        "assumption": None,
        "source": (f"{UNDEFINED}: в применённых порогах нет ни "
                   f"{KNOB} (ручка {KNOB_PARAM}), ни пола отдыха "
                   f"({', '.join(REST_HOME[:1] + REST_OUT[:1] + REST_ANY)})"
                   + (f"; {KNOB}={knob}" if knob is not None else ""))}


def derive_from_builder_args(args: list) -> dict:
    """То же определение для ведомости входа построителя без --sit-max:
    построитель режет по полу отдыха станции (--rest-base / --rest-off)
    — критерий границы смены = пол по месту; полов нет — не определено."""
    vals: dict[str, int] = {}
    for key, name in (("--rest-base", REST_HOME[0]),
                      ("--rest-off", REST_OUT[0])):
        for i, a in enumerate(args):
            if str(a) == key and i + 1 < len(args):
                v = _int(args[i + 1])
                if v is not None and v > 0:
                    vals[name] = v
    got = derive(vals)
    if got["value"] is not None:
        got["source"] = ("выведено из пола отдыха построителя (по месту: "
                         + ", ".join(f"{k}={v}" for k, v in vals.items())
                         + f"; показанием T — меньший {got['value']} мин) — "
                         "--sit-max в ведомости не задан: правила "
                         "максимальной стыковки в пакете нет, построитель "
                         "режет смену по полу отдыха станции — модельное "
                         "упрощение")
    else:
        got["source"] = (f"{UNDEFINED}: в ведомости входа построителя нет "
                         "ни --sit-max, ни полов отдыха --rest-base/--rest-off")
    return got
