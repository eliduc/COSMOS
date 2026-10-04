package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.ObjectMapper
import org.jboss.logging.Logger
import java.nio.file.Files
import java.nio.file.Path

/**
 * Multi-airline tier-1 rule parameters for the GERAD constraint profiles.
 *
 * Loaded ONCE at class initialization:
 *  - if env CREW_RULES_CONFIG points to an existing JSON file, the six
 *    parameters are read from it (either a flat object or wrapped as
 *    {"parameters": {...}}, matching webapp/config/airline_default.json);
 *    absent keys keep their defaults; unknown keys, non-integer values or
 *    malformed JSON FAIL LOUDLY with IllegalStateException — a config that
 *    exists but cannot be trusted must never be silently ignored;
 *  - if the env var is unset (or points to a missing file) the BUILT-IN
 *    DEFAULTS below apply. They are EXACTLY the historical hard-coded
 *    constants of the GERAD providers, so the no-config behavior and all
 *    artifacts stay byte-for-byte identical (reproducibility contract).
 *
 * Constraint NAMES (e.g. "G Min sit 60m", "G Monthly 85h") are stable
 * identifiers in downstream artifacts and deliberately do NOT change with
 * the configured values.
 */
object RulesConfig {

    const val ENV_VAR = "CREW_RULES_CONFIG"

    private val KNOWN_KEYS = setOf(
        "min_sit_min",
        "max_landings_per_duty",
        "max_duty_span_min",
        "min_rest_min",
        "max_monthly_block_min",
        "max_pairing_span_min",
        // crew-composition rules (multirole increment 4) — 0 = OFF
        "comp_no_two_reserves",
        "comp_min_pair_seniority_weight",
        "comp_language_mix",
    )

    /** The tier-1 rule parameters + crew-composition rules. Defaults ==
     *  the former hard-coded constants; composition rules default OFF (0)
     *  so the no-config behavior stays byte-for-byte identical. */
    data class Values(
        val minSitMin: Int = 30,
        val maxLandingsPerDuty: Int = 5,
        val maxDutySpanMin: Int = 14 * 60,        // 840
        val minRestMin: Int = 10 * 60,            // 600
        val maxMonthlyBlockMin: Int = 85 * 60,    // 5100
        val maxPairingSpanMin: Int = 4 * 24 * 60, // 5760
        val compNoTwoReserves: Int = 0,           // 1 = enabled
        val compMinPairSeniorityWeight: Int = 0,  // >0 = min pair weight sum
        val compLanguageMix: Int = 0,             // 1 = enabled
    )

    val DEFAULTS = Values()

    private var values: Values = initialize()

    /**
     * Свидетельство ПОТРЕБИТЕЛЯ (пятый аудит, пакет B).
     *
     * Отпечаток конфигурации доказывает, что значения решателю ПЕРЕДАЛИ.
     * Он не доказывает, что решатель их ПРИМЕНИЛ: параметр, который никто
     * не читает, даёт тот же отпечаток, что и работающий. Разница
     * существенна ровно там, где дороже всего ошибиться, — в правиле,
     * которое заказчик считает соблюдённым.
     *
     * Поэтому каждое чтение параметра записывается ЗДЕСЬ, в самом
     * геттере: наружу уходит не то, что мы собирались применить, а то,
     * что констрейнт-провайдер фактически прочитал, и ровно то значение,
     * которое он получил. Потребитель свидетельствует о себе сам.
     *
     * Предел назван вслух: запись доказывает чтение, а не то, что
     * прочитанное повлияло на счёт. Значение неизменяемо после загрузки
     * (кроме тестового хука), поэтому «прочитал» и «применил» здесь
     * совпадают — но это свойство кода, а не логическая необходимость.
     */
    private val readings = java.util.concurrent.ConcurrentHashMap<String, Int>()

    private fun <T : Int> note(key: String, v: T): T {
        // Запись ТОЛЬКО при изменении. Правила состава читаются внутри
        // фильтров, то есть на каждом ходе решателя: у набора заказчика
        // на участке обычно несколько мест, пары со-локации существуют, и
        // фильтр работает постоянно. Безусловная запись означала бы
        // состязание потоков за одну ячейку в многоостровных прогонах, а
        // решатель ограничен ВРЕМЕНЕМ — замедление меняло бы ростер при
        // том же бюджете. Значение после загрузки неизменно, поэтому в
        // установившемся режиме здесь остаётся одно чтение без блокировок.
        if (readings[key] != v) readings[key] = v
        return v
    }

    /** Что фактически прочитано с момента последнего сброса. */
    fun appliedReadings(): Map<String, Int> = java.util.TreeMap(readings)

    /** Сброс записи (используется тестами и между прогонами). */
    fun resetReadings() { readings.clear() }

    /** Test hook: constraint tests must exercise the composition rules
     *  without re-initializing the singleton (values are loaded once per
     *  JVM). Production code never calls this. */
    internal fun overrideForTests(v: Values) { values = v }

