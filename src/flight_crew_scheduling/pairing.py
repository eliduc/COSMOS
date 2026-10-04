"""Pairing domain — sequence of flights that forms a legal duty assignment.

A *Pairing* is the unit of work assigned to a crew member in airline-style
crew scheduling: a chronologically ordered sequence of one or more flights
that:

  * Begin and end at the **same base airport** (back to home)
  * Respect duty-time limits (max 14h between first dep and last arr in
    a single-day pairing)
  * Have legal **rest** between consecutive flights inside the pairing
    (≥ 1h ground time between arrival and next departure at the same hub)

For our PoC we only emit **1-day pairings** — i.e. start and end on the
same calendar day. Multi-day pairings (overnight layover, hotel, return
trip) require additional constraints (rest at outstation, layover cost,
crew per-diem) and are out of scope.

A Pairing is computed from a list of Flight objects; its ``flight_numbers``
attribute is the canonical reference back to the original flights.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, List, Set

from pydantic import Field

from .domain import Flight, JsonDomainBase


class Pairing(JsonDomainBase):
    """Immutable record of a legal multi-flight duty.

    Attributes:
        id            — unique pairing identifier (e.g. "P_001")
        flight_numbers — chronologically ordered list of flight numbers
        base_airport  — IATA code of departure of first AND arrival of
                        last flight (must match)
        start_time    — UTC departure of first flight
        end_time      — UTC arrival of last flight
        flight_minutes — sum of flight durations (excludes ground time)
        duty_minutes  — end_time - start_time (includes ground time)
        aircraft_type — primary aircraft type of the pairing
        required_skills — union of required_skill_set across flights
        languages     — union of languages required at any airport
                        in the pairing
    """
    id: Annotated[str, Field(default="")]
    flight_numbers: Annotated[List[str], Field(default_factory=list)]
    base_airport: Annotated[str, Field(default="")]
    start_time: Annotated[datetime | None, Field(default=None)]
    end_time: Annotated[datetime | None, Field(default=None)]
    flight_minutes: Annotated[int, Field(default=0)]
    duty_minutes: Annotated[int, Field(default=0)]
    aircraft_type: Annotated[str, Field(default="A320")]
    required_skills: Annotated[List[str], Field(default_factory=list)]
    languages: Annotated[List[str], Field(default_factory=list)]

    @classmethod
    def from_flights(cls, pairing_id: str, flights: List[Flight]) -> "Pairing":
        """Build a Pairing from a chronologically-ordered list of Flights.

        Caller is responsible for verifying:
          * Flights ordered by departure time
          * First flight departs from base, last flight arrives at base
          * Legal rest between consecutive flights
          * Total duty within limits
        """
        if not flights:
            raise ValueError("Pairing must contain at least one flight")
        first = flights[0]
        last = flights[-1]
        base = first.departure_airport.code if first.departure_airport else ""
        flight_minutes = sum(f.get_duration_minutes() for f in flights)
        duty_minutes = int((last.arrival_utc_date_time - first.departure_utc_date_time).total_seconds() / 60)
        # Use the most common aircraft type, or the first if all equal
        aircraft_types = [f.aircraft_type for f in flights]
        primary_aircraft = max(set(aircraft_types), key=aircraft_types.count)
        # Union of language requirements
        all_langs: Set[str] = set()
        for f in flights:
            all_langs.update(f.get_required_languages())

        return cls(
            id=pairing_id,
            flight_numbers=[f.flight_number for f in flights],
            base_airport=base,
            start_time=first.departure_utc_date_time,
            end_time=last.arrival_utc_date_time,
            flight_minutes=flight_minutes,
            duty_minutes=duty_minutes,
            aircraft_type=primary_aircraft,
            required_skills=list(set(s for f in flights
                                       if hasattr(f, "required_skill_set") and f.required_skill_set
                                       for s in f.required_skill_set)),
            languages=sorted(all_langs),
        )

    def __repr__(self) -> str:
        return (f"Pairing({self.id}, {len(self.flight_numbers)}f, "
                f"{self.base_airport}, "
                f"flight={self.flight_minutes}m duty={self.duty_minutes}m)")
