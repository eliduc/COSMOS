package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.config.solver.termination.TerminationConfig
import ai.timefold.solver.core.impl.constructionheuristic.scope.ConstructionHeuristicStepScope
import ai.timefold.solver.core.impl.localsearch.scope.LocalSearchPhaseScope
import ai.timefold.solver.core.impl.localsearch.scope.LocalSearchStepScope
import ai.timefold.solver.core.impl.phase.event.PhaseLifecycleListenerAdapter
import ai.timefold.solver.core.impl.phase.scope.AbstractPhaseScope
import ai.timefold.solver.core.impl.phase.scope.AbstractStepScope
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule

/**
 * ПО КАКОМУ ПРЕДЕЛУ остановился прогон (правка Д1, 08.09.2026).
 *
 * **Находка живого стенда 08.09.2026.** Детерминированный режим Д1
 * (`deterministic=true`) прежде ЗАМЕЩАЛ остановку целиком счётными
 * пределами: стенных часов в ней не оставалось. Счётный предел Timefold
 * судится ТОЛЬКО между завершёнными шагами поиска
 * (`StepCountTermination.isPhaseTerminated` смотрит на индекс следующего
 * шага), а на мире БЕЗ исполнимого хода первый шаг поиска не завершается
 * никогда: селектор ходов случайного порядка бесконечен, ни один ход не
 * исполним, «No doable selected move» не наступает. Прогон занимал поток
 * пула прогонов навсегда — так был заблокирован локальный решатель с
 * 06.09 по 08.09.
 *
 * **Правка.** Счётные пределы остаются ГЛАВНЫМИ; рядом с ними в той же
 * (ИЛИ-составной) остановке живёт СТРАХОВОЧНЫЙ предел стенных часов —
 * стенные часы судятся на каждом ходе, поэтому они разрывают и
 * бесконечный селектор. На здоровом мире счётный предел наступает
 * первым, и страховка не срабатывает вовсе: воспроизводимость не задета.
 * Timefold 2.0 отдельного признака «фаза поиска не может сделать ни
 * одного хода» наружу не даёт (`terminationClassList` в 2.x убран,
 * `MoveCountTermination` и `ScoreCalculationCountTermination` на таком
 * мире не растут — счётов и оценок ходов нет), поэтому страховочный
 * предел и есть ранний выход.
 *
 * **Признак остановки.** Прогон говорит СЛОВАМИ, что его остановило:
 * счётный предел (воспроизводимо) либо страховка по часам (счётный
 * предел не наступил). Признак читают стенд и кнопка — по нему видно,
 * законно ли ждать того же решения при повторе.
 *
 * **Правка по ревью 08.09.2026.** Три нечестности признака закрыты:
 *  - `cancelled` берётся ФАКТОМ остановки решателя
 *    (`Solver.isTerminateEarly()`), а не флагом запроса: прогон, чья
 *    отмена до решателя не дошла и который кончился своим пределом,
 *    больше не называется отменённым;
 *  - ранняя остановка «N секунд без улучшения» обычного профиля
 *    (`unimprovedSpentLimit`) разбирается своим видом
 *    [UNIMPROVED_WALL_CLOCK], а не записывается «поиск кончился сам»;
 *  - страховка при нуле шагов поиска зовётся воспроизводимой ТОЛЬКО
 *    когда фаза поиска действительно стартовала: конструктив тоже
 *    судится часами (его фаза видит остановку решателя мостом), и
 *    оборванный часами конструктив оставляет места пустыми — сколько
 *    успел раздать, зависит от машины.
 */
object RunTermination {

    /** Остановлен счётным пределом шагов — воспроизводимо. */
    const val STEP_COUNT = "step_count"

    /** Остановлен счётным пределом неулучшающих шагов — воспроизводимо. */
    const val UNIMPROVED_STEP_COUNT = "unimproved_step_count"

    /** Остановлен СТРАХОВОЧНЫМ пределом стенных часов внутри Д1: счётный
     *  предел не наступил. Если фаза поиска стартовала и шагов не было —
     *  результат есть решение конструктивной эвристики, воспроизводимое
     *  по построению; если шаги были либо конструктив сам оборван
     *  часами — числа зависят от машины. */
    const val WALL_CLOCK_SAFETY = "wall_clock_safety"

    /** Обычный прогон: остановлен своим пределом стенных часов. */
    const val WALL_CLOCK = "wall_clock"

    /** Обычный прогон: остановлен ранней остановкой «N секунд без
     *  улучшения» (`unimprovedSpentLimit` — явный параметр кнопки
     *  `unimproved_limit_s`). Предел СТЕННОЙ: сколько шагов успела
     *  машина, столько и есть — воспроизводимость не обещана. */
    const val UNIMPROVED_WALL_CLOCK = "unimproved_wall_clock"

