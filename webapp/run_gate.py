# -*- coding: utf-8 -*-
"""Гейт «от пакета»: нажатия COSMOS идут только у ОБЪЯВЛЕННОГО прогона.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, разделы
1–2 (прогон рождается объявлением), 4 (хозяин записи — та система, где
прогон начался), 10 (половина COSMOS шага 2) и 11 (половина CDPM: тела
ручек хранителя записи). Словарь полей — общий носитель
`vmp/run_record.py`; здесь он не повторяется, а читается.

Что охраняет гейт.

  * Пакет несёт НОМЕР ЗАПИСИ (`manifest.record_id`) — прогон объявлен
    хозяином (панель CDPM либо раннер наборов): COSMOS наследует голову
    ссылкой и складывает рядом свой фрагмент `run-record.cosmos.json` —
    объявимую часть СВОЕЙ среды (каталог, привязки, модели по ролям,
    решатели, код, библиотеки).
  * Пакет без номера записи — прогон ЧАСТНЫЙ (вход COSMOS от готового
    ВМП, решение владельца № 2 от 10.09.2026): нажатие отказывает кодом
    `run-not-declared`, пока человек или машина не объявит прогон
    ручкой `POST /api/run/declare`. Тогда хозяин записи — COSMOS, и
    `run-record.json` лежит в папке нажатий пакета.
  * Перед КАЖДЫМ нажатием — сверка «объявлено / на деле»: привязки
    исполнителей выпущены под каталог пакета; сертификатор и читатель
    ИДАК те же, что названы во фрагменте; решатель назван; коммит кода
    тот же (расхождение кода — предупреждение, не отказ: код меняется
    раскатом, а голова записи неизменна).

Чего гейт НЕ делает. Он не повторяет сторожа дрейфа каталога (его
исход `CHECK_STOP` уже останавливает нажатие в `package_runs.press`) и
не проверяет доступность решателя — это дело самого нажатия. Он также
не берёт ничего «из дерева по умолчанию»: чего среда назвать не может,
называется кодом причины и словами, а не тихим умолчанием.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path

from vmp import run_record as rr

#: Схема фрагмента COSMOS в папке нажатий пакета.
FRAGMENT_SCHEMA = "cosmos-run-record-fragment/v1"
#: Имя файла фрагмента.
FRAGMENT_FILE = "run-record.cosmos.json"
#: Схема ответа машинной ручки среды `GET /api/run/environment` (договор
#: с CDPM 11.09.2026): ровно то, что COSMOS кладёт фрагментом среды, но
#: отданное ДО объявления — хозяину записи для головы.
ENVIRONMENT_SCHEMA = "cosmos-run-environment/v1"

#: Хозяин записи, когда прогон объявлен НЕ здесь: панель CDPM либо
#: раннер наборов — по манифесту пакета их не различить, и различать не
#: нужно: обеим COSMOS дописывает журнал одной ручкой `record/append`.
OWNER_MANIFEST = "панель/раннер по манифесту"
#: Хозяин записи частного прогона от ВМП.
OWNER_COSMOS = "cosmos"

#: Базовый адрес ручек записи на панели CDPM — запасной путь площадки,
#: когда манифест пакета не несёт `record_api` (пакеты до 10.09.2026).
RECORD_API_ENV = "CREW_CDPM_RECORD_API"
#: Площадка прогона словом словаря записи (local | test | stage | prod).
CONTOUR_ENV = "CREW_CONTOUR"

#: План этапов частного прогона по умолчанию: два нажатия и две сверки
#: с ИДАК — те же имена, что кладёт в журнал `run_journal`.
DEFAULT_PLANNED_STAGES = ("пейринг", "сверка с ИДАК", "ростер",
                          "сверка с ИДАК")

#: Роли моделей COSMOS в записи о прогоне.
ROLE_CERTIFIER = "сертификатор"
ROLE_SOURCE_READER = "читатель ИДАК"

# ---------------------------------------------------------------- коды
#: Прогон не объявлен: пакет без номера записи.
CODE_NOT_DECLARED = "run-not-declared"
#: Привязки исполнителей выпущены под другой каталог, чем у пакета.
CODE_BINDINGS = "bindings-catalog-mismatch"
#: Модели по ролям сменились после объявления.
CODE_MODELS = "models-changed"
#: Повторное объявление того же пакета.
CODE_ALREADY = "already-declared"
#: Объявление не по словарю полей.
CODE_INVALID = "declaration-invalid"
#: Текущего пакета постановки нет.
CODE_PACKAGE_MISSING = "package-missing"
#: Запись о прогоне не легла в папку нажатий (беда записи файла).
CODE_RECORD_NOT_WRITTEN = "record-not-written"
#: Допуск запроса сверки шире допуска пакета.
CODE_READER_SCOPE = "reader-scope-mismatch"
#: Читателя ИДАК сейчас не вызвать (нет модели, канал невозможен).
CODE_READER_UNAVAILABLE = "reader-unavailable"

#: Исходы сверки среды перед нажатием.
CHECK_OK = "сошлось"
CHECK_WARN = "предупреждение"
CHECK_REFUSED = "расхождение"


class RunGateError(RuntimeError):
    """Отказ гейта: код причины и слова расхождения.

    Слова — перечень: расхождений среды бывает несколько, и человеку
    нужны все, а не первое. `status` — код ответа ручки (409 отказ по
    правилу, 422 объявление не по словарю)."""

    def __init__(self, code: str, words, status: int = 409):
        self.code = str(code)
        self.words = [str(w) for w in (words if isinstance(words, (list, tuple))
                                       else [words])]
        self.status = int(status)
        super().__init__(self.text())

    def text(self) -> str:
        return "; ".join(self.words) or self.code

    def payload(self) -> dict:
        """Тело ответа ручки: `detail` строкой (его читают сегодняшние
        клиенты нажатия), рядом — код и слова перечнем."""
        return {"detail": self.text(), "code": self.code, "words": self.words}


# ------------------------------------------------------------- мелочи
def _text(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def write_json_atomic(path: Path, doc) -> Path:
    """Файл JSON атомарно: сперва соседний временный, затем замена.
    Фрагмент переписывается после каждой сверки среды, и оборванная
    запись оставила бы половину строки вместо объявления."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def read_json(path: Path):
    """Словарь из файла; файла нет или он не разбирается — None."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def site_contour() -> str:
    """Площадка прогона словом словаря записи: переменная площадки,
    иначе «local». Значение вне словаря площадкой не признаётся —
    выдумывать контур по имени машины гейт не станет."""
    got = (os.environ.get(CONTOUR_ENV) or "").strip().lower()
    return got if got in rr.CONTOURS else rr.CONTOURS[0]


def record_api_base() -> str | None:
    """Базовый адрес ручек записи на панели CDPM с площадки."""
    return _text(os.environ.get(RECORD_API_ENV))


def _manifest_of(pkg) -> dict:
    """Манифест пакета: принимается и разбор `task_package.load`
    (ключ `manifest`), и сам манифест словарём."""
    if not isinstance(pkg, dict):
        return {}
    man = pkg.get("manifest")
    if isinstance(man, dict):
        return man
    return pkg


# --------------------------------------------------------- голова записи
def head_of(pkg) -> dict:
    """Голова записи ССЫЛКОЙ из манифеста пакета.

    `record_api` — полный адрес записи у хозяина: манифест несёт его сам
    (договор с CDPM, раздел 11.2), а пакеты до 10.09.2026 — нет, и тогда
    адрес строится от переменной площадки: `<база>/api/runs/<прогон>/
    record`. Ни того ни другого — сшивать некуда, и это состояние
    называется кодом, а не пустотой."""
    man = _manifest_of(pkg)
    record_id = _text(man.get("record_id"))
    source_run = _text(man.get("source_run"))
    api = _text(man.get("record_api"))
    api_code = None
    if not api:
        base = record_api_base()
        if base and source_run:
            api = "%s/api/runs/%s/record" % (base.rstrip("/"), source_run)
            api_code = "record-api-from-site"
        else:
            api_code = ("record-api-missing" if not base
                        else "record-api-without-source-run")
    head = {"record_id": record_id,
            "declaration_sha256": _text(man.get("declaration_sha256")),
            "record_api": api,
            "owner": OWNER_MANIFEST if record_id else None,
            "source_run": source_run,
            "customer": _text(man.get("customer"))}
    if api_code:
        head["record_api_code"] = api_code
    if record_id and not head["declaration_sha256"]:
        # Отпечаток головы нужен ручке append хозяина: без него запись
        # этапов не примут. Пакет без него — не повод отказать нажатию,
        # но молчать об этом нельзя.
        head["declaration_sha256_code"] = "manifest-without-declaration-sha"
    return head


def record_path(wd) -> Path:
    """Файл записи о прогоне в папке нажатий пакета (хозяин — COSMOS)."""
    return Path(wd) / rr.RECORD_FILE


def local_record(wd) -> dict | None:
    """Запись частного прогона из папки нажатий; нет — None."""
    return read_json(record_path(wd))


def head_for(wd, manifest) -> dict | None:
    """Голова прогона этого пакета: из манифеста (объявил хозяин) либо
    из местной записи (объявил COSMOS). Не объявлен — None."""
    head = head_of(manifest)
    if head["record_id"]:
        return head
    doc = local_record(wd)
    d = (doc or {}).get("declaration") if isinstance(doc, dict) else None
    if not isinstance(d, dict) or not d.get("run_id"):
        return None
    man = _manifest_of(manifest)
    return {"record_id": str(d["run_id"]),
            "declaration_sha256": _text(doc.get("declaration_sha256")),
            # Частный прогон хозяин ведёт сам: дописывать некуда и незачем.
            "record_api": None,
            "owner": OWNER_COSMOS,
            "source_run": _text(man.get("source_run")),
            "customer": _text(man.get("customer"))}


def declare_refusal_words() -> list:
    """Слова отказа нажатию у необъявленного прогона."""
    return ["прогон не объявлен: пакет без номера записи — объявите "
            "прогон от пакета"]


# --------------------------------------------------- читатель ИДАК
def reader_llm_scope(pkg) -> str:
    """Допуск моделей пакета для читателя сверки — строго по сырому
    значению `manifest.kit.llm_scope` и словарю `cdpm.kit.LLM_SCOPES`
    (без `.strip().lower()`: « Any » — не допуск). Отсутствует /
    неизвестен / kit не словарь → local-only (S06: согласовано CDPM,
    COSMOS и GPT-005; наружу ничего не уходит без явного допуска)."""
    try:
        from cdpm.kit import LLM_SCOPES as _scopes
    except Exception:                                      # noqa: BLE001
        _scopes = ("any", "local-only")
    kit = _manifest_of(pkg).get("kit")
    raw = kit.get("llm_scope") if isinstance(kit, dict) else None
    return raw if raw in _scopes else "local-only"


def reader_identity(scope: str, model: str | None = None) -> dict:
    """Читатель клауз для сверки с документами — ОДИН закон с
    сертификатором (`package_runs.certifier_identity`), политика S06
    плана единой версии (06.09.2026).

    Допуск «any» → облако: закреплённый профиль площадки
    (`CREW_CERTIFIER_MODEL`) — умолчание (`config_source="default"`),
    канал — `CREW_CERTIFIER_CHANNEL`; явная модель запроса — override в
    пределах допуска. «local-only» либо допуск не объявлен → только
    локальная модель установки; облачная модель в запросе при local-only
    — расхождение словами, а не молчаливая подмена.

    Расхождение возвращается ПОЛЯМИ `error`/`error_status`, а не
    исключением: ручка сверки превращает их в отказ, а объявление среды
    в записи о прогоне обязано назвать читателя даже тогда, когда
    вызвать его сейчас нельзя (иначе роль пропала бы из объявления)."""
    from . import package_runs as pr
    if scope != "any":
        ident = pr.certifier_identity(scope)
        local_model = ident.get("model")
        if model and model != local_model:
            return {"scope": scope, "requested": "local",
                    "model": local_model, "channel": "local",
                    "config_source": "default",
                    "error": ("модель %r в запросе при допуске local-only "
                              "не принимается: читатель — локальная модель "
                              "установки (%s), наружу ничего не уходит"
                              % (model, local_model or "не задана")),
                    "error_status": 400}
        return {"scope": scope, "requested": "local", "model": local_model,
                "channel": "local", "config_source": "default"}
    ident = pr.certifier_identity("any")
    if ident.get("error"):
        return {"scope": "any", "requested": "cloud",
                "model": ident.get("model"), "channel": ident.get("channel"),
                "config_source": "default",
                "error": "читатель сверки: %s" % ident["error"],
                "error_status": 409}
    site_model = ident.get("model")
    out = {"scope": "any", "requested": "cloud", "model": site_model,
           "channel": ident.get("channel"), "config_source": "default"}
    if model and model != site_model:
        out.update({"model": model, "config_source": "explicit_override",
                    "model_requested": model, "model_site": site_model})
    return out


def source_reader(pkg, model: str | None = None,
                  scope: str | None = None) -> dict:
    """Читатель сверки для ручки `/api/source-check/run`: тот же закон
    (`reader_identity`) плюс рубеж допуска запроса — расширять допуск
    пакета запросом нельзя никогда. Расхождение — RunGateError со
    статусом ответа."""
    eff = reader_llm_scope(pkg)
    if scope is not None and str(scope) != eff:
        raise RunGateError(
            CODE_READER_SCOPE,
            ["запрошенный допуск %r не равен допуску пакета %r — сверка "
             "идёт только в пределах допуска пакета" % (scope, eff)],
            status=400)
    got = reader_identity(eff, model)
    if got.get("error"):
        raise RunGateError(CODE_READER_UNAVAILABLE, [got["error"]],
                           status=int(got.get("error_status") or 409))
    return got


# ------------------------------------------------------- среда COSMOS
@functools.lru_cache(maxsize=1)
def _libraries() -> dict:
    """Библиотеки площадки: версия интерпретатора, перечень пакетов с
    версиями и отпечаток перечня. Перебор установленных пакетов —
    дорогая работа, а внутри процесса он неизменен: кэш на процесс."""
    try:
        from vmp import run_passport as rp
        py = rp.python_block()
        return {"python": py.get("version"),
                "packages_sha256": py.get("packages_sha256"),
                "packages": list(py.get("packages") or [])}
    except Exception as e:                                 # noqa: BLE001
        return {"python": None, "packages_sha256": None, "packages": [],
                "reason_code": "libraries-unreadable",
                "words": "перечень библиотек не прочитан: %s" % str(e)[:200]}


def _catalog_block() -> dict:
    """Каталог шаблонов, которым идёт продукт: название (имя папки),
    версия, отпечаток, путь. Каталога нет — код причины и слова."""
    try:
        from vmp import catalog_access as ca
        st = ca.stamp()
        return {"name": Path(str(st.get("path") or "")).name or None,
                "version": st.get("version"),
                "fingerprint": st.get("fingerprint"),
                "path": st.get("path")}
    except Exception as e:                                 # noqa: BLE001
        return {"name": None, "version": None, "fingerprint": None,
                "path": None, "reason_code": "catalog-unavailable",
                "words": "каталог шаблонов недоступен: %s" % str(e)[:200]}


def _bindings_block() -> dict:
    """Привязки исполнителей COSMOS: под какой каталог выпущены и где
    лежат. Файла нет — состояние словами, а не пустота."""
    try:
        from vmp import catalog_access as ca
        b = ca.bindings_summary()
        out = {"catalog_version": b.get("catalog_version"),
               "catalog_fingerprint": b.get("catalog_fingerprint"),
               "path": b.get("file")}
        if not out["catalog_version"]:
            out["reason_code"] = "bindings-missing"
            out["words"] = ("привязок исполнителей нет: файл %s не читается"
                            % b.get("file"))
        return out
    except Exception as e:                                 # noqa: BLE001
        return {"catalog_version": None, "catalog_fingerprint": None,
                "path": None, "reason_code": "bindings-unreadable",
                "words": "привязки не прочитаны: %s" % str(e)[:200]}


def _models_block(scope: str) -> list:
    """Модели COSMOS по ролям: сертификатор и читатель ИДАК — обе по
    допуску пакета и по одному закону. Модель назвать нельзя (нет
    настройки, канал невозможен) — роль остаётся в объявлении с пустой
    моделью и словами причины: молчание было бы потерей роли."""
    from . import package_runs as pr
    out = []
    try:
        cert = pr.certifier_passport(pr.certifier_identity(scope))
    except Exception as e:                                 # noqa: BLE001
        cert = {"model": None, "provider": None, "channel": None,
                "error": "сертификатор не назван: %s" % str(e)[:200]}
    row = {"role": ROLE_CERTIFIER, "model": cert.get("model"),
           "provider": cert.get("provider"), "channel": cert.get("channel"),
           "effort": cert.get("effort")}
    if cert.get("error"):
        row["words"] = cert["error"]
    out.append(row)
    reader = reader_identity(scope)
    row = {"role": ROLE_SOURCE_READER, "model": reader.get("model"),
           "provider": ("anthropic" if reader.get("requested") == "cloud"
                        else "ollama"),
           "channel": reader.get("channel"), "effort": None}
    if reader.get("error"):
        row["words"] = reader["error"]
    out.append(row)
    return out


def _solvers_block() -> list:
    """Решатели площадки: ростеринг — Timefold по файлам сборки (адрес и
    отпечаток jar), пейринг — движок в процессе, чья версия равна версии
    пакета интерпретатора."""
    from . import cosmos_runs as cr
    from . import package_runs as pr
    out = []
    try:
        env = cr.environment()
    except Exception as e:                                 # noqa: BLE001
        return [{"task": "rostering", "engine": None, "version": None,
                 "build_sha256": None, "reason_code": "environment-unreadable",
                 "words": "среда решателя не прочитана: %s" % str(e)[:200]}]
    solver = env.get("solver") if isinstance(env.get("solver"), dict) else {}
    build = solver.get("build") if isinstance(solver.get("build"), dict) else {}
    row = {"task": "rostering", "engine": solver.get("engine"),
           "version": build.get("timefold_version"),
           "build_sha256": build.get("app_jar_sha256"),
           "url": solver.get("url")}
    if not row["url"]:
        row["reason_code"] = "solver-url-missing"
        row["words"] = ("адрес решателя площадки не задан (%s)"
                        % pr.SOLVER_URL_ENV)
    out.append(row)
    try:
        versions = pr._engine_versions() or {}
    except Exception:                                      # noqa: BLE001
        versions = {}
    for engine in pr.ENGINES:
        pkg = pr._PAIRING_ENGINE_PACKAGE.get(engine)
        row = {"task": "pairing", "engine": engine,
               "version": versions.get(pkg) if pkg else None,
               "build_sha256": None}
        if not pkg:
            # У CPLEX версию называет только его собственный журнал
            # прогона — из процесса её не спросить.
            row["version_source_code"] = "engine-version-in-solver-log"
        out.append(row)
    return out


def _code_block() -> dict:
    """Коммит кода COSMOS."""
    from . import cosmos_runs as cr
    try:
        code = cr.environment().get("code") or {}
    except Exception as e:                                 # noqa: BLE001
        return {"cosmos_commit": None, "reason_code": "code-unreadable",
                "words": "коммит кода не прочитан: %s" % str(e)[:200]}
    out = {"cosmos_commit": code.get("commit")}
    if not out["cosmos_commit"]:
        out["reason_code"] = code.get("reason_code") or "no-git"
    return out


def cosmos_environment(scope: str = "local-only") -> dict:
    """Объявимая часть среды COSMOS по словарю записи о прогоне: каталог,
    привязки, модели по ролям, решатели, код, библиотеки.

    `scope` — допуск моделей ПАКЕТА: сертификатор и читатель ИДАК
    выбираются по нему, а не по площадке (дополнение к 1.14 от
    04.09.2026)."""
    return {"catalog": _catalog_block(),
            "bindings": _bindings_block(),
            "models": _models_block(scope),
            "solvers": _solvers_block(),
            "code": _code_block(),
            "libraries": _libraries()}


def own_models(rows) -> list:
    """Модели COSMOS строками ГОЛОВЫ записи: те же поля раскладки плюс
    слово системы (`system: "cosmos"`, словарь `rr.SYSTEMS`).

    Систему, названную вызывающим, строка сохраняет: чужую роль в свою
    не переписываем."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        if not item.get("system"):
            item["system"] = rr.SYSTEM_COSMOS
        out.append(item)
    return out


