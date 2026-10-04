package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/**
 * Перегоны экипажа (этап Г): место пассажира — тот же класс назначения
 * с флагом deadhead. Контракт зачёта по позициям (аналогия правил
 * обеих линий): непрерывность, стыковки, отдых и размах смены считают перегон
 * КАК ЕСТЬ; налёт, посадки, навыки, численность и покрытие — НЕ считают.
 * Пределы приходят только из пакета: без порога кресла не порождаются,
 * счёт прогонов без пакета не меняется ни на очко.
 */
class DeadheadTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun p(name: String, value: Double, relation: String,
                  unit: String = "count") =
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

    private val a = airport("AAA")
    private val b = airport("BBB")
    private val c = airport("CCC")
    private val d = airport("DDD")
    private val e1 = employee("E1")

    private fun seat(id: String, f: Flight, who: Employee?) =
        FlightAssignment(id, f, 0, Fixtures.SKILL).apply { employee = who }

    private fun dh(id: String, f: Flight, who: Employee?) =
        FlightAssignment(id, f, 101, "").apply { deadhead = true; employee = who }

    // ---------------------------------------------------------- порождение

    @Test
    fun `кресла порождаются из порога пакета и идемпотентно`() {
        val res = RunThresholds.resolve(
            listOf(p("max_deadheads_per_flight", 2.0, "max")), TaskModes.PROFILE_DEFAULT)
        val f1 = flight("F1", a, t(2, 10), b, t(2, 12))
        val problem = FlightCrewSchedule()
        problem.flights.add(f1)
        problem.flightAssignments.add(seat("s1", f1, null))
        assertEquals(2, DeadheadSeats.ensure(problem, res.applied))
        assertEquals(3, problem.flightAssignments.size)
        assertTrue(problem.flightAssignments.count { it.deadhead } == 2)
        // Повторный вызов не плодит вторых кресел (повторный счёт выданного).
        assertEquals(0, DeadheadSeats.ensure(problem, res.applied))
        assertEquals(3, problem.flightAssignments.size)
    }

    @Test
    fun `без порога пакета кресла не порождаются - возможность выключена`() {
        val res = RunThresholds.resolve(emptyList(), TaskModes.PROFILE_DEFAULT)
        val f1 = flight("F1", a, t(2, 10), b, t(2, 12))
        val problem = FlightCrewSchedule()
        problem.flights.add(f1)
        assertEquals(0, DeadheadSeats.ensure(problem, res.applied))
        assertEquals(0, problem.flightAssignments.size)
    }

    // ------------------------------------------------- зачёт по позициям

    @Test
    fun `пустое перегонное кресло не считается непокрытым местом`() {
        val v = verifierWith()
        val f1 = flight("F1", a, t(2, 10), b, t(2, 12))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::unassignedSlot)
            .given(dh("dh1", f1, null)).penalizes(0)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::unassignedSlot)
            .given(seat("s1", f1, null)).penalizesBy(1)
    }

    @Test
    fun `пассажиру не нужен навык места`() {
        val v = verifierWith()
        val f1 = flight("F1", a, t(2, 10), b, t(2, 12))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::requiredSkill)
            .given(dh("dh1", f1, e1)).penalizes(0)
    }

    @Test
    fun `перегон не входит в месячный налёт`() {
        val v = verifierWith(p("max_monthly_block", 60.0, "max", "duration_minutes"))
        val long1 = flight("F1", a, t(2, 8), b, t(2, 10))    // 120 мин
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxMonthlyFlightHours)
            .given(seat("s1", long1, e1)).penalizesBy(60)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxMonthlyFlightHours)
            .given(dh("dh1", long1, e1)).penalizes(0)
    }

    @Test
    fun `перегонное кресло не раздувает численность экипажа`() {
        val v = verifierWith(p("crew_size", 1.0, "eq"))
        val f1 = flight("F1", a, t(2, 10), b, t(2, 12))
        val e2 = employee("E2")
        // Одно место состава занято; пассажир сверх — численность ровно 1.
        v.verifyThat(FlightCrewSchedulingConstraintProvider::crewSizePerFlight)
            .given(seat("s1", f1, e1), dh("dh1", f1, e2)).penalizes(0)
    }

    // --------------------------------------------- перегон чинит маршрут

    @Test
    fun `перегон замыкает разрыв маршрута`() {
        val v = verifierWith(p("max_deadheads_per_flight", 1.0, "max"))
        val leg1 = flight("F1", a, t(2, 8), b, t(2, 9))
        val bridge = flight("F2", b, t(2, 11), c, t(2, 12))
        val leg2 = flight("F3", c, t(2, 14), d, t(2, 15))
        // Без перегона: прилетел в BBB, следующий вылет из CCC — разрыв.
        v.verifyThat(FlightCrewSchedulingConstraintProvider::routeContinuityHard)
            .given(seat("s1", leg1, e1), seat("s3", leg2, e1)).penalizesBy(1)
        // Пассажиром на BBB->CCC: обе смежные пары непрерывны.
        v.verifyThat(FlightCrewSchedulingConstraintProvider::routeContinuityHard)
            .given(seat("s1", leg1, e1), dh("dh2", bridge, e1), seat("s3", leg2, e1))
            .penalizes(0)
    }

    // ------------------------------------------------------ суточный предел

    @Test
    fun `перегонов в сутки не больше порога пакета`() {
        val v = verifierWith(p("max_deadheads_per_flight", 2.0, "max"),
                             p("max_deadheads_per_day", 1.0, "max"))
        val f1 = flight("F1", a, t(2, 8), b, t(2, 9))
        val f2 = flight("F2", b, t(2, 11), c, t(2, 12))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::deadheadsPerDay)
            .given(dh("dh1", f1, e1)).penalizes(0)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::deadheadsPerDay)
            .given(dh("dh1", f1, e1), dh("dh2", f2, e1)).penalizesBy(1)
    }

    @Test
    fun `без порогов пакета суточный предел выключен`() {
        val v = verifierWith()
        val f1 = flight("F1", a, t(2, 8), b, t(2, 9))
        val f2 = flight("F2", b, t(2, 11), c, t(2, 12))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::deadheadsPerDay)
            .given(dh("dh1", f1, e1), dh("dh2", f2, e1)).penalizes(0)
    }

    // ------------------------------------------------------ факты набора

    @Test
    fun `перегонные кресла не ломают проверку состава мест`() {
        val res = RunThresholds.resolve(
            listOf(p("crew_size", 1.0, "eq"),
                   p("max_deadheads_per_flight", 2.0, "max")), TaskModes.PROFILE_DEFAULT)
        val f1 = flight("F1", a, t(2, 10), b, t(2, 12))
        val problem = FlightCrewSchedule()
        problem.flights.add(f1)
        problem.flightAssignments.add(seat("s1", f1, null))
        DeadheadSeats.ensure(problem, res.applied)
        // Состав мест судит НАБОР: 1 место при численности 1 — годен,
        // перегонные кресла решателя вне состава.
        val out = RunThresholds.checkFacts(problem, res.applied)
        assertTrue(out.containsKey("crewSize"))
    }
}
