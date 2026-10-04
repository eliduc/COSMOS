# -*- coding: utf-8 -*-
"""Внешний аудит 04.09.2026, A7 (очередь 3, ступень M) — заявление
«история сотрудника не передана» в выдаче и сертификате.

Стенды с известным ответом:
  1. правила семей накопителей / полов отдыха / серий / выравнивания в
     зоне задачи перечислены поимённо, допущение «остаток = 0» названо;
     чужая зона и правила без семьи — не в перечне;
  2. раздел history в манифесте — «история передана», допущения нет;
  3. кнопка кладёт заявление в выдачу и в сертификат (обе задачи);
  4. НОСИТЕЛЬ перечня семей истории ОДИН (каталог 0.5.0, 11.09.2026):
     поле записи семьи `reads_history {need, kind}`. Ни природа записи,
     ни формы решателя, ни рукописный остаток границы горизонта
     перечень не строят; ожидание в тестах — тоже ПОЛЕ, а не список
     имён, и синтетическая запись с полем входит без правки кода;
  5. допущение и заявление разложены ПО ВИДУ чтения истории (блоки
     `reads`): семьи вида названы поимённо, потребность — текстом
     записи. Та же раскладка у анкеты CDPM (`Q-3b-история-<kind>`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp import catalog_access as ca                        # noqa: E402
from vmp import history_assumption as ha                    # noqa: E402
from vmp import kotlin_tables as ktab                       # noqa: E402
from vmp.run import logic_checks as lgc                     # noqa: E402
from tests import catalog_copies as cc                      # noqa: E402
from tests.test_run_button import (FakeSolver, _doc, _fakes,   # noqa: E402
                                   _pairing_fakes_min, _run_dir, btn)


def _model():
    return {"rules": [
        {"id": "R-1", "title": "месячный налёт", "zone": "ROSTERING",
         "kind": "HARD", "template_fit": {"category": "A",
                                          "family": "span_accumulators"}},
        {"id": "R-2", "title": "отдых перед сменой", "zone": "BOTH",
         "kind": "HARD", "export_solver": {"family": "rest_floors"}},
        {"id": "R-3", "title": "выходные подряд", "zone": "ROSTERING",
         "kind": "SOFT", "export_solver": {"family": "calendar_windows"}},
        {"id": "R-4", "title": "стыковка", "zone": "PAIRING",
         "kind": "HARD", "export_solver": {"family": "connection_windows"}},
        {"id": "R-5", "title": "справка", "zone": "ROSTERING",
         "kind": "INFO", "export_solver": {"family": "span_accumulators"}},
        {"id": "R-6", "title": "без семьи", "zone": "ROSTERING",
         "kind": "HARD"}]}


def test_statement_lists_history_dependent_rules_of_the_task_zone():
    st = ha.statement(_model(), {"spec_version": "1.16"}, "rostering")
    h = st["history"]
    assert h["passed"] is False and h["section"] is None
    assert [r["rule_id"] for r in h["rules"]] == ["R-1", "R-2", "R-3"]
    assert h["families"] == ["calendar_windows", "rest_floors",
                             "span_accumulators"]
    assert h["assumption"] == "остаток на начало периода = 0"
    assert "R-1, R-2, R-3" in h["note"] and "допущение" in h["note"]
    # зона пейринга: у цепочек людей нет — зависимых правил нет, остатки
    # судятся на ростеринге (замок и допущение — только у ростеринга)
    p = ha.statement(_model(), None, "pairing")["history"]
    assert p["rules"] == [] and p["assumption"] is None
    assert "судятся на ростеринге" in p["note"]


def test_statement_without_dependent_rules_is_harmless_and_says_so():
    h = ha.statement({"rules": [{"id": "R-4", "zone": "ROSTERING",
                                 "kind": "HARD",
                                 "export_solver": {"family": "crew_composition"}}]},
                     None, "rostering")["history"]
    assert h["rules"] == [] and h["assumption"] is None
    assert "ни на что не влияет" in h["note"]


def test_history_section_in_manifest_switches_the_statement():
    h = ha.statement(_model(), {"history": {"employees": []}}, "rostering")["history"]
    assert h["passed"] is True and h["section"] == "history"
    assert h["assumption"] is None and "передана" in h["note"]


def test_button_puts_the_statement_into_delivery_and_certificate(tmp_path,
                                                                 monkeypatch):
    # Реестр исполнителей при нажатии — как в стендах кнопки: фикстура
    # закрывает все правила свода живыми отпечатками кода.
    from vmp import registry_check as _regc
    root = Path(btn.__file__).resolve().parent.parent.parent

    def _fake_press_registry(run_dir, model, task):
        reg = {"created": "тест", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)
    # ростеринг
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                        **_fakes(True))
    assert d["verdict"] == "допустимо", d
    h = d["assumptions"]["history"]
    assert h["task"] == "rostering" and h["passed"] is False
    cert = json.loads((run / "button" / "certificate.json")
                      .read_text(encoding="utf-8"))
    assert cert["certificate"]["assumptions"]["history"]["note"] == h["note"]
    # пейринг
    run2, fakes = _pairing_fakes_min(tmp_path / "p")
    d2 = btn.orchestrate(run2, FakeSolver(), task="pairing",
                         sleep=lambda *_: None, **fakes)
    assert d2["verdict"] == "пейринг построен", d2
    assert d2["assumptions"]["history"]["task"] == "pairing"


# ------------------------------------------- носитель семей истории
#: Пробные записи копии каталога: имена нарочно не похожи на семьи
#: продукта — сторож судит ПРИЗНАК записи, а не её имя.
PROBE_HISTORY = "probe_history_family"
PROBE_SCHEDULE = "probe_schedule_family"
#: Потребность пробной записи в истории — текстом, как её пишет каталог.
ПРОБНАЯ_ПОТРЕБНОСТЬ = "серия пробных смен до горизонта"


@pytest.fixture
def дверь_каталога(monkeypatch):
    """Каталог — тот, что назван переменной двери; после теста дверь и
    кэш перечня забывают экземпляр, иначе tmp-копия уехала бы в соседний
    файл."""
    monkeypatch.delenv(ca.CATALOG_ENV, raising=False)
    ca.forget()
    ha.forget()
    yield
    ca.forget()
    ha.forget()


def _по_полю() -> set:
    """Ожидание ПО ПОЛЮ ЗАПИСИ, а не списком имён: семьи каталога, у
    которых задано `reads_history`. Список имён здесь был бы тем же
    рукописным перечнем, который чинится."""
    meta = ca.current().families_meta()
    return {str(fid) for fid, m in meta.items()
            if isinstance(m.get("reads_history"), dict)}


def test_семьи_истории_приходят_от_поля_записи(дверь_каталога):
    """Множество носителя = множеству записей каталога с полем
    `reads_history`; ни природы, ни форм решателя, ни рукописного
    остатка границы горизонта в нём больше нет. У каждой семьи есть
    слова: заявление называет семью и её потребность, а не код."""
    got = ha.history_families()
    want = _по_полю()
    assert set(got) == want, sorted(set(got) ^ want)
    assert want, "семей с полем чтения истории нет — сторож слеп"
    assert all(got[f].strip() for f in got), "семья без слов заявления"
    reads = ha.history_reads()
    assert set(reads) == want
    for fid, rec in reads.items():
        assert rec["kind"], fid
        assert rec["need"], fid
        assert rec["need"] in got[fid], fid


def test_семья_без_поля_допущения_не_получает(дверь_каталога):
    """Носитель один: запись без `reads_history` в перечень не входит,
    какой бы природы она ни была. Обратное правило (природа «история» ⇒
    поле задано) держит сам каталог — X19."""
    meta = ca.current().families_meta()
    got = ha.history_families()
    без_поля = {str(f) for f, m in meta.items()
                if not isinstance(m.get("reads_history"), dict)}
    assert без_поля, "все записи каталога читают историю — сторож слеп"
    assert not (без_поля & set(got))
    природа = {str(f) for f, m in meta.items()
               if m.get("nature") == ha.HISTORY_NATURE}
    assert природа <= set(got), "семья природы «история» без поля чтения"


def test_стороны_закона_паритета_дают_одно_множество(дверь_каталога):
    """Три стороны про ПРИРОДУ дают одно множество: природа записи
    каталога, таблица решателя (`LogicModel.HISTORY_FIT_FAMILIES`) и
    формы проверок. Поле `reads_history` ШИРЕ природы: историю читают и
    семьи границы горизонта, чья механика — расписание (их природа
    верна: правило судит расписание, а поле говорит о чтении прямо)."""
    meta = ca.current().families_meta()
    природа = {str(f) for f, m in meta.items()
               if m.get("nature") == ha.HISTORY_NATURE}
    решатель = set(ktab.history()["families"])
    формы = set(lgc.FAMILY_FORMS)
    assert природа == решатель == формы, (
        "стороны разошлись: каталог %s, решатель %s, формы %s"
        % (sorted(природа), sorted(решатель), sorted(формы)))
    assert природа < _по_полю(), (
        "поле чтения истории не шире природы — семьи границы горизонта "
        "потеряны: %s" % sorted(природа ^ _по_полю()))


def test_синтетическая_семья_истории_входит_без_правки_кода(
        tmp_path, monkeypatch, дверь_каталога):
    """Запись с полем `reads_history`, дописанная в копию каталога,
    попадает в перечень и в заявление сама — правки кода для этого не
    нужно (ровно то, чего не сделал рукописный перечень 09.09.2026).
    Кэш перечня следует за каталогом: `forget()` здесь не зовётся."""
    # Схема записи каталога: единица и размер нарушения бывают только у
    # мягкой семьи (X03, X16), перечень шаблонов проверок непуст ⇔ вид
    # `check_template`, семья природы «история» обязана нести вид
    # `kotlin_logic_form` (X13) и назвать чтение истории (X19), а её
    # природу подтверждает свидетель `solver_tables` (X17). Пробная
    # запись пишется по этим правилам, иначе копия красна не по
    # проверяемому делу.
    общее = {"kind": "HARD", "zone": "ROSTERING", "violation_unit": None,
             "violation_size": None, "check_templates": []}
    folder = cc.copy_catalog(tmp_path, families={
        PROBE_HISTORY: dict(общее, nature=ha.HISTORY_NATURE,
                            nature_confirmed_by="solver_tables",
                            reads_history={"kind": "series",
                                           "need": ПРОБНАЯ_ПОТРЕБНОСТЬ},
                            executor_kinds=["kotlin_logic_form"]),
        PROBE_SCHEDULE: dict(общее, nature="schedule",
                             nature_confirmed_by="none",
                             reads_history=None,
                             executor_kinds=["check_code"])})
    cc.use_catalog(monkeypatch, folder)
    got = ha.history_families()
    assert PROBE_HISTORY in got and PROBE_SCHEDULE not in got
    assert set(got) == _по_полю()
    assert ПРОБНАЯ_ПОТРЕБНОСТЬ in got[PROBE_HISTORY], got[PROBE_HISTORY]
    st = ha.statement({"rules": [
        {"id": "R-9", "title": "проба", "zone": "ROSTERING", "kind": "HARD",
         "export_solver": {"family": PROBE_HISTORY}},
        {"id": "R-8", "title": "проба вне истории", "zone": "ROSTERING",
         "kind": "HARD", "export_solver": {"family": PROBE_SCHEDULE}}]},
        None, "rostering")["history"]
    assert st["families"] == [PROBE_HISTORY]
    assert [r["rule_id"] for r in st["rules"]] == ["R-9"]
    assert st["assumption"] == "остаток на начало периода = 0"
    assert PROBE_HISTORY in st["note"] and ПРОБНАЯ_ПОТРЕБНОСТЬ in st["note"]
    assert [b["kind"] for b in st["reads"]] == ["series"]
    assert st["reads"][0]["families"] == [PROBE_HISTORY]
    assert st["reads"][0]["needs"] == [ПРОБНАЯ_ПОТРЕБНОСТЬ]


def test_заявление_разложено_по_видам_чтения(дверь_каталога):
    """Допущение — ПО ВИДУ чтения истории: блок на вид, семьи вида
    перечислены, потребности — словами записи (та же раскладка, что у
    анкеты CDPM: один блок вопросов на вид, `Q-3b-история-<kind>`).
    Восемь семей — не восемь допущений."""
    reads = ha.history_reads()
    семьи = sorted(reads)
    виды = sorted({reads[f]["kind"] for f in семьи})
    assert len(виды) < len(семьи), "видов столько же, сколько семей"
    блоки = ha.read_blocks(семьи, passed=False)
    assert [b["kind"] for b in блоки] == виды
    assert sorted(f for b in блоки for f in b["families"]) == семьи
    for b in блоки:
        assert b["assumption"] and b["kind"] in b["assumption"]
        assert b["needs"], b["kind"]
    # история передана — допущения нет ни у одного вида
    assert all(b["assumption"] is None
               for b in ha.read_blocks(семьи, passed=True))


def test_граница_горизонта_названа_полем_а_не_рукой(дверь_каталога):
    """Долг 10.09.2026 закрыт: семьи границы горизонта (историю у них
    читает не форма правила, а сама мера) названы полем записи, а не
    перечнем в коде. Их природа осталась «расписание» — и это верно:
    правило судит расписание, а поле говорит о чтении истории прямо."""
    meta = ca.current().families_meta()
    граница = sorted(_по_полю() - set(lgc.FAMILY_FORMS))
    assert граница, "семей границы горизонта у поля нет — сторож слеп"
    for fid in граница:
        assert meta[fid].get("nature") != ha.HISTORY_NATURE, fid
        assert fid not in set(ktab.history()["families"]), fid
        assert fid in ha.history_families(), fid
    src = Path(ha.__file__).read_text(encoding="utf-8")
    assert "reads_history" in src and "0.5.0" in src
    assert not [fid for fid in граница if fid in src], (
        "имя семьи границы горизонта осталось литералом в коде: перечень "
        "рукой вернулся")
    # Обе стороны читают ОДИН носитель — поле записи: перечень CDPM
    # (HISTORY_FAMILIES) снят в 0.5.0, history_families() обеих сторон
    # обязаны совпасть с полем и друг с другом.
    from cdpm import history_section as hs
    assert set(hs.history_families()) == _по_полю(), (
        "CDPM называет семьи истории не по полю записи: %s"
        % sorted(set(hs.history_families()) ^ _по_полю()))
    assert set(hs.history_families()) == set(ha.history_families())


def test_молчание_каталога_не_теряет_семьи_форм(monkeypatch, дверь_каталога):
    """Дверь каталога молчит — заявление не отказывает и не теряет семьи,
    чью форму судит сертификат: остаётся сторона кода, и о том, что вид
    чтения и потребность не названы, строка говорит словами. Молчание не
    запоминается кэшем: живой каталог сразу возвращает свои записи."""
    def _нет_каталога():
        raise ca.CatalogUnavailable("проба: каталога нет")
    живая_дверь = ca.current
    monkeypatch.setattr(ca, "current", _нет_каталога)
    ha.forget()
    got = ha.history_families()
    assert set(got) == set(lgc.FAMILY_FORMS)
    assert ha.history_reads() == {}
    assert all("каталог молчит" in got[f] for f in got)
    monkeypatch.setattr(ca, "current", живая_дверь)
    ca.forget()
    assert set(ha.history_families()) == _по_полю()
