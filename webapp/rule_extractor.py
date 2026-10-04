"""Экстрактор правил из документов — двухконтурный ИИ поверх Rule Pack
(2026-07-17, дорожная карта №4 документа COSMOS-DATA-FORMALIZATION-RU,
этап Ф7; множитель скорости формализации).

Двухконтурность (в точности схема Ф7):
  * sensitivity="public" (национальные нормы — документы открыты) — набор
    ведёт себя как llm_scope="any": допустима сильная облачная модель;
  * sensitivity="confidential" (коллективный договор, внутренние
    положения) — как llm_scope="local-only": ТОЛЬКО локальная модель,
    запрос облака — громкий ScopeViolation.
Маршрутизация повторно использует llm_backend.resolve_backend — тот же
механизм, что и у импорта данных; отдельной логики контура здесь нет.

ИИ предлагает — человек утверждает: каждый кандидат попадает в пакет
ЧЕРНОВИКОМ через штатный rule_packs.add_rule (полная валидация:
происхождение обязательно, лимиты, словарь параметров Яруса 1); кандидаты,
не прошедшие валидацию, честно возвращаются списком ошибок. Утверждение —
только руками (дисциплина Rule Pack: оба вида тестов, все тесты проходят).

Генератор тестов: если модель не предложила числовые тесты для
parameter-правила, они достраиваются детерминированно от значения
(min_*: легально value*1.1, нелегально value*0.9; max_* — наоборот).
"""
from __future__ import annotations

import json
import re

from . import llm_backend, rule_packs

EXTRACT_MAX_CHARS = 60_000
EXTRACT_TIMEOUT_S = 300.0
MAX_CANDIDATES = 40

SENSITIVITIES = ("public", "confidential")

_SYSTEM = """\
Ты — инженер по формализации правил планирования экипажей (COSMOS).
Из фрагмента нормативного документа извлеки правила, влияющие на
легальность нарядов/ростеров. Ответь ТОЛЬКО JSON-массивом (можно пустым),
без прозы:
[{"title": "<=90 симв, по-русски",
  "statement": "одна фраза без двусмысленностей, по-русски",
  "section": "пункт/статья документа",
  "parameter": "<имя из словаря или null>",
  "value": <число или null>,
  "note": "<для правил вне словаря: краткая суть>",
  "positive_given": <число или null>,
  "negative_given": <число или null>}]

Словарь исполняемых параметров (Ярус 1, минуты если *_min):
- min_sit_min: минимальная стыковка между рейсами в наряде (легально >=)
- max_landings_per_duty: максимум посадок за наряд (легально <=)
- max_duty_span_min: максимальная длительность наряда (легально <=)
- min_rest_min: минимальный отдых между нарядами (легально >=)
- max_monthly_block_min: месячный потолок налёта (легально <=)
- max_pairing_span_min: максимальная длительность пейринга (легально <=)

Правило, выражаемое параметром, ОБЯЗАНО иметь parameter+value (переведи
часы в минуты). Правило вне словаря — parameter=null и заполненный note.
positive_given — значение, которое должно быть легально; negative_given —
которое должно отклоняться. Не выдумывай правил, которых нет в тексте."""


class RuleExtractError(ValueError):
    """User-facing extraction failure."""


def _extract_json_array(text: str):
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else None
    except json.JSONDecodeError:
        return None


_AUTO_NOTE = "ИИ-черновик — проверьте извлечённое значение"


def _auto_tests(parameter: str, value: float,
                pos, neg) -> list[dict]:
    """Tests for an extracted parameter rule. A given the model actually
    proposed is kept VERBATIM — even if it contradicts the extracted value:
    that contradiction is the only available signal that the value was
    mis-extracted, so the test must be allowed to FAIL (blocking approval)
    rather than be silently coerced into passing. Only a MISSING given is
    fabricated from the value, and such a test is marked as a machine draft
    so a human never mistakes a tautological green tick for verification."""
    def num(x):
        return x if isinstance(x, (int, float)) and not isinstance(x, bool) \
            else None
    pos, neg = num(pos), num(neg)
    lo, hi = value * 0.9, value * 1.1
    default_pos = hi if parameter.startswith("min_") else lo
    default_neg = lo if parameter.startswith("min_") else hi
    tests = []
    tests.append({"kind": "positive",
                  "given": pos if pos is not None else default_pos,
                  "description": "" if pos is not None else _AUTO_NOTE})
    tests.append({"kind": "negative",
                  "given": neg if neg is not None else default_neg,
                  "description": "" if neg is not None else _AUTO_NOTE})
    return tests


