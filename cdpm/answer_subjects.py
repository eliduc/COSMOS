"""Literal parameter-subject evidence for the typed-answer parser.

The locator preserves all hits; the application guard refuses other subjects
and unresolved collisions. Neither assigns multiple parameters or closes rows.
Offsets always address the original raw answer; no normalization rewrites it.
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass

from . import catalog_bridge


# Closed relation grammar, not a parameter lexicon. The relation itself is
# authoritative only in the selected catalogue's parameter contract.
_RELATION_PHRASES = {
    "min": ("не менее", "не меньше", "минимум", "как минимум", "от", "и более",
            "хотя бы", "начиная с"),
    "max": ("не более", "не больше", "не свыше", "максимум", "как максимум", "до",
            "самое большее", "в пределах", "и менее"),
    "eq": ("ровно", "точно"),
}
_RELATION_WORDS = {phrase: relation for relation, phrases in _RELATION_PHRASES.items()
                   for phrase in phrases}
# Strict bounds cannot silently become the catalogue's inclusive min/max.
_RELATION_WORDS.update({"менее": "lt", "меньше": "lt", "более": "gt", "больше": "gt",
                        "свыше": "gt"})
_RELATION_SYMBOLS = {">=": "min", "≥": "min", "<=": "max", "≤": "max",
                     "=": "eq", ">": "gt", "<": "lt", "≠": "ne"}
_RELATION_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(r"\s+".join(re.escape(p) for p in phrase.split())
                            for phrase in sorted(_RELATION_WORDS, key=len, reverse=True))
    + r")(?!\w)|(?:>=|<=|[≥≤=><≠])", re.IGNORECASE)


@dataclass(frozen=True)
class SubjectMention:
    parameter: str
    word: str
    start: int
    end: int
    quote: str


def mentions(raw: str, family: str) -> tuple[SubjectMention, ...]:
    """Return every subject hit, including collisions between parameters.

    Full catalogue words/phrases only: a family stem is not a parameter
    subject. Whitespace and case may differ, but word boundaries and all
    punctuation in the catalogue phrase remain significant.
    """
    if not isinstance(raw, str):
        raise ValueError("ответ для разбора предмета должен быть строкой")
    found = set()
    for parameter, words in catalog_bridge.subjects(family).items():
        for word in words:
            phrase = r"\s+".join(re.escape(part) for part in word.split())
            pattern = re.compile(r"(?<!\w)" + phrase + r"(?!\w)", re.IGNORECASE)
            for hit in pattern.finditer(raw):
                found.add(SubjectMention(parameter, word, hit.start(), hit.end(), hit.group()))
    return tuple(sorted(found, key=lambda hit: (hit.start, hit.end, hit.parameter, hit.word)))


def value_text(raw: str, family: str, parameter: str, unit) -> str:
    """Scalar parse view, never a replacement for source text or quotations.

    An exact catalogue subject may contain a period/scope word of its own.
    Exclude that span only at a closed value boundary. In particular, the
    subject "в базе" cannot swallow the introducer in "в базе SVO": the
    undeclared attachment must remain visible to the scope guard.
    """
    from template_catalog.validate import _uses_schema_070
    from cdpm import family_params as fp
    if unit not in fp._NUMERIC_UNITS or not _uses_schema_070(catalog_bridge.catalog().version()):
        return raw
    text = list(raw)
    for hit in mentions(raw, family):
        if hit.parameter != parameter:
            continue
        tail = raw[hit.end:].lstrip(" \t\r\n.,;:()[]!?=—–-\"«»")
        relation = _RELATION_RE.match(tail)
        if relation:
            tail = tail[relation.end():].lstrip()
        if not tail or re.match(r"[+−-]?\d", tail):
            text[hit.start:hit.end] = " " * (hit.end - hit.start)
    return "".join(text)


def value_context_problem(raw: str, family: str, parameter: str, unit) -> str:
    """Keep raw-text safety checks as well as unconsumed scope/periods."""
    from cdpm import family_params as fp
    if (fp.mixed_script_word(raw) or fp.line_number_record(raw)
            or fp._decline_parts(raw) is not None):
        return fp.value_context_problem(raw, unit)
    return fp.value_context_problem(value_text(raw, family, parameter, unit), unit)


def read_value(raw: str, family: str, parameter: str, unit, domain=None):
    """Read the projected value, return the unchanged original sentence.

    The caller still judges the full answer with ``problem``. No selecting a
    requested subject out of multiple subjects, and no derived text persisted
    as a quotation. Legacy/code paths retain their exact existing reader.
    """
    from cdpm import family_params as fp
    if value_text(raw, family, parameter, unit) == raw:
        return fp.parse_value(raw, unit, domain)
    if value_context_problem(raw, family, parameter, unit):
        return None
    for original in fp._sentences(raw):
        sentence = value_text(original, family, parameter, unit)
        status, value = fp._parse_sentence(sentence, str(unit))
        if status == fp._VALUE:
            return (value, original) if fp._in_domain(value, domain) else None
        if status == fp._REFUSED or fp._has_payload(sentence, str(unit)):
            return None
    return None


def _relation_problem(raw, family, parameter, unit):
    """Judge every supported relation, with or without a named subject.

    Subject spans are excluded before reading grammar words: catalogue data
    cannot accidentally act as operators. Each sentence keeps its own bound;
    equal numbers do not make opposing bounds interchangeable.
    """
    from cdpm import family_params as fp
    if unit not in fp._NUMERIC_UNITS:
        return None
    expected = fp._param_spec(family, parameter).get("relation")
    # Split the original first: a masked initial capital must not remove a
    # sentence boundary and let a later malformed value hide in the first.
    for original in fp._sentences(raw):
        sentence = original
        for hit in sorted(mentions(original, family), key=lambda h: h.start, reverse=True):
            sentence = sentence[:hit.start] + " " * (hit.end - hit.start) + sentence[hit.end:]
        spans = fp._value_spans(sentence, str(unit))
        spans += [hit.span() for hit in fp._QTY_RE.finditer(sentence)]
        for hit in _RELATION_RE.finditer(sentence):
            phrase = " ".join(hit.group().lower().split())
            relation = _RELATION_WORDS.get(phrase, _RELATION_SYMBOLS.get(phrase))
            if re.search(r"\bне\s*$", sentence[:hit.start()], re.IGNORECASE):
                return "предмет: отрицание отношения ответа не разобрано; уточните предел"
            before = any(hit.end() <= start for start, _ in spans)
            after = any(end <= hit.start() for _, end in spans)
            if phrase == "=" and before and after:
                # Two equivalent notations (10 h = 600 min) describe one
                # magnitude, not an equality constraint on the parameter.
                status, _ = fp._parse_sentence(sentence, str(unit))
                if status == fp._VALUE:
                    continue
            if not spans or (phrase in ("от", "до", "начиная с") and not before) or (
                    phrase in ("и более", "и менее") and not after) or (
                    phrase in _RELATION_SYMBOLS and not before):
                return "предмет: положение отношения относительно значения не разобрано"
            if relation != expected:
                return (f"предмет: отношение ответа {relation} не равно отношению "
                        f"параметра {parameter} в каталоге ({expected}); уточните предел")
    return None


def _relation_help(expected):
    """Name usable scalar forms without asking to erase a real restriction."""
    names = {"min": "нижнего предела", "max": "верхнего предела", "eq": "точного значения"}
    if expected not in names:
        return ("Для этого отношения нет поддержанной скалярной формулировки; "
                "уточните вопрос и условия, не заменяйте их одним числом")
    # 'В пределах' is a known relation word but the independent period guard
    # refuses it. A suggested input must work through the complete reader.
    words = [word for word in _RELATION_PHRASES[expected] if word != "в пределах"]
    forms = [("… " + word if word.startswith("и ") else word + " …") for word in words]
    forms += [symbol + " …" for symbol, relation in _RELATION_SYMBOLS.items() if relation == expected]
    return (f"Для {names[expected]} распознаются: " + ", ".join(f"«{form}»" for form in forms)
            + ". Вместо … укажите значение в формате вопроса; если смысл иной, уточните "
              "сам вопрос, не удаляйте ограничения ради формата")


def _unread_numeric_problem(raw, family, parameter, unit):
    """A catalogue subject licenses that span, not arbitrary adjacent prose.

    Consume only actual scalar tokens and already judged grammar. Everything
    else stays an explicit unresolved fragment, including nonnumeric later
    sentences. This is a closed parse boundary, not a modifier blacklist.
    Original text is never rewritten in the answer or its value quotation.
    """
    from cdpm import family_params as fp
    if unit not in fp._NUMERIC_UNITS:
        return None
    punctuation = " \t\r\n.,;:()[]!?\"'«»„“”‘’" + fp._DASHES + fp._DOT_KEEP
    for original in fp._sentences(raw):
        subject_spans = [(hit.start, hit.end) for hit in mentions(original, family)]
        mask = fp._covered_mask(original, subject_spans)
        sentence = "".join(" " if mask[i] else char for i, char in enumerate(original))
        tokens = fp._sentence_tokens(sentence, str(unit))
        if tokens is None or fp.value_context_problem(sentence, unit):
            # The scalar/scope reader already refuses this sentence. Keep
            # its specific unit, period, or applicability explanation.
            continue
        value_spans = [span for _, span in tokens]
        spans = subject_spans + value_spans
        spans += [hit.span() for hit in _RELATION_RE.finditer(sentence)]
        # The shared applicability guard above accepted these only as an
        # unqualified universal domain, never 'для всех ... кроме ...'.
        spans += [hit.span() for hit in fp._UNIVERSAL_SCOPE_RE.finditer(sentence)]
        for hit in re.finditer(r"(?<!\w)и(?!\w)", sentence, re.IGNORECASE):
            if (any(end <= hit.start() for _, end in value_spans)
                    and any(hit.end() <= start for start, _ in value_spans)):
                spans.append(hit.span())
        mask = fp._covered_mask(original, spans)
        start = 0
        while start < len(original):
            if mask[start]:
                start += 1
                continue
            end = start + 1
            while end < len(original) and not mask[end]:
                end += 1
            fragment = original[start:end].strip(punctuation)
            if fragment:
                return (f"предмет: фрагмент «{fragment}» не распознан; "
                        + _relation_help(fp._param_spec(family, parameter).get("relation")))
            start = end
    return None


def problem(raw: str, family: str, parameter: str, unit) -> str | None:
    """Check the complete answer before it can justify one parameter.

    The same catalogue-version selector as validation keeps the explicit 0.5
    migration boundary. Modern catalogues must supply their own vocabulary.
    Mixed subjects remain unresolved until multi-parameter effects exist;
    selecting just the requested clause would silently discard other effects.
    This does not turn unresolved conditions into executable scope.
    """
    from template_catalog.validate import _uses_schema_070
    from cdpm import family_params as fp
    try:
        if not _uses_schema_070(catalog_bridge.catalog().version()):
            return None
        words = catalog_bridge.subjects(family)
        if parameter not in words:
            return f"предмет: каталог не задаёт слова параметра {family}.{parameter}"
        found = mentions(raw, family)
    except catalog_bridge.CATALOG_ERRORS as exc:
        return f"предмет: разбор не подтверждён каталогом: {exc}"
    subjects = {hit.parameter for hit in found}
    if subjects - {parameter}:
        return (f"предмет: ответ называет {', '.join(sorted(subjects))}, "
                f"а вопрос относится к {parameter}; несколько предметов "
                "нужно разрешить по отдельным параметрам, не выбирать первый")
    # A catalogue phrase can also be a scope introducer (e.g. "в базе").
    # Name both readings instead of asking to delete genuine restrictions.
    # This is an ambiguity diagnosis, not permission to discard the scope.
    value_view = value_text(raw, family, parameter, unit)
    negation = fp.negated_value_problem(value_view, unit)
    if negation:
        return negation
    approximation = fp.approximate_value_problem(value_view, unit)
    if approximation:
        return approximation
    if fp.applicability_qualifier(value_view, unit):
        overlaps = {hit.quote for hit in found if hit.parameter == parameter
                    for marker in fp._APPLICABILITY_INTRO_RE.finditer(raw)
                    if max(hit.start, marker.start()) < min(hit.end, marker.end())}
        if overlaps:
            shown = ", ".join(f"«{word}»" for word in sorted(overlaps))
            return (f"предмет: {shown} имеет две трактовки: предмет параметра "
                    f"{parameter} по каталогу и оговорка применимости; "
                    "уточните предмет и область действия, не удаляйте ограничения")
    relation_problem = _relation_problem(raw, family, parameter, unit)
    if relation_problem:
        return relation_problem + "; " + _relation_help(fp._param_spec(family, parameter).get("relation"))
    if unit == "time_of_day_minutes":
        unread_clock = fp.unread_quantity_context(value_view, unit)
        if unread_clock:
            return "единица: " + fp._clock_word_note(unread_clock)
    elif unit in fp._NUMERIC_UNITS:
        unread_unit = fp.unread_quantity_context(value_view, unit)
        if unread_unit:
            return (f"единица: фрагмент «{unread_unit}» не распознан при значении "
                    f"{unit}; {fp.unit_words(unit)}. "
                    "Не удаляйте ограничения и не меняйте смысл ради формата")
    if subjects:
        unread = _unread_numeric_problem(raw, family, parameter, unit)
        if unread:
            return unread
    if not subjects:
        # A bare value is an answer to the addressed question. Free prose is
        # not: unknown subjects must not be inferred from a family name/note.
        normalized = fp._normalized(raw)
        rest = normalized or ""
        # Even a number lacking its required unit is still a numerical answer,
        # not an unknown subject. The value parser diagnoses the missing unit.
        spans = fp._value_spans(rest, str(unit))
        spans += [hit.span() for hit in fp._QTY_RE.finditer(rest)]
        if unit in fp._NUMERIC_UNITS:
            spans += [span for _, span in (fp._sentence_tokens(rest, str(unit)) or [])]
        if unit in fp.m._cat("CODE_UNITS"):
            # Retain the existing code grammar, including its full-text
            # manufacturer/reference guards. Bare codes are not prose aliases.
            spans += [hit.span() for hit in fp._CODE_TOKEN_RE.finditer(rest)]
        for start, end in sorted(spans, reverse=True):
            rest = rest[:start] + " " * (end - start) + rest[end:]
        rest = " ".join(rest.strip(" \t\r\n.,;:()[]=!?" + fp._DOT_KEEP).lower().split())
        # Every relation was already checked against the catalogue above.
        # Repeating a matching bound is not an unknown subject. Leave all
        # other prose visible; the scalar reader still checks equal values.
        if unit in fp._NUMERIC_UNITS and _RELATION_RE.search(rest):
            rest = _RELATION_RE.sub(" ", rest)
            rest = re.sub(r"(?<!\w)и(?!\w)", " ", rest)
            rest = rest.strip(" \t\r\n.,;:()[]=!?" + fp._DOT_KEEP)
        if not normalized or rest:
            return (f"предмет: предмет ответа не определён словами каталога "
                    f"для {family}.{parameter}; назовите его явно")
    values = []
    sentences = [value_text(sent, family, parameter, unit) for sent in fp._sentences(raw)]
    for sent in sentences:
        got = fp.parse_value(sent, unit)
        if got is not None:
            values.append(got[0])
        elif len(sentences) > 1 and fp._has_payload(sent, str(unit)):
            return (f"предмет: не все значения ответа для {parameter} разобраны; "
                    "нельзя выбрать только первое понятное предложение")
    if len({json.dumps(v, sort_keys=True, ensure_ascii=False) for v in values}) > 1:
        return f"предмет: для {parameter} названы разные значения; выбор первого запрещён"
    return None
