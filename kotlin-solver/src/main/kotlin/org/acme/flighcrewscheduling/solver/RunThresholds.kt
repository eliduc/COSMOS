package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import java.time.temporal.ChronoUnit

/**
 * Пороги ЖЁСТКИХ правил, применяемые к текущему прогону (этап В, «г»).
 *
 * До этого места пакет постановки довозил параметры правил машинно, но
 * их не читал никто: провайдер ограничений получал числа литералами, а
 * ярус правил читается один раз на старте процесса. Здесь пороги пакета
 * впервые становятся ДЕЙСТВУЮЩИМИ.
 *
 * Механика. Провайдер ограничений строится заново на каждый прогон и
 * читает пороги из держателя при СОЗДАНИИ. Держатель заполняется ровно
 * на время сборки фабрики решателя — [withThresholds] — и очищается в
 * finally. Ревизия 1 оставляла его заполненным на всю жизнь процесса,
 * и прогон БЕЗ пакета наследовал пороги предыдущего прогона С пакетом;
 * отказ резолвера до apply сохранял пороги прошлого прогона. Оба пути
 * закрыты окном apply→clear.
 *
 * СВЕДЕНИЕ. Несколько требований на одну ручку — применяется
 * СТРОЖАЙШЕЕ (для «не больше» меньшее, для «не меньше» большее): оно не
 * нарушает ни одного требования, но пере-ограничивает остальные, и это
 * ЗАЯВЛЯЕТСЯ числом. У отношения «ровно» строжайшего нет: разные числа
 * — противоречие постановки, отказ.
 *
 * УТОЧНИТЕЛЬ ОБЛАСТИ. У решателя по ДВЕ ручки на стыковку и на отдых —
 * в базе и вне базы (ревизия 3). Отдых различён именами параметров,
 * стыковка — уточнителем. Уточнитель сопоставляется со словарём
 * областей ТОЧНО (после нормализации пробелов и регистра), не по
 * префиксу: «база PLY» — не «база», а «в базе и вне базы» — не «в базе»
 * (ревизия 4; до неё сопоставление шло префиксом и догадывалось).
 *
 * Уточнитель, который ручка НЕ различает (область вне словаря, либо
 * область у ручки без области — продолжительность смены, посадки,
 * месячный налёт), — это СУЖЕНИЕ требования. Применить суженное
 * требование ко всей ручке можно только как УЖЕСТОЧЕНИЕ: оно не нарушит
 * требование нигде и лишь пере-ограничит вне области — это заявляется.
 * Ослабление ко всей ручке применить нельзя — пакет вне области не
 * говорил ничего; такое требование не применяется и названо. Уточнитель,
 * противоречащий области в имени параметра («отдых дома» с уточнителем
 * «вне базы»), — не применяется и назван.
 *
 * КЛЮЧЕВАЯ РУЧКА (ревизия 5): норма суточного резерва. У неё уточнитель
 * — не область, а КЛЮЧ: код базы приписки. Ручка порождается по коду
 * («reserve_crews_min[PLY]»), литерал у всего семейства один — 0,
 * выключено. Норма без кода или с уточнителем, не похожим на код, — не
 * применена и названа. У отношения «ровно» (численность экипажа)
 * уточнитель — контекстное значение, не глобальное: не применено и
 * названо.
 *
 * ЗАМЕЩЕНИЕ ЗАЯВЛЯЕТСЯ ВСЕГДА — и ужесточение, и ослабление литерала
 * профиля. У ручки с литералом-выключателем (посадки, численность,
 * резерв: 0 = правило выключено) замещение — это ВКЛЮЧЕНИЕ правила, и
 * заявляется оно так, а не арифметикой направления.
 */
object RunThresholds {

    /** Что решатель знает о своей ручке: единица, отношение, профили с исполнителем. */
    data class Knob(
        val threshold: String,
        val unit: String,
        val relation: String,
        /** профили ограничений, в которых у порога есть исполнитель. */
        val executors: Set<String>,
        /** область ручки: "home" / "outstation"; null — ручка области не различает. */
        val scope: String? = null,
        /** литерал-выключатель: при этом значении правило в профиле ВЫКЛЮЧЕНО. */
        val offLiteral: Int? = null,
        /** ключевая ручка: уточнитель — код базы, ручка порождается по коду. */
        val keyed: Boolean = false,
        /** отношения, принимаемые ЧАСТИЧНО (с заявлением), помимо своего. */
        val alsoAccepts: Set<String> = emptySet(),
        /** сдвиг шкалы, не предел: ноль законен (шкала данных = счёт
         *  решателя); ненулевой сдвиг механикой не исполняется — отказ. */
        val scaleOffset: Boolean = false,
        /** Внешний исполнитель: порог проверяется и передаётся через applied,
         * но не выдаётся за ограничение Kotlin-провайдера. */
        val externalExecutor: String? = null,
    )

    /**
     * Уточнители ОБЛАСТИ, которые решатель понимает, → область ручки.
     * Список закрыт на нашей стороне и сопоставляется ТОЧНО: у
     * производителя уточнитель — свободный текст (с ревизии 1.4 —
     * канонические «база» | «вне базы»), и догадываться по нему нельзя.
     */
    val SCOPE_QUALIFIERS: Map<String, String> = linkedMapOf(
        "база" to "home", "в базе" to "home", "на базе" to "home",
        "вне базы" to "outstation", "внебазовый" to "outstation",
        "внебазовая" to "outstation", "внебазовые" to "outstation",
    )

    /** Уточнители, ЯВНО называющие обе области scope-ручек разом (ревизия 3). */
    val BOTH_AREAS_QUALIFIERS: Set<String> = setOf("в базе и вне базы")

    private val WHITESPACE = Regex("[\\s\\u00A0]+")

    /** Код базы в уточнителе ключевой ручки: 2–4 латинских буквы или цифры. */
    private val BASE_CODE = Regex("^[A-Z0-9]{2,4}$")

    /** Нормализация уточнителя: пробелы (в том числе неразрывные) и регистр. */
    fun normalizeQualifier(q: String?): String =
        (q ?: "").replace(WHITESPACE, " ").trim().lowercase()

