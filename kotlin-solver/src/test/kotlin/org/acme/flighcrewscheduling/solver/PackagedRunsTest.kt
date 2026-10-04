package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.config.solver.termination.TerminationConfig
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.SerializationFeature
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.rest.PackagedRunResult
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNotSame
import org.junit.jupiter.api.Assertions.assertSame
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.nio.file.Path
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * Прогоны по идентификатору (этап Г, Ш-2/Ш-2а): гейты до старта счёта,
 * состояния считает/готов/упал, показания — изнутри окна сборки, гейт
 * двусмысленности источников пакета.
 */
class PackagedRunsTest {

    private val executor = Executors.newSingleThreadExecutor()

    @AfterEach
    fun cleanup() {
        TaskPackage.overrideForTests(null)
        PackagedRuns.clearForTests()
        RunThresholds.clearForTests()
        executor.shutdownNow()
    }

    private fun sha256(b: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(b)
            .joinToString("") { x -> "%02x".format(x) }

    /** Минимальный годный пакет БАЙТАМИ (без файла: запросный путь). */
    private fun packageZip(tmp: Path, specVersion: String = "1.21"): ByteArray {
        val objective = """{"customer": "проба", "readiness": "в",
            "rostering": {"ready": true, "direction": "minimize", "objectives": [], "components": []}}"""
        val model = """{"meta": {"revision": 1, "model_schema": 2}, "rules": []}"""
        val manifest = """
            {"spec_version": "$specVersion", "customer": "проба", "source_run": "run-T", "revision": 1,
             "readiness": "в",
             "files": [
               {"name": "objective.json", "sha256": "${sha256(objective.toByteArray(Charsets.UTF_8))}"},
               {"name": "model.json", "sha256": "${sha256(model.toByteArray(Charsets.UTF_8))}"}],
             "allowed_modes": {"rostering": ["feasibility-soft"]},
             "objectives": {"rostering": []},
             "has_data": true}
        """.trimIndent()
        val zip = tmp.resolve("p.zip").toFile()
        ZipOutputStream(zip.outputStream()).use { z ->
            for ((name, body) in listOf("objective.json" to objective,
                                        "model.json" to model, "manifest.json" to manifest)) {
                z.putNextEntry(ZipEntry(name)); z.write(body.toByteArray(Charsets.UTF_8)); z.closeEntry()
            }
        }
        return zip.readBytes()
    }

    private fun problem(): FlightCrewSchedule {
        val home = airport("PLY"); val out = airport("OUT")
        val e1 = employee("E1").apply { homeAirport = home }
        val f = flight("F1", home, t(2, 10), out, t(2, 12))
        return Fixtures.schedule(listOf(home, out), listOf(e1), listOf(assignment("s1", f, e1)))
    }

    private fun cfg(): SolverConfig = SolverConfig()
        .withSolutionClass(FlightCrewSchedule::class.java)
        .withEntityClasses(FlightAssignment::class.java)
        .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        .withTerminationConfig(TerminationConfig().withSpentLimit(java.time.Duration.ofSeconds(2)))

    private fun await(r: RunRecord, seconds: Int = 60): RunRecord {
        val deadline = System.currentTimeMillis() + seconds * 1000L
        while (r.state == PackagedRuns.ST_RUNNING && System.currentTimeMillis() < deadline) {
            Thread.sleep(100)
        }
        return r
    }

    @Test
    fun `прогон по идентификатору — считает, готов, показания изнутри окна`(@TempDir tmp: Path) {
        val r = PackagedRuns.start(problem(), packageZip(tmp), "feasibility-soft", null,
            cfg(), executor)
        assertTrue(r.runId.isNotBlank())
        assertEquals(r, PackagedRuns.get(r.runId))
        await(r)
        assertEquals(PackagedRuns.ST_DONE, r.state, r.failure ?: "")
        assertNotNull(r.solution)
        assertNotNull(r.score)
        val report = r.report!!
        // Показания сняты внутри окна — непустые и с ключами профиля.
        assertTrue(report.profileReadings.isNotEmpty())
        assertTrue("max_duty_span_min" in report.profileReadings)
        // Статус — машинный снимок с отпечатком пакета.
        val st = r.statusSnapshot()
        assertEquals(PackagedRuns.ST_DONE, st["state"])
        assertEquals(r.packageFingerprint, st["packageFingerprint"])
        // K-15-a: без планов формы окна отдыха показания rolling_rest_audit нет.
        assertEquals(null, st["rollingRestAudit"])
        assertTrue("rolling_rest_audit" !in report.coverage)
    }

    @Test
    fun `archival wire remains readable but cannot start a solver`(@TempDir tmp: Path) {
        val zip = packageZip(tmp, specVersion = "1.6")
        val archived = TaskPackage.readBytes(zip)
        val error = assertThrows<TaskModeRefused> {
            TaskModes.resolve("feasibility-soft", archived, "rostering")
        }
        assertTrue(error.message!!.contains("landing-version"), error.message)
    }

    private val EMPTY_MODEL = """{"meta": {"revision": 1, "model_schema": 2}, "rules": []}"""

    /** Модель с порогом стыковки ВНЕ базы 11 часов (ручка min_sit_outstation_min). */
    private val SIT_OUT_660_MODEL = """{"meta": {"revision": 1, "model_schema": 2}, "rules": [
        {"id": "R-SIT", "kind": "HARD", "title": "Стыковка вне базы",
         "template_fit":{"schema_version":"landing/2","category":"A","family":"connection_windows",
           "registry_rev":"${"a".repeat(64)}","family_contract":{
             "family_id":"connection_windows","catalog_version":"synthetic-run-fixture",
             "catalog_fingerprint":"${"a".repeat(64)}","record_fingerprint":"${"b".repeat(64)}",
             "param_set":"default"},"params":{"min_turnaround":{
               "value":660,"unit":"duration_minutes","relation":"min",
               "condition":{"kind":"area","value":"вне базы"},"quote":"11 часов",
               "source":{"kind":"rule","rule_id":"R-SIT"}}}},
         "params": [{"name": "min_turnaround", "value": 660, "unit": "duration_minutes",
           "relation": "min", "qualifier": "вне базы", "as_written": "11 часов"}]}]}"""

    /** Пакет с ДВУМЯ задачами: ростеринг и пейринг (допустимость по жёстким);
     *  модель правил — по выбору стенда (пороги жёстких правил). */
    private fun packageZipTwoTasks(tmp: Path, model: String = EMPTY_MODEL): ByteArray {
        val objective = """{"customer": "проба", "readiness": "в",
            "rostering": {"ready": true, "direction": "minimize", "objectives": [], "components": []},
            "pairing": {"ready": true, "direction": "minimize", "objectives": [], "components": []}}"""
        val manifest = """
            {"spec_version": "1.21", "customer": "проба", "source_run": "run-T", "revision": 1,
             "readiness": "в",
             "files": [
               {"name": "objective.json", "sha256": "${sha256(objective.toByteArray(Charsets.UTF_8))}"},
               {"name": "model.json", "sha256": "${sha256(model.toByteArray(Charsets.UTF_8))}"}],
             "allowed_modes": {"rostering": ["feasibility-soft"], "pairing": ["feasibility-hard"]},
             "objectives": {"rostering": [], "pairing": []},
             "has_data": true}
        """.trimIndent()
        val zip = tmp.resolve("p2.zip").toFile()
        ZipOutputStream(zip.outputStream()).use { z ->
            for ((name, body) in listOf("objective.json" to objective,
                                        "model.json" to model, "manifest.json" to manifest)) {
                z.putNextEntry(ZipEntry(name)); z.write(body.toByteArray(Charsets.UTF_8)); z.closeEntry()
            }
        }
        return zip.readBytes()
    }

    @Test
    fun `задача прогона параметром — пейринг гоняется семейством пакета`(@TempDir tmp: Path) {
        val r = PackagedRuns.start(problem(), packageZipTwoTasks(tmp), "feasibility-hard", null,
            cfg(), executor, task = "pairing")
        await(r)
        assertEquals(PackagedRuns.ST_DONE, r.state, r.failure ?: "")
        assertEquals("pairing", r.statusSnapshot()["task"])
    }

    @Test
    fun `ростеринг на утверждённом пейринге — цепочки зафиксированы`(@TempDir tmp: Path) {
        val zip = packageZipTwoTasks(tmp)
        val pairing = await(PackagedRuns.start(problem(), zip, "feasibility-hard", null,
            cfg(), executor, task = "pairing"))
        assertEquals(PackagedRuns.ST_DONE, pairing.state, pairing.failure ?: "")
        val rostering = await(PackagedRuns.start(problem(), zip, "feasibility-soft", null,
            cfg(), executor, task = "rostering", pairingRunId = pairing.runId))
        assertEquals(PackagedRuns.ST_DONE, rostering.state, rostering.failure ?: "")
        val st = rostering.statusSnapshot()
        assertEquals(pairing.runId, st["pairingRunId"])
        assertEquals(1, st["chains"])
    }

    // ------------------------------------------------- Г-П4: внешний каталог

    private fun catalogJson(verdict: String = "INFEASIBLE_INPUT",
                            fns: List<List<String>> = listOf(listOf("F1"))): ByteArray {
        val sel = fns.joinToString(",") { ch ->
            """{"fns": [${ch.joinToString(",") { "\"$it\"" }}], "active_fns": [${ch.joinToString(",") { "\"$it\"" }}]}"""
        }
        return """{"coverage": {"verdict": "$verdict"}, "selected": [$sel]}"""
            .toByteArray(Charsets.UTF_8)
    }

    @Test
    fun `каталог построителя регистрируется и фиксируется ростерингом`(@TempDir tmp: Path) {
        val zip = packageZipTwoTasks(tmp)
        val cat = catalogJson()
        val rec = PackagedRuns.registerPairingCatalog(problem(), zip, cat, sha256(cat))
        assertEquals(PackagedRuns.ST_DONE, rec.state)
        assertEquals("pairing", rec.task)
        assertEquals(1, rec.chains)
        // K-16: отпечаток принятого каталога — в статусе пейринга…
        assertEquals(sha256(cat), rec.statusSnapshot()["catalogSha256"])
        val rostering = await(PackagedRuns.start(problem(), zip, "feasibility-soft", null,
            cfg(), executor, task = "rostering", pairingRunId = rec.runId))
        assertEquals(PackagedRuns.ST_DONE, rostering.state, rostering.failure ?: "")
        assertEquals(1, rostering.statusSnapshot()["chains"])
        // …и унаследован ростерингом на нём.
        assertEquals(sha256(cat), rostering.statusSnapshot()["catalogSha256"])
        // K-32/K-8: известная часть цены ночёвок — в статусе прогона.
        val cost = rostering.statusSnapshot()["layoverCost"] as Map<*, *>
        assertEquals(2, cost["version"])
        assertEquals("B", cost["contract"])
        assertEquals(false, cost["full_cost_confirmed"])
        assertEquals("flight_program_departure_calendar_span", cost["scope_source"])
    }

    @Test
    fun `H11-WIRE-1 — решение прогона несёт отчёт ночёвок того же прогона`(@TempDir tmp: Path) {
        // Рецензия GPT-024 (06.09.2026): `run/{id}/result` отдавал голый
        // FlightCrewSchedule без layoverCost, отчёт жил только в
        // applied-rules — экспортёр входа сертификатора его терял, и сверка
        // цены всегда давала «несравнимо». Стенд: два прогона одного набора
        // (ночь на OUT между рейсами соседних суток); ответ result каждого
        // несёт ТОТ ЖЕ объект отчёта, что статус и applied-rules ЕГО runId
        // (не соседа и не «последний»), с областью и известными событиями;
        // поля решения — на верхнем уровне, формат обмена прежний.
        fun twoDays(): FlightCrewSchedule {
            val home = airport("PLY"); val out = airport("OUT")
            val e1 = employee("E1").apply { homeAirport = home }
            val f1 = flight("F1", home, t(2, 10), out, t(2, 12))
            val f2 = flight("F2", out, t(3, 10), home, t(3, 12))
            return Fixtures.schedule(listOf(home, out), listOf(e1),
                listOf(assignment("s1", f1, e1), assignment("s2", f2, e1)))
        }
        val zip = packageZip(tmp)
        val a = await(PackagedRuns.start(twoDays(), zip, "feasibility-soft", null, cfg(), executor))
        val b = await(PackagedRuns.start(twoDays(), zip, "feasibility-soft", null, cfg(), executor))
        assertEquals(PackagedRuns.ST_DONE, a.state, a.failure ?: "")
        assertEquals(PackagedRuns.ST_DONE, b.state, b.failure ?: "")
        val viewA = PackagedRunResult.of(a)
        assertSame(a.solution, viewA.solution)
        assertNotNull(a.layoverCost)
        // Тот же объект, что статус и applied-rules ЭТОГО runId — не соседа.
        assertSame(a.layoverCost, viewA.layoverCost)
        assertSame(a.layoverCost, a.statusSnapshot()["layoverCost"])
        assertNotSame(a.layoverCost, b.layoverCost)
        assertSame(b.layoverCost, PackagedRunResult.of(b).layoverCost)
        // Провод: JSON ответа — решение на верхнем уровне + layoverCost рядом.
        val mapper = ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.FAIL_ON_EMPTY_BEANS)
        val json = mapper.readValue(mapper.writeValueAsString(viewA), Map::class.java)
        for (key in listOf("flightAssignments", "employees", "flights", "score")) {
            assertTrue(json.containsKey(key), "решение развёрнуто на верхнем уровне: нет ключа " + key)
        }
        val cost = json["layoverCost"] as Map<*, *>
        assertEquals(2, cost["version"])
        assertEquals("B", cost["contract"])
        assertTrue(cost.containsKey("scope") && cost.containsKey("scope_source"))
        assertEquals(1, cost["known_nights"])
        val events = cost["known_events"] as List<*>
        assertEquals(1, events.size)
        assertEquals("E1", (events[0] as Map<*, *>)["employee"])
        assertEquals("OUT", (events[0] as Map<*, *>)["station"])
        // Не готов — отказ словами состояния, не пустота.
        val running = RunRecord("r-считает", 0L, "fp", "feasibility-soft", pkg = a.pkg)
        assertThrows<TaskModeRefused> { PackagedRunResult.of(running) }
    }

