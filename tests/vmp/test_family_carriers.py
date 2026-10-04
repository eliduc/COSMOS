# -*- coding: utf-8 -*-
"""Носители перечней семей у цены, транслятора, ведомости и сверки.

09.09.2026 первая семья, пришедшая конвейером пополнения каталога
(`duty_series_limit`), сломала сразу несколько мест, которые держали
перечень семей своей рукой. Корень чинится не заплаткой в каждом месте,
а одним носителем на признак; здесь — сторожа этих носителей.

Ожидание НЕ ЗАШИВАЕТСЯ списком имён (это был бы тот же рукописный
перечень): оно строится из ПРИЗНАКА записи каталога, а сторож
показывает, что множество по признаку и множество кода — одно и то же.
Второй разряд каждого сторожа — синтетическая семья: запись с тем же
признаком, дописанная в КОПИЮ каталога, обязана попасть в множество без
единой правки кода.

Носители, за которыми смотрит файл:

  * `vmp.chain_price.soft_families()` — мягкие семьи части «Пейринг»
    (`kind == "SOFT"`, зона `PAIRING` | `BOTH`) и единица меры словом
    (`violation_unit` → `unit_words()`); с выпуска каталога 0.5.0 слово
    единицы живёт В КАТАЛОГЕ (словарь `violation_units` стал картой
    «код → слово»), и сторож сверки сменил смысл: он проверяет, что
    COSMOS берёт слово оттуда, а не что рукописная карта покрывает
    словарь — самой карты в коде больше нет;
  * `vmp.pairing_input.SOFT_EXECUTION` — опись исполнителей построителя
    (семья → ключи запуска); ведомость исполнения читает её же;
  * `vmp.penalty_reconcile.night_event_families()` — семьи событий ночи
    (`violation_unit == "night"`);
  * `vmp.penalty_reconcile.measured_rules()` — «строка измерена, а не
    посчитана»: признак у самого сертификата, не у перечня семей.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import catalog_copies as cc                                   # noqa: E402
from vmp import catalog_access as ca                          # noqa: E402
from vmp import chain_price as cp                             # noqa: E402
from vmp import execution_manifest as em                      # noqa: E402
from vmp import kotlin_tables as ktab                         # noqa: E402
from vmp import pairing_input as pi                           # noqa: E402
from vmp import penalty_reconcile as pr                       # noqa: E402

#: Пробные записи копии каталога: имена нарочно не похожи на семьи
#: продукта — сторож судит ПРИЗНАК записи, а не её имя. Образец имени
#: записи каталога — латиница (схема раздела families).
NEW_PAIRING_SOFT = "probe_pairing_soft"
NEW_ROSTERING_SOFT = "probe_rostering_soft"


@pytest.fixture(autouse=True)
def _door(monkeypatch):
    """Каталог — тот, что назван переменной двери; после теста дверь
    забывает экземпляр, иначе tmp-копия уехала бы в соседний файл."""
    monkeypatch.delenv(ca.CATALOG_ENV, raising=False)
    ca.forget()
    yield
    ca.forget()


def _meta():
    return ca.current().families_meta()


def _by_sign(pairing: bool = True) -> set:
    """Ожидание ПО ПРИЗНАКУ: мягкие семьи названной части — из данных
    каталога, а не из списка имён."""
    zones = ("PAIRING", "BOTH") if pairing else ("ROSTERING", "BOTH")
    return {fid for fid, m in _meta().items()
            if m.get("kind") == "SOFT" and m.get("zone") in zones}


# --------------------------------- мягкие семьи части «Пейринг»
def test_мягкие_семьи_пейринга_приходят_от_записи_каталога():
    """Множество носителя = множеству записей каталога с признаком
    «мягкая, зона части «Пейринг»», а единица меры — словом единицы
    нарушения записи."""
    got = cp.soft_families()
    assert set(got) == _by_sign(), (sorted(got), sorted(_by_sign()))
    assert got, "мягких семей пейринга не найдено — сторож слеп"
    meta = _meta()
    words = cp.unit_words()
    for fid, word in got.items():
        assert word == words[meta[fid]["violation_unit"]], fid
    # Порядок каталожный: перечень строк и отказов от обхода не зависит.
    assert list(got) == sorted(got)


def test_мягкая_семья_ростеринга_в_перечень_пейринга_не_входит():
    """Разброс кредитных часов — мягкая семья, но части «Ростеринг»: у
    цены цепочки её меры нет, и перечень её не несёт."""
    only_rostering = _by_sign(pairing=False) - _by_sign()
    assert only_rostering, "мягких семей ростеринга нет — сторож слеп"
    assert not (only_rostering & set(cp.soft_families()))


def test_синтетическая_мягкая_семья_пейринга_входит_без_правки_кода(
        tmp_path, monkeypatch):
    """Запись с тем же признаком, дописанная в копию каталога, попадает
    в перечень носителя сама: правки кода для этого не нужно."""
    folder = cc.copy_catalog(tmp_path, families={
        NEW_PAIRING_SOFT: {"kind": "SOFT", "zone": "PAIRING",
                           "violation_unit": "night"},
        NEW_ROSTERING_SOFT: {"kind": "SOFT", "zone": "ROSTERING",
                             "violation_unit": "hour"}})
    cc.use_catalog(monkeypatch, folder)
    got = cp.soft_families()
    assert NEW_PAIRING_SOFT in got and got[NEW_PAIRING_SOFT] == "ночь"
    assert NEW_ROSTERING_SOFT not in got
    assert set(got) == _by_sign()


class _КаталогСоСловарём:
    """Подставной каталог: отдаёт только словари. Нужен, чтобы судить
    ОТКАЗ карты единиц: с выпуска 0.5.0 карта и словарь — одно и то же,
    и разойтись они могут лишь у кривого каталога."""

    def __init__(self, violation_units):
        self._vu = violation_units

    def dictionaries(self) -> dict:
        return {"violation_units": self._vu}


def test_слово_единицы_берётся_из_каталога_а_не_из_карты_кода():
    """Смысл сверки изменился с выпуском 0.5.0: слово единицы нарушения
    живёт В КАТАЛОГЕ (словарь `violation_units` — карта «код → слово»), а
    COSMOS его только читает. Второй карты перевода в коде нет, и сторож
    смотрит на это прямо: рукописных русских слов единиц в модуле цены
    не осталось."""
    vu = ca.current().dictionaries()["violation_units"]
    assert isinstance(vu, dict) and vu, (
        "словарь единиц нарушения не карта «код → слово»: %r" % (vu,))
    assert cp.unit_words() == {str(k): str(v) for k, v in vu.items()}
    src = Path(cp.__file__).read_text(encoding="utf-8")
    for слово in sorted(vu.values()):
        assert '"%s"' % слово not in src and "'%s'" % слово not in src, (
            "слово единицы %r вернулось в код рукописной картой" % слово)


def test_карта_единиц_перечнем_отказ():
    """Словарь единиц перечнем (форма каталога 0.4.0) — отказ словами:
    слово единицы брать негде, а молчаливого перевода не бывает."""
    with pytest.raises(cp.ChainPriceError) as e:
        cp.unit_words(_КаталогСоСловарём(["night", "duty"]))
    assert "карта" in str(e.value) and "перевода" in str(e.value)


def test_единица_без_слова_отказ():
    """Код единицы с пустым словом — отказ словами: единицу называют
    словом, а не подгоняют под число."""
    with pytest.raises(cp.ChainPriceError) as e:
        cp.unit_words(_КаталогСоСловарём({"night": "ночь", "event": "  "}))
    assert "event" in str(e.value) and "словом" in str(e.value)


def test_мягкая_семья_с_единицей_вне_карты_отказ(tmp_path, monkeypatch):
    """Единица семьи, которой карта каталога не знает, — отказ словами у
    перечня мягких семей, а не молчаливый пропуск строки цены."""
    folder = cc.copy_catalog(tmp_path, families={
        NEW_PAIRING_SOFT: {"kind": "SOFT", "zone": "PAIRING",
                           "violation_unit": "event"}})
    cc.use_catalog(monkeypatch, folder)
    без_события = {k: v for k, v in cp.unit_words().items() if k != "event"}
    monkeypatch.setattr(cp, "unit_words", lambda cat=None: dict(без_события))
    with pytest.raises(cp.ChainPriceError) as e:
        cp.soft_families()
    assert NEW_PAIRING_SOFT in str(e.value) and "словом" in str(e.value)


def test_мягкие_семьи_каталога_объявлены_решателем():
    """Свидетель со стороны решателя (закон паритета L14): у каждой
    мягкой семьи пейринга есть ограничение в TaskPackage."""
    solver = set(ktab.family_to_constraint())
    assert set(cp.soft_families()) <= solver, sorted(
        set(cp.soft_families()) - solver)


def test_мера_семьи_без_исполнителя_отказ_словами():
    """Мера различает семьи ветвями (обход двери каталога в точке
    ценообразования стоил бы дороже самой меры), и семья без ветви — не
    ноль, а отказ: иначе штраф выпал бы из цели молча."""
    with pytest.raises(cp.ChainPriceError) as e:
        cp.measure({"rule_id": "S-X", "family": NEW_PAIRING_SOFT}, [])
    assert "не имеет меры у цены цепочки" in str(e.value)


def test_у_каждой_мягкой_семьи_пейринга_есть_ветвь_меры():
    """Равенство «ветви исполнителей меры ≡ мягким семьям носителя»:
    семья перечня без своей ветви выпала бы из цели молча — ровно то,
    что случилось 09.09.2026. Отказ ветви по НЕДОСТАЮЩЕМУ ПАРАМЕТРУ
    (окно дней, порог продления) — законный: ветвь есть, ей не хватает
    данных правила."""
    for fam in cp.soft_families():
        try:
            got = cp.measure({"rule_id": "S-%s" % fam, "family": fam}, [])
        except cp.ChainPriceError as e:
            assert "не имеет меры у цены цепочки" not in str(e), fam
            continue
        assert got["family"] == fam and got["unit"] == cp.soft_families()[fam]


# --------------------------------- опись исполнителей построителя
def test_опись_построителя_покрывает_мягкие_семьи_пейринга():
    """Каждая мягкая семья части «Пейринг» из каталога названа описью
    исполнителей построителя, и лишних имён в описи нет: опись растёт
    рукой человека вместе с ветвью, а расхождение видно сторожем, а не
    молчащим весом на прогоне."""
    assert set(pi.SOFT_EXECUTION) == set(cp.soft_families()), (
        "опись исполнителей построителя и мягкие семьи каталога "
        "разошлись: %s"
        % sorted(set(pi.SOFT_EXECUTION) ^ set(cp.soft_families())))
    for fam, spec in pi.SOFT_EXECUTION.items():
        assert spec["cli"] and spec["place"], fam


def test_ведомость_исполнения_читает_опись_построителя():
    """Ведомость своего перечня семей не держит — она читает опись там,
    где ключи и передаются (зеркало расходится с оригиналом молча)."""
    assert em.FAMILY_EXECUTION is pi.SOFT_EXECUTION


def test_семья_вне_описи_построителя_отказ():
    """Fail-closed: мягкое правило пейринга без исполнителя у
    построителя — отказ, а не молчаливо потерянный вес."""
    with pytest.raises(pi.PairingInputError) as e:
        pi._apply_soft_penalties(
            [{"rule_id": "S-X", "family": NEW_PAIRING_SOFT, "weight": 1}],
            [], {})
    assert "исполнителя" in str(e.value)


def test_названный_исполнитель_без_ветви_отказ(monkeypatch):
    """Опись без ветви молчала бы так же, как перечень без семьи: семья
    принята по описи — ключ обязан уехать построителю."""
    monkeypatch.setitem(pi.SOFT_EXECUTION, NEW_PAIRING_SOFT,
                        {"cli": ("--w-проба",), "place": "ветви нет"})
    with pytest.raises(pi.PairingInputError) as e:
        pi._apply_soft_penalties(
            [{"rule_id": "S-X", "family": NEW_PAIRING_SOFT, "weight": 1}],
            [], {})
    assert "--w-проба" in str(e.value) and "молча" in str(e.value)


# --------------------------------- семьи событий ночи
def test_семьи_событий_ночи_приходят_от_записи_каталога():
    """Множество носителя = записям каталога с единицей нарушения
    «ночь»."""
    want = {fid for fid, m in _meta().items()
            if m.get("violation_unit") == "night"}
    assert want, "семей с единицей «ночь» нет — сторож слеп"
    assert pr.night_event_families() == want


def test_синтетическая_семья_ночи_входит_без_правки_кода(
        tmp_path, monkeypatch):
    folder = cc.copy_catalog(tmp_path, families={
        NEW_PAIRING_SOFT: {"kind": "SOFT", "zone": "PAIRING",
                           "violation_unit": "night"},
        NEW_ROSTERING_SOFT: {"kind": "SOFT", "zone": "ROSTERING",
                             "violation_unit": "hour"}})
    cc.use_catalog(monkeypatch, folder)
    got = pr.night_event_families()
    assert NEW_PAIRING_SOFT in got and NEW_ROSTERING_SOFT not in got


# --------------------------------- «измерена, а не посчитана»
def test_измеренную_строку_называет_сертификат_а_не_перечень_семей():
    """Признак «размер нарушения — величина» несёт сам сертификат: поле
    measure записи проверки либо раздел measures. Семья тут ни при чём —
    рукописный перечень семей-мер не знал бы семьи, которой ещё нет."""
    assert pr.measured_rules(None) == set()
    cert = {"checks": [
        {"id": "C-1", "basis": {"rule": "S-1"}, "status": "прогнана",
         "measure": {"kind": "spread_hours_over_threshold", "value": 2}},
        {"id": "C-2", "basis": {"rule": "S-2"}, "status": "прогнана"},
        {"id": "C-3", "basis": {"rule": "S-3"}, "status": "не прогнана",
         "measure": {"kind": "spread_hours_over_threshold", "value": 2}}]}
    assert pr.measured_rules(cert) == {"S-1"}
    measures = {"measures": {"S-9": {"value": 3}, "S-8": 4,
                             "S-7": "не число"}}
    assert pr.measured_rules(measures) == {"S-9", "S-8"}
