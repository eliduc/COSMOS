# -*- coding: utf-8 -*-
"""Линт независимости кода от наборов (план «прямой ход» 2.9, решение
владельца 02.09: ни CDPM, ни COSMOS не зависят от набора ИДАК H0-ВМП).

Ищет в ИСПОЛНЯЕМОМ коде (cdpm/, vmp/, webapp/, kotlin-solver/src/main)
маркеры конкретных наборов и заказчиков-стендов: идентификаторы строк
эталона H0, коды баз Поляриса, имена наборов, версии ставок. Комментарии
и докстринги не судятся — там лежат уроки прогонов («прогон №5 H0: …»),
это законно; судится то, что исполняется: имена, строки-литералы в
выражениях, условия.

Запуск: `python -m tools.kit_independence_lint [--repo ПУТЬ]` —
находки с контекстом, код возврата 1 при любой.
"""
from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCOPES = ("cdpm", "vmp", "webapp", "kotlin-solver/src/main")
_WORD = "0-9A-Za-zА-Яа-яЁё_"
MARKERS = {
    "набор H0": r"H0|ИДАК-0|ID-H0-VMP|VMP-H0",
    "строка эталона": r"VN-\d{3}|O-PAIR|O-ROST",
    "стенд Полярис": r"Полярис|Polyaris|polyaris|Борей|Borealis",
    "коды баз стенда": r"PLY|SVG|GRT|Северогорск|Полярный|Гранитный",
    "версия ставок стенда": r"M-036|demo-1",
}
_PATTERNS = {name: re.compile(f"(?<![{_WORD}])(?:{rx})(?![{_WORD}])")
             for name, rx in MARKERS.items()}
# Законные вхождения: SVG как формат изображения в вебе.
_ALLOW = {"коды баз стенда": re.compile(r"image/svg|\.svg|<svg|svg\+xml|svgNS|svgElement",
                                        re.IGNORECASE)}
# Поимённые исключения (файл → причина): образцы данных стенда по замыслу
# несут имя стенда; ветвей поведения под набор в них нет.
EXCEPTIONS = {
    "cdpm/sample.py": "образец данных вымышленной авиакомпании (демо)",
    "cdpm/data_report.py": "образец отчёта по данным (демо-заказчик в шапке образца)",
}


def _py_code_lines(text: str) -> dict:
    """Строки файла Python с вычеркнутыми комментариями и докстрингами
    (позиция → текст исполняемой части)."""
    out: dict = {}
    lines = text.splitlines()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return {n + 1: ln for n, ln in enumerate(lines)}
    skip_spans: list = []          # (start_line, end_line) комментариев/докстрингов
    prev_sig = None
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            skip_spans.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
        elif tok.type == tokenize.STRING:
            if prev_sig in (None, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                            tokenize.ENCODING, tokenize.NL):
                skip_spans.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev_sig = tok.type
    for n, ln in enumerate(lines, 1):
        kept = ln
        for (sl, sc, el, ec) in skip_spans:
            if sl <= n <= el:
                a = sc if n == sl else 0
                b = ec if n == el else len(kept)
                kept = kept[:a] + " " * max(0, b - a) + kept[b:]
        if kept.strip():
            out[n] = kept
    return out


def _kt_code_lines(text: str) -> dict:
    out: dict = {}
    in_block = False
    for n, ln in enumerate(text.splitlines(), 1):
        s = ln
        if in_block:
            if "*/" in s:
                s = s.split("*/", 1)[1]
                in_block = False
            else:
                continue
        if "/*" in s:
            head, _, tail = s.partition("/*")
            if "*/" in tail:
                s = head + tail.split("*/", 1)[1]
            else:
                s = head
                in_block = True
        s = s.split("//", 1)[0]
        if s.strip():
            out[n] = s
    return out


def scan(repo: Path = REPO) -> list[tuple[str, int, str, str]]:
    findings: list = []
    for scope in SCOPES:
        base = repo / scope
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix not in (".py", ".kt") or not p.is_file():
                continue
            if "__pycache__" in p.parts or "node_modules" in p.parts:
                continue
            rel = str(p.relative_to(repo)).replace("\\", "/")
            if rel in EXCEPTIONS:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            code = _py_code_lines(text) if p.suffix == ".py" else _kt_code_lines(text)
            for n, ln in code.items():
                for name, pat in _PATTERNS.items():
                    if pat.search(ln):
                        allow = _ALLOW.get(name)
                        if allow and allow.search(text.splitlines()[n - 1]):
                            continue
                        findings.append((rel, n, name, text.splitlines()[n - 1].strip()[:120]))
                        break
    return findings


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    repo = (Path(argv[argv.index("--repo") + 1]).resolve()
            if "--repo" in argv else REPO)
    findings = scan(repo)
    print(f"репозиторий: {repo}; области: {SCOPES}; маркеров: {len(MARKERS)}; "
          f"находок: {len(findings)}")
    for rel, n, name, ctx in findings:
        print(f"  {rel}:{n}  [{name}]  {ctx}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
