package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import org.acme.flighcrewscheduling.domain.Airport
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

/**
 * П-2, волна 1 — приёмка ПАРАМИ производителя данных: мой исполнитель
 * каждой формы против НЕЗАВИСИМОГО оракула производителя (ожидания
 * expected_units в parах). Датасеты пар — микромиры (Рейсы/Назначения/
 * Экипажи/Заявки) из handoff, скопированы в ресурсы репозитория
 * (тесты не читают машинных путей вне репозитория — урок CI).
 *
 * Гейты волны: нарушающий датасет даёт ровно оракульные единицы,
 * чистый — ноль; мутация (сдвиг параметра формы либо данных) меняет
 * вклад — порча исполнителя не может пройти незамеченной.
 */
class P2WaveFormsTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private val mapper = ObjectMapper()
    private val pairs: JsonNode = mapper.readTree(
        javaClass.getResourceAsStream("/p2-wave1-pairs.json")!!)


    // ------------------------------------------------ сборка микромира

    private data class Micro(val assignments: List<FlightAssignment>)

    private fun micro(dataset: JsonNode): Micro {
        val tables = dataset["tables"]["SOL"]
        val airports = HashMap<String, Airport>()
        fun airport(code: String) = airports.getOrPut(code) { Airport(code) }
        val employees = HashMap<String, Employee>()
        for (row in tables["Экипажи"]["rows"]) {
            val id = row["Табельный номер"].asText()
            employees[id] = Employee(id).apply {
                name = id
                skills = mutableListOf(Fixtures.SKILL)
                typeRatings = mutableListOf("A320")
                homeAirport = airport(row["База"].asText())
            }
        }
        tables["Заявки"]?.let { sheet ->
            for (row in sheet["rows"]) {
                employees[row["Табельный номер"].asText()]!!
                    .preferredDaysOff.add(LocalDate.parse(row["Дата"].asText()))
            }
        }
        val flights = HashMap<String, Flight>()
        for (row in tables["Рейсы"]["rows"]) {
            val num = row["Номер рейса"].asText()
            val date = LocalDate.parse(row["Дата"].asText())
            val depT = LocalTime.parse(row["Время вылета"].asText())
            val arrT = LocalTime.parse(row["Время прилёта"].asText())
            val dep = LocalDateTime.of(date, depT)
            // прилёт раньше вылета — через полночь
            val arr = if (arrT.isAfter(depT)) LocalDateTime.of(date, arrT)
                      else LocalDateTime.of(date.plusDays(1), arrT)
            flights[num] = Flight(num).apply {
                departureAirport = airport(row["Вылет"].asText())
                departureUTCDateTime = dep
                arrivalAirport = airport(row["Прилёт"].asText())
                arrivalUTCDateTime = arr
            }
        }
        var i = 0
        val assignments = tables["Назначения"]["rows"].map { row ->
            i += 1
            FlightAssignment("s$i", flights[row["Номер рейса"].asText()]!!, 0,
                Fixtures.SKILL).apply {
                employee = employees[row["Табельный номер"].asText()]!!
            }
        }
        return Micro(assignments)
    }

    // ------------------------------------------------ план из spec пары

    private fun planFor(spec: JsonNode, mutate: Boolean = false): LogicPlan {
        val template = spec["template"].asText()
        val form = LogicForms.TEMPLATE_TO_FORM[template]
            ?: throw IllegalStateException("шаблон вне словаря волны: " + template)
        val p = spec["params"]
        var bound = 0
        var condBound: Int? = null
        var nightFrom: Int? = null
        var nightTo: Int? = null
        when (form) {
            LogicForms.FORM_SIT_OVERAGE_STEPS -> {
                bound = p["threshold_min"].asInt()
                condBound = p["step_min"].asInt()
                if (mutate) bound = 500
            }
            LogicForms.FORM_DUTY_SPLIT_SIT -> {
                bound = p["threshold_min"].asInt()
                if (mutate) bound = 500
            }
            LogicForms.FORM_CHAIN_NIGHT_DAY_MIX -> {
                val w = p["night_window"].asText().split("-")
                val f = LocalTime.parse(w[0]); val t = LocalTime.parse(w[1])
                nightFrom = f.hour * 60 + f.minute
                nightTo = t.hour * 60 + t.minute + 1   // «01:00-06:59» = [60, 420)
                if (mutate) { nightFrom = 21 * 60; nightTo = 21 * 60 + 1 }
            }
            LogicForms.FORM_CONSECUTIVE_DAYS_MAX -> {
                bound = p["bound"].asInt()
                if (mutate) bound = 50
            }
            LogicForms.FORM_UNMET_REQUEST -> { /* параметров нет */ }
        }
        return LogicPlan(
            ruleIds = listOf(spec["rule_id"].asText()),
            constraintId = "logic wave1 " + form,
            form = form, subject = "duty", relation = "max",
            bound = bound, boundParam = "wave1", baseScope = null,
            condBound = condBound, condParam = null,
            nightFrom = nightFrom, nightTo = nightTo,
            enforce = "penalty", weight = 1,
            reading = "приёмка парой волны 1", shapeSha = "wave1",
            soft = true)
    }

    /** Верификатор с планом В ДЕРЖАТЕЛЕ (урок ступени 3: проверяемое
     *  ограничение обязано быть в defineConstraints провайдера). */
    private fun assertUnits(plan: LogicPlan, assignments: List<FlightAssignment>,
                            expected: Long) {
        RunThresholds.withRunModel(emptyMap(), listOf(plan)) {
            ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
                FlightCrewSchedule::class.java, FlightAssignment::class.java)
        }.verifyThat { prov: FlightCrewSchedulingConstraintProvider,
                       cf: ai.timefold.solver.core.api.score.stream.ConstraintFactory ->
            prov.buildLogic(cf, plan)
        }.given(*assignments.toTypedArray()).let { check ->
            if (expected == 0L) check.penalizes(0) else check.penalizesBy(expected)
        }
    }

    private fun assertUnits(pair: JsonNode, side: String, expected: Long,
                            mutate: Boolean = false) {
        assertUnits(planFor(pair["spec"], mutate), micro(pair[side]).assignments, expected)
    }

    // --------------------------------------------------------- приёмка

    @Test
    fun `пары волны - нарушающий равен оракулу, чистый ноль`() {
        var checked = 0
        for (ruleId in pairs.fieldNames().asSequence().toList().sorted()) {
            val pair = pairs[ruleId]
            val exp = pair["spec"]["expected_units"]
            assertEquals(ruleId, pair["spec"]["rule_id"].asText(),
                "rule_id в spec обязан совпадать с ключом пары")
            assertUnits(pair, "dataset_bad", exp["bad"].asLong())
            assertUnits(pair, "dataset_good", exp["good"].asLong())
            checked += 1
        }
        assertEquals(5, checked, "пар рассмотрено")
    }

    @Test
    fun `мутации - сдвиг параметра гасит нарушающий датасет`() {
        var mutated = 0
        for (ruleId in pairs.fieldNames().asSequence().toList().sorted()) {
            val pair = pairs[ruleId]
            val template = pair["spec"]["template"].asText()
            if (template == "неудовлетворённая-выполнимая-заявка") {
                // у формы нет параметров — мутация данными: без заявок ноль
                val plan = planFor(pair["spec"])
                val m = micro(pair["dataset_bad"])
                m.assignments.forEach { it.employee!!.preferredDaysOff.clear() }
                assertUnits(plan, m.assignments, 0L)
            } else {
                assertUnits(pair, "dataset_bad", 0L, mutate = true)
            }
            mutated += 1
        }
        assertEquals(5, mutated, "мутаций рассмотрено")
    }

    @Test
    fun `мягкая форма штрафует в мягкие, не в жёсткие`() {
        // Пара выбирается по шаблону формы, а не по идентификатору правила:
        // ключи ресурса — данные производителя и на них тест не завязан.
        val pair = pairs.elements().asSequence()
            .first { it["spec"]["template"].asText() == "штраф-ступенчатый-за-стыковку" }
        val plan = planFor(pair["spec"])
        assertTrue(plan.soft)
        assertEquals(ai.timefold.solver.core.api.score.HardSoftScore.ofSoft(1),
            plan.score())
    }
}
