"""
Backtest-Report mit Grafiken: eine Strategie (Portfolio, top_n/Tag + Hebel) gegen S&P-500-Buy&Hold.

Lauf:
  python backtest_report.py                       # Standard, Hebel 5, 10 Signale/Tag, 2 Jahre
  python backtest_report.py adx_trend 3 8 2       # key years top_n leverage

Erzeugt eine PNG unter docs/ und druckt eine Kennzahlen-Übersicht.
Hinweis: Demo-Modell — Tages-Timeframe, ohne Gebühren/Slippage, feste €-Margin pro Position.
"""

import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from stockbot.backtest import engine as backtest
from stockbot.core import metrics as metrics_mod
from stockbot.market import strategies


def _clean(df):
    if hasattr(df.columns, "droplevel"):
        try:
            df = df.droplevel(1, axis=1)
        except Exception:
            pass
    return df.dropna(subset=["Close"])


def _drawdown_pct(series: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(series)
    return (series / peak - 1.0) * 100.0


def _equity_over_dates(trades, E0, bdates):
    """Realisierte Equity (Start E0) entlang der Benchmark-Handelstage."""
    events = sorted((t["exit_date"], t["pnl_eur"]) for t in trades)
    out, cum, k = [], 0.0, 0
    for d in bdates:
        ds = str(d.date())
        while k < len(events) and events[k][0] <= ds:
            cum += events[k][1]; k += 1
        out.append(E0 + cum)
    return np.array(out)


def compare_report(keys=("standard", "rsi_revert", "breakout", "ma_trend"),
                   years=2, top_n=10, leverage=1.0, benchmark="^GSPC",
                   out="docs/backtest_strategien_vergleich.png", allow_short=False):
    """Mehrere Strategien (Portfolio) gegen S&P-500-Buy&Hold vergleichen (eine Grafik).
    `allow_short` (nur Standard-Strategie, Backtest) nimmt zusätzlich Short-Setups auf."""
    from stockbot.backtest import engine as backtest
    bench = _clean(yf.download(benchmark, period=f"{years}y", interval="1d",
                               progress=False, auto_adjust=True))
    bdates = bench.index
    bclose = bench["Close"].astype(float).values
    E0 = 10000.0
    bench_eq = E0 * bclose / bclose[0]

    colors = ["#4f8cff", "#2ecc71", "#e67e22", "#9b59b6", "#1abc9c"]
    runs = []
    for key in keys:
        print(f"▶ Backtest {key} (Hebel {leverage:g}×, {top_n}/Tag, {years}J) …")
        r = backtest.backtest_portfolio(key, years=years, top_n=top_n, leverage=leverage,
                                        initial_capital=E0, allow_short=allow_short)
        eq = _equity_over_dates(r["trades"], E0, bdates)
        dd = _drawdown_pct(eq)
        runs.append({"key": key, "label": r["label"], "metrics": r["metrics"],
                     "eq": eq, "ret": (eq[-1] / E0 - 1) * 100, "maxdd": dd.min(),
                     "liq": r["liquidations"]})

    bench_ret = (bench_eq[-1] / E0 - 1) * 100
    bench_maxdd = _drawdown_pct(bench_eq).min()

    # Konsolen-Tabelle
    print(f"\nZeitraum {str(bdates[0].date())} → {str(bdates[-1].date())} · Hebel {leverage:g}× · {top_n}/Tag")
    print(f"{'Strategie':30}{'Rendite':>10}{'MaxDD':>9}{'PF':>7}{'Winrate':>9}{'Trades':>8}")
    for r in runs:
        m = r["metrics"]
        pf = "inf" if (m["profit_factor"] is None and m["trades"]) else (f"{m['profit_factor']:.2f}" if m["profit_factor"] else "—")
        print(f"{r['label'][:30]:30}{r['ret']:>9.1f}%{r['maxdd']:>8.1f}%{pf:>7}{m['win_rate']:>8.1f}%{m['trades']:>8}")
    print(f"{'S&P 500 (Buy&Hold)':30}{bench_ret:>9.1f}%{bench_maxdd:>8.1f}%{'—':>7}{'—':>9}{'—':>8}")

    # Grafik
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(12, 9))
    fig.suptitle(f"Strategie-Vergleich (Hebel {leverage:g}×, {top_n} Signale/Tag)  vs  S&P 500\n"
                 f"{str(bdates[0].date())} → {str(bdates[-1].date())} · Demo (ohne Gebühren/Slippage)",
                 fontsize=13, fontweight="bold")
    gs = fig.add_gridspec(2, 2, height_ratios=[2, 1], hspace=0.42, wspace=0.22)

    ax1 = fig.add_subplot(gs[0, :])
    for i, r in enumerate(runs):
        ax1.plot(bdates, r["eq"], color=colors[i % len(colors)], lw=1.8,
                 label=f"{r['label']} ({r['ret']:+.0f}%)")
    ax1.plot(bdates, bench_eq, color="#e6e9ef", lw=1.6, ls="--", label=f"S&P 500 ({bench_ret:+.0f}%)")
    ax1.axhline(E0, color="#8a93a6", lw=0.8, alpha=0.5)
    ax1.set_title("Equity-Kurven (Start 10.000 €)"); ax1.set_ylabel("€")
    ax1.legend(loc="upper left", fontsize=8); ax1.grid(alpha=0.15)
    for lbl in ax1.get_xticklabels():
        lbl.set_rotation(30); lbl.set_ha("right")

    names = [r["label"].split(" (")[0][:16] for r in runs] + ["S&P 500"]
    rets = [r["ret"] for r in runs] + [bench_ret]
    dds = [r["maxdd"] for r in runs] + [bench_maxdd]
    bar_colors = [colors[i % len(colors)] for i in range(len(runs))] + ["#e6e9ef"]
    x = np.arange(len(names))

    ax2 = fig.add_subplot(gs[1, 0])
    ax2.bar(x, rets, color=bar_colors); ax2.axhline(0, color="#8a93a6", lw=0.8)
    ax2.set_xticks(x); ax2.set_xticklabels(names, rotation=30, ha="right", fontsize=7)
    ax2.set_title("Gesamt-Rendite %"); ax2.grid(alpha=0.15, axis="y")

    ax3 = fig.add_subplot(gs[1, 1])
    ax3.bar(x, dds, color=bar_colors); ax3.axhline(0, color="#8a93a6", lw=0.8)
    ax3.set_xticks(x); ax3.set_xticklabels(names, rotation=30, ha="right", fontsize=7)
    ax3.set_title("Max. Drawdown %"); ax3.grid(alpha=0.15, axis="y")

    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\n📊 Grafik gespeichert: {out}")
    return out


