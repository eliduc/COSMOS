package org.acme.flighcrewscheduling.rest

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolutionManager
import ai.timefold.solver.core.api.solver.Solver
import ai.timefold.solver.core.api.solver.SolverConfigOverride
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.api.solver.SolverManager
import ai.timefold.solver.core.api.solver.SolverStatus
import ai.timefold.solver.core.api.solver.event.BestSolutionChangedEvent
import ai.timefold.solver.core.config.constructionheuristic.ConstructionHeuristicPhaseConfig
import ai.timefold.solver.core.config.constructionheuristic.ConstructionHeuristicType
import ai.timefold.solver.core.config.heuristic.selector.entity.EntitySorterManner
import ai.timefold.solver.core.config.localsearch.LocalSearchPhaseConfig
import ai.timefold.solver.core.config.localsearch.decider.acceptor.AcceptorType
import ai.timefold.solver.core.config.localsearch.decider.acceptor.LocalSearchAcceptorConfig
import ai.timefold.solver.core.config.score.director.ScoreDirectorFactoryConfig
import ai.timefold.solver.core.config.solver.PreviewFeature
import ai.timefold.solver.core.config.solver.SolverConfig
import ai.timefold.solver.core.config.solver.termination.TerminationConfig
import com.fasterxml.jackson.databind.ObjectMapper
import net.openhft.affinity.AffinityLock
import jakarta.inject.Inject
import jakarta.inject.Named
import jakarta.ws.rs.Consumes
import jakarta.ws.rs.DELETE
import jakarta.ws.rs.GET
import jakarta.ws.rs.POST
import jakarta.ws.rs.Path
import jakarta.ws.rs.PathParam
import jakarta.ws.rs.Produces
import jakarta.ws.rs.QueryParam
import jakarta.ws.rs.core.MediaType
import jakarta.ws.rs.core.Response
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.rest.exception.ScheduleSolverException
import org.acme.flighcrewscheduling.solver.FlightCrewSchedulingConstraintProvider
import org.acme.flighcrewscheduling.solver.GeradConstraintProvider
import org.acme.flighcrewscheduling.solver.GeradPhase2ConstraintProvider
import org.acme.flighcrewscheduling.solver.GeradPhase3ConstraintProvider
import org.acme.flighcrewscheduling.solver.GeradPairingRosterConstraintProvider
import org.acme.flighcrewscheduling.solver.LargeMoves
import org.acme.flighcrewscheduling.solver.LastRun
import org.acme.flighcrewscheduling.solver.DeadheadSeats
import org.acme.flighcrewscheduling.solver.LogicCompiler
import org.acme.flighcrewscheduling.solver.LogicReport
import org.acme.flighcrewscheduling.solver.NativeExecutionSupport
import org.acme.flighcrewscheduling.solver.PackagedRuns
import org.acme.flighcrewscheduling.solver.TaskModeRefused
import org.acme.flighcrewscheduling.solver.ResourceRules
import org.acme.flighcrewscheduling.solver.RunThresholds
import org.acme.flighcrewscheduling.solver.RulesConfig
import org.acme.flighcrewscheduling.solver.TaskModes
import org.acme.flighcrewscheduling.solver.TaskPackage
import org.acme.flighcrewscheduling.solver.TaskPackageError
import org.slf4j.LoggerFactory
import java.time.Duration
import java.time.LocalDateTime
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

