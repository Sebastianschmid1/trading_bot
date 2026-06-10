"""
LLM-gestütztes Re-Ranking der Signale mit Claude Haiku.

Nimmt die technisch+Smart-Money gerankten Signale (mit allen Metadaten), reichert sie je Aktie um
**Fundamentaldaten (Geschäftsberichts-Kennzahlen), Analysten-Sicht, Earnings-Termin und News** an
(yfinance, kostenlos) und lässt Claude Haiku daraus eine Rangfolge bilden.

Design-Prinzipien:
- **Bricht den Signalfluss nie**: ohne API-Key, ohne installiertes `anthropic` oder bei jedem Fehler
  wird die Eingabe unverändert zurückgegeben.
- **Kostensparend**: genau EINE gebündelte Haiku-Anfrage pro Signalliste, kompakter Kontext,
  kleines max_tokens, Tages-Cache für die Fundamentaldaten.
"""

import json
import logging
from datetime import date

import yfinance as yf

import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist ein nüchterner Trading-Analyst. Du erhältst mehrere technische Long-Signale für "
    "US-Aktien (je mit Kennzahlen) sowie pro Aktie Fundamentaldaten aus Geschäftsberichten "
    "(Umsatz/Gewinn/Wachstum/Margen/Bewertung), die Analysten-Empfehlung, den nächsten "
    "Earnings-Termin und aktuelle Schlagzeilen. Ranke die Signale nach Attraktivität für einen "
    "KURZFRISTIGEN Long-Trade (Stunden bis wenige Tage). Beziehe Technik UND Fundamentaldaten/News "
    "ein: bevorzuge starke Technik mit solider/gesunder Fundamentallage und ohne akutes "
    "Ereignisrisiko (z. B. Earnings unmittelbar bevorstehend, negative Schlagzeilen). Erfinde keine "
    "Daten — nutze nur die gelieferten. Antworte mit JSON gemäß Schema: pro Ticker ein score 0–100 "
    "(höher = attraktiver) und eine SEHR KURZE Begründung auf Deutsch (max. ~12 Wörter)."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "score":  {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["ticker", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranking"],
    "additionalProperties": False,
}

# Tages-Cache für Fundamentaldaten: { (datum, ticker): context }
_ctx_cache: dict[tuple, dict] = {}


# ── Zusatzquellen je Aktie (Geschäftsberichte, Analysten, Earnings, News) ────

def _safe(d, key, default=None):
    try:
        v = d.get(key, default)
        return v if v == v else default      # NaN-Schutz
    except Exception:
        return default


def gather_context(ticker: str) -> dict:
    """Kompakte Fundamental-/News-Zusatzdaten einer Aktie (robust, Tages-Cache)."""
    cache_key = (str(date.today()), ticker)
    if cache_key in _ctx_cache:
        return _ctx_cache[cache_key]

    ctx: dict = {}
    try:
        t = yf.Ticker(ticker)

        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        if info:
            ctx["sector"] = _safe(info, "sector")
            ctx["market_cap"] = _safe(info, "marketCap")
            ctx["pe_trailing"] = _safe(info, "trailingPE")
            ctx["pe_forward"] = _safe(info, "forwardPE")
            ctx["profit_margin"] = _safe(info, "profitMargins")
            ctx["revenue_growth"] = _safe(info, "revenueGrowth")
            ctx["earnings_growth"] = _safe(info, "earningsGrowth")
            ctx["analyst"] = _safe(info, "recommendationKey")
            ctx["target_mean"] = _safe(info, "targetMeanPrice")

        # Geschäftsbericht-Kennzahlen: Umsatz + Nettogewinn der letzten Perioden
        try:
            fin = t.income_stmt
            if fin is not None and getattr(fin, "empty", True) is False:
                def _row(name):
                    if name in fin.index:
                        vals = [float(v) for v in fin.loc[name].tolist()[:2] if v == v]
                        return vals or None
                    return None
                ctx["revenue"] = _row("Total Revenue")
                ctx["net_income"] = _row("Net Income")
        except Exception:
            pass

        # nächster Earnings-Termin
        try:
            cal = t.calendar
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    ctx["next_earnings"] = str(ed[0] if isinstance(ed, (list, tuple)) else ed)
        except Exception:
            pass

        # Top-3 News-Schlagzeilen
        try:
            news = t.news or []
            heads = []
            for n in news[:3]:
                title = (n.get("content", {}) or {}).get("title") or n.get("title")
                if title:
                    heads.append(str(title)[:160])
            if heads:
                ctx["news"] = heads
        except Exception:
            pass
    except Exception as e:
        log.debug(f"gather_context({ticker}) fehlgeschlagen: {e}")

    _ctx_cache[cache_key] = ctx
    return ctx


# ── Claude-Client (lazy, robust) ─────────────────────────────────────────────

def _get_client():
    """Anthropic-Client oder None (kein Key / Paket fehlt / deaktiviert)."""
    if not config.LLM_RANK_ENABLED:
        return None
    try:
        import anthropic
        return anthropic.Anthropic()          # liest ANTHROPIC_API_KEY aus der Umgebung
    except Exception as e:
        log.warning(f"Anthropic-Client nicht verfügbar: {e}")
        return None


def _signal_brief(s: dict) -> dict:
    """Kompakte Metadaten eines Signals für den LLM-Payload."""
    return {
        "ticker":     s.get("ticker"),
        "strategy":   s.get("strategy", "standard"),
        "strength":   s.get("strength"),
        "price":      s.get("price"),
        "rsi":        round(s["rsi"], 1) if isinstance(s.get("rsi"), (int, float)) else None,
        "macd":       s.get("macd_comment"),
        "trend":      s.get("trend_comment"),
        "weekly":     s.get("weekly_comment"),
        "volume":     s.get("volume_comment"),
        "levels":     s.get("sr_comment"),
        "smart_money": (s.get("smart_money") or {}).get("score"),
        "stop_loss":  round(s["stop_loss"], 2) if isinstance(s.get("stop_loss"), (int, float)) else None,
        "take_profit": round(s["take_profit"], 2) if isinstance(s.get("take_profit"), (int, float)) else None,
    }


def rank_signals(signals: list[dict], *, client=None, fetch=gather_context) -> list[dict]:
    """Re-rankt die Signale per Claude Haiku (Technik + Fundamentaldaten + News).
    Robust: bei fehlendem Client/Fehler bleibt die Reihenfolge unverändert."""
    if not signals or len(signals) < 2:
        return signals

    cli = client if client is not None else _get_client()
    if cli is None:
        return signals

    subset = signals[: config.LLM_MAX_SIGNALS]
    try:
        payload = {
            "signals": [_signal_brief(s) for s in subset],
            "fundamentals": {s["ticker"]: fetch(s["ticker"]) for s in subset},
        }
        resp = cli.messages.create(
            model=config.LLM_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, default=str, ensure_ascii=False)}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        ranking = json.loads(text).get("ranking", [])
    except Exception as e:
        log.warning(f"LLM-Ranking fehlgeschlagen — nutze technische Reihenfolge: {e}")
        return signals

    scores = {}
    for r in ranking:
        tkr = r.get("ticker")
        if tkr:
            scores[tkr] = (float(r.get("score", 0)), str(r.get("reason", ""))[:160])

    for s in signals:
        sc = scores.get(s["ticker"])
        if sc:
            s["llm_score"], s["llm_reason"] = round(sc[0], 1), sc[1]

    # nach LLM-Score sortieren (Signale ohne Score ans Ende, technische Reihenfolge erhalten)
    ranked = sorted(
        signals,
        key=lambda s: s["llm_score"] if isinstance(s.get("llm_score"), (int, float)) else -1.0,
        reverse=True,
    )
    log.info(f"LLM-Ranking: {[s['ticker'] for s in ranked]}")
    return ranked
