from itertools import count
from random import Random
from datetime import datetime, time, timedelta
from typing import List, Callable, TypeVar

from .domain import *

_DEMO_SEED = 0
random = Random(_DEMO_SEED)
T = TypeVar('T')

# ---------------------------------------------------------------- constants

# Crew ranks (skills)
CAPTAIN = "Captain"
FIRST_OFFICER = "First officer"
SENIOR_CA = "Senior cabin attendant"
CA = "Cabin attendant"

PILOT_RANKS = {CAPTAIN, FIRST_OFFICER}
CABIN_RANKS = {SENIOR_CA, CA}
LEGACY_PILOT = "Pilot"              # kept for back-compat in constraints.py
LEGACY_ATTENDANT = "Flight attendant"

# Aircraft types
NARROWBODY = "A320"
WIDEBODY = "A350"
LONG_HAUL_HOURS_THRESHOLD = 8       # flights >= this duration use widebody + augmented crew

FIRST_NAMES = ["Amy", "Beth", "Carl", "Dan", "Elsa", "Flo", "Gus", "Hugo", "Ivy", "Jay",
               "Jeri", "Hope", "Avis", "Lino", "Lyle", "Nick", "Dino", "Otha", "Gwen", "Jose",
               "Dena", "Jana", "Dave", "Russ", "Josh", "Dana", "Katy"]

LAST_NAMES = ["Cole", "Fox", "Green", "Jones", "King", "Li", "Poe", "Rye", "Smith", "Watt", "Howe",
              "Lowe", "Wise", "Clay", "Carr", "Hood", "Long", "Horn", "Haas", "Meza"]


AIRPORT_LANGUAGES = {
    "LHR": ["en"],
    "JFK": ["en"],
    "CNF": ["pt", "en"],
    "BRU": ["fr", "nl", "en"],
    "ATL": ["en"],
    "BNE": ["en"],
}


def generate_demo_data() -> FlightCrewSchedule:
    """Default dataset (compat shim) — matches the original demo (14 flights, 90 emp).
    All 4 engines find a feasible solution on this scale within ~30 seconds."""
    return generate_demo_data_scaled(seed=_DEMO_SEED, count_flights=14, teams_per_airport=2)


def generate_demo_data_scaled(seed: int = _DEMO_SEED,
                              count_flights: int = 56,
                              teams_per_airport: int = 3) -> FlightCrewSchedule:
    """Generate a FlightCrewSchedule of arbitrary scale.

    Args:
        seed: RNG seed; use a different value per benchmark dataset to get
              independent samples while keeping each dataset reproducible.
        count_flights: total flights to schedule. Must be even (the route
              builder generates round-trips). Bigger ⇒ more variables.
        teams_per_airport: how many full crew teams to instantiate per
              departure airport. Each team = 1 Captain + 2 First Officers +
              1 Senior CA + 2 CA, so each team brings 6 active employees plus
              3 reserves. Bigger ⇒ more workforce, easier to hit feasibility.
    """
    # Reseed on every call so the same args yield the same dataset.
    random.seed(seed)

    if count_flights % 2 != 0:
        count_flights += 1  # generate_flights() requires even count

    airports = [
        Airport(code="LHR", name="LHR", latitude=51.4775, longitude=-0.461389,
                languages=AIRPORT_LANGUAGES["LHR"]),
        Airport(code="JFK", name="JFK", latitude=40.639722, longitude=-73.778889,
                languages=AIRPORT_LANGUAGES["JFK"]),
        Airport(code="CNF", name="CNF", latitude=-19.624444, longitude=-43.971944,
                languages=AIRPORT_LANGUAGES["CNF"]),
        Airport(code="BRU", name="BRU", latitude=50.901389, longitude=4.484444,
                languages=AIRPORT_LANGUAGES["BRU"]),
        Airport(code="ATL", name="ATL", latitude=33.636667, longitude=-84.428056,
                languages=AIRPORT_LANGUAGES["ATL"]),
        Airport(code="BNE", name="BNE", latitude=-27.383333, longitude=153.118333,
                languages=AIRPORT_LANGUAGES["BNE"]),
    ]
    distances = {
        "LHR-JFK": 8, "LHR-CNF": 12, "LHR-BRU": 13, "LHR-ATL": 9, "LHR-BNE": 21,
        "JFK-LHR": 8, "JFK-BRU": 14, "JFK-CNF": 10, "JFK-ATL": 6, "JFK-BNE": 20,
        "CNF-LHR": 12, "CNF-JFK": 10, "CNF-BRU": 19, "CNF-ATL": 10, "CNF-BNE": 19,
        "BRU-LHR": 13, "BRU-JFK": 14, "BRU-CNF": 19, "BRU-ATL": 9, "BRU-BNE": 21,
        "ATL-LHR": 9, "ATL-JFK": 6, "ATL-CNF": 10, "ATL-BRU": 9, "ATL-BNE": 18,
        "BNE-LHR": 21, "BNE-JFK": 20, "BNE-CNF": 19, "BNE-BRU": 21, "BNE-ATL": 18,
    }

    first_date = date.today()
    # 7 days guarantees Saturday + Sunday → fair_weekend_distribution sees data.
    count_days = 7
    dates = [first_date + timedelta(days=i) for i in range(count_days)]
    home_airports = random.sample(airports, 2)
    # Full 24-hour grid — covers red-eye (00:00-06:00) for fair_red_eye_distribution.
    times = [time(hour=i, minute=0) for i in range(24)]
    flights = generate_flights(count_flights, datetime.now() + timedelta(minutes=1),
                               airports, home_airports, dates, times, distances)
    flight_assignments = generate_flight_assignments(flights)
    employees = generate_employees(flights, dates, teams_per_airport=teams_per_airport)

    schedule = FlightCrewSchedule(
        airports=airports, employees=employees, flights=flights,
        flight_assignments=flight_assignments, score=None,
        solver_status=SolverStatus.NOT_SOLVING,
    )
    return schedule


