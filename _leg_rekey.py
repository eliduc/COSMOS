"""Re-key повторных flightNumber на границе солвера (аудит-2, «Пакет B»).

Kotlin-идентичность на проводе = flightNumber и НЕ меняется (эмпирика
DESIGN-flightLegId-crewSlotId-RU.md: Jackson не умеет фолбэк-идентичность,
замена ломает легаси). Вместо этого конвейер строит ДЕТЕРМИНИРОВАННУЮ
биекцию: перед солвером повторные wire-номера переименовываются в
уникальные синтетические (`F100@1`, `F100@2` — по порядку flightLegId),
после — восстанавливаются в публикуемых артефактах. Внутри конвейера всё
уже джойнится по leg_id/slot_key (_leg_identity), поэтому достаточно
уникальности wire-имён.

Свойства:
  * отображение — чистая функция payload'а: любая стадия пересчитывает его
    локально и получает тот же результат; сохранённые копии — штампы
    целостности, не источник истины;
  * payload БЕЗ повторов возвращается ТЕМ ЖЕ объектом с пустой картой —
    канонические GERAD-артефакты не меняются ни на байт;
  * переименовываются ВСЕ вхождения группы (голого `F100` не остаётся):
    случайный джойн по старому имени промахивается громко, а не молча
    пришивается к первому вхождению;
  * блокеры (слот без flightLegId у повторного номера, `@` в номере,
    коллизия синтетики) — честный отказ через _leg_identity.
"""
from __future__ import annotations

import copy
import os

SEP = "@"


def _flights(payload: dict) -> list:
    return [f for f in (payload.get("flights") or []) if isinstance(f, dict)]


def _groups(payload: dict) -> dict[str, list]:
    by_fn: dict[str, list] = {}
    for f in _flights(payload):
        fn = f.get("flightNumber")
        if isinstance(fn, str) and fn:
            by_fn.setdefault(fn, []).append(f)
    return {fn: fs for fn, fs in by_fn.items() if len(fs) > 1}


def rekey_enabled() -> bool:
    return os.environ.get("CREW_GERAD_REKEY", "1") != "0"


def needs_rekey(payload: dict) -> bool:
    return bool(_groups(payload))


def rekey_blockers(payload: dict) -> list[str]:
    """Причины, по которым биекция НЕ строится (честный отказ)."""
    groups = _groups(payload)
    if not groups:
        return []
    reasons: list[str] = []
    if not rekey_enabled():
        reasons.append("re-key выключен (CREW_GERAD_REKEY=0) — повторные "
                       "номера не решаемы")
        return reasons
    all_fns = {f.get("flightNumber") for f in _flights(payload)}
    leg_ids: list = [f.get("flightLegId") for f in _flights(payload)
                     if f.get("flightLegId") is not None]
    if len(leg_ids) != len(set(map(str, leg_ids))):
        reasons.append("flightLegId не уникальны — биекция wire-имён "
                       "невозможна")
    for fn, fs in sorted(groups.items()):
        if SEP in fn:
            reasons.append(f"номер {fn!r} содержит {SEP!r} — риск коллизии "
                           f"с синтетическими wire-именами")
        for f in fs:
            lid = f.get("flightLegId")
            if not isinstance(lid, str) or not lid:
                reasons.append(
                    f"повторный номер {fn!r}: не у каждого вхождения есть "
                    f"flightLegId — материализуйте идентичность")
                break
        for i in range(1, len(fs) + 1):
            if f"{fn}{SEP}{i}" in all_fns:
                reasons.append(f"синтетическое имя {fn}{SEP}{i} совпадает "
                               f"с существующим номером")
    # покрытие без flightAssignments сертифицируется legacy-путём «по
    # номерам» — при повторных номерах он ложно считает восстановленное
    # решение дубликатом (ревью пакета): честный отказ
    if not (payload.get("flightAssignments") or []):
        reasons.append("payload без flightAssignments с повторными "
                       "номерами — покрытие не сертифицируемо")
    # слоты повторных номеров обязаны нести flightLegId (иначе ссылку
    # `flight: fn` не разрешить в конкретное вхождение)
    dup_fns = set(groups)
    legs_of_group = {str(f.get("flightLegId"))
                     for fn in dup_fns for f in groups[fn]}
    for i, s in enumerate(payload.get("flightAssignments") or []):
        if not isinstance(s, dict):
            continue
        if s.get("flight") in dup_fns:
            slid = s.get("flightLegId")
            if not isinstance(slid, str) or not slid:
                reasons.append(
                    f"flightAssignments[{i}]: слот повторного номера "
                    f"{s.get('flight')!r} без flightLegId")
            elif slid not in legs_of_group:
                reasons.append(
                    f"flightAssignments[{i}]: flightLegId {slid!r} не "
                    f"принадлежит ни одному вхождению номера "
                    f"{s.get('flight')!r}")
    return sorted(set(reasons))


