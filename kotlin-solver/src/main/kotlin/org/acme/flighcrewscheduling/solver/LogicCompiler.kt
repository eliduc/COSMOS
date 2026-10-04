package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.impl.score.stream.common.AbstractConstraintStreamScoreDirectorFactory
import ai.timefold.solver.core.impl.solver.DefaultSolverFactory
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import java.security.MessageDigest
import java.time.temporal.ChronoUnit

/**
 * Ступень 3 (шаг 9в): что решатель делает с блоком `logic` правила.
 *
 * Блок logic судится ДО первой секунды счёта и раскладывается ровно в одну
 * из пяти корзин — ни одно правило с logic не остаётся неназванным:
 *
 *  - ПОРОЖДЕНО — ограничение порождено по форме первой волны (шаг 1+);
 *  - ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ — требование уже считает ограничение профиля
 *    (реестр отнесения [LogicAttribution]); исполнитель назван, и на
 *    этом наборе он рассматривает больше нуля предметов;
 *  - НЕ ИСПОЛНЯЕТСЯ — `post-check` (по назначению либо после отказа);
 *    причина — словами решателя;
 *  - ОТВЕРГНУТО — `required` и любой отказ гейтов: прогон не стартует;
 *  - ВИД ПРАВИЛА — правило вида DEF/DATA/CASE/OUT/SOFT: исполнителем
 *    ступени 3 служат только жёсткие правила (как hardParams).
 *
 * Чего компилятор не делает никогда (глава 7): не ослабляет требование ради
 * выразимости, не подставляет умолчание вместо числа, не применяет к части
 * сказанное про целое — и не порождает второй исполнитель тому, что уже
 * считает существующий (двойной счёт одного нарушения).
 *
 * Два уровня: [compile] — функция ТОЛЬКО пакета (package-plan); [fit] —
 * функция (план, набор): предметов рассмотрено, пустые универсумы. Второе
 * считается на каждом входе со своим набором (`/single` и `/score`
 * получают разные наборы).
 */
object LogicDictionaries {
    val SUBJECTS: Set<String> = setOf(
        "assignment", "flight", "duty", "reserve_duty", "rest", "sit", "employee",
        "base", "day", "month", "rolling_window", "day_off", "qualification")
    /** предикат -> обязательные аргументы (зеркало cdpm.model.LOGIC_PREDICATES). */
    val PREDICATES: Map<String, List<String>> = linkedMapOf(
        "starts_in" to listOf("from_param", "to_param"),
        "longer_than" to listOf("param"),
        "shorter_than" to listOf("param"),
        "at_base" to emptyList(),
        "at_outstation" to emptyList(),
        "consecutive" to listOf("unit", "param"),
        "in_window" to listOf("window"),
        "follows" to listOf("subject"),
        "has_qualification_for" to listOf("type_value"),
        "overlaps_in_time" to emptyList(),
        "marked_unavailable" to emptyList(),
        "in_list" to listOf("allowed"),
    )
    /** требование -> обязательные аргументы (зеркало cdpm.model.LOGIC_DEMANDS). */
    val DEMANDS: Map<String, List<String>> = linkedMapOf(
        "count_within" to listOf("relation", "param"),
        "duration_within" to listOf("relation", "param"),
        "exists_person" to listOf("subject"),
        "exists_universe" to listOf("subject", "universe"),
        "not_exists" to listOf("subject"),
        "combination_forbidden" to listOf("subject"),
    )
    val UNITS: Set<String> = setOf("day", "duty")
    val ENFORCE: Set<String> = setOf("required", "post-check")
    /** Отношения ТРЕБОВАНИЯ: `window` — отношение параметра-окна, у требования его нет. */
    val DEMAND_RELATIONS: Set<String> = setOf("max", "min", "eq")
    val UNIVERSES: Set<String> = setOf(
        "employee×month", "employee×day", "base×day", "employee×rolling_window")
    val WINDOW_KINDS: Set<String> = setOf("month", "rolling")
    val MEASURES: Set<String> = setOf("span", "block")

    /** Идентификатор ограничения, который принимает Timefold (AbstractConstraintBuilder.sanitize). */
    val CONSTRAINT_ID: Regex = Regex("^[a-zA-Z0-9]+[a-zA-Z0-9 _.'()-]*$")

    private val DIGIT = Regex("\\d")
    fun hasDigit(s: String): Boolean = DIGIT.containsMatchIn(s)
}

/** Запись отчёта об одном правиле с logic. */
data class LogicEntry(
    val ruleId: String,
    val state: String,
    val enforce: String,
    val subject: String,
    val demand: String,
    val relation: String?,
    /** порождённое имя либо имена существующих исполнителей. */
    val constraintIds: List<String>,
    /** ручки, через которые исполнитель читает число правила. */
    val viaThresholds: List<String>,
    val reason: String?,
    val declarations: List<String>,
    val shapeSha: String,
    /** fit: сколько предметов исполнитель рассматривает на наборе и что это за предметы. */
    val considered: Int? = null,
    val consideredMeasure: String? = null,
    /** ПОРОЖДЕНО: план порождённого ограничения (одна форма — один исполнитель). */
    val plan: LogicPlan? = null,
) {
    fun snapshot(): Map<String, Any?> = linkedMapOf(
        "rule" to ruleId,
        "state" to state,
        "enforce" to enforce,
        "subject" to subject,
        "demand" to demand,
        "relation" to relation,
        "constraints" to constraintIds,
        "viaThresholds" to viaThresholds,
        "reason" to reason,
        "declarations" to declarations,
        "shapeSha" to shapeSha,
        "considered" to considered,
        "consideredMeasure" to consideredMeasure,
        "plan" to plan?.snapshot(),
    )
}

data class LogicReport(
    val blocksSeen: Int,
    val generated: List<LogicEntry>,
    val executedElsewhere: List<LogicEntry>,
    val ignored: List<LogicEntry>,
    val refused: List<LogicEntry>,
    val notHard: List<LogicEntry>,
    /** имена, которые ЗАРЕГИСТРИРОВАЛА построенная фабрика (от библиотеки, не от намерения). */
    val registered: List<String>? = null,
    /** набор, на котором считался fit, — чтобы числа не приписали чужому прогону. */
    val fitOn: String? = null,
) {
    val isEmpty: Boolean get() = blocksSeen == 0

    /** Планы порождённых ограничений — то, что провайдер читает в окне сборки. */
    val plans: List<LogicPlan> get() = generated.mapNotNull { it.plan }.distinct()

    fun snapshot(): Map<String, Any?> = linkedMapOf(
        "blocksSeen" to blocksSeen,
        "plans" to plans.map { it.snapshot() },
        "generated" to generated.map { it.snapshot() },
        "executedElsewhere" to executedElsewhere.map { it.snapshot() },
        "ignored" to ignored.map { it.snapshot() },
        "refused" to refused.map { it.snapshot() },
        "notHard" to notHard.map { it.snapshot() },
        "registered" to registered,
        "fitOn" to fitOn,
        "states" to linkedMapOf(
            "generated" to LogicCompiler.ST_GENERATED,
            "executedElsewhere" to LogicCompiler.ST_ELSEWHERE,
            "ignored" to LogicCompiler.ST_IGNORED,
            "refused" to LogicCompiler.ST_REFUSED,
            "notHard" to LogicCompiler.ST_NOT_HARD),
    )
}

/**
 * Реестр отнесения: какие требования УЖЕ считает ограничение профиля.
 *
 * Строится против фактического перечня имён, которые регистрирует
 * провайдер; тест [LogicAttributionTest] краснеет, когда в профиле
 * появляется имя, не описанное ни здесь, ни в [NOT_ATTRIBUTABLE]: новое
 * ограничение обязано быть отнесено человеком ДО того, как компилятор
 * впервые увидит блок logic о том же требовании.
 */
