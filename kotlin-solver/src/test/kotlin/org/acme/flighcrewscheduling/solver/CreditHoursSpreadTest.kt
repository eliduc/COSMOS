package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import java.math.BigDecimal
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

/**
 * Задачи стенда с известным ответом для правила ВМП о разбросе
 * кредитных часов (семья balanced_workload, решение владельца
 * 2026-09-03 «пилот без работы входит в разброс»):
 *  • совокупность — штат: сотрудник без назначений входит с нулём;
 *  • единица — целый кредитный час (минуты налёта / 60, усечение);
 *  • кредит — налёт по назначению, перелёт пассажиром не засчитывается;
 *  • правило штрафует только превышение порога, целевая — весь разброс.
 * Мера — √Σ(xᵢ−x̄)² по минутам (unfairness коллектора), без деления на n.
 */
class CreditHoursSpreadTest {

    private val verifier:
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider,
            FlightCrewSchedule> =
        ConstraintVerifier.build(
            FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    private val base = airport("BSE")
    private val out = airport("OUT")

    private fun crewman(id: String): Employee =
        employee(id).apply { homeAirport = base }

    private fun seat(id: String, f: Flight, who: Employee, dh: Boolean = false) =
        FlightAssignment(id, f, 0, Fixtures.SKILL).apply {
            employee = who; deadhead = dh }

    /** Два рейса по 2 и 4 часа: налёт 120 и 240 мин → √(60²+60²) = 84,85 мин → 1 час. */
    @Test
    fun `два сотрудника - разброс в целых часах`() {
        val a = crewman("A"); val b = crewman("B")
        val f1 = flight("F1", base, t(1, 8), out, t(1, 10))
        val f2 = flight("F2", base, t(2, 8), out, t(2, 12))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::creditHoursSpread)
            .given(a, b, seat("s1", f1, a), seat("s2", f2, b))
            .penalizesBy(1)
    }

    /** Тот же штат плюс сотрудник без единого назначения: 120, 240, 0 →
     *  среднее 120, √(0 + 120² + 120²) = 169,7 мин → 2 часа. Пилот без
     *  работы ВХОДИТ в разброс. */
    @Test
    fun `сотрудник без назначений входит в разброс нулём`() {
        val a = crewman("A"); val b = crewman("B"); val idle = crewman("C")
        val f1 = flight("F1", base, t(1, 8), out, t(1, 10))
        val f2 = flight("F2", base, t(2, 8), out, t(2, 12))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::creditHoursSpread)
            .given(a, b, idle, seat("s1", f1, a), seat("s2", f2, b))
            .penalizesBy(2)
    }

    /** Перелёт пассажиром кредита не даёт: сотрудник B летит 4 часа
     *  пассажиром → его кредит 0; 120 и 0 → √(60²+60²) = 84,85 → 1 час. */
    @Test
    fun `перелёт пассажиром не засчитывается`() {
        val a = crewman("A"); val b = crewman("B")
        val f1 = flight("F1", base, t(1, 8), out, t(1, 10))
        val f2 = flight("F2", base, t(2, 8), out, t(2, 12))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::creditHoursSpread)
            .given(a, b, seat("s1", f1, a), seat("s2", f2, b, dh = true))
            .penalizesBy(1)
    }

    /** Равный налёт — разброс ноль; один сотрудник — тоже ноль. */
    @Test
    fun `равный налёт и одиночка не штрафуются`() {
        val a = crewman("A"); val b = crewman("B")
        val f1 = flight("F1", base, t(1, 8), out, t(1, 10))
        val f2 = flight("F2", base, t(2, 8), out, t(2, 10))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::creditHoursSpread)
            .given(a, b, seat("s1", f1, a), seat("s2", f2, b))
            .penalizesBy(0)
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::creditHoursSpread)
            .given(a, seat("s1", f1, a))
            .penalizesBy(0)
    }

    /** Без порога в окне (прогон без пакета: порог 0) правило штрафует
     *  весь разброс — те же единицы, что у целевой. */
    @Test
    fun `правило без порога штрафует весь разброс`() {
        val a = crewman("A"); val b = crewman("B"); val idle = crewman("C")
        val f1 = flight("F1", base, t(1, 8), out, t(1, 10))
        val f2 = flight("F2", base, t(2, 8), out, t(2, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::creditHoursSpreadOverThreshold)
            .given(a, b, idle, seat("s1", f1, a), seat("s2", f2, b))
            .penalizesBy(2)
    }

    /** Арифметика порога на самой формуле (сертификация прохода 26:
     *  мера 174,3 мин = 2,9 ч): целевая 2 часа; порог 10 ч → 0;
     *  порог 1 ч → floor(2,9 − 1) = 1; порог 2,5 ч → floor(0,4) = 0;
     *  мера 12,7 ч при пороге 10 → 2. Часы вещественные до вычитания,
     *  усечение один раз после — контракт с сертификатом CDPM. */
    @Test
    fun `порог вычитается до усечения`() {
        val p = FlightCrewSchedulingConstraintProvider()
        val m174 = BigDecimal("174.3")
        assertEquals(2L, p.spreadHoursOf(m174))
        assertEquals(0L, p.spreadHoursOverThreshold(m174, 600))
        assertEquals(1L, p.spreadHoursOverThreshold(m174, 60))
        assertEquals(0L, p.spreadHoursOverThreshold(m174, 150))
        assertEquals(2L, p.spreadHoursOverThreshold(BigDecimal("762"), 600))
        assertEquals(0L, p.spreadHoursOverThreshold(BigDecimal("0"), 0))
    }
}
