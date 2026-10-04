package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.config.solver.termination.TerminationConfig
import org.acme.flighcrewscheduling.domain.Airport
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

/**
 * Шаг 4 плана 05.09.2026: ходы по цепочкам и начальное решение по
 * цепочкам. Стенды: раздача даёт каждой цепочке ОДНОГО человека с нужным
 * навыком, свою базу раньше чужой, без пересечения по времени; фабрика
 * ходов даёт переносы и обмены целыми цепочками; поиск с moves=chain на
 * малом наборе держит целостность цепочек и непрерывность маршрута.
 */
class ChainMovesTest {

    private val ply = airport("PLY")
    private val svg = airport("SVG")
    private val out = airport("OUT")

    private fun crewman(id: String, home: Airport): Employee =
        employee(id).apply { homeAirport = home }

    private fun seat(id: String, f: Flight, chain: String?): FlightAssignment =
        FlightAssignment(id, f, 0, Fixtures.SKILL).apply { chainId = chain }

    /** Две цепочки из PLY (день 2 и день 4), одна из SVG (день 2). */
    private fun world(): FlightCrewSchedule {
        val f1 = flight("F1", ply, t(2, 8), out, t(2, 10))
        val f2 = flight("F2", out, t(2, 12), ply, t(2, 14))
        val f3 = flight("F3", ply, t(4, 8), out, t(4, 10))
        val f4 = flight("F4", out, t(4, 12), ply, t(4, 14))
        val f5 = flight("F5", svg, t(2, 9), out, t(2, 11))
        val f6 = flight("F6", out, t(2, 13), svg, t(2, 15))
        val s = FlightCrewSchedule()
        s.airports = mutableListOf(ply, svg, out)
        s.employees = mutableListOf(crewman("P1", ply), crewman("P2", ply), crewman("S1", svg))
        s.flights = mutableListOf(f1, f2, f3, f4, f5, f6)
        s.flightAssignments = mutableListOf(
            seat("a1", f1, "c-A"), seat("a2", f2, "c-A"),
            seat("b1", f3, "c-B"), seat("b2", f4, "c-B"),
            seat("c1", f5, "c-C"), seat("c2", f6, "c-C"))
        return s
    }

    @Test
    fun `раздача — каждой цепочке один человек своей базы, без пересечений`() {
        val s = world()
        assertEquals(3, ChainSeeding.seed(s).seeded)
        val by = s.flightAssignments.groupBy { it.chainId!! }
        for ((_, seats) in by) {
            assertEquals(1, seats.map { it.employee!!.id }.toSet().size)
        }
        val a = by["c-A"]!![0].employee!!
        val c = by["c-C"]!![0].employee!!
        assertEquals("PLY", a.homeAirport!!.code)
        assertEquals("S1", c.id)
        // цепочка B (день 4) — тоже PLY; кто именно, решает нагрузка
        assertEquals("PLY", by["c-B"]!![0].employee!!.homeAirport!!.code)
    }

    @Test
    fun `закреплённая цепочка не двигается, повторная раздача ничего не меняет`() {
        val s = world()
        s.flightAssignments.filter { it.chainId == "c-C" }.forEach {
            it.employee = s.employees[0]; it.pinned = true
        }
        assertTrue(ChainMoves.chains(s).keys == setOf("c-A", "c-B"))
        assertEquals(2, ChainSeeding.seed(s).seeded)
        assertEquals(0, ChainSeeding.seed(s).seeded)
    }

    @Test
    fun `фабрика даёт переносы целых цепочек и обмены`() {
        val s = world()
        ChainSeeding.seed(s)
        val moves = ChainMoveFactory().createMoveList(s)
        val changes = moves.filterIsInstance<ChainChangeMove>()
        val swaps = moves.filterIsInstance<ChainSwapMove>()
        // K-30 (4): переносы только к совместимым по соседям людям —
        // A→P2, B→P1 (оба PLY, разные дни) и три освобождения = 5; переносы
        // к S1 (SVG, тот же день 2 — разрыв станций/пересечение) и C→P1/P2
        // (SVG ≠ PLY) фабрика не предлагает.
        assertEquals(5, changes.size)
        assertTrue(swaps.isNotEmpty())
        assertTrue(changes.all { it.planningEntities.size == 2 })
        assertTrue(moves.all { it.describe().isNotBlank() })
        // (3) предложение атомарно: каждая цепочка в ходе — целиком.
        val byChain = s.flightAssignments.groupBy { it.chainId!! }.mapValues { it.value.toSet() }
        for (m in moves) {
            val touched = m.planningEntities.map { it as FlightAssignment }.toSet()
            for (c in touched.map { it.chainId!! }.toSet()) assertTrue(byChain[c]!!.all { it in touched },
                "ход делит цепочку " + c + ": " + m.describe())
        }
    }

