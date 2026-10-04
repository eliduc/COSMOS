package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.time.DayOfWeek
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

/** Explicit cross-language READ probe. No catalog support evidence is emitted,
 * no solver/stand/service is called. Not a default-discovery *Test.
 */
class A06ComponentInputReadProbe {
    private val mapper = ObjectMapper()
    private fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes)
        .joinToString("") { "%02x".format(it) }

    @Test fun readActualPythonCalendarEnvelopeAndKeepExecutionClosed() {
        val path = Path.of(requireNotNull(System.getProperty("a06.component.readInput"))).toAbsolutePath().normalize()
        val expectedSha = requireNotNull(System.getProperty("a06.component.readSha256")).lowercase()
        require(Regex("[0-9a-f]{64}").matches(expectedSha))
        val original = Files.readAllBytes(path)
        assertEquals(expectedSha, sha(original), "Actual producer artifact changed before this probe")
        try {
            val input = ComponentInput.readBytes(original)
            assertEquals("0.7.0-draft.6", input.catalogVersion)
            assertEquals("a7180288f0c54b70a8009e6efcda730aec648e88ad9b8d148a51641664b8df81", input.catalogFingerprint)
            assertEquals("families/calendar_windows", input.record)
            assertEquals("st-window-1", input.taskId)
            assertFalse(LoadedTaskPackage::class.java.isInstance(input))
            val native = input.nativeModel
            assertEquals(mapOf("min_turnaround" to 45.0, "max_duty_duration" to 780.0,
                "max_pairing_days" to 7.0, "min_rest_home" to 660.0,
                "min_rest_outstation" to 700.0, "base_tz_offset_min" to 0.0),
                native.hardParams.associate { it.name to it.value })
            assertEquals(6, native.hardParams.size)
            assertEquals(1L, native.weights.getValue("Calendar window layover penalty"))
            val calendar = native.calendarWindows.single()
            assertEquals("R-STAND-calendar_windows", calendar.ruleId)
            assertEquals(setOf(DayOfWeek.THURSDAY), calendar.days)
            assertEquals("layover_penalty", calendar.action)
            val executableFailure = assertThrows<TaskPackageError> { TaskPackage.readBytes(original) }
            assertTrue(executableFailure.message.orEmpty().contains("component-only"))

            // Causal receiver control on those same real producer bytes: a
            // missing typed unit must be caught after fresh valid file hashes,
            // not hidden behind the archive hash guard or legacy carrier.
            val members = linkedMapOf<String, ByteArray>()
            ZipInputStream(ByteArrayInputStream(original)).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    require(!entry.isDirectory && entry.name !in members)
                    members[entry.name] = zip.readBytes()
                }
            }
            val model = mapper.readTree(members.getValue("model.json")) as ObjectNode
            val rule = model.path("rules").single { it.path("id").asText() == "R-STAND-rest_floors" }
            (rule.path("template_fit").path("params").path("min_rest_outstation") as ObjectNode).remove("unit")
            members["model.json"] = mapper.writeValueAsBytes(model)
            val manifest = mapper.readTree(members.getValue("component-manifest.json")) as ObjectNode
            val entry = manifest.path("files").single { it.path("name").asText() == "model.json" } as ObjectNode
            entry.put("sha256", sha(members.getValue("model.json"))).put("bytes", members.getValue("model.json").size)
            members["component-manifest.json"] = mapper.writeValueAsBytes(manifest)
            val modified = ByteArrayOutputStream()
            ZipOutputStream(modified).use { zip -> members.forEach { (name, bytes) ->
                zip.putNextEntry(ZipEntry(name)); zip.write(bytes); zip.closeEntry()
            } }
            val bad = assertThrows<TaskPackageError> { ComponentInput.readBytes(modified.toByteArray()) }
            assertTrue(bad.message.orEmpty().contains("landing-incomplete"), bad.message)
            assertFalse(bad.message.orEmpty().contains("hash mismatch"), bad.message)
        } finally {
            assertEquals(expectedSha, sha(Files.readAllBytes(path)), "Read probe must not change the producer artifact")
        }
    }
}
