package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.domain.solution.ConstraintWeightOverrides
import ai.timefold.solver.core.api.score.HardSoftScore
import java.math.BigDecimal

/**
 * Мост CDPM -> COSMOS: режим задачи, целевая функция и гейты запуска.
 *
 * Три режима спецификации (§4): `feasibility-hard` — любое допустимое
 * решение оптимально; `feasibility-soft` — среди допустимых
 * минимизировать штрафы мягких; `optimization` (этап Б) — выбранная
 * целевая функция, критерий = целевая + Σ штрафов мягких своей задачи.
 *
 * Каждая целевая — ОТДЕЛЬНАЯ задача оптимизации, поэтому выбор целевой
 * при optimization обязателен и явен: выбрать за заказчика решатель не
 * вправе.
 *
 * Все отказы — [TaskModeRefused] с причиной по-русски; молчаливых
 * умолчаний нет (условие МУ-02).
 */
class TaskModeRefused(message: String) : RuntimeException(message)

const val MODE_FEASIBILITY_HARD = "feasibility-hard"
const val MODE_FEASIBILITY_SOFT = "feasibility-soft"
const val MODE_OPTIMIZATION = "optimization"

/**
 * Что именно исполняется как целевая функция (режим optimization).
 *
 * Ярус важности 1 (§4) не является ценой единицы метрики. Вес читается
 * из машинного rate.value; только отсутствие legacy-носителя оставляет
 * единичный вес. Проза statement источником чисел не служит.
 */
data class ResolvedObjective(
    val id: String,
    val title: String,
    val metric: String?,
    val direction: String,
    /** ограничение решателя, играющее роль целевой. */
    val constraint: String,
    /** как найден исполнитель: словарь метрик либо таблица соответствий. */
    val executorSource: String,
    /** заявление приближения либо null, если целевая исполняется точно. */
    val approximation: String?,
    /** true, когда целевая и мягкий штраф метят в ОДНО ограничение. */
    val sharesConstraintWithPenalty: Boolean,
    val weight: Long,
    /** Exact rate from the package; null only for the legacy missing carrier. */
    val rate: BigDecimal? = null,
    val weightSource: String = "legacy: rate отсутствует; единичная цена метрики, не ярус",
) {
    /** One actual objective snapshot for ordinary runs and explicit native
     * qualification; semantic content only, not an admission decision. */
    fun snapshot(): Map<String, Any?> = mapOf(
        "id" to id,
        "title" to title,
        "metric" to metric,
        "direction" to direction,
        "constraint" to constraint,
        "executorSource" to executorSource,
        "weight" to weight,
        "rate" to rate,
        "weightSource" to weightSource,
        "approximation" to approximation,
        "sharesConstraintWithPenalty" to sharesConstraintWithPenalty,
        "criterion" to ("целевая + Σ штрафов мягких задачи " +
            "(direction=" + direction + ")"),
    )
}

/** Разрешённый к исполнению режим прогона и всё, что из него следует. */
data class ResolvedTaskMode(
    val mode: String,
    /** остановка по достижении допустимости (0 жёстких нарушений). */
    val stopAtFeasible: Boolean,
    /** переопределения весов из пакета либо null (прогон без пакета). */
    val weightOverrides: ConstraintWeightOverrides<HardSoftScore>?,
    /** веса в человекочитаемом виде — для гейта применённого (МУ-05). */
    val appliedWeights: Map<String, Long>,
    /**
     * Что фактически ушло решателю по каждому ограничению: штраф плюс
     * вклад целевой, если она метит туда же. Слагаемые названы порознь
     * в [appliedWeights] и [objective] — здесь их сумма, то есть сам
     * критерий, чтобы его не приходилось восстанавливать из двух мест.
     */
    val effectiveWeights: Map<String, Long>,
    /** целевая функция прогона либо null вне режима optimization. */
    val objective: ResolvedObjective?,
    /** пороги жёстких правил из пакета: применённые И проигнорированные. */
    val thresholds: RunThresholds.Resolution,
    /**
     * Профиль ограничений, которым прогон СЧИТАЛ, и его пороги.
     *
     * Отчёт применённого прежде отдавал показания ЯРУСА правил
     * (`RulesConfig`), а профиль по умолчанию ярус не читает вовсе — его
     * числа литеральны. Потребитель отчёта брал оттуда порог отдыха и
     * резал по нему смены, тогда как прогон считал по другому числу.
     * Разойтись так можно только молча: обе величины правдоподобны.
     */
    val rulesProfile: String,
    val profileReadings: Map<String, Int>,
    val fingerprint: String?,
    val customer: String?,
    val sourceRun: String?,
    val revision: Int?,
    val unmappedRules: List<String>,
    /** Д4: правило пакета -> ограничение, куда лёг его вес (объявление
     *  решателя для штрафной сверки приёмника). */
    val weightRules: Map<String, String> = emptyMap(),
    /** Ревизия литералов 2026-09-03: мягкие ограничения профиля, не
     *  названные пакетом и не исполняющие целевую, — вес 0 (выключены). */
    val literalsDisabled: List<String> = emptyList(),
    /**
     * Покрытие проверок фактов (RunThresholds.checkFacts): что рассмотрено
     * до старта — рейсы и места для численности, базы, горизонт и роли
     * для резерва. Заполняется ресурсом после загрузки набора.
     */
    val coverage: Map<String, Any?> = emptyMap(),
    /** ступень 3: раскладка блоков logic пакета по корзинам (null — пакета нет). */
    val logic: LogicReport? = null,
    /** Actual opaque designations; absent key means not measured, not an empty fleet. */
    val profileCodeReadings: Map<String, List<String>> = emptyMap(),
    val profilePolicyReadings: Map<String, DutyExtensionReading> = emptyMap(),
)

