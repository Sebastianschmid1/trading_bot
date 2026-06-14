#!/usr/bin/env python3
"""
Wandelt ein Markdown-Report (+ verlinkte PNGs) in ein PDF — nur mit matplotlib/PIL,
also OHNE zusätzliche Abhängigkeiten (kein pandoc/weasyprint/wkhtmltopdf nötig).

Text wird als A4-Textseiten gesetzt (Überschriften, Absätze, Tabellen in Monospace),
verlinkte Bilder (![alt](datei.png)) kommen als eigene Bildseite ins PDF.

Lauf (vom Repo-Root):
    python tools/report_to_pdf.py                                   # docs/STRATEGIEN_REPORT.md → .pdf
    python tools/report_to_pdf.py docs/STRATEGIEN_REPORT.md out.pdf
"""

import os
import re
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from PIL import Image

# Zeichen, die DejaVu (matplotlib-Standardfont) nicht sauber rendert → entfernen (Emojis/Symbole).
_EMOJI = re.compile("[←-⇿⌀-➿─-◿⬀-⯿️\U0001F000-\U0001FAFF]")


def _demark(s: str) -> str:
    """Inline-Markdown (fett/Code) und nicht renderbare Symbole entfernen."""
    return _EMOJI.sub("", s.replace("**", "").replace("`", "")).rstrip()


def _table_lines(tbl: list[str]) -> list[str]:
    """Markdown-Tabelle → ausgerichtete Monospace-Zeilen (Trennzeile entfällt)."""
    rows = [[_demark(c).strip() for c in r.strip().strip("|").split("|")] for r in tbl]
    rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]   # |---|-Zeile raus
    if not rows:
        return []
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    widths = [max(len(r[k]) for r in rows) for k in range(ncol)]
    return ["  ".join(c.ljust(widths[k]) for k, c in enumerate(r)) for r in rows]


def _blocks(md: str):
    """Markdown in einfache Blöcke zerlegen: img | table | h1/h2/h3 | quote | p | space."""
    raw = md.splitlines()
    out, i = [], 0
    while i < len(raw):
        ln = raw[i]
        m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", ln.strip())
        if m:
            out.append(("img", m.group(1))); i += 1; continue
        if ln.startswith("|"):
            tbl = []
            while i < len(raw) and raw[i].startswith("|"):
                tbl.append(raw[i]); i += 1
            out.append(("table", tbl)); continue
        if ln.startswith("### "):
            out.append(("h3", _demark(ln[4:])))
        elif ln.startswith("## "):
            out.append(("h2", _demark(ln[3:])))
        elif ln.startswith("# "):
            out.append(("h1", _demark(ln[2:])))
        elif ln.startswith(">"):
            out.append(("quote", _demark(ln.lstrip("> "))))
        elif ln.strip() == "":
            out.append(("space", ""))
        else:
            out.append(("p", _demark(ln)))
        i += 1
    return out


def md_to_pdf(md_path: str, pdf_path: str) -> str:
    base = os.path.dirname(os.path.abspath(md_path))
    with open(md_path, encoding="utf-8") as f:
        blocks = _blocks(f.read())

    W, H = 8.27, 11.69          # A4 Hochformat (Zoll)
    LEFT, TOP, BOTTOM = 0.07, 0.95, 0.06
    LH = 0.0150                 # Zeilenhöhe (Bruchteil der Seite) für 9pt

    pp = PdfPages(pdf_path)
    state = {"fig": None, "y": 0.0}

    def new_page():
        if state["fig"] is not None:
            pp.savefig(state["fig"]); plt.close(state["fig"])
        state["fig"] = plt.figure(figsize=(W, H)); state["y"] = TOP

    def emit(text, size=9, weight="normal", style="normal", mono=False, gap=0.0):
        if state["y"] <= BOTTOM:
            new_page()
        state["fig"].text(LEFT, state["y"], text, fontsize=size, fontweight=weight,
                          fontstyle=style, va="top",
                          family=("monospace" if mono else "sans-serif"))
        state["y"] -= LH * (size / 9.0) + gap

    new_page()
    for kind, payload in blocks:
        if kind == "img":
            path = payload if os.path.isabs(payload) else os.path.join(base, payload)
            if os.path.exists(path):
                if state["fig"] is not None:
                    pp.savefig(state["fig"]); plt.close(state["fig"]); state["fig"] = None
                fig = plt.figure(figsize=(W, H))
                ax = fig.add_axes([0.04, 0.04, 0.92, 0.92]); ax.axis("off")
                ax.imshow(Image.open(path))
                pp.savefig(fig); plt.close(fig)
                new_page()
        elif kind == "h1":
            emit(payload, size=19, weight="bold", gap=0.012)
        elif kind == "h2":
            state["y"] -= 0.012; emit(payload, size=14, weight="bold", gap=0.007)
        elif kind == "h3":
            state["y"] -= 0.007; emit(payload, size=11, weight="bold", gap=0.004)
        elif kind == "quote":
            for w in textwrap.wrap(payload, 105) or [""]:
                emit(w, size=8, style="italic")
        elif kind == "p":
            for w in textwrap.wrap(payload, 100) or [""]:
                emit(w, size=9)
        elif kind == "table":
            for tl in _table_lines(payload):
                emit(tl, size=8, mono=True)
            state["y"] -= 0.004
        elif kind == "space":
            state["y"] -= 0.006

    if state["fig"] is not None:
        pp.savefig(state["fig"]); plt.close(state["fig"])
    pp.close()
    return pdf_path


def main():
    md = sys.argv[1] if len(sys.argv) > 1 else "docs/STRATEGIEN_REPORT.md"
    pdf = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(md)[0] + ".pdf"
    out = md_to_pdf(md, pdf)
    print(f"PDF geschrieben: {out}")


if __name__ == "__main__":
    main()