def environment_scope(scope: str | None = None) -> str:
    """Допуск моделей у ЗАПРОСА к ручке среды — тот же закон, что у
    читателя сверки (`reader_llm_scope`, политика S06): строго по
    словарю `cdpm.kit.LLM_SCOPES`, без `.strip().lower()`. Не назван
    либо вне словаря — local-only: наружу ничего не уходит без явного
    допуска."""
    try:
        from cdpm.kit import LLM_SCOPES as _scopes
    except Exception:                                      # noqa: BLE001
        _scopes = ("any", "local-only")
    return scope if scope in _scopes else "local-only"


def environment_answer(scope: str | None = None) -> dict:
    """Тело ручки `GET /api/run/environment`: объявимая среда COSMOS для
    ГОЛОВЫ записи — площадка, код, каталог и модели COSMOS по ролям.

    Зачем ручка. Голову чеканит ХОЗЯИН записи (панель CDPM либо раннер
    наборов), и до 11.09.2026 моделей COSMOS в ней не было вовсе
    (находка К5): хозяин знает только свои. Ручка отдаёт ровно то, что
    COSMOS кладёт фрагментом `cosmos_environment` карточки записи, но
    ДО объявления — чтобы сертификатор и читатель ИДАК попали в голову и
    вошли в её отпечаток. Кто спросил и не дождался — пишет в голову
    `models_note`, а не выдумывает модели (договор с CDPM 11.09.2026).

    Ручка ЧИТАЮЩАЯ: ничего не пишет, текущего пакета не требует и о нём
    не спрашивает — при объявлении пакета в COSMOS ещё нет.

    `scope` — допуск моделей НАБОРА: его знает хозяин записи, а не
    COSMOS, и по нему выбираются сертификатор и читатель ИДАК — так же,
    как при нажатии. Не назван — local-only."""
    scope = environment_scope(scope)
    env = cosmos_environment(scope)
    return {"schema": ENVIRONMENT_SCHEMA,
            "contour": site_contour(),
            "llm_scope": scope,
            "code": env["code"],
            "catalog": env["catalog"],
            "models": own_models(env["models"])}


