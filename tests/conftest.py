"""Pytest-Bootstrap: deterministische Test-Defaults, BEVOR `stockbot` importiert wird.

`stockbot/config.py` bricht schon beim **Import** mit einem RuntimeError ab, wenn kein
`ENCRYPTION_KEY` in der Umgebung (oder als systemd-Credential) steht. Ohne diese Datei
startet die Suite aus einem frischen Checkout deshalb gar nicht — jedes Testmodul, das
`stockbot` importiert, scheitert bereits beim Einsammeln. pytest lädt `tests/conftest.py`
vor dem ersten Testmodul, also vor jedem `import stockbot`.

Regeln für diese Datei:

* Ausschließlich `os.environ.setdefault` — wer echte Werte exportiert hat, behält sie.
* Nur Werte, die für die **Importierbarkeit** nötig sind. Keine Fixtures, keine
  Test-Infrastruktur.
* Die Sicherheits-Defaults werden **nicht** verschoben: kein `TRADING_MODE`, kein
  `ALLOW_LIVE_TRADING`, kein `ALLOW_MARGIN`/`ALLOW_OPTIONS`/`ALLOW_SHORTS`, kein
  `DB_BACKEND`, kein `ALLOW_SQLITE_RUNTIME`. Paper bleibt Standard, Live bleibt gesperrt,
  das Backend bleibt so, wie es die Umgebung vorgibt.
"""

import os

# Fester Fernet-Dummy-Schlüssel: der einzige Wert, den config.py beim Import hart verlangt
# (alle übrigen Einstellungen haben Defaults). Er verschlüsselt ausschließlich Wegwerf-Daten
# in Test-Datenbanken und schützt nichts — deshalb steht er im Klartext im Repo. Ein fester
# statt zufällig erzeugter Wert hält Testläufe reproduzierbar.
os.environ.setdefault("ENCRYPTION_KEY", "Yxcqete0VtACZB1bAJsyy8kIewHwOJJc7ION48clywg=")

# Ohne Vorgabe ermittelt config.py die LAN-IP der Maschine über einen UDP-Socket
# (`_detect_lan_ip`) — maschinenabhängig und auf netzlosen CI-Runnern nutzlos. Das `http://`
# lässt COOKIE_SECURE und HSTS_ENABLED auf ihrem bisherigen Default (aus); ein `https://`
# würde sie stillschweigend anschalten.
os.environ.setdefault("DASHBOARD_BASE_URL", "http://localhost:8000")
