# -*- coding: utf-8 -*-
"""Единая проверка соответствия ВМП и шаблонов COSMOS.

Поручение владельца (2026-08-28): при каждом новом ВМП, ДО начала
расчёта пейрингов, — стопроцентное функциональное соответствие:
каждое правило и каждый критерий оптимизации закрыт шаблоном COSMOS
полностью либо обоснованно вне контура. Архитектура — золотой
стандарт эшелонированной защиты:

  * ОДИН источник истины — реестр исполнителей (vmp/executors-
    registry.json): правило -> вердикт -> компонент -> доказательство;
  * ГЛУБОКАЯ СВЕРКА на приёмке ВМП (deep_check): каждое правило и
    критерий пакета против реестра; любое несоответствие — поимённое
    извещение и стоп до отмашки владельца;
  * СТОРОЖ ДРЕЙФА на каждом запуске (drift_check): реестр несёт
    отпечатки файлов-исполнителей, каталога шаблонов и привязок
    исполнителей на момент сверки; изменение кода после сверки — стоп
    «реестр устарел, требуется пересверка» (живой пример нужности:
    2026-08-28 судья менялся четыре раза между двумя нажатиями кнопки).

Оба рубежа fail-closed: сомнение = остановка, не пропуск.
"""
import hashlib
import io
import json
from pathlib import Path

FINAL_VERDICTS = ("covered_full", "data_or_out_of_scope",
                  "excluded_until_data")
REGISTRY_PATH = Path(__file__).with_name("executors-registry.json")

# Файлы-исполнители, чей дрейф делает реестр устаревшим.
EXECUTOR_FILES = (
    "_pairing_builder.py", "_conditions.py", "_cos_rules.py",
    "vmp/criteria.py", "vmp/pairing_input.py",
    "vmp/conditions_export.py",
)

#: Заголовки строк дрейфа: два псевдофайла сторожа (каталог шаблонов и
#: привязки исполнителей) называются своими словами, всё прочее —
#: файл-исполнитель.
DRIFT_TITLES = {
    "catalog": "каталог шаблонов изменён",
    "bindings": "привязки исполнителей изменены",
}


class ConformanceError(RuntimeError):
    """Несоответствие ВМП и шаблонов; перечень — поимённо в .gaps."""

    def __init__(self, message: str, gaps: list):
        self.gaps = gaps
        super().__init__(message)


def load_registry(path: Path | None = None) -> dict:
    """Реестр с проверкой формы (fail-closed): реестр — источник
    истины, и бесформенный источник истины хуже отсутствующего."""
    p = path or REGISTRY_PATH
    if not Path(p).exists():
        raise ConformanceError(
            "реестра исполнителей нет (%s) — сверка ВМП не "
            "проводилась, расчёт не начинается" % p,
            [{"kind": "no_registry", "rule_id": str(p),
              "title": "реестр исполнителей отсутствует",
              "note": "выпустить реестр рубежом 1 (глубокая сверка)"}])
    with io.open(p, encoding="utf-8") as f:
        reg = json.load(f)
    rules = reg.get("rules") if isinstance(reg, dict) else None
    fps = reg.get("code_fingerprints") if isinstance(reg, dict) else None
    bad = []
    if not isinstance(rules, list) or not rules:
        bad.append("rules: пустой либо не список")
    else:
        seen = set()
        for i, r in enumerate(rules):
            rid = r.get("rule_id") if isinstance(r, dict) else None
            if not rid or not isinstance(r.get("verdict"), str):
                bad.append("rules[%d]: нет rule_id либо verdict" % i)
                break
            if rid in seen:
                bad.append("rules: rule_id %r дважды — источник "
                           "истины двоится" % rid)
                break
            seen.add(rid)
    if not isinstance(fps, dict) or not fps:
        bad.append("code_fingerprints: нет отпечатков кода — сторож "
                   "дрейфа слеп")
    if bad:
        raise ConformanceError(
            "реестр исполнителей не по форме: " + "; ".join(bad),
            [{"kind": "registry_malformed", "rule_id": str(p),
              "title": "реестр не по форме", "note": b}
             for b in bad])
    return reg


