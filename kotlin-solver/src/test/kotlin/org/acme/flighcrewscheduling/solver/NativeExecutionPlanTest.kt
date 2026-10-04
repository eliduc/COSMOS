package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import org.acme.flighcrewscheduling.domain.AccountingWindow
import org.acme.flighcrewscheduling.domain.CalendarWindowRule
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.math.BigDecimal
import java.time.DayOfWeek

/** Pure resolution and score-only refactor controls. Never calls solve. */
class NativeExecutionPlanTest {
    private fun native(weights: Map<String, Long> = emptyMap(), params: List<RuleParam> = emptyList(),
                       missing: List<String> = emptyList()) = DecodedNativeModel(
        ruleParams = emptyMap(), objectiveDetails = emptyMap(), hardParams = params,
        weights = weights, weightRules = emptyMap(), unmappedRules = emptyList(),
        componentsWithoutWeight = missing, calendarWindows = emptyList(), logic = emptyList(),
        logicUnproven = emptyMap(), objectiveToConstraint = null, dutyExtensionCapMin = null,
        dutyExtensionRules = emptyList(), workloadSpreadThresholdMin = null,
        workloadSpreadRules = emptyList(), dutyExtensionPolicies = emptyList(), landingArchiveOnly = false)

    @AfterEach fun cleanup() { RunThresholds.clearForTests(); LastProviderReadings.clearForTests() }

    @Test fun nativePlanHasNoCustomerAdmissionType() {
        val p = NativeExecutionPlan.resolve(native(), TaskModes.PROFILE_DEFAULT, true)
        assertFalse(ResolvedTaskMode::class.java.isInstance(p))
        assertFalse(LoadedTaskPackage::class.java.isInstance(p))
        assertTrue(p.appliedWeights.isEmpty())
        assertEquals(DefaultProviderRules.SOFT_CONSTRAINTS.toSet(), p.literalsDisabled.toSet())
        assertTrue(p.effectiveWeights.values.all { it == 0L })
    }

    @Test fun softWeightsAndObjectiveShareTheOriginalOverflowCheckedLaw() {
        val c = "Credit hours spread over threshold"
        val objective = ResolvedObjective("O", "objective", null, "minimize", c, "fixture", null, true, 7)
        val p = NativeExecutionPlan.resolve(native(mapOf(c to 13L)), TaskModes.PROFILE_DEFAULT, true, objective)
        assertEquals(13L, p.appliedWeights[c])
        assertEquals(20L, p.effectiveWeights[c])
        assertFalse(c in p.literalsDisabled)
        assertTrue(p.effectiveWeights.filterKeys { it != c }.values.all { it == 0L })
        assertThrows<TaskModeRefused> {
            NativeExecutionPlan.resolve(native(mapOf(c to Long.MAX_VALUE)), TaskModes.PROFILE_DEFAULT, true, objective)
        }
    }

    @Test fun hardFeasibilityStillDisablesAllSoftPenalties() {
        val c = "Calendar window layover penalty"
        val p = NativeExecutionPlan.resolve(native(mapOf(c to 5L)), TaskModes.PROFILE_DEFAULT, false)
        assertTrue(p.appliedWeights.isEmpty())
        assertEquals(0L, p.effectiveWeights[c])
    }

    @Test fun missingSoftWeightCannotBecomeDefaultWeight() {
        assertThrows<TaskModeRefused> {
            NativeExecutionPlan.resolve(native(missing = listOf("R")), TaskModes.PROFILE_DEFAULT, true)
        }
        NativeExecutionPlan.resolve(native(missing = listOf("R")), TaskModes.PROFILE_DEFAULT, false)
    }

    @Test fun wrongProfileStillRefusesNativeThresholds() {
        val p = RuleParam("max_duty_duration", 780.0, "780", "duration_minutes", "max", null, "780", "R", "HARD")
        assertThrows<TaskModeRefused> { NativeExecutionPlan.resolve(native(params = listOf(p)), "GERAD", true) }
    }

