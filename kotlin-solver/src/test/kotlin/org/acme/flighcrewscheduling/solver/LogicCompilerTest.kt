package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.nio.file.Path
import java.security.MessageDigest
import java.time.LocalDate

/**
 * Ступень 3, шаг 0: блок logic читается, судится гейтами и раскладывается
 * по пяти корзинам ДО первой секунды счёта. Порождённых ограничений ещё нет,
 * поэтому каждый кандидат на порождение честно отвергается (required) либо
 * ложится в «не исполняется» (post-check) — но никогда не молчит.
 *
 * Каждая отказная ветка проверена нарушающим случаем и контрольным.
 */
class LogicCompilerTest {

    @AfterEach
    fun cleanup() {
        TaskPackage.overrideForTests(null)
        LastRun.forget()
    }

    // ------------------------------------------------------- оснастка

    private fun sha256(b: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(b)
            .joinToString("") { x -> "%02x".format(x) }

    /** Historical parser fixtures explicitly use landing/1 compatibility.
     * Only the two named TaskModes tests request strict modern component wire.
     */
    private fun writePackage(dir: Path, rulesJson: String, unproven: String = "[]",
                             modernComponent: Boolean = false): File {
        val d = dir.toFile()
        val objective = """{"customer": "проба", "readiness": "в",
            "rostering": {"ready": true, "direction": "minimize", "objectives": [], "components": []}}"""
        val model = """{"meta": {"revision": 3}, "rules": [$rulesJson]}"""
        File(d, "objective.json").writeText(objective, Charsets.UTF_8)
        File(d, "model.json").writeText(model, Charsets.UTF_8)
        val manifest = """
            {"spec_version": "${if (modernComponent) "1.20" else "1.5"}",
             ${if (modernComponent) "" else "\"landing_compat\":1,"}
             "customer": "проба", "source_run": "run-0040",
             "revision": 3, "readiness": "в",
             "files": [
               {"name": "objective.json", "sha256": "${sha256(objective.toByteArray(Charsets.UTF_8))}"},
               {"name": "model.json", "sha256": "${sha256(model.toByteArray(Charsets.UTF_8))}"}],
             "allowed_modes": {"rostering": ["feasibility-hard", "feasibility-soft"]},
             "objectives": {"rostering": []},
             "logic_unproven": $unproven,
             "has_data": true}
        """.trimIndent()
        File(d, "manifest.json").writeText(manifest, Charsets.UTF_8)
        return d
    }

    private fun param(name: String, value: Any, relation: String, unit: String = "duration_minutes",
                      qualifier: String? = null, tz: String? = null) =
        """{"name": "$name", "value": $value, "unit": "$unit", "relation": "$relation",
            "as_written": "$value"${if (qualifier == null) "" else ", \"qualifier\": \"$qualifier\""}${
            if (tz == null) "" else ", \"tz\": \"$tz\""}}"""

    private fun rule(id: String, kind: String, params: List<String>, logic: String?) =
        """{"id": "$id", "title": "$id", "statement": "$id", "zone": "BOTH", "kind": "$kind",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "params": [${params.joinToString(",")}]${if (logic == null) "" else ", \"logic\": $logic"}}"""

    private fun compile(dir: File): LogicReport {
        // All compile-only callers above/below explicitly use historical wire.
        val pkg = readLegacyArchive(dir)
        val thresholds = RunThresholds.resolve(pkg.hardParams, TaskModes.PROFILE_DEFAULT)
        return LogicCompiler.compile(pkg, thresholds, TaskModes.PROFILE_DEFAULT)
    }

    private fun readLegacyArchive(dir: File): LoadedTaskPackage {
        val pkg = TaskPackage.read(dir, landingCompat = 1)
        assertEquals("1.5", pkg.specVersion)
        assertTrue(pkg.landingArchiveOnly)
        return pkg
    }

    /** Wire-shape component fixture, NOT a catalogue release or support claim.
     * These two mode tests preserve their original logic and numeric inputs.
     * The contract digest hashes this explicit synthetic declaration; it is
     * not presented as a real catalogue fingerprint or permission to launch.
     */
    private fun modernComponentRule(legacyRule: String): String {
        val mapper = ObjectMapper()
        val rule = mapper.readTree(legacyRule) as ObjectNode
        val family = "logic-component-test"
        val declaration = "LogicCompilerTest synthetic wire-only contract: explicit cells; no catalogue admission"
        val fingerprint = sha256(declaration.toByteArray(Charsets.UTF_8))
        val fit = rule.putObject("template_fit")
        fit.put("schema_version", "landing/2")
        fit.put("category", "A")
        fit.put("family", family)
        fit.put("registry_rev", fingerprint)
        fit.putObject("family_contract").also {
            it.put("family_id", family)
            it.put("catalog_version", "synthetic-component-only")
            it.put("catalog_fingerprint", fingerprint)
            it.put("record_fingerprint", fingerprint)
            it.put("param_set", "explicit-test-cells")
        }
        val cells = fit.putObject("params")
        for (param in rule.path("params")) {
            val name = param.path("name").asText()
            val cell = cells.putObject(name)
            cell.set<com.fasterxml.jackson.databind.JsonNode>("value", param.get("value"))
            cell.put("unit", param.path("unit").asText())
            cell.put("relation", param.path("relation").asText())
            cell.putNull("condition")
            cell.put("quote", param.path("as_written").asText())
            cell.putObject("source").put("kind", "rule").put("rule_id", rule.path("id").asText())
        }
        return mapper.writeValueAsString(rule)
    }

    private fun baskets(r: LogicReport): Int =
        r.generated.size + r.executedElsewhere.size + r.ignored.size + r.refused.size + r.notHard.size

    private val dutySpanLogic = """{"subject": "duty",
        "demand": {"kind": "duration_within", "relation": "max", "param": "max_duty_duration",
                   "as_written": "не более 12:00"},
        "source_params": ["max_duty_duration"]}"""

    // ------------------------------------------------ чтение и корзины

    @Test
    fun `пакет без logic — пустой отчёт, корзины пусты, прогон прежний`(@TempDir tmp: Path) {
        val d = writePackage(tmp, rule("R-1", "HARD", listOf(param("max_duty_duration", 720, "max")), null))
        val r = compile(d)
        assertTrue(r.isEmpty)
        assertEquals(0, r.blocksSeen)
        assertEquals(0, baskets(r))
    }

    @Test
    fun `размах смены с ручкой — исполняется существующим, ручка и прочтение названы`(@TempDir tmp: Path) {
        val d = writePackage(tmp, rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max")), dutySpanLogic))
        val r = compile(d)
        assertEquals(1, r.blocksSeen)
        assertEquals(1, r.executedElsewhere.size)
        val e = r.executedElsewhere[0]
        assertEquals(listOf("Max duty period (rest-segmented)"), e.constraintIds)
        assertEquals(listOf("max_duty_span_min"), e.viaThresholds)
        assertTrue(e.declarations.any { it.startsWith("ПРОЧТЕНИЕ: мера не названа") }, e.declarations.toString())
        assertTrue(e.declarations.any { it.startsWith("УМОЛЧАНИЕ: enforce") }, e.declarations.toString())
        assertEquals(baskets(r), r.blocksSeen)
    }

    @Test
    fun `налёт смены (мера block) ручкой размаха не исполняется — отказ, не подмена`(@TempDir tmp: Path) {
        val logic = dutySpanLogic.replace("\"as_written\": \"не более 12:00\"",
            "\"measure\": \"block\", \"as_written\": \"налёт не более 12:00\"")
        val d = writePackage(tmp, rule("R-10", "HARD", listOf(param("max_duty_duration", 720, "max")), logic))
        val r = compile(d)
        assertEquals(1, r.refused.size)
        assertTrue(r.refused[0].reason!!.contains("не это требование"), r.refused[0].reason)
        assertTrue(r.refused[0].reason!!.contains("мера block"), r.refused[0].reason)
        assertThrows<TaskModeRefused> { LogicCompiler.refuseIfNeeded(r) }
    }

    @Test
    fun `посадки за сутки под ручкой «за смену» — отказ (ручка исполняет не то)`(@TempDir tmp: Path) {
        val logic = """{"subject": "day",
            "demand": {"kind": "count_within", "relation": "max", "param": "max_landings_per_duty",
                       "as_written": "не более 6 за сутки"},
            "source_params": ["max_landings_per_duty"]}"""
        val d = writePackage(tmp, rule("R-11", "HARD",
            listOf(param("max_landings_per_duty", 6, "max", unit = "count")), logic))
        val r = compile(d)
        assertEquals(1, r.refused.size)
        assertTrue(r.refused[0].reason!!.contains("Max landings per duty"), r.refused[0].reason)
        assertTrue(r.refused[0].reason!!.contains("двойной счёт"), r.refused[0].reason)
    }

    @Test
    fun `пересечение рейсов без параметров — исполняется всегда включённым`(@TempDir tmp: Path) {
        val logic = """{"subject": "assignment",
            "condition": [{"predicate": "overlaps_in_time", "as_written": "одновременно"}],
            "demand": {"kind": "not_exists", "subject": "assignment", "as_written": "не допускается"},
            "source_params": []}"""
        val d = writePackage(tmp, rule("R-39", "HARD", emptyList(), logic))
        val r = compile(d)
        assertEquals(listOf("Flight conflict"), r.executedElsewhere.single().constraintIds)
        assertTrue(r.executedElsewhere[0].viaThresholds.isEmpty())
    }

    @Test
    fun `свежесть допуска — два исполнителя названы, не порождается`(@TempDir tmp: Path) {
        val logic = """{"subject": "assignment",
            "condition": [{"predicate": "has_qualification_for", "type_value": "Полярис-Р200",
                           "as_written": "действующий допуск"}],
            "demand": {"kind": "combination_forbidden", "subject": "qualification",
                       "as_written": "без действующего допуска нельзя"},
            "source_params": []}"""
        val d = writePackage(tmp, rule("R-52", "HARD", emptyList(), logic))
        val r = compile(d)
        assertEquals(listOf("Type rating required", "Type rating currency"),
            r.executedElsewhere.single().constraintIds)
        assertTrue(r.generated.isEmpty())
    }

    @Test
    fun `правило не HARD с logic — корзина «вид правила», не молчание`(@TempDir tmp: Path) {
        val d = writePackage(tmp, rule("R-D", "DATA", listOf(param("max_duty_duration", 750, "max")), dutySpanLogic))
        val r = compile(d)
        assertEquals(1, r.notHard.size)
        assertTrue(r.notHard[0].reason!!.contains("DATA"))
        assertEquals(baskets(r), r.blocksSeen)
    }

    // ------------------------------------------------- гейт 1: словарь

    @Test
    fun `предмет вне словаря — отказ при required, «не исполняется» при post-check`(@TempDir tmp: Path) {
        val bad = dutySpanLogic.replace("\"subject\": \"duty\"", "\"subject\": \"pairing\"")
        val r = compile(writePackage(tmp, rule("R-21", "HARD", listOf(param("max_duty_duration", 720, "max")), bad)))
        assertEquals(1, r.refused.size)
        assertTrue(r.refused[0].reason!!.contains("pairing"), r.refused[0].reason)
        val soft = bad.replace("\"source_params\"", "\"enforce\": \"post-check\", \"source_params\"")
        val r2 = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-21", "HARD", listOf(param("max_duty_duration", 720, "max")), soft)))
        assertEquals(1, r2.ignored.size)
        assertEquals(LogicCompiler.ST_IGNORED, r2.ignored[0].state)
        assertTrue(r2.ignored[0].declarations.none { it.startsWith("УМОЛЧАНИЕ") })
    }

    @Test
    fun `ссылка на имя с двумя записями — отказ с перечнем уточнителей`(@TempDir tmp: Path) {
        val d = writePackage(tmp, rule("R-S3", "HARD",
            listOf(param("max_duty_duration", 690, "max", qualifier = "без согласия"),
                   param("max_duty_duration", 780, "max", qualifier = "с согласия")), dutySpanLogic))
        val r = compile(d)
        assertEquals(1, r.refused.size)
        val why = r.refused[0].reason!!
        assertTrue(why.contains("2 записи"), why)
        assertTrue(why.contains("без согласия") && why.contains("с согласия"), why)
        assertTrue(why.contains("1.6"), why)
    }

    @Test
    fun `source_params расходится со ссылками атомов — отказ в обе стороны`(@TempDir tmp: Path) {
        val missing = dutySpanLogic.replace("\"source_params\": [\"max_duty_duration\"]", "\"source_params\": []")
        val r = compile(writePackage(tmp, rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max")), missing)))
        assertTrue(r.refused.single().reason!!.contains("source_params"), r.refused[0].reason)
        val extra = dutySpanLogic.replace("\"source_params\": [\"max_duty_duration\"]",
            "\"source_params\": [\"max_duty_duration\", \"crew_size\"]")
        val r2 = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max"),
                param("crew_size", 2, "eq", unit = "count")), extra)))
        assertTrue(r2.refused.single().reason!!.contains("source_params"), r2.refused[0].reason)
    }

