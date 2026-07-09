"""
Tests für das Strategie-Labor (stockbot/optimize/lab.py) + die KI-Strategie `ai_adaptive`.

Deckt ab:
- ai_adaptive ist registriert, feuert im Aufwärtstrend, schweigt im Abwärtstrend, und der
  Optimizer kann Kandidat-Parameter direkt einspeisen (ohne Live-Overrides).
- Zielfunktion MAR/CAGR.
- Kandidaten ändern GENAU eine Variable (±1 Rasterschritt).
- Das Out-of-Sample-Gate schlägt nur echte OOS-Gewinner vor (nicht IS-Overfits, nicht bei
  zu wenigen Trades oder verschlechtertem Drawdown).
- Ein vollständiger (kleiner) Zyklus schreibt Zustand/Pending/Hypothesen.
- Freigabe schreibt die Live-Parameter in strategy_configs; Verwerfen lässt Live unberührt.

Lauf:  pytest tests/test_optimize_lab.py   (offline, keine Netzabhängigkeit)
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stockbot.market import strategies as strat_mod
from stockbot.optimize import lab


# ── Hilfen ────────────────────────────────────────────────────────────────────

def _series(slope: float, noise: float, n: int = 520, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    base = 100 * np.exp(np.cumsum(rng.normal(slope, noise, n)))
    h = base * (1 + np.abs(rng.normal(0, 0.006, n)))
    l = base * (1 - np.abs(rng.normal(0, 0.006, n)))
    return pd.DataFrame({"Open": base, "High": h, "Low": l, "Close": base,
                         "Volume": np.full(n, 1e6)}, index=idx)


@pytest.fixture
def lab_tmp(monkeypatch):
    """Isoliertes Lab-Verzeichnis + kleine, schnelle Fenster/Suchraum.
    STREAK_REQUIRED=1 und EMBARGO=0, damit Einzel-Zyklus-Tests direkt vorschlagen können;
    die Streak-/Embargo-Mechanik hat eigene Tests."""
    tmp = Path(tempfile.mkdtemp(prefix="labtest_"))
    monkeypatch.setattr(lab, "LAB_DIR", tmp)
    monkeypatch.setattr(lab, "HISTORY_DIR", tmp / "history")
    monkeypatch.setattr(lab, "YEARS", 1)
    monkeypatch.setattr(lab, "OOS_FRACTION", 0.4)
    monkeypatch.setattr(lab, "MIN_TRADES", 1)
    monkeypatch.setattr(lab, "STREAK_REQUIRED", 1)
    monkeypatch.setattr(lab, "EMBARGO_DAYS", 0)
    monkeypatch.setattr(lab, "SEARCH_SPACE",
                        {"tp_mult": [6.0, 8.0, 10.0, 12.0], "factor": [2.5, 3.0, 3.5]})
    return tmp


# ── ai_adaptive-Strategie ─────────────────────────────────────────────────────

def test_ai_adaptive_registered():
    assert "ai_adaptive" in strat_mod.REGISTRY
    p = strat_mod.strategy_runtime_params("ai_adaptive")
    assert {"atr_period", "factor", "ma_regime", "sl_mult", "tp_mult"} <= set(p)


def test_ai_adaptive_fires_uptrend_and_injects_params():
    df = _series(0.0012, 0.008, seed=1)
    sig = strat_mod.ai_adaptive_signal("UP", {"1d": df})
    assert sig and sig["direction"] == "long" and sig["strategy"] == "ai_adaptive"
    assert sig["stop_loss"] < sig["price"] < sig["take_profit"]
    # Kandidat-Parameter direkt einspeisen: weiteres TP → größerer Abstand.
    sig2 = strat_mod.ai_adaptive_signal("UP", {"1d": df}, p={"tp_mult": 15.0})
    assert round((sig2["take_profit"] - sig2["price"]) / sig2["atr"], 1) == 15.0


def test_ai_adaptive_silent_downtrend():
    df = _series(-0.0015, 0.008, seed=2)
    assert strat_mod.ai_adaptive_signal("DOWN", {"1d": df}) is None


# ── Zielfunktion ──────────────────────────────────────────────────────────────

def test_cagr_and_mar():
    # +100 % in 1 Jahr → CAGR 100 %; bei 20 % Drawdown → MAR 5.0
    assert round(lab._cagr(100.0, 1.0), 1) == 100.0
    assert lab.mar({"return_pct": 100.0, "max_drawdown_pct": 20.0}, 1.0) == 5.0
    # Drawdown ~0 wird abgefangen (kein inf)
    assert lab.mar({"return_pct": 10.0, "max_drawdown_pct": 0.0}, 1.0) == round(10.0 / 0.5, 3)


# ── Kandidaten: genau eine Variable ───────────────────────────────────────────

def test_candidates_change_exactly_one_variable(lab_tmp):
    champ = {"tp_mult": 10.0, "factor": 3.0, "atr_period": 10, "ma_regime": 200, "sl_mult": 3.0}
    cands = lab.candidates(champ)
    assert cands, "es müssen Kandidaten erzeugt werden"
    for c in cands:
        diff = [k for k in champ if c["params"][k] != champ[k]]
        assert diff == [c["param"]], f"Kandidat ändert mehr als eine Variable: {diff}"
        grid = lab.SEARCH_SPACE[c["param"]]
        assert c["new"] in grid and c["new"] != c["old"]
    # tp_mult=10 hat die Nachbarn 8 und 12
    tp = sorted(c["new"] for c in cands if c["param"] == "tp_mult")
    assert tp == [8.0, 12.0]


def test_call_llm_proposer_parses_openai_json_response(lab_tmp, monkeypatch):
    import sys
    import types

    calls = {"model": None, "messages": None}

    class _Models:
        def list(self):
            return types.SimpleNamespace(data=[types.SimpleNamespace(id=lab.LAB_LLM_MODEL)])

    class _Completions:
        def create(self, model, messages):
            calls["model"] = model
            calls["messages"] = messages
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content='[{"param":"tp_mult","direction":"up","reason":"test"}]')
            )])

    class _OpenAI:
        def __init__(self, api_key):
            assert api_key == "test-key"
            self.models = _Models()
            self.chat = types.SimpleNamespace(completions=_Completions())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_OpenAI))

    proposals = lab._call_llm_proposer({"tp_mult": 10.0}, {"version": "01"})

    assert proposals == [{"param": "tp_mult", "direction": "up", "reason": "test"}]
    assert calls["model"] == lab.LAB_LLM_MODEL
    assert calls["messages"] is not None
    assert calls["messages"][0]["role"] == "system"


def test_llm_candidates_validates_single_grid_neighbor(lab_tmp, monkeypatch):
    champ = {"tp_mult": 10.0, "factor": 3.0, "atr_period": 10, "ma_regime": 200, "sl_mult": 3.0}
    monkeypatch.setattr(lab, "_call_llm_proposer", lambda champion, context: [
        {"param": "tp_mult", "direction": "up", "reason": "Gewinner laufen lassen"},
        {"param": "factor", "new": 9.9, "reason": "ungültig: nicht im Raster"},
        {"param": "unknown", "direction": "down", "reason": "ungültiger Parameter"},
    ])

    cands = lab._llm_candidates(champ, {"recent": []})

    assert len(cands) == 1
    assert cands[0]["param"] == "tp_mult"
    assert cands[0]["old"] == 10.0 and cands[0]["new"] == 12.0
    assert cands[0]["params"]["tp_mult"] == 12.0
    assert cands[0]["reason"] == "Gewinner laufen lassen"


def test_select_candidates_uses_grid_without_flag(lab_tmp, monkeypatch):
    champ = {"tp_mult": 10.0, "factor": 3.0}
    monkeypatch.delenv("LAB_PROPOSER", raising=False)
    monkeypatch.setattr(lab, "_llm_candidates", lambda champion, context: (_ for _ in ()).throw(AssertionError("LLM darf ohne Flag nicht laufen")))

    cands = lab.select_candidates(champ, {"recent": []})

    assert cands and {c["new"] for c in cands if c["param"] == "tp_mult"} == {8.0, 12.0}


def test_select_candidates_uses_llm_when_flagged(lab_tmp, monkeypatch):
    champ = {"tp_mult": 10.0, "factor": 3.0}
    monkeypatch.setenv("LAB_PROPOSER", "llm")
    monkeypatch.setattr(lab, "_llm_candidates", lambda champion, context: [{
        "param": "factor", "old": 3.0, "new": 3.5, "params": {**champion, "factor": 3.5}
    }])

    assert lab.select_candidates(champ, {"recent": []})[0]["new"] == 3.5


def test_select_candidates_falls_back_to_grid_when_llm_errors(lab_tmp, monkeypatch):
    champ = {"tp_mult": 10.0, "factor": 3.0}
    monkeypatch.setenv("LAB_PROPOSER", "llm")
    monkeypatch.setattr(lab, "_llm_candidates", lambda champion, context: (_ for _ in ()).throw(RuntimeError("api down")))

    cands = lab.select_candidates(champ, {"recent": []})

    assert cands and {c["new"] for c in cands if c["param"] == "tp_mult"} == {8.0, 12.0}


def test_llm_candidates_allows_multi_step_jump(lab_tmp, monkeypatch):
    # (a) Größere Raster-Sprünge: von tp_mult=10 direkt auf 6.0 (Distanz 2) — früher verboten.
    champ = {"tp_mult": 10.0, "factor": 3.0}
    monkeypatch.setattr(lab, "_call_llm_proposer", lambda champion, context: [
        {"param": "tp_mult", "new": 6.0, "reason": "2 Schritte runter"},
        {"param": "factor", "direction": "down", "steps": 2, "reason": "weit runter"},
    ])
    by = {c["param"]: c for c in lab._llm_candidates(champ, {})}
    assert by["tp_mult"]["new"] == 6.0 and by["tp_mult"]["params"]["tp_mult"] == 6.0
    assert by["factor"]["new"] == 2.5                     # 3.0 → 2 Schritte runter (geklemmt) → 2.5


def test_call_llm_proposer_warns_and_falls_back_on_invalid_model(lab_tmp, monkeypatch):
    # (b) Ungültiges Modell → klare Telegram-Warnung + Raster-Fallback, KEIN create()-Aufruf.
    import sys
    import types
    warned = {}
    monkeypatch.setattr(lab, "_notify_admin", lambda text: warned.__setitem__("text", text) or True)

    class _Models:
        def list(self):
            return types.SimpleNamespace(data=[types.SimpleNamespace(id="some-other-model")])

    class _Completions:
        def create(self, model, messages):
            raise AssertionError("create darf bei ungültigem Modell nicht aufgerufen werden")

    class _OpenAI:
        def __init__(self, api_key):
            self.models = _Models()
            self.chat = types.SimpleNamespace(completions=_Completions())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_OpenAI))

    assert lab._call_llm_proposer({"tp_mult": 10.0}, {}) == []
    assert lab.LAB_LLM_MODEL in warned.get("text", "") and "nicht verfügbar" in warned.get("text", "")


def test_call_llm_proposer_warns_on_missing_key(lab_tmp, monkeypatch):
    # (b) Fehlender Key bei LAB_PROPOSER=llm → Telegram-Warnung + Fallback.
    warned = {}
    monkeypatch.setattr(lab, "_notify_admin", lambda text: warned.__setitem__("text", text) or True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert lab._call_llm_proposer({"tp_mult": 10.0}, {}) == []
    assert "OPENAI_API_KEY" in warned.get("text", "")


# ── Gate: nur Out-of-Sample-Gewinner ──────────────────────────────────────────

def _cand(oos_mar, oos_trades=50, oos_dd=15.0, fold_mars=None):
    c = {"param": "tp_mult", "old": 10.0, "new": 12.0, "oos_mar": oos_mar,
         "oos": {"trades": oos_trades, "max_drawdown_pct": oos_dd, "return_pct": 30.0}}
    if fold_mars is not None:
        c["fold_mars"] = fold_mars
    return c


def _champ_eval(oos_mar=0.9, oos_dd=15.0, fold_mars=None, oos_trades_list=None):
    return {"oos": {"max_drawdown_pct": oos_dd}, "oos_mar": oos_mar,
            "fold_mars": fold_mars or [], "oos_trades_list": oos_trades_list or []}


def test_gate_promotes_true_oos_winner():
    ok, _ = lab._gate(_cand(oos_mar=1.2), _champ_eval(oos_mar=0.9), oos_years=1.0)
    assert ok is True


def test_gate_rejects_when_not_beating_oos():
    ok, reason = lab._gate(_cand(oos_mar=0.8), _champ_eval(oos_mar=0.9), oos_years=1.0)
    assert ok is False and "out-of-sample" in reason


def test_gate_rejects_too_few_oos_trades():
    ok, reason = lab._gate(_cand(oos_mar=2.0, oos_trades=5), _champ_eval(), oos_years=1.0)
    assert ok is False and "Out-of-Sample-Trades" in reason


def test_gate_rejects_worse_drawdown():
    ok, reason = lab._gate(_cand(oos_mar=2.0, oos_dd=30.0), _champ_eval(), oos_years=1.0)
    assert ok is False and "Drawdown" in reason


def test_gate_rejects_when_no_candidate():
    ok, reason = lab._gate(None, _champ_eval(), oos_years=1.0)
    assert ok is False and "In-Sample" in reason


def test_gate_requires_fold_majority():
    """Aggregat-Sieg reicht nicht: Kandidat gewinnt nur 1 von 3 Folds → abgelehnt."""
    champ = _champ_eval(oos_mar=0.9, fold_mars=[1.0, 1.0, 1.0])
    ok, reason = lab._gate(_cand(oos_mar=1.5, fold_mars=[2.0, 0.5, 0.5]), champ, oos_years=1.0)
    assert ok is False and "Folds" in reason
    # Mehrheit gewonnen (2/3) → durchgelassen.
    ok, _ = lab._gate(_cand(oos_mar=1.5, fold_mars=[2.0, 2.0, 0.5]), champ, oos_years=1.0)
    assert ok is True


def _trades_seq(pnls):
    return [{"pnl_eur": p, "pnl_pct": p / 10.0} for p in pnls]


def test_gate_bootstrap_blocks_fragile_winner():
    """Kandidat gleichwertig zum Champion (identische Trades) → Bootstrap-Anteil ~50 % < 70 %
    → abgelehnt, obwohl der Punktvergleich knapp für den Kandidaten ausgeht."""
    same = _trades_seq([50, -30, 40, -20, 60, -10, 30, -40, 20, 10, 25, -15])
    champ = _champ_eval(oos_mar=0.9, oos_trades_list=same)
    cand = _cand(oos_mar=0.91)
    cand["oos_trades_list"] = list(same)
    ok, reason = lab._gate(cand, champ, oos_years=1.0)
    assert ok is False and "Bootstrap" in reason


def test_gate_bootstrap_passes_clear_winner():
    """Klar besserer Kandidat (durchweg höhere P&L-Sequenz) → hohe Bootstrap-Konfidenz → ok."""
    champ_trades = _trades_seq([10, -30, 5, -25, 8, -20, 4, -35, 6, -15, 3, -10])
    cand_trades = _trades_seq([80, 60, 90, -5, 70, 50, 85, -10, 60, 75, 55, 65])
    champ = _champ_eval(oos_mar=0.5, oos_trades_list=champ_trades)
    cand = _cand(oos_mar=3.0)
    cand["oos_trades_list"] = cand_trades
    ok, _ = lab._gate(cand, champ, oos_years=1.0)
    assert ok is True and cand["boot_prob"] >= lab.BOOT_MIN_PROB


# ── Embargo & Folds ───────────────────────────────────────────────────────────

def test_evaluate_embargo_purges_boundary_fires(lab_tmp, monkeypatch):
    """Fires kurz vor dem Split gehören mit Embargo NICHT mehr zum In-Sample."""
    monkeypatch.setattr(lab, "EMBARGO_DAYS", 30)
    df = _series(0.0010, 0.008, seed=3)
    data = {"X": df}
    first, split, last, is_years, oos_years = lab._split(data)
    captured = {}
    real_pm = lab._portfolio_metrics

    def spy(data_, by_date, params=None):
        captured.setdefault("windows", []).append(sorted(by_date))
        return real_pm(data_, by_date, params)

    monkeypatch.setattr(lab, "_portfolio_metrics", spy)
    monkeypatch.setattr(lab, "_fires_by_date", lambda d, p, j: {
        str((split - pd.Timedelta(days=5)).date()): [{"ticker": "X", "idx": 300, "strength": 1,
                                                      "entry": 100.0, "sl": 95.0, "tp": 120.0,
                                                      "date": str((split - pd.Timedelta(days=5)).date())}],
        str((split - pd.Timedelta(days=60)).date()): [{"ticker": "X", "idx": 200, "strength": 1,
                                                       "entry": 100.0, "sl": 95.0, "tp": 120.0,
                                                       "date": str((split - pd.Timedelta(days=60)).date())}],
    })
    ev = lab.evaluate(data, {}, split, is_years, oos_years, jobs=1)
    is_window = captured["windows"][0]
    # Der Fire 5 Tage vor dem Split fällt ins Embargo (30 Tage) → nicht im IS-Fenster.
    assert str((split - pd.Timedelta(days=5)).date()) not in is_window
    assert str((split - pd.Timedelta(days=60)).date()) in is_window
    assert ev["oos"]["trades"] == 0                       # keiner der Fires liegt im OOS


def test_fold_bounds_partition_oos():
    split, last = pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31")
    folds = lab._fold_bounds(split, last, 3)
    assert len(folds) == 3
    assert folds[0][0] == split                            # beginnt am Split
    assert folds[-1][1] > last                             # letztes Fold schließt `last` ein
    for (a, b), (a2, _) in zip(folds, folds[1:]):
        assert b == a2                                     # lückenlos aneinander


# ── Voller (kleiner) Zyklus ───────────────────────────────────────────────────

def test_run_cycle_writes_state_pending_and_log(lab_tmp):
    data = {f"T{i}": _series(0.0008 + 0.0003 * (i % 3), 0.010, seed=i) for i in range(4)}
    summary = lab.run_cycle(data=data, jobs=1, log=lambda m: None)

    # Zustand
    st = lab.load_state()
    assert st["status"] == "idle" and st["target_key"] == "ai_adaptive"
    assert st["champion"] and "oos_mar" in st["champion"]
    assert st["window"]["split"] and st["last_cycle"]["decision"] in ("vorgeschlagen", "kein Vorschlag")
    # Pending-Datei existiert (Vorschlag oder None)
    assert lab.pending_path().exists()
    # Hypothesen-Log: Kandidaten + eine Entscheidung
    hyps = lab.load_hypotheses()
    assert any(h.get("kind") == "candidate" for h in hyps)
    assert any(h.get("kind") == "decision" for h in hyps)
    # jeder Kandidat trägt eine falsifizierbare Vorhersage + tatsächliches Ergebnis
    for h in hyps:
        if h.get("kind") == "candidate":
            assert h["predicted"] == "OOS-MAR steigt"
            assert h["actual"] in ("bestätigt", "widerlegt")
    assert summary["decision"] in ("vorgeschlagen", "kein Vorschlag")


# ── Freigabe / Verwerfen (Menschen-Gate) ──────────────────────────────────────

def _seed_pending(tmp: Path, params: dict):
    lab._write_json(lab.pending_path(), {"proposal": {
        "version": "02", "created_at": "t", "param": "tp_mult", "old": 10.0, "new": 12.0,
        "params": params, "champion_params": {}, "reason": "test",
        "champion_oos_mar": 0.9, "candidate_oos_mar": 1.3,
        "champion_oos": {"max_drawdown_pct": 15.0}, "candidate_oos": {"max_drawdown_pct": 14.0},
        "candidate_is": {"trades": 40}, "is_mar": 1.4,
    }, "updated_at": "t"})


def test_apply_pending_writes_live_config(lab_tmp, monkeypatch):
    from stockbot.core import db
    db.DB_FILE = Path(tempfile.mkdtemp(prefix="labdb_")) / "d.db"
    db.init_db()
    # Ausgangszustand + anstehender Vorschlag
    lab._write_json(lab.state_path(), lab.load_state())
    new_params = {"atr_period": 10, "factor": 3.0, "ma_regime": 200, "st_lb": 250,
                  "sl_mult": 3.0, "tp_mult": 12.0}
    _seed_pending(lab_tmp, new_params)

    res = lab.apply_pending()
    assert res["ok"] is True and res["version"] == "02"
    # Live-Override wurde geschrieben und wird von strategy_runtime_params übernommen
    cfg = db.get_strategy_config("ai_adaptive")
    assert cfg and cfg["params"]["tp_mult"] == 12.0
    strat_mod.refresh_strategy_overrides_cache()
    assert strat_mod.strategy_runtime_params("ai_adaptive")["tp_mult"] == 12.0
    # Version gebumpt, Pending geleert, Vorgänger archiviert
    assert lab.load_state()["version"] == "02"
    assert lab.load_pending() is None
    assert (lab.HISTORY_DIR / "v01.json").exists()
    # Aufräumen: Override wieder entfernen, damit andere Tests die Defaults sehen
    with db._connect() as c:
        c.execute("DELETE FROM strategy_configs WHERE key='ai_adaptive'")
    strat_mod.refresh_strategy_overrides_cache()


def test_reject_pending_keeps_live_untouched(lab_tmp):
    from stockbot.core import db
    db.DB_FILE = Path(tempfile.mkdtemp(prefix="labdb2_")) / "d.db"
    db.init_db()
    lab._write_json(lab.state_path(), lab.load_state())
    _seed_pending(lab_tmp, {"tp_mult": 12.0})

    res = lab.reject_pending()
    assert res["ok"] is True
    assert lab.load_pending() is None                       # verworfen
    assert db.get_strategy_config("ai_adaptive") is None     # kein Live-Eingriff
    hyps = lab.load_hypotheses()
    assert any(h.get("kind") == "rejected" for h in hyps)


def test_apply_pending_none_when_empty(lab_tmp):
    lab._write_json(lab.pending_path(), {"proposal": None})
    assert lab.apply_pending()["ok"] is False


# ── Bestätigungs-Serie (Streak) ───────────────────────────────────────────────

def _uptrend_data(n_tickers=4):
    return {f"T{i}": _series(0.0008 + 0.0003 * (i % 3), 0.010, seed=i) for i in range(n_tickers)}


def test_streak_requires_consecutive_gate_wins(lab_tmp, monkeypatch):
    """Mit STREAK_REQUIRED=2 landet ein Gate-Sieger erst im ZWEITEN Zyklus in pending.json."""
    monkeypatch.setattr(lab, "STREAK_REQUIRED", 2)
    data = _uptrend_data()
    first = lab.run_cycle(data=data, jobs=1, log=lambda m: None)
    if first["decision"].startswith("Serie"):              # Zyklus 1: Gate bestanden → Serie 1/2
        assert lab.load_pending() is None, "Serie 1/2 darf noch keinen Vorschlag schreiben"
        st = lab.load_state()
        assert st["streak"]["count"] == 1 and st["streak"]["required"] == 2
        second = lab.run_cycle(data=data, jobs=1, log=lambda m: None)   # gleiche Daten → gleicher Sieger
        assert second["decision"] == "vorgeschlagen"
        assert lab.load_pending() is not None
    else:
        # Auf diesen synthetischen Daten bestand kein Kandidat das Gate — dann darf es
        # auch keine Serie und keinen Vorschlag geben (Negativ-Pfad bleibt konsistent).
        assert first["decision"] == "kein Vorschlag" and lab.load_pending() is None
        assert lab.load_state().get("streak") in ({}, None)


def test_run_cycle_never_overwrites_waiting_pending(lab_tmp):
    """Ein wartender Vorschlag wird von Folgezyklen nicht überschrieben oder geleert."""
    marker = {"proposal": {"version": "99", "param": "tp_mult", "old": 10.0, "new": 12.0,
                           "params": {"tp_mult": 12.0}}, "updated_at": "marker"}
    lab._write_json(lab.pending_path(), marker)
    lab.run_cycle(data=_uptrend_data(), jobs=1, log=lambda m: None)
    after = lab._read_json(lab.pending_path(), None)
    assert after["updated_at"] == "marker" and after["proposal"]["version"] == "99"


# ── Lernen aus der Historie ───────────────────────────────────────────────────

def _seed_hypotheses(records):
    lab._append_hypotheses(records)


def test_history_stats_and_candidates_learn_confirmed_direction(lab_tmp):
    champ = {"tp_mult": 8.0, "factor": 3.0, "atr_period": 10, "ma_regime": 200, "sl_mult": 3.0}
    # Historie: tp_mult↑ zweimal bestätigt, factor↓ zweimal widerlegt.
    _seed_hypotheses([
        {"kind": "candidate", "param": "tp_mult", "old": 8.0, "new": 10.0, "actual": "bestätigt"},
        {"kind": "candidate", "param": "tp_mult", "old": 8.0, "new": 10.0, "actual": "bestätigt"},
        {"kind": "candidate", "param": "factor", "old": 3.0, "new": 2.5, "actual": "widerlegt"},
        {"kind": "candidate", "param": "factor", "old": 3.0, "new": 2.5, "actual": "widerlegt"},
        {"kind": "candidate", "param": "factor", "old": 3.0, "new": 3.5, "actual": "bestätigt"},
    ])
    stats = lab.history_stats()
    assert stats[("tp_mult", "up")]["bestätigt"] == 2
    assert stats[("factor", "down")]["widerlegt"] == 2

    cands = lab._history_candidates(champ, stats)
    assert cands, "bestätigte Richtungen müssen History-Kandidaten erzeugen"
    # Multi-Step: tp_mult 2 Schritte in die bestätigte Richtung (8 → 12 im Test-Raster).
    multi = next(c for c in cands if c["proposer"] == "history" and c["param"] == "tp_mult")
    assert multi["new"] == 12.0
    # Paar-Kandidat kombiniert die zwei besten bestätigten Richtungen (tp_mult↑ + factor↑).
    pair = next((c for c in cands if c["proposer"] == "history-pair"), None)
    assert pair is not None and pair["params"]["tp_mult"] == 10.0 and pair["params"]["factor"] == 3.5
    # Die widerlegte Richtung (factor↓) wird NICHT vorgeschlagen.
    assert all(not (c["param"] == "factor" and c["params"]["factor"] < 3.0) for c in cands)


def test_select_candidates_merges_all_sources_and_dedupes(lab_tmp, monkeypatch):
    champ = {"tp_mult": 10.0, "factor": 3.0}
    monkeypatch.setenv("LAB_PROPOSER", "llm")
    # LLM schlägt denselben Move vor wie ein Grid-Nachbar → Dedupe behält die LLM-Nennung.
    monkeypatch.setattr(lab, "_llm_candidates", lambda c, ctx: [
        {"param": "tp_mult", "old": 10.0, "new": 12.0,
         "params": {**champ, "tp_mult": 12.0}, "proposer": "llm"}])
    cands = lab.select_candidates(champ, {})
    keys = [json.dumps(c["params"], sort_keys=True) for c in cands]
    assert len(keys) == len(set(keys)), "Kandidaten müssen dedupliziert sein"
    tp12 = [c for c in cands if c.get("param") == "tp_mult" and c.get("new") == 12.0]
    assert len(tp12) == 1 and tp12[0]["proposer"] == "llm"
    # Grid-Basis bleibt vollständig enthalten (z. B. tp_mult 8.0 als Nachbar).
    assert any(c["param"] == "tp_mult" and c["new"] == 8.0 for c in cands)


def test_llm_context_contains_history(lab_tmp, monkeypatch):
    _seed_hypotheses([{"kind": "candidate", "param": "tp_mult", "old": 8.0, "new": 10.0,
                       "actual": "bestätigt", "proposer": "grid"}])
    seen = {}
    monkeypatch.setenv("LAB_PROPOSER", "llm")
    monkeypatch.setattr(lab, "_llm_candidates",
                        lambda c, ctx: seen.update(ctx=ctx) or [])
    lab.select_candidates({"tp_mult": 10.0, "factor": 3.0}, {})
    assert seen["ctx"]["history"], "LLM muss die Hypothesen-Historie als Kontext bekommen"
    assert seen["ctx"]["history"][0]["param"] == "tp_mult"
    assert "tp_mult:up" in seen["ctx"]["history_stats"]


# ── Regression-Guard (Rollback-Kandidat) ─────────────────────────────────────

def test_latest_archived_params_found_and_excluded(lab_tmp):
    lab.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    lab._write_json(lab.HISTORY_DIR / "v01.json", {"version": "01", "params": {"tp_mult": 8.0}})
    lab._write_json(lab.HISTORY_DIR / "v02.json", {"version": "02", "params": {"tp_mult": 10.0}})
    # Jüngste archivierte Version mit ANDEREN Parametern als der aktuelle Champion.
    ver, params = lab._latest_archived_params({"tp_mult": 12.0})
    assert ver == "02" and params == {"tp_mult": 10.0}
    # Identische Parameter werden übersprungen (kein Rollback auf sich selbst).
    ver, params = lab._latest_archived_params({"tp_mult": 10.0})
    assert ver == "01" and params == {"tp_mult": 8.0}


# ── Reality-Check (Live vs. Backtest-Erwartung) ───────────────────────────────

def test_reality_check_compares_live_with_expectation(lab_tmp):
    from stockbot.core import db
    db.DB_FILE = Path(tempfile.mkdtemp(prefix="labdb3_")) / "d.db"
    db.init_db()
    with db._connect() as c:
        c.execute("INSERT INTO users (user_id, username) VALUES (7, 'u')")
        for i, pnl in enumerate([2.0, 3.0, -1.0, 4.0, 1.5, -0.5]):
            c.execute(
                "INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, "
                "status, pnl_pct) VALUES (?, date('now'), ?, 'long', ?, 'closed', ?)",
                (7, f"T{i}", json.dumps({"strategy": "ai_adaptive"}), pnl))
        # Fremd-Strategie-Trade darf NICHT einfließen:
        c.execute(
            "INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, "
            "status, pnl_pct) VALUES (?, date('now'), 'ZZZ', 'long', ?, 'closed', -99.0)",
            (7, json.dumps({"strategy": "standard"})))

    r = lab.reality_check({"win_rate": 66.0, "avg_trade_pct": 1.5}, days=7, min_trades=5)
    assert r["n"] == 6
    assert r["live_win_rate"] == round(100 * 4 / 6, 1)
    assert r["divergent"] is False                        # 66.7 % vs. 66 % erwartet → im Rahmen
    r2 = lab.reality_check({"win_rate": 20.0, "avg_trade_pct": 1.5}, days=7, min_trades=5)
    assert r2["divergent"] is True                        # >15 pp Abweichung → Alarm


def test_reality_check_needs_enough_trades(lab_tmp):
    from stockbot.core import db
    db.DB_FILE = Path(tempfile.mkdtemp(prefix="labdb4_")) / "d.db"
    db.init_db()
    r = lab.reality_check({"win_rate": 50.0}, days=7, min_trades=5)
    assert r["n"] == 0 and "zu wenige" in r.get("note", "")


# ── Rasterrand-Warnung & Fires-Cache ─────────────────────────────────────────

def test_edge_warnings_flag_grid_boundaries(lab_tmp):
    assert lab._edge_warnings({"tp_mult": 12.0, "factor": 3.0})       # 12 = oberer Rand
    assert not lab._edge_warnings({"tp_mult": 10.0, "factor": 3.0})   # alles im Inneren


def test_fires_cache_hits_second_call(lab_tmp, monkeypatch):
    data = {"X": _series(0.0010, 0.008, seed=9)}
    calls = {"n": 0}
    real = lab._ai_fires_ticker

    def counting(args):
        calls["n"] += 1
        return real(args)

    monkeypatch.setattr(lab, "_ai_fires_ticker", counting)
    p = {"tp_mult": 10.0}
    first = lab._fires_by_date(data, p, jobs=1)
    assert calls["n"] == 1
    second = lab._fires_by_date(data, p, jobs=1)          # gleicher Satz + Datenstand → Cache
    assert calls["n"] == 1 and second == first
    lab._fires_by_date(data, {"tp_mult": 12.0}, jobs=1)   # andere Parameter → neu rechnen
    assert calls["n"] == 2
