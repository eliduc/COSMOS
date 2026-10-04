# 08 — Грабли и фиксы

[← Home](Home.md)

Все ловушки, на которые наступили, и как обошли. Каждая стоила времени —
читай перед тем, как воспроизводить.

## 1. Дубликаты flight_number → HTTP 400 (Jackson)
**Симптом:** single-stage POST на полный BTS → `400 Bad Request` с пустым
телом, ~0.4s. **Причина:** BTS переиспользует номер для разных плеч в день
(DL555 утром+вечером); Kotlin `@JsonIdentityInfo(property="flightNumber")`
требует уникальности → `Object Id conflict`. 317 дублей (47% рейсов).
**Фикс:** в `_bts_to_schedule.py` суффикс `-HHMM` (локальное время вылета)
к номеру → 1 358 уникальных. *(В two-stage не проявлялось — pairing_id
уникален.)*

## 2. Тело 4xx/5xx теряется
`r.raise_for_status()` до чтения `r.text` → причина ошибки невидима.
**Фикс:** в `timefold_kotlin_engine.py` логировать `r.text[:2000]` и размер
payload ДО raise. Для JVM-логов — `-Dquarkus.log.category."...jackson".level=DEBUG`.

## 3. CP-SAT num_workers=8 → недетерминированный набор ротаций
8 потоков гонятся, берётся первый из равно-оптимальных set-partition'ов;
разные наборы → разная трудность Stage 2 (−425k…−990k). **Фикс:**
`num_workers=1` в `_run_bts_compare.py`, `_run_bts_two_stage.py`,
`_run_bts_multitrial.py`. Проверено: hash набора стабилен.

## 4. PHASE_ASSERT (дефолт Quarkus Timefold) → CH 84 минуты
**Симптом:** интервал с бюджетом 150s шёл 84 минуты. **Причина:** Quarkus
Timefold по умолчанию `environment-mode=PHASE_ASSERT` (debug, гоняет дорогие
проверки, ×100–1000 медленнее). **Фикс:** `environment-mode=no-assert` в
`application.properties` И через JVM-флаг `-Dquarkus.timefold.solver.environment-mode=no-assert`.

## 5. Timefold 2.0 переименовал enum: REPRODUCIBLE → no-assert
Первый rebuild упал: `Cannot convert REPRODUCIBLE to enum ... allowed:
no-assert, full-assert, ..., non-reproducible, phase-assert`. Для
детерминированного быстрого режима — **`no-assert`**.

## 6. Timefold 2.0 API трудности: difficultyWeightFactoryClass → comparatorFactoryClass
`@PlanningEntity(difficultyWeightFactoryClass=...)` и
`SelectionSorterWeightFactory` **удалены** в 2.0. Теперь:
`@PlanningEntity(comparatorFactoryClass = ...)` +
`ComparatorFactory<Solution, T>.createComparator(solution): Comparator<T>`.
Проверять API через `javap` по jar в `~/.m2/repository`.

## 7. FIRST_FIT_DECREASING игнорирует phase-level entitySorterManner
Бандл-тип `FIRST_FIT_DECREASING` использует `DESCENDING_IF_AVAILABLE` и
молча откатывается к FF, не цепляя компаратор (FFD выходил байт-в-байт как
FF). **Обход:** `ALLOCATE_ENTITY_FROM_QUEUE` + явный
`entitySorterManner = DESCENDING` (документация: «для явного контроля
используйте verbose-форму»). Enum 2.0: `NONE / DESCENDING / DESCENDING_IF_AVAILABLE`.

## 8. Modern Standby (S0) убивает ночные прогоны
**Симптом:** прогон дважды оборвался посреди (на 14-й мин и через ~1ч);
java/python-процессы мертвы, CSV пустой. **Причина:** ноут на S0 Low Power
Idle (классический S1/S2/S3 отключён). `SetThreadExecutionState`
(будильник `_keep_awake.py`) **не удерживает** Modern Standby. **Фикс
(требует прав, обратим):** `powercfg /change standby-timeout-ac 0`,
`monitor-timeout-ac 0`, `hibernate-timeout-ac 0`, unattended-sleep
(`SUB_SLEEP 7bc4a2f9-... 0`). Восстановить: standby 7200 / monitor 1800.

