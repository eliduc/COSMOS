# -*- coding: utf-8 -*-
"""Журнал действий службы — `<рабочая папка>/actions.jsonl`.

Строка на действие: когда, кто, что, над чем, чем кончилось и слова.
Журнал ведётся ТОЛЬКО на рабочем месте (режим правки): в режиме
просмотра служба не пишет на диск ничего — ни замка, ни журнала, ни
рабочей папки (§1.4 проекта, гейт T2).

Форма строки закрыта шестью полями — её читают экран «Версии» (свёрнутая
панель «Журнал действий службы») и карточка ключа словаря («правка с
историей»).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FIELDS = ("at", "actor", "op", "target", "outcome", "words")
#: Исходы действия: закрытый перечень, чтобы журнал читался машинно.
OUTCOMES = ("ok", "refused", "warning")


def now_words() -> str:
    """Момент с точностью до секунды и со смещением часового пояса —
    образец `$defs/datetime` схемы (долей секунды в каталоге не бывает)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Journal:
    """Дописыватель журнала. Выключенный журнал (режим просмотра) молча
    ничего не делает — но и не притворяется, что писал: ручка
    `GET /api/journal` в просмотре говорит это словами."""

    def __init__(self, path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)

    def append(self, *, actor: str | None, op: str, target: str,
               outcome: str = "ok", words: str = "") -> dict | None:
        if not self.enabled:
            return None
        row = {"at": now_words(), "actor": actor, "op": str(op),
               "target": str(target), "outcome": str(outcome),
               "words": str(words)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
        return row

    def read(self, limit: int = 200) -> list[dict]:
        """Последние строки, новые первыми. Битая строка не роняет
        чтение: она отдаётся как есть под ключом `raw` — журнал обязан
        читаться даже после обрыва записи."""
        if not self.enabled or not self.path.is_file():
            return []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        rows: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                rows.append({"at": None, "actor": None, "op": "?",
                             "target": "?", "outcome": "warning",
                             "words": "строка журнала не читается",
                             "raw": line[:400]})
                continue
            rows.append(row if isinstance(row, dict) else {"raw": str(row)})
        rows.reverse()
        return rows[:max(0, int(limit))]

    def last(self, *, op: str, contains: str | None = None) -> dict | None:
        """Последняя строка журнала с этим действием (и этими словами) —
        либо None, если её нет.

        Нужна квитанции заявки: `journal_ref` обязан ссылаться на ЗАПИСЬ
        ЖУРНАЛА о приёме, а не пересказывать поля заявки. При повторной
        выдаче квитанции (200) строка ищется по номеру заявки в словах:
        своего поля под номер у строки журнала нет, а заводить его
        значило бы менять закрытую форму строки (шесть полей, их читают
        экран «Версии» и карточка словаря)."""
        for row in self.read(10_000):
            if str(row.get("op")) != str(op):
                continue
            if contains is not None and contains not in str(row.get("words")):
                continue
            return row
        return None

    def for_key(self, key: str, limit: int = 100) -> list[dict]:
        """Строки журнала по ключу словаря (карточка «Словари»)."""
        target = f"dictionaries/{key}"
        return [r for r in self.read(10_000)
                if str(r.get("target")) == target][:max(0, int(limit))]
