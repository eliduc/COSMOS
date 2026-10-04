package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.EmployeeHistory
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule

/**
 * Допущение нулевой истории (ревизия 1.17.2 раздела history, слово
 * владельца 05.09.2026: «можно не задавать историю, приложение предупредит,
 * пользователь нажмёт „Продолжать с нулевой историей“, расчёт продолжится»).
 *
 * Шапка раздела истории пакета (payload.history) несёт признак
 * assume_zero_when_missing и решение пользователя. При признаке сотрудникам
 * БЕЗ записи ставится явная нулевая запись на срез раздела: остатки месяца
 * 0, серий нет, последней смены нет. Тогда месяц среза у них судится так же,
 * как у сотрудников с историей (перенос отработанных суток 0), а не выпадает
 * из универсума формы «выходных в месяц» (до правки универсум был пуст и
 * решатель отказывал словами «ПУСТО» — дефект, найденный первым нажатием
 * на простом наборе Н0 без истории 05.09.2026).
 *
 * Без раздела или без признака ничего не меняется: null явно = истории нет,
 * ноль не подставляется, окна через границу не судятся (третий исход).
 */
object HistoryAssumption {

    /** Ставит нулевые записи и возвращает показание для статуса прогона;
     *  null — раздела истории в наборе нет. */
    fun applyZero(problem: FlightCrewSchedule): Map<String, Any?>? {
        val head = problem.history ?: return null
        val assume = head["assume_zero_when_missing"] == true
        val asOf = head["as_of"]?.toString()?.takeIf { it.isNotBlank() }
        val without = problem.employees.filter { it.history == null }
        var filled = 0
        if (assume && asOf != null) {
            for (e in without) {
                e.history = EmployeeHistory()
                e.historyAsOf = asOf
                e.historyAssumedZero = true
                filled++
            }
        }
        val decision = (head["user_decision"] as? Map<*, *>)?.get("decision")?.toString()
        return linkedMapOf(
            "asOf" to asOf,
            "assumeZeroWhenMissing" to assume,
            "userDecision" to decision,
            "employeesWithoutRecord" to without.size,
            "assumedZero" to filled,
            "basis" to when {
                !assume -> "признака assume_zero_when_missing нет — остатки сотрудников без " +
                    "записи неизвестны, окна через границу у них не судятся"
                asOf == null -> "признак assume_zero_when_missing есть, но среза (as_of) нет — " +
                    "нулевая запись не ставится"
                decision != null -> "решение пользователя $decision: у $filled сотрудников без " +
                    "записи остатки приняты нулевыми на срез $asOf"
                else -> "признак производителя assume_zero_when_missing: у $filled сотрудников " +
                    "без записи остатки приняты нулевыми на срез $asOf"
            })
    }
}
