# Контракт закона паритета — `catalog/tools/parity.py` (дорожка А, 08.09.2026)

Файл передачи дорожек Б («Читатель и служба») и В («Конвейер и документы»).
Основание — `final-report/KATALOG-PARITET-VREMENNYJ-KATALOG-PROEKT-CLD.md`
(решения 0.1, 0.3–0.7, 0.9, 0.13, §1.4, §2.1, §2.4, §3.2).

Всё, что названо здесь, — договор: дорожки Б и В вправе строить на нём
стенды и заглушки до готовности движка. Изменение договора — словами в
этом файле и извещением обеим дорожкам.

---

## 1. Команда

    python catalog/tools/parity.py [--catalog <папка>] [--bindings <файл>]
                                   [--with-candidates] [--candidate <файл>]
                                   [--json <файл>] [--quiet]

| Ключ | Умолчание | Смысл |
|---|---|---|
| `--catalog <папка>` | `catalog/` рядом с инструментом | судимая папка: живой каталог, временный каталог попытки CR, папка выпуска `catalog/releases/<v>/` |
| `--bindings <файл>` | `vmp/bindings.json` дерева | файл привязок судимого каталога; файла нет — L19/L22 отвечают `skipped` словами |
| `--with-candidates` | выключено | судить «живой ⊕ все кандидаты `<папка>/candidates/**`»: движок материализует временный каталог сам и строит привязки В ПАМЯТИ (`vmp.bindings_build`), `--bindings` при этом НЕ читается — переданный вместе с ним путь называется словами в `catalog.bindings`. Отказ генератора на таком каталоге — свойство кандидата: L19/L22 краснеют его словами, а не пропускаются |
| `--candidate <файл>` (можно повторять) | нет | файл кандидата, УЖЕ положенного в судимую папку зовущим (временный каталог попытки CR): движок ничего не материализует, берёт из файла адрес записи и номер заявки — этим правило L21 «запись ≡ заявке» получает предмет суда, а `--bindings` читается, потому что привязки собраны по той же папке |
| `--json <файл>` | нет | записать вердикт файлом (тот же объект, что печатается) — это и есть блок `gates.parity` свидетельства CR |
| `--quiet` | выключено | не печатать построчный отчёт (для службы); `--json` пишется всегда |

**Коды возврата.** `0` — красных правил нет (`skipped` не красит);
`1` — есть хотя бы одно красное правило; `2` — не судилось (папка,
Kotlin-таблицы или посев не читаются, аргументы негодны). Кода `3` у
инструмента паритета нет: он ничего не пишет, кроме `--json`.

**Раскладка дерева.** Корень продуктов ищет `build_from_code.repo_root`:
родитель папки каталога, затем текущая папка процесса (её ставит зовущий:
служба зовёт с `cwd=repo_of(catalog_dir)`), затем корень git. Раскладка
`<репозиторий>/data/catalog`, которую поддерживает служба каталога,
работает поэтому и у закона: прежде инструмент брал только родителя папки
каталога и падал на импорте `template_catalog` до разбора аргументов.

**Окружение.** Движок читает ТОЛЬКО названную папку; переменную
`CREW_TEMPLATE_CATALOG` он не снимает и не ставит — она свидетель правила
L10 («двери читают судимую папку»). Зовущая сторона (служба, конвейер CR)
обязана отдать подпроцессу `CREW_TEMPLATE_CATALOG=<судимая папка>` и
`PYTHONUTF8=1`, иначе L10 отвечает `skipped` словами «двери читают другой
каталог», а не красным.

---

## 2. Форма вердикта (`--json` и `judge()`)

```json
{
  "rc": 0,
  "ok": true,
  "catalog": {"version": "0.2.1-draft.1", "fingerprint": "<64 hex>",
              "path": "<абсолютный путь судимой папки>",
              "bindings": "<путь файла привязок | «в памяти по судимому каталогу»>",
              "source_fingerprint": "<64 hex живого, только если движок сам материализовал кандидатов>",
              "with_candidates": ["families/duty_limits"]},
  "growth_judged": {"knobs": "L05/L18", "metrics": "L08/L22",
                    "family_to_constraint": "L14/L22", "history_families": "L15",
                    "history_units": "L05", "logic_forms": "L22 (привязки)",
                    "constants": "только гейт привязок", "sit_knobs": "L05",
                    "sit_param": "L05"},
  "counts": {"rules": 23, "green": 21, "red": 0, "skipped": 2, "warning": 1},
  "rules": {
    "L05": {"ok": true, "words": null,
            "numbers": {"params": 46, "cdpm": 44, "knobs": 22, "history": 4,
                        "unbound": 17, "solver_only": 0}},
    "L08b": {"ok": null, "skipped": "CDPM ещё не знает метрику …",
             "warning": true},
    "L21": {"ok": true, "beyond_request": ["params: +max_night_sits (count, max)"]},
    "L23": {"ok": null, "skipped": "папка без tools/ — свежесть носителей судит дерево"}
  },
  "words": null
}
```

