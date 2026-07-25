#!/usr/bin/env python3
"""Kleines CLI für die Benutzerverwaltung der Hochzeits-Galerie.

    python wedding/manage.py set-password amelie
    python wedding/manage.py add-user oma "Oma Erna"

Die Ziel-Datei kommt aus ``--file``, sonst aus ``WEDDING_USERS_FILE``, sonst
``./data/wedding/users.json``. Das Passwort wird per getpass abgefragt; ist
``WEDDING_NEW_PASSWORD`` gesetzt, wird dieser Wert genommen (für Skripte).
Geschrieben wird atomar (tmp + rename), Rechte der Zieldatei bleiben erhalten.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from wedding.auth import DEFAULT_ITERATIONS, hash_password  # noqa: E402

DEFAULT_USERS_FILE = "./data/wedding/users.json"


def resolve_users_file(argument: str | None) -> Path:
    return Path(argument or os.getenv("WEDDING_USERS_FILE") or DEFAULT_USERS_FILE)


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} enthält kein JSON-Objekt.")
    return data


def save(path: Path, users: dict[str, dict]) -> None:
    """Atomar schreiben und dabei bestehende Dateirechte übernehmen."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o640
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(mode)
    os.replace(tmp, path)


def ask_password(username: str) -> str:
    """Passwort aus Env (Skriptbetrieb) oder zweimal per getpass."""
    from_env = os.getenv("WEDDING_NEW_PASSWORD")
    if from_env:
        if len(from_env) < 4:
            raise SystemExit("Passwort ist zu kurz (mindestens 4 Zeichen).")
        return from_env
    first = getpass.getpass(f"Neues Passwort für {username}: ")
    if len(first) < 4:
        raise SystemExit("Passwort ist zu kurz (mindestens 4 Zeichen).")
    second = getpass.getpass("Passwort wiederholen: ")
    if first != second:
        raise SystemExit("Die Passwörter stimmen nicht überein.")
    return first


def cmd_set_password(args: argparse.Namespace) -> int:
    path = resolve_users_file(args.file)
    users = load(path)
    username = args.username.strip().lower()
    if username not in users:
        raise SystemExit(f"Benutzer {username!r} existiert nicht in {path} — erst 'add-user'.")
    entry = dict(users[username])
    entry.update(hash_password(ask_password(username), iterations=DEFAULT_ITERATIONS))
    users[username] = entry
    save(path, users)
    print(f"Passwort für {username!r} in {path} aktualisiert.")
    return 0


def cmd_add_user(args: argparse.Namespace) -> int:
    path = resolve_users_file(args.file)
    users = load(path)
    username = args.username.strip().lower()
    if not username or "|" in username:
        raise SystemExit("Ungültiger Benutzername (leer oder enthält '|').")
    if username in users:
        raise SystemExit(f"Benutzer {username!r} existiert bereits — 'set-password' nutzen.")
    entry = {"display_name": args.display_name.strip() or username}
    entry.update(hash_password(ask_password(username), iterations=DEFAULT_ITERATIONS))
    users[username] = entry
    save(path, users)
    print(f"Benutzer {username!r} in {path} angelegt.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benutzerverwaltung der Hochzeits-Galerie.")
    parser.add_argument(
        "--file",
        help="Pfad zur users.json (Default: $WEDDING_USERS_FILE oder ./data/wedding/users.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    set_pw = sub.add_parser("set-password", help="Passwort eines bestehenden Benutzers ändern")
    set_pw.add_argument("username")
    set_pw.set_defaults(func=cmd_set_password)

    add = sub.add_parser("add-user", help="Neuen Benutzer anlegen")
    add.add_argument("username")
    add.add_argument("display_name")
    add.set_defaults(func=cmd_add_user)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
