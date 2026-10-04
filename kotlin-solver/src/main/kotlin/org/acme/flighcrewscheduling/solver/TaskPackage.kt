package org.acme.flighcrewscheduling.solver

import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import java.io.ByteArrayInputStream
import java.io.File
import java.math.BigDecimal
import java.security.MessageDigest
import java.util.logging.Logger
import java.util.zip.ZipFile
import java.util.zip.ZipInputStream

/**
 * Мост CDPM -> COSMOS, этап А: «Пакет постановки задачи» как ВХОД прогона.
 *
 * Env `CREW_TASK_PACKAGE` — путь к пакету (каталог или zip) по
 * спецификации `docs/spec-postanovka-cosmos-v1.md` версии 1.x.
 * Отсутствие переменной — прежнее поведение прогонов байт-идентично:
 * веса берутся из литералов кода, режимы не проверяются.
 *
 * Отличие от образца [ResourceRules]: пакет читается ПО ЗАПРОСУ, а не
 * один раз при загрузке класса. Причина оплачена: значение-val
 * невозможно переключить внутри процесса, из-за чего правило недоступно
 * тестам, а отказ пакета всплывает не там, где случился. Здесь разбор
 * кэшируется по (путь, время правки, размер) и честно повторяется,
 * когда пакет сменился.
 *
 * Fail-closed (условие МУ-02): несовпадение контрольной суммы,
 * неизвестная старшая версия спецификации, отсутствие обязательного
 * файла — [TaskPackageError], а не умолчание.
 */
class TaskPackageError(message: String) : RuntimeException(message)

/**
 * Целевая функция пакета — как её объявил производитель.
 *
 * `metric` — имя из закрытого словаря спецификации (§5а) либо null:
 * формализатор ставит его только при точном смысловом соответствии.
 * `statement` человеку и НЕ разбирается машиной: числа из свободного
 * текста мы больше не берём (класс «число ниоткуда»).
 */
data class PackageObjective(
    val id: String,
    val title: String,
    val statement: String,
    val direction: String,
    val metric: String?,
    /** Price per metric unit, not the objective tier. Null means legacy absence.
     * Positive fractional values remain model data until optimization selects
     * this objective and checks the score backend's exact numeric range. */
    val rate: BigDecimal? = null,
)

/**
 * Машинный параметр правила (спецификация 1.3).
 *
 * `value` уже канонизирован производителем: времяподобное сведено к
 * минутам, а `asWritten` хранит исходную запись правила для сверки
 * человеком. Разбирать `asWritten` машиной запрещено — это тот самый
 * класс «число в свободном тексте».
 */
data class RuleParam(
    val name: String,
    /** число как есть: кредитные часы бывают дробными (спецификация 1.3),
     *  целочисленность требуется только у ручек решателя при сведении. */
    val value: Double?,
    /** исходная запись значения — для причины отказа. */
    val rawValue: String,
    val unit: String,
    val relation: String,
    val qualifier: String?,
    val asWritten: String,
    val ruleId: String,
    val ruleKind: String,
    /** признак пояса параметра времени суток (спецификация: UTC |
     *  base-local | acclimatized; обязателен у time_of_day) — внешний
     *  аудит 04.09.2026, A9: прежде не читался, и окно «base-local» /
     *  «acclimatized» исполнялось как минуты шкалы меток молча. */
    val tz: String? = null,
    /** Parsed opaque code values. Null means a legacy caller supplied rawValue only, not JSON null. */
    val codeValues: List<String>? = null,
)

/** Разобранный пакет постановки: только то, что нужно прогону. */
data class LoadedTaskPackage(
    /** SHA-256 самого manifest.json — подпись постановки (МУ-04). */
    val fingerprint: String,
    val specVersion: String,
    val customer: String,
    val sourceRun: String,
    val revision: Int,
    val readiness: String,
    /** задача -> разрешённые режимы (гейт запуска). */
    val allowedModes: Map<String, List<String>>,
    /** задача -> идентификаторы незаблокированных целевых. */
    val objectives: Map<String, List<String>>,
    /** задача -> объявленные целевые целиком (этап Б). */
    override val objectiveDetails: Map<String, List<PackageObjective>>,
    /** машинные параметры ЖЁСТКИХ правил (этап В, спецификация 1.3). */
    override val hardParams: List<RuleParam>,
    /** имя ограничения решателя -> вес из пакета. */
    override val weights: Map<String, Long>,
    /** правило пакета -> имя ограничения, куда лёг его вес (Д4: штрафная
     *  сверка приёмника читает соответствие отсюда, не из копии карты). */
    override val weightRules: Map<String, String> = emptyMap(),
    /** правила пакета, которым на этапе А нет ограничения-исполнителя. */
    override val unmappedRules: List<String>,
    /** компоненты без числового веса: при режиме со штрафами — отказ. */
    override val componentsWithoutWeight: List<String>,
    val hasData: Boolean,
    /** Окна дней недели формы calendar_window_penalties (пусто = формы нет). */
    override val calendarWindows: List<org.acme.flighcrewscheduling.domain.CalendarWindowRule> = emptyList(),
    /** блоки logic правил ЛЮБОГО вида (ступень 3, спецификация 1.5); вид судит компилятор. */
    override val logic: List<RuleLogic> = emptyList(),
    /** manifest.logic_unproven: правило -> причина, по которой производитель не предъявил пару. */
    override val logicUnproven: Map<String, String> = emptyMap(),
    /**
     * Карта «целевая -> ограничение» из подсекции objective_to_constraint
     * секции rule_maps пакета (ревизия 1.14) — канал доставки для
     * [TaskModes.resolveObjective]. null — подсекции нет: действует
     * легаси-таблица [TaskModes.OBJECTIVE_TO_CONSTRAINT].
     */
    override val objectiveToConstraint: Map<String, String>? = null,
    /**
     * Потолок семьи duty_extension_penalty (минуты): жёсткий предел
     * смены, до которого мягкое правило семьи разрешает продление за
     * штраф. null — правил семьи в пакете нет. Несколько правил
      * семьи — минимум только в этом legacy/display поле; исполнение
      * использует отдельные dutyExtensionPolicies и их фактические области.
     */
    override val dutyExtensionCapMin: Int? = null,
    /** Правила-источники потолка семьи (след применённого). */
    override val dutyExtensionRules: List<String> = emptyList(),
    /**
     * Порог существенности разброса кредитных часов семьи
     * balanced_workload (минуты): штрафуется только превышение. null —
     * правил семьи в пакете нет; правило семьи без параметра порога —
     * 0 (весь разброс). Несколько правил семьи обязаны сходиться в
     * одном пороге — исполнитель один.
     */
    override val workloadSpreadThresholdMin: Int? = null,
    /** Правила-источники порога разброса (след применённого). */
    override val workloadSpreadRules: List<String> = emptyList(),
    /** Actual per-rule scopes; the legacy aggregate cap is never used when these are present. */
    override val dutyExtensionPolicies: List<DutyExtensionPolicy> = emptyList(),
    /** Parsed historical/explicit legacy archive: reading never authorizes execution. */
    val landingArchiveOnly: Boolean = false,
    /** All validated native parameter carriers, including SOFT families; no
     * admission is implied. Legacy manually constructed packages may omit it. */
    override val ruleParams: Map<String, List<RuleParam>> = emptyMap(),
) : NativeModel

