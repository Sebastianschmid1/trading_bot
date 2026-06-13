<#
  Upload/Deploy von Windows aus (PowerShell) - interaktiv:
    1) git commit (nur bei Aenderungen) + push
    2) ssh -t zum Server -> cd stockbot && git pull && systemctl restart stockbot

  Die Key-Passphrase wird bei jedem Schritt ganz normal abgefragt - genau wie beim
  manuellen ssh: einmal fuer die Verbindung zum Server, einmal fuer 'git pull' auf
  dem Server (dessen GitHub-Key ist ebenfalls passphrase-geschuetzt).

  Lauf (im Projektordner):
      .\upload.ps1 "deine commit message"
  Falls "running scripts is disabled": einmalig
      Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

  Tipp gegen mehrfaches Tippen: lokalen Key einmalig in den ssh-agent laden
      Get-Service ssh-agent | Set-Service -StartupType Automatic; Start-Service ssh-agent
      ssh-add $env:USERPROFILE\.ssh\id_ed25519
  Danach entfaellt die ERSTE Abfrage (Server-Verbindung); fuer 'git pull' auf dem
  Server entfaellt sie nur, wenn dort der Key passphrase-frei ist.
#>
param([string]$Message = "deploy $(Get-Date -Format 'yyyy-MM-dd HH:mm')")

$ErrorActionPreference = "Stop"
$Server = "root@217.160.103.25"
$AppDir = "stockbot"
Set-Location -LiteralPath $PSScriptRoot

# --- 1) commit (nur bei Aenderungen) + push --------------------------------
if (git status --porcelain) {
    Write-Host "-> committe lokale Aenderungen ..." -ForegroundColor Cyan
    git add -A
    git commit -m $Message
} else {
    Write-Host "i  keine lokalen Aenderungen - ueberspringe commit."
}
Write-Host "-> push ..." -ForegroundColor Cyan
git push
if ($LASTEXITCODE -ne 0) { throw "git push fehlgeschlagen" }

# --- 2) Deploy: interaktiv (ssh -t) ----------------------------------------
# -t = Pseudo-Terminal: beide Passphrase-Abfragen (Server-Login + git pull) erscheinen
# hier in der Konsole und werden ganz normal eingetippt.
Write-Host "-> deploye auf $Server  (Passphrase wird abgefragt) ..." -ForegroundColor Cyan
$remote = "cd $AppDir && git pull && systemctl restart stockbot && systemctl status stockbot --no-pager -l | head -n 15"
ssh -t $Server $remote
if ($LASTEXITCODE -ne 0) { throw "Deploy (ssh) fehlgeschlagen" }

Write-Host "OK - gepusht und auf dem Server neu gestartet." -ForegroundColor Green
