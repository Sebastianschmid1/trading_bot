#!/usr/bin/env python3
"""
Strategie-"Paper": tiefer Vergleich ALLER Strategien (stockbot.market.strategies.REGISTRY)
nach Industriestandard — als ein Markdown-Dokument (docs/STRATEGIEN_REPORT.md).

Methodik: Portfolio-Simulation (top_n Signale/Tag, optional Hebel) auf gemeinsamem Download,
mit täglicher **Mark-to-Market-Equity-Kurve** (offene Positionen werden täglich neu bewertet).
Darauf die üblichen Kennzahlen:
  • Rendite:        Total-Return, CAGR
  • Risiko:         annualisierte Volatilität, Max-Drawdown + -Dauer
  • Risk-adjusted:  Sharpe, Sortino, Calmar, Recovery-Factor
  • vs. Benchmark:  Beta, Alpha, Korrelation gegen S&P 500
  • Trade-Qualität: Profitfaktor, Win/Lose, Payoff, Erwartungswert, Kelly, Serien, Exposure
  • Robustheit:     t-Statistik der mittleren Trade-Rendite (Edge real oder Zufall?)

Demo-Modell — Tages-Timeframe, long-only, ATR-SL/TP, ohne Gebühren/Slippage.

Lauf (vom Repo-Root):
    python tools/strategy_report.py                       # S&P, 2 Jahre, mit Charts
    python tools/strategy_report.py --region msci_world --years 3
    python tools/strategy_report.py --limit 8 --no-charts # Schnelltest
"""

import os
import sys
import argparse
from datetime import date

import numpy as np
import pandas as pd

# Repo-Root auf den Importpfad setzen, damit `python tools/strategy_report.py` funktioniert
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Konsole UTF-8-fest machen (Windows cp1252 kann Emojis sonst nicht ausgeben → UnicodeEncodeError).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import yfinance as yf                                      # noqa: E402
from stockbot.core import db                                 # noqa: E402
from stockbot.market import universes, strategies          # noqa: E402
from stockbot.backtest import engine                        # noqa: E402
from stockbot.backtest import report as report_charts       # noqa: E402
from stockbot.core import metrics as M                       # noqa: E402
from stockbot.core.evaluator import realized_pnl             # noqa: E402
from stockbot.config import REGION_LABELS                    # noqa: E402

DEFAULT_OUT = "docs/STRATEGIEN_REPORT.md"
CHART_COMPARE = "docs/backtest_strategien_vergleich.png"
CHART_EXIT = "docs/backtest_tagesende_vs_halten.png"
BENCHMARK = "^GSPC"
E0 = 10000.0


# ── Anzeige-Helfer ────────────────────────────────────────────────────────────

def _lose_rate(m: dict) -> float:
    n = m.get("trades", 0)
    return round(m.get("losses", 0) / n * 100, 1) if n else 0.0


def _break_even(m: dict) -> int:
    return max(0, m.get("trades", 0) - m.get("wins", 0) - m.get("losses", 0))


def _pf_str(m: dict) -> str:
    pf = m.get("profit_factor")
    if pf is None:
        return "∞" if m.get("trades") else "—"
    return f"{pf:.2f}"


def _num(v, fmt="{:.2f}", dash="—"):
    """None-sicher formatieren."""
    return dash if v is None else fmt.format(v)


def _sharpe_sort_key(row) -> float:
    s = row["equity"].get("sharpe")
    return s if s is not None else float("-inf")


# ── Mark-to-Market-Equity & Exposure ─────────────────────────────────────────

