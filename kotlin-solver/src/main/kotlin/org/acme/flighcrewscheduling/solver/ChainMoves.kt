package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.impl.heuristic.selector.common.decorator.SelectionFilter
import ai.timefold.solver.core.impl.heuristic.selector.move.factory.MoveListFactory
import ai.timefold.solver.core.impl.heuristic.selector.move.generic.SelectorBasedChangeMove
import ai.timefold.solver.core.impl.heuristic.selector.move.generic.SelectorBasedSwapMove
import ai.timefold.solver.core.impl.score.director.ScoreDirector
import ai.timefold.solver.core.preview.api.domain.metamodel.PlanningVariableMetaModel
import ai.timefold.solver.core.preview.api.move.Move
import ai.timefold.solver.core.preview.api.move.MutableSolutionView
import java.time.Duration
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit
import java.util.logging.Logger
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule

/**
 * Групповые ходы по цепочкам (план 05.09.2026, шаг 4; наблюдение аудитора
 * по H0-КИ-окна): ростеринг на утверждённом пейринге двигал по одному
 * месту, а целостность цепочки («Pairing chain integrity») и
 * непрерывность маршрута делают одиночный ход всегда убыточным — поиск
 * мёрз на −465…−498 жёстких при 21 разрыве маршрута. Единица хода здесь
 * — ЦЕПОЧКА: все места одной метки chainId переезжают к одному
 * сотруднику разом (ChainChangeMove) либо две цепочки меняются людьми
 * (ChainSwapMove). Закреплённые места (pinned) не трогаются: цепочка с
 * закреплённым местом не двигается вовсе.
 *
 * Включение — ТОЛЬКО параметром прогона (moves=chain | chain-seed),
 * умолчание не задето (решение владельца 2026-08-25 о крупных ходах);
 * «разрушить и собрать» в режимах цепочек отключено (K-FIX-R1, см.
 * [LargeMoves]).
 * Начальное решение — [ChainSeeding]: цепочки раздаются людям жадно, и
 * поиск стартует с целых цепочек, а не с пустоты.
 *
 * Гарантии K-30 единой версии (из ChainRosterSearch GPT, сам алгоритм
 * не переносится — §3.6/§3.10.2 плана): [ChainRules] — один человек
 * пригоден для КАЖДОГО места цепочки; закрепления судятся до мутации,
 * один пин якорит всю цепочку, пустой пин держит её пустой; предложение
 * атомарно (цепочка не делится); вставка и удаление проверяют обоих
 * соседей (станции, стыковка, мёртвая зона, размах смены); явное
 * местоположение из истории — не «дом» и не телепорт; необязательное
 * перегонное кресло не заполняется жадно; тёплый старт, рвущий цепочку,
 * — отказ словами.
 */
object ChainMoves {

    /** Места цепочек, пригодные к ходу: с меткой, не перегон, не закреплены. */
    fun chains(schedule: FlightCrewSchedule): Map<String, List<FlightAssignment>> {
        val byChain = LinkedHashMap<String, MutableList<FlightAssignment>>()
        val frozen = HashSet<String>()
        for (fa in schedule.flightAssignments) {
            val c = fa.chainId ?: continue
            // Перегонное кресло с меткой цепочки — часть её единицы
            // (метку ставит фиксация пейринга по полю dh каталога).
            if (fa.pinned) { frozen.add(c); continue }
            byChain.getOrPut(c) { ArrayList() }.add(fa)
        }
        frozen.forEach { byChain.remove(it) }
        return byChain
    }

    fun employeeVariable(view: MutableSolutionView<FlightCrewSchedule>):
        PlanningVariableMetaModel<FlightCrewSchedule, FlightAssignment, Employee> {
        @Suppress("UNCHECKED_CAST")
        return view.solutionMetaModel.entity(FlightAssignment::class.java)
            .variable<Employee>("employee")
            as PlanningVariableMetaModel<FlightCrewSchedule, FlightAssignment, Employee>
    }
}

/**
 * Правила пригодности и соседства для раздачи и ходов по цепочкам (K-30):
 * пороги — снимок прогона [ChainSearchRules] на решении
 * (FlightCrewSchedule.layoverDutyRules, ставит RosterLayoverCost.prepare;
 * без пакета — литералы профиля). Проверяется только то, что ход
 * МЕНЯЕТ (новые соседства, первый рейс, смены с изменёнными местами) —
 * чужие, уже существующие нарушения ход не обязан чинить, иначе поиск
 * замерзает на решении конструктива.
 */
