package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.Constraint
import ai.timefold.solver.core.api.score.stream.ConstraintCollectors
import ai.timefold.solver.core.api.score.stream.ConstraintFactory
import ai.timefold.solver.core.api.score.stream.ConstraintProvider
import ai.timefold.solver.core.api.score.stream.Joiners
import ai.timefold.solver.core.api.score.stream.bi.BiConstraintStream
import ai.timefold.solver.core.api.score.stream.tri.TriConstraintStream
import ai.timefold.solver.core.api.score.stream.uni.UniConstraintStream
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit
import java.time.temporal.WeekFields

// ---- RULES v2 (duty-period-aware rest) -------------------------------------
// The original flat rule ("10h rest between ANY two flights of one employee")
// encoded a world where crew fly one leg per rest cycle — unrealistic, and it
// made every pairing-style roster (60-min turnarounds inside a duty day)
// massively infeasible on re-scoring (98% of -10.57M hard). v2 mirrors the
// pairing generator's connection rules EXACTLY, reconciling both models:
//   gap <  60 min                → illegal turnaround
//   60 min <= gap < 6 h          → legal sit within a duty day
//   6 h    <= gap < 8 h          → illegal (too long to be a sit, too short
//                                  to be an overnight rest — the dead zone)
//   gap >= 8 h                   → legal rest between duty periods
// Duty-day length is still bounded by MAX_DAILY_DUTY_MINUTES below.
/**
 * Пороги профиля по умолчанию — ОБЪЯВЛЕННЫЕ, а не спрятанные.
 *
 * Этот провайдер не читает ярус правил (`RulesConfig`) ни разу: его числа
 * живут литералами прямо здесь. Пока они были приватными, отчёт
 * применённого показывал показания ЯРУСА, которых этот прогон не касался,
 * — и потребитель отчёта честно строил на них выгрузку смен. Разойтись
 * так может только молча: числа с обеих сторон правдоподобные.
 *
 * Значения не изменены ни одно: объявление их видимыми — не правка
 * поведения, а прекращение умолчания о нём.
 */
object DefaultProviderRules {
    const val MIN_TURNAROUND_MIN = 60
    const val DAY_BREAK_THRESHOLD_MIN = 6 * 60
    const val MIN_DUTY_REST_MIN = 8 * 60
    const val MAX_DAILY_DUTY_MINUTES = 14 * 60
    const val MAX_MONTHLY_FLIGHT_MINUTES = 100 * 60
    const val MAX_WEEKLY_FLIGHT_MINUTES = 60 * 60
    const val MAX_CONSECUTIVE_DUTY_DAYS = 6
    /** Посадок за смену: 0 = правило ВЫКЛЮЧЕНО. У профиля по умолчанию
     *  такого ограничения никогда не было; включать его литералом значило
     *  бы изменить счёт прогонов без пакета. Число приходит из пакета. */
    const val MAX_LANDINGS_PER_DUTY_OFF = 0
    /** Численность экипажа на рейсе: 0 = правило ВЫКЛЮЧЕНО (число из пакета). */
    const val CREW_SIZE_OFF = 0
    /** Суточный резерв по базам: семейство ручек reserve_crews_min[КОД];
     *  маркер семейства 0 = правило ВЫКЛЮЧЕНО (нормы приходят из пакета). */
    const val RESERVE_CREWS_OFF = 0
    /** Имя семейства ручек резерва; ручка базы — «reserve_crews_min[PLY]». */
    const val RESERVE_FAMILY = "reserve_crews_min"

    /**
     * Все МЯГКИЕ ограничения профиля по умолчанию с их литеральными
     * весами. Ревизия литералов (поручение владельца 2026-09-03:
     * ограничения и критерии — исключительно из ВМП): в прогоне по
     * пакету каждое из них, не названное весом пакета и не исполняющее
     * целевую, получает вес 0 (Timefold: нулевой вес = ограничение
     * выключено). Перечень закрыт: новое мягкое ограничение обязано
     * появиться здесь, иначе тест «вне ВМП» его не увидит.
     */
    val SOFT_CONSTRAINTS: List<String> = listOf(
        "Transfer between two flights",
        "Positioning deadhead cost",
        "Layover nights away from home",
        "Calendar window layover penalty",
        "Duty extension over threshold",
        "Preferred day off violation",
        "Undesired airport",
        "Reserve crew usage",
        "Too many duty days per week",
        "Fair red-eye distribution",
        "Fair weekend distribution",
        "Credit hours spread",
        "Credit hours spread over threshold",
    )

    // Веса ЖЁСТКИХ ограничений профиля — объявлены здесь и ТОЛЬКО здесь:
    // вызовы penalize берут эти же константы, иначе отчёт (hardWeights) и
    // счёт — два источника правды, которые разойдутся молча.
    //
    // ЧТО ЭТО ЗА ЧИСЛА. Не нормы авиакомпании: свод говорит «нельзя», а не
    // «нельзя на 30 очков». Жёсткое нарушение недопустимо при любой
    // величине; вес нужен САМОМУ решателю как компас — по нему поиск
    // понимает, в какую сторону выбираться из недопустимого состояния.
    // Поэтому веса назначает инженер COSMOS и объявляет их в отчёте, а не
    // спрашивает у заказчика (веса МЯГКИХ правил — наоборот, приходят из
    // пакета постановки и в коде их нет).
    //
    // ПРАВИЛО НАЗНАЧЕНИЯ: физически невозможное дороже недопустимого по
    // нормам, недопустимое по нормам дороже нежелательного. Нарушить
    // правило не должно быть ВЫГОДНО ни для одной другой цели.
    const val HARD_UNASSIGNED_SLOT = 30
    const val HARD_REQUIRED_SKILL = 100
    /** Пересечение рейсов у одного человека — за каждый час пересечения.
     *  Физически невозможное обязано стоить дороже неукомплектованного
     *  места (30): при прежней цене 1 решателю было выгодно «раздваивать»
     *  человека ради резерва и ради занятия пустого места. Исправлено
     *  2026-08-23 по условию владельца «корректность важнее сравнимости
     *  со старыми числами». */
    const val HARD_FLIGHT_CONFLICT_PER_HOUR = 50
    const val HARD_EMPLOYEE_UNAVAILABLE = 10
    const val HARD_MIN_TURNAROUND_PER_MIN = 5
    const val HARD_REST_PER_MIN = 5
    const val HARD_ROUTE_CONTINUITY = 10
    const val HARD_MAX_DUTY_PER_MIN = 5
    const val HARD_LANDINGS_PER_EXTRA = 5
    const val HARD_MONTHLY_PER_MIN = 3
    const val HARD_WEEKLY_PER_MIN = 2
    const val HARD_LANGUAGE = 2
    const val HARD_TYPE_RATING = 50
    /** Допуск есть, но НЕ ДЕЙСТВУЕТ на время рейса (просрочен или ещё не
     *  начался). Отдельно от «допуска нет»: причины разные, и читатель
     *  отчёта обязан их различать. Цена та же — летит человек, который
     *  лететь не вправе. Правило свода было неисполнимо до 2026-08-23:
     *  конвертер терял колонку «Действует до», и срок до решателя не
     *  доезжал вовсе. */
    const val HARD_TYPE_RATING_CURRENCY = 50
    /** Численность: за каждого недостающего или лишнего РАЗЛИЧНОГО человека
     *  на рейсе. Вторая линия за «Unassigned slot» (30) и «Flight conflict»:
     *  вес 1 не переставляет лестницу жёстких и доводит число пакета до счёта. */
    const val HARD_CREW_SIZE_PER_PERSON = 1
    /** Резерв: за недостающий экипаж в сутки на базе. Вес 1 — программа
     *  полётов выше резерва (решение владельца 2026-08-23): опустошить два
     *  места ради экипажа стоит 2 × (30 + 1), «раздвоить» человека — от 50
     *  за час пересечения, то есть купить резерв нарушением невыгодно ни
     *  одним способом; нехватка честно отчитывается числом экипажей. */
    const val HARD_RESERVE_PER_CREW_DAY = 1
    /** Ступень 3: за единицу нехватки/превышения порождённого счётчика (выходных в месяц). */
    const val HARD_LOGIC_SHORTFALL = 1
    /** Перегоны экипажа: 0 = возможность ВЫКЛЮЧЕНА (пределы из пакета). */
    const val DEADHEADS_OFF = 0
    /** Сдвиг шкалы времени данных: 0 = счёт дат ведётся в шкале самих
     *  временных меток (для мира «местное время базы» это и есть его
     *  местное время). НЕ выключатель — значение шкалы; ненулевой сдвиг
     *  разрешитель порогов отвергает до сборки. */
    const val BASE_TZ_OFFSET_SAME_SCALE = 0
    /** За лишний перегон сотрудника в сутки сверх порога пакета. */
    const val HARD_DEADHEADS_PER_EXTRA = 5
    /** За пару мест одной цепочки утверждённого пейринга у разных людей. */
    const val HARD_CHAIN_INTEGRITY = 50