def code_fingerprints(repo_root: Path) -> dict:
    out = {}
    for rel in EXECUTOR_FILES:
        p = repo_root / rel
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() \
            if p.exists() else None
    return out


def drift_check(registry: dict, repo_root: Path) -> list:
    """Сторож дрейфа: отпечатки кода против отпечатков реестра; при блоке
    catalog реестра (шаг 2 каталога шаблонов: реестр выведен под этим
    каталогом) — и отпечаток каталога против текущего (псевдофайл
    "catalog" в перечне); при блоке bindings (шаг 4: реестр выведен под
    этими привязками исполнителей) — и отпечаток КАНОНА файла привязок
    против текущего (псевдофайл "bindings").

    Файл привязок в EXECUTOR_FILES не заводится намеренно: его сырой
    отпечаток менялся бы при каждой перегенерации (поля generated_at и
    generated_from изменчивы по замыслу), и реестры площадок старели бы
    без всякого изменения исполнителей. Сверяется отпечаток канона
    (vmp.bindings_build.fingerprint — документ без изменчивых полей и
    без моментов записей стенда), тот же, что кладёт в блок сводка
    двери.

    Реестр без блока catalog (до шага 2) или без блока bindings (до
    шага 4, синтетические реестры тестов) по этим псевдофайлам не
    судится: он и так устаревает по отпечатку каталога и уходит на
    пересверку. Возврат — отсортированный перечень изменившегося после
    сверки."""
    want = registry.get("code_fingerprints") or {}
    have = code_fingerprints(repo_root)
    drifted = [rel for rel in EXECUTOR_FILES
               if want.get(rel) != have.get(rel)]
    want_cat = registry.get("catalog")
    if isinstance(want_cat, dict):
        from vmp import catalog_access as _ca
        if want_cat.get("fingerprint") != _ca.stamp()["fingerprint"]:
            drifted.append("catalog")
    want_bind = registry.get("bindings")
    if isinstance(want_bind, dict) and want_bind.get("fingerprint"):
        from vmp import catalog_access as _ca
        if want_bind["fingerprint"] != _ca.bindings_summary().get(
                "fingerprint"):
            drifted.append("bindings")
    return sorted(drifted)


#: Категории посадки правила на шаблон (контракт пакета
#: «Посадка A/B/C/D», решение владельца 2026-09-01): A — точная
#: посадка (параметры с дословными цитатами клауз), B — посадка с
#: одним явным допущением (вопрос-подтверждение а/к обязателен),
#: C — не ложится (нужен новый шаблон), D — конфликт семей (арбитраж).
FIT_CATEGORIES = ("A", "B", "C", "D")