/**
 * Словарь машинных метрик спецификации (§5а, версия 1.1) — исполнители
 * на стороне решателя.
 *
 * Словарь ЗАКРЫТ: имя вне таблицы означает, что пакет старше или новее
 * нашего понимания, и догадка запрещена (спецификация §5а). Каждой
 * метрике объявлена зона применимости: `total_pairing_cost` вычислима
 * только раздельным прогоном пейринга, и на объединённой модели
 * ростеринга подставлять похожую величину нельзя.
 */
data class MetricExecutor(
    val constraint: String,
    val tasks: Set<String>,
    /** заявление приближения, если исполнитель считает не саму величину. */
    val approximation: String? = null,
    /** причина отказа, если метрика неисполнима на модели прогона. */
    val refusal: String? = null,
)

val METRIC_EXECUTORS: Map<String, MetricExecutor> = mapOf(
    "layover_nights_total" to MetricExecutor(
        constraint = "Layover nights away from home",
        tasks = setOf("pairing", "rostering")),
    "positioning_cost" to MetricExecutor(
        constraint = "Positioning deadhead cost",
        tasks = setOf("pairing", "rostering")),
    "uncovered_seats" to MetricExecutor(
        constraint = "Unassigned slot",
        tasks = setOf("pairing", "rostering")),
    "preferred_days_off_violations" to MetricExecutor(
        constraint = "Preferred day off violation",
        tasks = setOf("rostering")),
    "credit_hours_spread" to MetricExecutor(
        constraint = "Credit hours spread",
        tasks = setOf("rostering"),
        approximation = "ПРОЧТЕНИЕ (решение владельца 2026-09-03): разброс " +
            "кредитных часов = √Σ(xᵢ−x̄)² по ВСЕМУ штату (сотрудник без " +
            "назначений — 0 часов) в ЦЕЛЫХ часах (минуты налёта / 60, " +
            "усечение); кредитный час = час налёта по назначению " +
            "(определение общего раздела ВМП), перелёт пассажиром не " +
            "засчитывается. Целевая берёт разброс целиком; порог " +
            "существенности судит только правило семьи balanced_workload " +
            "(ограничение «Credit hours spread over threshold»)"),
    "total_pairing_cost" to MetricExecutor(
        constraint = "",
        tasks = setOf("pairing"),
        refusal = "метрика total_pairing_cost вычислима только раздельным " +
            "прогоном пейринга; на объединённой модели назначения " +
            "стоимости пейрингов нет, а похожую величину подставлять " +
            "нельзя"),
)

object TaskModes {

    val KNOWN = listOf(MODE_FEASIBILITY_HARD, MODE_FEASIBILITY_SOFT,
        MODE_OPTIMIZATION)

