package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.JsonNode

/**
 * Блок `logic` правила пакета постановки (спецификация 1.5, ступень 3).
 *
 * Машинное представление правила произвольного вида НАД params: предмет,
 * условия-атомы, требование, окно, ссылки на параметры. Числа в блоке не
 * живут — только ссылками на записи `params` ТОГО ЖЕ правила; каждый
 * атом несёт след `as_written` для сверки человеком.
 *
 * Здесь — только ЧТЕНИЕ структуры (что в пакете написано). Словарная
 * годность, выразимость, отнесение к существующим исполнителям и отказы —
 * в [LogicCompiler]: структура читается для всех видов правил, а судится
 * только то, что решатель собирается исполнять.
 */
/**
 * Ссылка на параметр правила (ревизия 1.6): имя либо имя с уточнителем.
 * Уточнитель обязателен, когда правило несёт несколько записей одного
 * имени (690 «без согласия» / 780 «с согласия») — какое число действует,
 * решатель не угадывает.
 */
data class LogicRef(val name: String, val qualifier: String?) {
    fun canonical(): String = if (qualifier.isNullOrBlank()) name else name + "|" + qualifier
}

data class LogicAtom(
    val predicate: String,
    /** аргументы как в пакете: строки; `allowed` — список строк; `window` — [LogicWindow]. */
    val args: Map<String, Any>,
    val asWritten: String,
)

/**
 * Окно требования. Форма узла согласована с производителем 2026-08-23:
 * `{kind: "month"}` либо `{kind: "rolling", unit: "day", param: "<имя>"}`.
 * Строка вместо узла или пустой объект — отказ, не умолчание: длина окна
 * «36 часов за 7 суток» иначе читалась бы пустой строкой и молча
 * превращалась в другое окно.
 */
data class LogicWindow(
    val kind: String,
    val unit: String?,
    val param: String?,
) {
    fun canonical(): String =
        if (kind == "month") "month" else "rolling:" + (unit ?: "?") + ":" + (param ?: "?")
}

data class LogicDemand(
    val kind: String,
    val relation: String?,
    val param: String?,
    /** уточнитель ссылки требования (1.6, ссылка-объект). */
    val paramQualifier: String? = null,
    val subject: String?,
    val universe: String?,
    /** ревизия 1.6: мера длительности смены — `span` (размах) | `block` (налёт). */
    val measure: String?,
    val asWritten: String,
    /** Роли мягких шаблонов волны П-2: поля `*_param` объекта demand
     *  (кроме самого `param`) — ссылки на params правила. */
    val roleRefs: Map<String, LogicRef> = emptyMap(),
)

data class RuleLogic(
    val ruleId: String,
    val ruleKind: String,
    val subject: String,
    val scope: String?,
    val conditions: List<LogicAtom>,
    val demand: LogicDemand,
    val window: LogicWindow?,
    val sourceParams: List<LogicRef>,
    /** `required` | `post-check`; умолчание — required, и это заявляется. */
    val enforce: String,
    val enforceDeclared: Boolean,
    /** ВСЕ записи params этого правила (любого вида) — для проверки ссылок. */
    val params: List<RuleParam>,
) {
    /** Ссылки атомов и требования (1.6: строка-имя либо объект с уточнителем). */
    fun references(): List<LogicRef> {
        val out = ArrayList<LogicRef>()
        for (c in conditions) {
            for ((k, v) in c.args) {
                if (k.endsWith("param")) when (v) {
                    is LogicRef -> out.add(v)
                    is String -> out.add(LogicRef(v, null))
                }
                if (v is LogicWindow && v.param != null) out.add(LogicRef(v.param, null))
            }
        }
        demand.param?.let { out.add(LogicRef(it, demand.paramQualifier)) }
        out.addAll(demand.roleRefs.values)
        window?.param?.let { out.add(LogicRef(it, null)) }
        return out.distinct()
    }
}

/** Чтение блоков `logic` из model.json пакета. */
object LogicReader {

