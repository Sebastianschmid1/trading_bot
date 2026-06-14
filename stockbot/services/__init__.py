"""
Framework-neutrale Service-Schicht: die Geschäftslogik des Bots (Trade-Aktionen, Einstellungen,
Watchlist) ohne Telegram-/HTTP-Bezug. Telegram-Handler UND ein künftiges Web-Backend rufen
dieselben Funktionen auf → beide Kanäle verhalten sich garantiert identisch (Phase 0 des
Telegram→Website-Konzepts, siehe docs/WEBSITE_KONZEPT.md).
"""
