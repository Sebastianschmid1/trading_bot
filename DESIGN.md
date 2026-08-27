---
name: Stockbot Dashboard (Liquid Glass, Light + Dark)
description: The stockbot web app, dressed in the adopted Liquid Glass system — light and dark, user-switchable, system default.
# This project ADOPTS an external system. The exhaustive token/component grammar
# lives upstream — see /static/liquid-glass.css (vendored) and the source below.
# Only project-specific application decisions are recorded here. The vendored file
# already ships light+dark --lg-* tokens; this project's own local tokens (below)
# now carry matching light+dark pairs too — see "Dark Mode" further down.
colors:
  # Project-added semantic money tokens (own light/dark pair, each >=4.5:1 on
  # solid glass in its own mode — see "Dark Mode" for the dark contrast numbers).
  money-gain: "#0A6B3C"        # light
  money-loss: "#A5122B"        # light
  money-gain-dark: "#4FDC9E"
  money-loss-dark: "#FF7A8E"
  # Accents the dashboard leans on, inherited from the vendored system (do not fork values):
  iris-violet: "#997CE6"
  iris-teal: "#6EC4B9"
  ink: "#241F35"
  ink-muted: "#4E4763"
  ink-on-iris: "#1B2340"
  # CVD-safe categorical chart palette (--cat-1..6, coupled to chart_palette.py) —
  # a data-encoding contract preserved for multi-series charts, deliberately NOT
  # restyled into the accent violet/teal, and IDENTICAL in light and dark (a
  # data-encoding contract, not a material — it must not shift with the theme).
  # Literal in Chart.js JS (canvas can't read CSS vars).
  cat-1: "#3987e5"
  cat-2: "#d95926"
  cat-3: "#199e70"
  cat-4: "#c98500"
  cat-5: "#d55181"
  cat-6: "#9085e9"
  # Chart chrome + mode-chip literals: mirror the --lg-* tokens / derived faint lines,
  # written literally because Chart.js / the canvas gauge cannot resolve CSS variables —
  # JS reads the live --chart-*/--mode-*/--money-* custom properties at draw time instead
  # (getComputedStyle), so these literals are the LIGHT values only; dark has its own pair.
  chart-grid: "rgb(38 28 60 / .08)"
  chart-grid-dark: "rgb(242 238 255 / .12)"
  mode-paper: "#8A5A0F"
  mode-shadow: "#3358B5"
  mode-paper-dark: "#E5A23D"
  mode-shadow-dark: "#7FA8FF"
  # Live-status pulse glow (connection heartbeat on the .pill dot) — color-mix() off the
  # theme's own --lg-success, so it needs no separate dark value.
  pulse-glow: "color-mix(in srgb, var(--lg-success) 60%, transparent)"
  pulse-glow-soft: "color-mix(in srgb, var(--lg-success) 45%, transparent)"
  # Glass edge highlight, sibling of --lg-edge (white at .5) — deliberately theme-invariant,
  # see "Dark Mode · What stays fixed on purpose".
  glass-edge-half: "rgb(255 255 255 / .5)"
  # .btn--live text-on-fill ink. Light danger (#A5122B) is dark enough for white text;
  # dark danger (#FF7A8E) is light and needs a dark ink instead (see contrast numbers below).
  danger-ink: "#fff"
  danger-ink-dark: "#2A0F14"
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
system in its **LIGHT variant, pinned** (no roll). It was landed on the `/app/dashboard`
first, then rolled out **app-wide** via base.html. The material truth is the vendored stylesheet at
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

- Adopted, pinned world — Liquid Glass, light **and** dark; the vendored CSS is the source
  of truth for both (it already ships both palettes — see "Dark Mode" below).
- Applied app-wide via base.html (global `lg-body`); the incumbent dark **avionics** cockpit
  is fully replaced by Liquid Glass — which itself has a dark mode, not to be confused with
  the old cockpit.
- One iris (colored) surface per view: the Gesamt-P&L tile. Everything else is glass.
- Semantic green/red for money only, each mode's pair own-darkened/-lightened for legibility
  on that mode's solid glass.
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

Project-specific compositions are documented here. `.btn` (below) is **not** a vendored
primitive — it is this project's own components.css rule, built on top of vendored materials
(`--lg-*` tokens); the remaining input/tab/switch/toast look-and-feel still comes straight from
the vendored classes (`lg-*`) — see upstream.

### Command Bar & Status Chips

- **Mode chip** (`.ck-chip`): solid-glass pill; the color lives in the dot, the fill stays
  glass. Paper `#8A5A0F`/`#E5A23D` (light/dark), Shadow `#3358B5`/`#7FA8FF`, Live `--lg-error`.
  Bound to `trade_mode`.
- **Live pill** (`.pill`): JS toggles live / paused / offline; success/muted/error dot.
- **Icon buttons** (`.iconbtn`): glass discs for pause/refresh, same disc for the theme toggle
  (`.theme-toggle`, sun/moon, see "Dark Mode").
- **Logout**: `.btn .btn--sm` (E6 — previously the vendored `lg-btn lg-btn--glass lg-btn--sm`,
  which carried the same translucent-glass contrast problem as every other blank `<button>`,
  see "Buttons — eine solide Glasfläche statt der Vendor-Scheibe" further down).
