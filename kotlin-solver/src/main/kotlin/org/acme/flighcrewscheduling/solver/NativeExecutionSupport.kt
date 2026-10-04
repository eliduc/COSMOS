package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.domain.solution.ConstraintWeightOverrides
import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.impl.score.constraint.ConstraintMatchPolicy
import ai.timefold.solver.core.impl.solver.DefaultSolverFactory
import org.acme.flighcrewscheduling.domain.CalendarWindowRule
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule

/** Shared score-context operations, with no package reader, mode admission,
 * run registry, scheduling, thread creation or Solver.solve call. */
object NativeExecutionSupport {
    fun prepare(problem: FlightCrewSchedule,
                applied: Map<String, RunThresholds.Applied>,
                effectiveWeights: Map<String, Long>,
                overrides: ConstraintWeightOverrides<HardSoftScore>?,
                calendarWindows: List<CalendarWindowRule>,
                packaged: Boolean = true) {
        overrides?.let { problem.constraintWeightOverrides = it }
        problem.layoverCostWeights = effectiveWeights
        RosterLayoverCost.prepare(problem, applied, packaged = packaged)
        problem.calendarWindowRules = calendarWindows.toMutableList()
    }

    /** Same complete contribution map used by the production run-scoped
     * endpoint. It includes zero contributions; a missing executor must not
     * be invented as a zero. The caller owns and names the score subject. */
    fun breakdown(factory: SolverFactory<FlightCrewSchedule>,
                  problem: FlightCrewSchedule, fingerprint: String?,
                  plans: List<LogicPlan>): Map<String, Any?> {
        val sdf = (factory as DefaultSolverFactory<FlightCrewSchedule>)
            .getScoreDirectorFactory<HardSoftScore>()
        val director = sdf.createScoreDirectorBuilder()
            .withConstraintMatchPolicy(ConstraintMatchPolicy.ENABLED).build()
        val byName = java.util.TreeMap<String, Map<String, Any?>>()
        val score: HardSoftScore
        try {
            director.setWorkingSolution(problem)
            score = director.calculateScore().raw()
            for ((ref, total) in director.constraintMatchTotalMap) {
                val s = total.score
                byName[ref.id()] = linkedMapOf(
                    "score" to s.toString(), "hard" to s.hardScore(),
                    "soft" to s.softScore(), "matches" to total.constraintMatchCount,
                    "weight" to total.constraintWeight.toString())
            }
        } finally {
            director.close()
        }
        return linkedMapOf(
            "score" to score.toString(), "hard" to score.hardScore(),
            "soft" to score.softScore(), "packageFingerprint" to fingerprint,
            "generated" to plans.map { it.constraintId }, "constraints" to byName)
    }
}
