# -*- coding: utf-8 -*-
"""Замок части 1 анкеты: противоречие свода — ТОЛЬКО машинно видимое
расхождение значений ОДНОЙ нормы (слово владельца 11.09.2026).

Предложение модели о противоречии — чтение, а не факт. Вопрос части 1
блокирует ОБЕ строки свода, поэтому право родить его имеет только то
предложение, у которого код САМ ВИДИТ: у пары строк одна норма и
разные значения. Всё прочее — уточнение части 2: вопрос авиакомпании
задаётся, строки продолжают действовать.

ОДНА НОРМА — общий носитель у обеих строк:

* одна семья посадки (`template_fit.family` категорий A/B) и одно имя
  параметра, либо
* один предмет (`logic.subject`, иначе имя формальной записи) и одно
  имя параметра;
* записи с разными уточнителями (`qualifier` параметра, `condition`
  параметра посадки) — РАЗНЫЕ нормы: уточнитель ровно для того и
  заведён (норма резерва по каждой базе не спорит сама с собой).

РАЗНЫЕ ЗНАЧЕНИЯ — значения этих записей не совпадают машинно: числа
сравниваются числами (времяподобные — в минутах канонизацией
`model.canon_minutes`), перечни — множествами. Параметров нет ни у
одной строки — сравниваются значения формальной записи: записи,
различающиеся ТОЛЬКО числом, — одна норма с разными значениями.

ОБЛАСТИ ПРИМЕНИМОСТИ обязаны совпадать или пересекаться. Разные зоны,
разные области действия (`scope`), разные условия (`logic.condition`) —
это две нормы для разных случаев, а не спор; предел и его условное
продление живут вместе.

Функция чистая и детерминированная: один и тот же вход даёт один и тот
же ответ, перебор идёт порядком предложения и порядком имён записей.

    evidence(rules) -> (свидетельство | None, причина словами)

Свидетельство:

    {"norm": {"family"|"subject": …, "param": …, "qualifier": …},
     "values": [{"rule_id", "value", "unit", "source"}, …]}
"""
from __future__ import annotations

import json
import re

from cdpm import model as m

# Число формальной записи и слово-единица сразу за ним: канонизацию
# времяподобных следов делает model.canon_minutes и только она.
_NUMBER_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
_UNIT_RE = re.compile(r"\s*([А-Яа-яЁёA-Za-z]{1,12})\.?")
# Имя формальной записи: латинский идентификатор (rest_home,
# flight_time) — им ВМП называет предмет нормы.
_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_LIST_SEP_RE = re.compile(r"[,;/]")


# ---------------------------------------------------------------------------
# Канон значений
# ---------------------------------------------------------------------------

def _canon_value(raw, unit=None):
    """Каноническое значение записи для машинного сравнения: («ч»,
    число) у чисел и времяподобных следов с единицей, («м», множество)
    у перечня, («с», строка) у прочего. Тип в паре нужен, чтобы число 10
    и строка «10 баз» не слились случайным равенством."""
    if isinstance(raw, bool):
        return ("л", raw)
    if isinstance(raw, (int, float)):
        if isinstance(unit, str) and unit.strip():
            mins = m.canon_minutes(f"{raw} {unit}")
            if mins is not None:
                return ("ч", float(mins))
        return ("ч", float(raw))
    if isinstance(raw, (list, tuple, set, frozenset)):
        return ("м", frozenset(_canon_value(x) for x in raw))
    if raw is None:
        return ("п", None)
    s = " ".join(str(raw).split())
    if not s:
        return ("п", None)
    mins = m.canon_minutes(s)
    if mins is not None:
        return ("ч", float(mins))
    try:
        return ("ч", float(s.replace(",", ".")))
    except ValueError:
        pass
    if _LIST_SEP_RE.search(s):
        items = [p.strip().casefold() for p in _LIST_SEP_RE.split(s)
                 if p.strip()]
        if len(items) > 1:
            return ("м", frozenset(items))
    return ("с", s.casefold())


# ---------------------------------------------------------------------------
# Машинные носители нормы у строки свода
# ---------------------------------------------------------------------------

def _records(rule: dict) -> dict:
    """Машинные записи значений строки: {(имя, уточнитель): ячейка}.
    Носителей два и они равноправны — параметры посадки A/B
    (`template_fit.params`) и `params[]` (П6: у эталонных сводов
    значение живёт только в посадке)."""
    out: dict = {}
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        for name, cell in (fit.get("params") or {}).items():
            if not isinstance(cell, dict) or not isinstance(name, str):
                continue
            cond = cell.get("condition")
            out.setdefault((name, cond if isinstance(cond, str) else None),
                           {"value": cell.get("value"),
                            "unit": cell.get("unit"),
                            "as_written": cell.get("quote")})
    for p in (rule.get("params") or []):
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            continue
        qual = p.get("qualifier")
        out.setdefault((p["name"], qual if isinstance(qual, str) else None),
                       {"value": p.get("value"), "unit": p.get("unit"),
                        "as_written": p.get("as_written")})
    return out


