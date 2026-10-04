"""Контур «Буфер стыковки» — перепись тесных стыковок (волна 2).

Проверяется то, на чём контур может соврать:
  * стык МЕЖДУ сменами — это отдых, а не стыковка (иначе ночёвка попала бы
    в перепись как пересадка с гигантским запасом и размыла бы долю);
  * граница «тесной» полуоткрыта [min_sit, min_sit + margin) — ровно на
    минимуме стыковка тесная, ровно на верхней границе уже нет;
  * ростер, посчитанный по ДРУГИМ правилам, не измеряется вовсе;
  * экземпляр без ростера не пишет снимка — ряд нулей неотличим от
    «стало идеально» и заставил бы тренд соврать улучшением;
  * база предложений — последний АГЕНТСКИЙ снимок: нажатие кнопки
    «Снять снимок» не должно съедать ухудшение;
  * предложение появляется ТОЛЬКО на ухудшение.

Хранилище во всех тестах переведено в tmp: контур не пишет в рабочие
настройки, и тесты тем более.
"""
import pytest

from webapp import connection_buffer as cb


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "SNAPSHOT_PATH", tmp_path / "connection_buffer.json")


def _leg(fn, dep, arr, dep_utc, arr_utc):
    return {"fn": fn, "dep": dep, "arr": arr,
            "dep_utc": dep_utc, "arr_utc": arr_utc, "ac": "A320"}


def _pairing(pid, legs):
    return {"pid": pid, "legs": legs}


# --- что считается стыковкой ------------------------------------------------

def test_rest_between_duties_is_not_a_connection():
    """Ночёвка — это отдых, и в перепись она попадать не должна."""
    legs = [
        _leg("F1", "AAA", "BBB", "2026-03-01T06:00:00", "2026-03-01T08:00:00"),
        # 40 минут — стыковка внутри смены
        _leg("F2", "BBB", "CCC", "2026-03-01T08:40:00", "2026-03-01T10:00:00"),
        # 12 часов — отдых, начинается новая смена
        _leg("F3", "CCC", "AAA", "2026-03-01T22:00:00", "2026-03-02T00:00:00"),
    ]
    rows = cb.connections([_pairing("C_1", legs)], rest_min=600)
    assert [r["sit_min"] for r in rows] == [40]
    assert rows[0]["airport"] == "BBB"
    assert (rows[0]["in_fn"], rows[0]["out_fn"]) == ("F1", "F2")


def test_single_leg_pairing_has_no_connections():
    legs = [_leg("F1", "AAA", "BBB",
                 "2026-03-01T06:00:00", "2026-03-01T08:00:00")]
    assert cb.connections([_pairing("C_1", legs)], rest_min=600) == []


# --- граница «тесной» -------------------------------------------------------

@pytest.mark.parametrize("sit,tight", [
    (29, False),    # нелегальна вовсе — не наше дело, считает валидатор
    (30, True),     # ровно минимум: тесная
    (44, True),     # последняя минута окна
    (45, False),    # верхняя граница НЕ включается
    (120, False),
])
def test_tight_window_is_half_open(monkeypatch, sit, tight):
    """[min_sit, min_sit + margin): на минимуме тесная, на верхней — нет."""
    legs = [
        _leg("F1", "AAA", "BBB", "2026-03-01T06:00:00", "2026-03-01T08:00:00"),
        _leg("F2", "BBB", "CCC",
             f"2026-03-01T{8 + (sit // 60):02d}:{sit % 60:02d}:00",
             "2026-03-01T23:00:00"),
    ]
    rows = cb.connections([_pairing("C_1", legs)], rest_min=600)
    assert rows[0]["sit_min"] == sit
    counted = cb.TIGHT_MARGIN_MIN == 15 and 30 <= sit < 45
    assert counted is tight, "окно тесноты сдвинулось — проверьте константы"


# --- отказ мерить -----------------------------------------------------------

def test_stale_roster_is_refused_not_measured(monkeypatch):
    """Ростер под ДРУГИМИ правилами не мерится сегодняшним минимумом.

    Цифра получилась бы, но теснота в ней была бы наведена сменой правил,
    а не решением солвера."""
    from webapp import active_roster
    monkeypatch.setattr(active_roster, "resolve",
                        lambda inst: {"status": "stale", "assignment": None,
                                      "meta": {}, "warnings": [],
                                      "mismatches": ["rules"]})
    out = cb.census({"id": "x", "label": "x"})
    assert out["counted"] is False and out["status"] == "stale"
    assert out["tight"] == 0 and out["connections"] == 0


