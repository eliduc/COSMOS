package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.time.LocalDate

/**
 * Суточный резерв по базам (правило свода). Каждая проверка — один
 * класс прочтения: по ролям, а не «любые двое»; нулевая клетка даёт
 * нехватку, а не молчит; сутки — по датам вылета и прилёта; отпуск — не
 * резерв; база без нормы не считается; вне окна порогов — выключено.
 * Факты (сотрудники, рейсы) передаются явно: универсум клеток строится
 * из них.
 */
class ReserveCrewsConstraintTest {

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    private fun norm(code: String, n: Double, rule: String = "R-1", relation: String = "min") =
        RuleParam("reserve_crews", n, n.toString(), "count", relation, code, "", rule, "HARD")

    private fun verifierWith(vararg params: RuleParam):
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> {
        val res = RunThresholds.resolve(params.toList(), TaskModes.PROFILE_DEFAULT)
        val provider = RunThresholds.withThresholds(res.applied) {
            FlightCrewSchedulingConstraintProvider()
        }
        return ConstraintVerifier.build(provider, FlightCrewSchedule::class.java,
            FlightAssignment::class.java)
    }

    private fun crew(id: String, base: String, role: String): Employee = Employee(id).apply {
        name = id
        skills = mutableListOf(role)
        typeRatings = mutableListOf("A320")
        homeAirport = airport(base)
    }

    private val ply = airport("PLY")
    private val out = airport("OUT")
    private val c1 = crew("C1", "PLY", "Captain")
    private val c2 = crew("C2", "PLY", "Captain")
    private val f1 = crew("F1", "PLY", "FirstOfficer")
    private val f2 = crew("F2", "PLY", "FirstOfficer")
    /** Рейс X в сутки 2 марта, 10:00–12:00. */
    private val x: Flight = flight("X", ply, t(2, 10), out, t(2, 12))
    private val day: LocalDate = LocalDate.of(2026, 3, 2)

    private fun seat(id: String, f: Flight, role: String, who: Employee?) =
        FlightAssignment(id, f, 0, role).apply { employee = who }

    private fun check(v: ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule>) =
        v.verifyThat(FlightCrewSchedulingConstraintProvider::reserveCrewsPerBase)

    @Test
    fun `все свободны - ноль, экипаж занят - нехватка один`() {
        val v = verifierWith(norm("PLY", 2.0))
        check(v).given(c1, c2, f1, f2, x, seat("xc", x, "Captain", null), seat("xf", x, "FirstOfficer", null))
            .penalizes(0)
        check(v).given(c1, c2, f1, f2, x, seat("xc", x, "Captain", c1), seat("xf", x, "FirstOfficer", f1))
            .penalizesBy(1)
    }

    @Test
    fun `счёт по ролям, не «любые двое» - один занятый командир уже рушит экипаж`() {
        // Свободны командир C2 и оба вторых: экипажей = min(1, 2) = 1 < 2.
        val v = verifierWith(norm("PLY", 2.0))
        check(v).given(c1, c2, f1, f2, x, seat("xc", x, "Captain", c1), seat("xf", x, "FirstOfficer", null))
            .penalizesBy(1)
    }

    @Test
    fun `отпуск в сутки - не резерв`() {
        val v = verifierWith(norm("PLY", 2.0))
        f1.unavailableDays = mutableListOf(day)
        try {
            // Никто не летит, но F1 недоступен: вторых свободно 1 → экипажей 1.
            check(v).given(c1, c2, f1, f2, x, seat("xc", x, "Captain", null), seat("xf", x, "FirstOfficer", null))
                .penalizesBy(1)
        } finally {
            f1.unavailableDays = mutableListOf()
        }
    }

    @Test
    fun `роль без свободных людей - нулевая клетка даёт нехватку, а не молчит`() {
        // На базе один командир и ни одного второго пилота: экипажей 0 из 2.
        val v = verifierWith(norm("PLY", 2.0))
        check(v).given(c1, x, seat("xc", x, "Captain", null), seat("xf", x, "FirstOfficer", null))
            .penalizesBy(2)
    }

