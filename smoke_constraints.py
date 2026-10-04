"""Headless test of new constraints: build demo problem, solve 30s, print breakdown."""
from timefold.solver import SolverFactory, SolutionManager
from timefold.solver.config import (
    Duration,
    ScoreDirectorFactoryConfig,
    SolverConfig,
    TerminationConfig,
)

from flight_crew_scheduling.constraints import define_constraints
from flight_crew_scheduling.demo_data import generate_demo_data
from flight_crew_scheduling.domain import FlightAssignment, FlightCrewSchedule


def main() -> None:
    problem = generate_demo_data()
    print(f"Problem: {len(problem.airports)} airports, {len(problem.employees)} employees, "
          f"{len(problem.flights)} flights, {len(problem.flight_assignments)} assignments")

    config = SolverConfig(
        solution_class=FlightCrewSchedule,
        entity_class_list=[FlightAssignment],
        score_director_factory_config=ScoreDirectorFactoryConfig(
            constraint_provider_function=define_constraints,
        ),
        termination_config=TerminationConfig(spent_limit=Duration(seconds=60)),
    )
    factory = SolverFactory.create(config)
    solver = factory.build_solver()
    solution = solver.solve(problem)

    print(f"\nFinal score: {solution.score}")

    manager = SolutionManager.create(factory)
    analysis = manager.analyze(solution)
    print("\nConstraint breakdown:")
    print(f"  {'constraint':<40s} {'score':>20s}  matches")
    for ca in sorted(analysis.constraint_analyses, key=lambda c: str(c.score)):
        print(f"  {ca.constraint_name:<40s} {str(ca.score):>20s}  {ca.match_count}")


if __name__ == "__main__":
    main()
