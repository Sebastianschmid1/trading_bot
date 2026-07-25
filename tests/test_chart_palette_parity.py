"""Parity-Test Style §32.8: die Python-Chart-Palette (core/chart_palette.py) und die
CSS-Tokens (web/static/tokens.css) muessen dieselben Hex-Werte tragen.

CSS und Python koennen keine Datei teilen; dieses Modul haelt eine Kopie derselben
Werte. Der Test parst tokens.css und vergleicht — so kann die Kopplung nicht lautlos
auseinanderlaufen (genau der Sinn von §32.8)."""

import re
from pathlib import Path

from stockbot.core import chart_palette

_TOKENS_CSS = Path(__file__).resolve().parents[1] / "stockbot" / "web" / "static" / "tokens.css"
# --name: <wert>;  (Kommentare/Trailing beliebig)
_DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def _parse_tokens() -> dict[str, str]:
    text = _TOKENS_CSS.read_text(encoding="utf-8")
    return {name: value.strip() for name, value in _DECL.findall(text)}


def test_tokens_css_exists_and_defines_cat_palette():
    tokens = _parse_tokens()
    for i in range(1, 7):
        assert f"--cat-{i}" in tokens, f"--cat-{i} fehlt in tokens.css"


def test_python_palette_matches_css_tokens():
    """Jeder Eintrag in chart_palette.TOKEN_HEX steht bitgleich in tokens.css."""
    tokens = _parse_tokens()
    for name, py_hex in chart_palette.TOKEN_HEX.items():
        assert name in tokens, f"{name} fehlt in tokens.css"
        assert tokens[name].lower() == py_hex.lower(), (
            f"{name}: tokens.css={tokens[name]!r} != chart_palette={py_hex!r}"
        )


def test_categorical_order_matches_cat_tokens():
    """CATEGORICAL[0..5] == --cat-1..6 in exakt dieser Reihenfolge (CVD-Schutz)."""
    tokens = _parse_tokens()
    for idx, py_hex in enumerate(chart_palette.CATEGORICAL, start=1):
        assert tokens[f"--cat-{idx}"].lower() == py_hex.lower(), (
            f"--cat-{idx}: tokens.css={tokens[f'--cat-{idx}']!r} != CATEGORICAL[{idx-1}]={py_hex!r}"
        )


def test_categorical_max_six_and_distinct():
    """§32.7: maximal 6 Kategorien, alle unterscheidbar (keine Duplikate)."""
    assert len(chart_palette.CATEGORICAL) == 6
    assert len({c.lower() for c in chart_palette.CATEGORICAL}) == 6


def test_categorical_separate_from_semantic_green_red():
    """§32.7: kategoriale Palette getrennt von der reservierten Gruen/Rot-Semantik."""
    tokens = _parse_tokens()
    reserved = {tokens.get("--success", "").lower(), tokens.get("--danger", "").lower()}
    reserved.discard("")
    for c in chart_palette.CATEGORICAL:
        assert c.lower() not in reserved, f"{c} kollidiert mit --success/--danger"


def test_categorical_helper_wraps():
    assert chart_palette.categorical(3) == chart_palette.CATEGORICAL[:3]
    assert chart_palette.categorical(7)[6] == chart_palette.CATEGORICAL[0]