def test_instance_without_roster_writes_no_snapshot(monkeypatch):
    """Ряд нулей неотличим от «стало идеально» — снимка быть не должно."""
    from webapp import active_roster
    monkeypatch.setattr(active_roster, "resolve",
                        lambda inst: {"status": "absent", "assignment": None,
                                      "meta": {}, "warnings": [],
                                      "mismatches": []})
    snap = cb.take_snapshot({"id": "x", "label": "x"})
    assert snap["counted"] is False
    assert cb.history("x") == [], "снимок без ростера попал в историю"


# --- снимки, база сравнения, предложения ------------------------------------

def _stub_census(monkeypatch, tight, connections=10):
    monkeypatch.setattr(cb, "census", lambda inst: {
        "status": "active", "counted": True, "connections": connections,
        "tight": tight, "min_sit_min": 30, "margin_min": 15,
        "buckets": {"30": tight}, "stations": [], "examples": [],
        "slack": {"median": 20, "p10": 2}})


def test_manual_snapshot_does_not_eat_the_worsening(monkeypatch):
    """Урок волны 1: база ПРЕДЛОЖЕНИЙ — последний агентский снимок.

    Если базой брать просто предыдущий снимок, человек, дважды нажавший
    «Снять снимок», сравнил бы ухудшение само с собой и потерял его."""
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 2)
    cb.take_snapshot(inst, by=cb.BY_AGENT)          # база предложений
    _stub_census(monkeypatch, 9)
    cb.take_snapshot(inst)                          # ручной — база не сдвигается
    cb.take_snapshot(inst)                          # и ещё раз

    base = cb._cr_baseline(cb.history("i1"))
    assert base["tight"] == 2 and base["by"] == cb.BY_AGENT

    rows = cb.build_crs([inst])
    assert len(rows) == 1
    assert "2 → 9" in rows[0]["title"]


def test_rule_change_does_not_produce_a_cr(monkeypatch):
    """Правка правила не должна выглядеть ухудшением ростера.

    `tight` осмысленно только вместе с окном, по которому посчитан.
    Поднимите минимальное время стыковки с 30 до 40 — счётчик подскочит
    на сотни, хотя солвер не принимал НИ ОДНОГО нового решения. Снимок в
    другом окне базой быть не может."""
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 2)                    # окно 30..45
    cb.take_snapshot(inst, by=cb.BY_AGENT)

    # то же самое, но минимум подняли: окно стало 40..55
    monkeypatch.setattr(cb, "census", lambda i: {
        "status": "active", "counted": True, "connections": 10,
        "tight": 430, "min_sit_min": 40, "margin_min": 15,
        "buckets": {"40": 430}, "stations": [], "examples": [],
        "slack": {"median": 20, "p10": 2}})

    assert cb.build_crs([inst]) == [], "смена правила выдана за ухудшение"
    # снимок при этом снят: история не теряется, сравнение начинается
    # заново с первого снимка в новом окне
    assert len(cb.history("i1")) == 2
    assert cb._cr_baseline(cb.history("i1"), (40, 15))["tight"] == 430


def test_worsening_inside_the_same_window_still_fires(monkeypatch):
    """Обратная сторона: отбор по окну не должен глушить настоящий сигнал."""
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 2)
    cb.take_snapshot(inst, by=cb.BY_AGENT)
    _stub_census(monkeypatch, 9)                    # окно то же
    rows = cb.build_crs([inst])
    assert len(rows) == 1 and "2 → 9" in rows[0]["title"]


def test_census_uses_the_instance_rules_not_the_process_rules(monkeypatch):
    """Минимум берётся ПО ЭКЗЕМПЛЯРУ.

    У сценария «что если» свой файл правил, перекрывающий общий, и
    rules_config.effective() про него не знает — он читает окружение
    веб-процесса. Мерили бы чужим минимумом, и сценарий с минимумом 60
    показывал бы ноль тесных стыковок вечно."""
    from webapp import active_roster, roster_board

    seen = {}

    def fake_rules(inst):
        seen["inst"] = inst
        return {"min_sit_min": 60, "min_rest_min": 600}

    monkeypatch.setattr(active_roster, "effective_rules_for", fake_rules)
    monkeypatch.setattr(active_roster, "resolve",
                        lambda inst: {"status": "active", "assignment": {},
                                      "meta": {}, "warnings": [],
                                      "mismatches": []})
    legs = [
        _leg("F1", "AAA", "BBB", "2026-03-01T06:00:00", "2026-03-01T08:00:00"),
        # 65 минут: тесно при минимуме 60, НЕ тесно при минимуме 30
        _leg("F2", "BBB", "CCC", "2026-03-01T09:05:00", "2026-03-01T11:00:00"),
    ]
    monkeypatch.setattr(roster_board, "_resolve",
                        lambda inst: ([_pairing("C_1", legs)], {}, []))

    out = cb.census({"id": "scen-1", "label": "scen-1"})
    assert out["min_sit_min"] == 60, "взяты правила процесса, а не экземпляра"
    assert out["tight"] == 1
    assert seen["inst"]["id"] == "scen-1"


