---
name: Stockbot Dashboard (Liquid Glass, Light)
description: The /app/dashboard surface, dressed in the adopted Liquid Glass light world over the incumbent cockpit.
# This project ADOPTS an external system. The exhaustive token/component grammar
# lives upstream — see /static/liquid-glass.css (vendored) and the source below.
# Only project-specific application decisions are recorded here.
colors:
  # Project-added semantic money tokens (darkened for >=4.5:1 on light/solid glass).
  money-gain: "#0A6B3C"
  money-loss: "#A5122B"
  # Accents the dashboard leans on, inherited from the vendored system (do not fork values):
  iris-violet: "#997CE6"
  iris-teal: "#6EC4B9"
  ink: "#241F35"
  ink-muted: "#4E4763"
  ink-on-iris: "#1B2340"
  # CVD-safe categorical chart palette (--cat-1..6, coupled to chart_palette.py) —
  # a data-encoding contract preserved for multi-series charts, deliberately NOT
  # restyled into the accent violet/teal. Literal in Chart.js JS (canvas can't read CSS vars).
  cat-1: "#3987e5"
  cat-2: "#d95926"
  cat-3: "#199e70"
  cat-4: "#c98500"
  cat-5: "#d55181"
  cat-6: "#9085e9"
  # Chart chrome + mode-chip literals: mirror the --lg-* tokens / derived faint lines,
  # written literally because Chart.js / the canvas gauge cannot resolve CSS variables.
  chart-grid: "rgb(38 28 60 / .08)"
  mode-paper: "#8A5A0F"
  mode-shadow: "#3358B5"
  # Live-status pulse glow (connection heartbeat on the .pill dot) — --lg-success (#3E9E6E) at alpha.
  pulse-glow: "rgb(62 158 110 / .6)"
  pulse-glow-soft: "rgb(62 158 110 / .45)"
  # Glass edge highlight, sibling of --lg-edge (white at .5).
  glass-edge-half: "rgb(255 255 255 / .5)"
components:
  # Project-specific compositions built ON the vendored primitives.
  kpi-hero-iris:
    backgroundColor: "{colors.iris-violet}"
    textColor: "{colors.ink-on-iris}"
    rounded: "{rounded.md}"
    padding: "14px 16px"
  status-chip:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.pill}"
    padding: "0 13px"
    height: "36px"
---

# Design System: Stockbot Dashboard (Liquid Glass, Light)

## Overview

**Creative North Star: „Ruhige Glasinstrumente über warmem Licht"**

This is not a from-scratch world. The project **adopted** the external **Liquid Glass**
system in its **LIGHT variant, pinned** (no roll), and applied it to a single surface:
the `/app/dashboard`. The material truth is the vendored stylesheet at
`/static/liquid-glass.css`; the full token grammar, component library, and named rules
live upstream at `/home/jms/main_projekt/styles/liquid-glass/` (its own `DESIGN.md`).
**This file does not restate that grammar** — it records only the decisions this project
made while landing the world on one screen. When a rule below is unqualified, it is the
upstream rule; consult upstream for the full doctrine.

The thesis the build followed: a trading cockpit as calm glass panels over a warm cream
ground. It refuses both the dark dense-terminal default and the wall-of-tiles dashboard —
few large glass panels, dense data held on solid glass *inside* them, and exactly one
colored surface for the one number that matters.

**Key Characteristics:**

- Adopted, pinned world — Liquid Glass light; the vendored CSS is the source of truth.
- Applied to `/app/dashboard` only; every other route stays on the incumbent dark cockpit.
- One iris (colored) surface per view: the Gesamt-P&L tile. Everything else is glass.
- Semantic green/red for money only, darkened for legibility on light surfaces.
- German UI, orthographically correct; tabular numerals throughout.

## Colors

The palette is inherited wholesale from the vendored system (cool violet+teal gel accents,
warm cream mesh ground, violet-black ink). Two project-specific decisions only:

### Primary

- **Iris Violet** (`#997CE6`) and **Iris Teal** (`#6EC4B9`): inherited gel accents. On the
  dashboard they carry the iris KPI tile, the equity trace line (violet), badges (teal),
  active-tab and sort indicators (violet). Values are not forked — they resolve from
  `--lg-violet` / `--lg-teal`.

### Secondary — Semantic Money (project-added)

- **Darkened Gain Green** (`#0A6B3C`) and **Darkened Loss Red** (`#A5122B`): the system's
  own `--lg-success`/`--lg-error` sit around 4:1 on light glass and fail on the ~white
  solid-glass plate. The dashboard therefore defines its **own** darker money pair for
  `≥4.5:1` on light and solid surfaces. Used for P&L figures, gain/loss rows, ticker
  P&L bars, and the equity/cumulative curve stroke (green up / red down).

### Neutral

- **Ink** (`#241F35`) / **Ink-muted** (`#4E4763`): inherited. Ink-muted is also set as
  the Chart.js default text color; grid lines are `rgba(38,28,60,.08)`.

### Named Rules

**The Money-Only-Color Rule.** Green and red mean money, nothing else. Status lives in the
dot of a chip, never in a fill. (Inherited discipline; the darkened pair is the local means.)

**The Inherited-Palette Rule.** Do not fork or re-tint an upstream accent. If a color needs
to change for contrast, add a *named local* token (as with the money pair) rather than
redefining `--lg-*`.

