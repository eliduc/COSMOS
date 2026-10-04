"""Разрешение конфигурации по ОБЛАСТИ и по ЭКЗЕМПЛЯРУ.

Замечание внешнего аудита P1-10 (2026-07-26): правила яруса 1 и ставки
хранились по одному файлу на всю установку, и администратор, меняющий
норму под свою авиакомпанию, менял её и для чужого набора. Экземпляров
много, у каждого есть владелец (`registry.load_ownership`), а набор
правил был один на всех.

Здесь — ЕДИНСТВЕННОЕ место, где решается, какой файл конфигурации
относится к данному экземпляру. Областей четыре по смыслу и три по
реализации: правила яруса 1, ставки стоимости, ставки оплаты (пакеты
правил яруса 2 привязываются отдельно и здесь пока не участвуют).

Порядок разрешения — от общего к частному, побеждает САМЫЙ ЧАСТНЫЙ
СУЩЕСТВУЮЩИЙ файл:

  1. встроенные значения области            — договор воспроизводимости;
  2. `webapp/config/<область>.json`          — ШАБЛОН установки;
  3. `webapp/config/instance-<область>/<id>.json` — правила экземпляра;
  4. `webapp/config/scenario-rules/<id>.json`     — правила сценария.

Файл каждого уровня ПОЛНЫЙ: писатель кладёт весь набор значений, а не
разницу. Поэтому «побеждает самый частный файл» и «послойное наложение»
здесь — одно и то же, и ни один читатель не обязан уметь склеивать слои.
Так же устроены и сценарии с самого их появления (`scenarios.create`
пишет `full_rules`).

Почему уровень установки — ШАБЛОН, а не живая ссылка. Значение по
умолчанию подставляется ОДИН РАЗ, при создании набора, и записывается в
набор (`materialize_instance`). Иначе один и тот же набор, посчитанный в
разные месяцы, считается по разным нормам, и по артефакту этого не видно.
Тот же приём: Kubernetes дописывает умолчания в сам объект при создании,
файлы блокировки фиксируют разрешённые версии, Terraform записывает
разрешённые значения переменных в план.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from . import registry

CONFIG_DIR = registry.REPO_ROOT / "webapp" / "config"

DOMAIN_RULES = "rules"
DOMAIN_COST = "cost"
DOMAIN_PAY = "pay"
DOMAINS = (DOMAIN_RULES, DOMAIN_COST, DOMAIN_PAY)

# Уровень 2: шаблон установки. Имена файлов историчны — переименование
# осиротило бы рабочие конфиги на трёх контурах.
#
# ЭТО ЕДИНСТВЕННОЕ ОБЪЯВЛЕНИЕ путей установки. Раньше их было три
# (`app.AIRLINE_CONFIG_PATH`, `jobs.AIRLINE_CONFIG`, `scenarios.AIRLINE_CONFIG`),
# и тестовая изоляция чинила каждое по отдельности — ровно тот разъезд
# защит по копиям, на который указывает вывод внешнего аудита. Читателям и
# писателям положено ходить через `install_path()`; тесту достаточно
# подменить один элемент этого отображения.
INSTALL_FILES = {
    DOMAIN_RULES: CONFIG_DIR / "airline_default.json",
    DOMAIN_COST: CONFIG_DIR / "cost_config.json",
    DOMAIN_PAY: CONFIG_DIR / "pay_config.json",
}
# Уровень 3: конфигурация экземпляра.
INSTANCE_DIRS = {
    DOMAIN_RULES: CONFIG_DIR / "instance-rules",
    DOMAIN_COST: CONFIG_DIR / "instance-cost",
    DOMAIN_PAY: CONFIG_DIR / "instance-pay",
}
# Уровень 4: сценарий «что если». Каталог существует с 2026-07-15 и
# остаётся на своём историческом имени; ставок на этом уровне нет —
# сценарные ставки живут полями `cost_overrides` в реестре сценариев.
SCENARIO_DIRS = {
    DOMAIN_RULES: CONFIG_DIR / "scenario-rules",
}

ORIGIN_DEFAULTS = "defaults"
ORIGIN_INSTALLATION = "installation"
ORIGIN_INSTANCE = "instance"
ORIGIN_SCENARIO = "scenario"

# Раскладка файла: у правил значения лежат под ключом "parameters"
# (исторический формат `airline_default.json`), у ставок — плоско.
_WRAPPED = {DOMAIN_RULES}


def _inst_id(inst) -> str:
    """Экземпляр принимается словарём реестра или строкой-идентификатором."""
    if inst is None:
        return ""
    if isinstance(inst, str):
        return inst
    return str(inst.get("id") or "")


def install_path(domain: str) -> Path:
    return INSTALL_FILES[domain]


def instance_dir(domain: str) -> Path:
    return INSTANCE_DIRS[domain]


def scenario_dir(domain: str) -> Path | None:
    return SCENARIO_DIRS.get(domain)


def instance_path(domain: str, inst) -> Path | None:
    """Где ДОЛЖЕН лежать файл экземпляра (существует он или нет)."""
    inst_id = _inst_id(inst)
    if not inst_id:
        return None
    return INSTANCE_DIRS[domain] / f"{inst_id}.json"


def scenario_path(domain: str, inst, known_scenarios=None) -> Path | None:
    """Файл сценария, если экземпляр — сценарий И файл на месте.

    Через реестр сценариев, а не по одному существованию файла:
    осиротевший файл без записи в реестре не должен получать власть над
    расчётом (семантика сохранена с 2026-07-15).

    `known_scenarios` — уже разобранный набор идентификаторов сценариев.
    Нужен перечню наборов: иначе реестр сценариев разбирался бы заново на
    КАЖДЫЙ набор, а их на боевом контуре сотни."""
    if domain not in SCENARIO_DIRS:
        return None
    inst_id = _inst_id(inst)
    if not inst_id:
        return None
    if known_scenarios is None:
        from . import scenarios          # ленивый импорт: цикл через costing
        if scenarios.meta_for(inst_id) is None:
            return None
    elif inst_id not in known_scenarios:
        return None
    p = SCENARIO_DIRS[domain] / f"{inst_id}.json"
    return p if p.exists() else None


def resolve(domain: str, inst=None, known_scenarios=None
            ) -> tuple[Path | None, str]:
    """Самый частный СУЩЕСТВУЮЩИЙ файл области и его происхождение.

    `(None, ORIGIN_DEFAULTS)` — файла нет ни на одном уровне, действуют
    встроенные значения области."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown config domain {domain!r} "
                         f"(known: {', '.join(DOMAINS)})")
    p = scenario_path(domain, inst, known_scenarios)
    if p is not None:
        return p, ORIGIN_SCENARIO
    p = instance_path(domain, inst)
    if p is not None and p.exists():
        return p, ORIGIN_INSTANCE
    p = install_path(domain)
    if p.exists():
        return p, ORIGIN_INSTALLATION
    return None, ORIGIN_DEFAULTS


