package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.config.constructionheuristic.ConstructionHeuristicPhaseConfig
import ai.timefold.solver.core.config.heuristic.selector.common.SelectionOrder
import ai.timefold.solver.core.config.heuristic.selector.entity.EntitySelectorConfig
import ai.timefold.solver.core.config.heuristic.selector.move.generic.ChangeMoveSelectorConfig
import ai.timefold.solver.core.config.heuristic.selector.value.ValueSelectorConfig
import ai.timefold.solver.core.config.localsearch.LocalSearchPhaseConfig
import ai.timefold.solver.core.config.localsearch.decider.acceptor.AcceptorType
import ai.timefold.solver.core.config.localsearch.decider.acceptor.LocalSearchAcceptorConfig
import ai.timefold.solver.core.config.localsearch.decider.forager.LocalSearchForagerConfig
import ai.timefold.solver.core.config.localsearch.decider.forager.LocalSearchPickEarlyType
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.impl.solver.AbstractSolver
import com.fasterxml.jackson.core.JsonParser
import com.fasterxml.jackson.core.JsonGenerator
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.SerializationFeature
import com.fasterxml.jackson.databind.module.SimpleModule
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.rest.FlightCrewSchedulingResource
import org.junit.jupiter.api.Test
import java.math.BigDecimal
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.CREATE_NEW
import java.nio.file.StandardOpenOption.WRITE
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID

/** Explicitly selected LOCAL-ONLY qualification. Not named *Test: ordinary
 * test discovery must never start these solves. A separate, independently
 * pinned request authorizes only the closed synthetic task pairs below.
 *
 * Component ZIPs stay non-executable data. There is no LoadedTaskPackage,
 * ResolvedTaskMode, service, HTTP endpoint or PackagedRuns registration here.
 * Actual run evidence is a separate schema, never component-score or live
 * customer admission. Catalog expectations are NOT read by the measurements.
 */
