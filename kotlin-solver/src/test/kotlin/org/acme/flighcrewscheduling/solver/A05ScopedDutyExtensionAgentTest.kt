package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.stream.test.ConstraintVerifier
import com.fasterxml.jackson.databind.ObjectMapper
import org.acme.flighcrewscheduling.domain.DutyExtensionConsent
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.solver.Fixtures.airport
import org.acme.flighcrewscheduling.solver.Fixtures.assignment
import org.acme.flighcrewscheduling.solver.Fixtures.employee
import org.acme.flighcrewscheduling.solver.Fixtures.flight
import org.acme.flighcrewscheduling.solver.Fixtures.t
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.Arguments
import org.junit.jupiter.params.provider.MethodSource
import org.junit.jupiter.params.provider.ValueSource
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import java.util.stream.Stream

/** Actual package reader -> resolution -> provider -> ConstraintVerifier, never solve.
 * The in-memory archive is a Kotlin consumer contract fixture, not a Python/catalogue acceptance.
 * SPEC 1.15 addition 5: an extension for one base must not raise another base's hard limit.
 */
class A05ScopedDutyExtensionAgentTest {
    companion object {
        @JvmStatic
        fun malformedCapValues(): Stream<Arguments> = listOf("fit", "legacy").flatMap { carrier ->
            listOf("780.5", "780.0", "true", "\"780\"", "null", "2147483648", "0", "-1")
                .map { value -> Arguments.of(carrier, value) }
        }.stream()
    }

    @AfterEach
    fun cleanup() {
        RunThresholds.clearForTests()
        LastProviderReadings.clearForTests()
    }

    // Current wire, explicitly synthetic provenance. Domain values and negative
    // controls remain the original ones; no legacy compatibility is enabled.
    private fun landing(family: String, cells: String) = """{
        "schema_version":"landing/2","category":"A","family":"$family",
        "registry_rev":"${"a".repeat(64)}","family_contract":{
          "family_id":"$family","catalog_version":"synthetic-scope-fixture",
          "catalog_fingerprint":"${"a".repeat(64)}",
          "record_fingerprint":"${"b".repeat(64)}","param_set":"default"},
        "params":{$cells}}"""

    private fun cell(rule: String, name: String, value: String,
                     unit: String = "duration_minutes", relation: String = "max") = """"$name":{
        "value":$value,"unit":"$unit","relation":"$relation",
        "condition":null,"quote":"synthetic $name",
        "source":{"kind":"rule","rule_id":"$rule"}}"""

    private fun extensionRule(id: String = "EXT", consent: String? = "true",
                              bases: String? = "\"A\"", cap: Int = 780,
                              capLiteral: String = cap.toString()): String {
        val conditions = listOfNotNull(
            bases?.let { cell(id, "extension_bases", it, if (it.startsWith("[")) "code_list" else "code", "eq") },
            consent?.let { cell(id, "extension_consent", it, "flag", "eq") })
        val cells = listOf(cell(id, "long_duty_threshold", "720"),
            cell(id, "duty_extension_cap", capLiteral)) + conditions
        return """{"id":"$id","kind":"SOFT",
            "template_fit":${landing("duty_extension_penalty", cells.joinToString(","))}}"""
    }

