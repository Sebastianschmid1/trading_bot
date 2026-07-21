# Stylekonzept: Trading Research & Execution Assistant

**Version:** 1.1  
**Stand:** 21. Juli 2026 (Audit-Nachtrag, s. §32) · v1.0 vom 11. Juli 2026  
**Designrichtung:** modern, reduziert, professionell, Dark Mode  
**Ziel:** Einheitliches visuelles und interaktives Design für Web-App, Dashboard und Telegram-Kommunikation

**Kanonische Quelle der Tokens:** `stockbot/web/static/tokens.css` (in W7 1:1 aus §27 umgesetzt).
Dieses Dokument ist die **Begründung/Spezifikation**; bei Abweichung gewinnt `tokens.css` und das
Dokument wird nachgezogen. Änderungen an Tokens erfordern einen Eintrag im Changelog (§32).

---

## 1. Designziel

Die Anwendung soll wie ein professionelles Analyse- und Kontrollwerkzeug wirken – nicht wie ein aggressives Krypto-Casino, eine Gaming-App oder ein überladenes Trading-Terminal.

Die visuelle Sprache ist:

- modern,
- ruhig,
- technisch,
- hochwertig,
- vertrauenswürdig,
- datenorientiert,
- und bewusst reduziert.

Das Interface stellt Risiko, Status und Handlungsfolgen klar dar. Gewinne dürfen visuell nicht überbetont werden. Verluste, Warnungen und blockierte Aktionen müssen mindestens ebenso deutlich sichtbar sein.

### Leitidee

> Weniger visuelle Reize, mehr Klarheit über Daten, Risiko und nächste Aktion.

---

## 2. Designprinzipien

## 2.1 Klarheit vor Dekoration

Jedes Element benötigt einen erkennbaren Zweck.

Vermeiden:

- unnötige Verläufe,
- starke Neonfarben,
- permanente Glow-Effekte,
- übermäßige Schatten,
- dekorative Charts ohne Aussage,
- unnötige Animationen,
- zu viele gleichzeitig sichtbare Kennzahlen.

Bevorzugen:

- klare Informationshierarchie,
- ruhige Flächen,
- konsistente Abstände,
- deutliche Zustände,
- fokussierte Aktionen,
- gut lesbare Daten.

## 2.2 Risiko vor Rendite

Das Interface zeigt bei jedem Trade zuerst:

- maximales geplantes Risiko,
- Positionsgröße,
- Stop,
- Ablaufzeit,
- Modus,
- und erst danach mögliche Zielwerte.

Grün wird nicht als allgemeine Standardfarbe verwendet. Es bleibt für positive oder bestätigte Zustände reserviert.

## 2.3 Progressive Offenlegung

Die wichtigsten Informationen sind sofort sichtbar. Details erscheinen erst bei Bedarf.

Beispiel Signalkarte:

1. Ticker, Strategie und Status,
2. Entry, Stop, Risiko und Ablaufzeit,
3. Begründung und Marktdaten,
4. technische Details und historische Kennzahlen.

## 2.4 Ein Interface, mehrere Modi

Backtest, Shadow, Paper und Live müssen visuell eindeutig unterscheidbar sein.

Der Nutzer darf niemals unsicher sein, ob eine Aktion echtes Geld betrifft.

## 2.5 Konsistenz

Gleiche Informationen erhalten überall dieselbe:

- Farbe,
- Bezeichnung,
- Position,
- Einheit,
- Formatierung,
- und Interaktion.

## 2.6 Ruhiger Dark Mode

Der Dark Mode verwendet kein reines Schwarz als Hauptfläche. Mehrere dunkle Graustufen erzeugen Tiefe, ohne hohe Kontraste oder visuelle Härte.

---

## 3. Markenwirkung

Die Marke soll folgende Eigenschaften vermitteln:

- präzise,
- kontrolliert,
- nüchtern,
- zuverlässig,
- intelligent,
- transparent.

### Nicht gewünschte Wirkung

- verspielt,
- luxuriös-goldfarben,
- aggressiv,
- spekulativ,
- „schnell reich werden“,
- futuristisch um jeden Preis,
- übertrieben KI-orientiert.

### Visuelle Referenzrichtung

Die Gestaltung orientiert sich eher an:

- moderner B2B-SaaS-Software,
- professionellen Entwicklerwerkzeugen,
- hochwertigen Finanz-Dashboards,
- klaren Analyseplattformen.

Nicht an:

- Casino-Oberflächen,
- Meme-Trading-Apps,
- Cyberpunk-Neon,
- stark gamifizierten Broker-Apps.

---

## 4. Farbkonzept

## 4.1 Grundpalette

### Hintergrundfarben

| Token | Hex | Verwendung |
|---|---:|---|
| `--bg-base` | `#0B0D10` | Haupt-Hintergrund |
| `--bg-surface-1` | `#11151A` | Navigation, größere Flächen |
| `--bg-surface-2` | `#171C22` | Karten, Panels |
| `--bg-surface-3` | `#1D242C` | aktive oder hervorgehobene Bereiche |
| `--bg-elevated` | `#222A33` | Dropdowns, Dialoge, Popover |
| `--bg-hover` | `#252E38` | Hover-Zustand |
| `--bg-selected` | `#263242` | ausgewählte Navigation oder Tabelle |

Die Flächen sollen sich hauptsächlich durch Helligkeit, nicht durch starke Rahmen oder Schatten unterscheiden.

## 4.2 Textfarben