def path_for(domain: str, inst=None) -> Path | None:
    return resolve(domain, inst)[0]


def origin_for(domain: str, inst=None) -> str:
    return resolve(domain, inst)[1]


def known_scenario_ids() -> set:
    """Идентификаторы сценариев одним разбором реестра — для перечня
    наборов, где иначе реестр читался бы на каждый набор."""
    from . import scenarios
    try:
        return set(scenarios._load())
    except (OSError, ValueError):
        return set()


def is_strict(domain: str, inst=None) -> bool:
    """Нужен ли строгий режим (`CREW_RULES_STRICT`) для этого экземпляра.

    ТОЛЬКО для сценариев, и это НЕ упущение. Строгий режим заставляет
    явные значения стыковки и отдыха побеждать предустановки профиля
    этапа 1 — ради этого сценарий и существует. Распространить его на
    уровень экземпляра нельзя: после переноса файл появится У КАЖДОГО
    набора, предустановки профиля перестали бы применяться везде разом, и
    результаты разошлись бы с уже посчитанными. Требование переноса —
    байт-идентичность."""
    return origin_for(domain, inst) == ORIGIN_SCENARIO


def _defaults(domain: str) -> dict:
    """Встроенные значения области. Импорт ленивый: `costing` и
    `pay_credit` сами читают конфигурацию через этот модуль."""
    if domain == DOMAIN_RULES:
        import rules_config
        return dict(rules_config.DEFAULTS)
    if domain == DOMAIN_COST:
        from . import costing
        return dict(costing.COST_DEFAULTS)
    if domain == DOMAIN_PAY:
        from . import pay_credit
        return dict(pay_credit.PAY_DEFAULTS)
    raise ValueError(f"unknown config domain {domain!r}")


