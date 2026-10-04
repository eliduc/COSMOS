package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

/** Real provider construction and report serialization only: no solver or service. */
class A05ProviderReadingsAgentTest {
    private val codes = listOf("Mixed Case / 42", "Полярис-Р200")

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun resolution(): RunThresholds.Resolution {
        val base = RunThresholds.resolve(listOf(
            RuleParam("fleet_types", null, codes.joinToString(", "), "code_list", "eq",
                null, codes.joinToString(", "), "R-FLEET", "HARD"),
            RuleParam("max_duty_duration", 720.0, "720", "duration_minutes", "max",
                null, "720 минут", "R-BASE", "HARD")), TaskModes.PROFILE_DEFAULT)
        return RunThresholds.applyDutyExtension(base, 780, listOf("R-EXT"))
    }

    private fun provider(): FlightCrewSchedulingConstraintProvider =
        RunThresholds.withRunModel(resolution().applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }

    private fun mode() = TaskModes.resolve("feasibility-soft", null, "rostering")

    @Test
    fun `fleet reading is the number captured by the actual provider`() {
        val actual = provider().readings()
        assertEquals(2, actual["fleet_types"], "Actual fleet reading is absent or a boolean switch")
        assertEquals(780, actual["max_duty_span_min"], "Existing numeric readings must remain intact")
    }

    @Test
    fun `soft duty reading is the threshold captured by the actual provider`() {
        assertEquals(720, provider().readings()["duty_extension_threshold_min"])
    }

    @Test
    fun `task report retains exact provider codes without taking them from applied`() {
        provider()
        // This mode deliberately declares no fleet. The actual provider did read it.
        val actual = TaskModes.withActualReadings(mode())
        val json = ObjectMapper().readTree(ObjectMapper().writeValueAsBytes(LastRun.snapshotOf(actual)))
        assertTrue(json.path("profileCodeReadings").has("fleet_types"), "Actual code readings are missing")
        assertEquals(codes, json.path("profileCodeReadings").path("fleet_types").map { it.asText() })
        assertTrue(json.path("thresholds").path("applied").isEmpty,
            "The control must not supply codes through an applied declaration")
    }

    @Test
    fun `empty packaged provider reports off not the declared fleet`() {
        provider()
        val empty = RunThresholds.withRunModel(emptyMap(), emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }
        val actual = TaskModes.withActualReadings(mode().copy(thresholds = resolution()))
        val json = ObjectMapper().valueToTree<com.fasterxml.jackson.databind.JsonNode>(LastRun.snapshotOf(actual))
        assertEquals(0, empty.readings()["fleet_types"])
        assertEquals(0, actual.profileReadings["duty_extension_threshold_min"])
        assertTrue(json.path("profileCodeReadings").path("fleet_types").isArray)
        assertTrue(json.path("profileCodeReadings").path("fleet_types").isEmpty)
        assertTrue(json.path("thresholds").path("applied").size() > 0,
            "Declared fleet must not be substituted for the empty actual reading")
    }

    @Test
    fun `missing actual provider reading still refuses the report`() {
        LastProviderReadings.clearForTests()
        assertThrows<TaskModeRefused> { TaskModes.withActualReadings(mode()) }
    }

    @Test
    fun `captured full reading survives a later provider and retains numeric compatibility`() {
        provider()
        val captured = LastProviderReadings.snapshotWithCodes()!!
        assertEquals(LastProviderReadings.snapshot()!!.second, captured.readings)
        FlightCrewSchedulingConstraintProvider() // A different construction replaces the global last reading.
        val actual = TaskModes.withActualReadings(mode(), captured.readings, captured.codeReadings)
        assertEquals(codes, actual.profileCodeReadings["fleet_types"])
        assertEquals(2, actual.profileReadings["fleet_types"])
        assertEquals(720, actual.profileReadings["duty_extension_threshold_min"])
        assertTrue(LastProviderReadings.snapshotWithCodes()!!.codeReadings.isEmpty())
    }

    @Test
    fun `numeric-only caller leaves code evidence absent instead of inferring applied codes`() {
        provider()
        val actual = TaskModes.withActualReadings(mode().copy(thresholds = resolution()),
            LastProviderReadings.snapshot()!!.second)
        assertEquals(2, actual.profileReadings["fleet_types"])
        assertTrue(actual.profileCodeReadings.isEmpty(), "Missing evidence must not become an empty measured fleet")
    }

    @Test
    fun `report copies supplied reading collections rather than retaining mutable aliases`() {
        provider()
        val captured = LastProviderReadings.snapshotWithCodes()!!
        val numeric = captured.readings.toMutableMap()
        val labels = codes.toMutableList()
        val input = mutableMapOf("fleet_types" to labels)
        val actual = TaskModes.withActualReadings(mode(), numeric, input)
        numeric["fleet_types"] = 99
        labels.clear()
        input.clear()
        assertEquals(2, actual.profileReadings["fleet_types"])
        assertEquals(codes, actual.profileCodeReadings["fleet_types"])
    }

    @Test
    fun `unpackaged all-types fallback is not reported as a measured empty fleet`() {
        provider()
        val outside = FlightCrewSchedulingConstraintProvider()
        val actual = TaskModes.withActualReadings(mode().copy(thresholds = resolution()))
        assertTrue("fleet_types" !in outside.readings())
        assertTrue(actual.profileCodeReadings.isEmpty())
        assertEquals(0, actual.profileReadings["duty_extension_threshold_min"])
    }

    @Test
    fun `another profile does not inherit the default provider code evidence`() {
        provider()
        val actual = TaskModes.withActualReadings(
            TaskModes.resolve("feasibility-soft", null, "rostering", rulesProfile = "GERAD"))
        assertTrue(actual.profileCodeReadings.isEmpty())
    }
}