    /**
     * Параметр пакета -> ручка решателя. Ручки с областью ДВЕ (в базе /
     * вне базы): у отдыха они разделены именами параметров, у стыковки —
     * уточнителем. Параметр стыковки БЕЗ уточнителя — требование ко всей
     * сети, то есть к обеим ручкам сразу.
     */
    val KNOBS: Map<String, Knob> = linkedMapOf(
        "max_pairing_days" to Knob("max_pairing_days", "count", "max",
            setOf(TaskModes.PROFILE_DEFAULT), offLiteral = 0,
            externalExecutor = "pairing builder: pairing_days_ok / source-rules.max_pairing_days"),
        "max_duty_duration" to Knob("max_duty_span_min", "duration_minutes", "max",
            setOf(TaskModes.PROFILE_DEFAULT)),
        // K-10 (решение GPT-011 единой версии): порог «мёртвой зоны»
        // стыковки — ручка пакета (семья sit/rest): стыковка не длиннее X,
        // иначе разрыв обязан быть полным отдыхом. Без ручки в пакетном
        // прогоне правило не объявлено (0) — минимум отдыха максимума
        // стыковки не порождает; без пакета — литерал профиля 360.
        "max_sit_duration" to Knob("day_break_threshold_min", "duration_minutes", "max",
            setOf(TaskModes.PROFILE_DEFAULT)),
        "min_rest_home" to Knob("min_rest_home_min", "duration_minutes", "min",
            setOf(TaskModes.PROFILE_DEFAULT), scope = "home"),
        "min_rest_outstation" to Knob("min_rest_outstation_min", "duration_minutes", "min",
            setOf(TaskModes.PROFILE_DEFAULT), scope = "outstation"),
        "max_monthly_block" to Knob("max_monthly_block_min", "duration_minutes", "max",
            setOf(TaskModes.PROFILE_DEFAULT)),
        // Недельный налёт — прежде литерал профиля без ручки (60 ч);
        // ревизия литералов 2026-09-03: ручка пакета, без пакета литерал,
        // в пакетном прогоне без правила — выключено.
        "max_weekly_block" to Knob("max_weekly_block_min", "duration_minutes", "max",
            setOf(TaskModes.PROFILE_DEFAULT)),
        "max_landings_per_duty" to Knob("max_landings_per_duty", "count", "max",
            setOf(TaskModes.PROFILE_DEFAULT),
            offLiteral = DefaultProviderRules.MAX_LANDINGS_PER_DUTY_OFF),
        "crew_size" to Knob("crew_size", "count", "eq",
            setOf(TaskModes.PROFILE_DEFAULT),
            offLiteral = DefaultProviderRules.CREW_SIZE_OFF),
        "reserve_crews" to Knob(DefaultProviderRules.RESERVE_FAMILY, "count", "min",
            setOf(TaskModes.PROFILE_DEFAULT),
            offLiteral = DefaultProviderRules.RESERVE_CREWS_OFF,
            keyed = true, alsoAccepts = setOf("eq")),
        // Перегоны экипажа (этап Г): пределы приходят ТОЛЬКО из пакета,
        // без них возможность выключена (кресла не порождаются).
        "max_deadheads_per_flight" to Knob("max_deadheads_per_flight", "count", "max",
            setOf(TaskModes.PROFILE_DEFAULT),
            offLiteral = DefaultProviderRules.DEADHEADS_OFF),
        "max_deadheads_per_day" to Knob("max_deadheads_per_day", "count", "max",
            setOf(TaskModes.PROFILE_DEFAULT),
            offLiteral = DefaultProviderRules.DEADHEADS_OFF),
        // Шкала времени мира (линия нано, 2026-08-29): «все времена —
        // местное время главной базы» приходит правилом пакета со
        // смещением шкалы данных. Не предел, а константа мира.
        "base_tz_offset_min" to Knob("base_tz_offset_min", "duration_minutes", "eq",
            setOf(TaskModes.PROFILE_DEFAULT), scaleOffset = true),
    )

    /** Ручки стыковки по области; без уточнителя — обе. */
    private val SIT_KNOBS: Map<String, Knob> = linkedMapOf(
        "home" to Knob("min_sit_home_min", "duration_minutes", "min",
            setOf(TaskModes.PROFILE_DEFAULT), scope = "home"),
        "outstation" to Knob("min_sit_outstation_min", "duration_minutes", "min",
            setOf(TaskModes.PROFILE_DEFAULT), scope = "outstation"),
    )

    private const val PARAM_SIT = "min_turnaround"

    private fun byName(name: String): List<Knob>? = when (name) {
        PARAM_SIT -> SIT_KNOBS.values.toList()
        else -> KNOBS[name]?.let { listOf(it) }
    }

    /** Образец ручки по имени параметра (единица/отношение ожиданий); null — имя не ручка порога. */
    fun knobByName(name: String): Knob? = byName(name)?.firstOrNull()

    /** Код базы из ручки семейства: «reserve_crews_min[PLY]» → «PLY»; null — не ключевая. */
    fun keyOf(threshold: String): String? =
        if ('[' in threshold && threshold.endsWith("]")) threshold.substringAfter('[').substringBefore(']') else null

    /**
     * Куда метит параметр. [narrowed] — уточнитель сужает требование, а
     * ручки этой области не различают: применимо только как ужесточение
     * ко всей ручке. [refusal] — применять нельзя, причина названа.
     */
    data class Targeting(
        val knobs: List<Knob>,
        val narrowed: Boolean,
        val refusal: String? = null,
        /** уточнитель ЯВНО покрывает все области scope-ручки («в базе и
         *  вне базы», ревизия 3): применение ко всей ручке — точное, не
         *  пере-ограничение; допускается как ужесточение. */
        val coversAll: Boolean = false,
    )

    /** Все ручки, к которым может метить параметр, с учётом уточнителя; null — имя неизвестно. */
    fun knobsFor(p: RuleParam): Targeting? {
        val named = byName(p.name) ?: return null
        val q = normalizeQualifier(p.qualifier)
        val first = named.first()
        if (first.keyed) {
            // Ключевая ручка: уточнитель — код базы, ровно и только он.
            if (q.isEmpty()) {
                return Targeting(emptyList(), narrowed = false, refusal =
                    "норма " + p.name + " без уточнителя-кода базы: к какой базе её " +
                    "относить, решатель не угадывает; правило НЕ исполняется")
            }
            val code = q.uppercase()
            if (!BASE_CODE.matches(code)) {
                return Targeting(emptyList(), narrowed = false, refusal =
                    "уточнитель «" + p.qualifier + "» у " + p.name + " — не код базы " +
                    "(ожидается код приписки вида «PLY»); правило НЕ исполняется")
            }
            return Targeting(listOf(first.copy(threshold = first.threshold + "[" + code + "]")),
                narrowed = false)
        }
        if (q.isEmpty()) return Targeting(named, narrowed = false)
        if (first.relation == "eq") {
            // «Ровно N» с уточнителем — контекстное значение (спецификация
            // 1.3: численность производится только из записей eq БЕЗ
            // уточнителя), не глобальное: ко всей сети не применяется.
            return Targeting(emptyList(), narrowed = false, refusal =
                "уточнитель «" + p.qualifier + "» у параметра равенства " + p.name +
                ": значение с уточнителем — контекстное, а не глобальное " +
                "(спецификация 1.3: численность — только записи «ровно» без " +
                "уточнителя); правило НЕ исполняется")
        }
        val scope = SCOPE_QUALIFIERS[q]
        if (scope != null) {
            val own = named.filter { it.scope == scope }
            if (own.isNotEmpty()) return Targeting(own, narrowed = false)
            val other = named.firstOrNull { it.scope != null }
            if (other != null) {
                return Targeting(emptyList(), narrowed = false, refusal =
                    "уточнитель «" + p.qualifier + "» противоречит области параметра " +
                    p.name + " (ручка " + other.threshold + " — область «" +
                    scopeWord(other.scope!!) + "»): правило НЕ исполняется")
            }
        }
        // Уточнитель, явно покрывающий ОБЕ области scope-ручки (ревизия 3):
        // применение ко всей ручке точное; ужесточение допускается.
        if (q in BOTH_AREAS_QUALIFIERS && named.any { it.scope != null }) {
            return Targeting(named, narrowed = true, coversAll = true)
        }
        // Область не из словаря либо область у ручки без области — сужение.
        return Targeting(named, narrowed = true)
    }