# ---------------------------------------------------------- фрагмент
def fragment_path(wd) -> Path:
    return Path(wd) / FRAGMENT_FILE


def read_fragment(wd) -> dict | None:
    return read_json(fragment_path(wd))


def ensure_fragment(wd, head: dict, scope: str = "local-only",
                    actor: str | None = None) -> dict:
    """Фрагмент COSMOS в папке нажатий пакета — идемпотентно.

    Первое обращение гейта к пакету (нажатие либо карточка) кладёт
    фрагмент: голова ссылкой плюс объявление среды COSMOS. Дальше
    фрагмент не переписывается: объявление среды — тоже голова, и после
    старта она неизменна. Сменился номер записи (другой прогон в той же
    папке) — фрагмент пишется заново: это другой прогон."""
    wd = Path(wd)
    got = read_fragment(wd)
    if isinstance(got, dict) and (got.get("head") or {}).get("record_id") \
            == head.get("record_id"):
        return got
    doc = {"schema": FRAGMENT_SCHEMA,
           "head": dict(head),
           "cosmos_environment": cosmos_environment(scope),
           "declared_at": rr.now_iso(),
           "declared_by": _text(actor),
           "environment_check": {"at": None, "state": None, "words": []}}
    write_json_atomic(fragment_path(wd), doc)
    return doc


