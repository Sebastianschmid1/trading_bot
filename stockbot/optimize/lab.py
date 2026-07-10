"""
Strategie-Labor — Walk-Forward-Selbstoptimierung der KI-Strategie `ai_adaptive`.

Idee (bewusst anders als naive Live-Tuner): Parameter werden NICHT an ein paar Live-Trades
angepasst, sondern gegen die Backtest-Engine über die volle S&P-500 optimiert — mit sauberer
Trennung in In-Sample (Training) und Out-of-Sample (Test). Ein Kandidat wird nur vorgeschlagen,
wenn er den amtierenden Champion **out-of-sample** schlägt. So bleibt Overfitting sichtbar und
wird nicht befördert.

Ablauf eines Zyklus (`run_cycle`):
  1. Champion = aktuelle Live-Parameter der Strategie (strategy_configs → strategy_runtime_params).
  2. Kandidaten aus drei Quellen (alle durch DASSELBE Gate): Raster-Nachbarn (±1 Schritt),
     History-Kandidaten aus dem eigenen Hypothesen-Log (Multi-Step in bestätigte Richtungen +
     ein Parameter-Paar), optional LLM-Vorschläge (mit Historie als Lern-Kontext). Zusätzlich
     läuft die zuletzt archivierte Version als Rollback-Kandidat mit (Regression-Guard).
  3. Jede Variante EINMAL über das Universum feuern (Fires-Cache pro Parametersatz+Datenstand);
     Fires per Einstiegsdatum in IS/OOS teilen — mit Embargo-Lücke vor dem Split (Purging).
  4. Bewertung: MAR = CAGR / max. Drawdown, je für IS, OOS und N OOS-Teilfenster (Folds).
     Transaktionskosten (config.BACKTEST_COST_PCT) sind eingerechnet.
  5. Auswahl: bester Kandidat nach IS-MAR (nur mit genug IS-Trades).
  6. Gate (mehrstufig): OOS-MAR-Aggregat > Champion UND Mehrheit der Folds gewonnen UND
     Bootstrap-Konfidenz ≥ BOOT_MIN_PROB UND Drawdown nicht verschlechtert. Danach
     Bestätigungs-Serie: erst nach STREAK_REQUIRED Gate-Siegen in Folge wird vorgeschlagen.
  7. Alles geloggt: falsifizierbares Hypothesen-Log (vorhergesagt vs. tatsächlich, inkl.
     Kandidaten-Quelle) + Zustand; ein bestandener Vorschlag landet in pending.json und wartet
     auf Menschen-Freigabe (ein wartender Vorschlag wird nie überschrieben). Zusätzlich je
     Zyklus: Reality-Check (Live-Trades vs. Backtest-Erwartung) und Rasterrand-Warnungen.

Freigabe (`apply_pending`): schreibt die Kandidat-Parameter als Live-Override in strategy_configs
(der Live-Bot übernimmt sie über den 20-s-Cache), archiviert den vorigen Champion und bumpt die
Version. Verwerfen (`reject_pending`) protokolliert und verwirft.

CLI (Repo-Root, venv aktiv):
  python -m stockbot.optimize.lab                 # voller Zyklus, volle S&P 500, 5 Jahre
  python -m stockbot.optimize.lab --limit 150     # schneller: nur die ersten 150 Ticker
  python -m stockbot.optimize.lab --apply         # anstehenden Vorschlag freigeben
  python -m stockbot.optimize.lab --reject        # anstehenden Vorschlag verwerfen
"""

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockbot.paths import DATA_DIR
from stockbot.market import strategies as strat_mod
from stockbot.market import universes
from stockbot.backtest import engine
from stockbot.core import metrics as metrics_mod
from stockbot import config
from stockbot.config import DEFAULT_REGION

log = logging.getLogger(__name__)

# ── Konfiguration (Labor-Zielfunktion & Suchraum) ────────────────────────────

TARGET_KEY = "ai_adaptive"          # welche Strategie das Labor tunt
LAB_DIR = DATA_DIR / "lab"
HISTORY_DIR = LAB_DIR / "history"

YEARS = 5                           # Auswertungsfenster
OOS_FRACTION = 0.30                 # letzte 30 % = Out-of-Sample (Test)
MIN_TRADES = 30                     # unter so wenig Trades ist ein Ergebnis nicht vertrauenswürdig
MAX_DD_TOLERANCE = 1.10             # Kandidat-OOS-Drawdown ≤ Champion × 1.10
MAX_DD_HARD = 35.0                  # harte Drawdown-Obergrenze (%)
TRADE_SIZE = 1000.0
TOP_N = 10
START_CAPITAL = TRADE_SIZE * TOP_N  # fester 10.000-USD-Pool (wie im Sweep)

# Statistische Härtung des Gates (Schutz vor Mehrfachtest-Overfitting auf dasselbe OOS):
EMBARGO_DAYS = int(os.getenv("LAB_EMBARGO_DAYS", "56"))   # Lücke IS→OOS (~ max. Haltedauer),
#   damit Trades, die über die Splitgrenze laufen, nicht vom Training ins Testfenster leaken.
N_FOLDS = int(os.getenv("LAB_OOS_FOLDS", "3"))            # OOS in K Teilfenster: Kandidat muss
#   den Champion in der MEHRHEIT der Folds schlagen, nicht nur im Aggregat (Robustheit).
STREAK_REQUIRED = int(os.getenv("LAB_STREAK", "2"))       # Gate-Siege in FOLGE, bevor ein
#   Kandidat in pending.json landet — ein Zufallstreffer übersteht selten mehrere Tage.
BOOT_N = int(os.getenv("LAB_BOOTSTRAP_N", "200"))         # Block-Bootstrap-Resamples
BOOT_MIN_PROB = float(os.getenv("LAB_BOOTSTRAP_MIN_PROB", "0.70"))  # Kandidat muss Champion in
#   ≥ diesem Anteil der Resamples schlagen (Punktvergleich allein ist bei ~30-60 Trades Rauschen).
BOOT_MIN_TRADES = 10                # unter so wenigen Trades ist Bootstrap nicht aussagekräftig

SURVIVORSHIP_NOTE = ("Universum = heutige S&P-500-Liste (Survivorship-Bias): absolute Kennzahlen "
                     "sind geschönt; das Gate vergleicht nur relativ auf denselben Daten.")