object TaskPackage {

    private val log: Logger = Logger.getLogger(TaskPackage::class.java.name)

    /** Старшая версия спецификации, которую понимает этот код. */
    const val SUPPORTED_MAJOR = 1

    /**
     * Таблица соответствий этапа А (пакет работ 03-TABLICA-SOOTVETSTVIJ):
     * идентификатор мягкого правила CDPM -> имя ограничения решателя.
     * Полный транслятор — этап В; здесь соответствие задано явно и узко.
     *
     * С ревизии 1.14 пакет может нести подсекцию rule_to_constraint
     * секции rule_maps манифеста — тогда действует КАРТА ПАКЕТА, а эта
     * таблица остаётся легаси-умолчанием для пакетов без подсекции.
     * Снятие таблицы — отдельный шаг после переиздания живых пакетов.
     */
    val RULE_TO_CONSTRAINT: Map<String, String> = mapOf(
        "R-S1-табл-3-строка-6-кол-3" to "Layover nights away from home",
        // Мини-мир перевыпускался отдельно: то же правило «ночёвки вне
        // базы» живёт под другой строкой (зеркально словарю зоны Г-П3).
        "R-S1-табл-3-строка-7-кол-3" to "Layover nights away from home",
        "R-S1-табл-4-строка-2-кол-3" to "Credit hours spread over threshold",
    )

    /**
     * P-N-SHD (контракт 2026-08-30): тип механики мягкого правила
     * приходит полем export_solver.family самого правила (закрытый
     * словарь, имена = семьи базы шаблонов vmp/template_registry).
     * Это ШТАТНЫЙ путь соотнесения правило→ограничение, инвариантный
     * к авиакомпании; координатные карты (rule_maps пакета и
     * легаси-таблица выше) сохраняют верховенство как явное
     * переопределение, но обязаны отмереть вместе с переизданием
     * живых пакетов.
     */
    val FAMILY_TO_CONSTRAINT: Map<String, String> = mapOf(
        "layover_penalty" to "Layover nights away from home",
        "calendar_windows" to "Calendar window layover penalty",
        // Правило семьи штрафует превышение порога существенности
        // (решение владельца 2026-09-03); разброс целиком — целевая
        // (METRIC_EXECUTORS: «Credit hours spread»).
        "balanced_workload" to "Credit hours spread over threshold",
        // Семья родилась конвейером шаблонов 2026-08-31 (заявка демо
        // «Полярис-нано», правило «Продление смены с согласия»):
        // мягкое превышение порога длительности смены до жёсткого
        // потолка. Порог — базовый предел смены пакета, потолок —
        // параметр правила семьи, вес — весом пакета.
        "duty_extension_penalty" to "Duty extension over threshold",
    )

    @Volatile private var cache: Pair<String, LoadedTaskPackage>? = null
    @Volatile private var forTests: LoadedTaskPackage? = null

    /** Подмена пакета в тестах: процесс один, переменную окружения
     *  переключить нельзя (урок ResourceRules). */
    fun overrideForTests(pkg: LoadedTaskPackage?) {
        forTests = pkg
        cache = null
    }

    /**
     * Есть ли ПРОЦЕССНЫЙ пакет (env-переменная либо тестовая подмена) — по
     * ФАКТУ НАЛИЧИЯ, без чтения содержимого. Гейт двусмысленности запросного
     * прогона обязан отказывать словами двусмысленности, а не падать на
     * контрольной сумме битого env-пакета (этап Г, контракт Ш-2 п.5).
     */
    fun processPackagePresent(): Boolean =
        forTests != null || !System.getenv("CREW_TASK_PACKAGE")?.trim().isNullOrEmpty()

    /** Пакет текущего прогона либо null, когда переменная не задана. */
    fun current(): LoadedTaskPackage? {
        forTests?.let { return it }
        val raw = System.getenv("CREW_TASK_PACKAGE")?.trim()
        if (raw.isNullOrEmpty()) return null
        return cachedByManifest(File(raw))
    }

    /**
     * Чтение с кэшем ПО СОДЕРЖИМОМУ манифеста, не по (путь, mtime, размер):
     * mtime КАТАЛОГА на NTFS не меняется правкой файла внутри, а zip той же
     * длины, перезаписанный в пределах кванта mtime, неотличим по штампу —
     * прежний ключ отдавал устаревший пакет (найдено разбором этапа Г).
     * Манифест перечитывается при каждом обращении (единственная запись,
     * дёшево); совпал отпечаток — пакет тот же по построению: содержимое
     * остальных файлов сверено суммами манифеста при разборе.
     */
    internal fun cachedByManifest(f: File): LoadedTaskPackage {
        if (!f.exists()) {
            throw TaskPackageError(
                "пакет постановки не найден по пути CREW_TASK_PACKAGE=" + f.path)
        }
        val manifestBytes = manifestBytesOf(f)
            ?: throw TaskPackageError("в пакете нет manifest.json: " + f.path)
        val key = sha256(manifestBytes)
        cache?.let { pair -> if (pair.first == key) return pair.second }
        val loaded = read(f)
        cache = key to loaded
        log.info("TaskPackage: пакет принят, отпечаток=" + loaded.fingerprint +
            ", заказчик=" + loaded.customer + ", режимы=" + loaded.allowedModes)
        return loaded
    }

    private fun manifestBytesOf(f: File): ByteArray? {
        if (f.isDirectory) {
            refuseComponentMembers(f.listFiles()?.map { it.name }.orEmpty())
            val m = File(f, "manifest.json")
            return if (m.isFile) m.readBytes() else null
        }
        return try {
            ZipFile(f).use { z ->
                val entries = z.entries().asSequence().toList()
                refuseComponentMembers(entries.map { it.name })
                val e = entries.asSequence()
                    .firstOrNull { !it.isDirectory && it.name.substringAfterLast('/') == "manifest.json" }
                    ?: return null
                z.getInputStream(e).readBytes()
            }
        } catch (e: Exception) {
            throw TaskPackageError("пакет-архив не читается: " + e.message)
        }
    }

    // ------------------------------------------------------------ чтение

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { b -> "%02x".format(b) }

    /** Разбор пакета по пути. Доступен тестам напрямую: обёртка
     *  рефлексией прятала бы причину отказа за InvocationTarget. */
    internal fun read(f: File, landingCompat: Int? = null): LoadedTaskPackage =
        parse(if (f.isDirectory) readDir(f) else readZip(f), landingCompat)

