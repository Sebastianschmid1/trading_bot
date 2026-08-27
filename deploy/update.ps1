<#
  Server-Update von Windows aus (PowerShell) - NUR Server, KEIN Upload:
    -> ssh zum Server -> git pull && Dependencies + Caddy + Dienst neu starten

  Im Gegensatz zu upload.ps1 wird hier NICHT lokal committet/gepusht. Nutze dieses Skript,
  wenn die Aenderungen bereits auf GitHub liegen (z. B. ueber einen Merge in main) und du nur
  den Server auf den neuesten Stand bringen + neu starten willst.

  Passphrase-Handling identisch zu upload.ps1: zwei passphrase-geschuetzte Keys
    - lokaler Key  (C:\Users\<du>\.ssh\id_ed25519)  -> Verbindung zum Server
    - Server-Key   (/root/.ssh/id_ed25519)          -> 'git pull' von GitHub
  Beide nutzen dieselbe Passphrase = LOCAL_PASSWORD aus .env.

  Lauf (im Projektordner):
      .\update.ps1
  Falls "running scripts is disabled": einmalig
      Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
$ErrorActionPreference = "Stop"
$Server = "root@217.160.103.25"
$AppDir = "stockbot"
# Das Skript liegt seit dem Aufraeumen in deploy/, arbeitet aber weiterhin im
# Repo-Wurzelverzeichnis (dort liegen .env und das Git-Arbeitsverzeichnis).
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

# --- .env -> LOCAL_PASSWORD (Wert wird nie ausgegeben) ----------------------
$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) { Write-Error ".env nicht gefunden in $RepoRoot"; exit 1 }
$pw = $null
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*LOCAL_PASSWORD\s*=\s*(.*)$') { $pw = $matches[1].Trim().Trim('"').Trim("'") }
}
if ([string]::IsNullOrWhiteSpace($pw)) { Write-Error "LOCAL_PASSWORD fehlt in .env"; exit 1 }
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    Write-Error "ssh.exe fehlt - OpenSSH-Client aktivieren (Einstellungen > Apps > Optionale Features)."
    exit 1
}

# --- lokaler Askpass-Helfer (Passphrase des LOKALEN Keys beim Verbinden) ----
# Datei enthaelt KEIN Secret - liest die Passphrase aus der Umgebungsvariable DEPLOY_PW.
$askpass = Join-Path $env:TEMP ("askpass_{0}.cmd" -f $PID)
@'
@echo off
powershell -NoProfile -Command "[Console]::Out.Write($env:DEPLOY_PW)"
'@ | Out-File -FilePath $askpass -Encoding ascii
$env:DEPLOY_PW = $pw
$env:SSH_ASKPASS = $askpass
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "localhost:0"

try {
    # --- Server: pullen + Dienste neu starten (Passphrase fuer BEIDE Keys automatisch) ---
    Write-Host "-> aktualisiere $Server (git pull + restart) ..." -ForegroundColor Cyan
    $passB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pw))
    # Server-seitiges Bash-Kommando (Platzhalter werden literal ersetzt):
    # git pull -> pip install -> Caddy/TLS synchronisieren -> stockbot (und, falls aktiv, dashboard)
    # neu starten -> Status zeigen und 30s Live-Logs anhaengen.
    $tmpl = 'cd {APP} && ASK=$(mktemp) && printf ''#!/bin/sh\nprintf %%s "$DEPLOY_PASS"\n'' > "$ASK" && chmod +x "$ASK" && DEPLOY_PASS="$(printf %s ''{PASS}'' | base64 -d)" SSH_ASKPASS="$ASK" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 git pull < /dev/null; rc=$?; rm -f "$ASK"; [ $rc -eq 0 ] && { venv/bin/pip install -q -r requirements.lock || echo "WARN: pip install fehlgeschlagen"; bash deploy/sync_caddy.sh || echo "WARN: caddy-sync fehlgeschlagen"; systemctl restart stockbot; systemctl is-active --quiet dashboard && systemctl restart dashboard; systemctl status stockbot --no-pager -l | head -n 15; echo "==== stockbot Logs (30s) ===="; timeout 30 journalctl -u stockbot -n 20 -f || true; }; exit $rc'
    $bash = $tmpl.Replace('{APP}', $AppDir).Replace('{PASS}', $passB64)
    # Ganzes Kommando base64-verpacken -> quote-freie ssh-Argumentzeile.
    $cmdB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
    # Der Server relait git-pull-/systemctl-Ausgabe nach stderr; unter Stop wuerde PowerShell
    # das als Fehler werten. Daher fuer den ssh-Aufruf auf Continue und Erfolg ueber Exitcode.
    $ErrorActionPreference = "Continue"
    $null | ssh -o StrictHostKeyChecking=accept-new $Server "echo $cmdB64 | base64 -d | bash"
    $sshRc = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($sshRc -ne 0) { throw "Update (ssh) fehlgeschlagen" }

    Write-Host "OK - Server aktualisiert und neu gestartet." -ForegroundColor Green
}
finally {
    Remove-Item $askpass -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\DEPLOY_PW -ErrorAction SilentlyContinue
    Remove-Item Env:\SSH_ASKPASS -ErrorAction SilentlyContinue
    Remove-Item Env:\SSH_ASKPASS_REQUIRE -ErrorAction SilentlyContinue
    Remove-Item Env:\DISPLAY -ErrorAction SilentlyContinue
}
