# -*- coding: utf-8 -*-
"""Мост CDPM → каталог шаблонов, шаг 7 переезда (стадии 1а–1б): КАТАЛОГ
ОБЯЗАТЕЛЕН И ЕДИНСТВЕН. Словарь параметров, закрытые перечни, карта
семей, условия и лексикон читаются ТОЛЬКО из каталога через один
экземпляр на процесс; со стадии 1б у моста ОДНИ ЧИСТЫЕ ПРОЕКЦИИ —
имён `*_or` и аргументов «запаса из кода» больше нет, потому что нет и
самого запаса: без каталога отказ словами (`CatalogRequired`, код
«catalog-unavailable»), а импорт модулей при этом не падает.
"""
import json
import re
from pathlib import Path

import pytest

from cdpm import catalog_bridge as bridge
from cdpm import family_lexicon as lex
from cdpm import model as m
from cdpm import vmp_form as vf

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog"
HISTORY_WINDOW_NAMES = ("night_window_days", "max_night_duties_in_window",
                        "days_off_window_days", "min_days_off_in_window")


@pytest.fixture(autouse=True)
def _fresh_bridge(monkeypatch):
    monkeypatch.delenv(bridge.ENV_CATALOG, raising=False)
    monkeypatch.delenv("CREW_VMP_TEMPLATE_BASE", raising=False)
    bridge.forget()
    yield
    bridge.forget()


def _dictionaries() -> dict:
    return json.loads((CATALOG / "dictionaries.json").read_text(
        encoding="utf-8"))


def test_params_are_catalog_names_in_reader_order():
    """Множество имён PARAMS — параметры семей ∪ unbound_params без имён
    словаря решателя; порядок — ЧИТАТЕЛЯ (по имени), а не кода: один
    источник — один порядок (шаг 7)."""
    assert bridge.catalog_available()
    fam = json.loads((CATALOG / "families.json").read_text(encoding="utf-8"))
    dic = _dictionaries()
    cat_names = {p["name"] for f in fam["families"]
                 if f.get("status") == "есть" for p in f["params"]}
    unbound = {n for n, spec in dic["unbound_params"].items()
               if spec.get("source") != bridge.SOLVER_ONLY_SOURCE}
    assert set(m.PARAMS) == cat_names | unbound
    # Каталог 0.2.0 (draft.2): max_sit_duration и max_weekly_block розданы
    # семьям (connection_windows, span_accumulators) и входят в PARAMS
    # как длительности; fleet_type (единица code) сменил семью на
    # fleet_exclusions и в PARAMS остаётся, как и прежде. Имён «только
    # словарь решателя» в каталоге не осталось.
    assert m.PARAMS["max_sit_duration"] == "duration_minutes" \
        and m.PARAMS["max_weekly_block"] == "duration_minutes"
    assert m.PARAMS["fleet_type"] == "code"
    # Число имён — явным равенством ОТ КАТАЛОГА, а не литералом (просьба 1
    # извещения о паритете, 08.09): рост каталога (новый параметр записи
    # классом К-табл) не должен красить тест «по числу, а не по делу».
    # На выпуске 0.2.0 это 46.
    table = bridge.catalog().params()
    assert len(m.PARAMS) == len({
        n for n, s in table.items()
        if s.get("unit") and s.get("source") != bridge.SOLVER_ONLY_SOURCE})
    assert list(m.PARAMS) == sorted(m.PARAMS)
    for n in HISTORY_WINDOW_NAMES:
        assert m.PARAMS[n] == "count"
    assert all(u in m.PARAM_UNITS for u in m.PARAMS.values())
    # Снимок держится по экземпляру каталога: два обращения подряд
    # отдают ТОТ ЖЕ объект (пересборки словаря на вызов нет).
    assert m.PARAMS is m.PARAMS


