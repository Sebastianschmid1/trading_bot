"""
Smart-Money: Was handeln große/informierte Trader?

Aggregiert pro Aktie öffentlich gemeldete Aktivität (über yfinance, kein API-Key):
- Insider (SEC Form 4, ~2 Tage Verzug): Netto-Käufe/-Verkäufe der letzten 6 Monate.
- Institutionen (SEC 13F, quartalsweise): Top-Halter und ihre Bestandsänderung (pctChange).

Daraus entsteht ein Smart-Money-Score 0–100. Da ein Voll-Scan (1 Abfrage pro Aktie)
Minuten dauert, läuft er als nächtlicher Job und wird in data/smartmoney_cache.json
zwischengespeichert; /top5trade und /signals lesen nur den Cache.

⚠️ Positionierungs-/Sentiment-Indikator mit Datenverzug — keine Echtzeit, keine Garantie.
"""

import json
import time
import logging

import yfinance as yf

from stockbot import config
from stockbot.paths import DATA_DIR

log = logging.getLogger(__name__)

SCAN_CACHE = DATA_DIR / "smartmoney_cache.json"

# Interne Score-Gewichte (Summe der Maxima = 100)
_INSIDER_MAX = 50.0
_INST_MAX = 40.0
_LEVEL_MAX = 10.0


# ── yfinance-Rohdaten je Ticker ──────────────────────────────────────────────

def fetch_raw(ticker: str) -> dict:
    """Holt die Smart-Money-Rohdaten einer Aktie von yfinance (robust, Felder ggf. None)."""
    raw = {
        "insider_net": None, "insider_buys": None, "insider_sells": None,
        "insider_buy_shares": None, "insider_sell_shares": None,
        "inst_avg_change": None, "inst_added": None, "inst_reduced": None, "inst_total": None,
        "inst_pct": None, "largest_buy": None,
    }
    t = yf.Ticker(ticker)

    # Insider-Käufe (Zusammenfassung 6 Monate)
    try:
        ip = t.insider_purchases
        if ip is not None and len(ip) > 0:
            label_col = ip.columns[0]
            by_label = {str(r[label_col]).strip(): r for _, r in ip.iterrows()}

            def _num(label, col):
                row = by_label.get(label)
                if row is None:
                    return None
                try:
                    v = row[col]
                    return None if v != v else float(v)   # NaN-Check
                except Exception:
                    return None

            raw["insider_buy_shares"]  = _num("Purchases", "Shares")
            raw["insider_sell_shares"] = _num("Sales", "Shares")
            raw["insider_net"]         = _num("Net Shares Purchased (Sold)", "Shares")
            raw["insider_buys"]        = _num("Purchases", "Trans")
            raw["insider_sells"]       = _num("Sales", "Trans")
    except Exception as e:
        log.debug(f"{ticker}: insider_purchases nicht verfügbar ({e})")

    # Größter jüngster Insider-Kauf (für die Detail-Anzeige)
    try:
        it = t.insider_transactions
        if it is not None and len(it) > 0 and "Text" in it.columns:
            buys = it[it["Text"].astype(str).str.contains("Purchase", case=False, na=False)]
            if len(buys) > 0 and "Value" in buys.columns:
                top = buys.sort_values("Value", ascending=False).iloc[0]
                date = str(top["Start Date"]) if "Start Date" in buys.columns else ""
                raw["largest_buy"] = {"value": float(top["Value"]), "date": date}
    except Exception as e:
        log.debug(f"{ticker}: insider_transactions nicht verfügbar ({e})")

    # Institutionelle Halter + Bestandsänderung
    try:
        ih = t.institutional_holders
        if ih is not None and len(ih) > 0 and "pctChange" in ih.columns:
            changes = [float(c) for c in ih["pctChange"].tolist() if c == c]   # ohne NaN
            if changes:
                raw["inst_avg_change"] = sum(changes) / len(changes)
                raw["inst_added"]   = sum(1 for c in changes if c > 0)
                raw["inst_reduced"] = sum(1 for c in changes if c < 0)
                raw["inst_total"]   = len(changes)
    except Exception as e:
        log.debug(f"{ticker}: institutional_holders nicht verfügbar ({e})")

    # Anteil im Institutionen-Besitz (Niveau)
    try:
        mh = t.major_holders
        if mh is not None and "institutionsPercentHeld" in mh.index:
            v = mh.loc["institutionsPercentHeld"].iloc[0]
            raw["inst_pct"] = None if v != v else float(v)
    except Exception as e:
        log.debug(f"{ticker}: major_holders nicht verfügbar ({e})")

    return raw


# ── Score-Berechnung ─────────────────────────────────────────────────────────