    @Test fun ordinaryAdmissionRefusesBeforeAttemptingTheSharedNativePlan() {
        val invalid = RuleParam("max_duty_duration", -1.0, "-1", "wrong", "max", null, "invalid", "R", "HARD")
        val ordinary = LoadedTaskPackage(fingerprint = "synthetic-normal-package", specVersion = "1.21",
            customer = "unit", sourceRun = "unit", revision = 1, readiness = "not-admitted",
            allowedModes = emptyMap(), objectives = emptyMap(), objectiveDetails = emptyMap(),
            hardParams = listOf(invalid), weights = emptyMap(), unmappedRules = emptyList(),
            componentsWithoutWeight = emptyList(), hasData = true)
        val refusal = assertThrows<TaskModeRefused> { TaskModes.resolve("feasibility-soft", ordinary, "rostering") }
        assertTrue(refusal.message.orEmpty().contains("allowed_modes="))
        assertThrows<TaskModeRefused> { NativeExecutionPlan.resolve(ordinary, TaskModes.PROFILE_DEFAULT, true) }
    }

    @Test fun qualificationCannotSilentlyDropUnmappedRulesOrObjectives() {
        assertThrows<IllegalArgumentException> {
            A06QualificationRunProbe.nativePlan(native().copy(unmappedRules = listOf("R-NO-EXECUTOR")))
        }
        assertThrows<IllegalArgumentException> {
            A06QualificationRunProbe.nativePlan(native().copy(objectiveDetails = mapOf("rostering" to listOf(
                PackageObjective("O", "objective", "source", "minimize", "credit_hours_spread")))))
        }
        val ignored = RuleParam("unknown_native_parameter", 1.0, "1", "count", "min", null, "1", "R", "HARD")
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.nativePlan(native(params = listOf(ignored))) }
    }

    private fun metricObjective() = PackageObjective(A06QualificationRunProbe.METRIC_OBJECTIVE,
        "declared spread objective", "source text is not a numeric rate", "minimize", "credit_hours_spread", BigDecimal.ONE)

    @Test fun qualificationMetricUsesExplicitParsedObjectiveAndSeparatePenaltyWeights() {
        val obj = metricObjective()
        val content = native(weights = mapOf("Credit hours spread over threshold" to 13L))
            .copy(objectiveDetails = mapOf("rostering" to listOf(obj)))
        val selected = A06QualificationRunProbe.nativePlan(content, MODE_OPTIMIZATION, obj.id)
        val resolved = requireNotNull(selected.objective)
        assertEquals("Credit hours spread", resolved.constraint)
        assertEquals("словарь метрик §5а", resolved.executorSource)
        assertFalse(resolved.sharesConstraintWithPenalty)
        assertEquals(1L, resolved.weight)
        assertEquals(BigDecimal.ONE, resolved.rate)
        assertEquals(13L, selected.plan.appliedWeights["Credit hours spread over threshold"])
        assertEquals(13L, selected.plan.effectiveWeights["Credit hours spread over threshold"])
        assertEquals(1L, selected.plan.effectiveWeights["Credit hours spread"])
        assertEquals("objective.json: rostering.objectives[${obj.id}].rate.value", resolved.weightSource)
        assertEquals(setOf("id", "title", "metric", "direction", "constraint", "executorSource", "weight",
            "rate", "weightSource", "approximation", "sharesConstraintWithPenalty", "criterion"), resolved.snapshot().keys)
    }

    @Test fun qualificationMetricCannotUseLegacyRateAnotherObjectiveOrAnotherZone() {
        val obj = metricObjective()
        fun content(value: PackageObjective) = native().copy(objectiveDetails = mapOf("rostering" to listOf(value)))
        for (bad in listOf(obj.copy(rate = null), obj.copy(rate = BigDecimal("2")),
            obj.copy(id = "O-other"), obj.copy(metric = "uncovered_seats"))) {
            assertThrows<IllegalArgumentException> { A06QualificationRunProbe.nativePlan(content(bad), MODE_OPTIMIZATION, obj.id) }
        }
        assertThrows<TaskModeRefused> {
            A06QualificationRunProbe.nativePlan(content(obj.copy(direction = "maximize")), MODE_OPTIMIZATION, obj.id)
        }
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.nativePlan(native(), MODE_OPTIMIZATION, obj.id) }
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.nativePlan(content(obj), MODE_OPTIMIZATION, null) }
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.nativePlan(content(obj), MODE_FEASIBILITY_SOFT, obj.id) }
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.nativePlan(content(obj), MODE_FEASIBILITY_HARD, null) }
        for (objectives in listOf(mapOf("pairing" to listOf(obj)),
            mapOf("rostering" to listOf(obj, obj.copy(id = "O-extra"))),
            mapOf("rostering" to listOf(obj), "pairing" to listOf(obj.copy(id = "O-extra"))))) {
            assertThrows<IllegalArgumentException> {
                A06QualificationRunProbe.nativePlan(native().copy(objectiveDetails = objectives), MODE_OPTIMIZATION, obj.id)
            }
        }
    }

    @Test fun ordinaryObjectiveAdmissionStillPrecedesTheSharedSemanticResolver() {
        val obj = metricObjective()
        val ordinary = LoadedTaskPackage(fingerprint = "unit-native-objective", specVersion = "1.21",
            customer = "unit", sourceRun = "unit", revision = 1, readiness = "unit",
            allowedModes = mapOf("rostering" to listOf(MODE_OPTIMIZATION, MODE_FEASIBILITY_SOFT)),
            objectives = mapOf("rostering" to listOf(obj.id)), objectiveDetails = mapOf("rostering" to listOf(obj)),
            hardParams = emptyList(), weights = emptyMap(), unmappedRules = emptyList(),
            componentsWithoutWeight = emptyList(), hasData = true)
        for (id in listOf(null, "", "O-not-declared")) {
            assertThrows<TaskModeRefused> { TaskModes.resolve(MODE_OPTIMIZATION, ordinary, "rostering", id) }
        }
        val undeclared = ordinary.copy(objectives = emptyMap())
        assertTrue(assertThrows<TaskModeRefused> {
            TaskModes.resolve(MODE_OPTIMIZATION, undeclared, "rostering", obj.id)
        }.message.orEmpty().contains("не объявлена"))
        assertTrue(assertThrows<TaskModeRefused> {
            TaskModes.resolve(MODE_OPTIMIZATION, ordinary.copy(objectiveDetails = emptyMap()), "rostering", obj.id)
        }.message.orEmpty().contains("нет в objective.json"))
        assertTrue(assertThrows<TaskModeRefused> {
            TaskModes.resolve(MODE_OPTIMIZATION, ordinary.copy(allowedModes = emptyMap()), "rostering", obj.id)
        }.message.orEmpty().contains("allowed_modes="))
        val approved = TaskModes.resolve(MODE_OPTIMIZATION, ordinary, "rostering", obj.id)
        assertEquals(TaskModes.resolveNativeObjective(obj, ordinary, "rostering"), approved.objective)
        assertEquals(approved.objective!!.snapshot(), LastRun.snapshotOf(approved)["objective"])
        val soft = TaskModes.resolve(MODE_FEASIBILITY_SOFT, ordinary, "rostering")
        assertNull(soft.objective)
        assertNull(LastRun.snapshotOf(soft)["objective"])
    }

    @Test fun nativeObjectiveKeepsProductionDirectionRateAndMappingRefusals() {
        val obj = metricObjective()
        for (rate in listOf(BigDecimal("0"), BigDecimal("-1"), BigDecimal("1.5"),
            BigDecimal(Long.MAX_VALUE).add(BigDecimal.ONE))) {
            assertThrows<TaskModeRefused> { TaskModes.resolveNativeObjective(obj.copy(rate = rate), native(), "rostering") }
        }
        for (direction in listOf("maximize", "unknown")) {
            assertThrows<TaskModeRefused> { TaskModes.resolveNativeObjective(obj.copy(direction = direction), native(), "rostering") }
        }
        assertThrows<TaskModeRefused> { TaskModes.resolveNativeObjective(obj, native(), "pairing") }
        assertThrows<TaskModeRefused> { TaskModes.resolveNativeObjective(obj.copy(metric = "unknown"), native(), "rostering") }
        assertThrows<TaskModeRefused> { TaskModes.resolveNativeObjective(obj.copy(metric = null), native(), "rostering") }
        val table = native().copy(objectiveToConstraint = mapOf(obj.id to "Credit hours spread"))
        for (metric in listOf(null, "unknown")) {
            val resolved = TaskModes.resolveNativeObjective(obj.copy(metric = metric, rate = BigDecimal("3")), table, "rostering")
            assertEquals(3L, resolved.weight)
            assertTrue(resolved.executorSource.startsWith("карта пакета rule_maps"))
        }
        // Legacy ordinary semantics are retained, but qualification above refuses this carrier.
        assertEquals(1L, TaskModes.resolveNativeObjective(obj.copy(rate = null), native(), "rostering").weight)
    }

    @Test fun spreadMetricRetainsOneZeroImpactMatchAndDoesNotBecomeThresholdPenalty() {
        val objective = metricObjective()
        val content = native(weights = mapOf("Credit hours spread over threshold" to 1L))
            .copy(objectiveDetails = mapOf("rostering" to listOf(objective)),
                workloadSpreadThresholdMin = 300, workloadSpreadRules = listOf("R-spread"))
        val selected = A06QualificationRunProbe.nativePlan(content, MODE_OPTIMIZATION, objective.id)
        val plan = selected.plan
        fun schedule(healthy: Boolean): FlightCrewSchedule {
            val home = airport("ST1"); val out = airport("ST2")
            val a = employee("P1").apply { homeAirport = home }
            val b = employee("P2").apply { homeAirport = home }
            val f1 = flight("F1", home, t(1, 8), out, t(1, 13))
            val f2 = flight("F2", out, t(2, 8), home, t(2, 13))
            return Fixtures.schedule(listOf(home, out), listOf(a, b), listOf(
                assignment("F1#0", f1, a), assignment("F2#0", f2, if (healthy) b else a)))
        }
        val cfg = SolverConfig().withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        val factory = RunThresholds.withRunModel(plan.thresholds.applied, plan.logic.plans, packaged = true) {
            SolverFactory.create<FlightCrewSchedule>(cfg)
        }
        for (healthy in listOf(false, true)) {
            val p = schedule(healthy)
            NativeExecutionSupport.prepare(p, plan.thresholds.applied, plan.effectiveWeights, plan.weightOverrides, emptyList())
            val all = NativeExecutionSupport.breakdown(factory, p, "unit-source-world", plan.logic.plans)["constraints"] as Map<*, *>
            val metric = all["Credit hours spread"] as Map<*, *>
            val penalty = all["Credit hours spread over threshold"] as Map<*, *>
            // Single loadBalance tuple remains in ENABLED match totals at zero impact.
            // This law is fixed before catalog qualification measurements, not tuned to them.
            assertEquals(if (healthy) 0L else -7L, metric["soft"])
            assertEquals(1, metric["matches"])
            assertEquals(if (healthy) 0L else -2L, penalty["soft"])
            assertEquals(1, penalty["matches"])
        }
    }

    @Test fun sharedBreakdownMeasuresNativeDutyAndHealthyControlWithoutSearch() {
        val param = RuleParam("max_duty_duration", 780.0, "780", "duration_minutes", "max", null, "780", "R", "HARD")
        val plan = NativeExecutionPlan.resolve(native(params = listOf(param)), TaskModes.PROFILE_DEFAULT, true)
        val home = airport("ST1"); val out = airport("ST2")
        fun problem(endHour: Int): FlightCrewSchedule {
            val person = employee("P1").apply { homeAirport = home }
            val f = flight("F1", home, t(1, 6), out, t(1, endHour))
            return Fixtures.schedule(listOf(home, out), listOf(person), listOf(assignment("F1#0", f, person)))
        }
        val cfg = SolverConfig().withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        val factory = RunThresholds.withRunModel(plan.thresholds.applied, plan.logic.plans, packaged = true) {
            SolverFactory.create<FlightCrewSchedule>(cfg)
        }
        fun score(hour: Int): Map<*, *> {
            val p = problem(hour)
            NativeExecutionSupport.prepare(p, plan.thresholds.applied, plan.effectiveWeights, plan.weightOverrides, emptyList())
            val result = NativeExecutionSupport.breakdown(factory, p, "actual-input-fingerprint", plan.logic.plans)
            assertEquals("actual-input-fingerprint", result["packageFingerprint"])
            return (result["constraints"] as Map<*, *>)["Max duty period (rest-segmented)"] as Map<*, *>
        }
        assertEquals(-600L, score(21)["hard"])
        assertEquals(1, score(21)["matches"])
        assertEquals(0L, score(19)["hard"])
        assertEquals(0, score(19)["matches"])
    }

    @Test fun resourcePrepareMatchesLegacyOrderAndIsIdempotentWithoutExtendingEitherHorizon() {
        fun rest(name: String, value: Int) = RuleParam(name, value.toDouble(), "$value",
            "duration_minutes", "min", null, "$value", "R-$name", "HARD")
        val plan = NativeExecutionPlan.resolve(native(
            weights = mapOf("Layover nights away from home" to 13L, "Calendar window layover penalty" to 7L),
            params = listOf(rest("min_rest_home", 660), rest("min_rest_outstation", 700))),
            TaskModes.PROFILE_DEFAULT, true)
        val calendar = listOf(CalendarWindowRule("R-window", "layover_penalty", setOf(DayOfWeek.FRIDAY)))
        fun problem(declared: Boolean): FlightCrewSchedule {
            val home = airport("ST1"); val away = airport("ST2")
            val person = employee("P1").apply { homeAirport = home }
            val idle = employee("P2").apply { homeAirport = home }
            return Fixtures.schedule(listOf(home, away), listOf(person, idle), listOf(
                assignment("F1#0", flight("F1", home, t(6, 16), away, t(6, 18)), person),
                assignment("F2#0", flight("F2", away, t(7, 3), home, t(7, 5)), person),
                assignment("F3#0", flight("F3", home, t(7, 23, 30), away, t(8, 1, 10)), person)))
                .apply {
                    if (declared) windowHorizon = AccountingWindow().apply {
                        start_inclusive = t(1, 0).toString(); end_exclusive = t(8, 0).toString()
                    }
                    // Simulate a deserialized or previously prepared result with stale context.
                    layoverCostWeights = mapOf("Layover nights away from home" to 99L)
                    employees.forEach { it.layoverAccountingStart = t(3, 0); it.layoverAccountingEnd = t(30, 0) }
                }
        }
        val cfg = SolverConfig().withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        val factory = RunThresholds.withRunModel(plan.thresholds.applied, plan.logic.plans, packaged = true) {
            SolverFactory.create<FlightCrewSchedule>(cfg)
        }
        for (declared in listOf(false, true)) {
            val legacy = problem(declared)
            // Exact previous packagedRunBreakdown -> breakdownWith preparation order.
            plan.weightOverrides?.let { legacy.constraintWeightOverrides = it }
            legacy.layoverCostWeights = plan.effectiveWeights
            legacy.calendarWindowRules = calendar.toMutableList()
            RosterLayoverCost.prepare(legacy, plan.thresholds.applied, packaged = true)

            val shared = problem(declared)
            val horizon = shared.windowHorizon
            NativeExecutionSupport.prepare(shared, plan.thresholds.applied, plan.effectiveWeights,
                plan.weightOverrides, calendar)
            // breakdownWith still prepares the same native context again.
            RosterLayoverCost.prepare(shared, plan.thresholds.applied, packaged = true)
            val report = RosterLayoverCost.report(shared)
            assertEquals(RosterLayoverCost.report(legacy), report)
            assertEquals(0L, report["known_nights"])
            assertEquals(660, shared.layoverDutyRules.minRestHome)
            assertEquals(700, shared.layoverDutyRules.minRestOut)
            assertEquals(calendar, shared.calendarWindowRules)
            shared.employees.forEach {
                assertEquals(if (declared) t(1, 0) else t(6, 0), it.layoverAccountingStart)
                assertEquals(t(8, 0), it.layoverAccountingEnd)
            }
            assertSame(horizon, shared.windowHorizon)
            assertEquals(NativeExecutionSupport.breakdown(factory, legacy, "same-input", plan.logic.plans),
                NativeExecutionSupport.breakdown(factory, shared, "same-input", plan.logic.plans))
            repeat(2) {
                NativeExecutionSupport.prepare(shared, plan.thresholds.applied, plan.effectiveWeights,
                    plan.weightOverrides, calendar)
            }
            assertEquals(report, RosterLayoverCost.report(shared))
            assertSame(horizon, shared.windowHorizon)
            assertEquals(calendar, shared.calendarWindowRules)

            // A wrong/profile threshold does reach this mechanism: 9h splits at 8h,
            // while the exact packaged 700-minute rest keeps it one normative duty.
            val wrongRest = RunThresholds.resolve(listOf(rest("min_rest_home", 660),
                rest("min_rest_outstation", 480)), TaskModes.PROFILE_DEFAULT).applied
            RosterLayoverCost.prepare(shared, wrongRest, packaged = true)
            assertEquals(1L, RosterLayoverCost.report(shared)["known_nights"])
        }
    }
}
