# -*- coding: utf-8 -*-
"""Помощник копий каталога (tests/catalog_copies): заявки-свидетели
кандидатов дерева и слияние очереди заявок копии.

Единицы имён записи КАНДИДАТА конвейера (`catalog/candidates/**`),
которых не знают ни словарь CDPM, ни таблицы решателя, подтверждает
черновик её принятой заявки (решение 0.6 сборки; правила L05/L05b;
`unit_of` инструмента). Стенд, писавший очередь копии целиком
(`{"requests": [своя заявка]}`), на ветке CR duty_series_limit
(09.09.2026, попытка #3) стирал этого свидетеля, и копия краснела
«единицу не подтверждает ни словарь CDPM, ни таблицы решателя, ни
заявка» — по построению стенда, а не по делу. Помощник `write_requests`
кладёт заявки-свидетели впереди строк стенда; `witness_requests` их
называет; `witness_ids` решает, КОМУ свидетель нужен: кандидату, чьи
имена решатель уже знает (попытка #4: ручки и единицы истории объявлены
в Kotlin), свидетель не нужен — и очередь стендов службы остаётся
пустой, как они ждут. На чистом дереве все три — пустые множества."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402

PHANTOM = "phantom_family_of_copies_test"
#: параметр, которого не знают ни словарь CDPM, ни таблицы решателя
UNKNOWN_PARAM = {"name": "phantom_knob_of_copies_test", "unit": "count",
                 "relation": "max", "qualifier": None, "note": None}


def _cand(rid: str, params: list) -> dict:
    return {"id": rid, "params": copy.deepcopy(params)}


def _row(rid: str, record_id: str, state: str = "исполнитель заявлен") -> dict:
    return {"request_id": rid, "state": state, "section": "families",
            "record_id": record_id,
            "submitted_at": "2026-09-09T10:00:00+03:00",
            "submitted_by": "COSMOS",
            "source": {"kind": "пробел при нажатии",
                       "document_class": "документ свода правил авиакомпании",
                       "run_class": "прогон COSMOS", "ref": None},
            "draft": {"params": [dict(UNKNOWN_PARAM)]},
            "executor_kinds_requested": None,
            "decision": {"by": "владелец", "at": "2026-09-09T10:00:00+03:00",
                         "words": "принята"},
            "cr_key": "cr00000000" if state == "исполнитель заявлен" else None}


def _queue(folder: Path) -> list:
    return json.loads((folder / "requests.json").read_text(encoding="utf-8"))["requests"]


def test_на_дереве_без_кандидатов_свидетелей_нет_и_очередь_равна_строкам(
        tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "tree_candidates", lambda: ({}, {}))
    folder = cc.copy_catalog(tmp_path, candidates=False)
    assert cc.witness_ids() == set()
    assert cc.witness_requests(folder) == []
    own = _row("req-own", "duty_limits", state="подана")
    written = cc.write_requests(folder, [own])
    assert written == [own]
    assert _queue(folder) == [own]


def test_свидетель_нужен_только_имени_без_словаря_и_решателя(monkeypatch):
    """Кандидат с именем, которого не знают ни словарь CDPM, ни таблицы
    решателя, — свидетель нужен; кандидат, чьи имена решатель знает
    (ручка либо единица истории из Kotlin), — не нужен; кандидат без
    параметров — не нужен."""
    from vmp import kotlin_tables as ktab
    t = ktab.tables()
    known_name = sorted(t["knobs"])[0]
    known = _cand("known_family_of_copies_test",
                  [{"name": known_name, "unit": t["knobs"][known_name]["unit"]}])
    unknown = _cand(PHANTOM, [UNKNOWN_PARAM])
    empty = _cand("empty_family_of_copies_test", [])
    assert cc.witness_ids({known["id"]: known, unknown["id"]: unknown,
                           empty["id"]: empty}, {}) == {PHANTOM}
    # свидетели не прочитаны — заявки оставляются (копия не вправе
    # оказаться «не собранной» из-за молчания помощника)
    monkeypatch.setattr(cc, "_known_param_names", lambda: None)
    assert cc.witness_ids({known["id"]: known}, {}) == {known["id"]}


def test_заявка_свидетель_кандидата_переживает_запись_очереди(
        tmp_path, monkeypatch):
    """Строки стенда ⊕ свидетель впереди; свидетель — только заявка на
    запись кандидата дерева, чужие заявки очереди свидетелями не
    считаются."""
    folder = cc.copy_catalog(tmp_path, candidates=False)
    witness = _row("req-witness", PHANTOM)
    stranger = _row("req-stranger", "duty_limits")
    (folder / "requests.json").write_text(json.dumps(
        {"form_version": 1, "requests": [stranger, witness]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    monkeypatch.setattr(cc, "tree_candidates",
                        lambda: ({PHANTOM: _cand(PHANTOM, [UNKNOWN_PARAM])}, {}))
    assert cc.witness_requests(folder) == [witness]
    own = _row("req-own", "duty_limits", state="подана")
    written = cc.write_requests(folder, [own])
    assert written == [witness, own]
    assert _queue(folder) == [witness, own]
    # своя строка с номером свидетеля — не дубль: побеждает строка стенда
    mine = copy.deepcopy(witness)
    mine["state"] = "подана"
    written = cc.write_requests(folder, [mine])
    assert written == [mine]
    # keep_witnesses=False — очередь ровно из строк стенда (стенды негодной
    # формы очереди)
    (folder / "requests.json").write_text(json.dumps(
        {"form_version": 1, "requests": [witness]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    assert cc.write_requests(folder, [own], keep_witnesses=False) == [own]
    assert _queue(folder) == [own]


def test_свидетели_считаются_по_кандидатам_обоих_разделов(tmp_path, monkeypatch):
    folder = cc.copy_catalog(tmp_path, candidates=False)
    fam = _row("req-f", PHANTOM)
    crit = dict(_row("req-c", "phantom_criterion"), section="criteria")
    (folder / "requests.json").write_text(json.dumps(
        {"form_version": 1, "requests": [fam, crit]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    monkeypatch.setattr(cc, "tree_candidates",
                        lambda: ({PHANTOM: _cand(PHANTOM, [UNKNOWN_PARAM])},
                                 {"phantom_criterion": _cand("phantom_criterion",
                                                             [UNKNOWN_PARAM])}))
    assert [r["request_id"] for r in cc.witness_requests(folder)] == ["req-f", "req-c"]
    # без файла очереди свидетелей нет — и это не ошибка
    (folder / "requests.json").unlink()
    assert cc.witness_requests(folder) == []


def test_пустая_очередь_копии_держит_только_нужных_свидетелей(tmp_path):
    """requests=False: очередь копии — ровно заявки живой очереди на
    кандидатов дерева, которым свидетель нужен (`witness_ids`); на чистом
    дереве и у кандидата с известными решателю именами — пустая, как её
    ждут стенды службы."""
    folder = cc.copy_catalog(tmp_path, requests=False)
    live = cc._read(cc.CATALOG / "requests.json").get("requests") or []
    ids = cc.witness_ids()
    assert [r["request_id"] for r in _queue(folder)] == \
        [r["request_id"] for r in live if str(r.get("record_id")) in ids]
