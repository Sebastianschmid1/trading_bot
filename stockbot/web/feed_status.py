"""UI-Spiegelung des Quote-Freshness-Gates (Stylekonzept §32.3).

Die Frische-Anzeige ist ein **Sicherheits-Element**, kein Deko-Element: sie hat drei
sichtbare Zustaende an *denselben* Schwellen wie das Backend-Gate, und der veraltete
Zustand blockiert orderrelevante Aktionen sichtbar.

Bewusst ein eigenes, IO-freies Modul und **kein** Patch in ``core/data_quality.py`` —
jenes ist eine reine Gate-Bibliothek ohne UI-Wissen (Labels, Chip-Klassen). Die
Stale-Grenze wird hier nicht nachgebaut, sondern durch einen echten Aufruf von
``data_quality.check_quote_age`` bestimmt; es gibt also keine zweite hartkodierte Zahl,
die vom Gate wegdriften koennte.

Die UI-Blockade ist Defense-in-Depth: sie blockt nur, was ``risk.pretrade_check`` ueber
``check_quote_age`` ohnehin ablehnen wuerde — nie mehr, nie weniger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from stockbot.core.data_quality import check_quote_age
from stockbot.core.market_data import Quote

# Anteil von `max_quote_age_seconds`, ab dem der Feed als „verzoegert" gilt. Bewusst als
# benannte Konstante und als *Bruchteil* des Gate-Werts: aendert sich das Risikoprofil,
# wandert die Vorwarnstufe automatisch mit. 50 % laesst dem Nutzer die halbe Gate-Zeit als
# Vorwarnung, bevor Orders blockiert werden.
DELAYED_FRACTION = 0.5

# Fixer Referenzzeitpunkt fuer den Probe-Quote unten. Nur die Differenz zaehlt, deshalb
# ist der konkrete Wert egal — er macht die Funktion aber deterministisch und IO-frei.
_REFERENCE_NOW = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FeedStatus:
    """Dreistufiger Feed-Status (plus expliziter „unbekannt"-Fall) fuer die Templates."""

    state: str                      # "fresh" | "delayed" | "stale" | "unknown"
    label: str                      # sichtbarer Chip-Text (deutsch, sachlich, §25)
    chip_class: str                 # vorhandene Klasse aus components.css
    age_seconds: float | None       # None ⇒ Datenalter nicht bekannt
    blocks_orders: bool             # nur im Zustand "stale" wahr
    reason: str = ""                # Begruendung fuer den blockierten Fall (sonst "")


def is_stale(age_seconds: float, *, max_quote_age_seconds: float) -> bool:
    """True ⇔ ``data_quality.check_quote_age`` wuerde einen Quote dieses Alters ablehnen.

    Einzige Quelle der Stale-Grenze: der Gate-Check selbst wird mit einem Probe-Quote
    aufgerufen, dessen ``as_of`` genau ``age_seconds`` zurueckliegt."""
    probe = Quote(
        ticker="", price=0.0,
        as_of=_REFERENCE_NOW - timedelta(seconds=age_seconds),
        fetched_at=_REFERENCE_NOW, provider="ui", feed="feed_status_probe",
    )
    decision = check_quote_age(
        probe, max_age_seconds=max_quote_age_seconds, now=_REFERENCE_NOW)
    return not decision.ok


def unknown() -> FeedStatus:
    """Kein belastbarer Zeitstempel vorhanden.

    Wird wie „verzoegert" dargestellt (Vorsicht signalisieren), blockiert aber **nicht**:
    das Backend-Gate wuerde ohne bekanntes Alter ebenfalls nicht ablehnen, und die UI darf
    nichts blocken, was das Backend durchliesse. Es wird bewusst kein Alter geraten."""
    return FeedStatus(
        state="unknown", label="Datenalter unbekannt", chip_class="chip--caution",
        age_seconds=None, blocks_orders=False,
    )


def evaluate(age_seconds: float | None, *, max_quote_age_seconds: float) -> FeedStatus:
    """Baut den Feed-Status aus dem Alter der angezeigten Kursdaten (§32.3).

    ``age_seconds=None`` ⇒ :func:`unknown`. Negative Alter (Uhren-Drift) werden auf 0
    geklemmt statt als „aus der Zukunft" angezeigt."""
    if age_seconds is None:
        return unknown()

    age = max(0.0, float(age_seconds))
    if is_stale(age, max_quote_age_seconds=max_quote_age_seconds):
        return FeedStatus(
            state="stale", label="veraltet – Orders blockiert", chip_class="chip--warn",
            age_seconds=age, blocks_orders=True,
            reason=(f"Die angezeigten Kurse sind {age:.0f} s alt "
                    f"(Grenze {max_quote_age_seconds:.0f} s). Orderrelevante Aktionen sind "
                    f"blockiert — bitte die Signale neu anfordern."),
        )
    if age >= DELAYED_FRACTION * float(max_quote_age_seconds):
        return FeedStatus(
            state="delayed", label=f"verzögert · {age:.0f} s", chip_class="chip--caution",
            age_seconds=age, blocks_orders=False,
        )
    return FeedStatus(
        state="fresh", label="aktuell", chip_class="chip--go",
        age_seconds=age, blocks_orders=False,
    )
