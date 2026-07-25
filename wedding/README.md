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

### Repo unter `/root` oder `/home`?

Der Dienst läuft als unprivilegierter Systemuser `wedding`. Liegt das Repo unter `/root`
(Standardrechte 0700) oder in einem Home-Verzeichnis, kommt dieser User dort nicht heran —
zusätzlich blockt `ProtectHome=true` in der Unit den Zugriff auf `/home` und `/root`.

`setup_wedding.sh` erkennt das selbst (Lesetest auf `wedding/run.py` und das venv als User
`wedding`) und schaltet dann automatisch auf einen **Fallback** um:

* Der App-Code wird nach **`/opt/wedding/app/wedding/`** kopiert (`cp -a`, Paket-Layout
  bleibt erhalten, kein `rsync` nötig).
* Unter **`/opt/wedding/venv`** entsteht ein eigenes Mini-venv mit nur den vier benötigten
  Paketen (fastapi, uvicorn, jinja2, python-multipart). Beim Re-Run ist die Installation ein
  schneller No-op.
* Die Unit bekommt `WorkingDirectory=/opt/wedding/app` und startet
  `/opt/wedding/venv/bin/python /opt/wedding/app/wedding/run.py`. Weil der Code damit unter
  `/opt` liegt, ist `ProtectHome=true` unkritisch und bleibt aktiv.
* Das Skript sagt am Ende deutlich, dass der Fallback aktiv ist.

**Wichtig:** In diesem Modus läuft der Dienst aus einer *Kopie*. Nach **jedem** `git pull`
also erneut `bash deploy/setup_wedding.sh` ausführen — das aktualisiert die Kopie unter
`/opt/wedding/app` und startet den Dienst neu. Ohne den zweiten Schritt läuft die Galerie
weiter mit dem alten Code.

Sobald das Repo an einem für `wedding` lesbaren Ort liegt (z. B. nach der Migration nach
`/opt/stockbot`), wählt derselbe Aufruf wieder den Direktbetrieb aus dem Repo — es ist
nichts von Hand zurückzubauen.

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

# Nur-Ansehen-Zugang anlegen (kein Upload, kein Löschen)
/opt/stockbot/venv/bin/python /opt/stockbot/wedding/manage.py \
    --file /etc/wedding/users.json add-user schwiegermutter "Renate" --guest

# Fehlende Benutzer aus dem Repo nachziehen — bestehende bleiben unberührt
/opt/stockbot/venv/bin/python /opt/stockbot/wedding/manage.py \
    --file /etc/wedding/users.json seed /opt/stockbot/deploy/wedding-users.json

systemctl restart wedding   # nicht nötig, die users.json wird pro Login gelesen
```

Ohne `--file` nimmt das CLI `WEDDING_USERS_FILE`, sonst `./data/wedding/users.json`.
Geschrieben wird atomar (tmp + rename), die Dateirechte bleiben erhalten.

`seed` ist der Weg, einen **neuen** Zugang auf einen bereits laufenden Server zu bringen:
es fügt ausschließlich Benutzer ein, die im Ziel noch fehlen, und fasst bestehende Einträge
(inklusive der dort gesetzten Passwörter) nie an. Ein zweiter Lauf ist ein No-op.
`setup_wedding.sh` ruft das automatisch auf, wenn `/etc/wedding/users.json` schon existiert.

## Gast-Zugang (nur ansehen)

Ein Benutzer mit `"can_upload": false` in der users.json darf die Galerie ansehen und Fotos
herunterladen, aber **nicht hochladen und nicht löschen**. Das Feld ist optional — fehlt es,
gilt `true`, bestehende Einträge funktionieren also unverändert. Erzwungen wird das
serverseitig (`POST /upload` und `POST /photos/…/delete` antworten mit `403`), nicht bloß
durch Ausblenden im UI; die Upload-Karte wird für solche Zugänge zusätzlich gar nicht erst
gerendert.

Ausgeliefert wird der Benutzer `gast` (Anzeigename „Gast"). Für ihn gibt es einen
**Direktlink ohne Anmeldemaske**:

* mit Domain: `https://<DOMAIN>/hochzeit/gast`
* ohne Domain: `http://<server-ip>:8100/gast`

Der Aufruf setzt direkt die Gast-Session und landet in der Galerie. **Wer den Link hat, kann
die Fotos sehen** — der Link selbst ist das Zugangsgeheimnis, also nur an Leute schicken, die
die Bilder sehen dürfen. Der normale Login mit `gast` + Passwort funktioniert zusätzlich
weiterhin. Gibt es keinen `gast`-Eintrag in der users.json, liefert `/gast` ein 404.

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
