# LEGACY (audit 2026-07-10): не входит в результатный тракт отчётов.
# NOTE (rules v2): the AUTHORITATIVE constraint set is the Kotlin provider
# (kotlin-solver/.../FlightCrewSchedulingConstraintProvider.kt). It has been
# refactored to duty-period-aware rest rules (min turnaround 60 min; 6-8h
# dead zone illegal; >=8h rest between duty periods), reconciling the flat
# model with the pairing generator. THIS Python provider still implements
# the OLD "10h rest between any two flights" rule and cannot currently be
# tested (no JPype wheel for Python 3.14 on this machine). Do not compare
# scores across the two providers. See wiki/10-Experiments-Log.md.
from decimal import Decimal
from timefold.solver.score import *
from datetime import time

from .domain import *


MIN_REST_MINUTES = 10 * 60                 # 10 h rest between flights (OLD rule — see NOTE)
MAX_DAILY_DUTY_MINUTES = 14 * 60           # 14 h duty period span per calendar day
MAX_MONTHLY_FLIGHT_MINUTES = 100 * 60      # 100 flying hours per calendar month (EASA cap)
MAX_WEEKLY_FLIGHT_MINUTES = 60 * 60        # 60 flying hours per rolling 7 days (approx.)
MAX_CONSECUTIVE_DUTY_DAYS = 6              # soft threshold: 7+ straight days = warning


@constraint_provider
def define_constraints(constraint_factory: ConstraintFactory):
    return [
        # -------------------- hard (regulatory / operational) --------------
        required_skill(constraint_factory),
        flight_conflict(constraint_factory),
        employee_unavailability(constraint_factory),
        minimum_rest_between_flights(constraint_factory),
        max_daily_duty_period(constraint_factory),
        max_monthly_flight_hours(constraint_factory),
        max_weekly_flight_hours(constraint_factory),
        language_qualification(constraint_factory),
        type_rating_required(constraint_factory),
        # -------------------- soft (preferences / cost) --------------------
        # NB: `positioning_cost` supersedes the old first/last-assignment
        #     home-base rules; we no longer double-count them.
        transfer_between_two_flights(constraint_factory),
        positioning_cost(constraint_factory),
        layover_nights(constraint_factory),
        preferred_day_off_violation(constraint_factory),
        undesired_airport_violation(constraint_factory),
        reserve_usage(constraint_factory),
        consecutive_duty_days(constraint_factory),
        fair_red_eye_distribution(constraint_factory),
        fair_weekend_distribution(constraint_factory),
        balance_workload(constraint_factory),
    ]


def required_skill(constraint_factory: ConstraintFactory) -> Constraint:
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: not fa.has_required_skills())
            .penalize(HardSoftScore.of_hard(100))
            .as_constraint("Required skill"))


def flight_conflict(constraint_factory: ConstraintFactory) -> Constraint:
    return (constraint_factory
            .for_each_unique_pair(FlightAssignment,
                                  Joiners.equal(lambda fa: fa.employee),
                                  Joiners.overlapping(lambda fa: fa.flight.departure_utc_date_time,
                                                      lambda fa: fa.flight.arrival_utc_date_time))
            .penalize(HardSoftScore.of_hard(10))
            .as_constraint("Flight conflict"))


