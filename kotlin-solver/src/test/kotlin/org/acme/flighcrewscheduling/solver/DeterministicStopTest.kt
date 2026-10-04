package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.config.solver.termination.TerminationConfig
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.rest.FlightCrewSchedulingResource
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Path
import java.security.MessageDigest
import java.time.Duration
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * ЧЕСТНАЯ ОСТАНОВКА детерминированного режима Д1 (правка 08.09.2026).
 *
 * Находка живого стенда: профиль Д1 замещал остановку счётными пределами
 * целиком, и на мире БЕЗ ИСПОЛНИМОГО ХОДА прогон не заканчивался никогда
 * — счётный предел судится только между завершёнными шагами поиска, а
 * шаг не завершался. Прогон навсегда занимал поток пула прогонов
 * (`Executors.newFixedThreadPool(2)`); так был заблокирован локальный
 * решатель с 06.09 по 08.09.
 *
 * Судьи здесь:
 *  (а) НЕДОСТИЖИМЫЙ счётный предел в режиме Д1 — прогон всё равно
 *      заканчивается, и признак остановки называет страховочный предел
 *      стенных часов;
 *  (а2) мир, где шаг поиска не завершается сам (все места заданы, ходов
 *      акцептор не принимает), — счётный предел не наступает даже при
 *      `stepLimit = 1`, прогон кончает страховка;
 *  (б) обычный мир в режиме Д1 даёт ТОТ ЖЕ результат на двух прогонах, и
 *      признак называет счётный предел (страховка не сработала);
 *  (в) ручка отмены останавливает СЧИТАЮЩИЙ решатель и освобождает поток
 *      пула — одной отменой, без повторов;
 *  (г) отмена, пришедшая ДО старта счёта (прогон в очереди занятого
 *      пула), не даёт прогону считать вовсе;
 *  (д) разбор признака остановки без решателя — чистой функцией.
 *
 * Правка по ревью 08.09.2026: прежний судья (в) стоял на `stepLimit = 1`
 * и мире, который сам завершает шаг за ~100 мс, — он был зелёным ПО
 * ПОСТРОЕНИЮ (прогон кончался счётным пределом, а `kind = cancelled`
 * получался из флага запроса) и прошёл бы и с ПОТЕРЯННОЙ отменой. Теперь
 * отмена судится на прогоне, который сам не кончится, и по факту
 * остановки решателя.
 */
class DeterministicStopTest {

    private val executor = Executors.newSingleThreadExecutor()

    @AfterEach
    fun cleanup() {
        TaskPackage.overrideForTests(null)
        PackagedRuns.clearForTests()
        RunThresholds.clearForTests()
        executor.shutdownNow()
    }

