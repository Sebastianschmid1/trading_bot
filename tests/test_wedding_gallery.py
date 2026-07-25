"""Tests der eigenständigen Hochzeits-Foto-Galerie (`wedding/`).

Alles läuft gegen eine frische App-Instanz auf `tmp_path`: eigene users.json mit
selbst erzeugten Hashes (bewusst NICHT die echten Einträge aus
`deploy/wedding-users.json`), eigenes Secret, eigenes Datenverzeichnis.
"""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wedding.app import create_app
from wedding.auth import SESSION_COOKIE, hash_password, make_session_cookie
from wedding.manage import main as manage_main
from wedding.manage import save as manage_save

# Kleine Rundenzahl: die Tests loggen sich oft ein, 600k Runden wären reine Wartezeit.
TEST_ITERATIONS = 1_000

# Frei erfundene Test-Passwörter — sie haben nichts mit den echten Zugängen zu tun.
AMELIE_PW = "brautstrauss"
TOBI_PW = "trauzeuge"
GAST_PW = "einladungskarte"
NURGUCKER_PW = "nurgucken"

# 1x1-PNG (echte Bytes, damit der Upload nicht nur „irgendwas" ist).
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63f8ffff3f0005fe02fea735a4d200"
    "00000049454e44ae426082"
)


@pytest.fixture()
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "secret"
    path.write_text("a" * 64, encoding="utf-8")
    return path


@pytest.fixture()
def users_file(tmp_path: Path) -> Path:
    # amelie/tobi bewusst OHNE `can_upload` — das prüft die Abwärtskompatibilität mit.
    # `gast` ist der geteilte Zugang (darf hochladen), `nurgucker` der Nur-Ansehen-Fall.
    users = {
        "amelie": {"display_name": "Amelie", **hash_password(AMELIE_PW, iterations=TEST_ITERATIONS)},
        "tobi": {"display_name": "Tobi", **hash_password(TOBI_PW, iterations=TEST_ITERATIONS)},
        "gast": {
            "display_name": "Gast",
            "can_upload": True,
            "can_delete": False,
            **hash_password(GAST_PW, iterations=TEST_ITERATIONS),
        },
        "nurgucker": {
            "display_name": "Nur Gucker",
            "can_upload": False,
            **hash_password(NURGUCKER_PW, iterations=TEST_ITERATIONS),
        },
    }
    path = tmp_path / "users.json"
    path.write_text(json.dumps(users), encoding="utf-8")
    return path


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "daten"


@pytest.fixture()
def app(data_dir: Path, users_file: Path, secret_file: Path):
    return create_app(
        data_dir=data_dir,
        users_file=users_file,
        secret_file=secret_file,
        max_bytes=2048,
        cookie_secure=False,
    )


@pytest.fixture()
def client(app) -> TestClient:
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def login(test_client: TestClient, username: str, password: str):
    return test_client.post("/login", data={"username": username, "password": password})


def photos_dir(data_dir: Path) -> Path:
    return data_dir / "photos"


def upload_png(test_client: TestClient, filename: str = "strauss.png", payload: bytes = PNG_BYTES):
    return test_client.post("/upload", files={"files": (filename, payload, "image/png")})