# 6 benchmark datasets — keep assignments-per-employee ratio in [1.1 .. 3.3]
# so all 4 engines can actually find feasible solutions inside reasonable
# budgets. The ratio is the dominant predictor of solver effort: ratios
# > 4 push the FTL/duty constraints into infeasibility, ratios < 1 are
# trivial.
BENCHMARK_DATASETS = [
    {"label": "S1 (tiny)",        "seed": 1, "count_flights": 14, "teams_per_airport": 2},
    {"label": "S2 (small)",       "seed": 2, "count_flights": 21, "teams_per_airport": 2},
    {"label": "S3 (small-med)",   "seed": 3, "count_flights": 28, "teams_per_airport": 2},
    {"label": "S4 (medium)",      "seed": 4, "count_flights": 42, "teams_per_airport": 2},
    {"label": "S5 (medium-lg)",   "seed": 5, "count_flights": 56, "teams_per_airport": 3},
    {"label": "S6 (large)",       "seed": 6, "count_flights": 70, "teams_per_airport": 4},
]


def generate_flights(size: int, start_datetime: datetime, airports: List[Airport],
                     home_airports: List[Airport], dates: List[datetime.date],
                     time_groups: List[datetime.time], distances: Dict[str, int]) -> List[Flight]:
    if size % 2 != 0:
        raise ValueError("The size of flights must be even")

    flights = []
    remaining_airports = [airport for airport in airports if airport not in home_airports]
    count_flights = 0

    while count_flights < size:
        route_size = pick_random_route_size(count_flights, size)
        home_airport = random.choice(home_airports)
        home_flight = Flight(flight_number=str(count_flights),
                             departure_airport=home_airport,
                             arrival_airport=random.choice(remaining_airports))
        flights.append(home_flight)
        count_flights += 1

        next_flight = home_flight
        for _ in range(route_size - 2):
            next_flight = Flight(
                flight_number=str(count_flights),
                departure_airport=next_flight.arrival_airport,
                arrival_airport=pick_random_airport(remaining_airports, next_flight.arrival_airport.code),
            )
            flights.append(next_flight)
            count_flights += 1

        flights.append(Flight(flight_number=str(count_flights),
                              departure_airport=next_flight.arrival_airport,
                              arrival_airport=home_flight.departure_airport))
        count_flights += 1

    for i, flight in enumerate(flights):
        flight.flight_number = f"Flight {i + 1}"

    count_dates = size // len(dates)

    def flight_consumer(f: Flight, d: date):
        key = f"{f.departure_airport.code}-{f.arrival_airport.code}"
        count_hours = distances[key]
        start_time = random.choice(time_groups)
        departure_datetime = datetime.combine(d, start_time)
        if departure_datetime < start_datetime:
            departure_datetime = start_datetime + timedelta(hours=random.randint(0, 4))
        arrival_datetime = departure_datetime + timedelta(hours=count_hours)
        f.departure_utc_date_time = departure_datetime
        f.arrival_utc_date_time = arrival_datetime
        # Assign aircraft type based on duration
        f.aircraft_type = WIDEBODY if count_hours >= LONG_HAUL_HOURS_THRESHOLD else NARROWBODY

    for start_date in dates:
        apply_random_value(count_dates, flights, lambda f: f.departure_utc_date_time is None,
                           lambda f: flight_consumer(f, start_date))
    for flight in flights:
        if flight.departure_utc_date_time is None:
            flight_consumer(flight, random.choice(dates))

    return flights


