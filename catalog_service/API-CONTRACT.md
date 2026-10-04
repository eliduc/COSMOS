# Контракт службы каталога шаблонов (`catalog_service`)

Версия контракта 1 (шаг 5 каталога, 08.09.2026). Источник истины —
`final-report/KATALOG-SHAG-5-PROEKT-CLD.md` (§1–§7). Этот файл —
машинно-читаемая часть договора между службой (дорожка A) и интерфейсом
(дорожка B) и ссылками продуктов (дорожка D): имена ручек, формы тел,
машинные коды и тексты отказов, переменные, адреса экранов. Ни одна
сторона не выдумывает других имён.

Служба ничего не разбирает сама: чтение каталога — `template_catalog`,
запись — `template_catalog.writer`, «что изменилось» —
`template_catalog.diff`, состояния исполнителей —
`template_catalog.bindings`. Импортов `cdpm`, `vmp`, `webapp`,
`_data_paths` у службы нет (гейт T2).

---

## 1. Запуск, режимы, роли

```
python -m catalog_service --port 8105 [--host 127.0.0.1]
                          [--actor owner|analyst] [--read-only]
                          [--catalog <папка>] [--work-dir <папка>]

python -m catalog_service import-requests --inbox <папка>
                          [--api-url http://127.0.0.1:8105] [--token …] [--reply]
```

| Переменная / ключ | Смысл | Умолчание |
|---|---|---|
| `CREW_TEMPLATE_CATALOG` / `--catalog` | папка каталога | `template_catalog.DEFAULT_CATALOG_DIR` |
| `CATALOG_WORK_DIR` / `--work-dir` | черновики и журнал действий (только режим правки) | `<корень данных>/catalog-work` (`CREW_DATA_DIR`, иначе `<репозиторий>-data` рядом) |
| `CATALOG_ACTOR` / `--actor` | `owner` → «владелец», `analyst` → «аналитик»; не задана → просмотр | не задана |
| `CATALOG_READ_ONLY=1` / `--read-only` | режим просмотра принудительно | выключено |
| `CREW_COSMOS_BINDINGS` | путь `vmp/bindings.json` | `<репозиторий>/vmp/bindings.json` |
| `CREW_CDPM_BINDINGS` | путь `cdpm/bindings.json` | `<репозиторий>/cdpm/bindings.json` |
| `CATALOG_MARKERS_FILE` | перечень маркеров заказчика | `tools.customer_markers.MARKERS_FILE` |
| `CREW_CATALOG_URL` | базовый адрес службы для ссылок из продуктов (службой не читается) | не задан → ссылок нет |
| `CATALOG_MACHINE_TOKEN` | токен машинного канала заявок (§8): пусто — канал выключен, `POST /api/requests` от продукта → 403 `machine-channel-off` | не задана → канал выключен |

Ключ командной строки сильнее переменной среды. `--actor` вне таблицы —
отказ старта словами: `роль службы — owner либо analyst; дано '<значение>'`.
`--read-only` при `--actor owner` — режим просмотра.

Режимы:

* **просмотр** (`mode == "view"`) — все ручки чтения; любая ручка записи →
  403 `read-only`; ни замка, ни журнала, ни рабочей папки служба не создаёт;
* **правка** (`mode == "edit"`) — по роли (таблица §3 контракта).

В режиме правки при старте берётся замок папки каталога
`<temp>/cosmos-catalog-<sha256(путь)[:12]>.lock`; занят — отказ старта
словами с именем файла замка.

---

## 2. Общая форма ответа и отказа

Все ручки — JSON, префикс `/api`. Отказ — HTTP-код и тело

```json
{"detail": {"code": "<машинный код>", "words": "<словами по-русски>",
            "problems": [{"where": "...", "code": "X01",
                          "message": "...", "severity": "error"}],
            "extra": {"...": "..."}}}
```

`problems` и `extra` присутствуют не у всех отказов. Интерфейс показывает
`detail.words`; перечень `detail.problems` — компонентом `ProblemList`
(адрес → код → слова). Абсолютных путей наружу нет: имена файлов — от
папки каталога; полный текст исключения — в журнале процесса
(`catalog-server.log`).

### 2.1. Перечень машинных кодов

| HTTP | Код | Когда |
|---|---|---|
| 400 | `bad-form` | тело запроса не по форме |
| 400 | `submitter-not-allowed` | `submitted_by` продукта с экрана: «подать от имени продукта может только продукт» |
| 400 | `bad-filter` | значение фильтра вне словаря: «зона X вне словаря zones: BOTH, PAIRING, ROSTERING» |
| 403 | `read-only` | «служба каталога в режиме просмотра: правки, заявки, сборка и выпуск — на рабочем месте владельца (запуск с --actor owner)» |
| 403 | `owner-only` | «заявки принимает, записи снимает и версии выпускает только владелец (слово 06.09); служба запущена с ролью «аналитик»» |
| 404 | `unknown-record` | записи нет (текст `UnknownRecord` с перечнем известных) |
| 404 | `unknown-key` | ключа словаря нет |
| 404 | `unknown-request` | заявки нет |
| 404 | `unknown-version` | «версии 0.9.0 нет: текущая 0.2.0-draft.1, папки releases/0.9.0 нет»; номер не по образцу semver — «версии '..' нет: это не номер вида X.Y.Z[-draft.N]» (в путь `releases/<строка>` строка пользователя не подставляется) |
| 404 | `no-draft` | черновика нет |
| 409 | `bad-transition` | незаконный переход заявки |
| 409 | `drafts-pending` | «сначала соберите или отмените черновики: записей 3, словарей 1» |
| 409 | `nothing-to-assemble` | «черновики не меняют канон» |
| 409 | `version-not-newer` | «0.2.0-draft.1 не новее текущей 0.2.0-draft.1; предложено 0.2.0-draft.2» |
| 409 | `version-downgrade` | загрузка понизила бы версию; повтор с `?force=1` |
| 409 | `release-exists` | папка `releases/<v>` уже есть |
| 409 | `write-busy` | другая запись держит замок процесса дольше 10 с |
| 409 | `id-taken-by-request` | «на id x есть открытая заявка req-0003 — правьте её черновик» |
| 412 | `bindings-stop` | после записи дверь продукта остановится по B01; `confirm_stop: true` — осознанное продолжение |
| 412 | `markers-missing` | перечня маркеров заказчика нет (только выпуск — отказ). Причина называется словами и различается: «не найден: `<имя>`», «не читается: `<имя>` (`<тип ошибки>`)», «пуст: `<имя>` — ни одной проверки», «линт маркеров tools.customer_markers недоступен» |
| 412 | `git-dirty` | дерево `catalog/`, `template_catalog/` не чисто |
| 412 | `no-git` | `git rev-parse HEAD` не дал 40 hex |
| 412 | `executor-not-present` | у ЭТОЙ записи нет ячейки СВИДЕТЕЛЬСТВА «есть» по виду ни у одного продукта: «у записи families/x не подтверждена поддержка видов … ни одним продуктом: свидетельство поддержки на каталоге 0.5.0 5534196f8248 — «есть» нет …»; `extra.kinds`, `extra.record`, `extra.support` (ЯЧЕЙКИ СВИДЕТЕЛЬСТВА записи — то, чем судили). Вид, написанный у ДРУГОЙ записи, исполнимости этой не даёт; строка привязок «есть» без стенда по виду и файл привязок иного каталога дают «не проверено» и перевода не открывают (K06) |
| 422 | `invalid-draft` | черновик даёт НОВЫЕ ошибки каталога; `problems` |
| 422 | `invalid-catalog` | собранный каталог не проходит `validate()`; `problems` |
| 422 | `invalid-request` | заявка не по правилам R01–R03 / A-правилам; `problems` |
| 422 | `invalid-single-file` | единый файл выгрузки не читается |
| 422 | `fingerprint-mismatch` | отпечаток шапки файла не равен вычисленному |
| 422 | `version-too-low` | версия выпуска ниже подсказки/журнала/черновика |
| 422 | `words-required` | «решение владельца без слов не записывается» |
| 422 | `bad-cr-key` | ключ CR не по образцу `^cr[0-9a-f]{8}$` |
| 422 | `closed-list` | служебный перечень: «изменение — правка кода читателя» |
| 503 | `catalog-unavailable` | любой `CatalogError` читателя: «каталог шаблонов недоступен: …»; сюда же — срез читателя без закона номера (`template_catalog/requests.py`) или без правил кандидата (`template_catalog/candidates.py`): служба номера не выдумывает |
| 503 | `ui-not-built` | `catalog-ui/dist/index.html` нет |

