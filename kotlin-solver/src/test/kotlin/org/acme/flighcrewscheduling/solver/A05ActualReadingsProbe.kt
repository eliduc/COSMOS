package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.io.ByteArrayInputStream
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest
import java.util.zip.ZipInputStream

/** Actual provider-construction readings, not solve/HTTP/live admission or a full stand.
 * No result is substituted for a missing reading; external executors remain explicit.
 * Requires explicit a05.readings.{repo,inputs,evidence,commit}; not a default *Test.
 */
class A05ActualReadingsProbe {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private val source = "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/A05ActualReadingsProbe.kt"
    private fun property(name: String) = requireNotNull(System.getProperty("a05.readings.$name")) {
        "Explicit probe requires -Da05.readings.$name"
    }
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }

    private fun census(root: Path, dir: Path, accept: (Path) -> Boolean = { true }): Map<String, String> {
        require(Files.isDirectory(dir)) { "Missing evidence directory: $dir" }
        return Files.walk(dir).use { stream ->
            stream.filter { Files.isRegularFile(it) && accept(it) }.toList()
                .associate { root.relativize(it).toString().replace('\\', '/') to sha(Files.readAllBytes(it)) }
                .toSortedMap()
        }
    }

    private fun sources(repo: Path) = (census(repo, repo.resolve("kotlin-solver/src/main/kotlin")) {
        it.fileName.toString().endsWith(".kt")
    } + listOf("kotlin-solver/pom.xml", source).associateWith { sha(Files.readAllBytes(repo.resolve(it))) }).toSortedMap()

    private fun paramRow(p: RuleParam): Map<String, Any?> = linkedMapOf(
        "name" to p.name, "rule" to p.ruleId, "kind" to p.ruleKind,
        "value" to p.value, "rawValue" to p.rawValue, "unit" to p.unit,
        "relation" to p.relation, "qualifier" to p.qualifier, "tz" to p.tz,
        "asWritten" to p.asWritten, "codeValues" to p.codeValues)

    @Test
    fun captureAllActualNativeAppliedThresholdsAndProviderReadings() {
        val repo = Path.of(property("repo")).toAbsolutePath().normalize()
        val input = Path.of(property("inputs")).toAbsolutePath().normalize()
        val output = Path.of(property("evidence")).toAbsolutePath().normalize()
        val commit = property("commit")
        require(Regex("[0-9a-f]{40}").matches(commit))
        require(!output.startsWith(input) && !output.startsWith(repo)) { "Output must be outside source/input trees" }
        require(!Files.exists(output)) { "Evidence is CREATE_NEW only" }
        val names = listOf("st-window-1.zip", "st-window-1.world.json", "st-window-1.problem.json", "identity.json")
        val inputBytes = names.associateWith { Files.readAllBytes(input.resolve(it)) }
        val inputHashes = inputBytes.mapValues { sha(it.value) }.toSortedMap()
        val beforeInput = census(input, input)
        val beforeSources = sources(repo)
        val classes = repo.resolve("kotlin-solver/target/classes")
        val probeDir = repo.resolve("kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver")
        val beforeArtifacts = census(repo, classes)
        val probeFilter: (Path) -> Boolean = { it.fileName.toString().startsWith("A05ActualReadingsProbe") && it.fileName.toString().endsWith(".class") }
        val beforeProbe = census(repo, probeDir, probeFilter)
        assertTrue(beforeArtifacts.isNotEmpty())
        assertTrue(beforeProbe.isNotEmpty())
        val previousApplied = RunThresholds.current()
        val previousPlans = RunThresholds.plans()
        val previousPackaged = RunThresholds.packaged()
        fun unchanged() {
            assertEquals(beforeInput, census(input, input), "Input tree changed")
            assertEquals(beforeSources, sources(repo), "Source tree changed")
            assertEquals(beforeArtifacts, census(repo, classes), "Production artifacts changed")
            assertEquals(beforeProbe, census(repo, probeDir, probeFilter), "Probe artifacts changed")
            assertEquals(previousApplied, RunThresholds.current(), "Threshold window leaked")
            assertEquals(previousPlans, RunThresholds.plans(), "Logic-plan window leaked")
            assertEquals(previousPackaged, RunThresholds.packaged(), "Packaged window leaked")
        }
        try {
            val identity = mapper.readTree(inputBytes.getValue("identity.json"))
            assertEquals("st-window-1", identity.path("task").asText())
            for (name in names.filter { it != "identity.json" }) {
                assertEquals(identity.path("files").path(name).asText(), inputHashes.getValue(name), name)
            }
            val members = linkedMapOf<String, ByteArray>()
            ZipInputStream(ByteArrayInputStream(inputBytes.getValue("st-window-1.zip"))).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    require(!entry.isDirectory)
                    require(members.put(entry.name, zip.readBytes()) == null)
                }
            }
            val manifest = mapper.readTree(members.getValue("manifest.json"))
            for (file in manifest.path("files")) {
                assertEquals(file.path("sha256").asText(), sha(members.getValue(file.path("name").asText())))
            }
            assertEquals("1.20", manifest.path("spec_version").asText())
            assertEquals(identity.path("catalog_version"), manifest.path("catalog").path("version"))
            assertEquals(identity.path("catalog_fingerprint"), manifest.path("catalog").path("fingerprint"))
            val pkg = TaskPackage.readBytes(inputBytes.getValue("st-window-1.zip"))
            assertEquals(listOf("feasibility-hard"), pkg.allowedModes["pairing"])
            assertTrue(pkg.allowedModes["rostering"].orEmpty().isEmpty())
            val mode = TaskModes.resolve("feasibility-hard", pkg, task = "pairing")
            val resolution = mode.thresholds
            assertEquals("feasibility-hard", mode.mode)
            assertTrue(resolution.applied.isNotEmpty())
            val readings = RunThresholds.withRunModel(resolution.applied, emptyList(), packaged = true) {
                FlightCrewSchedulingConstraintProvider().readings().toSortedMap()
            }
            val snapshot = RunThresholds.snapshot(resolution, readings)
            val world = mapper.readTree(inputBytes.getValue("st-window-1.world.json"))
            // Independent original-world controls, not values copied out of the resolver.
            val expectedInputs = linkedMapOf("min_turnaround" to 45, "max_duty_duration" to 780,
                "max_pairing_days" to 7, "min_rest_home" to 660,
                "min_rest_outstation" to 700, "base_tz_offset_min" to 0)
            assertEquals(expectedInputs.keys, world.path("world").path("params").fieldNames().asSequence().toSet())
            assertEquals(expectedInputs.keys, pkg.hardParams.map { it.name }.toSet())
            for ((name, value) in expectedInputs) {
                assertEquals(value, world.path("world").path("params").path(name).intValue(), name)
                assertEquals(value.toDouble(), pkg.hardParams.single { it.name == name }.value, name)
            }
            val expected = linkedMapOf("min_sit_home_min" to 45, "min_sit_outstation_min" to 45,
                "max_duty_span_min" to 780, "min_rest_home_min" to 660,
                "min_rest_outstation_min" to 700, "base_tz_offset_min" to 0)
            assertEquals(expected.keys + "max_pairing_days", resolution.applied.keys)
            for ((name, value) in expected) {
                assertEquals(value, resolution.applied.getValue(name).value, name)
                assertEquals(value, readings[name], name)
            }
            assertEquals(7, resolution.applied.getValue("max_pairing_days").value)
            assertFalse(readings.containsKey("max_pairing_days"), "Builder-only value must not be fabricated as a provider reading")
            assertTrue(pkg.hardParams.none { it.name == "max_landings_per_duty" }, "This native world does not declare a landing limit")
            assertFalse(resolution.applied.containsKey("max_landings_per_duty"))
            assertEquals(0, readings["max_landings_per_duty"], "Undeclared packaged landing constraint remains off")
            val applied = resolution.applied.values.map { a ->
                val parameters = pkg.hardParams.filter { it.name in a.fromParams && it.ruleId in a.rules }
                assertTrue(parameters.isNotEmpty(), "Applied entry has no actual input source: ${a.threshold}")
                linkedMapOf<String, Any?>("threshold" to a.threshold, "value" to a.value,
                    "fromParams" to a.fromParams, "rules" to a.rules, "units" to parameters.map { it.unit }.distinct().sorted(),
                    "literal" to a.literal, "declarations" to a.declarations, "codes" to a.codes,
                    "parameters" to parameters.map(::paramRow),
                    "provider_reading_present" to readings.containsKey(a.threshold),
                    "provider_value" to readings[a.threshold])
            }
            val missing = resolution.applied.keys.filter { it !in readings }.sorted()
            val mismatches = resolution.applied.filter { (key, value) -> key in readings && readings[key] != value.value }
                .map { (key, value) -> mapOf("threshold" to key, "applied" to value.value, "provider" to readings[key]) }
            assertTrue(mismatches.isEmpty(), "Actual observed numeric values disagree: $mismatches")
            unchanged()
            val envelope = linkedMapOf<String, Any?>(
                "schema" to "a05-actual-readings/1", "scope" to "component/admission-prefix-only",
                "catalog" to manifest.path("catalog"),
                "producer" to mapOf("class" to javaClass.name, "commit" to commit,
                    "java_version" to System.getProperty("java.version")),
                "sources" to beforeSources, "artifacts" to beforeArtifacts, "probe" to beforeProbe,
                "inputs" to mapOf("task" to "st-window-1", "files" to inputHashes,
                    "zip_members" to members.mapValues { sha(it.value) }.toSortedMap()),
                "observations" to linkedMapOf("mode" to mode.mode, "task" to "pairing",
                    "allowed_modes" to pkg.allowedModes, "hard_params" to pkg.hardParams.map(::paramRow),
                    "applied" to applied, "thresholds" to snapshot,
                    "paramToThreshold" to snapshot["paramToThreshold"],
                    "qualifiedParamToThreshold" to snapshot["qualifiedParamToThreshold"],
                    "profileReadings" to readings, "missing_provider_readings" to missing,
                    "mismatches" to mismatches, "declared_expected" to world.path("expected")),
                "limitations" to listOf("Provider construction readings only; no score calculation, solve or service run",
                    "max_pairing_days is carried to an external pairing builder, not measured by this Kotlin provider",
                    "max_landings_per_duty is absent from this native input; observed zero means disabled, not a tested declared limit"),
                "guards" to mapOf("inputs_unchanged" to true, "sources_unchanged" to true,
                    "artifacts_unchanged" to true, "probe_unchanged" to true, "run_window_restored" to true))
            Files.write(output, mapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(envelope),
                StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)
            println("A05 actual readings: applied=${resolution.applied.size}, provider=${readings.size}, missing=$missing, evidence=$output")
        } finally {
            unchanged()
            LastProviderReadings.clearForTests()
        }
    }
}