WEB_LIMIT = 150                     # Web-Trigger nutzt ein kleineres Universum (schneller, interaktiv)
LAB_LLM_MODEL = os.getenv("LAB_LLM_MODEL", "gpt-5.5")
LAB_PROPOSER = os.getenv("LAB_PROPOSER", "grid").strip().lower()
HISTORY_CONTEXT_N = 30              # so viele jüngste Hypothesen bekommt der LLM-Proposer als Kontext
FIRES_CACHE_MAX = 12                # so viele Fires-Cache-Dateien werden vorgehalten (gzip, je ~5-10 MB
#   beim Voll-Universum; unkomprimiert wären es >80 MB pro Datei — 2026-07-10 auf dem VPS gesehen)

# Suchraum: je tunebarer Parameter ein sortiertes Werteraster. Der Optimizer bewegt sich pro
# Zyklus um GENAU einen Schritt (±1) auf GENAU einem dieser Raster (wissenschaftliche Disziplin).
SEARCH_SPACE = {
    "factor":     [2.0, 2.5, 3.0, 3.5, 4.0],     # SuperTrend-Band-Breite
    "atr_period": [7, 10, 14, 20],               # ATR-Fenster
    "ma_regime":  [100, 150, 200],               # Regime-Filter-Länge
    "sl_mult":    [2.0, 2.5, 3.0, 3.5, 4.0],     # Stop-Loss in ATR
    "tp_mult":    [6.0, 8.0, 10.0, 12.0, 15.0],  # Take-Profit in ATR
    "trail_mult": [2.0, 2.5, 3.0, 3.5, 4.0],     # optionaler ATR-Trailing-Stop
}

_run_lock = threading.Lock()        # verhindert gleichzeitige Läufe (Web-Trigger)


# ── Kennzahlen / Zielfunktion ────────────────────────────────────────────────

def _cagr(return_pct: float, years: float) -> float:
    if years <= 0:
        return 0.0
    return ((1.0 + return_pct / 100.0) ** (1.0 / years) - 1.0) * 100.0


def mar(metrics: dict, years: float) -> float:
    """MAR-Ratio = CAGR / max. Drawdown (Rendite je Risiko). Robust gegen Drawdown≈0."""
    dd = metrics.get("max_drawdown_pct") or 0.0
    c = _cagr(metrics.get("return_pct", 0.0), years)
    return round(c / max(dd, 0.5), 3)


# ── Fires für einen Parametersatz (parallel über Ticker) ─────────────────────

def _ai_fires_ticker(args):
    """Worker (picklebar): sammelt die Fires der KI-Strategie für EINEN Ticker mit den
    übergebenen Kandidat-Parametern `params` (ohne die Live-Overrides zu berühren)."""
    ticker, df, params = args
    fires = []
    n = len(df)
    for i in range(engine.WARMUP_BARS, n - 1):
        sig = strat_mod.ai_adaptive_signal(ticker, {"1d": df.iloc[:i + 1]}, p=params)
        if not sig or sig.get("direction") != "long" or not sig.get("stop_loss"):
            continue
        fires.append({
            "date": str(df.index[i].date()), "ticker": ticker, "idx": i,
            "strength": sig.get("strength", 0.0), "entry": float(df["Close"].iloc[i]),
            "sl": float(sig["stop_loss"]), "tp": float(sig["take_profit"]),
        })
    return fires


def _fires_cache_key(data: dict, params: dict) -> str:
    """Cache-Schlüssel: Parametersatz + Datenstand (Ticker, erster/letzter Bar). Gleicher Satz
    am selben Tag (Web-Trigger + Cron, Champion in Folds) wird nicht doppelt gerechnet."""
    import hashlib
    last = max(df.index[-1] for df in data.values())
    first = min(df.index[0] for df in data.values())
    payload = json.dumps({"p": params, "t": sorted(data.keys()),
                          "a": str(first.date()), "z": str(last.date())},
                         sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _fires_cache_dir() -> Path:
    return LAB_DIR / "cache"


def _prune_fires_cache():
    try:
        files = sorted(_fires_cache_dir().glob("fires-*.json*"), key=lambda p: p.stat().st_mtime)
        for p in files[:-FIRES_CACHE_MAX]:
            p.unlink(missing_ok=True)
    except Exception:
        pass


def _fires_by_date(data: dict, params: dict, jobs) -> dict:
    import gzip
    cache_file = _fires_cache_dir() / f"fires-{_fires_cache_key(data, params)}.json.gz"
    try:
        with gzip.open(cache_file, "rt", encoding="utf-8") as f:
            cached = json.load(f)
        if isinstance(cached, dict) and "by_date" in cached:
            return cached["by_date"]
    except FileNotFoundError:
        pass
    except Exception as e:
        log.debug(f"[lab] Fires-Cache nicht lesbar ({e}) — rechne neu.")
    tasks = [(t, df, dict(params)) for t, df in data.items()]
    by_date: dict[str, list] = {}
    for fires in engine._pmap(_ai_fires_ticker, tasks, jobs):
        for f in fires:
            by_date.setdefault(f["date"], []).append(f)
    try:
        _fires_cache_dir().mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"by_date": by_date, "params": params}, f, default=str)
        tmp.replace(cache_file)
        _prune_fires_cache()
    except Exception as e:
        log.debug(f"[lab] Fires-Cache nicht schreibbar: {e}")
    return by_date


def _portfolio_metrics(data: dict, by_date: dict, params: dict | None = None) -> tuple[dict, list]:
    """Top-N-Portfolio-Kennzahlen (Hebel 1, fester Pool) für die gegebenen Fires.
    Gibt (kennzahlen, trades) zurück — die Trade-Liste braucht das Bootstrap-Gate."""
    trail_mult = (params or {}).get("trail_mult")
    trades = engine.simulate_portfolio(data, by_date, top_n=TOP_N, leverage=1.0,
                                       trade_size=TRADE_SIZE, max_concurrent=TOP_N,
                                       max_hold=engine.MAX_HOLD_DAYS,
                                       trail_mode=("atr" if trail_mult else None),
                                       trail_mult=(float(trail_mult) if trail_mult else None))
    m = metrics_mod.compute_metrics(trades, initial_capital=START_CAPITAL)
    pnl = m.get("total_pnl_eur", 0.0)
    return {
        "trades": m.get("trades", 0),
        "win_rate": m.get("win_rate"),
        "profit_factor": m.get("profit_factor"),
        "total_pnl_eur": round(pnl, 2),
        "return_pct": round(pnl / START_CAPITAL * 100, 2),
        "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
        "avg_trade_pct": m.get("avg_trade_pct", 0.0),
    }, trades


def _split(data: dict):
    """Walk-Forward-Grenzen: (fenster_start, split, fenster_ende) sowie IS-/OOS-Jahre."""
    last = max(df.index[-1] for df in data.values())
    first = last - pd.Timedelta(days=int(YEARS * 365.25))
    span_days = max((last - first).days, 1)
    split = first + pd.Timedelta(days=int(span_days * (1.0 - OOS_FRACTION)))
    is_years = max((split - first).days / 365.25, 1e-6)
    oos_years = max((last - split).days / 365.25, 1e-6)
    return first, split, last, is_years, oos_years


