# -*- coding: utf-8 -*-
"""Ведомость исполнения правил ВМП у построителя пейринга.

Внешний аудит 04.09.2026, находка A1 (класс ошибки: умолчания площадки
молча подменяли нормы ВМП). Гейт соответствия Ш-1п2 судил по реестру
исполнителей: «исполнитель у правила есть» — и считал правило покрытым.
Но исполнитель (например, `_pairing_builder.duty_limit_ok`) судит
ТОЛЬКО по переданному ему порогу; порог, оставшийся в следе, — это
правило без исполнения. Ведомость закрывает этот зазор: для каждого
правила пакета зоны задачи — правило, применимость к задаче и режиму,
параметры (имя / значение / единица / условие), исполнитель по реестру,
ФАКТИЧЕСКИ переданные построителю значения (ключи запуска и контракта)
и место исполнения. Жёсткое применимое правило без переданного значения
или без исполнителя — остановка кнопки словами.

Формат — общий контракт обеих сторон (план AUDIT-20260904-PLAN-CLD,
раздел 5, п. 2): часть «из пакета» (правило, применимость, параметры,
условия) даёт CDPM, часть «от исполнителей» (исполнитель, переданные
значения, место исполнения) — COSMOS. Ведомость пишется в каталог
нажатия (execution-manifest.json) и доступна ссылкой на странице.

Карта каналов исполнения ниже — зеркало ручек решателя
(RunThresholds.kt: параметр пакета → порог applied) и ключей
транслятора (vmp/pairing_input.builder_args: порог applied → ключ
построителя). Расширение карты — только вместе с исполнителем.

Перечень МЯГКИХ СЕМЕЙ ведомость своей рукой не держит (10.09.2026): она
читает опись исполнителей построителя `vmp.pairing_input.SOFT_EXECUTION`
— там же, где ключи и передаются. Зеркало перечня расходится с оригиналом
молча, и первая семья конвейера пополнения (09.09.2026) это показала.
"""
from __future__ import annotations

from vmp import pairing_input, registry_check

#: Вид ведомости (версия контракта).
MANIFEST_KIND = "ведомость исполнения правил ВМП (аудит A1, 04.09.2026)"

