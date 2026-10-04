package org.acme.flighcrewscheduling.solver

import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.nio.file.Path
import java.security.MessageDigest

/**
 * Мост CDPM -> COSMOS, этап А: приём пакета постановки и гейты запуска.
 *
 * Каждая отказная ветка условия МУ-02 проверена НАРУШАЮЩИМ случаем и
 * контрольным: без контроля «отказ случился» неотличим от «отказывает
 * всегда», а гейт, отвергающий годные пакеты, так же вреден, как
 * пропускающий негодные.
 */
class TaskPackageTest {

    @AfterEach
    fun cleanup() {
        TaskPackage.overrideForTests(null)
        LastRun.forget()
    }

    // ------------------------------------------------------- оснастка

    private fun sha256(b: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(b)
            .joinToString("") { x -> "%02x".format(x) }

    /** Минимальный годный пакет на диске: манифест + модель + критерий. */
    private fun writePackage(
        dir: Path,
        weightNights: String = "10",
        weightBalance: String = "3",
        specVersion: String = "1.0",
        /** Explicit opt-in for named historical landing/1 parser fixtures only. */
        archiveLanding1: Boolean = false,
        rosteringModes: String = """["feasibility-hard", "feasibility-soft"]""",
        rosteringObjectives: String = "[]",
        manifestObjectives: String = """{"pairing": [], "rostering": []}""",
        /** секция rule_maps манифеста (ревизия 1.14) как JSON либо null — секции нет. */
        ruleMaps: String? = null,
        /** тело threshold-forms.json либо null — файла нет; подписывается в files. */
        formsJson: String? = null,
        /** тело model.json (по умолчанию — пустая модель ревизии 3). */
        modelJson: String = """{"meta": {"revision": 3}}""",
        /** компоненты целевой пейринга (P-N-SHD: тесты семей подают свои id). */
        pairingComponents: String =
            """[{"rule_id": "R-S1-табл-3-строка-6-кол-3",
                 "title": "ночёвки", "weight": $weightNights}]""",
    ): File {
        val d = dir.toFile()
        val objective = """
            {"customer": "проба", "readiness": "в",
             "pairing": {"ready": true, "direction": "minimize",
               "components": $pairingComponents},
             "rostering": {"ready": true, "direction": "minimize",
               "objectives": $rosteringObjectives,
               "components": [{"rule_id": "R-S1-табл-4-строка-2-кол-3",
                               "title": "разброс", "weight": $weightBalance},
                              {"rule_id": "R-НЕТ-ИСПОЛНИТЕЛЯ",
                               "title": "чужое", "weight": 7}]}}
        """.trimIndent()
        val model = modelJson
        File(d, "objective.json").writeText(objective, Charsets.UTF_8)
        File(d, "model.json").writeText(model, Charsets.UTF_8)
        val formsEntry = if (formsJson == null) "" else {
            File(d, "threshold-forms.json")
                .writeText(formsJson, Charsets.UTF_8)
            """,
               {"name": "threshold-forms.json", "sha256": "${sha256(formsJson.toByteArray(Charsets.UTF_8))}"}"""
        }
        val ruleMapsField =
            if (ruleMaps == null) "" else """"rule_maps": $ruleMaps,"""
        val manifest = """
            {"spec_version": "$specVersion",
             ${if (archiveLanding1) "\"landing_compat\":1," else ""}
             "customer": "проба", "source_run": "run-0025", "revision": 3,
             "readiness": "в",
             "files": [
               {"name": "objective.json", "sha256": "${sha256(objective.toByteArray(Charsets.UTF_8))}"},
               {"name": "model.json", "sha256": "${sha256(model.toByteArray(Charsets.UTF_8))}"}$formsEntry],
             $ruleMapsField
             "allowed_modes": {"pairing": ["feasibility-hard"],
                               "rostering": $rosteringModes},
             "objectives": $manifestObjectives,
             "has_data": true}
        """.trimIndent()
        val mf = File(d, "manifest.json")
        mf.writeText(manifest, Charsets.UTF_8)
        return d
    }

    @Test
    fun `параметры видов HARD и DEF применяются, OUT и DATA - нет`(@TempDir tmp: Path) {
        // Линия нано 2026-08-30: константы мира (сдвиг шкалы времени)
        // приходят параметром правила-определения (kind=DEF).
        val d = writePackage(tmp, archiveLanding1 = true, modelJson = """
            {"meta": {"revision": 3}, "rules": [
              {"id": "R-DEF", "kind": "DEF", "title": "Шкала времени",
               "params": [{"name": "base_tz_offset_min", "value": 0,
                 "unit": "duration_minutes", "relation": "eq",
                 "as_written": "0 минут"}]},
              {"id": "R-HARD", "kind": "HARD", "title": "Смена",
               "params": [{"name": "max_duty_duration", "value": 720,
                 "unit": "duration_minutes", "relation": "max",
                 "as_written": "12 часов"}]},
              {"id": "R-OUT", "kind": "OUT", "title": "Отменено",
               "params": [{"name": "max_duty_duration", "value": 999,
                 "unit": "duration_minutes", "relation": "max",
                 "as_written": "нет"}]},
              {"id": "R-DATA", "kind": "DATA", "title": "Данные",
               "params": [{"name": "min_rest_home", "value": 1,
                 "unit": "duration_minutes", "relation": "min",
                 "as_written": "нет"}]}]}
        """.trimIndent())
        val names = load(d, landingCompat = 1).hardParams.map { it.ruleKind to it.name }
        assertEquals(setOf("DEF" to "base_tz_offset_min",
            "HARD" to "max_duty_duration"), names.toSet())
    }

    // ------------------------- calendar_window_penalties (семья calendar_windows)

    @Test
    fun `сутки пейринга из A посадки без legacy params доходят до applied`(@TempDir tmp: Path) {
        val d = writePackage(tmp, archiveLanding1 = true, modelJson = """
            {"meta": {"revision": 3}, "rules": [
              {"id": "R-DAYS-FIT", "kind": "HARD", "zone": "PAIRING",
               "template_fit": {"category": "A", "family": "duty_limits",
                 "registry_rev": "test",
                 "params": {"max_pairing_days": {"value": 1, "quote": "не более суток"}}}}]}
        """.trimIndent())
        val pkg = load(d, landingCompat = 1)
        val param = pkg.hardParams.single()
        assertEquals("max_pairing_days", param.name)
        assertEquals("count", param.unit)
        assertEquals("max", param.relation)
        val a = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
            .applied.getValue("max_pairing_days")
        assertEquals(1, a.value)
        assertEquals(listOf("R-DAYS-FIT"), a.rules)
    }

    @Test
    fun `семья механики правила мапит вес без координатных карт`(@TempDir tmp: Path) {
        // P-N-SHD (2026-08-30): правило ЧУЖОГО мира с id, которого нет
        // ни в rule_maps, ни в легаси-таблице, соотносится с
        // ограничением решателя полем export_solver.family — вес
        // доезжает без правки кода и без карт.
        val model = """{"meta": {"revision": 3},
            "rules": [{"id": "R-ЧУЖОЙ-МИР-строка-9", "kind": "SOFT",
                       "export_solver": {"family": "layover_penalty"}}]}"""
        val d = writePackage(
            tmp, archiveLanding1 = true, modelJson = model,
            pairingComponents =
                """[{"rule_id": "R-ЧУЖОЙ-МИР-строка-9",
                     "title": "ночёвки чужого мира", "weight": 10}]""")
        val pkg = load(d, landingCompat = 1)
        assertEquals(10L, pkg.weights["Layover nights away from home"])
        assertEquals(false,
            pkg.unmappedRules.contains("R-ЧУЖОЙ-МИР-строка-9"))
    }

    @Test
    fun `посадка правила — первичный источник семьи механики`(@TempDir tmp: Path) {
        // Пакет посадки A/B/C/D (решение владельца 2026-09-01 «от ВМП
        // и далее исключительно в COSMOS»): решатель читает семью из
        // template_fit.family первично; export_solver — легаси. Второе
        // правило — расхождение легаси-поля с посадкой: побеждает
        // посадка (живой случай VN-D91: текстовый классификатор дал
        // layover, посадка каноном — calendar_windows).
        val model = """{"meta": {"revision": 3},
            "rules": [
              {"id": "VN-030-T", "kind": "SOFT",
               "template_fit": {"category": "A",
                 "family": "layover_penalty", "registry_rev": "r"}},
              {"id": "VN-D91-T", "kind": "SOFT",
               "export_solver": {"family": "layover_penalty"},
               "template_fit": {"category": "A",
                 "family": "calendar_windows", "registry_rev": "r"}}]}"""
        val d = writePackage(
            tmp, archiveLanding1 = true, modelJson = model,
            pairingComponents =
                """[{"rule_id": "VN-030-T",
                     "title": "ночёвки", "weight": 10},
                    {"rule_id": "VN-D91-T",
                     "title": "пт-сб", "weight": 25}]""")
        val pkg = load(d, landingCompat = 1)
        assertEquals(10L, pkg.weights["Layover nights away from home"])
        assertEquals(25L, pkg.weights["Calendar window layover penalty"])
        assertEquals(false, pkg.unmappedRules.contains("VN-030-T"))
        assertEquals(false, pkg.unmappedRules.contains("VN-D91-T"))
    }

    @Test
    fun `семья продления мапит вес и отдаёт строжайший потолок`(@TempDir tmp: Path) {
        // Шаблон duty_extension_penalty (конвейер 2026-08-31): чужой
        // мир, другие числа — вес и потолок доезжают без правки кода;
        // два правила семьи — потолок строжайший (минимум).
        val model = """{"meta": {"revision": 3},
            "rules": [
              {"id": "REGLA-91", "kind": "SOFT",
               "export_solver": {"family": "duty_extension_penalty"},
               "params": [{"name": "max_duty_duration", "value": 800,
                 "unit": "duration_minutes", "relation": "max",
                 "as_written": "13:20"}]},
              {"id": "REGLA-92", "kind": "SOFT",
               "export_solver": {"family": "duty_extension_penalty"},
               "params": [{"name": "max_duty_duration", "value": 790,
                 "unit": "duration_minutes", "relation": "max",
                 "as_written": "13:10"}]}]}"""
        val d = writePackage(
            tmp, archiveLanding1 = true, modelJson = model,
            pairingComponents =
                """[{"rule_id": "REGLA-91",
                     "title": "prolongación", "weight": 7}]""")
        val pkg = load(d, landingCompat = 1)
        assertEquals(7L, pkg.weights["Duty extension over threshold"])
        assertEquals(false, pkg.unmappedRules.contains("REGLA-91"))
        assertEquals(790, pkg.dutyExtensionCapMin)
        assertEquals(listOf("REGLA-91", "REGLA-92"),
            pkg.dutyExtensionRules)
    }

    @Test
    fun `кривой потолок семьи продления - отказ словами`(@TempDir tmp: Path) {
        val model = """{"meta": {"revision": 3},
            "rules": [{"id": "R-X", "kind": "SOFT",
               "export_solver": {"family": "duty_extension_penalty"},
               "params": [{"name": "max_duty_duration", "value": 0,
                 "unit": "duration_minutes", "relation": "max",
                 "as_written": "0"}]}]}"""
        val d = writePackage(tmp, archiveLanding1 = true, modelJson = model)
        val e = org.junit.jupiter.api.assertThrows<TaskPackageError> {
            load(d, landingCompat = 1)
        }
        assertEquals(true, "duty_extension_penalty" in (e.message ?: ""))
    }

    @Test
    fun `неизвестная семья честно остаётся несоотнесённой`(@TempDir tmp: Path) {
        val model = """{"meta": {"revision": 3},
            "rules": [{"id": "R-ЧУЖОЙ-МИР-строка-9", "kind": "SOFT",
                       "export_solver": {"family": "не_из_словаря"}}]}"""
        val d = writePackage(
            tmp, archiveLanding1 = true, modelJson = model,
            pairingComponents =
                """[{"rule_id": "R-ЧУЖОЙ-МИР-строка-9",
                     "title": "ночёвки", "weight": 10}]""")
        val pkg = load(d, landingCompat = 1)
        assertEquals(true,
            pkg.unmappedRules.contains("R-ЧУЖОЙ-МИР-строка-9"))
    }

    @Test
    fun `форма календарных окон разбирается в факты`(@TempDir tmp: Path) {
        val d = writePackage(tmp, formsJson = """
            {"calendar_window_penalties": [
              {"days": ["friday", "saturday"], "tz": "base-local",
               "action": "layover_penalty", "weight": null,
               "rule": "R-S1-табл-3-строка-7-кол-3",
               "as_written": "проза"}]}
        """.trimIndent())
        val pkg = load(d)
        assertEquals(1, pkg.calendarWindows.size)
        val w = pkg.calendarWindows[0]
        assertEquals("R-S1-табл-3-строка-7-кол-3", w.ruleId)
        assertEquals("layover_penalty", w.action)
        assertEquals(setOf(java.time.DayOfWeek.FRIDAY,
            java.time.DayOfWeek.SATURDAY), w.days)
        // без файла форм — легаси: пусто, не отказ
        assertEquals(0, load(writePackage(tmp.resolve("l")
            .also { it.toFile().mkdirs() })).calendarWindows.size)
    }

    @Test
    fun `кривая форма окон - отказ словами`(@TempDir tmp: Path) {
        // день вне закрытого словаря имён (числом) — класс сдвига
        // на единицу закрыт по построению
        val bad = writePackage(tmp, formsJson = """
            {"calendar_window_penalties": [
              {"days": [5], "tz": "base-local",
               "action": "layover_penalty", "rule": "R-X"}]}
        """.trimIndent())
        val e1 = assertThrows<TaskPackageError> { load(bad) }
        assertTrue(e1.message!!.contains("словар"), e1.message)
        // tz не base-local — закрытая семантика формы
        val badTz = writePackage(tmp.resolve("t")
            .also { it.toFile().mkdirs() }, formsJson = """
            {"calendar_window_penalties": [
              {"days": ["friday"], "tz": "utc",
               "action": "layover_penalty", "rule": "R-X"}]}
        """.trimIndent())
        val e2 = assertThrows<TaskPackageError> { load(badTz) }
        assertTrue(e2.message!!.contains("base-local"), e2.message)
        // файл есть, но НЕ подписан в files — не применяется
        val unsigned = writePackage(tmp.resolve("u")
            .also { it.toFile().mkdirs() })
        File(unsigned, "threshold-forms.json").writeText(
            """{"calendar_window_penalties": []}""", Charsets.UTF_8)
        val e3 = assertThrows<TaskPackageError> { load(unsigned) }
        assertTrue(e3.message!!.contains("не объявлен"), e3.message)
    }

    private fun load(dir: File, landingCompat: Int? = null): LoadedTaskPackage =
        // Читаем каталог напрямую, минуя переменную окружения: её в
        // одном процессе не переключить (урок ResourceRules).
        TaskPackage.read(dir, landingCompat = landingCompat)

    // ------------------------------------------------------ приём пакета

    @Test
    fun `пакет из байтов запроса разбирается тем же парсером`(@TempDir tmp: Path) {
        val d = writePackage(tmp)
        val zip = tmp.resolve("p.zip").toFile()
        java.util.zip.ZipOutputStream(zip.outputStream()).use { z ->
            for (f in d.listFiles()!!) {
                z.putNextEntry(java.util.zip.ZipEntry(f.name))
                z.write(f.readBytes()); z.closeEntry()
            }
        }
        val fromFile = TaskPackage.read(zip)
        val fromBytes = TaskPackage.readBytes(zip.readBytes())
        assertEquals(fromFile.fingerprint, fromBytes.fingerprint)
        assertEquals(fromFile.weights, fromBytes.weights)
        // Мусор телом запроса — отказ словами разбора, не пустой пакет.
        val e = assertThrows<TaskPackageError> { TaskPackage.readBytes(ByteArray(16) { 7 }) }
        assertTrue(e.message!!.contains("zip"), e.message)
    }

    @Test
    fun `вложенные клаузы заморозки в пакете видны по полному и короткому имени`(@TempDir tmp: Path) {
        // Дополнение к 1.17 (06.09.2026): manifest.files называет клаузы
        // freeze/clauses/<sid>.json с путём; плоское чтение архива отказывало
        // «файл объявлен манифестом, но отсутствует в пакете».
        val d = writePackage(tmp)
        val clause = """{"clauses": [{"anchor": "S1-1", "text": "t"}]}""".toByteArray(Charsets.UTF_8)
        val mf = File(d, "manifest.json")
        mf.writeText(mf.readText(Charsets.UTF_8).replace(
            "\"files\": [",
            "\"files\": [\n {\"name\": \"freeze/clauses/S1.json\", \"sha256\": \"${sha256(clause)}\"},"),
            Charsets.UTF_8)
        val zip = tmp.resolve("p.zip").toFile()
        java.util.zip.ZipOutputStream(zip.outputStream()).use { z ->
            for (f in d.listFiles()!!) {
                z.putNextEntry(java.util.zip.ZipEntry(f.name))
                z.write(f.readBytes()); z.closeEntry()
            }
            z.putNextEntry(java.util.zip.ZipEntry("freeze/clauses/S1.json"))
            z.write(clause); z.closeEntry()
        }
        val fromBytes = TaskPackage.readBytes(zip.readBytes())
        val fromFile = TaskPackage.read(zip)
        assertEquals(fromFile.fingerprint, fromBytes.fingerprint)
        // карта членов: полное имя и короткое; файл корня сильнее вложенного
        val m = LinkedHashMap<String, ByteArray>()
        TaskPackage.putMember(m, "freeze/clauses/manifest.json", "nested".toByteArray())
        TaskPackage.putMember(m, "manifest.json", "root".toByteArray())
        TaskPackage.putMember(m, "freeze/clauses/S1.json", clause)
        assertEquals("root", String(m["manifest.json"]!!))
        assertEquals("nested", String(m["freeze/clauses/manifest.json"]!!))
        assertTrue(m["freeze/clauses/S1.json"]!!.contentEquals(clause))
        assertTrue(m["S1.json"]!!.contentEquals(clause))
    }

    @Test
    fun `кэш по содержимому видит правку файла на месте`(@TempDir tmp: Path) {
        // Ключ (путь, mtime, размер) лгал: mtime КАТАЛОГА на NTFS не меняется
        // правкой файла внутри — кэш отдавал устаревший пакет (разбор этапа Г).
        val d = writePackage(tmp)
        val first = TaskPackage.cachedByManifest(d)
        val mf = File(d, "manifest.json")
        mf.writeText(mf.readText(Charsets.UTF_8)
            .replace("\"revision\": 3", "\"revision\": 4"), Charsets.UTF_8)
        val second = TaskPackage.cachedByManifest(d)
        assertTrue(first.fingerprint != second.fingerprint,
            "правка манифеста на месте обязана смениться в кэше")
        assertEquals(4, second.revision)
        // Тот же манифест — из кэша (тот же объект).
        assertTrue(TaskPackage.cachedByManifest(d) === second)
    }

    @Test
    fun `наличие процессного пакета видно без чтения содержимого`(@TempDir tmp: Path) {
        // Гейт двусмысленности источников (этап Г, Ш-2 п.5) судит по факту
        // наличия: битый env-пакет обязан дать отказ словами двусмысленности,
        // а не упасть контрольной суммой при проверке наличия.
        val had = TaskPackage.processPackagePresent()
        val pkg = load(writePackage(tmp))
        TaskPackage.overrideForTests(pkg)
        assertTrue(TaskPackage.processPackagePresent())
        TaskPackage.overrideForTests(null)
        assertEquals(had, TaskPackage.processPackagePresent())
    }

    @Test
    fun `годный пакет принимается, веса и режимы прочитаны`(@TempDir tmp: Path) {
        val pkg = load(writePackage(tmp))
        assertEquals("1.0", pkg.specVersion)
        assertEquals("run-0025", pkg.sourceRun)
        assertEquals(3, pkg.revision)
        // Веса пришли ИЗ ПАКЕТА и разложены по именам ограничений.
        assertEquals(10L, pkg.weights["Layover nights away from home"])
        assertEquals(3L, pkg.weights["Credit hours spread over threshold"])
        // Правило без исполнителя названо, а не пропущено молча.
        assertEquals(listOf("R-НЕТ-ИСПОЛНИТЕЛЯ"), pkg.unmappedRules)
        assertTrue(pkg.componentsWithoutWeight.isEmpty())
        assertEquals(listOf("feasibility-hard", "feasibility-soft"),
            pkg.allowedModes["rostering"])
        assertTrue(pkg.hasData)
    }

    @Test
    fun `подмена байта в файле пакета — отказ по контрольной сумме`(@TempDir tmp: Path) {
        val d = writePackage(tmp)
        load(d)                       // контроль: до подмены пакет годен
        val obj = File(d, "objective.json")
        obj.writeText(obj.readText(Charsets.UTF_8).replace("\"weight\": 10",
            "\"weight\": 11"), Charsets.UTF_8)
        val e = assertThrows<TaskPackageError> { load(d) }
        assertTrue(e.message!!.contains("контрольная сумма"), e.message)
        assertTrue(e.message!!.contains("objective.json"), e.message)
    }

    @Test
    fun `подмена самого манифеста — тоже отказ`(@TempDir tmp: Path) {
        val d = writePackage(tmp)
        val before = load(d).fingerprint
        val mf = File(d, "manifest.json")
        // Подменён вес В МАНИФЕСТЕ? Его там нет — меняем заявленную сумму:
        // так выглядит попытка подсунуть свой файл под чужой манифест.
        mf.writeText(mf.readText(Charsets.UTF_8)
            .replace("\"revision\": 3", "\"revision\": 4"), Charsets.UTF_8)
        val after = load(d).fingerprint
        // Отпечаток — свойство манифеста: правка манифеста меняет подпись
        // постановки (условие МУ-04).
        assertTrue(before != after,
            "отпечаток обязан меняться вместе с манифестом")
    }

    @Test
    fun `неизвестная старшая версия спецификации — отказ`(@TempDir tmp: Path) {
        val d = writePackage(tmp, specVersion = "2.0")
        val e = assertThrows<TaskPackageError> { load(d) }
        assertTrue(e.message!!.contains("не поддержана"), e.message)
    }

    @Test
    fun `старшая версия та же, младшая выше — пакет принимается`(@TempDir tmp: Path) {
        // Спецификация §3: при известном MAJOR и большем MINOR читать
        // известные поля, неизвестные игнорировать.
        val pkg = load(writePackage(tmp, specVersion = "1.7"))
        assertEquals("1.7", pkg.specVersion)
        assertEquals(10L, pkg.weights["Layover nights away from home"])
    }

    @Test
    fun `объявленного манифестом файла нет в пакете — отказ`(@TempDir tmp: Path) {
        val d = writePackage(tmp)
        File(d, "model.json").delete()
        val e = assertThrows<TaskPackageError> { load(d) }
        assertTrue(e.message!!.contains("отсутствует в пакете"), e.message)
    }

    @Test
    fun `вес null при мягком режиме — компонент назван`(@TempDir tmp: Path) {
        val pkg = load(writePackage(tmp, weightBalance = "null"))
        assertEquals(listOf("R-S1-табл-4-строка-2-кол-3"),
            pkg.componentsWithoutWeight)
        assertNull(pkg.weights["Credit hours spread over threshold"])
    }

    // ------------------------------------------------------ гейты режима

    private fun pkgOf(dir: Path, vararg args: Pair<String, String>):
        LoadedTaskPackage {
        val m = args.toMap()
        return load(writePackage(dir,
            weightBalance = m["weightBalance"] ?: "3",
            // Mode tests require executable modern wire; archive reading is tested separately.
            specVersion = m["specVersion"] ?: "1.20",
            modelJson = """{"meta":{"revision":3},"rules":[]}""",
            rosteringModes = m["rosteringModes"]
                ?: """["feasibility-hard", "feasibility-soft"]""",
            rosteringObjectives = m["rosteringObjectives"] ?: "[]",
            manifestObjectives = m["manifestObjectives"]
                ?: """{"pairing": [], "rostering": []}""",
            ruleMaps = m["ruleMaps"]))
    }

    /**
     * Пакет с целевой в зоне ростеринга и разрешённым режимом
     * optimization: оснастка этапа Б. Метрика и направление задаются
     * машинными полями — прозу statement машина не разбирает.
     */
    private fun pkgWithObjective(
        dir: Path,
        id: String = "O-S1-табл-2-строка-2-кол-3-2",
        metric: String? = "credit_hours_spread",
        direction: String = "minimize",
        specVersion: String = "1.20",
        ruleMaps: String? = null,
    ): LoadedTaskPackage {
        val metricField = if (metric == null) "" else ""","metric": "$metric""""
        return load(writePackage(dir,
            specVersion = specVersion,
            modelJson = """{"meta":{"revision":3},"rules":[]}""",
            rosteringModes =
                """["feasibility-hard", "feasibility-soft", "optimization"]""",
            rosteringObjectives = """
                [{"id": "$id", "title": "разброс кредита",
                  "statement": "проза человеку; машина её не читает",
                  "direction": "$direction"$metricField}]
            """.trimIndent(),
            manifestObjectives =
                """{"pairing": [], "rostering": ["$id"]}""",
            ruleMaps = ruleMaps))
    }

    @Test
    fun `режим со штрафами применяет веса пакета`(@TempDir tmp: Path) {
        val r = TaskModes.resolve("feasibility-soft", pkgOf(tmp), "rostering")
        assertEquals(MODE_FEASIBILITY_SOFT, r.mode)
        assertNotNull(r.weightOverrides)
        assertEquals(10L, r.appliedWeights["Layover nights away from home"])
        assertEquals(3L, r.appliedWeights["Credit hours spread over threshold"])
        assertTrue(!r.stopAtFeasible)
    }

    @Test
    fun `режим допустимости останавливается на нуле жёстких и не штрафует`(
        @TempDir tmp: Path) {
        val r = TaskModes.resolve("feasibility-hard", pkgOf(tmp), "rostering")
        assertTrue(r.stopAtFeasible)
        // Веса пакета не применяются: в этом режиме штрафы не критерий.
        assertTrue(r.appliedWeights.isEmpty())
        // Ревизия литералов 2026-09-03: в пакетном прогоне мягкие литералы
        // профиля не действуют вовсе — все переопределены нулём
        // (прежде переопределений не было, и литералы входили в счёт).
        assertTrue(r.literalsDisabled.isNotEmpty())
        assertTrue(r.effectiveWeights.values.all { it == 0L })
        assertNotNull(r.weightOverrides)
    }

    @Test
    fun `режим вне allowed_modes — отказ с перечнем разрешённого`(
        @TempDir tmp: Path) {
        val pkg = pkgOf(tmp, "rosteringModes" to """["feasibility-hard"]""")
        // Контроль: разрешённый режим на том же пакете проходит.
        TaskModes.resolve("feasibility-hard", pkg, "rostering")
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("feasibility-soft", pkg, "rostering")
        }
        assertTrue(e.message!!.contains("не разрешён пакетом"), e.message)
        assertTrue(e.message!!.contains("feasibility-hard"), e.message)
    }

