package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.api.solver.event.BestSolutionChangedEvent
import ai.timefold.solver.core.config.solver.SolverConfig
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.atomic.AtomicReference
import java.util.logging.Logger

/**
 * Прогоны по идентификатору (этап Г, контракт Ш-2/Ш-2а/Ш-5).
 *
 * Кнопке этапа Г нельзя жить на «последнем прогоне» и env-переменной:
 * LastRun — один слот на процесс (при двух прогонах сертифицируется
 * чужой), показания снимались ВНЕ окна сборки (гонка), а синхронный
 * HTTP-запрос длинного прогона рвётся таймаутом простоя (живой обрыв
 * 45-минутного прогона 2026-08-24). Здесь прогон получает идентификатор;
 * решение, показания и повкладовый счёт адресуются ему, а не «последнему».
 *
 * Состояния прогона (Ш-2а): СЧИТАЕТ → ГОТОВ | УПАЛ (причина словами).
 * Показания снимаются ВНУТРИ окна сборки — гонка исчезает по построению.
 */
data class RunRecord(
    val runId: String,
    val startedAtMs: Long,
    val packageFingerprint: String,
    val mode: String,
    /** задача прогона: rostering | pairing (проекция этапа А, §2). */
    val task: String = "rostering",
    /** классический способ: прогон пейринга, чьи цепочки зафиксированы. */
    val pairingRunId: String? = null,
    /** число зафиксированных цепочек утверждённого пейринга. */
    val chains: Int = 0,
    @Volatile var state: String = PackagedRuns.ST_RUNNING,
    @Volatile var failure: String? = null,
    @Volatile var solution: FlightCrewSchedule? = null,
    @Volatile var score: String? = null,
    /** снимок прогона (пороги, показания, logic) — источник certifier-input. */
    @Volatile var report: ResolvedTaskMode? = null,
    /** пакет прогона: под ним считает /score-breakdown этого runId. */
    val pkg: LoadedTaskPackage,
    /** Г-П4: внешний каталог пейринга (ortools-построитель) — готовые
     *  метки цепочек по МЕСТАМ набора; при непустом поле фиксация идёт
     *  по нему, решение прогона не требуется. */
    val chainBySeat: Map<String, String>? = null,
    /** Шаг 4 плана 05.09.2026: перегоны каждой цепочки каталога (номера
     *  рейсов по имени цепочки-слоя) — перегонное кресло цепочки входит
     *  в её единицу хода и раздачи, иначе маршрут рвётся на перегоне. */
    val dhByChain: Map<String, List<String>>? = null,
    /** Набор ходов прогона (moves) и итог раздачи по цепочкам. */
    val moves: String? = null,
    @Volatile var seeded: Int? = null,
    @Volatile var seedBreakdown: Map<String, Any?>? = null,
    /** K-30-c: итог раздачи по цепочкам поимённо — {checked, fallback,
     *  unseeded}; цепочки, отданные непроверенному кандидату (fallback),
     *  успешной раздачей не считаются; null — раздачи не было. */
    @Volatile var seedReading: Map<String, Any?>? = null,
    /** Показание о перегонах (решение владельца 05.09.2026): разрешены ли,
     *  основание, кресел на рейс, использовано после счёта. */
    @Volatile var deadheads: Map<String, Any?>? = null,
    /** Показание о допущении нулевой истории (HistoryAssumption): срез,
     *  признак, решение пользователя, сколько сотрудников без записи
     *  получили нулевую запись; null — раздела истории в наборе нет. */
    @Volatile var history: Map<String, Any?>? = null,
    val progress: AtomicReference<List<Map<String, Any>>> = AtomicReference(emptyList()),
    /** Д1 плана прямого хода: профиль детерминированного режима поимённо
     *  (счётный предел, поток ходов, зерно, порядок сущностей, режим
     *  среды); null — обычный прогон по стенным часам. */
    val profile: Map<String, Any?>? = null,
    /** K-16: неизменяемый отпечаток принятого внешнего каталога пейринга
     *  (SHA-256 байт); у ростеринга на пейринге — унаследован от прогона
     *  pairingRunId. Читатель сверяет, на каком каталоге построен ростер. */
    val catalogSha256: String? = null,
    /** K-32/K-8: известная часть цены ночёвок по Контракту Б (отчёт
     *  RosterLayoverCost v2), считается один раз после счёта. */
    @Volatile var layoverCost: Map<String, Any?>? = null,
    /** K-15-a: показание непрерывной проверки окон отдыха по решению
     *  (version=1, unit=critical_cell, по сотрудникам expected / passed /
     *  violated / unknown) — тот же горизонт и нарезка, что у ограничения;
     *  null — планов формы в пакете нет. */
    @Volatile var rollingRestAudit: Map<String, Any?>? = null,
    /** Д1 (08.09.2026): ПО КАКОМУ пределу прогон остановился — счётному
     *  (воспроизводимо) либо страховочному по стенным часам; форма —
     *  [RunTermination.reading]. null — прогон ещё не досчитал. */
    @Volatile var termination: Map<String, Any?>? = null,
    /** ЗАПРОШЕНА ли отмена прогона ручкой DELETE /schedules/run/{runId}.
     *  Именно запрошена: дошла ли она до решателя и остановила ли его —
     *  говорит `termination.kind` (правка по ревью 08.09.2026: прежде
     *  этот флаг сам назначал вид остановки и лгал, когда отмена
     *  терялась). */
    @Volatile var cancelRequested: Boolean = false,
    /** ГДЕ прогон находится внутри состояния «считает»: в очереди пула,
     *  в сборке решателя, в счёте, в отчётах. Ручка отмены отвечает по
     *  этому полю разными словами (находка ревью 08.09.2026: прежде три
     *  разных положения назывались одинаково «решатель ещё собирается»). */
    @Volatile var stage: String = PackagedRuns.STAGE_QUEUED,
) {
    fun statusSnapshot(): Map<String, Any?> = linkedMapOf(
        "runId" to runId,
        "state" to state,
        "failure" to failure,
        "score" to score,
        "packageFingerprint" to packageFingerprint,
        "catalogSha256" to catalogSha256,
        "mode" to mode,
        "task" to task,
        "pairingRunId" to pairingRunId,
        "chains" to chains,
        "startedAtMs" to startedAtMs,
        "profile" to profile,
        "progress" to progress.get(),
        // Шаг 4 (05.09.2026): набор ходов, итог раздачи по цепочкам и
        // разложение счёта начального решения — поимённо в статусе.
        "moves" to moves,
        "seeded" to seeded,
        "seedReading" to seedReading,
        "seedBreakdown" to seedBreakdown,
        "deadheads" to deadheads,
        "history" to history,
        "layoverCost" to layoverCost,
        "rollingRestAudit" to rollingRestAudit,
        // Д1 (08.09.2026): признак остановки — счётный предел либо
        // страховка по стенным часам, словами и числами.
        "termination" to termination,
        "cancelRequested" to cancelRequested,
        "stage" to stage,
    )
}