object ChainRules {

    /** Полный порядок вылета — тот же ключ, что у нарезки смен провайдера. */
    val ORDER: Comparator<FlightAssignment> = compareBy(
        { it.flight?.departureUTCDateTime }, { it.flight?.departureAirport?.code },
        { it.flight?.arrivalUTCDateTime }, { it.flight?.arrivalAirport?.code },
        { it.flight?.flightNumber }, { it.crewSlotId })

    /** (1) Один человек пригоден для КАЖДОГО места цепочки: доступность по
     *  датам, навык (перегон навыка не требует), языки, допуск по типу с
     *  датированными окнами (судится по правилу флота прогона). */
    fun eligible(seats: List<FlightAssignment>, e: Employee, rules: ChainSearchRules): Boolean =
        seats.all { seat ->
            val f = seat.flight ?: return@all false
            val dep = f.departureUTCDateTime ?: return@all false
            val arr = f.arrivalUTCDateTime ?: return@all false
            if (arr < dep || !e.isAvailable(dep.toLocalDate(), arr.toLocalDate())) return@all false
            if (!seat.deadhead && seat.requiredSkill.isNotEmpty() && !e.hasSkill(seat.requiredSkill)) return@all false
            if (!seat.deadhead && f.getRequiredLanguages().isNotEmpty() &&
                !e.speaksAny(f.getRequiredLanguages())) return@all false
            val type = f.aircraftType
            val judged = !seat.deadhead && type.isNotEmpty() &&
                (rules.fleetTypes?.contains(type) ?: !rules.packaged)
            if (judged && !e.isQualifiedFor(type)) return@all false
            if (judged && !e.qualificationWindows[type].isNullOrEmpty() &&
                !e.holdsQualification(type, dep.toEpochSecond(ZoneOffset.UTC) / 60,
                    arr.toEpochSecond(ZoneOffset.UTC) / 60)) return@all false
            true
        }

    /** (4) Соседи одного человека a → b: непрерывность станций, стыковка не
     *  короче порога, разрыв не в мёртвой зоне [порог разрыва, отдых). */
    fun neighboursOk(a: FlightAssignment, b: FlightAssignment, e: Employee, rules: ChainSearchRules): Boolean {
        val fa = a.flight ?: return false
        val fb = b.flight ?: return false
        val end = fa.arrivalUTCDateTime ?: return false
        val start = fb.departureUTCDateTime ?: return false
        if (fa.arrivalAirport == null || fa.arrivalAirport != fb.departureAirport) return false
        val gap = ChronoUnit.MINUTES.between(end, start)
        val station = fa.arrivalAirport?.code
        if (gap < 0 || gap < rules.sit(station, e)) return false
        val rest = rules.rest(station, e)
        if (rules.dayBreakThreshold > 0 && gap >= rules.dayBreakThreshold && gap < rest) return false
        return true
    }

    /** (5) Явное местоположение из истории — не «дом» и не телепорт: первый
     *  рейс человека вылетает из станции снимка (historyLocation, иначе
     *  станция конца последней смены), не раньше среза, и после конца
     *  последней смены выдержан отдых. Без истории — ограничения нет
     *  («старт цепочки = база» правилом не является). */
    fun historyOk(e: Employee, first: FlightAssignment, rules: ChainSearchRules): Boolean {
        val f = first.flight ?: return false
        val dep = f.departureUTCDateTime ?: return false
        val asOf = e.historyAsOfAt()
        val historyEnd = e.historyLastDutyEndAt()
        val initialStation = e.history?.historyLocation?.takeIf { it.isNotBlank() }
            ?: e.history?.historyLastDutyStation?.takeIf { historyEnd != null && it.isNotBlank() }
        val locationAt = asOf ?: historyEnd
        if (locationAt != null && dep < locationAt) return false
        if (locationAt != null && initialStation != null && f.departureAirport?.code != initialStation) return false
        if (historyEnd != null &&
            ChronoUnit.MINUTES.between(historyEnd, dep) < rules.rest(e.history?.historyLastDutyStation, e)) return false
        return true
    }