    /**
     * Разбор пакета из БАЙТОВ zip-архива — запросный путь этапа Г (Ш-2):
     * пакет приходит телом запроса, файла на диске у него нет. Гейты те же,
     * что у файла: один парсер, одна дисциплина отказов.
     */
    fun readBytes(zipBytes: ByteArray, landingCompat: Int? = null): LoadedTaskPackage {
        val out = LinkedHashMap<String, ByteArray>()
        try {
            ZipInputStream(ByteArrayInputStream(zipBytes)).use { z ->
                while (true) {
                    val e = z.nextEntry ?: break
                    if (e.isDirectory) continue
                    putMember(out, e.name, z.readBytes())
                }
            }
        } catch (e: Exception) {
            throw TaskPackageError("пакет-архив (тело запроса) не читается: " + e.message)
        }
        if (out.isEmpty()) {
            throw TaskPackageError("тело запроса не является zip-пакетом либо пусто")
        }
        return parse(out, landingCompat)
    }

    private fun parse(files: Map<String, ByteArray>, landingCompat: Int? = null): LoadedTaskPackage {
        refuseComponentMembers(files.keys)
        val manifestBytes = files["manifest.json"]
            ?: throw TaskPackageError("в пакете нет manifest.json")
        val om = ObjectMapper()
        val manifest = try {
            om.readTree(manifestBytes)
        } catch (e: Exception) {
            throw TaskPackageError("manifest.json не разбирается: " + e.message)
        }
        if (manifest.path("schema").asText().startsWith("component-input/") ||
                manifest.path("scope").asText() == "component-only")
            throw TaskPackageError("component-only input is not an executable task package")

        val spec = manifest.get("spec_version")?.asText()
            ?: throw TaskPackageError("в манифесте нет spec_version")
        val major = spec.substringBefore(".").toIntOrNull()
            ?: throw TaskPackageError("нечитаемая spec_version " + spec)
        if (major != SUPPORTED_MAJOR) {
            throw TaskPackageError(
                "версия спецификации " + spec + " не поддержана: этот код " +
                "понимает старшую версию " + SUPPORTED_MAJOR)
        }

        // Сверка контрольных сумм ДО какого-либо чтения содержимого
        // (обязанность потребителя, спецификация §7 п.1).
        val listed = manifest.get("files")
            ?: throw TaskPackageError("в манифесте нет перечня files")
        var checked = 0
        for (entry in listed) {
            val name = entry.get("name")?.asText()
                ?: throw TaskPackageError("в перечне files запись без name")
            val want = entry.get("sha256")?.asText()
                ?: throw TaskPackageError("в перечне files нет sha256 у " + name)
            val body = files[name]
                ?: throw TaskPackageError("файл " + name +
                    " объявлен манифестом, но отсутствует в пакете")
            val got = sha256(body)
            if (!got.equals(want, ignoreCase = true)) {
                throw TaskPackageError("контрольная сумма " + name +
                    " не совпала: манифест " + want + ", факт " + got)
            }
            checked++
        }
        if (checked == 0) {
            throw TaskPackageError("перечень files пуст — сверять было нечего")
        }

        val native = decodeNative(files, manifest, spec,
            listed.map { it.get("name")?.asText() }.toSet(), landingCompat)
        return LoadedTaskPackage(
            fingerprint = sha256(manifestBytes),
            specVersion = spec,
            customer = manifest.get("customer")?.asText() ?: "",
            sourceRun = manifest.get("source_run")?.asText() ?: "",
            revision = manifest.get("revision")?.asInt() ?: 0,
            readiness = manifest.get("readiness")?.asText() ?: "",
            allowedModes = modesOf(manifest.get("allowed_modes")),
            objectives = modesOf(manifest.get("objectives")),
            objectiveDetails = native.objectiveDetails,
            hardParams = native.hardParams,
            weights = native.weights,
            weightRules = native.weightRules,
            unmappedRules = native.unmappedRules,
            componentsWithoutWeight = native.componentsWithoutWeight,
            hasData = manifest.get("has_data")?.asBoolean() ?: false,
            calendarWindows = native.calendarWindows,
            logic = native.logic,
            logicUnproven = native.logicUnproven,
            objectiveToConstraint = native.objectiveToConstraint,
            dutyExtensionCapMin = native.dutyExtensionCapMin,
            dutyExtensionRules = native.dutyExtensionRules,
            workloadSpreadThresholdMin = native.workloadSpreadThresholdMin,
            workloadSpreadRules = native.workloadSpreadRules,
            dutyExtensionPolicies = native.dutyExtensionPolicies,
            landingArchiveOnly = native.landingArchiveOnly,
            ruleParams = native.ruleParams,
        )
    }

    /** Input members and their hashes have already been checked by the owning
     * envelope reader. This is the single native-semantic decoder; it cannot
     * produce execution authority or a LoadedTaskPackage.
     */
    internal fun decodeNative(files: Map<String, ByteArray>, manifest: JsonNode,
                              spec: String, listedNames: Set<String?>,
                              landingCompat: Int? = null): DecodedNativeModel {
        val om = ObjectMapper()

        // Секция rule_maps (ревизия 1.14): карты контура CDPM, собранные
        // производителем из носителя. Судится ПО ПОДСЕКЦИЯМ: подсекция
        // есть — читаем ИЗ ПАКЕТА; подсекции (или всей секции) нет —
        // легаси-таблицы кода. Кривая форма — отказ словами; подсекции
        // других потребителей здесь не читаются и не судятся.
        val ruleMaps = ruleMapsOf(manifest, spec)

        val objectiveBytes = files["objective.json"]
            ?: throw TaskPackageError("в пакете нет objective.json")
        val objective = try {
            // Scope exact decimal parsing to objective.json. In particular,
            // 9007199254740993.0 must not first round through a Double. The
            // existing manifest/model/threshold readers keep their own mapper.
            ObjectMapper().enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS)
                .readTree(objectiveBytes)
        } catch (e: Exception) {
            throw TaskPackageError("objective.json не разбирается: " + e.message)
        }

