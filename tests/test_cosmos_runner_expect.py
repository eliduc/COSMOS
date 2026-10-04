# -*- coding: utf-8 -*-
"""Раннер наборов H0 (open-bench/polyaris-h0/run_local_pipeline.py):
дополнительные ожидания манифеста для наборов-вариаций Н0-B и Н0-C
(поручение владельца 09.09.2026) — судятся чистой функцией
`extra_expect_problems(exp, model, man, requests_status)` без сервера:
полнота параметров семьи, пробел покрытия, посадка правила, пакет не
собран, заявка в каталог.

Честность итога (поручение владельца 10.09.2026): раннер отделяет
ключи, которые СУДИТ сам, от ключей, которых не касается (с причиной),
и судит числа сверки с ИДАК только по машинному отчёту нажатий COSMOS
(`cosmos_numbers_problems`); число строк сверки — справочное, критерий
«без ошибок» — вердикты, сертификаты и НОЛЬ нарушений."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _runner():
    p = ROOT / "open-bench" / "polyaris-h0" / "run_local_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_local_pipeline_h0", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rule(rid, cat="A", family=None, params=None, kind="HARD", zone="ROSTERING"):
    fit = {"category": cat}
    if family:
        fit["family"] = family
    if params:
        fit["params"] = {k: {"value": v} for k, v in params.items()}
    if cat == "C":
        fit["gap"] = "семьи нет"
    return {"id": rid, "kind": kind, "zone": zone, "template_fit": fit}


def test_полнота_семьи_по_всем_носителям():
    r = _runner()
    model = {"rules": [_rule("VN-023", family="rest_floors", params={"min_rest_home": 720,
                                                                     "min_rest_outstation": 600})]}
    exp = {"family_complete": {"rest_floors": ["min_rest_home", "min_rest_outstation"]}}
    assert r.extra_expect_problems(exp, model, {}, None) == []
    model2 = {"rules": [_rule("VN-023", family="rest_floors", params={"min_rest_home": 720})]}
    probs = r.extra_expect_problems(exp, model2, {}, None)
    assert probs and "min_rest_outstation" in probs[0] and "rest_floors" in probs[0]
    # параметры могут сидеть у разных носителей одной семьи (как в H0)
    model3 = {"rules": [_rule("VN-023", family="rest_floors", params={"min_rest_home": 720}),
                        _rule("VN-024", family="rest_floors", params={"min_rest_outstation": 600})]}
    assert r.extra_expect_problems(exp, model3, {}, None) == []


def test_пробел_покрытия_и_посадка_правила():
    r = _runner()
    model = {"rules": [_rule("VN-023", family="rest_floors", params={"min_rest_home": 720}),
                       _rule("VN-026", cat="C")]}
    assert r.extra_expect_problems({"coverage_gap_rules": ["VN-026"]}, model, None, None) == []
    probs = r.extra_expect_problems({"coverage_gap_rules": []}, model, None, None)
    assert probs and "VN-026" in probs[0]
    probs = r.extra_expect_problems({"rule_landing": {"VN-026": "A"}}, model, None, None)
    assert probs and "VN-026" in probs[0] and "'C'" in probs[0]
    model2 = {"rules": [_rule("VN-026", family="duty_series", params={"max_consecutive_duties": 3})]}
    assert r.extra_expect_problems({"rule_landing": {"VN-026": "A"}}, model2, {}, None) == []
    probs = r.extra_expect_problems({"rule_landing": {"VN-099": "A"}}, model2, {}, None)
    assert probs and "VN-099" in probs[0]


def test_правила_модели_называются_по_эталону_через_якоря():
    """Модель CDPM называет правила стабильными id по якорю
    (R-S2-лист-«VMP-H0-2»-строка-5), ожидания манифеста — VN-номерами
    эталона: сопоставление по якорям источников, как у гейта
    эквивалентности (Н0-C фаза 1, run-0102, 09.09.2026 — раннер краснел
    «посадка C у правил ['R-S2-…-строка-5'], ожидалось ['VN-026']»)."""
    r = _runner()
    src = {"sid": "S2", "anchor": "лист «VMP-H0-2»,  строка 5"}   # лишний пробел — нормализуется
    etalon = {"rules": [{"id": "VN-026", "sources": [{"sid": "S2", "anchor": "лист «VMP-H0-2», строка 5"}]},
                        {"id": "VN-060", "sources": [{"sid": "S2", "anchor": "лист «VMP-H0-2», строка 4"}]}]}
    stable = {"id": "R-S2-лист-«VMP-H0-2»-строка-5", "sources": [src],
              "template_fit": {"category": "C"}}
    other = {"id": "R-S2-лист-«VMP-H0-2»-строка-4", "sources": [{"sid": "S2", "anchor": "лист «VMP-H0-2», строка 4"}],
             "template_fit": {"category": "A", "family": "reserve_floors", "params": {"x": {"value": 1}}}}
    orphan = {"id": "R-S9-стр-1", "sources": [{"sid": "S9", "anchor": "стр 1"}], "template_fit": {"category": "A"}}
    model = {"rules": [stable, other, orphan]}
    assert r.rule_ids_by_etalon(model["rules"], etalon) == {
        stable["id"]: "VN-026", other["id"]: "VN-060", orphan["id"]: orphan["id"]}
    assert r.extra_expect_problems({"coverage_gap_rules": ["VN-026"]}, model, None, None,
                                   etalon=etalon) == []
    assert r.extra_expect_problems({"rule_landing": {"VN-060": "A"}}, model, {}, None,
                                   etalon=etalon) == []
    probs = r.extra_expect_problems({"rule_landing": {"VN-026": "A"}}, model, {}, None,
                                    etalon=etalon)
    assert probs and "VN-026" in probs[0] and "'C'" in probs[0]
    # без эталона — прежнее поведение: сравнение по id модели
    probs = r.extra_expect_problems({"coverage_gap_rules": ["VN-026"]}, model, None, None)
    assert probs and "строка-5" in probs[0]


def test_пакет_не_собран_как_ожидание():
    r = _runner()
    model = {"rules": []}
    assert r.extra_expect_problems({"package_ready": False}, model, None, None) == []
    probs = r.extra_expect_problems({"package_ready": False}, model, {"coverage": 1}, None)
    assert probs and "пакет" in probs[0].lower()
    probs = r.extra_expect_problems({"package_ready": True}, model, None, None)
    assert probs and "пакет" in probs[0].lower()


def test_заявка_в_каталог_по_ручке_панели():
    r = _runner()
    exp = {"catalog_requests": {"count": 1, "kind": "HARD", "zone": "ROSTERING",
                                "statement_contains": "смен подряд"}}
    status = {"candidates": [
        {"rule_id": "VN-026", "kind": "HARD", "zone": "ROSTERING",
         "statement": "Рабочих смен подряд — не более 3; после серии — не менее одного свободного дня.",
         "candidate": True, "request_id": "req-1"},
        {"rule_id": "VN-050", "kind": "SOFT", "zone": "ROSTERING",
         "statement": "разброс", "candidate": False, "reason": "служебная C"}]}
    assert r.extra_expect_problems(exp, {"rules": []}, None, status) == []
    probs = r.extra_expect_problems(exp, {"rules": []}, None, {"candidates": []})
    assert probs and "заяв" in probs[0].lower()
    bad = {"candidates": [dict(status["candidates"][0], zone="PAIRING")]}
    probs = r.extra_expect_problems(exp, {"rules": []}, None, bad)
    assert probs and "PAIRING" in probs[0]
    probs = r.extra_expect_problems(exp, {"rules": []}, None, None)
    assert probs and "ручк" in probs[0].lower()


# ── Честность итога ожиданий (поручение владельца 10.09.2026) ──
# Прежде строка «ОЖИДАНИЯ НАБОРА выполнены» перечисляла ВСЕ ключи
# `expect`, включая cosmos_numbers — числа сверки с ИДАК, которые
# получаются НАЖАТИЯМИ в COSMOS, а не прогоном CDPM.

#: Образец машинного отчёта нажатий COSMOS (волна 8, локальный прогон
#: Н0-C фазы 2 09.09.2026): вердикты, сертификаты, счётчики сверки и
#: нарушения; здесь — синтетические данные той же формы.
PRESS_REPORT = {
    "base": "http://cosmos.invalid",
    "package_sha": "bb" * 32,
    "package_run": "run-0035",
    "pairing": {
        "attempt": "20260909T213954-d93dd9",
        "verdict": "пейринг построен", "error": None, "score": None,
        "certified": True, "code": "b2e6136",
        "check": {"at": "2026-09-09T21:40:05",
                  "counts": {"not_checkable": 5, "soft_info": 3, "ok": 4},
                  "channel": "cli", "reader": "cloud/claude-sonnet-5",
                  "problems": 0}},
    "rostering": {
        "attempt": "20260909T214009-91961b",
        "verdict": "допустимо", "error": None, "score": "0hard/-120soft",
        "certified": True, "code": "b2e6136",
        "check": {"at": "2026-09-09T21:44:16",
                  "counts": {"not_checkable": 5, "ok": 7, "soft_info": 3},
                  "channel": "cli", "reader": "cloud/claude-sonnet-5",
                  "problems": 0}},
    "rc": 0,
}


def _press(**over):
    """Копия образца отчёта с точечной подменой блока задачи."""
    doc = json.loads(json.dumps(PRESS_REPORT))
    for task, fields in over.items():
        doc[task] = {**doc[task], **fields} if isinstance(fields, dict) else fields
    return doc


def test_итог_различает_судимые_и_несудимые_ключи():
    """`checked` — только то, что раннер судил сам; несудимые ключи
    названы с причиной; неизвестный ключ не выдаёт себя за проверенный."""
    r = _runner()
    exp = {"questions": [], "objectives": 2, "family_complete": {},
           "cosmos_numbers": {"pairing": "11/0"}, "family": {"id": "f"},
           "cosmos_default_mode": "feasibility-soft",
           "_comment": "не ключ ожидания", "новый_ключ": 1}
    judged, skipped = r.expect_key_verdicts(exp)
    assert judged == ["family_complete", "objectives", "questions"], judged
    assert set(skipped) == {"cosmos_numbers", "family", "cosmos_default_mode",
                            "новый_ключ"}, skipped
    assert "--cosmos-press" in skipped["cosmos_numbers"]
    assert "нажатиями в COSMOS" in skipped["cosmos_numbers"]
    assert "неизвестен" in skipped["новый_ключ"]
    # с отчётом нажатий числа COSMOS становятся судимыми
    judged2, skipped2 = r.expect_key_verdicts(exp, ("cosmos_numbers",))
    assert "cosmos_numbers" in judged2 and "cosmos_numbers" not in skipped2


def test_числа_cosmos_без_отчёта_нажатий_не_судятся():
    """Без машинного отчёта ключ честно отказывается судить — раннер не
    выдаёт «выполнено» по числам, которых не видел."""
    r = _runner()
    exp = {"cosmos_numbers": {"pairing": "11/0", "rostering": "14/0"}}
    probs, warns = r.cosmos_numbers_problems(exp, None)
    assert probs and "--cosmos-press" in probs[0] and warns == []
    # ожидания без ключа — молчание, а не отказ
    assert r.cosmos_numbers_problems({}, None) == ([], [])


def test_числа_cosmos_с_отчётом_нажатий_выполнены():
    r = _runner()
    exp = {"cosmos_numbers": {"pairing": "12/0", "rostering": "15/0"}}
    assert r.cosmos_numbers_problems(exp, PRESS_REPORT) == ([], [])
    # пакет отчёта сверяется с пакетом прогона
    probs, _w = r.cosmos_numbers_problems(exp, PRESS_REPORT, "aa" * 32)
    assert probs and "другом пакете" in probs[0]
    assert r.cosmos_numbers_problems(exp, PRESS_REPORT, "bb" * 32) == ([], [])


def test_расхождение_только_в_строках_при_нуле_нарушений_предупреждение():
    """Число строк сверки гуляет по чтению модели (not_checkable ±1):
    при совпавших нарушениях это ПРЕДУПРЕЖДЕНИЕ, а не провал (критерий
    «без ошибок» — вердикты, сертификаты и 0 нарушений)."""
    r = _runner()
    exp = {"cosmos_numbers": {"pairing": "11/0", "rostering": "14/0"}}
    probs, warns = r.cosmos_numbers_problems(exp, PRESS_REPORT)
    assert probs == [], probs
    assert len(warns) == 2, warns
    assert "строк сверки 12" in warns[0] and "в манифесте 11" in warns[0]
    assert "справочное" in warns[0] and "выполнен" in warns[0]
    assert "строк сверки 15" in warns[1] and "в манифесте 14" in warns[1]


def test_расхождение_в_нарушениях_вердикте_и_сертификате_провал():
    r = _runner()
    exp = {"cosmos_numbers": {"pairing": "12/0", "rostering": "15/0"}}
    # (1) нарушение сверки — провал, а не предупреждение
    bad = _press(pairing={"check": {"counts": {"ok": 4, "soft_info": 3,
                                               "not_checkable": 5},
                                    "problems": 1}})
    probs, warns = r.cosmos_numbers_problems(exp, bad)
    assert probs and "нарушений 1" in probs[0] and warns == []
    # (2) вердикт задачи
    bad = _press(rostering={"verdict": "недопустимо"})
    probs, _w = r.cosmos_numbers_problems(exp, bad)
    assert probs and "вердикт" in probs[0] and "недопустимо" in probs[0]
    # (3) сертификат не выдан
    bad = _press(pairing={"certified": False})
    probs, _w = r.cosmos_numbers_problems(exp, bad)
    assert probs and "сертификат не выдан" in probs[0]
    # (4) задачи нет в отчёте / нет блока сверки
    bad = _press(rostering=None)
    probs, _w = r.cosmos_numbers_problems(exp, bad)
    assert probs and "нет этой задачи" in probs[0]
    bad = _press(pairing={"check": None})
    probs, _w = r.cosmos_numbers_problems(exp, bad)
    assert probs and "нет блока сверки" in probs[0]
    # (5) ожидание не вида «строк/нарушений»
    probs, _w = r.cosmos_numbers_problems({"cosmos_numbers": {"pairing": "нет"}},
                                          PRESS_REPORT)
    assert probs and "вида" in probs[0]


def test_ключ_cosmos_press_читается_отдельной_функцией(tmp_path):
    """--cosmos-press: путь не назван — None (числа не судятся); файла
    нет, не JSON, не словарь — отказ словами ДО прогона."""
    r = _runner()
    assert r.load_cosmos_press(None) is None
    with pytest.raises(SystemExit) as e:
        r.load_cosmos_press(tmp_path / "нет-такого.json")
    assert "--cosmos-press" in str(e.value) and "файла нет" in str(e.value)
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        r.load_cosmos_press(bad)
    assert "не словарь" in str(e.value)
    bad.write_text("{не json", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        r.load_cosmos_press(bad)
    assert "JSON" in str(e.value)
    good = tmp_path / "press.json"
    good.write_text(json.dumps(PRESS_REPORT, ensure_ascii=False),
                    encoding="utf-8")
    assert r.load_cosmos_press(good) == PRESS_REPORT


def test_раннер_знает_ключ_cosmos_press_в_командной_строке(tmp_path,
                                                           monkeypatch):
    """Ключ объявлен разбором командной строки и доезжает до раннера;
    умолчание — «отчёта нажатий нет»."""
    r = _runner()
    got: dict = {}

    class _Stop(Exception):
        pass

    def _fake_runner(*a, **kw):
        got.clear()
        got.update(kw)
        raise _Stop()
    monkeypatch.setattr(r, "Runner", _fake_runner)
    out = str(tmp_path / "out")
    press = str(tmp_path / "press.json")
    for argv, want in ((["--out", out], None),
                       (["--out", out, "--cosmos-press", press], press)):
        with pytest.raises(_Stop):
            r.main(argv)
        assert (str(got["cosmos_press"]) if got["cosmos_press"] else None) \
            == want, got


# ── Паспорт прогона до первой стадии (поручение владельца 10.09.2026) ──
# «Перед каждым прогоном нужно чётко установить все его параметры… Не
# может быть, что остаётся старый каталог предыдущего прогона». Сам
# паспорт судится в tests/test_run_passport.py; здесь — ПОРЯДОК раннера:
# двери контура → раннер → паспорт → первая стадия.

def test_ключи_паспорта_ставят_двери_и_доезжают_до_паспорта(tmp_path,
                                                             monkeypatch):
    """Ключи --catalog / --bindings / --solver-url ставят двери контура
    ДО построения раннера и называются источниками в паспорте, а паспорт
    собирается РАНЬШЕ первой стадии."""
    r = _runner()
    order: list = []
    monkeypatch.setattr(
        r, "apply_doors",
        lambda c=None, b=None, s=None: order.append(("двери", c, b, s)))

    class _Fake:
        def __init__(self, *a, **kw):
            order.append(("раннер", None, None, None))
            self.state = {"stages": {}}

        def make_passport(self, **kw):
            order.append(("паспорт", kw.get("catalog_key"),
                          kw.get("bindings_key"), kw.get("solver_key")))
            return {}

        def guard_code(self, stage):
            pass

        def stage_create(self):
            order.append(("стадия", "create", None, None))

        # Запись о прогоне ведёт тот же раннер (проект «Запись о
        # прогоне»): подстава обязана знать её приёмы, иначе стенд
        # судит не порядок, а полноту подставы.
        def make_record(self, a, planned):
            return None

        def record_stage(self, *a, **kw):
            pass

        def finish_record(self, state=None):
            pass

    monkeypatch.setattr(r, "Runner", _Fake)
    rc = r.main(["--out", str(tmp_path / "o"), "--until", "create",
                 "--catalog", "каталог-прогона",
                 "--bindings", "привязки-прогона",
                 "--solver-url", "адрес-решателя"])
    assert rc == 0
    assert [x[0] for x in order] == ["двери", "раннер", "паспорт", "стадия"]
    assert order[0][1:] == ("каталог-прогона", "привязки-прогона",
                            "адрес-решателя")
    assert order[2][1:] == ("каталог-прогона", "привязки-прогона",
                            "адрес-решателя")


def test_раскладка_набора_ищется_от_корня_репозитория():
    """Раскладку моделей ищет ОДНО место продукта (vmp.run_passport), и
    все наборы формы H0 называют её путём ОТ КОРНЯ: копия набора вне
    open-bench раскладку находит (корень отказа показа 09.09.2026)."""
    from vmp import run_passport as rp
    for name in ("polyaris-h0", "polyaris-h0b", "polyaris-h0c",
                 "polyaris-h0k"):
        kit = ROOT / "open-bench" / name
        man = json.loads((kit / "kit-manifest.json").read_text(encoding="utf-8"))
        assert man.get(rp.LAYOUT_KEY_REPO), name
        info = rp.resolve_layout(kit, man, ROOT)
        assert info["file"] is not None and info["file"].is_file(), name
        assert rp.LAYOUT_KEY_REPO in info["source"], name


# ── Запись о прогоне в раннере (проект 10.09.2026, шаг 1) ──────────────
# Прогон — сущность с началом и концом: голова из паспорта до стадии
# create, журнал этапов с исходом и хронометражем, итоги в конце.
# Словарь полей судит tests/test_run_record.py; здесь — чистые функции
# раннера и его порядок, без служб и без моделей.

def _passport_for_record(**over) -> dict:
    """Наименьший паспорт, из которого запись о прогоне уже собирается:
    каталог, привязки и документы правил — то, без чего объявление не
    принимается."""
    sha = "1" * 64
    doc = {"schema": "cosmos-run-passport/v1",
           "kit": {"customer": "Проба-раннер",
                   "manifest": "наборы/проба/kit-manifest.json",
                   "references": {}},
           "rules_documents": [{"name": "ПРАВИЛА.docx", "sha256": sha,
                                "bytes": 10}],
           "data_documents": [],
           "catalog": {"path": "каталоги/проба", "version": "0.4.0",
                       "fingerprint": sha},
           "bindings": {"file": "vmp/bindings.json", "catalog_version": "0.4.0",
                        "catalog_fingerprint": sha},
           "agents_layout": {"roles": {}},
           "solver": {"engine": "timefold", "url": None, "build": None},
           "python": {"version": "3.14.4", "packages": [],
                      "packages_sha256": sha},
           "code": {"commit": "коммит-пробы", "fingerprint": "отпечаток-пробы"},
           "panel": {"base_url": None, "in_process": True},
           "stages": {"until": "package"}}
    doc.update(over)
    return doc


class _Keys:
    """Ключи командной строки для сборки записи (то, что читает
    build_record)."""

    def __init__(self, purpose=None, contour=None, from_stage=None,
                 until="package"):
        self.purpose = purpose
        self.contour = contour
        self.from_stage = from_stage
        self.until = until


def test_исход_стадии_разделяет_отказ_по_правилу_и_сбой():
    """409 на выпуске пакета — пробел покрытия, то есть штатный отказ ПО
    ПРАВИЛУ (Н0-C, фаза 1); прочие 4xx — тоже отказ по правилу с кодом
    ответа; 5xx — поломка службы, то есть сбой. Стадия без отметки
    состояния — успех."""
    from vmp import run_record as rr
    r = _runner()
    assert r.stage_outcome({"stages": {}}, "package") == (rr.OUTCOME_OK, None,
                                                          None)
    gap = r.stage_outcome({"stages": {"package_status": {
        "status": 409, "error": "правило не ложится ни на один шаблон"}}},
        "package")
    assert gap[0] == rr.OUTCOME_RULE and gap[1] == "coverage-gap"
    assert "пробел покрытия" in gap[2]
    other = r.stage_outcome({"stages": {"package_status": {"status": 422}}},
                            "package")
    assert other[0] == rr.OUTCOME_RULE and other[1] == "http-422"
    broken = r.stage_outcome({"stages": {"package_status": {"status": 500}}},
                             "package")
    assert broken[0] == rr.OUTCOME_FAILURE and broken[1] == "http-500"


def test_счётчики_и_артефакты_стадии_берутся_из_состояния():
    """Счётчики этапа — все числа блока стадии под их собственными
    именами (имена носителей, а не новые); признаки и слова числами не
    притворяются."""
    r = _runner()
    state = {"run": "прогон-1",
             "stages": {"create": {"run": "прогон-1", "sec": 12,
                                   "jpg_exact": True,
                                   "freeze": {"files": 3, "clauses": 41}},
                        "package_status": {"path": "пакет.zip",
                                           "coverage": 0.5}}}
    counters = r.stage_counters(state, "create")
    assert counters == {"sec": 12, "files": 3, "clauses": 41}
    assert r.stage_counters(state, "package") == {"coverage": 0.5}
    assert r.stage_artifacts(state, "create") == [
        {"kind": "прогон CDPM", "path": "прогон-1", "sha256": None}]
    assert r.stage_artifacts(state, "package")[0]["kind"] == "пакет постановки"


def test_план_этапов_записи_по_ключам():
    """План этапов — от --from-stage до --until: непройденные этапы
    плана потом помечаются «не начат»."""
    r = _runner()
    assert r.planned_stages(_Keys(until="create")) == ["create"]
    assert r.planned_stages(_Keys(from_stage="extract", until="cycle")) == [
        "extract", "data_run", "cycle"]


def test_запись_о_прогоне_собирается_из_паспорта_и_ключей():
    """Голова записи — параметры паспорта; цель и площадку называют
    ключи, умолчания названы словами («регресс», «local»), а адреса
    служб едут из паспорта."""
    from vmp import run_record as rr
    r = _runner()
    record = r.build_record(_passport_for_record(), _Keys(),
                            ["create", "package"])
    assert rr.validate(record) == []
    d = record["declaration"]
    assert d["purpose"] == r.PURPOSE_DEFAULT == "регресс"
    assert d["contour"]["name"] == r.CONTOUR_DEFAULT == "local"
    assert d["created_by"] == r.RECORD_ACTOR
    assert d["planned_stages"] == ["create", "package"]
    assert d["catalog"]["version"] == "0.4.0"
    assert record["results"]["state"] == rr.STATE_DECLARED
    named = r.build_record(_passport_for_record(),
                           _Keys(purpose="приёмка", contour="prod"),
                           ["create"])
    assert named["declaration"]["purpose"] == "приёмка"
    assert named["declaration"]["contour"]["name"] == "prod"


def test_раннер_пишет_запись_файлом_и_ведёт_её_журнал(tmp_path):
    """Приёмы самого раннера (без служб и моделей): голова ложится
    файлом, каждая стадия дописывает этап со счётчиками и артефактами,
    итоги закрывают запись, а этап плана, до которого прогон не дошёл,
    помечается «не начат»."""
    from vmp import run_record as rr
    r = _runner()
    # Раннер без __init__: так же его строят стенды CDPM — приёмы записи
    # не имеют права требовать поднятой панели.
    rn = object.__new__(r.Runner)
    rn.out = tmp_path
    rn.state = {"stages": {}}
    rn.passport = _passport_for_record()
    rn.record = None
    assert rn.make_record(_Keys(), ["create", "extract", "package"])
    assert (tmp_path / rr.RECORD_FILE).is_file()

    rn.state["run"] = "прогон-1"
    rn.state["stages"]["create"] = {"sec": 3, "freeze": {"files": 2}}
    rn.record_stage("create", "2026-09-10T09:00:00+03:00",
                    "2026-09-10T09:00:03+03:00", rr.OUTCOME_OK)
    rn.state["stages"]["package_status"] = {"status": 409, "error": "пробел"}
    rn.record_stage("package", "2026-09-10T09:00:03+03:00",
                    "2026-09-10T09:00:05+03:00",
                    *r.stage_outcome(rn.state, "package"))
    rn.finish_record()

    record = rr.load(tmp_path)
    assert rr.validate(record) == []
    journal = {e["stage"]: e for e in record["journal"]}
    assert journal["create"]["outcome"] == rr.OUTCOME_OK
    assert journal["create"]["counters"] == {"sec": 3, "files": 2}
    assert journal["create"]["artifacts"][0]["path"] == "прогон-1"
    assert journal["create"]["duration_machine_s"] == 3
    assert journal["package"]["reason_code"] == "coverage-gap"
    assert journal["extract"]["outcome"] == rr.OUTCOME_NOT_STARTED
    assert record["results"]["state"] == rr.STATE_RULE_STOP
    assert record["results"]["timing"]["machine_s"] == 5


def test_ключи_цели_и_площадки_объявлены_командной_строкой(tmp_path,
                                                            monkeypatch):
    """Ключи --purpose и --contour есть в разборе командной строки и
    доезжают до сборки записи; чужое слово цели не принимается."""
    r = _runner()
    seen: dict = {}

    class _Fake:
        def __init__(self, *a, **kw):
            self.state = {"stages": {}}

        def make_passport(self, **kw):
            return {}

        def make_record(self, a, planned):
            seen.update({"purpose": a.purpose, "contour": a.contour,
                         "planned": list(planned)})
            return None

        def guard_code(self, stage):
            pass

        def stage_create(self):
            pass

        def record_stage(self, *a, **kw):
            pass

        def finish_record(self, state=None):
            pass

    monkeypatch.setattr(r, "Runner", _Fake)
    assert r.main(["--out", str(tmp_path / "o"), "--until", "create",
                   "--purpose", "приёмка", "--contour", "test"]) == 0
    assert seen == {"purpose": "приёмка", "contour": "test",
                    "planned": ["create"]}
    with pytest.raises(SystemExit):
        r.main(["--out", str(tmp_path / "o"), "--purpose", "просто так"])


def test_раннер_ведёт_журнал_записи_по_стадиям(tmp_path, monkeypatch):
    """Порядок раннера: паспорт → запись (голова) → стадии с отметками в
    журнале → итоги. Отметка идёт ПОСЛЕ стадии и несёт её исход."""
    from vmp import run_record as rr
    r = _runner()
    order: list = []

    class _Fake:
        def __init__(self, *a, **kw):
            self.state = {"stages": {}}

        def make_passport(self, **kw):
            order.append("паспорт")
            return {}

        def make_record(self, a, planned):
            order.append("запись:голова")
            return None

        def guard_code(self, stage):
            pass

        def stage_create(self):
            order.append("стадия:create")
            self.state["stages"]["create"] = {"sec": 1}

        def stage_data(self):
            order.append("стадия:data")

        def record_stage(self, stage, started_at, finished_at, outcome,
                         reason=None, words=None):
            order.append(f"журнал:{stage}:{outcome}")
            assert started_at and finished_at

        def finish_record(self, state=None):
            order.append(f"итоги:{state}")

    monkeypatch.setattr(r, "Runner", _Fake)
    assert r.main(["--out", str(tmp_path / "o"), "--until", "data"]) == 0
    assert order == ["паспорт", "запись:голова", "стадия:create",
                     f"журнал:create:{rr.OUTCOME_OK}", "стадия:data",
                     f"журнал:data:{rr.OUTCOME_OK}", "итоги:None"]


def test_сбой_стадии_попадает_в_журнал_и_закрывает_запись(tmp_path,
                                                           monkeypatch):
    """Стадия упала — этап помечается сбоем, запись закрывается
    состоянием «сбой», и исключение идёт дальше: летопись прогона не
    глотает поломку."""
    from vmp import run_record as rr
    r = _runner()
    order: list = []

    class _Fake:
        def __init__(self, *a, **kw):
            self.state = {"stages": {}}

        def make_passport(self, **kw):
            return {}

        def make_record(self, a, planned):
            return None

        def guard_code(self, stage):
            pass

        def stage_create(self):
            raise RuntimeError("заморозка не прошла")

        def record_stage(self, stage, started_at, finished_at, outcome,
                         reason=None, words=None):
            order.append((stage, outcome, reason, words))

        def finish_record(self, state=None):
            order.append(("итоги", state, None, None))

    monkeypatch.setattr(r, "Runner", _Fake)
    with pytest.raises(RuntimeError):
        r.main(["--out", str(tmp_path / "o"), "--until", "create"])
    assert order[0][:3] == ("create", rr.OUTCOME_FAILURE, "stage-exception")
    assert "заморозка не прошла" in order[0][3]
    assert order[1] == ("итоги", rr.STATE_FAILURE, None, None)


# ── Итоги записи: сравнения и золотой (шаг 3, разделы 3.3 и 12.4) ──────
# Раннер кладёт в хвост записи три сравнения: с ОЖИДАНИЯМИ набора (ровно
# тот суд, что прошёл на стадии package — второго суда нет), с
# ПРЕДЫДУЩИМ прогоном того же корня и с ЗОЛОТЫМ (оба — из ручки дерева
# панели). Признак золотого раннер не ставит НИКОГДА.

DECLARED = {"expectations": {"kind": "манифест набора",
                             "ref": "open-bench/проба/kit-manifest.json"}}
JUDGED = {"problems": [], "checked": ["objectives", "questions"],
          "not_judged": {"cosmos_numbers": "нужен машинный отчёт нажатий"},
          "warnings": ["строк сверки 12, в манифесте 11"],
          "cosmos_press": None}


def test_сравнение_с_ожиданиями_берётся_из_суда_стадии_пакета():
    """Форма `results.comparison.expectations`: вид и путь ожиданий — из
    ОБЪЯВЛЕНИЯ, судимые и несудимые ключи, расхождения и предупреждения
    — из суда стадии package. Вердикт различает три вещи: расхождения —
    «не выполнены», судимые ключи без расхождений — «выполнены», судить
    было нечего — «не судились»."""
    r = _runner()
    got = r.comparison_expectations(DECLARED, JUDGED)
    assert got == {"kind": "манифест набора",
                   "ref": "open-bench/проба/kit-manifest.json",
                   "checked": ["objectives", "questions"],
                   "not_judged": {"cosmos_numbers":
                                  "нужен машинный отчёт нажатий"},
                   "problems": [],
                   "warnings": ["строк сверки 12, в манифесте 11"],
                   "verdict": r.EXPECT_VERDICT_OK}
    bad = r.comparison_expectations(
        DECLARED, {**JUDGED, "problems": ["целевых 1, ожидалось 2"]})
    assert bad["verdict"] == r.EXPECT_VERDICT_BAD
    assert bad["problems"] == ["целевых 1, ожидалось 2"]
    # Суда не было: прогон встал раньше стадии package либо у набора нет
    # ключа expect — «не судились», а не «выполнены».
    none = r.comparison_expectations(DECLARED, None)
    assert none["verdict"] == r.EXPECT_VERDICT_NONE
    assert none["checked"] == [] and none["problems"] == []
    assert none["ref"] == DECLARED["expectations"]["ref"]
    # Все ключи набора несудимые — сказать «выполнены» не о чем.
    empty = r.comparison_expectations(
        DECLARED, {"checked": [], "not_judged": {"cosmos_numbers": "нажатия"}})
    assert empty["verdict"] == r.EXPECT_VERDICT_NONE
    # Объявления нет — вид ожиданий раннера всё равно назван.
    assert r.comparison_expectations(None, None)["kind"] == r.EXPECTATIONS_KIND


def test_сравнение_с_предыдущим_и_золотым_из_ответа_дерева():
    """`compare_previous` и `compare_golden` ручки дерева ложатся в итоги
    строкой `{run_id, words}`: с КЕМ сравнивали (`a` сравнения) и слова
    сравнения. Ручка не ответила либо сравнивать не с чем — ПУСТО."""
    r = _runner()
    tree = {"node": {"run_id": "запись-2"},
            "compare_previous": {"kind": "против предыдущего прогона того же "
                                         "корня",
                                 "a": "запись-1", "b": "запись-2",
                                 "words": ["СРАВНЕНИЕ ПРОГОНОВ запись-1 → "
                                           "запись-2", "   корень один"]},
            "compare_golden": None}
    got = r.comparison_from_tree(tree)
    assert got["previous_run"] == {
        "run_id": "запись-1",
        "words": ["СРАВНЕНИЕ ПРОГОНОВ запись-1 → запись-2", "   корень один"]}
    # Золотого у корня нет — раннер не выдумывает сравнения.
    assert got["golden"] == {}
    assert r.comparison_from_tree(None) == {"previous_run": {}, "golden": {}}
    assert r.comparison_from_tree("не словарь") == {"previous_run": {},
                                                    "golden": {}}
    assert r.comparison_row({"a": None, "words": []}) == {"run_id": None,
                                                          "words": []}
    assert r.tree_path("прогон-1") == "/api/runs/прогон-1/tree"
    assert r.tree_path(None) is None


def test_слова_сравнения_считает_общий_модуль_дерева():
    """Слова сравнения раннер не сочиняет: они приходят из `vmp/run_tree.py`
    — одного модуля на обе системы. Раннер лишь называет, с кем
    сравнивали, и переносит слова в итоги."""
    from vmp import run_tree as rt
    r = _runner()
    a = {"declaration": {"run_id": "запись-1"},
         "results": {"verdicts": {"create": "EQUIVALENT"}, "state": "завершён"}}
    b = {"declaration": {"run_id": "запись-2"},
         "results": {"verdicts": {"create": "DIFFERS"}, "state": "завершён"}}
    cmp = rt.compare(a, b, rt.COMPARE_PREVIOUS)
    row = r.comparison_row(cmp)
    assert row["run_id"] == "запись-1"
    assert row["words"] == cmp["words"] and row["words"]
    assert any("РАСХОЖДЕНИЕ" in w for w in row["words"])


def test_ожидание_золотого_ставит_только_владелец():
    """`golden_by_owner_only`: признак у ХОЗЯИНА записи без подписи —
    красная строка; с подписью владельца — зелено; признак, поставленный
    РАННЕРОМ, — красная строка всегда."""
    r = _runner()
    exp = {"golden_by_owner_only": {"runner_sets_golden": False}}
    clean = {"results": {"golden": {"confirmed": False, "by": None,
                                    "at": None, "words": None}}}
    assert r.golden_by_owner_only_problems(exp, clean) == []
    signed = {"results": {"golden": {"confirmed": True, "by": "владелец",
                                     "at": "2026-09-10T20:00:00+03:00",
                                     "words": "прогон-образец"}}}
    assert r.golden_by_owner_only_problems(exp, signed) == []
    for gap in ("by", "at", "words"):
        unsigned = {"results": {"golden": {**signed["results"]["golden"],
                                           gap: None}}}
        probs = r.golden_by_owner_only_problems(exp, unsigned)
        assert len(probs) == 1 and "БЕЗ подписи" in probs[0], gap
    # Признак поставил сам раннер — расхождение, даже если у хозяина всё
    # чисто: прогон-образец называет владелец, а не машина.
    probs = r.golden_by_owner_only_problems(exp, clean, mine=signed)
    assert len(probs) == 1 and "РАННЕР" in probs[0]
    # Записи у хозяина не видно — судить признак нечем, и это сказано.
    probs = r.golden_by_owner_only_problems(exp, None)
    assert len(probs) == 1 and "записи у хозяина нет" in probs[0]
    # Ожидания в наборе нет — судить нечего.
    assert r.golden_by_owner_only_problems({}, None) == []
    assert r.golden_signature_missing({"confirmed": True}) == [
        "кто подтвердил (by)", "когда (at)", "слова владельца (words)"]


def test_манифесты_трёх_наборов_несут_ожидание_золотого():
    """Ожидание «золотой — только словом владельца» стоит у всех трёх
    наборов приёмки и одним текстом (сборщики берут его у базового
    набора). Ключ известен раннеру: без нажатий — с причиной, с
    нажатиями — судимый."""
    r = _runner()
    for name in ("polyaris-h0", "polyaris-h0b", "polyaris-h0c"):
        man = json.loads((ROOT / "open-bench" / name / "kit-manifest.json")
                         .read_text(encoding="utf-8"))
        want = man["expect"]["golden_by_owner_only"]
        assert want["runner_sets_golden"] is False, name
        assert "владелец" in want["_note"] and "Prod" in want["_note"], name
        judged, skipped = r.expect_key_verdicts(man["expect"])
        assert "golden_by_owner_only" in skipped, name
        assert "--cosmos-press" in skipped["golden_by_owner_only"], name
        judged, skipped = r.expect_key_verdicts(
            man["expect"], ("cosmos_numbers", "record_stitched",
                            "golden_by_owner_only"))
        assert "golden_by_owner_only" in judged, name
    # Фаза 2 Н0-C описана тем же ожиданием, что и живой ключ expect; у
    # фазы 1 его нет — прогон встаёт до нажатий, приёмки не было.
    h0c = json.loads((ROOT / "open-bench" / "polyaris-h0c" /
                      "kit-manifest.json").read_text(encoding="utf-8"))
    assert h0c["expect_phases"]["2"]["golden_by_owner_only"] == \
        h0c["expect"]["golden_by_owner_only"]
    assert "golden_by_owner_only" not in h0c["expect_phases"]["1"]


# ── Ожидаемые вердикты стадий (ключ stage_verdicts манифеста) ──────────

#: Стадии CDPM, пройденные прогоном, в том виде, в каком их держит
#: состояние раннера (гейт кладёт сводку под именем стадии).
_STAGES_DONE = {"extract": {"verdict": "NOT_EQUIVALENT"},
                "answers": {"verdict": "EQUIVALENT"},
                "package": {"verdict": "EQUIVALENT"}}


def test_вердикты_стадий_судятся_по_манифесту_а_не_по_умолчанию():
    """Вердикт стадии — свойство НАБОРА: у Н0-B извлечение «не
    эквивалентно до ответов» по замыслу, и это ожидание, а не ошибка.
    Расхождение — красная строка с ИМЕНЕМ стадии, что вышло и что
    ожидалось; ключа нет — прежнее поведение (не судится)."""
    r = _runner()
    exp = {"stage_verdicts": {"extract": "NOT_EQUIVALENT",
                              "package": "EQUIVALENT",
                              "_note": "пояснение набора не судится"}}
    assert r.stage_verdicts_problems(exp, _STAGES_DONE) == ([], [])
    # ожидание разошлось с делом — краснота словами
    probs, warns = r.stage_verdicts_problems(
        {"stage_verdicts": {"extract": "EQUIVALENT"}}, _STAGES_DONE)
    assert warns == []
    assert len(probs) == 1 and "extract" in probs[0]
    assert "NOT_EQUIVALENT" in probs[0] and "ожидался" in probs[0]
    # ключа нет — судить нечего
    assert r.stage_verdicts_problems({}, _STAGES_DONE) == ([], [])
    assert r.stage_verdicts_problems({"stage_verdicts": None},
                                     _STAGES_DONE) == ([], [])


def test_непройденная_стадия_и_неизвестное_имя_стадии():
    """Стадия, до которой прогон не дошёл, — предупреждение, а не
    краснота: сверять нечего. Имя стадии, неизвестное раннеру, — именно
    краснота: молча пропустить ожидание нельзя."""
    r = _runner()
    probs, warns = r.stage_verdicts_problems(
        {"stage_verdicts": {"cycle": "EQUIVALENT"}}, _STAGES_DONE)
    assert probs == []
    assert len(warns) == 1 and "cycle" in warns[0] and "не пройдена" in warns[0]
    probs, warns = r.stage_verdicts_problems(
        {"stage_verdicts": {"выдумка": "EQUIVALENT"}}, _STAGES_DONE)
    assert warns == []
    assert len(probs) == 1 and "выдумка" in probs[0] and "не знает" in probs[0]
    # ожидание не словом вердикта — тоже краснота
    probs, _w = r.stage_verdicts_problems(
        {"stage_verdicts": {"package": 1}}, _STAGES_DONE)
    assert len(probs) == 1 and "не слово вердикта" in probs[0]


def test_вердикты_стадий_cosmos_судятся_только_с_отчётом_нажатий():
    """Пейринг, ростер и сверка с ИДАК получаются НАЖАТИЯМИ: без
    машинного отчёта — предупреждение с названным ключом, с отчётом —
    сверка по вердиктам отчёта."""
    r = _runner()
    exp = {"stage_verdicts": {"pairing": "пейринг построен",
                              "rostering": "допустимо",
                              "source-check": "без нарушений"}}
    probs, warns = r.stage_verdicts_problems(exp, _STAGES_DONE, None)
    assert probs == [] and len(warns) == 3
    assert all("--cosmos-press" in w for w in warns)
    assert r.stage_verdicts_problems(exp, _STAGES_DONE, PRESS_REPORT) \
        == ([], [])
    # вердикт задачи разошёлся — краснота с именем стадии
    bad = _press(rostering={"verdict": "недопустимо"})
    probs, _w = r.stage_verdicts_problems(exp, _STAGES_DONE, bad)
    assert len(probs) == 1 and "rostering" in probs[0]
    assert "недопустимо" in probs[0]
    # нарушения сверки — вердикт стадии сверки называет их числом
    dirty = _press(pairing={"check": {"counts": {"ok": 4}, "problems": 2}})
    probs, _w = r.stage_verdicts_problems(exp, _STAGES_DONE, dirty)
    assert len(probs) == 1 and "source-check" in probs[0]
    assert "нарушений 2" in probs[0]
    assert r.source_check_verdict({}) is None
    # блоков сверки нет — судить нечем, и это краснота при ожидании
    probs, _w = r.stage_verdicts_problems(
        {"stage_verdicts": {"source-check": "без нарушений"}},
        _STAGES_DONE, {"pairing": {"verdict": "пейринг построен"}})
    assert len(probs) == 1 and "нет блоков сверки" in probs[0]


def test_вердикт_задачи_cosmos_берётся_из_манифеста_а_не_из_умолчания():
    """Числа COSMOS судят вердикт задачи по ожиданию НАБОРА, если оно
    объявлено; без объявления — прежнее умолчание раннера."""
    r = _runner()
    assert r.expected_task_verdict({}, "pairing") == "пейринг построен"
    assert r.expected_task_verdict(
        {"stage_verdicts": {"pairing": "своё слово"}}, "pairing") \
        == "своё слово"
    exp = {"cosmos_numbers": {"pairing": "12/0"},
           "stage_verdicts": {"pairing": "своё слово"}}
    probs, _w = r.cosmos_numbers_problems(exp, PRESS_REPORT)
    assert probs and "своё слово" in probs[0]
    mine = _press(pairing={"verdict": "своё слово"})
    assert r.cosmos_numbers_problems(exp, mine) == ([], [])


def _kit_builder():
    """Сборщик базового комплекта: у него живут ОБЩИЕ вердикты стадий,
    и манифесты обязаны совпадать с ним по словам вердиктов."""
    p = ROOT / "open-bench" / "polyaris-h0" / "build_kit_h0.py"
    sys.path.insert(0, str(p.parent))
    spec = importlib.util.spec_from_file_location("build_kit_h0_stand", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: Манифесты комплектов приёмки: пять наборов плюс ПОЛНЫЙ вариант Н0-КИ
#: (комплект в подкаталоге набора — свой манифест и своя книга данных,
#: документы правил и эталон общие со стоп-вариантом). Ожидания обоих
#: вариантов Н0-КИ ставит сборщик Н0-К при переносе (sync_h0kh).
KIT_MANIFESTS = (
    ("polyaris-h0", "polyaris-h0/kit-manifest.json"),
    ("polyaris-h0b", "polyaris-h0b/kit-manifest.json"),
    ("polyaris-h0c", "polyaris-h0c/kit-manifest.json"),
    ("polyaris-h0k", "polyaris-h0k/kit-manifest.json"),
    ("polyaris-h0kh", "polyaris-h0kh/kit-manifest.json"),
    ("polyaris-h0kh-полный", "polyaris-h0kh/full/kit-manifest.json"),
)


def _kit_manifest(rel: str) -> dict:
    return json.loads((ROOT / "open-bench" / rel).read_text(encoding="utf-8"))


def test_манифесты_пяти_комплектов_и_полного_варианта_несут_вердикты_стадий():
    """Поле объявлено у всех пяти комплектов и у полного варианта Н0-КИ,
    все имена стадий раннеру известны, слова вердиктов — те же, что у
    сборщика базового комплекта (одно место правды: пересборка комплекта
    поле не теряет и не переписывает), и ключ считается СУДИМЫМ (он не
    уезжает в «не судится»)."""
    r = _runner()
    bk = _kit_builder()
    base = dict(bk.STAGE_VERDICTS_CDPM, **bk.STAGE_VERDICTS_COSMOS)
    known = set(r.STAGES) | set(r.COSMOS_TASK_STAGES) | {r.SOURCE_CHECK_STAGE}
    assert set(base) <= known, "сборщик назвал стадию, которой раннер не знает"
    # Извлечение — свойство НАБОРА: эквивалентно там, где вопросов при
    # извлечении нет (Н0, Н0-C — вопросы об истории рождаются линией
    # данных, стадия cycle), не эквивалентно там, где вопрос задаётся при
    # извлечении (Н0-B — второй параметр семьи, Н0-К/Н0-КИ — целевая;
    # у обоих вариантов Н0-КИ документы правил одни и те же).
    extract_by_kit = {"polyaris-h0": "EQUIVALENT", "polyaris-h0c": "EQUIVALENT",
                      "polyaris-h0b": "NOT_EQUIVALENT",
                      "polyaris-h0k": "NOT_EQUIVALENT",
                      "polyaris-h0kh": "NOT_EQUIVALENT",
                      "polyaris-h0kh-полный": "NOT_EQUIVALENT"}
    for name, rel in KIT_MANIFESTS:
        man = _kit_manifest(rel)
        want = man["expect"]["stage_verdicts"]
        named = [k for k in want if not k.startswith("_")]
        assert named, name
        assert set(named) <= known, (name, sorted(set(named) - known))
        assert all(isinstance(want[k], str) and want[k].strip() for k in named)
        assert all(want[k] == base[k] for k in named if k != "extract"), name
        assert want["extract"] == extract_by_kit[name], name
        assert "_note" in want, name
        judged, skipped = r.expect_key_verdicts(man["expect"])
        assert "stage_verdicts" in judged, name
        assert "stage_verdicts" not in skipped, name
    # Замысел Н0-B: извлечение НЕ эквивалентно до ответов, после — да.
    h0b = _kit_manifest("polyaris-h0b/kit-manifest.json")
    assert h0b["expect"]["stage_verdicts"]["extract"] == "NOT_EQUIVALENT"
    assert h0b["expect"]["stage_verdicts"]["answers"] == "EQUIVALENT"
    assert "ДО ОТВЕТОВ" in h0b["expect"]["stage_verdicts"]["_note"]
    # Фаза 1 Н0-C ключа не несёт: там пакет не выдаётся вовсе.
    h0c = _kit_manifest("polyaris-h0c/kit-manifest.json")
    assert "stage_verdicts" in h0c["expect_phases"]["2"]
    assert "stage_verdicts" not in h0c["expect_phases"]["1"]
    # Стадии COSMOS объявляет только тот вариант Н0-КИ, чей замысел их
    # ждёт: у стоп-варианта кнопка ростеринга ОСТАНАВЛИВАЕТСЯ словами (у
    # H14 нет истории), у полного история есть у всех 24 — ростеринг
    # «допустимо», и вердикт стадии назван. Пейринг и сверку с ИДАК не
    # объявляет ни один: живого прогона с отчётом нажатий у набора нет.
    стоп = _kit_manifest("polyaris-h0kh/kit-manifest.json")["expect"]
    полный = _kit_manifest("polyaris-h0kh/full/kit-manifest.json")["expect"]
    assert set(стоп["stage_verdicts"]) & set(r.COSMOS_TASK_STAGES) == set()
    assert полный["stage_verdicts"]["rostering"] == base["rostering"]
    assert "pairing" not in полный["stage_verdicts"]
    assert r.SOURCE_CHECK_STAGE not in полный["stage_verdicts"]
    # Без машинного отчёта нажатий объявленный ростеринг — предупреждение
    # с названным ключом, а не краснота (стадии COSMOS судятся нажатиями).
    probs, warns = r.stage_verdicts_problems(полный, _STAGES_DONE, None)
    assert probs == []
    assert len(warns) == 1 and "--cosmos-press" in warns[0]


def test_манифесты_пяти_комплектов_и_полного_варианта_несут_непротиворечивость():
    """Гейт непротиворечивости выпуска (11.09.2026): все пять комплектов
    и полный вариант Н0-КИ ждут «sat» — жёсткие правила у них есть,
    противоречий в них нет. Слово одно на все наборы (константа базового
    сборщика: пересборка комплекта его не теряет и не переписывает), ключ
    считается СУДИМЫМ, а фаза 1 Н0-C ожидания не несёт — там пакет не
    выдаётся вовсе."""
    r = _runner()
    bk = _kit_builder()
    assert bk.CONSISTENCY in r.CONSISTENCY_VERDICTS
    for name, rel in KIT_MANIFESTS:
        man = _kit_manifest(rel)
        exp = man["expect"]
        assert exp.get("consistency") == bk.CONSISTENCY, name
        judged, skipped = r.expect_key_verdicts(exp)
        assert "consistency" in judged, name
        assert "consistency" not in skipped, name
        # Ожидание сбывается на пакете с этим вердиктом и краснеет на
        # пакете без блока — сверяется ровно то, что кладёт гейт.
        assert r.consistency_problems(
            exp, {"consistency": {"verdict": bk.CONSISTENCY}}) == []
        assert r.consistency_problems(exp, {}), name
    h0c = _kit_manifest("polyaris-h0c/kit-manifest.json")
    assert h0c["expect_phases"]["2"]["consistency"] == bk.CONSISTENCY
    assert "consistency" not in h0c["expect_phases"]["1"]