    /**
     * Блоки logic всех правил модели (вида любого: вид решает компилятор).
     * Fail-closed по СТРУКТУРЕ: блок не объект, требование не объект,
     * атом без предиката — [TaskPackageError]. Имена из словарей здесь не
     * сверяются: незнакомое имя — не порча пакета, а предмет отказа
     * компилятора с причиной по правилу.
     */
    fun read(model: JsonNode, paramsByRule: Map<String, List<RuleParam>>): List<RuleLogic> {
        val out = ArrayList<RuleLogic>()
        for (rule in (model.get("rules") ?: return out)) {
            // Исключённое до получения данных правило (структура ВМП
            // 2026-09-02) решатель не исполняет: его logic не читается и
            // в blocksSeen не считается — сертификат судит показания по
            // своду без исключённых.
            if (TaskPackage.isExcludedUntilData(rule)) continue
            val ruleId = rule.get("id")?.asText() ?: ""
            val kind = rule.get("kind")?.asText() ?: ""
            val nodeOrNull = rule.get("logic")
            if (nodeOrNull == null || nodeOrNull.isNull) {
                // Путь А (05.09.2026): правило семьи истории с посадкой, но
                // без блока logic получает логику формы ИЗ ПОСАДКИ — решатель
                // исполняет посадку формой, а не молчит.
                synthesizeFromFit(rule, ruleId, kind)?.let { out.add(it) }
                continue
            }
            val node = nodeOrNull
            if (!node.isObject) {
                throw TaskPackageError("правило " + ruleId + ": блок logic не объект")
            }
            val subject = textOf(node.get("subject"))
                ?: throw TaskPackageError("правило " + ruleId + ": в блоке logic нет subject")
            val demandNode = node.get("demand")
            if (demandNode == null || !demandNode.isObject) {
                throw TaskPackageError("правило " + ruleId + ": в блоке logic нет demand (объекта требования)")
            }
            val demandKind = textOf(demandNode.get("kind"))
                ?: throw TaskPackageError("правило " + ruleId + ": у требования logic нет kind")
            val conds = ArrayList<LogicAtom>()
            val condNode = node.get("condition")
            if (condNode != null && !condNode.isNull) {
                if (!condNode.isArray) {
                    throw TaskPackageError("правило " + ruleId + ": condition в logic не список")
                }
                for ((i, c) in condNode.withIndex()) {
                    if (!c.isObject) {
                        throw TaskPackageError("правило " + ruleId + ": condition[" + i + "] не объект")
                    }
                    val pred = textOf(c.get("predicate"))
                        ?: throw TaskPackageError("правило " + ruleId + ": condition[" + i + "] без predicate")
                    val args = LinkedHashMap<String, Any>()
                    val names = c.fieldNames()
                    while (names.hasNext()) {
                        val k = names.next()
                        if (k == "predicate" || k == "as_written") continue
                        args[k] = argOf(ruleId, "condition[" + i + "] (" + pred + ")", k, c.get(k),
                            isRef = k.endsWith("param"))
                    }
                    conds.add(LogicAtom(pred, args, textOf(c.get("as_written")) ?: ""))
                }
            }
            val windowNode = node.get("window")
            val window = if (windowNode == null || windowNode.isNull) null
                         else windowOf(ruleId, "window", windowNode)
            val sp = ArrayList<LogicRef>()
            val spNode = node.get("source_params")
            if (spNode != null && !spNode.isNull) {
                if (!spNode.isArray) {
                    throw TaskPackageError("правило " + ruleId + ": source_params в logic не список")
                }
                for (s in spNode) sp.add(refOf(ruleId, "source_params", s))
            }
            val enforceNode = node.get("enforce")
            val enforceDeclared = enforceNode != null && !enforceNode.isNull
            out.add(RuleLogic(
                ruleId = ruleId,
                ruleKind = kind,
                subject = subject,
                scope = textOf(node.get("scope")),
                conditions = conds,
                demand = demandOf(ruleId, demandNode, demandKind),
                window = window,
                sourceParams = sp,
                enforce = if (enforceDeclared) enforceNode!!.asText() else "required",
                enforceDeclared = enforceDeclared,
                params = paramsByRule[ruleId] ?: emptyList(),
            ))
        }
        return out
    }

