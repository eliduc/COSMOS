package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import java.time.LocalDateTime

/**
 * Форма ЛИМИТ СЕРИИ РАБОЧИХ СМЕН ПОДРЯД И ОБЯЗАТЕЛЬНЫЙ ОТДЫХ ПОСЛЕ НЕЁ
 * (запись каталога `families/duty_series_limit`, заявка CDPM 09.09.2026).
 *
 * Миры и числа — те же, что у стендовых задач записи (`st-series-0`,
 * `st-series-1`, `st-series-2`): один мир — два судьи. Октябрь 2026,
 * горизонт 01–10; рабочий день — пара рейсов ST1→ST2→ST1; пределы
 * приходят планом (`max_duty_series_length` = 3, `post_series_days_off`
 * = 2), своих чисел у формы нет.
 */
class DutySeriesLimitConstraintTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private val home = airport("ST1")
    private val out = airport("ST2")

    /** План формы: границы — из ИМЁН параметров записи каталога. */
    private fun plan(maxSeries: Int, daysOffAfter: Int) = LogicPlan(
        ruleIds = listOf("R-STAND-duty_series_limit"),
        constraintId = "logic dutyser1",
        form = LogicForms.FORM_DUTY_SERIES_LIMIT, subject = "day_off", relation = "min",
        bound = daysOffAfter, boundParam = "post_series_days_off",
        baseScope = null, condBound = maxSeries, condParam = "max_duty_series_length",
        enforce = "required", weight = 1,
        reading = LogicForms.READING_DUTY_SERIES, shapeSha = "x")

    /**
     * Провайдер строится ВНУТРИ окна с планом — ровно так, как его
     * создаёт фабрика прогона; форма берётся его же построителем.
     */
    private fun check(p: LogicPlan, vararg facts: Any) =
        RunThresholds.withRunModel(emptyMap(), listOf(p)) {
            ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
                FlightCrewSchedule::class.java, FlightAssignment::class.java)
        }.verifyThat { prov, cf -> prov.buildLogic(cf, p) }.given(*facts)

    /** Рабочие сутки октября: пара рейсов туда-обратно, ночёвка дома. */
    private fun workDay(facts: MutableList<Any>, e: Employee, d: Int) {
        val outbound = flight("F${d}A", home, LocalDateTime.of(2026, 10, d, 6, 0),
            out, LocalDateTime.of(2026, 10, d, 8, 30))
        val back = flight("F${d}B", out, LocalDateTime.of(2026, 10, d, 9, 15),
            home, LocalDateTime.of(2026, 10, d, 12, 0))
        facts.add(outbound)
        facts.add(assignment("a${d}A", outbound, e))
        facts.add(back)
        facts.add(assignment("a${d}B", back, e))
    }

    /** Мир задачи: сотрудник P1 и его рабочие сутки. */
    private fun world(vararg workedDays: Int): Array<Any> {
        val e = employee("P1").apply { homeAirport = home }
        val facts = ArrayList<Any>(listOf(e))
        for (d in workedDays) workDay(facts, e, d)
        return facts.toTypedArray()
    }

    @Test
    fun `st-series-0 — серия ровно в предел и два свободных дня после неё не штрафуются`() {
        // Рабочие сутки 2,3,4 (серия 3 = предел), свободные 5 и 6,
        // затем 7,8 — последняя серия, её хвост не судится.
        check(plan(3, 2), *world(2, 3, 4, 7, 8)).penalizes(0)
    }

    @Test
    fun `st-series-1 — серия сверх предела и один свободный день после неё`() {
        // Рабочие сутки 2,3,4,5 (серия 4 > 3 — сутки сверх предела),
        // свободные только 6 (нужно 2), затем 7,8 — последняя серия.
        check(plan(3, 2), *world(2, 3, 4, 5, 7, 8)).penalizes(1)
        check(plan(3, 2), *world(2, 3, 4, 5, 7, 8)).penalizesBy(2)
    }

    @Test
    fun `st-series-2 — серия ровно в предел, но свободных суток после неё меньше нормы`() {
        // Рабочие сутки 2,3,4 (серия 3 = предел достигнут), свободные
        // только 5, затем 6,7 — последняя серия.
        check(plan(3, 2), *world(2, 3, 4, 6, 7)).penalizes(1)
        check(plan(3, 2), *world(2, 3, 4, 6, 7)).penalizesBy(1)
    }

    @Test
    fun `предел серии приезжает планом - при пределе 4 тот же мир чист`() {
        // Ни одного своего числа у формы нет: при другом пределе тот же
        // мир не даёт штрафа (серия 4 не достигает предела 5).
        check(plan(5, 2), *world(2, 3, 4, 5, 7, 8)).penalizes(0)
    }

    @Test
    fun `имена формы и границ — литералы строки посева привязок`() {
        assertEquals("DUTY_SERIES_LIMIT", LogicForms.FORM_DUTY_SERIES_LIMIT)
        assertEquals("max_duty_series_length", plan(3, 2).condParam)
        assertEquals("post_series_days_off", plan(3, 2).boundParam)
    }
}
