# -*- coding: utf-8 -*-
"""Заявки каталога шаблонов со стороны продукта COSMOS (шаг 6, вход
«пробел при нажатии»).

Охраняемое:
  * закон ключа и номера — ТОТ ЖЕ, что у читателя и у CDPM (три
    свидетеля одних литералов);
  * классификатор: девять строк таблицы §1.2 проекта — форма пробела,
    адрес записи, виды, номер и слова; открытая заявка по записи
    судится первой; неизвестный вид пробела называется словами, а не
    молчит;
  * очередь: файл создаётся ИСКЛЮЧИТЕЛЬНО (два нажатия — один номер),
    повтор ничего не переписывает, квитанция ложится в папку
    артефактов нажатия;
  * клиент службы: 201/200, повтор на «служба занята записью» и на
    сети, отсутствие повтора на прочих отказах;
  * СТОРОЖ ДАННЫХ ЗАКАЗЧИКА: в файлах очереди нет ни ключей
    `rule/statement/sources/quote`, ни маркеров заказчика;
  * за время тестов под `catalog/` репозитория не появилось и не
    изменилось ни одного байта.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import catalog_copies as cc                                       # noqa: E402
from template_catalog import requests as law                      # noqa: E402
from template_catalog.export import dump_json                     # noqa: E402
from vmp import catalog_access as ca                              # noqa: E402
from vmp import catalog_requests as vcr                           # noqa: E402

LIVE_CATALOG = ROOT / "catalog"
RUN = "run-0031"
#: Адрес службы каталога для стендов — вымышленный (зона `.invalid`
#: закреплена RFC 6761 и не разрешается никогда). urlopen подменён,
#: в сеть тест не ходит; адрес задаёт сам тест и из него же выводит
#: ожидаемый адрес вызова.
SERVICE_URL = "http://catalog.invalid"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Носители контура заявок — свои у каждого теста: очередь во
    временной папке, машинный канал выключен, автоподача выключена
    (тест подачи включает её сам). Без этого прогон писал бы в живой
    каталог данных машины и стучался в живую службу."""
    monkeypatch.setenv(vcr.ENV_OUTBOX, str(tmp_path / "queue"))
    monkeypatch.setenv(vcr.ENV_AUTO, "0")
    monkeypatch.delenv(vcr.ENV_API_URL, raising=False)
    monkeypatch.delenv(vcr.ENV_TOKEN, raising=False)
    monkeypatch.delenv(vcr.ENV_UI_URL, raising=False)
    yield
    ca.forget()


@pytest.fixture(scope="module", autouse=True)
def _catalog_untouched():
    """Живой каталог репозитория тесты не трогают (правило шага 2)."""
    before = _snapshot(LIVE_CATALOG)
    yield
    assert _snapshot(LIVE_CATALOG) == before, \
        "тесты изменили каталог репозитория — заявки пишет только служба"


def _snapshot(folder: Path) -> dict:
    return {str(p.relative_to(folder)): p.read_bytes()
            for p in sorted(folder.rglob("*.json"))
            if "__pycache__" not in p.parts}


# ─────────────────────────────────────────────────────────── помощники

#: Состояния, после которых у заявки обязано быть решение владельца
#: (R02 читателя): «подана» — единственное состояние без него.
DECIDED = {"by": "владелец", "at": "2026-09-12T10:00:00+03:00",
           "words": "механика общая, исполнителя стоит написать"}


def _request(request_id: str, *, section="families", record_id="probe_family",
             state="подана", by="CDPM", kinds=None, draft=None) -> dict:
    return {"request_id": request_id, "state": state, "section": section,
            "record_id": record_id, "submitted_at": "2026-09-12T10:15:00+03:00",
            "submitted_by": by,
            "source": {"kind": "пробел при нажатии", "document_class": None,
                       "run_class": "прогон COSMOS", "ref": "run-0088#abcdef012345"},
            "draft": draft, "executor_kinds_requested": kinds,
            "decision": ({"by": None, "at": None, "words": None}
                         if state == "подана" else dict(DECIDED)),
            "cr_key": None}


def _draft_from(cat, record_id: str, new_id: str) -> dict:
    """Черновик записи — существующая запись без полей, которых у
    черновика не бывает (`stand_tasks`, `status`, `history`)."""
    draft = {k: v for k, v in cat.family(record_id).items()
             if k not in ("stand_tasks", "status", "history")}
    draft["id"] = new_id
    return draft


def _catalog(tmp_path, monkeypatch, *, requests=(), families=None,
             criteria=None, name="catalog"):
    """Копия каталога с заявками. Заявки живут ВНЕ отпечатка — их файл
    пишется прямо в копию, пересчёт шапки не нужен."""
    folder = cc.copy_catalog(tmp_path, families=families, criteria=criteria,
                             name=name)
    (folder / "requests.json").write_bytes(
        dump_json({"form_version": 1, "requests": list(requests)})
        .encode("utf-8"))
    cc.use_catalog(monkeypatch, folder)
    return ca.current()


