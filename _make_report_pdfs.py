"""Rebuild final-report PDFs: markdown -> styled HTML -> Edge headless.

Usage: python _make_report_pdfs.py [file.md ...]
Default: PROFESSIONAL-REPORT-RU.md and EXEC-SUMMARY-RU.md.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
FR = HERE / "final-report"
EDGE_CANDIDATES = [
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
]

CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 11pt;
       line-height: 1.45; color: #1a1a1a; max-width: 19cm; margin: auto; }
h1 { font-size: 17pt; border-bottom: 2px solid #444; padding-bottom: 4px; }
h2 { font-size: 14pt; margin-top: 22px; border-bottom: 1px solid #999;
     padding-bottom: 2px; }
h3 { font-size: 12.5pt; margin-top: 18px; }
h4 { font-size: 11.5pt; margin-top: 14px; }
table { border-collapse: collapse; margin: 10px 0; font-size: 9.5pt; }
th, td { border: 1px solid #bbb; padding: 4px 8px; text-align: left; }
th { background: #f0f0f0; }
code { font-family: Consolas, monospace; font-size: 9.5pt;
       background: #f5f5f5; padding: 0 3px; }
em { color: #444; }
"""


def build(md_path: Path) -> Path:
    html_body = markdown.markdown(
        md_path.read_text(encoding="utf-8"),
        extensions=["tables", "sane_lists"])
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body>{html_body}</body></html>")
    html_path = md_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    pdf_path = md_path.with_suffix(".pdf")
    edge = next((e for e in EDGE_CANDIDATES if e.exists()), None)
    if edge is None:
        raise SystemExit("Edge not found")
    subprocess.run([str(edge), "--headless", "--disable-gpu",
                    f"--print-to-pdf={pdf_path}",
                    "--no-pdf-header-footer",
                    html_path.as_uri()], check=True, timeout=120)
    html_path.unlink()
    return pdf_path


def main():
    targets = ([Path(a) for a in sys.argv[1:]] or
               [FR / "PROFESSIONAL-REPORT-RU.md", FR / "EXEC-SUMMARY-RU.md"])
    for t in targets:
        p = build(t)
        print(f"{p.name}: {p.stat().st_size:,} bytes", flush=True)


if __name__ == "__main__":
    main()
