"""UI-Spiegelung des Quote-Freshness-Gates (Stylekonzept §32.3).

Die Frische-Anzeige ist ein **Sicherheits-Element**, kein Deko-Element: drei sichtbare
Zustaende, und der veraltete Zustand blockiert sichtbar neue Einstiege.

Bewusst ein eigenes, IO-freies Modul und **kein** Patch in ``core/data_quality.py`` —
jenes ist eine reine Gate-Bibliothek ohne UI-Wissen (Labels, Chip-Klassen).

Zwei verschiedene Groessen, deshalb zwei Schwellen
--------------------------------------------------
Das Backend-Gate (``risk.pretrade_check`` → ``check_quote_age``) misst das Alter der
**Ausfuehrungs**-Quote: ``risk_context.quote_context`` holt sie im Moment des Order-
Versuchs frisch. Dieses Modul misst das Alter des **angezeigten** Preises, auf dessen
Basis der Nutzer entscheidet. Beides an dieselbe Schwelle zu haengen war falsch: der
Scan-Cache lebt 10 Minuten, das Gate erlaubt 60 Sekunden — die Seite haette sich rund
eine Minute nach jedem Scan selbst gesperrt, obwohl das Backend die (frische) Quote
anstandslos durchgelassen haette. Die UI haette also systematisch blockiert, was das
Backend erlaubt.

Aufloesung — die Gate-Grenze sitzt an der **fresh**-Kante, nicht an der Block-Kante:

* ``fresh``   ⇔ das Gate wuerde einen Quote dieses Alters akzeptieren. „aktuell" wird
  damit nie fuer etwas behauptet, das das Ausfuehrungs-Gate ablehnen wuerde — die
  Kopplung an ``check_quote_age`` bleibt erhalten und bekommt eine ehrliche Bedeutung.
* ``delayed`` = darueber, aber noch unterhalb der UI-Blockgrenze. Warnt, blockt nicht.
* ``stale``   = ab ``UI_STALE_SECONDS``. Nur hier ``blocks_orders=True``.

``blocks_orders`` betrifft ausschliesslich **neue Einstiege**. Exits werden nie
blockiert — ein blockierter Ausstieg waere gefaehrlicher als ein Einstieg auf altem
Kurs (siehe ``templates/app.html``). Die UI-Blockade bleibt Defense-in-Depth; das
Backend-Gate ist und bleibt der eigentliche Schutz.

``unknown`` vs. ``degraded`` — zwei verschiedene Aussagen (§32.5)
-----------------------------------------------------------------
Beide sind „nicht aktuell belegbar", meinen aber Unterschiedliches und haben deshalb
unterschiedliche Folgen:

* :func:`unknown` — wir kennen das **Alter** nicht, der angezeigte Wert selbst ist
  plausibel (er stammt aus einer erfolgreichen Abfrage, nur ohne Zeitstempel). Warnt,
  blockiert nicht: das Backend-Gate wuerde ohne bekanntes Alter ebenfalls durchlassen.
* :func:`degraded` — die **Datenquelle hat versagt**; der Wert waere ein Ersatzwert
  (Fallback) und damit keine Entscheidungsgrundlage. Warnt **und** sperrt neue
  Einstiege. §32.5 verbietet in diesem Zustand ausdruecklich optimistische
  Schaetzwerte — die betroffene Stelle zeigt „—", keinen Ersatzwert.

Visuell sind die drei Faelle klar getrennt: „Loading" ist ein Skeleton, ``degraded``
ein ``alert2--warning``-Banner, der Fehler-/veraltet-Fall ein ``alert2--danger``-Banner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from stockbot.core.data_quality import check_quote_age
from stockbot.core.market_data import Quote

# Alter des ANGEZEIGTEN Preises, ab dem er keine belastbare Grundlage mehr fuer eine
# Trade-Bestaetigung ist und die UI neue Einstiege sperrt. Bewusst eine eigene, absolute
# UI-Konstante und kein Bruchteil des Gate-Werts: sie misst eine andere Groesse als
# `max_quote_age_seconds` (Entscheidungsgrundlage vs. Ausfuehrungs-Quote) und darf sich
# nicht mit dem Risikoprofil verschieben.
UI_STALE_SECONDS = 180.0

# Fixer Referenzzeitpunkt fuer den Probe-Quote unten. Nur die Differenz zaehlt, deshalb
# ist der konkrete Wert egal — er macht die Funktion aber deterministisch und IO-frei.
_REFERENCE_NOW = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FeedStatus:
    """Dreistufiger Feed-Status (plus expliziter „unbekannt"-Fall) fuer die Templates."""

    state: str                      # "fresh" | "delayed" | "stale" | "unknown" | "degraded"
    label: str                      # sichtbarer Chip-Text (deutsch, sachlich, §25)
    chip_class: str                 # vorhandene Klasse aus components.css
    age_seconds: float | None       # None ⇒ Datenalter nicht bekannt
    blocks_orders: bool             # in "stale"/"degraded" wahr; betrifft NUR Einstiege
    reason: str = ""                # Begruendung fuer den blockierten Fall (sonst "")


def is_stale(age_seconds: float, *, max_quote_age_seconds: float) -> bool:
    """True ⇔ ``data_quality.check_quote_age`` wuerde einen Quote dieses Alters ablehnen.

    Einzige Quelle der Gate-Grenze: der Gate-Check selbst wird mit einem Probe-Quote
    aufgerufen, dessen ``as_of`` genau ``age_seconds`` zurueckliegt. Diese Grenze trennt
    hier ``fresh`` von ``delayed`` (siehe Modul-Docstring) — sie ist NICHT die
    UI-Blockgrenze, das ist :data:`UI_STALE_SECONDS`."""
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


def degraded(detail: str = "Aktuelle Kursdaten sind nicht abrufbar.") -> FeedStatus:
    """Die Datenquelle hat versagt — der angezeigte Wert waere nur ein Ersatzwert (§32.5).

    Abgrenzung zu :func:`unknown`: dort ist der Wert plausibel und nur sein Alter
    unbekannt. Hier gibt es gar keinen belastbaren Wert, deshalb ``blocks_orders=True``
    — ein Einstieg braucht eine Entscheidungsgrundlage, und die existiert gerade nicht.
    Wie ueberall gilt das nur fuer **neue Einstiege**; Exits bleiben bedienbar.

    ``age_seconds`` bleibt ``None``: ein Alter zu nennen wuerde eine Aktualitaet
    behaupten, die es nicht gibt."""
    return FeedStatus(
        # Chip-Text liest sich im Template als „Kursdaten: unsicher"; der Banner-Titel
        # traegt die volle Formulierung „Daten unsicher — …" aus §32.5.
        state="degraded", label="unsicher", chip_class="chip--caution",
        age_seconds=None, blocks_orders=True,
        reason=(f"{detail} Es wird bewusst kein Schätzwert angezeigt, sondern ein Strich. "
                f"Neue Einstiege sind gesperrt, weil dafür gerade keine belastbare "
                f"Entscheidungsgrundlage vorliegt. Bestehende Positionen lassen sich "
                f"weiterhin schließen."),
    )


def evaluate(age_seconds: float | None, *, max_quote_age_seconds: float) -> FeedStatus:
    """Baut den Feed-Status aus dem Alter der angezeigten Kursdaten (§32.3).

    Reihenfolge der Pruefungen ist bewusst „stale zuerst": reicht ein Aufrufer ein
    ``max_quote_age_seconds > UI_STALE_SECONDS`` herein, darf die Staffelung nicht
    kippen — ``fresh`` gewinnt nie ueber ``stale``.

    ``age_seconds=None`` ⇒ :func:`unknown`. Negative Alter (Uhren-Drift) werden auf 0
    geklemmt statt als „aus der Zukunft" angezeigt."""
    if age_seconds is None:
        return unknown()

    age = max(0.0, float(age_seconds))
    if age >= UI_STALE_SECONDS:
        return FeedStatus(
            state="stale", label="veraltet – keine neuen Trades", chip_class="chip--warn",
            age_seconds=age, blocks_orders=True,
            reason=(f"Die angezeigten Kurse sind {age:.0f} s alt "
                    f"(Grenze {UI_STALE_SECONDS:.0f} s) und damit keine belastbare "
                    f"Grundlage für eine Trade-Bestätigung. Neue Einstiege sind gesperrt — "
                    f"bitte die Signale neu anfordern. Bestehende Positionen lassen sich "
                    f"weiterhin schließen."),
        )
    if is_stale(age, max_quote_age_seconds=max_quote_age_seconds):
        # Ueber der Gate-Grenze: „aktuell" waere hier eine Falschaussage, blockiert wird
        # aber noch nicht (die Ausfuehrungs-Quote wird beim Order-Versuch frisch geholt).
        return FeedStatus(
            state="delayed", label=f"verzögert · {age:.0f} s", chip_class="chip--caution",
            age_seconds=age, blocks_orders=False,
        )
    return FeedStatus(
        state="fresh", label="aktuell", chip_class="chip--go",
        age_seconds=age, blocks_orders=False,
    )
