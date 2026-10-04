package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.nio.file.Path
import java.security.MessageDigest

/**
 * Волна П-2, этап 2: мягкое правило со СЛОВАРНЫМ шаблоном (demand.kind из
 * TEMPLATE_TO_FORM, числа ТОЛЬКО ссылками-ролями `*_param`) порождается
 * мягким планом (литерал 1, вес компоненты — весами пакета); всё прочее
 * мягкое — прежний notHard байт-в-байт. Роли судятся словарём: недостающая
 * или лишняя роль, чужая единица — честный отказ, решатель не угадывает.
 */
class P2WaveCompilerTest {

    @AfterEach
    fun cleanup() {
        TaskPackage.overrideForTests(null)
        LastRun.forget()
    }

    private fun sha256(b: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(b)
            .joinToString("") { x -> "%02x".format(x) }

    private fun writePackage(dir: Path, rulesJson: String): File {
        val d = dir.toFile()
        val objective = """{"customer": "проба", "readiness": "в",
            "rostering": {"ready": true, "direction": "minimize", "objectives": [], "components": []}}"""
        val model = """{"meta": {"revision": 3, "model_schema": 2}, "rules": [$rulesJson]}"""
        File(d, "objective.json").writeText(objective, Charsets.UTF_8)
        File(d, "model.json").writeText(model, Charsets.UTF_8)
        val manifest = """
            {"spec_version": "1.21", "customer": "проба", "source_run": "run-0040",
             "revision": 3, "readiness": "в",
             "files": [
               {"name": "objective.json", "sha256": "${sha256(objective.toByteArray(Charsets.UTF_8))}"},
               {"name": "model.json", "sha256": "${sha256(model.toByteArray(Charsets.UTF_8))}"}],
             "allowed_modes": {"rostering": ["feasibility-hard", "feasibility-soft"]},
             "objectives": {"rostering": []},
             "logic_unproven": [],
             "has_data": true}
        """.trimIndent()
        File(d, "manifest.json").writeText(manifest, Charsets.UTF_8)
        return d
    }

    private fun param(name: String, value: Any, unit: String) =
        """{"name": "$name", "value": $value, "unit": "$unit", "relation": "max",
            "as_written": "$value"${
            if (unit == "time_of_day_minutes") ", \"tz\": \"base-local\"" else ""}}"""

    private fun softRule(id: String, logic: String, params: List<String>): String {
        // The compiler tests declare their own units/timezones. Copy them
        // losslessly into the current wire; do not infer or repair domain data.
        val mapper = ObjectMapper()
        val cells = params.joinToString(",") { raw ->
            val p = mapper.readTree(raw)
            """${p["name"]}:{"value":${p["value"]},"unit":${p["unit"]},
              "relation":${p["relation"]},"condition":null,"quote":${p["as_written"]},
              "source":{"kind":"rule","rule_id":"$id"}
              ${if (p.has("tz")) ",\"tz\":" + p["tz"].toString() else ""}}"""
        }
        return """{"id": "$id", "title": "$id", "statement": "$id", "zone": "BOTH", "kind": "SOFT",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit":{"schema_version":"landing/2","category":"A","family":"synthetic_logic_form",
              "registry_rev":"${"a".repeat(64)}","family_contract":{
                "family_id":"synthetic_logic_form","catalog_version":"synthetic-compiler-fixture",
                "catalog_fingerprint":"${"a".repeat(64)}","record_fingerprint":"${"b".repeat(64)}",
                "param_set":"default"},"params":{$cells}},
            "params": [${params.joinToString(",")}], "logic": $logic}"""
    }

    private fun compile(dir: File): LogicReport {
        val pkg = TaskPackage.read(dir)
        val thresholds = RunThresholds.resolve(pkg.hardParams, TaskModes.PROFILE_DEFAULT)
        return LogicCompiler.compile(pkg, thresholds, TaskModes.PROFILE_DEFAULT)
    }

    // ------------------------------------------------------------ порождение

    @Test
    fun `словарный шаблон порождает мягкий план с ролями`(@TempDir dir: Path) {
        val logic = """{"subject": "duty",
            "demand": {"kind": "штраф-ступенчатый-за-стыковку",
                       "threshold_param": "long_sit_threshold",
                       "step_param": "long_sit_step",
                       "as_written": "штраф ступенями за длинную стыковку"},
            "source_params": ["long_sit_threshold", "long_sit_step"]}"""
        val r = compile(writePackage(dir, softRule("SR-1", logic, listOf(
            param("long_sit_threshold", 60, "duration_minutes"),
            param("long_sit_step", 30, "duration_minutes")))))
        assertEquals(1, r.generated.size, r.refused.joinToString { it.reason ?: "" })
        val plan = r.generated[0].plan!!
        assertTrue(plan.soft)
        assertEquals(LogicForms.FORM_SIT_OVERAGE_STEPS, plan.form)
        assertEquals(60, plan.bound)
        assertEquals(30, plan.condBound)
        assertEquals(1, plan.weight)
        assertEquals("long_sit_threshold", plan.boundParam)
        assertTrue(r.generated[0].declarations.any { it.contains("МЯГКАЯ форма") })
    }

    @Test
    fun `окно ночи парой ссылок и пустое окно отказом`(@TempDir dir: Path) {
        fun logic(from: Int, to: Int) = """{"subject": "duty",
            "demand": {"kind": "однородность-цепочки-ночь-день",
                       "window_from_param": "night_duty_start",
                       "window_to_param": "night_duty_end",
                       "as_written": "однородность ночных и дневных"},
            "source_params": ["night_duty_start", "night_duty_end"]}"""
        fun pack(dir: Path, from: Int, to: Int) = writePackage(dir, softRule("SR-3", logic(from, to), listOf(
            param("night_duty_start", from, "time_of_day_minutes"),
            param("night_duty_end", to, "time_of_day_minutes"))))
        val ok = compile(pack(dir.resolve("a").apply { toFile().mkdirs() }, 1320, 360))
        assertEquals(1, ok.generated.size, ok.refused.joinToString { it.reason ?: "" })
        assertEquals(1320, ok.generated[0].plan!!.nightFrom)
        assertEquals(360, ok.generated[0].plan!!.nightTo)
        val bad = compile(pack(dir.resolve("b").apply { toFile().mkdirs() }, 360, 360))
        assertEquals(0, bad.generated.size)
        assertTrue(bad.refused[0].reason!!.contains("окно ночи пусто"))
    }

    @Test
    fun `шаблон без ролей порождается а лишняя роль отказ`(@TempDir dir: Path) {
        val clean = """{"subject": "employee",
            "demand": {"kind": "неудовлетворённая-выполнимая-заявка",
                       "as_written": "неудовлетворённая выполнимая заявка"},
            "source_params": []}"""
        val ok = compile(writePackage(dir.resolve("a").apply { toFile().mkdirs() },
            softRule("SR-5", clean, emptyList())))
        assertEquals(1, ok.generated.size, ok.refused.joinToString { it.reason ?: "" })
        assertEquals(LogicForms.FORM_UNMET_REQUEST, ok.generated[0].plan!!.form)
        val extra = """{"subject": "employee",
            "demand": {"kind": "неудовлетворённая-выполнимая-заявка",
                       "bound_param": "лишний",
                       "as_written": "с лишней ролью"},
            "source_params": []}"""
        val bad = compile(writePackage(dir.resolve("b").apply { toFile().mkdirs() },
            softRule("SR-5b", extra, listOf(param("лишний", 1, "count")))))
        assertEquals(0, bad.generated.size)
        assertTrue(bad.refused[0].reason!!.contains("требует роли"))
    }

    @Test
    fun `недостающая роль и чужая единица отказом`(@TempDir dir: Path) {
        val missing = """{"subject": "duty",
            "demand": {"kind": "штраф-ступенчатый-за-стыковку",
                       "threshold_param": "long_sit_threshold",
                       "as_written": "без шага"},
            "source_params": ["long_sit_threshold"]}"""
        val r1 = compile(writePackage(dir.resolve("a").apply { toFile().mkdirs() },
            softRule("SR-2", missing, listOf(param("long_sit_threshold", 60, "duration_minutes")))))
        assertEquals(0, r1.generated.size)
        assertTrue(r1.refused[0].reason!!.contains("требует роли"))
        val wrongUnit = """{"subject": "employee",
            "demand": {"kind": "мягкий-предел-дней-подряд",
                       "bound_param": "soft_consecutive_days",
                       "as_written": "дней подряд мягко"},
            "source_params": ["soft_consecutive_days"]}"""
        val r2 = compile(writePackage(dir.resolve("b").apply { toFile().mkdirs() },
            softRule("SR-4", wrongUnit, listOf(param("soft_consecutive_days", 600, "duration_minutes")))))
        assertEquals(0, r2.generated.size)
        assertTrue(r2.refused[0].reason!!.contains("ожидается счёт дней"))
    }

    @Test
    fun `мягкое вне словаря остаётся notHard прежним текстом`(@TempDir dir: Path) {
        val logic = """{"subject": "duty",
            "demand": {"kind": "duration_within", "relation": "max",
                       "param": "max_duty_duration", "as_written": "не более 12:00"},
            "source_params": ["max_duty_duration"]}"""
        val r = compile(writePackage(dir, softRule("SR-6", logic,
            listOf(param("max_duty_duration", 720, "duration_minutes")))))
        assertEquals(0, r.generated.size)
        assertEquals(1, r.notHard.size)
        assertTrue(r.notHard[0].reason!!.contains("исполнителем ступени 3 служат только жёсткие"))
    }
}