    /**
     * Переход ростера сотрудника before → after допустим: каждое НОВОЕ
     * соседство (вставка ставит места между соседями, удаление сводит
     * прежних соседей) проходит [neighboursOk]; новый первый рейс — по
     * истории; нормативная смена, содержащая изменённое место или новое
     * соседство, не длиннее размаха прогона.
     */
    fun transitionOk(e: Employee, before: List<FlightAssignment>, after: List<FlightAssignment>,
                     changed: Collection<FlightAssignment>, rules: ChainSearchRules): Boolean {
        val sortedAfter = after.sortedWith(ORDER)
        if (sortedAfter.isEmpty()) return true
        val sortedBefore = before.sortedWith(ORDER)
        val oldPairs = sortedBefore.zipWithNext().map { it.first.id to it.second.id }.toHashSet()
        val affected = changed.map { it.id }.toHashSet()
        for ((a, b) in sortedAfter.zipWithNext()) {
            if ((a.id to b.id) in oldPairs) continue
            if (!neighboursOk(a, b, e, rules)) return false
            affected.add(a.id); affected.add(b.id)
        }
        val first = sortedAfter.first()
        if (sortedBefore.firstOrNull()?.id != first.id && !historyOk(e, first, rules)) return false
        if (rules.maxDutySpan > 0 && affected.isNotEmpty()) {
            val duties = NormativeDuties.slice(sortedAfter) { fa -> rules.rest(fa.flight?.arrivalAirport?.code, e) }
            for (d in duties) {
                if (d.none { it.id in affected }) continue
                val span = ChronoUnit.MINUTES.between(
                    d.minOf { it.flight!!.departureUTCDateTime!! }, d.maxOf { it.flight!!.arrivalUTCDateTime!! })
                if (span > rules.maxDutySpan) return false
            }
        }
        return true
    }

    /** Ростеры людей по решению (закреплённые места — тоже занятость). */
    fun timelines(schedule: FlightCrewSchedule): Map<Employee, List<FlightAssignment>> =
        schedule.flightAssignments.filter { it.employee != null && it.flight != null }.groupBy { it.employee!! }
}

/**
 * Гарантии K-30 для ШТАТНЫХ ходов режима (K-30-b единой версии): фильтры
 * ChangeMove / SwapMove / ruin-recreate из [LargeMoves.chainUnion]. Место с
 * меткой цепочки ходит ТОЛЬКО целиком через [ChainMoveFactory] — одиночный
 * ход по нему отвергается; ход по свободному месту проходит те же
 * проверки, что цепочка: пригодность получателя для места и соседство у
 * получателя и у отдающего ([ChainRules], пороги прогона —
 * FlightCrewSchedule.layoverDutyRules). Чужие существующие нарушения ход
 * чинить не обязан (transitionOk судит только новые соседства). Правила
 * — здесь, без Timefold; фильтры ниже лишь разбирают ход.
 */
object ChainSeatGuard {

    /** Одиночный перенос места [seat] к [to] (null — освободить). */
    fun changeOk(schedule: FlightCrewSchedule, seat: FlightAssignment, to: Employee?): Boolean {
        if (seat.chainId != null) return false
        val from = seat.employee
        if (from === to) return true
        val rules = schedule.layoverDutyRules
        val timelines = ChainRules.timelines(schedule)
        if (from != null) {
            val tl = timelines[from].orEmpty()
            if (!ChainRules.transitionOk(from, tl, tl - seat, emptyList(), rules)) return false
        }
        if (to != null) {
            if (!ChainRules.eligible(listOf(seat), to, rules)) return false
            val tl = timelines[to].orEmpty()
            if (!ChainRules.transitionOk(to, tl, tl + seat, listOf(seat), rules)) return false
        }
        return true
    }

    /** Обмен людьми двух мест: после обмена [a] → человек [b], [b] → человек [a]. */
    fun swapOk(schedule: FlightCrewSchedule, a: FlightAssignment, b: FlightAssignment): Boolean {
        if (a.chainId != null || b.chainId != null) return false
        val ea = a.employee
        val eb = b.employee
        if (ea === eb) return true
        val rules = schedule.layoverDutyRules
        val timelines = ChainRules.timelines(schedule)
        if (eb != null) {
            if (!ChainRules.eligible(listOf(a), eb, rules)) return false
            val tl = timelines[eb].orEmpty()
            if (!ChainRules.transitionOk(eb, tl, (tl - b) + a, listOf(a), rules)) return false
        }
        if (ea != null) {
            if (!ChainRules.eligible(listOf(b), ea, rules)) return false
            val tl = timelines[ea].orEmpty()
            if (!ChainRules.transitionOk(ea, tl, (tl - a) + b, listOf(b), rules)) return false
        }
        return true
    }
}