object LogicAttribution {

    data class Entry(
        /** имена ограничений-исполнителей (у допуска их два: наличие и срок). */
        val constraintIds: List<String>,
        val subjects: Set<String>,
        val demandKinds: Set<String>,
        /** отношения требования; пусто — отношение у вида отсутствует. */
        val relations: Set<String>,
        /** имя параметра требования (demand.param); пусто — число исполнителю не нужно. */
        val paramNames: Set<String>,
        /** предикаты, которые обязаны стоять в условиях. */
        val predicates: Set<String>,
        /** ручки, через которые исполнитель читает число; пусто — всегда включён. */
        val thresholdPrefixes: Set<String>,
        /** мера длительности, которой исполнитель считает (только у смен). */
        val measure: String?,
        /** ПРОЧТЕНИЕ исполнителя — то, что читатель сертификата обязан знать. */
        val reading: String,
        val considered: (FlightCrewSchedule, Map<String, RunThresholds.Applied>) -> Pair<Int, String>,
    ) {
        fun matches(l: RuleLogic): Boolean {
            if (l.demand.kind !in demandKinds) return false
            if (l.subject !in subjects) return false
            if (relations.isNotEmpty() && l.demand.relation !in relations) return false
            if (paramNames.isNotEmpty() && l.demand.param !in paramNames) return false
            if (!predicates.all { p -> l.conditions.any { it.predicate == p } }) return false
            // Мера: исполнитель считает размах; «налёт» — другое требование.
            if (measure != null && l.demand.measure != null && l.demand.measure != measure) return false
            return true
        }
    }

    private fun assignments(p: FlightCrewSchedule, @Suppress("UNUSED_PARAMETER") a: Map<String, RunThresholds.Applied>) =
        p.flightAssignments.size to "назначений набора"

    val TABLE: List<Entry> = listOf(
        Entry(listOf("Crew size"), setOf("flight"), setOf("count_within"), setOf("eq"),
            setOf("crew_size"), emptySet(), setOf("crew_size"), null,
            "число мест рейса равно численности; состав мест — факт набора",
            { p, _ -> p.flights.size to "рейсов" }),
        Entry(listOf("Reserve crews"), setOf("reserve_duty", "base"), setOf("count_within"),
            setOf("min", "eq"), setOf("reserve_crews"), emptySet(),
            setOf(DefaultProviderRules.RESERVE_FAMILY + "["), null,
            "на каждые (база, сутки) не менее N свободных экипажей по ролям состава",
            { p, a ->
                val codes = a.keys.filter { it.startsWith(DefaultProviderRules.RESERVE_FAMILY + "[") }
                // K-13: универсум клеток — единый горизонт (объявленное
                // окно, иначе сутки по вылетам), тот же, что у провайдера.
                val h = LogicForms.horizonOf(p)
                if (h == null || codes.isEmpty()) 0 to "клеток (база × сутки)"
                else (codes.size * (ChronoUnit.DAYS.between(h.lo, h.hi) + 1).toInt()) to "клеток (база × сутки)"
            }),
        Entry(listOf("Max duty period (rest-segmented)"), setOf("duty"), setOf("duration_within"),
            setOf("max"), setOf("max_duty_duration"), emptySet(), setOf("max_duty_span_min"), "span",
            "РАЗМАХ смены: от первого вылета до последнего прилёта нарезки по порогам отдыха (не налёт)",
            ::assignments),
        Entry(listOf("Max landings per duty"), setOf("duty"), setOf("count_within"),
            setOf("max"), setOf("max_landings_per_duty"), emptySet(), setOf("max_landings_per_duty"), null,
            "посадок в смене нарезки по порогам отдыха", ::assignments),
        Entry(listOf("Rest between duty periods"), setOf("rest"), setOf("duration_within"),
            setOf("min"), setOf("min_rest_home", "min_rest_outstation"), emptySet(),
            setOf("min_rest_home_min", "min_rest_outstation_min"), null,
            "порог по месту отдыха (база / вне базы) определяет нормативные границы смен; " +
                "разрыв ниже порога остаётся внутри смены и ограничивается её размахом; " +
                "мёртвая зона стыковки (day_break_threshold_min) действует только объявленной — " +
                "из минимума отдыха она не выводится",
            ::assignments),
        Entry(listOf("Minimum turnaround"), setOf("sit"), setOf("duration_within"),
            setOf("min"), setOf("min_turnaround"), emptySet(),
            setOf("min_sit_home_min", "min_sit_outstation_min"), null,
            "разрыв между соседними рейсами одного человека внутри смены; ручка по месту",
            ::assignments),
        Entry(listOf("Max monthly flight hours"), setOf("assignment", "month", "employee", "duty"),
            setOf("duration_within"), setOf("max"), setOf("max_monthly_block"), emptySet(),
            setOf("max_monthly_block_min"), null,
            "налёт (блок) одного человека за календарный месяц UTC", ::assignments),
        Entry(listOf("Flight conflict"), setOf("assignment"), setOf("not_exists", "combination_forbidden"),
            emptySet(), emptySet(), setOf("overlaps_in_time"), emptySet(), null,
            "два назначения одного человека, пересекающиеся во времени; цена за час пересечения",
            ::assignments),
        Entry(listOf("Employee unavailable"), setOf("assignment"), setOf("not_exists", "combination_forbidden"),
            emptySet(), emptySet(), setOf("marked_unavailable"), emptySet(), null,
            "назначение на сутки из перечня недоступности сотрудника (unavailableDays)",
            { p, _ -> p.employees.count { it.unavailableDays.isNotEmpty() } to
                "сотрудников с отмеченными днями недоступности" }),
        Entry(listOf("Type rating required", "Type rating currency"), setOf("assignment", "qualification"),
            setOf("combination_forbidden", "not_exists"), emptySet(), emptySet(),
            setOf("has_qualification_for"), emptySet(), null,
            "допуск на тип судна рейса (наличие) и его срок по датированным окнам " +
            "qualificationWindows (без окон срок не судится — «срок не передан»)",
            { p, _ -> p.employees.count { it.qualificationWindows.isNotEmpty() } to
                "сотрудников с датированными окнами допуска" }),
    )

    /** Имена профиля, которым блок logic не соответствует НИКОГДА, — с причиной. */
    val NOT_ATTRIBUTABLE: Map<String, String> = linkedMapOf(
        "Unassigned slot" to "полнота укомплектования — постановка, не правило свода",
        "Required skill" to "роль места — постановка, не правило свода",
        "Route continuity (hard)" to "связность маршрута — модель, не правило свода",
        "Max weekly flight hours" to "литерал профиля по ISO-неделе; ручки нет — ПЕРЕСЕЧЕНИЕ для окна 7 суток",
        "Language qualification" to "язык — данных в пакете нет",
        "Transfer between two flights" to "мягкое: вес из пакета, не logic",
        "Positioning deadhead cost" to "мягкое: вес из пакета, не logic",
        "Layover nights away from home" to "мягкое: вес из пакета, не logic",
        "Calendar window layover penalty" to
            "мягкое: окно/пояс формой пакета, вес из пакета, не logic",
        "Duty extension over threshold" to
            "мягкое: порог/потолок порогами пакета (семья " +
            "duty_extension_penalty), вес из пакета, не logic",
        "Preferred day off violation" to "мягкое: вес из пакета, не logic",
        "Undesired airport" to "мягкое: вес из пакета, не logic",
        "Reserve crew usage" to "мягкое: вес из пакета, не logic",
        "Too many duty days per week" to "мягкое: литерал 6 дней за ISO-неделю — ПЕРЕСЕЧЕНИЕ для «дней подряд»",
        "Fair red-eye distribution" to "мягкое: вес из пакета, не logic",
        // Ревизия 1.17 (история сотрудника): отдых от конца последней смены
        // истории до первого вылета горизонта — тот же пол отдыха (ручки
        // min_rest_*_min), что у «Rest between duty periods»; правило
        // ростеринга исполняется тем же порогом, отдельного logic нет.
        "Rest before horizon (history)" to
            "жёсткое: отдых до первой смены горизонта от последней смены истории (раздел history 1.17), " +
            "порог — ручки min_rest_home_min / min_rest_outstation_min того же правила; не logic",
        "Fair weekend distribution" to "мягкое: вес из пакета, не logic",
        "Credit hours spread" to "мягкое: целевая credit_hours_spread, вес — ставка целевой, не logic",
        "Credit hours spread over threshold" to "мягкое: вес из пакета, порог — параметр workload_spread_threshold, не logic",
        "Deadheads per day" to "перегоны: пределы из порогов пакета " +
            "(max_deadheads_per_flight/day), словарь logic предмета «перегон» " +
            "не описывает — при появлении в правилах отнести исполнителем",
        "Pairing chain integrity" to "целостность цепочки утверждённого " +
            "пейринга — постановка классического способа, не правило свода",
    )

