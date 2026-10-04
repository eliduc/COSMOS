package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import java.time.DayOfWeek
import org.acme.flighcrewscheduling.domain.CalendarWindowRule
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.Test

/**
 * Мягкая семья calendar_windows (трафарет демо 2026-08-29, правило
 * «Ночёвки в конце недели»): штраф за ночёвку вне базы, чей
 * календарный день попадает в окно дней недели правила пакета.
 * Окно — фактом CalendarWindowRule (из формы пакета), вес —
 * переопределением весов пакета; без фактов окна счёт байт-идентичен
 * прежнему (легаси). Календарь фикстур: март-2026, 6-е — пятница,
 * 7-е — суббота, 10-е — вторник.
 */
class CalendarWindowConstraintTest {

    private val verifier:
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider,
            FlightCrewSchedule> =
        ConstraintVerifier.build(
            FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    private val base = airport("BSE")
    private val out = airport("OUT")

    private val weekend = CalendarWindowRule(
        "R-S1-табл-3-строка-7-кол-3", "layover_penalty",
        setOf(DayOfWeek.FRIDAY, DayOfWeek.SATURDAY))

    private fun crewman(id: String): Employee =
        employee(id).apply { homeAirport = base }

    private fun seat(id: String, f: Flight, who: Employee) =
        FlightAssignment(id, f, 0, Fixtures.SKILL).apply { employee = who }

    @Test
    fun `пятничная ночёвка вне базы в окне штрафуется`() {
        // Контракт Б (05.09.2026): ночь доказана продолжением — назначение
        // того же человека из OUT на следующий день; без продолжения
        // (граница горизонта) автоматической ночи нет, см. стенд ниже.
        val e = crewman("E1")
        val fri = flight("F1", base, t(6, 10), out, t(6, 12))
        val sat = flight("F2", out, t(7, 10), base, t(7, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", fri, e), seat("s2", sat, e))
            .penalizesBy(1)
    }

    @Test
    fun `пятничное окончание вне базы без продолжения — автоматической ночи в окне нет`() {
        val e = crewman("E1")
        val fri = flight("F1", base, t(6, 10), out, t(6, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", fri, e))
            .penalizesBy(0)
    }

    @Test
    fun `вторник вне окна и ночёвка в базе не штрафуются`() {
        val e = crewman("E1")
        val tue = flight("F1", base, t(10, 10), out, t(10, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", tue, e))
            .penalizesBy(0)
        val friHome = flight("F2", out, t(6, 10), base, t(6, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s2", friHome, e))
            .penalizesBy(0)
    }

    @Test
    fun `ночёвка считается по дате - два прилёта одного дня дают одну`() {
        val e = crewman("E1")
        // Контракт Б (05.09.2026): ночь доказывается продолжением в
        // другой день; хвост без продолжения ночи не даёт.
        val fri1 = flight("F1", base, t(6, 8), out, t(6, 10))
        val fri2 = flight("F2", out, t(6, 12), out, t(6, 14))
        val satHome = flight("F9", out, t(7, 10), base, t(7, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", fri1, e), seat("s2", fri2, e),
                   seat("s9", satHome, e))
            .penalizesBy(1)
        val sat = flight("F3", out, t(7, 12), out, t(7, 14))
        val sunHome = flight("F8", out, t(8, 10), base, t(8, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", fri1, e), seat("s3", sat, e),
                   seat("s8", sunHome, e))
            .penalizesBy(2)
    }

    @Test
    fun `неизвестный конец не добавляет ночь - известный внутренний переход остаётся`() {
        // K-22 (стенд GPT, четвёртый к трём main): два прилёта одного дня
        // без продолжения — ночи нет (хвост неизвестен); внутренний
        // переход пятница → суббота вне базы (OUT → OUT) доказан — одна.
        val e = crewman("E1")
        val fri1 = flight("F1", base, t(6, 8), out, t(6, 10))
        val fri2 = flight("F2", out, t(6, 12), out, t(6, 14))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", fri1, e), seat("s2", fri2, e))
            .penalizesBy(0)
        val sat = flight("F3", out, t(7, 12), out, t(7, 14))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", fri1, e), seat("s3", sat, e))
            .penalizesBy(1)
    }

    @Test
    fun `возврат в тот же день - не ночёвка`() {
        // Ступень стенда (сертификат H0 2026-09-03: H19 GRT→PLY 13:10 и
        // PLY→GRT 19:00 того же дня получал «ночь» и надбавку пятницы).
        val e = crewman("E1")
        val friOut = flight("F1", base, t(6, 13), out, t(6, 15))
        val friBack = flight("F2", out, t(6, 19), base, t(6, 21))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", friOut, e), seat("s2", friBack, e))
            .penalizesBy(0)
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", friOut, e), seat("s2", friBack, e))
            .penalizesBy(0)
    }

    @Test
    fun `две ночи подряд без рейсов считаются обе`() {
        // Прилёт вне базы в пятницу, следующий вылет в воскресенье:
        // ночи пятницы и субботы — обе в окне; ночёвок всего две.
        val e = crewman("E1")
        val fri = flight("F1", base, t(6, 10), out, t(6, 12))
        val sun = flight("F2", out, t(8, 10), base, t(8, 12))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(weekend, e, seat("s1", fri, e), seat("s2", sun, e))
            .penalizesBy(2)
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", fri, e), seat("s2", sun, e))
            .penalizesBy(2)
    }

    @Test
    fun `чужое действие окна не штрафует ночёвки`() {
        val e = crewman("E1")
        val fri = flight("F1", base, t(6, 10), out, t(6, 12))
        val other = CalendarWindowRule(
            "R-X", "иное_действие", setOf(DayOfWeek.FRIDAY))
        verifier.verifyThat(
            FlightCrewSchedulingConstraintProvider::calendarWindowLayovers)
            .given(other, e, seat("s1", fri, e))
            .penalizesBy(0)
    }
}