/** Фильтр штатного ChangeMove (набор moves=chain*): см. [ChainSeatGuard.changeOk]. */
class ChainSeatChangeFilter : SelectionFilter<FlightCrewSchedule, Move<FlightCrewSchedule>> {
    override fun accept(scoreDirector: ScoreDirector<FlightCrewSchedule>,
                        move: Move<FlightCrewSchedule>): Boolean {
        val seat: FlightAssignment
        val to: Employee?
        if (move is SelectorBasedChangeMove<*>) {
            seat = move.entity as? FlightAssignment ?: return true
            to = move.toPlanningValue as Employee?
        } else {
            seat = move.planningEntities.firstOrNull() as? FlightAssignment ?: return true
            to = move.planningValues.firstOrNull() as Employee?
        }
        return ChainSeatGuard.changeOk(scoreDirector.workingSolution, seat, to)
    }
}

/** Фильтр штатного SwapMove (набор moves=chain*): см. [ChainSeatGuard.swapOk]. */
class ChainSeatSwapFilter : SelectionFilter<FlightCrewSchedule, Move<FlightCrewSchedule>> {
    override fun accept(scoreDirector: ScoreDirector<FlightCrewSchedule>,
                        move: Move<FlightCrewSchedule>): Boolean {
        val a: FlightAssignment
        val b: FlightAssignment
        if (move is SelectorBasedSwapMove<*>) {
            a = move.leftEntity as? FlightAssignment ?: return true
            b = move.rightEntity as? FlightAssignment ?: return true
        } else {
            val entities = move.planningEntities.toList()
            if (entities.size != 2) return true
            a = entities[0] as? FlightAssignment ?: return true
            b = entities[1] as? FlightAssignment ?: return true
        }
        return ChainSeatGuard.swapOk(scoreDirector.workingSolution, a, b)
    }
}

/** Вся цепочка — к одному сотруднику (null — освободить цепочку). */
class ChainChangeMove(private val chainId: String,
                      private val seats: List<FlightAssignment>,
                      private val to: Employee?) : Move<FlightCrewSchedule> {

    override fun execute(view: MutableSolutionView<FlightCrewSchedule>) {
        val v = ChainMoves.employeeVariable(view)
        for (fa in seats) view.changeVariable<FlightAssignment, Employee>(v, fa, to)
    }

    override fun getPlanningEntities(): java.util.SequencedCollection<Any> =
        java.util.ArrayDeque<Any>(seats)

    override fun getPlanningValues(): java.util.SequencedCollection<Any?> =
        if (to == null) java.util.ArrayDeque<Any?>() else java.util.ArrayDeque<Any?>(listOf(to))

    override fun describe(): String =
        "цепочка " + chainId + " (" + seats.size + " мест) → " + (to?.id ?: "свободна")
}

/** Две цепочки меняются сотрудниками целиком. */
class ChainSwapMove(private val chainA: String, private val seatsA: List<FlightAssignment>,
                    private val chainB: String, private val seatsB: List<FlightAssignment>)
    : Move<FlightCrewSchedule> {

    override fun execute(view: MutableSolutionView<FlightCrewSchedule>) {
        val v = ChainMoves.employeeVariable(view)
        val ea: Employee? = view.getValue<FlightAssignment, Employee>(v, seatsA[0])
        val eb: Employee? = view.getValue<FlightAssignment, Employee>(v, seatsB[0])
        for (fa in seatsA) view.changeVariable<FlightAssignment, Employee>(v, fa, eb)
        for (fa in seatsB) view.changeVariable<FlightAssignment, Employee>(v, fa, ea)
    }

    override fun getPlanningEntities(): java.util.SequencedCollection<Any> =
        java.util.ArrayDeque<Any>(seatsA + seatsB)

    override fun getPlanningValues(): java.util.SequencedCollection<Any?> {
        val out = java.util.ArrayDeque<Any?>()
        seatsA[0].employee?.let { out.add(it) }
        seatsB[0].employee?.let { out.add(it) }
        return out
    }

    override fun describe(): String = "обмен цепочек " + chainA + " ↔ " + chainB
}

