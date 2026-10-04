package org.acme.flighcrewscheduling.rest

import ai.timefold.solver.core.config.solver.EnvironmentMode
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.config.solver.termination.TerminationConfig
import org.acme.flighcrewscheduling.solver.TaskModeRefused
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.Duration

/**
 * K-7-a (единая версия 06.09.2026): конфигурация пакетного прогона —
 * функция ресурса без Quarkus. Шаблон — тот же solverConfig.xml, что у
 * ресурса (randomSeed = 0).
 */
class PackagedSolverConfigTest {

    private fun template(): SolverConfig =
        SolverConfig.createFromXmlResource("solverConfig.xml").apply { randomSeed = 0 }

    private fun base(): SolverConfig = SolverConfig(template()).apply {
        terminationConfig = TerminationConfig().withSpentLimit(Duration.ofSeconds(30))
    }

    @Test
    fun `зерно доезжает до конфигурации, без зерна остаётся зерно шаблона`() {
        val seeded = FlightCrewSchedulingResource.packagedSolverConfig(base(), 17L, null)
        assertEquals(17L, seeded.randomSeed)
        assertEquals(Duration.ofSeconds(30), seeded.terminationConfig!!.spentLimit)
        val plain = FlightCrewSchedulingResource.packagedSolverConfig(base(), null, null)
        assertEquals(template().randomSeed, plain.randomSeed)
        assertEquals(0L, plain.randomSeed)
        assertNull(plain.moveThreadCount)
        // Зерно и вне детерминированного режима не трогает остановку и потоки.
        assertNull(seeded.moveThreadCount)
        assertNull(seeded.terminationConfig!!.stepCountLimit)
    }

    @Test
    fun `ранняя остановка без улучшения — явным параметром поверх стенного предела`() {
        val cfg = FlightCrewSchedulingResource.packagedSolverConfig(base(), null, null,
            unimprovedSpentLimitSeconds = 7)
        assertEquals(Duration.ofSeconds(7), cfg.terminationConfig!!.unimprovedSpentLimit)
        assertEquals(Duration.ofSeconds(30), cfg.terminationConfig!!.spentLimit)
        val off = FlightCrewSchedulingResource.packagedSolverConfig(base(), null, null,
            unimprovedSpentLimitSeconds = 0)
        assertNull(off.terminationConfig!!.unimprovedSpentLimit)
    }

    @Test
    fun `детерминированный режим — счётный предел главным, один поток, зерно`() {
        val cfg = FlightCrewSchedulingResource.packagedSolverConfig(base(), null, true, stepLimit = 40)
        assertEquals(0L, cfg.randomSeed)
        assertEquals("NONE", cfg.moveThreadCount)
        assertEquals(40, cfg.terminationConfig!!.stepCountLimit)
        assertEquals(EnvironmentMode.NO_ASSERT, cfg.environmentMode)
        val seeded = FlightCrewSchedulingResource.packagedSolverConfig(base(), 5L, true,
            unimprovedStepLimit = 12)
        assertEquals(5L, seeded.randomSeed)
        assertEquals(12, seeded.terminationConfig!!.unimprovedStepCountLimit)
        // Профиль Д1 — те же числа поимённо; вне режима профиля нет.
        val profile = FlightCrewSchedulingResource.deterministicProfile("rostering", true, null,
            40, null)!!
        assertEquals(0L, profile["seed"])
        assertEquals("rostering", profile["task"])
        assertEquals(1, profile["move_threads"])
        // Состав профиля — РОВНО тот же, что у питоновского
        // vmp.signatures.deterministic_profile: профиль штампуется целиком
        // (profile_stamp), и лишний ключ развёл бы штампы двух сторон и
        // двух прогонов одного рецепта (правка по ревью 08.09.2026 —
        // страховочное число стенных часов из профиля убрано).
        assertEquals(mapOf("kind" to "step_count", "step_limit" to 40,
            "unimproved_step_limit" to null),
            profile["termination"])
        assertEquals(
            listOf("version", "task", "engine", "seed", "move_threads",
                "environment_mode", "entity_order", "termination"),
            profile.keys.toList())
        assertNull(FlightCrewSchedulingResource.deterministicProfile("rostering", null, 5L, 40, null))
        // Без счётного предела — отказ словами.
        val e = assertThrows<TaskModeRefused> {
            FlightCrewSchedulingResource.packagedSolverConfig(base(), 5L, true)
        }
        assertTrue(e.message!!.contains("счётного предела"), e.message)
    }