def _pair_words(what: str, declared, now) -> str:
    return "%s: объявлено %s / на деле %s" % (
        what, declared if declared not in (None, "") else "не названо",
        now if now not in (None, "") else "не названо")


def _models_by_role(rows) -> dict:
    out = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("role"):
            out[str(row["role"])] = row
    return out


def _model_words(row) -> str:
    row = row if isinstance(row, dict) else {}
    return "%s (%s, канал %s)" % (row.get("model") or "не названа",
                                  row.get("provider") or "провайдер не назван",
                                  row.get("channel") or "не назван")


def check_before_press(pkg, task: str | None = None, *, wd,
                       catalog_check: dict | None = None,
                       scope: str = "local-only",
                       actor: str | None = None) -> dict:
    """Сверка «объявлено / на деле» перед нажатием.

    Судится ровно то, что объявлено фрагментом и манифестом пакета:

      * каталог — исход сторожа дрейфа (`catalog_check`), полученный
        нажатием: гейт его не пересчитывает и не дублирует отказ;
      * привязки исполнителей — той же версии каталога, что у пакета
        (иначе отказ `bindings-catalog-mismatch`);
      * сертификатор и читатель ИДАК — те же, что во фрагменте (иначе
        отказ `models-changed` словами «объявлено … / на деле …»);
      * решатель — адрес назван (доступность судит само нажатие);
      * код — тот же коммит (иначе ПРЕДУПРЕЖДЕНИЕ: код меняется
        раскатом, и запись говорит об этом, а не запирает площадку).

    Возвращает фрагмент с дописанным исходом сверки; отказ —
    RunGateError."""
    wd = Path(wd)
    man = _manifest_of(pkg)
    head = head_for(wd, man)
    if head is None:
        raise RunGateError(CODE_NOT_DECLARED, declare_refusal_words())
    fragment = ensure_fragment(wd, head, scope, actor)
    declared = fragment.get("cosmos_environment") or {}
    words: list = []
    warnings: list = []
    try:
        now = cosmos_environment(scope)
    except Exception as e:                                 # noqa: BLE001
        # Сверка среды сломалась — это не повод запереть площадку:
        # состояние называется словами и едет во фрагмент.
        return _stamp_check(wd, fragment, CHECK_WARN,
                            ["среда COSMOS не сверена: %s" % str(e)[:300]])

    # (1) Каталог: исход сторожа дрейфа как есть — гейт его не
    # пересчитывает и НЕ отказывает вторым голосом. Остановка каталога
    # — дело нажатия (`package_runs.press` отвечает своим кодом
    # `catalog-version-mismatch`); здесь она остаётся словами в записи
    # сверки, чтобы карточка называла причину.
    check = catalog_check if isinstance(catalog_check, dict) else {}
    if check.get("words"):
        warnings.append(str(check["words"]))

    # (2) Привязки — под каталог ПАКЕТА.
    pkg_catalog = man.get("catalog") if isinstance(man.get("catalog"), dict) \
        else {}
    pkg_version = _text(pkg_catalog.get("version"))
    bind_version = _text((now.get("bindings") or {}).get("catalog_version"))
    if pkg_version and bind_version and pkg_version != bind_version:
        words.append(_pair_words(
            "привязки исполнителей выпущены под другой каталог, чем пакет",
            pkg_version, bind_version))
    elif not bind_version:
        warnings.append((now.get("bindings") or {}).get("words")
                        or "привязки исполнителей не названы")

    # (3) Модели по ролям — те же, что объявлены.
    was, is_now = _models_by_role(declared.get("models")), \
        _models_by_role(now.get("models"))
    changed = []
    for role in (ROLE_CERTIFIER, ROLE_SOURCE_READER):
        a, b = was.get(role), is_now.get(role)
        if a is None or b is None:
            continue
        keys = ("model", "provider", "channel")
        if [a.get(k) for k in keys] == [b.get(k) for k in keys]:
            continue
        if not a.get("model"):
            # Роль объявлена БЕЗ модели (настройки площадки тогда не
            # было, и слова причины лежат рядом): назвать её теперь —
            # не подмена объявленного, а закрытие пробела. Отказывать
            # здесь значило бы запереть пакет навсегда.
            warnings.append(_pair_words("модель роли «%s» названа после "
                                        "объявления" % role,
                                        _model_words(a), _model_words(b)))
            continue
        changed.append(_pair_words("модель роли «%s»" % role,
                                   _model_words(a), _model_words(b)))

    # (4) Решатель: адрес назван.
    for row in now.get("solvers") or []:
        if isinstance(row, dict) and row.get("reason_code") == "solver-url-missing":
            warnings.append(str(row.get("words") or "адрес решателя не задан"))

    # (5) Код: коммит тот же — иначе предупреждение, не отказ.
    was_code = (declared.get("code") or {}).get("cosmos_commit")
    now_code = (now.get("code") or {}).get("cosmos_commit")
    if was_code and now_code and was_code != now_code:
        warnings.append(_pair_words("коммит кода COSMOS сменился раскатом",
                                    rr.short(was_code), rr.short(now_code)))

    # Отказ один, слов в нём — сколько расхождений: человеку нужны все.
    # Код называет ПЕРВОЕ расхождение по порядку сверки; модели старше
    # привязок только тем, что их расхождение чаще рукотворное.
    if words or changed:
        all_words = changed + words + warnings
        _stamp_check(wd, fragment, CHECK_REFUSED, all_words)
        raise RunGateError(CODE_MODELS if changed else CODE_BINDINGS,
                           all_words)
    return _stamp_check(wd, fragment,
                        CHECK_WARN if warnings else CHECK_OK, warnings)


