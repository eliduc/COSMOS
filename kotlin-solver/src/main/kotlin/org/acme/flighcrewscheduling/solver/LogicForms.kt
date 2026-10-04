package org.acme.flighcrewscheduling.solver

import ai.timefold.solver.core.api.score.HardSoftScore
import ai.timefold.solver.core.api.score.stream.Constraint
import ai.timefold.solver.core.api.score.stream.ConstraintCollectors
import ai.timefold.solver.core.api.score.stream.ConstraintFactory
import ai.timefold.solver.core.api.score.stream.Joiners
import ai.timefold.solver.core.api.score.stream.bi.BiConstraintStream
import ai.timefold.solver.core.api.score.stream.uni.UniConstraintStream
import org.acme.flighcrewscheduling.domain.Employee
import org.acme.flighcrewscheduling.domain.Flight
import org.acme.flighcrewscheduling.domain.FlightAssignment
import org.acme.flighcrewscheduling.domain.FlightCrewSchedule
import org.acme.flighcrewscheduling.domain.AccountingWindow
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.YearMonth
import java.util.function.BiFunction
import java.util.function.Function

/**
 * План порождённого ограничения — функция ТОЛЬКО пакета (package-plan).
 *
 * Всё, что читает исполнитель, лежит здесь явно: форма, предмет,
 * отношение, граница и её параметр, область, вес и его источник. Число
 * клеток и прочие факты набора сюда не входят — они считаются на каждом
 * входе отдельно ([LogicForms.cells]).
 */
data class LogicPlan(
    /** правила, сведённые в этот план (одна форма — один исполнитель). */
    val ruleIds: List<String>,
    val constraintId: String,
    val form: String,
    val subject: String,
    val relation: String,
    val bound: Int,
    val boundParam: String,
    /** код базы из scope либо null — все сотрудники. */
    val baseScope: String?,
    /** вторая граница формы (порог условия): «после смены длиннее X» — X минут. */
    val condBound: Int? = null,
    val condParam: String? = null,
    /** окно времени суток (ночная смена): минуты от полуночи UTC, [from, to), через полночь законно. */
    val nightFrom: Int? = null,
    val nightTo: Int? = null,
    val enforce: String,
    /** вес за единицу нехватки/превышения — компас поиска COSMOS, не норма заказчика. */
    val weight: Int,
    val reading: String,
    val shapeSha: String,
    /** МЯГКАЯ форма (волна конвейера П-2): литерал 1 за единицу счёта,
     *  вес компоненты пакета применяется весами прогона, не формой. */
    val soft: Boolean = false,
) {
    /** Счёт формы: мягкая — в мягкие, жёсткая — в жёсткие. */
    fun score(): ai.timefold.solver.core.api.score.HardSoftScore =
        if (soft) ai.timefold.solver.core.api.score.HardSoftScore.ofSoft(weight.toLong())
        else ai.timefold.solver.core.api.score.HardSoftScore.ofHard(weight.toLong())

    fun snapshot(): Map<String, Any?> = linkedMapOf(
        "rules" to ruleIds, "constraint" to constraintId, "form" to form,
        "subject" to subject, "relation" to relation, "bound" to bound,
        "boundParam" to boundParam, "baseScope" to baseScope,
        "condBound" to condBound, "condParam" to condParam,
        "nightFrom" to nightFrom, "nightTo" to nightTo, "enforce" to enforce,
        "weight" to weight, "weightSource" to LogicForms.WEIGHT_SOURCE,
        "reading" to reading, "shapeSha" to shapeSha, "soft" to soft)
}

/** Клетка универсума «сотрудник × календарный месяц»; `carryDays` —
 *  сутки, отработанные до среза истории в этом месяце (ревизия 1.17;
 *  0 без истории и у полных месяцев горизонта). */
data class EmployeeMonth(val employeeId: String, val month: YearMonth, val carryDays: Long = 0L)

/**
 * Горизонт учёта (K-14 единой версии): объявленное `window_horizon` либо
 * единый fallback — календарные сутки от первого до последнего ВЫЛЕТА
 * (d1ee521; прилёт после границы календарь не расширяет). `lo`/`hi` —
 * затронутые календарные даты; `startInclusive`/`endExclusive` — точные
 * границы [start, end) для непрерывных форм.
 */
data class Horizon(val lo: LocalDate, val hi: LocalDate,
                   val startInclusive: LocalDateTime = lo.atStartOfDay(),
                   val endExclusive: LocalDateTime = hi.plusDays(1).atStartOfDay())

/** Промежуток [from, to] в шкале меток — занятость смены либо отдых. */
internal data class RestInterval(val from: LocalDateTime, val to: LocalDateTime)

/** Итог непрерывной проверки окон отдыха (K-15): критические клетки —
 *  точки разреза и открытые промежутки между ними; expected = passed +
 *  violated + unknown. */
internal data class RollingRestAudit(val expectedCells: Long, val passedCells: Long,
                                     val violatedCells: Long, val unknownCells: Long)

/**
 * Формы первой волны ступени 3 — каждая со своим независимым прочтением
 * на стороне Python (webapp/logic_oracle.py) и мутационной проверкой.
 *
 * Первая форма — ВЫХОДНЫХ В МЕСЯЦ НЕ МЕНЕЕ N (правила пакета о
 * минимуме выходных):
 * `count_within(min, param)` над `day_off` по универсуму `employee×month`.
 * Почему она первая: исполнителя у неё нет ни среди имён профиля, ни среди
 * показаний; она требует универсума с подстановкой нуля (механика, нужная
 * скользящему окну); клеток — сотрудников × полных месяцев, дёшево.
 */
object LogicForms {

    const val FORM_DAYS_OFF_PER_MONTH_MIN = "DAYS_OFF_PER_MONTH_MIN"
    const val FORM_CONSECUTIVE_DAYS_MAX = "CONSECUTIVE_DAYS_MAX"
    const val FORM_REST_AFTER_LONG_DUTY_MIN = "REST_AFTER_LONG_DUTY_MIN"
    const val FORM_REST_IN_ROLLING_WINDOW = "REST_IN_ROLLING_WINDOW"
    const val FORM_CONSECUTIVE_NIGHT_DUTIES_MAX = "CONSECUTIVE_NIGHT_DUTIES_MAX"
    // Счётчики скользящих окон (решение владельца 05.09.2026 «нужно
    // учитывать»: UZ — «2 выходных за 7 суток», «не более 2 ночных за
    // 3 суток»). Окна через границу горизонта — по посуточному календарю
    // истории (ревизия 1.17.2).
    const val FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN = "DAYS_OFF_IN_ROLLING_WINDOW_MIN"
    const val FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX = "NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX"
    // Лимит серии рабочих суток подряд и обязательный отдых после неё
    // (семья истории duty_series_limit, заявка CDPM 09.09.2026): у формы
    // ДВЕ границы — длина серии и число свободных суток после неё, и
    // ни одна из них не выводится из другой.
    const val FORM_DUTY_SERIES_LIMIT = "DUTY_SERIES_LIMIT"

    // ---- мягкие формы волны 1 конвейера П-2 (литерал 1 за единицу) ----
    const val FORM_SIT_OVERAGE_STEPS = "SIT_OVERAGE_STEPS"
    const val FORM_DUTY_SPLIT_SIT = "DUTY_SPLIT_SIT"
    const val FORM_CHAIN_NIGHT_DAY_MIX = "CHAIN_NIGHT_DAY_MIX"
    const val FORM_UNMET_REQUEST = "UNMET_REQUEST"

    /** Словарные имена шаблонов волны (пары производителя) → формы.
     *  Ф-4 переиспользует форму «дней подряд» ступени 3 мягкой веткой. */
    val TEMPLATE_TO_FORM: Map<String, String> = mapOf(
        "штраф-ступенчатый-за-стыковку" to FORM_SIT_OVERAGE_STEPS,
        "штраф-за-разрыв-смены" to FORM_DUTY_SPLIT_SIT,
        "однородность-цепочки-ночь-день" to FORM_CHAIN_NIGHT_DAY_MIX,
        "мягкий-предел-дней-подряд" to FORM_CONSECUTIVE_DAYS_MAX,
        "неудовлетворённая-выполнимая-заявка" to FORM_UNMET_REQUEST,
    )

    /** Роли ссылок мягких шаблонов (этап 2 волны, word-в-word с CDPM:
     *  cdpm/model.py — demand.kind = словарное имя, числа ТОЛЬКО
     *  ссылками `*_param` на params правила). Ровно эти роли: лишняя
     *  либо недостающая — отказ, решатель не угадывает. */
    val TEMPLATE_ROLES: Map<String, Set<String>> = mapOf(
        "штраф-ступенчатый-за-стыковку" to setOf("threshold_param", "step_param"),
        "штраф-за-разрыв-смены" to setOf("threshold_param"),
        "однородность-цепочки-ночь-день" to setOf("window_from_param", "window_to_param"),
        "мягкий-предел-дней-подряд" to setOf("bound_param"),
        "неудовлетворённая-выполнимая-заявка" to emptySet(),
    )

