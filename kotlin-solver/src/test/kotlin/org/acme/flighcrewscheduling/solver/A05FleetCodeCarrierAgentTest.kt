package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Literal designations are data, including "null". JSON null is absence, not a designation.
 * Actual Kotlin ZIP reader/resolve/provider only; no claim of Python export or external delivery.
 * Source contract: cdpm/model.py validate_param; template_catalog/landing.py _value_problem.
 */
class A05FleetCodeCarrierAgentTest {
    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun pkg(name: String, value: String?, typed: Boolean = false): LoadedTaskPackage {
        val unit = if (name == "fleet_type") "code" else "code_list"
        val paramSet = if (name == "fleet_type") "single" else "several"
        val valueField = value?.let { "\"value\":$it," }.orEmpty()
        // Synthetic component fixture, pinned to the real draft.4 family contract.
        // The pin is metadata, not support approval or execution certification.
        val carrier = if (typed) """"template_fit":{"schema_version":"landing/2",
            "category":"A","family":"fleet_exclusions",
            "registry_rev":"aece6ae3736e859a45fa3f1d4eb1974f58a87f64ba4d4d7ddc003ad2c1db1c98",
            "family_contract":{"family_id":"fleet_exclusions","catalog_version":"0.7.0-draft.4",
            "catalog_fingerprint":"aece6ae3736e859a45fa3f1d4eb1974f58a87f64ba4d4d7ddc003ad2c1db1c98",
            "record_fingerprint":"76c9fc7878b056030ccf9de3d5a4dd9d29df3b852ac5e68b87f11a29687df045",
            "param_set":"$paramSet"},"params":{"$name":{
            $valueField"unit":"$unit","relation":null,"condition":null,
            "quote":"designation input","source":{"kind":"rule","rule_id":"R-FLEET"}}}}"""
        else """"params":[{"name":"$name",$valueField"unit":"$unit","relation":"eq",
            "as_written":"designation input"}]"""
        val model = """{"meta":{"revision":1},"rules":[{"id":"R-FLEET","kind":"HARD",$carrier}]}"""
        val objective = """{"pairing":{"ready":true,"direction":"minimize","components":[]},
            "rostering":{"ready":true,"direction":"minimize","components":[]}}"""
        fun sha(s: String) = MessageDigest.getInstance("SHA-256").digest(s.toByteArray())
            .joinToString("") { "%02x".format(it) }
        // Only the explicit legacy carrier opts into archive parsing. Typed
        // fixtures retain strict reading; neither path is execution admission.
        val manifest = """{"spec_version":"1.18","customer":"code-unit","source_run":"code-unit",
            ${if (!typed) "\"landing_compat\":1," else ""}
            "revision":1,"readiness":"в","has_data":true,
            "allowed_modes":{"pairing":["feasibility-hard"],"rostering":["feasibility-hard"]},
            "objectives":{"pairing":[],"rostering":[]},"files":[
            {"name":"model.json","sha256":"${sha(model)}"},
            {"name":"objective.json","sha256":"${sha(objective)}"}]}"""
        val bytes = ByteArrayOutputStream()
        ZipOutputStream(bytes).use { zip ->
            for ((file, body) in mapOf("manifest.json" to manifest,
                    "model.json" to model, "objective.json" to objective)) {
                zip.putNextEntry(ZipEntry(file)); zip.write(body.toByteArray()); zip.closeEntry()
            }
        }
        return TaskPackage.readBytes(bytes.toByteArray(), landingCompat = if (!typed) 1 else null)
    }