#: Пробная семья: два объявленных вида, оба без исполнителя.
PROBE = {"probe_family": {"executor_kinds": ["kotlin_knob", "check_code"],
                          "kind": "HARD", "zone": "PAIRING",
                          "violation_unit": None, "check_templates": [],
                          "title": "пробная семья помощника"}}
#: Правило с посадкой A на пробную семью — «данных заказчика» в нём нет.
RULE = {"id": "R-PROBE", "kind": "HARD", "zone": "PAIRING",
        "statement": "правило образец сторожа закона",
        "title": "предел пробной семьи",
        "template_fit": {"category": "A", "family": "probe_family",
                         "params": {"max_probe": 1}}}


def _states(record_id="probe_family", section="families", **kinds):
    return {section: {record_id: {k: {"COSMOS": v}
                                  for k, v in kinds.items()}}}


# ═══════════════════════════════════════ 1. Закон ключа (третий свидетель)

def test_закон_ключа_тот_же_что_у_читателя_и_у_cdpm():
    """Литералы §2.1 проекта. Тот же материал даёт тот же номер у трёх
    свидетелей — иначе один пробел, увиденный CDPM и COSMOS, породил бы
    две заявки и две строки очереди изменений."""
    key = law.request_key("families", "HARD", "PAIRING",
                          "правило образец сторожа закона")
    assert key == ("421af21358a4aa18e4a9cd0bc6e6dc62"
                   "1552dc4d3a438dd82923f9cdaa0c5063")
    assert law.request_id_of(key) == "req-04763385720803207704"
    assert law.cr_key_of("req-04763385720803207704") == "crd89c9550"
    kinds_key = law.kind_request_key("families", "duty_limits",
                                     ["kotlin_knob"])
    assert kinds_key == ("33729a630c940327125b0be105673ecd"
                         "6bb00417930f97521ae94e08e09014f4")
    assert law.request_id_of(kinds_key) == "req-03707195193482806055"
    assert law.cr_key_of("req-03707195193482806055") == "cr575b6c6a"
    # Состав набора видов — часть содержания, порядок — нет.
    assert law.kind_request_key("families", "duty_limits",
                                ["check_code", "kotlin_knob"]) == \
        law.kind_request_key("families", "duty_limits",
                             ["kotlin_knob", "check_code"])
    assert law.request_id_of(law.kind_request_key(
        "families", "duty_limits", ["kotlin_knob", "check_code"])) == \
        "req-11038172079365523094"


# ═══════════════════════════════════════════════ 2. Классификатор пробелов

def test_пробел_относительно_записи_даёт_заявку_на_вид(tmp_path, monkeypatch):
    """Первая строка таблицы §1.2: семья в каталоге есть, виды объявлены,
    исполнителя нет ни у одного продукта — заявка на вид исполнителя.
    В строке нет ни формулировки правила, ни его идентификатора в ключе:
    ключ считается от записи и набора видов."""
    cat = _catalog(tmp_path, monkeypatch, families=PROBE)
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE",
                        "note": "исполнитель не найден"}, RULE, cat,
                       _states(kotlin_knob="нет", check_code="нет"),
                       run_name=RUN)
    assert row["form"] == vcr.FORM_KIND
    assert (row["section"], row["record"]) == ("families", "probe_family")
    assert row["kinds"] == ["kotlin_knob", "check_code"]
    assert row["key"] == law.kind_request_key(
        "families", "probe_family", ["kotlin_knob", "check_code"])
    assert row["request_id"] == law.request_id_of(row["key"])
    assert row["state"] is None and row["existing"] is None
    assert "заявка на вид исполнителя" in row["words"]
    assert "families/probe_family" in row["words"]


def test_вид_с_исполнителем_в_заявку_не_просится(tmp_path, monkeypatch):
    """Заявка просит РОВНО недостающие виды: вид, который уже исполняет
    хоть один продукт, в набор не попадает — иначе владелец решал бы по
    заявке о том, что уже сделано (и ключ был бы другой)."""
    cat = _catalog(tmp_path, monkeypatch, families=PROBE)
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, RULE, cat,
                       _states(kotlin_knob="нет", check_code="есть"),
                       run_name=RUN)
    assert row["kinds"] == ["kotlin_knob"]
    assert row["request_id"] == law.request_id_of(law.kind_request_key(
        "families", "probe_family", ["kotlin_knob"]))
    # Чужой продукт с исполнителем — тоже исполнитель (клетка общая).
    row2 = vcr.classify(
        {"kind": "coverage", "rule_id": "R-PROBE"}, RULE, cat,
        {"families": {"probe_family": {"kotlin_knob": {"CDPM": "есть"},
                                       "check_code": {"COSMOS": "нет"}}}},
        run_name=RUN)
    assert row2["kinds"] == ["check_code"]