#: Параметр пакета (закрытый словарь посадок / имена params) → каналы
#: исполнения у построителя: пороги applied решателя, ключи запуска,
#: ключи контракта source-rules, канал данных, место исполнения,
#: задачи, где параметр исполняется (умолчание — pairing).
PARAM_EXECUTION: dict[str, dict] = {
    "max_duty_duration": {
        "value_semantics": ("duty_limits", "duration_minutes", "max"),
        "cli_type": "int",
        "contract_tiers": True,
        "applied": ("max_duty_span_min", "max_duty_duration_min",
                    "max_duty_duration"),
        "cli": ("--duty-span",),
        "contract": ("work_time_limit_by_tier",),
        "place": "генерация смен: предел рабочего времени "
                 "(_pairing_builder.duty_limit_ok, work_period с "
                 "брифингом/дебрифингом)"},
    # Правило максимальной стыковки / разделённой смены (ручка решателя
    # max_sit_duration → порог day_break_threshold_min; решение владельца
    # 06.09.2026): граница «стыковка / отдых» построителя — --sit-max из
    # него; без правила граница выводится из пола минимального отдыха
    # (vmp.sit_boundary), и параметр в пакете отсутствует.
    "max_sit_duration": {
        "value_semantics": ("connection_windows", "duration_minutes", "max"),
        "cli_type": "int",
        "applied": ("day_break_threshold_min",),
        "cli": ("--sit-max",),
        "contract": (),
        "place": "генерация смен: верх окна стыковки "
                 "(_pairing_builder.sit_cap_at / --sit-max) — пауза не "
                 "короче порога кончает смену и требует полного отдыха"},
    "min_turnaround": {
        "value_semantics": ("connection_windows", "duration_minutes", "min"),
        "cli_type": "int",
        "applied": ("min_sit_home_min", "min_sit_outstation_min",
                    "min_sit_min", "min_sit_home", "min_sit_outstation",
                    "min_sit"),
        "cli": ("--sit-min",),
        "contract": ("min_sit_min",),
        "place": "генерация смен: минимальная стыковка (строже из "
                 "применённых порогов дом/вне базы)"},
    "min_rest_home": {
        "value_semantics": ("rest_floors", "duration_minutes", "min"),
        "cli_type": "int",
        "contract_columns": (1, 3, 5),
        "applied": ("min_rest_home_min", "min_rest_home"),
        "cli": ("--rest-base", "--rest-base-long"),
        "contract": ("layover_tiers",),
        "place": "генерация цепочек: отдых между сменами на базе "
                 "(таблица отдыха контракта / --rest-base)"},
    "min_rest_outstation": {
        "value_semantics": ("rest_floors", "duration_minutes", "min"),
        "cli_type": "int",
        "contract_columns": (2, 4, 6),
        "applied": ("min_rest_outstation_min", "min_rest_outstation"),
        "cli": ("--rest-off", "--rest-off-long"),
        "contract": ("layover_tiers",),
        "place": "генерация цепочек: отдых между сменами вне базы "
                 "(таблица отдыха контракта / --rest-off)"},
    "max_landings_per_duty": {
        "value_semantics": ("duty_limits", "count", "max"),
        "cli_type": "int",
        "applied": ("max_landings_per_duty",),
        "cli": ("--max-segs",),
        "contract": ("max_landings_per_duty",),
        "place": "генерация смен: счёт посадок "
                 "(_pairing_builder.duty_limit_ok / предел перебора)"},
    "max_pairing_days": {
        "value_semantics": ("duty_limits", "count", "max"),
        "cli_type": "int",
        # pairing_input.builder_args transmits days * 1440. This is the
        # explicit CLI adapter, not a catalog count-to-duration conversion.
        "cli_unit": "duration_minutes",
        "cli_scale": 1440,
        "applied": ("max_pairing_days",),
        "cli": ("--pairing-span",),
        "contract": ("max_pairing_days",),
        "place": "генерация цепочек: предел суток командировки "
                 "(pairing_days_ok / --pairing-span)"},
    "base_tz_offset_min": {
        "applied": ("base_tz_offset_min",),
        "cli": ("--base-tz-offset-min",),
        "contract": ("base_tz_offset_min",),
        "place": "контракт построителя: шкала времени данных (окна "
                 "местного времени базы)"},
    "crew_size": {
        "applied": ("crew_size",),
        "cli": (),
        "contract": (),
        "data": "payload.flightAssignments (места участков набора)",
        "place": "данные набора: состав каталога по местам участков; "
                 "судья состава — решатель"},
    "fleet_types": {
        "applied": (),
        "cli": (),
        "contract": (),
        "tasks": ("rostering",),
        "place": "решатель ростеринга: допуск на тип и действие срока "
                 "(Type rating required / currency); в пейринге цепочки "
                 "безымянны — исполнения не требует"},
    "long_duty_threshold": {
        "applied": (),
        "cli": ("--long-duty-threshold", "--w-long-duty"),
        "contract": (),
        "place": "цена цепочки (отбор): штраф продлённой смены"},
    "duty_extension_cap": {
        "applied": (),
        "cli": ("--duty-cap",),
        "contract": (),
        "place": "предел смены построителя: потолок продления (--duty-cap из applied "
                 "max_duty_span_min.dutyExtensions, политика без адреса); штраф продления — от базы "
                 "(--long-duty-threshold); адресная политика в пейринге не исполняется (мера 100)"},
    "reserve_crews": {
        "applied": (), "cli": (), "contract": (),
        "tasks": ("rostering",),
        "place": "решатель ростеринга: нормы резерва по базам"},
    "workload_spread_threshold": {
        "applied": (), "cli": (), "contract": (),
        "tasks": ("rostering",),
        "place": "решатель ростеринга: разброс кредитных часов"},
    # Семьи истории сотрудника (05.09.2026, путь А): исполнители —
    # формы решателя из блока logic правила (ростеринг), построителю
    # пейринга эти параметры не нужны.
    "max_consecutive_night_duties": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: форма CONSECUTIVE_NIGHT_DUTIES_MAX "
                 "(блок logic правила)"},
    "night_duty_start": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: окно ночи формы "
                 "CONSECUTIVE_NIGHT_DUTIES_MAX (блок logic правила)"},
    "night_duty_end": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: окно ночи формы "
                 "CONSECUTIVE_NIGHT_DUTIES_MAX (блок logic правила)"},
    "weekly_rest_required": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: форма REST_IN_ROLLING_WINDOW "
                 "(блок logic правила)"},
    "weekly_rest_period": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: окно формы REST_IN_ROLLING_WINDOW "
                 "(блок logic правила)"},
    "min_days_off": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: форма DAYS_OFF_PER_MONTH_MIN "
                 "(блок logic правила)"},
    # Счётчики скользящих окон (05.09.2026): формы из посадки.
    "max_night_duties_in_window": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: форма NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX "
                 "(из посадки)"},
    "night_window_days": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: окно формы "
                 "NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX (из посадки)"},
    "min_days_off_in_window": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: форма DAYS_OFF_IN_ROLLING_WINDOW_MIN "
                 "(из посадки)"},
    "days_off_window_days": {
        "applied": (), "cli": (), "contract": (), "tasks": ("rostering",),
        "place": "решатель ростеринга: окно формы "
                 "DAYS_OFF_IN_ROLLING_WINDOW_MIN (из посадки)"},
}

