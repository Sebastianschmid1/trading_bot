<#
  Upload/Deploy von Windows aus (PowerShell):
    1) git commit (nur bei Aenderungen) + push
    2) SSH zum Server -> cd stockbot && git pull && systemctl restart stockbot

  Die Passphrase/das Passwort kommt aus .env (LOCAL_PASSWORD) und wird ueber den
  eingebauten OpenSSH-Client (ssh.exe, in Windows 10/11 enthalten) per SSH_ASKPASS
  uebergeben - es ist KEIN sshpass/PuTTY noetig. Funktioniert fuer Key-Passphrase
  ebenso wie fuer Server-Passwort-Auth.

  Lauf (im Projektordner):
      powershell -ExecutionPolicy Bypass -File .\upload.ps1 "deine commit message"
  oder einfach:
      .\upload.ps1 "deine commit message"

  Falls "running scripts is disabled": einmalig
      Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param([string]$Message = "deploy $(Get-Date -Format 'yyyy-MM-dd HH:mm')")

$ErrorActionPreference = "Stop"
$Server = "root@217.160.103.25"
$AppDir = "stockbot"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# --- .env -> LOCAL_PASSWORD (Wert wird nie ausgegeben) ----------------------
$envFile = Join-Path $ScriptDir ".env"
if (-not (Test-Path $envFile)) { Write-Error ".env nicht gefunden in $ScriptDir"; exit 1 }
$pw = $null
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*LOCAL_PASSWORD\s*=\s*(.*)$') {
        $pw = $matches[1].Trim().Trim('"').Trim("'")
    }
}
if ([string]::IsNullOrWhiteSpace($pw)) { Write-Error "LOCAL_PASSWORD fehlt in .env"; exit 1 }

if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    Write-Error "ssh.exe nicht gefunden. OpenSSH-Client aktivieren: Einstellungen > Apps > Optionale Features > 'OpenSSH-Client'."
    exit 1
}

# --- askpass-Helfer: gibt die Passphrase aus (liest sie aus DEPLOY_PW). -----
# Die .cmd-Datei enthaelt KEIN Secret - nur einen Verweis auf die Umgebungsvariable.
# Ausgabe per PowerShell ohne Zeilenumbruch -> robust auch bei Sonderzeichen im Passwort.
$askpass = Join-Path $env:TEMP ("askpass_{0}.cmd" -f $PID)
@'
@echo off
powershell -NoProfile -Command "[Console]::Out.Write($env:DEPLOY_PW)"
'@ | Out-File -FilePath $askpass -Encoding ascii
$env:DEPLOY_PW = $pw
$env:SSH_ASKPASS = $askpass
$env:SSH_ASKPASS_REQUIRE = "force"   # ssh nutzt askpass auch ohne separates Fenster
$env:DISPLAY = "localhost:0"         # noetig, damit aeltere ssh-Builds askpass aufrufen

try {
    # --- 1) commit (nur bei Aenderungen) + push ---------------------------
    if (git status --porcelain) {
        Write-Host "-> committe lokale Aenderungen ..."
        git add -A
        git commit -m $Message
    } else {
        Write-Host "i  keine lokalen Aenderungen - ueberspringe commit."
    }
    Write-Host "-> push ..."
    git push
    if ($LASTEXITCODE -ne 0) { throw "git push fehlgeschlagen" }

    # --- 2) Server: pullen + Dienst neu starten ---------------------------
    Write-Host "-> deploye auf $Server ..."
    $remote = "cd $AppDir && git pull && systemctl restart stockbot && systemctl status stockbot --no-pager -l | head -n 15"
    # leeres stdin -> ssh hat kein TTY -> nutzt SSH_ASKPASS fuer die Passphrase
    $null | ssh -o StrictHostKeyChecking=accept-new $Server $remote
    if ($LASTEXITCODE -ne 0) { throw "Deploy (ssh) fehlgeschlagen" }

    Write-Host "OK - gepusht und auf dem Server neu gestartet."
}
finally {
    Remove-Item $askpass -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\DEPLOY_PW -ErrorAction SilentlyContinue
    Remove-Item Env:\SSH_ASKPASS -ErrorAction SilentlyContinue
    Remove-Item Env:\SSH_ASKPASS_REQUIRE -ErrorAction SilentlyContinue
    Remove-Item Env:\DISPLAY -ErrorAction SilentlyContinue
}