    /** Пересечения, которые заявляются (не блокируют): подпись требования -> существующий счётчик. */
    fun overlaps(l: RuleLogic): List<String> {
        val out = ArrayList<String>()
        if (l.subject == "day" && l.demand.kind == "count_within" &&
            l.conditions.any { it.predicate == "consecutive" }) {
            out.add("ПЕРЕСЕЧЕНИЕ: мягкое ограничение профиля «Too many duty days per week» " +
                "считает дни работы за ISO-неделю литералом " +
                DefaultProviderRules.MAX_CONSECUTIVE_DUTY_DAYS + " — другое требование, тот же ростер")
        }
        if (l.subject == "rest" && l.demand.kind == "duration_within" &&
            l.conditions.any { it.predicate == "follows" }) {
            out.add("ПЕРЕСЕЧЕНИЕ: порог отдыха определяет границы нормативных смен; " +
                "это правило добавляет более строгий минимум после длинной смены; " +
                "стыковка внутри одной смены не становится отдыхом автоматически, а жёсткое " +
                "«Rest between duty periods» судит разрыв только при объявленном пороге мёртвой зоны")
            out.add("ПЕРЕСЕЧЕНИЕ: границы смен — нарезка по порогам отдыха прогона " +
                "(min_rest_home/min_rest_outstation); прогоны с разными порогами несопоставимы " +
                "по правилам о сменах")
        }
        if (l.demand.kind == "duration_within" && l.window?.kind == "rolling") {
            out.add("ПЕРЕСЕЧЕНИЕ: жёсткое ограничение профиля «Max weekly flight hours» считает " +
                "налёт за ISO-неделю литералом " + DefaultProviderRules.MAX_WEEKLY_FLIGHT_MINUTES +
                " мин — окно календарное, не скользящее")
        }
        return out
    }

    fun allKnownIds(): Set<String> =
        TABLE.flatMap { it.constraintIds }.toSet() + NOT_ATTRIBUTABLE.keys

    fun executorOf(threshold: String): List<String> =
        TABLE.filter { e -> e.thresholdPrefixes.any { threshold.startsWith(it) } }
            .flatMap { it.constraintIds }
}

object LogicCompiler {

    const val ST_GENERATED = "ПОРОЖДЕНО"
    const val ST_ELSEWHERE = "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ"
    const val ST_IGNORED = "НЕ ИСПОЛНЯЕТСЯ"
    const val ST_REFUSED = "ОТВЕРГНУТО"
    const val ST_NOT_HARD = "ВИД ПРАВИЛА"

    const val POST_CHECK = "post-check"
    const val REQUIRED = "required"

    private fun sha256(s: String): String =
        MessageDigest.getInstance("SHA-256").digest(s.toByteArray(Charsets.UTF_8))
            .joinToString("") { b -> "%02x".format(b) }

    /**
     * Имя порождённого ограничения: идентификаторы правил кириллические,
     * а Timefold принимает только латиницу, цифры, пробел и `_ . ( ) -` —
     * значит, имя производится от правила, а соответствие правило ↔ имя
     * едет полем отчёта.
     */
    fun constraintIdFor(ruleId: String): String = "logic " + sha256(ruleId).take(8)

    /** Канон формы — ровно то, что читает исполнитель; иное прочтение = иной отпечаток. */
    fun shapeOf(l: RuleLogic): String {
        val sb = StringBuilder()
        sb.append("subject=").append(l.subject)
        sb.append("|scope=").append(RunThresholds.normalizeQualifier(l.scope))
        for (c in l.conditions.sortedBy { it.predicate + "|" + canonicalArgs(it.args) }) {
            sb.append("|cond=").append(c.predicate).append("(").append(canonicalArgs(c.args)).append(")")
        }
        val d = l.demand
        sb.append("|demand=").append(d.kind)
            .append(";rel=").append(d.relation ?: "")
            .append(";param=").append(d.param ?: "")
            .append(";subject=").append(d.subject ?: "")
            .append(";universe=").append(d.universe ?: "")
            .append(";measure=").append(d.measure ?: "")
        sb.append("|window=").append(l.window?.canonical() ?: "")
        sb.append("|enforce=").append(l.enforce)
        return sha256(sb.toString())
    }

    private fun canonicalArgs(args: Map<String, Any>): String =
        args.entries.sortedBy { it.key }.joinToString(",") { (k, v) ->
            k + "=" + when (v) {
                is LogicWindow -> v.canonical()
                is LogicRef -> v.canonical()
                is List<*> -> v.joinToString("/") { it.toString() }
                else -> v.toString()
            }
        }

    private class Refusal(val reason: String) : RuntimeException(reason)

    /**
     * A9 (внешний аудит 04.09.2026): окно времени суток исполняется в
     * шкале меток данных, а шкала меток по контракту решателя — местное
     * время базы (base_tz_offset_min = 0). Поэтому исполнитель есть
     * только у окна с признаком пояса «base-local»: «acclimatized»
     * (пояс места акклиматизации) исполнителя не имеет, «UTC» при
     * шкале местного времени конвертировать нечем, отсутствие признака
     * — пробел производителя (спецификация требует его у time_of_day).
     * Прежде признак не читался, и такие окна считались как минуты
     * шкалы меток молча.
     */
    private fun requireBaseLocal(rec: RuleParam) {
        when (rec.tz) {
            "base-local" -> return
            null -> throw Refusal(
                "граница окна ночи «" + rec.name + "» правила " + rec.ruleId +
                    " без признака пояса (tz): спецификация требует его у времени суток; " +
                    "исполнитель есть только для base-local")
            else -> throw Refusal(
                "граница окна ночи «" + rec.name + "» правила " + rec.ruleId +
                    " с признаком пояса «" + rec.tz + "»: решатель считает время суток в " +
                    "шкале меток (местное время базы), исполнителя для «" + rec.tz +
                    "» нет — расширение явным шагом, не молча")
        }
    }

    /**
     * Раскладка блоков logic пакета по корзинам — функция ТОЛЬКО пакета и
     * сведённых порогов. Отказы корзины ОТВЕРГНУТО собраны, но не брошены:
     * бросает [refuseIfNeeded], чтобы в сообщении были ВСЕ причины разом.
     */
    fun compile(pkg: LoadedTaskPackage, thresholds: RunThresholds.Resolution,
                profile: String): LogicReport {
        return compileNative(pkg, thresholds, profile)
    }