/**
 * Список ходов шага: каждая цепочка × каждый сотрудник, ПРИГОДНЫЙ для всех
 * её мест и совместимый по соседям (K-30: вставка проверяет соседей у
 * получателя, удаление — у отдающего), и освобождение цепочки; обмен
 * каждой пары цепочек с разными людьми при пригодности обоих. Кэш на
 * шаг — по умолчанию конфигурации Timefold. Предложение всегда
 * атомарно: цепочка целиком, часть цепочки не двигается.
 */
class ChainMoveFactory : MoveListFactory<FlightCrewSchedule> {

    override fun createMoveList(schedule: FlightCrewSchedule):
        List<Move<FlightCrewSchedule>> {
        val rules = schedule.layoverDutyRules
        val chains = ChainMoves.chains(schedule)
        val timelines = ChainRules.timelines(schedule)
        fun removalOk(from: Employee, seats: List<FlightAssignment>): Boolean {
            val tl = timelines[from].orEmpty()
            return ChainRules.transitionOk(from, tl, tl - seats.toSet(), emptyList(), rules)
        }
        fun insertionOk(to: Employee, seats: List<FlightAssignment>, minus: List<FlightAssignment> = emptyList()): Boolean {
            if (!ChainRules.eligible(seats, to, rules)) return false
            val tl = timelines[to].orEmpty()
            return ChainRules.transitionOk(to, tl, (tl - minus.toSet()) + seats, seats, rules)
        }
        val out = ArrayList<Move<FlightCrewSchedule>>()
        val entries = chains.entries.toList()
        for ((c, seats) in entries) {
            val cur = seats[0].employee
            val skill = (seats.firstOrNull { !it.deadhead } ?: seats[0]).requiredSkill
            val freeable = cur == null || removalOk(cur, seats)
            for (e in schedule.employees) {
                if (e === cur) continue
                if (skill.isNotEmpty() && !e.skills.contains(skill)) continue
                if (!freeable || !insertionOk(e, seats)) continue
                out.add(ChainChangeMove(c, seats, e))
            }
            if (cur != null && freeable) out.add(ChainChangeMove(c, seats, null))
        }
        for (i in entries.indices) {
            val (ca, sa) = entries[i]
            for (j in i + 1 until entries.size) {
                val (cb, sb) = entries[j]
                val ea = sa[0].employee
                val eb = sb[0].employee
                if (ea === eb) continue
                val ska = (sa.firstOrNull { !it.deadhead } ?: sa[0]).requiredSkill
                val skb = (sb.firstOrNull { !it.deadhead } ?: sb[0]).requiredSkill
                if (ska != skb) continue
                // Обмен: у каждого из двоих одна цепочка уходит, другая приходит.
                if (ea != null && !insertionOk(ea, sb, minus = sa)) continue
                if (eb != null && !insertionOk(eb, sa, minus = sb)) continue
                if (ea == null && eb != null && !removalOk(eb, sb)) continue
                if (eb == null && ea != null && !removalOk(ea, sa)) continue
                out.add(ChainSwapMove(ca, sa, cb, sb))
            }
        }
        return out
    }
}

/**
 * Начальное решение для ходов по цепочкам: цепочки в порядке старта
 * раздаются сотрудникам, ПРИГОДНЫМ для всех их мест и совместимым по
 * соседям (K-30) — своя база (первый вылет цепочки = база приписки)
 * раньше чужой, без пересечения по времени с уже розданными цепочками
 * (зазор [REST_GAP]), при равенстве — самый свободный. Не нашлось
 * совместимого — прежний fallback main: самый свободный ПРИГОДНЫЙ (поиск
 * починит ходами), о чём говорит журнал И итог [SeedResult] — такая
 * раздача успешной не считается (K-30-c). Место без метки цепочки не
 * трогается; необязательное перегонное кресло без метки не заполняется.
 */