def transfer_between_two_flights(constraint_factory: ConstraintFactory) -> Constraint:
    """When the same employee covers two consecutive flights that don't chain
    (arrival airport of the first != departure airport of the next), the crew
    has to be repositioned. In real operations this is a deadhead, not an
    illegal state — so the rule is soft (and weighted heavily)."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .join(FlightAssignment,
                  Joiners.equal(lambda fa: fa.employee),
                  Joiners.less_than(lambda fa: fa.get_departure_utc_date_time()),
                  Joiners.filtering(lambda fa1, fa2: fa1.id != fa2.id))
            .if_not_exists(FlightAssignment,
                           Joiners.equal(lambda fa1, fa2: fa1.employee, lambda fa2: fa2.employee),
                           Joiners.filtering(
                               lambda fa1, fa2, other_fa: other_fa.id != fa1.id and other_fa.id != fa2.id and
                                                          other_fa.get_departure_utc_date_time() >= fa1.get_departure_utc_date_time() and
                                                          other_fa.get_departure_utc_date_time() < fa2.get_departure_utc_date_time()))
            .filter(lambda fa1, fa2: fa1.flight.arrival_airport != fa2.flight.departure_airport)
            .penalize(HardSoftScore.of_soft(800))
            .as_constraint("Transfer between two flights"))


def employee_unavailability(constraint_factory: ConstraintFactory) -> Constraint:
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.is_unavailable_employee())
            .penalize(HardSoftScore.of_hard(10))
            .as_constraint("Employee unavailable"))


def first_assignment_not_departing_from_home(constraint_factory: ConstraintFactory) -> Constraint:
    return (constraint_factory
            .for_each(Employee)
            .join(FlightAssignment, Joiners.equal(lambda emp: emp, lambda fa: fa.employee))
            .if_not_exists(FlightAssignment,
                           Joiners.equal(lambda emp, fa: emp, lambda fa: fa.employee),
                           Joiners.greater_than(lambda emp, fa: fa.get_departure_utc_date_time(),
                                                lambda fa: fa.get_departure_utc_date_time()))
            .filter(lambda emp, fa: emp.home_airport != fa.flight.departure_airport)
            .penalize(HardSoftScore.of_soft(1000))
            .as_constraint("First assignment not departing from home"))


def last_assignment_not_arriving_at_home(constraint_factory: ConstraintFactory) -> Constraint:
    return (constraint_factory
            .for_each(Employee)
            .join(FlightAssignment, Joiners.equal(lambda emp: emp, lambda fa: fa.employee))
            .if_not_exists(FlightAssignment,
                           Joiners.equal(lambda emp, fa: emp, lambda fa: fa.employee),
                           Joiners.less_than(lambda emp, fa: fa.get_departure_utc_date_time(),
                                             lambda fa: fa.get_departure_utc_date_time()))
            .filter(lambda emp, fa: emp.home_airport != fa.flight.arrival_airport)
            .penalize(HardSoftScore.of_soft(1000))
            .as_constraint("Last assignment not arriving at home"))


# ---------------------------------------------------------------- new rules


def minimum_rest_between_flights(constraint_factory: ConstraintFactory) -> Constraint:
    """Need at least MIN_REST_MINUTES between arrival of a flight and departure of the next one."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None)
            .join(FlightAssignment,
                  Joiners.equal(lambda fa: fa.employee),
                  Joiners.less_than(lambda fa: fa.get_departure_utc_date_time()),
                  Joiners.filtering(lambda fa1, fa2: fa1.id != fa2.id))
            .if_not_exists(FlightAssignment,
                           Joiners.equal(lambda fa1, fa2: fa1.employee, lambda fa: fa.employee),
                           Joiners.filtering(
                               lambda fa1, fa2, other_fa: other_fa.id != fa1.id and other_fa.id != fa2.id and
                                                          fa1.get_departure_utc_date_time() < other_fa.get_departure_utc_date_time() < fa2.get_departure_utc_date_time()))
            .filter(lambda fa1, fa2:
                    (fa2.flight.departure_utc_date_time - fa1.flight.arrival_utc_date_time).total_seconds() / 60 < MIN_REST_MINUTES)
            .penalize(HardSoftScore.of_hard(5),
                      lambda fa1, fa2: max(1, int(MIN_REST_MINUTES -
                                                  (fa2.flight.departure_utc_date_time - fa1.flight.arrival_utc_date_time).total_seconds() / 60)))
            .as_constraint("Minimum rest between flights"))


def max_daily_duty_period(constraint_factory: ConstraintFactory) -> Constraint:
    """For each (employee, calendar day) with 2+ flights, span from first dep to last arr must not exceed 14 h.
    Single long-haul flights are treated as augmented-crew operations and excluded."""
    duty_collector = ConstraintCollectors.compose(
        ConstraintCollectors.count(),
        ConstraintCollectors.min(lambda fa: fa.flight.departure_utc_date_time),
        ConstraintCollectors.max(lambda fa: fa.flight.arrival_utc_date_time),
        lambda n, earliest, latest: (n, earliest, latest),
    )
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None)
            .group_by(
                lambda fa: fa.employee,
                lambda fa: fa.flight.departure_utc_date_time.date(),
                duty_collector,
            )
            .filter(lambda emp, day, duty:
                    duty[0] >= 2 and
                    (duty[2] - duty[1]).total_seconds() / 60 > MAX_DAILY_DUTY_MINUTES)
            .penalize(HardSoftScore.of_hard(5),
                      lambda emp, day, duty:
                      int((duty[2] - duty[1]).total_seconds() / 60) - MAX_DAILY_DUTY_MINUTES)
            .as_constraint("Max daily duty period"))