    @Test
    fun `отношение window у требования — отказ (это отношение параметра-окна)`(@TempDir tmp: Path) {
        // Живой случай Лайта: weekly_rest_period 10080 relation=window — это
        // отношение ПАРАМЕТРА-окна; у требования такого отношения нет.
        val bad = """{"subject": "rest",
            "demand": {"kind": "duration_within", "relation": "window", "param": "weekly_rest_period",
                       "as_written": "36 часов в 7 сутках"},
            "source_params": ["weekly_rest_period"]}"""
        val r = compile(writePackage(tmp, rule("R-33", "HARD", listOf(param("weekly_rest_period", 10080, "window")), bad)))
        assertTrue(r.refused.single().reason!!.contains("window"), r.refused[0].reason)
        assertTrue(r.refused[0].reason!!.contains("отношение параметра-окна"), r.refused[0].reason)
    }

    @Test
    fun `число в as_written без параметра — «число ниоткуда»`(@TempDir tmp: Path) {
        val logic = """{"subject": "day_off",
            "demand": {"kind": "exists_universe", "subject": "day_off", "universe": "employee×month",
                       "as_written": "не менее 8"},
            "source_params": []}"""
        val r = compile(writePackage(tmp, rule("R-34", "HARD", emptyList(), logic)))
        assertTrue(r.refused.single().reason!!.contains("число ниоткуда"), r.refused[0].reason)
    }

