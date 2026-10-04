package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest

/** Actual parser -> resolution -> constructed provider readings. No scoring,
 * search, HTTP, native export, catalogue-support proof or admission. */
class A06SitRestComponentProbe {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private val targets = linkedMapOf(
        "min_turnaround" to listOf("min_sit_home_min", "min_sit_outstation_min"),
        "min_rest_home" to listOf("min_rest_home_min"),
        "min_rest_outstation" to listOf("min_rest_outstation_min"))
    private val original = linkedMapOf("min_turnaround" to 45, "min_rest_home" to 660, "min_rest_outstation" to 700)
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }
    private fun property(name: String) = requireNotNull(System.getProperty("a06.sitrest.parser.$name"))
    private fun census(root: Path, directory: Path, accept: (Path) -> Boolean = { true }): Map<String, String> =
        Files.walk(directory).use { stream ->
            stream.filter { Files.isRegularFile(it) && accept(it) }.toList()
                .associate { root.relativize(it).toString().replace('\\', '/') to sha(Files.readAllBytes(it)) }.toSortedMap()
        }
    private fun sources(repo: Path) = (census(repo, repo.resolve("kotlin-solver/src/main/kotlin")) {
        it.toString().endsWith(".kt")
    } + listOf("kotlin-solver/pom.xml", "tests/test_a06_sit_rest_inputs_gpt.py",
        "tests/test_a06_component_inputs_gpt.py", "vmp/stand_solver.py", "vmp/stand_adapters.py",
        "cdpm/export_solver.py", "cdpm/threshold_forms.py",
        "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/A06SitRestComponentProbe.kt")
        .associateWith { sha(Files.readAllBytes(repo.resolve(it))) }).toSortedMap()

    private fun desired(values: Map<String, Int>) = targets.flatMap { (param, names) ->
        names.map { it to values.getValue(param) }
    }.toMap()

    private fun readings(pkg: LoadedTaskPackage, values: Map<String, Int>): Map<String, Int> {
        for ((name, value) in values) {
            val param = pkg.hardParams.single { it.name == name }
            assertEquals(value.toDouble(), param.value, name)
            assertEquals("duration_minutes", param.unit, name)
            assertEquals("min", param.relation, name)
            assertNull(param.qualifier, name)
        }
        val resolved = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        val want = desired(values)
        for ((threshold, value) in want) assertEquals(value, resolved.applied.getValue(threshold).value, threshold)
        assertFalse(resolved.applied.containsKey("day_break_threshold_min"))
        val got = RunThresholds.withRunModel(resolved.applied, emptyList(), packaged = true) {
            val provider = FlightCrewSchedulingConstraintProvider()
            assertEquals(0, provider.readings().getValue("day_break_threshold_min"))
            provider.readings().filterKeys { it in want }
        }
        assertEquals(want, got)
        assertTrue(RunThresholds.current().isEmpty())
        assertFalse(RunThresholds.packaged())
        return got
    }

    /** Copy/re-sign only local negative-control directories. Source is untouched. */
    private fun changed(source: Path, dest: Path, change: (ObjectNode) -> Unit): Path {
        Files.createDirectory(dest)
        Files.list(source).use { files -> files.filter { Files.isRegularFile(it) }.forEach {
            Files.copy(it, dest.resolve(it.fileName))
        } }
        val path = dest.resolve("model.json")
        val doc = mapper.readTree(Files.readAllBytes(path)) as ObjectNode
        change(doc)
        Files.write(path, mapper.writeValueAsBytes(doc))
        val manifestPath = dest.resolve("manifest.json")
        val manifest = mapper.readTree(Files.readAllBytes(manifestPath)) as ObjectNode
        val entry = manifest.path("files").single { it.path("name").asText() == "model.json" } as ObjectNode
        entry.put("sha256", sha(Files.readAllBytes(path)))
        Files.write(manifestPath, mapper.writeValueAsBytes(manifest))
        return dest
    }

    private fun rule(doc: ObjectNode, param: String): ObjectNode = doc.path("rules").single {
        it.path("template_fit").path("params").has(param)
    } as ObjectNode
    private fun cell(doc: ObjectNode, param: String) =
        rule(doc, param).path("template_fit").path("params").get(param) as ObjectNode

    private fun parserNegatives(source: Path, tmp: Path): List<Map<String, String>> =
        listOf("missing-condition", "missing-source", "wrong-source", "bool-value", "string-value",
            "unknown-scope", "bad-pin", "carrier-divergence").map { name ->
            val dest = changed(source, tmp.resolve(name)) { doc ->
                val r = rule(doc, "min_turnaround")
                val c = cell(doc, "min_turnaround")
                // Exercise typed semantic validation, not a checksum mismatch
                // or an accidental disagreement with the compatible carrier.
                r.remove("params")
                when (name) {
                    "missing-condition" -> c.remove("condition")
                    "missing-source" -> c.remove("source")
                    "wrong-source" -> c.set<JsonNode>("source", mapper.createObjectNode()
                        .put("kind", "rule").put("rule_id", "R-OTHER"))
                    "bool-value" -> c.put("value", true)
                    "string-value" -> c.put("value", "45")
                    "unknown-scope" -> c.set<JsonNode>("condition", mapper.createObjectNode()
                        .put("kind", "unknown").put("value", "база"))
                    "bad-pin" -> (r.path("template_fit").path("family_contract") as ObjectNode)
                        .put("catalog_fingerprint", "0".repeat(64))
                    "carrier-divergence" -> r.set<JsonNode>("params", mapper.createArrayNode().add(
                        mapper.createObjectNode().put("name", "min_turnaround").put("value", 46)
                            .put("unit", "duration_minutes").put("relation", "min")))
                }
            }
            val error = assertThrows(TaskPackageError::class.java) { TaskPackage.read(dest.toFile()) }
            val code = when (name) { "bad-pin" -> "landing-contract"
                "carrier-divergence" -> "param-carriers-diverge"
                else -> "landing-incomplete" }
            assertTrue(error.message.orEmpty().contains(code), error.message)
            mapOf("mutation" to name, "refusal" to error.message.orEmpty())
        }

    private fun numericNegatives(source: Path, tmp: Path): List<Map<String, Any>> =
        listOf(0.0, -1.0, 45.5).mapIndexed { index, value ->
            val dest = changed(source, tmp.resolve("numeric-$index")) { doc ->
                rule(doc, "min_turnaround").remove("params")
                cell(doc, "min_turnaround").put("value", value)
            }
            val pkg = TaskPackage.read(dest.toFile())
            assertEquals(value, pkg.hardParams.single { it.name == "min_turnaround" }.value)
            val error = assertThrows(TaskModeRefused::class.java) {
                RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
            }
            assertTrue(error.message.orEmpty().contains("min_turnaround"), error.message)
            mapOf("value" to value, "stage" to "resolveFor", "refusal" to error.message.orEmpty())
        }

    private fun windowIsolation(pkg: LoadedTaskPackage): Map<String, Any> {
        val keys = desired(original).keys
        val defaults = FlightCrewSchedulingConstraintProvider().readings().filterKeys { it in keys }
        val resolved = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        val empty = keys.associateWith { 0 }
        RunThresholds.withRunModel(resolved.applied, emptyList(), packaged = true) {
            assertEquals(desired(original), FlightCrewSchedulingConstraintProvider().readings().filterKeys { it in keys })
            val error = assertThrows(IllegalStateException::class.java) {
                RunThresholds.withRunModel(emptyMap(), emptyList(), packaged = true) {
                    assertEquals(empty, FlightCrewSchedulingConstraintProvider().readings().filterKeys { it in keys })
                    throw IllegalStateException("component-window-control")
                }
            }
            assertEquals("component-window-control", error.message)
            assertTrue(RunThresholds.packaged())
            assertEquals(desired(original), FlightCrewSchedulingConstraintProvider().readings().filterKeys { it in keys })
        }
        assertTrue(RunThresholds.current().isEmpty())
        assertFalse(RunThresholds.packaged())
        assertEquals(defaults, FlightCrewSchedulingConstraintProvider().readings().filterKeys { it in keys })
        val next = RunThresholds.withRunModel(emptyMap(), emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider().readings().filterKeys { it in keys }
        }
        assertEquals(empty, next)
        assertTrue(RunThresholds.current().isEmpty())
        assertFalse(RunThresholds.packaged())
        return mapOf("unpackaged_baseline" to defaults, "subsequent_empty_packaged" to next,
            "nested_exception_restored_outer" to true)
    }

    @Test
    fun realSitRestSerializersToProviderRemainNonexecutable(@TempDir tmp: Path) {
        val repo = Path.of(property("repo")).toAbsolutePath().normalize()
        val root = Path.of(property("inputs")).toAbsolutePath().normalize()
        val output = Path.of(property("evidence")).toAbsolutePath().normalize()
        val commit = property("commit")
        require(Regex("[0-9a-f]{40}").matches(commit))
        require(!output.startsWith(root) && !Files.exists(output))
        val beforeInputs = census(root, root)
        val beforeSources = sources(repo)
        val beforeArtifacts = census(repo, repo.resolve("kotlin-solver/target/classes"))
        val probeDir = repo.resolve("kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver")
        val beforeProbe = census(repo, probeDir) { it.fileName.toString().startsWith("A06SitRestComponentProbe") }
        assertTrue(beforeProbe.isNotEmpty())
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
        try {
            val identity = mapper.readTree(Files.readAllBytes(root.resolve("identity.json")))
            assertEquals("a06-sit-rest-parser-input/1", identity.path("schema").asText())
            assertEquals("parser-component-only", identity.path("scope").asText())
            assertEquals("not attempted; diagnostic directory is not a ZIP", identity.path("native_export").asText())
            assertEquals("0.7.0-draft.6", identity.path("catalog").path("version").asText())
            val tasks = listOf("st-sit-1", "st-rest-1")
            assertEquals(tasks, identity.path("tasks").map { it.asText() })
            val indexed = identity.path("files").fields().asSequence().associate { it.key to it.value.asText() }
            assertEquals(beforeInputs - "identity.json", indexed)
            assertFalse(beforeInputs.keys.any { it.endsWith(".zip") })
            val observations = tasks.map { task ->
                val directory = root.resolve("$task/diagnostic-directory")
                val manifest = mapper.readTree(Files.readAllBytes(directory.resolve("manifest.json")))
                assertFalse(manifest.has("support"))
                assertFalse(manifest.has("approved_by"))
                assertFalse(manifest.path("has_data").asBoolean())
                val world = mapper.readTree(Files.readAllBytes(root.resolve("$task/world.json"))).path("world")
                for ((name, value) in original) assertEquals(value, world.path("params").path(name).asInt(-1))
                assertFalse(world.path("params").has("max_sit_duration"))
                val pkg = TaskPackage.read(directory.toFile())
                val got = readings(pkg, original)
                val admission = listOf("pairing", "rostering").flatMap { type ->
                    assertTrue(pkg.allowedModes[type].orEmpty().isEmpty())
                    listOf("optimization", "feasibility-soft", "feasibility-hard").map { mode ->
                        val error = assertThrows(TaskModeRefused::class.java) { TaskModes.resolve(mode, pkg, type) }
                        assertTrue(error.message.orEmpty().contains("allowed_modes=пусто"), error.message)
                        mapOf("task" to type, "mode" to mode, "refusal" to error.message.orEmpty())
                    }
                }
                val taskTmp = Files.createDirectory(tmp.resolve(task))
                val negatives = parserNegatives(directory, taskTmp)
                val numeric = numericNegatives(directory, taskTmp)
                val changedValues = original.mapValues { (_, value) -> value + 1 }
                val healthy = changed(directory, taskTmp.resolve("healthy-other-scalars")) { doc ->
                    for ((name, value) in changedValues) {
                        rule(doc, name).remove("params")
                        cell(doc, name).put("value", value)
                    }
                }
                val healthyGot = readings(TaskPackage.read(healthy.toFile()), changedValues)
                mapOf("task" to task, "original_readings" to got, "healthy_changed_readings" to healthyGot,
                    "admission_refusals" to admission, "parser_refusals" to negatives,
                    "numeric_refusals" to numeric, "window_isolation" to windowIsolation(pkg))
            }
            assertEquals(beforeInputs, census(root, root))
            assertEquals(beforeSources, sources(repo))
            assertEquals(beforeArtifacts, census(repo, repo.resolve("kotlin-solver/target/classes")))
            assertEquals(beforeProbe, census(repo, probeDir) { it.fileName.toString().startsWith("A06SitRestComponentProbe") })
            val envelope = mapOf("schema" to "a06-sit-rest-parser-component/1", "scope" to "parser-component-only",
                "catalog" to identity.path("catalog"), "producer" to mapOf("class" to javaClass.name,
                    "commit" to commit, "java_version" to System.getProperty("java.version")),
                "sources" to beforeSources, "artifacts" to beforeArtifacts, "probe" to beforeProbe,
                "inputs" to beforeInputs, "observations" to observations,
                "qualification" to "No native export, support acceptance, scoring, solve or admission; max_sit_duration absent",
                "guards" to mapOf("inputs_unchanged" to true, "sources_unchanged" to true, "artifacts_unchanged" to true))
            Files.write(output, mapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(envelope),
                StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)
        } finally {
            RunThresholds.clearForTests()
            LastProviderReadings.clearForTests()
            assertEquals(beforeInputs, census(root, root))
        }
    }
}
