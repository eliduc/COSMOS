"""Flight-leg / crew-slot identity contract — phase 1 (C01/C02, 2026-07-18).

Two identities, both DERIVED on read and PREFERRED when present, so seed data
(GERAD, BTS) is byte-identical and never re-solved:

  * flightLegId — the operating-leg identity. leg_id(x) = x.flightLegId or
    x.flightNumber (or x.flight, tolerating a dict form). Importers may later
    materialize it as f"{flightNumber}|{departureUTCDateTime}" (the exact key
    data_import.semantic_checks and schedule_deltas.find already use).
  * crewSlotId — the seat identity. In phases 1-2 the coverage join key is the
    ordinal slot_key (leg_id, indexInFlight); the explicit field arrives later.

The `pipeline_representable` guard is the fail-closed boundary for the FLAT
single-crew GERAD pipeline: it names payloads the pipeline cannot model
faithfully (C01 multi-seat/multi-role legs; C02 repeated leg identity that
would collapse). It runs ONLY at the solver entry (colgen / stage-2), never in
registry.validate_payload — the shared validator is used by the delta/ops
layer, which legitimately carries repeated flightNumbers across dates.

Design: final-report/DESIGN-flightLegId-crewSlotId-RU.md.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict


class GeradPipelineUnsupported(RuntimeError):
    """The flat single-crew pipeline cannot faithfully represent this payload.
    Carries the human-readable reasons for a fail-closed verdict."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons) or "unsupported payload")


def leg_id(obj):
    """Leg identity of a flight OR an assignment dict. Prefers an explicit
    flightLegId, else flightNumber, else the `flight` ref (dict or string)."""
    if not isinstance(obj, dict):
        return obj
    lid = obj.get("flightLegId")
    if lid:
        return lid
    fn = obj.get("flightNumber")
    if fn is not None:
        return fn
    f = obj.get("flight")
    if isinstance(f, dict):
        return f.get("flightNumber")
    return f


def build_addr_index(payload) -> dict:
    """addr -> flight: адресное пространство операционного слоя (события
    «Сбоя», окрестность ремонта — трек occurrence-идентичности).

    addr(f) = flightNumber, если номер уникален в payload (байт-в-байт
    обратная совместимость всех существующих событий), иначе flightLegId
    (повтор без flightLegId не адресуем). Кросс-коллизия — строка,
    являющаяся одновременно уникальным номером одного рейса и flightLegId
    другого, — громкий отказ: адрес не должен молча уезжать не туда."""
    flights = [f for f in (payload.get("flights") or [])
               if isinstance(f, dict) and f.get("flightNumber")]
    counts = Counter(str(f["flightNumber"]) for f in flights)
    index: dict = {}
    for f in flights:
        fn = str(f["flightNumber"])
        if counts[fn] == 1:
            if fn in index:
                raise GeradPipelineUnsupported(
                    [f"адресная коллизия: {fn!r} одновременно номер рейса "
                     f"и flightLegId другого рейса"])
            index[fn] = f
        lid = f.get("flightLegId")
        if isinstance(lid, str) and lid:
            if lid in index and index[lid] is not f:
                raise GeradPipelineUnsupported(
                    [f"адресная коллизия: {lid!r} одновременно flightLegId "
                     f"и номер/идентичность другого рейса"])
            index[lid] = f
    return index


def addr_of(flight: dict, fn_counts: dict) -> str | None:
    """Канонический адрес рейса: уникальный номер либо flightLegId;
    None, если повторный номер без материализованного flightLegId."""
    fn = str(flight.get("flightNumber"))
    if fn_counts.get(fn, 0) == 1:
        return fn
    lid = flight.get("flightLegId")
    return str(lid) if isinstance(lid, str) and lid else None


def fn_counts(payload) -> dict:
    return dict(Counter(str(f.get("flightNumber"))
                        for f in (payload.get("flights") or [])
                        if isinstance(f, dict) and f.get("flightNumber")))


def slot_key(a):
    """Coverage join key for a crew seat: (leg identity, seat ordinal).
    Reconstructable identically on the demand (payload.flightAssignments) and
    supply (solution.flightAssignments) sides. For single-crew GERAD data this
    is (flightNumber, 0) — one per leg — exactly today's identity."""
    idx = a.get("indexInFlight", 0) if isinstance(a, dict) else 0
    return (leg_id(a), idx)


def _leg_seats(payload) -> dict:
    """leg identity -> list of its crew-seat assignment dicts."""
    seats: dict = defaultdict(list)
    for a in payload.get("flightAssignments") or []:
        if isinstance(a, dict):
            seats[leg_id(a)].append(a)
    return seats


def seat_signature(payload):
    """The canonical crew-seat signature IF every leg requires the SAME
    multiset of skills (the co-located-crew model: one crew per role flies
    the whole pairing) — a sorted tuple of requiredSkill strings, one entry
    per required seat. GERAD -> ('Captain',); Captain+FO -> ('Captain',
    'FirstOfficer'). Returns None when there are no explicit crew seats
    (legacy single-slot pipeline) OR the signature is NON-uniform across
    legs (different legs need different crew complements — the flat pairing
    pipeline cannot faithfully model that)."""
    seats = _leg_seats(payload)
    if not seats:
        return None
    sigs = {tuple(sorted(str(a.get("requiredSkill")) for a in s))
            for s in seats.values()}
    if len(sigs) != 1:
        return None
    return next(iter(sigs))