    @Test
    fun `two identical Pilot labels remain two independent slot chains`(@TempDir tmp: Path) {
        // K-17/K-24 (стенд GPT без частей chainSearch/seed): два места Pilot
        // одного рейса — две цепочки-слоя; раздача по цепочкам
        // (moves=chain-seed) даёт каждой своего человека, все места заняты.
        val home = airport("PLY"); val out = airport("OUT")
        val e1 = employee("E1").apply { homeAirport = home }
        val e2 = employee("E2").apply { homeAirport = home }
        val flights = listOf(flight("F1", home, t(2, 8), out, t(2, 9)),
            flight("F2", out, t(2, 10), home, t(2, 11)))
        fun twoSlotProblem(): FlightCrewSchedule = Fixtures.schedule(listOf(home, out), listOf(e1, e2),
            flights.flatMap { f -> (0..1).map { slot ->
                FlightAssignment("${f.flightNumber}-$slot", f, slot, Fixtures.SKILL)
            } })
        val zip = packageZipTwoTasks(tmp)
        val catalog = catalogJson("OK", listOf(listOf("F1", "F2")))
        val pairing = PackagedRuns.registerPairingCatalog(twoSlotProblem(), zip, catalog, sha256(catalog))
        assertEquals(2, pairing.chains)
        assertEquals(setOf("каталог:0:slot:0", "каталог:0:slot:1"), pairing.chainBySeat!!.values.toSet())
        val run = await(PackagedRuns.start(twoSlotProblem(), zip, "feasibility-soft", null,
            cfg(), executor, pairingRunId = pairing.runId, moves = "chain-seed"))
        assertEquals(PackagedRuns.ST_DONE, run.state, run.failure ?: "")
        val solved = run.solution!!
        val layers = solved.flightAssignments.groupBy { it.chainId }
        assertEquals(2, layers.size)
        layers.values.forEach { assertEquals(1, it.map { a -> a.employee }.distinct().size) }
        assertTrue(solved.flightAssignments.all { it.employee != null })
        assertEquals(2, layers.values.map { it.first().employee }.distinct().size)
    }