* `rules` — объект по идентификатору правила; ключи одного правила:
  `ok` (`true` | `false` | `null`), `words` (слова красного; у зелёного
  ключа нет), `numbers` (объект чисел правила; может отсутствовать),
  `skipped` (слова, только при `ok: null`), `warning` (`true` только у
  `skipped`, который требует подтверждения на выпуске — сегодня это
  L08b), `beyond_request` (только L21: поля и параметры записи СВЕРХ
  заявки — слова, а не отказ; ключ есть и у зелёного L21).
* `counts` — `{rules, green, red, skipped, warning}`; `counts.warning`
  считает правила с признаком `warning`.
* `catalog.path` — абсолютный путь судимой папки; `catalog.bindings`
  говорит, чем судились строки (путь файла либо «в памяти»);
  `with_candidates` появляется, когда кандидаты назывались (`--candidate`
  либо найдены `--with-candidates`), а `source_fingerprint` — только
  когда движок САМ материализовал их поверх живой папки (при
  `--candidate` судимая папка уже собрана зовущим, и второго отпечатка
  у неё нет).
* `ok` вердикта — `true`, когда красных нет. `skipped` вердикт не красит.
* `words` вердикта — склейка слов всех красных правил через `; `
  (у зелёного — `null`). Это единственная строка, которую конвейеру
  достаточно показать человеку.
* `rc` дублирует код возврата команды; при `rc: 2` есть только
  `rc`, `ok: false`, `words` и `catalog.path`.
* Порядок ключей в `rules` — порядок правил L01…L24 (без L12).

**Правила и их идентификаторы:** `L01`, `L02`, `L04`–`L11`, `L13`–`L23`
в вердикте команды (L12 снят частью 7-А). L08 и L08b — два разных
правила; `L03` отдельным ключом НЕ приходит: атрибуты базовых записей и
спецификацию базовых критериев несёт одно правило `L02` (функция
`rule_l02_l03`), и потребителю вердикта искать `L03` не в чем.
L24 живёт ТОЛЬКО в pytest-файле (два читателя Kotlin) и в вердикте
команды не появляется.

---

## 3. Движок в процессе

```python
from parity import judge, load_witnesses     # catalog/tools/parity.py
verdict = judge(catalog_dir, bindings=<путь|None>, with_candidates=False,
                candidate_files=None,        # список файлов кандидатов
                witnesses=None)              # witnesses=None → снимает с кода
verdict.ok        # bool
verdict.rc        # 0 | 1 | 2
verdict.as_dict() # объект §2
```

`witnesses` — словарь свидетелей ДАННЫМИ (`kotlin`, `snapshot`, `hard`,
`data_checks`, `certify`, `cdpm`, `kinds`, `bindings_seed`); подмена свидетеля — путь стендов
(«решатель вырос на ручку»), а не ключ команды.

---

## 4. Блок `base` посева (`catalog/tools/catalog_seed.json`)

Жёсткий блок владельца: `--refresh` его НЕ переписывает, правится только
словом владельца. Форма:

```json
"base": {
  "purpose": "снимок шага 1 …",
  "code": "754c272613f075c3978a0d242b35e3a9185e4eb2",
  "version": "0.1.0-draft.3",
  "fingerprint": "b357cd33…",
  "legacy_registry_rev": "bc78f37804ec",
  "snapshot": "tests/fixtures/registry_step1.json",
  "families": ["balanced_workload", "…"],          // 16 семей кода 754c272
  "criteria": ["min_person_days", "rank_normalized_cost"],
  "param_to_family": {"max_duty_duration": "duty_limits", "…": "…"},
  "executor_kinds": {"duty_limits": ["kotlin_constraint", "…"]},
  "world_forms": ["cost_pair", "schedule"],
  "synthetic_codes": ["employee_pattern", "fleet_types", "stations"],
  "history_marker": {"date": "2026-09-07", "kind": "изменение",
                     "by": "сессия COSMOS", "note": "перенесена в каталог …",
                     "ref": null}
}
```