    private fun scopeWord(scope: String): String =
        if (scope == "home") "база" else "вне базы"

    /** Литералы профиля по умолчанию под именами ручек; у ключевой — литерал семейства. */
    private fun literal(threshold: String): Int? {
        // У транзитной ручки нет литерала Kotlin: 0 означает отсутствие
        // объявленного порога, а не установленную норму построителя.
        KNOBS.values.firstOrNull { it.threshold == threshold && it.externalExecutor != null }
            ?.let { return it.offLiteral }
        val r = DefaultProviderRules.readings()
        return r[threshold] ?: keyOf(threshold)?.let { r[threshold.substringBefore('[')] }
    }

    data class Applied(
        val threshold: String,
        val value: Int,
        val fromParams: List<String>,
        val rules: List<String>,
        /** литерал профиля, который замещён. */
        val literal: Int,
        /** заявления: замещение, сведение, область, включение, приближение — всё, о чём нельзя молчать. */
        val declarations: List<String>,
        /** Коды перечня (ручки-перечни, например fleet_types); у числовых ручек пусто. */
        val codes: List<String> = emptyList(),
        /** Scoped exceptions, never flattened into a globally weaker numeric hard limit. */
        val dutyExtensions: List<DutyExtensionPolicy> = emptyList(),
    )

    data class Ignored(
        val name: String,
        val rules: List<String>,
        val reason: String,
        /** уточнители неприменённых записей — чтобы гейт вычёркивал ровно их, а не все записи имени. */
        val qualifiers: List<String> = emptyList(),
    )

    data class Resolution(
        val applied: Map<String, Applied>,
        val ignored: List<Ignored>,
    ) {
        val isEmpty: Boolean get() = applied.isEmpty()
    }

    @Volatile private var current: Map<String, Applied> = emptyMap()
    @Volatile private var currentPlans: List<LogicPlan> = emptyList()

    /** Пороги текущей сборки; пусто вне окна [withThresholds]. */
    fun current(): Map<String, Applied> = current

    /** Планы порождённых ограничений текущей сборки (ступень 3); пусто вне окна. */
    fun plans(): List<LogicPlan> = currentPlans

    /**
     * Значение ручки либо литерал профиля, если пакет о ней молчит.
     *
     * В ПАКЕТНОМ окне (прогон по пакету постановки) литерал не
     * подставляется: ручка, о которой пакет молчит, ВЫКЛЮЧЕНА (0) —
     * ограничения и критерии берутся исключительно из ВМП (поручение
     * владельца 2026-09-03; ревизия литералов решателя). Каждое
     * ограничение обязано понимать 0 как «правила нет».
     */
    fun valueOr(threshold: String, fallback: Int): Int =
        current[threshold]?.value ?: (if (packagedWindow) 0 else fallback)

    /** Пакетное ли окно сборки: пороги от пакета, литералов профиля нет. */
    fun packaged(): Boolean = packagedWindow

    /** Параметры-перечни семьи флота (единицы «перечень кодов» / «код» словаря CDPM). */
    val FLEET_PARAMS: Set<String> = setOf("fleet_types", "fleet_type")

    /** Имя ручки-перечня допуска на тип в отчёте применённого. */
    const val FLEET_THRESHOLD = "fleet_types"

    /** Перечень типов судов, для которых пакет включил допуск на тип; null — правила в окне нет. */
    fun fleetTypes(): Set<String>? =
        current[FLEET_THRESHOLD]?.codes?.takeIf { it.isNotEmpty() }?.toSet()

    /** Значения семейства ключевых ручек по коду: «reserve_crews_min[PLY]» → PLY → 2. Пусто — выключено. */
    fun keyedValues(family: String): Map<String, Int> {
        val out = LinkedHashMap<String, Int>()
        for ((t, a) in current) {
            if (t.startsWith("$family[")) keyOf(t)?.let { out[it] = a.value }
        }
        return out
    }

    /**
     * Окно применения: пороги видны провайдеру ТОЛЬКО внутри блока.
     * Провайдер создаётся при сборке фабрики — внутри блока; после
     * выхода держатель пуст, и следующий прогон без пакета увидит
     * литералы. Синхронизировано: две одновременные сборки с разными
     * порогами не перемешают ручки.
     */
    @Synchronized
    fun <T> withThresholds(applied: Map<String, Applied>, block: () -> T): T =
        withRunModel(applied, emptyList(), block = block)

    /**
     * Окно сборки ступени 3: пороги И планы порождённых ограничений.
     * Держатели СОХРАНЯЮТСЯ и восстанавливаются: монитор объекта
     * реентрантен, и вложенная сборка (проба формы, второй вход) иначе
     * тихо очищала бы держатель внешнего окна на своём выходе.
     */
    @Volatile private var packagedWindow = false

    @Synchronized
    fun <T> withRunModel(applied: Map<String, Applied>, plans: List<LogicPlan>,
                         packaged: Boolean = false, block: () -> T): T {
        val prevApplied = current
        val prevPlans = currentPlans
        val prevPackaged = packagedWindow
        current = applied
        currentPlans = plans
        packagedWindow = packaged
        try {
            return block()
        } finally {
            current = prevApplied
            currentPlans = prevPlans
            packagedWindow = prevPackaged
        }
    }

    /** Только для тестов: держатель снаружи окна трогать нельзя. */
    internal fun clearForTests() { current = emptyMap(); currentPlans = emptyList() }

    private data class Entry(val p: RuleParam, val knob: Knob, val narrowed: Boolean, val q: String)

