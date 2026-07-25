# Hochzeits-Foto-Galerie „Amelie & Tobi" 💍

Kleine, eigenständige FastAPI-App: Gäste melden sich an, laden Fotos vom Handy hoch und
sehen die gemeinsame Galerie (Lightbox, Download, eigene Fotos löschen).

Die App hat **nichts** mit dem stockbot zu tun — sie liegt nur im selben Repo, damit ein
einziges `git pull` reicht. Auf dem Server läuft sie als **getrennter** systemd-Dienst
`wedding` unter einem eigenen Systemuser mit eigenem Datenverzeichnis. Keine zusätzlichen
Abhängigkeiten (fastapi, uvicorn, jinja2, python-multipart sind bereits in
`requirements.lock`), kein CDN, keine Webfonts.

## Deploy

Auf dem Server als root:

```bash
cd /opt/stockbot
git pull
bash deploy/setup_wedding.sh
```

Das Skript ist idempotent und legt an bzw. aktualisiert:

| Pfad | Inhalt |
| --- | --- |
| `/var/lib/wedding/photos` | die hochgeladenen Fotos + `<uuid>.json`-Metadaten |
| `/etc/wedding/users.json` | Benutzer + PBKDF2-Hashes (wird **nie** überschrieben) |
| `/etc/wedding/secret` | Session-Secret (`openssl rand -hex 32`) |
| `/etc/wedding/wedding.env` | Konfiguration des Dienstes |
| `/etc/systemd/system/wedding.service` | die Unit (Pfade auf den echten Repo-Ort gesetzt) |

Beim ersten Lauf werden die Benutzer `amelie` und `tobi` aus `deploy/wedding-users.json`
übernommen. Spätere Passwortänderungen bleiben erhalten, weil die Datei nur angelegt wird,
wenn sie fehlt.

## URL-Schema

* **Mit `DOMAIN` in der `.env`** (Regelfall): Caddy terminiert TLS und leitet
  `/hochzeit/*` an `127.0.0.1:8100` weiter → **`https://<DOMAIN>/hochzeit/`**.
  Der Dienst lauscht nur auf localhost, Cookies sind `Secure`.
* **Ohne `DOMAIN`**: der Dienst lauscht auf `0.0.0.0:8100` → **`http://<server-ip>:8100/`**
  (Port wird bei aktiver ufw freigegeben, Cookies ohne `Secure`).

Der Unterpfad kommt über `WEDDING_ROOT_PATH` in die App; alle Links, Formulare und
Fetch-URLs werden damit präfixt.

## Konfiguration (Env-Vars)

| Variable | Default | Bedeutung |
| --- | --- | --- |
| `WEDDING_BIND` | `127.0.0.1` | Listen-Adresse |
| `WEDDING_PORT` | `8100` | Listen-Port |
| `WEDDING_ROOT_PATH` | *(leer)* | Unterpfad hinter dem Reverse-Proxy, z. B. `/hochzeit` |
| `WEDDING_DATA_DIR` | `./data/wedding` | Datenverzeichnis (enthält `photos/`) |
| `WEDDING_USERS_FILE` | `<DATA_DIR>/users.json` | Benutzerdatei |
| `WEDDING_SECRET_FILE` | `<DATA_DIR>/secret` | Session-Secret (wird lokal erzeugt, falls es fehlt) |
| `WEDDING_COOKIE_SECURE` | `false` | `Secure`-Flag am Session-Cookie |
| `WEDDING_MAX_BYTES` | `31457280` (30 MB) | Maximale Größe **pro Datei** |

Lokal starten: `python wedding/run.py` → <http://127.0.0.1:8100/>.

## Benutzer verwalten

```bash
# Passwort ändern (fragt interaktiv, oder WEDDING_NEW_PASSWORD=... setzen)
/opt/stockbot/venv/bin/python /opt/stockbot/wedding/manage.py \
    --file /etc/wedding/users.json set-password amelie

# Weiteren Gast anlegen
/opt/stockbot/venv/bin/python /opt/stockbot/wedding/manage.py \
    --file /etc/wedding/users.json add-user oma "Oma Erna"

systemctl restart wedding   # nicht nötig, die users.json wird pro Login gelesen
```

Ohne `--file` nimmt das CLI `WEDDING_USERS_FILE`, sonst `./data/wedding/users.json`.
Geschrieben wird atomar (tmp + rename), die Dateirechte bleiben erhalten.

## Daten & Backup

Alle Fotos liegen unter **`/var/lib/wedding/photos`** — pro Foto die Bilddatei
`<uuid>.<ext>` plus ein Sidecar `<uuid>.json` mit Uploader, Originalname und Zeitstempel.
Es gibt keine Datenbank; ein Backup ist einfach ein Kopieren des Verzeichnisses:

```bash
tar czf /root/wedding-fotos-$(date +%F).tar.gz -C /var/lib/wedding photos
```

Vor jedem größeren Eingriff (oder nach der Feier) einmal sichern — die Bilder sind
unwiederbringlich, wenn der Server verloren geht. `/etc/wedding/` (users.json + secret)
gehört ebenfalls ins Backup, sonst müssen alle Passwörter neu gesetzt werden.

## Sicherheit in Kurzform

* Passwörter: PBKDF2-HMAC-SHA256, 600 000 Runden, Vergleich in konstanter Zeit.
* Session: HMAC-signiertes Cookie (`HttpOnly`, `SameSite=Lax`, 30 Tage), kein Server-State.
* Login-Rate-Limit: max. 5 Fehlversuche pro Minute und IP, danach HTTP 429.
* Uploads: Endungs-Whitelist, Größenlimit pro Datei, max. 30 Dateien pro Request; der
  Server-Dateiname wird immer neu erzeugt (`uuid4`), der Originalname landet nur in den
  Metadaten. Ausgeliefert wird nur, was dem Muster `^[a-f0-9]{32}\.[a-z]+$` entspricht.
* Ohne gültige Session ist außer `/login` und `/healthz` nichts erreichbar.