Прежние ключи посева `step4`, `history_step4`/`history_draft2` записей,
`executor_kinds_step4`, `param_families_base`, `stand_worlds` СНЯТЫ: их
правду теперь несут блок `base` и проекция каталога. Ключ
`param_families` ОСТАЛСЯ, но сменил смысл: теперь это проекция ВСЕЙ карты
«имя → семья» (база ⊕ прибавка), а не одна прибавка. Прибавился ключ
`dictionaries_extra` — словари, которых инструмент не выводит из кода:
проекция кладёт туда КАЖДЫЙ ключ словарей каталога вне перечня
`TOOL_DICTIONARY_KEYS` (и вне `READER_DICTIONARY_KEYS` — их дописывает
читатель). Служба вправе завести свой словарь (`document_classes`
появляется её первой сборкой), и пересборка его не потеряет; на живом
каталоге раздел сегодня пуст. Записи посева получили поля `status`, `acceptance`, `note`.

Остальные жёсткие блоки посева (тоже вне `--refresh`):
`cdpm_dictionaries.params` (44 имени с единицами), `.vocabularies`,
`.zones` (поля происхождения 7-А/7-Б рядом), `params_relation_set`.
Всё прочее в посеве — машинная проекция каталога.

---

## 5. Дельта `catalog/transition/step4_additions.json`

Форма 1, ключи прежние (их читают `tests/vmp/test_template_registry_wrapper.py`,
`tests/test_catalog_drift_guard.py`, `tests/test_template_catalog_step4_rules.py`),
плюс два новых:

| Ключ | Что несёт |
|---|---|
| `form_version` | `1` |
| `version` | версия судимого каталога (из шапки) |
| `legacy_registry_rev` | старая 12-значная ревизия каталога |
| `base` | копия блока `base` посева (без `families`/`criteria`/`executor_kinds`/`world_forms`/`synthetic_codes`/`history_marker` — пять полей тождества: `code`, `version`, `fingerprint`, `legacy_registry_rev`, `snapshot`) |
| `params_to_families` | `{имя: {family, unit, relation, qualifier, was: {source, relation}}}`; `was.source` ∈ `словарь CDPM` \| `словарь решателя` \| `заявка <номер>` |
| `params_relation_set` | `{имя: {family, unit, relation, was: {relation, by}}}`; `was.by` ∈ `владелец` \| `заявка <номер>` |
| `executor_kinds_added` | `{запись: [виды]}` = виды записи − `base.executor_kinds` |
| `criteria_added` | критерии − `base.criteria` |
| `families_added` (новый) | `{семья: {class, title, kind, zone, nature, violation_unit}}` = семьи − `base.families` |
| `stand_tasks` | `{"families/<id>": [идентификаторы задач]}` по всем записям |
| `records_without_stand_tasks` | ключи записей без задач |
| `history_added` | хвост истории после `base.history_marker` (у записи вне `base` — вся история) |
| `dates` | отсортированные даты `history_added` |
| `dictionaries` | `world_forms_added`, `synthetic_codes_added`, `unbound_params_removed` |
| `gates` | копия `header.gates` |
| `executors_source` | `vmp/bindings.json` |
| `fingerprint` (новый) | отпечаток каталога, от которого дельта посчитана (L23 судит свежесть по нему) |

---

## 6. Инструмент извлечения — новые режимы

| Команда | Читает | Пишет | Код |
|---|---|---|---|
| `build_from_code.py --out <п>` | посев ⊕ код | шесть файлов, `families_order`, дельта, схема | `0`/`2`; `3` — `refuse_write` |
| `build_from_code.py --out <п> --check` | посев ⊕ код | ничего | `0` — «расхождений 0», `1` — расхождения |
| `build_from_code.py --out <п> --check --from-catalog` | `curated_of(<п>)` ⊕ код | ничего | то же; судит ЛЮБУЮ папку из её же курируемых полей |
| `build_from_code.py --out <п> --refresh` | `<п>` ⊕ код ⊕ заявки `<п>` | ТОЛЬКО `tools/catalog_seed.json` и `transition/step4_additions.json` | `0`; `3` — папка без `tools/` либо под `releases/`; `2` — проекция не даёт отпечаток папки |
| `build_from_code.py --out <п> --refresh --check` | то же | ничего | `0` — «расхождений 0 — посев и дельта свежи», `1` — отстали (в словах названа команда `--refresh`) |