def compute_score(raw: dict) -> dict | None:
    """
    Verdichtet die Rohdaten zu einem Score 0–100 (+ 1–5 Sterne).
    Gibt None zurück, wenn weder Insider- noch Institutionen-Daten vorliegen.
    """
    have_insider = raw.get("insider_net") is not None or raw.get("insider_buys") is not None
    have_inst = raw.get("inst_total") or raw.get("inst_avg_change") is not None
    if not have_insider and not have_inst:
        return None

    # Insider-Komponente [0..50], neutral = 25
    if not have_insider:
        insider_comp = _INSIDER_MAX / 2
    else:
        bs = raw.get("insider_buy_shares") or 0.0
        ss = raw.get("insider_sell_shares") or 0.0
        buys = raw.get("insider_buys") or 0.0
        sells = raw.get("insider_sells") or 0.0
        net = raw.get("insider_net")
        parts = []
        if (bs + ss) > 0:
            parts.append((bs - ss) / (bs + ss))
        elif net is not None and net != 0:
            parts.append(1.0 if net > 0 else -1.0)
        if (buys + sells) > 0:
            parts.append((buys - sells) / (buys + sells))
        sig = sum(parts) / len(parts) if parts else 0.0
        insider_comp = (_INSIDER_MAX / 2) * (1 + sig)

    # Institutionen-Komponente [0..40], neutral = 20
    if not have_inst:
        inst_comp = _INST_MAX / 2
    else:
        parts = []
        avg = raw.get("inst_avg_change")
        if avg is not None:
            parts.append(max(-1.0, min(1.0, avg)))
        total = raw.get("inst_total") or 0
        if total > 0:
            added = raw.get("inst_added") or 0
            reduced = raw.get("inst_reduced") or 0
            parts.append((added - reduced) / total)
        sig = sum(parts) / len(parts) if parts else 0.0
        inst_comp = (_INST_MAX / 2) * (1 + sig)

    # Niveau-Komponente [0..10]
    inst_pct = raw.get("inst_pct")
    level_comp = _LEVEL_MAX * max(0.0, min(1.0, inst_pct)) if inst_pct is not None else 0.0

    score = int(round(max(0.0, min(100.0, insider_comp + inst_comp + level_comp))))
    stars = max(1, min(5, round(score / 20)))

    return {
        "score": score,
        "stars": stars,
        "insider_net": raw.get("insider_net"),
        "insider_buys": raw.get("insider_buys"),
        "insider_sells": raw.get("insider_sells"),
        "inst_avg_change": raw.get("inst_avg_change"),
        "inst_added": raw.get("inst_added"),
        "inst_total": raw.get("inst_total"),
        "inst_pct": raw.get("inst_pct"),
        "largest_buy": raw.get("largest_buy"),
    }


# ── Cache ────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        return json.loads(SCAN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        SCAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SCAN_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except Exception as e:
        log.warning(f"Smart-Money-Cache konnte nicht gespeichert werden: {e}")


def last_scanned() -> float | None:
    return _load_cache().get("scanned_at")


# ── Scan & Abruf ─────────────────────────────────────────────────────────────

def scan_universe(tickers: list[str], pause: float = 0.0) -> dict:
    """
    Scannt alle Ticker (sequenziell, je in try/except) und schreibt die Scores in den Cache.
    Gibt das Score-Dict { ticker: scoredict } zurück.
    """
    scores: dict[str, dict] = {}
    start = time.time()
    for ticker in tickers:
        try:
            s = compute_score(fetch_raw(ticker))
            if s is not None:
                scores[ticker] = s
        except Exception as e:
            log.warning(f"Smart-Money-Scan fehlgeschlagen für {ticker}: {e}")
        if pause:
            time.sleep(pause)

    _save_cache({"scanned_at": time.time(), "scores": scores})
    log.info(f"Smart-Money-Scan: {len(scores)}/{len(tickers)} Aktien bewertet "
             f"in {time.time() - start:.0f}s.")
    return scores


def get_score(ticker: str) -> dict | None:
    """Gibt den gecachten Score einer Aktie zurück (oder None, falls nicht vorhanden)."""
    return _load_cache().get("scores", {}).get(ticker)


def get_top(tickers: list[str], n: int) -> list[dict]:
    """Die n Aktien mit dem höchsten Smart-Money-Score aus dem Cache (für die Ticker-Liste)."""
    scores = _load_cache().get("scores", {})
    items = []
    for t in tickers:
        s = scores.get(t)
        if s is not None:
            items.append({**s, "ticker": t})
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:n]


def rank(signals: list[dict], top_n: int,
         w_tech: float | None = None, w_smart: float | None = None) -> list[dict]:
    """
    Reiht technische Signale unter Berücksichtigung des Smart-Money-Scores neu.
    Hängt jedem Signal das Feld 'smart_money' an (dict oder None) und schneidet auf top_n.
    Ohne Cache bleibt die technische Reihenfolge erhalten.
    """
    w_tech = config.SMARTMONEY_W_TECH if w_tech is None else w_tech
    w_smart = config.SMARTMONEY_W_SMART if w_smart is None else w_smart

    enriched = []
    for sig in signals:
        sm = get_score(sig["ticker"])
        sig["smart_money"] = sm
        # Beide Größen auf 0–100-Skala: technische Stärke + Smart-Money-Score
        sm_score = sm["score"] if sm else 0.0
        combined = sig.get("strength", 0) * w_tech + sm_score * w_smart
        enriched.append((combined, -abs(sig.get("rsi", 50) - 50), sig))

    enriched.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [sig for _, _, sig in enriched[:top_n]]