    // ------------------------------------------- гарантии K-30 единой версии

    @Test
    fun `один человек пригоден для каждого места цепочки — иначе цепочка не раздаётся ему`() {
        val s = world()
        // Второе место цепочки C требует роли, которой нет ни у кого:
        // раздача и фабрика не отдают C никому (fallback — самому
        // свободному с навыком — тоже невозможен: навыка нет).
        s.flightAssignments.first { it.id == "c2" }.requiredSkill = "OtherRole"
        val r = ChainSeeding.seed(s)
        assertEquals(2, r.seeded)
        // K-30-c: цепочка без пригодного кандидата названа в итоге явно.
        assertEquals(listOf("c-C"), r.unseeded)
        assertTrue(r.fallback.isEmpty())
        assertTrue(s.flightAssignments.filter { it.chainId == "c-C" }.all { it.employee == null })
        assertTrue(ChainMoveFactory().createMoveList(s)
            .filterIsInstance<ChainChangeMove>().none { it.describe().startsWith("цепочка c-C") })
        // Недоступность по дате тоже делает человека непригодным для всей цепочки.
        val t2 = world()
        t2.employees.first { it.id == "S1" }.unavailableDays = mutableListOf(java.time.LocalDate.of(2026, 3, 2))
        ChainSeeding.seed(t2)
        assertTrue(t2.flightAssignments.filter { it.chainId == "c-C" }.map { it.employee!!.id }.none { it == "S1" })
    }

    @Test
    fun `конфликт пинов — отказ до изменения значений, один пин якорит цепочку, пустой пин держит её пустой`() {
        val s = world()
        val cSeats = s.flightAssignments.filter { it.chainId == "c-C" }
        cSeats[0].apply { employee = s.employees[0]; pinned = true }
        cSeats[1].apply { employee = s.employees[1]; pinned = true }
        val before = s.flightAssignments.map { it.employee?.id }
        val e = assertThrows<TaskModeRefused> { ChainSeeding.seed(s) }
        assertTrue(e.message!!.contains("противоречат"), e.message)
        assertEquals(before, s.flightAssignments.map { it.employee?.id })
        // Один пин с человеком якорит незакреплённые места цепочки.
        val a = world()
        val aSeats = a.flightAssignments.filter { it.chainId == "c-A" }
        aSeats[0].apply { employee = a.employees[1]; pinned = true }
        ChainSeeding.seed(a)
        assertTrue(aSeats.all { it.employee === a.employees[1] })
        // Пустой пин держит цепочку пустой — раздача её не трогает.
        val p = world()
        val pSeats = p.flightAssignments.filter { it.chainId == "c-B" }
        pSeats[0].apply { employee = null; pinned = true }
        assertEquals(2, ChainSeeding.seed(p).seeded)
        assertTrue(pSeats.all { it.employee == null })
    }

    @Test
    fun `вставка и удаление проверяют обоих соседей — станции и отдых`() {
        val rules = ChainSearchRules()
        val e = crewman("P1", ply)
        val before = FlightAssignment("before", flight("before", ply, t(2, 6), out, t(2, 7)), 0, Fixtures.SKILL).apply { employee = e }
        val middle = FlightAssignment("middle", flight("middle", out, t(2, 9), svg, t(2, 10)), 0, Fixtures.SKILL).apply { employee = e; chainId = "mid" }
        val after = FlightAssignment("after", flight("after", svg, t(2, 12), ply, t(2, 13)), 0, Fixtures.SKILL).apply { employee = e }
        val timeline = listOf(before, middle, after)
        // Удаление моста сводит соседей OUT ≠ SVG — отказ.
        assertFalse(ChainRules.transitionOk(e, timeline, listOf(before, after), emptyList(), rules))
        // Вставка того же моста между ними — принимается.
        assertTrue(ChainRules.transitionOk(e, listOf(before, after), timeline, listOf(middle), rules))
        // Шестичасовой разрыв — мёртвая зона профиля (6 ч ≤ разрыв < 8 ч): отказ.
        val night = FlightAssignment("n", flight("n", ply, t(3, 15), out, t(3, 16)), 0, Fixtures.SKILL)
        val late = FlightAssignment("l", flight("l", out, t(3, 22), ply, t(3, 23)), 0, Fixtures.SKILL)
        assertFalse(ChainRules.transitionOk(e, emptyList(), listOf(night, late), listOf(night, late), rules))
        // Тот же разрыв при необъявленном порозе пакета (0) — законная стыковка.
        assertTrue(ChainRules.transitionOk(e, emptyList(), listOf(night, late), listOf(night, late),
            rules.copy(dayBreakThreshold = 0, packaged = true)))
        // Фабрика: перенос, сводящий чужих соседей у отдающего, не предлагается.
        val s = FlightCrewSchedule().apply {
            airports = mutableListOf(ply, svg, out)
            employees = mutableListOf(e, crewman("P2", ply))
            flights = mutableListOf(before.flight!!, middle.flight!!, after.flight!!)
            flightAssignments = mutableListOf(before, middle, after)
        }
        assertTrue(ChainMoveFactory().createMoveList(s).isEmpty())
    }

