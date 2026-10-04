package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test

/**
 * Семья duty_extension_penalty (шаблон «мягкое превышение порога
 * длительности смены до жёсткого потолка», конвейер 2026-08-31 по
 * заявке демо): смена длиннее мягкого порога штрафуется ЗА КАЖДУЮ
 * такую смену, жёсткой гранью становится потолок продления; без
 * семьи прежний порядок нетронут (жёсткая грань — базовый предел,
 * штраф спит). Провайдер строится в окне порогов — проверяется и
 * доезд порога, а не только арифметика.
 */
class DutyExtensionConstraintTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun p(name: String, value: Double, relation: String,
                  rule: String = "R-1") =
        RuleParam(name, value, value.toString(), "duration_minutes",
            relation, null, "", rule, "HARD")

    private fun verifier(capMin: Int?):
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> {
        var res = RunThresholds.resolve(
            listOf(p("max_duty_duration", 720.0, "max")),
            TaskModes.PROFILE_DEFAULT)
        if (capMin != null) {
            res = RunThresholds.applyDutyExtension(
                res, capMin, listOf("R-EXT"))
        }
        val provider = RunThresholds.withThresholds(res.applied) {
            FlightCrewSchedulingConstraintProvider()
        }
        return ConstraintVerifier.build(provider,
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)
    }

    private fun homeEmployee() =
        employee("E1").apply { homeAirport = airport("HOME") }

    /** Смена 750 минут одного человека: два рейса с короткой стыковкой. */
    private fun longDuty(e: org.acme.flighcrewscheduling.domain.Employee,
                         day: Int): List<FlightAssignment> {
        val home = airport("HOME"); val out = airport("OUT")
        return listOf(
            assignment("d$day-1",
                flight("F$day-1", home, t(day, 6), out, t(day, 12)), e),
            assignment("d$day-2",
                flight("F$day-2", out, t(day, 12, 45), home,
                    t(day, 18, 30)), e))
    }

    @Test
    fun `смена длиннее порога штрафуется, потолок держит жёсткую грань`() {
        val v = verifier(780)
        val e = homeEmployee()
        val duty = longDuty(e, 2)   // 750 мин: порог 720 < 750 < потолок 780
        v.verifyThat(FlightCrewSchedulingConstraintProvider::dutyExtensionOverThreshold)
            .given(*duty.toTypedArray()).penalizesBy(1)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(*duty.toTypedArray()).penalizes(0)
    }

    @Test
    fun `смена в пределах порога не штрафуется`() {
        val v = verifier(780)
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        val short = listOf(
            assignment("s1", flight("S1", home, t(2, 6), out, t(2, 12)), e),
            assignment("s2", flight("S2", out, t(2, 12, 45), home,
                t(2, 17, 40)), e))   // 700 мин
        v.verifyThat(FlightCrewSchedulingConstraintProvider::dutyExtensionOverThreshold)
            .given(*short.toTypedArray()).penalizes(0)
    }

    @Test
    fun `каждая продлённая смена платит отдельно`() {
        val v = verifier(780)
        val e = homeEmployee()
        val both = longDuty(e, 2) + longDuty(e, 4)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::dutyExtensionOverThreshold)
            .given(*both.toTypedArray()).penalizesBy(2)
    }

    @Test
    fun `без семьи штраф спит, а жёсткая грань - базовый предел`() {
        val v = verifier(null)
        val e = homeEmployee()
        val duty = longDuty(e, 2)   // 750 мин против предела 720
        v.verifyThat(FlightCrewSchedulingConstraintProvider::dutyExtensionOverThreshold)
            .given(*duty.toTypedArray()).penalizes(0)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(*duty.toTypedArray()).penalizesBy(750L - 720L)
    }
}