    /** Common semantic compiler; no mode admission or run registration. */
    fun compileNative(pkg: NativeModel, thresholds: RunThresholds.Resolution,
                      profile: String): LogicReport {
        val generated = ArrayList<LogicEntry>()
        val elsewhere = ArrayList<LogicEntry>()
        val ignored = ArrayList<LogicEntry>()
        val refused = ArrayList<LogicEntry>()
        val notHard = ArrayList<LogicEntry>()

        val hard = pkg.logic.filter { it.ruleKind == "HARD" }
        // Имена порождённых — от правила; коллизия имён двух правил — отказ обоим.
        val idOwners = HashMap<String, MutableList<String>>()
        for (l in hard) idOwners.getOrPut(constraintIdFor(l.ruleId)) { ArrayList() }.add(l.ruleId)
        // Кандидаты на порождение: (правило, форма, область, границы).
        data class Candidate(val l: RuleLogic, val form: String, val baseScope: String?,
                             val bound: Int, val condBound: Int?, val condParam: String?,
                             val shape: String, val decl: List<String>,
                             val nightFrom: Int? = null, val nightTo: Int? = null,
                             val soft: Boolean = false)
        val candidates = ArrayList<Candidate>()

        for (l in pkg.logic) {
            val shape = shapeOf(l)
            val decl = ArrayList<String>()
            if (!l.enforceDeclared) {
                decl.add("УМОЛЧАНИЕ: enforce не объявлен, прочитан как required")
            }
            fun entry(state: String, ids: List<String>, via: List<String>, reason: String?,
                      extra: List<String> = emptyList()) = LogicEntry(
                ruleId = l.ruleId, state = state, enforce = l.enforce,
                subject = l.subject, demand = l.demand.kind, relation = l.demand.relation,
                constraintIds = ids, viaThresholds = via, reason = reason,
                declarations = decl + extra, shapeSha = shape)

            // Отказ при required — ОТВЕРГНУТО; при post-check — НЕ ИСПОЛНЯЕТСЯ с той же причиной.
            fun refuseOrIgnore(reason: String) {
                if (l.enforce == POST_CHECK) ignored.add(entry(ST_IGNORED, emptyList(), emptyList(), reason))
                else refused.add(entry(ST_REFUSED, emptyList(), emptyList(), reason))
            }
            if (l.ruleKind != "HARD") {
                // Волна П-2, этап 2: мягкое правило со СЛОВАРНЫМ шаблоном
                // (demand.kind из TEMPLATE_TO_FORM) порождается мягкой формой;
                // всё прочее — прежний notHard байт-в-байт.
                val softForm = if (l.ruleKind == "SOFT")
                    LogicForms.TEMPLATE_TO_FORM[l.demand.kind] else null
                if (softForm == null) {
                    notHard.add(entry(ST_NOT_HARD, emptyList(), emptyList(),
                        "вид правила " + (l.ruleKind.ifEmpty { "не указан" }) +
                        ": исполнителем ступени 3 служат только жёсткие правила (HARD); " +
                        "значения прочих видов заказчик требованием не объявлял"))
                    continue
                }
                try {
                    val roles = LogicForms.TEMPLATE_ROLES[l.demand.kind]!!
                    val got = l.demand.roleRefs.keys
                    if (got != roles) throw Refusal(
                        "шаблон «" + l.demand.kind + "» требует роли " +
                        (if (roles.isEmpty()) "(без ссылок)" else roles.sorted().joinToString(", ")) +
                        ", а объявлены " +
                        (if (got.isEmpty()) "(ничего)" else got.sorted().joinToString(", ")) +
                        ": решатель не угадывает")
                    pkg.logicUnproven[l.ruleId]?.let { why ->
                        throw Refusal(
                            "производитель не предъявил контрпримерную пару (manifest.logic_unproven: " +
                            why.ifEmpty { "без причины" } + "): принимать required-исполнителем " +
                            "недоказанную логику нельзя")
                    }
                    val baseScope = LogicForms.baseScopeOf(l)
                    if (l.scope != null && baseScope == null) throw Refusal(
                        "область «" + l.scope + "» для формы " + softForm + " не применима: форма " +
                        "различает только код базы; часть вместо целого не применяется")
                    fun minutesRole(role: String): Int {
                        val rec = resolveRef(l, l.demand.roleRefs[role]!!, "demand.$role")
                        if (rec.unit != "duration_minutes") throw Refusal(
                            "роль «" + role + "» (" + rec.name + ") единицей " + rec.unit +
                            ": ожидаются минуты (duration_minutes)")
                        val v = rec.value!!.toInt()
                        if (v <= 0) throw Refusal(
                            "роль «" + role + "» (" + rec.name + ") = " + rec.rawValue +
                            ": ожидается положительное число минут")
                        return v
                    }
                    when (softForm) {
                        LogicForms.FORM_SIT_OVERAGE_STEPS -> candidates.add(Candidate(
                            l, softForm, baseScope, minutesRole("threshold_param"),
                            minutesRole("step_param"),
                            l.demand.roleRefs["step_param"]!!.name, shape, decl, soft = true))
                        LogicForms.FORM_DUTY_SPLIT_SIT -> candidates.add(Candidate(
                            l, softForm, baseScope, minutesRole("threshold_param"),
                            null, null, shape, decl, soft = true))
                        LogicForms.FORM_CHAIN_NIGHT_DAY_MIX -> {
                            val fromRec = resolveRef(l, l.demand.roleRefs["window_from_param"]!!,
                                "demand.window_from_param")
                            val toRec = resolveRef(l, l.demand.roleRefs["window_to_param"]!!,
                                "demand.window_to_param")
                            for (rec in listOf(fromRec, toRec)) {
                                if (rec.unit != "time_of_day_minutes") throw Refusal(
                                    "граница окна ночи «" + rec.name + "» единицей " + rec.unit +
                                    ": ожидается время суток минутами (time_of_day_minutes)")
                                if (rec.value!!.toInt() >= 1440) throw Refusal(
                                    "граница окна ночи «" + rec.name + "» = " + rec.rawValue +
                                    " не является минутой суток (0..1439)")
                                requireBaseLocal(rec)
                            }
                            val fromMin = fromRec.value!!.toInt()
                            val toMin = toRec.value!!.toInt()
                            if (fromMin == toMin) throw Refusal(
                                "окно ночи пусто: от и до совпадают (" + fromMin + ")")
                            candidates.add(Candidate(l, softForm, baseScope, 0, null, null, shape,
                                decl + listOf("ОКНО НОЧИ: [" + fromMin + ", " + toMin + ") минут суток в шкале меток (местное время базы, tz base-local)"),
                                nightFrom = fromMin, nightTo = toMin, soft = true))
                        }
                        LogicForms.FORM_CONSECUTIVE_DAYS_MAX -> {
                            val rec = resolveRef(l, l.demand.roleRefs["bound_param"]!!,
                                "demand.bound_param")
                            if (rec.unit != "count") throw Refusal(
                                "роль «bound_param» (" + rec.name + ") единицей " + rec.unit +
                                ": ожидается счёт дней (count)")
                            val v = rec.value!!.toInt()
                            if (v <= 0) throw Refusal(
                                "роль «bound_param» (" + rec.name + ") = " + rec.rawValue +
                                ": ожидается положительное число дней")
                            candidates.add(Candidate(l, softForm, baseScope, v, null, null,
                                shape, decl, soft = true))
                        }
                        LogicForms.FORM_UNMET_REQUEST -> candidates.add(Candidate(
                            l, softForm, baseScope, 0, null, null, shape, decl, soft = true))
                        else -> throw Refusal("шаблон «" + l.demand.kind + "» без построителя")
                    }
                } catch (r: Refusal) {
                    refuseOrIgnore(r.reason)
                }
                continue
            }
            try {
                // Гейт 1: словарь и ссылки.
                gateDictionary(l)
                val owners = idOwners[constraintIdFor(l.ruleId)].orEmpty()
                if (owners.size > 1) throw Refusal(
                    "имя порождённого ограничения совпало у правил " + owners.joinToString(", ") +
                    ": одно имя — один исполнитель; решатель не угадывает, чьё требование считать")

                // Гейт 3: отнесение к действующему и пересечение с ручками.
                val viaKnobs = thresholds.applied.values
                    .filter { a -> l.ruleId in a.rules }.map { it.threshold }
                val attr = LogicAttribution.TABLE.firstOrNull { it.matches(l) }
                if (viaKnobs.isNotEmpty()) {
                    val feeds = attr != null &&
                        viaKnobs.any { t -> attr.thresholdPrefixes.any { t.startsWith(it) } }
                    if (!feeds) {
                        val executors = viaKnobs.flatMap { LogicAttribution.executorOf(it) }.distinct()
                        throw Refusal(
                            "параметры правила применены ручками " + viaKnobs.joinToString(", ") +
                            " (исполнитель: " + executors.joinToString(", ").ifEmpty { "нет" } +
                            "), а блок logic требует " + describe(l) +
                            ": ручка исполняет не это требование; второй исполнитель " +
                            "дал бы двойной счёт одного нарушения; решатель не угадывает")
                    }
                    elsewhere.add(entry(ST_ELSEWHERE, attr!!.constraintIds, viaKnobs, null,
                        listOf("ИСПОЛНИТЕЛЬ: " + attr.constraintIds.joinToString(", "),
                               "ПРОЧТЕНИЕ: " + attr.reading,
                               "ЧЕРЕЗ РУЧКУ: " + viaKnobs.joinToString(", ")) +
                        measureNote(l, attr) + LogicAttribution.overlaps(l)))
                    continue
                }
                if (attr != null) {
                    if (attr.thresholdPrefixes.isNotEmpty()) {
                        val why = thresholds.ignored.filter { l.ruleId in it.rules }
                            .joinToString("; ") { it.name + ": " + it.reason }
                        throw Refusal(
                            "исполнитель «" + attr.constraintIds.joinToString(", ") +
                            "» читает число ручкой " + attr.thresholdPrefixes.joinToString(", ") +
                            ", а параметры правила ручкой не применены" +
                            (if (why.isEmpty()) "" else " (" + why + ")") +
                            ": требование без своего числа не исполняется; порождать второй " +
                            "исполнитель — двойной счёт; решатель не угадывает")
                    }
                    elsewhere.add(entry(ST_ELSEWHERE, attr.constraintIds, emptyList(), null,
                        listOf("ИСПОЛНИТЕЛЬ: " + attr.constraintIds.joinToString(", "),
                               "ПРОЧТЕНИЕ: " + attr.reading) +
                        measureNote(l, attr) + LogicAttribution.overlaps(l)))
                    continue
                }

                // Гейт 2: выразимость кандидата на порождение.
                gateExpressibility(l)
                // Гейт 4: доказанность у производителя.
                pkg.logicUnproven[l.ruleId]?.let { why ->
                    throw Refusal(
                        "производитель не предъявил контрпримерную пару (manifest.logic_unproven: " +
                        why.ifEmpty { "без причины" } + "): принимать required-исполнителем " +
                        "недоказанную логику нельзя")
                }
                // Порождение: форма первой волны либо честный отказ.
                val form = LogicForms.formOf(l) ?: throw Refusal(
                    "форма требования " + describe(l) +
                    " потребителем не порождается (вне первой волны ступени 3): " +
                    "правило решателем не исполняется")
                val baseScope = LogicForms.baseScopeOf(l)
                if (l.scope != null && baseScope == null) throw Refusal(
                    "область «" + l.scope + "» для формы " + form + " не применима: форма " +
                    "различает только код базы; часть вместо целого не применяется")
                val longer = l.conditions.firstOrNull { it.predicate == "longer_than" }
                val condRef = longer?.args?.get("param") as? LogicRef
                if (form == LogicForms.FORM_CONSECUTIVE_NIGHT_DUTIES_MAX) {
                    // Границы: N — число требования; окно ночи — from/to
                    // (минуты времени суток; равные границы — пустое окно, отказ).
                    val bound = resolveRef(l,
                        LogicRef(l.demand.param ?: "", l.demand.paramQualifier),
                        "требование " + l.demand.kind).value!!.toInt()
                    val starts = l.conditions.first { it.predicate == "starts_in" }
                    val fromRec = resolveRef(l, starts.args["from_param"] as LogicRef, "starts_in.from")
                    val toRec = resolveRef(l, starts.args["to_param"] as LogicRef, "starts_in.to")
                    for (rec in listOf(fromRec, toRec)) {
                        if (rec.unit != "time_of_day_minutes") throw Refusal(
                            "граница окна ночи «" + rec.name + "» единицей " + rec.unit +
                            ": ожидается время суток минутами (time_of_day_minutes)")
                        if (rec.value!!.toInt() >= 1440) throw Refusal(
                            "граница окна ночи «" + rec.name + "» = " + rec.rawValue +
                            " не является минутой суток (0..1439)")
                        requireBaseLocal(rec)
                    }
                    val fromMin = fromRec.value!!.toInt()
                    val toMin = toRec.value!!.toInt()
                    if (fromMin == toMin) throw Refusal(
                        "окно ночи пусто: от и до совпадают (" + fromMin + ")")
                    candidates.add(Candidate(l, form, baseScope, bound, null, null, shape,
                        decl + listOf("ОКНО НОЧИ: [" + fromMin + ", " + toMin + ") минут суток в шкале меток (местное время базы, tz base-local)"))
                        .copy(nightFrom = fromMin, nightTo = toMin))
                    continue
                }
                if (form == LogicForms.FORM_REST_IN_ROLLING_WINDOW) {
                    // Границы: W — из longer_than, N суток — из параметра окна.
                    // Окно в минутах (10080) сводится к суткам ТОЛЬКО нацело.
                    val w = resolveRef(l, condRef ?: throw Refusal(
                        "форма окна без longer_than: длительность отдыха — число ниоткуда"),
                        "condition (longer_than)").value!!.toInt()
                    val winRec = resolveRef(l, LogicRef(l.window!!.param ?: "", null), "window (rolling)")
                    val winDays = when {
                        winRec.unit == "count" -> winRec.value!!.toInt()
                        winRec.unit == "duration_minutes" && winRec.value!!.toInt() % 1440 == 0 -> {
                            decl.add("ПРОЧТЕНИЕ: длина окна " + winRec.value.toInt() +
                                " мин прочитана как " + (winRec.value.toInt() / 1440) + " суток (нацело)")
                            winRec.value.toInt() / 1440
                        }
                        else -> throw Refusal(
                            "длина окна «" + winRec.rawValue + " " + winRec.unit +
                            "»: ожидаются сутки счётом либо минуты, кратные 1440; " +
                            "решатель не угадывает")
                    }
                    if (winDays <= 0) throw Refusal("длина окна " + winDays + " суток — не положительна")
                    candidates.add(Candidate(l, form, baseScope, w, winDays, winRec.name, shape, decl))
                    continue
                }
                if (form == LogicForms.FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN ||
                    form == LogicForms.FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX) {
                    // Счётчики скользящих окон (решение владельца 05.09.2026):
                    // K — число требования; N суток — из параметра окна (сутки
                    // счётом либо минуты, кратные 1440 — как у формы отдыха в
                    // окне); у ночной формы окно ночи — из starts_in (base-local).
                    val k = resolveRef(l,
                        LogicRef(l.demand.param ?: "", l.demand.paramQualifier),
                        "требование " + l.demand.kind).value!!.toInt()
                    if (k < 0) throw Refusal("граница счётчика окна " + k + " отрицательна")
                    val winRec = resolveRef(l, LogicRef(l.window!!.param ?: "", null), "window (rolling)")
                    val winDays = when {
                        winRec.unit == "count" -> winRec.value!!.toInt()
                        winRec.unit == "duration_minutes" && winRec.value!!.toInt() % 1440 == 0 -> {
                            decl.add("ПРОЧТЕНИЕ: длина окна " + winRec.value.toInt() +
                                " мин прочитана как " + (winRec.value.toInt() / 1440) + " суток (нацело)")
                            winRec.value.toInt() / 1440
                        }
                        else -> throw Refusal(
                            "длина окна «" + winRec.rawValue + " " + winRec.unit +
                            "»: ожидаются сутки счётом либо минуты, кратные 1440; " +
                            "решатель не угадывает")
                    }
                    if (winDays <= 0) throw Refusal("длина окна " + winDays + " суток — не положительна")
                    if (form == LogicForms.FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX) {
                        val starts = l.conditions.first { it.predicate == "starts_in" }
                        val fromRec = resolveRef(l, starts.args["from_param"] as LogicRef, "starts_in.from")
                        val toRec = resolveRef(l, starts.args["to_param"] as LogicRef, "starts_in.to")
                        for (rec in listOf(fromRec, toRec)) {
                            if (rec.unit != "time_of_day_minutes") throw Refusal(
                                "граница окна ночи «" + rec.name + "» единицей " + rec.unit +
                                ": ожидается время суток минутами (time_of_day_minutes)")
                            if (rec.value!!.toInt() >= 1440) throw Refusal(
                                "граница окна ночи «" + rec.name + "» = " + rec.rawValue +
                                " не является минутой суток (0..1439)")
                            requireBaseLocal(rec)
                        }
                        val fromMin = fromRec.value!!.toInt()
                        val toMin = toRec.value!!.toInt()
                        if (fromMin == toMin) throw Refusal(
                            "окно ночи пусто: от и до совпадают (" + fromMin + ")")
                        candidates.add(Candidate(l, form, baseScope, k, winDays, winRec.name, shape,
                            decl + listOf("ОКНО НОЧИ: [" + fromMin + ", " + toMin + ") минут суток в шкале меток (местное время базы − сдвиг)"))
                            .copy(nightFrom = fromMin, nightTo = toMin))
                    } else {
                        candidates.add(Candidate(l, form, baseScope, k, winDays, winRec.name, shape, decl))
                    }
                    continue
                }
                if (form == LogicForms.FORM_DUTY_SERIES_LIMIT) {
                    // ДВЕ границы, ни одна не выводится из другой: M —
                    // свободных суток после серии (число требования); N —
                    // длина серии из атома consecutive (сутки СЧЁТОМ).
                    val m = resolveRef(l,
                        LogicRef(l.demand.param ?: "", l.demand.paramQualifier),
                        "требование " + l.demand.kind).value!!.toInt()
                    if (m < 0) throw Refusal(
                        "свободных суток после серии " + m + " — отрицательное число")
                    val consec = l.conditions.first { it.predicate == "consecutive" }
                    val seriesRec = resolveRef(l, consec.args["param"] as LogicRef,
                        "condition (consecutive)")
                    if (seriesRec.unit != "count") throw Refusal(
                        "длина серии «" + seriesRec.name + "» единицей " + seriesRec.unit +
                        ": ожидаются сутки счётом (count); решатель не угадывает")
                    val seriesDays = seriesRec.value!!.toInt()
                    if (seriesDays <= 0) throw Refusal(
                        "длина серии " + seriesDays + " суток — не положительна")
                    candidates.add(Candidate(l, form, baseScope, m, seriesDays,
                        seriesRec.name, shape,
                        decl + listOf("ХВОСТ ПОСЛЕДНЕЙ СЕРИИ: не судится — следующей серии " +
                            "в наборе нет, свободные сутки после неё неизвестны")))
                    continue
                }
                val bound = resolveRef(l,
                    LogicRef(l.demand.param ?: "", l.demand.paramQualifier),
                    "требование " + l.demand.kind).value!!.toInt()
                // Вторая граница формы — порог условия longer_than (если есть).
                val condParamRec = condRef?.let { resolveRef(l, it, "condition (longer_than)") }
                candidates.add(Candidate(l, form, baseScope, bound,
                    condParamRec?.value?.toInt(), condRef?.name, shape, decl))
            } catch (r: Refusal) {
                refuseOrIgnore(r.reason)
            }
        }
        // Одна форма и область — один исполнитель: два правила свода об одном
        // предмете не считают одно нарушение дважды. Разные границы
        // сводятся строжайшей с заявлением СВЕДЕНО — как у порогов.
        for ((key, group) in candidates.groupBy {
                listOf(it.form, it.baseScope, it.condBound, it.nightFrom, it.nightTo, it.soft) }) {
            val form = key[0] as String
            val baseScope = key[1] as String?
            val soft = key[5] as Boolean
            val relation = group[0].l.demand.relation ?: "min"   // exists-формы читаются как «не менее»
            val strict = if (relation == "max") group.minOf { it.bound } else group.maxOf { it.bound }
            val winner = group.first { it.bound == strict }
            val ruleIds = group.map { it.l.ruleId }.sorted()
            val merged = ArrayList<String>()
            if (group.size > 1) merged.add("СВЕДЕНО: правила " + ruleIds.joinToString(", ") +
                " — одна форма, один исполнитель" +
                (if (group.any { it.bound != strict }) "; границы " +
                    group.joinToString(", ") { it.l.ruleId + "=" + it.bound } +
                    " сведены строжайшей " + strict else ""))
            val plan = LogicPlan(
                ruleIds = ruleIds,
                constraintId = constraintIdFor(ruleIds.joinToString("+")),
                form = form, subject = winner.l.subject, relation = relation,
                bound = strict,
                boundParam = winner.l.demand.param
                    ?: winner.l.demand.roleRefs["threshold_param"]?.name
                    ?: winner.l.demand.roleRefs["bound_param"]?.name ?: "",
                baseScope = baseScope,
                condBound = winner.condBound, condParam = winner.condParam,
                nightFrom = winner.nightFrom, nightTo = winner.nightTo,
                enforce = if (group.any { it.l.enforce != POST_CHECK }) REQUIRED else POST_CHECK,
                weight = if (soft) 1 else DefaultProviderRules.HARD_LOGIC_SHORTFALL,
                reading = when (form) {
                    LogicForms.FORM_DAYS_OFF_PER_MONTH_MIN -> LogicForms.READING_DAYS_OFF
                    LogicForms.FORM_CONSECUTIVE_DAYS_MAX -> LogicForms.READING_CONSECUTIVE
                    LogicForms.FORM_REST_AFTER_LONG_DUTY_MIN -> LogicForms.READING_REST_AFTER
                    LogicForms.FORM_REST_IN_ROLLING_WINDOW -> LogicForms.READING_ROLLING
                    LogicForms.FORM_CONSECUTIVE_NIGHT_DUTIES_MAX -> LogicForms.READING_NIGHT
                    LogicForms.FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN -> LogicForms.READING_DAYS_OFF_ROLLING
                    LogicForms.FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX -> LogicForms.READING_NIGHT_ROLLING
                    LogicForms.FORM_DUTY_SERIES_LIMIT -> LogicForms.READING_DUTY_SERIES
                    LogicForms.FORM_SIT_OVERAGE_STEPS -> LogicForms.READING_SIT_OVERAGE
                    LogicForms.FORM_DUTY_SPLIT_SIT -> LogicForms.READING_DUTY_SPLIT
                    LogicForms.FORM_CHAIN_NIGHT_DAY_MIX -> LogicForms.READING_CHAIN_MIX
                    LogicForms.FORM_UNMET_REQUEST -> LogicForms.READING_UNMET_REQUEST
                    else -> ""
                },
                shapeSha = winner.shape,
                soft = soft)
            for (c in group) {
                generated.add(LogicEntry(
                    ruleId = c.l.ruleId, state = ST_GENERATED, enforce = c.l.enforce,
                    subject = c.l.subject, demand = c.l.demand.kind, relation = c.l.demand.relation,
                    constraintIds = listOf(plan.constraintId), viaThresholds = emptyList(),
                    reason = null,
                    declarations = c.decl + listOf(
                        "ФОРМА: " + form + ", граница " + strict + " (" + plan.boundParam + ")" +
                            (plan.condBound?.let { ", порог условия " + it + " (" + plan.condParam + ")" } ?: ""),
                        "ПРОЧТЕНИЕ: " + plan.reading,
                        if (soft) "ВЕС: литерал 1 за единицу счёта (МЯГКАЯ форма) — " +
                            "вес компоненты применяется весами пакета прогона, не формой"
                        else "ВЕС: " + plan.weight + " за единицу нехватки — " + LogicForms.WEIGHT_SOURCE) +
                        (if (baseScope != null) listOf("ОБЛАСТЬ: база " + baseScope) else emptyList()) +
                        merged + LogicAttribution.overlaps(c.l),
                    shapeSha = c.shape, plan = plan))
            }
        }
        return LogicReport(
            blocksSeen = pkg.logic.size,
            generated = generated, executedElsewhere = elsewhere,
            ignored = ignored, refused = refused, notHard = notHard)
    }