| Token | Hex | Verwendung |
|---|---:|---|
| `--text-primary` | `#F4F7FA` | Überschriften, Primärwerte |
| `--text-secondary` | `#B5C0CC` | Beschreibungen, Labels |
| `--text-muted` | `#7F8B99` | Metadaten, Zeitangaben |
| `--text-disabled` | `#56616D` | deaktivierte Inhalte |
| `--text-inverse` | `#0B0D10` | Text auf hellen Akzentflächen |

Text mit hoher Bedeutung verwendet niemals gedämpfte Farben.

## 4.3 Primärfarbe

| Token | Hex | Verwendung |
|---|---:|---|
| `--primary` | `#5B8CFF` | Hauptaktionen, Links, Fokus |
| `--primary-hover` | `#74A0FF` | Hover |
| `--primary-active` | `#4779E8` | aktiver Zustand |
| `--primary-soft` | `rgba(91, 140, 255, 0.14)` | ausgewählte Flächen |
| `--primary-border` | `rgba(91, 140, 255, 0.38)` | Akzentrahmen |

Die Primärfarbe ist ein ruhiges, klares Blau. Sie signalisiert Aktion und Navigation, nicht Gewinn.

## 4.4 Semantische Farben

### Erfolg und positiver Status

| Token | Hex |
|---|---:|
| `--success` | `#35C98F` |
| `--success-soft` | `rgba(53, 201, 143, 0.14)` |
| `--success-border` | `rgba(53, 201, 143, 0.36)` |

Verwendung:

- Order gefüllt,
- Verbindung aktiv,
- Prüfung bestanden,
- positiver P&L-Wert.

### Risiko und Fehler

| Token | Hex |
|---|---:|
| `--danger` | `#FF667A` |
| `--danger-hover` | `#FF7C8C` |
| `--danger-soft` | `rgba(255, 102, 122, 0.14)` |
| `--danger-border` | `rgba(255, 102, 122, 0.38)` |

Verwendung:

- Live-Risiko,
- Verlust,
- Orderfehler,
- Stop,
- Kill-Switch,
- destruktive Aktion.

### Warnung

| Token | Hex |
|---|---:|
| `--warning` | `#F2B84B` |
| `--warning-soft` | `rgba(242, 184, 75, 0.14)` |
| `--warning-border` | `rgba(242, 184, 75, 0.38)` |

Verwendung:

- Quote veraltet,
- Signal läuft bald ab,
- teilweise Ausführung,
- Risikogrenze fast erreicht.

### Information

| Token | Hex |
|---|---:|
| `--info` | `#66B7FF` |
| `--info-soft` | `rgba(102, 183, 255, 0.13)` |

## 4.5 Rahmen und Trennlinien

| Token | Hex |
|---|---:|
| `--border-subtle` | `#242B33` |
| `--border-default` | `#303945` |
| `--border-strong` | `#45515F` |
| `--focus-ring` | `rgba(91, 140, 255, 0.55)` |

Rahmen werden sparsam verwendet. Abstände und Flächenwechsel sollen die Hauptstruktur erzeugen.

---

## 5. Modusfarben

Die Betriebsmodi erhalten feste Farben und Labels.

| Modus | Farbe | Bedeutung |
|---|---:|---|
| Backtest | `#9B8CFF` | historische Simulation |
| Shadow | `#66B7FF` | Live-Daten, keine Order |
| Paper | `#F2B84B` | simulierte Brokerorder |
| Live | `#FF667A` | echtes Geld |

### Regeln

- Der aktuelle Modus ist dauerhaft im Header sichtbar.
- Live wird nie ausschließlich durch Farbe gekennzeichnet.
- Das Label enthält immer Text und Icon.
- Live-Aktionen zeigen zusätzlich einen Bestätigungsdialog.
- Die Live-Kennzeichnung erscheint auf jeder orderrelevanten Seite.

Beispiele:

```text
● PAPER
● LIVE – ECHTES GELD
```

---

## 6. Typografie

## 6.1 Schriftfamilie

Empfohlen:

```css
font-family:
  Inter,
  ui-sans-serif,
  system-ui,
  -apple-system,
  BlinkMacSystemFont,
  "Segoe UI",
  sans-serif;
```

Alternativ kann `Geist Sans` verwendet werden.

Für Zahlen, Kurse, IDs und technische Werte:

```css
font-family:
  "JetBrains Mono",
  "SFMono-Regular",
  Consolas,
  monospace;
```

Die Monospace-Schrift wird nur für tabellarische Zahlen, Kurse, Zeitstempel und technische Daten eingesetzt.

## 6.2 Größen

| Stil | Größe | Zeilenhöhe | Gewicht |
|---|---:|---:|---:|
| Display | 32 px | 40 px | 650 |
| H1 | 28 px | 36 px | 650 |
| H2 | 22 px | 30 px | 600 |
| H3 | 18 px | 26 px | 600 |
| Body Large | 16 px | 25 px | 400 |
| Body | 14 px | 22 px | 400 |
| Small | 13 px | 19 px | 400 |
| Caption | 12 px | 17 px | 500 |
| Metric Large | 28 px | 34 px | 600 |
| Metric | 18 px | 24 px | 600 |

## 6.3 Zahlenformat

- Kurse: Monospace.
- P&L: Monospace.
- Prozente: maximal zwei Dezimalstellen.
- Große Werte mit lokaler Tausendertrennung.
- Positive Werte mit `+`, negative Werte mit `−` (echtes Minus U+2212, nicht Bindestrich).
- Keine unnötig hohe Präzision.

