# Реестр инвариантов COSMOS

Назначение реестра — сделать сходимость процесса «аудит → исправление»
измеримой. Внешний аудит R9 показал, что прежние раунды закрывали
ЭКЗЕМПЛЯР дефекта, а следующий аудит находил СОСЕДА ПО КЛАССУ. Причина не
в невнимательности: закрытие формулировалось прозой («механизм добавлен»),
а проза не проверяется машиной.

Здесь каждый инвариант несёт **область действия** и **запрос переписи** —
проверку по исходным текстам, которую исполняет судья
`tests/test_invariant_registry.py`. Пока перепись зелёная, класс закрыт не
по нашему утверждению, а по состоянию кода. Инвариант без переписи
считается **объявленной отсрочкой** и обязан нести раунд и причину —
честная отсрочка законна, широкое обещание при узком механизме запрещено.

Правило формулировки: **область действия инварианта записывается ровно по
тому, что механизм действительно накрывает.** Формулировки вида «в системе
не существует…» при механизме на один файл — та самая форма, из-за которой
аудитор R10 находит первое место, где обещание ложно.

---

## Действующие инварианты

| Идентификатор | Формулировка | Область действия | Перепись | Тесты | Раунд | Статус |
|---|---|---|---|---|---|---|
| `INV-TIME-01` | Метка времени в хранимом наборе не несёт смещения: канонизация — свойство границы записи, а не обязанность читателя | `webapp/registry.py`, пять производных путей записи, вход расчётного слоя | `census_payload_writers_function_level` (AST, помодульно) | `test_canonical_time_r9.py::test_is_canonical_exact_contract_rejects`, `test_canonical_time_r9.py` | R9 → сужен R10 → доработан пакетом R10 | **частично** |
| `INV-TIME-02` | Идентификатор участка не переписывается канонизацией: он личность, а не формула | `_canonical_time.py`, `webapp/data_import.py` | — (покрыт тестом) | `test_canonical_time_r9.py::test_leg_identity_is_not_rewritten_by_canonization` | R9 | закрыт |
| `INV-TIME-03` | Аварийный выключатель идентичности не гасит проверку времени | `_leg_identity.py` | — (покрыт тестом) | `test_canonical_time_r9.py::test_identity_kill_switch_does_not_disable_the_time_check` | R9 | закрыт |
| `INV-PRICE-01` | Усечение поиска записывается в момент наблюдения отброшенного варианта, а не выводится из длины уже усечённого списка | `_pricing_certificate.py`, `_pairing_builder.py` | `census_dh_options_sink`, `census_price_pass_returns_pricing_result` | `test_differential_oracle.py::test_oracle_grid_completeness_or_explained`, `test_differential_oracle.py::test_depth_witness_sees_teleport_space`, `test_differential_oracle.py::test_one_stop_transfer_is_order_invariant`, `test_differential_oracle.py::test_oracle_catches_muted_recorder` | R9 → переоткрыт R10 → закрыт пакетом R10 (дифференциальный оракул + мета-мутации) | закрыт |
| `INV-PRICE-02` | Приёмники усечений раздельны: разогрев пула не подавляет границу, проход ценообразования подавляет | `_pricing_certificate.py`, `_pairing_builder.py` | — (покрыт тестом) | `test_pricing_certificate.py::test_pool_truncation_does_not_suppress_the_bound`, `test_pricing_certificate.py::test_pricing_result_certifies_only_without_suppressing_rows` | R9 | закрыт |
| `INV-PRICE-03` | Нижняя граница округляется вниз | `_pricing_certificate.py`, `_pairing_builder.py`, `_gerad_colgen.py` | — (покрыт тестом) | `test_pricing_certificate.py::test_lower_bound_rounds_down_not_to_nearest` | R9 | закрыт |
| `INV-PRICE-04` | Структурное усечение второго движка отнимает достоверность у границы линейной релаксации | `_gerad_colgen.py` | `census_price_pass_returns_pricing_result` | `test_pricing_certificate.py::test_pricing_result_and_truncations_survive_pickle`, `test_pricing_certificate.py` | R9 → переоткрыт R10 → продуктивный прайсер смен переведён на PricingResult (max_legs пишет усечение); прямой мутационной пары у второго движка нет | **частично** |
| `INV-SIG-01` | Подпись постановки различает прогоны по каждому параметру, меняющему допустимость или цель | `_pairing_builder.py::config_signature` | — (покрыт тестом) | `test_pricing_certificate.py::test_signature_separates_runs_by_every_formulation_parameter` | R9 → уточнён R10 → max_landings и предел налёта внесены в отпечаток; содержимое каталогов в подпись не входит | **частично** |
| `INV-RULES-01` | Правила источника читаются с диска один раз за прогон: отпечаток и работа идут по одним байтам | `_gerad_stage2_roster.py`, `_gerad_trajectory_check.py`, `webapp/jobs.py` | — (покрыт тестом) | `test_source_rules_single_read.py::test_loader_accepts_the_bytes_that_were_hashed`, `test_source_rules_single_read.py` | R9 | закрыт |
| `INV-INBOX-01` | Идентичность заявки — функция содержания находки, а не её номера в ответе модели | `webapp/audit_inbox.py` | — (покрыт тестом) | `test_audit_inbox_idempotency.py::test_finding_id_is_content_addressed_not_positional` | R9 | закрыт |
| `INV-INBOX-02` | Уникальность идентификатора обеспечивает физическая точка записи очереди | `webapp/change_requests.py::_add_crs` | — (покрыт тестом) | `test_audit_inbox_idempotency.py::test_duplicate_id_is_refused_at_the_write_point` | R9 | закрыт |
| `INV-INBOX-03` | Восстановление после сбоя не обращается к языковой модели | `webapp/audit_inbox.py` | — (покрыт тестом) | `test_audit_inbox_idempotency.py::test_dropped_finding_does_not_shift_neighbours`, `test_audit_inbox_idempotency.py` | R9 | закрыт |
| `INV-RULESCFG-01` | Три таблицы умолчаний правил (Python, решатель, отслеживаемый образец) совпадают | `rules_config.py`, `RulesConfig.kt`, `airline_default.json.example` | — (покрыт тестом) | `test_handover_pins.py::test_kotlin_defaults_match_python_defaults` | R9 | закрыт |
| `INV-CAND-02` | Кандидатский код раскладывается без своего каталога `.git`; неотслеживаемые настройки переносятся явно | `webapp/evolution_runner.py::materialize` | — (покрыт тестом) | `test_package_c.py::test_materialized_tree_has_no_candidate_git` | R9 | закрыт |
| `INV-CAND-04` | Продвижение на внутренний и боевой контуры невозможно без подтверждения человека, привязанного к правке, срезу и фазе | `webapp/evolution_runner.py` (гейт фаз), `tools/countersign.py` | — (покрыт тестом) | `test_package_c.py::test_candidate_hooks_do_not_survive_materialization`, `test_package_c.py` | R9 | закрыт |
| `INV-EXPORT-01` | Состав экипажа в выгрузке — свойство командировки, а не смены: одинаков во всех сменах и не занижается до поучасткового спроса | `_payload_to_meridian.py` | — (покрыт тестом) | `test_meridian_export_critique.py::test_complements_are_chain_level_and_identical_across_duties` | R9 | закрыт |
| `INV-EXPORT-02` | Границы смены и `workTimeCredit` несут подготовку и разбор, а не размах полётов | `_payload_to_meridian.py` | — (покрыт тестом) | `test_meridian_export_critique.py::test_catalog_crew_is_not_reduced_to_leg_demand`, `test_meridian_export_critique.py` | R9 | закрыт |
| `INV-EXPORT-03` | Перелёт пассажиром принадлежит смене (роль «D/H»), отдельного наряда не существует; смен с пустым составом нет | `_payload_to_meridian.py` | — (покрыт тестом) | `test_meridian_export_critique.py`, `test_meridian_export_critique.py::test_complements_are_chain_level_and_identical_across_duties` | R9 | закрыт |
| `INV-PROBLEM-01` | Единый неизменяемый манифест постановки: контент-хэши набора, правил, каталогов, конфигурации и сборки решателя записываются один раз при постановке; идентификатор пересчитывается при чтении; каталог этапа 1 и производные артефакты штампуют его | `webapp/problem_manifest.py`, `webapp/jobs.py` | — (покрыт тестом) | `test_problem_manifest.py::test_write_is_immutable_and_load_verifies`, `test_problem_manifest.py::test_build_refuses_command_fp_mismatch`, `test_problem_manifest.py::test_build_records_catalog_hash_and_upstream_chain` | R10 → закрыт пакетом закрытия | закрыт |
| `INV-RULES-02` | Все линии этапа 1 получают правила источника: у второго движка fp обязателен, числовые пределы применяются к формулировке (и едут в воркеры), а равнозначные станции — громкий отказ, не тихое игнорирование | `webapp/jobs.py`, `_gerad_colgen.py` | — (покрыт тестом) | `test_formulation_identity_r10.py::test_colgen_applies_numeric_source_limits`, `test_formulation_identity_r10.py::test_colgen_refuses_station_equivalence`, `test_formulation_identity_r10.py::test_colgen_source_rules_fp_mismatch_refused` | R10 → закрыт пакетом закрытия | закрыт |
| `INV-RULES-04` | Каталог второго движка несёт отпечаток правил источника в подписи | `_gerad_colgen.py` | — | `test_formulation_identity_r10.py::test_colgen_source_rules_fp_mismatch_refused` | R10 → штамп в подписи есть; юнита, читающего сам дамп, нет | **частично** |
| `INV-CAND-03` | Кандидат и подписант разделены субъектами: агент внедрения И батарея проверки (pytest/замер) исполняются под записью `cosmos-agent` через посредника; материализация без `.git` — шаг конвейера | `webapp/agent_broker.py`, `webapp/evolution_runner.py`, `webapp/change_requests.py` | — | `test_agent_broker.py::test_launch_exec_mode_writes_script_and_cleans_stale`, `test_evolution_runner.py::test_test_phase_happy_path`, `test_change_requests.py::test_implementer_uses_broker_when_available` | R10 → Test-контур разделён; остатки: целостность кода завершения в пределах записи агента, производственный посредник Stage/Prod, внешний держатель секрета (`INV-CAND-05`) | **частично** |