/**
 * Итог раздачи цепочек (K-30-c единой версии): [seeded] — цепочек
 * получили человека (= [checked] + fallback.size); [checked] — отданы
 * кандидату, проверенному по пригодности И соседству; [fallback] — имена
 * цепочек, отданных непроверенному по соседям пригодному кандидату
 * (не успешная раздача: статус и нарушения остаются явными, фильтр и
 * сертификат её не засчитывают); [unseeded] — имена цепочек без
 * пригодного кандидата, остались пустыми.
 */
data class SeedResult(val seeded: Int, val checked: Int,
                      val fallback: List<String>, val unseeded: List<String>) {
    /** Показание для статуса прогона (RunRecord.seedReading). */
    fun reading(): Map<String, Any?> = linkedMapOf(
        "version" to 1, "checked" to checked, "fallback" to fallback, "unseeded" to unseeded)

    companion object {
        val EMPTY = SeedResult(0, 0, emptyList(), emptyList())
    }
}

object ChainSeeding {

    val REST_GAP: Duration = Duration.ofHours(10)

    private val log: Logger = Logger.getLogger(ChainSeeding::class.java.name)

    private class Load(val e: Employee) {
        val busy = ArrayList<Pair<LocalDateTime, LocalDateTime>>()
        val seats = ArrayList<FlightAssignment>()
        var minutes = 0L
        fun free(from: LocalDateTime, to: LocalDateTime): Boolean =
            busy.none { (a, b) -> from.isBefore(b.plus(REST_GAP)) && a.isBefore(to.plus(REST_GAP)) }
    }

    /** (7) Присланное начальное решение, рвущее цепочку между людьми, —
     *  отказ словами до счёта: цепочка достаётся одному человеку целиком.
     *  K-30-d: «пусто» (null) — отдельное значение: цепочка «E1 + пусто»
     *  тоже разорвана. Исключение — цепочка с закреплениями: её
     *  незакреплённые пустые места якорит пин ([anchorPinnedChains]),
     *  поэтому пустое незакреплённое место рядом с пином разрывом не
     *  считается; два разных человека — разрыв всегда. */
    fun refuseSplitChains(schedule: FlightCrewSchedule) {
        val split = schedule.flightAssignments.filter { it.chainId != null }
            .groupBy { it.chainId!! }
            .filter { (_, seats) ->
                val judged = if (seats.any { it.pinned }) seats.filter { it.pinned || it.employee != null } else seats
                judged.map { it.employee?.id }.distinct().size > 1
            }
        if (split.isEmpty()) return
        throw TaskModeRefused(
            "тёплый старт рвёт цепочки между людьми: " +
            split.entries.joinToString("; ") { (c, seats) ->
                c + " → " + seats.map { it.employee?.id ?: "пусто" }.distinct().joinToString(", ") } +
            " — цепочка утверждённого пейринга достаётся одному человеку целиком; " +
            "уберите назначения этих мест либо выровняйте их на одного сотрудника")
    }

    /**
     * (2) Закрепления: конфликт пинов одной цепочки (разные люди, либо
     * человек и пусто) — отказ ДО изменения любого значения; закреплённый
     * человек, непригодный хоть для одного места цепочки, — тоже отказ;
     * затем один пин якорит всю цепочку (незакреплённые места получают
     * его сотрудника), пустой пин держит цепочку пустой.
     */
    fun anchorPinnedChains(schedule: FlightCrewSchedule) {
        val rules = schedule.layoverDutyRules
        val raw = schedule.flightAssignments.filter { it.chainId != null }.groupBy { it.chainId!! }
        for ((c, seats) in raw) {
            val pins = seats.filter { it.pinned }
            if (pins.isEmpty()) continue
            val anchors = pins.map { it.employee }.distinct()
            if (anchors.size > 1) throw TaskModeRefused(
                "закрепления цепочки " + c + " противоречат друг другу: " +
                anchors.joinToString(", ") { it?.id ?: "пусто" } +
                " — раздача не начата, значения не тронуты")
            val anchor = anchors.single() ?: continue
            if (!ChainRules.eligible(seats, anchor, rules)) throw TaskModeRefused(
                "закреплённый сотрудник " + anchor.id + " непригоден для всех мест цепочки " + c +
                " (доступность, навык, языки или допуск) — раздача не начата")
        }
        for ((_, seats) in raw) {
            val pin = seats.firstOrNull { it.pinned } ?: continue
            for (fa in seats) if (!fa.pinned) fa.employee = pin.employee
        }
    }