def _family(rule: dict):
    """Семья посадки A/B строки либо None (C/D и отсутствие посадки
    носителем нормы не считаются: ярлыка семьи у них нет)."""
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        fam = fit.get("family")
        if isinstance(fam, str) and fam.strip():
            return fam.strip()
    return None


def _subject(rule: dict):
    """Предмет строки: `logic.subject` машинной логики, иначе имя
    формальной записи (первый латинский идентификатор). None — предмет
    машинно не назван."""
    logic = rule.get("logic")
    if isinstance(logic, dict):
        subj = logic.get("subject")
        if isinstance(subj, str) and subj.strip():
            return subj.strip()
    mm = _IDENT_RE.search(str(rule.get("statement") or ""))
    return mm.group(0) if mm else None


def _conditions(rule: dict) -> tuple:
    """Условия применения машинной логики строки канонической формой
    (порядок записи условий значения не имеет)."""
    logic = rule.get("logic")
    if not isinstance(logic, dict):
        return ()
    conds = logic.get("condition")
    if not isinstance(conds, list):
        return ()
    return tuple(sorted(json.dumps(c, ensure_ascii=False, sort_keys=True)
                        for c in conds))


def _scope_key(rule: dict):
    """Область действия параметра мира словами (поле `scope` формы ВМП)
    в сравнимой форме либо None, если области нет."""
    scope = rule.get("scope")
    if not isinstance(scope, dict):
        return None
    return tuple(sorted(
        (str(k), " ".join(str(v).casefold().split()))
        for k, v in scope.items()))


def _source(rule: dict):
    """Первое место источника строки {"sid", "anchor"} либо None."""
    for ref in (rule.get("sources") or []):
        if not isinstance(ref, dict):
            continue
        sid, anchor = ref.get("sid"), ref.get("anchor")
        if isinstance(sid, str) and sid and isinstance(anchor, str) \
                and anchor:
            return {"sid": sid, "anchor": anchor}
    return None


# ---------------------------------------------------------------------------
# Формальная запись: маска и значения
# ---------------------------------------------------------------------------

def _shape(statement) -> tuple:
    """Маска формальной записи и её значения: «rest_home ≥ 10 ч» даёт
    («rest_home ≥ # ч», [{"raw": "10", "unit": "ч", …}]). Маска — та же
    запись с числами, заменёнными на «#»: две записи с одной маской
    различаются ТОЛЬКО значениями."""
    s = " ".join(str(statement or "").split())
    parts: list = []
    values: list = []
    pos = 0
    for mm in _NUMBER_RE.finditer(s):
        parts.append(s[pos:mm.start()])
        parts.append("#")
        um = _UNIT_RE.match(s[mm.end():])
        idents = _IDENT_RE.findall(s[:mm.start()])
        values.append({"raw": mm.group(0),
                       "unit": um.group(1) if um else None,
                       "ident": idents[-1] if idents else None})
        pos = mm.end()
    parts.append(s[pos:])
    return "".join(parts), values


# ---------------------------------------------------------------------------
# Свидетельство
# ---------------------------------------------------------------------------

def _applicability_mismatch(a: dict, b: dict):
    """Причина, по которой строки применяются к РАЗНЫМ случаям, либо
    None (области одинаковы или пересекаются)."""
    za, zb = a.get("zone"), b.get("zone")
    if za != zb and "BOTH" not in (za, zb):
        return f"зоны {za!r} и {zb!r} не пересекаются"
    for field, word in (("tasks", "задачам"), ("modes", "режимам")):
        sa = (a.get("applicability") or {}).get(field)
        sb = (b.get("applicability") or {}).get(field)
        if isinstance(sa, list) and isinstance(sb, list) and sa and sb \
                and not (set(map(str, sa)) & set(map(str, sb))):
            return (f"применимость по {word} не пересекается: "
                    f"{sorted(map(str, sa))} и {sorted(map(str, sb))}")
    ka, kb = _scope_key(a), _scope_key(b)
    if ka is not None and kb is not None and ka != kb:
        return "области действия (scope) строк заданы по-разному"
    ca, cb = _conditions(a), _conditions(b)
    if ca != cb:
        return ("условия применения строк различаются — это нормы для "
                "разных случаев, не спор об одном")
    return None


def _carriers(rules: list, key: tuple) -> list:
    """Строки предложения, несущие машинную запись `key`, порядком
    предложения."""
    out = []
    for r in rules:
        cell = _records(r).get(key)
        if cell is not None:
            out.append((r, cell))
    return out