class A06QualificationRunProbe {
    companion object {
        const val SCOPE = "catalog-executor-qualification/local-only"
        const val SOURCE = "offline-qualification-run"
        const val PROBE = "org.acme.flighcrewscheduling.solver.A06QualificationRunProbe"
        const val METRIC_RECORD = "criteria/credit_hours_spread"
        const val METRIC_OBJECTIVE = "O-STAND-credit_hours_spread"
        const val MAX_CASES = 16
        val PAIRS = linkedMapOf(
            METRIC_RECORD to "chs",
            "families/balanced_workload" to "spread-native",
            "families/calendar_windows" to "window",
            "families/crew_composition" to "crew",
            "families/crew_unavailability" to "unavail-native",
            "families/fleet_exclusions" to "fleet",
            "families/reserve_floors" to "reserve",
            "families/rest_floors" to "rest")
        val PYTHON_SOURCES = listOf("cdpm/component_input.py", "cdpm/export_task_package.py",
            "cdpm/model.py", "cdpm/gates/gate_task_package.py", "vmp/stand_adapters.py",
            "vmp/stand_solver.py", "vmp/stand_tasks.py", "vmp/qualification_run.py",
            "vmp/kotlin_tables.py", "template_catalog/stand_contracts.py")
        private val mapper = ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .registerModule(SimpleModule().addSerializer(HardSoftScore::class.java, ToStringSerializer.instance))
            .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
        private val hash = Regex("[a-f0-9]{64}")
        private val commitPattern = Regex("[a-f0-9]{40}")

        internal fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
            .digest(bytes).joinToString("") { "%02x".format(it) }
        private fun keys(node: JsonNode) = node.fieldNames().asSequence().toSet()
        private fun exact(node: JsonNode, fields: Set<String>, label: String) {
            require(node.isObject && keys(node) == fields) { "$label has missing or unknown fields" }
        }
        private fun string(node: JsonNode, label: String): String {
            require(node.isTextual && node.asText().isNotBlank() && node.asText().trim() == node.asText()) {
                "$label must be exact nonempty text"
            }
            return node.asText()
        }
        private fun integer(node: JsonNode, wanted: Int, label: String) {
            require(node.isIntegralNumber && node.canConvertToInt() && node.intValue() == wanted) {
                "$label must be exactly $wanted"
            }
        }
        private fun json(bytes: ByteArray): JsonNode = requireNotNull(mapper.readTree(bytes)) { "empty JSON" }
        private fun property(name: String) = requireNotNull(System.getProperty("a06.qualification.$name")) {
            "Explicit -Da06.qualification.$name is required; no default local run"
        }

        data class Case(val record: String, val task: String, val role: String,
                        val controlFor: String?, val zipName: String, val zipSha: String,
                        val executorKind: String, val mode: String, val objectiveId: String?)
        data class Request(val document: JsonNode, val fingerprint: String, val challenge: String,
                           val catalogVersion: String, val catalogFingerprint: String,
                           val cases: List<Case>)

        internal fun parseRequest(bytes: ByteArray, expectedSha: String, challenge: String): Request {
            require(hash.matches(expectedSha) && sha(bytes) == expectedSha) { "request SHA differs" }
            require(UUID.fromString(challenge).toString() == challenge) { "noncanonical challenge" }
            val root = json(bytes)
            exact(root, setOf("schema", "scope", "catalog", "challenge", "limits", "cases"), "request")
            require(root.path("schema").asText() == "a06-qualification-request/2" &&
                root.path("scope").asText() == SCOPE) { "not an explicit local qualification request" }
            require(string(root.path("challenge"), "challenge") == challenge) { "challenge differs" }
            val catalog = root.path("catalog")
            exact(catalog, setOf("version", "fingerprint"), "catalog")
            val version = string(catalog.path("version"), "catalog version")
            val fingerprint = string(catalog.path("fingerprint"), "catalog fingerprint")
            require(hash.matches(fingerprint)) { "invalid catalog SHA" }
            val cases = root.path("cases")
            require(cases.isArray && cases.size() in 2..MAX_CASES && cases.size() % 2 == 0) { "bounded task-pair list required" }
            val limits = root.path("limits")
            exact(limits, setOf("spent_limit_ms", "step_limit", "seed", "max_tasks"), "limits")
            integer(limits.path("spent_limit_ms"), 1000, "spent_limit_ms")
            integer(limits.path("step_limit"), 1, "step_limit")
            integer(limits.path("seed"), 0, "seed")
            integer(limits.path("max_tasks"), cases.size(), "max_tasks")
            val parsed = cases.map { c ->
                exact(c, setOf("record", "task", "role", "control_for", "component_zip", "sha256",
                    "executor_kind", "mode", "objective_id"), "case")
                val record = string(c.path("record"), "record")
                val stem = requireNotNull(PAIRS[record]) { "record is outside closed qualification allowlist" }
                val task = string(c.path("task"), "task")
                val role = string(c.path("role"), "role")
                val original = "st-$stem-1"
                val healthy = "st-$stem-healthy-1"
                val control = c.path("control_for")
                require((task == original && role == "violation" && control.isNull) ||
                    (task == healthy && role == "healthy" && control.isTextual && control.asText() == original)) {
                    "case is not one of the declared violation/healthy roles"
                }
                val name = string(c.path("component_zip"), "component_zip")
                require(name == "$task.zip") { "component input must be the exact task basename" }
                val zipSha = string(c.path("sha256"), "input sha256")
                require(hash.matches(zipSha)) { "invalid component SHA" }
                val metric = record == METRIC_RECORD
                val kind = string(c.path("executor_kind"), "executor_kind")
                val mode = string(c.path("mode"), "mode")
                val objective = c.path("objective_id")
                require(kind == (if (metric) "kotlin_metric" else "kotlin_constraint") &&
                    mode == (if (metric) MODE_OPTIMIZATION else MODE_FEASIBILITY_SOFT) &&
                    (if (metric) objective.isTextual && objective.asText() == METRIC_OBJECTIVE else objective.isNull)) {
                    "qualification executor kind, mode or selected objective differs from the finite case contract"
                }
                Case(record, task, role, control.takeIf { it.isTextual }?.asText(), name, zipSha,
                    kind, mode, objective.takeIf { it.isTextual }?.asText())
            }
            val order = parsed.map { it.record + "/" + it.task }
            require(order == order.sorted() && order.size == order.toSet().size) { "cases must be exact, sorted and unique" }
            parsed.groupBy { it.record }.forEach { (_, pair) ->
                require(pair.size == 2 && pair.map { it.role }.toSet() == setOf("violation", "healthy")) {
                    "both independently declared control tasks are mandatory"
                }
            }
            return Request(root, expectedSha, challenge, version, fingerprint, parsed)
        }

        private fun realDirectory(path: Path): Path {
            require(Files.isDirectory(path, NOFOLLOW_LINKS) && !Files.isSymbolicLink(path)) { "not a plain directory: $path" }
            return path.toRealPath()
        }
        private fun regular(root: Path, path: Path): Path {
            require(Files.isRegularFile(path, NOFOLLOW_LINKS) && !Files.isSymbolicLink(path)) { "not a plain file: $path" }
            val actual = path.toRealPath()
            require(actual.startsWith(root)) { "file escapes approved root: $path" }
            var parent = path.parent
            while (parent != null && parent.startsWith(root)) {
                require(!Files.isSymbolicLink(parent)) { "symlink parent: $parent" }
                if (parent == root) break
                parent = parent.parent
            }
            return actual
        }
        private fun walk(root: Path, directory: Path, accept: (Path) -> Boolean = { true }): List<Path> {
            require(Files.isDirectory(directory, NOFOLLOW_LINKS)) { "missing identity directory: $directory" }
            return Files.walk(directory).use { paths ->
                paths.peek { require(!Files.isSymbolicLink(it)) { "identity census contains a symlink" } }
                    .filter { Files.isRegularFile(it, NOFOLLOW_LINKS) && accept(it) }
                    .map { regular(root, it) }.sorted().toList()
            }
        }
        private fun census(root: Path, paths: List<Path>): Map<String, String> {
            require(paths.isNotEmpty()) { "empty identity census" }
            return paths.associate { p ->
                val actual = regular(root, p)
                root.relativize(actual).toString().replace('\\', '/') to sha(Files.readAllBytes(actual))
            }.toSortedMap().also { require(it.size == paths.size) { "duplicate identity path" } }
        }
        private fun identities(repo: Path, javaHome: Path): Map<String, Map<String, String>> {
            val kotlin = repo.resolve("kotlin-solver")
            val source = "kotlin-solver/src/test/kotlin/" + PROBE.replace('.', '/') + ".kt"
            val sources = walk(repo, kotlin.resolve("src/main")) { it.toString().endsWith(".kt") } +
                (listOf("kotlin-solver/pom.xml", source) + PYTHON_SOURCES).map { repo.resolve(it) }
            val probe = walk(repo, kotlin.resolve("target/test-classes/org/acme/flighcrewscheduling/solver")) {
                it.fileName.toString().startsWith("A06QualificationRunProbe") && it.toString().endsWith(".class")
            }
            return linkedMapOf("sources" to census(repo, sources),
                "artifacts" to census(repo, walk(repo, kotlin.resolve("target/classes"))),
                "probe" to census(repo, probe),
                "config" to census(repo, walk(repo, kotlin.resolve("src/main/resources"))),
                "jdk" to census(javaHome, listOf("release", "bin/java.exe", "lib/modules").map { javaHome.resolve(it) }))
        }
        internal fun qualifiedTermination(value: Map<String, Any?>) {
            require(value["search_started"] == true) { "qualification inconclusive: search phase did not start" }
            require(value["reproducible"] == true) { "qualification inconclusive: solver termination is not reproducible" }
            val kind = value["kind"]
            require(kind in setOf(RunTermination.STEP_COUNT, RunTermination.SEARCH_EXHAUSTED,
                RunTermination.WALL_CLOCK_SAFETY)) { "qualification termination is not allowed: $kind" }
            if (kind == RunTermination.WALL_CLOCK_SAFETY) require(value["search_steps"] == 0) {
                "qualification inconclusive: wall-clock safety interrupted a search step"
            }
            val elapsed = (value["elapsed_ms"] as? Number)?.toLong()
            require(elapsed != null && elapsed in 0..5000) { "qualification solve exceeded resource boundary" }
        }
        /** Log the actual lifecycle before admission, including a rejected run.
         * This is diagnostic only, not a qualification envelope or a PASS.
         * Existing partial sidecars are deliberately preserved on any failure. */
        internal fun recordAndQualifyTermination(task: String, runId: String,
                                                value: Map<String, Any?>,
                                                record: (String) -> Unit = { println(it) }) {
            // Escape non-ASCII so Windows stdout code pages cannot lose the
            // Russian human-readable termination words in the retained log.
            val diagnostic = mapper.writer().with(JsonGenerator.Feature.ESCAPE_NON_ASCII)
                .writeValueAsString(linkedMapOf(
                "schema" to "a06-qualification-termination-diagnostic/1",
                "task" to task, "run_id" to runId, "termination" to value))
            record(diagnostic)
            try {
                qualifiedTermination(value)
            } catch (e: IllegalArgumentException) {
                throw IllegalArgumentException("${e.message}; actual lifecycle: $diagnostic", e)
            }
        }
        internal fun checkRuntimeLimits(maxHeapBytes: Long, processors: Int) {
            require(maxHeapBytes in 1..(1536L * 1024 * 1024) && processors in 1..2) {
                "qualification requires the test JVM itself bounded by -Xmx1536m -XX:ActiveProcessorCount=2; MAVEN_OPTS alone is insufficient"
            }
        }
        internal fun solverConfig(): SolverConfig {
            // Test-only qualification profile. The production solver's default
            // random neighbourhood is infinite; when every doable move is
            // rejected, a step can end only at the wall-clock guard. A finite
            // ORIGINAL change neighbourhood instead visits each native employee
            // (including the domain's unassigned value) exactly once per seat.
            // Hill climbing and a full, nonrandom forager may exhaust without
            // an accepted move. This is genuine search exhaustion, not a fake
            // step, clock relabel or change of any native score constraint.
            val moves = ChangeMoveSelectorConfig()
                .withSelectionOrder(SelectionOrder.ORIGINAL)
                .withEntitySelectorConfig(EntitySelectorConfig(FlightAssignment::class.java)
                    .withSelectionOrder(SelectionOrder.ORIGINAL))
                .withValueSelectorConfig(ValueSelectorConfig("employee")
                    .withSelectionOrder(SelectionOrder.ORIGINAL))
            val search = LocalSearchPhaseConfig()
                .withAcceptorConfig(LocalSearchAcceptorConfig()
                    .withAcceptorTypeList(listOf(AcceptorType.HILL_CLIMBING)))
                .withMoveSelectorConfig(moves)
                .withForagerConfig(LocalSearchForagerConfig()
                    .withPickEarlyType(LocalSearchPickEarlyType.NEVER)
                    .withAcceptedCountLimit(Int.MAX_VALUE)
                    .withBreakTieRandomly(false))
            val cfg = SolverConfig.createFromXmlResource("solverConfig.xml")
                .withPhases(ConstructionHeuristicPhaseConfig(), search)
            return FlightCrewSchedulingResource.packagedSolverConfig(cfg, seed = 0,
                deterministic = true, stepLimit = 1, spentLimitSeconds = 1)
        }
        data class NativeQualification(val plan: NativeExecutionPlan, val objective: ResolvedObjective?)
        internal fun nativePlan(native: NativeModel, mode: String = MODE_FEASIBILITY_SOFT,
                                objectiveId: String? = null): NativeQualification {
            require(native.unmappedRules.isEmpty()) { "qualification refuses unmapped native soft rules" }
            val objective = when (mode) {
                MODE_FEASIBILITY_SOFT -> {
                    require(objectiveId == null && native.objectiveDetails.values.all { it.isEmpty() }) {
                        "constraint qualification cannot silently ignore or select a native objective"
                    }
                    null
                }
                MODE_OPTIMIZATION -> {
                    require(objectiveId == METRIC_OBJECTIVE) { "qualification objective is outside the finite allowlist" }
                    require(native.objectiveDetails.filterKeys { it != "rostering" }.values.all { it.isEmpty() }) {
                        "qualification refuses an objective outside its declared task zone"
                    }
                    val obj = native.objectiveDetails["rostering"].orEmpty().singleOrNull()
                        ?: throw IllegalArgumentException("metric qualification requires exactly one native rostering objective")
                    require(obj.id == objectiveId && obj.metric == "credit_hours_spread" &&
                        obj.rate?.compareTo(BigDecimal.ONE) == 0) {
                        "metric qualification requires the exact native objective, metric and explicit rate 1; no legacy rate"
                    }
                    TaskModes.resolveNativeObjective(obj, native, "rostering")
                }
                else -> throw IllegalArgumentException("qualification mode is outside its finite contract")
            }
            val plan = NativeExecutionPlan.resolve(native, TaskModes.PROFILE_DEFAULT, useWeights = true, objective = objective)
            require(plan.thresholds.ignored.isEmpty()) { "qualification refuses ignored native thresholds" }
            require(plan.logic.ignored.isEmpty()) { "qualification refuses ignored native logic" }
            return NativeQualification(plan, objective)
        }
        internal fun solutionBytes(value: FlightCrewSchedule): ByteArray =
            mapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(value)
    }

