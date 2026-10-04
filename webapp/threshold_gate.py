# -*- coding: utf-8 -*-
"""Сравнение порогов пакета постановки с порогами, по которым считает решатель.

Мост CDPM -> COSMOS, этап В, пункт (б). До ревизии спецификации 1.3
сравнивать было нечего: пороги жёстких правил жили ТОЛЬКО в тексте
формулировок, и сопоставить их можно было лишь разбором чужой прозы —
запрещённый класс. С 1.3 у правил появились машинные параметры
{name, value, unit, relation, qualifier}, и сравнение стало
арифметикой.

ОТКУДА БЕРУТСЯ НАШИ ЧИСЛА. Не отсюда. Модуль не знает ни одного порога
решателя и не хранит их копий: копия — второй источник правды, который
разойдётся с первым молча. Показания приходят аргументом — те самые,
что решатель отдаёт в отчёте применённого для профиля, которым он
СЧИТАЛ (`lastRun.profileReadings`). Профиль по умолчанию и профили
GERAD живут разными числами, и подставлять одни вместо других нельзя:
на этом уже обжигались (ревизия 2 отчёта приёмки этапа Б).

ВЕРДИКТ ТРЁХЗНАЧНЫЙ, и третье значение здесь не вежливость.
«Совпадает» и «расходится» описывают случай, когда величины вообще
сопоставимы. Но у решателя ОДНА величина отдыха против ДВУХ параметров
пакета (в базе 720, вне базы 600) — и её совпадение с внебазовой
нормой не означает согласия: оно означает промах мимо базовой ровно на
разницу. Назвать такое «совпадает» — та же ложь, что зелёная проверка,
которая ничего не проверяет: при следующем изменении порога расхождение
всплывёт как «вдруг покраснело совпадавшее», и никто не вспомнит, что
оно и не совпадало. Поэтому третий вердикт — НЕСРАВНИМО, с названной
причиной.
"""

from __future__ import annotations

import hashlib
import json
import math

MATCH = "совпадает"
DIVERGE = "расходится"
INCOMPARABLE = "несравнимо"

#: Соответствия «параметр -> показание» модуль НЕ хранит: их объявляет
#: решатель в отчёте применённого (thresholds.paramToThreshold), и
#: сравнение принимает их аргументом. Копия таблицы здесь была бы вторым
#: источником правды — ровно тем, что этот модуль запрещает для чисел.

#: Параметры, у которых встречной величины у решателя НЕТ вовсе.
#: Перечислены поимённо, чтобы «нет исполнителя» отличалось от «имя
#: неизвестно»: первое — известная граница, второе — повод для отказа.
NO_READING: dict[str, str] = {
    # crew_size и reserve_crews ушли отсюда 2026-08-23: у обоих появились
    # исполнители (строки 7 и 8 свода), и «встречной величины нет» стало бы
    # ложью — имя без цели теперь означает дефект отчёта, не границу.
    "duty_rig": "кредитные часы решатель не считает (этап В, пункт кредита)",
    "trip_rig": "кредитные часы решатель не считает",
    "min_duty_credit": "кредитные часы решатель не считает",
    "monthly_guarantee": "кредитные часы решатель не считает",
    "earliest_departure": "границы времени суток решатель не исполняет",
    "latest_arrival": "границы времени суток решатель не исполняет",
    # Волна условий (этап 5б, 2026-08-27): нормы подготовки и разбора
    # исполняет файл условий построителя пейринга; встречной величины
    # в показаниях решателя нет — это спроектированная граница, а не
    # «имя неизвестно» (ревью волны, находка 14).
    "briefing_min": "норму подготовки исполняет файл условий "
                    "построителя; встречной величины у решателя нет",
    "debriefing_min": "норму разбора исполняет файл условий "
                      "построителя; встречной величины у решателя "
                      "нет",
}

#: Единицы, сравнимые арифметически. Смешение природы — отказ, а не
#: арифметика: длительность и время суток обе меряются минутами и
#: сравнению НЕ подлежат.
COMPARABLE_UNITS = {"duration_minutes", "count", "credit_hours"}


class ThresholdGateError(RuntimeError):
    """Сравнение невозможно — с названной причиной, а не молча."""


def _finite_number(value) -> bool:
    """JSON numbers are evidence; strings and booleans are not numbers here."""
    return ((isinstance(value, int) and not isinstance(value, bool))
            or (isinstance(value, float) and math.isfinite(value)))


