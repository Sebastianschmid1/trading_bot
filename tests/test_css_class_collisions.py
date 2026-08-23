"""Guard gegen Klassennamen-Kollisionen zwischen `components.css` und Seiten-Templates (E4).

Hintergrund: Seiten-Templates setzen ihr `<style>` im `{% block content %}` und landen damit
in der Kaskade **nach** `components.css`. Definiert eine Seite eine Klasse, die es dort schon
gibt, gewinnt bei gleicher Spezifität die Seitenregel — lautlos und nur auf dieser einen Seite.

Der konkrete Anlass (2026-08-24): `reports.html` und `lab.html` brachten je eine eigene
`.chip`-Regel mit (Filterpille bzw. Parameteranzeige). `.chip` gehört aber dem Statuschip aus
`components.css`, den `base.html` in der appbar rendert — unter anderem für den **Kill-Switch**.
Auf diesen beiden Seiten hätte die Warnung damit Farbe und Hintergrund verloren, also genau
dann, wenn sie zählt. Aufgelöst über Seitenpräfixe (`.rp-filter`, `.lab-param`).

`base.html` und `components.html` sind ausgenommen: `base.html` definiert die globale Glaswelt
und darf bewusst nachschärfen, `components.html` ist ein Makro-Modul ohne eigenes `<style>`.
"""

import re
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parents[1] / "stockbot" / "web"
_COMPONENTS_CSS = _WEB / "static" / "components.css"
_TEMPLATES = _WEB / "templates"

# base.html hält die globale Glasschicht und darf Komponentenklassen nachschärfen;
# components.html enthält nur Jinja-Makros.
_AUSGENOMMEN = {"base.html", "components.html"}


def _ohne_kommentare(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _klassen_aus_css(css: str) -> set[str]:
    """Klassennamen, die links von einem `{` als Selektor stehen."""
    css = _ohne_kommentare(css)
    out: set[str] = set()
    for block in re.finditer(r"([^{}]+)\{[^{}]*\}", css):
        out |= set(re.findall(r"\.([A-Za-z][\w-]*)", block.group(1)))
    return out


def _seiten_styles() -> dict[str, set[str]]:
    """{Dateiname: Klassen, die das Template in einem <style>-Block selbst definiert}"""
    out: dict[str, set[str]] = {}
    for pfad in sorted(_TEMPLATES.glob("*.html")):
        if pfad.name in _AUSGENOMMEN:
            continue
        text = pfad.read_text(encoding="utf-8")
        styles = "\n".join(re.findall(r"<style>(.*?)</style>", text, flags=re.S))
        if styles.strip():
            out[pfad.name] = _klassen_aus_css(styles)
    return out


def test_components_css_wird_geparst():
    """Schutz vor einem stillschweigend leeren Test (Pfad verschoben, Regex kaputt)."""
    klassen = _klassen_aus_css(_COMPONENTS_CSS.read_text(encoding="utf-8"))
    assert len(klassen) > 20, f"nur {len(klassen)} Klassen gefunden — Parser oder Pfad kaputt?"
    assert "chip" in klassen and "card2" in klassen


def test_seiten_templates_werden_geparst():
    seiten = _seiten_styles()
    assert len(seiten) >= 3, f"nur {len(seiten)} Templates mit <style> — Parser kaputt?"


@pytest.mark.parametrize("template", sorted(_seiten_styles()))
def test_seite_definiert_keine_komponentenklasse_neu(template):
    komponenten = _klassen_aus_css(_COMPONENTS_CSS.read_text(encoding="utf-8"))
    kollision = sorted(_seiten_styles()[template] & komponenten)
    assert not kollision, (
        f"{template} definiert Klassen neu, die components.css schon führt: {kollision}. "
        "Das <style> der Seite steht in der Kaskade später und überschreibt die Komponente "
        "auf dieser Seite. Entweder der Seite einen eigenen Präfix geben (Muster: .rp-filter, "
        ".lab-param, .ck-*) oder die Komponente selbst ändern — aber nicht doppelt führen."
    )
