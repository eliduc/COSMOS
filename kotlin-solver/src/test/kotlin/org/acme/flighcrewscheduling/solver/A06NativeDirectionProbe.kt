package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.domain.solution.ConstraintWeightOverrides
import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.impl.score.constraint.ConstraintMatchPolicy
import ai.timefold.solver.core.impl.solver.DefaultSolverFactory
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.io.ByteArrayInputStream
import java.math.BigDecimal
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest
import java.time.LocalDate
import java.util.zip.ZipInputStream

/** Native reader -> objective resolver -> fixed score components, NOT mode
 * admission, optimization, a complete catalogue-world package or a live stand.
 * Deliberately not named *Test: external inputs must be explicitly selected.
 */
class A06NativeDirectionProbe {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private data class Case(val metric: String, val task: String, val constraint: String, val raw: Long)
    private val cases = listOf(
        Case("credit_hours_spread", "st-chs-1", "Credit hours spread", 7),
        Case("layover_nights_total", "st-lnt-1", "Layover nights away from home", 1),
        Case("positioning_cost", "st-pos-1", "Positioning deadhead cost", 1),
        Case("preferred_days_off_violations", "st-pdo-1", "Preferred day off violation", 70),
        Case("uncovered_seats", "st-unc-1", "Unassigned slot", 1),
    )
    private fun property(name: String) = requireNotNull(System.getProperty(name)) { "Explicit -D$name required" }
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes)
        .joinToString("") { "%02x".format(it) }
    private fun census(root: Path, dir: Path, accept: (Path) -> Boolean = { true }): Map<String, String> {
        require(Files.isDirectory(dir)) { "Required evidence directory missing: $dir" }
        return Files.walk(dir).use { stream -> stream.filter { Files.isRegularFile(it) && accept(it) }.toList()
            .associate { root.relativize(it).toString().replace('\\', '/') to sha(Files.readAllBytes(it)) }.toSortedMap() }
    }
    private fun sources(repo: Path) = (census(repo, repo.resolve("kotlin-solver/src/main/kotlin")) {
        it.toString().endsWith(".kt")
    } + listOf("kotlin-solver/pom.xml",
        "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/A06NativeDirectionProbe.kt")
        .associateWith { sha(Files.readAllBytes(repo.resolve(it))) }).toSortedMap()
    private data class Input(val pkg: LoadedTaskPackage, val problem: ByteArray, val world: JsonNode,
                             val identity: JsonNode, val hashes: Map<String, String>)
    private fun input(root: Path, case: Case): Input {
        val dir = root.resolve(case.metric)
        val names = listOf("task.zip", "world.json", "problem.json", "identity.json")
        val bytes = names.associateWith { Files.readAllBytes(dir.resolve(it)) }
        val hashes = bytes.mapValues { sha(it.value) }
        val identity = mapper.readTree(bytes.getValue("identity.json"))
        assertEquals("a06-native-direction-input/1", identity.path("schema").asText())
        assertEquals("direction-only-model/component-only", identity.path("scope").asText())
        assertEquals(case.metric, identity.path("metric").asText())
        assertEquals(case.task, identity.path("task").asText())
        names.filter { it != "identity.json" }.forEach {
            assertEquals(identity.path("files").path(it).asText(), hashes.getValue(it), it)
        }
        val members = linkedMapOf<String, ByteArray>()
        ZipInputStream(ByteArrayInputStream(bytes.getValue("task.zip"))).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                require(!entry.isDirectory)
                require(members.put(entry.name, zip.readBytes()) == null)
            }
        }
        val manifest = mapper.readTree(members.getValue("manifest.json"))
        manifest.path("files").forEach {
            assertEquals(it.path("sha256").asText(), sha(members.getValue(it.path("name").asText())))
        }
        listOf("version", "fingerprint").forEach {
            assertEquals(identity.path("catalog").path(it).asText(), manifest.path("catalog").path(it).asText())
        }
        val pkg = TaskPackage.readBytes(bytes.getValue("task.zip"))
        assertEquals(mapper.valueToTree<JsonNode>(pkg.allowedModes), identity.path("allowed_modes"))
        val world = mapper.readTree(bytes.getValue("world.json"))
        val expected = world.path("expected").path("kotlin_metric")
        assertEquals(case.raw, expected.path("value").asLong(-1))
        assertEquals(1, expected.path("matches").asInt(-1))
        assertFalse(expected.path("refusal").asBoolean(true))
        return Input(pkg, bytes.getValue("problem.json"), world, identity, hashes)
    }
    private fun problem(input: Input): FlightCrewSchedule {
        val schedule = mapper.readValue(input.problem, FlightCrewSchedule::class.java)
        schedule.flightAssignments.forEach { a ->
            assertTrue(schedule.flights.any { it === a.flight })
            if (a.employee != null) assertTrue(schedule.employees.any { it === a.employee })
        }
        return schedule
    }

    /** Independent arithmetic and explicit twin edits; never edit input files.
     * Twins judge ONLY the selected component, not overall schedule feasibility.
     */
    private fun verifyWorldAndMakeTwin(case: Case, original: FlightCrewSchedule,
                                      twin: FlightCrewSchedule): String = when (case.metric) {
        "credit_hours_spread" -> {
            assertEquals(2, original.employees.size)
            assertEquals(listOf(300, 300), original.flights.map { it.getDurationMinutes() }.sorted())
            assertEquals(setOf("P1"), original.flightAssignments.map { it.employee!!.id }.toSet())
            // sqrt((10-5)^2 + (0-5)^2) = sqrt(50); whole-hour floor is 7.
            assertEquals(7L, kotlin.math.sqrt(50.0).toLong())
            twin.flightAssignments.single { it.flight!!.flightNumber == "F2" }.employee = twin.employees.single { it.id == "P2" }
            "F2 employee P1 -> P2; two employees receive five credit hours each"
        }
        "layover_nights_total" -> {
            assertEquals(1, original.employees.size)
            val first = original.flights.single { it.flightNumber == "F1" }
            val last = original.flights.single { it.flightNumber == "F2" }
            assertEquals("ST1", original.employees.single().homeAirport!!.code)
            assertEquals("ST2", first.arrivalAirport!!.code)
            assertEquals(first.arrivalAirport, last.departureAirport)
            assertEquals(LocalDate.of(2026, 10, 1), first.arrivalUTCDateTime!!.toLocalDate())
            assertEquals(LocalDate.of(2026, 10, 2), last.departureUTCDateTime!!.toLocalDate())
            val shifted = twin.flights.single { it.flightNumber == "F2" }
            shifted.departureUTCDateTime = requireNotNull(shifted.departureUTCDateTime).minusDays(1)
            shifted.arrivalUTCDateTime = requireNotNull(shifted.arrivalUTCDateTime).minusDays(1)
            "F2 departure and arrival minus one day; return within the same daytime duty"
        }
        "positioning_cost" -> {
            assertEquals(1, original.employees.size)
            assertEquals("ST1", original.employees.single().homeAirport!!.code)
            assertEquals("ST2", original.flights.minBy { it.departureUTCDateTime!! }.departureAirport!!.code)
            twin.employees.single().homeAirport = twin.flights.minBy { it.departureUTCDateTime!! }.departureAirport
            "P1 home airport ST1 -> ST2, matching first departure"
        }
        "preferred_days_off_violations" -> {
            val employee = original.employees.single()
            assertEquals(500, employee.seniority)
            assertEquals(listOf(LocalDate.of(2026, 10, 1)), employee.preferredDaysOff)
            assertEquals(1, original.flightAssignments.count { it.flight!!.getDepartureUTCDate() in employee.preferredDaysOff })
            // Contract: (11 - floor((500-1)/100)) * 10 = 70, not plain match count.
            assertEquals(70, (11 - (500 - 1) / 100) * 10)
            twin.employees.single().preferredDaysOff.clear()
            "Clear P1 declared preferred days off; unchanged seniority and assignments"
        }
        "uncovered_seats" -> {
            assertEquals(2, original.flightAssignments.size)
            assertEquals(1, original.flightAssignments.count { it.employee == null && !it.deadhead })
            twin.flightAssignments.single { it.employee == null && !it.deadhead }.employee = twin.employees.single()
            "Fill the unassigned slot with existing P1; coverage-only twin, NOT a legal full schedule claim"
        }
        else -> error("Unexpected metric")
    }

    private fun score(input: Input, objective: ResolvedObjective, schedule: FlightCrewSchedule): Map<String, Any> {
        val thresholds = RunThresholds.resolveFor(input.pkg, TaskModes.PROFILE_DEFAULT)
        RunThresholds.checkFacts(schedule, thresholds.applied)
        val compiled = LogicCompiler.compile(input.pkg, thresholds, TaskModes.PROFILE_DEFAULT)
        LogicCompiler.refuseIfNeeded(compiled)
        val fitted = LogicCompiler.fit(schedule, thresholds.applied, compiled, "A06 direction component score-only")
        LogicCompiler.refuseIfNeeded(fitted)
        // Deliberate below-admission component projection. Exactly the soft
        // objective weight law of TaskModes, without changing allowed_modes.
        val effective = DefaultProviderRules.SOFT_CONSTRAINTS.associateWith { 0L }.toMutableMap()
        effective[objective.constraint] = objective.weight
        assertTrue(input.pkg.weights.isEmpty(), "Direction-only model must have no hidden penalty sum")
        schedule.constraintWeightOverrides = ConstraintWeightOverrides.of(effective.mapValues { HardSoftScore.ofSoft(it.value) })
        schedule.layoverCostWeights = emptyMap()
        RosterLayoverCost.prepare(schedule, thresholds.applied, packaged = true)
        return RunThresholds.withRunModel(thresholds.applied, fitted.plans, packaged = true) {
            val config = SolverConfig().withSolutionClass(FlightCrewSchedule::class.java)
                .withEntityClasses(FlightAssignment::class.java)
                .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
            val factory = SolverFactory.create<FlightCrewSchedule>(config) as DefaultSolverFactory<FlightCrewSchedule>
            val director = factory.getScoreDirectorFactory<HardSoftScore>().createScoreDirectorBuilder()
                .withConstraintMatchPolicy(ConstraintMatchPolicy.ENABLED).build()
            try {
                director.setWorkingSolution(schedule)
                director.calculateScore()
                val total = director.constraintMatchTotalMap.entries.single { it.key.id() == objective.constraint }.value
                mapOf("hard_score" to total.score.hardScore(), "soft_score" to total.score.softScore(),
                    "matches" to total.constraintMatchCount, "constraint" to objective.constraint,
                    "objective_weight" to objective.weight)
            } finally { director.close() }
        }
    }

    @Test
    fun measureFiveNativeDirectionComponentsWithoutOpeningAdmission() {
        val repo = Path.of(property("a06.direction.repo")).toAbsolutePath().normalize()
        val root = Path.of(property("a06.direction.inputs")).toAbsolutePath().normalize()
        val output = Path.of(property("a06.direction.evidence")).toAbsolutePath().normalize()
        val commit = property("a06.direction.commit")
        require(Regex("[0-9a-f]{40}").matches(commit))
        require(!output.startsWith(root) && !Files.exists(output))
        val beforeInputs = census(root, root)
        val beforeSources = sources(repo)
        val beforeClasses = census(repo, repo.resolve("kotlin-solver/target/classes"))
        val probeDir = repo.resolve("kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver")
        val beforeProbe = census(repo, probeDir) { it.fileName.toString().startsWith("A06NativeDirectionProbe") }
        assertTrue(beforeProbe.isNotEmpty())
        val rows = mutableListOf<Map<String, Any>>()
        val inputs = linkedMapOf<String, Input>()
        try {
            cases.forEach { case ->
                val input = input(root, case)
                inputs[case.metric] = input
                val objectiveId = "O-STAND-${case.metric}"
                val details = input.pkg.objectiveDetails.getValue("rostering").single { it.id == objectiveId }
                assertEquals(case.metric, details.metric)
                assertEquals("minimize", details.direction)
                assertEquals(0, BigDecimal.ONE.compareTo(requireNotNull(details.rate)))
                val admission = assertThrows(TaskModeRefused::class.java) {
                    TaskModes.resolve(MODE_OPTIMIZATION, input.pkg, "rostering", objectiveId)
                }
                assertTrue(admission.message.orEmpty().contains("allowed_modes="))
                assertFalse(MODE_OPTIMIZATION in input.pkg.allowedModes["rostering"].orEmpty())
                assertTrue(MODE_FEASIBILITY_HARD in input.pkg.allowedModes["pairing"].orEmpty())
                val admittedHard = TaskModes.resolve(MODE_FEASIBILITY_HARD, input.pkg, "pairing")
                assertEquals(MODE_FEASIBILITY_HARD, admittedHard.mode)
                val weightsBefore = input.pkg.weights.toMap()
                val modesBefore = input.pkg.allowedModes.mapValues { it.value.toList() }
                // Mutate only a detached parsed-object view; never the native ZIP.
                val maximize = input.pkg.copy(objectiveDetails = input.pkg.objectiveDetails +
                    ("rostering" to input.pkg.objectiveDetails.getValue("rostering").map {
                        if (it.id == objectiveId) it.copy(direction = "maximize") else it
                    }))
                val refused = assertThrows(TaskModeRefused::class.java) {
                    TaskModes.resolveObjective(objectiveId, maximize, "rostering")
                }
                assertTrue(refused.message.orEmpty().contains("maximize"))
                assertTrue(refused.message.orEmpty().contains(objectiveId))
                assertEquals(weightsBefore, input.pkg.weights)
                assertEquals(modesBefore, input.pkg.allowedModes)
                // No director or weight override is created before the refusal above.
                val objective = TaskModes.resolveObjective(objectiveId, input.pkg, "rostering")
                assertEquals(case.constraint, objective.constraint)
                assertEquals(1L, objective.weight)
                assertEquals(case.metric, objective.metric)
                val original = problem(input)
                val twin = problem(input)
                val edit = verifyWorldAndMakeTwin(case, original, twin)
                val healthyBytes = mapper.writeValueAsBytes(twin)
                val observed = score(input, objective, original)
                val healthy = score(input, objective, twin)
                assertEquals(0L, observed["hard_score"])
                assertEquals(-case.raw, observed["soft_score"])
                assertEquals(1, observed["matches"])
                assertEquals(0L, healthy["hard_score"])
                assertEquals(0L, healthy["soft_score"])
                // A zero-weight match (spread) may remain in the match set;
                // healthy evidence is a zero contribution, not a fabricated count.
                rows.add(mapOf("record" to "criteria/${case.metric}", "task" to case.task,
                    "measured_component_kind" to "kotlin_metric", "input_model_scope" to "direction-only",
                    "projection" to "manual component-only soft override after direct resolveObjective; NOT optimization admission",
                    "allowed_modes" to input.pkg.allowedModes,
                    "native_pairing_hard_resolved" to admittedHard.mode,
                    "objective_id" to objectiveId, "rate" to requireNotNull(details.rate).toPlainString(),
                    "resolver" to objective, "native_admission_refusal" to admission.message.orEmpty(),
                    "direct_maximize_refusal" to refused.message.orEmpty(),
                    "original" to observed, "healthy" to healthy,
                    "healthy_input" to mapOf("mutation" to edit, "problem_sha256" to sha(healthyBytes),
                        "problem" to mapper.readTree(healthyBytes)),
                    "got" to mapOf("value" to -(observed.getValue("soft_score") as Long),
                        "matches" to observed.getValue("matches"), "refusal" to false)))
            }
            val catalog = inputs.values.first().identity.path("catalog")
            inputs.values.forEach { assertEquals(catalog, it.identity.path("catalog")) }
            assertEquals(beforeInputs, census(root, root))
            assertEquals(beforeSources, sources(repo))
            assertEquals(beforeClasses, census(repo, repo.resolve("kotlin-solver/target/classes")))
            assertEquals(beforeProbe, census(repo, probeDir) { it.fileName.toString().startsWith("A06NativeDirectionProbe") })
            val envelope = mapOf("schema" to "a06-native-direction-component/1",
                "scope" to "direction-only-model/component-only", "catalog" to catalog,
                "producer" to mapOf("class" to javaClass.name, "commit" to commit,
                    "java_version" to System.getProperty("java.version")),
                "sources" to beforeSources, "artifacts" to beforeClasses, "probe" to beforeProbe,
                "inputs" to inputs.mapValues { it.value.hashes }, "observations" to rows,
                "guards" to mapOf("inputs_unchanged" to true, "sources_unchanged" to true,
                    "artifacts_unchanged" to true, "probe_unchanged" to true))
            Files.write(output, mapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(envelope),
                StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)
        } finally {
            RunThresholds.clearForTests()
            LastProviderReadings.clearForTests()
            assertEquals(beforeInputs, census(root, root))
        }
    }
}