#: Мягкие семьи без словарных параметров: коэффициент правила едет
#: построителю ключом веса (композиция целевой, канон ВМП 2026-09-02).
#: Перечень НЕ ПИШЕТСЯ ЗДЕСЬ — он у транслятора построителя
#: (`vmp.pairing_input.SOFT_EXECUTION`), который эти ключи и передаёт.
#: До 10.09.2026 ведомость держала «зеркало» того перечня своей рукой:
#: два списка одних и тех же семей расходятся молча, и первая семья
#: конвейера пополнения (09.09.2026) это показала.
FAMILY_EXECUTION: dict[str, dict] = pairing_input.SOFT_EXECUTION

ZONES_OF_TASK = {"pairing": ("PAIRING", "BOTH"),
                 "rostering": ("ROSTERING", "BOTH")}
SOFT_MODES = ("optimization", "feasibility-soft")


def rule_params(rule: dict) -> list[dict]:
    """Параметры правила из пакета: посадка на шаблон (template_fit.params,
    первична) и машинные params — по имени, посадка главнее. Каждая
    запись: name / value / unit / relation / condition / quote."""
    out: dict[str, dict] = {}
    fit = rule.get("template_fit") \
        if isinstance(rule.get("template_fit"), dict) else {}
    for name, cell in (fit.get("params") or {}).items():
        cell = cell if isinstance(cell, dict) else {"value": cell}
        out[str(name)] = {
            "name": str(name), "value": cell.get("value"),
            "unit": cell.get("unit"), "relation": cell.get("relation"),
            "condition": cell.get("condition"),
            "quote": cell.get("quote"), "from": "template_fit"}
    for p in rule.get("params") or ():
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name = str(p["name"])
        if name in out:
            for k in ("unit", "relation", "condition"):
                if out[name].get(k) is None and p.get(k) is not None:
                    out[name][k] = p.get(k)
            continue
        out[name] = {"name": name, "value": p.get("value"),
                     "unit": p.get("unit"), "relation": p.get("relation"),
                     "condition": p.get("condition"),
                     "quote": p.get("as_written"), "from": "params"}
    return list(out.values())


def rule_family(rule: dict) -> str | None:
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B") \
            and fit.get("family"):
        return str(fit["family"])
    exp = rule.get("export_solver")
    if isinstance(exp, dict) and exp.get("family"):
        return str(exp["family"])
    return None


def _applies(rule: dict, mode: str | None) -> tuple[bool, str]:
    kind = rule.get("kind")
    if kind == "SOFT":
        if mode in SOFT_MODES:
            return True, "мягкое правило: слагаемое цены цепочки в " \
                         "режиме %s" % mode
        return False, "мягкое правило в режиме %s не судится" % mode
    if kind in ("DATA", "OUT"):
        return False, "данные набора / вне автоматического планирования"
    return True, "жёсткое правило (или определение с нормой)"


