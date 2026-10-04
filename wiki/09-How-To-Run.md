# 09 — Как запускать

[← Home](Home.md)

## Окружение (текущая машина)

| Компонент | Путь / версия |
|---|---|
| Python | 3.14, venv в `crew-demo/.venv` |
| Python deps | httpx, pydantic, ortools, fastapi, uvicorn (**без** timefold) |
| JDK | Eclipse Temurin 21 — `C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot` |
| Maven | portable — `crew-demo/.tools/apache-maven-3.9.15` |
| Kotlin JAR | `kotlin-solver/target/quarkus-app/quarkus-run.jar` |

### PATH для сессии (Git Bash)
```bash
export PATH="/c/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot/bin:$PATH"
# для сборки добавить Maven:
export PATH="$PATH:/c/work/Sandbox/Testing/TimeFold/crew-demo/.tools/apache-maven-3.9.15/bin"
export JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot"
```
Python везде вызывать как `.venv/Scripts/python.exe`.

## Пересборка Kotlin-движка (после правок в kotlin-solver/)
```bash
cd kotlin-solver && mvn package -DskipTests -q
# JAR обновится: target/quarkus-app/quarkus-run.jar (~7 мин)
```
Проверять API Timefold через `javap -cp <jar-в-~/.m2> <FQCN>`.

## Подготовка данных
```bash
# Перегенерировать BTS-расписание (с уникальными flight_number):
.venv/Scripts/python.exe _bts_to_schedule.py \
  "data/bts/On_Time_Reporting_..._2024_8.csv" DL ATL 2024-08-15
```

## Запуск бенчмарков

### BTS compare (single-stage vs two-stage варианты)
```bash
.venv/Scripts/python.exe _run_bts_compare.py \
  data/bts_schedules/DL_ATL_2024-08-15.json 1200 \
  ss_classic,ts_classic,ts_iso_I_8
# алгоритмы: ss_classic, ts_classic, ts_compr_E, ts_iso_I_8,
#            ts_iso_classic, ts_iso_I_6wide, ts_ortools
# → data/bts_compare_results.csv
```

### BTS multi-trial (n=5, достоверно)
```bash
.venv/Scripts/python.exe _run_bts_multitrial.py 1200 5
# 5 блоков × 3 метода (all_at_once / trips_no_exchange / trips_with_exchange)
# → data/bts_multitrial_results.csv (+ агрегат: медиана/размах/стандартное откл.)
```

### Изолированный ансамбль (один trial)
```bash
.venv/Scripts/python.exe _compr_isolated.py I_8 4 1200 8
#  <ensemble> <dataset_idx S1=0..S6=5> <total_s> <num_intervals>
```

### CH-эксперимент (FF vs FFD на S4)
```bash
.venv/Scripts/python.exe _run_ch_experiment.py 3 90 FF,FFD,CI
#  <dataset_idx> <budget_s> <ch-список: FF,FFD,WF,WFD,SF,SFD,CI>
```

## ⚠️ Перед длинным прогоном без присмотра — отключить сон
Modern Standby убивает прогон (см. [грабли §8](08-Gotchas.md)). Требует прав:
```powershell
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP 7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 0
powercfg /setactive SCHEME_CURRENT
# дополнительно — будильник на время прогона:
.venv/Scripts/python.exe _keep_awake.py &
```
**Восстановить после:** `standby-timeout-ac 7200`, `monitor-timeout-ac 1800`.

## Полезные мелочи
- Убить зависшие JVM-порты: импортировать `_kill_port` из
  `flight_crew_scheduling.benchmark`, пройтись по 9091–9096.
- Долгие прогоны — в фоне (`run_in_background`), результаты пишутся в CSV
  построчно (переживают обрыв).

См. далее: [10 — Журнал экспериментов](10-Experiments-Log.md)