def fit_gap(rule: dict) -> dict | None:
    """Пробел покрытия по ПОСАДКЕ правила на шаблон (template_fit).

    Возврат: None — посадка валидна, правило покрыто семьёй-исполнителем
    (категории A и B; открытость B-вопроса держит запуск существующим
    механизмом открытых вопросов анкеты, не этим рубежом); словарь-гап —
    поимённое несоответствие. Кривая посадка хуже отсутствующей —
    fail-closed гап, а не тихий пропуск легаси-путём."""
    from vmp.template_registry import RULE_MECHANIC_TYPES, PARAM_TO_FAMILY
    fit = rule.get("template_fit")
    rid = str(rule.get("id"))
    title = rule.get("title") or ""
    cat = str((fit or {}).get("category") or "")
    if cat not in FIT_CATEGORIES:
        return {"kind": "fit_invalid", "rule_id": rid, "title": title,
                "note": "категория посадки %r вне перечня %s"
                        % (cat, "/".join(FIT_CATEGORIES))}
    # Modern cells include the family's declared CONDITIONS as well as
    # numerical parameters. PARAM_TO_FAMILY is the legacy numeric
    # projection, not the landing/2 vocabulary. Judge modern cells against
    # their pinned family using the shared read-only law; do not flatten
    # conditions or infer permission to execute from a valid landing.
    modern = fit.get("schema_version", "landing/1") != "landing/1"
    if modern:
        from template_catalog import landing
        from vmp import catalog_access
        problems = landing.problems(rule, catalog_access.current())
        if problems:
            return {"kind": "fit_invalid", "rule_id": rid, "title": title,
                    "note": "; ".join(p["words"] for p in problems)}
    if cat == "C":
        return {"kind": "rule_no_template", "rule_id": rid,
                "title": title,
                "note": (fit.get("gap")
                         or "правило не ложится в базу шаблонов — "
                            "нужен новый шаблон")}
    if cat == "D":
        cands = [str(c) for c in (fit.get("candidates") or []) if c]
        if len(cands) < 2:
            return {"kind": "fit_invalid", "rule_id": rid,
                    "title": title,
                    "note": "категория D требует не менее двух "
                            "семей-кандидатов"}
        return {"kind": "rule_fit_conflict", "rule_id": rid,
                "title": title,
                "note": "конфликт семей шаблонов: %s — требуется "
                        "арбитраж" % ", ".join(cands)}
    # A и B: семья и параметры — по закрытым словарям базы.
    fam = fit.get("family")
    if fam not in RULE_MECHANIC_TYPES:
        return {"kind": "fit_invalid", "rule_id": rid, "title": title,
                "note": "семья посадки %r вне базы шаблонов" % (fam,)}
    params = fit.get("params")
    from vmp.template_registry import PARAM_TO_FAMILY as _p2f
    family_has_params = any(f == fam for f in _p2f.values())
    if not isinstance(params, dict) or not params:
        # Семья БЕЗ словарных параметров (мягкие механики, судящиеся
        # полем family: layover_penalty, calendar_windows и родня) —
        # посадка без params законна, но обязана нести дословную
        # цитату-носитель (интеграционная находка ВМП нано 2026-09-01).
        if family_has_params:
            return {"kind": "fit_invalid", "rule_id": rid,
                    "title": title,
                    "note": "посадка категории %s без машинных "
                            "параметров, а семья %r их требует"
                            % (cat, fam)}
        if not str(fit.get("quote") or "").strip():
            return {"kind": "fit_invalid", "rule_id": rid,
                    "title": title,
                    "note": "посадка беспараметрной семьи %r без "
                            "дословной цитаты-носителя" % (fam,)}
        if cat == "B" and not str(fit.get("assumption") or "").strip():
            return {"kind": "fit_invalid", "rule_id": rid,
                    "title": title,
                    "note": "категория B требует ровно одного явно "
                            "названного допущения"}
        return None
    for name, cell in params.items():
        want_fam = PARAM_TO_FAMILY.get(str(name))
        if not modern and want_fam is None:
            return {"kind": "fit_invalid", "rule_id": rid,
                    "title": title,
                    "note": "параметр %r вне закрытого словаря"
                            % (name,)}
        if not modern and want_fam != fam:
            return {"kind": "fit_invalid", "rule_id": rid,
                    "title": title,
                    "note": "параметр %r принадлежит семье %r, а не "
                            "%r" % (name, want_fam, fam)}
        if not isinstance(cell, dict) \
                or not str(cell.get("quote") or "").strip():
            return {"kind": "fit_invalid", "rule_id": rid,
                    "title": title,
                    "note": "параметр %r без дословной цитаты "
                            "клаузы-источника" % (name,)}
    if cat == "B" and not str(fit.get("assumption") or "").strip():
        return {"kind": "fit_invalid", "rule_id": rid, "title": title,
                "note": "категория B требует ровно одного явно "
                        "названного допущения"}
    return None