def _fold_bounds(split: pd.Timestamp, last: pd.Timestamp, n_folds: int = N_FOLDS) -> list[tuple]:
    """Teilt das OOS-Fenster [split, last] in n aufeinanderfolgende Teilfenster (Folds)."""
    span = max((last - split).days, 1)
    step = span / max(n_folds, 1)
    bounds = []
    for k in range(n_folds):
        a = split + pd.Timedelta(days=int(k * step))
        b = split + pd.Timedelta(days=int((k + 1) * step)) if k < n_folds - 1 \
            else last + pd.Timedelta(days=1)
        bounds.append((a, b))
    return bounds


def evaluate(data: dict, params: dict, split: pd.Timestamp, is_years: float,
             oos_years: float, jobs=None, folds: list[tuple] | None = None) -> dict:
    """Feuert EINMAL über das Universum und teilt per Einstiegsdatum in IS/OOS.
    IS endet EMBARGO_DAYS vor dem Split (Purging: Trades, die über die Grenze laufen,
    leaken sonst Testdaten ins Training). Gibt zusätzlich je OOS-Fold den MAR sowie die
    OOS-Trade-Liste (für das Bootstrap-Gate) zurück."""
    by_date = _fires_by_date(data, params, jobs)
    embargo = split - pd.Timedelta(days=EMBARGO_DAYS)
    is_bd = {d: v for d, v in by_date.items() if pd.Timestamp(d) < embargo}
    oos_bd = {d: v for d, v in by_date.items() if pd.Timestamp(d) >= split}
    is_m, _ = _portfolio_metrics(data, is_bd, params)
    oos_m, oos_trades = _portfolio_metrics(data, oos_bd, params)
    fold_mars = []
    for (a, b) in (folds or []):
        f_bd = {d: v for d, v in by_date.items() if a <= pd.Timestamp(d) < b}
        f_years = max((b - a).days / 365.25, 1e-6)
        f_m, _ = _portfolio_metrics(data, f_bd, params)
        fold_mars.append(mar(f_m, f_years))
    return {
        "is": is_m, "oos": oos_m,
        "is_mar": mar(is_m, is_years), "oos_mar": mar(oos_m, oos_years),
        "fold_mars": fold_mars,
        "oos_trades_list": oos_trades,
        "n_fires": sum(len(v) for v in by_date.values()),
    }


