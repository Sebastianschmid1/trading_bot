<#
  Upload/Deploy von Windows aus (PowerShell) - VOLL AUTOMATISCH (kein Tippen):
    1) git commit (nur bei Aenderungen) + push
    2) ssh zum Server -> git pull && systemctl restart stockbot

  Es sind ZWEI passphrase-geschuetzte Keys im Spiel:
    - lokaler Key  (C:\Users\<du>\.ssh\id_ed25519)  -> Verbindung zum Server
    - Server-Key   (/root/.ssh/id_ed25519)          -> 'git pull' von GitHub
  Beide nutzen dieselbe Passphrase = LOCAL_PASSWORD aus .env.

  Automatisierung ueber SSH_ASKPASS:
    - lokal: ssh nutzt einen kleinen Askpass-Helfer (gibt die Passphrase aus).
    - serverseitig: das Remote-Kommando legt einen temporaeren Askpass-Helfer an,
      uebergibt die Passphrase (base64-verpackt ueber den verschluesselten SSH-Kanal),
      laesst 'git pull' ohne TTY laufen (SSH_ASKPASS_REQUIRE=force) und raeumt wieder auf.

  Lauf (im Projektordner):
      .\upload.ps1 "deine commit message"
  Falls "running scripts is disabled": einmalig
      Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param(
    [string]$Message = "deploy $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
    [string]$AppDir = "/opt/stockbot" # PLAT-008: Zielpfad des unprivilegierten Dienstes
)

$ErrorActionPreference = "Stop"
$Server = "root@217.160.103.25"
Set-Location -LiteralPath $PSScriptRoot

# --- .env -> LOCAL_PASSWORD (Wert wird nie ausgegeben) ----------------------
$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) { Write-Error ".env nicht gefunden in $PSScriptRoot"; exit 1 }
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
    # --- 1) commit (nur bei Aenderungen) + push ---------------------------
    # git schreibt normale Hinweise (z. B. LF/CRLF) und Push-Fortschritt nach stderr. Unter
    # ErrorActionPreference=Stop wuerde PowerShell (5.1) das faelschlich als Fehler werten.
    # Daher NUR fuer die git-Aufrufe temporaer auf "Continue" und Erfolg ueber $LASTEXITCODE.
    $ErrorActionPreference = "Continue"
    if (git status --porcelain) {
        Write-Host "-> committe lokale Aenderungen ..." -ForegroundColor Cyan
        git add -A
        git commit -m $Message
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = "Stop"; throw "git commit fehlgeschlagen" }
    } else {
        Write-Host "i  keine lokalen Aenderungen - ueberspringe commit."
    }
    Write-Host "-> push ..." -ForegroundColor Cyan
    git push
    $pushRc = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($pushRc -ne 0) { throw "git push fehlgeschlagen" }

    # --- 2) Deploy: Passphrase fuer BEIDE Keys automatisch ----------------
    Write-Host "-> deploye auf $Server (automatisch) ..." -ForegroundColor Cyan
    $passB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pw))
    # Server-seitiges Bash-Kommando (Platzhalter werden literal ersetzt):
    # PLAT-008: nach git pull Ownership auf stockbot setzen und Dependencies als stockbot
    # installieren; TLS/Caddy synchronisieren, neu starten, Status und Logs zeigen.
    $tmpl = 'cd {APP} && ASK=$(mktemp) && printf ''#!/bin/sh\nprintf %%s "$DEPLOY_PASS"\n'' > "$ASK" && chmod +x "$ASK" && DEPLOY_PASS="$(printf %s ''{PASS}'' | base64 -d)" SSH_ASKPASS="$ASK" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 git -c safe.directory={APP} pull < /dev/null; rc=$?; rm -f "$ASK"; [ $rc -eq 0 ] && { chown -R stockbot:stockbot {APP}; runuser -u stockbot -- {APP}/venv/bin/pip install -q -r {APP}/requirements.txt || echo "WARN: pip install fehlgeschlagen"; bash deploy/sync_caddy.sh || echo "WARN: caddy-sync fehlgeschlagen"; systemctl restart stockbot; systemctl status stockbot --no-pager -l | head -n 15; echo "==== stockbot Logs (30s) ===="; timeout 30 journalctl -u stockbot -n 20 -f || true; }; exit $rc'
    $bash = $tmpl.Replace('{APP}', $AppDir).Replace('{PASS}', $passB64)
    # Ganzes Kommando base64-verpacken -> quote-freie ssh-Argumentzeile
    # (umgeht PowerShells fehleranfaelliges Native-Arg-Quoting bei Anfuehrungszeichen).
    $cmdB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
    # Der Server relait git-pull-/systemctl-Ausgabe nach stderr; unter Stop wuerde PowerShell
    # das als Fehler werten. Daher fuer den ssh-Aufruf auf Continue und Erfolg ueber Exitcode.
    $ErrorActionPreference = "Continue"
    $null | ssh -o StrictHostKeyChecking=accept-new $Server "echo $cmdB64 | base64 -d | bash"
    $sshRc = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($sshRc -ne 0) { throw "Deploy (ssh) fehlgeschlagen" }

    Write-Host "OK - gepusht und auf dem Server neu gestartet." -ForegroundColor Green
}
finally {
    Remove-Item $askpass -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\DEPLOY_PW -ErrorAction SilentlyContinue
    Remove-Item Env:\SSH_ASKPASS -ErrorAction SilentlyContinue
    Remove-Item Env:\SSH_ASKPASS_REQUIRE -ErrorAction SilentlyContinue
    Remove-Item Env:\DISPLAY -ErrorAction SilentlyContinue
}
