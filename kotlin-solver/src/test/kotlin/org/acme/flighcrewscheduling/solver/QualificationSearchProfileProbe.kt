package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.impl.solver.AbstractSolver
import com.fasterxml.jackson.databind.ObjectMapper
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.rest.FlightCrewSchedulingResource
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.util.UUID

/** Explicitly selected, offline profile regression; NOT an ordinary *Test and
 * NOT catalog evidence. Reads only two independently prepared tiny inputs.
 * At most five real solves, each bounded by the unchanged D1 1-step/1-second
 * limits and the JVM heap/CPU gate. No HTTP, Quarkus service, run registry,
 * generated qualification envelope or writes to the source/input archive. */
class QualificationSearchProfileProbe {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private data class Outcome(val termination: Map<String, Any?>, val score: String,
                               val assignments: Map<String, String?>)

    @AfterEach fun clean() { RunThresholds.clearForTests(); LastProviderReadings.clearForTests() }

    private fun input(task: String): Pair<Path, ByteArray> {
        A06QualificationRunProbe.checkRuntimeLimits(Runtime.getRuntime().maxMemory(),
            Runtime.getRuntime().availableProcessors())
        require(!TaskPackage.processPackagePresent()) { "process package contaminates profile regression" }
        require(task in setOf("st-window-1", "st-crew-healthy-1"))
        val root = Path.of(requireNotNull(System.getProperty("a06.profile.inputs"))).toRealPath()
        val path = root.resolve(task).resolve("$task.zip")
        require(Files.isRegularFile(path, NOFOLLOW_LINKS) && !Files.isSymbolicLink(path))
        require(path.toRealPath().startsWith(root))
        return path to Files.readAllBytes(path)
    }

    private fun solve(task: String, raw: ByteArray, cfg: SolverConfig): Outcome {
        val input = ComponentInput.readBytes(raw)
        require(input.taskId == task && input.record == if (task == "st-window-1")
            "families/calendar_windows" else "families/crew_composition")
        require(input.catalogFingerprint == requireNotNull(System.getProperty("a06.profile.catalogFingerprint")))
        val native = input.nativeModel
        val resolved = A06QualificationRunProbe.nativePlan(native)
        val plan = resolved.plan
        val p = mapper.readValue(input.problemBytes(), FlightCrewSchedule::class.java)
        require(p.employees.size == if (task == "st-window-1") 1 else 2)
        require(p.flights.size in 1..2 && p.flightAssignments.size in 1..2)
        p.employees = p.employees.sortedBy { it.id }.toMutableList()
        p.flights = p.flights.sortedBy { it.flightNumber }.toMutableList()
        p.flightAssignments = p.flightAssignments.sortedBy { it.id }.toMutableList()
        HistoryAssumption.applyZero(p)
        RunThresholds.checkFacts(p, plan.thresholds.applied)
        val fit = LogicCompiler.fit(p, plan.thresholds.applied, plan.logic, "profile-regression:$task")
        LogicCompiler.refuseIfNeeded(fit)
        require(fit.ignored.isEmpty())
        NativeExecutionSupport.prepare(p, plan.thresholds.applied, plan.effectiveWeights,
            plan.weightOverrides, native.calendarWindows)
        val factory = RunThresholds.withRunModel(plan.thresholds.applied, fit.plans, packaged = true) {
            SolverFactory.create<FlightCrewSchedule>(cfg)
        }
        val solver = factory.buildSolver()
        val counter = RunTermination.StepCounter()
        (solver as AbstractSolver<FlightCrewSchedule>).addPhaseLifecycleListener(counter)
        val start = System.nanoTime()
        val solved = solver.solve(p)
        val elapsedMs = (System.nanoTime() - start) / 1_000_000
        val termination = RunTermination.reading(cfg.terminationConfig, true,
            counter.searchSteps, counter.unimprovedStreak, elapsedMs, solver.isTerminateEarly(),
            counter.searchStarted, counter.constructionSteps)
        println("PROFILE-REGRESSION task=$task actual_termination=" + mapper.writeValueAsString(termination))
        return Outcome(termination, requireNotNull(solved.score).toString(),
            solved.flightAssignments.associate { it.id to it.employee?.id })
    }

    private fun assertFinite(task: String, value: Outcome) {
        A06QualificationRunProbe.recordAndQualifyTermination(task, UUID.randomUUID().toString(), value.termination)
        assertTrue(value.termination["kind"] in setOf(RunTermination.STEP_COUNT, RunTermination.SEARCH_EXHAUSTED),
            "finite qualification must end before its clock safety: ${value.termination}")
        assertEquals(true, value.termination["search_started"], value.termination.toString())
        assertEquals(true, value.termination["reproducible"], value.termination.toString())
        assertTrue((value.termination["search_steps"] as Int) in 0..1, value.termination.toString())
    }

    @Test fun calendarSingletonPreservesNativeScoreAndEndsByFiniteSearchInsteadOfClock() {
        val (path, raw) = input("st-window-1")
        try {
            // Exact previous configuration, not a fabricated clock reading.
            val old = FlightCrewSchedulingResource.packagedSolverConfig(
                SolverConfig.createFromXmlResource("solverConfig.xml"), seed = 0,
                deterministic = true, stepLimit = 1, spentLimitSeconds = 1)
            val baseline = solve("st-window-1", raw, old)
            assertEquals(RunTermination.WALL_CLOCK_SAFETY, baseline.termination["kind"], baseline.termination.toString())
            assertEquals(true, baseline.termination["search_started"], baseline.termination.toString())
            assertEquals(false, baseline.termination["reproducible"], baseline.termination.toString())
            assertThrows<IllegalArgumentException> {
                A06QualificationRunProbe.recordAndQualifyTermination("st-window-1:old-random-profile",
                    UUID.randomUUID().toString(), baseline.termination)
            }
            val first = solve("st-window-1", raw, A06QualificationRunProbe.solverConfig())
            val second = solve("st-window-1", raw, A06QualificationRunProbe.solverConfig())
            assertFinite("st-window-1:first-finite", first)
            assertFinite("st-window-1:second-finite", second)
            assertEquals(baseline.score, first.score)
            assertEquals(baseline.assignments, first.assignments)
            assertEquals(first.score, second.score)
            assertEquals(first.assignments, second.assignments)
            assertEquals(first.termination["search_steps"], second.termination["search_steps"])
        } finally {
            assertArrayEquals(raw, Files.readAllBytes(path), "profile test must not rewrite the declared input")
        }
    }

    @Test fun ordinaryTwoEmployeeCaseStillRunsNativeSearchReproducibly() {
        val (path, raw) = input("st-crew-healthy-1")
        try {
            val first = solve("st-crew-healthy-1", raw, A06QualificationRunProbe.solverConfig())
            val second = solve("st-crew-healthy-1", raw, A06QualificationRunProbe.solverConfig())
            assertFinite("st-crew-healthy-1:first-finite", first)
            assertFinite("st-crew-healthy-1:second-finite", second)
            assertEquals(first.score, second.score)
            assertEquals(first.assignments, second.assignments)
            assertEquals(first.termination["search_steps"], second.termination["search_steps"])
        } finally {
            assertArrayEquals(raw, Files.readAllBytes(path), "profile test must not rewrite the declared input")
        }
    }
}