    const val WEIGHT_SOURCE = "лестница жёстких весов COSMOS (компас поиска, не норма заказчика): " +
        "HARD_LOGIC_SHORTFALL за единицу нехватки"

    const val READING_DAYS_OFF = "выходной — календарные сутки UTC, которых не касается ни одно " +
        "назначение сотрудника (даты вылета и прилёта включительно); дни недоступности " +
        "(unavailableDays) — выходные; клетка — сотрудник × ПОЛНЫЙ календарный месяц " +
        "объявленного window_horizon (без него — календарные сутки от первого до последнего " +
        "ВЫЛЕТА набора, прилёт после границы календарь не расширяет); неполные месяцы вне " +
        "оценки и перечислены в отчёте; сотрудник без назначений в месяце имеет все дни " +
        "месяца выходными; ИСТОРИЯ (ревизия 1.17): месяц среза истории судится у сотрудника с " +
        "историей, когда месяц начат до горизонта и горизонт доходит до его конца — занятые сутки = " +
        "отработанные до среза (month_to_date.days_worked) + занятые сутки горизонта; без истории " +
        "неполный месяц вне оценки"

    const val READING_CONSECUTIVE = "рабочий день — сутки UTC, которых касается хотя бы одно " +
        "назначение сотрудника (даты вылета и прилёта включительно; две смены в одни сутки — " +
        "один рабочий день); серия — подряд идущие рабочие сутки; штраф — за каждый день " +
        "сверх границы в каждой серии; ИСТОРИЯ (ревизия 1.17): серия, начатая в день среза " +
        "истории, продолжает серию рабочих дней истории (series.consecutive_duty_days); без " +
        "истории серия, продолжающаяся за границы горизонта, видна только внутри него"

    const val READING_ROLLING = "отдых — непрерывный промежуток вне нормативных смен сотрудника " +
        "(нарезка по порогам отдыха прогона; стыковка внутри смены — не отдых); окно W = N × 1440 " +
        "минут заканчивается в любой момент горизонта учёта (объявленного window_horizon, без него — " +
        "суток от первого до последнего вылета), включая границы; отдых [a,b] длиной не меньше R " +
        "доказывает окна с концом в [a+R, b+W−R]; проверяются критические точки и открытые " +
        "промежутки между ними (непрерывное покрытие, не суточная сетка); штраф — за каждую " +
        "доказанно нарушенную клетку с полностью известным окном; сотрудник без назначений " +
        "судится тоже; ИСТОРИЯ (ревизия 1.17): конец последней смены (last_duty.end) расширяет " +
        "известный промежуток только при as_of = началу горизонта; явные выходные календаря до " +
        "горизонта — известный отдых; допущение нулевой истории (historyAssumedZero) — известный " +
        "отдых от среза; неизвестные окна не штрафуются и не доказывают допустимость; " +
        "суточная форма (старты по календарным суткам) сохранена как диагностика"

    const val READING_NIGHT = "смена — нарезка назначений по порогам отдыха прогона; ночная " +
        "смена — смена, чей ПЕРВЫЙ вылет попадает в окно времени суток UTC [от, до) (окно через " +
        "полночь законно); «подряд» — соседние смены в последовательности смен сотрудника (между " +
        "ними нет других смен), календарный разрыв между ними серию не рвёт — единица «смена», не " +
        "«сутки»; штраф — за каждую ночную смену сверх границы в каждой серии; ИСТОРИЯ (ревизия " +
        "1.17): серия ночных из истории (series.consecutive_night_duties) продолжается первой " +
        "сменой горизонта, если та ночная; без истории — 0"

    const val READING_DAYS_OFF_ROLLING = "выходной — календарные сутки UTC горизонта, которых не " +
        "касается ни одно назначение сотрудника; окно — N подряд идущих суток, старты окон — сутки " +
        "объявленного window_horizon (без него — от первого до последнего ВЫЛЕТА набора), где окно " +
        "целиком помещается; " +
        "нарушение — выходных в окне меньше K, штраф — за каждый недостающий выходной в каждом окне; " +
        "сотрудник без назначений выполняет все окна; ИСТОРИЯ (ревизия 1.17.2): посуточный календарь " +
        "до горизонта открывает окна через его границу — выходной там только явный день отдыха " +
        "(day_off, leave, sick), работа и резерв — занятые сутки, сутки без записи неизвестны, и окно " +
        "с ними не судится; без календаря старты окон только внутри горизонта"

    const val READING_NIGHT_ROLLING = "смена — нарезка назначений по порогам отдыха прогона; ночная " +
        "смена — смена, чей ПЕРВЫЙ вылет попадает в окно времени суток [от, до) в шкале меток (окно " +
        "через полночь законно); смена относится к суткам своего начала; окно — N подряд идущих суток, " +
        "старты окон — сутки горизонта набора, где окно целиком помещается; нарушение — ночных смен в " +
        "окне больше K, штраф — за каждую сверх K в каждом окне; ИСТОРИЯ (ревизия 1.17.2): ночные сутки " +
        "посуточного календаря до горизонта входят в окна через его границу; сутки без записи или " +
        "рабочие без признака ночи неизвестны, и окно с ними не судится"

    const val READING_DUTY_SERIES = "рабочий день — сутки UTC, которых касается хотя бы одно " +
        "назначение сотрудника (даты вылета и прилёта включительно; две смены в одни сутки — " +
        "один рабочий день); серия — подряд идущие рабочие сутки; свободные сутки после серии — " +
        "разрыв до начала следующей серии минус один; нарушение двойное: серия длиннее предела " +
        "(штраф за каждые сутки сверх) и серия, ДОСТИГШАЯ предела, после которой свободных суток " +
        "меньше требуемого (штраф за каждые недостающие); ИСТОРИЯ (ревизия 1.17): серия, начатая " +
        "в день среза истории, продолжает серию рабочих дней истории (series.consecutive_duty_days); " +
        "ПРИБЛИЖЕНИЕ: хвост ПОСЛЕДНЕЙ серии сотрудника не судится — следующей серии в наборе нет, " +
        "и «свободных суток после» там неизвестно, а неизвестное не штрафуется"

    const val READING_REST_AFTER = "смена — нарезка назначений по порогам отдыха прогона " +
        "(дома / вне базы), та же, что у правил размаха и посадок; продолжительность смены — " +
        "размах от первого вылета до последнего прилёта; отдых после смены — разрыв до первого " +
        "вылета следующей смены; штраф — за каждую минуту недобора; разрыв меньше порога " +
        "отдыха — не граница смен (его судят правила отдыха и размаха); ПРИБЛИЖЕНИЕ: отдых " +
        "после последней смены горизонта не судится"

    const val READING_SIT_OVERAGE = "стыковка — разрыв между соседними назначениями сотрудника " +
        "ВНУТРИ смены (нарезка смен — порогами отдыха прогона); единица — ступень: " +
        "floor((стыковка − порог) / шаг) за каждую стыковку длиннее порога; перегонное плечо " +
        "в смене — та же стыковка вокруг него (зачёт в рабочее время)"

    const val READING_DUTY_SPLIT = "единица — СМЕНА, внутри которой есть стыковка длиннее " +
        "порога (одна на смену, сколько бы длинных стыковок она ни несла — «за её наличие " +
        "внутри смены»); нарезка смен — порогами отдыха прогона"

    const val READING_CHAIN_MIX = "цепочка — последовательность смен сотрудника, которую рвёт " +
        "пребывание в базе приписки (конец смены в базе — граница; отдых вне базы цепочку не " +
        "рвёт — по правилам свода); ночная смена — интервал смены касается окна времени " +
        "суток [от, до); поясов в мире нет — окно читается по времени данных; единица — " +
        "цепочка, где есть и ночная, и дневная смена"

    const val READING_UNMET_REQUEST = "заявка — строка листа заявок (сотрудник, дата); " +
        "неудовлетворённая — заявленных суток касается хотя бы одно назначение сотрудника " +
        "(две смены в заявленные сутки — одна единица); выполнимость заявки судит анкета " +
        "производителя, форма считает факт"

