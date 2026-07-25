"""Hochzeits-Foto-Galerie „Amelie & Tobi".

Eigenständige kleine FastAPI-App, die auf demselben Server als **getrennter**
systemd-Dienst läuft. Sie hat bewusst keinerlei Verbindung zum stockbot-Code:
eigener Systemuser, eigenes Datenverzeichnis, eigene Auth.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