    /**
     * Таблица соответствий для целевых БЕЗ машинной метрики — запасной
     * путь спецификации §5а: словарь потребителя может быть старее
     * пакета, и тогда целевая исполняется по явному соответствию, а не
     * по догадке.
     *
     * Заполняется так же узко и явно, как [TaskPackage.RULE_TO_CONSTRAINT]:
     * идентификатор целевой -> имя ограничения решателя. Целевой зоны
     * пейринга здесь намеренно НЕТ: суммарная стоимость пейрингов
     * неисполнима на объединённой модели ни через метрику, ни через
     * таблицу, и запасной путь не должен становиться лазейкой в обход
     * этого отказа.
     *
     * С ревизии 1.14 пакет может нести подсекцию objective_to_constraint
     * секции rule_maps ([LoadedTaskPackage.objectiveToConstraint]) —
     * тогда действует КАРТА ПАКЕТА, а эта таблица остаётся
     * легаси-умолчанием для пакетов без подсекции. Снятие таблицы —
     * отдельный шаг после переиздания живых пакетов.
     */
    val OBJECTIVE_TO_CONSTRAINT: Map<String, String> = mapOf(
        "O-S1-табл-2-строка-2-кол-3-2" to "Credit hours spread",
    )

    /** Имя профиля по умолчанию: он ЯРУС НЕ ЧИТАЕТ, числа литеральны. */
    const val PROFILE_DEFAULT = "DEFAULT"

    /**
     * Пороги, по которым прогон считал НА САМОМ ДЕЛЕ.
     *
     * У профиля по умолчанию — его собственные литералы; у профилей
     * GERAD — показания яруса правил. Смешивать их нельзя: именно
     * смешение и дало расхождение, ради которого поле появилось.
     */
    /**
     * Показания профиля по умолчанию ДО сборки — только для прогона без
     * пакета. С пакетом показания берутся у созданного провайдера, а не
     * вычисляются заранее: заранее вычисленное совпало бы с пакетом по
     * построению, и гейт «прислали = применили» сравнивал бы пакет с
     * самим собой (так и было в ревизии 1).
     */
    fun readingsOf(profile: String): Map<String, Int> =
        if (profile == PROFILE_DEFAULT) DefaultProviderRules.readings()
        else RulesConfig.appliedReadings()

    /**
     * Дополнить снимок прогона ФАКТИЧЕСКИМИ показаниями созданного
     * провайдера. Вызывается после сборки фабрики решателя.
     */
    fun withActualReadings(m: ResolvedTaskMode): ResolvedTaskMode {
        val actual = LastProviderReadings.snapshotWithCodes()
        return withActualReadings(m, actual?.readings, actual?.codeReadings ?: emptyMap(),
            actual?.policyReadings ?: emptyMap())
    }

    /**
     * То же — с показаниями, снятыми ВЫЗЫВАЮЩИМ (внутри окна сборки, этап Г
     * Ш-2 п.2): между закрытием окна и съёмом из глобального держателя есть
     * зазор, в котором чужая сборка подменяет числа; снятое внутри окна
     * гонки не имеет по построению.
     */
    fun withActualReadings(m: ResolvedTaskMode, readings: Map<String, Int>?,
                           codeReadings: Map<String, List<String>> = emptyMap(),
                           policyReadings: Map<String, DutyExtensionReading> = emptyMap()): ResolvedTaskMode {
        val actual = if (m.rulesProfile == PROFILE_DEFAULT)
            readings
            ?: throw TaskModeRefused(
                "провайдер профиля по умолчанию не записал показания: " +
                "сборка прошла мимо конструктора, отчитаться нечем")
        else RulesConfig.appliedReadings()
        return m.copy(profileReadings = LinkedHashMap(actual),
            profileCodeReadings = if (m.rulesProfile == PROFILE_DEFAULT)
                codeReadings.mapValues { (_, codes) -> codes.toList() } else emptyMap(),
            profilePolicyReadings = if (m.rulesProfile == PROFILE_DEFAULT)
                policyReadings.mapValues { (_, reading) -> reading.detached() } else emptyMap())
    }

    /** Направления, которые умеет исполнять решатель. */
    const val DIRECTION_MINIMIZE = "minimize"
    const val DIRECTION_MAXIMIZE = "maximize"

