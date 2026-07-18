"""Idempotenz für mutierende API-v1-Aktionen (W7).

Ein `Idempotency-Key` je mutierender Anfrage sorgt dafür, dass ein wiederholter Request mit
demselben Schlüssel GENAU EINMAL ausgeführt wird und beim zweiten Mal das gespeicherte Ergebnis
zurückgibt (kein Doppel-Trade bei Retry/Doppelklick).

In-Prozess-Store (threadsicher). Für Mehr-Prozess-/Neustart-Garantien wird derselbe Vertrag
später gegen den DB-Seam implementiert — die Schnittstelle (`get_or_run`) bleibt gleich.
"""

from __future__ import annotations

import threading


class IdempotencyStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._store: dict[tuple, dict] = {}

    def get(self, user_id: int, key: str):
        with self._lock:
            return self._store.get((user_id, key))

    def get_or_run(self, user_id: int, key: str, fn):
        """Führt `fn()` nur beim ERSTEN Auftreten von (user_id, key) aus.

        Gibt `(result, replayed)` zurück: `replayed=True`, wenn das gespeicherte Ergebnis eines
        früheren identischen Requests zurückkam. `fn` läuft unter dem Lock → derselbe Schlüssel
        wird nie doppelt ausgeführt (konservativ; die DB-Variante nutzt später einen Unique-Key).
        """
        with self._lock:
            cache_key = (user_id, key)
            if cache_key in self._store:
                return self._store[cache_key], True
            result = fn()
            self._store[cache_key] = result
            return result, False