def is_excluded_until_data(rule: dict) -> bool:
    """Пометка строки «исключено до получения данных по решению
    авиакомпании» (единственная допустимая пометка строки формы ВМП):
    словарь excluded_until_data с вопросом анкеты, чей ответ «а»
    исключил правило; снимается кодом при поступлении данных."""
    return isinstance(rule, dict) \
        and isinstance(rule.get("excluded_until_data"), dict) \
        and bool(rule["excluded_until_data"].get("question"))


def excluded_until_data_ids(model_or_rules) -> list[str]:
    """Идентификаторы правил модели (или перечня правил) с пометкой
    «исключено до получения данных» — для приёмника, матрицы
    готовности и сертификатов."""
    rules = model_or_rules.get("rules") if isinstance(model_or_rules, dict) \
        else model_or_rules
    return [str(r.get("id")) for r in (rules or [])
            if is_excluded_until_data(r)]


def _registry_gap(rec: dict) -> dict:
    """Пробел по вердикту реестра исполнителей — словами реестра."""
    return {"kind": "rule_" + str(rec.get("verdict")),
            "rule_id": rec["rule_id"],
            "title": rec.get("title") or "",
            "note": rec.get("gap_note") or rec.get("evidence")
            or "исполнитель неполон"}


def deep_check(model_rules: list, criterion: dict | None,
               registry: dict, zones=("PAIRING", "BOTH")) -> list:
    """Глубокая сверка приёмки: каждое правило зон расчёта и критерий
    против реестра. Возврат — перечень пробелов (пусто = 100%).

    Правило с template_fit судится ПОСАДКОЙ (первичный источник
    покрытия, пакет A/B/C/D); чистая посадка не отменяет вердикт
    реестра исполнителей, если запись о правиле в нём есть (реестр
    знает исполнителей и «две правды о семье» — family_conflict,
    критика принципов 2026-09-02); отсутствие записи при посадке —
    не пробел (ручные реестры до пакета посадок правил с посадкой не
    вели). Без посадки — прежний путь по реестру (обратная
    совместимость сводов до пакета)."""
    by_id = {r["rule_id"]: r for r in registry.get("rules") or []}
    gaps = []
    for rule in model_rules:
        if rule.get("zone") not in zones:
            continue
        if is_excluded_until_data(rule):
            # Решение авиакомпании «считать до получения данных»
            # (структура ВМП, владелец 2026-09-02): правило исключено
            # из исполнения с явной пометкой — к исполнению не
            # требуется и пробелом покрытия не считается; сертификат
            # решения помечает его непроверенным.
            continue
        rec = by_id.get(str(rule.get("id")))
        if rec is not None and rec.get("verdict") == "excluded_until_data":
            # Реестр вывелся, когда правило было исключено, а теперь
            # пометка снята (данные поступили): исполнителя реестр не
            # сверял — пересверка, а не тихое «покрыто» (аудит №1).
            gaps.append({
                "kind": "rule_excluded_stale", "rule_id": rec["rule_id"],
                "title": rule.get("title") or "",
                "note": "реестр помечает правило исключённым до "
                        "получения данных, модель — нет: требуется "
                        "пересверка реестра"})
            continue
        if isinstance(rule.get("template_fit"), dict):
            g = fit_gap(rule)
            if g is not None:
                gaps.append(g)
            elif rec is not None \
                    and rec.get("verdict") not in FINAL_VERDICTS:
                gaps.append(_registry_gap(rec))
            continue
        if rec is None:
            gaps.append({
                "kind": "rule_unknown", "rule_id": str(rule.get("id")),
                "title": rule.get("title") or "",
                "note": "правила нет в реестре исполнителей — новое "
                        "для COSMOS, требуется сверка и отмашка"})
        elif rec.get("verdict") not in FINAL_VERDICTS:
            gaps.append(_registry_gap(rec))
    # Критерий — тем же рубежом, через закрытый словарь видов.
    if criterion is not None:
        from vmp import criteria as _c
        kind = criterion.get("kind")
        if kind not in _c.SPEC:
            gaps.append({"kind": "criterion_unknown",
                         "rule_id": str(kind),
                         "title": "вид критерия вне словаря",
                         "note": "требуется шаблон вида (этап 2)"})
        elif _c.EXECUTORS.get(kind) is None:
            gaps.append({"kind": "criterion_no_executor",
                         "rule_id": str(kind),
                         "title": _c.SPEC[kind].get("title") or "",
                         "note": _c.SPEC[kind].get("needs")
                         or "исполнителя нет"})
    return gaps


