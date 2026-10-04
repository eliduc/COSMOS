# 03 — Методы

[← Home](Home.md)

Четыре класса методов + каталог встроенных алгоритмов Timefold.

## Класс 1 — Single-engine (один движок, один поток)

| Метод | Что | Файл |
|---|---|---|
| TF-Python | Timefold 1.24 через JPype (архивный референс) | `solver.py` |
| **TF-Kotlin** | Timefold 2.0 нативно в JVM (Quarkus, порт 9091) | `timefold_kotlin_engine.py` |
| SForge | community-форк Timefold 1.24 | `solverforge_engine.py` |
| OR-Tools CP-SAT | SAT-based, другая парадигма | `ortools_engine.py` |

Дефолт TF-Kotlin: Construction Heuristic FIRST_FIT → Local Search Late
Acceptance (size 400).

> ⚠️ **Python-движки (TF-Python, SForge) не работают на Python 3.14** —
> нет JPype-wheel. На текущей машине доступен только Kotlin-путь
> (см. [09 — Как запускать](09-How-To-Run.md)). Шим `_timefold_compat.py`
> позволяет импортировать пакет без `timefold`.

## Класс 2 — Independent parallel ensembles (N островов, без обмена)

N планировщиков параллельно, в конце — лучший. Реестр в Kotlin
`FlightCrewSchedulingResource.kt` (endpoint `/schedules/comprehensive`).

| Ensemble | Острова |
|---|---|
| classic | LA + SA + TABU + GD |
| E (Multi-LA) | LA-200 / LA-400 / LA-800 / LA-1600 |
| B (gold) | LA + DLA + SA + SCHC |
| B1 (3-way) | LA + SA + SCHC |
| H (multi-phase) | 4 копии CH→LA→SA→Tabu→LA с разными seeds |

## Класс 3 — Cooperative ensembles (с миграцией, in-JVM)

Каждые τ берётся global best, все острова рестартуют от него (Synchronous
Island Model). Варианты I (5/3/6/7/8/10 интервалов), Ip (perturbation),
Ia (asymmetric), HI (hybrid).

## Класс 4 — Process-isolated ensembles (N отдельных JVM)

То же, но каждый остров — отдельный JVM-процесс (порты 9091+). Убирает
конкуренцию за GC/JIT/память. Координатор — `_compr_isolated.py`
(`IsolatedJvmFleet`), реестр `ENSEMBLES`. Endpoint `/schedules/single`.

- **single-LA-iso**, **classic-iso**, **I-iso (5)**, **I_8-iso (8)**
- Расширенные: **I_6wide** (LA+DLA+SA+SCHC+TABU+GD, 6 JVM), I_8wide.

> Это **идиоматичный способ Community-edition** запускать разные стратегии
> одновременно: сам Timefold внутри одного solver'а фазы выполняет
> **последовательно**; параллельный портфель = несколько Solver-инстансов.

## Класс 5 — Two-stage decomposition

**Stage 1 (pairing):** перечислить легальные ротации (DFS,
`pairing_generator.py`) → CP-SAT set-partitioning выбрать оптимальный
набор (`pairing_selector.py`). **Stage 2 (rostering):** Timefold
расставляет экипаж по выбранным ротациям (`pairing_to_schedule.py`).

Драйверы: `_run_bts_two_stage.py`, `_run_bts_compare.py` (алгоритмы
`ts_classic`, `ts_compr_E`, `ts_iso_I_8`, `ts_iso_classic`, `ts_ortools`),
`ss_classic` (single-stage на полном расписании).

> ⚠️ CP-SAT с `num_workers>1` **недетерминирован** — выбирает случайный из
> равно-оптимальных наборов ротаций, что меняет трудность Stage 2. Для
> воспроизводимости — `num_workers=1`. См. [08 — Грабли](08-Gotchas.md).

---

## Каталог встроенных методов Timefold (для feasibility)

Фазы выполняются **последовательно** внутри одного solver'а. Всё доступно
в Community 2.0.0.

### Construction Heuristics (строят начальное решение — главный рычаг feasibility)
- **First Fit** — по порядку, лучший доступный, без пересмотра (наш дефолт).
- **First Fit Decreasing** — сначала самые трудные сущности (нужен
  difficulty-компаратор). В 2.0: `@PlanningEntity(comparatorFactoryClass=...)`.
- Weakest/Strongest Fit (+Decreasing) — по силе значения (нужен value-strength).
- Cheapest Insertion / Allocate From Pool — дешевейшая пара на шаг, дороже масштабируется.
- Allocate Entity From Queue — общий каркас с настраиваемыми сортировщиками.

### Exhaustive Search (гарантия, но только крошечные задачи)
- Brute Force, Branch And Bound.

### Local Search acceptors (чинят черновик к 0 hard)
- Hill Climbing, Tabu Search, **Simulated Annealing** (пробивает плато),
  Late Acceptance (дефолт), Diversified LA, Step Counting HC, Great Deluge.

### Параллельность Timefold (Enterprise-only — у нас нет)
- Multithreaded incremental solving (moveThreadCount), Partitioned Search,
  Nearby Selection. Эти фичи **ускоряют один метод**, а не стравливают
  разные — портфель разных стратегий всё равно делается островами.

> Подробный разбор «can we run different methods at once» и рекомендации
> по feasibility — в [10 — Журнал](10-Experiments-Log.md) и
> [06 — Выводы](06-Findings.md).

См. далее: [04 — Архитектура](04-Architecture.md)