def _stamp_check(wd, fragment: dict, state: str, words: list) -> dict:
    """Исход сверки — во фрагмент (последний исход, а не история: она
    живёт записями журнала)."""
    fragment["environment_check"] = {"at": rr.now_iso(), "state": state,
                                     "words": [str(w) for w in words]}
    try:
        write_json_atomic(fragment_path(wd), fragment)
    except OSError:
        pass
    return fragment


# ------------------------------------------------------------- нажатие
def check_press(task: str | None = None, actor: str | None = None) -> dict | None:
    """Крючок ручки нажатия: прогон объявлен и среда сошлась.

    Текущего пакета нет — гейт молчит: об этом своими словами скажет
    само нажатие (у него один резолвер и одни слова отказа)."""
    from . import package_runs as pr
    res = pr.resolve_current()
    if res is None:
        return None
    _cur, _sha, pkg_path, wd, _run = res
    man = pr._package_manifest(pkg_path)
    try:
        scope = pr.package_scope(pkg_path)
    except Exception:                                      # noqa: BLE001
        scope = "local-only"
    try:
        from vmp import catalog_access as ca
        catalog_check = ca.manifest_check(man)
    except Exception:                                      # noqa: BLE001
        catalog_check = None
    return check_before_press(man, task, wd=wd, catalog_check=catalog_check,
                              scope=scope, actor=actor)


