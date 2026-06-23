"""
Log-Zeilen nach Zeitraum (und optional Level) filtern — reine, testbare Logik.

Die Bot-Logzeilen beginnen mit `YYYY-MM-DD HH:MM:SS,mmm [LEVEL] …` (Format aus bot.py).
Mehrzeilige Einträge (z. B. Tracebacks) haben in den Folgezeilen KEINEN eigenen Zeitstempel —
diese erben die Entscheidung der vorangehenden Zeile, damit sie zusammenbleiben.
"""

import re

# Führender Zeitstempel + optionales Level, z. B. "2026-06-23 08:47:55,123 [INFO] ..."
_LINE_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?\s+(?:\[(?P<level>[A-Z]+)\])?")


def _norm_bound(value: str | None, *, end: bool) -> str | None:
    """Grenze normalisieren: reines Datum (len 10) → Tagesanfang/-ende, sonst unverändert."""
    if not value:
        return None
    value = value.strip().replace("T", " ")
    if len(value) == 10:                     # nur 'YYYY-MM-DD'
        return value + (" 23:59:59" if end else " 00:00:00")
    return value


def filter_log_lines(lines, ts_from: str | None = None, ts_to: str | None = None,
                     level: str | None = None) -> list[str]:
    """Behält Logzeilen, deren Zeitstempel in [ts_from, ts_to] liegt (Stringvergleich auf dem
    sortierbaren ISO-Format) und — falls `level` gesetzt — dem Level entsprechen.

    - `ts_from`/`ts_to`: 'YYYY-MM-DD' oder 'YYYY-MM-DD HH:MM:SS' (UTC); None = keine Grenze.
    - `level`: z. B. 'ERROR'/'WARNING'; None = alle Level.
    - Folgezeilen ohne eigenen Zeitstempel folgen der Behavailung der vorigen Zeile.
    """
    lo = _norm_bound(ts_from, end=False)
    hi = _norm_bound(ts_to, end=True)
    want_level = (level or "").strip().upper() or None

    out: list[str] = []
    keep_prev = False
    for raw in lines:
        line = raw.rstrip("\n")
        m = _LINE_RE.match(line)
        if not m:
            if keep_prev:               # Folgezeile (Traceback) → mit voriger Zeile mitnehmen
                out.append(line)
            continue
        ts = m.group("ts")
        keep = True
        if lo and ts < lo:
            keep = False
        if hi and ts > hi:
            keep = False
        if keep and want_level and (m.group("level") or "") != want_level:
            keep = False
        keep_prev = keep
        if keep:
            out.append(line)
    return out