    private val outputFiles = mutableListOf<Map<String, Any>>()
    private fun sidecar(output: Path, task: String, kind: String, bytes: ByteArray): Map<String, Any> {
        val name = "$task.$kind.json"
        require(!Files.exists(output.resolve(name), NOFOLLOW_LINKS)) { "output sidecar already exists" }
        Files.write(output.resolve(name), bytes, CREATE_NEW, WRITE)
        return linkedMapOf<String, Any>("path" to name, "sha256" to sha(bytes), "bytes" to bytes.size)
            .also { outputFiles.add(it) }
    }
    private fun bytes(value: Any): ByteArray = mapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(value)
    private fun problem(input: LoadedComponentInput): FlightCrewSchedule {
        val p = mapper.readValue(input.problemBytes(), FlightCrewSchedule::class.java)
        require(p.flights.isNotEmpty() && p.employees.isNotEmpty() && p.flightAssignments.isNotEmpty()) { "empty qualification world" }
        require(p.flights.size <= 8 && p.employees.size <= 8 && p.flightAssignments.size <= 32) { "synthetic world resource ceiling exceeded" }
        p.flightAssignments.forEach { a ->
            require(p.flights.any { it === a.flight }) { "flight identity is not native" }
            require(a.employee == null || p.employees.any { it === a.employee }) { "employee identity is not native" }
            require(a.chainId == null && !a.deadhead) { "qualification v2 has no external pairing/deadhead authority" }
        }
        p.airports = p.airports.sortedBy { it.code }.toMutableList()
        p.employees = p.employees.sortedBy { it.id }.toMutableList()
        p.flights = p.flights.sortedBy { it.flightNumber }.toMutableList()
        p.flightAssignments = p.flightAssignments.sortedBy { it.id }.toMutableList()
        return p
    }

