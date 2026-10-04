package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
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
 * Контракт Б (план 05.09.2026, согласован владельцем, аудитором, COSMOS и
 * CDPM): ночёвка вне базы начисляется только ДОКАЗАННАЯ — отдых вне базы
 * приписки до следующего назначения того же человека в другой
 * календарный день. Граница горизонта сама по себе — не основание ни
 * начислить ночёвку, ни признать её отсутствующей: после ПОСЛЕДНЕГО
 * назначения продолжение неизвестно, автоматической ночи нет (прежде
 * решатель начислял ночь после последнего прилёта вне базы, а
 * сертификатор — «одну ночь после последней смены»; это и были две
 * выдуманные ночёвки по границе). Неопределённость называет сертификат.
 * Ожидания — из сценария приёмки шага 1 (случаи 2, 6, 7, 8), не из
 * согласия двух реализаций. Календарь фикстур: март-2026.
 */
class LayoverBoundaryTest {

    private val verifier:
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider,
            FlightCrewSchedule> =
        ConstraintVerifier.build(
            FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    private val base = airport("BSE")
    private val away = airport("AWY")

    private fun crewman(id: String): Employee =
        employee(id).apply { homeAirport = base }

    private fun seat(id: String, f: Flight, who: Employee) =
        FlightAssignment(id, f, 0, Fixtures.SKILL).apply { employee = who }

    @Test
    fun `случай 7 — дневное окончание вне базы без продолжения не даёт автоматической ночи`() {
        val e = crewman("E1")
        val out = flight("F1", base, t(4, 6), away, t(4, 8))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e))
            .penalizesBy(0)
    }

    @Test
    fun `случай 2 и 6 — доказанная ночёвка вне базы начисляется ровно один раз`() {
        val e = crewman("E1")
        val out = flight("F1", base, t(4, 6), away, t(4, 17))
        val back = flight("F2", away, t(5, 8), base, t(5, 11))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e), seat("s2", back, e))
            .penalizesBy(1)
    }

    @Test
    fun `случай 8 — известное продолжение с возвратом в тот же день не начисляется`() {
        val e = crewman("E1")
        val out = flight("F1", base, t(4, 6), away, t(4, 8))
        val back = flight("F2", away, t(4, 9), base, t(4, 11))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e), seat("s2", back, e))
            .penalizesBy(0)
    }

    @Test
    fun `случай 5 — две смены подряд вне базы через одну ночь дают одну ночь, не две`() {
        val e = crewman("E1")
        val out = flight("F1", base, t(4, 6), away, t(4, 17))
        val local = flight("F2", away, t(5, 8), away, t(5, 12))
        val back = flight("F3", away, t(5, 13), base, t(5, 15))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e), seat("s2", local, e), seat("s3", back, e))
            .penalizesBy(1)
    }

    // ------------------------------------------ K-12/K-33: уточнения GPT на layoverNightsOf

    @Test
    fun `полночная стыковка внутри одной нормативной смены — не ночёвка`() {
        // 20:00 → 22:00 вне базы, обратно 01:00 → 03:00: разрыв 3 ч < отдыха
        // профиля (8 ч) — одна смена; переход даты внутри смены ночью не является.
        val e = crewman("E1")
        val out = flight("F1", base, t(6, 20), away, t(6, 22))
        val back = flight("F2", away, t(7, 1), base, t(7, 3))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e), seat("s2", back, e))
            .penalizesBy(0)
        // Тот же переход даты при законном отдыхе (9 ч ≥ 8 ч) — ночь доказана.
        val late = flight("F3", away, t(7, 7), base, t(7, 9))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e), seat("s3", late, e))
            .penalizesBy(1)
    }

    @Test
    fun `разрыв станций — неизвестное, а не ночь`() {
        // Прилёт вне базы, следующий вылет ИЗ БАЗЫ: маршрут разомкнут
        // (жёсткое «Route continuity» своё скажет), ночь не начисляется.
        val e = crewman("E1")
        val out = flight("F1", base, t(6, 10), away, t(6, 12))
        val fromHome = flight("F2", base, t(7, 10), base, t(7, 12))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e), seat("s2", fromHome, e))
            .penalizesBy(0)
    }

    @Test
    fun `несколько сотрудников — своя база и своя ночь у каждого`() {
        val e1 = crewman("E1")
        val e2 = employee("E2").apply { homeAirport = away }
        val out = flight("F1", base, t(6, 10), away, t(6, 12))
        val back = flight("F2", away, t(7, 10), base, t(7, 12))
        // Для E1 ночь в AWY — одна; для E2 AWY — дом, ночи нет.
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e1, e2, seat("s1", out, e1), seat("s2", back, e1),
                FlightAssignment("x1", out, 1, Fixtures.SKILL).apply { employee = e2 },
                FlightAssignment("x2", back, 1, Fixtures.SKILL).apply { employee = e2 })
            .penalizesBy(1)
    }

    @Test
    fun `возврат в тот же день перегоном — ночи нет и неизвестного нет`() {
        val e = crewman("E1")
        val out = flight("F1", base, t(6, 10), away, t(6, 12))
        val dh = flight("F2", away, t(6, 16), base, t(6, 18))
        verifier.verifyThat(FlightCrewSchedulingConstraintProvider::layoverNights)
            .given(e, seat("s1", out, e), seat("dh", dh, e).apply { deadhead = true })
            .penalizesBy(0)
    }
}