Коды шага 6 (машинный канал, закон номера, кандидат, выпуск):

| HTTP | Код | Когда |
|---|---|---|
| 400 | `submitter-not-allowed` | заголовок `X-Catalog-Product` вне словаря `products` каталога; либо `submitted_by` продукта с экрана |
| 401 | `bad-token` | `X-Catalog-Token` не равен `CATALOG_MACHINE_TOKEN` службы |
| 403 | `machine-channel-off` | «машинный канал заявок выключен: задайте CATALOG_MACHINE_TOKEN на рабочем месте владельца …» |
| 409 | `request-exists` | столкновение содержания по записи: тот же номер с иным каноном черновика или набора видов («заявка req-… уже подана из run-0088 иным черновиком; дубль не заводится») либо ОТКРЫТАЯ заявка по той же записи иным черновиком («по записи families/x уже есть открытая заявка req-… (состояние «подана») иным черновиком»); `extra.existing`, `extra.state`, `extra.ref`. С волны 17б код один на оба случая — отдельный `duplicate-request` снят (П0 §4.1, «0-бис») |
| 409 | `request-closed` | заявка с этим номером отклонена владельцем: «… повторная подача — только после пересмотра решения на экране «Заявки»»; `extra.existing`, `extra.state`, `extra.decision` (`words`, `version`, `at`) |
| 409 | `candidate-pending` | предпроверка выпуска 9: «кандидат families/x из CR cr… не импортирован или не удалён … удаление — в коммит» |
| 412 | `executors-incomplete` | предпроверка выпуска 10: у записи из заявки «есть» вид без строки «есть» / «заявлен» с ключом CR / «нет» с заметкой, либо провал стенда |
| 412 | `parity-red` | предпроверка выпуска 12: закон паритета (`catalog/tools/parity.py`) вернул красные правила — «паритет каталога с кодом COSMOS красен: `<слова вердикта>`; версию, противоречащую таблицам решателя, выпускать нельзя — подтверждением это не снимается»; `extra.parity` |
| 412 | `parity-unconfirmed` | предпроверка выпуска 12 не судилась (закона рядом нет, код 2, предел 120 с, вердикт не прочитан) либо судья предупредил (`skipped` с признаком `warning`, сегодня L08b «CDPM ещё не знает метрику …»): «… продолжить осознанно — полем `confirm_parity: true`»; `extra.parity` |
| 422 | `bad-request-id` | номер заявки на вид не равен `request_id_of(kind_request_key(…))`; либо `key` формы не 64 hex; `extra.expected` |
| 404 | `no-candidate` | файла `catalog/candidates/<раздел>/<id>.json` нет |
| 422 | `candidate-mismatch` | кандидат не по схеме `candidate.schema.json` либо назван чужой заявкой, чужим ключом CR, чужой записью |

### 2.2. Права по ролям

| Операция | просмотр | аналитик | владелец |
|---|---|---|---|
| Все ручки чтения, выгрузка, сравнение версий, `/api/validate`, журнал | да | да | да |
| `PUT /api/drafts/…` без смены `status` | нет | да | да |
| `PUT /api/drafts/…` со сменой `status` | нет | нет | да |
| `DELETE /api/drafts/…` | нет | да | да |
| `POST /api/requests` | нет | да | да |
| `POST /api/support/refresh` | нет | да | да |
| `POST /api/versions/assemble` | нет | да | да |
| `POST /api/requests/{id}/decide`, `PUT …/cr_key`, `POST …/adopt` | нет | нет | да |
| `POST /api/requests` машинным каналом (§8) | нет (403 `read-only`) | да | да |
| `POST /api/requests/dry-run` | да | да | да |
| `POST /api/versions/release`, `POST /api/import` | нет | нет | да |

Интерфейс показывает недоступные кнопки НЕАКТИВНЫМИ с причиной в
подсказке (слова `read-only` / `owner-only`), не скрывает их.

---

## 3. Точки наблюдения и раздача интерфейса

| Ручка | Ответ |
|---|---|
| `GET /` | 302 на `/ui/`; без сборки — 503 `ui-not-built` |
| `GET /ui/…` | статические файлы `catalog-ui/dist`; `Cache-Control: no-cache` только у `index.html` |
| `GET /healthz` | 200 `{"status": "ok"}` — ничего не читает с диска |
| `GET /readyz` | 200 `{"ready": true, "checks": {"catalog": true, "ui_built": true, "work_dir": true}, "catalog": {"version", "fingerprint", "errors": 0, "warnings": N}, "mode": "view"\|"edit"}`; неготовность — 503 с `"ready": false` и `"reason"` |
| `GET /versionz` | 200 `{"service": "catalog_service", "service_version", "reader_version", "code": "<git rev-parse --short HEAD>"\|null, "catalog": {"version", "fingerprint", "is_draft"}, "mode", "actor"}` |

`checks.work_dir` в режиме просмотра — `true` без обращения к диску.

---

## 4. Ручки чтения

### `GET /api/session`
```json
{"mode": "view"|"edit", "actor": "владелец"|"аналитик"|null,
 "read_only": false,
 "catalog": {"version": "0.2.0-draft.1", "fingerprint": "<64 hex>",
             "is_draft": true, "dir_name": "catalog"},
 "drafts": {"families": 0, "criteria": 0, "dictionaries": 0},
 "requests_open": 0, "requests_in_work": 0, "machine_channel": false,
 "document_classes": ["положение о рабочем времени и времени отдыха", "…"],
 "rights": {"draft-save": {"allowed": true, "words": null},
            "release": {"allowed": false, "words": "…owner-only словами"}}}
```
`rights` — карта десяти операций записи: интерфейс гасит недоступную
кнопку и показывает `words` подсказкой, а не прячет её.

`requests_in_work` — заявок в состоянии «исполнитель заявлен» (по ним
идёт CR продукта). `machine_channel` — включён ли машинный канал заявок
(§8.1). `document_classes` — ОДИН перечень классов документа: словарь
каталога, когда он заведён, иначе перечень читателя
(`template_catalog.requests.DOCUMENT_CLASSES`, решение 0.14). Экран
своей копии семи значений не держит — копия разошлась бы с читателем и с
формой CDPM.

### `GET /api/catalog`
Шапка каталога целиком плюс
```json
{"is_draft": true,
 "counts": {"families": 16, "criteria": 8, "dictionaries_keys": 38,
            "requests_open": 0, "versions": 0, "drafts": 0},
 "problems": {"errors": 0, "warnings": 2},
 "releases_ok": true}
```
`dictionaries_keys` — ключи ФАЙЛА словарей, без вычисляемого читателем
`area_param_names` (он назван отдельно в `GET /api/dictionaries.derived`).
Отказ: 503 `catalog-unavailable`.

### `GET /api/validate`
```json
{"problems": [Problem…], "errors": 0, "warnings": 2,
 "releases": [{"version": "0.2.0", "ok": true, "words": "…"}]}
```
Судится ЖИВОЙ каталог: черновики службы не подмешиваются. `releases` —
гейт папок выпусков (§5 п.3 проекта): у каждой записи журнала есть папка,
она читается, её отпечаток и версия равны записи журнала.

### `GET /api/records`
Параметры: `section=families|criteria` (умолчание `families`),
`status=есть|снята|all` (умолчание `есть`), `kind`, `zone`, `class`,
`state=есть|заявлен|нет|не-объявлено`, `q`, `drafts=1`, `requests=1`.
Состояние в адресе пишется через дефис (`не-объявлено`), внутри службы —
словом с пробелом; правило одно на оба экрана. Фильтр `state` у списка
записей судит ХУДШЕЕ состояние записи: запись с неисполненным видом
ищется именно по нему.