    /**
     * Правка 08.09.2026 (находка живого стенда): в детерминированном
     * режиме стенные часы из остановки БОЛЬШЕ НЕ ИСЧЕЗАЮТ — они остаются
     * страховкой рядом со счётным пределом. Счётный предел главный:
     * на здоровом мире он наступает первым, страховка не срабатывает.
     */
    @Test
    fun `страховочный предел стенных часов остаётся в детерминированном режиме`() {
        // Предел ЗАПРОСА становится страховкой.
        val cfg = FlightCrewSchedulingResource.packagedSolverConfig(base(), null, true,
            stepLimit = 40, spentLimitSeconds = 30)
        assertEquals(Duration.ofSeconds(30), cfg.terminationConfig!!.spentLimit)
        assertEquals(40, cfg.terminationConfig!!.stepCountLimit)
        // Запрос без стенного предела — потолок умолчания, а не «навсегда».
        val noWall = SolverConfig(template()).apply { terminationConfig = TerminationConfig() }
        val capped = FlightCrewSchedulingResource.packagedSolverConfig(noWall, null, true,
            unimprovedStepLimit = 3)
        assertEquals(
            Duration.ofSeconds(FlightCrewSchedulingResource.D1_DEFAULT_SAFETY_SECONDS),
            capped.terminationConfig!!.spentLimit)
        // И совсем без остановки в базе — тот же потолок.
        val bare = SolverConfig(template()).apply { terminationConfig = null }
        assertEquals(
            Duration.ofSeconds(FlightCrewSchedulingResource.D1_DEFAULT_SAFETY_SECONDS),
            FlightCrewSchedulingResource.packagedSolverConfig(bare, null, true, stepLimit = 1)
                .terminationConfig!!.spentLimit)
        assertEquals(Duration.ofSeconds(5),
            FlightCrewSchedulingResource.deterministicSafety(5))
        assertEquals(Duration.ofSeconds(FlightCrewSchedulingResource.D1_DEFAULT_SAFETY_SECONDS),
            FlightCrewSchedulingResource.deterministicSafety(0))
    }

    /**
     * СУДЬЯ ПУТИ РУЧКИ (находка ревью 08.09.2026). Руководство обещает:
     * «запрос предела не назвал — потолок 60 секунд». Прежде число
     * страховки читалось из БАЗОВОЙ остановки, а её `startPackagedRun`
     * заполняет всегда (`spentLimitSeconds ?: 30`) — значит через ручку
     * страховка была ровно 30 с, а объявленный потолок 60 с достигался
     * только в модульном тесте, собиравшем базу вручную. Судья повторяет
     * путь ручки шаг в шаг: база, как её строит `buildSolverConfig`, плюс
     * СЫРОЙ параметр запроса.
     */
    @Test
    fun `страховка Д1 берётся из предела ЗАПРОСА, а не из базовой остановки`() {
        // Ручка без spentLimitSeconds: база несёт умолчание 30 с, запрос —
        // ничего. Страховка обязана быть объявленной руководством.
        val asHandleNoLimit = SolverConfig(template()).apply {
            terminationConfig = TerminationConfig().withSpentLimit(Duration.ofSeconds(30))
        }
        assertEquals(
            Duration.ofSeconds(FlightCrewSchedulingResource.D1_DEFAULT_SAFETY_SECONDS),
            FlightCrewSchedulingResource.packagedSolverConfig(asHandleNoLimit, null, true,
                stepLimit = 1_000_000, spentLimitSeconds = null)
                .terminationConfig!!.spentLimit)
        // Ручка с spentLimitSeconds=5: база несёт те же 5 с, и страховка — 5 с.
        val asHandleFive = SolverConfig(template()).apply {
            terminationConfig = TerminationConfig().withSpentLimit(Duration.ofSeconds(5))
        }
        assertEquals(Duration.ofSeconds(5),
            FlightCrewSchedulingResource.packagedSolverConfig(asHandleFive, null, true,
                stepLimit = 1_000_000, spentLimitSeconds = 5)
                .terminationConfig!!.spentLimit)
    }

    /**
     * Ранняя остановка «N секунд без улучшения» — предел СТЕННОЙ: в
     * детерминированную остановку он не переносится (иначе прогон Д1 стал
     * бы невоспроизводимым), а вне режима Д1 живёт рядом со стенным
     * пределом. Прежде этот выбор был неявным и нигде не проверялся.
     */
    @Test
    fun `ранняя остановка по стенным часам в режим Д1 не переносится`() {
        val cfg = FlightCrewSchedulingResource.packagedSolverConfig(base(), null, true,
            stepLimit = 40, unimprovedSpentLimitSeconds = 7, spentLimitSeconds = 30)
        assertNull(cfg.terminationConfig!!.unimprovedSpentLimit)
        assertEquals(40, cfg.terminationConfig!!.stepCountLimit)
        assertEquals(Duration.ofSeconds(30), cfg.terminationConfig!!.spentLimit)
        // …а обычный прогон её сохраняет.
        val plain = FlightCrewSchedulingResource.packagedSolverConfig(base(), null, null,
            unimprovedSpentLimitSeconds = 7, spentLimitSeconds = 30)
        assertEquals(Duration.ofSeconds(7), plain.terminationConfig!!.unimprovedSpentLimit)
    }
}