    private fun measureNote(l: RuleLogic, attr: LogicAttribution.Entry): List<String> =
        if (attr.measure != null && l.demand.measure == null)
            listOf("ПРОЧТЕНИЕ: мера не названа, исполнитель считает " +
                   (if (attr.measure == "span") "размахом смены" else attr.measure))
        else emptyList()

    private fun describe(l: RuleLogic): String =
        l.demand.kind + (l.demand.relation?.let { " (" + it + ")" } ?: "") +
        " над «" + l.subject + "»" +
        (l.demand.measure?.let { ", мера " + it } ?: "") +
        (l.window?.let { ", окно " + it.canonical() } ?: "")

    /** Гейт 1: словарь и ссылки — с текстами по образцу RunThresholds.knobsFor. */
    private fun gateDictionary(l: RuleLogic) {
        val D = LogicDictionaries
        if (l.subject !in D.SUBJECTS) throw Refusal(
            "предмет «" + l.subject + "» вне словаря предметов первой волны (" +
            D.SUBJECTS.joinToString(", ") + "); решатель не угадывает")
        if (l.enforce !in D.ENFORCE) throw Refusal(
            "enforce «" + l.enforce + "» вне перечня " + D.ENFORCE.joinToString(", "))
        l.scope?.let { s ->
            val q = RunThresholds.normalizeQualifier(s)
            val known = q in RunThresholds.SCOPE_QUALIFIERS ||
                Regex("^[A-Z0-9]{2,4}$").matches(s.trim().uppercase())
            if (!known) throw Refusal(
                "область «" + s + "» вне словаря областей решателя (" +
                RunThresholds.SCOPE_QUALIFIERS.keys.joinToString(", ") +
                ", код базы): часть вместо целого не применяется; решатель не угадывает")
        }
        for ((i, c) in l.conditions.withIndex()) {
            val args = D.PREDICATES[c.predicate] ?: throw Refusal(
                "condition[" + i + "]: предикат «" + c.predicate + "» вне словаря предикатов")
            for (a in args) {
                val v = c.args[a] ?: throw Refusal(
                    "condition[" + i + "] (" + c.predicate + "): нет аргумента " + a)
                when {
                    a.endsWith("param") -> paramRef(l, v as LogicRef, "condition[" + i + "] (" + c.predicate + ")")
                    a == "unit" -> if (v !in D.UNITS) throw Refusal(
                        "condition[" + i + "] (" + c.predicate + "): единица «" + v +
                        "» вне перечня " + D.UNITS.joinToString(", ") +
                        " — «подряд» по суткам и по сменам дают разные ответы")
                    a == "subject" -> if (v !in D.SUBJECTS) throw Refusal(
                        "condition[" + i + "] (follows): предмет «" + v + "» вне словаря предметов")
                    a == "window" -> windowCheck(l, v as LogicWindow, "condition[" + i + "]")
                    a == "allowed" -> if (v !is List<*> || v.isEmpty()) throw Refusal(
                        "condition[" + i + "] (in_list): allowed — не непустой список")
                    a == "type_value" -> if (v.toString().isBlank()) throw Refusal(
                        "condition[" + i + "] (has_qualification_for): type_value пуст")
                }
            }
        }
        val d = l.demand
        val dargs = D.DEMANDS[d.kind] ?: throw Refusal(
            "требование «" + d.kind + "» вне словаря требований (" + D.DEMANDS.keys.joinToString(", ") + ")")
        for (a in dargs) {
            when (a) {
                "relation" -> {
                    val r = d.relation ?: throw Refusal("требование " + d.kind + ": нет отношения")
                    if (r !in D.DEMAND_RELATIONS) throw Refusal(
                        "требование " + d.kind + ": отношение «" + r + "» вне перечня " +
                        D.DEMAND_RELATIONS.joinToString(", ") +
                        " (window — отношение параметра-окна, не требования)")
                }
                "param" -> paramRef(l,
                    LogicRef(d.param ?: throw Refusal("требование " + d.kind + ": нет param"), d.paramQualifier),
                    "требование " + d.kind)
                "subject" -> if (d.subject == null || d.subject !in D.SUBJECTS) throw Refusal(
                    "требование " + d.kind + ": предмет «" + d.subject + "» вне словаря предметов")
                "universe" -> if (d.universe == null || d.universe !in D.UNIVERSES) throw Refusal(
                    "требование " + d.kind + ": универсум «" + d.universe + "» вне перечня " +
                    D.UNIVERSES.joinToString(", "))
            }
        }
        if (d.param == null && D.hasDigit(d.asWritten) && l.references().isEmpty()) throw Refusal(
            "требование " + d.kind + " несёт число в as_written («" + d.asWritten +
            "»), а ссылок на параметры у блока нет вовсе: число ниоткуда; нужен count_within с param")
        d.measure?.let { m -> if (m !in D.MEASURES) throw Refusal(
            "мера «" + m + "» вне перечня " + D.MEASURES.joinToString(", ")) }
        d.universe?.let { u -> if (u !in D.UNIVERSES) throw Refusal(
            "универсум «" + u + "» вне перечня " + D.UNIVERSES.joinToString(", ")) }
        l.window?.let { windowCheck(l, it, "window") }
        // source_params == множество применённых ссылок, в обе стороны.
        // Сверка ПО ИМЕНАМ (как у производителя): какая запись действует,
        // решает резолвер ссылки, а не перечень происхождения.
        val refs = l.references().map { it.name }.toSet()
        val declared = l.sourceParams.map { it.name }.toSet()
        if (refs != declared) throw Refusal(
            "source_params " + declared.sorted().joinToString(", ").ifEmpty { "пуст" } +
            " не совпадает со ссылками атомов " + refs.sorted().joinToString(", ").ifEmpty { "пусто" } +
            ": число, которого нет в source_params, либо ссылка, которой никто не применяет")
        val id = constraintIdFor(l.ruleId)
        if (!D.CONSTRAINT_ID.matches(id)) throw Refusal(
            "имя ограничения «" + id + "» не проходит выражение идентификатора Timefold")
    }