def _cli_value(bargs: list, key: str):
    """Значение ключа запуска: следующий аргумент, если он не ключ;
    флаг без значения — True. Нет ключа — None.

    Ключ может стоять в строке запуска ДВАЖДЫ по замыслу: критерий
    пакета обнуляет веса штрафов («чистота цели», Г-П4 п. 7.1), а мягкое
    правило ВМП ниже по строке ставит свой вес — построитель (argparse)
    берёт ПОСЛЕДНИЙ, и происхождение веса записано за правилом
    (tests/vmp/test_pairing_input.py: «последний --w-night побеждает»).
    Судить надо то, что исполняется: последнее вхождение. Живой случай
    09.09.2026 — пакет Н0-B с критерием и мягким правилом «штраф за
    ночёвку вне базы» (вес 10): по первому вхождению (0 критерия)
    манифест исполнения останавливал расчёт словами «две правды о цене»,
    хотя построителю уходило 10."""
    if key not in bargs:
        return None
    i = len(bargs) - 1 - bargs[::-1].index(key)
    if i + 1 < len(bargs) and not str(bargs[i + 1]).startswith("--"):
        return bargs[i + 1]
    return True


def _duty_cap_contract(name: str, key: str, value, bargs: list):
    """Потолок продления на канале контракта (Stage 03.10, РВ-108).

    Путь пакета ВМП судит предел смены контрактом правил: при поданном
    потолке продления (--duty-cap, мера 100) контракт несёт потолок, а
    база правила предела смены едет ключом --duty-span. Канал контракта
    принимается как потолок ЛИШЬ если потолок подан, выше базы и все
    ярусы контракта ровно равны ему; иначе — обычная сверка с ВМП
    (иное число — две правды). Возврат — потолок либо None."""
    if name != "max_duty_duration" or key != "work_time_limit_by_tier":
        return None
    try:
        cap = int(_cli_value(bargs, "--duty-cap"))
        base = int(_cli_value(bargs, "--duty-span"))
    except (TypeError, ValueError):
        return None
    if cap <= base or not isinstance(value, list) or not value:
        return None
    if any(type(v) is not int or v != cap for v in value):
        return None
    return cap


