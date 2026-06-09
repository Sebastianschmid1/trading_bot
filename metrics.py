"""
Kennzahlen für Backtests & das Live-Journal.

Eingabe ist immer eine Liste geschlossener Trades (chronologisch), jeder mit mindestens
`pnl_eur` und `pnl_pct`. Schwerpunkt: **Profitfaktor** (vom Nutzer gewählte Zielgröße).
Reine Rechenfunktion, offline testbar.
"""


def compute_metrics(trades: list[dict], initial_capital: float = 1000.0) -> dict:
    """Verdichtet geschlossene Trades zu Kennzahlen.

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

    return {
        "trades":           n,
        "wins":             len(wins),
        "losses":           len(losses),
        "win_rate":         round(len(wins) / n * 100, 1),
        "gross_profit":     round(gross_profit, 2),
        "gross_loss":       round(gross_loss, 2),
        "profit_factor":    round(profit_factor, 2) if profit_factor is not None else None,
        "total_pnl_eur":    round(total_pnl, 2),
        "avg_win":          round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss":         round(gross_loss / len(losses), 2) if losses else 0.0,
        "expectancy":       round(total_pnl / n, 2),
        "max_drawdown_pct": round(max_dd, 2),
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
