"""
Strategie-Labor — Walk-Forward-Selbstoptimierung der KI-Strategie `ai_adaptive`.

Idee (bewusst anders als naive Live-Tuner): Parameter werden NICHT an ein paar Live-Trades
angepasst, sondern gegen die Backtest-Engine über die volle S&P-500 optimiert — mit sauberer
Trennung in In-Sample (Training) und Out-of-Sample (Test). Ein Kandidat wird nur vorgeschlagen,
wenn er den amtierenden Champion **out-of-sample** schlägt. So bleibt Overfitting sichtbar und
wird nicht befördert.

Ablauf eines Zyklus (`run_cycle`):
  1. Champion = aktuelle Live-Parameter der Strategie (strategy_configs → strategy_runtime_params).
  2. Kandidaten = Champion mit GENAU EINER geänderten Variablen (±1 Rasterschritt je Parameter).
  3. Jede Variante EINMAL über das Universum feuern; Fires per Einstiegsdatum in IS/OOS teilen.
  4. Bewertung: MAR = CAGR / max. Drawdown (Rendite je Risiko), je für IS und OOS.
  5. Auswahl: bester Kandidat nach IS-MAR (nur mit genug IS-Trades).
  6. Gate: nur „vorschlagen", wenn er den Champion OOS bei MAR schlägt UND den OOS-Drawdown
     nicht über Toleranz/Hartlimit verschlechtert und genug OOS-Trades hat.
  7. Alles geloggt: falsifizierbares Hypothesen-Log (vorhergesagt vs. tatsächlich) + Zustand;
     ein bestandener Vorschlag landet in pending.json und wartet auf Menschen-Freigabe.

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
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockbot.paths import DATA_DIR
from stockbot.market import strategies as strat_mod
from stockbot.market import universes
from stockbot.backtest import engine
from stockbot.core import metrics as metrics_mod
from stockbot.config import DEFAULT_REGION

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

WEB_LIMIT = 150                     # Web-Trigger nutzt ein kleineres Universum (schneller, interaktiv)
LAB_LLM_MODEL = os.getenv("LAB_LLM_MODEL", "gpt-5.5")
LAB_PROPOSER = os.getenv("LAB_PROPOSER", "grid").strip().lower()

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


def _fires_by_date(data: dict, params: dict, jobs) -> dict:
    tasks = [(t, df, dict(params)) for t, df in data.items()]
    by_date: dict[str, list] = {}
    for fires in engine._pmap(_ai_fires_ticker, tasks, jobs):
        for f in fires:
            by_date.setdefault(f["date"], []).append(f)
    return by_date


def _portfolio_metrics(data: dict, by_date: dict, params: dict | None = None) -> dict:
    """Top-N-Portfolio-Kennzahlen (Hebel 1, fester Pool) für die gegebenen Fires."""
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
    }


def _split(data: dict):
    """Walk-Forward-Grenzen: (fenster_start, split, fenster_ende) sowie IS-/OOS-Jahre."""
    last = max(df.index[-1] for df in data.values())
    first = last - pd.Timedelta(days=int(YEARS * 365.25))
    span_days = max((last - first).days, 1)
    split = first + pd.Timedelta(days=int(span_days * (1.0 - OOS_FRACTION)))
    is_years = max((split - first).days / 365.25, 1e-6)
    oos_years = max((last - split).days / 365.25, 1e-6)
    return first, split, last, is_years, oos_years


def evaluate(data: dict, params: dict, split: pd.Timestamp, is_years: float,
             oos_years: float, jobs=None) -> dict:
    """Feuert EINMAL über das Universum und teilt per Einstiegsdatum in IS/OOS.
    Gibt {is, oos, is_mar, oos_mar, n_fires} zurück."""
    by_date = _fires_by_date(data, params, jobs)
    is_bd = {d: v for d, v in by_date.items() if pd.Timestamp(d) < split}
    oos_bd = {d: v for d, v in by_date.items() if pd.Timestamp(d) >= split}
    is_m = _portfolio_metrics(data, is_bd, params)
    oos_m = _portfolio_metrics(data, oos_bd, params)
    return {
        "is": is_m, "oos": oos_m,
        "is_mar": mar(is_m, is_years), "oos_mar": mar(oos_m, oos_years),
        "n_fires": sum(len(v) for v in by_date.values()),
    }


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
                out.append({"param": param, "old": cur, "new": grid[j], "params": new_params})
    return out


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
    """Ruft GPT-5.5 für Hypothesen-Vorschläge auf. Kein API-Call ohne OPENAI_API_KEY."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        from openai import OpenAI
    except Exception:
        return []
    prompt = {
        "task": "Propose 1-3 single-parameter grid moves for ai_adaptive strategy lab.",
        "rules": [
            "Return JSON array only.",
            "Each item: {param, direction, reason} OR {param, new, reason}.",
            "Use exactly one param from search_space per item.",
            "Do not decide live params or orders; backtest gate is judge.",
        ],
        "champion": champion,
        "search_space": SEARCH_SPACE,
        "context": context,
    }
    client = OpenAI(api_key=api_key)
    # Modellliste best-effort prüfen; kein Logging von Keys/Prompt.
    try:
        models = {m.id for m in client.models.list().data}
        model = LAB_LLM_MODEL if LAB_LLM_MODEL in models else LAB_LLM_MODEL
    except Exception:
        model = LAB_LLM_MODEL
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a cautious quantitative trading research assistant. Output JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=0.2,
    )
    content = resp.choices[0].message.content or "[]"
    parsed = _extract_json_array(content)
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
        direction = str(proposal.get("direction") or "").strip().lower()
        target_idx = idx + (1 if direction in ("up", "increase", "higher", "+") else -1 if direction in ("down", "decrease", "lower", "-") else 0)
        if not (0 <= target_idx < len(grid)):
            return None
        new = grid[target_idx]
    # Harte Validierung: nur gültiger direkter Raster-Nachbar, genau eine Variable.
    if new not in grid:
        return None
    new_idx = grid.index(new)
    if abs(new_idx - idx) != 1 or new == cur:
        return None
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
        out.append(cand)
    return out