    /** Форма для блока logic либо null — «форма не порождается» (причину даёт компилятор). */
    fun formOf(l: RuleLogic): String? {
        val d = l.demand
        // Месяц может приехать и атомом in_window {kind: month} (живой полный
        // пакет 2026-08-24): то же прочтение, что универсум employee×month, —
        // клетка формы и есть месяц. Окно другого рода в атоме — не эта форма.
        val monthAtomOk = l.conditions.isEmpty() ||
            (l.conditions.size == 1 && l.conditions[0].predicate == "in_window" &&
             (l.conditions[0].args["window"] as? LogicWindow)?.kind == "month")
        if (d.kind == "count_within" && d.relation == "min" && l.subject == "day_off" &&
            monthAtomOk &&
            (d.universe == "employee×month" || (d.universe == null && l.window?.kind == "month"))) {
            return FORM_DAYS_OFF_PER_MONTH_MIN
        }
        // Ночных смен подряд не более N: starts_in задаёт окно
        // ночи, consecutive по СМЕНАМ с тем же числом, что у требования.
        if (d.kind == "count_within" && d.relation == "max" && l.subject == "duty" &&
            d.universe == null && l.window == null && l.conditions.size == 2) {
            val starts = l.conditions.firstOrNull { it.predicate == "starts_in" }
            val consec = l.conditions.firstOrNull { it.predicate == "consecutive" }
            if (starts != null && consec != null && consec.args["unit"] == "duty" &&
                (consec.args["param"] as? LogicRef)?.name == d.param) {
                return FORM_CONSECUTIVE_NIGHT_DUTIES_MAX
            }
        }
        // Отдых в скользящем окне: «в каждом окне N суток —
        // непрерывный отдых не менее W минут»: exists_universe над rest по
        // employee×rolling_window, ровно одно условие longer_than(W), окно
        // rolling с явной единицей «сутки».
        if (d.kind == "exists_universe" && l.subject == "rest" && d.subject == "rest" &&
            d.universe == "employee×rolling_window" &&
            l.window?.kind == "rolling" && l.window.unit == "day" &&
            l.conditions.size == 1 && l.conditions[0].predicate == "longer_than") {
            return FORM_REST_IN_ROLLING_WINDOW
        }
        // Отдых после продлённой смены: «после смены длиннее X
        // отдых не менее Y» — ровно два условия: follows(duty) и
        // longer_than(X); Y — граница требования.
        if (d.kind == "duration_within" && d.relation == "min" && l.subject == "rest" &&
            d.universe == null && l.window == null && l.conditions.size == 2) {
            val follows = l.conditions.firstOrNull { it.predicate == "follows" }
            val longer = l.conditions.firstOrNull { it.predicate == "longer_than" }
            if (follows != null && longer != null && follows.args["subject"] == "duty") {
                return FORM_REST_AFTER_LONG_DUTY_MIN
            }
        }
        // Рабочих дней подряд не более N: ровно одно условие
        // consecutive по СУТКАМ, и его число — то же, что граница требования
        // (одно «не более N подряд», а не два разных числа). Серии по
        // сменам (unit=duty) — вне этой формы: им нужна нарезка смен.
        if (d.kind == "count_within" && d.relation == "max" && l.subject == "day" &&
            d.universe == null && l.window == null &&
            l.conditions.size == 1 && l.conditions[0].predicate == "consecutive" &&
            l.conditions[0].args["unit"] == "day" &&
            (l.conditions[0].args["param"] as? LogicRef)?.name == d.param) {
            return FORM_CONSECUTIVE_DAYS_MAX
        }
        // Выходных в скользящем окне не менее K: count_within(min) над
        // day_off по универсуму employee×rolling_window, окно rolling в
        // сутках, условий нет.
        if (d.kind == "count_within" && d.relation == "min" && l.subject == "day_off" &&
            d.universe == "employee×rolling_window" &&
            l.window?.kind == "rolling" && l.window.unit == "day" && l.conditions.isEmpty()) {
            return FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN
        }
        // Лимит серии рабочих суток подряд и отдых после неё:
        // count_within(min) над day_off БЕЗ универсума и окна, ровно два
        // условия — consecutive по СУТКАМ (длина серии) и follows(day)
        // («выходные следуют за серией рабочих суток»). Форма «рабочих
        // дней подряд не более N» сюда не попадает: у неё предмет day и
        // ровно одно условие.
        if (d.kind == "count_within" && d.relation == "min" && l.subject == "day_off" &&
            d.universe == null && l.window == null && l.conditions.size == 2) {
            val consec = l.conditions.firstOrNull { it.predicate == "consecutive" }
            val follows = l.conditions.firstOrNull { it.predicate == "follows" }
            if (consec != null && follows != null && consec.args["unit"] == "day" &&
                follows.args["subject"] == "day") {
                return FORM_DUTY_SERIES_LIMIT
            }
        }
        // Ночных смен в скользящем окне не более K: count_within(max) над
        // duty, ровно одно условие starts_in (окно ночи), окно rolling в
        // сутках (универсум employee×rolling_window либо не указан).
        if (d.kind == "count_within" && d.relation == "max" && l.subject == "duty" &&
            (d.universe == null || d.universe == "employee×rolling_window") &&
            l.window?.kind == "rolling" && l.window.unit == "day" &&
            l.conditions.size == 1 && l.conditions[0].predicate == "starts_in") {
            return FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX
        }
        return null
    }

    /** Область формы: null (все) либо код базы; слова областей ручек здесь смысла не имеют. */
    fun baseScopeOf(l: RuleLogic): String? {
        val s = l.scope?.trim() ?: return null
        return if (Regex("^[A-Z0-9]{2,4}$").matches(s.uppercase())) s.uppercase() else null
    }

    /** Полные календарные месяцы внутри [lo, hi]. */
    fun fullMonths(lo: LocalDate, hi: LocalDate): List<YearMonth> {
        val out = ArrayList<YearMonth>()
        var m = YearMonth.from(lo)
        val last = YearMonth.from(hi)
        while (m <= last) {
            if (!m.atDay(1).isBefore(lo) && !m.atEndOfMonth().isAfter(hi)) out.add(m)
            m = m.plusMonths(1)
        }
        return out
    }

    /** Сутки, которых касается рейс: от даты вылета до даты прилёта включительно. */
    fun touchedDays(f: Flight): List<LocalDate> {
        val lo = f.getDepartureUTCDate()
        val hi = f.arrivalUTCDateTime!!.toLocalDate()
        return lo.datesUntil(hi.plusDays(1)).toList()
    }

    private fun inScope(e: Employee, plan: LogicPlan): Boolean =
        plan.baseScope == null || (e.homeAirport?.code?.uppercase() ?: "") == plan.baseScope

    /**
     * Единый горизонт учёта набора (K-14): объявленное `window_horizon`
     * задачи имеет приоритет; без него — календарные сутки от первого до
     * последнего ВЫЛЕТА (тот же fallback у провайдера резерва, fit и
     * checkFacts — d1ee521). Null — ни окна, ни рейсов.
     */
    fun horizonOf(problem: FlightCrewSchedule): Horizon? = problem.windowHorizon?.let(::declaredHorizon)
        ?: if (problem.flights.isEmpty()) null else Horizon(
            problem.flights.minOf { it.getDepartureUTCDate() },
            problem.flights.maxOf { it.getDepartureUTCDate() })

    /** Объявленное окно [start, end) → затронутые даты и точные границы. */
    fun declaredHorizon(window: AccountingWindow): Horizon =
        Horizon(window.startAt().toLocalDate(), window.endAt().minusNanos(1).toLocalDate(),
            window.startAt(), window.endAt())

    /**
     * Клетки универсума на наборе и их имя — для fit. Ноль клеток значит
     * «считать нечего»: при required — отказ прогона, при post-check —
     * НЕ ИСПОЛНЯЕТСЯ с заявлением ПУСТО. Вторым числом — неполные месяцы,
     * чтобы читатель видел, что именно осталось вне оценки.
     */
    fun cells(problem: FlightCrewSchedule, plan: LogicPlan): Triple<Int, String, List<String>> {
        if (plan.form == FORM_CONSECUTIVE_NIGHT_DUTIES_MAX) {
            val people = problem.employees.count { inScope(it, plan) }
            return Triple(people, "сотрудников области (серии смен считаются по каждому)", emptyList())
        }
        if (plan.form == FORM_REST_IN_ROLLING_WINDOW) {
            // K-15: непрерывное покрытие — единица «сотрудник» (критические
            // клетки зависят от его ростера, а не от сетки суток); горизонт
            // короче окна — не пустой универсум, а неизвестные ранние окна.
            if (horizonOf(problem) == null) return Triple(0, "сотрудников непрерывного окна", emptyList())
            return Triple(problem.employees.count { inScope(it, plan) },
                "сотрудников; непрерывные окна по критическим точкам и открытым промежуткам; " +
                    "неизвестные окна не являются доказательством допустимости", emptyList())
        }
        if (plan.form == FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN ||
            plan.form == FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX) {
            val h = horizonOf(problem) ?: return Triple(0, "клеток (сотрудник × старт окна)", emptyList())
            val lo = h.lo
            val hi = h.hi
            val daysTotal = java.time.temporal.ChronoUnit.DAYS.between(lo, hi) + 1
            val starts = maxOf(0L, daysTotal - (plan.condBound ?: 0) + 1).toInt()
            val people = problem.employees.count { inScope(it, plan) }
            return Triple(people * starts,
                "клеток (сотрудник × старт окна: " + people + " × " + starts + ")", emptyList())
        }
        if (plan.form == FORM_REST_AFTER_LONG_DUTY_MIN) {
            val people = problem.employees.count { inScope(it, plan) }
            return Triple(people, "сотрудников области (границы смен считаются по каждому)", emptyList())
        }
        if (plan.form == FORM_CONSECUTIVE_DAYS_MAX) {
            // Серии судятся по каждому сотруднику области; горизонт не режет
            // форму на клетки — мера честно называет, кого рассматривают.
            val people = problem.employees.count { inScope(it, plan) }
            return Triple(people, "сотрудников области (серии считаются по каждому)", emptyList())
        }
        if (plan.form == FORM_DUTY_SERIES_LIMIT) {
            // Та же единица, что у «дней подряд»: серии и разрывы между
            // ними считаются по каждому сотруднику области.
            val people = problem.employees.count { inScope(it, plan) }
            return Triple(people,
                "сотрудников области (серии и разрывы между ними считаются по каждому)",
                emptyList())
        }
        val h = horizonOf(problem) ?: return Triple(0, "клеток (сотрудник × полный месяц)", emptyList())
        val lo = h.lo
        val hi = h.hi
        val full = fullMonths(lo, hi)
        val partial = ArrayList<String>()
        var m = YearMonth.from(lo)
        while (m <= YearMonth.from(hi)) {
            if (m !in full) partial.add(m.toString())
            m = m.plusMonths(1)
        }
        val people = problem.employees.count { inScope(it, plan) }
        // Ревизия 1.17: месяц среза истории — клетка у сотрудника с историей,
        // когда месяц начат до горизонта и горизонт доходит до его конца.
        val hist = problem.employees.count { e ->
            inScope(e, plan) && (e.historyMonth()?.let { m ->
                m !in full && m.atDay(1).isBefore(lo) && !m.atEndOfMonth().isAfter(hi)
            } ?: false)
        }
        return Triple(people * full.size + hist,
            "клеток (сотрудник × полный месяц: " + people + " × " + full.size +
                (if (hist > 0) "; месяц среза истории у " + hist else "") + ")", partial)
    }

