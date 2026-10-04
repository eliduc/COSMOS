"""Convert a Meridian crew-rostering export into the canonical payload.

Standalone import adapter (layer 1). Reads the airline crew-management
system "Meridian" dataset export (format:1, type:pairing — one JSON object
with legs / duties / pairings / employees / airports / groundActivities),
optionally consults the matching Solver export (format:5) as a reference
for aircraft types and geo/timezone data, and writes a payload that
validates against ``webapp/schema/canonical_payload.schema.json``.

The adapter is fully deterministic: no model calls, no network, standard
library + jsonschema only — customer data never leaves the machine
(zero inference, not "local inference").

House rules honoured here (cf. _ssim_to_payload.py):
  * loud failures — malformed legs / unresolvable seats are collected and
    FAIL the run (nonzero exit); no silent fixes;
  * the conversion check artifact is always written, even on failure, and
    any sanity violation is listed and exits nonzero;
  * the output payload is validated against the canonical JSON Schema
    before it is considered done — self-invalid output is a hard error;
  * PII minimization: employee names, birth dates, sex, citizenship and
    the squadron field (which embeds full names) are DROPPED — the
    canonical employee is keyed and labelled by the personnel id only.

Source-format facts this adapter relies on (verified against the
customer August-2026 dataset, 2026-07-24):
  * leg.start/end are naive ISO datetimes ALREADY IN UTC — verified
    against the Solver export's zoned datetimes (4401/4401 legs equal).
    When --solver is given the adapter re-verifies this on every shared
    leg and fails loudly on any mismatch (the hypothesis is a guard, not
    an assumption).
  * leg.legId is '<flightNumber>$<dd.mm.yyyy>$<legNumber>' and is unique
    across the file.
  * leg.dutyCrewTypes maps dutyId -> the leg's role inside that duty
    ('Р' = operating flight, 'D/H' = deadhead positioning, plus rare
    'У'/'О'/'W'/'Д' variants). Inverting it links legs to duties; the
    duty's `complements` list (complementRank per seat) is the native
    per-duty crew composition.
  * employee.fleets[] carries {aircraft, rankQualification, from, to} —
    the rank-per-fleet qualification windows.
  * employee.features[] carries {code, activeSince, activeUntil} — dated
    clearances (all records carry activeUntil in the reference dataset).

Seat (flightAssignment) derivation, in priority order per operating leg:
  1. Meridian: duties containing the leg in an operating role (not
     'D/H') -> the duty's complements signature. When several candidate
     duties disagree, the most common signature wins (ties: the duty with
     the smallest dutyId); the leg is counted as "ambiguous".
  2. Solver (with --solver): leg2Duties rows with crewRole in
     {STD, ENF, DBL} -> the duty's employeeAssignments preferredRank
     multiset (same disagreement rule).
  3. Aircraft-type mode: the most common signature among legs of the
     same (canonical) aircraft type resolved by (1)/(2). Disabled by
     --no-fill, in which case unresolved legs fail the run. Every leg
     resolved this way is counted per aircraft type in the check
     artifact — no silent invention.

Usage:
    python _meridian_to_payload.py <meridian.json> [--solver <solver.json>]
        [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--no-fill]
        [--out payload.json] [--check conversion_check.json]

Defaults: --from/--to = the file's planSince/planUntil dates;
--out = <input>_payload.json next to the input;
--check = <input>_conversion_check.json next to the input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "webapp" / "schema" / "canonical_payload.schema.json"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DURATION_MAX = timedelta(hours=20)
DEADHEAD_DUTY_ROLE = "D/H"          # leg flown as a passenger inside a duty
# Materialized deadheads of the Solver export carry this prefix on the leg
# id ('dh%<legId>') — the SAME leg the Meridian file lists without it.
DEADHEAD_PREFIX = "dh%"
QUAL_CODE_MAX = 40                   # canonical qualifications[].code limit

# Deterministic seat ordering inside a flight (indexInFlight): the senior
# cabin ranks first, then business-class, then line attendants; ranks not
# listed here go after, alphabetically.
RANK_ORDER = ["PURS", "INST C-cl", "INST", "INSTR", "CA C-cl", "CA TRN", "CA"]

# Cyrillic homoglyphs -> Latin, applied ONLY to code-like fields that must
# be ASCII on the wire (flight numbers, aircraft types, rank tokens) —
# never to human-language qualification texts. The reference dataset
# carries carrier '7С' (Cyrillic Es) and aircraft 'А330-200' (Cyrillic A).
_HOMOGLYPHS = str.maketrans(
    "АВЕКМНОРСТУХаеорсух",
    "ABEKMHOPCTYXaeopcyx")


def _code(s: str) -> str:
    """Canonicalize a code-like value: homoglyphs -> Latin, collapse
    whitespace runs, strip. 'А330-200' -> 'A330-200', 'A320  ' -> 'A320'."""
    return re.sub(r"\s+", " ", str(s).translate(_HOMOGLYPHS)).strip()


def _text(s: str) -> str:
    """Canonicalize a human-language value: collapse whitespace runs and
    strip ONLY — no homoglyph translation ('Опасные грузы' stays
    Cyrillic; translating it would mangle real Russian words)."""
    return re.sub(r"\s+", " ", str(s)).strip()


class SourceError(ValueError):
    """A malformed entity in the Meridian export (collected, then fatal)."""


# ------------------------------------------------------------- field parsing

_MS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?$")
_ZONED_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?"
    r"([+-]\d{2}):(\d{2})(?:\[[^\]]+\])?$")
_LEGID_RE = re.compile(r"^(.+)\$(\d{2})\.(\d{2})\.(\d{4})\$(\d+)$")


def parse_naive_utc(raw: object, what: str) -> datetime:
    """Meridian naive ISO datetime (optionally with millis) -> datetime.
    Nonzero seconds AND nonzero fractional seconds are refused (the
    downstream pipeline is minute-aligned — silently truncating '.999'
    would shift the instant); calendar-invalid dates that pass the digit
    regex ('2026-13-01') are refused loudly too."""
    m = _MS_RE.match(str(raw or ""))
    if not m:
        raise SourceError(f"{what}: expected naive ISO datetime, got {raw!r}")
    if m.group(2) and int(m.group(2)):
        raise SourceError(f"{what}: nonzero fractional seconds in {raw!r} "
                          f"(pipeline is minute-aligned)")
    try:
        dt = datetime.fromisoformat(m.group(1))
    except ValueError as exc:
        raise SourceError(f"{what}: invalid datetime {raw!r} ({exc})") from exc
    if dt.second != 0:
        raise SourceError(f"{what}: nonzero seconds in {raw!r} "
                          f"(pipeline is minute-aligned)")
    return dt


def parse_zoned_to_utc(raw: object) -> Optional[datetime]:
    """Solver zoned datetime '...+05:00[Asia/Karachi]' -> naive UTC.
    Returns None for anything unparseable, calendar-invalid or carrying a
    nonzero fractional second (truncation could mask or fabricate a UTC
    guard mismatch) — callers count these, they are never silent."""
    m = _ZONED_RE.match(str(raw or ""))
    if not m:
        return None
    if m.group(2) and int(m.group(2)):
        return None
    try:
        dt = datetime.fromisoformat(m.group(1))
    except ValueError:
        return None
    sign = 1 if m.group(3)[0] == "+" else -1
    off = timedelta(hours=abs(int(m.group(3))),
                    minutes=int(m.group(4)))
    return dt - sign * off


def parse_leg_id(leg_id: str) -> Tuple[str, date, int]:
    """'ZZ092$08.09.2026$1' -> ('ZZ092', date(2026,9,8), 1)."""
    m = _LEGID_RE.match(leg_id)
    if not m:
        raise SourceError(f"legId {leg_id!r}: expected "
                          f"'<flight>$<dd.mm.yyyy>$<n>'")
    try:
        d = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
    except ValueError as exc:
        raise SourceError(f"legId {leg_id!r}: invalid date ({exc})") from exc
    return m.group(1), d, int(m.group(5))


def _date_part(raw: object) -> Optional[str]:
    """'2026-10-30T00:00:00.000' -> '2026-10-30'. None when unparseable OR
    calendar-invalid ('2026-13-01' passes the digit form but is not a
    date) — every caller decides loud-vs-counted for None, never crashes."""
    s = str(raw or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        date.fromisoformat(s[:10])
    except ValueError:
        return None
    return s[:10]


# ---------------------------------------------------------------- signatures

def _signature(ranks: List[str]) -> Tuple[Tuple[str, int], ...]:
    """A seat multiset as a canonical, hashable signature."""
    return tuple(sorted(Counter(_code(r) for r in ranks if r).items()))


def _pick_signature(cands: List[Tuple[str, Tuple]],
                    ) -> Tuple[Tuple, bool, str]:
    """Pick one signature among (duty_id, signature) candidates: the most
    common signature wins; ties break on the smallest duty id. Returns
    (signature, ambiguous?, winner_duty_id) — the winner is the smallest
    duty id carrying the winning signature, and it doubles as the leg's
    OWNER unit in the vendor catalog (путь Б): ownership must agree with
    the seat signature the payload records for the leg."""
    freq = Counter(sig for _, sig in cands)
    top = max(freq.values())
    ambiguous = len(freq) > 1
    for did, sig in sorted(cands):
        if freq[sig] == top:
            return sig, ambiguous, did
    return cands[0][1], ambiguous, cands[0][0]   # unreachable; defensive


def _expand_signature(sig: Tuple[Tuple[str, int], ...]) -> List[str]:
    """Signature -> ordered seat list (RANK_ORDER first, rest alphabetical)."""
    order = {r: i for i, r in enumerate(RANK_ORDER)}
    seats: List[str] = []
    for rank, n in sorted(sig, key=lambda kv: (order.get(kv[0], 99), kv[0])):
        seats.extend([rank] * n)
    return seats


# ------------------------------------------------------------------- loading

def _load_json(path: Path, what: str) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceError(f"{what}: cannot read/parse {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise SourceError(f"{what}: expected a JSON object, got "
                          f"{type(data).__name__}")
    return data


def _require_list(src: dict, key: str, what: str) -> list:
    v = src.get(key)
    if not isinstance(v, list):
        raise SourceError(f"{what}: missing/invalid '{key}' "
                          f"(got {type(v).__name__})")
    return v


# --------------------------------------------------------------- qualification kinds

_KIND_RULES = [
    ("licence", re.compile(r"свидетельств", re.IGNORECASE)),
    ("medical", re.compile(r"влэк|медиц|медком", re.IGNORECASE)),
    ("passport", re.compile(r"паспорт", re.IGNORECASE)),
    ("visa", re.compile(r"виза", re.IGNORECASE)),
]


def classify_qualification(code: str, fleet_names: set) -> str:
    """Meridian feature code -> canonical qualification kind.

    Fleet-prefixed codes ('B787-8_Проверка в рейсовых условиях') are
    type-bound recurrent checks -> type_rating. Keyword kinds next.
    Everything else (route clearances, dangerous-goods training, security)
    has no dedicated kind in the canonical enum — they land in 'licence'
    as the generic dated-clearance bucket (a labelled compromise; the
    expiry tracking is what matters downstream, the category label is
    cosmetic). Counts per kind go to the check artifact."""
    for fl in fleet_names:
        if code.startswith(fl + "_") or _code(code).startswith(fl + " _"):
            return "type_rating"
    for kind, rx in _KIND_RULES:
        if rx.search(code):
            return kind
    return "licence"


# ------------------------------------------------------------------- convert

def convert(meridian: dict, solver: Optional[dict], source_name: str,
            check_rel: str, date_from: Optional[date],
            date_to: Optional[date], no_fill: bool,
            catalog_source: str = "auto",
            ) -> Tuple[dict, dict, list, list]:
    """Meridian (+ optional Solver) -> (payload, check_details, errors,
    catalog_units).

    Collects SourceErrors instead of raising where practical; a non-empty
    error list means the run must fail (payload is still built as far as
    possible so the check artifact is informative)."""
    errors: List[str] = []
    check: dict = {"solver_used": solver is not None}

    legs_raw = _require_list(meridian, "legs", "meridian")
    duties_raw = _require_list(meridian, "duties", "meridian")
    employees_raw = _require_list(meridian, "employees", "meridian")
    airports_raw = _require_list(meridian, "airports", "meridian")
    grounds_raw = meridian.get("groundActivities")
    if grounds_raw is None:
        grounds_raw = []
    elif not isinstance(grounds_raw, list):
        # A present-but-broken container must not silently erase ALL
        # crew unavailability (review 2026-07-24, correctness finding 4).
        errors.append(f"meridian: groundActivities must be a list, got "
                      f"{type(grounds_raw).__name__}")
        grounds_raw = []

    # Plan window (default conversion range).
    plan_since = _date_part(meridian.get("planSince"))
    plan_until = _date_part(meridian.get("planUntil"))
    rng_from = date_from or (date.fromisoformat(plan_since)
                             if plan_since else None)
    rng_to = date_to or (date.fromisoformat(plan_until)
                         if plan_until else None)
    if rng_from is None or rng_to is None:
        errors.append("no conversion range: the file has no "
                      "planSince/planUntil and no --from/--to given")
        return {}, check, errors, []
    if rng_from > rng_to:
        errors.append(f"conversion range: from {rng_from} after to {rng_to}")
        return {}, check, errors, []
    check["range_from"] = rng_from.isoformat()
    check["range_to"] = rng_to.isoformat()

    # ---------------- Solver reference maps (aircraft type, UTC guard) ----
    sol_leg: Dict[str, dict] = {}
    sol_seats_by_duty: Dict[str, List[str]] = defaultdict(list)
    sol_duty_roles: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    sol_station: Dict[str, dict] = {}
    # Восстановление пейрингов солвера (2026-07-24, инкремент 3 — по
    # требованию «восстановить все пейринги из солвер-файла приложением»):
    # назначения несут duty + sequence; sequences сворачивают полные
    # идентификаторы в sequenceId (пейринг солвера). Связка детерминирована
    # и строится ТОЛЬКО по явным ссылкам файла — никакого угадывания.
    seq_of_duty: Dict[str, str] = {}
    if solver is not None:
        seq_id_of_full: Dict[str, str] = {}
        for s in solver.get("sequences") or []:
            if (isinstance(s, dict) and isinstance(s.get("id"), str)
                    and isinstance(s.get("sequenceId"), str)):
                seq_id_of_full[s["id"]] = s["sequenceId"]
        votes: Dict[str, Counter] = defaultdict(Counter)
        for ea in solver.get("employeeAssignments") or []:
            if not isinstance(ea, dict):
                continue
            did, full = ea.get("duty"), ea.get("sequence")
            if isinstance(did, str) and isinstance(full, str):
                sid = seq_id_of_full.get(full)
                if sid:
                    votes[did][sid] += 1
        for did, cnt in votes.items():
            top = max(cnt.values())
            seq_of_duty[did] = min(s for s, n in cnt.items() if n == top)
    if solver is not None:
        for l in solver.get("legs") or []:
            if isinstance(l, dict) and isinstance(l.get("id"), str):
                sol_leg[l["id"]] = l
        for ea in solver.get("employeeAssignments") or []:
            if not isinstance(ea, dict):
                continue
            did, rank = ea.get("duty"), ea.get("preferredRank")
            if isinstance(did, str) and isinstance(rank, str):
                sol_seats_by_duty[did].append(rank)
        for row in solver.get("leg2Duties") or []:
            if not isinstance(row, dict):
                continue
            leg_id, did = row.get("leg"), row.get("duty")
            role = row.get("crewRole")
            if isinstance(leg_id, str) and isinstance(did, str):
                # Перегонные строки солвера ссылаются на участок с
                # приставкой 'dh%' — тот же самый участок, что и без неё
                # (проверено на наборе: все 567 таких идентификаторов
                # есть в файле «Меридиана»). Без снятия приставки ключ не
                # совпадает ни с одним участком, и ротациям солвера не
                # привязывается НИ ОДИН позиционный перелёт — юнит теряет
                # привязку к базе. Ср. _solver_to_payload.py.
                plain = (leg_id[len(DEADHEAD_PREFIX):]
                         if leg_id.startswith(DEADHEAD_PREFIX) else leg_id)
                sol_duty_roles[plain].append((did, str(role or "")))
        for st in solver.get("stations") or []:
            if isinstance(st, dict) and isinstance(st.get("iata"), str):
                sol_station[st["iata"]] = st

    # ---------------- duty / pairing maps (нужны до фильтра окна) --------
    duty_by_id: Dict[str, dict] = {}
    pairing_of_duty: Dict[str, str] = {}
    for d in duties_raw:
        if isinstance(d, dict) and isinstance(d.get("dutyId"), str):
            duty_by_id[d["dutyId"]] = d
            pid = d.get("pairingId")
            pairing_of_duty[d["dutyId"]] = (
                pid if isinstance(pid, str) and pid else d["dutyId"])

    # Пейринги, затронутые окном: хоть один операционный участок пейринга
    # вылетает внутри окна. Обрезка окна действует ПО ПЕЙРИНГАМ (2026-07-24,
    # инкремент 1): пейринг, зацепивший август, входит целиком — обрезка по
    # участкам превращала замкнутую ротацию в неназначаемый обрывок.
    touched_pairings: set = set()
    for leg in legs_raw:
        if not isinstance(leg, dict) or leg.get("deadhead"):
            continue
        d0 = _date_part(leg.get("start"))
        if not d0 or not (rng_from <= date.fromisoformat(d0) <= rng_to):
            continue
        dct0 = leg.get("dutyCrewTypes")
        if not isinstance(dct0, dict):
            continue
        for did, role in dct0.items():
            if str(role) != DEADHEAD_DUTY_ROLE and did in pairing_of_duty:
                touched_pairings.add(pairing_of_duty[did])

    # ---------------- legs -> flights ------------------------------------
    # Exclusions are counted, never silent. Operating = deadhead is falsy.
    flights: List[dict] = []
    leg_meta: List[dict] = []            # parallel: seat resolution input
    excluded = Counter()
    boundary_included = 0
    utc_checked = 0
    utc_unparsed = 0
    utc_shared = 0
    utc_mismatches: List[dict] = []
    seen_leg_ids = set()
    for i, leg in enumerate(legs_raw):
        if not isinstance(leg, dict):
            errors.append(f"legs[{i}]: not an object")
            continue
        leg_id = leg.get("legId")
        if not isinstance(leg_id, str) or not leg_id:
            errors.append(f"legs[{i}]: missing legId")
            continue
        if leg_id in seen_leg_ids:
            errors.append(f"legs[{i}]: duplicate legId {leg_id!r}")
            continue
        seen_leg_ids.add(leg_id)
        if leg.get("deadhead"):
            excluded["deadhead"] += 1
            continue
        try:
            dep = parse_naive_utc(leg.get("start"), f"leg {leg_id} start")
            arr = parse_naive_utc(leg.get("end"), f"leg {leg_id} end")
            fn_raw, leg_date, leg_no = parse_leg_id(leg_id)
        except SourceError as exc:
            errors.append(str(exc))
            continue
        if not (rng_from <= dep.date() <= rng_to):
            # Заграничный участок входит, если принадлежит затронутому
            # окном пейрингу (целостность ротации); сироты — исключаются.
            dct0 = leg.get("dutyCrewTypes")
            member = isinstance(dct0, dict) and any(
                str(role) != DEADHEAD_DUTY_ROLE
                and pairing_of_duty.get(did) in touched_pairings
                for did, role in dct0.items())
            if not member:
                excluded["outside_range"] += 1
                continue
            boundary_included += 1
        if not (timedelta(0) < arr - dep <= DURATION_MAX):
            errors.append(f"leg {leg_id}: duration "
                          f"{(arr - dep)} out of (0, {DURATION_MAX}]")
            continue
        # Station codes are code fields — homoglyph-canonicalized like
        # flight numbers: a code whose first letter is a Cyrillic look-alike
        # must not silently fork a second airport next to the real one.
        # (The example is deliberately NOT spelled out: writing a real
        # station code here is the very leak this canonicalization
        # exists to prevent, and a published comment is as public as
        # published data.)
        dep_st = leg.get("departureStation")
        arr_st = leg.get("arrivalStation")
        if not (isinstance(dep_st, str) and dep_st.strip()
                and isinstance(arr_st, str) and arr_st.strip()):
            errors.append(f"leg {leg_id}: missing departure/arrival station")
            continue
        dep_st, arr_st = _code(dep_st), _code(arr_st)
        dct = leg.get("dutyCrewTypes")
        if dct is None:
            dct = {}
        if not isinstance(dct, dict):
            errors.append(f"leg {leg_id}: dutyCrewTypes must be an object, "
                          f"got {type(dct).__name__}")
            continue

        # UTC guard against the Solver reference (never assume, verify).
        # Both endpoints of the leg are compared; solver values that do
        # not parse are counted, never silently skipped.
        sl = sol_leg.get(leg_id)
        if sl is not None:
            utc_shared += 1
            szoned = parse_zoned_to_utc(sl.get("start"))
            ezoned = parse_zoned_to_utc(sl.get("end"))
            if szoned is None:
                utc_unparsed += 1
            else:
                utc_checked += 1
                if szoned != dep:
                    utc_mismatches.append(
                        {"legId": leg_id, "field": "start",
                         "meridian": str(dep), "solver_utc": str(szoned)})
                if ezoned is not None and ezoned != arr:
                    utc_mismatches.append(
                        {"legId": leg_id, "field": "end",
                         "meridian": str(arr), "solver_utc": str(ezoned)})

        fn_ascii = _code(str(leg.get("flightNumber") or fn_raw))
        flight_number = (f"{fn_ascii}-{leg_date.strftime('%Y%m%d')}"
                         f"-{leg_no}")
        dep_s = dep.strftime("%Y-%m-%dT%H:%M:%S")
        flight = {
            "flightNumber": flight_number,
            "flightLegId": f"{flight_number}|{dep_s}",
            "sourceLegId": leg_id,
            "departureAirport": dep_st,
            "departureUTCDateTime": dep_s,
            "arrivalAirport": arr_st,
            "arrivalUTCDateTime": arr.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        ac = sol_leg.get(leg_id, {}).get("acType")
        if isinstance(ac, str) and ac.strip() and ac != "UNKNOWN":
            flight["aircraftType"] = _code(ac)
        flights.append(flight)
        leg_meta.append({"legId": leg_id, "flight": flight,
                         "dutyCrewTypes": dct})
    if utc_mismatches:
        errors.append(f"UTC guard: {len(utc_mismatches)} mismatch(es) where "
                      f"Meridian time != Solver time converted to UTC — "
                      f"the naive-times-are-UTC premise does not hold for "
                      f"this file (see check artifact)")
    if solver is not None and utc_shared > 0 and utc_checked == 0:
        # The guard exists to VERIFY the naive-times-are-UTC premise; if
        # none of the shared legs' solver times parse, it never engaged —
        # passing silently would turn the guard back into an assumption.
        errors.append(f"UTC guard could not engage: none of the "
                      f"{utc_shared} shared leg(s) carry a parseable "
                      f"zoned solver time")
    check["legs_total"] = len(legs_raw)
    check["legs_converted"] = len(flights)
    check["legs_excluded"] = dict(excluded)
    check["boundary_legs_included"] = boundary_included
    # Эффективный диапазон данных: ОБЪЕДИНЕНИЕ планового окна и дат
    # включённых рейсов — хвосты пейрингов его раздвигают (граунд 30 июля
    # обязан блокировать июльский хвост), но данные, начинающиеся позже
    # начала окна, НЕ сужают его (недоступности первых чисел месяца).
    eff_from, eff_to = rng_from, rng_to
    if flights:
        eff_from = min(eff_from,
                       min(date.fromisoformat(f["departureUTCDateTime"][:10])
                           for f in flights))
        eff_to = max(eff_to,
                     max(date.fromisoformat(f["arrivalUTCDateTime"][:10])
                         for f in flights))
    check["effective_span"] = [eff_from.isoformat(), eff_to.isoformat()]
    check["utc_guard_checked"] = utc_checked
    check["utc_guard_unparsed"] = utc_unparsed
    check["utc_guard_mismatches"] = utc_mismatches[:20]

    # ---------------- seats: Meridian duties first ------------------------
    def meridian_signature(duty: dict) -> Optional[Tuple]:
        comps = duty.get("complements")
        if not isinstance(comps, list) or not comps:
            return None
        ranks = [c.get("complementRank") for c in comps
                 if isinstance(c, dict)
                 and isinstance(c.get("complementRank"), str)]
        return _signature(ranks) if ranks else None

    seat_source = Counter()
    ambiguous_legs = 0
    unresolved: List[dict] = []
    resolved_sig: List[Optional[Tuple]] = []
    # Owner unit per leg (путь Б, каталог из нарядов): ("meridian", dutyId)
    # / ("solver", dutyId) / ("mode", legId) — всегда согласован с
    # выбранной сигнатурой мест.
    owners: List[Optional[Tuple[str, str]]] = []
    def _meridian_cands(meta) -> List[Tuple[str, Tuple]]:
        out: List[Tuple[str, Tuple]] = []
        for did, role in sorted(meta["dutyCrewTypes"].items()):
            if str(role) == DEADHEAD_DUTY_ROLE:
                continue
            duty = duty_by_id.get(did)
            sig = meridian_signature(duty) if duty else None
            if sig:
                out.append((did, sig))
        return out

    def _solver_cands(meta) -> List[Tuple[str, Tuple]]:
        out: List[Tuple[str, Tuple]] = []
        if solver is None:
            return out
        for did, role in sorted(sol_duty_roles.get(meta["legId"], [])):
            if role in ("STD", "ENF", "DBL"):
                sig = _signature(sol_seats_by_duty.get(did, []))
                if sig:
                    out.append((did, sig))
        return out

    # Приоритет источника состава/владельца: auto = наряды Меридиана,
    # затем солвер; solver = структура солвера первой («отдельное решение»
    # 2026-07-24 — восстановленные пейринги солвера как основной каталог).
    lanes = ([("solver_duty", _solver_cands),
              ("meridian_duty", _meridian_cands)]
             if catalog_source == "solver" else
             [("meridian_duty", _meridian_cands),
              ("solver_duty", _solver_cands)])
    for meta in leg_meta:
        cands: List[Tuple[str, Tuple]] = []
        source = lanes[0][0]
        for src_name, fn in lanes:
            cands = fn(meta)
            if cands:
                source = src_name
                break
        if cands:
            sig, ambiguous, winner = _pick_signature(cands)
            if ambiguous:
                ambiguous_legs += 1
            seat_source[source] += 1
            resolved_sig.append(sig)
            owners.append(("meridian" if source == "meridian_duty"
                           else "solver", winner))
        else:
            resolved_sig.append(None)
            owners.append(None)

    # Aircraft-type mode fill for the tail (deterministic, fully counted).
    mode_by_actype: Dict[str, Tuple] = {}
    sig_freq: Dict[str, Counter] = defaultdict(Counter)
    for meta, sig in zip(leg_meta, resolved_sig):
        ac = meta["flight"].get("aircraftType")
        if sig and ac:
            sig_freq[ac][sig] += 1
    for ac, freq in sig_freq.items():
        # Deterministic mode: highest count, then smallest signature.
        mode_by_actype[ac] = min(
            (sig for sig, n in freq.items() if n == max(freq.values())))
    fill_by_actype = Counter()
    for idx, (meta, sig) in enumerate(zip(leg_meta, resolved_sig)):
        if sig is not None:
            continue
        ac = meta["flight"].get("aircraftType")
        mode = mode_by_actype.get(ac or "")
        if no_fill or mode is None:
            unresolved.append({"legId": meta["legId"],
                               "aircraftType": ac})
        else:
            resolved_sig[idx] = mode
            owners[idx] = ("mode", meta["legId"])
            fill_by_actype[ac] += 1
            seat_source["actype_mode"] += 1
    if unresolved:
        errors.append(
            f"seats: {len(unresolved)} leg(s) with no derivable crew "
            f"composition"
            + (" (--no-fill given)" if no_fill else
               " (no aircraft type / no observed signature for the type)")
            + " — see check artifact")
    check["seat_source"] = dict(seat_source)
    check["seat_signature_ambiguous_legs"] = ambiguous_legs
    check["seat_fill_by_aircraft_type"] = dict(fill_by_actype)
    check["seat_unresolved"] = unresolved[:20]
    check["seat_unresolved_total"] = len(unresolved)

    assignments: List[dict] = []
    for meta, sig in zip(leg_meta, resolved_sig):
        if sig is None:
            continue
        fl = meta["flight"]
        for i, rank in enumerate(_expand_signature(sig)):
            slot_id = f"{fl['flightLegId']}#{i}"
            assignments.append({
                "id": slot_id,
                "crewSlotId": slot_id,
                "flight": fl["flightNumber"],
                "flightLegId": fl["flightLegId"],
                "indexInFlight": i,
                "requiredSkill": rank,
                "employee": None,
            })

    # ---------------- vendor catalog units (путь Б) ----------------------
    # Ростеринговый юнит = ПЕЙРИНГ вендора (ревью 2026-07-24, P1): солвер и
    # сертификат траектории требуют замкнутый на базу юнит, а наряды — лишь
    # фрагменты ротаций (1630/2351 разомкнуты на реальных данных). Участки
    # группируются по пейрингу наряда-владельца; состав юнита — поролевой
    # МАКСИМУМ по сигнатурам его участков (у 178 пейрингов состав нарядов
    # плавает; расширение ростера обрезает экипаж до спроса участка).
    # Солвер-наряды пейринговой структуры не несут — остаются нарядными
    # группами; мода — одиночками (честный непокрываемый хвост).
    def _unit_key(owner: Tuple[str, str]) -> Tuple[str, str]:
        kind, ident = owner
        if kind == "meridian":
            return ("meridian", pairing_of_duty.get(ident, ident))
        if kind == "solver" and ident in seq_of_duty:
            # Инкремент 3: наряды солвера сворачиваются в ЕГО пейринги
            # (sequenceId) — замкнутые ротации вместо нарядных обрывков.
            return ("sequence", seq_of_duty[ident])
        return owner

    def _sig_max(sigs: List[Tuple]) -> Tuple:
        """Element-wise per-role maximum over signatures."""
        acc: Dict[str, int] = {}
        for sig in sigs:
            for role, n in sig:
                acc[role] = max(acc.get(role, 0), n)
        return tuple(sorted(acc.items()))

    groups: Dict[Tuple[str, str], dict] = {}
    riders: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for meta, sig, owner in zip(leg_meta, resolved_sig, owners):
        if sig is None or owner is None:
            continue
        key = _unit_key(owner)
        g = groups.setdefault(key, {"legs": [], "sigs": []})
        g["legs"].append(meta["flight"])
        g["sigs"].append(sig)
    # Инкремент 2 (2026-07-24): перегонные участки нарядов («D/H» —
    # экипаж летит пассажиром) входят в юнит своего пейринга КАК
    # ПАССАЖИРСКИЕ (в fns, но не в active_fns) — цепочка «рейс—перегон—
    # рейс» остаётся непрерывной, а спрос участка обслуживает его
    # юнит-владелец. Перегон при этом сам — операционный участок другого
    # юнита (или исключённый TRANSFER — тогда пассажиром не едет никто).
    for meta in leg_meta:
        for did, role in sorted((meta.get("dutyCrewTypes") or {}).items()):
            if str(role) == DEADHEAD_DUTY_ROLE and did in pairing_of_duty:
                riders[("meridian", pairing_of_duty[did])].append(
                    meta["flight"])
        # Симметрично для структуры солвера: роли ONLY/FIRST/LAST_DH —
        # экипаж наряда едет этим участком пассажиром.
        for did, role in sorted(sol_duty_roles.get(meta["legId"], [])):
            if role in ("ONLY_DH", "FIRST_DH", "LAST_DH"):
                riders[_unit_key(("solver", did))].append(meta["flight"])
    # Границы смен внутри юнита. Без них уровень свободы 2 («сохраняем
    # смены заказчика, цепочки строим заново») невыполним: построитель
    # не может отличить рабочий день от всей цепочки и тихо считает
    # пейринг одной сменой, то есть подменяет постановку. Данные для
    # границ уже разобраны выше — наряды заданы в источнике явно.
    meta_by_fn = {m["flight"]["flightNumber"]: m for m in leg_meta}

    def owning_duty(key: Tuple[str, str], meta: dict) -> Optional[str]:
        """Наряд, которому участок принадлежит ВНУТРИ этого юнита."""
        kind, ident = key
        if kind == "meridian":
            for did, role in sorted((meta.get("dutyCrewTypes") or {}).items()):
                if str(role) != DEADHEAD_DUTY_ROLE \
                        and pairing_of_duty.get(did) == ident:
                    return did
            return None
        if kind == "sequence":
            # Ротация солвера состоит из НЕСКОЛЬКИХ нарядов; отдать её
            # одной группой — значит выдать многодневную командировку за
            # одну смену и молча выродить уровень свободы 2 (та же мина,
            # что закрыта для нарядов «Меридиана»).
            for did, role in sorted(sol_duty_roles.get(meta["legId"], [])):
                if role in ("STD", "ENF", "DBL") \
                        and seq_of_duty.get(did) == ident:
                    return did
            return None
        return None                # solver: юнит и есть ровно один наряд

    def duty_groups(key: Tuple[str, str], active: List[str]) -> List[List[str]]:
        if key[0] not in ("meridian", "sequence"):
            return [list(active)] if active else []
        by_duty: Dict[str, List[str]] = defaultdict(list)
        for fn in active:
            meta = meta_by_fn.get(fn)
            if meta is None:
                continue
            did = owning_duty(key, meta)
            if did is not None:
                by_duty[did].append(fn)
        order = {fn: i for i, fn in enumerate(active)}
        out = [sorted(v, key=lambda x: order.get(x, 0))
               for v in by_duty.values()]
        out.sort(key=lambda grp: order.get(grp[0], 0))
        # плечи, которым наряд не нашёлся, не теряем — иначе разбивка
        # перестанет покрывать юнит и уровень 2 недосчитается работы
        placed = {fn for grp in out for fn in grp}
        rest = [fn for fn in active if fn not in placed]
        if rest:
            out.append(rest)
            out.sort(key=lambda grp: order.get(grp[0], 0))
        return out

    # Вид юнита и его целость. Выводить это из служебного имени
    # `sourceUnit` нельзя: имя — опознавательная метка, а не контракт, и
    # потребитель, разбирающий его по двоеточию, ломается от любого
    # переименования. Различие принципиальное: цепочка — целая
    # командировка исходной структуры, по ней законно судить замкнутость
    # на базу и размах; достройка (одиночный участок по моде типа судна,
    # голый наряд без ротации) ни начала, ни конца командировки не имеет,
    # и те же правила к ней неприменимы по определению.
    #
    # Целость считается по СЫРЫМ спискам, до фильтра окна: участок
    # командировки может не попасть в юнит, потому что его забрал другой
    # владелец (участок числится сразу в нескольких пейрингах, а владелец
    # у него один) или потому что его отсекло окно конверсии. Урезанная
    # цепочка теряет привязку к базе не по вине заказчика, и судить её
    # замкнутость — значит выдавать дефект стыковки за дефект данных.
    CHAIN_KINDS = ("meridian", "sequence")
    src_trip_legs: Dict[Tuple[str, str], set] = defaultdict(set)
    for leg in legs_raw:
        if not isinstance(leg, dict) or not isinstance(leg.get("legId"), str):
            continue
        dct0 = leg.get("dutyCrewTypes")
        if not isinstance(dct0, dict):
            continue
        for did in dct0:
            pid = pairing_of_duty.get(did)
            if pid is not None:
                src_trip_legs[("meridian", pid)].add(leg["legId"])
    for plain, rows in sol_duty_roles.items():
        for did, _role in rows:
            sid = seq_of_duty.get(did)
            if sid is not None:
                src_trip_legs[("sequence", sid)].add(plain)
    src_by_fn = {m["flight"]["flightNumber"]: m["legId"] for m in leg_meta}

    catalog_units: List[dict] = []
    varying_units = 0
    rider_legs_attached = 0
    for key, g in groups.items():
        active_set = {fl["flightNumber"] for fl in g["legs"]}
        ride = [fl for fl in riders.get(key, [])
                if fl["flightNumber"] not in active_set]
        rider_legs_attached += len(ride)
        legs = sorted(g["legs"] + ride,
                      key=lambda f: (f["departureUTCDateTime"],
                                     f["flightNumber"]))
        seats_sig = _sig_max(g["sigs"])
        if len(set(g["sigs"])) > 1:
            varying_units += 1
        active_ordered = [fl["flightNumber"] for fl in legs
                          if fl["flightNumber"] in active_set]
        fns = [fl["flightNumber"] for fl in legs]
        rec = {
            "fns": fns,
            "active_fns": active_ordered,
            "duty_fns": duty_groups(key, active_ordered),
            "cost": 0,
            "seats": _expand_signature(seats_sig),
            "sourceUnit": f"{key[0]}:{key[1]}",
            "kind": "chain" if key[0] in CHAIN_KINDS else "fill",
        }
        if rec["kind"] == "chain":
            have = {src_by_fn[fn] for fn in fns if fn in src_by_fn}
            rec["sourceLegsMissing"] = len(src_trip_legs.get(key, set())
                                           - have)
        catalog_units.append(rec)
    catalog_units.sort(key=lambda u: (u["fns"][0], u["sourceUnit"]))
    # Замкнутость юнитов на базу — решающее свойство для солвера
    # (homeBase — жёсткое правило) и сертификата якорения: считаем честно.
    fl_by_fn = {f["flightNumber"]: f for f in flights}
    closed_units = sum(
        1 for u in catalog_units
        if u["fns"]
        and fl_by_fn[u["fns"][0]]["departureAirport"]
        == fl_by_fn[u["fns"][-1]]["arrivalAirport"])
    check["catalog_units"] = len(catalog_units)
    check["catalog_units_closed"] = closed_units
    check["catalog_units_varying_composition"] = varying_units
    check["catalog_rider_legs_attached"] = rider_legs_attached
    check["catalog_source"] = catalog_source
    check["catalog_units_by_kind"] = dict(Counter(
        u["sourceUnit"].split(":")[0] for u in catalog_units))
    chain_units = [u for u in catalog_units if u["kind"] == "chain"]
    check["catalog_chains_complete"] = sum(
        1 for u in chain_units if not u["sourceLegsMissing"])
    check["catalog_chains_truncated"] = sum(
        1 for u in chain_units if u["sourceLegsMissing"])
    check["catalog_units_fill"] = len(catalog_units) - len(chain_units)

    # ---------------- employees ------------------------------------------
    # PII minimization: name/birth/sex/citizenship/squadron/mentors/
    # doNotFlyWith are dropped; the canonical name IS the personnel id.
    pii_dropped = ["squadron (embeds full names)", "birth", "sex",
                   "citizenship", "mentors", "doNotFlyWith"]
    grounds_by_emp: Dict[str, List[dict]] = defaultdict(list)
    ground_types = Counter()
    for gi, g in enumerate(grounds_raw):
        if not isinstance(g, dict) or not isinstance(g.get("employeeId"),
                                                     str):
            # A malformed record means an unavailability we cannot place —
            # dropping it silently would leave the employee "free" on
            # their day off (review 2026-07-24, correctness finding 4).
            errors.append(f"groundActivities[{gi}]: not an object with a "
                          f"string employeeId")
            continue
        grounds_by_emp[g["employeeId"]].append(g)
        ground_types[str(g.get("groundType"))] += 1
    check["ground_activity_types"] = dict(ground_types)

    def _overlaps(fr: Optional[str], to: Optional[str]) -> bool:
        """Qualification window [fr, to] overlaps the EFFECTIVE data span
        (conversion range + included pairing tails)."""
        d_fr = date.fromisoformat(fr) if fr else date.min
        d_to = date.fromisoformat(to) if to else date.max
        return d_fr <= eff_to and d_to >= eff_from

    employees: List[dict] = []
    qualifications: List[dict] = []
    quals_by_kind = Counter()
    quals_skipped_expired = 0
    quals_skipped_undated = 0
    quals_code_truncated = 0
    fleet_windows_unreadable = 0
    placeholder_ranks = Counter()
    skills_vocab = Counter()
    ratings_vocab = Counter()
    fleet_names: set = set()
    for e in employees_raw:
        if isinstance(e, dict):
            for fl in e.get("fleets") or []:
                if isinstance(fl, dict) and isinstance(fl.get("aircraft"),
                                                       str):
                    fleet_names.add(_code(fl["aircraft"]))
                    fleet_names.add(str(fl["aircraft"]).strip())

    seen_emp = set()
    for i, e in enumerate(employees_raw):
        if not isinstance(e, dict):
            errors.append(f"employees[{i}]: not an object")
            continue
        emp_id = e.get("employeeId")
        if not isinstance(emp_id, str) or not emp_id:
            errors.append(f"employees[{i}]: missing employeeId")
            continue
        if emp_id in seen_emp:
            errors.append(f"employees[{i}]: duplicate employeeId {emp_id!r}")
            continue
        seen_emp.add(emp_id)
        base = e.get("base")
        if not isinstance(base, str) or not base.strip():
            errors.append(f"employee {emp_id}: missing base")
            continue
        base = _code(base)               # code field — same canon as stations

        skills: set = set()
        ratings: set = set()
        for fl in e.get("fleets") or []:
            if not isinstance(fl, dict):
                continue
            rank = fl.get("rankQualification")
            aircraft = fl.get("aircraft")
            if not (isinstance(rank, str) and rank
                    and isinstance(aircraft, str) and aircraft):
                continue
            # Angle-bracketed values are vendor placeholders ('<нет>' =
            # "no rank") — not skills; counted, never emitted.
            if rank.strip().startswith("<") and rank.strip().endswith(">"):
                placeholder_ranks[rank.strip()] += 1
                continue
            # A window value that is PRESENT but unreadable must not
            # silently become an eternal window — that would grant a
            # qualification on garbage data (review 2026-07-24,
            # correctness finding 3). Absent values stay open-ended.
            fr_raw, to_raw = fl.get("from"), fl.get("to")
            fr, to = _date_part(fr_raw), _date_part(to_raw)
            if (fr_raw is not None and fr is None) \
                    or (to_raw is not None and to is None):
                fleet_windows_unreadable += 1
                continue
            if not _overlaps(fr, to):
                continue
            skills.add(_code(rank))
            ratings.add(_code(aircraft))
        for s in skills:
            skills_vocab[s] += 1
        for r in ratings:
            ratings_vocab[r] += 1

        unavailable: set = set()
        for g in grounds_by_emp.get(emp_id, ()):
            d0 = _date_part(g.get("start"))
            d1 = _date_part(g.get("end"))
            if not d0 or not d1:
                errors.append(f"employee {emp_id}: ground activity with "
                              f"unparseable dates {g.get('start')!r}.."
                              f"{g.get('end')!r}")
                continue
            start_d, end_d = date.fromisoformat(d0), date.fromisoformat(d1)
            if end_d < start_d:
                # A reversed interval is corrupted unavailability — losing
                # it silently would leave the employee "free" (review
                # 2026-07-24, tests finding on reversed intervals).
                errors.append(f"employee {emp_id}: ground activity ends "
                              f"before it starts ({d0}..{d1})")
                continue
            # An activity ending exactly at midnight occupies none of the
            # end day — do not block that day.
            if (end_d > start_d
                    and str(g.get("end", ""))[11:19] in ("00:00:00", "")):
                end_d -= timedelta(days=1)
            day = max(start_d, eff_from)
            stop = min(end_d, eff_to)
            while day <= stop:
                unavailable.add(day.isoformat())
                day += timedelta(days=1)

        emp_rec = {
            "id": emp_id,
            "name": emp_id,                      # PII minimization
            "homeAirport": base,
            "skills": sorted(skills),
            "typeRatings": sorted(ratings),
            "unavailableDays": sorted(unavailable),
        }
        # ЛИЧНЫЕ месячные пределы сотрудника (разбор 2026-07-27). Файл
        # «Меридиана» их несёт, адаптер формата решателя их переносит, а
        # этот — терял: у всех выходил ноль, то есть «личного предела
        # нет», и решатель судил по общему.
        #
        # Разница не косметическая. По правилам заказчика месячная норма
        # налёта различается по категориям состава, и у большинства
        # сотрудников личный предел выше общего предела конфигурации.
        # Потеря личного лимита ужесточала задачу против нормы и
        # оставляла места незакрытыми там, где закон это разрешает.
        for src_key, dst_key in (("flightTimeLimit", "flightTimeLimitMin"),
                                 ("workTimeLimit", "workTimeLimitMin")):
            try:
                val = int(e.get(src_key) or 0)
            except (TypeError, ValueError):
                val = 0
            if val > 0:
                emp_rec[dst_key] = val
        employees.append(emp_rec)

        for f in e.get("features") or []:
            if not isinstance(f, dict):
                continue
            code_raw = f.get("code")
            if not (isinstance(code_raw, str) and code_raw):
                continue
            valid_to = _date_part(f.get("activeUntil"))
            if not valid_to:
                # Undated (or unreadably dated) clearances cannot become
                # canonical qualifications (validTo is required by the
                # schema) — counted, never silent (review 2026-07-24).
                quals_skipped_undated += 1
                continue
            if date.fromisoformat(valid_to) < rng_from:
                quals_skipped_expired += 1
                continue
            code = _text(code_raw)
            if len(code) > QUAL_CODE_MAX:
                code = code[:QUAL_CODE_MAX]
                quals_code_truncated += 1
            kind = classify_qualification(code_raw, fleet_names)
            qual = {"employee": emp_id, "kind": kind, "code": code,
                    "validTo": valid_to}
            valid_from = _date_part(f.get("activeSince"))
            if valid_from:
                qual["validFrom"] = valid_from
            qualifications.append(qual)
            quals_by_kind[kind] += 1

    check["employees_total"] = len(employees)
    check["placeholder_ranks_skipped"] = dict(placeholder_ranks)
    check["skills_vocabulary"] = dict(skills_vocab.most_common())
    check["type_ratings_vocabulary"] = dict(ratings_vocab.most_common())
    check["qualifications_by_kind"] = dict(quals_by_kind)
    check["qualifications_skipped_expired"] = quals_skipped_expired
    check["qualifications_skipped_undated"] = quals_skipped_undated
    check["qualifications_code_truncated"] = quals_code_truncated
    check["fleet_windows_unreadable"] = fleet_windows_unreadable
    check["pii_dropped_fields"] = pii_dropped

    # ---------------- airports -------------------------------------------
    # Referenced subset only: flight stations + employee bases. Geo comes
    # from the Solver station reference (decimal degrees, zeroes = absent).
    referenced = ({f["departureAirport"] for f in flights}
                  | {f["arrivalAirport"] for f in flights}
                  | {e["homeAirport"] for e in employees})
    ap_name: Dict[str, str] = {}
    for a in airports_raw:
        if isinstance(a, dict) and isinstance(a.get("IATA"), str):
            nm = a.get("name") or a.get("city")
            if isinstance(nm, str) and nm.strip():
                # Keyed by the CANONICAL code so a homoglyph in the
                # directory still joins with the (canonicalized) stations.
                ap_name.setdefault(_code(a["IATA"]), _text(nm))
    airports: List[dict] = []
    geo_filled = 0
    for code in sorted(referenced):
        ap: dict = {"code": code, "name": ap_name.get(code, code)}
        st = sol_station.get(code)
        if st is not None:
            lat, lon = st.get("latitude"), st.get("longitude")
            if (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                    and not isinstance(lat, bool) and not isinstance(lon, bool)
                    and (lat or lon)):
                ap["latitude"] = float(lat)
                ap["longitude"] = float(lon)
                geo_filled += 1
        airports.append(ap)
    check["airports_referenced"] = len(airports)
    check["airports_with_geo"] = geo_filled

    # ---------------- payload --------------------------------------------
    payload = {
        "formatVersion": "2",
        "provenance": {
            "adapter": "meridian",
            "source": source_name,
            "convertedAt": datetime.now(timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "conversionCheck": check_rel,
        },
        "airports": airports,
        "employees": employees,
        "flights": flights,
        "flightAssignments": assignments,
    }
    if qualifications:
        payload["qualifications"] = qualifications
    return payload, check, errors, catalog_units


# ------------------------------------------------------------------ sanity

def run_sanity_checks(payload: dict) -> dict:
    """Uniqueness of the three identities + referential closure. The
    canonical schema cannot express these; validate_payload re-checks them
    server-side, but the adapter must not ship a payload it knows is bad."""
    flights = payload.get("flights") or []
    slots = payload.get("flightAssignments") or []
    airports = {a["code"] for a in payload.get("airports") or []}

    dup_fn = [fn for fn, n in
              Counter(f["flightNumber"] for f in flights).items() if n > 1]
    dup_leg = [lid for lid, n in
               Counter(f["flightLegId"] for f in flights).items() if n > 1]
    dup_slot = [sid for sid, n in
                Counter(s["crewSlotId"] for s in slots).items() if n > 1]
    bad_airport = sorted(
        {f["departureAirport"] for f in flights if
         f["departureAirport"] not in airports}
        | {f["arrivalAirport"] for f in flights if
           f["arrivalAirport"] not in airports}
        | {e["homeAirport"] for e in payload.get("employees") or []
           if e["homeAirport"] not in airports})
    non_ascii = sorted({f["flightNumber"] for f in flights
                        if not f["flightNumber"].isascii()})
    non_ascii_ap = sorted(a for a in airports if not a.isascii())
    return {
        "duplicate_flight_numbers": dup_fn[:10],
        "duplicate_leg_ids": dup_leg[:10],
        "duplicate_slot_ids": dup_slot[:10],
        "unknown_airports": bad_airport[:10],
        "non_ascii_flight_numbers": non_ascii[:10],
        "non_ascii_airport_codes": non_ascii_ap[:10],
    }


# --------------------------------------------------------------------- CLI

def _parse_iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {raw!r}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Meridian crew-rostering export -> canonical payload")
    parser.add_argument("meridian_file", type=Path)
    parser.add_argument("--solver", type=Path, default=None,
                        help="matching Solver export (format:5) — reference "
                             "for aircraft types, geo and the UTC guard")
    parser.add_argument("--from", dest="date_from", type=_parse_iso_date,
                        help="conversion range start "
                             "(default: the file's planSince)")
    parser.add_argument("--to", dest="date_to", type=_parse_iso_date,
                        help="conversion range end "
                             "(default: the file's planUntil)")
    parser.add_argument("--no-fill", action="store_true",
                        help="disable the aircraft-type mode fill for legs "
                             "with no derivable crew composition (they "
                             "fail the run instead)")
    parser.add_argument("--out", type=Path,
                        help="payload output path "
                             "(default: <input>_payload.json)")
    parser.add_argument("--check", type=Path,
                        help="conversion check artifact path "
                             "(default: <input>_conversion_check.json)")
    parser.add_argument("--emit-catalog", type=Path, default=None,
                        help="also write a stage-2 pairing catalog built "
                             "from the vendor duty structure (путь Б): "
                             "each unit carries its own ordered seat list "
                             "('seats'), units partition the converted "
                             "flights exactly; each unit is marked "
                             "'kind' = chain|fill, and every chain carries "
                             "'sourceLegsMissing' — how many legs of its "
                             "source trip are absent from the unit")
    parser.add_argument("--catalog-label", default=None,
                        help="label recorded inside the emitted catalog "
                             "(default: the --out file stem)")
    parser.add_argument("--catalog-source", default="auto",
                        choices=("auto", "solver"),
                        help="источник состава/структуры юнитов: auto — "
                             "наряды Меридиана, затем солвер; solver — "
                             "восстановленные пейринги солвера (sequences) "
                             "первыми")
    args = parser.parse_args(argv)

    src: Path = args.meridian_file
    if not src.is_file():
        print(f"ERROR: Meridian file not found: {src}", flush=True)
        return 2
    if args.solver is not None and not args.solver.is_file():
        print(f"ERROR: Solver file not found: {args.solver}", flush=True)
        return 2
    out_path: Path = args.out or src.with_name(src.stem + "_payload.json")
    check_path: Path = args.check or src.with_name(
        src.stem + "_conversion_check.json")

    print("=== Meridian -> canonical payload ===", flush=True)
    print(f"  input:  {src}", flush=True)
    if args.solver:
        print(f"  solver: {args.solver}", flush=True)

    try:
        check_rel = str(check_path.resolve().relative_to(HERE)) \
            .replace("\\", "/")
    except ValueError:
        check_rel = str(check_path)

    def _write_check(check: dict, errors: list, sanity: Optional[dict],
                     ok: bool) -> None:
        body = {
            "source": src.name,
            "generatedAt": datetime.now(timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
            **check,
            "errors": errors[:50],
            "errors_total": len(errors),
            **(sanity or {}),
            "ok": ok,
        }
        check_path.parent.mkdir(parents=True, exist_ok=True)
        with check_path.open("w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        print(f"  check:  {check_path}", flush=True)

    try:
        meridian = _load_json(src, "meridian")
        solver = _load_json(args.solver, "solver") if args.solver else None
        payload, check, errors, catalog_units = convert(
            meridian, solver, src.name, check_rel,
            args.date_from, args.date_to, args.no_fill,
            catalog_source=args.catalog_source)
    except SourceError as exc:
        _write_check({}, [str(exc)], None, False)
        print(f"  ERROR: {exc}", flush=True)
        return 1
    except Exception as exc:            # noqa: BLE001 — safety net: the
        # check artifact must exist EVEN on an unexpected internal defect
        # (house rule "always written"); the traceback still reaches
        # stderr for diagnostics, and the exit stays nonzero.
        import traceback
        traceback.print_exc()
        _write_check({}, [f"internal error: "
                          f"{exc.__class__.__name__}: {exc}"], None, False)
        print(f"  ERROR: internal error: {exc}", flush=True)
        return 1

    if errors:
        _write_check(check, errors, None, False)
        print(f"  ERROR: {len(errors)} problem(s) — failing loudly, "
              f"no payload written:", flush=True)
        for e in errors[:20]:
            print(f"    {e}", flush=True)
        return 1

    if not payload.get("flights"):
        _write_check(check, ["no operating legs in the conversion range"],
                     None, False)
        print("  ERROR: no operating legs in the conversion range",
              flush=True)
        return 3

    sanity = run_sanity_checks(payload)
    sanity_bad = any(v for v in sanity.values())
    if sanity_bad:
        _write_check(check, [], sanity, False)
        print("  ERROR: sanity violations (see check artifact):",
              flush=True)
        for k, v in sanity.items():
            if v:
                print(f"    {k}: {v}", flush=True)
        return 3

    # Self-validation against the canonical JSON Schema — loud failure.
    import jsonschema
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        schema = json.load(f)
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        _write_check(check, [f"self-validation: {exc.message}"], sanity,
                     False)
        print(f"  ERROR: adapter output does not validate against "
              f"{SCHEMA_PATH.name}: {exc.message}", flush=True)
        return 4

    if args.emit_catalog is not None:
        # Partition sanity: every converted flight is ACTIVE in EXACTLY one
        # unit (fns may additionally carry it as a rider elsewhere), and
        # every fns entry references a converted flight. A violation means
        # the emitter itself is broken (loud, exit 3).
        active_fns_all = [fn for u in catalog_units
                          for fn in u["active_fns"]]
        flight_fns = {f["flightNumber"] for f in payload["flights"]}
        all_fns_known = all(fn in flight_fns
                            for u in catalog_units for fn in u["fns"])
        if len(active_fns_all) != len(set(active_fns_all)) \
                or set(active_fns_all) != flight_fns \
                or not all_fns_known:
            _write_check(check, ["catalog: active units do not partition "
                                 "the converted flights"], sanity, False)
            print("  ERROR: catalog units do not partition the converted "
                  "flights", flush=True)
            return 3
        label = args.catalog_label or out_path.stem
        catalog_doc = {
            "inst": None,
            "label": label,
            "profile": "meridian-duties",
            "run_id": datetime.now(timezone.utc)
                              .strftime("meridian-%Y%m%dT%H%M%SZ"),
            "ip_status": "VENDOR_CATALOG",
            "ip_objective": None,
            "source": src.name,
            "selected": catalog_units,
        }
        args.emit_catalog.parent.mkdir(parents=True, exist_ok=True)
        with args.emit_catalog.open("w", encoding="utf-8") as f:
            json.dump(catalog_doc, f, ensure_ascii=False, indent=1)
        print(f"  catalog: {args.emit_catalog} "
              f"({len(catalog_units)} units)", flush=True)

    _write_check(check, [], sanity, True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"  payload: {out_path}", flush=True)
    print(f"  {len(payload['flights'])} flights, "
          f"{len(payload['employees'])} employees, "
          f"{len(payload['flightAssignments'])} crew slots, "
          f"{len(payload['airports'])} airports, "
          f"{len(payload.get('qualifications') or [])} qualifications",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
