# -*- coding: utf-8 -*-
"""Лист «Допуски» для независимого сертификатора — датами, не минутами.

Решатель судит свежесть допуска по окнам в минутах эпохи
(`Employee.qualificationWindows` -> `holdsQualification`), потому что так
дешевле считать на каждом ходе. Сертификатору те же сроки нужны датами:
его шаблон «действительность-на-дату» работает с календарём книги
данных, а не с эпохой. Перевод делается ЗДЕСЬ и один раз, чтобы у двух
сторон не завелось двух разных прочтений одного и того же срока.

Границы читаются ровно так же, как их записал конвертер пакета
(webapp/task_package.py): «Действует до» — последняя минута указанных
суток включительно; нижняя граница — полночь «Действует с», а без
этой колонки или при пустой ячейке окно открыто снизу (аудит A7D,
04.09.2026); срок не указан — окно открыто сверху. Открытые границы
отдаются пустой строкой, а не выдуманной датой: «не указан» не то же
самое, что «действует с 1970 года» или «просрочен».
"""

from __future__ import annotations

from datetime import datetime, timedelta

from webapp.task_package import QUAL_OPEN_END_MIN

#: Дословное прочтение границ — едет рядом со строками, чтобы читатель
#: сертификата не восстанавливал его из кода.
READING = ("«Действует до» — конец указанных суток включительно; пустая "
           "граница значит «срок не указан» (окно открыто), а не «просрочен»; "
           "перевод из минут эпохи UTC, которыми судит решатель")

_EPOCH = datetime(1970, 1, 1)


def _date_of(minutes, *, upper: bool) -> str:
    """Дата границы окна; пустая строка — граница не задана."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return ""
    if upper and m >= QUAL_OPEN_END_MIN:
        return ""
    if not upper and m <= 0:
        return ""
    moment = _EPOCH + timedelta(minutes=m)
    if upper:
        # Конец суток включительно: 23:59 тех же суток — это ИХ дата.
        return moment.date().isoformat()
    return moment.date().isoformat()


def qualification_rows(payload: dict) -> list[dict]:
    """Строки листа «Допуски»: сотрудник, тип судна, границы датами."""
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        windows = e.get("qualificationWindows") or {}
        for code in sorted(windows):
            for w in windows[code] or []:
                if not isinstance(w, (list, tuple)) or len(w) != 2:
                    continue
                rows.append({
                    "employee": str(e.get("id")),
                    "type": str(code),
                    "valid_from": _date_of(w[0], upper=False),
                    "valid_to": _date_of(w[1], upper=True),
                })
    rows.sort(key=lambda r: (r["employee"], r["type"], r["valid_from"]))
    return rows