## Typography

Inherited entirely from the vendored system: the platform humanist sans stack
(`-apple-system`…), sizes `--lg-size-display/title/body/small/micro`, tabular numerals on
all figures. The dashboard adds no faces and no scale steps. Chart.js is pointed at the same
system stack so canvas text matches the DOM. See upstream `DESIGN.md` for the full ramp.

## Layout

`main` is widened to `max-width: 1180px` for the cockpit (the incumbent app is 880px). The
composition is the project's own work over inherited materials:

- **Command bar** (`.ck-bar`, one `lg-glass` pane): brand · nav · mode chip · live pill ·
  refresh controls · timestamp. Sticky at the top.
- **KPI hero** (`.hero`): the gauge instrument (2:1) plus square KPI tiles (1:1) in one grid.
- **Content grid** (`.grid`, `1.45fr / 1fr`): few large `lg-panel` plates; collapses to one
  column under 900px. Dense tables/rows sit on **one** solid-glass surface inside a panel —
  never as many small tiles. This honors the upstream low-density mandate.

Spacing follows the inherited 4px rhythm (`--lg-space-*`).

## Elevation & Depth

Inherited unchanged: depth by refraction (blur + bright light-edge), neutral cast shadows
(`--lg-cast-1…3`) for height, colored caustic shadow **only** on gel bodies, `--lg-facet`
inner edges on every glass body. The dashboard adds no new shadow tokens; recessed data
surfaces (tables, search, selects) use the inherited `--lg-trough`.

## Shapes

Inherited. Panels `--lg-r-xl`, cards/command-bar `--lg-r-lg`, tiles/tables `--lg-r-md`,
all controls and chips are pills. Every glass surface keeps its 1px light-edge line, and
`backdrop-filter` is mandatory with it (the vendored `@supports` fallback makes glass opaque
where the filter is unsupported — do not ship glass without that fallback).

## Components

Only the project-specific compositions are documented here; the button/input/tab/switch/
toast primitives are the vendored ones (`lg-btn`, `lg-input`, …) — see upstream.

### KPI Hero — the one iris surface

The **Gesamt-P&L tile** (`.card-hero`, the `big` KPI) is the single iris (colored) surface
of the view — an iris gradient tile, **not** a full-width band. Its label and text carry
dark iris ink (`--lg-ink-on-iris`). Its P&L **value** sits on a solid-glass plate
(`--lg-glass-solid` pill) so the darkened green/red reads at contrast against the color.
This is the local expression of the upstream One-Iris-Surface rule.

### Command Bar & Status Chips

- **Mode chip** (`.ck-chip`): solid-glass pill; the color lives in the dot, the fill stays
  glass. Paper `#8A5A0F`, Shadow `#3358B5`, Live `--lg-error`. Bound to `trade_mode`.
- **Live pill** (`.pill`): JS toggles live / paused / offline; success/muted/error dot.
- **Icon buttons** (`.iconbtn`): glass discs for pause/refresh.
- **Logout**: the vendored `lg-btn lg-btn--glass lg-btn--sm`.

### Signature — Rohscore Gauge

The incumbent strategy raw-score gauge instrument is **kept** and reskinned from the dark
avionics bezel to a light glass card (`--lg-glass-solid` frame). Its zone arc uses the
Liquid-Glass semantic triad (error → warning → success); the needle color follows the score.

### Charts (Chart.js)

Chrome is themed to the world — ink-muted default text, violet equity fill/stroke, teal
badges, `rgba(38,28,60,.08)` grid, violet-tinted crosshair and break-even guides. The
**CVD-safe categorical palette** (`--cat-1..6`, coupled to `chart_palette.py`) is
**preserved for multi-series charts and NOT restyled** into the accent violet/teal — it is
the accessibility contract for distinguishing series and stays authoritative.

## Do's and Don'ts

### Do:

- **Do** treat `/static/liquid-glass.css` as the source of truth; add local CSS for
  *composition* (grid, rows, KPIs) only, and pull every material from the vendored tokens.
- **Do** use the darkened money pair (`#0A6B3C` / `#A5122B`) for P&L on any light or solid
  surface; the vendored `--lg-success/--lg-error` are too light there.
- **Do** keep exactly one iris surface per view (the P&L tile) and put its number on a
  solid-glass plate.
- **Do** keep the CVD-safe `--cat-*` palette for multi-series charts as-is.
- **Do** write orthographically correct German for every user-visible string.

### Don't:

- **Don't** roll or restyle the adopted world — it is pinned. Extend via named local tokens,
  never by re-tinting `--lg-*`.
- **Don't** apply the light glass world to other routes yet. base.html gates it on
  `active=='dashboard'` (adds `data-theme="light"`, `lg-body`, the glass command bar);
  every other page stays on the incumbent dark "Instrument Cockpit". The split is
  intentional — full migration is pending, not a defect to paper over.
- **Don't** color a glass fill for status; the color belongs in the chip's dot.
- **Don't** lay out many small glass tiles; hold dense data on one solid-glass surface.
- **Don't** re-introduce a static "Kill-Switch" status chip on the dashboard. The comp
  carried one (`Kill-Switch · Scharf`); it was **deliberately not shipped** because there is
  no data hook behind it — a hardcoded chip would assert a false safety status, violating
  the product's "no silent/false status" principle. A kill-switch indicator returns only
  when wired to real state.
