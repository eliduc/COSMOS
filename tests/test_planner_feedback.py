"""Контур «Отклик планировщика»: журнал переделок с причиной,
детерминированная кластеризация и черновики CR. Хранилище перенаправлено в
tmp-файл фикстурой store."""
import json

import pytest


@pytest.fixture()
def store(tmp_path):
    from webapp import planner_feedback
    planner_feedback.OVERRIDE_PATH = tmp_path / "override_log.json"
    return planner_feedback


def _rec(store, inst="i1", pid="P1", frm="E1", to="E2",
         reason="missing_rule", note="почему-то так", by="a@x", slot=0):
    return store.record(inst, pid=pid, slot=slot, frm=frm, to=to,
                        reason_code=reason, reason_text=note, by=by)


# ------------------------------------------------------------------ запись

def test_record_shape_and_roundtrip(store):
    ep = _rec(store)
    assert ep["id"].startswith("ov") and ep["inst"] == "i1"
    assert ep["pid"] == "P1" and ep["slot"] == 0
    assert ep["from"] == "E1" and ep["to"] == "E2"
    assert ep["reason"] == "missing_rule" and ep["by"] == "a@x"
    assert ep["at"].endswith("+00:00") and len(ep["at"]) == 25
    assert store.episodes("i1") == [ep]
    assert store.episodes() == [ep]


def test_unknown_reason_degrades_to_other(store):
    # запись факта важнее строгости: неизвестный код не роняет журнал
    ep = _rec(store, reason="Правило!!!")
    assert ep["reason"] == "other"
    assert store.REASON_LABELS["other"] == "Иное"
    assert set(store.REASON_LABELS) == set(store.REASONS)


def test_note_clamped_and_empty_fields_are_none(store):
    ep = store.record("i1", pid="P1", slot=None, frm=None, to="E2",
                      reason_code="other", reason_text="д" * 500, by="")
    assert len(ep["note"]) == store.MAX_NOTE
    assert ep["from"] is None and ep["by"] is None and ep["slot"] is None


def test_cap_keeps_newest(store, monkeypatch):
    monkeypatch.setattr(store, "MAX_EPISODES", 5)
    for i in range(9):
        _rec(store, pid=f"P{i}")
    rows = store.episodes("i1")
    assert len(rows) == 5
    assert [r["pid"] for r in rows] == ["P4", "P5", "P6", "P7", "P8"]


def test_global_cap_bounds_the_whole_journal(store, monkeypatch):
    """Ревью волны 1: потолка на экземпляр мало — экземпляры заводятся
    свободно, и журнал рос бы линейно от их числа, а он целиком читается и
    целиком переписывается на КАЖДУЮ правку доски."""
    monkeypatch.setattr(store, "MAX_EPISODES", 100)     # не он здесь режет
    monkeypatch.setattr(store, "MAX_EPISODES_TOTAL", 10)
    for i in range(30):
        _rec(store, inst=f"i{i}", pid=f"P{i}")
    assert len(store.episodes()) == 10       # потолок держит весь журнал
    # и он держится дальше, сколько бы экземпляров ни завели
    for i in range(30, 60):
        _rec(store, inst=f"i{i}", pid=f"P{i}")
    assert len(store.episodes()) == 10


def test_global_cap_trims_the_fattest_instance(store, monkeypatch):
    """Режем у самого большого экземпляра, а не «самый старый по журналу»:
    иначе один активный экземпляр вытеснил бы историю всех остальных и
    адресные кластеры спокойных авиакомпаний перестали бы набираться."""
    monkeypatch.setattr(store, "MAX_EPISODES_TOTAL", 4)
    for i in range(5):
        _rec(store, inst="шумный", pid=f"N{i}")
    _rec(store, inst="тихий", pid="Q0")
    # у тихого экземпляра его единственный эпизод уцелел
    assert [r["pid"] for r in store.episodes("тихий")] == ["Q0"]
    # у шумного срезаны САМЫЕ СТАРЫЕ, порядок записи сохранён
    assert [r["pid"] for r in store.episodes("шумный")] == ["N2", "N3", "N4"]
    assert len(store.episodes()) == 4


def test_global_cap_is_deterministic(store, monkeypatch):
    """Тот же поток правок обязан давать тот же журнал: иначе обрезка
    порождала бы разные кластеры и разные черновики CR из одних данных."""
    monkeypatch.setattr(store, "MAX_EPISODES_TOTAL", 7)
    seq = [("a", 4), ("b", 3), ("c", 5), ("a", 2)]
    shots = []
    for _ in range(2):
        store.OVERRIDE_PATH.unlink(missing_ok=True)
        for inst, n in seq:
            for i in range(n):
                _rec(store, inst=inst, pid=f"{inst}{i}")
        shots.append([(e["inst"], e["pid"]) for e in store.episodes()])
    assert shots[0] == shots[1] and len(shots[0]) == 7