def effective(domain: str, inst=None) -> dict:
    """Действующие значения области для экземпляра.

    Разбор и нормализация — ТЕМИ ЖЕ загрузчиками, что у писателей: иначе
    сырой `600.0` из файла против `int 600` писателя навсегда расходит
    отпечатки (урок манифестов действующего ростера)."""
    p = path_for(domain, inst)
    if domain == DOMAIN_RULES:
        import rules_config
        return rules_config.load_from(p)
    if domain == DOMAIN_COST:
        from . import costing
        return costing.load_cost_config_from(p)
    if domain == DOMAIN_PAY:
        from . import pay_credit
        return pay_credit.load_pay_config_from(p)
    raise ValueError(f"unknown config domain {domain!r}")


def write_instance(domain: str, inst, values: dict) -> Path:
    """Записать ПОЛНЫЙ набор значений области для экземпляра.

    Запись атомарна (`tmp → replace`): прямая запись оставляет окно, в
    котором файл уже усечён, но ещё не дописан, и попавший в него старт
    расчёта прочитал бы обрезанный JSON. Повтор на `PermissionError` —
    Windows не даёт заменить файл, открытый на чтение параллельным
    процессом (тот же приём, что у конфигурации установки)."""
    p = instance_path(domain, inst)
    if p is None:
        raise ValueError("instance id required to write instance config")
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"parameters": dict(values)} if domain in _WRAPPED else dict(values)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".json.tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        for attempt in range(5):
            try:
                tmp.replace(p)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)
    return p


def reset_instance(domain: str, inst) -> dict:
    """Переписать конфигурацию набора из ШАБЛОНА установки.

    Это и есть «сбросить»: у набора всегда есть свой полный файл, и сброс
    означает «взять сегодняшний шаблон», а не «ходить за шаблоном каждый
    раз». Иначе смысл уровня установки снова стал бы плавающим, и набор,
    сброшенный сегодня, завтра считался бы по другим нормам."""
    values = effective(domain, None)         # шаблон установки, без набора
    write_instance(domain, inst, values)
    return values


def drop_instance(domain: str, inst) -> bool:
    """Убрать файл конфигурации набора совсем (используется при удалении
    набора и сценария). True, если файл был."""
    p = instance_path(domain, inst)
    if p is None or not p.exists():
        return False
    p.unlink(missing_ok=True)
    return True


def materialize_instance(inst, domains=DOMAINS) -> list[str]:
    """Записать наборам ИХ СОБСТВЕННУЮ конфигурацию из действующей.

    Подстановка значений по умолчанию — событие, у которого есть время:
    она происходит один раз, при появлении набора, и дальше набор
    самодостаточен. Правка шаблона установки после этого влияет только на
    БУДУЩИЕ наборы — ровно та семантика, которую слово «по умолчанию» и
    обещает.

    Значения не меняются, лишь перестают быть неявными: пишется то, что
    этот набор и так получал. Сценарии пропускаются — у них свой,
    более частный уровень, и переносить их некуда.

    Возвращает список областей, для которых файл был создан."""
    inst_id = _inst_id(inst)
    if not inst_id:
        return []
    written: list[str] = []
    for domain in domains:
        if scenario_path(domain, inst) is not None:
            continue                     # сценарий уже самый частный
        p = instance_path(domain, inst)
        if p is None or p.exists():
            continue
        write_instance(domain, inst, effective(domain, inst))
        written.append(domain)
    return written