    @Test
    fun `зерно прогона доезжает до решателя — разные зёрна дают разные траектории`(@TempDir tmp: Path) {
        // K-7: randomSeed конфигурации действует и вне детерминированного
        // режима. Локальный поиск без конструктива от пустого решения (места
        // допускают незанятость), счётный предел и один поток — траектория
        // воспроизводима при одном зерне; хотя бы одна пара из четырёх зёрен
        // расходится (случайный выбор ходов идёт от зерна).
        val home = airport("PLY"); val out = airport("OUT")
        fun world(): FlightCrewSchedule {
            val es = (1..4).map { employee("E$it").apply { homeAirport = home } }
            val seats = (1..6).flatMap { d ->
                val f1 = flight("F${d}a", home, t(d, 8), out, t(d, 10))
                val f2 = flight("F${d}b", out, t(d, 12), home, t(d, 14))
                listOf(FlightAssignment("s${d}a", f1, 0, Fixtures.SKILL), FlightAssignment("s${d}b", f2, 0, Fixtures.SKILL))
            }
            return Fixtures.schedule(listOf(home, out), es, seats)
        }
        fun cfgSeed(seed: Long): SolverConfig = SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
            .withRandomSeed(seed).withMoveThreadCount("NONE")
            .withPhases(ai.timefold.solver.core.config.localsearch.LocalSearchPhaseConfig())
            .withTerminationConfig(TerminationConfig().withStepCountLimit(40))
        val zip = packageZip(tmp)
        fun trajectory(seed: Long): List<String> {
            val r = await(PackagedRuns.start(world(), zip, "feasibility-soft", null, cfgSeed(seed), executor))
            assertEquals(PackagedRuns.ST_DONE, r.state, r.failure ?: "")
            return r.solution!!.flightAssignments.sortedBy { it.id }.map { it.employee?.id ?: "-" } +
                r.progress.get().map { it["hard"].toString() + "/" + it["soft"] }
        }
        assertEquals(trajectory(17), trajectory(17))
        val distinct = (1L..4L).map { trajectory(it) }.distinct().size
        assertTrue(distinct > 1, "четыре зерна дали одну траекторию — зерно не доезжает до решателя")
    }