**Locale-Regel (v1.1, verbindlich — vorher uneinheitlich):** genau zwei Format-Domänen, nie
gemischt:

1. **Instrumentwerte** (Einzelkurse, Entry, Stop) in der **Währung und Konvention des Instruments**
   — US-Aktien also `$184.26` mit Punkt-Dezimaltrennung.
2. **Konto-/Portfoliowerte und P&L** in der **Kontowährung mit deutscher Konvention** — Komma als
   Dezimal-, Punkt als Tausendertrennung, Währungssymbol nachgestellt: `1.250,00 €`, `−18,40 €`.

Prozente immer mit Punkt und Leerzeichen vor `%`: `+2.41 %`.

Beispiele:

```text
$184.26        (Instrumentkurs)
1.250,00 €     (Kontowert)
−18,40 €       (P&L, Kontowährung)
+2.41 %        (Prozent)
```

---

## 7. Abstände und Raster

## 7.1 Spacing-System

Basis: 4 px.

| Token | Wert |
|---|---:|
| `--space-1` | 4 px |
| `--space-2` | 8 px |
| `--space-3` | 12 px |
| `--space-4` | 16 px |
| `--space-5` | 20 px |
| `--space-6` | 24 px |
| `--space-8` | 32 px |
| `--space-10` | 40 px |
| `--space-12` | 48 px |

## 7.2 Seitenraster

Desktop:

- linke Navigation: 240 px,
- einklappbar auf 72 px,
- Hauptinhalt maximal 1440 px,
- Außenabstand 24 bis 32 px,
- Kartenraster mit 12 Spalten,
- Gap 16 oder 24 px.

Tablet:

- Navigation als Drawer,
- 8-Spalten-Raster,
- Außenabstand 20 px.

Mobile:

- eine Spalte,
- Außenabstand 16 px,
- Bottom Navigation für Hauptbereiche,
- keine horizontal überladenen Tabellen.

---

## 8. Radien und Schatten

## 8.1 Radien

| Token | Wert | Verwendung |
|---|---:|---|
| `--radius-sm` | 6 px | Tags, kleine Controls |
| `--radius-md` | 10 px | Buttons, Inputs |
| `--radius-lg` | 14 px | Karten |
| `--radius-xl` | 18 px | Dialoge |

Keine extrem runden „Bubble“-Elemente im gesamten Interface.

## 8.2 Schatten

Dark Mode nutzt Schatten sehr sparsam.

```css
--shadow-sm: 0 4px 12px rgba(0, 0, 0, 0.18);
--shadow-md: 0 12px 32px rgba(0, 0, 0, 0.28);
--shadow-dialog: 0 24px 60px rgba(0, 0, 0, 0.42);
```

Karten im normalen Layout benötigen meist keinen Schatten. Dialoge und Popover dürfen deutlicher angehoben werden.

---

## 9. Navigation

## 9.1 Desktop-Sidebar

Reihenfolge:

1. Übersicht
2. Signale
3. Positionen
4. Orders
5. Strategien
6. Performance
7. Research
8. Einstellungen

Admin-Bereich getrennt:

- Strategie-Labor
- Systemstatus
- Audit
- Nutzerverwaltung

### Sidebar-Stil

- dunklere Fläche als Hauptinhalt,
- aktive Navigation mit `primary-soft`,
- linke Akzentlinie oder dezente gefüllte Fläche,
- Icons in 18 bis 20 px,
- Text in 14 px Medium,
- keine starken Glow-Effekte.

## 9.2 Topbar

Enthält:

- Seitentitel,
- aktueller Betriebsmodus,
- Marktstatus,
- Datenfeedstatus,
- Benachrichtigungen,
- Nutzerprofil.

Beispiel:

```text
Signale     PAPER     US-Markt geöffnet     Feed aktuell
```

## 9.3 Mobile Navigation

Bottom Navigation mit maximal fünf Hauptpunkten:

- Übersicht,
- Signale,
- Positionen,
- Performance,
- Mehr.

Live- und Kill-Switch-Status bleiben oben sichtbar.

---

## 10. Dashboard-Aufbau

## 10.1 Übersicht

Empfohlene Reihenfolge:

### Statusleiste

- Modus,
- Brokerstatus,
- Marktdatenstatus,
- Marktstatus,
- Kill-Switch.

### Hauptkennzahlen

- Kontowert,
- Tages-P&L,
- offenes Risiko,
- freie Risikokapazität.

### Aktionsbereich

- neue Signale,
- ablaufende Signale,
- offene Risikoereignisse.

### Positionen

- offene Positionen,
- Stopstatus,
- unrealisiertes P&L,
- Restlaufzeit oder Haltedauer.

### Performance

- Equity-Kurve,
- Drawdown,
- Strategieaufteilung.

## 10.2 Informationshierarchie

Die Reihenfolge der Kennzahlen ist:

1. Risiko,
2. Status,
3. offene Aktionen,
4. Performance,
5. historische Details.

Der aktuelle Gewinn darf nicht die größte visuelle Fläche einnehmen.

---

## 11. Karten

## 11.1 Standardkarte

```css
background: var(--bg-surface-2);
border: 1px solid var(--border-subtle);
border-radius: var(--radius-lg);
padding: 20px;
```

### Aufbau

- Header mit Titel und optionalem Status,
- klarer Hauptwert,
- sekundäre Informationen,
- Footer mit Aktionen.

## 11.2 Signalkarte

### Sichtbarer Kopf

- Ticker,
- Assetname,
- Strategie,
- Modus,
- Ablaufzeit.

### Hauptbereich