def test_открытая_заявка_по_записи_судится_первой(tmp_path, monkeypatch):
    """Пока по записи открыта заявка ЛЮБОГО подателя, вторая не
    рождается: кнопка называет номер, состояние и подателя. Ключ при
    этом не считается — номер уже есть, и он чужой."""
    rq = _request("req-00000000000000000042", state="принята", by="COSMOS",
                  kinds=["kotlin_knob"])
    cat = _catalog(tmp_path, monkeypatch, families=PROBE, requests=[rq])
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, RULE, cat,
                       _states(kotlin_knob="нет", check_code="нет"),
                       run_name=RUN)
    assert row["form"] == vcr.FORM_KIND
    assert row["request_id"] == "req-00000000000000000042"
    assert row["state"] == "принята"
    assert row["existing"]["submitted_by"] == "COSMOS"
    assert row["key"] is None, "ключ не считается — заявка уже есть"
    for word in ("пробел уже заявлен", "принята", "COSMOS", "kotlin_knob"):
        assert word in row["words"]
    assert row["link"] == "#/requests/req-00000000000000000042"


def test_закрытая_заявка_открытию_новой_не_мешает(tmp_path, monkeypatch):
    """«Отклонена» и «есть» — состояния закрытые: пробел, увиденный
    снова, заявляется заново (пересмотр — решение владельца, а молчание
    кнопки было бы потерей пробела)."""
    cat = _catalog(tmp_path, monkeypatch, families=PROBE,
                   requests=[_request("req-00000000000000000042",
                                      state="отклонена", kinds=["kotlin_knob"])])
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, RULE, cat,
                       _states(kotlin_knob="нет", check_code="нет"))
    assert row["existing"] is None and row["key"] is not None


def test_все_виды_есть_дефект_кода_а_не_пробел_каталога(tmp_path, monkeypatch):
    """Третья строка таблицы: запись полна, а решатель всё равно отказал
    — это дефект исполнителя. Заявка каталога НЕ подаётся, и сказано
    почему."""
    cat = _catalog(tmp_path, monkeypatch, families=PROBE)
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, RULE, cat,
                       _states(kotlin_knob="есть", check_code="есть"))
    assert row["form"] == vcr.FORM_DEFECT
    assert row["request_id"] is None
    assert "пробел не в каталоге, а в коде исполнителя" in row["words"]


def test_запись_без_объявленных_видов_ведёт_на_правку(tmp_path, monkeypatch):
    """Заявка на вид допустима только из ОБЪЯВЛЕННЫХ записью видов
    (R02 читателя): запись, не объявившая ни одного, закрывается
    правкой записи на экране службы, а не заявкой с пустым набором
    (у такой заявки ключ считался бы от пустоты).

    Такой каталог дверь не пропустит (схема требует непустой
    executor_kinds) — потому здесь читатель без проверки: сторож стоит
    на СБОРКЕ заявки, а не только на входе каталога."""
    import template_catalog as tc
    folder = cc.copy_catalog(
        tmp_path, families={"probe_family": dict(PROBE["probe_family"])})
    doc = json.loads((folder / "families.json").read_text(encoding="utf-8"))
    for rec in doc["families"]:
        if rec["id"] == "probe_family":
            rec["executor_kinds"] = []
    (folder / "families.json").write_bytes(dump_json(doc).encode("utf-8"))
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, RULE,
                       tc.load(folder), {"families": {"probe_family": {}}})
    assert row["form"] == vcr.FORM_EDIT
    assert row["link"] == "#/records/families/probe_family#executors"


def test_семья_находится_по_словарю_параметров_того_же_каталога(tmp_path,
                                                                 monkeypatch):
    """Правило без посадки (свод до пакета посадок): семья ищется тем же
    порядком, что у вывода реестра — `export_solver.family`, затем
    словарь параметров. Словарь берётся из ТОГО ЖЕ экземпляра каталога,
    что и записи: на временном каталоге гейта проекции обёртки назвали
    бы семью каталога двери."""
    cat = _catalog(tmp_path, monkeypatch)
    by_param = dict(RULE, template_fit=None,
                    params=[{"name": "max_duty_duration", "value": 720}])
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, by_param,
                       cat, _states("duty_limits", kotlin_knob="нет"),
                       run_name=RUN)
    assert (row["form"], row["record"]) == (vcr.FORM_KIND, "duty_limits")
    # export_solver.family первичнее словаря параметров.
    legacy = dict(by_param, export_solver={"family": "probe_family"})
    cat2 = _catalog(tmp_path, monkeypatch, families=PROBE, name="второй")
    row2 = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, legacy,
                        cat2, _states(kotlin_knob="нет", check_code="нет"),
                        run_name=RUN)
    assert row2["record"] == "probe_family"


def test_снятая_запись_считается_отсутствующей(tmp_path, monkeypatch):
    """Запись со статусом «снята» никого не исполняет: пробел по ней —
    пробел БЕЗ записи (новая запись), а не заявка на вид снятой."""
    cat = _catalog(tmp_path, monkeypatch,
                   families={"probe_family": dict(PROBE["probe_family"],
                                                  status="снята")})
    row = vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, RULE, cat,
                       _states(kotlin_knob="нет", check_code="нет"),
                       run_name=RUN)
    assert row["form"] == vcr.FORM_NEW