def _bootstrap_prob(cand_trades: list[dict], champ_trades: list[dict], years: float,
                    n_boot: int = BOOT_N, seed: int = 42) -> float | None:
    """Block-Bootstrap über die chronologische Trade-Sequenz: Anteil der Resamples, in denen
    der Kandidat den Champion bei MAR schlägt. None, wenn zu wenige Trades für eine Aussage.
    Fester Seed → reproduzierbar (gleiche Daten ⇒ gleiche Entscheidung)."""
    import random
    if len(cand_trades) < BOOT_MIN_TRADES or len(champ_trades) < BOOT_MIN_TRADES:
        return None
    rng = random.Random(seed)

    def mar_of(sample: list[dict]) -> float:
        m = metrics_mod.compute_metrics(sample, initial_capital=START_CAPITAL)
        pnl = m.get("total_pnl_eur", 0.0)
        return mar({"return_pct": pnl / START_CAPITAL * 100,
                    "max_drawdown_pct": m.get("max_drawdown_pct", 0.0)}, years)

    def resample(trades: list[dict]) -> list[dict]:
        n = len(trades)
        block = max(3, n // 10)                      # Blöcke erhalten Drawdown-Cluster
        out = []
        while len(out) < n:
            i = rng.randrange(0, max(n - block + 1, 1))
            out.extend(trades[i:i + block])
        return out[:n]

    wins = sum(1 for _ in range(n_boot)
               if mar_of(resample(cand_trades)) > mar_of(resample(champ_trades)))
    return round(wins / n_boot, 3)


# ── Kandidaten (eine Variable pro Zyklus) ────────────────────────────────────

def _nearest_index(grid: list, value) -> int:
    if value is None:
        return 0
    return min(range(len(grid)), key=lambda k: abs(grid[k] - value))


def candidates(champion: dict) -> list[dict]:
    """Alle Ein-Variablen-Nachbarn des Champions (±1 Rasterschritt je Parameter)."""
    out = []
    for param, grid in SEARCH_SPACE.items():
        cur = champion.get(param)
        idx = _nearest_index(grid, cur)
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(grid) and grid[j] != cur:
                new_params = dict(champion)
                new_params[param] = grid[j]
                out.append({"param": param, "old": cur, "new": grid[j],
                            "params": new_params, "proposer": "grid"})
    return out


# ── Lernen aus der eigenen Historie (hypotheses.jsonl) ───────────────────────

def history_stats(limit: int = 400) -> dict:
    """Verdichtet das Hypothesen-Log zu {(param, richtung): {"bestätigt": n, "widerlegt": m}}.
    Das ist der Lernspeicher des Labors: Richtungen, die wiederholt bestätigt wurden, sind
    aussichtsreicher; wiederholt widerlegte werden gemieden."""
    stats: dict[tuple, dict] = {}
    for h in load_hypotheses(limit=limit):
        if h.get("kind") != "candidate" or h.get("old") is None or h.get("new") is None:
            continue
        try:
            direction = "up" if float(h["new"]) > float(h["old"]) else "down"
        except (TypeError, ValueError):
            continue
        key = (h["param"], direction)
        s = stats.setdefault(key, {"bestätigt": 0, "widerlegt": 0})
        if h.get("actual") == "bestätigt":
            s["bestätigt"] += 1
        else:
            s["widerlegt"] += 1
    return stats


def _history_candidates(champion: dict, stats: dict | None = None) -> list[dict]:
    """Kandidaten AUS der eigenen Historie (Lern-Schritt über die blinden ±1-Nachbarn hinaus):
      1. Multi-Step: für Parameter mit klar bestätigter Richtung ein 2-Schritte-Sprung dorthin.
      2. Paar-Kandidat: die zwei am besten bestätigten Parameter-Richtungen kombiniert (je 1 Schritt)
         — der einzige kontrollierte Blick auf Parameter-Interaktionen; das Gate bleibt Richter.
    Wiederholt widerlegte Richtungen (widerlegt ≥ bestätigt + 2) werden übersprungen."""
    stats = history_stats() if stats is None else stats
    scored = []
    for (param, direction), s in stats.items():
        if param not in SEARCH_SPACE:
            continue
        score = s["bestätigt"] - s["widerlegt"]
        if score > 0:
            scored.append((score, param, direction))
    scored.sort(reverse=True)

    out = []
    # 1) Multi-Step-Sprung in die am besten bestätigte Richtung je Parameter.
    for score, param, direction in scored[:2]:
        grid = SEARCH_SPACE[param]
        cur = champion.get(param)
        idx = _nearest_index(grid, cur)
        j = min(max(idx + (2 if direction == "up" else -2), 0), len(grid) - 1)
        if grid[j] != cur:
            new_params = dict(champion)
            new_params[param] = grid[j]
            out.append({"param": param, "old": cur, "new": grid[j], "params": new_params,
                        "proposer": "history",
                        "reason": f"Richtung {direction} bisher {score}× netto bestätigt"})
    # 2) Paar-Kandidat aus den zwei besten Parametern (verschiedene Parameter, je 1 Schritt).
    seen_params = []
    for score, param, direction in scored:
        if param not in seen_params:
            seen_params.append(param)
        if len(seen_params) == 2:
            break
    if len(seen_params) == 2:
        new_params = dict(champion)
        moves = []
        for score, param, direction in scored:
            if param not in seen_params or any(m[0] == param for m in moves):
                continue
            grid = SEARCH_SPACE[param]
            cur = champion.get(param)
            idx = _nearest_index(grid, cur)
            j = min(max(idx + (1 if direction == "up" else -1), 0), len(grid) - 1)
            if grid[j] != cur:
                new_params[param] = grid[j]
                moves.append((param, cur, grid[j]))
        if len(moves) == 2:
            out.append({
                "param": "+".join(m[0] for m in moves),
                "old": ",".join(str(m[1]) for m in moves),
                "new": ",".join(str(m[2]) for m in moves),
                "params": new_params, "proposer": "history-pair",
                "reason": "Kombination der zwei am besten bestätigten Einzel-Richtungen",
            })
    return out


def _notify_admin(text: str) -> bool:
    """Best-effort Telegram-Warnung an den Admin-Chat (Token/Chat aus config). Wirft NIE,
    loggt den Token NIE. No-op, wenn TELEGRAM_TOKEN oder ADMIN_CHAT_ID fehlen."""
    token = getattr(config, "TELEGRAM_TOKEN", None)
    chat = getattr(config, "ADMIN_CHAT_ID", None)
    if not token or not chat:
        return False
    try:
        import urllib.request
        data = json.dumps({"chat_id": chat, "text": text, "parse_mode": "Markdown"}).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception as e:
        log.warning(f"[lab] Telegram-Warnung nicht zustellbar: {e}")
        return False


def _extract_json_array(text: str):
    """Extrahiert robust ein JSON-Array aus LLM-Text/Markdown."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].strip()
    start, end = s.find("["), s.rfind("]")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    return json.loads(s)


def _call_llm_proposer(champion: dict, context: dict) -> list[dict]:
    """Ruft die LLM (LAB_LLM_MODEL) für Hypothesen-Vorschläge auf. Kein API-Call ohne
    OPENAI_API_KEY. Jeder Fehlschlag (fehlender Key/Paket, ungültiges Modell, API-/JSON-Fehler)
    wird klar geloggt UND als Telegram-Warnung an den Admin gemeldet, dann [] → Raster-Fallback.
    Token/Key/Prompt werden nie geloggt oder gemeldet."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        log.warning("[lab] LAB_PROPOSER=llm, aber OPENAI_API_KEY fehlt — Raster-Proposer.")
        _notify_admin("⚠️ *Strategie-Labor:* LLM-Proposer aktiv, aber `OPENAI_API_KEY` fehlt — "
                      "es wird der Raster-Proposer genutzt.")
        return []
    try:
        from openai import OpenAI
    except Exception:
        log.warning("[lab] openai-Paket nicht installiert — Raster-Proposer.")
        _notify_admin("⚠️ *Strategie-Labor:* `openai`-Paket nicht installiert — "
                      "es wird der Raster-Proposer genutzt.")
        return []
    prompt = {
        "task": "Propose 1-3 single-parameter grid moves for ai_adaptive strategy lab.",
        "rules": [
            "Return JSON array only.",
            "Each item: {param, direction, steps?, reason} OR {param, new, reason}.",
            "Use exactly one param from search_space per item.",
            "'new' may be ANY value in that param's grid (multi-step jumps allowed);",
            "with 'direction' you may add 'steps' (>=1) to jump further.",
            "LEARN from 'history': avoid moves repeatedly refuted (actual='widerlegt'), "
            "prefer directions repeatedly confirmed (actual='bestätigt'), explore untested params.",
            "Do not decide live params or orders; backtest gate is judge.",
        ],
        "champion": champion,
        "search_space": SEARCH_SPACE,
        "context": context,
    }
    client = OpenAI(api_key=api_key)
    model = LAB_LLM_MODEL
    # Modell-ID HART prüfen: ist sie nicht verfügbar, klar warnen (Log + Telegram) und auf den
    # Raster-Proposer zurückfallen — kein stiller Fehlschlag mehr.
    try:
        available = {m.id for m in client.models.list().data}
    except Exception as e:
        available = None
        log.warning(f"[lab] OpenAI-Modellliste nicht abrufbar ({e}); versuche '{model}' direkt.")
    if available is not None and model not in available:
        log.warning(f"[lab] LLM-Modell '{model}' bei OpenAI nicht verfügbar — Fallback auf Raster.")
        _notify_admin(f"⚠️ *Strategie-Labor:* LLM-Modell `{model}` ist bei OpenAI nicht verfügbar — "
                      f"LLM-Proposer übersprungen, es wird der Raster-Proposer genutzt. "
                      f"Setze `LAB_LLM_MODEL` auf eine gültige Modell-ID.")
        return []
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a cautious quantitative trading research assistant. Output JSON only."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        )
        content = resp.choices[0].message.content or "[]"
        parsed = _extract_json_array(content)
    except Exception as e:
        log.warning(f"[lab] LLM-Aufruf/Parsing fehlgeschlagen ({type(e).__name__}) — Raster-Proposer.")
        _notify_admin(f"⚠️ *Strategie-Labor:* LLM-Aufruf fehlgeschlagen ({type(e).__name__}) — "
                      f"es wird der Raster-Proposer genutzt.")
        return []
    return parsed if isinstance(parsed, list) else []


def _candidate_from_proposal(champion: dict, proposal: dict) -> dict | None:
    param = str(proposal.get("param") or "").strip()
    if param not in SEARCH_SPACE:
        return None
    grid = SEARCH_SPACE[param]
    cur = champion.get(param)
    idx = _nearest_index(grid, cur)
    new = proposal.get("new")
    if new is None:
        # Richtung + optional 'steps' (>=1) auf das Raster abbilden → auch größere Sprünge.
        direction = str(proposal.get("direction") or "").strip().lower()
        sign = 1 if direction in ("up", "increase", "higher", "+") else \
            -1 if direction in ("down", "decrease", "lower", "-") else 0
        if sign == 0:
            return None
        try:
            steps = int(proposal.get("steps") or 1)
        except (TypeError, ValueError):
            steps = 1
        steps = max(1, min(steps, len(grid) - 1))
        new = grid[min(max(idx + sign * steps, 0), len(grid) - 1)]
    # Harte Validierung: gültiger Raster-Wert (größere Sprünge erlaubt), genau EINE Variable,
    # tatsächlich verändert. Kein Live-Write hier — nur ein Kandidat für das Backtest-Gate.
    if new not in grid or new == cur:
        return None
    new = grid[grid.index(new)]                 # auf kanonischen Rasterwert normieren (int→float)
    new_params = dict(champion)
    new_params[param] = new
    cand = {"param": param, "old": cur, "new": new, "params": new_params}
    if proposal.get("reason"):
        cand["reason"] = str(proposal["reason"])
    return cand


def _llm_candidates(champion: dict, context: dict) -> list[dict]:
    out, seen = [], set()
    for proposal in _call_llm_proposer(champion, context):
        if not isinstance(proposal, dict):
            continue
        cand = _candidate_from_proposal(champion, proposal)
        if not cand:
            continue
        key = (cand["param"], cand["new"])
        if key in seen:
            continue
        seen.add(key)
        cand["proposer"] = "llm"
        out.append(cand)
    return out


def _dedupe_candidates(cands: list[dict]) -> list[dict]:
    """Entfernt Kandidaten mit identischem Parametersatz (erste Nennung gewinnt —
    Reihenfolge llm → history → grid, damit die Herkunfts-Markierung informativ bleibt)."""
    out, seen = [], set()
    for c in cands:
        key = json.dumps(c["params"], sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def select_candidates(champion: dict, context: dict | None = None) -> list[dict]:
    """Kandidaten aus drei Quellen, alle durch DASSELBE Gate:
      - LLM (nur mit LAB_PROPOSER=llm): bekommt die Hypothesen-Historie als Lern-Kontext.
      - Historie: Multi-Step-Sprünge in bestätigte Richtungen + ein Paar-Kandidat.
      - Raster: die vollständigen ±1-Nachbarn als erschöpfende Basis (immer dabei)."""
    context = dict(context or {})
    stats = history_stats()
    context.setdefault("history", [
        {k: h.get(k) for k in ("param", "old", "new", "actual", "proposer")}
        for h in load_hypotheses(limit=HISTORY_CONTEXT_N) if h.get("kind") == "candidate"
    ])
    context.setdefault("history_stats", {f"{p}:{d}": s for (p, d), s in stats.items()})

    merged: list[dict] = []
    if os.getenv("LAB_PROPOSER", LAB_PROPOSER).strip().lower() == "llm":
        try:
            merged.extend(_llm_candidates(champion, context))
        except Exception:
            pass
    try:
        merged.extend(_history_candidates(champion, stats))
    except Exception as e:
        log.warning(f"[lab] History-Kandidaten fehlgeschlagen: {e}")
    merged.extend(candidates(champion))
    return _dedupe_candidates(merged)


# ── Gate: nur out-of-sample-Gewinner vorschlagen ─────────────────────────────

def _gate(best: dict | None, champ_eval: dict, oos_years: float) -> tuple[bool, str]:
    """Mehrstufiges OOS-Gate: Aggregat-MAR → Fold-Mehrheit → Bootstrap-Konfidenz → Drawdown.
    Fold-/Bootstrap-Stufen greifen nur, wenn die nötigen Daten vorliegen (Tests/Altbestand
    ohne diese Felder durchlaufen weiter die klassischen Stufen)."""
    champ_oos = champ_eval.get("oos") or {}
    champ_oos_mar = champ_eval.get("oos_mar", 0.0)
    if best is None:
        return False, "kein Kandidat mit genug In-Sample-Trades"
    oos = best["oos"]
    if oos["trades"] < MIN_TRADES:
        return False, f"zu wenige Out-of-Sample-Trades ({oos['trades']}<{MIN_TRADES})"
    if best["oos_mar"] <= champ_oos_mar:
        return False, f"schlägt Champion out-of-sample nicht (MAR {best['oos_mar']} ≤ {champ_oos_mar})"
    # Fold-Mehrheit: Aggregat-Sieg reicht nicht, wenn er an EINEM Teilfenster hängt.
    c_folds, b_folds = champ_eval.get("fold_mars") or [], best.get("fold_mars") or []
    if len(c_folds) >= 2 and len(c_folds) == len(b_folds):
        wins = sum(1 for b, c in zip(b_folds, c_folds) if b > c)
        need = len(c_folds) // 2 + 1
        if wins < need:
            return False, (f"gewinnt nur {wins}/{len(c_folds)} OOS-Folds (nötig {need}) — "
                           f"Sieg hängt an einem Teilfenster")
        best["fold_wins"] = f"{wins}/{len(c_folds)}"
    # Bootstrap-Konfidenz: Punktvergleich allein ist bei wenigen Trades Rauschen.
    boot = _bootstrap_prob(best.get("oos_trades_list") or [],
                           champ_eval.get("oos_trades_list") or [], oos_years)
    if boot is not None:
        best["boot_prob"] = boot
        if boot < BOOT_MIN_PROB:
            return False, (f"nicht robust: schlägt Champion nur in {round(boot*100)}% der "
                           f"Bootstrap-Resamples (nötig ≥{round(BOOT_MIN_PROB*100)}%)")
    champ_dd = champ_oos.get("max_drawdown_pct") or 0.0
    dd_cap = max(champ_dd * MAX_DD_TOLERANCE, champ_dd)
    if oos["max_drawdown_pct"] > max(dd_cap, 1e-9) and champ_dd > 0:
        return False, f"Out-of-Sample-Drawdown verschlechtert ({oos['max_drawdown_pct']}%>{round(dd_cap,1)}%)"
    if oos["max_drawdown_pct"] > MAX_DD_HARD:
        return False, f"Out-of-Sample-Drawdown über Hartlimit ({oos['max_drawdown_pct']}%>{MAX_DD_HARD}%)"
    return True, "schlägt Champion out-of-sample bei MAR ohne Drawdown-Verschlechterung"


# ── Rasterrand, Rollback-Kandidat, Reality-Check ─────────────────────────────

def _edge_warnings(params: dict) -> list[str]:
    """Parameter am Rasterrand: dort endet die Suche stillschweigend — Hinweis an den Menschen,
    das Raster (SEARCH_SPACE) bewusst zu erweitern. Keine Auto-Erweiterung (Leitplanke)."""
    out = []
    for p, grid in SEARCH_SPACE.items():
        v = params.get(p)
        if v is not None and v in (grid[0], grid[-1]):
            out.append(f"{p}={v} liegt am Rasterrand — ggf. SEARCH_SPACE erweitern")
    return out


def _latest_archived_params(exclude_params: dict) -> tuple[str, dict] | None:
    """Jüngste archivierte Champion-Version mit anderen Parametern (für den Regression-Guard)."""
    try:
        files = sorted(HISTORY_DIR.glob("v*.json"))
    except Exception:
        return None
    for f in reversed(files):
        d = _read_json(f, None)
        params = (d or {}).get("params")
        if params and params != exclude_params:
            return str(d.get("version") or f.stem.lstrip("v")), params
    return None


def reality_check(expected_oos: dict | None, days: int = 45, min_trades: int = 5) -> dict:
    """Vergleicht die jüngsten geschlossenen Trades der KI-Strategie (DB, alle Nutzer) mit der
    Backtest-Erwartung (Champion-OOS): Trefferquote + Ø-Trade-%. Große Abweichung heißt: das
    Modell, gegen das optimiert wird, bildet die Realität nicht ab — wichtigster Alarm des Labors."""
    out = {"checked_at": _now(), "days": days, "n": 0}
    try:
        from stockbot.core import db
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT signal_json, pnl_pct FROM trades WHERE status = 'closed' "
                "AND pnl_pct IS NOT NULL AND trade_date >= date('now', ?)",
                (f"-{int(days)} days",),
            ).fetchall()
    except Exception as e:
        out["note"] = f"DB nicht lesbar ({e})"
        return out
    pcts = []
    for r in rows:
        try:
            sig = json.loads(r["signal_json"])
        except Exception:
            continue
        if sig.get("strategy") == TARGET_KEY:
            pcts.append(float(r["pnl_pct"]))
    out["n"] = len(pcts)
    if len(pcts) < min_trades:
        out["note"] = f"zu wenige Live-Trades (<{min_trades}) für einen Abgleich"
        return out
    live_win = round(100.0 * sum(1 for p in pcts if p > 0) / len(pcts), 1)
    live_avg = round(sum(pcts) / len(pcts), 3)
    out.update({"live_win_rate": live_win, "live_avg_trade_pct": live_avg})
    if expected_oos:
        exp_win = expected_oos.get("win_rate")
        exp_avg = expected_oos.get("avg_trade_pct")
        out.update({"expected_win_rate": exp_win, "expected_avg_trade_pct": exp_avg})
        divergent = bool(exp_win is not None and abs(live_win - exp_win) > 15.0)
        if exp_avg is not None and exp_avg * live_avg < 0 and abs(live_avg) > 0.05:
            divergent = True                          # Vorzeichen des Ø-Trades kippt deutlich
        out["divergent"] = divergent
    return out


# ── JSON-Zustand / Log ───────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _write_json(path: Path, payload: dict):
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def state_path() -> Path:      return LAB_DIR / "state.json"
def pending_path() -> Path:    return LAB_DIR / "pending.json"
def hypotheses_path() -> Path: return LAB_DIR / "hypotheses.jsonl"


def load_state() -> dict:
    st = _read_json(state_path(), None)
    if st:
        return st
    # Saat-Zustand: aktuelle Live-Parameter, noch kein Lauf.
    return {
        "target_key": TARGET_KEY, "version": "01",
        "live_params": strat_mod.strategy_runtime_params(TARGET_KEY),
        "updated_at": None, "status": "idle", "last_run_at": None,
        "champion": None, "last_cycle": None,
        "config": {"years": YEARS, "oos_fraction": OOS_FRACTION, "min_trades": MIN_TRADES,
                   "objective": "MAR (CAGR/maxDD)", "top_n": TOP_N},
    }


def load_pending() -> dict | None:
    p = _read_json(pending_path(), None)
    return p if (p and p.get("proposal")) else None


def load_hypotheses(limit: int = 60) -> list[dict]:
    try:
        lines = hypotheses_path().read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    out.reverse()   # neueste zuerst
    return out


def _append_hypotheses(records: list[dict]):
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    with hypotheses_path().open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _set_status(status: str, **extra):
    st = load_state()
    st["status"] = status
    st.update(extra)
    _write_json(state_path(), st)


# ── Ein Optimierungszyklus ───────────────────────────────────────────────────

def run_cycle(data: dict | None = None, limit: int | None = None, jobs=None,
              log=print) -> dict:
    """Führt einen vollständigen Walk-Forward-Zyklus aus, schreibt Zustand/Log/Pending und gibt
    eine Zusammenfassung zurück. `data` (vorab geladen) überspringt den Download (für Tests)."""
    t0 = time.time()
    if data is None:
        tickers = universes.get_tickers(DEFAULT_REGION, auto=True)
        if limit:
            tickers = tickers[:limit]
        log(f"[lab] lade {len(tickers)} Ticker ({YEARS+1}J Historie) ...")
        data = engine._download_daily(tickers, YEARS)
    if not data:
        raise RuntimeError("keine Kursdaten geladen")
    log(f"[lab] {len(data)} Ticker mit Historie; walk-forward optimieren ...")

    first, split, last, is_years, oos_years = _split(data)
    folds = _fold_bounds(split, last)
    st = load_state()
    version = st.get("version", "01")
    existing_pending = load_pending()                 # nie einen wartenden Vorschlag überschreiben
    # Champion = aktuelle Live-Parameter (oder Labor-Champion aus dem letzten Lauf, falls neuer).
    champion = dict((st.get("champion") or {}).get("params") or
                    strat_mod.strategy_runtime_params(TARGET_KEY))

    champ_eval = evaluate(data, champion, split, is_years, oos_years, jobs, folds=folds)
    champ_oos_mar = champ_eval["oos_mar"]
    log(f"[lab] Champion v{version}: IS-MAR {champ_eval['is_mar']} | OOS-MAR {champ_oos_mar} "
        f"(OOS Return {champ_eval['oos']['return_pct']}% / DD {champ_eval['oos']['max_drawdown_pct']}%, "
        f"{champ_eval['oos']['trades']} Trades, Folds {champ_eval['fold_mars']})")

    ctx = {"champion_eval": {k: v for k, v in champ_eval.items() if k != "oos_trades_list"},
           "version": version}
    cands = select_candidates(champion, ctx)
    # Regression-Guard: die zuletzt archivierte Version läuft als Rollback-Kandidat mit —
    # schlägt der Vorgänger den amtierenden Champion durch dasselbe Gate, wird der
    # Rückbau vorgeschlagen (Menschen-Gate wie immer).
    archived = _latest_archived_params(champion)
    if archived:
        arch_version, arch_params = archived
        cands.append({"param": "rollback", "old": f"v{version}", "new": f"v{arch_version}",
                      "params": arch_params, "proposer": "rollback",
                      "reason": "Vorgänger-Parameter als Regression-Guard"})
    log(f"[lab] {len(cands)} Kandidaten prüfen "
        f"({', '.join(sorted({c.get('proposer', 'grid') for c in cands}))}) ...")
    cycle_id = _now()
    results, hyp_records = [], []
    for k, cand in enumerate(cands, 1):
        ev = evaluate(data, cand["params"], split, is_years, oos_years, jobs, folds=folds)
        cand.update({"is": ev["is"], "oos": ev["oos"],
                     "is_mar": ev["is_mar"], "oos_mar": ev["oos_mar"], "n_fires": ev["n_fires"],
                     "fold_mars": ev["fold_mars"], "oos_trades_list": ev["oos_trades_list"]})
        results.append(cand)
        beats_oos = ev["oos_mar"] > champ_oos_mar
        hyp_records.append({
            "ts": cycle_id, "version": version, "param": cand["param"],
            "old": cand["old"], "new": cand["new"],
            "proposer": cand.get("proposer", "grid"),
            "predicted": "OOS-MAR steigt",
            "champion_oos_mar": champ_oos_mar, "candidate_oos_mar": ev["oos_mar"],
            "candidate_is_mar": ev["is_mar"], "oos_trades": ev["oos"]["trades"],
            "fold_mars": ev["fold_mars"],
            "actual": "bestätigt" if beats_oos else "widerlegt",
            "kind": "candidate",
        })
        log(f"[lab]   {k}/{len(cands)}  [{cand.get('proposer', 'grid')}] "
            f"{cand['param']}: {cand['old']}→{cand['new']}  "
            f"IS-MAR {ev['is_mar']} | OOS-MAR {ev['oos_mar']}  "
            f"({'schlägt' if beats_oos else 'unter'} Champion)")

    # Auswahl auf In-Sample (Training), Validierung auf Out-of-Sample (Gate).
    valid = [c for c in results if c["is"]["trades"] >= MIN_TRADES]
    best = max(valid, key=lambda c: c["is_mar"], default=None)
    gate_ok, reason = _gate(best, champ_eval, oos_years)

    # Bestätigungs-Serie: derselbe Kandidat muss das Gate STREAK_REQUIRED Zyklen in Folge
    # bestehen, bevor er vorgeschlagen wird — Schutz gegen Zufallstreffer auf demselben OOS.
    streak = dict(st.get("streak") or {})
    promote = False
    if gate_ok and best is not None:
        best_key = json.dumps(best["params"], sort_keys=True, default=str)
        count = streak.get("count", 0) + 1 if streak.get("key") == best_key else 1
        streak = {"key": best_key, "param": best["param"], "old": best["old"],
                  "new": best["new"], "count": count, "required": STREAK_REQUIRED,
                  "last_ts": cycle_id}
        if count >= STREAK_REQUIRED:
            promote = True
        else:
            reason = (f"Gate bestanden — Serie {count}/{STREAK_REQUIRED}, "
                      f"wartet auf Bestätigung im nächsten Zyklus")
    else:
        streak = {}                                  # Serie reißt, sobald das Gate scheitert

    proposal = None
    if promote and existing_pending is None:
        next_version = f"{int(version) + 1:02d}"
        proposal = {
            "version": next_version, "created_at": cycle_id, "param": best["param"],
            "old": best["old"], "new": best["new"], "params": best["params"],
            "proposer": best.get("proposer", "grid"),
            "kind": "rollback" if best.get("proposer") == "rollback" else "tuning",
            "champion_params": champion, "reason": reason,
            "champion_oos_mar": champ_oos_mar, "candidate_oos_mar": best["oos_mar"],
            "champion_oos": champ_eval["oos"], "candidate_oos": best["oos"],
            "candidate_is": best["is"], "is_mar": best["is_mar"],
            "fold_mars": best.get("fold_mars"), "fold_wins": best.get("fold_wins"),
            "boot_prob": best.get("boot_prob"),
            "edge_warnings": _edge_warnings(best["params"]),
        }
        streak = {}                                  # Serie erfüllt → zurücksetzen
    elif promote and existing_pending is not None:
        reason = "Gate + Serie bestanden, aber ein Vorschlag wartet bereits auf Freigabe"
        promote = False

    decision = ("vorgeschlagen" if proposal else
                (f"Serie {streak['count']}/{STREAK_REQUIRED}" if streak else "kein Vorschlag"))
    hyp_records.append({
        "ts": cycle_id, "version": version, "kind": "decision",
        "decision": decision, "reason": reason,
        "best_param": (best or {}).get("param"), "best_old": (best or {}).get("old"),
        "best_new": (best or {}).get("new"),
        "best_proposer": (best or {}).get("proposer"),
        "boot_prob": (best or {}).get("boot_prob"),
        "fold_wins": (best or {}).get("fold_wins"),
    })
    _append_hypotheses(hyp_records)

    if existing_pending is None:
        _write_json(pending_path(), {"proposal": proposal, "updated_at": cycle_id})

    # Reality-Check (Live vs. Backtest-Erwartung) + Rasterrand-Hinweise — best effort.
    reality = reality_check(champ_eval["oos"])
    if reality.get("divergent"):
        _append_hypotheses([{"ts": cycle_id, "kind": "reality_check", **reality}])
        _notify_admin("⚠️ *Strategie-Labor Reality-Check:* Live-Trades weichen deutlich von der "
                      f"Backtest-Erwartung ab (live {reality.get('live_win_rate')}% Treffer vs. "
                      f"erwartet {reality.get('expected_win_rate')}%, n={reality.get('n')}). "
                      "Das Optimierungs-Modell bildet die Realität evtl. nicht ab.")
    edge = _edge_warnings(champion)
    if proposal and proposal.get("edge_warnings"):
        _notify_admin("ℹ️ *Strategie-Labor:* Vorschlag liegt am Rasterrand — "
                      + "; ".join(proposal["edge_warnings"]))

    took = round(time.time() - t0, 1)
    last_cycle = {
        "ts": cycle_id, "n_candidates": len(cands), "n_tickers": len(data),
        "decision": decision, "reason": reason,
        "champion_is_mar": champ_eval["is_mar"], "champion_oos_mar": champ_oos_mar,
        "champion_fold_mars": champ_eval["fold_mars"],
        "best": None if not best else {
            "param": best["param"], "old": best["old"], "new": best["new"],
            "proposer": best.get("proposer", "grid"),
            "is_mar": best["is_mar"], "oos_mar": best["oos_mar"],
            "fold_mars": best.get("fold_mars"), "fold_wins": best.get("fold_wins"),
            "boot_prob": best.get("boot_prob"),
            "oos_return_pct": best["oos"]["return_pct"], "oos_dd": best["oos"]["max_drawdown_pct"],
            "oos_trades": best["oos"]["trades"],
        },
        "took_sec": took,
    }
    st = load_state()
    st.update({
        "target_key": TARGET_KEY, "version": version, "status": "idle",
        "updated_at": cycle_id, "last_run_at": cycle_id,
        "live_params": strat_mod.strategy_runtime_params(TARGET_KEY),
        "champion": {"params": champion, "is": champ_eval["is"], "oos": champ_eval["oos"],
                     "is_mar": champ_eval["is_mar"], "oos_mar": champ_oos_mar,
                     "fold_mars": champ_eval["fold_mars"]},
        "window": {"start": str(first.date()), "split": str(split.date()), "end": str(last.date()),
                   "is_years": round(is_years, 2), "oos_years": round(oos_years, 2),
                   "embargo_days": EMBARGO_DAYS, "n_folds": len(folds)},
        "streak": streak,
        "reality": reality,
        "edge_warnings": edge,
        "last_cycle": last_cycle,
        "config": {"years": YEARS, "oos_fraction": OOS_FRACTION, "min_trades": MIN_TRADES,
                   "objective": "MAR (CAGR/maxDD)", "top_n": TOP_N,
                   "embargo_days": EMBARGO_DAYS, "n_folds": N_FOLDS,
                   "streak_required": STREAK_REQUIRED,
                   "bootstrap": {"n": BOOT_N, "min_prob": BOOT_MIN_PROB},
                   "cost_pct": config.BACKTEST_COST_PCT,
                   "caveats": [SURVIVORSHIP_NOTE]},
    })
    _write_json(state_path(), st)
    log(f"[lab] fertig in {took}s → {decision} ({reason})")
    return last_cycle


def start_background_cycle(limit: int | None = WEB_LIMIT) -> bool:
    """Startet einen Zyklus in einem Hintergrund-Thread (Web-Trigger). Gibt False zurück,
    wenn bereits ein Lauf aktiv ist."""
    if not _run_lock.acquire(blocking=False):
        return False

    def _work():
        try:
            _set_status("running", last_run_at=_now())
            run_cycle(limit=limit, log=lambda m: None)
        except Exception as e:
            _set_status("error", error=str(e))
        finally:
            _run_lock.release()

    threading.Thread(target=_work, name="lab-cycle", daemon=True).start()
    return True


def is_running() -> bool:
    return _run_lock.locked()


# ── Freigabe / Verwerfen (Menschen-Gate) ─────────────────────────────────────

def apply_pending() -> dict:
    """Gibt den anstehenden Vorschlag frei: schreibt die Kandidat-Parameter als Live-Override
    in strategy_configs, archiviert den vorigen Champion, bumpt die Version, leert pending."""
    pend = load_pending()
    if not pend:
        return {"ok": False, "msg": "kein anstehender Vorschlag"}
    proposal = pend["proposal"]
    strat = strat_mod.REGISTRY.get(TARGET_KEY)
    label = strat.label if strat else TARGET_KEY
    desc = strat.description if strat else ""

    st = load_state()
    old_version = st.get("version", "01")
    # vorigen Champion archivieren
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(HISTORY_DIR / f"v{old_version}.json",
                {"version": old_version, "archived_at": _now(),
                 "params": (st.get("champion") or {}).get("params")
                 or strat_mod.strategy_runtime_params(TARGET_KEY),
                 "champion": st.get("champion")})

    from stockbot.core import db
    db.upsert_strategy_config(TARGET_KEY, label, desc, params=proposal["params"], enabled=True)
    strat_mod.refresh_strategy_overrides_cache()

    new_version = proposal["version"]
    st.update({
        "version": new_version, "updated_at": _now(),
        "live_params": strat_mod.strategy_runtime_params(TARGET_KEY),
        "champion": {"params": proposal["params"], "oos": proposal.get("candidate_oos"),
                     "is": proposal.get("candidate_is"), "oos_mar": proposal.get("candidate_oos_mar"),
                     "is_mar": proposal.get("is_mar")},
    })
    _write_json(state_path(), st)
    _write_json(pending_path(), {"proposal": None, "updated_at": _now()})
    _append_hypotheses([{
        "ts": _now(), "kind": "applied", "version": new_version,
        "param": proposal["param"], "old": proposal["old"], "new": proposal["new"],
        "candidate_oos_mar": proposal.get("candidate_oos_mar"),
    }])
    return {"ok": True, "msg": f"KI-Strategie v{new_version} übernommen "
            f"({proposal['param']}: {proposal['old']}→{proposal['new']}).",
            "version": new_version}


def reject_pending() -> dict:
    """Verwirft den anstehenden Vorschlag (protokolliert, kein Live-Eingriff)."""
    pend = load_pending()
    if not pend:
        return {"ok": False, "msg": "kein anstehender Vorschlag"}
    proposal = pend["proposal"]
    _append_hypotheses([{
        "ts": _now(), "kind": "rejected", "version": proposal.get("version"),
        "param": proposal["param"], "old": proposal["old"], "new": proposal["new"],
    }])
    _write_json(pending_path(), {"proposal": None, "updated_at": _now()})
    return {"ok": True, "msg": f"Vorschlag verworfen ({proposal['param']}: "
            f"{proposal['old']}→{proposal['new']})."}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Strategie-Labor: Walk-Forward-Auto-Tuning der KI-Strategie.")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Ticker (Schnelltest)")
    ap.add_argument("--jobs", type=int, default=0, help="parallele Prozesse (0 = alle Kerne)")
    ap.add_argument("--apply", action="store_true", help="anstehenden Vorschlag freigeben")
    ap.add_argument("--reject", action="store_true", help="anstehenden Vorschlag verwerfen")
    args = ap.parse_args()

    if args.apply:
        print(apply_pending()["msg"]); return
    if args.reject:
        print(reject_pending()["msg"]); return

    jobs = args.jobs if args.jobs > 0 else (os.cpu_count() or 1)
    summary = run_cycle(limit=args.limit, jobs=jobs)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