---

## Переоткрыто аудитом R10 (31.07.2026)

Записи выше со статусом «переоткрыт» понижены не по нашей инициативе:
внешний аудит предъявил мутации, опровергающие гарантию. Понижение
статуса — часть контракта реестра, а не исключение из него. Конкретные
опровержения:

| Инвариант | Чем опровергнут |
|---|---|
| `INV-PRICE-01` | Отбор перегона с пересадкой обрывается на первом подходящем втором рейсе; отброшенные альтернативы не записываются, и порядок массива начинает менять математический ответ |
| `INV-PRICE-01` | Свидетель предела глубины смотрит на вылеты со станции, тогда как продолжения включают и равнозначные станции — усечение можно ПРОПУСТИТЬ |
| `INV-PRICE-04` | Исправлен запасной прайсер `_gerad_colgen`, а продуктивный прайсер смен `_gerad_duty_pricing.py` обрезает по числу участков, не трогая признак исчерпанности |
| `INV-SIG-01` | Содержимое базового и закрепляющего каталогов меняет пространство допустимых решений, но в подпись не входит |
| `INV-TIME-01` | Проверка канонической формы смотрит только хвост строки и принимает `+0300`, дробные секунды и мусор |

**Урок реестра.** Аудит показал пять сценариев ложной зелени у судьи:
удаление строки проходит, ссылка на несуществующий тест проходит,
писатель через синоним обходит перепись, один вызов канонизации
«оправдывает» второй небезопасный писатель, вызов системы контроля версий
в другой форме кавычек невидим. Из тринадцати «закрытых» записей
настоящую перепись имели только две.

