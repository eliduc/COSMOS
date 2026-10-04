# -*- coding: utf-8 -*-
"""Ревизия 1.3: машинные параметры правил — «ни одного голого числа».

Охраняемое: канонизация «чч:мм» → минуты живёт у производителя и
сверяется с as_written; единицы различают природу; window не граница;
time_of_day без tz не существует; чужие имена не проставляются со
следом; manifest.crew_size производен от параметра правила и гейт
ловит расхождение."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from cdpm import extract, model as m
from cdpm import export_task_package as tp
from cdpm.gates import gate_task_package as gate


@pytest.fixture
def package_translator(tmp_path, monkeypatch):
    """Only the external translation input is synthetic, not an export verdict.

    Same explicit seam as the normal test harness; local Z3, consistency
    reports, producer and gate remain real. These are metadata regressions,
    not a semantic acceptance of the synthetic hard-rule translations.
    """
    from cdpm import catalog_bridge
    from test_k03_subject_vocabulary import selected_catalogue
    from _smt_gate_stub import channel_factory
    # A private validated 0.6 fixture declares the count unit words needed
    # by landing/2. The shipped 0.5 catalogue is not silently upgraded.
    cat = selected_catalogue(tmp_path / "modern-package-catalog", monkeypatch)
    # Existing executor declarations copied as synthetic metadata, scoped
    # to this catalogue. This is not support approval or a stand result.
    root = Path(__file__).resolve().parents[1]
    for folder, env in (("vmp", "CREW_COSMOS_BINDINGS"), ("cdpm", "CDPM_BINDINGS_FILE")):
        raw = json.loads((root / folder / "bindings.json").read_text(encoding="utf-8"))
        raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
        path = tmp_path / (folder + "-package-bindings.json")
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv(env, str(path))
    monkeypatch.setattr(tp, "smt_channel_factory", channel_factory)
    try:
        yield
    finally:
        catalog_bridge.forget()


def _modern_crew_fit(rule, value=2, condition=None):
    """Complete the test input, never migrate or relax the export boundary."""
    _modern_fit(rule, "crew_composition", {"crew_size": {
        "value": value, "unit": "count", "relation": "eq",
        "condition": condition, "quote": str(value)}})


def _modern_fit(rule, family, cells):
    from cdpm import catalog_bridge
    from template_catalog import family_contract
    cat = catalog_bridge.catalog()
    rule["template_fit"] = {
        "schema_version": "landing/2", "category": "A",
        "family": family, "registry_rev": cat.fingerprint(),
        "family_contract": family_contract.snapshot(cat, family, cells),
        "params": copy.deepcopy(cells)}
    for cell in rule["template_fit"]["params"].values():
        cell["source"] = {"kind": "code", "note": "explicit synthetic test input"}


def _assert_modern_package(files, path, rule_id):
    """Healthy control reaches real SMT report, producer bytes and gate."""
    manifest = json.loads(files["manifest.json"])
    report = json.loads(files["smt-report.json"])
    assert manifest["consistency"]["verdict"] == report["verdict"] == "sat"
    assert report["n_rules"] == report["n_translated"] == 1
    assert [row["rule"] for row in report["translations"]] == [rule_id]
    path.write_bytes(tp.write_zip(files))
    before = path.read_bytes()
    problems, counters = gate.check(path)
    assert problems == [], problems
    assert counters["landing_archive_only"] is False
    assert path.read_bytes() == before


def _legacy_11_archive(doc):
    """Historical receive-only bytes: the modern exporter cannot emit 1.1."""
    from cdpm import catalog_bridge
    doc = copy.deepcopy(doc)
    for rule in doc["rules"]:
        rule["applicability"] = tp.applicability_of(rule)
        rule["required_data"] = tp.required_data_of(rule)
    modes, objectives = tp.allowed_modes(doc, spec_version="1.1")
    files = {"model.json": json.dumps(doc, ensure_ascii=False).encode("utf-8"),
             "objective.json": b"{}"}
    manifest = {
        "spec_version": "1.1", "landing_compat": 1,
        "catalog": catalog_bridge.stamp(), "allowed_modes": modes,
        "objectives": objectives,
        "files": [{"name": name, "sha256": hashlib.sha256(body).hexdigest()}
                  for name, body in files.items()]}
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    return files


def _param(**over):
    p = {"name": "max_duty_duration", "value": 720,
         "unit": "duration_minutes", "as_written": "12:00",
         "relation": "max"}
    p.update(over)
    return p


def test_canon_minutes_owns_the_colon():
    assert m.canon_minutes("12:00") == 720
    assert m.canon_minutes("0:45") == 45
    assert m.canon_minutes("12.5") is None
    assert m.canon_minutes("12:60") is None
    # Словесные формы с ЯВНОЙ единицей (ревью 1.3).
    assert m.canon_minutes("12 часов") == 720
    assert m.canon_minutes("45 минут") == 45
    assert m.canon_minutes("7 суток") == 10080
    assert m.canon_minutes("11 часов 30 минут") == 690
    # Голое число не канонизируется: «12» могло быть и часами —
    # трактовка минутами инвертировала сверку (верное 720 краснело,
    # ослабленное ×60 проходило).
    assert m.canon_minutes("600") is None
    # Юникод-десятичные не пролезают следом.
    assert m.canon_minutes("١٢:00") is None


def test_validate_param_is_typeproof_and_failclosed():
    """Ревью 1.3: (а) нехешируемые name/qualifier — нарушение, не
    TypeError (гейт судит недоверенный пакет); (б) неразбираемый след
    минутной природы краснит запись, а не выключает сверку;
    (в) время суток ограничено сутками."""
    assert m.validate_param(_param(name=["crew_size"]), "HARD")
    assert m.validate_param(_param(unit={"u": 1}), "HARD")
    assert any("qualifier" in e for e in m.validate_param(
        _param(qualifier={"base": "X"}), "HARD"))
    doc = {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [{"id": "R-1", "title": "Т", "statement": "s",
                   "zone": "BOTH", "kind": "HARD",
                   "sources": [{"sid": "S1", "anchor": "п.1"}],
                   "params": [_param(qualifier={"base": "X"}),
                              _param(name=["crew_size"])]}],
        "questions": [], "objectives": [],
    }
    assert m.validate_model(doc)          # нарушения, не TypeError
    # (б) fail-closed сверки: «12 часов» при value 12 — ослабление
    # ×60, ловится словесной канонизацией; «полдень» — не
    # канонизируется и краснит сам по себе.
    errs = m.validate_param(_param(value=12, as_written="12 часов"),
                            "HARD")
    assert any("не сходится" in e for e in errs)
    assert m.validate_param(
        _param(value=720, as_written="12 часов"), "HARD") == []
    assert any("не канонизируется" in e for e in m.validate_param(
        _param(as_written="полдень"), "HARD"))
    assert any("не канонизируется" in e for e in m.validate_param(
        _param(value=12, as_written="12"), "HARD"))
    # (в) время суток вне суток.
    errs = m.validate_param(
        {"name": "latest_arrival", "value": 1500,
         "unit": "time_of_day_minutes", "as_written": "25:00",
         "relation": "max", "tz": "UTC"}, "HARD")
    assert any("0..1439" in e for e in errs)


def test_validate_param_catches_the_traps():
    assert m.validate_param(_param(), "HARD") == []
    # «12:00 → 12»: ослабление в шестьдесят раз ловится сверкой
    # канонизации против следа.
    errs = m.validate_param(_param(value=12), "HARD")
    assert any("канонизация не сходится" in e for e in errs)
    # Имя вне словаря; единица не по словарю; window — законное
    # значение relation; tz обязателен при time_of_day и запрещён
    # вне его.
    assert any("вне словаря" in e
               for e in m.validate_param(_param(name="своё"), "HARD"))
    assert any("не согласована" in e for e in m.validate_param(
        _param(unit="count"), "HARD"))
    assert m.validate_param(
        _param(name="weekly_rest_period", as_written="7 суток",
               value=10080, relation="window"), "HARD") == []
    errs = m.validate_param(
        _param(name="earliest_departure",
               unit="time_of_day_minutes", value=360,
               as_written="06:00"), "HARD")
    assert any("требует tz" in e for e in errs)
    assert m.validate_param(
        _param(name="earliest_departure",
               unit="time_of_day_minutes", value=360,
               as_written="06:00", tz="base-local"), "HARD") == []
    assert any("tz допустим только" in e for e in m.validate_param(
        _param(tz="UTC"), "HARD"))
    # Типоустойчивость: мусор — нарушения, не падение.
    assert m.validate_param("мусор", "HARD")
    assert any("не число" in e for e in m.validate_param(
        _param(value=True), "HARD"))


def test_extractor_keeps_clean_params_drops_foreign_with_trace():
    asm = extract._Assembler(
        "Т", [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}])
    asm.add_candidate("S1", {
        "anchor": "п.1", "title": "Смена",
        "statement": "смена ≤ 12:00", "zone": "BOTH",
        "kind": "HARD",
        "params": [_param(),
                   _param(name="выдумка", as_written="5")]})
    rule = asm.doc["rules"][0]
    assert rule["params"] == [_param()]
    assert "параметр отброшен" in rule["note"]
    assert m.validate_model(asm.doc) == []


def test_manifest_crew_size_derived_from_params(tmp_path, package_translator):
    doc = {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [
            {"id": "R-CS", "title": "Состав",
             "statement": "какая угодно формулировка состава",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}],
             "params": [{"name": "crew_size", "value": 2,
                         "unit": "count", "as_written": "2",
                         "relation": "eq"}]},
        ],
        "questions": [], "objectives": [],
    }
    assert m.validate_model(doc) == []
    # Производный источник главнее текста: формулировка нечитаемая,
    # число из параметра.
    assert tp.crew_size_of(doc) == {"value": 2, "rule": "R-CS"}
    run = tmp_path / "run-9001"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    # This old fixture is archive-only, never a modern execution grant.
    with pytest.raises(tp.TaskPackageError) as refused:
        tp.build_package(run)
    assert refused.value.code == "landing-version"
    _modern_crew_fit(doc["rules"][0])
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    original = (run / "extract" / "model.json").read_bytes()
    files = tp.build_package(run)
    assert (run / "extract" / "model.json").read_bytes() == original
    manifest = json.loads(files["manifest.json"])
    assert manifest["spec_version"] == tp.SPEC_VERSION
    assert manifest["crew_size"] == {"value": 2, "rule": "R-CS"}
    pkg = tmp_path / "p.zip"
    _assert_modern_package(files, pkg, "R-CS")
    assert json.loads(files["model.json"])["rules"][0]["params"][0]["value"] == 2
    # Подделка манифеста расходится с производным — красный гейт.
    manifest["crew_size"] = {"value": 3, "rule": "R-CS"}
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    pkg2 = tmp_path / "p2.zip"
    pkg2.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg2)
    assert any("производному" in p for p in problems)


def test_gate_judges_package_by_declared_spec_version(tmp_path):
    """Эталоны ранних версий (1.0/1.1) манифестного crew_size не
    обещали — гейт не вправе требовать поле задним числом (живой
    красный check_handoff на run-0025 2026-08-22). Присутствующее
    поле сверяется при любой версии."""
    doc = {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [
            {"id": "R-CS", "title": "Состав",
             "statement": "экипаж каждого рейса — 2 пилота",
             "zone": "BOTH", "kind": "HARD",
             "sources": [{"sid": "S1", "anchor": "п.1"}]},
        ],
        "questions": [], "objectives": [],
    }
    run = tmp_path / "run-9002"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(tp.TaskPackageError) as refused:
        tp.build_package(run)
    assert refused.value.code == "landing-version"
    files = _legacy_11_archive(doc)
    manifest = json.loads(files["manifest.json"])
    assert manifest["spec_version"] == "1.1"
    assert "crew_size" not in manifest
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    pkg = tmp_path / "old.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, counters = gate.check(pkg, landing_compat=1)
    assert problems == [], problems
    assert counters["landing_archive_only"] is True
    assert any("landing-version" in p for p in gate.check(pkg)[0])
    # Reader consent alone must not turn an unmarked package into an archive.
    manifest.pop("landing_compat")
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    pkg.write_bytes(tp.write_zip(files))
    assert any("landing-version" in p for p in gate.check(pkg, landing_compat=1)[0])
    manifest["landing_compat"] = 1
    # Подложное поле у старой версии всё равно сверяется.
    manifest["crew_size"] = {"value": 3, "rule": "R-CS"}
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    pkg2 = tmp_path / "old2.zip"
    pkg2.write_bytes(tp.write_zip(files))
    assert any("производному" in p for p in gate.check(pkg2, landing_compat=1)[0])


def test_duplicate_param_name_in_rule_is_violation():
    doc = {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [{"id": "R-1", "title": "Смена",
                   "statement": "s", "zone": "BOTH", "kind": "HARD",
                   "sources": [{"sid": "S1", "anchor": "п.1"}],
                   "params": [_param(), _param(value=720)]}],
        "questions": [], "objectives": [],
    }
    assert any("дважды" in p for p in m.validate_model(doc))


def _rule(rid="R-1", kind="HARD", statement="s", params=None, **over):
    r = {"id": rid, "title": "Т", "statement": statement,
         "zone": "BOTH", "kind": kind,
         "sources": [{"sid": "S1", "anchor": "п.1"}]}
    if params is not None:
        r["params"] = params
    r.update(over)
    return r


def _doc(*rules, questions=None):
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": list(rules),
        "questions": questions or [], "objectives": [],
    }


def _cs(v, q=None, rel="eq"):
    p = {"name": "crew_size", "value": v, "unit": "count",
         "as_written": str(v), "relation": rel}
    if q:
        p["qualifier"] = q
    return p


def test_crew_size_of_boundaries():
    """Ревью 1.3: конфликт → None (не угадываем), OUT/SOFT не кормят
    манифест, qualifier/не-eq — не глобальная численность, текст —
    перекрёстная сверка, params не-список не роняет."""
    # Конфликт двух HARD — None, порядок не решает.
    assert tp.crew_size_of(_doc(_rule("R-A", params=[_cs(2)]),
                                _rule("R-B", params=[_cs(3)]))) is None
    # OUT игнорируется: побеждает действующий текст.
    got = tp.crew_size_of(_doc(
        _rule("R-OUT", kind="OUT", params=[_cs(5)]),
        _rule("R-H", statement="экипаж каждого рейса — 2 пилота")))
    assert got == {"value": 2, "rule": "R-H"}
    # Контекстные записи (qualifier) и границы (не eq) — не источник.
    assert tp.crew_size_of(_doc(_rule("R-Q", params=[
        _cs(2, q="A320"), _cs(3, q="B767")]))) is None
    assert tp.crew_size_of(_doc(_rule("R-M", params=[
        _cs(4, rel="max")]))) is None
    # Params против текста другого правила — конфликт.
    assert tp.crew_size_of(_doc(
        _rule("R-P", params=[_cs(2)]),
        _rule("R-T", statement="экипаж — 3 пилота"))) is None
    # Согласие источников: params первее как имя источника.
    got = tp.crew_size_of(_doc(
        _rule("R-P", params=[_cs(2)]),
        _rule("R-T", statement="экипаж — 2 пилота")))
    assert got == {"value": 2, "rule": "R-P"}
    # Мусорный params не роняет производителя.
    assert tp.crew_size_of(_doc(_rule("R-X", params=123))) is None


def _fit_cs(v, condition=None, category="A", relation=None):
    p = {"value": v, "quote": "Экипаж смены — ровно 2 пилота"}
    if condition:
        p["condition"] = condition
    if relation:
        p["relation"] = relation
    return {"category": category, "registry_rev": "rev-test",
            "family": "crew_composition", "params": {"crew_size": p}}


def test_crew_size_from_template_fit_params():
    """Ревизия 1.19 (отказ потребителя по пакету run-0114, 09.09.2026):
    численность места читается из параметра посадки crew_size семьи
    crew_composition — носитель, а не формулировка, которую модель
    переписывает между чтениями («crew_size(смена) = 2; должности …»
    не брал ни один текстовый образец, поле молча отсутствовало)."""
    unreadable = ("crew_size(смена) = 2; должности КВС и Второй пилот "
                  "при комплектовании не различаются")
    doc = _doc(_rule("R-CS", statement=unreadable,
                     template_fit=_fit_cs(2)))
    assert tp._crew_size_from_text(doc) is None      # текст молчит
    assert tp.crew_size_of(doc) == {"value": 2, "rule": "R-CS"}
    assert tp.crew_size_problem(doc) is None
    # Число строкой — то же число.
    doc = _doc(_rule("R-CS", statement=unreadable,
                     template_fit=_fit_cs("2")))
    assert tp.crew_size_of(doc) == {"value": 2, "rule": "R-CS"}
    # Категория B (допущение с вопросом) — носитель тоже; C — нет.
    doc = _doc(_rule("R-CS", statement=unreadable,
                     template_fit=_fit_cs(2, category="B")))
    assert tp.crew_size_of(doc) == {"value": 2, "rule": "R-CS"}
    doc = _doc(_rule("R-CS", statement=unreadable,
                     template_fit=_fit_cs(2, category="C")))
    assert tp.crew_size_of(doc) is None
    assert tp.crew_size_problem(doc) is None         # посадки нет — 1.12
    # Условие применения — вариант состава, не численность места;
    # отношение «не равно» — граница, не численность.
    doc = _doc(_rule("R-EXT", statement=unreadable,
                     template_fit=_fit_cs(3, condition="расширенный")),
               _rule("R-CS", statement=unreadable,
                     template_fit=_fit_cs(2)))
    assert tp.crew_size_of(doc) == {"value": 2, "rule": "R-CS"}
    doc = _doc(_rule("R-M", statement=unreadable,
                     template_fit=_fit_cs(4, relation="max")))
    assert tp.crew_size_of(doc) is None
    # Согласие носителей: посадка первее как имя источника.
    doc = _doc(_rule("R-F", statement=unreadable, template_fit=_fit_cs(2)),
               _rule("R-P", params=[_cs(2)]),
               _rule("R-T", statement="экипаж — 2 пилота"))
    assert tp.crew_size_of(doc) == {"value": 2, "rule": "R-F"}
    # Расхождение носителей — конфликт с названными источниками.
    doc = _doc(_rule("R-F", statement=unreadable, template_fit=_fit_cs(2)),
               _rule("R-T", statement="экипаж — 3 пилота"))
    assert tp.crew_size_of(doc) is None
    why = tp.crew_size_problem(doc)
    assert "расходятся" in why and "2 (R-F)" in why and "3 (R-T)" in why


def test_package_refuses_when_crew_size_not_derivable(tmp_path, package_translator):
    """Fail-closed 1.19: посадка на семью состава есть, численность не
    выводится — пакет не собирается с названной причиной (прежде
    уезжал без поля, отказывал потребитель на кнопке). Без посадки на
    семью состава отсутствие ключа по-прежнему законно (1.12)."""
    unreadable = "crew_size(смена) = 2; должности не различаются"

    def _build(doc, name):
        run = tmp_path / name
        (run / "extract").mkdir(parents=True)
        (run / "extract" / "model.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        original = (run / "extract" / "model.json").read_bytes()
        try:
            return tp.build_package(run)
        finally:
            assert (run / "extract" / "model.json").read_bytes() == original

    # Живой случай run-0114: текст нечитаем, число — из посадки.
    crew_rule = _rule("R-CS", statement=unreadable)
    _modern_crew_fit(crew_rule)
    files = _build(_doc(crew_rule), "run-9101")
    assert json.loads(files["manifest.json"])["crew_size"] == \
        {"value": 2, "rule": "R-CS"}
    _assert_modern_package(files, tmp_path / "derived-fit.zip", "R-CS")
    # Конфликт носителей при посадке — отказ словами.
    with pytest.raises(tp.TaskPackageError) as ei:
        _build(_doc(copy.deepcopy(crew_rule),
                    _rule("R-T", statement="экипаж — 3 пилота")), "run-9102")
    assert "численность экипажа не выводится" in str(ei.value)
    # Посадка есть, но число не читается (только условный вариант).
    with pytest.raises(tp.TaskPackageError) as ei:
        conditional = _rule("R-EXT", statement=unreadable)
        _modern_crew_fit(conditional, 3, {"kind": "crew_variant", "value": "расширенный"})
        _build(_doc(conditional), "run-9103")
    assert "не читается" in str(ei.value)
    # Без шаблона HARD теперь не исполним: это не здоровая modern-фикстура.
    from cdpm import catalog_bridge
    no_crew = _rule("R-X", statement="какая угодно формулировка",
                   template_fit={"schema_version": "landing/2", "category": "C",
                                 "gap": "не удалось выбрать семью",
                                 "registry_rev": catalog_bridge.catalog().fingerprint()})
    with pytest.raises(tp.TaskPackageError, match="пробел покрытия"):
        _build(_doc(no_crew), "run-9104-gap")
    # Без семьи СОСТАВА ключ по-прежнему отсутствует. Иная корректная
    # modern-семья сохраняет реальный HARD producer→gate путь.
    no_crew["statement"] = "Рабочая смена не более 720 минут."
    _modern_fit(no_crew, "duty_limits", {"max_duty_duration": {
        "value": 720, "unit": "duration_minutes", "relation": "max",
        "condition": None, "quote": "720 минут"}})
    files = _build(_doc(no_crew), "run-9104")
    assert "crew_size" not in json.loads(files["manifest.json"])
    _assert_modern_package(files, tmp_path / "without-crew-family.zip", "R-X")


def test_worker_patch_does_not_erase_machine_fields():
    """Ревью 1.3: патч обработчика, не повторивший params/weight,
    наследует их — машинные поля не исчезают молча."""
    from cdpm import answers
    q = {"id": "Q-1", "part": "2", "title": "Т", "text": "т?",
         "answer_format": {"type": "choice", "options": [
             {"key": "а", "text": "смена ≤ 12:00"},
             {"key": "б", "text": "смена ≤ 11:30"}]},
         "rule_refs": ["R-1"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}],
         "answer": {"raw": "вариант а", "key": "а",
                    "status": "ожидает обработчика", "revision": 2}}
    doc = _doc(_rule("R-1", params=[_param()], blockers=["Q-1"]),
               questions=[q])
    doc["meta"]["revision"] = 2
    assert m.validate_model(doc) == []
    patch_rule = {"id": "R-1", "title": "Т",
                  "statement": "подтверждено: смена ≤ 12:00",
                  "zone": "BOTH", "kind": "HARD",
                  "sources": [{"sid": "S1", "anchor": "п.1"}]}
    reply = json.dumps({"patches": [
        {"question": "Q-1", "rules": [patch_rule]}]},
        ensure_ascii=False)
    summary = answers.apply_worker_patches(doc, lambda _: reply)
    assert summary["worker_applied"] == 1, summary
    rule = doc["rules"][0]
    assert rule["params"] == [_param()], "params стёрты патчем"


def test_extractor_dedupes_param_keys_with_trace():
    """Ревью 1.3: повтор ключа (имя, уточнитель) отбрасывается со
    следом при извлечении — а не убивает весь прогон на финальном
    validate_model."""
    asm = extract._Assembler(
        "Т", [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}])
    asm.add_candidate("S1", {
        "anchor": "п.1", "title": "Отдых",
        "statement": "отдых ≥ 10:00, ошибочный повтор 12:00",
        "zone": "BOTH", "kind": "HARD",
        "params": [
            {"name": "min_rest_home", "value": 600,
             "unit": "duration_minutes", "as_written": "10:00",
             "relation": "min"},
            {"name": "min_rest_home", "value": 600,
             "unit": "duration_minutes", "as_written": "10:00",
             "relation": "min"},
            {"name": "min_rest_home", "value": 720,
             "unit": "duration_minutes", "as_written": "12:00",
             "relation": "min"},
        ]})
    rule = asm.doc["rules"][0]
    assert len(rule["params"]) == 1
    assert rule["params"][0]["value"] == 600
    assert "дважды" in rule.get("note", "")
    assert m.validate_model(asm.doc) == []


def test_gate_rejects_unparseable_minor_version(tmp_path, package_translator):
    """Ревью 1.3: «1.x»/«1» при известном major — красный, а не тихий
    перевод пакета в «до-1.2» с отключением контракта crew_size."""
    doc = _doc(_rule("R-CS", params=[_cs(2)]))
    _modern_crew_fit(doc["rules"][0])
    run = tmp_path / "run-9003"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    original = (run / "extract" / "model.json").read_bytes()
    files = tp.build_package(run)
    assert (run / "extract" / "model.json").read_bytes() == original
    healthy = tmp_path / "healthy-version.zip"
    _assert_modern_package(files, healthy, "R-CS")
    for bad in ("1.x", "1", "1."):
        manifest = json.loads(files["manifest.json"])
        manifest["spec_version"] = bad
        manifest.pop("crew_size", None)
        files["manifest.json"] = json.dumps(
            manifest, ensure_ascii=False).encode("utf-8")
        pkg = tmp_path / f"bad-{bad.replace('.', '_')}.zip"
        pkg.write_bytes(tp.write_zip(files))
        problems, _ = gate.check(pkg)
        assert any("неразбираемая версия" in p for p in problems), bad


def test_unsigned_model_json_is_red(tmp_path, package_translator):
    """Потребитель (транслятор 5b0bdd2) читает model.json только из
    подписанного перечня files — гейт производителя обязан краснить
    пакет, где model.json есть, но в перечне не назван."""
    doc = _doc(_rule("R-CS", params=[_cs(2)]))
    _modern_crew_fit(doc["rules"][0])
    run = tmp_path / "run-9004"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    original = (run / "extract" / "model.json").read_bytes()
    files = tp.build_package(run)
    assert (run / "extract" / "model.json").read_bytes() == original
    healthy = tmp_path / "signed.zip"
    _assert_modern_package(files, healthy, "R-CS")
    manifest = json.loads(files["manifest.json"])
    manifest["files"] = [f for f in manifest["files"]
                         if f.get("name") != "model.json"]
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False).encode("utf-8")
    pkg = tmp_path / "unsigned.zip"
    pkg.write_bytes(tp.write_zip(files))
    problems, _ = gate.check(pkg)
    assert any("не назван" in p for p in problems), problems


def test_multi_entry_param_needs_qualifier():
    """Норма резерва по базам: несколько записей одного имени законны
    с разными уточнителями и красны без них (живой отказ извлечения
    2026-08-22 — гейт поймал упущение дизайна)."""
    def rec(q=None, v=2):
        p = {"name": "reserve_crews", "value": v, "unit": "count",
             "as_written": str(v), "relation": "eq"}
        if q:
            p["qualifier"] = q
        return p
    doc = {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md",
                     "sha256": "0" * 64}],
        "rules": [{"id": "R-R", "title": "Резерв",
                   "statement": "PLY-2, SVG-1, GRT-1",
                   "zone": "ROSTERING", "kind": "HARD",
                   "sources": [{"sid": "S1", "anchor": "п.1"}],
                   "params": [rec("PLY", 2), rec("SVG", 1),
                              rec("GRT", 1)]}],
        "questions": [], "objectives": [],
    }
    assert m.validate_model(doc) == []
    doc["rules"][0]["params"] = [rec(), rec(v=1)]
    assert any("без различающего уточнителя" in p
               for p in m.validate_model(doc))


def test_area_qualifier_closed_dictionary():
    """Ревизия 1.4: уточнитель ОБЛАСТИ у min_turnaround — закрытый
    словарь «база»/«вне базы» (свободный текст ложился у решателя в
    «не применено» молча); синонимы законны и канонизируются
    извлечением; словарь НЕ трогает контекстные уточнители других
    имён (резерв по базам)."""
    def tp_(q):
        return {"name": "min_turnaround", "value": 45,
                "unit": "duration_minutes", "as_written": "45 минут",
                "relation": "min", "qualifier": q}
    assert m.validate_param(tp_("база"), "HARD") == []
    assert m.validate_param(tp_("в базе"), "HARD") == []
    assert any("вне закрытого словаря" in e
               for e in m.validate_param(tp_("ТАС-base"), "HARD"))
    # Контекстный уточнитель другого имени свободен как был.
    assert m.validate_param(
        {"name": "reserve_crews", "value": 2, "unit": "count",
         "as_written": "2", "relation": "eq",
         "qualifier": "PLY"}, "HARD") == []
    # Извлечение канонизирует синоним.
    asm = extract._Assembler(
        "Т", [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}])
    asm.add_candidate("S1", {
        "anchor": "п.9", "title": "Стыковка",
        "statement": "стыковка в базе ≥ 45 минут", "zone": "BOTH",
        "kind": "HARD", "params": [tp_("на базе")]})
    assert asm.doc["rules"][0]["params"][0]["qualifier"] == "база"


def test_worker_patch_foreign_param_dropped_with_trace():
    """Живой Лайт run-0040: обработчик выдумал имя параметра
    ('min_rest_away') и единицу ('minutes') — отказ топил весь патч и
    строка оставалась заблокированной. Чужая запись отбрасывается со
    следом (как при извлечении), чистая — применяется."""
    from cdpm import answers
    q = {"id": "Q-1", "part": "2", "title": "Т", "text": "т?",
         "answer_format": {"type": "choice", "options": [
             {"key": "а", "text": "вариант первый"},
             {"key": "б", "text": "вариант второй"}]},
         "rule_refs": ["R-1"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}],
         "answer": {"raw": "а", "key": "а",
                    "status": "ожидает обработчика", "revision": 2}}
    doc = _doc(_rule("R-1", blockers=["Q-1"]), questions=[q])
    doc["meta"]["revision"] = 2
    patch_rule = {"id": "R-1", "title": "Т",
                  "statement": "отдых при задержке ≥ 10:00",
                  "zone": "BOTH", "kind": "HARD",
                  "sources": [{"sid": "S1", "anchor": "п.1"}],
                  "params": [
                      {"name": "min_rest_away", "value": 600,
                       "unit": "minutes", "as_written": "10:00",
                       "relation": "min"},
                      {"name": "min_rest_outstation", "value": 600,
                       "unit": "duration_minutes",
                       "as_written": "10:00", "relation": "min"}]}
    reply = json.dumps({"patches": [
        {"question": "Q-1", "rules": [patch_rule]}]},
        ensure_ascii=False)
    summary = answers.apply_worker_patches(doc, lambda _: reply)
    assert summary["worker_applied"] == 1, summary
    rule = doc["rules"][0]
    assert rule["params"] == [
        {"name": "min_rest_outstation", "value": 600,
         "unit": "duration_minutes", "as_written": "10:00",
         "relation": "min"}]
    assert "параметр отброшен" in rule.get("note", "")
    assert not rule.get("blockers")


def test_logic_block_contract():
    """Ревизия 1.5 (ступень 3): блок logic валидируется в модели —
    имена из словарей, числа только ссылками на params, след у
    каждого атома, единица «подряд» явная; патч обработчика без
    непустого logic не стирает его."""
    logic = {"subject": "duty",
             "condition": [{"predicate": "longer_than",
                            "param": "max_duty_duration",
                            "as_written": "смена свыше 12:00"}],
             "demand": {"kind": "exists_person", "subject": "rest",
                        "as_written": "предоставляется отдых"},
             "source_params": ["max_duty_duration"]}
    doc = _doc(_rule("R-L", params=[_param()], logic=logic))
    assert m.validate_model(doc) == []
    assert m.validate_schema(doc) == []
    # Чужое имя, голое число, отсутствие следа — нарушения.
    bad = dict(logic, subject="чужой",
               condition=[{"predicate": "consecutive",
                           "unit": "week", "param": "нет"}])
    doc2 = _doc(_rule("R-L", params=[_param()], logic=bad))
    probs = m.validate_model(doc2)
    assert any("вне словаря" in x for x in probs)
    assert any("единица обязана быть явной" in x for x in probs)
    assert any("такого params у правила нет" in x for x in probs)
    assert any("без следа" in x for x in probs)
    # Наследование патчем (как params/weight).
    from cdpm import answers
    q = {"id": "Q-1", "part": "2", "title": "Т", "text": "т?",
         "answer_format": {"type": "choice", "options": [
             {"key": "а", "text": "первое"},
             {"key": "б", "text": "второе"}]},
         "rule_refs": ["R-L"],
         "source_refs": [{"sid": "S1", "anchor": "п.1"}],
         "answer": {"raw": "а", "key": "а",
                    "status": "ожидает обработчика", "revision": 2}}
    doc3 = _doc(_rule("R-L", params=[_param()], logic=logic,
                      blockers=["Q-1"]), questions=[q])
    doc3["meta"]["revision"] = 2
    patch_rule = {"id": "R-L", "title": "Т",
                  "statement": "уточнено: смена ≤ 12:00",
                  "zone": "BOTH", "kind": "HARD",
                  "sources": [{"sid": "S1", "anchor": "п.1"}]}
    reply = json.dumps({"patches": [
        {"question": "Q-1", "rules": [patch_rule]}]},
        ensure_ascii=False)
    summary = answers.apply_worker_patches(doc3, lambda _: reply)
    assert summary["worker_applied"] == 1, summary
    assert doc3["rules"][0]["logic"] == logic, "logic стёрт патчем"


def test_extractor_keeps_clean_logic_drops_dirty_with_trace():
    """9б-2: извлечение производит logic — чистый блок принимается,
    грязный отброшен ЦЕЛИКОМ со следом (половина логики опаснее её
    отсутствия: правило остаётся текстом для сертификатора)."""
    good = {"subject": "duty",
            "source_params": ["max_duty_duration"],
            "demand": {"kind": "duration_within", "relation": "max",
                       "param": "max_duty_duration",
                       "measure": "span",
                       "as_written": "смена ≤ 12:00"}}
    asm = extract._Assembler(
        "Т", [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}])
    asm.add_candidate("S1", {
        "anchor": "п.1", "title": "Смена",
        "statement": "смена ≤ 12:00", "zone": "BOTH", "kind": "HARD",
        "params": [_param()], "logic": good})
    assert asm.doc["rules"][0]["logic"] == good
    asm.add_candidate("S1", {
        "anchor": "п.2", "title": "Чужая",
        "statement": "т", "zone": "BOTH", "kind": "HARD",
        "params": [_param()],
        "logic": {"subject": "выдумка",
                  "demand": {"kind": "duration_within",
                             "relation": "max",
                             "param": "max_duty_duration",
                             "as_written": "т"}}})
    r2 = asm.doc["rules"][1]
    assert "logic" not in r2
    assert "логика отброшена" in r2.get("note", "")
    assert m.validate_model(asm.doc) == []


def test_render_logic_deterministic_retell():
    """9б-2, рубеж 1: обратный пересказ — детерминированное русское
    предложение с числами из params (минуты — «чч:мм»)."""
    rule = {"params": [_param(), {"name": "max_landings_per_duty",
                                  "value": 3, "unit": "count",
                                  "as_written": "3",
                                  "relation": "max"}]}
    logic = {"subject": "duty",
             "condition": [
                 {"predicate": "longer_than",
                  "param": "max_duty_duration",
                  "as_written": "свыше 12:00"},
                 {"predicate": "at_outstation",
                  "as_written": "вне базы"}],
             "demand": {"kind": "count_within", "relation": "max",
                        "param": "max_landings_per_duty",
                        "as_written": "не более 3 посадок"},
             "enforce": "post-check"}
    text = m.render_logic(logic, rule)
    assert "полётная смена" in text
    assert "длится больше 12:00" in text
    assert "вне базы приписки" in text
    assert "не более 3" in text
    assert "сертификатором" in text
    assert m.render_logic(logic, rule) == text   # детерминизм


def test_param_conflict_becomes_part1_question():
    """Хвост Лайта 15: два незаблокированных правила с одним
    (имя, уточнитель) и РАЗНЫМИ числами — вопрос части 1 с
    развилкой значений, ОБЕ строки заблокированы; одинаковые числа
    и отменённые (OUT) конфликтом не считаются."""
    doc = _doc(
        _rule("R-A", params=[_param()]),                    # 720
        _rule("R-B", kind="DATA",
              params=[_param(value=750, as_written="12:30")],
              sources=[{"sid": "S1", "anchor": "п.9"}]),
        _rule("R-C", kind="OUT",
              params=[_param(value=999, as_written="16:39")]),
        _rule("R-D", params=[{"name": "crew_size", "value": 2,
                              "unit": "count", "as_written": "2",
                              "relation": "eq"}]),
        _rule("R-E", params=[{"name": "crew_size", "value": 2,
                              "unit": "count", "as_written": "2",
                              "relation": "eq"}]))
    created = m.ensure_param_conflict_questions(doc)
    assert len(created) == 1
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    assert q["part"] == "1"
    assert set(q["rule_refs"]) == {"R-A", "R-B"}
    keys = {o["key"] for o in q["answer_format"]["options"]}
    assert len(keys) == 2
    ra = next(r for r in doc["rules"] if r["id"] == "R-A")
    rb = next(r for r in doc["rules"] if r["id"] == "R-B")
    assert created[0] in ra["blockers"] and created[0] in rb["blockers"]
    rc = next(r for r in doc["rules"] if r["id"] == "R-C")
    assert not rc.get("blockers")
    assert m.validate_model(doc) == []
    # Повторный вызов не плодит дублей.
    assert m.ensure_param_conflict_questions(doc) == []


def test_data_scope_finding_becomes_question():
    """Хвост Лайта 16: значение данных вне закрытого перечня свода
    (ARH) — вопрос части 3 БЕЗ блокировки правила; повторный вызов
    дублей не плодит; часть — по зоне правила."""
    doc = _doc(_rule("R-P", kind="DATA",
                     statement="База_приписки ∈ {SVG, PLY, GRT}"))
    report = {"checks": [
        {"id": "C-1", "template": "перечень-значений",
         "basis": {"rule": "R-P"}, "status": "прогнана"}],
        "findings": [
            {"check": "C-1",
             "text": "значение «ARH» вне перечня ['GRT','PLY','SVG']",
             "target": {"sheet": "Экипажи", "row": 4}},
            {"check": "C-1",
             "text": "значение «ARH» вне перечня ['GRT','PLY','SVG']",
             "target": {"sheet": "Экипажи", "row": 9}}]}
    created = m.ensure_data_scope_questions(doc, report)
    assert len(created) == 1
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    assert q["part"] == "3b" and "ARH" in q["text"]
    assert q["rule_refs"] == ["R-P"]
    rp = next(r for r in doc["rules"] if r["id"] == "R-P")
    assert not rp.get("blockers"), "перечень верен до ответа"
    assert m.validate_model(doc) == []
    assert m.ensure_data_scope_questions(doc, report) == []


def test_logic16_param_ref_object_for_multi_entries():
    """Ревизия 1.6, пункт 1: имя с несколькими записями params
    (резерв по базам) строкой — красный с перечнем уточнителей;
    объект {name, qualifier} — законная ссылка; чужой уточнитель —
    красный."""
    params = [
        {"name": "min_turnaround", "value": 180,
         "unit": "duration_minutes", "as_written": "3:00",
         "relation": "min", "qualifier": "база"},
        {"name": "min_turnaround", "value": 420,
         "unit": "duration_minutes", "as_written": "7:00",
         "relation": "min", "qualifier": "вне базы"}]
    demand = {"kind": "duration_within", "relation": "min",
              "param": {"name": "min_turnaround",
                        "qualifier": "вне базы"},
              "measure": "span",
              "as_written": "стыковка вне базы не короче 7:00"}
    logic = {"subject": "sit",
             "source_params": [{"name": "min_turnaround",
                                "qualifier": "вне базы"}],
             "demand": dict(demand)}
    doc = _doc(_rule("R-16A", params=params, logic=logic))
    assert m.validate_model(doc) == []
    # Строка-имя при двух записях — красный, перечень уточнителей.
    bad = {"subject": "sit",
           "demand": dict(demand, param="min_turnaround")}
    probs = m.validate_model(
        _doc(_rule("R-16A", params=params, logic=bad)))
    assert any("обязана быть объектом" in x for x in probs), probs
    assert any("база" in x and "вне базы" in x for x in probs), probs
    # Чужой уточнитель в объекте — красный.
    bad2 = {"subject": "sit",
            "demand": dict(
                demand,
                param={"name": "min_turnaround",
                       "qualifier": "чужая-область"})}
    probs2 = m.validate_model(
        _doc(_rule("R-16A", params=params, logic=bad2)))
    assert any("уточнитель" in x and "не найден" in x
               for x in probs2), probs2


def test_logic16_duration_measure_mandatory():
    """Ревизия 1.6, пункт 2: duration_within без measure — красный
    (умолчания нет: «налёт размахом» = класс «голое 12»); чужое
    значение меры — красный."""
    base = {"subject": "duty",
            "demand": {"kind": "duration_within", "relation": "max",
                       "param": "max_duty_duration",
                       "as_written": "смена ≤ 12:00"}}
    probs = m.validate_model(
        _doc(_rule("R-16B", params=[_param()], logic=base)))
    assert any("нет аргумента 'measure'" in x for x in probs), probs
    bad = {"subject": "duty",
           "demand": dict(base["demand"], measure="хронометраж")}
    probs2 = m.validate_model(
        _doc(_rule("R-16B", params=[_param()], logic=bad)))
    assert any("measure" in x and "вне перечня" in x
               for x in probs2), probs2


def test_logic16_number_without_param_and_universe_dict():
    """Ревизия 1.6, пункты 3–4: число в следе exists_*/not_exists
    без ссылки param — красный («число ниоткуда»); universe у
    count_within — только из закрытого словаря."""
    num = {"subject": "reserve_duty",
           "demand": {"kind": "exists_universe",
                      "subject": "reserve_duty",
                      "universe": "base\u00d7day",
                      "as_written": "не менее 8 в резерве"}}
    probs = m.validate_model(
        _doc(_rule("R-16C", params=[_param()], logic=num)))
    assert any("число в следе" in x for x in probs), probs
    alien = {"subject": "duty",
             "demand": {"kind": "count_within", "relation": "max",
                        "param": "max_duty_duration",
                        "universe": "экипаж\u00d7неделя",
                        "as_written": "т"}}
    probs2 = m.validate_model(
        _doc(_rule("R-16C", params=[_param()], logic=alien)))
    assert any("universe" in x and "вне словаря" in x
               for x in probs2), probs2


def test_logic16_window_is_node():
    """Ревизия 1.6, пункт 5: окно in_window — узел {kind: month}
    либо {kind: rolling, unit, param}; строка — красный; rolling
    с чужой единицей или без ссылки — красный."""
    def _l(window):
        return {"subject": "day_off",
                "source_params": ["min_days_off_in_window"] + (
                    ["days_off_window_days"] if isinstance(window, dict)
                    and window.get("kind") == "rolling" else []),
                "condition": [{"predicate": "in_window",
                               "window": window,
                               "as_written": "за месяц"}],
                "demand": {"kind": "count_within", "relation": "min",
                           "param": "min_days_off_in_window",
                           "as_written": "т"}}
    # Both count_within and rolling-window length require counts. The old
    # positive fixture incorrectly reused a duration for these two roles.
    params = [
        _param(name="min_days_off_in_window", value=2, unit="count",
               relation="min", as_written="2"),
        _param(name="days_off_window_days", value=7, unit="count",
               relation="window", as_written="7"),
    ]
    ok = _doc(_rule("R-16D", params=params,
                    logic=_l({"kind": "month"})))
    assert m.validate_model(ok) == []
    ok2 = _doc(_rule("R-16D", params=params,
                     logic=_l({"kind": "rolling", "unit": "day",
                               "param": "days_off_window_days"})))
    assert m.validate_model(ok2) == []
    probs = m.validate_model(
        _doc(_rule("R-16D", params=params, logic=_l("month"))))
    assert any("узел вида" in x for x in probs), probs
    probs2 = m.validate_model(
        _doc(_rule("R-16D", params=params,
                   logic=_l({"kind": "rolling", "unit": "week",
                             "param": "days_off_window_days"}))))
    assert any("window.unit" in x for x in probs2), probs2


@pytest.mark.parametrize("role", ["demand", "window"])
def test_logic16_duration_cannot_supply_a_count_role(role):
    logic = {"subject": "duty", "source_params": ["max_duty_duration"],
             "demand": {"kind": "count_within", "relation": "max",
                        "param": "max_duty_duration", "as_written": "т"}}
    params = [_param()]
    if role == "window":
        params.append(_param(name="max_landings_per_duty", value=3,
                             unit="count", as_written="3"))
        logic["source_params"].append("max_landings_per_duty")
        logic["demand"]["param"] = "max_landings_per_duty"
        logic["condition"] = [{"predicate": "in_window", "as_written": "т",
            "window": {"kind": "rolling", "unit": "day", "param": "max_duty_duration"}}]
    problems = m.validate_model(_doc(_rule("R-16-UNIT", params=params, logic=logic)))
    assert any(role in p and "duration_minutes" in p and "требуется 'count'" in p for p in problems), problems


def test_logic16_alignment_with_consumer_gate():
    """Выравнивание с гейтом 1 потребителя (дизайн 9в): производитель
    краснеет раньше решателя — source_params обязан совпадать с
    фактом ссылок в обе стороны; relation требования — только
    max/min/eq; чужой ключ блока и пустой scope — красные."""
    dem = {"kind": "duration_within", "relation": "max",
           "param": "max_duty_duration", "measure": "span",
           "as_written": "смена ≤ 12:00"}
    ok = {"subject": "duty",
          "source_params": ["max_duty_duration"],
          "demand": dict(dem)}
    doc = _doc(_rule("R-16E", params=[_param()], logic=ok))
    assert m.validate_model(doc) == []
    # Ссылка применена, а след источников молчит — красный.
    silent = {"subject": "duty", "demand": dict(dem)}
    probs = m.validate_model(
        _doc(_rule("R-16E", params=[_param()], logic=silent)))
    assert any("расходится с применёнными" in x for x in probs), probs
    # Лишнее имя в source_params — красный той же сверкой.
    extra = dict(ok, source_params=["max_duty_duration",
                                    "max_landings_per_duty"])
    r = _rule("R-16E", params=[
        _param(), {"name": "max_landings_per_duty", "value": 3,
                   "unit": "count", "as_written": "3",
                   "relation": "max"}], logic=extra)
    probs2 = m.validate_model(_doc(r))
    assert any("расходится с применёнными" in x
               for x in probs2), probs2
    # relation=window у требования незаконна (окно — узлом).
    wnd = dict(ok, demand=dict(dem, relation="window"))
    probs3 = m.validate_model(
        _doc(_rule("R-16E", params=[_param()], logic=wnd)))
    assert any("relation 'window' вне перечня" in x
               for x in probs3), probs3
    # Чужой ключ блока и нестроковый scope — красные.
    alien = dict(ok, priority=1)
    probs4 = m.validate_model(
        _doc(_rule("R-16E", params=[_param()], logic=alien)))
    assert any("чужой ключ" in x for x in probs4), probs4
    scoped = dict(ok, scope=5)
    probs5 = m.validate_model(
        _doc(_rule("R-16E", params=[_param()], logic=scoped)))
    assert any("scope" in x for x in probs5), probs5


def test_render_logic_speaks_universe():
    """12а/рубеж 1: пересказ обязан проговаривать универсум — сверщик
    честно краснил «не менее 8» без месячной области (run-0044)."""
    rule = {"params": [{"name": "min_days_off", "value": 8,
                        "unit": "count", "as_written": "8",
                        "relation": "min"}]}
    logic = {"subject": "day_off",
             "source_params": ["min_days_off"],
             "demand": {"kind": "count_within", "relation": "min",
                        "param": "min_days_off",
                        "universe": "employee×month",
                        "as_written": "не менее 8"}}
    text = m.render_logic(logic, rule)
    assert "не менее 8" in text
    assert "за календарный месяц" in text
    assert "пустая клетка" in text


def test_missing_data_questions_created_deduped_normalized():
    """Г-C4: проверка «не прогнана: нет колонки/листа» рождает вопрос
    части 3 по правилу (предоставить данные | post-check «нет
    данных»), без блокировки; повтор стадии данных не плодит дубль;
    скобочный вариант id правила сводится к тому же вопросу (живой
    дубль run-0044/0046)."""
    doc = _doc(_rule("R-CHK", params=[_param()]))
    report = {"checks": [
        {"id": "C-1", "status": "не прогнана",
         "reason": "на листе «Смены» нет колонки «Явка»",
         "basis": {"rule": "R-CHK"}},
        {"id": "C-2", "status": "не скомпилирована",
         "reason": "нет листа «Перегоны»",
         "basis": {"rule": "R-[CHK]"}},
        {"id": "C-3", "status": "прогнана",
         "basis": {"rule": "R-CHK"}},
    ], "findings": []}
    created = m.ensure_missing_data_questions(doc, report)
    assert len(created) == 1, created
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    assert q["part"] in ("3a", "3b")
    assert "нет колонки" in q["text"] and "нет листа" in q["text"]
    assert [o["key"] for o in q["answer_format"]["options"]]         == ["а", "б"]
    rule = doc["rules"][0]
    # Решение владельца 2026-09-02: до ответа «считать до получения
    # данных?» строка-носитель заблокирована (fail-closed).
    assert rule.get("blockers") == [q["id"]]
    assert "Считать ли до получения данных" in q["text"]
    assert m.validate_model(doc) == []
    # Повтор — дубль не создаётся.
    assert m.ensure_missing_data_questions(doc, report) == []


def test_правило_с_исполнителем_судится_решением_а_не_вопросом():
    """Плоды планирования (смены, назначения, сводка резерва) во
    ВХОДНОЙ книге не живут, и их отсутствие — не пробел данных.
    Правило, у которого есть исполнитель (посадка на семью базы
    шаблонов), вопроса «исключить до получения данных» не порождает:
    ответ «да» снял бы с расчёта действующее ограничение. Судит
    сертификат решения, а след пишется в отчёт (набор H0, 03.09)."""
    # Семья с исполнителем — по КАТАЛОГУ шаблонов через мост (шаг 7:
    # обёртки решателя `vmp.template_registry` CDPM не читает; закон —
    # виды исполнителей записи вне шаблона проверки данных).
    from cdpm import catalog_bridge as bridge
    fam = next(f for f in sorted(m.template_families())
               if bridge.family_has_executor(f))
    with_exec = _rule("R-ИСП", template_fit={
        "category": "A", "family": fam, "registry_rev": "x" * 12,
        "quote": "порог названа клаузой"})
    without = _rule("R-БЕЗ")
    doc = _doc(with_exec, without)
    report = {"checks": [
        {"id": "C-1", "status": "не прогнана", "basis": {"rule": "R-ИСП"},
         "reason": "компилятор: в наборе нет листа полётных смен"},
        {"id": "C-2", "status": "не прогнана", "basis": {"rule": "R-БЕЗ"},
         "reason": "компилятор: нет листа «Перегоны»"},
    ], "findings": []}
    created = m.ensure_missing_data_questions(doc, report)
    assert len(created) == 1, created
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    assert q["rule_refs"] == ["R-БЕЗ"]
    # Правило с исполнителем не заблокировано и названо в отчёте.
    assert not with_exec.get("blockers")
    assert [x["rule"] for x in report["judged_on_solution"]] == ["R-ИСП"]
    assert "сертификат" in report["judged_on_solution"][0]["note"]


def test_missing_data_questions_family_of_identical_texts():
    """Живой случай одной из линий: соседние ячейки
    вложенной таблицы — разные правила с ОДИНАКОВОЙ формулировкой;
    раньше рождались текстуальные дубли и контракт уникальности
    рубил модель. Семейство сводится к одному вопросу с перечнем
    правил-носителей; повтор стадии дубль не плодит."""
    doc = _doc(_rule("R-A8", params=[_param()]),
               _rule("R-A9", params=[_param()]),
               _rule("R-A10", params=[_param()]))
    for r in doc["rules"]:
        r["statement"] = "лимит по приложению 4"
    report = {"checks": [
        {"id": f"C-{i}", "status": "не прогнана",
         "reason": "на листе «Экипажи» нет колонки «Допуск»",
         "basis": {"rule": rid}}
        for i, rid in enumerate(("R-A8", "R-A9", "R-A10"))
    ], "findings": []}
    created = m.ensure_missing_data_questions(doc, report)
    assert len(created) == 1, created
    q = next(x for x in doc["questions"] if x["id"] == created[0])
    assert q["rule_refs"] == ["R-A10", "R-A8", "R-A9"] \
        or sorted(q["rule_refs"]) == ["R-A10", "R-A8", "R-A9"]
    assert "R-A8" in q["text"] and "R-A9" in q["text"]
    assert m.validate_model(doc) == []
    # Повтор стадии — дубль не создаётся.
    assert m.ensure_missing_data_questions(doc, report) == []