def rekey_payload(payload: dict):
    """(payload_wire, wire_map). Без повторов — ТОТ ЖЕ объект и {}.

    wire_map: {wire_fn: {"flightNumber": исходный, "flightLegId": lid}}.
    """
    groups = _groups(payload)
    if not groups:
        return payload, {}
    blockers = rekey_blockers(payload)
    if blockers:
        raise ValueError("re-key заблокирован: " + "; ".join(blockers))
    rename: dict[str, str] = {}          # leg_id -> wire_fn
    wire_map: dict[str, dict] = {}
    for fn, fs in groups.items():
        for i, f in enumerate(sorted(fs, key=lambda x: str(x["flightLegId"])),
                              start=1):
            wire = f"{fn}{SEP}{i}"
            rename[str(f["flightLegId"])] = wire
            wire_map[wire] = {"flightNumber": fn,
                              "flightLegId": str(f["flightLegId"])}
    dup_fns = set(groups)
    out = copy.deepcopy(payload)
    for f in _flights(out):
        lid = str(f.get("flightLegId"))
        if lid in rename:
            f["flightNumber"] = rename[lid]
    for s in out.get("flightAssignments") or []:
        if isinstance(s, dict) and s.get("flight") in dup_fns:
            s["flight"] = rename[str(s["flightLegId"])]
    # префы: «предпочитаю рейсы этого номера» разворачиваются во все
    # wire-имена группы — иначе Kotlin-констрейнты молча теряют префы
    group_wires = {fn: sorted(w for w, rec in wire_map.items()
                              if rec["flightNumber"] == fn)
                   for fn in dup_fns}
    for e in out.get("employees") or []:
        prefs = e.get("preferredFlights")
        if not isinstance(prefs, list):
            continue
        new: list = []
        for p in prefs:
            if p in group_wires:
                new.extend(group_wires[p])
            else:
                new.append(p)
        e["preferredFlights"] = new
    return out, wire_map


def restore_solution(solution: dict, wire_map: dict) -> dict:
    """Возврат исходных flightNumber в flight-level решение (артефакты и
    CSV показывают исходный номер + flightLegId; wire — внутренняя кухня).
    Развёрнутые wire-префы сотрудников сворачиваются обратно (ревью
    пакета: опубликованное решение не должно нести F100@1/F100@2 при
    рейсах F100)."""
    if not wire_map:
        return solution
    out = copy.deepcopy(solution)
    for f in out.get("flights") or []:
        if isinstance(f, dict) and f.get("flightNumber") in wire_map:
            f["flightNumber"] = wire_map[f["flightNumber"]]["flightNumber"]
    for s in out.get("flightAssignments") or []:
        if isinstance(s, dict) and s.get("flight") in wire_map:
            rec = wire_map[s["flight"]]
            s.setdefault("flightLegId", rec["flightLegId"])
            s["flight"] = rec["flightNumber"]
    for e in out.get("employees") or []:
        prefs = e.get("preferredFlights") if isinstance(e, dict) else None
        if not isinstance(prefs, list):
            continue
        seen: set = set()
        folded: list = []
        for p in prefs:
            orig = wire_map.get(p, {}).get("flightNumber", p)
            if orig not in seen:
                seen.add(orig)
                folded.append(orig)
        e["preferredFlights"] = folded
    return out


def rekey_or_passthrough(payload: dict):
    """rekey_payload, но при заблокированной биекции и ВЫКЛЮЧЕННОМ строгом
    гарде (CREW_GERAD_STRICT_IDENTITY=0 — аварийный рубильник) — старое
    поведение passthrough (коллапс по номеру), а не непойманный ValueError
    (ревью пакета: рубильник перестал быть рубильником)."""
    try:
        return rekey_payload(payload)
    except ValueError:
        from _leg_identity import strict_identity_enabled
        if strict_identity_enabled():
            raise
        return payload, {}


def translate_fns(fns, wire_map: dict) -> list:
    """wire-имена -> исходные (для allowed_missing/повествования)."""
    if not wire_map:
        return list(fns or [])
    return [wire_map.get(fn, {}).get("flightNumber", fn)
            for fn in (fns or [])]


def wire_to_leg_ids(fns, wire_map: dict) -> set:
    """wire-имена -> flightLegId (для сверки с leg_id-пространством
    coverage-валидатора)."""
    out = set()
    for fn in (fns or []):
        rec = wire_map.get(fn)
        out.add(rec["flightLegId"] if rec else fn)
    return out


def leg_ids_to_wire(fns, wire_map: dict) -> list:
    """Обратный перевод: addr-пространство (flightLegId для повторных,
    номер для уникальных) -> wire-имена данного payload'а. Для строк без
    соответствия в биекции возвращается сама строка (уникальный номер =
    свой же wire). Трек occurrence-идентичности операционного слоя:
    merge_catalog ремонта публикует каталог в wire derived-payload'а."""
    if not wire_map:
        return list(fns or [])
    by_leg = {rec["flightLegId"]: wfn for wfn, rec in wire_map.items()}
    return [by_leg.get(fn, fn) for fn in (fns or [])]