    fun build(cf: ConstraintFactory, plan: LogicPlan,
              dutySlicer: ((List<FlightAssignment>) -> List<List<FlightAssignment>>)? = null): Constraint =
        when (plan.form) {
            FORM_DAYS_OFF_PER_MONTH_MIN -> daysOffPerMonthMin(cf, plan)
            FORM_CONSECUTIVE_DAYS_MAX -> consecutiveDaysMax(cf, plan)
            FORM_REST_AFTER_LONG_DUTY_MIN -> restAfterLongDuty(cf, plan,
                dutySlicer ?: throw IllegalStateException(
                    "форма " + plan.form + " требует нарезку смен провайдера"))
            FORM_REST_IN_ROLLING_WINDOW -> restInRollingWindow(cf, plan,
                dutySlicer ?: throw IllegalStateException(
                    "форма " + plan.form + " требует нарезку смен провайдера"))
            FORM_CONSECUTIVE_NIGHT_DUTIES_MAX -> consecutiveNightDuties(cf, plan,
                dutySlicer ?: throw IllegalStateException(
                    "форма " + plan.form + " требует нарезку смен провайдера"))
            FORM_SIT_OVERAGE_STEPS -> sitOverageSteps(cf, plan,
                dutySlicer ?: throw IllegalStateException(
                    "форма " + plan.form + " требует нарезку смен провайдера"))
            FORM_DUTY_SPLIT_SIT -> dutySplitSit(cf, plan,
                dutySlicer ?: throw IllegalStateException(
                    "форма " + plan.form + " требует нарезку смен провайдера"))
            FORM_CHAIN_NIGHT_DAY_MIX -> chainNightDayMix(cf, plan,
                dutySlicer ?: throw IllegalStateException(
                    "форма " + plan.form + " требует нарезку смен провайдера"))
            FORM_UNMET_REQUEST -> unmetRequest(cf, plan)
            FORM_DAYS_OFF_IN_ROLLING_WINDOW_MIN -> daysOffInRollingWindow(cf, plan)
            FORM_DUTY_SERIES_LIMIT -> dutySeriesLimit(cf, plan)
            FORM_NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX -> nightDutiesInRollingWindow(cf, plan,
                dutySlicer ?: throw IllegalStateException(
                    "форма " + plan.form + " требует нарезку смен провайдера"))
            else -> throw IllegalStateException("форма " + plan.form + " без построителя")
        }

    // ------------------------------------------ счётчики скользящих окон

    /**
     * Горизонт как поток фактов (K-11/K-14): объявленное окно
     * (AccountingWindow задачи) — приоритет; без него — fallback по
     * ВЫЛЕТАМ, тот же, что у [horizonOf]. Internal — потребители:
     * резерв провайдера, формы окон и месяцев.
     */
    internal fun horizonStream(cf: ConstraintFactory): UniConstraintStream<Horizon> {
        val depDate = Function<Flight, LocalDate> { it.getDepartureUTCDate() }
        val fallback = cf.forEach(Flight::class.java)
            .ifNotExists(AccountingWindow::class.java)
            .groupBy(ConstraintCollectors.min(depDate), ConstraintCollectors.max(depDate))
            .map(BiFunction<LocalDate, LocalDate, Horizon> { lo, hi -> Horizon(lo, hi) })
        return cf.forEach(AccountingWindow::class.java)
            .map(Function<AccountingWindow, Horizon> { declaredHorizon(it) }).concat(fallback)
    }

    /** Сутки календаря истории: 1 — выходной, 0 — занятые, null — неизвестно. */
    private fun calendarDayOff(cal: Map<LocalDate, org.acme.flighcrewscheduling.domain.HistoryDay>,
                               d: LocalDate): Int? {
        val h = cal[d] ?: return null
        return when (h.kind) {
            "day_off", "leave", "sick" -> 1
            "work", "reserve" -> 0
            else -> null
        }
    }

    /** Ночных смен в сутках календаря истории: null — неизвестно. */
    private fun calendarNights(cal: Map<LocalDate, org.acme.flighcrewscheduling.domain.HistoryDay>,
                               d: LocalDate): Int? {
        val h = cal[d] ?: return null
        return when {
            h.kind == "unknown" -> null
            h.kind == "work" && h.night == null -> null
            h.night == true -> 1
            else -> 0
        }
    }

    /**
     * Нехватка выходных по окнам сотрудника: Σ (K − выходных) по окнам,
     * где выходных меньше K. Старты окон — от `h.lo − (N−1)` (часть до
     * горизонта — по календарю истории; сутки без записи делают окно
     * несудимым) до `h.hi − (N−1)`.
     */
    internal fun daysOffWindowShortfall(e: Employee, list: List<FlightAssignment>, h: Horizon,
                                        windowDays: Long, minOff: Long): Long {
        if (list.isEmpty() || windowDays <= 0) return 0
        val busy = HashSet<LocalDate>()
        for (fa in list) busy.addAll(touchedDays(fa.flight!!))
        val cal = e.historyCalendarMap()
        var total = 0L
        var d = h.lo.minusDays(windowDays - 1)
        while (!d.plusDays(windowDays - 1).isAfter(h.hi)) {
            var off = 0L
            var known = true
            var x = d
            val end = d.plusDays(windowDays)
            while (x.isBefore(end)) {
                if (x.isBefore(h.lo)) {
                    val v = calendarDayOff(cal, x)
                    if (v == null) { known = false; break }
                    off += v
                } else if (x !in busy) {
                    off++
                }
                x = x.plusDays(1)
            }
            if (known && off < minOff) total += minOff - off
            d = d.plusDays(1)
        }
        return total
    }

