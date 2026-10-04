"""Display-only UTS39 internalSkeleton diagnostics, never identifier equality.

One full Unicode17 mapping dataset is shared with the UI. Standard-library NFD
uses the host Unicode version; bidi rendering is not implemented. A match means
possible visual confusion, not font-independent identity or permission to repair
a code. Callers retain their existing lookup, validation and mutation decisions.
"""
import json
import unicodedata
from bisect import bisect_right
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("confusables.json").read_text(encoding="utf-8"))
_MAPPING = {int(cp): target for cp, target in _DATA["mappings"].items()}
_RANGES = _DATA["defaultIgnorableRanges"]
_STARTS = [start for start, _ in _RANGES]


def _ignorable(cp: int) -> bool:
    i = bisect_right(_STARTS, cp) - 1
    return i >= 0 and cp <= _RANGES[i][1]


def internal_skeleton(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    retained = "".join(c for c in normalized if not _ignorable(ord(c)))
    return unicodedata.normalize("NFD", retained.translate(_MAPPING))


def visually_confusable(left: str, right: str) -> bool:
    return left != right and internal_skeleton(left) == internal_skeleton(right)


def code_points(value: str) -> str:
    return " ".join(f"U+{ord(c):04X}" for c in value)


def confusable_hint(value: str, candidates) -> str:
    """Extra explanation on an already-missing ID; never change its outcome."""
    if not isinstance(value, str):
        return ""
    known = {c for c in candidates if isinstance(c, str)}
    if value in known:
        return ""
    skeleton = internal_skeleton(value)
    matches = sorted(c for c in known if internal_skeleton(c) == skeleton)
    if not matches:
        return ""
    def display(s):
        head = s[:80]
        return f"{head!r} ({code_points(head)})" + ("…" if len(s) > 80 else "")
    shown = "; ".join(display(c) for c in matches[:3])
    if len(matches) > 3:
        shown += f"; ещё {len(matches) - 3}"
    return (f" — Обозначение {display(value)} может выглядеть похоже на {shown}, "
            "но строки различаются. Код не заменён. Сверьте обозначение в исходных "
            "данных и правиле; визуальное сходство не подтверждает соответствие.")