    @Test
    fun `явное местоположение из истории — не дом и не телепорт`() {
        val rules = ChainSearchRules()
        val e = crewman("P1", ply)
        e.historyAsOf = t(2, 0).toString()
        e.history = org.acme.flighcrewscheduling.domain.EmployeeHistory().apply { historyLocation = out.code }
        val fromHome = FlightAssignment("h", flight("h", ply, t(2, 8), out, t(2, 9)), 0, Fixtures.SKILL)
        assertFalse(ChainRules.transitionOk(e, emptyList(), listOf(fromHome), listOf(fromHome), rules))
        val fromOut = FlightAssignment("o", flight("o", out, t(2, 8), ply, t(2, 9)), 0, Fixtures.SKILL)
        assertTrue(ChainRules.transitionOk(e, emptyList(), listOf(fromOut), listOf(fromOut), rules))
        // Без истории правила «старт цепочки = база» нет.
        assertTrue(ChainRules.transitionOk(crewman("P2", ply), emptyList(), listOf(fromOut), listOf(fromOut), rules))
        // Отдых после конца последней смены истории обязан быть выдержан.
        val rested = crewman("P3", ply).apply {
            historyAsOf = t(2, 0).toString()
            history = org.acme.flighcrewscheduling.domain.EmployeeHistory().apply {
                historyLastDutyEnd = t(2, 4).toString(); historyLastDutyStation = ply.code
            }
        }
        assertFalse(ChainRules.transitionOk(rested, emptyList(), listOf(fromHome), listOf(fromHome), rules))
        val later = FlightAssignment("x", flight("x", ply, t(2, 13), out, t(2, 14)), 0, Fixtures.SKILL)
        assertTrue(ChainRules.transitionOk(rested, emptyList(), listOf(later), listOf(later), rules))
    }

    @Test
    fun `необязательное перегонное кресло не заполняется жадно, тёплый старт с разорванной цепочкой — отказ`() {
        val s = world()
        val f7 = flight("F7", ply, t(6, 8), out, t(6, 10))
        val optional = FlightAssignment("dh", f7, 101, "").apply { deadhead = true }
        s.flights.add(f7); s.flightAssignments.add(optional)
        assertEquals(3, ChainSeeding.seed(s).seeded)
        assertEquals(null, optional.employee)
        // (7) цепочка, разорванная между людьми в присланном решении, — отказ словами.
        val w = world()
        w.flightAssignments.first { it.id == "a1" }.employee = w.employees[0]
        w.flightAssignments.first { it.id == "a2" }.employee = w.employees[1]
        val e = assertThrows<TaskModeRefused> { ChainSeeding.refuseSplitChains(w) }
        assertTrue(e.message!!.contains("c-A"), e.message)
        w.flightAssignments.first { it.id == "a2" }.employee = w.employees[0]
        ChainSeeding.refuseSplitChains(w)
    }