def test_пробел_без_записи_называет_номер_и_ссылку(tmp_path, monkeypatch):
    """Пятая строка таблицы: посадка категории C и родня — новая запись.
    COSMOS черновика не составляет: считает номер по закону и ведёт на
    ручную подачу ключом, чтобы номер службы сошёлся с номером кнопки."""
    cat = _catalog(tmp_path, monkeypatch)
    rule = dict(RULE, template_fit={"category": "C", "gap": "не ложится"})
    row = vcr.classify({"kind": "rule_no_template", "rule_id": "R-PROBE",
                        "note": "правило не ложится в базу шаблонов"},
                       rule, cat, {}, run_name=RUN)
    assert row["form"] == vcr.FORM_NEW
    key = law.request_key("families", "HARD", "PAIRING",
                          "правило образец сторожа закона")
    assert row["key"] == key
    assert row["request_id"] == "req-04763385720803207704"
    assert row["state"] is None
    assert "из CDPM" in row["words"] and "вручную" in row["words"]
    assert row["link"].startswith("#/requests/new?")
    assert "key=" + key in row["link"]
    assert "run-0031%23" + key[:12] in row["link"], \
        "ссылка источника — имя прогона и 12 знаков ключа, не текст правила"
    assert "правило образец" not in row["link"]


@pytest.mark.parametrize("kind", ["family_unknown", "rule_family_unknown",
                                  "param_unknown", "rule_param_unknown",
                                  "no_mechanic", "rule_no_mechanic",
                                  "rule_unknown", "rule_covered_partial",
                                  "rule_no_template"])
def test_вердикты_реестра_без_записи_дают_ту_же_новую_заявку(
        tmp_path, monkeypatch, kind):
    """Пробелы разных рубежей об одном правиле дают ОДИН номер: закон
    считает от механики, а не от того, кто первым заметил. Вердикт
    реестра приходит и своим именем, и с приставкой записи от глубокой
    сверки — оба написания обязаны вести в одну заявку."""
    cat = _catalog(tmp_path, monkeypatch)
    row = vcr.classify({"kind": kind, "rule_id": "R-PROBE", "note": "…"},
                       dict(RULE, template_fit=None), cat, {}, run_name=RUN)
    assert row["form"] == vcr.FORM_NEW
    assert row["request_id"] == "req-04763385720803207704"


def test_пробел_без_записи_уже_заявлен_из_прогона_cdpm(tmp_path, monkeypatch):
    """Заявка CDPM (посадка категории C) уже в каталоге — кнопка её
    находит по номеру и называет состояние, а не заводит дубль."""
    live = _catalog(tmp_path, monkeypatch, families=PROBE, name="исходный")
    rq = _request("req-04763385720803207704", state="принята", by="CDPM",
                  record_id="family_421af21358a4", kinds=None,
                  draft=_draft_from(live, "probe_family",
                                    "family_421af21358a4"))
    cat = _catalog(tmp_path, monkeypatch, requests=[rq])
    row = vcr.classify({"kind": "rule_no_template", "rule_id": "R-PROBE"},
                       dict(RULE, template_fit={"category": "C",
                                                "gap": "не ложится"}),
                       cat, {}, run_name=RUN)
    assert row["form"] == vcr.FORM_NEW
    assert row["state"] == "принята"
    assert "пробел уже заявлен из прогона CDPM" in row["words"]
    assert row["link"] == "#/requests/req-04763385720803207704"


def test_критерий_без_исполнителя_даёт_заявку_на_вид(tmp_path, monkeypatch):
    """Седьмая строка таблицы: запись критерия есть, вид объявлен,
    исполнителя нет — заявка на вид раздела criteria."""
    cat = _catalog(tmp_path, monkeypatch)
    row = vcr.classify({"kind": "criterion_no_executor",
                        "rule_id": "min_person_days",
                        "note": "исполнителя нет"},
                       {"kind": "min_person_days"}, cat,
                       _states("min_person_days", section="criteria",
                               builder_method="нет"), run_name=RUN)
    assert row["form"] == vcr.FORM_KIND
    assert (row["section"], row["record"]) == ("criteria", "min_person_days")
    assert row["kinds"] == ["builder_method"]
    assert row["request_id"] == law.request_id_of(law.kind_request_key(
        "criteria", "min_person_days", ["builder_method"]))


def test_критерий_без_вида_целевой_ведёт_на_правку_записи(tmp_path, monkeypatch):
    """Четвёртая строка таблицы: запись критерия есть, но вида, которым
    COSMOS считает целевую, она не объявила — правка записи (старшая
    версия), заявкой на вид это невозможно по R02."""
    cat = _catalog(tmp_path, monkeypatch)
    row = vcr.classify({"kind": "criterion_unknown",
                        "rule_id": "uncovered_seats"},
                       {"kind": "uncovered_seats"}, cat, {}, run_name=RUN)
    assert row["form"] == vcr.FORM_EDIT
    assert "builder_method" in row["words"]
    assert row["link"] == "#/records/criteria/uncovered_seats#executors"