object PackagedRuns {

    const val ST_RUNNING = "считает"
    const val ST_DONE = "готов"
    const val ST_FAILED = "упал"

    /** Положения прогона ВНУТРИ состояния «считает» (08.09.2026, ревью):
     *  очередь пула — сборка решателя — счёт — отчёты. По ним ручка
     *  отмены отвечает разными словами, а не одним «решатель ещё
     *  собирается» на три разных случая. */
    const val STAGE_QUEUED = "в очереди пула"
    const val STAGE_BUILDING = "сборка решателя"
    const val STAGE_SOLVING = "счёт"
    const val STAGE_REPORTS = "отчёты после счёта"
    const val STAGE_FINISHED = "закончен"

    /**
     * Фиксация утверждённого пейринга: цепочки прогона [pairingRunId]
     * становятся метками chainId мест задачи. Гейты fail-closed: прогон
     * обязан существовать в реестре, быть «готов», быть задачей pairing и
     * считан ПО ТОМУ ЖЕ пакету (отпечаток байт в байт); решение обязано
     * покрывать хотя бы одно место набора. Возвращает число цепочек.
     */
    private fun fixPairing(problem: FlightCrewSchedule, pairingRunId: String,
                           task: String, pkg: LoadedTaskPackage): Int {
        if (task != "rostering") {
            throw TaskModeRefused(
                "pairingRunId применим только к задаче rostering: ростеринг " +
                "на утверждённом пейринге; задача прогона — " + task)
        }
        val prev = runs[pairingRunId]
            ?: throw TaskModeRefused(
                "прогон пейринга " + pairingRunId + " не найден в реестре " +
                "решателя: фиксировать нечего")
        if (prev.state != ST_DONE) {
            throw TaskModeRefused(
                "прогон пейринга " + pairingRunId + " в состоянии «" +
                prev.state + "», а фиксация требует «" + ST_DONE + "»")
        }
        if (prev.task != "pairing") {
            throw TaskModeRefused(
                "прогон " + pairingRunId + " — задача " + prev.task +
                ", а фиксация цепочек берёт только прогон задачи pairing")
        }
        if (prev.packageFingerprint != pkg.fingerprint) {
            throw TaskModeRefused(
                "пейринг " + pairingRunId + " считан по другому пакету " +
                "(отпечатки различаются): фиксация цепочек чужого пакета — " +
                "то же «число ниоткуда», нужен пейринг этого пакета")
        }
        val chainBySeat = HashMap<String, String>()
        val chainNames = HashSet<String>()
        if (prev.chainBySeat != null) {
            // Г-П4: каталог ortools-построителя — метки готовы при
            // регистрации, решения у такой записи нет по построению.
            chainBySeat.putAll(prev.chainBySeat)
        } else {
            val sol = prev.solution ?: throw TaskModeRefused(
                "прогон пейринга " + pairingRunId + " готов, но решения не несёт " +
                "— фиксировать нечего")
            for (fa in sol.flightAssignments) {
                val emp = fa.employee ?: continue
                if (fa.deadhead) continue
                val chain = "пейринг:" + emp.id
                chainBySeat[fa.id] = chain
            }
        }
        var stamped = 0
        for (fa in problem.flightAssignments) {
            chainBySeat[fa.id]?.let { c ->
                fa.chainId = c
                chainNames.add(c)
                stamped++
            }
        }
        // Шаг 4 (05.09.2026): перегонные кресла цепочки получают её метку —
        // по одному свободному креслу на каждый перегон цепочки каждого
        // слоя; кресел не хватило — маршрут на перегоне рвётся, и об этом
        // говорит журнал (порог max_deadheads_per_flight пакета).
        var dhUnplaced = 0
        prev.dhByChain?.let { dhMap ->
            for (c in chainNames) {
                for (fn in dhMap[c] ?: continue) {
                    val seat = problem.flightAssignments.firstOrNull {
                        it.deadhead && it.chainId == null && it.flight?.flightNumber == fn
                    }
                    if (seat == null) { dhUnplaced++; continue }
                    seat.chainId = c
                }
            }
        }
        if (dhUnplaced > 0) {
            log.warning("перегонам цепочек не хватило кресел: " + dhUnplaced +
                " (порог max_deadheads_per_flight) — маршрут этих цепочек на перегоне разомкнут")
        }
        if (stamped == 0) {
            throw TaskModeRefused(
                "решение пейринга " + pairingRunId + " не покрывает ни одного " +
                "места набора: фиксировать нечего")
        }
        return chainNames.size
    }