def select_candidates(champion: dict, context: dict | None = None) -> list[dict]:
    proposer = os.getenv("LAB_PROPOSER", LAB_PROPOSER).strip().lower()
    if proposer == "llm":
        llm = _llm_candidates(champion, context or {})
        if llm:
            return llm
    return candidates(champion)


# ── Gate: nur out-of-sample-Gewinner vorschlagen ─────────────────────────────

def _gate(best: dict | None, champ_oos: dict, champ_oos_mar: float) -> tuple[bool, str]:
    if best is None:
        return False, "kein Kandidat mit genug In-Sample-Trades"
    oos = best["oos"]
    if oos["trades"] < MIN_TRADES:
        return False, f"zu wenige Out-of-Sample-Trades ({oos['trades']}<{MIN_TRADES})"
    if best["oos_mar"] <= champ_oos_mar:
        return False, f"schlägt Champion out-of-sample nicht (MAR {best['oos_mar']} ≤ {champ_oos_mar})"
    champ_dd = champ_oos.get("max_drawdown_pct") or 0.0
    dd_cap = max(champ_dd * MAX_DD_TOLERANCE, champ_dd)
    if oos["max_drawdown_pct"] > max(dd_cap, 1e-9) and champ_dd > 0:
        return False, f"Out-of-Sample-Drawdown verschlechtert ({oos['max_drawdown_pct']}%>{round(dd_cap,1)}%)"
    if oos["max_drawdown_pct"] > MAX_DD_HARD:
        return False, f"Out-of-Sample-Drawdown über Hartlimit ({oos['max_drawdown_pct']}%>{MAX_DD_HARD}%)"
    return True, "schlägt Champion out-of-sample bei MAR ohne Drawdown-Verschlechterung"


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
    st = load_state()
    version = st.get("version", "01")
    # Champion = aktuelle Live-Parameter (oder Labor-Champion aus dem letzten Lauf, falls neuer).
    champion = dict((st.get("champion") or {}).get("params") or
                    strat_mod.strategy_runtime_params(TARGET_KEY))

    champ_eval = evaluate(data, champion, split, is_years, oos_years, jobs)
    champ_oos_mar = champ_eval["oos_mar"]
    log(f"[lab] Champion v{version}: IS-MAR {champ_eval['is_mar']} | OOS-MAR {champ_oos_mar} "
        f"(OOS Return {champ_eval['oos']['return_pct']}% / DD {champ_eval['oos']['max_drawdown_pct']}%, "
        f"{champ_eval['oos']['trades']} Trades)")

    cands = select_candidates(champion, {"champion_eval": champ_eval, "version": version})
    proposer = "llm" if os.getenv("LAB_PROPOSER", LAB_PROPOSER).strip().lower() == "llm" else "grid"
    log(f"[lab] {len(cands)} Ein-Variablen-Kandidaten prüfen ({proposer}) ...")
    cycle_id = _now()
    results, hyp_records = [], []
    for k, cand in enumerate(cands, 1):
        ev = evaluate(data, cand["params"], split, is_years, oos_years, jobs)
        cand.update({"is": ev["is"], "oos": ev["oos"],
                     "is_mar": ev["is_mar"], "oos_mar": ev["oos_mar"], "n_fires": ev["n_fires"]})
        results.append(cand)
        beats_oos = ev["oos_mar"] > champ_oos_mar
        hyp_records.append({
            "ts": cycle_id, "version": version, "param": cand["param"],
            "old": cand["old"], "new": cand["new"],
            "predicted": "OOS-MAR steigt",
            "champion_oos_mar": champ_oos_mar, "candidate_oos_mar": ev["oos_mar"],
            "candidate_is_mar": ev["is_mar"], "oos_trades": ev["oos"]["trades"],
            "actual": "bestätigt" if beats_oos else "widerlegt",
            "kind": "candidate",
        })
        log(f"[lab]   {k}/{len(cands)}  {cand['param']}: {cand['old']}→{cand['new']}  "
            f"IS-MAR {ev['is_mar']} | OOS-MAR {ev['oos_mar']}  "
            f"({'schlägt' if beats_oos else 'unter'} Champion)")

    # Auswahl auf In-Sample (Training), Validierung auf Out-of-Sample (Gate).
    valid = [c for c in results if c["is"]["trades"] >= MIN_TRADES]
    best = max(valid, key=lambda c: c["is_mar"], default=None)
    promote, reason = _gate(best, champ_eval["oos"], champ_oos_mar)

    proposal = None
    if promote:
        next_version = f"{int(version) + 1:02d}"
        proposal = {
            "version": next_version, "created_at": cycle_id, "param": best["param"],
            "old": best["old"], "new": best["new"], "params": best["params"],
            "champion_params": champion, "reason": reason,
            "champion_oos_mar": champ_oos_mar, "candidate_oos_mar": best["oos_mar"],
            "champion_oos": champ_eval["oos"], "candidate_oos": best["oos"],
            "candidate_is": best["is"], "is_mar": best["is_mar"],
        }
    hyp_records.append({
        "ts": cycle_id, "version": version, "kind": "decision",
        "decision": "vorgeschlagen" if promote else "kein Vorschlag", "reason": reason,
        "best_param": (best or {}).get("param"), "best_old": (best or {}).get("old"),
        "best_new": (best or {}).get("new"),
    })
    _append_hypotheses(hyp_records)

    _write_json(pending_path(), {"proposal": proposal, "updated_at": cycle_id})

    took = round(time.time() - t0, 1)
    last_cycle = {
        "ts": cycle_id, "n_candidates": len(cands), "n_tickers": len(data),
        "decision": "vorgeschlagen" if promote else "kein Vorschlag", "reason": reason,
        "champion_is_mar": champ_eval["is_mar"], "champion_oos_mar": champ_oos_mar,
        "best": None if not best else {
            "param": best["param"], "old": best["old"], "new": best["new"],
            "is_mar": best["is_mar"], "oos_mar": best["oos_mar"],
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
                     "is_mar": champ_eval["is_mar"], "oos_mar": champ_oos_mar},
        "window": {"start": str(first.date()), "split": str(split.date()), "end": str(last.date()),
                   "is_years": round(is_years, 2), "oos_years": round(oos_years, 2)},
        "last_cycle": last_cycle,
        "config": {"years": YEARS, "oos_fraction": OOS_FRACTION, "min_trades": MIN_TRADES,
                   "objective": "MAR (CAGR/maxDD)", "top_n": TOP_N},
    })
    _write_json(state_path(), st)
    log(f"[lab] fertig in {took}s → {last_cycle['decision']} ({reason})")
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
