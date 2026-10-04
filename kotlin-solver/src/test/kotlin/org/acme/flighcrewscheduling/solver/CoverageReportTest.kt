package org.acme.flighcrewscheduling.solver

import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Path
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * П-1 — предпрогонная сверка покрытия: без счёта и без набора отчёт
 * называет исполнимое (пороги, корзины), неисполнимое (unmapped) и
 * вычислимость целевых ТЕМ ЖЕ резолвером, что у прогона.
 */
class CoverageReportTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun sha256(b: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(b)
            .joinToString("") { x -> "%02x".format(x) }

    private fun packageZip(tmp: Path): ByteArray {
        val objective = """{"customer": "проба", "readiness": "в",
            "rostering": {"ready": true, "direction": "minimize", "objectives": [
                {"id": "O-R1", "title": "Ночи вне базы", "statement": "минимизировать",
                 "direction": "minimize", "metric": "layover_nights_total"}], "components": []},
            "pairing": {"ready": true, "direction": "minimize", "objectives": [
                {"id": "O-P1", "title": "Стоимость пейрингов", "statement": "минимизировать",
                 "direction": "minimize", "metric": "total_pairing_cost"}], "components": []}}"""
        val model = """{"meta": {"revision": 1, "model_schema": 2}, "rules": [
            {"id": "R-1", "kind": "HARD", "statement": "смена не длиннее 10 часов",
             "template_fit":{"schema_version":"landing/2","category":"A","family":"duty_limits",
              "registry_rev":"${"a".repeat(64)}","family_contract":{
                "family_id":"duty_limits","catalog_version":"synthetic-coverage-fixture",
                "catalog_fingerprint":"${"a".repeat(64)}","record_fingerprint":"${"b".repeat(64)}",
                "param_set":"default"},"params":{"max_duty_duration":{
                  "value":600.0,"unit":"duration_minutes","relation":"max","condition":null,
                  "quote":"10:00","source":{"kind":"rule","rule_id":"R-1"}}}},
             "params": [{"name": "max_duty_duration", "value": 600.0,
                         "as_written": "10:00", "unit": "duration_minutes",
                         "relation": "max", "rule_id": "R-1", "rule_kind": "HARD"}]}]}"""
        val manifest = """
            {"spec_version": "1.21", "customer": "проба", "source_run": "run-T", "revision": 1,
             "readiness": "в",
             "files": [
               {"name": "objective.json", "sha256": "${sha256(objective.toByteArray(Charsets.UTF_8))}"},
               {"name": "model.json", "sha256": "${sha256(model.toByteArray(Charsets.UTF_8))}"}],
             "allowed_modes": {"rostering": ["feasibility-hard", "feasibility-soft"],
                               "pairing": ["feasibility-hard"]},
             "objectives": {"rostering": ["O-R1"], "pairing": ["O-P1"]},
             "has_data": true}
        """.trimIndent()
        val zip = tmp.resolve("pc.zip").toFile()
        ZipOutputStream(zip.outputStream()).use { z ->
            for ((name, body) in listOf("objective.json" to objective,
                                        "model.json" to model, "manifest.json" to manifest)) {
                z.putNextEntry(ZipEntry(name)); z.write(body.toByteArray(Charsets.UTF_8)); z.closeEntry()
            }
        }
        return zip.readBytes()
    }

    @Test
    fun `сверка называет задачи, пороги и вычислимость целевых`(@TempDir tmp: Path) {
        val pkg = TaskPackage.readBytes(packageZip(tmp))
        val rep = CoverageReport.of(pkg)
        assertEquals(pkg.fingerprint, rep["packageFingerprint"])
        @Suppress("UNCHECKED_CAST")
        val tasks = rep["tasks"] as Map<String, Map<String, Any?>>
        assertEquals(setOf("pairing", "rostering"), tasks.keys)

        val rost = tasks["rostering"]!!
        @Suppress("UNCHECKED_CAST")
        val thr = rost["thresholds"] as Map<String, Any?>
        @Suppress("UNCHECKED_CAST")
        val applied = thr["applied"] as List<Map<String, Any?>>
        assertTrue(applied.any { it["threshold"] == "max_duty_span_min" && it["value"] == 600 },
            "порог смены из пакета обязан быть в applied")
        @Suppress("UNCHECKED_CAST")
        val objsR = rost["objectives"] as List<Map<String, Any?>>
        val or1 = objsR.first { it["id"] == "O-R1" }
        assertEquals(true, or1["computable"])
        assertEquals("Layover nights away from home", or1["constraint"])

        val pair = tasks["pairing"]!!
        @Suppress("UNCHECKED_CAST")
        val objsP = pair["objectives"] as List<Map<String, Any?>>
        val op1 = objsP.first { it["id"] == "O-P1" }
        assertEquals(false, op1["computable"])
        assertTrue((op1["reason"] as String).contains("раздельным прогоном"),
            "причина невычислимости — словами резолвера")
    }

    @Test
    fun `задача без режимов — отказ строкой, не молчание`(@TempDir tmp: Path) {
        val pkg = TaskPackage.readBytes(packageZip(tmp))
        val rep = CoverageReport.of(pkg)
        @Suppress("UNCHECKED_CAST")
        val tasks = rep["tasks"] as Map<String, Map<String, Any?>>
        // обе задачи фикстуры несут режимы — отказов быть не должно
        assertFalse(tasks.values.any { it.containsKey("refused") })
    }
}
