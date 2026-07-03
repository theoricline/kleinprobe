"""
kleinprobe/snapshot.py
======================
Snapshot dataclass: the output of a single KleinProbe run.

A Snapshot captures the circuit-conditioned hardware state at a
specific moment: which physical qubits were used, what the syndrome
distribution looked like, and how far it deviated from the
topological prediction.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
import json
import numpy as np


@dataclass
class CalibrationSlice:
    """
    Calibration data for the physical qubits actually used
    by the probe circuit (extracted from transpiler layout).

    This is the circuit-conditioned projection of backend.properties():
    only the qubits that matter for this specific circuit.
    """
    physical_data_qubits: dict    # {logical_idx: physical_idx}
    physical_syn_qubits:  dict    # {logical_idx: physical_idx}
    t1_us:   Optional[dict] = None   # {physical_idx: T1 in μs}
    t2_us:   Optional[dict] = None   # {physical_idx: T2 in μs}
    ro_err:  Optional[dict] = None   # {physical_idx: readout error}
    cx_err:  Optional[dict] = None   # {(phys_i, phys_j): CX error}

    @property
    def antipodal_physical(self):
        """Physical qubit index of the antipodal (b-anyon) edge d[11]."""
        return self.physical_data_qubits.get(11)

    @property
    def invariant_syn_physical(self):
        """Physical qubit index of syndrome 0 (Klein invariant readout)."""
        return self.physical_syn_qubits.get(0)

    @property
    def t2_mean_us(self):
        if not self.t2_us: return None
        vals = list(self.t2_us.values())
        return round(np.mean(vals) * 1e6, 2) if vals else None

    @property
    def ro_mean(self):
        if not self.ro_err: return None
        vals = list(self.ro_err.values())
        return round(np.mean(vals), 5) if vals else None

    @property
    def antipodal_t2_us(self):
        """T2 of the physical qubit hosting the antipodal edge."""
        if not self.t2_us: return None
        phys = self.antipodal_physical
        return round(self.t2_us.get(phys, 0) * 1e6, 2) if phys else None

    @property
    def antipodal_ro(self):
        """Readout error of the physical qubit hosting the antipodal edge."""
        if not self.ro_err: return None
        phys = self.antipodal_physical
        return self.ro_err.get(phys) if phys else None


@dataclass
class Snapshot:
    """
    A single KleinProbe measurement snapshot.

    Captures the circuit-conditioned hardware state:
    what topology predicts vs what hardware produces,
    and the noise metrics that characterise the gap.
    """
    # Identity
    timestamp:  str
    backend:    str
    job_id:     str
    delta:      int
    shots:      int
    seed:       int

    # Circuit info
    circuit_depth:     int
    predicted_pattern: str
    flip_edge:         int            # logical data qubit flipped

    # Measurement result
    dominant_pattern:  str
    dominant_f:        float          # frequency of dominant pattern
    match:             bool           # dominant == predicted

    # Noise metrics
    H:    float    # Shannon entropy of syndrome distribution (bits)
    inv:  float    # Klein invariant fraction (P(bit_0 = 1))
    Z:    float    # Z-score vs flat baseline

    # Layout (circuit-conditioned calibration projection)
    calibration: Optional[CalibrationSlice] = None

    # Full distribution (optional, for offline analysis)
    counts: Optional[dict] = None

    # Baseline comparison (filled in by DriftTracker)
    delta_H:   Optional[float] = None   # H - baseline_H
    delta_inv: Optional[float] = None   # inv - baseline_inv
    alert:     Optional[str]  = None    # drift alert message if any

    def report(self, verbose=False):
        """Human-readable snapshot report."""
        match_str = "✓ MATCH" if self.match else "✗ MISMATCH"
        alert_str = f"\n  ⚠️  {self.alert}" if self.alert else ""

        lines = [
            f"KleinProbe Snapshot — {self.timestamp}",
            f"  Backend:   {self.backend}",
            f"  Job:       {self.job_id}",
            f"  δ={self.delta}  shots={self.shots}  depth={self.circuit_depth}",
            f"",
            f"  Predicted: '{self.predicted_pattern}'",
            f"  Got:       '{self.dominant_pattern}'  f={self.dominant_f:.4f}  {match_str}",
            f"",
            f"  H   = {self.H:.4f} bits   (syndrome entropy)",
            f"  inv = {self.inv:.4f}       (Klein invariant fraction)",
            f"  Z   = {self.Z:.1f}σ         (vs flat baseline)",
        ]

        if self.delta_H is not None:
            lines.append(f"")
            lines.append(f"  ΔH   = {self.delta_H:+.4f} vs baseline")
            lines.append(f"  Δinv = {self.delta_inv:+.4f} vs baseline")

        if alert_str:
            lines.append(alert_str)

        if verbose and self.calibration:
            c = self.calibration
            lines += [
                f"",
                f"  Circuit layout (logical → physical):",
                f"    Antipodal edge d[11] → q{c.antipodal_physical}",
                f"    Klein invariant s[0] → q{c.invariant_syn_physical}",
            ]
            if c.antipodal_t2_us:
                lines.append(f"    Antipodal qubit T2  = {c.antipodal_t2_us}μs")
            if c.antipodal_ro:
                lines.append(f"    Antipodal qubit RO  = {c.antipodal_ro:.4f}")
            if c.t2_mean_us:
                lines.append(f"    Mean T2 (circuit)   = {c.t2_mean_us}μs")
            if c.ro_mean:
                lines.append(f"    Mean RO (circuit)   = {c.ro_mean:.4f}")

        return "\n".join(lines)

    def to_dict(self):
        d = asdict(self)
        if self.calibration:
            d['calibration'] = {
                'physical_data_qubits': self.calibration.physical_data_qubits,
                'physical_syn_qubits':  self.calibration.physical_syn_qubits,
                't1_us':  self.calibration.t1_us,
                't2_us':  {str(k): v for k, v in (self.calibration.t2_us or {}).items()},
                'ro_err': {str(k): v for k, v in (self.calibration.ro_err or {}).items()},
            }
        return d

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2, default=str)

    @property
    def noise_point(self):
        """Return (H, inv) as a point in the noise manifold."""
        return (self.H, self.inv)

    @property
    def is_healthy(self):
        """
        Basic health check: did the probe produce the predicted pattern
        and is the Z-score above 50σ (well above noise floor)?
        """
        return self.match and self.Z > 50