def test_неизвестный_критерий_новая_запись_с_зоной_задачи(tmp_path, monkeypatch):
    """Критерия нет в каталоге — новая запись раздела criteria; зона
    берётся из задачи нажатия (другой у неё взяться неоткуда)."""
    cat = _catalog(tmp_path, monkeypatch)
    row = vcr.classify({"kind": "criterion_unknown", "rule_id": "0096",
                        "title": "вид критерия вне словаря"},
                       {"kind": "0096", "statement": "нормированная стоимость"},
                       cat, {}, run_name=RUN, task="pairing")
    assert row["form"] == vcr.FORM_NEW and row["section"] == "criteria"
    assert row["key"] == law.request_key("criteria", "0096", "PAIRING",
                                         "нормированная стоимость")


@pytest.mark.parametrize("kind", list(vcr.NOT_CATALOG_GAPS))
def test_брак_свода_и_среда_заявкой_не_закрываются(tmp_path, monkeypatch, kind):
    """Девятая строка таблицы: две правды о семье, кривая посадка,
    устаревшая пометка, дрейф кода и недоступный каталог — не пробелы
    каталога; слова остановки остаются прежними."""
    cat = _catalog(tmp_path, monkeypatch, families=PROBE)
    row = vcr.classify({"kind": kind, "rule_id": "R-PROBE",
                        "note": "прежние слова остановки"}, RULE, cat,
                       _states(kotlin_knob="нет", check_code="нет"))
    assert row["form"] == vcr.FORM_NONE
    assert row["words"] == "прежние слова остановки"
    assert row["request_id"] is None


def test_неизвестный_вид_пробела_называется_словами(tmp_path, monkeypatch):
    """Молчания нет: вид пробела, которого классификатор не знает,
    назван вслух — иначе новый рубеж тихо терял бы заявки.

    Приставка «rule_» при этом означает «пробел о правиле» и разбирается
    (перечень вердиктов реестра открыт — реестр файл); без неё
    неизвестное слово — молчаливая потеря, и её нет."""
    cat = _catalog(tmp_path, monkeypatch, families=PROBE)
    row = vcr.classify({"kind": "новый_рубеж", "rule_id": "R-PROBE"}, RULE,
                       cat, _states(kotlin_knob="нет"))
    assert row["form"] == vcr.FORM_NONE
    assert "классификатору неизвестен" in row["words"]
    # Вердикт реестра, которого сегодня не выводит ни один код, приходит
    # приставкой — заявка на вид, а не потеря.
    row2 = vcr.classify({"kind": "rule_вердикт_из_чужого_реестра",
                         "rule_id": "R-PROBE"}, RULE, cat,
                        _states(kotlin_knob="нет", check_code="нет"))
    assert row2["form"] == vcr.FORM_KIND


def test_правило_без_формулировки_не_даёт_номера(tmp_path, monkeypatch):
    """Правила нет в модели пакета (брак согласованности отчёта
    покрытия и ВМП) — номер не считается, и это сказано словами, а не
    выдумано из пустой строки."""
    cat = _catalog(tmp_path, monkeypatch)
    row = vcr.classify({"kind": "coverage", "rule_id": "R-GHOST",
                        "note": "правила нет в модели пакета"}, None, cat, {})
    assert row["form"] == vcr.FORM_NONE and row["request_id"] is None
    assert "формулировки" in row["words"]


def test_перечень_пробелов_судится_одним_экземпляром(tmp_path, monkeypatch):
    """classify_gaps берёт машинную форму правила из модели пакета по
    идентификатору и отдаёт строки артефакта; проекция артефакта не
    несёт ни ключа, ни подробностей чужой заявки."""
    cat = _catalog(tmp_path, monkeypatch, families=PROBE)
    rows = vcr.classify_gaps(
        [{"kind": "coverage", "rule_id": "R-PROBE"},
         {"kind": "fit_invalid", "rule_id": "R-BAD", "note": "кривая посадка"}],
        model={"rules": [RULE]}, cat=cat,
        states=_states(kotlin_knob="нет", check_code="нет"), run_name=RUN)
    assert [r["form"] for r in rows] == [vcr.FORM_KIND, vcr.FORM_NONE]
    art = vcr.artifact_rows(rows)
    assert set(art[0]) == set(vcr.ARTIFACT_KEYS)
    assert vcr.requests_of(rows)["R-BAD"] is None
    assert vcr.requests_of(rows)["R-PROBE"]["request_id"] == rows[0]["request_id"]


# ═══════════════════════════════════════════════ 3. Очередь и подача

