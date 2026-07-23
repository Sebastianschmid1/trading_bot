"""Tests für die Anti-Spam-Entscheidung bei Auto-Accept-Signalen.

Auto-Accept-Nutzer sollen außerhalb der regulären US-Sitzung keine Signal-Karte in den
Telegram-Chat bekommen (der Kauf wird erst zur Öffnung frisch geprüft). Getestet wird die
reine Prädikat-Funktion `_suppress_auto_accept_out_of_session` — seiteneffektfrei, damit
kein Bot/Job-Queue-Setup nötig ist.
"""
import pytest

from stockbot.tgbot.bot import _suppress_auto_accept_out_of_session as suppress


@pytest.mark.parametrize(
    "auto_accept, regular_session_open, expected",
    [
        # Auto-Accept + geschlossene reguläre Sitzung → unterdrücken (Anti-Spam)
        (True, False, True),
        # Auto-Accept + offene reguläre Sitzung → nicht unterdrücken (In-Sitzung-Auto-Accept greift)
        (True, True, False),
        # Kein Auto-Accept, Sitzung zu → normale Info-Karte, nie unterdrücken
        (False, False, False),
        # Kein Auto-Accept, Sitzung offen → normale Karte mit JA/NEIN, nie unterdrücken
        (False, True, False),
    ],
)
def test_suppress_auto_accept_out_of_session(auto_accept, regular_session_open, expected):
    assert suppress(auto_accept, regular_session_open) is expected
