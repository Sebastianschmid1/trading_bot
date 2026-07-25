"""Tests der eigenständigen Hochzeits-Foto-Galerie (`wedding/`).

Alles läuft gegen eine frische App-Instanz auf `tmp_path`: eigene users.json mit
selbst erzeugten Hashes (bewusst NICHT die echten Einträge aus
`deploy/wedding-users.json`), eigenes Secret, eigenes Datenverzeichnis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wedding.app import create_app
from wedding.auth import SESSION_COOKIE, hash_password, make_session_cookie
from wedding.manage import main as manage_main

# Kleine Rundenzahl: die Tests loggen sich oft ein, 600k Runden wären reine Wartezeit.
TEST_ITERATIONS = 1_000

# Frei erfundene Test-Passwörter — sie haben nichts mit den echten Zugängen zu tun.
AMELIE_PW = "brautstrauss"
TOBI_PW = "trauzeuge"
GAST_PW = "nurgucken"

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
    # amelie/tobi bewusst OHNE `can_upload` — das prüft die Abwärtskompatibilität mit,
    # `gast` ist der Nur-Ansehen-Zugang.
    users = {
        "amelie": {"display_name": "Amelie", **hash_password(AMELIE_PW, iterations=TEST_ITERATIONS)},
        "tobi": {"display_name": "Tobi", **hash_password(TOBI_PW, iterations=TEST_ITERATIONS)},
        "gast": {
            "display_name": "Gast",
            "can_upload": False,
            **hash_password(GAST_PW, iterations=TEST_ITERATIONS),
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
# Nur-Ansehen-Zugang „Gast" (can_upload=false)
# --------------------------------------------------------------------------- #
def test_gast_sieht_die_galerie_ohne_upload_karte(client: TestClient, app) -> None:
    with TestClient(app, follow_redirects=False) as amelie:
        login(amelie, "amelie", AMELIE_PW)
        name = upload_png(amelie).json()["results"][0]["name"]

    assert login(client, "gast", GAST_PW).status_code == 303
    gallery = client.get("/")
    assert gallery.status_code == 200
    assert "Fotos hochladen" not in gallery.text
    assert 'id="upload-form"' not in gallery.text
    assert "Schön, dass du reinschaust!" in gallery.text
    # Ansehen und Herunterladen bleiben erlaubt.
    assert name in gallery.text
    foto = client.get(f"/photos/{name}")
    assert foto.status_code == 200
    assert foto.content == PNG_BYTES


def test_gast_darf_nicht_hochladen(client: TestClient, data_dir: Path) -> None:
    login(client, "gast", GAST_PW)
    response = upload_png(client)
    assert response.status_code == 403
    assert response.json()["error"] == "Dieser Zugang kann Fotos nur ansehen."
    assert list(photos_dir(data_dir).iterdir()) == []


def test_gast_darf_fremdes_foto_nicht_loeschen(client: TestClient, app, data_dir: Path) -> None:
    with TestClient(app, follow_redirects=False) as amelie:
        login(amelie, "amelie", AMELIE_PW)
        name = upload_png(amelie).json()["results"][0]["name"]

    login(client, "gast", GAST_PW)
    assert client.post(f"/photos/{name}/delete").status_code == 403
    assert (photos_dir(data_dir) / name).is_file()


def test_user_ohne_can_upload_feld_darf_weiterhin_hochladen(
    client: TestClient, users_file: Path
) -> None:
    assert "can_upload" not in json.loads(users_file.read_text("utf-8"))["amelie"]
    login(client, "amelie", AMELIE_PW)
    assert upload_png(client).status_code == 200


# --------------------------------------------------------------------------- #
# Direktlink /gast
# --------------------------------------------------------------------------- #
def test_gast_direktlink_loggt_ohne_formular_ein(client: TestClient, data_dir: Path) -> None:
    response = client.get("/gast")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE in client.cookies

    gallery = client.get("/")
    assert gallery.status_code == 200
    assert "Schön, dass du reinschaust!" in gallery.text
    assert upload_png(client).status_code == 403
    assert list(photos_dir(data_dir).iterdir()) == []


def test_gast_direktlink_ohne_gast_benutzer_ist_404(
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
        response = test_client.get("/gast")
        assert response.status_code == 404
        assert SESSION_COOKIE not in test_client.cookies


def test_gast_direktlink_beruecksichtigt_den_root_path(app) -> None:
    with TestClient(app, follow_redirects=False, root_path="/hochzeit") as sub:
        response = sub.get("/gast")
        assert response.status_code == 303
        assert response.headers["location"] == "/hochzeit/"


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


def test_seed_legt_datei_an_wenn_sie_fehlt(tmp_path: Path) -> None:
    quelle = tmp_path / "quelle.json"
    quelle.write_text(json.dumps({"gast": {"display_name": "Gast", "can_upload": False}}), "utf-8")
    ziel = tmp_path / "neu" / "users.json"

    assert manage_main(["--file", str(ziel), "seed", str(quelle)]) == 0
    assert json.loads(ziel.read_text("utf-8"))["gast"]["can_upload"] is False


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