    /** Прогонов в реестре; старейшие ЗАВЕРШЁННЫЕ вытесняются. */
    private const val MAX_RUNS = 32

    private val log: Logger = Logger.getLogger(PackagedRuns::class.java.name)
    private val runs = ConcurrentHashMap<String, RunRecord>()

    /** Решатели СЧИТАЮЩИХ прогонов — по ним работает ручка отмены
     *  (08.09.2026). Запись появляется перед `solve` и снимается сразу
     *  после него: карта не держит завершённые прогоны. */
    private val solvers = ConcurrentHashMap<String,
        ai.timefold.solver.core.api.solver.Solver<FlightCrewSchedule>>()

    fun get(runId: String): RunRecord? = runs[runId]

    internal fun clearForTests() { runs.clear(); solvers.clear() }

    /** Считает ли решатель ЭТОГО прогона прямо сейчас (`Solver.isSolving`).
     *  Нужна ручке отмены и судьям: «прогон в состоянии считает» и
     *  «решатель действительно внутри solve» — разные вещи (между ними
     *  очередь пула, сборка фабрики и отчёты после счёта). */
    fun isSolving(runId: String): Boolean =
        solvers[runId]?.let { runCatching { it.isSolving() }.getOrDefault(false) } ?: false

    /**
     * ОТМЕНА считающего прогона (08.09.2026): освобождает поток пула
     * прогонов без перезапуска службы. Ответ — состояние СЛОВАМИ, а не
     * молчаливое «ок»: отменять завершённый нечего, и это говорится.
     * null — прогона в реестре нет.
     *
     * **Правка по ревью 08.09.2026.** Прежний KDoc обещал «отмена до
     * сборки решателя исполняется, как только решатель появится (гонки
     * нет по построению)» — это было НЕВЕРНО: `DefaultSolver.solve`
     * начинается с `outerSolvingStarted`, а тот зовёт
     * `BasicPlumbingTermination.resetTerminateEarly()`, поэтому любой
     * `terminateEarly()`, сделанный ДО `solve`, стирался. Теперь:
     *  - прогон, отменённый ДО старта счёта, вообще не считает — задача
     *    исполнителя закрывает запись сразу (поток пула свободен);
     *  - решателю остановка передаётся из `phaseStarted` (это ПОСЛЕ
     *    сброса), поэтому зазор «решатель собран, `solve` не начат»
     *    больше ничего не теряет;
     *  - `solverReached` говорит правду: решатель ВНУТРИ `solve`
     *    (`isSolving`) и остановка на нём стоит (`isTerminateEarly`).
     *
     * Поля ответа: `cancelled` — принята ли ЗАЯВКА на остановку;
     * `solverReached` — дошла ли она до считающего решателя; `stage` —
     * где прогон был в этот миг; `words` — то же самое по-русски.
     */
    fun cancel(runId: String): Map<String, Any?>? {
        val r = runs[runId] ?: return null
        if (r.state != ST_RUNNING) {
            return linkedMapOf(
                "runId" to runId,
                "state" to r.state,
                "stage" to r.stage,
                "cancelled" to false,
                "solverReached" to false,
                "words" to ("прогон в состоянии «" + r.state +
                    "» — останавливать нечего"))
        }
        // Положение снимается ОДИН раз: прогон живёт своим потоком, и
        // ответ обязан говорить о том положении, по которому принято
        // решение, а не о том, в какое прогон успел перейти, пока ответ
        // собирался.
        val stage = r.stage
        val solver = solvers[runId]
        // Счёт уже позади — идут отчёты, прогон закончен, либо `solve`
        // только что вернулся (решателя в карте уже нет, а положение ещё
        // не переписано). Останавливать нечего, и флаг запроса тут не
        // ставится, чтобы статус не говорил «отмена запрошена» о прогоне,
        // которого никто не останавливал.
        if (stage == STAGE_REPORTS || stage == STAGE_FINISHED ||
            (stage == STAGE_SOLVING && solver == null)) {
            return linkedMapOf(
                "runId" to runId,
                "state" to r.state,
                "stage" to stage,
                "cancelled" to false,
                "solverReached" to false,
                "words" to ("счёт прогона уже закончен (" + stage +
                    ") — останавливать нечего; результат вот-вот появится"))
        }
        val repeated = r.cancelRequested
        r.cancelRequested = true
        val solving = solver != null && runCatching { solver.isSolving() }.getOrDefault(false)
        // Остановку ставим ТОЛЬКО считающему решателю: до solve она
        // стирается сбросом внутри solve (см. KDoc выше), а её исполнение
        // держит флаг записи и слушатель phaseStarted.
        val reached = solving && runCatching {
            solver.terminateEarly()
            solver.isTerminateEarly()
        }.getOrDefault(false)
        val words = when {
            reached && repeated ->
                "остановка уже была запрошена; решатель считает и " +
                "останавливается — прогон завершится лучшим решением на " +
                "момент отмены"
            reached ->
                "остановка запрошена; решатель считает и получил её — прогон " +
                "завершится лучшим решением на момент отмены"
            stage == STAGE_QUEUED ->
                "прогон стоит в очереди пула и считать ещё не начал: отмена " +
                "запомнена, счёт не начнётся вовсе"
            else ->
                // Сюда попадают сборка решателя и «решатель собран, solve
                // ещё не начат»: то и другое закрывает слушатель
                // phaseStarted, повторять отмену не нужно.
                "решатель ещё не считает: отмена запомнена и будет исполнена " +
                "на старте первой фазы поиска"
        }
        log.info("PackagedRuns: отмена прогона " + runId + " (" + stage + ") — " +
            (if (reached) "решателю сказано остановиться" else "отмена запомнена записью"))
        return linkedMapOf(
            "runId" to runId,
            "state" to r.state,
            "stage" to stage,
            "cancelled" to true,
            "solverReached" to reached,
            "words" to words)
    }

