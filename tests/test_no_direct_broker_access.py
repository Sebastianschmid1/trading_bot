"""
Strukturtest für Phase 0 / Gate P0 (TSAFE-006): Telegram und Web dürfen den Broker
NIE direkt ansprechen, sondern ausschließlich über die gate-geprüften Funktionen in
`stockbot/broker/client.py` (Live-Kill-Switch, Hebel-Deckel, Optionsverbot).

Diese Tests lesen den Quellcode statisch und stellen sicher, dass kein anderes Modul
das Alpaca-SDK importiert oder `submit_order`/`TradingClient` direkt aufruft. Der
vollständige "kein Direktpfad"-Nachweis (Idempotency, Zustandsmaschine) kommt mit dem
OMS in Phase 4 — hier geht es nur um den strukturellen Umgehungsschutz von heute.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BROKER_CLIENT = REPO_ROOT / "stockbot" / "broker" / "client.py"
CALLER_MODULES = [
    REPO_ROOT / "stockbot" / "tgbot" / "bot.py",
    REPO_ROOT / "stockbot" / "web" / "webapp.py",
]

_SDK_PATTERNS = [
    re.compile(r"\.submit_order\("),
    re.compile(r"TradingClient\("),
    re.compile(r"from alpaca\.trading"),
    re.compile(r"import alpaca\.trading"),
]


def test_broker_client_is_the_only_alpaca_sdk_caller():
    for path in CALLER_MODULES:
        src = path.read_text(encoding="utf-8")
        for pattern in _SDK_PATTERNS:
            assert not pattern.search(src), (
                f"{path.relative_to(REPO_ROOT)} ruft das Alpaca-SDK direkt auf "
                f"({pattern.pattern}) — muss über stockbot/broker/client.py laufen."
            )


def test_callers_only_use_gated_broker_functions():
    """Telegram/Web dürfen Orders nur über die live-/hebel-/optionsgeprüften
    `broker.*`-Funktionen auslösen, nie über einen internen/anderen Pfad."""
    allowed_order_calls = {
        "submit_buy", "submit_option_buy", "submit_exit_order",
        "submit_stop_sell", "close_position", "cancel_order",
    }
    call_pattern = re.compile(r"broker\.(\w+)\(")
    for path in CALLER_MODULES:
        src = path.read_text(encoding="utf-8")
        calls = {m.group(1) for m in call_pattern.finditer(src)}
        order_like = {c for c in calls if "submit" in c or "close_position" in c or "cancel_order" in c}
        assert order_like <= allowed_order_calls, (
            f"{path.relative_to(REPO_ROOT)} ruft unbekannte/ungeschützte Broker-Funktion(en) auf: "
            f"{order_like - allowed_order_calls}"
        )


def test_entry_order_functions_check_live_block_reason():
    """Die beiden Einstiegs-Order-Funktionen (nicht Exits) müssen den Live-Kill-Switch prüfen."""
    src = BROKER_CLIENT.read_text(encoding="utf-8")
    for fn_name in ("submit_buy", "submit_option_buy"):
        m = re.search(rf"def {fn_name}\(.*?\n(?=def |\Z)", src, re.DOTALL)
        assert m, f"{fn_name} nicht gefunden in {BROKER_CLIENT}"
        assert "_live_block_reason(" in m.group(0), (
            f"{fn_name} prüft nicht `_live_block_reason` — Live-Kill-Switch könnte umgangen werden."
        )