def _kind_row(tmp_path, monkeypatch, **kw):
    cat = _catalog(tmp_path, monkeypatch, families=PROBE, **kw)
    return vcr.classify({"kind": "coverage", "rule_id": "R-PROBE"}, RULE, cat,
                        _states(kotlin_knob="нет", check_code="нет"),
                        run_name=RUN)


def test_подача_пишет_файл_очереди_формой_каталога(tmp_path, monkeypatch):
    """Файл очереди — 11 полей схемы, форма файлов каталога (LF,
    отсортированные ключи), имя — номер заявки."""
    row = _kind_row(tmp_path, monkeypatch)
    receipt = vcr.submit_kind_requests([row], run_name=RUN,
                                       out_dir=tmp_path / "button", auto=True)
    path = tmp_path / "queue" / "outbox" / (row["request_id"] + ".json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == {"request_id", "state", "section", "record_id",
                        "submitted_at", "submitted_by", "source", "draft",
                        "executor_kinds_requested", "decision", "cr_key"}
    assert doc["state"] == "подана" and doc["submitted_by"] == "COSMOS"
    assert doc["draft"] is None and doc["cr_key"] is None
    assert doc["executor_kinds_requested"] == ["kotlin_knob", "check_code"]
    assert doc["source"] == {"kind": "пробел при нажатии",
                             "document_class": None,
                             "run_class": "прогон COSMOS",
                             "ref": RUN + "#" + row["key"][:12]}
    assert path.read_bytes() == dump_json(doc).encode("utf-8"), \
        "форма файла — та же, что у файлов каталога"
    item = receipt["rows"]["R-PROBE"]
    assert item["state"] == "подана" and item["delivered"] is False
    assert "подана" in item["words"] and "доставка — не удалась" in item["words"]


def test_повтор_нажатия_файлы_не_переписывает(tmp_path, monkeypatch):
    """Тот же пакет, то же нажатие второй раз — тот же номер и ТОТ ЖЕ
    файл: очередь идемпотентна по построению закона ключа."""
    row = _kind_row(tmp_path, monkeypatch)
    out = tmp_path / "button"
    vcr.submit_kind_requests([row], run_name=RUN, out_dir=out, auto=True)
    path = tmp_path / "queue" / "outbox" / (row["request_id"] + ".json")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    receipt = vcr.submit_kind_requests([row], run_name="run-0032", out_dir=out,
                                       auto=True)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert "уже подана" in receipt["rows"]["R-PROBE"]["words"]


def test_файл_победитель_с_иным_набором_видов_называет_прогон(tmp_path,
                                                              monkeypatch):
    """Одновременные нажатия ловятся исключительным созданием файла:
    проигравший читает победителя и говорит, из какого прогона он подан
    (дубль не заводится)."""
    row = _kind_row(tmp_path, monkeypatch)
    outbox = tmp_path / "queue" / "outbox"
    outbox.mkdir(parents=True)
    winner = vcr.request_document(row, run_name="run-0088")
    winner["executor_kinds_requested"] = ["kotlin_knob"]
    (outbox / (row["request_id"] + ".json")).write_bytes(
        dump_json(winner).encode("utf-8"))
    receipt = vcr.submit_kind_requests([row], run_name=RUN,
                                       out_dir=tmp_path / "button", auto=True)
    words = receipt["rows"]["R-PROBE"]["words"]
    assert "иным набором видов из run-0088" in words
    assert receipt["rows"]["R-PROBE"]["delivered"] is False


def test_автоподача_выключена_подача_кнопкой(tmp_path, monkeypatch):
    """`CREW_CATALOG_REQUESTS_AUTO=0` — файлов очереди нет вовсе, и
    квитанция говорит, чем подавать."""
    row = _kind_row(tmp_path, monkeypatch)
    receipt = vcr.submit_kind_requests([row], run_name=RUN,
                                       out_dir=tmp_path / "button")
    assert not (tmp_path / "queue" / "outbox").exists()
    assert "кнопкой «Заявить в каталог»" in receipt["rows"]["R-PROBE"]["words"]


