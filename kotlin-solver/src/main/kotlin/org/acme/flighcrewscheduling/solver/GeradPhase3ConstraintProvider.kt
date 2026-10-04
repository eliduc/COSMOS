package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.Constraint
import ai.timefold.solver.core.api.score.stream.ConstraintFactory
import ai.timefold.solver.core.api.score.stream.ConstraintProvider
import ai.timefold.solver.core.api.score.stream.Joiners
import org.acme.flighcrewscheduling.domain.FlightAssignment
import java.time.temporal.ChronoUnit

/**
 * GERAD phase 3, experiment 1 (rules=GERAD3): the DIRECT config's profile
 * (full GERAD2 personalization stack, by composition) plus an
 * INTEGRATED-OBJECTIVE structure term — the user's "сквозная задача" idea.
 *
 * Rationale: the pairing-structure cost of a roster is
 *   sum over chains of [span - 200*(legs-1)]
 *     = const(total flight time) + sum over in-chain adjacent pairs of
 *       (gap - 200),
 * so minimizing it is equivalent to REWARDING adjacent same-employee legs
 * with a tight connection: every pair with gap in [60, 200) earns
 * (200 - gap). This is a proxy (ignores loop closure and the 5-leg cap);
 * the TRUE structure cost of the resulting roster is measured afterwards
 * by the DP segmentation evaluator — the experiment's metric is whether
 * this term closes the measured structure-cost gap between TF and
 * TF+OR-T+CG, and what it costs on the personalization axes.
 */
class GeradPhase3ConstraintProvider : ConstraintProvider {

    private val discount = 200
    private val minSit = 60

    override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> =
        GeradPhase2ConstraintProvider().defineConstraints(cf) + arrayOf(
            chainCompactness(cf),
        )

    /** Reward tight legal turnarounds — the LS-friendly equivalent of
     * minimizing the implied pairing-structure cost. */
    private fun chainCompactness(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .join(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::employee),
                Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
                Joiners.filtering { fa1, fa2 ->
                    fa1.id != fa2.id
                        && !fa2.flight!!.departureUTCDateTime!!
                            .isBefore(fa1.flight!!.arrivalUTCDateTime!!)
                })
            .ifNotExists(FlightAssignment::class.java,
                Joiners.equal({ fa1, _ -> fa1.employee },
                              FlightAssignment::employee),
                Joiners.filtering { fa1, fa2, other ->
                    other.id != fa1.id && other.id != fa2.id
                        && fa1.getDepartureUTCDateTime()
                            .isBefore(other.getDepartureUTCDateTime())
                        && other.getDepartureUTCDateTime()
                            .isBefore(fa2.getDepartureUTCDateTime())
                })
            .filter { fa1, fa2 ->
                val gap = ChronoUnit.MINUTES.between(
                    fa1.flight!!.arrivalUTCDateTime,
                    fa2.flight!!.departureUTCDateTime)
                gap in minSit until discount.toLong()
            }
            .reward(HardSoftScore.ONE_SOFT) { fa1, fa2 ->
                discount - ChronoUnit.MINUTES.between(
                    fa1.flight!!.arrivalUTCDateTime,
                    fa2.flight!!.departureUTCDateTime)
            }
            .asConstraint("G3 Chain compactness")
}
