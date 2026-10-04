# 04 — Архитектура

[← Home](Home.md)

## Слои

```
┌─ Python (orchestration) ────────────────────────────────────┐
│  demo_data.py            генерация S1–S6                     │
│  _bts_to_schedule.py     BTS CSV → FlightCrewSchedule JSON   │
│  domain.py / json_*.py   Pydantic-модель + (де)сериализация  │
│  pairing_*.py            two-stage Stage 1 (enum + CP-SAT)   │
│  timefold_kotlin_engine  HTTP-прокси к Kotlin-движку         │
│  ortools_engine.py       OR-Tools CP-SAT                     │
│  _compr_isolated.py      координатор N изолированных JVM     │
│  _run_*.py               драйверы экспериментов              │
└─────────────────────────────────────────────────────────────┘
                    │ HTTP (JSON, camelCase)
                    ▼
┌─ Kotlin / Quarkus (solver) ─ kotlin-solver/ ────────────────┐
│  domain/*.kt             Flight, Employee, FlightAssignment, │
│                          FlightCrewSchedule (@Planning*)     │
│  solver/...ConstraintProvider.kt   22 ограничения (v2)      │
│  solver/...DifficultyComparatorFactory.kt  (FFD-эксперимент) │
│  rest/...Resource.kt     /schedules, /schedules/single,     │
│                          /schedules/comprehensive           │
│  Timefold Solver 2.0.0 Community core                       │
└─────────────────────────────────────────────────────────────┘
```

## Доменная модель (Kotlin)

- **`FlightCrewSchedule`** `@PlanningSolution` — airports, employees, flights
  (problem facts), flightAssignments (`@PlanningEntityCollectionProperty`),
  score (`@PlanningScore`).
- **`FlightAssignment`** `@PlanningEntity` — id, flight, indexInFlight,
  requiredSkill, **employee (`@PlanningVariable`)**. С 2.0 несёт
  `comparatorFactoryClass` для FFD.
- **`Flight`** — `@JsonIdentityInfo(property="flightNumber")` (требует
  уникальный номер! см. [грабли](08-Gotchas.md)), aircraftType, времена UTC,
  хелперы `getDurationMinutes/isLongHaul/isRedEye/...`.
- **`Employee`** — `@JsonIdentityInfo(property="id")`, skills, typeRatings,
  languages, home airport, unavailable/preferred days.

## 22 ограничения (`FlightCrewSchedulingConstraintProvider.kt`) — RULES v2

> **Эпоха правил v2 (duty-периоды):** старое «Min rest 10 ч между любыми
> рейсами» заменено парой turnaround/duty-rest, зеркалящей пороги
> генератора ротаций — плоская модель и pairing-модель теперь кодируют
> один операционный мир (кроме 14-часовой смены, см. примечание).
> Счета эпох v1 и v2 несравнимы между собой.

**Hard (10):**
| Правило | Штраф | Тип |
|---|---|---|
| Required skill | ofHard(100) | плоский (бинарный) |
| Type rating required | ofHard(50) | плоский (бинарный) |
| Flight conflict (наложение) | ofHard(1)×часы наложения | пропорциональный ✓ (изменён в экспер. #5) |
| Employee unavailable | ofHard(10) | плоский |
| **Minimum turnaround** (v2) | ofHard(5)×(60мин−gap) | пропорциональный ✓ |
| **Rest between duty periods** (v2) | ofHard(5)×(8ч−gap), для gap в мёртвой зоне 6–8 ч | пропорциональный ✓ |
| Max daily duty | ofHard(5)×excess | пропорциональный ✓ |
| Max monthly hours | ofHard(3)×excess | пропорциональный ✓ |
| Max weekly hours | ofHard(2)×excess | пропорциональный ✓ |
| Language qualification | ofHard(2) | плоский |

**Soft (12):** transfer between flights (800), positioning deadhead (500),
reserve usage (300), too many duty days (500×), layover nights (40),
preferred day off (50×seniority), undesired airport (20×seniority),
fair red-eye / weekend (loadBalance×20), balanced workload (loadBalance).

> История правок: `Flight conflict` сделан пропорциональным в эксперименте
> #5 (выигрыш маргинален, оставлен для консистентности); rest-правила
> переведены на duty-периоды (v2) после re-scoring'а, показавшего, что
> старое 10-часовое правило запрещает саму суть ротаций — см.
> [10 — Журнал](10-Experiments-Log.md). Python-провайдер
> (`constraints.py`, JPype) НЕ обновлён (нетестируем на Python 3.14) и
> остаётся на правилах v1 — авторитетен Kotlin.

## Stage 1 — pairing (two-stage)

1. `pairing_generator.enumerate_legal_pairings` — DFS: min ground 60 мин,
   min overnight rest 8 ч, max flight 30 ч, max окно 96 ч, max 5 плеч,
   один тип ВС в ротации, single-flight как fallback.
2. `pairing_selector.select_pairings_set_partitioning` — CP-SAT:
   `min Σ cost(p)·x_p` при `Σ x_p = 1 ∀ flight` (точное покрытие),
   `cost = duty_minutes − 200·(legs−1)`. **`num_workers=1` для
   воспроизводимости.**
3. `pairing_to_schedule.build_rostering_schedule` — каждая ротация →
   синтетический «супер-рейс»; crew composition по long-haul флагу.

> ⚠️ Синтетический рейс имеет `duration = pairing span` (включая overnight
> rest), что **раздувает H8 (weekly hours)** → завышенная нагрузка. Главная
> причина проигрыша two-stage по per-slot. См. [06 — Выводы](06-Findings.md).

## Kotlin endpoints

| Endpoint | Назначение |
|---|---|
| `POST /schedules` | async job (CH FIRST_FIT → LA-400), polling |
| `POST /schedules/single` | синхронный, 1 acceptor; параметры `acceptor`, `ch`, `warmStart`, `randomSeed`, `spentLimitSeconds` |
| `POST /schedules/comprehensive` | in-JVM ансамбль (`ensemble`, `phaseAFraction`) |
| `POST /schedules/score` | **score-only** (`SolutionManager.update`, Community) — пересчёт счёта без решения; основа re-scoring |
| `POST /schedules/analyze` | per-constraint breakdown — **Enterprise-only**, в Community вернёт 500 (используйте Python-зеркало) |
| `GET /schedules/{id}/islands` | live per-island scores |

См. далее: [05 — Результаты](05-Results.md)