def test_vocabularies_are_catalog_dictionaries():
    """Перечни равны словарям каталога — значением и порядком. Со
    стадии 1б сверять их не с чем: констант кода нет, и запрет на их
    возвращение держит гейт источника (`test_cdpm_catalog_source`)."""
    d = _dictionaries()
    assert m.PARAM_UNITS == tuple(d["param_units"])
    assert m.CODE_UNITS == tuple(d["code_units"])
    assert m.PARAM_RELATIONS == tuple(d["param_relations"])
    assert m.PARAM_TZ == tuple(d["param_tz"])
    assert m.AREA_QUALIFIERS == tuple(d["area_qualifiers"])
    assert m.FIT_CATEGORIES == tuple(d["fit_categories"])
    # area_param_names читатель ВЫЧИСЛЯЕТ (параметры с уточнителем area).
    assert m.AREA_PARAM_NAMES == ("min_turnaround",)
    # Природа — подмножество единиц: код, приняв code_list, обязан знать
    # его и как единицу значения (свидетель самосогласованности данных).
    assert set(m.CODE_UNITS) <= set(m.PARAM_UNITS)


def test_violation_units_are_codes_and_words_of_one_carrier(tmp_path,
                                                            monkeypatch):
    """Каталог 0.5.0 пишет единицы размера нарушения КАРТОЙ «код → слово».
    Контракт членства не меняется: `vocab` отдаёт КОДЫ в порядке записи;
    слова живут рядом с кодами и берутся ТОЛЬКО оттуда — второй карты
    перевода у CDPM нет. Словарь прежней формы (перечень без слов) —
    отказ словами, а не молчаливый показ кода."""
    units = _dictionaries()["violation_units"]
    assert isinstance(units, dict), "0.5.0: словарь единиц — карта"
    assert bridge.vocab("violation_units") == tuple(units)
    assert bridge.violation_unit_words() == {str(k): v.strip()
                                            for k, v in units.items()}
    # Слово единицы формы ВМП — из того же словаря, без карты в коде.
    assert vf.unit_word("night") == units["night"]
    assert vf.unit_word(None) is None
    assert not [n for n in dir(vf) if n == "UNIT_RU"], "карта слов в коде"
    # Перечень старой формы: слов взять негде — отказ словами.
    cat = bridge.catalog()
    monkeypatch.setitem(bridge._dictionaries(cat), "violation_units",
                        list(units))
    bridge._projections(cat).pop("violation_unit_words", None)
    with pytest.raises(bridge.CatalogRequired) as e:
        bridge.violation_unit_words()
    assert "картой" in str(e.value) and "слова единицы брать негде" in str(e.value)


def test_family_traits_carry_the_three_fields_of_050():
    """Три признака каталога 0.5.0 наружу: что семья читает из истории,
    чем меряется её штраф и кто подтверждает её природу."""
    traits = bridge.family_traits()
    recs = {str(r["id"]): r for r in bridge.catalog().families_full()}
    for fid, tr in traits.items():
        rec = recs[fid]
        assert tr["reads_history"] == rec.get("reads_history")
        assert tr["violation_size"] == rec.get("violation_size")
        assert tr["nature_confirmed_by"] == rec.get("nature_confirmed_by")
    # Снимок общий — чтение истории отдаётся копией, на месте его не правят.
    one = bridge.family_traits("rest_floors")
    assert one["reads_history"]["kind"] == "boundary"
    one["reads_history"]["kind"] = "подделка"
    assert bridge.family_traits("rest_floors")["reads_history"]["kind"] \
        == "boundary"
    # Мягкая семья называет и единицу, и размер нарушения.
    soft = bridge.family_traits("balanced_workload")
    assert soft["kind"] == "SOFT" and soft["violation_size"] == "magnitude"
    assert bridge.family_traits("duty_limits")["violation_size"] is None
    assert bridge.family_traits("нет_такой_семьи") == {}


def test_bridge_has_only_clean_projections():
    """Стадия 1б: имён `*_or` и аргументов «запаса из кода» у моста нет
    — иначе вызывающий мог бы подать перечень мимо каталога, и источник
    перестал бы быть одним."""
    import inspect
    assert not [n for n in dir(bridge) if n.endswith("_or")
                and not n.startswith("__")]
    for name in ("params", "vocab", "families", "conditions",
                 "subject_words", "complements", "lexicon_order"):
        sig = inspect.signature(getattr(bridge, name))
        assert not {"code", "fallback", "code_words", "code_pairs"} \
            & set(sig.parameters), f"{name}{sig}"


