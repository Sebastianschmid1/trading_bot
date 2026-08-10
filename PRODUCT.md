# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

<!-- Zwei Nutzer-Surfaces: die FastAPI-Web-App (diese Design-Surface) und ein
Telegram-Bot. Beide zeigen denselben Zustand; der Bot ist keine Web-Design-Surface,
teilt aber Terminologie und Glossar mit der Web-App. -->

## Users

Externe, fremde Endnutzer (echtes Multi-User-Produkt), die sich über
Telegram-Auth (`/auth/telegram`) registrieren und pro Nutzer eigene Broker-Zugänge
(Alpaca) hinterlegen. Zwei Rollen laut RBAC: `user` (Regelnutzer) und `admin`.
Primärer Nutzer = Retail-Trader, der Handelssignale prüft, testet und (heute im
Paper-Modus) ausführt. Weil das Publikum fremd und wachsend ist, sind Onboarding,
Vertrauensaufbau und Selbsterklärung materielle Anforderungen — nicht nur dichte
Operator-Dichte.

## Product Purpose

Ein gehärteter „Trading Research & Execution Assistant": Nutzer finden
Handelssignale, prüfen sie, testen Strategien per Backtest/Lab und führen Trades
aus. **Heutiger Zustand:** Paper-Trading ist Standard, Live-Handel ist im Code hart
gesperrt (Kill-Switch TSAFE-001). **Erklärtes Produktziel:** perspektivisch ein
echtes Live-Trading-Tool mit echten Orders und echtem Geld. Erfolg = ein Nutzer
kann sicher vom Signal zur (heute Paper-, künftig Live-)Ausführung kommen, ohne dass
eine Fehlbedienung oder ein Systemfehler zu unbeabsichtigtem Kapitalrisiko führt.

<!-- Offen (Produktentscheidung, nicht erfinden): Zeitpunkt und Bedingungen, unter
denen Live-Handel freigeschaltet wird. Live ist heute TSAFE-gesperrt; die Freigabe
ist ein bewusstes, noch offenes Gate. -->

## Positioning

Der Unterschied ist die **gehärtete Ausführungsstrecke**, die ein „normaler"
Signal-Bot nicht wahrheitsgemäß kopieren könnte: jede Order läuft durch ein
zentrales Risk-Gate mit sichtbaren Ablehngründen, einen persistenten Kill-Switch,
Positions-/Exposure-/Verlust-Limits und eine Reconciliation gegen den Broker;
Paper ist Standard und Live ist bis zur ausdrücklichen Freigabe gesperrt. Dazu
strikte **Pro-Nutzer-Isolation der Broker-Credentials** (ein Nutzer handelt nie über
fremde oder Betreiber-Schlüssel). Sicherheit-vor-Rendite ist hier kein Slogan,
sondern in TSAFE-Guards codiert.

## Operating Context

- **Zwei Kanäle, ein Zustand:** Telegram-Bot und Web-App benennen Status/Modus/
  Aktion identisch (gemeinsames Glossar). Nutzer wechseln zwischen beiden.
- **Broker:** Alpaca (Paper heute). Prod läuft auf PostgreSQL auf einem VPS.
- **Kern-Workflows der Web-App (Routen `/app/*`):**
  Dashboard (aktive Trades, Signale) · Signal-Review und Annahme (`/app/accept`,
  `/app/scan`) · Verkauf (`/app/sell`) · Backtest (`/app/backtest`) ·
  Lab/Strategie-Optimierung (`/app/lab/run|apply|reject`) · Watchlist · Reports/
  Equity · History · Settings (eigene Alpaca-Keys, Kill-Switch, Benachrichtigungen,
  Token-Rotation, Hebel).
- **Auto-Accept:** Nutzer können Signale automatisch annehmen lassen; sie erhalten
  dann einen gebündelten Tagesreport statt Einzelmeldungen.

## Capabilities and Constraints

- **Deutsche UI ist bindend:** alle nutzersichtbaren Texte auf Deutsch, orthografisch
  korrekt (Umlaute/ß), keine ASCII-Ersatzschreibung.