    private fun windowCheck(l: RuleLogic, w: LogicWindow, where: String) {
        val D = LogicDictionaries
        if (w.kind !in D.WINDOW_KINDS) throw Refusal(
            where + ": окно «" + w.kind + "» вне перечня " + D.WINDOW_KINDS.joinToString(", "))
        if (w.kind == "rolling") {
            if (w.unit !in D.UNITS) throw Refusal(
                where + ": у скользящего окна единица «" + w.unit + "» вне перечня " + D.UNITS.joinToString(", "))
            paramRef(l, LogicRef(w.param ?: throw Refusal(where + ": у скользящего окна нет param (длина окна — число ниоткуда)"), null),
                where + " (окно)")
        }
    }

    /**
     * Ссылка на параметр: разрешается в РОВНО ОДНУ запись ЭТОГО правила.
     * Без уточнителя — имя обязано быть единственным; с уточнителем (1.6,
     * ссылка-объект) — берётся запись с тем же уточнителем (нормализация
     * та же, что у ручек). Значение — целое положительное.
     */
    internal fun resolveRef(l: RuleLogic, ref: LogicRef, where: String): RuleParam {
        val byName = l.params.filter { it.name == ref.name }
        if (byName.isEmpty()) throw Refusal(
            where + ": ссылка на параметр «" + ref.name + "» — такой записи params у правила нет")
        val hits = if (ref.qualifier.isNullOrBlank()) byName
                   else byName.filter {
                       RunThresholds.normalizeQualifier(it.qualifier) ==
                       RunThresholds.normalizeQualifier(ref.qualifier)
                   }
        if (ref.qualifier != null && hits.isEmpty()) throw Refusal(
            where + ": ссылка «" + ref.canonical() + "» — записи с таким уточнителем нет " +
            "(есть: " + byName.joinToString(", ") { it.qualifier ?: "без уточнителя" } + ")")
        if (hits.size > 1) throw Refusal(
            where + ": правило несёт " + hits.size + " записи параметра «" + ref.name + "» (" +
            hits.joinToString(", ") { (it.qualifier ?: "без уточнителя") + " = " + it.rawValue } +
            "), а ссылка уточнителя не несёт — какое число действует, решатель не угадывает " +
            "(ревизия формата 1.6: ссылка {name, qualifier})")
        return hits[0]
    }