    /**
     * Свести параметры пакета к ручкам решателя для ПРОФИЛЯ.
     *
     * Отказы (всё — [TaskModeRefused], с причиной): значение не целое
     * или не положительное; единица или отношение параметра не те, что у
     * ручки (кроме отношений, принимаемых частично); разные числа у
     * отношения «ровно»; порог отдыха не выше порога разрыва дня (иначе
     * правило отдыха исчезает молча — пустой диапазон вместо отказа).
     *
     * Не применённое — не отказ, а перечень [Resolution.ignored] с
     * причиной: имя вне таблицы; нет исполнителя в профиле; уточнитель
     * противоречит области параметра; суженное требование ослабляет
     * ручку, которая области не различает; норма без кода базы;
     * равенство с уточнителем. Одинаковые причины сводятся в одну
     * запись с перечнем правил и уточнителей. Молчаливый пропуск запрещён.
     */
    fun resolve(params: List<RuleParam>, profile: String, packaged: Boolean = false): Resolution {
        val ignoredAcc = LinkedHashMap<Pair<String, String>, Pair<MutableList<String>, MutableList<String>>>()
        fun ignore(p: RuleParam, reason: String) {
            val (rules, quals) = ignoredAcc.getOrPut(p.name to reason) { ArrayList<String>() to ArrayList<String>() }
            if (p.ruleId !in rules) rules.add(p.ruleId)
            val q = p.qualifier?.trim().orEmpty()
            if (q.isNotEmpty() && q !in quals) quals.add(q)
        }
        val byKnob = LinkedHashMap<String, MutableList<Entry>>()
        // Ручка-перечень семьи флота (fleet_types / fleet_type): коды
        // типов судов, для которых действует допуск на тип; исполнители
        // — «Type rating required» и «Type rating currency». До этого
        // параметр падал в ignored как неизвестный, а реестр Python
        // считал правило покрытым (сертификат H0 2026-09-03).
        val fleetCodes = LinkedHashSet<String>()
        val fleetRules = ArrayList<String>()
        val fleetParams = ArrayList<String>()
        for (p in params) {
            if (p.name in FLEET_PARAMS) {
                if (profile != TaskModes.PROFILE_DEFAULT) {
                    ignore(p, "в профиле " + profile + " нет ограничения-исполнителя " +
                        "допуска на тип: правило НЕ исполняется")
                    continue
                }
                val codes = p.codeValues ?: p.rawValue.split(",").map { it.trim() }.filter { it.isNotEmpty() }
                if (codes.isEmpty()) {
                    ignore(p, "перечень типов судов пуст: правило НЕ исполняется")
                    continue
                }
                fleetCodes.addAll(codes)
                if (p.ruleId !in fleetRules) fleetRules.add(p.ruleId)
                if (p.name !in fleetParams) fleetParams.add(p.name)
                continue
            }
            val target = knobsFor(p)
            if (target == null) {
                ignore(p, "имя параметра не известно таблице соответствий решателя: " +
                    "жёсткое правило заказчика НЕ исполняется")
                continue
            }
            if (target.refusal != null) {
                ignore(p, target.refusal)
                continue
            }
            val q = normalizeQualifier(p.qualifier)
            for (knob in target.knobs) {
                if (profile !in knob.executors) {
                    ignore(p, "в профиле " + profile + " нет ограничения-исполнителя для " +
                        knob.threshold + ": правило НЕ исполняется")
                    continue
                }
                if (p.unit != knob.unit) {
                    throw TaskModeRefused(
                        "параметр " + p.name + " правила " + p.ruleId + " несёт единицу " +
                        p.unit + ", а ручка " + knob.threshold + " ждёт " + knob.unit)
                }
                if (p.relation != knob.relation && p.relation !in knob.alsoAccepts) {
                    throw TaskModeRefused(
                        "параметр " + p.name + " правила " + p.ruleId + " объявлен как «" +
                        p.relation + "», а ручка " + knob.threshold + " — это «" +
                        knob.relation + "»: применить правило-минимум как максимум нельзя")
                }
                val v = p.value
                if (knob.scaleOffset) {
                    if (v == null || v != Math.floor(v)) {
                        throw TaskModeRefused(
                            "параметр " + p.name + " правила " + p.ruleId + " несёт значение " +
                            p.rawValue + ": сдвиг шкалы обязан быть целым числом минут")
                    }
                    if (v != 0.0) {
                        throw TaskModeRefused(
                            "параметр " + p.name + " правила " + p.ruleId + " несёт сдвиг шкалы " +
                            p.rawValue + ": решатель считает даты в шкале временных меток " +
                            "данных (сдвиг 0); ненулевой сдвиг механикой не исполняется — " +
                            "расширение явным шагом, не молча")
                    }
                } else if (v == null || v != Math.floor(v) || v <= 0.0 || v > Int.MAX_VALUE) {
                    throw TaskModeRefused(
                        "параметр " + p.name + " правила " + p.ruleId + " несёт значение " +
                        p.rawValue + ": порог обязан быть целым положительным числом")
                }
                if (target.narrowed && !target.coversAll) {
                    // Требование СУЖЕННОЙ области, которую ручка не различает,
                    // не применяется ВОВСЕ (решение обеих сторон 2026-08-25,
                    // канон ревизии 4 «уточнитель точно по словарю»). Прежнее
                    // «влить как ужесточение» превращало правило чужой области
                    // (резервная смена ≤600) в пере-ограничение лётных смен —
                    // на run-0050 это дало −13485 hard законному решению.
                    // Причина одна на параметр (ручки названы вместе), чтобы
                    // стыковка с двумя ручками не давала двух записей.
                    ignore(p,
                        "уточнитель «" + p.qualifier + "» сужает область требования, а " +
                        "ручка " + target.knobs.joinToString("/") { it.threshold } +
                        " этой области не различает (области решателя: " +
                        SCOPE_QUALIFIERS.keys.joinToString(", ") +
                        " — только у стыковки и отдыха): применить число суженной " +
                        "области ко ВСЕЙ ручке — пере-ограничение вне области " +
                        "(ужесточением) либо ослабление правила; правило НЕ исполняется")
                    continue
                }
                if (target.narrowed && target.coversAll) {
                    // «В базе и вне базы» покрывает обе области ручки (ревизия
                    // 3): применение точное, но только как ужесточение —
                    // ослаблять литерал комбинированным уточнителем нельзя.
                    val lit = literal(knob.threshold)
                        ?: throw TaskModeRefused(
                            "у профиля " + profile + " нет литерала для " + knob.threshold)
                    val isMax = knob.relation == "max"
                    val tightens = lit == knob.offLiteral ||
                        (if (isMax) v.toInt() < lit else v.toInt() > lit)
                    if (!tightens) {
                        ignore(p,
                            "уточнитель «" + p.qualifier + "» покрывает обе области, но " +
                            "применить ОСЛАБЛЕНИЕ " + v.toInt() + " против литерала " + lit +
                            " ко всей ручке " + target.knobs.joinToString("/") { it.threshold } +
                            " нельзя; правило НЕ исполняется")
                        continue
                    }
                }
                byKnob.getOrPut(knob.threshold) { ArrayList() }
                    .add(Entry(p, knob, target.narrowed, q))
            }
        }

        val applied = LinkedHashMap<String, Applied>()
        if (fleetCodes.isNotEmpty()) {
            applied[FLEET_THRESHOLD] = Applied(
                threshold = FLEET_THRESHOLD, value = fleetCodes.size,
                fromParams = fleetParams.toList(), rules = fleetRules.toList(),
                literal = 0,
                declarations = listOf(
                    "ВКЛЮЧЕНИЕ: допуск на тип судна по перечню ВМП (" +
                        fleetCodes.joinToString(", ") + "); исполнители «Type rating " +
                        "required» (допуск из листа «Допуски») и «Type rating currency» " +
                        "(действие срока) судят рейсы только этих типов",
                    "ПРОЧТЕНИЕ: рейс типа вне перечня допуском не судится; в пакетном " +
                        "прогоне без правила семьи флота допуск на тип не судится вовсе " +
                        "(правило не в ВМП)"),
                codes = fleetCodes.toList())
        }
        for ((threshold, group) in byKnob) {
            val knob = group.first().knob
            val lit = literal(threshold)
                ?: throw TaskModeRefused(
                    "у профиля " + profile + " нет литерала для " + threshold)
            val decl = ArrayList<String>()
            val isMax = knob.relation == "max"
            val isEq = knob.relation == "eq"
            val distinct = group.map { it.p.value!!.toInt() }.toSortedSet()
            if (isEq && distinct.size > 1) {
                throw TaskModeRefused(
                    "параметр " + group.first().p.name + " объявлен как «ровно» разными " +
                    "числами (" + distinct.joinToString(", ") + ") правилами " +
                    group.map { it.p.ruleId }.distinct().sorted().joinToString(", ") +
                    ": у равенства строжайшего нет — это противоречие постановки, " +
                    "не сведение")
            }
            val strictest = if (isMax) distinct.first() else distinct.last()
            if (distinct.size > 1) {
                val others = distinct.filter { it != strictest }
                decl.add("СВЕДЕНО: одна ручка " + threshold + " против " +
                    group.size + " требований пакета (" +
                    distinct.joinToString(", ") + "). Применено строжайшее " +
                    strictest + ": решения, законные по пакету, могут отвергаться; " +
                    "пере-ограничение остальных на " +
                    others.joinToString(", ") { Math.abs(it - strictest).toString() } +
                    " " + knob.unit)
            }
            // ОБЛАСТЬ — одно заявление на уточнитель, с перечнем правил:
            // повтор источников виден именами, а не дословным дублем.
            // У ключевой ручки уточнитель — код, не область: заявление КЛЮЧ.
            // Чужие (narrowed без покрытия) области сюда не доходят — они
            // отвергнуты при отборе; здесь различённые области и уточнители,
            // покрывающие обе области разом (ревизия 3).
            val scoped = group.filter { it.q.isNotEmpty() && !it.knob.keyed }
                .groupBy { it.q to it.narrowed }
            for ((key, entries) in scoped) {
                val narrowed = key.second
                val first = entries.first().p
                val rules = entries.map { it.p.ruleId }.distinct().sorted().joinToString(", ")
                decl.add(
                    if (narrowed)
                        "ОБЛАСТЬ: требование " + first.name + " с уточнителем «" +
                        first.qualifier + "» (правила " + rules + ") применено ко ВСЕЙ " +
                        "ручке " + threshold + " как ужесточение — уточнитель покрывает " +
                        "обе области ручки разом"
                    else
                        "ОБЛАСТЬ: требование " + first.name + " с уточнителем «" +
                        first.qualifier + "» (правила " + rules + ") применено к ручке " +
                        threshold + " — область различена по базе приписки и аэропорту " +
                        "прилёта")
            }
            keyOf(threshold)?.let { code ->
                decl.add("КЛЮЧ: уточнитель «" + code + "» прочитан как КОД БАЗЫ приписки; " +
                    "ручка " + threshold + " действует на сотрудников с базой " + code +
                    " (правила " + group.map { it.p.ruleId }.distinct().sorted().joinToString(", ") + ")")
            }
            // Отношение, принятое частично: «ровно» исполнено как нижняя граница.
            val partial = group.filter { it.p.relation != knob.relation && it.p.relation in knob.alsoAccepts }
            if (partial.isNotEmpty()) {
                decl.add("РАВЕНСТВО: правила " +
                    partial.map { it.p.ruleId }.distinct().sorted().joinToString(", ") +
                    " требуют «ровно " + partial.map { it.p.value!!.toInt() }.distinct().joinToString(", ") +
                    "»; исполнено как «не менее» — верхняя граница НЕ исполняется: " +
                    "резервного наряда как сущности у решателя нет, лишние свободные люди " +
                    "резервом не считаются")
            }
            if (knob.externalExecutor != null) {
                decl.add("ПЕРЕДАЧА ПОРОГА: " + threshold + " = " + strictest +
                    "; исполнитель — " + knob.externalExecutor +
                    "; это не ограничение Kotlin score. Построение и сертификат " +
                    "подтверждают исполнение отдельно; отсутствие нормы обозначено 0.")
            } else if (lit == knob.offLiteral) {
                decl.add("ВКЛЮЧЕНИЕ: правило " + threshold + " в профиле ВЫКЛЮЧЕНО " +
                    "(литерал " + lit + " = выключено); пакет включает его с порогом " +
                    strictest + " — новое жёсткое правило, УЖЕСТОЧЕНИЕ")
            } else {
                val direction = when {
                    strictest == lit -> "совпадает с литералом"
                    (isMax && strictest < lit) || (!isMax && strictest > lit) -> "УЖЕСТОЧЕНИЕ"
                    else -> "ОСЛАБЛЕНИЕ"
                }
                decl.add("ЗАМЕЩЕНИЕ: " + threshold + " литерал профиля " + lit +
                    " → пакет " + strictest + " (" + direction + ")")
            }
            decl.addAll(readingsDeclarations(threshold))
            applied[threshold] = Applied(
                threshold = threshold, value = strictest,
                fromParams = group.map { it.p.name }.distinct().sorted(),
                rules = group.map { it.p.ruleId }.distinct().sorted(),
                literal = lit, declarations = decl)
        }

        // Связка порогов: оба отдыха обязаны быть выше порога разрыва дня,
        // иначе диапазон «мёртвой зоны» пуст и правило отдыха исчезает
        // молча. K-20: в пакетном окне непрошенный литерал профиля (360)
        // порогом не является — гейт судит только ОБЪЯВЛЕННЫЙ пакетом
        // порог (max_sit_duration); без него правило мёртвой зоны в
        // пакетном прогоне не действует, и минимум отдыха ниже 6 ч законен.
        val dayBreak: Int? = if (packaged) applied["day_break_threshold_min"]?.value
            else DefaultProviderRules.DAY_BREAK_THRESHOLD_MIN
        for (key in listOf("min_rest_home_min", "min_rest_outstation_min")) {
            applied[key]?.let { a ->
                if (dayBreak != null && a.value <= dayBreak) {
                    throw TaskModeRefused(
                        "порог отдыха " + key + " = " + a.value + " не выше порога " +
                        "разрыва дня " + dayBreak +
                        ": правило «Rest between duty periods» перестало бы " +
                        "действовать молча")
                }
                (a.declarations as ArrayList<String>).add(
                    "ПРИБЛИЖЕНИЕ: порог отдыха одновременно режет назначения на " +
                    "смены; ужесточение отдыха укрупняет смены и усиливает правила " +
                    "о продолжительности смены и о посадках за смену")
            }
        }
        applied["max_duty_span_min"]?.let { a ->
            (a.declarations as ArrayList<String>).add(
                "ПРИБЛИЖЕНИЕ: продолжительность смены считается от первого " +
                "вылета до последнего прилёта; время явки до вылета не учтено")
        }
        val ignored = ignoredAcc.map { (key, acc) ->
            Ignored(key.first, acc.first.sorted(), key.second, acc.second.sorted())
        }
        return Resolution(applied, ignored)
    }