    private fun measure(case: Case, input: LoadedComponentInput, output: Path): Map<String, Any?> {
        val native = input.nativeModel
        val resolved = nativePlan(native, case.mode, case.objectiveId)
        val plan = resolved.plan
        val p = problem(input)
        val history = HistoryAssumption.applyZero(p)
        val coverage = RunThresholds.checkFacts(p, plan.thresholds.applied)
        val fitted = LogicCompiler.fit(p, plan.thresholds.applied, plan.logic, "qualification:" + case.task)
        LogicCompiler.refuseIfNeeded(fitted)
        require(fitted.ignored.isEmpty()) { "qualification fit left ignored native rules" }
        // Same synchronous facts/fit -> deadhead -> native context sequence as
        // PackagedRuns. These declared cases have no deadhead seats or rules.
        require(DeadheadSeats.ensure(p, plan.thresholds.applied, explicitSeats = null, allowByDefault = false) == 0) {
            "qualification v2 cannot add undeclared deadhead seats"
        }
        NativeExecutionSupport.prepare(p, plan.thresholds.applied, plan.effectiveWeights,
            plan.weightOverrides, native.calendarWindows)
        val cfg = solverConfig()
        val (factory, readings) = RunThresholds.withRunModel(plan.thresholds.applied, fitted.plans, packaged = true) {
            LastProviderReadings.clearForTests()
            val f = SolverFactory.create<FlightCrewSchedule>(cfg)
            f to requireNotNull(LastProviderReadings.snapshotWithCodes()) { "actual provider readings missing" }
        }
        require(readings.profile == TaskModes.PROFILE_DEFAULT) { "unexpected actual provider profile" }
        val fit = fitted.copy(registered = LogicCompiler.registeredIds(factory))
        val context = linkedMapOf<String, Any?>(
            "input_fingerprint" to input.fingerprint, "profile" to TaskModes.PROFILE_DEFAULT,
            "mode" to case.mode, "objective" to resolved.objective?.snapshot(),
            "applied" to RunThresholds.snapshot(plan.thresholds, readings.readings), "coverage" to coverage,
            "fit" to fit.snapshot(), "provider_readings" to readings.readings,
            "provider_code_readings" to readings.codeReadings,
            "provider_policy_readings" to readings.policyReadings.mapValues { it.value.snapshot() },
            "applied_weights" to plan.appliedWeights, "effective_weights" to plan.effectiveWeights,
            "literals_disabled" to plan.literalsDisabled, "calendar_rules" to native.calendarWindows,
            "history" to history)
        val contextRef = sidecar(output, case.task, "execution-context", bytes(context))
        val solver = factory.buildSolver()
        val steps = RunTermination.StepCounter()
        (solver as? AbstractSolver<FlightCrewSchedule>
            ?: error("actual solver cannot report lifecycle")) .addPhaseLifecycleListener(steps)
        val runId = UUID.randomUUID().toString()
        val startedAt = Instant.now()
        val t0 = System.nanoTime()
        // The only solve call in this harness. No expected value is consulted.
        val solved = solver.solve(p)
        val elapsedMs = (System.nanoTime() - t0) / 1_000_000
        val completedAt = Instant.now()
        val termination = RunTermination.reading(cfg.terminationConfig, true,
            steps.searchSteps, steps.unimprovedStreak, elapsedMs, solver.isTerminateEarly(),
            steps.searchStarted, steps.constructionSteps)
        recordAndQualifyTermination(case.task, runId, termination)
        val solvedScore = requireNotNull(solved.score) { "actual solve returned no score" }
        val solutionBytes = solutionBytes(solved)
        val solutionRef = sidecar(output, case.task, "solution", solutionBytes)
        NativeExecutionSupport.prepare(solved, plan.thresholds.applied, plan.effectiveWeights,
            plan.weightOverrides, native.calendarWindows)
        val solutionBreakdown = NativeExecutionSupport.breakdown(factory, solved, input.fingerprint, fitted.plans)
        require(solutionBreakdown["score"] == solvedScore.toString()) { "solved result and same-context score disagree" }
        val solvedRef = sidecar(output, case.task, "solved-solution-breakdown", bytes(linkedMapOf(
            "execution_context_sha256" to contextRef.getValue("sha256"),
            "score_subject_sha256" to solutionRef.getValue("sha256"), "breakdown" to solutionBreakdown)))
        // Existing stand_solver deliberately measures original assignments,
        // not the optimized solution. Decode a fresh exact original input.
        val original = problem(input)
        HistoryAssumption.applyZero(original)
        RunThresholds.checkFacts(original, plan.thresholds.applied)
        NativeExecutionSupport.prepare(original, plan.thresholds.applied, plan.effectiveWeights,
            plan.weightOverrides, native.calendarWindows)
        val declaredBreakdown = NativeExecutionSupport.breakdown(factory, original, input.fingerprint, fitted.plans)
        val declaredRef = sidecar(output, case.task, "declared-world-breakdown", bytes(linkedMapOf(
            "execution_context_sha256" to contextRef.getValue("sha256"),
            "score_subject_sha256" to sha(input.problemBytes()), "breakdown" to declaredBreakdown)))
        return linkedMapOf("record" to case.record, "task" to case.task, "run_id" to runId,
            "started_at" to startedAt.toString(), "completed_at" to completedAt.toString(),
            "input_fingerprint" to input.fingerprint,
            "solve" to mapOf("called" to true, "completed" to true, "exception" to null),
            "termination" to termination, "execution_context" to contextRef, "solution" to solutionRef,
            "declared_world_breakdown" to declaredRef, "solved_solution_breakdown" to solvedRef)
    }

