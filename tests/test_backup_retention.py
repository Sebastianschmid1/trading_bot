from datetime import datetime

import pytest

from stockbot.ops.backup_retention import backups_to_delete


NOW = datetime(2026, 7, 15, 12)


def backup(day: str, time: str = "033000", extension: str = "age") -> str:
    return f"stockbot-{day}-{time}.dump.{extension}"


def test_empty_list_deletes_nothing():
    assert backups_to_delete([], NOW) == []


def test_keeps_latest_seven_days_and_four_weekly_representatives():
    names = [
        backup("20260621"),
        backup("20260628"),
        *(backup(f"202607{day:02d}") for day in range(1, 16)),
    ]

    deleted = set(backups_to_delete(names, NOW))

    expected_kept = {
        *(backup(f"202607{day:02d}") for day in range(9, 16)),
        backup("20260705"),
        backup("20260628"),
    }
    assert set(names) - deleted == expected_kept


def test_keeps_only_latest_backup_in_a_retained_bucket():
    names = [backup("20260715", "030000"), backup("20260715", "040000")]

    assert backups_to_delete(names, NOW) == [backup("20260715", "030000")]


def test_fewer_backups_than_retention_are_all_kept():
    names = [backup("20260715"), backup("20260714", extension="gpg")]

    assert backups_to_delete(names, NOW) == []


def test_malformed_and_future_names_are_ignored():
    names = [
        "README.txt",
        "stockbot-invalid.dump.age",
        backup("20261340"),
        backup("20260716"),
        backup("20260715"),
    ]

    assert backups_to_delete(names, NOW, daily=0, weekly=0) == [backup("20260715")]


def test_negative_retention_is_rejected():
    with pytest.raises(ValueError):
        backups_to_delete([], NOW, daily=-1)
