# -*- coding: utf-8 -*-
"""Р1: закон ключа, номера заявки и ключа CR (шаг 6, §2.1) — читатель.

Закон один на два продукта и три машины, поэтому у него ТРИ свидетеля с
одними литералами: этот тест (читатель), tests/test_cdpm_catalog_requests
(CDPM) и tests/vmp/test_catalog_requests (продукт COSMOS). Расхождение
хоть в одном байте материала развело бы номера — и один пробел получил
бы две заявки.

Здесь же — правила читателя, которые шаг 6 добавил к заявкам: образец
ключа CR (R02), проза слов решения владельца (A01–A06), класс документа
(D01 только при наличии словаря), `Catalog.request()` и память вердикта
`ensure_valid()`.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402
import template_catalog as tc                                     # noqa: E402
from template_catalog import requests as rq                       # noqa: E402

#: Литералы закона, проверенные расчётом 08.09.2026 (§2.1 проекта шага 6).
МАТЕРИАЛ = "families\nHARD\nPAIRING\nправило образец сторожа закона"
КЛЮЧ = "421af21358a4aa18e4a9cd0bc6e6dc621552dc4d3a438dd82923f9cdaa0c5063"
НОМЕР = "req-04763385720803207704"
КЛЮЧ_CR = "crd89c9550"
КЛЮЧ_ВИДА = "33729a630c940327125b0be105673ecd6bb00417930f97521ae94e08e09014f4"
НОМЕР_ВИДА = "req-03707195193482806055"
КЛЮЧ_CR_ВИДА = "cr575b6c6a"
НОМЕР_ДВУХ_ВИДОВ = "req-11038172079365523094"


# ═══════════════════════════════════ 1. Литералы закона

def test_1_литералы_ключа_номера_и_ключа_cr(capsys):
    key = rq.request_key("families", "HARD", "PAIRING",
                         "правило образец сторожа закона")
    assert key == hashlib.sha256(МАТЕРИАЛ.encode("utf-8")).hexdigest()
    assert key == КЛЮЧ
    assert rq.request_id_of(key) == НОМЕР
    assert rq.cr_key_of(НОМЕР) == КЛЮЧ_CR
    kkey = rq.kind_request_key("families", "duty_limits", ["kotlin_knob"])
    assert kkey == КЛЮЧ_ВИДА
    assert rq.request_id_of(kkey) == НОМЕР_ВИДА
    assert rq.cr_key_of(НОМЕР_ВИДА) == КЛЮЧ_CR_ВИДА
    assert rq.REQUEST_ID_RE.match(НОМЕР) and rq.CR_KEY_RE.match(КЛЮЧ_CR)
    print(f"\nР1: закон сверен по 4 литералам — ключ {КЛЮЧ[:12]}…, номер "
          f"{НОМЕР}, ключ CR {КЛЮЧ_CR}; ключ вида {КЛЮЧ_ВИДА[:12]}…")


def test_1_литерал_совпадает_с_законом_cdpm():
    """Тот же материал у CDPM (`cdpm/catalog_requests.request_key` —
    трёхаргументная обёртка с разделом «families»)."""
    from cdpm import catalog_requests as cr
    for kind, zone, statement in (
            ("HARD", "PAIRING", "правило образец сторожа закона"),
            ("SOFT", "ROSTERING", "Отдых на базе не менее нормы"),
            ("HARD", "BOTH", "  разрыв, между ЯВКАМИ — не менее нормы ")):
        assert cr.request_key(kind, zone, statement) == \
            rq.request_key("families", kind, zone, statement)
        assert cr.request_id_of(cr.request_key(kind, zone, statement)) == \
            rq.request_id_of(rq.request_key("families", kind, zone,
                                            statement))


def test_2_регистр_и_пунктуация_номер_не_меняют():
    a = rq.request_key("families", "HARD", "PAIRING",
                       "Отдых на базе не менее нормы")
    b = rq.request_key("families", "HARD", "PAIRING",
                       "  отдых, на базе — не менее НОРМЫ ")
    assert a == b
    # ...а вид, зона и раздел — меняют.
    assert a != rq.request_key("families", "SOFT", "PAIRING",
                               "Отдых на базе не менее нормы")
    assert a != rq.request_key("families", "HARD", "ROSTERING",
                               "Отдых на базе не менее нормы")
    assert a != rq.request_key("criteria", "HARD", "PAIRING",
                               "Отдых на базе не менее нормы")
    assert rq.normalized_text("  Тест — ПРОБА;  ") == "тест проба"


def test_3_ключ_вида_не_зависит_от_порядка_но_зависит_от_набора():
    один = rq.kind_request_key("families", "duty_limits",
                               ["kotlin_knob", "check_code"])
    другой = rq.kind_request_key("families", "duty_limits",
                                 ["check_code", "kotlin_knob"])
    повтор = rq.kind_request_key("families", "duty_limits",
                                 ["check_code", "kotlin_knob", "check_code"])
    assert один == другой == повтор
    assert rq.request_id_of(один) == НОМЕР_ДВУХ_ВИДОВ
    assert один != rq.kind_request_key("families", "duty_limits",
                                       ["kotlin_knob"])
    assert один != rq.kind_request_key("criteria", "duty_limits",
                                       ["kotlin_knob", "check_code"])
    assert один != rq.kind_request_key("families", "rest_floors",
                                       ["kotlin_knob", "check_code"])


def test_4_ручная_заявка_и_канон_черновика():
    черновик = {"id": "sit_overage_steps", "title": "Проба",
                "params": [{"name": "a", "unit": "minutes"}]}
    иной = {"id": "sit_overage_steps", "title": "Проба",
            "params": [{"name": "b", "unit": "minutes"}]}
    canon1 = rq.draft_canon(черновик)
    # Порядок ключей канон не двигает, содержание — двигает.
    assert canon1 == rq.draft_canon(dict(reversed(list(черновик.items()))))
    assert canon1 != rq.draft_canon(иной)
    assert rq.draft_canon(None) is None
    a = rq.manual_key("families", "sit_overage_steps", canon1)
    b = rq.manual_key("families", "sit_overage_steps", rq.draft_canon(иной))
    c = rq.manual_key("families", "sit_overage_steps", None)
    assert len({a, b, c}) == 3
    assert len({rq.request_id_of(x) for x in (a, b, c)}) == 3


def test_4_канон_черновика_равен_канону_cdpm():
    from cdpm import catalog_requests as cr
    черновик = {"id": "sit_overage_steps", "title": "Проба",
                "semantics": {"text": "механика словами"}}
    assert cr.draft_canon(черновик) == rq.draft_canon(черновик)


def test_5_ссылка_источника_и_её_хвост():
    ключ = rq.request_key("families", "HARD", "PAIRING", "проба")
    ref = rq.ref_of("run-0090", ключ)
    assert ref == f"run-0090#{ключ[:12]}" and len(ключ[:12]) == rq.KEY_TAIL
    assert rq.key_tail_of(ref) == ключ[:12]
    assert rq.key_tail_of("run-0090") is None
    assert rq.key_tail_of(None) is None
    # Хвост непрозрачен: текста правила он не несёт (A01/A05 проходят).
    assert not re.search(r"[А-Яа-я]", rq.key_tail_of(ref))


def test_6_негодный_ключ_и_номер_отказывают_словами():
    with pytest.raises(ValueError) as e:
        rq.request_id_of("не ключ")
    assert "sha256" in str(e.value)
    with pytest.raises(ValueError) as e:
        rq.cr_key_of("req-0001")
    assert "req-<20 цифр>" in str(e.value)


def test_7_перечень_классов_документа_один_на_два_продукта():
    from cdpm import catalog_requests as cr
    assert len(rq.DOCUMENT_CLASSES) == 7
    assert tuple(cr.DOCUMENT_CLASSES) == tuple(rq.DOCUMENT_CLASSES)
    assert cr.DEFAULT_DOCUMENT_CLASS in rq.DOCUMENT_CLASSES
    assert all(isinstance(x, str) and x.strip() for x in rq.DOCUMENT_CLASSES)


# ═══════════════════════════════════ 2. Читатель: заявка и вердикт

def _документ_заявки(cat, *, request_id=НОМЕР, state="подана", **правки):
    """Заявка на вид исполнителя у существующей записи (черновика нет)."""
    doc = {"request_id": request_id, "state": state, "section": "families",
           "record_id": "duty_limits",
           "submitted_at": "2026-09-12T10:15:00+03:00",
           "submitted_by": "COSMOS",
           "source": {"kind": "пробел при нажатии",
                      "document_class": _класс_документа(cat),
                      "run_class": "прогон COSMOS",
                      "ref": rq.ref_of("run-0031", КЛЮЧ_ВИДА)},
           "draft": None,
           "executor_kinds_requested": _вид_записи(cat, "duty_limits"),
           "decision": {"by": None, "at": None, "words": None},
           "cr_key": None}
    doc.update(правки)
    return doc


def _класс_документа(cat):
    """Значение — из живой копии: словарь `document_classes` появится
    первой сборкой службой, до него — перечень читателя."""
    словарь = cat.dictionaries().get("document_classes")
    return (словарь or list(rq.DOCUMENT_CLASSES))[0]


def _вид_записи(cat, fid: str) -> list:
    return [sorted(cat.family(fid)["executor_kinds"])[0]]


def _положить(dst: Path, заявки: list) -> None:
    (dst / "requests.json").write_bytes(
        (json.dumps({"form_version": 1, "requests": заявки},
                    ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))


@pytest.fixture()
def копия(tmp_path) -> Path:
    return cc.copy_catalog(tmp_path)


def test_8_request_отдаёт_копию_и_none(копия, capsys):
    cat0 = tc.load(копия)
    _положить(копия, [_документ_заявки(cat0)])
    cat = tc.load(копия)
    got = cat.request(НОМЕР)
    assert got is not None and got["record_id"] == "duty_limits"
    got["record_id"] = "испорчено"
    assert cat.request(НОМЕР)["record_id"] == "duty_limits", "отдана не копия"
    assert cat.request("req-00000000000000000000") is None
    assert cat.fingerprint() == cat0.fingerprint(), "заявки вне отпечатка"
    assert not [p for p in cat.validate() if p.severity == "error"]
    print(f"\nР1: заявок в копии {len(cat.requests())}, отпечаток не сдвинут")


def test_9_r02_ключ_cr_не_по_образцу(копия):
    cat0 = tc.load(копия)
    решение = {"by": "владелец", "at": "2026-09-12T10:00:00+03:00",
               "words": "принимаю: механика общая"}
    _положить(копия, [_документ_заявки(cat0, state="исполнитель заявлен",
                                       decision=решение, cr_key="CR-1")])
    probs = tc.load(копия).validate()
    r02 = [p for p in probs if p.code == "R02"
           and p.where.endswith(".cr_key")]
    assert r02 and "cr<8 hex>" in r02[0].message
    # ...а ключ по закону проходит.
    _положить(копия, [_документ_заявки(
        cat0, state="исполнитель заявлен", decision=решение,
        cr_key=rq.cr_key_of(НОМЕР))])
    assert not [p for p in tc.load(копия).validate() if p.severity == "error"]


@pytest.mark.parametrize("слова,код", [
    ("отклоняю: в базе SVX своя норма", "A01"),
    ("принимаю: отдых не более 13 часов подряд", "A06"),
])
def test_10_слова_решения_владельца_судятся_прозой(копия, слова, код):
    cat0 = tc.load(копия)
    решение = {"by": "владелец", "at": "2026-09-12T10:00:00+03:00",
               "words": слова}
    _положить(копия, [_документ_заявки(cat0, state="принята",
                                       decision=решение)])
    probs = tc.load(копия).validate()
    беда = [p for p in probs if p.where.endswith(".decision.words")]
    assert беда, [str(p) for p in probs]
    assert код in {p.code for p in беда}
    assert беда[0].where == f"requests/{НОМЕР}.decision.words"


def test_10_слова_решения_с_маркером_заказчика(копия):
    cat0 = tc.load(копия)
    решение = {"by": "владелец", "at": "2026-09-12T10:00:00+03:00",
               "words": "принимаю по просьбе Полярис"}
    _положить(копия, [_документ_заявки(cat0, state="принята",
                                       decision=решение)])
    probs = tc.load(копия).validate(forbidden_terms=("Полярис",))
    беда = [p for p in probs if p.where.endswith(".decision.words")]
    assert беда and беда[0].code == "A02"


def test_11_d01_класс_документа_судится_только_при_словаре(копия, capsys):
    cat0 = tc.load(копия)
    _положить(копия, [_документ_заявки(
        cat0, source={"kind": "пробел при нажатии",
                      "document_class": "выдуманный класс",
                      "run_class": "прогон COSMOS", "ref": None})])
    # Словаря нет — судить нечем, и это молчание названо в 0.14.
    cat = tc.load(копия)
    assert cat.dictionaries().get("document_classes") is None
    assert not [p for p in cat.validate() if p.code == "D01"
                and "document_class" in p.where]
    # Заводим словарь черновиком службы — правило оживает.
    d = json.loads((копия / "dictionaries.json").read_text(encoding="utf-8"))
    d["document_classes"] = list(rq.DOCUMENT_CLASSES)
    (копия / "dictionaries.json").write_bytes(
        (json.dumps(d, ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))
    cat2 = tc.load(копия)
    d01 = [p for p in cat2.validate() if p.code == "D01"
           and "document_class" in p.where]
    assert d01 and "выдуманный класс" in d01[0].message
    print(f"\nР1: словарь document_classes из "
          f"{len(cat2.dictionaries()['document_classes'])} значений — "
          f"D01 ожил; без словаря молчит")


def test_11_словарь_классов_не_ломает_отпечаток_и_схему(копия):
    """Свойство схемы необязательное: каталог без ключа зелен, с ключом
    — тоже; отпечаток при этом законно меняется (словарь — часть тела)."""
    было = tc.load(копия)
    assert not [p for p in было.validate() if p.severity == "error"]
    d = json.loads((копия / "dictionaries.json").read_text(encoding="utf-8"))
    d["document_classes"] = list(rq.DOCUMENT_CLASSES)
    (копия / "dictionaries.json").write_bytes(
        (json.dumps(d, ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))
    стало = tc.load(копия)
    assert стало.fingerprint() != было.fingerprint()
    schema_problems = [p for p in стало.validate() if p.code == "S06"]
    assert not schema_problems, [str(p) for p in schema_problems]
    # Отпечаток шапки теперь не сходится — это H02, а не беда словаря.
    коды = {p.code for p in стало.validate() if p.severity == "error"}
    assert коды <= {"H02"}, коды


def test_12_память_вердикта_validate(копия, monkeypatch, capsys):
    """Ш2 шага 7: проверка идёт один раз на экземпляр, слова отказа —
    одни для двери COSMOS и моста CDPM."""
    cat = tc.load(копия)
    счёт = cc.count_validate(monkeypatch)
    assert cat.ensure_valid() == cat.ensure_valid()
    assert len(счёт) == 1, f"проверка повторилась {len(счёт)} раз"
    # Негодный каталог отказывает ТЕМИ ЖЕ словами при каждом обращении.
    h = json.loads((копия / "catalog.json").read_text(encoding="utf-8"))
    h["fingerprint"] = "0" * 64
    (копия / "catalog.json").write_bytes(
        (json.dumps(h, ensure_ascii=False, sort_keys=True, indent=1)
         + "\n").encode("utf-8"))
    плохой = tc.load(копия)
    счёт2 = cc.count_validate(monkeypatch)
    with pytest.raises(tc.CatalogInvalid) as e1:
        плохой.ensure_valid()
    with pytest.raises(tc.CatalogInvalid) as e2:
        плохой.ensure_valid()
    with pytest.raises(tc.CatalogInvalid) as e3:
        плохой.raise_if_invalid()
    assert str(e1.value) == str(e2.value) == str(e3.value)
    assert "не проходит проверку" in str(e1.value)
    assert len(счёт2) == 1, "негодный каталог проверялся дважды"
    print(f"\nР1: вердикт вычислен 1 раз на экземпляр; слова отказа — "
          f"{str(e1.value)[:60]}…")


def test_13_current_validated_не_плодит_экземпляр(копия, monkeypatch):
    """`validated` не входит в ключ кэша: дверь и мост обязаны держать
    ОДИН экземпляр на процесс."""
    tc.forget_current()
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(копия))
    a = tc.current(str(копия))
    b = tc.current(str(копия), validated=True)
    c = tc.current(str(копия))
    assert a is b is c
    tc.forget_current()
