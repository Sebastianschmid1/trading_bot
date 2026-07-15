# Python-Abhaengigkeiten

`requirements.in` ist die menschenlesbare Liste direkter Laufzeitabhaengigkeiten.
`requirements.txt` bleibt aus Kompatibilitaetsgruenden als bisherige Top-Level-Liste
bestehen. Produktion und Deploy-Skripte installieren ausschliesslich den voll
gepinnten `requirements.lock`.

## Abhaengigkeiten aktualisieren

1. Gewuenschte direkte Abhaengigkeit in `requirements.in` aendern.
2. Tooling installieren: `python -m pip install pip-tools`.
3. Lockfile mit Hashes neu erzeugen:
   `pip-compile --generate-hashes --output-file requirements.lock requirements.in`.
4. Den Lockfile in einer frischen venv mit
   `python -m pip install --require-hashes -r requirements.lock` installieren.
5. `python -m pytest -q` und `scripts/pip_audit.sh` ausfuehren und beide Dateien
   gemeinsam committen.

Der initiale Lockfile wurde mangels Netzwerkzugriff als dokumentierter
`pip freeze`-Fallback aus der funktionierenden Projekt-venv erzeugt und enthaelt
noch keine Hashes. Darum verwenden die Deploy-Skripte derzeit kein
`--require-hashes`. Beim naechsten regulaeren Update ist er mit `pip-compile` wie
oben beschrieben zu ersetzen und der Deploy-Aufruf um `--require-hashes` zu
ergaenzen.

Die Installation in eine frische venv konnte in derselben Sandbox nicht
abgeschlossen werden: ohne Netzwerkzugriff und lokalen Wheel-Cache war bereits
`alembic==1.18.5` nicht beziehbar. `pip check` bestaetigte jedoch, dass der
Snapshot in der bestehenden Projekt-venv widerspruchsfrei installiert ist. Die
Fresh-venv-Pruefung muss vor dem Deploy mit erreichbarem Paketindex nachgeholt
werden.

## Sicherheits-Audit

`scripts/pip_audit.sh` auditiert den Lockfile und gibt bei bekannten CVEs einen
Exit-Code ungleich null zurueck. Das Skript soll bei jedem Dependency-Update und
vor jedem Produktions-Deploy laufen. `pip-audit` ist bewusst keine
Laufzeitabhaengigkeit; bei Bedarf separat mit
`python -m pip install pip-audit` installieren.

Der Audit konnte bei Erstellung dieses Lockfiles nicht ausgefuehrt werden:
`pip-audit` war nicht vorhanden und die Sandbox konnte wegen gesperrter
DNS-/Netzwerkverbindung weder das Tool noch die Vulnerability-Daten installieren
bzw. abrufen. Es liegen daher lokal keine belastbaren Findings vor; der Audit ist
vor dem naechsten Deploy in einer Umgebung mit Netzwerkzugriff nachzuholen.

## yfinance: bekannter FD-Leak

`yfinance` ist auf die aktuell verifizierte Version 1.5.1 gepinnt. Der bekannte
Leak offener Datei-Handles am tz-Cache (`~/.cache/py-yfinance/tkr-tz.db`) ist damit
nicht als behoben bewertet. `LimitNOFILE=65535` in den systemd-Units bleibt der
produktive Workaround; dieser Dependency-Task enthaelt bewusst keinen Code-Fix.
