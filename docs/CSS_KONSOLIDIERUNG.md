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

## Die angebliche Voraussetzung — widerlegt (2026-08-23)

Der Plan verlangte, die Ladereihenfolge auf **`tokens.css` → `liquid-glass.css` → `components.css`**
zu drehen. **Nachgemessen am Stand vom 23.08.2026 ist das wirkungslos** und deshalb gestrichen:

- **0** Regelpaare mit gleicher Spezifität, gemeinsamem Ziel und gemeinsamer Eigenschaft
  (90 Selektoren in `components.css` gegen 71 in `liquid-glass.css`, maschinell verglichen).
- **0** identische Selektoren in beiden Dateien.
- **0** überschneidende Custom Properties zwischen `components.css` (6) und `liquid-glass.css` (63);
  auch `tokens.css` (84) überschneidet sich mit dem Vendor nicht. Einzige Überschneidung
  `--primary` zwischen `tokens.css` und `components.css` — dort ist die Reihenfolge schon heute richtig.

Der Beleg im alten Text (`.lg-body input` 0,1,1 gegen eine Element-Regel 0,0,1) trägt nicht:
bei **unterschiedlicher** Spezifität entscheidet die Spezifität, nicht die Reihenfolge. Die
Reihenfolge bleibt deshalb unverändert — eine Änderung ohne heutigen Anlass wäre reines Risiko.

## Die sieben Etappen

Ein Branch pro Etappe, jede für sich mergebar, jede mit einem Erfolgskriterium, das **ohne Auge**
belegbar ist (grep = 0, gerendertes HTML unverändert, Test war vorher rot).

| # | Inhalt | Erfolgskriterium |
|---|---|---|
| **E0** | ✅ **erledigt 2026-08-23**: toter Code raus (`.table2`, `.select2`, `.textarea2`, `.tabs__tab`, `.tooltip*`, `.text-*`, `.numeric`, `.font-mono`) — je Selektor 0 Verwendungen belegt; `.tabs` blieb (dashboard.html nutzt es). Ladereihenfolge gestrichen, siehe oben. 68 Zeilen weniger, 118 Web-Tests grün. |
| **E1** | `tokens.css` entkernen, `.mode-badge*` nach `components.css` | jedes entfernte Token hat eine Definition in `.lg-body`; Parity-Test grün. **⚠️ Falle vorab geprüft, siehe unten** |
| **E2** | ✅ **erledigt 2026-08-27** (`47b0c92`): `card2*` → `card*`, eine `.card`-Regel in `components.css`, `.card`-Regel aus `base.html:290-294` gelöscht (Pflicht — sonst laufen die Varianten ins Leere, ohne dass ein Test es meldet), Kachel als `.card--tile` an 8 Stellen inkl. **beider JS-Stellen**. `.card--tile`s Basisregel bleibt bewusst dashboard-lokal: in `components.css` **und** dort kollidiert der Guard erneut. `grep card2` = 0 (außer dem Token `--card2` in `base.html:55`, gehört zu E1). |
| **E3** | ✅ **erledigt 2026-08-27** (`e053d8e` + `c183bdf`): war ein **Kontrast-Bugfix**, keine Kosmetik — die Banner lagen auf dem nackten Mesh, **sieben von acht** gerechneten Werten unter 4,5:1, darunter die Feed-Warnungen „Daten unsicher" und „Kursdaten veraltet". Jetzt auf `--lg-glass-solid`, Ton in Rand + eingefärbtem Titel. Vom design-lead live nachgemessen: Fließtext 13,65:1 / 14,36:1, alle vier Titel darüber. Das Icon wurde nach der Sichtprüfung wieder gestrichen — ein für alle Töne gleiches Ausrufezeichen kodiert nichts und stand bei ruhigen Hinweisen falsch. |
| **E4** | ✅ **erledigt 2026-08-24**: `.chip`-Look-alikes umbenannt (`reports.html` → `.rp-filter`, `lab.html` → `.lab-param`), `.tabs`-Doppelbelegung pixelgleich aufgelöst, Guard-Test `tests/test_css_class_collisions.py`. Test gegen den Vorstand belegt rot (3 Kollisionen), jetzt grün; `feed_status.py` unverändert. |
| **E5** | ✅ **erledigt 2026-08-27** (`8f6230b`): `dialog2*` → `dialog*`. **Die Annahme „Tests greifen über IDs" war falsch** — `test_web_style_phases.py:369/461` nennen die Klasse wörtlich; das Mitziehen ist ein Rename, keine gelockerte Assertion. `:461` war nach dem Rename tautologisch (`"dialog" in tag` bei `<dialog …>`) und prüft jetzt `class="dialog"`. |
| **E6** | Buttons (~70 Stellen, 10 Templates) | grep = 0; `test_web_style_phases.py` grün **ohne** gelockerte Assertion |
| **E7** | Felder + Schlussputz, `DESIGN.md` | grep `--space-`/`--radius-`/`input2` = 0 |