    @Test
    fun `окно строкой — порча пакета, а не пустая длина окна`(@TempDir tmp: Path) {
        val logic = """{"subject": "rest", "window": "7 суток",
            "demand": {"kind": "exists_universe", "subject": "rest", "universe": "employee×rolling_window",
                       "as_written": "есть"},
            "source_params": []}"""
        val e = assertThrows<TaskPackageError> {
            readLegacyArchive(writePackage(tmp, rule("R-33", "HARD", emptyList(), logic)))
        }
        assertTrue(e.message!!.contains("не объект"), e.message)
    }

    @Test
    fun `область вне словаря областей — отказ, не применение к целому`(@TempDir tmp: Path) {
        val bad = dutySpanLogic.replace("\"subject\": \"duty\"", "\"subject\": \"duty\", \"scope\": \"только летом\"")
        val r = compile(writePackage(tmp, rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max")), bad)))
        assertTrue(r.refused.single().reason!!.contains("область"), r.refused[0].reason)
        val ok = dutySpanLogic.replace("\"subject\": \"duty\"", "\"subject\": \"duty\", \"scope\": \"PLY\"")
        val r2 = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max")), ok)))
        assertEquals(1, r2.executedElsewhere.size)
    }

    // --------------------------------------- кандидаты на порождение

    private val daysOffLogic = """{"subject": "day_off",
        "demand": {"kind": "count_within", "relation": "min", "param": "min_days_off",
                   "universe": "employee×month", "as_written": "не менее 8"},
        "window": {"kind": "month"},
        "source_params": ["min_days_off"]}"""

    @Test
    fun `выходных не менее 8 в месяц — порождается с планом, весом и прочтением`(@TempDir tmp: Path) {
        val r = compile(writePackage(tmp, rule("R-34", "HARD",
            listOf(param("min_days_off", 8, "min", unit = "count")), daysOffLogic)))
        assertEquals(1, r.generated.size)
        val g = r.generated[0]
        assertEquals(LogicCompiler.ST_GENERATED, g.state)
        val plan = g.plan!!
        assertEquals(LogicForms.FORM_DAYS_OFF_PER_MONTH_MIN, plan.form)
        assertEquals(8, plan.bound)
        assertEquals("min_days_off", plan.boundParam)
        assertEquals(listOf(plan.constraintId), g.constraintIds)
        assertTrue(LogicDictionaries.CONSTRAINT_ID.matches(plan.constraintId), plan.constraintId)
        assertTrue(g.declarations.any { it.startsWith("ПРОЧТЕНИЕ: выходной") }, g.declarations.toString())
        assertTrue(g.declarations.any { it.startsWith("ВЕС: 1") }, g.declarations.toString())
        assertEquals(listOf(plan), r.plans)
        assertEquals(baskets(r), r.blocksSeen)
    }

    @Test
    fun `форма вне первой волны — отказ при required, ignored при post-check`(@TempDir tmp: Path) {
        // Правило 35 («выходной после двух ночных подряд», exists_person) —
        // вторая волна: формы нет, отказ честный.
        val logic = """{"subject": "day_off",
            "demand": {"kind": "exists_person", "subject": "day_off", "as_written": "предоставляется"},
            "source_params": []}"""
        val r = compile(writePackage(tmp, rule("R-35", "HARD", emptyList(), logic)))
        assertEquals(1, r.refused.size)
        assertTrue(r.refused[0].reason!!.contains("не порождается"), r.refused[0].reason)
        val post = logic.replace("\"source_params\"", "\"enforce\": \"post-check\", \"source_params\"")
        val r2 = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-35", "HARD", emptyList(), post)))
        assertEquals(1, r2.ignored.size)
        assertTrue(r2.refused.isEmpty())
    }

    @Test
    fun `месяц атомом in_window — та же форма выходных, чужое окно в атоме — отказ`(@TempDir tmp: Path) {
        // Живой полный пакет 2026-08-24: «в календарном месяце» — атомом.
        val withAtom = daysOffLogic.replace(""""demand"""",
            """"condition": [{"predicate": "in_window", "window": {"kind": "month"},
                             "as_written": "в календарном месяце"}], "demand"""")
        val r = compile(writePackage(tmp, rule("R-34", "HARD",
            listOf(param("min_days_off", 8, "min", unit = "count")), withAtom)))
        assertEquals(1, r.generated.size)
        assertEquals(LogicForms.FORM_DAYS_OFF_PER_MONTH_MIN, r.generated[0].plan!!.form)
        // Атом с месяцем и правило без атома — одна форма: сводятся.
        val both = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-34", "HARD", listOf(param("min_days_off", 8, "min", unit = "count")), withAtom) + "," +
            rule("R-61", "HARD", listOf(param("min_days_off", 8, "min", unit = "count")), daysOffLogic)))
        assertEquals(1, both.plans.size)
        assertEquals(listOf("R-34", "R-61"), both.plans[0].ruleIds)
        // Скользящее окно в атоме при месячном универсуме — не эта форма.
        val alien = daysOffLogic.replace(""""demand"""",
            """"condition": [{"predicate": "in_window",
                             "window": {"kind": "rolling", "unit": "day", "param": "min_days_off"},
                             "as_written": "в окне"}], "demand"""")
        val r3 = compile(writePackage(tmp.resolve("c").also { it.toFile().mkdirs() },
            rule("R-34", "HARD", listOf(param("min_days_off", 8, "min", unit = "count")), alien)))
        assertTrue(r3.refused.single().reason!!.contains("не порождается"), r3.refused[0].reason)
    }

    @Test
    fun `два правила одной формы — один исполнитель, границы сведены строжайшей`(@TempDir tmp: Path) {
        val other = daysOffLogic.replace("не менее 8", "не менее 9")
        val r = compile(writePackage(tmp,
            rule("R-34", "HARD", listOf(param("min_days_off", 8, "min", unit = "count")), daysOffLogic) + "," +
            rule("R-61", "HARD", listOf(param("min_days_off", 9, "min", unit = "count")), other)))
        assertEquals(2, r.generated.size)
        assertEquals(1, r.plans.size)
        val plan = r.plans[0]
        assertEquals(9, plan.bound)
        assertEquals(listOf("R-34", "R-61"), plan.ruleIds)
        assertTrue(r.generated.all { it.constraintIds == listOf(plan.constraintId) })
        assertTrue(r.generated[0].declarations.any { it.startsWith("СВЕДЕНО") && it.contains("строжайшей 9") },
            r.generated[0].declarations.toString())
    }

    @Test
    fun `область формы — только код базы, слова областей ручек отвергаются`(@TempDir tmp: Path) {
        val bad = daysOffLogic.replace("\"subject\": \"day_off\"", "\"subject\": \"day_off\", \"scope\": \"вне базы\"")
        val r = compile(writePackage(tmp, rule("R-34", "HARD",
            listOf(param("min_days_off", 8, "min", unit = "count")), bad)))
        assertTrue(r.refused.single().reason!!.contains("код базы"), r.refused[0].reason)
        val ok = daysOffLogic.replace("\"subject\": \"day_off\"", "\"subject\": \"day_off\", \"scope\": \"PLY\"")
        val r2 = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-34", "HARD", listOf(param("min_days_off", 8, "min", unit = "count")), ok)))
        assertEquals("PLY", r2.plans.single().baseScope)
    }

    @Test
    fun `fit порождённого — горизонт без полного месяца это ПУСТО`(@TempDir tmp: Path) {
        val r = compile(writePackage(tmp, rule("R-34", "HARD",
            listOf(param("min_days_off", 8, "min", unit = "count")), daysOffLogic)))
        val home = airport("HOME"); val out = airport("OUT")
        val e1 = employee("E1").apply { homeAirport = home }
        val short = Fixtures.schedule(listOf(home, out), listOf(e1),
            listOf(assignment("s1", flight("F1", home, t(2, 10), out, t(2, 12)), e1)))
        val fitShort = LogicCompiler.fit(short, emptyMap(), r, "нано")
        assertTrue(fitShort.generated.isEmpty())
        assertEquals(1, fitShort.refused.size)
        assertTrue(fitShort.refused[0].reason!!.startsWith("ПУСТО"), fitShort.refused[0].reason)
        assertTrue(fitShort.plans.isEmpty())
        // Полный месяц: клеток = сотрудников × 1, план в силе.
        val full = Fixtures.schedule(listOf(home, out), listOf(e1, employee("E2")),
            listOf(assignment("a", flight("F1", home, t(1, 10), out, t(1, 12)), e1),
                   assignment("b", flight("F2", home, t(31, 10), out, t(31, 12)), e1)))
        val fitFull = LogicCompiler.fit(full, emptyMap(), r, "месяц")
        assertEquals(1, fitFull.generated.size)
        assertEquals(2, fitFull.generated[0].considered)
        assertEquals(1, fitFull.plans.size)
    }

    @Test
    fun `ссылка-объект 1_6 — запись выбирается уточнителем, чужой уточнитель отказ`(@TempDir tmp: Path) {
        val logic = daysOffLogic
            .replace("\"param\": \"min_days_off\"",
                "\"param\": {\"name\": \"min_days_off\", \"qualifier\": \"лето\"}")
            .replace("\"source_params\": [\"min_days_off\"]",
                "\"source_params\": [{\"name\": \"min_days_off\", \"qualifier\": \"лето\"}]")
        val two = listOf(param("min_days_off", 8, "min", unit = "count", qualifier = "лето"),
                         param("min_days_off", 10, "min", unit = "count", qualifier = "зима"))
        val r = compile(writePackage(tmp, rule("R-34", "HARD", two, logic)))
        assertEquals(1, r.generated.size)
        assertEquals(8, r.generated[0].plan!!.bound)
        // Тот же блок со ссылкой БЕЗ уточнителя — отказ: записей две.
        val bare = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-34", "HARD", two, daysOffLogic)))
        assertTrue(bare.refused.single().reason!!.contains("2 записи"), bare.refused[0].reason)
        // Уточнитель, которого нет, — отказ с перечнем существующих.
        val wrong = logic.replace("\"qualifier\": \"лето\"", "\"qualifier\": \"осень\"")
        val r3 = compile(writePackage(tmp.resolve("c").also { it.toFile().mkdirs() },
            rule("R-34", "HARD", two, wrong)))
        assertTrue(r3.refused.single().reason!!.contains("записи с таким уточнителем нет"), r3.refused[0].reason)
    }

    @Test
    fun `отдых после продлённой смены — порождается с обеими границами и пересечением`(@TempDir tmp: Path) {
        val logic = """{"subject": "rest",
            "condition": [{"predicate": "follows", "subject": "duty", "as_written": "после смены"},
                          {"predicate": "longer_than", "param": "long_duty_threshold",
                           "as_written": "свыше 12:00"}],
            "demand": {"kind": "duration_within", "relation": "min", "param": "min_rest_after_long",
                       "as_written": "не менее 14:00"},
            "source_params": ["long_duty_threshold", "min_rest_after_long"]}"""
        val r = compile(writePackage(tmp, rule("R-20", "HARD",
            listOf(param("long_duty_threshold", 720, "max"),
                   param("min_rest_after_long", 840, "min")), logic)))
        assertEquals(1, r.generated.size)
        val plan = r.generated[0].plan!!
        assertEquals(LogicForms.FORM_REST_AFTER_LONG_DUTY_MIN, plan.form)
        assertEquals(840, plan.bound)
        assertEquals(720, plan.condBound)
        assertEquals("long_duty_threshold", plan.condParam)
        assertTrue(r.generated[0].declarations.any { it.contains("Rest between duty periods") },
            r.generated[0].declarations.toString())
        assertTrue(r.generated[0].declarations.any { it.contains("нарезка по порогам отдыха") },
            r.generated[0].declarations.toString())
    }

    @Test
    fun `окно 33 — порождается, минуты окна сводятся к суткам только нацело`(@TempDir tmp: Path) {
        val logic = """{"subject": "rest",
            "condition": [{"predicate": "longer_than", "param": "weekly_rest_required",
                           "as_written": "не менее 36 часов"}],
            "demand": {"kind": "exists_universe", "subject": "rest",
                       "universe": "employee×rolling_window", "as_written": "в каждом окне"},
            "window": {"kind": "rolling", "unit": "day", "param": "weekly_rest_period"},
            "source_params": ["weekly_rest_required", "weekly_rest_period"]}"""
        // Живой случай Лайта: weekly_rest_period = 10080 МИНУТ (7 суток).
        val r = compile(writePackage(tmp, rule("R-33", "HARD",
            listOf(param("weekly_rest_required", 2160, "min"),
                   param("weekly_rest_period", 10080, "window")), logic)))
        assertEquals(1, r.generated.size)
        val plan = r.generated[0].plan!!
        assertEquals(LogicForms.FORM_REST_IN_ROLLING_WINDOW, plan.form)
        assertEquals(2160, plan.bound)
        assertEquals(7, plan.condBound)
        assertTrue(r.generated[0].declarations.any { it.contains("прочитана как 7 суток") },
            r.generated[0].declarations.toString())
        // Число в as_written требования при ЖИВЫХ ссылках блока — не «число ниоткуда».
        val digits = logic.replace("в каждом окне", "не менее 36 часов в окне 7 суток")
        assertEquals(1, compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-33", "HARD", listOf(param("weekly_rest_required", 2160, "min"),
                param("weekly_rest_period", 10080, "window")), digits))).generated.size)
        // Минуты, не кратные суткам, — отказ, не округление.
        val odd = compile(writePackage(tmp.resolve("c").also { it.toFile().mkdirs() },
            rule("R-33", "HARD", listOf(param("weekly_rest_required", 2160, "min"),
                param("weekly_rest_period", 10000, "window")), logic)))
        assertTrue(odd.refused.single().reason!!.contains("кратные 1440"), odd.refused[0].reason)
        // Сутки счётом — тоже законны.
        val byDays = compile(writePackage(tmp.resolve("d").also { it.toFile().mkdirs() },
            rule("R-33", "HARD", listOf(param("weekly_rest_required", 2160, "min"),
                param("weekly_rest_period", 7, "window", unit = "count")), logic)))
        assertEquals(7, byDays.generated.single().plan!!.condBound)
    }

    @Test
    fun `ночные подряд — порождается с окном ночи, чужие единицы окна отказ`(@TempDir tmp: Path) {
        val logic = """{"subject": "duty",
            "condition": [{"predicate": "starts_in", "from_param": "night_duty_start",
                           "to_param": "night_duty_end", "as_written": "с 22:00 до 06:00"},
                          {"predicate": "consecutive", "unit": "duty",
                           "param": "max_consecutive_night_duties", "as_written": "подряд"}],
            "demand": {"kind": "count_within", "relation": "max",
                       "param": "max_consecutive_night_duties", "as_written": "не более двух"},
            "source_params": ["night_duty_start", "night_duty_end", "max_consecutive_night_duties"]}"""
        val params = listOf(
            param("night_duty_start", 1320, "min", unit = "time_of_day_minutes", tz = "base-local"),
            param("night_duty_end", 360, "max", unit = "time_of_day_minutes", tz = "base-local"),
            param("max_consecutive_night_duties", 2, "max", unit = "count"))
        val r = compile(writePackage(tmp, rule("R-14", "HARD", params, logic)))
        assertEquals(1, r.generated.size)
        val plan = r.generated[0].plan!!
        assertEquals(LogicForms.FORM_CONSECUTIVE_NIGHT_DUTIES_MAX, plan.form)
        assertEquals(2, plan.bound)
        assertEquals(1320, plan.nightFrom)
        assertEquals(360, plan.nightTo)
        assertTrue(r.generated[0].declarations.any { it.startsWith("ОКНО НОЧИ") },
            r.generated[0].declarations.toString())
        // Граница окна не временем суток — отказ.
        val bad = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-14", "HARD", listOf(
                param("night_duty_start", 1320, "min"),
                param("night_duty_end", 360, "max", unit = "time_of_day_minutes", tz = "base-local"),
                param("max_consecutive_night_duties", 2, "max", unit = "count")), logic)))
        assertTrue(bad.refused.single().reason!!.contains("time_of_day_minutes"), bad.refused[0].reason)
        // A9: признак пояса окна читается и судится — без него и с
        // «acclimatized» (исполнителя нет) отказ словами, не молчаливый
        // счёт в шкале меток.
        val noTz = compile(writePackage(tmp.resolve("c").also { it.toFile().mkdirs() },
            rule("R-14", "HARD", listOf(
                param("night_duty_start", 1320, "min", unit = "time_of_day_minutes"),
                param("night_duty_end", 360, "max", unit = "time_of_day_minutes", tz = "base-local"),
                param("max_consecutive_night_duties", 2, "max", unit = "count")), logic)))
        assertTrue(noTz.refused.single().reason!!.contains("без признака пояса"), noTz.refused[0].reason)
        val accl = compile(writePackage(tmp.resolve("d").also { it.toFile().mkdirs() },
            rule("R-14", "HARD", listOf(
                param("night_duty_start", 1320, "min", unit = "time_of_day_minutes", tz = "acclimatized"),
                param("night_duty_end", 360, "max", unit = "time_of_day_minutes", tz = "base-local"),
                param("max_consecutive_night_duties", 2, "max", unit = "count")), logic)))
        assertTrue(accl.refused.single().reason!!.contains("acclimatized"), accl.refused[0].reason)
    }

    @Test
    fun `недоказанное у производителя при required — отказ с причиной производителя`(@TempDir tmp: Path) {
        val logic = """{"subject": "day_off",
            "demand": {"kind": "count_within", "relation": "min", "param": "min_days_off",
                       "universe": "employee×month", "as_written": "не менее 8"},
            "window": {"kind": "month"},
            "source_params": ["min_days_off"]}"""
        val r = compile(writePackage(tmp, rule("R-34", "HARD",
            listOf(param("min_days_off", 8, "min", unit = "count")), logic),
            unproven = """[{"rule": "R-34", "reason": "пара первой волной не строится"}]"""))
        assertTrue(r.refused.single().reason!!.contains("контрпримерную пару"), r.refused[0].reason)
        assertTrue(r.refused[0].reason!!.contains("пара первой волной не строится"), r.refused[0].reason)
    }

    @Test
    fun `длительность над сменой без ручки и без меры — отказ «мера не названа»`(@TempDir tmp: Path) {
        val logic = """{"subject": "duty",
            "demand": {"kind": "duration_within", "relation": "max", "param": "max_block_per_duty",
                       "as_written": "не более 8:00"},
            "source_params": ["max_block_per_duty"]}"""
        val r = compile(writePackage(tmp, rule("R-10", "HARD",
            listOf(param("max_block_per_duty", 480, "max")), logic)))
        assertTrue(r.refused.single().reason!!.contains("без меры"), r.refused[0].reason)
    }

    @Test
    fun `дни подряд — порождается, пересечение с мягким счётчиком недели заявлено`(@TempDir tmp: Path) {
        val logic = """{"subject": "day",
            "condition": [{"predicate": "consecutive", "unit": "day", "param": "max_consecutive",
                           "as_written": "подряд"}],
            "demand": {"kind": "count_within", "relation": "max", "param": "max_consecutive",
                       "as_written": "не более 5"},
            "source_params": ["max_consecutive"]}"""
        val r = compile(writePackage(tmp, rule("R-23", "HARD",
            listOf(param("max_consecutive", 5, "max", unit = "count")), logic)))
        assertEquals(1, r.generated.size)
        val g = r.generated[0]
        assertEquals(LogicForms.FORM_CONSECUTIVE_DAYS_MAX, g.plan!!.form)
        assertEquals(5, g.plan!!.bound)
        assertTrue(g.declarations.any { it.contains("Too many duty days per week") }, g.declarations.toString())
        // Серии по СМЕНАМ — вне первой волны: нужна нарезка смен, не сутки.
        val byDuty = logic.replace("\"unit\": \"day\"", "\"unit\": \"duty\"")
        val r2 = compile(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-23", "HARD", listOf(param("max_consecutive", 5, "max", unit = "count")), byDuty)))
        assertEquals(1, r2.refused.size)
        assertTrue(r2.refused[0].reason!!.contains("не порождается"), r2.refused[0].reason)
    }

    // ------------------------------------------------- отпечаток формы

    @Test
    fun `отпечаток формы стабилен и меняется с enforce и мерой`(@TempDir tmp: Path) {
        val a = readLegacyArchive(writePackage(tmp, rule("R-7", "HARD",
            listOf(param("max_duty_duration", 720, "max")), dutySpanLogic))).logic.single()
        val b = readLegacyArchive(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max")), dutySpanLogic))).logic.single()
        assertEquals(LogicCompiler.shapeOf(a), LogicCompiler.shapeOf(b))
        val c = readLegacyArchive(writePackage(tmp.resolve("c").also { it.toFile().mkdirs() },
            rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max")),
                dutySpanLogic.replace("\"source_params\"", "\"enforce\": \"post-check\", \"source_params\"")))).logic.single()
        assertNotEquals(LogicCompiler.shapeOf(a), LogicCompiler.shapeOf(c))
    }

    // ------------------------------------------------------------ fit

    @Test
    fun `недоступность отнесена, но в наборе нет ни одного отмеченного дня — ПУСТО`(@TempDir tmp: Path) {
        val logic = """{"subject": "assignment",
            "condition": [{"predicate": "marked_unavailable", "as_written": "в отпуске"}],
            "demand": {"kind": "not_exists", "subject": "assignment", "as_written": "не назначать"},
            "source_params": []}"""
        val r = compile(writePackage(tmp, rule("R-36", "HARD", emptyList(), logic)))
        assertEquals(listOf("Employee unavailable"), r.executedElsewhere.single().constraintIds)
        val home = airport("HOME"); val out = airport("OUT")
        val e1 = employee("E1"); val e2 = employee("E2")
        val f = flight("F1", home, t(2, 10), out, t(2, 12))
        val empty = Fixtures.schedule(listOf(home, out), listOf(e1, e2), listOf(assignment("s1", f, e1)))
        val fitEmpty = LogicCompiler.fit(empty, emptyMap(), r, "проба")
        assertEquals(1, fitEmpty.refused.size)
        assertTrue(fitEmpty.refused[0].reason!!.startsWith("ПУСТО"), fitEmpty.refused[0].reason)
        assertEquals(0, fitEmpty.refused[0].considered)
        assertEquals("проба", fitEmpty.fitOn)
        // Контроль: один отмеченный день — исполнитель рассматривает предмет.
        e2.unavailableDays = mutableListOf(LocalDate.of(2026, 3, 2))
        val fitOk = LogicCompiler.fit(empty, emptyMap(), r, "проба")
        assertEquals(1, fitOk.executedElsewhere.size)
        assertEquals(1, fitOk.executedElsewhere[0].considered)
        assertTrue(fitOk.refused.isEmpty())
    }

    @Test
    fun `ПУСТО при post-check — не исполняется, а прогон идёт`(@TempDir tmp: Path) {
        val logic = """{"subject": "assignment", "enforce": "post-check",
            "condition": [{"predicate": "marked_unavailable", "as_written": "в отпуске"}],
            "demand": {"kind": "not_exists", "subject": "assignment", "as_written": "не назначать"},
            "source_params": []}"""
        val r = compile(writePackage(tmp, rule("R-36", "HARD", emptyList(), logic)))
        val home = airport("HOME"); val out = airport("OUT")
        val e1 = employee("E1")
        val f = flight("F1", home, t(2, 10), out, t(2, 12))
        val sch = Fixtures.schedule(listOf(home, out), listOf(e1), listOf(assignment("s1", f, e1)))
        val fit = LogicCompiler.fit(sch, emptyMap(), r, "проба")
        assertTrue(fit.refused.isEmpty())
        assertEquals(1, fit.ignored.size)
        assertEquals(0, fit.ignored[0].considered)
    }

    // ----------------------------------------------- сведение режима

    @Test
    fun `TaskModes сводит пакет с logic — отказ называет правило и причину`(@TempDir tmp: Path) {
        val bad = dutySpanLogic.replace("\"subject\": \"duty\"", "\"subject\": \"pairing\"")
        val pkg = TaskPackage.read(writePackage(tmp, modernComponentRule(rule("R-21", "HARD",
            listOf(param("max_duty_duration", 720, "max")), bad)), modernComponent = true))
        assertTrue(!pkg.landingArchiveOnly)
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("feasibility-soft", pkg, "rostering")
        }
        assertTrue(e.message!!.contains("R-21"), e.message)
        assertTrue(e.message!!.contains("pairing"), e.message)
        // Контроль: годный блок проходит, и корзина видна в снимке прогона.
        val ok = TaskPackage.read(writePackage(tmp.resolve("b").also { it.toFile().mkdirs() },
            modernComponentRule(rule("R-7", "HARD", listOf(param("max_duty_duration", 720, "max")), dutySpanLogic)),
            modernComponent = true))
        assertTrue(!ok.landingArchiveOnly)
        val m = TaskModes.resolve("feasibility-soft", ok, "rostering")
        assertEquals(1, m.logic!!.executedElsewhere.size)
        LastRun.remember(m)
        @Suppress("UNCHECKED_CAST")
        val snap = LastRun.snapshot()!!["logic"] as Map<String, Any?>
        assertEquals(1, snap["blocksSeen"])
        assertEquals(LogicCompiler.ST_ELSEWHERE, (snap["states"] as Map<*, *>)["executedElsewhere"])
    }

    @Test
    fun `профиль GERAD с logic пакета — отказ, как и с порогами`(@TempDir tmp: Path) {
        val logic = """{"subject": "assignment",
            "condition": [{"predicate": "overlaps_in_time", "as_written": "одновременно"}],
            "demand": {"kind": "not_exists", "subject": "assignment", "as_written": "нет"},
            "source_params": []}"""
        val pkg = TaskPackage.read(writePackage(tmp, modernComponentRule(rule("R-39", "HARD", emptyList(), logic)),
            modernComponent = true))
        assertTrue(!pkg.landingArchiveOnly)
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("feasibility-soft", pkg, "rostering", rulesProfile = "GERAD")
        }
        assertTrue(e.message!!.contains("logic"), e.message)
    }

    @Test
    fun `счётчики окон — выходных в окне не менее K и ночных в окне не более K порождаются`(@TempDir tmp: Path) {
        val offLogic = """{"subject": "day_off",
            "demand": {"kind": "count_within", "relation": "min", "param": "min_days_off_window",
                       "universe": "employee×rolling_window", "as_written": "не менее 2 выходных в окне"},
            "window": {"kind": "rolling", "unit": "day", "param": "window_days"},
            "source_params": ["min_days_off_window", "window_days"]}"""
        val r = compile(writePackage(tmp, rule("R-OFF7", "HARD",
            listOf(param("min_days_off_window", 2, "count"),
                   param("window_days", 10080, "window")), offLogic)))
        assertEquals(1, r.generated.size, r.refused.toString())
        val plan = r.generated[0].plan!!
        assertEquals(LogicForms.FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN, plan.form)
        assertEquals(2, plan.bound)
        assertEquals(7, plan.condBound)
        assertEquals(LogicForms.READING_DAYS_OFF_ROLLING, plan.reading)
        val nightLogic = """{"subject": "duty",
            "condition": [{"predicate": "starts_in", "from_param": "night_duty_start",
                           "to_param": "night_duty_end", "as_written": "ночная смена"}],
            "demand": {"kind": "count_within", "relation": "max", "param": "max_nights_window",
                       "universe": "employee×rolling_window", "as_written": "не более 2 ночных в окне"},
            "window": {"kind": "rolling", "unit": "day", "param": "window_days"},
            "source_params": ["max_nights_window", "window_days", "night_duty_start", "night_duty_end"]}"""
        val r2 = compile(writePackage(tmp.resolve("n").also { it.toFile().mkdirs() },
            rule("R-N3", "HARD",
                listOf(param("max_nights_window", 2, "count"),
                       param("window_days", 3, "window", unit = "count"),
                       param("night_duty_start", 1320, "min", unit = "time_of_day_minutes", tz = "base-local"),
                       param("night_duty_end", 360, "max", unit = "time_of_day_minutes", tz = "base-local")),
                nightLogic)))
        assertEquals(1, r2.generated.size, r2.refused.toString())
        val p2 = r2.generated[0].plan!!
        assertEquals(LogicForms.FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX, p2.form)
        assertEquals(2, p2.bound)
        assertEquals(3, p2.condBound)
        assertEquals(1320, p2.nightFrom)
        assertEquals(360, p2.nightTo)
        assertTrue(r2.generated[0].declarations.any { it.contains("ОКНО НОЧИ") },
            r2.generated[0].declarations.toString())
        // Окно ночи без признака пояса — отказ, как у серии ночных.
        val r3 = compile(writePackage(tmp.resolve("z").also { it.toFile().mkdirs() },
            rule("R-N3", "HARD",
                listOf(param("max_nights_window", 2, "count"),
                       param("window_days", 3, "window", unit = "count"),
                       param("night_duty_start", 1320, "min", unit = "time_of_day_minutes"),
                       param("night_duty_end", 360, "max", unit = "time_of_day_minutes")),
                nightLogic)))
        assertEquals(1, r3.refused.size, r3.generated.toString())
    }

    private fun fittedRule(id: String, family: String, params: Map<String, Int>) =
        """{"id": "$id", "title": "$id", "statement": "$id", "zone": "ROSTERING", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.1"}], "params": [],
            "template_fit": {"category": "A", "family": "$family", "registry_rev": "063f3cc0ea00",
                             "quote": "$id", "params": {${params.entries.joinToString(",") {
                                 "\"${it.key}\": {\"value\": ${it.value}, \"quote\": \"${it.key} ${it.value}\"}" }}}}}"""

    @Test
    fun `посадка на семью истории без блока logic — логика формы синтезируется из посадки`(@TempDir tmp: Path) {
        // Первый живой пакет H0-КИ-окна (05.09.2026): три правила с посадкой
        // night_series / rolling_rest / days_off_floor и без блока logic.
        val rules = listOf(
            fittedRule("VN-091", "night_series", mapOf("max_consecutive_night_duties" to 2,
                "night_duty_start" to 1320, "night_duty_end" to 360)),
            fittedRule("VN-092", "rolling_rest", mapOf("weekly_rest_required" to 2160,
                "weekly_rest_period" to 10080)),
            fittedRule("VN-093", "days_off_floor", mapOf("min_days_off" to 8)))
        val r = compile(writePackage(tmp, rules.joinToString(",")))
        assertEquals(3, r.blocksSeen)
        assertEquals(3, r.generated.size, r.refused.toString() + r.ignored.toString())
        val byRule = r.generated.associateBy { it.ruleId }
        val night = byRule["VN-091"]!!.plan!!
        assertEquals(LogicForms.FORM_CONSECUTIVE_NIGHT_DUTIES_MAX, night.form)
        assertEquals(2, night.bound)
        assertEquals(1320, night.nightFrom)
        assertEquals(360, night.nightTo)
        val roll = byRule["VN-092"]!!.plan!!
        assertEquals(LogicForms.FORM_REST_IN_ROLLING_WINDOW, roll.form)
        assertEquals(2160, roll.bound)
        assertEquals(7, roll.condBound)
        val off = byRule["VN-093"]!!.plan!!
        assertEquals(LogicForms.FORM_DAYS_OFF_PER_MONTH_MIN, off.form)
        assertEquals(8, off.bound)
        // Посадка без одного из параметров — блока нет, правило не в корзинах.
        val partial = compile(writePackage(tmp.resolve("p").also { it.toFile().mkdirs() },
            fittedRule("VN-091", "night_series", mapOf("max_consecutive_night_duties" to 2))))
        assertEquals(0, partial.blocksSeen)
        assertTrue(partial.generated.isEmpty() && partial.refused.isEmpty())
    }

    @Test
    fun `посадка со вторым набором параметров — счётчики скользящих окон из посадки`(@TempDir tmp: Path) {
        val rules = listOf(
            fittedRule("UZ-N3", "night_series", mapOf("max_night_duties_in_window" to 2,
                "night_window_days" to 3, "night_duty_start" to 1320, "night_duty_end" to 360)),
            fittedRule("UZ-OFF7", "days_off_floor", mapOf("min_days_off_in_window" to 2,
                "days_off_window_days" to 7)))
        val r = compile(writePackage(tmp, rules.joinToString(",")))
        assertEquals(2, r.generated.size, r.refused.toString() + r.ignored.toString())
        val byRule = r.generated.associateBy { it.ruleId }
        val n = byRule["UZ-N3"]!!.plan!!
        assertEquals(LogicForms.FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX, n.form)
        assertEquals(2, n.bound)
        assertEquals(3, n.condBound)
        assertEquals(1320, n.nightFrom)
        assertEquals(360, n.nightTo)
        val d = byRule["UZ-OFF7"]!!.plan!!
        assertEquals(LogicForms.FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN, d.form)
        assertEquals(2, d.bound)
        assertEquals(7, d.condBound)
    }
}