    /** Мягкий порог семьи duty_extension_penalty (минуты); 0/нет = семья не активна. */
    const val DUTY_EXTENSION_THRESHOLD = "duty_extension_threshold_min"

    /**
     * Семья duty_extension_penalty (шаблон «мягкое превышение порога
     * длительности смены до жёсткого потолка», конвейер 2026-08-31):
     * жёсткий предел смены поднимается до потолка семьи, а прежний
     * базовый предел становится МЯГКИМ порогом — смена свыше порога
     * штрафуется ограничением «Duty extension over threshold» весом
     * пакета. Применяется только при обоих слагаемых: есть потолок
     * семьи И есть базовый предел (без базового предела мягкий порог
     * не существует — пороги не трогаются, ограничение спит). Потолок
     * не строже базового предела — тоже не применяется (продление,
     * которое ничего не продлевает, ослабляло бы жёсткую грань молча).
     */
    fun applyDutyExtension(res: Resolution, capMin: Int?,
                           capRules: List<String>): Resolution {
        if (capMin == null) return res
        val base = res.applied["max_duty_span_min"] ?: return res
        if (capMin <= base.value) return res
        val applied = LinkedHashMap(res.applied)
        applied["max_duty_span_min"] = base.copy(
            value = capMin,
            declarations = base.declarations + (
                "СЕМЬЯ duty_extension_penalty (" +
                capRules.joinToString(", ") + "): жёсткий предел смены " +
                "поднят до потолка продления " + capMin + " мин; прежний " +
                "предел " + base.value + " мин — мягкий порог, превышение " +
                "штрафуется весом пакета"))
        applied[DUTY_EXTENSION_THRESHOLD] = Applied(
            threshold = DUTY_EXTENSION_THRESHOLD,
            value = base.value,
            fromParams = base.fromParams,
            rules = (base.rules + capRules).distinct(),
            literal = 0,
            declarations = listOf(
                "мягкий порог семьи duty_extension_penalty: базовый " +
                "предел смены " + base.value + " мин (потолок " +
                capMin + " мин — жёсткая грань)"))
        return Resolution(applied, res.ignored)
    }

