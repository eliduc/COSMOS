"""Scalar spans and conversion from the selected catalogue's unit_words.

The scalar grammar remains bounded; vocabulary and scales are not inferred
from parameter names. A consumed word must be the word actually converted.
"""
from decimal import Decimal
from fractions import Fraction
import re

from template_catalog import units


def tokens(text, unit, source, rejected_words=None, *, consumed_words=None,
           count_rule_quote=False, rule_quote=False):
    from . import family_params as fp
    from .answer_subjects import _RELATION_RE

    entry = units.unit_words(source).get(unit)
    if not isinstance(entry, dict):
        return None
    # Longest first: "ч кредита" must not be consumed as the shorter "ч".
    words = {units._word(w) for w in entry.get("words", ())} | {unit}
    patterns = [(word, re.compile(r"\s*" + r"\s+".join(
        re.escape(part) for part in word.split())
        + r"(?:[." + fp._DOT_KEEP + r"])?(?![^\W\d]|_)", re.I))
        for word in sorted(words, key=lambda w: (-len(w), w))]
    scalar = re.compile(fp._HHMM_RE.pattern + "|" + fp._TOKEN_L
                        + fp._NUM_T + fp._NUM_R, re.I)
    component = re.compile(r"\s*" + fp._NUM_T + fp._NUM_R, re.I)
    punctuation = " \t\r\n.,;:()[]!?\"'«»„“”‘’" + fp._DASHES + fp._DOT_KEEP

    def word_at(end):
        for word, pattern in patterns:
            hit = pattern.match(text, end)
            if hit:
                return word, hit.end()
        return None, end

    def converted(number, word):
        return Fraction(units.to_canonical(number, word, unit,
                                          source=source, exact=True))

    out, cursor, converted_words = [], 0, []
    while (hit := scalar.search(text, cursor)) is not None:
        word, end = word_at(hit.end())
        word_spans = [(hit.end(), end)] if word else []
        try:
            if hit.group("num") is None:
                if (hit.group("sign") and unit == "time_of_day_minutes"
                        or fp._is_negative(hit.group("sign"))):
                    return None
                if word not in (None, units.CLOCK_WORD, unit):
                    return None
                value = converted(hit.group("h") + ":" + hit.group("m"),
                                  units.CLOCK_WORD)
            else:
                number = hit.group("num")
                magnitude = fp._magnitude(number)
                if magnitude is None or fp._is_negative(number):
                    return None
                if unit == "time_of_day_minutes":
                    return None
                if word is None and unit == "duration_minutes":
                    if rejected_words is not None:
                        rejected_words.append(fp._quantity_word_tail(text, end, len(text)))
                    return None
                value = converted(Decimal(number.replace(",", ".")), word or unit)
                # Preserve the established compound hour/minute grammar,
                # but only for words whose selected scales really are 60/1.
                if unit == "duration_minutes" and word and converted(1, word) == 60:
                    minute = component.match(text, end)
                    if minute:
                        minute_word, minute_end = word_at(minute.end())
                        if (minute_word and minute_word != units.CLOCK_WORD
                                and converted(1, minute_word) == 1):
                            amount = fp._magnitude(minute.group("num"))
                            if (amount is None or not fp._is_whole(magnitude)
                                    or not fp._is_whole(amount) or amount > 59
                                    or minute.group("num")[0] in "+-−"):
                                return None
                            value += converted(Decimal(minute.group("num")), minute_word)
                            word_spans.append((minute.end(), minute_end))
                            end = minute_end
        except (units.UnitsError, ValueError, ArithmeticError):
            return None
        out.append((value, (hit.start(), end)))
        converted_words.extend(word_spans)
        if consumed_words is not None:
            consumed_words.extend(word_spans)
        cursor = end

    if count_rule_quote and unit == "count":
        # A source clause can name what is counted ("2 пилота"). This is
        # a numeric witness, not the closed grammar of a company answer
        # and not a semantic proof of the noun. Do not infer a conversion
        # from that noun or alter the catalogue's unit/subject vocabulary.
        # Mask only words actually converted by this exact source release:
        # its count vocabulary may legitimately include e.g. "суток".
        mask = fp._covered_mask(text, converted_words)
        view = "".join(" " if mask[i] else char for i, char in enumerate(text))
        if fp._foreign_unit_at(view, unit):
            return None
        # Other declared units remain units, not arbitrary counted nouns.
        # This also covers catalogue-specific words absent from the legacy
        # refusal vocabulary. A word declared for count was consumed above.
        foreign = set()
        for code, spec in units.unit_words(source).items():
            if code != unit and isinstance(spec, dict):
                foreign.update(units._word(w) for w in spec.get("words", ()))
                foreign.add(code)
        foreign.difference_update(words)
        for _, (_, end) in out:
            for word in foreign:
                pattern = (r"\s*" + r"\s+".join(re.escape(p) for p in word.split())
                           + r"(?:[." + fp._DOT_KEEP + r"])?(?![^\W\d]|_)")
                if re.match(pattern, text[end:], re.I):
                    return None
        return out

    # A source quotation can retain an unnumbered calendar/duty period.
    # It is context, not a conversion word or permission to erase arbitrary
    # prose. In particular a ratio's declared unit is already consumed and
    # must not be reinterpreted as a period by the quotation reader.
    periods = fp._rule_quote_period_spans(text, converted_words) if rule_quote else []

    # Unit spelling is part of the token. Nothing after it can disappear
    # merely because the scalar was successfully converted.
    for index, (_, (_, end)) in enumerate(out):
        next_start = out[index + 1][1][0] if index + 1 < len(out) else len(text)
        tail = text[end:next_start]
        spans = [m.span() for m in _RELATION_RE.finditer(tail)]
        spans += [m.span() for m in fp._UNIVERSAL_SCOPE_RE.finditer(tail)]
        spans += [(start - end, stop - end) for start, stop in periods
                  if end <= start and stop <= next_start]
        if index + 1 < len(out):
            spans += [m.span() for m in re.finditer(r"(?<!\w)и(?!\w)", tail, re.I)]
        covered = fp._covered_mask(tail, spans)
        rest = "".join(" " if covered[i] else char for i, char in enumerate(tail))
        if rest.strip(punctuation):
            if rejected_words is not None:
                rejected_words.append(rest.strip(punctuation))
            return None
    return out


def word_spans(text, unit, source):
    """Only words actually converted, including before a refused later tail.

    A ratio's declared unit phrase is data, not an additional period. The
    span does not license any qualifier outside that exact catalogue word.
    """
    spans = []
    tokens(text, unit, source, consumed_words=spans)
    return spans
