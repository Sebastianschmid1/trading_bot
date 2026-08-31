# Zonenlabor — Prototyp (Boxen-Labeling + Prädiktor-Vergleich)

Explorativer Prototyp, **kein Produktionscode**. Liegt bewusst unter `tools/`, nicht
unter `stockbot/` — nichts hier wird importiert, deployt oder getestet.

Veröffentlichtes Artefakt:
<https://claude.ai/code/artifact/e8b50aeb-92a6-4693-83d1-7f8b28d17a75>

## Was es tut

1. **Boxen-Labeling.** Setzt automatisch rote Kaufboxen an Kurstiefs und blaue
   Verkaufsboxen an Kurshochs — die Farbkonvention stammt aus einem handgezeichneten
   extraETF-Screenshot des Nutzers. Drei Schritte: ZigZag-Wendepunkte, Filter auf eine
   lohnende Folgewelle, Rechteck über alle Bars im Toleranzband. Vier Regler live.
2. **Fünf Prädiktoren.** RSI-Extrem (Baseline), Z-Score & Drawdown, logistische
   Regression, Gradient Boosting auf Stümpfen, k-NN-Formvergleich. Alle in reinem
   JavaScript, ohne Bibliothek.
3. **Bewertung.** Walk-forward mit Embargo am Trainingsrand, Schwellenwahl auf einem
   zurückgehaltenen Fünftel des Trainingsfensters, Präzision/Trefferquote/F1/MCC je
   Klasse, Timing-Fehler und eine Handelssimulation gegen Kaufen-und-Halten.

Die Boxen sind konstruktionsbedingt Look-ahead — sie sind die Zielgröße, nicht das
Signal. Das Embargo ist die Leckage-Sperre.

## Offener Blocker: echte Kursdaten

Der Prototyp läuft mit einer **synthetischen Demo-Reihe**, im UI dreifach als solche
markiert. Grund: in der Cloud-Session (Claude Code on the web) sperrt die
Egress-Policy sämtliche Marktdaten-Hosts — Yahoo, stooq, WisdomTree, justETF,
extraETF, Alpaca, Alphavantage, Twelvedata, Polygon, EODHD, Tiingo, Finnhub, alle
403 am Proxy, `curl` und WebFetch gleichermaßen. Erreichbar sind nur GitHub und
Paket-Registries.

**Lokal entfällt das.** `yfinance` ist ohnehin Repo-Abhängigkeit; eine lokale Sitzung
zieht die Reihe direkt.

Zielpapier: **WisdomTree NASDAQ 100 5x Daily Leveraged**, ISIN `XS2771642134`,
WKN `A4AFDW`, Ticker QS5L (Mailand) / LQS5 (LSE), Auflage 30.04.2024, TER 0,70 % p. a.
Yahoo-Symbolkandidaten zum Durchprobieren: `LQS5.L`, `QS5L.MI`, `QSL5.SG`, `QSL5.F`.

Ersatzweise ohne Netz: CSV-Export bei investing.com oder finanzen.net ziehen und im
Artefakt einlesen. Der Parser versteht deutsches Format (Semikolon, Dezimalkomma,
`TT.MM.JJJJ`) und ISO; Spalten werden über die Kopfzeile gefunden
(`Datum`/`Date`, `Schluss`/`Close`/`Kurs`/`NAV`). extraETF hat **keinen**
Kurshistorien-Export, nur Portfolio-Transaktionen.

## Nächster Schritt (vom Nutzer bestellt, noch nicht gebaut)

ETF-Labor mit Dropdown, QS5L als erster Eintrag: je Papier eigene Kursreihe, eigene
Boxen, eigener Lauf. Aufteilung **erste 75 % Training, letzte 25 % Prädiktion**.

Vorbehalt, der beim Bauen zu berücksichtigen ist: bei ~590 Bars liegen im 25-%-Fenster
nur ~10 gelabelte Box-Tage; Präzision und Trefferquote springen dann pro Treffer um
zweistellige Prozentpunkte, und das Testfenster ist genau ein Marktregime. Vereinbart
ist deshalb 75/25 als Standard **und** Walk-forward als zweiter Modus daneben, damit
sichtbar wird, ob ein Sieger beides übersteht.

## Lokal ansehen

Die Datei ist eine Artefaktquelle: sie beginnt direkt mit `<title>` und `<style>`,
ohne `<!doctype>`/`<html>`/`<head>`/`<body>` — die ergänzt der Artefakt-Host beim
Veröffentlichen. Zum Öffnen im Browser einmal umhüllen:

```bash
{ printf '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  cat tools/zonenlabor/zonenlabor.html
} > /tmp/zonenlabor-preview.html && xdg-open /tmp/zonenlabor-preview.html
```

Zum Aktualisieren des veröffentlichten Artefakts aus einer anderen Sitzung muss die
obige URL als `url` mitgegeben werden — sonst entsteht ein zweites Artefakt statt
einer neuen Version.
