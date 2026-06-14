"""
Kennzahlen für Backtests & das Live-Journal.

Zwei Ebenen:
- `compute_metrics(trades)`  — **Trade-Ebene** (Profitfaktor, Win/Lose, Payoff, Erwartungswert,
  Gewinn-/Verlustserien, Kelly, t-Statistik der Edge). Eingabe: Liste geschlossener Trades
  (je mit `pnl_eur`, optional `pnl_pct`).
- `equity_metrics(equity, bench=…)` — **Zeitreihen-Ebene** auf einer (täglichen) Equity-Kurve
  (CAGR, annualisierte Volatilität, Sharpe, Sortino, Calmar, Max-Drawdown + -Dauer,
  Recovery-Factor sowie Beta/Alpha/Korrelation gegen eine Benchmark).

Reine Rechenfunktionen ohne Fremd-Abhängigkeiten — offline testbar.
"""

import math


def _std(xs: list[float], ddof: int = 1) -> float:
    """Stichproben-Standardabweichung (ddof=1). 0.0 bei zu wenig Werten."""
    n = len(xs)
    if n <= ddof:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def _max_consecutive(trades: list[dict]):
    """Längste Serien aufeinanderfolgender Gewinn- bzw. Verlust-Trades."""
    max_w = max_l = cur_w = cur_l = 0
    for t in trades:
        p = t.get("pnl_eur") or 0.0
        if p > 0:
            cur_w += 1; cur_l = 0
        elif p < 0:
            cur_l += 1; cur_w = 0
        else:
            cur_w = cur_l = 0
        max_w = max(max_w, cur_w); max_l = max(max_l, cur_l)
    return max_w, max_l