        // Машинные параметры правил (спецификация 1.3). Модель читается
        // ПОСЛЕ сверки сумм, как и всё прочее содержимое, — и ТОЛЬКО если
        // она объявлена манифестом: файл, не вошедший в подписанный
        // перечень, суммой не сверен, и брать из него пороги нельзя.
        if ("model.json" !in listedNames) {
            throw TaskPackageError(
                "model.json не объявлен в перечне files манифеста: " +
                "пороги правил из неподписанного файла не применяются")
        }
        val modelNode = modelOf(files["model.json"], om)
        val archiveOnly = landingModeOf(modelNode, manifest, spec, landingCompat)
        val paramsByRule = paramsOf(modelNode, archiveOnly)
        // HARD — требования; DEF — определения мира (линия нано
        // 2026-08-30): константы вроде сдвига шкалы времени приходят
        // параметром правила-определения. Чужие имена у закрытого
        // словаря ручек по-прежнему «не применено и названо».
        val hardParams = paramsByRule.values.flatten()
            .filter { it.ruleKind == "HARD" || it.ruleKind == "DEF" }
        val logic = if (modelNode == null) emptyList()
                    else LogicReader.read(modelNode, paramsByRule)
        val unproven = LinkedHashMap<String, String>()
        val unprovenNode = manifest.get("logic_unproven")
        if (unprovenNode != null && unprovenNode.isArray) {
            for (u in unprovenNode) {
                val r = u.get("rule")?.asText() ?: continue
                unproven[r] = u.get("reason")?.asText() ?: ""
            }
        }

        // Карта «правило -> ограничение»: подсекция пакета сильнее
        // зашитой таблицы — при живой подсекции легаси-таблица не
        // читается вовсе (верховенство пакета, ревизия 1.14).
        val ruleTable = ruleMaps.ruleToConstraint ?: RULE_TO_CONSTRAINT

        // Форма календарных окон (файл форм порогов): читается
        // только ПОДПИСАННЫЙ файл (в перечне files манифеста) —
        // тот же закон, что у model.json выше. Нет файла/секции —
        // легаси (пусто), кривая запись — отказ словами.
        val calendarWindows = calendarWindowsOf(
            files["threshold-forms.json"], listedNames, om)

        // P-N-SHD: семья механики из самого правила — штатное
        // соотнесение; карты (пакета либо легаси) — явное
        // переопределение поверх.
        val familyByRule = familiesOf(modelNode)
        val dutyExtPolicies = dutyExtensionOf(modelNode)
        val dutyExtCap = dutyExtPolicies.minOfOrNull { it.capMin }
        val dutyExtRules = dutyExtPolicies.map { it.ruleId }.distinct()
        val (spreadThreshold, spreadRules) = workloadSpreadOf(modelNode)

        val weights = LinkedHashMap<String, Long>()
        val weightRules = LinkedHashMap<String, String>()
        val unmapped = ArrayList<String>()
        val noWeight = ArrayList<String>()
        val details = LinkedHashMap<String, List<PackageObjective>>()
        for (task in listOf("pairing", "rostering")) {
            val node = objective.get(task) ?: continue
            details[task] = objectivesOf(node, task)
            val components = node.get("components") ?: continue
            for (c in components) {
                val ruleId = c.get("rule_id")?.asText() ?: continue
                val wNode = c.get("weight")
                val constraint = ruleTable[ruleId]
                    ?: familyByRule[ruleId]?.let { FAMILY_TO_CONSTRAINT[it] }
                if (constraint == null) {
                    unmapped.add(ruleId)
                    continue
                }
                if (wNode == null || wNode.isNull) {
                    noWeight.add(ruleId)
                    continue
                }
                weights[constraint] = wNode.asLong()
                weightRules[ruleId] = constraint
            }
        }