def test_write_cost_does_not_grow_with_history(store, monkeypatch):
    """Стоимость записи ограничена сверху потолком, а не всей историей:
    после насыщения журнала объём переписываемого файла перестаёт расти."""
    monkeypatch.setattr(store, "MAX_EPISODES_TOTAL", 20)
    for i in range(20):
        _rec(store, inst=f"i{i % 4}", pid=f"P{i}")
    saturated = store.OVERRIDE_PATH.stat().st_size
    for i in range(200):
        _rec(store, inst=f"i{i % 7}", pid=f"P{i}")
    assert len(store.episodes()) == 20
    # 200 дополнительных правок не добавили журналу ни строки сверх потолка
    assert store.OVERRIDE_PATH.stat().st_size <= saturated * 2


def test_instances_isolated(store):
    _rec(store, inst="i1")
    _rec(store, inst="i2")
    assert len(store.episodes("i1")) == 1
    assert len(store.episodes()) == 2


def test_corrupt_store_degrades_to_empty(store):
    store.OVERRIDE_PATH.write_text("{не json", encoding="utf-8")
    assert store.episodes() == []            # читаем защитно, а не 500
    assert store.clusters() == []
    _rec(store)                              # и остаёмся пригодны к записи
    assert len(store.episodes("i1")) == 1


def test_garbage_rows_ignored(store):
    store.OVERRIDE_PATH.write_text(
        json.dumps({"i1": ["строка", 7, {"reason": "other", "pid": "P1"}],
                    "i2": "не список"}), encoding="utf-8")
    rows = store.episodes()
    assert len(rows) == 1 and rows[0]["pid"] == "P1"


# ------------------------------------------------------------- кластеризация

def test_clusters_by_employee_and_pairing(store):
    for _ in range(3):
        _rec(store, pid="P1", frm="E1", to="E2", reason="missing_rule")
    cls = store.clusters(min_size=3)
    keys = {(c["key"]["facet"], c["key"]["value"]) for c in cls}
    assert ("pairing", "P1") in keys
    assert ("employee", "E1") in keys and ("employee", "E2") in keys
    for c in cls:
        assert c["size"] == 3 and c["reason"] == "missing_rule"
        assert len(c["sample"]) <= 3 and c["instances"] == ["i1"]


def test_different_reason_does_not_cluster(store):
    for _ in range(2):
        _rec(store, pid="P1", reason="missing_rule")
    for _ in range(2):
        _rec(store, pid="P1", reason="wrong_cost")
    assert store.clusters(min_size=3) == []
    assert store.clusters(min_size=2)          # разошлись по причинам
    sizes = {c["key"]["reason"] for c in store.clusters(min_size=2)}
    assert sizes == {"missing_rule", "wrong_cost"}


def test_min_size_filters(store):
    for _ in range(2):
        _rec(store, pid="P9", frm="E9", to=None)
    assert store.clusters(min_size=3) == []
    assert len(store.clusters(min_size=2)) == 2   # pairing + employee


def test_same_person_both_sides_counts_once(store):
    for _ in range(3):
        _rec(store, frm="E1", to="E1")
    emp = [c for c in store.clusters(min_size=3)
           if c["key"]["facet"] == "employee"]
    assert len(emp) == 1 and emp[0]["size"] == 3


def test_clusters_sorted_deterministically(store):
    for _ in range(4):
        _rec(store, pid="PB", frm="EB", to=None)
    for _ in range(3):
        _rec(store, pid="PA", frm="EA", to=None)
    cls = store.clusters(min_size=3)
    assert [c["size"] for c in cls] == sorted(
        [c["size"] for c in cls], reverse=True)
    # при равном размере порядок задан ключом, а не порядком записи
    same = [(c["key"]["reason"], c["key"]["facet"], c["key"]["value"])
            for c in cls if c["size"] == 4]
    assert same == sorted(same)
    assert store.clusters(min_size=3) == cls     # повтор даёт то же самое


def test_note_text_never_groups(store):
    # смысловой близости нет и быть не должно: разные пейринги/люди с
    # похожим комментарием остаются разными кластерами
    for i in range(4):
        _rec(store, pid=f"P{i}", frm=f"E{i}", to=None,
             note="нужен отдых подольше")
    assert store.clusters(min_size=2) == []


