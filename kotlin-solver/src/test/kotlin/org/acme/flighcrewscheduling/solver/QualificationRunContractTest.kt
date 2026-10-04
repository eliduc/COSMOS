package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.config.constructionheuristic.ConstructionHeuristicPhaseConfig
import ai.timefold.solver.core.config.heuristic.selector.common.SelectionOrder
import ai.timefold.solver.core.config.heuristic.selector.move.generic.ChangeMoveSelectorConfig
import ai.timefold.solver.core.config.localsearch.LocalSearchPhaseConfig
import ai.timefold.solver.core.config.localsearch.decider.acceptor.AcceptorType
import ai.timefold.solver.core.config.localsearch.decider.forager.LocalSearchPickEarlyType
import ai.timefold.solver.core.config.solver.SolverConfig
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.Duration

/** Structural tests only: do NOT invoke the explicit qualification test or solve. */
class QualificationRunContractTest {
    private val mapper = ObjectMapper()
    private val challenge = "29f91d61-999b-412e-a1e4-c22da9ce1b93"
    private fun request(): ObjectNode = mapper.readTree("""{
      "schema":"a06-qualification-request/2","scope":"catalog-executor-qualification/local-only",
      "catalog":{"version":"0.8.0","fingerprint":"${"a".repeat(64)}"},"challenge":"$challenge",
      "limits":{"spent_limit_ms":1000,"step_limit":1,"seed":0,"max_tasks":2},"cases":[
      {"record":"families/crew_composition","task":"st-crew-1","role":"violation","control_for":null,
       "executor_kind":"kotlin_constraint","mode":"feasibility-soft","objective_id":null,
       "component_zip":"st-crew-1.zip","sha256":"${"b".repeat(64)}"},
      {"record":"families/crew_composition","task":"st-crew-healthy-1","role":"healthy","control_for":"st-crew-1",
       "executor_kind":"kotlin_constraint","mode":"feasibility-soft","objective_id":null,
       "component_zip":"st-crew-healthy-1.zip","sha256":"${"c".repeat(64)}"}]}""") as ObjectNode
    private fun parse(doc: JsonNode) = mapper.writeValueAsBytes(doc).let {
        A06QualificationRunProbe.parseRequest(it, A06QualificationRunProbe.sha(it), challenge)
    }
    @Test fun explicitPinnedPairIsAcceptedAsRequestNotAcceptance() {
        val value = parse(request())
        assertEquals(listOf("st-crew-1", "st-crew-healthy-1"), value.cases.map { it.task })
        assertEquals(challenge, value.challenge)
        assertTrue(value.cases.all { it.executorKind == "kotlin_constraint" &&
            it.mode == MODE_FEASIBILITY_SOFT && it.objectiveId == null })
    }
    private fun metricRequest() = request().also { doc ->
        doc.path("cases").forEachIndexed { i, case ->
            val task = if (i == 0) "st-chs-1" else "st-chs-healthy-1"
            (case as ObjectNode).put("record", "criteria/credit_hours_spread")
                .put("task", task).put("component_zip", "$task.zip")
                .put("executor_kind", "kotlin_metric").put("mode", MODE_OPTIMIZATION)
                .put("objective_id", "O-STAND-credit_hours_spread")
            if (i == 1) case.put("control_for", "st-chs-1")
        }
    }
    @Test fun metricPairHasExplicitExecutorModeAndObjective() {
        val value = parse(metricRequest())
        assertEquals(listOf("st-chs-1", "st-chs-healthy-1"), value.cases.map { it.task })
        assertTrue(value.cases.all { it.executorKind == "kotlin_metric" &&
            it.mode == MODE_OPTIMIZATION && it.objectiveId == "O-STAND-credit_hours_spread" })
    }
    @Test fun executorModeAndObjectiveCannotBeRelabeledOrOmitted() {
        for (base in listOf(request(), metricRequest())) {
            for (key in listOf("executor_kind", "mode", "objective_id")) {
                val omitted = base.deepCopy(); (omitted.path("cases")[0] as ObjectNode).remove(key)
                assertThrows<IllegalArgumentException> { parse(omitted) }
            }
            for ((key, value) in listOf("executor_kind" to "live", "mode" to MODE_FEASIBILITY_HARD,
                "objective_id" to "O-unreviewed")) {
                val bad = base.deepCopy(); (bad.path("cases")[0] as ObjectNode).put(key, value)
                assertThrows<IllegalArgumentException> { parse(bad) }
            }
        }
        val dropped = metricRequest(); (dropped.path("cases")[0] as ObjectNode).putNull("objective_id")
        assertThrows<IllegalArgumentException> { parse(dropped) }
        val wrongKind = metricRequest(); (wrongKind.path("cases")[0] as ObjectNode).put("executor_kind", "kotlin_constraint")
        assertThrows<IllegalArgumentException> { parse(wrongKind) }
        val wrongMode = request(); (wrongMode.path("cases")[0] as ObjectNode).put("mode", MODE_OPTIMIZATION)
        assertThrows<IllegalArgumentException> { parse(wrongMode) }
    }
    @Test fun schemaV1CannotBePromotedToMetricQualification() {
        assertThrows<IllegalArgumentException> { parse(request().put("schema", "a06-qualification-request/1")) }
        assertThrows<IllegalArgumentException> { parse(metricRequest().put("schema", "a06-qualification-request/1")) }
    }
    @Test fun allEightFinitePairsFitTheSixteenCaseCeiling() {
        val full = request()
        val cases = mapper.createArrayNode()
        A06QualificationRunProbe.PAIRS.forEach { (record, stem) ->
            val base = if (record == A06QualificationRunProbe.METRIC_RECORD) metricRequest() else request()
            base.path("cases").forEachIndexed { i, row ->
                val task = if (i == 0) "st-$stem-1" else "st-$stem-healthy-1"
                (row as ObjectNode).put("record", record).put("task", task).put("component_zip", "$task.zip")
                if (i == 1) row.put("control_for", "st-$stem-1")
                cases.add(row)
            }
        }
        full.set<JsonNode>("cases", cases)
        (full.path("limits") as ObjectNode).put("max_tasks", 16)
        assertEquals(16, parse(full).cases.size)
        cases.addAll(request().path("cases") as com.fasterxml.jackson.databind.node.ArrayNode)
        (full.path("limits") as ObjectNode).put("max_tasks", 18)
        assertThrows<IllegalArgumentException> { parse(full) }
    }
    @Test fun unavailabilityRequiresItsOwnDeclaredNativePair() {
        val doc = request()
        doc.path("cases").forEachIndexed { i, row ->
            val task = if (i == 0) "st-unavail-native-1" else "st-unavail-native-healthy-1"
            (row as ObjectNode).put("record", "families/crew_unavailability")
                .put("task", task).put("component_zip", "$task.zip")
            if (i == 1) row.put("control_for", "st-unavail-native-1")
        }
        assertEquals(listOf("st-unavail-native-1", "st-unavail-native-healthy-1"), parse(doc).cases.map { it.task })
        val old = doc.deepCopy()
        (old.path("cases")[0] as ObjectNode).put("task", "st-unavail-1")
            .put("component_zip", "st-unavail-1.zip")
        assertThrows<IllegalArgumentException> { parse(old) }
        val relabeled = doc.deepCopy()
        (relabeled.path("cases")[1] as ObjectNode).put("control_for", "st-crew-1")
        assertThrows<IllegalArgumentException> { parse(relabeled) }
    }
    @Test fun spreadQualificationRequiresTheAdditiveNativePairNotTheOriginalWorlds() {
        fun spreadRequest(stem: String) = request().also { doc ->
            doc.path("cases").forEachIndexed { i, case ->
                val task = if (i == 0) "st-$stem-1" else "st-$stem-healthy-1"
                (case as ObjectNode).put("record", "families/balanced_workload")
                    .put("task", task).put("component_zip", "$task.zip")
                if (i == 1) case.put("control_for", "st-$stem-1")
            }
        }
        val value = parse(spreadRequest("spread-native"))
        assertEquals(listOf("st-spread-native-1", "st-spread-native-healthy-1"), value.cases.map { it.task })
        assertEquals("st-spread-native-1", value.cases[1].controlFor)
        assertThrows<IllegalArgumentException> { parse(spreadRequest("spread")) }
        val mixed = spreadRequest("spread-native")
        (mixed.path("cases")[1] as ObjectNode).put("control_for", "st-spread-1")
        assertThrows<IllegalArgumentException> { parse(mixed) }
    }
    @Test fun independentHashAndChallengeCannotBeTakenFromEvidence() {
        val raw = mapper.writeValueAsBytes(request())
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.parseRequest(raw, "d".repeat(64), challenge) }
        assertThrows<IllegalArgumentException> {
            A06QualificationRunProbe.parseRequest(raw, A06QualificationRunProbe.sha(raw), "49f91d61-999b-412e-a1e4-c22da9ce1b93")
        }
    }
    @Test fun softeningScopeOrAddingAdmissionFieldsIsRejected() {
        listOf("scope" to "live", "allowed_modes" to "feasibility-soft", "proof_source" to "live").forEach { (k, v) ->
            assertThrows<IllegalArgumentException> { parse(request().put(k, v)) }
        }
    }
    @Test fun missingHealthyDuplicateOrRelabeledCaseIsRejected() {
        val missing = request(); (missing.path("cases") as com.fasterxml.jackson.databind.node.ArrayNode).remove(1)
        assertThrows<IllegalArgumentException> { parse(missing) }
        val duplicate = request(); (duplicate.path("cases") as com.fasterxml.jackson.databind.node.ArrayNode).set(1, duplicate.path("cases")[0])
        assertThrows<IllegalArgumentException> { parse(duplicate) }
        listOf("record" to "families/other", "task" to "st-arbitrary-1", "role" to "healthy",
            "control_for" to "other", "component_zip" to "../input.zip").forEach { (k, v) ->
            val bad = request(); (bad.path("cases")[0] as ObjectNode).put(k, v)
            assertThrows<IllegalArgumentException> { parse(bad) }
        }
    }
    @Test fun resourceLimitsMustBeExactIntegers() {
        listOf("spent_limit_ms" to 1001, "step_limit" to 2, "seed" to 1, "max_tasks" to 12).forEach { (k, v) ->
            val bad = request(); (bad.path("limits") as ObjectNode).put(k, v)
            assertThrows<IllegalArgumentException> { parse(bad) }
        }
        val bad = request(); (bad.path("limits") as ObjectNode).put("step_limit", 1.0)
        assertThrows<IllegalArgumentException> { parse(bad) }
    }
    @Test fun duplicateJsonKeysAreRejected() {
        val raw = mapper.writeValueAsString(request()).replace("\"step_limit\":1", "\"step_limit\":1,\"step_limit\":1").toByteArray()
        assertThrows<Exception> { A06QualificationRunProbe.parseRequest(raw, A06QualificationRunProbe.sha(raw), challenge) }
    }
    @Test fun qualificationD1ConfigIsFiniteBoundedAndSingleThreadedWithoutChangingProduction() {
        val cfg = A06QualificationRunProbe.solverConfig()
        assertEquals(0L, cfg.randomSeed)
        assertEquals("NONE", cfg.moveThreadCount)
        assertEquals(1, cfg.terminationConfig!!.stepCountLimit)
        assertEquals(Duration.ofSeconds(1), cfg.terminationConfig!!.spentLimit)
        val phases = requireNotNull(cfg.phaseConfigList)
        assertEquals(2, phases.size)
        assertInstanceOf(ConstructionHeuristicPhaseConfig::class.java, phases[0])
        val phase = assertInstanceOf(LocalSearchPhaseConfig::class.java, phases[1])
        assertNull(phase.localSearchType)
        assertEquals(listOf(AcceptorType.HILL_CLIMBING), requireNotNull(phase.acceptorConfig).acceptorTypeList)
        val moves = assertInstanceOf(ChangeMoveSelectorConfig::class.java, phase.moveSelectorConfig)
        assertEquals(SelectionOrder.ORIGINAL, moves.selectionOrder)
        assertEquals(SelectionOrder.ORIGINAL, requireNotNull(moves.entitySelectorConfig).selectionOrder)
        val values = requireNotNull(moves.valueSelectorConfig)
        assertEquals(SelectionOrder.ORIGINAL, values.selectionOrder)
        assertEquals("employee", values.variableName)
        val forager = requireNotNull(phase.foragerConfig)
        assertEquals(LocalSearchPickEarlyType.NEVER, forager.pickEarlyType)
        assertEquals(Int.MAX_VALUE, forager.acceptedCountLimit)
        assertEquals(false, forager.breakTieRandomly)
        // The XML/default production phase selection has not been modified.
        assertTrue(SolverConfig.createFromXmlResource("solverConfig.xml").phaseConfigList.isNullOrEmpty())
    }
    @Test fun forkedJvmMustHaveItsOwnResourceBoundaryBeforeAnySolve() {
        A06QualificationRunProbe.checkRuntimeLimits(1536L * 1024 * 1024, 2)
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.checkRuntimeLimits(2048L * 1024 * 1024, 2) }
        assertThrows<IllegalArgumentException> { A06QualificationRunProbe.checkRuntimeLimits(1536L * 1024 * 1024, 8) }
    }
    @Test fun inconclusiveStepAndClockOutcomesCannotBePromoted() {
        val healthy = mapOf<String, Any?>("kind" to RunTermination.STEP_COUNT, "search_started" to true,
            "search_steps" to 1, "reproducible" to true, "elapsed_ms" to 45L)
        A06QualificationRunProbe.qualifiedTermination(healthy)
        A06QualificationRunProbe.qualifiedTermination(healthy + mapOf("kind" to RunTermination.WALL_CLOCK_SAFETY, "search_steps" to 0))
        listOf(mapOf("search_started" to false), mapOf("reproducible" to false),
            mapOf("kind" to RunTermination.CANCELLED), mapOf("elapsed_ms" to 5001L),
            mapOf("kind" to RunTermination.WALL_CLOCK_SAFETY)).forEach { delta ->
            assertThrows<IllegalArgumentException> { A06QualificationRunProbe.qualifiedTermination(healthy + delta) }
        }
    }
    @Test fun rejectedActualLifecycleIsRecordedBeforeTheUnchangedGate() {
        val raw = linkedMapOf<String, Any?>("kind" to RunTermination.WALL_CLOCK_SAFETY,
            "search_started" to true, "search_steps" to 1, "reproducible" to false,
            "elapsed_ms" to 1008L, "construction_steps" to 0, "step_limit" to 1,
            "words" to "СТРАХОВОЧНЫЙ предел: воспроизводимость не обещана")
        val lines = mutableListOf<String>()
        val rejected = assertThrows<IllegalArgumentException> {
            A06QualificationRunProbe.recordAndQualifyTermination("st-window-1", challenge, raw, lines::add)
        }
        assertEquals(1, lines.size)
        val diagnostic = mapper.readTree(lines.single())
        assertEquals("a06-qualification-termination-diagnostic/1", diagnostic.path("schema").asText())
        assertEquals("st-window-1", diagnostic.path("task").asText())
        assertEquals(challenge, diagnostic.path("run_id").asText())
        assertEquals(mapper.readTree(mapper.writeValueAsBytes(raw)), diagnostic.path("termination"))
        assertTrue(lines.single().all { it.code < 128 }, "console encoding must not destroy raw termination words")
        assertTrue(rejected.message.orEmpty().contains("not reproducible"))
        assertTrue(rejected.message.orEmpty().contains("st-window-1"))
        assertTrue(rejected.message.orEmpty().contains(lines.single()))
        assertFalse(diagnostic.has("observations")) // Not a successful evidence envelope.
    }
    @Test fun actualScoreSerializationUsesTheProductionStringShape() {
        val solution = FlightCrewSchedule().apply { score = HardSoftScore.of(-2, -3) }
        val json = mapper.readTree(A06QualificationRunProbe.solutionBytes(solution))
        assertEquals("-2hard/-3soft", json.path("score").asText())
        assertTrue(json.path("flightAssignments").isArray)
    }
    @Test fun solutionSidecarPreservesDomainIdentitiesAndIsoMoments() {
        val home = Fixtures.airport("ST1"); val away = Fixtures.airport("ST2")
        val person = Fixtures.employee("P1").apply { homeAirport = home }
        val f = Fixtures.flight("F1", home, Fixtures.t(1, 6), away, Fixtures.t(1, 8))
        val schedule = Fixtures.schedule(listOf(home, away), listOf(person),
            listOf(Fixtures.assignment("F1#0", f, person))).apply { score = HardSoftScore.ZERO }
        val tree = mapper.readTree(A06QualificationRunProbe.solutionBytes(schedule))
        assertEquals("2026-03-01T06:00:00", tree.path("flights")[0].path("departureUTCDateTime").asText())
        assertEquals("F1", tree.path("flightAssignments")[0].path("flight").asText())
        assertEquals("P1", tree.path("flightAssignments")[0].path("employee").asText())
    }
}