def _candidate_to_rule(c: dict, document: str) -> dict:
    """One model candidate -> the rule body rule_packs.add_rule validates.
    Raises RulePackError there when the candidate is unusable."""
    param = c.get("parameter")
    if param:
        try:
            value = float(c.get("value"))
        except (TypeError, ValueError):
            raise rule_packs.RulePackError(
                f"кандидат «{c.get('title')}»: parameter без числового value")
        constraint = {"kind": "parameter", "parameter": str(param),
                      "value": value}
        tests = _auto_tests(str(param), value,
                            c.get("positive_given"), c.get("negative_given"))
    else:
        constraint = {"kind": "note", "note": str(c.get("note") or "")}
        tests = [{"kind": "positive",
                  "description": "подтверждающий кейс — заполнит человек"},
                 {"kind": "negative",
                  "description": "отклоняемый кейс — заполнит человек"}]
    return {
        "title": c.get("title"),
        "statement": c.get("statement"),
        "source": {"document": document, "section": c.get("section")},
        "constraint": constraint,
        "tests": tests,
    }


def extract(pack_id: str, text: str, sensitivity: str, document: str,
            by: str, backend_request: str | None = None) -> dict:
    """Extract rule candidates from a document fragment into pack DRAFTS."""
    if rule_packs.get_pack(pack_id) is None:
        raise RuleExtractError(f"no such rule pack: {pack_id}")
    if sensitivity not in SENSITIVITIES:
        raise RuleExtractError(
            f"'sensitivity' must be one of {list(SENSITIVITIES)}")
    text = str(text or "").strip()
    if len(text) < 40:
        raise RuleExtractError("текст документа слишком короткий")
    document = str(document or "").strip()
    if not document:
        raise RuleExtractError("укажите название исходного документа")
    truncated = len(text) > EXTRACT_MAX_CHARS
    text = text[:EXTRACT_MAX_CHARS]

    # the whole contour story in one line: public≈any, confidential≈local-only
    scope = "any" if sensitivity == "public" else "local-only"
    backend = llm_backend.resolve_backend(scope, backend_request)
    res = llm_backend.complete("ФРАГМЕНТ ДОКУМЕНТА:\n" + text,
                               backend=backend, timeout_s=EXTRACT_TIMEOUT_S,
                               system=_SYSTEM, usage="text")
    if not res.get("ok"):
        raise RuleExtractError(f"модель недоступна: {res.get('error')}")
    arr = _extract_json_array(res.get("text"))
    if arr is None:
        raise RuleExtractError("ответ модели не разобрался как JSON-массив")

    added, errors = [], []
    for c in arr[:MAX_CANDIDATES]:
        if not isinstance(c, dict):
            continue
        try:
            rule = rule_packs.add_rule(pack_id,
                                       _candidate_to_rule(c, document), by)
            added.append(rule["id"])
        except rule_packs.RulePackError as exc:
            errors.append(str(exc))
    # M03: surface silent losses — document text past EXTRACT_MAX_CHARS
    # (`truncated`) and candidates beyond MAX_CANDIDATES (`candidates_dropped`)
    # so the UI can warn the reviewer that some rules may be missing.
    candidates_dropped = max(0, len(arr) - MAX_CANDIDATES)
    return {"added": len(added), "rule_ids": added, "errors": errors,
            "candidates": len(arr), "backend": backend,
            "model": res.get("model"), "truncated": truncated,
            "candidates_dropped": candidates_dropped,
            "sensitivity": sensitivity}