    /** Веса жёстких ограничений под именами ограничений — для отчёта. */
    fun hardWeights(): Map<String, Int> = linkedMapOf(
        "Unassigned slot" to HARD_UNASSIGNED_SLOT,
        "Required skill" to HARD_REQUIRED_SKILL,
        "Flight conflict (per hour of overlap)" to HARD_FLIGHT_CONFLICT_PER_HOUR,
        "Employee unavailable" to HARD_EMPLOYEE_UNAVAILABLE,
        "Minimum turnaround (per minute)" to HARD_MIN_TURNAROUND_PER_MIN,
        "Rest between duty periods (per minute)" to HARD_REST_PER_MIN,
        "Route continuity (hard)" to HARD_ROUTE_CONTINUITY,
        "Max duty period (rest-segmented) (per minute)" to HARD_MAX_DUTY_PER_MIN,
        "Max landings per duty (per extra landing)" to HARD_LANDINGS_PER_EXTRA,
        "Max monthly flight hours (per minute)" to HARD_MONTHLY_PER_MIN,
        "Max weekly flight hours (per minute)" to HARD_WEEKLY_PER_MIN,
        "Language qualification" to HARD_LANGUAGE,
        "Type rating required" to HARD_TYPE_RATING,
        "Type rating currency" to HARD_TYPE_RATING_CURRENCY,
        "Crew size (per person)" to HARD_CREW_SIZE_PER_PERSON,
        "Reserve crews (per crew-day)" to HARD_RESERVE_PER_CREW_DAY,
        "Deadheads per day (per extra)" to HARD_DEADHEADS_PER_EXTRA,
        "Pairing chain integrity (per pair)" to HARD_CHAIN_INTEGRITY,
    )

    /**
     * Показания профиля ПОД ТЕМИ ЖЕ ИМЕНАМИ, что и ярус правил.
     *
     * Одно понятие — одно имя, независимо от профиля: иначе потребитель
     * отчёта снова угадывал бы, под каким ключом лежит порог отдыха.
     * Три величины имени в ярусе не имеют и названы своими.
     */
    fun readings(): Map<String, Int> = linkedMapOf(
        // Две ручки на стыковку и две на отдых: в базе и вне базы. Литерал
        // у обеих один — профиль по умолчанию области не различал; пакет
        // задаёт их порознь.
        "min_sit_home_min" to MIN_TURNAROUND_MIN,
        "min_sit_outstation_min" to MIN_TURNAROUND_MIN,
        "min_rest_home_min" to MIN_DUTY_REST_MIN,
        "min_rest_outstation_min" to MIN_DUTY_REST_MIN,
        "max_duty_span_min" to MAX_DAILY_DUTY_MINUTES,
        "max_monthly_block_min" to MAX_MONTHLY_FLIGHT_MINUTES,
        "max_landings_per_duty" to MAX_LANDINGS_PER_DUTY_OFF,
        "day_break_threshold_min" to DAY_BREAK_THRESHOLD_MIN,
        "max_weekly_block_min" to MAX_WEEKLY_FLIGHT_MINUTES,
        "max_consecutive_duty_days" to MAX_CONSECUTIVE_DUTY_DAYS,
        "crew_size" to CREW_SIZE_OFF,
        RESERVE_FAMILY to RESERVE_CREWS_OFF,
        "max_deadheads_per_flight" to DEADHEADS_OFF,
        "max_deadheads_per_day" to DEADHEADS_OFF,
        "base_tz_offset_min" to BASE_TZ_OFFSET_SAME_SCALE,
    )
}

private const val MIN_TURNAROUND_MIN = DefaultProviderRules.MIN_TURNAROUND_MIN
private const val MIN_DUTY_REST_MIN = DefaultProviderRules.MIN_DUTY_REST_MIN
private const val MAX_DAILY_DUTY_MINUTES = DefaultProviderRules.MAX_DAILY_DUTY_MINUTES
private const val MAX_MONTHLY_FLIGHT_MINUTES = DefaultProviderRules.MAX_MONTHLY_FLIGHT_MINUTES
private const val MAX_WEEKLY_FLIGHT_MINUTES = DefaultProviderRules.MAX_WEEKLY_FLIGHT_MINUTES
private const val MAX_CONSECUTIVE_DUTY_DAYS = DefaultProviderRules.MAX_CONSECUTIVE_DUTY_DAYS

/**
 * Что ПОСЛЕДНИЙ созданный провайдер прочитал в свои поля.
 *
 * Единственный честный источник показаний: провайдер записывает их в
 * конструкторе, и если он НЕ прочитал пороги пакета (ошибка, другой
 * профиль, сборка вне окна) — здесь окажутся литералы, и гейт
 * сравнения покраснеет. Именно этого свойства не было у ревизии 1.
 */
data class ProviderReadings(
    val profile: String,
    val readings: Map<String, Int>,
    val codeReadings: Map<String, List<String>>,
    val policyReadings: Map<String, DutyExtensionReading> = emptyMap(),
)

object LastProviderReadings {
    @Volatile private var last: ProviderReadings? = null

    fun record(profile: String, readings: Map<String, Int>,
               codeReadings: Map<String, List<String>> = emptyMap(),
               policyReadings: Map<String, DutyExtensionReading> = emptyMap()) {
        last = ProviderReadings(profile, LinkedHashMap(readings),
            codeReadings.mapValues { (_, codes) -> codes.toList() },
            policyReadings.mapValues { (_, reading) -> reading.detached() })
    }

    /** Compatibility view for numeric-only callers. */
    fun snapshot(): Pair<String, Map<String, Int>>? =
        last?.let { it.profile to LinkedHashMap(it.readings) }

    /** Numbers and designations belong to one provider construction, not two reads. */
    fun snapshotWithCodes(): ProviderReadings? = last?.let {
        it.copy(readings = LinkedHashMap(it.readings),
            codeReadings = it.codeReadings.mapValues { (_, codes) -> codes.toList() },
            policyReadings = it.policyReadings.mapValues { (_, reading) -> reading.detached() })
    }

    internal fun clearForTests() { last = null }
}

private data class IsoWeek(val year: Int, val week: Int)

private fun isoWeek(dt: LocalDateTime): IsoWeek =
    IsoWeek(dt.get(WeekFields.ISO.weekBasedYear()), dt.get(WeekFields.ISO.weekOfWeekBasedYear()))

/**
 * Одна нарезка нормативных смен для всех ограничений и отчёта цены
 * ночёвок (K-9 единой версии): назначения в полном порядке вылета, разрез
 * там, где разрыв до следующего вылета не меньше законного отдыха В ЭТОМ
 * МЕСТЕ (порог даёт вызывающий). Границы цепочек и полночь — не границы
 * смен. Алгоритм побайтно тот, что был в duties() провайдера; без
 * назначений — пустой список.
 */
internal object NormativeDuties {
    fun slice(assignments: List<FlightAssignment>, restMinimum: (FlightAssignment) -> Int): List<List<FlightAssignment>> {
        if (assignments.isEmpty()) return emptyList()
        val sorted = assignments.sortedWith(compareBy(
            { it.flight!!.departureUTCDateTime }, { it.flight!!.departureAirport?.code },
            { it.flight!!.arrivalUTCDateTime }, { it.flight!!.arrivalAirport?.code },
            { it.flight!!.flightNumber }))
        val out = ArrayList<List<FlightAssignment>>()
        var cur = arrayListOf(sorted.first())
        var dutyEnd = sorted.first().flight!!.arrivalUTCDateTime!!
        for (fa in sorted.drop(1)) {
            val f = fa.flight!!
            val gap = ChronoUnit.MINUTES.between(dutyEnd, f.departureUTCDateTime)
            if (gap >= restMinimum(cur.last())) {
                out.add(cur)
                cur = ArrayList()
                dutyEnd = f.arrivalUTCDateTime!!
            } else if (f.arrivalUTCDateTime!! > dutyEnd) dutyEnd = f.arrivalUTCDateTime!!
            cur.add(fa)
        }
        out.add(cur)
        return out
    }
}

class FlightCrewSchedulingConstraintProvider : ConstraintProvider {

