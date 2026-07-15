#!/usr/bin/env bash
# Prueft den produktiven Lockfile; bekannte Schwachstellen liefern Exit-Code != 0.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v pip-audit >/dev/null 2>&1; then
    exec pip-audit -r requirements.lock
fi

if python -c 'import pip_audit' >/dev/null 2>&1; then
    exec python -m pip_audit -r requirements.lock
fi

echo "FEHLER: pip-audit ist nicht installiert (python -m pip install pip-audit)." >&2
exit 127
