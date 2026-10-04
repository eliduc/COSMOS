# -*- coding: utf-8 -*-
"""Сборщик набора «ИД H0-КИ-окна» — ИДАК H0-К плюс два жёстких правила
ростеринга об окнах истории сотрудника (путь А развилки 05.09.2026:
семьи night_series, rolling_rest в базе шаблонов).

Комплект выводится из комплекта H0-К (open-bench/polyaris-h0k/etalon/
VMP-H0K-form.json и VMP-H0K.json) тем же генератором build_kit_h0:
форма H0-К (критериев оптимизации нет, ответы о целевой — отказ) плюс
в разделе «Часть 2. Ростеринг — а) жёсткие ограничения» две новых
записи:
  VN-091 «Серия ночных смен»      — семья night_series,
      параметры max_consecutive_night_duties=2, night_duty_start=1320,
      night_duty_end=360 (окно ночи — местное время базы, tz=base-local);
  VN-092 «Отдых в скользящем окне» — семья rolling_rest,
      параметры weekly_rest_required=2160, weekly_rest_period=10080.
Третье правило VN-093 «Выходные в месяц» (семья days_off_floor) убрано
05.09.2026 вечером по требованию простоты (см. WINDOW_RULES).

Два носителя у каждого нового правила — ПО КОНТРАКТУ cdpm.model:
  * посадка template_fit (категория A, семья, params {имя: {value,
    quote}}) — то, что судит гейт эквивалентности (vmp/equivalence:
    kind, zone, family, params — family_params_of берёт значения из
    посадки A/B) и что рендерит форма: строка docx/xlsx несёт
    формулировку + «Шаблон: <семья> (категория A).» + «Параметры: имя =
    значение единица («цитата»); …» (cdpm.vmp_form.render_row_line) —
    по этому тексту извлекатель обязан воспроизвести посадку;
  * блок logic (машинная логика решателя: субъект, условия, требование,
    окно, ссылки на параметры, enforce=required) — ТОЛЬКО машинный
    носитель: живёт в эталоне VMP-H0KW.json и в поле machine строки
    формы, в документы авиакомпании не рендерится и гейтом
    эквивалентности НЕ сравнивается (vmp/equivalence.diff_models и
    diff_forms логику не читают). Числа блока logic по validate_logic
    допустимы ТОЛЬКО ссылками на записи легаси-списка params правила
    (name/value/unit/as_written/relation, у времени суток tz), поэтому
    каждое новое правило несёт и этот список — схема правила допускает
    template_fit, params и logic одновременно, validate_template_fit
    легаси-список не судит, validate_model судит его validate_param
    (канонизация as_written → value обязана сходиться: «22:00» = 1320,
    «36 часов» = 2160, «7 суток» = 10080).

Известное расхождение конвейера (зафиксировано здесь, не чинится):
сборка извлечения (cdpm/extract.py, _Assembler.add_candidate) при
посадке с params ОТБРАСЫВАЕТ легаси-список params кандидата, а целевой
проход produce_logic проверяет ссылки logic именно по нему — у
кандидата CDPM блок logic к посаженному жёсткому правилу сегодня
прикрепиться не может (уйдёт в примечание «логика отброшена»). На
вердикт гейта это не влияет (логика не сравнивается), исполнитель
COSMOS собирает форму из посадки (vmp/run/logic_checks.plan_from_fit).

Источники (якоря) ВСЕХ правил берутся из свежей карты якорей набора:
три новые строки xlsx сдвигают якорь VN-050 (раздел «б)» уезжает
ниже), поэтому совпадения якорей с H0/H0-К сборщик не требует.

Книга данных data/ID-H0KW-DATA.xlsx (лист «История» у всех 24
сотрудников) собирается отдельным генератором — здесь только
объявляется манифестом. Книга ответов: отказ от критерия (как H0-К) и
ответ «б» на вопросы линии данных об истории (спецификация 1.17.2:
Q-3b-история-частично, Q-3b-история-нет).

Запуск: python open-bench/polyaris-h0kw/build_kit_h0kw.py
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
H0 = REPO / "open-bench" / "polyaris-h0"
H0K = REPO / "open-bench" / "polyaris-h0k"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(H0))
sys.path.insert(0, str(H0K))
import build_kit_h0 as bk  # noqa: E402
import build_kit_h0k as bkk  # noqa: E402
from cdpm import model as m  # noqa: E402
from cdpm import vmp_form as vf  # noqa: E402
from vmp import equivalence as eq  # noqa: E402
from vmp import template_registry as reg  # noqa: E402

CUSTOMER = ("Полярис-H0-КИ-окна (ВМП H0 без критерия оптимизации, с "
            "правилами окон истории сотрудника)")
DOCX_NAME = "ID-H0KW-VMP-1.docx"
XLSX_NAME = "ID-H0KW-VMP-2.xlsx"
JPG_NAME = "ID-H0KW-VMP-3.jpg"
DATA_NAME = "ID-H0KW-DATA.xlsx"     # книга данных — отдельный генератор
RENAMES = {bkk.DOCX_NAME: DOCX_NAME, bkk.XLSX_NAME: XLSX_NAME,
           bkk.JPG_NAME: JPG_NAME}
ZONE, KIND, CATEGORY = "ROSTERING", "HARD", "A"

#: Ответы линии данных об истории (спецификация 1.17.2): вариант «б» —
#: продолжить по частичным данным / с нулевыми остатками.
HISTORY_ANSWER = "б"
HISTORY_QUESTIONS = ("Q-3b-история-частично", "Q-3b-история-нет")

# Три правила окон истории. У каждого параметра: value — машинное
# значение словаря PARAMS; quote — дословный фрагмент формулировки
# (носитель посадки и строки формы); as_written — след легаси-записи
# (канонизируется в value: «чч:мм», «N часов», «N суток»); relation —
# природа параметра (min_/max_ — по имени, границы окна — window);
# tz — только у времени суток. Блоки logic — по образцам
# tools/most_v_logic.py (night_logic, roll_logic, days_off_logic),
# следы as_written — фрагменты формулировок.
WINDOW_RULES = [
    {
        "id": "VN-091", "title": "Серия ночных смен",
        "statement": ("Ночных смен подряд у сотрудника не более 2; ночная "
                      "смена — начавшаяся с 22:00 до 06:00 местного "
                      "времени базы."),
        "family": "night_series",
        "params": [
            {"name": "max_consecutive_night_duties", "value": 2,
             "quote": "не более 2", "as_written": "не более 2",
             "relation": "max"},
            {"name": "night_duty_start", "value": 1320,
             "quote": "22:00", "as_written": "22:00",
             "relation": "window", "tz": "base-local"},
            {"name": "night_duty_end", "value": 360,
             "quote": "06:00", "as_written": "06:00",
             "relation": "window", "tz": "base-local"},
        ],
        "logic": {
            "subject": "duty",
            "condition": [
                {"predicate": "starts_in",
                 "from_param": "night_duty_start",
                 "to_param": "night_duty_end",
                 "as_written": "с 22:00 до 06:00 местного времени базы"},
                {"predicate": "consecutive", "unit": "duty",
                 "param": "max_consecutive_night_duties",
                 "as_written": "Ночных смен подряд"},
            ],
            "demand": {"kind": "count_within", "relation": "max",
                       "param": "max_consecutive_night_duties",
                       "as_written": "не более 2"},
            "source_params": ["night_duty_start", "night_duty_end",
                              "max_consecutive_night_duties"],
            "enforce": "required",
        },
    },
    {
        "id": "VN-092", "title": "Отдых в скользящем окне",
        "statement": ("В каждые 7 суток у сотрудника есть непрерывный "
                      "отдых не менее 36 часов (2160 минут)."),
        "family": "rolling_rest",
        "params": [
            {"name": "weekly_rest_required", "value": 2160,
             "quote": "не менее 36 часов (2160 минут)",
             "as_written": "36 часов", "relation": "min"},
            {"name": "weekly_rest_period", "value": 10080,
             "quote": "В каждые 7 суток", "as_written": "7 суток",
             "relation": "window"},
        ],
        "logic": {
            "subject": "rest",
            "condition": [
                {"predicate": "longer_than", "param": "weekly_rest_required",
                 "as_written": "не менее 36 часов (2160 минут)"},
            ],
            # След требования без цифр: число окна несёт узел window
            # (validate_logic: цифра в следе exists_universe без param —
            # «число ниоткуда»).
            "demand": {"kind": "exists_universe", "subject": "rest",
                       "universe": "employee×rolling_window",
                       "as_written": "у сотрудника есть непрерывный отдых"},
            "window": {"kind": "rolling", "unit": "day",
                       "param": "weekly_rest_period"},
            "source_params": ["weekly_rest_required", "weekly_rest_period"],
            "enforce": "required",
        },
    },
    # VN-093 «Выходные в месяц» (семья days_off_floor, min_days_off=8)
    # убрано 05.09.2026 вечером по требованию простоты набора: правилу
    # нужен закрытый горизонтом календарный месяц (иначе универсум формы
    # пуст — отказ решателя «ПУСТО»), а 27 суток делали ростеринг
    # несходящимся за 240 с. Горизонт книги — семь суток.
]
NEW_IDS = [r["id"] for r in WINDOW_RULES]

# Ревизия 06.09.2026 (слово владельца утром демо: «сделай так, чтобы
# сходилось; набор Н0 менять можно»): предел полётной смены 720 → 600
# минут БЕЗ продления (VN-011 снято). Причина, доказанная разбором
# 06.09: суточное расписание даёт паузы 400–530 мин на базе, которые
# построитель цепочек считал стыковкой внутри смены (граница «стыковка/
# отдых» у него — пол отдыха 720/600), а решатель ростеринга — недопустимым
# отдыхом (граница 360 мин); при пределе смены 780 такие цепочки
# строились, и путь «ростеринг на утверждённом пейринге» не мог их отдать
# ни одному пилоту (−330 hard = точная нижняя граница каталога). При
# пределе 600 ни одна стыковка с зазором ≥ 360 не укладывается в смену
# (наименьший размах такой цепочки 610), и оба пути ростеринга сходятся.
# Свидетель комплекта не меняется: самая длинная его ротация 550 мин.
DUTY_LIMIT_MIN = 600
DUTY_STATEMENT = ("Предел полётной смены — 10:00 (600 минут); продление "
                  "смены не допускается.")
DUTY_QUOTE = "Предел полётной смены — 10:00 (600 минут)"
DROPPED_IDS = ("VN-011",)


def override_duty_form(form: dict) -> None:
    """VN-010 → 600 минут без продления; строка VN-011 снимается."""
    hard = form.setdefault("pairing", {}).setdefault("hard", [])
    row = next((r for r in hard if r.get("id") == "VN-010"), None)
    if row is None:
        raise SystemExit("в форме H0-К нет VN-010")
    row["statement"] = DUTY_STATEMENT
    for p in row.get("params") or []:
        if p.get("name") == "max_duty_duration":
            p["value"] = DUTY_LIMIT_MIN
            p["quote"] = DUTY_QUOTE
    soft = form["pairing"].setdefault("soft", [])
    before = len(soft)
    soft[:] = [r for r in soft if r.get("id") not in DROPPED_IDS]
    if len(soft) != before - len(DROPPED_IDS):
        raise SystemExit("в форме H0-К нет VN-011 для снятия")


def override_duty_etalon(et: dict) -> None:
    """Эталон: VN-010 с пределом 600 и цитатой формулировки; VN-011 нет."""
    rules = et["rules"]
    rule = next((r for r in rules if r.get("id") == "VN-010"), None)
    if rule is None:
        raise SystemExit("в эталоне H0-К нет VN-010")
    rule["statement"] = DUTY_STATEMENT
    rule["template_fit"]["params"]["max_duty_duration"] = {
        "value": DUTY_LIMIT_MIN, "quote": DUTY_QUOTE}
    et["rules"] = [r for r in rules if r.get("id") not in DROPPED_IDS]
    if len(et["rules"]) != len(rules) - len(DROPPED_IDS):
        raise SystemExit("в эталоне H0-К нет VN-011 для снятия")

EXPECT = copy.deepcopy(bkk.EXPECT)
EXPECT["_comment"] = (
    "Ожидание прогона H0-КИ-окна (простой набор, требование владельца "
    "05.09.2026): критерия оптимизации нет → отказ подтверждён, "
    "feasibility-soft (как H0-К); анкета — вопросы о целевой и вопрос об "
    "истории сотрудников (лист «История» не передан): Q-3b-история-нет → "
    "«б» (продолжить с нулевой историей), раздел history пакета — coverage "
    "none с решением пользователя continue_zero. Три правила окон истории "
    "судятся по посадке (kind/zone/family/params), блок logic гейтом не "
    "сравнивается. Раннер сверяет ключи ниже с пакетом.")
EXPECT["questions"] = ["Q-3a-GOAL", "Q-3b-GOAL", "Q-3b-история-нет"]
EXPECT["history"] = {"present": True, "employees_total": 24,
                     "with_history": 0, "coverage_kind": "none",
                     "user_decision": "continue_zero"}
EXPECT["_history_question"] = (
    "истории нет ни у кого (книга данных по умолчанию без листа «История») "
    "— задаётся Q-3b-история-нет, книга ответов отвечает «б»: продолжить с "
    "нулевой историей, COSMOS называет допущение и считает (пример владельца "
    "05.09.2026). Вариант с историей — data/ID-H0KW-HIST-DATA.xlsx, "
    "отдельная демонстрация.")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fit_params(spec: dict) -> dict:
    """params посадки: {имя: {value, quote}} — форма VN-023 эталона H0-К."""
    return {p["name"]: {"value": p["value"], "quote": p["quote"]}
            for p in spec["params"]}


def _legacy_params(spec: dict) -> list:
    """Легаси-список params правила — носитель ссылок блока logic
    (validate_logic читает только его): name/value/unit/as_written/
    relation (+ tz у времени суток)."""
    out = []
    for p in spec["params"]:
        entry = {"name": p["name"], "value": p["value"],
                 "unit": m.PARAMS[p["name"]],
                 "as_written": p["as_written"], "relation": p["relation"]}
        if p.get("tz"):
            entry["tz"] = p["tz"]
        out.append(entry)
    return out


def _form_params(spec: dict) -> list:
    """Параметры строки формы — проекция params посадки (vmp_form.
    _params_to_form): name/value/quote/unit."""
    return [{"name": p["name"], "value": p["value"], "quote": p["quote"],
             "unit": m.PARAMS[p["name"]]} for p in spec["params"]]


def window_rule(spec: dict, rev: str, sources: list) -> dict:
    """Правило эталона: поля как у VN-023 H0-К + params (легаси) + logic."""
    return {
        "id": spec["id"], "title": spec["title"],
        "statement": spec["statement"],
        "zone": ZONE, "kind": KIND,
        "sources": copy.deepcopy(sources),
        "template_fit": {"category": CATEGORY, "registry_rev": rev,
                         "family": spec["family"],
                         "params": _fit_params(spec)},
        "params": _legacy_params(spec),
        "logic": copy.deepcopy(spec["logic"]),
    }


def window_row(spec: dict, rev: str, sources: list) -> dict:
    """Строка формы: как VN-023 формы H0-К; machine несёт вид, отпечаток
    посадки, легаси-params и logic (ровно то, что даёт vmp_form.to_form
    для правила window_rule — сборка это сверяет)."""
    return {
        "id": spec["id"], "title": spec["title"],
        "statement": spec["statement"],
        "sources": copy.deepcopy(sources),
        "template": {"family": spec["family"], "category": CATEGORY},
        "params": _form_params(spec),
        "machine": {"kind": KIND,
                    "template_fit": {"registry_rev": rev},
                    "params": _legacy_params(spec),
                    "logic": copy.deepcopy(spec["logic"])},
    }


def _placeholder_sources() -> list:
    # Якоря ставятся по факту заморозки носителей (после сборки docs);
    # до того — заглушка ради схемы формы (sources: minItems 1).
    return [{"sid": bk.SID_XLSX, "anchor": "—"}]


def _set_registry_rev(form: dict, rev: str) -> None:
    form["registry_rev"] = rev
    for _part, _sec, rows in vf.form_sections(form):
        for row in rows:
            fit = (row.get("machine") or {}).get("template_fit")
            if isinstance(fit, dict) and fit.get("registry_rev"):
                fit["registry_rev"] = rev


def derive_form(h0k_form: dict, rev: str) -> dict:
    """Форма H0-КИ-окна: форма H0-К + две строки в «Часть 2 — а)»."""
    form = copy.deepcopy(h0k_form)
    form["customer"] = CUSTOMER
    meta = form.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = ("ИДАК H0-КИ-окна (2026-09-05): форма H0-К плюс три "
                    "жёстких правила ростеринга об окнах истории "
                    f"сотрудника ({', '.join(NEW_IDS)}: семьи night_series, "
                    "rolling_rest базы шаблонов "
                    f"{rev}); у каждого — посадка A с параметрами и "
                    "машинный блок logic (только в machine/эталоне, в "
                    "документы не рендерится). "
                    + str(meta.get("note") or ""))
    for s in form.get("sources") or []:
        s["file"] = RENAMES.get(s.get("file"), s.get("file"))
    _set_registry_rev(form, rev)
    for part in ("pairing", "rostering"):
        form.setdefault(part, {})["criteria"] = []
    override_duty_form(form)
    meta["note"] = (f"Ревизия 06.09.2026: предел полётной смены "
                    f"{DUTY_LIMIT_MIN} минут без продления (VN-011 снято) "
                    "— чтобы оба пути ростеринга сходились на суточном "
                    "расписании. " + str(meta.get("note") or ""))
    hard = form.setdefault("rostering", {}).setdefault("hard", [])
    present = {r.get("id") for r in hard}
    for spec in WINDOW_RULES:
        if spec["id"] in present:
            raise SystemExit(f"в форме H0-К уже есть {spec['id']}")
        hard.append(window_row(spec, rev, _placeholder_sources()))
    return form


def derive_etalon(h0k_etalon: dict, form: dict, rev: str,
                  amap: dict) -> dict:
    """Эталон ВМП H0-КИ-окна: правила H0-К буква в букву + три правила
    окон истории (после VN-060 — конец жёстких правил ростеринга);
    источники всех правил — из карты якорей набора; отпечаток базы —
    в каждой посадке (в meta слота registry_rev у схемы модели нет —
    отпечаток назван в meta.note)."""
    et = copy.deepcopy(h0k_etalon)
    override_duty_etalon(et)
    et["objectives"] = []
    et["questions"] = []
    meta = et.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = (form.get("meta") or {}).get("note")
    et["sources"] = copy.deepcopy(form.get("sources") or [])
    rules = et["rules"]
    for r in rules:
        fit = r.get("template_fit")
        if isinstance(fit, dict) and fit.get("registry_rev"):
            fit["registry_rev"] = rev
    last_hard = max((i for i, r in enumerate(rules)
                     if r.get("zone") == ZONE and r.get("kind") == KIND),
                    default=len(rules) - 1)
    new = [window_rule(spec, rev, _placeholder_sources())
           for spec in WINDOW_RULES]
    et["rules"] = rules[:last_hard + 1] + new + rules[last_hard + 1:]
    for r in et["rules"]:
        anchors = amap.get(r["id"])
        if not anchors:
            raise SystemExit(f"эталон: у правила {r['id']} нет якоря в карте")
        r["sources"] = copy.deepcopy(anchors)
    return et


def _rel(p: Path) -> str:
    # Пути эталона в манифесте — от корня репозитория (раннер читает
    # REPO / etalon_vmp); сборка вне репозитория (тесты) — абсолютно.
    try:
        return str(p.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def self_check(form: dict, et: dict, amap: dict, by_file: dict) -> list[str]:
    """Самопроверка комплекта: форма чиста; две строки в «Часть 2 — а)»
    и в документе несут формулировку и параметры; эталон чист по трём
    слоям cdpm.model; logic каждого нового правила чист; форма ↔ модель
    без потерь (from_form даёт те же правила, to_form — те же строки);
    источники правил = карта якорей."""
    problems: list[str] = []
    bad = vf.validate_form(form, strict=False)
    problems += [f"форма: {b}" for b in bad]
    if any(form[p].get("criteria") for p in ("pairing", "rostering")):
        problems.append("в форме остались критерии оптимизации")
    hard_ids = [r["id"] for r in form["rostering"]["hard"]]
    for spec in WINDOW_RULES:
        if spec["id"] not in hard_ids:
            problems.append(f"{spec['id']}: нет в «Часть 2 — а)» формы")
    xlsx_lines = {rid: text for kind, rid, text in by_file["xlsx"]
                  if kind == "row"}
    for spec in WINDOW_RULES:
        line = xlsx_lines.get(spec["id"], "")
        if spec["statement"] not in line or "Параметры:" not in line \
                or f"Шаблон: {spec['family']} (категория {CATEGORY})" not in line:
            problems.append(f"{spec['id']}: строка xlsx без формулировки/"
                            f"шаблона/параметров: {line[:120]!r}")
        for p in spec["params"]:
            if f"{p['name']} = {p['value']}" not in line:
                problems.append(f"{spec['id']}: в строке xlsx нет параметра "
                                f"{p['name']} = {p['value']}")
        if "logic" in line or "enforce" in line:
            problems.append(f"{spec['id']}: блок logic просочился в документ")
    contract = (m.validate_schema(et) + m.validate_model(et)
                + m.validate_template_fit(et))
    problems += [f"эталон: {c}" for c in contract]
    by_id = {r["id"]: r for r in et["rules"]}
    for rid in NEW_IDS:
        r = by_id.get(rid)
        if r is None:
            problems.append(f"{rid}: нет в эталоне")
            continue
        problems += [f"{rid}: logic: {e}"
                     for e in m.validate_logic(r["logic"], r)]
        if r["template_fit"].get("family") != \
                reg.PARAM_TO_FAMILY.get(next(iter(r["template_fit"]["params"]))):
            problems.append(f"{rid}: семья посадки не владеет параметрами")
    for r in et["rules"]:
        if r.get("sources") != amap.get(r["id"]):
            problems.append(f"{r['id']}: источники эталона ≠ карте якорей")
    back = vf.from_form(form)
    back_by_id = {r["id"]: r for r in back.get("rules") or []}
    for rid in NEW_IDS:
        if back_by_id.get(rid) != by_id.get(rid):
            problems.append(f"{rid}: from_form(форма) ≠ правило эталона: "
                            f"{json.dumps(back_by_id.get(rid), ensure_ascii=False)[:300]}")
    problems += [f"эталон, форма→модель→форма: {p}"
                 for p in vf.roundtrip_problems(et)]
    projected = vf.to_form(et)
    problems += [f"форма набора ≠ проекция эталона: {p}"
                 for p in eq.diff_forms(form, projected, rows_only=True)["problems"]]
    proj_rows = {r["id"]: r for r in projected["rostering"]["hard"]}
    for spec in WINDOW_RULES:
        mine = next(r for r in form["rostering"]["hard"] if r["id"] == spec["id"])
        if proj_rows.get(spec["id"]) != mine:
            problems.append(f"{spec['id']}: строка формы ≠ to_form(эталон) "
                            f"(machine/params расходятся)")
    return problems


def build(kit_dir: Path = HERE) -> dict:
    rev = reg.registry_rev()
    h0k_form = json.loads((H0K / "etalon" / "VMP-H0K-form.json").read_text(encoding="utf-8"))
    h0k_etalon = json.loads((H0K / "etalon" / "VMP-H0K.json").read_text(encoding="utf-8"))
    form = derive_form(h0k_form, rev)
    docs = kit_dir / "docs"
    etalon = kit_dir / "etalon"
    data = kit_dir / "data"
    for d in (docs, etalon, data):
        d.mkdir(parents=True, exist_ok=True)
    by_file = bk.lines_by_file(form)
    by_file["jpg"] = bkk.jpg_items(by_file)
    bk.build_docx(docs / DOCX_NAME, by_file["docx"])
    bk.build_xlsx(docs / XLSX_NAME, by_file["xlsx"])
    jpg_lines = bk.build_jpg(docs / JPG_NAME, by_file["jpg"])
    frozen = bk.freeze_texts(docs / DOCX_NAME, docs / XLSX_NAME)
    amap, problems = bk.build_anchors_map(by_file, frozen, jpg_lines)
    n_rows = sum(len(rows) for _p, _s, rows in vf.form_sections(form))
    n_map = sum(1 for k in amap if not k.startswith("_"))
    if n_map != n_rows:
        problems.append(f"в карте {n_map} записей, в форме {n_rows}")
    # Источники строк формы — по факту заморозки носителей (три новые
    # строки xlsx сдвигают якоря раздела «б)» — совпадения с H0/H0-К
    # не требуется, требуется полнота карты).
    for _part, _sec, rows in vf.form_sections(form):
        for row in rows:
            anchors = amap.get(row["id"])
            if not anchors:
                problems.append(f"{row['id']}: нет якоря в карте")
                continue
            row["sources"] = copy.deepcopy(anchors)
    if problems:
        raise SystemExit("САМОПРОВЕРКА КРАСНАЯ (носители):\n  " + "\n  ".join(problems))
    for s in form.get("sources") or []:
        s["sha256"] = _sha(docs / s["file"])
    et = derive_etalon(h0k_etalon, form, rev, amap)
    problems = self_check(form, et, amap, by_file)
    if problems:
        raise SystemExit("САМОПРОВЕРКА КРАСНАЯ:\n  " + "\n  ".join(problems))
    _dump(etalon / "VMP-H0KW-form.json", form)
    _dump(etalon / "VMP-H0KW.json", et)
    _dump(etalon / "anchors-map.json", amap)
    _dump(etalon / "jpg-lines.json", jpg_lines)
    _dump(etalon / "ANSWER-BOOK-MACHINE.json", {
        "form_version": 1, "customer": CUSTOMER,
        "_comment": ("Набор «ИД H0-КИ-окна»: законные вопросы — о целевой "
                     "(Q-3a-GOAL, Q-3b-GOAL; ответ «критерия оптимизации "
                     "нет», как у H0-К); вопрос линии данных об истории "
                     "сотрудников (спецификация 1.17.2: Q-3b-история-частично "
                     "/ Q-3b-история-нет; ответ «б» — продолжить) в варианте "
                     "hist не задаётся (история 24/24) и закрыт книгой только "
                     "для варианта nohist. Любой "
                     "другой вопрос = дефект конвейера (несопоставленный "
                     "вопрос — красный отчёт). Сопоставление: exact по id, "
                     "затем prefix."),
        "exact": {"Q-3a-GOAL": bkk.GOAL_ANSWER["3a"],
                  "Q-3b-GOAL": bkk.GOAL_ANSWER["3b"],
                  **{q: HISTORY_ANSWER for q in HISTORY_QUESTIONS}},
        "prefix": {"Q-3a-GOAL-T": bkk.GOAL_ANSWER["3a"],
                   "Q-3b-GOAL-T": bkk.GOAL_ANSWER["3b"]},
        "regex": []})
    manifest = {
        "customer": CUSTOMER,
        "stage": ("ИД H0-КИ-окна (H0-К + два жёстких правила окон истории "
                  "сотрудника: серия ночных смен, отдых в скользящем окне, "
                  "выходные в месяц; спецификация 1.17)"),
        "data_class": "synthetic",
        "llm_scope": "any",
        "llm_scope_basis": ("набор синтетический: вымышленная авиакомпания, "
                            "генератор в репозитории (build_kit_h0kw.py поверх "
                            "build_kit_h0k.py и build_kit_h0.py); решение "
                            "владельца 03.09.2026 — для синтетических и "
                            "открытых данных любые модели, включая облачные"),
        "rules_files": [f"docs/{DOCX_NAME}", f"docs/{XLSX_NAME}", f"docs/{JPG_NAME}"],
        "data_files": [f"data/{DATA_NAME}"],
        "answer_book": "etalon/ANSWER-BOOK-MACHINE.json",
        "agents_layout": "../polyaris-h0/agents-layout-h0.json",
        "strict_questions": False,
        "etalon_vmp": _rel(etalon / "VMP-H0KW.json"),
        "etalon_form": _rel(etalon / "VMP-H0KW-form.json"),
        "expect": EXPECT,
        "note": ("ИДАК H0-КИ-окна = H0-К плюс два жёстких правила ростеринга "
                 f"об окнах истории сотрудника ({', '.join(NEW_IDS)}: семьи "
                 "night_series, rolling_rest) — в xlsx "
                 "(часть 2 а), с посадкой A и параметрами в тексте строки; "
                 "блок logic — только в эталоне и machine формы, гейт судит "
                 "посадку (kind/zone/family/params). Книга ответов закрывает "
                 "вопросы о целевой («критерия оптимизации нет») и вопрос об "
                 "истории («б» — продолжить с нулевой историей). Книга данных "
                 "ID-H0KW-DATA.xlsx — простой набор (требование владельца "
                 "05.09.2026): сбалансированная программа рейсов, без листа "
                 "«История»; отдельный генератор build_data_h0kw.py."),
    }
    _dump(kit_dir / "kit-manifest.json", manifest)
    res = {"docx": len(by_file["docx"]), "xlsx": len(by_file["xlsx"]),
           "jpg": len(by_file["jpg"]), "rows": n_rows, "map": n_map,
           "rules": len(et["rules"]), "objectives": len(et["objectives"]),
           "registry_rev": rev,
           "data_present": (data / DATA_NAME).is_file()}
    print(f"ИД H0-КИ-окна собран: docx {res['docx']} абзацев, xlsx {res['xlsx']} "
          f"строк, jpg {res['jpg']} абзацев ({jpg_lines}); записей формы "
          f"{res['rows']}, карта якорей {res['map']}; эталон: правил "
          f"{res['rules']} (новых {len(NEW_IDS)}: {', '.join(NEW_IDS)}), целевых "
          f"{res['objectives']}; база шаблонов {rev}; самопроверка чиста; "
          f"книга данных data/{DATA_NAME}: "
          f"{'на месте' if res['data_present'] else 'НЕТ (отдельный генератор)'}")
    return res


if __name__ == "__main__":
    # Переобёртка stdout — только при запуске из консоли (при импорте
    # из тестов она ломает захват вывода pytest).
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    build()
