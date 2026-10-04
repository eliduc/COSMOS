package org.acme.flighcrewscheduling.domain

import ai.timefold.solver.core.api.domain.solution.ConstraintWeightOverrides
import ai.timefold.solver.core.api.domain.solution.PlanningEntityCollectionProperty
import ai.timefold.solver.core.api.domain.solution.PlanningScore
import ai.timefold.solver.core.api.domain.solution.PlanningSolution
import ai.timefold.solver.core.api.domain.solution.ProblemFactCollectionProperty
import ai.timefold.solver.core.api.domain.valuerange.ValueRangeProvider
import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverStatus

/**
 * Момент учёта из строки набора (K-3/K-4 единой версии, редакция GPT):
 * доли секунды сохраняются; дата без времени — начало суток; явное
 * смещение (+01:00 / Z) переводится в UTC; строка не обрезается молча
 * (прежде take(19) выбрасывал доли секунды и смещение). Нечитаемая
 * строка — исключение разбора, решает вызывающий.
 */
internal fun parseAccountingMoment(value: String): java.time.LocalDateTime {
    val normalized = value.trim().replace(' ', 'T')
    if (normalized.length == 10) return java.time.LocalDate.parse(normalized).atStartOfDay()
    return try {
        java.time.OffsetDateTime.parse(normalized).withOffsetSameInstant(java.time.ZoneOffset.UTC).toLocalDateTime()
    } catch (_: java.time.format.DateTimeParseException) {
        java.time.LocalDateTime.parse(normalized)
    }
}

/**
 * Объявленный неизменяемый горизонт учёта [start_inclusive, end_exclusive)
 * (K-4; источник — manifest.horizon пакета, §3.10.3 C-27b плана единой
 * версии). Факт задачи с приоритетом над размахом рейсов: прилёт после
 * границы (H0: 30.09 23:30 → 01.10) не расширяет календарь резерва, окна
 * и месяцы форм. Без факта — fallback единый для провайдера, fit и
 * checkFacts: календарные сутки от первого до последнего ВЫЛЕТА (d1ee521).
 */
class AccountingWindow {
    var start_inclusive: String = ""
    var end_exclusive: String = ""
    /** Происхождение окна (package_manifest, inferred, …); null — не названо. */
    var source: String? = null

    fun startAt(): java.time.LocalDateTime = parseAccountingMoment(start_inclusive)
    fun endAt(): java.time.LocalDateTime = parseAccountingMoment(end_exclusive)
}

@PlanningSolution
class FlightCrewSchedule {

    @ProblemFactCollectionProperty
    var airports: MutableList<Airport> = mutableListOf()

    @ProblemFactCollectionProperty
    @ValueRangeProvider
    var employees: MutableList<Employee> = mutableListOf()

    @ProblemFactCollectionProperty
    var flights: MutableList<Flight> = mutableListOf()

    /**
     * Шапка раздела истории пакета (payload.history: as_of, coverage,
     * assume_zero_when_missing, user_decision, employees_without_history)
     * — данные набора, не факт планирования; читается один раз при старте
     * пакетного прогона (HistoryAssumption.applyZero): при признаке
     * assume_zero_when_missing сотрудники без записи получают явную
     * нулевую запись на срез. Раздела нет — null.
     */
    var history: MutableMap<String, Any?>? = null

    /**
     * Окна дней недели из формы пакета (calendar_window_penalties)
     * — факты мягкой семьи calendar_windows. Пусто = формы нет
     * (легаси, счёт байт-идентичен прежнему). Из сетевого обмена
     * исключено тем же законом, что переопределения весов ниже.
     */
    @ProblemFactCollectionProperty
    @get:com.fasterxml.jackson.annotation.JsonIgnore
    @set:com.fasterxml.jackson.annotation.JsonIgnore
    var calendarWindowRules: MutableList<CalendarWindowRule> = mutableListOf()

    @PlanningEntityCollectionProperty
    var flightAssignments: MutableList<FlightAssignment> = mutableListOf()

    /**
     * Веса ограничений из «Пакета постановки задачи» CDPM (мост А).
     *
     * Умолчание `none()` — прогон БЕЗ пакета: действуют литералы весов
     * в провайдере ограничений, прежнее поведение байт-идентично.
     * Пустым (null) поле быть не может: объявленное на решении, оно
     * проверяется Timefold на непустоту при построении сессии подсчёта,
     * поэтому «переопределений нет» выражается пустым НАБОРОМ.
     * Непустое значение
     * ставится точкой входа прогона из objective.json пакета: вес
     * мягкого правила приходит из ответов авиакомпании, а не из кода
     * (условие МУ-03). Поле распознаётся Timefold по ТИПУ.
     *
     * Из обмена по сети поле исключено: тип — интерфейс, и обратный
     * разбор присланного решения на нём бы падал; формат обмена
     * существующих прогонов обязан остаться прежним.
     */
    @get:com.fasterxml.jackson.annotation.JsonIgnore
    @set:com.fasterxml.jackson.annotation.JsonIgnore
    var constraintWeightOverrides: ConstraintWeightOverrides<HardSoftScore> =
        ConstraintWeightOverrides.none()

    @PlanningScore
    var score: HardSoftScore? = null

    /** Ignored by Timefold; used by the UI to display solve / stop button state. */
    var solverStatus: SolverStatus? = null

    /**
     * Объявленный горизонт учёта (K-4): факт задачи `window_horizon`
     * набора решателя; null — не объявлен, действует fallback по вылетам
     * ([LogicForms.horizonOf]). Решатель окно НЕ дописывает сам: отчёт
     * цены ночёвок называет источник области честно.
     */
    @get:com.fasterxml.jackson.annotation.JsonProperty("window_horizon")
    @set:com.fasterxml.jackson.annotation.JsonProperty("window_horizon")
    @ai.timefold.solver.core.api.domain.solution.ProblemFactProperty
    var windowHorizon: AccountingWindow? = null

    /** Веса мягких правил прогона по именам ограничений (effectiveWeights
     *  пакета) — для известной части цены ночёвок (K-5/K-32); пусто — вне
     *  пакета, известная сумма не считается. Из обмена исключено. */
    @get:com.fasterxml.jackson.annotation.JsonIgnore
    @set:com.fasterxml.jackson.annotation.JsonIgnore
    var layoverCostWeights: Map<String, Long> = emptyMap()

    /** Снимок порогов отдыха прогона для нарезки нормативных смен в отчёте
     *  цены ночёвок ВНЕ окна сборки провайдера (K-5): ставит
     *  RosterLayoverCost.prepare из applied прогона. Из обмена исключено. */
    @get:com.fasterxml.jackson.annotation.JsonIgnore
    @set:com.fasterxml.jackson.annotation.JsonIgnore
    var layoverDutyRules: org.acme.flighcrewscheduling.solver.ChainSearchRules =
        org.acme.flighcrewscheduling.solver.ChainSearchRules()

    constructor()

    constructor(score: HardSoftScore?, solverStatus: SolverStatus?) {
        this.score = score
        this.solverStatus = solverStatus
    }
}
