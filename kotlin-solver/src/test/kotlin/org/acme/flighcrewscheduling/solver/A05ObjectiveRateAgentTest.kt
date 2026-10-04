package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.solver.SolverFactory
import ai.timefold.solver.core.config.solver.SolverConfig
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.Arguments
import org.junit.jupiter.params.provider.MethodSource
import org.junit.jupiter.params.provider.ValueSource
import java.io.ByteArrayOutputStream
import java.math.BigDecimal
import java.security.MessageDigest
import java.util.stream.Stream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * Synthetic, actual ZIP -> TaskPackage.readBytes -> TaskModes.resolve controls.
 * The rate is a price per metric unit, independent of objective tier 1.
 *
 * The bounded score-only cases below additionally calculate the real provider
 * score. No search, HTTP, catalogue certification or external service is run;
 * they do not replace the Python producer/receiver projection gate.
 * Both JSON bodies carry the same source objective; their exact UTF-8 bytes
 * are hashed in the manifest. No loader, resolver, or verdict is mocked.
 *
 * The current score API takes a Long weight. These tests require positive
 * integral values to remain exact through that API, and reject fractional or
 * unrepresentable selected rates rather than inventing a scale or rounding.
 * A valid fractional rate of an inactive objective is not a feasibility input.
 */
class A05ObjectiveRateAgentTest {
    companion object {
        private const val OBJECTIVE_ID = "O-ROST"
        private const val SPREAD = "Credit hours spread"
        private const val PENALTY = "Layover nights away from home"

        @JvmStatic
        fun integralRates(): Stream<Arguments> = Stream.of(
            Arguments.of("1", 1L),
            Arguments.of("7", 7L),
            Arguments.of("7.0", 7L),
            Arguments.of("2147483648", 2147483648L),
            Arguments.of("9007199254740993", 9007199254740993L),
            Arguments.of("9007199254740993.0", 9007199254740993L),
            Arguments.of("9223372036854775807", Long.MAX_VALUE),
        )

        @JvmStatic
        fun invalidRateCarriers(): Stream<Arguments> = Stream.of(
            Arguments.of("null carrier", "null"),
            Arguments.of("scalar carrier", "7"),
            Arguments.of("array carrier", "[7]"),
            Arguments.of("missing value", "{}"),
            Arguments.of("null value", """{"value":null}"""),
            Arguments.of("true value", """{"value":true}"""),
            Arguments.of("false value", """{"value":false}"""),
            Arguments.of("string value", """{"value":"7"}"""),
            Arguments.of("object value", """{"value":{"amount":7}}"""),
            Arguments.of("array value", """{"value":[7]}"""),
            Arguments.of("zero", """{"value":0}"""),
            Arguments.of("negative", """{"value":-7}"""),
            Arguments.of("above Long maximum", """{"value":9223372036854775808}"""),
            Arguments.of("below Long minimum", """{"value":-9223372036854775809}"""),
            Arguments.of("large exponent", """{"value":1e100}"""),
        )
    }