def exit_mode_analysis(keys=("standard", "adx_trend", "rsi_revert", "breakout", "ma_trend"),
                       years=2, top_n=10, leverage=1.0,
                       out="docs/backtest_tagesende_vs_halten.png", allow_short=False):
    """Vergleicht je Strategie 'Tagesende schließen (1 Tag)' vs 'Halten bis SL/TP' + Signale/Tag.
    `allow_short` (nur Standard-Strategie, Backtest) nimmt zusätzlich Short-Setups auf."""
    E0 = 10000.0
    tickers = backtest.universes.get_tickers(backtest.DEFAULT_REGION, auto=False)
    data = backtest._download_daily(tickers, years)
    last = max(df.index[-1] for df in data.values())
    start_after = last - pd.Timedelta(days=int(years * 365.25))

    rows = []
    for key in keys:
        strat = strategies.get(key)
        print(f"▶ Scan {key} …")
        by_date = backtest.gather_fires(strat, data, start_after, allow_short=allow_short)
        spd = backtest.signals_per_day(by_date)
        ts = E0 / top_n
        eod = backtest.simulate_portfolio(data, by_date, top_n, leverage, ts, top_n, max_hold=1)
        hold = backtest.simulate_portfolio(data, by_date, top_n, leverage, ts, top_n, max_hold=40)
        m_eod = metrics_mod.compute_metrics(eod, E0)
        m_hold = metrics_mod.compute_metrics(hold, E0)
        avg_hold = float(np.mean([t["hold_days"] for t in hold])) if hold else 0.0
        rows.append({
            "key": key, "label": strat.label,
            "ret_eod": m_eod["total_pnl_eur"] / E0 * 100, "pf_eod": m_eod["profit_factor"],
            "ret_hold": m_hold["total_pnl_eur"] / E0 * 100, "pf_hold": m_hold["profit_factor"],
            "spd": spd, "avg_hold": avg_hold,
            "ge10": sum(1 for v in by_date.values() if len(v) >= top_n), "days": spd["days"],
        })

    # Tabelle
    print(f"\nZeitraum {str(start_after.date())} → {str(last.date())} · {top_n}/Tag · Hebel {leverage:g}×")
    print(f"{'Strategie':30}{'Tagesende%':>11}{'Halten%':>10}{'Ø Halte-Tg':>12}{'Ø Sig/Tag':>11}{'Max':>5}")
    for r in rows:
        print(f"{r['label'][:30]:30}{r['ret_eod']:>10.1f}%{r['ret_hold']:>9.1f}%"
              f"{r['avg_hold']:>11.1f}d{r['spd']['avg']:>11}{r['spd']['max']:>5}")

    # Grafik
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(12, 9))
    fig.suptitle(f"Effekt 'am Tagesende schließen' vs 'Halten bis SL/TP'  ·  {top_n} Signale/Tag, Hebel {leverage:g}×\n"
                 f"{str(start_after.date())} → {str(last.date())} · Demo (ohne Gebühren/Slippage)",
                 fontsize=13, fontweight="bold")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.4, 1], hspace=0.45, wspace=0.22)
    names = [r["label"].split(" (")[0][:16] for r in rows]
    x = np.arange(len(names)); w = 0.38

    ax1 = fig.add_subplot(gs[0, :])
    ax1.bar(x - w/2, [r["ret_eod"] for r in rows], w, color="#e74c3c", label="Tagesende (1 Tag)")
    ax1.bar(x + w/2, [r["ret_hold"] for r in rows], w, color="#2ecc71", label="Halten bis SL/TP")
    ax1.axhline(0, color="#8a93a6", lw=0.8)
    ax1.set_xticks(x); ax1.set_xticklabels(names, rotation=20, ha="right")
    ax1.set_title("Gesamt-Rendite % je Halte-Modus"); ax1.set_ylabel("%")
    ax1.legend(); ax1.grid(alpha=0.15, axis="y")

    ax2 = fig.add_subplot(gs[1, 0])
    ax2.bar(x, [r["spd"]["avg"] for r in rows], color="#4f8cff")
    ax2.set_xticks(x); ax2.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax2.set_title("Ø Signale pro Tag"); ax2.grid(alpha=0.15, axis="y")

    ax3 = fig.add_subplot(gs[1, 1])
    ax3.bar(x, [r["avg_hold"] for r in rows], color="#9b59b6")
    ax3.set_xticks(x); ax3.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax3.set_title("Ø Haltedauer (bis SL/TP) in Tagen"); ax3.grid(alpha=0.15, axis="y")

    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\n📊 Grafik gespeichert: {out}")
    return out


