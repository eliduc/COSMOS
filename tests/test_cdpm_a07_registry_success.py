# -*- coding: utf-8 -*-
"""A07 (аудит «четыре принципа ВМП», CONS-02, fcec7a2e): аудит-цикл не
может закончиться успехом при живой критической находке реестра.

Ожидания — по контракту П0 §1.2 и контрпримеру аудитора
(`consistency/probe_consistency.py::exchange_probe`) с ИНВЕРСИЕЙ:
раньше два «чистых» раунда по метрикам давали «успех», хотя находка A1
уровня КРИТИЧЕСКОЕ осталась в состоянии «патч отбракован».

Охраняемое:
- один перечень живых состояний `LIVE_STATES` (включая «патч
  отбракован» и «отклонено кодом») и одна функция
  `registry_blockers(registry)` — носитель истины для цикла, панели,
  гейта и раннера;
- `decide(history, max_rounds, registry=…)` возвращает «не закрыто»
  при непустых блокирующих находках, «успех» — только при пустых;
- молчание аудитора в следующих раундах закрытием не считается;
- гейт цикла красный, когда записанный вердикт «успех» противоречит
  реестру."""
import json

import pytest

from cdpm import exchange as ex
from cdpm import webui
from cdpm.gates import gate_exchange


def _doc():
    return {"meta": {"customer": "SYNTHETIC-AUDIT", "mode": 1,
                     "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "synthetic.md",
                         "sha256": "0" * 64}],
            "rules": [{"id": "R-A", "title": "R-A", "statement": "x <= 1",
                       "zone": "BOTH", "kind": "HARD",
                       "sources": [{"sid": "S1", "anchor": "R-A"}]}],
            "questions": [], "objectives": []}


def _dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _three_rounds(out, doc):
    """Раунд 1: критическая находка A1, патч обработчика отбракован;
    раунды 2 и 3: аудитор молчит (пакеты пусты)."""
    registry, history = {}, []
    for rnd in (1, 2, 3):
        rm = {"id": "A1", "severity": "КРИТИЧЕСКОЕ",
              "defect_class": "искажение", "target": {"rule": "R-A"},
              "source_refs": [{"sid": "S1", "anchor": "R-A"}],
              "text": "Synthetic unresolved distortion", "evidence": "x <= 1"}
        pack = {"round": rnd, "mode": "полный",
                "remarks": [rm] if rnd == 1 else []}
        # A07-R1 (находка аудитора 12.09.2026): каталог этой фикстуры
        # судит ДЕТЕРМИНИРОВАННАЯ проводка гейта — значит, он обязан
        # быть таким, каким его написал бы цикл. Принятый ответ БЕЗ
        # заплаты — «пустое обещание»: та же живая находка «патч
        # отбракован», ради которой фикстура и существует, и круг без
        # заплат воспроизводится проводкой один в один (замороженный
        # комплект ему не нужен — судить нечего).
        response = {"answers": [
            {"remark": "A1", "verdict": "ПРИНЯТО", "basis": "x <= 1",
             "change": "correct distortion"}]
            if rnd == 1 else []}
        rejected = [{"answer": "A1",
                     "reasons": [ex.EMPTY_PROMISE_REASON]}] \
            if rnd == 1 else []
        assert ex.validate_remarks(pack, doc) == []
        assert ex.validate_response(response, pack) == []
        ex.registry_update(registry, rnd, pack, response,
                           patch_rejected=rejected)
        ex.registry_post_round(registry, set(), doc, doc)
        metrics = ex.metrics_of(pack)
        metrics.update(round=rnd, mode="полный", patched=False)
        history.append(metrics)
        rd = out / "rounds" / f"round-{rnd:02d}"
        for name, obj in [("remarks", pack), ("response", response),
                          ("metrics", metrics), ("patch-rejections", rejected)]:
            _dump(rd / (name + ".json"), obj)
        _dump(out / "versions" / f"model-v{rnd:02d}.json", doc)
    return registry, history


def test_live_states_name_rejected_patch_and_code_rejection():
    """Один перечень живых состояний для всех читателей."""
    assert "патч отбракован" in ex.LIVE_STATES
    assert "отклонено кодом" in ex.LIVE_STATES
    assert "открыто" in ex.LIVE_STATES and "тупик" in ex.LIVE_STATES
    assert "закрыто" not in ex.LIVE_STATES
    # состояния кода — в полном перечне STATES
    assert "патч отбракован" in ex.STATES
    assert "отклонено кодом" in ex.STATES


def test_registry_blockers_list_the_rejected_critical_finding(tmp_path):
    doc = _doc()
    registry, history = _three_rounds(tmp_path / "ex", doc)
    assert registry["findings"][0]["state"] == "патч отбракован"
    blockers = ex.registry_blockers(registry)
    assert [b["id"] for b in blockers] == [registry["findings"][0]["id"]]
    assert blockers[0]["severity"] == "КРИТИЧЕСКОЕ"


def test_decide_is_not_success_while_a_critical_finding_is_live(tmp_path):
    """Контрпример аудитора: два чистых раунда после отбракованного
    патча. Раньше — «успех»; теперь — «не закрыто»."""
    doc = _doc()
    registry, history = _three_rounds(tmp_path / "ex", doc)
    assert ex.decide(history, 9, registry=registry) == "не закрыто"


def test_decide_success_when_registry_is_clean(tmp_path):
    """Положительный контроль: та же история метрик при закрытой находке
    даёт «успех»."""
    doc = _doc()
    registry, history = _three_rounds(tmp_path / "ex", doc)
    f = registry["findings"][0]
    f["state"] = "закрыто"
    f["closed_round"] = 3
    assert ex.registry_blockers(registry) == []
    assert ex.decide(history, 9, registry=registry) == "успех"


def test_serious_finding_does_not_block_but_stays_visible(tmp_path):
    """Живая находка уровня СЕРЬЁЗНОЕ не блокирует успех (порог Б/К),
    но остаётся живой."""
    doc = _doc()
    registry, history = _three_rounds(tmp_path / "ex", doc)
    registry["findings"][0]["severity"] = "СЕРЬЁЗНОЕ"
    assert ex.registry_blockers(registry) == []
    assert ex.decide(history, 9, registry=registry) == "успех"


def test_gate_is_red_when_recorded_success_contradicts_registry(tmp_path):
    """Гейт цикла судит по реестру: записанный «успех» при живой
    критической находке — нарушение с именем находки."""
    doc = _doc()
    out = tmp_path / "ex"
    _dump(out / "versions" / "model-v00.json", doc)
    registry, history = _three_rounds(out, doc)
    _dump(out / "registry.json", registry)
    _dump(out / "verdict.json", {"verdict": "успех", "rounds": 3,
                                 "max_rounds": 9})
    problems, counters = gate_exchange.check(out)
    assert any("A1" in p or registry["findings"][0]["id"] in p
               for p in problems), problems
    # честный вердикт с перечнем блокирующих — гейт зелёный
    _dump(out / "verdict.json", {
        "verdict": "не закрыто", "rounds": 3, "max_rounds": 9,
        "registry_verdict": {"blockers": [
            {"id": registry["findings"][0]["id"], "severity": "КРИТИЧЕСКОЕ",
             "state": "патч отбракован", "opened_round": 1}], "count": 1}})
    problems, counters = gate_exchange.check(out)
    assert problems == [], problems


# ── Дополнения исполнителя И1 (12.09.2026): форма реестра, один закон
# панели и гейта. Ожидания — контракт П0 §1.2 (перечень STATES закрыт,
# schema_version registry/2, registry_verdict в verdict.json).

def test_states_closed_list_and_registry_update_fails_closed():
    """Перечень STATES полон и закрыт; состояние вне перечня — отказ
    формы при проводке, а не молчаливая запись."""
    assert set(ex.LIVE_STATES) <= set(ex.STATES)
    for state in ("арбитр: обработчик прав", "арбитр: аудитор прав",
                  "арбитр: в анкету", "не разрешено арбитром",
                  "принято", "исправлено", "закрыто"):
        assert state in ex.STATES
    registry = {"findings": [
        {"id": "X1", "key": "k", "state": "прижато",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
         "closed_round": None}]}
    with pytest.raises(ValueError, match="вне перечня"):
        ex.registry_update(registry, 2, {"remarks": []}, {"answers": []})


def _mixed_registry():
    return {"findings": [
        {"id": "F1", "key": "k1", "state": "открыто",
         "severity": "СЕРЬЁЗНОЕ", "opened_round": 1, "closed_round": None},
        {"id": "F2", "key": "k2", "state": "патч отбракован",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
         "closed_round": None},
        {"id": "F3", "key": "k3", "state": "не разрешено арбитром",
         "severity": "БЛОКИРУЮЩЕЕ", "opened_round": 2,
         "closed_round": None},
        # Унаследованный реестр (писатель до 12.09): «отклонено кодом»
        # с проставленным closed_round и closed_by «замок кода». Это
        # ТЕХНИЧЕСКАЯ отбраковка заплаты, а не исполненный патч и не
        # решение арбитра, — миграция чтения возвращает её в ЖИВЫЕ
        # (A07-F3; прежнее ожидание «закрыта» было тем самым классом
        # ложного успеха, ради которого запись и сохранена в наборе).
        {"id": "F4", "key": "k4", "state": "отклонено кодом",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 2, "closed_round": 2,
         "closed_by": "замок кода"},
        {"id": "F5", "key": "k5", "state": "закрыто",
         "severity": "БЛОКИРУЮЩЕЕ", "opened_round": 1, "closed_round": 2},
        {"id": "F6", "key": "k6", "state": "арбитр: обработчик прав",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
         "closed_round": None},
        # Эхо решённого арбитром ключа — не живая.
        {"id": "F7", "key": "k6", "state": "открыто",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 3,
         "closed_round": None},
        {"id": "F8", "key": "k8", "state": "тупик",
         "severity": "СЕРЬЁЗНОЕ", "opened_round": 1, "closed_round": None},
    ]}


