package org.acme.flighcrewscheduling.solver

import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

/**
 * Перенос порогов пакета в прогон (этап В, пункт «г»), ревизия 4:
 * две ручки на стыковку и на отдых (в базе / вне базы), исполнитель
 * посадок за смену, ТОЧНОЕ сопоставление уточнителя со словарём
 * областей, суженное требование ко всей ручке только как ужесточение,
 * литерал-выключатель заявляется включением. Каждая проверка закрывает
 * класс, а не экземпляр.
 */
class RunThresholdsTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun p(name: String, value: Double?, relation: String,
                  unit: String = "duration_minutes",
                  rule: String = "R-1", qualifier: String? = null) =
        RuleParam(name, value, value?.toString() ?: "null", unit, relation,
            qualifier, "", rule, "HARD")

    private val D = TaskModes.PROFILE_DEFAULT

    private fun decls(r: RunThresholds.Resolution, threshold: String) =
        r.applied[threshold]!!.declarations

    @Test
    fun `сутки пейринга передаются построителю со строжайшим порогом и честным исполнителем`() {
        for (days in listOf(1.0, 3.0)) {
            val r = RunThresholds.resolve(listOf(
                p("max_pairing_days", days, "max", unit = "count", rule = "R-DAYS"),
                p("max_pairing_days", days + 2, "max", unit = "count", rule = "R-WIDER")), D)
            val a = r.applied.getValue("max_pairing_days")
            assertEquals(days.toInt(), a.value)
            assertEquals(listOf("R-DAYS", "R-WIDER"), a.rules)
            assertTrue(a.declarations.any { it.contains("pairing builder") && it.contains("не ограничение Kotlin") })
            assertTrue(r.ignored.isEmpty())
            @Suppress("UNCHECKED_CAST")
            val rows = RunThresholds.snapshot(r)["applied"] as List<Map<String, Any?>>
            assertEquals("pairing-builder", rows.single()["executor"])
        }
        assertFalse(RunThresholds.resolve(emptyList(), D).applied.containsKey("max_pairing_days"))
    }

    @Test
    fun `кривые сутки пейринга не становятся переданным порогом`() {
        for (days in listOf(null, 0.0, -1.0, 1.5)) {
            assertThrows<TaskModeRefused> {
                RunThresholds.resolve(listOf(p("max_pairing_days", days, "max", unit = "count")), D)
            }
        }
        val scoped = RunThresholds.resolve(listOf(
            p("max_pairing_days", 1.0, "max", unit = "count", qualifier = "с согласия")), D)
        assertTrue(scoped.applied.isEmpty())
        assertEquals(1, scoped.ignored.size)
    }

    @Test
    fun `порог пакета замещает литерал и замещение ЗАЯВЛЯЕТСЯ`() {
        val r = RunThresholds.resolve(listOf(p("max_duty_duration", 720.0, "max")), D)
        val a = r.applied["max_duty_span_min"]!!
        assertEquals(720, a.value)
        assertEquals(DefaultProviderRules.MAX_DAILY_DUTY_MINUTES, a.literal)
        assertTrue(a.declarations.any { it.contains("ЗАМЕЩЕНИЕ") && it.contains("УЖЕСТОЧЕНИЕ") })
    }

    // ---------------- семья duty_extension_penalty (шаблон 2026-08-31)

    @Test
    fun `семья продления поднимает грань до потолка и заявляет мягкий порог`() {
        val base = RunThresholds.resolve(
            listOf(p("max_duty_duration", 720.0, "max")), D)
        val r = RunThresholds.applyDutyExtension(base, 780, listOf("R-EXT"))
        assertEquals(780, r.applied["max_duty_span_min"]!!.value)
        assertEquals(720,
            r.applied[RunThresholds.DUTY_EXTENSION_THRESHOLD]!!.value)
        assertTrue(decls(r, "max_duty_span_min")
            .any { it.contains("duty_extension_penalty") && it.contains("R-EXT") })
        assertTrue(r.applied[RunThresholds.DUTY_EXTENSION_THRESHOLD]!!
            .rules.contains("R-EXT"))
    }

    @Test
    fun `семья продления без потолка или без базового предела не трогает пороги`() {
        val base = RunThresholds.resolve(
            listOf(p("max_duty_duration", 720.0, "max")), D)
        assertEquals(base,
            RunThresholds.applyDutyExtension(base, null, emptyList()))
        // Потолок не строже базы — продление ничего не продлевает,
        // молчаливое ослабление жёсткой грани запрещено.
        assertEquals(base,
            RunThresholds.applyDutyExtension(base, 720, listOf("R-EXT")))
        // Базового предела нет — мягкого порога не существует.
        val empty = RunThresholds.resolve(emptyList(), D)
        assertEquals(empty,
            RunThresholds.applyDutyExtension(empty, 780, listOf("R-EXT")))
    }

    @Test
    fun `ослабление литерала тоже заявляется`() {
        val r = RunThresholds.resolve(listOf(p("max_duty_duration", 900.0, "max")), D)
        assertTrue(decls(r, "max_duty_span_min").any { it.contains("ОСЛАБЛЕНИЕ") })
    }

    @Test
    fun `отдых в базе и вне базы — ДВЕ ручки, сведения больше нет`() {
        val r = RunThresholds.resolve(listOf(
            p("min_rest_home", 720.0, "min"),
            p("min_rest_outstation", 600.0, "min", rule = "R-2")), D)
        assertEquals(720, r.applied["min_rest_home_min"]!!.value)
        assertEquals(600, r.applied["min_rest_outstation_min"]!!.value)
        assertFalse(r.applied.values.any { a -> a.declarations.any { it.contains("СВЕДЕНО") } })
        assertTrue(r.ignored.isEmpty())
    }

    @Test
    fun `стыковка с уточнителем «база» метит в домашнюю ручку и применяется`() {
        // 45 меньше литерала 60 — в СВОЕЙ области это законное ослабление:
        // ручка домашняя, внебазовая остаётся 60.
        val r = RunThresholds.resolve(listOf(
            p("min_turnaround", 45.0, "min", qualifier = "база")), D)
        assertEquals(45, r.applied["min_sit_home_min"]!!.value)
        assertFalse(r.applied.containsKey("min_sit_outstation_min"))
        assertTrue(decls(r, "min_sit_home_min").any {
            it.startsWith("ОБЛАСТЬ") && it.contains("область различена") })
        assertTrue(r.ignored.isEmpty())
    }

    @Test
    fun `«в базе» и «вне базы» разводятся по разным ручкам`() {
        val r = RunThresholds.resolve(listOf(
            p("min_turnaround", 45.0, "min", qualifier = "в базе"),
            p("min_turnaround", 90.0, "min", qualifier = "вне базы", rule = "R-2")), D)
        assertEquals(45, r.applied["min_sit_home_min"]!!.value)
        assertEquals(90, r.applied["min_sit_outstation_min"]!!.value)
    }

    @Test
    fun `стыковка без уточнителя — требование ко всей сети, обе ручки`() {
        val r = RunThresholds.resolve(listOf(p("min_turnaround", 50.0, "min")), D)
        assertEquals(50, r.applied["min_sit_home_min"]!!.value)
        assertEquals(50, r.applied["min_sit_outstation_min"]!!.value)
    }

    @Test
    fun `уточнитель нормализуется по пробелам и регистру, но не угадывается`() {
        // Лишние пробелы (в том числе неразрывный) и регистр — не смысл.
        val ok = RunThresholds.resolve(listOf(
            p("min_turnaround", 45.0, "min", qualifier = "  В базе ")), D)
        assertEquals(45, ok.applied["min_sit_home_min"]!!.value)
        // Латинская «a» в слове «базa» — другое слово: в словаре его нет.
        val no = RunThresholds.resolve(listOf(
            p("min_turnaround", 45.0, "min", qualifier = "базa")), D)
        assertTrue(no.applied.isEmpty())
        assertEquals(1, no.ignored.size)
    }

    @Test
    fun `сопоставление ТОЧНОЕ, а не по префиксу - «база PLY» не домашняя ручка`() {
        // Ревизия 3 брала startsWith: «база PLY» и «база SVG» сливались в
        // домашнюю ручку, код базы выбрасывался молча. Теперь это сужение
        // неизвестного рода: 45 и 60 против литерала 60 — не ужесточение,
        // обе записи не применены и названы.
        val r = RunThresholds.resolve(listOf(
            p("min_turnaround", 45.0, "min", qualifier = "база PLY"),
            p("min_turnaround", 60.0, "min", qualifier = "база SVG", rule = "R-2")), D)
        assertTrue(r.applied.isEmpty())
        assertEquals(setOf("R-1", "R-2"), r.ignored.flatMap { it.rules }.toSet())
        assertTrue(r.ignored.all { it.reason.contains("сужает область") }, r.ignored.toString())
    }

    @Test
    fun `«в базе и вне базы» — не «в базе» - требование на обе области не сужается до дома`() {
        val loosening = RunThresholds.resolve(listOf(
            p("min_turnaround", 45.0, "min", qualifier = "в базе и вне базы")), D)
        assertTrue(loosening.applied.isEmpty())
        // Ужесточение — ко всей ручке (обе области), и это заявлено.
        val tightening = RunThresholds.resolve(listOf(
            p("min_turnaround", 90.0, "min", qualifier = "в базе и вне базы")), D)
        assertEquals(90, tightening.applied["min_sit_home_min"]!!.value)
        assertEquals(90, tightening.applied["min_sit_outstation_min"]!!.value)
        assertTrue(decls(tightening, "min_sit_home_min").any { it.contains("ко ВСЕЙ ручке") })
    }

    @Test
    fun `чужая область на ручке без областей не применяется вовсе`() {
        // ПЕРЕВЁРНУТО 2026-08-25 (решение обеих сторон, канон ревизии 4
        // «уточнитель точно по словарю»): прежнее «влить как ужесточение»
        // превращало правило чужой области в пере-ограничение всей ручки —
        // на run-0050 «резерв» ≤600 пере-ограничил лётные смены (−13485
        // hard законному решению). Теперь суженная область, которую ручка
        // не различает, НЕ применяется ни ужесточением, ни ослаблением —
        // правило честно ложится в ignored с причиной.
        for (v in listOf(780.0, 900.0)) {
            val r = RunThresholds.resolve(listOf(
                p("max_duty_duration", v, "max", qualifier = "с согласия")), D)
            assertTrue(r.applied.isEmpty(), "значение $v не должно применяться")
            val reason = r.ignored.single().reason
            assertTrue(reason.contains("этой области не различает") &&
                reason.contains("пере-ограничение"), reason)
        }
        // «резерв» — тот самый случай run-0050: длительность резервной
        // смены не пере-ограничивает лётные.
        val res = RunThresholds.resolve(listOf(
            p("max_duty_duration", 720.0, "max"),
            p("max_duty_duration", 600.0, "max", qualifier = "резерв")), D)
        assertEquals(720, res.applied["max_duty_span_min"]!!.value)
        assertTrue(res.ignored.single().reason.contains("этой области не различает"))
    }

    @Test
    fun `уточнитель, согласный с областью в имени отдыха, применяется в свою область`() {
        val r = RunThresholds.resolve(listOf(
            p("min_rest_home", 900.0, "min", qualifier = "в базе")), D)
        assertEquals(900, r.applied["min_rest_home_min"]!!.value)
        assertTrue(decls(r, "min_rest_home_min").any { it.contains("область различена") })
    }

    @Test
    fun `уточнитель, противоречащий области в имени отдыха, - не применено и названо`() {
        val r = RunThresholds.resolve(listOf(
            p("min_rest_home", 900.0, "min", qualifier = "вне базы")), D)
        assertTrue(r.applied.isEmpty())
        assertTrue(r.ignored.single().reason.contains("противоречит области"),
            r.ignored.single().reason)
    }

    @Test
    fun `посадки за смену - ВКЛЮЧЕНИЕ выключенного правила, а не «ослабление» нуля`() {
        val r = RunThresholds.resolve(listOf(
            p("max_landings_per_duty", 6.0, "max", unit = "count")), D)
        val a = r.applied["max_landings_per_duty"]!!
        assertEquals(6, a.value)
        assertEquals(DefaultProviderRules.MAX_LANDINGS_PER_DUTY_OFF, a.literal)
        assertTrue(a.declarations.any { it.startsWith("ВКЛЮЧЕНИЕ") && it.contains("УЖЕСТОЧЕНИЕ") },
            a.declarations.toString())
        assertFalse(a.declarations.any { it.contains("ОСЛАБЛЕНИЕ") })
        assertTrue(r.ignored.isEmpty())
    }

    @Test
    fun `ОБЛАСТЬ заявляется один раз на уточнитель с перечнем правил`() {
        val r = RunThresholds.resolve(listOf(
            p("min_turnaround", 45.0, "min", qualifier = "база"),
            p("min_turnaround", 45.0, "min", qualifier = "база", rule = "R-2")), D)
        val area = decls(r, "min_sit_home_min").filter { it.startsWith("ОБЛАСТЬ") }
        assertEquals(1, area.size, area.toString())
        assertTrue(area[0].contains("R-1, R-2"), area[0])
    }

    @Test
    fun `одинаковые причины неприменения сводятся в одну запись с перечнем правил`() {
        val r = RunThresholds.resolve(listOf(
            p("crew_happiness", 2.0, "eq", unit = "count"),
            p("crew_happiness", 2.0, "eq", unit = "count", rule = "R-2")), D)
        assertTrue(r.applied.isEmpty())
        val ig = r.ignored.single()
        assertEquals(listOf("R-1", "R-2"), ig.rules)
        assertTrue(ig.reason.contains("не известно таблице"))
    }

    @Test
    fun `ноль, отрицательное и дробное у ручки — отказ`() {
        for (bad in listOf(0.0, -10.0, 12.5)) {
            val e = assertThrows<TaskModeRefused> {
                RunThresholds.resolve(listOf(p("max_duty_duration", bad, "max")), D)
            }
            assertTrue(e.message!!.contains("целым положительным"), e.message)
        }
    }

    @Test
    fun `сдвиг шкалы - ноль законен, заявлен прочтением, ненулевой и дробный - отказ`() {
        // Линия нано 2026-08-29: «все времена — местное время главной
        // базы» приходит параметром base_tz_offset_min = 0. Ноль — не
        // выключатель и не «пустой порог», а значение шкалы мира.
        val r = RunThresholds.resolve(listOf(
            p("base_tz_offset_min", 0.0, "eq")), D)
        val a = r.applied["base_tz_offset_min"]!!
        assertEquals(0, a.value)
        assertEquals(0, a.literal)
        assertTrue(a.declarations.any { it.contains("совпадает с литералом") })
        assertTrue(a.declarations.any {
            it.contains("ПРОЧТЕНИЕ") && it.contains("шкал") }, a.declarations.toString())
        // Ненулевой сдвиг механикой не исполняется — честный отказ, не
        // молчаливое применение к датам, которые никто не сдвигает.
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.resolve(listOf(p("base_tz_offset_min", 120.0, "eq")), D)
        }.message!!.contains("ненулевой сдвиг механикой не исполняется"))
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.resolve(listOf(p("base_tz_offset_min", 0.5, "eq")), D)
        }.message!!.contains("целым числом минут"))
        // Отношение и единица закрыты, как у любой ручки.
        assertThrows<TaskModeRefused> {
            RunThresholds.resolve(listOf(p("base_tz_offset_min", 0.0, "min")), D)
        }
    }

    @Test
    fun `дробное значение у параметра, который решатель не применяет, — не отказ`() {
        val r = RunThresholds.resolve(listOf(p("duty_rig", 2.5, "min",
            unit = "hours_per_credit_hour")), D)
        assertTrue(r.applied.isEmpty())
        assertEquals(1, r.ignored.size)
    }

    @Test
    fun `отношение или единица не совпадают с ручкой — отказ`() {
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.resolve(listOf(p("max_duty_duration", 720.0, "min")), D)
        }.message!!.contains("правило-минимум как максимум"))
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.resolve(listOf(p("max_duty_duration", 720.0, "max", unit = "count")), D)
        }.message!!.contains("ждёт duration_minutes"))
    }

    @Test
    fun `любой из двух отдыхов не выше порога разрыва дня — отказ`() {
        for (name in listOf("min_rest_home", "min_rest_outstation")) {
            val e = assertThrows<TaskModeRefused> {
                RunThresholds.resolve(listOf(p(name,
                    DefaultProviderRules.DAY_BREAK_THRESHOLD_MIN.toDouble(), "min")), D)
            }
            assertTrue(e.message!!.contains("перестало бы действовать молча"), e.message)
        }
    }

    @Test
    fun `в пакетном окне гейт разрыва дня судит только объявленный порог`() {
        // K-20: непрошенный литерал профиля (360) в пакетном окне порогом не
        // является — минимум отдыха 300 принимается; объявленный пакетом
        // max_sit_duration восстанавливает гейт с его числом.
        for (name in listOf("min_rest_home", "min_rest_outstation")) {
            val r = RunThresholds.resolve(listOf(p(name, 300.0, "min")), D, packaged = true)
            assertEquals(300, r.applied.values.single().value)
            val e = assertThrows<TaskModeRefused> {
                RunThresholds.resolve(listOf(p(name, 300.0, "min"),
                    p("max_sit_duration", 300.0, "max", rule = "R-SIT")), D, packaged = true)
            }
            assertTrue(e.message!!.contains("разрыва дня 300"), e.message)
        }
        // Вне пакета — прежний гейт литералом профиля.
        assertThrows<TaskModeRefused> { RunThresholds.resolve(listOf(p("min_rest_home", 300.0, "min")), D) }
        // Ручка объявлена: порог доезжает до applied под именем показания.
        val declared = RunThresholds.resolve(listOf(p("max_sit_duration", 420.0, "max", rule = "R-SIT")), D)
        assertEquals(420, declared.applied.getValue("day_break_threshold_min").value)
        assertEquals(listOf("R-SIT"), declared.applied.getValue("day_break_threshold_min").rules)
    }

    @Test
    fun `окно применения — снаружи держатель пуст, и при исключении тоже`() {
        val r = RunThresholds.resolve(listOf(p("min_rest_home", 900.0, "min")), D)
        assertEquals(900, RunThresholds.withThresholds(r.applied) {
            RunThresholds.valueOr("min_rest_home_min", 0) })
        assertEquals(DefaultProviderRules.MIN_DUTY_REST_MIN,
            RunThresholds.valueOr("min_rest_home_min", DefaultProviderRules.MIN_DUTY_REST_MIN))
        assertThrows<IllegalStateException> {
            RunThresholds.withThresholds<Unit>(r.applied) { throw IllegalStateException("сборка упала") }
        }
        assertTrue(RunThresholds.current().isEmpty())
    }

    @Test
    fun `показания — у СОЗДАННОГО провайдера, по обеим ручкам`() {
        val r = RunThresholds.resolve(listOf(
            p("min_rest_home", 900.0, "min"),
            p("min_rest_outstation", 700.0, "min", rule = "R-2")), D)
        RunThresholds.withThresholds(r.applied) { FlightCrewSchedulingConstraintProvider() }
        val got = LastProviderReadings.snapshot()!!.second
        assertEquals(900, got["min_rest_home_min"])
        assertEquals(700, got["min_rest_outstation_min"])
        // Провайдер ВНЕ окна читает литералы — различие, которым живёт гейт.
        FlightCrewSchedulingConstraintProvider()
        assertEquals(DefaultProviderRules.MIN_DUTY_REST_MIN,
            LastProviderReadings.snapshot()!!.second["min_rest_home_min"])
    }

    @Test
    fun `снимок несёт словарь уточнителей, подстановочный ключ, выключатели и литералы в силе`() {
        val r = RunThresholds.resolve(listOf(p("max_duty_duration", 720.0, "max")), D)
        @Suppress("UNCHECKED_CAST")
        val snap = RunThresholds.snapshot(r, DefaultProviderRules.readings())
        val m = snap["paramToThreshold"] as Map<String, Any>
        val appliedRows = snap["applied"] as List<Map<String, Any?>>
        assertTrue(appliedRows.isNotEmpty())
        assertTrue(appliedRows.all { it["executor"] == "kotlin-provider" })
        assertTrue(m["min_turnaround"] is List<*>)
        val q = snap["qualifiedParamToThreshold"] as Map<String, Any>
        assertEquals("min_sit_home_min", q["min_turnaround|база"])
        assertEquals("min_sit_outstation_min", q["min_turnaround|вне базы"])
        assertEquals("min_rest_home_min", q["min_rest_home|в базе"])
        assertFalse(q.containsKey("min_rest_home|вне базы"))       // противоречие — не цель
        assertEquals("max_duty_span_min", q["max_duty_duration|*"])  // сужение — ко всей ручке
        assertTrue(q["min_turnaround|*"] is List<*>)
        val off = snap["offLiterals"] as Map<String, Int>
        assertEquals(DefaultProviderRules.MAX_LANDINGS_PER_DUTY_OFF, off["max_landings_per_duty"])
        // Литералы в силе: всё, о чём пакет молчит; применённое — нет.
        val inForce = snap["literalsInForce"] as List<Map<String, Any?>>
        val names = inForce.map { it["threshold"] }
        assertFalse("max_duty_span_min" in names)
        assertTrue("min_sit_outstation_min" in names)
        assertTrue(inForce.single { it["threshold"] == "max_landings_per_duty" }["note"]
            .toString().contains("выключено"))
        // Без показаний литералов в силе нет — и это null, а не пустой список.
        assertEquals(null, RunThresholds.snapshot(r)["literalsInForce"])
    }

    // --------------------------------------- численность и резерв (свод)

    @Test
    fun `норма резерва с кодом базы — ручка на каждую базу, ВКЛЮЧЕНИЕ и КЛЮЧ`() {
        val r = RunThresholds.resolve(listOf(
            p("reserve_crews", 2.0, "min", unit = "count", qualifier = "PLY"),
            p("reserve_crews", 1.0, "min", unit = "count", qualifier = "svg"),
            p("reserve_crews", 1.0, "min", unit = "count", qualifier = " GRT ")), D)
        assertEquals(setOf("reserve_crews_min[PLY]", "reserve_crews_min[SVG]", "reserve_crews_min[GRT]"),
            r.applied.keys)
        assertEquals(2, r.applied["reserve_crews_min[PLY]"]!!.value)
        assertEquals(DefaultProviderRules.RESERVE_CREWS_OFF, r.applied["reserve_crews_min[PLY]"]!!.literal)
        val d = decls(r, "reserve_crews_min[PLY]")
        assertTrue(d.any { it.startsWith("ВКЛЮЧЕНИЕ") }, d.toString())
        assertTrue(d.any { it.startsWith("КЛЮЧ") && it.contains("PLY") }, d.toString())
        assertTrue(d.any { it.startsWith("ПРОЧТЕНИЕ") && it.contains("наименьшее по ролям") })
        assertTrue(r.ignored.isEmpty())
    }

    @Test
    fun `«ровно» у резерва — нижняя граница с заявлением РАВЕНСТВО, не отказ`() {
        val r = RunThresholds.resolve(listOf(
            p("reserve_crews", 2.0, "min", unit = "count", qualifier = "PLY"),
            p("reserve_crews", 2.0, "eq", unit = "count", qualifier = "PLY", rule = "R-2")), D)
        val a = r.applied["reserve_crews_min[PLY]"]!!
        assertEquals(2, a.value)
        assertEquals(listOf("R-1", "R-2"), a.rules)
        assertTrue(a.declarations.any { it.startsWith("РАВЕНСТВО") && it.contains("R-2") }, a.declarations.toString())
    }

    @Test
    fun `норма без кода базы или не с кодом — не применено и названо с уточнителем`() {
        val r = RunThresholds.resolve(listOf(
            p("reserve_crews", 2.0, "min", unit = "count"),
            p("reserve_crews", 1.0, "min", unit = "count", qualifier = "база", rule = "R-2")), D)
        assertTrue(r.applied.isEmpty())
        assertEquals(2, r.ignored.size)
        assertTrue(r.ignored.any { it.reason.contains("без уточнителя-кода") })
        val notCode = r.ignored.single { it.reason.contains("не код базы") }
        assertEquals(listOf("база"), notCode.qualifiers)
    }

    @Test
    fun `численность - одинаковые числа одной записью, разные отказ, с уточнителем не применено`() {
        val same = RunThresholds.resolve(listOf(
            p("crew_size", 2.0, "eq", unit = "count"),
            p("crew_size", 2.0, "eq", unit = "count", rule = "R-2")), D)
        val a = same.applied["crew_size"]!!
        assertEquals(2, a.value)
        assertEquals(listOf("R-1", "R-2"), a.rules)
        assertTrue(a.declarations.any { it.startsWith("ВКЛЮЧЕНИЕ") })
        assertFalse(a.declarations.any { it.startsWith("СВЕДЕНО") })
        val e = assertThrows<TaskModeRefused> {
            RunThresholds.resolve(listOf(
                p("crew_size", 2.0, "eq", unit = "count"),
                p("crew_size", 3.0, "eq", unit = "count", rule = "R-2")), D)
        }
        assertTrue(e.message!!.contains("противоречие"), e.message)
        val ctx = RunThresholds.resolve(listOf(
            p("crew_size", 4.0, "eq", unit = "count", qualifier = "бортпроводники")), D)
        assertTrue(ctx.applied.isEmpty())
        assertTrue(ctx.ignored.single().reason.contains("контекстное"))
    }

    @Test
    fun `снимок - ключи по базам, без подстановочных у «ровно» и ключевых, выключатели и веса`() {
        val r = RunThresholds.resolve(listOf(
            p("reserve_crews", 2.0, "min", unit = "count", qualifier = "PLY"),
            p("crew_size", 2.0, "eq", unit = "count", rule = "R-2")), D)
        @Suppress("UNCHECKED_CAST")
        val snap = RunThresholds.snapshot(r)
        val q = snap["qualifiedParamToThreshold"] as Map<String, Any>
        assertEquals("reserve_crews_min[PLY]", q["reserve_crews|PLY"])
        assertFalse(q.containsKey("reserve_crews|*"))
        assertFalse(q.containsKey("crew_size|*"))
        val off = snap["offLiterals"] as Map<String, Int>
        assertEquals(0, off["crew_size"]); assertEquals(0, off["reserve_crews_min"])
        assertEquals(0, off["reserve_crews_min[PLY]"])
        val hw = snap["hardWeights"] as Map<String, Int>
        assertEquals(DefaultProviderRules.HARD_UNASSIGNED_SLOT, hw["Unassigned slot"])
        assertEquals(DefaultProviderRules.HARD_RESERVE_PER_CREW_DAY, hw["Reserve crews (per crew-day)"])
        val m = snap["paramToThreshold"] as Map<String, Any>
        assertEquals("reserve_crews_min", m["reserve_crews"])
        assertEquals("crew_size", m["crew_size"])
    }

    @Test
    fun `значения семейства читаются в окне, показания провайдера несут ключи`() {
        val r = RunThresholds.resolve(listOf(
            p("reserve_crews", 2.0, "min", unit = "count", qualifier = "PLY"),
            p("crew_size", 2.0, "eq", unit = "count", rule = "R-2")), D)
        val got = RunThresholds.withThresholds(r.applied) {
            assertEquals(mapOf("PLY" to 2), RunThresholds.keyedValues("reserve_crews_min"))
            FlightCrewSchedulingConstraintProvider().readings()
        }
        assertEquals(2, got["reserve_crews_min[PLY]"])
        assertEquals(2, got["crew_size"])
        assertFalse(got.containsKey("reserve_crews_min"))
        val off = FlightCrewSchedulingConstraintProvider().readings()
        assertEquals(0, off["crew_size"]); assertEquals(0, off["reserve_crews_min"])
        assertTrue(RunThresholds.keyedValues("reserve_crews_min").isEmpty())
    }

    @Test
    fun `проверка фактов до старта - состав мест, база без людей, две роли, нет рейсов`() {
        val ply = Fixtures.airport("PLY"); val out = Fixtures.airport("OUT")
        val c1 = Fixtures.employee("C1").apply { skills = mutableListOf("Captain"); homeAirport = ply }
        val f1 = Fixtures.employee("F1").apply { skills = mutableListOf("FirstOfficer"); homeAirport = ply }
        val x = Fixtures.flight("X", ply, Fixtures.t(2, 10), out, Fixtures.t(2, 12))
        fun seats(n: Int) = (0 until n).map { i ->
            org.acme.flighcrewscheduling.domain.FlightAssignment("s$i", x, i,
                if (i % 2 == 0) "Captain" else "FirstOfficer") }
        val ok = RunThresholds.resolve(listOf(
            p("crew_size", 2.0, "eq", unit = "count"),
            p("reserve_crews", 1.0, "min", unit = "count", qualifier = "PLY", rule = "R-2")), D)
        val cov = RunThresholds.checkFacts(
            Fixtures.schedule(listOf(ply, out), listOf(c1, f1), seats(2)), ok.applied)
        @Suppress("UNCHECKED_CAST")
        val res = cov["reserve"] as Map<String, Any?>
        assertEquals(1L, res["horizonDays"])
        assertEquals(listOf("PLY"), res["namedBases"])
        assertEquals(listOf("Captain", "FirstOfficer"), res["compositionRoles"])
        // Мест 3 против численности 2 — дефект набора, отказ с именем рейса.
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.checkFacts(Fixtures.schedule(listOf(ply, out), listOf(c1, f1), seats(3)), ok.applied)
        }.message!!.contains("несёт 3 мест"))
        // База нормы, которой нет ни среди аэропортов, ни среди приписок.
        val grt = RunThresholds.resolve(listOf(
            p("reserve_crews", 1.0, "min", unit = "count", qualifier = "GRT")), D)
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.checkFacts(Fixtures.schedule(listOf(ply, out), listOf(c1, f1), seats(2)), grt.applied)
        }.message!!.contains("ни аэропорта, ни сотрудника"))
        // Сотрудник базы нормы с двумя ролями состава.
        val both = Fixtures.employee("B1").apply {
            skills = mutableListOf("Captain", "FirstOfficer"); homeAirport = ply }
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.checkFacts(Fixtures.schedule(listOf(ply, out), listOf(both), seats(2)), ok.applied)
        }.message!!.contains("роли состава"))
        // Нет рейсов — горизонт не определён.
        assertTrue(assertThrows<TaskModeRefused> {
            RunThresholds.checkFacts(Fixtures.schedule(listOf(ply), listOf(c1, f1), emptyList()), grt.applied)
        }.message!!.contains("рейсов в наборе нет"))
    }

    @Test
    fun `профиль GERAD с порогами пакета — отказ, а не «применено» на бумаге`() {
        val pkg = LoadedTaskPackage(
            fingerprint = "f", specVersion = "1.3", customer = "", sourceRun = "",
            revision = 1, readiness = "в",
            allowedModes = mapOf("rostering" to listOf("feasibility-soft")),
            objectives = emptyMap(), objectiveDetails = emptyMap(),
            hardParams = listOf(p("max_duty_duration", 720.0, "max")),
            weights = emptyMap(), unmappedRules = emptyList(),
            componentsWithoutWeight = emptyList(), hasData = true)
        val e = assertThrows<TaskModeRefused> {
            TaskModes.resolve("feasibility-soft", pkg, "rostering", rulesProfile = "GERAD")
        }
        assertTrue(e.message!!.contains("только профилем по умолчанию"), e.message)
    }
}