- Entry,
- Stop,
- Risiko,
- Positionsgröße,
- erwartete Haltedauer.

### Sekundärbereich

- Markregime,
- Spread,
- Datenalter,
- Strategie-Rohscore,
- historischer Netto-Erwartungswert.

### Aktionen

- Primär: „Trade prüfen“
- Sekundär: „Ablehnen“
- Tertiär: „Details“

Nicht direkt in der ersten Kartenansicht:

- großer grüner Kaufen-Button,
- aggressives Renditeziel,
- „Top Pick“-Badge,
- künstliche Dringlichkeit.

## 11.3 Risikokarte

Risikokarten verwenden keine dekorativen Charts.

Beispiel:

```text
Maximal geplantes Risiko
€24,50

0,24 % des Kontowerts
Stop: $181,40
```

---

## 12. Buttons

## 12.1 Primärbutton

Verwendung:

- Trade prüfen,
- Änderungen speichern,
- Broker verbinden.

Stil:

```css
background: var(--primary);
color: var(--text-inverse);
height: 40px;
padding: 0 16px;
border-radius: var(--radius-md);
font-weight: 600;
```

## 12.2 Sekundärbutton

- transparente oder dunkle Fläche,
- sichtbarer Rahmen,
- heller Text.

Verwendung:

- Details,
- Zurück,
- Filter,
- neutrale Aktionen.

## 12.3 Destruktiver Button

Verwendung:

- Position schließen,
- Broker trennen,
- Kill-Switch aktivieren.

Nie als primäre Standardaktion darstellen.

## 12.4 Live-Orderbutton

Beschriftung eindeutig:

```text
Live-Order verbindlich senden
```

Nicht:

```text
Kaufen
```

Vor der Aktion erscheint ein zusätzlicher Bestätigungsdialog.

## 12.5 Button-Zustände

- Default,
- Hover,
- Active,
- Focus,
- Disabled,
- Loading,
- Success,
- Error.

Loading-Zustände dürfen keine erneute Ausführung ermöglichen.

---

## 13. Formulare und Eingaben

## 13.1 Inputs

```css
background: var(--bg-surface-1);
border: 1px solid var(--border-default);
color: var(--text-primary);
height: 40px;
border-radius: var(--radius-md);
```

## 13.2 Labels

- immer oberhalb des Feldes,
- klar und kurz,
- Pflichtfelder sichtbar,
- Hilfetext unterhalb,
- Fehlermeldung direkt am Feld.

## 13.3 Risiko-Einstellungen

Keine freien unbegrenzten Zahlenfelder für kritische Limits.

Bevorzugt:

- Slider mit sicheren Grenzen,
- Presets,
- erklärende Maximalwerte,
- sofortige Auswirkungsvorschau.

Beispiel:

```text
Risiko pro Trade
[ 0,10 % — 0,25 % — 0,50 % ]

Bei aktuellem Kontowert:
maximal €25,00 Verlust pro Trade
```

---

## 14. Tabellen

## 14.1 Grundstil

- dunkle ruhige Zeilen,
- dezente Trennlinien,
- kompakte, aber nicht enge Abstände,
- Kopfzeile sticky bei langen Tabellen,
- Zahlen rechtsbündig,
- Text linksbündig,
- Status mittig oder links.

## 14.2 Tabellenzeilen

- Hover-Fläche,
- ausgewählte Zeile deutlich,
- keine Zebra-Streifen mit hohem Kontrast,
- row click nur, wenn klar erkennbar.

## 14.3 Wichtige Spalten

Positionstabelle:

- Ticker,
- Strategie,
- Modus,
- Stückzahl,
- Entry,
- aktueller Kurs,
- Stop,
- offenes Risiko,
- P&L,
- Status.

Auf Mobile wird daraus eine Kartenliste.

---

## 15. Charts

## 15.1 Chartstil

- dunkler Hintergrund ohne separaten weißen Plotbereich,
- dezente Gridlines,
- keine 3D-Darstellung,
- keine starken Verläufe,
- maximal zwei bis drei hervorgehobene Datenreihen,
- direkte Labels oder klare Legende.

## 15.2 Equity-Kurve

- Hauptkurve in Primärblau,
- Benchmark in gedämpftem Grau,
- Drawdown separat,
- Backtest, Paper und Live niemals in derselben Linie vermischen.

## 15.3 Positive und negative Werte

Grün und Rot nur für tatsächliche positive oder negative Werte.

Keine gesamte Chartfläche wird rot oder grün eingefärbt.

## 15.4 Tooltips

Tooltips zeigen:

- Datum,
- Modus,
- Strategie,
- Wert,
- Drawdown,
- relevante Kosten.

## 15.5 Kerzencharts

Nur dort einsetzen, wo sie eine echte Analysefunktion erfüllen. Nicht standardmäßig auf jeder Seite.

---

## 16. Statussystem

## 16.1 Status-Chips

Status-Chips sind kompakt und enthalten Text.

Beispiele:

- `GEFÜLLT`
- `TEILGEFÜLLT`
- `BLOCKIERT`
- `ABGELAUFEN`
- `LIVE`
- `PAPER`

## 16.2 Statusfarben

| Status | Farbe |
|---|---|
| neutral | Grau |
| aktiv | Blau |
| erfolgreich | Grün |
| Warnung | Gelb |
| kritisch | Rot |
| Research | Violett |

## 16.3 Keine reine Farbcodierung

Jeder Status besitzt zusätzlich:

- Text,
- optional Icon,
- Tooltip bei technischen Zuständen.

---

