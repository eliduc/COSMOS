# -*- coding: utf-8 -*-
"""Шаг 4 переноса ВМП: словарь видов целевой + маршрут пробелов.

Вид 0096 «нормированная стоимость по рангам» объявлен в спецификации
БЕЗ исполнителя намеренно: его исполнитель — первый живой прогон
механизма этапа 2 (отмашка владельца → LLM через контур
самоулучшения), не ручная реализация. Тесты фиксируют оба маршрута
судьи и форму заявки."""
import pytest

from vmp import criteria, gaps
from vmp import pairing_input as pi

APPLIED = {
    "min_sit_home_min": 50,
    "max_duty_span_min": 540,
    "min_rest_home_min": 720,
    "min_rest_outstation_min": 960,
}
PAYLOAD = {
    "employees": [{"homeAirport": "HUB", "skills": ["CP"]}],
    "flights": [{"departureUTCDateTime": "2026-09-01T08:00:00Z",
                 "arrivalUTCDateTime": "2026-09-01T10:00:00Z"}]}


def _crit_0096():
    return {"kind": "rank_normalized_cost",
            "label": "NORMALIZED_COST", "source_rule": "0096",
            "rates": {"flight": 1.0, "work": 0.7},
            "rates_version": {"source": "PARAM-TEST-01",
                              "version": "005",
                              "effective_from": "2026-08-01"}}


def test_вид_исполняется_ставками_из_пакета():
    """Обезличенный шаблон (принцип владельца 2026-08-29): критерий
    пакета транслируется в расчётную ветку с ДЕЦИ-коэффициентами,
    вычисленными из ставок пакета; веса w-* не подаются вовсе."""
    args, prov = [], {}
    gap = criteria.apply_criterion(_crit_0096(), args, prov)
    assert gap is None
    assert args == ["--cost-0096",
                    "--cost-rank-deci-work", "17",
                    "--cost-rank-deci-dh", "7",
                    "--cost-rates-param", "PARAM-TEST-01/v005",
                    "--criterion-source", "критерий пакета: 0096"]
    assert "PARAM-TEST-01" in prov["--cost-0096"]["from"]
    assert prov["--cost-rank-deci-work"]["value"] == 17
    assert prov["--cost-rank-deci-dh"]["value"] == 7