    /**
     * Свести запрошенный режим и пакет постановки к тому, что будет
     * исполнено, либо отказать.
     *
     * @param requested значение параметра mode запроса (может быть null)
     * @param pkg пакет постановки текущего прогона (может быть null)
     * @param task задача, к которой отнесён прогон: на этапе А
     *        объединённая модель назначения трактуется как РОСТЕРИНГ
     *        (постановка §2, «проекция задач на прогон решателя»).
     */
    fun resolve(requested: String?, pkg: LoadedTaskPackage?,
                task: String, objectiveId: String? = null,
                rulesProfile: String? = null): ResolvedTaskMode {
        val profile = rulesProfile?.trim()?.uppercase().orEmpty()
            .ifEmpty { PROFILE_DEFAULT }
        val asked = requested?.trim()?.lowercase().orEmpty()
        if (asked.isNotEmpty() && asked !in KNOWN) {
            throw TaskModeRefused(
                "неизвестный режим задачи " + asked + "; известные: " +
                KNOWN.joinToString(", "))
        }
        // Целевая приходит ТОЛЬКО пакетом: без пакета оптимизировать
        // нечего, а свои умолчания подставлять запрещено (МУ-03).
        if (asked == MODE_OPTIMIZATION && pkg == null) {
            throw TaskModeRefused(
                "режим " + MODE_OPTIMIZATION + " требует пакета постановки: " +
                "целевая функция берётся из пакета, задать её иначе нельзя")
        }

        if (pkg == null) {
            // Прогон без пакета: прежнее поведение. Режим по умолчанию —
            // минимизация штрафов; допустимость по запросу разрешена.
            val mode = if (asked.isEmpty()) MODE_FEASIBILITY_SOFT else asked
            return ResolvedTaskMode(
                mode = mode,
                stopAtFeasible = mode == MODE_FEASIBILITY_HARD,
                weightOverrides = null,
                appliedWeights = emptyMap(),
                effectiveWeights = emptyMap(),
                objective = null,
                thresholds = RunThresholds.Resolution(emptyMap(), emptyList()),
                rulesProfile = profile,
                profileReadings = emptyMap(),
                fingerprint = null, customer = null, sourceRun = null,
                revision = null, unmappedRules = emptyList())
        }

        if (pkg.landingArchiveOnly) {
            throw TaskModeRefused("landing-version: архивное чтение старого пакета не разрешает исполнение")
        }
        val mode = if (asked.isEmpty()) MODE_FEASIBILITY_SOFT else asked
        // Гейт запуска: режим обязан быть в allowed_modes СВОЕЙ задачи.
        val allowed = pkg.allowedModes[task].orEmpty()
        if (mode !in allowed) {
            throw TaskModeRefused(
                "режим " + mode + " не разрешён пакетом для задачи " + task +
                ": allowed_modes=" + (if (allowed.isEmpty()) "пусто"
                    else allowed.joinToString(", ")))
        }
        // Компонент без числового веса при режиме со штрафами — отказ,
        // а не умолчание (спецификация §4, условие МУ-02).
        if (mode != MODE_FEASIBILITY_HARD && pkg.componentsWithoutWeight.isNotEmpty()) {
            throw TaskModeRefused(
                "в пакете есть мягкие компоненты без подтверждённого веса, " +
                "а запрошен режим со штрафами (" + mode + "): " +
                pkg.componentsWithoutWeight.joinToString(", "))
        }

        // В режиме допустимости штрафы не критерий — веса не применяем:
        // прогон обязан остановиться на первом же допустимом решении.
        val useWeights = mode != MODE_FEASIBILITY_HARD
        val objective = if (mode == MODE_OPTIMIZATION)
            resolveObjective(objectiveId, pkg, task) else null

        // Content resolution is shared with explicit local qualification.
        // It is deliberately BELOW every ordinary package/admission gate.
        val native = NativeExecutionPlan.resolve(pkg, profile, useWeights, objective)

        return ResolvedTaskMode(
            mode = mode,
            stopAtFeasible = mode == MODE_FEASIBILITY_HARD,
            weightOverrides = native.weightOverrides,
            appliedWeights = native.appliedWeights,
            effectiveWeights = native.effectiveWeights,
            objective = objective,
            thresholds = native.thresholds,
            logic = native.logic,
            rulesProfile = profile,
            // Показания заполняются ПОСЛЕ сборки провайдера — из того, что
            // он прочитал на самом деле (см. [withActualReadings]).
            profileReadings = emptyMap(),
            fingerprint = pkg.fingerprint,
            customer = pkg.customer,
            sourceRun = pkg.sourceRun,
            revision = pkg.revision,
            unmappedRules = pkg.unmappedRules,
            weightRules = pkg.weightRules,
            literalsDisabled = native.literalsDisabled)
    }

