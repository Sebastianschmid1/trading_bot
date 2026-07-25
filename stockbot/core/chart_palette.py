"""Gemeinsame Chart-Farbquelle (Style §32.8): eine Python-Quelle, deren Werte mit
den kanonischen CSS-Tokens in ``stockbot/web/static/tokens.css`` uebereinstimmen.

Hintergrund: Web-Charts (Chart.js) und die server-seitigen Matplotlib-PNGs
(``stockbot/backtest/report.py``) sollen dieselben Farben/Konventionen benutzen
(dunkler Hintergrund, Primaerblau-Hauptkurve, Benchmark in gedaempftem Grau,
kategoriale Palette aus §32.7). CSS und Python koennen keine Datei teilen, also
ist dieses Modul eine **Kopie derselben Hex-Werte** — die Kopplung wird von
``tests/test_chart_palette_parity.py`` festgenagelt (der Test parst tokens.css
und vergleicht). **Beim Aendern einer Farbe IMMER beide Seiten anpassen.**

Kategoriale Palette (§32.7): farbenblind-sicher, maximal 6 Kategorien (danach
"Sonstige" aggregieren), bewusst getrennt von der reservierten Gruen/Rot-Semantik
(``--success``/``--danger``). Aus der dataviz-Methodik abgeleitet und validiert
(Machado-Oliveira-Fernandes-2009-CVD-Simulation auf dunkler Flaeche: schlechtestes
benachbartes Paar ΔE 8.4 bei Ziel ≥ 8; Normalsicht-Boden ΔE 19.3 bei Boden ≥ 15).
Die Reihenfolge ist der CVD-Schutz und darf nicht umsortiert werden.
"""

# ── Chart-Chrome (jeweils eine Kopie des gleichnamigen tokens.css-Wertes) ──────
BACKGROUND = "#0B0D10"   # --bg-base   : dunkler Chart-Hintergrund
PRIMARY = "#5B8CFF"      # --primary   : Hauptkurve (§15.2)
BENCHMARK = "#7F8B99"    # --text-muted: Benchmark/Nulllinie in gedaempftem Grau (§15.2)

# ── Kategoriale Serienpalette --cat-1..6 (§32.7) ───────────────────────────────
CATEGORICAL = [
    "#3987e5",  # --cat-1 blau
    "#d95926",  # --cat-2 orange
    "#199e70",  # --cat-3 aqua
    "#c98500",  # --cat-4 gelb
    "#d55181",  # --cat-5 magenta
    "#9085e9",  # --cat-6 violett
]

# token-Name -> Hex: der maschinelle Vertrag mit tokens.css. Der Paritaets-Test
# parst tokens.css und prueft, dass jeder Eintrag hier bitgleich dort steht — so
# koennen Web und Matplotlib nicht lautlos auseinanderlaufen.
TOKEN_HEX = {
    "--bg-base": BACKGROUND,
    "--primary": PRIMARY,
    "--text-muted": BENCHMARK,
    "--cat-1": CATEGORICAL[0],
    "--cat-2": CATEGORICAL[1],
    "--cat-3": CATEGORICAL[2],
    "--cat-4": CATEGORICAL[3],
    "--cat-5": CATEGORICAL[4],
    "--cat-6": CATEGORICAL[5],
}


def categorical(n: int) -> list[str]:
    """Die ersten ``n`` Serienfarben. Ueber 6 hinaus wird zyklisch wiederholt —
    §32.7 verlangt aber, ab der 7. Kategorie "Sonstige" zu aggregieren statt zu
    zyklen; die Modulo-Logik ist nur ein defensiver Fallback."""
    if not CATEGORICAL:  # pragma: no cover - defensiv
        return []
    return [CATEGORICAL[i % len(CATEGORICAL)] for i in range(n)]
