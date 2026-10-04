package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
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

/** Offline admission-prefix evidence, not a run or a solver stand.
 * Uses an actual native TEST release input; never changes its allowed_modes/support.
 * The historical fixture tests old-wire READING only, not permission to execute it.
 * Explicit -Da05.legacy.inputs=<component-inputs/st-window-1>; not a default *Test.
 */
class A05LegacyAdmissionProbe {
    private val mapper = ObjectMapper()
    private val ruleId = "R-STAND-duty_limits"
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }

    private fun unzip(bytes: ByteArray): LinkedHashMap<String, ByteArray> {
        val result = linkedMapOf<String, ByteArray>()
        ZipInputStream(ByteArrayInputStream(bytes)).use { stream ->
            while (true) {
                val entry = stream.nextEntry ?: break
                check(!entry.isDirectory)
                check(result.put(entry.name, stream.readBytes()) == null)
            }
        }
        return result
    }

    private fun zip(files: Map<String, ByteArray>): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { stream ->
            files.forEach { (name, bytes) ->
                stream.putNextEntry(ZipEntry(name))
                stream.write(bytes)
                stream.closeEntry()
            }
        }
        return out.toByteArray()
    }

    private fun withNative(block: (LinkedHashMap<String, ByteArray>) -> Unit) {
        val dir = Path.of(requireNotNull(System.getProperty("a05.legacy.inputs")))
            .toAbsolutePath().normalize()
        val names = listOf("identity.json", "st-window-1.zip", "st-window-1.world.json", "st-window-1.problem.json")
        val before = names.associateWith { Files.readAllBytes(dir.resolve(it)) }
        val hashes = before.mapValues { sha(it.value) }
        try {
            val identity = mapper.readTree(before.getValue("identity.json"))
            assertEquals("st-window-1", identity.path("task").asText())
            assertEquals("0.7.0", identity.path("catalog_version").asText())
            for (name in names.filter { it != "identity.json" }) {
                assertEquals(identity.path("files").path(name).asText(), hashes.getValue(name))
            }
            val files = unzip(before.getValue("st-window-1.zip"))
            val manifest = mapper.readTree(files.getValue("manifest.json"))
            assertEquals("1.20", manifest.path("spec_version").asText())
            assertEquals(listOf("feasibility-hard"), manifest.path("allowed_modes").path("pairing").map { it.asText() })
            assertTrue(manifest.path("allowed_modes").path("rostering").isEmpty)
            val model = mapper.readTree(files.getValue("model.json"))
            val fit = model.path("rules").single { it.path("id").asText() == ruleId }.path("template_fit")
            assertEquals("landing/2", fit.path("schema_version").asText())
            assertEquals(identity.path("catalog_fingerprint").asText(), fit.path("family_contract").path("catalog_fingerprint").asText())
            println("A05 native input identities=" + mapper.writeValueAsString(hashes))
            val healthy = TaskPackage.readBytes(zip(files))
            assertFalse(healthy.landingArchiveOnly)
            assertEquals(780, TaskModes.resolve("feasibility-hard", healthy, task = "pairing")
                .thresholds.applied.getValue("max_duty_span_min").value)
            block(files)
        } finally {
            assertEquals(hashes, names.associateWith { sha(Files.readAllBytes(dir.resolve(it))) })
        }
    }

    private fun mutate(files: Map<String, ByteArray>,
                       modelChange: (ObjectNode) -> Unit = {},
                       manifestChange: (ObjectNode) -> Unit = {},
                       change: (ObjectNode) -> Unit): ByteArray {
        val result = LinkedHashMap(files)
        val model = mapper.readTree(files.getValue("model.json")) as ObjectNode
        change(model.path("rules").single { it.path("id").asText() == ruleId } as ObjectNode)
        modelChange(model)
        result["model.json"] = mapper.writeValueAsBytes(model)
        val before = mapper.readTree(files.getValue("manifest.json")) as ObjectNode
        val manifest = before.deepCopy()
        val modelEntry = manifest.path("files").single { it.path("name").asText() == "model.json" } as ObjectNode
        modelEntry.put("sha256", sha(result.getValue("model.json")))
        modelEntry.put("bytes", result.getValue("model.json").size)
        manifestChange(manifest)
        result["manifest.json"] = mapper.writeValueAsBytes(manifest)
        // Only the named test mutation and the model digest/size may change.
        val normalized = manifest.deepCopy()
        val normalizedEntry = normalized.path("files").single { it.path("name").asText() == "model.json" } as ObjectNode
        val originalEntry = before.path("files").single { it.path("name").asText() == "model.json" }
        normalizedEntry.set<com.fasterxml.jackson.databind.JsonNode>("sha256", originalEntry.path("sha256"))
        normalizedEntry.set<com.fasterxml.jackson.databind.JsonNode>("bytes", originalEntry.path("bytes"))
        val expectedManifest = before.deepCopy()
        manifestChange(expectedManifest)
        assertEquals(expectedManifest, normalized, "Undeclared manifest mutation")
        for ((name, bytes) in files) if (name !in setOf("model.json", "manifest.json")) {
            assertArrayEquals(bytes, result.getValue(name), name)
        }
        for (entry in manifest.path("files")) {
            assertEquals(entry.path("sha256").asText(), sha(result.getValue(entry.path("name").asText())))
        }
        return zip(result)
    }

    private fun stripHeaders(rule: ObjectNode) {
        val fit = rule.path("template_fit") as ObjectNode
        fit.remove("schema_version")
        fit.remove("family_contract")
    }

    private fun assertFormatRefusal(code: String, block: () -> Unit) {
        val error = assertThrows<TaskPackageError> { block() }
        assertTrue(error.message.orEmpty().contains(code), "Wrong-reason refusal: ${error.message}")
        assertFalse(error.message.orEmpty().contains("контрольная сумма"), "Must reach semantic format gate")
    }

    private fun assertArchiveCannotExecute(pkg: LoadedTaskPackage) {
        assertTrue(pkg.landingArchiveOnly)
        for (task in listOf("pairing", "rostering")) {
            for (mode in listOf("feasibility-hard", "feasibility-soft", "optimization")) {
                val error = assertThrows<TaskModeRefused> { TaskModes.resolve(mode, pkg, task = task) }
                assertTrue(error.message.orEmpty().contains("landing-version"),
                    "Archive refusal must precede allowed_modes/objectives: ${error.message}")
            }
        }
    }

    private fun admitPrefix(bytes: ByteArray): ResolvedTaskMode {
        // Same package reader and mode gate as PackagedRuns.start; no problem mutation,
        // facts check, solver, executor, HTTP handler, or live admission is invoked.
        val pkg = TaskPackage.readBytes(bytes)
        return TaskModes.resolve("feasibility-hard", pkg, task = "pairing")
    }

    @Test
    fun actualModernNativePackageRemainsHealthy() = withNative { files ->
        val mode = admitPrefix(zip(files))
        assertEquals("feasibility-hard", mode.mode)
        assertEquals(780, mode.thresholds.applied.getValue("max_duty_span_min").value)
    }

    @ParameterizedTest
    @ValueSource(strings = ["dual-carrier", "landing-only", "divergent-legacy"])
    fun strippingBothModernLandingHeadersCannotEnableHistoricalFallback(variant: String) = withNative { files ->
        // A healthy admission immediately precedes every negative: unrelated refusal is not evidence.
        assertEquals(780, admitPrefix(zip(files)).thresholds.applied.getValue("max_duty_span_min").value)
        val mutant = mutate(files) { rule ->
            val fit = rule.path("template_fit") as ObjectNode
            fit.remove("schema_version")
            fit.remove("family_contract")
            when (variant) {
                "landing-only" -> rule.remove("params")
                "divergent-legacy" -> (rule.path("params").single { it.path("name").asText() == "max_duty_duration" } as ObjectNode)
                    .put("value", 30)
            }
        }
        val error = assertThrows<RuntimeException>("Modern spec1.20 must refuse missing landing headers, not fall back ($variant)") {
            val admitted = admitPrefix(mutant)
            println("A05 MISSING GUARD: variant=$variant admitted=${admitted.mode} duty=" +
                admitted.thresholds.applied.getValue("max_duty_span_min").value)
        }
        assertTrue(error is TaskPackageError || error is TaskModeRefused, "Unexpected exception: $error")
        assertTrue(error.message.orEmpty().contains("landing-version"), "Wrong-reason refusal: ${error.message}")
        assertFalse(error.message.orEmpty().contains("контрольная сумма"), "Rehash must reach landing validation")
    }

    @ParameterizedTest
    @ValueSource(strings = ["missing-fit", "missing-fit-and-params", "excluded-malformed-fit",
        "unknown-version", "excluded-unknown-version", "missing-typed-params"])
    fun modernFormatCannotDisappearBeforeParameterReading(variant: String) = withNative { files ->
        val mutant = mutate(files) { rule ->
            when (variant) {
                "missing-fit" -> rule.remove("template_fit")
                "missing-fit-and-params" -> { rule.remove("template_fit"); rule.remove("params") }
                "excluded-malformed-fit" -> {
                    rule.putObject("excluded_until_data").put("question", "Q-EXPLICIT-EXCLUSION")
                    rule.put("template_fit", "not-an-object")
                }
                "unknown-version", "excluded-unknown-version" -> {
                    (rule.path("template_fit") as ObjectNode).put("schema_version", "landing/999")
                    if (variant.startsWith("excluded"))
                        rule.putObject("excluded_until_data").put("question", "Q-EXPLICIT-EXCLUSION")
                }
                "missing-typed-params" -> (rule.path("template_fit") as ObjectNode).remove("params")
            }
        }
        val code = if (variant in setOf("excluded-malformed-fit", "missing-typed-params"))
            "landing-incomplete" else "landing-version"
        assertFormatRefusal(code) { TaskPackage.readBytes(mutant) }
    }

    @ParameterizedTest
    @ValueSource(strings = ["boolean", "string", "zero", "null"])
    fun declaredCompatibilityMustBeTheIntegerOne(variant: String) = withNative { files ->
        val mutant = mutate(files, manifestChange = { manifest ->
            when (variant) {
                "boolean" -> manifest.put("landing_compat", true)
                "string" -> manifest.put("landing_compat", "1")
                "zero" -> manifest.put("landing_compat", 0)
                "null" -> manifest.putNull("landing_compat")
            }
        }) { stripHeaders(it) }
        assertFormatRefusal("landing-version") { TaskPackage.readBytes(mutant, landingCompat = 1) }
    }

    @ParameterizedTest
    @ValueSource(strings = ["reader-only", "manifest-only"])
    fun oneCompatibilityFlagDoesNotAuthorizeModernLegacyReading(variant: String) = withNative { files ->
        val mutant = mutate(files, manifestChange = {
            if (variant == "manifest-only") it.put("landing_compat", 1)
        }) { stripHeaders(it) }
        assertFormatRefusal("landing-version") {
            TaskPackage.readBytes(mutant, landingCompat = if (variant == "reader-only") 1 else null)
        }
    }

    @Test
    fun doubleExplicitCompatibilityIsArchiveOnlyAndDoesNotLeakToStrictReaders(@TempDir temp: Path) = withNative { files ->
        val mutant = mutate(files, manifestChange = { it.put("landing_compat", 1) }) { stripHeaders(it) }
        val archive = TaskPackage.readBytes(mutant, landingCompat = 1)
        assertEquals(listOf("feasibility-hard"), archive.allowedModes["pairing"])
        assertArchiveCannotExecute(archive)
        assertFormatRefusal("landing-version") { TaskPackage.readBytes(mutant) }
        val path = temp.resolve("archive.zip")
        Files.write(path, mutant)
        TaskPackage.overrideForTests(null)
        try {
            assertArchiveCannotExecute(TaskPackage.read(path.toFile(), landingCompat = 1))
            assertFormatRefusal("landing-version") { TaskPackage.cachedByManifest(path.toFile()) }
            assertFormatRefusal("landing-version") { TaskPackage.cachedByManifest(path.toFile()) }
            assertArrayEquals(mutant, Files.readAllBytes(path))
        } finally {
            TaskPackage.overrideForTests(null)
        }
    }

    @ParameterizedTest
    @ValueSource(strings = ["typed-intact", "headers-stripped", "all-carriers-stripped", "modern-meta-retained"])
    fun downgradingThePackageHeaderCannotEnableExecution(variant: String) = withNative { files ->
        val mutant = mutate(files, modelChange = { model ->
            val meta = model.path("meta") as ObjectNode
            if (variant == "modern-meta-retained") meta.put("model_schema", 2) else meta.remove("model_schema")
        // 1.19 keeps native rule_maps legal (introduced at 1.14), so this
        // reaches landing admission instead of an unrelated spec/map error.
        }, manifestChange = { it.put("spec_version", "1.19") }) { rule ->
            when (variant) {
                "headers-stripped", "modern-meta-retained" -> stripHeaders(rule)
                "all-carriers-stripped" -> { rule.remove("template_fit"); rule.remove("params") }
            }
        }
        if (variant != "typed-intact") {
            assertFormatRefusal("landing-version") { TaskPackage.readBytes(mutant) }
            assertFormatRefusal("landing-version") { TaskPackage.readBytes(mutant, landingCompat = 1) }
            val archiveFiles = unzip(mutant)
            val archiveManifest = mapper.readTree(archiveFiles.getValue("manifest.json")) as ObjectNode
            archiveManifest.put("landing_compat", 1)
            archiveFiles["manifest.json"] = mapper.writeValueAsBytes(archiveManifest)
            val explicitArchive = zip(archiveFiles)
            assertFormatRefusal("landing-version") { TaskPackage.readBytes(explicitArchive) }
            assertArchiveCannotExecute(TaskPackage.readBytes(explicitArchive, landingCompat = 1))
        } else {
            val archive = TaskPackage.readBytes(mutant)
            assertEquals("1.19", archive.specVersion)
            assertEquals(listOf("feasibility-hard"), archive.allowedModes["pairing"])
            assertArchiveCannotExecute(archive)
        }
    }

    @Test
    fun genuineHistoricalWireRemainsReadableWithoutGrantingExecution() {
        // A separately declared old format, NOT the modern native ZIP with a downgraded header.
        val model = """{"meta":{"revision":1},"rules":[{"id":"LEGACY-DUTY","kind":"HARD","params":[{"name":"max_duty_duration","value":720,"unit":"duration_minutes","relation":"max","as_written":"12 hours"}]}]}""".toByteArray()
        val objective = """{"pairing":{"components":[]},"rostering":{"components":[]}}""".toByteArray()
        val files = linkedMapOf("model.json" to model, "objective.json" to objective)
        val manifest = mapper.createObjectNode().put("spec_version", "1.3").put("revision", 1)
        manifest.put("customer", "explicit historical reader-only fixture")
        manifest.putObject("allowed_modes").also { it.putArray("pairing"); it.putArray("rostering") }
        manifest.putObject("objectives").also { it.putArray("pairing"); it.putArray("rostering") }
        val listed = manifest.putArray("files")
        for ((name, bytes) in files) listed.addObject().put("name", name).put("sha256", sha(bytes)).put("bytes", bytes.size)
        files["manifest.json"] = mapper.writeValueAsBytes(manifest)
        assertFormatRefusal("landing-version") { TaskPackage.readBytes(zip(files)) }
        assertFormatRefusal("landing-version") { TaskPackage.readBytes(zip(files), landingCompat = 1) }
        manifest.put("landing_compat", 1)
        files["manifest.json"] = mapper.writeValueAsBytes(manifest)
        assertFormatRefusal("landing-version") { TaskPackage.readBytes(zip(files)) }
        val pkg = TaskPackage.readBytes(zip(files), landingCompat = 1)
        assertEquals("1.3", pkg.specVersion)
        val param = pkg.hardParams.single()
        assertEquals("max_duty_duration", param.name)
        assertEquals(720.0, param.value)
        assertEquals("duration_minutes", param.unit)
        assertEquals("max", param.relation)
        assertTrue(pkg.allowedModes.values.all { it.isEmpty() })
        val refused = assertThrows<TaskModeRefused> { TaskModes.resolve("feasibility-hard", pkg, task = "pairing") }
        assertTrue(pkg.landingArchiveOnly)
        assertTrue(refused.message.orEmpty().contains("landing-version"))
    }
}
