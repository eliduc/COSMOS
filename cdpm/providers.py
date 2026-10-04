# -*- coding: utf-8 -*-
"""Провайдеры языковых моделей и настройки CDPM (пакет работ УИ-4).

Автономный реестр провайдеров для интерфейса (по образцу реестра
провайдеров CodeForge): какие каналы доступны, какие модели можно
просить, настроен ли ключ. Ключ Claude API хранится в КАТАЛОГЕ ДАННЫХ
(вне репозитория, У-01/У-04): `<каталог данных>/cdpm-settings.json`;
наружу ключ отдаётся только маской. Если свой ключ не задан, канал
облака работает через подписочную командную строку Claude — как и
раньше.

Пер-блочная конфигурация ролей (обработчик/аудитор/арбитр) живёт в
каталоге прогона (`agents.json`): провайдер + модель применяются к
каналу; усилие рассуждения, температура и максимум токенов сохраняются
и применяются там, где канал это умеет (канал API), — интерфейс
показывает это честно.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Полная независимость выбора LLM (директива владельца 2026-09-02,
# развитие принципа 31.08 «у каждого LLM-блока — свой параметр
# модели»): каждая точка конвейера — своя роль. Четыре прежние роли
# остаются РОДИТЕЛЯМИ: роль без собственной конфигурации наследует
# конфигурацию родителя (старые раскладки и прогоны читаются без
# изменений), родитель без конфигурации — умолчание канала.
ROLE_PARENT = {
    "extractor": "worker",        # извлечение правил из клауз
    "replier": "worker",          # ответчик в аудит-цикле и арбитраже
    "smt_translator": "worker",   # переводчик правил в SMT
    "logic_verifier": "auditor",  # сверщик обратного пересказа логики
    "mode2_checker": "auditor",   # аудитор чужой формализации (режим 2)
    "second_reader": "auditor",   # второе независимое чтение клауз (слой 2)
    "answer_matcher": "applier",  # разборщик свободных ответов анкеты
    "answer_applier": "applier",  # внесение ответов патчами строк
    "data_compiler": "applier",   # компилятор проверок данных
    "data_reviewer": "applier",   # сверщик спорных находок данных
    # Шаг 6 каталога шаблонов (07.09.2026): обезличенный черновик
    # записи семьи по посадке категории C.
    "catalog_composer": "worker",
    "vision": None,               # распознавание сканов при заморозке
}
ROLE_META = {
    "worker": ("Обработчик (родитель)", "умолчание для извлечения, "
               "ответчика цикла, сертификации и SMT"),
    "auditor": ("Аудитор", "независимый аудит свода и анкеты в "
                "цикле; родитель сверщика логики и режима 2"),
    "arbiter": ("Арбитр", "третейский судья тупиков цикла"),
    "applier": ("Применитель (родитель)", "умолчание для разбора и "
                "внесения ответов, компилятора и сверщика данных"),
    "extractor": ("Извлечение", "клаузы документов → кандидаты "
                  "правил, посадки, вопросы, целевые"),
    "replier": ("Ответчик цикла", "ответы на замечания аудитора и "
                "исполнение арбитража"),
    "logic_verifier": ("Сверщик логики", "обратный пересказ машинной "
                       "логики против формулировки"),
    "answer_matcher": ("Разборщик ответов", "свободный ответ "
                       "авиакомпании → вариант перечня"),
    "answer_applier": ("Внесение ответов", "патчи строк свода по "
                       "принятым ответам"),
    "mode2_checker": ("Аудитор режима 2", "сверка формализации "
                      "третьей стороны"),
    "second_reader": ("Второй читатель", "независимое второе чтение "
                      "клауз моделью другого семейства; расхождение с "
                      "извлечением — вопрос части 2 (слой 2)"),
    "data_compiler": ("Компилятор проверок данных",
                      "строки свода → машинные проверки данных"),
    "data_reviewer": ("Сверщик находок данных", "разбор спорных "
                      "находок проверок"),
    "smt_translator": ("SMT-переводчик", "правила → формулы "
                       "SMT-проверки"),
    "catalog_composer": ("Составитель заявок каталога",
                         "обезличенный черновик записи семьи из "
                         "посадки C"),
    "vision": ("Зрение", "распознавание сканов и памяток при "
               "заморозке (площадочный дефолт: заморозка идёт до "
               "настройки прогона)"),
}
ROLES = ("worker", "auditor", "arbiter", "applier") \
    + tuple(ROLE_PARENT)


def role_family(agents: dict, role: str, klass: str = "cloud"):
    """Семейство модели, действующее для роли в слоте класса: (провайдер,
    семейство по префиксу имени модели — claude/gpt/gemini/grok/…); None —
    роль без конфигурации (умолчание канала)."""
    eff = effective_role(agents, role)
    if eff is None:
        return None
    chan = ((agents.get(eff) or {}).get("channels") or {}).get(klass) or {}
    provider = str(chan.get("provider") or "cloud")
    model = str(chan.get("model") or "")
    if model:
        # Naming-family guard, not proof of model ancestry/independence.
        # Repository namespaces, numeric versions and size/quantization tags
        # must not turn two variants of one named family into two readers.
        name = model.strip().lower().rsplit("/", 1)[-1]
        fam = re.split(r"[-:.\d]", name, maxsplit=1)[0]
        if len(fam) == 1:
            # Preserve the existing o1/o3-style generation distinction;
            # size/tag variants still share the same undelimited base.
            fam = re.split(r"[-:.]", name, maxsplit=1)[0]
        if provider == "cloud" and fam in {"opus", "sonnet", "haiku"}:
            fam = "claude"
    else:
        fam = "claude" if provider == "cloud" else provider
    return (provider, fam)


def independence_problems(agents: dict) -> list[str]:
    """Машинная проверка раскладки для слоя 2: ни одна роль не судит
    собственное чтение — второй читатель и извлечение должны быть
    разных семейств моделей."""
    out: list[str] = []
    ex, sr = role_family(agents, "extractor"), role_family(agents, "second_reader")
    if sr is None:
        out.append("второй читатель не настроен (ни своей роли, ни аудитора-родителя)")
    elif ex is not None and ex[1] == sr[1]:
        out.append(f"второй читатель и извлечение — одно семейство моделей "
                   f"{sr[1]!r}: слой 2 не независим")
    return out


def effective_role(agents: dict, role: str) -> str | None:
    """Роль, чья конфигурация действует для запрошенной: своя, иначе
    родитель по цепочке; None — конфигурации нет (умолчание канала)."""
    seen = set()
    cur = role
    while cur and cur not in seen:
        if agents.get(cur):
            return cur
        seen.add(cur)
        cur = ROLE_PARENT.get(cur)
    return None

# Модели, которые имеет смысл просить у каналов; список — подсказка
# интерфейсу, не ограничение (канал принимает и полное имя модели).
CLOUD_MODELS = ["(по умолчанию)", "opus", "sonnet", "haiku"]


def settings_path() -> Path:
    env = (os.environ.get("CDPM_SETTINGS_FILE") or "").strip()
    if env:
        return Path(env)
    import _data_paths
    return _data_paths.data_root() / "cdpm-settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_settings(settings: dict) -> None:
    """Сохранить настройки АТОМАРНО: текст пишется во временный файл
    рядом (свой у каждого писателя — pid и нить), затем файл заменяется
    целиком. Прямая запись `write_text` оставляла окно, в котором
    читатель (`load_settings` другого запроса панели) видел рваный JSON
    или пустой файл (находка COSMOS, К5 каталога 0.5.0, 11.09.2026)."""
    import threading
    from cdpm.file_replace import replace_with_retry
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2)
                   + "\n", encoding="utf-8")
    try:
        replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "…" + key[-4:]


# Ключи всех провайдеров живут в одном месте настроек: {"keys": {…}}.
# Историческое поле claude_api_key читается и переносится сюда.
def _keys() -> dict:
    s = load_settings()
    keys = dict(s.get("keys") or {})
    legacy = (s.get("claude_api_key") or "").strip()
    if legacy and "cloud" not in keys:
        keys["cloud"] = legacy
    return keys


def sync_cloud_key_to_env() -> bool:
    """Ключ «cloud» из настроек CDPM — каналу выполнения webapp (МП-2,
    находка 2026-08-21): проверка провайдера и живой перечень моделей
    читали ключ из настроек CDPM, а КАНАЛ ВЫПОЛНЕНИЯ искал его только
    в файле webapp/config или переменной окружения — и, не найдя,
    молча уезжал в агентный CLI (100–300 с на вызов при заданном
    ключе). Ключ отдаётся ТОЛЬКО в память процесса (переменная
    окружения); файл webapp и заданная снаружи переменная — сильнее.
    Возвращает, был ли ключ прокинут."""
    import os
    try:
        from webapp import settings as cosmos_settings
        already = cosmos_settings.has_api_key()
    except Exception:                                      # noqa: BLE001
        return False
    if already:
        return False
    key = (_keys().get("cloud") or "").strip()
    if not key:
        return False
    os.environ["CREW_ANTHROPIC_API_KEY"] = key
    return True


def provider_key(provider: str) -> str | None:
    """Ключ провайдера из настроек CDPM (каталог данных); для облачной
    Claude — с откатом на настройки COSMOS."""
    key = (_keys().get(provider) or "").strip()
    if key:
        return key
    if provider == "cloud":
        try:
            from webapp import settings as cosmos_settings
            return cosmos_settings.get_api_key() or None
        except Exception:                                  # noqa: BLE001
            return None
    return None


def set_provider_key(provider: str, key: str) -> None:
    """Сохранение или удаление ключа провайдера (пустое значение —
    удаление). Сырой ключ наружу не отдаётся никогда."""
    s = load_settings()
    keys = dict(s.get("keys") or {})
    legacy = (s.pop("claude_api_key", "") or "").strip()
    if legacy and "cloud" not in keys:
        keys["cloud"] = legacy
    key = (key or "").strip()
    if key:
        keys[provider] = key
    else:
        keys.pop(provider, None)
    s["keys"] = keys
    save_settings(s)
    if provider == "cloud":
        # МП-2: свежесохранённый ключ сразу доступен каналу
        # выполнения — без перезапуска сервера.
        import os
        if not key:
            os.environ.pop("CREW_ANTHROPIC_API_KEY", None)
        sync_cloud_key_to_env()


def api_key() -> str | None:
    """Ключ Claude API (совместимость с прежними вызовами)."""
    return provider_key("cloud")


def list_providers() -> list[dict]:
    """Реестр провайдеров для интерфейса. Ключей в ответе нет — только
    маска и признак настроенности. Внешние провайдеры (OpenAI, Grok,
    Gemini) — облачные: при наборе `local-only` они запрещены наравне с
    облачной Claude, интерфейс это показывает признаком `cloud`."""
    from cdpm import external_llm
    from webapp import llm_backend
    cloud_cli = llm_backend.resolve_claude_cli() is not None
    keys = _keys()
    own_cloud = (keys.get("cloud") or "").strip()
    # РВ-119: показ каналов — без отказа; модель не задана → None.
    local_model = llm_backend.ollama_model_or_none()
    providers = [
        {
            "name": "cloud",
            "title": "Облако (Claude)",
            "configured": bool(cloud_cli or provider_key("cloud")),
            "channel": "API" if provider_key("cloud")
                       else "командная строка",
            "key_masked": _mask(own_cloud) if own_cloud else None,
            "key_hint": "sk-ant-…",
            "cloud": True,
            # Пакет трёх 2026-08-30, п.2: юрисдикция для слота
            # «облако внутри страны» — зарубежное облако.
            "in_country": False,
            "models": CLOUD_MODELS,
            "params_supported": (["model", "max_tokens", "thinking"]
                                 if provider_key("cloud") else ["model"]),
        },
        {
            "name": "local",
            "title": "Локальная модель (Ollama)",
            "configured": True,
            "channel": "локально",
            "key_masked": None,
            "key_hint": None,
            "cloud": False,
            "in_country": True,
            "models": ["(по умолчанию)", local_model],
            "params_supported": ["model", "temperature", "num_ctx"],
        },
    ]
    for name, spec in external_llm.SPECS.items():
        key = (keys.get(name) or "").strip()
        providers.append({
            "name": name,
            "title": spec["title"],
            "configured": bool(key),
            "channel": "API по ключу",
            "key_masked": _mask(key) if key else None,
            "key_hint": spec["key_hint"],
            "cloud": True,
            "in_country": bool(spec.get("in_country")),
            "models": spec["models"],
            "params_supported": ["model", "temperature", "max_tokens"],
        })
    return providers


def check_provider(provider: str, scope: str = "any") -> dict:
    """Проверка связи: короткий запрос к провайдеру. Возвращает
    {ok, detail[, models]} — ключ и тело ответа наружу не отдаются.

    Если канал с моделью по умолчанию отказал, а провайдер внешний —
    честная диагностика: живой перечень моделей КЛЮЧА и повторная
    проверка первой доступной (зашитые подсказки устаревают; живой
    случай Gemini 2026-08-18: ключ верный, модель-подсказка не
    существует)."""
    from cdpm import llm as llm_mod
    # Проверка связи — короткая: одна попытка без пауз (повторы с
    # паузами нужны долгому конвейеру, а здесь человек ждёт у экрана).
    try:
        fn = llm_mod.channel(scope, provider=provider, attempts=1,
                             timeout_s=60.0)
        got = fn("Ответь одним словом: готов")
        return {"ok": True, "detail": got.strip()[:80]}
    except Exception as e:                                 # noqa: BLE001
        first_error = str(e)[:300]

    from cdpm import external_llm
    if provider not in external_llm.SPECS:
        return {"ok": False, "detail": first_error}
    key = provider_key(provider) or ""
    try:
        models = external_llm.list_models(provider, key=key)
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False,
                "detail": f"{first_error}; перечень моделей ключа "
                          f"недоступен: {str(e)[:150]}"}
    # Автоподбор МП-1: ранжирование по свежести (новейшие первыми),
    # без зашитых номеров семейств — они устаревают (живой случай
    # 2026-08-20: в подсказках gpt-5.4 при фактических 5.6).
    try:
        candidates = external_llm.ranked_models(provider, key=key)
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False,
                "detail": f"{first_error}; ранжирование моделей "
                          f"недоступно: {str(e)[:150]}"}
    models = candidates
    for model in candidates[:3]:
        try:
            fn = llm_mod.channel(scope, provider=provider, model=model,
                                 attempts=1, timeout_s=60.0)
            got = fn("Ответь одним словом: готов")
            return {"ok": True, "models": models[:12],
                    "detail": f"связь есть; новейшая модель ключа: "
                              f"«{model}» — задайте её в слоте "
                              f"блока; ответ: {got.strip()[:60]}"}
        except Exception:                                  # noqa: BLE001
            continue
    return {"ok": False, "models": models[:12],
            "detail": f"{first_error}; доступные модели ключа: "
                      + ", ".join(models[:8])}


# ── Пер-блочная конфигурация ролей прогона ──
# Три слота модели у каждой роли (решение владельца 2026-08-18): блок
# получает входные файлы с признаком политики обработки и обрабатывает
# каждый моделью соответствующего слота: «cloud» — облачная модель,
# «local» — локальная, «cloud-country» — облачная модель внутри страны
# (пока провайдер не выбран, слот работает локальной моделью — строже,
# не мягче).

CHANNEL_CLASSES = ("cloud", "local", "cloud-country")
_CHANNEL_FIELDS = ("provider", "model", "temperature", "max_tokens",
                   "effort", "base_url", "think", "num_ctx")
_ROLE_FIELDS = ("channels", "thinking", "instruction")
# Прежняя плоская форма конфигурации роли — мигрирует в слот класса.
_LEGACY_FIELDS = _CHANNEL_FIELDS + ("thinking", "instruction")


def known_providers() -> tuple[str, ...]:
    from cdpm import external_llm
    return ("cloud", "local", "auto") + tuple(external_llm.SPECS)


def _migrate_role(cfg: dict) -> dict:
    """Плоская конфигурация прежних прогонов {provider, model, …}
    переезжает в слот своего класса; настройки не теряются."""
    if not isinstance(cfg, dict) or "channels" in cfg:
        return cfg
    if not any(k in cfg for k in _CHANNEL_FIELDS):
        return cfg
    klass = "local" if cfg.get("provider") == "local" else "cloud"
    chan = {k: cfg[k] for k in _CHANNEL_FIELDS if k in cfg}
    out = {k: cfg[k] for k in ("thinking", "instruction") if k in cfg}
    out["channels"] = {klass: chan}
    return out


def _agents_default_path():
    import os
    override = os.environ.get("CDPM_AGENTS_DEFAULT")
    if override is not None:
        if not override.strip():
            return None
        return Path(override)
    import _data_paths
    return _data_paths.data_root() / "agents-default.json"


# Источник действующей раскладки ролей (ревизия 1.18.4 спецификации,
# таблица S06): площадочный носитель — умолчание, пер-прогонный
# agents.json — явный выбор в пределах допуска, «none» — раскладки нет
# (умолчание канала). Потребители (отпечаток канала, журнал стадии)
# получают источник тем же разбором файлов, что и саму раскладку.
AGENTS_SOURCES = ("run-explicit", "site-default", "none")


def _load_agents_with_source(run_dir: Path) -> tuple[dict, str]:
    """Один разбор носителей раскладки на оба ответа: (раскладка,
    источник). Пер-прогонный agents.json СИЛЬНЕЕ («run-explicit»);
    без него (или пустого) действует ПЛОЩАДОЧНЫЙ ДЕФОЛТ
    («site-default») — носитель контура agents-default.json (правило
    владельца 31.08: раскладка моделей всех LLM-точек задаётся им
    явно; переопределение пути — CDPM_AGENTS_DEFAULT, пустое значение
    = дефолта нет). Служебные ключи конвенции носителей
    (form_version/customer/_comment) игнорируются; носитель, из
    которого раскладки не вышло, источником не считается («none»)."""
    path = run_dir / "agents.json"
    agents = None
    source = "none"
    if path.is_file():
        got = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(got, dict) and got:
            agents, source = got, "run-explicit"
    if agents is None:
        dpath = _agents_default_path()
        if dpath is not None and dpath.is_file():
            got = json.loads(dpath.read_text(encoding="utf-8"))
            agents = got if isinstance(got, dict) else {}
            for aux in ("form_version", "customer", "_comment"):
                agents.pop(aux, None)
            source = "site-default"
    if not agents:
        return {}, "none"
    # Переименование роли (решение владельца 2026-08-18): судья-эталон
    # ушёл из продукта, третья модель — арбитр тупиков. Старые
    # конфигурации прогонов читаются без потери настроек.
    if "judge" in agents and "arbiter" not in agents:
        agents["arbiter"] = agents.pop("judge")
    else:
        agents.pop("judge", None)
    return ({role: _migrate_role(cfg) for role, cfg in agents.items()},
            source)


def load_agents(run_dir: Path) -> dict:
    """Конфигурация ролей: пер-прогонный agents.json СИЛЬНЕЕ;
    без него (или пустого) новые прогоны наследуют ПЛОЩАДОЧНЫЙ
    ДЕФОЛТ — носитель контура agents-default.json (правило владельца
    31.08; переопределение пути — CDPM_AGENTS_DEFAULT, пустое
    значение = дефолта нет). Разбор — в _load_agents_with_source."""
    return _load_agents_with_source(run_dir)[0]


def agents_source(run_dir: Path) -> str:
    """Источник действующей раскладки ролей прогона: «run-explicit»
    (пер-прогонный agents.json), «site-default» (площадочный
    носитель), «none» (раскладки нет). Тот же разбор, что у
    load_agents, — расхождения между ответами не бывает."""
    return _load_agents_with_source(run_dir)[1]


def channel_kind(provider: str | None) -> str | None:
    """Вид канала провайдера (S06): локальная модель — «local»;
    облачная Claude — «api» при заданном ключе (provider_key), иначе
    «cli» (подписочная командная строка); внешние провайдеры — только
    «api». Провайдер не назван (None/«auto») — вида нет (None).
    Явного поля cli/api в слоте роли нет — вид выводится из ключа."""
    if provider in (None, "", "auto"):
        return None
    if provider == "local":
        return "local"
    if provider == "cloud":
        return "api" if provider_key("cloud") else "cli"
    return "api"


def effective_channel(fn, provider: str | None = None,
                      model: str | None = None) -> dict:
    """Действующие {provider, model, channel} построенного канала —
    по его отпечатку (провайдер после resolve_backend и модель после
    подстановки умолчания ставит llm.channel/cosmos_llm); у канала
    без отпечатка (подменные функции тестов) — то, что каналу
    передали. Вид канала — channel_kind по действующему провайдеру."""
    from cdpm import llm as llm_mod
    ident: dict = {}
    raw = llm_mod.channel_identity(fn)
    if raw:
        try:
            got = json.loads(raw)
            ident = got if isinstance(got, dict) else {}
        except ValueError:
            ident = {}
    eff_provider = ident.get("provider", provider)
    eff_model = ident.get("model", model)
    # S06-IDENTITY-1 (ревью GPT-027): вид канала — из транспорта,
    # ЗАКРЕПЛЁННОГО в самом построенном канале (отпечаток `transport`
    # cosmos_llm: api / cli / local), а не из повторного чтения ключа:
    # смена ключа между построением канала и записью отпечатка давала
    # effective.channel, расходящийся с тем, чем канал исполняется.
    # Вывод по ключу — только у канала без закреплённого транспорта
    # (внешние провайдеры, подменные функции тестов).
    pinned = ident.get("transport")
    channel = pinned if pinned in ("api", "cli", "local") \
        else channel_kind(eff_provider)
    return {"provider": eff_provider, "model": eff_model,
            "channel": channel}


def _check_channel(role: str, klass: str, chan,
                   problems: list[str]) -> dict:
    if not isinstance(chan, dict):
        problems.append(f"{role}/{klass}: слот не объект")
        return {}
    unknown = set(chan) - set(_CHANNEL_FIELDS)
    if unknown:
        problems.append(f"{role}/{klass}: неизвестные поля "
                        f"{sorted(unknown)}")
        return {}
    if chan.get("provider") not in (None,) + known_providers():
        problems.append(f"{role}/{klass}: провайдер "
                        f"{chan.get('provider')!r} вне перечня "
                        f"{known_providers()}")
        return {}
    if klass == "local" and chan.get("provider") not in (None, "local"):
        problems.append(f"{role}/{klass}: слот локальной модели не "
                        f"принимает облачного провайдера "
                        f"{chan.get('provider')!r}")
        return {}
    from cdpm import external_llm
    effort = chan.get("effort")
    if effort not in (None, "") and \
            effort not in external_llm.EFFORT_LEVELS:
        problems.append(f"{role}/{klass}: усилие рассуждения "
                        f"{effort!r} вне перечня "
                        f"{external_llm.EFFORT_LEVELS}")
        return {}
    # Принцип трёх моделей (директива владельца 2026-08-31):
    # base_url — адрес ЛОКАЛЬНОЙ Ollama роли (роль-на-GPU/машину);
    # облачным слотам он не принадлежит.
    burl = chan.get("base_url")
    if burl not in (None, ""):
        if not isinstance(burl, str) \
                or not burl.startswith(("http://", "https://")):
            problems.append(f"{role}/{klass}: base_url не "
                            f"http(s)-адрес: {burl!r}")
            return {}
        if klass != "local":
            problems.append(f"{role}/{klass}: base_url применим "
                            "только слоту локальной модели")
            return {}
    think = chan.get("think")
    num_ctx = chan.get("num_ctx")
    if num_ctx is not None:
        if type(num_ctx) is not int or num_ctx <= 0:
            problems.append(f"{role}/{klass}: num_ctx должен быть положительным целым")
            return {}
        if klass != "local" and chan.get("provider") != "local":
            problems.append(f"{role}/{klass}: num_ctx применим только к локальному каналу")
            return {}
    if think is not None and not isinstance(think, bool):
        problems.append(f"{role}/{klass}: think не булево: "
                        f"{think!r}")
        return {}
    if think is not None and klass != "local":
        problems.append(f"{role}/{klass}: think применим только "
                        "слоту локальной модели")
        return {}
    return chan


def save_agents(run_dir: Path, agents: dict) -> list[str]:
    """Сохранение конфигурации ролей; возвращает нарушения (пустой
    список = сохранено). Принимает и прежнюю плоскую форму — она
    мигрирует в слот своего класса."""
    problems: list[str] = []
    clean: dict = {}
    for role, cfg in (agents or {}).items():
        if role not in ROLES:
            problems.append(f"{role}: роль вне перечня {ROLES}")
            continue
        if not isinstance(cfg, dict):
            problems.append(f"{role}: конфигурация не объект")
            continue
        cfg = _migrate_role(cfg)
        unknown = set(cfg) - set(_ROLE_FIELDS)
        if unknown:
            problems.append(f"{role}: неизвестные поля "
                            f"{sorted(unknown)}")
            continue
        channels = cfg.get("channels") or {}
        if not isinstance(channels, dict):
            problems.append(f"{role}: channels не объект")
            continue
        bad_classes = set(channels) - set(CHANNEL_CLASSES)
        if bad_classes:
            problems.append(f"{role}: классы каналов "
                            f"{sorted(bad_classes)} вне перечня "
                            f"{CHANNEL_CLASSES}")
            continue
        clean_channels = {
            klass: _check_channel(role, klass, chan, problems)
            for klass, chan in channels.items()}
        from cdpm import external_llm
        thinking = cfg.get("thinking")
        if thinking not in (None, "") and \
                thinking not in external_llm.EFFORT_LEVELS:
            problems.append(f"{role}: усилие рассуждения {thinking!r} "
                            f"вне перечня {external_llm.EFFORT_LEVELS}")
            continue
        out = {k: cfg[k] for k in ("thinking", "instruction")
               if k in cfg}
        out["channels"] = clean_channels
        clean[role] = out
    if problems:
        return problems
    path = run_dir / "agents.json"
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2)
                    + "\n", encoding="utf-8")
    return []


class LayoutUndeclared(RuntimeError):
    """S06-R2: у роли, чья конфигурация действует, не объявлен слот
    класса «cloud» (ни провайдера, ни модели) при допуске, разрешающем
    облако — облачный канал не строится, панель отвечает словами."""

    def __init__(self, role: str, klass: str, config_source: str):
        super().__init__(f"облачный слот роли «{role}» не объявлен "
                         f"(источник раскладки {config_source})")
        self.role = role
        self.klass = klass
        self.config_source = config_source


def cloud_undeclared_roles(agents: dict,
                           roles=("worker", "auditor", "arbiter", "applier")) -> list[str]:
    """Роли-семейства, у которых действующая конфигурация не объявляет
    облачный слот (ни провайдера, ни модели): для гейта раскладки
    прогона (S06-R2). Роль без конфигурации вовсе — тоже не объявлена."""
    out = []
    for role in roles:
        eff = effective_role(agents, role)
        chan = (((agents.get(eff) or {}).get("channels") or {}).get("cloud") or {}) \
            if eff else {}
        m = chan.get("model")
        if not (chan.get("provider") or (m not in (None, "", "(по умолчанию)"))):
            out.append(role)
    return out


def role_channel(scope: str, run_dir: Path, role: str,
                 fallback_requested: str | None = None,
                 klass: str = "cloud",
                 timeout_s: float = 600.0,
                 effort_default: str | None = None):
    """Канал модели для роли и КЛАССА данных: слот класса из
    конфигурации роли; температура и максимум токенов — там, где канал
    их принимает (внешние провайдеры принимают).

    Слот «cloud-country» без выбранного провайдера работает локальной
    моделью роли — строже, не мягче (fail-closed)."""
    from cdpm import llm as llm_mod
    agents, config_source = _load_agents_with_source(run_dir)
    role_cfg = agents.get(role, {})
    channels = role_cfg.get("channels") or {}
    chan = channels.get(klass) or {}
    # МП-3: «Усилие рассуждения» роли (поле существовало в интерфейсе,
    # но никуда не прокидывалось — мёртвый параметр, скриншот владельца
    # 2026-08-21); слотовое поле effort — тонкая настройка поверх.
    # `effort_default` — умолчание вызывающей стороны (УСК-1: каналу
    # ответов обработчика в цикле — low); явная настройка главнее.
    effort = chan.get("effort") or role_cfg.get("thinking") \
        or effort_default or None

    def _model(c: dict):
        m = c.get("model")
        return None if m in (None, "", "(по умолчанию)") else m

    # Отпечаток канала (A11): роль, чья конфигурация действует, класс
    # политики файла и сама конфигурация роли — вместе с отпечатком
    # провайдера/модели (его ставит llm.channel) это соль кэшей стадий.
    # Ревизия 1.18.4 (таблица S06): отпечаток несёт и источник
    # конфигурации (config_source), просьбу слота (requested) и
    # действующие провайдер/модель/вид канала (effective) — смена
    # источника раскладки при той же модели = другой ключ кэша.
    requested_provider = chan.get("provider") or None
    requested = {"provider": requested_provider,
                 "model": _model(chan),
                 "channel": channel_kind(requested_provider)}

    def _stamped(fn, provider=None, model=None):
        return llm_mod.stamp_identity(
            fn, role=role, klass=klass, role_config=role_cfg,
            config_source=config_source, requested=requested,
            effective=effective_channel(fn, provider, model))

    if klass == "local":
        provider = "local"
    elif klass == "cloud-country":
        provider = chan.get("provider")
        if not provider:
            local = channels.get("local") or {}
            return _stamped(llm_mod.channel(scope, provider="local",
                                            model=_model(local),
                                            timeout_s=timeout_s,
                                            base_url=local.get("base_url"),
                                            num_ctx=local.get("num_ctx")),
                            "local", _model(local))
    else:
        # S06-R2 (GPT-060, P1 S06-CLOUD-SLOT-FALLBACK): роль объявлена, а
        # её облачный слот — нет (только локальный слот, пустые channels,
        # одна инструкция) → это НЕ объявленная модель облачной стадии;
        # молчаливое «cloud / модель по умолчанию» снято. Допуск
        # «только локально» — старший отказ: там облачный канал и так не
        # строится (ScopeViolation ниже), гейт раскладки не судится.
        if not (chan.get("provider") or _model(chan)) and scope != "local-only":
            raise LayoutUndeclared(role, klass, config_source)
        # Класс «облако» не может молча уехать в «авто» (авто на
        # машине с локальной моделью выберет её — живой отказ
        # арбитража 2026-08-18): умолчание слота — облачная Claude.
        provider = chan.get("provider") or fallback_requested or "cloud"
    # МП-4: таймаут роли — как у канала цикла без конфигурации (600 с):
    # прежде сама настройка роли (например, усилия) молча возвращала
    # 300-секундный дефолт — ровно тот таймаут, что ронял UniA
    # 2026-08-20; включённое мышление удлиняет ответы дополнительно.
    return _stamped(llm_mod.channel(
        scope, provider=provider, model=_model(chan),
        timeout_s=timeout_s,
        temperature=chan.get("temperature"),
        max_tokens=chan.get("max_tokens"),
        effort=effort,
        base_url=(chan.get("base_url") if klass == "local" else None),
        think=(chan.get("think") if klass == "local" else None),
        num_ctx=(chan.get("num_ctx") if provider == "local" else None)),
        provider, _model(chan))