**Zusätzlich zur ursprünglichen Serie erledigt (2026-08-27):**

- **Dashboard-Hierarchie** (`a0311c4`) — `.card-hero` ersatzlos entfernt, die neun KPI-Kacheln nach
  Risiko → Status → Performance → Historie umsortiert. Anlass: `PLAN_CHECKLIST.md:695` fordert
  „Gewinn nicht größte Fläche", `dashboard.html:640` machte die P&L-Kachel zum einzigen gesättigten
  Farbblock der Ansicht. Der design-lead hat die Prämisse dabei korrigiert — die Kachel war nie
  *größer*, sie war der einzige Fixationspunkt. Bewusst **keine** Risiko-Kachel als Ersatz: eine
  Anzeige, die 95 % der Zeit „alles in Ordnung" sagt, trainiert das Übersehen; Risiko läuft
  ereignisgetrieben über Kill-Switch-Chip und Warnbanner. Die Verlustfärbung (`cls`) blieb bindend
  unangetastet.
- **CSS-Kommentar-Bug** (`7fc3b94`) — bei der Sichtprüfung gefunden, **vorbestehend und schwer**:
  `base.html:118` schrieb `--bg-surface-*/--bg-elevated` in einen Kommentar; das `*/` beendete ihn
  mittendrin, der Parser verwarf alles bis zum nächsten `;` — und das war das `;` von
  `--text-primary`. Die Rolle wurde nie deklariert und fiel auf den Dunkel-Wert zurück. Im
  Hellmodus stand der Text des **Pflicht-Bestätigungsdialogs bei 1,18:1**, „Abbrechen" bei 1,00:1.
  Guard: `tests/test_css_comment_integrity.py`.

**E6 ist die einzige app-weite optische Änderung** der Serie: Die Buttonform wird auf die Pille
vereinheitlicht (`--lg-r-pill`), `btn--primary` bekommt das violette Gel. Der Design-Lead hat das
ausdrücklich als Änderung angesagt statt es als Aufräumen zu verkaufen — **E6 ist ohne seine
Sichtprüfung nicht abgenommen** (9 Seiten × 2 Themes × 2 Breiten).

## ⚠️ Vorarbeit für E1: „verwaist laut grep" heißt nicht ungenutzt

Nach E0 sehen **39 der 84 Tokens** in `tokens.css` verwaist aus (keine `var(--x)`-Referenz in
CSS, Templates oder Python). **Mindestens 7 davon sind trotzdem in Benutzung**, über einen Pfad,
den ein `var()`-Grep nicht sieht:

`--bg-base`, `--cat-1`, `--cat-2`, `--cat-3`, `--cat-4`, `--cat-5`, `--cat-6`

Sie stehen in `stockbot/core/chart_palette.py::TOKEN_HEX` als **Wertekopie** (CSS und Python
können keine Datei teilen) und werden von `tests/test_chart_palette_parity.py` gegen `tokens.css`
geprüft. Wer sie mechanisch löscht, macht den Parity-Test rot — und die naheliegende „Reparatur"
wäre, den Test anzupassen. Genau das darf nicht passieren.

**Auflage für E1:** Vor dem Löschen eines Tokens gegen `chart_palette.TOKEN_HEX` **und** gegen
`var()`-Referenzen prüfen. Die vollständige Liste der 39 Kandidaten steht nicht hier — sie ist in
einem Durchlauf reproduzierbar und veraltet sonst still.

## Warum E4 ein Bugfix war, keine Kosmetik (2026-08-24)

Die `.chip`-Mehrfachbelegung saß nicht in `components.css`, sondern als Inline-`<style>` in
`reports.html` und `lab.html`. Beide Seiten erben von `base.html`, und deren `<style>` steht im
`{% block content %}` — in der Kaskade also **nach** `components.css`.

`base.html` rendert in der appbar den Statuschip, unter anderem für den **Kill-Switch**
(`chip` + `chip--warn`). Bei gleicher Spezifität (0,1,0) gewinnt die spätere Seitenregel für
`color` und `background`: Auf `/app/reports` und `/app/lab` hätte die Kill-Switch-Warnung ihre
Rotfärbung verloren — also genau auf zwei von neun Seiten und genau dann, wenn sie zählt.

Nebenbefund: `.tabs` stand in `components.css` **und** in `dashboard.html`. Von der
Komponentenregel schlug nur `border-bottom` durch (der Rest wurde überschrieben) — eine
Zusatzlinie, die niemand vorgesehen hatte. Aufgelöst, indem die Linie in `dashboard.html`
übernommen und die Komponentenregel entfernt wurde: optisch unverändert, eine Quelle statt zwei.

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
