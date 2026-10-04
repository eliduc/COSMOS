package org.acme.flighcrewscheduling.solver

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.CsvSource
import org.junit.jupiter.params.provider.ValueSource
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Actual ZIP/loader/threshold resolution; no provider, solve, HTTP or release assertion. */
class A05DutyExtensionThresholdAgentTest {
    // Explicit current-wire metadata for synthetic threshold inputs. No
    // compatibility flags, parameter-value corrections or production mocks.
    private fun landing(family: String, cells: String) = """{
        "schema_version":"landing/2","category":"A","family":"$family",
        "registry_rev":"${"a".repeat(64)}","family_contract":{
          "family_id":"$family","catalog_version":"synthetic-threshold-fixture",
          "catalog_fingerprint":"${"a".repeat(64)}",
          "record_fingerprint":"${"b".repeat(64)}","param_set":"default"},
        "params":{$cells}}"""

    private fun cell(rule: String, name: String, value: String) = """"$name":{
        "value":$value,"unit":"duration_minutes","relation":"max",
        "condition":null,"quote":"synthetic $name",
        "source":{"kind":"rule","rule_id":"$rule"}}"""

    private fun pkg(base: Int?, alias: String? = null, legacyAlias: String? = null): LoadedTaskPackage {
        val hard = if (base == null) "" else """
            {"id":"R-BASE","kind":"HARD","export_solver":{"family":"duty_limits"},
             "template_fit":${landing("duty_limits", cell("R-BASE", "max_duty_duration", base.toString()))},
             "params":[{"name":"max_duty_duration","value":$base,
                        "unit":"duration_minutes","relation":"max"}]},
        """.trimIndent()
        val aliasCell = if (alias == null) "" else "," + cell("R-EXT", "long_duty_threshold", alias)
        val legacy = if (legacyAlias == null) "" else
            """, "params":[{"name":"long_duty_threshold","value":$legacyAlias,
              "unit":"duration_minutes","relation":"max"}]"""
        val model = """{"meta":{"revision":1,"model_schema":2},"rules":[$hard
            {"id":"R-EXT","kind":"SOFT","template_fit":${landing("duty_extension_penalty",
                cell("R-EXT", "duty_extension_cap", "960") + aliasCell)}$legacy}]}"""
        val objective = """{"pairing":{"components":[]},"rostering":{"components":[]}}"""
        fun sha(text: String): String = MessageDigest.getInstance("SHA-256")
            .digest(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        val manifest = """{"spec_version":"1.20","customer":"synthetic","source_run":"threshold",
            "revision":1,"has_data":true,"allowed_modes":{"rostering":["feasibility-soft"]},
            "objectives":{"pairing":[],"rostering":[]},"files":[
              {"name":"model.json","sha256":"${sha(model)}"},
              {"name":"objective.json","sha256":"${sha(objective)}"}]}"""
        val bytes = ByteArrayOutputStream()
        ZipOutputStream(bytes).use { zip ->
            for ((name, text) in linkedMapOf("manifest.json" to manifest,
                "model.json" to model, "objective.json" to objective)) {
                zip.putNextEntry(ZipEntry(name))
                zip.write(text.toByteArray(Charsets.UTF_8))
                zip.closeEntry()
            }
        }
        return TaskPackage.readBytes(bytes.toByteArray())
    }

    private fun resolution(base: Int?, alias: String?) =
        RunThresholds.resolveFor(pkg(base, alias), TaskModes.PROFILE_DEFAULT)

    private fun refused(base: Int?, alias: String) {
        val error = runCatching { resolution(base, alias) }.exceptionOrNull()
        assertTrue(error is TaskPackageError || error is TaskModeRefused, "$error")
        val words = error!!.message.orEmpty()
        assertTrue(words.contains("R-EXT"), words)
        assertTrue(words.contains("long_duty_threshold") || words.contains("баз"), words)
    }

    @ParameterizedTest
    @ValueSource(ints = [720, 780])
    fun matchingAliasPreservesExactBaseAndScopedPolicy(base: Int) {
        val result = resolution(base, base.toString())
        assertEquals(base, result.applied.getValue("max_duty_span_min").value)
        assertEquals(base, result.applied.getValue(RunThresholds.DUTY_EXTENSION_THRESHOLD).value)
        assertEquals(960, result.applied.getValue("max_duty_span_min").dutyExtensions.single().capMin)
    }

    @Test
    fun absentLegacyAliasUsesTheDeclaredHardBase() {
        val result = resolution(780, null)
        assertEquals(780, result.applied.getValue(RunThresholds.DUTY_EXTENSION_THRESHOLD).value)
    }

    @ParameterizedTest
    @CsvSource("720,780", "780,720")
    fun differentAliasIsNotSilentlyReplacedByBase(base: Int, alias: Int) {
        refused(base, alias.toString())
    }

    @ParameterizedTest
    @ValueSource(strings = ["null", "true", "\"720\"", "720.5", "-1", "0", "2147483648"])
    fun malformedAliasIsNotIgnored(alias: String) {
        refused(720, alias)
    }

    @Test
    fun aliasCannotInventAnAbsentHardBase() {
        refused(null, "720")
    }

    @Test
    fun mismatchIsRefusedEvenWhenCapDoesNotRaiseBase() {
        refused(960, "720")
    }

    @Test
    fun matchingLegacyAliasIsPreserved() {
        val result = RunThresholds.resolveFor(pkg(720, null, "720"), TaskModes.PROFILE_DEFAULT)
        assertEquals(720, result.applied.getValue(RunThresholds.DUTY_EXTENSION_THRESHOLD).value)
        assertEquals(720, result.applied.getValue("max_duty_span_min")
            .dutyExtensions.single().snapshot()["long_duty_threshold"])
    }

    @ParameterizedTest
    @ValueSource(strings = ["780", "720.5"])
    fun legacyCarrierCannotBypassThresholdContract(alias: String) {
        val error = runCatching {
            RunThresholds.resolveFor(pkg(720, null, alias), TaskModes.PROFILE_DEFAULT)
        }.exceptionOrNull()
        assertTrue(error is TaskPackageError || error is TaskModeRefused, "$error")
        assertTrue(error!!.message.orEmpty().contains("R-EXT"))
        assertTrue(error.message.orEmpty().contains("long_duty_threshold"))
    }

    @Test
    fun conflictingFitAndLegacyAliasesAreNotSilentlyPrioritized() {
        val error = assertThrows(TaskPackageError::class.java) { pkg(720, "720", "780") }
        assertTrue(error.message.orEmpty().contains("R-EXT"))
        assertTrue(error.message.orEmpty().contains("long_duty_threshold"))
    }
}
