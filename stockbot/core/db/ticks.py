"""Intraday-Ticks: Kurs- und Stärkeverlauf je aktivem Trade.

Der Intraday-Monitor schreibt hier während der Sitzung laufend fort; ``update_high_water``
hält zusätzlich den Höchstkurs, gegen den die Trailing-Exits rechnen.
"""


# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


# ── Intraday-Ticks (Kurs- & Stärke-Verlauf je aktivem Trade) ────────────────

def add_tick(user_id: int, ticker: str, price: float | None, strength: float | None):
    """Schreibt einen Verlaufspunkt (Kurs + Signal-Stärke) für einen aktiven Trade."""
    with db._database().transaction() as transaction:
        transaction.execute(
            "INSERT INTO trade_ticks (user_id, trade_date, ticker, ts, price, strength) "
            "VALUES (:user_id, :trade_date, :ticker, :ts, :price, :strength)",
            {"user_id": user_id, "trade_date": db._today(), "ticker": ticker,
             "ts": db._utc_timestamp(), "price": price, "strength": strength},
        )


def update_high_water(user_id: int, ticker: str, price: float | None) -> float | None:
    """Schreibt den Hoechstkurs seit Einstieg fort und gibt den gueltigen Wert zurueck.

    Monoton steigend ueber die WHERE-Bedingung statt einer skalaren Funktion: SQLite kennt
    `MAX(a, b)`, Postgres nur `GREATEST` — die Bedingung laeuft auf beiden Backends gleich
    und kann den Wert nicht zurueckdrehen. Ein leerer Ausgangswert wird mit dem aktuellen
    Kurs initialisiert — der Trailing-Stop liegt dann eine ATR-Spanne darunter und kann
    nicht verfrueht ausloesen.
    Betrifft nur den aktiven Trade des Tages-Schluessels (user_id, ticker, status='active')."""
    if price is None:
        return None
    with db._database().transaction() as transaction:
        transaction.execute(
            """UPDATE trades SET high_water = :price
               WHERE user_id = :user_id AND ticker = :ticker AND status = 'active'
                 AND (high_water IS NULL OR high_water < :price)""",
            {"user_id": user_id, "ticker": ticker, "price": float(price)},
        )
        row = transaction.one(
            """SELECT high_water FROM trades
               WHERE user_id = :user_id AND ticker = :ticker AND status = 'active'""",
            {"user_id": user_id, "ticker": ticker},
        )
    return float(row["high_water"]) if row and row["high_water"] is not None else None


def get_today_ticks(user_id: int) -> dict:
    """Gibt die heutigen Verlaufspunkte je Ticker zurück: { ticker: [{ts, price, strength}, ...] }."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            """SELECT ticker, ts, price, strength FROM trade_ticks
               WHERE user_id = :user_id AND trade_date = :trade_date
               ORDER BY ts ASC, id ASC""",
            {"user_id": user_id, "trade_date": db._today()},
        )
    series: dict[str, list] = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append(
            {"ts": r["ts"], "price": r["price"], "strength": r["strength"]}
        )
    return series