        return DecodedNativeModel(
            ruleParams = paramsByRule,
            objectiveDetails = details,
            hardParams = hardParams,
            weights = weights,
            weightRules = weightRules,
            unmappedRules = unmapped,
            componentsWithoutWeight = noWeight,
            calendarWindows = calendarWindows,
            logic = logic,
            logicUnproven = unproven,
            objectiveToConstraint = ruleMaps.objectiveToConstraint,
            dutyExtensionCapMin = dutyExtCap,
            dutyExtensionRules = dutyExtRules,
            workloadSpreadThresholdMin = spreadThreshold,
            workloadSpreadRules = spreadRules,
            dutyExtensionPolicies = dutyExtPolicies,
            landingArchiveOnly = archiveOnly,
        )
    }

    private fun refuseComponentMembers(names: Collection<String>) {
        val componentNames = setOf("component-manifest.json", "declared-world.json", "declared-expected.json")
        if (names.any { it.replace('\\', '/').substringAfterLast('/') in componentNames })
            throw TaskPackageError("component-only input is not an executable task package")
    }

    /** Закрытый словарь имён дней формы календарных окон. */
    private val DAY_NAMES: Map<String, java.time.DayOfWeek> = mapOf(
        "monday" to java.time.DayOfWeek.MONDAY,
        "tuesday" to java.time.DayOfWeek.TUESDAY,
        "wednesday" to java.time.DayOfWeek.WEDNESDAY,
        "thursday" to java.time.DayOfWeek.THURSDAY,
        "friday" to java.time.DayOfWeek.FRIDAY,
        "saturday" to java.time.DayOfWeek.SATURDAY,
        "sunday" to java.time.DayOfWeek.SUNDAY)

    /**
     * Разбор секции calendar_window_penalties файла форм порогов.
     * Валидация закрытой семантики формы: дни — имена строчными из
     * словаря семи значений (имена, не числа: ISO/питон/крон нумеруют
     * по-разному — класс сдвига на единицу закрыт по построению);
     * tz — ровно "base-local"; action — непустая строка; rule —
     * непустой идентификатор. Вес формы здесь НЕ читается: вес
     * приходит каналом весов пакета (objective -> карта -> веса).
     */
    private fun calendarWindowsOf(
        bytes: ByteArray?, listed: Set<String?>, om: ObjectMapper,
    ): List<org.acme.flighcrewscheduling.domain.CalendarWindowRule> {
        if (bytes == null) return emptyList()
        if ("threshold-forms.json" !in listed) {
            throw TaskPackageError(
                "threshold-forms.json не объявлен в перечне files " +
                "манифеста: формы из неподписанного файла не " +
                "применяются")
        }
        val root = try { om.readTree(bytes) } catch (e: Exception) {
            throw TaskPackageError(
                "threshold-forms.json не разбирается: " + e.message)
        }
        val section = root.get("calendar_window_penalties")
            ?: return emptyList()
        if (!section.isArray) {
            throw TaskPackageError(
                "calendar_window_penalties: не список")
        }
        val out = ArrayList<
            org.acme.flighcrewscheduling.domain.CalendarWindowRule>()
        for (e in section) {
            val rule = e.get("rule")?.asText()?.trim() ?: ""
            if (rule.isEmpty()) throw TaskPackageError(
                "calendar_window_penalties: запись без rule")
            val action = e.get("action")?.asText()?.trim() ?: ""
            if (action.isEmpty()) throw TaskPackageError(
                "calendar_window_penalties[" + rule + "]: " +
                "пустое action")
            val tz = e.get("tz")?.asText()
            if (tz != "base-local") throw TaskPackageError(
                "calendar_window_penalties[" + rule + "]: tz " +
                "обязан быть base-local (закрытая семантика " +
                "формы), получено: " + tz)
            val daysNode = e.get("days")
            if (daysNode == null || !daysNode.isArray ||
                daysNode.isEmpty) throw TaskPackageError(
                "calendar_window_penalties[" + rule + "]: days " +
                "обязан быть непустым списком имён дней")
            val days = LinkedHashSet<java.time.DayOfWeek>()
            for (d in daysNode) {
                val name = d.asText()
                val dow = DAY_NAMES[name] ?: throw TaskPackageError(
                    "calendar_window_penalties[" + rule + "]: " +
                    "день " + name + " вне закрытого словаря " +
                    DAY_NAMES.keys.joinToString(","))
                days.add(dow)
            }
            out.add(org.acme.flighcrewscheduling.domain
                .CalendarWindowRule(rule, action, days))
        }
        return out
    }

    /** Прочитанные подсекции rule_maps, нужные ЭТОМУ потребителю. */
    private data class RuleMapsSection(
        val ruleToConstraint: Map<String, String>?,
        val objectiveToConstraint: Map<String, String>?,
    )

    /**
     * Секция rule_maps манифеста (спецификация 1.14): карты контура CDPM
     * «правило/целевая свода -> имя ограничения решателя». Отсутствие
     * секции законно (легаси-ветка); секция в пакете до 1.14 — расхождение
     * форм, кривая форма секции — отказ словами (закон пакета). Формы
     * подсекций других потребителей судят их читатели.
     */
    private fun ruleMapsOf(manifest: JsonNode, spec: String): RuleMapsSection {
        val node = manifest.get("rule_maps")
            ?: return RuleMapsSection(null, null)
        val minor = spec.substringAfter('.', "").substringBefore('.').toIntOrNull()
        if (minor == null || minor < 14) {
            throw TaskPackageError(
                "секция rule_maps законна только со спецификации 1.14, а " +
                "пакет объявил spec_version " + spec + ": расхождение форм")
        }
        if (!node.isObject || node.size() == 0) {
            throw TaskPackageError(
                "секция rule_maps не является непустым объектом — " +
                "кривая секция, гейт производителя такую не выпускает")
        }
        return RuleMapsSection(
            ruleToConstraint = stringMapOf(node, "rule_to_constraint"),
            objectiveToConstraint = stringMapOf(node, "objective_to_constraint"),
        )
    }

    /** Подсекция вида {идентификатор: непустая строка} либо null, когда её нет. */
    private fun stringMapOf(section: JsonNode, name: String): Map<String, String>? {
        val sub = section.get(name) ?: return null
        if (!sub.isObject) {
            throw TaskPackageError("подсекция rule_maps." + name +
                " не является объектом «идентификатор -> имя ограничения»")
        }
        val out = LinkedHashMap<String, String>()
        val keys = sub.fieldNames()
        while (keys.hasNext()) {
            val key = keys.next()
            if (key.isBlank()) {
                throw TaskPackageError("в подсекции rule_maps." + name +
                    " есть запись с пустым идентификатором")
            }
            val v = sub.get(key)
            if (!v.isTextual || v.asText().isBlank()) {
                throw TaskPackageError("в подсекции rule_maps." + name +
                    " у записи " + key + " значение не является непустой строкой")
            }
            out[key] = v.asText()
        }
        return out
    }

    /**
     * Целевые одной зоны. Обязательны `id` и `direction`: целевая без
     * направления неисполнима, и догадываться о нём по имени метрики
     * нельзя — направление объявлено машинным полем (спецификация §5а).
     */
    private fun objectivesOf(zone: JsonNode, task: String): List<PackageObjective> {
        val arr = zone.get("objectives") ?: return emptyList()
        val out = ArrayList<PackageObjective>()
        for (o in arr) {
            val id = o.get("id")?.asText()
                ?: throw TaskPackageError(
                    "в зоне " + task + " есть целевая без id")
            val dir = o.get("direction")?.asText()
                ?: throw TaskPackageError(
                    "у целевой " + id + " нет машинного поля direction")
            val metricNode = o.get("metric")
            out.add(PackageObjective(
                id = id,
                title = o.get("title")?.asText() ?: "",
                statement = o.get("statement")?.asText() ?: "",
                direction = dir.trim().lowercase(),
                metric = if (metricNode == null || metricNode.isNull) null
                         else metricNode.asText().trim().ifEmpty { null },
                rate = objectiveRateOf(o, id, task),
            ))
        }
        return out
    }

    private fun objectiveRateOf(objective: JsonNode, id: String, task: String): BigDecimal? {
        if (!objective.has("rate")) return null
        val carrier = objective.get("rate")
        val value = if (carrier.isObject) carrier.get("value") else null
        if (value == null || !value.isNumber) {
            throw TaskPackageError(
                "целевая " + id + " задачи " + task +
                ": ставка rate обязана быть объектом с числом rate.value > 0; получено " + carrier)
        }
        val rate = value.decimalValue()
        if (rate.signum() <= 0) {
            throw TaskPackageError(
                "целевая " + id + " задачи " + task +
                ": ставка rate.value обязана быть > 0; получено " + rate)
        }
        return rate
    }

    private fun modelOf(modelBytes: ByteArray?, om: ObjectMapper): JsonNode? {
        if (modelBytes == null) return null
        return try {
            om.readTree(modelBytes)
        } catch (e: Exception) {
            throw TaskPackageError("model.json не разбирается: " + e.message)
        }
    }

    /**
     * Машинные параметры правил модели, по правилам (вид — в записи).
     *
     * К прогону применяются параметры видов HARD (требования) и DEF
     * (определения мира — например, сдвиг шкалы времени; введено линией
     * нано 2026-08-30, см. [LoadedTaskPackage.hardParams]): значения
     * отменённых (OUT) и справочных заказчик либо отозвал, либо
     * не объявлял требованием — применять их к прогону значило бы
     * поверять решатель тем, чего авиакомпания не просила. Параметры
     * прочих видов читаются ради блоков logic: ссылка атома обязана
     * указывать на запись СВОЕГО правила, и проверять это нужно для любого
     * вида. Модели без параметров (спецификация до 1.3) дают пустой
     * перечень, а не отказ: поле необязательное.
     */
    /**
     * P-N-SHD: семьи механик правил модели — rule id →
     * export_solver.family (строка закрытого словаря). Поле
     * необязательное: модели до контракта дают пустой перечень.
     * Неизвестное словарю имя здесь НЕ отбрасывается — судит
     * потребитель (FAMILY_TO_CONSTRAINT вернёт null, правило честно
     * останется несоотнесённым и попадёт в отчёт покрытия).
     */
    private fun familiesOf(model: JsonNode?): Map<String, String> {
        if (model == null) return emptyMap()
        val out = LinkedHashMap<String, String>()
        for (rule in (model.get("rules") ?: return emptyMap())) {
            // Исключённое до получения данных правило не исполняется:
            // ни семьи, ни порогов, ни logic (структура ВМП 2026-09-02).
            if (isExcludedUntilData(rule)) continue
            val ruleId = rule.get("id")?.asText() ?: continue
            val fam = familyOfRule(rule)
            if (!fam.isNullOrBlank()) out[ruleId] = fam
        }
        return out
    }

    /**
     * Семья механики правила: ПОСАДКА (template_fit пакета A/B/C/D,
     * решение владельца 2026-09-01 «от ВМП и далее исключительно в
     * COSMOS») — первичный канонический источник; поле
     * export_solver.family — легаси для моделей без посадок. Посадки
     * категорий C/D семьи не несут по контракту.
     */
    private fun familyOfRule(rule: JsonNode): String? {
        val fit = rule.get("template_fit")
        if (fit != null) {
            val cat = fit.get("category")?.asText()
            if (cat == "A" || cat == "B") {
                val fam = fit.get("family")?.asText()
                if (!fam.isNullOrBlank()) return fam
            }
        }
        return rule.get("export_solver")?.get("family")?.asText()
    }

    /**
     * Потолок семьи duty_extension_penalty: у мягкого правила семьи
     * параметр max_duty_duration задаёт жёсткий предел продлённой
     * смены (мягкий порог — базовый предел пакета, штраф — вес
     * правила). Несколько правил семьи — строжайший потолок
     * (минимум). Кривой потолок — отказ словами: молчаливое
     * игнорирование выглядело бы как «прислали, не применили».
     */
    /**
     * Порог существенности разброса кредитных часов семьи
     * balanced_workload: параметр правила workload_spread_threshold
     * (часы; единица словаря credit_hours) либо тот же параметр
     * посадки. Правило семьи без параметра — порог 0 (штрафуется весь
     * разброс). Несколько правил семьи с разными порогами — отказ
     * словами: исполнитель один, «строжайший» здесь не выбрать честно.
     * Исключённое правило порога не задаёт.
     */
    private fun workloadSpreadOf(model: JsonNode?): Pair<Int?, List<String>> {
        if (model == null) return null to emptyList()
        var thresholdMin: Int? = null
        val rules = ArrayList<String>()
        for (rule in (model.get("rules") ?: return null to emptyList())) {
            if (isExcludedUntilData(rule)) continue
            if (familyOfRule(rule) != "balanced_workload") continue
            val ruleId = rule.get("id")?.asText() ?: continue
            var hours: JsonNode? = rule.get("template_fit")?.get("params")
                ?.get("workload_spread_threshold")?.get("value")
            for (p in (rule.get("params") ?: emptyList<JsonNode>())) {
                if (p.get("name")?.asText() == "workload_spread_threshold") {
                    hours = p.get("value")
                }
            }
            val minutes = if (hours == null || hours.isNull) 0 else {
                if (!hours.isNumber || hours.asDouble() < 0.0) {
                    throw TaskPackageError(
                        "правило " + ruleId + " семьи balanced_workload: порог " +
                        "workload_spread_threshold обязан быть числом часов ≥ 0, " +
                        "получено " + hours.asText())
                }
                Math.round(hours.asDouble() * 60.0).toInt()
            }
            if (thresholdMin != null && thresholdMin != minutes) {
                throw TaskPackageError(
                    "правила семьи balanced_workload расходятся в пороге разброса: " +
                    rules.joinToString(", ") + " → " + thresholdMin + " мин, " +
                    ruleId + " → " + minutes + " мин; исполнитель один, " +
                    "постановка обязана назвать один порог")
            }
            thresholdMin = minutes
            rules.add(ruleId)
        }
        return thresholdMin to rules
    }

    private fun dutyExtensionOf(model: JsonNode?): List<DutyExtensionPolicy> {
        if (model == null) return emptyList()
        val policies = ArrayList<DutyExtensionPolicy>()
        for (rule in (model.get("rules") ?: return emptyList())) {
            // Исключённое правило потолок продления не поднимает
            // (аудит №2: расчёт идёт БЕЗ правила).
            if (isExcludedUntilData(rule) || rule.get("kind")?.asText() == "OUT") continue
            val fam = familyOfRule(rule)
            if (fam != "duty_extension_penalty" &&
                rule.get("template_fit")?.get("family")?.asText() != "duty_extension_penalty") continue
            val ruleId = rule.get("id")?.asText() ?: continue
            fun bad(why: String): Nothing = throw TaskPackageError(
                "правило $ruleId семьи duty_extension_penalty: $why")
            val fit = rule.get("template_fit")
            val params = fit?.get("params")
            if (fit != null && (fit.get("category")?.asText() !in listOf("A", "B") ||
                    params == null || !params.isObject)) bad("неполная/неопределённая посадка")
            fun value(name: String): JsonNode? {
                val cell = params?.get(name) ?: return null
                if (!cell.isObject || !cell.has("value") || cell.get("value").isNull ||
                    cell.has("pending_revision")) bad("$name: неполная ячейка или открытый пересмотр")
                return cell.get("value")
            }
            params?.fieldNames()?.forEachRemaining { name ->
                if (name.startsWith("extension_") && name !in setOf("extension_bases", "extension_consent"))
                    bad("неподдержанное условие $name")
            }
            val bases = value("extension_bases")?.let { node ->
                val codes = when {
                    node.isTextual -> node.asText().split(',').map { it.trim() }
                    node.isArray && node.all { it.isTextual } -> node.map { it.asText() }
                    else -> bad("extension_bases: требуется перечень кодов")
                }
                if (codes.isEmpty() || codes.any { it.isBlank() }) bad("extension_bases: пустой код/перечень")
                codes.distinct()
            }
            val consent = value("extension_consent")?.let {
                if (!it.isBoolean) bad("extension_consent: требуется true/false")
                it.asBoolean()
            } ?: false
            fun thresholdOf(node: JsonNode?): Int {
                if (node == null || !node.isIntegralNumber || !node.canConvertToInt() || node.asInt() <= 0)
                    bad("long_duty_threshold: требуется целое число минут > 0 в диапазоне Int")
                return node.asInt()
            }
            var threshold = value("long_duty_threshold")?.let { thresholdOf(it) }
            var cap: Int? = null
            // Потолок из ПОСАДКИ — словарное имя duty_extension_cap
            // (пакет A/B/C/D: параметр потолка принадлежит семье
            // продления; max_duty_duration словарь держит за семьёй
            // duty_limits как базовый предел).
            val fitCap = value("duty_extension_cap")
            if (fitCap != null) {
                if (!fitCap.isIntegralNumber || !fitCap.canConvertToInt() || fitCap.asInt() <= 0) {
                    throw TaskPackageError(
                        "правило " + ruleId + " семьи duty_extension_penalty: " +
                        "потолок duty_extension_cap обязан быть целым числом " +
                        "минут > 0, получено " + fitCap.asText())
                }
                cap = fitCap.asInt()
            }
            for (p in (rule.get("params")?.toList() ?: emptyList())) {
                if (p.get("name")?.asText() == "long_duty_threshold") {
                    val alias = thresholdOf(p.get("value"))
                    if (threshold != null && threshold != alias)
                        bad("носители long_duty_threshold расходятся: $threshold и $alias")
                    threshold = alias
                    continue
                }
                if (p.get("name")?.asText() != "max_duty_duration") continue
                val v = p.get("value")
                if (v == null || !v.isIntegralNumber || !v.canConvertToInt() || v.asInt() <= 0) {
                    throw TaskPackageError(
                        "правило " + ruleId + " семьи duty_extension_penalty: " +
                        "потолок max_duty_duration обязан быть целым числом " +
                        "минут > 0, получено " + (v?.asText() ?: "<нет>"))
                }
                val minutes = v.asInt()
                if (cap != null && cap != minutes) bad("носители потолка расходятся: $cap и $minutes")
                cap = minutes
            }
            if (cap == null) bad("нет потолка duty_extension_cap")
            policies.add(DutyExtensionPolicy(ruleId, cap, bases, consent, threshold))
        }
        return policies
    }

    private fun landingModeOf(model: JsonNode?, manifest: JsonNode, spec: String,
                              readerCompat: Int?): Boolean {
        fun bad(code: String, why: String): Nothing = throw TaskPackageError("$code: $why")
        val declaredCompat = manifest.get("landing_compat")
        if (readerCompat != null && readerCompat != 1 || declaredCompat != null &&
            (!declaredCompat.isIntegralNumber || !declaredCompat.canConvertToInt() || declaredCompat.intValue() != 1))
            bad("landing-version", "landing_compat requires integer 1")
        val parts = spec.split('.')
        if (parts.size != 2 || parts.any { it.isEmpty() || it.any { c -> c !in '0'..'9' } })
            bad("landing-version", "invalid package spec_version")
        val minor = parts[1].toIntOrNull() ?: bad("landing-version", "invalid package spec_version")
        // Historical wire is archival, never executable. Its legacy landing
        // still requires BOTH explicit compatibility flags, regardless of spec.
        // This also prevents a modern package downgraded to an old spec from
        // acquiring execution through a permissive old parameter reader.
        val oldWire = minor < 20
        var archive = oldWire
        val schema = model?.get("meta")?.get("model_schema")
        if (schema != null && (!schema.isIntegralNumber || !schema.canConvertToInt() || schema.intValue() !in 1..2))
            bad("landing-version", "unsupported model_schema")
        val requireModern = !oldWire || schema?.intValue() == 2
        val rules = model?.get("rules")
        if (requireModern && (rules == null || !rules.isArray))
            bad("landing-incomplete", "modern model requires rules array")
        for (rule in rules ?: return archive) {
            if (!rule.isObject) bad("landing-incomplete", "rule must be an object")
            val fit = rule.get("template_fit")
            if (fit != null && !fit.isObject) bad("landing-incomplete", "template_fit must be an object")
            val needsLanding = fit != null || rule.path("kind").asText() in setOf("HARD", "SOFT", "УСЛОВНЫЙ") ||
                (rule.path("kind").asText() == "DEF" && rule.has("params"))
            if (!needsLanding) continue
            val version = fit?.get("schema_version")
            if (version != null && (!version.isTextual || version.textValue() !in setOf("landing/1", "landing/2")))
                bad("landing-version", "unsupported landing version for ${rule.path("id").asText()}")
            if (version?.textValue() != "landing/2") {
                archive = true
                if (!(readerCompat == 1 && declaredCompat?.intValue() == 1))
                    bad("landing-version", "legacy landing requires explicit reader and manifest compatibility; execution forbidden")
            }
        }
        return archive
    }

    private fun paramsOf(model: JsonNode?, archiveOnly: Boolean): Map<String, List<RuleParam>> {
        if (model == null) return emptyMap()
        val out = LinkedHashMap<String, MutableList<RuleParam>>()
        for (rule in (model.get("rules") ?: return emptyMap())) {
            // Пометка «исключено до получения данных по решению
            // авиакомпании» (структура ВМП 2026-09-02): правило из
            // исполнения исключено — его параметры в пороги не идут.
            if (isExcludedUntilData(rule)) continue
            val kind = rule.get("kind")?.asText() ?: ""
            val ruleId = rule.get("id")?.asText() ?: ""
            val legacy = rule.get("params")
            val legacyArchive = archiveOnly && rule.get("template_fit")?.get("schema_version")?.textValue() != "landing/2"
            val typed = if (legacyArchive) null else TypedLanding.params(rule)
            if (typed != null) {
                out.getOrPut(ruleId) { ArrayList() }.addAll(typed)
                if (legacy == null || legacy.isNull) continue
            }
            if (legacy == null) {
                // Посадка (пакет A/B/C/D) — первичный носитель
                // машинных параметров ВМП: правило без легаси-массива
                // params отдаёт параметры из template_fit.params (та
                // же граница, что familyOfRule). Канон per-base строк
                // ("GRT:1,PLY:2,SVG:1", решение К-2) разворачивается
                // в записи с qualifier.
                val fp = fitParamsOf(rule, ruleId, kind)
                if (fp.isNotEmpty()) {
                    out.getOrPut(ruleId) { ArrayList() }.addAll(fp)
                }
                continue
            }
            for (p in legacy) {
                val name = p.get("name")?.asText() ?: continue
                // Typed landing is primary, after both original carriers
                // have been compared. Keep only genuinely legacy-only names.
                if (typed != null && rule.get("template_fit")?.get("params")?.has(name) == true) continue
                val vNode = p.get("value")
                val codeValues = codeValuesOf(name, vNode, ruleId,
                    p.get("unit")?.asText(), typed = false, allowArray = false)
                // Число принимается любым: дробные кредитные часы законны.
                // Годность как ПОРОГА (целое, положительное) проверяется
                // при сведении — и только у параметров, которые решатель
                // применяет. Ревизия 1 роняла весь пакет из-за дробного
                // значения параметра, который транслятор не трогает.
                val num = if (vNode != null && vNode.isNumber) vNode.asDouble() else null
                out.getOrPut(ruleId) { ArrayList() }.add(RuleParam(
                    name = name,
                    value = num,
                    rawValue = if (codeValues != null && vNode?.isContainerNode == true)
                        vNode.toString() else vNode?.asText() ?: "null",
                    unit = p.get("unit")?.asText() ?: "",
                    relation = p.get("relation")?.asText() ?: "",
                    qualifier = p.get("qualifier")?.asText(),
                    asWritten = p.get("as_written")?.asText() ?: "",
                    ruleId = ruleId,
                    tz = p.get("tz")?.asText(),
                    ruleKind = kind, codeValues = codeValues))
            }
        }
        return out
    }

    private val PER_BASE_RE = Regex("^([A-ZА-ЯЁ0-9]+:\\d+)(,[A-ZА-ЯЁ0-9]+:\\d+)*$")

    /** Judge code shape before asText can collapse JSON null/arrays into an apparent scalar.
     * No designation is reserved: in particular the STRING "null" is an ordinary label.
     */
    private fun codeValuesOf(name: String, node: JsonNode?, ruleId: String, unit: String?,
                             typed: Boolean, allowArray: Boolean): List<String>? {
        if (name !in RunThresholds.FLEET_PARAMS) return null
        fun bad(why: String): Nothing = throw TaskPackageError("правило $ruleId, параметр $name: $why")
        val expectedUnit = if (name == "fleet_type") "code" else "code_list"
        if (!unit.isNullOrEmpty() && unit != expectedUnit)
            bad("единица $unit не соответствует $expectedUnit")
        if (node == null || node.isNull) bad("value отсутствует/JSON null — это не обозначение")
        val codes = when {
            node.isTextual && expectedUnit == "code" -> {
                // landing/2 code is one opaque string: punctuation and outer
                // whitespace are identity, not legacy list separators/padding.
                if (!typed && ',' in node.asText()) bad("code несёт одно обозначение, не перечень")
                listOf(if (typed) node.asText() else node.asText().trim())
            }
            node.isTextual && !typed -> node.asText().split(',').map { it.trim() }
            node.isArray && allowArray && expectedUnit == "code_list" -> {
                if (node.any { !it.isTextual }) bad("перечень должен состоять из строковых обозначений")
                node.map { it.asText() }
            }
            else -> bad("value не соответствует форме $expectedUnit")
        }
        if (codes.isEmpty() || codes.any { it.isBlank() }) bad("пустой перечень/обозначение")
        if (typed && codes.distinct().size != codes.size) bad("перечень содержит повтор обозначения")
        return codes
    }

    /**
     * Пометка строки ВМП «исключено до получения данных по решению
     * авиакомпании»: объект excluded_until_data с непустым question
     * (та же форма, что vmp/registry_check.is_excluded_until_data).
     * Такое правило решатель не исполняет: ни пороги, ни logic.
     *
     * Член самого object TaskPackage: блок companion внутри standalone
     * object недопустим в Kotlin (правка 5e6dbac не собиралась; найдено
     * сборкой профиля Д1 2026-09-03).
     */
    fun isExcludedUntilData(rule: JsonNode?): Boolean {
        val ex = rule?.get("excluded_until_data") ?: return false
        if (!ex.isObject) return false
        val q = ex.get("question") ?: return false
        return !q.isNull && q.asText().isNotBlank()
    }

    /** Параметры из посадки правила (template_fit категорий A/B). */
    private fun fitParamsOf(rule: JsonNode, ruleId: String,
                            kind: String): List<RuleParam> {
        val fit = rule.get("template_fit") ?: return emptyList()
        val cat = fit.get("category")?.asText()
        if (cat != "A" && cat != "B") return emptyList()
        val params = fit.get("params") ?: return emptyList()
        val outp = ArrayList<RuleParam>()
        val names = params.fieldNames()
        while (names.hasNext()) {
            val name = names.next()
            val cell = params.get(name) ?: continue
            val vNode = cell.get("value")
            val quote = cell.get("quote")?.asText() ?: ""
            val codeValues = codeValuesOf(name, vNode, ruleId, cell.get("unit")?.asText(),
                typed = fit.get("schema_version")?.asText() == "landing/2", allowArray = true)
            val raw = if (codeValues != null && vNode?.isContainerNode == true)
                vNode.toString() else vNode?.asText() ?: "null"
            // Единица и отношение — из СОБСТВЕННОЙ карты ручек
            // решателя (посадка единиц не несёт — канон словаря
            // живёт по обе стороны, здесь авторитет — RunThresholds).
            val knob = RunThresholds.knobByName(name)
            val unit = knob?.unit ?: if (codeValues != null)
                (if (name == "fleet_type") "code" else "code_list") else ""
            val relation = knob?.relation ?: ""
            if (codeValues == null && vNode != null && vNode.isTextual
                    && PER_BASE_RE.matches(raw)) {
                for (part in raw.split(",")) {
                    val base = part.substringBefore(":")
                    val n = part.substringAfter(":").toDouble()
                    outp.add(RuleParam(
                        name = name, value = n, rawValue = part,
                        unit = unit, relation = "min",
                        qualifier = base, asWritten = quote,
                        ruleId = ruleId, ruleKind = kind))
                }
                continue
            }
            val num = if (vNode != null && vNode.isNumber)
                vNode.asDouble() else null
            outp.add(RuleParam(
                name = name, value = num, rawValue = raw,
                unit = unit, relation = relation,
                qualifier = null, asWritten = quote,
                ruleId = ruleId, ruleKind = kind, codeValues = codeValues))
        }
        return outp
    }

    private fun modesOf(node: JsonNode?): Map<String, List<String>> {
        if (node == null || !node.isObject) return emptyMap()
        val out = LinkedHashMap<String, List<String>>()
        val names = node.fieldNames()
        while (names.hasNext()) {
            val task = names.next()
            out[task] = node.get(task).map { v -> v.asText() }
        }
        return out
    }

    private fun readDir(dir: File): Map<String, ByteArray> =
        dir.listFiles()?.filter { it.isFile }
            ?.associate { it.name to it.readBytes() } ?: emptyMap()

    private fun readZip(zip: File): Map<String, ByteArray> {
        val out = LinkedHashMap<String, ByteArray>()
        try {
            ZipFile(zip).use { z ->
                val entries = z.entries()
                while (entries.hasMoreElements()) {
                    val e = entries.nextElement()
                    if (e.isDirectory) continue
                    putMember(out, e.name, z.getInputStream(e).readBytes())
                }
            }
        } catch (e: Exception) {
            throw TaskPackageError("пакет-архив не читается: " + e.message)
        }
        return out
    }

    /**
     * Запись архива в карту членов. Корень пакета плоский; вложенные записи
     * (дополнение к 1.17 от 06.09.2026: клаузы заморозки freeze/clauses/<sid>.json)
     * доступны по полному имени с «/», как их называет manifest.files, и по
     * короткому имени — если его не занял файл корня (файл корня сильнее).
     */
    internal fun putMember(out: MutableMap<String, ByteArray>, rawName: String, body: ByteArray) {
        val rel = rawName.replace('\\', '/').trimStart('/')
        if (rel.isEmpty()) return
        out.putIfAbsent(rel, body)
        val short = rel.substringAfterLast('/')
        if (rel.contains('/')) {
            out.putIfAbsent(short, body)
        } else {
            out[short] = body
        }
    }
}