# ---------------------------------------------- объявление от пакета
def _kind_of(name) -> str | None:
    suffix = Path(str(name or "")).suffix.lstrip(".").lower()
    return suffix or None


def _inputs_of_manifest(man: dict) -> tuple[list, list, str | None]:
    """Документы правил и файлы данных прогона по манифесту пакета.

    Честный источник — ключ `made_by.inputs` (CDPM кладёт туда ИДАК и
    книги данных по отпечаткам). Пакет без него описывает себя сам:
    правила — файлы постановки, данные — очищенные данные. Ни того ни
    другого — пустые перечни: выдумывать документы гейт не станет, а
    словарь записи откажет словами."""
    made = man.get("made_by") if isinstance(man.get("made_by"), dict) else {}
    inputs = made.get("inputs") if isinstance(made.get("inputs"), dict) else {}
    rules = [{"name": r.get("name"), "kind": _kind_of(r.get("name")),
              "sha256": r.get("sha256"), "size": r.get("bytes")}
             for r in (inputs.get("rules") or []) if isinstance(r, dict)]
    data = [{"name": r.get("name"), "sheets": None, "rows": None,
             "sha256": r.get("sha256")}
            for r in (inputs.get("data") or []) if isinstance(r, dict)]
    if rules or data:
        return rules, data, "manifest-made-by-inputs"
    files = [f for f in (man.get("files") or []) if isinstance(f, dict)]
    if not files:
        return [], [], None
    for f in files:
        row_name = str(f.get("name") or "")
        if row_name.startswith("clean-data"):
            data.append({"name": row_name, "sheets": None, "rows": None,
                         "sha256": f.get("sha256")})
        else:
            rules.append({"name": row_name, "kind": _kind_of(row_name),
                          "sha256": f.get("sha256"), "size": f.get("bytes")})
    return rules, data, "manifest-files"


