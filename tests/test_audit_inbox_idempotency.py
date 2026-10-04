"""Семантическая идемпотентность приёмника аудитов (аудит R9, P1-02).

Класс дефекта: идентификатор заявки строился из НОМЕРА находки в ответе
языковой модели, а модель после сбоя вызывалась заново. Детерминизм был
синтаксическим — он держался на допущении, что ответ повторится в том же
порядке. Аудитор воспроизвёл: первый ответ `[A]`, сбой до записи реестра,
повтор даёт `[X, A]` — в очереди две копии `A`, находка `X` потеряна, а
ссылки реестра указывают на семантически чужие строки.

Здесь закрыта ПРИЧИНА, а не следствие: разбор сохраняется до создания
заявок, восстановление к модели не обращается вовсе, идентификатор
считается от содержания находки, а уникальность обеспечивает сама точка
записи очереди.
"""
from __future__ import annotations

import pytest


def _finding(title: str, detail: str = "подробности", kind: str = "bug"):
    return {"kind": kind, "title": title, "detail": detail,
            "severity": 3, "effort": 5}


def test_finding_id_is_content_addressed_not_positional():
    """Перестановка находок не меняет их идентификаторов."""
    from webapp import audit_inbox as ai

    a, x = _finding("A"), _finding("X")
    first = {}
    ids_ab = [ai._finding_id("r.md", "sha", f, first) for f in (a, x)]
    second = {}
    ids_ba = [ai._finding_id("r.md", "sha", f, second) for f in (x, a)]
    assert ids_ab[0] == ids_ba[1], "идентификатор A обязан пережить перестановку"
    assert ids_ab[1] == ids_ba[0]


def test_dropped_finding_does_not_shift_neighbours():
    """Отбраковка соседа (пустой заголовок) прежде сдвигала все номера и
    вместе с ними — все идентификаторы."""
    from webapp import audit_inbox as ai

    a, b = _finding("A"), _finding("B")
    full = {}
    ids_full = [ai._finding_id("r.md", "sha", f, full) for f in (a, b)]
    short = {}
    ids_short = [ai._finding_id("r.md", "sha", f, short) for f in (b,)]
    assert ids_short[0] == ids_full[1]


def test_same_bytes_under_different_names_get_own_requests():
    """Два отчёта с одинаковым содержимым под разными именами прежде
    получали совпадающие идентификаторы: второй помечался обработанным,
    не создав ни одной заявки, и его находки исчезали молча."""
    from webapp import audit_inbox as ai

    f = _finding("A")
    one = ai._finding_id("10_00_01_08_2026_audit.md", "sha", f, {})
    two = ai._finding_id("11_00_01_08_2026_audit.md", "sha", f, {})
    assert one != two


def test_identical_findings_inside_one_audit_get_distinct_ids():
    """Буквально одинаковые находки внутри ОДНОГО отчёта обязаны остаться
    различимыми, иначе одна из них потеряется."""
    from webapp import audit_inbox as ai

    seen: dict = {}
    f = _finding("A")
    assert ai._finding_id("r.md", "sha", f, seen) != \
        ai._finding_id("r.md", "sha", f, seen)


def test_normalization_ignores_case_and_spacing():
    from webapp import audit_inbox as ai

    a = _finding("Утечка   памяти", "деталь")
    b = _finding("утечка памяти", "деталь")
    assert ai._finding_id("r.md", "sha", a, {}) == \
        ai._finding_id("r.md", "sha", b, {})


# ------------------------------------------- уникальность в точке записи

def _cr(rid: str, title: str = "t", summary: str = "s") -> dict:
    return {"id": rid, "agent": "audit", "title": title, "summary": summary,
            "status": "draft", "priority": 3, "effort": 5,
            "suggestion_ids": [], "source": "r.md", "country": None,
            "created_at": "2026-07-31T00:00:00Z",
            "updated_at": "2026-07-31T00:00:00Z"}


def test_duplicate_id_is_refused_at_the_write_point(tmp_path, monkeypatch):
    """Повтор с тем же содержанием пропускается молча (восстановление),
    повтор с ДРУГИМ содержанием — громкий отказ.

    Проверка стоит в физической точке записи: вызывающий не может её
    обойти, и любой будущий контур наследует её автоматически.
    """
    from webapp import change_requests as crq

    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "cr.json")
    assert len(crq._add_crs([_cr("cra1")], unique_by_id=True)) == 1
    # тот же идентификатор и то же содержание — восстановление
    assert crq._add_crs([_cr("cra1")], unique_by_id=True) == []
    # тот же идентификатор, другое содержание — коллизия
    with pytest.raises(crq.DuplicateChangeRequest):
        crq._add_crs([_cr("cra1", title="другое")], unique_by_id=True)


def test_row_without_id_is_refused(tmp_path, monkeypatch):
    from webapp import change_requests as crq

    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "cr.json")
    row = _cr("x")
    row.pop("id")
    with pytest.raises(ValueError):
        crq._add_crs([row], unique_by_id=True)


def test_duplicates_inside_one_batch_are_collapsed_once(tmp_path, monkeypatch):
    """Дубль внутри одной пачки не должен попадать в очередь дважды."""
    from webapp import change_requests as crq

    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "cr.json")
    added = crq._add_crs([_cr("cra1"), _cr("cra1")], unique_by_id=True)
    assert len(added) == 1
    assert len([c for c in crq.list_crs() if c["id"] == "cra1"]) == 1