def _typed_value_problem(rule, param, passed, rules, registry, task, mode):
    """Compare typed thresholds at each actual transmission channel.

    Count and duration units are distinct. Pairing-days has an explicit CLI
    adapter; this proves transmission, not the calendar-day executor.
    A stricter value needs another applicable HARD formulation row with the
    same parameter scope and the same actual canonical threshold.
    """
    fit = rule.get("template_fit") or {}
    spec = PARAM_EXECUTION.get(param["name"], {})
    if (fit.get("schema_version") != "landing/2"
            or "value_semantics" not in spec):
        return None
    from template_catalog import CatalogError
    from template_catalog.units import to_canonical
    from vmp import catalog_access

    family, semantic_unit, semantic_relation = spec["value_semantics"]
    unit = param.get("unit")
    is_cli = passed.get("channel") == "ключ запуска"
    channel_unit = spec.get("cli_unit", semantic_unit) if is_cli else semantic_unit
    channel_scale = spec.get("cli_scale", 1) if is_cli else 1
    passed["applied_unit"] = channel_unit
    if (rule_family(rule) != family or param.get("relation") != semantic_relation
            or unit != semantic_unit or param.get("condition") is not None):
        passed["comparison"] = "не проверяемо"
        return ("порог %s не проверяем: единица, отношение или область "
                "параметра не соответствуют глобальному каналу %s"
                % (param["name"], family))
    values = passed.get("value")
    if passed.get("channel") == "контракт" and spec.get("contract_columns"):
        # The first column selects duty duration; only the declared side's
        # ordinary/night/trans-timezone rest columns carry this parameter.
        if (not isinstance(values, list) or not values
                or any(not isinstance(row, list) or len(row) != 7 for row in values)):
            passed["comparison"] = "не проверяемо"
            return "порог %s не проверяем: неверная семиколоночная таблица отдыха" % param["name"]
        values = [row[column] for row in values for column in spec["contract_columns"]]
    elif passed.get("channel") == "контракт" and spec.get("contract_tiers"):
        if not isinstance(values, list) or not values:
            passed["comparison"] = "не проверяемо"
            return "порог %s не проверяем: пустая или неверная таблица ярусов" % param["name"]
    else:
        values = [values]
    try:
        expected = to_canonical(param.get("value"), unit, unit,
                                source=catalog_access.catalog_path(), exact=True)
        # Only the integral count adapter uses a scale; avoid Decimal
        # context rounding while preserving the catalog's type validation.
        if channel_scale != 1:
            expected = int(expected) * channel_scale
        if is_cli:
            # These actual builder options use argparse type=int. A written
            # quantity or clock can normalize to minutes yet be rejected by
            # that parser: validate transport before comparing its magnitude.
            if spec.get("cli_type") != "int" or any(
                    not isinstance(value, str) for value in values):
                raise ValueError("канал запуска требует строку целого числа")
            values = [int(value) for value in values]
        actuals = [to_canonical(value, channel_unit, channel_unit,
                               source=catalog_access.catalog_path(), exact=True)
                   for value in values]
    except (CatalogError, TypeError, ValueError) as exc:
        passed["comparison"] = "не проверяемо"
        return "порог %s не проверяем: %s" % (param["name"], exc)
    # A non-uniform tier table needs an explicit crew-scope mapping; taking
    # max(table) would hide a different threshold in another reachable tier.
    if len(set(actuals)) != 1:
        passed["comparison"] = "не проверяемо"
        return ("порог %s в ВМП %s %s, переданы ярусы %r: область каждого "
                "яруса не доказана — сравнение не проверяемо" % (
                    param["name"], param.get("value"), unit, passed.get("value")))
    actual = actuals[0]
    if expected == actual:
        passed["comparison"] = "совпало"
        return None

    relation = param.get("relation")
    stricter = ((relation == "max" and actual < expected)
                or (relation == "min" and actual > expected))
    covered = {str(r.get("rule_id")) for r in (registry or {}).get("rules", [])
               if r.get("verdict") == "covered_full"}
    if stricter:
        for other in rules:
            if (other is rule or str(other.get("id")) == str(rule.get("id"))
                    or other.get("kind") != "HARD"
                    or other.get("zone") not in ZONES_OF_TASK.get(task, ())
                    or (registry is not None and str(other.get("id")) not in covered)
                    or registry_check.is_excluded_until_data(other)
                    or not _applies(other, mode)[0]
                    or (other.get("template_fit") or {}).get("schema_version") != "landing/2"):
                continue
            for candidate in rule_params(other):
                if (candidate["name"] != param["name"]
                        or candidate.get("relation") != relation
                        or candidate.get("unit") != unit
                        or rule_family(other) != family
                        or candidate.get("condition") != param.get("condition")):
                    continue
                try:
                    other_value = to_canonical(
                        candidate.get("value"), candidate.get("unit"), unit,
                        source=catalog_access.catalog_path(), exact=True)
                    if channel_scale != 1:
                        other_value = int(other_value) * channel_scale
                except (CatalogError, TypeError, ValueError):
                    continue
                if other_value == actual:
                    passed["comparison"] = "ужесточено правилом " + str(other["id"])
                    passed["explained_by"] = str(other["id"])
                    return None
    passed["comparison"] = "расходится"
    return ("порог %s в ВМП %s %s, построителю передан %s=%s %s — "
            "две правды о параметре" % (
                param["name"], param.get("value"), unit, passed["key"],
                passed["value"], passed["applied_unit"]))


