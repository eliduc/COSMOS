package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule

/**
 * Перегонные кресла (этап Г): порождение мест ПАССАЖИРА из порога пакета.
 *
 * Возможность включается ТОЛЬКО порогом `max_deadheads_per_flight` из
 * пакета постановки (без пакета — ноль кресел, счёт прогонов прежний
 * байт-в-байт: пустой перегон не входит ни в одно ограничение). На
 * каждый рейс набора добавляется ровно k необязательных кресел с
 * `deadhead = true`: предел «перегонов на рейс» держится самой
 * структурой — ходов больше, чем кресел, не существует.
 *
 * Порождение идёт ПОСЛЕ проверки фактов набора (состав мест — предмет
 * НАБОРА, перегонные кресла решателя в него не входят) и идемпотентно:
 * решение, уже несущее перегонные кресла (повторный счёт выданного
 * ростера), не получает вторых.
 */
object DeadheadSeats {

    /** Число мест состава на рейс (максимум по рейсам набора) — умолчание
     *  перегонных кресел, когда ВМП о перегонах молчит. */
    fun crewSizeOf(problem: FlightCrewSchedule): Int =
        problem.flightAssignments.filter { !it.deadhead }
            .groupBy { it.flight?.flightLegId ?: it.flight?.flightNumber ?: "" }
            .values.maxOfOrNull { it.size } ?: 0

    /**
     * Порождает кресла в задаче; возвращает, сколько добавлено.
     *
     * Решение владельца 05.09.2026 (единый план, раздел 3): отсутствие в
     * ВМП семьи deadhead_rules — отсутствие ограничения, а не запрет
     * («ничего сверх ВМП»; VN-006 H0 описывает мир с перелётами
     * пассажиром). В пакетном прогоне без порога перегоны РАЗРЕШЕНЫ:
     * кресел на рейс — явный параметр прогона [explicitSeats], без него —
     * размер состава экипажа; это допущение решателя, не правило, и оно
     * называется показанием в статусе прогона и сертификате. Порог
     * пакета (семья deadhead_rules), когда он есть, действует как прежде.
     * Непакетный путь (без пакета) умолчание не меняет: [allowByDefault]
     * = false → ноль кресел, как раньше.
     */
    fun ensure(problem: FlightCrewSchedule,
               applied: Map<String, RunThresholds.Applied>,
               explicitSeats: Int? = null,
               allowByDefault: Boolean = false): Int {
        val perFlight = applied["max_deadheads_per_flight"]?.value
            ?: explicitSeats
            ?: (if (allowByDefault) crewSizeOf(problem) else DefaultProviderRules.DEADHEADS_OFF)
        if (perFlight <= 0) return 0
        if (problem.flightAssignments.any { it.deadhead }) return 0
        var added = 0
        for (f in problem.flights) {
            for (i in 1..perFlight) {
                val seat = FlightAssignment(
                    "DH-" + f.flightLegId + "-" + i, f,
                    // Вне полосы мест состава: перегон составом не судится.
                    100 + i, "")
                seat.crewSlotId = seat.id
                seat.deadhead = true
                problem.flightAssignments.add(seat)
                added++
            }
        }
        return added
    }
}