`versions.json` и `requests.json` в сравнение `--check` не входят (как
сегодня); дельта сравнивается, только если её `fingerprint` равен
отпечатку папки (иначе о ней говорит L23). `--refresh` разделов
отпечатка не касается и обходом `refuse_write` не является — у него свой
сторож `refuse_refresh` с тем же кодом 3. Аргументы `--version`,
`--released-at`, `--released-by`, `--note` без явного значения берутся из
шапки папки назначения.

Движок паритета зовёт инструмент В ПРОЦЕССЕ двумя точками входа —
`check_from_catalog(out, kt=<таблицы решателя>)` (правило L18) и
`refreshed(out, kt=…)` (L23: посев ≡ `curated_of`, дельта ≡
`compute_delta`, отпечаток дельты ≡ отпечатку папки). `refreshed`
каталог НЕ собирает: свежесть носителей и паритет каталога с кодом —
разные суждения, и полная сборка делала бы `--refresh --check` (а с ним
L23 и `pytest -q` клона) красным по построению на ветке класса «рост
таблиц решателя», где живой каталог законно отстаёт от выросшего
решателя. Паритет с кодом там судят `--check --from-catalog` (L18) и
правила L05/L05b;
`build(..., kt=…)` принимает таблицы решателя данными, поэтому стенд
«решатель вырос на ручку» судит воспроизводимость по тому же свидетелю.

---

## 7. Что нужно дорожке Б

* Заглушка `tools/parity.py` в tmp-дереве стендов службы: печатает
  вердикт §2 и возвращает названный код. Служба обязана отличать
  `rc: 0` (зелёный), `rc: 1` (`parity-red`, 412), `rc: 2` и любой
  `skipped` с `warning: true` («не судился» / предупреждение →
  `confirm_parity`).
* Читать поля: `ok`, `rc`, `words`, `counts.warning`, и для чек-листа —
  `catalog.version`, `catalog.fingerprint`.
* Строки `next`: первой — `python catalog/tools/build_from_code.py --refresh --out catalog`,
  последней — `python catalog/tools/parity.py`.

## 8. Что нужно дорожке В

* argv гейта (§2.1 проекта):

      [py, str(repo / "catalog" / "tools" / "parity.py"),
       "--catalog", str(tmp / "catalog"),
       "--candidate", str(cand_path),
       "--bindings", str(tmp / "bindings.json"),
       "--json", str(tmp / "parity.json")]

  Ключ `--candidate` обязателен: судимая папка уже собрана
  материализацией, и без него движок не знает, какая запись в ней —
  кандидат. Правило L21 «запись ≡ заявке» отвечало бы «кандидатов рядом
  с каталогом нет», и единственный независимый якорь владельца
  оставался бы непросуженным при зелёном вердикте. Пропуск L21 конвейер
  КРАСИТ (`_parity_verdict`): «не судилось» — не «сошлось».

* `gates.parity` = ПРОЧИТАННЫЙ `parity.json` целиком (копия, не пересказ).
* Зелёный конвейера — `(report.get("parity") or {}).get("ok") is True`.
* Команда самопроверки агента в строке CR —
  `python catalog/tools/parity.py --with-candidates`.
* Генератор привязок (`vmp/bindings_build.py`, решение 0.10) пропускает
  СЛОВАМИ строку посева, чей параметр `kotlin_knob.param` называет не
  живой каталог, а файл-кандидат `catalog/candidates/<раздел>/<id>.json`
  рядом, и адрес записи, которую несёт кандидат. Слова доступны
  отдельно: `vmp.bindings_build.awaiting_words(catalog, seed)` →
  `["families/duty_limits: max_night_sits", …]`; они же печатаются
  строкой «ожидают записи кандидата: …» после записи файла. Имя, которого
  не знает и решатель, по-прежнему отказ (слова о
  `RunThresholds.KNOBS`), запись без кандидата — тоже отказ. Поэтому
  `chain_live` у правки класса К-табл «изменена» зелен ПО СУЩЕСТВУ.

