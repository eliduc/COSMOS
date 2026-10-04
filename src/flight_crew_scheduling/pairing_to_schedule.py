"""Stage 2 prep: convert (selected_pairings, employees) into a Timefold-
ready FlightCrewSchedule using the *synthetic-flight* technique.

We create one synthetic Flight per Pairing, with:
  * flight_number = pairing.id
  * departure/arrival = pairing.start_time / end_time (real wall-clock span)
  * departure_airport / arrival_airport = pairing's first/last airports
  * aircraft_type = pairing.aircraft_type

Each synthetic flight gets the full crew composition (captain, first
officer, cabin crew) just like a real Flight in the original schedule.

Then we feed this schedule to the existing Timefold model. Constraints
(qualifications, language, unavailability, weekly/monthly hours,
positioning, etc.) operate on synthetic flights as if they were real
flights. The crew → pairing assignment is recovered from the resulting
``flight_assignments`` (each FlightAssignment.flight.flight_number
corresponds to a Pairing.id).

**Simplifications:**
  * Weekly/monthly flight-hour constraints (H8/H9) sum the synthetic
    flight duration, which is the **duty period** (incl. ground time)
    rather than pure block hours. Production model would track these
    separately.
  * Pairing-internal flight conflicts (H5/H6/H7) are **already resolved**
    by ``pairing_generator`` (we only enumerated legal sequences). When
    the same crew gets two pairings, the cross-pairing rest is checked
    naturally via H5/H6 on the synthetic flight time-spans.
"""
from __future__ import annotations

from itertools import count
from typing import Dict, List

from .demo_data import CAPTAIN, FIRST_OFFICER, SENIOR_CA, CA
from .domain import (
    Airport, Employee, Flight, FlightAssignment, FlightCrewSchedule,
)
from .pairing import Pairing


def build_rostering_schedule(
    selected_pairings: List[Pairing],
    employees: List[Employee],
    airports: List[Airport],
    aircraft_type_default: str = "A320",
    real_flights: List[Flight] | None = None,
) -> tuple[FlightCrewSchedule, Dict[str, Pairing]]:
    """Build a FlightCrewSchedule whose flights are synthetic (= pairings).

    Returns:
        (schedule, pairing_by_flight_number) — the second element maps
        each synthetic flight back to its source Pairing for output
        translation.
    """
    # Audit fix (2026-07-12): silently defaulting to A->A geography when
    # real_flights is omitted reproduced the old bug for any caller that
    # forgot the argument — refuse loudly instead.
    if not real_flights:
        raise ValueError(
            "build_rostering_schedule requires real_flights: without it "
            "one-way pairings degrade to fictitious A->A geography")
    airport_by_code = {a.code: a for a in airports}
    # Audit fix (2026-07-10): resolve the TRUE arrival airport of the last
    # leg instead of forcing arrival == departure (which turned a one-way
    # A->B single-leg pairing into a bogus A->A synthetic flight and fed
    # wrong geography to downstream constraints). ``real_flights`` enables
    # exact resolution; multi-leg closed pairings are unaffected
    # (their last leg arrives at the base by construction).
    arr_code_by_last_leg: Dict[str, str] = {}
    if real_flights:
        by_fn = {f.flight_number: f for f in real_flights}
        for p in selected_pairings:
            last = by_fn.get(p.flight_numbers[-1])
            if last is not None and last.arrival_airport is not None:
                arr_code_by_last_leg[p.id] = last.arrival_airport.code

    pairing_by_flight_number: Dict[str, Pairing] = {}
    synthetic_flights: List[Flight] = []
    for p in selected_pairings:
        # Departure airport: where the pairing physically starts.
        dep_airport = airport_by_code.get(p.base_airport)
        arr_airport = airport_by_code.get(
            arr_code_by_last_leg.get(p.id, p.base_airport), dep_airport)

        f = Flight(
            flight_number=p.id,
            departure_airport=dep_airport,
            departure_utc_date_time=p.start_time,
            arrival_airport=arr_airport,
            arrival_utc_date_time=p.end_time,
            aircraft_type=p.aircraft_type or aircraft_type_default,
        )
        synthetic_flights.append(f)
        pairing_by_flight_number[p.id] = p

    # Generate flight assignments — same crew composition logic as
    # ``demo_data.generate_flight_assignments`` but adapted for our
    # synthetic flights. Each pairing gets a Captain + F/O + 1 Senior CA
    # + 2 CA = 5 assignments. Long-haul (>=8h block) gets +1 F/O +1 CA.
    flight_assignments: List[FlightAssignment] = []
    id_counter = count(1)
    for f in synthetic_flights:
        # The pairing's flight_minutes is closer to "block hours" than
        # f.get_duration_minutes() (which is duty span). Use it for
        # long-haul detection.
        p = pairing_by_flight_number[f.flight_number]
        is_long_haul = p.flight_minutes >= 8 * 60
        seats: List[str] = [CAPTAIN, FIRST_OFFICER, SENIOR_CA, CA, CA]
        if is_long_haul:
            seats.insert(2, FIRST_OFFICER)
            seats.append(CA)
        for idx, seat in enumerate(seats, start=1):
            flight_assignments.append(FlightAssignment(
                id=str(next(id_counter)),
                flight=f,
                index_in_flight=idx,
                required_skill=seat,
            ))

    schedule = FlightCrewSchedule(
        airports=airports,
        employees=employees,
        flights=synthetic_flights,
        flight_assignments=flight_assignments,
    )
    return schedule, pairing_by_flight_number


def expand_pairing_assignments_to_flight_assignments(
    solved_schedule: FlightCrewSchedule,
    pairing_by_flight_number: Dict[str, Pairing],
    real_flights: List[Flight],
) -> List[FlightAssignment]:
    """Given a Timefold-solved synthetic schedule, expand each crew→pairing
    assignment into individual crew→real-flight assignments.

    For each FlightAssignment in the solved schedule:
      * The "flight" is a synthetic flight (= pairing)
      * The pairing covers some number of real flights (1, 2, 3, ...)
      * Expand: 1 crew→pairing assignment becomes N crew→flight
        assignments (one per real flight in pairing), preserving the
        same crew member.
    """
    real_by_id = {f.flight_number: f for f in real_flights}
    expanded: List[FlightAssignment] = []
    id_counter = count(1)

    if not solved_schedule.flight_assignments:
        return expanded

    for fa in solved_schedule.flight_assignments:
        if fa.flight is None:
            continue
        pairing = pairing_by_flight_number.get(fa.flight.flight_number)
        if pairing is None:
            continue
        for fn in pairing.flight_numbers:
            real_flight = real_by_id.get(fn)
            if real_flight is None:
                continue
            expanded.append(FlightAssignment(
                id=str(next(id_counter)),
                flight=real_flight,
                index_in_flight=fa.index_in_flight,
                required_skill=fa.required_skill,
                employee=fa.employee,
            ))
    return expanded
