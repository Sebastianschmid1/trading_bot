"""Auth-Bausteine der Hochzeits-Galerie: Passwörter, Session-Cookie, Rate-Limit.

Bewusst minimal und ohne zusätzliche Abhängigkeiten (nur Python-Stdlib):

* **Passwörter** liegen als PBKDF2-HMAC-SHA256-Hashes in einer `users.json`.
* **Sessions** sind ein HMAC-signiertes Cookie (kein Server-State, damit ein
  Neustart niemanden ausloggt).
* **Login-Rate-Limit** ist in-memory pro Client-IP — reicht für eine Handvoll
  Hochzeitsgäste hinter einem einzelnen Prozess.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

log = logging.getLogger(__name__)

#: Name des Session-Cookies.
SESSION_COOKIE = "wedding_session"
#: Gültigkeit einer Session (30 Tage) — Gäste sollen nicht ständig neu tippen.
SESSION_MAX_AGE = 30 * 24 * 60 * 60
#: PBKDF2-Runden für neu erzeugte Hashes.
DEFAULT_ITERATIONS = 600_000
#: Maximale Fehlversuche je IP innerhalb des Zeitfensters.
LOGIN_MAX_FAILURES = 5
#: Länge des Rate-Limit-Fensters in Sekunden.
LOGIN_WINDOW_SECONDS = 60


# --------------------------------------------------------------------------- #
# users.json
# --------------------------------------------------------------------------- #
def load_users(path: str | Path) -> dict[str, dict[str, Any]]:
    """Liest die users.json. Fehlt/kaputt → leeres Dict (Login schlägt dann fehl)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("users.json fehlt: %s — Login ist damit nicht möglich.", path)
        return {}
    except OSError as exc:
        log.error("users.json nicht lesbar (%s): %s", path, exc)
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("users.json ist kein gültiges JSON (%s): %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.error("users.json hat kein Objekt auf oberster Ebene (%s).", path)
        return {}
    return {str(name): entry for name, entry in data.items() if isinstance(entry, dict)}


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    salt: bytes | None = None,
) -> dict[str, Any]:
    """Erzeugt einen users.json-Eintrag (ohne `display_name`) für *password*."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {"salt_hex": salt.hex(), "hash_hex": digest.hex(), "iterations": iterations}


def verify_password(entry: Mapping[str, Any], password: str) -> bool:
    """Prüft *password* gegen einen users.json-Eintrag (konstante Zeit)."""
    try:
        salt = bytes.fromhex(str(entry["salt_hex"]))
        expected = bytes.fromhex(str(entry["hash_hex"]))
        iterations = int(entry.get("iterations", DEFAULT_ITERATIONS))
    except (KeyError, TypeError, ValueError):
        return False
    if iterations <= 0 or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def display_name_for(users: Mapping[str, Mapping[str, Any]], username: str) -> str:
    """Anzeigename aus der users.json — Fallback ist der Benutzername selbst."""
    entry = users.get(username) or {}
    name = str(entry.get("display_name") or "").strip()
    return name or username


# --------------------------------------------------------------------------- #
# Session-Cookie:  "username|expires_epoch|hexdigest"
# --------------------------------------------------------------------------- #
def _secret_bytes(secret: str) -> bytes:
    """Secret ist hex-kodiert; alles andere wird als UTF-8 genommen."""
    text = secret.strip()
    try:
        return bytes.fromhex(text)
    except ValueError:
        return text.encode("utf-8")


def _sign(secret: str, payload: str) -> str:
    return hmac.new(_secret_bytes(secret), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_cookie(
    secret: str,
    username: str,
    *,
    max_age: int = SESSION_MAX_AGE,
    now: float | None = None,
) -> str:
    """Baut den signierten Cookie-Wert für *username*."""
    expires = int((time.time() if now is None else now) + max_age)
    payload = f"{username}|{expires}"
    return f"{payload}|{_sign(secret, payload)}"


def read_session_cookie(secret: str, raw: str | None) -> str | None:
    """Verifiziert den Cookie-Wert; gibt den Benutzernamen oder ``None`` zurück."""
    if not raw:
        return None
    parts = raw.rsplit("|", 2)
    if len(parts) != 3:
        return None
    username, expires_raw, digest = parts
    if not username:
        return None
    expected = _sign(secret, f"{username}|{expires_raw}")
    if not hmac.compare_digest(expected, digest):
        return None
    try:
        expires = int(expires_raw)
    except ValueError:
        return None
    if expires <= time.time():
        return None
    return username


def load_or_create_secret(path: str | Path) -> str:
    """Liest das Session-Secret; legt es an, wenn die Datei fehlt.

    Auf dem Server schreibt `deploy/setup_wedding.sh` das Secret vorab —
    das Anlegen hier ist nur der Komfortpfad für die lokale Entwicklung.
    """
    secret_path = Path(path)
    try:
        text = secret_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.error("Session-Secret nicht lesbar (%s): %s", secret_path, exc)

    generated = secrets.token_hex(32)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text(generated + "\n", encoding="utf-8")
        secret_path.chmod(0o600)
        log.warning("Session-Secret neu erzeugt: %s", secret_path)
    except OSError as exc:
        log.warning(
            "Session-Secret konnte nicht gespeichert werden (%s): %s — "
            "nutze ein flüchtiges Secret (Sessions überleben keinen Neustart).",
            secret_path,
            exc,
        )
    return generated


# --------------------------------------------------------------------------- #
# Rate-Limit
# --------------------------------------------------------------------------- #
class LoginRateLimiter:
    """Zählt Fehlversuche je Schlüssel (Client-IP) in einem Zeitfenster."""

    def __init__(
        self,
        max_failures: int = LOGIN_MAX_FAILURES,
        window_seconds: int = LOGIN_WINDOW_SECONDS,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures[key]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return bucket

    def is_blocked(self, key: str) -> bool:
        """True, sobald das Limit im aktuellen Fenster erreicht ist."""
        with self._lock:
            return len(self._prune(key, time.time())) >= self.max_failures

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key, time.time()).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