    /**
     * Свести запрошенную целевую к исполнителю либо отказать.
     *
     * Порядок проверок задан спецификацией: явный `objective_id`
     * обязателен (§7 п.2); целевая обязана принадлежать СВОЕЙ зоне;
     * направление берётся машинным полем; метрика — из закрытого
     * словаря, а неизвестное имя идёт запасным путём таблицы
     * соответствий либо честно отвергается (§5а). Ни одной догадки.
     */
    // Internal (not private): предпрогонная сверка покрытия (П-1) судит
    // вычислимость каждой целевой тем же резолвером, что и прогон.
    internal fun resolveObjective(objectiveId: String?, pkg: LoadedTaskPackage,
                                 task: String): ResolvedObjective {
        val wanted = objectiveId?.trim().orEmpty()
        val declared = pkg.objectives[task].orEmpty()
        if (wanted.isEmpty()) {
            throw TaskModeRefused(
                "режим " + MODE_OPTIMIZATION + " требует явного objective_id: " +
                "каждая целевая — отдельная задача оптимизации, выбирать " +
                "за заказчика нельзя. Целевые задачи " + task + ": " +
                (if (declared.isEmpty())
                    "пусто — критерий оптимизации не задан данными; " +
                    "доступны только режимы " + MODE_FEASIBILITY_HARD +
                    " / " + MODE_FEASIBILITY_SOFT +
                    " (решение владельца 2026-08-26)"
                 else declared.joinToString(", ")))
        }
        if (wanted !in declared) {
            throw TaskModeRefused(
                "целевая " + wanted + " не объявлена пакетом для задачи " +
                task + ": " + (if (declared.isEmpty()) "целевых нет"
                    else "есть " + declared.joinToString(", ")))
        }
        val details = pkg.objectiveDetails[task].orEmpty()
        val obj = details.firstOrNull { it.id == wanted }
            ?: throw TaskModeRefused(
                "целевая " + wanted + " объявлена манифестом, но её описания " +
                "нет в objective.json зоны " + task)

        return resolveNativeObjective(obj, pkg, task)
    }

    /** Resolve already-selected native content. This does NOT check or grant
     * customer admission: resolveObjective above retains the mandatory ID,
     * manifest membership and detail checks before entering this shared law. */
    internal fun resolveNativeObjective(obj: PackageObjective, pkg: NativeModel,
                                        task: String): ResolvedObjective {
        val wanted = obj.id

        if (obj.direction == DIRECTION_MAXIMIZE) {
            throw TaskModeRefused(
                "целевая " + wanted + " объявлена с направлением maximize, " +
                "а её величина по смыслу минимизируемая (стоимость, " +
                "разброс, нарушения, непокрытые места): исполнять такое " +
                "направление значило бы догадываться о намерении " +
                "заказчика. Нужна ревизия постановки")
        }
        if (obj.direction != DIRECTION_MINIMIZE) {
            throw TaskModeRefused(
                "у целевой " + wanted + " нечитаемое направление «" +
                obj.direction + "»; известные: " + DIRECTION_MINIMIZE + ", " +
                DIRECTION_MAXIMIZE)
        }

        // One rate law for all executor paths, including both table fallbacks.
        // This runs only for the selected optimization objective: a fractional
        // rate is not a parameter of feasibility-hard / feasibility-soft.
        val weight = if (obj.rate == null) 1L else {
            val exact = try {
                obj.rate.longValueExact()
            } catch (e: ArithmeticException) {
                throw TaskModeRefused(
                    "целевая " + wanted + ": ставка rate.value=" + obj.rate +
                    " не представима точным положительным Long (1.." + Long.MAX_VALUE +
                    "); дробная ставка или переполнение требуют другого числового " +
                    "контракта, округление и подстановка 1 запрещены")
            }
            if (exact <= 0L) {
                throw TaskModeRefused(
                    "целевая " + wanted + ": ставка rate.value обязана быть > 0")
            }
            exact
        }
        val weightSource = if (obj.rate == null)
            "legacy: rate отсутствует; единичная цена метрики, не ярус"
        else "objective.json: " + task + ".objectives[" + wanted + "].rate.value"

        // Запасной путь §5а — карта «целевая -> ограничение». Подсекция
        // objective_to_constraint секции rule_maps пакета (ревизия 1.14)
        // сильнее зашитой таблицы: при живой подсекции легаси-таблица не
        // читается вовсе. Источник исполнителя называется в отчёте.
        val objectiveTable = pkg.objectiveToConstraint ?: OBJECTIVE_TO_CONSTRAINT
        val tableSource = if (pkg.objectiveToConstraint != null)
            "карта пакета rule_maps" else "таблица соответствий"

        val metric = obj.metric
        if (metric != null) {
            val ex = METRIC_EXECUTORS[metric]
            if (ex == null) {
                // Словарь потребителя старее пакета: запасной путь —
                // таблица соответствий, догадка запрещена (§5а).
                val byTable = objectiveTable[wanted]
                    ?: throw TaskModeRefused(
                        "метрика " + metric + " вне известного словаря, и " +
                        "целевой " + wanted + " нет в таблице соответствий: " +
                        "подставлять похожую величину нельзя. Известные " +
                        "метрики: " + METRIC_EXECUTORS.keys.joinToString(", "))
                return ResolvedObjective(
                    id = wanted, title = obj.title, metric = metric,
                    direction = obj.direction, constraint = byTable,
                    executorSource = tableSource + " (метрика вне словаря)",
                    approximation = null,
                    sharesConstraintWithPenalty = byTable in pkg.weights,
                    weight = weight, rate = obj.rate, weightSource = weightSource)
            }
            ex.refusal?.let { throw TaskModeRefused(it) }
            if (task !in ex.tasks) {
                throw TaskModeRefused(
                    "метрика " + metric + " не применима задаче " + task +
                    ": словарь объявляет её для " + ex.tasks.joinToString(", "))
            }
            return ResolvedObjective(
                id = wanted, title = obj.title, metric = metric,
                direction = obj.direction, constraint = ex.constraint,
                executorSource = "словарь метрик §5а",
                approximation = ex.approximation,
                sharesConstraintWithPenalty = ex.constraint in pkg.weights,
                weight = weight, rate = obj.rate, weightSource = weightSource)
        }

        // Целевая без машинной метрики — только карта соответствий.
        val byTable = objectiveTable[wanted]
            ?: throw TaskModeRefused(
                "у целевой " + wanted + " нет машинной метрики, и в таблице " +
                "соответствий её тоже нет: исполнителя не существует. " +
                "Прозу statement машина не разбирает")
        return ResolvedObjective(
            id = wanted, title = obj.title, metric = null,
            direction = obj.direction, constraint = byTable,
            executorSource = tableSource + " (целевая без метрики)",
            approximation = null,
            sharesConstraintWithPenalty = byTable in pkg.weights,
            weight = weight, rate = obj.rate, weightSource = weightSource)
    }
}