def compute_metrics(trades: list[dict], initial_capital: float = 1000.0) -> dict:
    """Verdichtet geschlossene Trades zu Trade-Ebenen-Kennzahlen.

    Profitfaktor = Bruttogewinn / |Bruttoverlust|. Ohne Verlust-Trades ist er unendlich
    (None signalisiert „kein Verlust" → in der Anzeige als „∞").
    """
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "profit_factor": None,
            "total_pnl_eur": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "expectancy": 0.0, "max_drawdown_pct": 0.0,
            "payoff_ratio": None, "avg_trade_pct": 0.0, "best_trade_pct": 0.0,
            "worst_trade_pct": 0.0, "max_consec_wins": 0, "max_consec_losses": 0,
            "kelly_pct": None, "t_stat": None,
        }

    wins   = [t for t in trades if (t.get("pnl_eur") or 0.0) > 0]
    losses = [t for t in trades if (t.get("pnl_eur") or 0.0) < 0]
    gross_profit = sum(t["pnl_eur"] for t in wins)
    gross_loss   = sum(t["pnl_eur"] for t in losses)        # ≤ 0
    total_pnl    = sum((t.get("pnl_eur") or 0.0) for t in trades)

    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else None

    # Equity-Kurve & maximaler Drawdown (auf Basis kumuliertem €-P&L)
    equity = initial_capital
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity += (t.get("pnl_eur") or 0.0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)

    avg_win  = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    payoff   = (avg_win / abs(avg_loss)) if avg_loss != 0 else None

    # Kelly-Anteil (fraktioniert, %): f = W − (1−W)/Payoff
    w_frac = len(wins) / n
    kelly = (w_frac - (1 - w_frac) / payoff) * 100 if payoff else None

    # Statistische Signifikanz der Edge: t = Ø/(σ/√n) über die Trade-Renditen (%)
    pnls_pct = [(t.get("pnl_pct") or 0.0) for t in trades]
    mean_pct = sum(pnls_pct) / n
    sd_pct = _std(pnls_pct)
    t_stat = (mean_pct / (sd_pct / math.sqrt(n))) if sd_pct > 0 else None

    max_cw, max_cl = _max_consecutive(trades)

    return {
        "trades":           n,
        "wins":             len(wins),
        "losses":           len(losses),
        "win_rate":         round(len(wins) / n * 100, 1),
        "gross_profit":     round(gross_profit, 2),
        "gross_loss":       round(gross_loss, 2),
        "profit_factor":    round(profit_factor, 2) if profit_factor is not None else None,
        "total_pnl_eur":    round(total_pnl, 2),
        "avg_win":          round(avg_win, 2),
        "avg_loss":         round(avg_loss, 2),
        "expectancy":       round(total_pnl / n, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "payoff_ratio":     round(payoff, 2) if payoff is not None else None,
        "avg_trade_pct":    round(mean_pct, 2),
        "best_trade_pct":   round(max(pnls_pct), 2),
        "worst_trade_pct":  round(min(pnls_pct), 2),
        "max_consec_wins":  max_cw,
        "max_consec_losses": max_cl,
        "kelly_pct":        round(kelly, 1) if kelly is not None else None,
        "t_stat":           round(t_stat, 2) if t_stat is not None else None,
    }


def equity_metrics(equity: list[float], bench: list[float] | None = None,
                   periods_per_year: int = 252, rf: float = 0.0) -> dict:
    """Zeitreihen-Kennzahlen einer (gleichmäßig getakteten) Equity-Kurve.

    `equity` z. B. tägliche Mark-to-Market-Werte. `bench` (gleiche Länge) liefert Beta/Alpha/
    Korrelation gegen eine Benchmark. `rf` = risikofreier Tages-/Periodenzins (Default 0).
    Sharpe/Sortino/Calmar sind None, wenn nicht definiert (z. B. keine Volatilität / kein DD).
    """
    base = {
        "total_return_pct": 0.0, "cagr_pct": 0.0, "ann_vol_pct": 0.0,
        "sharpe": None, "sortino": None, "calmar": None,
        "max_dd_pct": 0.0, "max_dd_days": 0, "recovery_factor": None,
        "beta": None, "alpha_pct": None, "corr": None,
    }
    n = len(equity)
    if n < 3 or equity[0] <= 0:
        return base

    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, n) if equity[i - 1] > 0]
    if len(rets) < 2:
        return base

    total_return = equity[-1] / equity[0] - 1
    years = (n - 1) / periods_per_year
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 and equity[-1] > 0 else 0.0

    mean = sum(rets) / len(rets)
    sd = _std(rets)
    ann_vol = sd * math.sqrt(periods_per_year)
    excess_ann = mean * periods_per_year - rf
    sharpe = (excess_ann / ann_vol) if ann_vol > 0 else None

    downside = [min(r, 0.0) for r in rets]
    dd_dev = math.sqrt(sum(d * d for d in downside) / len(downside)) * math.sqrt(periods_per_year)
    sortino = (excess_ann / dd_dev) if dd_dev > 0 else None

    # Max-Drawdown (peak-to-trough) + längste Unterwasser-Phase (in Perioden)
    peak = equity[0]; max_dd = 0.0; cur_len = 0; max_len = 0
    for v in equity:
        if v >= peak:
            peak = v; cur_len = 0
        else:
            cur_len += 1; max_len = max(max_len, cur_len)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)
    calmar = (cagr / max_dd) if max_dd > 0 else None
    recovery = (total_return / max_dd) if max_dd > 0 else None

    beta = alpha_pct = corr = None
    if bench is not None and len(bench) == n:
        brets = [bench[i] / bench[i - 1] - 1 for i in range(1, n) if bench[i - 1] > 0]
        if len(brets) == len(rets) and _std(brets) > 0:
            bmean = sum(brets) / len(brets)
            cov = sum((rets[i] - mean) * (brets[i] - bmean) for i in range(len(rets))) / (len(rets) - 1)
            bvar = _std(brets) ** 2
            beta = cov / bvar if bvar > 0 else None
            if beta is not None:
                alpha_pct = (mean - beta * bmean) * periods_per_year * 100
            denom = _std(rets) * _std(brets)
            corr = cov / denom if denom > 0 else None

    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct":         round(cagr * 100, 2),
        "ann_vol_pct":      round(ann_vol * 100, 2),
        "sharpe":           round(sharpe, 2) if sharpe is not None else None,
        "sortino":          round(sortino, 2) if sortino is not None else None,
        "calmar":           round(calmar, 2) if calmar is not None else None,
        "max_dd_pct":       round(max_dd * 100, 2),
        "max_dd_days":      max_len,
        "recovery_factor":  round(recovery, 2) if recovery is not None else None,
        "beta":             round(beta, 2) if beta is not None else None,
        "alpha_pct":        round(alpha_pct, 2) if alpha_pct is not None else None,
        "corr":             round(corr, 2) if corr is not None else None,
    }


def format_metrics(m: dict, title: str = "") -> str:
    """Kennzahlen als kompakter Text (für Telegram), Profitfaktor zuerst."""
    pf = "∞" if m["profit_factor"] is None and m["trades"] else (
        f"{m['profit_factor']:.2f}" if m["profit_factor"] is not None else "—")
    head = f"📊 *{title}*\n" if title else ""
    return (
        f"{head}"
        f"💹 Profitfaktor: *{pf}*\n"
        f"🎯 Trefferquote: {m['win_rate']:.1f}%  ({m['wins']}/{m['trades']})\n"
        f"💰 Gesamt-P&L: {m['total_pnl_eur']:+.2f}€\n"
        f"📉 Max. Drawdown: {m['max_drawdown_pct']:.1f}%\n"
        f"📈 Ø Gewinn/Verlust: {m['avg_win']:+.2f}€ / {m['avg_loss']:+.2f}€\n"
        f"🧮 Erwartungswert/Trade: {m['expectancy']:+.2f}€"
    )
