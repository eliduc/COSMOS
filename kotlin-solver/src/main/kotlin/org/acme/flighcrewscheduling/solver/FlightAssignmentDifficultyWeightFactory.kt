package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.domain.common.ComparatorFactory
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule

/**
 * Difficulty comparator for FlightAssignment, used by FIRST_FIT_DECREASING (and
 * the other *_DECREASING construction heuristics) to place the HARDEST-to-staff
 * assignments first — into a still-empty schedule — so Local Search starts much
 * closer to feasibility.
 *
 * Timefold 2.0 expresses entity difficulty via @PlanningEntity(
 * comparatorFactoryClass = ...): a ComparatorFactory builds, from the solution,
 * a Comparator<FlightAssignment> that orders entities ascending = least → most
 * difficult. The *_DECREASING heuristics then assign from the most-difficult
 * end first.
 *
 * "Harder" = fewer employees can legally fill the slot. We score each slot by
 * the count of employees carrying BOTH the required skill AND a type rating for
 * the flight's aircraft; ties broken by flight duration (longer = harder), then
 * id for determinism.
 *
 * Only problem facts are read (requiredSkill, the flight's aircraft type, the
 * fixed employee pool) — never the planning variable (employee). The per-(skill,
 * type) count is cached so the comparator is O(1) per comparison.
 */
class FlightAssignmentDifficultyComparatorFactory :
    ComparatorFactory<FlightCrewSchedule, FlightAssignment> {

    override fun createComparator(
        schedule: FlightCrewSchedule,
    ): Comparator<FlightAssignment> {
        val countCache = HashMap<Pair<String, String>, Int>()

        fun qualifiedCount(a: FlightAssignment): Int {
            // Перегонное кресло может занять любой сотрудник (и может
            // никто): для конструктивной эвристики оно самое лёгкое.
            if (a.deadhead) return Int.MAX_VALUE
            val skill = a.requiredSkill
            val type = a.flight?.aircraftType ?: ""
            return countCache.getOrPut(skill to type) {
                schedule.employees.count { e ->
                    e.skills.contains(skill) &&
                        (type.isEmpty() || e.typeRatings.contains(type))
                }
            }
        }

        // Ascending difficulty: most-qualified / shortest first (least
        // difficult), fewest-qualified / longest last (most difficult). The
        // *_DECREASING heuristics place the most-difficult end first.
        return compareByDescending<FlightAssignment> { qualifiedCount(it) }
            .thenBy { it.flight?.getDurationMinutes() ?: 0 }
            .thenBy { it.id }
    }
}
