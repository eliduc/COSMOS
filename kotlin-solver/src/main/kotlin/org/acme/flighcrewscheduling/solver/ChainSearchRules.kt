package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.Employee

/**
 * Снимок порогов прогона (K-5/K-30 единой версии, редакция GPT): те же
 * ручки, которыми считает провайдер ограничений, снятые из applied окна
 * сборки — никаких процессно-глобальных держателей. Живёт отдельно от
 * алгоритма поиска: домен (FlightCrewSchedule.layoverDutyRules) и
 * гарантии раздачи/ходов по цепочкам (ChainSeeding, ChainMoveFactory)
 * читают его, не завися от отклонённого ChainRosterSearch.
 *
 * В пакетном прогоне ручка, о которой пакет молчит, ВЫКЛЮЧЕНА (0) —
 * тот же закон, что у RunThresholds.valueOr; литералы профиля действуют
 * только вне пакета.
 */
data class ChainSearchRules(
    val minSitHome: Int = DefaultProviderRules.MIN_TURNAROUND_MIN,
    val minSitOut: Int = DefaultProviderRules.MIN_TURNAROUND_MIN,
    val minRestHome: Int = DefaultProviderRules.MIN_DUTY_REST_MIN,
    val minRestOut: Int = DefaultProviderRules.MIN_DUTY_REST_MIN,
    val maxDutySpan: Int = DefaultProviderRules.MAX_DAILY_DUTY_MINUTES,
    /** Перечень типов судов правила допуска (семья флота); null — правила нет. */
    val fleetTypes: Set<String>? = null,
    val packaged: Boolean = false,
    /** Порог «мёртвой зоны» стыковки; 0 — правило не объявлено (K-10). */
    val dayBreakThreshold: Int = DefaultProviderRules.DAY_BREAK_THRESHOLD_MIN,
) {
    /** Порог отдыха по месту отдыха: станция = база приписки → домашний. */
    fun rest(station: String?, employee: Employee): Int =
        if (station != null && station == employee.homeAirport?.code) minRestHome else minRestOut

    /** Порог стыковки по месту стыковки. */
    fun sit(station: String?, employee: Employee): Int =
        if (station != null && station == employee.homeAirport?.code) minSitHome else minSitOut

    companion object {
        /** Снимок ПАКЕТНОГО окна: молчание пакета = 0 (правила нет). */
        fun from(applied: Map<String, RunThresholds.Applied>) = ChainSearchRules(
            applied["min_sit_home_min"]?.value ?: 0,
            applied["min_sit_outstation_min"]?.value ?: 0,
            applied["min_rest_home_min"]?.value ?: 0,
            applied["min_rest_outstation_min"]?.value ?: 0,
            applied["max_duty_span_min"]?.value ?: 0,
            applied[RunThresholds.FLEET_THRESHOLD]?.codes?.toSet(), true,
            applied["day_break_threshold_min"]?.value ?: 0)

        /** Снимок окна БЕЗ пакета: молчание = литерал профиля. */
        fun profile(applied: Map<String, RunThresholds.Applied>) = ChainSearchRules(
            applied["min_sit_home_min"]?.value ?: DefaultProviderRules.MIN_TURNAROUND_MIN,
            applied["min_sit_outstation_min"]?.value ?: DefaultProviderRules.MIN_TURNAROUND_MIN,
            applied["min_rest_home_min"]?.value ?: DefaultProviderRules.MIN_DUTY_REST_MIN,
            applied["min_rest_outstation_min"]?.value ?: DefaultProviderRules.MIN_DUTY_REST_MIN,
            applied["max_duty_span_min"]?.value ?: DefaultProviderRules.MAX_DAILY_DUTY_MINUTES,
            applied[RunThresholds.FLEET_THRESHOLD]?.codes?.toSet(), false,
            applied["day_break_threshold_min"]?.value ?: DefaultProviderRules.DAY_BREAK_THRESHOLD_MIN)

        /** Снимок по окну: пакетное — [from], иначе — [profile]. */
        fun of(applied: Map<String, RunThresholds.Applied>, packaged: Boolean) =
            if (packaged) from(applied) else profile(applied)
    }
}
