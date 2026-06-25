#!/usr/bin/env python3
"""Claude-Code-Konversation (Kontext) als JSON oder Markdown exportieren.

Liest ein Claude-Code-Transkript (``*.jsonl`` aus ``~/.claude/projects/<slug>/``)
und schreibt eine bereinigte, gut lesbare Fassung:

* ``--format json`` : strukturierte Liste der Turns (role, ts, blocks).
* ``--format md``   : menschenlesbares Markdown-Transkript.
* ``--format both`` : beides (Standard).

Ohne ``--input`` wird automatisch das **neueste** Transkript des aktuellen
Projekts (abgeleitet aus dem cwd) genommen – der Export "läuft einfach".

Beispiele:
    python tools/export_context.py                       # neuestes, both -> docs/
    python tools/export_context.py --format md           # nur Markdown
    python tools/export_context.py --input pfad.jsonl --out-dir .
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

# Meta-Zeilen, die keine echten Gesprächs-Turns sind und übersprungen werden.
_SKIP_TYPES = {
    "queue-operation", "attachment", "file-history-snapshot",
    "ai-title", "last-prompt", "mode",
}
# Block-Typen, die als Tool-Ausgabe gelten (in user-Messages eingebettet).
_MAX_TOOL_CHARS_MD = 1500  # Tool-Ergebnisse im Markdown kürzen


# --------------------------------------------------------------------------- #
# Transkript finden / laden
# --------------------------------------------------------------------------- #
def _project_slug(cwd: Path) -> str:
    """Claude-Code-Projektordnername: Pfad mit '-' statt Trenner/':' ."""
    s = str(cwd)
    return re.sub(r"[\\/:]+", "-", s).strip("-")


def find_latest_transcript() -> Path | None:
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        return None
    slug = _project_slug(Path.cwd())
    candidates: list[Path] = []
    proj = base / slug
    if proj.exists():
        candidates = list(proj.glob("*.jsonl"))
    if not candidates:  # Fallback: irgendein neuestes Transkript
        candidates = list(base.glob("*/*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_records(path: Path) -> list[dict]:
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# --------------------------------------------------------------------------- #
# Normalisieren: roh -> Turns mit Blöcken
# --------------------------------------------------------------------------- #
def _norm_content(content) -> list[dict]:
    """Message-Content (str | list) -> Liste einheitlicher Blöcke."""
    blocks: list[dict] = []
    if isinstance(content, str):
        if content.strip():
            blocks.append({"kind": "text", "text": content})
        return blocks
    if not isinstance(content, list):
        return blocks
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            blocks.append({"kind": "text", "text": b.get("text", "")})
        elif t == "thinking":
            blocks.append({"kind": "thinking", "text": b.get("thinking", "")})
        elif t == "tool_use":
            blocks.append({
                "kind": "tool_use",
                "name": b.get("name", ""),
                "input": b.get("input", {}),
                "id": b.get("id", ""),
            })
        elif t == "tool_result":
            blocks.append({
                "kind": "tool_result",
                "tool_use_id": b.get("tool_use_id", ""),
                "content": _flatten_tool_result(b.get("content")),
                "is_error": bool(b.get("is_error", False)),
            })
        elif t == "image":
            blocks.append({"kind": "image", "text": "[image]"})
    return blocks


def _flatten_tool_result(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "image":
                    parts.append("[image]")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def build_turns(records: list[dict], include_sidechain: bool) -> list[dict]:
    turns: list[dict] = []
    for o in records:
        if o.get("type") in _SKIP_TYPES:
            continue
        if o.get("type") not in ("user", "assistant"):
            continue
        if o.get("isSidechain") and not include_sidechain:
            continue
        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        blocks = _norm_content(msg.get("content"))
        if not blocks:
            continue
        turns.append({
            "role": msg.get("role", o.get("type")),
            "timestamp": o.get("timestamp", ""),
            "sidechain": bool(o.get("isSidechain")),
            "blocks": blocks,
        })
    return turns


def meta_of(records: list[dict], path: Path) -> dict:
    title = ""
    cwd = ""
    branch = ""
    version = ""
    first_ts = ""
    last_ts = ""
    for o in records:
        if o.get("type") == "ai-title" and o.get("aiTitle"):
            title = o["aiTitle"]
        cwd = o.get("cwd", cwd)
        branch = o.get("gitBranch", branch)
        version = o.get("version", version)
        ts = o.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
    return {
        "title": title or path.stem,
        "source": str(path),
        "cwd": cwd,
        "git_branch": branch,
        "claude_version": version,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
    }


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
def to_json(meta: dict, turns: list[dict]) -> str:
    return json.dumps(
        {"meta": meta, "turn_count": len(turns), "turns": turns},
        ensure_ascii=False, indent=2,
    )


def _short_ts(ts: str) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def _truncate(text: str, limit: int) -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… [gekürzt, {len(text) - limit} Zeichen]"


def to_markdown(meta: dict, turns: list[dict], max_tool: int = _MAX_TOOL_CHARS_MD) -> str:
    L: list[str] = []
    L.append(f"# {meta['title']}")
    L.append("")
    L.append(f"- **Quelle:** `{meta['source']}`")
    if meta["cwd"]:
        L.append(f"- **Arbeitsverzeichnis:** `{meta['cwd']}`")
    if meta["git_branch"]:
        L.append(f"- **Git-Branch:** `{meta['git_branch']}`")
    rng = " → ".join(x for x in (_short_ts(meta["first_timestamp"]),
                                 _short_ts(meta["last_timestamp"])) if x)
    if rng:
        L.append(f"- **Zeitraum:** {rng}")
    L.append(f"- **Turns:** {len(turns)}")
    L.append("")
    L.append("---")
    L.append("")

    for t in turns:
        who = "🧑 User" if t["role"] == "user" else "🤖 Claude"
        if t["sidechain"]:
            who += " (Subagent)"
        ts = _short_ts(t["timestamp"])
        head = f"## {who}" + (f"  ·  _{ts}_" if ts else "")
        L.append(head)
        L.append("")
        for b in t["blocks"]:
            k = b["kind"]
            if k == "text":
                L.append(b["text"].rstrip())
                L.append("")
            elif k == "thinking":
                L.append("<details><summary>💭 Thinking</summary>")
                L.append("")
                L.append(_truncate(b["text"], max_tool))
                L.append("")
                L.append("</details>")
                L.append("")
            elif k == "tool_use":
                inp = json.dumps(b["input"], ensure_ascii=False, indent=2)
                inp = _truncate(inp, max_tool)
                L.append(f"**🔧 Tool → `{b['name']}`**")
                L.append("")
                L.append("```json")
                L.append(inp)
                L.append("```")
                L.append("")
            elif k == "tool_result":
                tag = "⚠️ Tool-Fehler" if b["is_error"] else "📤 Tool-Ergebnis"
                L.append(f"**{tag}**")
                L.append("")
                L.append("```")
                L.append(_truncate(b["content"], max_tool))
                L.append("```")
                L.append("")
            elif k == "image":
                L.append("_[Bild]_")
                L.append("")
        L.append("---")
        L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="Pfad zum .jsonl-Transkript (Standard: neuestes des Projekts).")
    ap.add_argument("--format", choices=["json", "md", "both"], default="both")
    ap.add_argument("--out-dir", default="docs", help="Zielordner (Standard: docs).")
    ap.add_argument("--stem", help="Dateiname-Basis (Standard: context_export_<stamp>).")
    ap.add_argument("--include-sidechain", action="store_true",
                    help="Subagent-/Sidechain-Turns mit aufnehmen.")
    ap.add_argument("--max-tool-chars", type=int, default=_MAX_TOOL_CHARS_MD,
                    help="Max. Zeichen je Tool-/Thinking-Block im Markdown.")
    a = ap.parse_args()

    path = Path(a.input) if a.input else find_latest_transcript()
    if not path or not path.exists():
        print("Kein Transkript gefunden. Mit --input einen .jsonl-Pfad angeben.")
        return 1

    records = load_records(path)
    meta = meta_of(records, path)
    turns = build_turns(records, include_sidechain=a.include_sidechain)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = a.stem or f"context_export_{stamp}"

    written: list[Path] = []
    if a.format in ("json", "both"):
        p = out_dir / f"{stem}.json"
        p.write_text(to_json(meta, turns), encoding="utf-8")
        written.append(p)
    if a.format in ("md", "both"):
        p = out_dir / f"{stem}.md"
        p.write_text(to_markdown(meta, turns, a.max_tool_chars), encoding="utf-8")
        written.append(p)

    print(f"Transkript: {path}")
    print(f"Turns:      {len(turns)} (von {len(records)} Roh-Zeilen)")
    for p in written:
        print(f"Export:     {p}  ({p.stat().st_size:,} Bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