    private fun sha256(b: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(b)
            .joinToString("") { x -> "%02x".format(x) }

    /** Минимальный годный пакет постановки БАЙТАМИ (запросный путь). */
    private fun packageZip(tmp: Path): ByteArray {
        val objective = """{"customer": "проба", "readiness": "в",
            "rostering": {"ready": true, "direction": "minimize", "objectives": [], "components": []}}"""
        val model = """{"meta": {"revision": 1, "model_schema": 2}, "rules": []}"""
        val manifest = """
            {"spec_version": "1.21", "customer": "проба", "source_run": "run-D1", "revision": 1,
             "readiness": "в",
             "files": [
               {"name": "objective.json", "sha256": "${sha256(objective.toByteArray(Charsets.UTF_8))}"},
               {"name": "model.json", "sha256": "${sha256(model.toByteArray(Charsets.UTF_8))}"}],
             "allowed_modes": {"rostering": ["feasibility-soft"]},
             "objectives": {"rostering": []},
             "has_data": true}
        """.trimIndent()
        val zip = tmp.resolve("d1.zip").toFile()
        ZipOutputStream(zip.outputStream()).use { z ->
            for ((name, body) in listOf("objective.json" to objective,
                                        "model.json" to model, "manifest.json" to manifest)) {
                z.putNextEntry(ZipEntry(name)); z.write(body.toByteArray(Charsets.UTF_8)); z.closeEntry()
            }
        }
        return zip.readBytes()
    }

    /** Крошечный мир: один сотрудник, единственное назначение уже отдано
     *  ему. Фаза поиска на нём ЖИВАЯ (снятие назначения исполнимо,
     *  `allowsUnassigned = true`) — десятки шагов в секунду. */
    private fun tinyProblem(): FlightCrewSchedule {
        val home = Fixtures.airport("PLY")
        val out = Fixtures.airport("OUT")
        val e1 = Fixtures.employee("E1").apply { homeAirport = home }
        val f = Fixtures.flight("F1", home, Fixtures.t(2, 10), out, Fixtures.t(2, 12))
        return Fixtures.schedule(listOf(home, out), listOf(e1),
            listOf(Fixtures.assignment("s1", f, e1)))
    }

    /** Мир ЖИВОГО СТЕНДА (`st-layover-1`): один сотрудник, тур «туда и
     *  обратно через ночёвку», ОБА места уже заданы. Улучшить нечего,
     *  принимаемого хода нет — шаг фазы поиска сам не завершается, и
     *  счётный предел (даже `stepLimit = 1`) не наступает никогда. Ровно
     *  на таком мире прогон Д1 занимал поток пула навсегда. */
    private fun hangProblem(): FlightCrewSchedule {
        val home = Fixtures.airport("ST1")
        val out = Fixtures.airport("ST2")
        val e1 = Fixtures.employee("E1").apply { homeAirport = home }
        val f1 = Fixtures.flight("F1", home, Fixtures.t(2, 10), out, Fixtures.t(2, 12))
        val f2 = Fixtures.flight("F2", out, Fixtures.t(3, 10), home, Fixtures.t(3, 12))
        return Fixtures.schedule(listOf(home, out), listOf(e1), listOf(
            Fixtures.assignment("s1", f1, e1), Fixtures.assignment("s2", f2, e1)))
    }

    /** Обычный мир: два сотрудника, четыре нераспределённых места —
     *  конструктив раздаёт, поиск делает настоящие шаги. */
    private fun openProblem(): FlightCrewSchedule {
        val home = Fixtures.airport("PLY")
        val out = Fixtures.airport("OUT")
        val e1 = Fixtures.employee("E1").apply { homeAirport = home }
        val e2 = Fixtures.employee("E2").apply { homeAirport = home }
        val f1 = Fixtures.flight("F1", home, Fixtures.t(2, 6), out, Fixtures.t(2, 8))
        val f2 = Fixtures.flight("F2", out, Fixtures.t(2, 12), home, Fixtures.t(2, 14))
        val f3 = Fixtures.flight("F3", home, Fixtures.t(4, 6), out, Fixtures.t(4, 8))
        val f4 = Fixtures.flight("F4", out, Fixtures.t(4, 12), home, Fixtures.t(4, 14))
        return Fixtures.schedule(listOf(home, out), listOf(e1, e2), listOf(
            Fixtures.assignment("a1", f1, null), Fixtures.assignment("a2", f2, null),
            Fixtures.assignment("a3", f3, null), Fixtures.assignment("a4", f4, null)))
    }

    /** Конфигурация прогона ровно того же вида, что строит ручка
     *  `/schedules/run` при `deterministic=true`: базовая остановка со
     *  стенным пределом запроса и ТОТ ЖЕ предел отдельным параметром
     *  (страховка Д1 берётся из предела ЗАПРОСА, не из базы). */
    private fun d1Config(stepLimit: Int?, unimprovedStepLimit: Int? = null,
                         safetySeconds: Long): SolverConfig {
        val base = SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
            .withTerminationConfig(TerminationConfig()
                .withSpentLimit(Duration.ofSeconds(safetySeconds)))
        return FlightCrewSchedulingResource.packagedSolverConfig(
            base, 0L, true, stepLimit, unimprovedStepLimit,
            spentLimitSeconds = safetySeconds.toInt())
    }

    private fun d1Profile(stepLimit: Int?, unimprovedStepLimit: Int? = null): Map<String, Any?> =
        FlightCrewSchedulingResource.deterministicProfile("rostering", true, 0L,
            stepLimit, unimprovedStepLimit)!!

    private fun await(r: RunRecord, seconds: Int): RunRecord {
        val deadline = System.currentTimeMillis() + seconds * 1000L
        while (r.state == PackagedRuns.ST_RUNNING && System.currentTimeMillis() < deadline) {
            Thread.sleep(50)
        }
        return r
    }

    // ------------------------------------------------------------------ (а)

    /**
     * СУДЬЯ НАХОДКИ. Прежде в режиме Д1 остановка состояла ТОЛЬКО из
     * счётных пределов, и прогон, до счётного предела не доходивший,
     * не заканчивался никогда — держал поток пула прогонов до
     * перезапуска службы.
     *
     * Здесь «счётный предел не наступает» задано ПРЯМО — недостижимым
     * числом шагов. Мир, где шаг поиска сам не завершается, судит
     * отдельный судья (а2): условие правки шире одного мира, и обе его
     * стороны сторожатся по отдельности.
     */
    @Test
    fun `счётный предел не наступает — прогон Д1 кончается страховочным пределом`(
        @TempDir tmp: Path,
    ) {
        val safety = 3L
        val unreachable = 1_000_000
        val started = System.currentTimeMillis()
        val r = PackagedRuns.start(tinyProblem(), packageZip(tmp), "feasibility-soft", null,
            d1Config(stepLimit = unreachable, safetySeconds = safety), executor,
            profile = d1Profile(stepLimit = unreachable))
        await(r, seconds = 40)
        val spent = System.currentTimeMillis() - started
        assertEquals(PackagedRuns.ST_DONE, r.state,
            "прогон не закончился за " + spent + " мс: " + (r.failure ?: "всё ещё считает"))
        assertNotNull(r.solution)
        val term = r.termination!!
        assertEquals(RunTermination.WALL_CLOCK_SAFETY, term["kind"], term.toString())
        assertEquals(unreachable, term["step_limit"], term.toString())
        assertEquals(safety * 1000L, term["wall_clock_limit_ms"])
        assertTrue((term["elapsed_ms"] as Long) >= safety * 1000L - RunTermination.SLACK_MS,
            term.toString())
        // Уложились в страховку с запасом — «вечно» кончилось.
        assertTrue(spent < 20_000L, "страховка не сработала вовремя: " + spent + " мс")
        assertTrue((term["words"] as String).contains("СТРАХОВОЧНЫМ"), term["words"].toString())
        // Шаги поиска — часть признака, а не украшение: конструктив на
        // этом мире отрабатывает целиком (все места уже заданы), фаза
        // поиска стартует, и воспроизводимость обещается ТОЛЬКО когда
        // страховка застала поиск до первого шага.
        assertEquals(true, term["search_started"], term.toString())
        assertNotNull(term["search_steps"])
        assertEquals(term["search_steps"] == 0, term["reproducible"], term.toString())
        // Признак виден и в статусе прогона; профиль Д1 сторожевого числа
        // не несёт (он — рецепт воспроизводимости, а не сторож).
        assertEquals(term, r.statusSnapshot()["termination"])
        @Suppress("UNCHECKED_CAST")
        val profileTerm = (r.profile!!["termination"] as Map<String, Any?>)
        assertEquals(setOf("kind", "step_limit", "unimproved_step_limit"), profileTerm.keys)
    }

    // ----------------------------------------------------------------- (а2)

    /**
     * СУДЬЯ МИРА ЗАВИСАНИЯ (находка ревью 08.09.2026). Судья (а) задаёт
     * условие «счётный предел не наступает» недостижимым числом; здесь
     * оно возникает САМО — как на живом стенде: все места заданы,
     * улучшать нечего, шаг фазы поиска не завершается, и `stepLimit = 1`
     * не наступает никогда. Прогон обязан кончиться страховкой.
     */
    @Test
    fun `мир без принимаемого хода — счётный предел не наступает даже при пределе 1`(
        @TempDir tmp: Path,
    ) {
        val safety = 2L
        val started = System.currentTimeMillis()
        val r = PackagedRuns.start(hangProblem(), packageZip(tmp), "feasibility-soft", null,
            d1Config(stepLimit = 1, safetySeconds = safety), executor,
            profile = d1Profile(stepLimit = 1))
        await(r, seconds = 40)
        val spent = System.currentTimeMillis() - started
        assertEquals(PackagedRuns.ST_DONE, r.state,
            "прогон не закончился за " + spent + " мс: " + (r.failure ?: "всё ещё считает"))
        val term = r.termination!!
        // Не `step_count`: предел в один шаг НЕ НАСТУПИЛ — шаг закрылся
        // только тем, что часы оборвали перебор ходов.
        assertEquals(RunTermination.WALL_CLOCK_SAFETY, term["kind"], term.toString())
        assertEquals(1, term["step_limit"], term.toString())
        assertTrue((term["elapsed_ms"] as Long) >= safety * 1000L - RunTermination.SLACK_MS,
            term.toString())
        assertTrue(spent < 25_000L, "страховка не сработала вовремя: " + spent + " мс")
        assertEquals(true, term["search_started"], term.toString())
        assertNotNull(term["search_steps"])
    }

    // ------------------------------------------------------------------ (б)

    @Test
    fun `обычный мир в режиме Д1 — счётный предел и тот же результат дважды`(
        @TempDir tmp: Path,
    ) {
        val zip = packageZip(tmp)
        val safety = 30L
        fun once(): RunRecord {
            val r = PackagedRuns.start(openProblem(), zip, "feasibility-soft", null,
                d1Config(stepLimit = 3, safetySeconds = safety), executor,
                profile = d1Profile(stepLimit = 3))
            await(r, seconds = 60)
            assertEquals(PackagedRuns.ST_DONE, r.state, r.failure ?: "")
            return r
        }
        val first = once()
        val second = once()
        // Остановил СЧЁТНЫЙ предел — страховка стенных часов не при чём.
        for (r in listOf(first, second)) {
            val term = r.termination!!
            assertEquals(RunTermination.STEP_COUNT, term["kind"], term.toString())
            assertEquals(3, term["search_steps"], term.toString())
            assertEquals(true, term["reproducible"], term.toString())
            assertEquals("deterministic", term["mode"])
            assertEquals(true, term["search_started"], term.toString())
            // Шаги конструктива в признаке есть и они настоящие. Точного
            // числа судья НЕ фиксирует: замер 08.09.2026 показал, что
            // счётный предел уровня решателя мостится и в остановку
            // конструктива (при `stepLimit = 3` конструктив раздал 3 места
            // из 4). Это отдельная находка о ПОВЕДЕНИИ счётного предела —
            // за рамками правки признака остановки; заранее закреплять
            // здесь ни старое, ни новое число нельзя.
            assertTrue((term["construction_steps"] as Int) > 0, term.toString())
        }
        // …и результат двух прогонов совпадает — назначениями, не только счётом.
        assertEquals(first.score, second.score)
        fun seats(r: RunRecord): Map<String, String?> =
            r.solution!!.flightAssignments.associate { it.id to it.employee?.id }
        assertEquals(seats(first), seats(second))
    }

    // ------------------------------------------------------------------ (в)

    /**
     * СУДЬЯ ОТМЕНЫ. Условие задано так, что закончить прогон может
     * ТОЛЬКО отмена: счётный предел недостижим, страховка 300 с. Отмена
     * подаётся ОДИН раз и лишь после того, как решатель действительно
     * вошёл в счёт, а проверяется по ФАКТУ остановки решателя, а не по
     * флагу запроса.
     */
    @Test
    fun `ручка отмены останавливает считающий решатель и освобождает поток пула`(
        @TempDir tmp: Path,
    ) {
        val safety = 300L
        val r = PackagedRuns.start(tinyProblem(), packageZip(tmp), "feasibility-soft", null,
            d1Config(stepLimit = 1_000_000, safetySeconds = safety), executor,
            profile = d1Profile(stepLimit = 1_000_000))
        // Ждём, пока решатель ДЕЙСТВИТЕЛЬНО считает: «прогон считает» и
        // «решатель внутри solve» — разные вещи.
        val deadline = System.currentTimeMillis() + 60_000L
        while (!PackagedRuns.isSolving(r.runId) && r.state == PackagedRuns.ST_RUNNING &&
            System.currentTimeMillis() < deadline) {
            Thread.sleep(20)
        }
        assertTrue(PackagedRuns.isSolving(r.runId),
            "решатель так и не начал счёт: положение «" + r.stage + "»")
        val answer = PackagedRuns.cancel(r.runId)!!
        assertEquals(true, answer["cancelled"], answer.toString())
        assertEquals(true, answer["solverReached"], answer.toString())
        assertEquals(PackagedRuns.STAGE_SOLVING, answer["stage"], answer.toString())
        await(r, seconds = 60)
        assertEquals(PackagedRuns.ST_DONE, r.state, r.failure ?: "всё ещё считает")
        assertEquals(true, r.cancelRequested)
        val term = r.termination!!
        assertEquals(RunTermination.CANCELLED, term["kind"], term.toString())
        assertEquals(false, term["reproducible"], term.toString())
        // Кончила прогон ОТМЕНА, а не страховка: до 300 с не дошло и близко.
        assertTrue((term["elapsed_ms"] as Long) < 60_000L, term.toString())
        // Завершённый прогон отменять нечего — и это говорится словами.
        val again = PackagedRuns.cancel(r.runId)!!
        assertEquals(false, again["cancelled"])
        assertTrue((again["words"] as String).contains("останавливать нечего"),
            again.toString())
        // Незнакомый идентификатор — null (ручка отвечает 404).
        org.junit.jupiter.api.Assertions.assertNull(PackagedRuns.cancel("нет-такого"))
    }

    // ------------------------------------------------------------------ (г)

    /**
     * СУДЬЯ ОТМЕНЫ ДО СТАРТА (находка ревью 08.09.2026). Прогон стоит в
     * очереди занятого пула — решателя у него ещё нет. Прежде такая
     * отмена «запоминалась» и исполнялась вызовом `terminateEarly()`
     * ПЕРЕД `solve`, а `solve` начинается со сброса этого признака
     * (`DefaultSolver.outerSolvingStarted` →
     * `BasicPlumbingTermination.resetTerminateEarly`) — отмена терялась,
     * и прогон считал до собственного предела. Теперь он не считает
     * вовсе: поток пула освобождается сразу.
     */
    @Test
    fun `отмена до старта счёта — прогон не считает вовсе`(@TempDir tmp: Path) {
        val gate = CountDownLatch(1)
        // Пул занят: прогон встанет в очередь и решателя не получит.
        executor.submit { gate.await() }
        val safety = 5L
        val r = PackagedRuns.start(tinyProblem(), packageZip(tmp), "feasibility-soft", null,
            d1Config(stepLimit = 1_000_000, safetySeconds = safety), executor,
            profile = d1Profile(stepLimit = 1_000_000))
        assertEquals(PackagedRuns.STAGE_QUEUED, r.stage)
        val answer = PackagedRuns.cancel(r.runId)!!
        assertEquals(true, answer["cancelled"], answer.toString())
        assertEquals(false, answer["solverReached"], answer.toString())
        assertEquals(PackagedRuns.STAGE_QUEUED, answer["stage"], answer.toString())
        assertTrue((answer["words"] as String).contains("в очереди пула"), answer.toString())
        val released = System.currentTimeMillis()
        gate.countDown()
        await(r, seconds = 30)
        val spent = System.currentTimeMillis() - released
        assertEquals(PackagedRuns.ST_DONE, r.state, r.failure ?: "всё ещё считает")
        // Считать НЕ начинал: страховка в 5 с даже не понадобилась.
        assertTrue(spent < 3_000L, "отменённый прогон всё-таки считал: " + spent + " мс")
        assertNotNull(r.solution)
        val term = r.termination!!
        assertEquals(RunTermination.CANCELLED, term["kind"], term.toString())
        assertEquals(0, term["search_steps"], term.toString())
        assertEquals(false, term["reproducible"], term.toString())
        assertEquals(false, term["search_started"], term.toString())
    }

    // ------------------------------------------- разбор признака без решателя

    @Test
    fun `признак остановки разбирает счётный предел, страховку и отмену`() {
        val d1 = TerminationConfig().apply {
            stepCountLimit = 2
            spentLimit = Duration.ofSeconds(10)
        }
        // Счётный предел набран шагами задолго до часов — воспроизводимо.
        val counted = RunTermination.reading(d1, true, 2, 0, 120L, false)
        assertEquals(RunTermination.STEP_COUNT, counted["kind"])
        assertEquals(true, counted["reproducible"])
        // …а вот тот же счётный предел, набранный РОВНО на исходе часов,
        // счётным не записывается: шаг мог завершиться только потому, что
        // часы оборвали перебор ходов (живой прогон 08.09.2026 — 1008 мс
        // при пределе 1 с). Осторожность важнее красивого слова.
        val atDeadline = RunTermination.reading(d1, true, 2, 0, 10_010L, false)
        assertEquals(RunTermination.WALL_CLOCK_SAFETY, atDeadline["kind"])
        assertEquals(false, atDeadline["reproducible"])
        // Часы израсходованы, фаза поиска стартовала, шагов нет —
        // страховка, решение конструктива, воспроизводимо по построению.
        val safety = RunTermination.reading(d1, true, 0, 0, 10_000L, false,
            searchStarted = true, constructionSteps = 12)
        assertEquals(RunTermination.WALL_CLOCK_SAFETY, safety["kind"])
        assertEquals(true, safety["reproducible"])
        // …а те же ноль шагов поиска, когда фаза поиска НЕ СТАРТОВАЛА,
        // значат обратное: часы оборвали КОНСТРУКТИВ, часть мест осталась
        // пустой, и сколько их — зависит от машины (находка ревью
        // 08.09.2026: прежде такой прогон звался воспроизводимым).
        val chCut = RunTermination.reading(d1, true, 0, 0, 10_000L, false,
            searchStarted = false, constructionSteps = 62)
        assertEquals(RunTermination.WALL_CLOCK_SAFETY, chCut["kind"])
        assertEquals(false, chCut["reproducible"])
        assertEquals(62, chCut["construction_steps"])
        assertTrue((chCut["words"] as String).contains("конструктив"), chCut["words"].toString())
        // Часы израсходованы, шаги были, но предела не набрали — числа
        // зависят от машины, воспроизводимость не обещана.
        val partial = RunTermination.reading(d1, true, 1, 1, 10_000L, false)
        assertEquals(RunTermination.WALL_CLOCK_SAFETY, partial["kind"])
        assertEquals(false, partial["reproducible"])
        // Обычный прогон по стенным часам — не «страховка», а свой предел.
        val plain = RunTermination.reading(
            TerminationConfig().withSpentLimit(Duration.ofSeconds(1)), false, 9, 3, 1_000L, false)
        assertEquals(RunTermination.WALL_CLOCK, plain["kind"])
        assertFalse(plain["reproducible"] as Boolean)
        // Ранняя остановка «N секунд без улучшения» обычного профиля —
        // предел СТЕННОЙ (явный параметр кнопки `unimproved_limit_s`).
        // Прежде такой прогон записывался «поиск кончился сам» — ложь
        // ровно там, где признак обязан быть честным (ревью 08.09.2026).
        val unimprovedWall = RunTermination.reading(
            TerminationConfig().apply {
                spentLimit = Duration.ofSeconds(30)
                unimprovedSpentLimit = Duration.ofSeconds(1)
            }, false, 1, 1, 1_019L, false)
        assertEquals(RunTermination.UNIMPROVED_WALL_CLOCK, unimprovedWall["kind"])
        assertEquals(false, unimprovedWall["reproducible"])
        assertEquals(1_000L, unimprovedWall["unimproved_wall_clock_limit_ms"])
        assertTrue((unimprovedWall["words"] as String).contains("без улучшения"),
            unimprovedWall["words"].toString())
        // Неулучшающие шаги и самостоятельный конец поиска.
        val unimproved = RunTermination.reading(
            TerminationConfig().apply { unimprovedStepCountLimit = 4 }, true, 12, 4, 55L, false)
        assertEquals(RunTermination.UNIMPROVED_STEP_COUNT, unimproved["kind"])
        val exhausted = RunTermination.reading(d1, true, 1, 1, 30L, false)
        assertEquals(RunTermination.SEARCH_EXHAUSTED, exhausted["kind"])
        // Отмена — ФАКТ остановки решателя, и он перебивает любой предел.
        assertEquals(RunTermination.CANCELLED,
            RunTermination.reading(d1, true, 2, 0, 120L, true)["kind"])
    }
}
