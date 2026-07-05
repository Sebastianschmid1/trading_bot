"""Selbst-optimierendes Strategie-Labor (Walk-Forward-Auto-Tuning der KI-Strategie).

Kern: `lab.run_cycle()` schlägt walk-forward genau EINE Parameteränderung der Strategie
`ai_adaptive` vor (Ziel MAR, Out-of-Sample-Gate). Übernahme erst nach Menschen-Freigabe
(`lab.apply_pending()`), die die Live-Parameter in strategy_configs schreibt.
"""

from stockbot.optimize import lab  # noqa: F401