    @Test
    fun `база названа нормой, людей у неё нет - честная нехватка на каждые сутки`() {
        val v = verifierWith(norm("GRT", 1.0))
        check(v).given(c1, c2, f1, f2, x, seat("xc", x, "Captain", null), seat("xf", x, "FirstOfficer", null))
            .penalizesBy(1)
    }

    @Test
    fun `declared reserve horizon neither shrinks to flights nor extends for spillover arrival`() {
        // K-25 (стенд GPT): объявленное окно 04.03..01.04 при единственном
        // рейсе 31.03 23:00 → 01.04 02:00 у базы без людей — ровно 28 суток
        // нехватки: окно не сужается к рейсам и не расширяется на прилёт.
        val v = verifierWith(norm("GRT", 1.0))
        val edge = flight("EDGE", ply, t(31, 23), out,
            java.time.LocalDateTime.of(2026, 4, 1, 2, 0))
        val declared = org.acme.flighcrewscheduling.domain.AccountingWindow().apply {
            start_inclusive = "2026-03-04"; end_exclusive = "2026-04-01"
        }
        check(v).given(c1, edge, declared, seat("edge", edge, "Captain", null)).penalizesBy(28)
    }

    @Test
    fun `перегонное кресло - вне состава, пустая роль не рушит резерв`() {
        // Класс 2026-08-25: кресло решателя с requiredSkill="" попадало в
        // роли состава; клетка (база, день, «») не имеет свободных ни у
        // кого, и min по ролям навсегда ноль — недостача резерва была
        // ПОЛНОЙ константой без градиента на всех длинных прогонах полного.
        val v = verifierWith(norm("PLY", 1.0))
        val dh = FlightAssignment("dh", x, 101, "").apply { deadhead = true }
        check(v).given(c1, c2, f1, f2, x, dh,
            seat("xc", x, "Captain", null), seat("xf", x, "FirstOfficer", null))
            .penalizes(0)
        // Занятое перегоном кресло занимает ЧЕЛОВЕКА (день перегона — работа):
        // c1 уехал пассажиром — свободных командиров нет, нехватка один.
        val dhBusy = FlightAssignment("dh2", x, 102, "").apply {
            deadhead = true; employee = c1
        }
        check(v).given(c1, f1, x, dhBusy,
            seat("xc", x, "Captain", null), seat("xf", x, "FirstOfficer", null))
            .penalizesBy(1)
    }

    @Test
    fun `норма один при одном занятом экипаже - ноль`() {
        val v = verifierWith(norm("PLY", 1.0))
        check(v).given(c1, c2, f1, f2, x, seat("xc", x, "Captain", c1), seat("xf", x, "FirstOfficer", f1))
            .penalizes(0)
    }

    @Test
    fun `вторые сутки горизонта считаются отдельно - штраф только за занятые`() {
        // Y — рейс 3 марта с пустыми местами: горизонт 2–3 марта; занят
        // экипаж только 2 марта → нехватка 1 за 2 марта, 0 за 3 марта.
        val y = flight("Y", ply, t(3, 10), out, t(3, 12))
        val v = verifierWith(norm("PLY", 2.0))
        check(v).given(c1, c2, f1, f2, x, y,
            seat("xc", x, "Captain", c1), seat("xf", x, "FirstOfficer", f1),
            seat("yc", y, "Captain", null), seat("yf", y, "FirstOfficer", null))
            .penalizesBy(1)
    }

