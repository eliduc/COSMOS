package org.acme.flighcrewscheduling.solver

import org.acme.flighcrewscheduling.domain.CalendarWindowRule

/** Decoded native semantics, not execution authority. No mode, readiness,
 * customer-run registration or conversion to LoadedTaskPackage is available.
 * The production reader and isolated component reader use the same decoder.
 */
interface NativeModel {
    val ruleParams: Map<String, List<RuleParam>>
    val objectiveDetails: Map<String, List<PackageObjective>>
    val hardParams: List<RuleParam>
    val weights: Map<String, Long>
    val weightRules: Map<String, String>
    val unmappedRules: List<String>
    val componentsWithoutWeight: List<String>
    val calendarWindows: List<CalendarWindowRule>
    val logic: List<RuleLogic>
    val logicUnproven: Map<String, String>
    val objectiveToConstraint: Map<String, String>?
    val dutyExtensionCapMin: Int?
    val dutyExtensionRules: List<String>
    val workloadSpreadThresholdMin: Int?
    val workloadSpreadRules: List<String>
    val dutyExtensionPolicies: List<DutyExtensionPolicy>
}

/** Internal decoder result; historical compatibility remains explicit and
 * is rejected by the component reader, not silently promoted to modern data.
 */
internal data class DecodedNativeModel(
    override val ruleParams: Map<String, List<RuleParam>>,
    override val objectiveDetails: Map<String, List<PackageObjective>>,
    override val hardParams: List<RuleParam>,
    override val weights: Map<String, Long>,
    override val weightRules: Map<String, String>,
    override val unmappedRules: List<String>,
    override val componentsWithoutWeight: List<String>,
    override val calendarWindows: List<CalendarWindowRule>,
    override val logic: List<RuleLogic>,
    override val logicUnproven: Map<String, String>,
    override val objectiveToConstraint: Map<String, String>?,
    override val dutyExtensionCapMin: Int?,
    override val dutyExtensionRules: List<String>,
    override val workloadSpreadThresholdMin: Int?,
    override val workloadSpreadRules: List<String>,
    override val dutyExtensionPolicies: List<DutyExtensionPolicy>,
    val landingArchiveOnly: Boolean,
) : NativeModel