    private fun paramRef(l: RuleLogic, ref: LogicRef, where: String) {
        val p = resolveRef(l, ref, where)
        val v = p.value
        if (v == null || v != Math.floor(v) || v <= 0) throw Refusal(
            where + ": значение параметра «" + ref.canonical() + "» = " + p.rawValue +
            " не целое положительное число")
    }

    /** Гейт 2: выразимость кандидата на порождение (закрытый перечень главы 5). */
    private fun gateExpressibility(l: RuleLogic) {
        if (l.demand.kind == "duration_within" && l.subject in setOf("duty", "assignment") &&
            l.demand.measure == null) throw Refusal(
            "длительность над «" + l.subject + "» без меры: словарь 1.5 не различает размах " +
            "смены и налёт; умолчание подставлять нельзя (ревизия 1.6: demand.measure span | block)")
    }

    /** Бросить [TaskModeRefused] со ВСЕМИ причинами корзины ОТВЕРГНУТО. */
    fun refuseIfNeeded(r: LogicReport) {
        if (r.refused.isEmpty()) return
        throw TaskModeRefused(
            "блок logic: " + r.refused.size + " правил(а) с enforce=required не исполняются — " +
            r.refused.joinToString("; ") { "правило " + it.ruleId + ": " + it.reason })
    }