ALL_LANGUAGES = ["en", "pt", "fr", "nl"]


def _employee_languages(home_airport: Airport) -> List[str]:
    """Base language profile: all languages of home airport + English + 40% bonus language."""
    langs: List[str] = list(home_airport.languages)
    if "en" not in langs:
        langs.append("en")
    if random.random() < 0.4:
        extras = [l for l in ALL_LANGUAGES if l not in langs]
        if extras:
            langs.append(random.choice(extras))
    return langs


def _pilot_type_ratings() -> List[str]:
    """Pilots mostly have one primary type; a third keeps dual rating for flexibility."""
    roll = random.random()
    if roll < 0.35:
        return [NARROWBODY]
    if roll < 0.70:
        return [WIDEBODY]
    return [NARROWBODY, WIDEBODY]


def _cabin_type_ratings() -> List[str]:
    """Cabin crew are cross-trained on both narrow- and wide-body in our demo."""
    return [NARROWBODY, WIDEBODY]


def _assign_preferences(employee: Employee, dates: List[date],
                          all_airport_codes: List[str]) -> None:
    # 35% of staff have one preferred day off
    if random.random() < 0.35:
        employee.preferred_days_off = [random.choice(dates)]
    # 15% have a second preferred day off
    if random.random() < 0.15 and not employee.preferred_days_off:
        employee.preferred_days_off = random.sample(dates, 1)
    elif employee.preferred_days_off and random.random() < 0.25:
        extras = [d for d in dates if d not in employee.preferred_days_off]
        if extras:
            employee.preferred_days_off.append(random.choice(extras))

    # 18% of staff list one airport they'd rather not be sent to (excluding
    # their home base). Drives the `undesired_airport_violation` soft rule.
    home_code = employee.home_airport.code if employee.home_airport is not None else None
    candidates = [c for c in all_airport_codes if c != home_code]
    if candidates and random.random() < 0.18:
        employee.undesired_airports = [random.choice(candidates)]
        # 4% additionally name a second one
        if random.random() < 0.04:
            extras = [c for c in candidates if c not in employee.undesired_airports]
            if extras:
                employee.undesired_airports.append(random.choice(extras))