    // ------------------------------------------- этап Б: целевые функции

    @Test
    fun `целевая пакета исполняется, критерий складывается с штрафами`(
        @TempDir tmp: Path) {
        val pkg = pkgWithObjective(tmp)
        val r = TaskModes.resolve("optimization", pkg, "rostering",
            "O-S1-табл-2-строка-2-кол-3-2")
        val o = r.objective!!
        assertEquals("credit_hours_spread", o.metric)
        assertEquals("minimize", o.direction)
        assertEquals("Credit hours spread", o.constraint)
        // Legacy-носитель rate отсутствует: сохраняется единичная цена.
        // Ярус важности и проза statement не являются источником ставки.
        assertEquals(1L, o.weight)
        assertTrue(o.executorSource.contains("словарь"), o.executorSource)
        // Прочтение заявлено: целые кредитные часы по всему штату.
        assertNotNull(o.approximation)
        assertTrue(o.approximation!!.contains("ПРОЧТЕНИЕ"), o.approximation)
        // Решение владельца 2026-09-03: целевая берёт разброс целиком,
        // правило — превышение порога; это ДВА ограничения, критерий =
        // ставка целевой 1 + коэффициент правила 3 по своим строкам.
        assertTrue(!o.sharesConstraintWithPenalty)
        assertEquals(1L, r.effectiveWeights["Credit hours spread"])
        assertEquals(3L, r.effectiveWeights["Credit hours spread over threshold"])
        // Штраф зоны остался собой — сумма не съела слагаемые.
        assertEquals(3L, r.appliedWeights["Credit hours spread over threshold"])
        assertNull(r.appliedWeights["Credit hours spread"])
        // Ограничение, которого целевая не касается, не сдвинулось.
        assertEquals(10L, r.effectiveWeights["Layover nights away from home"])
        assertNotNull(r.weightOverrides)
    }