def max_monthly_flight_hours(constraint_factory: ConstraintFactory) -> Constraint:
    """Total flying minutes per employee per calendar month must not exceed 100 h."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None)
            .group_by(
                lambda fa: fa.employee,
                lambda fa: fa.flight.get_year_month(),
                ConstraintCollectors.sum(lambda fa: fa.flight.get_duration_minutes()),
            )
            .filter(lambda emp, ym, total_minutes: total_minutes > MAX_MONTHLY_FLIGHT_MINUTES)
            .penalize(HardSoftScore.of_hard(3),
                      lambda emp, ym, total_minutes: total_minutes - MAX_MONTHLY_FLIGHT_MINUTES)
            .as_constraint("Max monthly flight hours"))


def language_qualification(constraint_factory: ConstraintFactory) -> Constraint:
    """Employee must speak at least one of the route languages (union of dep + arr airports)."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and
                    len(fa.flight.get_required_languages()) > 0 and
                    not fa.employee.speaks_any(fa.flight.get_required_languages()))
            .penalize(HardSoftScore.of_hard(2))
            .as_constraint("Language qualification"))


def balance_workload(constraint_factory: ConstraintFactory) -> Constraint:
    """Spread flying minutes evenly across employees (minimise unfairness)."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None)
            .group_by(ConstraintCollectors.load_balance(
                lambda fa: fa.employee,
                lambda fa: fa.flight.get_duration_minutes(),
            ))
            .penalize(HardSoftScore.ONE_SOFT,
                      lambda load_balance: int(float(str(load_balance.unfairness()))))
            .as_constraint("Balanced workload"))


# ==================================================================== #10 hard #
#                       PRACTICAL REAL-WORLD CONSTRAINTS                       #
# ============================================================================ #


def type_rating_required(constraint_factory: ConstraintFactory) -> Constraint:
    """(#1) Pilot or cabin crew must hold the type rating for the aircraft.
    In our demo cabin crew are cross-trained, so the rule mostly targets pilots."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and
                    fa.flight is not None and
                    fa.flight.aircraft_type is not None and
                    len(str(fa.flight.aircraft_type)) > 0 and
                    not fa.employee.is_qualified_for(fa.flight.aircraft_type))
            .penalize(HardSoftScore.of_hard(50))
            .as_constraint("Type rating required"))


