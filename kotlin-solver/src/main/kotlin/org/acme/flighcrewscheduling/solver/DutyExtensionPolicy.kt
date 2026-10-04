package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.Employee
import java.time.LocalDateTime

/** One independently scoped exception from template_fit.params. Null bases means no base condition. */
data class DutyExtensionPolicy(
    val ruleId: String,
    val capMin: Int,
    val bases: List<String>? = null,
    val consentRequired: Boolean = false,
    val declaredThresholdMin: Int? = null,
) {
    fun snapshot(): Map<String, Any?> = linkedMapOf(
        "rule" to ruleId, "cap_min" to capMin,
        "extension_bases" to bases?.toList(), "extension_consent" to consentRequired
    ).also { if (declaredThresholdMin != null) it["long_duty_threshold"] = declaredThresholdMin }
}

/** Actual constructor reading, transported separately from declared Applied records. */
data class DutyExtensionReading(val baseMin: Int, val policies: List<DutyExtensionPolicy>) {
    fun detached(): DutyExtensionReading = copy(policies = policies.map { it.copy(bases = it.bases?.toList()) })
    fun snapshot(): Map<String, Any?> = linkedMapOf(
        "base_min" to baseMin, "policies" to policies.map { it.snapshot() },
        "base_source" to "employee.homeAirport.code",
        "consent_match" to "employee+exact_duty_start_end_payload_scale")
}

/** Pure, shared addressable selector used by the actual hard constraint; it never reads rule prose. */
object DutyExtensionPolicies {
    private data class Consent(val start: LocalDateTime, val end: LocalDateTime, val yes: Boolean)

    private fun consents(employee: Employee): List<Consent> {
        val out = ArrayList<Consent>()
        for ((index, row) in employee.dutyExtensionConsents.withIndex()) {
            val address = "сотрудник ${employee.id}, dutyExtensionConsents[$index]" +
                (row?.source?.let { " ($it)" } ?: "")
            fun bad(why: String): Nothing = throw TaskModeRefused("$address: $why")
            if (row == null) bad("запись согласия null")
            if (row.source.isNullOrBlank()) bad("нет source исходной строки согласия")
            fun moment(raw: String?, key: String): LocalDateTime = try {
                LocalDateTime.parse(raw ?: bad("нет $key"))
            } catch (e: java.time.DateTimeException) {
                bad("$key должен быть ISO LocalDateTime в шкале пакета без offset: $raw")
            }
            val start = moment(row.start, "start")
            val end = moment(row.end, "end")
            if (end <= start) bad("end не позже start")
            val yes = row.consent ?: bad("consent отсутствует/null: требуется явное true/false")
            if (out.any { it.start == start && it.end == end && it.yes != yes })
                bad("противоречивые согласия для той же смены $start — $end")
            out.add(Consent(start, end, yes))
        }
        return out
    }

    /** Called before solving even for employees without assignments. No malformed row is lost. */
    fun validate(employee: Employee) { consents(employee) }

    fun capFor(baseMin: Int, policies: List<DutyExtensionPolicy>, employee: Employee?,
               start: LocalDateTime, end: LocalDateTime): Int {
        if (baseMin <= 0 || policies.isEmpty() || employee == null) return baseMin
        val evidence = consents(employee)
        val base = employee.homeAirport?.code
        val applicable = policies.filter { policy ->
            policy.capMin > baseMin &&
                (policy.bases == null || (base != null && base in policy.bases)) &&
                (!policy.consentRequired || evidence.any { it.start == start && it.end == end && it.yes })
        }
        if (applicable.map { it.capMin }.distinct().size > 1) {
            throw TaskModeRefused("сотрудник ${employee.id}, смена $start — $end: " +
                "несколько применимых потолков продления без заданного порядка: " +
                applicable.joinToString { "${it.ruleId}=${it.capMin}" })
        }
        return applicable.firstOrNull()?.capMin ?: baseMin
    }
}