    private fun resolution(pkg: LoadedTaskPackage) = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)

    @Test
    fun `both fleet aliases declare one report mapping and one measured codes-count knob`() {
        for (name in listOf("fleet_type", "fleet_types")) {
            val r = resolution(pkg(name, "\"B737\""))
            val mapping = RunThresholds.snapshot(r)["paramToThreshold"] as Map<*, *>
            assertEquals("fleet_types", mapping["fleet_type"])
            assertEquals("fleet_types", mapping["fleet_types"])
            val provider = RunThresholds.withRunModel(r.applied, emptyList(), packaged = true) {
                FlightCrewSchedulingConstraintProvider()
            }
            assertEquals(1, provider.readings()["fleet_types"])
            assertEquals(listOf("B737"), provider.codeReadings()["fleet_types"])
        }
    }

    @Test
    fun `legacy scalar null and NULL are actual opaque labels for both aliases`() {
        for (name in listOf("fleet_type", "fleet_types")) for (label in listOf("null", "NULL")) {
            val r = resolution(pkg(name, "\"$label\""))
            assertTrue(r.ignored.isEmpty(), "$name=$label must not disappear into ignored")
            assertEquals(listOf(label), r.applied["fleet_types"]?.codes)
            assertEquals(1, r.applied["fleet_types"]?.value)
        }
    }

    @Test
    fun `typed scalar code and list code_list retain literal null labels`() {
        for (label in listOf("null", "NULL")) {
            assertEquals(listOf(label), resolution(pkg("fleet_type", "\"$label\"", true))
                .applied["fleet_types"]?.codes)
            assertEquals(listOf(label), resolution(pkg("fleet_types", "[\"$label\"]", true))
                .applied["fleet_types"]?.codes)
        }
    }

    @Test
    fun `typed opaque list preserves a comma inside one label to the actual provider`() {
        val r = resolution(pkg("fleet_types", "[\"Airbus A320, special\",\"B737\"]", true))
        val provider = RunThresholds.withRunModel(r.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }
        assertEquals(listOf("Airbus A320, special", "B737"), provider.codeReadings()["fleet_types"])
        assertEquals(2, provider.readings()["fleet_types"])
    }

    @ParameterizedTest
    @ValueSource(strings = ["Airbus A320, special", " B737 ", "\tA320\t"])
    fun typedScalarCodeIsOneExactDesignationThroughActualProvider(label: String) {
        val r = assertDoesNotThrow<RunThresholds.Resolution> {
            resolution(pkg("fleet_type", ObjectMapper().writeValueAsString(label), true))
        }
        assertTrue(r.ignored.isEmpty(), "Typed designation must not disappear")
        assertEquals(listOf(label), r.applied["fleet_types"]?.codes)
        val provider = RunThresholds.withRunModel(r.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }
        assertEquals(listOf(label), provider.codeReadings()["fleet_types"])
        assertEquals(1, provider.readings()["fleet_types"])
    }

    @Test
    fun typedListDoesNotTrimOrMergeDistinctOpaqueDesignations() {
        val labels = listOf(" B737 ", "B737", "Airbus A320, special")
        val r = resolution(pkg("fleet_types", ObjectMapper().writeValueAsString(labels), true))
        val provider = RunThresholds.withRunModel(r.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }
        assertEquals(labels.sorted(), provider.codeReadings()["fleet_types"])
        assertEquals(3, provider.readings()["fleet_types"])
    }

    @Test
    fun `JSON null and a missing value refuse with their actual rule-param address`() {
        for (typed in listOf(false, true)) for (value in listOf("null", null)) {
            val error = assertThrows<TaskPackageError> { pkg("fleet_types", value, typed) }
            assertTrue(error.message!!.contains("R-FLEET"))
            assertTrue(error.message!!.contains("fleet_types"))
        }
    }

    @Test
    fun `malformed code carriers are not coerced into labels or silently dropped`() {
        for (value in listOf("[]", "[null]", "[\"B737\",7]", "[\"B737\",\"B737\"]",
                "{}", "7", "false", "\"\"")) {
            val error = assertThrows<TaskPackageError> { pkg("fleet_types", value, true) }
            assertTrue(error.message!!.contains("fleet_types"))
        }
        assertThrows<TaskPackageError> { pkg("fleet_type", "\"A,B\"") }
        assertThrows<TaskPackageError> { pkg("fleet_type", "[\"B737\"]", true) }
    }

    @Test
    fun `ordinary legacy comma-delimited lists retain their existing interpretation`() {
        val r = resolution(pkg("fleet_types", "\"A320, B737\""))
        assertEquals(listOf("A320", "B737"), r.applied["fleet_types"]?.codes)
        assertEquals(2, r.applied["fleet_types"]?.value)
        assertEquals(listOf("B737"), resolution(pkg("fleet_type", "\" B737 \""))
            .applied["fleet_types"]?.codes)
    }

    @Test
    fun `a designation resembling a base map is not interpreted as a numeric rule`() {
        val parsed = pkg("fleet_type", "\"ABC:1\"", true)
        assertEquals("code", parsed.hardParams.single().unit)
        assertNull(parsed.hardParams.single().qualifier)
        assertEquals(listOf("ABC:1"), resolution(parsed).applied["fleet_types"]?.codes)
    }
}