## 17. Alerts und Hinweise

## 17.1 Informationshinweis

Blauer, dezenter Hinweis.

Beispiel:

> Das Signal wurde im Shadow-Modus erzeugt und kann nicht ausgeführt werden.

## 17.2 Warnung

Gelb.

Beispiel:

> Das Signal läuft in 3 Minuten ab.

## 17.3 Kritischer Hinweis

Rot.

Beispiel:

> Der Marktdatenfeed ist nicht aktuell. Neue Positionen sind blockiert.

## 17.4 Erfolg

Grün, kurz und sachlich.

Beispiel:

> Paper-Order wurde vollständig ausgeführt.

Keine konfettiartigen Animationen oder Gamification.

---

## 18. Dialoge

## 18.1 Trade-Bestätigungsdialog

Der Dialog enthält in fester Reihenfolge:

1. Modus,
2. Ticker und Strategie,
3. Ordertyp,
4. Positionsgröße,
5. Entry,
6. Stop,
7. maximales Risiko,
8. erwartete Kosten,
9. Exit-Policy,
10. Bestätigung.

Bei Live:

- roter Live-Hinweis,
- Text „Echtes Geld“,
- Button mit vollständiger Handlung,
- optional Eingabe eines kurzen Bestätigungstextes bei besonders kritischen Aktionen.

## 18.2 Position schließen

Der Dialog zeigt:

- aktuelle Position,
- geschätzten Exit,
- erwarteten Spread,
- offene Schutzorders,
- Auswirkung auf P&L.

---

## 19. Empty States

Leere Bereiche sollen ruhig und hilfreich sein.

Beispiel Signale:

```text
Keine neuen Signale

Aktuell erfüllt kein Setup alle Strategie- und Risikokriterien.
```

Beispiel Positionen:

```text
Keine offenen Positionen

Angenommene und ausgeführte Trades erscheinen hier.
```

Keine motivierenden Aussagen wie:

- „Bereit für deinen nächsten Gewinner?“
- „Verpasse nicht den nächsten Trade.“

---

## 20. Loading- und Fehlerzustände

## 20.1 Loading

- Skeletons für Karten und Tabellen,
- keine dauerhaft rotierenden großen Spinner,
- Button lädt inline,
- bestehende Inhalte bleiben sichtbar, wenn möglich.

## 20.2 Fehler

Fehlermeldungen sind konkret:

Schlecht:

```text
Etwas ist schiefgelaufen.
```

Besser:

```text
Die Brokerpositionen konnten nicht geladen werden.
Neue Orders bleiben blockiert. Erneuter Versuch in 30 Sekunden.
```

## 20.3 Unsichere Zustände

Wenn Daten nicht sicher sind:

- keine optimistische Schätzung,
- Status deutlich markieren,
- orderrelevante Aktionen blockieren.

---

## 21. Icons

Empfohlen:

- Lucide Icons,
- Heroicons Outline,
- oder ein vergleichbares einheitliches Outline-Set.

Regeln:

- ein Iconset,
- 1,5 bis 2 px Strichstärke,
- keine Emoji als Interface-Icons,
- Icons nie allein bei kritischen Aktionen,
- Tooltips bei unklaren Symbolen.

---

## 22. Animation und Bewegung

Animationen sind kurz und funktional.

### Dauer

- Hover: 120 ms,
- normale Transition: 160 bis 200 ms,
- Dialog: maximal 220 ms.

### Erlaubt

- dezenter Farbwechsel,
- leichtes Einblenden,
- Accordion-Öffnung,
- Statusaktualisierung.

### Nicht erlaubt

- springende Kurse,
- blinkende P&L-Werte,
- dauerhafte Glow-Animation,
- Konfetti,
- aggressive Countdown-Animationen.

`prefers-reduced-motion` wird respektiert.

---

## 23. Responsive Design

## 23.1 Desktop

Optimiert für Analyse und Verwaltung.

- mehrere Karten nebeneinander,
- Tabellen,
- erweiterte Charts,
- feste Sidebar.

## 23.2 Tablet

- reduzierte Spaltenzahl,
- Navigation als Drawer,
- Karten untereinander,
- wichtige Tabellen horizontal scrollbar oder transformiert.

## 23.3 Mobile

Fokus auf:

- neue Signale,
- Freigabe,
- offene Positionen,
- Risiko,
- Kill-Switch.

Komplexe Research-Funktionen können auf Mobile reduziert oder als Desktop-Hinweis dargestellt werden.

## 23.4 Mobile Signal-Freigabe

Auf einer mobilen Ansicht müssen vor der Freigabe ohne horizontales Scrollen sichtbar sein:

- Modus,
- Ticker,
- Entry,
- Stop,
- Risiko,
- Positionsgröße,
- Ablaufzeit.

---

## 24. Accessibility

Mindestens WCAG 2.1 AA als Ziel.

### Anforderungen

- ausreichender Kontrast,
- sichtbarer Tastaturfokus,
- vollständige Tastaturbedienung,
- verständliche Labels,
- semantisches HTML,
- Screenreader-Texte,
- keine reine Farbcodierung,
- Mindestgröße für Touch-Ziele: 44 × 44 px,
- respektierte Reduced-Motion-Einstellung.

Charts benötigen tabellarische oder textliche Alternativen.

---

## 25. Sprache und Microcopy

## 25.1 Tonalität

- sachlich,
- ruhig,
- direkt,
- verständlich,
- nicht werblich.

## 25.2 Begriffe

Bevorzugt:

