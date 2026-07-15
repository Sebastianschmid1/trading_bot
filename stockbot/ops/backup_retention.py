"""Auswahl alter PostgreSQL-Backups nach einer einfachen GFS-Retention."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable


_BACKUP_NAME = re.compile(
    r"^stockbot-(?P<timestamp>\d{8}-\d{6})\.dump\.(?:age|gpg)$"
)


def backups_to_delete(
    filenames: Iterable[str],
    now: datetime | None = None,
    *,
    daily: int = 7,
    weekly: int = 4,
) -> list[str]:
    """Gibt verwaltete Backups zur Loeschung zurueck.

    Behalten wird jeweils das neueste Backup der letzten ``daily`` belegten
    Kalendertage und der letzten ``weekly`` belegten ISO-Wochen. Unbekannte
    Dateinamen und Zeitstempel in der Zukunft werden aus Sicherheitsgruenden
    ignoriert.
    """
    if daily < 0 or weekly < 0:
        raise ValueError("Aufbewahrungszahlen duerfen nicht negativ sein")

    reference = now or datetime.now()
    parsed: list[tuple[datetime, str]] = []
    for filename in filenames:
        name = Path(filename).name
        match = _BACKUP_NAME.fullmatch(name)
        if match is None:
            continue
        try:
            timestamp = datetime.strptime(match.group("timestamp"), "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        if timestamp <= reference:
            parsed.append((timestamp, filename))

    parsed.sort(reverse=True)
    keep: set[str] = set()

    seen_days = set()
    for timestamp, filename in parsed:
        if timestamp.date() not in seen_days and len(seen_days) < daily:
            seen_days.add(timestamp.date())
            keep.add(filename)

    seen_weeks = set()
    for timestamp, filename in parsed:
        week = timestamp.isocalendar()[:2]
        if week not in seen_weeks and len(seen_weeks) < weekly:
            seen_weeks.add(week)
            keep.add(filename)

    return [filename for timestamp, filename in parsed if filename not in keep]


# Lesbarer Alias fuer Aufrufer, die die Funktion als Auswahloperation benennen.
select_backups_to_delete = backups_to_delete


def main() -> int:
    parser = argparse.ArgumentParser(description="Alte Stockbot-Backups auswaehlen")
    parser.add_argument("filenames", nargs="*", help="vorhandene Backup-Dateien")
    parser.add_argument("--delete", action="store_true", help="ausgewaehlte Dateien loeschen")
    args = parser.parse_args()

    for filename in backups_to_delete(args.filenames):
        if args.delete:
            Path(filename).unlink(missing_ok=True)
        else:
            print(filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