def form_defaults(scope: str = "local-only") -> dict:
    """Предзаполнение формы «Новый прогон от пакета»: слова словарей и
    та самая среда, которую человек подтверждает."""
    env = cosmos_environment(scope)
    return {"purposes": list(rr.PURPOSES),
            "contours": list(rr.CONTOURS),
            "contour": site_contour(),
            "purpose": rr.PURPOSES[4],
            "models": env["models"],
            "solvers": env["solvers"],
            "planned_stages": list(DEFAULT_PLANNED_STAGES),
            "answer_policies": list(rr.ANSWER_POLICIES)}


def _declared_solvers(env_solvers, given) -> list:
    """Решатели объявления: названные человеком либо среда площадки с
    параметрами кнопки по умолчанию (предел счёта — тот же, что у
    ручки нажатия)."""
    if given:
        return [dict(r) for r in given if isinstance(r, dict)]
    from . import package_runs as pr
    out = []
    for row in env_solvers or []:
        if not isinstance(row, dict) or row.get("reason_code") == \
                "environment-unreadable":
            continue
        if row.get("task") == "pairing" and row.get("engine") != pr.ENGINES[0]:
            continue                     # умолчание кнопки — первый движок
        out.append({**row, "spent_limit_s": pr.PRESS_DEFAULT_SPENT_LIMIT_S})
    return out


def declare(body: dict | None = None, actor: str | None = None) -> dict:
    """Ручка «Новый прогон от пакета»: объявление, чеканка номера,
    запись `run-record.json` в папку нажатий (хозяин — COSMOS).

    Отказы: пакета нет (`package-missing`), прогон уже объявлен
    (`already-declared` — и своей записью, и номером в манифесте: голова
    после объявления неизменна), объявление не по словарю
    (`declaration-invalid` со словами расхождений)."""
    from . import package_runs as pr
    body = body if isinstance(body, dict) else {}
    res = pr.resolve_current()
    if res is None:
        raise RunGateError(CODE_PACKAGE_MISSING,
                           ["текущего пакета постановки нет — объявлять "
                            "прогон не от чего: загрузите пакет из CDPM"])
    _cur, sha, pkg_path, wd, _run = res
    man = pr._package_manifest(pkg_path)
    head = head_of(man)
    if head["record_id"]:
        raise RunGateError(CODE_ALREADY,
                           ["прогон уже объявлен хозяином записи: номер %s "
                            "в манифесте пакета — голова записи неизменна"
                            % head["record_id"]])
    if local_record(wd) is not None:
        got = local_record(wd) or {}
        raise RunGateError(CODE_ALREADY,
                           ["прогон от этого пакета уже объявлен: запись %s "
                            "лежит в папке нажатий — новая ветвь заводится "
                            "новой записью"
                            % ((got.get("declaration") or {}).get("run_id"))])
    scope = pr.package_scope(pkg_path)
    env = cosmos_environment(scope)
    rules, data, inputs_code = _inputs_of_manifest(man)
    kit = man.get("kit") if isinstance(man.get("kit"), dict) else {}
    lineage = man.get("lineage") if isinstance(man.get("lineage"), dict) else {}
    root = _text(lineage.get("root_sha256")) or sha
    contour = _text(body.get("contour")) or site_contour()
    purpose = _text(body.get("purpose")) or rr.PURPOSES[4]
    stages = [str(s) for s in (body.get("planned_stages")
                               or DEFAULT_PLANNED_STAGES)]
    expectations = body.get("expectations") \
        if isinstance(body.get("expectations"), dict) else {}
    declaration = {
        "run_id": rr.mint_run_id(root),
        "created_at": rr.now_iso(),
        "created_by": _text(body.get("created_by")) or _text(actor),
        "purpose": purpose,
        "contour": {"name": contour,
                    "services": {"solver": next(
                        (r.get("url") for r in env["solvers"]
                         if isinstance(r, dict) and r.get("url")), None),
                        "catalog": (env["catalog"] or {}).get("path")}},
        "airline": {"name": _text(man.get("customer")),
                    "data_class": kit.get("data_class"),
                    "llm_scope": kit.get("llm_scope") or scope},
        "rules_documents": rules,
        "data_files": data,
        # Ответы на анкеты закрыты на стороне CDPM: частный прогон от
        # готового ВМП их не выдаёт и не хранит.
        "answers": [],
        "catalog": env["catalog"],
        "catalog_extension": None,
        "bindings": env["bindings"],
        # Частный прогон объявляет САМ COSMOS: роли головы — его
        # собственные, и строка называет это словом словаря, а не молчит
        # (без поля голова читалась бы «модели CDPM»).
        "models": own_models(body.get("models") or env["models"]),
        "solvers": _declared_solvers(env["solvers"], body.get("solvers")),
        "code": env["code"],
        "libraries": env["libraries"],
        "planned_stages": stages,
        "stop_after": _text(body.get("stop_after")),
        "expectations": expectations,
        "lineage": {"root_sha256": _text(lineage.get("root_sha256")),
                    "root_answers_sha256": None,
                    "parent_run": None,
                    "branch_of": _text(body.get("branch_of")),
                    "package_sha256": sha,
                    "source_run": head["source_run"]},
        "answer_policy": _text(body.get("answer_policy"))
        or rr.ANSWER_POLICIES[0],
    }
    if purpose not in rr.PURPOSES:
        raise RunGateError(CODE_INVALID,
                           ["цель прогона «%s» не из перечня слов (%s)"
                            % (purpose, ", ".join(rr.PURPOSES))], status=422)
    if contour not in rr.CONTOURS:
        raise RunGateError(CODE_INVALID,
                           ["площадка «%s» не из перечня слов (%s)"
                            % (contour, ", ".join(rr.CONTOURS))], status=422)
    try:
        record = rr.new_record(declaration)
    except rr.RunRecordError as e:
        raise RunGateError(CODE_INVALID, [str(e)], status=422) from None
    try:
        rr.save(record_path(wd), record)
    except OSError as e:
        raise RunGateError(CODE_RECORD_NOT_WRITTEN,
                           ["запись о прогоне не легла в папку нажатий: %s"
                            % str(e)[:200]]) from None
    head = head_for(wd, man)
    fragment = ensure_fragment(wd, head, scope, actor)
    # Объявленный прогон — новый узел дерева прогонов: индекс
    # перестраивается по событию (раздел 12.1 проекта). Беда индекса
    # объявления не касается — `note_change` наружу не бросает.
    from . import run_tree_api
    run_tree_api.note_change("объявлен прогон от пакета")
    return {"ok": True, "record": record, "head": head,
            "fragment": fragment, "inputs_code": inputs_code,
            "package_sha": sha}