    private fun packageFor(consent: String? = "true", bases: String? = "\"A\"",
                           extraRules: List<String> = emptyList(),
                           baseValue: Int? = 720): LoadedTaskPackage {
        val model = """{"meta":{"revision":1,"model_schema":2},"rules":[
            ${if (baseValue == null) "" else """
            {"id":"BASE","kind":"HARD","template_fit":${landing("duty_limits", listOf(
                cell("BASE", "max_duty_duration", baseValue.toString()),
                cell("BASE", "min_rest_home", "720", relation = "min"),
                cell("BASE", "min_rest_outstation", "720", relation = "min")).joinToString(","))},
             "params":[{"name":"max_duty_duration",
             "value":$baseValue,"unit":"duration_minutes","relation":"max"},
             {"name":"min_rest_home","value":720,"unit":"duration_minutes","relation":"min"},
             {"name":"min_rest_outstation","value":720,"unit":"duration_minutes","relation":"min"}]},
            """}
            ${(listOf(extensionRule(consent = consent, bases = bases)) + extraRules).joinToString(",")}
            ]}""".trimIndent()
        val objective = """{"pairing":{"ready":true,"direction":"minimize","components":[]},
            "rostering":{"ready":true,"direction":"minimize","components":[]}}"""
        fun sha(s: String) = MessageDigest.getInstance("SHA-256").digest(s.toByteArray())
            .joinToString("") { "%02x".format(it) }
        val manifest = """{"spec_version":"1.21","customer":"unit-contract","source_run":"r3-unit",
            "revision":1,"readiness":"в","has_data":true,
            "allowed_modes":{"pairing":["feasibility-hard"],"rostering":["feasibility-hard"]},
            "objectives":{"pairing":[],"rostering":[]},"files":[
            {"name":"model.json","sha256":"${sha(model)}"},
            {"name":"objective.json","sha256":"${sha(objective)}"}]}"""
        val bytes = ByteArrayOutputStream()
        ZipOutputStream(bytes).use { zip ->
            for ((name, content) in mapOf("manifest.json" to manifest,
                    "model.json" to model, "objective.json" to objective)) {
                zip.putNextEntry(ZipEntry(name))
                zip.write(content.toByteArray())
                zip.closeEntry()
            }
        }
        return TaskPackage.readBytes(bytes.toByteArray())
    }

    private fun verifier(consent: String = "true"):
        ConstraintVerifier<FlightCrewSchedulingConstraintProvider, FlightCrewSchedule> {
        return verifier(packageFor(consent))
    }

    private fun provider(pkg: LoadedTaskPackage): FlightCrewSchedulingConstraintProvider {
        val res = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        return RunThresholds.withRunModel(res.applied, emptyList(), packaged = true) {
            FlightCrewSchedulingConstraintProvider()
        }
    }

    private fun verifier(pkg: LoadedTaskPackage) =
        ConstraintVerifier.build(provider(pkg), FlightCrewSchedule::class.java,
            FlightAssignment::class.java)

    private fun duty(base: String, minutes: Long = 780): FlightAssignment {
        val emp = employee("E").apply { homeAirport = airport(base) }
        // Departure airport is A even for an employee whose home base is B.
        return assignment("a", flight("f", airport("A"), t(2, 6), airport("A"),
            t(2, 6).plusMinutes(minutes)), emp)
    }

    private fun consent(a: FlightAssignment, yes: Boolean): DutyExtensionConsent =
        DutyExtensionConsent().apply {
            start = a.flight!!.departureUTCDateTime.toString()
            end = a.flight!!.arrivalUTCDateTime.toString()
            consent = yes
            source = "Смены!row2"
        }

    private fun cap(pkg: LoadedTaskPackage, a: FlightAssignment): Int {
        val res = RunThresholds.resolveFor(pkg, TaskModes.PROFILE_DEFAULT)
        val applied = res.applied.getValue("max_duty_span_min")
        return DutyExtensionPolicies.capFor(applied.value, applied.dutyExtensions,
            a.employee, a.flight!!.departureUTCDateTime!!, a.flight!!.arrivalUTCDateTime!!)
    }

    private fun capRule(carrier: String, literal: String): String = if (carrier == "fit") {
        extensionRule("CAP-FIT", capLiteral = literal)
    } else {
        // Exercise the supported legacy-only parameter carrier inside a current
        // landing, rather than converting this test into a typed-cell test.
        """{"id":"CAP-LEGACY","kind":"SOFT","export_solver":{"family":"duty_extension_penalty"},
            "template_fit":${landing("duty_extension_penalty", "")},
            "params":[{"name":"max_duty_duration","value":$literal,
            "unit":"duration_minutes","relation":"max"}]}"""
    }

