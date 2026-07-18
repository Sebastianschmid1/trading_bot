"""Rollenbasierte Autorisierung für API v1 (W7).

Es gibt (noch) keine Rollenspalte — die Admin-Rolle leitet sich aus dem bestehenden
`config.ADMIN_CHAT_ID` ab (gleiches Admin-Konzept wie im Telegram-Bot). Reine, IO-freie Logik.
"""

from __future__ import annotations

from enum import Enum

from stockbot import config


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"


class Permission(str, Enum):
    READ_SIGNALS = "read:signals"
    ACCEPT_TRADE = "accept:trade"
    MANAGE_KILL_SWITCH = "manage:kill_switch"
    VIEW_ADMIN = "view:admin"


_ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.USER: {Permission.READ_SIGNALS, Permission.ACCEPT_TRADE},
    Role.ADMIN: {
        Permission.READ_SIGNALS, Permission.ACCEPT_TRADE,
        Permission.MANAGE_KILL_SWITCH, Permission.VIEW_ADMIN,
    },
}


def role_for_user(user: dict | None) -> Role:
    """Leitet die Rolle aus dem Nutzer ab (Admin nur bei `config.ADMIN_CHAT_ID`)."""
    if not user:
        return Role.USER
    admin_id = getattr(config, "ADMIN_CHAT_ID", None)
    try:
        if admin_id is not None and int(user.get("user_id", -1)) == int(admin_id):
            return Role.ADMIN
    except (TypeError, ValueError):
        pass
    return Role.USER


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in _ROLE_PERMISSIONS.get(role, set())


def user_has_permission(user: dict | None, permission: Permission) -> bool:
    return has_permission(role_for_user(user), permission)
