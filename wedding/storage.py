"""Foto-Ablage der Hochzeits-Galerie.

Layout unterhalb von `WEDDING_DATA_DIR`::

    photos/<uuid4hex><.ext>     # das Bild, Server-Name ist immer neu erzeugt
    photos/<uuid4hex>.json      # Sidecar mit Uploader + Originalname + Zeit

Der vom Client gelieferte Dateiname landet **nur** in den Metadaten, nie im
Dateisystempfad — damit ist Path-Traversal über den Upload ausgeschlossen. Beim
Ausliefern wird der Name zusätzlich strikt gegen ein Regex geprüft.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

log = logging.getLogger(__name__)

#: Erlaubte Endungen (Handy-Kameras liefern häufig HEIC/HEIF).
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif")
#: Endungen, die Browser typischerweise *nicht* rendern können.
UNRENDERABLE_EXTENSIONS = (".heic", ".heif")
#: Maximale Anzahl Dateien pro Upload-Request.
MAX_FILES_PER_REQUEST = 30
#: Server-Dateinamen: 32 Hex-Zeichen + Kleinbuchstaben-Endung. Sonst nichts.
PHOTO_NAME_RE = re.compile(r"^[a-f0-9]{32}\.[a-z]+$")
#: Chunk-Größe beim Streamen auf die Platte.
CHUNK_SIZE = 1024 * 1024

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


class PhotoRejected(Exception):
    """Upload einer einzelnen Datei abgelehnt (Endung, Größe, leer)."""


class _Uploaded(Protocol):
    """Minimaler Ausschnitt von `starlette.datastructures.UploadFile`."""

    filename: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class Photo:
    """Ein Foto samt Metadaten für die Galerie-Ansicht."""

    name: str
    uploader: str
    display_name: str
    original_name: str
    uploaded_at: str
    size: int

    @property
    def extension(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def renderable(self) -> bool:
        """False für HEIC/HEIF — dafür zeigt die Galerie eine Download-Karte."""
        return self.extension not in UNRENDERABLE_EXTENSIONS

    @property
    def uploaded_label(self) -> str:
        """Deutsches Datumsformat für die Anzeige (Fallback: Rohwert)."""
        try:
            moment = datetime.fromisoformat(self.uploaded_at)
        except ValueError:
            return self.uploaded_at
        return moment.strftime("%d.%m.%Y, %H:%M")


def extension_of(filename: str | None) -> str:
    """Endung eines Client-Dateinamens in Kleinbuchstaben (inkl. Punkt)."""
    return Path(filename or "").suffix.lower()


def content_type_for(name: str) -> str:
    return _CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _human_size(num_bytes: int) -> str:
    """Größenangabe für Fehlermeldungen — MB, bei kleinen Limits KB."""
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes // (1024 * 1024)} MB"
    return f"{max(1, num_bytes // 1024)} KB"


class PhotoStore:
    """Liest/schreibt Fotos + Sidecar-Metadaten unter ``data_dir/photos``."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.photos_dir = self.data_dir / "photos"

    def ensure_dirs(self) -> None:
        self.photos_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- Namen --
    @staticmethod
    def is_valid_name(name: str) -> bool:
        """Nur selbst vergebene Namen passieren — kein `/`, kein `..`, kein Punkt-Trick."""
        return bool(PHOTO_NAME_RE.fullmatch(name or "")) and Path(name).suffix.lower() in ALLOWED_EXTENSIONS

    def photo_path(self, name: str) -> Path | None:
        """Pfad zu einem existierenden Foto — sonst ``None``."""
        if not self.is_valid_name(name):
            return None
        path = self.photos_dir / name
        # Doppelter Boden: der aufgelöste Pfad muss im photos-Verzeichnis liegen.
        try:
            path.relative_to(self.photos_dir)
        except ValueError:
            return None
        return path if path.is_file() else None

    def _meta_path(self, name: str) -> Path:
        return self.photos_dir / (Path(name).stem + ".json")

    # ---------------------------------------------------------------- Lesen --
    def _read_meta(self, name: str) -> dict[str, Any]:
        try:
            data = json.loads(self._meta_path(name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def uploader_of(self, name: str) -> str:
        return str(self._read_meta(name).get("uploader") or "")

    def list_photos(self) -> list[Photo]:
        """Alle Fotos, neueste zuerst."""
        if not self.photos_dir.is_dir():
            return []
        photos: list[Photo] = []
        for entry in self.photos_dir.iterdir():
            if not entry.is_file() or not self.is_valid_name(entry.name):
                continue
            meta = self._read_meta(entry.name)
            try:
                stat = entry.stat()
            except OSError:
                continue
            uploaded_at = str(meta.get("uploaded_at") or "") or (
                datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat()
            )
            photos.append(
                Photo(
                    name=entry.name,
                    uploader=str(meta.get("uploader") or ""),
                    display_name=str(meta.get("display_name") or meta.get("uploader") or "Gast"),
                    original_name=str(meta.get("original_name") or entry.name),
                    uploaded_at=uploaded_at,
                    size=stat.st_size,
                )
            )
        photos.sort(key=lambda photo: (photo.uploaded_at, photo.name), reverse=True)
        return photos

    # -------------------------------------------------------------- Schreiben --
    async def save_upload(
        self,
        upload: _Uploaded,
        *,
        uploader: str,
        display_name: str,
        max_bytes: int,
    ) -> Photo:
        """Streamt einen Upload auf die Platte und legt das Sidecar an.

        Wirft `PhotoRejected`, wenn Endung, Größe oder Inhalt nicht passen.
        """
        original_name = (upload.filename or "").strip() or "foto"
        extension = extension_of(original_name)
        if extension not in ALLOWED_EXTENSIONS:
            raise PhotoRejected("Dateityp nicht erlaubt (nur Fotos: JPG, PNG, GIF, WEBP, HEIC).")

        self.ensure_dirs()
        name = f"{uuid4().hex}{extension}"
        target = self.photos_dir / name
        size = 0
        try:
            with target.open("wb") as handle:
                while True:
                    chunk = await upload.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise PhotoRejected(f"Datei ist zu groß (max. {_human_size(max_bytes)}).")
                    handle.write(chunk)
            if size == 0:
                raise PhotoRejected("Datei ist leer.")
        except PhotoRejected:
            target.unlink(missing_ok=True)
            raise
        except OSError as exc:
            target.unlink(missing_ok=True)
            log.error("Upload konnte nicht gespeichert werden (%s): %s", target, exc)
            raise PhotoRejected("Datei konnte nicht gespeichert werden.") from exc

        uploaded_at = _utc_now_iso()
        self._write_meta(
            name,
            {
                "uploader": uploader,
                "display_name": display_name,
                "original_name": original_name,
                "uploaded_at": uploaded_at,
            },
        )
        return Photo(
            name=name,
            uploader=uploader,
            display_name=display_name,
            original_name=original_name,
            uploaded_at=uploaded_at,
            size=size,
        )

    def _write_meta(self, name: str, meta: dict[str, Any]) -> None:
        path = self._meta_path(name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def delete(self, name: str, *, username: str) -> None:
        """Löscht ein eigenes Foto.

        `FileNotFoundError` → gibt es nicht, `PermissionError` → fremder Upload.
        """
        path = self.photo_path(name)
        if path is None:
            raise FileNotFoundError(name)
        if self.uploader_of(name) != username:
            raise PermissionError(name)
        path.unlink(missing_ok=True)
        self._meta_path(name).unlink(missing_ok=True)