    /** Семьи базы шаблонов, чей исполнитель — форма решателя (путь А). */
    val HISTORY_FIT_FAMILIES: Set<String> = setOf("night_series", "rolling_rest", "days_off_floor",
                                                  "duty_series_limit")

    /** Единицы и отношения словаря CDPM у параметров семей истории
     *  (cdpm/model.py; посадка единиц не несёт, а формам они нужны). */
    private val HISTORY_FIT_UNITS: Map<String, Pair<String, String>> = mapOf(
        "max_consecutive_night_duties" to ("count" to "max"),
        "night_duty_start" to ("time_of_day_minutes" to "min"),
        "night_duty_end" to ("time_of_day_minutes" to "max"),
        "weekly_rest_required" to ("duration_minutes" to "min"),
        "weekly_rest_period" to ("duration_minutes" to "window"),
        "min_days_off" to ("count" to "min"),
        // Счётчики скользящих окон (второй набор параметров семей).
        "max_night_duties_in_window" to ("count" to "max"),
        "night_window_days" to ("count" to "window"),
        "min_days_off_in_window" to ("count" to "min"),
        "days_off_window_days" to ("count" to "window"),
        // Лимит серии рабочих смен подряд (заявка CDPM 09.09.2026).
        "max_duty_series_length" to ("count" to "max"),
        "post_series_days_off" to ("count" to "min"),
    )

    /** Семья посадки правила (A/B), если это семья истории; иначе null. */
    fun fittedHistoryFamily(rule: JsonNode): String? {
        val fit = rule.get("template_fit") ?: return null
        if (fit.isNull) return null
        val cat = fit.get("category")?.asText()
        if (cat != "A" && cat != "B") return null
        val fam = fit.get("family")?.asText() ?: return null
        return if (fam in HISTORY_FIT_FAMILIES) fam else null
    }

    private fun fitParam(fit: JsonNode, name: String, ruleId: String, kind: String): RuleParam? {
        val cell = fit.get("params")?.get(name) ?: return null
        if (cell.isNull) return null
        val v = cell.get("value") ?: return null
        if (v.isNull || !v.isNumber) return null
        val (unit, rel) = HISTORY_FIT_UNITS[name] ?: return null
        return RuleParam(name = name, value = v.asDouble(), rawValue = v.asText(), unit = unit,
            relation = rel, qualifier = null, asWritten = cell.get("quote")?.asText() ?: "",
            ruleId = ruleId, ruleKind = kind,
            tz = if (unit == "time_of_day_minutes") "base-local" else null)
    }