    /** Порог существенности разброса кредитных часов семьи balanced_workload (минуты); нет = семьи нет. */
    const val WORKLOAD_SPREAD_THRESHOLD = "workload_spread_threshold_min"

    /**
     * Семья balanced_workload (правило ВМП «штраф за каждый кредитный
     * час разброса сверх порога существенности», решение владельца
     * 2026-09-03): порог правила кладётся в окно порогов минутами, и
     * ограничение «Credit hours spread over threshold» штрафует только
     * превышение. Правило семьи без порога — порог 0: штрафуется весь
     * разброс, и об этом сказано словами.
     */
    fun applyWorkloadSpread(res: Resolution, thresholdMin: Int?,
                            rules: List<String>): Resolution {
        if (thresholdMin == null) return res
        val applied = LinkedHashMap(res.applied)
        applied[WORKLOAD_SPREAD_THRESHOLD] = Applied(
            threshold = WORKLOAD_SPREAD_THRESHOLD,
            value = thresholdMin,
            fromParams = listOf("workload_spread_threshold"),
            rules = rules,
            literal = 0,
            declarations = listOf(
                "СЕМЬЯ balanced_workload (" + rules.joinToString(", ") +
                "): порог существенности разброса кредитных часов " +
                thresholdMin + " мин (" + (thresholdMin / 60.0) + " ч); " +
                (if (thresholdMin == 0) "порога у правила нет — штрафуется весь разброс; "
                 else "штрафуется только превышение; ") +
                "мера — √Σ(xᵢ−x̄)² по минутам налёта ВСЕГО штата " +
                "(сотрудник без назначений — 0), в целых часах; кредитный " +
                "час = час налёта по назначению, перелёт пассажиром не засчитывается"))
        return Resolution(applied, res.ignored)
    }

    /** Preserve the base hard reading; the provider selects an exception for each actual duty. */
    fun applyDutyExtensions(res: Resolution, policies: List<DutyExtensionPolicy>): Resolution {
        if (policies.isEmpty()) return res
        val base = res.applied["max_duty_span_min"] ?: return res
        for (policy in policies) {
            if (policy.declaredThresholdMin != null && policy.declaredThresholdMin != base.value)
                throw TaskModeRefused("правило ${policy.ruleId}: long_duty_threshold=" +
                    "${policy.declaredThresholdMin} не совпадает с базовым пределом " +
                    "max_duty_duration=${base.value}")
        }
        val active = policies.filter { it.capMin > base.value }
            .map { it.copy(bases = it.bases?.toList()) }
        if (active.isEmpty()) return res
        val applied = LinkedHashMap(res.applied)
        applied["max_duty_span_min"] = base.copy(dutyExtensions = active,
            declarations = base.declarations + "предел продления выбирается адресно для каждой смены; базовый предел сохранён")
        applied[DUTY_EXTENSION_THRESHOLD] = Applied(DUTY_EXTENSION_THRESHOLD,
            base.value, base.fromParams, (base.rules + active.map { it.ruleId }).distinct(), 0,
            listOf("мягкий порог семьи duty_extension_penalty: базовый предел ${base.value} мин"))
        return Resolution(applied, res.ignored)
    }

    /** Пороги пакета целиком; legacy manually constructed packages retain their unscoped API. */
    fun resolveFor(pkg: LoadedTaskPackage, profile: String): Resolution {
        return resolveNative(pkg, profile)
    }