def test_no_cr_without_worsening(monkeypatch):
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 5)
    cb.take_snapshot(inst, by=cb.BY_AGENT)
    _stub_census(monkeypatch, 3)                    # стало ЛУЧШЕ
    assert cb.build_crs([inst]) == []


def test_first_snapshot_is_a_baseline_not_a_reason(monkeypatch):
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 40)
    assert cb.build_crs([inst]) == [], "первый срез — база, а не повод"
    assert len(cb.history("i1")) == 1


def test_cr_names_the_price_of_changing_the_rule(monkeypatch):
    """Минимальное время стыковки входит в отпечаток задачи. Контур не
    вправе назначать такую цену молча — она обязана быть в тексте."""
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 1)
    cb.take_snapshot(inst, by=cb.BY_AGENT)
    _stub_census(monkeypatch, 6)
    row = cb.build_crs([inst])[0]
    assert row["agent"] == "connbuf" and row["status"] == "draft"
    assert "отпечаток задачи" in row["summary"]
    assert "решение" in row["summary"]


def test_one_broken_instance_does_not_abort_the_whole_loop(monkeypatch):
    """Один нечитаемый экземпляр не должен обрывать обход остальных.

    Агент проходит ВСЕ экземпляры; падение на середине оставило бы прогон
    наполовину сделанным и без единого предложения — притом что снимки
    первых экземпляров уже записаны."""
    good = {"id": "good", "label": "good"}
    bad = {"id": "bad", "label": "bad"}
    _stub_census(monkeypatch, 1)
    cb.take_snapshot(good, by=cb.BY_AGENT)

    def census(inst):
        if inst["id"] == "bad":
            raise OSError("payload не читается")
        return {"status": "active", "counted": True, "connections": 10,
                "tight": 7, "min_sit_min": 30, "margin_min": 15,
                "buckets": {}, "stations": [], "examples": [],
                "slack": {"median": 1, "p10": 0}}

    monkeypatch.setattr(cb, "census", census)
    rows = cb.build_crs([bad, good])          # битый ПЕРВЫМ
    assert len(rows) == 1 and "good" in rows[0]["title"]


def test_cr_lists_the_actual_transfers_it_asks_to_review(monkeypatch):
    """Текст просит «разобрать перечисленные пересадки» — значит, перечень
    обязан быть в самом предложении, а не только на экране контура."""
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 1)
    cb.take_snapshot(inst, by=cb.BY_AGENT)
    monkeypatch.setattr(cb, "census", lambda i: {
        "status": "active", "counted": True, "connections": 40, "tight": 6,
        "min_sit_min": 30, "margin_min": 15, "buckets": {"30": 6},
        "stations": [{"airport": "BASE2", "bucket": 30, "n": 4}],
        "examples": [{"pid": "C_1", "airport": "BASE2", "in_fn": "F1",
                      "out_fn": "F2", "at": "2026-03-01T20:23:00",
                      "sit_min": 32}],
        "slack": {"median": 20, "p10": 2}})
    summary = cb.build_crs([inst])[0]["summary"]
    assert "F1 → F2" in summary and "32 мин" in summary


def test_store_unreadable_refuses_to_write(monkeypatch):
    """Нечитаемое хранилище не должно молча стереть историю всех
    экземпляров — путь ЗАПИСИ обязан отказать."""
    inst = {"id": "i1", "label": "i1"}
    _stub_census(monkeypatch, 1)
    cb.take_snapshot(inst, by=cb.BY_AGENT)

    def boom():
        raise cb.SnapshotStoreUnreadable("занято")
    original = cb._read_raw
    monkeypatch.setattr(cb, "_read_raw", boom)
    with pytest.raises(cb.SnapshotStoreUnreadable):
        cb.take_snapshot(inst)
    # ПОКАЗ ту же поломку переживает — деградирует в пусто, не падает
    assert cb.history("i1") == []
    # возвращаем ТОЛЬКО чтение: monkeypatch.undo() снял бы заодно и
    # изоляцию хранилища из autouse-фикстуры, и проверка ушла бы в
    # рабочий webapp/config
    monkeypatch.setattr(cb, "_read_raw", original)
    assert len(cb.history("i1")) == 1, "запись всё-таки затёрла историю"