def test_addressed_clusters_never_span_instances(store):
    """Ревью волны 1 (HIGH): табельные (EMP001) и коды пейрингов (C_000001)
    в разных экземплярах совпадают сплошь и рядом, поэтому без экземпляра в
    ключе три несвязанные переделки трёх авиакомпаний склеивались в один
    «кластер» и рождали ложный CR."""
    for inst in ("i2", "i1", "i1"):
        _rec(store, inst=inst, pid="P1", frm="E1", to=None)
    addressed = [c for c in store.clusters(min_size=3)
                 if c["key"]["facet"] in ("employee", "pairing")]
    assert addressed == []                 # 2+1 больше НЕ склеиваются в 3

    # каждый адресный кластер живёт ровно в одном экземпляре, и он назван
    for c in store.clusters(min_size=2):
        if c["key"]["facet"] == "systemic":
            continue
        assert c["instances"] == ["i1"]
        assert c["key"]["instance"] == "i1"


def test_same_ids_in_two_instances_stay_separate(store):
    """Один и тот же табельный в двух экземплярах — два разных человека."""
    for _ in range(2):
        _rec(store, inst="airlineA", pid="C_000001", frm="EMP001", to=None)
    for _ in range(2):
        _rec(store, inst="airlineB", pid="C_000001", frm="EMP001", to=None)
    assert not [c for c in store.clusters(min_size=3)
                if c["key"]["facet"] in ("employee", "pairing")]
    per_inst = {(c["key"]["facet"], c["key"]["instance"])
                for c in store.clusters(min_size=2)
                if c["key"]["facet"] != "systemic"}
    assert per_inst == {("employee", "airlineA"), ("pairing", "airlineA"),
                        ("employee", "airlineB"), ("pairing", "airlineB")}


def test_systemic_stays_cross_instance_and_says_so(store):
    """Системный срез оставлен межэкземплярным ОСОЗНАННО: он про «эту
    причину выбирают часто», а не про конкретного человека. Область обязана
    быть видна и в ключе, и в тексте CR."""
    for i in range(15):
        _rec(store, inst=f"inst{i % 3}", pid=f"P{i}", frm=f"E{i}", to=None,
             reason="missing_rule")
    sysc = [c for c in store.clusters(min_size=3)
            if c["key"]["facet"] == "systemic"]
    assert len(sysc) == 1 and sysc[0]["size"] == 15
    assert sysc[0]["key"]["instance"] == store.ANY_INSTANCE
    assert sysc[0]["instances"] == ["inst0", "inst1", "inst2"]
    summary = store.build_crs(min_size=5)[0]["summary"]
    assert "СЧЁТ ПОВЕРХ ВСЕХ ЭКЗЕМПЛЯРОВ" in summary


def test_addressed_cr_text_names_the_instance(store):
    for _ in range(6):
        _rec(store, inst="airlineA", pid="C_000001", frm="EMP001", to=None)
    texts = [cr["summary"] for cr in store.build_crs(min_size=5)]
    assert texts and all("в экземпляре airlineA" in s for s in texts)


# --------------------------------------------------------------- черновики CR

def test_build_crs_contract(store):
    for _ in range(6):
        _rec(store, pid="P1", frm="E1", to=None, reason="missing_rule")
    rows = store.build_crs(min_size=5)
    assert rows
    for cr in rows:
        assert cr["id"].startswith("cr") and len(cr["id"]) == 10
        assert cr["agent"] == "override" and cr["status"] == "draft"
        assert len(cr["title"]) <= 90 and len(cr["summary"]) <= 600
        assert cr["priority"] == 6 and cr["effort"] == 5
        assert cr["source"] is None and cr["country"] is None
        assert cr["created_at"] == cr["updated_at"]
        assert "6 раз одна и та же переделка" in cr["title"]
        assert "правило не учтено" in cr["title"]


def test_summary_states_three_explanations_and_caveat(store):
    for _ in range(5):
        _rec(store, pid="P1", frm="E1", to=None, reason="wrong_cost")
    s = store.build_crs(min_size=5)[0]["summary"]
    assert "(1)" in s and "(2)" in s and "(3)" in s
    assert "не хватает правила" in s          # версия «нет правила»
    assert "цена или приоритет заданы неверно" in s
    assert "неудобен интерфейс" in s
    # прямая оговорка: оператор мог ошибиться
    assert "НЕ доказывает правоту оператора" in s
    assert len(s) <= 600


def test_long_instance_names_do_not_truncate_the_caveat(store):
    # кластер адресный, значит внутри ОДНОГО экземпляра — но имя длинное
    for _ in range(6):
        _rec(store, inst="экземпляр-" + "ю" * 60, pid="P1", frm="E1",
             to=None)
    s = store.build_crs(min_size=5)[0]["summary"]
    assert len(s) <= 600
    assert s.endswith("а не правка настроек.")   # оговорка уцелела
    assert "НЕ доказывает правоту оператора" in s