    /**
     * Принять пакет и стартовать прогон. Все гейты постановки — ЗДЕСЬ,
     * синхронно (отказ уходит вызывающему словами решателя до старта
     * счёта); сам счёт — в исполнителе, статус по runId.
     *
     * Гейт двусмысленности (Ш-2 п.5): наличие процессного пакета
     * (env-переменная или тестовая подмена) при запросном прогоне — отказ
     * по факту наличия, до чтения содержимого любого из двух.
     */
    /** Белый список вердиктов покрытия внешнего каталога — тот же, что
     *  у этапа 2 одной из линий: ослабленное покрытие не регистрируется. */
    private val CATALOG_VERDICTS = setOf("OK", "INFEASIBLE_INPUT", "INCOMPLETE_SEARCH")

    /**
     * Г-П4 (директива владельца 2026-08-26): регистрация ВНЕШНЕГО каталога
     * пейринга (ortools-построитель) записью задачи pairing в реестре.
     * Гейты fail-closed: отпечаток каталога байт в байт; вердикт покрытия
     * из белого списка; каталог — разбиение (рейс в двух цепочках — отказ);
     * пустых цепочек нет. Цепочки штампуются ПО СЛОЯМ мест (роль в имени):
     * целостность держит каждый слой отдельно. Запись сразу «готов»,
     * решения не несёт по построению — фиксация идёт по chainBySeat.
     */
    fun registerPairingCatalog(problem: FlightCrewSchedule,
                               packageZip: ByteArray,
                               catalogBytes: ByteArray,
                               declaredSha256: String,
                               acceptUnplaceable: Boolean = false): RunRecord {
        val actual = java.security.MessageDigest.getInstance("SHA-256")
            .digest(catalogBytes).joinToString("") { "%02x".format(it) }
        if (!actual.equals(declaredSha256.trim(), ignoreCase = true)) {
            throw TaskModeRefused(
                "отпечаток каталога расходится с заявленным (получен " + actual +
                "): каталог подменён между построением и регистрацией — отказ")
        }
        val pkg = TaskPackage.readBytes(packageZip)
        val root = com.fasterxml.jackson.databind.ObjectMapper().readTree(catalogBytes)
        val verdict = root.path("coverage").path("verdict").asText("")
        if (verdict !in CATALOG_VERDICTS) {
            throw TaskModeRefused(
                "каталог несёт вердикт покрытия «" + verdict + "» — регистрируются " +
                "только " + CATALOG_VERDICTS.joinToString(", ") +
                "; ослабленный каталог на ростеринг не идёт (Г-П4)")
        }
        val selected = root.path("selected")
        if (!selected.isArray || selected.isEmpty) {
            throw TaskModeRefused("каталог без цепочек (selected пуст) — фиксировать нечего")
        }
        val chainOfFlight = HashMap<String, Int>()
        val dhOfChain = HashMap<Int, List<String>>()
        for ((k, ch) in selected.withIndex()) {
            val fns = ch.path("active_fns").takeIf { it.isArray && !it.isEmpty }
                ?: ch.path("fns")
            if (!fns.isArray || fns.isEmpty) {
                throw TaskModeRefused("цепочка " + k + " каталога пуста — каталог битый")
            }
            // Перегоны цепочки (поле dh каталога) — часть её единицы (шаг 4).
            val dh = ch.path("dh")
            if (dh.isArray && !dh.isEmpty) {
                dhOfChain[k] = dh.map { it.asText() }.filter { it.isNotBlank() }
            }
            for (f in fns) {
                val fn = f.asText()
                chainOfFlight.put(fn, k)?.let { other ->
                    if (other != k) throw TaskModeRefused(
                        "рейс " + fn + " в двух цепочках каталога (" + other + " и " + k +
                        ") — каталог не разбиение, регистрировать нельзя")
                }
            }
        }
        // Гейт размещаемости (Г-П4, п. 8 постановки): дешёвая арифметика
        // на шве пейринг -> ростеринг. Каталог, который физически не
        // раздать людям пакета, отклоняется ДО того, как ростеринг
        // потратит на него часы. Нормы правил (выходные и т.п.) сюда НЕ
        // зашиваются — они поедут блоком критериев пакета (п. 10); здесь
        // только безусловная арифметика: (а) пиковая одновременность
        // «цепочка + её отдых» против численности роли — необходимое
        // условие ЛЮБОЙ раздачи; (б) цепочко-дни против физического
        // потолка «численность × дни горизонта роли».
        val placement = StringBuilder()
        var placementChecked = true
        run {
            class RoleNeed {
                var days = 0L
                val events = ArrayList<LongArray>()
                val daysUnion = HashSet<Long>()
            }
            val needs = HashMap<String, RoleNeed>()
            for (ch in selected) {
                val wins = ch.path("busyWindows")
                val seats = ch.path("seats")
                if (!wins.isArray || wins.isEmpty || !seats.isArray || seats.isEmpty) {
                    placementChecked = false; break
                }
                var s = Long.MAX_VALUE; var e = Long.MIN_VALUE
                val days = HashSet<Long>()
                for (w in wins) {
                    val a = w.path(0).asLong(); val b = w.path(1).asLong()
                    s = minOf(s, a); e = maxOf(e, b)
                    for (d in (a / 1440)..(b / 1440)) days.add(d)
                }
                val rest = ch.path("minRestAfterMin").asLong(0)
                for (seat in seats) {
                    val n = needs.getOrPut(seat.asText()) { RoleNeed() }
                    n.days += days.size
                    n.daysUnion.addAll(days)
                    n.events.add(longArrayOf(s, 1))
                    n.events.add(longArrayOf(e + rest, -1))
                }
            }
            if (placementChecked) {
                val bad = ArrayList<String>()
                for ((role, n) in needs.toSortedMap()) {
                    val head = problem.employees.count { it.hasSkill(role) }
                    // при равном времени сперва освобождение: отдых,
                    // истёкший ровно к старту следующей, не конфликт
                    n.events.sortWith(compareBy({ it[0] }, { it[1] }))
                    var cur = 0; var peak = 0
                    for (ev in n.events) { cur += ev[1].toInt(); if (cur > peak) peak = cur }
                    val cap = head.toLong() * n.daysUnion.size
                    placement.append(role).append(": пик ").append(peak)
                        .append(" при людях ").append(head)
                        .append(", цепочко-дни ").append(n.days)
                        .append(" при потолке ").append(cap).append("; ")
                    if (peak > head) bad.add(
                        "роль " + role + ": пик одновременных «цепочка+отдых» " +
                        peak + " при численности " + head)
                    if (n.days > cap) bad.add(
                        "роль " + role + ": цепочко-дни " + n.days +
                        " при физическом потолке " + cap +
                        " (численность × дни горизонта)")
                }
                if (bad.isNotEmpty() && !acceptUnplaceable) {
                    throw TaskModeRefused(
                        "каталог неразмещаем в штат пакета: " +
                        bad.joinToString("; ") +
                        ". Это свойство каталога, не раздачи: пересоберите " +
                        "пейринг с критерием бережливости к человеко-дням " +
                        "либо повторите с acceptUnplaceable=true — явное " +
                        "решение оператора")
                }
            } else {
                placement.append("не проверена: каталог без busyWindows/seats")
            }
        }
        val chainBySeat = HashMap<String, String>()
        val chains = HashSet<String>()
        val dhByChain = HashMap<String, List<String>>()
        for (fa in problem.flightAssignments) {
            if (fa.deadhead) continue
            val fn = fa.flight?.flightNumber ?: continue
            val k = chainOfFlight[fn] ?: continue
            // K-17: слой — МЕСТО, не роль: два взаимозаменяемых места Pilot
            // одного рейса остаются двумя независимо комплектуемыми
            // цепочками даже при одинаковых метках (прежде одна метка на
            // роль требовала одного человека на два места — неразрешимо).
            val name = "каталог:" + k + ":slot:" + fa.indexInFlight
            chainBySeat[fa.id] = name
            chains.add(name)
            dhOfChain[k]?.let { dhByChain[name] = it }
        }
        if (chainBySeat.isEmpty()) {
            throw TaskModeRefused(
                "каталог не покрывает ни одного места набора — чужие рейсы либо чужой мир")
        }
        val record = RunRecord(
            runId = UUID.randomUUID().toString(),
            startedAtMs = System.currentTimeMillis(),
            packageFingerprint = pkg.fingerprint,
            mode = "external-catalog",
            task = "pairing",
            state = ST_DONE,
            score = "каталог: цепочек " + chains.size + ", вердикт " + verdict +
                "; размещаемость: " + placement.toString().trim().trimEnd(';'),
            pkg = pkg,
            chains = chains.size,
            chainBySeat = chainBySeat,
            dhByChain = dhByChain.takeIf { it.isNotEmpty() },
            catalogSha256 = actual,
            // Запись каталога решателем не считается: она «закончена» с
            // самого начала — очереди пула у неё не бывает.
            stage = STAGE_FINISHED,
        )
        runs[record.runId] = record
        return record
    }