## 9. Warm-start миграции: нельзя пропускать CH
Попытка `warmStart=true` (только LS, без CH) на интервалах 2+ падает:
`Local Search needs an initialized solution, but there are N uninitialized
entities` — в расписании есть легальные null (нет годного экипажа).
**Решение:** оставить CH+LS; FIRST_FIT по дефолту переинициализирует только
null-сущности (REINITIALIZE_VARIABLE), global best не трогается.

## 10. Координатор миграции сеял best-of-interval, не best-ever
Деградация по интервалам, потому что seed следующего интервала брался от
лучшего острова ЭТОГО интервала. **Фикс:** обновлять seed только при новом
global best.

## 11. interval_s игнорировал бюджет
`max(60, budget//n)` давал 8×60s=480s при бюджете 120s (overshoot ×4).
**Фикс:** `compute_intervals()` в `_compr_isolated.py` — уменьшает число
интервалов, если бюджет мал, с предупреждением.

## 16. Строка счёта НЕ выдаёт частичную инициализацию — считайте null-слоты
Решение с 70% ПУСТЫХ слотов возвращает невинный счёт вида
`-150hard/-396765soft` — без init-префикса; пустые сущности просто
исключаются из всех constraint'ов (`forEach` пропускает неинициализированные).
Меньше заполнено → меньше штрафов → «лучше» счёт. На этом сгорел вывод v2
(«портфель выигрывает 43%»): CH на синтетике требует ~300s, интервал
ансамбля — 150s → «победители» интервала 1 были полу-пустыми. **Правило:**
при любом сравнении счетов сначала подтвердить полноту
(`count_nulls == 0`); в координаторах ансамблей — принимать в зачёт только
полные решения, частичные использовать лишь как warm-start.

## 14. In-place мутация расписания → контаминация «независимых» прогонов
`solve_with_timefold_kotlin` **мутирует переданный объект** (докстринг это
даже говорит). Харнесс, передающий один объект в серию trial'ов, получает
warm-start вместо холодных повторов. **Улики:** first_feasible=2.0s на
холодной задаче из 6 824 слотов; монотонное улучшение soft «между
trial'ами». **Фикс:** сырые dict-payload + `copy.deepcopy` на прогон
(`_run_bts_multitrial_v2.py`).

## 15. random-seed=0 в application.properties = seed-монокультура
Все solver'ы (и все острова ансамблей) наследуют seed 0: повторы реплицируют
одну траекторию, диверсификация островов — только по acceptor'у, LA-остров
идентичен одиночному прогону. Из-за этого «ансамбль ничего не даёт» выглядело
доказанным, а было артефактом. **Фикс:** параметр `randomSeed` в
`/schedules/single` + `seeds=` в `IsolatedJvmFleet.solve_parallel`; свой seed
на trial/остров/интервал. Legacy-пути без seeds сохраняют старое поведение.

## 13. SolutionManager.analyze() — Enterprise-only
Per-constraint breakdown через `solutionManager.analyze(solution)` бросает
`IllegalStateException: A commercial feature "Score analysis" ... could not
be loaded` (ClassNotFound `...enterprise.core...`). В Community недоступно.
**Обход:** считать breakdown в Python по решённому расписанию
(`_run_constraint_breakdown.py` — реплицирует 9 жёстких правил с весами из
`.kt`; совпадает со счётом решателя до цифры). Endpoint `/schedules/analyze`
оставлен, но в Community вернёт 500.

## 12. Python 3.14 — нет timefold (JPype) wheel
JPype не собирается на 3.14 → TF-Python/SForge недоступны. **Фикс:**
`_timefold_compat.py` — опциональный импорт со stub'ами; `domain.py`,
`json_serialization.py`, `benchmark.py`, `__init__.py` импортят через шим.
Доступен только Kotlin-путь (он и так быстрее).

См. далее: [09 — Как запускать](09-How-To-Run.md)