def test_unknown_vocabulary_key_is_a_refusal():
    """Словаря нет — отказ словами, а не молчаливый перечень из кода."""
    with pytest.raises(bridge.CatalogRequired) as e:
        bridge.vocab("нет_такого_словаря")
    assert "нет словаря" in str(e.value)


def test_slice_mode_variable_is_not_read(monkeypatch, tmp_path):
    """Переходный тест до части COSMOS 7-Б: переменная среза базы
    CREW_VMP_TEMPLATE_BASE мостом НЕ ЧИТАЕТСЯ — ни уважается, ни
    отвергается (§3 проекта: отказ по ней сделал бы красным стенд
    сторожа дрейфа COSMOS). Снимается одним окном с 7-Б."""
    from cdpm import export_task_package as tp
    fams, rev, stamp = (m.template_families(), m.registry_fingerprint(),
                        tp.catalog_stamp_of())
    slice_path = tmp_path / "NSH-срез.json"
    slice_path.write_text(json.dumps(
        {"registry_rev": "0ce7a9570d66", "catalog_version": "0.1.0-срез",
         "catalog_fingerprint": "a" * 64}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setenv("CREW_VMP_TEMPLATE_BASE", str(slice_path))
    assert m.template_families() == fams
    assert m.registry_fingerprint() == rev
    assert tp.catalog_stamp_of() == stamp


def test_template_families_from_catalog():
    fams = m.template_families()
    assert "night_series" in fams and set(fams["night_series"]) >= {
        "max_consecutive_night_duties", "night_duty_start", "night_duty_end"}
    assert "duty_limits" in fams and "max_duty_duration" in fams["duty_limits"]


def test_lexicon_reads_catalog_and_null_means_empty():
    assert lex.family_subject_words("rest_floors") == ("отдых", "отдох", "rest")
    # Семьи истории без слов — пустой кортеж: лексический контракт не судит.
    assert lex.family_subject_words("night_series") == ()
    assert lex.family_subject_words("no_such_family") == ()
    assert lex.complementary_families("duty_limits", "duty_extension_penalty")
    assert lex.complementary_families("duty_extension_penalty", "duty_limits")
    assert not lex.complementary_families("duty_limits", "rest_floors")
    assert bridge.lexicon_order()[0] == "duty_limits"
    # Порядок перебора семей — словарь каталога, а не порядок вставки
    # перечня кода (его нет со стадии 1б).
    assert lex.lexicon_families() == bridge.lexicon_order()
    # Порядок — перестановка семей СО СЛОВАМИ: семья без слов в переборе
    # не нужна (её слово-носитель никогда не найдётся), а пропущенная
    # семья со словами молча ослабила бы контракт.
    assert sorted(lex.lexicon_families()) == sorted(
        f for f in bridge.families() if lex.family_subject_words(f))


def test_subject_conflict_follows_the_catalog_order():
    """Порядок перебора семей `subject_conflict` — словарь каталога
    `lexicon_order` (прежде им был порядок вставки перечня кода): в
    тексте со словами двух семей названа ТА, что раньше по порядку —
    иначе текст проблемы у двух прогонов на одном каталоге разошёлся
    бы."""
    order = lex.lexicon_families()
    early, late = order[2], order[4]
    assert order.index(early) < order.index(late)
    stems = (lex.family_subject_words(early)[0],
             lex.family_subject_words(late)[0])
    text = f"норма про {stems[1]} и про {stems[0]} в одном предложении"
    # Семья, на которую посажено правило, своих слов в тексте не имеет.
    host = order[0]
    assert lex.subject_hit(host, text) is None, "чистота образца"
    got = lex.subject_conflict(host, text)
    assert got is not None and got[0] == early, (got, order)


def test_without_catalog_first_use_refuses_with_words(monkeypatch, tmp_path):
    """Без каталога запаса кода НЕТ: первое обращение к словарю —
    `CatalogRequired` словами с путём и причиной; модули при этом уже
    импортированы и не падали (гейт импорта — в тесте источника)."""
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "no-such-catalog"))
    bridge.forget()
    assert not bridge.catalog_available()
    for call in (lambda: m.PARAMS,
                 lambda: m.PARAM_UNITS,
                 lambda: m.template_families(),
                 lambda: m.registry_fingerprint(),
                 lambda: lex.family_subject_words("rest_floors"),
                 lambda: lex.complementary_families("duty_limits",
                                                    "rest_floors"),
                 lambda: lex.lexicon_families(),
                 lambda: bridge.params(),
                 lambda: bridge.vocab("param_units"),
                 lambda: bridge.families(),
                 lambda: bridge.subject_words("rest_floors"),
                 lambda: bridge.complements(),
                 lambda: bridge.lexicon_order()):
        with pytest.raises(bridge.CatalogRequired) as e:
            call()
        words = str(e.value)
        assert words.startswith("CDPM: каталог шаблонов обязателен: ")
        assert bridge.ENV_CATALOG in words and "no-such-catalog" in words
        assert e.value.code == "catalog-unavailable"