def projected_params(rule: dict, *, numeric_only: bool = False) -> list[dict]:
    """Read canonical landing without mutating the received model.

    Numeric normalization reuses the producer's lossless projection, including
    carrier consistency, qualifiers and repeated legacy records. Opaque codes
    are copied for reporting, never serialized into a legacy comma-separated
    list. Numeric checks do not judge that unrelated representability limit.
    """
    import copy
    from cdpm import catalog_bridge, model as m
    from cdpm.param_projection import ParamProjectionError, derive_params_from_landing

    try:
        fit = rule.get("template_fit")
        legacy = rule.get("params") or []
        if not isinstance(fit, dict) or fit.get("category") not in ("A", "B") \
                or not isinstance(legacy, list) or any(not isinstance(p, dict)
                or not isinstance(p.get("name"), str) for p in legacy):
            return derive_params_from_landing(rule)
        units = m.PARAMS
        code_names = {name for name, unit in units.items() if unit in m.CODE_UNITS}
        # Unknown legacy numeric records still need an explicit verdict.
        selected = (set(units) | {p["name"] for p in legacy}) - code_names
        out = derive_params_from_landing(rule, selected_names=selected)
        if numeric_only:
            return out
        cells = fit.get("params") or {}
        codes = [copy.deepcopy(p) for p in legacy if p["name"] in code_names]
        for name, cell in cells.items():
            if name not in code_names:
                continue
            previous = [p for p in codes if p["name"] == name]
            if not previous:
                previous = [{"name": name, "as_written": cell.get("quote")}]
                codes.extend(previous)
            for record in previous:
                record.update(value=copy.deepcopy(cell.get("value")),
                              unit=units[name], quote=cell.get("quote"))
                for key in ("source", "tz"):
                    if key in cell:
                        record[key] = copy.deepcopy(cell[key])
                if "relation" in cell:
                    record["relation"] = cell["relation"]
                if "condition" in cell:
                    condition = copy.deepcopy(cell["condition"])
                    record["condition"] = condition
                    if condition is None:
                        record.pop("qualifier", None)
                    else:
                        record["qualifier"] = (condition["value"]
                            if fit.get("schema_version") == "landing/2" else condition)
        return out + codes
    except (ParamProjectionError, *catalog_bridge.CATALOG_ERRORS) as exc:
        raise ThresholdGateError(
            f"{rule.get('id')}: параметры правила нельзя сравнить: {exc}"
        ) from exc


def collect_params(pkg: dict, *, kinds=("HARD",)) -> list[dict]:
    """Параметры правил пакета, годные к сравнению.

    Правила вида OUT (значение отменено авиакомпанией) в сравнение НЕ
    берутся, но и не пропадают: вызывающая сторона получает их отдельно
    через `excluded_params` — сравнить с отменённым значением значило бы
    поверять решатель тем, что заказчик уже отозвал.
    """
    out: list[dict] = []
    # Исключённые до получения данных правила решатель не применяет
    # (структура ВМП 2026-09-02) — сравнивать нечего; они видны в
    # excluded_params.
    for rule in _rules_for_compare(pkg):
        if str(rule.get("kind")) not in kinds:
            continue
        for p in projected_params(rule):
            item = dict(p)
            item["rule"] = rule.get("id")
            item["zone"] = rule.get("zone")
            out.append(item)
    return out


def excluded_params(pkg: dict) -> list[dict]:
    """Параметры отменённых, не-жёстких и исключённых до получения
    данных правил — для явного отчёта."""
    from vmp.registry_check import is_excluded_until_data
    out: list[dict] = []
    for rule in ((pkg.get("model") or {}).get("rules") or []):
        excluded = is_excluded_until_data(rule)
        if str(rule.get("kind")) == "HARD" and not excluded:
            continue
        for p in projected_params(rule):
            item = dict(p)
            item["rule"] = rule.get("id")
            item["kind"] = ("excluded_until_data" if excluded
                            else rule.get("kind"))
            out.append(item)
    return out


def _norm_qualifier(q) -> str:
    """Нормализация уточнителя — та же, что у решателя: пробелы и регистр."""
    return " ".join(str(q or "").split()).lower()


def conflict_qualifier(param: dict) -> str:
    """Use existing area aliases only for catalogue-declared area parameters.

    This is a comparison key, not a rewrite of the original trace or the
    consumer's target map. Other qualifiers keep their literal meaning.
    """
    from cdpm import catalog_bridge, model as m
    qualifier = _norm_qualifier(param.get("qualifier"))
    canonical = m.canon_area_qualifier(qualifier)
    if canonical is not None:
        try:
            if param.get("name") in m.AREA_PARAM_NAMES:
                return canonical
        except catalog_bridge.CATALOG_ERRORS as exc:
            raise ThresholdGateError(
                f"{param.get('name')}: область параметра нельзя сравнить: {exc}"
            ) from exc
    return qualifier


def _rules_for_compare(pkg: dict) -> list[dict]:
    """Правила модели пакета, участвующие в сравнении порогов: без
    исключённых до получения данных (решатель их не применяет —
    аудит №2)."""
    from vmp.registry_check import is_excluded_until_data
    return [r for r in ((pkg.get("model") or {}).get("rules") or [])
            if isinstance(r, dict) and not is_excluded_until_data(r)]


