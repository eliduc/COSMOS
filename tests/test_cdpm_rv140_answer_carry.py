# -*- coding: utf-8 -*-
"""РВ-140 п. 1 (слово владельца 04.10.2026 05:35): повторное
«Извлечь правила» после ответов анкеты не теряет ответ авиакомпании.
Ответ переносится по месту в документе (id вопроса, отпечаток издания,
живые строки) тем же `answers.apply_answers`; вопрос изменился или
исчез — задан заново, причина словами; молча — ни один (Stage run-0025
03.10, Prod run-9060 04.10)."""
import copy
import hashlib
import json
import time

from cdpm import anketa_xlsx
from cdpm import answer_carry as ac
from cdpm import model as m
from test_cdpm_answers_audit import _XL_MIME, _fill
from test_cdpm_webui import _extract_and_wait, _upload, client  # noqa: F401
import pytest


@pytest.fixture(autouse=True)
def _s06r2_declared_layout(declared_test_layout):
    """Облачные стадии панели требуют объявленной раскладки ролей —
    тестовая раскладка conftest, молчаливого облака нет (S06-R2)."""


def _q(qid, text="Считать ли?", options=("а", "б"), rules=("R-1",)):
    return {"id": qid, "part": "2", "title": "Вопрос", "text": text,
            "answer_format": {"type": "choice", "options": [
                {"key": k, "text": f"вариант {k}"} for k in options]},
            "rule_refs": list(rules),
            "source_refs": [{"sid": "S1", "anchor": "абз.1"}]}


def _doc(*qs):
    return {"meta": {"customer": "Т", "mode": 1, "revision": 1},
            "rules": [{"id": "R-1"}, {"id": "R-2"}],
            "questions": [copy.deepcopy(q) for q in qs]}


BOOK = b"airline book rev-002"
BOOK_SHA = hashlib.sha256(BOOK).hexdigest()


def _answered(q, raw="а"):
    """Применённый ответ книгой ревизии 2 — с квитанцией книги, как его
    кладёт панель (`answers.apply_answers(..., book_sha256=…)`)."""
    from cdpm.provenance import answer_version_fingerprint
    q = copy.deepcopy(q)
    q["answer"] = {"raw": raw, "key": raw, "status": "применён", "revision": 2,
                   "answer_version": {
                       "fingerprint": answer_version_fingerprint(raw),
                       "received_at": "2026-10-04T09:00:00+02:00",
                       "book_sha256": BOOK_SHA}}
    return q


def _book(run_dir, body=BOOK):
    path = run_dir / "answers" / "rev-002" / "anketa-otvety.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_snapshot_skips_open_and_code_closed():
    a = _answered(_q("Q-2-a"))
    closed = _answered(_q("Q-2-c"))
    closed["answer"]["closed_by"] = "code"
    snap = ac.snapshot(_doc(a, _q("Q-2-open"), closed))
    assert [i["id"] for i in snap] == ["Q-2-a"]
    assert snap[0]["fingerprint"] == anketa_xlsx.question_fingerprint(a)
    assert snap[0]["source_refs"] == [{"sid": "S1", "anchor": "абз.1"}]


def test_judge_reasks_changed_missing_and_freeform():
    items = ac.snapshot(_doc(_answered(_q("Q-2-x"))))
    # вопрос исчез
    assert "нет в новом своде" in ac._judge(_doc(), items[0],
                                            data_line_done=False)
    # вопрос изменился (другой текст — другой отпечаток)
    assert "отпечаток" in ac._judge(_doc(_q("Q-2-x", text="Иначе?")),
                                    items[0], data_line_done=False)
    # строка вопроса исчезла
    gone = _doc(_q("Q-2-x"))
    gone["rules"] = [{"id": "R-2"}]
    assert "строк вопроса нет" in ac._judge(gone, items[0],
                                            data_line_done=False)
    # тот же вопрос — переносится
    assert ac._judge(_doc(_q("Q-2-x")), items[0], data_line_done=False) is None


