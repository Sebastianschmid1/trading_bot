"""Guard gegen ein `*/`, das mitten im CSS-Kommentartext steht.

Anlass (2026-08-27, design-lead-Abnahme): `base.html` erklaerte in einem Kommentar die
Rollen `--bg-surface-*/--bg-elevated`. Die Zeichenfolge `*/` darin beendete den Kommentar
mittendrin; der Parser verwarf den Rest bis zum naechsten `;` — und das war das `;` der
direkt folgenden Deklaration `--text-primary`. Folge: `--text-primary` wurde auf `.lg-body`
nie gesetzt und fiel auf den Dunkel-Wert aus `tokens.css` zurueck. Im Hellmodus stand der
Text des Pflicht-Bestaetigungsdialogs damit bei 1,18:1 statt 11,80:1.

Der Fehler ist unsichtbar, solange niemand den Hellmodus prueft, und die Schreibweise
`--bg-surface-*` ist naheliegend genug, um wiederzukehren. Geprueft wird deshalb genau
dieses Muster — ein `*/`, dem unmittelbar ein Wortzeichen vorausgeht, statt des sonst
ueblichen Leerzeichens.
"""
import re
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1] / "stockbot" / "web"
DATEIEN = sorted((WURZEL / "templates").glob("*.html")) + sorted((WURZEL / "static").glob("*.css"))

#: `--bg-surface-*/` trifft zu, ` */` (regulaeres Kommentarende) nicht.
VERSEHENTLICHES_ENDE = re.compile(r"[A-Za-z0-9_-]\*/")
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)


def _css_teile(datei: Path) -> list[str]:
    """Nur echte CSS-Kontexte. In JS-Zeilenkommentaren (`// … --lg-*/--money-*`) ist ein
    `*/` harmlos, weil `//` bis Zeilenende gilt — dort darf der Guard nicht anschlagen."""
    text = datei.read_text(encoding="utf-8")
    return [text] if datei.suffix == ".css" else STYLE_BLOCK.findall(text)


@pytest.mark.parametrize("datei", DATEIEN, ids=lambda p: p.name)
def test_kein_versehentliches_kommentarende(datei):
    treffer = [m.group(0) for teil in _css_teile(datei)
               for m in VERSEHENTLICHES_ENDE.finditer(teil)]
    assert not treffer, (
        f"{datei.name}: `*/` klebt in einem CSS-Kontext an einem Wortzeichen ({treffer}) — das "
        "beendet den Kommentar vorzeitig und verschluckt die naechste Deklaration. "
        "Siehe Modul-Docstring."
    )


def test_text_primary_steht_ausserhalb_eines_kommentars():
    """Die Rolle `--text-primary` muss im `<style>` von base.html tatsaechlich ankommen.

    Der urspruengliche Fehler wurde hier sichtbar: die Deklaration stand da, wurde vom
    Parser aber zusammen mit dem kaputten Kommentar verworfen.
    """
    text = (WURZEL / "templates" / "base.html").read_text(encoding="utf-8")
    kopf, _, rest = text.partition("--text-primary:")
    assert rest, "base.html deklariert --text-primary nicht mehr"
    assert kopf.count("/*") == kopf.count("*/"), (
        "Vor --text-primary sind Kommentar-Anfang und -Ende unbalanciert — die Deklaration "
        "faellt damit dem Parser zum Opfer (siehe Modul-Docstring)."
    )
