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
 * GERAD/Kasirzadeh CORE legality profile (benchmark ①, rules phase 1).
 *
 * Mirrors the legality rules of the G-2014-22 monthly pilot instances as
 * documented in Mercier et al. 2005 / Saddoune et al. 2012/2013 (see
 * final-report/kasirzadeh-scoping.md):
 *   - min sit (connection) 30 min inside a duty — aligned to the GENCOL
 *     reference solutions (active-active connections down to 30-42 min in
 *     every month; the former 60 was our artifact)               [rule 4]
 *   - a gap >= 10 h is a rest; ANY gap in [30 min, 10 h) is a legal sit
 *     (no dead zone — unlike our RULES v2)                       [rule 5]
 *   - max 5 landings per duty — calendar-day proxy (documented
 *     approximation: a duty rarely crosses midnight in these
 *     domestic instances)                                        [rule 6]
 *   - max duty elapsed span 14 h — paper value not printed; stated
 *     assumption consistent with US domestic ops                 [rule 7]
 *   - max credited flying 85 h per monthly schedule (approximated
 *     as block hours)                                            [rule 10]
 *   - structural: skill, type rating, availability, no overlap
 *
 * DEFERRED to phase 2 (documented in the report's fidelity note):
 * preferences/vacations personalization (rules 11-13), per-base credit and
 * availability link constraints (rule 14), briefing/debriefing credit
 * accounting (rule 8), pairing-level 4-day/4-duty limits (rules 2-3 —
 * enforced in config (a) by the pairing generator instead).
 */
class GeradConstraintProvider : ConstraintProvider {

    // Rule parameters come from RulesConfig (env CREW_RULES_CONFIG, or
    // built-in defaults equal to the historical constants). Reference-
    // aligned (2026-07-10): GENCOL solutions use 30-42 min sits.
    private val minSitMin = RulesConfig.minSitMin
    private val maxLandingsPerDuty = RulesConfig.maxLandingsPerDuty
    private val maxDutySpanMin = RulesConfig.maxDutySpanMin
    private val maxMonthlyBlockMin = RulesConfig.maxMonthlyBlockMin
    // Rest boundary: a gap >= this is a rest, below it is (paid) sitting.
    private val minRestMin = RulesConfig.minRestMin

    companion object {
        /** Параметры яруса 1, которые ЭТОТ профиль применяет (пятый
         *  аудит, пакет B). Сверяется с фактическими чтениями. */
        val RULE_KEYS = listOf(
            "min_sit_min", "max_landings_per_duty", "max_duty_span_min",
            "max_monthly_block_min", "min_rest_min",
        )
    }

    /** Core legality WITHOUT the unassigned rule — composed by the
     *  personalization profiles whose uncovered-slot semantics is SOFT
     *  (thesis weight 10000), which must stay live now that the planning
     *  variable allows unassigned values. */
    fun coreConstraints(cf: ConstraintFactory): Array<Constraint> = arrayOf(
        requiredSkill(cf),
        typeRating(cf),
        unavailability(cf),
        overlap(cf),
        continuity(cf),
        minSit(cf),
        maxLandings(cf),
        dutySpan(cf),
        monthlyBlock(cf),
        // Single soft: total sit/waiting minutes (a monotone proxy of the
        // waiting-cost part of GERAD's pairing cost g(delta_w); the full
        // piecewise-linear g and preference terms are phase 2).
        waitingCost(cf),
    )

    override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> =
        coreConstraints(cf) + arrayOf(unassignedHard(cf))

    /** Core-profile solves must not park legs unassigned — the historical
     *  forced-assignment behavior made explicit now that the planning
     *  variable allows unassigned values (audit 2026-07-12). The
     *  personalization profiles compose coreConstraints() instead and
     *  keep their SOFT uncovered semantics live. */
    private fun unassignedHard(cf: ConstraintFactory): Constraint =
        cf.forEachIncludingUnassigned(FlightAssignment::class.java)
            .filter { it.employee == null }
            .penalize(HardSoftScore.ofHard(25))
            .asConstraint("G Unassigned slot")

    /** Route continuity (audit fix, 2026-07-10): consecutive legs of one
     *  crew member must be airport-continuous — no teleporting. HARD. */
    private fun continuity(cf: ConstraintFactory): Constraint =
        adjacent(cf)
            .filter { fa1, fa2 ->
                fa1.flight!!.arrivalAirport?.code !=
                    fa2.flight!!.departureAirport?.code
            }
            .penalize(HardSoftScore.ofHard(20))
            .asConstraint("G Continuity")

    private fun requiredSkill(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { !it.hasRequiredSkills() }
            .penalize(HardSoftScore.ofHard(100))
            .asConstraint("G Required skill")

    private fun typeRating(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null
                && it.flight?.aircraftType?.isNotEmpty() == true
                && !it.employee!!.isQualifiedFor(it.flight!!.aircraftType) }
            .penalize(HardSoftScore.ofHard(50))
            .asConstraint("G Type rating")

    private fun unavailability(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter(FlightAssignment::isUnavailableEmployee)
            .penalize(HardSoftScore.ofHard(10))
            .asConstraint("G Unavailable")

    private fun overlap(cf: ConstraintFactory): Constraint =
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
            .asConstraint("G Overlap")

    private fun gapMin(fa1: FlightAssignment, fa2: FlightAssignment): Int =
        ChronoUnit.MINUTES.between(
            fa1.flight!!.arrivalUTCDateTime, fa2.flight!!.departureUTCDateTime).toInt()

    private fun adjacent(cf: ConstraintFactory) =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .join(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::employee),
                Joiners.lessThan(FlightAssignment::getDepartureUTCDateTime),
                Joiners.filtering { fa1, fa2 -> fa1.id != fa2.id })
            .ifNotExists(FlightAssignment::class.java,
                Joiners.equal({ fa1, _ -> fa1.employee }, FlightAssignment::employee),
                Joiners.filtering { fa1, fa2, other ->
                    other.id != fa1.id && other.id != fa2.id
                        && fa1.getDepartureUTCDateTime().isBefore(other.getDepartureUTCDateTime())
                        && other.getDepartureUTCDateTime().isBefore(fa2.getDepartureUTCDateTime())
                })

    /** Rule 4: consecutive legs of a duty need >= 60 min connection. */
    private fun minSit(cf: ConstraintFactory): Constraint =
        adjacent(cf)
            .filter { fa1, fa2 -> gapMin(fa1, fa2) < minSitMin }
            .penalize(HardSoftScore.ofHard(5)) { fa1, fa2 ->
                maxOf(1, minSitMin - gapMin(fa1, fa2)).toLong()
            }
            .asConstraint("G Min sit 60m")

    /** Rule 6 (calendar-day proxy): max 5 landings per duty. */
    private fun maxLandings(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .groupBy(FlightAssignment::employee,
                { it.flight!!.getDepartureUTCDate() },
                ConstraintCollectors.count())
            .filter { _, _, n -> n > maxLandingsPerDuty }
            .penalize(HardSoftScore.ofHard(5)) { _, _, n ->
                (n - maxLandingsPerDuty).toLong()
            }
            .asConstraint("G Max landings per duty")

    /** Rule 7 (day proxy): duty elapsed span cap. */
    private fun dutySpan(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .groupBy(FlightAssignment::employee,
                { it.flight!!.getDepartureUTCDate() },
                ConstraintCollectors.compose(
                    ConstraintCollectors.count(),
                    ConstraintCollectors.min<FlightAssignment, java.time.LocalDateTime> { it.flight!!.departureUTCDateTime },
                    ConstraintCollectors.max<FlightAssignment, java.time.LocalDateTime> { it.flight!!.arrivalUTCDateTime },
                    { n, e, l -> Triple((n ?: 0L).toInt(), e, l) }))
            .filter { _, _, t ->
                // audit fix 2026-07-10: no n>=2 exemption — a single leg
                // longer than the duty cap is a violation too.
                ChronoUnit.MINUTES.between(t.second, t.third) > maxDutySpanMin
            }
            .penalize(HardSoftScore.ofHard(5)) { _, _, t ->
                (ChronoUnit.MINUTES.between(t.second, t.third) - maxDutySpanMin)
            }
            .asConstraint("G Duty span")

    /** Rule 10: max credited hours per CALENDAR month (block proxy).
     *  Паритет правил (2026-07-24): помесячные доли налёта (monthShares) —
     *  для месячных наборов GERAD доля одна, поведение байт-идентично. */
    private fun monthlyBlock(cf: ConstraintFactory): Constraint =
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
            .filter { _, _, tot -> tot > maxMonthlyBlockMin }
            .penalize(HardSoftScore.ofHard(3)) { _, _, tot -> tot - maxMonthlyBlockMin }
            .asConstraint("G Monthly 85h")

    /** Soft proxy of the waiting-cost term: minimize total sit minutes. */
    private fun waitingCost(cf: ConstraintFactory): Constraint =
        adjacent(cf)
            .filter { fa1, fa2 -> gapMin(fa1, fa2) in minSitMin until minRestMin }
            .penalize(HardSoftScore.ONE_SOFT) { fa1, fa2 ->
                gapMin(fa1, fa2).toLong()
            }
            .asConstraint("G Waiting")
}
