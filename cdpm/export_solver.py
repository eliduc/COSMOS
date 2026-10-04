# -*- coding: utf-8 -*-
"""Экспорт итоговой модели CDPM в форматы решателей (пакет ЭКС-1,
директива владельца 2026-08-21).

Фаза А — три формата:

- **rave** — скелет правил на языке Rave (Jeppesen/Carmen): модуль с
  правилом на каждую строку свода, полная трассировка к источникам в
  remark. Семантика выражений НЕ выдумывается (принцип «модель
  предлагает — код проверяет» здесь без модели): формальная запись
  строки идёт комментарием, тело — заготовка `%TODO%` для инженера
  Carmen. Скелет проверяем глазами и системой контроля версий.
- **csv** — универсальная таблица правил для интеграторов: столбцы
  идентификатор/зона/тип/формулировка/источники/блокировки.
- **json** — машинная модель CDPM как есть (контракт П-1).

**meridian** объявлен, но не реализован: владелец передаст
документацию формата позже — до тех пор честный отказ, не имитация.

Экспорт разрешён только когда данных достаточно хотя бы для одной
задачи (readiness «б» или «в»): непригодный файл не должен уезжать в
решатель молча.
"""
from __future__ import annotations

import csv
import io
import json
import re

from cdpm import model as m
from vmp import objective_projection as _objective_projection

# name → (расширение, mime, человеческое имя); None = ждёт документацию.
FORMATS: dict[str, tuple[str, str, str] | None] = {
    "rave": (".rave", "text/plain", "Rave (Jeppesen)"),
    "minizinc": (".mzn", "text/plain", "MiniZinc"),
    "csv": (".csv", "text/csv", "CSV (интеграторы)"),
    "json": (".json", "application/json", "JSON (модель CDPM)"),
    "objective": (".json", "application/json",
                  "Критерий оптимизации"),
    "meridian": None,
}

# Compatibility name for callers inspecting the historical marker pattern.
# The scalar decision is shared with the modern writer and both readers.
_WEIGHT_RE = _objective_projection._WEIGHT_RE


def soft_weight(r) -> int | None:
    """Вес мягкой строки: МАШИННОЕ поле главнее распознавания текста
    (третий живой урок вехи А); регэксп — запасной путь для строк,
    взвешенных до появления поля. Единая точка для целевой функции
    и матрицы режимов."""
    return _objective_projection.soft_weight(r)


class ExportNotReady(ValueError):
    """Данных недостаточно для решателя — причина названа."""


class ExportUnavailable(ValueError):
    """Формат объявлен, но его документация ещё не передана."""


def check_exportable(doc: dict) -> dict:
    """Готовность модели к экспорту: «б»/«в» — можно (пейринг или обе
    задачи), «а» — честный отказ с перечислением открытого."""
    ready = m.readiness(doc)
    if ready["state"] == "а":

        def _n(part: str) -> int:
            got = ready.get(part) or {}
            vals = got.values() if isinstance(got, dict) else got
            total = 0
            for v in vals:
                total += v if isinstance(v, int) \
                    else len(v) if hasattr(v, "__len__") else 1
            return total

        raise ExportNotReady(
            "данных недостаточно ни для пейринга, ни для ростеринга — "
            "экспорт в решатель закрыт; открыто по пейрингу: "
            f"{_n('pairing_open')}, по ростерингу: "
            f"{_n('rostering_open')} (блокированные строки и вопросы "
            f"анкеты)")
    return ready


_RAVE_ID_RE = re.compile(r"[^A-Za-z0-9_]")

# Транслитерация идентификаторов: Rave принимает только латиницу.
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


def _rave_ident(text: str) -> str:
    low = (text or "").lower().translate(_TRANSLIT)
    ident = _RAVE_ID_RE.sub("_", low).strip("_")
    ident = re.sub(r"__+", "_", ident) or "rule"
    if ident[0].isdigit():
        ident = "r_" + ident
    return ident


def _rave_str(text: str) -> str:
    return '"' + (text or "").replace("\\", "\\\\") \
        .replace('"', '\\"').replace("\n", " ") + '"'