def test_one_catalog_instance_per_process():
    a = bridge.catalog()
    b = bridge.catalog()
    assert a is b


def test_stamp_is_version_and_fingerprint_of_one_instance(monkeypatch,
                                                          tmp_path):
    """Одна отметка каталога — версия и отпечаток ТОГО экземпляра,
    которым идёт процесс; ею штампуются посадки правил, ключ
    manifest.catalog пакета и паспорт прогона. Без каталога отметки нет
    — отказ словами (шаг 7), а не None и не выдуманная константа."""
    st = bridge.stamp()
    cat = bridge.catalog()
    assert set(st) == {"version", "fingerprint"}
    assert st["version"] == str(cat.version()) and st["version"]
    assert st["fingerprint"] == str(cat.fingerprint())
    assert re.fullmatch(r"[0-9a-f]{64}", st["fingerprint"])
    # Посадки правил штампуются той же отметкой (один каталог — один
    # ответ на вопрос «по какому каталогу посажено»).
    assert m.registry_fingerprint() == st["fingerprint"]
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "no-such-catalog"))
    bridge.forget()
    with pytest.raises(bridge.CatalogRequired):
        bridge.stamp()


def test_conditions_come_from_catalog_and_refuse_without_it(monkeypatch,
                                                            tmp_path):
    """Условия исключения семей — из записей conditions[] каталога;
    по семье — только её; без каталога — отказ словами, не пустая
    карта (пустая карта врала бы «условий у семьи нет»)."""
    conds = bridge.conditions()
    assert set(conds) == {"extension_bases", "extension_consent"}
    assert conds["extension_bases"]["family"] == "duty_extension_penalty"
    assert conds["extension_bases"]["unit"] == "code_list"
    assert conds["extension_bases"]["relation"] == "in"
    assert conds["extension_consent"]["unit"] == "flag"
    assert conds["extension_consent"]["relation"] == "yes"
    assert set(bridge.conditions("duty_extension_penalty")) == set(conds)
    assert bridge.conditions("duty_limits") == {}
    assert m.family_conditions() == conds
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "no-such-catalog"))
    bridge.forget()
    with pytest.raises(bridge.CatalogRequired):
        bridge.conditions()


