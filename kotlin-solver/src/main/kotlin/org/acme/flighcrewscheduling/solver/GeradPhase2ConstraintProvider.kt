package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.Constraint
import ai.timefold.solver.core.api.score.stream.ConstraintFactory
import ai.timefold.solver.core.api.score.stream.ConstraintProvider
import org.acme.flighcrewscheduling.domain.FlightAssignment

/**
 * GERAD/Kasirzadeh PHASE 2 profile: full core legality (all constraints of
 * [GeradConstraintProvider], by composition) plus the personalization terms
 * of the G-2014-22 dataset (rules 11-13 of kasirzadeh-scoping.md):
 *
 *   - uncovered flight:            10000 soft per unassigned slot
 *   - work on a vacation day:       1000 soft per assignment whose flight
 *                                   touches a requested vacation day
 *                                   (Employee.preferredDaysOff)
 *   - preferred flight flown:       -100 soft (reward) per assignment of a
 *                                   leg from Employee.preferredFlights
 *
 * Weights follow the dataset paper (G-2014-22); revisit against the
 * dataset README extraction before publishing quality comparisons.
 */
class GeradPhase2ConstraintProvider : ConstraintProvider {

    private val uncoveredPenalty = 10_000L
    private val vacationPenalty = 1_000L
    private val preferredBonus = 100L

    override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> =
        // coreConstraints (NOT defineConstraints): this profile's uncovered
        // semantics is the thesis' SOFT 10000/slot — the core hard
        // unassigned rule would make it dead code again (audit 2026-07-12).
        GeradConstraintProvider().coreConstraints(cf) + arrayOf(
            uncovered(cf),
            vacationDay(cf),
            preferredFlight(cf),
        )

    /** Every leg left without a crew member costs 10000 (soft, like the
     * set-covering uncovered-flight cost in the reference model). Also
     * closes the "partial roster looks better" measurement gotcha. */
    private fun uncovered(cf: ConstraintFactory): Constraint =
        cf.forEachIncludingUnassigned(FlightAssignment::class.java)
            .filter { it.employee == null }
            .penalize(HardSoftScore.ofSoft(uncoveredPenalty))
            .asConstraint("G2 Uncovered flight")

    /** Assignment overlapping a requested vacation day. */
    private fun vacationDay(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa ->
                val emp = fa.employee ?: return@filter false
                if (emp.preferredDaysOff.isEmpty()) return@filter false
                val dep = fa.flight!!.getDepartureUTCDate()
                val arr = fa.flight!!.arrivalUTCDateTime!!.toLocalDate()
                emp.preferredDaysOff.contains(dep)
                    || (arr != dep && emp.preferredDaysOff.contains(arr))
            }
            .penalize(HardSoftScore.ofSoft(vacationPenalty))
            .asConstraint("G2 Vacation day")

    /** Reward flying a leg the crew member asked for. */
    private fun preferredFlight(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa ->
                val emp = fa.employee ?: return@filter false
                emp.preferredFlights.isNotEmpty()
                    && emp.preferredFlights.contains(fa.flight!!.flightNumber)
            }
            .reward(HardSoftScore.ofSoft(preferredBonus))
            .asConstraint("G2 Preferred flight")
}