def test_carry_without_book_receipt_is_reasked_not_carried(tmp_path):
    """Проба Prod run-9065 (04.10 09:1x): перенесённый ответ без квитанции
    книги отказан выпуском 409 source-answer-evidence. Закон: нет
    свидетельства источника — не «перенесён», а «задан заново» словами."""
    bare = _answered(_q("Q-2-x"))
    bare["answer"].pop("answer_version")
    items = ac.snapshot(_doc(bare))
    new = _doc(_q("Q-2-x"))
    fate = ac.carry(new, items, data_line_done=False, run_dir=tmp_path)
    assert fate[0]["status"] == ac.REASKED
    assert "свидетельства источника" in fate[0]["reason"]
    assert m.is_open_question(new["questions"][0])
    # квитанция есть, но книги ревизии нет / она другая — тоже заново
    items = ac.snapshot(_doc(_answered(_q("Q-2-x"))))
    fate = ac.carry(_doc(_q("Q-2-x")), items, data_line_done=False,
                    run_dir=tmp_path)
    assert fate[0]["status"] == ac.REASKED and "книги ответа" in fate[0]["reason"]
    _book(tmp_path, b"another book")
    fate = ac.carry(_doc(_q("Q-2-x")), items, data_line_done=False,
                    run_dir=tmp_path)
    assert fate[0]["status"] == ac.REASKED and "отпечаток" in fate[0]["reason"]


def test_carry_restores_original_revision_and_book_receipt(tmp_path):
    """Перенос кладёт ответ с ИСХОДНОЙ ревизией и квитанцией книги — их
    сверяет гейт выпуска (answers/rev-002/anketa-otvety.xlsx); прежде
    ответ получал «текущая + 1» и book_sha256 = None."""
    _book(tmp_path)
    old = _answered(_q("Q-2-x"))
    items = ac.snapshot(_doc(old))
    new = _doc(_q("Q-2-x"))
    fate = ac.carry(new, items, data_line_done=False, run_dir=tmp_path)
    assert fate == [{"id": "Q-2-x", "status": ac.CARRIED}]
    a = new["questions"][0]["answer"]
    assert a["revision"] == 2 and new["meta"]["revision"] == 2
    assert a["answer_version"] == old["answer"]["answer_version"]


def test_carry_keeps_null_receipt_time_not_redated(tmp_path):
    """Ревью GPT 09:44 (P1C-RECEIPT-TIME-01), НАДЗОР 09:45: исходная
    квитанция с неизвестным временем (`received_at: null`, перенесённая
    прежняя запись) переносится как есть — новой даты не подставляется."""
    _book(tmp_path)
    old = _answered(_q("Q-2-x"))
    old["answer"]["answer_version"]["received_at"] = None
    items = ac.snapshot(_doc(old))
    new = _doc(_q("Q-2-x"))
    fate = ac.carry(new, items, data_line_done=False, run_dir=tmp_path)
    assert fate == [{"id": "Q-2-x", "status": ac.CARRIED}]
    assert new["questions"][0]["answer"]["answer_version"] \
        == old["answer"]["answer_version"]
    assert new["questions"][0]["answer"]["answer_version"]["received_at"] is None


@pytest.mark.parametrize("damage", ["fingerprint", "incomplete", "time"])
def test_damaged_receipt_checked_before_apply_question_stays_open(tmp_path,
                                                                 damage):
    """Ревью GPT 09:57 (P1D-INVALID-RECEIPT-01), НАДЗОР 09:58: квитанция с
    изъяном проверяется ДО применения — вопрос открыт, ответа и следствий
    нет, судьба «задан заново» словами, исключения нет."""
    _book(tmp_path)
    old = _answered(_q("Q-2-x"))
    version = old["answer"]["answer_version"]
    if damage == "fingerprint":
        version["fingerprint"] = "0" * 12
    elif damage == "incomplete":
        version.pop("received_at")
    else:
        version["received_at"] = "вчера"
    items = ac.snapshot(_doc(old))
    new = _doc(_q("Q-2-x"))
    before = copy.deepcopy(new)
    fate = ac.carry(new, items, data_line_done=False, run_dir=tmp_path)
    assert fate[0]["status"] == ac.REASKED
    assert "изъяном" in fate[0]["reason"]
    assert new == before, "ответ или его следствия применены"


def test_restore_allows_same_revision_only_in_restore_mode():
    """`apply_answers(restore=True)` — ответ Истории ревизии 2 ложится на
    линии данных, когда модель уже на ревизии 2; без режима
    восстановления прежний закон «ревизия больше текущей» цел."""
    from cdpm import answers
    doc = _doc(_q("Q-2-x"))
    doc["meta"]["revision"] = 2
    with pytest.raises(answers.AnswersError):
        answers.apply_answers(doc, {"Q-2-x": "а"}, None, revision=2)
    answers.apply_answers(doc, {"Q-2-x": "а"}, None, revision=2,
                          book_sha256=BOOK_SHA, restore=True)
    assert doc["questions"][0]["answer"]["revision"] == 2
    assert doc["meta"]["revision"] == 2