    /**
     * Действующие пороги прогона: из пакета постановки, если он их дал,
     * иначе литералы профиля.
     *
     * Читаются ОДИН РАЗ при создании провайдера — а он создаётся заново
     * на каждый прогон, поэтому значение соответствует именно этому
     * прогону. Держатель заполняется до сборки решателя; прогон без
     * пакета оставляет его пустым, и здесь остаются прежние числа.
     */
    private val minSitHome: Int = RunThresholds.valueOr(
        "min_sit_home_min", DefaultProviderRules.MIN_TURNAROUND_MIN)
    private val minSitOut: Int = RunThresholds.valueOr(
        "min_sit_outstation_min", DefaultProviderRules.MIN_TURNAROUND_MIN)
    private val minRestHome: Int = RunThresholds.valueOr(
        "min_rest_home_min", DefaultProviderRules.MIN_DUTY_REST_MIN)
    private val minRestOut: Int = RunThresholds.valueOr(
        "min_rest_outstation_min", DefaultProviderRules.MIN_DUTY_REST_MIN)
    private val maxDutySpan: Int = RunThresholds.valueOr(
        "max_duty_span_min", DefaultProviderRules.MAX_DAILY_DUTY_MINUTES)
    private val dutyExtensions: List<DutyExtensionPolicy> =
        RunThresholds.current()["max_duty_span_min"]?.dutyExtensions.orEmpty()
            .map { it.copy(bases = it.bases?.toList()) }
    /** Мягкий порог семьи duty_extension_penalty; 0 = семьи нет, штраф спит.
     *  Пакетный путь сохраняет maxDutySpan как базу и несёт отдельные области;
     *  legacy applyDutyExtension без областей остаётся совместимым. */
    private val softDutyThreshold: Int = RunThresholds.valueOr(
        RunThresholds.DUTY_EXTENSION_THRESHOLD, 0)
    /** Порог существенности разброса кредитных часов (семья
     *  balanced_workload), минуты; 0 = порога нет, штрафуется весь разброс. */
    private val spreadThresholdMin: Int = RunThresholds.valueOr(
        RunThresholds.WORKLOAD_SPREAD_THRESHOLD, 0)
    private val maxMonthlyBlock: Int = RunThresholds.valueOr(
        "max_monthly_block_min", DefaultProviderRules.MAX_MONTHLY_FLIGHT_MINUTES)
    /** Недельный налёт: ручка пакета (ревизия литералов 2026-09-03);
     *  без пакета — литерал профиля; 0 = правила нет. */
    private val maxWeeklyBlock: Int = RunThresholds.valueOr(
        "max_weekly_block_min", DefaultProviderRules.MAX_WEEKLY_FLIGHT_MINUTES)
    /** Пакетный ли прогон (литералов профиля нет; правила — только из ВМП). */
    private val packagedRun: Boolean = RunThresholds.packaged()
    /** Порог «мёртвой зоны» стыковки (K-10, решение GPT-011 единой версии):
     *  минимум отдыха НЕ порождает максимума стыковки. Ручка пакета
     *  max_sit_duration → day_break_threshold_min; в пакетном прогоне без
     *  неё — 0 = правило не объявлено (семичасовая стыковка H0 законна,
     *  разрыв короче отдыха остаётся внутри смены и ограничен её размахом);
     *  без пакета — литерал профиля 360. */
    private val dayBreakThreshold: Int = RunThresholds.valueOr(
        "day_break_threshold_min", DefaultProviderRules.DAY_BREAK_THRESHOLD_MIN)
    /** Перечень типов судов правила допуска (семья флота); null — правила нет. */
    private val fleetTypes: Set<String>? = RunThresholds.fleetTypes()

    /**
     * Судится ли допуск на тип для рейса этого типа: правило семьи
     * флота в пакете — только типы из перечня ВМП; пакетный прогон без
     * такого правила — не судится (правила нет в ВМП, ревизия литералов
     * 2026-09-03); прогон без пакета — все типы, как прежде.
     */
    private fun fleetJudged(type: String?): Boolean = when {
        type.isNullOrEmpty() -> false
        fleetTypes != null -> type in fleetTypes
        packagedRun -> false
        else -> true
    }
    private val maxLandings: Int = RunThresholds.valueOr(
        "max_landings_per_duty", DefaultProviderRules.MAX_LANDINGS_PER_DUTY_OFF)
    /** Численность экипажа на рейсе; 0 = выключено (без пакета). */
    private val crewSize: Int = RunThresholds.valueOr(
        "crew_size", DefaultProviderRules.CREW_SIZE_OFF)
    /** Нормы суточного резерва по кодам баз; пусто = выключено (без пакета). */
    private val reserveNorm: Map<String, Int> =
        RunThresholds.keyedValues(DefaultProviderRules.RESERVE_FAMILY)
    /** Планы порождённых ограничений (ступень 3); пусто = прогон без пакета либо без logic. */
    private val logicPlans: List<LogicPlan> = RunThresholds.plans()
    /** Перегоны: кресел пассажира на рейс; 0 = возможность выключена (без пакета). */
    private val maxDeadheadsPerFlight: Int = RunThresholds.valueOr(
        "max_deadheads_per_flight", DefaultProviderRules.DEADHEADS_OFF)
    /** Перегоны: предел на сотрудника в сутки; 0 = предела нет. */
    private val maxDeadheadsPerDay: Int = RunThresholds.valueOr(
        "max_deadheads_per_day", DefaultProviderRules.DEADHEADS_OFF)

    /**
     * Место стыковки или отдыха между двумя рейсами одного человека — это
     * аэропорт прилёта первого (маршрутная непрерывность делает его же
     * аэропортом вылета второго). Дома или нет — по базе приписки.
     * Нет базы у человека или нет аэропорта прилёта у рейса — дома он
     * нигде: внебазовая ручка. Это правило, а не умолчание: выгрузка смен
     * режет так же и считает, скольким разрывам досталась каждая ручка.
     */
    private fun atHome(fa1: FlightAssignment): Boolean {
        val home = fa1.employee?.homeAirport?.code ?: return false
        return fa1.flight?.arrivalAirport?.code == home
    }
    private fun minSitAt(fa1: FlightAssignment): Int = if (atHome(fa1)) minSitHome else minSitOut
    private fun minRestAt(fa1: FlightAssignment): Int = if (atHome(fa1)) minRestHome else minRestOut

    init {
        // Показания записываются В МОМЕНТ СОЗДАНИЯ — это то, по чему
        // провайдер считает на самом деле. Отчёт применённого обязан
        // брать числа отсюда, а не из намерения резолвера: ревизия 1
        // собирала «показания» из параметров пакета и гейт «прислали =
        // применили» сравнивал пакет сам с собой.
        LastProviderReadings.record(TaskModes.PROFILE_DEFAULT, readings(), codeReadings(), policyReadings())
    }

    /** Показания ИМЕННО ЭТОГО прогона — для отчёта применённого. */
    fun readings(): Map<String, Int> {
        val m = linkedMapOf(
            "min_sit_home_min" to minSitHome,
            "min_sit_outstation_min" to minSitOut,
            "min_rest_home_min" to minRestHome,
            "min_rest_outstation_min" to minRestOut,
            "max_duty_span_min" to maxDutySpan,
            RunThresholds.DUTY_EXTENSION_THRESHOLD to softDutyThreshold,
            "max_monthly_block_min" to maxMonthlyBlock,
            "max_landings_per_duty" to maxLandings,
            "day_break_threshold_min" to dayBreakThreshold,
            "max_weekly_block_min" to maxWeeklyBlock,
            "workload_spread_threshold_min" to spreadThresholdMin,
            "max_consecutive_duty_days" to MAX_CONSECUTIVE_DUTY_DAYS,
            "crew_size" to crewSize,
            "max_deadheads_per_flight" to maxDeadheadsPerFlight,
            "max_deadheads_per_day" to maxDeadheadsPerDay,
            // Шкала времени: провайдер считает даты в шкале меток данных;
            // это и есть его фактическое показание (ненулевой сдвиг
            // отвергнут разрешителем до сборки — сюда не доходит).
            "base_tz_offset_min" to DefaultProviderRules.BASE_TZ_OFFSET_SAME_SCALE,
        )
        // Without a package or an explicit fleet, fleetJudged checks all types.
        // That fallback is not a measured empty fleet and has no code count.
        if (packagedRun || fleetTypes != null) {
            m[RunThresholds.FLEET_THRESHOLD] = fleetTypes?.size ?: 0
        }
        // Резерв: при нормах — по ручке на базу; без норм — один маркер
        // семейства «выключено», чтобы он не стоял рядом с включёнными базами.
        if (reserveNorm.isEmpty()) {
            m[DefaultProviderRules.RESERVE_FAMILY] = DefaultProviderRules.RESERVE_CREWS_OFF
        } else {
            for ((code, n) in reserveNorm) m[DefaultProviderRules.RESERVE_FAMILY + "[" + code + "]"] = n
        }
        return m
    }

    /** Actual conditional selector inputs captured by this provider, not reconstructed by the report. */
    fun policyReadings(): Map<String, DutyExtensionReading> =
        if (dutyExtensions.isEmpty()) emptyMap() else mapOf(
            "max_duty_span_min" to DutyExtensionReading(maxDutySpan, dutyExtensions).detached())