def kind_conflicts(pkg: dict) -> list[dict]:
    """Одно имя параметра с РАЗНЫМИ числами в правилах разных видов.

    Класс найден живьём на наборе «Лайт» (2026-08-23): порог отдыха дома
    720 пришёл правилом вида HARD и был применён решателем, а правило
    «не менее 12:30» (750) осталось видом DATA — решатель его не
    применяет, а независимый сертификатор проверяет, и ростер получает
    13 нарушений при зелёном сравнении порогов. Каждое сравнение по
    отдельности верно; неверно молчание о том, что пакет требует двух
    разных чисел одного и того же.

    Сравнение таких правил невозможно (какое из чисел «правда» — решает
    производитель постановки), но молчать о них запрещено.
    """
    seen: dict[tuple[str, str], dict[int | float, set[str]]] = {}
    kinds: dict[tuple[str, str], dict[str, set[str]]] = {}
    for rule in _rules_for_compare(pkg):
        kind = str(rule.get("kind"))
        if kind == "OUT":                       # отменённое заказчиком — не требование
            continue
        for p in projected_params(rule, numeric_only=True):
            v = p.get("value")
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            key = (str(p.get("name")), conflict_qualifier(p))
            # Credit-hour parameters may be fractional. Truncation would
            # collapse distinct requirements before checking their kinds.
            seen.setdefault(key, {}).setdefault(v, set()).add(str(rule.get("id")))
            kinds.setdefault(key, {}).setdefault(kind, set()).add(str(rule.get("id")))
    out: list[dict] = []
    for key, by_value in seen.items():
        if len(by_value) < 2 or len(kinds.get(key, {})) < 2:
            continue
        name, qual = key
        out.append({
            "name": name,
            "qualifier": qual,
            "values": sorted(by_value),
            "by_kind": {k: sorted(v) for k, v in sorted(kinds[key].items())},
            "rules": sorted({r for rs in by_value.values() for r in rs}),
        })
    return out


def _targets(p: dict, mapping: dict, qualified: dict | None) -> list[str]:
    """Показания решателя, к которым метит параметр — с учётом уточнителя.

    Решатель объявляет соответствие сам: имя -> показание (либо СПИСОК
    показаний, если требование без уточнителя метит в обе области) и
    «имя|уточнитель» -> показание для уточнённых. Уточнитель
    сопоставляется ТОЧНО (ревизия 3 брала префикс — «база PLY» уходила в
    домашнюю ручку). «имя|*» — подстановочный ключ решателя для
    уточнителя, которого ручка не различает: такое требование он
    применяет ко всей ручке только как ужесточение, а ослабление кладёт
    в ignored, и сюда оно не доходит. Нет таблицы уточнителей — нет
    цели: домысливать «ко всей сети» здесь запрещено.
    """
    name = str(p.get("name"))
    q = _norm_qualifier(p.get("qualifier"))
    if q:
        if not qualified:
            return []
        table: dict = {}
        for key, target in qualified.items():
            kname, _, kq = str(key).partition("|")
            table[(kname, kq if kq == "*" else _norm_qualifier(kq))] = target
        target = table.get((name, q))
        if target is None:
            target = table.get((name, "*"))
    else:
        target = mapping.get(name)
    if target is None:
        return []
    return [str(t) for t in target] if isinstance(target, list) else [str(target)]


def _group(params: list[dict], mapping: dict,
           qualified: dict | None = None) -> dict[str, list[dict]]:
    """Параметры по имени показания решателя, к которому они метят."""
    by_reading: dict[str, list[dict]] = {}
    for p in params:
        for key in _targets(p, mapping, qualified):
            by_reading.setdefault(key, []).append(p)
    return by_reading


def unjudged_claimed_params(verdicts, applied):
    """A declared applied parameter without a comparison is not proven applied.

    Names come from the actual resolver report, not a Python copy of its map.
    Advisory incomparable parameters with no applied claim remain distinct.
    """
    claimed = {name for row in (applied or []) for name in (row.get("fromParams") or [])}
    return {name for verdict in verdicts if verdict["verdict"] == INCOMPARABLE
            for name in verdict["params"] if name in claimed}


def _code_labels(value, unit, address, *, measured=False):
    """Typed labels keep their boundaries/case; only legacy lists split commas."""
    from cdpm.model import split_code_list
    if measured:
        labels = value
    elif unit == "code":
        labels = [value]
    elif isinstance(value, str):
        labels = split_code_list(value)
    else:
        labels = value
    if (not isinstance(labels, list)
            or any(not isinstance(s, str) or not s.strip() for s in labels)
            or (not measured and not labels)
            or (measured and len(labels) != len(set(labels)))):
        raise ThresholdGateError(f"{address}: нужны точные непустые обозначения, "
                                 "не строковое представление списка или null")
    return sorted(set(labels))


def _policy_rows(rows, address):
    if not isinstance(rows, list):
        raise ThresholdGateError(f"{address}: политики продления не список")
    out = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "rule", "cap_min", "extension_bases", "extension_consent"}:
            raise ThresholdGateError(f"{address}: неполная/неизвестная форма политики")
        rid, cap = row["rule"], row["cap_min"]
        if (not isinstance(rid, str) or not rid.strip() or type(cap) is not int
                or cap <= 0 or type(row["extension_consent"]) is not bool):
            raise ThresholdGateError(f"{address}: правило, потолок или условие согласия неверны")
        bases = row["extension_bases"]
        if bases is not None:
            bases = _code_labels(bases, "code_list", address, measured=True)
            if not bases:
                raise ThresholdGateError(f"{address}: пустая область баз")
        out.append(dict(row, extension_bases=bases))
    if len({r["rule"] for r in out}) != len(out):
        raise ThresholdGateError(f"{address}: повторная политика одного правила")
    return sorted(out, key=lambda r: r["rule"])