def main(strategy_key="standard", years=2, top_n=10, leverage=5.0,
         benchmark="^GSPC", out="docs/backtest_standard_lev5_vs_sp500.png"):
    print(f"▶ Backtest {strategy_key} · Hebel {leverage:g}× · {top_n} Signale/Tag · {years} Jahre …")
    res = backtest.backtest_portfolio(strategy_key, years=years, top_n=top_n, leverage=leverage)
    m, E0, trades = res["metrics"], res["initial_capital"], res["trades"]

    # ── Benchmark (S&P 500) ──
    bench = _clean(yf.download(benchmark, period=f"{years}y", interval="1d",
                               progress=False, auto_adjust=True))
    bdates = bench.index
    bclose = bench["Close"].astype(float).values
    bench_eq = E0 * bclose / bclose[0]

    # ── Strategie-Equity (realisiert) über die Benchmark-Handelstage ──
    events = sorted((t["exit_date"], t["pnl_eur"]) for t in trades)
    strat_eq, cum, k = [], 0.0, 0
    for d in bdates:
        ds = str(d.date())
        while k < len(events) and events[k][0] <= ds:
            cum += events[k][1]
            k += 1
        strat_eq.append(E0 + cum)
    strat_eq = np.array(strat_eq)

    strat_ret = (strat_eq[-1] / E0 - 1) * 100
    bench_ret = (bench_eq[-1] / E0 - 1) * 100
    strat_dd = _drawdown_pct(strat_eq)
    bench_dd = _drawdown_pct(bench_eq)
    pf = "∞" if (m["profit_factor"] is None and m["trades"]) else (
        f"{m['profit_factor']:.2f}" if m["profit_factor"] is not None else "—")

    # ── Konsolen-Übersicht ──
    print(f"\nZeitraum {res['start']} → {res['end']} · {res['n_tickers']} Aktien · Margin {res['trade_size']:.0f}€/Position")
    print(f"{'':22}{'Strategie':>14}{'S&P 500':>14}")
    print(f"{'Gesamt-Rendite':22}{strat_ret:>13.1f}%{bench_ret:>13.1f}%")
    print(f"{'Max. Drawdown':22}{strat_dd.min():>13.1f}%{bench_dd.min():>13.1f}%")
    print(f"{'End-Equity (10k€)':22}{strat_eq[-1]:>13.0f}€{bench_eq[-1]:>13.0f}€")
    print(f"Trades={m['trades']}  Winrate={m['win_rate']:.1f}%  Profitfaktor={pf}  Liquidationen={res['liquidations']}")

    # ── Grafiken ──
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(12, 9))
    fig.suptitle(f"{res['label']} — Hebel {leverage:g}×, {top_n} Signale/Tag  vs  S&P 500\n"
                 f"{res['start']} → {res['end']} · {res['n_tickers']} Aktien · Demo (ohne Gebühren/Slippage)",
                 fontsize=13, fontweight="bold")
    gs = fig.add_gridspec(2, 2, height_ratios=[2, 1], hspace=0.32, wspace=0.25)

    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(bdates, strat_eq, color="#4f8cff", lw=2, label=f"{res['label']} (+{strat_ret:.0f}%)")
    ax1.plot(bdates, bench_eq, color="#e6e9ef", lw=1.6, ls="--", label=f"S&P 500 ({bench_ret:+.0f}%)")
    ax1.axhline(E0, color="#8a93a6", lw=0.8, alpha=0.5)
    ax1.set_title("Equity-Kurve (Start 10.000 €)"); ax1.set_ylabel("€")
    ax1.legend(loc="upper left"); ax1.grid(alpha=0.15)

    ax2 = fig.add_subplot(gs[1, 0])
    ax2.fill_between(bdates, strat_dd, 0, color="#4f8cff", alpha=0.4)
    ax2.fill_between(bdates, bench_dd, 0, color="#e6e9ef", alpha=0.25)
    ax2.set_title("Drawdown (Unterwasser)"); ax2.set_ylabel("%"); ax2.grid(alpha=0.15)

    ax3 = fig.add_subplot(gs[1, 1])
    labels = ["Rendite %", "Max DD %"]
    x = np.arange(len(labels)); w = 0.38
    ax3.bar(x - w/2, [strat_ret, strat_dd.min()], w, color="#4f8cff", label="Strategie")
    ax3.bar(x + w/2, [bench_ret, bench_dd.min()], w, color="#e6e9ef", label="S&P 500")
    ax3.set_xticks(x); ax3.set_xticklabels(labels); ax3.axhline(0, color="#8a93a6", lw=0.8)
    ax3.set_title(f"Profitfaktor {pf} · Winrate {m['win_rate']:.0f}% · {m['trades']} Trades")
    ax3.legend(fontsize=8); ax3.grid(alpha=0.15, axis="y")

    for ax in (ax1, ax2):
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(30); lbl.set_ha("right")
    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\n📊 Grafik gespeichert: {out}")
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    main(
        strategy_key=a[0] if len(a) > 0 else "standard",
        years=int(a[1]) if len(a) > 1 else 2,
        top_n=int(a[2]) if len(a) > 2 else 10,
        leverage=float(a[3]) if len(a) > 3 else 5.0,
    )