    /** Фаза поиска завершилась сама, раньше любого предела. */
    const val SEARCH_EXHAUSTED = "search_exhausted"

    /** Остановлен ручкой отмены прогона (DELETE /schedules/run/{runId}). */
    const val CANCELLED = "cancelled"

    /** Зазор к стенному пределу, мс: решатель судит часы между ходами,
     *  поэтому «дошли до предела» опознаётся с допуском, а не по равенству. */
    const val SLACK_MS = 250L

    /**
     * Счётчик завершённых шагов ФАЗЫ ПОИСКА (шаги конструктива считаются
     * отдельно: счётные пределы Д1 считают именно шаги поиска). Ведёт и
     * длину текущей цепочки неулучшающих шагов — по ней опознаётся
     * срабатывание `unimprovedStepCountLimit`.
     *
     * Ведёт ещё и признак [searchStarted] — стартовала ли фаза ПОИСКА
     * вообще. Решатель проверяет собственную остановку ПЕРЕД каждой
     * фазой (`AbstractSolver.runPhases`), поэтому старт фазы поиска
     * означает: конструктив отработал ПОЛНОСТЬЮ. Без этого признака
     * «шагов поиска нет» нельзя отличить от «конструктив оборван часами,
     * места остались пустыми».
     */
    class StepCounter : PhaseLifecycleListenerAdapter<FlightCrewSchedule>() {

        @Volatile
        var searchSteps: Int = 0
            private set

        @Volatile
        var unimprovedStreak: Int = 0
            private set

        /** Завершённых шагов КОНСТРУКТИВА (раздача мест): по ним видно,
         *  докуда дошла раздача, если часы оборвали её. */
        @Volatile
        var constructionSteps: Int = 0
            private set

        /** Стартовала ли фаза ПОИСКА — то есть отработал ли конструктив
         *  целиком. */
        @Volatile
        var searchStarted: Boolean = false
            private set

        override fun phaseStarted(phaseScope: AbstractPhaseScope<FlightCrewSchedule>) {
            if (phaseScope is LocalSearchPhaseScope<FlightCrewSchedule>) searchStarted = true
        }

        override fun stepEnded(stepScope: AbstractStepScope<FlightCrewSchedule>) {
            if (stepScope is ConstructionHeuristicStepScope<FlightCrewSchedule>) {
                constructionSteps += 1
                return
            }
            if (stepScope !is LocalSearchStepScope<FlightCrewSchedule>) return
            searchSteps += 1
            unimprovedStreak = if (stepScope.bestScoreImproved) 0 else unimprovedStreak + 1
        }
    }

