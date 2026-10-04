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
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest
import java.time.DayOfWeek
import java.util.zip.ZipInputStream

/** Explicit component measurement, not admission, solve, service or release.
 * Read unchanged native exporter inputs; emit observations only after all
 * assertions and before/after file-census checks. No stand PASS is fabricated.
 * Not *Test: normal discovery must not require external input/output paths.
 */
class A06TypedComponentProbe {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }
    private fun property(name: String): String = requireNotNull(System.getProperty(name)) {
        "Explicit component probe requires -D$name"
    }

    private fun census(root: Path, directory: Path, accept: (Path) -> Boolean = { true }): Map<String, String> {
        require(Files.isDirectory(directory)) { "Required evidence directory missing: $directory" }
        return Files.walk(directory).use { stream ->
            stream.filter { Files.isRegularFile(it) && accept(it) }.toList()
                .associate { root.relativize(it).toString().replace('\\', '/') to sha(Files.readAllBytes(it)) }
                .toSortedMap()
        }
    }

    private fun sources(repo: Path): Map<String, String> =
        (census(repo, repo.resolve("kotlin-solver/src/main/kotlin")) { it.toString().endsWith(".kt") } +
            listOf("kotlin-solver/pom.xml",
                "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/A06TypedComponentProbe.kt")
                .associateWith { sha(Files.readAllBytes(repo.resolve(it))) })
            .toSortedMap()

    private data class Input(val task: String, val pkg: NativeModel,
                             val problem: ByteArray, val world: JsonNode,
                             val manifest: JsonNode, val identity: JsonNode,
                             val hashes: Map<String, String>, val model: JsonNode)

    private fun input(root: Path, task: String): Input {
        val directory = root.resolve(task)
        val names = listOf("$task.zip", "$task.problem.json", "$task.world.json", "identity.json")
        val bytes = names.associateWith { Files.readAllBytes(directory.resolve(it)) }
        val hashes = bytes.mapValues { sha(it.value) }
        val identity = mapper.readTree(bytes.getValue("identity.json"))
        assertEquals(task, identity.path("task").asText())
        for (name in names.filter { it != "identity.json" })
            assertEquals(identity.path("files").path(name).asText(), hashes.getValue(name), name)
        val members = linkedMapOf<String, ByteArray>()
        ZipInputStream(ByteArrayInputStream(bytes.getValue("$task.zip"))).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                require(!entry.isDirectory)
                require(members.put(entry.name, zip.readBytes()) == null)
            }
        }
        val component = if ("component-manifest.json" in members)
            ComponentInput.readBytes(bytes.getValue("$task.zip")) else null
        val manifest = component?.manifest ?: mapper.readTree(members.getValue("manifest.json"))
        for (file in manifest.path("files"))
            assertEquals(file.path("sha256").asText(), sha(members.getValue(file.path("name").asText())))
        assertEquals(identity.path("catalog_version").asText(), manifest.path("catalog").path("version").asText())
        assertEquals(identity.path("catalog_fingerprint").asText(), manifest.path("catalog").path("fingerprint").asText())
        val world = mapper.readTree(bytes.getValue("$task.world.json"))
        if (component != null) {
            assertEquals(task, component.taskId)
            assertArrayEquals(bytes.getValue("$task.problem.json"), component.problemBytes())
            assertEquals(world.path("world"), component.world)
            assertEquals(world.path("expected"), component.expected)
        }
        return Input(task, component?.nativeModel ?: TaskPackage.readBytes(bytes.getValue("$task.zip")),
            bytes.getValue("$task.problem.json"), world,
            manifest, identity, hashes, component?.model ?: mapper.readTree(members.getValue("model.json")))
    }

    private fun problem(input: Input): FlightCrewSchedule {
        val result = mapper.readValue(input.problem, FlightCrewSchedule::class.java)
        checkProblemReferences(result, mapper.readTree(input.problem).path("flightAssignments"))
        return result
    }

    /** Unassigned is a declared native planning value, not a broken object
     * reference. Preserve it only when the corresponding input says null;
     * never repair a missing/dangling employee or replace a noncanonical one.
     * The downstream typed readings and actual constraint checks are unchanged.
     */
    internal fun checkProblemReferences(result: FlightCrewSchedule, declared: JsonNode) {
        assertTrue(declared.isArray, "Native assignments must be an explicit array")
        assertEquals(declared.size(), result.flightAssignments.size, "Native assignment census differs")
        for ((index, assignment) in result.flightAssignments.withIndex()) {
            assertTrue(result.flights.any { it === assignment.flight })
            if (assignment.employee == null) {
                assertTrue(declared[index].has("employee") && declared[index].path("employee").isNull,
                    "Unassigned employee must be explicit null in native input: ${assignment.id}")
            } else {
                assertTrue(result.employees.any { it === assignment.employee },
                    "Employee reference must be the native fact instance: ${assignment.id}")
            }
        }
    }

    private fun knob(input: Input): Map<String, Any> {
        val param = input.pkg.hardParams.single { it.name == "max_duty_duration" }
        assertEquals(780, input.world.path("world").path("params").path(param.name).asInt(-1))
        assertEquals(780.0, param.value)
        assertEquals("duration_minutes", param.unit)
        assertEquals("max", param.relation)
        val resolved = RunThresholds.resolveNative(input.pkg, TaskModes.PROFILE_DEFAULT)
        val applied = resolved.applied.getValue("max_duty_span_min")
        assertEquals(780, applied.value)
        val observed = RunThresholds.withRunModel(resolved.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider().readings().getValue("max_duty_span_min")
        }
        assertEquals(780, observed)
        return mapOf("param" to param.name, "threshold" to applied.threshold,
            "unit" to param.unit, "relation" to param.relation,
            "value" to applied.value, "provider_value" to observed)
    }

    /** No expected values enter the observation path. The world/typed carrier
     * is the declared input; applied and provider values are measured separately.
     * Kept internal for isolated negative/control tests without file I/O.
     */
    internal fun typedKnob(record: String, model: NativeModel, carrier: JsonNode,
                           world: JsonNode, schedule: FlightCrewSchedule): Map<String, Any?> {
        val names = when (record) {
            "families/balanced_workload" -> listOf("workload_spread_threshold")
            "families/crew_composition" -> listOf("crew_size")
            "families/fleet_exclusions" -> listOf("fleet_types")
            "families/reserve_floors" -> listOf("reserve_crews")
            "families/rest_floors" -> listOf("min_rest_home", "min_rest_outstation")
            "families/time_scale" -> listOf("base_tz_offset_min")
            else -> error("Unsupported typed knob measurement record: $record")
        }
        fun cell(name: String): Pair<String, JsonNode> {
            val rules = carrier.path("rules").filter {
                it.path("template_fit").path("family").asText() == record.substringAfter('/') &&
                    it.path("template_fit").path("params").has(name)
            }
            val rule = rules.single()
            val value = rule.path("template_fit").path("params").path(name)
            assertEquals(world.path("params").path(name), value.path("value"), "Declared native/world input differs: $name")
            assertTrue(model.ruleParams[rule.path("id").asText()].orEmpty().any { it.name == name }, "Not decoded: $name")
            return rule.path("id").asText() to value
        }
        fun integer(value: JsonNode): Int {
            require(value.isIntegralNumber && value.canConvertToInt()) { "Expected exact native integer, not ${value.nodeType}" }
            return value.intValue()
        }
        val cells = names.associateWith { cell(it) }
        if (record == "families/time_scale" && integer(cells.getValue("base_tz_offset_min").second.path("value")) != 0) {
            val (rid, value) = cells.getValue("base_tz_offset_min")
            val parameter = model.ruleParams.getValue(rid).single { it.name == "base_tz_offset_min" }
            assertEquals(60, integer(value.path("value")), "This closed refusal profile names the independently declared 60-minute input")
            assertEquals(60.0, parameter.value)
            val failure = assertThrows(TaskModeRefused::class.java) {
                RunThresholds.resolveNative(model, TaskModes.PROFILE_DEFAULT)
            }
            assertTrue(failure.message.orEmpty().contains("base_tz_offset_min") &&
                failure.message.orEmpty().contains("ненулевой сдвиг"), failure.message)
            return mapOf("profile" to "typed-knob-refusal/1", "param" to parameter.name,
                "unit" to parameter.unit, "relation" to parameter.relation,
                "input_value" to integer(value.path("value")), "refused" to true, "reason" to "nonzero_scale_offset")
        }
        val resolved = RunThresholds.resolveNative(model, TaskModes.PROFILE_DEFAULT)
        RunThresholds.checkFacts(schedule, resolved.applied)
        val compiled = LogicCompiler.compileNative(model, resolved, TaskModes.PROFILE_DEFAULT)
        LogicCompiler.refuseIfNeeded(compiled)
        val fitted = LogicCompiler.fit(schedule, resolved.applied, compiled, "A06 component knob-only")
        LogicCompiler.refuseIfNeeded(fitted)
        val readings = RunThresholds.withRunModel(resolved.applied, fitted.plans, packaged = true) {
            val provider = FlightCrewSchedulingConstraintProvider()
            provider.readings() to provider.codeReadings()
        }
        val rows = names.map { name ->
            val (rid, source) = cells.getValue(name)
            val decoded = model.ruleParams.getValue(rid).filter { it.name == name }
            assertTrue(decoded.isNotEmpty())
            val unit = source.path("unit").textValue()
            val relation = source.path("relation").takeUnless { it.isNull }?.textValue()
            val threshold = when (name) {
                "workload_spread_threshold" -> RunThresholds.WORKLOAD_SPREAD_THRESHOLD
                "fleet_types" -> RunThresholds.FLEET_THRESHOLD
                "reserve_crews" -> DefaultProviderRules.RESERVE_FAMILY
                else -> requireNotNull(RunThresholds.knobByName(name)).threshold
            }
            val inputValue: Any
            val value: Any
            val providerValue: Any
            when (name) {
                "fleet_types" -> {
                    assertTrue(source.path("value").isArray)
                    inputValue = source.path("value").map { it.textValue() }.sorted()
                    assertEquals(inputValue, decoded.single().codeValues!!.sorted())
                    value = resolved.applied.getValue(threshold).codes.sorted()
                    providerValue = readings.second.getValue(threshold)
                    assertEquals(inputValue, value)
                }
                "reserve_crews" -> {
                    assertTrue(source.path("value").isObject)
                    inputValue = source.path("value").fields().asSequence().associate { it.key to integer(it.value) }.toSortedMap()
                    assertEquals(inputValue, decoded.associate { requireNotNull(it.qualifier) to requireNotNull(it.value).toInt() }.toSortedMap())
                    value = (inputValue as Map<*, *>).keys.associate { key ->
                        key.toString() to resolved.applied.getValue("$threshold[$key]").value }.toSortedMap()
                    providerValue = inputValue.keys.associate { key ->
                        key.toString() to readings.first.getValue("$threshold[$key]") }.toSortedMap()
                    assertEquals(inputValue, value)
                }
                else -> {
                    inputValue = integer(source.path("value"))
                    assertEquals(inputValue.toDouble(), decoded.single().value)
                    value = resolved.applied.getValue(threshold).value
                    providerValue = readings.first.getValue(threshold)
                    assertEquals(if (name == "workload_spread_threshold") Math.multiplyExact(inputValue, 60) else inputValue, value)
                }
            }
            assertEquals(value, providerValue, "Real provider reading differs from applied: $name")
            mapOf("param" to name, "threshold" to threshold, "unit" to unit, "relation" to relation,
                "input_value" to inputValue, "value_unit" to if (name == "workload_spread_threshold") "duration_minutes" else unit,
                "value" to value, "provider_value" to providerValue)
        }
        return mapOf("profile" to "typed-knob-readings/1", "readings" to rows, "refusals" to emptyList<Any>())
    }

    private fun calendarScore(input: Input, shiftDays: Long): Map<String, Any> {
        val constraint = "Calendar window layover penalty"
        val schedule = problem(input)
        assertEquals(2, schedule.flights.size)
        for (flight in schedule.flights) if (shiftDays != 0L) {
            flight.departureUTCDateTime = requireNotNull(flight.departureUTCDateTime).plusDays(shiftDays)
            flight.arrivalUTCDateTime = requireNotNull(flight.arrivalUTCDateTime).plusDays(shiftDays)
        }
        val resolved = RunThresholds.resolveNative(input.pkg, TaskModes.PROFILE_DEFAULT)
        RunThresholds.checkFacts(schedule, resolved.applied)
        val compiled = LogicCompiler.compileNative(input.pkg, resolved, TaskModes.PROFILE_DEFAULT)
        LogicCompiler.refuseIfNeeded(compiled)
        val fitted = LogicCompiler.fit(schedule, resolved.applied, compiled, "A06 component score-only")
        LogicCompiler.refuseIfNeeded(fitted)
        val weight = input.pkg.weights.getValue(constraint)
        assertTrue(weight > 0)
        // Same actual form-to-fact assignment as PackagedRuns. Only this
        // component weight is projected; no claim of complete mode admission.
        schedule.calendarWindowRules = input.pkg.calendarWindows.toMutableList()
        schedule.constraintWeightOverrides = ConstraintWeightOverrides.of(mapOf(constraint to HardSoftScore.ofSoft(weight)))
        schedule.layoverCostWeights = input.pkg.weights
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
                val total = director.constraintMatchTotalMap.entries.single { it.key.id() == constraint }.value
                val hard = total.score.hardScore()
                val soft = total.score.softScore()
                val matches = total.constraintMatchCount
                assertEquals(0L, hard)
                assertEquals(if (shiftDays == 0L) -weight else 0L, soft)
                assertEquals(if (shiftDays == 0L) 1 else 0, matches)
                mapOf("name" to if (shiftDays == 0L) "original" else "all_flights_plus_one_day",
                    "flight_shift_seconds" to shiftDays * 86400,
                    "constraint" to constraint, "hard_score" to hard,
                    "soft_score" to soft, "matches" to matches, "package_weight" to weight)
            } finally { director.close() }
        }
    }

    private fun form(input: Input): Map<String, Any> {
        assertEquals(listOf("thursday"), input.world.path("world").path("rule_form").path("days").map { it.asText() })
        assertEquals(1, input.pkg.calendarWindows.size)
        assertEquals(setOf(DayOfWeek.THURSDAY), input.pkg.calendarWindows.single().days)
        assertEquals("layover_penalty", input.pkg.calendarWindows.single().action)
        val rules = input.pkg.calendarWindows.map { rule -> mapOf("rule_id" to rule.ruleId,
            "action" to rule.action, "days" to rule.days.map { it.name.lowercase() }.sorted()) }
        return mapOf("form" to "calendar_window_penalties", "rules" to rules,
            "twins" to listOf(calendarScore(input, 0), calendarScore(input, 1)))
    }

    @Test
    fun measureActualNativeKnobAndCalendarForm() {
        val repo = Path.of(property("a06.component.repo")).toAbsolutePath().normalize()
        val inputRoot = Path.of(property("a06.component.inputs")).toAbsolutePath().normalize()
        val output = Path.of(property("a06.component.evidence")).toAbsolutePath().normalize()
        require(!output.startsWith(inputRoot)) { "Evidence may not be written into native input directory" }
        require(!Files.exists(output)) { "Evidence output must be a new immutable file" }
        val commit = property("a06.component.commit")
        require(Regex("[0-9a-f]{40}").matches(commit))
        val selected = property("a06.component.kinds").split(',').toSet()
        require(selected.isNotEmpty() && selected.all { it in setOf("kotlin_knob", "kotlin_form") })
        val beforeInputs = census(inputRoot, inputRoot)
        val beforeSources = sources(repo)
        val beforeClasses = census(repo, repo.resolve("kotlin-solver/target/classes"))
        val probePath = repo.resolve("kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver")
        val beforeProbe = census(repo, probePath) { it.fileName.toString().startsWith("A06TypedComponentProbe") }
        assertTrue(beforeProbe.isNotEmpty())
        try {
            val inputs = mutableListOf<Input>()
            val rows = mutableListOf<Map<String, Any>>()
            if ("kotlin_knob" in selected) {
                val tasks = (System.getProperty("a06.component.knobTasks") ?: "st-duty-1").split(',')
                require(tasks.isNotEmpty() && tasks.distinct().size == tasks.size &&
                    tasks.all { Regex("[A-Za-z0-9][A-Za-z0-9_-]*").matches(it) })
                for (task in tasks) {
                    val actual = input(inputRoot, task)
                    inputs.add(actual)
                    val record = actual.manifest.path("task").path("record").textValue()
                        ?: if (task == "st-duty-1") "families/duty_limits" else error("Typed component record is missing")
                    val got = if (record == "families/duty_limits") knob(actual) else
                        typedKnob(record, actual.pkg, actual.model, actual.world.path("world"), problem(actual))
                    rows.add(mapOf("record" to record, "task" to actual.task,
                        "executor_kind" to "kotlin_knob", "got" to got))
                }
            }
            if ("kotlin_form" in selected) {
                val calendar = input(inputRoot, "st-window-1")
                inputs.add(calendar)
                rows.add(mapOf("record" to "families/calendar_windows", "task" to calendar.task,
                    "executor_kind" to "kotlin_form", "got" to form(calendar)))
            }
            val catalog = mapOf("version" to inputs.first().identity.path("catalog_version").asText(),
                "fingerprint" to inputs.first().identity.path("catalog_fingerprint").asText())
            inputs.forEach { input ->
                assertEquals(catalog["version"], input.identity.path("catalog_version").asText())
                assertEquals(catalog["fingerprint"], input.identity.path("catalog_fingerprint").asText())
            }
            assertEquals(beforeInputs, census(inputRoot, inputRoot))
            assertEquals(beforeSources, sources(repo))
            assertEquals(beforeClasses, census(repo, repo.resolve("kotlin-solver/target/classes")))
            assertEquals(beforeProbe, census(repo, probePath) { it.fileName.toString().startsWith("A06TypedComponentProbe") })
            val envelope = mapOf("schema" to "a06-kotlin-component/1", "scope" to "component-only",
                "catalog" to catalog,
                "producer" to mapOf("class" to javaClass.name, "commit" to commit,
                    "java_version" to System.getProperty("java.version")),
                "sources" to beforeSources,
                "artifacts" to beforeClasses, "probe" to beforeProbe,
                "inputs" to inputs.associate { input -> input.task to mapOf(
                    "zip_sha256" to input.hashes.getValue("${input.task}.zip"),
                    "world_sha256" to input.hashes.getValue("${input.task}.world.json"),
                    "problem_sha256" to input.hashes.getValue("${input.task}.problem.json"),
                    "identity_sha256" to input.hashes.getValue("identity.json")) },
                "observations" to rows,
                "guards" to mapOf("inputs_unchanged" to true, "sources_unchanged" to true,
                    "artifacts_unchanged" to true))
            Files.write(output, mapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(envelope),
                StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)
        } finally {
            RunThresholds.clearForTests()
            LastProviderReadings.clearForTests()
            assertEquals(beforeInputs, census(inputRoot, inputRoot))
        }
    }
}
