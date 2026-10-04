package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.EmployeeHistory
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import java.time.LocalDate
import java.time.YearMonth

/**
 * История сотрудника (ревизия 1.17, ступень L аудита A7): остатки на
 * начало периода входят в счёт исполнителей — отдых до первой смены
 * горизонта от конца последней смены истории, налёт месяца среза с
 * остатком; без истории (null) исполнители считают только горизонт —
 * это заявленное допущение кнопки, не ноль «по умолчанию».
 */
class EmployeeHistoryConstraintTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun p(name: String, value: Double, relation: String, unit: String = "duration_minutes") =
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

    private fun withHistory(id: String, lastEnd: String?, station: String?,
                            monthBlock: Long?, asOf: String = "2026-03-02T00:00:00"): Employee =
        employee(id).apply {
            homeAirport = home
            historyAsOf = asOf
            history = EmployeeHistory().apply {
                historyLastDutyEnd = lastEnd
                historyLastDutyStation = station
                historyMonthBlockMin = monthBlock
                historyConsecutiveDutyDays = 3
            }
        }

    @Test
    fun `отдых до первой смены горизонта — от конца последней смены истории`() {
        val v = verifierWith(p("min_rest_home", 720.0, "min"), p("min_rest_outstation", 600.0, "min"))
        // дома: конец 1-го в 20:00, первый вылет 2-го в 06:00 — 600 < 720 → 120
        val e1 = withHistory("E1", "2026-03-01T20:00:00", "HOME", null)
        val f = flight("F1", home, t(2, 6), out, t(2, 8))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBeforeHorizon)
            .given(assignment("a1", f, e1)).penalizesBy(120)
        // вне базы: порог 600 — ровно 600, нарушения нет
        val e2 = withHistory("E2", "2026-03-01T20:00:00", "OUT", null)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBeforeHorizon)
            .given(assignment("a2", f, e2)).penalizes(0)
        // без истории — ограничение молчит (допущение заявлено кнопкой)
        val e3 = employee("E3").apply { homeAirport = home }
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBeforeHorizon)
            .given(assignment("a3", f, e3)).penalizes(0)
        // первый вылет — минимум по всем назначениям сотрудника
        val later = flight("F2", out, t(3, 6), home, t(3, 8))
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBeforeHorizon)
            .given(assignment("a1", f, e1), assignment("a4", later, e1)).penalizesBy(120)
    }

    @Test
    fun `налёт месяца среза — с остатком из истории`() {
        val v = verifierWith(p("max_monthly_block", 300.0, "max"))
        val f = flight("F1", home, t(2, 6), out, t(2, 8))          // 120 мин, март 2026
        // остаток 200 + 120 = 320 > 300 → 20
        val e1 = withHistory("E1", null, null, 200L)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxMonthlyFlightHours)
            .given(assignment("a1", f, e1)).penalizesBy(20)
        // срез в другом месяце — остаток не прибавляется
        val e2 = withHistory("E2", null, null, 200L, asOf = "2026-02-02T00:00:00")
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxMonthlyFlightHours)
            .given(assignment("a2", f, e2)).penalizes(0)
        // без истории — только горизонт
        val e3 = employee("E3").apply { homeAirport = home }
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxMonthlyFlightHours)
            .given(assignment("a3", f, e3)).penalizes(0)
    }

    @Test
    fun `переносы истории — только в день и месяц среза`() {
        val e = withHistory("E1", "2026-03-01T20:00:00", "HOME", 200L)
        assertEquals(200L, e.carryMonthBlockMin(YearMonth.of(2026, 3)))
        assertEquals(0L, e.carryMonthBlockMin(YearMonth.of(2026, 4)))
        assertEquals(3L, e.carryConsecutiveDutyDays(LocalDate.of(2026, 3, 2)))
        assertEquals(0L, e.carryConsecutiveDutyDays(LocalDate.of(2026, 3, 3)))
        assertEquals(LocalDate.of(2026, 3, 2), e.historyAsOfDate())
        val none = employee("E9")
        assertNull(none.historyAsOfDate())
        assertEquals(0L, none.carryMonthBlockMin(YearMonth.of(2026, 3)))
    }
}