    /**
     * Факты набора против плана (fit): у каждого исполнителя, которому отнесено
     * правило, — сколько предметов он рассматривает ЗДЕСЬ. Ноль при required —
     * отказ (глава 7: пустая группа молчит, а «0 нарушений» неотличимо от
     * «считать нечего»); при post-check — НЕ ИСПОЛНЯЕТСЯ с заявлением ПУСТО.
     */
    fun fit(problem: FlightCrewSchedule, applied: Map<String, RunThresholds.Applied>,
            report: LogicReport, label: String): LogicReport {
        if (report.isEmpty) return report.copy(fitOn = label)
        val elsewhere = ArrayList<LogicEntry>()
        val generated = ArrayList<LogicEntry>()
        val ignored = ArrayList(report.ignored)
        val refused = ArrayList(report.refused)
        for (e in report.generated) {
            val plan = e.plan ?: continue
            val (n, measure, partial) = LogicForms.cells(problem, plan)
            val outside = if (partial.isEmpty()) emptyList()
                          else listOf("ВНЕ ОЦЕНКИ: неполные месяцы горизонта " + partial.joinToString(", "))
            if (n <= 0) {
                val reason = "ПУСТО: универсум формы " + plan.form + " на этом наборе пуст (" + measure +
                    " = 0): ни одной клетки, «0 нарушений» было бы неотличимо от «считать нечего»"
                if (e.enforce == POST_CHECK) ignored.add(e.copy(state = ST_IGNORED, reason = reason,
                    considered = 0, consideredMeasure = measure, declarations = e.declarations + outside,
                    plan = null))
                else refused.add(e.copy(state = ST_REFUSED, reason = reason,
                    considered = 0, consideredMeasure = measure, declarations = e.declarations + outside,
                    plan = null))
                continue
            }
            generated.add(e.copy(considered = n, consideredMeasure = measure,
                declarations = e.declarations + outside))
        }
        for (e in report.executedElsewhere) {
            val attr = LogicAttribution.TABLE.firstOrNull { it.constraintIds == e.constraintIds }
            val (n, measure) = attr?.considered?.invoke(problem, applied) ?: (0 to "предметов (исполнитель не описан)")
            if (n <= 0) {
                val reason = "ПУСТО: исполнитель «" + e.constraintIds.joinToString(", ") +
                    "» на этом наборе не рассматривает ни одного предмета (" + measure +
                    " = 0): «0 нарушений» было бы неотличимо от «считать нечего»"
                if (e.enforce == POST_CHECK) ignored.add(e.copy(state = ST_IGNORED, reason = reason,
                    considered = 0, consideredMeasure = measure))
                else refused.add(e.copy(state = ST_REFUSED, reason = reason,
                    considered = 0, consideredMeasure = measure))
                continue
            }
            elsewhere.add(e.copy(considered = n, consideredMeasure = measure))
        }
        return report.copy(generated = generated, executedElsewhere = elsewhere,
            ignored = ignored, refused = refused, fitOn = label)
    }

    /**
     * Имена, которые ЗАРЕГИСТРИРОВАЛА построенная фабрика, — от библиотеки,
     * не от намерения провайдера. Именно против них гейт сверяет «заявил».
     */
    fun registeredIds(factory: SolverFactory<*>): List<String> {
        val sdf = (factory as? DefaultSolverFactory<*>)
            ?.getScoreDirectorFactory<HardSoftScore>()
            ?: throw TaskModeRefused(
                "фабрика решателя не отдаёт фабрику счёта (" + factory.javaClass.name +
                "): перечень зарегистрированных ограничений недоступен, заявить ПРИНЯТО нечем")
        val meta = (sdf as? AbstractConstraintStreamScoreDirectorFactory<*, *, *>)
            ?.constraintMetaModel
            ?: throw TaskModeRefused(
                "фабрика счёта не потоковая (" + sdf.javaClass.name + "): метамодель ограничений недоступна")
        return meta.constraints.map { it.constraintRef.id() }.sorted()
    }
}