def materialize_all(domains=DOMAINS) -> dict:
    """Разовый перенос: выдать СВОЮ конфигурацию каждому наличному набору.

    Запускается при старте приложения и идемпотентен — набор, у которого
    файл уже есть, не трогается. Перенос ЧИСТЫЙ: пишется ровно то, что
    набор и так получал сегодня, поэтому отпечаток постановки и все уже
    посчитанные артефакты остаются прежними.

    Отказ записи не имеет права ронять старт приложения: набор без своего
    файла продолжит работать по шаблону установки — это ровно прежнее
    поведение, и на экране оно честно подписано «взяты из шаблона»."""
    done: dict[str, list[str]] = {}
    failed: list[str] = []
    try:
        instances = registry.list_instances()
    except OSError:
        return {"migrated": {}, "failed": [], "instances": 0}
    for inst in instances:
        try:
            written = materialize_instance(inst, domains)
        except (OSError, ValueError):
            failed.append(str(inst.get("id") or ""))
            continue
        if written:
            done[inst["id"]] = written
    return {"migrated": done, "failed": failed, "instances": len(instances)}


# --------------------------------------------------- заморозка на прогон

# Снимок конфигурации прогона. Живёт рядом с прочими артефактами прогона и
# убирается вместе с ними.
RUNS_CONFIG_DIR = registry.FINAL_REPORT / "runs" / "config"


def fingerprint(values: dict) -> str:
    """Отпечаток набора значений. ТА ЖЕ формула, что у построителя цепочек
    (`_pairing_builder.config_signature`), иначе сравнить отпечатки этапов
    было бы нельзя."""
    return hashlib.sha256(
        json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]


def run_dir(run_id: str) -> Path:
    return RUNS_CONFIG_DIR / str(run_id)


def frozen_path(run_id: str, domain: str) -> Path | None:
    p = run_dir(run_id) / f"{domain}.json"
    return p if p.exists() else None


def freeze_run(run_id: str, inst, domains=DOMAINS) -> dict:
    """Заморозить конфигурацию прогона: снимок вместо живого файла.

    Без этого правку, сделанную МЕЖДУ этапом 1 и этапом 2, половины
    системы увидят по-разному: цепочки построены по одним нормам, люди
    рассажены по другим, и расхождение тихое. Тот же приём, что у
    воспроизводимых сборок: входы прогона фиксируются и хешируются,
    прогон, читающий изменяемое окружение, воспроизводимым не считается.

    Замораживаются ВСЕ прогоны, без разрядов на «пробные» и «настоящие»:
    держать два механизма — значит гарантированно сломать тот, которым
    пользуются реже. Ставки замораживаются наравне с правилами: отчёт,
    чьи денежные числа невозможно воспроизвести, хуже отчёта с
    невоспроизводимыми правилами.

    Возвращает `{"dir", "files": {область: путь}, "fingerprints": {...}}`.
    """
    d = run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    prints: dict[str, str] = {}
    for domain in domains:
        values = effective(domain, inst)
        doc = ({"parameters": values} if domain in _WRAPPED else dict(values))
        p = d / f"{domain}.json"
        p.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                     encoding="utf-8")
        files[domain] = str(p)
        prints[domain] = fingerprint(values)
    (d / "manifest.json").write_text(json.dumps({
        "run_id": str(run_id),
        "instance": _inst_id(inst),
        "fingerprints": prints,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"dir": d, "files": files, "fingerprints": prints}


def drop_run(run_id: str) -> None:
    """Убрать снимок вместе с артефактами прогона."""
    import shutil
    shutil.rmtree(run_dir(run_id), ignore_errors=True)


def _display_path(path: Path | None) -> str | None:
    """Путь для показа: относительный от корня репозитория, когда файл в
    нём лежит. Файл МОЖЕТ лежать снаружи — так работает изоляция тестов и
    так будет со снимками прогонов на отдельном томе; тогда отдаём путь
    как есть, а не роняем разрез `ValueError`."""
    if path is None:
        return None
    try:
        return path.relative_to(registry.REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def describe(domain: str, inst=None) -> dict:
    """Разрез для экрана и для отчётов: откуда взяты значения, какие они и
    чем отличаются от шаблона установки."""
    path, origin = resolve(domain, inst)
    values = effective(domain, inst)
    install = effective(domain, None)
    overridden = sorted(k for k in values
                        if k in install and values[k] != install[k])
    return {
        "domain": domain,
        "origin": origin,
        "path": _display_path(path),
        "values": values,
        "installation_values": install,
        "overridden": overridden,
    }