def test_long_names_everywhere_keep_summary_intact(store):
    # худший случай: длинные имя сотрудника, код пейринга и экземпляра —
    # теперь экземпляр входит ещё и в адресную фразу места кластера, и
    # оговорка обязана уцелеть всё равно
    for _ in range(6):
        _rec(store, inst="инстанс-" + "я" * 80, pid="П" * 110,
             frm="Ф" * 110, to=None)
    rows = store.build_crs(min_size=5)
    assert rows
    for cr in rows:
        assert len(cr["summary"]) <= 600 and len(cr["title"]) <= 90
        assert cr["summary"].endswith("а не правка настроек.")
        assert "НЕ доказывает правоту оператора" in cr["summary"]


def test_huge_size_and_long_names_still_keep_the_caveat(store, monkeypatch):
    # бюджет считается от РЕАЛЬНОЙ длины каркаса, включая пятизначный size
    monkeypatch.setattr(store, "MAX_EPISODES", 40000)
    monkeypatch.setattr(store, "MAX_EPISODES_TOTAL", 40000)
    cluster = {"key": {"reason": "wrong_cost", "facet": "employee",
                       "instance": "и" * 200, "value": "з" * 200},
               "reason": "wrong_cost", "size": 99999, "sample": [],
               "instances": ["и" * 200]}
    monkeypatch.setattr(store, "clusters", lambda **kw: [cluster])
    cr = store.build_crs(min_size=5)[0]
    assert len(cr["summary"]) <= 600
    assert cr["summary"].endswith("а не правка настроек.")


def test_priority_capped_at_ten(store):
    for _ in range(14):
        _rec(store, pid="P1", frm="E1", to=None)
    assert {cr["priority"] for cr in store.build_crs(min_size=5)} == {10}


def test_below_threshold_yields_nothing(store):
    for _ in range(4):
        _rec(store, pid="P1", frm="E1", to=None)
    assert store.build_crs(min_size=5) == []
    assert len(store.build_crs(min_size=4)) == 2


def test_build_crs_does_not_touch_working_config(store, tmp_path):
    from webapp import registry
    cfg = registry.REPO_ROOT / "webapp" / "config"
    before = {p.name: p.stat().st_mtime_ns for p in cfg.glob("*.json")} \
        if cfg.exists() else {}
    for _ in range(6):
        _rec(store, pid="P1", frm="E1", to=None)
    store.build_crs(min_size=5)
    after = {p.name: p.stat().st_mtime_ns for p in cfg.glob("*.json")} \
        if cfg.exists() else {}
    assert after == before                   # выход контура — только черновик


def test_systemic_cluster_needs_higher_threshold(store):
    """Найдено проверкой на живом стенде (2026-07-20): переделки с ОДНОЙ
    причиной по РАЗНЫМ пейрингам и людям не давали ни одного кластера —
    самый частый в жизни случай был невидим. Системный срез его ловит, но
    порог у него кратно выше: улика слабее адресных."""
    # 5 переделок, все разные пейринги и люди, причина одна
    for i in range(5):
        _rec(store, pid=f"P{i}", frm=f"A{i}", to=f"B{i}",
             reason="missing_rule")
    # адресных кластеров нет: ни один ключ не повторился
    cls = store.clusters(min_size=3)
    assert not [c for c in cls if c["key"]["facet"] in ("employee",
                                                        "pairing")]
    # системного тоже ещё нет: 5 < 3*SYSTEMIC_FACTOR
    assert not [c for c in cls if c["key"]["facet"] == "systemic"]

    for i in range(5, 10):
        _rec(store, pid=f"P{i}", frm=f"A{i}", to=f"B{i}",
             reason="missing_rule")
    cls = store.clusters(min_size=3)
    sysc = [c for c in cls if c["key"]["facet"] == "systemic"]
    assert len(sysc) == 1
    assert sysc[0]["size"] == 10 and sysc[0]["reason"] == "missing_rule"


def test_systemic_cluster_is_reason_scoped(store):
    """Системный срез считает ОТДЕЛЬНО по каждой причине — иначе он бы
    склеивал несвязанные сигналы в один бессмысленный счётчик."""
    for i in range(9):
        _rec(store, pid=f"X{i}", reason="missing_rule")
    for i in range(9):
        _rec(store, pid=f"Y{i}", reason="data_error")
    sysc = [c for c in store.clusters(min_size=3)
            if c["key"]["facet"] == "systemic"]
    assert {c["reason"] for c in sysc} == {"missing_rule", "data_error"}
    assert all(c["size"] == 9 for c in sysc)
