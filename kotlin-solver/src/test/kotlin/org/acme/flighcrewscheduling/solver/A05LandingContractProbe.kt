package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

/** Explicit offline receiver-contract probe against the actual saved native ZIP.
 * P0-CONTRACTS-v1.13 section 3.1: landing-version / landing-incomplete /
 * param-carriers-diverge are receiver refusals, not admission or checksum errors.
 * No permissions/support/original input is changed. Not default-suite *Test.
 */
class A05LandingContractProbe {
    private val mapper = ObjectMapper().findAndRegisterModules()
    private val ruleId = "R-STAND-duty_limits"
    private val param = "max_duty_duration"

    private fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }

    private fun entries(bytes: ByteArray): LinkedHashMap<String, ByteArray> {
        val result = linkedMapOf<String, ByteArray>()
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                check(!entry.isDirectory)
                check(result.put(entry.name, zip.readBytes()) == null)
            }
        }
        return result
    }

    private fun zip(entries: Map<String, ByteArray>): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zip ->
            for ((name, bytes) in entries) {
                zip.putNextEntry(ZipEntry(name))
                zip.write(bytes)
                zip.closeEntry()
            }
        }
        return out.toByteArray()
    }

    private fun withNative(block: (LinkedHashMap<String, ByteArray>) -> Unit) {
        val dir = Path.of(requireNotNull(System.getProperty("a06.native.inputs")))
            .toAbsolutePath().normalize()
        val names = listOf("st-duty-1.zip", "st-duty-1.problem.json", "st-duty-1.world.json", "identity.json")
        val original = names.associateWith { Files.readAllBytes(dir.resolve(it)) }
        val hashes = original.mapValues { sha(it.value) }
        try {
            val identity = mapper.readTree(original.getValue("identity.json"))
            assertEquals("st-duty-1", identity.path("task").asText())
            assertEquals("0.7.0-draft.3", identity.path("catalog_version").asText())
            for (name in names.filter { it != "identity.json" }) {
                assertEquals(identity.path("files").path(name).asText(), hashes.getValue(name))
            }
            block(entries(original.getValue("st-duty-1.zip")))
        } finally {
            assertEquals(hashes, names.associateWith { sha(Files.readAllBytes(dir.resolve(it))) },
                "Native saved inputs must remain byte-identical")
            RunThresholds.clearForTests()
            LastProviderReadings.clearForTests()
        }
    }

    private fun mutate(original: Map<String, ByteArray>, change: (ObjectNode) -> Unit): ByteArray {
        val result = LinkedHashMap(original)
        val model = mapper.readTree(original.getValue("model.json")) as ObjectNode
        val rule = model.path("rules").single { it.path("id").asText() == ruleId } as ObjectNode
        change(rule)
        result["model.json"] = mapper.writeValueAsBytes(model)
        val before = mapper.readTree(original.getValue("manifest.json")) as ObjectNode
        val manifest = before.deepCopy()
        val modelEntry = manifest.path("files").single { it.path("name").asText() == "model.json" } as ObjectNode
        modelEntry.put("sha256", sha(result.getValue("model.json")))
        modelEntry.put("bytes", result.getValue("model.json").size)
        result["manifest.json"] = mapper.writeValueAsBytes(manifest)
        assertEquals(before.path("allowed_modes"), manifest.path("allowed_modes"))
        val expected = before.deepCopy()
        val expectedEntry = expected.path("files").single { it.path("name").asText() == "model.json" } as ObjectNode
        expectedEntry.put("sha256", sha(result.getValue("model.json")))
        expectedEntry.put("bytes", result.getValue("model.json").size)
        assertEquals(expected, manifest, "Only model checksum/length may change in manifest")
        for ((name, bytes) in original) if (name !in setOf("model.json", "manifest.json")) {
            assertArrayEquals(bytes, result.getValue(name), name)
        }
        for (file in manifest.path("files")) {
            assertEquals(file.path("sha256").asText(), sha(result.getValue(file.path("name").asText())),
                "All manifest checksums must pass before judging landing")
        }
        return zip(result)
    }

    private fun resolve(bytes: ByteArray): Int {
        val pkg = TaskPackage.readBytes(bytes)
        assertTrue(pkg.allowedModes["pairing"].orEmpty().isEmpty(), "No pairing admission was granted")
        return RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
            .applied.getValue("max_duty_span_min").value
    }

    private fun requireRefusal(bytes: ByteArray, marker: String) {
        val error = assertThrows<TaskPackageError>("Receiver must refuse $marker, not choose either carrier") {
            val applied = resolve(bytes)
            println("A05 CONTRACT NOT ENFORCED: expected=$marker; actual duty threshold=$applied")
        }
        assertTrue(error.message.orEmpty().contains(marker),
            "Wrong-reason refusal must not count: expected=$marker; actual=${error.message}")
        assertFalse(error.message.orEmpty().contains("контрольная сумма"), error.message)
    }

    @Test
    fun originalNativeDualCarrierRemainsHealthy() = withNative {
        assertEquals(780, resolve(zip(it)))
    }

    @Test
    fun nativeLandingOnlyRemainsHealthy() = withNative {
        assertEquals(780, resolve(mutate(it) { rule -> rule.remove("params") }))
    }

    @Test
    fun upstreamChecksumGuardRemainsEffective() = withNative {
        val changed = LinkedHashMap(it)
        changed["model.json"] = changed.getValue("model.json") + byteArrayOf(32)
        val error = assertThrows<TaskPackageError> { TaskPackage.readBytes(zip(changed)) }
        assertTrue(error.message.orEmpty().contains("контрольная сумма model.json"), error.message)
    }

    @ParameterizedTest
    @ValueSource(strings = ["landing/999", "MISSING"])
    fun unknownOrMissingLandingVersionRefusesWithoutLegacyFallback(version: String) = withNative {
        requireRefusal(mutate(it) { rule ->
            val fit = rule.path("template_fit") as ObjectNode
            if (version == "MISSING") fit.remove("schema_version") else fit.put("schema_version", version)
            // Keep the valid legacy carrier: a fallback would hide the invalid header.
        }, "landing-version")
    }

    @ParameterizedTest
    @ValueSource(strings = ["unit", "source"])
    fun incompleteTypedCellRefusesInsteadOfInventingFields(field: String) = withNative {
        requireRefusal(mutate(it) { rule ->
            rule.remove("params")
            (rule.path("template_fit").path("params").path(param) as ObjectNode).remove(field)
        }, "landing-incomplete")
    }

    @ParameterizedTest
    @ValueSource(strings = ["value", "unit", "relation"])
    fun divergentCarriersRefuseAtReceiver(field: String) = withNative {
        requireRefusal(mutate(it) { rule ->
            val legacy = (rule.path("params") as ArrayNode).single { it.path("name").asText() == param } as ObjectNode
            when (field) {
                "value" -> legacy.put("value", 30)
                "unit" -> legacy.put("unit", "count")
                "relation" -> legacy.put("relation", "min")
            }
        }, "param-carriers-diverge")
    }

    @ParameterizedTest
    @ValueSource(strings = ["condition", "relation", "quote", "value"])
    fun additionalRequiredTypedFieldsCannotDisappear(field: String) = withNative {
        requireRefusal(mutate(it) { rule ->
            rule.remove("params")
            (rule.path("template_fit").path("params").path(param) as ObjectNode).remove(field)
        }, "landing-incomplete")
    }

    private fun replaceCellField(rule: ObjectNode, field: String, json: String) {
        rule.remove("params")
        (rule.path("template_fit").path("params").path(param) as ObjectNode)
            .set<JsonNode>(field, mapper.readTree(json))
    }

    @ParameterizedTest
    @ValueSource(strings = ["null", "[]", "\"code\"", "{}",
        "{\"kind\":\"code\"}", "{\"kind\":\"code\",\"note\":\" \"}",
        "{\"kind\":\"rule\",\"rule_id\":\"OTHER-RULE\"}"])
    fun invalidSourceIsNotAcceptedAsProvenance(json: String) = withNative {
        requireRefusal(mutate(it) { rule -> replaceCellField(rule, "source", json) }, "landing-incomplete")
    }

    @Test
    fun sourceMayNameItsActualCarrierRule() = withNative {
        assertEquals(780, resolve(mutate(it) { rule ->
            replaceCellField(rule, "source", """{"kind":"rule","rule_id":"$ruleId"}""")
        }))
    }

    @ParameterizedTest
    @ValueSource(strings = ["\"база\"", "[]", "{}",
        "{\"kind\":\"area\"}", "{\"kind\":\"area\",\"value\":7}",
        "{\"kind\":\"invented-scope\",\"value\":\"база\"}",
        "{\"kind\":\"area\",\"value\":\"база\",\"extra\":true}"])
    fun malformedConditionCannotBecomeGlobal(json: String) = withNative {
        requireRefusal(mutate(it) { rule -> replaceCellField(rule, "condition", json) }, "landing-incomplete")
    }

    @Test
    fun diagnosticWireScopeIsPreservedAndNeverAppliedAsGlobalDuty() = withNative {
        // Explicit wire derivative, NOT a claim that this scope belongs to
        // duty_limits in the pinned catalogue or that this is export-ready.
        val pkg = TaskPackage.readBytes(mutate(it) { rule ->
            replaceCellField(rule, "condition", """{"kind":"area","value":"база"}""")
        })
        val cell = pkg.hardParams.single { p -> p.name == param }
        assertEquals("база", cell.qualifier)
        assertEquals(780.0, cell.value)
        assertEquals("duration_minutes", cell.unit)
        assertEquals("max", cell.relation)
        val resolved = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        assertFalse(resolved.applied.containsKey("max_duty_span_min"),
            "Scoped duty must never silently become a global applied threshold")
        val ignored = resolved.ignored.single { item -> item.name == param }
        assertEquals(listOf(ruleId), ignored.rules)
        assertEquals(listOf("база"), ignored.qualifiers)
        assertTrue(ignored.reason.contains("сужает область"), ignored.reason)
        assertEquals(780, resolve(zip(it)), "Unscoped healthy control must still apply")
    }

    @Test
    fun typedScopeAndGlobalLegacyCarrierCannotDisagree() = withNative {
        requireRefusal(mutate(it) { rule ->
            (rule.path("template_fit").path("params").path(param) as ObjectNode)
                .set<JsonNode>("condition", mapper.readTree("""{"kind":"area","value":"база"}"""))
        }, "param-carriers-diverge")
    }

    @ParameterizedTest
    @ValueSource(strings = ["\"780\"", "true", "false", "null", "[]", "{}"])
    fun numericWireValueCannotBeCoercedFromAnotherJsonType(json: String) = withNative {
        requireRefusal(mutate(it) { rule -> replaceCellField(rule, "value", json) }, "landing-incomplete")
    }

    @ParameterizedTest
    @ValueSource(strings = ["0", "-1", "780.5", "2147483648"])
    fun invalidExecutableIntegerBoundaryRefusesAtActualThresholdGuard(json: String) = withNative {
        // The existing execution law, not a new wire-level domain invented by
        // this test: never truncate, overflow, or clamp a positive integer knob.
        val pkg = TaskPackage.readBytes(mutate(it) { rule -> replaceCellField(rule, "value", json) })
        assertEquals(mapper.readTree(json).asDouble(), pkg.hardParams.single { p -> p.name == param }.value)
        val error = assertThrows<TaskModeRefused> { RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT) }
        assertTrue(error.message.orEmpty().contains(param), error.message)
        assertTrue(error.message.orEmpty().contains(ruleId), error.message)
        assertTrue(error.message.orEmpty().contains("целым положительным числом"), error.message)
    }

    @ParameterizedTest
    @ValueSource(strings = ["1", "780", "780.0", "1440"])
    fun validNumericBoundariesRetainTheirExactThreshold(json: String) = withNative {
        assertEquals(mapper.readTree(json).asInt(),
            resolve(mutate(it) { rule -> replaceCellField(rule, "value", json) }))
    }
}