/**
 * Что было применено в последнем прогоне — для гейта применённого
 * (условие МУ-05): производитель сверяет «прислали = применили».
 * Заявление без чтения и есть та самая «передали, но не применили»,
 * поэтому запоминается ФАКТ применения, а не намерение.
 */
object LastRun {
    @Volatile private var last: ResolvedTaskMode? = null

    fun remember(m: ResolvedTaskMode) { last = m }

    fun forget() { last = null }

    fun snapshot(): Map<String, Any?>? = last?.let { snapshotOf(it) }

    /** Снимок ЛЮБОГО прогона — и «последнего», и адресуемого по runId (этап Г). */
    fun snapshotOf(m: ResolvedTaskMode): Map<String, Any?> {
        val obj = m.objective
        return mapOf(
            "mode" to m.mode,
            "stopAtFeasible" to m.stopAtFeasible,
            "appliedWeights" to m.appliedWeights,
            "effectiveWeights" to m.effectiveWeights,
            "rulesProfile" to m.rulesProfile,
            "profileReadings" to m.profileReadings,
            "profileCodeReadings" to m.profileCodeReadings,
            "profilePolicyReadings" to m.profilePolicyReadings.mapValues { (_, reading) -> reading.snapshot() },
            // Снимок порогов — вместе с показаниями созданного провайдера:
            // по ним в отчёт попадают и литералы В СИЛЕ, о которых пакет
            // молчит (число, по которому судили, не бывает невидимым).
            "thresholds" to RunThresholds.snapshot(m.thresholds, m.profileReadings),
            "coverage" to m.coverage,
            // Ступень 3: корзины logic + имена, зарегистрированные фабрикой.
            "logic" to m.logic?.snapshot(),
            "objective" to obj?.snapshot(),
            "packageFingerprint" to m.fingerprint,
            "customer" to m.customer,
            "sourceRun" to m.sourceRun,
            "revision" to m.revision,
            "rulesWithoutExecutor" to m.unmappedRules,
            // Д4: объявление решателя «правило -> ограничение веса» для
            // штрафной сверки приёмника (vmp/penalty_reconcile.py).
            "weightRules" to m.weightRules,
            // Ревизия литералов 2026-09-03: какие мягкие ограничения
            // профиля прогон по пакету выключил (вес 0).
            "literalsDisabled" to m.literalsDisabled,
        )
    }
}
