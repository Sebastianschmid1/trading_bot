"""Deterministisches Kostenmodell fuer Backtests.

``legacy_cost_pct`` bildet die bisherige Backtest-Spread-Pauschale je Orderseite nach
(im Live-Report ueber ``config.BACKTEST_COST_PCT`` = 5 bps je Seite gespeist). ``slippage_spread_fraction``
traegt zusaetzlich eine **konservative Nicht-Null-Slippage** als Bruchteil dieses Spreads:
kurz nach dem US-Open sind Spreads real breiter, und eine marktnahe Order kreuzt einen Teil
davon. Der Default ist bewusst so gewaehlt, dass Kosten eher **ueber**- als unterschaetzt
werden (Backtest-Ehrlichkeit) — keine Aussage ueber tagesaktuelle Broker- oder Behoerdenpreise.
Alle uebrigen Werte (SEC/FINRA, Liquiditaetsaufschlag, Market Impact) bleiben deaktiviert und
muessen fuer einen Validierungslauf bewusst gesetzt werden.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostBreakdown:
    """Aufgeschluesselte Kosten einer Round-Trip-Order in Kontowaehrung."""

    commission: float = 0.0
    spread: float = 0.0
    slippage: float = 0.0
    sec_fee: float = 0.0
    finra_taf: float = 0.0
    liquidity_penalty: float = 0.0
    market_impact: float = 0.0
    requested_shares: float = 0.0
    filled_shares: float = 0.0
    unfilled_shares: float = 0.0

    @property
    def total(self) -> float:
        """Gesamtkosten ohne Mengenfelder."""
        return (self.commission + self.spread + self.slippage + self.sec_fee + self.finra_taf
                + self.liquidity_penalty + self.market_impact)

    def as_dict(self) -> dict:
        """JSON-freundliche Darstellung inklusive Gesamtsumme."""
        out = asdict(self)
        out["total"] = self.total
        return out


@dataclass(frozen=True)
class CostModel:
    """Parametrisierbare, reine Kostenannahmen fuer US-Aktien-Backtests.

    Alpaca-kommissionsfrei ist deshalb der Kommissions-Default. ``legacy_cost_pct`` ist die
    vorhandene Spread-Pauschale in Prozent je Seite (der Live-Report speist sie aus
    ``config.BACKTEST_COST_PCT`` = 0.05 % = 5 bps). ``slippage_spread_fraction`` steht
    **bewusst auf 0.5**: eine marktnahe Order schluckt real rund die Haelfte des (nach dem
    US-Open breiteren) Spreads als Slippage. So ist ein Default-Backtest **nicht mehr
    slippage-blind** und schoent das Ergebnis nicht; konservativ heisst hier lieber Kosten
    ueberschaetzen. SEC/FINRA, Liquiditaetsaufschlag und Market Impact bleiben deaktiviert
    (0.0): ihre Saetze haengen von Markt, Broker und Zeitpunkt ab und muessen fuer einen
    Validierungslauf bewusst gesetzt werden.
    """

    legacy_cost_pct: float = 0.0
    commission_per_share: float = 0.0
    commission_pct: float = 0.0
    # Konservative Nicht-Null-Slippage: 50 % des (nach dem US-Open breiteren) Spreads.
    # Bewusst kostenueberschaetzend statt slippage-blind (Backtest-Ehrlichkeit).
    slippage_spread_fraction: float = 0.5
    sec_fee_rate: float = 0.0
    finra_taf_per_share: float = 0.0
    finra_taf_cap: float = 0.0
    max_volume_fraction: float = 1.0
    liquidity_penalty_pct: float = 0.0
    market_impact_coefficient: float = 0.0

    def calculate_trade(self, entry_price: float, exit_price: float, direction: str,
                        position_notional: float, entry_volume: float | None = None,
                        exit_volume: float | None = None,
                        entry_bid: float | None = None, entry_ask: float | None = None,
                        exit_bid: float | None = None, exit_ask: float | None = None) -> CostBreakdown:
        """Berechnet Kosten fuer die tatsaechlich fuellbare Menge eines Round-Trips.

        ``position_notional`` bleibt pro Seite konstant, damit das Defaultmodell exakt
        dem bisherigen P&L-Abzug entspricht. Bei gesetzter ``max_volume_fraction`` wird
        die geringste auf Ein- und Ausstieg fuellbare Menge verwendet.
        """
        if entry_price <= 0 or exit_price <= 0 or position_notional <= 0:
            return CostBreakdown()
        if direction not in {"long", "short"}:
            raise ValueError("direction muss 'long' oder 'short' sein")

        requested = position_notional / entry_price
        filled = min(requested, self._fillable_shares(entry_volume, requested),
                     self._fillable_shares(exit_volume, requested))
        unfilled = max(0.0, requested - filled)
        entry_notional = position_notional * (filled / requested)
        exit_notional = entry_notional  # konstante Notional-Basis verhindert Default-Abweichungen

        entry_costs = self._side_costs(entry_price, entry_notional, filled, entry_bid, entry_ask,
                                       is_sell=direction == "short", volume=entry_volume)
        exit_costs = self._side_costs(exit_price, exit_notional, filled, exit_bid, exit_ask,
                                      is_sell=direction == "long", volume=exit_volume)
        return CostBreakdown(
            commission=entry_costs["commission"] + exit_costs["commission"],
            spread=entry_costs["spread"] + exit_costs["spread"],
            slippage=entry_costs["slippage"] + exit_costs["slippage"],
            sec_fee=entry_costs["sec_fee"] + exit_costs["sec_fee"],
            finra_taf=entry_costs["finra_taf"] + exit_costs["finra_taf"],
            liquidity_penalty=entry_costs["liquidity_penalty"] + exit_costs["liquidity_penalty"],
            market_impact=entry_costs["market_impact"] + exit_costs["market_impact"],
            requested_shares=requested, filled_shares=filled, unfilled_shares=unfilled,
        )

    def _fillable_shares(self, volume: float | None, requested: float) -> float:
        if volume is None or volume <= 0:
            return requested
        return min(requested, max(0.0, self.max_volume_fraction) * volume)

    def _side_costs(self, price: float, notional: float, shares: float,
                    bid: float | None, ask: float | None, is_sell: bool,
                    volume: float | None) -> dict[str, float]:
        quote_spread = 0.0
        if bid is not None and ask is not None and ask >= bid >= 0:
            quote_spread = (ask - bid) * shares / 2.0
        # Ohne Quote bleibt die bisherige Pauschale sichtbar als Spread-Annahme.
        spread = quote_spread if quote_spread else notional * self.legacy_cost_pct / 100.0
        commission = shares * self.commission_per_share + notional * self.commission_pct / 100.0
        sec_fee = notional * self.sec_fee_rate if is_sell else 0.0
        finra_taf = min(shares * self.finra_taf_per_share, self.finra_taf_cap) if is_sell else 0.0
        participation = shares / volume if volume and volume > 0 else 0.0
        liquidity_penalty = notional * self.liquidity_penalty_pct / 100.0 * participation
        market_impact = notional * self.market_impact_coefficient * participation ** 0.5
        return {
            "commission": commission,
            "spread": spread,
            "slippage": spread * self.slippage_spread_fraction,
            "sec_fee": sec_fee,
            "finra_taf": finra_taf,
            "liquidity_penalty": liquidity_penalty,
            "market_impact": market_impact,
        }