    /** Content-only resolution for native measurements; it grants no task mode. */
    fun resolveNative(pkg: NativeModel, profile: String): Resolution {
        val base = resolve(pkg.hardParams, profile, packaged = true)
        // An inert low-level extension helper is not permission to execute a
        // package whose declared policy would silently disappear. Do not borrow
        // a provider literal or the SOFT threshold to invent the missing base.
        if ((pkg.dutyExtensionPolicies.isNotEmpty() || pkg.dutyExtensionCapMin != null) &&
                "max_duty_span_min" !in base.applied) {
            val rules = (pkg.dutyExtensionPolicies.map { it.ruleId } + pkg.dutyExtensionRules).distinct()
            throw TaskModeRefused("правила продления " + rules.joinToString(", ") +
                ": нет применённого базового предела max_duty_duration (max_duty_span_min); " +
                "исполнение duty_extension_penalty без базы запрещено")
        }
        return applyWorkloadSpread(
            if (pkg.dutyExtensionPolicies.isNotEmpty())
                applyDutyExtensions(base, pkg.dutyExtensionPolicies)
            else applyDutyExtension(base,
                pkg.dutyExtensionCapMin, pkg.dutyExtensionRules),
            pkg.workloadSpreadThresholdMin, pkg.workloadSpreadRules)
    }

    /** Прочтения и приближения исполнителей строк 7 и 8 — всё, что свод не сказал, а решатель додумал. */
    private fun readingsDeclarations(threshold: String): List<String> = when {
        threshold == "base_tz_offset_min" -> listOf(
            "ПРОЧТЕНИЕ: сдвиг шкалы — константа мира, не предел: все временные метки " +
                "данных объявлены пакетом в местном времени главной базы; счёт календарных " +
                "дат решателя ведётся по этой же шкале без преобразования (сдвиг 0)")
        threshold == "crew_size" -> listOf(
            "ПРОЧТЕНИЕ: «полётная смена» численности прочитана как рейс-участок набора " +
                "(прочтение конвертера); «Crew size» считает РАЗЛИЧНЫХ сотрудников на местах " +
                "рейса — пустое место уменьшает число различных, один человек на двух местах " +
                "считается одним; роли мест судит «Required skill», состав ролей задаёт " +
                "конвертер из книги данных; число мест на рейсе (ровно N) проверяется " +
                "фактами до старта, а не счётом",
            "ПЕРЕСЕЧЕНИЕ: пустое место считают «Unassigned slot» (" +
                DefaultProviderRules.HARD_UNASSIGNED_SLOT + ") и «Crew size» (" +
                DefaultProviderRules.HARD_CREW_SIZE_PER_PERSON + "); один человек на двух " +
                "местах рейса — «Flight conflict» и «Crew size»; на наборах конвертера " +
                "«Crew size» — вторая линия")
        keyOf(threshold) != null -> listOf(
            "ПРОЧТЕНИЕ: экипаж в резерве — по одному СВОБОДНОМУ сотруднику базы на каждую " +
                "роль состава мест набора; экипажей = наименьшее по ролям; свободен в сутки — " +
                "ни одно назначение не касается суток (даты вылета и прилёта, UTC) и сутки не " +
                "в unavailableDays; сутки — календарные даты объявленного window_horizon " +
                "[start_inclusive, end_exclusive), без расширения на прилёт после границы; " +
                "без объявленного окна — календарные даты от первого до последнего ВЫЛЕТА " +
                "набора; сотрудник без роли состава не считается",
            "ПРИБЛИЖЕНИЕ: сутки по UTC — местного времени базы в наборе нет; отдых после " +
                "смены, допуски по типу и датированные допуски, порядок вызова не " +
                "учитываются; признак isReserve и «Reserve crew usage» — другое понятие " +
                "(пул резервистов) и не участвуют; наряда на резерв набор не несёт — " +
                "исполняется НЕОБХОДИМОЕ условие правила",
            "ПЕРЕСЕЧЕНИЕ: число свободных людей зависит от литералов стыковки и отдыха, о " +
                "которых пакет может молчать; единица нехватки (" +
                DefaultProviderRules.HARD_RESERVE_PER_CREW_DAY + " hard за экипаже-сутки) " +
                "ниже цены незанятого места (" + DefaultProviderRules.HARD_UNASSIGNED_SLOT +
                "): программа полётов выше резерва, нехватка отчитывается числом экипажей")
        else -> emptyList()
    }

    /** Литералы-выключатели под именами ручек: показание, равное ему, значит «правило выключено». */
    fun offLiterals(applied: Map<String, Applied> = emptyMap()): Map<String, Int> {
        val out = LinkedHashMap<String, Int>()
        for (k in KNOBS.values) if (k.offLiteral != null) out[k.threshold] = k.offLiteral
        for ((t, _) in applied) {
            val code = keyOf(t) ?: continue
            val family = t.substringBefore('[')
            KNOBS.values.firstOrNull { it.keyed && it.threshold == family }?.offLiteral?.let { out[t] = it }
            if (code.isEmpty()) continue
        }
        return out
    }