    /** Каталог с окнами занятости: две пересекающиеся цепочки роли PILOT. */
    private fun catalogWithWindows(rest: Long = 720): ByteArray =
        ("""{"coverage": {"verdict": "OK"}, "selected": [
           {"fns": ["F1"], "active_fns": ["F1"], "seats": ["PILOT"],
            "busyWindows": [[1000, 1400]], "minRestAfterMin": $rest},
           {"fns": ["F9"], "active_fns": ["F9"], "seats": ["PILOT"],
            "busyWindows": [[1200, 1600]], "minRestAfterMin": $rest}]}""")
            .toByteArray(Charsets.UTF_8)

    @Test
    fun `гейт размещаемости — пик выше численности даёт отказ с числами`(@TempDir tmp: Path) {
        val cat = catalogWithWindows()
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.registerPairingCatalog(problem(), packageZipTwoTasks(tmp), cat, sha256(cat))
        }
        val msg = e.message ?: ""
        assertTrue("неразмещаем" in msg, msg)
        assertTrue("пик" in msg && "2" in msg && "1" in msg, msg)
    }

    @Test
    fun `гейт размещаемости — явное решение оператора пропускает`(@TempDir tmp: Path) {
        val cat = catalogWithWindows()
        val rec = PackagedRuns.registerPairingCatalog(
            problem(), packageZipTwoTasks(tmp), cat, sha256(cat), acceptUnplaceable = true)
        assertEquals(PackagedRuns.ST_DONE, rec.state)
        assertTrue("размещаемость" in (rec.score ?: ""), rec.score ?: "")
    }

    @Test
    fun `каталог без окон занятости — размещаемость честно не проверена`(@TempDir tmp: Path) {
        val cat = catalogJson(verdict = "OK")
        val rec = PackagedRuns.registerPairingCatalog(problem(), packageZipTwoTasks(tmp), cat, sha256(cat))
        assertEquals(PackagedRuns.ST_DONE, rec.state)
        assertTrue("не проверена" in (rec.score ?: ""), rec.score ?: "")
    }

    @Test
    fun `каталог с ослабленным покрытием — отказ регистрации`(@TempDir tmp: Path) {
        val cat = catalogJson(verdict = "RELAXED_COVER")
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.registerPairingCatalog(problem(), packageZipTwoTasks(tmp), cat, sha256(cat))
        }
        assertTrue("RELAXED_COVER" in (e.message ?: ""), e.message ?: "")
    }

    @Test
    fun `подменённый каталог — отказ отпечатком`(@TempDir tmp: Path) {
        val cat = catalogJson()
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.registerPairingCatalog(problem(), packageZipTwoTasks(tmp), cat,
                "0".repeat(64))
        }
        assertTrue("подменён" in (e.message ?: ""), e.message ?: "")
    }

    @Test
    fun `каталог не разбиение — рейс в двух цепочках, отказ`(@TempDir tmp: Path) {
        val cat = catalogJson(fns = listOf(listOf("F1"), listOf("F1")))
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.registerPairingCatalog(problem(), packageZipTwoTasks(tmp), cat, sha256(cat))
        }
        assertTrue("не разбиение" in (e.message ?: ""), e.message ?: "")
    }

    @Test
    fun `фиксация пейринга чужого пакета — отказ словами отпечатка`(@TempDir tmp: Path) {
        val pairing = await(PackagedRuns.start(problem(), packageZipTwoTasks(tmp),
            "feasibility-hard", null, cfg(), executor, task = "pairing"))
        assertEquals(PackagedRuns.ST_DONE, pairing.state, pairing.failure ?: "")
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.start(problem(), packageZip(tmp), "feasibility-soft", null,
                cfg(), executor, task = "rostering", pairingRunId = pairing.runId)
        }
        assertTrue(e.message!!.contains("по другому пакету"), e.message)
    }

    @Test
    fun `pairingRunId у задачи pairing — отказ`(@TempDir tmp: Path) {
        val zip = packageZipTwoTasks(tmp)
        val pairing = await(PackagedRuns.start(problem(), zip, "feasibility-hard", null,
            cfg(), executor, task = "pairing"))
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.start(problem(), zip, "feasibility-hard", null,
                cfg(), executor, task = "pairing", pairingRunId = pairing.runId)
        }
        assertTrue(e.message!!.contains("применим только к задаче rostering"), e.message)
    }

    @Test
    fun `фиксация несуществующего пейринга — отказ реестром`(@TempDir tmp: Path) {
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.start(problem(), packageZipTwoTasks(tmp), "feasibility-soft", null,
                cfg(), executor, task = "rostering", pairingRunId = "нет-такого")
        }
        assertTrue(e.message!!.contains("не найден в реестре"), e.message)
    }

    @Test
    fun `задача вне пакета — отказ словами allowed_modes своей задачи`(@TempDir tmp: Path) {
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.start(problem(), packageZip(tmp), "feasibility-hard", null,
                cfg(), executor, task = "pairing")
        }
        assertTrue(e.message!!.contains("не разрешён пакетом для задачи pairing"), e.message)
    }

    @Test
    fun `двусмысленность источников — отказ до чтения содержимого`(@TempDir tmp: Path) {
        val zip = packageZip(tmp)
        TaskPackage.overrideForTests(TaskPackage.readBytes(zip))
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.start(problem(), zip, "feasibility-soft", null, cfg(), executor)
        }
        assertTrue(e.message!!.contains("двусмысленность"), e.message)
    }

    @Test
    fun `падение сборки — состояние «упал» с причиной, не молчание`(@TempDir tmp: Path) {
        // Конфигурация без класса решения роняет сборку фабрики ВНУТРИ задачи:
        // это Ш-2а — статус обязан сказать «упал» и почему.
        val r = PackagedRuns.start(problem(), packageZip(tmp), "feasibility-soft", null,
            SolverConfig(), executor)
        await(r)
        assertEquals(PackagedRuns.ST_FAILED, r.state)
        assertTrue(!r.failure.isNullOrBlank())
        assertEquals(null, r.solution)
    }

    @Test
    fun `мусор вместо пакета — отказ словами разбора, прогон не создаётся`(@TempDir tmp: Path) {
        assertThrows<TaskPackageError> {
            PackagedRuns.start(problem(), ByteArray(8) { 1 }, "feasibility-soft", null, cfg(), executor)
        }
        // Гейт режимов — до старта счёта: запрещённый режим не рождает записи.
        assertThrows<TaskModeRefused> {
            PackagedRuns.start(problem(), packageZip(tmp), "optimization", null, cfg(), executor)
        }
    }


    // ------------------------------------------------- шаг 4 (05.09.2026)

    /** Цепочка F1 с перегоном F2; на F2 есть перегонное кресло; место F1 свободно. */
    private fun problemWithDeadhead(): FlightCrewSchedule {
        val home = airport("PLY"); val out = airport("OUT")
        val e1 = employee("E1").apply { homeAirport = home }
        val f1 = flight("F1", home, t(2, 10), out, t(2, 12))
        val f2 = flight("F2", out, t(2, 14), home, t(2, 16))
        val seat = FlightAssignment("s1", f1, 0, Fixtures.SKILL)
        val dh = FlightAssignment("DH-F2-1", f2, 101, "").apply {
            crewSlotId = id; deadhead = true
        }
        val s = Fixtures.schedule(listOf(home, out), listOf(e1), listOf(seat, dh))
        s.flights = mutableListOf(f1, f2)
        return s
    }

    @Test
    fun `перегон цепочки входит в её единицу, moves=chain раздаёт цепочку до поиска`(@TempDir tmp: Path) {
        val zip = packageZipTwoTasks(tmp)
        val cat = """{"coverage": {"verdict": "OK"}, "selected": [
            {"fns": ["F1"], "active_fns": ["F1"], "dh": ["F2"]}]}""".toByteArray(Charsets.UTF_8)
        val rec = PackagedRuns.registerPairingCatalog(problemWithDeadhead(), zip, cat, sha256(cat))
        // K-17: имя слоя — место (slot), не роль.
        assertEquals(mapOf("каталог:0:slot:0" to listOf("F2")), rec.dhByChain)
        val rostering = await(PackagedRuns.start(problemWithDeadhead(), zip, "feasibility-soft", null,
            cfg(), executor, task = "rostering", pairingRunId = rec.runId, moves = "chain-seed"))
        assertEquals(PackagedRuns.ST_DONE, rostering.state, rostering.failure ?: "")
        assertEquals("chain-seed", rostering.moves)
        assertEquals(1, rostering.seeded)
        val st = rostering.statusSnapshot()
        assertEquals("chain-seed", st["moves"])
        assertEquals(1, st["seeded"])
        assertTrue(st["seedBreakdown"] is Map<*, *>, "разложение начального решения в статусе")
        val seats = rostering.solution!!.flightAssignments.associateBy { it.id }
        assertEquals("каталог:0:slot:0", seats["DH-F2-1"]!!.chainId)
        assertEquals("E1", seats["s1"]!!.employee?.id)
        assertEquals("E1", seats["DH-F2-1"]!!.employee?.id)
    }


    // ------------------------------------------------- K-30-a (единая версия 06.09.2026)

    /** Две цепочки одного дня: A = F1 (PLY→OUT 08–10), B = F2 (OUT→PLY
     *  20:30–22:30) — стыковка 10,5 ч в OUT; при двух людях E2 несёт чужое
     *  место дня 5 (300 мин) и по нагрузке тяжелее E1. */
    private fun problemSit(vararg ids: String): FlightCrewSchedule {
        val home = airport("PLY"); val out = airport("OUT")
        val es = ids.map { employee(it).apply { homeAirport = home } }
        val f1 = flight("F1", home, t(2, 8), out, t(2, 10))
        val f2 = flight("F2", out, t(2, 20, 30), home, t(2, 22, 30))
        val f3 = flight("F3", home, t(5, 8), out, t(5, 13))
        val seats = mutableListOf(FlightAssignment("s1", f1, 0, Fixtures.SKILL),
            FlightAssignment("s2", f2, 0, Fixtures.SKILL))
        if (es.size > 1) seats.add(assignment("s3", f3, es[1]))
        return Fixtures.schedule(listOf(home, out), es, seats)
    }

    /** Поиск без шагов: решение прогона = начальное (раздача цепочек). */
    private fun cfgNoSearch(): SolverConfig = SolverConfig()
        .withSolutionClass(FlightCrewSchedule::class.java)
        .withEntityClasses(FlightAssignment::class.java)
        .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        .withPhases(ai.timefold.solver.core.config.localsearch.LocalSearchPhaseConfig())
        .withTerminationConfig(TerminationConfig().withStepCountLimit(0))

    @Test
    fun `раздача цепочек судит порогами прогона, а не литералами профиля`(@TempDir tmp: Path) {
        // K-30-a: пакет с порогом стыковки вне базы 11 ч — E1 после A
        // несовместим с B по соседям (10,5 ч < 11 ч), B уходит E2, хотя тот
        // тяжелее по нагрузке. Пакет без порога (стыковка 0 = правила нет)
        // — B остаётся у самого свободного E1. Литерал профиля (60 мин)
        // дал бы E1 в обоих случаях: раздача обязана видеть снимок порогов
        // прогона, снятый в start до раздачи.
        val cat = catalogJson("OK", listOf(listOf("F1"), listOf("F2")))
        fun holderOfB(model: String): String {
            val zip = packageZipTwoTasks(tmp, model)
            val pairing = PackagedRuns.registerPairingCatalog(problemSit("E1", "E2"), zip, cat, sha256(cat))
            assertEquals(2, pairing.chains)
            val run = await(PackagedRuns.start(problemSit("E1", "E2"), zip, "feasibility-soft", null,
                cfgNoSearch(), executor, pairingRunId = pairing.runId, moves = "chain-seed"))
            assertEquals(PackagedRuns.ST_DONE, run.state, run.failure ?: "")
            assertEquals(2, run.seeded)
            return run.solution!!.flightAssignments.first { it.id == "s2" }.employee!!.id
        }
        assertEquals("E2", holderOfB(SIT_OUT_660_MODEL))
        assertEquals("E1", holderOfB(EMPTY_MODEL))
    }

    @Test
    fun `нет совместимого по соседям — fallback раздачи виден в статусе прогона`(@TempDir tmp: Path) {
        // K-30-c: один человек, порог стыковки вне базы 11 ч при стыковке
        // 10,5 ч — цепочка B отдана непроверенному пригодному кандидату:
        // seedReading называет её в fallback, checked = 1; fallback не
        // считается успешной раздачей (seeded — лишь число занятых цепочек).
        val cat = catalogJson("OK", listOf(listOf("F1"), listOf("F2")))
        val zip = packageZipTwoTasks(tmp, SIT_OUT_660_MODEL)
        val pairing = PackagedRuns.registerPairingCatalog(problemSit("E1"), zip, cat, sha256(cat))
        val run = await(PackagedRuns.start(problemSit("E1"), zip, "feasibility-soft", null,
            cfgNoSearch(), executor, pairingRunId = pairing.runId, moves = "chain-seed"))
        assertEquals(PackagedRuns.ST_DONE, run.state, run.failure ?: "")
        val st = run.statusSnapshot()
        val reading = st["seedReading"] as Map<*, *>
        assertEquals(1, reading["checked"])
        assertEquals(listOf("каталог:1:slot:0"), reading["fallback"])
        assertEquals(emptyList<String>(), reading["unseeded"])
        assertEquals(2, st["seeded"])
        // Без раздачи показания нет.
        val plain = await(PackagedRuns.start(problem(), packageZip(tmp), "feasibility-soft", null, cfg(), executor))
        assertEquals(null, plain.statusSnapshot()["seedReading"])
    }

    @Test
    fun `негодное окно учёта — отказ словами до раздачи, синхронно`(@TempDir tmp: Path) {
        // K-30-a: bounds() судит window_horizon в start (до фиксации и
        // раздачи), а не в исполнителе — отказ приходит вызывающему словами,
        // записи прогона нет.
        val p = problem().apply {
            windowHorizon = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
                start_inclusive = t(5, 0).toString(); end_exclusive = t(1, 0).toString()
            }
        }
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.start(p, packageZip(tmp), "feasibility-soft", null, cfg(), executor)
        }
        assertTrue(e.message!!.contains("window_horizon"), e.message)
    }

    // ------------------------------------------------- решение владельца 05.09.2026

    @Test
    fun `перегоны — только явным параметром или порогом пакета, показание в статусе`(@TempDir tmp: Path) {
        val zip = packageZip(tmp)
        // умолчание (простой набор Н0, требование владельца 05.09.2026):
        // перегонных кресел нет, показание называет основание
        val r = await(PackagedRuns.start(problem(), zip, "feasibility-soft", null,
            cfg(), executor, task = "rostering"))
        assertEquals(PackagedRuns.ST_DONE, r.state, r.failure ?: "")
        val dh = r.statusSnapshot()["deadheads"] as Map<*, *>
        assertEquals(false, dh["allowed"])
        assertEquals(0, dh["seatsAdded"])
        assertTrue((dh["basis"] as String).contains("перегонных кресел нет"))
        assertTrue(r.solution!!.flightAssignments.none { it.deadhead })
        // явный параметр 1 — одно перегонное кресло на рейс
        val r1 = await(PackagedRuns.start(problem(), zip, "feasibility-soft", null,
            cfg(), executor, task = "rostering", deadheadSeats = 1))
        val dh1 = r1.statusSnapshot()["deadheads"] as Map<*, *>
        assertEquals(true, dh1["allowed"])
        assertEquals(1, dh1["seatsPerFlight"])
        assertEquals(1, dh1["seatsAdded"])
        assertTrue((dh1["basis"] as String).contains("явный параметр"))
        assertTrue(r1.solution!!.flightAssignments.any { it.deadhead })
    }

    // ------------------------------------------------------- K-FIX-R2 (GPT-029)

    /** Тёплый старт: только локальный поиск режима chain, без конструктива. */
    private fun cfgWarmChain(): SolverConfig = SolverConfig()
        .withSolutionClass(FlightCrewSchedule::class.java)
        .withEntityClasses(FlightAssignment::class.java)
        .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        .withPhases(LargeMoves.lsPhase("chain")!!)
        .withTerminationConfig(TerminationConfig().withStepCountLimit(5))

    @Test
    fun `тёплый старт moves=chain — цепочка «пин + пусто» якорится до счёта, непригодный пин — отказ до мутации`(@TempDir tmp: Path) {
        // K-FIX-R2 (ревью GPT-029): warmStart + moves=chain — раздачи нет
        // (seedsChains=false) и конструктива нет; refuseSplitChains
        // пропускает «пин + пусто» как якоримую, поэтому якорение обязано
        // случиться в start синхронно — иначе цепочка с пином не двигается
        // (ChainMoves.chains) и её пустое место не заполняется (фильтр
        // одиночного хода отвергает место с меткой).
        val cat = catalogJson("OK", listOf(listOf("F1", "F2")))
        val zip = packageZipTwoTasks(tmp)
        fun pinned(eligible: Boolean): FlightCrewSchedule = problemSit("E1", "E2").also { p ->
            val e1 = p.employees.first { it.id == "E1" }
            p.flightAssignments.first { it.id == "s1" }.apply { employee = e1; pinned = true }
            if (!eligible) e1.unavailableDays = mutableListOf(java.time.LocalDate.of(2026, 3, 2))
        }
        val pairing = PackagedRuns.registerPairingCatalog(problemSit("E1", "E2"), zip, cat, sha256(cat))
        assertEquals(1, pairing.chains)
        // (а) пригодный пин: пустое место цепочки получает человека пина до
        // счёта, решение прогона держит цепочку у него целиком.
        val run = await(PackagedRuns.start(pinned(true), zip, "feasibility-soft", null,
            cfgWarmChain(), executor, pairingRunId = pairing.runId, moves = "chain"))
        assertEquals(PackagedRuns.ST_DONE, run.state, run.failure ?: "")
        assertEquals(null, run.seeded)
        val chainSeats = run.solution!!.flightAssignments.filter { it.chainId != null }
        assertEquals(setOf("s1", "s2"), chainSeats.map { it.id }.toSet())
        assertTrue(chainSeats.all { it.employee?.id == "E1" },
            "цепочка не заякорена пином: " + chainSeats.map { it.id + "→" + it.employee?.id })
        // (б) непригодный пин (E1 недоступен в день рейсов): отказ словами
        // до любой мутации назначений, прогон не создаётся.
        val bad = pinned(false)
        val before = bad.flightAssignments.map { it.employee?.id }
        val e = assertThrows<TaskModeRefused> {
            PackagedRuns.start(bad, zip, "feasibility-soft", null,
                cfgWarmChain(), executor, pairingRunId = pairing.runId, moves = "chain")
        }
        assertTrue(e.message!!.contains("непригоден") && e.message!!.contains("E1"), e.message)
        assertEquals(before, bad.flightAssignments.map { it.employee?.id })
    }
}
