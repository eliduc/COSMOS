from ._timefold_compat import (
    SolverStatus,
    planning_entity, planning_solution, PlanningId, PlanningVariable,
    PlanningEntityCollectionProperty, ProblemFactCollectionProperty,
    ValueRangeProvider, PlanningScore,
    HardSoftScore,
)
from typing import Dict, List, Any, Annotated, Tuple, Optional
from .json_serialization import *
from datetime import date, timedelta, datetime


class Airport(JsonDomainBase):
    code: Annotated[str, PlanningId]
    name: str
    latitude: Annotated[float, Field(default=0.0)]
    longitude: Annotated[float, Field(default=0.0)]
    taxi_time_in_minutes: Annotated[Dict[str, int] | None,
                                   TaxiTimeValidator,
                                   Field(default_factory=dict)]
    languages: Annotated[List[str], Field(default_factory=list)]


    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return (f"Airport(code={self.code}, name={self.name}, latitude={self.latitude}, longitude={self.longitude},"
                f" taxi_time_in_minutes={self.taxi_time_in_minutes})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Airport):
            return False
        return self.code == other.code

    def __hash__(self) -> int:
        return hash(self.code)

    def __lt__(self, other: "Airport") -> bool:
        return self.code < other.code


class Employee(JsonDomainBase):
    id: Annotated[str, PlanningId]
    name: str
    home_airport: Annotated[Airport | None, IdSerializer, AirportDeserializer, Field(default=None)]
    skills: Annotated[List[str], Field(default_factory=list)]
    languages: Annotated[List[str], Field(default_factory=list)]
    unavailable_days: Annotated[List[date], Field(default_factory=list)]
    # --- realistic extensions --------------------------------------------
    type_ratings: Annotated[List[str], Field(default_factory=list)]
    seniority: Annotated[int, Field(default=500)]
    is_reserve: Annotated[bool, Field(default=False)]
    preferred_days_off: Annotated[List[date], Field(default_factory=list)]
    undesired_airports: Annotated[List[str], Field(default_factory=list)]


    def has_skill(self, skill: str) -> bool:
        """Checks if the employee has a specific skill."""
        return skill in self.skills

    def is_qualified_for(self, aircraft_type) -> bool:
        """True when the employee carries the given aircraft type rating."""
        if aircraft_type is None:
            return True
        ac = str(aircraft_type)
        if len(ac) == 0:
            return True
        own = self.type_ratings if self.type_ratings is not None else []
        for rating in own:
            if str(rating) == ac:
                return True
        return False

    def seniority_weight(self) -> int:
        """Convert raw seniority (lower=senior) into a multiplier 1..10."""
        raw = self.seniority if self.seniority is not None else 500
        # seniority 1 -> 10x; seniority 1000 -> 1x; linear
        clamped = max(1, min(1000, int(raw)))
        return max(1, 11 - (clamped - 1) // 100)

    def speaks_any(self, languages: List[str]) -> bool:
        """Checks if the employee speaks any of the given languages."""
        if languages is None or len(languages) == 0:
            return True
        own = self.languages if self.languages is not None else []
        for lang in languages:
            if lang in own:
                return True
        return False

    def is_available(self, from_date_inclusive: date, to_date_inclusive: date) -> bool:
        """Checks if the employee is available between two dates."""
        if len(self.unavailable_days) == 0:
            return True
        current_date = from_date_inclusive
        while current_date <= to_date_inclusive:
            if current_date in self.unavailable_days:
                return False
            current_date += timedelta(days=1)
        return True

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return (f"Employee(id={self.id}, name={self.name}, home_airport={self.home_airport}, skills={self.skills},"
                f" unavailable_days={self.unavailable_days})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Employee):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


class Flight(JsonDomainBase):
    flight_number: Annotated[str, PlanningId]
    departure_airport: Annotated[Airport | None, IdSerializer, AirportDeserializer, Field(default=None)]
    departure_utc_date_time: Annotated[datetime | None, Field(default=None, alias="departureUTCDateTime")]
    arrival_airport: Annotated[Airport | None, IdSerializer, AirportDeserializer, Field(default=None)]
    arrival_utc_date_time: Annotated[datetime | None, Field(default=None, alias="arrivalUTCDateTime")]
    aircraft_type: Annotated[str, Field(default="A320")]


    def is_long_haul(self) -> bool:
        """Flights >= 8 hours trigger augmented crew."""
        return self.get_duration_minutes() >= 8 * 60

    def is_red_eye(self) -> bool:
        """Departures between 00:00 and 05:59 UTC."""
        if self.departure_utc_date_time is None:
            return False
        return 0 <= self.departure_utc_date_time.hour < 6

    def is_weekend_flight(self) -> bool:
        if self.departure_utc_date_time is None:
            return False
        return self.departure_utc_date_time.weekday() >= 5

    def get_departure_utc_date(self) -> date:
        """Retrieve flight's departure date."""
        return self.departure_utc_date_time.date()

    def get_duration_minutes(self) -> int:
        """Flight duration in minutes."""
        return int((self.arrival_utc_date_time - self.departure_utc_date_time).total_seconds() / 60)

    def get_required_languages(self) -> List[str]:
        """Union of languages spoken at departure and arrival airports."""
        langs: List[str] = []
        if self.departure_airport is not None:
            dep_langs = self.departure_airport.languages
            if dep_langs is not None:
                for lang in dep_langs:
                    langs.append(lang)
        if self.arrival_airport is not None:
            arr_langs = self.arrival_airport.languages
            if arr_langs is not None:
                for lang in arr_langs:
                    if lang not in langs:
                        langs.append(lang)
        return langs

    def get_year_month(self) -> Tuple[int, int]:
        return (self.departure_utc_date_time.year, self.departure_utc_date_time.month)

    def __str__(self) -> str:
        return f"{self.flight_number}@{self.get_departure_utc_date()}"

    def __repr__(self) -> str:
        return (f"Flight(flight_number={self.flight_number}, departure_airport={self.departure_airport}, departure_utc_date_time={self.departure_utc_date_time},"
                f" arrival_airport={self.arrival_airport}, arrival_utc_date_time{self.arrival_utc_date_time})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Flight):
            return False
        return self.flight_number == other.flight_number

    def __hash__(self) -> int:
        return hash(self.flight_number)

    def _comparison_key(self) -> Tuple[Optional[datetime], Optional[Airport], Optional[datetime], Optional[Airport], str]:
        return (self.departure_utc_date_time, self.departure_airport, self.arrival_utc_date_time, self.arrival_airport, self.flight_number)

    def __lt__(self, other: "Flight") -> bool:
        return self._comparison_key() < other._comparison_key()


@planning_entity
class FlightAssignment(JsonDomainBase):
    id: Annotated[str, PlanningId]
    flight: Annotated[Flight | None, IdSerializer, FlightDeserializer, Field(default=None)]
    index_in_flight: int
    required_skill: str
    employee: Annotated[Employee | None, PlanningVariable, IdSerializer, EmployeeDeserializer, Field(default=None)]


    def has_required_skills(self) -> bool:
        """Checks if the employee has a specific skill."""
        return self.employee is not None and self.employee.has_skill(self.required_skill)

    def is_unavailable_employee(self) -> bool:
        """Checks if the employee is unavailable."""
        return self.employee is not None and not self.employee.is_available(
            self.flight.get_departure_utc_date(), self.flight.arrival_utc_date_time.date()
        )

    def get_departure_utc_date_time(self) -> datetime:
        """Retrieve flight's departure date time."""
        return self.flight.departure_utc_date_time

    def __str__(self) -> str:
        return f"{self.flight}-{self.index_in_flight}"

    def __repr__(self) -> str:
        return (f"FlightAssignment(id='{self.id}', flight={self.flight}, index_in_flight={self.index_in_flight},"
                f" required_skill='{self.required_skill}', employee={self.employee})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FlightAssignment):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


@planning_solution
class FlightCrewSchedule(JsonDomainBase):
    airports: Annotated[list[Airport],
                       ProblemFactCollectionProperty]
    employees: Annotated[list[Employee],
                        ProblemFactCollectionProperty,
                        ValueRangeProvider]
    flights: Annotated[list[Flight],
                      ProblemFactCollectionProperty]
    flight_assignments: Annotated[list[FlightAssignment],
                                 PlanningEntityCollectionProperty]
    score: Annotated[HardSoftScore | None,
                    PlanningScore,
                    ScoreSerializer,
                    ScoreValidator,
                    Field(default=None)]
    solver_status: Annotated[SolverStatus | None, Field(default=SolverStatus.NOT_SOLVING)]