def test_приёмка_обезличенности_другие_ставки_без_правки_кода(
        tmp_path, monkeypatch):
    """Машинный критерий приёмки шаблона (владелец, 2026-08-29):
    правило того же типа с ДРУГИМИ значениями другой авиакомпании
    исполняется БЕЗ правки кода. Контур изолирован: у другой
    авиакомпании свой каталог данных (контрольная пара текущего
    контура сюда не относится — и это отдельно проверено живым
    контуром: чужие ставки на его паре дают честный отказ)."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    other = _crit_0096()
    other["label"] = "COST-X"
    other["source_rule"] = "0123"
    other["rates"] = {"flight": 1.2, "work": 0.9}
    other["rates_version"] = {"source": "X-PARAM-01", "version": "1",
                              "effective_from": "2027-01-01"}
    args, prov = [], {}
    assert criteria.apply_criterion(other, args, prov) is None
    assert args == ["--cost-0096",
                    "--cost-rank-deci-work", "21",
                    "--cost-rank-deci-dh", "9",
                    "--cost-rates-param", "X-PARAM-01/v1",
                    "--criterion-source", "критерий пакета: 0123"]
    assert "X-PARAM-01" in prov["--cost-0096"]["from"]


def test_отказы_формы_ставок():
    """Код валидирует ФОРМУ, не значения: ставка вне кратности 0,1
    (деци-форма не выражает), не число, пустая метка, неполная
    версия — честные отказы словами."""
    frac = _crit_0096()
    frac["rates"] = {"flight": 1.0, "work": 0.75}
    with pytest.raises(criteria.CriteriaError) as e:
        criteria.apply_criterion(frac, [], {})
    assert "деци" in str(e.value)
    neg = _crit_0096()
    neg["rates"] = {"flight": -1.0, "work": 0.7}
    with pytest.raises(criteria.CriteriaError):
        criteria.apply_criterion(neg, [], {})
    no_label = _crit_0096()
    no_label["label"] = "  "
    with pytest.raises(criteria.CriteriaError):
        criteria.apply_criterion(no_label, [], {})
    no_ver = _crit_0096()
    no_ver["rates_version"] = {"source": "PARAM-TEST-01"}
    with pytest.raises(criteria.CriteriaError) as e2:
        criteria.apply_criterion(no_ver, [], {})
    assert "MUST_BE_EXTERNAL" in str(e2.value)


def _fake_gap_kind(monkeypatch):
    monkeypatch.setitem(criteria.SPEC, "test_gap_kind", {
        "title": "синтетический вид без исполнителя",
        "needs": "нужен исполнитель"})


def test_builder_args_не_молчит_о_пробеле(monkeypatch):
    """Механизм пробела жив для будущих видов: вид в SPEC без
    исполнителя → builder_args останавливается, не продолжает."""
    _fake_gap_kind(monkeypatch)
    with pytest.raises(pi.CriterionGapError) as e:
        pi.builder_args(APPLIED, PAYLOAD, budget_s=60,
                        criterion={"kind": "test_gap_kind"})
    assert e.value.gap.kind == "test_gap_kind"
    assert "пробел шаблона" in str(e.value)


def test_маршрут_stop_умолчание():
    gap = criteria.CriterionGap(
        kind="rank_normalized_cost", title="т", source_rule="0096",
        needs="нужен исполнитель")
    with pytest.raises(gaps.VmpGapStop) as e:
        gaps.route([gap])
    text = str(e.value)
    assert "rank_normalized_cost" in text
    assert "0096" in text
    assert "заявкой каталога шаблонов" in text


def test_маршрут_warn_отдаёт_извещение():
    gap = criteria.CriterionGap(
        kind="rank_normalized_cost", title="т", source_rule=None,
        needs="нужен исполнитель")
    text = gaps.route([gap], on_gap="warn")
    assert "пробелы шаблонов COSMOS: 1" in text
    assert "источник в ВМП не назван" in text
    # Пустой перечень — тишина без исключений в обоих режимах.
    assert gaps.route([], on_gap="stop") == ""
    assert gaps.route([], on_gap="warn") == ""


def test_маршрут_чужого_флага_отказ():
    with pytest.raises(ValueError):
        gaps.route([], on_gap="silent")


def test_строки_очереди_изменений_модуль_больше_не_собирает():
    """Шаг 6 каталога (§3.7 проекта): путь «пробел → строка очереди
    изменений с машинной формой правила авиакомпании» снят. Модуль
    пробелов остаётся сверкой и извещением; ЧТО делать с пробелом —
    решает классификатор заявок каталога, и в его заявке нет ни
    формулировки правила, ни его источников.

    Сторож именной: пока имена живы, их зовут — и текст правила снова
    уезжает облачной модели через очередь изменений."""
    for name in ("to_cr", "rule_gap_to_cr", "CR_AGENT"):
        assert not hasattr(gaps, name), (
            f"vmp.gaps.{name} снят шагом 6 — заявку каталога собирает "
            f"vmp.catalog_requests")
    from vmp import catalog_requests as vcr
    assert callable(vcr.classify)


def test_вычислимость_целевой_пейринга_судит_исполнитель():
    """Серия H0 (03.09): кнопка брала computable из отчёта покрытия
    решателя ростеринга (ответ о себе) и отступала на выполнимость —
    OPTIMAL по нулевой целевой. Судья вычислимости пейринга — словарь
    исполнителей построителя."""
    from vmp import criteria as c
    ok = c.coverage({"kind": "min_person_days", "rate_per_duty": 100,
                     "source_rule": "O-1"})
    assert ok["computable"] is True
    assert ok["executor"] == "vmp.criteria._exec_min_person_days"
    bad = c.coverage({"kind": "min_person_days", "rate_per_duty": 0})
    assert bad["computable"] is False and "негодны" in bad["reason"]
    assert c.coverage(None)["computable"] is False
    assert "не объявлен" in c.coverage({})["reason"]
    alien = c.coverage({"kind": "нет_такого_вида"})
    assert alien["computable"] is False and "вне словаря" in alien["reason"]
    # Отказ исполнителя иного класса (контрольная пара контура не
    # сошлась) — «невычислима» с причиной, а не исключение у потребителя.
    from vmp import template_registry as tr

    def _boom(kind, deci, path=None):
        raise tr.ControlPairError("контрольная пара не сходится (тест)")
    import unittest.mock as um
    with um.patch.object(tr, "check_control_pair", _boom):
        rank = c.coverage({"kind": "rank_normalized_cost", "label": "X",
                           "rates": {"flight": 1.0, "work": 2.0},
                           "rates_version": {"source": "s", "version": "1",
                                             "effective_from": "2026-01-01"}})
    assert rank["computable"] is False
    assert "исполнитель отказал" in rank["reason"]
    assert rank["executor"] == "vmp.criteria._exec_rank_normalized_cost"


def test_неизвестный_вид_остаётся_отказом():
    """Закрытость словаря: вид вне SPEC — CriteriaError, не пробел."""
    with pytest.raises(criteria.CriteriaError) as e:
        criteria.apply_criterion({"kind": "min_fuel"}, [], {})
    assert "вне перечня" in str(e.value)
    assert "rank_normalized_cost" in str(e.value)


def test_контрольная_пара_из_носителя_контура(tmp_path):
    """Контрольная пара — файлом контура данных (конвенция
    2026-08-29): нет файла = спит; пара сходится = ok; расхождение =
    отказ («код и данные разъехались»); кривой файл = отказ."""
    import json
    from vmp import template_registry as tr
    deci = {"deci_work": 18, "deci_dh": 5}
    missing = tmp_path / "нет.json"
    assert tr.check_control_pair("rank_normalized_cost", deci,
                                 missing) == "silent"
    good = tmp_path / "pairs.json"
    good.write_text(json.dumps({
        "form_version": 1, "customer": "Т", "pairs": [
            {"criterion_kind": "rank_normalized_cost",
             "flight_min": 400, "dh_min": 80, "crew": 2,
             "expected_total_deci": 15200}]},
        ensure_ascii=False), encoding="utf-8")
    assert tr.check_control_pair("rank_normalized_cost", deci,
                                 good) == "ok"
    assert tr.check_control_pair("другой_вид", deci,
                                 good) == "no_pair"
    with pytest.raises(tr.ControlPairError, match="разъехались"):
        tr.check_control_pair("rank_normalized_cost",
                              {"deci_work": 21, "deci_dh": 9}, good)
    bad = tmp_path / "кривой.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(tr.ControlPairError, match="не читается"):
        tr.check_control_pair("rank_normalized_cost", deci, bad)


def test_реестр_типов_шаблонов():
    """Реестр типов (решение владельца 2026-08-29): критерии живые с
    меткой ядро/расширение и связкой со словарём; семьи механик
    правил — ядро с исполнителями; неизвестный тип — None."""
    from vmp import template_registry as tr
    c = tr.lookup("rank_normalized_cost")
    assert c["section"] == "criterion" and c["class"] == "core"
    assert c["known_to_spec"] and c["has_executor"]
    r = tr.lookup("rest_floors")
    assert r["section"] == "rule_mechanic" and r["executors"]
    assert tr.lookup("нет_такого_типа") is None


def test_машинная_семантика_вида_живёт_в_спецификации():
    """Машинная семантика вида (обезличенная: формула + контракт формы
    ставок) остаётся В КАТАЛОГЕ, а не переписывается в заявку: исполнитель
    вида читает её из записи критерия, и второго носителя у неё нет.

    Прежде её копировала заявка очереди изменений (`gaps.to_cr`) — со
    шага 6 такой копии нет, и тест сторожит именно источник."""
    sem = (criteria.SPEC.get("rank_normalized_cost") or {}).get("semantics")
    assert sem, "спецификация вида без машинной семантики"
    assert "формула" not in sem  # структура: formula + rates_form
    assert "по рангам состава" in sem["formula"]
    assert sem["rates_form"]["policy"] == "MUST_BE_EXTERNAL"
    assert "control_pair" not in sem  # пары — в носителе контура


def test_пробелы_правил_обогащаются_моделью():
    """Сверка ВМП с шаблонами (приоритет владельца 2026-08-28, п. 2):
    непокрытые правила отчёта покрытия получают машинную форму из
    модели пакета; отсутствие правила в модели — громкая пометка."""
    model = {"rules": [
        {"id": "R-1", "title": "Отдых перед вылетом",
         "statement": "720 в базе", "zone": "ROSTERING",
         "kind": "HARD", "sources": [{"sid": "S1"}]}]}
    unc = [{"rule_id": "R-1", "reason": "logic: отказ компилятора"},
           {"rule_id": "R-9", "reason": "мягкое без исполнителя"}]
    gs = gaps.rule_gaps(unc, model)
    assert len(gs) == 2
    assert gs[0].rule["title"] == "Отдых перед вылетом"
    assert gs[1].rule is None
    assert "брак согласованности" in gs[1].reason
    text = gaps.notify_rules_text(gs)
    assert "правил ВМП без шаблона-исполнителя COSMOS: 2" in text
    assert "R-1 (Отдых перед вылетом)" in text
    assert "заявкой каталога шаблонов" in text


def test_машинная_форма_правила_остаётся_внутри_контура():
    """Машинная форма правила нужна ЧЕЛОВЕКУ у артефакта нажатия (он
    читает её в своём контуре) и не нужна заявке каталога: заявку
    строит классификатор от записи и видов, и `RuleGap` для этого не
    зовётся вовсе."""
    gs = gaps.rule_gaps(
        [{"rule_id": "R-1", "reason": "notHard"}],
        {"rules": [{"id": "R-1", "title": "т", "statement": "с",
                    "zone": "PAIRING", "kind": "HARD"}]})
    assert gs[0].rule["statement"] == "с"
    # Правило вне модели — форма None и громкая пометка, а не выдумка.
    gs2 = gaps.rule_gaps([{"rule_id": "R-X", "reason": "r"}], {"rules": []})
    assert gs2[0].rule is None and "брак согласованности" in gs2[0].reason
