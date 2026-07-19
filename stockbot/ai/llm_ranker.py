"""
LLM-gestütztes Re-Ranking der Signale mit Claude Haiku.

Nimmt die technisch+Smart-Money gerankten Signale (mit allen Metadaten), reichert sie je Aktie um
**Fundamentaldaten (Geschäftsberichts-Kennzahlen), Analysten-Sicht, Earnings-Termin und News** an
(yfinance, kostenlos) und lässt Claude Haiku daraus eine Rangfolge bilden.

Leitplanke W3.2: fundamentale Anreicherung (Kennzahlen/Analysten/News) fürs LLM-Re-Ranking =
RESEARCH, nicht Preissignal — Alpaca liefert diese Fundamentaldaten nicht. yfinance ist hier
bewusst zulässig; das Re-Ranking ordnet nur um und erzeugt nie allein einen Trade.

Design-Prinzipien:
- **Bricht den Signalfluss nie**: ohne API-Key, ohne installiertes `anthropic` oder bei jedem Fehler
  wird die Eingabe unverändert zurückgegeben.
- **Kostensparend**: genau EINE gebündelte Haiku-Anfrage pro Signalliste, kompakter Kontext,
  kleines max_tokens, Tages-Cache für die Fundamentaldaten.
"""

import json
import time
import logging
from datetime import date

import yfinance as yf

from stockbot import config

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


def _rate_limit_wait_seconds(exc) -> float | None:
    """Versucht aus einer Rate-Limit-Exception ein Retry-Intervall zu ziehen."""
    for attr in ("retry_after", "retry_after_seconds", "retry_after_s"):
        val = getattr(exc, attr, None)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if headers:
        for key in ("retry-after", "Retry-After"):
            val = headers.get(key)
            if val is None:
                continue
            try:
                return float(val)
            except Exception:
                continue
    msg = str(exc).lower()
    if "rate limit" in msg or "rate-limiting" in msg or "429" in msg:
        return None
    return None


def _is_rate_limit_error(exc) -> bool:
    text = str(exc).lower()
    if "rate limit" in text or "rate-limiting" in text or "429" in text:
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return code == 429


def _call_with_retry(label: str, fn, *, attempts: int = 3):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            if not _is_rate_limit_error(e) or attempt >= attempts:
                raise
            wait = _rate_limit_wait_seconds(e) or min(8.0, 2.0 ** (attempt - 1))
            log.warning(f"{label}: Rate limit erkannt — retry in {wait:.1f}s (Versuch {attempt}/{attempts})")
            time.sleep(wait)
    raise RuntimeError(f"Unexpected retry loop exit in {label}")


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
    """Bewertet/re-rankt die Signale per Claude Haiku (Technik + Fundamentaldaten + News).
    Läuft schon ab EINEM Signal (dann reine KI-Bewertung mit Score + Begründung).
    Robust: bei fehlendem Client/Fehler bleibt die Reihenfolge unverändert."""
    if not signals:
        return signals

    cli = client if client is not None else _get_client()
    if cli is None:
        return signals

    subset = signals[: config.LLM_MAX_SIGNALS]
    payload = {
        "signals": [_signal_brief(s) for s in subset],
        "fundamentals": {s["ticker"]: fetch(s["ticker"]) for s in subset},
    }
    try:
        resp = _call_with_retry(
            "LLM-Ranking",
            lambda: cli.messages.create(
                model=config.LLM_MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload, default=str, ensure_ascii=False)}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            ),
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


_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "tickers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tickers"],
    "additionalProperties": False,
}

_SUGGEST_SYSTEM = (
    "Du bist ein Ticker-Finder. Der Nutzer gibt einen (evtl. ungenauen) Firmen-, Produkt- oder "
    "Markennamen ein. Gib die passendsten BÖRSENTICKER zurück (bevorzugt US-Börsen, auch ETFs), "
    "max. 3, vom wahrscheinlichsten zuerst. Nur echte, existierende Symbole. Wenn nichts passt: "
    "leere Liste. Antworte als JSON gemäß Schema (Feld 'tickers')."
)


def suggest_tickers(query: str, *, client=None) -> list[str]:
    """Schlägt zu einer ungenauen Eingabe (z. B. 'Apple') echte Ticker vor (Claude Haiku).
    Fallback für die "Meinten Sie?"-Hilfe. Ohne Client/bei Fehler: []. Wirft nie."""
    q = (query or "").strip()
    if not q:
        return []
    if client is None:
        return []
    cli = client
    try:
        resp = _call_with_retry(
            f"Ticker-Vorschlag für '{q}'",
            lambda: cli.messages.create(
                model=config.LLM_MODEL,
                max_tokens=128,
                system=_SUGGEST_SYSTEM,
                messages=[{"role": "user", "content": q}],
                output_config={"format": {"type": "json_schema", "schema": _SUGGEST_SCHEMA}},
            ),
        )
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        tickers = json.loads(text).get("tickers", [])
    except Exception as e:
        log.warning(f"Ticker-Vorschlag fehlgeschlagen für '{q}': {e}")
        return []
    return [str(t).strip().upper() for t in tickers if str(t).strip()][:3]


def health_check(client=None) -> dict:
    """Selbsttest: prüft, ob das KI-Ranking wirklich funktioniert (echter Mini-Call an Haiku).
    Gibt {ok: bool, detail: str, ranking: [...]} zurück — ohne Netzwerk-Fundamentaldaten."""
    if client is None:
        if not config.LLM_RANK_ENABLED:
            return {"ok": False, "detail": "Kein ANTHROPIC_API_KEY gesetzt — KI-Ranking ist aus."}
        client = _get_client()
        if client is None:
            return {"ok": False, "detail": "Anthropic-Client nicht verfügbar (Key ungültig oder Paket fehlt)."}

    payload = {
        "signals": [{"ticker": "AAA", "strength": 60}, {"ticker": "BBB", "strength": 80}],
        "fundamentals": {},
    }
    t0 = time.time()
    try:
        resp = _call_with_retry(
            "LLM-Healthcheck",
            lambda: client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            ),
        )
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        ranking = json.loads(text).get("ranking", [])
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    dt = time.time() - t0
    if not ranking:
        return {"ok": False, "detail": "Antwort ohne verwertbares Ranking-JSON."}
    return {
        "ok": True,
        "detail": f"OK — Modell {config.LLM_MODEL}, {len(ranking)} Signale bewertet, {dt:.1f}s",
        "ranking": ranking,
    }