- „Trade prüfen“
- „Order freigeben“
- „Maximal geplantes Risiko“
- „Durch Risikoregel blockiert“
- „Signal abgelaufen“
- „Brokerverbindung getrennt“

Vermeiden:

- „Jetzt zuschlagen“
- „Top Chance“
- „Sicheres Signal“
- „Gewinner“
- „Moon“
- „Verdopplungspotenzial“
- „KI empfiehlt“

## 25.3 Risikohinweise

Risikohinweise sind konkret und kontextbezogen.

Beispiel:

> Der tatsächliche Verlust kann bei Kurslücken über dem geplanten Stop liegen.

---

## 26. Telegram-Stil

Telegram bleibt funktional und kompakt.

## 26.1 Signalnachricht

Empfohlener Aufbau:

```text
PAPER · Neues Swing-Setup

AAPL · Swing Trend v1.2

Entry: $184,20–$184,80
Stop: $181,40
Größe: 8 Aktien
Max. geplantes Risiko: $24,80
Ablauf: 16:10 New York

Marktregime: positiv
Spread: 0,03 %
Datenstatus: aktuell

[Trade prüfen] [Ablehnen]
[In Web-App öffnen]
```

## 26.2 Live-Nachricht

Live-Nachrichten beginnen immer mit:

```text
LIVE · ECHTES GELD
```

## 26.3 Emojis

Sehr sparsam oder gar nicht.

Erlaubt höchstens für eindeutige Statusmeldungen:

- Warnung,
- Fehler,
- Erfolg.

Keine Raketen, Geldsäcke oder Feuer-Symbole.

---

## 27. Design-Tokens

Beispiel für eine zentrale CSS-Definition:

```css
:root {
  color-scheme: dark;

  --bg-base: #0B0D10;
  --bg-surface-1: #11151A;
  --bg-surface-2: #171C22;
  --bg-surface-3: #1D242C;
  --bg-elevated: #222A33;
  --bg-hover: #252E38;
  --bg-selected: #263242;

  --text-primary: #F4F7FA;
  --text-secondary: #B5C0CC;
  --text-muted: #7F8B99;
  --text-disabled: #56616D;
  --text-inverse: #0B0D10;

  --primary: #5B8CFF;
  --primary-hover: #74A0FF;
  --primary-active: #4779E8;
  --primary-soft: rgba(91, 140, 255, 0.14);
  --primary-border: rgba(91, 140, 255, 0.38);

  --success: #35C98F;
  --success-soft: rgba(53, 201, 143, 0.14);
  --success-border: rgba(53, 201, 143, 0.36);

  --warning: #F2B84B;
  --warning-soft: rgba(242, 184, 75, 0.14);
  --warning-border: rgba(242, 184, 75, 0.38);

  --danger: #FF667A;
  --danger-hover: #FF7C8C;
  --danger-soft: rgba(255, 102, 122, 0.14);
  --danger-border: rgba(255, 102, 122, 0.38);

  --info: #66B7FF;
  --info-soft: rgba(102, 183, 255, 0.13);

  --border-subtle: #242B33;
  --border-default: #303945;
  --border-strong: #45515F;
  --focus-ring: rgba(91, 140, 255, 0.55);

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;

  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-xl: 18px;

  --shadow-sm: 0 4px 12px rgba(0, 0, 0, 0.18);
  --shadow-md: 0 12px 32px rgba(0, 0, 0, 0.28);
  --shadow-dialog: 0 24px 60px rgba(0, 0, 0, 0.42);
}
```

---

## 28. Beispiel-Komponenten

## 28.1 Statusleiste

```text
[PAPER]  Markt geöffnet  Feed aktuell  Broker verbunden  Kill-Switch aus
```

## 28.2 Hauptmetriken

```text
Kontowert             Offenes Risiko
€10.240,50            €42,80

Tages-P&L             Freies Risikobudget
−€18,40               €76,20
```

## 28.3 Signalzusammenfassung

```text
AAPL
Swing Trend v1.2

Entry       Stop        Größe       Max. Risiko
$184,40     $181,40     8            $24,00

Ablauf: 16:10 New York
```

---

## 29. Prioritäten für die Umsetzung

### Phase 1 – Designgrundlage

- Farben,
- Typografie,
- Spacing,
- Radien,
- Icons,
- Statussystem,
- Mode-Badges.

### Phase 2 – Kernkomponenten

- Button,
- Input,
- Select,
- Dialog,
- Alert,
- Card,
- Status-Chip,
- Tabelle,
- Tooltip,
- Tabs.

### Phase 3 – Hauptseiten

- Dashboard,
- Signale,
- Positionen,
- Orders,
- Performance,
- Einstellungen.

### Phase 4 – Risikointeraktionen

- Trade-Bestätigungsdialog,
- Kill-Switch,
- Brokerstatus,
- Risk-Profile-Editor,
- Fehler- und Unsicherheitszustände.

### Phase 5 – Responsive und Accessibility

- Mobile Layout,
- Tastaturbedienung,
- Screenreader,
- Kontrastprüfung,
- Reduced Motion.

---

## 30. Definition of Done für das Style-System

Das Style-System gilt als umgesetzt, wenn:

- alle Hauptseiten dieselben Design-Tokens verwenden,
- Dark Mode durchgängig ist,
- keine kritische Information nur farblich dargestellt wird,
- Betriebsmodi auf jeder relevanten Seite sichtbar sind,
- Live-Aktionen eindeutig gekennzeichnet sind,
- alle Kernkomponenten dokumentiert sind,
- Desktop, Tablet und Mobile unterstützt werden,
- Tastaturfokus sichtbar ist,
- Kontrastanforderungen geprüft wurden,
- und Telegram-Nachrichten dieselbe Terminologie wie die Web-App verwenden.