    @Test
    fun `тёплый старт с разорванной цепочкой — отказ`() {
        // K-30-d: «пусто» — отдельное значение: a1 = E1, a2 = пусто — цепочка
        // разорвана, отказ называет цепочку и «пусто».
        val w = world()
        w.flightAssignments.first { it.id == "a1" }.employee = w.employees[0]
        val e = assertThrows<TaskModeRefused> { ChainSeeding.refuseSplitChains(w) }
        assertTrue(e.message!!.contains("c-A"), e.message)
        assertTrue(e.message!!.contains("пусто"), e.message)
        assertTrue(e.message!!.contains("P1"), e.message)
        // Полностью пустая и полностью занятая одним человеком — не разрыв.
        w.flightAssignments.first { it.id == "a2" }.employee = w.employees[0]
        ChainSeeding.refuseSplitChains(w)
        ChainSeeding.refuseSplitChains(world())
        // Закрепление: пустое незакреплённое место рядом с пином якорится
        // раздачей — разрывом не считается; два разных человека — разрыв.
        val p = world()
        p.flightAssignments.first { it.id == "b1" }.apply { employee = p.employees[1]; pinned = true }
        ChainSeeding.refuseSplitChains(p)
        p.flightAssignments.first { it.id == "b2" }.employee = p.employees[0]
        assertTrue(assertThrows<TaskModeRefused> { ChainSeeding.refuseSplitChains(p) }.message!!.contains("c-B"))
    }

    @Test
    fun `нет совместимого по соседям — fallback раздачи явный, не успешная раздача`() {
        // K-30-c: один человек, две цепочки одного дня со стыковкой 10,5 ч в
        // OUT при пороге стыковки вне базы 11 ч (снимок порогов прогона):
        // вторая цепочка достаётся пригодному, но непроверенному по соседям
        // кандидату — она в fallback, не в checked; при пороге 0 (пакет
        // молчит) обе проверены.
        fun pair(rules: ChainSearchRules): FlightCrewSchedule {
            val f1 = flight("F1", ply, t(2, 8), out, t(2, 10))
            val f2 = flight("F2", out, t(2, 20, 30), ply, t(2, 22, 30))
            return FlightCrewSchedule().apply {
                airports = mutableListOf(ply, out)
                employees = mutableListOf(crewman("P1", ply))
                flights = mutableListOf(f1, f2)
                flightAssignments = mutableListOf(seat("a1", f1, "c-A"), seat("b1", f2, "c-B"))
                layoverDutyRules = rules
            }
        }
        val strict = pair(ChainSearchRules.from(emptyMap()).copy(minSitOut = 660))
        val r = ChainSeeding.seed(strict)
        assertEquals(SeedResult(2, 1, listOf("c-B"), emptyList()), r)
        assertEquals(listOf("c-B"), r.reading()["fallback"])
        assertEquals(1, r.reading()["checked"])
        assertTrue(strict.flightAssignments.all { it.employee?.id == "P1" })
        assertEquals(SeedResult(2, 2, emptyList(), emptyList()),
            ChainSeeding.seed(pair(ChainSearchRules.from(emptyMap()))))
        assertEquals(SeedResult.EMPTY, ChainSeeding.seed(FlightCrewSchedule()))
    }

    @Test
    fun `откат ChainChangeMove и ChainSwapMove возвращает назначения и счёт`() {
        // K-30-e: атомарность и undo — временный ход через директор счёта
        // (executeTemporaryMove исполняет ход и откатывает его): во время
        // хода цепочка целиком у нового человека, после отката назначения
        // всех мест и счёт равны исходным.
        val s = world()
        ChainSeeding.seed(s)
        val director = scoreDirector(s)
        try {
            val before = s.flightAssignments.map { it.employee?.id }
            val score = director.calculateScore().raw()
            val chains = ChainMoves.chains(s)
            val a = chains["c-A"]!!
            val b = chains["c-B"]!!
            val s1 = s.employees.first { it.id == "S1" }
            var applied = false
            director.executeTemporaryMove(ChainChangeMove("c-A", a, s1),
                java.util.function.Consumer<ai.timefold.solver.core.preview.api.move.SolutionView<FlightCrewSchedule>> {
                    applied = a.all { it.employee === s1 }
                }, false)
            assertTrue(applied, "ход по цепочке не исполнился целиком")
            assertEquals(before, s.flightAssignments.map { it.employee?.id })
            assertEquals(score, director.calculateScore().raw())
            val holderA = a[0].employee
            val holderB = b[0].employee
            applied = false
            director.executeTemporaryMove(ChainSwapMove("c-A", a, "c-B", b),
                java.util.function.Consumer<ai.timefold.solver.core.preview.api.move.SolutionView<FlightCrewSchedule>> {
                    applied = a.all { it.employee === holderB } && b.all { it.employee === holderA }
                }, false)
            assertTrue(applied, "обмен цепочек не исполнился целиком")
            assertEquals(before, s.flightAssignments.map { it.employee?.id })
            assertEquals(score, director.calculateScore().raw())
        } finally {
            director.close()
        }
    }