def criteria_tiers_gaps(form: dict | None,
                        parts=("pairing",)) -> list:
    """Ярусы критериев формы ВМП (структура владельца 2026-09-02:
    раздел в) — упорядоченный список, ярусы — последовательными
    оптимизациями с допуском). Решатель исполняет ОДИН критерий;
    несколько критериев или ярус > 1 в части — честный стоп приёмника
    (fail-closed) до отдельного пакета решателя, не молчаливый выбор
    первого. Без формы — проверять нечего (пусто)."""
    if not isinstance(form, dict):
        return []
    gaps = []
    for part in parts:
        crits = [c for c in ((form.get(part) or {}).get("criteria") or [])
                 if isinstance(c, dict)]
        tiers = sorted({int(c.get("tier") or 1) for c in crits})
        if len(crits) > 1 or (tiers and tiers[-1] > 1):
            gaps.append({
                "kind": "criteria_tiers_unsupported",
                "rule_id": ", ".join(str(c.get("id")) for c in crits),
                "title": "ярусы критериев части %s" % part,
                "note": "критериев %d, ярусов до %d — решатель "
                        "исполняет один критерий; последовательные "
                        "оптимизации по ярусам не реализованы "
                        "(отдельный пакет решателя), расчёт не "
                        "начинается" % (len(crits), tiers[-1] if tiers
                                        else 1)})
    return gaps


def conformance_gate(model: dict, criterion: dict | None,
                     repo_root: Path,
                     registry_path: Path | None = None,
                     form: dict | None = None,
                     task: str = "pairing") -> dict:
    """Единый гейт (оба рубежа). Возврат — отчёт; несоответствие —
    ConformanceError с поимённым перечнем (fail-closed). form —
    форма ВМП пакета (vmp-form.json), если есть: ярусы критериев ЧАСТИ
    task судятся только при исполняемом критерии (режим оптимизации;
    в режимах выполнимости критерий не исполняется — стоп по ярусам
    был бы по основанию, к расчёту не относящемуся)."""
    from vmp import catalog_access as _ca
    try:
        registry = load_registry(registry_path)
        drifted = drift_check(registry, repo_root)
        if drifted:
            raise ConformanceError(
                "реестр исполнителей устарел: код, каталог шаблонов или "
                "привязки изменены после сверки (%s) — требуется "
                "пересверка реестра" % ", ".join(drifted),
                [{"kind": "drift", "rule_id": rel,
                  "title": DRIFT_TITLES.get(rel, "файл-исполнитель изменён"),
                  "note": ""}
                 for rel in drifted])
        gaps = deep_check(model.get("rules") or [], criterion, registry)
        if criterion is not None:
            gaps += criteria_tiers_gaps(form, parts=(task,))
    except _ca.CATALOG_ERRORS as e:
        # Словари семей и видов — проекции каталога шаблонов (шаг 2):
        # без каталога сверка не выполнима — стоп словами, не трассировка.
        raise ConformanceError(
            "каталог шаблонов недоступен — сверка ВМП не выполнима: %s" % e,
            [{"kind": "catalog_unavailable", "rule_id": "catalog",
              "title": "каталог шаблонов недоступен", "note": str(e)}]) \
            from None
    if gaps:
        raise ConformanceError(
            "ВМП не закрыт шаблонами COSMOS на 100%%: несоответствий "
            "%d — перечень поимённо, расчёт не начинается" % len(gaps),
            gaps)
    excluded = excluded_until_data_ids(model)
    return {"verdict": "OK",
            "rules_checked": sum(1 for r in (model.get("rules") or [])
                                 if r.get("zone") in ("PAIRING", "BOTH")
                                 and not is_excluded_until_data(r)),
            "rules_excluded_until_data": excluded,
            "criterion_checked": criterion is not None,
            "registry_created": registry.get("created")}