def thresholds_gaps(rules: list, applied: dict, task: str = "pairing") -> list:
    """Пробелы порогов ДО трансляции: жёсткое правило зоны задачи с
    параметром, чей порог решатель обязан был применить (карта applied),
    но в applied его нет — исполнитель не получит норму ВМП. Возврат —
    перечень гапов формы conformance_gaps (kind rule_threshold_missing).
    Одна функция для матрицы готовности (Ш-2м) и кнопки (Ш-1п3)."""
    zones = ZONES_OF_TASK.get(task, ())
    applied = applied if isinstance(applied, dict) else {}
    gaps = []
    for rule in rules or ():
        if not isinstance(rule, dict) or rule.get("zone") not in zones:
            continue
        if rule.get("kind") not in ("HARD", "DEF") \
                or registry_check.is_excluded_until_data(rule):
            continue
        for p in rule_params(rule):
            spec = PARAM_EXECUTION.get(p["name"])
            if spec is None or task not in spec.get("tasks", ("pairing",)):
                continue
            names = spec.get("applied") or ()
            if not names:
                continue
            present = [n for n in names if applied.get(n) is not None]
            if present:
                for name in present:
                    passed = {"param": p["name"], "channel": "applied",
                              "key": name, "value": applied[name]}
                    problem = _typed_value_problem(
                        rule, p, passed, rules, None, task, "feasibility-hard")
                    if problem:
                        gaps.append({"kind": "rule_threshold_mismatch",
                                     "rule_id": str(rule.get("id")),
                                     "title": rule.get("title") or "",
                                     "param": p["name"], "note": problem,
                                     "passed": passed})
                continue
            gaps.append({
                "kind": "rule_threshold_missing",
                "rule_id": str(rule.get("id")),
                "title": rule.get("title") or "",
                "param": p["name"],
                "note": "параметр %s правила %s (значение %r) не "
                        "применён решателем: в applied нет ни одного "
                        "из порогов %s — исполнитель построителя не "
                        "получит норму ВМП"
                        % (p["name"], rule.get("id"), p.get("value"),
                           ", ".join(names))})
    return gaps