def export_rave(doc: dict) -> str:
    """Скелет модуля Rave: правило на строку свода, трассировка и
    формальная запись — в remark; тело — заготовка для инженера."""
    check_exportable(doc)
    meta = doc.get("meta", {})
    module = _rave_ident("cdpm_" + str(meta.get("customer") or "rules"))
    out = [
        "/*",
        " * Скелет правил Rave (Jeppesen), автогенерация CDPM "
        f"{meta.get('cdpm_version', '')}.".rstrip(),
        f" * Авиакомпания: {meta.get('customer', '?')}.",
        " * Семантика выражений НЕ выведена автоматически: формальная",
        " * запись каждой строки — в remark; тело правила — заготовка",
        " * %TODO% к доводке инженером Carmen. Трассировка к",
        " * источникам сохранена в каждом правиле.",
        " */",
        "",
        f"module {module};",
        "",
    ]
    for r in doc.get("rules", []):
        kind = r.get("kind")
        if kind in ("OUT",):
            # Вне контура оптимизации — решателю не передаётся.
            continue
        name = _rave_ident(r.get("id", "rule"))
        src = "; ".join(f"{ref.get('sid')}:{ref.get('anchor')}"
                        for ref in r.get("sources", []))
        blockers = r.get("blockers") or []
        out.append(f"/* [{kind}] {r.get('title', '')} */")
        out.append(f"rule {name} =")
        out.append(f"  valid %TODO%; /* формализовать выражение */")
        out.append(f"  remark {_rave_str(str(r.get('statement', '')))},")
        out.append(f"  remark {_rave_str('источник: ' + src)}"
                   + ("," if blockers else ";"))
        if blockers:
            out.append(f"  remark {_rave_str('БЛОКИРОВАНО вопросами '
                                             'анкеты: '
                                             + ', '.join(blockers))};")
        out.append("end")
        out.append("")
    return "\n".join(out)


def export_minizinc(doc: dict) -> str:
    """Скелет модели MiniZinc (решение владельца 2026-08-21): в
    отличие от инстанс-форматов (FlatZinc/XCSP3/MPS/CNF) модель
    MiniZinc отделена от данных — данные придут отдельным .dzn.

    Скелет КОМПИЛИРУЕМ ПО ПОСТРОЕНИЮ: тело каждого жёсткого правила —
    заглушка `true` с пометкой TODO (семантика не выдумывается —
    формальная запись и трассировка в комментарии); мягкие правила —
    слагаемые целевой функции с нулевой заглушкой штрафа и весом в
    комментарии. Из готового .mzn любой CP-решатель получает .fzn
    штатной компиляцией с данными."""
    check_exportable(doc)
    meta = doc.get("meta", {})
    out = [
        "% Скелет модели MiniZinc, автогенерация CDPM "
        f"{meta.get('cdpm_version', '')}.".rstrip(),
        f"% Авиакомпания: {meta.get('customer', '?')}.",
        "% Семантика выражений НЕ выведена автоматически: тело каждого",
        "% ограничения — заглушка true, формальная запись и трассировка",
        "% к источникам — в комментарии над ним. Данные задачи",
        "% подключаются отдельным .dzn.",
        "",
    ]
    soft_pens: list[tuple[str, str]] = []   # (pen_имя, зона правила)
    for r in doc.get("rules", []):
        kind = r.get("kind")
        if kind == "OUT":
            continue          # вне контура оптимизации
        name = _rave_ident(r.get("id", "rule"))
        src = "; ".join(f"{ref.get('sid')}:{ref.get('anchor')}"
                        for ref in r.get("sources", []))
        stmt = str(r.get("statement", "")).replace("\n", " ")
        out.append(f"% [{kind}] {r.get('title', '')}")
        out.append(f"% формально: {stmt}")
        out.append(f"% источник: {src}")
        blockers = r.get("blockers") or []
        if blockers:
            out.append("% БЛОКИРОВАНО вопросами анкеты: "
                       + ", ".join(blockers))
        if kind == "SOFT":
            out.append(f"var int: pen_{name} = 0; "
                       f"% TODO: штраф правила (вес — из ответа "
                       f"авиакомпании)")
            soft_pens.append((f"pen_{name}", r.get("zone", "BOTH")))
        else:
            out.append(f"constraint true; % TODO: {name}")
        out.append("")
    # ЦФ-1: целевые функции — отдельные задачи оптимизации; каждая
    # выписана заготовкой, активен режим feasibility (штрафы либо
    # satisfy) — инженер включает нужную целевую раскомментированием.
    # В сумму штрафов целевой входят ТОЛЬКО мягкие её зоны (+ BOTH):
    # каждая целевая — задача оптимизации своей зоны.
    for o in doc.get("objectives", []):
        o_zones = ("PAIRING", "BOTH") if o.get("zone") == "PAIRING" \
            else ("ROSTERING", "BOTH") if o.get("zone") == "ROSTERING" \
            else ("PAIRING", "ROSTERING", "BOTH")
        soft_ids = [p for p, z in soft_pens if z in o_zones]
        name = _rave_ident(o.get("id", "obj"))
        out.append(f"% [ЦЕЛЕВАЯ/{o.get('zone')}] {o.get('title', '')}"
                   f" — {o.get('direction')}")
        out.append(f"% формально: "
                   + str(o.get("statement", "")).replace("\n", " "))
        pen = (" + " + " + ".join(soft_ids)
               if o.get("direction") == "minimize" and soft_ids
               else " - (" + " + ".join(soft_ids) + ")"
               if soft_ids else "")
        out.append(f"% solve {o.get('direction')} {name}{pen}; "
                   f"% TODO: выразить метрику")
        out.append("")
    if soft_pens:
        out.append("solve minimize "
                   + " + ".join(p for p, _ in soft_pens) + ";")
    else:
        out.append("solve satisfy;")
    out.append("")
    return "\n".join(out)


