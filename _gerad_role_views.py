"""Фаза 2b (multirole): экстрактор пер-ролевых представлений payload'а.

Из payload'а с ОДНОРОДНОЙ сигнатурой мест (модель со-локации фазы 2) строит по
одному ОДНОЧЛЕННОМУ payload'у на роль: те же flights/airports, employees —
держатели навыка роли, спрос — места этой роли, пере-индексированные в
indexInFlight=0. Каждое представление байт-в-байт совместимо с одночленным
конвейером (colgen/stage-2 generic mode) — движки не правятся вовсе.

Математика (дизайн-док DESIGN-multirole-independence-RU.md §2): легальность и
стоимость пар роль-агностичны, межролевых ограничений нет — объединённая
модель покрытия блочно-диагональна по ролям, поэтому независимое решение
lane'ов ТОЧНО оптимально для неё.

Fail-closed (инкремент 1): неоднородная сигнатура, повторная роль в сигнатуре,
пересекающиеся пулы ролей и пустой пул роли — громкий отказ.
"""
from __future__ import annotations

import copy
import re

from _leg_identity import seat_signature


class MultiroleUnsupported(RuntimeError):
    """Payload не поддерживается multirole-инкрементом 1; несёт причины."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons) or "unsupported payload")


def role_slug(role: str) -> str:
    """Роль -> [a-z0-9-]+ для lane-метки ('FirstOfficer' -> 'firstofficer')."""
    s = re.sub(r"[^a-z0-9]+", "-", str(role).lower()).strip("-")
    if not s:
        raise MultiroleUnsupported(
            [f"роль '{role}' не даёт допустимой метки lane"])
    return s


def overlapping_ids(payload: dict) -> set:
    """Сотрудники, входящие в пулы БОЛЕЕ одной роли сигнатуры (двойные
    квалификации). Пустое множество = пулы дизъюнктны."""
    sig = seat_signature(payload) or ()
    seen: dict = {}
    dup: set = set()
    for e in payload.get("employees", []):
        if not isinstance(e, dict) or not e.get("id"):
            continue
        skills = e.get("skills") or []
        n = sum(1 for r in set(sig) if r in skills)
        if n > 1:
            dup.add(e["id"])
        seen[e["id"]] = n
    return dup


def role_views(payload: dict, allow_overlap: bool = False) -> dict:
    """role -> одночленный payload-представление. Громкий отказ, если модель
    неприменима (см. докстринг модуля). allow_overlap=True (инкремент 3,
    совместный stage-2): пересекающиеся пулы НЕ отвергаются — двойная
    квалификация попадает в оба представления, а межролевой double-booking
    исключает СОВМЕСТНЫЙ солвер и контролирует слитая физика."""
    reasons: list = []
    sig = seat_signature(payload)
    if not sig:
        reasons.append("нет однородной сигнатуры мест — multirole требует "
                       "одинаковый состав экипажа на всех участках")
        raise MultiroleUnsupported(reasons)
    if len(sig) < 2:
        reasons.append("сигнатура одночленная — multirole не нужен, "
                       "используйте обычный конвейер")
    if len(set(sig)) != len(sig):
        reasons.append("повторная роль в сигнатуре (например, два места "
                       "Captain) — инкремент 1 поддерживает только "
                       "различные роли")
    # Ревью (2026-07-19): role_slug лоссиевый ('Captain'/'CAPTAIN' -> один
    # слаг) — различные строки ролей, дающие одну lane-метку, перезаписали бы
    # артефакты друг друга; отказ громкий и ДО расходования бюджета.
    try:
        slugs = [role_slug(r) for r in sig]
    except MultiroleUnsupported as exc:
        reasons.extend(exc.reasons)
        slugs = []
    if slugs and len(set(slugs)) != len(slugs):
        reasons.append("роли сигнатуры дают одинаковую lane-метку "
                       f"({', '.join(sorted(set(sig)))}) — переименуйте "
                       "роли так, чтобы их слаги различались")
    if reasons:
        raise MultiroleUnsupported(reasons)

    pools = {}
    for role in sig:
        pools[role] = [e for e in payload.get("employees", [])
                       if isinstance(e, dict)
                       and role in (e.get("skills") or [])]
        if not pools[role]:
            reasons.append(f"пул роли '{role}' пуст — некому летать")
        no_id = [e for e in pools[role] if not e.get("id")]
        if no_id:
            reasons.append(f"в пуле роли '{role}' {len(no_id)} чел. без id — "
                           f"дизъюнктность пулов непроверяема")
    if reasons:
        raise MultiroleUnsupported(reasons)
    if not allow_overlap:
        roles = list(sig)
        for i, r1 in enumerate(roles):
            ids1 = {e.get("id") for e in pools.get(r1, [])}
            for r2 in roles[i + 1:]:
                ids2 = {e.get("id") for e in pools.get(r2, [])}
                both = sorted(ids1 & ids2)
                if both:
                    reasons.append(
                        f"пулы ролей '{r1}' и '{r2}' пересекаются "
                        f"({', '.join(both[:5])}"
                        f"{'...' if len(both) > 5 else ''}) — для двойных "
                        f"квалификаций используйте совместный режим "
                        f"(allow_overlap)")
    if reasons:
        raise MultiroleUnsupported(reasons)

    views = {}
    for role in sig:
        v = {
            "airports": copy.deepcopy(payload.get("airports") or []),
            "employees": copy.deepcopy(pools[role]),
            "flights": copy.deepcopy(payload.get("flights") or []),
            "flightAssignments": [],
        }
        for a in payload.get("flightAssignments") or []:
            if not isinstance(a, dict):
                continue
            if a.get("requiredSkill") != role:
                continue
            b = copy.deepcopy(a)
            b["indexInFlight"] = 0        # одночленное представление
            b["employee"] = None          # lane решает с нуля
            v["flightAssignments"].append(b)
        views[role] = v
    return views


def canonical_ordinal(payload: dict) -> dict:
    """role -> канонический ordinal сигнатуры исходного payload'а (для
    восстановления indexInFlight при слиянии lane-ростеров)."""
    sig = seat_signature(payload) or ()
    return {role: i for i, role in enumerate(sig)}
