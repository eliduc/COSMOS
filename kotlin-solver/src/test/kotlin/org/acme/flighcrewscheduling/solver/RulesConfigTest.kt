package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path

/** RulesConfig: parsing/validation via the internal loadFrom/fromMap
 *  entry points (env vars cannot be flipped per-test in the JUnit JVM),
 *  plus the contract that in this test JVM (CREW_RULES_CONFIG unset) the
 *  object exposes exactly the built-in defaults == today's constants. */
class RulesConfigTest {

    @TempDir
    lateinit var tempDir: Path

    private fun writeJson(name: String, content: String): Path {
        val p = tempDir.resolve(name)
        Files.write(p, content.toByteArray(Charsets.UTF_8))
        return p
    }

    @Test
    fun `object vals equal built-in defaults when env is unset`() {
        // Guard: the reproducibility contract is only checkable when the
        // env var is not set — which is the expected test-JVM state.
        assertNull(System.getenv(RulesConfig.ENV_VAR),
            "test JVM must not set ${RulesConfig.ENV_VAR}")
        assertEquals(30, RulesConfig.minSitMin)
        assertEquals(5, RulesConfig.maxLandingsPerDuty)
        assertEquals(840, RulesConfig.maxDutySpanMin)
        assertEquals(600, RulesConfig.minRestMin)
        assertEquals(5100, RulesConfig.maxMonthlyBlockMin)
        assertEquals(5760, RulesConfig.maxPairingSpanMin)
    }

    @Test
    fun `flat json with one key overrides only that value`() {
        val p = writeJson("flat.json", """{"min_sit_min": 45}""")
        val v = RulesConfig.loadFrom(p)
        assertEquals(45, v.minSitMin)
        assertEquals(RulesConfig.DEFAULTS.copy(minSitMin = 45), v)
    }

    @Test
    fun `wrapped parameters json is accepted (airline_default shape)`() {
        val p = writeJson("wrapped.json", """
            {"parameters": {
              "min_sit_min": 45,
              "max_landings_per_duty": 6,
              "max_duty_span_min": 780,
              "min_rest_min": 660,
              "max_monthly_block_min": 4800,
              "max_pairing_span_min": 4320
            }}
        """.trimIndent())
        val v = RulesConfig.loadFrom(p)
        assertEquals(RulesConfig.Values(45, 6, 780, 660, 4800, 4320), v)
    }

    @Test
    fun `unknown parameter key fails loudly`() {
        val p = writeJson("unknown.json",
            """{"min_sit_min": 45, "max_sit_min": 90}""")
        val e = assertThrows(IllegalStateException::class.java) {
            RulesConfig.loadFrom(p)
        }
        assertTrue(e.message!!.contains("max_sit_min"), e.message)
    }

    @Test
    fun `unknown top-level key next to parameters fails loudly`() {
        val p = writeJson("extra.json",
            """{"parameters": {"min_sit_min": 45}, "airline": "XX"}""")
        val e = assertThrows(IllegalStateException::class.java) {
            RulesConfig.loadFrom(p)
        }
        assertTrue(e.message!!.contains("airline"), e.message)
    }

    @Test
    fun `malformed json fails loudly`() {
        val p = writeJson("broken.json", """{"min_sit_min": """)
        assertThrows(IllegalStateException::class.java) {
            RulesConfig.loadFrom(p)
        }
    }

    @Test
    fun `non-integer value fails loudly`() {
        val p = writeJson("noninteger.json", """{"min_sit_min": "45"}""")
        assertThrows(IllegalStateException::class.java) {
            RulesConfig.loadFrom(p)
        }
    }

    // --- свидетельство потребителя (пятый аудит, пакет B) --------------

    @Test
    fun `reading a parameter is recorded with the value the caller got`() {
        RulesConfig.resetReadings()
        assertTrue(RulesConfig.appliedReadings().isEmpty())
        val v = RulesConfig.minRestMin
        val applied = RulesConfig.appliedReadings()
        assertEquals(v, applied["min_rest_min"])
        // непрочитанный параметр не имеет права появиться: «применено»
        // означает прочитано, а не «объявлено в контракте»
        assertTrue(!applied.containsKey("max_pairing_span_min"))
    }

    @Test
    fun `installing the roster profile records every rule it declares`() {
        // Правила состава читаются внутри фильтров; на наборе без
        // со-локации фильтр не вызывается, и «применено» осталось бы
        // пустым при РАБОТАЮЩЕМ правиле. Установка профиля обязана
        // записать все заявленные параметры.
        RulesConfig.resetReadings()
        val verifier: ConstraintVerifier<GeradPairingRosterConstraintProvider,
                                         FlightCrewSchedule> =
            ConstraintVerifier.build(
                GeradPairingRosterConstraintProvider(),
                FlightCrewSchedule::class.java,
                FlightAssignment::class.java)
        val a = Fixtures.airport("BASE")
        val f = Fixtures.flight("C_000000", a, Fixtures.t(1, 8), a,
            Fixtures.t(1, 12))
        val e = Fixtures.employee("E1")
        val fa = Fixtures.assignment("1", f, e)
        verifier.verifyThat().givenSolution(
            Fixtures.schedule(listOf(a), listOf(e), listOf(fa)))
        val applied = RulesConfig.appliedReadings()
        for (key in GeradPairingRosterConstraintProvider.RULE_KEYS) {
            assertTrue(applied.containsKey(key),
                "профиль заявляет $key, но ни разу его не прочитал: $applied")
        }
    }

    @Test
    fun `resource rules report how many times they were evaluated`() {
        ResourceRules.resetObservations()
        assertEquals(0L, ResourceRules.weeklyRestEvaluations())
        val rr = WeeklyRestParams(
            requiredMin = 2880, periodMin = 1440, tzOffsetMin = 0,
            horizonFromEpochMin = 0, horizonToEpochMin = 100000)
        weeklyRestShortfallMin(emptyList(), rr)
        weeklyRestShortfallMin(emptyList(), rr)
        assertEquals(2L, ResourceRules.weeklyRestEvaluations())
    }
}