---

## 31. Zusammenfassung

Das Interface soll nicht durch starke Effekte beeindrucken, sondern durch Ruhe, Präzision und Vertrauen.

Die wichtigsten visuellen Regeln sind:

- fast schwarzer, aber nicht rein schwarzer Hintergrund,
- klare blaue Primärfarbe,
- Grün und Rot nur semantisch,
- geringe visuelle Dichte,
- starke Informationshierarchie,
- sichtbare Betriebsmodi,
- risikoorientierte Darstellung,
- keine Gamification,
- keine übertriebene KI- oder Trading-Ästhetik.

Das Ergebnis ist ein modernes Dark-Mode-Produkt, das wie ein professionelles Kontroll- und Analysewerkzeug wirkt.

---

## 32. Audit-Nachtrag & Präzisierungen (v1.1, 2026-07-22)

Ergebnis eines Reviews von v1.0 gegen die tatsächliche W7-Umsetzung. v1.0 ist inhaltlich stark;
die folgenden Punkte schließen konkrete Lücken und lösen zwei innere Widersprüche auf. Alles hier
ist **normativ** und ergänzt bzw. präzisiert die referenzierten Abschnitte.

### 32.1 Kontrast — verifiziert (WCAG 2.1 AA), ergänzt §24

Die Token-Paare wurden gemessen. **Alle Text- und Semantikfarben bestehen AA (≥ 4.5:1)** auf
`--bg-surface-2`; die Regel „kritische Info nie nur farblich" (§16.3) bleibt trotzdem Pflicht.

| Paar (auf `#171C22` surface-2) | Ratio | Bewertung |
|---|---:|---|
| `--text-primary #F4F7FA` | 15.94 | AA ✓ |
| `--text-secondary #B5C0CC` | 9.28 | AA ✓ |
| `--text-muted #7F8B99` | 4.94 | AA ✓ (knapp — s. u.) |
| `--text-disabled #56616D` | 2.71 | **fällt durch** — nur für *disabled* zulässig (WCAG-Ausnahme) |
| `--primary #5B8CFF` | 5.42 | AA ✓ |
| `--success #35C98F` | 8.08 | AA ✓ |
| `--warning #F2B84B` | 9.57 | AA ✓ |
| `--danger #FF667A` | 6.07 | AA ✓ |
| `--info #66B7FF` | 7.98 | AA ✓ |
| `--text-inverse` auf `--primary`/`--success`/`--warning`/`--danger` | 6.15 / 9.18 / 10.87 / 6.89 | AA ✓ (Button-Text lesbar) |

Regeln daraus:
- `--text-muted` liegt bei 4.94 nur knapp über AA und wird **auf helleren Flächen als surface-2
  (surface-3/elevated/hover) nicht mehr für lesepflichtigen Text** verwendet, sondern nur für
  Metadaten. Auf hellen Flächen `--text-secondary` nehmen.
- `--text-disabled` ist **ausschließlich** für inaktive Controls — nie für dauerhaft zu lesenden
  Inhalt, nie für Status.

### 32.2 Modusfarben vs. Semantikfarben — Kollision aufgelöst, ergänzt §5/§16

Die Modusfarben sind **absichtlich** identisch zu Semantikfarben (Paper = `--warning`,
Live = `--danger`, Shadow = `--info`, Backtest ≈ Research-Violett). Das ist eine Kollision: ein
gelber Chip könnte „Paper" oder „Warnung" heißen. Auflösung — Modus-Kennzeichen ist **nie nur
Farbe**, sondern eine eigene, unverwechselbare Behandlung:
- Modus-Chip trägt **immer** einen gefüllten Punkt **und** das Modus-Wort in Großbuchstaben
  (`● LIVE`, `● PAPER`), sitzt an fester Position (Topbar/Kartenkopf) und ist **persistent**.
- Transiente Semantik (Warnung/Fehler/Erfolg) erscheint **nie** in dieser Chip-Form an
  Modus-Positionen, sondern als Alert/Inline-Status (§17).
- Ein Element zeigt nie gleichzeitig Modus- und Semantikbedeutung in derselben Farbfläche.

### 32.3 Datenaktualität & Zeit — an das Safety-Modell gekoppelt, neu

Die Frische-Anzeige ist kein Kosmetik-, sondern ein Sicherheits-Element (Quote-Freshness-Gate,
P2-quote). Verbindlich:
- **Feed-Status** hat drei sichtbare Zustände, an denselben Schwellen wie das Backend-Gate:
  `aktuell` (neutral/grün-Chip), `verzögert` (`--warning`, Alter anzeigen), `veraltet – Orders
  blockiert` (`--danger`). Der veraltete Zustand **blockiert orderrelevante Aktionen sichtbar**
  (Buttons disabled + Begründung), statt sie nur zu markieren.
- **Zeitzonen** sind immer beschriftet. Marktbezogene Zeiten (Ablauf, Marktöffnung) in
  **Marktzeit mit Kürzel** (`16:10 ET`); System-/Audit-Zeiten in **UTC**. Nie eine unbeschriftete
  Lokalzeit rendern (der DB-Zeitvertrag ist naive UTC — die UI beschriftet explizit, was sie zeigt).

### 32.4 Dialog- & Fokus-Verhalten — a11y-Pflicht, ergänzt §18/§22/§24