def test_history_question_waits_for_data_line_then_reasked_if_absent():
    items = [{"id": "Q-3b-история-accumulator", "fingerprint": "x",
              "rule_refs": [], "raw": "б"}]
    assert ac._judge(_doc(), items[0], data_line_done=False) \
        == ac.PENDING_DATA_LINE
    assert "нет в новом своде" in ac._judge(_doc(), items[0],
                                            data_line_done=True)


def test_carry_pending_writes_fate_file(tmp_path):
    items = [{"id": "Q-3b-история-accumulator", "fingerprint": "x",
              "rule_refs": [], "raw": "б"}]
    fate = ac.carry(_doc(), items, data_line_done=False)
    assert fate == [{"id": "Q-3b-история-accumulator",
                     "status": ac.PENDING_DATA_LINE}]
    ac.write(tmp_path, items, fate)
    # линия данных вопроса так и не родила — задан заново словами
    assert ac.carry_pending(tmp_path, _doc()) == []
    rec = json.loads((tmp_path / ac.CARRY_FILE).read_text(encoding="utf-8"))
    assert rec["fate"][0]["status"] == ac.REASKED


def test_answer_survives_reextraction_through_panel(client, tmp_path):
    """Живой путь панели: ответ → «Извлечь правила» ещё раз → ответ цел
    (тот же вопрос закрыт, ключ тот же), судьба — в статусе и файле."""
    run_id = _upload(client).json()["run"]
    st = _extract_and_wait(client, run_id)
    assert st["state"] == "извлечено"
    model = client.get(f"/api/runs/{run_id}/file/модель").json()
    # Развилка части 2 применяется машинно («применён») — такой ответ
    # гейт выпуска `source-answer-evidence` и удостоверяет.
    q = next(q for q in model["questions"]
             if m.is_open_question(q) and q["id"].startswith("Q-2-")
             and q.get("answer_format", {}).get("type") == "choice")
    key = q["answer_format"]["options"][0]["key"]
    book = tmp_path / "otvety.xlsx"
    book.write_bytes(client.get(f"/api/runs/{run_id}/file/анкета-xlsx").content)
    _fill(book, {q["id"]: key})
    resp = client.post(f"/api/runs/{run_id}/answers",
                       files={"file": ("a.xlsx", book.read_bytes(), _XL_MIME)})
    assert resp.status_code == 200, resp.text
    deadline = time.monotonic() + 30
    while client.get(f"/api/runs/{run_id}").json()["state"] \
            == "применение ответов идёт" and time.monotonic() < deadline:
        time.sleep(0.1)
    before = client.get(f"/api/runs/{run_id}/file/модель").json()
    qb = next(x for x in before["questions"] if x["id"] == q["id"])
    assert not m.is_open_question(qb)

    st = _extract_and_wait(client, run_id)          # переизвлечение
    assert st["state"] == "извлечено", st.get("extract_problems")
    after = client.get(f"/api/runs/{run_id}/file/модель").json()
    qa = next(x for x in after["questions"] if x["id"] == q["id"])
    assert not m.is_open_question(qa), "ответ потерян переизвлечением"
    assert qa["answer"]["key"] == qb["answer"]["key"]
    carried = st["answers_carry"]
    assert q["id"] in carried["carried"]
    assert not any(r.startswith(q["id"] + ":") for r in carried["reasked"])
    # Проба Prod run-9065: перенесённый ответ несёт свидетельство источника —
    # гейт выпуска `source-answer-evidence` (тот, что отказал 409) проходит.
    assert qa["answer"]["revision"] == qb["answer"]["revision"]
    assert qa["answer"]["answer_version"] == qb["answer"]["answer_version"]
    import pathlib
    from cdpm import source_answer_evidence as sae
    from cdpm import webui
    run_dir = pathlib.Path(webui.runs_root()) / run_id
    files = sae.export_files(
        run_dir, (run_dir / "extract" / "model.json").read_bytes(), run_id)
    ev = json.loads(files[sae.EVIDENCE])
    assert q["id"] in [r["question"]["id"] for r in ev["answers"]]


def test_reextraction_before_data_line_keeps_pending_history_answer(tmp_path):
    """Ревью GPT 07:30, п. 1: ответ Истории ждёт линию данных; второе
    переизвлечение ДО неё не затирает его пустым снимком."""
    item = {"id": "Q-3b-история-accumulator", "fingerprint": "x",
            "rule_refs": [], "raw": "б", "source_refs": []}
    ac.write(tmp_path, [item], ac.carry(_doc(), [item], data_line_done=False))
    m.dump_model(_doc(), tmp_path / "model.json")      # модель без вопроса
    snap = ac.snapshot_file(tmp_path / "model.json")
    assert [i["id"] for i in snap] == ["Q-3b-история-accumulator"]
    fate = ac.carry(_doc(), snap, data_line_done=False)
    ac.write(tmp_path, snap, fate)
    rec = json.loads((tmp_path / ac.CARRY_FILE).read_text(encoding="utf-8"))
    assert rec["fate"] == [{"id": "Q-3b-история-accumulator",
                            "status": ac.PENDING_DATA_LINE}]


