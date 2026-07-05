"""
kleinprobe/state.py
===================
Structured hardware state objects for KleinProbe v2.

Separates measurement (Snapshot) from state estimation (HardwareState),
and provides first-class objects for state differences (StateDelta)
and time-series trajectories (HardwareTrajectory).

Dependency: snapshot.py only. No imports from probe.py, analyzer.py,
or policy.py — this layer knows only about raw measurements.

Usage:
    snap  = probe.run()                          # raw measurement
    state = HardwareState.from_snapshot(snap)   # structured state
    
    state.vector     # np.array([H, inv, f, Z_raw, S])
    state.regime     # 'high_entropy' | 'mid_entropy' | 'collapsed'
    
    delta = state2 - state1   # StateDelta
    delta.drift_score         # scalar 0-1
    delta.is_significant      # bool
    
    traj = HardwareTrajectory()
    traj.add(state)
    traj.stability            # float
    traj.max_drift            # StateDelta
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List
from .snapshot import Snapshot


# ── Regime thresholds ─────────────────────────────────────────────────────
# Based on empirical validation across three IBM Heron r2 processors.
# Regime is a property of the probe-response space, not hardware quality.
# See: doi:10.5281/zenodo.21186260, Section 4.

REGIME_THRESHOLDS = {
    'collapsed':    1.5,   # H < 1.5 → near-deterministic syndrome
    'mid_entropy':  3.0,   # 1.5 ≤ H < 3.0
    'high_entropy': 3.0,   # H ≥ 3.0
}

# Significance threshold for drift detection (in normalized units)
DRIFT_SIGNIFICANCE = 0.15   # ΔH > 0.15 bits or Δinv > 0.05 = significant


# ── HardwareState ─────────────────────────────────────────────────────────

@dataclass
class HardwareState:
    """
    Structured estimate of the circuit-conditioned hardware state Θ(L,t).

    Wraps a Snapshot and provides:
    - A normalized state vector suitable for arithmetic and comparison
    - Regime classification (high_entropy / mid_entropy / collapsed)
    - Identity metadata (backend, seed, timestamp) for provenance

    HardwareState is an estimate of observable statistics induced by
    Θ(L,t); it does not claim to reconstruct Θ directly.
    See: doi:10.5281/zenodo.21186260
    """
    # Core state vector components
    H:     float   # syndrome entropy (bits)
    inv:   float   # Klein invariant fraction
    f:     float   # dominant pattern frequency
    Z_raw: float   # statistical significance vs uniform baseline
    S:     float   # normalized probe signal score

    # Identity — required to interpret state differences meaningfully
    backend:   str
    seed:      int
    timestamp: str
    job_id:    str
    delta:     int   # δ value used (0, 1, or 2)

    # Optional: layout identity (populated after sensitivity experiment)
    # If seed variation shows H varies >0.3 bits across seeds,
    # layout must be included in state identity for valid comparisons.
    layout_hash: Optional[str] = None

    # Source snapshot (kept for traceability)
    _snapshot: Optional[Snapshot] = field(default=None, repr=False)

    @classmethod
    def from_snapshot(cls, snap: Snapshot) -> 'HardwareState':
        """Construct a HardwareState from a raw Snapshot."""
        return cls(
            H         = snap.H,
            inv       = snap.inv,
            f         = snap.dominant_f,
            Z_raw     = snap.Z_raw,
            S         = snap.S,
            backend   = snap.backend,
            seed      = snap.seed,
            timestamp = snap.timestamp,
            job_id    = snap.job_id,
            delta     = snap.delta,
            _snapshot = snap,
        )

    @property
    def vector(self) -> np.ndarray:
        """State vector θ = (H, inv, f, Z_raw, S) ∈ ℝ⁵."""
        return np.array([self.H, self.inv, self.f, self.Z_raw, self.S])

    @property
    def primary_vector(self) -> np.ndarray:
        """Primary observables only: θ_primary = (H, inv, f) ∈ ℝ³."""
        return np.array([self.H, self.inv, self.f])

    @property
    def regime(self) -> str:
        """
        Entropy regime classification based on H.

        'collapsed':    H < 1.5 — near-deterministic syndrome basin
                        (e.g. ibm_kingston: H≈1.05)
        'mid_entropy':  1.5 ≤ H < 3.0
        'high_entropy': H ≥ 3.0 — broad syndrome distribution
                        (e.g. ibm_fez: H≈4.5)

        Note: regime reflects circuit-induced attractor geometry,
        not hardware quality ranking.
        """
        if self.H < REGIME_THRESHOLDS['collapsed']:
            return 'collapsed'
        elif self.H < REGIME_THRESHOLDS['high_entropy']:
            return 'mid_entropy'
        else:
            return 'high_entropy'

    @property
    def effective_patterns(self) -> float:
        """Effective number of syndrome patterns: 2^H."""
        return round(2 ** self.H, 1)

    @property
    def is_healthy(self) -> bool:
        """True if probe signal score S > 0.5 and pattern matched."""
        if self._snapshot:
            return self._snapshot.is_healthy
        return self.S > 0.5

    def __sub__(self, other: 'HardwareState') -> 'StateDelta':
        """
        Compute state difference: delta = self - other.

        Requires same backend and seed for a valid comparison.
        Cross-seed or cross-backend deltas are computed but flagged.
        """
        return StateDelta.compute(self, other)

    def __repr__(self):
        return (f"HardwareState({self.backend}, seed={self.seed}, "
                f"H={self.H:.3f}, inv={self.inv:.3f}, "
                f"regime='{self.regime}')")


# ── StateDelta ────────────────────────────────────────────────────────────

@dataclass
class StateDelta:
    """
    Difference between two HardwareState observations: Δθ = θ₂ - θ₁.

    Provides multiple distance metrics and a scalar drift score
    for use in drift detection and monitoring.
    """
    # Raw differences
    dH:    float   # Δ entropy (bits)
    dinv:  float   # Δ invariant fraction
    df:    float   # Δ dominant frequency
    dZ:    float   # Δ statistical significance
    dS:    float   # Δ probe signal score

    # Timing
    dt_seconds: Optional[float]   # time between observations

    # Identity metadata
    backend:    str
    seed_from:  int
    seed_to:    int
    same_seed:  bool   # False = cross-seed comparison
    same_backend: bool

    @classmethod
    def compute(cls, state2: HardwareState,
                state1: HardwareState) -> 'StateDelta':
        """Compute Δθ = state2 - state1."""
        try:
            t1 = datetime.fromisoformat(state1.timestamp.rstrip('Z'))
            t2 = datetime.fromisoformat(state2.timestamp.rstrip('Z'))
            dt = (t2 - t1).total_seconds()
        except Exception:
            dt = None

        return cls(
            dH          = round(state2.H    - state1.H,    4),
            dinv        = round(state2.inv  - state1.inv,  4),
            df          = round(state2.f    - state1.f,    4),
            dZ          = round(state2.Z_raw - state1.Z_raw, 1),
            dS          = round(state2.S    - state1.S,    3),
            dt_seconds  = round(dt, 1) if dt is not None else None,
            backend     = state2.backend,
            seed_from   = state1.seed,
            seed_to     = state2.seed,
            same_seed   = state1.seed == state2.seed,
            same_backend= state1.backend == state2.backend,
        )

    @property
    def raw_vector(self) -> np.ndarray:
        """Raw difference vector Δθ = (ΔH, Δinv, Δf, ΔZ_raw, ΔS)."""
        return np.array([self.dH, self.dinv, self.df, self.dZ, self.dS])

    @property
    def primary_vector(self) -> np.ndarray:
        """Primary observable differences: (ΔH, Δinv, Δf)."""
        return np.array([self.dH, self.dinv, self.df])

    @property
    def norm_l2(self) -> float:
        """L2 norm of primary difference vector."""
        return round(float(np.linalg.norm(self.primary_vector)), 4)

    @property
    def norm_inf(self) -> float:
        """L∞ norm (max absolute component) of primary vector."""
        return round(float(np.max(np.abs(self.primary_vector))), 4)

    @property
    def direction(self) -> np.ndarray:
        """Unit vector in direction of primary change."""
        n = np.linalg.norm(self.primary_vector)
        if n < 1e-10:
            return np.zeros(3)
        return self.primary_vector / n

    @property
    def relative_change(self) -> dict:
        """
        Relative changes as fractions of typical baseline ranges.
        Uses empirical ranges from validated hardware sessions.
        """
        # Typical ranges from 3-backend validation
        ranges = {'H': 4.5, 'inv': 0.15, 'f': 0.35}
        return {
            'H':   round(abs(self.dH)   / ranges['H'],   3),
            'inv': round(abs(self.dinv) / ranges['inv'],  3),
            'f':   round(abs(self.df)   / ranges['f'],    3),
        }

    @property
    def dominant_shift(self) -> str:
        """Which component changed most (H, inv, or f)."""
        components = {'H': abs(self.dH),
                      'inv': abs(self.dinv),
                      'f': abs(self.df)}
        return max(components, key=components.get)

    @property
    def drift_score(self) -> float:
        """
        Scalar drift score ∈ [0, 1].

        Combines ΔH and Δinv with empirically motivated thresholds:
          ΔH > 0.3 bits → significant entropy drift
          Δinv > 0.05   → significant invariant drift

        Score is the maximum of the two normalized deviations,
        clipped to [0, 1]. A score > 0.5 indicates meaningful drift.
        """
        H_score   = min(abs(self.dH)   / 0.3,  1.0)
        inv_score = min(abs(self.dinv) / 0.05, 1.0)
        return round(max(H_score, inv_score), 3)

    @property
    def is_significant(self) -> bool:
        """True if drift_score > 0.5."""
        return self.drift_score > 0.5

    @property
    def is_valid_comparison(self) -> bool:
        """
        True if the delta is between same-seed, same-backend states.
        Cross-seed or cross-backend deltas may reflect layout changes,
        not temporal drift.
        """
        return self.same_seed and self.same_backend

    def summary(self) -> str:
        """Human-readable drift summary."""
        valid = "" if self.is_valid_comparison else " ⚠ cross-seed"
        direction = "↑" if self.dH > 0 else "↓"
        status = "⚠ DRIFT" if self.is_significant else "✓ stable"
        dt_str = (f"  Δt={self.dt_seconds:.0f}s"
                  if self.dt_seconds else "")
        return (f"StateDelta [{status}]{valid}\n"
                f"  ΔH={self.dH:+.4f} {direction}  "
                f"Δinv={self.dinv:+.4f}  "
                f"Δf={self.df:+.4f}\n"
                f"  drift_score={self.drift_score:.3f}  "
                f"dominant_shift={self.dominant_shift}  "
                f"norm_L2={self.norm_l2:.4f}{dt_str}")

    def __repr__(self):
        return (f"StateDelta(ΔH={self.dH:+.3f}, "
                f"Δinv={self.dinv:+.3f}, "
                f"score={self.drift_score:.3f}, "
                f"significant={self.is_significant})")


# ── HardwareTrajectory ────────────────────────────────────────────────────

class HardwareTrajectory:
    """
    Time-ordered sequence of HardwareState observations.

    Represents the evolution of the circuit-conditioned hardware state
    Θ(L,t) over a series of probe executions. Provides summary
    statistics over the trajectory for drift analysis.

    Usage:
        traj = HardwareTrajectory()
        traj.add(HardwareState.from_snapshot(probe.run()))
        # ... run experiment jobs ...
        traj.add(HardwareState.from_snapshot(probe.run()))
        print(traj.stability)
        print(traj.summary())
    """

    def __init__(self, label: str = ""):
        self.label:  str = label
        self._states: List[HardwareState] = []

    def add(self, state: HardwareState) -> None:
        """Append a new HardwareState observation."""
        self._states.append(state)

    def __len__(self):
        return len(self._states)

    def __getitem__(self, idx):
        return self._states[idx]

    def __iter__(self):
        return iter(self._states)

    @property
    def states(self) -> List[HardwareState]:
        return list(self._states)

    @property
    def n(self) -> int:
        return len(self._states)

    @property
    def is_empty(self) -> bool:
        return self.n == 0

    @property
    def duration(self) -> Optional[float]:
        """Total duration in seconds (first to last observation)."""
        if self.n < 2:
            return None
        try:
            t0 = datetime.fromisoformat(self._states[0].timestamp.rstrip('Z'))
            t1 = datetime.fromisoformat(self._states[-1].timestamp.rstrip('Z'))
            return round((t1 - t0).total_seconds(), 1)
        except Exception:
            return None

    @property
    def H_series(self) -> List[float]:
        return [s.H for s in self._states]

    @property
    def inv_series(self) -> List[float]:
        return [s.inv for s in self._states]

    @property
    def S_series(self) -> List[float]:
        return [s.S for s in self._states]

    @property
    def mean_state(self) -> Optional[np.ndarray]:
        """Mean state vector over the trajectory."""
        if self.is_empty:
            return None
        vectors = np.stack([s.primary_vector for s in self._states])
        return np.mean(vectors, axis=0)

    @property
    def std_state(self) -> Optional[np.ndarray]:
        """Std dev of state vector over the trajectory."""
        if self.n < 2:
            return None
        vectors = np.stack([s.primary_vector for s in self._states])
        return np.std(vectors, axis=0)

    @property
    def stability(self) -> Optional[float]:
        """
        Trajectory stability score ∈ [0, 1].
        1.0 = perfectly stable, 0.0 = maximally drifted.
        Computed as 1 - max_drift_score across consecutive pairs.
        """
        if self.n < 2:
            return None
        deltas = [self._states[i+1] - self._states[i]
                  for i in range(self.n - 1)]
        max_score = max(d.drift_score for d in deltas)
        return round(1.0 - max_score, 3)

    @property
    def max_drift(self) -> Optional['StateDelta']:
        """The StateDelta with the highest drift score."""
        if self.n < 2:
            return None
        deltas = [self._states[i+1] - self._states[i]
                  for i in range(self.n - 1)]
        return max(deltas, key=lambda d: d.drift_score)

    @property
    def cumulative_delta(self) -> Optional['StateDelta']:
        """Total state change from first to last observation."""
        if self.n < 2:
            return None
        return self._states[-1] - self._states[0]

    @property
    def regime_sequence(self) -> List[str]:
        return [s.regime for s in self._states]

    @property
    def had_regime_change(self) -> bool:
        """True if regime changed at any point in the trajectory."""
        regimes = self.regime_sequence
        return len(set(regimes)) > 1

    def deltas(self) -> List['StateDelta']:
        """All consecutive state deltas."""
        return [self._states[i+1] - self._states[i]
                for i in range(self.n - 1)]

    def summary(self) -> str:
        """Human-readable trajectory summary."""
        if self.is_empty:
            return "HardwareTrajectory: empty"

        lines = [
            f"HardwareTrajectory{' — '+self.label if self.label else ''}",
            f"  Backend:    {self._states[0].backend}",
            f"  Seed:       {self._states[0].seed}",
            f"  Points:     {self.n}",
        ]

        if self.duration:
            lines.append(f"  Duration:   {self.duration:.0f}s")

        if self.n >= 2:
            H_arr   = np.array(self.H_series)
            inv_arr = np.array(self.inv_series)
            lines += [
                f"  H:          {H_arr.min():.3f} – {H_arr.max():.3f} "
                f"(mean={H_arr.mean():.3f})",
                f"  inv:        {inv_arr.min():.3f} – {inv_arr.max():.3f} "
                f"(mean={inv_arr.mean():.3f})",
                f"  Stability:  {self.stability:.3f}",
                f"  Regimes:    {' → '.join(self.regime_sequence)}",
            ]

            if self.had_regime_change:
                lines.append(f"  ⚠ Regime change detected")

            md = self.max_drift
            if md and md.is_significant:
                lines.append(f"  ⚠ Max drift: {md.summary()}")
            else:
                lines.append(f"  ✓ No significant drift detected")

        return "\n".join(lines)

    def __repr__(self):
        return (f"HardwareTrajectory(n={self.n}, "
                f"stability={self.stability})")