@Path("schedules")
class FlightCrewSchedulingResource @Inject constructor(
    // Named-solver bean: with two @PlanningSolution classes on the
    // classpath every solver must be named (see application.properties).
    // The extension exposes only SolverManager as a named bean, so the
    // config template and SolutionManager are constructed below from the
    // same solverConfig.xml (+ the same defaults the properties set).
    @Named("crew")
    private val solverManager: SolverManager<FlightCrewSchedule>,
    // Wired only to satisfy the named-solver "gerad" registration; per-
    // request GERAD configs are built programmatically (rules=GERAD param).
    @Suppress("unused")
    @Named("gerad")
    private val geradSolverManager: SolverManager<FlightCrewSchedule>,
    // Same for the phase-2 personalization profile (rules=GERAD2).
    @Suppress("unused")
    @Named("gerad2")
    private val geradPhase2SolverManager: SolverManager<FlightCrewSchedule>,
    // Same for the stage-2 pairing-roster profile (rules=GERAD2P).
    @Suppress("unused")
    @Named("gerad2p")
    private val geradPairingRosterSolverManager: SolverManager<FlightCrewSchedule>,
    // Same for the phase-3 integrated-objective profile (rules=GERAD3).
    @Suppress("unused")
    @Named("gerad3")
    private val geradPhase3SolverManager: SolverManager<FlightCrewSchedule>,
    private val objectMapper: ObjectMapper,
) {
    /** Template config — identical content to the named "crew" solver:
     *  same solverConfig.xml, same seed/environment defaults. Cloned by
     *  buildSolverConfig for every island/single solve. */
    private val templateSolverConfig: SolverConfig =
        SolverConfig.createFromXmlResource("solverConfig.xml").apply {
            randomSeed = 0
            environmentMode = ai.timefold.solver.core.config.solver.EnvironmentMode.NO_ASSERT
        }

    private val solutionManager: SolutionManager<FlightCrewSchedule, HardSoftScore> =
        SolutionManager.create(SolverFactory.create(templateSolverConfig))

    private val log = LoggerFactory.getLogger(javaClass)
    private val jobs = ConcurrentHashMap<String, Job>()
    private val maxJobsCacheSize = 2

    /** Lock used while updating ``jobs[jobId]`` from concurrent island solvers
     *  in comprehensive mode, so two acceptors don't race on the best-so-far. */
    private val comprehensiveLocks = ConcurrentHashMap<String, Any>()

    /** Solvers belonging to currently running comprehensive jobs. Lets DELETE
     *  /schedules/{jobId} terminate every island in one call. */
    private val comprehensiveSolvers = ConcurrentHashMap<String, MutableList<Solver<FlightCrewSchedule>>>()

    /** Comprehensive jobs cancelled via DELETE /schedules/{jobId}.
     *  ``terminateEarly()`` only stops the solvers that are CURRENTLY
     *  running; interval-based runners (island migration) additionally check
     *  this set before starting the next interval, so a cancelled job
     *  finishes with its current global best instead of launching more
     *  solves. Cleaned up when the comprehensive job thread completes. */
    private val cancelledJobs: MutableSet<String> = ConcurrentHashMap.newKeySet()

    /** Per-island best score (string form) for live monitoring.
     *  Outer key = jobId; inner key = acceptor name (LA/SA/TABU/GD).
     *  Updated on every BestSolutionChangedEvent regardless of whether the
     *  candidate becomes the global best, so the client can see all four
     *  trajectories independently. Cleared when ``cleanJobs()`` evicts the
     *  parent job. */
    private val comprehensiveIslandScores = ConcurrentHashMap<String, ConcurrentHashMap<String, String>>()

    /** All four acceptors we run in parallel during comprehensive search.
     *  Order is fixed so logs/labels stay aligned. */
    private val COMPREHENSIVE_ACCEPTORS = listOf("LA", "SA", "TABU", "GD")

    companion object {
        /**
         * K-7-a (единая версия 06.09.2026): конфигурация ПАКЕТНОГО прогона
         * поверх базовой [base] (клон шаблона с фазами и стенным пределом):
         * зерно действует и вне детерминированного режима (прежде молча
         * бралось randomSeed=0 шаблона — K-7); ранняя остановка «N секунд
         * без улучшения» — явным параметром; детерминированный режим (Д1
         * плана прямого хода) — счётный предел ГЛАВНЫМ, один поток ходов,
         * зерно seed ?: 0, режим среды NO_ASSERT (Timefold 2.0:
         * воспроизводимы все режимы, кроме NON_REPRODUCIBLE). Без зерна
         * остаётся зерно шаблона. Меняет и возвращает [base].
         *
         * Правка 08.09.2026 (находка живого стенда, [RunTermination]):
         * счётный предел БОЛЬШЕ НЕ ЗАМЕЩАЕТ стенные часы целиком — рядом
         * с ним в той же ИЛИ-составной остановке остаётся СТРАХОВОЧНЫЙ
         * предел стенных часов ([deterministicSafety]). Прежде на мире без
         * исполнимого хода счётный предел не наступал никогда (Timefold
         * судит его только между завершёнными шагами), стенных часов в
         * остановке не оставалось, и прогон занимал поток пула прогонов
         * навсегда. На здоровом мире счётный предел наступает первым, и
         * страховка не срабатывает: воспроизводимость не задета.
         *
         * Правка по ревью 08.09.2026: число страховки берётся из
         * [spentLimitSeconds] — ПРЕДЕЛА САМОГО ЗАПРОСА, а не из базовой
         * остановки. Базовую всегда заполняет `buildSolverConfig`
         * (`spentLimitSeconds ?: 30`), поэтому «запрос предела не назвал»
         * по ней не отличить от «запрос назвал 30», и объявленный
         * руководством потолок [D1_DEFAULT_SAFETY_SECONDS] через ручку был
         * недостижим.
         */
        fun packagedSolverConfig(base: SolverConfig, seed: Long?, deterministic: Boolean?,
                                 stepLimit: Int? = null, unimprovedStepLimit: Int? = null,
                                 unimprovedSpentLimitSeconds: Int? = null,
                                 spentLimitSeconds: Int? = null): SolverConfig {
            if (seed != null) base.randomSeed = seed
            if ((unimprovedSpentLimitSeconds ?: 0) > 0) {
                base.terminationConfig = (base.terminationConfig
                    ?: TerminationConfig()).apply {
                    unimprovedSpentLimit =
                        Duration.ofSeconds(unimprovedSpentLimitSeconds!!.toLong())
                }
            }
            if (deterministic == true) {
                val steps = stepLimit?.takeIf { it > 0 }
                val unimproved = unimprovedStepLimit?.takeIf { it > 0 }
                if (steps == null && unimproved == null) {
                    throw TaskModeRefused(
                        "детерминированный режим требует счётного предела: " +
                        "stepLimit либо unimprovedStepLimit > 0 (стенные часы " +
                        "воспроизводимости не дают)")
                }
                // Ранняя остановка «N секунд без улучшения» — предел
                // СТЕННОЙ и воспроизводимость режима Д1 он разрушает,
                // поэтому в детерминированную остановку не переносится.
                // Прежде это делалось МОЛЧА; теперь говорится в журнал —
                // явный параметр запроса не должен исчезать беззвучно.
                if ((unimprovedSpentLimitSeconds ?: 0) > 0) {
                    cfgLog.warn("детерминированный режим Д1: ранняя остановка " +
                        "unimprovedSpentLimitSeconds={} НЕ ПРИМЕНЕНА — это предел " +
                        "стенных часов, он разрушил бы воспроизводимость; счётные " +
                        "пределы прогона: stepLimit={}, unimprovedStepLimit={}",
                        unimprovedSpentLimitSeconds, steps, unimproved)
                }
                // Счётный предел — главный; стенные часы остаются
                // СТРАХОВКОЙ (ИЛИ-составная остановка Timefold: что
                // наступит раньше). Без неё прогон на мире без исполнимого
                // хода не заканчивался никогда.
                val safety = deterministicSafety(spentLimitSeconds)
                base.terminationConfig = TerminationConfig().apply {
                    stepCountLimit = steps
                    unimprovedStepCountLimit = unimproved
                    spentLimit = safety
                }
                base.moveThreadCount = "NONE"
                base.randomSeed = seed ?: 0L
                base.environmentMode =
                    ai.timefold.solver.core.config.solver.EnvironmentMode.NO_ASSERT
            }
            return base
        }

        /** Журнал самой конфигурации (companion — вне экземпляра ресурса):
         *  сюда пишется всё, что запрос попросил, а режим Д1 принять не
         *  может. Молчаливых потерь параметров быть не должно. */
        private val cfgLog = LoggerFactory.getLogger("cosmos.packagedSolverConfig")

        /** Д1: страховочный предел стенных часов детерминированного режима.
         *  Берётся ПРЕДЕЛ ЗАПРОСА (`spentLimitSeconds` ручки); запрос
         *  предела не назвал — потолок [D1_DEFAULT_SAFETY_SECONDS].
         *  Страховка счётный предел не отменяет: она только не даёт прогону
         *  висеть вечно, когда счётный предел не наступает. */
        const val D1_DEFAULT_SAFETY_SECONDS = 60L

        fun deterministicSafety(requestedSeconds: Int?): Duration =
            Duration.ofSeconds(requestedSeconds?.toLong()?.takeIf { it > 0 }
                ?: D1_DEFAULT_SAFETY_SECONDS)

        /** Д1: профиль детерминированного режима поимённо (счётный предел,
         *  поток ходов, зерно, порядок сущностей, режим среды); null вне
         *  режима. Числа — те же, что кладёт [packagedSolverConfig].
         *
         *  Страховочного предела стенных часов в профиле НЕТ намеренно
         *  (правка по ревью 08.09.2026). Профиль — РЕЦЕПТ воспроизводимости,
         *  и его штампует `vmp.signatures.profile_stamp` целиком: попади
         *  страховка внутрь, два прогона одного рецепта, отличающиеся лишь
         *  сторожевым числом, получили бы разные штампы и разную подпись
         *  задачи, а состав профиля разошёлся бы с питоновским
         *  `deterministic_profile`. Сторожевое число живёт там, где ему
         *  место, — в признаке остановки прогона
         *  ([RunTermination.reading], поле `wall_clock_limit_ms`). */
        fun deterministicProfile(task: String, deterministic: Boolean?, seed: Long?,
                                 stepLimit: Int?, unimprovedStepLimit: Int?): Map<String, Any?>? {
            if (deterministic != true) return null
            return linkedMapOf(
                "version" to 1,
                "task" to task,
                "engine" to "timefold",
                "seed" to (seed ?: 0L),
                "move_threads" to 1,
                "environment_mode" to "NO_ASSERT",
                "entity_order" to "by_id",
                "termination" to linkedMapOf(
                    "kind" to "step_count",
                    "step_limit" to stepLimit?.takeIf { it > 0 },
                    "unimproved_step_limit" to unimprovedStepLimit?.takeIf { it > 0 }))
        }

        /** Ход ТЕКУЩЕГО single-sync прогона для графика схождения:
         *  каждое улучшение лучшего решения — событие (мс от старта,
         *  hard, soft). Один синхронный прогон на JVM за раз (контракт
         *  изолированной JVM), поэтому одна ссылка; /schedules/progress
         *  отдаёт снимок, Python-поллер пишет его в машинный след.
         *  Журнальный tail в отставке: числа графика приходят из
         *  события решателя, а не парсингом строк лога. */
        val singleSyncProgress =
            java.util.concurrent.atomic.AtomicReference<List<Map<String, Any>>>(
                emptyList())
    }

    private data class Job(
        val schedule: FlightCrewSchedule?,
        val createdAt: LocalDateTime,
        val exception: Throwable?,
    ) {
        companion object {
            fun ofSchedule(s: FlightCrewSchedule) = Job(s, LocalDateTime.now(), null)
            fun ofException(t: Throwable) = Job(null, LocalDateTime.now(), t)
        }
    }

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    fun list(): Collection<String> = jobs.keys

    @POST
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.TEXT_PLAIN)
    fun solve(
        problem: FlightCrewSchedule,
        @QueryParam("spentLimitSeconds") spentLimitSeconds: Int?,
        @QueryParam("softThreshold") softThreshold: Int?,
    ): String {
        // Решатель этого пути построен при старте — мимо окна порогов и
        // блоков logic пакета. Под пакетом он считал бы литералами и
        // объявлял бы это прогоном по пакету; отказ вместо молчания.
        if (TaskPackage.current() != null) {
            throw TaskModeRefused(
                "POST /schedules не читает пакет постановки (решатель построен при " +
                "старте, мимо окна порогов и logic): под пакетом — POST /schedules/single")
        }
        val jobId = UUID.randomUUID().toString()
        jobs[jobId] = Job.ofSchedule(problem)

        val term = TerminationConfig()
        if ((spentLimitSeconds ?: 0) > 0) term.spentLimit = Duration.ofSeconds(spentLimitSeconds!!.toLong())
        if (softThreshold != null) term.bestScoreLimit = "0hard/${softThreshold}soft"
        val override = SolverConfigOverride().withTerminationConfig(term)

        solverManager.solveBuilder()
            .withProblemId(jobId)
            .withConfigOverride(override)
            .withProblemFinder { jobs[jobId]!!.schedule!! }
            .withBestSolutionEventConsumer { event ->
                jobs[jobId] = Job.ofSchedule(event.solution())
            }
            .withExceptionHandler { id: Any, ex: Throwable ->
                jobs[id as String] = Job.ofException(ex)
                log.error("Failed solving jobId ({}).", id, ex)
            }
            .run()
        cleanJobs()
        return jobId
    }

    /**
     * Single-acceptor synchronous solve (no parallelism, no migration).
     *
     * Used by the Python multi-JVM coordinator to run one island in this
     * entire JVM, eliminating cross-island GC / memory contention. Caller
     * spawns 4 such JVMs on different ports and submits parallel POSTs.
     *
     * Returns the solved schedule directly (synchronous), not a jobId.
     * The HTTP client must use a timeout >= spentLimitSeconds + buffer.
     */
    @POST
    @Path("single")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    fun solveSingleSync(
        problem: FlightCrewSchedule,
        @QueryParam("acceptor") acceptor: String?,
        @QueryParam("spentLimitSeconds") spentLimitSeconds: Int?,
        // When true, skip the Construction Heuristic phase entirely —
        // assume the input is already a solved schedule and run only
        // Local Search. Used by the Python multi-JVM coordinator for
        // intervals 2..N of cooperative migration: the warm-start
        // input is the global best of all prior intervals, and we want
        // LS to refine it, not have FIRST_FIT re-fill any null entities
        // (which can perturb the optimum found in interval 1; see
        // RESULTS.md migration-degradation discussion).
        @QueryParam("warmStart") warmStart: Boolean?,
        // Construction-heuristic selector: FF (First Fit, default), FFD
        // (First Fit Decreasing), WF, WFD, SF, SFD, CI (Cheapest Insertion).
        // The *_DECREASING / CI variants use FlightAssignment's difficulty
        // weight factory to place the hardest-to-staff slots first — the
        // lever we test for piercing hard-constraint plateaus.
        @QueryParam("ch") ch: String?,
        // Per-request RNG seed. Without it every solver inherits
        // application.properties' random-seed=0, so "independent" islands
        // and repeated trials all follow the same deterministic trajectory
        // (the seed-monoculture flaw found in the session audit). Passing a
        // distinct seed per trial/island/interval restores real
        // diversification and makes multi-trial statistics meaningful.
        @QueryParam("randomSeed") randomSeed: Long?,
        // Rules profile: default = our RULES v2 provider; "GERAD" switches
        // to the GeradConstraintProvider core-legality profile (benchmark ①).
        @QueryParam("rules") rules: String?,
        // Мост CDPM -> COSMOS, этап А. Режим задачи:
        //   feasibility-hard — искать ЛЮБОЕ допустимое решение, остановка
        //     по достижении 0hard (bestScoreLimit);
        //   feasibility-soft — прежнее поведение: среди допустимых
        //     минимизировать штрафы;
        //   optimization — целевая функция пакета; критерий = целевая +
        //     Σ штрафов мягких (этап Б).
        // Без пакета постановки умолчание — прежнее поведение.
        @QueryParam("mode") mode: String?,
        // Этап Б: КАЖДАЯ целевая — отдельная задача оптимизации
        // (спецификация §4), поэтому в режиме optimization выбор целевой
        // обязателен и явен; выбирать за заказчика решатель не вправе.
        @QueryParam("objective_id") objectiveId: String?,
        // Ранняя остановка «N секунд без улучшения» (слово владельца
        // 2026-08-30, демо): локальный поиск не умеет сам сказать
        // «лучше не будет» — на крошечных мирах он высиживал весь
        // spentLimit ради нуля шагов. Параметр ЯВНЫЙ и по умолчанию
        // ВЫКЛЮЧЕН: боевые прогоны и сравнимость замеров не задеты;
        // передаёт его вызывающий (кнопка демо — 5).
        @QueryParam("unimprovedSpentLimitSeconds")
        unimprovedSpentLimitSeconds: Int?,
    ): FlightCrewSchedule {
        val seconds = (spentLimitSeconds ?: 60).coerceAtLeast(1).toLong()
        val acc = (acceptor ?: "LA").uppercase()
        val chName = (ch ?: "FF").uppercase()
        val skipCh = warmStart == true
        // Audit fix (2026-07-12, round 5): an unknown rules profile must be
        // a 400, not a silent fallback to the default provider.
        val knownRules = setOf("", "GERAD", "GERAD2", "GERAD2P", "GERAD3")
        if ((rules ?: "").uppercase() !in knownRules) {
            throw jakarta.ws.rs.BadRequestException(
                "unknown rules profile '$rules'; known: GERAD, GERAD2, " +
                "GERAD2P, GERAD3 or empty for the default")
        }
        // --- мост А: пакет постановки задачи -------------------------
        // Порядок намеренный: пакет разбирается и проверяется ДО сборки
        // решателя — отказ обязан случиться раньше, чем потрачена
        // секунда счёта.
        val taskMode = TaskModes.resolve(mode, TaskPackage.current(),
            task = "rostering", objectiveId = objectiveId,
            rulesProfile = rules)
        // Факты набора против применённых порогов — ДО сборки решателя:
        // состав мест, базы норм резерва, горизонт, роли. Что ни один ход
        // не исправит, отвергается здесь с именами, а не ложится в счёт.
        val coverage = RunThresholds.checkFacts(problem, taskMode.thresholds.applied)
        // Ступень 3: факты набора против раскладки logic (предметов
        // рассмотрено у каждого исполнителя; пусто при required — отказ).
        val logicFit = taskMode.logic?.let {
            LogicCompiler.fit(problem, taskMode.thresholds.applied, it, "single")
        }
        logicFit?.let { LogicCompiler.refuseIfNeeded(it) }
        // Перегонные кресла — из порога пакета, ПОСЛЕ фактов и fit:
        // состав мест и «предметов рассмотрено» — предметы НАБОРА,
        // кресла решателя в них не входят.
        DeadheadSeats.ensure(problem, taskMode.thresholds.applied)

        val cfg = buildSolverConfig(acc, seconds,
            skipConstructionHeuristic = skipCh, ch = chName)
        if ((unimprovedSpentLimitSeconds ?: 0) > 0) {
            cfg.terminationConfig = (cfg.terminationConfig
                ?: TerminationConfig()).apply {
                unimprovedSpentLimit =
                    Duration.ofSeconds(unimprovedSpentLimitSeconds!!.toLong())
            }
        }
        if (taskMode.stopAtFeasible) {
            // Любое допустимое решение оптимально: останавливаемся, как
            // только жёстких нарушений не осталось. Нижняя граница по
            // мягким берётся минимальной — они в этом режиме не критерий.
            cfg.terminationConfig = (cfg.terminationConfig
                ?: ai.timefold.solver.core.config.solver.termination
                    .TerminationConfig()).apply {
                bestScoreLimit = "0hard/" + Long.MIN_VALUE + "soft"
            }
        }
        if ((rules ?: "").uppercase() == "GERAD") {
            cfg.scoreDirectorFactoryConfig = ScoreDirectorFactoryConfig().apply {
                constraintProviderClass = GeradConstraintProvider::class.java
            }
        }
        if ((rules ?: "").uppercase() == "GERAD2") {
            cfg.scoreDirectorFactoryConfig = ScoreDirectorFactoryConfig().apply {
                constraintProviderClass = GeradPhase2ConstraintProvider::class.java
            }
        }
        if ((rules ?: "").uppercase() == "GERAD2P") {
            cfg.scoreDirectorFactoryConfig = ScoreDirectorFactoryConfig().apply {
                constraintProviderClass = GeradPairingRosterConstraintProvider::class.java
            }
        }
        if ((rules ?: "").uppercase() == "GERAD3") {
            cfg.scoreDirectorFactoryConfig = ScoreDirectorFactoryConfig().apply {
                constraintProviderClass = GeradPhase3ConstraintProvider::class.java
            }
        }
        if (randomSeed != null) {
            cfg.randomSeed = randomSeed
        }
        // Веса мягких — из пакета, а не из литералов кода (МУ-03).
        taskMode.weightOverrides?.let { problem.constraintWeightOverrides = it }
        // K-6: веса и пороги отдыха прогона — в решение, границы учёта
        // ночёвок — сотрудникам, до директора счёта (RosterLayoverCost).
        problem.layoverCostWeights = taskMode.effectiveWeights
        org.acme.flighcrewscheduling.solver.RosterLayoverCost.prepare(problem,
            taskMode.thresholds.applied, packaged = taskMode.fingerprint != null)
        // Окна дней недели — фактами из формы пакета (мягкая
        // семья calendar_windows).
        TaskPackage.current()?.let {
            problem.calendarWindowRules = it.calendarWindows.toMutableList()
        }

        // Пороги пакета видны провайдеру ТОЛЬКО в окне сборки фабрики:
        // провайдер создаётся здесь и читает их в свои поля; после выхода
        // держатель пуст, и следующий прогон без пакета увидит литералы.
        // Показания в отчёт — ФАКТИЧЕСКИЕ, от созданного провайдера.
        val factory = RunThresholds.withRunModel(taskMode.thresholds.applied,
                                                 logicFit?.plans ?: emptyList(),
                                                 packaged = taskMode.fingerprint != null) {
            SolverFactory.create<FlightCrewSchedule>(cfg)
        }
        // Имена ограничений — от построенной фабрики, не от намерения:
        // против них гейт сверяет «заявил = зарегистрировал».
        val registered = if (logicFit == null) null else LogicCompiler.registeredIds(factory)
        val remembered = TaskModes.withActualReadings(taskMode).copy(
            coverage = coverage, logic = logicFit?.copy(registered = registered))
        LastRun.remember(remembered)

        val solver = factory.buildSolver()
        log.info("single-sync solve: acceptor={}, ch={}, seconds={}, warmStart={}, seed={}",
            acc, chName, seconds, skipCh, randomSeed ?: 0L)
        log.info("single-sync solve: режим={}, пакет={}, целевая={}",
            taskMode.mode, taskMode.fingerprint ?: "нет",
            taskMode.objective?.id ?: "нет")
        // Серия схождения: каждое улучшение — событие для графика.
        singleSyncProgress.set(emptyList())
        val t0 = System.currentTimeMillis()
        solver.addEventListener { event: BestSolutionChangedEvent<FlightCrewSchedule> ->
            val s = event.newBestSolution.score
            if (s != null) {
                singleSyncProgress.updateAndGet { prev ->
                    prev + mapOf(
                        "tMillis" to (System.currentTimeMillis() - t0),
                        "hard" to s.hardScore(),
                        "soft" to s.softScore())
                }
            }
        }
        val solved = solver.solve(problem)
        log.info("single-sync solve done: acceptor={}, ch={}, score={}",
            acc, chName, solved.score)
        return solved
    }

    /** Снимок серии схождения текущего (или последнего) single-sync
     *  прогона — для писателя машинного следа на стороне Python. */
    @GET
    @Path("progress")
    @Produces(MediaType.APPLICATION_JSON)
    fun singleSyncProgressSnapshot(): List<Map<String, Any>> =
        singleSyncProgress.get()

    /**
     * Per-constraint score breakdown of a (typically already-solved)
     * schedule. POST a solved FlightCrewSchedule; returns
     * ScoreAnalysis.summarize() — a human-readable list of every constraint
     * with its total score and match count. Used to diagnose WHICH hard
     * constraints dominate a feasibility plateau before deciding which to
     * make proportional (gradient-smoothing experiment #5).
     */
    @POST
    @Path("analyze")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.TEXT_PLAIN)
    fun analyze(problem: FlightCrewSchedule): String {
        return packageAwareManager(problem).analyze(problem).summarize()
    }

    /**
     * Менеджер решений, знающий о пакете постановки.
     *
     * Бин solutionManager построен один раз при старте — его провайдер
     * читал литералы. С пакетом пересчёт решения по литералам дал бы
     * счёт, не совпадающий со счётом прогона (тот шёл по порогам пакета),
     * и два числа разошлись бы молча. Поэтому при пакете менеджер
     * строится на запрос, внутри окна порогов.
     */
    private fun packageAwareManager(problem: FlightCrewSchedule): SolutionManager<FlightCrewSchedule, HardSoftScore> {
        val model = packageRunModel(problem, "score") ?: return solutionManager
        // K-6: один вызов — границы учёта и пороги отдыха ПАКЕТА, как у прогона.
        org.acme.flighcrewscheduling.solver.RosterLayoverCost.prepare(problem, model.first.applied, packaged = true)
        return RunThresholds.withRunModel(model.first.applied, model.second.plans,
                                          packaged = true) {
            SolutionManager.create<FlightCrewSchedule, HardSoftScore>(
                SolverFactory.create(templateSolverConfig))
        }
    }

    /**
     * Модель прогона под пакетом для второго входа: пороги и раскладка
     * logic — те же гейты, что у прогона (чужой набор под пакетом не
     * считается молча; ступень 3 — те же корзины и тот же fit). Null —
     * пакета нет.
     */
    private fun packageRunModel(problem: FlightCrewSchedule, label: String):
            Pair<RunThresholds.Resolution, LogicReport>? {
        val pkg = TaskPackage.current() ?: return null
        val res = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        RunThresholds.checkFacts(problem, res.applied)
        val logic = LogicCompiler.compile(pkg, res, TaskModes.PROFILE_DEFAULT)
        LogicCompiler.refuseIfNeeded(logic)
        val fit = LogicCompiler.fit(problem, res.applied, logic, label)
        LogicCompiler.refuseIfNeeded(fit)
        return res to fit
    }

    /**
     * Повкладовый счёт ОДНОГО ростера по именам ограничений — путь
     * Community (директор счёта с учётом совпадений), не платный analyze.
     *
     * Ступень 3 доказывает вклад порождённого ограничения здесь: вклад по
     * его имени обязан равняться минус-сумме нарушений независимого оракула
     * на том же ростере. Под пакетом — та же модель прогона (пороги, планы,
     * гейты), что у /score; без пакета — литералы профиля.
     */
    @POST
    @Path("score-breakdown")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    fun scoreBreakdown(problem: FlightCrewSchedule): Map<String, Any?> {
        val model = packageRunModel(problem, "score-breakdown")
        return breakdownWith(model?.first?.applied ?: emptyMap(),
            model?.second?.plans ?: emptyList(),
            problem, TaskPackage.current()?.fingerprint)
    }

    private fun breakdownWith(applied: Map<String, RunThresholds.Applied>,
                              plans: List<org.acme.flighcrewscheduling.solver.LogicPlan>,
                              problem: FlightCrewSchedule,
                              fingerprint: String?,
                              packaged: Boolean = false): Map<String, Any?> {
        // K-6: разбор счёта — теми же границами учёта ночёвок, что у прогона.
        org.acme.flighcrewscheduling.solver.RosterLayoverCost.prepare(problem, applied, packaged = packaged)
        val factory = RunThresholds.withRunModel(applied, plans, packaged = packaged) {
            SolverFactory.create<FlightCrewSchedule>(templateSolverConfig)
        }
        return NativeExecutionSupport.breakdown(factory, problem, fingerprint, plans)
    }

    // ----------------------------------------------------- этап Г: прогоны по идентификатору

    /** Пул прогонов кнопки: счёт долгий, HTTP-запрос — нет (урок обрыва 45-минутного прогона). */
    private val runExecutor = Executors.newFixedThreadPool(2)

    /** Тело запросного прогона: набор решателя + пакет постановки байтами. */
    class RunRequest {
        var problem: FlightCrewSchedule? = null
        var packageZipBase64: String? = null
    }

    /** Г-П4: регистрация внешнего каталога пейринга (ortools-построитель). */
    class PairingCatalogRequest {
        var problem: FlightCrewSchedule? = null
        var packageZipBase64: String? = null
        var catalogBase64: String? = null
        var catalogSha256: String? = null
        /** Г-П4 п. 8: явное решение оператора принять неразмещаемый каталог. */
        var acceptUnplaceable: Boolean = false
    }

    /**
     * Г-П4 (директива владельца 2026-08-26): пейринг идёт лучшим найденным
     * инструментом — ortools-построителем; его каталог регистрируется здесь
     * записью задачи pairing и дальше фиксируется ШТАТНЫМ
     * `run?task=rostering&pairingRunId=...`. Гейты — в PackagedRuns
     * (отпечаток каталога, белый список вердиктов, разбиение).
     */
    @POST
    @Path("pairing-catalog")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    fun registerPairingCatalog(req: PairingCatalogRequest): Map<String, Any?> {
        val problem = req.problem ?: throw TaskModeRefused(
            "в теле запроса нет problem — набора решателя")
        val zip = req.packageZipBase64?.takeIf { it.isNotBlank() }
            ?: throw TaskModeRefused("в теле запроса нет packageZipBase64 — пакета постановки")
        val cat = req.catalogBase64?.takeIf { it.isNotBlank() }
            ?: throw TaskModeRefused("в теле запроса нет catalogBase64 — каталога пейринга")
        val sha = req.catalogSha256?.takeIf { it.isNotBlank() }
            ?: throw TaskModeRefused(
                "в теле запроса нет catalogSha256 — отпечаток каталога обязателен (Г-П4)")
        val record = PackagedRuns.registerPairingCatalog(
            problem,
            java.util.Base64.getDecoder().decode(zip),
            java.util.Base64.getDecoder().decode(cat),
            sha,
            req.acceptUnplaceable)
        return record.statusSnapshot()
    }

    /**
     * П-1: предпрогонная сверка покрытия — пакет запросом, БЕЗ счёта.
     * Что решатель умеет учитывать (пороги, корзины ступени 3), что нет
     * (unmapped, refused, notHard), и какие целевые вычислимы — тем же
     * резолвером, что у прогона. Гейт двусмысленности источников — как
     * у прогона по идентификатору.
     */
    @POST
    @Path("coverage")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    fun packageCoverage(req: RunRequest): Map<String, Any?> {
        if (org.acme.flighcrewscheduling.solver.TaskPackage.processPackagePresent()) {
            throw TaskModeRefused(
                "двусмысленность источников пакета: процессу задан пакет, и " +
                "одновременно пакет прислан запросом — какой сверять, решатель " +
                "не угадывает; уберите один из двух")
        }
        val zip = try {
            java.util.Base64.getDecoder().decode(req.packageZipBase64
                ?: throw TaskModeRefused("в теле запроса нет packageZipBase64 — пакета постановки"))
        } catch (e: IllegalArgumentException) {
            throw TaskModeRefused("packageZipBase64 не читается как base64: " + e.message)
        }
        val pkg = org.acme.flighcrewscheduling.solver.TaskPackage.readBytes(zip)
        return org.acme.flighcrewscheduling.solver.CoverageReport.of(pkg)
    }

    /**
     * Этап Г, Ш-2: прогон ПО ИДЕНТИФИКАТОРУ. Пакет приходит В ЗАПРОСЕ
     * (без процессного состояния), гейты постановки отрабатывают до
     * старта счёта (400 словами решателя), счёт идёт асинхронно —
     * статус и результат адресуются runId (Ш-2а: считает / готов / упал).
     */
    @POST
    @Path("run")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    fun startPackagedRun(req: RunRequest,
                         @QueryParam("mode") mode: String?,
                         @QueryParam("objective_id") objectiveId: String?,
                         @QueryParam("task") task: String?,
                         @QueryParam("pairingRunId") pairingRunId: String?,
                         @QueryParam("moves") moves: String?,
                         // Решение владельца 05.09.2026: кресел перегона на
                         // рейс — явный параметр; без него — состав экипажа.
                         @QueryParam("deadheadSeats") deadheadSeats: Int?,
                         @QueryParam("warmStart") warmStart: Boolean?,
                         @QueryParam("spentLimitSeconds") spentLimitSeconds: Int?,
                         // Ранняя остановка «N секунд без улучшения»
                         // (слово владельца 2026-08-30, демо): явный
                         // параметр, по умолчанию выключен — боевые
                         // прогоны не задеты. Тот же контракт, что у
                         // POST /single.
                         @QueryParam("unimprovedSpentLimitSeconds")
                         unimprovedSpentLimitSeconds: Int?,
                         // Д1 плана прямого хода (final-report/CDPM-PLAN-
                         // PRYAMOJ-HOD-CLD.md): детерминированный режим —
                         // остановка по СЧЁТНЫМ событиям (число шагов либо
                         // неулучшающих шагов), не по стенным часам; один
                         // поток ходов; фиксированное зерно; порядок
                         // сущностей по идентификаторам; режим среды
                         // REPRODUCIBLE. Профиль штампуется в записи прогона
                         // (status, applied-rules) поимённо.
                         @QueryParam("deterministic") deterministic: Boolean?,
                         @QueryParam("seed") seed: Long?,
                         @QueryParam("stepLimit") stepLimit: Int?,
                         @QueryParam("unimprovedStepLimit")
                         unimprovedStepLimit: Int?): Map<String, Any?> {
        val problem = req.problem ?: throw TaskModeRefused(
            "в теле запроса нет problem — набора решателя")
        val zip = try {
            java.util.Base64.getDecoder().decode(req.packageZipBase64
                ?: throw TaskModeRefused("в теле запроса нет packageZipBase64 — пакета постановки"))
        } catch (e: IllegalArgumentException) {
            throw TaskModeRefused("packageZipBase64 не читается как base64: " + e.message)
        }
        val seconds = (spentLimitSeconds ?: 30).toLong()
        // Шаг 4 (05.09.2026): ходы по цепочкам стартуют с начального
        // решения по цепочкам (раздача в PackagedRuns.start) — конструктив
        // не нужен и мешал бы (заново раздал бы места по одному).
        val cfg = buildSolverConfig("LA", seconds,
            skipConstructionHeuristic = (warmStart ?: false) || LargeMoves.seedsChains(moves),
            ch = "FF", moves = moves,
            // Перегонные кресла — только явным параметром deadheadSeats (или
            // порогом пакета); при них конструктив их не заполняет.
            filterDeadheadSeats = (deadheadSeats ?: 0) > 0)
        val taskName = task?.trim()?.lowercase().orEmpty().ifEmpty { "rostering" }
        // K-7-a: параметры прогона (зерно, ранняя остановка, детерминированный
        // режим Д1) накладываются одной функцией без Quarkus —
        // [packagedSolverConfig]; стенд проверяет её напрямую.
        // spentLimitSeconds отдаётся СЫРЫМ (не `seconds`): страховке Д1
        // нужно отличать «запрос назвал 30» от «запрос предела не назвал»,
        // а базовая остановка это различие уже потеряла.
        packagedSolverConfig(cfg, seed, deterministic, stepLimit, unimprovedStepLimit,
            unimprovedSpentLimitSeconds, spentLimitSeconds)
        // Профиль Д1 — рецепт воспроизводимости; сторожевое число стенных
        // часов в него не входит (оно в признаке остановки прогона).
        val profile = deterministicProfile(taskName, deterministic, seed, stepLimit,
            unimprovedStepLimit)
        val record = PackagedRuns.start(problem, zip, mode, objectiveId, cfg, runExecutor,
            task = taskName,
            pairingRunId = pairingRunId?.trim()?.ifEmpty { null },
            profile = profile,
            moves = moves,
            deadheadSeats = deadheadSeats)
        return linkedMapOf(
            "runId" to record.runId,
            "state" to record.state,
            "packageFingerprint" to record.packageFingerprint,
            "mode" to record.mode)
    }

    private fun runOr404(runId: String): org.acme.flighcrewscheduling.solver.RunRecord =
        PackagedRuns.get(runId) ?: throw jakarta.ws.rs.NotFoundException(
            "прогон " + runId + " не найден в реестре решателя")

    /** Ш-2а/Ш-5: статус прогона — считает / готов / упал (с причиной). */
    @GET
    @Path("run/{runId}/status")
    @Produces(MediaType.APPLICATION_JSON)
    fun packagedRunStatus(@PathParam("runId") runId: String): Map<String, Any?> =
        runOr404(runId).statusSnapshot()

    /**
     * ОТМЕНА прогона по идентификатору (08.09.2026). Прежде отменить
     * пакетный прогон было нечем: DELETE был только у старого пути
     * jobId, и зависший прогон держал поток пула прогонов до перезапуска
     * службы. Ответ — состояние СЛОВАМИ: отменён ли, что было до того.
     * Отменённый прогон заканчивается лучшим решением на момент отмены
     * (Timefold `terminateEarly`), а не падением.
     *
     * Поля ответа (правка по ревью 08.09.2026 — прежде два разных факта
     * назывались одним словом): `cancelled` — принята ли ЗАЯВКА на
     * остановку; `solverReached` — дошла ли она до СЧИТАЮЩЕГО решателя;
     * `stage` — где прогон был в этот миг (очередь пула, сборка
     * решателя, счёт, отчёты после счёта). Повторять отмену не нужно:
     * заявка живёт в записи прогона и исполняется сама — прогон,
     * отменённый до старта счёта, не считает вовсе, а отмену в зазоре
     * «решатель собран, счёт не начат» решатель получает на старте
     * первой фазы.
     */
    @DELETE
    @Path("run/{runId}")
    @Produces(MediaType.APPLICATION_JSON)
    fun packagedRunCancel(@PathParam("runId") runId: String): Map<String, Any?> {
        runOr404(runId)
        return PackagedRuns.cancel(runId)
            ?: throw jakarta.ws.rs.NotFoundException(
                "прогон " + runId + " не найден в реестре решателя")
    }

    /** Решение прогона + отчёт цены ночёвок ТОГО ЖЕ прогона (H11-WIRE-1:
     *  тот же объект RunRecord.layoverCost, что в applied-rules этого runId);
     *  пока считает или упал — отказ словами состояния. */
    @GET
    @Path("run/{runId}/result")
    @Produces(MediaType.APPLICATION_JSON)
    fun packagedRunResult(@PathParam("runId") runId: String): PackagedRunResult =
        PackagedRunResult.of(runOr404(runId))

    /** Отчёт применённого ЭТОГО прогона (не «последнего»): источник certifier-input. */
    @GET
    @Path("run/{runId}/applied-rules")
    @Produces(MediaType.APPLICATION_JSON)
    fun packagedRunApplied(@PathParam("runId") runId: String): Map<String, Any?> {
        val r = runOr404(runId)
        val report = r.report ?: throw TaskModeRefused(
            "прогон " + runId + " ещё не собрал показаний: состояние «" + r.state + "»")
        return linkedMapOf(
            "runId" to r.runId,
            "state" to r.state,
            "score" to r.score,
            // Д1: профиль детерминированного режима — поимённо, вместе с
            // показаниями (сертификат и подпись задачи читают отсюда).
            "profile" to r.profile,
            // Д1 (08.09.2026): ПО КАКОМУ пределу прогон остановился —
            // счётный (воспроизводимо) либо страховочный по стенным
            // часам; читатель отличает эти случаи словами.
            "termination" to r.termination,
            // K-8/K-32: известная часть цены ночёвок (Контракт Б) — из
            // статуса прогона, как прочие показания main.
            "layoverCost" to r.layoverCost,
            "lastRun" to LastRun.snapshotOf(report))
    }

    /** Повкладовый счёт ростера ПОД ПАКЕТОМ ЭТОГО прогона (Ш-3, run-scoped). */
    @POST
    @Path("run/{runId}/score-breakdown")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    fun packagedRunBreakdown(@PathParam("runId") runId: String,
                             problem: FlightCrewSchedule): Map<String, Any?> {
        val r = runOr404(runId)
        val report = r.report ?: throw TaskModeRefused(
            "прогон " + runId + " ещё не собрал показаний: состояние «" + r.state + "»")
        // Разбор ПОД ПАКЕТОМ прогона означает и его веса, и его окна
        // календаря: присланное решение их не несёт, и без этого разбор
        // считался бы литералами провайдера (самотест Д1 2026-09-03:
        // счёт прогона −5143soft против разбора −3712soft при одном
        // решении — штрафная сверка Д4 такое обязана ловить, а разбор
        // обязан быть тем же счётом, что у прогона).
        NativeExecutionSupport.prepare(problem, report.thresholds.applied,
            report.effectiveWeights, report.weightOverrides, r.pkg.calendarWindows)
        return breakdownWith(report.thresholds.applied, report.logic?.plans ?: emptyList(),
            problem, r.packageFingerprint, packaged = true)
    }

    /**
     * Score-only pass: recalculate the score of a (possibly partially
     * initialized) schedule WITHOUT solving. Unlike analyze(), plain
     * update() is a Community feature. Used by the re-scoring experiment:
     * expand a pairing-based roster onto real flights and evaluate it with
     * the ORIGINAL constraint set, so single-stage and two-stage results
     * become comparable on one scale. Returns the score string (may carry
     * an init prefix like "-12init/..." when some employees are null —
     * constraint streams' forEach() skips uninitialized entities).
     */
    /**
     * Свидетельство ПОТРЕБИТЕЛЯ о применённых правилах (пятый аудит,
     * пакет B).
     *
     * Отпечаток конфигурации доказывает, что значения решателю ПЕРЕДАЛИ.
     * Он ничего не говорит о том, применил ли их решатель: параметр,
     * который никто не читает, даёт тот же отпечаток, что и работающий.
     * Здесь решатель отвечает за себя сам — возвращаются ФАКТИЧЕСКИЕ
     * чтения параметров его констрейнт-провайдерами и то, сколько раз
     * ресурсное правило реально считалось при оценке.
     *
     * `declared` — что профиль ЗАЯВЛЯЕТ как применяемое; `applied` — что
     * прочитано на самом деле. Сверяет их вызывающая сторона: заявление
     * без чтения и есть та самая «передали, но не применили».
     */
    @GET
    @Path("applied-rules")
    @Produces(MediaType.APPLICATION_JSON)
    fun appliedRules(@QueryParam("rules") rules: String?): Map<String, Any?> {
        val profile = (rules ?: "").uppercase()
        val declared: List<String> = when (profile) {
            "GERAD2P" -> GeradPairingRosterConstraintProvider.RULE_KEYS
            "GERAD" -> GeradConstraintProvider.RULE_KEYS
            else -> emptyList()
        }
        val wr = ResourceRules.weeklyRest
        // Мост А: пакет постановки — заявленное (что прочитано из
        // пакета) и применённое (что реально ушло в последний прогон).
        // Расходятся они ровно в том случае, ради которого гейт и нужен.
        val declaredPkg = try {
            TaskPackage.current()?.let { p ->
                mapOf(
                    "fingerprint" to p.fingerprint,
                    "specVersion" to p.specVersion,
                    "customer" to p.customer,
                    "sourceRun" to p.sourceRun,
                    "revision" to p.revision,
                    "allowedModes" to p.allowedModes,
                    "objectives" to p.objectives,
                    "weights" to p.weights,
                    "rulesWithoutExecutor" to p.unmappedRules,
                    "componentsWithoutWeight" to p.componentsWithoutWeight,
                    "hasData" to p.hasData)
            }
        } catch (e: TaskPackageError) {
            // Отчёт обязан говорить и о негодном пакете: молчание здесь
            // выглядело бы как «пакета нет».
            mapOf("error" to e.message)
        }
        return mapOf(
            "schema" to "cosmos-applied-rules/v1",
            "profile" to (if (profile.isEmpty()) null else profile),
            "taskPackage" to declaredPkg,
            "lastRun" to LastRun.snapshot(),
            "declared" to declared,
            "applied" to RulesConfig.appliedReadings(),
            "resourceRules" to mapOf(
                "weeklyRest" to (wr?.let {
                    mapOf("requiredMin" to it.requiredMin,
                        "periodMin" to it.periodMin,
                        "tzOffsetMin" to it.tzOffsetMin,
                        "horizonFromEpochMin" to it.horizonFromEpochMin,
                        "horizonToEpochMin" to it.horizonToEpochMin)
                }),
                "weeklyRestEvaluations" to
                    ResourceRules.weeklyRestEvaluations(),
            ),
        )
    }

    @POST
    @Path("score")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.TEXT_PLAIN)
    fun scoreOnly(problem: FlightCrewSchedule,
                  @QueryParam("rules") rules: String?): String {
        val providerClass = when ((rules ?: "").uppercase()) {
            "GERAD" -> GeradConstraintProvider::class.java
            "GERAD2" -> GeradPhase2ConstraintProvider::class.java
            "GERAD2P" -> GeradPairingRosterConstraintProvider::class.java
            "GERAD3" -> GeradPhase3ConstraintProvider::class.java
            "" -> null
            else -> throw jakarta.ws.rs.BadRequestException(
                "unknown rules profile '$rules'; known: GERAD, GERAD2, " +
                "GERAD2P, GERAD3 or empty for the default")
        }
        if (providerClass == null) {
            val score = packageAwareManager(problem).update(problem)
            return score.toString()
        }
        if (TaskPackage.current() != null) {
            throw TaskModeRefused(
                "профиль " + rules + " под пакетом постановки не читает ни порогов, ни " +
                "logic пакета: счёт под пакетом — только профилем по умолчанию")
        }
        // One-off SolutionManager under the requested rules profile so both
        // configurations' final rosters are scored on one scale.
        val cfg = SolverConfig(templateSolverConfig).apply {
            scoreDirectorFactoryConfig = ScoreDirectorFactoryConfig().apply {
                constraintProviderClass = providerClass
            }
        }
        val sm = SolutionManager.create<FlightCrewSchedule, HardSoftScore>(
            SolverFactory.create(cfg))
        return sm.update(problem).toString()
    }

    /**
     * Comprehensive ("ensemble") search.
     *
     * Spawns four solvers in parallel, each with a different Local-Search
     * acceptor — Late Acceptance, Simulated Annealing, Tabu Search and
     * Great Deluge — and reports the best score across all of them.
     *
     * The semantics of `phaseAFraction` (N%):
     *   - **1.0** (the only mode supported in this iteration): all four run
     *     for the full `spentLimitSeconds`; at the end we pick the highest
     *     score. This is the simplest "embarrassingly parallel" variant.
     *   - **<1.0** (planned): Phase A runs all four for `fraction*total`,
     *     then Phase B continues a single Late-Acceptance solver from the
     *     global best for the remaining `(1-fraction)*total` seconds.
     *
     * The endpoint returns a jobId; clients poll GET /schedules/{jobId}
     * exactly as for the regular solver. The cached schedule is updated
     * whenever ANY of the four islands finds a strictly better score, so
     * UI watchers see live progress.
     */
    @POST
    @Path("comprehensive")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.TEXT_PLAIN)
    fun solveComprehensive(
        problem: FlightCrewSchedule,
        @QueryParam("spentLimitSeconds") spentLimitSeconds: Int?,
        @QueryParam("phaseAFraction") phaseAFraction: Double?,
        @QueryParam("ensemble") ensemble: String?,
    ): String {
        // Пакет постановки этим путём не исполняется: режимы, веса и
        // пороги применяет только /single. Молча посчитать по литералам,
        // когда пакет задан, — «прислали, не применили»; отказ.
        if (TaskPackage.current() != null) {
            throw jakarta.ws.rs.BadRequestException(
                "задан пакет постановки (CREW_TASK_PACKAGE), а путь " +
                "comprehensive его не исполняет: режимы, веса и пороги " +
                "пакета применяет только /schedules/single")
        }
        val jobId = UUID.randomUUID().toString()
        jobs[jobId] = Job.ofSchedule(problem)
        comprehensiveLocks[jobId] = Any()
        comprehensiveSolvers[jobId] = java.util.Collections.synchronizedList(mutableListOf())
        comprehensiveIslandScores[jobId] = ConcurrentHashMap()

        val totalSeconds = (spentLimitSeconds ?: 120).coerceAtLeast(1).toLong()
        val fraction = (phaseAFraction ?: 1.0).coerceIn(0.01, 1.0)
        val ensembleName = (ensemble ?: "classic").lowercase()

        Thread({
            try {
                when (ensembleName) {
                    "island-migration", "i" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 5)
                    "i_3" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 3)
                    "i_6" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 6)
                    "i_7" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 7)
                    "i_8" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 8)
                    "i_10" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 10)
                    "ip", "island-perturbed" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 5, perturbPct = 0.05)
                    "ia", "island-asymmetric" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 5, asymmetric = true)
                    "hi", "hybrid-hi" ->
                        runIslandMigrationGeneric(jobId, problem, totalSeconds,
                            numIntervals = 5, multiPhasePerIsland = true)
                    else -> {
                        if (fraction >= 1.0) {
                            runAllToEnd(jobId, problem, totalSeconds, ensembleName)
                        } else {
                            // Phase A + Phase B will be implemented in a follow-up;
                            // for now, behave like fraction=1.0 to avoid silent breakage.
                            log.warn("phaseAFraction={} not yet supported; running fraction=1.0 instead", fraction)
                            runAllToEnd(jobId, problem, totalSeconds, ensembleName)
                        }
                    }
                }
            } catch (e: Throwable) {
                log.error("Comprehensive solve failed for jobId {}", jobId, e)
                jobs[jobId] = Job.ofException(e)
            } finally {
                comprehensiveLocks.remove(jobId)
                comprehensiveSolvers.remove(jobId)
                cancelledJobs.remove(jobId)
            }
        }, "comprehensive-${jobId.substring(0, 8)}").apply {
            isDaemon = true
            start()
        }

        cleanJobs()
        return jobId
    }

    /**
     * Run all four acceptors in parallel for the full budget; pick global best.
     *
     * Each acceptor gets its own ``SolverFactory`` (built from a programmatic
     * ``SolverConfig``) and its own deep-cloned problem instance — so the four
     * threads don't share mutable state. As each island fires
     * ``BestSolutionChangedEvent``s we update ``jobs[jobId]`` if and only if
     * the candidate beats the current global best.
     */
    private fun runAllToEnd(
        jobId: String,
        problem: FlightCrewSchedule,
        totalSeconds: Long,
        ensembleName: String,
    ) {
        // Resolve the ensemble selection into a list of (island-label, config)
        // pairs. The label is what the client sees in /islands and in the
        // log; the config is the per-island SolverConfig that gets fed into
        // SolverFactory.create().
        val islands: List<Pair<String, SolverConfig>> = when (ensembleName) {
            "multi-la", "e" -> listOf(
                "LA-200"  to buildLaConfigWithSize(200,  totalSeconds),
                "LA-400"  to buildLaConfigWithSize(400,  totalSeconds),
                "LA-800"  to buildLaConfigWithSize(800,  totalSeconds),
                "LA-1600" to buildLaConfigWithSize(1600, totalSeconds),
            )
            "gold", "b" -> listOf(
                "LA"   to buildSolverConfig("LA",   totalSeconds),
                "DLA"  to buildSolverConfig("DLA",  totalSeconds),
                "SA"   to buildSolverConfig("SA",   totalSeconds),
                "SCHC" to buildSolverConfig("SCHC", totalSeconds),
            )
            // 3-island variant: LA + SA + SCHC, no DLA. Reproduces the
            // effective behaviour of the original "B v1" run (where DLA
            // crashed immediately due to the preview-feature gating bug
            // and contributed nothing). Useful as a control to disentangle
            // "DLA value" from "contention from 4 islands vs 3".
            "gold-3way", "b1" -> listOf(
                "LA"   to buildSolverConfig("LA",   totalSeconds),
                "SA"   to buildSolverConfig("SA",   totalSeconds),
                "SCHC" to buildSolverConfig("SCHC", totalSeconds),
            )
            "multi-phase", "h" -> (0 until 4).map { seed ->
                "MP-S$seed" to buildMultiPhaseConfig(seed.toLong(), totalSeconds)
            }
            else -> COMPREHENSIVE_ACCEPTORS.map { acc ->
                acc to buildSolverConfig(acc, totalSeconds)
            }
        }

        log.info("comprehensive jobId={} ensemble={} islands={} budget={}s",
            jobId, ensembleName, islands.map { it.first }, totalSeconds)

        val executor = Executors.newFixedThreadPool(islands.size) { r ->
            Thread(r, "comprehensive-${jobId.substring(0, 8)}-island").apply { isDaemon = true }
        }

        // Serialize the problem ONCE so each island can Jackson-clone an
        // independent copy from JSON. ObjectMapper round-trip is the cleanest
        // way to deep-clone a Timefold solution from outside Solver internals.
        val problemJson = objectMapper.writeValueAsString(problem)

        try {
            val futures = islands.map { (label, config) ->
                executor.submit<FlightCrewSchedule> {
                    runIsland(jobId, label, problemJson, config)
                }
            }

            // Block until every island has reported its final solution. We
            // give Timefold up to 60s of slack on top of the budget for graceful
            // shutdown / final score computation.
            val solutions = futures.mapIndexed { idx, fut ->
                try {
                    fut.get(totalSeconds + 60, TimeUnit.SECONDS)
                } catch (e: Throwable) {
                    log.error("island {} ({}) failed", idx, islands[idx].first, e)
                    null
                }
            }.filterNotNull()

            // argmax across all islands' final solutions, plus whatever was
            // already cached (in case an island crashed but its mid-solve
            // best was still streamed via the event listener).
            val candidates = solutions.toMutableList()
            jobs[jobId]?.schedule?.let { candidates += it }
            val best = candidates.maxByOrNull { it.score ?: HardSoftScore.ZERO } ?: solutions.first()

            jobs[jobId] = Job.ofSchedule(best)
            log.info("comprehensive jobId={} done; best score={}", jobId, best.score)
        } finally {
            executor.shutdown()
            executor.awaitTermination(5, TimeUnit.SECONDS)
        }
    }

    /**
     * Variant I (Island Migration / Cooperative Multi-Start) — generic.
     *
     * Splits the budget into ``numIntervals`` chunks. In each chunk, four
     * islands (LA / DLA / SA / SCHC) run in parallel. At the end of each
     * chunk we pick the best across the four and (depending on the mode
     * flags) use it as the seed for the next chunk.
     *
     * Mode flags:
     *  - ``asymmetric``: top 2 islands keep **their own** previous solution
     *    (continue exploring their basin), bottom 2 reset to global best.
     *    Default symmetric (all reset to global best).
     *  - ``perturbPct``: when > 0, before each island's solve (after the
     *    first interval) clone the input and randomly reassign that fraction
     *    of FlightAssignments to random employees. Different RNG seed per
     *    island so they diverge.
     *  - ``multiPhasePerIsland``: each island runs the multi-phase pipeline
     *    (CH → LA → SA → Tabu → LA-polish) over its interval, instead of
     *    a single fixed acceptor. Combines H within-island variety with I
     *    cross-island migration.
     */
    private fun runIslandMigrationGeneric(
        jobId: String,
        problem: FlightCrewSchedule,
        totalSeconds: Long,
        numIntervals: Int = 5,
        asymmetric: Boolean = false,
        perturbPct: Double = 0.0,
        multiPhasePerIsland: Boolean = false,
    ) {
        // Budget honoring (audit round 6, P6.3): the former
        // (totalSeconds / numIntervals).coerceAtLeast(60) floor could blow
        // the requested budget up by orders of magnitude (a 1-second request
        // with 3 intervals ran 180s). Instead, distribute the budget: every
        // interval gets the integer share, the division remainder is spread
        // one extra second at a time over the first intervals, and when the
        // budget is smaller than the interval count we run fewer intervals
        // (1s each) — the SUM of interval budgets never exceeds totalSeconds.
        val effectiveIntervals =
            if (totalSeconds < numIntervals) totalSeconds.coerceAtLeast(1L).toInt() else numIntervals
        val baseIntervalSeconds = totalSeconds / effectiveIntervals
        val extraSeconds = totalSeconds % effectiveIntervals
        val acceptors = listOf("LA", "DLA", "SA", "SCHC")

        log.info("comprehensive jobId={} ensemble=island-migration intervals={} "
            + "baseIntervalSecs={} (+1s for the first {}) asymmetric={} perturbPct={} multiPhase={}",
            jobId, effectiveIntervals, baseIntervalSeconds, extraSeconds,
            asymmetric, perturbPct, multiPhasePerIsland)

        val executor = Executors.newFixedThreadPool(acceptors.size) { r ->
            Thread(r, "comprehensive-${jobId.substring(0, 8)}-island").apply { isDaemon = true }
        }

        var globalBest: FlightCrewSchedule = problem
        // Per-island last-output (for asymmetric mode)
        val islandLastSolution = mutableMapOf<String, FlightCrewSchedule>()

        try {
            for (k in 1..effectiveIntervals) {
                // Cancellation check (audit round 6, P6.3): DELETE
                // /schedules/{jobId} terminates the currently running island
                // solvers, but without this check the NEXT interval would
                // still start. Finish with the current global best instead.
                if (jobId in cancelledJobs) {
                    log.info("comprehensive jobId={} cancelled; skipping intervals {}..{}; "
                        + "globalBest score={}", jobId, k, effectiveIntervals, globalBest.score)
                    break
                }
                // The first `extraSeconds` intervals absorb the remainder.
                val intervalSeconds = baseIntervalSeconds + if (k <= extraSeconds) 1L else 0L
                comprehensiveIslandScores[jobId]?.clear()
                log.info("comprehensive jobId={} interval {}/{} starting from globalBest score={}",
                    jobId, k, effectiveIntervals, globalBest.score)

                // Decide each island's input for this interval
                val inputs: Map<String, FlightCrewSchedule> = if (k == 1 || !asymmetric) {
                    acceptors.associateWith { globalBest }
                } else {
                    // Asymmetric: top 2 keep their own previous, bottom 2 use globalBest
                    val sorted = islandLastSolution.entries
                        .sortedByDescending { it.value.score ?: HardSoftScore.ZERO }
                        .map { it.key }
                    val topAcceptors = sorted.take(2).toSet()
                    log.info("comprehensive jobId={} asymmetric: topAcceptors={}",
                        jobId, topAcceptors)
                    acceptors.associateWith { acc ->
                        if (acc in topAcceptors) islandLastSolution[acc] ?: globalBest
                        else globalBest
                    }
                }

                val futures = acceptors.mapIndexed { idx, acceptor ->
                    val input = inputs[acceptor]!!
                    val inputJson = objectMapper.writeValueAsString(input)
                    executor.submit<FlightCrewSchedule> {
                        var cloned = objectMapper.readValue(
                            inputJson, FlightCrewSchedule::class.java)

                        // Apply perturbation (skip first interval — we want the
                        // initial CH-untouched solve from the raw problem)
                        if (perturbPct > 0.0 && k > 1) {
                            cloned = perturbSchedule(cloned, perturbPct,
                                seed = k * 1000L + idx)
                        }

                        val cfg = if (multiPhasePerIsland) {
                            buildMultiPhaseConfig(idx.toLong(), intervalSeconds)
                        } else {
                            buildSolverConfig(acceptor, intervalSeconds)
                        }

                        val solver: Solver<FlightCrewSchedule> =
                            SolverFactory.create<FlightCrewSchedule>(cfg).buildSolver()
                        comprehensiveSolvers[jobId]?.add(solver)
                        solver.addEventListener {
                            event: BestSolutionChangedEvent<FlightCrewSchedule> ->
                            onIslandImprovement(jobId, acceptor, event.newBestSolution)
                        }
                        solver.solve(cloned)
                    }
                }

                val solutions = futures.mapIndexed { idx, fut ->
                    val acc = acceptors[idx]
                    val sol = try {
                        fut.get(intervalSeconds + 60, TimeUnit.SECONDS)
                    } catch (e: Throwable) {
                        log.error("interval {} island {} ({}) failed", k, idx, acc, e)
                        null
                    }
                    acc to sol
                }

                // Update per-island last solution (for asymmetric)
                solutions.forEach { (acc, sol) ->
                    if (sol != null) islandLastSolution[acc] = sol
                }

                // Update globalBest
                val intervalBest = solutions.mapNotNull { it.second }.maxByOrNull {
                    it.score ?: HardSoftScore.ZERO
                }
                if (intervalBest != null) {
                    val newScore = intervalBest.score
                    val oldScore = globalBest.score
                    val isBetter = oldScore == null
                        || (newScore != null && newScore.compareTo(oldScore) > 0)
                    if (isBetter) {
                        globalBest = intervalBest
                        jobs[jobId] = Job.ofSchedule(globalBest)
                    }
                }
                log.info("comprehensive jobId={} interval {}/{} ended; globalBest score={}",
                    jobId, k, effectiveIntervals, globalBest.score)
            }
            log.info("comprehensive jobId={} done; final best score={}",
                jobId, globalBest.score)
        } finally {
            executor.shutdown()
            executor.awaitTermination(5, TimeUnit.SECONDS)
        }
    }

    /**
     * Perturb a schedule by reassigning ``pct`` of its FlightAssignments
     * to random Employees. Used by the perturbation variant of island
     * migration to break identical post-migration trajectories. Mutates
     * the passed-in schedule and returns it (for chain-style usage).
     *
     * Note: the perturbed solution will likely be **worse** than the input
     * (random reassignments break previously-satisfied constraints). The
     * bet is that downstream LS recovers + finds different basins to
     * explore.
     */
    private fun perturbSchedule(
        schedule: FlightCrewSchedule,
        pct: Double,
        seed: Long,
    ): FlightCrewSchedule {
        val flightAssignments = schedule.flightAssignments ?: return schedule
        val employees = schedule.employees ?: return schedule
        if (flightAssignments.isEmpty() || employees.isEmpty()) return schedule

        val rng = kotlin.random.Random(seed)
        val k = (flightAssignments.size * pct).toInt().coerceAtLeast(1)
        repeat(k) {
            val ass = flightAssignments[rng.nextInt(flightAssignments.size)]
            ass.employee = employees[rng.nextInt(employees.size)]
        }
        return schedule
    }

    /** Worker for one island — builds a Solver with the given acceptor and runs it.
     *
     *  Optionally pins the island's thread to a dedicated CPU core via
     *  OpenHFT AffinityLock when ``-Dcomprehensive.affinity=true`` is on the
     *  JVM command line. Default is OFF so we can cleanly compare:
     *    - baseline (no affinity, OS-scheduled)
     *    - vs. pinned (each island owns one of 4 cores)
     *  to see how much CPU contention is eating throughput.
     */
    private fun runIsland(
        jobId: String,
        acceptor: String,
        problemJson: String,
        cfg: SolverConfig,
    ): FlightCrewSchedule {
        val solver: Solver<FlightCrewSchedule> = SolverFactory.create<FlightCrewSchedule>(cfg).buildSolver()
        // Register so DELETE /schedules/{jobId} can terminate every island.
        comprehensiveSolvers[jobId]?.add(solver)

        solver.addEventListener { event: BestSolutionChangedEvent<FlightCrewSchedule> ->
            onIslandImprovement(jobId, acceptor, event.newBestSolution)
        }

        val cloned = objectMapper.readValue(problemJson, FlightCrewSchedule::class.java)
        log.info("comprehensive jobId={} island={} starting", jobId, acceptor)

        val affinityEnabled = System.getProperty("comprehensive.affinity", "false")
            .equals("true", ignoreCase = true)

        val solved = if (!affinityEnabled) {
            solver.solve(cloned)
        } else {
            try {
                AffinityLock.acquireLock().use { lock ->
                    log.info("comprehensive jobId={} island={} pinned to CPU#{}",
                        jobId, acceptor, lock.cpuId())
                    solver.solve(cloned)
                }
            } catch (e: UnsatisfiedLinkError) {
                log.warn("comprehensive jobId={} island={} affinity native lib unavailable; "
                    + "falling back to OS scheduling", jobId, acceptor, e)
                solver.solve(cloned)
            } catch (e: NoClassDefFoundError) {
                log.warn("comprehensive jobId={} island={} affinity classes missing; "
                    + "falling back to OS scheduling", jobId, acceptor, e)
                solver.solve(cloned)
            }
        }

        log.info("comprehensive jobId={} island={} finished score={}", jobId, acceptor, solved.score)
        return solved
    }

    /**
     * Update the global best in ``jobs[jobId]`` if the candidate strictly beats
     * the current cached score. Uses HardSoftScore's natural comparator
     * (hard tier dominates, soft tier breaks ties) under a per-job lock so two
     * islands cannot trample each other's update. Always records the
     * candidate score in ``comprehensiveIslandScores`` so the per-island
     * monitoring endpoint sees it even if it isn't the global best.
     */
    private fun onIslandImprovement(
        jobId: String,
        acceptor: String,
        candidate: FlightCrewSchedule,
    ) {
        val candidateScore = candidate.score
        // Per-island tracking: always record, regardless of global beat.
        if (candidateScore != null) {
            comprehensiveIslandScores[jobId]?.put(acceptor, candidateScore.toString())
        }
        val lock = comprehensiveLocks[jobId] ?: return
        synchronized(lock) {
            val current = jobs[jobId]?.schedule
            val currentScore = current?.score
            val isBetter = when {
                candidateScore == null -> false
                currentScore == null -> true
                else -> candidateScore.compareTo(currentScore) > 0
            }
            if (isBetter) {
                log.debug("comprehensive jobId={} island={} new global best {} (was {})",
                    jobId, acceptor, candidateScore, currentScore)
                jobs[jobId] = Job.ofSchedule(candidate)
            }
        }
    }

    /**
     * Live per-island scores for a comprehensive run.
     *
     * Returns ``{"LA": "0hard/-X", "SA": "...", "TABU": "...", "GD": "..."}``
     * with the latest improvement reported by each island. Missing entries
     * mean that island hasn't fired its first BestSolutionChangedEvent yet.
     * For non-comprehensive jobs (or expired ones) returns an empty map.
     */
    @GET
    @Path("{jobId}/islands")
    @Produces(MediaType.APPLICATION_JSON)
    fun getIslandScores(@PathParam("jobId") jobId: String): Map<String, String> {
        return comprehensiveIslandScores[jobId]?.toMap() ?: emptyMap()
    }

    /**
     * Build a fresh ``SolverConfig`` for one comprehensive island.
     *
     * **Key invariant:** the LA island must run with bit-for-bit the same
     * configuration as the regular ``/schedules`` endpoint, so that
     * ``comprehensive ≥ standalone`` holds by construction. We achieve this
     * by cloning Quarkus's auto-built ``SolverConfig`` (which has all the
     * defaults already wired up: solutionClass, scoreDirectorFactoryConfig,
     * environmentMode, randomSeed from application.properties, etc.) and:
     *
     *   - For the LA island: ONLY override termination. Everything else
     *     stays identical to the regular endpoint — same phase chain, same
     *     default move selectors, same RNG path. The LA island is then
     *     literally a parallel copy of standalone TF-Kotlin.
     *   - For SA / TABU / GD: also override phaseConfigList with an
     *     explicit Local Search phase carrying the requested acceptor.
     *     The construction heuristic phase is left at the default
     *     (FIRST_FIT), inherited from the cloned config.
     */
    /** Map a CH selector string to Timefold's ConstructionHeuristicType. */
    private fun chTypeOf(ch: String): ConstructionHeuristicType = when (ch.uppercase()) {
        "FF" -> ConstructionHeuristicType.FIRST_FIT
        "FFD" -> ConstructionHeuristicType.FIRST_FIT_DECREASING
        "WF" -> ConstructionHeuristicType.WEAKEST_FIT
        "WFD" -> ConstructionHeuristicType.WEAKEST_FIT_DECREASING
        "SF" -> ConstructionHeuristicType.STRONGEST_FIT
        "SFD" -> ConstructionHeuristicType.STRONGEST_FIT_DECREASING
        "CI" -> ConstructionHeuristicType.CHEAPEST_INSERTION
        else -> ConstructionHeuristicType.FIRST_FIT
    }

    /** A construction-heuristic phase of the given type, with its own
     *  per-phase spent-limit (see note below on overshoot). */
    private fun chPhase(ch: String, totalSeconds: Long): ConstructionHeuristicPhaseConfig {
        val cfg = ConstructionHeuristicPhaseConfig().apply {
            terminationConfig = TerminationConfig().apply {
                spentLimit = Duration.ofSeconds(totalSeconds)
            }
        }
        when (ch.uppercase()) {
            "FFD" -> {
                // "First Fit Decreasing" via the VERBOSE form. The bundled
                // ConstructionHeuristicType.FIRST_FIT_DECREASING ignored a
                // phase-level entitySorterManner and its internal
                // DESCENDING_IF_AVAILABLE never engaged our comparator
                // (FFD came out byte-identical to FF). ALLOCATE_ENTITY_FROM_QUEUE
                // has no bundled sorter, so the explicit DESCENDING below is the
                // sole source of ordering and actually uses FlightAssignment's
                // comparatorFactoryClass to place the hardest-to-staff slots
                // first. (docs: "If you need explicit control, use the verbose
                // ALLOCATE_ENTITY_FROM_QUEUE approach".)
                // Перегонные кресла конструктив не заполняет (WorkingSeatsFilter,
                // 05.09.2026): очередь сущностей — только действующие места,
                // порядок — тот же DESCENDING по comparatorFactoryClass.
                cfg.entityPlacerConfig = ai.timefold.solver.core.config.constructionheuristic.placer.QueuedEntityPlacerConfig()
                    .withEntitySelectorConfig(
                        ai.timefold.solver.core.config.heuristic.selector.entity.EntitySelectorConfig()
                            .withFilterClass(org.acme.flighcrewscheduling.solver.WorkingSeatsFilter::class.java)
                            .withSorterManner(EntitySorterManner.DESCENDING))
            }
            else -> {
                val type = chTypeOf(ch)
                if (type == ConstructionHeuristicType.FIRST_FIT) {
                    // FIRST_FIT в развёрнутой форме (очередь сущностей с
                    // умолчаниями Timefold) плюс фильтр перегонных кресел:
                    // для наборов без перегонных кресел — байт-идентично.
                    cfg.entityPlacerConfig = ai.timefold.solver.core.config.constructionheuristic.placer.QueuedEntityPlacerConfig()
                        .withEntitySelectorConfig(
                            ai.timefold.solver.core.config.heuristic.selector.entity.EntitySelectorConfig()
                                .withFilterClass(org.acme.flighcrewscheduling.solver.WorkingSeatsFilter::class.java))
                } else {
                    cfg.constructionHeuristicType = type
                }
            }
        }
        return cfg
    }

    private fun buildSolverConfig(
        acceptor: String,
        totalSeconds: Long,
        skipConstructionHeuristic: Boolean = false,
        ch: String = "FF",
        moves: String? = null,
        filterDeadheadSeats: Boolean = false,
    ): SolverConfig {
        val cloned = SolverConfig(templateSolverConfig)

        cloned.terminationConfig = TerminationConfig().apply {
            spentLimit = Duration.ofSeconds(totalSeconds)
        }

        // Многопоточные ходы — ЯВНЫМ системным свойством
        // (-Dcosmos.move.threads=8|AUTO): умолчание остаётся прежним
        // однопоточным, контуры и сравнимость старых замеров не задеты;
        // включивший отвечает за то, что машине хватает ядер.
        System.getProperty("cosmos.move.threads")?.takeIf { it.isNotBlank() }
            ?.let { cloned.moveThreadCount = it }

        // Крупные ходы (moves=pillar | pillar-rr): явная цепочка фаз;
        // пустое умолчание не трогает прежние пути ни на очко. Тёплый
        // старт (затравленный прогон) пропускает конструктив целиком:
        // на полном наборе конструктив тратит минуты на перебор пустых
        // перегонных кресел (614 ходов/с), которые всё равно оставляет
        // пустыми, — поиск назначает из пустоты сам.
        LargeMoves.lsPhase(moves)?.let { ls ->
            cloned.phaseConfigList = if (skipConstructionHeuristic) listOf(ls)
                else listOf(chPhase(ch, totalSeconds), ls)
            return cloned
        }

        if (acceptor == "LA") {
            if (skipConstructionHeuristic) {
                // Warm-start path: replace template's CH→LS chain with a
                // single LS phase using default LA settings. The template's
                // own LS config isn't easily extractable here, so we rebuild
                // the LA LS phase manually (size=400 matches the template's
                // Quarkus default for our problem).
                cloned.phaseConfigList = listOf(
                    LocalSearchPhaseConfig().apply {
                        acceptorConfig = LocalSearchAcceptorConfig().apply {
                            acceptorTypeList = listOf(AcceptorType.LATE_ACCEPTANCE)
                            lateAcceptanceSize = 400
                        }
                    },
                )
                return cloned
            }
            if (ch.uppercase() != "FF") {
                // Non-default CH with LA: build the chain explicitly so the
                // chosen construction heuristic (e.g. FIRST_FIT_DECREASING)
                // replaces the template's FIRST_FIT. LS = LA size 400 to match
                // the baseline.
                cloned.phaseConfigList = listOf(
                    chPhase(ch, totalSeconds),
                    LocalSearchPhaseConfig().apply {
                        acceptorConfig = LocalSearchAcceptorConfig().apply {
                            acceptorTypeList = listOf(AcceptorType.LATE_ACCEPTANCE)
                            lateAcceptanceSize = 400
                        }
                    },
                )
                return cloned
            }
            if (filterDeadheadSeats) {
                // Пакетный прогон с перегонными креслами (решение владельца
                // 05.09.2026): та же цепочка FIRST_FIT → LA 400, но
                // конструктив не заполняет перегонные кресла
                // (WorkingSeatsFilter) — их занимает только поиск, когда
                // перегон нужен. Первый замер без фильтра: 314 перегонных
                // мест из 594, резерв −54.
                cloned.phaseConfigList = listOf(
                    chPhase(ch, totalSeconds),
                    LocalSearchPhaseConfig().apply {
                        acceptorConfig = LocalSearchAcceptorConfig().apply {
                            acceptorTypeList = listOf(AcceptorType.LATE_ACCEPTANCE)
                            lateAcceptanceSize = 400
                        }
                    },
                )
                return cloned
            }
            // Default cold-start (FF + LA): preserve the exact LA setup the
            // regular endpoint uses. This is the whole point of cloning the
            // template.
            return cloned
        }

        // Non-LA islands need an explicit acceptor in the LS phase. Keep CH
        // at default (FIRST_FIT, which Quarkus uses anyway).
        val acceptorConfig = LocalSearchAcceptorConfig()
        when (acceptor) {
            "SA" -> {
                // SA picks AcceptorType automatically when starting
                // temperature is set; no need to declare it explicitly.
                acceptorConfig.simulatedAnnealingStartingTemperature = "0hard/100soft"
            }
            "TABU" -> {
                // entityTabuSize ~6% of entity count is the rule-of-thumb in
                // the literature for problems of our scale. For 396 entities
                // that's ~25 (vs. the previous default 7 that got us stuck).
                acceptorConfig.entityTabuSize = 25
            }
            "GD" -> {
                acceptorConfig.acceptorTypeList = listOf(AcceptorType.GREAT_DELUGE)
                acceptorConfig.greatDelugeWaterLevelIncrementScore = "0hard/1soft"
            }
            "DLA" -> {
                // Diversified Late Acceptance — variant of LA with explicit
                // diversity penalty; helps escape "flat" local minima where
                // plain LA cycles in place. DLA is a preview feature in
                // Timefold 2.0 and must be explicitly enabled at the
                // SolverConfig level.
                acceptorConfig.acceptorTypeList = listOf(AcceptorType.DIVERSIFIED_LATE_ACCEPTANCE)
                acceptorConfig.lateAcceptanceSize = 400
                cloned.enablePreviewFeatureSet =
                    setOf(PreviewFeature.DIVERSIFIED_LATE_ACCEPTANCE)
            }
            "SCHC" -> {
                // Step Counting Hill Climbing — Hill Climbing with periodic
                // acceptance-level reset every K accepted moves. Parameter-
                // light alternative to LA.
                acceptorConfig.acceptorTypeList = listOf(AcceptorType.STEP_COUNTING_HILL_CLIMBING)
                acceptorConfig.stepCountingHillClimbingSize = 400
            }
            else -> error("unknown acceptor: $acceptor")
        }

        // Warm-start path: skip CH; otherwise CH=FIRST_FIT then LS.
        // CH gets its own per-phase spent-limit equal to the total budget
        // — Timefold's outer spentLimit is checked between CH steps, but
        // a single CH step can take many seconds on a problem with
        // ~9k value range, leading to massive overshoot. Per-phase
        // termination is checked more aggressively. We give CH the full
        // budget; in practice CH finishes long before that and LS picks
        // up the rest. If CH genuinely can't fit in the budget, we'd
        // rather have a partially-initialized solution at deadline than
        // a 30× overshoot.
        cloned.phaseConfigList = if (skipConstructionHeuristic) {
            listOf(
                LocalSearchPhaseConfig().apply { this.acceptorConfig = acceptorConfig },
            )
        } else {
            listOf(
                chPhase(ch, totalSeconds),
                LocalSearchPhaseConfig().apply { this.acceptorConfig = acceptorConfig },
            )
        }
        return cloned
    }

    /**
     * Variant E (Multi-size LA): four LA islands with different memory depths.
     *
     * Smaller sizes converge faster but get stuck more easily; bigger sizes
     * preserve more "old worse" reference points so they keep accepting bigger
     * detours. With 200 / 400 / 800 / 1600 we span a wide range from "greedy
     * LA" to "very tolerant LA" — diversification by parameter rather than by
     * algorithm.
     */
    private fun buildLaConfigWithSize(size: Int, totalSeconds: Long): SolverConfig {
        val cloned = SolverConfig(templateSolverConfig)
        cloned.terminationConfig = TerminationConfig().apply {
            spentLimit = Duration.ofSeconds(totalSeconds)
        }
        cloned.phaseConfigList = listOf(
            ConstructionHeuristicPhaseConfig().apply {
                constructionHeuristicType = ConstructionHeuristicType.FIRST_FIT
            },
            LocalSearchPhaseConfig().apply {
                acceptorConfig = LocalSearchAcceptorConfig().apply {
                    acceptorTypeList = listOf(AcceptorType.LATE_ACCEPTANCE)
                    lateAcceptanceSize = size
                }
            },
        )
        return cloned
    }

    /**
     * Variant H (Multi-phase): each island runs the same internal pipeline
     * (CH → 25% LA → 25% SA → 25% Tabu(25) → 25% LA-polish) but with a
     * different RNG seed so cross-island trajectories diverge.
     *
     * The four phases give every island exposure to all major acceptor
     * styles; cross-island diversity comes from the different seed only.
     * Last LA phase has no per-phase termination, so it picks up the
     * remainder of the total budget.
     */
    private fun buildMultiPhaseConfig(seed: Long, totalSeconds: Long): SolverConfig {
        val cloned = SolverConfig(templateSolverConfig)
        cloned.terminationConfig = TerminationConfig().apply {
            spentLimit = Duration.ofSeconds(totalSeconds)
        }
        cloned.randomSeed = seed

        val phaseSeconds = (totalSeconds / 4).coerceAtLeast(1)

        cloned.phaseConfigList = listOf(
            ConstructionHeuristicPhaseConfig().apply {
                constructionHeuristicType = ConstructionHeuristicType.FIRST_FIT
            },
            // Phase 2: LA — initial workhorse
            LocalSearchPhaseConfig().apply {
                terminationConfig = TerminationConfig().apply {
                    spentLimit = Duration.ofSeconds(phaseSeconds)
                }
                acceptorConfig = LocalSearchAcceptorConfig().apply {
                    acceptorTypeList = listOf(AcceptorType.LATE_ACCEPTANCE)
                    lateAcceptanceSize = 400
                }
            },
            // Phase 3: SA — landscape change
            LocalSearchPhaseConfig().apply {
                terminationConfig = TerminationConfig().apply {
                    spentLimit = Duration.ofSeconds(phaseSeconds)
                }
                acceptorConfig = LocalSearchAcceptorConfig().apply {
                    simulatedAnnealingStartingTemperature = "0hard/100soft"
                }
            },
            // Phase 4: Tabu — explicit memory escape
            LocalSearchPhaseConfig().apply {
                terminationConfig = TerminationConfig().apply {
                    spentLimit = Duration.ofSeconds(phaseSeconds)
                }
                acceptorConfig = LocalSearchAcceptorConfig().apply {
                    entityTabuSize = 25
                }
            },
            // Phase 5: LA polish — finish on the proven workhorse, runs
            // until the outer total-budget terminates.
            LocalSearchPhaseConfig().apply {
                acceptorConfig = LocalSearchAcceptorConfig().apply {
                    acceptorTypeList = listOf(AcceptorType.LATE_ACCEPTANCE)
                    lateAcceptanceSize = 400
                }
            },
        )
        return cloned
    }

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    @Path("{jobId}")
    fun getSchedule(@PathParam("jobId") jobId: String): FlightCrewSchedule {
        val schedule = checkAndGet(jobId)
        schedule.solverStatus = currentSolverStatus(jobId)
        return schedule
    }

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    @Path("{jobId}/status")
    fun getStatus(@PathParam("jobId") jobId: String): FlightCrewSchedule {
        val schedule = checkAndGet(jobId)
        return FlightCrewSchedule(schedule.score, currentSolverStatus(jobId))
    }

    @DELETE
    @Produces(MediaType.APPLICATION_JSON)
    @Path("{jobId}")
    fun terminate(@PathParam("jobId") jobId: String): FlightCrewSchedule {
        // Comprehensive jobs aren't tracked by the SolverManager, so we
        // terminate each registered island solver explicitly.
        val islands = comprehensiveSolvers[jobId]
        if (islands != null) {
            // Flag first so interval-based runners won't start another
            // interval, then stop the currently running island solvers.
            cancelledJobs.add(jobId)
            synchronized(islands) {
                islands.forEach { runCatching { it.terminateEarly() } }
            }
        }
        solverManager.terminateEarly(jobId)
        return getSchedule(jobId)
    }

    /**
     * Compute the right SolverStatus for a job: comprehensive runs aren't
     * registered with the SolverManager, so we fall back to checking the
     * comprehensive bookkeeping maps.
     */
    private fun currentSolverStatus(jobId: String): SolverStatus {
        if (comprehensiveLocks.containsKey(jobId)) return SolverStatus.SOLVING_ACTIVE
        return solverManager.getSolverStatus(jobId)
    }

    private fun checkAndGet(jobId: String): FlightCrewSchedule {
        val j = jobs[jobId] ?: throw ScheduleSolverException(jobId, Response.Status.NOT_FOUND, "No schedule found.")
        j.exception?.let { throw ScheduleSolverException(jobId, it) }
        return j.schedule!!
    }

    private fun cleanJobs() {
        if (jobs.size <= maxJobsCacheSize) return
        val toRemove = jobs.entries
            .filter { solverManager.getSolverStatus(it.key) == SolverStatus.NOT_SOLVING }
            .filter { !comprehensiveLocks.containsKey(it.key) }  // don't evict still-running comprehensive jobs
            .filter { it.value.schedule != null }
            .sortedBy { it.value.createdAt }
            .map { it.key }
        for (i in 0 until (toRemove.size - maxJobsCacheSize).coerceAtLeast(0)) {
            val key = toRemove[i]
            jobs.remove(key)
            // Also drop side-state for evicted jobs so per-island map doesn't
            // leak entries forever.
            comprehensiveIslandScores.remove(key)
        }
    }
}