    fun seed(schedule: FlightCrewSchedule): SeedResult {
        anchorPinnedChains(schedule)
        val rules = schedule.layoverDutyRules
        val chains = ChainMoves.chains(schedule)
        if (chains.isEmpty()) return SeedResult.EMPTY
        val loads = schedule.employees.map { Load(it) }
        // уже назначенные (в т.ч. закреплённые) места занимают время людей
        for (fa in schedule.flightAssignments) {
            val e = fa.employee ?: continue
            val f = fa.flight ?: continue
            val dep = f.departureUTCDateTime ?: continue
            val arr = f.arrivalUTCDateTime ?: continue
            loads.firstOrNull { it.e === e }?.let {
                it.busy.add(dep to arr); it.seats.add(fa)
                it.minutes += Duration.between(dep, arr).toMinutes()
            }
        }
        val ordered = chains.entries
            .filter { (_, seats) -> seats.all { it.employee == null } }
            .sortedBy { (_, seats) -> seats.mapNotNull { it.flight?.departureUTCDateTime }.minOrNull() }
        var checked = 0
        val fallback = ArrayList<String>()
        val unseeded = ArrayList<String>()
        for ((c, seats) in ordered) {
            val flights = seats.mapNotNull { it.flight }
            val dep = flights.mapNotNull { it.departureUTCDateTime }.minOrNull()
            val arr = flights.mapNotNull { it.arrivalUTCDateTime }.maxOrNull()
            if (dep == null || arr == null) { unseeded.add(c); continue }
            val start = flights.minByOrNull { it.departureUTCDateTime ?: LocalDateTime.MAX }?.departureAirport
            val skill = (seats.firstOrNull { !it.deadhead } ?: seats[0]).requiredSkill
            val skilled = loads.filter { skill.isEmpty() || it.e.skills.contains(skill) }
            // K-30 (1): пригодность для КАЖДОГО места — без неё цепочка
            // никому не раздаётся (unseeded, явно); (4) совместимость по
            // соседям — предпочтение, при отсутствии совместимого — прежний
            // fallback main среди пригодных (самый свободный): журнал и итог
            // называют такую цепочку, успешной раздачей она не считается.
            val eligible = skilled.filter { ChainRules.eligible(seats, it.e, rules) }
            if (eligible.isEmpty()) {
                unseeded.add(c)
                log.warning("раздача по цепочкам: для цепочки " + c + " нет сотрудника, пригодного " +
                    "для всех её мест (доступность, навык, языки, допуск) — цепочка не роздана")
                continue
            }
            val compatible = eligible.filter {
                ChainRules.transitionOk(it.e, it.seats, it.seats + seats, seats, rules)
            }
            val home = compatible.filter { it.e.homeAirport === start || it.e.homeAirport?.code == start?.code }
            var viaFallback = false
            val pick = home.filter { it.free(dep, arr) }.minByOrNull { it.minutes }
                ?: compatible.filter { it.free(dep, arr) }.minByOrNull { it.minutes }
                ?: compatible.minByOrNull { it.minutes }
                ?: eligible.minByOrNull { it.minutes }!!.also {
                    viaFallback = true
                    log.warning("раздача по цепочкам: для цепочки " + c + " нет совместимого по " +
                        "соседям сотрудника — отдана самому свободному пригодному (" +
                        it.e.id + ") НЕПРОВЕРЕННО, поиск обязан починить ходами")
                }
            for (fa in seats) fa.employee = pick.e
            pick.busy.add(dep to arr)
            pick.seats.addAll(seats)
            pick.minutes += Duration.between(dep, arr).toMinutes()
            if (viaFallback) fallback.add(c) else checked++
        }
        if (fallback.isNotEmpty()) log.warning("раздача по цепочкам: цепочек, отданных непроверенному " +
            "кандидату (fallback) — " + fallback.size + ": " + fallback.joinToString(", "))
        if (unseeded.isNotEmpty()) log.warning("раздача по цепочкам: цепочек без пригодного кандидата — " +
            unseeded.size + ": " + unseeded.joinToString(", "))
        return SeedResult(checked + fallback.size, checked, fallback, unseeded)
    }
}
