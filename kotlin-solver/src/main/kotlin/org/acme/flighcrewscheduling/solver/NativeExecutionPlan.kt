package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.domain.solution.ConstraintWeightOverrides
import ai.timefold.solver.core.api.score.HardSoftScore

/** Semantic plan only, never customer admission. The normal TaskModes entry
 * checks its package/mode gates BEFORE calling this shared calculation. The
 * explicit local qualification harness may measure the same calculation on
 * native data, but cannot obtain a ResolvedTaskMode or register a package run.
 */
class NativeExecutionPlan internal constructor(
    val thresholds: RunThresholds.Resolution,
    val logic: LogicReport,
    val appliedWeights: Map<String, Long>,
    val effectiveWeights: Map<String, Long>,
    val weightOverrides: ConstraintWeightOverrides<HardSoftScore>?,
    val literalsDisabled: List<String>,
) {
    companion object {
        fun resolve(native: NativeModel, profile: String, useWeights: Boolean,
                    objective: ResolvedObjective? = null): NativeExecutionPlan {
            if (useWeights && native.componentsWithoutWeight.isNotEmpty()) {
                throw TaskModeRefused("native content has soft components without confirmed weights: " +
                    native.componentsWithoutWeight.joinToString(", "))
            }
            if (profile != TaskModes.PROFILE_DEFAULT && native.hardParams.isNotEmpty()) {
                throw TaskModeRefused(
                    "пакет несёт пороги жёстких правил, а профиль " + profile +
                    " их не применяет (читает ярус правил, не пакет): прогон по " +
                    "пакету с порогами — только профилем по умолчанию")
            }
            val thresholds = RunThresholds.resolveNative(native, profile)
            if (profile != TaskModes.PROFILE_DEFAULT && native.logic.any { it.ruleKind == "HARD" }) {
                throw TaskModeRefused(
                    "пакет несёт блоки logic жёстких правил, а профиль " + profile +
                    " их не исполняет: прогон по такому пакету — только профилем по умолчанию")
            }
            val logic = LogicCompiler.compileNative(native, thresholds, profile)
            LogicCompiler.refuseIfNeeded(logic)
            val effective = LinkedHashMap<String, Long>()
            if (useWeights) effective.putAll(native.weights)
            objective?.let { o ->
                val penaltyWeight = effective[o.constraint] ?: 0L
                effective[o.constraint] = try {
                    Math.addExact(penaltyWeight, o.weight)
                } catch (e: ArithmeticException) {
                    throw TaskModeRefused(
                        "целевая " + o.id + ": ставка rate " + o.weight +
                        " вместе с весом штрафа " + penaltyWeight +
                        " переполняет Long для ограничения " + o.constraint)
                }
            }
            val disabled = ArrayList<String>()
            if (profile == TaskModes.PROFILE_DEFAULT) {
                for (name in DefaultProviderRules.SOFT_CONSTRAINTS) {
                    if (name !in effective) {
                        effective[name] = 0L
                        disabled.add(name)
                    }
                }
            }
            val overrides = if (effective.isNotEmpty()) ConstraintWeightOverrides.of(
                effective.mapValues { HardSoftScore.ofSoft(it.value) }) else null
            return NativeExecutionPlan(thresholds, logic,
                if (useWeights) native.weights.toMap() else emptyMap(),
                effective, overrides, disabled)
        }
    }
}