def _expected_extension_policies(pkg):
    """Independent source projection: neither Applied nor provider declarations."""
    from vmp.run.roster_certify import _rule_family
    expected = []
    for rule in _rules_for_compare(pkg):
        if rule.get("kind") == "OUT" or _rule_family(rule) != "duty_extension_penalty":
            continue
        rid = rule.get("id")
        # Existing landing validation/carrier consistency must still run.
        projected_params(rule, numeric_only=True)
        fit = rule.get("template_fit")
        cells = fit.get("params") if isinstance(fit, dict) else {}
        if not isinstance(cells, dict):
            raise ThresholdGateError(f"{rid}: нет читаемых ячеек политики продления")
        def value(name):
            if name not in cells:
                return None
            cell = cells[name]
            if (not isinstance(cell, dict) or cell.get("value") is None
                    or "pending_revision" in cell):
                raise ThresholdGateError(f"{rid}/{name}: условие неизвестно/пересматривается")
            return cell["value"]
        if any(n.startswith("extension_") and n not in {
                "extension_bases", "extension_consent"} for n in cells):
            raise ThresholdGateError(f"{rid}: неподдержанное условие продления")
        cap = value("duty_extension_cap")
        legacy = [p.get("value") for p in (rule.get("params") or [])
                  if p.get("name") == "max_duty_duration"]
        if cap is None and legacy:
            cap = legacy[0]
        if any(v != cap or type(v) is not int for v in legacy):
            raise ThresholdGateError(f"{rid}: носители потолка расходятся")
        bases = value("extension_bases")
        if bases is not None:
            bases = _code_labels(bases, "code_list", f"{rid}/extension_bases")
        consent = value("extension_consent") if "extension_consent" in cells else False
        expected.append({"rule": rid, "cap_min": cap,
                         "extension_bases": bases, "extension_consent": consent})
    return _policy_rows(expected, "пакет/duty_extension_penalty")


def _compare_policy_readings(pkg, readings, applied, policy_readings):
    expected = _expected_extension_policies(pkg)
    # The mapping here is the structured report's schema, not an invented
    # parameter-to-number table. The base remains compared by the usual map.
    key = "max_duty_span_min"
    declared = [a for a in (applied or []) if a.get("threshold") == key]
    actual = (policy_readings or {}).get(key)
    if not expected and not actual and not any(a.get("dutyExtensions") for a in declared):
        return []
    group = [{"rule": p["rule"]} for p in expected]
    if actual is None:
        return [_v(DIVERGE, ["duty_extension_cap"], key, None, expected,
                   "созданный провайдер не сообщил адресные политики продления", group)]
    if not isinstance(actual, dict) or set(actual) != {
            "base_min", "policies", "base_source", "consent_match"}:
        raise ThresholdGateError(f"{key}/profilePolicyReadings: неполная форма")
    observed = _policy_rows(actual["policies"], "profilePolicyReadings")
    if len(declared) != 1:
        raise ThresholdGateError(f"{key}: нет единственной декларации базового предела")
    claimed = _policy_rows(declared[0].get("dutyExtensions"), "applied.dutyExtensions")
    same = (expected == observed == claimed
            and type(actual["base_min"]) is int and actual["base_min"] > 0
            and actual["base_min"] == readings.get(key) == declared[0].get("value")
            and actual["base_source"] == "employee.homeAirport.code"
            and actual["consent_match"] == "employee+exact_duty_start_end_payload_scale")
    return [_v(MATCH if same else DIVERGE, ["duty_extension_cap"], key,
        observed, expected, "" if same else
        "пакет, декларация и фактические адресные условия продления не совпали", group)]


