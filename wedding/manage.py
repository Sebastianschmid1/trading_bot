#!/usr/bin/env python3
"""Kleines CLI für die Benutzerverwaltung der Hochzeits-Galerie.

    python wedding/manage.py set-password amelie
    python wedding/manage.py add-user oma "Oma Erna"
    python wedding/manage.py add-user gast "Gast" --guest
    python wedding/manage.py set-upload gast on
    python wedding/manage.py set-delete gast off
    python wedding/manage.py --file /etc/wedding/users.json seed deploy/wedding-users.json

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
    """Atomar schreiben und dabei bestehende Dateirechte übernehmen.

    Neben dem Modus werden auch Owner/Gruppe der bestehenden Zieldatei
    übernommen: Auf dem Server ist ``users.json`` bewusst ``root:wedding``
    mit Modus ``0640``, damit der unprivilegierte Dienst-User via Gruppe
    lesen kann. Ohne das ``chown`` entstünde die neue Datei (als root
    geschrieben) als ``root:root`` und der Dienst könnte sie nicht mehr
    lesen. Bei Erstanlage (Datei fehlt) wird kein Owner erzwungen.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.stat()
        mode = stat.S_IMODE(existing.st_mode)
        owner: tuple[int, int] | None = (existing.st_uid, existing.st_gid)
    except FileNotFoundError:
        mode = 0o640
        owner = None
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(users, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.chmod(mode)
        if owner is not None:
            # Nur der Server (als root) darf/muss Owner/Gruppe setzen. Ohne die
            # nötigen Rechte (Nicht-root im Dev/CI) oder auf Plattformen ohne
            # os.chown (Windows) ist das kein Fehler: os.replace behält den
            # bestehenden Owner ohnehin bei, wenn er sich nicht ändert.
            try:
                os.chown(tmp, owner[0], owner[1])
            except (PermissionError, NotImplementedError, AttributeError, OSError):
                pass
        os.replace(tmp, path)
    except BaseException:
        # tmp-Datei nicht als Leiche liegen lassen, falls os.replace nie erreicht wird.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


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
    entry: dict = {"display_name": args.display_name.strip() or username}
    if args.guest:
        entry["can_upload"] = False
    entry.update(hash_password(ask_password(username), iterations=DEFAULT_ITERATIONS))
    users[username] = entry
    save(path, users)
    art = "Nur-Ansehen-Zugang" if args.guest else "Benutzer"
    print(f"{art} {username!r} in {path} angelegt.")
    return 0


def cmd_set_upload(args: argparse.Namespace) -> int:
    """Setzt `can_upload` eines bestehenden Benutzers explizit auf true/false.

    Nötig für den Rollout auf einem laufenden Server: `seed` fasst bestehende
    Einträge grundsätzlich nicht an, Rechte-Änderungen laufen deshalb hierüber.
    """
    path = resolve_users_file(args.file)
    users = load(path)
    username = args.username.strip().lower()
    if username not in users:
        raise SystemExit(f"Benutzer {username!r} existiert nicht in {path}.")
    allowed = args.value == "on"
    entry = dict(users[username])
    entry["can_upload"] = allowed
    users[username] = entry
    save(path, users)
    zustand = "darf hochladen" if allowed else "kann nur ansehen"
    print(f"{username!r}: can_upload={str(allowed).lower()} ({zustand}) — {path}")
    return 0


def cmd_set_delete(args: argparse.Namespace) -> int:
    """Setzt `can_delete` eines bestehenden Benutzers explizit auf true/false.

    Nötig für den Rollout auf einem laufenden Server: `seed` fasst bestehende
    Einträge grundsätzlich nicht an, Rechte-Änderungen laufen deshalb hierüber.
    """
    path = resolve_users_file(args.file)
    users = load(path)
    username = args.username.strip().lower()
    if username not in users:
        raise SystemExit(f"Benutzer {username!r} existiert nicht in {path}.")
    allowed = args.value == "on"
    entry = dict(users[username])
    entry["can_delete"] = allowed
    users[username] = entry
    save(path, users)
    zustand = "darf löschen" if allowed else "kann nicht löschen"
    print(f"{username!r}: can_delete={str(allowed).lower()} ({zustand}) — {path}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    """Fügt nur *fehlende* Benutzer aus einer Quelldatei ein.

    Bestehende Einträge bleiben unangetastet — auch ihre Passwörter und Felder.
    Genau dafür ist das Kommando da: eine bereits ausgerollte users.json um neue
    Zugänge ergänzen, ohne die auf dem Server gesetzten Passwörter zu verlieren.
    """
    path = resolve_users_file(args.file)
    source = Path(args.source)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Quelle {source} nicht lesbar: {exc}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Quelle {source} ist kein gültiges JSON: {exc}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"{source} enthält kein JSON-Objekt.")

    users = load(path)
    missing = [
        name for name, entry in data.items() if isinstance(entry, dict) and name not in users
    ]
    if not missing:
        print(f"seed: nichts zu tun — alle Benutzer aus {source} sind bereits in {path}.")
        return 0
    for name in missing:
        users[name] = data[name]
    save(path, users)
    print(f"seed: ergänzt in {path}: {', '.join(sorted(missing))}")
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
    add.add_argument(
        "--guest",
        action="store_true",
        help="Nur-Ansehen-Zugang anlegen (can_upload=false: kein Upload, kein Löschen)",
    )
    add.set_defaults(func=cmd_add_user)

    upload_flag = sub.add_parser(
        "set-upload", help="Upload-Recht eines bestehenden Benutzers setzen (on/off)"
    )
    upload_flag.add_argument("username")
    upload_flag.add_argument(
        "value", choices=["on", "off"], help="on = darf hochladen, off = nur ansehen"
    )
    upload_flag.set_defaults(func=cmd_set_upload)

    delete_flag = sub.add_parser(
        "set-delete", help="Lösch-Recht eines bestehenden Benutzers setzen (on/off)"
    )
    delete_flag.add_argument("username")
    delete_flag.add_argument(
        "value", choices=["on", "off"], help="on = darf löschen, off = kann nicht löschen"
    )
    delete_flag.set_defaults(func=cmd_set_delete)

    seed = sub.add_parser(
        "seed", help="Fehlende Benutzer aus einer Quelldatei ergänzen (bestehende bleiben)"
    )
    seed.add_argument("source", help="Quell-users.json, z. B. deploy/wedding-users.json")
    seed.set_defaults(func=cmd_seed)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