- **Kill-Switch chip** (agent/UI-KILLSWITCH-VISIBLE, base.html's `.appbar` only — every page
  except the dashboard, which keeps its own richer `.ck-bar`): reuses the existing
  `chip(label, 'warn', href, title)` component unchanged, the same tone settings.html already
  uses for its own kill-switch chip (`--danger` on `--danger-soft`, already ≥4.5:1 in both
  themes per "Kontrast-Härtung" below — no new token). Renders **only** while a kill-switch is
  active (global, or this user's own); silent otherwise, per the "no silent/false status"
  rule. States both required facts in the visible label ("keine neuen Positionen, Schutz-
  Verkäufe laufen weiter"), links to `/app/settings` (where it's switched off), and carries
  the stored `reason` as `title` (plus inline, appended after `·`, when short enough to fit
  the pill). Sourced from `webapp.py::_render` via `_kill_switch_banner_status` — read-only,
  same public `KillSwitchService` getters `settings.html`'s route already calls.

### Signature — Rohscore Gauge

The incumbent strategy raw-score gauge instrument is **kept** and reskinned from the dark
avionics bezel to a light glass card (`--lg-glass-solid` frame). Its zone arc uses the
Liquid-Glass semantic triad (error → warning → success); the needle color follows the score.

### Charts (Chart.js)

Chrome is themed to the world — ink-muted default text, violet equity fill/stroke, teal
badges, faint ink grid, violet-tinted crosshair and break-even guides. Canvas can't resolve
CSS variables, so every chart color is read at draw time via
`getComputedStyle(document.documentElement).getPropertyValue(...)` instead of being literal —
see "Dark Mode · Charts" for the mechanism and the redraw trigger. The **CVD-safe categorical
palette** (`--cat-1..6`, coupled to `chart_palette.py`) is **preserved for multi-series
charts and NOT restyled** into the accent violet/teal, and is the one chart color family
that stays **identical in light and dark** — it is the accessibility contract for
distinguishing series, and a data-encoding contract must not shift with the theme.

## Dark Mode

Liquid Glass ships both palettes upstream (`@media (prefers-color-scheme: dark)` plus
`:root[data-theme="dark"]` in `liquid-glass.css`) — the `--lg-*` material tokens already
flip themselves. What didn't flip, before this change, were this project's own **local**
tokens layered on top in base.html's `.lg-body` block: hairline borders, the money pair, the
mode-chip pair, and the Chart.js/SVG chrome literals were hardcoded to their light values.
Those now carry a light value and a dark override, following the exact two-selector pattern
`liquid-glass.css` itself uses (`@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .lg-body { … } }`
plus `:root[data-theme="dark"] .lg-body { … }`), so an explicit dark choice always wins over
system preference, and no attribute at all means "follow the system" (the default).

### State & switch

- **Three states**, one `data-theme` attribute on `<html>`: `light`, `dark`, or absent
  (= System, the default — `prefers-color-scheme` decides). Persisted in `localStorage`
  (`stockbot:theme`) once the user picks one explicitly.
- **No FOUC**: a small inline `<script>` at the very top of `<head>`, before the stylesheet
  links, sets `data-theme` from `localStorage` synchronously — the attribute is on the
  element before the browser has anything to paint. No framework, no extra asset.
- **The switch** (`.theme-toggle`, `id="themeToggle"`): the same glass icon-button/disc as
  the dashboard's existing `.iconbtn` (pause/refresh) — one control pattern, not a new one.
  Sun/moon SVG visibility is pure CSS (`:root[data-theme="dark"] .theme-toggle .icon-sun {
  display:none }`, mirrored under the system-preference `@media` block) — no flash of the
  wrong icon. `aria-pressed` reflects the *effective* theme (explicit or system-derived) and
  is kept in sync by `window.stockbotTheme`, a small shared module in base.html that both the
  appbar's and the dashboard's `.ck-bar`'s toggle call into. Clicking sets an explicit
  `light`/`dark` choice (abandoning "System" from then on, the common toggle convention);
  `aria-label="Dunkelmodus umschalten"`, keyboard-operable (`<button>`), visible
  `:focus-visible` ring inherited from the vendored focus rule.
- Every explicit or system-driven change dispatches `document` event
  `stockbot:themechange` — the hook charts and the reports SVG use to redraw (below).

### Local tokens: light value → dark value

All declared in base.html's `.lg-body` block (light) plus its two dark blocks; consumed via
`var(--token)` in CSS or `getComputedStyle` in JS. `--danger`/`--green`/`--red` stay as
aliases of `--money-loss`/`--money-gain` for old call sites, so they flip for free.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--border`, `--line`, `--edge` | `rgb(38 28 60 / .12–.14)` | `rgb(242 238 255 / .16–.20)` | generic hairlines |
| `--sep`, `--grid` | `rgb(38 28 60 / .08–.10)` | `rgb(242 238 255 / .12–.14)` | table rows, chart grid |
| `--border-subtle/-default/-strong` | `rgb(38 28 60 / .10–.26)` | `rgb(242 238 255 / .14–.32)` | components.css inputs/tabs/dialogs |
| `--money-gain` / `--money-loss` | `#0A6B3C` / `#A5122B` | `#4FDC9E` / `#FF7A8E` | P&L text, bars, equity stroke |
| `--money-gain-rgb` / `--money-loss-rgb` | `10 107 60` / `165 18 43` | `79 220 158` / `255 122 142` | translucent P&L area fills |
| `--danger-ink` | `#fff` | `#2A0F14` | text on a **solid** `--danger` fill (`.btn2--live`) |
| `--mode-paper` / `--mode-shadow` | `#8A5A0F` / `#3358B5` | `#E5A23D` / `#7FA8FF` | `.ck-chip.is-paper/.is-shadow` (Chip-Punktfarbe, KEIN Fließtext — siehe `--tone-warning-fg` unten für `#sizing-hint`) |
| `--overlay` / `--overlay-strong` | `rgb(38 28 60 / .38–.45)` | `rgb(8 6 14 / .55–.62)` | modal/scan-overlay backdrops |
| `--chart-crosshair`, `--chart-tick`, `--chart-breakeven-line/-label` | ink-based, light alpha | light-ink-based, matching alpha | Chart.js/canvas-only chrome |
| `--bg-hover`, `--bg-selected`, `--primary-soft/-border`, `--info-soft` | `rgb(var(--lg-violet-rgb) / X)` | same formula, `--lg-violet-rgb` already flips | hover/selected tints — **no dark override needed**, they ride the vendor accent |
| `--success-soft/-border`, `--warning-soft/-border`, `--danger-soft/-border` | `color-mix(in srgb, var(--lg-success/-warning) X%, transparent)` | same formula, alpha raised slightly | soft semantic fills — ride `--lg-success/-warning`, which already flip |

Two rows above are intentionally formula-based rather than hardcoded pairs: whatever already
resolves through `--lg-violet-rgb` or `color-mix()` off a vendor semantic color needs no
second dark literal, because the vendor token underneath already flips. New local tokens
were only added where the value doesn't derive from anything the vendor already themes
(hairlines, money, mode chips, overlays, chart-only chrome).

### Contrast (WCAG 2.1, computed against each mode's own solid-glass surface)

Solid-glass surface approximated by compositing `--lg-glass-solid` over the body mesh
gradient (light ≈ `rgb(247 244 243)`, dark ≈ `rgb(30 26 45)`); ratios below use that
composite, not the raw bare-background color.

| Pair | Ratio | Needs |
|---|---|---|
| `--money-gain` light `#0A6B3C` on light solid glass | 6.04:1 | ≥4.5:1 ✓ |
| `--money-loss` light `#A5122B` on light solid glass | 7.06:1 | ≥4.5:1 ✓ |
| `--money-gain` dark `#4FDC9E` on dark solid glass | 9.75:1 | ≥4.5:1 ✓ |
| `--money-loss` dark `#FF7A8E` on dark solid glass | 6.81:1 | ≥4.5:1 ✓ |
| `--mode-paper` light `#8A5A0F` on light solid glass | 5.41:1 | ≥4.5:1 ✓ |
| `--mode-shadow` light `#3358B5` on light solid glass | 6.00:1 | ≥4.5:1 ✓ |
| `--mode-paper` dark `#E5A23D` on dark solid glass | 7.73:1 | ≥4.5:1 ✓ |
| `--mode-shadow` dark `#7FA8FF` on dark solid glass | 7.23:1 | ≥4.5:1 ✓ |
| white `#fff` on dark `--danger` fill `#FF7A8E` (`.btn2--live`, unfixed) | 2.49:1 | fails — why `--danger-ink` exists |
| `--danger-ink` dark `#2A0F14` on `--danger` fill `#FF7A8E` | 7.16:1 | ≥4.5:1 ✓ |
| `--lg-ink` dark `#F2EEFF` on dark solid glass | 14.91:1 | ≥4.5:1 ✓ |
| `--lg-ink-muted` dark `#B8B0D0` on dark solid glass | 8.21:1 | ≥4.5:1 ✓ |
| vendor `--lg-success` dark `#6FD3A0` / `--lg-error` dark `#F0899C` on dark solid glass | 9.29:1 / 7.08:1 | ≥4.5:1 ✓ (unchanged, vendor-owned) |

Hairlines (`--border`/`--sep`/`--grid`/…) are decorative row/panel dividers, not the
"UI component boundary" WCAG 1.4.11 targets with 3:1 (that's the vendored `--lg-edge`/
`--lg-focus`, unchanged, upstream's responsibility) — their dark alphas were chosen to match
the *same* low-contrast-by-design weight the shipped light hairlines already have (≈1.2–1.7:1
against their own surface), not invented from scratch.

### Charts

Chart.js/canvas/inline-SVG code cannot read CSS custom properties, so every color that used
to be a literal hex in `dashboard.html`'s `<script>` and `reports.html`'s SVG-string builder
now goes through a small helper (`cssVar()` in dashboard.html, `themeColor()` in reports.html)
that calls `getComputedStyle(document.documentElement).getPropertyValue(name).trim()` at
**draw time** — never cached, so a redraw always picks up whatever theme is active then.
`Chart.defaults.color`/grid/legend/gauge-zone/gauge-tick/crosshair/break-even colors, the
equity/ticker/trades/price/factor chart series and fills, and the reports equity SVG (axis
text, baseline, benchmark line, up/down stroke, legend) all route through it. On
`stockbot:themechange`, dashboard.html re-applies `Chart.defaults` and re-runs `render(state.data)`
— every `render*` function reads its colors fresh, so all charts and the Rohscore gauge
repaint with the new theme without a page reload; reports.html keeps the last-drawn equity
payload and redraws the same SVG with the new palette. The one exception left as a
static server-rendered value: the 7-day sparkline in `app.html` — that's plain inline SVG
with no JS driving it, so its stroke color moved from an inline `stroke="#hex"` attribute to
a CSS class (`.spark-up`/`.spark-down`, `stroke:var(--money-gain)`), which **is** theme-aware
because SVG presentation attributes lose to a CSS declaration — no JS needed for that one.

### What stays fixed on purpose

- **`--cat-1..6`** — the CVD-safe categorical palette. A data-encoding contract, not a
  material; shifting it with the theme would break the accessibility guarantee it exists for.
  Unchanged, and `tests/test_chart_palette_parity.py` still couples it to `chart_palette.py`.
- **The iris hero tile** (`.card-hero`) and its white `rgb(255 255 255 / .5)` edge — the
  vendored `--lg-iris-1/-2`/`--lg-pink`/`--lg-pink-rgb` tokens are not redefined in
  `liquid-glass.css`'s dark blocks either, so the one colored gel surface in the view stays
  the same vivid gradient in both modes; only the money figure sitting on its solid-glass
  plate (`.card-hero .value`) needs — and gets — the mode's own money color.
- **`stockbot/backtest/report.py`** (Matplotlib PNGs) and **`stockbot/core/chart_palette.py`**
  — untouched, out of scope, and not theme-driven (server-rendered images, not the live app).

## Do's and Don'ts

### Do:

- **Do** treat `/static/liquid-glass.css` as the source of truth; add local CSS for
  *composition* (grid, rows, KPIs) only, and pull every material from the vendored tokens.
- **Do** use each mode's own money pair (`--money-gain`/`--money-loss`, light `#0A6B3C`/
  `#A5122B`, dark `#4FDC9E`/`#FF7A8E`) for P&L on any solid surface; the vendored
  `--lg-success/--lg-error` are tuned for status dots, not money text, in either mode.
- **Do** keep exactly one iris surface per view (the P&L tile) and put its number on a
  solid-glass plate.
- **Do** keep the CVD-safe `--cat-*` palette for multi-series charts as-is, identical in
  both modes.
- **Do** read Chart.js/canvas/SVG colors via `getComputedStyle` at draw time, never bake a
  hex into JS/inline-SVG — canvas can't see CSS variables, so that's the only way a chart
  follows the theme (see "Dark Mode · Charts").
- **Do** write orthographically correct German for every user-visible string.

### Don't:

- **Don't** roll or restyle the adopted world — it is pinned. Extend via named local tokens,
  never by re-tinting `--lg-*`.
- **Don't** re-introduce the incumbent **avionics** cockpit (the pre-migration dark theme).
  Liquid Glass itself has a light *and* a dark mode — both are the adopted world; `lg-body`
  is global on every page, `data-theme` just picks which of the vendored palettes is active
  (see "Dark Mode"). Only the shared header is gated `active != 'dashboard'`, because the
  dashboard renders its own richer command bar (`.ck-*`); every other page uses the base
  glass command bar (`.appbar`).
- **Don't** build a second theming mechanism next to `data-theme`/`prefers-color-scheme` —
  extend the two-block dark pattern already used throughout (`@media` block for System +
  `:root[data-theme="dark"]` for the explicit choice), matching how `liquid-glass.css`
  itself does it.
- **Don't** color a glass fill for status; the color belongs in the chip's dot.
- **Don't** lay out many small glass tiles; hold dense data on one solid-glass surface.
- **Don't** hardcode a "Kill-Switch" status chip. This bullet used to justify leaving one out
  entirely (the original comp's `Kill-Switch · Scharf` chip had no data hook — a hardcoded
  chip would have asserted a false safety status). **That reasoning is now overtaken:**
  agent/UI-KILLSWITCH-VISIBLE wired a real hook (`webapp.py::_render` →
  `_kill_switch_banner_status`, reading `KillSwitchService.global_status`/`.user_status` —
  display-only, no new TSAFE logic) and the base-`.appbar` chip (`chip(..., 'warn', href=...)`,
  see "Command Bar & Status Chips" below) now shows *only* while a kill-switch is actually
  active, sourced from that same real state. The rule stands only in its literal form now:
  never fake the chip from a static value.

## Kontrast-Härtung (agent/UI-CONTRAST)

A binding design review found four hard contrast blockers (WCAG 2.1, measured against the
**actual composite background**, not an idealized surface) plus one confirmation-dialog
blocker. All are fixed with new local tokens, following the existing `--money-gain`/
`--money-loss` pattern: where a vendor accent (`--lg-*`) is used as **text** and doesn't
clear 4.5:1 on its own surface, that spot gets its own light/dark pair instead of forking
`--lg-*`. `liquid-glass.css` and `--cat-1..6` are untouched.

### Why `--lg-success`/`--lg-warning` don't work as text color

Both vendor semantic colors are tuned for **dots and thin fills** (the "status lives in the
dot, never in a fill" rule), not for body text or badge text on their own soft tint. Two
independent failure modes compound:

1. **`--lg-success` cannot reach AA as text even on a best-case surface.** `--lg-success`
   light `#3E9E6E` on **pure white** is only 3.32:1 — below the 4.5:1 text minimum before any
   real (slightly warm, slightly translucent) surface is even considered. No amount of
   background tuning fixes this while keeping the vendor hex as the ink; the token is
   accessible as a small dot/border (≥3:1, non-text) but not as text.
2. **Text-on-its-own-soft-tint is a self-similar-luminosity trap.** `--warning-soft`/
   `--info-soft`/`--success-soft` are `color-mix()`/`rgba()` of the *same* accent at 13–18%
   over the surface — so the accent-as-text sits on a background that is itself mostly that
   accent's hue, just diluted. Diluting a light-ish accent toward the (light) surface raises
   background luminance faster than the unmodified accent-as-text can keep pace with, so the
   two converge well short of 4.5:1 (measured 2.15–2.84:1 in Light Mode for Paper/Shadow/
   Backtest/chip--caution/chip--go; **money**'s already-darkened pair (`--money-loss`) avoids
   this because it starts far enough from its own soft-tint's luminance).

### New tokens: light value → dark value

All declared in base.html's `.lg-body` block (light) plus its two dark blocks, next to the
existing money/mode-chip tokens; consumed via `var(--token)` in CSS. Fallback literals
(`var(--token, #hex)`) are given at the two `tokens.css`/`components.css` call sites so they
degrade sensibly if ever used outside `.lg-body`.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--badge-teal-ink` | `var(--lg-ink-on-teal)` (unchanged) | `var(--lg-teal)` | `.dir.long`, `.badge` (teal-tinted pill text) |
| `--tone-warning-bg` / `-fg` | `#F5E6C8` / `#6B430A` | `#4A3416` / `#FFD699` | `.mode-badge--paper`, `.chip--caution`; `-fg` allein (ohne `-bg`) auch als Textfarbe von `#sizing-hint` (dashboard.html) auf `--lg-glass-solid` — 7.42:1 Light / 11.90:1 Dark am schlechtesten Mesh-Punkt (`.panel`-Methodik) |
| `--tone-violet-bg` / `-fg` | `#ECE6FB` / `#443592` | `#322A54` / `#D8CCFB` | `.mode-badge--shadow` |
| `--tone-indigo-bg` / `-fg` | `#E7E4FE` / `#3F3AAE` | `#2E2B57` / `#CFCBFF` | `.mode-badge--backtest` |
| `--tone-success-bg` / `-fg` | `#DCEEE4` / `#146C43` | `#1B3A2C` / `#8CE6BB` | `.chip--go` |
| `--status-ink-success` | `#2E7552` | `var(--lg-success)` (unchanged, already ≥4.5:1) | `--lg-success`-as-text spots (Blocker 4) |
| `--status-ink-error` | `#9C3043` | `var(--lg-error)` (unchanged, already ≥4.5:1) | `--lg-error`-as-text spots (Blocker 4) |

The `--tone-*` pairs are **opaque, self-contained** colors (not a translucent tint over an
unknown backdrop) — chosen so the contrast is deterministic and doesn't depend on what glass
layers happen to sit behind the badge/chip. `--mode-paper`/`--mode-shadow` (the dashboard's
own richer `.ck-chip`, on solid glass) and `--danger`/`--danger-soft` (already routed through
the darkened `--money-loss`) needed no change — both already clear 4.5:1 in both themes.

### Contrast: before → after (WCAG 2.1, real composite background)

Methodology: solid-glass surface approximated the same way the existing Dark-Mode table
does — `--lg-glass-solid` composited over the body mesh (light ≈ `rgb(247 244 243)`, dark ≈
`rgb(30 26 45)`); soft tints are `color-mix()`/`rgba()` of the accent composited over that
same surface at their declared alpha. This reproduces the review's own numbers exactly
(validated against 3.04:1, 4.35:1, 1.40:1, 10.97:1, 3.32:1-on-white — all matched to 2
decimals), so it's used throughout below. New `--tone-*` values are opaque, so their ratio is
exact regardless of backdrop.

| # | Spot | Before | After | Needs |
|---|---|---|---|---|
| 1 | `.dir.long`/`.badge` teal-tint text, **Dark** | `--lg-ink-on-teal` `#0E2F2A` on 22% teal-over-dark-glass `#33424F` → **1.40:1** | `--badge-teal-ink` = `--lg-teal` `#7FD2C6` on same bg → **5.84:1** | ≥4.5:1 |
| 1 | same, Light (unchanged) | `--lg-ink-on-teal` `#0E332E` on 22% teal-over-light-glass `#D9E9E6` → **10.97:1** | unchanged | ≥4.5:1 ✓ already |
| 2 | `.mode-badge--paper`, Light | `--lg-warning` `#B87A18` on `--warning-soft` → **2.31–2.84:1** (range measured) | `--tone-warning-fg` `#6B430A` on `--tone-warning-bg` `#F5E6C8` → **7.00:1** | ≥4.5:1 |
| 2 | `.mode-badge--paper`, Dark | `--lg-warning` dark on `--warning-soft` dark → **4.30:1** | `--tone-warning-fg` `#FFD699` on `--tone-warning-bg` `#4A3416` → **8.55:1** | ≥4.5:1 |
| 2 | `.mode-badge--shadow`, Light | `--lg-violet` `#997CE6` on `--info-soft` → **2.15–2.66:1** | `--tone-violet-fg` `#443592` on `--tone-violet-bg` `#ECE6FB` → **7.96:1** | ≥4.5:1 |
| 2 | `.mode-badge--shadow`, Dark | `--lg-violet` dark on `--info-soft` dark → **3.16:1** | `--tone-violet-fg` `#D8CCFB` on `--tone-violet-bg` `#322A54` → **8.75:1** | ≥4.5:1 |
| 2 | `.mode-badge--backtest`, Light+Dark | hardcoded `#9B8CFF` on `rgba(155,140,255,.14)`, unthemed → **1.83:1 / 3.62:1** | `--tone-indigo-fg` `#3F3AAE`/`#CFCBFF` on `--tone-indigo-bg` `#E7E4FE`/`#2E2B57` → **6.97:1 / 8.53:1** | ≥4.5:1 |
| 2 | `.mode-badge--live`, Light+Dark (unchanged) | `--danger` on `--danger-soft` → **4.60:1 / ok** | unchanged | ≥4.5:1 ✓ already |
| 3 | `.chip--go`, Light | `--lg-success` `#3E9E6E` on `--success-soft` → **2.64:1** (and only 3.32:1 even on pure white — AA unreachable with this token) | `--tone-success-fg` `#146C43` on `--tone-success-bg` `#DCEEE4` → **5.34:1** | ≥4.5:1 |
| 3 | `.chip--go`, Dark (unchanged, already ok) | `--lg-success` dark on `--success-soft` dark → ok | `--tone-success-fg` `#8CE6BB` on `--tone-success-bg` `#1B3A2C` → **8.38:1** | ≥4.5:1 |
| 3 | `.chip--caution`, Light | `--lg-warning` on `--warning-soft` → **2.84:1** | `--tone-warning-fg`/`-bg` (shared with Paper-Badge, same pair) → **7.00:1** | ≥4.5:1 |
| 3 | `.chip--caution`, Dark | ok-ish per review, hardened anyway for consistency | `--tone-warning-fg`/`-bg` dark → **8.55:1** | ≥4.5:1 |
| 3 | `.chip--warn`, both themes (unchanged) | `--danger` on `--danger-soft` → **5.71:1 / ok** | unchanged | ≥4.5:1 ✓ already |
| — | `.chip--warn` reused for the appbar Kill-Switch chip (agent/UI-KILLSWITCH-VISIBLE), computed precisely | `--danger` `#A5122B` on `--danger-soft` (12% over solid-glass `rgb(247 244 243)`) → **5.71:1 Light**; `--danger` `#FF7A8E` on `--danger-soft` (14% over solid-glass `rgb(30 26 45)`) → **5.40:1 Dark** | unchanged (same token, no new spot) | ≥4.5:1 ✓ both |
| 4 | `--lg-success`/`--lg-error` as text, Light (`#error`, `.pill.live/.offline`, `.ck-chip.is-live/.is-armed`, `button.red`, `.win/.lose`, `.kind.applied/.rejected`, `.ok`) | `#3E9E6E` / `#C6455C` on light solid glass → **3.04:1 / 4.35:1** | `--status-ink-success` `#2E7552` / `--status-ink-error` `#9C3043` on same bg → **5.07:1 / 6.58:1** | ≥4.5:1 |
| 4 | same, Dark (unchanged, already ok) | `--lg-success`/`--lg-error` dark on dark solid glass → **9.29:1 / 7.08:1** (existing Dark-Mode table) | unchanged (`--status-ink-*` dark = `var(--lg-success/-error)`) | ≥4.5:1 ✓ already |

### Cascade fix: `color-scheme`

`tokens.css`'s bare `:root { color-scheme: dark }` (selector specificity 0,1,0 — a leftover
from the pre-migration dark avionics default) beat base.html's `html { color-scheme: light
dark }` (specificity 0,0,1) **regardless of load order**, because higher specificity always
wins within the same origin. Verified by cascade math, not by rendering: `:root` and `html`
both match the root element, `:root`'s pseudo-class contributes a class-level specificity
point that the bare type selector `html` doesn't have, so `:root`'s declaration always won
for the un-attributed (System, no `[data-theme]`) case — the explicit-choice rules
(`html[data-theme="light|dark"]`, specificity 0,1,1) were never affected, only the default.

**Effect confirmed:** in System mode (no explicit theme choice — the default state for every
new visitor), the page rendered its light Liquid Glass look while every native UA widget
(`select` dropdowns, scrollbars, checkboxes, autofill, the `input[type=date]` calendar icon)
rendered in **dark** chrome, because the browser's `color-scheme` hint said `dark`
unconditionally. This is exactly the mismatch flagged in the task.

**Fix:** removed the stray `color-scheme: dark;` line from `tokens.css`'s `:root` (it
predates the light/dark toggle system and is fully superseded by base.html's three rules,
which already correctly cover System/light/dark). No `liquid-glass.css` change needed — that
file never declared `color-scheme` itself.

### Confirmation dialogs (Blocker 8, §18.1)

`settings.html` ("Alpaca-Verbindung entfernen", "Demo zurücksetzen") and `lab.html` ("Als
Live-Override übernehmen") now open a `dialog2` instead of a native `confirm()`, via a small
shared, declarative helper added to base.html (`form[data-confirm-dialog]` → opens the named
`<dialog class="dialog2">`, with the same focus-trap / initial-focus-on-cancel / Enter-safe-
confirm-button / focus-restore behavior as the existing `#tradeConfirm` dialog in app.html —
app.html's own richer, per-row dynamic dialog is untouched). Each dialog **lists its concrete
consequences** instead of stating them in the surrounding prose:

- **Alpaca-Verbindung entfernen** — löscht die gespeicherten API-Zugangsdaten, deaktiviert
  sofort die echte Broker-Order-Ausführung, offene Positionen bleiben (unsteuerbar) bestehen.
- **Demo zurücksetzen** — löscht alle Trades/Verlauf/Mitteilungen, schließt offene
  Alpaca-Positionen (falls echte Broker-Ausführung aktiv), Einstellungen/Alpaca-Verbindung
  bleiben erhalten.
- **Live-Override übernehmen** (lab.html) — nennt den konkreten Parameter samt altem/neuem
  Wert, dass es ab dem nächsten Bot-Zyklus (~20 s) gilt, und dass der bisherige Wert danach
  nicht automatisch wiederhergestellt wird.

The kill-switch toggle and the dashboard-link-rotate `confirm()` in `settings.html` were left
untouched — out of scope (no money movement, not among the three named blockers).

## Kontrast-Härtung: Fließtext auf dichten Flächen (agent/UI-READABLE-GROUND)

A binding design review found one blocker plus two related findings — same root cause: body
text sitting on a background whose contrast isn't predictable, either because the surface
itself is translucent (`.panel`) or because there is no surface at all (bare page ground under
`main`). `liquid-glass.css:303` states the rule the codebase had one exception to: *"Flaechen,
die Fliesstext tragen, sind deutlich dichter — sonst laeuft der Grund durch die Schrift und der
Kontrast ist nicht mehr vorhersagbar."* The vendored file is unchanged; the fix is either
routing the affected spot onto the existing `--lg-glass-solid` recipe (already used by `.card`,
`.table-scroll`, `dialog#modal`, `.ck-chip`, `.pill`, `.tab`) or, where there genuinely is no
surface to move onto, a new local, opaque token — same pattern as `--money-gain`/`--money-loss`
and the `--tone-*`/`--status-ink-*` pairs above.

`body.lg-body`'s mesh background is `background-attachment: fixed` (viewport-anchored), so a
panel's composite background changes as the page scrolls underneath it — every ratio below is
therefore checked against **all four** mesh stops (`--lg-bg-1..4`), not just the best case, and
the worst stop is called out explicitly.

### Blocker: `.panel` (dashboard.html)

Before, `.panel` used the three-stop translucent gradient (`--lg-glass-hi .38` →
`--lg-glass .16` → `--lg-glass-lo .10`) meant for *decorative* glass, not text-bearing glass.
`.desc`/`.empty`/the `replaceCanvas` chart-failure message all sit on it in `--lg-ink-muted`.

| Mesh point | Light: before (composite, worst stop) | Light: after | Dark: before (composite, worst stop) | Dark: after |
|---|---|---|---|---|
| `--lg-bg-1` | bottom `rgb(244,232,222)` → **7.25:1** | `rgb(253,250,248)` → **8.40:1** | top `rgb(90,84,108)` (worst overall) → **3.48:1** ❌ | `rgb(34,29,49)` → **7.89:1** |
| `--lg-bg-2` | bottom `rgb(224,208,202)` → **5.84:1** | `rgb(249,246,244)` → **8.11:1** | top `rgb(73,68,86)` → **4.53:1** | `rgb(30,25,44)` → **8.26:1** |
| `--lg-bg-3` | bottom `rgb(178,166,166)` (worst overall) → **3.70:1** ❌ | `rgb(240,237,237)` → **7.50:1** ✓ | top `rgb(58,55,67)` → **5.62:1** | `rgb(27,23,40)` → **8.47:1** ✓ |
| `--lg-bg-4` | bottom `rgb(206,191,186)` → **4.90:1** | `rgb(245,242,241)` → **7.84:1** | top `rgb(76,70,95)` → **4.33:1** ❌ | `rgb(31,26,46)` → **8.16:1** |

("Before" picks whichever of the gradient's three stops (`.38`/`.16`/`.10` light,
`.16`/`.07`/`.04` dark) is worst at that mesh point — the gradient sweeps through all three
over the panel's height, so every stop is a real on-screen position, not a hypothetical. Light's
worst stop is uniformly the bottom edge (`--lg-glass-lo .10`, thinnest wash); Dark's worst stop
is uniformly the top edge (`--lg-glass-hi .16`, paradoxically the *most* opaque stop, because in
Dark Mode the overlay is white-tinted and lightens a dark mesh toward mid-grey, which is worse
for the light Dark-Mode ink than staying close to the mesh's own near-black value). Overall
worst point: **Light `--lg-bg-3`, bottom edge → 3.70:1**; **Dark `--lg-bg-1`, top edge →
3.48:1** — both below the 4.5:1 floor, confirming the review's own measurements (3.69 / 3.49).

**Fix chosen: the preferred path** — `.panel` now uses `background:var(--lg-glass-solid)` +
`border-color:var(--lg-edge-soft)`, i.e. the same recipe `liquid-glass.css`'s own
`.lg-glass--solid`/`.lg-card--solid` apply (radius/shadow are unchanged, still `--lg-r-xl` /
`--lg-cast-3`, per the surrounding `.lg-panel` sizing — only the fill and border color move to
the solid variant). This was possible without any visible-design cost (no fallback to raising
`.desc`/`.empty` to `--lg-ink` was needed): `≥7.50:1` in both themes at the worst mesh point,
comfortably above the 4.5:1 floor with headroom for the blur/saturate filter's minor color
shift. `.desc`/`.empty` keep `--lg-ink-muted` unchanged — the surface moved, not the ink.

### Related 1: `--lg-ink-muted` on the bare page ground

`main` has no surface of its own; `.muted` (base.html, used across every template) and two
page-local rules with the same shape — `.filters legend` (`reports.html`) and `.note`
(`lab.html`, only its one out-of-card instance, "So arbeitet das Labor…") — sit directly on the
mesh in `--lg-ink-muted`.

| Mesh point | Light: `--lg-ink-muted` (before) | Light: `--ink-muted-ground` (after) | Dark: `--lg-ink-muted` |
|---|---|---|---|
| `--lg-bg-1` | **7.13:1** | **9.59:1** | **5.72:1** ✓ (dark unchanged, already passing) |
| `--lg-bg-2` | **5.58:1** | **7.51:1** | **7.57:1** ✓ |
| `--lg-bg-3` (worst) | **3.29:1** ❌ | **4.93:1** ✓ | **8.99:1** ✓ |
| `--lg-bg-4` | **4.55:1** (borderline) | **6.13:1** | **7.19:1** ✓ |

**Decision: raise the text color, not move the paragraphs onto glass.** Wrapping every
`.muted` paragraph across `settings.html`/`reports.html`/`lab.html` in a card would be the
larger, more invasive change (layout, not just a token) for text whose whole point is to read
as *inline*, lower-emphasis prose next to un-muted siblings — moving it onto its own glass
plate would visually promote it, the opposite of "muted". A new local token
(`--ink-muted-ground: #332E40` light, `= var(--lg-ink-muted)` dark — dark already clears 4.5:1
at every mesh stop, so no change needed there) keeps the paragraphs inline and fixes the one
theme that was failing, following the same Inherited-Palette pattern as `--money-gain/-loss`
rather than forking `--lg-ink-muted` itself (which stays correct and unchanged for its many
solid-glass uses elsewhere).

### Related 2: `--text-primary/-secondary/-muted/-disabled` collapsed to two values

`base.html`'s alias block for `tokens.css`'s component tokens (`.btn2`, `.card2`, `.chip`,
`.table2`, `.tabs__tab`, `.dialog2`, …) had `--text-primary`/`--text-secondary` both pointing at
`--lg-ink`, and `--text-muted`/`--text-disabled` both at `--lg-ink-muted` — a disabled control
was visually identical to ordinary secondary text. All four consumers of these tokens render on
`--bg-surface-*`/`--bg-elevated`, which are themselves `var(--lg-glass-solid)` (base.html:111–
114) — never the bare mesh — so the roles are checked against the solid-glass composite at its
worst mesh point (Light `--lg-bg-3`, Dark `--lg-bg-1`), matching the `.panel` methodology above.

| Role | Value | Light (worst: bg-3) | Dark (worst: bg-1) |
|---|---|---|---|
| `--text-primary` | `var(--lg-ink)` (unchanged) | **13.64:1** | **14.32:1** |
| `--text-secondary` | `var(--lg-ink-muted)` (was `var(--lg-ink)`) | **7.50:1** | **7.89:1** |
| `--text-muted` | `var(--lg-ink-muted)` (unchanged) | **7.50:1** | **7.89:1** |
| `--text-disabled` | `#7C729A` light / `#7E6FA9` dark (was `var(--lg-ink-muted)`) | **3.81:1** | **3.68:1** |

Four roles, three visually distinguishable values by design: `secondary` and `muted` are
deliberately the **same** value — both name "de-emphasized running text", and a fourth,
in-between tone with no distinct role would just be noise. `disabled` is the one role WCAG
doesn't require 4.5:1 for (deactivated controls are exempt from the text-contrast success
criterion); it's set to sit clearly below `muted` (3.7–3.8:1 vs. 7.5–7.9:1 — visibly dimmer at a
glance) while staying above the 3:1 non-text-UI floor, so "disabled" reads as *present but
inactive* rather than invisible.

## Layout-Utilities und Warte-Dialog (agent/UI-PLAINSPEAK)

Drei Design-Entscheidungen vor dem Handoff, damit Inline-Styles und ein zweiter
Overlay-Klon gar nicht erst entstehen.

### `.card2--narrow` / `.card2--center` — die schmale, zentrierte Seitenkarte

`error.html` und `index.html` sind Vollseiten mit einer einzelnen Karte und trugen dafür
Inline-Styles (`max-width:520px` bzw. `640px`, `margin:3rem auto`). Neu als Modifier auf dem
bestehenden Block, additiv in `components.css`:

| Klasse | Werte | Zweck |
|---|---|---|
| `.card2--narrow` | `max-width: 560px; margin-inline: auto; margin-block: var(--space-8);` | Box-Breite + Zentrierung der Karte |
| `.card2--center` | `text-align: center;` | nur wo der Inhalt zentriert gehört (Fehlerseite) |
| `.card2__header--center` | `justify-content: center;` | Kopfzeile der zentrierten Variante |
| `.card2__title--lg` | `font-size: var(--font-size-h2);` | Karten-Titel, der als Seiten-`h1` dient |

**Eine Breite statt zweier.** 520px und 640px waren nicht begründet unterschiedlich, sondern
gewachsen. 560px liegt bei 16px-Fließtext bei ~70 Zeichen je Zeile und damit im Lesemaß-Fenster
45–75 — für die kurze Fehlermeldung wie für die zwei Absätze der Startseite. Kein
Breiten-Modifier, solange kein Inhalt einen zweiten Wert erzwingt.

### `.btn-row` — Buttonreihe unter einer Karte

`display: flex; gap: var(--space-3); flex-wrap: wrap; margin-top: var(--space-5);`, Modifier
`.btn-row--center { justify-content: center; }`. Ersetzt die Inline-Flex-Zeilen in `error.html`
und `index.html`; `.6rem`/`1.4rem` rasten dabei auf den 4px-Rhythmus (12px/20px) ein.
`.dialog2__actions` bleibt getrennt — das ist die Aktionszeile *im* Dialog (rechtsbündig), keine
Seiten-Utility.

### `wait_dialog()` — ein Warte-Overlay für alle langsamen POST-Formulare

Das native `<dialog>` aus `app.html` (Scan) wird als Makro nach `components.html` gezogen und
von `app.html` und `backtest.html` genutzt. Grund: der Timeout-Zweig ist Sicherheitsverhalten
(kein Endlos-Spinner, immer ein Ausweg per Schließen-Button) und darf zwischen zwei Kopien
nicht auseinanderlaufen. Optik unverändert — das Makro rendert weiterhin einen transparenten
Dialog mit `.card` darin; die beiden bisher id-gebundenen Regeln (`background:transparent`,
`::backdrop` mit `--overlay-strong` + Blur) leben als `.wait-dialog` in `components.css`.

## Komponenten-Konsolidierung E5–E7 (Ein-Namen-Regel)

Fünf Etappen (E5 Dialoge, E3 Meldungen, E2 Karte, E6 Buttons, E7 Felder + Schlussputz) führen
die zwei parallel gewachsenen Komponentenwelten — die dunkle Style-Phase-2-Welt mit `2`-Suffix
(`btn2`, `input2`, `card2`, `alert2`, `dialog2`) und die helle Liquid-Glass-Welt aus `base.html`
— zu je **einer** Regel pro Komponente in `components.css` zusammen. E6 macht `.btn` fertig
(dieser Abschnitt); `.input2` folgt in E7 (siehe „Felder" weiter unten, danach ist keine
`*2`-Klasse mehr im Bestand außer dem Token-Namen `--card2` in `base.html`, E1).

### Buttons — eine solide Glasfläche statt der Vendor-Scheibe (E6)

Jedes `<button>` und `.btn` bekam bis E6 die durchscheinende Vendor-Standardfläche
(`linear-gradient` über `--lg-glass-hi`/`--lg-glass` mit Backdrop-Blur). Gerechnet mit
`--lg-ink` als Beschriftung, gegen den jeweils ungünstigsten Mesh-Punkt:

| Mesh | Light Kante/Mitte | Dark Kante/Mitte |
|---|---|---|
| `--lg-bg-1` | 14,08 / 13,41 | **3,32** / 6,32 |
| `--lg-bg-2` | 12,15 / 10,91 | **3,95** / 8,22 |
| `--lg-bg-3` | 9,08 / 7,19 | **4,61** / 10,21 |

Im Dark Mode fällt die Beschriftung an der hellen Verlaufskante (`--lg-bg-1`, die Radiale bei
12 % 8 % — oben links, wo die Buttons stehen) auf **3,32:1**. Button-Labels sind normaler Text
(14,4px/600), die Schwelle liegt bei 4,5:1 — das war ein Bestandsbefund auf ~40 blanken
`<button>`-Elementen ohne eigene Klasse. Die Fläche wechselt deshalb auf `--lg-glass-solid`:
13,64:1 (Light) / 14,32:1 (Dark), an jedem Mesh-Punkt. `liquid-glass.css` bleibt dabei
unverändert — die neue Regel legt nur darüber (Selektor bleibt bewusst `button, .btn`, damit
alle 40 Bestandsstellen den Kontrastfix ohne Template-Änderung bekommen).

Die Varianten `.btn--primary` (violettes Gel) und `.btn--success` (türkises Gel) übernehmen die
Rezeptur der früheren `.on`/`.green`-Gel-Klassen wortgleich; `.btn--destructive` (Solid-Glas +
`--lg-error`-Rand, Tinte `--status-ink-error`) übernimmt die frühere `.red`-Rezeptur.
`.btn--live` bleibt die volle Danger-Fläche mit Schriftgewicht 700 (Live-Bestätigungsdialog,
§18.1) — das ändert sich durch die Konsolidierung nicht.

### `[aria-pressed]`/`[aria-current]` als Ausgewählt-Zustand

`.on` war **zweifach belegt** — einmal für die primäre Aktion (z. B. „Signale anfordern") und
einmal für den ausgewählten Zustand eines Toggle-Buttons (z. B. „Top N Signale: 5"). Dieselbe
Krankheit wie `.chip` in E4. Aufgelöst über zwei getrennte Bedeutungen:

- **Primäre Aktion** → `.btn--primary` (eine echte Variante, kein Zustand).
- **Ausgewählter Zustand** → keine neue Klasse, sondern das ARIA-Attribut, das die Buttons
  ohnehin schon tragen (`aria-pressed` für Toggle-Buttons, `aria-current` für `<a>`-Filterlinks,
  die kein `aria-pressed` tragen dürfen):
  ```css
  .btn[aria-pressed="true"], .btn[aria-current] {
    color: var(--lg-ink); font-weight: 600;
    box-shadow: var(--lg-cast-1), inset 0 0 0 1px var(--lg-violet);
    border-color: var(--lg-violet);
  }
  ```
  Optisch deckungsgleich mit `.tab.active` (dashboard.html) — dem im Haus bereits abgenommenen
  Auswahl-Muster. Die beiden `<a>`-Filterlinks in `app.html` („Alle" / Anlageklassen-Filter)
  hatten vorher **gar keinen** programmatischen Ausgewählt-Zustand — nur die Klasse `.on`, die
  weder Screenreader noch Assistive Technology auswerten. `aria-current="true"` schließt diese
  Lücke nebenbei.

`.rp-filter.on` (reports.html, Jahres-Filter) ist davon **nicht** betroffen — das ist eine
eigene, seitenlokale Komponente mit eigenem Präfix (siehe `tests/test_css_class_collisions.py`),
keine `.btn`-Variante, und trägt `.on` nur für genau einen Zweck (kein Doppelbelegungsproblem).
