# -*- coding: utf-8 -*-
"""Р2: файл-кандидат записи каталога (шаг 6, §3.5 и §5.4).

Кандидат — предложение конвейера самоулучшения по принятой заявке.
Судей у него два и ровно два: ФОРМУ файла судит схема
`template_catalog/schemas/candidate.schema.json`, СМЫСЛ записи — обычный
`validate()` читателя на каталоге в памяти (живой ⊕ кандидат). Третьего
судьи (своей копии правил каталога) здесь нет намеренно: он разошёлся бы
с читателем на первом же новом правиле.

Временный каталог (`materialize`) нужен потому, что стенд, генератор
привязок и гейт привязок ищут запись в КАТАЛОГЕ: без неё они красны по
построению, а красный гейт, неизбежный по построению, ничего не судит.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402
import template_catalog as tc                                     # noqa: E402
from template_catalog import candidates as cand                   # noqa: E402
from template_catalog import requests as rq                       # noqa: E402

ОБРАЗЕЦ = "layover_penalty"
НОВАЯ = "sit_overage_steps"
НОМЕР = "req-04763385720803207704"
КЛЮЧ_CR = "crd89c9550"


@pytest.fixture()
def копия(tmp_path) -> Path:
    return cc.copy_catalog(tmp_path)


def _запись(cat, rid: str = НОВАЯ, *, задачи=None) -> dict:
    """Запись кандидата по образцу живой: без параметров (X02 не о том),
    со своими стендовыми задачами и статусом «есть»."""
    rec = copy.deepcopy(cat.family(ОБРАЗЕЦ))
    rec.pop("history", None)
    rec.update({"id": rid, "params": [], "param_sets": [], "conditions": [],
                "complements": [], "subject_words": None, "rule_form": None,
                "status": "есть",
                "stand_tasks": (задачи if задачи is not None
                                else _задачи(cat))})
    return rec


def _задачи(cat) -> list:
    задача = copy.deepcopy(cat.family(ОБРАЗЕЦ)["stand_tasks"][0])
    задача["id"] = "st-sit-overage-1"
    return [задача]


def _кандидат(cat, **правки) -> dict:
    doc = {"form_version": 1, "section": "families", "id": НОВАЯ,
           "record": _запись(cat), "note": "исполнители по заявке владельца",
           "by": "контур самоулучшения",
           "saved_at": "2026-09-12T11:00:00+03:00",
           "request_id": НОМЕР, "cr_key": КЛЮЧ_CR}
    doc.update(правки)
    return doc


def _положить(tmp_path: Path, doc: dict, имя="candidate.json") -> Path:
    p = Path(tmp_path) / имя
    p.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                              indent=1) + "\n").encode("utf-8"))
    return p


# ═══════════════════════════════════ форма файла

def test_1_годный_кандидат_читается(копия, tmp_path, capsys):
    cat = tc.load(копия)
    p = _положить(tmp_path, _кандидат(cat))
    doc = cand.load_candidate(p)
    assert doc["id"] == НОВАЯ and doc["cr_key"] == КЛЮЧ_CR
    rec = cand.record_of(doc)
    assert rec["status"] == "есть" and "history" not in rec
    assert len(rec["stand_tasks"]) == 1
    print(f"\nР2: кандидат {doc['section']}/{doc['id']} по заявке "
          f"{doc['request_id']} прочитан; задач стенда "
          f"{len(rec['stand_tasks'])}")


@pytest.mark.parametrize("правка,слова", [
    ({"лишнее": 1}, "лишние поля"),
    ({"form_version": 2}, "форма"),
    ({"section": "dictionaries"}, "раздел"),
    ({"request_id": "req-0001"}, "req-<20 цифр>"),
    ({"cr_key": "CR-1"}, "cr<8 hex>"),
    ({"saved_at": "2026-09-12"}, "ISO с зоной"),
    ({"note": ""}, "непустая строка"),
])
def test_2_форма_файла_закрыта(копия, tmp_path, правка, слова):
    cat = tc.load(копия)
    p = _положить(tmp_path, _кандидат(cat, **правка))
    with pytest.raises(cand.CandidateError) as e:
        cand.load_candidate(p)
    assert слова in str(e.value), str(e.value)


def test_3_история_у_кандидата_запрещена(копия, tmp_path):
    """Историю пишет сборка службой: три строки §2.5 — след решения
    владельца, а не работы агента."""
    cat = tc.load(копия)
    rec = _запись(cat)
    rec["history"] = [{"date": "2026-09-12", "kind": "создание",
                       "by": "контур самоулучшения", "note": "я сам",
                       "ref": None}]
    p = _положить(tmp_path, _кандидат(cat, record=rec))
    with pytest.raises(cand.CandidateError) as e:
        cand.load_candidate(p)
    assert "историю пишет сборка" in str(e.value)


def test_4_id_записи_и_кандидата_обязаны_совпасть(копия, tmp_path):
    cat = tc.load(копия)
    rec = _запись(cat, rid="другое_имя")
    p = _положить(tmp_path, _кандидат(cat, record=rec))
    with pytest.raises(cand.CandidateError) as e:
        cand.load_candidate(p)
    assert "≠ id кандидата" in str(e.value)


def test_5_свидетельство_судится_схемой(копия, tmp_path):
    cat = tc.load(копия)
    свид = {"cr_key": КЛЮЧ_CR, "branch": "cr-" + КЛЮЧ_CR,
            "commit": "abc1234", "evidence_commit": None,
            "at": "2026-09-12T12:00:00+03:00",
            "gates": {"pytest": "green"},
            "executors": [{"kind": "builder_method", "state": "есть",
                           "file": "vmp/chain_price.py", "symbol": "measure"}],
            "isolation": "broker"}
    p = _положить(tmp_path, _кандидат(cat, evidence=свид))
    assert cand.load_candidate(p)["evidence"]["branch"] == "cr-" + КЛЮЧ_CR
    # Форму свидетельства судит только схема (структурный минимум кодом
    # её не дублирует), поэтому без jsonschema проверять нечего.
    pytest.importorskip("jsonschema")
    свид_плохой = dict(свид, executors=[{"kind": "builder_method"}])
    p2 = _положить(tmp_path, _кандидат(cat, evidence=свид_плохой),
                   имя="c2.json")
    with pytest.raises(cand.CandidateError) as e:
        cand.load_candidate(p2)
    assert "executors" in str(e.value)


# ═══════════════════════════════════ смысл записи

def test_6_проза_кандидата_судится_по_адресу(копия, tmp_path):
    cat = tc.load(копия)
    rec = _запись(cat)
    rec["semantics"]["text"] = "штраф считает Builder.chain_cost(x) по цепочке"
    doc = _кандидат(cat, record=rec)
    probs = cand.validate_with_candidate(cat, doc)
    беда = [p for p in probs if p.where.startswith(f"families/{НОВАЯ}")]
    assert беда and "A05" in {p.code for p in беда}
    assert беда[0].where == f"families/{НОВАЯ}.semantics.text"


def test_7_код_станции_в_мире_ловит_a03(копия, tmp_path):
    cat = tc.load(копия)
    задачи = _задачи(cat)
    задачи[0]["world"]["flights"][0]["from"] = "SVX"
    doc = _кандидат(cat, record=_запись(cat, задачи=задачи))
    probs = cand.validate_with_candidate(cat, doc)
    a03 = [p for p in probs if p.code == "A03"]
    assert a03, [str(p) for p in probs]
    assert "SVX" in a03[0].message and "синтетическ" in a03[0].message


def test_8_маркер_заказчика_ловится_перечнем(копия):
    cat = tc.load(копия)
    rec = _запись(cat)
    rec["note"] = "по просьбе Полярис"
    doc = _кандидат(cat, record=rec)
    чисто = cand.validate_with_candidate(cat, doc)
    assert not [p for p in чисто if p.code == "A02"]
    с_маркерами = cand.validate_with_candidate(cat, doc,
                                               forbidden_terms=("Полярис",))
    assert [p for p in с_маркерами if p.code == "A02"]


def test_9_годный_кандидат_даёт_ноль_ошибок(копия, capsys):
    cat = tc.load(копия)
    probs = cand.validate_with_candidate(cat, _кандидат(cat))
    errors = [p for p in probs if p.severity == "error"]
    assert not errors, [str(p) for p in errors]
    print(f"\nР2: рассмотрено проблем {len(probs)}, ошибок {len(errors)}")


# ═══════════════════════════════════ временный каталог

def test_10_materialize_даёт_годную_папку(копия, tmp_path, capsys):
    cat = tc.load(копия)
    doc = _кандидат(cat)
    out = cand.materialize(копия, doc, tmp_path / "gate" / "catalog")
    новый = tc.load(out)
    problems = новый.validate()
    errors = [p for p in problems if p.severity == "error"]
    assert not errors, [str(p) for p in errors]
    assert новый.fingerprint() != cat.fingerprint(), "отпечаток не пересчитан"
    assert новый.fingerprint() == новый.declared_fingerprint()
    rec = новый.family(НОВАЯ)
    assert rec["status"] == "есть" and len(rec["stand_tasks"]) == 1
    assert rec["history"][-1]["by"] == "контур самоулучшения"
    assert rec["history"][-1]["ref"] == НОМЕР
    assert НОВАЯ in новый.families()
    # Порядок семей пополнен — иначе дверь COSMOS откажет словами.
    order = json.loads((out / "transition" / "families_order.json")
                       .read_text(encoding="utf-8"))
    assert НОВАЯ in order and len(set(order)) == len(order)
    assert set(order) == set(новый.families())
    print(f"\nР2: временный каталог {новый.version()} | отпечаток "
          f"{новый.fingerprint()[:12]}… | семей {len(новый.families())} | "
          f"порядок из {len(order)}")


def test_11_версия_временного_каталога_законна(копия, tmp_path):
    """Отступление от буквы проекта («<v>-draft.gate»): такой суффикс не
    semver — H01 отказал бы, и гейт судил бы собственный ярлык."""
    assert cand.gate_version("0.2.0-draft.1") == "0.2.0-draft.1"
    assert cand.gate_version("0.2.0") == "0.2.1-draft.1"
    with pytest.raises(cand.CandidateError):
        cand.gate_version("0.2.0-draft.gate")
    cat = tc.load(копия)
    out = cand.materialize(копия, _кандидат(cat), tmp_path / "g" / "catalog")
    h = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
    # Номер временного каталога — по той же формуле gate_version: у
    # черновика он остаётся прежним, у ВЫПУСКА (с 08.09 копия несёт
    # номер выпуска 0.2.0) поднимается на патч-черновик.
    assert h["version"] == cand.gate_version(cat.version())
    assert КЛЮЧ_CR in h["note"] and НОМЕР in h["note"]
    assert "выпуску не подлежит" in h["note"]


def test_12_живой_каталог_не_тронут(копия, tmp_path):
    """`materialize` пишет ТОЛЬКО в свою папку."""
    было = {str(p.relative_to(копия)): p.read_bytes()
            for p in sorted(копия.rglob("*")) if p.is_file()}
    cand.materialize(копия, _кандидат(tc.load(копия)),
                     tmp_path / "g2" / "catalog")
    стало = {str(p.relative_to(копия)): p.read_bytes()
             for p in sorted(копия.rglob("*")) if p.is_file()}
    assert стало == было


def test_13_кандидат_существующей_записи_меняет_её(копия, tmp_path):
    """Заявка на ВИД исполнителя: запись уже есть, кандидат приносит ей
    новые стендовые задачи, а история записи не теряется."""
    cat = tc.load(копия)
    было = cat.family(ОБРАЗЕЦ)
    задачи = copy.deepcopy(было["stand_tasks"])
    задачи.append(dict(задачи[0], id="st-layover-2"))
    rec = copy.deepcopy(было)
    rec.pop("history")
    rec["stand_tasks"] = задачи
    doc = _кандидат(cat, id=ОБРАЗЕЦ, record=rec)
    out = cand.materialize(копия, doc, tmp_path / "g3" / "catalog")
    новый = tc.load(out)
    assert not [p for p in новый.validate() if p.severity == "error"]
    стало = новый.family(ОБРАЗЕЦ)
    assert len(стало["stand_tasks"]) == len(было["stand_tasks"]) + 1
    assert стало["history"][:len(было["history"])] == было["history"]
    assert стало["history"][-1]["note"] == "временная запись гейта CR"
    assert len(новый.families()) == len(cat.families())


# ══════════════════ производные инварианты сборки (решение 0.8)

def _связывающий(cat, tmp_path) -> tuple[dict, str, str]:
    """Кандидат, который СВЯЗЫВАЕТ имя из `unbound_params` записью и
    объявляет дополнение существующей семье: на нём видны оба
    производных инварианта сборки. Возврат — (кандидат, имя, сосед)."""
    имя = sorted(cat.dictionaries()["unbound_params"])[0]
    свободное = cat.dictionaries()["unbound_params"][имя]
    сосед = next(r["id"] for r in cat.families_full()
                 if r.get("complements") and r["id"] != НОВАЯ)
    rec = _запись(cat)
    rec["complements"] = [сосед]
    rec["params"] = [{"name": имя, "unit": свободное["unit"],
                      "relation": свободное.get("relation") or "max",
                      "qualifier": None,
                      "note": "имя связано записью кандидата"}]
    return _кандидат(cat, record=rec), имя, сосед


def test_20_materialize_применяет_инварианты_сборки(копия, tmp_path, capsys):
    """Решение 0.8: временный каталог кандидата обязан быть равен тому,
    что позже соберёт из этого кандидата служба.

    Кандидат связывает имя из `unbound_params` и объявляет дополнение:
    без инвариантов писателя временный каталог был бы красен D03 и X01
    там, где сборка службы зелена, — два закона на один переход."""
    from catalog_service import store as srv

    cat = tc.load(копия)
    doc, имя, сосед = _связывающий(cat, tmp_path)
    out = cand.materialize(копия, doc, tmp_path / "gate" / "catalog")
    новый = tc.load(out)
    assert not [p for p in новый.validate() if p.severity == "error"]
    # D03: имя ушло из словаря — оно обрело семью
    assert имя in cat.dictionaries()["unbound_params"]
    assert имя not in новый.dictionaries()["unbound_params"]
    assert новый.params()[имя]["family"] == НОВАЯ
    # X01: дополнение симметрично
    assert НОВАЯ in новый.family(сосед)["complements"]
    # L10: старая ревизия шапки — по формуле над семьями ЭТОГО каталога
    from template_catalog import writer
    assert новый.header["legacy_registry_rev"] == \
        writer.legacy_registry_rev(новый.families())
    assert новый.header["legacy_registry_rev"] != \
        cat.header["legacy_registry_rev"]
    # и главное: тело равно телу сборки службы по тому же кандидату
    draft = srv.draft_doc("families", НОВАЯ, record=cand.record_of(doc),
                          note="сборка по кандидату", by="владелец")
    собранный = srv.assemble_in_memory(cat, [draft], actor="владелец")

    def тело(fams, crits, d):
        чисто = lambda r: {k: v for k, v in r.items() if k != "history"}
        return {"families": sorted(map(чисто, fams),
                                   key=lambda r: str(r["id"])),
                "criteria": sorted(map(чисто, crits),
                                   key=lambda r: str(r["id"])),
                "dictionaries": d}

    было = новый.body()
    assert тело(было["families"], было["criteria"], было["dictionaries"]) == \
        тело(собранный.families, собранный.criteria, собранный.dictionaries)
    print(f"\nР2 0.8: имя {имя} связано записью, дополнение {сосед} "
          f"симметрично, ревизия {новый.header['legacy_registry_rev']}; "
          f"тело временного каталога равно телу сборки службы")


def test_21_materialize_порядок_семей_формулой(копия, tmp_path, capsys):
    """Порядок семей — формула писателя (прежний ∩ «есть» ∪ новые в
    конец), а не «дописать id в хвост»: дверь COSMOS требует равенства
    множеств, и лишнее имя прежнего порядка её остановило бы."""
    порядок = копия / "transition" / "families_order.json"
    было = json.loads(порядок.read_text(encoding="utf-8"))
    порядок.write_bytes((json.dumps(["нет_такой_семьи"] + было[1:] + [было[0]],
                                    ensure_ascii=False) + "\n")
                        .encode("utf-8"))
    cat = tc.load(копия)
    out = cand.materialize(копия, _кандидат(cat), tmp_path / "g" / "catalog")
    новый = tc.load(out)
    стало = json.loads((out / "transition" / "families_order.json")
                       .read_text(encoding="utf-8"))
    from template_catalog import writer
    есть = [r["id"] for r in новый.families_full() if r["status"] == "есть"]
    assert стало == writer.refresh_families_order(
        ["нет_такой_семьи"] + было[1:] + [было[0]], есть)
    assert set(стало) == set(есть), "равенство множеств — закон двери"
    assert "нет_такой_семьи" not in стало
    assert стало[-1] == НОВАЯ, "новая семья дописана в конец"
    assert not [p for p in новый.validate() if p.severity == "error"]
    print(f"\nР2: порядок семей из {len(стало)} имён по формуле писателя")


def test_22_временный_каталог_отличается_от_живого_ровно_кандидатом(
        копия, tmp_path, capsys):
    """Стенд `materialize`, а не правило закона: разница тел живого и
    временного каталогов — это запись кандидата и ровно те словари и
    записи, которых коснулись производные инварианты. Ничего сверх."""
    from template_catalog import diff as tdiff

    cat = tc.load(копия)
    doc, имя, сосед = _связывающий(cat, tmp_path)
    out = cand.materialize(копия, doc, tmp_path / "g2" / "catalog")
    новый = tc.load(out)
    changes = tdiff.diff_bodies(cat.body(), новый.body())
    адреса = sorted((c.section, c.id) for c in changes)
    assert адреса == sorted([("families", НОВАЯ), ("families", сосед),
                             ("dictionaries", "unbound_params")]), адреса
    kinds = {(c.section, c.id): c.kind for c in changes}
    assert kinds[("families", НОВАЯ)] == "добавлена"
    assert kinds[("families", сосед)] == "изменена"
    print(f"\nР2: разница живого и временного — {len(changes)} строк: "
          + ", ".join(f"{s}/{i}" for s, i in адреса))


# ═══════════════════════════════════ команда

def test_14_команда_код_0_и_число_проблем(копия, tmp_path, capsys):
    cat = tc.load(копия)
    p = _положить(tmp_path, _кандидат(cat))
    rc = cand.main([str(p), "--catalog", str(копия)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ошибок 0" in out and str(копия) in out


def test_15_команда_код_1_на_негодном_кандидате(копия, tmp_path, capsys):
    cat = tc.load(копия)
    rec = _запись(cat)
    rec["semantics"]["text"] = "штраф считает Builder.chain_cost(x)"
    p = _положить(tmp_path, _кандидат(cat, record=rec))
    rc = cand.main([str(p), "--catalog", str(копия)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "ошибок 1" in out and "A05" in out


def test_16_команда_предупреждает_о_неизвестной_заявке(копия, tmp_path,
                                                       capsys):
    """Кандидат ссылается на заявку, которой в каталоге нет: это не
    отказ (заявка живёт на рабочем месте владельца), но молчать нельзя."""
    cat = tc.load(копия)
    assert cat.request(НОМЕР) is None
    p = _положить(tmp_path, _кандидат(cat))
    rc = cand.main([str(p), "--catalog", str(копия)])
    out = capsys.readouterr().out
    assert rc == 0 and "предупреждение" in out and НОМЕР in out


def test_17_команда_на_кривом_файле_отказывает_словами(tmp_path, capsys):
    p = tmp_path / "битый.json"
    p.write_text("{ это не JSON", encoding="utf-8")
    rc = cand.main([str(p)])
    out = capsys.readouterr().out
    assert rc == 1 and "кандидат не по форме" in out


def test_18_маркеры_перечнем_из_файла(копия, tmp_path, capsys):
    cat = tc.load(копия)
    rec = _запись(cat)
    rec["note"] = "по просьбе Полярис"
    p = _положить(tmp_path, _кандидат(cat, record=rec))
    markers = tmp_path / "markers.txt"
    markers.write_text("Полярис\n", encoding="utf-8")
    rc = cand.main([str(p), "--catalog", str(копия),
                    "--markers", str(markers)])
    out = capsys.readouterr().out
    assert rc == 1 and "A02" in out


def test_19_кандидат_id_не_по_образцу_отказ(копия, tmp_path):
    cat = tc.load(копия)
    p = _положить(tmp_path, _кандидат(cat, id="Плохое-Имя",
                                      record=_запись(cat, rid="Плохое-Имя")))
    with pytest.raises(cand.CandidateError) as e:
        cand.load_candidate(p)
    assert "идентификатора записи" in str(e.value)


def _копия_выпуском(tmp_path, version: str = "0.9.0") -> Path:
    """Копия каталога, ОБЪЯВЛЕННАЯ выпуском: номер без суффикса черновика
    и запись журнала о ней же (её отпечаток).

    Живой каталог бывает черновиком — так выглядит волна выпуска, — а
    ветвь «кандидат на ВЫПУЩЕННОМ каталоге» обязана оставаться под
    гейтом в любой день, а не только между выпусками."""
    # candidates=False: копия обязана РАВНЯТЬСЯ выпуску. На ветке CR
    # помощник по умолчанию вливает кандидатов дерева и пересчитывает
    # отпечаток при журнале выпусков — копия красна H03 по построению
    # (сквозная проверка E2E-6 09.09.2026 упала именно здесь).
    folder = cc.copy_catalog(tmp_path, releases=True, candidates=False,
                             version=version)
    cat = tc.load(folder)
    doc = json.loads((folder / "versions.json").read_text(encoding="utf-8"))
    doc["versions"] = [v for v in doc["versions"]
                       if v["version"] != version]
    doc["versions"].append({"version": version,
                            "fingerprint": cat.fingerprint(),
                            "released_at": cat.header["released_at"],
                            "released_by": "владелец", "code": "0" * 40,
                            "changes": []})
    (folder / "versions.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder


def test_20_кандидат_на_выпущенном_каталоге_без_H03(tmp_path):
    """Каталог-ВЫПУСК: любая запись меняет тело и отпечаток шапки, и с
    номером выпуска в шапке читатель отвечал бы H03 «отпечаток записи
    журнала не равен шапке» на любом кандидате — по построению, а не по
    делу (сквозная проверка 09.09.2026). Каталог в памяти «живой ⊕
    кандидат» несёт версию следующего черновика, как временный каталог
    гейтов и как сборка службы."""
    копия = _копия_выпуском(tmp_path)
    cat = tc.load(копия)
    assert not cat.is_draft(), "предпосылка теста — каталог-выпуск"
    assert cat.versions(), "копия с журналом выпусков — предпосылка теста"
    assert not [p for p in cat.validate() if p.code == "H03"], \
        "сама копия выпуска обязана быть чиста по H03"
    doc = _кандидат(cat)
    mem = cand.catalog_with_candidate(cat, doc)
    assert mem.version() == cand.gate_version(cat.version())
    assert mem.version() != cat.version() and "-draft." in mem.version()
    h03 = [p for p in cand.validate_with_candidate(cat, doc)
           if p.code == "H03"]
    assert h03 == [], [p.message for p in h03]
