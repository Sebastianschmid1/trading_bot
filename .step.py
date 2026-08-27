"""Loest einen Fachbereich aus stockbot/core/db/__init__.py heraus (ein Schritt, ein Commit).

Aufruf: python .step.py <modul> [<modul> ...]
Die Modul-Dateien selbst stammen aus dem bereits geprueften Endzustand im Scratchpad.
"""
import ast
import re
import shutil
import sys
from pathlib import Path

BASE = Path('/home/jms/trading_bot/.claude/worktrees/agent-a3c8ae55144999989')
FINAL = Path('/tmp/claude-1000/-home-jms-trading-bot/'
             '74ddfb2a-fe28-4ab2-af1c-6094e3e0da0e/scratchpad/final/db')
PKG = BASE / 'stockbot/core/db'
INIT = PKG / '__init__.py'

module = sys.argv[1:]
assert module, 'Modulnamen angeben'

# --- Re-Export-Bloecke aus dem Endzustand holen ------------------------------
final_init = (FINAL / '__init__.py').read_text(encoding='utf-8')
reexport = {}
for treffer in re.finditer(r'((?:^#[^\n]*\n)+)from \.(\w+) import \([^)]*\)\n',
                           final_init, re.M):
    reexport[treffer.group(2)] = treffer.group(0).rstrip('\n')
for m in module:
    assert m in reexport, f'kein Re-Export-Block fuer {m}'

# --- Namen bestimmen, die dieses Modul uebernimmt ----------------------------
def top_level(src):
    out = {}
    tree = ast.parse(src)
    for node in tree.body:
        namen, start = [], node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            namen = [node.name]
        elif isinstance(node, ast.Assign):
            namen = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            namen = [node.target.id]
        for n in namen:
            out[n] = (start, node.end_lineno)
    return out


umzieher = set()
for m in module:
    umzieher |= set(top_level((FINAL / f'{m}.py').read_text(encoding='utf-8')))

# --- __init__.py umschreiben -------------------------------------------------
init_src = INIT.read_text(encoding='utf-8')
lines = init_src.splitlines()
bloecke = top_level(init_src)

# Block = ab Ende der vorigen Definition (Abschnittskommentare wandern mit)
grenzen = sorted(bloecke.values())
start_von = {}
prev_end = 0
for start, end in grenzen:
    start_von[(start, end)] = prev_end + 1
    prev_end = end

entfernen = set()
for name in umzieher:
    if name not in bloecke:
        continue
    start, end = bloecke[name]
    entfernen |= set(range(start_von[(start, end)], end + 1))

rest = [t for i, t in enumerate(lines, 1) if i not in entfernen]
neu = '\n'.join(rest).rstrip() + '\n'

# init_db schreibt den Strategie-Cache ab jetzt in `strategies`, nicht in die Kopie
if 'strategies' in module and 'init_db' in bloecke and 'init_db' not in umzieher:
    neu = neu.replace(
        "    global _STRATEGY_VERSIONS_BOOTSTRAPPED\n"
        "    _STRATEGY_VERSION_CACHE.clear()   # frische DB → gecachte Strategie-Version-IDs verwerfen\n"
        "    _STRATEGY_VERSIONS_BOOTSTRAPPED = False\n",
        "    # Der Cache lebt in `strategies` — hier wird er dort zurückgesetzt, nicht über das Paket:\n"
        "    # ein rebindender Schreibzugriff träfe sonst nur die Kopie im Re-Export.\n"
        "    strategies._STRATEGY_VERSION_CACHE.clear()   # frische DB → gecachte Strategie-Version-IDs verwerfen\n"
        "    strategies._STRATEGY_VERSIONS_BOOTSTRAPPED = False\n")
    neu = neu.replace("from stockbot.core import db_backend\n",
                      "from stockbot.core import db_backend\n", 1)

anhang = ['']
if 'strategies' in module:
    anhang.append('from . import strategies   # init_db setzt den Strategieversions-Cache zurück')
    anhang.append('')
for m in module:
    anhang.append(reexport[m])
    anhang.append('')

INIT.write_text(neu + '\n' + '\n'.join(anhang).rstrip() + '\n', encoding='utf-8')

for m in module:
    shutil.copy(FINAL / f'{m}.py', PKG / f'{m}.py')

print('herausgeloest:', ', '.join(module))
print('__init__.py jetzt:', len(INIT.read_text(encoding='utf-8').splitlines()), 'Zeilen')
