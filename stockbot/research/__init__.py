"""Pure research workflows that do not perform market-data or broker IO."""

from stockbot.research.shadow import (
    SimulatedExit,
    SimulatedFill,
    shadow_performance_snapshot,
    simulate_entry,
    simulate_exit,
    to_shadow_signal,
)

__all__ = [
    "SimulatedExit",
    "SimulatedFill",
    "shadow_performance_snapshot",
    "simulate_entry",
    "simulate_exit",
    "to_shadow_signal",
]