def test_live_findings_and_blockers_are_one_law_for_panel(tmp_path):
    """Панель (`webui._live_findings`) и цикл/гейт читают один предикат
    `exchange.live_findings`; блокирующие — его Б/К-подмножество.
    Унаследованная отбраковка замком кода (F4) — живая: закрытием её
    делал прежний писатель, а не исполнение (A07-F3)."""
    reg = _mixed_registry()
    assert [f["id"] for f in ex.live_findings(reg)] \
        == ["F1", "F2", "F3", "F4", "F8"]
    assert [f["id"] for f in ex.registry_blockers(reg)] \
        == ["F2", "F3", "F4"]
    assert ex.registry_verdict_of(reg) == {"count": 3, "blockers": [
        {"id": "F2", "severity": "КРИТИЧЕСКОЕ", "state": "патч отбракован",
         "opened_round": 1},
        {"id": "F3", "severity": "БЛОКИРУЮЩЕЕ",
         "state": "не разрешено арбитром", "opened_round": 2},
        {"id": "F4", "severity": "КРИТИЧЕСКОЕ",
         "state": "отклонено кодом", "opened_round": 2}]}
    assert ex.registry_verdict_of({}) == {"blockers": [], "count": 0}
    # Чтение реестр НЕ переписывает: вход остаётся как был.
    assert reg["findings"][3]["closed_round"] == 2
    run_dir = tmp_path / "run"
    _dump(run_dir / "exchange" / "registry.json", reg)
    assert [f["id"] for f in webui._live_findings(run_dir)] \
        == ["F1", "F2", "F3", "F4", "F8"]
    assert json.loads((run_dir / "exchange" / "registry.json")
                      .read_text(encoding="utf-8")) == reg
    assert webui._live_findings(tmp_path / "нет") == []


def test_exchange_verdict_words_name_blocker_count():
    """Состояние прогона: «не закрыто: N блокирующих»; прочие вердикты
    — словом как есть."""
    summary = {"verdict": "не закрыто",
               "registry_verdict": {"count": 2,
                                    "blockers": [{"id": "a"}, {"id": "b"}]}}
    assert webui._exchange_verdict_words(summary) \
        == "не закрыто: 2 блокирующих"
    assert webui._exchange_verdict_words({"verdict": "успех"}) == "успех"
    assert webui._exchange_verdict_words({"verdict": "стагнация"}) \
        == "стагнация"


def test_gate_rejects_unknown_schema_and_stale_registry_verdict(tmp_path):
    """Форма носителя: неизвестная версия схемы реестра — красный;
    перечень registry_verdict, не совпадающий с реестром, — красный;
    сводка «не закрыто» без перечня — красный."""
    doc = _doc()
    out = tmp_path / "ex"
    _dump(out / "versions" / "model-v00.json", doc)
    registry, history = _three_rounds(out, doc)
    fid = registry["findings"][0]["id"]
    honest = {"verdict": "не закрыто", "rounds": 3, "max_rounds": 9,
              "registry_verdict": {"blockers": [
                  {"id": fid, "severity": "КРИТИЧЕСКОЕ",
                   "state": "патч отбракован", "opened_round": 1}],
                  "count": 1}}
    # Положительный контроль: registry/2 и честная сводка — зелёный.
    _dump(out / "registry.json", dict(registry, schema_version="registry/2"))
    _dump(out / "verdict.json", honest)
    problems, _c = gate_exchange.check(out)
    assert problems == [], problems
    # Неизвестная версия схемы.
    _dump(out / "registry.json", dict(registry, schema_version="registry/9"))
    problems, _c = gate_exchange.check(out)
    assert any("версия схемы" in p for p in problems), problems
    # Перечень сводки расходится с реестром.
    _dump(out / "registry.json", dict(registry, schema_version="registry/2"))
    _dump(out / "verdict.json",
          dict(honest, registry_verdict={"blockers": [], "count": 0}))
    problems, _c = gate_exchange.check(out)
    assert any("registry_verdict не сходится" in p for p in problems), \
        problems
    # «Не закрыто» без перечня.
    _dump(out / "verdict.json", {"verdict": "не закрыто", "rounds": 3,
                                 "max_rounds": 9})
    problems, _c = gate_exchange.check(out)
    assert any("без registry_verdict" in p for p in problems), problems


# ── Миграция показа (контракт П0 §1.2): записанный «успех» старого
# цикла при живой Б/К-находке судится реестром при чтении, файлы
# истории не переписываются.

def test_verdict_by_registry_rejudges_recorded_success_only(tmp_path):
    """Чистая функция: «успех» + живой блокер → «не закрыто (по
    реестру)»; «успех» при чистом реестре и любой иной вердикт — как
    записано."""
    doc = _doc()
    registry, _ = _three_rounds(tmp_path / "ex", doc)
    assert ex.registry_blockers(registry)
    assert ex.verdict_by_registry("успех", registry)         == "не закрыто (по реестру)"
    assert ex.verdict_by_registry("успех", {"findings": []}) == "успех"
    assert ex.verdict_by_registry("успех", None) == "успех"
    assert ex.verdict_by_registry("лимит", registry) == "лимит"
    assert ex.verdict_by_registry("не закрыто", registry) == "не закрыто"
    # «остановлен по Б/К» с A07 невозможен при живом блокере — старая
    # запись пересуживается так же, как «успех».
    assert ex.verdict_by_registry("остановлен по Б/К", registry) \
        == "не закрыто (по реестру)"
    assert ex.verdict_by_registry("остановлен по Б/К", None) \
        == "остановлен по Б/К"


def test_exchange_status_shows_recorded_success_by_registry(
        tmp_path, monkeypatch):
    """Ручка состояния цикла отдаёт вердикт по реестру рядом с
    записанным: старый «успех» показывается как «не закрыто (по
    реестру)» с числом живых блокирующих, verdict.json не тронут."""
    doc = _doc()
    out = tmp_path / "exchange"
    _dump(out / "versions" / "model-v00.json", doc)
    registry, history = _three_rounds(out, doc)
    _dump(out / "registry.json", registry)
    stored = {"verdict": "успех", "rounds": 3, "max_rounds": 9}
    _dump(out / "verdict.json", stored)
    _dump(tmp_path / "status.json", {"state": "аудит-цикл: успех"})
    monkeypatch.setattr(webui, "_run_dir", lambda rid: tmp_path)
    got = webui.exchange_status("run-0001")
    assert got["verdict"]["verdict"] == "успех"          # как записано
    assert got["verdict_by_registry"] == "не закрыто (по реестру)"
    assert got["registry_verdict"]["count"] == 1
    assert got["registry_verdict"]["blockers"][0]["severity"]         == "КРИТИЧЕСКОЕ"
    assert json.loads((out / "verdict.json").read_text(
        encoding="utf-8")) == stored                     # не переписан
    # чистый реестр: показ совпадает с записью, сводка блокеров пуста
    _dump(out / "registry.json", {"schema_version": ex.REGISTRY_SCHEMA,
                                  "findings": []})
    got = webui.exchange_status("run-0001")
    assert got["verdict_by_registry"] == "успех"
    assert got["registry_verdict"] == {"blockers": [], "count": 0}
    # без verdict.json — нет и показа по реестру
    (out / "verdict.json").unlink()
    got = webui.exchange_status("run-0001")
    assert got["verdict"] is None
    assert got["verdict_by_registry"] is None


# ── Правки по неавтор-ревью A07 (12.09.2026): живое «отклонено кодом»,
# писатель судит форму, ОСТ-1 гейта судит реестр.