    /** Exact labels captured by this provider; no reconstruction from applied. */
    fun codeReadings(): Map<String, List<String>> =
        if (packagedRun || fleetTypes != null) mapOf(
            RunThresholds.FLEET_THRESHOLD to fleetTypes.orEmpty().sorted())
        else emptyMap()

    /**
     * Ограничения профиля + порождённые по планам ступени 3. Порождённые
     * идут в конец массива под своими именами («logic <8 hex>»): набор
     * имён фабрики — единственный честный перечень того, что считалось.
     */
    override fun defineConstraints(cf: ConstraintFactory): Array<Constraint> {
        val all = profileConstraints(cf) + logicPlans.map { buildLogic(cf, it) }.toTypedArray()
        // ДИАГНОСТИЧЕСКИЙ выключатель профилирования по исключению
        // (cosmos.profile.skip=имя,имя): только для замера скорости счёта
        // вне пакетных прогонов — при пакете гейты «исполнитель
        // зарегистрирован» честно откажут, и это правильно. В продуктовых
        // запусках свойство не ставится, перечень не меняется.
        val skip = System.getProperty("cosmos.profile.skip")?.split(',')
            ?.map { it.trim() }?.filter { it.isNotEmpty() }?.toSet().orEmpty()
        if (skip.isEmpty()) return all
        return all.filter { c ->
            val ref = c.constraintRef.id()
            ref !in skip && ref.substringAfterLast('/') !in skip
        }.toTypedArray()
    }

    /** Порождённое ограничение с нарезкой смен ЭТОГО провайдера (пороги прогона). */
    internal fun buildLogic(cf: ConstraintFactory, plan: LogicPlan): Constraint =
        LogicForms.build(cf, plan) { list -> duties(list) }

    private fun profileConstraints(cf: ConstraintFactory): Array<Constraint> = arrayOf(
        // -------------------- HARD -----------------------------------------
        // Explicit replacement of the former forced assignment: the
        // planning variable now allows unassigned values (audit
        // 2026-07-12), and this profile keeps full staffing mandatory.
        unassignedSlot(cf),
        requiredSkill(cf),
        flightConflict(cf),
        employeeUnavailability(cf),
        minimumTurnaround(cf),
        restBetweenDutyPeriods(cf),
        restBeforeHorizon(cf),
        routeContinuityHard(cf),
        maxDutyPeriodRestSegmented(cf),
        maxLandingsPerDuty(cf),
        crewSizePerFlight(cf),
        reserveCrewsPerBase(cf),
        maxMonthlyFlightHours(cf),
        maxWeeklyFlightHours(cf),
        languageQualification(cf),
        typeRatingRequired(cf),
        typeRatingCurrency(cf),
        // -------------------- SOFT -----------------------------------------
        transferBetweenTwoFlights(cf),
        positioningCost(cf),
        layoverNights(cf),
        calendarWindowLayovers(cf),
        dutyExtensionOverThreshold(cf),
        preferredDayOffViolation(cf),
        undesiredAirportViolation(cf),
        reserveUsage(cf),
        deadheadsPerDay(cf),
        pairingChainIntegrity(cf),
        consecutiveDutyDays(cf),
        fairRedEyeDistribution(cf),
        fairWeekendDistribution(cf),
        creditHoursSpread(cf),
        creditHoursSpreadOverThreshold(cf),
    )

    // ----------------------------------------------------------------- HARD

