package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.Constraint
import ai.timefold.solver.core.api.score.stream.ConstraintCollectors
import ai.timefold.solver.core.api.score.stream.ConstraintFactory
import ai.timefold.solver.core.api.score.stream.ConstraintProvider
import ai.timefold.solver.core.api.score.stream.Joiners
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import java.time.temporal.ChronoUnit

/**
 * GERAD phase 2, stage-2 profile (rules=GERAD2P): rostering over SYNTHETIC
 * pairing-flights (one Flight = one selected pairing spanning up to 4 days).
 *
 * The flight-level GERAD constraints misread synthetic flights (a 4-day
 * span is not a duty; span-based duration is not block time — the BTS
 * "synthetic distortion" lesson), so this profile replaces them with
 * pairing-aware equivalents. Intra-pairing legality is already guaranteed
 * by the pairing generator/pricing.
 *
 *   hard: skill, type rating, unavailability, overlap,
 *         inter-pairing rest >= 10 h, monthly block <= 85 h (real
 *         blockMinutes of the pairing, not its span)
 *   soft: uncovered pairing 10000 x its leg count; vacation 1000 x
 *         vacation days the pairing span touches; preferred legs
 *         -100 x |pairing legs ∩ employee preferences| (reward)
 */
class GeradPairingRosterConstraintProvider : ConstraintProvider {

    // Rule parameters come from RulesConfig (env CREW_RULES_CONFIG, or
    // built-in defaults equal to the historical constants).
    private val minInterPairingRestMin = RulesConfig.minRestMin
    private val maxMonthlyBlockMin = RulesConfig.maxMonthlyBlockMin
    private val uncoveredPerLeg = 10_000L
    private val vacationPenalty = 1_000L
    private val preferredBonus = 100L

    companion object {
        /** Параметры яруса 1, которые ЭТОТ профиль применяет (пятый
         *  аудит, пакет B). Список — заявление профиля; свидетельством
         *  становится совпадение с фактическими чтениями. */
        val RULE_KEYS = listOf(
            "min_rest_min", "max_monthly_block_min",
            "comp_no_two_reserves", "comp_min_pair_seniority_weight",
        )
    }

    override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> {
        // Правила состава читаются внутри фильтров, то есть только когда
        // фильтр вызван. На наборе без со-локации (одно место на участок)
        // он не вызывается никогда, и «применено» осталось бы пустым при
        // работающем правиле. Читаем их здесь — в момент, когда профиль
        // ФАКТИЧЕСКИ устанавливается в решатель: это и есть событие
        // «правило вступило в силу».
        @Suppress("UNUSED_EXPRESSION")
        RulesConfig.compNoTwoReserves
        @Suppress("UNUSED_EXPRESSION")
        RulesConfig.compMinPairSeniorityWeight
        return defineConstraintArray(cf)
    }

    private fun defineConstraintArray(
        cf: ConstraintFactory,
    ): Array<Constraint> = arrayOf(
        requiredSkill(cf),
        typeRating(cf),
        unavailability(cf),
        overlap(cf),
        interPairingRest(cf),
        perUnitRestAfter(cf),
        weeklyRest(cf),
        deniedStations(cf),
        requiredQualifications(cf),
        interPairingContinuity(cf),
        homeBase(cf),
        monthlyBlock(cf),
        monthlyWork(cf),
        doNotFlyWith(cf),
        uncovered(cf),
        vacationDays(cf),
        preferredLegs(cf),
        compositionNoTwoReserves(cf),
        compositionSeniorityMix(cf),
    )

