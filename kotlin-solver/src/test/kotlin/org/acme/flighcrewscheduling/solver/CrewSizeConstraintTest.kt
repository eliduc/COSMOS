package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test

/**
 * Численность экипажа (правило свода): исполнитель считает РАЗЛИЧНЫХ
 * сотрудников на местах рейса. Провайдер строится внутри окна порогов —
 * проверяется, что число пакета доезжает до ограничения.
 */
class CrewSizeConstraintTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun p(name: String, value: Double, relation: String, unit: String = "count") =
        RuleParam(name, value, value.toString(), unit, relation, null, "", "R-1", "HARD")

    private fun verifierWith(vararg params: RuleParam):
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> {
        val res = RunThresholds.resolve(params.toList(), TaskModes.PROFILE_DEFAULT)
        val provider = RunThresholds.withThresholds(res.applied) {
            FlightCrewSchedulingConstraintProvider()
        }
        return ConstraintVerifier.build(provider, FlightCrewSchedule::class.java,
            FlightAssignment::class.java)
    }

    private val home = airport("HOME")
    private val out = airport("OUT")
    private val e1 = employee("E1")
    private val e2 = employee("E2")
    private val f = flight("F1", home, t(2, 10), out, t(2, 12))

    private fun seat(id: String, who: org.acme.flighcrewscheduling.domain.Employee?) =
        FlightAssignment(id, f, 0, Fixtures.SKILL).apply { employee = who }

    @Test
    fun `ровно N различных — ноль, дубль лица — один, пустое место — один, два пустых — два`() {
        val v = verifierWith(p("crew_size", 2.0, "eq"))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::crewSizePerFlight)
            .given(seat("s1", e1), seat("s2", e2)).penalizes(0)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::crewSizePerFlight)
            .given(seat("s1", e1), seat("s2", e1)).penalizesBy(1)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::crewSizePerFlight)
            .given(seat("s1", e1), seat("s2", null)).penalizesBy(1)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::crewSizePerFlight)
            .given(seat("s1", null), seat("s2", null)).penalizesBy(2)
    }

    @Test
    fun `число пакета доезжает - при ручке 3 два разных человека дают один`() {
        val v = verifierWith(p("crew_size", 3.0, "eq"))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::crewSizePerFlight)
            .given(seat("s1", e1), seat("s2", e2)).penalizesBy(1)
    }

    @Test
    fun `без пакета правило выключено - дубль лица не штрафуется численностью`() {
        val off = ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java, FlightAssignment::class.java)
        off.verifyThat(FlightCrewSchedulingConstraintProvider::crewSizePerFlight)
            .given(seat("s1", e1), seat("s2", e1)).penalizes(0)
    }
}