    @Test
    fun `режим оптимизации без явной целевой — отказ`(@TempDir tmp: Path) {
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", pkgWithObjective(tmp), "rostering")
        }
        assertTrue(e.message!!.contains("objective_id"), e.message)
        // Контроль: с целевой тот же пакет проходит.
        assertNotNull(TaskModes.resolve("optimization", pkgWithObjective(tmp),
            "rostering", "O-S1-табл-2-строка-2-кол-3-2").objective)
    }

    @Test
    fun `optimization при пустом перечне целевых — алерт с доступными режимами`(
        @TempDir tmp: Path) {
        // Решение владельца 2026-08-26: без критерия оптимизации задача
        // решается только как feasibility/soft — отказ обязан назвать это.
        val pkg = load(writePackage(tmp,
            specVersion = "1.20",
            modelJson = """{"meta":{"revision":3},"rules":[]}""",
            rosteringModes =
                """["feasibility-hard", "feasibility-soft", "optimization"]"""))
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", pkg, "rostering")
        }
        assertTrue(e.message!!.contains("критерий оптимизации не задан"), e.message)
        assertTrue(e.message!!.contains("feasibility-soft"), e.message)
    }

    @Test
    fun `чужая целевая — отказ с перечнем объявленных`(@TempDir tmp: Path) {
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", pkgWithObjective(tmp),
                "rostering", "O-ЧУЖАЯ")
        }
        assertTrue(e.message!!.contains("не объявлена пакетом"), e.message)
    }

    @Test
    fun `метрика вне словаря — отказ, а не похожая величина`(
        @TempDir tmp: Path) {
        val pkg = pkgWithObjective(tmp, id = "O-НОВАЯ",
            metric = "crew_happiness_index")
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", pkg, "rostering", "O-НОВАЯ")
        }
        assertTrue(e.message!!.contains("вне известного словаря"), e.message)
        assertTrue(e.message!!.contains("credit_hours_spread"), e.message)
    }

    @Test
    fun `метрика не применима задаче — отказ с названием зоны`(
        @TempDir tmp: Path) {
        // total_pairing_cost вычислима только раздельным прогоном пейринга.
        val pkg = pkgWithObjective(tmp, id = "O-СТОИМОСТЬ",
            metric = "total_pairing_cost")
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", pkg, "rostering", "O-СТОИМОСТЬ")
        }
        assertTrue(e.message!!.contains("раздельным прогоном пейринга"),
            e.message)
    }

    @Test
    fun `направление maximize у минимизируемой величины — отказ`(
        @TempDir tmp: Path) {
        val pkg = pkgWithObjective(tmp, direction = "maximize")
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", pkg, "rostering",
                "O-S1-табл-2-строка-2-кол-3-2")
        }
        assertTrue(e.message!!.contains("maximize"), e.message)
    }

    @Test
    fun `целевая без метрики идёт таблицей соответствий`(@TempDir tmp: Path) {
        // Словарь потребителя может быть старее пакета — запасной путь
        // спецификации §5а. Догадки при этом всё равно нет: соответствие
        // задано явно.
        val pkg = pkgWithObjective(tmp, metric = null)
        val o = TaskModes.resolve("optimization", pkg, "rostering",
            "O-S1-табл-2-строка-2-кол-3-2").objective!!
        assertNull(o.metric)
        assertEquals("Credit hours spread", o.constraint)
        assertTrue(o.executorSource.contains("таблица"), o.executorSource)
        // Контроль: целевой, которой нет в таблице, исполнителя не нашлось.
        val ne = pkgWithObjective(tmp, id = "O-БЕЗ-ВСЕГО", metric = null)
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", ne, "rostering", "O-БЕЗ-ВСЕГО")
        }
        assertTrue(e.message!!.contains("исполнителя не существует"), e.message)
    }

    @Test
    fun `оптимизация без пакета — отказ, целевую взять неоткуда`() {
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", null, "rostering", "O-ЛЮБАЯ")
        }
        assertTrue(e.message!!.contains("требует пакета"), e.message)
    }

    @Test
    fun `целевая попадает в отчёт применённого вместе с приближением`(
        @TempDir tmp: Path) {
        val r = TaskModes.resolve("optimization", pkgWithObjective(tmp),
            "rostering", "O-S1-табл-2-строка-2-кол-3-2")
        LastRun.remember(r)
        @Suppress("UNCHECKED_CAST")
        val o = LastRun.snapshot()!!["objective"] as Map<String, Any?>
        assertEquals("O-S1-табл-2-строка-2-кол-3-2", o["id"])
        assertEquals("credit_hours_spread", o["metric"])
        assertEquals("Credit hours spread", o["constraint"])
        assertEquals(false, o["sharesConstraintWithPenalty"])   // два ограничения с 03.09
        assertNotNull(o["approximation"])
        assertTrue((o["criterion"] as String).contains("Σ штрафов"))
    }

    @Test
    fun `неизвестный режим — отказ, а не тихое умолчание`(@TempDir tmp: Path) {
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("побыстрее", pkgOf(tmp), "rostering")
        }
        assertTrue(e.message!!.contains("неизвестный режим"), e.message)
    }

    @Test
    fun `компонент без веса при мягком режиме — отказ, при жёстком — нет`(
        @TempDir tmp: Path) {
        val pkg = pkgOf(tmp, "weightBalance" to "null")
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("feasibility-soft", pkg, "rostering")
        }
        assertTrue(e.message!!.contains("без подтверждённого веса"), e.message)
        // Контроль: в режиме допустимости штрафы не нужны — прогон законен.
        val ok = TaskModes.resolve("feasibility-hard", pkg, "rostering")
        assertTrue(ok.stopAtFeasible)
    }

    @Test
    fun `без пакета поведение прежнее — веса из кода`(@TempDir tmp: Path) {
        val r = TaskModes.resolve(null, null, "rostering")
        assertEquals(MODE_FEASIBILITY_SOFT, r.mode)
        assertNull(r.weightOverrides)
        assertNull(r.fingerprint)
        assertTrue(r.appliedWeights.isEmpty())
    }

    // -------------------------------------------- гейт применённого

    @Test
    fun `последний прогон отчитывается применённым, а не намерением`(
        @TempDir tmp: Path) {
        assertNull(LastRun.snapshot(), "до прогона отчитываться нечем")
        val r = TaskModes.resolve("feasibility-soft", pkgOf(tmp), "rostering")
        LastRun.remember(r)
        val snap = LastRun.snapshot()!!
        assertEquals(MODE_FEASIBILITY_SOFT, snap["mode"])
        @Suppress("UNCHECKED_CAST")
        val w = snap["appliedWeights"] as Map<String, Long>
        assertEquals(10L, w["Layover nights away from home"])
        assertEquals(3L, w["Credit hours spread over threshold"])
        assertNotNull(snap["packageFingerprint"])
        assertEquals(listOf("R-НЕТ-ИСПОЛНИТЕЛЯ"), snap["rulesWithoutExecutor"])
    }

    // --------------------------- секция rule_maps (ревизия 1.14)

    @Test
    fun `карта правил пакета сильнее зашитой таблицы`(@TempDir tmp: Path) {
        // Подсекция rule_to_constraint есть — веса раскладываются ТОЛЬКО
        // по ней: прежде безысполнительное правило получает исполнителя
        // из пакета, а правила зашитой таблицы, которых в карте пакета
        // нет, честно уходят в безысполнительные (верховенство пакета,
        // легаси-таблица при живой подсекции не читается вовсе).
        val pkg = load(writePackage(tmp, specVersion = "1.14",
            ruleMaps = """{"rule_to_constraint":
                {"R-НЕТ-ИСПОЛНИТЕЛЯ": "Layover nights away from home"}}"""))
        assertEquals(mapOf("Layover nights away from home" to 7L), pkg.weights)
        assertEquals(listOf("R-S1-табл-3-строка-6-кол-3",
            "R-S1-табл-4-строка-2-кол-3"), pkg.unmappedRules)
        // Карта целевых пакетом не объявлена — для TaskModes легаси-ветка.
        assertNull(pkg.objectiveToConstraint)
    }

    @Test
    fun `без секции rule_maps действуют зашитые таблицы — легаси-ветка`(
        @TempDir tmp: Path) {
        // Легаси-путь закреплён отдельно: пакет 1.14 БЕЗ секции читается
        // прежними зашитыми таблицами байт-в-байт со старым поведением.
        // Снятие таблиц — отдельный шаг после переиздания живых пакетов.
        val pkg = load(writePackage(tmp, specVersion = "1.14"))
        assertEquals(10L, pkg.weights["Layover nights away from home"])
        assertEquals(3L, pkg.weights["Credit hours spread over threshold"])
        assertEquals(listOf("R-НЕТ-ИСПОЛНИТЕЛЯ"), pkg.unmappedRules)
        assertNull(pkg.objectiveToConstraint)
    }

    @Test
    fun `секция rule_maps в пакете до 1_14 — отказ расхождения форм`(
        @TempDir tmp: Path) {
        val d = writePackage(tmp, specVersion = "1.13",
            ruleMaps = """{"rule_to_constraint": {"R-СИНТ": "Credit hours spread over threshold"}}""")
        val e = assertThrows<TaskPackageError> { load(d) }
        assertTrue(e.message!!.contains("1.14"), e.message)
        // Контроль: та же секция при spec_version 1.14 принимается.
        assertNotNull(load(writePackage(tmp, specVersion = "1.14",
            ruleMaps = """{"rule_to_constraint": {"R-СИНТ": "Credit hours spread over threshold"}}""")))
    }

    @Test
    fun `кривая секция rule_maps — отказ словами`(@TempDir tmp: Path) {
        // Секция не объект.
        var e = assertThrows<TaskPackageError> {
            load(writePackage(tmp, specVersion = "1.14", ruleMaps = "[]"))
        }
        assertTrue(e.message!!.contains("rule_maps"), e.message)
        // Пустая секция: гейт производителя такую не выпускает.
        e = assertThrows<TaskPackageError> {
            load(writePackage(tmp, specVersion = "1.14", ruleMaps = "{}"))
        }
        assertTrue(e.message!!.contains("rule_maps"), e.message)
        // Подсекция не объект.
        e = assertThrows<TaskPackageError> {
            load(writePackage(tmp, specVersion = "1.14",
                ruleMaps = """{"rule_to_constraint": ["R-СИНТ"]}"""))
        }
        assertTrue(e.message!!.contains("rule_to_constraint"), e.message)
        // Значение записи не строка.
        e = assertThrows<TaskPackageError> {
            load(writePackage(tmp, specVersion = "1.14",
                ruleMaps = """{"objective_to_constraint": {"O-СИНТ": 5}}"""))
        }
        assertTrue(e.message!!.contains("objective_to_constraint"), e.message)
        // Значение записи — пустая строка.
        e = assertThrows<TaskPackageError> {
            load(writePackage(tmp, specVersion = "1.14",
                ruleMaps = """{"rule_to_constraint": {"R-СИНТ": ""}}"""))
        }
        assertTrue(e.message!!.contains("непустой строкой"), e.message)
        // Контроль: годная секция на том же пакете принимается; подсекции
        // других потребителей (crew_size_rules и т.п.) котлин не судит —
        // их формы проверяют их читатели.
        val pkg = load(writePackage(tmp, specVersion = "1.14",
            ruleMaps = """{"crew_size_rules": ["R-СИНТ-Ч"],
                "rule_to_constraint": {"R-СИНТ": "Credit hours spread over threshold"}}"""))
        assertNotNull(pkg)
    }

    @Test
    fun `целевая без метрики решается картой пакета`(@TempDir tmp: Path) {
        // Подсекция objective_to_constraint есть — исполнитель берётся
        // ИЗ ПАКЕТА, источник в отчёте назван картой пакета.
        val pkg = pkgWithObjective(tmp, id = "O-СИНТ", metric = null,
            specVersion = "1.20",
            ruleMaps = """{"objective_to_constraint": {"O-СИНТ": "Credit hours spread"}}""")
        val o = TaskModes.resolve("optimization", pkg, "rostering", "O-СИНТ")
            .objective!!
        assertEquals("Credit hours spread", o.constraint)
        assertNull(o.metric)
        assertTrue(o.executorSource.contains("карта пакета"), o.executorSource)
        // Контроль верховенства: та же целевая БЕЗ секции — прежний отказ
        // легаси-таблицы, которая её не знает.
        val legacy = pkgWithObjective(tmp, id = "O-СИНТ", metric = null)
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", legacy, "rostering", "O-СИНТ")
        }
        assertTrue(e.message!!.contains("исполнителя не существует"), e.message)
    }

    @Test
    fun `карта целевых пакета сильнее зашитой таблицы целевых`(
        @TempDir tmp: Path) {
        // Целевая из зашитой таблицы, которой НЕТ в карте пакета, —
        // отказ: при живой подсекции легаси-таблица не читается вовсе.
        val pkg = pkgWithObjective(tmp, metric = null, specVersion = "1.20",
            ruleMaps = """{"objective_to_constraint": {"O-СИНТ": "Credit hours spread"}}""")
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("optimization", pkg, "rostering",
                "O-S1-табл-2-строка-2-кол-3-2")
        }
        assertTrue(e.message!!.contains("исполнителя не существует"), e.message)
    }

    @Test
    fun `метрика вне словаря идёт картой пакета, когда та есть`(
        @TempDir tmp: Path) {
        val pkg = pkgWithObjective(tmp, id = "O-НОВАЯ",
            metric = "crew_happiness_index", specVersion = "1.20",
            ruleMaps = """{"objective_to_constraint": {"O-НОВАЯ": "Credit hours spread"}}""")
        val o = TaskModes.resolve("optimization", pkg, "rostering", "O-НОВАЯ")
            .objective!!
        assertEquals("Credit hours spread", o.constraint)
        assertEquals("crew_happiness_index", o.metric)
        assertTrue(o.executorSource.contains("карта пакета"), o.executorSource)
        assertTrue(o.executorSource.contains("вне словаря"), o.executorSource)
    }

    // ------------------------- порог разброса кредитных часов (семья balanced_workload)

    @Test
    fun `порог разброса читается из параметра правила и ложится в окно порогов`(
        @TempDir tmp: Path) {
        // Правило ВМП H0 VN-050: «сверх порога существенности 10
        // кредитных часов за месяц» — CDPM извлекает параметр
        // workload_spread_threshold = 10 (часы); решатель кладёт его в
        // окно порогов минутами.
        val model = """{"meta": {"revision": 3},
            "rules": [{"id": "VN-050", "kind": "SOFT",
                       "export_solver": {"family": "balanced_workload"},
                       "params": [{"name": "workload_spread_threshold", "value": 10,
                                   "unit": "credit_hours", "relation": "max",
                                   "as_written": "10 кредитных часов"}]}]}"""
        val pkg = load(writePackage(tmp, archiveLanding1 = true, modelJson = model), landingCompat = 1)
        assertEquals(600, pkg.workloadSpreadThresholdMin)
        assertEquals(listOf("VN-050"), pkg.workloadSpreadRules)
        val res = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        val a = res.applied[RunThresholds.WORKLOAD_SPREAD_THRESHOLD]!!
        assertEquals(600, a.value)
        assertTrue(a.declarations.any { it.contains("превышение") }, a.declarations.toString())
    }

    @Test
    fun `правило семьи без порога - порог 0, разные пороги - отказ`(
        @TempDir tmp: Path) {
        val noParam = """{"meta": {"revision": 3},
            "rules": [{"id": "VN-X", "kind": "SOFT",
                       "export_solver": {"family": "balanced_workload"}}]}"""
        val pkg = load(writePackage(tmp, archiveLanding1 = true, modelJson = noParam), landingCompat = 1)
        assertEquals(0, pkg.workloadSpreadThresholdMin)
        val a = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
            .applied[RunThresholds.WORKLOAD_SPREAD_THRESHOLD]!!
        assertTrue(a.declarations.any { it.contains("весь разброс") }, a.declarations.toString())
        // Пакета без семьи — окно порог не несёт вовсе.
        assertNull(load(writePackage(tmp)).workloadSpreadThresholdMin)
        val twoRules = """{"meta": {"revision": 3},
            "rules": [{"id": "VN-A", "kind": "SOFT",
                       "export_solver": {"family": "balanced_workload"},
                       "params": [{"name": "workload_spread_threshold", "value": 10}]},
                      {"id": "VN-B", "kind": "SOFT",
                       "export_solver": {"family": "balanced_workload"},
                       "params": [{"name": "workload_spread_threshold", "value": 5}]}]}"""
        val e = assertThrows<TaskPackageError> {
            load(writePackage(tmp, archiveLanding1 = true, modelJson = twoRules), landingCompat = 1)
        }
        assertTrue(e.message!!.contains("расходятся в пороге"), e.message)
        val negative = """{"meta": {"revision": 3},
            "rules": [{"id": "VN-N", "kind": "SOFT",
                       "export_solver": {"family": "balanced_workload"},
                       "params": [{"name": "workload_spread_threshold", "value": -1}]}]}"""
        val e2 = assertThrows<TaskPackageError> {
            load(writePackage(tmp, archiveLanding1 = true, modelJson = negative), landingCompat = 1)
        }
        assertTrue(e2.message!!.contains("часов ≥ 0"), e2.message)
    }
}