def build(rules: list, registry: dict | None, task: str, mode: str | None,
          bargs: list, prov: dict, contract: dict | None,
          source_rules_fp: str | None = None) -> dict:
    """Ведомость исполнения. rules — правила модели пакета (байты
    пакета); registry — реестр исполнителей нажатия; bargs/prov —
    аргументы построителя и след происхождения; contract — контракт
    source-rules, поданный в запуск (None = не подан)."""
    zones = ZONES_OF_TASK.get(task, ())
    by_id = {r.get("rule_id"): r
             for r in ((registry or {}).get("rules") or [])
             if isinstance(r, dict)}
    contract = contract if isinstance(contract, dict) else None
    rows: list[dict] = []
    blocking: list[dict] = []
    for rule in rules or ():
        if not isinstance(rule, dict) or rule.get("zone") not in zones:
            continue
        rid = str(rule.get("id"))
        rec = by_id.get(rid) or {}
        fam = rule_family(rule)
        applies, why = _applies(rule, mode)
        row = {
            "rule_id": rid, "title": rule.get("title") or "",
            "kind": rule.get("kind"), "zone": rule.get("zone"),
            "family": fam,
            "applicability": {"task": task, "mode": mode,
                              "applies": applies, "note": why},
            "params": rule_params(rule),
            "executor": {"registry_verdict": rec.get("verdict"),
                         "evidence": rec.get("evidence")
                         or rec.get("gap_note")},
            "passed": [], "place": None, "status": None, "note": None,
        }
        rows.append(row)
        if registry_check.is_excluded_until_data(rule):
            row["status"] = "не требуется"
            row["note"] = ("исключено до получения данных по решению "
                           "авиакомпании")
            continue
        if not applies:
            row["status"] = "не судится"
            row["note"] = why
            continue
        if rule.get("kind") in ("DATA", "OUT"):
            row["status"] = "не требуется"
            continue
        if rec.get("verdict") == "data_or_out_of_scope":
            row["status"] = "не требуется"
            row["note"] = rec.get("evidence")
            continue
        if rec and rec.get("verdict") not in registry_check.FINAL_VERDICTS:
            row["status"] = "НЕ ИСПОЛНЯЕТСЯ"
            row["note"] = "реестр: %s" % (rec.get("gap_note")
                                          or rec.get("verdict"))
            blocking.append({"kind": "rule_not_executed", "rule_id": rid,
                             "title": row["title"], "note": row["note"]})
            continue
        missing: list[str] = []
        places: list[str] = []
        other_task: list[str] = []
        for p in row["params"]:
            spec = PARAM_EXECUTION.get(p["name"])
            if spec is None:
                missing.append("параметр %s вне карты исполнения "
                               "построителя (значение %r)"
                               % (p["name"], p.get("value")))
                continue
            if task not in spec.get("tasks", ("pairing",)):
                other_task.append(p["name"])
                places.append(spec["place"])
                row["passed"].append({
                    "param": p["name"], "channel": "другая задача",
                    "key": None, "value": None,
                    "note": "исполняется в задаче %s"
                            % "/".join(spec.get("tasks", ()))})
                continue
            places.append(spec["place"])
            got = False
            for key in spec.get("cli") or ():
                v = _cli_value(bargs, key)
                if v is not None:
                    got = True
                    row["passed"].append({
                        "param": p["name"], "channel": "ключ запуска",
                        "key": key, "value": v,
                        "from": (prov.get(key) or {}).get("from")
                        if isinstance(prov.get(key), dict) else None})
            for key in spec.get("contract") or ():
                if contract is not None and contract.get(key) is not None:
                    got = True
                    entry = {
                        "param": p["name"], "channel": "контракт",
                        "key": "source-rules.json:%s" % key,
                        "value": contract.get(key)}
                    cap = _duty_cap_contract(p["name"], key,
                                             contract.get(key), bargs)
                    if cap is not None:
                        entry["cap_of"] = "--duty-cap"
                        entry["comparison"] = (
                            "потолок продления --duty-cap %d; база правила "
                            "судится ключом --duty-span" % cap)
                    row["passed"].append(entry)
            if spec.get("data"):
                got = True
                row["passed"].append({
                    "param": p["name"], "channel": "данные",
                    "key": spec["data"], "value": None})
            if not got:
                missing.append(
                    "параметр %s (значение %r) не передан построителю: "
                    "нет ни ключа запуска %s, ни ключа контракта %s"
                    % (p["name"], p.get("value"),
                       "/".join(spec.get("cli") or ()) or "—",
                       "/".join(spec.get("contract") or ()) or "—"))
            for passed in row["passed"]:
                if passed.get("cap_of"):
                    continue
                if passed.get("param") == p["name"]:
                    value_problem = _typed_value_problem(
                        rule, p, passed, rules, registry, task, mode)
                    if value_problem:
                        missing.append(value_problem)
        if not row["params"] and rule.get("kind") == "SOFT":
            spec = FAMILY_EXECUTION.get(fam or "")
            if spec is None:
                missing.append("мягкое правило семьи %r без исполнителя "
                               "цены у построителя" % (fam,))
            else:
                places.append(spec["place"])
                for key in spec["cli"]:
                    v = _cli_value(bargs, key)
                    if v is not None:
                        row["passed"].append({
                            "param": None, "channel": "ключ запуска",
                            "key": key, "value": v,
                            "from": (prov.get(key) or {}).get("from")
                            if isinstance(prov.get(key), dict) else None})
                if not row["passed"]:
                    missing.append("вес правила не передан построителю "
                                   "(нет ключей %s)" % "/".join(spec["cli"]))
                else:
                    # Вес пакета обязан совпасть с переданным ключом
                    # веса (первый ключ семьи): расхождение — две правды
                    # о цене, штраф в цели не тот, что в ВМП.
                    w = rule.get("weight")
                    got_w = _cli_value(bargs, spec["cli"][0])
                    if isinstance(w, int) and not isinstance(w, bool) \
                            and got_w is not None \
                            and str(got_w) != str(w):
                        missing.append(
                            "вес правила в пакете %s, построителю передан "
                            "%s=%s — две правды о цене" % (w, spec["cli"][0],
                                                          got_w))
        row["place"] = "; ".join(dict.fromkeys(places)) or None
        if missing:
            row["status"] = "НЕ ИСПОЛНЯЕТСЯ"
            row["note"] = "; ".join(missing)
            blocking.append({"kind": "rule_not_executed", "rule_id": rid,
                             "title": row["title"], "note": row["note"]})
        elif not row["params"]:
            row["status"] = "исполнитель по реестру"
            row["note"] = ("машинных параметров у правила нет — "
                           "значений не требует; исполнитель: %s"
                           % (rec.get("evidence") or "по реестру нажатия"))
        elif other_task and len(other_task) == len(row["params"]):
            row["status"] = "исполняется в другой задаче"
        else:
            row["status"] = "исполняется"
    return {"kind": MANIFEST_KIND, "task": task, "mode": mode,
            "source_rules_fp": source_rules_fp,
            "rules": rows, "blocking": blocking}