def _values_block(carriers: list):
    """Блок значений свидетельства либо None, если у строки-носителя нет
    места источника (fail-closed: свидетельство без адреса не
    предъявляется)."""
    values = []
    for rule, cell in carriers:
        src = _source(rule)
        if src is None:
            return None
        unit = cell.get("unit")
        values.append({"rule_id": str(rule.get("id")),
                       "value": cell.get("value"),
                       "unit": unit if isinstance(unit, str) and unit
                       else None,
                       "source": src})
    return values


def _pair_evidence(a: dict, b: dict, rules: list) -> tuple:
    """Свидетельство по паре строк либо (None, причина)."""
    why = _applicability_mismatch(a, b)
    if why:
        return None, why
    ra, rb = _records(a), _records(b)
    shared = sorted(set(ra) & set(rb), key=lambda k: (k[0], str(k[1])))
    if shared:
        fam_a, fam_b = _family(a), _family(b)
        fam = fam_a if fam_a and fam_a == fam_b else None
        subj = None
        if fam is None:
            sa, sb = _subject(a), _subject(b)
            subj = sa if sa and sa == sb else None
        if fam is None and subj is None:
            return None, (f"нормы разные: у строк нет ни общей семьи "
                          f"посадки ({fam_a!r} и {fam_b!r}), ни общего "
                          f"предмета ({_subject(a)!r} и {_subject(b)!r})")
        same, unknown = [], []
        for key in shared:
            name, qual = key
            va = _canon_value(ra[key].get("value"), ra[key].get("unit"))
            vb = _canon_value(rb[key].get("value"), rb[key].get("unit"))
            if va == vb:
                same.append(name)
                continue
            # Fail-closed: пустое значение и значения разной природы
            # (число против слов, код против перечня) расхождением не
            # объявляются — код их не сравнил, а не увидел спор.
            if va[0] == "п" or vb[0] == "п" or va[0] != vb[0]:
                unknown.append(name)
                continue
            values = _values_block(_carriers(rules, key))
            if values is None:
                return None, (f"у строки-носителя записи {name!r} нет "
                              f"места источника — свидетельство не "
                              f"собирается")
            norm = {"family": fam} if fam else {"subject": subj}
            norm["param"] = name
            if qual:
                norm["qualifier"] = qual
            return {"norm": norm, "values": values}, ""
        if unknown:
            return None, (f"значения записей ({', '.join(sorted(unknown))}) "
                          f"машинно несопоставимы — пусты или разной "
                          f"природы")
        return None, (f"значения общих записей ({', '.join(sorted(same))}) "
                      f"машинно совпадают — расхождения нет")
    if ra or rb:
        return None, ("общих машинных записей значений у строк нет: "
                      f"{sorted(n for n, _q in ra)} и "
                      f"{sorted(n for n, _q in rb)}")
    # Параметров нет ни у одной строки — судится формальная запись.
    mask_a, vals_a = _shape(a.get("statement"))
    mask_b, vals_b = _shape(b.get("statement"))
    if not vals_a or not vals_b:
        return None, ("в формальных записях строк нет чисел — расхождение "
                      "значений машинно не видно")
    if mask_a != mask_b:
        return None, ("формальные записи строк различаются не только "
                      "значением — это разные нормы")
    for pos, (xa, xb) in enumerate(zip(vals_a, vals_b)):
        if _canon_value(xa["raw"], xa["unit"]) \
                == _canon_value(xb["raw"], xb["unit"]):
            continue
        name = xa["ident"] or "формальная запись"
        values = []
        for rule in rules:
            mask_r, vals_r = _shape(rule.get("statement"))
            if mask_r != mask_a or pos >= len(vals_r):
                continue
            src = _source(rule)
            if src is None:
                return None, ("у строки-носителя формальной записи нет "
                              "места источника — свидетельство не "
                              "собирается")
            cell = vals_r[pos]
            values.append({"rule_id": str(rule.get("id")),
                           "value": cell["raw"], "unit": cell["unit"],
                           "source": src})
        return {"norm": {"subject": xa["ident"] or mask_a,
                         "param": name},
                "values": values}, ""
    return None, "значения формальных записей строк машинно совпадают"


def evidence(rules: list) -> tuple:
    """Машинное свидетельство расхождения значений одной нормы у строк
    предложения: (свидетельство, "") либо (None, причина словами).

    Перебор — парами в порядке предложения; первая пара со свидетельством
    его и даёт. Причина возвращается от ПЕРВОЙ пары: у предложения из
    двух строк (обычный случай) пара одна."""
    rows = [r for r in rules if isinstance(r, dict)]
    if len(rows) < 2:
        return None, "предложение ссылается менее чем на две строки свода"
    reason = ""
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            ev, why = _pair_evidence(rows[i], rows[j], rows)
            if ev is not None:
                return ev, ""
            if not reason:
                reason = why
    return None, reason