    @Test
    fun explicitlyRequestedLocalQualificationRuns() {
        checkRuntimeLimits(Runtime.getRuntime().maxMemory(), Runtime.getRuntime().availableProcessors())
        val repo = realDirectory(Path.of(property("repo")).toAbsolutePath().normalize())
        val inputsRoot = realDirectory(Path.of(property("inputs")).toAbsolutePath().normalize())
        val requestPath = Path.of(property("request")).toAbsolutePath().normalize()
        require(Files.isRegularFile(requestPath, NOFOLLOW_LINKS) && !Files.isSymbolicLink(requestPath))
        val requestBytes = Files.readAllBytes(requestPath)
        val request = parseRequest(requestBytes, property("requestSha256"), property("challenge"))
        val commit = property("commit")
        require(commitPattern.matches(commit)) { "full producer commit required" }
        require(!TaskPackage.processPackagePresent()) { "process package would contaminate local qualification" }
        val output = Path.of(property("output")).toAbsolutePath().normalize()
        require(!output.startsWith(inputsRoot) && !Files.exists(output, NOFOLLOW_LINKS)) { "fresh output outside input root required" }
        val outputParent = realDirectory(requireNotNull(output.parent))
        require(outputParent.resolve(output.fileName) == output) { "output parent is not canonical" }
        val javaHome = realDirectory(Path.of(System.getProperty("java.home")).toAbsolutePath().normalize())
        val paths = request.cases.associate { c -> c.task to regular(inputsRoot, inputsRoot.resolve(c.task).resolve(c.zipName)) }
        fun inputCensus() = paths.mapValues { sha(Files.readAllBytes(it.value)) }.toSortedMap()
        val beforeInputs = inputCensus()
        request.cases.forEach { require(beforeInputs.getValue(it.task) == it.zipSha) { "input differs from independently pinned request" } }
        val decoded = request.cases.associate { c ->
            c.task to ComponentInput.readBytes(Files.readAllBytes(paths.getValue(c.task))).also { input ->
                require(input.record == c.record && input.taskId == c.task) { "native task was relabeled" }
                require(input.catalogVersion == request.catalogVersion && input.catalogFingerprint == request.catalogFingerprint) {
                    "native catalog identity differs"
                }
            }
        }
        val before = identities(repo, javaHome)
        Files.createDirectory(output)
        try {
            val observations = request.cases.map { measure(it, decoded.getValue(it.task), output) }
            val afterInputs = inputCensus()
            val after = identities(repo, javaHome)
            require(beforeInputs == afterInputs && before == after) { "qualification identities changed during runs" }
            require(Files.readAllBytes(requestPath).contentEquals(requestBytes)) { "request changed during runs" }
            require(outputFiles.map { it["path"] }.toSet().size == request.cases.size * 4)
            val files = outputFiles.sortedBy { it.getValue("path").toString() }
            require(census(output, walk(output, output)) == files.associate { it.getValue("path").toString() to it.getValue("sha256").toString() }) {
                "output census differs"
            }
            val envelope = linkedMapOf("schema" to "a06-kotlin-qualification-run/2", "scope" to SCOPE,
                "proof_source" to SOURCE, "catalog" to request.document.path("catalog"),
                "challenge" to request.challenge, "request_sha256" to request.fingerprint,
                "producer" to mapOf("class" to PROBE, "commit" to commit, "java_version" to System.getProperty("java.version")),
                "identities" to mapOf("before" to before, "after" to after),
                "inputs" to mapOf("before" to beforeInputs, "after" to afterInputs),
                "observations" to observations, "files" to files)
            Files.write(output.resolve("qualification-run.json"), bytes(envelope), CREATE_NEW, WRITE)
            println("A06 LOCAL qualification: ${observations.size} actual bounded solves recorded; importer/oracle acceptance is separate")
        } finally {
            RunThresholds.clearForTests()
            LastProviderReadings.clearForTests()
            require(beforeInputs == inputCensus()) { "qualification input changed" }
            require(before == identities(repo, javaHome)) { "qualification executable identity changed" }
        }
    }
}