def test_квитанция_ложится_в_папку_артефактов_и_копится(tmp_path, monkeypatch):
    """Квитанция нажатия — рядом с прочими артефактами; вторая подача
    того же нажатия (другой рубеж) дописывает строки, а не стирает."""
    row = _kind_row(tmp_path, monkeypatch)
    out = tmp_path / "button"
    vcr.submit_kind_requests([row], run_name=RUN, out_dir=out, auto=True)
    other = dict(row, rule_id="R-OTHER", form=vcr.FORM_DEFECT, key=None,
                 request_id=None, words="все виды записи уже «есть»")
    doc = json.loads((out / vcr.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert doc["form"] == 1 and doc["run"] == RUN
    vcr.submit_kind_requests([other], run_name=RUN, out_dir=out, auto=True)
    doc = json.loads((out / vcr.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert set(doc["rows"]) == {"R-PROBE", "R-OTHER"}
    assert doc["rows"]["R-OTHER"]["form"] == vcr.FORM_DEFECT


def test_заявка_подаётся_только_по_пробелу_относительно_записи(tmp_path,
                                                               monkeypatch):
    """Новая запись, правка и дефект в очередь не уходят — их закрывают
    CDPM, экран службы и код; попытка собрать запись заявки из такой
    строки отказывает словами."""
    cat = _catalog(tmp_path, monkeypatch)
    row = vcr.classify({"kind": "rule_no_template", "rule_id": "R-PROBE"},
                       dict(RULE, template_fit={"category": "C", "gap": "…"}),
                       cat, {}, run_name=RUN)
    with pytest.raises(vcr.CatalogRequestError):
        vcr.request_document(row, run_name=RUN)
    receipt = vcr.submit_kind_requests([row], run_name=RUN,
                                       out_dir=tmp_path / "button", auto=True)
    assert not list((tmp_path / "queue").rglob("*.json"))
    assert receipt["rows"]["R-PROBE"]["form"] == vcr.FORM_NEW


# ═══════════════════════════════════════════════ 4. Клиент службы

class _Fake:
    """Подставной urlopen: очередь ответов, счёт вызовов."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, req, timeout=None):
        self.calls.append({"url": req.full_url, "method": req.get_method(),
                           "headers": dict(req.header_items()),
                           "body": req.data})
        answer = self.answers.pop(0) if self.answers else self.answers
        if isinstance(answer, Exception):
            raise answer
        return answer


class _Resp:
    def __init__(self, status, doc):
        self.status = status
        self._body = json.dumps(doc, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, detail):
    return urllib.error.HTTPError(
        SERVICE_URL + "/api/requests", code, "no", {},
        io.BytesIO(json.dumps({"detail": detail},
                              ensure_ascii=False).encode("utf-8")))


@pytest.fixture()
def channel(monkeypatch):
    monkeypatch.setenv(vcr.ENV_API_URL, SERVICE_URL)
    monkeypatch.setenv(vcr.ENV_TOKEN, "секрет-канала")


def _doc():
    return {"request_id": "req-03707195193482806055", "state": "подана",
            "executor_kinds_requested": ["kotlin_knob"]}


def test_канал_без_настроек_молчать_не_имеет_права(tmp_path):
    """Адреса нет — доставки нет, и это СЛОВА, а не тихий успех."""
    res = vcr.service_client.submit(_doc())
    assert res["ok"] is False
    assert vcr.ENV_API_URL in res["words"]


def test_подача_несёт_заголовки_продукта_и_токена(monkeypatch, channel):
    fake = _Fake([_Resp(201, {"request_id": "req-03707195193482806055",
                              "state": "подана"})])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    res = vcr.service_client.submit(_doc())
    assert res["ok"] is True and res["state"] == "подана"
    call = fake.calls[0]
    assert call["url"] == SERVICE_URL + "/api/requests"
    assert call["method"] == "POST"
    heads = {k.lower(): v for k, v in call["headers"].items()}
    assert heads["X-catalog-product".lower()] == "COSMOS"
    assert heads["X-catalog-token".lower()] == "секрет-канала"
    assert json.loads(call["body"].decode("utf-8")) == _doc()


def test_повторная_подача_того_же_номера_уже_подана(monkeypatch, channel):
    """200 — идемпотентность закона ключа: не отказ, а «уже подана»."""
    monkeypatch.setattr(urllib.request, "urlopen",
                        _Fake([_Resp(200, {"request_id": "req-1",
                                           "state": "принята",
                                           "note": "уже подана"})]))
    res = vcr.service_client.submit(_doc())
    assert res["ok"] is True and "уже подана" in res["words"]


def test_служба_занята_записью_три_попытки(monkeypatch, channel):
    """409 write-busy и сеть — замок и соединение, а не содержание:
    три попытки, потом слова."""
    fake = _Fake([_http_error(409, {"code": "write-busy",
                                    "words": "служба занята записью"}),
                  _http_error(409, {"code": "write-busy",
                                    "words": "служба занята записью"}),
                  _Resp(201, {"request_id": "req-1", "state": "подана"})])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    res = vcr.service_client.submit(_doc(), sleep=lambda *_: None)
    assert res["ok"] is True and len(fake.calls) == 3


def test_сеть_три_попытки_потом_слова(monkeypatch, channel):
    fake = _Fake([urllib.error.URLError("соединение отказано")] * 3)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    res = vcr.service_client.submit(_doc(), sleep=lambda *_: None)
    assert res["ok"] is False and len(fake.calls) == 3
    assert "служба каталога недоступна" in res["words"]


def test_прочий_отказ_не_повторяется_и_несёт_проблемы(monkeypatch, channel):
    """422 — о содержании заявки: повтор бессмыслен, слова службы и её
    проблемы едут человеку целиком."""
    fake = _Fake([_http_error(422, {"code": "invalid-request",
                                    "words": "заявка не по правилам",
                                    "problems": [{"message": "A02: маркер"}]})])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    res = vcr.service_client.submit(_doc(), sleep=lambda *_: None)
    assert res["ok"] is False and len(fake.calls) == 1
    assert "заявка не по правилам" in res["words"]
    assert "A02: маркер" in res["words"]


def test_ключ_cr_и_сеанс_службы(monkeypatch, channel):
    fake = _Fake([_Resp(200, {"mode": "edit",
                              "catalog": {"fingerprint": "a" * 64}}),
                  _Resp(200, {"state": "исполнитель заявлен"})])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert vcr.service_client.session() == {"mode": "edit",
                                            "fingerprint": "a" * 64}
    res = vcr.service_client.put_cr_key("req-03707195193482806055",
                                        "cr575b6c6a")
    assert res["ok"] is True and res["state"] == "исполнитель заявлен"
    assert fake.calls[1]["method"] == "PUT"
    assert fake.calls[1]["url"].endswith(
        "/api/requests/req-03707195193482806055/cr_key")


def test_доставка_очереди_повторная(tmp_path, monkeypatch, channel):
    """Заявка родилась при недоступной службе — файл лежит в очереди без
    ответа; повторная доставка отдаёт его и кладёт рядом ответ, после
    чего очередь пуста."""
    row = _kind_row(tmp_path, monkeypatch)
    vcr.submit_kind_requests([row], run_name=RUN, out_dir=tmp_path / "button",
                             auto=True, sleep=lambda *_: None)
    outbox = tmp_path / "queue" / "outbox"
    assert len(vcr.pending(outbox)) == 1
    monkeypatch.setattr(urllib.request, "urlopen",
                        _Fake([_Resp(201, {"request_id": row["request_id"],
                                           "state": "подана"})]))
    out = vcr.deliver_outbox(outbox, sleep=lambda *_: None)
    assert [d["request_id"] for d in out["delivered"]] == [row["request_id"]]
    assert out["pending"] == 0 and not out["failed"]
    assert vcr.reply_path(outbox, row["request_id"]).is_file()
    # Повтор пропускает доставленное.
    assert vcr.deliver_outbox(outbox)["delivered"] == []


# ═══════════════════════════════════════════ 5. Сторож данных заказчика

#: Ключи, которых в файле очереди быть не может: они несут текст правила
#: авиакомпании либо ссылку на её документ.
FORBIDDEN_KEYS = ("rule", "statement", "sources", "quote")


def _keys(doc, out=None):
    out = set() if out is None else out
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.add(str(k))
            _keys(v, out)
    elif isinstance(doc, list):
        for v in doc:
            _keys(v, out)
    return out


def test_сторож_данных_заказчика_в_файлах_очереди(tmp_path, monkeypatch,
                                                  capsys):
    """P-N-SHD: в очередь не уезжает ни ключей с текстом правила, ни
    маркеров заказчика. Перечень маркеров — свой, из tmp_path: гейт
    судит форму файла, а не наличие перечня на машине."""
    markers = ["ЗАО Пример", "PRM", "Иванов"]
    (tmp_path / "markers.json").write_text(
        json.dumps({"checks": [{"kind": "substring", "values": markers}]},
                   ensure_ascii=False), encoding="utf-8")
    rule = dict(RULE, statement="Экипаж ЗАО Пример: Иванов, рейс PRM-101",
                sources=[{"document": "ЗАО Пример, ПРД-2026", "quote": "…"}])
    cat = _catalog(tmp_path, monkeypatch, families=PROBE)
    rows = vcr.classify_gaps([{"kind": "coverage", "rule_id": "R-PROBE"}],
                             model={"rules": [rule]}, cat=cat,
                             states=_states(kotlin_knob="нет",
                                            check_code="нет"),
                             run_name=RUN)
    vcr.submit_kind_requests(rows, run_name=RUN, out_dir=tmp_path / "button",
                             auto=True, sleep=lambda *_: None)
    files = sorted((tmp_path / "queue").rglob("*.json")) \
        + [tmp_path / "button" / vcr.RECEIPT_NAME]
    assert files, "сторожу нечего судить — подача не состоялась"
    for path in files:
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
        assert not (_keys(doc) & set(FORBIDDEN_KEYS)), \
            f"{path.name}: ключ с текстом правила авиакомпании"
        for marker in markers:
            assert marker not in text, f"{path.name}: маркер {marker!r}"
    print("сторож заявок: рассмотрено файлов %d, маркеров %d"
          % (len(files), len(markers)))
    assert "рассмотрено файлов" in capsys.readouterr().out


def test_за_время_подачи_под_каталогом_ни_одной_записи(tmp_path, monkeypatch):
    """Продукт пишет только свою очередь: разделы каталога — зона
    службы, и ни один путь подачи туда не ведёт."""
    row = _kind_row(tmp_path, monkeypatch)
    before = _snapshot(Path(ca.catalog_path()))
    vcr.submit_kind_requests([row], run_name=RUN,
                             out_dir=tmp_path / "button", auto=True,
                             sleep=lambda *_: None)
    assert _snapshot(Path(ca.catalog_path())) == before