    val minSitMin: Int get() = note("min_sit_min", values.minSitMin)
    val maxLandingsPerDuty: Int
        get() = note("max_landings_per_duty", values.maxLandingsPerDuty)
    val maxDutySpanMin: Int
        get() = note("max_duty_span_min", values.maxDutySpanMin)
    val minRestMin: Int get() = note("min_rest_min", values.minRestMin)
    val maxMonthlyBlockMin: Int
        get() = note("max_monthly_block_min", values.maxMonthlyBlockMin)
    val maxPairingSpanMin: Int
        get() = note("max_pairing_span_min", values.maxPairingSpanMin)
    val compNoTwoReserves: Int
        get() = note("comp_no_two_reserves", values.compNoTwoReserves)
    val compMinPairSeniorityWeight: Int
        get() = note("comp_min_pair_seniority_weight",
            values.compMinPairSeniorityWeight)
    val compLanguageMix: Int
        get() = note("comp_language_mix", values.compLanguageMix)

    private fun initialize(): Values {
        val log = Logger.getLogger(RulesConfig::class.java)
        val env = System.getenv(ENV_VAR)
        if (env.isNullOrBlank()) {
            log.infof("RulesConfig: %s unset -> built-in defaults %s",
                ENV_VAR, DEFAULTS)
            return DEFAULTS
        }
        val path = Path.of(env)
        if (!Files.isRegularFile(path)) {
            log.infof("RulesConfig: %s=%s does not exist -> built-in defaults %s",
                ENV_VAR, env, DEFAULTS)
            return DEFAULTS
        }
        val loaded = loadFrom(path)
        log.infof("RulesConfig: loaded from %s=%s -> %s", ENV_VAR, env, loaded)
        return loaded
    }

    /** Parse a rules-config JSON file. Internal so unit tests can exercise
     *  parsing and validation without manipulating process env vars. */
    internal fun loadFrom(path: Path): Values {
        val raw = try {
            ObjectMapper().readValue(Files.readAllBytes(path), Map::class.java)
        } catch (e: IllegalStateException) {
            throw e
        } catch (e: Exception) {
            throw IllegalStateException(
                "Malformed rules config at $path: ${e.message}", e)
        } ?: throw IllegalStateException(
            "Rules config at $path is not a JSON object")
        return fromMap(raw)
    }

    /** Validate and convert a parsed JSON map ({"parameters": {...}} or
     *  flat). Unknown keys and non-integer values are loud errors. */
    internal fun fromMap(raw: Map<*, *>): Values {
        val params: Map<*, *> =
            if (raw.containsKey("parameters")) {
                val extras = raw.keys.map { it.toString() } - "parameters"
                if (extras.isNotEmpty()) {
                    throw IllegalStateException(
                        "Rules config: unknown top-level keys $extras "
                            + "alongside 'parameters'")
                }
                raw["parameters"] as? Map<*, *>
                    ?: throw IllegalStateException(
                        "Rules config: 'parameters' must be a JSON object")
            } else {
                raw
            }
        val unknown = params.keys.map { it.toString() } - KNOWN_KEYS
        if (unknown.isNotEmpty()) {
            throw IllegalStateException(
                "Rules config: unknown parameter keys $unknown "
                    + "(known: ${KNOWN_KEYS.sorted()})")
        }
        fun intOf(key: String, default: Int): Int {
            if (!params.containsKey(key)) return default
            val v = params[key]
            val n = v as? Number ?: throw IllegalStateException(
                "Rules config: '$key' must be an integer number, got: $v")
            if (n.toDouble() != n.toInt().toDouble()) {
                throw IllegalStateException(
                    "Rules config: '$key' must be an integer, got: $v")
            }
            return n.toInt()
        }
        // composition keys allow 0 (= OFF); flags are strictly 0/1; the pair
        // threshold is capped at 22 (= two weights of 11) — parity with the
        // Python validator (review 2026-07-19)
        fun compOf(key: String, default: Int, flag: Boolean): Int {
            val v = intOf(key, default)
            val max = if (flag) 1 else 22
            if (v < 0 || v > max) {
                throw IllegalStateException(
                    "Rules config: '$key' must be "
                        + (if (flag) "0 or 1" else "an integer 0..22")
                        + ", got: $v")
            }
            return v
        }
        return Values(
            minSitMin = intOf("min_sit_min", DEFAULTS.minSitMin),
            maxLandingsPerDuty =
                intOf("max_landings_per_duty", DEFAULTS.maxLandingsPerDuty),
            maxDutySpanMin = intOf("max_duty_span_min", DEFAULTS.maxDutySpanMin),
            minRestMin = intOf("min_rest_min", DEFAULTS.minRestMin),
            maxMonthlyBlockMin =
                intOf("max_monthly_block_min", DEFAULTS.maxMonthlyBlockMin),
            maxPairingSpanMin =
                intOf("max_pairing_span_min", DEFAULTS.maxPairingSpanMin),
            compNoTwoReserves = compOf(
                "comp_no_two_reserves", DEFAULTS.compNoTwoReserves, true),
            compMinPairSeniorityWeight = compOf(
                "comp_min_pair_seniority_weight",
                DEFAULTS.compMinPairSeniorityWeight, false),
            compLanguageMix = compOf(
                "comp_language_mix", DEFAULTS.compLanguageMix, true),
        )
    }
}