def test_invalid_catalog_is_refused_once_per_instance(monkeypatch, tmp_path):
    """Проверка каталога — ОДИН раз на экземпляр, той же формулой слов,
    что у двери COSMOS: битый каталог даёт запомненный отказ, а не
    повторную проверку и не словари из кода."""
    import catalog_copies as cc
    folder = cc.copy_catalog(tmp_path)
    fam = json.loads((folder / "families.json").read_text(encoding="utf-8"))
    fam["families"][0]["zone"] = "НЕ-ЗОНА"
    (folder / "families.json").write_text(
        json.dumps(fam, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    monkeypatch.setenv(bridge.ENV_CATALOG, str(folder))
    bridge.forget()
    with pytest.raises(bridge.CatalogRequired) as e:
        bridge.catalog()
    assert "не проходит проверку" in str(e.value)
    assert e.value.problems, "перечень проблем проверки сохраняется"
    with pytest.raises(bridge.CatalogRequired):
        m.template_families()


def test_metrics_are_catalog_criteria_with_solver_executor():
    """METRICS — проекция каталога (просьба 2 извещения о паритете, 09.09):
    имена = критерии «есть» с `kotlin_metric` в `computable_by`, задачи —
    по зоне записи, слова (`title`/`note`) — CDPM; критерии только
    исполнителя-построителя в перечень не входят. Один объект снимка на
    экземпляр каталога."""
    cat = bridge.catalog()
    expected = {cid for cid, rec in cat.criteria().items()
                if bridge.METRIC_EXECUTOR_KIND in (rec.get("computable_by") or ())}
    assert expected, "в каталоге есть метрики решателя"
    assert set(m.METRICS) == expected
    assert list(m.METRICS) == sorted(m.METRICS)
    for cid, spec in m.METRICS.items():
        rec = cat.criterion(cid)
        assert spec["tasks"] == bridge.METRIC_ZONE_TASKS[rec["zone"]]
        assert spec["title"]
    # Слова CDPM у известных метрик — прежние (спецификация 1.1, соль кэша).
    assert m.METRICS["total_pairing_cost"]["title"] == "суммарная стоимость пейрингов"
    assert "note" in m.METRICS["total_pairing_cost"]
    assert m.METRICS["credit_hours_spread"]["tasks"] == ("rostering",)
    assert m.METRICS["layover_nights_total"]["tasks"] == ("pairing", "rostering")
    # Критерии без исполнителя решателя (только построитель) — не метрики.
    builder_only = {cid for cid, rec in cat.criteria().items()
                    if bridge.METRIC_EXECUTOR_KIND not in (rec.get("computable_by") or ())}
    assert builder_only and not (builder_only & set(m.METRICS))
    assert m.METRICS is m.METRICS


def test_new_solver_metric_of_catalog_appears_without_code(monkeypatch, tmp_path):
    """Новая метрика решателя в каталоге (класс правки К-табл) попадает в
    METRICS сама: заголовок — каталога (слов CDPM у неё нет), заметки нет,
    задачи — по зоне; правило паритета L08b «CDPM ещё не знает метрику»
    больше не срабатывает по числу."""
    import catalog_copies as cc
    folder = cc.copy_catalog(tmp_path, criteria={
        "probe_solver_metric": {"status": "есть", "zone": "PAIRING",
                                "computable_by": ["kotlin_metric"],
                                "title": "пробная метрика решателя"},
        "probe_builder_only": {"status": "есть", "zone": "ROSTERING",
                               "computable_by": ["builder_method"],
                               "title": "пробный критерий построителя"}})
    cc.use_catalog(monkeypatch, folder)
    bridge.forget()
    got = m.METRICS
    assert got["probe_solver_metric"] == {"tasks": ("pairing",),
                                          "title": "пробная метрика решателя"}
    assert "probe_builder_only" not in got
    assert "total_pairing_cost" in got and got["total_pairing_cost"]["note"]


def test_metrics_of_projects_any_catalog_instance(tmp_path):
    """`metrics_of(cat)` — чистая проекция по экземпляру, без переменной
    среды и без кэша моста: свидетель паритета COSMOS судит временный
    каталог (кандидат-метрика) и обязан видеть её так же, как CDPM увидит
    после выпуска. Слова CDPM подставляются по умолчанию."""
    import catalog_copies as cc
    import template_catalog as tc
    folder = cc.copy_catalog(tmp_path, criteria={
        "candidate_metric": {"status": "есть", "zone": "BOTH",
                             "computable_by": ["kotlin_metric"],
                             "title": "метрика-кандидат"}})
    cat = tc.load(folder)
    got = bridge.metrics_of(cat)
    assert got["candidate_metric"] == {"tasks": ("pairing", "rostering"),
                                       "title": "метрика-кандидат"}
    assert got["total_pairing_cost"]["title"] == "суммарная стоимость пейрингов"
    assert "note" in got["total_pairing_cost"]
    # Живой каталог мостом при этом не тронут: METRICS панели — прежние 6.
    assert "candidate_metric" not in m.METRICS
    assert list(got) == sorted(got)
    # Без слов CDPM — только заголовки каталога.
    bare = bridge.metrics_of(cat, words={})
    assert bare["positioning_cost"]["title"] == cat.criterion("positioning_cost")["title"]
    assert bare["positioning_cost"]["title"] != m.METRICS["positioning_cost"]["title"]
    assert "note" not in bare["total_pairing_cost"]