    /**
     * Превышение ночных смен по окнам сотрудника: Σ (ночных − K) по окнам,
     * где ночных больше K; смена относится к суткам своего начала.
     */
    internal fun nightWindowOverage(e: Employee, list: List<FlightAssignment>, h: Horizon,
                                    windowDays: Long, maxNights: Long, from: Int, to: Int,
                                    slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Long {
        if (list.isEmpty() || windowDays <= 0) return 0
        val nights = HashMap<LocalDate, Int>()
        for (duty in slicer(list)) {
            val dep = duty.minOf { it.flight!!.departureUTCDateTime!! }
            if (inNightWindow(dep.hour * 60 + dep.minute, from, to)) {
                nights.merge(dep.toLocalDate(), 1, Int::plus)
            }
        }
        val cal = e.historyCalendarMap()
        if (nights.isEmpty() && cal.isEmpty()) return 0
        var total = 0L
        var d = h.lo.minusDays(windowDays - 1)
        while (!d.plusDays(windowDays - 1).isAfter(h.hi)) {
            var n = 0L
            var known = true
            var x = d
            val end = d.plusDays(windowDays)
            while (x.isBefore(end)) {
                if (x.isBefore(h.lo)) {
                    val v = calendarNights(cal, x)
                    if (v == null) { known = false; break }
                    n += v
                } else {
                    n += (nights[x] ?: 0)
                }
                x = x.plusDays(1)
            }
            if (known && n > maxNights) total += n - maxNights
            d = d.plusDays(1)
        }
        return total
    }

    /** ВЫХОДНЫХ В СКОЛЬЗЯЩЕМ ОКНЕ НЕ МЕНЕЕ K (окно N суток — condBound). */
    private fun daysOffInRollingWindow(cf: ConstraintFactory, plan: LogicPlan): Constraint {
        val weight = HardSoftScore.ofHard(plan.weight.toLong())
        val days = (plan.condBound ?: 0).toLong()
        val minOff = plan.bound.toLong()
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .groupBy(Function<FlightAssignment, Employee> { it.employee!! },
                     ConstraintCollectors.toList())
            .join(horizonStream(cf))
            .filter { e, list, h -> daysOffWindowShortfall(e, list, h, days, minOff) > 0L }
            .penalize(weight) { e, list, h -> daysOffWindowShortfall(e, list, h, days, minOff) }
            .asConstraint(plan.constraintId)
    }

    /**
     * Штраф серии рабочих суток (форма [FORM_DUTY_SERIES_LIMIT]): за
     * каждые сутки серии сверх предела ПЛЮС за каждые недостающие
     * свободные сутки после серии, ДОСТИГШЕЙ предела.
     *
     * Хвост ПОСЛЕДНЕЙ серии сотрудника не судится: следующей серии в
     * наборе нет, «свободных суток после» неизвестно, а неизвестное не
     * штрафуется (то же приближение объявлено прочтением формы).
     */
    internal fun seriesPenalty(
        e: Employee,
        seq: ai.timefold.solver.core.api.score.stream.common.Sequence<LocalDate, Int>,
        maxSeries: Long, requiredDaysOff: Long,
    ): Long {
        if (maxSeries <= 0L) return 0L
        // Ревизия 1.17: серия, начатая в день среза истории, продолжает
        // серию рабочих дней истории (без истории — 0).
        val length = seq.count + e.carryConsecutiveDutyDays(seq.firstItem)
        val over = maxOf(0L, length - maxSeries)
        // Хвоста нет: следующая серия неизвестна (последняя серия набора
        // либо разрыв, который набор не показывает) — судится только
        // превышение длины.
        val nextBreak = if (seq.isLast) null else seq.nextBreak
        val freeDays = ((nextBreak ?: return over).length - 1).toLong()
        val shortfall = if (length >= maxSeries) maxOf(0L, requiredDaysOff - freeDays) else 0L
        return over + shortfall
    }

    /**
     * ЛИМИТ СЕРИИ РАБОЧИХ СУТОК ПОДРЯД И ОБЯЗАТЕЛЬНЫЙ ОТДЫХ ПОСЛЕ НЕЁ
     * (bound — свободных суток после серии не менее, condBound — длина
     * серии не более). Сутки дедуплицируются ДО серий, как у формы «дней
     * подряд»: две смены в одни сутки — один рабочий день.
     */
    private fun dutySeriesLimit(cf: ConstraintFactory, plan: LogicPlan): Constraint {
        val weight = HardSoftScore.ofHard(plan.weight.toLong())
        val required = plan.bound.toLong()
        val maxSeries = (plan.condBound ?: 0).toLong()
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .map(Function<FlightAssignment, FlightAssignment> { it },
                 Function<FlightAssignment, Iterable<LocalDate>> { touchedDays(it.flight!!) })
            .flattenLast(Function<Iterable<LocalDate>, Iterable<LocalDate>> { it })
            // Дедупликация: (сотрудник, сутки) — один рабочий день.
            .groupBy(BiFunction<FlightAssignment, LocalDate, Employee> { fa, _ -> fa.employee!! },
                     BiFunction<FlightAssignment, LocalDate, LocalDate> { _, d -> d })
            .groupBy(BiFunction<Employee, LocalDate, Employee> { e, _ -> e },
                     ConstraintCollectors.toConsecutiveSequences(
                         BiFunction<Employee, LocalDate, LocalDate> { _, d -> d },
                         java.util.function.ToIntFunction<LocalDate> { d -> Math.toIntExact(d.toEpochDay()) }))
            .flattenLast(Function<ai.timefold.solver.core.api.score.stream.common.SequenceChain<LocalDate, Int>,
                                  Iterable<ai.timefold.solver.core.api.score.stream.common.Sequence<LocalDate, Int>>> {
                chain -> chain.consecutiveSequences
            })
            .filter { e, seq -> seriesPenalty(e, seq, maxSeries, required) > 0L }
            .penalize(weight) { e, seq -> seriesPenalty(e, seq, maxSeries, required) }
            .asConstraint(plan.constraintId)
    }

    /** НОЧНЫХ СМЕН В СКОЛЬЗЯЩЕМ ОКНЕ НЕ БОЛЕЕ K (окно N суток — condBound, окно ночи — nightFrom/To). */
    private fun nightDutiesInRollingWindow(cf: ConstraintFactory, plan: LogicPlan,
                                           slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Constraint {
        val weight = HardSoftScore.ofHard(plan.weight.toLong())
        val days = (plan.condBound ?: 0).toLong()
        val maxNights = plan.bound.toLong()
        val from = plan.nightFrom ?: 0
        val to = plan.nightTo ?: 0
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .groupBy(Function<FlightAssignment, Employee> { it.employee!! },
                     ConstraintCollectors.toList())
            .join(horizonStream(cf))
            .filter { e, list, h -> nightWindowOverage(e, list, h, days, maxNights, from, to, slicer) > 0L }
            .penalize(weight) { e, list, h -> nightWindowOverage(e, list, h, days, maxNights, from, to, slicer) }
            .asConstraint(plan.constraintId)
    }

    // ------------------------------------------------ формы волны 1 (П-2)

    /** Зазор между соседними назначениями, минуты. */
    private fun sitMinutes(a: FlightAssignment, b: FlightAssignment): Long =
        java.time.temporal.ChronoUnit.MINUTES.between(
            a.flight!!.arrivalUTCDateTime, b.flight!!.departureUTCDateTime)

    /** Ф-1: ступени сверх порога за каждую длинную стыковку внутри смены. */
    private fun sitOverageSteps(cf: ConstraintFactory, plan: LogicPlan,
                                slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Constraint {
        val threshold = plan.bound
        val step = plan.condBound ?: throw IllegalStateException(
            "форма " + plan.form + " требует шаг ступени (condBound)")
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.toList<FlightAssignment>())
            .filter { _, list -> sitOverageUnits(list, threshold, step, slicer) > 0L }
            .penalize(plan.score()) { _, list -> sitOverageUnits(list, threshold, step, slicer) }
            .asConstraint(plan.constraintId)
    }

    internal fun sitOverageUnits(list: List<FlightAssignment>, threshold: Int, step: Int,
                                 slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Long {
        var units = 0L
        for (duty in slicer(list)) {
            for (i in 1 until duty.size) {
                val sit = sitMinutes(duty[i - 1], duty[i])
                if (sit > threshold) units += (sit - threshold) / step
            }
        }
        return units
    }

    /** Ф-2: одна единица за СМЕНУ, содержащую стыковку длиннее порога. */
    private fun dutySplitSit(cf: ConstraintFactory, plan: LogicPlan,
                             slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Constraint {
        val threshold = plan.bound
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.toList<FlightAssignment>())
            .filter { _, list -> dutySplitUnits(list, threshold, slicer) > 0L }
            .penalize(plan.score()) { _, list -> dutySplitUnits(list, threshold, slicer) }
            .asConstraint(plan.constraintId)
    }

    internal fun dutySplitUnits(list: List<FlightAssignment>, threshold: Int,
                                slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Long =
        slicer(list).count { duty ->
            (1 until duty.size).any { i -> sitMinutes(duty[i - 1], duty[i]) > threshold }
        }.toLong()

    /** Ф-3: цепочка (рвётся пребыванием в базе приписки) со смешением
     *  ночных и дневных смен — одна единица за цепочку. */
    private fun chainNightDayMix(cf: ConstraintFactory, plan: LogicPlan,
                                 slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Constraint {
        val from = plan.nightFrom ?: throw IllegalStateException(
            "форма " + plan.form + " требует окно ночи (nightFrom/nightTo)")
        val to = plan.nightTo ?: throw IllegalStateException(
            "форма " + plan.form + " требует окно ночи (nightFrom/nightTo)")
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .groupBy(FlightAssignment::employee,
                ConstraintCollectors.toList<FlightAssignment>())
            .filter { _, list -> chainMixUnits(list, from, to, slicer) > 0L }
            .penalize(plan.score()) { _, list -> chainMixUnits(list, from, to, slicer) }
            .asConstraint(plan.constraintId)
    }

    internal fun chainMixUnits(list: List<FlightAssignment>, from: Int, to: Int,
                               slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Long {
        val duties = slicer(list)
        if (duties.isEmpty()) return 0L
        val home = list.first().employee?.homeAirport?.code
        var units = 0L
        var night = false
        var day = false
        fun closeChain() {
            if (night && day) units++
            night = false; day = false
        }
        for (duty in duties) {
            if (dutyTouchesNight(duty, from, to)) night = true else day = true
            val endAirport = duty.last().flight!!.arrivalAirport?.code
            if (home != null && endAirport == home) closeChain()
        }
        closeChain()
        return units
    }

    /** Смена касается ночного окна: хоть одна минута интервала смены в окне. */
    internal fun dutyTouchesNight(duty: List<FlightAssignment>, from: Int, to: Int): Boolean {
        val start = duty.first().flight!!.departureUTCDateTime!!
        val end = duty.last().flight!!.arrivalUTCDateTime!!
        var t = start
        // шаг по границам суток: окно повторяется ежесуточно
        while (!t.isAfter(end)) {
            val minute = t.hour * 60 + t.minute
            if (inNightWindow(minute, from, to)) return true
            t = t.plusMinutes(1)
            if (java.time.temporal.ChronoUnit.MINUTES.between(start, t) > 7 * 24 * 60) break
        }
        return false
    }

    /** Ф-5: заявленных суток касается назначение — одна единица за (сотрудник, дата заявки). */
    private fun unmetRequest(cf: ConstraintFactory, plan: LogicPlan): Constraint =
        cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .map(Function<FlightAssignment, FlightAssignment> { it },
                 Function<FlightAssignment, Iterable<LocalDate>> { touchedDays(it.flight!!) })
            .flattenLast(Function<Iterable<LocalDate>, Iterable<LocalDate>> { it })
            .filter { fa, d -> fa.employee!!.preferredDaysOff.contains(d) }
            // Дедупликация: (сотрудник, заявленные сутки) — одна единица.
            .groupBy(BiFunction<FlightAssignment, LocalDate, Employee> { fa, _ -> fa.employee!! },
                     BiFunction<FlightAssignment, LocalDate, LocalDate> { _, d -> d })
            .penalize(plan.score())
            .asConstraint(plan.constraintId)

    /** Минута суток UTC в окне [from, to); окно через полночь (from > to) законно. */
    internal fun inNightWindow(minuteOfDay: Int, from: Int, to: Int): Boolean =
        if (from <= to) minuteOfDay in from until to
        else minuteOfDay >= from || minuteOfDay < to

    /**
     * НОЧНЫХ СМЕН ПОДРЯД НЕ БОЛЕЕ N — на той же нарезке смен, что размах и
     * посадки. Серии — по последовательности смен сотрудника; штраф за
     * каждую ночную смену сверх границы в каждой серии.
     */
    private fun consecutiveNightDuties(cf: ConstraintFactory, plan: LogicPlan,
                                       slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Constraint {
        val weight = HardSoftScore.ofHard(plan.weight.toLong())
        val bound = plan.bound
        val from = plan.nightFrom ?: 0
        val to = plan.nightTo ?: 0
        fun overage(e: Employee, list: List<FlightAssignment>): Long {
            val ds = slicer(list)
            var total = 0L
            // Ревизия 1.17 (решение владельца 05.09.2026): серия ночных из
            // истории сотрудника продолжается первой сменой горизонта, если
            // та ночная; без истории — 0 (прежнее прочтение).
            var run = 0
            if (ds.isNotEmpty()) {
                val dep0 = ds.first().minOf { it.flight!!.departureUTCDateTime!! }
                if (inNightWindow(dep0.hour * 60 + dep0.minute, from, to)) {
                    run = e.carryConsecutiveNightDuties().toInt()
                }
            }
            for (d in ds) {
                val dep = d.minOf { it.flight!!.departureUTCDateTime!! }
                val night = inNightWindow(dep.hour * 60 + dep.minute, from, to)
                if (night) {
                    run++
                } else {
                    if (run > bound) total += (run - bound).toLong()
                    run = 0
                }
            }
            if (run > bound) total += (run - bound).toLong()
            return total
        }
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .groupBy(Function<FlightAssignment, Employee> { it.employee!! },
                     ConstraintCollectors.toList())
            .filter { e, list -> overage(e, list) > 0L }
            .penalize(weight) { e, list -> overage(e, list) }
            .asConstraint(plan.constraintId)
    }

    /**
     * СУТОЧНАЯ ФОРМА — legacy-диагностика (K-15 единой версии, §3.10.3
     * плана): старты окон по календарным суткам; её показания — «окон
     * нарушено», а не доказательство правила; ограничение прогона строит
     * [rollingRestAudit] (непрерывное покрытие). Разные единицы и домены —
     * числовое неравенство двух форм не расхождение результатов.
     *
     * Нарушенные окна сотрудника: замер 2026-08-24 — ×0.984 к базе на 20 000 ходов (шум).
     *
     * Ревизия 1.17 (решение владельца 05.09.2026): `lastDutyEnd` — конец
     * последней смены истории. Он открывает ИЗВЕСТНЫЙ отдых перед первой
     * сменой горизонта, и старты окон начинаются с первых полных суток
     * после него (окна с более ранним стартом содержат неизвестные сутки —
     * не судятся; неизвестное не превращается ни в отдых, ни в работу).
     * Без истории — прежнее прочтение: старты окон только внутри горизонта.
     *
     * Допущение нулевой истории (`assumedFreeFrom` — срез раздела при
     * continue_zero, ревизия 1.17.2): сотрудник заведомо свободен до среза,
     * старты окон — с (срез − (окно − 1)), как у сертификатора; иначе окно,
     * начатое до первого рейса сотрудника (например 04–10.09 при первом
     * рейсе 05.09), решатель не видел, а сертификат его краснил (первое
     * нажатие простого Н0 на семи сутках 05.09.2026: H19, 2080 < 2160 мин).
     */
    internal fun violatedWindows(list: List<FlightAssignment>, windowDays: Long, restMin: Long,
                                 lastDutyEnd: java.time.LocalDateTime? = null,
                                 assumedFreeFrom: LocalDate? = null): Long {
        if (list.isEmpty()) return 0
        val sorted = list.sortedBy { it.flight!!.departureUTCDateTime }
        val firstDep = sorted.first().flight!!.departureUTCDateTime!!
        var lo = firstDep.toLocalDate()
        val hi = sorted.maxOf { it.flight!!.arrivalUTCDateTime!! }.toLocalDate()
        data class Iv(val from: java.time.LocalDateTime, val to: java.time.LocalDateTime)
        val rests = ArrayList<Iv>()
        var cursor = lo.atStartOfDay()
        if (lastDutyEnd != null && lastDutyEnd.isBefore(firstDep)) {
            cursor = lastDutyEnd
            val endDay = lastDutyEnd.toLocalDate()
            val firstFull = if (lastDutyEnd == endDay.atStartOfDay()) endDay else endDay.plusDays(1)
            if (firstFull.isBefore(lo)) lo = firstFull
        } else if (lastDutyEnd == null && assumedFreeFrom != null) {
            val start = assumedFreeFrom.minusDays(windowDays - 1)
            if (start.isBefore(lo)) {
                lo = start
                cursor = lo.atStartOfDay()
            }
        }
        for (fa in sorted) {
            val dep = fa.flight!!.departureUTCDateTime!!
            val arr = fa.flight!!.arrivalUTCDateTime!!
            if (dep.isAfter(cursor)) rests.add(Iv(cursor, dep))
            if (arr.isAfter(cursor)) cursor = arr
        }
        rests.add(Iv(cursor, hi.plusDays(1).atStartOfDay()))
        var violated = 0L
        var d = lo
        while (!d.plusDays(windowDays - 1).isAfter(hi)) {
            val wFrom = d.atStartOfDay()
            val wTo = d.plusDays(windowDays).atStartOfDay()
            val ok = rests.any { r ->
                val from = maxOf(r.from, wFrom)
                val to = minOf(r.to, wTo)
                java.time.temporal.ChronoUnit.MINUTES.between(from, to) >= restMin
            }
            if (!ok) violated++
            d = d.plusDays(1)
        }
        return violated
    }

    private fun mergeRestIntervals(intervals: List<RestInterval>): List<RestInterval> {
        val out = ArrayList<RestInterval>()
        for (iv in intervals.filter { it.to >= it.from }.sortedBy { it.from }) {
            val last = out.lastOrNull()
            if (last != null && iv.from <= last.to) {
                out[out.lastIndex] = RestInterval(last.from, maxOf(last.to, iv.to))
            } else out.add(iv)
        }
        return out
    }

    /**
     * Непрерывная проверка окон отдыха (K-15 единой версии, редакция
     * GPT-013, принята COSMOS и CDPM): точные окна, представленные
     * точками разреза и открытыми промежутками между ними. Отдых [a,b]
     * доказывает окно W минут с концом x тогда и только тогда, когда x ∈
     * [a+R, b+W−R]. Нарушение засчитывается ТОЛЬКО там, где всё окно
     * лежит в известной занятости; неизвестная клетка даёт ноль, но этот
     * ноль — не сертификат допустимости.
     *
     * Известное: горизонт [start, end); конец последней смены истории
     * расширяет известное к началу только при as_of = началу горизонта
     * (снимок конца без совпадающего среза — не доказательство содержимого
     * промежутка); явные выходные календаря до горизонта — известный
     * отдых; допущение нулевой истории ([assumedFreeFrom] — срез раздела
     * при continue_zero, ревизия 1.17.2) открывает известный отдых от
     * (срез − W) до горизонта — то же явное основание и та же область,
     * что у сертификатора (§3.10.3 плана).
     */
    internal fun rollingRestAudit(dutyOccupancy: List<RestInterval>, windowMin: Long,
                                  restMin: Long, horizon: Horizon,
                                  lastDutyEnd: LocalDateTime? = null,
                                  historyAsOf: LocalDateTime? = null,
                                  knownRestDays: List<LocalDate> = emptyList(),
                                  assumedFreeFrom: LocalDate? = null): RollingRestAudit {
        require(windowMin > 0 && restMin >= 0)
        val start = horizon.startInclusive
        val end = horizon.endExclusive
        require(end > start)
        val knownRaw = arrayListOf(RestInterval(start, end))
        if (historyAsOf == start && lastDutyEnd != null && lastDutyEnd <= start) {
            knownRaw.add(RestInterval(lastDutyEnd, start))
        }
        for (day in knownRestDays) {
            val from = day.atStartOfDay()
            val to = day.plusDays(1).atStartOfDay()
            if (to <= start) knownRaw.add(RestInterval(from, to))
        }
        if (assumedFreeFrom != null) {
            val cut = assumedFreeFrom.atStartOfDay()
            if (cut <= start) knownRaw.add(RestInterval(cut.minusMinutes(windowMin), start))
        }
        val known = mergeRestIntervals(knownRaw)
        val busy = mergeRestIntervals(dutyOccupancy)
        val rests = ArrayList<RestInterval>()
        for (k in known) {
            var cursor = k.from
            for (d in busy) {
                if (d.to <= cursor || d.from >= k.to) continue
                if (d.from > cursor) rests.add(RestInterval(cursor, minOf(d.from, k.to)))
                cursor = maxOf(cursor, minOf(d.to, k.to))
            }
            if (cursor < k.to) rests.add(RestInterval(cursor, k.to))
        }
        fun clipped(intervals: List<RestInterval>): List<RestInterval> = mergeRestIntervals(
            intervals.map { RestInterval(maxOf(start, it.from), minOf(end, it.to)) })
        val pass = if (restMin == 0L) listOf(RestInterval(start, end))
            else if (restMin > windowMin) emptyList()
            else clipped(rests.filter { it.to >= it.from.plusMinutes(restMin) }
                .map { RestInterval(it.from.plusMinutes(restMin),
                    it.to.plusMinutes(windowMin - restMin)) })
        val fullyKnown = clipped(known.filter { it.to >= it.from.plusMinutes(windowMin) }
            .map { RestInterval(it.from.plusMinutes(windowMin), it.to) })
        val cuts = java.util.TreeSet<LocalDateTime>()
        cuts.add(start); cuts.add(end)
        for (iv in pass + fullyKnown) { cuts.add(iv.from); cuts.add(iv.to) }
        var expected = 0L
        var passed = 0L
        var violated = 0L
        var unknown = 0L
        fun judge(from: LocalDateTime, to: LocalDateTime) {
            expected++
            if (pass.any { it.from <= from && to <= it.to }) passed++
            else if (fullyKnown.any { it.from <= from && to <= it.to }) violated++
            else unknown++
        }
        val points = cuts.toList()
        for (i in points.indices) {
            judge(points[i], points[i])
            if (i + 1 < points.size) judge(points[i], points[i + 1])
        }
        return RollingRestAudit(expected, passed, violated, unknown)
    }

    /** Занятость нормативных смен сотрудника: [первый вылет, последний прилёт] каждой смены. */
    private fun dutyOccupancy(list: List<FlightAssignment>,
                              slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): List<RestInterval> =
        slicer(list).map { d -> RestInterval(
            d.minOf { it.flight!!.departureUTCDateTime!! }, d.maxOf { it.flight!!.arrivalUTCDateTime!! }) }

    /** Нарушенные критические клетки сотрудника на горизонте — вход для стендов. */
    internal fun violatedContinuousCells(list: List<FlightAssignment>, windowDays: Long, restMin: Long,
                                         horizon: Horizon, e: Employee? = null,
                                         dutySlicer: (List<FlightAssignment>) -> List<List<FlightAssignment>> =
                                             FlightCrewSchedulingConstraintProvider()::duties): Long {
        val restDays = e?.historyCalendarMap()?.filterValues { it.kind == "day_off" && it.night != true }
            ?.keys?.toList().orEmpty()
        return rollingRestAudit(dutyOccupancy(list, dutySlicer), windowDays * 1440, restMin, horizon,
            e?.historyLastDutyEndAt(), e?.historyAsOfAt(), restDays, e?.assumedFreeFrom()).violatedCells
    }

    /**
     * Показание непрерывной проверки окон отдыха (K-15-a единой версии):
     * version=1, unit=critical_cell; по каждому плану формы
     * [FORM_REST_IN_ROLLING_WINDOW] — по сотрудникам области
     * {expected, passed, violated, unknown} от [rollingRestAudit] с ТЕМ ЖЕ
     * горизонтом ([horizonOf]) и той же нарезкой смен, что у ограничения
     * ([restInRollingWindow]): expected = passed + violated + unknown,
     * Σ violated = штраф ограничения (вес 1 за клетку). Null — планов
     * формы нет; без горизонта (ни окна, ни рейсов) — показание с пометкой
     * и без сотрудников.
     */
    fun rollingRestAuditReading(problem: FlightCrewSchedule, plans: List<LogicPlan>,
                                dutySlicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Map<String, Any?>? {
        val rolling = plans.filter { it.form == FORM_REST_IN_ROLLING_WINDOW }
        if (rolling.isEmpty()) return null
        val h = horizonOf(problem) ?: return linkedMapOf(
            "version" to 1, "unit" to "critical_cell", "horizon" to null,
            "constraints" to emptyList<Any>(),
            "note" to "горизонта нет: ни объявленного окна, ни рейсов — клеток не судить")
        val byEmployee = problem.flightAssignments.filter { it.employee != null && it.flight != null }
            .groupBy { it.employee!! }
        val constraints = rolling.map { plan ->
            val windowMin = (plan.condBound ?: 0).toLong() * 1440
            val restMin = plan.bound.toLong()
            val employees = LinkedHashMap<String, Map<String, Long>>()
            var expected = 0L; var passed = 0L; var violated = 0L; var unknown = 0L
            for (e in problem.employees.sortedBy { it.id }) {
                if (!inScope(e, plan)) continue
                val restDays = e.historyCalendarMap().filterValues { it.kind == "day_off" && it.night != true }.keys.toList()
                val a = rollingRestAudit(dutyOccupancy(byEmployee[e].orEmpty(), dutySlicer), windowMin, restMin, h,
                    e.historyLastDutyEndAt(), e.historyAsOfAt(), restDays, e.assumedFreeFrom())
                employees[e.id] = linkedMapOf("expected" to a.expectedCells, "passed" to a.passedCells,
                    "violated" to a.violatedCells, "unknown" to a.unknownCells)
                expected += a.expectedCells; passed += a.passedCells
                violated += a.violatedCells; unknown += a.unknownCells
            }
            linkedMapOf<String, Any?>(
                "constraint" to plan.constraintId, "rules" to plan.ruleIds,
                "windowDays" to plan.condBound, "restMin" to plan.bound, "baseScope" to plan.baseScope,
                "employees" to employees,
                "totals" to linkedMapOf("expected" to expected, "passed" to passed,
                    "violated" to violated, "unknown" to unknown))
        }
        return linkedMapOf(
            "version" to 1, "unit" to "critical_cell",
            "horizon" to linkedMapOf("start" to h.startInclusive.toString(), "end" to h.endExclusive.toString()),
            "constraints" to constraints)
    }

    /**
     * ОТДЫХ НЕ МЕНЕЕ W МИНУТ В КАЖДОМ СКОЛЬЗЯЩЕМ ОКНЕ N СУТОК — непрерывное
     * покрытие (K-15): отдых — вне нормативных смен провайдера (стыковка
     * внутри смены — не отдых); окно заканчивается в любой момент
     * горизонта; сотрудники без назначений судятся тоже (concat пустого
     * списка); штраф — за доказанно нарушенную клетку.
     */
    private fun restInRollingWindow(cf: ConstraintFactory, plan: LogicPlan,
                                    slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Constraint {
        val occupied = cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && inScope(fa.employee!!, plan) }
            .groupBy(Function<FlightAssignment, Employee> { it.employee!! }, ConstraintCollectors.toList())
        val empty = cf.forEach(Employee::class.java).filter { inScope(it, plan) }
            .ifNotExists(FlightAssignment::class.java, Joiners.equal(
                Function<Employee, Employee> { it }, Function<FlightAssignment, Employee?> { it.employee }))
        val windowMin = (plan.condBound ?: 0).toLong() * 1440
        val restMin = plan.bound.toLong()
        fun failures(e: Employee, list: List<FlightAssignment>, h: Horizon): Long {
            val restDays = e.historyCalendarMap().filterValues { it.kind == "day_off" && it.night != true }.keys.toList()
            return rollingRestAudit(dutyOccupancy(list, slicer), windowMin, restMin, h,
                e.historyLastDutyEndAt(), e.historyAsOfAt(), restDays, e.assumedFreeFrom()).violatedCells
        }
        return occupied.concat(empty, Function<Employee, List<FlightAssignment>> { emptyList() })
            .join(horizonStream(cf))
            .filter { e, list, h -> failures(e, list, h) > 0L }
            .penalize(HardSoftScore.ofHard(plan.weight.toLong())) { e, list, h -> failures(e, list, h) }
            .asConstraint(plan.constraintId)
    }

    /**
     * ОТДЫХ ПОСЛЕ ПРОДЛЁННОЙ СМЕНЫ НЕ МЕНЕЕ Y. Едет на ТОЙ ЖЕ нарезке
     * смен, что правила размаха и посадок (порог отдыха по месту): вторая
     * нарезка разъехалась бы с первой молча. Судятся только фактические
     * границы смен; штраф за минуту недобора.
     */
    private fun restAfterLongDuty(cf: ConstraintFactory, plan: LogicPlan,
                                  slicer: (List<FlightAssignment>) -> List<List<FlightAssignment>>): Constraint {
        val weight = HardSoftScore.ofHard(plan.weight.toLong())
        val longMin = (plan.condBound ?: 0).toLong()
        val restMin = plan.bound.toLong()
        fun shortfall(list: List<FlightAssignment>): Long {
            val ds = slicer(list)
            var total = 0L
            for (i in 0 until ds.size - 1) {
                val d = ds[i]
                val start = d.minOf { it.flight!!.departureUTCDateTime!! }
                val end = d.maxOf { it.flight!!.arrivalUTCDateTime!! }
                val span = java.time.temporal.ChronoUnit.MINUTES.between(start, end)
                if (span <= longMin) continue
                val nextStart = ds[i + 1].minOf { it.flight!!.departureUTCDateTime!! }
                val rest = java.time.temporal.ChronoUnit.MINUTES.between(end, nextStart)
                if (rest < restMin) total += restMin - rest
            }
            return total
        }
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .groupBy(Function<FlightAssignment, Employee> { it.employee!! },
                     ConstraintCollectors.toList())
            .filter { _, list -> shortfall(list) > 0L }
            .penalize(weight) { _, list -> shortfall(list) }
            .asConstraint(plan.constraintId)
    }

    /**
     * РАБОЧИХ ДНЕЙ ПОДРЯД НЕ БОЛЕЕ N. Сутки дедуплицируются ДО серий
     * (две смены в одни сутки — один рабочий день); после дедупликации
     * число дней серии — Sequence.getCount() (== getLength(); «+1» здесь —
     * ошибка на единицу, пойманная пробой). Универсум не нужен: пустая
     * серия не нарушает «не более».
     */
    private fun consecutiveDaysMax(cf: ConstraintFactory, plan: LogicPlan): Constraint {
        // Мягкая ветка (волна П-2, Ф-4): тот же строитель, счёт по plan.score().
        val weight = plan.score()
        val bound = plan.bound
        return cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .map(Function<FlightAssignment, FlightAssignment> { it },
                 Function<FlightAssignment, Iterable<LocalDate>> { touchedDays(it.flight!!) })
            .flattenLast(Function<Iterable<LocalDate>, Iterable<LocalDate>> { it })
            // Дедупликация: (сотрудник, сутки) — один рабочий день.
            .groupBy(BiFunction<FlightAssignment, LocalDate, Employee> { fa, _ -> fa.employee!! },
                     BiFunction<FlightAssignment, LocalDate, LocalDate> { _, d -> d })
            .groupBy(BiFunction<Employee, LocalDate, Employee> { e, _ -> e },
                     ConstraintCollectors.toConsecutiveSequences(
                         BiFunction<Employee, LocalDate, LocalDate> { _, d -> d },
                         java.util.function.ToIntFunction<LocalDate> { d -> Math.toIntExact(d.toEpochDay()) }))
            .flattenLast(Function<ai.timefold.solver.core.api.score.stream.common.SequenceChain<LocalDate, Int>,
                                  Iterable<ai.timefold.solver.core.api.score.stream.common.Sequence<LocalDate, Int>>> {
                chain -> chain.consecutiveSequences
            })
            // Ревизия 1.17: серия, начатая в день среза истории, продолжает
            // серию рабочих дней из истории сотрудника (без истории — 0).
            .filter { e, seq -> seq.count + e.carryConsecutiveDutyDays(seq.firstItem) > bound }
            .penalize(weight) { e, seq -> (seq.count + e.carryConsecutiveDutyDays(seq.firstItem) - bound) }
            .asConstraint(plan.constraintId)
    }

    /**
     * ВЫХОДНЫХ В МЕСЯЦ НЕ МЕНЕЕ N. Клетки (сотрудник × полный месяц) строятся
     * из фактов и подставляются нулём занятых суток там, где назначений нет
     * (concat): пустая группа в потоке молчит, а «ни одного выходного» —
     * ровно случай, ради которого правило пишется.
     */
    private fun daysOffPerMonthMin(cf: ConstraintFactory, plan: LogicPlan): Constraint {
        val weight = HardSoftScore.ofHard(plan.weight.toLong())
        val bound = plan.bound

        // Горизонт набора (K-14): объявленное окно — приоритет; без него —
        // единый fallback по вылетам (horizonStream).
        val horizon: UniConstraintStream<Horizon> = horizonStream(cf)
        // Полные месяцы горизонта.
        val months: UniConstraintStream<YearMonth> = horizon
            .map(Function<Horizon, Iterable<YearMonth>> { fullMonths(it.lo, it.hi) })
            .flattenLast(Function<Iterable<YearMonth>, Iterable<YearMonth>> { it })

        // Универсум клеток: каждый сотрудник области × каждый полный месяц;
        // перенос отработанных суток из истории у полного месяца — ноль по
        // контракту (срез истории не позже начала горизонта).
        val fullCells: UniConstraintStream<EmployeeMonth> = cf.forEach(Employee::class.java)
            .filter { e -> inScope(e, plan) }
            .join(months)
            .map(BiFunction<Employee, YearMonth, EmployeeMonth> { e, m ->
                EmployeeMonth(e.id, m, e.carryMonthDaysWorked(m))
            })
        // Ревизия 1.17 (решение владельца 05.09.2026): месяц среза истории —
        // клетка сотрудника С ИСТОРИЕЙ, когда месяц начат до горизонта и
        // горизонт доходит до его конца: занятые сутки = отработанные до
        // среза (month_to_date.days_worked) + занятые сутки горизонта. Без
        // истории неполный месяц — вне оценки, как прежде.
        val histCells: UniConstraintStream<EmployeeMonth> = cf.forEach(Employee::class.java)
            .filter { e -> inScope(e, plan) && e.historyMonth() != null }
            .join(horizon, Joiners.filtering(java.util.function.BiPredicate<Employee, Horizon> { e, h ->
                val m = e.historyMonth()!!
                m.atDay(1).isBefore(h.lo) && !m.atEndOfMonth().isAfter(h.hi)
            }))
            .map(BiFunction<Employee, Horizon, EmployeeMonth> { e, _ ->
                val m = e.historyMonth()!!
                EmployeeMonth(e.id, m, e.carryMonthDaysWorked(m))
            })
        val universe: UniConstraintStream<EmployeeMonth> = fullCells.concat(histCells)

        // Занятые сутки сотрудника по месяцам: назначение касается суток от
        // даты вылета до даты прилёта; одни сутки считаются один раз. Ключ
        // несёт тот же перенос из истории, что и универсум (одна функция от
        // того же сотрудника и месяца).
        val busy: BiConstraintStream<EmployeeMonth, Long> = cf.forEach(FlightAssignment::class.java)
            .filter { fa -> fa.employee != null && (plan.baseScope == null || inScope(fa.employee!!, plan)) }
            .map(Function<FlightAssignment, FlightAssignment> { it },
                 Function<FlightAssignment, Iterable<LocalDate>> { touchedDays(it.flight!!) })
            .flattenLast(Function<Iterable<LocalDate>, Iterable<LocalDate>> { it })
            .groupBy(BiFunction<FlightAssignment, LocalDate, EmployeeMonth> { fa, d ->
                val m = YearMonth.from(d)
                EmployeeMonth(fa.employee!!.id, m, fa.employee!!.carryMonthDaysWorked(m))
            }, ConstraintCollectors.countDistinct(BiFunction<FlightAssignment, LocalDate, LocalDate> { _, d -> d }))
            // Только клетки универсума: неполный месяц без истории вне оценки.
            .ifExists(universe, Joiners.equal(
                BiFunction<EmployeeMonth, Long, EmployeeMonth> { c, _ -> c },
                Function<EmployeeMonth, EmployeeMonth> { it }))
        val busyKeys: UniConstraintStream<EmployeeMonth> =
            busy.map(BiFunction<EmployeeMonth, Long, EmployeeMonth> { c, _ -> c })

        // Клетки без назначений — ноль занятых суток горизонта (все дни
        // месяца сверх переноса из истории — выходные).
        val zero: UniConstraintStream<EmployeeMonth> = universe
            .ifNotExists(busyKeys, Joiners.equal(
                Function<EmployeeMonth, EmployeeMonth> { it },
                Function<EmployeeMonth, EmployeeMonth> { it }))
        val cells: BiConstraintStream<EmployeeMonth, Long> =
            busy.concat(zero, Function<EmployeeMonth, Long> { 0L })

        return cells
            .filter { c, busyDays -> c.month.lengthOfMonth() - (busyDays + c.carryDays) < bound }
            .penalize(weight) { c, busyDays -> (bound - (c.month.lengthOfMonth() - (busyDays + c.carryDays))).toLong() }
            .asConstraint(plan.constraintId)
    }
}