    @Test
    fun `рейс через полночь занимает обе даты, но сутки горизонта — по вылету`() {
        // Единый план 05.09.2026 (день 1, находка аудитора 5.2): сутки
        // резерва — от первого до последнего ВЫЛЕТА; прилёт 3-го после
        // полуночи не создаёт суток резерва 3-го марта. Занятость экипажа
        // при этом по-прежнему покрывает обе даты (см. следующий стенд).
        val night = flight("N", ply, t(2, 23), out, t(3, 1))
        val v = verifierWith(norm("PLY", 2.0))
        check(v).given(c1, c2, f1, f2, night,
            seat("nc", night, "Captain", c1), seat("nf", night, "FirstOfficer", f1))
            .penalizesBy(1)
    }

    @Test
    fun `рейс через полночь занимает экипаж и на дату прилёта`() {
        // Дневной рейс 3-го держит сутки 3-го в горизонте; ночной рейс со
        // 2-го на 3-е занимает тот же экипаж и 3-го — нехватка 3-го такая
        // же, как 2-го.
        val night = flight("N", ply, t(2, 23), out, t(3, 1))
        val day3 = flight("D3", ply, t(3, 15), out, t(3, 17))
        val v = verifierWith(norm("PLY", 2.0))
        check(v).given(c1, c2, f1, f2, night, day3,
            seat("nc", night, "Captain", c1), seat("nf", night, "FirstOfficer", f1),
            seat("dc", day3, "Captain", null), seat("df", day3, "FirstOfficer", null))
            .penalizesBy(2)
    }

    @Test
    fun `база без нормы не считается`() {
        val s1 = crew("S1", "SVG", "Captain")
        val v = verifierWith(norm("PLY", 2.0))
        check(v).given(c1, c2, f1, f2, s1, x,
            seat("xc", x, "Captain", s1), seat("xf", x, "FirstOfficer", null))
            .penalizes(0)
    }

    @Test
    fun `нарушить правило не выгодно - лестница жёстких весов упорядочена`() {
        // Веса — компас поиска, а не нормы заказчика, и назначаются так,
        // чтобы нарушение не окупалось: физически невозможное (человек на
        // двух рейсах) дороже неукомплектованного места, а место дороже
        // недостающего экипажа резерва. Проверяется отношение, а не числа:
        // тест не должен краснеть от смены масштаба, только от смены
        // ПОРЯДКА (класс «пришпилка к числу»).
        val conflict = DefaultProviderRules.HARD_FLIGHT_CONFLICT_PER_HOUR
        val seat = DefaultProviderRules.HARD_UNASSIGNED_SLOT
        val reserve = DefaultProviderRules.HARD_RESERVE_PER_CREW_DAY
        val crewSize = DefaultProviderRules.HARD_CREW_SIZE_PER_PERSON
        assertTrue(conflict > seat,
            "раздвоить человека ($conflict) обязано быть дороже пустого места ($seat)")
        assertTrue(seat > reserve,
            "пустое место ($seat) дороже недостающего экипажа резерва ($reserve): " +
            "программа полётов выше резерва")
        assertTrue(conflict > reserve + seat + crewSize,
            "раздвоение ($conflict) обязано быть дороже ЛЮБОЙ выгоды от него: " +
            "экипаж резерва + место + численность = ${reserve + seat + crewSize}")
        // Отчёт и счёт — один источник: каждый вес объявлен в hardWeights.
        val w = DefaultProviderRules.hardWeights()
        assertEquals(conflict, w["Flight conflict (per hour of overlap)"])
        assertEquals(reserve, w["Reserve crews (per crew-day)"])
        assertEquals(seat, w["Unassigned slot"])
    }

    @Test
    fun `вне окна порогов правило выключено`() {
        val off = ConstraintVerifier.build(FlightCrewSchedulingConstraintProvider(),
            FlightCrewSchedule::class.java, FlightAssignment::class.java)
        off.verifyThat(FlightCrewSchedulingConstraintProvider::reserveCrewsPerBase)
            .given(c1, c2, f1, f2, x, seat("xc", x, "Captain", c1), seat("xf", x, "FirstOfficer", f1))
            .penalizes(0)
    }
}
