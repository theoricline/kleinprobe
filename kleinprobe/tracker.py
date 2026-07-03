"""
kleinprobe/tracker.py
=====================
DriftTracker: monitors hardware calibration drift across
multiple checkpoints during a long experiment.

Usage pattern:
    tracker = DriftTracker(probe, baseline_sigma=2.0)
    tracker.checkpoint()           # before your experiment
    run_experiment_job_1()
    tracker.checkpoint()           # mid-experiment
    run_experiment_job_2()
    tracker.checkpoint()           # after experiment
    print(tracker.report())        # drift summary
"""

import numpy as np
from datetime import datetime
from typing import List, Optional
from .snapshot import Snapshot
from .baselines import get_baseline, Baseline


class DriftTracker:
    """
    Tracks KleinProbe snapshots over time and detects drift.

    Each checkpoint() call runs a probe and stores the result.
    The tracker maintains a history of (H, inv, f, Z) values
    and compares each to the baseline and to the first checkpoint.
    """

    def __init__(self, probe, baseline_sigma: float = 2.0,
                 auto_update_baseline: bool = False):
        """
        Args:
            probe:                  KleinProbe instance
            baseline_sigma:         Alert threshold in σ from baseline
            auto_update_baseline:   If True, update EMA baseline after each run
        """
        self.probe                = probe
        self.baseline_sigma       = baseline_sigma
        self.auto_update_baseline = auto_update_baseline
        self.history: List[Snapshot] = []
        self._baseline: Optional[Baseline] = None

    def checkpoint(self, label: str = "", delta: int = 0) -> Snapshot:
        """
        Run a probe snapshot and store it.

        Args:
            label:  Optional label for this checkpoint (e.g. 'pre', 'post')
            delta:  δ value to use for the probe circuit

        Returns:
            Snapshot with drift metrics filled in
        """
        snap = self.probe.run(delta=delta)

        # Get baseline
        bl = self._baseline or get_baseline(self.probe.backend.name)

        if bl:
            snap.delta_H   = bl.delta_H(snap.H)
            snap.delta_inv = bl.delta_inv(snap.inv)
            snap.alert     = bl.alert_message(
                snap.H, snap.inv, n_sigma=self.baseline_sigma)

        # Also compare to first checkpoint if we have history
        if self.history and bl is None:
            first = self.history[0]
            snap.delta_H   = round(snap.H   - first.H,   4)
            snap.delta_inv = round(snap.inv - first.inv, 4)
            if abs(snap.delta_H) > 0.3 or abs(snap.delta_inv) > 0.1:
                snap.alert = (f"Drift from first checkpoint: "
                             f"ΔH={snap.delta_H:+.3f} "
                             f"Δinv={snap.delta_inv:+.3f}")

        if label:
            snap._label = label

        self.history.append(snap)

        if self.auto_update_baseline:
            from .baselines import update_baseline_from_snapshot
            update_baseline_from_snapshot(snap)

        return snap

    def set_baseline(self, baseline: Baseline):
        """Override automatic baseline with a custom one."""
        self._baseline = baseline

    @property
    def n_checkpoints(self):
        return len(self.history)

    @property
    def has_drift(self):
        """True if any checkpoint triggered an alert."""
        return any(s.alert for s in self.history)

    @property
    def H_series(self):
        return [s.H for s in self.history]

    @property
    def inv_series(self):
        return [s.inv for s in self.history]

    @property
    def H_range(self):
        if not self.history: return (None, None)
        return (min(self.H_series), max(self.H_series))

    @property
    def inv_range(self):
        if not self.history: return (None, None)
        return (min(self.inv_series), max(self.inv_series))

    def match_rate(self):
        """Fraction of checkpoints where dominant == predicted."""
        if not self.history: return None
        return sum(1 for s in self.history if s.match) / len(self.history)

    def report(self, verbose=False) -> str:
        """Full drift report across all checkpoints."""
        if not self.history:
            return "DriftTracker: no checkpoints recorded."

        lines = [
            "=" * 60,
            f"KleinProbe Drift Report",
            f"Backend:     {self.probe.backend.name}",
            f"Checkpoints: {self.n_checkpoints}",
            f"Match rate:  {self.match_rate():.0%}",
            f"H range:     {self.H_range[0]:.3f} – {self.H_range[1]:.3f} bits",
            f"inv range:   {self.inv_range[0]:.3f} – {self.inv_range[1]:.3f}",
            "=" * 60,
            "",
            f"  {'#':>3}  {'time':>8}  {'H':>7}  {'inv':>7}  "
            f"{'ΔH':>7}  {'Δinv':>7}  {'match':>6}  alert",
            "  " + "-" * 65,
        ]

        for i, s in enumerate(self.history):
            t   = s.timestamp[11:19]   # HH:MM:SS
            dH  = f"{s.delta_H:+.3f}" if s.delta_H  is not None else "  —"
            di  = f"{s.delta_inv:+.3f}" if s.delta_inv is not None else "  —"
            ok  = "✓" if s.match else "✗"
            al  = "⚠️ ALERT" if s.alert else ""
            lines.append(f"  {i+1:>3}  {t}  {s.H:>7.4f}  {s.inv:>7.4f}  "
                        f"{dH:>7}  {di:>7}  {ok:>6}  {al}")

        if self.has_drift:
            lines += ["", "ALERTS:"]
            for i, s in enumerate(self.history):
                if s.alert:
                    lines.append(f"  Checkpoint {i+1}: {s.alert}")
        else:
            lines += ["", "✓ No drift detected across all checkpoints."]

        if verbose:
            lines += ["", "DETAILED CHECKPOINTS:"]
            for i, s in enumerate(self.history):
                lines.append(f"\n  [{i+1}] {s.report(verbose=True)}")

        return "\n".join(lines)

    def to_dict(self):
        return {
            'backend': self.probe.backend.name,
            'n_checkpoints': self.n_checkpoints,
            'match_rate': self.match_rate(),
            'H_series': self.H_series,
            'inv_series': self.inv_series,
            'has_drift': self.has_drift,
            'snapshots': [s.to_dict() for s in self.history],
        }