def _mtm(trades: list[dict], data: dict, dates: list, init_cap: float,
         trade_size: float, leverage: float):
    """Tägliche Mark-to-Market-Equity über `dates`: Startkapital + realisierter P&L +
    unrealisierter P&L offener Positionen (zum jeweiligen Tagesschlusskurs, Hebel/Cap via
    realized_pnl). Gibt (equity_liste, exposure_pct, ø_gleichzeitige_positionen) zurück."""
    close_re = {t: df["Close"].astype(float).reindex(dates, method="ffill")
                for t, df in data.items()}
    tr = [{"tkr": t["ticker"], "e": pd.Timestamp(t["entry_date"]),
           "x": pd.Timestamp(t["exit_date"]), "entry": float(t["entry"]),
           "dir": t.get("direction", "long"), "pnl": float(t["pnl_eur"])} for t in trades]
    tr.sort(key=lambda z: z["x"])

    equity, realized, k = [], 0.0, 0
    days_invested, concurrent_sum = 0, 0
    for d in dates:
        while k < len(tr) and tr[k]["x"] <= d:
            realized += tr[k]["pnl"]; k += 1
        unreal, open_n = 0.0, 0
        for z in tr:
            if z["e"] <= d < z["x"]:
                ser = close_re.get(z["tkr"])
                price = None if ser is None else ser.get(d)
                if price is None or price != price:     # NaN-Schutz
                    continue
                unreal += realized_pnl(z["entry"], float(price), z["dir"], trade_size, leverage)[1]
                open_n += 1
        equity.append(init_cap + realized + unreal)
        concurrent_sum += open_n
        if open_n > 0:
            days_invested += 1

    n = len(dates)
    exposure = round(days_invested / n * 100, 1) if n else 0.0
    avg_conc = round(concurrent_sum / n, 2) if n else 0.0
    return equity, exposure, avg_conc


def _bench_equity(dates: list, years: int, init_cap: float):
    """S&P-500-Equity auf dieselbe Datums-Achse normiert (Start = init_cap). None bei Fehlschlag."""
    try:
        b = report_charts._clean(yf.download(BENCHMARK, period=f"{years + 1}y", interval="1d",
                                             progress=False, auto_adjust=True))
        s = b["Close"].astype(float).reindex(dates, method="ffill").bfill()
        vals = [float(v) for v in s.values]
        if not vals or vals[0] <= 0:
            return None
        return [init_cap * v / vals[0] for v in vals]
    except Exception as e:
        print(f"⚠️  Benchmark-Download fehlgeschlagen: {e}", flush=True)
        return None


# ── Datenerhebung (Netz) ─────────────────────────────────────────────────────

def collect(region="sp500", years=2, top_n=10, leverage=1.0, limit=None,
            allow_short=False) -> dict:
    """Backtestet alle Registry-Strategien (Portfolio + MTM-Equity) auf einem Download.
    `allow_short` (nur Standard-Strategie, Backtest) nimmt zusätzlich Short-Setups auf."""
    tickers = universes.get_tickers(region, auto=False)
    if limit:
        tickers = tickers[:limit]
    print(f"Region {region}: {len(tickers)} Ticker, {years}J — lade Kursdaten …", flush=True)
    data = engine._download_daily(tickers, years)
    print(f"Verwertbare Ticker: {len(data)}\n", flush=True)

    if not data:
        return {"config": {"region": region, "region_label": REGION_LABELS.get(region, region),
                           "years": years, "top_n": top_n, "leverage": leverage,
                           "n_tickers": 0, "start": None, "end": None,
                           "generated": str(date.today()), "init_cap": E0,
                           "allow_short": allow_short, "benchmark": None}, "rows": []}

    last = max(df.index[-1] for df in data.values())
    start_after = last - pd.Timedelta(days=int(years * 365.25))
    dates = sorted({d for df in data.values() for d in df.index if d >= start_after})
    trade_size = E0 / max(top_n, 1)

    bench_eq = _bench_equity(dates, years, E0)
    bench_em = M.equity_metrics(bench_eq) if bench_eq else None

    rows = []
    for strat in strategies.all_strategies():
        by_date = engine.gather_fires(strat, data, start_after, allow_short=allow_short)
        trades = engine.simulate_portfolio(data, by_date, top_n, leverage, trade_size, top_n, max_hold=40)
        m = M.compute_metrics(trades, E0)
        eq, exposure, avg_conc = _mtm(trades, data, dates, E0, trade_size, leverage)
        em = M.equity_metrics(eq, bench=bench_eq if (bench_eq and len(bench_eq) == len(eq)) else None)
        spd = engine.signals_per_day(by_date)
        avg_hold = round(float(np.mean([t["hold_days"] for t in trades])), 1) if trades else 0.0
        years_actual = max((last - start_after).days / 365.25, 1e-9)
        rows.append({
            "key": strat.key, "label": strat.label, "description": strat.description,
            "trade": m, "equity": em, "direction_split": engine._direction_split(trades),
            "extra": {"exposure": exposure, "avg_concurrent": avg_conc,
                      "trades_per_year": round(m["trades"] / years_actual, 1),
                      "avg_hold": avg_hold, "spd": spd},
        })
        print(f"  fertig: {strat.label}  (Sharpe {_num(em['sharpe'])}, CAGR {em['cagr_pct']:.1f}%)", flush=True)

    return {
        "config": {"region": region, "region_label": REGION_LABELS.get(region, region),
                   "years": years, "top_n": top_n, "leverage": leverage,
                   "n_tickers": len(data), "start": str(start_after.date()),
                   "end": str(last.date()), "generated": str(date.today()),
                   "init_cap": E0, "allow_short": allow_short, "benchmark": bench_em},
        "rows": rows,
    }