# ------------------------------------------------------------- карточка
def current_view(actor: str | None = None) -> dict:
    """Тело ручки `GET /api/run/current`: голова, единая запись, фрагмент
    COSMOS с журналом, состояние сшивки и предзаполнение формы.

    Ручка ЧИТАЮЩАЯ, поэтому она ничего не требует: у необъявленного
    прогона она называет причину кодом и отдаёт форму — человеку есть
    что нажать."""
    from . import package_runs as pr
    from . import run_journal
    res = pr.resolve_current()
    if res is None:
        return {"present": False, "declared": False,
                "declare_reason_code": CODE_PACKAGE_MISSING,
                "words": ["текущего пакета постановки нет"]}
    _cur, sha, pkg_path, wd, run = res
    from . import task_package as wtp
    man = pr._package_manifest(pkg_path)
    try:
        scope = pr.package_scope(pkg_path)
    except Exception:                                      # noqa: BLE001
        scope = "local-only"
    head = head_for(wd, man)
    out = {"present": True, "package_sha": sha,
           "objective_projection": wtp.projection_status(pkg_path),
           "run": (run or {}).get("run") if isinstance(run, dict) else None,
           "head": head or head_of(man),
           "declared": head is not None,
           "declare_reason_code": None if head else CODE_NOT_DECLARED,
           "words": [] if head else declare_refusal_words(),
           "form": form_defaults(scope)}
    if head is None:
        out.update(record=None, cosmos={"environment": cosmos_environment(scope),
                                        "entries": []},
                   stitch=run_journal.stitch_state(wd, None),
                   state="не объявлен")
        return out
    fragment = ensure_fragment(wd, head, scope, actor)
    # Очередь сшивки повторяется при открытии карточки (раздел 10):
    # панель могла подняться после нажатия.
    run_journal.flush_outbox(wd, head)
    view = run_journal.record_view(wd, head)
    out.update(record=view.get("record"),
               record_source=view.get("source"),
               record_words=view.get("words"),
               cosmos={"environment": fragment.get("cosmos_environment"),
                       "environment_check": fragment.get("environment_check"),
                       "declared_at": fragment.get("declared_at"),
                       "declared_by": fragment.get("declared_by"),
                       "entries": run_journal.entries(wd)},
               stitch=run_journal.stitch_state(wd, head),
               state=view.get("state"))
    return out


def record_of_run(run_id: str) -> dict:
    """Тело ручки `GET /api/cosmos-runs/<run>/record`: та же единая
    запись по ТЕКУЩЕМУ пакету названного прогона COSMOS."""
    from . import cosmos_runs as cr
    from . import run_journal
    doc = cr.read_run(cr.check_run_id(run_id))
    pkg = cr._current_pkg(doc)
    if pkg is None:
        raise RunGateError(CODE_PACKAGE_MISSING,
                           ["у прогона %s нет текущего пакета постановки — "
                            "записи о прогоне здесь нет" % run_id], status=404)
    wd = cr.workdir_of_package(pkg)
    path = cr.package_path_of(pkg)
    man = {}
    if path is not None:
        from . import package_runs as pr
        man = pr._package_manifest(path)
    if not man:
        # Байты пакета утеряны — голова собирается из записи прогона
        # (её поля пишет приём канала).
        man = {"record_id": pkg.get("record_id"),
               "declaration_sha256": pkg.get("declaration_sha256"),
               "record_api": pkg.get("record_api"),
               "source_run": pkg.get("source_run"),
               "customer": pkg.get("customer")}
    head = head_for(wd, man)
    if head is None:
        raise RunGateError(CODE_NOT_DECLARED, declare_refusal_words(),
                           status=409)
    view = run_journal.record_view(wd, head)
    from . import task_package as wtp
    return {"present": True, "declared": True, "run": doc.get("run"), "head": head,
            "objective_projection": wtp.projection_status(path),
            "package_sha": pkg.get("sha"),
            "record": view.get("record"), "record_source": view.get("source"),
            "record_words": view.get("words"),
            "cosmos": {"entries": run_journal.entries(wd)},
            "stitch": run_journal.stitch_state(wd, head),
            "state": view.get("state")}
