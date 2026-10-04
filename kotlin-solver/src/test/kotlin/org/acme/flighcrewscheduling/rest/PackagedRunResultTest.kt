package org.acme.flighcrewscheduling.rest

import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.acme.flighcrewscheduling.solver.LoadedTaskPackage
import org.acme.flighcrewscheduling.solver.PackagedRuns
import org.acme.flighcrewscheduling.solver.RunRecord
import org.acme.flighcrewscheduling.solver.TaskModeRefused
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertSame
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

/**
 * H11-K-STATE-1 (рецензия GPT-031 от 06.09.2026): готовность ответа
 * `run/{id}/result` судится СОСТОЯНИЕМ записи прогона, не наличием
 * решения. В жизненном цикле [PackagedRuns.start] решение публикуется
 * в запись раньше отчёта ночёвок и раньше «готов»; при падении после
 * публикации решение остаётся. Прежний гейт «solution != null» в этом
 * окне отдавал решение с null-отчётом при «считает» и решение при «упал».
 *
 * Стенд — записи в достижимых промежуточных состояниях, без решателя:
 * ровно те снимки, которые прямой GET застаёт между строками цикла.
 */
class PackagedRunResultTest {

    private fun pkg(): LoadedTaskPackage = LoadedTaskPackage(
        fingerprint = "fp", specVersion = "1.6", customer = "проба", sourceRun = "run-T",
        revision = 1, readiness = "в",
        allowedModes = mapOf("rostering" to listOf("feasibility-soft")),
        objectives = emptyMap(), objectiveDetails = emptyMap(),
        hardParams = emptyList(), weights = emptyMap(), unmappedRules = emptyList(),
        componentsWithoutWeight = emptyList(), hasData = true)

    private fun solution(): FlightCrewSchedule {
        val home = airport("PLY"); val out = airport("OUT")
        val e1 = employee("E1").apply { homeAirport = home }
        val f1 = flight("F1", home, t(2, 10), out, t(2, 12))
        val f2 = flight("F2", out, t(3, 10), home, t(3, 12))
        return Fixtures.schedule(listOf(home, out), listOf(e1),
            listOf(assignment("s1", f1, e1), assignment("s2", f2, e1)))
    }

    private fun record(runId: String, state: String): RunRecord =
        RunRecord(runId, 0L, "fp", "feasibility-soft", pkg = pkg(), state = state)

    @Test
    fun `считает с опубликованным решением и без отчёта — отказ словами состояния`() {
        // Окно PackagedRuns.start: record.solution уже присвоен, layoverCost
        // и ST_DONE ещё нет. Прямой GET здесь должен получить отказ, а не
        // решение с null-отчётом.
        val r = record("r-окно", PackagedRuns.ST_RUNNING).apply {
            solution = solution(); score = "0hard/0soft"; layoverCost = null
        }
        val e = assertThrows<TaskModeRefused> { PackagedRunResult.of(r) }
        assertTrue(e.message!!.contains("r-окно"), e.message)
        assertTrue(e.message!!.contains("«" + PackagedRuns.ST_RUNNING + "»"), e.message)
    }

    @Test
    fun `упал после публикации решения — отказ с причиной падения`() {
        // Падение после record.solution (например, в отчёте окон отдыха):
        // решение в записи осталось, состояние «упал». Отдавать его нельзя.
        val r = record("r-упал", PackagedRuns.ST_FAILED).apply {
            solution = solution(); failure = "проверка окон отдыха не сошлась"
            layoverCost = mapOf("version" to 2)
        }
        val e = assertThrows<TaskModeRefused> { PackagedRunResult.of(r) }
        assertTrue(e.message!!.contains("«" + PackagedRuns.ST_FAILED + "»"), e.message)
        assertTrue(e.message!!.contains("проверка окон отдыха не сошлась"), e.message)
    }

    @Test
    fun `готов с решением и отчётом — результат несёт тот же объект отчёта`() {
        val cost: Map<String, Any?> = linkedMapOf("version" to 2, "contract" to "B", "known_nights" to 1)
        val sol = solution()
        val r = record("r-готов", PackagedRuns.ST_DONE).apply {
            solution = sol; score = "0hard/0soft"; layoverCost = cost
        }
        val view = PackagedRunResult.of(r)
        assertSame(sol, view.solution)
        assertSame(cost, view.layoverCost)
        assertSame(r.statusSnapshot()["layoverCost"], view.layoverCost)
    }

    @Test
    fun `готов без отчёта ночёвок — решение с null-отчётом, не отказ`() {
        // Запись прежней сборки (или пейринг): отчёт не считался. Готовность
        // судится состоянием — результат отдаётся, layoverCost = null.
        val sol = solution()
        val r = record("r-legacy", PackagedRuns.ST_DONE).apply { solution = sol; layoverCost = null }
        val view = PackagedRunResult.of(r)
        assertSame(sol, view.solution)
        assertNull(view.layoverCost)
    }

    @Test
    fun `готов без решения — отказ по-прежнему, словами состояния`() {
        // Зарегистрированный каталог пейринга: state=готов, solution=null.
        val r = RunRecord("r-каталог", 0L, "fp", "external-catalog", task = "pairing",
            pkg = pkg(), state = PackagedRuns.ST_DONE)
        val e = assertThrows<TaskModeRefused> { PackagedRunResult.of(r) }
        assertTrue(e.message!!.contains("без решения"), e.message)
        assertEquals(PackagedRuns.ST_DONE, r.state)
    }
}
