"""
Offline-Tests für das LLM-Ranking (kein Netzwerk, kein echter Anthropic-Call).

Lauf:  python test_llm_ranker.py   oder   pytest test_llm_ranker.py
"""

import sys
import json

import llm_ranker
import config


def _sig(ticker, strength):
    return {"ticker": ticker, "strength": strength, "price": 100.0, "direction": "long"}


# ── Fake-Anthropic-Client ────────────────────────────────────────────────────

class _Block:
    type = "text"
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, parent):
        self.parent = parent
    def create(self, **kwargs):
        self.parent.calls += 1
        self.parent.last_kwargs = kwargs
        return _Resp(self.parent.payload)


class _FakeClient:
    """Liefert bei messages.create() einen vorgegebenen JSON-Text zurück."""
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
    @property
    def messages(self):
        return _FakeMessages(self)


# ── Tests ────────────────────────────────────────────────────────────────────

def test_disabled_returns_unchanged():
    orig = config.LLM_RANK_ENABLED
    config.LLM_RANK_ENABLED = False
    try:
        sigs = [_sig("A", 60), _sig("B", 90)]
        assert llm_ranker.rank_signals(sigs) is sigs        # kein Client → unverändert
    finally:
        config.LLM_RANK_ENABLED = orig


def test_single_signal_is_assessed():
    fake = _FakeClient(json.dumps({"ranking": [{"ticker": "A", "score": 73, "reason": "solide"}]}))
    sigs = [_sig("A", 60)]
    out = llm_ranker.rank_signals(sigs, client=fake, fetch=lambda t: {})
    assert fake.calls == 1                                   # auch EIN Signal wird bewertet
    assert out[0]["llm_score"] == 73 and out[0]["llm_reason"] == "solide"


def test_reorders_and_attaches_scores():
    # technische Reihenfolge: A (60) vor B (90); LLM bewertet B höher → B muss nach vorne
    payload = json.dumps({"ranking": [
        {"ticker": "A", "score": 40, "reason": "schwache Fundamentaldaten"},
        {"ticker": "B", "score": 88, "reason": "starkes Wachstum, kein Earnings-Risiko"},
    ]})
    fake = _FakeClient(payload)
    sigs = [_sig("A", 60), _sig("B", 90)]
    out = llm_ranker.rank_signals(sigs, client=fake, fetch=lambda t: {})
    assert fake.calls == 1
    assert [s["ticker"] for s in out] == ["B", "A"]
    assert out[0]["llm_score"] == 88 and "Wachstum" in out[0]["llm_reason"]
    # der LLM-Call nutzt das konfigurierte Haiku-Modell
    assert fake.last_kwargs["model"] == config.LLM_MODEL


def test_bad_response_falls_back_to_input():
    fake = _FakeClient("kein JSON")
    sigs = [_sig("A", 60), _sig("B", 90)]
    out = llm_ranker.rank_signals(sigs, client=fake, fetch=lambda t: {})
    assert out is sigs                                      # Fehler → Original-Reihenfolge


def test_health_check_no_key():
    orig = config.LLM_RANK_ENABLED
    config.LLM_RANK_ENABLED = False
    try:
        res = llm_ranker.health_check()          # ohne Client + ohne Key
        assert res["ok"] is False and "ANTHROPIC_API_KEY" in res["detail"]
    finally:
        config.LLM_RANK_ENABLED = orig


def test_health_check_ok_with_fake_client():
    fake = _FakeClient(json.dumps({"ranking": [
        {"ticker": "AAA", "score": 55, "reason": "ok"},
        {"ticker": "BBB", "score": 80, "reason": "stark"},
    ]}))
    res = llm_ranker.health_check(client=fake)    # Client injiziert → echter Call-Pfad
    assert res["ok"] is True and "Modell" in res["detail"]
    assert res["ranking"][0]["ticker"] in ("AAA", "BBB")
    assert fake.calls == 1


def test_health_check_reports_error():
    class _BoomClient:
        @property
        def messages(self):
            class _M:
                def create(self, **kw):
                    raise RuntimeError("401 invalid api key")
            return _M()
    res = llm_ranker.health_check(client=_BoomClient())
    assert res["ok"] is False and "invalid api key" in res["detail"]


def test_gather_context_robust_on_error():
    class _BoomYF:
        @staticmethod
        def Ticker(t):
            raise RuntimeError("kein Netz")
    orig = llm_ranker.yf
    llm_ranker.yf = _BoomYF
    try:
        ctx = llm_ranker.gather_context("ZZZ_UNIQUE_TEST")
        assert isinstance(ctx, dict)                        # kein Exception, leeres/teilweises dict
    finally:
        llm_ranker.yf = orig


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"OK  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERR  {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