- **Paper-Standard / Live hart gesperrt (heute):** codeseitig über TSAFE-Guards
  (Kill-Switch, Leverage-/Options-Blockade, zentrales OMS-Order-Routing). Diese
  Sicherheitsmechanismen sind Produkt-Fakten, keine Deko — Redesign darf sie nicht
  umgehen oder aufweichen.
- **Sichtbare Risk-Gate-Gründe:** eine abgelehnte Order zeigt den sachlichen Grund
  (z. B. „Positionslimit erreicht"), nicht nur ein generisches Scheitern.
- **Pro-Nutzer-Broker-Isolation** und **RBAC** (`user`/`admin`).
- **Bestehende UX-Sicherungen:** Einstieg heißt „Trade prüfen" (nicht „Kaufen"),
  mit Pflicht-Bestätigungsdialog. Incumbent-Fakt; bei der Bewegung Richtung Live
  bewusst weiterzuführen, nicht stillschweigend zu entfernen.
- **Mobile-first-Realität:** 44 px Touch-Ziele, Bottom-Navigation am Daumenende.

## Brand Commitments

- **Deutsche Stimme** (sachlich, kein Ticket-/Fachjargon) ist bindend.
- **Visuelle Direction (bindend, vom Nutzer gesetzt):** das Design-System
  **liquid-glass, Light-Variante**, Quelle
  `/home/jms/main_projekt/styles/liquid-glass/` (mit `DESIGN.md`, `liquid-glass.css`,
  `styleguide.html`, `.impeccable/design.json`). Dieses System **löst das bisherige
  `Stylekonzept.md` des Repos als maßgebliche Visual-Authority ab.** (Die konkrete
  Ausarbeitung/Übernahme ist Sache von `new-work`/`document`, nicht von `init`.)
- **Produktname:** incumbent „Signal Bot" / „Stock Signal Bot" mit kleinem
  Linien-Chart-Logo — vom Nutzer **nicht** als bindend bestätigt, also als offen zu
  behandeln (kann bei der Visual-Erneuerung überdacht werden).

## Evidence on Hand

- Laufendes Produkt auf dem VPS (Paper) mit echten Postgres-Daten und aktiven
  Nutzern.
- Bestehende Jinja-Templates unter `stockbot/web/templates/` und Assets unter
  `stockbot/web/static/` (`tokens.css`, `components.css`) — der **abzulösende**
  Ist-Zustand.
- Neues Design-System `liquid-glass` (Light) unter dem oben genannten Pfad — die
  neue Referenz.
- Kein erfundenes Marketing, keine erfundenen Testimonials/Preise/Nutzerzahlen:
  solche Angaben existieren nicht und dürfen nicht behauptet werden.

## Product Principles

1. **Sicherheit vor Rendite, sichtbar.** Schutzmechanismen (Risk-Gate, Kill-Switch,
   Paper-Default) sind Teil des Produktversprechens und müssen im UI erkennbar und
   erklärt sein — nicht versteckt.
2. **Ein Zustand, zwei Kanäle.** Web und Telegram benennen denselben Zustand
   identisch; Divergenz ist ein Defekt.
3. **Kein stiller Fehlschlag.** Was nicht passiert ist (z. B. ein nicht gekaufter
   Trade), wird dem Nutzer mit Grund gezeigt, nicht verschwiegen.
4. **Fremdes Publikum ernst nehmen.** Onboarding, Vertrauen und Selbsterklärung
   zählen, weil sich echte, unbekannte Nutzer registrieren und eigenes Geld
   (perspektivisch live) einsetzen.
5. **Der Weg zu Live ist ein bewusstes Gate.** Jede Bewegung Richtung echtem Handel
   verschärft Warn-, Bestätigungs- und Trust-Design, statt es zu lockern.

## Accessibility & Inclusion

Orthografisch korrektes Deutsch als Zugänglichkeits-Baseline. Mobile-Bedienbarkeit
ist real (44 px Touch-Ziele, Bottom-Nav bereits vorhanden) und bleibt Anforderung.
Kein darüber hinausgehender formaler Standard (z. B. WCAG-Stufe) wurde bislang
festgelegt — offen, nicht erfinden.