# ── Markdown-Aufbau (rein, ohne Netz — unit-testbar) ─────────────────────────

_METHODIK = (
    "## 📐 Methodik — wie Strategien verglichen werden\n\n"
    "Verglichen wird wie bei professionellen Backtests **risiko-adjustiert**, nicht nur nach roher "
    "Rendite. Grundlage ist eine **tägliche Mark-to-Market-Equity-Kurve** (offene Positionen werden "
    "täglich zum Schlusskurs neu bewertet), aus der die Standard-Kennzahlen abgeleitet werden:\n\n"
    "- **CAGR** — annualisierte Wachstumsrate (vergleichbar über verschieden lange Zeiträume).\n"
    "- **Volatilität** — annualisierte Schwankung der Tagesrenditen (Risikomaß).\n"
    "- **Max-Drawdown (+ Dauer)** — größter Rückgang vom Hoch und wie lange er anhielt.\n"
    "- **Sharpe** = Überrendite / Volatilität (Rendite je Risikoeinheit; > 1 gut, > 2 sehr gut).\n"
    "- **Sortino** = wie Sharpe, aber nur **Downside**-Volatilität (bestraft nur Verluste).\n"
    "- **Calmar** = CAGR / Max-Drawdown (Rendite je Drawdown-Risiko).\n"
    "- **Recovery-Factor** = Gesamtrendite / Max-Drawdown.\n"
    "- **Beta / Alpha / Korrelation** gegenüber dem **S&P 500** (Markt­abhängigkeit & Mehrwert).\n"
    "- **Trade-Qualität**: Profitfaktor, Trefferquote, **Payoff** (Ø Gewinn/Ø Verlust), "
    "Erwartungswert, **Kelly**, längste Gewinn-/Verlustserie, **Exposure** (Zeit im Markt).\n"
    "- **t-Statistik** der mittleren Trade-Rendite: ist die Edge statistisch belastbar "
    "(Faustregel |t| > 2) oder Zufall?\n"
)


def _bench_line(b: dict) -> str:
    if not b:
        return ""
    return (f"| **S&P 500 (Buy & Hold)** | {b['cagr_pct']:+.1f} | {_num(b['sharpe'])} | "
            f"{_num(b['sortino'])} | {_num(b['calmar'])} | {b['max_dd_pct']:.1f} | "
            f"{b['ann_vol_pct']:.1f} | — | — | — |")


