package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

/**
 * Две ручки на стыковку и отдых — в базе и вне базы — и посадки за смену.
 *
 * Провайдер читает пороги при СОЗДАНИИ, поэтому каждый тест строит
 * свой провайдер внутри окна порогов: так проверяется не только правило,
 * но и то, что порог из пакета реально доезжает до ограничения.
 */
class ScopedThresholdsConstraintTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun p(name: String, value: Double, relation: String,
                  unit: String = "duration_minutes", qualifier: String? = null,
                  rule: String = "R-1") =
        RuleParam(name, value, value.toString(), unit, relation, qualifier, "", rule, "HARD")

    private fun verifierWith(vararg params: RuleParam):
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> {
        val res = RunThresholds.resolve(params.toList(), TaskModes.PROFILE_DEFAULT)
        val provider = RunThresholds.withThresholds(res.applied) {
            FlightCrewSchedulingConstraintProvider()
        }
        return ConstraintVerifier.build(provider, FlightCrewSchedule::class.java,
            FlightAssignment::class.java)
    }

    /** Сотрудник с базой HOME; рейсы HOME→OUT→HOME. */
    private fun homeEmployee() = employee("E1").apply { homeAirport = airport("HOME") }

    @Test
    fun `стыковка в базе судится домашней ручкой, вне базы — внебазовой`() {
        // База 90 минут, вне базы 30. Стыковка В БАЗЕ 45 минут: дома —
        // нарушение (45 < 90); та же стыковка вне базы — законна (45 >= 30).
        val v = verifierWith(
            p("min_turnaround", 90.0, "min", qualifier = "база"),
            p("min_turnaround", 30.0, "min", qualifier = "вне базы", rule = "R-2"))
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        // Прилёт ДОМОЙ в 12:00, вылет из дома в 12:45 — стыковка дома.
        val a1 = assignment("a1", flight("F1", out, t(2, 10), home, t(2, 12)), e)
        val a2 = assignment("a2", flight("F2", home, t(2, 12, 45), out, t(2, 14)), e)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::minimumTurnaround).given(a1, a2).penalizesBy(90L - 45L)
        // Прилёт ВНЕ базы в 12:00, вылет оттуда в 12:45 — стыковка вне базы.
        val b1 = assignment("b1", flight("F3", home, t(3, 10), out, t(3, 12)), e)
        val b2 = assignment("b2", flight("F4", out, t(3, 12, 45), home, t(3, 14)), e)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::minimumTurnaround).given(b1, b2).penalizes(0)
    }

    @Test
    fun `отдых вне базы судится внебазовой ручкой`() {
        // Дома 720 (12 ч), вне базы 600 (10 ч). Разрыв 11 ч вне базы —
        // законный отдых; тот же разрыв дома — нарушение на 60 минут.
        val v = verifierWith(
            p("min_rest_home", 720.0, "min"),
            p("min_rest_outstation", 600.0, "min", rule = "R-2"))
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        val o1 = assignment("o1", flight("F1", home, t(2, 6), out, t(2, 8)), e)
        val o2 = assignment("o2", flight("F2", out, t(2, 19), home, t(2, 21)), e)   // 11 ч вне базы
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBetweenDutyPeriods).given(o1, o2).penalizes(0)
        val h1 = assignment("h1", flight("F3", out, t(4, 6), home, t(4, 8)), e)
        val h2 = assignment("h2", flight("F4", home, t(4, 19), out, t(4, 21)), e)   // 11 ч дома
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBetweenDutyPeriods).given(h1, h2).penalizesBy(720L - 660L)
    }

    @Test
    fun `packaged rest minimum does not invent a six hour sit maximum`() {
        // K-10/K-20/K-27 (стенд GPT, решение GPT-011): в пакетном окне
        // порог мёртвой зоны не объявлен → 0; семичасовая стыковка H0
        // законна по обоим правилам; девятичасовая — не отдых, смена
        // склеена и превышает размах на 120; минимум отдыха ниже 6 ч не
        // отвергается непрошенным порогом legacy-профиля.
        val res = RunThresholds.resolve(listOf(
            p("min_rest_home", 720.0, "min"),
            p("min_rest_outstation", 600.0, "min"),
            p("max_duty_duration", 720.0, "max")), TaskModes.PROFILE_DEFAULT)
        val provider = RunThresholds.withRunModel(res.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }
        assertEquals(0, provider.readings()["day_break_threshold_min"])
        val v = ConstraintVerifier.build(provider, FlightCrewSchedule::class.java, FlightAssignment::class.java)
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        val a = assignment("a", flight("A", home, t(2, 6), out, t(2, 8)), e)
        val b = assignment("b", flight("B", out, t(2, 15), home, t(2, 17)), e)
        // Seven-hour sit, eleven-hour total duty: legal under H0's limits.
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBetweenDutyPeriods).given(a, b).penalizes(0)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented).given(a, b).penalizes(0)
        val c = assignment("c", flight("C", out, t(2, 17), home, t(2, 20)), e)
        // A nine-hour sit is still not a legal 10-hour rest; the joined
        // fourteen-hour duty exceeds its 12-hour cap by two hours.
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented).given(a, c).penalizesBy(120)
        // A source minimum below six hours also cannot be rejected merely
        // because of the legacy profile's unrequested dead-zone threshold.
        assertEquals(300, RunThresholds.resolve(listOf(p("min_rest_outstation", 300.0, "min")),
            TaskModes.PROFILE_DEFAULT, packaged = true).applied["min_rest_outstation_min"]?.value)
    }

    @Test
    fun `объявленный порог разрыва дня исполняется, необъявленный не изобретается`() {
        // Пара «порог объявлен / отсутствует» (§3.10.2 плана): пакет с
        // max_sit_duration = 360 — та же семичасовая стыковка нарушает
        // отдых (штраф 600 − 420 = 180); показание — 360; минимум отдыха
        // не выше объявленного порога — отказ словами, как в профиле.
        val declared = RunThresholds.resolve(listOf(
            p("min_rest_home", 720.0, "min"),
            p("min_rest_outstation", 600.0, "min"),
            p("max_sit_duration", 360.0, "max", rule = "R-SIT")), TaskModes.PROFILE_DEFAULT, packaged = true)
        assertEquals(360, declared.applied["day_break_threshold_min"]?.value)
        val provider = RunThresholds.withRunModel(declared.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }
        assertEquals(360, provider.readings()["day_break_threshold_min"])
        val v = ConstraintVerifier.build(provider, FlightCrewSchedule::class.java, FlightAssignment::class.java)
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        val a = assignment("a", flight("A", home, t(2, 6), out, t(2, 8)), e)
        val b = assignment("b", flight("B", out, t(2, 15), home, t(2, 17)), e)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::restBetweenDutyPeriods).given(a, b).penalizesBy(180)
        // Без объявленного порога — то же назначение законно (см. стенд выше).
        val silent = RunThresholds.resolve(listOf(
            p("min_rest_home", 720.0, "min"),
            p("min_rest_outstation", 600.0, "min")), TaskModes.PROFILE_DEFAULT, packaged = true)
        assertEquals(null, silent.applied["day_break_threshold_min"])
        // Объявленный порог выше минимума отдыха — правило исчезло бы молча: отказ.
        assertThrows<TaskModeRefused> {
            RunThresholds.resolve(listOf(
                p("min_rest_outstation", 300.0, "min"),
                p("max_sit_duration", 360.0, "max", rule = "R-SIT")), TaskModes.PROFILE_DEFAULT, packaged = true)
        }
    }

    @Test
    fun `нарезка смен тоже по месту отдыха`() {
        // 11-часовой разрыв ДОМА (порог 720) НЕ режет смену: два рейса
        // сливаются в одну смену длиной 15 ч против предела 14 ч.
        val v = verifierWith(
            p("min_rest_home", 720.0, "min"),
            p("min_rest_outstation", 600.0, "min", rule = "R-2"))
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        val h1 = assignment("h1", flight("F3", out, t(4, 6), home, t(4, 8)), e)
        val h2 = assignment("h2", flight("F4", home, t(4, 19), out, t(4, 21)), e)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(h1, h2).penalizesBy(900L - 840L)
    }

    @Test
    fun `отдых вне базы режет смену, и продолжительность смены не штрафуется`() {
        // Симметрия к домашнему случаю: 11-часовой разрыв ВНЕ базы при
        // порогах 720/600 — законный отдых, смена разрезана, две смены по
        // 2 часа против предела 14 часов — ноль. Тот же разрыв при
        // внебазовом 720 НЕ режет: одна смена 15 часов, перебор 60 минут.
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        val o1 = assignment("o1", flight("F1", home, t(2, 6), out, t(2, 8)), e)
        val o2 = assignment("o2", flight("F2", out, t(2, 19), home, t(2, 21)), e)
        verifierWith(p("min_rest_home", 720.0, "min"),
                     p("min_rest_outstation", 600.0, "min", rule = "R-2"))
            .verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(o1, o2).penalizes(0)
        verifierWith(p("min_rest_home", 720.0, "min"),
                     p("min_rest_outstation", 720.0, "min", rule = "R-2"))
            .verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(o1, o2).penalizesBy(900L - 840L)
    }

    @Test
    fun `рейс без аэропорта прилёта судится внебазовой ручкой - явно, а не случайно`() {
        // Нет аэропорта прилёта — дома человек быть не может; это правило
        // решателя (см. atHome), и выгрузка смен режет так же.
        val v = verifierWith(
            p("min_turnaround", 90.0, "min", qualifier = "база"),
            p("min_turnaround", 30.0, "min", qualifier = "вне базы", rule = "R-2"))
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        val a1 = assignment("a1", flight("F1", out, t(2, 10), home, t(2, 12)), e)
        a1.flight!!.arrivalAirport = null
        val a2 = assignment("a2", flight("F2", home, t(2, 12, 45), out, t(2, 14)), e)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::minimumTurnaround).given(a1, a2).penalizes(0)
    }

    @Test
    fun `равный вылет - «последний рейс» по полному ключу, а не по истории ходов`() {
        // F1 и F2 вылетают в одну минуту; F1 прилетает ДОМОЙ в 12:00, F2 —
        // вне базы в 11:00. По полному ключу (вылет, аэропорт вылета,
        // прилёт, ...) последним стоит F1 → место отдыха дома → 630 минут
        // до F3 меньше домашних 720 → ОДНА смена 10:00–23:30 = 810 минут
        // против предела 600 → перебор 210. Порядок подачи назначений на
        // ответ не влияет — в этом и смысл.
        val v = verifierWith(
            p("min_rest_home", 720.0, "min"),
            p("min_rest_outstation", 600.0, "min", rule = "R-2"),
            p("max_duty_duration", 600.0, "max", rule = "R-3"))
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT"); val x = airport("XXX")
        val f1 = assignment("f1", flight("F1", x, t(2, 10), home, t(2, 12)), e)
        val f2 = assignment("f2", flight("F2", x, t(2, 10), out, t(2, 11)), e)
        val f3 = assignment("f3", flight("F3", home, t(2, 22, 30), out, t(2, 23, 30)), e)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(f1, f2, f3).penalizesBy(810L - 600L)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(f2, f1, f3).penalizesBy(810L - 600L)
    }

    @Test
    fun `посадки за смену - без пакета правило выключено, с пакетом считает`() {
        val e = homeEmployee()
        val home = airport("HOME"); val out = airport("OUT")
        // Три рейса одной смены (стыковки по часу).
        val a = assignment("a", flight("F1", home, t(2, 6), out, t(2, 7)), e)
        val b = assignment("b", flight("F2", out, t(2, 8), home, t(2, 9)), e)
        val c = assignment("c", flight("F3", home, t(2, 10), out, t(2, 11)), e)
        // Без пакета — литерал 0 = выключено.
        val off = ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java, FlightAssignment::class.java)
        off.verifyThat(FlightCrewSchedulingConstraintProvider::maxLandingsPerDuty)
            .given(a, b, c).penalizes(0)
        // С пакетом: не больше 2 посадок — третья лишняя.
        val on = verifierWith(p("max_landings_per_duty", 2.0, "max", unit = "count"))
        on.verifyThat(FlightCrewSchedulingConstraintProvider::maxLandingsPerDuty)
            .given(a, b, c).penalizesBy(1L)
    }

    @Test
    fun `сотрудник без базы приписки судится внебазовой ручкой`() {
        // Нет базы — дома он нигде; применяется внебазовая ручка. Какая из
        // двух строже — свойство чисел пакета, не правила; само правило
        // заявлено кодом (atHome), а не подразумевается.
        val v = verifierWith(
            p("min_turnaround", 90.0, "min", qualifier = "база"),
            p("min_turnaround", 30.0, "min", qualifier = "вне базы", rule = "R-2"))
        val e = employee("E2")                 // homeAirport = null
        val x = airport("XXX"); val y = airport("YYY")
        val a1 = assignment("a1", flight("F1", y, t(2, 10), x, t(2, 12)), e)
        val a2 = assignment("a2", flight("F2", x, t(2, 12, 45), y, t(2, 14)), e)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::minimumTurnaround).given(a1, a2).penalizes(0)
    }
}