    /**
     * Проверка ФАКТОВ набора до секунды счёта — для правил, чьё нарушение
     * ни один ход не исправит (дефект набора, не расписания) либо чьё
     * молчание скрыло бы правило. Отказы — [TaskModeRefused] с именами.
     * Возвращает покрытие для отчёта (что именно рассмотрено).
     */
    fun checkFacts(problem: FlightCrewSchedule, applied: Map<String, Applied>): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>()
        problem.employees.forEach { DutyExtensionPolicies.validate(it) }
        applied["crew_size"]?.let { a ->
            val n = a.value
            val seatsByLeg = problem.flightAssignments.filter { !it.deadhead }
                .groupBy { it.flight?.flightLegId ?: "" }
            for (f in problem.flights) {
                val seats = seatsByLeg[f.flightLegId]?.size ?: 0
                if (seats != n) {
                    throw TaskModeRefused(
                        "рейс " + f.flightNumber + " (" + f.flightLegId + ") несёт " + seats +
                        " мест, а пакет требует численность " + n + ": состав мест — " +
                        "дефект набора, не расписания; ни один ход решателя его не исправит")
                }
            }
            out["crewSize"] = linkedMapOf(
                "flightsChecked" to problem.flights.size,
                "seatsPerFlight" to n,
                "compositionRoles" to problem.flightAssignments.filter { !it.deadhead }
                    .map { it.requiredSkill }.distinct().sorted())
        }
        val norms = applied.filterKeys { keyOf(it) != null && it.startsWith(DefaultProviderRules.RESERVE_FAMILY + "[") }
        if (norms.isNotEmpty()) {
            if (problem.flights.isEmpty()) {
                throw TaskModeRefused(
                    "норма резерва применена, а рейсов в наборе нет: горизонт суток " +
                    "не определён, правило молчало бы нулём")
            }
            val codes = norms.keys.map { keyOf(it)!! }
            val airportCodes = problem.airports.map { it.code.uppercase() }.toSet()
            val baseCodes = problem.employees.mapNotNull { it.homeAirport?.code?.uppercase() }.toSet()
            val roles = problem.flightAssignments.map { it.requiredSkill }.toSet()
            for (c in codes) {
                if (c !in airportCodes && c !in baseCodes) {
                    throw TaskModeRefused(
                        "норма резерва названа для базы «" + c + "», а в наборе нет ни " +
                        "аэропорта, ни сотрудника с такой базой (базы набора: " +
                        baseCodes.sorted().joinToString(", ") + "): опечатка это или база " +
                        "без людей — решатель не угадывает")
                }
            }
            for (e in problem.employees) {
                val b = e.homeAirport?.code?.uppercase() ?: continue
                if (b !in codes) continue
                val own = e.skills.filter { it in roles }
                if (own.size > 1) {
                    throw TaskModeRefused(
                        "сотрудник " + e.id + " базы " + b + " держит роли состава " +
                        own.joinToString(", ") + ": в скольких экипажах резерва он стоит, " +
                        "свод не говорит")
                }
            }
            // K-21: показания горизонта — тот же единый горизонт, что у
            // провайдера и fit (объявленное окно, иначе сутки по вылетам).
            val horizon = LogicForms.horizonOf(problem)!!
            val lo = horizon.lo
            val hi = horizon.hi
            out["reserve"] = linkedMapOf(
                "namedBases" to codes,
                "horizonSource" to (problem.windowHorizon?.let { it.source ?: "declared_window_horizon" }
                    ?: "flight_program_departure_calendar_span"),
                "basesInData" to baseCodes.sorted(),
                "namedBasesWithoutEmployees" to codes.filter { it !in baseCodes },
                "horizonFrom" to lo.toString(),
                "horizonTo" to hi.toString(),
                "horizonDays" to (ChronoUnit.DAYS.between(lo, hi) + 1),
                "compositionRoles" to roles.sorted(),
                "employeesConsidered" to problem.employees.count {
                    (it.homeAirport?.code?.uppercase() ?: "") in codes })
        }
        // Свежесть допуска судится независимо от резерва — только там,
        // где набор несёт датированные окна. Их число в отчёте: иначе
        // «нарушений нет» неотличимо от «сроков не передали».
        out["qualifications"] = linkedMapOf(
            "employeesWithWindows" to problem.employees.count { it.qualificationWindows.isNotEmpty() },
            "employeesTotal" to problem.employees.size,
            "note" to ("правило свежести допуска судит назначение только при наличии " +
                "датированного окна у сотрудника; отсутствие окон значит «срок не " +
                "передан», а не «допуск действует»"))
        return out
    }

    /**
     * Снимок для отчёта применённого: числа и заявления порознь.
     *
     * [readings] — показания СОЗДАННОГО провайдера (если есть): по ним
     * перечисляются литералы В СИЛЕ — ручки, о которых пакет молчит и
     * которые считали литералом профиля. Читатель сертификата обязан
     * видеть и их: число, по которому судили, не бывает невидимым.
     */
    fun snapshot(r: Resolution, readings: Map<String, Int>? = null): Map<String, Any?> {
        // Соответствие для гейта: имя -> ручка (или СПИСОК ручек, если
        // требование без уточнителя метит в обе); «имя|уточнитель» -> ручка
        // для уточнителей из словаря; «имя|*» -> ручка для уточнителя,
        // которого ручка не различает (применим ко всей ручке только как
        // ужесточение — ослабление лежит в ignored); у ключевых ручек —
        // «имя|код» -> порождённая ручка по каждой применённой базе. Ни
        // «ровно», ни ключевые подстановочного ключа не имеют. Гейт берёт
        // это отсюда, а не из копии.
        val qualified = LinkedHashMap<String, Any>()
        for ((q, scope) in SCOPE_QUALIFIERS) {
            qualified[PARAM_SIT + "|" + q] = SIT_KNOBS.getValue(scope).threshold
            for ((name, knob) in KNOBS) if (knob.scope == scope) qualified[name + "|" + q] = knob.threshold
        }
        qualified["$PARAM_SIT|*"] = SIT_KNOBS.values.map { it.threshold }
        for ((name, knob) in KNOBS) {
            if (knob.keyed || knob.relation == "eq") continue
            qualified["$name|*"] = knob.threshold
        }
        for (t in r.applied.keys) {
            val code = keyOf(t) ?: continue
            val family = t.substringBefore('[')
            val name = KNOBS.entries.firstOrNull { it.value.keyed && it.value.threshold == family }?.key ?: continue
            qualified["$name|$code"] = t
        }
        val off = offLiterals(r.applied)
        val inForce = readings?.filterKeys { it !in r.applied }?.map { (k, v) ->
            mapOf("threshold" to k, "value" to v,
                  "note" to when {
                      off[k] == v -> "правило выключено (литерал " + v +
                          " = выключено); пакет о нём молчит"
                      v == 0 -> "правило ВЫКЛЮЧЕНО: пакет о нём молчит — " +
                          "пакетный прогон правил вне ВМП не исполняет " +
                          "(ревизия литералов 2026-09-03)"
                      else -> "литерал профиля; пакет о нём молчит"
                  })
        }
        return mapOf(
            "applied" to r.applied.values.map { a ->
                mapOf("threshold" to a.threshold, "value" to a.value,
                      // Transit values are not observations of the Kotlin provider.
                      "executor" to if (KNOBS.values.any {
                          it.threshold == a.threshold && it.externalExecutor != null
                      }) "pairing-builder" else "kotlin-provider",
                      "literal" to a.literal, "fromParams" to a.fromParams,
                      "rules" to a.rules, "declarations" to a.declarations,
                      // ручки-перечни (fleet_types): коды поимённо
                      "codes" to a.codes,
                      "dutyExtensions" to a.dutyExtensions.map { it.snapshot() })
            },
            "ignored" to r.ignored.map { i ->
                mapOf("param" to i.name, "rules" to i.rules, "reason" to i.reason,
                      "qualifiers" to i.qualifiers)
            },
            "paramToThreshold" to (KNOBS.mapValues { it.value.threshold } +
                FLEET_PARAMS.associateWith { FLEET_THRESHOLD } +
                mapOf(PARAM_SIT to SIT_KNOBS.values.map { it.threshold })),
            "qualifiedParamToThreshold" to qualified,
            "offLiterals" to off,
            "literalsInForce" to inForce,
            // Веса жёстких ограничений профиля: число, влияющее на счёт,
            // видно в отчёте — все, а не только новые.
            "hardWeights" to DefaultProviderRules.hardWeights(),
        )
    }
}
