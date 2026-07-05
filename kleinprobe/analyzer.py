"""
kleinprobe/analyzer.py
======================
Interpretation layer for KleinProbe v2.

Takes HardwareState and HardwareTrajectory objects as input
and produces interpreted drift signals. Does not run circuits,
does not make decisions, does not modify execution.

KleinProbe measures.
This module interprets.
Users decide.

Dependency chain: analyzer.py → state.py → snapshot.py → probe.py
analyzer.py never imports from policy.py.

Classes:
    DriftAnalyzer       — interprets a trajectory for drift patterns
    QueueDriftTracker   — tracks θ₁/θ₂/Δθ pattern for queued jobs
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict
from .state import HardwareState, StateDelta, HardwareTrajectory
from .baselines import get_baseline


# ── DriftAnalysis result ──────────────────────────────────────────────────

@dataclass
class DriftAnalysis:
    """
    Interpreted drift analysis over a HardwareTrajectory.
    Output of DriftAnalyzer.analyze().
    """
    n_points:       int
    stability:      Optional[float]
    trend:          str       # 'stable' | 'drifting' | 'degraded' | 'recovering'
    max_drift:      Optional[StateDelta]
    cumulative:     Optional[StateDelta]
    had_regime_change: bool
    alerts:         List[str]
    H_trend:        str       # 'rising' | 'falling' | 'flat'
    inv_trend:      str       # 'rising' | 'falling' | 'flat'

    def summary(self) -> str:
        lines = [
            f"DriftAnalysis",
            f"  Points:    {self.n_points}",
            f"  Trend:     {self.trend}",
            f"  Stability: {self.stability}",
            f"  H trend:   {self.H_trend}",
            f"  inv trend: {self.inv_trend}",
        ]
        if self.had_regime_change:
            lines.append(f"  ⚠ Regime change detected")
        if self.alerts:
            lines.append("  Alerts:")
            for a in self.alerts:
                lines.append(f"    ⚠ {a}")
        else:
            lines.append("  ✓ No alerts")
        return "\n".join(lines)


# ── DriftAnalyzer ─────────────────────────────────────────────────────────

class DriftAnalyzer:
    """
    Interprets a HardwareTrajectory for drift patterns.

    Does not run circuits. Takes a trajectory as input and
    returns a DriftAnalysis describing trend, stability,
    and alerts.

    Usage:
        traj = HardwareTrajectory()
        # ... populate with HardwareState observations ...
        analyzer = DriftAnalyzer(baseline_backend="ibm_marrakesh")
        analysis = analyzer.analyze(traj)
        print(analysis.summary())
    """

    def __init__(self, baseline_backend: Optional[str] = None,
                 drift_threshold: float = 0.5,
                 trend_window: int = 3):
        """
        Args:
            baseline_backend:  backend name to load stored baseline
            drift_threshold:   drift_score above which we alert (default 0.5)
            trend_window:      number of points for trend detection
        """
        self.baseline_backend = baseline_backend
        self.drift_threshold  = drift_threshold
        self.trend_window     = trend_window
        self._baseline        = (get_baseline(baseline_backend)
                                 if baseline_backend else None)

    def analyze(self, trajectory: HardwareTrajectory) -> DriftAnalysis:
        """
        Analyze a trajectory and return a DriftAnalysis.
        """
        if trajectory.n < 2:
            return DriftAnalysis(
                n_points=trajectory.n, stability=None,
                trend='insufficient_data', max_drift=None,
                cumulative=None, had_regime_change=False,
                alerts=[], H_trend='unknown', inv_trend='unknown')

        alerts = []
        deltas = trajectory.deltas()

        # Trend detection on H and inv
        H_trend   = self._detect_trend(trajectory.H_series)
        inv_trend = self._detect_trend(trajectory.inv_series)

        # Overall trend classification
        max_drift   = trajectory.max_drift
        cumulative  = trajectory.cumulative_delta
        stability   = trajectory.stability

        if max_drift and max_drift.drift_score > self.drift_threshold:
            if H_trend == 'rising':
                trend = 'degraded'
            elif H_trend == 'falling':
                trend = 'recovering'
            else:
                trend = 'drifting'
            alerts.append(
                f"Max drift score={max_drift.drift_score:.3f} "
                f"({max_drift.dominant_shift} shifted most)")
        else:
            trend = 'stable'

        # Regime change alert
        if trajectory.had_regime_change:
            regimes = trajectory.regime_sequence
            alerts.append(
                f"Regime change: {' → '.join(regimes)}")

        # Cumulative drift alert
        if cumulative and cumulative.is_significant:
            alerts.append(
                f"Cumulative drift: ΔH={cumulative.dH:+.3f}, "
                f"Δinv={cumulative.dinv:+.3f}")

        # Baseline comparison alerts
        if self._baseline:
            last = trajectory.states[-1]
            bl_alert = self._baseline.alert_message(last.H, last.inv)
            if bl_alert:
                alerts.append(f"Baseline: {bl_alert}")

        return DriftAnalysis(
            n_points          = trajectory.n,
            stability         = stability,
            trend             = trend,
            max_drift         = max_drift,
            cumulative        = cumulative,
            had_regime_change = trajectory.had_regime_change,
            alerts            = alerts,
            H_trend           = H_trend,
            inv_trend         = inv_trend,
        )

    def _detect_trend(self, series: List[float],
                      threshold: float = 0.05) -> str:
        """
        Detect monotonic trend in a series.
        Returns 'rising', 'falling', or 'flat'.
        Uses last min(n, trend_window) points.
        """
        if len(series) < 2:
            return 'unknown'
        window = series[-min(len(series), self.trend_window):]
        if len(window) < 2:
            return 'flat'
        # Linear regression slope
        x = np.arange(len(window))
        slope = np.polyfit(x, window, 1)[0]
        if slope > threshold:
            return 'rising'
        elif slope < -threshold:
            return 'falling'
        return 'flat'


# ── QueueDriftResult ──────────────────────────────────────────────────────

@dataclass
class QueueDriftResult:
    """
    Result of a queue drift measurement: θ₁ → θ₂ → Δθ.

    θ₁: state at submission time
    θ₂: state at (or near) execution time
    Δθ: the difference — how much the hardware changed while the job waited
    """
    theta_1:       HardwareState
    theta_2:       HardwareState
    delta:         StateDelta
    queue_time_s:  Optional[float]   # estimated queue wait in seconds
    recommendation: str              # 'proceed' | 'caution' | 'pause'

    @property
    def was_stable(self) -> bool:
        return not self.delta.is_significant

    def summary(self) -> str:
        status = "✓ stable" if self.was_stable else "⚠ drifted"
        lines = [
            f"QueueDriftResult [{status}]",
            f"  Backend:     {self.theta_1.backend}",
        ]
        if self.queue_time_s:
            lines.append(f"  Queue wait:  {self.queue_time_s/60:.1f} min")
        lines += [
            f"  θ₁ → θ₂:",
            f"    H:   {self.theta_1.H:.4f} → {self.theta_2.H:.4f} "
            f"(Δ={self.delta.dH:+.4f})",
            f"    inv: {self.theta_1.inv:.4f} → {self.theta_2.inv:.4f} "
            f"(Δ={self.delta.dinv:+.4f})",
            f"    S:   {self.theta_1.S:.3f} → {self.theta_2.S:.3f} "
            f"(Δ={self.delta.dS:+.3f})",
            f"  Drift score: {self.delta.drift_score:.3f}",
            f"  Recommendation: {self.recommendation}",
        ]
        return "\n".join(lines)


# ── QueueDriftTracker ─────────────────────────────────────────────────────

class QueueDriftTracker:
    """
    Tracks the θ₁/θ₂/Δθ pattern for queued IBM Quantum jobs.

    Measures hardware state at submission time (θ₁) and again
    at or after execution (θ₂), then computes the drift Δθ
    experienced while the job waited in the queue.

    This addresses a specific problem: IBM Quantum jobs can wait
    hours in queue. The hardware state at submission is not the
    state at execution. QueueDriftTracker makes this gap visible.

    Usage:
        from kleinprobe import KleinProbe
        from kleinprobe.analyzer import QueueDriftTracker

        probe   = KleinProbe(backend)
        tracker = QueueDriftTracker(probe)

        tracker.record_submission()        # run probe, store θ₁
        job = sampler.run(my_circuits)     # submit your job
        job.result()                       # wait for execution

        result = tracker.record_execution()  # run probe, store θ₂
        print(result.summary())
    """

    def __init__(self, probe, drift_threshold: float = 0.5):
        """
        Args:
            probe:           KleinProbe instance
            drift_threshold: drift_score above which to caution/pause
        """
        self.probe           = probe
        self.drift_threshold = drift_threshold
        self._theta_1:  Optional[HardwareState] = None
        self._theta_2:  Optional[HardwareState] = None
        self._t1:       Optional[datetime]       = None
        self._t2:       Optional[datetime]       = None

    def record_submission(self) -> HardwareState:
        """
        Run probe and record θ₁ (hardware state at submission time).
        Call this immediately before submitting your job.
        """
        snap = self.probe.run()
        self._theta_1 = HardwareState.from_snapshot(snap)
        self._t1      = datetime.now()
        return self._theta_1

    def record_execution(self) -> QueueDriftResult:
        """
        Run probe and record θ₂ (hardware state at execution time).
        Call this after your job has completed.

        Returns a QueueDriftResult with the full θ₁/θ₂/Δθ analysis.
        """
        if self._theta_1 is None:
            raise RuntimeError(
                "Call record_submission() before record_execution()")

        snap = self.probe.run()
        self._theta_2 = HardwareState.from_snapshot(snap)
        self._t2      = datetime.now()

        delta = self._theta_2 - self._theta_1

        # Queue time estimate
        queue_time = None
        if self._t1 and self._t2:
            queue_time = (self._t2 - self._t1).total_seconds()

        # Recommendation based on drift score
        if delta.drift_score < 0.3:
            rec = 'proceed'
        elif delta.drift_score < 0.7:
            rec = 'caution'
        else:
            rec = 'pause'

        return QueueDriftResult(
            theta_1      = self._theta_1,
            theta_2      = self._theta_2,
            delta        = delta,
            queue_time_s = queue_time,
            recommendation = rec,
        )

    @property
    def theta_1(self) -> Optional[HardwareState]:
        return self._theta_1

    @property
    def theta_2(self) -> Optional[HardwareState]:
        return self._theta_2

    @property
    def has_both(self) -> bool:
        return self._theta_1 is not None and self._theta_2 is not None
