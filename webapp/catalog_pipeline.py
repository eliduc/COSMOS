# -*- coding: utf-8 -*-
"""Конвейер пополнения каталога шаблонов на стороне COSMOS: сверка
заявок каталога с очередью CR (шаг 6, дорожка B).

Заявка — ЗАПИСЬ каталога (раздел `requests` вне отпечатка), а не текст в
очереди CR. Решение по заявке принимает ТОЛЬКО владелец, и принимает его
в службе каталога. Здесь — зеркало этого решения в очередь изменений
COSMOS:

  заявка «принята» ⟶ строка CR со статусом `accepted` и ключом
      `cr_key_of(request_id)` (детерминирован — тот же CR открывается на
      любой машине без переноса очереди);
  ключ пишется обратно в каталог машинно (`PUT /api/requests/{id}/cr_key`)
      ТОЛЬКО там, где служба в режиме правки, — на рабочем месте
      владельца; на площадках (`--read-only`) ключ не пишется, и об этом
      говорится один раз словами;
  заявка «отклонена» ⟶ строка CR отклоняется зеркалом (кроме той, что
      уже в работе: её отклоняют после завершения);
  заявка «есть» ⟶ строка журнала, статус CR не трогается.

Второй отмашки в очереди нет по замыслу (решение 0.4 проекта шага 6):
решение владельца уже записано в каталоге, и просить его второй раз в
другом экране значило бы держать два источника истины.

Сверка ИДЕМПОТЕНТНА: повтор ничего не меняет и ничего не пишет —
ни в очередь, ни в каталог. Отказ службы — строка отчёта словами, не
падение старта.

Слои соседних дорожек берутся ленивым импортом (как и во всём
`webapp/`): закон ключа — читатель `template_catalog.requests`, машинный
клиент службы и очередь заявок продукта — `vmp.catalog_requests`. Модуль
не пишет ни одного файла под `catalog/`: единственный писатель каталога —
служба.
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone

#: Автор строк журнала CR, рождённых сверкой.
SYNC_BY = "сверка заявок каталога"

#: Состояния заявки, у которых есть строка очереди CR (0.4).
KEYED_STATES = ("принята", "исполнитель заявлен")

#: Статусы строки CR, которые сверка вправе обновить или отклонить.
#: `implementing` не трогается (иначе отклонение оборвало бы работу
#: агента на полпути), `implemented` получает только строку журнала.
OPEN_CR_STATUSES = ("draft", "accepted", "needs_human")

#: Признак вида исполнителя РЕШАТЕЛЯ (Kotlin). С 08.09.2026 (слово
#: владельца: «в контуре автоулучшения всё пишет сам COSMOS») дорожка
#: «6-К» отменена — такие виды пишет тот же агент тем же CR, и признак
#: означает ровно одно: этому CR нужна сборка решателя из ветки С
#: ТЕСТАМИ, белый список файлов Kotlin и живой суд стенда (решение 0.1).
SOLVER_KIND_PREFIX = "kotlin_"

#: Формы ожиданий стендовых задач по видам (проект шага 4 §5.2 и решение
#: 0.17): что агент обязан положить в `expected` стендовой задачи. Три
#: вида решателя судит живой стенд, и формы их ожиданий стоят здесь
#: ЛИТЕРАЛОМ со СВИДЕТЕЛЕМ: тест сверяет ключи форм с ключами ожиданий
#: тех же видов в ЖИВОМ каталоге (`catalog/families.json`,
#: `catalog/criteria.json`) — второй источник закона о форме без
#: свидетеля молчалив. Просьба Ж-2 процессу живого стенда — экспорт
#: `GOT_FORMS`; после неё литерал заменяется импортом. Виды без судьи
#: стенда (`condition_fn`, `export_form`) форм не имеют — их свидетель
#: гейт символов, и врать формой о судействе нельзя.
STAND_FORMS = {
    "builder_method": {"symbol": "…", "unit": "…", "value": 0},
    "check_template": {"template": "…", "params": {}, "findings": 0,
                       "measure": None},
    "check_code": {"symbol": "…", "findings": 0},
    "kotlin_constraint": {"constraint": "…", "matches": 0, "amount": 0},
    "kotlin_logic_form": {"form": "…", "matches": 0, "amount": None},
    "kotlin_metric": {"value": 0, "matches": 0, "refusal": False},
}

#: Закон паритета словами — в строку CR и оттуда в задание агенту (§2.5
#: пакета «паритет на временном каталоге», решения 0.3 и 0.6). Это не
#: второй закон: судит его движок `catalog/tools/parity.py`, а здесь
#: сказано, ЧТО он потребует от кандидата, — иначе рост таблиц решателя
#: оставался бы в коде и краснел бы на гейте без объяснения.
PARITY_LAW = (
    "рост таблиц решателя законен, если его ОБЪЯВИЛА запись кандидата, и "
    "судит это закон паритета на ВРЕМЕННОМ каталоге «живой каталог ⊕ твой "
    "кандидат» (python catalog/tools/parity.py --with-candidates — тот же "
    "вердикт даст пятый гейт). Что чем объявляется: новая ручка решателя "
    "— параметр записи с той же единицей и тем же отношением (имя в "
    "«несвязанных» не остаётся); новая метрика — критерий с видом "
    "kotlin_metric, зоной по задачам и ДОСЛОВНЫМ текстом отказа; новая "
    "семья истории — запись природы history с единицей из таблиц "
    "решателя; новая мягкая семья — запись класса SOFT с единицей "
    "нарушения; новая форма логики — строка привязок. Имя параметра, "
    "которого НЕТ в таблицах решателя, законно только словарём CDPM "
    "посева или черновиком заявки владельца: строка истории записи "
    "свидетелем не считается. Строки посева привязок, опережающие живой "
    "каталог, законны по твоему кандидату — перечни ожидающих записей "
    "править не нужно. Посев каталога, дельту перехода и catalog/tools/ "
    "не трогай: они производные, их пересчитывает инструмент.")

#: Куда агенту CR каталога дозволено писать (белый список §3.4 п.2).
#: Всё, что вне перечня, — `needs_human`, дерево сбрасывается.
ALLOWED_PATHS = (
    "vmp/", "_pairing_builder.py", "_duty_periods.py", "_conditions.py",
    "_cos_rules.py", "tests/vmp/", "tests/test_", "catalog/candidates/",
    "vmp/bindings_seed.json", "vmp/bindings.json", "vmp/stand_results.json",
    "docs/manual/13-katalog-shablonov-RU.md",
    "docs/manual/03-optimizaciya-RU.md",
)

#: Судьи, гейты и чужие зоны: агент их не трогает (§3.1). Сторож
#: `_forbidden_paths` очереди действует сверх этого перечня.
#:
#: `kotlin-solver/` стоит здесь целиком: у CR БЕЗ видов решателя решатель
#: агенту закрыт, как и прежде. У CR с видами решателя это начало
#: снимается и заменяется закрытым перечнем `KOTLIN_FORBIDDEN` плюс
#: точным белым списком файлов (`build_row`, решение 0.8): измеряемое не
#: может лежать в зоне правок инструмента измерения.
FORBIDDEN_PATHS = (
    "catalog/catalog.json", "catalog/families.json", "catalog/criteria.json",
    "catalog/dictionaries.json", "catalog/requests.json",
    "catalog/versions.json", "catalog/schema/", "catalog/transition/",
    "catalog/releases/", "catalog/tools/", "template_catalog/",
    "catalog_service/", "catalog-ui/", "webapp/", "kotlin-solver/", "cdpm/",
    "tests/conftest.py", "vmp/stand_tasks.py", "vmp/stand_adapters.py",
    "vmp/stand_solver.py", "vmp/solver_build.py",
    # Модули-«прихожие» пакетов: их исполняет КАЖДЫЙ импорт `vmp.…`, в
    # том числе в процессе судьи (снимок таблиц идёт в клоне). Строчка в
    # `vmp/__init__.py` подменила бы читателя таблиц или счёт surefire,
    # не тронув ни одного файла из перечня запретов.
    "vmp/__init__.py", "vmp/run/__init__.py",
    "vmp/bindings_build.py", "vmp/bindings_gate.py", "vmp/catalog_access.py",
    "vmp/catalog_requests.py", "vmp/registry_check.py",
    "vmp/registry_derive.py", "vmp/readiness_verify.py",
    "vmp/template_registry.py", "vmp/run/button.py", "vmp/kotlin_tables.py",
)

#: Корень исполнителей решателя (тот же, что у гейта символов —
#: `vmp.bindings_gate.KOTLIN_DIR`; равенство сторожит тест).
KOTLIN_SOLVER_DIR = ("kotlin-solver/src/main/kotlin/org/acme/"
                     "flighcrewscheduling/solver/")

#: Белый список файлов Kotlin (решение 0.8, §2): ТОЧНОЕ совпадение пути.
#: Это таблицы и исполнители решателя — и ничего больше. `TaskModes.kt`
#: открыт ТОЛЬКО блоком `METRIC_EXECUTORS` (судья диапазона строк живёт
#: в `change_requests._kotlin_hunk_problems`).
KOTLIN_AGENT_FILES = (
    KOTLIN_SOLVER_DIR + "FlightCrewSchedulingConstraintProvider.kt",
    KOTLIN_SOLVER_DIR + "RunThresholds.kt",
    KOTLIN_SOLVER_DIR + "LogicForms.kt",
    KOTLIN_SOLVER_DIR + "LogicCompiler.kt",
    KOTLIN_SOLVER_DIR + "LogicModel.kt",
    KOTLIN_SOLVER_DIR + "TaskPackage.kt",
    KOTLIN_SOLVER_DIR + "TaskModes.kt",
)

#: Файл, открытый агенту лишь одним блоком (0.8): изменённые строки вне
#: `METRIC_EXECUTORS` — красное словами.
KOTLIN_BLOCK_ONLY = {KOTLIN_SOLVER_DIR + "TaskModes.kt": "METRIC_EXECUTORS"}

#: Папки Kotlin, куда агенту дозволены ТОЛЬКО НОВЫЕ файлы (статус `A`):
#: помощники исполнителя под судьёй токенов, новые файлы тестов и их
#: ресурсы. Правка существующего файла тестов — путь самосертификации
#: (0.4), поэтому судится статусом, а не содержанием.
KOTLIN_AGENT_DIRS = (
    KOTLIN_SOLVER_DIR,
    "kotlin-solver/src/test/kotlin/",
    "kotlin-solver/src/test/resources/",
)

#: Имена ресурсов тестов, которыми можно погасить весь прогон surefire
#: (профиль JUnit, настройки Quarkus, службы `META-INF`): новыми файлами
#: они тоже не заводятся (0.4).
KOTLIN_TEST_RESOURCE_DENY = ("junit-platform.properties", "META-INF/")
KOTLIN_TEST_RESOURCE_DENY_PREFIX = ("application",)

#: Закрытые зоны решателя (0.8, §2): инструмент измерения, машинерия
#: поиска, чужие профили, домен, конфигурация запуска и сборки. Сверх
#: сторожа очереди `_forbidden_paths`, который и так знает `pom.xml`.
KOTLIN_FORBIDDEN = (
    # инструмент измерения — им судят измеряемое
    "kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/rest/",
    KOTLIN_SOLVER_DIR + "PackagedRuns.kt",
    KOTLIN_SOLVER_DIR + "RulesConfig.kt",
    KOTLIN_SOLVER_DIR + "ResourceRules.kt",
    KOTLIN_SOLVER_DIR + "CoverageReport.kt",
    KOTLIN_SOLVER_DIR + "RosterLayoverCost.kt",
    KOTLIN_SOLVER_DIR + "HistoryAssumption.kt",
    # машинерия поиска и чужие профили
    KOTLIN_SOLVER_DIR + "ChainMoves.kt",
    KOTLIN_SOLVER_DIR + "LargeMoves.kt",
    KOTLIN_SOLVER_DIR + "ChainSearchRules.kt",
    KOTLIN_SOLVER_DIR + "DeadheadSeats.kt",
    KOTLIN_SOLVER_DIR + "WorkingSeatsFilter.kt",
    KOTLIN_SOLVER_DIR + "FlightAssignmentDifficultyWeightFactory.kt",
    KOTLIN_SOLVER_DIR + "Gerad",          # начало имени: все профили Gerad*
    "kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/domain/",
    "kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/spp/",
    # конфигурация запуска и сборки
    "kotlin-solver/src/main/resources/", "kotlin-solver/pom.xml",
    "kotlin-solver/mvnw", "kotlin-solver/.mvn/", "kotlin-solver/run.bat",
    "kotlin-solver/target/", "kotlin-solver/hs_err_",
    "kotlin-solver/_kotlin_build_manifest.py",
)

#: Судья токенов (0.8, §2): в ДОБАВЛЕННЫХ строках любого файла под
#: `src/main/kotlin/**` и `src/test/**` этих слов нет. Исполнитель
#: читает мир только из пакета постановки; ручка HTTP, среда, диск и
#: сеть — не его дело. Тесты решателя судятся тем же перечнем: их код
#: исполняет surefire под учётной записью службы, в scratch-копии
#: ВНУТРИ клона, то есть до всех остальных судей и с полным доступом к
#: диску — один такой тест переписал бы судью и закрыл сразу три гейта.
KOTLIN_FORBIDDEN_TOKENS = (
    "@Path(", "@GET", "@POST", "@PUT", "@DELETE", "System.getenv(",
    "System.getProperty(", "ProcessBuilder", "Runtime.getRuntime",
    "File(", "Files.", "URL(", "URI(", "HttpClient", "Socket(",
    "Class.forName", "Path.of(", "Paths.get(", "FileInputStream(",
    "FileOutputStream(", "FileWriter(", "FileReader(", "kotlin.io.path",
)

#: Корни, добавленные строки которых судит судья токенов (0.8 и находка
#: ревью о тестах агента).
KOTLIN_TOKEN_ROOTS = ("kotlin-solver/src/main/kotlin/",
                      "kotlin-solver/src/test/")

#: Те же запреты РЕГУЛЯРНЫМИ выражениями: подстрочный поиск обходится
#: пробелом перед скобкой (`File ("…")`), полным именем аннотации
#: (`@jakarta.ws.rs.Path(`) и пробелами вокруг точки. Судятся оба
#: способа — объединение строго сильнее подстроки (`RandomAccessFile(`
#: ловит подстрока, `File (` — выражение).
def _token_pattern(token: str) -> "re.Pattern":
    """Выражение по написанию токена: точки допускают пробелы, скобка
    отделяется пробелами, аннотация узнаётся и по полному имени."""
    body = token[:-1] if token.endswith("(") else token
    if body.startswith("@"):
        simple = body.lstrip("@").rsplit(".", 1)[-1]
        head = (r"@\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)*"
                + re.escape(simple))
        tail = r"\s*\(" if token.endswith("(") else r"\b"
        return re.compile(head + tail)
    parts = [re.escape(p) for p in body.split(".")]
    head = r"\b" + r"\s*\.\s*".join(parts)
    tail = r"\s*\(" if token.endswith("(") else r"\b"
    return re.compile(head + tail)


KOTLIN_TOKEN_PATTERNS = tuple(
    (t, _token_pattern(t)) for t in KOTLIN_FORBIDDEN_TOKENS)

#: Судья ИМПОРТОВ: импорт обойти труднее, чем вызов, — без него ни
#: диска, ни сети, ни ручки HTTP в файле не появится (Kotlin разрешает
#: и полное имя в месте вызова, но его ловят выражения выше).
KOTLIN_FORBIDDEN_IMPORTS = (
    "java.io.", "java.nio.file", "java.net", "java.lang.Runtime",
    "java.lang.Process", "kotlin.io.", "jakarta.ws.rs", "javax.ws.rs",
    "io.vertx", "io.quarkus.runtime",
)

KOTLIN_IMPORT_RE = re.compile(
    r"^\s*import\s+(" + "|".join(re.escape(x)
                                 for x in KOTLIN_FORBIDDEN_IMPORTS) + r")")

#: Закон приёмки по умолчанию — из словаря каталога `acceptance_default`,
#: если он есть; иначе эти слова (они же в постановке P-N-SHD).
ACCEPTANCE_FALLBACK = (
    "правило того же типа с другими значениями другой авиакомпании "
    "исполняется без правки кода и без нового носителя контура")


class CatalogPipelineError(RuntimeError):
    """Отказ конвейера словами (текст безопасно показывать человеку)."""


# --------------------------------------------------------------- слои

def _law():
    """Закон ключа заявки и ключа CR — читатель (одно вычисление на три
    продукта). Читателя нет рядом — честный отказ словами, а не
    самодельная копия закона."""
    try:
        from template_catalog import requests as tc_requests
    except ImportError as exc:                            # pragma: no cover
        raise CatalogPipelineError(
            f"закон ключа заявки живёт в читателе template_catalog.requests, "
            f"и он недоступен ({exc}): установка неполна — конвейер "
            f"пополнения не начинается") from exc
    return tc_requests


def _cr_key_or_words(law, request_id: str) -> tuple:
    """Ключ CR по закону читателя либо (None, слова) — БЕЗ исключения.

    Закон ключа требует номер `req-<20 цифр>` (`cr_key_of`), а образец
    читателя R01 шире (`^req-\\d{4,}$`): номер прежней формы может
    прийти правкой `requests.json` руками или загрузкой файла волны 1.
    Такая заявка — строка со словами в отчёте и на экране, а не 500 у
    вида «Каталог» и не погасшая нить сверки (ревью волны 2; служба
    судит так же — `request_queue.view` смотрит `id_ok` до `cr_key_of`).
    """
    try:
        return law.cr_key_of(request_id), None
    except ValueError as exc:
        return None, (f"заявка {request_id or '(без номера)'}: ключ CR не "
                      f"считается — {exc}; строка очереди по ней не "
                      f"открывается, поправьте номер в службе каталога")


def _product():
    """Продуктовый слой заявок COSMOS: клиент службы, очередь нажатия."""
    try:
        from vmp import catalog_requests as vmp_requests
    except ImportError as exc:                            # pragma: no cover
        raise CatalogPipelineError(
            f"продуктовый слой заявок vmp.catalog_requests недоступен "
            f"({exc}): установка неполна — сверка и доставка не "
            f"начинаются") from exc
    return vmp_requests


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session():
    """Кто мы для службы и в какой каталог она смотрит; служба не
    настроена либо не отвечает — None (клиент отказывает молча по
    построению, слова говорит вызывающий)."""
    try:
        return _product().service_client.session()
    except CatalogPipelineError:
        raise
    except Exception:                                    # noqa: BLE001
        return None


def configured() -> dict:
    """Настройки машинного канала — ОДНИМ законом с продуктовым слоем
    (`vmp.catalog_requests.configured`): канал настроен при адресе И
    токене вместе. Токен в ответ НЕ кладётся (отступление от буквы §3.2
    проекта с причиной: ответ ручки видит экран и журнал браузера, а
    машинные вызовы делает клиент продукта, читающий переменную сам) —
    вместо значения признак «задан»."""
    cfg = _product().configured()
    return {"api_url": cfg["api_url"], "token": bool(cfg["token"]),
            "ui_url": cfg["ui_url"], "on": bool(cfg["on"])}


def request_link(request_id: str) -> str | None:
    """Адрес карточки заявки в интерфейсе службы (§6.1 шага 5)."""
    ui = configured()["ui_url"]
    if not ui or not request_id:
        return None
    return ui.rstrip("/") + "/ui/#/requests/" + str(request_id)


# ------------------------------------------------------- строка очереди

def _kinds_of(rq: dict, record: dict | None) -> list[str]:
    """Виды исполнителей, которых просит заявка. У заявки на вид — её
    перечень; у заявки на новую запись — виды, объявленные черновиком
    записи (R02 читателя: просить можно только объявленное): у семьи —
    поле `executor_kinds`, у критерия — `computable_by` (схема каталога;
    `executors` — поле привязок продукта, у записи его нет). Первая
    заявка CDPM на новую семью (09.09.2026, duty_series_limit) шла с
    видами только в черновике и рождала «нужен человек: видов не
    названо»."""
    kinds = [str(k) for k in (rq.get("executor_kinds_requested") or ())]
    if kinds:
        return sorted(set(kinds))
    src = rq.get("draft") if rq.get("draft") is not None else record
    src = src if isinstance(src, dict) else {}
    out: list[str] = []
    for field in ("executor_kinds", "computable_by"):
        declared = src.get(field)
        if isinstance(declared, (list, tuple)):
            out.extend(str(k) for k in declared if k)
        if out:
            break
    if not out:                          # запись-привязка: перечень исполнителей
        for ex in (src.get("executors") or ()):
            kind = ex.get("kind") if isinstance(ex, dict) else None
            if kind:
                out.append(str(kind))
    return sorted(set(out))


def _hints(cat, kinds: list[str]) -> dict:
    """Подсказка места по строкам «есть» ТОГО ЖЕ вида в привязках
    продукта: агент пишет исполнителя по образцу, а не наугад."""
    from vmp import catalog_access as _ca
    seen: dict[str, list[str]] = {k: [] for k in kinds}
    try:
        doc = _ca.bindings(cat)
    except Exception:                                    # noqa: BLE001
        return {k: "" for k in kinds}
    for rec in (doc or {}).get("bindings") or ():
        for ex in (rec.get("executors") or ()):
            if not isinstance(ex, dict) or ex.get("state") != "есть":
                continue
            kind = str(ex.get("kind"))
            if kind in seen and len(seen[kind]) < 3:
                line = str(ex.get("file") or "")
                sym = str(ex.get("symbol") or "")
                if sym:
                    line = f"{line}::{sym}" if line else sym
                if line and line not in seen[kind]:
                    seen[kind].append(line)
    return {k: ", ".join(v) for k, v in seen.items()}


def _record_of(cat, section: str, record_id: str) -> dict | None:
    """Существующая запись раздела; её нет — None (заявка на новую)."""
    try:
        if section == "criteria":
            return cat.criterion(record_id)
        return cat.family(record_id)
    except Exception:                                    # noqa: BLE001
        return None


def _acceptance(cat) -> str:
    try:
        words = (cat.dictionaries() or {}).get("acceptance_default")
    except Exception:                                    # noqa: BLE001
        words = None
    if isinstance(words, str) and words.strip():
        return words.strip()
    return ACCEPTANCE_FALLBACK


def _summary(spec: dict, kinds: list[str]) -> str:
    """Описание строки: механика записи словами + виды + приёмка.
    Данных авиакомпании здесь нет по построению — берётся только
    обезличенная спецификация каталога (её судят A01–A06 и маркеры)."""
    text = ((spec or {}).get("semantics") or {}).get("text") or ""
    text = str(text).strip()[:500]
    head = text or (spec or {}).get("title") or ""
    return (f"{head} — виды: {', '.join(kinds) or '—'}; приёмка: стенд "
            f"записи, генератор и гейт привязок, проверка каталога, тесты")


def work_lists(kinds) -> dict:
    """Перечни путей и команд самопроверки для строки CR (§2, §3).

    У CR с видами решателя (`kotlin_*`) начало `kotlin-solver/` из
    запретов снимается и заменяется точным белым списком файлов, папками
    «только новые файлы» и закрытым перечнем `KOTLIN_FORBIDDEN`; первой
    командой самопроверки становится сборка решателя с живым стендом.
    У CR без видов решателя перечни те же, что были, — решатель закрыт
    целиком.
    """
    solver = [k for k in kinds if str(k).startswith(SOLVER_KIND_PREFIX)]
    forbidden = list(FORBIDDEN_PATHS)
    allowed_files: list = []
    allowed_dirs: list = []
    if solver:
        forbidden = [p for p in forbidden if p != "kotlin-solver/"]
        forbidden += list(KOTLIN_FORBIDDEN)
        allowed_files = list(KOTLIN_AGENT_FILES)
        allowed_dirs = list(KOTLIN_AGENT_DIRS)
    return {"allowed_paths": list(ALLOWED_PATHS),
            "allowed_files": allowed_files,
            "allowed_dirs": allowed_dirs,
            "forbidden_paths": forbidden,
            "solver_kinds": solver}


def build_row(rq: dict, cat) -> dict:
    """Строка очереди CR по заявке каталога (форма §3.1).

    В строке НЕТ поля `rule` (машинная форма правила авиакомпании) и НЕТ
    слов решения владельца — только обезличенная спецификация записи и
    виды исполнителей: строку читает облачная модель-исполнитель.

    С 08.09.2026 виды исполнителей решателя (`kotlin_*`) идут агенту
    наравне с Python-видами (решение 0.1, слово владельца): перечень
    `executors_deferred` остаётся ради формы строки и всегда пуст, а
    «нужен человек» при рождении бывает ровно в одном случае — заявка не
    называет ни одного вида исполнителя.
    """
    law = _law()
    request_id = str(rq.get("request_id") or "")
    cid = law.cr_key_of(request_id)
    section = str(rq.get("section") or "")
    record_id = str(rq.get("record_id") or "")
    draft = rq.get("draft")
    record_now = _record_of(cat, section, record_id)
    spec = draft if draft is not None else (record_now or {})
    kinds = _kinds_of(rq, record_now)
    deferred: list = []                  # 0.1: отложенных видов больше нет
    requested = list(kinds)
    hints = _hints(cat, requested)
    lists = work_lists(requested)
    candidate = f"catalog/candidates/{section}/{record_id}.json"
    decision = rq.get("decision") or {}
    at = _now()
    row = {
        "id": cid,
        "kind": "catalog-executor",
        "agent": "catalog-executor",
        "title": (f"Исполнитель записи {section}/{record_id}: "
                  f"{', '.join(requested) or 'видов не названо'}"),
        "summary": _summary(spec, kinds),
        "suggestion_ids": [],
        "priority": 1,
        "effort": 8,
        "status": "accepted",
        # Слов решения владельца в строке нет — только кто и когда:
        # слова уходят агенту вместе со строкой (0.6).
        "accepted_by": (f"владелец: решение по заявке {request_id} от "
                        f"{decision.get('at') or ''}"),
        "source": f"catalog:{request_id}",
        "country": None,
        "created_at": at,
        "updated_at": at,
        "record": {
            "section": section,
            "record_id": record_id,
            "request_id": request_id,
            "request_form": ("новая запись" if draft is not None
                             else "вид исполнителя"),
            "request_state_at_open": rq.get("state"),
            "decision": {"by": decision.get("by"), "at": decision.get("at")},
            "submitted_by": rq.get("submitted_by"),
            "source_kind": (rq.get("source") or {}).get("kind"),
            "catalog_version": cat.version(),
            "catalog_fingerprint": cat.fingerprint(),
        },
        "spec": {
            "draft": draft,
            "record_now": None if draft is not None else record_now,
            "executors_requested": [{"kind": k, "hint": hints.get(k, "")}
                                    for k in requested],
            # Поле остаётся ради формы строки и всегда пусто (0.1).
            "executors_deferred": [],
            "stand_forms": {k: STAND_FORMS[k] for k in requested
                            if k in STAND_FORMS},
            "acceptance": _acceptance(cat),
            "parity_law": PARITY_LAW,
        },
        "work": {
            "allowed_paths": lists["allowed_paths"],
            "allowed_files": lists["allowed_files"],
            "allowed_dirs": lists["allowed_dirs"],
            "forbidden_paths": lists["forbidden_paths"],
            "executor_files": (
                "CODE_FILES (vmp/stand_tasks.py) ∪ EXECUTOR_FILES "
                "(vmp/registry_check.py); виды решателя — пять файлов "
                "гейта символов (vmp/bindings_gate.py)"
                if lists["solver_kinds"] else
                "CODE_FILES (vmp/stand_tasks.py) ∪ "
                "EXECUTOR_FILES (vmp/registry_check.py)"),
            "candidate": candidate,
            "commands": (
                ([f"python -m vmp.solver_build check --records {record_id}"]
                 if lists["solver_kinds"] else [])
                + [
                    "python -m vmp.stand_tasks --write-results "
                    "vmp/stand_results.json",
                    "python -m vmp.bindings_build --out vmp/bindings.json "
                    "--stand-results vmp/stand_results.json",
                    "python -m vmp.bindings_gate vmp/bindings.json",
                    f"python -m template_catalog.candidates {candidate}",
                    # Тот же закон, что у пятого гейта: агент видит
                    # вердикт до конвейера, а не после (§2.5).
                    "python catalog/tools/parity.py --with-candidates",
                    "python -m pytest -q",
                ]),
        },
        "journal": [{
            "at": at, "by": SYNC_BY, "from": None, "to": "accepted",
            "words": (f"открыт по заявке {request_id} "
                      f"(состояние «{rq.get('state')}»)"),
        }],
    }
    if not requested:
        # Единственный случай рождения «нужен человек» (0.1): реализовать
        # нечего, потому что заявка не назвала вида. Квота реализации не
        # жжётся, ключ CR у заявки записан.
        row["status"] = "needs_human"
        row["impl"] = {
            "needs_human": True,
            "report": ("заявка не называет ни одного вида исполнителя: "
                       "реализовывать нечего — назовите вид в службе "
                       "каталога; ключ CR записан"),
        }
    return row


def press_rows(artifact: dict, rule_ids=()) -> tuple:
    """Строки заявок на ВИД по артефакту нажатия (`vmp-rule-gaps.json`,
    ключ `catalog`) — вход кнопки «Заявить в каталог».

    Артефакт ключа заявки не несёт по замыслу (в нём нет ничего, что
    раскрывало бы правило), поэтому ключ ПЕРЕСЧИТЫВАЕТСЯ законом
    читателя от раздела, записи и набора видов и сверяется с номером
    заявки из артефакта: расхождение — отказ словами, а не молчаливая
    подача не той заявки. Возврат: (строки, проблемы словами)."""
    law = _law()
    product = _product()
    only = {str(x) for x in (rule_ids or ())}
    rows, problems = [], []
    for row in (artifact.get("catalog") or ()):
        if not isinstance(row, dict) or row.get("form") != product.FORM_KIND:
            continue
        if only and str(row.get("rule_id")) not in only:
            continue
        key = law.kind_request_key(str(row.get("section")),
                                   str(row.get("record")),
                                   row.get("kinds") or ())
        rid = law.request_id_of(key)
        if row.get("request_id") and str(row.get("request_id")) != rid:
            problems.append(
                f"строка {row.get('rule_id')}: номер заявки артефакта "
                f"{row.get('request_id')} не сходится с законом ключа "
                f"({rid}) — подача не выполняется")
            continue
        rows.append({**row, "key": key, "request_id": rid})
    return rows, problems


# ------------------------------------------------------------- сверка

def _journal_words(row: dict) -> set:
    return {str(j.get("words")) for j in (row.get("journal") or ())
            if isinstance(j, dict)}


def _append_journal(crq, cid: str, row: dict, by: str, words: str,
                    to: str | None = None) -> bool:
    """Дописать строку журнала ОДИН раз: повтор сверки не плодит строк
    (идемпотентность §3.2)."""
    if words in _journal_words(row):
        return False
    journal = list(row.get("journal") or ())
    journal.append({"at": _now(), "by": by, "from": row.get("status"),
                    "to": to or row.get("status"), "words": words})
    crq._update_cr(cid, {"journal": journal})
    return True


def sync_catalog_requests(*, cat=None, write_back: bool = True,
                          by: str = SYNC_BY) -> dict:
    """Сверить заявки каталога с очередью CR (§3.2).

    Возврат: `{opened, keyed, rejected, refreshed, skipped, problems}` —
    ключи CR по каждому исходу и слова о том, чего сделать не удалось.
    """
    from vmp import catalog_access as _ca
    from . import change_requests as crq
    law = _law()
    out: dict = {"opened": [], "keyed": [], "rejected": [], "refreshed": [],
                 "skipped": [], "problems": []}
    if cat is None:
        cat = _ca.current()

    session = _session()
    mode, same = None, None
    if isinstance(session, dict):
        mode = session.get("mode")
        service_fp = str(session.get("fingerprint") or "")
        same = service_fp == cat.fingerprint()
        if write_back and mode == "edit" and not same:
            out["problems"].append(
                f"служба смотрит в другой каталог: у службы "
                f"{service_fp[:12]}…, у продукта {cat.fingerprint()[:12]}… "
                f"— ключи CR не пишутся")
            write_back = False
    else:
        out["problems"].append(
            "служба каталога недоступна: ключи CR не пишутся — строки "
            "очереди открываются по каталогу продукта")
    out["service"] = {"mode": mode, "same_catalog": same}

    internal = crq._repo_is_internal_contour()
    if internal:
        out["problems"].append(
            "внутренний контур: реализация CR каталога здесь запрещена — "
            "строки очереди не открываются, сверка даёт только отчёт")

    rows = {str(r.get("id")): r for r in crq.list_crs()
            if isinstance(r, dict)}
    said_view = False
    for rq in cat.requests():
        if not isinstance(rq, dict):
            continue
        request_id = str(rq.get("request_id") or "")
        state = rq.get("state")
        cid, words = _cr_key_or_words(law, request_id)
        if cid is None:
            out["problems"].append(words)
            continue
        row = rows.get(cid)
        if state in KEYED_STATES:
            if state == "исполнитель заявлен" and rq.get("cr_key") != cid:
                out["problems"].append(
                    f"ключ CR заявки {request_id} не по закону "
                    f"({rq.get('cr_key')!r} вместо {cid}) — CR не "
                    f"открывается, разберитесь на экране «Заявки»")
                continue
            if internal:
                out["skipped"].append(cid)
            else:
                _open_or_refresh(crq, cat, rq, cid, row, by, out)
            if state == "принята" and write_back and mode == "edit":
                res = _product().service_client.put_cr_key(request_id, cid)
                if res.get("ok"):
                    out["keyed"].append(cid)
                else:
                    out["problems"].append(
                        f"ключ CR {cid} заявке {request_id} записать не "
                        f"удалось: {res.get('words')}")
            elif state == "принята" and mode == "view" and not said_view:
                said_view = True
                out["problems"].append(
                    "служба каталога открыта в просмотре: ключ CR пишет "
                    "рабочее место владельца — здесь строки очереди "
                    "открываются по каталогу, каталог не меняется")
        elif state == "отклонена":
            if row is None:
                continue
            status = row.get("status")
            if status in OPEN_CR_STATUSES:
                crq.reject_cr(cid, by,
                              f"заявка {request_id} отклонена владельцем",
                              catalog_mirror=True)
                out["rejected"].append(cid)
            elif status == "implementing":
                if _append_journal(
                        crq, cid, row, by,
                        f"заявка {request_id} отклонена владельцем — "
                        f"отклонить после завершения работы агента"):
                    out["refreshed"].append(cid)
                out["skipped"].append(cid)
            else:
                if _append_journal(
                        crq, cid, row, by,
                        f"заявка {request_id} отклонена владельцем "
                        f"(строка уже {status})"):
                    out["refreshed"].append(cid)
        elif state == "есть":
            if row is not None and _append_journal(
                    crq, cid, row, by,
                    f"заявка {request_id} в состоянии «есть»"):
                out["refreshed"].append(cid)
    return out


#: Слова, по которым узнаётся строка ПРЕЖНЕЙ дорожки «6-К»: её закрыл
#: мгновенный «нужен человек» рождения, а не работа агента (0.1).
OLD_SOLVER_TRACK_MARKS = ("6-К", "исполнители решателя (kotlin_*)")

#: Слова журнала при переоткрытии такой строки: решение по ней уже
#: принято, и просить владельца жать «принять» второй раз нельзя.
REOPEN_WORDS = ("дорожка «6-К» отменена — слово владельца 08.09.2026: "
                "исполнителей решателя пишет тот же агент тем же CR; "
                "строка переоткрыта принятой")


def _old_solver_track(row: dict | None) -> bool:
    """Строка прежней дорожки: «нужен человек» рождения со словами про
    исполнителей решателя и БЕЗ ветки (работы агента не было — стирать
    нечего, переоткрытие ничего не теряет)."""
    if not isinstance(row, dict) or row.get("status") != "needs_human":
        return False
    impl = row.get("impl") or {}
    if impl.get("branch"):
        return False
    report = str(impl.get("report") or "")
    return any(mark in report for mark in OLD_SOLVER_TRACK_MARKS)


#: «Нужен человек» рождения второго рода (0.1): заявка не назвала вида —
#: строка закрыта мгновенно, без работы агента. Когда виды появились
#: (заявка правлена в службе либо чтение видов из черновика починено),
#: сверка переоткрывает строку принятой: решение по заявке уже есть.
NO_KIND_MARK = "не называет ни одного вида исполнителя"
NO_KIND_REOPEN_WORDS = ("виды исполнителей названы — строка переоткрыта "
                        "принятой (решение по заявке владелец уже принял)")


def _birth_needs_human(row: dict | None) -> str | None:
    """Слова переоткрытия для строки «нужен человек» РОЖДЕНИЯ — без ветки
    (работы агента не было, терять нечего): прежняя дорожка «6-К» либо
    «видов не названо». Иначе None: строку, где агент работал, сверка
    молча не переоткрывает."""
    if not isinstance(row, dict) or row.get("status") != "needs_human":
        return None
    impl = row.get("impl") or {}
    if impl.get("branch"):
        return None
    report = str(impl.get("report") or "")
    if any(mark in report for mark in OLD_SOLVER_TRACK_MARKS):
        return REOPEN_WORDS
    if NO_KIND_MARK in report:
        return NO_KIND_REOPEN_WORDS
    return None


def _refresh_catalog_stamp(crq, row: dict | None, new_row: dict, cid: str,
                           by: str, out: dict) -> bool:
    """Каталог сменился под ОТКРЫТОЙ строкой (выпуск между открытием CR и
    реализацией): привязка строки к каталогу (`record.catalog_version` /
    `catalog_fingerprint`, спецификация, работа) обновляется по живому
    каталогу со словами в журнале — иначе предпосылка реализации честно
    отказывает «каталог в клоне не тот, по которому открыт CR», а
    сверка молчит, потому что заголовок строки не изменился (живой
    случай 09.09.2026: CR duty_series_limit открыт на 0.2.0, выпуск
    0.3.0, повторная реализация после «нужен человек»). Строка в
    работе или закрытая не трогается: её свидетельства принадлежат
    своему каталогу."""
    if not isinstance(row, dict) or row.get("status") not in OPEN_CR_STATUSES:
        return False
    old = (row.get("record") or {})
    new = (new_row.get("record") or {})
    old_fp = str(old.get("catalog_fingerprint") or "")
    new_fp = str(new.get("catalog_fingerprint") or "")
    if not new_fp or old_fp == new_fp:
        return False
    journal = list(row.get("journal") or ())
    journal.append({"at": _now(), "by": by, "from": row.get("status"),
                    "to": row.get("status"),
                    "words": (f"каталог сменился ({old.get('catalog_version')} "
                              f"{old_fp[:12]}… → {new.get('catalog_version')} "
                              f"{new_fp[:12]}…): привязка строки к каталогу "
                              f"обновлена по живому; реализация — на живом "
                              f"каталоге")})
    crq._update_cr(cid, {"record": new_row["record"], "spec": new_row["spec"],
                         "work": new_row["work"], "journal": journal})
    if cid not in out["refreshed"]:
        out["refreshed"].append(cid)
    return True


def _open_or_refresh(crq, cat, rq: dict, cid: str, row: dict | None,
                     by: str, out: dict) -> None:
    """Открыть строку CR по заявке либо освежить существующую.

    Проверка «строка уже есть» делается ПОД локом очереди (`_add_crs`
    с первичным ключом `id`), а не чтением до записи: два одновременных
    вызова сверки иначе разошлись бы между чтением и записью."""
    new_row = build_row(rq, cat)
    try:
        added = crq._add_crs([new_row], unique_by_id=True)
    except crq.DuplicateChangeRequest:
        row = row if row is not None else crq.get_cr(cid)
        status = (row or {}).get("status")
        if status in OPEN_CR_STATUSES:
            patch = {k: new_row[k] for k in ("title", "summary", "spec",
                                             "record", "work")}
            journal = list((row or {}).get("journal") or ())
            words = (f"строка обновлена по заявке {rq.get('request_id')}: "
                     f"спецификация правлена в каталоге")
            to = status
            reopen = _birth_needs_human(row)
            if reopen and new_row["status"] == "accepted":
                # Переоткрытие (0.1): решение по заявке владелец уже
                # принял — второй отмашки в очереди не просим, работы
                # агента в строке нет, терять нечего.
                to, words = "accepted", reopen
                patch["status"] = "accepted"
                patch["impl"] = None
            journal.append({
                "at": _now(), "by": by, "from": status, "to": to,
                "words": words})
            patch["journal"] = journal
            crq._update_cr(cid, patch)
            out["refreshed"].append(cid)
        else:
            out["problems"].append(
                f"строка CR {cid} в статусе «{status}» несёт иное "
                f"содержание, чем заявка {rq.get('request_id')}: молчаливо "
                f"переписать её нельзя — разберитесь вручную")
        return
    if added:
        out["opened"].append(cid)
        return
    # Содержание совпало — строку `_add_crs` пропустил молча. Прежняя
    # дорожка «6-К» переоткрывается и здесь: у неё заголовок мог совпасть
    # (например, у заявки на один вид решателя без правки спецификации).
    row = row if row is not None else crq.get_cr(cid)
    _refresh_catalog_stamp(crq, row, new_row, cid, by, out)
    row = crq.get_cr(cid) if row is not None else None
    reopen = _birth_needs_human(row)
    if reopen and new_row["status"] == "accepted":
        journal = list((row or {}).get("journal") or ())
        journal.append({"at": _now(), "by": by, "from": "needs_human",
                        "to": "accepted", "words": reopen})
        crq._update_cr(cid, {"status": "accepted", "impl": None,
                             "spec": new_row["spec"],
                             "work": new_row["work"],
                             "title": new_row["title"],
                             "journal": journal})
        out["refreshed"].append(cid)


# ---------------------------------------------------------------- вид

def _candidate_path(rel: str):
    from . import registry
    return registry.REPO_ROOT / str(rel).replace("\\", "/")


def catalog_view() -> dict:
    """Вид «Каталог» вкладки «Изменения»: заявки × CR × кандидаты ×
    очередь COSMOS. Ничего не пишет — только показывает."""
    from vmp import catalog_access as _ca
    from . import change_requests as crq
    law = _law()
    cat = _ca.current()
    cfg = configured()
    session = _session()
    mode, same = None, None
    if isinstance(session, dict):
        mode = session.get("mode")
        same = str(session.get("fingerprint") or "") == cat.fingerprint()
    crs = [r for r in crq.list_crs()
           if isinstance(r, dict) and r.get("agent") in crq.CATALOG_AGENTS]
    by_key = {str(r.get("id")): r for r in crs}
    requests = []
    for rq in cat.requests():
        if not isinstance(rq, dict):
            continue
        rid = str(rq.get("request_id") or "")
        cid, words = _cr_key_or_words(law, rid)
        row = by_key.get(cid) if cid else None
        requests.append({
            "request_id": rid,
            "state": rq.get("state"),
            "section": rq.get("section"),
            "record_id": rq.get("record_id"),
            "submitted_by": rq.get("submitted_by"),
            "source_kind": (rq.get("source") or {}).get("kind"),
            "ref": (rq.get("source") or {}).get("ref"),
            "kinds": _kinds_of(rq, _record_of(cat, str(rq.get("section")),
                                              str(rq.get("record_id")))),
            "cr_key": rq.get("cr_key"),
            "cr_key_expected": cid,
            "cr_key_words": words,
            "cr_status": (row or {}).get("status"),
            "link": request_link(rid),
        })
    candidates = []
    for row in crs:
        impl = row.get("impl") or {}
        rel = impl.get("candidate")
        if not rel:
            continue
        in_tree = _candidate_path(rel).is_file()
        candidates.append({
            "path": rel, "cr_key": row.get("id"),
            "branch": impl.get("branch"),
            "evidence_commit": impl.get("evidence_commit"),
            "where": ("в дереве" if in_tree
                      else f"в ветке {impl.get('branch') or 'cr-…'} "
                           f"(не слита)"),
        })
    outbox = {"pending": None, "path": None, "words": None}
    auto = None
    try:
        product = _product()
        root = product.outbox_root()
        # «Ожидает доставки» считает ОДИН закон — продуктовый
        # `pending()`: файлы заявок без ответа службы. Свой счёт по
        # `*.json` считал бы и файлы ответов `<номер>.reply.json`,
        # то есть после успешной подачи показывал бы вдвое больше.
        outbox = {"pending": len(product.pending(root)),
                  "path": str(root), "words": None}
        auto = bool(product.auto_enabled())
    except CatalogPipelineError as exc:
        outbox["words"] = str(exc)
    except Exception as exc:                             # noqa: BLE001
        outbox["words"] = f"очередь заявок COSMOS не читается: {exc}"
    return {
        "service": {"api_url": cfg["api_url"], "ui_url": cfg["ui_url"],
                    "token": cfg["token"], "mode": mode,
                    "same_catalog": same},
        "catalog": _ca.stamp(),
        "requests": requests,
        "crs": crs,
        "candidates": candidates,
        "outbox": outbox,
        "auto": auto,
    }


# ------------------------------------------------------------ старт

#: Пауза до первой сверки при старте: приложение отвечает раньше, чем
#: конвейер сходит в службу (старт не ждёт сети).
START_DELAY_S = 2.0


def start_sync_thread():
    """Сверка при старте — отдельной нитью и только при настроенном
    адресе службы (`CREW_CATALOG_API_URL`): без настройки машинного
    канала фоновых походов в сеть не бывает. Отказ службы — строка в
    журнал процесса, не падение старта."""
    try:
        if not configured()["api_url"]:
            return None
    except Exception as exc:                             # noqa: BLE001
        print(f"COSMOS: сверка заявок каталога не запущена: {exc}",
              flush=True)
        return None

    def once():
        time.sleep(START_DELAY_S)
        try:
            out = sync_catalog_requests()
        except Exception as exc:                         # noqa: BLE001
            print(f"COSMOS: сверка заявок каталога не выполнена: {exc}",
                  flush=True)
            return
        print("COSMOS: сверка заявок каталога — открыто "
              f"{len(out['opened'])}, ключей записано {len(out['keyed'])}, "
              f"отклонено {len(out['rejected'])}"
              + ("; " + "; ".join(out["problems"]) if out["problems"] else ""),
              flush=True)

    t = threading.Thread(target=once, name="catalog-sync", daemon=True)
    t.start()
    return t