    /** Директор счёта без поиска: рабочее решение — [s] как есть. */
    private fun scoreDirector(s: FlightCrewSchedule) = (SolverFactory.create<FlightCrewSchedule>(SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java))
        as ai.timefold.solver.core.impl.solver.DefaultSolverFactory<FlightCrewSchedule>)
        .getScoreDirectorFactory<ai.timefold.solver.core.api.score.HardSoftScore>()
        .createScoreDirectorBuilder().build().also { it.setWorkingSolution(s) }

    @Test
    fun `штатный ход по месту цепочки отвергнут фильтром, по свободному месту — принят`() {
        // K-30-b: гарантии цепочек — на все семейства ходов режима chain:
        // обычные Change/Swap из LargeMoves идут через фильтры.
        val s = world()
        ChainSeeding.seed(s)
        val f7 = flight("F7", ply, t(6, 8), out, t(6, 10))
        val free = FlightAssignment("free", f7, 0, Fixtures.SKILL)
        s.flights.add(f7); s.flightAssignments.add(free)
        val a1 = s.flightAssignments.first { it.id == "a1" }
        val p2 = s.employees.first { it.id == "P2" }
        // Правила без Timefold: место цепочки одиночным ходом не ходит
        // (ни к человеку, ни в пусто); свободное место при пригодности и
        // соседстве проходит; обмен с местом цепочки — отказ.
        assertFalse(ChainSeatGuard.changeOk(s, a1, p2))
        assertFalse(ChainSeatGuard.changeOk(s, a1, null))
        assertTrue(ChainSeatGuard.changeOk(s, free, p2))
        assertFalse(ChainSeatGuard.swapOk(s, a1, free))
        // Свободное место, рвущее соседство у получателя (OUT→SVG внутри
        // дня 4 держателя цепочки B: пересечение по времени), — отказ.
        val broken = FlightAssignment("brk", flight("F8", out, t(4, 9), svg, t(4, 10)), 0, Fixtures.SKILL)
        s.flights.add(broken.flight!!); s.flightAssignments.add(broken)
        val holderB = s.flightAssignments.first { it.chainId == "c-B" }.employee!!
        assertFalse(ChainSeatGuard.changeOk(s, broken, holderB))
        // Настоящие ходы Timefold через фильтры набора moves=chain.
        val director = scoreDirector(s)
        try {
            val vd = director.solutionDescriptor.findEntityDescriptorOrFail(FlightAssignment::class.java)
                .getGenuineVariableDescriptor("employee")
            assertFalse(ChainSeatChangeFilter().accept(director,
                ai.timefold.solver.core.impl.heuristic.selector.move.generic.SelectorBasedChangeMove(vd, a1, p2)))
            assertTrue(ChainSeatChangeFilter().accept(director,
                ai.timefold.solver.core.impl.heuristic.selector.move.generic.SelectorBasedChangeMove(vd, free, p2)))
            val basic = vd as ai.timefold.solver.core.impl.domain.variable.descriptor.BasicVariableDescriptor<FlightCrewSchedule>
            assertFalse(ChainSeatSwapFilter().accept(director,
                ai.timefold.solver.core.impl.heuristic.selector.move.generic.SelectorBasedSwapMove(listOf(basic), a1, free)))
            // Обмен двух свободных мест разных людей: пригодность и соседство.
            val free2 = FlightAssignment("free2", flight("F9", ply, t(8, 8), out, t(8, 10)), 0, Fixtures.SKILL)
                .apply { employee = s.employees.first { it.id == "P1" } }
            free.employee = p2
            s.flights.add(free2.flight!!); s.flightAssignments.add(free2)
            assertTrue(ChainSeatSwapFilter().accept(director,
                ai.timefold.solver.core.impl.heuristic.selector.move.generic.SelectorBasedSwapMove(listOf(basic), free, free2)))
        } finally {
            director.close()
        }
        // Фильтры навешены на штатные ходы обоих наборов chain.
        for (name in listOf("chain", "chain-seed")) {
            val union = LargeMoves.lsPhase(name)!!.moveSelectorConfig
                as ai.timefold.solver.core.config.heuristic.selector.move.composite.UnionMoveSelectorConfig
            assertEquals(ChainSeatChangeFilter::class.java, union.moveSelectorList!![0].filterClass)
            assertEquals(ChainSeatSwapFilter::class.java, union.moveSelectorList!![1].filterClass)
        }
    }

    @Test
    fun `ruin-recreate в режимах цепочек отключён — отказ словами, штатные наборы без него`() {
        // K-FIX-R1 (ревью GPT-029): фильтр отбора видит ход ДО исполнения, а
        // достройка выбитых мест идёт вложенным конструктивом внутри хода —
        // пригодность получателя и соседство достроенных мест (гарантия
        // changeOk/swapOk) фильтр судить не может, защита одних мест с
        // меткой не защищает их соседей. Семейство в режимах цепочек не
        // включается: имена -rr отвергаются словами с названием замены.
        for (name in listOf("chain-rr", "chain-seed-rr", "CHAIN-RR")) {
            val e = assertThrows<TaskModeRefused> { LargeMoves.lsPhase(name) }
            assertTrue(e.message!!.contains("ruin-recreate"), e.message)
            assertTrue(e.message!!.contains("chain-seed"), e.message)
            assertTrue(e.message!!.contains("отключён"), e.message)
        }
        assertFalse(LargeMoves.seedsChains("chain-seed-rr"))
        assertTrue(LargeMoves.seedsChains("chain-seed"))
        assertTrue(LargeMoves.chainMode("chain") && LargeMoves.chainMode("chain-seed"))
        assertFalse(LargeMoves.chainMode("pillar") || LargeMoves.chainMode(null))
        // Штатные наборы цепочек: ровно три семейства, без ruin-recreate.
        for (name in listOf("chain", "chain-seed")) {
            val union = LargeMoves.lsPhase(name)!!.moveSelectorConfig
                as ai.timefold.solver.core.config.heuristic.selector.move.composite.UnionMoveSelectorConfig
            assertEquals(3, union.moveSelectorList!!.size)
            assertTrue(union.moveSelectorList!!.none {
                it is ai.timefold.solver.core.config.heuristic.selector.move.generic.RuinRecreateMoveSelectorConfig })
        }
        // Оракул на живом поиске chain: места цепочек после серии шагов —
        // у одного человека каждая (разрушаемого множества с меткой нет).
        val s = world()
        ChainSeeding.seed(s)
        val f7 = flight("F7", ply, t(6, 8), out, t(6, 10))
        s.flights.add(f7); s.flightAssignments.add(FlightAssignment("free", f7, 0, Fixtures.SKILL))
        val cfg = SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
            .withPhases(LargeMoves.lsPhase("chain")!!)
            .withTerminationConfig(TerminationConfig().withStepCountLimit(30))
        val solved = SolverFactory.create<FlightCrewSchedule>(cfg).buildSolver().solve(s)
        for ((c, seats) in solved.flightAssignments.filter { it.chainId != null }.groupBy { it.chainId!! }) {
            assertEquals(1, seats.map { it.employee?.id }.toSet().size, "цепочка " + c + " разорвана")
        }
        // pillar-rr не задет: ruin-recreate остаётся у режимов опоры.
        val pillar = LargeMoves.lsPhase("pillar-rr")!!.moveSelectorConfig
            as ai.timefold.solver.core.config.heuristic.selector.move.composite.UnionMoveSelectorConfig
        assertTrue(pillar.moveSelectorList!!.any {
            it is ai.timefold.solver.core.config.heuristic.selector.move.generic.RuinRecreateMoveSelectorConfig })
    }

    @Test
    fun `поиск с ходами по цепочкам держит целостность цепочек`() {
        val s = world()
        ChainSeeding.seed(s)
        val cfg = SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
            .withPhases(LargeMoves.lsPhase("chain")!!)
            .withTerminationConfig(TerminationConfig().withStepCountLimit(40))
        val factory = SolverFactory.create<FlightCrewSchedule>(cfg)
        val manager = ai.timefold.solver.core.api.solver.SolutionManager.create(factory)
        manager.update(s)
        val startHard = s.score!!.hardScore()
        val solved = factory.buildSolver().solve(s)
        val by = solved.flightAssignments.groupBy { it.chainId!! }
        for ((_, seats) in by) {
            assertEquals(1, seats.map { it.employee?.id }.toSet().size,
                "цепочка разорвана между людьми: " + seats.map { it.employee?.id })
        }
        assertTrue(solved.score!!.hardScore() >= startHard,
            "поиск ухудшил жёсткие: " + startHard + " → " + solved.score)
    }
}