def builder_package_identity(pkg: dict) -> str:
    """Bind component evidence to the compared model/manifest, not a ZIP path.

    This content digest is NOT authentication of execution. Builder readings,
    like provider readings, must be captured by the caller from the real reader.
    """
    if not isinstance(pkg, dict) or any(not isinstance(pkg.get(k), dict)
                                        for k in ("model", "manifest")):
        raise ThresholdGateError("pairing-builder: model/manifest identity missing")
    try:
        raw = json.dumps({k: pkg[k] for k in ("model", "manifest")},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ThresholdGateError("pairing-builder: invalid package identity") from exc
    return hashlib.sha256(raw).hexdigest()


def _builder_values(pkg: dict, evidence: dict | None, *, applied, readings,
                    mapping, qualified) -> dict:
    """Validate the separate, explicitly supplied Builder observation channel.

    Only the existing calendar-day executor is supported here. Contract bytes
    are the bytes actually supplied to the Builder, not a reconstruction from
    applied. Re-reading them checks the observation's binding/shape; it does
    not by itself prove that a particular run consumed them.
    """
    if evidence is None:
        return {}
    fields = {"schema", "package_identity", "contract_sha256", "contract_bytes",
              "source_keys_read", "readings"}
    if not isinstance(evidence, dict) or set(evidence) != fields:
        raise ThresholdGateError("pairing-builder: invalid evidence envelope")
    if evidence["schema"] != "pairing-builder-thresholds/1":
        raise ThresholdGateError("pairing-builder: unknown evidence schema")
    if evidence["package_identity"] != builder_package_identity(pkg):
        raise ThresholdGateError("pairing-builder: package identity mismatch")
    raw = evidence["contract_bytes"]
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != evidence["contract_sha256"]:
        raise ThresholdGateError("pairing-builder: contract identity mismatch")
    import _cos_rules
    try:
        contract = _cos_rules.load_bytes(raw, where="threshold-gate Builder contract", strict=True)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ThresholdGateError("pairing-builder: unreadable contract") from exc
    keys = evidence["source_keys_read"]
    if (not isinstance(keys, list) or any(type(k) is not str for k in keys)
            or len(set(keys)) != len(keys) or set(keys) != set(contract.source_keys_read)
            or not {"max_pairing_days", "base_tz_offset_min"} <= set(keys)):
        raise ThresholdGateError("pairing-builder: source_keys_read do not confirm the contract")
    observations = evidence["readings"]
    if not isinstance(observations, dict) or set(observations) != {"max_pairing_days"}:
        raise ThresholdGateError("pairing-builder: unsupported or missing reading")
    cell = observations["max_pairing_days"]
    if not isinstance(cell, dict) or set(cell) != {
            "value", "unit", "relation", "semantics", "base_tz_offset_min"}:
        raise ThresholdGateError("pairing-builder: invalid calendar-day reading")
    if (type(cell["value"]) is not int or cell["value"] <= 0
            or type(contract.max_pairing_days) is not int
            or cell["value"] != contract.max_pairing_days
            or type(cell["base_tz_offset_min"]) is not int
            or type(contract.base_tz_offset_min) is not int
            or cell["base_tz_offset_min"] != contract.base_tz_offset_min
            or cell["unit"] != "count" or cell["relation"] != "max"
            or cell["semantics"] != "inclusive-local-calendar-days"):
        raise ThresholdGateError("pairing-builder: reading/contract calendar semantics mismatch")
    # The days limit alone cannot identify its calendar. An internally valid
    # contract with a different offset changes which dates the predicate sees.
    # Bind the observed scale to explicit canonical package parameters AND the
    # claimed/measured provider channel; an absent declaration is not offset 0.
    offset = cell["base_tz_offset_min"]
    params = [p for p in collect_params(pkg, kinds=("HARD", "DEF"))
              if p.get("name") == "base_tz_offset_min"]
    if not params or any(
            not _finite_number(p.get("value")) or p["value"] != offset
            or p.get("unit") != "duration_minutes" or p.get("relation") != "eq"
            or p.get("condition") is not None or _norm_qualifier(p.get("qualifier"))
            or _targets(p, mapping, qualified) != ["base_tz_offset_min"]
            for p in params):
        raise ThresholdGateError("pairing-builder: calendar package binding missing or inconsistent")
    claims = [a for a in (applied or []) if a.get("threshold") == "base_tz_offset_min"]
    covered_rules = {r for a in claims for r in (a.get("rules") or [])}
    if (not claims or not {p["rule"] for p in params} <= covered_rules
            or any(a.get("executor", "kotlin-provider") != "kotlin-provider"
                   or "base_tz_offset_min" not in (a.get("fromParams") or [])
                   or not _finite_number(a.get("value")) or a["value"] != offset
                   for a in claims)
            or not _finite_number(readings.get("base_tz_offset_min"))
            or readings["base_tz_offset_min"] != offset):
        raise ThresholdGateError("pairing-builder: calendar applied/provider binding missing or inconsistent")
    return {"max_pairing_days": cell["value"]}


def compare(pkg: dict, readings: dict, *, profile: str | None = None,
            mapping: dict | None = None,
            ignored: list | None = None,
            applied: list | None = None,
            qualified: dict | None = None,
            off_literals: dict | None = None,
            code_readings: dict | None = None,
            policy_readings: dict | None = None,
            builder_readings: dict | None = None) -> list[dict]:
    """Сверить пороги пакета с показаниями профиля, которым идёт счёт.

    `readings` — lastRun.profileReadings отчёта применённого: то, что
    провайдер ПРОЧИТАЛ при создании, а не то, что резолвер собирался
    применить. `mapping` — thresholds.paramToThreshold из того же отчёта.
    `ignored` — параметры, которые решатель НЕ применил, с его причинами:
    они несравнимы по определению и называются его словами, не нашими.
    `off_literals` — thresholds.offLiterals: показание, равное
    выключателю, означает «правило не считается», а не «предельно
    строго» — арифметика направления на нём лгала бы («СТРОЖЕ на 6»).

    `builder_readings` — отдельное явное свидетельство чтения внешнего
    построителя с исходными contract_bytes. Хеши связывают содержимое,
    но не удостоверяют исполнение. Без него внешний applied остаётся
    несравнимым; общий caller обязан удержать unjudged_claimed_params.

    Пустые показания или пустое соответствие — отказ: подставлять своё
    здесь запрещено.
    """
    # Old reports without executor retain the original provider-only contract.
    # An external claim is never satisfied by inserting it into JVM readings.
    external = set()
    executors = {}
    for a in (applied or []):
        executor = a.get("executor", "kotlin-provider")
        if executor not in ("kotlin-provider", "pairing-builder"):
            raise ThresholdGateError("unknown threshold executor: %r" % executor)
        threshold = a.get("threshold")
        if threshold in executors and executors[threshold] != executor:
            raise ThresholdGateError("conflicting threshold executors: %r" % threshold)
        executors[threshold] = executor
        if executor == "pairing-builder":
            if a.get("threshold") != "max_pairing_days":
                raise ThresholdGateError("pairing-builder: unsupported threshold")
            external.add(a["threshold"])
    if not isinstance(readings, dict) or (not readings and not external):
        raise ThresholdGateError(
            "показания решателя пусты: сравнивать пороги пакета не с чем. "
            "Ожидается lastRun.profileReadings отчёта применённого")
    if not isinstance(mapping, dict) or not mapping:
        raise ThresholdGateError(
            "решатель не сообщил соответствие параметр -> показание "
            "(thresholds.paramToThreshold): сравнивать по своей копии нельзя")
    if external.intersection(readings):
        raise ThresholdGateError("pairing-builder: external threshold in provider readings")
    builder_values = _builder_values(pkg, builder_readings, applied=applied,
                                     readings=readings, mapping=mapping, qualified=qualified)
    if set(builder_values) - external:
        raise ThresholdGateError("pairing-builder: observation has no declared external executor")
    observed_values = dict(readings)
    observed_values.update(builder_values)
    params = collect_params(pkg)
    if code_readings is not None and not isinstance(code_readings, dict):
        raise ThresholdGateError("profileCodeReadings: ожидается объект показаний")
    if policy_readings is not None and not isinstance(policy_readings, dict):
        raise ThresholdGateError("profilePolicyReadings: ожидается объект показаний")
    verdicts: list[dict] = []
    applied_by_thr = {str(a.get("threshold")): a for a in (applied or [])}
    code_names = {p["name"] for p in params if p.get("unit") in {"code", "code_list"}}

    # ГЛАВНАЯ СВЕРКА «прислали = применили»: что решатель ЗАЯВИЛ
    # применённым против того, что созданный провайдер ПРОЧИТАЛ. Она не
    # зависит от группировки параметров и ловит ровно тот класс, ради
    # которого гейт написан: пороги доехали до отчёта, но не до счёта.
    # Ревизия 1 этого не умела — показания собирались из того же пакета.
    for a in (applied or []):
        thr = str(a.get("threshold"))
        claimed = a.get("value")
        actual = observed_values.get(thr)
        if not _finite_number(claimed) or (actual is not None
                                          and not _finite_number(actual)):
            raise ThresholdGateError(
                f"{thr}: заявленное значение и показание должны быть конечными "
                "числами; строка, логическое значение или неверная форма "
                "не подтверждают чтение порога")
        # Never truncate either side: distinct fractions must not pass this
        # independent claim-versus-provider check as equal integer parts.
        if thr in external and actual is None:
            verdicts.append(_v(INCOMPARABLE, list(a.get("fromParams") or [thr]),
                thr, None, [claimed],
                "порог передан pairing-builder; фактическое чтение построителя не подтверждено",
                [{"rule": r} for r in (a.get("rules") or [])]))
        elif actual is None or actual != claimed:
            verdicts.append(_v(
                DIVERGE, list(a.get("fromParams") or [thr]), thr, actual,
                [claimed],
                ("заявленное значение %s, показание pairing-builder %s" % (claimed, actual)
                 if thr in external else
                 "решатель ЗАЯВИЛ применённым %s, а созданный провайдер "
                 "ПРОЧИТАЛ %s: пороги дошли до отчёта, но не до счёта" % (claimed, actual)),
                [{"rule": r} for r in (a.get("rules") or [])]))
        # A matching count is not evidence of the identities that were read.
        # Numeric applied entries also serialize codes:[]; that alone does not
        # turn them into code thresholds requiring an unrelated observation.
        if (a.get("codes") or code_names.intersection(a.get("fromParams") or [])
                or thr in (code_readings or {})):
            declared_codes = _code_labels(a.get("codes"), "code_list",
                                          f"{thr}/applied.codes", measured=True)
            observed = (code_readings or {}).get(thr)
            actual_codes = (None if observed is None else _code_labels(
                observed, "code_list", f"{thr}/profileCodeReadings", measured=True))
            if (actual_codes != declared_codes or actual != len(declared_codes)
                    or claimed != len(declared_codes)):
                verdicts.append(_v(DIVERGE, list(a.get("fromParams") or [thr]),
                    thr, actual_codes, declared_codes,
                    "точные обозначения созданного провайдера не подтверждают "
                    "applied.codes; совпадение количества недостаточно",
                    [{"rule": r} for r in (a.get("rules") or [])]))

    # Что решатель отверг сам — несравнимо его словами, до всякой арифметики.
    ignored_by_name: dict[str, list] = {}
    for i in (ignored or []):
        ignored_by_name.setdefault(str(i.get("param")), []).append(i)
    for name, items in ignored_by_name.items():
        # Each report entry owns its rule/qualifier sets. Unioning the two
        # dimensions separately invents cross-entry pairs and can hide an
        # applied parameter. Keep set semantics within one entry, OR entries.
        scopes = [(set(i.get("rules") or []),
                   {_norm_qualifier(q) for q in (i.get("qualifiers") or [])})
                  for i in items]

        def _hit(p):
            if str(p.get("name")) != name:
                return False
            return any((not rules or p.get("rule") in rules)
                       and (not quals or _norm_qualifier(p.get("qualifier")) in quals)
                       for rules, quals in scopes)
        group = [p for p in params if _hit(p)]
        # Empty sets are wildcards only within their own report entry.
        params = [p for p in params if not _hit(p)]
        reasons = []
        for i in items:                     # без повторов: по правилу на запись
            r = str(i.get("reason"))
            if r not in reasons:
                reasons.append(r)
        verdicts.append(_v(INCOMPARABLE, [name], mapping.get(name), None,
                           sorted({p.get("value") for p in group
                                   if isinstance(p.get("value"), (int, float))}),
                           "решатель не применил: " + "; ".join(reasons),
                           group or [{"rule": r} for i in items
                                     for r in (i.get("rules") or [])]))

    for reading_key, group in _group(params, mapping, qualified).items():
        names = sorted({str(p["name"]) for p in group})
        values = sorted({int(p["value"]) for p in group
                         if isinstance(p.get("value"), int)})
        units = {str(p.get("unit")) for p in group}
        ours = observed_values.get(reading_key)

        if units <= {"code", "code_list"} and code_readings is not None:
            wanted_codes = sorted({code for p in group for code in _code_labels(
                p.get("value"), p.get("unit"), f"{p.get('rule')}/{p['name']}")})
            observed = code_readings.get(reading_key)
            actual_codes = (None if observed is None else _code_labels(
                observed, "code_list", f"{reading_key}/profileCodeReadings", measured=True))
            if not _finite_number(ours):
                if ours is not None:
                    raise ThresholdGateError(f"{reading_key}: число обозначений некорректно")
            relations = {p.get("relation") for p in group}
            if not relations <= {None, "eq"}:
                verdicts.append(_v(INCOMPARABLE, names, reading_key, actual_codes,
                    wanted_codes, "отношение кодового параметра не объявляет точный перечень", group))
            else:
                same = actual_codes == wanted_codes and ours == len(wanted_codes)
                verdicts.append(_v(MATCH if same else DIVERGE, names, reading_key,
                    actual_codes, wanted_codes,
                    "" if same else "точные обозначения или их количество не совпали с пакетом", group))
            continue
        if ours is None:
            verdicts.append(_v(INCOMPARABLE, names, reading_key, None, values,
                               ("pairing-builder не сообщает подтверждённое показание %s" % reading_key
                                if reading_key in external else
                                "профиль %s не сообщает показания %s"
                                % (profile or "?", reading_key)), group))
            continue
        if len(units) != 1 or units.pop() not in COMPARABLE_UNITS:
            verdicts.append(_v(INCOMPARABLE, names, reading_key, ours, values,
                               "единицы параметров несопоставимы", group))
            continue
        if not _finite_number(ours):
            raise ThresholdGateError(
                f"{reading_key}: показание должно быть конечным числом")
        off = (off_literals or {}).get(reading_key)
        if off is not None and not _finite_number(off):
            raise ThresholdGateError(
                f"{reading_key}: литерал выключения должен быть конечным числом")
        if off is not None and ours == off:
            # Показание-выключатель — не число посадок «ноль», а «правило
            # не считается»; сравнивать его с порогом пакета нельзя.
            verdicts.append(_v(
                INCOMPARABLE, names, reading_key, ours, values,
                "правило у решателя ВЫКЛЮЧЕНО (литерал %s = выключено): "
                "требование пакета %s не исполняется"
                % (off, ", ".join(str(v) for v in values)), group))
            continue
        if len(values) > 1:
            # Одна ручка решателя против нескольких РАЗНЫХ требований
            # пакета: ни одно число не удовлетворяет обоим, и совпадение
            # с любым из них — не согласие, а промах мимо остальных.
            verdicts.append(_v(
                INCOMPARABLE, names, reading_key, ours, values,
                "у решателя ОДНА величина %s против %d разных требований "
                "пакета (%s): сравнение возможно только после разведения "
                "ручек" % (reading_key, len(values),
                           ", ".join(str(v) for v in values)), group))
            continue
        want = values[0]
        relations = {str(p.get("relation")) for p in group}
        if ours == want:
            # Совпадение — с оговоркой решателя, если он исполнил «ровно»
            # как «не менее» (заявление РАВЕНСТВО): читатель обязан это видеть.
            note = ""
            for d in ((applied_by_thr.get(reading_key) or {}).get("declarations") or []):
                if str(d).startswith("РАВЕНСТВО") and "eq" in relations:
                    note = str(d)
            verdicts.append(_v(MATCH, names, reading_key, ours, values,
                               note, group))
        elif relations == {"eq"}:
            verdicts.append(_v(
                DIVERGE, names, reading_key, ours, values,
                "решатель НЕ РАВЕН требованию пакета «ровно %s»: расхождение %s"
                % (want, abs(ours - want)), group))
        else:
            side = "ШИРЕ" if _is_max(group) == (ours > want) else "СТРОЖЕ"
            verdicts.append(_v(
                DIVERGE, names, reading_key, ours, values,
                "решатель %s требования пакета на %s"
                % (side, abs(ours - want)), group))

    # Параметры без встречной величины собираются ГРУППОЙ по имени: у
    # нормы резерва их три (по базе на каждую), и показать одну значило
    # бы скрыть, что баз несколько.
    rest: dict[str, list[dict]] = {}
    for p in params:
        name = str(p.get("name"))
        # A mapped record does not cover every other scope of the same name.
        # Explicitly ignored records were removed above; all remaining records
        # without a target must receive their own incomparable verdict.
        if not _targets(p, mapping, qualified):
            rest.setdefault(name, []).append(p)
    # Конфликт видов: одно имя, разные числа, разные виды правил. Решатель
    # применил число ЖЁСТКОГО правила, а сертификатор проверит и другое —
    # ростер получит нарушения при зелёном сравнении. Молчать нельзя.
    for c in kind_conflicts(pkg):
        by_kind = "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in c["by_kind"].items())
        verdicts.append(_v(
            INCOMPARABLE, [c["name"]], mapping.get(c["name"]), None, c["values"],
            "КОНФЛИКТ ВИДОВ: пакет требует разных чисел одного имени в правилах "
            "разных видов (%s) — решатель применяет только жёсткие, независимый "
            "сертификатор проверяет и остальные: ростер получит нарушения при "
            "зелёном сравнении. Какое число действует, решает постановка"
            % by_kind,
            [{"rule": r, "qualifier": c["qualifier"] or None} for r in c["rules"]]))

    for name, group in rest.items():
        why = NO_READING.get(name)
        if why is None and name in mapping:
            # Имя известно, цели нет — значит, не нашёл цели уточнитель:
            # сказать «имя не известно» было бы неправдой.
            quals = sorted({str(p["qualifier"]) for p in group if p.get("qualifier")})
            why = ("уточнитель %s у параметра %s не нашёл цели в таблице "
                   "решателя (qualifiedParamToThreshold): сравнивать нельзя"
                   % (", ".join("«%s»" % q for q in quals) or "(пустой)", name))
        verdicts.append(_v(
            INCOMPARABLE, [name], None, None,
            sorted({p.get("value") for p in group
                    if isinstance(p.get("value"), int)}),
            why or ("имя параметра %s не известно таблице соответствий: "
                    "угадывать встречную величину нельзя" % name), group))
    verdicts.extend(_compare_policy_readings(pkg, readings, applied, policy_readings))
    return verdicts


def _is_max(group: list[dict]) -> bool:
    """Отношение группы: True для «не больше», False для «не меньше»."""
    return any(str(p.get("relation")) == "max" for p in group)


def _v(verdict, names, reading, ours, wants, note, group):
    return {
        "verdict": verdict,
        "params": names,
        "reading": reading,
        "solver_value": ours,
        "package_values": wants,
        "note": note,
        "rules": sorted({str(p.get("rule")) for p in group}),
        "qualifiers": sorted({str(p["qualifier"]) for p in group
                              if p.get("qualifier")}),
    }


def report(verdicts: list[dict], excluded: list[dict] | None = None,
           considered: int | None = None) -> str:
    """Человекочитаемый отчёт. Числа — из сравнения, не из пересказа.

    `considered` — сколько записей жёстких параметров было в пакете
    (len(collect_params(pkg))): строка «рассмотрено» обязана сходиться с
    источником, а не со счётом вердиктов — требование без уточнителя
    даёт ДВА вердикта (обе ручки), и счёт по вердиктам завышал бы её.
    """
    lines = []
    for v in sorted(verdicts, key=lambda x: (x["verdict"], x["params"])):
        head = "[%-10s] %-46s" % (v["verdict"], ", ".join(v["params"]))
        if v["verdict"] == DIVERGE:
            lines.append("%s пакет %s, решатель %s — %s"
                         % (head, v["package_values"], v["solver_value"],
                            v["note"]))
        elif v["verdict"] == MATCH:
            lines.append("%s пакет %s = решатель %s"
                         % (head, v["package_values"], v["solver_value"]))
        else:
            lines.append("%s %s" % (head, v["note"]))
        if v["qualifiers"]:
            lines.append("%s уточнители пакета: %s"
                         % (" " * 13, ", ".join(v["qualifiers"])))
    for p in (excluded or []):
        lines.append("[%-10s] %-46s правило вида %s, в сравнение не берётся"
                     % ("вне счёта", str(p.get("name")), p.get("kind")))
    counts = {}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    lines.append("")
    names = {n for v in verdicts for n in v["params"]}
    if considered is None:
        considered = len({(r, n) for v in verdicts
                          for r in v["rules"] for n in v["params"]})
    lines.append("рассмотрено имён: %d, записей параметров: %d; %s"
                 % (len(names), considered,
                    ", ".join("%s: %d" % (k, n) for k, n in sorted(counts.items()))))
    return "\n".join(lines)
