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
import java.time.LocalDate

/**
 * Профиль GERAD2P: КАЖДОЕ ограничение ростеринга проверено нарушающим
 * случаем И контрольным.
 *
 * Зачем этот файл. Факт «решатель применяет персональные ограничения»
 * был установлен 2026-08-12 живой пробой — матрицей из 16 случаев на
 * поднятой JVM. Проба жила в сессии и исчезла вместе с ней: назавтра
 * доказательства уже не существовало, а безобидная правка домена или
 * фильтра сняла бы ограничение молча — ростер остался бы «допустимым» с
 * нарушением нормы внутри. Тот же класс дефекта однажды уже стоил
 * рекордного ростера, который сертификат разнёс 595 нарушениями.
 *
 * Контрольный случай обязателен в каждой проверке: без него «штраф
 * начислен» не отличить от «штраф начисляется всегда», и ограничение,
 * ломающее допустимые решения, выглядело бы работающим.
 *
 * Ревизия 4 контракта ростеринга (2026-08-16) относит к этому профилю
 * правила свода класса А.
 */
class GeradPairingRosterConstraintProviderTest {

    private val verifier:
        ConstraintVerifier<GeradPairingRosterConstraintProvider,
                           FlightCrewSchedule> =
        ConstraintVerifier.build(
            GeradPairingRosterConstraintProvider(),
            FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    private val base = airport("BASE")
    private val dest = airport("DEST")

    /** Цепочка BASE→DEST в первый день: 08:00–12:00. */
    private fun pairing(
        id: String = "C_1",
        dep: org.acme.flighcrewscheduling.domain.Airport = base,
        arr: org.acme.flighcrewscheduling.domain.Airport = dest,
        day: Int = 1,
        fromHour: Int = 8,
        toHour: Int = 12,
    ): Flight = flight(id, dep, t(day, fromHour), arr, t(day, toHour))

    private fun seat(
        id: String, f: Flight, e: Employee?, role: String = Fixtures.SKILL,
    ): FlightAssignment =
        FlightAssignment(id, f, 0, role).apply { employee = e }

    /** Сотрудник, приписанный к базе (иначе срабатывает домашняя база). */
    private fun crew(id: String): Employee =
        employee(id).apply { homeAirport = base }

    // ------------------------------------------------- роль и тип судна

    @Test
    fun `required skill fires on a wrong role and not on the right one`() {
        val f = pairing()
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::requiredSkill)
            .given(seat("1", f, crew("E1"), role = "CABIN"))
            .penalizesBy(1)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::requiredSkill)
            .given(seat("2", f, crew("E2")))
            .penalizesBy(0)
    }

    @Test
    fun `type rating is checked for every type of the pairing`() {
        // Цепочка смешана по типам: допуска на второй тип нет.
        val mixed = pairing().apply {
            aircraftTypes = mutableListOf("A320", "B787")
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::typeRating)
            .given(seat("1", mixed, crew("E1")))
            .penalizesBy(1)                 // ровно один недостающий тип
        val rated = crew("E2").apply {
            typeRatings = mutableListOf("A320", "B787")
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::typeRating)
            .given(seat("2", mixed, rated))
            .penalizesBy(0)
    }

    // ------------------------------------------------------- доступность

    @Test
    fun `unavailable day blocks the pairing that touches it`() {
        val f = pairing()
        val off = crew("E1").apply {
            unavailableDays = mutableListOf(LocalDate.of(2026, 3, 1))
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::unavailability)
            .given(seat("1", f, off))
            .penalizesBy(1)
        val free = crew("E2").apply {
            unavailableDays = mutableListOf(LocalDate.of(2026, 3, 9))
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::unavailability)
            .given(seat("2", f, free))
            .penalizesBy(0)
    }

    @Test
    fun `overlapping pairings of one crew member are penalized by hours`() {
        val e = crew("E1")
        val a = pairing("C_A", fromHour = 8, toHour = 12)
        val b = pairing("C_B", fromHour = 10, toHour = 14)
        verifier.verifyThat(GeradPairingRosterConstraintProvider::overlap)
            .given(seat("1", a, e), seat("2", b, e))
            .penalizesBy(2)                 // два часа пересечения
        val other = crew("E2")
        verifier.verifyThat(GeradPairingRosterConstraintProvider::overlap)
            .given(seat("1", a, e), seat("2", b, other))
            .penalizesBy(0)                 // разные люди — не пересечение
    }

    // ------------------------------------------------------------ отдых

    @Test
    fun `rest between pairings shorter than the minimum is penalized`() {
        val e = crew("E1")
        val first = pairing("C_A", day = 1, fromHour = 8, toHour = 12)
        // 4 часа между цепочками против минимума 600 минут
        val soon = pairing("C_B", dep = dest, arr = base,
                           day = 1, fromHour = 16, toHour = 20)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::interPairingRest)
            .given(seat("1", first, e), seat("2", soon, e))
            .penalizesBy((RulesConfig.minRestMin - 240).toLong())
        val later = pairing("C_C", dep = dest, arr = base,
                            day = 2, fromHour = 8, toHour = 12)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::interPairingRest)
            .given(seat("1", first, e), seat("3", later, e))
            .penalizesBy(0)
    }

    @Test
    fun `per-unit rest after a pairing acts above the global minimum`() {
        val e = crew("E1")
        // Требование юнита строже глобального минимума на 60 минут.
        val demanding = pairing("C_A", day = 1, fromHour = 8, toHour = 12)
            .apply { minRestAfterMin = RulesConfig.minRestMin + 60 }
        val next = pairing("C_B", dep = dest, arr = base, day = 2,
                           fromHour = 8, toHour = 12)   // 20 часов отдыха
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::perUnitRestAfter)
            .given(seat("1", demanding, e), seat("2", next, e))
            .penalizesBy(0)                 // 1200 минут хватает
        val tight = pairing("C_D", dep = dest, arr = base, day = 1,
                            fromHour = 23, toHour = 23)  // 11 часов
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::perUnitRestAfter)
            .given(seat("1", demanding, e), seat("3", tight, e))
            .penalizesBy((demanding.minRestAfterMin - 660).toLong())
    }

    @Test
    fun `weekly rest is a no-op while the resource rule is not supplied`() {
        // Правило включается документом CREW_RESOURCE_RULES, который
        // разбирается один раз при загрузке класса: в этом процессе его
        // нет, и ограничение обязано быть пустым, а не падать.
        val e = crew("E1")
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::weeklyRest)
            .given(seat("1", pairing(), e))
            .penalizesBy(0)
    }

    // --------------------------------------------- станции и допуски

    @Test
    fun `denied station of the crew member blocks the pairing`() {
        val f = pairing().apply {
            stationCodes = mutableListOf("BASE", "DEST")
        }
        val banned = crew("E1").apply {
            stationsDenied = mutableListOf("DEST")
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::deniedStations)
            .given(seat("1", f, banned))
            .penalizesBy(1)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::deniedStations)
            .given(seat("2", f, crew("E2")))
            .penalizesBy(0)
    }

    @Test
    fun `qualification must be valid for the whole pairing interval`() {
        val f = pairing().apply {
            requiredCodesAll = mutableListOf("MVL")
        }
        val noQual = crew("E1")
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::requiredQualifications)
            .given(seat("1", f, noQual))
            .penalizesBy(1)
        val lo = t(1, 0).toEpochSecond(java.time.ZoneOffset.UTC) / 60
        val hi = t(9, 0).toEpochSecond(java.time.ZoneOffset.UTC) / 60
        val holder = crew("E2").apply {
            qualificationWindows = mutableMapOf(
                "MVL" to mutableListOf(mutableListOf(lo, hi)))
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::requiredQualifications)
            .given(seat("2", f, holder))
            .penalizesBy(0)
        // Окно, истекающее ДО конца цепочки, не годится: допуск обязан
        // действовать на всём интервале, а не в момент вылета.
        val expiring = crew("E3").apply {
            qualificationWindows = mutableMapOf(
                "MVL" to mutableListOf(mutableListOf(lo, lo + 9 * 60)))
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::requiredQualifications)
            .given(seat("3", f, expiring))
            .penalizesBy(1)
    }

    // ------------------------------------------- география и база

    @Test
    fun `next pairing must depart where the previous one arrived`() {
        val e = crew("E1")
        val first = pairing("C_A", day = 1, fromHour = 8, toHour = 12)
        val fromElsewhere = pairing("C_B", dep = airport("OTHER"),
                                    arr = base, day = 3,
                                    fromHour = 8, toHour = 12)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::interPairingContinuity)
            .given(seat("1", first, e), seat("2", fromElsewhere, e))
            .penalizesBy(1)
        val fromDest = pairing("C_C", dep = dest, arr = base, day = 3,
                               fromHour = 8, toHour = 12)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::interPairingContinuity)
            .given(seat("1", first, e), seat("3", fromDest, e))
            .penalizesBy(0)
    }

    @Test
    fun `pairing must start at the home base of the crew member`() {
        val f = pairing()
        val foreign = employee("E1").apply { homeAirport = airport("OTHER") }
        verifier.verifyThat(GeradPairingRosterConstraintProvider::homeBase)
            .given(seat("1", f, foreign))
            .penalizesBy(1)
        verifier.verifyThat(GeradPairingRosterConstraintProvider::homeBase)
            .given(seat("2", f, crew("E2")))
            .penalizesBy(0)
    }

    // ------------------------------------------------- месячные пределы

    @Test
    fun `monthly block time uses the personal limit when it is set`() {
        val f = pairing().apply {
            blockMinutesByMonth = mutableMapOf("2026-03" to 5000L)
        }
        val tight = crew("E1").apply { flightTimeLimitMin = 4000 }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::monthlyBlock)
            .given(seat("1", f, tight))
            .penalizesBy(1000)              // 5000 - 4000
        val roomy = crew("E2").apply { flightTimeLimitMin = 6000 }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::monthlyBlock)
            .given(seat("2", f, roomy))
            .penalizesBy(0)
    }

    @Test
    fun `monthly work time acts only for a crew member with a limit`() {
        val f = pairing().apply {
            workMinutesByMonth = mutableMapOf("2026-03" to 3000L)
        }
        val limited = crew("E1").apply { workTimeLimitMin = 2500 }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::monthlyWork)
            .given(seat("1", f, limited))
            .penalizesBy(500)
        verifier.verifyThat(          // предела нет — правило не задано
            GeradPairingRosterConstraintProvider::monthlyWork)
            .given(seat("2", f, crew("E2")))
            .penalizesBy(0)
    }

    // ------------------------------------------ совместная работа

    @Test
    fun `incompatible crew members must not share one pairing`() {
        val f = pairing()
        val a = crew("E1").apply { doNotFlyWith = mutableListOf("E2") }
        val b = crew("E2")                  // список односторонний
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::doNotFlyWith)
            .given(seat("1", f, a), seat("2", f, b))
            .penalizesBy(1)
        val c = crew("E3")
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::doNotFlyWith)
            .given(seat("1", f, a), seat("3", f, c))
            .penalizesBy(0)
    }

    // ------------------------------------------------- штрафуемые цели

    @Test
    fun `uncovered pairing costs its every leg`() {
        val f = pairing().apply {
            legIds = mutableListOf("L1", "L2", "L3")
        }
        verifier.verifyThat(GeradPairingRosterConstraintProvider::uncovered)
            .given(seat("1", f, null))
            .penalizesBy(3)                 // по одному за участок
        verifier.verifyThat(GeradPairingRosterConstraintProvider::uncovered)
            .given(seat("2", f, crew("E1")))
            .penalizesBy(0)
    }

    @Test
    fun `vacation days touched by the pairing span are counted`() {
        val f = pairing(day = 1, fromHour = 8, toHour = 12)
        val wants = crew("E1").apply {
            preferredDaysOff = mutableListOf(LocalDate.of(2026, 3, 1))
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::vacationDays)
            .given(seat("1", f, wants))
            .penalizesBy(1)
        val elsewhere = crew("E2").apply {
            preferredDaysOff = mutableListOf(LocalDate.of(2026, 3, 20))
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::vacationDays)
            .given(seat("2", f, elsewhere))
            .penalizesBy(0)
    }

    @Test
    fun `legs the crew member asked for are rewarded`() {
        val f = pairing().apply { legIds = mutableListOf("L1", "L2") }
        val asked = crew("E1").apply {
            preferredFlights = mutableListOf("L1", "L2")
        }
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::preferredLegs)
            .given(seat("1", f, asked))
            .rewardsWith(2)
        verifier.verifyThat(
            GeradPairingRosterConstraintProvider::preferredLegs)
            .given(seat("2", f, crew("E2")))
            .rewardsWith(0)
    }
}