def test_file_summary_reflects_data_line_outcome(tmp_path):
    """Ревью GPT 07:30, п. 2: после линии данных сводка следа — уже не
    «ждёт», а итог (здесь: вопроса нет → задан заново)."""
    item = {"id": "Q-3b-история-accumulator", "fingerprint": "x",
            "rule_refs": [], "raw": "б", "source_refs": []}
    ac.write(tmp_path, [item], ac.carry(_doc(), [item], data_line_done=False))
    assert ac.file_summary(tmp_path)["pending_data_line"] == [item["id"]]
    ac.carry_pending(tmp_path, _doc())
    got = ac.file_summary(tmp_path)
    assert got["pending_data_line"] == []
    assert got["reasked"] and got["reasked"][0].startswith(item["id"])


def test_history_answer_survives_two_extractions_then_data_line(tmp_path):
    """НАДЗОР 07:31, п. 1: ответ «Истории» → извлечение → ещё раз
    извлечение ДО линии данных → линия данных родила тот же вопрос →
    ответ цел (применён тем же apply_answers)."""
    hq = _q("Q-3b-история-accumulator", text="Продолжать с нулями?")
    ex = tmp_path / "extract"
    ex.mkdir()
    _book(tmp_path)
    old = _doc(_answered(hq, raw="б"))
    m.dump_model(old, ex / "model.json")
    # извлечение 1: модель без вопроса Истории (его рождает линия данных)
    snap1 = ac.snapshot_file(ex / "model.json")
    new1 = _doc()
    ac.write(ex, snap1, ac.carry(new1, snap1, data_line_done=False,
                                 run_dir=tmp_path))
    m.dump_model(new1, ex / "model.json")
    # извлечение 2 — до линии данных
    snap2 = ac.snapshot_file(ex / "model.json")
    assert [i["id"] for i in snap2] == [hq["id"]], "ждущий ответ затёрт"
    new2 = _doc()
    # другой ответ той же ревизии 2 уже перенесён при извлечении —
    # модель на ревизии 2, ответ Истории той же ревизии ложится позже
    new2["meta"]["revision"] = 2
    ac.write(ex, snap2, ac.carry(new2, snap2, data_line_done=False,
                                 run_dir=tmp_path))
    # линия данных: тот же вопрос, то же издание
    new2["questions"].append(copy.deepcopy(hq))
    assert ac.carry_pending(ex, new2) == [hq["id"]]
    q = next(x for x in new2["questions"] if x["id"] == hq["id"])
    assert not m.is_open_question(q) and q["answer"]["key"] == "б"
    assert q["answer"]["revision"] == 2
    assert q["answer"]["answer_version"]["book_sha256"] == BOOK_SHA
    assert ac.file_summary(ex)["carried"] == [hq["id"]]


def test_data_run_updates_status_answers_carry(client, monkeypatch):
    """НАДЗОР 07:31, п. 2 (ревью GPT 07:30): после переноса на линии
    данных статус `answers_carry` обновляется — не остаётся «ждёт».
    Ждущий ответ Истории, чей вопрос линия данных не родила, — задан
    заново словами в статусе."""
    import pathlib
    from cdpm import webui
    from test_cdpm_webui import _run_checks_and_wait, _upload_two_books
    run_id = _upload(client).json()["run"]
    st = _extract_and_wait(client, run_id)
    assert st["state"] == "извлечено", st.get("extract_problems")
    _upload_two_books(client, run_id)
    ex = pathlib.Path(webui.runs_root()) / run_id / "extract"
    item = {"id": "Q-3b-история-accumulator", "fingerprint": "x",
            "rule_refs": [], "raw": "б", "source_refs": []}
    ac.write(ex, [item], [{"id": item["id"], "status": ac.PENDING_DATA_LINE}])
    _run_checks_and_wait(client, run_id, monkeypatch)
    status = client.get(f"/api/runs/{run_id}").json()
    got = status.get("answers_carry")
    assert got is not None, "статус не обновлён после линии данных"
    assert got["pending_data_line"] == []
    assert got["reasked"] and got["reasked"][0].startswith(item["id"])