    fun start(problem: FlightCrewSchedule, packageZip: ByteArray,
              mode: String?, objectiveId: String?,
              cfg: SolverConfig, executor: ExecutorService,
              task: String = "rostering",
              pairingRunId: String? = null,
              profile: Map<String, Any?>? = null,
              moves: String? = null,
              deadheadSeats: Int? = null): RunRecord {
        if (profile != null) {
            // Д1: порядок сущностей по идентификаторам — порядок входа
            // (JSON клиента) не должен влиять на траекторию поиска.
            problem.airports = problem.airports.sortedBy { it.code }.toMutableList()
            problem.employees = problem.employees.sortedBy { it.id }.toMutableList()
            problem.flights = problem.flights.sortedBy { it.flightNumber }.toMutableList()
            problem.flightAssignments =
                problem.flightAssignments.sortedBy { it.id }.toMutableList()
        }
        if (TaskPackage.processPackagePresent()) {
            throw TaskModeRefused(
                "двусмысленность источников пакета: процессу задан пакет " +
                "(CREW_TASK_PACKAGE либо тестовая подмена), и одновременно пакет " +
                "прислан запросом — какой действует, решатель не угадывает; " +
                "уберите один из двух")
        }
        val pkg = TaskPackage.readBytes(packageZip)
        // Допущение нулевой истории (1.17.2) — ДО fit: при признаке раздела
        // assume_zero_when_missing сотрудники без записи получают явную
        // нулевую запись на срез, и месяц среза входит в универсум формы
        // «выходных в месяц» (иначе универсум пуст и отказ «ПУСТО»).
        val historyReading = HistoryAssumption.applyZero(problem)
        // Задача прогона — параметром: ростеринг (умолчание) либо пейринг.
        // Неизвестное имя или задача вне пакета отвергаются гейтом
        // allowed_modes своей задачи — словами пакета, не догадкой.
        val taskMode = TaskModes.resolve(mode, pkg, task = task,
            objectiveId = objectiveId, rulesProfile = null)
        val coverage = RunThresholds.checkFacts(problem, taskMode.thresholds.applied)
        val fit = taskMode.logic?.let {
            LogicCompiler.fit(problem, taskMode.thresholds.applied, it, "run")
        }
        fit?.let { LogicCompiler.refuseIfNeeded(it) }
        // Перегонные кресла — из порога пакета, ПОСЛЕ фактов и fit:
        // состав мест и «предметов рассмотрено» — предметы НАБОРА,
        // кресла решателя в них не входят.
        // Решение владельца 05.09.2026 (единый план, раздел 3): в пакетном
        // прогоне перегоны разрешены и без правила в ВМП; кресел на рейс —
        // порог пакета, иначе явный параметр, иначе состав экипажа.
        // Требование простоты Н0 (владелец 05.09.2026, вечер): набор
        // сбалансирован, позиционирование не нужно — умолчание прежнее
        // (без порога и без параметра перегонных кресел нет, счёт
        // байт-идентичен), перегоны включаются порогом пакета либо явным
        // параметром deadheadSeats; показание называет основание.
        val dhThreshold = taskMode.thresholds.applied["max_deadheads_per_flight"]?.value
        val dhSeatsPerFlight = dhThreshold ?: deadheadSeats ?: 0
        val dhAdded = DeadheadSeats.ensure(problem, taskMode.thresholds.applied,
            explicitSeats = deadheadSeats, allowByDefault = false)
        val deadheadReading = linkedMapOf<String, Any?>(
            "allowed" to (dhSeatsPerFlight > 0),
            "basis" to when {
                dhThreshold != null -> "порог пакета max_deadheads_per_flight (семья deadhead_rules ВМП)"
                deadheadSeats != null -> "явный параметр прогона deadheadSeats; правила о перегонах в ВМП нет"
                else -> "правила о перегонах в ВМП нет и параметр прогона не задан — перегонных кресел нет (простой набор Н0 сбалансирован, позиционирование не нужно)"
            },
            "seatsPerFlight" to dhSeatsPerFlight,
            "seatsAdded" to dhAdded,
            "used" to null)

        // K-6/K-32/K-30-a: веса и пороги отдыха прогона — в решение, границы
        // учёта — сотрудникам, СИНХРОННО и ДО фиксации, проверки тёплого
        // старта и раздачи цепочек: раздача и ходы по цепочкам судят
        // порогами ПРОГОНА (снимок ChainSearchRules пакетного окна:
        // стыковка, отдых, мёртвая зона, размах, флот), а не литералами
        // профиля; негодное window_horizon отвергается словами (bounds())
        // до раздачи, не в исполнителе. Отчёт цены ночёвок после счёта
        // считается по тем же порогам.
        problem.layoverCostWeights = taskMode.effectiveWeights
        RosterLayoverCost.prepare(problem, taskMode.thresholds.applied, packaged = true)

        // Классический способ (директива владельца 2026-08-24): ростеринг
        // НА УТВЕРЖДЁННОМ ПЕЙРИНГЕ. Цепочки прогона пейринга фиксируются
        // метками chainId; гейты — синхронно, отказ словами до счёта.
        val chains = pairingRunId?.let { fixPairing(problem, it, task, pkg) } ?: 0
        // K-30 (7): присланное начальное решение, рвущее цепочку между
        // людьми, — отказ словами до счёта (тёплый старт цепочку не делит).
        if (chains > 0) {
            ChainSeeding.refuseSplitChains(problem)
            // K-FIX-R2 (ревью GPT-029): в режимах цепочек закрепления судятся
            // и якорят цепочку СИНХРОННО в каждом прогоне, не только при
            // раздаче: при moves=chain с тёплым стартом конструктива нет, а
            // фильтры одиночных ходов место цепочки не заполняют — цепочка
            // «пин + пусто», пропущенная refuseSplitChains как якоримая,
            // осталась бы неякорной и неремонтируемой. Конфликт пинов либо
            // непригодный закреплённый — отказ словами ДО любой мутации;
            // иначе пустые места цепочки получают человека пина. Повторный
            // вызов из раздачи (seed) идемпотентен.
            if (LargeMoves.chainMode(moves)) ChainSeeding.anchorPinnedChains(problem)
        }
        // K-16: отпечаток каталога наследуется от прогона пейринга.
        val selectedCatalogSha = pairingRunId?.let { runs[it]?.catalogSha256 }
        // Шаг 4 плана 05.09.2026 (moves=chain): начальное решение по
        // цепочкам — цепочки раздаются людям целиком до старта поиска;
        // только по явному параметру, умолчание не задето.
        var seeded: Int? = null
        var seedReading: Map<String, Any?>? = null
        if (LargeMoves.seedsChains(moves) && chains > 0) {
            // K-30-c: итог раздачи поимённо — fallback (непроверенный
            // кандидат) и unseeded (без пригодного) видны в статусе прогона.
            val result = ChainSeeding.seed(problem)
            seeded = result.seeded
            seedReading = result.reading()
            log.info("начальное решение по цепочкам: роздано " + seeded + " из " + chains +
                " (проверено по соседям " + result.checked + ", непроверенный fallback " +
                result.fallback.size + ", без пригодного " + result.unseeded.size + ")")
        }

        val record = RunRecord(
            runId = UUID.randomUUID().toString(),
            startedAtMs = System.currentTimeMillis(),
            packageFingerprint = pkg.fingerprint,
            mode = taskMode.mode,
            task = task,
            pairingRunId = pairingRunId,
            chains = chains,
            pkg = pkg,
            profile = profile,
            moves = moves?.trim()?.ifEmpty { null },
            seeded = seeded,
            seedReading = seedReading,
            deadheads = deadheadReading,
            history = historyReading,
            catalogSha256 = selectedCatalogSha)
        admit(record)

        executor.submit {
            try {
                // ОТМЕНА ДО СТАРТА (правка по ревью 08.09.2026). Прогон,
                // отменённый пока стоял в очереди пула, не считает вовсе:
                // ни фабрики, ни конструктива, ни поиска — поток пула
                // свободен сразу. Прежде отмена «запоминалась» и
                // исполнялась вызовом terminateEarly() ПЕРЕД solve, а
                // solve его стирал (DefaultSolver.outerSolvingStarted →
                // BasicPlumbingTermination.resetTerminateEarly), и прогон
                // считал до собственного предела.
                if (record.cancelRequested) {
                    record.stage = STAGE_FINISHED
                    record.solution = problem
                    record.termination = RunTermination.reading(
                        cfg.terminationConfig, record.profile != null,
                        searchSteps = 0, unimprovedStreak = 0,
                        elapsedMs = System.currentTimeMillis() - record.startedAtMs,
                        terminatedEarly = true, searchStarted = false)
                    record.state = ST_DONE
                    log.info("PackagedRuns: прогон " + record.runId +
                        " отменён до старта счёта — решатель не собирался, " +
                        "поток пула свободен")
                    return@submit
                }
                record.stage = STAGE_BUILDING
                // K-6/K-32: веса и пороги отдыха прогона — в решение, границы
                // учёта — сотрудникам, ДО создания директора счёта. Первый
                // (решающий) вызов — синхронно в start до раздачи (K-30-a);
                // повтор здесь идемпотентен: те же пороги, те же границы.
                NativeExecutionSupport.prepare(problem, taskMode.thresholds.applied,
                    taskMode.effectiveWeights, taskMode.weightOverrides, pkg.calendarWindows)
                // Показания — ИЗНУТРИ окна сборки: между закрытием окна и
                // съёмом больше нет зазора, чужая сборка не подменит числа.
                val (factory, readings) = RunThresholds.withRunModel(
                    taskMode.thresholds.applied, fit?.plans ?: emptyList(),
                    packaged = true) {
                    val f = SolverFactory.create<FlightCrewSchedule>(cfg)
                    f to LastProviderReadings.snapshotWithCodes()
                }
                val registered = if (fit == null) null else LogicCompiler.registeredIds(factory)
                record.report = TaskModes.withActualReadings(taskMode, readings?.readings,
                    readings?.codeReadings ?: emptyMap(), readings?.policyReadings ?: emptyMap())
                    .copy(coverage = coverage, logic = fit?.copy(registered = registered))
                if (record.seeded != null) {
                    // Шаг 4: разложение счёта НАЧАЛЬНОГО решения по цепочкам —
                    // до поиска, по весам пакета; видно в статусе прогона.
                    try {
                        // Тот же путь, что у ручки score-breakdown: директор
                        // счёта с учётом совпадений (analyze() — платная
                        // возможность Timefold, здесь не используется).
                        val sdf = (factory as ai.timefold.solver.core.impl.solver.DefaultSolverFactory<FlightCrewSchedule>)
                            .getScoreDirectorFactory<ai.timefold.solver.core.api.score.HardSoftScore>()
                        val director = sdf.createScoreDirectorBuilder()
                            .withConstraintMatchPolicy(
                                ai.timefold.solver.core.impl.score.constraint.ConstraintMatchPolicy.ENABLED)
                            .build()
                        val bd = LinkedHashMap<String, Any?>()
                        try {
                            director.setWorkingSolution(problem)
                            val score = director.calculateScore().raw()
                            for ((ref, total) in director.constraintMatchTotalMap) {
                                val sc = total.score
                                if (sc.hardScore() == 0L && sc.softScore() == 0L) continue
                                bd[ref.id().substringAfter('/')] = mapOf(
                                    "hard" to sc.hardScore(), "soft" to sc.softScore(),
                                    "matches" to total.constraintMatchCount)
                            }
                            bd["_score"] = score.toString()
                        } finally {
                            director.close()
                        }
                        record.seedBreakdown = bd
                    } catch (e: RuntimeException) {
                        record.seedBreakdown = mapOf("error" to (e.message ?: e.toString()))
                    }
                }
                val solver = factory.buildSolver()
                val t0 = System.currentTimeMillis()
                // Д1 (08.09.2026): считаем ЗАВЕРШЁННЫЕ шаги фазы поиска —
                // по ним потом видно, счётный предел остановил прогон или
                // страховка по стенным часам (без единого шага счётный
                // предел не судится вовсе).
                val steps = RunTermination.StepCounter()
                val abstractSolver = solver as?
                    ai.timefold.solver.core.impl.solver.AbstractSolver<FlightCrewSchedule>
                abstractSolver?.addPhaseLifecycleListener(steps)
                // Ручка отмены: пока прогон считает, его решатель адресуем
                // по runId. Отмена, попавшая в зазор «решатель собран —
                // solve не начат», исполняется НА СТАРТЕ ПЕРВОЙ ФАЗЫ:
                // solve начинается со сброса признака раннего завершения
                // (DefaultSolver.outerSolvingStarted →
                // BasicPlumbingTermination.resetTerminateEarly), поэтому
                // всё, сказанное решателю ДО solve, стирается, а
                // phaseStarted — уже после сброса.
                abstractSolver?.addPhaseLifecycleListener(
                    object : ai.timefold.solver.core.impl.phase.event
                    .PhaseLifecycleListenerAdapter<FlightCrewSchedule>() {
                        override fun phaseStarted(
                            phaseScope: ai.timefold.solver.core.impl.phase.scope
                            .AbstractPhaseScope<FlightCrewSchedule>,
                        ) {
                            if (record.cancelRequested) {
                                runCatching { solver.terminateEarly() }
                            }
                        }
                    })
                solvers[record.runId] = solver
                record.stage = STAGE_SOLVING
                solver.addEventListener { event: BestSolutionChangedEvent<FlightCrewSchedule> ->
                    val s = event.newBestSolution.score
                    if (s != null) {
                        record.progress.updateAndGet { prev ->
                            prev + mapOf(
                                "tMillis" to (System.currentTimeMillis() - t0),
                                "hard" to s.hardScore(),
                                "soft" to s.softScore())
                        }
                    }
                }
                // Факт остановки снимается В ТОТ ЖЕ МИГ, когда решатель
                // уходит из карты отмены: иначе DELETE, попавший между
                // концом solve и чтением признака, пометил бы отменённым
                // прогон, остановленный собственным пределом.
                var stoppedEarly = false
                val solved = try {
                    solver.solve(problem)
                } finally {
                    stoppedEarly = runCatching { solver.isTerminateEarly() }
                        .getOrDefault(false)
                    solvers.remove(record.runId)
                    record.stage = STAGE_REPORTS
                }
                // Признак остановки — сразу после счёта, до тяжёлых
                // отчётов: он про решатель, а не про них. Отмена берётся
                // ФАКТОМ решателя (isTerminateEarly после solve флаг не
                // сбрасывает), а не флагом запроса: отмена, не дошедшая до
                // решателя, прогон не остановила — и звать его отменённым
                // значило бы врать ровно там, где признак обязан быть
                // честным (находка ревью 08.09.2026).
                record.termination = RunTermination.reading(
                    cfg.terminationConfig, record.profile != null,
                    steps.searchSteps, steps.unimprovedStreak,
                    System.currentTimeMillis() - t0, stoppedEarly,
                    steps.searchStarted, steps.constructionSteps)
                record.solution = solved
                record.score = solved.score?.toString()
                record.deadheads = LinkedHashMap(deadheadReading).apply {
                    put("used", solved.flightAssignments.count { it.deadhead && it.employee != null })
                }
                // K-32/K-8: известная часть цены ночёвок — один раз после
                // счёта, в статус прогона (не на каждой сериализации решения).
                record.layoverCost = try {
                    RosterLayoverCost.report(solved)
                } catch (e: RuntimeException) {
                    mapOf("error" to (e.message ?: e.toString()))
                }
                // K-15-a: показание непрерывной проверки окон отдыха по
                // решению — тот же горизонт (LogicForms.horizonOf) и та же
                // нарезка смен (пороги отдыха прогона), что у ограничения;
                // в статус прогона и в coverage отчёта (рядом с checkFacts).
                record.rollingRestAudit = fit?.plans?.let { plans ->
                    val rules = solved.layoverDutyRules
                    try {
                        LogicForms.rollingRestAuditReading(solved, plans) { list ->
                            NormativeDuties.slice(list) { fa ->
                                rules.rest(fa.flight?.arrivalAirport?.code, fa.employee!!) }
                        }
                    } catch (e: RuntimeException) {
                        mapOf("error" to (e.message ?: e.toString()))
                    }
                }
                record.rollingRestAudit?.let { reading ->
                    record.report = record.report?.let {
                        it.copy(coverage = it.coverage + ("rolling_rest_audit" to reading)) }
                }
                record.stage = STAGE_FINISHED
                record.state = ST_DONE
                log.info("PackagedRuns: прогон " + record.runId + " готов, счёт " + record.score)
            } catch (e: Throwable) {
                record.failure = (e.message ?: e.javaClass.simpleName)
                record.stage = STAGE_FINISHED
                record.state = ST_FAILED
                log.warning("PackagedRuns: прогон " + record.runId + " упал: " + record.failure)
            }
        }
        return record
    }

    /** Впустить запись; при переполнении вытесняются старейшие завершённые. */
    private fun admit(record: RunRecord) {
        if (runs.size >= MAX_RUNS) {
            val evictable = runs.values
                .filter { it.state != ST_RUNNING }
                .sortedBy { it.startedAtMs }
            for (old in evictable) {
                if (runs.size < MAX_RUNS) break
                runs.remove(old.runId)
            }
            if (runs.size >= MAX_RUNS) {
                throw TaskModeRefused(
                    "реестр прогонов полон (" + MAX_RUNS + " считающих): новый прогон " +
                    "не принят; дождитесь завершения либо остановите лишние")
            }
        }
        runs[record.runId] = record
    }
}
