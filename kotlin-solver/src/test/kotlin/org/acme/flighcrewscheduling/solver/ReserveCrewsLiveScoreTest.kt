package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

/**
 * РЕПРО 2026-08-25: исполнитель суточного резерва в ЖИВОЙ полной сессии
 * (ScoreDirector полного провайдера — прод-идиома score-breakdown) обязан
 * видеть свободных так же, как одиночный ConstraintVerifier. На длинных
 * прогонах полного недостача резерва была ПОЛНОЙ константой (128 = Σнорм
 * × сутки, matches = базы × сутки) при заведомо свободных людях, и
 * ПУСТОЕ решение давало ту же полную недостачу — расхождение живого
 * каскада с верификатором. Тест держит инвариант на минимальном мире.
 */
class ReserveCrewsLiveScoreTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun norm(code: String, n: Double) =
        RuleParam("reserve_crews", n, n.toString(), "count", "min", code, "", "R-1", "HARD")

    private fun crew(id: String, base: String, role: String): Employee = Employee(id).apply {
        name = id
        skills = mutableListOf(role)
        typeRatings = mutableListOf("A320")
        homeAirport = airport(base)
    }

    private fun seat(id: String, f: Flight, role: String, who: Employee?) =
        FlightAssignment(id, f, 0, role).apply { employee = who }

    /** (hard, matches) «Reserve crews» живым ScoreDirector полного провайдера. */
    private fun reserveScore(assign: Boolean): Pair<Int, Int> {
        val ply = airport("PLY")
        val out = airport("OUT")
        val c1 = crew("C1", "PLY", "Captain")
        val c2 = crew("C2", "PLY", "Captain")
        val f1 = crew("F1", "PLY", "FirstOfficer")
        val f2 = crew("F2", "PLY", "FirstOfficer")
        val x = flight("X", ply, t(2, 10), out, t(2, 12))
        val res = RunThresholds.resolve(listOf(norm("PLY", 1.0)), TaskModes.PROFILE_DEFAULT)
        return RunThresholds.withThresholds(res.applied) {
            val cfg = SolverConfig()
                .withSolutionClass(FlightCrewSchedule::class.java)
                .withEntityClasses(FlightAssignment::class.java)
                .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
            val factory = SolverFactory.create<FlightCrewSchedule>(cfg)
            val sdf = (factory as ai.timefold.solver.core.impl.solver.DefaultSolverFactory<FlightCrewSchedule>)
                .getScoreDirectorFactory<HardSoftScore>()
            val director = sdf.createScoreDirectorBuilder()
                .withConstraintMatchPolicy(
                    ai.timefold.solver.core.impl.score.constraint.ConstraintMatchPolicy.ENABLED)
                .build()
            try {
                val problem = FlightCrewSchedule()
                problem.employees.addAll(listOf(c1, c2, f1, f2))
                problem.flights.add(x)
                problem.flightAssignments.add(seat("xc", x, "Captain", if (assign) c1 else null))
                problem.flightAssignments.add(seat("xf", x, "FirstOfficer", if (assign) f1 else null))
                // перегонное кресло решателя (роль — пустая строка): класс
                // 2026-08-25 — не должно попадать в роли состава резерва
                problem.flightAssignments.add(
                    FlightAssignment("dh", x, 101, "").apply { deadhead = true })
                director.setWorkingSolution(problem)
                director.calculateScore()
                val total = director.constraintMatchTotalMap.entries
                    .firstOrNull { it.key.id().endsWith("Reserve crews") }?.value
                (total?.score?.hardScore()?.toInt() ?: 0) to (total?.constraintMatchCount ?: 0)
            } finally {
                director.close()
            }
        }
    }

    @Test
    fun `живая сессия - никто не назначен, свободных хватает - ноль`() {
        assertEquals(0 to 0, reserveScore(assign = false))
    }

    @Test
    fun `живая сессия - экипаж занят, но второй свободен - ноль`() {
        assertEquals(0 to 0, reserveScore(assign = true))
    }
}
