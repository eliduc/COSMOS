# -*- coding: utf-8 -*-
"""Авто-вывод реестра исполнителей из базы шаблонов (P-N-SHD).

Постановка владельца (2026-08-30, PNSHD-POSTANOVKA-CLD раздел 4):
реестр исполнителей строится автоматически при приёме ВМП — для
каждого правила пакета сопоставление с семьями template_registry;
вердикты covered_full / data_or_out_of_scope / пробел ВЫВОДЯТСЯ, а не
издаются рукой. Ручное издание реестра (скрипт от модели конкретного
прогона) уходит: оно устаревало с каждым новым ВМП и противоречило
принципу инвариантности к данным авиакомпании.

Машинные признаки сопоставления — обезличенные, из контракта пакета:
  * kind DATA / OUT -> данные набора либо вне автоматического
    планирования (не исполнители);
  * посадка на шаблон template_fit категории A/B (пакет A/B/C/D) —
    ПЕРВИЧНА для любого вида: семья посадки и есть семья исполнителя
    (одна правда о семье, критика принципов 2026-09-02, П4);
  * без посадки — запас формы до посадок: мягкое правило (SOFT) ->
    поле export_solver.family из закрытого словаря семей (мягкая
    механика без объявленного типа — честный пробел, даже если
    правило несёт числовые параметры); жёсткое/определяющее
    (HARD/DEF) -> имена машинных параметров по словарю
    PARAM_TO_FAMILY;
  * две правды о семье (посадка ≠ запас) — НЕ тихий выбор, а
    вердикт-пробел family_conflict: расхождение предъявляется
    поимённо, лечится в своде.

Вывод детерминирован и fail-closed: неизвестная семья, неизвестный
параметр, правило без машинных признаков, конфликт семей —
вердикты-пробелы, которые deep_check (vmp/registry_check) предъявляет
поимённо и останавливает запуск. Приём пакета пробелы не рушат:
пробел — состояние постановки.

Форма результата — та же, что у ручного реестра (load_registry её
проверяет): created / source / scope / code_fingerprints / rules.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from vmp import registry_check
# Импорт модуля, не имён: константы обёртки — снимок экземпляра каталога
# (шаг 2), from-импорт уровня модуля заморозил бы первый снимок.
from vmp import template_registry as _tr

#: Вердикты-пробелы вывода (вне FINAL_VERDICTS реестра — deep_check
#: останавливает запуск с note из gap_note).
GAP_FAMILY_UNKNOWN = "family_unknown"
GAP_PARAM_UNKNOWN = "param_unknown"
GAP_NO_MECHANIC = "no_mechanic"
GAP_FAMILY_CONFLICT = "family_conflict"

_KIND_DATA_OUT = {
    "DATA": "данные набора, не исполнитель",
    "OUT": "вне автоматического планирования",
}


def _family_evidence(fam: str, rmt: dict | None = None) -> str:
    spec = (rmt if rmt is not None else _tr.RULE_MECHANIC_TYPES)[fam]
    return "семья %s (%s); исполнители: %s" % (
        fam, spec.get("title") or "", "; ".join(spec.get("executors")
                                                or ()))


def _param_names(rule: dict) -> list[str]:
    out = []
    for p in rule.get("params") or ():
        name = p.get("name") if isinstance(p, dict) else None
        if name and name not in out:
            out.append(str(name))
    return out


def fit_family(rule: dict) -> str | None:
    """Семья посадки на шаблон (template_fit категории A/B); без
    посадки либо при посадке C/D — None (запас формы решает)."""
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        return str(fit.get("family") or "") or None
    return None


def _family_unknown(rec: dict, fam: str) -> dict:
    rec["verdict"] = GAP_FAMILY_UNKNOWN
    rec["gap_note"] = (
        "семья механики %r не значится в базе шаблонов — "
        "требуется новый универсальный шаблон (заявка "
        "конвейера, отмашка человека)" % fam)
    return rec


def _family_conflict(rec: dict, fit_fam: str, other: str,
                     carrier: str) -> dict:
    rec["verdict"] = GAP_FAMILY_CONFLICT
    rec["gap_note"] = (
        "две правды о семье: посадка на шаблон даёт %r, %s — %r; "
        "расхождение лечится в своде, тихий выбор запрещён"
        % (fit_fam, carrier, other))
    return rec


def derive_rule(rule: dict) -> dict:
    """Вердикт одного правила от базы шаблонов (без данных а/к)."""
    rec = {"rule_id": str(rule.get("id")),
           "title": (rule.get("title") or "").strip()}
    kind = rule.get("kind")
    if kind in _KIND_DATA_OUT:
        rec["verdict"] = "data_or_out_of_scope"
        rec["evidence"] = _KIND_DATA_OUT[kind]
        return rec
    if registry_check.is_excluded_until_data(rule):
        # Пометка строки «исключено до получения данных по решению
        # авиакомпании» (структура ВМП 2026-09-02): исполнителя не
        # требует, пробелом не считается; вердикт финальный, след —
        # вопрос анкеты, чей ответ исключил правило.
        ex = rule["excluded_until_data"]
        rec["verdict"] = "excluded_until_data"
        rec["evidence"] = ("исключено до получения данных по решению "
                           "авиакомпании (вопрос %s, ревизия %s)"
                           % (ex.get("question"), ex.get("revision")))
        return rec
    fit_fam = fit_family(rule)
    # Одна операция — один экземпляр каталога: обе проекции снимаются
    # один раз на правило.
    rmt = _tr.RULE_MECHANIC_TYPES
    p2f = _tr.PARAM_TO_FAMILY
    if kind == "SOFT":
        legacy = (rule.get("export_solver") or {}).get("family")
        if fit_fam and legacy and str(legacy) != fit_fam:
            return _family_conflict(rec, fit_fam, str(legacy),
                                    "поле export_solver.family")
        fam = fit_fam or legacy
        if not fam:
            rec["verdict"] = GAP_NO_MECHANIC
            rec["gap_note"] = (
                "мягкое правило без машинного типа механики "
                "(посадки на шаблон нет, export_solver.family пуст) — "
                "шаблона-исполнителя нет, требуется заявка конвейера "
                "шаблонов")
            return rec
        if fam not in rmt or not rmt[fam].get("executors"):
            return _family_unknown(rec, fam)
        rec["verdict"] = "covered_full"
        rec["evidence"] = _family_evidence(fam, rmt) + (
            " [посадка на шаблон]" if fit_fam else "")
        return rec
    names = _param_names(rule)
    if fit_fam:
        # Посадка A/B первична и для HARD/DEF; имена параметров —
        # запас: словарные параметры чужой семьи = две правды.
        if fit_fam not in rmt or not rmt[fit_fam].get("executors"):
            return _family_unknown(rec, fit_fam)
        by_params = []
        for n in names:
            f = p2f.get(n)
            if f and f not in by_params:
                by_params.append(f)
        if by_params and fit_fam not in by_params:
            return _family_conflict(rec, fit_fam, "+".join(by_params),
                                    "словарь параметров правила")
        rec["verdict"] = "covered_full"
        rec["evidence"] = _family_evidence(fit_fam, rmt) + " [посадка на шаблон]"
        return rec
    if not names:
        # Определение мира без машинных параметров (лестница H0,
        # отмашка владельца 2026-09-01): DEF не несёт нормы расчёта
        # и исполнителя не требует — по контракту посадки A/B/C/D
        # определения вне области посадки, а инвариант владельца
        # «нет исполнителя = пробел расчётного слоя» к определениям
        # не относится. DEF С параметрами судится как прежде
        # (fail-closed: параметрное определение — норма).
        if kind == "DEF":
            rec["verdict"] = "data_or_out_of_scope"
            rec["evidence"] = ("определение мира без машинных "
                               "параметров; нормы расчёта не несёт")
            return rec
        rec["verdict"] = GAP_NO_MECHANIC
        rec["gap_note"] = (
            "правило вида %s без машинных параметров — сопоставить "
            "с семьёй базы шаблонов нечем" % (kind or "<нет>"))
        return rec
    unknown = [n for n in names if n not in p2f]
    if unknown:
        rec["verdict"] = GAP_PARAM_UNKNOWN
        rec["gap_note"] = (
            "параметры %s вне закрытого словаря семей — механики-"
            "исполнителя нет, требуется заявка конвейера шаблонов"
            % ", ".join(sorted(unknown)))
        return rec
    fams = []
    for n in names:
        f = p2f[n]
        if f not in fams:
            fams.append(f)
    rec["verdict"] = "covered_full"
    rec["evidence"] = "; ".join(_family_evidence(f, rmt) for f in fams)
    return rec


def derive_registry(model: dict, repo_root: Path,
                    scope: str = "", *,
                    manifest_check: str | None = None,
                    manifest_check_against: dict | None = None) -> dict:
    """Реестр исполнителей, выведенный из модели пакета и базы
    шаблонов. Отпечатки кода — той же процедурой, что у сторожа
    дрейфа (registry_check.code_fingerprints); блок catalog (версия,
    отпечаток, путь) — «реестр выведен под этим каталогом шаблонов»,
    сторож дрейфа сверяет отпечаток (шаг 2 каталога).

    manifest_check (шаг 3) — исход сверки каталога продукта с ключом
    пакета manifest.catalog у ЭТОГО нажатия: match | не объявлена |
    другой отпечаток | стоп (перечень — vmp.catalog_access.CHECK_*).
    Реестр, выведенный не при нажатии (приём пакета, канал задач),
    пакета не сверял — поле None, и это видно.

    manifest_check_against — ВТОРАЯ СТОРОНА той сверки: версия,
    отпечаток и источник — с шага 7 (часть 7-Б, 08.09.2026) всегда
    `catalog`, режим СРЕЗА базы шаблонов снят, второго источника у
    продукта не осталось, и пути мерка больше не несёт. Поле оставлено:
    без него запись говорила бы «сошлось» под отметкой, по которой
    сверки не было, а записи прежних прогонов несут в нём `slice` с
    путём среза и читаются как прежде.

    Блок bindings (шаг 4 каталога, 08.09.2026) — сводка привязок
    продукта, под которыми выведен реестр: файл, версия продукта,
    коммит и момент сборки, версия и отпечаток каталога привязок,
    ОТПЕЧАТОК КАНОНА файла (`vmp.bindings_build.fingerprint` — без
    изменчивых полей), состояние, счётчики клеток и строк, итог стенда
    (числами ЗАДАЧ и СТРОК подтверждения — это разные числа) и слово о
    привязках CDPM («объявлены | не объявлены | не читаются», причина
    третьего — в `cdpm_note`). Отпечаток канона — то, что сверяет
    сторож дрейфа (registry_check.drift_check, псевдофайл «bindings»):
    перегенерация файла с новым `generated_at` реестра не старит, а
    смена строк исполнителей — старит."""
    from vmp import catalog_access as _ca
    return {
        "created": "%s (авто-вывод из базы шаблонов)"
                   % datetime.now().isoformat(timespec="seconds"),
        "source": "vmp/registry_derive.py: вывод от модели пакета и "
                  "базы шаблонов (P-N-SHD, ручное издание реестра "
                  "уходит)",
        "scope": scope,
        "derived": True,
        "code_fingerprints": registry_check.code_fingerprints(
            Path(repo_root)),
        "catalog": {**_ca.stamp(), "manifest_check": manifest_check,
                    "manifest_check_against": manifest_check_against},
        "bindings": _ca.bindings_summary(),
        "rules": [derive_rule(r) for r in model.get("rules") or ()
                  if isinstance(r, dict)],
    }


def derive_and_write(model: dict, repo_root: Path, path: Path,
                     scope: str = "", *,
                     manifest_check: str | None = None,
                     manifest_check_against: dict | None = None) -> dict:
    """Вывести реестр и записать атомарно (tmp -> os.replace).
    Возврат — сам реестр (для верификации без повторного чтения)."""
    reg = derive_registry(model, Path(repo_root), scope,
                          manifest_check=manifest_check,
                          manifest_check_against=manifest_check_against)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%s.tmp" % (path.name,
                                        uuid.uuid4().hex[:12]))
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)
    return reg