    private fun sha256(body: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(body)
            .joinToString("") { "%02x".format(it) }

    private fun rate(value: String): String =
        """{"value":$value,"per":"hour of spread","as_written":"$value per hour of spread"}"""

    /** Null here means ABSENT legacy field, not the explicit JSON null carrier. */
    private fun packageBytes(
        rateJson: String?,
        metric: String? = "credit_hours_spread",
        useTable: Boolean = false,
        sharedPenalty: Boolean = false,
        legacyLanding: Boolean = false,
    ): ByteArray {
        val rateField = if (rateJson == null) "" else """, "rate": $rateJson"""
        val metricField = if (metric == null) "" else """, "metric": "$metric""""
        val ruleMapsField = if (useTable || sharedPenalty) """
            "rule_maps":{
              "objective_to_constraint":{"$OBJECTIVE_ID":"$SPREAD"},
              "rule_to_constraint":{"R-AGENT-SOFT":"${if (sharedPenalty) SPREAD else PENALTY}"}},
        """.trimIndent() else ""
        val sourceObjective = """
            {"id":"$OBJECTIVE_ID","title":"Synthetic credit spread",
             "statement":"Synthetic objective: the rate is an explicit carrier",
             "zone":"ROSTERING","direction":"minimize",
             "tier":1$metricField,
             "sources":[{"sid":"S-AGENT"}]$rateField}
        """.trimIndent()
        // This class exercises objective prices, not archival landing admission.
        // Declare the current synthetic landing explicitly; never enable legacy
        // compatibility or relax the production reader to reach the rate checks.
        val landing = if (legacyLanding) "" else """,
          "template_fit":{"schema_version":"landing/2","category":"A",
            "family":"layover_penalty","registry_rev":"${"a".repeat(64)}",
            "family_contract":{"family_id":"layover_penalty",
              "catalog_version":"synthetic-rate-fixture",
              "catalog_fingerprint":"${"a".repeat(64)}",
              "record_fingerprint":"${"b".repeat(64)}","param_set":"default"},
            "params":{}}
        """.trimIndent()
        val model = """
            {"meta":{"revision":3,"model_schema":2},
             "sources":[{"sid":"S-AGENT","title":"Synthetic input"}],
             "rules":[{"id":"R-AGENT-SOFT","kind":"SOFT","zone":"ROSTERING",
                       "export_solver":{"family":"layover_penalty"}$landing}],
             "objectives":[$sourceObjective]}
        """.trimIndent().toByteArray(Charsets.UTF_8)
        val objective = """
            {"customer":"synthetic-rate-agent",
             "pairing":{"direction":"minimize","components":[],"objectives":[]},
             "rostering":{"direction":"minimize","objectives":[$sourceObjective],
               "components":[{"rule_id":"R-AGENT-SOFT","weight":3}]}}
        """.trimIndent().toByteArray(Charsets.UTF_8)
        val manifest = """
            {"spec_version":"1.20","customer":"synthetic-rate-agent",
             "source_run":"synthetic-objective-rate","revision":3,"readiness":"в",
             "files":[{"name":"model.json","sha256":"${sha256(model)}"},
                      {"name":"objective.json","sha256":"${sha256(objective)}"}],
             $ruleMapsField
             "allowed_modes":{"pairing":["feasibility-hard"],
               "rostering":["feasibility-hard","feasibility-soft","optimization"]},
             "objectives":{"pairing":[],"rostering":["$OBJECTIVE_ID"]},
             "has_data":true}
        """.trimIndent().toByteArray(Charsets.UTF_8)
        val buffer = ByteArrayOutputStream()
        ZipOutputStream(buffer).use { zip ->
            for ((name, bytes) in linkedMapOf(
                "manifest.json" to manifest,
                "model.json" to model,
                "objective.json" to objective,
            )) {
                zip.putNextEntry(ZipEntry(name))
                zip.write(bytes)
                zip.closeEntry()
            }
        }
        return buffer.toByteArray()
    }

    @Test
    fun unversionedLandingStillRefusesBeforeObjectiveExecution() {
        val error = assertThrows(TaskPackageError::class.java) {
            TaskPackage.readBytes(packageBytes(rate("7"), legacyLanding = true))
        }
        assertTrue(error.message.orEmpty().contains("landing-version"), error.message)
    }

    private fun optimize(
        rateJson: String?,
        metric: String? = "credit_hours_spread",
        useTable: Boolean = false,
        sharedPenalty: Boolean = false,
    ): ResolvedTaskMode =
        TaskModes.resolve(
            MODE_OPTIMIZATION,
            TaskPackage.readBytes(packageBytes(rateJson, metric, useTable, sharedPenalty)),
            "rostering", OBJECTIVE_ID,
        )

    private fun assertExactWeight(
        result: ResolvedTaskMode,
        expected: Long,
        expectedMetric: String? = "credit_hours_spread",
    ) {
        assertEquals(MODE_OPTIMIZATION, result.mode)
        assertNotNull(result.objective)
        val objective = result.objective!!
        assertEquals(OBJECTIVE_ID, objective.id)
        assertEquals(expectedMetric, objective.metric)
        assertEquals("minimize", objective.direction)
        assertEquals(SPREAD, objective.constraint)
        assertEquals(expected, objective.weight, "price per hour is not the tier")
        assertEquals(expected, result.effectiveWeights[SPREAD])
        assertFalse(objective.sharesConstraintWithPenalty)
        assertEquals(3L, result.appliedWeights[PENALTY])
        assertEquals(3L, result.effectiveWeights[PENALTY])
        assertNull(result.appliedWeights[SPREAD], "the objective is not a SOFT rule")
        assertNotNull(result.weightOverrides)
    }

    private fun assertNamedRateRefusal(
        rateJson: String,
        label: String,
        metric: String? = "credit_hours_spread",
        useTable: Boolean = false,
        sharedPenalty: Boolean = false,
    ) {
        val error = runCatching {
            optimize(rateJson, metric, useTable, sharedPenalty)
        }.exceptionOrNull()
        assertNotNull(error, "$label must not become an implicit objective weight 1")
        assertTrue(error is TaskPackageError || error is TaskModeRefused,
            "$label must fail at the package/mode boundary, not incidentally: $error")
        val words = error!!.message.orEmpty()
        assertTrue(words.contains(OBJECTIVE_ID), "the affected objective is missing: $words")
        assertTrue(words.contains("rate", ignoreCase = true) ||
            words.contains("ставк", ignoreCase = true),
            "the refused carrier is missing: $words")
    }

    @ParameterizedTest(name = "rate {0}, tier 1 -> exact objective weight {1}")
    @MethodSource("integralRates")
    fun integralRateReachesActualMode(value: String, expected: Long) {
        assertExactWeight(optimize(rate(value)), expected)
    }

    @Test
    fun missingLegacyRatePreservesUnitWeight() {
        assertExactWeight(optimize(null), 1L)
    }

    @Test
    fun fractionalSelectedRateIsRefusedWithoutRoundingOrInventingScale() {
        // Long/HardSoftScore cannot carry 1.5 exactly without a new scaling contract.
        assertNamedRateRefusal(rate("1.5"), "fractional rate 1.5")
    }

    @Test
    fun fractionalDigitsMustNotRoundThroughDoubleToIntegralOne() {
        assertNamedRateRefusal(rate("1.000000000000000000000001"),
            "fractional precision beyond Double")
    }

    @ParameterizedTest(name = "{0} must not silently become rate 1")
    @MethodSource("invalidRateCarriers")
    fun malformedAndUnrepresentableSelectedRatesRefuse(label: String, carrier: String) {
        assertNamedRateRefusal(carrier, label)
    }

    @ParameterizedTest
    @ValueSource(strings = ["missing", "unknown_metric"])
    fun tableFallbackPreservesRateSeven(metricCase: String) {
        val metric = if (metricCase == "missing") null else metricCase
        val result = optimize(rate("7"), metric, useTable = true)
        assertExactWeight(result, 7L, metric)
        assertTrue(result.objective!!.executorSource.contains("карта пакета"))
    }

    @ParameterizedTest
    @ValueSource(strings = ["missing", "unknown_metric"])
    fun tableFallbackCannotBypassFractionalRateRefusal(metricCase: String) {
        val metric = if (metricCase == "missing") null else metricCase
        assertNamedRateRefusal(rate("1.5"), "fractional table fallback",
            metric, useTable = true)
    }

    @Test
    fun appliedReportExplainsRateValueAndExactSourceInsteadOfTier() {
        val result = optimize(rate("7"))
        val objective = LastRun.snapshotOf(result)["objective"] as Map<*, *>
        assertEquals(7L, objective["weight"])
        assertEquals(BigDecimal("7"), objective["rate"])
        val source = objective["weightSource"].toString()
        assertTrue(source.contains("objective.json"), source)
        assertTrue(source.contains(OBJECTIVE_ID), source)
        assertTrue(source.contains("rate.value"), source)
    }

    @Test
    fun appliedReportNamesLegacyMissingRate() {
        val objective = LastRun.snapshotOf(optimize(null))["objective"] as Map<*, *>
        assertEquals(1L, objective["weight"])
        assertNull(objective["rate"])
        val source = objective["weightSource"].toString()
        assertTrue(source.contains("legacy"), source)
        assertTrue(source.contains("rate"), source)
    }

    @Test
    fun sharedConstraintAddsRateAndPenaltyWithoutConfusingTheirSources() {
        val result = optimize(rate("7"), sharedPenalty = true)
        assertEquals(7L, result.objective!!.weight)
        assertTrue(result.objective!!.sharesConstraintWithPenalty)
        assertEquals(3L, result.appliedWeights[SPREAD])
        assertEquals(10L, result.effectiveWeights[SPREAD])
    }

    @Test
    fun sharedConstraintOverflowRefusesBeforeWeightOverrides() {
        assertNamedRateRefusal(rate(Long.MAX_VALUE.toString()),
            "objective rate plus shared penalty overflow", sharedPenalty = true)
    }

    @ParameterizedTest
    @ValueSource(strings = ["feasibility-hard", "feasibility-soft"])
    fun inactiveFractionalObjectiveDoesNotChangeFeasibility(mode: String) {
        // A positive fractional rate is legal model data, but is not selected here.
        // Malformed JSON/carriers are deliberately NOT required to pass a global gate.
        val packageWithFractionalRate = TaskPackage.readBytes(packageBytes(rate("1.5")))
        val result = TaskModes.resolve(mode, packageWithFractionalRate, "rostering")
        assertEquals(mode, result.mode)
        assertNull(result.objective)
        assertEquals(0L, result.effectiveWeights[SPREAD])
        assertEquals(mode == MODE_FEASIBILITY_HARD, result.stopAtFeasible)
        if (mode == MODE_FEASIBILITY_HARD) {
            assertTrue(result.appliedWeights.isEmpty())
            assertEquals(0L, result.effectiveWeights[PENALTY])
        } else {
            assertEquals(3L, result.appliedWeights[PENALTY])
            assertEquals(3L, result.effectiveWeights[PENALTY])
        }
    }

    /** Real complete provider session, but no solver/search/server. The pinned
     * world has credits 120/240 minutes; an idle employee increases the metric
     * from one to two whole hours. Expected scores are independent arithmetic. */
    private fun actualSpreadScore(resolved: ResolvedTaskMode, idle: Boolean, balanced: Boolean = false): Long {
        val base = Fixtures.airport("BSE")
        val out = Fixtures.airport("OUT")
        val a = Fixtures.employee("A").apply { homeAirport = base }
        val b = Fixtures.employee("B").apply { homeAirport = base }
        val staff = mutableListOf(a, b)
        if (idle) staff.add(Fixtures.employee("C").apply { homeAirport = base })
        val assignments = listOf(
            Fixtures.assignment("s1", Fixtures.flight("F1", base, Fixtures.t(1, 8), out, Fixtures.t(1, 10)), a),
            Fixtures.assignment("s2", Fixtures.flight("F2", base, Fixtures.t(2, 8), out, Fixtures.t(2, if (balanced) 10 else 12)), b),
        )
        val problem = Fixtures.schedule(listOf(base, out), staff, assignments)
        problem.constraintWeightOverrides = resolved.weightOverrides!!
        val config = SolverConfig()
            .withSolutionClass(FlightCrewSchedule::class.java)
            .withEntityClasses(FlightAssignment::class.java)
            .withConstraintProviderClass(FlightCrewSchedulingConstraintProvider::class.java)
        val factory = SolverFactory.create<FlightCrewSchedule>(config)
            as ai.timefold.solver.core.impl.solver.DefaultSolverFactory<FlightCrewSchedule>
        val director = factory.getScoreDirectorFactory<HardSoftScore>()
            .createScoreDirectorBuilder()
            .withConstraintMatchPolicy(ai.timefold.solver.core.impl.score.constraint.ConstraintMatchPolicy.ENABLED)
            .build()
        try {
            director.setWorkingSolution(problem)
            director.calculateScore()
            val totals = director.constraintMatchTotalMap
            val selected = totals.entries.singleOrNull { it.key.id().endsWith(SPREAD) }
            if (resolved.effectiveWeights[SPREAD] != 0L && !balanced) {
                assertNotNull(selected, "missing objective match: " + totals.entries.joinToString { "${it.key.id()}=${it.value.score}" })
            }
            return selected?.value?.score?.softScore() ?: 0L
        } finally {
            director.close()
            RunThresholds.clearForTests()
            LastProviderReadings.clearForTests()
        }
    }

    @ParameterizedTest
    @ValueSource(longs = [1, 7, 9007199254740993, 4611686018427387903, 4611686018427387904])
    fun exactPackageRateReachesRealScore(weight: Long) {
        val resolved = optimize(rate(weight.toString()))
        assertEquals(-weight, actualSpreadScore(resolved, idle = false))
        assertEquals(java.math.BigInteger.valueOf(weight).negate().multiply(java.math.BigInteger.TWO).longValueExact(),
            actualSpreadScore(resolved, idle = true))
    }

    @Test
    fun inactiveObjectiveHasZeroActualScoreAndSharedPenaltyAddsExactly() {
        val pkg = TaskPackage.readBytes(packageBytes(rate("7")))
        assertEquals(0L, actualSpreadScore(
            TaskModes.resolve(MODE_FEASIBILITY_HARD, pkg, "rostering"), idle = true))
        assertEquals(-20L, actualSpreadScore(optimize(rate("7"), sharedPenalty = true), idle = true))
    }

    @Test
    fun unrepresentableObjectiveScoreMustRefuseInsteadOfRewardingPenalty() {
        val resolved = optimize(rate(Long.MAX_VALUE.toString()))
        val error = assertThrows(RuntimeException::class.java) {
            actualSpreadScore(resolved, idle = true)
        }
        // Timefold 2 guards the weighted product with multiplyExact; it wraps
        // the arithmetic refusal with the addressed constraint in Scorer.
        // Do not invent a product-level TaskModeRefused that this API does not promise.
        assertTrue(generateSequence<Throwable>(error) { it.cause }
            .any { it is ArithmeticException && it.message.orEmpty().contains("overflow") })
        assertTrue(error.message.orEmpty().contains(SPREAD))
    }

    @Test
    fun maximumRateStillWorksForOneUnitAndZeroMetric() {
        val resolved = optimize(rate(Long.MAX_VALUE.toString()))
        assertEquals(-Long.MAX_VALUE, actualSpreadScore(resolved, idle = false))
        assertEquals(0L, actualSpreadScore(resolved, idle = false, balanced = true))
    }
}