    @ParameterizedTest(name = "{0} cap rejects exact JSON {1}")
    @MethodSource("malformedCapValues")
    fun capRequiresPositiveIntegralJsonWithinIntRange(carrier: String, literal: String) {
        val error = assertThrows<TaskPackageError> {
            packageFor(extraRules = listOf(capRule(carrier, literal)))
        }
        assertTrue(error.message!!.contains("CAP-${carrier.uppercase()}"), error.message)
        assertTrue(error.message!!.contains(if (carrier == "fit") "duty_extension_cap" else "max_duty_duration"),
            error.message)
    }

    @ParameterizedTest
    @ValueSource(strings = ["fit", "legacy"])
    fun integralJsonCapSurvivesTheActualZipReaderExactly(carrier: String) {
        for (value in listOf(780, Int.MAX_VALUE)) {
            val pkg = packageFor(extraRules = listOf(capRule(carrier, value.toString())))
            assertEquals(value, pkg.dutyExtensionPolicies.single {
                it.ruleId == "CAP-${carrier.uppercase()}"
            }.capMin)
        }
    }

    @Test
    fun `package execution refuses an extension whose base is absent or unresolved`() {
        for (value in listOf(null, 0)) {
            val pkg = packageFor(baseValue = value).copy(
                weights = mapOf("Duty extension over threshold" to 3L),
                allowedModes = mapOf("rostering" to listOf("feasibility-hard", "feasibility-soft")))
            assertEquals(listOf("EXT"), pkg.dutyExtensionPolicies.map { it.ruleId })
            for (mode in listOf("feasibility-hard", "feasibility-soft")) {
                val error = assertThrows<TaskModeRefused> {
                    TaskModes.resolve(mode, pkg, "rostering")
                }
                // A malformed declared base already refuses while resolving
                // its own rule; an absent base must name the dependent policy.
                assertTrue(error.message!!.contains(if (value == null) "EXT" else "BASE"), error.message)
                assertTrue(error.message!!.contains("max_duty_duration"), error.message)
            }
        }
    }

    @Test
    fun `valid base preserves declared policy and extension-free package needs no base`() {
        val complete = TaskModes.resolve("feasibility-hard", packageFor(), "rostering")
        assertEquals(720, complete.thresholds.applied.getValue("max_duty_span_min").value)
        assertEquals(listOf("EXT"), complete.thresholds.applied.getValue("max_duty_span_min")
            .dutyExtensions.map { it.ruleId })
        val empty = packageFor(baseValue = null).copy(dutyExtensionPolicies = emptyList(),
            dutyExtensionCapMin = null, dutyExtensionRules = emptyList())
        assertTrue(TaskModes.resolve("feasibility-hard", empty, "rostering").thresholds.applied.isEmpty())
    }

    @ParameterizedTest
    @ValueSource(strings = ["feasibility-hard", "feasibility-soft"])
    fun legacyCapWithoutPoliciesStillRequiresAnAppliedBase(mode: String) {
        // The legacy manually constructed carrier is a supported resolver API;
        // unlike the actual ZIP reader it can contain a cap without policies.
        val pkg = packageFor(baseValue = null).copy(
            dutyExtensionPolicies = emptyList(), dutyExtensionCapMin = 780,
            dutyExtensionRules = listOf("LEGACY-CAP"),
            weights = mapOf("Duty extension over threshold" to 3L),
            allowedModes = mapOf("rostering" to listOf("feasibility-hard", "feasibility-soft")))
        assertTrue(pkg.dutyExtensionPolicies.isEmpty())
        assertEquals(780, pkg.dutyExtensionCapMin)
        val error = assertThrows<TaskModeRefused> { TaskModes.resolve(mode, pkg, "rostering") }
        assertTrue(error.message!!.contains("LEGACY-CAP"), error.message)
        assertTrue(error.message!!.contains("max_duty_duration"), error.message)
    }