def execution_gate(manifest: dict) -> dict:
    """Рубеж исполнения (внешний аудит 04.09.2026, A1): исполнитель
    покрывает правило ТОЛЬКО при переданных ему порогах ВМП. Реестр
    отвечает «исполнитель есть», ведомость исполнения
    (vmp/execution_manifest) — «что ему передано». Жёсткое применимое
    правило без переданного значения или без исполнителя — остановка
    с поимённым перечнем (ConformanceError, kind rule_not_executed);
    возврат — сводка ведомости при чистом рубеже."""
    blocking = list((manifest or {}).get("blocking") or [])
    if blocking:
        raise ConformanceError(
            "правила ВМП без исполнения у построителя: %d — перечень "
            "поимённо, расчёт не начинается" % len(blocking), blocking)
    rows = (manifest or {}).get("rules") or []
    return {"verdict": "OK",
            "rules_executed": sum(1 for r in rows
                                  if r.get("status") == "исполняется"),
            "rules_total": len(rows)}


def main(argv=None) -> int:
    """Ручной/конвейерный запуск гейта: код возврата 0 — соответствие,
    2 — стоп с поимённым перечнем (печать в stdout)."""
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        description="Единая проверка соответствия ВМП и шаблонов "
                    "COSMOS (оба рубежа, fail-closed)")
    ap.add_argument("model", help="model.json пакета постановки")
    ap.add_argument("--criterion-json",
                    help="критерий оптимизации расчёта (файл JSON); "
                         "без него проверяются только правила")
    ap.add_argument("--registry", help="файл реестра исполнителей "
                                       "(умолчание: рядом с модулем)")
    ap.add_argument("--repo-root", default=str(Path(__file__)
                                               .resolve().parent.parent))
    ap.add_argument("--form-json",
                    help="форма ВМП пакета (vmp-form.json): ярусы "
                         "критериев части задачи судятся при заданном "
                         "критерии")
    ap.add_argument("--task", default="pairing",
                    choices=("pairing", "rostering"),
                    help="часть формы, чьи ярусы судятся (умолчание "
                         "pairing)")
    a = ap.parse_args(argv)
    with io.open(a.model, encoding="utf-8") as f:
        model = json.load(f)
    criterion = None
    if a.criterion_json:
        with io.open(a.criterion_json, encoding="utf-8") as f:
            criterion = json.load(f)
    form = None
    if a.form_json:
        with io.open(a.form_json, encoding="utf-8") as f:
            form = json.load(f)
    try:
        rep = conformance_gate(
            model, criterion, Path(a.repo_root),
            Path(a.registry) if a.registry else None,
            form=form, task=a.task)
    except ConformanceError as e:
        print("СТОП: %s" % e)
        for g in e.gaps:
            print("  - [%s] %s: %s" % (g.get("kind"), g.get("rule_id"),
                                       g.get("note") or g.get("title")))
        return 2
    print("OK: правил проверено %d, критерий %s, реестр от %s%s"
          % (rep["rules_checked"],
             "проверен" if rep["criterion_checked"] else "не задан",
             rep.get("registry_created"),
             ("; исключено до получения данных по решению авиакомпании: "
              + ", ".join(rep["rules_excluded_until_data"]))
             if rep.get("rules_excluded_until_data") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
