# -*- coding: utf-8 -*-
"""Перенос ответов авиакомпании сквозь переизвлечение (РВ-140 п. 1,
слово владельца 04.10.2026 05:35).

Класс дефекта: ответ живёт в самой модели (`questions[].answer`), а
«Извлечь правила» пересобирает модель с нуля — ответ молча терялся
(Stage run-0025 03.10: вопросы переизданы, книга прежней ревизии
отказана; Prod run-9060 04.10: решение по Истории пропало из пакета).

Закон — один носитель правды и ни одного ответа молча:
1. Перед извлечением снимается снимок ответов прежней модели: id
   вопроса, отпечаток издания (`anketa_xlsx.question_fingerprint`), место
   в документе (`source_refs` — sid/anchor), сырой текст ответа.
2. После извлечения (а для вопросов линии данных — после того, как
   линия данных их родит) ответ применяется заново ТЕМ ЖЕ
   `answers.apply_answers`, если вопрос с тем же id стоит на том же
   месте, с тем же отпечатком, его строки живы и формат — выбор варианта
   (без модели, детерминированно). Ревизия и квитанция книги — ИСХОДНЫЕ:
   их сверяет гейт выпуска `source-answer-evidence` (книга
   `answers/rev-NNN/` переизвлечением не трогается); нет квитанции или
   книги того же SHA — ответ не переносится (проба Prod run-9065 04.10).
3. Иначе вопрос остаётся открытым — задаётся заново, — и причина
   пишется словами в `extract/answers-carry.json` и статус прогона.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from cdpm import anketa_xlsx
from cdpm import answer_versions
from cdpm import answers as answers_mod
from cdpm import history_section as hs
from cdpm import model as m

CARRY_FILE = "answers-carry.json"

PENDING_DATA_LINE = "ждёт линии данных"
CARRIED = "перенесён"
REASKED = "задан заново"


def _airline_answer(q: dict) -> str | None:
    """Сырой текст ответа авиакомпании закрытого вопроса либо None:
    открытые вопросы, снятые кодом и ответы без текста не переносятся."""
    ans = q.get("answer") if isinstance(q.get("answer"), dict) else None
    if not ans or m.is_open_question(q):
        return None
    raw = str(ans.get("raw") or "").strip()
    # Снятое кодом — не ответ авиакомпании (`model.close_question_by_code`).
    if not raw or ans.get("closed_by") == "code":
        return None
    return raw


def snapshot(doc: dict | None) -> list[dict]:
    """Снимок ответов модели до переизвлечения (пустой без модели)."""
    out: list[dict] = []
    for q in (doc or {}).get("questions", []) or []:
        if not isinstance(q, dict):
            continue
        raw = _airline_answer(q)
        if raw is None:
            continue
        out.append({
            "id": q.get("id"),
            "fingerprint": anketa_xlsx.question_fingerprint(q),
            "source_refs": [dict(s) for s in (q.get("source_refs") or [])
                            if isinstance(s, dict)],
            "rule_refs": list(q.get("rule_refs") or []),
            "raw": raw,
            "revision": (q.get("answer") or {}).get("revision"),
            # Квитанция книги ответа (отпечаток, время получения): без
            # неё перенесённый ответ не удостоверен для выпуска.
            "answer_version": copy.deepcopy(
                (q.get("answer") or {}).get("answer_version")),
        })
    return out


def snapshot_file(model_path: Path) -> list[dict]:
    """Снимок ответов модели плюс ответы, что ещё ЖДУТ линию данных в
    следе прежнего переноса (ревью GPT 04.10 07:30: переизвлечение до
    линии данных иначе затирало ждущий ответ Истории пустым снимком).
    Ответ из модели главнее ждущего с тем же id."""
    model_path = Path(model_path)
    items = snapshot(m.load_model(model_path)) if model_path.is_file() else []
    have = {i["id"] for i in items}
    path = model_path.parent / CARRY_FILE
    if path.is_file():
        rec = json.loads(path.read_text(encoding="utf-8"))
        prev = {i["id"]: i for i in rec.get("items") or []}
        for f in rec.get("fate") or []:
            if f.get("status") == PENDING_DATA_LINE and f["id"] in prev \
                    and f["id"] not in have:
                items.append(prev[f["id"]])
                have.add(f["id"])
    return items


def _receipt_problem(item: dict, run_dir: Path | None) -> str | None:
    """Свидетельство источника ответа — то, что сверяет гейт выпуска
    `source-answer-evidence`: ревизия применения, квитанция с отпечатком
    книги и сама книга `answers/rev-NNN/anketa-otvety.xlsx` того же SHA
    (проба Prod run-9065 04.10: без квитанции пакет отказан 409).
    Нет свидетельства — ответ не переносится, вопрос задаётся заново."""
    rev = item.get("revision")
    version = item.get("answer_version")
    book_sha = version.get("book_sha256") if isinstance(version, dict) else None
    if type(rev) is not int or rev < 2 or not isinstance(book_sha, str) \
            or re.fullmatch(r"[0-9a-f]{64}", book_sha) is None:
        return ("нет свидетельства источника ответа (квитанции книги "
                "анкеты) — ответ не переносится")
    # Вся квитанция против дословного ответа — ДО применения: изъян
    # (отпечаток не тот, неполная, неверное время) не должен успеть
    # применить ответ и его следствия (ревью GPT 09:57, P1D-INVALID-RECEIPT-01).
    bad = answer_versions.problems({"raw": item.get("raw"),
                                    "answer_version": version})
    if bad:
        return ("квитанция книги ответа с изъяном — ответ не переносится: "
                + "; ".join(bad))
    if run_dir is not None:
        book = Path(run_dir) / "answers" / f"rev-{rev:03d}" / "anketa-otvety.xlsx"
        if not book.is_file() \
                or hashlib.sha256(book.read_bytes()).hexdigest() != book_sha:
            return (f"книги ответа ревизии {rev} нет или она не та (отпечаток "
                    "не совпадает) — ответ не переносится")
    return None


def _judge(doc: dict, item: dict, *, data_line_done: bool,
           run_dir: Path | None = None) -> str | None:
    """Причина «задан заново» словами, PENDING_DATA_LINE либо None
    (ответ можно применить)."""
    by_id = {q.get("id"): q for q in doc.get("questions", []) or []
             if isinstance(q, dict)}
    q = by_id.get(item["id"])
    if q is None:
        if not data_line_done and (
                hs.is_history_question(item["id"])
                or answers_mod.is_data_line_question(item["id"])):
            return PENDING_DATA_LINE
        return "вопроса нет в новом своде (правило изменилось или исчезло)"
    if not m.is_open_question(q):
        return "вопрос в новом своде уже закрыт — ответ не перезаписан"
    if anketa_xlsx.question_fingerprint(q) != item["fingerprint"]:
        return "вопрос изменился (другой отпечаток издания)"
    live = {r.get("id") for r in doc.get("rules", []) or []
            if isinstance(r, dict)}
    gone = [rid for rid in item.get("rule_refs") or [] if rid not in live]
    if gone:
        return f"строк вопроса нет в новом своде: {', '.join(gone)}"
    if (q.get("answer_format") or {}).get("type") != "choice":
        return ("свободный ответ вносит обработчик — автоматически не "
                "переносится")
    return _receipt_problem(item, run_dir)


def carry(doc: dict, items: list[dict], *, data_line_done: bool,
          run_dir: Path | None = None) -> list[dict]:
    """Применить переносимые ответы к `doc` (на месте) с ИСХОДНОЙ
    ревизией и квитанцией книги — тем же `apply_answers` в режиме
    восстановления. Возвращает судьбу КАЖДОГО ответа снимка:
    {id, status, reason?}."""
    fate: dict[str, dict] = {}
    groups: dict[tuple, dict[str, str]] = {}
    for item in items:
        why = _judge(doc, item, data_line_done=data_line_done, run_dir=run_dir)
        if why is None:
            version = item["answer_version"]
            key = (item["revision"], version.get("received_at"),
                   version["book_sha256"])
            groups.setdefault(key, {})[item["id"]] = item["raw"]
        elif why == PENDING_DATA_LINE:
            fate[item["id"]] = {"id": item["id"], "status": PENDING_DATA_LINE}
        else:
            fate[item["id"]] = {"id": item["id"], "status": REASKED,
                                "reason": why}
    by_id = {q.get("id"): q for q in doc.get("questions", [])
             if isinstance(q, dict)}
    versions = {i["id"]: i["answer_version"] for i in items
                if any(i["id"] in g for g in groups.values())}
    for (rev, received_at, book_sha), raw_map in sorted(
            groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), kv[0][2])):
        answers_mod.apply_answers(doc, raw_map, None, received_at=received_at,
                                  book_sha256=book_sha, revision=rev,
                                  restore=True)
        for qid in raw_map:
            a = (by_id.get(qid) or {}).get("answer") or {}
            if isinstance(by_id.get(qid, {}).get("answer"), dict):
                # Квитанция — ИСХОДНАЯ, байт в байт: `context()` подставил бы
                # текущее время вместо неизвестного исторического (null)
                # (ревью GPT 09:44, P1C-RECEIPT-TIME-01).
                a["answer_version"] = copy.deepcopy(versions[qid])
            if m.is_open_question(by_id.get(qid) or {}) \
                    or a.get("revision") != rev \
                    or answer_versions.problems(a) \
                    or (a.get("answer_version") or {}).get("book_sha256") \
                    != book_sha:
                fate[qid] = {"id": qid, "status": REASKED,
                             "reason": "ответ не применился однозначно — "
                                       "вопрос задан заново"}
            else:
                fate[qid] = {"id": qid, "status": CARRIED}
    return [fate[i["id"]] for i in items if i["id"] in fate]


def write(extract_dir: Path, items: list[dict], fate: list[dict]) -> Path | None:
    """След переноса. Без ответов в снимке файл пишется лишь поверх
    прежнего (пустой — чтобы ждущие ответы старого извлечения не ожили
    на линии данных); нечего переносить и следа нет — файла нет."""
    path = Path(extract_dir) / CARRY_FILE
    if not items and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items, "fate": fate},
                               ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def carry_pending(extract_dir: Path, doc: dict) -> list[str]:
    """Линия данных родила свои вопросы: применить ответы, что ждали её.
    Возвращает id перенесённых; файл следа обновляется."""
    path = Path(extract_dir) / CARRY_FILE
    if not path.is_file():
        return []
    rec = json.loads(path.read_text(encoding="utf-8"))
    items = {i["id"]: i for i in rec.get("items") or []}
    pending = [items[f["id"]] for f in rec.get("fate") or []
               if f.get("status") == PENDING_DATA_LINE and f["id"] in items]
    if not pending:
        return []
    got = {f["id"]: f for f in carry(doc, pending, data_line_done=True,
                                     run_dir=Path(extract_dir).parent)}
    rec["fate"] = [got.get(f["id"], f) for f in rec.get("fate") or []]
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return [qid for qid, f in got.items() if f["status"] == CARRIED]


def file_summary(extract_dir: Path) -> dict | None:
    """Сводка судьбы ответов по следу (для статуса после линии данных)."""
    path = Path(extract_dir) / CARRY_FILE
    if not path.is_file():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    if not rec.get("items"):
        return None
    return summary(rec.get("fate") or [])


def summary(fate: list[dict]) -> dict:
    """Сводка для статуса прогона словами и числами."""
    return {
        "carried": [f["id"] for f in fate if f["status"] == CARRIED],
        "pending_data_line": [f["id"] for f in fate
                              if f["status"] == PENDING_DATA_LINE],
        "reasked": [f"{f['id']}: {f.get('reason')}" for f in fate
                    if f["status"] == REASKED],
    }