    /**
     * Логика формы ИЗ ПОСАДКИ (путь А, решение COSMOS 05.09.2026): правило
     * семьи истории без блока logic получает тот же блок, что писал бы
     * производитель, — предметы, атомы и окно словаря 1.6, числа ссылками
     * на параметры посадки (единицы словаря CDPM, окно ночи base-local).
     * Не хватает параметра — блока нет (правило остаётся без исполнителя
     * и названо реестром порогов, как любой параметр без ручки).
     */
    fun synthesizeFromFit(rule: JsonNode, ruleId: String, kind: String): RuleLogic? {
        val fam = fittedHistoryFamily(rule) ?: return null
        val fit = rule.get("template_fit")
        fun p(name: String) = fitParam(fit, name, ruleId, kind)
        return when (fam) {
            "night_series" -> {
                val a = p("night_duty_start") ?: return null
                val b = p("night_duty_end") ?: return null
                val k = p("max_night_duties_in_window")
                val wd = p("night_window_days")
                if (k != null && wd != null) {
                    // Счётчик окна: ночных смен в окне не более K.
                    return RuleLogic(ruleId = ruleId, ruleKind = kind, subject = "duty", scope = null,
                        conditions = listOf(LogicAtom("starts_in", linkedMapOf<String, Any>(
                            "from_param" to LogicRef(a.name, null), "to_param" to LogicRef(b.name, null)),
                            a.asWritten)),
                        demand = LogicDemand(kind = "count_within", relation = "max", param = k.name,
                            paramQualifier = null, subject = null, universe = "employee×rolling_window",
                            measure = null, asWritten = k.asWritten),
                        window = LogicWindow("rolling", "day", wd.name),
                        sourceParams = listOf(LogicRef(k.name, null), LogicRef(wd.name, null),
                            LogicRef(a.name, null), LogicRef(b.name, null)),
                        enforce = "required", enforceDeclared = false, params = listOf(k, wd, a, b))
                }
                val n = p("max_consecutive_night_duties") ?: return null
                RuleLogic(ruleId = ruleId, ruleKind = kind, subject = "duty", scope = null,
                    conditions = listOf(
                        LogicAtom("starts_in", linkedMapOf<String, Any>(
                            "from_param" to LogicRef(a.name, null), "to_param" to LogicRef(b.name, null)),
                            a.asWritten),
                        LogicAtom("consecutive", linkedMapOf<String, Any>(
                            "unit" to "duty", "param" to LogicRef(n.name, null)), n.asWritten)),
                    demand = LogicDemand(kind = "count_within", relation = "max", param = n.name,
                        paramQualifier = null, subject = null, universe = null, measure = null,
                        asWritten = n.asWritten),
                    window = null,
                    sourceParams = listOf(LogicRef(n.name, null), LogicRef(a.name, null), LogicRef(b.name, null)),
                    enforce = "required", enforceDeclared = false, params = listOf(n, a, b))
            }
            "rolling_rest" -> {
                val w = p("weekly_rest_required") ?: return null
                val period = p("weekly_rest_period") ?: return null
                RuleLogic(ruleId = ruleId, ruleKind = kind, subject = "rest", scope = null,
                    conditions = listOf(LogicAtom("longer_than",
                        linkedMapOf<String, Any>("param" to LogicRef(w.name, null)), w.asWritten)),
                    demand = LogicDemand(kind = "exists_universe", relation = null, param = null,
                        paramQualifier = null, subject = "rest", universe = "employee×rolling_window",
                        measure = null, asWritten = period.asWritten),
                    window = LogicWindow("rolling", "day", period.name),
                    sourceParams = listOf(LogicRef(w.name, null), LogicRef(period.name, null)),
                    enforce = "required", enforceDeclared = false, params = listOf(w, period))
            }
            "duty_series_limit" -> {
                // Лимит серии рабочих суток подряд и обязательный отдых
                // после неё: предмет day_off, требование «свободных суток
                // после серии не менее M», условия — consecutive по суткам
                // (длина серии N) и follows(day) («следуют за серией»).
                val n = p("max_duty_series_length") ?: return null
                val m = p("post_series_days_off") ?: return null
                RuleLogic(ruleId = ruleId, ruleKind = kind, subject = "day_off", scope = null,
                    conditions = listOf(
                        LogicAtom("consecutive", linkedMapOf<String, Any>(
                            "unit" to "day", "param" to LogicRef(n.name, null)), n.asWritten),
                        LogicAtom("follows", linkedMapOf<String, Any>(
                            "subject" to "day"), n.asWritten)),
                    demand = LogicDemand(kind = "count_within", relation = "min", param = m.name,
                        paramQualifier = null, subject = null, universe = null, measure = null,
                        asWritten = m.asWritten),
                    window = null,
                    sourceParams = listOf(LogicRef(m.name, null), LogicRef(n.name, null)),
                    enforce = "required", enforceDeclared = false, params = listOf(m, n))
            }
            else -> {
                val k = p("min_days_off_in_window")
                val wd = p("days_off_window_days")
                if (k != null && wd != null) {
                    // Счётчик окна: выходных в окне не менее K.
                    return RuleLogic(ruleId = ruleId, ruleKind = kind, subject = "day_off", scope = null,
                        conditions = emptyList(),
                        demand = LogicDemand(kind = "count_within", relation = "min", param = k.name,
                            paramQualifier = null, subject = null, universe = "employee×rolling_window",
                            measure = null, asWritten = k.asWritten),
                        window = LogicWindow("rolling", "day", wd.name),
                        sourceParams = listOf(LogicRef(k.name, null), LogicRef(wd.name, null)),
                        enforce = "required", enforceDeclared = false, params = listOf(k, wd))
                }
                val n = p("min_days_off") ?: return null
                RuleLogic(ruleId = ruleId, ruleKind = kind, subject = "day_off", scope = null,
                    conditions = emptyList(),
                    demand = LogicDemand(kind = "count_within", relation = "min", param = n.name,
                        paramQualifier = null, subject = null, universe = "employee×month",
                        measure = null, asWritten = n.asWritten),
                    window = null, sourceParams = listOf(LogicRef(n.name, null)),
                    enforce = "required", enforceDeclared = false, params = listOf(n))
            }
        }
    }