```json
{"items": [{"section": "families", "id": "duty_limits", "title": "…",
            "class": "core", "kind": "HARD", "zone": "BOTH",
            "nature": "schedule", "violation_unit": "duty",
            "status": "есть", "params": 4, "stand_tasks": 2,
            "executor_summary": {"есть": 3, "заявлен": 0, "нет": 1,
                                 "не объявлено": 0},
            "draft": "изменена"|"новая"|"снята"|null,
            "request_id": null}],
 "total": 16}
```
`q` — подстрока по `id`, `title`, `subject`, `subject_words`,
`params[].name` (без регистра). `requests=1` подмешивает черновики
открытых заявок как записи со `status: "заявлен"` (и для семей, и для
критериев). `drafts=1` подмешивает черновики службы.
Отказ: 400 `bad-filter`, 503.

Поле `stand_tasks` строки списка — ЧИСЛО стендовых задач записи, а не
счётчики исходов: исходы (`passed`/`failed`/`skipped`) приходят из
привязок продуктов и живут в карточке записи (`stand`) и на экране
«Исполнители по продуктам». Колонка «стенд» списка показывает это число
(отступление от §4.1 проекта, где колонка названа счётчиками исходов;
счётчики по всем записям сразу — работа шага 5б).

### `GET /api/records/{section}/{id}`
Параметр `?version=<semver>` — показать запись из `catalog/releases/<v>/`
(или из текущей папки при равной версии).
```json
{"record": {…},
 "draft": {…файл черновика…}|null,
 "changes": [Change…],
 "in_catalog": true,
 "executors": {"<вид>": {"<продукт>": {"state","file","symbol","note","cr_key"}}},
 "stand": {"passed": 2, "failed": 0, "skipped": 1, "rows": [stand_verified…]},
 "request": {…заявка на запись…}|null,
 "problems": [Problem по адресу записи],
 "shown": {"version": "0.2.0-draft.1", "fingerprint": "<64 hex>"},
 "pinned_words": "продукт закреплён на 0.1.0-draft.3 · b357cd33a706, показана 0.2.0-draft.1"|null,
 "pinned_release_ok": false}
```
`pinned_words` строится, когда запрос принёс `?fingerprint=` продукта,
отличный от показанного; `pinned_release_ok` — откроется ли карточка
`#/versions/<версия из ?version=>` (это текущая версия каталога либо есть
папка выпуска), и только тогда экран ставит рядом ссылку на неё.

**Черновик новой записи.** Если записи в каталоге ещё нет, но на рабочем
месте есть её черновик, карточка ОТКРЫВАЕТСЯ: `record` — запись из
черновика, `in_catalog: false`, `changes` — одна строка `добавлена`,
`executors`/`stand` пусты. Так поток «Новая запись» живёт после первого
сохранения (иначе отменить такой черновик и дойти до него по ссылке
списка было бы нечем). 404 `unknown-record` — когда нет ни записи, ни
черновика, а также всегда при `?version=` (в выпуске этой записи нет).
Отказы: 404 `unknown-record`, 404 `unknown-version`, 503.

### `GET /api/drafts`
```json
{"items": [{"section": "families", "id": "duty_limits",
            "kind": "изменена"|"новая"|"снята"|"словарь",
            "key": "violation_units",
            "saved_at": "2026-09-08T12:00:00+03:00", "by": "владелец",
            "note": "…", "request_id": null, "changes": [Change…]}],
 "suggested": {"level": "minor", "version": "0.2.0-draft.2",
               "base": "0.2.0"|null},
 "bindings_stop": {"code": "bindings-stop", "words": "…B01 словами…",
                   "extra": {"product": "COSMOS", "catalog_version": "0.2.0",
                             "catalog_fingerprint": "<64 hex>"}}|null}
```
Черновиков нет (в том числе всегда в режиме просмотра) — `items: []`,
`suggested: null`, `bindings_stop: null`: собирать нечего, и подсказывать
номер не о чем. `bindings_stop` — сухой прогон предпроверки B01 для
подсказанного номера: диалог «Собрать черновик» показывает это
предупреждение с флажком «продолжить осознанно» ДО кнопки (§4.5
проекта), а отказ 412 при нажатии остаётся второй линией. Слова — те же,
что у отказа: закон один.

### `GET /api/dictionaries` / `GET /api/dictionaries/{key}`
```json
{"keys": [{"key": "violation_units", "kind": "list"|"object"|"text"|"order",
           "size": 6, "draft": false, "usages": 12}],
 "derived": ["area_param_names"]}
```
```json
{"key": "violation_units", "kind": "list", "value": [...], "draft": false,
 "usages": [{"section": "families", "id": "duty_limits",
             "where": "violation_unit"}],
 "journal": [{"at": "…", "actor": "…", "op": "…", "words": "…"},
             {"version": "0.2.0", "kind": "изменена", "note": "…"}]}
```