    // Internal (not private) so ConstraintVerifier tests reach it.
    internal fun unassignedSlot(cf: ConstraintFactory): Constraint =
        cf.forEachIncludingUnassigned(FlightAssignment::class.java)
            .filter { it.employee == null && !it.deadhead }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_UNASSIGNED_SLOT.toLong()))
            .asConstraint("Unassigned slot")

    // Internal (not private) so ConstraintVerifier tests reach it.
    internal fun requiredSkill(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { !it.deadhead && !it.hasRequiredSkills() }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_REQUIRED_SKILL.toLong()))
            .asConstraint("Required skill")

    /**
     * Один человек на двух пересекающихся рейсах — ФИЗИЧЕСКИ невозможно,
     * и цена этого обязана быть выше любой выгоды от такого «решения».
     *
     * До 2026-08-23 пара стоила 1 очко за час пересечения (минимум 1) —
     * наследие опыта со сглаживанием градиента. При такой цене решателю
     * было ВЫГОДНО раздвоить человека: пустое место стоит 30, а
     * недостающий экипаж резерва — 1, то есть невозможное покупалось за
     * бесценок (ничья 1 = 1 против резерва обнаружилась на первом же
     * прогоне с повышенной нормой). Теперь пол цены — 50, как у «нет
     * допуска на тип судна»: невозможное дороже неукомплектованного.
     * Пропорциональность времени пересечения СОХРАНЕНА (градиент, ради
     * которого множитель вводился): цена = 50 × max(1, часы).
     */
    private fun flightConflict(cf: ConstraintFactory): Constraint =
        cf.forEachUniquePair(FlightAssignment::class.java,
                Joiners.equal(FlightAssignment::employee),
                Joiners.overlapping(
                    { it.flight!!.departureUTCDateTime },
                    { it.flight!!.arrivalUTCDateTime }))
            .penalize(HardSoftScore.ofHard(
                DefaultProviderRules.HARD_FLIGHT_CONFLICT_PER_HOUR.toLong())) { fa1, fa2 ->
                val start = maxOf(fa1.flight!!.departureUTCDateTime!!,
                    fa2.flight!!.departureUTCDateTime!!)
                val end = minOf(fa1.flight!!.arrivalUTCDateTime!!,
                    fa2.flight!!.arrivalUTCDateTime!!)
                maxOf(1L, ChronoUnit.MINUTES.between(start, end) / 60)
            }
            .asConstraint("Flight conflict")

    private fun employeeUnavailability(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter(FlightAssignment::isUnavailableEmployee)
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_EMPLOYEE_UNAVAILABLE.toLong()))
            .asConstraint("Employee unavailable")

    /** Gap in minutes between fa1's arrival and fa2's departure. */
    private fun gapMinutes(fa1: FlightAssignment, fa2: FlightAssignment): Int =
        ChronoUnit.MINUTES.between(
            fa1.flight!!.arrivalUTCDateTime, fa2.flight!!.departureUTCDateTime).toInt()

    /**
     * Соседние пары назначений сотрудника — общий скелет правил
     * стыковки, отдыха, непрерывности и стоимости пересадки.
     *
     * Была тройным соединением «нет третьего между» (join + ifNotExists):
     * профилирование по исключению 2026-08-25 показало 70-кратную цену —
     * 260 ходов/с против 18426 без этого семейства: инкремент каждого
     * хода переоценивал кубическое число пар сотрудника. Теперь —
     * пер-сотрудниковая группировка со списком (как у нарезки смен,
     * которая узким местом НЕ была): соседство = соседние элементы в
     * ПОЛНОМ порядке вылета [DEPARTURE_ORDER] (тот же ключ, что у
     * duties(): порядок не зависит от истории ходов). Пер-парная
     * величина каждого правила суммируется по сотруднику — вклады имён
     * сохранены точь-в-точь, судья каждой пары прежний.
     */
    private fun sumAdjacent(assignments: List<FlightAssignment>,
                            perPair: (FlightAssignment, FlightAssignment) -> Long): Long {
        if (assignments.size < 2) return 0L
        val sorted = assignments.sortedWith(DEPARTURE_ORDER)
        var total = 0L
        for (i in 1 until sorted.size) total += perPair(sorted[i - 1], sorted[i])
        return total
    }

    private fun perEmployeeAdjacency(cf: ConstraintFactory, weight: HardSoftScore,
                                     name: String,
                                     perPair: (FlightAssignment, FlightAssignment) -> Long): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.toList<FlightAssignment>())
            .filter { _, assignments -> sumAdjacent(assignments, perPair) > 0L }
            .penalize(weight) { _, assignments -> sumAdjacent(assignments, perPair) }
            .asConstraint(name)

    /** RULES v2: consecutive legs of one duty day need >= 60 min turnaround. */
    internal fun minimumTurnaround(cf: ConstraintFactory): Constraint =
        perEmployeeAdjacency(cf,
            HardSoftScore.ofHard(DefaultProviderRules.HARD_MIN_TURNAROUND_PER_MIN.toLong()),
            "Minimum turnaround") { fa1, fa2 ->
            val gap = gapMinutes(fa1, fa2)
            if (gap < minSitAt(fa1)) maxOf(1, minSitAt(fa1) - gap).toLong() else 0L
        }

    /** RULES v2: a gap too long to be a sit (>= dead-zone threshold) must be
     *  a full rest between duty periods — the dead zone is illegal. K-10:
     *  порог зоны — ручка (0 = правило не объявлено, штрафа нет; разрыв
     *  короче отдыха тогда остаётся внутри смены и судится её размахом). */
    internal fun restBetweenDutyPeriods(cf: ConstraintFactory): Constraint =
        perEmployeeAdjacency(cf,
            HardSoftScore.ofHard(DefaultProviderRules.HARD_REST_PER_MIN.toLong()),
            "Rest between duty periods") { fa1, fa2 ->
            val gap = gapMinutes(fa1, fa2)
            if (dayBreakThreshold > 0 && gap in dayBreakThreshold until minRestAt(fa1))
                (minRestAt(fa1) - gap).toLong() else 0L
        }

    /** Route continuity (audit round 6, P6.1): consecutive flights of one
     *  employee must be airport-continuous — arriving at B and next departing
     *  from C is physically impossible. Previously a teleport only cost the
     *  SOFT 800 of "Transfer between two flights" (kept below as the cost
     *  signal), so the solver could buy impossible routings with soft
     *  savings. Internal (not private) so ConstraintVerifier tests reach it. */
    internal fun routeContinuityHard(cf: ConstraintFactory): Constraint =
        perEmployeeAdjacency(cf,
            HardSoftScore.ofHard(DefaultProviderRules.HARD_ROUTE_CONTINUITY.toLong()),
            "Route continuity (hard)") { fa1, fa2 ->
            if (fa1.flight!!.arrivalAirport != fa2.flight!!.departureAirport) 1L else 0L
        }

    /** Max duty period, rest-segmented (audit round 6, P6.2). The former rule
     *  grouped flights by calendar departure date, so a duty crossing
     *  midnight (20:00-23:00 plus 00:00-12:00 next day, 1h gap) split into
     *  two innocent-looking halves and scored 0 hard despite a 16h span.
     *  Here we collect each employee's assignments, sort by departure,
     *  split into duty periods wherever the gap is a legal rest
     *  (>= minDutyRest, see the RULES v2 header) and bound the elapsed
     *  span of every duty period by maxDutySpan.
     *  Internal (not private) so ConstraintVerifier tests reach it. */
    internal fun maxDutyPeriodRestSegmented(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.toList<FlightAssignment>())
            .filter { _, assignments -> dutySpanOverageMinutes(assignments) > 0L }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_MAX_DUTY_PER_MIN.toLong())) { _, assignments ->
                dutySpanOverageMinutes(assignments)
            }
            .asConstraint("Max duty period (rest-segmented)")

    /**
     * Смены одного человека: назначения по вылету, разрез там, где разрыв
     * до следующего вылета не меньше законного отдыха В ЭТОМ МЕСТЕ — дома
     * или вне базы. Одна нарезка для обоих правил о сменах, чтобы
     * продолжительность и посадки считались по одним и тем же границам.
     */
    /** Полный порядок вылета (не только время): список из коллектора
     *  приходит в порядке истории ходов, и при равном вылете сосед (а с
     *  ним место отдыха и порог) зависел бы от истории. Тот же ключ у
     *  нарезки смен и выгрузки для сертификатора (solution_export). */
    private val DEPARTURE_ORDER: Comparator<FlightAssignment> = compareBy(
        { it.flight!!.departureUTCDateTime },
        { it.flight!!.departureAirport?.code },
        { it.flight!!.arrivalUTCDateTime },
        { it.flight!!.arrivalAirport?.code },
        { it.flight!!.flightNumber })

    /** K-9: та же нарезка через [NormativeDuties] — потребители: правила о
     *  сменах, формы ступени 3, отчёт цены ночёвок; internal — для стендов. */
    internal fun duties(assignments: List<FlightAssignment>): List<List<FlightAssignment>> =
        NormativeDuties.slice(assignments, ::minRestAt)

    /** Sum over the employee's duty periods of max(0, span - cap). */
    private fun dutySpanOverageMinutes(assignments: List<FlightAssignment>): Long {
        // 0 = правила о пределе смены нет (пакетный прогон без него).
        if (maxDutySpan <= 0) return 0L
        var overage = 0L
        for (duty in duties(assignments)) {
            val start = duty.first().flight!!.departureUTCDateTime!!
            val end = duty.maxOf { it.flight!!.arrivalUTCDateTime!! }
            val cap = DutyExtensionPolicies.capFor(maxDutySpan, dutyExtensions,
                duty.first().employee, start, end)
            overage += maxOf(0L, ChronoUnit.MINUTES.between(start, end) - cap)
        }
        return overage
    }

    /**
     * Посадок за смену — исполнитель параметра `max_landings_per_duty`
     * пакета постановки. Без пакета правило ВЫКЛЮЧЕНО (литерал 0): у
     * профиля по умолчанию его не было, и включать его своим числом
     * значило бы менять счёт прогонов без пакета.
     * Internal (not private) so ConstraintVerifier tests reach it.
     */
    internal fun maxLandingsPerDuty(cf: ConstraintFactory): Constraint =
        if (maxLandings <= 0)
            // Выключено: ограничение остаётся зарегистрированным (имя в
            // анализе счёта, проверка тестами), но без группировки по
            // сотруднику и нарезки смен на каждом ходе ради нуля — пустой
            // фильтр на одиночном потоке.
            cf.forEach(FlightAssignment::class.java)
                .filter { false }
                .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_LANDINGS_PER_EXTRA.toLong()))
                .asConstraint("Max landings per duty")
        else
            cf.forEach(FlightAssignment::class.java)
                .filter { it.employee != null }
                .groupBy(FlightAssignment::employee,
                    ConstraintCollectors.toList<FlightAssignment>())
                .filter { _, assignments -> landingsOverage(assignments) > 0L }
                .penalize(HardSoftScore.ofHard(
                    DefaultProviderRules.HARD_LANDINGS_PER_EXTRA.toLong())) { _, assignments ->
                    landingsOverage(assignments)
                }
                .asConstraint("Max landings per duty")

    private fun landingsOverage(assignments: List<FlightAssignment>): Long {
        if (maxLandings <= 0) return 0L
        var over = 0L
        // Посадки считает пилотирующий: перегон в смене есть (размах,
        // отдых), а посадки пассажиру не идут.
        for (duty in duties(assignments)) over += maxOf(0,
            duty.count { !it.deadhead } - maxLandings).toLong()
        return over
    }

    /**
     * Численность экипажа — исполнитель параметра `crew_size` пакета
     * (правило свода о численности экипажа). Единица — рейс-участок
     * набора; считаются
     * РАЗЛИЧНЫЕ сотрудники на местах рейса: пустое место уменьшает число
     * различных, один человек на двух местах считается одним. Без пакета
     * правило ВЫКЛЮЧЕНО (литерал 0). Состав мест на рейсе (ровно N) —
     * не предмет счёта: его ни один ход не меняет, это проверка фактов
     * до старта (RunThresholds.checkFacts). Роли мест судит «Required
     * skill». Internal (not private) so ConstraintVerifier tests reach it.
     */
    internal fun crewSizePerFlight(cf: ConstraintFactory): Constraint =
        if (crewSize <= DefaultProviderRules.CREW_SIZE_OFF)
            cf.forEach(FlightAssignment::class.java)
                .filter { false }
                .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_CREW_SIZE_PER_PERSON.toLong()))
                .asConstraint("Crew size")
        else
            cf.forEachIncludingUnassigned(FlightAssignment::class.java)
                .filter { it.flight != null && !it.deadhead }
                .groupBy({ it.flight!! }, ConstraintCollectors.toList<FlightAssignment>())
                .filter { _, seats -> crewMismatch(seats) > 0L }
                .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_CREW_SIZE_PER_PERSON.toLong())) { _, seats ->
                    crewMismatch(seats)
                }
                .asConstraint("Crew size")

    private fun crewMismatch(seats: List<FlightAssignment>): Long {
        val people = seats.mapNotNull { it.employee?.id }.toHashSet().size
        return Math.abs(people - crewSize).toLong()
    }

    /**
     * Классический способ: все места одной цепочки утверждённого
     * пейринга — ОДИН сотрудник. Метки цепочек ставит прогон с
     * pairingRunId; без меток ограничение молчит (прежние прогоны не
     * меняются ни на очко). Судятся только пары ДВУХ НАЗНАЧЕННЫХ мест
     * с разными людьми: пустое место судит «Unassigned slot», и это не
     * буквоедство — первая ревизия штрафовала пару «назначенный ↔
     * пустой», и назначение ПЕРВОГО места цепочки стоило дороже пустоты
     * (50 за пару против 30 за непокрытость): конструктив и поиск
     * оставляли цепочки пустыми НАВСЕГДА (мини: −682 жёстких за 35 мс и
     * ноль ходов за 20 минут — гора, на которую нельзя взойти по одному
     * месту). Классовый фикс: наполнение цепочки одним человеком
     * монотонно улучшает счёт по построению.
     * Internal (not private) so ConstraintVerifier tests reach it.
     */
    internal fun pairingChainIntegrity(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.chainId != null && it.employee != null }
            .join(cf.forEach(FlightAssignment::class.java)
                    .filter { it.chainId != null && it.employee != null },
                Joiners.equal(FlightAssignment::chainId),
                Joiners.lessThan(FlightAssignment::id))
            .filter { a, b -> a.employee!!.id != b.employee!!.id }
            .penalize(HardSoftScore.ofHard(
                DefaultProviderRules.HARD_CHAIN_INTEGRITY.toLong()))
            .asConstraint("Pairing chain integrity")

    /**
     * Перегонов сотрудника в сутки — не больше порога пакета (аналогия
     * правила одной из линий о суточном лимите перегонов, гранула
     * «сутки»). Возможность без
     * порогов выключена: пустой фильтр, имя остаётся зарегистрированным.
     * Internal (not private) so ConstraintVerifier tests reach it.
     */
    internal fun deadheadsPerDay(cf: ConstraintFactory): Constraint =
        if (maxDeadheadsPerDay <= 0 || maxDeadheadsPerFlight <= 0)
            cf.forEach(FlightAssignment::class.java)
                .filter { false }
                .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_DEADHEADS_PER_EXTRA.toLong()))
                .asConstraint("Deadheads per day")
        else
            cf.forEach(FlightAssignment::class.java)
                .filter { it.deadhead && it.employee != null }
                .groupBy(FlightAssignment::employee,
                    { fa -> fa.flight!!.getDepartureUTCDate() },
                    ConstraintCollectors.count())
                .filter { _, _, n -> n > maxDeadheadsPerDay }
                .penalize(HardSoftScore.ofHard(
                    DefaultProviderRules.HARD_DEADHEADS_PER_EXTRA.toLong())) { _, _, n ->
                    (n - maxDeadheadsPerDay).toLong()
                }
                .asConstraint("Deadheads per day")

    /** Клетка резерва: база приписки, роль состава, сутки. */
    private data class ReserveCell(val base: String, val role: String, val day: LocalDate)

    /** Рейс касается суток, если дата вылета ≤ сутки ≤ дата прилёта (UTC). */
    private fun touchesDay(f: Flight, d: LocalDate): Boolean =
        !f.getDepartureUTCDate().isAfter(d) && !f.arrivalUTCDateTime!!.toLocalDate().isBefore(d)

    /**
     * Суточный резерв по базам — исполнитель параметров `reserve_crews`
     * пакета (правило свода о суточном резерве): для каждой названной
     * базы и каждых суток
     * набора свободных экипажей не меньше нормы базы.
     *
     * Прочтение (заявлено в отчёте): экипаж в резерве — по одному
     * СВОБОДНОМУ сотруднику базы на КАЖДУЮ роль состава мест (роли —
     * различные requiredSkill мест набора); экипажей = наименьшее по
     * ролям; свободен в сутки — ни одно назначение не касается суток
     * (даты вылета и прилёта, UTC) и сутки не в unavailableDays; сутки
     * горизонта — календарные даты UTC от первого вылета до последнего
     * прилёта набора.
     *
     * Нулевая группа в потоке ограничений молчит, а «свободных ноль» —
     * ровно случай, ради которого правило пишется. Поэтому клетки
     * (база × роль × сутки) строятся из норм ручки и фактов и
     * подставляются нулём там, где свободных нет (concat).
     * Internal (not private) so ConstraintVerifier tests reach it.
     */
    internal fun reserveCrewsPerBase(cf: ConstraintFactory): Constraint {
        val weight = HardSoftScore.ofHard(DefaultProviderRules.HARD_RESERVE_PER_CREW_DAY.toLong())
        if (reserveNorm.isEmpty()) {
            return cf.forEach(Employee::class.java)
                .filter { false }
                .penalize(weight)
                .asConstraint("Reserve crews")
        }
        val norm: Map<String, Int> = reserveNorm

        // Сутки горизонта (K-11): объявленное window_horizon книги, а без
        // него — единый fallback от первого до ПОСЛЕДНЕГО ВЫЛЕТА
        // (LogicForms.horizonStream — тот же универсум у fit и checkFacts).
        // Рейс относится к горизонту по вылету; прилёт ночного рейса после
        // конца горизонта (H0: PL401 30.09 23:30 → 01.10 01:10) прежде
        // создавал фиктивный 28-й день резерва (находка аудитора 05.09,
        // отчёт GPT 5.2; единый план, день 1).
        val days: UniConstraintStream<LocalDate> = LogicForms.horizonStream(cf)
            .map(java.util.function.Function<Horizon, Iterable<LocalDate>> { h ->
                h.lo.datesUntil(h.hi.plusDays(1)).toList()
            })
            .flattenLast(java.util.function.Function<Iterable<LocalDate>, Iterable<LocalDate>> { it })

        // Роли состава — из мест НАБОРА (и незанятых тоже: состав — данные).
        // Перегонные кресла решателя — ВНЕ состава (их «роль» — пустая
        // строка): без фильтра пустая роль попадает в универсум клеток,
        // свободных с нею нет ни у кого, и min по ролям навсегда ноль —
        // недостача резерва становится полной константой без градиента
        // (длинные прогоны полного: ровно Σнорм×сутки при свободных людях).
        val roles: UniConstraintStream<String> = cf.forEachIncludingUnassigned(FlightAssignment::class.java)
            .filter { !it.deadhead && it.requiredSkill.isNotEmpty() }
            .groupBy(java.util.function.Function<FlightAssignment, String> { it.requiredSkill })

        // Свободные клетки (сотрудник базы из норм, роль состава, сутки):
        // доступен в сутки и ни одно его назначение суток не касается.
        // ifNotExists по классу идёт forEach-семантикой: незанятое место
        // (employee == null) никого не занимает.
        val free: TriConstraintStream<Employee, String, LocalDate> = cf.forEach(Employee::class.java)
            .filter { e -> norm.containsKey(e.homeAirport?.code ?: "") }
            .map(java.util.function.Function<Employee, Employee> { it },
                 java.util.function.Function<Employee, Iterable<String>> { it.skills })
            .flattenLast(java.util.function.Function<Iterable<String>, Iterable<String>> { it })
            .ifExists(roles, Joiners.equal(
                java.util.function.BiFunction<Employee, String, String> { _, s -> s },
                java.util.function.Function<String, String> { it }))
            .join(days)
            .filter { e, _, d -> !e.unavailableDays.contains(d) }
            .ifNotExists(FlightAssignment::class.java,
                Joiners.equal(
                    ai.timefold.solver.core.api.function.TriFunction<Employee, String, LocalDate, Employee?> { e, _, _ -> e },
                    java.util.function.Function<FlightAssignment, Employee?> { it.employee }),
                Joiners.filtering(
                    ai.timefold.solver.core.api.function.QuadPredicate<Employee, String, LocalDate, FlightAssignment> { _, _, d, fa ->
                        touchesDay(fa.flight!!, d)
                    }))

        val counted: BiConstraintStream<ReserveCell, Long> = free
            .groupBy(ai.timefold.solver.core.api.function.TriFunction<Employee, String, LocalDate, ReserveCell> { e, r, d ->
                ReserveCell(e.homeAirport!!.code, r, d)
            }, ConstraintCollectors.countTri())
        val countedKeys: UniConstraintStream<ReserveCell> = counted
            .map(java.util.function.BiFunction<ReserveCell, Long, ReserveCell> { c, _ -> c })

        // Универсум клеток из норм ручки и фактов: база без людей и роль
        // без свободных дают честную нехватку, а не молчаливый ноль.
        val universe: BiConstraintStream<Pair<String, LocalDate>, String> = days
            .flattenLast(java.util.function.Function<LocalDate, Iterable<Pair<String, LocalDate>>> { d ->
                norm.keys.map { b -> b to d }
            })
            .join(roles)
        val zero: UniConstraintStream<ReserveCell> = universe
            .ifNotExists(countedKeys, Joiners.equal(
                java.util.function.BiFunction<Pair<String, LocalDate>, String, ReserveCell> { bd, r ->
                    ReserveCell(bd.first, r, bd.second)
                },
                java.util.function.Function<ReserveCell, ReserveCell> { it }))
            .map(java.util.function.BiFunction<Pair<String, LocalDate>, String, ReserveCell> { bd, r ->
                ReserveCell(bd.first, r, bd.second)
            })
        val cells: BiConstraintStream<ReserveCell, Long> =
            counted.concat(zero, java.util.function.Function<ReserveCell, Long> { 0L })

        return cells
            .groupBy(java.util.function.BiFunction<ReserveCell, Long, String> { c, _ -> c.base },
                     java.util.function.BiFunction<ReserveCell, Long, LocalDate> { c, _ -> c.day },
                     ConstraintCollectors.min(java.util.function.BiFunction<ReserveCell, Long, Long> { _, n -> n }))
            .filter { b, _, m -> m < norm.getValue(b) }
            .penalize(weight) { b, _, m -> norm.getValue(b) - m }
            .asConstraint("Reserve crews")
    }

    // Internal (not private) so ConstraintVerifier tests reach it.
    // Ревизия 1.17 (история сотрудника): к налёту месяца среза прибавляется
    // остаток с начала месяца из истории (Employee.carryMonthBlockMin);
    // без истории остаток 0 — по заявлению кнопки «история не передана».
    internal fun maxMonthlyFlightHours(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && !it.deadhead }
            .groupBy(FlightAssignment::employee,
                { it.flight!!.getYearMonth() },
                ConstraintCollectors.sum<FlightAssignment> { fa -> fa.flight!!.getDurationMinutes().toLong() })
            .filter { e, ym, total -> maxMonthlyBlock > 0 && total + e!!.carryMonthBlockMin(ym) > maxMonthlyBlock }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_MONTHLY_PER_MIN.toLong())) { e, ym, total ->
                total + e!!.carryMonthBlockMin(ym) - maxMonthlyBlock }
            .asConstraint("Max monthly flight hours")

    /**
     * Отдых до первой смены горизонта (ревизия 1.17): от конца последней
     * смены истории до первого вылета сотрудника — не короче пола отдыха
     * по месту последней смены (дом — база приписки, иначе вне базы).
     * Без истории или без последней смены ограничение молчит — это
     * заявленное допущение, а не исполненное правило.
     */
    internal fun restBeforeHorizon(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && it.employee!!.historyLastDutyEndAt() != null }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.min<FlightAssignment, java.time.LocalDateTime> { fa -> fa.flight!!.departureUTCDateTime!! })
            .filter { e, first -> restBeforeHorizonShortfall(e!!, first) > 0L }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_REST_PER_MIN.toLong())) { e, first ->
                restBeforeHorizonShortfall(e!!, first) }
            .asConstraint("Rest before horizon (history)")

    private fun restBeforeHorizonShortfall(e: Employee, firstDeparture: java.time.LocalDateTime): Long {
        val end = e.historyLastDutyEndAt() ?: return 0L
        val home = e.homeAirport?.code
        val station = e.history?.historyLastDutyStation
        val need = if (home != null && station == home) minRestHome else minRestOut
        val gap = ChronoUnit.MINUTES.between(end, firstDeparture)
        return if (gap < need) (need - gap) else 0L
    }

    private fun maxWeeklyFlightHours(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && !it.deadhead }
            .groupBy(FlightAssignment::employee,
                { isoWeek(it.flight!!.departureUTCDateTime!!) },
                ConstraintCollectors.sum<FlightAssignment> { fa -> fa.flight!!.getDurationMinutes().toLong() })
            .filter { _, _, total -> maxWeeklyBlock > 0 && total > maxWeeklyBlock }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_WEEKLY_PER_MIN.toLong())) { _, _, total -> total - maxWeeklyBlock }
            .asConstraint("Max weekly flight hours")

    private fun languageQualification(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { !it.deadhead }
            .filter { it.employee != null
                && it.flight!!.getRequiredLanguages().isNotEmpty()
                && !it.employee!!.speaksAny(it.flight!!.getRequiredLanguages()) }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_LANGUAGE.toLong()))
            .asConstraint("Language qualification")

    private fun typeRatingRequired(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null
                && fleetJudged(it.flight?.aircraftType)
                && !it.employee!!.isQualifiedFor(it.flight!!.aircraftType) }
            .penalize(HardSoftScore.ofHard(DefaultProviderRules.HARD_TYPE_RATING.toLong()))
            .asConstraint("Type rating required")

    /**
     * СВЕЖЕСТЬ допуска: допуск на тип есть, но не действует на время
     * рейса. Проверяется ТОЛЬКО там, где набор несёт датированные окна
     * (`qualificationWindows`): их отсутствие значит «срок не передан», а
     * не «просрочен» — молчаливое умолчание здесь запрещено, и правило
     * просто не срабатывает, что видно в отчёте (coverage.qualifications).
     *
     * Отдельное ограничение, а не ужесточение «Type rating required»:
     * «допуска нет» и «допуск не действует» — разные нарушения с разными
     * причинами, и читатель отчёта обязан их различать.
     * Internal (not private) so ConstraintVerifier tests reach it.
     */
    internal fun typeRatingCurrency(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa ->
                val e = fa.employee ?: return@filter false
                val f = fa.flight ?: return@filter false
                val type = f.aircraftType
                if (!fleetJudged(type)) return@filter false
                // Допуска нет вовсе — это другое правило; окон нет —
                // срок не передан, судить не о чем.
                if (!e.isQualifiedFor(type)) return@filter false
                if (e.qualificationWindows[type].isNullOrEmpty()) return@filter false
                val dep = f.departureUTCDateTime ?: return@filter false
                val arr = f.arrivalUTCDateTime ?: return@filter false
                !e.holdsQualification(type, epochMin(dep), epochMin(arr))
            }
            .penalize(HardSoftScore.ofHard(
                DefaultProviderRules.HARD_TYPE_RATING_CURRENCY.toLong()))
            .asConstraint("Type rating currency")

    // ----------------------------------------------------------------- SOFT

    private fun transferBetweenTwoFlights(cf: ConstraintFactory): Constraint =
        perEmployeeAdjacency(cf, HardSoftScore.ofSoft(800),
            "Transfer between two flights") { fa1, fa2 ->
            if (fa1.flight!!.arrivalAirport != fa2.flight!!.departureAirport) 1L else 0L
        }

    private fun positioningCost(cf: ConstraintFactory): Constraint =
        cf.forEach(Employee::class.java)
            .join(FlightAssignment::class.java,
                Joiners.equal({ it: Employee -> it }, FlightAssignment::employee))
            .ifNotExists(FlightAssignment::class.java,
                Joiners.equal({ emp, _ -> emp }, FlightAssignment::employee),
                Joiners.greaterThan({ _, fa -> fa.getDepartureUTCDateTime() },
                    FlightAssignment::getDepartureUTCDateTime))
            .filter { emp, fa ->
                emp.homeAirport != null && emp.homeAirport != fa.flight!!.departureAirport
            }
            .penalize(HardSoftScore.ofSoft(500))
            .asConstraint("Positioning deadhead cost")

    /**
     * Ночи вне базы по назначениям сотрудника (ревизия семантики
     * 2026-09-03, сертификат H0: прилёт вне базы с возвратом тем же
     * днём ночёвкой не является). Ночь — календарная дата, которую
     * сотрудник заканчивает вне базы: от даты прилёта вне базы до даты
     * следующего вылета (исключительно); после последнего назначения
     * горизонта — одна ночь (та же единица, что у сертификата). Окно
     * дней недели (calendar_windows) — те же ночи, отобранные по дню
     * начала. Единица штрафа — ночь, поэтому число ночей едет
     * множителем, и «совпадений × вес» разбора счёта читается как
     * «ночей × вес» через soft / вес.
     */
    internal fun layoverNightsOf(assignments: List<FlightAssignment>,
                                 days: Set<java.time.DayOfWeek>? = null): Long =
        // Контракт Б (план 05.09.2026, согласован с аудитором; K-12/K-32
        // единой версии): граница горизонта сама по себе не основание ни
        // начислить ночёвку, ни признать её отсутствующей — после
        // ПОСЛЕДНЕГО назначения продолжение неизвестно, ночь не
        // начисляется автоматически; полночная стыковка внутри одной
        // нормативной смены (duties()) ночью не является; разрыв станций —
        // неизвестное, не ночь; явные интервалы истории закрывают границы.
        // Неопределённость называет отчёт RosterLayoverCost / сертификат.
        RosterLayoverCost.measure(assignments, dutySlicer = ::duties).count(days)

    internal fun layoverNights(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && it.employee!!.homeAirport != null }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.toList<FlightAssignment>())
            .filter { _, assignments -> layoverNightsOf(assignments) > 0L }
            .penalize(HardSoftScore.ofSoft(40)) { _, assignments ->
                layoverNightsOf(assignments) }
            .asConstraint("Layover nights away from home")

    // Мягкая семья calendar_windows (обезличенный шаблон; родилась
    // трафаретом демо 2026-08-29, правило «Ночёвки в конце недели»):
    // ночёвка вне базы, чей календарный день попадает в окно дней
    // недели правила пакета. Окно и правило — фактами из формы
    // пакета (CalendarWindowRule), вес — переопределением весов
    // пакета; литерал 40 — как у соседа layoverNights, работает
    // только в прогоне без пакета. Ночёвка считается по той же
    // семантике, что у соседа: (сотрудник, дата прилёта вне базы).
    // internal, не private: верификатор теста зовёт напрямую
    // (образец — соседние *ConstraintTest).
    internal fun calendarWindowLayovers(cf: ConstraintFactory): Constraint =
        cf.forEach(org.acme.flighcrewscheduling.domain
                .CalendarWindowRule::class.java)
            .filter { it.action == "layover_penalty" }
            .join(FlightAssignment::class.java)
            .filter { _, fa -> fa.employee != null && fa.employee!!.homeAirport != null }
            .groupBy({ rule, _ -> rule },
                { _, fa -> fa.employee },
                ConstraintCollectors.toList { _, fa -> fa })
            .filter { rule, _, assignments -> layoverNightsOf(assignments, rule.days) > 0L }
            .penalize(HardSoftScore.ofSoft(40)) { rule, _, assignments ->
                layoverNightsOf(assignments, rule.days) }
            .asConstraint("Calendar window layover penalty")

    // Мягкая семья duty_extension_penalty (обезличенный шаблон;
    // родилась конвейером 2026-08-31 по заявке демо «Полярис-нано»,
    // правило «Продление смены с согласия»): смена длиннее мягкого
    // порога (базовый предел смены пакета) штрафуется ЗА КАЖДУЮ
    // такую смену; жёсткая грань — потолок продления (подмена
    // maxDutySpan в RunThresholds.applyDutyExtension). Порог 0 =
    // семьи в пакете нет, ограничение спит. Нарезка смен — та же
    // duties(), что у жёстких правил о сменах (одни границы). Вес —
    // переопределением весов пакета; литерал 40 — как у соседей,
    // работает только в прогоне без пакета.
    // internal, не private: верификатор теста зовёт напрямую.
    internal fun dutyExtensionOverThreshold(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { softDutyThreshold > 0 && it.employee != null }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.toList<FlightAssignment>())
            .filter { _, assignments ->
                dutiesOverThreshold(assignments) > 0L }
            .penalize(HardSoftScore.ofSoft(40)) { _, assignments ->
                dutiesOverThreshold(assignments)
            }
            .asConstraint("Duty extension over threshold")

    /** Число смен сотрудника длиннее мягкого порога семьи. */
    private fun dutiesOverThreshold(assignments: List<FlightAssignment>): Long {
        var over = 0L
        for (duty in duties(assignments)) {
            val start = duty.first().flight!!.departureUTCDateTime!!
            val end = duty.maxOf { it.flight!!.arrivalUTCDateTime!! }
            if (ChronoUnit.MINUTES.between(start, end) > softDutyThreshold) over++
        }
        return over
    }

    private fun preferredDayOffViolation(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null
                && it.employee!!.preferredDaysOff.contains(it.flight!!.getDepartureUTCDate()) }
            .penalize(HardSoftScore.ofSoft(50)) { (it.employee!!.seniorityWeight() * 10).toLong() }
            .asConstraint("Preferred day off violation")

    private fun undesiredAirportViolation(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter {
                val e = it.employee ?: return@filter false
                val dep = it.flight!!.departureAirport!!.code
                val arr = it.flight!!.arrivalAirport!!.code
                dep in e.undesiredAirports || arr in e.undesiredAirports
            }
            .penalize(HardSoftScore.ofSoft(20)) { (it.employee!!.seniorityWeight() * 5).toLong() }
            .asConstraint("Undesired airport")

    private fun reserveUsage(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && it.employee!!.isReserve }
            .penalize(HardSoftScore.ofSoft(300))
            .asConstraint("Reserve crew usage")

    private fun consecutiveDutyDays(cf: ConstraintFactory): Constraint {
        // Kotlin can't disambiguate the 4 countDistinct overloads from a lambda;
        // wrap as java.util.function.Function explicitly.
        val dateMapper: java.util.function.Function<FlightAssignment, java.time.LocalDate> =
            java.util.function.Function { fa -> fa.flight!!.getDepartureUTCDate() }
        val daysCollector = ConstraintCollectors.countDistinct(dateMapper)
        return cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null }
            .groupBy(FlightAssignment::employee,
                { fa -> isoWeek(fa.flight!!.departureUTCDateTime!!) },
                daysCollector)
            .filter { _, _, nDays -> nDays > MAX_CONSECUTIVE_DUTY_DAYS }
            .penalize(HardSoftScore.ofSoft(500)) { _, _, nDays -> (nDays - MAX_CONSECUTIVE_DUTY_DAYS).toLong() }
            .asConstraint("Too many duty days per week")
    }

    private fun fairRedEyeDistribution(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && !it.deadhead && it.flight!!.isRedEye() }
            .groupBy(ConstraintCollectors.loadBalance(FlightAssignment::employee))
            .penalize(HardSoftScore.ONE_SOFT) { lb -> (lb.unfairness().toDouble() * 20).toLong() }
            .asConstraint("Fair red-eye distribution")

    private fun fairWeekendDistribution(cf: ConstraintFactory): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { it.employee != null && !it.deadhead && it.flight!!.isWeekendFlight() }
            .groupBy(ConstraintCollectors.loadBalance(FlightAssignment::employee))
            .penalize(HardSoftScore.ONE_SOFT) { lb -> (lb.unfairness().toDouble() * 20).toLong() }
            .asConstraint("Fair weekend distribution")

    // ------------------------------------------------ разброс кредитных часов
    //
    // Правило ВМП (семья balanced_workload) и целевая (метрика
    // credit_hours_spread) читаются дословно (решение владельца
    // 2026-09-03 «входит в разброс»):
    //  • совокупность — ШТАТ: все сотрудники набора, сотрудник без единого
    //    назначения входит с нулём часов (complement по фактам Employee);
    //  • кредитный час — час налёта по назначению (определение общего
    //    раздела ВМП: время от вылета до прилёта рейса, засчитанное
    //    пилоту); перелёт пассажиром (deadhead) не засчитывается;
    //  • мера — √Σ(xᵢ−x̄)² по минутам налёта (unfairness коллектора), в
    //    ЦЕЛЫХ часах: минуты / 60 с усечением — единица «час» реестра;
    //  • целевая берёт разброс целиком («Credit hours spread», ставка
    //    целевой); правило — только превышение порога существенности
    //    («Credit hours spread over threshold», коэффициент правила):
    //    единицы = floor(max(0, часы − порог)), порог — параметр правила
    //    workload_spread_threshold (минуты в окне порогов).
    // Литерал ONE_SOFT — только прогон без пакета; в пакетном оба веса
    // приходят из ВМП.

    /** Кредитные минуты назначения: налёт рейса; пассажиром — ноль. */
    private fun creditMinutesOf(fa: FlightAssignment?): Long =
        if (fa == null || fa.deadhead || fa.flight == null) 0L
        else fa.flight!!.getDurationMinutes().toLong()

    /** Поток «сотрудник × назначение» по всему штату: у сотрудника без назначений пара (e, null). */
    private fun creditSpreadStream(cf: ConstraintFactory) =
        cf.forEach(Employee::class.java)
            .join(FlightAssignment::class.java,
                Joiners.equal({ e: Employee -> e }, { fa: FlightAssignment -> fa.employee }))
            .complement(Employee::class.java)
            .groupBy(ConstraintCollectors.loadBalance<Employee, FlightAssignment?, Employee>(
                { e: Employee, _: FlightAssignment? -> e },
                { _: Employee, fa: FlightAssignment? -> creditMinutesOf(fa) }))

    /** Целые часы разброса: √Σ(xᵢ−x̄)² минут / 60, усечение вниз. */
    internal fun spreadHoursOf(unfairnessMinutes: java.math.BigDecimal): Long =
        unfairnessMinutes.divide(java.math.BigDecimal(60), 9, java.math.RoundingMode.DOWN)
            .setScale(0, java.math.RoundingMode.DOWN).toLong()

    /** Целые часы разброса сверх порога: floor(max(0, часы − порог)). */
    internal fun spreadHoursOverThreshold(unfairnessMinutes: java.math.BigDecimal,
                                         thresholdMin: Int): Long {
        val over = unfairnessMinutes.subtract(java.math.BigDecimal(thresholdMin))
        if (over.signum() <= 0) return 0L
        return spreadHoursOf(over)
    }

    // internal, не private: верификатор теста зовёт напрямую.
    internal fun creditHoursSpread(cf: ConstraintFactory): Constraint =
        creditSpreadStream(cf)
            .penalize(HardSoftScore.ONE_SOFT) { lb -> spreadHoursOf(lb.unfairness()) }
            .asConstraint("Credit hours spread")

    internal fun creditHoursSpreadOverThreshold(cf: ConstraintFactory): Constraint =
        creditSpreadStream(cf)
            .penalize(HardSoftScore.ONE_SOFT) { lb ->
                spreadHoursOverThreshold(lb.unfairness(), spreadThresholdMin) }
            .asConstraint("Credit hours spread over threshold")
}