def generate_employees(flights: List[Flight], dates: List[date],
                        teams_per_airport: int = 2) -> List[Employee]:
    name_supplier = lambda: f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    langs = lambda airport: _employee_languages(airport)

    flight_airports = list({flight.departure_airport for flight in flights})

    employees: List[Employee] = []
    ids = map(str, count())

    def add(*, airport: Airport, rank: str, type_ratings: List[str],
            is_reserve: bool = False) -> Employee:
        emp = Employee(
            id=next(ids), name=name_supplier(), home_airport=airport,
            skills=[rank], languages=langs(airport), unavailable_days=[],
            type_ratings=type_ratings,
            seniority=random.randint(1, 1000),
            is_reserve=is_reserve,
            preferred_days_off=[], undesired_airports=[],
        )
        employees.append(emp)
        return emp

    for airport in flight_airports:
        # `teams_per_airport` full crew teams per base — caller picks scale.
        for team in range(teams_per_airport):
            # Alternate primary rating per team: narrowbody / widebody / dual ...
            mod = team % 3
            if mod == 0:
                pilot_type = [NARROWBODY]
            elif mod == 1:
                pilot_type = [WIDEBODY]
            else:
                pilot_type = [NARROWBODY, WIDEBODY]
            # 30% of pilots within a team keep dual rating regardless.
            if random.random() < 0.3:
                pilot_type = [NARROWBODY, WIDEBODY]
            add(airport=airport, rank=CAPTAIN, type_ratings=list(pilot_type))
            add(airport=airport, rank=FIRST_OFFICER, type_ratings=list(pilot_type))
            add(airport=airport, rank=FIRST_OFFICER, type_ratings=list(pilot_type))
            add(airport=airport, rank=SENIOR_CA, type_ratings=_cabin_type_ratings())
            add(airport=airport, rank=CA, type_ratings=_cabin_type_ratings())
            add(airport=airport, rank=CA, type_ratings=_cabin_type_ratings())

        # Stand-by reserve pool — ~15% of active head-count at each base.
        add(airport=airport, rank=CAPTAIN, type_ratings=_pilot_type_ratings(), is_reserve=True)
        add(airport=airport, rank=FIRST_OFFICER, type_ratings=_pilot_type_ratings(), is_reserve=True)
        add(airport=airport, rank=CA, type_ratings=_cabin_type_ratings(), is_reserve=True)

    # Unavailable days (vacation / sick) — 28% one date, 4% two dates.
    apply_random_value(
        int(0.28 * len(employees)),
        employees,
        lambda e: not e.unavailable_days,
        lambda e: setattr(e, 'unavailable_days', [random.choice(dates)])
    )
    apply_random_value(
        int(0.04 * len(employees)),
        employees,
        lambda e: not e.unavailable_days,
        lambda e: setattr(e, 'unavailable_days', random.sample(dates, 2))
    )

    # Seniority-based preferences (preferred days off + undesired airports)
    all_airport_codes = sorted({a.code for a in flight_airports})
    for emp in employees:
        _assign_preferences(emp, dates, all_airport_codes)

    return employees


def pick_random_airport(airports: List[Airport], exclude_code: str) -> Airport:
    airport = None
    while airport is None or airport.code == exclude_code:
        airport = random.choice(airports)
    return airport


def pick_random_route_size(count_flights: int, max_count_flights: int) -> int:
    allowed_sizes = [2, 4, 6]
    limit = max_count_flights - count_flights
    route_size = 0
    while route_size == 0 or route_size > limit:
        route_size = random.choice(allowed_sizes)
    return route_size


def generate_flight_assignments(flights: List[Flight]) -> List[FlightAssignment]:
    """Each flight gets 1 Captain + 1 First Officer + 1 Senior CA + 2 CA.
    Long-haul flights (>=8h) are augmented with an extra First Officer and an extra CA."""
    flight_assignments: List[FlightAssignment] = []
    id_counter = count(1)

    for flight in flights:
        seats: List[str] = [CAPTAIN, FIRST_OFFICER, SENIOR_CA, CA, CA]
        if flight.is_long_haul():
            # Augmented crew: extra relief First Officer + extra CA
            seats.insert(2, FIRST_OFFICER)
            seats.append(CA)
        if flight.departure_airport.code == "CNF" or flight.arrival_airport.code == "CNF":
            seats.append(CA)
        for idx, seat in enumerate(seats, start=1):
            flight_assignments.append(FlightAssignment(
                id=str(next(id_counter)),
                flight=flight,
                index_in_flight=idx,
                required_skill=seat,
            ))
    return flight_assignments


def apply_random_value(counter: int, values: List[T], filter_func: Callable[[T], bool],
                       consumer: Callable[[T], None]) -> None:
    filtered_values = [v for v in values if filter_func(v)]
    size = len(filtered_values)
    for _ in range(counter):
        if size <= 0:
            break
        selected_item = random.choice(filtered_values) if size > 0 else None
        if selected_item:
            consumer(selected_item)
            filtered_values.remove(selected_item)
            size -= 1