def export_objective(doc: dict) -> str:
    """ЭКС-2 (решение владельца 2026-08-21): критерий оптимизации
    пейринга и ростеринга отдельным артефактом — мягкие правила зоны
    как компоненты целевой функции. Вес компонента берётся только
    там, где он назван в формальной записи (число не выдумывается);
    рядом — сырой ответ авиакомпании на вопрос целевой функции
    (части 3а/3б) как источник весов и порядка важности."""
    check_exportable(doc)
    return build_objective_json(doc)


# Перенос в модель (ревизия 1.11, приказ 0096): критерий — свойство
# модели, его видит матрица корректности readiness_matrix; здесь —
# реэкспорт для потребителей экспортного слоя (webui, кнопка, гейты).
# ВНИМАНИЕ (тестам): это алиасы-снапшоты — monkeypatch по
# export_solver до матрицы НЕ достаёт, патчить cdpm.model.
_criterion_from_answer = m._criterion_from_answer
derive_pairing_criterion = m.derive_pairing_criterion


def build_objective_json(doc: dict, *, spec_version: str | None = None) -> str:
    """Критерий оптимизации БЕЗ экспортного гейта а/б/в — для пакета
    постановки (мост А): пакет законен и при «а», когда матрица
    разрешает хотя бы feasibility-hard; готовность зон честно
    напечатана внутри полями ready/note. Без явной ревизии используется
    ревизия текущего производителя, а не исторической входной модели."""
    from cdpm import catalog_bridge as cb
    from vmp import objective_projection as op
    cat = cb.catalog()
    if spec_version is None:
        # Resolve at call time: export_task_package imports this module.
        # An omitted version must follow the producer after a future release,
        # without activating the new projection on today's 1.18 producer.
        from cdpm.export_task_package import SPEC_VERSION
        spec_version = SPEC_VERSION
    ready = m.readiness(doc, spec_version)
    # The current 0.5 catalogue/1.18 producer keeps its historical shape.
    # A catalogue declaring the new direction dictionary uses the SAME writer
    # as both readers; 1.20 must never silently fall back when it is unavailable.
    if ("directions" in cat.dictionaries()
            or op.judged_for_spec(spec_version)):
        context = {
            "criterion": m.derive_pairing_criterion(doc),
            "readiness": ready.get("state"), "task_modes": list(m.TASK_MODES),
            "matrix": m.readiness_matrix(doc, spec_version),
            "ready": {task: bool(ready.get(task + "_ready")) for task in op.TASKS},
            "note": {task: "данных зоны недостаточно — компоненты показаны справочно, веса не подтверждены"
                     for task in op.TASKS if not ready.get(task + "_ready")}}
        return op.canonical_bytes(op.project(doc, cat, context=context)).decode("utf-8")
    answers_by_part: dict[str, dict] = {}
    for q in doc.get("questions", []):
        if q.get("part") in ("3a", "3b") \
                and (q.get("answer") or {}).get("status") == "применён":
            answers_by_part[q["part"]] = {
                "question_id": q.get("id"),
                "raw": (q.get("answer") or {}).get("raw", "")}

    def _zone(zone_names: tuple, part: str, zone_ready: bool) -> dict:
        components = []
        for r in doc.get("rules", []):
            if r.get("kind") != "SOFT" \
                    or r.get("zone") not in zone_names:
                continue
            stmt = str(r.get("statement", ""))
            w = soft_weight(r)
            components.append({
                "rule_id": r.get("id"), "title": r.get("title"),
                "statement": stmt,
                "weight": w,
                "sources": r.get("sources", []),
            })
        out = {"ready": zone_ready, "direction": "minimize",
               "components": components}
        if part in answers_by_part:
            out["airline_answer"] = answers_by_part[part]
        if not zone_ready:
            out["note"] = ("данных зоны недостаточно — компоненты "
                           "показаны справочно, веса не подтверждены")
        return out

    def _objectives(zone_names: tuple) -> list[dict]:
        out = []
        for o in doc.get("objectives", []):
            if o.get("zone") not in zone_names:
                continue
            row = {"id": o.get("id"), "title": o.get("title"),
                   "statement": o.get("statement"),
                   "direction": o.get("direction"),
                   "sources": o.get("sources", [])}
            if o.get("metric"):
                # Спецификация 1.1: машинная метрика из закрытого
                # словаря — потребитель исполняет по ней, statement
                # остаётся человеку для сверки.
                row["metric"] = o["metric"]
            out.append(row)
        return out

    pairing = _zone(("PAIRING", "BOTH"), "3a",
                    bool(ready.get("pairing_ready")))
    rostering = _zone(("ROSTERING", "BOTH"), "3b",
                      bool(ready.get("rostering_ready")))
    # ЦФ-1: каждая целевая — отдельная задача оптимизации; критерий =
    # целевая ± сумма штрафов мягких (знак по направлению целевой).
    pairing["objectives"] = _objectives(("PAIRING", "BOTH"))
    rostering["objectives"] = _objectives(("ROSTERING", "BOTH"))
    # Спецификация 1.9: машинный критерий пейринга из строки данных
    # авиакомпании; None — честно (optimization пейринга недоступен).
    pairing["criterion"] = derive_pairing_criterion(doc)
    payload = {
        "customer": doc.get("meta", {}).get("customer"),
        "readiness": ready.get("state"),
        "task_modes": list(m.TASK_MODES),
        "matrix": m.readiness_matrix(doc, spec_version),
        "criterion_note": (
            "optimization: критерий = целевая + Σ штрафов (minimize) "
            "либо целевая − Σ штрафов (maximize); каждая целевая — "
            "отдельная задача. feasibility-soft: минимум Σ штрафов. "
            "feasibility-hard: любое допустимое."),
        "pairing": pairing,
        "rostering": rostering,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def export_csv(doc: dict) -> str:
    """Таблица правил для интеграторов; разделитель — точка с
    запятой (привычен Excel в русской локали)."""
    check_exportable(doc)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["Идентификатор", "Зона", "Тип", "Название",
                "Формальная запись", "Источники",
                "Блокирующие вопросы анкеты"])
    for r in doc.get("rules", []):
        w.writerow([
            r.get("id", ""), r.get("zone", ""), r.get("kind", ""),
            r.get("title", ""), r.get("statement", ""),
            "; ".join(f"{ref.get('sid')}:{ref.get('anchor')}"
                      for ref in r.get("sources", [])),
            "; ".join(r.get("blockers") or []),
        ])
    return buf.getvalue()


def export_json(doc: dict) -> str:
    check_exportable(doc)
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def export(doc: dict, fmt: str) -> tuple[str, str, str]:
    """Единая точка: (содержимое, расширение, mime). Неизвестный
    формат — ValueError; объявленный без документации — честный
    ExportUnavailable."""
    if fmt not in FORMATS:
        raise ValueError(f"неизвестный формат экспорта {fmt!r}; "
                         f"доступны: {sorted(FORMATS)}")
    spec = FORMATS[fmt]
    if spec is None:
        raise ExportUnavailable(
            f"формат {fmt!r} объявлен, но его документация ещё не "
            f"передана владельцем — экспорт появится после неё")
    body = {"rave": export_rave, "minizinc": export_minizinc,
            "csv": export_csv, "json": export_json,
            "objective": export_objective}[fmt](doc)
    return body, spec[0], spec[1]
