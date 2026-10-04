"""Bounded display of complete items; never use it to truncate stored evidence."""
from __future__ import annotations


def summarize_items(items, *, limit: int, separator: str = ", ", tail: bool = False) -> str:
    """Preserve short lists verbatim and explicitly count every omitted item."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    words = [str(item) for item in items]
    shown = words[-limit:] if tail else words[:limit]
    text = separator.join(shown)
    omitted = len(words) - len(shown)
    if omitted:
        extra = f"; показаны последние {len(shown)}" if tail else ""
        text += f" (и ещё {omitted}{extra})"
    return text
