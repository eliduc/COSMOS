package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.domain.solution.ConstraintWeightOverrides
import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.impl.score.constraint.ConstraintMatchPolicy
import ai.timefold.solver.core.impl.solver.DefaultSolverFactory
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest
import java.time.DayOfWeek

/** Explicit parser/score component only. Never a native export, release,
 * admission or catalogue-support witness. No server and no solver.solve(). */
class A06CalendarParserProbe {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }
    private fun property(name: String) = requireNotNull(System.getProperty("a06.calendar.parser.$name"))
    private fun census(root: Path, directory: Path, accept: (Path) -> Boolean = { true }): Map<String, String> {
        require(Files.isDirectory(directory))
        return Files.walk(directory).use { stream ->
            stream.filter { Files.isRegularFile(it) && accept(it) }.toList()
                .associate { root.relativize(it).toString().replace('\\', '/') to sha(Files.readAllBytes(it)) }
                .toSortedMap()
        }
    }
    private fun sources(repo: Path) = (census(repo, repo.resolve("kotlin-solver/src/main/kotlin")) {
        it.toString().endsWith(".kt")
    } + listOf("kotlin-solver/pom.xml", "tests/test_a06_calendar_parser_input_gpt.py",
        "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/A06CalendarParserProbe.kt")
        .associateWith { sha(Files.readAllBytes(repo.resolve(it))) }).toSortedMap()

    private fun score(pkg: LoadedTaskPackage, problem: ByteArray, shift: Long): Map<String, Any> {
        val name = "Calendar window layover penalty"
        val schedule = mapper.readValue(problem, FlightCrewSchedule::class.java)
        assertEquals(2, schedule.flights.size)
        for (assignment in schedule.flightAssignments) {
            assertTrue(schedule.flights.any { it === assignment.flight })
            assertTrue(schedule.employees.any { it === assignment.employee })
        }
        for (flight in schedule.flights) if (shift != 0L) {
            flight.departureUTCDateTime = requireNotNull(flight.departureUTCDateTime).plusDays(shift)
            flight.arrivalUTCDateTime = requireNotNull(flight.arrivalUTCDateTime).plusDays(shift)
        }
        val resolved = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        RunThresholds.checkFacts(schedule, resolved.applied)
        val compiled = LogicCompiler.compile(pkg, resolved, TaskModes.PROFILE_DEFAULT)
        LogicCompiler.refuseIfNeeded(compiled)
        val fitted = LogicCompiler.fit(schedule, resolved.applied, compiled, "calendar parser score-only")
        LogicCompiler.refuseIfNeeded(fitted)
        val weight = pkg.weights.getValue(name)
        assertEquals(1L, weight)
        schedule.calendarWindowRules = pkg.calendarWindows.toMutableList()
        schedule.constraintWeightOverrides = ConstraintWeightOverrides.of(mapOf(name to HardSoftScore.ofSoft(weight)))
        schedule.layoverCostWeights = pkg.weights
        RosterLayoverCost.prepare(schedule, resolved.applied, packaged = true)
        return RunThresholds.withRunModel(resolved.applied, fitted.plans, packaged = true) {
            val config = SolverConfig().withSolutionClass(FlightCrewSchedule::class.java)
                .withEntityClasses(FlightAssignment::class.java)
                .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
            val factory = SolverFactory.create<FlightCrewSchedule>(config) as DefaultSolverFactory<FlightCrewSchedule>
            val director = factory.getScoreDirectorFactory<HardSoftScore>().createScoreDirectorBuilder()
                .withConstraintMatchPolicy(ConstraintMatchPolicy.ENABLED).build()
            try {
                director.setWorkingSolution(schedule)
                director.calculateScore()
                val total = director.constraintMatchTotalMap.entries.single { it.key.id() == name }.value
                assertEquals(0L, total.score.hardScore())
                assertEquals(if (shift == 0L) -1L else 0L, total.score.softScore())
                assertEquals(if (shift == 0L) 1 else 0, total.constraintMatchCount)
                mapOf("flight_shift_seconds" to shift * 86400, "constraint" to name,
                    "hard_score" to total.score.hardScore(), "soft_score" to total.score.softScore(),
                    "matches" to total.constraintMatchCount, "component_weight" to weight)
            } finally { director.close() }
        }
    }

    private fun malformed(directory: Path, tmp: Path): List<Map<String, String>> {
        val cases = listOf("section" to "не список", "days" to "словар", "timezone" to "base-local",
            "action" to "пустое action", "rule" to "без rule", "checksum" to "контрольная сумма",
            "unlisted" to "не объявлен")
        return cases.map { (name, wanted) ->
            val dest = Files.createDirectory(tmp.resolve(name))
            Files.list(directory).use { files -> files.filter { Files.isRegularFile(it) }.forEach {
                Files.copy(it, dest.resolve(it.fileName))
            } }
            val formPath = dest.resolve("threshold-forms.json")
            val form = mapper.readTree(Files.readAllBytes(formPath)) as ObjectNode
            val rows = form.get("calendar_window_penalties") as ArrayNode
            val row = rows[0] as ObjectNode
            when (name) {
                "section" -> form.put("calendar_window_penalties", "not-an-array")
                "days" -> row.set<JsonNode>("days", mapper.createArrayNode().add(5))
                "timezone" -> row.put("tz", "utc")
                "action" -> row.put("action", "")
                "rule" -> row.remove("rule")
            }
            if (name == "checksum") Files.write(formPath, Files.readAllBytes(formPath) + byteArrayOf(32))
            else {
                Files.write(formPath, mapper.writeValueAsBytes(form))
                val manifestPath = dest.resolve("manifest.json")
                val manifest = mapper.readTree(Files.readAllBytes(manifestPath)) as ObjectNode
                val listed = manifest.get("files") as ArrayNode
                val index = (0 until listed.size()).single { listed[it].path("name").asText() == "threshold-forms.json" }
                if (name == "unlisted") listed.remove(index)
                else (listed[index] as ObjectNode).put("sha256", sha(Files.readAllBytes(formPath)))
                Files.write(manifestPath, mapper.writeValueAsBytes(manifest))
            }
            val error = assertThrows(TaskPackageError::class.java) { TaskPackage.read(dest.toFile()) }
            assertTrue(error.message.orEmpty().contains(wanted), error.message)
            mapOf("mutation" to name, "refusal" to error.message.orEmpty())
        }
    }

    @Test
    fun realCalendarSerializerToParserRemainsNonexecutable(@TempDir tmp: Path) {
        val repo = Path.of(property("repo")).toAbsolutePath().normalize()
        val root = Path.of(property("inputs")).toAbsolutePath().normalize()
        val output = Path.of(property("evidence")).toAbsolutePath().normalize()
        require(!output.startsWith(root) && !Files.exists(output))
        val commit = property("commit")
        require(Regex("[0-9a-f]{40}").matches(commit))
        val beforeInputs = census(root, root)
        val beforeSources = sources(repo)
        val beforeArtifacts = census(repo, repo.resolve("kotlin-solver/target/classes"))
        val probeDir = repo.resolve("kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver")
        val beforeProbe = census(repo, probeDir) { it.fileName.toString().startsWith("A06CalendarParserProbe") }
        assertTrue(beforeProbe.isNotEmpty())
        try {
            val identity = mapper.readTree(Files.readAllBytes(root.resolve("identity.json")))
            assertEquals("a06-calendar-parser-input/1", identity.path("schema").asText())
            assertEquals("parser-component-only", identity.path("scope").asText())
            assertEquals("support-missing", identity.path("native_export_refusal").asText())
            identity.path("files").fields().forEachRemaining { (name, hash) ->
                assertEquals(hash.asText(), beforeInputs.getValue(name), name)
            }
            val directory = root.resolve("diagnostic-directory")
            val manifest = mapper.readTree(Files.readAllBytes(directory.resolve("manifest.json")))
            assertFalse(manifest.has("support"))
            assertFalse(manifest.has("approved_by"))
            assertFalse(manifest.path("has_data").asBoolean())
            val pkg = TaskPackage.read(directory.toFile())
            val world = mapper.readTree(Files.readAllBytes(root.resolve("world.json")))
            assertEquals(listOf("thursday"), world.path("world").path("rule_form").path("days").map { it.asText() })
            assertEquals(1, pkg.calendarWindows.size)
            val calendar = pkg.calendarWindows.single()
            assertEquals("R-STAND-calendar_windows", calendar.ruleId)
            assertEquals(setOf(DayOfWeek.THURSDAY), calendar.days)
            assertEquals("layover_penalty", calendar.action)
            val refusals = listOf("pairing", "rostering").flatMap { task ->
                assertTrue(pkg.allowedModes[task].orEmpty().isEmpty())
                listOf("optimization", "feasibility-soft", "feasibility-hard").map { mode ->
                    val error = assertThrows(TaskModeRefused::class.java) { TaskModes.resolve(mode, pkg, task) }
                    assertTrue(error.message.orEmpty().contains("allowed_modes=пусто"), error.message)
                    mapOf("task" to task, "mode" to mode, "refusal" to error.message.orEmpty())
                }
            }
            val negatives = malformed(directory, tmp)
            val problem = Files.readAllBytes(root.resolve("problem.json"))
            val twins = listOf(score(pkg, problem, 0), score(pkg, problem, 1))
            assertEquals(beforeInputs, census(root, root))
            assertEquals(beforeSources, sources(repo))
            assertEquals(beforeArtifacts, census(repo, repo.resolve("kotlin-solver/target/classes")))
            assertEquals(beforeProbe, census(repo, probeDir) { it.fileName.toString().startsWith("A06CalendarParserProbe") })
            val envelope = mapOf("schema" to "a06-calendar-parser-component/1", "scope" to "parser-component-only",
                "catalog" to identity.path("catalog"),
                "producer" to mapOf("class" to javaClass.name, "commit" to commit,
                    "java_version" to System.getProperty("java.version")),
                "sources" to beforeSources, "artifacts" to beforeArtifacts, "probe" to beforeProbe,
                "inputs" to beforeInputs,
                "observations" to mapOf("form" to "calendar_window_penalties", "rule_id" to calendar.ruleId,
                    "action" to calendar.action, "days" to calendar.days.map { it.name.lowercase() }.sorted(),
                    "twins" to twins, "admission_refusals" to refusals, "parser_refusals" to negatives),
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