    /**
     * Признак остановки прогона машинными полями и словами.
     *
     * Разбор по порядку и НАМЕРЕННО осторожный. Израсходованные стенные
     * часы записываются причиной ПЕРВЫМИ — даже когда счётный предел к
     * этому мигу тоже набрался: шаг поиска мог завершиться лишь потому,
     * что часы оборвали перебор ходов, и тогда содержание этого шага
     * зависит от скорости машины. Измерено на живом стенде 08.09.2026:
     * прогон Д1 с `step_limit 1` и пределом 1 с закончился за 1008 мс с
     * одним шагом — назвать это воспроизводимым счётным пределом значило
     * бы пообещать больше, чем есть. Дальше — счётные пределы (без
     * завершённого шага Timefold их не судит вовсе), затем ранняя
     * остановка «N секунд без улучшения» (тоже стенная, потому и
     * невоспроизводимая), в конце — самостоятельный конец фазы поиска.
     *
     * @param terminatedEarly ФАКТ остановки решателя ручкой отмены
     *   (`Solver.isTerminateEarly()` после `solve`), а НЕ флаг запроса:
     *   отмена, не дошедшая до решателя, прогон не остановила, и звать
     *   его отменённым — врать (находка ревью 08.09.2026).
     * @param searchStarted стартовала ли фаза поиска. `false` значит:
     *   конструктив оборван часами, решение неполное.
     */
    fun reading(term: TerminationConfig?, deterministic: Boolean,
                searchSteps: Int, unimprovedStreak: Int,
                elapsedMs: Long, terminatedEarly: Boolean,
                searchStarted: Boolean = true,
                constructionSteps: Int = 0): Map<String, Any?> {
        val stepLimit = term?.stepCountLimit?.takeIf { it > 0 }
        val unimprovedLimit = term?.unimprovedStepCountLimit?.takeIf { it > 0 }
        val wallMs = term?.spentLimit?.toMillis()?.takeIf { it > 0 }
        val unimprovedWallMs = term?.unimprovedSpentLimit?.toMillis()?.takeIf { it > 0 }
        val wallSpent = wallMs != null && elapsedMs >= wallMs - SLACK_MS
        val kind = when {
            terminatedEarly -> CANCELLED
            wallSpent -> if (deterministic) WALL_CLOCK_SAFETY else WALL_CLOCK
            stepLimit != null && searchSteps >= stepLimit -> STEP_COUNT
            unimprovedLimit != null && unimprovedStreak >= unimprovedLimit -> UNIMPROVED_STEP_COUNT
            // Ранняя остановка «N секунд без улучшения» — предел СТЕННОЙ:
            // раз он в остановке был, а счётные пределы не наступили,
            // оборвал прогон он, а не «поиск кончился сам».
            unimprovedWallMs != null -> UNIMPROVED_WALL_CLOCK
            else -> SEARCH_EXHAUSTED
        }
        // Воспроизводимо ли ЭТО решение: счётный предел и самостоятельно
        // исчерпанный поиск — да; страховка при нуле шагов поиска — да,
        // но ТОЛЬКО когда фаза поиска стартовала (значит конструктив
        // отработал целиком и решение — его решение); страховка после
        // шагов поиска либо поверх оборванного конструктива — нет.
        val reproducible = when (kind) {
            STEP_COUNT, UNIMPROVED_STEP_COUNT -> true
            SEARCH_EXHAUSTED -> deterministic
            WALL_CLOCK_SAFETY -> searchSteps == 0 && searchStarted
            else -> false
        }
        val words = when {
            kind == CANCELLED ->
                "прогон остановлен ручкой отмены; шагов поиска " + searchSteps +
                ", решение — лучшее на момент отмены"
            kind == STEP_COUNT ->
                "остановлен СЧЁТНЫМ пределом шагов: шагов поиска " + searchSteps +
                " при пределе " + stepLimit + " — воспроизводимо"
            kind == UNIMPROVED_STEP_COUNT ->
                "остановлен СЧЁТНЫМ пределом неулучшающих шагов: подряд " +
                unimprovedStreak + " при пределе " + unimprovedLimit + " — воспроизводимо"
            kind == WALL_CLOCK_SAFETY && !searchStarted ->
                "остановлен СТРАХОВОЧНЫМ пределом стенных часов (" +
                (wallMs ?: 0L) / 1000.0 + " с) ДО старта фазы поиска: конструктив " +
                "оборван часами на шаге " + constructionSteps + " — решение неполное " +
                "(часть мест осталась пустой), воспроизводимость не обещана"
            kind == WALL_CLOCK_SAFETY ->
                "остановлен СТРАХОВОЧНЫМ пределом стенных часов (" +
                (wallMs ?: 0L) / 1000.0 + " с): счётный предел не наступил, шагов " +
                "поиска " + searchSteps +
                (if (searchSteps == 0)
                    " — исполнимого хода у поиска нет, решение есть решение " +
                    "конструктивной эвристики (воспроизводимо по построению)"
                 else
                    " — числа зависят от машины, воспроизводимость не обещана")
            kind == UNIMPROVED_WALL_CLOCK ->
                "остановлен ранней остановкой «" + (unimprovedWallMs ?: 0L) / 1000.0 +
                " с без улучшения» (предел СТЕННОЙ): счётные пределы не наступили, " +
                "шагов поиска " + searchSteps +
                " — числа зависят от машины, воспроизводимость не обещана"
            kind == WALL_CLOCK ->
                "остановлен пределом стенных часов (" + (wallMs ?: 0L) / 1000.0 +
                " с); шагов поиска " + searchSteps
            else ->
                "фаза поиска завершилась сама раньше любого предела: шагов поиска " +
                searchSteps
        }
        return linkedMapOf(
            "version" to 1,
            "kind" to kind,
            "mode" to if (deterministic) "deterministic" else "wall_clock",
            "reproducible" to reproducible,
            "search_steps" to searchSteps,
            "unimproved_steps" to unimprovedStreak,
            // Стартовала ли фаза поиска и сколько шагов сделал конструктив:
            // без этих двух чисел «шагов поиска 0» двусмысленно.
            "search_started" to searchStarted,
            "construction_steps" to constructionSteps,
            "elapsed_ms" to elapsedMs,
            "step_limit" to stepLimit,
            "unimproved_step_limit" to unimprovedLimit,
            "wall_clock_limit_ms" to wallMs,
            "unimproved_wall_clock_limit_ms" to unimprovedWallMs,
            "words" to words)
    }
}