def make_jpeg_bytes(size: tuple[int, int] = (24, 18), color: tuple[int, int, int] = (210, 120, 150)) -> bytes:
    """Erzeugt ein echtes kleines JPEG (setzt Pillow voraus)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


# Minimaler MP4-Header (ftyp-Box) — reicht als „echte" Video-Bytes für den Upload.
MP4_BYTES = bytes.fromhex("0000001c66747970"  # size + 'ftyp'
                          "6d70343200000000") + b"mp42isom"
MOV_BYTES = bytes.fromhex("0000001466747970") + b"qt  " + bytes(8)


# --------------------------------------------------------------------------- #
# Zugriffsschutz
# --------------------------------------------------------------------------- #
def test_healthz_braucht_keinen_login(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


def test_galerie_ohne_login_leitet_auf_login(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_kaputte_cookie_signatur_gilt_als_ausgeloggt(client: TestClient) -> None:
    echt = make_session_cookie("a" * 64, "amelie")
    # Letztes Hex-Zeichen der Signatur garantiert verändern (nicht "0" hart setzen —
    # das wäre in 1 von 16 Läufen dasselbe Zeichen und damit gar keine Manipulation).
    client.cookies.set(SESSION_COOKIE, echt[:-1] + ("1" if echt[-1] == "0" else "0"))
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_fremdes_secret_erzeugt_keine_gueltige_session(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, make_session_cookie("b" * 64, "amelie"))
    assert client.get("/").status_code == 303


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def test_login_mit_falschem_passwort_wird_abgelehnt(client: TestClient) -> None:
    response = login(client, "amelie", "falsch")
    assert response.status_code == 401
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/").status_code == 303


def test_login_mit_richtigem_passwort_fuehrt_in_die_galerie(client: TestClient) -> None:
    response = login(client, "amelie", AMELIE_PW)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE in client.cookies

    gallery = client.get("/")
    assert gallery.status_code == 200
    assert "Amelie" in gallery.text


def test_rate_limit_greift_beim_sechsten_fehlversuch(client: TestClient) -> None:
    for _ in range(5):
        assert login(client, "amelie", "falsch").status_code == 401
    blocked = login(client, "amelie", "falsch")
    assert blocked.status_code == 429
    # Auch das richtige Passwort kommt im gesperrten Fenster nicht mehr durch.
    assert login(client, "amelie", AMELIE_PW).status_code == 429


def test_logout_entfernt_die_session(client: TestClient) -> None:
    login(client, "amelie", AMELIE_PW)
    assert client.get("/").status_code == 200
    response = client.post("/logout")
    assert response.status_code == 303
    assert client.get("/").status_code == 303


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
def test_upload_legt_foto_und_sidecar_an(client: TestClient, data_dir: Path) -> None:
    login(client, "amelie", AMELIE_PW)
    response = upload_png(client, "Erste Tanz.PNG")
    assert response.status_code == 200
    body = response.json()
    assert body["uploaded"] == 1
    name = body["results"][0]["name"]
    assert name.endswith(".png")

    stored = photos_dir(data_dir) / name
    assert stored.is_file()
    assert stored.read_bytes() == PNG_BYTES

    sidecar = json.loads((photos_dir(data_dir) / (Path(name).stem + ".json")).read_text("utf-8"))
    assert sidecar["uploader"] == "amelie"
    assert sidecar["display_name"] == "Amelie"
    assert sidecar["original_name"] == "Erste Tanz.PNG"
    assert sidecar["uploaded_at"].endswith("+00:00")

    gallery = client.get("/")
    assert name in gallery.text
    assert "Erste Tanz.PNG" in gallery.text


def test_upload_ohne_login_ist_401(client: TestClient) -> None:
    assert upload_png(client).status_code == 401


@pytest.mark.parametrize("filename", ["virus.exe", "notizen.txt", "ohne_endung"])
def test_upload_lehnt_fremde_dateitypen_ab(
    client: TestClient, data_dir: Path, filename: str
) -> None:
    login(client, "amelie", AMELIE_PW)
    response = client.post("/upload", files={"files": (filename, b"MZ nope", "image/png")})
    assert response.status_code == 400
    assert response.json()["results"][0]["ok"] is False
    assert list(photos_dir(data_dir).iterdir()) == []


def test_upload_lehnt_zu_grosse_datei_ab(client: TestClient, data_dir: Path) -> None:
    login(client, "amelie", AMELIE_PW)
    response = client.post("/upload", files={"files": ("gross.jpg", b"x" * 4096, "image/jpeg")})
    assert response.status_code == 400
    assert response.json()["results"][0]["ok"] is False
    # Der angefangene Torso muss wieder weg sein.
    assert list(photos_dir(data_dir).iterdir()) == []


def test_upload_mit_zu_vielen_dateien_wird_abgelehnt(client: TestClient, data_dir: Path) -> None:
    login(client, "amelie", AMELIE_PW)
    files = [("files", (f"foto{index}.png", PNG_BYTES, "image/png")) for index in range(31)]
    response = client.post("/upload", files=files)
    assert response.status_code == 400
    assert list(photos_dir(data_dir).iterdir()) == []


# --------------------------------------------------------------------------- #
# Ausliefern / Path-Traversal
# --------------------------------------------------------------------------- #
def test_foto_wird_nur_eingeloggt_ausgeliefert(client: TestClient) -> None:
    login(client, "amelie", AMELIE_PW)
    name = upload_png(client).json()["results"][0]["name"]
    client.post("/logout")
    assert client.get(f"/photos/{name}").status_code == 303

    login(client, "tobi", TOBI_PW)
    response = client.get(f"/photos/{name}")
    assert response.status_code == 200
    assert response.content == PNG_BYTES


@pytest.mark.parametrize(
    "name",
    [
        "..%2F..%2Fgeheim.txt",
        "..%2Fgeheim.txt",
        "%2Fetc%2Fpasswd",
        "geheim.txt",
        "abc.png",
        "0123456789abcdef0123456789abcdef.exe",
        "0123456789ABCDEF0123456789ABCDEF.png",
    ],
)
def test_pfad_traversal_und_muellnamen_liefern_nichts(
    client: TestClient, tmp_path: Path, name: str
) -> None:
    (tmp_path / "geheim.txt").write_text("streng geheim", encoding="utf-8")
    login(client, "amelie", AMELIE_PW)
    response = client.get(f"/photos/{name}")
    assert response.status_code in (404, 422)
    assert b"streng geheim" not in response.content


# --------------------------------------------------------------------------- #
# Löschen
# --------------------------------------------------------------------------- #
def test_eigenes_foto_kann_geloescht_werden(client: TestClient, data_dir: Path) -> None:
    login(client, "amelie", AMELIE_PW)
    name = upload_png(client).json()["results"][0]["name"]

    response = client.post(f"/photos/{name}/delete")
    assert response.status_code == 303
    assert not (photos_dir(data_dir) / name).exists()
    assert not (photos_dir(data_dir) / (Path(name).stem + ".json")).exists()


def test_fremdes_foto_kann_nicht_geloescht_werden(app, data_dir: Path) -> None:
    with TestClient(app, follow_redirects=False) as amelie:
        login(amelie, "amelie", AMELIE_PW)
        name = upload_png(amelie).json()["results"][0]["name"]

    with TestClient(app, follow_redirects=False) as tobi:
        login(tobi, "tobi", TOBI_PW)
        response = tobi.post(f"/photos/{name}/delete")

    assert response.status_code == 403
    assert (photos_dir(data_dir) / name).is_file()


def test_loeschen_ohne_login_leitet_auf_login(client: TestClient, app, data_dir: Path) -> None:
    with TestClient(app, follow_redirects=False) as amelie:
        login(amelie, "amelie", AMELIE_PW)
        name = upload_png(amelie).json()["results"][0]["name"]

    response = client.post(f"/photos/{name}/delete")
    assert response.status_code == 303
    assert (photos_dir(data_dir) / name).is_file()


# --------------------------------------------------------------------------- #
# Lösch-Recht (can_delete) — unabhängig von can_upload
# --------------------------------------------------------------------------- #
def test_gast_darf_eigenes_foto_nicht_loeschen(client: TestClient, data_dir: Path) -> None:
    # Der Gast darf hochladen (can_upload=true), aber nicht löschen (can_delete=false) —
    # auch nicht das selbst hochgeladene Foto.
    client.post("/gast", data={"password": GAST_PW})
    name = upload_png(client, "gastfoto.png").json()["results"][0]["name"]
    assert (photos_dir(data_dir) / name).is_file()

    response = client.post(f"/photos/{name}/delete")
    assert response.status_code == 403
    assert response.json()["detail"] == "Dieser Zugang kann keine Fotos löschen."
    # Das Foto liegt danach noch da.
    assert (photos_dir(data_dir) / name).is_file()


def test_galerie_zeigt_gast_keinen_loeschen_button(client: TestClient) -> None:
    # Gast lädt ein eigenes Foto hoch — trotzdem darf kein Lösch-Formular erscheinen.
    client.post("/gast", data={"password": GAST_PW})
    assert upload_png(client, "gastfoto.png").status_code == 200

    gallery = client.get("/")
    assert gallery.status_code == 200
    assert "/delete" not in gallery.text
    assert "tile__delete" not in gallery.text


def test_galerie_zeigt_berechtigtem_user_den_loeschen_button(client: TestClient) -> None:
    login(client, "amelie", AMELIE_PW)
    assert upload_png(client).status_code == 200

    gallery = client.get("/")
    assert gallery.status_code == 200
    assert "/delete" in gallery.text
    assert "tile__delete" in gallery.text


def test_user_ohne_can_delete_feld_darf_eigenes_foto_loeschen(
    client: TestClient, users_file: Path, data_dir: Path
) -> None:
    # Abwärtskompatibilität: amelie hat kein can_delete-Feld → darf weiter löschen.
    assert "can_delete" not in json.loads(users_file.read_text("utf-8"))["amelie"]
    login(client, "amelie", AMELIE_PW)
    name = upload_png(client).json()["results"][0]["name"]

    response = client.post(f"/photos/{name}/delete")
    assert response.status_code == 303
    assert not (photos_dir(data_dir) / name).exists()


# --------------------------------------------------------------------------- #
# Nur-Ansehen-Zugang (can_upload=false)
# --------------------------------------------------------------------------- #
def test_nurgucker_sieht_die_galerie_ohne_upload_karte(client: TestClient, app) -> None:
    with TestClient(app, follow_redirects=False) as amelie:
        login(amelie, "amelie", AMELIE_PW)
        name = upload_png(amelie).json()["results"][0]["name"]

    assert login(client, "nurgucker", NURGUCKER_PW).status_code == 303
    gallery = client.get("/")
    assert gallery.status_code == 200
    assert "Fotos hochladen" not in gallery.text
    assert 'id="upload-form"' not in gallery.text
    assert "+ Fotos hinzufügen" not in gallery.text
    assert "Schön, dass du reinschaust!" in gallery.text
    # Ansehen und Herunterladen bleiben erlaubt.
    assert name in gallery.text
    foto = client.get(f"/photos/{name}")
    assert foto.status_code == 200
    assert foto.content == PNG_BYTES


def test_nurgucker_darf_nicht_hochladen(client: TestClient, data_dir: Path) -> None:
    login(client, "nurgucker", NURGUCKER_PW)
    response = upload_png(client)
    assert response.status_code == 403
    assert response.json()["error"] == "Dieser Zugang kann Fotos nur ansehen."
    assert list(photos_dir(data_dir).iterdir()) == []


def test_nurgucker_darf_fremdes_foto_nicht_loeschen(
    client: TestClient, app, data_dir: Path
) -> None:
    with TestClient(app, follow_redirects=False) as amelie:
        login(amelie, "amelie", AMELIE_PW)
        name = upload_png(amelie).json()["results"][0]["name"]

    login(client, "nurgucker", NURGUCKER_PW)
    assert client.post(f"/photos/{name}/delete").status_code == 403
    assert (photos_dir(data_dir) / name).is_file()


def test_user_ohne_can_upload_feld_darf_weiterhin_hochladen(
    client: TestClient, users_file: Path
) -> None:
    assert "can_upload" not in json.loads(users_file.read_text("utf-8"))["amelie"]
    login(client, "amelie", AMELIE_PW)
    assert upload_png(client).status_code == 200


def test_galerie_zeigt_den_upload_button_fuer_berechtigte(client: TestClient) -> None:
    login(client, "amelie", AMELIE_PW)
    gallery = client.get("/")
    assert gallery.status_code == 200
    assert "+ Fotos hinzufügen" in gallery.text
    assert 'id="upload-fab"' in gallery.text
    assert 'id="upload-shell"' in gallery.text


# --------------------------------------------------------------------------- #
# Gäste-Zugang /gast (Passwortabfrage, gemeinsames Rate-Limit mit /login)
# --------------------------------------------------------------------------- #
def test_gast_seite_zeigt_nur_ein_passwortfeld(client: TestClient) -> None:
    response = client.get("/gast")
    assert response.status_code == 200
    assert 'action="/gast"' in response.text
    assert 'name="password"' in response.text
    assert 'name="username"' not in response.text
    # Das reine Aufrufen loggt niemanden ein.
    assert SESSION_COOKIE not in client.cookies


def test_gast_login_mit_richtigem_passwort(client: TestClient, data_dir: Path) -> None:
    response = client.post("/gast", data={"password": GAST_PW})
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE in client.cookies

    gallery = client.get("/")
    assert gallery.status_code == 200
    # Gäste dürfen jetzt ebenfalls hochladen.
    assert "+ Fotos hinzufügen" in gallery.text
    assert upload_png(client, "gastfoto.png").status_code == 200
    assert len(list(photos_dir(data_dir).glob("*.png"))) == 1


def test_gast_login_mit_falschem_passwort(client: TestClient) -> None:
    response = client.post("/gast", data={"password": "falsch"})
    assert response.status_code == 401
    assert SESSION_COOKIE not in client.cookies
    assert 'action="/gast"' in response.text


def test_gast_upload_landet_mit_uploader_gast_im_sidecar(
    client: TestClient, data_dir: Path
) -> None:
    client.post("/gast", data={"password": GAST_PW})
    name = upload_png(client, "Tanzflaeche.png").json()["results"][0]["name"]

    assert (photos_dir(data_dir) / name).is_file()
    sidecar = json.loads((photos_dir(data_dir) / (Path(name).stem + ".json")).read_text("utf-8"))
    assert sidecar["uploader"] == "gast"
    assert sidecar["display_name"] == "Gast"
    assert sidecar["original_name"] == "Tanzflaeche.png"


def test_gast_rate_limit_zaehlt_gemeinsam_mit_login(client: TestClient) -> None:
    # Drei Fehlversuche am normalen Login …
    for _ in range(3):
        assert login(client, "amelie", "falsch").status_code == 401
    # … und zwei am Gäste-Formular ergeben zusammen fünf.
    for _ in range(2):
        assert client.post("/gast", data={"password": "falsch"}).status_code == 401
    # Der sechste Versuch ist gesperrt — egal über welches Formular.
    assert client.post("/gast", data={"password": "falsch"}).status_code == 429
    assert client.post("/gast", data={"password": GAST_PW}).status_code == 429
    assert login(client, "amelie", AMELIE_PW).status_code == 429


def test_gast_seite_ohne_gast_benutzer_ist_404(
    tmp_path: Path, data_dir: Path, secret_file: Path
) -> None:
    ohne_gast = tmp_path / "ohne_gast.json"
    ohne_gast.write_text(
        json.dumps(
            {"amelie": {"display_name": "Amelie", **hash_password(AMELIE_PW, iterations=TEST_ITERATIONS)}}
        ),
        encoding="utf-8",
    )
    app = create_app(
        data_dir=data_dir, users_file=ohne_gast, secret_file=secret_file,
        max_bytes=2048, cookie_secure=False,
    )
    with TestClient(app, follow_redirects=False) as test_client:
        assert test_client.get("/gast").status_code == 404
        assert test_client.post("/gast", data={"password": "egal"}).status_code == 404
        assert SESSION_COOKIE not in test_client.cookies


def test_gast_login_ueber_das_normale_formular_geht_weiterhin(client: TestClient) -> None:
    assert login(client, "gast", GAST_PW).status_code == 303
    assert client.get("/").status_code == 200


def test_gast_seite_beruecksichtigt_den_root_path(app) -> None:
    with TestClient(app, follow_redirects=False, root_path="/hochzeit") as sub:
        page = sub.get("/gast")
        assert page.status_code == 200
        assert 'action="/hochzeit/gast"' in page.text
        assert sub.post("/gast", data={"password": GAST_PW}).headers["location"] == "/hochzeit/"


# --------------------------------------------------------------------------- #
# Thumbnails (/thumb) — on-demand mit Cache, graceful ohne Pillow
# --------------------------------------------------------------------------- #
def test_thumb_wird_erzeugt_gecacht_und_beim_zweiten_aufruf_wiederverwendet(
    client: TestClient, data_dir: Path
) -> None:
    pytest.importorskip("PIL")
    login(client, "amelie", AMELIE_PW)
    name = client.post(
        "/upload", files={"files": ("foto.jpg", make_jpeg_bytes(), "image/jpeg")}
    ).json()["results"][0]["name"]

    response = client.get(f"/thumb/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")

    cache = data_dir / "thumbs" / (Path(name).stem + ".jpg")
    assert cache.is_file()

    # Cache manipulieren: ein zweiter Aufruf darf NICHT neu erzeugen, sondern muss
    # exakt den (manipulierten) Cache-Inhalt ausliefern.
    cache.write_bytes(b"AUS-DEM-CACHE")
    zweite = client.get(f"/thumb/{name}")
    assert zweite.status_code == 200
    assert zweite.content == b"AUS-DEM-CACHE"


def test_thumb_ist_auch_fuer_ein_bild_ohne_harten_fehler_200(client: TestClient) -> None:
    # Auch für das winzige 1x1-PNG kommt ein 200 zurück (mit oder ohne Pillow).
    login(client, "amelie", AMELIE_PW)
    name = upload_png(client).json()["results"][0]["name"]
    assert client.get(f"/thumb/{name}").status_code == 200


def test_thumb_ohne_pillow_liefert_das_original(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(client, "amelie", AMELIE_PW)
    name = upload_png(client).json()["results"][0]["name"]
    # Pillow „ausschalten" → graceful Fallback auf das Original, kein Crash, kein Cache.
    monkeypatch.setattr("wedding.storage.PIL_AVAILABLE", False)
    response = client.get(f"/thumb/{name}")
    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert not (data_dir / "thumbs" / (Path(name).stem + ".jpg")).exists()


def test_thumb_bei_kaputtem_bild_faellt_auf_das_original_zurueck(client: TestClient) -> None:
    pytest.importorskip("PIL")
    login(client, "amelie", AMELIE_PW)
    kaputt = b"das ist kein gueltiges bild"
    name = client.post(
        "/upload", files={"files": ("kaputt.jpg", kaputt, "image/jpeg")}
    ).json()["results"][0]["name"]
    response = client.get(f"/thumb/{name}")
    assert response.status_code == 200
    assert response.content == kaputt


def test_thumb_nur_fuer_eingeloggte(client: TestClient) -> None:
    login(client, "amelie", AMELIE_PW)
    name = upload_png(client).json()["results"][0]["name"]
    client.post("/logout")
    assert client.get(f"/thumb/{name}").status_code == 303


def test_galerie_bilder_nutzen_die_thumb_route(client: TestClient) -> None:
    login(client, "amelie", AMELIE_PW)
    name = upload_png(client).json()["results"][0]["name"]
    gallery = client.get("/")
    # Das Raster-<img> zeigt auf /thumb, Download/Lightbox weiter auf /photos.
    assert f"/thumb/{name}" in gallery.text
    assert f'data-src="/photos/{name}"' in gallery.text


def test_delete_entfernt_auch_das_gecachte_thumbnail(
    client: TestClient, data_dir: Path
) -> None:
    pytest.importorskip("PIL")
    login(client, "amelie", AMELIE_PW)
    name = client.post(
        "/upload", files={"files": ("foto.jpg", make_jpeg_bytes(), "image/jpeg")}
    ).json()["results"][0]["name"]
    assert client.get(f"/thumb/{name}").status_code == 200
    cache = data_dir / "thumbs" / (Path(name).stem + ".jpg")
    assert cache.is_file()

    assert client.post(f"/photos/{name}/delete").status_code == 303
    assert not cache.exists()


# --------------------------------------------------------------------------- #
# Video-Upload & -Wiedergabe
# --------------------------------------------------------------------------- #
def test_video_upload_wird_in_der_galerie_als_video_markiert(
    client: TestClient, data_dir: Path
) -> None:
    login(client, "amelie", AMELIE_PW)
    response = client.post("/upload", files={"files": ("tanz.mp4", MP4_BYTES, "video/mp4")})
    assert response.status_code == 200
    name = response.json()["results"][0]["name"]
    assert name.endswith(".mp4")
    assert (photos_dir(data_dir) / name).is_file()

    gallery = client.get("/")
    assert 'data-kind="video"' in gallery.text
    assert "<video" in gallery.text
    assert f'data-content-type="video/mp4"' in gallery.text


def test_mov_wird_akzeptiert_aber_als_download_karte_gezeigt(client: TestClient) -> None:
    login(client, "amelie", AMELIE_PW)
    response = client.post("/upload", files={"files": ("omas_walzer.mov", MOV_BYTES, "video/quicktime")})
    assert response.status_code == 200
    name = response.json()["results"][0]["name"]
    assert name.endswith(".mov")

    gallery = client.get("/")
    # .mov ist nicht sicher inline abspielbar → Fallback-Karte, keine Vorschau.
    assert 'data-kind="video"' in gallery.text
    assert 'data-renderable="false"' in gallery.text
    assert "Video &ndash; per Download" in gallery.text


def test_video_und_bild_limit_sind_unabhaengig(
    data_dir: Path, users_file: Path, secret_file: Path
) -> None:
    # Bild-Limit 2048, Video-Limit 1024 (klein konfiguriert).
    app = create_app(
        data_dir=data_dir, users_file=users_file, secret_file=secret_file,
        max_bytes=2048, max_video_bytes=1024, cookie_secure=False,
    )
    with TestClient(app, follow_redirects=False) as client:
        login(client, "amelie", AMELIE_PW)

        # 1500-Byte-Bild → erlaubt (unter dem Bild-Limit von 2048).
        bild = client.post("/upload", files={"files": ("gross.jpg", b"x" * 1500, "image/jpeg")})
        assert bild.status_code == 200

        # Dasselbe Volumen als Video → abgelehnt (über dem Video-Limit von 1024).
        zu_gross = client.post("/upload", files={"files": ("clip.mp4", b"x" * 1500, "video/mp4")})
        assert zu_gross.status_code == 400
        assert zu_gross.json()["results"][0]["ok"] is False

        # Kleines Video unter dem Video-Limit → erlaubt.
        klein = client.post("/upload", files={"files": ("kurz.mp4", b"x" * 200, "video/mp4")})
        assert klein.status_code == 200


def test_bild_ueber_max_bytes_bleibt_abgelehnt(
    data_dir: Path, users_file: Path, secret_file: Path
) -> None:
    app = create_app(
        data_dir=data_dir, users_file=users_file, secret_file=secret_file,
        max_bytes=2048, max_video_bytes=1024, cookie_secure=False,
    )
    with TestClient(app, follow_redirects=False) as client:
        login(client, "amelie", AMELIE_PW)
        # 3000-Byte-Bild → über dem Bild-Limit → abgelehnt.
        response = client.post("/upload", files={"files": ("riesig.jpg", b"x" * 3000, "image/jpeg")})
        assert response.status_code == 400
        assert list(photos_dir(data_dir).iterdir()) == []


# --------------------------------------------------------------------------- #
# manage.py seed
# --------------------------------------------------------------------------- #
def test_seed_ergaenzt_nur_fehlende_benutzer(tmp_path: Path) -> None:
    quelle = tmp_path / "quelle.json"
    quelle.write_text(
        json.dumps(
            {
                "amelie": {"display_name": "Amelie (Repo)", "salt_hex": "aa", "hash_hex": "bb",
                           "iterations": 600000},
                "gast": {"display_name": "Gast", "can_upload": False, "salt_hex": "cc",
                         "hash_hex": "dd", "iterations": 600000},
            }
        ),
        encoding="utf-8",
    )
    bestehend = {
        "amelie": {"display_name": "Amelie", **hash_password("serverpasswort", iterations=TEST_ITERATIONS)}
    }
    ziel = tmp_path / "ziel.json"
    ziel.write_text(json.dumps(bestehend), encoding="utf-8")

    assert manage_main(["--file", str(ziel), "seed", str(quelle)]) == 0
    danach = json.loads(ziel.read_text("utf-8"))
    # Der bestehende Eintrag bleibt byte-identisch — das Server-Passwort überlebt.
    assert danach["amelie"] == bestehend["amelie"]
    assert danach["gast"]["can_upload"] is False
    assert danach["gast"]["hash_hex"] == "dd"

    # Zweiter Lauf ist ein No-op: die Datei wird nicht einmal neu geschrieben.
    unveraendert = ziel.read_bytes()
    assert manage_main(["--file", str(ziel), "seed", str(quelle)]) == 0
    assert ziel.read_bytes() == unveraendert


def test_set_upload_schaltet_hin_und_her(tmp_path: Path) -> None:
    original = {
        "gast": {"display_name": "Gast", **hash_password("pw", iterations=TEST_ITERATIONS)},
    }
    ziel = tmp_path / "users.json"
    ziel.write_text(json.dumps(original), encoding="utf-8")

    assert manage_main(["--file", str(ziel), "set-upload", "gast", "off"]) == 0
    entry = json.loads(ziel.read_text("utf-8"))["gast"]
    assert entry["can_upload"] is False

    assert manage_main(["--file", str(ziel), "set-upload", "gast", "on"]) == 0
    entry = json.loads(ziel.read_text("utf-8"))["gast"]
    assert entry["can_upload"] is True

    # Alle übrigen Felder sind unverändert geblieben.
    for feld, wert in original["gast"].items():
        assert entry[feld] == wert


def test_set_upload_kennt_nur_bestehende_benutzer(tmp_path: Path) -> None:
    ziel = tmp_path / "users.json"
    ziel.write_text(json.dumps({"amelie": {"display_name": "Amelie"}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        manage_main(["--file", str(ziel), "set-upload", "gibtsnicht", "on"])


def test_set_delete_schaltet_hin_und_her(tmp_path: Path) -> None:
    original = {
        "gast": {"display_name": "Gast", **hash_password("pw", iterations=TEST_ITERATIONS)},
    }
    ziel = tmp_path / "users.json"
    ziel.write_text(json.dumps(original), encoding="utf-8")

    assert manage_main(["--file", str(ziel), "set-delete", "gast", "off"]) == 0
    entry = json.loads(ziel.read_text("utf-8"))["gast"]
    assert entry["can_delete"] is False

    assert manage_main(["--file", str(ziel), "set-delete", "gast", "on"]) == 0
    entry = json.loads(ziel.read_text("utf-8"))["gast"]
    assert entry["can_delete"] is True

    # Alle übrigen Felder sind unverändert geblieben.
    for feld, wert in original["gast"].items():
        assert entry[feld] == wert


def test_set_delete_kennt_nur_bestehende_benutzer(tmp_path: Path) -> None:
    ziel = tmp_path / "users.json"
    ziel.write_text(json.dumps({"amelie": {"display_name": "Amelie"}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        manage_main(["--file", str(ziel), "set-delete", "gibtsnicht", "off"])


def test_seed_legt_datei_an_wenn_sie_fehlt(tmp_path: Path) -> None:
    quelle = tmp_path / "quelle.json"
    quelle.write_text(json.dumps({"gast": {"display_name": "Gast", "can_upload": False}}), "utf-8")
    ziel = tmp_path / "neu" / "users.json"

    assert manage_main(["--file", str(ziel), "seed", str(quelle)]) == 0
    assert json.loads(ziel.read_text("utf-8"))["gast"]["can_upload"] is False


# --------------------------------------------------------------------------- #
# manage.py save(): atomarer Schreibvorgang erhält Modus + Owner/Gruppe
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(os.name != "posix", reason="POSIX-Modi sind auf Windows unzuverlässig.")
def test_save_erhaelt_den_modus_der_bestehenden_datei(tmp_path: Path) -> None:
    ziel = tmp_path / "users.json"
    ziel.write_text(json.dumps({"amelie": {"display_name": "Amelie"}}), encoding="utf-8")
    ziel.chmod(0o600)

    manage_save(ziel, {"amelie": {"display_name": "Amelie neu"}})

    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600
    assert json.loads(ziel.read_text("utf-8"))["amelie"]["display_name"] == "Amelie neu"


def test_save_als_nichtroot_wirft_nicht_und_schreibt_inhalt(tmp_path: Path) -> None:
    # Realer Dev-/CI-Fall: die Datei gehört dem aktuellen (Nicht-root-)User, ein
    # echtes chown auf einen fremden Owner scheiterte an fehlenden Rechten — save()
    # darf deshalb nicht werfen und muss den Inhalt trotzdem korrekt schreiben.
    ziel = tmp_path / "users.json"
    ziel.write_text(json.dumps({"gast": {"display_name": "Gast"}}), encoding="utf-8")

    manage_save(ziel, {"gast": {"display_name": "Gast", "can_upload": True}})

    danach = json.loads(ziel.read_text("utf-8"))
    assert danach["gast"]["can_upload"] is True
    # Keine tmp-Leiche zurückgelassen.
    assert not ziel.with_name(ziel.name + ".tmp").exists()


def test_save_chownt_tmp_auf_bestehenden_owner_und_schluckt_permissionerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ziel = tmp_path / "users.json"
    ziel.write_text(json.dumps({"gast": {"display_name": "Gast"}}), encoding="utf-8")
    st = ziel.stat()

    aufrufe: list[tuple[str, int, int]] = []

    def fake_chown(pfad, uid, gid):  # noqa: ANN001
        aufrufe.append((os.fspath(pfad), uid, gid))
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr("wedding.manage.os.chown", fake_chown, raising=False)

    # Trotz der geworfenen PermissionError gelingt der Schreibvorgang.
    manage_save(ziel, {"gast": {"display_name": "Gast", "can_upload": False}})

    assert len(aufrufe) == 1
    _, uid, gid = aufrufe[0]
    assert (uid, gid) == (st.st_uid, st.st_gid)
    assert json.loads(ziel.read_text("utf-8"))["gast"]["can_upload"] is False
    assert not ziel.with_name(ziel.name + ".tmp").exists()


# --------------------------------------------------------------------------- #
# Betrieb unter einem Unterpfad (Caddy: /hochzeit)
# --------------------------------------------------------------------------- #
def test_links_beruecksichtigen_den_root_path(app) -> None:
    with TestClient(app, follow_redirects=False, root_path="/hochzeit") as sub:
        login_page = sub.get("/login")
        assert login_page.status_code == 200
        assert 'action="/hochzeit/login"' in login_page.text
        assert "/hochzeit/static/style.css" in login_page.text

        assert sub.get("/").headers["location"] == "/hochzeit/login"
        assert login(sub, "amelie", AMELIE_PW).headers["location"] == "/hochzeit/"