Für alle modalen Dialoge, besonders den Trade-Bestätigungsdialog (§18.1):
- `role="dialog"` / `aria-modal="true"`, Fokus wird **gefangen** (Fokus-Trap), ESC schließt,
  beim Schließen kehrt der Fokus auf das auslösende Element zurück.
- **Anti-Fehlklick (Live/verbindlich):** Der bestätigende Primärbutton ist **nicht**
  initial fokussiert und **nicht** per Enter auslösbar; Initialfokus liegt auf „Abbrechen" bzw.
  dem Bestätigungs-Eingabefeld. Damit löst kein versehentliches Enter eine Order aus.
- Loading-Zustand des Bestätigungsbuttons verhindert Doppel-Submit (deckt sich mit der
  OMS-Doppelklick-Absicherung; §12.5).

### 32.5 „Unsicher/degradiert" als eigener visueller Zustand, ergänzt §20.3

§20.3 fordert Markierung, definiert aber kein Muster. Festgelegt: ein **degradierter Zustand**
nutzt ein `--warning`-umrandetes Banner mit Text „Daten unsicher — …" plus disabled-geschaltete
orderrelevante Controls. Er ist visuell von „Loading" (Skeleton) und „Fehler" (`--danger`)
klar unterscheidbar. Optimistische Schätzwerte sind in diesem Zustand verboten.

### 32.6 Navigations-Taxonomie mit der echten App abgeglichen, ergänzt §9.1

§9.1 listet eine generische Idealnavigation. Die reale App hat u. a. `dashboard`, `settings`,
`lab` (Strategie-Labor), `reports`, `backtest`, `watchlist`, `history`, `login`. Verbindlich:
Bevor die Seiten aufgebaut werden, wird §9.1 auf die **tatsächlichen Routen** gemappt (z. B.
Watchlist/History als Erstklasse-Punkte, Backtest+Labor im Admin-/Research-Bereich). Das Konzept
beschreibt Ziel-IA; die Umsetzung darf keine im Konzept fehlende Seite ungestylt lassen.

### 32.7 Kategoriale Chart-Palette, ergänzt §15

§15 deckt 2–3 Reihen ab, aber „Strategieaufteilung" braucht mehrere unterscheidbare Serien.
Festgelegt: eine **feste, farbenblind-sichere kategoriale Palette** (getrennt von Grün/Rot, die
semantisch reserviert bleiben), maximal 6 Kategorien, danach „Sonstige" aggregieren. Wird als
eigene Token-Gruppe (`--cat-1 … --cat-6`) definiert, sobald die erste Mehrserien-Grafik gebaut wird.

### 32.8 Web ↔ Matplotlib-Export-Parität, neu

Backtest/Reports erzeugen server-seitige PNGs (matplotlib). Diese müssen dieselben Farben/
Konventionen wie die Web-Charts verwenden (dunkler Hintergrund, Primärblau-Kurve, Benchmark grau,
kategoriale Palette aus §32.7). Ein gemeinsames matplotlib-Style-Mapping der Tokens wird
angelegt, damit Export und Web nicht auseinanderlaufen. Dies deckt auch die
dataviz-Konsistenz aus §15 für Exporte ab.

### 32.9 Begriffs-Parität Web ↔ Telegram, ergänzt §25/§26/§30

Die DoD (§30) verlangt identische Terminologie über beide Kanäle, es fehlt aber die gemeinsame
Quelle. Festgelegt: ein **einziges Glossar** (Web + Telegram referenzieren dieselben Strings für
Status-, Modus- und Aktionsbegriffe — „Trade prüfen", „Durch Risikoregel blockiert",
„Signal abgelaufen" …). Divergierende Formulierungen für denselben Zustand sind ein Defekt.

### 32.10 Light Mode als bewusstes Nicht-Ziel, ergänzt §2.6/§24

Dark-only ist eine **bewusste Produktentscheidung** (`color-scheme: dark`), kein Versäumnis.
Explizit festgehalten, damit A11y-Reviews es als Scope-Grenze kennen. Falls je ein Hell-/
Hochkontrast-Modus nötig wird, geschieht das über die bestehende Token-Ebene (ein zweites
`:root`-Theme), nicht durch Einzelfarben — die Komponenten bleiben tokenbasiert.

### 32.11 Umsetzungsstand (Abgleich mit W7)

- **Umgesetzt (W7, deployt):** §27 Tokens → `tokens.css` (1:1, 100 Tokens); Kernkomponenten
  (§11/§12/§14/§16/§17) → `components.css` + Makros; Pflicht-Bestätigungsdialog (§18.1),
  „Trade prüfen" statt grünem Kaufbutton (§11.2/§12.4), Kill-Switch-Chip, Mode-Report-Panel
  (§5); base.html lädt beide Stylesheets.
- **Offen / durch diesen Nachtrag präzisiert:** §32.3 Staleness-Kopplung an das Gate,
  §32.4 Fokus-Trap + Anti-Fehlklick im Live-Dialog, §32.5 degradierter Zustand, §32.6
  Routen-Mapping, §32.7 kategoriale Palette, §32.8 Matplotlib-Parität, §32.9 Glossar. Diese
  gehen als Style-Nacharbeit in den Umsetzungsplan (Gate Style / W7-Rest).

### Changelog

- **v1.1 (2026-07-22):** Audit-Nachtrag §32; §6.3 Locale-Regel vereinheitlicht (war
  Punkt/Komma gemischt); Header: `tokens.css` als kanonische Quelle + Governance.
- **v1.0 (2026-07-11):** Erstfassung.