def build_markdown(data: dict, charts: bool = True) -> str:
    c = data["config"]
    rows = sorted(data["rows"], key=_sharpe_sort_key, reverse=True)
    zeitraum = f"{c['start']} → {c['end']}" if c.get("start") else "—"
    allow_short = bool(c.get("allow_short"))
    richtung = "long + short" if allow_short else "long-only"

    o = []
    o.append("# 📊 Strategie-Report (alle Strategien) — Tiefenanalyse\n")
    o.append(
        f"*Erstellt: {c['generated']} · Korb: {c['region_label']} ({c['n_tickers']} Aktien) · "
        f"Zeitraum: {zeitraum} · {c['years']} Jahre · Tages-Timeframe · {richtung} · "
        f"Portfolio: {c['top_n']} Signale/Tag, Hebel {c['leverage']:g}×, Start {c['init_cap']:.0f}€*\n"
    )
    if allow_short:
        o.append(
            "> **Demo-Backtest** — **long + short**, ATR-SL/TP (Short gespiegelt), ohne "
            "Gebühren/Slippage. Shorts liefert **nur die Standard-Strategie** (Spiegel der "
            "Long-Logik); die übrigen Strategien bleiben auch im Backtest long-only. "
            "**Der Live-Bot handelt unverändert ausschließlich long** — Shorts sind reine "
            "Backtest-Analyse. Alle Kennzahlen basieren auf einer täglichen "
            "Mark-to-Market-Equity-Kurve der Portfolio-Simulation.\n"
        )
    else:
        o.append(
            "> **Demo-Backtest** — long-only, ATR-SL/TP, ohne Gebühren/Slippage. Alle Kennzahlen "
            "basieren auf einer täglichen Mark-to-Market-Equity-Kurve der Portfolio-Simulation.\n"
        )
    o.append(_METHODIK)

    # ── Rangliste (nach Sharpe) ──
    o.append("## 🏆 Rangliste (nach Sharpe-Ratio)\n")
    o.append("| # | Strategie | CAGR % | Sharpe | Sortino | Calmar | Max DD % | Vol % | PF | Win % | Trades |")
    o.append("|---|-----------|------:|------:|------:|------:|--------:|-----:|----|------:|------:|")
    for i, r in enumerate(rows, 1):
        e, m = r["equity"], r["trade"]
        o.append(
            f"| {i} | {r['label']} (`{r['key']}`) | {e['cagr_pct']:+.1f} | {_num(e['sharpe'])} | "
            f"{_num(e['sortino'])} | {_num(e['calmar'])} | {e['max_dd_pct']:.1f} | "
            f"{e['ann_vol_pct']:.1f} | {_pf_str(m)} | {m['win_rate']:.1f} | {m['trades']} |"
        )
    bl = _bench_line(c.get("benchmark"))
    if bl:
        o.append(bl)
    o.append("")

    if charts:
        o.append("## 📈 Grafiken\n")
        o.append(f"![Equity-Vergleich vs S&P 500]({os.path.basename(CHART_COMPARE)})\n")
        o.append(f"![Tagesende vs Halten]({os.path.basename(CHART_EXIT)})\n")

    # ── Detail je Strategie ──
    o.append("## 🔍 Strategien im Detail\n")
    for r in rows:
        e, m, x = r["equity"], r["trade"], r["extra"]
        ds = r.get("direction_split") or {}
        o.append(f"### {r['label']}  (`{r['key']}`)\n")
        if r["description"]:
            o.append(f"{r['description']}\n")

        o.append("**Rendite & Risiko (Equity-Kurve)**\n")
        o.append("| Kennzahl | Wert |")
        o.append("|----------|-----:|")
        o.append(f"| Gesamt-Rendite | {e['total_return_pct']:+.1f} % |")
        o.append(f"| CAGR (annualisiert) | {e['cagr_pct']:+.1f} % |")
        o.append(f"| Volatilität (annualisiert) | {e['ann_vol_pct']:.1f} % |")
        o.append(f"| Max. Drawdown | {e['max_dd_pct']:.1f} % |")
        o.append(f"| Max. Drawdown-Dauer | {e['max_dd_days']} Handelstage |")
        o.append("")

        o.append("**Risiko-adjustiert & vs. S&P 500**\n")
        o.append("| Kennzahl | Wert |")
        o.append("|----------|-----:|")
        o.append(f"| Sharpe-Ratio | {_num(e['sharpe'])} |")
        o.append(f"| Sortino-Ratio | {_num(e['sortino'])} |")
        o.append(f"| Calmar-Ratio | {_num(e['calmar'])} |")
        o.append(f"| Recovery-Factor | {_num(e['recovery_factor'])} |")
        o.append(f"| Beta (vs. S&P 500) | {_num(e['beta'])} |")
        o.append(f"| Alpha annualisiert | {_num(e['alpha_pct'], '{:+.1f}')} % |")
        o.append(f"| Korrelation (vs. S&P 500) | {_num(e['corr'])} |")
        o.append("")

        o.append("**Trade-Qualität**\n")
        o.append("| Kennzahl | Wert |")
        o.append("|----------|-----:|")
        o.append(f"| Profitfaktor | {_pf_str(m)} |")
        o.append(f"| Trefferquote (Win) | {m['win_rate']:.1f} % ({m['wins']}/{m['trades']}) |")
        o.append(f"| Verlustquote (Lose) | {_lose_rate(m):.1f} % ({m['losses']}/{m['trades']}) |")
        o.append(f"| Payoff-Ratio (Ø Gewinn/Ø Verlust) | {_num(m['payoff_ratio'])} |")
        o.append(f"| Ø Gewinn / Ø Verlust | {m['avg_win']:+.2f}€ / {m['avg_loss']:+.2f}€ |")
        o.append(f"| Erwartungswert/Trade | {m['expectancy']:+.2f}€ ({m['avg_trade_pct']:+.2f} %) |")
        o.append(f"| Bester / schlechtester Trade | {m['best_trade_pct']:+.1f} % / {m['worst_trade_pct']:+.1f} % |")
        o.append(f"| Längste Serie Gewinne / Verluste | {m['max_consec_wins']} / {m['max_consec_losses']} |")
        o.append(f"| Kelly-Anteil | {_num(m['kelly_pct'], '{:.1f}')} % |")
        o.append(f"| t-Statistik der Edge | {_num(m['t_stat'])}"
                 f"{'  ✅ signifikant' if (m['t_stat'] is not None and abs(m['t_stat']) > 2) else '  ⚠️ schwach'} |")
        o.append("")

        o.append("**Aktivität**\n")
        o.append("| Kennzahl | Wert |")
        o.append("|----------|-----:|")
        o.append(f"| Trades gesamt / pro Jahr | {m['trades']} / {x['trades_per_year']} |")
        o.append(f"| Ø Haltedauer | {x['avg_hold']} Tage |")
        o.append(f"| Exposure (Zeit im Markt) | {x['exposure']} % |")
        o.append(f"| Ø gleichzeitige Positionen | {x['avg_concurrent']} |")
        o.append(f"| Ø Signale/Tag | {x['spd']['avg']} (Median {x['spd']['median']}, Max {x['spd']['max']}) |")
        if allow_short and ds.get("n_short"):
            o.append(f"| Richtungs-Split (Long / Short) | {ds.get('n_long', 0)} / {ds['n_short']} Trades |")
            o.append(f"| P&L-Beitrag (Long / Short) | {ds.get('pnl_long', 0.0):+.2f}€ / {ds['pnl_short']:+.2f}€ |")
        o.append("")

    return "\n".join(o) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Strategie-Report (Tiefenanalyse) für alle Strategien.")
    ap.add_argument("--region", default="sp500", help="Korb: sp500 | msci_world | emerging")
    ap.add_argument("--years", type=int, default=2, help="Testfenster in Jahren (Default 2)")
    ap.add_argument("--top-n", type=int, default=10, help="Signale/Tag (Portfolio-Simulation)")
    ap.add_argument("--leverage", type=float, default=1.0, help="Hebel der Portfolio-Simulation")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Ticker (Schnelltest)")
    ap.add_argument("--shorts", action="store_true",
                    help="zusätzlich Short-Setups der Standard-Strategie (nur Backtest; Live bleibt long-only)")
    ap.add_argument("--no-charts", action="store_true", help="keine PNG-Grafiken erzeugen")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"Ziel-Markdown (Default {DEFAULT_OUT})")
    a = ap.parse_args()

    db.init_db()   # Tabellen (u. a. strategy_configs für Parameter-Overrides) sicherstellen

    data = collect(region=a.region, years=a.years, top_n=a.top_n,
                   leverage=a.leverage, limit=a.limit, allow_short=a.shorts)

    charts = not a.no_charts
    if charts and data["rows"]:
        keys = [r["key"] for r in data["rows"]]
        try:
            print("\n▶ Erzeuge Equity-Vergleichsgrafik …", flush=True)
            report_charts.compare_report(keys=keys, years=a.years, top_n=a.top_n,
                                         leverage=a.leverage, out=CHART_COMPARE,
                                         allow_short=a.shorts)
            print("▶ Erzeuge Tagesende-vs-Halten-Grafik …", flush=True)
            report_charts.exit_mode_analysis(keys=keys, years=a.years, top_n=a.top_n,
                                             leverage=a.leverage, out=CHART_EXIT,
                                             allow_short=a.shorts)
        except Exception as e:
            print(f"⚠️  Grafik-Erzeugung fehlgeschlagen ({e}) — schreibe Paper ohne Charts.", flush=True)
            charts = False

    md = build_markdown(data, charts=charts)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n📄 Paper geschrieben: {a.out}")


if __name__ == "__main__":
    main()
