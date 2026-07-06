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
    """Isoliertes Lab-Verzeichnis + kleine, schnelle Fenster/Suchraum."""
    tmp = Path(tempfile.mkdtemp(prefix="labtest_"))
    monkeypatch.setattr(lab, "LAB_DIR", tmp)
    monkeypatch.setattr(lab, "HISTORY_DIR", tmp / "history")
    monkeypatch.setattr(lab, "YEARS", 1)
    monkeypatch.setattr(lab, "OOS_FRACTION", 0.4)
    monkeypatch.setattr(lab, "MIN_TRADES", 1)
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

def _cand(oos_mar, oos_trades=50, oos_dd=15.0):
    return {"param": "tp_mult", "old": 10.0, "new": 12.0, "oos_mar": oos_mar,
            "oos": {"trades": oos_trades, "max_drawdown_pct": oos_dd, "return_pct": 30.0}}


def test_gate_promotes_true_oos_winner():
    champ_oos = {"max_drawdown_pct": 15.0}
    ok, _ = lab._gate(_cand(oos_mar=1.2), champ_oos, champ_oos_mar=0.9)
    assert ok is True


def test_gate_rejects_when_not_beating_oos():
    champ_oos = {"max_drawdown_pct": 15.0}
    ok, reason = lab._gate(_cand(oos_mar=0.8), champ_oos, champ_oos_mar=0.9)
    assert ok is False and "out-of-sample" in reason


def test_gate_rejects_too_few_oos_trades():
    champ_oos = {"max_drawdown_pct": 15.0}
    ok, reason = lab._gate(_cand(oos_mar=2.0, oos_trades=5), champ_oos, champ_oos_mar=0.9)
    assert ok is False and "Out-of-Sample-Trades" in reason


def test_gate_rejects_worse_drawdown():
    champ_oos = {"max_drawdown_pct": 15.0}
    ok, reason = lab._gate(_cand(oos_mar=2.0, oos_dd=30.0), champ_oos, champ_oos_mar=0.9)
    assert ok is False and "Drawdown" in reason


def test_gate_rejects_when_no_candidate():
    ok, reason = lab._gate(None, {"max_drawdown_pct": 15.0}, 0.9)
    assert ok is False and "In-Sample" in reason


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