### `GET /api/requests` / `GET /api/requests/{id}`
Фильтры: `state`, `submitted_by`, `source_kind`, `section`, `record_id`,
`cr_key`, `ref` (префикс `source.ref` до «#»), `q`.
Список — заявки по схеме `$defs/request` плюс `{"record_exists": bool,
"has_draft": bool, "cr_key_expected": "cr…", "candidate":
"candidates/families/x.json"|null, "candidate_evidence": {…}|null}`.
Карточка — заявка плюс `{"draft_problems": [Problem…], "record": {…}|null,
"has_service_draft": bool}`.

`cr_key_expected` — ключ CR по закону читателя (`cr_key_of(request_id)`):
им экран предзаполняет «Заявить исполнителя», а COSMOS сверяет строку
своей очереди. `candidate` — имя файла конвейера под папкой каталога
(путь снаружи не принимается ни здесь, ни при `adopt`);
`candidate_evidence` — блок `evidence` кандидата (гейты, ветка, коммит).

### `GET /api/executors`
Параметры: `product=COSMOS|CDPM|all`, `section`, `id`, `kind`,
`state=есть|заявлен|нет|не-объявлено` (то же правило дефиса).
```json
{"kinds": ["builder_method", "…"],
 "products": [{"product": "COSMOS", "file_name": "bindings.json",
               "found": true, "product_version": "1.18.5",
               "catalog_version": "0.2.0-draft.1",
               "catalog_fingerprint": "<64 hex>",
               "generated_at": "…", "generated_from": "<40 hex>",
               "freshness": "match"|"stale-draft"|"stop"|"missing",
               "freshness_words": "сошлось"|"черновик отстал"|"остановка сторожа"|"не объявлены",
               "problems": [Problem по адресу bindings[<продукт>]…]}],
 "rows": [{"section": "families", "id": "duty_limits", "title": "…",
           "cells": {"<вид>": {"best": "есть"|"заявлен"|"нет"|null,
                               "by": {"COSMOS": {"state","lang","file","symbol",
                                                 "note","cr_key","fields": {}}}}},
           "stand": {"COSMOS": [stand_verified…]}}],
 "totals": {"cells": 70, "есть": 60, "заявлен": 0, "нет": 8,
            "не объявлено": 2},
 "problems": [Problem B04 по адресу "families/<id>.executor_kinds[<вид>]"]}
```
Файла продукта нет — `found: false`, `freshness: "missing"`, не отказ.
Файл есть, но не читается — `problems` его карточки заполнены, строки
продукта в `rows` не показываются.

Замечания привязок служба считает ОДИН раз по всем прочитанным файлам
(`template_catalog.bindings.check_bindings(cat, cosmos, cdpm)`) и
раскладывает по адресу: `bindings[<продукт>]…` — в карточку продукта,
остальные — в общий перечень `problems` верхнего уровня. Так велит
правило шага 4 (§2.1 п.2 его проекта): полнота по видам записи (B04)
судится ЗОНАМИ ВСЕХ файлов сразу — счёт по одному файлу объявил бы чужую
зону несуществующей. Фильтр `product` сужает показ строк, но не
суждение: правда о полноте от фильтра экрана не зависит.

### `GET /api/records/{section}/{id}/support`
Свидетельство поддержки ЗАПИСИ — ПРЕДСТАВЛЕНИЕ файла
`catalog/support.json`, а не второй носитель: ручка показывает ячейки,
записанные пересчётом (ниже) или выпуском, и ничего не считает сама.
```json
{"record": "families/duty_series_limit", "computed": true,
 "computed_at": "…", "computed_by": "служба",
 "schema_version": "support/1",
 "catalog": {"version": "0.5.0", "fingerprint": "<64 hex>"},
 "stale": false,
 "cells": [{"section": "families", "record_id": "duty_series_limit",
            "kind": "kotlin_logic_form", "product": "COSMOS",
            "state": "есть"|"нет"|"не проверено",
            "binding": {"state","file","symbol","cr_key","note"}|null,
            "bindings_file": {"sha256","product_version","generated_from",
                              "catalog_version","catalog_fingerprint",
                              "generated_at"},
            "stand": {"tasks": ["st-dsl-1"], "passed": 1, "failed": 0,
                      "skipped": 0, "commit": "<40 hex>", "at": "…"}|null,
            "approved": {"by": "владелец", "at": "2026-09-12",
                         "how": "выпуск 0.6.0, предпроверка 10"}|null}],
 "why": null,
 "words": "свидетельство поддержки на каталоге 0.5.0 5534196f8248"}
```
Свидетельства нет — `computed: false`, `cells: []` и слова «пересчитайте
его»; это не отказ, но и не поддержка: для готовности «не проверено» =
не поддержано. `stale: true` — свидетельство к ЭТОМУ каталогу не
относится (иная версия либо иной отпечаток), `why` называет причину
словами: ячейки показаны, исполнимостью не считаются. Годность судит
ОДНА функция читателя `template_catalog.bindings.support_of` (версия
схемы, версия каталога, отпечаток) — её же зовут дверь COSMOS и CDPM.

Записи нет — `404 unknown-record`, как у остальных ручек записи. Файл
неизвестной версии схемы (`support/9`) — `503 catalog-unavailable` со
словами читателя «свидетельство неизвестной версии схемы …»: неизвестная
мажорная версия носителя — отказ, а не «годное свидетельство» (общий
закон 5 П0). Схема файла — `template_catalog/schemas/support.schema.json`
(у службы байт-в-байт копия), и читатель применяет её при каждом чтении.

Состояние ячейки — три слова и ничего больше: «есть» = строка «есть» в
привязках продукта И стенд по виду на этом отпечатке без провалов (≥ 1
`passed`, 0 `failed`); «нет» = строки нет, «нет» либо «заявлен»
(обещание исполнителем не делает); «не проверено» = строка «есть» без
подтверждения на этом отпечатке. Считает ячейку ОДНА функция читателя
`template_catalog.bindings.support_table` — её же зовёт CDPM, читая файл
`Catalog.support()`: второй формулы «исполнимо» в дереве нет.

### `POST /api/support/refresh` (аналитик+)
Пересчитать свидетельство по текущим привязкам и положить файлом
`catalog/support.json` (вне отпечатка, как `requests.json`: ни версия, ни
отпечаток каталога не меняются). Между выпусками `approved` у всех ячеек
пуст — утверждает владелец АКТОМ ВЫПУСКА: выпуск считает таблицу заново
по выпущенному телу, заполняет `approved` у подтверждённых ячеек и кладёт
неизменяемую копию в `releases/<версия>/support.json`.
```json
{"catalog": {"version": "0.5.0", "fingerprint": "<64 hex>"},
 "computed_at": "…", "computed_by": "служба",
 "schema_version": "support/1", "cells": 70, "file": "support.json",
 "totals": {"есть": 60, "нет": 8, "не проверено": 2}}
```
Отказы: 403 `read-only` (в режиме просмотра служба не пишет ни байта),
503 `catalog-unavailable`, 409 `write-busy`.

### `GET /api/versions` / `GET /api/versions/{v}`
```json
{"current": {…шапка…, "is_draft": true},
 "versions": [{…запись журнала…, "release_dir_ok": true,
               "release_fingerprint_ok": true}],
 "suggested": {"level": "minor", "version": "0.3.0", "changes": 7,
               "by_level": {"major": 1, "minor": 5, "patch": 1},
               "base": "0.2.0"|null},
 "drafts": 0}
```

Карточка версии `GET /api/versions/{v}` — ОДНА форма (её читает и экран
«Версии», и ссылка паспорта CDPM `<CREW_CATALOG_URL>/ui/#/versions/<v>`):
```json
{"version": "0.2.0",
 "entry": {…запись журнала версий: fingerprint, released_at, released_by,
           code, changes…}|null,
 "header": {…шапка выпуска: version, fingerprint, released_at,
            released_by, note, gates, "is_draft": false…}|null,
 "changes": [Change…],
 "loadable": true,
 "is_current": false}
```
Шапка лежит ПОД ключом `header` (из `catalog.json` папки выпуска), а не
на верхнем уровне: на верхнем уровне только `version`. `entry` — запись
журнала (её нет, если папка выпуска есть, а записи ещё нет). `loadable`
— читается ли папка выпуска читателем.

**Текущая версия.** `v == версия каталога` — карточка отвечает
`is_current: true`, `header` — текущая шапка с `is_draft`, `loadable:
true`, `changes` — записи журнала этой версии либо (у черновика) дифф от
последнего выпуска. Правило то же, что у `?version=` записи: до первого
выпуска ссылка паспорта ведёт именно сюда, и отказ «версии нет: текущая —
она же» был бы неправдой. Отказ: 404 `unknown-version`.

### `GET /api/versions/compare?a=&b=`
`a`/`b` — `current`, `drafts` либо semver.
```json
{"a": {"version": "0.2.0", "fingerprint": "…"},
 "b": {"version": "0.2.0-draft.2", "fingerprint": "…"},
 "changes": [Change…], "level": "minor"}
```

### `GET /api/versions/release/checks?version=`
Сухой прогон выпуска: двенадцать предпроверок словами (восемь §2.6
шага 5, три §4 шага 6 и двенадцатая — паритет с кодом COSMOS), БЕЗ
единого байта на диск. Доступен в любом режиме — это чтение.
```json
{"version": "0.2.0",
 "suggested": {"level": "minor", "version": "0.2.0", "changes": 62},
 "ok": false,
 "checks": [{"n": 1, "key": "role", "title": "режим правки и роль владельца",
             "ok": true, "code": null, "words": "рабочее место: владелец",
             "extra": {}},
            {"n": 6, "key": "git", "title": "репозиторий и чистое дерево каталога",
             "ok": false, "code": "git-dirty", "words": "…именами файлов…",
             "extra": {"dirty": ["catalog/families.json"]}},
            {"n": 12, "key": "parity", "title": "паритет с кодом COSMOS",
             "ok": true, "code": null,
             "words": "паритет с кодом COSMOS зелёный: 0.2.0 9d47e56f0c1a… | rules 23, green 21, red 0, skipped 2, warning 0",
             "extra": {}}],
 "confirm_stop_needed": false,
 "confirm_parity_needed": false,
 "parity": {"ok": true, "rc": 0, "words": "…", "warnings": []}}
```
Ключи проверок по порядку: `role`, `drafts`, `markers`, `validate`,
`version`, `git`, `bindings`, `folder`, `candidates`, `executors`,
`in_work`, `parity` (четыре последних — шаг 6 и пакет паритета; их
смысл — в `POST /api/versions/release` ниже). Коды красных строк — те же, что
у отказа `POST /api/versions/release`; закон один: и чек-лист, и выпуск
зовут одни и те же функции проверок. Ответ несёт ещё
`requests_in_work: [номера]` и `warnings: [слова]` — заявки, по которым
идёт CR: выпуск их не трогает, но владелец обязан их видеть. Диалог
«Выпустить версию»
(§4.5 проекта) показывает этот перечень ДО нажатия: маркеры заказчика,
чистоту дерева git, остановку сторожа и вердикт паритета знает служба, а
не экран.

`confirm_parity_needed: true` значит: строка 12 снимается полем
`confirm_parity: true` выпуска (судья не судил либо предупредил). У
КРАСНОГО паритета признак остаётся `false` — этот отказ не снимается
ничем. Блок `parity` — короткая выжимка вердикта (`ok`, `rc`, `words`,
`warnings`); полный вердикт остаётся у судьи (`--json`) и в
свидетельстве CR (`gates.parity`).

Судья — подпроцесс до 120 с, и поднимается он только там, где выпуск
возможен: у роли с правом выпуска. На площадке (`--read-only`) и у роли
без этого права строка 12 зелена СЛОВАМИ «паритет с кодом COSMOS не
судится: режим только чтение — выпусков площадка не делает» (либо «право
выпуска у роли … отсутствует»), `parity.ok` — `null`,
`confirm_parity_needed` — `false`: выпуск там остановит первая же строка
чек-листа (право роли), и ждать подпроцесса ради этого незачем.

### `GET /api/journal?limit=`
Всегда `{"items": [строки `<work_dir>/actions.jsonl`], "words": null}` —
последние `limit` (умолчание 200), новые первыми. Строка журнала:
`{"at", "actor", "op", "target", "outcome", "words"}`. В режиме просмотра
— `{"items": [], "words": "журнал ведётся только на рабочем месте"}`.

### `GET /api/export`
Тело — `template_catalog.export.export_single(cat)`;
`Content-Disposition: attachment; filename="template-catalog-<version>-<fp[:12]>.json"`.

### Форма `Change` (из `template_catalog.diff`)
```json
{"section": "families", "id": "duty_limits", "kind": "изменена",
 "note": "поля: title; params: +max_x, −min_y", "level": "patch"}
```
`kind` — ровно `добавлена | изменена | удалена`; `level` —
`major | minor | patch` (в журнал версий не пишется).

---

## 5. Ручки записи

### `PUT /api/drafts/{section}/{id}` (аналитик+; смена `status` — владелец)
Тело `{"record": {…полная запись по схеме читателя, с status и
stand_tasks, без history…}, "note": "слова автора правки"}`.
```json
{"saved": true, "changes": [Change…], "problems": [предупреждения],
 "derived": ["lexicon_order: +duty_limits"]}
```
Отказы: 403 `read-only` / `owner-only`, 409 `id-taken-by-request`,
422 `invalid-draft` (+`problems` — НОВЫЕ ошибки относительно живого
каталога; адрес может указывать на другую запись — например, X01
сообщает асимметрию `complements`).

Перечень маркеров заказчика подаётся в `validate()` всегда, когда он
прочитан; перечня нет — в `problems` предупреждение
`{"code": "markers-missing", "severity": "warning"}` и строка журнала.

### `PUT /api/drafts/dictionaries/{key}` (аналитик+)
Тело `{"value": <значение ключа целиком>, "note": "…"}`; ответ как выше.
Отказы: 404 `unknown-key`, 422 `closed-list` (служебные перечни
`request_states`, `executor_states`, `record_statuses`, `products`,
`languages`, `stand_results`), 422 `invalid-draft` (удаление
используемого значения — с адресами использующих записей).

Ключа, которого в каталоге ещё нет, черновик не заводит — с одним
исключением: `OPTIONAL_DICT_KEYS = ("document_classes",)`. Схема читателя
знает `document_classes` как НЕОБЯЗАТЕЛЬНОЕ свойство словарей (D01 судит
`source.document_class` только когда ключ есть), а сам словарь появляется
первой сборкой службой. Прочие неизвестные ключи — 404 `unknown-key` со
словами о том, что завести черновиком можно только их.

### `DELETE /api/drafts/{section}/{id}` и `DELETE /api/drafts/dictionaries/{key}` (аналитик+)
`{"discarded": true}`; 404 `no-draft`.

### `DELETE /api/drafts` (аналитик+) — «Отменить все»
`{"discarded": N}` — сколько файлов черновиков выброшено.

### `POST /api/requests` (аналитик+ с экрана; продукт — машинным каналом §8)
Тело с экрана
```json
{"section": "families", "record_id": "duty_limits",
 "source": {"kind": "вручную", "document_class": null,
            "run_class": null, "ref": null},
 "draft": {…черновик записи без status/history/stand_tasks…}|null,
 "executor_kinds_requested": ["kotlin_constraint"]|null,
 "key": "<64 hex материала заявки>"|null, "submitted_at": "…"|null}
```
201 — КВИТАНЦИЯ по схеме `receipt/1` (ниже). Заявка считается поданной
ТОЛЬКО по ней: «любой 2xx» приёмом не является.

**Номер заявки** служба не выдумывает: он считается ЗАКОНОМ ЧИТАТЕЛЯ
(`template_catalog.requests`, §2.1 проекта шага 6) от содержания —
`request_id_of(key)`, где `key` — либо принесённый ссылкой продукта
(параметр `key`, 64 hex), либо `kind_request_key(section, record_id,
kinds)` у заявки на вид, либо `manual_key(section, record_id,
draft_canon(draft))` у ручного черновика. Один и тот же пробел, увиденный
двумя продуктами на разных машинах, получает ОДИН номер без обмена, а
повтор подачи идемпотентен.

Повтор той же заявки — 200 с ТОЙ ЖЕ квитанцией: `note` = «уже подана»,
`repeat: true`, `state` — текущее (в том числе «принята» и «есть»). Не
201; ничего не записано.

**Квитанция (`receipt/1`).** Тело 201 и 200 — одной формы; схема живёт у
читателя каталога (`template_catalog/schemas/receipt.schema.json`), у
службы — байт-в-байт копия (`catalog_service/schemas/receipt.schema.json`).

```json
{"schema_version": "receipt/1",
 "request_id": "req-04763385720803207704",
 "state": "подана", "note": null, "repeat": false,
 "catalog": {"version": "0.5.0", "fingerprint": "<64 hex>"},
 "section": "families", "record_id": "duty_limits",
 "canon": "<64 hex draft_canon(draft)>"|null,
 "decision": {"by": null, "at": null, "words": null, "version": 0},
 "journal_ref": {"op": "request-submit", "at": "…", "actor": "владелец"},
 "issued_at": "…",
 "request": {…}, "submitted_by": "CDPM", "words": "…", "problems": []}
```

Поля отвечают на четыре вопроса клиента: ТА ЛИ заявка (`request_id` по
закону номера, `section`, `record_id`, `canon`), В КАКОМ она состоянии
(`state` словом словаря `request_states`, `decision`), НА КАКОМ каталоге
выдана (`catalog`) и ГДЕ след приёма (`journal_ref` — действие, момент и
кто записал ИЗ ЖУРНАЛА службы: у повторной выдачи это строка о первом
приёме, а строки нет — честные `null`; `issued_at` — часы службы
сейчас). `decision.version` — номер решения: 0, пока решения нет, и 1 у
записанного; ДО каталога 0.6.0 пересмотр решения («отклонена → принята»)
оставляет `version: 1` и меняет только `at` — отличать новое решение от
старого обязано время, а не версия; счётчик пересмотров станет полем
записи заявки в 0.6.0. Перечень слов `state` закрыт `enum` самой схемы:
клиент судит ответ службы, каталога которой у него может не быть, и
своей копии словаря не держит. Прежние поля тела (`request`,
`submitted_by`, `words`, `problems`) сохранены — тело только
ДОПОЛНЯЕТСЯ, и старый клиент его не отвергает.

**Закон клиента — ОДНА функция на обоих клиентов**:
`template_catalog.receipts.validate_receipt(body, *, sent=None) ->
list[str]` (пусто — квитанция), её зовут `catalog_service.import_requests
.receipt_problems` и `cdpm/catalog_requests.deliver_one`. Доставка
засчитывается, только если код 201 (либо 200 со словами «уже подана»),
`Content-Type: application/json`, тело проходит схему `receipt/1`,
`state` — слово закрытого перечня схемы, а `request_id`, `section`,
`record_id` и `canon` равны отправленным. Иначе исход «неизвестен»: файл
остаётся в очереди, повтор идёт ТЕМ ЖЕ номером, нового номера не
порождается. Страница входа с кодом 200, пустое тело, 204, чужой номер,
ответ про другую запись, состояние вне перечня, 201 с признаком
повторной выдачи и ответ старой службы без `schema_version` квитанциями
НЕ являются (последнее задумано: площадка со старой службой обязана
обновляться одним раскатом). Код ответа, тип содержимого и согласие
`repeat`/`note` с кодом добавляет клиент: тело о своём HTTP-коде не
знает.

Отказы: 409 `request-exists` (`extra.existing`, `extra.state`, `extra.ref`
— по записи уже есть открытая заявка иным черновиком либо тот же номер с
иным каноном), 409 `request-closed` (`extra.state`, `extra.decision`
{`words`, `version`, `at`} — решение владельца видно из любого прогона),
409 `write-busy`, 422 `invalid-request`, 422 `bad-request-id`, 400
`bad-form`, 400 `submitter-not-allowed`, 401 `bad-token`, 403
`machine-channel-off` / `read-only` / `owner-only`.

### `POST /api/requests/dry-run` (кто угодно, в том числе просмотр)
Тело — как у подачи. Ответ
```json
{"request_id": "req-04763385720803207704", "cr_key_expected": "crd89c9550",
 "known": {…}|null, "record_exists": false, "open_request": {…}|null,
 "problems": [Problem…]}
```
Ни записи, ни замка, ни строки журнала: форма экрана обязана уметь
спросить «что скажет служба», не оставляя следа. `record_exists` форма
читает, чтобы предложить законное: у существующей записи допустима
ТОЛЬКО заявка на вид исполнителя (R02), и карточка черновика ей обещала
бы невозможное. `open_request` — открытая заявка на ту же запись, если
она уже есть (иначе подача получит 409 `request-exists`).

### `POST /api/requests/{id}/decide` (владелец)
Тело `{"decision": "принята"|"отклонена", "words": "…"}` → заявка целиком.
Законные переходы: подана → принята | отклонена; принята → отклонена;
исполнитель заявлен → отклонена; **отклонена → принята** (пересмотр
решения словами владельца — единственный выход из тупика для того же
пробела). Из «есть» — 409: решать нечего.
Слова решения судятся A-правилами и маркерами заказчика по адресу
`requests/<id>.decision.words` (422 `invalid-request` с `problems`).
Отказы: 409 `bad-transition`, 422 `words-required`, 422 `invalid-request`.

### `PUT /api/requests/{id}/cr_key` (владелец с экрана либо COSMOS токеном)
Тело `{"cr_key": "crd89c9550"}` → заявка в состоянии «исполнитель заявлен».
Ключ обязан равняться `cr_key_of(request_id)` закона читателя: CR — это
ПРОЕКЦИЯ заявки, и тот же CR открывается в любом экземпляре COSMOS без
переноса очереди. Повтор того же ключа — 200 без записи.
Отказы: 409 `bad-transition` («ключ CR принимается только у принятой
заявки»), 422 `bad-cr-key` (`extra.expected`), 403 машинного канала.

### `POST /api/requests/{id}/adopt` (владелец)
Тело `{"confirm": null|"<слова владельца>", "candidate": false|true}`.
```json
{"draft_written": true, "executors_missing": [],
 "candidate": "candidates/families/x.json"|null,
 "next": "соберите черновик — заявка перейдёт в «есть» той же записью"}
```
Пишется ТОЛЬКО черновик записи с `request_id`; `requests.json` не
меняется. С `"candidate": true` запись берётся из файла конвейера
`catalog/candidates/<раздел>/<id>.json` — целиком, вместе со
`stand_tasks` (схема запрещает `stand_tasks` у черновика заявки, и
другого пути принести стендовые задачи в новую запись нет). Путь строит
служба по разделу и записи заявки; снаружи он не принимается. Предпроверка
«исполнитель есть» у новой записи идёт по `evidence.executors` кандидата:
строки «есть» в файле привязок дерева у неё нет по построению (B02).
Кандидат судится теми же правилами, что и гейты ветки CR, —
`template_catalog.candidates.validate_with_candidate` (одно вычисление).
Черновик кандидата помечается признаком `from_candidate: true` — по нему
и только по нему сборка удаляет файл кандидата (ниже).
Отказы: 409 `bad-transition`, 412 `executor-not-present` (снимается полем
`confirm` со словами — они попадают в `note` истории), 412
`candidate-ignored` (перевод БЕЗ кандидата, когда файл по этой заявке
лежит: «кандидат по этой заявке есть, а взят не он: …»; снимается тем же
`confirm`, и тогда файл остаётся — его увидит предпроверка выпуска 9),
404 `no-candidate`, 422 `candidate-mismatch`, 422 `invalid-draft`.

### `POST /api/versions/assemble` (аналитик+)
Тело `{"note": "…", "version": null|"X.Y.Z-draft.N", "confirm_stop": false}`.
```json
{"version": "0.2.0-draft.2", "fingerprint": "<64 hex>", "applied": 3,
 "derived": ["lexicon_order: +x", "complements: families/y += x"],
 "problems": [предупреждения],
 "next": [".venv\\Scripts\\python.exe catalog\\tools\\build_from_code.py --refresh --out catalog",
          ".venv\\Scripts\\python.exe -m vmp.stand_tasks --write-results vmp\\stand_results.json",
          ".venv\\Scripts\\python.exe -m vmp.bindings_build --out vmp\\bindings.json --stand-results vmp\\stand_results.json",
          ".venv\\Scripts\\python.exe -m vmp.bindings_gate vmp\\bindings.json cdpm\\bindings.json",
          ".venv\\Scripts\\python.exe catalog\\tools\\parity.py"]}
```
Цепочка `next` НАЧИНАЕТСЯ пересчётом посева и дельты перехода
(`--refresh`) и КОНЧАЕТСЯ законом паритета: посев и дельта — проекция
каталога, их правит инструмент, а не рука, и до `--refresh` правило
свежести носителей L23 красно словами.
Отказы: 409 `nothing-to-assemble`, 409 `version-not-newer`, 409
`write-busy`, 412 `bindings-stop` (снимается `confirm_stop: true`),
422 `invalid-catalog`, 403.

Сборка черновика, пришедшего из файла-кандидата конвейера (§8.4, признак
`from_candidate` черновика), УДАЛЯЕТ этот файл и называет удалённое:
`{"candidates_removed": ["candidates/…"]}`
и строка в `next` («…включите удаление в коммит сборки, иначе выпуск даст
412 git-dirty»). Черновик БЕЗ этого признака (заявку перевели в записи не
кандидатом) чужой файл не трогает: удалять содержимое, которого в записи
нет, сборка не вправе — оставленный кандидат краснит предпроверку выпуска
9, и это честнее молчаливого удаления. ЛЮБОЙ отказ явной версии сборки — 409
`version-not-newer` (не черновик, ниже пола уровня, не новее текущей);
`version-too-low` — код выпуска и в сборке не употребляется.

**Правило версии сборки.** База — последняя запись `versions.json` (нет —
базы нет). `level = suggest_level(diff_bodies(тело базы | None, кандидат))`;
`floor = next_version(база, level)` либо `0.1.0` без базы;
`тройка = max(текущая тройка, floor)`; `N = N_текущий + 1`, если тройка не
изменилась и текущая версия — черновик, иначе `1`. Явная `version` в
теле обязана быть черновиком, тройка ≥ floor и ≥ текущей, пара
(тройка, N) строго больше текущей.

### `POST /api/versions/release` (владелец)
Тело `{"version": "0.2.0", "note": "…", "confirm_stop": false,
"confirm_parity": false}`.
```json
{"version": "0.2.0", "fingerprint": "<64 hex>", "released_at": "2026-09-08",
 "code": "<40 hex>", "changes": 24, "release_dir": "releases/0.2.0",
 "parity": {"ok": true, "rc": 0, "words": "паритет с кодом COSMOS зелёный: …",
            "warnings": []},
 "confirmed_parity": false,
 "next": [".venv\\Scripts\\python.exe catalog\\tools\\build_from_code.py --refresh --out catalog (…)",
          "git add catalog && git commit -m \"каталог: выпуск 0.2.0\" (…)", "…",
          ".venv\\Scripts\\python.exe catalog\\tools\\parity.py → 0 красных", "…"]}
```
Двенадцать предпроверок по порядку: роль и режим → черновиков нет →
перечень маркеров прочитан → `validate()` 0 ошибок → версия по правилу →
git (HEAD 40 hex и чистое дерево `catalog/`, `template_catalog/`) →
привязки (B01) → папки `releases/<v>` ещё нет → кандидатов конвейера не
осталось (409 `candidate-pending`) → у записей из заявок «есть»
исполнители названы (412 `executors-incomplete`) → заявки в работе
(предупреждение, выпуск не останавливает) → **паритет с кодом COSMOS**
(412 `parity-red` / `parity-unconfirmed`).

**Двенадцатая предпроверка — паритет с кодом COSMOS.** Служба зовёт
закон паритета `<папка каталога>/tools/parity.py` ПОДПРОЦЕССОМ
(интерпретатор — `.venv` рядом с деревом каталога, если он есть, иначе
интерпретатор службы; рабочая папка — корень дерева; предел 120 с;
`--catalog <папка каталога>`, `--bindings <файл привязок COSMOS>`,
`--json <во временную папку>`, `--quiet`). Переменные подпроцесса —
`CREW_TEMPLATE_CATALOG=<папка каталога>` и `PYTHONUTF8=1`: двери
продуктов читают именно их, и без переменной правило L10 отвечало бы
«двери читают другой каталог» не по делу. Коды судьи: 0 — красных
правил нет, 1 — есть красные (`parity-red`), 2 — не судилось
(`parity-unconfirmed`). Замок записи держится всё время подпроцесса:
судить каталог, который в эту секунду переписывают, значило бы судить
не то, что выпускается. Служба ничего не пишет в папку каталога —
вердикт уходит во временную папку вместе с ней. Вердикт называется
словами в журнале выпуска; при осознанном продолжении исход строки
журнала — `warning`, а ответ несёт `confirmed_parity: true`.

Ответ выпуска и `GET /api/versions/release/checks` несут
`{"requests_in_work": ["req-…"], "warnings": ["заявок в работе: N …"]}`:
одиннадцатая строка чек-листа никогда не красная, но всегда называет,
сколько заявок останется в работе после выпуска. К тем же `warnings`
дописываются предупреждения судьи паритета (`skipped` с признаком
`warning`) — они не молчат ни в чек-листе, ни в ответе выпуска.

**Полнота исполнителей (10) и сторож версий (7) — разные вопросы.**
`bindings-stop` судит СВЕЖЕСТЬ файлов привязок относительно версии
каталога; `executors-incomplete` — ПОЛНОТУ исполнителей у записи,
рождённой заявкой. Виду засчитываются три честных исхода: строка «есть»,
строка «заявлен» с ключом CR по образцу `^cr[0-9a-f]{8}$` (работа названа
и адресована) и строка «нет» с заметкой. Молчание — четвёртый исход, и он
не засчитывается; провал стенда записи — тоже отказ.

**Смысл поля `code`.** Это срез кода, в котором лежит ТЕЛО выпуска:
дерево чисто по предпроверке 6, значит HEAD уже несёт собранный черновик
с тем же отпечатком. Коммит самого выпуска (шапка, журнал,
`releases/<v>/`) — следующий за ним.

**Чистота дерева (предпроверка 6).** Корень дерева даёт сам git
(`rev-parse --show-toplevel` из папки каталога), путь каталога считается
относительно корня: каталог вправе лежать не прямо под корнем. Проверяются
папка каталога и `template_catalog`; если `template_catalog` в этом дереве
нет, это сказано словами в строке чек-листа и в отказе («… в этом дереве
нет — чистота этих папок не проверялась»), а не пропущено молча.

**Заметки первого выпуска.** У ПЕРВОГО выпуска базы нет: каждая запись и
каждый ключ словаря — `добавлена`, и `note` каждой строки журнала —
«первый выпуск службой; до него каталог собирался инструментом
извлечения» (§2.6 проекта): она о происхождении снимка, а не о правке,
которой не было. У последующих выпусков `note` — слова диффа от
предыдущего выпуска.

### `POST /api/import` (владелец; multipart `file`)
```json
{"replaced": true, "version": "0.2.0", "fingerprint": "<64 hex>",
 "warnings": ["в журнале версий 0.1.0, папки releases/0.1.0 нет"]}
```
Отказы: 409 `drafts-pending`, 422 `invalid-single-file`, 422
`fingerprint-mismatch`, 422 `invalid-catalog`, 409 `version-downgrade`
(снимается `?force=1`), 403. Папка `releases/` не переносится.

Присланный файл служба кладёт во ВРЕМЕННУЮ папку машины
(`tempfile.TemporaryDirectory(prefix="catalog-import-")`) и уносит её за
собой: это единственное место записи службы вне рабочей папки, папки
каталога и файла замка, и гейт T2 знает о нём.

---

## 6. Адреса экранов (дополняет дорожка D)

База — `<CREW_CATALOG_URL>/ui/#/…` (HashRouter; интерфейс раздаётся по
`/ui/`). Без настройки `CREW_CATALOG_URL` продукты показывают текст без
ссылок и подпись «служба каталога не настроена (CREW_CATALOG_URL)».

| Адрес | Экран |
|---|---|
| `#/records?section=&kind=&zone=&class=&state=&status=&q=` | «Семьи и критерии», список |
| `#/records/families/<id>`, `#/records/criteria/<id>` (`?version=&fingerprint=`; якоря `#params`, `#executors`, `#stand`, `#history`) | карточка записи |
| `#/dictionaries`, `#/dictionaries/<key>` | «Словари» |
| `#/requests?state=&submitted_by=&source_kind=&record_id=&cr_key=&ref=`, `#/requests/<req-id>`, `#/requests/new?section=&record_id=&source=&document_class=&run_class=&ref=&key=` | «Заявки» |
| `#/executors?product=&section=&id=&kind=&state=` | «Исполнители по продуктам» |
| `#/versions`, `#/versions/<v>`, `#/versions/compare?a=&b=` | «Версии и выпуски» |

Адреса в артефакты продуктов (реестр нажатия, паспорт, пакет) не
пишутся — только `version` и `fingerprint`; ссылку строит сторона,
показывающая экран.

### Карта цветов состояний (одна на три панели)

| Состояние | Чип |
|---|---|
| есть | `ok` (зелёный) |
| заявлен | `warn` (жёлтый) |
| нет | `neutral` (серый) |
| вид никем не объявлен | `info` (синий) с подсказкой «вид вне зоны обоих продуктов» |
| ошибки B01–B05 | `error` (красный) |

---

## 7. Штамп свежести сборки интерфейса

`catalog-ui/dist/build-stamp.json` = `{"src_sha256": "<64 hex>"}`.
Формула — одна на три места (`catalog-ui/scripts/stamp.mjs`,
`tests/catalog_service/test_ui_static.py`, этот файл):

* входы: `src/**`, `index.html`, `public/**`, `package.json`,
  `package-lock.json`, `vite.config.ts`, `tsconfig*.json`,
  `tailwind.config.js`, `postcss.config.js`, `.oxlintrc.json`;
* пути — относительные, в POSIX-форме (`/`), отсортированы;
* содержимое каждого файла — байты с заменой `\r\n` → `\n`;
* вклад файла в хеш — `<путь>\0<содержимое>\0`;
* итог — sha256 конкатенации вкладов в порядке путей, 64 hex.


---

## 8. Машинный канал и заявки шага 6

Шаг 6 (конвейер пополнения) добавляет службе ТРЕТИЙ вход заявок — вызов
продукта, — и не добавляет ей ни одного нового состояния, поля или файла
у заявки. Источник истины: `final-report/KATALOG-SHAG-6-PROEKT-CLD.md`
(§1.1, §2.1–2.3, §5.1, поправки к шагу 5 П1–П9).

### 8.1. Канал

```
POST <адрес службы>/api/requests
Content-Type: application/json
X-Catalog-Product: CDPM | COSMOS          (значение ∈ словаря products)
X-Catalog-Token:   <CATALOG_MACHINE_TOKEN службы>
Тело: запись заявки §6 схемы ЦЕЛИКОМ (11 полей)
```

Признак канала — ЗАГОЛОВКИ, а не тело: без обоих заголовков ручка
работает как экранная (роль рабочего места). Порядок отказов закрыт:

1. `CATALOG_MACHINE_TOKEN` у службы не задан → 403 `machine-channel-off`
   (площадки и установка заказчика — именно этот случай: там служба
   стоит `--read-only` и почтового ящика у неё нет);
2. `X-Catalog-Token` не совпал → 401 `bad-token`;
3. `X-Catalog-Product` вне словаря `products` → 400
   `submitter-not-allowed`;
4. режим просмотра / роль без права `request-submit` → 403 `read-only`
   либо `owner-only`.

Сначала судится КАНАЛ, а не токен: без токена службы сравнивать нечего, и
«неверный токен» был бы неправдой о причине.

Тело продукта обязано быть заявкой в состоянии «подана» с нулевым
`decision` и `cr_key: null`, а `submitted_by` — совпадать с заголовком
продукта (подписать чужим именем нельзя). `submitted_at` подателя
сохраняется как есть (момент составления — часть рецепта повтора CDPM);
поля нет — служба ставит своё.

Ответы: 201 — квитанция `receipt/1` (§5, `POST /api/requests`); повтор
той же заявки — 200 с той же квитанцией (`note`: «уже подана», `repeat:
true`); отказы — §2.1 (409 `request-exists`, 409 `request-closed`, 409
`write-busy`, 422 `invalid-request`, 422 `bad-request-id`). Продукт
считает заявку поданной ТОЛЬКО по квитанции с совпавшим номером; иной
2xx, не-JSON и чужой номер — исход «неизвестен» и повтор тем же номером.

`409 write-busy` — единственный ПОВТОРЯЕМЫЙ отказ (замок записи занят):
продукты повторяют его трижды, как сетевую ошибку; прочие 4xx повтора не
заслуживают.

**Слова приёма.** Не только отказ, но и приём службы говорит словами —
поле `words` тела ответа (продукт кладёт его в квитанцию, инструмент
импорта — в файл-ответ, экран — в подсказку):

| Ручка | Слова |
|---|---|
| `POST /api/requests` (канал, 201) | «заявка от продукта COSMOS принята: req-… (подана); решение — за владельцем» |
| `POST /api/requests` (экран, 201) | «заявка req-… подана с экрана: решение — за владельцем» |
| `POST /api/requests` (повтор, 200) | «заявка req-… уже подана: состояние «подана» — дубль не заводится» |
| `PUT /api/requests/{id}/cr_key` | «ключ CR cr… записан по вызову COSMOS: заявка req-… — «исполнитель заявлен»» (с экрана — «записан с экрана владельцем») |
| `POST /api/requests/{id}/adopt` | «кандидат записи из CR cr… (ветка cr-…, коммит …, стенд 3/0/1, привязки ok) импортирован в черновики; соберите черновик — заявка перейдёт в «есть» той же записью» |
| `POST /api/requests/{id}/decide` (422) | «решение владельца без слов не записывается»; «слова решения содержат данные заказчика: <проблема> — перепишите словами о механике» |

### 8.2. Закон номера и ключа CR (читатель, не служба)

`template_catalog.requests`:

| Функция | Что даёт |
|---|---|
| `request_key(section, kind, zone, statement)` | ключ новой записи от «раздел + вид + зона + нормализованная формулировка» |
| `kind_request_key(section, record_id, kinds)` | ключ заявки на вид исполнителя (набор видов — часть содержания) |
| `manual_key(section, record_id, draft_canon)` | ключ ручного черновика |
| `request_id_of(key)` | `"req-" + str(int(key[:16], 16)).zfill(20)` |
| `cr_key_of(request_id)` | `"cr" + sha256("catalog-request:" + request_id)[:8]` |

Три свидетеля литералов (тест читателя, тест CDPM, тест продукта COSMOS):
материал «раздел, вид, зона, формулировка», склеенный переводами
строки (`families`, `HARD`, `PAIRING`, `правило образец сторожа закона`) →
ключ `421af213…`, номер `req-04763385720803207704`, ключ CR `crd89c9550`.

Что служба ПЕРЕСЧИТЫВАЕТ и судит: номер заявки на вид (`draft == null`) —
несовпадение даёт 422 `bad-request-id` с `extra.expected`. Чего она
пересчитать не может: номер черновика новой записи — формулировки правила
у неё нет, и она принимает номер по образцу от продукта-подателя
владельца. Это сказано здесь честно, а не скрыто.

### 8.3. Машина состояний

| Из → в | Кто | Чем |
|---|---|---|
| — → подана | продукт (канал), аналитик+ (экран), инструмент импорта | `POST /api/requests` |
| подана → принята \| отклонена | только владелец | `POST /api/requests/{id}/decide` |
| принята → исполнитель заявлен | владелец с экрана либо COSMOS токеном | `PUT /api/requests/{id}/cr_key` |
| принята \| исполнитель заявлен → отклонена | только владелец | `decide` |
| отклонена → принята (пересмотр) | только владелец | `decide` |
| принята \| исполнитель заявлен → есть | владелец: `adopt` → аналитик+: сборка | `adopt` + `POST /api/versions/assemble` |

Отзыв подателем состоянием каталога не заводится: продукт отзывает файл
из своей очереди до принятия номера, а после решения владельца путь назад
— его же решение.

### 8.4. Кандидат конвейера

`catalog/candidates/<раздел>/<id>.json` по схеме
`template_catalog/schemas/candidate.schema.json` — черновик службы ⊕
`request_id` ⊕ `cr_key` ⊕ `evidence`. Файл пишет ветка CR продукта; вне
отпечатка каталога и вне отметки читателя. Служба его ЧИТАЕТ (`adopt
{"candidate": true}`), а сборка — УДАЛЯЕТ и называет удалённое в `next`
(`candidates_removed`): оставленный файл краснит предпроверку выпуска 9.

### 8.5. Инструмент приёма очередей

```
python -m catalog_service import-requests --inbox <папка>
       [--api-url http://127.0.0.1:8105] [--token <CATALOG_MACHINE_TOKEN>] [--reply]
```

Почтового ящика у службы нет: заявки площадок копятся в очередях
продуктов (`CDPM_CATALOG_OUTBOX`, `CREW_CATALOG_OUTBOX`) и переносятся на
рабочее место владельца копией папок. Инструмент проходит `*.json` папки
(кроме самих ответов), берёт продукта-подателя ИЗ ФАЙЛА (`submitted_by`) и
отдаёт заявку службе тем же машинным каналом — единственный писатель
каталога остаётся один. С `--reply` рядом ложится файл-ответ
`<имя>.reply.json`:

```json
{"request_id": "<номер подателя>", "catalog_request_id": "req-…"|null,
 "accepted": true, "state": "подана"|null, "problems": [Problem…],
 "words": "…", "at": "…", "file": "req-….json",
 "by": "инструмент импорта COSMOS (catalog_service import-requests)"}
```

Повторный прогон файлы с готовым ответом пропускает. Коды возврата: 0 —
все приняты, 1 — были отказы (ответы записаны), 2 — службы нет или папки
очереди нет (словами, ничего не сделано). Под `catalog/` инструмент не
пишет ничего.