    @Test
    fun `legacy cap without policies still works with an actual base`() {
        val pkg = packageFor().copy(dutyExtensionPolicies = emptyList(),
            dutyExtensionCapMin = 780, dutyExtensionRules = listOf("LEGACY-CAP"))
        val applied = TaskModes.resolve("feasibility-hard", pkg, "rostering").thresholds.applied
        assertEquals(780, applied.getValue("max_duty_span_min").value)
        assertEquals(720, applied.getValue(RunThresholds.DUTY_EXTENSION_THRESHOLD).value)
        assertTrue("LEGACY-CAP" in applied.getValue(RunThresholds.DUTY_EXTENSION_THRESHOLD).rules)
    }

    @Test
    fun `base B does not inherit the extension allowed for base A`() {
        verifier("false").verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(duty("B")).penalizesBy(60)
    }

    @Test
    fun `base A without actual required consent keeps the base hard limit`() {
        verifier().verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(duty("A")).penalizesBy(60)
    }

    @Test
    fun `false consent requirement permits base A without a consent record`() {
        verifier("false").verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(duty("A")).penalizes(0)
    }

    @Test
    fun `actual consent for base A permits 780 and preserves the soft penalty`() {
        val a = duty("A")
        a.employee!!.dutyExtensionConsents.add(consent(a, true))
        assertEquals(780, cap(packageFor(), a))
        val v = verifier()
        v.verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented).given(a).penalizes(0)
        v.verifyThat(FlightCrewSchedulingConstraintProvider::dutyExtensionOverThreshold).given(a).penalizesBy(1)
    }

    @Test
    fun `actual no keeps 720 even for the allowed base`() {
        val a = duty("A")
        a.employee!!.dutyExtensionConsents.add(consent(a, false))
        assertEquals(720, cap(packageFor(), a))
        verifier().verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(a).penalizesBy(60)
    }

    @Test
    fun `actual yes does not override a wrong base or the 780 cap`() {
        for ((base, minutes, overage) in listOf(Triple("B", 780L, 60L), Triple("A", 781L, 1L))) {
            val a = duty(base, minutes)
            a.employee!!.dutyExtensionConsents.add(consent(a, true))
            verifier().verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
                .given(a).penalizesBy(overage)
        }
    }

    @Test
    fun `false requirement accepts actual yes no or missing but 781 always violates cap`() {
        for (yes in listOf(true, false, null)) {
            val a = duty("A", 781)
            if (yes != null) a.employee!!.dutyExtensionConsents.add(consent(a, yes))
            assertEquals(780, cap(packageFor("false"), a))
            verifier("false").verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
                .given(a).penalizesBy(1)
        }
    }

    @Test
    fun `unknown base cannot borrow the departure airport`() {
        val a = duty("A").apply { employee!!.homeAirport = null }
        a.employee!!.dutyExtensionConsents.add(consent(a, true))
        assertEquals(720, cap(packageFor(), a))
        verifier().verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(a).penalizesBy(60)
    }

    @Test
    fun `consent never widens to a changed duty boundary or another employee`() {
        val a = duty("A")
        a.employee!!.dutyExtensionConsents.add(consent(a, true).apply {
            end = a.flight!!.arrivalUTCDateTime!!.plusMinutes(1).toString()
        })
        assertEquals(720, cap(packageFor(), a))
        val other = employee("OTHER").apply { homeAirport = airport("A") }
        a.employee = other
        assertEquals(720, cap(packageFor(), a))
    }

    @Test
    fun `a second duty of the same employee does not inherit the first consent`() {
        val a = duty("A")
        val b = assignment("b", flight("b", airport("A"), t(4, 6), airport("A"), t(4, 19)), a.employee)
        a.employee!!.dutyExtensionConsents.add(consent(a, true))
        verifier().verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(a, b).penalizesBy(60)
    }

    @Test
    fun `disjoint exceptions retain their own caps instead of a global minimum`() {
        val pkg = packageFor("false", extraRules = listOf(extensionRule("EXT-B", "false", "\"B\"", 810)))
        assertEquals(2, pkg.dutyExtensionPolicies.size)
        assertEquals(780, cap(pkg, duty("A")))
        assertEquals(810, cap(pkg, duty("B", 810)))
        verifier(pkg).verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(duty("B", 810)).penalizes(0)
    }

    @Test
    fun `different applicable caps refuse ambiguity with rule and employee addresses`() {
        val pkg = packageFor("false", extraRules = listOf(extensionRule("EXT-OTHER", "false", "\"A\"", 810)))
        val error = assertThrows<TaskModeRefused> { cap(pkg, duty("A")) }
        assertTrue(error.message!!.contains("EXT=780"))
        assertTrue(error.message!!.contains("EXT-OTHER=810"))
        assertTrue(error.message!!.contains("сотрудник E"))
    }

    @Test
    fun `identical applicable caps keep both sources without inventing a priority`() {
        val pkg = packageFor("false", extraRules = listOf(extensionRule("EXT-SAME", "false")))
        assertEquals(listOf("EXT", "EXT-SAME"), pkg.dutyExtensionPolicies.map { it.ruleId })
        assertEquals(780, cap(pkg, duty("A")))
    }

    @Test
    fun `an OUT extension retained for history never contributes a policy or a higher cap`() {
        val outRule = extensionRule("OLD-OUT", "false", "\"A\"", 900)
            .replace("\"kind\":\"SOFT\"", "\"kind\":\"OUT\"")
        val pkg = packageFor("false", extraRules = listOf(outRule))
        assertEquals(listOf("EXT"), pkg.dutyExtensionPolicies.map { it.ruleId })
        assertEquals(780, cap(pkg, duty("A")))
        verifier(pkg).verifyThat(FlightCrewSchedulingConstraintProvider::maxDutyPeriodRestSegmented)
            .given(duty("A", 781)).penalizesBy(1)
    }

    @Test
    fun `excluded until data extension remains outside actual policies`() {
        val excluded = extensionRule("WAIT-DATA", "false", "\"A\"", 900)
            .replace("\"kind\":\"SOFT\"", "\"kind\":\"SOFT\",\"excluded_until_data\":{\"question\":\"Q\"}")
        val pkg = packageFor("false", extraRules = listOf(excluded))
        assertEquals(listOf("EXT"), pkg.dutyExtensionPolicies.map { it.ruleId })
        assertEquals(780, cap(pkg, duty("A")))
    }

    @Test
    fun `absent optional conditions stay unscoped while declared null or malformed refuses`() {
        assertEquals(780, cap(packageFor(null, null), duty("B")))
        for ((consent, bases) in listOf("null" to "\"A\"", "\"yes\"" to "\"A\"",
                "false" to "null", "false" to "[]")) {
            val error = assertThrows<TaskPackageError> { packageFor(consent, bases) }
            assertTrue(error.message!!.contains("EXT"))
        }
    }

    @Test
    fun `structured base codes are not split and remain exact`() {
        val pkg = packageFor("false", "[\"A, special\",\"B\"]")
        assertEquals(listOf("A, special", "B"), pkg.dutyExtensionPolicies.single().bases)
        assertEquals(720, cap(pkg, duty("A")))
        assertEquals(780, cap(pkg, duty("A, special")))
    }

    @Test
    fun `JSON employee carrier survives real Jackson conversion`() {
        val e = ObjectMapper().readValue("""{"id":"E","dutyExtensionConsents":[{
            "start":"2026-03-02T06:00:00","end":"2026-03-02T19:00:00",
            "consent":true,"source":"Смены!row2"}]}""", Employee::class.java)
        e.homeAirport = airport("A")
        val a = duty("A").apply { employee = e }
        assertEquals(780, cap(packageFor(), a))
        assertEquals(true, ObjectMapper().readTree(ObjectMapper().writeValueAsBytes(e))
            .path("dutyExtensionConsents").get(0).path("consent").asBoolean())
    }

    @Test
    fun `JSON consent is a boolean decision not Jackson coercion or null`() {
        for (value in listOf("\"true\"", "\"false\"", "1", "0", "null", "{}")) {
            val error = assertThrows<com.fasterxml.jackson.databind.JsonMappingException> {
                ObjectMapper().readValue("""{"id":"E","dutyExtensionConsents":[{
                    "start":"2026-03-02T06:00:00","end":"2026-03-02T19:00:00",
                    "consent":$value,"source":"Смены!row2"}]}""", Employee::class.java)
            }
            assertTrue(error.message!!.contains("consent"))
        }
        assertThrows<com.fasterxml.jackson.databind.JsonMappingException> {
            ObjectMapper().readValue("""{"id":"E","dutyExtensionConsents":null}""", Employee::class.java)
        }
    }

    @Test
    fun `an explicitly incomplete landing never becomes an unscoped exception`() {
        val unresolved = extensionRule("BAD", "false").replace("\"category\":\"A\"", "\"category\":\"C\"")
        val error = assertThrows<TaskPackageError> { packageFor(extraRules = listOf(unresolved)) }
        assertTrue(error.message!!.contains("BAD"))
        assertTrue(error.message!!.contains("неполная/неопределённая"))
    }

    @Test
    fun `malformed or conflicting consent rows refuse before solving including unassigned employees`() {
        val a = duty("A")
        val badRows = listOf(consent(a, true).apply { start = "yesterday" },
            consent(a, true).apply { end = start }, consent(a, true).apply { consent = null },
            consent(a, true).apply { source = null })
        for (row in badRows) {
            a.employee!!.dutyExtensionConsents = mutableListOf(row)
            val problem = FlightCrewSchedule().apply { employees.add(a.employee!!) }
            val error = assertThrows<TaskModeRefused> { RunThresholds.checkFacts(problem, emptyMap()) }
            assertTrue(error.message!!.contains("dutyExtensionConsents[0]"))
        }
        a.employee!!.dutyExtensionConsents = mutableListOf(consent(a, true), consent(a, false))
        val error = assertThrows<TaskModeRefused> { cap(packageFor(), a) }
        assertTrue(error.message!!.contains("противоречивые согласия"))
    }

    @Test
    fun `actual policy report retains captured scopes after another provider is built`() {
        val p = provider(packageFor())
        assertEquals(720, p.readings()["max_duty_span_min"])
        val captured = LastProviderReadings.snapshotWithCodes()!!
        FlightCrewSchedulingConstraintProvider()
        val mode = TaskModes.withActualReadings(TaskModes.resolve("feasibility-soft", null, "rostering"),
            captured.readings, captured.codeReadings, captured.policyReadings)
        val json = ObjectMapper().valueToTree<com.fasterxml.jackson.databind.JsonNode>(LastRun.snapshotOf(mode))
        val actual = json.path("profilePolicyReadings").path("max_duty_span_min")
        assertEquals(720, actual.path("base_min").asInt())
        assertEquals(780, actual.path("policies").get(0).path("cap_min").asInt())
        assertEquals("A", actual.path("policies").get(0).path("extension_bases").get(0).asText())
        assertEquals(true, actual.path("policies").get(0).path("extension_consent").asBoolean())
        assertTrue(json.path("thresholds").path("applied").isEmpty,
            "The report must not reconstruct actual policies from declarations")
        assertTrue(LastProviderReadings.snapshotWithCodes()!!.policyReadings.isEmpty())
    }
}