def max_weekly_flight_hours(constraint_factory: ConstraintFactory) -> Constraint:
    """(#2) No employee may fly more than 60 hours in any ISO week."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None)
            .group_by(
                lambda fa: fa.employee,
                lambda fa: fa.flight.departure_utc_date_time.isocalendar()[:2],
                ConstraintCollectors.sum(lambda fa: fa.flight.get_duration_minutes()),
            )
            .filter(lambda emp, week, total: total > MAX_WEEKLY_FLIGHT_MINUTES)
            .penalize(HardSoftScore.of_hard(2),
                      lambda emp, week, total: total - MAX_WEEKLY_FLIGHT_MINUTES)
            .as_constraint("Max weekly flight hours"))


# ============================================================================ #
#                               SOFT CONSTRAINTS                               #
# ============================================================================ #


def positioning_cost(constraint_factory: ConstraintFactory) -> Constraint:
    """(#5) Penalise assignments that force an employee to reposition (deadhead)
    from their home base. One penalty per pairing that does not depart from home
    — duration isn't a multiplier here because we already penalise `layover
    nights` separately and don't want this dominating the soft score."""
    return (constraint_factory
            .for_each(Employee)
            .join(FlightAssignment, Joiners.equal(lambda emp: emp, lambda fa: fa.employee))
            .if_not_exists(FlightAssignment,
                           Joiners.equal(lambda emp, fa: emp, lambda fa: fa.employee),
                           Joiners.greater_than(lambda emp, fa: fa.get_departure_utc_date_time(),
                                                lambda fa: fa.get_departure_utc_date_time()))
            .filter(lambda emp, fa: emp.home_airport != fa.flight.departure_airport)
            .penalize(HardSoftScore.of_soft(500))
            .as_constraint("Positioning deadhead cost"))


def layover_nights(constraint_factory: ConstraintFactory) -> Constraint:
    """(#5) Penalise nights spent away from the home base. Approximation:
    count distinct calendar days where an employee is on duty but not at home."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and
                    fa.employee.home_airport != fa.flight.arrival_airport)
            .group_by(lambda fa: fa.employee,
                      lambda fa: fa.flight.arrival_utc_date_time.date(),
                      ConstraintCollectors.count())
            .filter(lambda emp, day, n: emp.home_airport is not None)
            .penalize(HardSoftScore.of_soft(40))
            .as_constraint("Layover nights away from home"))


def preferred_day_off_violation(constraint_factory: ConstraintFactory) -> Constraint:
    """(#6) PBS: respect preferred days off, weighted by seniority (senior gets 10x)."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and
                    fa.employee.preferred_days_off is not None and
                    fa.flight.get_departure_utc_date() in fa.employee.preferred_days_off)
            .penalize(HardSoftScore.of_soft(50),
                      lambda fa: fa.employee.seniority_weight() * 10)
            .as_constraint("Preferred day off violation"))


def undesired_airport_violation(constraint_factory: ConstraintFactory) -> Constraint:
    """(#6) PBS: avoid sending crew to airports they explicitly asked to skip."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and
                    fa.employee.undesired_airports is not None and
                    (fa.flight.departure_airport.code in fa.employee.undesired_airports or
                     fa.flight.arrival_airport.code in fa.employee.undesired_airports))
            .penalize(HardSoftScore.of_soft(20),
                      lambda fa: fa.employee.seniority_weight() * 5)
            .as_constraint("Undesired airport"))


def reserve_usage(constraint_factory: ConstraintFactory) -> Constraint:
    """(#8) Reserves cost more than active line crew — use only when no alternative."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and fa.employee.is_reserve)
            .penalize(HardSoftScore.of_soft(300))
            .as_constraint("Reserve crew usage"))


def consecutive_duty_days(constraint_factory: ConstraintFactory) -> Constraint:
    """(#2 / fatigue) Penalise employees who work more than 6 calendar days in
    any ISO week. This is a soft indicator of cumulative fatigue."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None)
            .group_by(
                lambda fa: fa.employee,
                lambda fa: fa.flight.departure_utc_date_time.isocalendar()[:2],
                ConstraintCollectors.count_distinct(lambda fa: fa.flight.departure_utc_date_time.date()),
            )
            .filter(lambda emp, week, n_days: n_days > MAX_CONSECUTIVE_DUTY_DAYS)
            .penalize(HardSoftScore.of_soft(500),
                      lambda emp, week, n_days: n_days - MAX_CONSECUTIVE_DUTY_DAYS)
            .as_constraint("Too many duty days per week"))


def fair_red_eye_distribution(constraint_factory: ConstraintFactory) -> Constraint:
    """(#10) Share night departures (00:00-06:00) evenly among employees."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and fa.flight.is_red_eye())
            .group_by(ConstraintCollectors.load_balance(lambda fa: fa.employee))
            .penalize(HardSoftScore.ONE_SOFT,
                      lambda lb: int(float(str(lb.unfairness())) * 20))
            .as_constraint("Fair red-eye distribution"))


def fair_weekend_distribution(constraint_factory: ConstraintFactory) -> Constraint:
    """(#10) Share weekend work evenly among employees."""
    return (constraint_factory
            .for_each(FlightAssignment)
            .filter(lambda fa: fa.employee is not None and fa.flight.is_weekend_flight())
            .group_by(ConstraintCollectors.load_balance(lambda fa: fa.employee))
            .penalize(HardSoftScore.ONE_SOFT,
                      lambda lb: int(float(str(lb.unfairness())) * 20))
            .as_constraint("Fair weekend distribution"))