Вывод для процесса: перепись по ИМЕНАМ вызовов слабее, чем проверка
СМЫСЛА. Следующий шаг — дифференциальный оракул: если прайсер объявил
полноту, полный перебор без ограничений не должен находить ни одной
улучшающей колонки. Такая проверка закрывает класс, а не перечень мест.

## Объявленные отсрочки

Каждая строка — сознательное решение не закрывать инвариант в этом
раунде, с причиной. Отсрочка без причины и раунда считается дефектом
самого реестра.

| Идентификатор | Формулировка | Причина отсрочки | Раунд |
|---|---|---|---|
| `INV-RULES-03` | Решатель на Kotlin отказывает вместо перехода на встроенные умолчания при заданной переменной окружения | Меняет эталон Kotlin и требует пересборки и развёртывания архива решателя в одном окне с кодом | R10 |
| `INV-PRICE-05` | Пяти эвристическим пределам предъявлено доказуемое условие ненаступления, статус «сертифицировано» достижим | Исследовательская работа (доказать завершаемость перебора без пределов) плюс решение о вычислительном бюджете | R10 |
| `INV-CAND-05` | Держатель секрета подтверждения находится ВНЕ машины | Сейчас секрет на том же хосте: автономное продвижение невозможно, но полной независимости гейта это не даёт | Пакет C |
| `INV-CAND-06` | Производственный посредник: батарея Stage/Prod исполняется не под подписантом | Разделение построено на Test-контуре; у Stage/Prod запись `cosmos-agent` намеренно лишена прав (DENY) — им нужен собственный посредник с отдельной записью | Пакет C |
| `INV-UI-01` | Браузерная матрица проверок (роли, клавиатура, коды отказа, мобильная раскладка) | Отложена осознанно: большой самостоятельный объём, не блокирует ни расчёт, ни развёртывание кода | Отдельный шаг |

---

## Как читать статус

- **закрыт** — есть негативный тест на альтернативный обход, а где
  возможно — перепись по исходным текстам.
- **объявленная отсрочка** — механизма нет, и это сказано вслух с
  причиной и раундом.
- Понижение статуса допускается только вместе с указанием раунда и
  причины: молчаливое исчезновение строки — дефект реестра.
