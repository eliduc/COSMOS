package org.acme.flighcrewscheduling.spp

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.Constraint
import ai.timefold.solver.core.api.score.stream.ConstraintCollectors
import ai.timefold.solver.core.api.score.stream.ConstraintFactory
import ai.timefold.solver.core.api.score.stream.ConstraintProvider
import ai.timefold.solver.core.api.score.stream.Joiners

/**
 * Exact-cover as score: every row covered by EXACTLY one selected column.
 *   hard: -1 per uncovered row; -(k-1) per row covered k>1 times
 *   soft: -cost per selected column
 * At hard=0 the soft equals the set-partitioning objective, directly
 * comparable to CP-SAT's proven optimum.
 */
class SppConstraintProvider : ConstraintProvider {

    override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> = arrayOf(
        uncoveredRow(cf),
        overCoveredRow(cf),
        totalCost(cf),
    )

    private fun uncoveredRow(cf: ConstraintFactory): Constraint =
        cf.forEach(SppRow::class.java)
            .ifNotExists(SppColumn::class.java,
                Joiners.filtering { row, col ->
                    col.selected == true && col.coversRow(row.id)
                })
            .penalize(HardSoftScore.ONE_HARD)
            .asConstraint("Uncovered row")

    private fun overCoveredRow(cf: ConstraintFactory): Constraint =
        cf.forEach(SppColumn::class.java)
            .filter { it.selected == true }
            .join(SppRow::class.java,
                Joiners.filtering { col, row -> col.coversRow(row.id) })
            .groupBy({ _, row -> row }, ConstraintCollectors.countBi())
            .filter { _, cnt -> cnt > 1 }
            .penalize(HardSoftScore.ONE_HARD) { _, cnt -> (cnt - 1).toLong() }
            .asConstraint("Over-covered row")

    private fun totalCost(cf: ConstraintFactory): Constraint =
        cf.forEach(SppColumn::class.java)
            .filter { it.selected == true }
            .penalize(HardSoftScore.ONE_SOFT) { col -> col.cost.toLong() }
            .asConstraint("Total cost")
}
