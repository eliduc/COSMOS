package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.Test
import java.time.LocalDateTime
import java.time.ZoneOffset

/**
 * Свежесть допуска (правило свода о действующем допуске на тип судна).
 *
 * До 2026-08-23 правило было неисполнимо по отсутствию ДАННЫХ: конвертер
 * пакета читал лист «Допуски», но колонку «Действует до» отбрасывал, и
 * срок до решателя не доезжал вовсе. Профиль проверял только наличие
 * допуска в списке. Здесь проверяется разделение трёх случаев: допуска
 * нет; допуск есть и действует; допуск есть, но просрочен.
 */
class TypeRatingCurrencyTest {

    private val verifier: ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> =
        ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java, FlightAssignment::class.java)

    private val home = airport("HOME")
    private val out = airport("OUT")
    private val type = "Полярис-Р200"
    /** Рейс 2 марта 10:00–12:00 (Fixtures.t — 2026 год). */
    private val f = flight("F1", home, t(2, 10), out, t(2, 12)).apply { aircraftType = type }

    private fun mins(d: LocalDateTime): Long = d.toEpochSecond(ZoneOffset.UTC) / 60

    private fun crew(id: String, rated: Boolean, window: Pair<LocalDateTime, LocalDateTime>?): Employee =
        Employee(id).apply {
            name = id
            skills = mutableListOf(Fixtures.SKILL)
            typeRatings = if (rated) mutableListOf(type) else mutableListOf()
            if (window != null) {
                qualificationWindows = mutableMapOf(
                    type to mutableListOf(mutableListOf(mins(window.first), mins(window.second))))
            }
        }

    private fun seat(who: Employee) = FlightAssignment("s1", f, 0, Fixtures.SKILL).apply { employee = who }

    private fun check(e: Employee) =
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::typeRatingCurrency).given(seat(e))

    @Test
    fun `допуск действует на время рейса — ноль`() {
        val e = crew("E1", rated = true,
            window = LocalDateTime.of(2020, 1, 1, 0, 0) to LocalDateTime.of(2027, 3, 31, 23, 59))
        check(e).penalizes(0)
    }

    @Test
    fun `допуск просрочен к рейсу — нарушение`() {
        val e = crew("E2", rated = true,
            window = LocalDateTime.of(2020, 1, 1, 0, 0) to LocalDateTime.of(2026, 1, 5, 23, 59))
        check(e).penalizesBy(1)
    }

    @Test
    fun `допуск ещё не начался — нарушение`() {
        val e = crew("E3", rated = true,
            window = LocalDateTime.of(2026, 6, 1, 0, 0) to LocalDateTime.of(2027, 1, 1, 0, 0))
        check(e).penalizesBy(1)
    }

    @Test
    fun `окно кончается посреди рейса — нарушение, судится весь рейс`() {
        // Допуск действует до 11:00, рейс 10:00–12:00: человек в воздухе
        // с недействующим допуском — правило не делит рейс пополам.
        val e = crew("E4", rated = true,
            window = LocalDateTime.of(2020, 1, 1, 0, 0) to LocalDateTime.of(2026, 3, 2, 11, 0))
        check(e).penalizesBy(1)
    }

    @Test
    fun `сроков не передали — правило молчит, а не считает допуск просроченным`() {
        // Отсутствие окон значит «срок не передан»: судить не о чем, и
        // подставлять «сегодня» вместо срока запрещено.
        check(crew("E5", rated = true, window = null)).penalizes(0)
    }

    @Test
    fun `допуска нет вовсе — это другое правило, здесь ноль`() {
        // Ловит «Type rating required»; дублировать нарушение нельзя.
        check(crew("E6", rated = false, window = null)).penalizes(0)
    }
}