    private fun textOf(n: JsonNode?): String? =
        if (n == null || n.isNull || !n.isValueNode) null else n.asText()

    private fun demandOf(ruleId: String, node: JsonNode, kind: String): LogicDemand {
        val paramNode = node.get("param")
        val ref = if (paramNode == null || paramNode.isNull) null
                  else refOf(ruleId, "demand.param", paramNode)
        val roles = LinkedHashMap<String, LogicRef>()
        for (name in node.fieldNames()) {
            if (name != "param" && name.endsWith("_param") && !node.get(name).isNull) {
                roles[name] = refOf(ruleId, "demand.$name", node.get(name))
            }
        }
        return LogicDemand(
            kind = kind,
            relation = textOf(node.get("relation")),
            param = ref?.name,
            paramQualifier = ref?.qualifier,
            subject = textOf(node.get("subject")),
            universe = textOf(node.get("universe")),
            measure = textOf(node.get("measure")),
            asWritten = textOf(node.get("as_written")) ?: "",
            roleRefs = roles)
    }

    /** Ссылка 1.6: строка-имя либо объект {name, qualifier}. */
    private fun refOf(ruleId: String, where: String, n: JsonNode): LogicRef {
        if (n.isValueNode) return LogicRef(n.asText(), null)
        if (n.isObject) {
            val name = textOf(n.get("name")) ?: throw TaskPackageError(
                "правило " + ruleId + ": " + where + " — у ссылки-объекта нет name")
            return LogicRef(name, textOf(n.get("qualifier")))
        }
        throw TaskPackageError(
            "правило " + ruleId + ": " + where + " — ссылка не строка и не объект {name, qualifier}")
    }

    /** Аргумент атома: скаляр — строкой, ссылка — [LogicRef], `allowed` — списком, `window` — узлом. */
    private fun argOf(ruleId: String, where: String, name: String, n: JsonNode,
                      isRef: Boolean = false): Any {
        if (name == "window") return windowOf(ruleId, where + ".window", n)
        if (isRef) return refOf(ruleId, where + "." + name, n)
        if (n.isArray) {
            val items = ArrayList<String>()
            for (v in n) {
                if (!v.isValueNode) throw TaskPackageError(
                    "правило " + ruleId + ": " + where + ": элемент списка " + name + " не скаляр")
                items.add(v.asText())
            }
            return items
        }
        if (n.isObject) throw TaskPackageError(
            "правило " + ruleId + ": " + where + ": аргумент " + name + " — объект неизвестной формы")
        return n.asText()
    }

    /** Узел окна: объект известной формы; строка/пустой объект — отказ. */
    private fun windowOf(ruleId: String, where: String, n: JsonNode): LogicWindow {
        if (!n.isObject) throw TaskPackageError(
            "правило " + ruleId + ": " + where + " — не объект (" + n.nodeType + "); " +
            "окно читается узлом {kind: month} либо {kind: rolling, unit, param}, строкой — нет")
        val kind = textOf(n.get("kind")) ?: throw TaskPackageError(
            "правило " + ruleId + ": " + where + " — у окна нет kind")
        return LogicWindow(kind, textOf(n.get("unit")), textOf(n.get("param")))
    }
}
