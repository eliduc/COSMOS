package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.EmployeeHistory
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.time.YearMonth

/**
 * Допущение нулевой истории (1.17.2, слово владельца 05.09.2026): при
 * признаке раздела assume_zero_when_missing сотрудник без записи получает
 * явную нулевую запись на срез — месяц среза у него судится с переносом 0,
 * а не выпадает из универсума формы «выходных в месяц» (первое нажатие
 * простого Н0 без истории дало отказ «ПУСТО»).
 */
class HistoryAssumptionTest {

    private fun schedule(head: MutableMap<String, Any?>?): FlightCrewSchedule =
        FlightCrewSchedule().apply {
            employees = mutableListOf(
                employee("H01"),
                employee("H02").apply {
                    history = EmployeeHistory().apply { historyMonthDaysWorked = 3 }
                    historyAsOf = "2026-09-04T00:00"
                })
            history = head
        }

    @Test
    fun `признак assume_zero ставит нулевую запись только сотрудникам без записи`() {
        val p = schedule(mutableMapOf(
            "as_of" to "2026-09-04T00:00",
            "assume_zero_when_missing" to true,
            "user_decision" to mapOf("question" to "Q-3b-история-нет", "decision" to "continue_zero")))
        val reading = HistoryAssumption.applyZero(p)
        assertNotNull(reading)
        assertEquals(1, reading!!["assumedZero"])
        assertEquals(1, reading["employeesWithoutRecord"])
        assertEquals("continue_zero", reading["userDecision"])
        assertTrue(reading["basis"].toString().contains("решение пользователя continue_zero"))
        val h01 = p.employees.first { it.id == "H01" }
        assertTrue(h01.historyAssumedZero)
        assertEquals(YearMonth.of(2026, 9), h01.historyMonth())
        assertEquals(0L, h01.carryMonthDaysWorked(YearMonth.of(2026, 9)))
        assertEquals(0L, h01.carryConsecutiveNightDuties())
        assertNull(h01.history!!.historyLastDutyEnd)
        // сотрудник с записью не тронут
        val h02 = p.employees.first { it.id == "H02" }
        assertFalse(h02.historyAssumedZero)
        assertEquals(3L, h02.carryMonthDaysWorked(YearMonth.of(2026, 9)))
    }

    @Test
    fun `без признака ноль не подставляется — истории нет явно`() {
        val p = schedule(mutableMapOf(
            "as_of" to "2026-09-04T00:00",
            "assume_zero_when_missing" to false,
            "user_decision" to mapOf("decision" to "continue_partial")))
        val reading = HistoryAssumption.applyZero(p)
        assertNotNull(reading)
        assertEquals(0, reading!!["assumedZero"])
        assertEquals(1, reading["employeesWithoutRecord"])
        val h01 = p.employees.first { it.id == "H01" }
        assertNull(h01.history)
        assertNull(h01.historyMonth())
        assertFalse(h01.historyAssumedZero)
    }

    @Test
    fun `без раздела истории показания нет и сотрудники не тронуты`() {
        val p = schedule(null)
        assertNull(HistoryAssumption.applyZero(p))
        assertNull(p.employees.first { it.id == "H01" }.history)
    }
}
