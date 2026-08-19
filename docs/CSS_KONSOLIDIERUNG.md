# CSS-Konsolidierung: zwei Komponentenwelten zu einer

**Stand:** Etappe E0 beauftragt (2026-08-19). Vorgabe vom `design-lead` (Entscheidungs-Modus).

## Das Problem

Zwei Komponentenwelten nebeneinander: `.card`/`button`/`table` (in `base.html`) gegen
`.card2`/`.btn2`/`.table2`/`.alert2` (in `components.css`). `app.html` nutzt beide in derselben
Karte. `.chip` ist dreifach mit unterschiedlicher Bedeutung belegt. `tokens.css` ist die
abgelöste Avionik-Welt und wird weiterhin geladen. Das ist die strukturelle Ursache dafür, dass
die Seiten optisch auseinanderdriften.

## Die Entscheidung

Nicht „Welt A oder B", sondern **getrennt nach Ebene**:

| Ebene | Gewinner |
|---|---|
| Erscheinungsbild | die **Glaswelt** (`base.html`, direkt auf `--lg-*`) |
| Struktur, Varianten, Zustände, a11y | die **`2`-Welt** (`components.css`) |
| Ort | `components.css` als einzige Komponentendatei |
| Namen | **ohne** `2` |

Begründung in Kürze: Die `2`-Welt hat eine **eigene Geometrie** (Radien 6/10/14/18 gegen
`--lg-r-*` 12/18/26/34) und sitzt damit *neben* dem vendorierten System statt darauf — sie
gewinnt deshalb nicht als Erscheinungsbild. Sie hat aber als einzige Disabled-Zustände,
`is-loading` (Doppelklick-Schutz), `aria-invalid`, den Pflicht-Bestätigungsdialog (§18.1),
vier Alert-Tonlagen, `.sr-only` — deshalb gewinnt sie als API.

**Drift-Beleg:** `class="btn2"` erbt auf einem `<button>` das Glasmaterial aus der Element-Regel,
auf einem `<a>` (`error.html`, `index.html`) aber nicht — dieselbe Klasse, zwei Erscheinungsbilder.

## Voraussetzung, ohne die nichts greift

Ladereihenfolge in `base.html`: **`tokens.css` → `liquid-glass.css` → `components.css`**.
Heute steht `components.css` in der Mitte; `.lg-body input` (Spezifität 0,1,1) schlägt sonst
jede Element-Regel in `components.css` (0,0,1).

## Die sieben Etappen

Ein Branch pro Etappe, jede für sich mergebar, jede mit einem Erfolgskriterium, das **ohne Auge**
belegbar ist (grep = 0, gerendertes HTML unverändert, Test war vorher rot).

| # | Inhalt | Erfolgskriterium |
|---|---|---|
| **E0** | Ladereihenfolge + toter Code (`.table2`, `.select2`, `.textarea2`, `.tabs*`, `.tooltip*`, `.text-*`, `.numeric`, `.font-mono`) | grep je Selektor = 0; HTML bis auf die Link-Zeilen identisch |
| **E1** | `tokens.css` entkernen, `.mode-badge*` nach `components.css` | jedes entfernte Token hat eine Definition in `.lg-body`; Parity-Test grün |
| **E2** | Karte: `card2*` → `card*`, `dashboard.html:106` → `.card--tile` | `grep card2` = 0; `app.html` mit nur **einem** Kartenmaterial |
| **E3** | Meldungen: `.flash` + `alert2*` → `.alert*` auf **solider** Fläche | vier Kontrastzahlen ≥ 4,5:1 in beiden Themes im Report |
| **E4** | Chips + Kollisions-Guard (neuer Test) | Test vorher rot, nachher grün; `feed_status.py` unverändert |
| **E5** | Dialoge: `dialog2` → `dialog` (38 Stellen, reiner Rename) | §18.1-Tests unverändert grün (greifen über IDs) |
| **E6** | Buttons (~70 Stellen, 10 Templates) | grep = 0; `test_web_style_phases.py` grün **ohne** gelockerte Assertion |
| **E7** | Felder + Schlussputz, `DESIGN.md` | grep `--space-`/`--radius-`/`input2` = 0 |

**E6 ist die einzige app-weite optische Änderung** der Serie: Die Buttonform wird auf die Pille
vereinheitlicht (`--lg-r-pill`), `btn--primary` bekommt das violette Gel. Der Design-Lead hat das
ausdrücklich als Änderung angesagt statt es als Aufräumen zu verkaufen — **E6 ist ohne seine
Sichtprüfung nicht abgenommen** (9 Seiten × 2 Themes × 2 Breiten).

## `.chip` — Namensauflösung

`.chip` gehört dem **Statuschip** (`components.css`), weil `feed_status.py` die Klassennamen aus
Python liefert (`chip--go|--caution|--warn`) und die Tonpaare die Kontrast-Härtung tragen. Jede
andere Vergabe erzwänge einen Python-Diff für eine reine Umbenennung.

Die Look-alikes bekommen ein Seitenpräfix (Muster `ck-*` existiert schon):
`reports.html` Filterpille → `.rp-filter`, `lab.html` Parameteranzeige → `.lab-param`.

## Nicht anfassen

`liquid-glass.css` (vendoriert) · `--cat-1..6`, `chart_palette.py`, Pfad und Name von
`tokens.css` (Parity-Test) · `feed_status.py` und jeder Python-Pfad — **muss ein Worker Python
anfassen, hat er das Mapping verlassen: stoppen und zurückgeben** · die Kontrast-Härtung
(`--tone-*`, `--status-ink-*`, `--badge-teal-ink`, `--money-*`) und die Dark-Blöcke · die
`ck-*`-Grammatik und das Chart-JS im Dashboard · das Verhalten des Bestätigungsdialogs (nur
Klassennamen, nie Logik) · keine Assertion lockern.

**Bewusst ausgelassen:** die dreifach kopierten Seitenutilities `.empty`, `.scroll`, `.searchbox`
— dieselbe Krankheit, aber eigener Schnitt, sonst platzt die Reviewgrenze.