def test_code_rejection_keeps_finding_live(tmp_path):
    """Ревью, находка 2: замок кода отверг заплату — находка живая
    (closed_round не ставится), состояние — «тупик» вида «отклонено
    кодом» (§12.8, слово владельца «Вариант 1»): дозревание немедленное,
    потому что действовать не вправе ни одна сторона, — так у находки
    появляется путь к арбитру. Критическая держит «не закрыто» на чистом
    хвосте.

    УНАСЛЕДОВАННАЯ запись старой формы — «отклонено кодом» с
    проставленным closed_round — ТОЖЕ живая (A07-F3): этот номер ставил
    прежний писатель технической отбраковкой, а не исполненный патч и не
    решение арбитра. Прежнее ожидание A09 `registry_blockers(legacy) ==
    []` заменено здесь по прямому разрешению аудитора
    (DECISION-A07-A09-INTEGRATION-LAW от 12.09.2026): на общей голове
    действует F3, а дозревание §12.8 доводит ту же запись до тупика.
    Фикстура старой формы строится ЯВНО, а не из результата нового
    писателя: иначе смена писателя незаметно меняла бы исторический вход
    проверки."""
    reg = {"findings": [
        {"id": "A1", "key": "k1", "severity": "КРИТИЧЕСКОЕ",
         "state": "открыто", "opened_round": 1, "closed_round": None},
        {"id": "A2", "key": "k2", "severity": "СЕРЬЁЗНОЕ",
         "state": "закрыто", "opened_round": 1, "closed_round": 1}]}
    marked = ex.mark_code_rejected(reg, {"k1": "смена семьи",
                                         "k2": "смена семьи"}, 2)
    assert marked == ["k1"]
    a1 = reg["findings"][0]
    assert a1["state"] == "тупик"                       # §12.8
    assert a1["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    assert a1["closed_round"] is None
    assert a1["code_rejected_round"] == 2
    assert a1["code_rejection"] == "смена семьи"
    assert reg["findings"][1]["state"] == "закрыто"
    assert [f["id"] for f in ex.registry_blockers(reg)] == ["A1"]
    clean = {"weighty": (0, 0, 0), "mode": "полный"}
    assert ex.decide([clean, clean], 9, reg) == ex.VERDICT_NOT_CLOSED
    # A07-F3 на общей голове: унаследованная техническая отбраковка —
    # не исполненный патч и не решение арбитра. Обе формы `closed_by`,
    # названные решением аудитора, судятся одинаково.
    for closed_by in (ex.LEGACY_CODE_CLOSURE, None):
        legacy = {"schema_version": ex.REGISTRY_SCHEMA, "findings": [
            {"id": "A1", "key": "k1", "severity": "КРИТИЧЕСКОЕ",
             "state": ex.CODE_DEADLOCK_KIND, "opened_round": 1,
             "closed_round": 1, "closed_by": closed_by,
             "code_rejected_round": 1,
             "code_rejection": "смена семьи"}]}
        assert [f["id"] for f in ex.registry_blockers(legacy)] == ["A1"]
        # Миграция чтения сохраняет историю: прежний номер закрытия
        # виден отдельным полем, а вход функции не мутируется.
        shown = ex.normalize_registry(legacy)["findings"][0]
        assert shown["legacy_code_closure_round"] == 1
        assert shown["closed_round"] is None
        assert shown["closed_by"] is None
        assert legacy["findings"][0]["closed_round"] == 1
        # Композиция A07 × A09: тот же вход, прочитанный ЕДИНЫМ
        # загрузчиком, дозревает до тупика с кодовой причиной — у
        # унаследованной записи появляется путь к арбитру, и живость
        # F3 при этом не теряется. Повторное чтение идемпотентно.
        path = tmp_path / f"legacy-{closed_by or 'none'}.json"
        _dump(path, legacy)
        loaded = ex.load_registry(path)
        got = loaded["findings"][0]
        assert got["state"] == "тупик"
        assert got["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
        assert got["legacy_code_closure_round"] == 1
        assert got["closed_round"] is None
        assert [f["id"] for f in ex.registry_blockers(loaded)] == ["A1"]
        assert ex.load_registry(path) == loaded
        assert json.loads(path.read_text(encoding="utf-8")) == legacy


def test_registry_writer_rejects_state_outside_list(tmp_path):
    """Ревью, находка 7: закон формы держится на писателе — состояние
    вне перечня не записывается."""
    path = tmp_path / "registry.json"
    with pytest.raises(AssertionError, match="вне перечня"):
        ex._dump_registry(path, {"findings": [
            {"id": "X", "key": "k", "state": "прижато",
             "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
             "closed_round": None}]})
    assert not path.exists()
    ex._dump_registry(path, {"findings": []})
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] \
        == ex.REGISTRY_SCHEMA


def test_gate_red_on_bk_stop_with_live_blocker(tmp_path):
    """Ревью, находка 1 (гейт): «остановлен по Б/К» при живой
    критической находке реестра — подмена вердикта с именем находки."""
    doc = _doc()
    out = tmp_path / "ex"
    _dump(out / "versions" / "model-v00.json", doc)
    registry, history = _three_rounds(out, doc)
    _dump(out / "registry.json", registry)
    _dump(out / "verdict.json", {"verdict": "остановлен по Б/К",
                                 "rounds": 3, "max_rounds": 9})
    problems, _counters = gate_exchange.check(out)
    fid = registry["findings"][0]["id"]
    assert any("остановлен по Б/К" in p and "живых блокирующих" in p
               and fid in p for p in problems), problems


def test_state_words_of_old_cycle_are_rejudged_by_registry(tmp_path):
    """Миграция показа доходит до списка прогонов и карточки: «аудит-
    цикл: успех» / «аудит остановлен: …» при живой Б/К-находке →
    «аудит-цикл: не закрыто (по реестру): N блокирующих»; чистый
    реестр, другие состояния и прогон без цикла — как записаны;
    status.json не переписывается."""
    doc = _doc()
    out = tmp_path / "exchange"
    _dump(out / "versions" / "model-v00.json", doc)
    registry, _history = _three_rounds(out, doc)
    _dump(out / "registry.json", registry)
    assert webui._state_by_registry(tmp_path, "аудит-цикл: успех") \
        == "аудит-цикл: не закрыто (по реестру): 1 блокирующих"
    assert webui._state_by_registry(
        tmp_path, "аудит остановлен: Б 0, К 0, С 1 — продолжить аудит?") \
        == "аудит-цикл: не закрыто (по реестру): 1 блокирующих"
    assert webui._state_by_registry(tmp_path, "аудит-цикл: лимит") \
        == "аудит-цикл: лимит"
    assert webui._state_by_registry(tmp_path, "новый") == "новый"
    assert webui._state_by_registry(tmp_path, None) is None
    _dump(out / "registry.json", {"schema_version": ex.REGISTRY_SCHEMA,
                                  "findings": []})
    assert webui._state_by_registry(tmp_path, "аудит-цикл: успех") \
        == "аудит-цикл: успех"
    # A07-R5 (закон «реестр после исполненного цикла обязан быть»):
    # прогон БЕЗ каталога цикла показывается как записан ровно до тех
    # пор, пока его состояние исхода цикла не ЗАЯВЛЯЛО. Заявленный
    # исход без реестра — исчезнувший носитель, и прежний зелёный показ
    # такого прогона был fail-open (см. блок тестов A07-R5 ниже).
    assert webui._state_by_registry(tmp_path / "нет", "аудит-цикл идёт") \
        == "аудит-цикл идёт"
    assert "реестр отсутствует после исполненного цикла" in \
        webui._state_by_registry(tmp_path / "нет", "аудит-цикл: успех")


# ── Обязательные замечания независимого аудитора к A07 (ef0ac6f9,
# 12.09.2026): F1 «принятие без исполненного патча», F2 «гейт допускает
# успех с живым блокером при короткой истории», F3 «старое закрытие
# замком кода переживает миграцию», F4 «неизвестная/неполная форма
# реестра чинится до проверки версии». Ожидания — контракт П0 §0 п. 5,
# §1.2, §1.5; сценарии аудитора перенесены с инверсией дефекта и с
# независимыми положительными контролями.

def _cycle_with_prior(out, n, registry, verdict="успех"):
    """Новый цикл, несущий перенесённый реестр, и n пустых ПОЛНЫХ
    раундов: ровно те файлы, которые читает гейт."""
    import copy
    reg = copy.deepcopy(registry)
    doc = _doc()
    _dump(out / "registry-start.json", reg)
    _dump(out / "versions" / "model-v00.json", doc)
    for rnd in range(1, n + 1):
        pack = {"round": rnd, "mode": "полный", "remarks": []}
        resp = {"answers": []}
        ex.registry_update(reg, rnd, pack, resp)
        ex.registry_post_round(reg, set(), doc, doc)
        rd = out / "rounds" / f"round-{rnd:02d}"
        _dump(rd / "remarks.json", pack)
        _dump(rd / "response.json", resp)
        _dump(rd / "metrics.json", ex.metrics_of(pack))
        _dump(out / "versions" / f"model-v{rnd:02d}.json", doc)
    _dump(out / "registry.json", reg)
    _dump(out / "verdict.json",
          {"verdict": verdict, "rounds": n, "max_rounds": 9,
           "registry_verdict": ex.registry_verdict_of(reg)})
    return reg


def _live_finding(state, severity="КРИТИЧЕСКОЕ", **extra):
    return {"id": "OLD-F1", "key": "k-перенесённый", "state": state,
            "severity": severity, "opened_round": 1,
            "closed_round": None, **extra}


@pytest.mark.parametrize("state", ex.LIVE_STATES)
@pytest.mark.parametrize("n", [0, 1, 2])
def test_gate_red_on_forged_success_at_any_history_length(tmp_path, state,
                                                          n):
    """A07-F2: «успех» при непустом registry_blockers — красный гейт
    ВСЕГДА, а не только когда правило останова уже требует иного
    вердикта. При нуле и одном чистом раунде decide говорит
    «продолжать» — прежде подменённый «успех» проходил гейт."""
    reg = {"schema_version": ex.REGISTRY_SCHEMA,
           "findings": [_live_finding(state)]}
    out = tmp_path / f"ex-{n}"
    _cycle_with_prior(out, n, reg)
    problems, _c = gate_exchange.check(out)
    assert any("OLD-F1" in p and "успех" in p for p in problems), problems


@pytest.mark.parametrize("n", [0, 1, 2])
def test_gate_green_on_honest_not_closed_at_any_history_length(tmp_path, n):
    """Положительный контроль к F2: честный вердикт «не закрыто» с тем
    же живым блокером — зелёный при любой длине истории."""
    reg = {"schema_version": ex.REGISTRY_SCHEMA,
           "findings": [_live_finding("патч отбракован")]}
    out = tmp_path / f"ex-{n}"
    _cycle_with_prior(out, n, reg, verdict=ex.VERDICT_NOT_CLOSED)
    assert gate_exchange.check(out)[0] == []


def test_gate_green_on_success_with_clean_registry(tmp_path):
    """Второй положительный контроль к F2: закрытая находка — «успех»
    законен и гейт зелёный (замок судит реестр, а не слово)."""
    reg = {"schema_version": ex.REGISTRY_SCHEMA, "findings": [
        _live_finding("закрыто", closed_round=1)]}
    _cycle_with_prior(tmp_path / "ex", 2, reg, verdict="успех")
    assert gate_exchange.check(tmp_path / "ex")[0] == []


def test_registry_update_refuses_fixed_without_executed_patch():
    """A07-F1, второй (независимый от patch-rejections) замок: проводка
    не ставит «исправлено» принятому ответу, которого нет среди
    ИСПОЛНЕННЫХ, даже когда перечень отбраковок пуст."""
    rm = {"id": "A1", "severity": "КРИТИЧЕСКОЕ",
          "defect_class": "искажение", "target": {"rule": "R-A"},
          "source_refs": [{"sid": "S1", "anchor": "R-A"}],
          "text": "Synthetic unresolved distortion", "evidence": "x <= 1"}
    pack = {"round": 1, "mode": "полный", "remarks": [rm]}
    resp = {"answers": [{"remark": "A1", "verdict": "ПРИНЯТО",
                         "basis": "x <= 1", "change": "обещано исправить"}]}
    promise: dict = {}
    ex.registry_update(promise, 1, pack, resp, patch_rejected=[],
                       executed=set())
    got = promise["findings"][0]
    assert got["state"] == "патч отбракован"
    assert "без исполненной правки" in got["patch_reason"]
    assert [f["id"] for f in ex.registry_blockers(promise)] == [got["id"]]
    # Молчание аудитора следующего раунда такую находку НЕ закрывает.
    ex.registry_update(promise, 2, {"round": 2, "mode": "полный",
                                    "remarks": []}, {"answers": []},
                       executed=set())
    assert promise["findings"][0]["state"] == "патч отбракован"
    assert promise["findings"][0]["closed_round"] is None
    # Положительный контроль: исполненная правка — «исправлено», и
    # молчание следующего раунда закрывает её законно.
    done: dict = {}
    ex.registry_update(done, 1, pack, resp, patch_rejected=[],
                       executed={"A1"})
    assert done["findings"][0]["state"] == "исправлено"
    ex.registry_update(done, 2, {"round": 2, "mode": "полный",
                                 "remarks": []}, {"answers": []},
                       executed=set())
    assert done["findings"][0]["state"] == "закрыто"
    assert done["findings"][0]["closed_round"] == 2
    assert ex.registry_blockers(done) == []
    # Совместимость: executed=None — прежний закон (судит только
    # отбраковка патчей); этим путём ходит повторная проводка гейта.
    old_way: dict = {}
    ex.registry_update(old_way, 1, pack, resp)
    assert old_way["findings"][0]["state"] == "исправлено"


def test_normalize_registry_does_not_reopen_real_closures():
    """A07-F3, положительный контроль: миграция чтения трогает ТОЛЬКО
    старую техническую отбраковку замком кода. Закрытие по исполненному
    патчу и решения арбитра не переоткрываются; вход не мутируется."""
    reg = {"findings": [
        {"id": "C1", "key": "k1", "state": "закрыто",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 1, "closed_round": 2},
        {"id": "C2", "key": "k2", "state": "арбитр: обработчик прав",
         "severity": "БЛОКИРУЮЩЕЕ", "opened_round": 1,
         "closed_round": None},
        {"id": "C3", "key": "k3", "state": "отклонено кодом",
         "severity": "КРИТИЧЕСКОЕ", "opened_round": 2,
         "closed_round": None, "closed_by": None},
    ]}
    got = ex.normalize_registry(reg)
    assert got["findings"] == reg["findings"]
    assert [f["id"] for f in ex.registry_blockers(reg)] == ["C3"]
    assert ex.decide([{"weighty": (0, 0, 0), "mode": "полный"}] * 2, 9,
                     {"findings": reg["findings"][:2]}) == "успех"


def test_load_registry_judges_declared_version_before_use(tmp_path):
    """A07-F4: загрузчик судит ЗАЯВЛЕННУЮ версию и полноту её формы до
    всякого использования и перезаписи. Отсутствие файла (новый цикл),
    старая форма registry/1 и корректный пустой registry/2 законны;
    неизвестная версия и неполная новая форма — отказ словами."""
    p = tmp_path / "registry.json"
    assert ex.load_registry(p) == {}
    _dump(p, {"findings": []})
    assert ex.load_registry(p) == {"findings": []}
    _dump(p, {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    assert ex.load_registry(p)["findings"] == []
    _dump(p, {"schema_version": "registry/9", "findings": []})
    with pytest.raises(ex.ExchangeError, match="версия схемы"):
        ex.load_registry(p)
    _dump(p, {"schema_version": ex.REGISTRY_SCHEMA})
    with pytest.raises(ex.ExchangeError, match="неполная форма"):
        ex.load_registry(p)
    _dump(p, {"schema_version": ex.REGISTRY_SCHEMA, "findings": {}})
    with pytest.raises(ex.ExchangeError, match="findings"):
        ex.load_registry(p)
    _dump(p, {"schema_version": ex.REGISTRY_SCHEMA, "findings": ["строка"]})
    with pytest.raises(ex.ExchangeError, match="не объекты"):
        ex.load_registry(p)
    p.write_text("{не JSON", encoding="utf-8")
    with pytest.raises(ex.ExchangeError, match="JSON"):
        ex.load_registry(p)


def test_dump_registry_refuses_foreign_schema_and_writes_complete_form(
        tmp_path):
    """A07-F4: писатель не подменяет чужую объявленную версию своей —
    это и «чинило» неизвестную форму до проверки; форма registry/2
    пишется полной (пустой реестр объявляется явно)."""
    p = tmp_path / "registry.json"
    with pytest.raises(ex.ExchangeError, match="посторонняя версия"):
        ex._dump_registry(p, {"schema_version": "registry/9",
                              "findings": []})
    assert not p.exists()
    ex._dump_registry(p, {"cycle": 1})
    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["schema_version"] == ex.REGISTRY_SCHEMA
    assert got["findings"] == []
    assert ex.load_registry(p)["findings"] == []


def test_state_words_do_not_show_success_on_unreadable_registry(tmp_path):
    """A07-F4 у показа: негодная форма реестра — не повод показать
    записанный «успех». Судить нечем — слова той же семьи «не закрыто
    (по реестру)» (панель красит её предупреждением, новых слов
    состояния не вводится); корректный реестр — как прежде."""
    refused = ("аудит-цикл: не закрыто (по реестру): "
               "форма реестра не читается")
    out = tmp_path / "exchange"
    _dump(out / "registry.json", {"schema_version": "registry/9",
                                  "findings": []})
    assert webui._state_by_registry(tmp_path, "аудит-цикл: успех") \
        == refused
    _dump(out / "registry.json", {"schema_version": ex.REGISTRY_SCHEMA})
    assert webui._state_by_registry(
        tmp_path, "аудит остановлен: Б 0, К 0, С 1 — продолжить аудит?") \
        == refused
    # Состояния вне семьи «успех / остановлен» не пересуживаются.
    assert webui._state_by_registry(tmp_path, "аудит-цикл: лимит") \
        == "аудит-цикл: лимит"
    _dump(out / "registry.json", {"schema_version": ex.REGISTRY_SCHEMA,
                                  "findings": []})
    assert webui._state_by_registry(tmp_path, "аудит-цикл: успех") \
        == "аудит-цикл: успех"


# ── Третье неавтор-ревью дельты A07 (12.09.2026): остаток F2 (успех при
# незавершённой истории), семья слов сквозного цикла, отказ ручки и
# заявок каталога словами вместо 500 / молчаливого fail-open, форма
# «findings» при ЛЮБОЙ версии реестра. Пробы ревьюера — P7, P3, P4, P5;
# ожидания перенесены с инверсией дефекта и с контролями «зелёного».

@pytest.mark.parametrize("n", [0, 1])
def test_gate_red_on_success_with_unfinished_history(tmp_path, n):
    """Проба P7 ревьюера, инверсия: «успех» при ПУСТОМ реестре и
    незавершённой истории (правило останова говорит «продолжать»)
    прежде проходил гейт — сверка с decide шла только при
    expected != «продолжать». Терминальный успех доказывают два чистых
    полных аудита подряд; их нет — красный словами."""
    out = tmp_path / f"ex-{n}"
    _cycle_with_prior(out, n, {"schema_version": ex.REGISTRY_SCHEMA,
                               "findings": []}, verdict="успех")
    problems, _c = gate_exchange.check(out)
    assert any("незавершённой истории" in p and "успех" in p
               for p in problems), problems


@pytest.mark.parametrize("verdict", [ex.VERDICT_NOT_CLOSED, "лимит",
                                     "стагнация"])
@pytest.mark.parametrize("n", [0, 1])
def test_gate_green_on_honest_verdicts_with_unfinished_history(
        tmp_path, n, verdict):
    """Положительный контроль к P7: честные слова на той же короткой
    истории закрытым успехом цикл не объявляют — гейт зелёный."""
    out = tmp_path / f"ex-{n}-{verdict}"
    _cycle_with_prior(out, n, {"schema_version": ex.REGISTRY_SCHEMA,
                               "findings": []}, verdict=verdict)
    assert gate_exchange.check(out)[0] == []


def test_state_words_of_full_cycle_are_rejudged_by_registry(tmp_path):
    """Проба P3 ревьюера, инверсия: финал СКВОЗНОГО цикла «чисто по
    Б/К» — то же утверждение «живых Б/К-споров нет», и при
    унаследованной отбраковке замком кода он показывался зелёным.
    Теперь пересуживается наравне с «аудит-цикл: успех», словами той
    же семьи; чистый реестр слова не трогает."""
    _dump(tmp_path / "exchange" / "registry.json", {"findings": [
        {"id": "OLD-1", "key": "k", "severity": "КРИТИЧЕСКОЕ",
         "state": "отклонено кодом", "opened_round": 1, "closed_round": 1,
         "closed_by": "замок кода"}]})
    rejudged = "аудит-цикл: не закрыто (по реестру): 1 блокирующих"
    for words in ("сквозной цикл: чисто по Б/К (кругов: 1)",
                  "сквозной цикл: чисто по Б/К (кругов: 2); "
                  "серьёзных в бэклоге: 1"):
        assert webui._state_by_registry(tmp_path, words) == rejudged
    # Прочие состояния сквозного цикла не пересуживаются.
    assert webui._state_by_registry(
        tmp_path, "сквозной цикл: лимит кругов (2)") \
        == "сквозной цикл: лимит кругов (2)"
    # Положительный контроль: чистый реестр — слова как записаны.
    _dump(tmp_path / "exchange" / "registry.json",
          {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    assert webui._state_by_registry(
        tmp_path, "сквозной цикл: чисто по Б/К (кругов: 1)") \
        == "сквозной цикл: чисто по Б/К (кругов: 1)"
    # Негодная форма — тот же отказ словами, что у аудит-цикла.
    _dump(tmp_path / "exchange" / "registry.json",
          {"schema_version": "registry/9", "findings": []})
    assert webui._state_by_registry(
        tmp_path, "сквозной цикл: чисто по Б/К (кругов: 1)") \
        == ("аудит-цикл: не закрыто (по реестру): форма реестра не "
            "читается")


@pytest.mark.parametrize("body", [
    {"schema_version": "registry/9", "findings": []},
    {"schema_version": "registry/2"}],
    ids=["неизвестная-версия", "неполная-новая-форма"])
def test_exchange_handle_refuses_bad_registry_form_with_words(
        tmp_path, monkeypatch, body):
    """Проба P4 ревьюера, инверсия: ручка состояния цикла падала 500
    («Internal Server Error» вместо причины). Теперь — 200 и отказ
    СЛОВАМИ: счётчик состояний пуст, сводки блокеров нет, вердикт по
    реестру — «не закрыто (по реестру): форма реестра не читается»,
    сама причина рядом полем registry_error; записанный «успех»
    показан как записан, файлы не тронуты."""
    from fastapi.testclient import TestClient
    run = tmp_path / "run-0001"
    _dump(run / "exchange" / "registry.json", body)
    _dump(run / "exchange" / "verdict.json",
          {"verdict": "успех", "rounds": 2, "max_rounds": 9})
    _dump(run / "status.json", {"state": "аудит-цикл: успех"})
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run)
    client = TestClient(webui.app, raise_server_exceptions=False)
    r = client.get("/api/runs/run-0001/exchange")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["verdict_by_registry"] == ("не закрыто (по реестру): "
                                          "форма реестра не читается")
    assert got["registry"] == {} and got["registry_verdict"] is None
    assert "реестр находок" in got["registry_error"]
    assert got["verdict"]["verdict"] == "успех"
    assert json.loads((run / "exchange" / "registry.json")
                      .read_text(encoding="utf-8")) == body


def test_exchange_handle_keeps_words_on_readable_registry(tmp_path,
                                                          monkeypatch):
    """Положительный контроль к P4: читаемый реестр — прежний ответ,
    причины отказа нет."""
    from fastapi.testclient import TestClient
    run = tmp_path / "run-0001"
    _dump(run / "exchange" / "registry.json",
          {"schema_version": ex.REGISTRY_SCHEMA, "findings": [
              {"id": "F1", "key": "k", "severity": "КРИТИЧЕСКОЕ",
               "state": "патч отбракован", "opened_round": 1,
               "closed_round": None}]})
    _dump(run / "exchange" / "verdict.json",
          {"verdict": "успех", "rounds": 2, "max_rounds": 9})
    _dump(run / "status.json", {"state": "аудит-цикл: успех"})
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run)
    client = TestClient(webui.app, raise_server_exceptions=False)
    got = client.get("/api/runs/run-0001/exchange").json()
    assert got["verdict_by_registry"] == "не закрыто (по реестру)"
    assert got["registry_verdict"]["count"] == 1
    assert got["registry"] == {"патч отбракован": 1}
    assert got["registry_error"] is None


def _catalog_doc():
    """Свод с одной строкой посадки C — кандидат заявки в каталог."""
    return {"rules": [{"id": "R-C1", "title": "C1", "kind": "HARD",
                       "zone": "PAIRING", "statement": "x <= 1",
                       "template_fit": {"category": "C",
                                        "gap": "семьи нет"}}],
            "questions": []}


def test_catalog_rows_refuse_when_registry_form_is_unreadable(
        tmp_path, monkeypatch):
    """Проба P4 ревьюера, вторая половина: негодная форма реестра
    давала ПУСТОЙ перечень живых находок, то есть «споров посадки
    нет», — и оспоренная строка уезжала заявкой в каталог шаблонов
    (fail-open). Теперь строка не кандидат, причина названа тем же
    полем `reason`, которое панель уже показывает."""
    monkeypatch.setattr(webui.catreq_mod, "blocked_words", lambda: None)
    run = tmp_path / "run-0001"
    _dump(run / "exchange" / "registry.json",
          {"schema_version": "registry/9", "findings": []})
    rows = webui._catalog_rows(run, _catalog_doc())
    assert [r["rule_id"] for r in rows] == ["R-C1"]
    assert rows[0]["candidate"] is False
    assert webui.CATALOG_REGISTRY_REFUSAL in rows[0]["reason"]
    # Положительный контроль: читаемый реестр — кандидат как прежде.
    _dump(run / "exchange" / "registry.json",
          {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    rows = webui._catalog_rows(run, _catalog_doc())
    assert rows[0]["candidate"] is True and rows[0]["reason"] is None


def test_registry_form_judges_findings_at_any_version(tmp_path):
    """Проба P5 ревьюера, инверсия: старая форма registry/1 с
    «findings» НЕ списком проезжала проверку, разбиралась по буквам и
    роняла проводку AttributeError «'str' object has no attribute
    'get'» — отказ без причины вместо названного отказа формы.
    Обязательность самого поля остаётся законом только новой формы."""
    p = tmp_path / "registry.json"
    for body, words in (({"findings": {"a": 1}}, "не список"),
                        ({"findings": "abc"}, "не список"),
                        ({"findings": [1, "x"]}, "не объекты")):
        _dump(p, body)
        with pytest.raises(ex.ExchangeError, match=words):
            ex.load_registry(p)
    # Положительный контроль: законная старая форма читается как
    # прежде и проводится без падения; поля «findings» у неё может и
    # не быть вовсе.
    _dump(p, {"findings": [{"id": "F1", "key": "k", "state": "открыто",
                            "severity": "СЕРЬЁЗНОЕ", "opened_round": 1,
                            "closed_round": None}]})
    got = ex.load_registry(p)
    assert [f["id"] for f in got["findings"]] == ["F1"]
    ex.registry_update(got, 2, {"round": 2, "mode": "полный",
                                "remarks": []}, {"answers": []})
    _dump(p, {"cycle": 1})
    assert ex.load_registry(p) == {"cycle": 1}


# ---------------------------------------------------------------------------
# Четвёртое неавтор-ревью A07, находки 3 и 4: отказ формы реестра
# доходит до ПОТРЕБИТЕЛЕЙ строк-кандидатов теми же словами — и в
# причине автоподачи, и на кнопке строки с подтверждением.

def _run_with_bad_registry(tmp_path):
    run = tmp_path / "run-0001"
    _dump(run / "extract" / "model.json", _catalog_doc())
    _dump(run / "exchange" / "registry.json",
          {"schema_version": "registry/9", "findings": []})
    return run


def test_catalog_auto_reason_names_registry_refusal(tmp_path, monkeypatch):
    """Находка 4: когда КАЖДАЯ строка отказана негодной формой реестра,
    автоподача говорила «кандидатов нет» — то есть «работы нет», хотя
    работа есть, а судить спор посадки нечем. Теперь причина названа
    теми же словами, что и у строки."""
    monkeypatch.setattr(webui.catreq_mod, "blocked_words", lambda: None)
    monkeypatch.setattr(webui.catreq_mod, "auto_enabled", lambda: True)
    run = _run_with_bad_registry(tmp_path)
    rows = webui._catalog_rows(run, _catalog_doc())
    reason = webui._catalog_auto_reason(run, {"state": "новый"}, rows)
    assert reason and webui.CATALOG_REGISTRY_REFUSAL in reason, reason
    # Положительный контроль: читаемый реестр — прежние слова
    # («кандидатов нет» звучит, только когда кандидатов и правда нет).
    _dump(run / "exchange" / "registry.json",
          {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    rows_ok = webui._catalog_rows(run, _catalog_doc())
    assert rows_ok[0]["candidate"] is True
    assert webui._catalog_auto_reason(run, {"state": "новый"},
                                      rows_ok) is None


def test_catalog_one_mode_confirmation_does_not_bypass_registry_refusal(
        tmp_path, monkeypatch):
    """Находка 3: кнопка строки с подтверждением «это пробел каталога,
    не брак посадки» снимает спор ПОСАДКИ — а при негодной форме
    реестра спор судить нечем, и подтверждать нечего. Прежде
    `confirm_service_c` проезжал мимо отказа формы, и оспоренная строка
    уезжала заявкой в каталог (тот самый fail-open, ради которого
    ставился отказ)."""
    from fastapi import HTTPException
    monkeypatch.setattr(webui.catreq_mod, "blocked_words", lambda: None)
    run = _run_with_bad_registry(tmp_path)
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run)
    for confirm in (False, True):
        with pytest.raises(HTTPException) as got:
            webui.submit_catalog_requests(
                "run-0001", rule_ids=["R-C1"], mode="one",
                confirm_service_c=confirm)
        assert got.value.status_code == 409
        assert webui.CATALOG_REGISTRY_REFUSAL in str(got.value.detail), \
            got.value.detail


# ---------------------------------------------------------------------------
# A07-R2 / A07-R3 (вердикт VERDICT-A07-d9343ead, 12.09.2026): негодный
# и НЕДОСТУПНЫЙ реестр одинаково названы у ВСЕХ потребителей — списка
# прогонов, ручки состояния, кандидатов заявок, кнопки строки, цикла и
# обоих гейтов. «Не удалось узнать о спорах» — не «споров нет».
# ---------------------------------------------------------------------------

CLEAN_STATES = ("аудит-цикл: успех",
                "сквозной цикл: чисто по Б/К (кругов: 2)",
                "аудит остановлен: чисто по Б/К")

_LIVE_CRITICAL = {"schema_version": "registry/2",
                  "findings": [{"id": "F-R3", "key": "k", "state": "открыто",
                                "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
                                "closed_round": None}]}


def _fail_reading(monkeypatch, path, error_cls):
    """Точечная ошибка чтения ОДНОГО пути (не всей файловой системы)."""
    from pathlib import Path as _Path
    original = _Path.read_text

    def fail_one(self, *a, **kw):
        if self == path:
            raise error_cls("synthetic registry read unavailable")
        return original(self, *a, **kw)
    monkeypatch.setattr(_Path, "read_text", fail_one)


@pytest.mark.parametrize("state", CLEAN_STATES)
@pytest.mark.parametrize("error_cls", [PermissionError, OSError, TimeoutError])
def test_a07_r3_run_list_keeps_no_recorded_success_when_registry_is_gone(
        tmp_path, monkeypatch, state, error_cls):
    """Список прогонов при ошибке чтения реестра возвращал ЗАПИСАННОЕ
    состояние — прогон оставался зелёным «успехом» ровно тогда, когда
    судить его было нечем. Теперь слова той же семьи «не закрыто (по
    реестру)»: панель красит их предупреждением."""
    path = tmp_path / "exchange" / "registry.json"
    _dump(path, _LIVE_CRITICAL)
    _fail_reading(monkeypatch, path, error_cls)
    shown = webui._state_by_registry(tmp_path, state)
    assert shown != state
    assert "реестр недоступен" in shown, shown


@pytest.mark.parametrize("state", CLEAN_STATES)
def test_a07_r3_directory_instead_of_registry_is_not_a_new_run(tmp_path, state):
    """Каталог с именем registry.json — не «файла нет»: `is_file()`
    прежде равнял его с новым прогоном, и показ оставался чистым."""
    import shutil
    path = tmp_path / "exchange" / "registry.json"
    path.mkdir(parents=True)
    shown = webui._state_by_registry(tmp_path, state)
    assert "реестр недоступен" in shown, shown
    # A07-R5 (закон уточнён 12.09.2026, согласован с COSMOS, ведомость
    # CDPM-122): РЕАЛЬНОЕ отсутствие реестра при ЗАЯВЛЕННОМ исходе
    # цикла — не «новый прогон», а исчезнувший носитель: отказ другими
    # словами, но той же семьи. Прежде здесь показывалось записанное
    # состояние — исчезновение реестра работало доказательством
    # чистоты. Положительный контроль новизны — в тестах A07-R5 ниже
    # (состояние, исхода не заявлявшее).
    shutil.rmtree(path)
    shown = webui._state_by_registry(tmp_path, state)
    assert "реестр отсутствует после исполненного цикла" in shown, shown
    # И читаемый чистый реестр — как записано.
    _dump(path, {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    assert webui._state_by_registry(tmp_path, state) == state


def test_a07_r3_exchange_status_answers_200_with_named_unavailability(
        tmp_path, monkeypatch):
    """Ручка состояния цикла: недоступный реестр — названный отказ в
    теле (как негодная форма), а не 500 и не пустой счётчик,
    выданный за «находок нет»."""
    out = tmp_path / "exchange"
    _dump(out / "verdict.json", {"verdict": "успех", "rounds": 0,
                                 "max_rounds": 9})
    (out / "registry.json").mkdir(parents=True)
    monkeypatch.setattr(webui, "_run_dir", lambda rid: tmp_path)
    got = webui.exchange_status("run-0001")
    assert got["registry_error"] and "недоступен" in got["registry_error"]
    assert got["registry_verdict"] is None
    assert got["verdict_by_registry"] \
        == "не закрыто (по реестру): реестр недоступен"
    assert got["registry"] == {}


@pytest.mark.parametrize("error_cls", [PermissionError, OSError])
def test_a07_r3_catalog_row_button_and_auto_refuse_unavailable_registry(
        tmp_path, monkeypatch, error_cls):
    """Спор посадки судится живыми находками реестра. Ошибка чтения
    давала ПУСТОЙ перечень находок — «споров нет», и оспоренная строка
    становилась кандидатом заявки в каталог шаблонов (fail-open).
    Отказ обязан быть один у строки, у автоподачи и у кнопки."""
    from fastapi import HTTPException
    monkeypatch.setattr(webui.catreq_mod, "blocked_words", lambda: None)
    monkeypatch.setattr(webui.catreq_mod, "auto_enabled", lambda: True)
    run = tmp_path / "run-0001"
    _dump(run / "extract" / "model.json", _catalog_doc())
    path = run / "exchange" / "registry.json"
    _dump(path, {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    # Контроль ДО отказа: реестр чист, строка — кандидат.
    assert webui._catalog_rows(run, _catalog_doc())[0]["candidate"] is True
    _fail_reading(monkeypatch, path, error_cls)
    rows = webui._catalog_rows(run, _catalog_doc())
    assert [r["rule_id"] for r in rows] == ["R-C1"]
    assert rows[0]["candidate"] is False
    assert webui.CATALOG_REGISTRY_UNAVAILABLE in rows[0]["reason"]
    reason = webui._catalog_auto_reason(run, {"state": "новый"}, rows)
    assert reason and webui.CATALOG_REGISTRY_UNAVAILABLE in reason, reason
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run)
    for confirm in (False, True):
        with pytest.raises(HTTPException) as got:
            webui.submit_catalog_requests(
                "run-0001", rule_ids=["R-C1"], mode="one",
                confirm_service_c=confirm)
        assert got.value.status_code == 409
        assert webui.CATALOG_REGISTRY_UNAVAILABLE in str(got.value.detail)


@pytest.mark.parametrize("bad", [
    {"schema_version": None, "findings": []},
    {"schema_version": "registry/2",
     "findings": [{"id": "F-X", "key": "k", "state": "foreign-state",
                   "severity": "КРИТИЧЕСКОЕ", "opened_round": 1,
                   "closed_round": None}]},
    {"schema_version": "registry/2",
     "findings": [{"id": "F-X", "key": "k", "state": "открыто",
                   "severity": "foreign-severity", "opened_round": 1,
                   "closed_round": None}]}],
    ids=["null-version", "foreign-state", "foreign-severity"])
@pytest.mark.parametrize("prior_file", ["registry.json", "registry-start.json"])
def test_a07_r2_one_named_refusal_for_cycle_panel_and_both_gates(
        tmp_path, monkeypatch, bad, prior_file):
    """Один закон формы — один отказ у всех читателей. Проверяются оба
    носителя (рабочий реестр и снимок старта), загрузчик цикла, оба
    гейта и показ панели; байты входа при отказе не переписываются."""
    from cdpm.gates import gate_arbiter
    out = tmp_path / "exchange"
    path = out / prior_file
    _dump(path, bad)
    before = path.read_bytes()
    # Цикл читает носитель ТЕМ ЖЕ загрузчиком: отказ до перезаписи.
    with pytest.raises(ex.ExchangeError):
        ex.load_registry(path)
    assert path.read_bytes() == before
    # Гейт цикла: красный СЛОВАМИ, не трассировкой.
    _dump(out / "verdict.json", {"verdict": "успех", "rounds": 0,
                                 "max_rounds": 9})
    problems, _c = gate_exchange.check(out)
    assert any("реестр находок" in p for p in problems), problems
    if prior_file == "registry.json":
        # Гейт арбитража: тот же загрузчик, те же слова.
        _dump(out / "arbiter.json", {"decisions": []})
        problems, _c = gate_arbiter.check(out)
        assert any("реестр находок" in p for p in problems), problems
        # Показ панели: записанный успех не показывается.
        monkeypatch.setattr(webui, "_run_dir", lambda rid: tmp_path)
        shown = webui._state_by_registry(tmp_path, "аудит-цикл: успех")
        assert shown != "аудит-цикл: успех", shown
    assert path.read_bytes() == before


# ── A07-R5 (12.09.2026): РЕЕСТР ПОСЛЕ ИСПОЛНЕННОГО ЦИКЛА ОБЯЗАН
# СУЩЕСТВОВАТЬ. Закон согласован с COSMOS и записан в ведомость
# CDPM-122; аудитор GPT-116 принял формулировку: «отсутствие после
# заявленного исхода учитывается как отказ; один outcome_claimed для
# всех потребителей; положительный контроль действительно нового
# прогона сохраняется».
#
# ДЕФЕКТ, КОТОРЫЙ ЗАКРЫВАЕТСЯ. Реестр пишет КАЖДЫЙ круг цикла, и
# арбитраж переписывает его в конце. Значит «состояние заявило исход, а
# файла нет» — не новый прогон и не «споров нет»: реестр ИСЧЕЗ. Прежде
# такой прогон читался пустым реестром, и исчезновение носителя
# работало доказательством чистоты: панель показывала записанный
# «успех», ручка цикла — пустой счётчик состояний, каталог — оспоренную
# строку кандидатом заявки, оба гейта — зелёное.

#: Слова состояний машины прогона, ЗАЯВЛЯЮЩИЕ исход цикла обмена.
_R5_CLAIM = (
    "аудит-цикл: успех",
    "аудит-цикл: не закрыто: 2 блокирующих",
    "аудит-цикл: лимит",
    "аудит-цикл: стагнация",
    "аудит-цикл: остановлен по Б/К",
    "аудит-цикл: не закрыто (по реестру): 1 блокирующих",
    "аудит остановлен: Б 0, К 0, С 1 — продолжить аудит?",
    "аудит остановлен: чисто по Б/К",
    "сквозной цикл: чисто по Б/К (кругов: 2)",
    "сквозной цикл: лимит кругов 3",
    "арбитраж готов",
)
#: Слова того же автомата, исхода цикла НЕ заявляющие: стадии до цикла,
#: идущие стадии, прерывание человеком, падение стадии, отказ ГЕЙТА
#: (исход проверки, а не цикла) и побочные линии данных и SMT.
_R5_NO_CLAIM = (
    "новый", "отказ заморозки", "заморожено", "красный гейт заморозки",
    "извлечение идёт", "извлечено", "красный гейт извлечения",
    "остановлено: извлечение",
    "применение ответов идёт", "ответы применены", "красный гейт ответов",
    "остановлено: применение ответов",
    "сверка формализации идёт", "формализация сверена",
    "красный гейт сверки формализации", "остановлено: сверка формализации",
    "аудит-цикл идёт", "остановлено: аудит-цикл",
    "красный гейт аудит-цикла", "отказ аудит-цикла: синтетика",
    "сквозной цикл идёт", "остановлено: сквозной цикл",
    "арбитраж идёт", "остановлено: арбитраж",
    "красный гейт арбитража", "отказ арбитража: синтетика",
    "данные заморожены", "проверки идут", "отчёт готов",
    "сверка идёт", "спорное сверено", "остановлено: проверки данных",
    "SMT-проверка идёт", "SMT-проверка готова", "отказ SMT-проверки",
)


def test_a07_r5_outcome_claimed_is_the_single_source_of_the_law():
    """(d) Таблица предиката СЛОВА по КАЖДОМУ состоянию автомата
    прогона. `outcome_claimed` — ПОЛОВИНА закона A07-R5 (вторая —
    `cycle_ran` по каталогу обмена); перечень слов в продукте один, и
    второго нет. Потребители зовут не его, а весь предикат
    `registry_expected(exdir, state)` — см. тест ниже."""
    for state in _R5_CLAIM:
        assert ex.outcome_claimed(state) is True, state
    for state in _R5_NO_CLAIM:
        assert ex.outcome_claimed(state) is False, state
    # Не-строка исходом не заявляется (статуса может не быть).
    for junk in (None, 5, [], {}, ""):
        assert ex.outcome_claimed(junk) is False, junk
    # Двоеточие — часть закона: «идёт» и «: вердикт» не путаются.
    assert ex.outcome_claimed("аудит-цикл идёт") is False
    assert ex.outcome_claimed("сквозной цикл идёт") is False


def test_a07_r5_loader_refuses_missing_file_after_claimed_outcome(tmp_path):
    """(a) Ядро закона у загрузчика: тот же путь, те же байты на
    диске — исход заявлен, значит носитель обязан быть."""
    path = tmp_path / "exchange" / "registry.json"
    path.parent.mkdir(parents=True)
    # Без требования — прежнее поведение (совместимость подписи).
    assert ex.load_registry(path) == {}
    assert ex.load_registry(path, expect_file=False) == {}
    with pytest.raises(ex.RegistryMissing) as got:
        ex.load_registry(path, expect_file=True)
    assert "реестр отсутствует после исполненного цикла" in str(got.value)
    assert str(path) in str(got.value)
    # Подкласс недоступности: каждый читатель, уже отказывающий на
    # RegistryUnavailable, отказывает и здесь — новых веток не нужно.
    assert isinstance(got.value, ex.RegistryUnavailable)
    assert isinstance(got.value, ex.ExchangeError)
    # Чтение ничего не создаёт и не чинит.
    assert not path.exists()


def test_a07_r5_refusal_is_idempotent(tmp_path):
    """(e) Два чтения подряд — тот же отказ теми же словами: отказ не
    «залипает» и не подменяется вторым исходом."""
    path = tmp_path / "exchange" / "registry.json"
    path.parent.mkdir(parents=True)
    words = []
    for _ in range(2):
        with pytest.raises(ex.RegistryMissing) as got:
            ex.load_registry(path, expect_file=True)
        words.append(str(got.value))
    assert words[0] == words[1]
    assert not path.exists()
    shown = [webui._state_by_registry(tmp_path, "аудит-цикл: успех")
             for _ in range(2)]
    assert shown[0] == shown[1]
    assert "реестр отсутствует после исполненного цикла" in shown[0]


@pytest.mark.parametrize("expect_file", [False, True])
@pytest.mark.parametrize("error_cls", [PermissionError, OSError, TimeoutError])
def test_a07_r5_unavailable_stays_unavailable(tmp_path, monkeypatch,
                                              expect_file, error_cls):
    """(c) Каталог вместо файла и отказ доступа — ВСЕГДА «временно
    недоступен» (A07-R3), при любом значении `expect_file`: закон R4
    судит ОТСУТСТВИЕ носителя, а не сбой чтения, и одно не подменяет
    другое в словах отказа."""
    as_dir = tmp_path / "registry.json"
    as_dir.mkdir()
    with pytest.raises(ex.RegistryUnavailable) as got:
        ex.load_registry(as_dir, expect_file=expect_file)
    assert "временно недоступен" in str(got.value)
    assert not isinstance(got.value, ex.RegistryMissing)
    good = tmp_path / "good.json"
    _dump(good, {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    _fail_reading(monkeypatch, good, error_cls)
    with pytest.raises(ex.RegistryUnavailable) as got2:
        ex.load_registry(good, expect_file=expect_file)
    assert "временно недоступен" in str(got2.value)
    assert not isinstance(got2.value, ex.RegistryMissing)


@pytest.mark.parametrize("state", CLEAN_STATES)
def test_a07_r5_panel_does_not_show_recorded_success_without_registry(
        tmp_path, state):
    """(a) Панель и список прогонов: заявленный исход без реестра —
    отказ семьи «не закрыто (по реестру)», которую панель уже красит
    предупреждением (api.ts::isStalledState); новых слов состояния не
    вводится и сборка UI не нужна."""
    (tmp_path / "exchange").mkdir(parents=True)
    shown = webui._state_by_registry(tmp_path, state)
    assert shown != state
    assert shown.startswith("аудит-цикл: не закрыто (по реестру): "), shown
    assert "реестр отсутствует после исполненного цикла" in shown
    # Положительный контроль тем же прогоном: реестр вернулся и чист —
    # состояние показывается как записано.
    _dump(tmp_path / "exchange" / "registry.json",
          {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    assert webui._state_by_registry(tmp_path, state) == state


def test_a07_r5_exchange_status_answers_200_with_named_missing_registry(
        tmp_path, monkeypatch):
    """(a) Ручка состояния цикла: исчезнувший реестр — названный отказ
    200-м ответом (как негодная форма и недоступность), а не пустой
    счётчик состояний, выданный за «находок нет»."""
    out = tmp_path / "exchange"
    _dump(out / "verdict.json", {"verdict": "успех", "rounds": 0,
                                 "max_rounds": 9})
    _dump(tmp_path / "status.json", {"state": "аудит-цикл: успех"})
    monkeypatch.setattr(webui, "_run_dir", lambda rid: tmp_path)
    got = webui.exchange_status("run-0001")
    assert got["registry_error"]
    assert "реестр отсутствует после исполненного цикла" \
        in got["registry_error"]
    assert got["registry_verdict"] is None
    assert got["verdict_by_registry"] == (
        "не закрыто (по реестру): реестр отсутствует после исполненного "
        "цикла")
    assert got["registry"] == {}
    # Половина закона по КАТАЛОГУ: слово состояния исхода не заявляет,
    # а вердикт цикла на диске лежит — цикл отработал, и реестр обязан
    # быть. Так выглядит прогон раннера H0, пересуженный ПОСЛЕ стадии
    # ответов (проверка COSMOS на трёх золотых прогонах 12.09.2026).
    for state in ("ответы применены", "аудит-цикл идёт"):
        _dump(tmp_path / "status.json", {"state": state})
        got = webui.exchange_status("run-0001")
        assert got["registry_error"], state
        assert "реестр отсутствует после исполненного цикла" \
            in got["registry_error"], state
        assert got["registry"] == {}, state
    # Положительный контроль: ни слова об исходе, ни вердикта цикла в
    # каталоге — цикла не было, пустой реестр законен, отказа нет.
    (out / "verdict.json").unlink()
    got = webui.exchange_status("run-0001")
    assert got["registry_error"] is None
    assert got["registry"] == {}


def test_a07_r5_catalog_row_button_and_auto_refuse_missing_registry(
        tmp_path, monkeypatch):
    """(a) Спор посадки судится живыми находками реестра. Исчезнувший
    реестр давал ПУСТОЙ перечень находок — «споров посадки нет», и
    оспоренная строка уезжала заявкой в каталог шаблонов (fail-open).
    Отказ один: у строки, у автоподачи и у кнопки."""
    from fastapi import HTTPException
    monkeypatch.setattr(webui.catreq_mod, "blocked_words", lambda: None)
    monkeypatch.setattr(webui.catreq_mod, "auto_enabled", lambda: True)
    run = tmp_path / "run-0001"
    _dump(run / "extract" / "model.json", _catalog_doc())
    st = {"state": "аудит-цикл: успех"}
    _dump(run / "status.json", st)
    rows = webui._catalog_rows(run, _catalog_doc())
    assert [r["rule_id"] for r in rows] == ["R-C1"]
    assert rows[0]["candidate"] is False
    assert webui.CATALOG_REGISTRY_MISSING in rows[0]["reason"]
    assert webui._is_registry_refusal(rows[0]["reason"]) is True
    reason = webui._catalog_auto_reason(run, st, rows)
    assert reason and webui.CATALOG_REGISTRY_MISSING in reason, reason
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run)
    for confirm in (False, True):
        with pytest.raises(HTTPException) as got:
            webui.submit_catalog_requests(
                "run-0001", rule_ids=["R-C1"], mode="one",
                confirm_service_c=confirm)
        assert got.value.status_code == 409
        assert webui.CATALOG_REGISTRY_MISSING in str(got.value.detail)


def test_a07_r5_positive_control_a_really_new_run(tmp_path):
    """(b) ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ. Прогон, чьё состояние исхода цикла
    НЕ заявляло, реестра законно не имеет: читатели получают пустой
    реестр, строка остаётся кандидатом заявки, панель показывает
    состояние старта. `outcome_claimed(...) == False` новизны НЕ
    доказывает — он лишь снимает требование носителя; новизну
    доказывают отсутствие вердикта цикла и снимок старта."""
    run = tmp_path / "run-0001"
    _dump(run / "extract" / "model.json", _catalog_doc())
    for state in ("новый", "извлечено", "аудит-цикл идёт",
                  "сквозной цикл идёт"):
        _dump(run / "status.json", {"state": state})
        assert webui._read_registry(run) == {}, state
        assert webui._live_findings(run) == [], state
        rows = webui._catalog_rows(run, _catalog_doc())
        assert rows[0]["candidate"] is True, state
        assert webui._state_by_registry(run, state) == state, state
    # Снимок старта — носитель НОВИЗНЫ, требования файла не несёт.
    assert ex.load_registry(run / "exchange" / "registry-start.json") == {}


def test_a07_r5_gate_and_arbiter_refuse_a_vanished_registry(tmp_path):
    """(a) Оба гейта и вход арбитража: исход заявлен самим каталогом
    (вердикт цикла и отчёт арбитража на диске), реестра нет — красное
    СЛОВАМИ. Прежде исчезнувший реестр читался пустым: находок 0,
    блокеров нет, «успех» сходился сам с собой, а все тупики
    «сходились» с пустым перечнем находок."""
    from cdpm.gates import gate_arbiter
    out = tmp_path / "exchange"
    _dump(out / "verdict.json", {"verdict": "успех", "rounds": 0,
                                 "max_rounds": 9, "deadlocks": []})
    problems, counters = gate_exchange.check(out)
    assert any("реестр отсутствует после исполненного цикла" in p
               for p in problems), problems
    assert counters["findings"] == 0
    _dump(out / "arbiter.json", {"verdicts": [], "enforced": 0,
                                 "added_questions": []})
    problems, _c = gate_arbiter.check(out)
    assert any("реестр отсутствует после исполненного цикла" in p
               for p in problems), problems
    # Вход арбитража: отказ словами, а не пустой реестр («тупиков нет»).
    with pytest.raises(ex.RegistryMissing) as got:
        ex.run_arbiter(tmp_path / "kit", out, None, None)
    assert "реестр отсутствует после исполненного цикла" in str(got.value)
    # Положительный контроль: реестр на месте — тех слов гейт цикла
    # больше не говорит.
    _dump(out / "registry.json", {"schema_version": ex.REGISTRY_SCHEMA,
                                  "findings": []})
    problems, _c = gate_exchange.check(out)
    assert not any("реестр отсутствует" in p for p in problems), problems


# ── A07-R5, ВТОРАЯ ПОЛОВИНА ЗАКОНА (проверка COSMOS на трёх золотых
# прогонах, 12.09.2026). Раннер H0 пересуживает цикл ПОСЛЕ стадии
# ответов: цикл отработал раньше и записал exchange/registry.json,
# затем применились ответы — и состояние прогона стало «ответы
# применены». По слову состояния требования носителя нет, и
# исчезнувший реестр читался бы пустым («чисто») — ложный успех. Но
# «ответы применены» бывает и ДО всякого цикла (в панели ответы
# применяются раньше обмена), поэтому слово одно решать не вправе.
# Свидетельство берётся с ДИСКА: вердикт цикла `exchange/verdict.json`
# пишет конец КАЖДОГО цикла, реестр — каждый его круг. Весь закон —
# ОДИН предикат `registry_expected(exdir, state) = outcome_claimed
# (state) or cycle_ran(exdir)`.

#: Состояния, при которых прогон стоит ПОСЛЕ исполненного цикла, но
#: собственного вердикта цикла не называет (сердце находки COSMOS).
_R5_AFTER_CYCLE_WORDS = ("ответы применены", "формализация сверена",
                         "извлечено", "аудит-цикл идёт")


def test_a07_r5_registry_expected_is_the_single_predicate_of_the_law(
        tmp_path):
    """(d) ТАБЛИЦА всего закона: состояния × наличие вердикта цикла в
    каталоге обмена. Достаточно ЛЮБОЙ половины, и ни одной половины не
    хватает в одиночку."""
    exdir = tmp_path / "exchange"
    exdir.mkdir(parents=True)
    # Каталог пуст: судит только слово.
    assert ex.cycle_ran(exdir) is False
    for state in _R5_CLAIM:
        assert ex.registry_expected(exdir, state) is True, state
    for state in _R5_NO_CLAIM:
        assert ex.registry_expected(exdir, state) is False, state
    # Вердикт цикла на диске: цикл ИСПОЛНЕН — реестр обязан быть при
    # ЛЮБОМ слове, включая «ответы применены» золотых прогонов.
    _dump(exdir / "verdict.json", {"verdict": "успех", "rounds": 1,
                                   "max_rounds": 9})
    assert ex.cycle_ran(exdir) is True
    for state in _R5_CLAIM + _R5_NO_CLAIM:
        assert ex.registry_expected(exdir, state) is True, state
    # Состояния может не быть вовсе (его ведёт панель, а продолжение
    # цикла работает одним каталогом) — предикат вырождается в
    # предметную половину, второго «is_file()» в продукте нет.
    assert ex.registry_expected(exdir) is True
    assert ex.registry_expected(tmp_path / "no-such-dir") is False
    for junk in (None, 5, [], {}, ""):
        assert ex.registry_expected(tmp_path / "no-such-dir", junk) \
            is False, junk
    # Закон — ровно дизъюнкция двух названных половин, а не третий
    # перечень правил.
    for exd in (exdir, tmp_path / "no-such-dir"):
        for state in _R5_CLAIM + _R5_NO_CLAIM + ("", None):
            assert ex.registry_expected(exd, state) == (
                ex.outcome_claimed(state) or ex.cycle_ran(exd)), (exd, state)


def test_a07_r5_cycle_ran_is_fail_closed_when_the_disk_does_not_answer(
        tmp_path, monkeypatch):
    """(4) Каталог судится fail-closed, как и чтение реестра (A07-R3):
    ошибку ввода-вывода молча не глотаем. Пути НЕТ — цикла не было
    (единственная дорога к «реестра законно нет»); путь ЗАНЯТ, в том
    числе КАТАЛОГОМ с именем verdict.json, — цикл считается
    исполненным; узнать не удалось (отказ доступа, сбой) — тоже
    исполненным: «не удалось узнать, был ли цикл» — не «цикла не
    было»."""
    exdir = tmp_path / "exchange"
    exdir.mkdir(parents=True)
    assert ex.cycle_ran(exdir) is False
    # Каталог вместо файла: `is_file()` сказал бы «цикла не было» и
    # снял требование носителя — ровно тот fail-open, что закрывает
    # A07-R3 у чтения реестра.
    (exdir / "verdict.json").mkdir()
    assert ex.cycle_ran(exdir) is True
    assert ex.registry_expected(exdir, "ответы применены") is True
    # Отказ доступа и сбой ввода-вывода при попытке узнать о пути.
    (exdir / "verdict.json").rmdir()
    _dump(exdir / "verdict.json", {"verdict": "успех"})
    from pathlib import Path as _Path
    original = _Path.stat
    for error_cls in (PermissionError, OSError, TimeoutError):
        def fail_one(self, *a, _err=error_cls, **kw):
            if self == exdir / "verdict.json":
                raise _err("synthetic verdict stat unavailable")
            return original(self, *a, **kw)
        monkeypatch.setattr(_Path, "stat", fail_one)
        assert ex.cycle_ran(exdir) is True, error_cls
        assert ex.registry_expected(exdir, "извлечено") is True, error_cls
        monkeypatch.undo()
    # Чтение ничего не создаёт и не чинит.
    assert sorted(p.name for p in exdir.iterdir()) == ["verdict.json"]


def _golden_shaped_run(tmp_path, state="ответы применены"):
    """Форма золотого прогона H0: цикл ОТРАБОТАЛ (вердикт на диске),
    затем применились ответы — и слово состояния об исходе молчит.
    Реестра нет: ровно тот случай, что COSMOS проверил на трёх золотых
    прогонах 12.09.2026."""
    run = tmp_path / "run-0001"
    _dump(run / "extract" / "model.json", _catalog_doc())
    _dump(run / "exchange" / "verdict.json",
          {"verdict": "успех", "rounds": 2, "max_rounds": 9})
    _dump(run / "status.json", {"state": state})
    assert not (run / "exchange" / "registry.json").exists()
    return run


@pytest.mark.parametrize("state", _R5_AFTER_CYCLE_WORDS)
def test_a07_r5_vanished_registry_is_named_after_the_answers_stage(
        tmp_path, monkeypatch, state):
    """(1) ЗОЛОТАЯ ФОРМА. Вердикт цикла на диске, состояние — «ответы
    применены» (и родня), реестр исчез. Прежде такой прогон читался
    пустым реестром: живых находок нет → «чисто» → ложный успех.
    Теперь отказ назван у каждого потребителя теми же словами."""
    from fastapi import HTTPException
    monkeypatch.setattr(webui.catreq_mod, "blocked_words", lambda: None)
    monkeypatch.setattr(webui.catreq_mod, "auto_enabled", lambda: True)
    run = _golden_shaped_run(tmp_path, state)
    # Строка закона у загрузчика — ровно та, что зовёт раннер H0 на
    # стороне COSMOS (одна строка, `reg_path.parent` — каталог обмена).
    reg_path = run / "exchange" / "registry.json"
    with pytest.raises(ex.RegistryMissing) as got:
        ex.load_registry(reg_path, expect_file=ex.registry_expected(
            reg_path.parent, state))
    assert "реестр отсутствует после исполненного цикла" in str(got.value)
    # Читатели панели.
    with pytest.raises(ex.RegistryMissing):
        webui._read_registry(run)
    with pytest.raises(ex.RegistryMissing):
        webui._live_findings(run)
    # Ручка состояния цикла: 200 с названной причиной.
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run)
    answer = webui.exchange_status("run-0001")
    assert "реестр отсутствует после исполненного цикла" \
        in (answer["registry_error"] or "")
    assert answer["registry"] == {}
    assert answer["registry_verdict"] is None
    # Кандидаты заявки, автоподача и кнопка строки.
    rows = webui._catalog_rows(run, _catalog_doc())
    assert [r["rule_id"] for r in rows] == ["R-C1"]
    assert rows[0]["candidate"] is False
    assert webui.CATALOG_REGISTRY_MISSING in rows[0]["reason"]
    assert webui._is_registry_refusal(rows[0]["reason"]) is True
    reason = webui._catalog_auto_reason(run, {"state": state}, rows)
    assert reason and webui.CATALOG_REGISTRY_MISSING in reason, reason
    with pytest.raises(HTTPException) as bad:
        webui.submit_catalog_requests("run-0001", rule_ids=["R-C1"],
                                      mode="one", confirm_service_c=False)
    assert bad.value.status_code == 409
    assert webui.CATALOG_REGISTRY_MISSING in str(bad.value.detail)
    # Панель списка прогонов пересуживает ТОЛЬКО состояния, заявившие
    # чистый исход; «ответы применены» успеха не объявляет, ложным
    # зелёным не показывается и остаётся как записано (охрана
    # `_state_by_registry` этим законом не расширяется).
    assert webui._state_by_registry(run, state) == state
    assert "реестр отсутствует после исполненного цикла" in \
        webui._state_by_registry(run, "аудит-цикл: успех")
    # Положительный контроль ТЕМ ЖЕ прогоном: реестр вернулся и чист —
    # отказов нет, строка снова кандидат.
    _dump(reg_path, {"schema_version": ex.REGISTRY_SCHEMA, "findings": []})
    assert webui._read_registry(run) == {"schema_version": ex.REGISTRY_SCHEMA,
                                         "findings": []}
    assert webui.exchange_status("run-0001")["registry_error"] is None
    assert webui._catalog_rows(run, _catalog_doc())[0]["candidate"] is True


@pytest.mark.parametrize("state", _R5_AFTER_CYCLE_WORDS + ("новый",))
def test_a07_r5_positive_control_no_cycle_no_requirement(tmp_path, state):
    """(2) ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ второй половины: те же слова
    состояния, но цикла в каталоге НЕ было (вердикта нет) — реестра
    законно нет. Иначе закон запретил бы панели применять ответы до
    обмена: «ответы применены» бывает и ДО всякого цикла."""
    run = tmp_path / "run-0001"
    _dump(run / "extract" / "model.json", _catalog_doc())
    _dump(run / "status.json", {"state": state})
    (run / "exchange").mkdir(parents=True)
    assert ex.registry_expected(run / "exchange", state) is False
    assert webui._read_registry(run) == {}
    assert webui._live_findings(run) == []
    assert webui._catalog_rows(run, _catalog_doc())[0]["candidate"] is True
    assert webui._state_by_registry(run, state) == state
    # Снимок старта — носитель НОВИЗНЫ, требования файла не несёт.
    assert ex.load_registry(run / "exchange" / "registry-start.json") == {}


def test_a07_r5_catalog_half_refusal_is_idempotent(tmp_path, monkeypatch):
    """(5) Два чтения подряд — тот же отказ теми же словами: предикат
    ничего не создаёт и не «залипает», отказ не подменяется вторым
    исходом."""
    run = _golden_shaped_run(tmp_path)
    reg_path = run / "exchange" / "registry.json"
    monkeypatch.setattr(webui, "_run_dir", lambda _rid: run)
    words, shown, answers = [], [], []
    for _ in range(2):
        assert ex.registry_expected(run / "exchange", "ответы применены") \
            is True
        with pytest.raises(ex.RegistryMissing) as got:
            webui._read_registry(run)
        words.append(str(got.value))
        shown.append(webui._catalog_rows(run, _catalog_doc())[0]["reason"])
        answers.append(webui.exchange_status("run-0001")["registry_error"])
    assert words[0] == words[1]
    assert shown[0] == shown[1]
    assert answers[0] == answers[1]
    assert not reg_path.exists()


def test_a07_r5_stopped_by_human_is_its_own_family_of_states():
    """Просьба COSMOS 12.09 15:2x: раннер H0 различает «остановлен
    человеком» предикатом машины состояний, а не литералом панели.
    Семейство «остановлено: <стадия>» — прерывание человеком: не
    заявляет исход цикла и не отказ стадии; «аудит остановлен: …»
    (остановка по правилу) и «отказ …» (падение) — не оно."""
    human = ["остановлено: аудит-цикл", "остановлено: сквозной цикл",
             "остановлено: извлечение", "остановлено: применение ответов",
             "  остановлено: арбитраж  "]
    for st in human:
        assert ex.stopped_by_human(st), st
        assert not ex.outcome_claimed(st), st
    for st in ["аудит остановлен: по Б/К", "аудит-цикл: успех",
               "сквозной цикл: чисто по Б/К", "отказ извлечения",
               "ответы применены", "новый", "", None, 5]:
        assert not ex.stopped_by_human(st), st