    /** Crew composition (multirole increment 4), OFF by default: two RESERVE
     *  crew must not share one co-located pairing (both seats of one
     *  synthetic flight). Single-seat payloads (GERAD) have no same-flight
     *  pairs, so this is a no-op there regardless of the flag. NOTE: in the
     *  multirole joint/independent modes the Captain and FO of one REAL leg
     *  sit on DIFFERENT pairing-flights — for those the merged Python
     *  composition validator (_crew_composition) is the enforcement point. */
    internal fun compositionNoTwoReserves(cf: ConstraintFactory): Constraint =
        cf.forEachUniquePair(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::flight))
            .filter { a, b ->
                RulesConfig.compNoTwoReserves > 0
                    && a.employee?.isReserve == true
                    && b.employee?.isReserve == true
            }
            .penalize(HardSoftScore.ofHard(30))
            .asConstraint("G2P Composition no-two-reserves")

    /** Crew composition, OFF by default: the seniorityWeight sum of a
     *  co-located seat pair must reach the configured minimum (blocks a
     *  junior+junior cockpit). Same scoping note as above. */
    internal fun compositionSeniorityMix(cf: ConstraintFactory): Constraint =
        cf.forEachUniquePair(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::flight))
            .filter { a, b ->
                val need = RulesConfig.compMinPairSeniorityWeight
                val ea = a.employee
                val eb = b.employee
                need > 0 && ea != null && eb != null
                    && ea.seniorityWeight() + eb.seniorityWeight() < need
            }
            .penalize(HardSoftScore.ofHard(25)) { a, b ->
                (RulesConfig.compMinPairSeniorityWeight
                    - a.employee!!.seniorityWeight()
                    - b.employee!!.seniorityWeight()).toLong().coerceAtLeast(1)
            }
            .asConstraint("G2P Composition seniority mix")

    /** Route continuity between CONSECUTIVE pairings of one crew member
     *  (audit fix, 2026-07-10): the next pairing must depart from where the
     *  previous one arrived — no teleporting between pairings. Closed
     *  pairings (start==end) make this equivalent to a consistent base. */
    internal fun interPairingContinuity(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .join(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::employee),
                Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
                Joiners.filtering { fa1, fa2 -> fa1.id != fa2.id })
            .ifNotExists(FlightAssignment::class.java,
                Joiners.equal({ fa1, _ -> fa1.employee },
                              FlightAssignment::employee),
                Joiners.filtering { fa1, fa2, other ->
                    other.id != fa1.id && other.id != fa2.id
                        && fa1.getDepartureUTCDateTime()
                            .isBefore(other.getDepartureUTCDateTime())
                        && other.getDepartureUTCDateTime()
                            .isBefore(fa2.getDepartureUTCDateTime())
                })
            .filter { fa1, fa2 ->
                fa1.flight!!.arrivalAirport?.code !=
                    fa2.flight!!.departureAirport?.code
            }
            .penalize(HardSoftScore.ofHard(20))
            .asConstraint("G2P Inter-pairing continuity")

    /** GENCOL semantics (audit fix, 2026-07-10): a crew member flies the
     *  pairings of their own base — the pairing (synthetic flight) must
     *  depart from the employee's home airport. With base-anchored pairings
     *  (start == end == base) this pins the whole roster to the home base. */
    internal fun homeBase(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa ->
                val home = fa.employee?.homeAirport ?: return@filter false
                fa.flight!!.departureAirport?.code != home.code
            }
            .penalize(HardSoftScore.ofHard(15))
            .asConstraint("G2P Home base")

    internal fun requiredSkill(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { !it.hasRequiredSkills() }
            .penalize(HardSoftScore.ofHard(100))
            .asConstraint("G2P Required skill")

    /** Допуск по типу судна на ВСЕ типы цепочки (перепроверка 2026-07-24:
     *  смешанные по типам пейринги проверялись только по первому участку). */
    internal fun typeRating(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa: FlightAssignment ->
                fa.employee != null && fa.flight != null
                    && fa.flight!!.requiredTypeRatings().any { t ->
                        !fa.employee!!.isQualifiedFor(t)
                    }
            }
            .penalize(HardSoftScore.ofHard(50)) { fa: FlightAssignment ->
                fa.flight!!.requiredTypeRatings().count { t ->
                    !fa.employee!!.isQualifiedFor(t)
                }.toLong()
            }
            .asConstraint("G2P Type rating")

    internal fun unavailability(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter(FlightAssignment::isUnavailableEmployee)
            .penalize(HardSoftScore.ofHard(10))
            .asConstraint("G2P Unavailable")

    internal fun overlap(cf: ConstraintFactory): Constraint =
        cf.forEachUniquePair(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::employee),
                Joiners.overlapping(
                    { it.flight!!.departureUTCDateTime },
                    { it.flight!!.arrivalUTCDateTime }))
            .penalize(HardSoftScore.ofHard(1)) { fa1, fa2 ->
                val start = maxOf(fa1.flight!!.departureUTCDateTime!!,
                    fa2.flight!!.departureUTCDateTime!!)
                val end = minOf(fa1.flight!!.arrivalUTCDateTime!!,
                    fa2.flight!!.arrivalUTCDateTime!!)
                maxOf(1L, ChronoUnit.MINUTES.between(start, end) / 60)
            }
            .asConstraint("G2P Overlap")

    /** Rest between consecutive pairings of one crew member >= 10 h. */
    internal fun interPairingRest(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .join(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::employee),
                Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
                Joiners.filtering { fa1, fa2 ->
                    fa1.id != fa2.id
                        && !fa2.flight!!.departureUTCDateTime!!
                            .isBefore(fa1.flight!!.arrivalUTCDateTime!!)
                })
            .filter { fa1, fa2 ->
                ChronoUnit.MINUTES.between(
                    fa1.flight!!.arrivalUTCDateTime,
                    fa2.flight!!.departureUTCDateTime) < minInterPairingRestMin
            }
            .penalize(HardSoftScore.ofHard(5)) { fa1, fa2 ->
                maxOf(1L, minInterPairingRestMin - ChronoUnit.MINUTES.between(
                    fa1.flight!!.arrivalUTCDateTime,
                    fa2.flight!!.departureUTCDateTime))
            }
            .asConstraint("G2P Inter-pairing rest")

    /** Требования допусков (2026-07-25, полный паритет правил источника):
     *  участковые коды рейса — КАЖДОМУ члену экипажа; местовые коды
     *  «тип судна_роль» — сотруднику этого места. Валидность допусков —
     *  датированными окнами на всём интервале рейса-цепочки. Пустые
     *  требования = правило для рейса/места не задано. */
    internal fun requiredQualifications(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa: FlightAssignment ->
                fa.employee != null && fa.flight != null
                    && (fa.flight!!.requiredCodesAll.isNotEmpty()
                        || fa.requiredCodes.isNotEmpty())
            }
            .filter { fa: FlightAssignment ->
                val f = fa.flight!!
                val dep = f.departureUTCDateTime
                val arr = f.arrivalUTCDateTime
                if (dep == null || arr == null) return@filter false
                val lo = epochMin(dep)
                val hi = epochMin(arr)
                (f.requiredCodesAll.any { c ->
                    !fa.employee!!.holdsQualification(c, lo, hi)
                } || fa.requiredCodes.any { c ->
                    !fa.employee!!.holdsQualification(c, lo, hi)
                })
            }
            .penalize(HardSoftScore.ofHard(30)) { fa: FlightAssignment ->
                val f = fa.flight!!
                val lo = epochMin(f.departureUTCDateTime!!)
                val hi = epochMin(f.arrivalUTCDateTime!!)
                (f.requiredCodesAll.count { c ->
                    !fa.employee!!.holdsQualification(c, lo, hi)
                } + fa.requiredCodes.count { c ->
                    !fa.employee!!.holdsQualification(c, lo, hi)
                }).toLong()
            }
            .asConstraint("G2P Required qualifications")

    /** Запрещённые станции (перепроверка 2026-07-24): сотрудник не
     *  назначается на цепочку, посещающую станцию из его личного списка
     *  запретов. Пустой список = правило для сотрудника не задано. */
    internal fun deniedStations(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa: FlightAssignment ->
                fa.employee != null && fa.flight != null
                    && fa.employee!!.stationsDenied.isNotEmpty()
                    && fa.flight!!.visitedStationCodes().any { s ->
                        fa.employee!!.stationsDenied.contains(s)
                    }
            }
            .penalize(HardSoftScore.ofHard(20)) { fa: FlightAssignment ->
                fa.flight!!.visitedStationCodes().count { s ->
                    fa.employee!!.stationsDenied.contains(s)
                }.toLong()
            }
            .asConstraint("G2P Denied stations")

    /** Ярус 2в (2026-07-24): требуемый отдых ПОСЛЕ конкретного пейринга
     *  (ступенчатая таблица вендора, значение приезжает данными на юните:
     *  Flight.minRestAfterMin). Действует только сверх глобального минимума
     *  и только для юнитов с заданным значением — прежние payload'ы (0)
     *  не затронуты. */
    internal fun perUnitRestAfter(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null
                && it.flight!!.minRestAfterMin > minInterPairingRestMin }
            .join(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::employee),
                Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
                Joiners.filtering { fa1, fa2 ->
                    fa1.id != fa2.id
                        && !fa2.flight!!.departureUTCDateTime!!
                            .isBefore(fa1.flight!!.arrivalUTCDateTime!!)
                })
            .filter { fa1, fa2 ->
                ChronoUnit.MINUTES.between(
                    fa1.flight!!.arrivalUTCDateTime,
                    fa2.flight!!.departureUTCDateTime) <
                    fa1.flight!!.minRestAfterMin
            }
            .penalize(HardSoftScore.ofHard(5)) { fa1, fa2 ->
                maxOf(1L, fa1.flight!!.minRestAfterMin -
                    ChronoUnit.MINUTES.between(
                        fa1.flight!!.arrivalUTCDateTime,
                        fa2.flight!!.departureUTCDateTime))
            }
            .asConstraint("G2P Per-unit rest after")

    /** Ярус 2в (2026-07-24): еженедельный непрерывный отдых (правило
     *  вендора twoPerWeek: в каждой календарной неделе, целиком лежащей
     *  в горизонте, сумма периодов отдыха >= requiredMin, причём каждый
     *  период >= periodMin в границах недели). Включается только когда
     *  задан CREW_RESOURCE_RULES.weeklyRest — прежние прогоны байт-
     *  идентичны. Штраф = минуты недобора (шкала самого вендора). */
    internal fun weeklyRest(cf: ConstraintFactory): Constraint {
        val rr = ResourceRules.weeklyRest
        if (rr == null) {
            // выключено: пустое ограничение (нет ни одного сотрудника
            // с отрицательным id) — фабрике нужен валидный поток
            return cf.forEach(Employee::class.java)
                .filter { false }
                .penalize(HardSoftScore.ofHard(4))
                .asConstraint("G2P Weekly rest (off)")
        }
        return cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && it.flight != null }
            .groupBy({ fa: FlightAssignment -> fa.employee!! },
                ConstraintCollectors.toList { fa: FlightAssignment ->
                    fa.flight!!
                })
            .filter { _: Employee, flights: List<Flight> ->
                weeklyRestShortfallMin(flights, rr) > 0L
            }
            .penalize(HardSoftScore.ofHard(4)) {
                    _: Employee, flights: List<Flight> ->
                weeklyRestShortfallMin(flights, rr)
            }
            .asConstraint("G2P Weekly rest")
    }

    /** Monthly block cap on REAL block minutes (not the pairing span). */
    // Паритет правил (2026-07-24): налёт считается ПО КАЛЕНДАРНОМУ МЕСЯЦУ
    // (у вендора счётчик начинается заново 1-го числа), причём налёт
    // пейринга ДЕЛИТСЯ по фактическим месяцам его участков (monthShares) —
    // пейринг, пересекающий границу месяца, пополняет оба месяца точно.
    // Для месячных наборов GERAD доля одна — поведение байт-идентично.
    internal fun monthlyBlock(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && it.flight != null }
            .map({ fa: FlightAssignment -> fa.employee!! },
                 { fa: FlightAssignment -> fa.flight!! })
            .flattenLast { f: Flight -> f.monthShares().entries }
            .groupBy({ e: Employee, _: Map.Entry<String, Long> -> e },
                { _: Employee, share: Map.Entry<String, Long> -> share.key },
                ConstraintCollectors.sum { _: Employee,
                                               share: Map.Entry<String, Long> ->
                    share.value
                })
            .filter { e, _, tot ->
                tot > effectiveBlockLimit(e)
            }
            .penalize(HardSoftScore.ofHard(3)) { e, _, tot ->
                tot - effectiveBlockLimit(e)
            }
            .asConstraint("G2P Monthly 85h")

    /** Персональный лимит налёта из данных сотрудника; 0 -> глобальный. */
    private fun effectiveBlockLimit(e: Employee): Long =
        if (e.flightTimeLimitMin > 0) e.flightTimeLimitMin.toLong()
        else maxMonthlyBlockMin.toLong()

    /** Паритет правил вендора (2026-07-24): помесячный лимит РАБОЧЕГО
     *  времени сотрудника (WORK_TIME_LIMIT, у вендора HIGH_HIGH) —
     *  дневные окна пейринга, суммированные по месяцам (workMonthShares).
     *  Действует только для сотрудников с заданным лимитом — прежние
     *  payload'ы (лимит 0) не затронуты. */
    internal fun monthlyWork(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && it.flight != null
                && it.employee!!.workTimeLimitMin > 0 }
            .map({ fa: FlightAssignment -> fa.employee!! },
                 { fa: FlightAssignment -> fa.flight!! })
            .flattenLast { f: Flight -> f.workMonthShares().entries }
            .groupBy({ e: Employee, _: Map.Entry<String, Long> -> e },
                { _: Employee, share: Map.Entry<String, Long> -> share.key },
                ConstraintCollectors.sum { _: Employee,
                                           share: Map.Entry<String, Long> ->
                    share.value
                })
            .filter { e, _, tot -> tot > e.workTimeLimitMin }
            .penalize(HardSoftScore.ofHard(3)) { e, _, tot ->
                tot - e.workTimeLimitMin
            }
            .asConstraint("G2P Monthly work time")

    /** Паритет правил вендора (2026-07-24): DO_NOT_FLY_WITH (HIGH_HIGH) —
     *  несовместимые сотрудники не летают одним юнитом (списки
     *  односторонние, проверка в обе стороны). Пустые списки (все прежние
     *  payload'ы) — правило не срабатывает никогда. */
    internal fun doNotFlyWith(cf: ConstraintFactory): Constraint =
        cf.forEachUniquePair(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::flight))
            .filter { a, b ->
                val ea = a.employee
                val eb = b.employee
                ea != null && eb != null && ea.id != eb.id
                    && (ea.doNotFlyWith.contains(eb.id)
                        || eb.doNotFlyWith.contains(ea.id))
            }
            .penalize(HardSoftScore.ofHard(40))
            .asConstraint("G2P Do not fly with")

    /** Unassigned pairing = all its real legs uncovered. */
    internal fun uncovered(cf: ConstraintFactory): Constraint =
        cf.forEachIncludingUnassigned(FlightAssignment::class.java)
            .filter { it.employee == null }
            .penalize(HardSoftScore.ofSoft(uncoveredPerLeg)) { fa ->
                maxOf(1, fa.flight!!.legIds.size).toLong()
            }
            .asConstraint("G2P Uncovered pairing")

    /** Vacation days of the employee that the pairing span touches. */
    internal fun vacationDays(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa ->
                val emp = fa.employee ?: return@filter false
                emp.preferredDaysOff.isNotEmpty()
            }
            .penalize(HardSoftScore.ofSoft(vacationPenalty)) { fa ->
                val emp = fa.employee!!
                var d = fa.flight!!.getDepartureUTCDate()
                val last = fa.flight!!.arrivalUTCDateTime!!.toLocalDate()
                var hits = 0L
                while (!d.isAfter(last)) {
                    if (emp.preferredDaysOff.contains(d)) hits++
                    d = d.plusDays(1)
                }
                hits
            }
            .asConstraint("G2P Vacation days")

    /** Reward legs of the pairing the crew member asked for. */
    internal fun preferredLegs(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa ->
                val emp = fa.employee ?: return@filter false
                emp.preferredFlights.isNotEmpty()
                    && fa.flight!!.legIds.isNotEmpty()
            }
            .reward(HardSoftScore.ofSoft(preferredBonus)) { fa ->
                val prefs = fa.employee!!.preferredFlights.toHashSet()
                fa.flight!!.legIds.count { prefs.contains(it) }.toLong()
            }
            .asConstraint("G2P Preferred legs")
}
