package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.config.solver.SolverConfig
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
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
import org.junit.jupiter.api.io.TempDir
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.TimeUnit
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Isolated envelope/decoder/admission tests. No service, solver or remote
 * stand is started. The refusing executor proves rejection occurs pre-submit.
 */
class ComponentInputTest {
    private val om = ObjectMapper()
    private val catalog = "a".repeat(64)
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes)
        .joinToString("") { "%02x".format(it) }
    private fun bytes(value: String) = value.toByteArray(Charsets.UTF_8)
    private fun node(value: String) = om.readTree(value)

    private fun payload(): LinkedHashMap<String, ByteArray> {
        val world = """{"params":{"max_duty_duration":780}}"""
        val expected = """{"kotlin_knob":{"value":780}}"""
        val model = """{"meta":{"model_schema":2},"rules":[{"id":"R-DUTY","kind":"HARD",
          "template_fit":{"schema_version":"landing/2","category":"A","family":"duty_limits",
            "registry_rev":"$catalog","family_contract":{"family_id":"duty_limits",
              "catalog_version":"0.8.0-draft.1","catalog_fingerprint":"$catalog",
              "record_fingerprint":"${"b".repeat(64)}","param_set":"default"},
            "params":{"max_duty_duration":{"value":780,"unit":"duration_minutes","relation":"max",
              "condition":null,"quote":"780 minutes","source":{"kind":"rule","rule_id":"R-DUTY"}}}}}]}"""
        return linkedMapOf(
            "model.json" to bytes(model), "objective.json" to bytes("""{"pairing":{"objectives":[],"components":[]}}"""),
            "vmp-form.json" to bytes("{}"), "vmp-form.md" to bytes("model view"),
            "smt-report.json" to bytes("{}"), "problem.json" to bytes("{}"),
            "declared-world.json" to bytes(world), "declared-expected.json" to bytes(expected),
            "world.json" to bytes("""{"world":$world,"expected":$expected}"""),
            "stand-world.json" to bytes("""{"стенд":{"kind":"synthetic-code-world","world":$world,"rules":[]}}"""))
    }

    private fun manifest(files: Map<String, ByteArray>): ObjectNode {
        val result = node("""{"schema":"component-input/1","scope":"component-only","spec_version":"1.21",
          "catalog":{"version":"0.8.0-draft.1","fingerprint":"$catalog"},
          "task":{"record":"families/duty_limits","id":"st-duty-1",
            "world_sha256":"${sha(files.getValue("declared-world.json"))}",
            "expected_sha256":"${sha(files.getValue("declared-expected.json"))}"},
          "consistency":{},"families":{},"form":{},"coverage":{},"measure":{},
          "has_data":true,"excluded_until_data":[]}""") as ObjectNode
        val listed = result.putArray("files")
        for ((name, body) in files.toSortedMap()) listed.addObject().put("name", name).put("sha256", sha(body)).put("bytes", body.size)
        return result
    }

    private fun zip(files: Map<String, ByteArray>): ByteArray {
        val output = ByteArrayOutputStream()
        ZipOutputStream(output).use { zip ->
            files.forEach { (name, body) -> zip.putNextEntry(ZipEntry(name)); zip.write(body); zip.closeEntry() }
        }
        return output.toByteArray()
    }

    private fun component(files: Map<String, ByteArray> = payload(), edit: (ObjectNode) -> Unit = {}): ByteArray {
        val m = manifest(files).also(edit)
        return zip(files + ("component-manifest.json" to om.writeValueAsBytes(m)))
    }

    private fun normal(files: Map<String, ByteArray> = payload()): LinkedHashMap<String, ByteArray> {
        val m = manifest(files)
        m.remove(listOf("schema", "scope", "task"))
        val ordinary = files - setOf("declared-world.json", "declared-expected.json")
        val listed = m.putArray("files")
        for ((name, body) in ordinary.toSortedMap()) listed.addObject().put("name", name).put("sha256", sha(body)).put("bytes", body.size)
        m.put("customer", "reader control").put("source_run", "synthetic-unit").put("revision", 1).put("readiness", "not-admitted")
        m.set<JsonNode>("allowed_modes", node("{}"))
        m.set<JsonNode>("objectives", node("{}"))
        return LinkedHashMap(ordinary + ("manifest.json" to om.writeValueAsBytes(m)))
    }

    private fun refused(input: ByteArray, marker: String) {
        val error = assertThrows<TaskPackageError> { ComponentInput.readBytes(input) }
        assertTrue(error.message.orEmpty().contains(marker), error.message)
    }

    @AfterEach fun cleanup() {
        TaskPackage.overrideForTests(null)
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
        PackagedRuns.clearForTests()
    }

    @Test fun healthyComponentUsesTheActualProductionDecoderAndProvider() {
        val c = ComponentInput.readBytes(component())
        val p = TaskPackage.readBytes(zip(normal()))
        assertFalse(LoadedTaskPackage::class.java.isInstance(c))
        assertFalse(LoadedTaskPackage::class.java.isInstance(c.nativeModel))
        assertEquals(p.hardParams, c.nativeModel.hardParams)
        assertEquals(p.objectiveDetails, c.nativeModel.objectiveDetails)
        assertEquals(p.weights, c.nativeModel.weights)
        assertEquals(p.calendarWindows, c.nativeModel.calendarWindows)
        assertEquals(p.logic, c.nativeModel.logic)
        val resolved = RunThresholds.resolveNative(c.nativeModel, TaskModes.PROFILE_DEFAULT)
        assertEquals(RunThresholds.resolveFor(p, TaskModes.PROFILE_DEFAULT), resolved)
        assertEquals(780, resolved.applied.getValue("max_duty_span_min").value)
        val reading = RunThresholds.withRunModel(resolved.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider().readings().getValue("max_duty_span_min")
        }
        assertEquals(780, reading)
        assertEquals(LogicCompiler.compile(p, resolved, TaskModes.PROFILE_DEFAULT),
            LogicCompiler.compileNative(c.nativeModel, resolved, TaskModes.PROFILE_DEFAULT))
    }

    @Test fun calendarFormAndWeightsUseTheSameDecoder() {
        val f = payload()
        f["objective.json"] = bytes("""{"pairing":{"components":[{"rule_id":"R-WINDOW","weight":23}]}}""")
        f["threshold-forms.json"] = bytes("""{"calendar_window_penalties":[{"rule":"R-WINDOW","action":"layover_penalty","tz":"base-local","days":["thursday"]}]}""")
        val c = ComponentInput.readBytes(component(f) { it.set<JsonNode>("rule_maps", node("""{"rule_to_constraint":{"R-WINDOW":"Calendar window layover penalty"}}""")) })
        assertEquals(23L, c.nativeModel.weights.getValue("Calendar window layover penalty"))
        assertEquals(setOf(java.time.DayOfWeek.THURSDAY), c.nativeModel.calendarWindows.single().days)
        f["threshold-forms.json"] = bytes("""{"calendar_window_penalties":[{"rule":"R-WINDOW","action":"layover_penalty","tz":"UTC","days":["thursday"]}]}""")
        refused(component(f), "base-local")
    }

    @ParameterizedTest
    @ValueSource(strings = ["allowed_modes", "readiness", "readiness_matrix", "objectives", "support", "stand_only", "landing_compat"])
    fun forgedAdmissionFieldsAreNotRecognized(field: String) {
        refused(component { it.set<JsonNode>(field, node("""{"rostering":["feasibility-soft"]}""")) }, "unknown fields")
    }

    @Test fun executableAndComponentEnvelopesCannotBeMixed() {
        val f = payload()
        val mixed = normal(f)
        mixed["component-manifest.json"] = om.writeValueAsBytes(manifest(f))
        refused(zip(mixed), "mixed")
        assertTrue(assertThrows<TaskPackageError> { TaskPackage.readBytes(zip(mixed)) }.message!!.contains("component-only"))
        assertTrue(assertThrows<TaskPackageError> { TaskPackage.readBytes(component()) }.message!!.contains("component-only"))
        refused(zip(normal()), "mixed")
    }

    @Test fun addingComponentManifestCannotReuseAFormerExecutableCache(@TempDir directory: Path) {
        val normal = normal()
        normal.forEach { (name, content) -> Files.write(directory.resolve(name), content) }
        TaskPackage.cachedByManifest(directory.toFile())
        Files.write(directory.resolve("component-manifest.json"), bytes("{}"))
        val failure = assertThrows<TaskPackageError> { TaskPackage.cachedByManifest(directory.toFile()) }
        assertTrue(failure.message!!.contains("component-only"))
    }

    @ParameterizedTest
    @ValueSource(strings = ["schema", "scope", "declared-world.json", "declared-expected.json"])
    fun renamedComponentMarkersCannotGainExecutionFromFakeAllowedModes(marker: String) {
        val files = normal()
        val m = om.readTree(files.getValue("manifest.json")) as ObjectNode
        m.set<JsonNode>("allowed_modes", node("""{"pairing":["feasibility-hard"],"rostering":["feasibility-soft"]}"""))
        m.put("stand_only", true)
        if (marker.endsWith(".json")) files[marker] = bytes("{}")
        else m.put(marker, if (marker == "schema") "component-input/1" else "component-only")
        files["manifest.json"] = om.writeValueAsBytes(m)
        val error = assertThrows<TaskPackageError> { TaskPackage.readBytes(zip(files)) }
        assertTrue(error.message!!.contains("component-only"))
    }

    private class NoExecution : AbstractExecutorService() {
        var submitted = false
        override fun execute(command: Runnable) { submitted = true; error("No executor work is allowed") }
        override fun shutdown() {}
        override fun shutdownNow(): MutableList<Runnable> = mutableListOf()
        override fun isShutdown() = false
        override fun isTerminated() = false
        override fun awaitTermination(timeout: Long, unit: TimeUnit) = true
    }

    @Test fun allRunAdmissionEntryPointsRejectBeforeSubmittingAnyWork() {
        val executor = NoExecution()
        val bytes = component()
        val start = assertThrows<TaskPackageError> {
            PackagedRuns.start(FlightCrewSchedule(), bytes, "feasibility-soft", null, SolverConfig(), executor)
        }
        assertTrue(start.message!!.contains("component-only"))
        val catalogBytes = bytes("""{"coverage":{"verdict":"ok"},"selected":[]}""")
        val register = assertThrows<TaskPackageError> {
            PackagedRuns.registerPairingCatalog(FlightCrewSchedule(), bytes, catalogBytes, sha(catalogBytes))
        }
        assertTrue(register.message!!.contains("component-only"))
        assertFalse(executor.submitted)
    }

    @Test fun missingExtraAndPathAliasedMembersRefuse() {
        val f = payload()
        val m = manifest(f)
        refused(zip((f - "objective.json") + ("component-manifest.json" to om.writeValueAsBytes(m))), "required members")
        refused(component(f + ("unexpected.json" to bytes("{}"))), "noncanonical member")
        refused(component(f + ("../model.json" to f.getValue("model.json"))), "noncanonical member")
        refused(component(f + ("nested/component-manifest.json" to bytes("{}"))), "noncanonical member")
        refused(component(f + ("freeze\\clauses\\S1.json" to bytes("{}"))), "noncanonical member")
    }

    @Test fun duplicateArchiveMembersRefuseBeforeAnyHashDecision() {
        val f = payload()
        val archive = zip(f + ("modal.json" to f.getValue("model.json")) +
            ("component-manifest.json" to om.writeValueAsBytes(manifest(f))))
        val from = bytes("modal.json")
        val to = bytes("model.json")
        for (i in 0..archive.size - from.size) {
            if (from.indices.all { archive[i + it] == from[it] }) to.copyInto(archive, i)
        }
        refused(archive, "duplicate archive member")
    }

    @Test fun manifestListsCannotHideDuplicatesExtraOrUnlistedMembers() {
        refused(component { (it.path("files") as ArrayNode).add(it.path("files").first().deepCopy<JsonNode>()) }, "duplicate/self-declared")
        refused(component { (it.path("files") as ArrayNode).remove(0) }, "exact sorted member list")
        refused(component { m ->
            val files = m.path("files") as ArrayNode
            val first = files.remove(0)
            files.add(first)
        }, "exact sorted member list")
    }

    @Test fun bytesAndHashAreBothChecked() {
        refused(component { (it.path("files").first() as ObjectNode).put("sha256", "0".repeat(64)) }, "hash mismatch")
        refused(component { (it.path("files").first() as ObjectNode).put("bytes", -1) }, "byte count mismatch")
    }

    @Test fun duplicateJsonKeysAreRejectedRatherThanLastValueWinning() {
        val f = payload()
        f["world.json"] = bytes("""{"world":{},"world":{"params":{"max_duty_duration":780}},"expected":{}}""")
        refused(component(f), "unambiguous JSON")
    }

    @Test fun boundWorldExpectedAndNativeSourceMustAgree() {
        val f = payload()
        refused(component(f) { (it.path("task") as ObjectNode).put("world_sha256", "0".repeat(64)) }, "declaration hash mismatch")
        f["world.json"] = bytes("""{"world":{},"expected":{}}""")
        refused(component(f), "differs from the bound")
        val source = payload()
        source["stand-world.json"] = bytes("""{"стенд":{"world":{"params":{"max_duty_duration":999}}}}""")
        refused(component(source), "native source world differs")
    }

    @Test fun catalogAndModernCellGuardsRemainActiveAfterFreshChecksums() {
        val f = payload()
        val m = node(String(f.getValue("model.json"), Charsets.UTF_8)) as ObjectNode
        val fit = m.path("rules").first().path("template_fit") as ObjectNode
        (fit.path("family_contract") as ObjectNode).put("catalog_version", "other")
        f["model.json"] = om.writeValueAsBytes(m)
        refused(component(f), "catalog identity differs")
        (fit.path("family_contract") as ObjectNode).put("catalog_version", "0.8.0-draft.1")
        (fit.path("params").path("max_duty_duration") as ObjectNode).remove("unit")
        f["model.json"] = om.writeValueAsBytes(m)
        refused(component(f), "landing-incomplete")
        fit.put("schema_version", "landing/1")
        f["model.json"] = om.writeValueAsBytes(m)
        refused(component(f), "landing-version")
    }

    private fun knobInput(family: String, parameters: String, kind: String = "HARD"): LoadedComponentInput {
        val f = payload()
        val model = node(String(f.getValue("model.json"), Charsets.UTF_8)) as ObjectNode
        val rule = model.path("rules").first() as ObjectNode
        rule.put("kind", kind)
        val fit = rule.path("template_fit") as ObjectNode
        fit.put("family", family)
        (fit.path("family_contract") as ObjectNode).put("family_id", family)
        val cells = node(parameters) as ObjectNode
        for (cell in cells) {
            (cell as ObjectNode).putNull("condition").put("quote", "independent declared unit fixture")
            cell.set<JsonNode>("source", node("""{"kind":"rule","rule_id":"R-DUTY"}"""))
        }
        fit.set<JsonNode>("params", cells)
        f["model.json"] = om.writeValueAsBytes(model)
        val world = om.createObjectNode()
        val worldParams = world.putObject("params")
        cells.fields().forEachRemaining { worldParams.set<JsonNode>(it.key, it.value.path("value")) }
        // No expected numeric value is supplied to the measurement. This test
        // would fail if got were copied from expected rather than the provider.
        f["declared-world.json"] = om.writeValueAsBytes(world)
        f["declared-expected.json"] = bytes("{}")
        val envelope = om.createObjectNode()
        envelope.set<JsonNode>("world", world)
        envelope.set<JsonNode>("expected", node("{}"))
        f["world.json"] = om.writeValueAsBytes(envelope)
        val source = om.createObjectNode()
        source.putObject("стенд").set<JsonNode>("world", world)
        f["stand-world.json"] = om.writeValueAsBytes(source)
        return ComponentInput.readBytes(component(f) { (it.path("task") as ObjectNode).put("record", "families/$family") })
    }

    private fun measured(input: LoadedComponentInput): JsonNode {
        val home = airport("ST1")
        val away = airport("ST2")
        val first = employee("E1").apply { homeAirport = home }
        val second = employee("E2").apply { homeAirport = home }
        val leg = flight("F1", home, t(2, 10), away, t(2, 12))
        val facts = Fixtures.schedule(listOf(home, away), listOf(first, second),
            listOf(assignment("a1", leg, first), assignment("a2", leg, second)))
        return om.valueToTree(A06TypedComponentProbe().typedKnob(input.record, input.nativeModel, input.model, input.world, facts))
    }

    @Test fun measuredWorkloadUsesActualHoursToMinutesAndProvider() {
        val result = measured(knobInput("balanced_workload",
            """{"workload_spread_threshold":{"value":5,"unit":"credit_hours","relation":"max"}}""", "SOFT"))
        val row = result.path("readings").single()
        assertEquals(5, row.path("input_value").asInt())
        assertEquals(300, row.path("value").asInt())
        assertEquals(300, row.path("provider_value").asInt())
        assertEquals("duration_minutes", row.path("value_unit").asText())
    }

    @Test fun measuredCrewAndDistinctRestValuesReachTheirOwnThresholds() {
        val crew = measured(knobInput("crew_composition",
            """{"crew_size":{"value":2,"unit":"count","relation":"eq"}}"""))
        assertEquals(2, crew.path("readings").single().path("provider_value").asInt())
        val rest = measured(knobInput("rest_floors",
            """{"min_rest_home":{"value":660,"unit":"duration_minutes","relation":"min"},
               "min_rest_outstation":{"value":700,"unit":"duration_minutes","relation":"min"}}"""))
        assertEquals(mapOf("min_rest_home" to 660, "min_rest_outstation" to 700),
            rest.path("readings").associate { it.path("param").asText() to it.path("provider_value").asInt() })
    }

    @Test fun measuredFleetAndKeyedReserveReportValuesNotCountsOrCollapsedLabels() {
        val fleet = measured(knobInput("fleet_exclusions",
            """{"fleet_types":{"value":["T2","T3"],"unit":"code_list","relation":null}}"""))
        assertEquals(node("""["T2","T3"]"""), fleet.path("readings").single().path("provider_value"))
        assertTrue(fleet.path("readings").single().path("relation").isNull)
        val reserve = measured(knobInput("reserve_floors",
            """{"reserve_crews":{"value":{"ST1":1,"ST2":2},"unit":"count","relation":"min"}}"""))
        assertEquals(node("""{"ST1":1,"ST2":2}"""), reserve.path("readings").single().path("provider_value"))
    }

    @Test fun separatelyDeclaredNonzeroScaleInputIsActuallyRefused() {
        val zero = measured(knobInput("time_scale",
            """{"base_tz_offset_min":{"value":0,"unit":"duration_minutes","relation":"eq"}}""", "DEF"))
        assertEquals("typed-knob-readings/1", zero.path("profile").asText())
        assertEquals(0, zero.path("readings").single().path("provider_value").asInt())
        val refused = measured(knobInput("time_scale",
            """{"base_tz_offset_min":{"value":60,"unit":"duration_minutes","relation":"eq"}}""", "DEF"))
        assertEquals("typed-knob-refusal/1", refused.path("profile").asText())
        assertEquals(60, refused.path("input_value").asInt())
        assertTrue(refused.path("refused").asBoolean())
        assertEquals("nonzero_scale_offset", refused.path("reason").asText())
    }
}