def crew_roles(payload):
    """Ordered crew-seat roles for the co-located pairing model, one entry
    per required seat as (indexInFlight, requiredSkill), derived from the
    uniform seat signature. GERAD -> [(0, 'Captain')]. None when the payload
    has no explicit crew seats (legacy single-crew) or is non-uniform. The
    ordinal is canonical (0..N-1 over the sorted signature) so the same
    payload always yields the same synthetic slots — coverage counts seats
    per leg (index-agnostic), so a canonical ordinal is sufficient."""
    sig = seat_signature(payload)
    if not sig:
        return None
    return [(i, skill) for i, skill in enumerate(sig)]


def pipeline_representable(payload, allow_heterogeneous_seats: bool = False) -> list:
    """Reasons the pairing pipeline cannot faithfully model this payload
    (empty list == representable). Two classes:
      C02 — repeated leg identity: two flights collapse to one key.
      C01 — NON-uniform crew-seat signature: different legs demand different
        crew complements. Phase 2 solves the UNIFORM multi-seat case
        (co-located Captain+FO flying the same rotation); a heterogeneous
        signature the flat pairing model still cannot represent."""

    reasons: list = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    flights = payload.get("flights") or []
    dup = [lid for lid, c in Counter(leg_id(f) for f in flights
                                     if isinstance(f, dict)).items() if c > 1]
    if dup:
        reasons.append(
            "повторная идентичность рейса — участки сольются "
            "(нужен уникальный flightLegId): " + ", ".join(
                str(x) for x in sorted(map(str, dup))[:10]))
    # Phase 3 -> Пакет B (аудит-2): внутренние структуры конвейера ключуются
    # по flightNumber, но повторный номер теперь РЕШАЕМ через детерминированную
    # биекцию wire-имён на границе солвера (_leg_rekey: F100 -> F100@1/F100@2,
    # восстановление в публикуемых артефактах). Отказ остаётся только когда
    # биекция не строится (слоты без flightLegId, '@' в номере, коллизии) —
    # причины называет rekey_blockers.
    dup_fn = [fn for fn, c in Counter(
        f.get("flightNumber") for f in flights
        if isinstance(f, dict) and f.get("flightNumber")).items() if c > 1]
    dup_fn = [fn for fn in dup_fn if fn not in set(map(str, dup))]
    if dup_fn:
        from _leg_rekey import rekey_blockers
        reasons.extend(rekey_blockers(payload))
    seats = _leg_seats(payload)
    if seats and not allow_heterogeneous_seats:
        sigs = {lid: tuple(sorted(str(a.get("requiredSkill")) for a in s))
                for lid, s in seats.items()}
        if len(set(sigs.values())) > 1:
            from collections import Counter as _C
            top = _C(sigs.values()).most_common(1)[0][0]
            odd = sorted(str(lid) for lid, sg in sigs.items() if sg != top)
            reasons.append(
                "неоднородный состав экипажа по участкам — модель со-локации "
                "(один экипаж на роль на всю пару) требует одинаковых мест на "
                "всех участках; отличаются: " + ", ".join(odd[:10]))
    return reasons


def strict_identity_enabled() -> bool:
    """The fail-closed guard is on by default; CREW_GERAD_STRICT_IDENTITY=0 is
    the emergency kill-switch if a surprise payload appears in prod."""
    return os.environ.get("CREW_GERAD_STRICT_IDENTITY", "1").strip() != "0"


def assert_representable(payload, where: str,
                         allow_heterogeneous_seats: bool = False) -> None:
    """Raise GeradPipelineUnsupported (unless the kill-switch is off) when the
    payload cannot be faithfully solved by the flat pipeline. Call at the
    SOLVER boundary only.

    allow_heterogeneous_seats=True relaxes ONLY the C01 uniform-signature
    reason — for the stage-2 catalog-composition mode (путь Б, 2026-07-24),
    where each catalog record carries its own seat list and the co-location
    model no longer needs a global signature. C02 (leg-identity collapse)
    stays fatal unconditionally."""
    # Каноническая форма времени проверяется ДО аварийного выключателя
    # идентичности и НЕ им управляется (внешний аудит R9, P0-03): одна
    # переменная не должна гасить два разных предмета. Это общий вход
    # расчётного слоя — четыре точки входа конвейера, — и именно он
    # закрывает наборы, записанные МИМО приложения: восемь из девяти
    # базовых наборов реестра пишут корневые скрипты, а не веб-приложение,
    # поэтому одной границы записи для инварианта недостаточно.
    if isinstance(payload, dict):
        from _canonical_time import non_canonical_marks
        marks = non_canonical_marks(payload)
        if marks:
            raise GeradPipelineUnsupported(
                [f"[{where}] набор не канонизован по времени "
                 f"({len(marks)} меток со смещением): "
                 + ", ".join(marks[:5])])
    if not strict_identity_enabled():
        return
    reasons = pipeline_representable(
        payload, allow_heterogeneous_seats=allow_heterogeneous_seats)
    if reasons:
        raise GeradPipelineUnsupported([f"[{where}] {r}" for r in reasons])
