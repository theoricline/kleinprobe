"""
kleinprobe/baselines.py
=======================
Hardware reference baselines from validated experiments.

These baselines were collected from real IBM hardware runs
across multiple calibration sessions and are used to detect
calibration drift. All values are for the 3×2 Klein code, δ=0.

Sources:
  ibm_fez:       Papers 1-6, doi:10.5281/zenodo.19454514
  ibm_marrakesh: Attestation experiments, job d93t5jcql68s73c8qg30
  ibm_kingston:  Paper 4, doi:10.5281/zenodo.19333513
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Baseline:
    """Reference values for a specific backend."""
    backend:    str
    H_mean:     float          # expected syndrome entropy
    H_std:      float          # session-to-session std dev
    inv_mean:   float          # expected Klein invariant fraction
    inv_std:    float
    f_mean:     float          # expected dominant pattern frequency
    f_std:      float
    n_sessions: int            # number of sessions this is based on
    delta:      int = 0        # which δ value
    notes:      str = ""

    def in_range(self, H: float, inv: float, n_sigma: float = 2.0):
        """Check if (H, inv) is within n_sigma of baseline."""
        H_ok   = abs(H   - self.H_mean)   <= n_sigma * max(self.H_std,   0.05)
        inv_ok = abs(inv - self.inv_mean)  <= n_sigma * max(self.inv_std, 0.02)
        return H_ok and inv_ok

    def delta_H(self, H: float):
        return round(H - self.H_mean, 4)

    def delta_inv(self, inv: float):
        return round(inv - self.inv_mean, 4)

    def alert_message(self, H: float, inv: float, n_sigma: float = 2.0):
        """Return alert string if outside baseline, else None."""
        dH   = abs(H   - self.H_mean)
        dinv = abs(inv - self.inv_mean)
        H_thresh   = n_sigma * max(self.H_std,   0.05)
        inv_thresh = n_sigma * max(self.inv_std, 0.02)

        issues = []
        if dH > H_thresh:
            direction = "higher" if H > self.H_mean else "lower"
            issues.append(f"H={H:.3f} is {direction} than baseline "
                         f"{self.H_mean:.3f}±{self.H_std:.3f} "
                         f"(Δ={H-self.H_mean:+.3f})")
        if dinv > inv_thresh:
            direction = "higher" if inv > self.inv_mean else "lower"
            issues.append(f"inv={inv:.3f} is {direction} than baseline "
                         f"{self.inv_mean:.3f}±{self.inv_std:.3f} "
                         f"(Δ={inv-self.inv_mean:+.3f})")

        if issues:
            return "Calibration drift detected: " + "; ".join(issues)
        return None


# ── Known hardware baselines ──────────────────────────────────────────────

BASELINES = {

    "ibm_fez": Baseline(
        backend    = "ibm_fez",
        H_mean     = 4.50,
        H_std      = 0.15,
        inv_mean   = 0.900,
        inv_std    = 0.020,
        f_mean     = 0.470,
        f_std      = 0.015,
        n_sessions = 6,
        notes      = ("6 calibration sessions, Papers 1-6. "
                      "Higher entropy than Marrakesh — noisier qubit region. "
                      "Depth 112 for 3×2 on Fez."),
    ),

    "ibm_marrakesh": Baseline(
        backend    = "ibm_marrakesh",
        H_mean     = 3.37,
        H_std      = 0.10,
        inv_mean   = 0.834,
        inv_std    = 0.020,
        f_mean     = 0.484,
        f_std      = 0.015,
        n_sessions = 1,
        notes      = ("Single session, job d93t5jcql68s73c8qg30. "
                      "Circuit placed on physical qubits 18-71. "
                      "Notable: antipodal edge d[11]→q29, RO=0.100. "
                      "Klein invariant s[0]→q39, RO=0.030. "
                      "Depth 87-88 for 3×2 on Marrakesh."),
    ),

    "ibm_kingston": Baseline(
        backend    = "ibm_kingston",
        H_mean     = 1.05,    
        H_std      = 0.00,
        inv_mean   = 0.947,   
        inv_std    = 0.00,
        f_mean     = 0.817,   
        f_std      = 0.000,
        n_sessions = a,       
        notes      = ("Experimental baseline from first KleinProbe session "
                      "(seed=77, δ=0, 4096 shots). "
                      "Single-session baseline; update standard deviations "
                      "after repeated calibration sessions."),
    ),
}


def get_baseline(backend: str) -> Optional[Baseline]:
    """
    Get baseline for a backend. Returns None if unknown.
    Fuzzy-matches partial names (e.g. 'fez' → 'ibm_fez').
    """
    backend_lower = backend.lower()
    for key, bl in BASELINES.items():
        if key in backend_lower or backend_lower in key:
            return bl
    return None


def register_baseline(baseline: Baseline):
    """Add or update a baseline (e.g. from user's own data)."""
    BASELINES[baseline.backend] = baseline


def update_baseline_from_snapshot(snapshot):
    """
    Update a baseline with new session data using exponential moving average.
    alpha=0.3 weights recent observations without discarding history.
    """
    backend = snapshot.backend
    bl = get_baseline(backend)
    alpha = 0.3

    if bl is None:
        BASELINES[backend] = Baseline(
            backend    = backend,
            H_mean     = snapshot.H,
            H_std      = 0.10,
            inv_mean   = snapshot.inv,
            inv_std    = 0.02,
            f_mean     = snapshot.dominant_f,
            f_std      = 0.015,
            n_sessions = 1,
        )
        return

    bl.H_mean   = (1 - alpha) * bl.H_mean   + alpha * snapshot.H
    bl.inv_mean = (1 - alpha) * bl.inv_mean + alpha * snapshot.inv
    bl.f_mean   = (1 - alpha) * bl.f_mean   + alpha * snapshot.dominant_f
    bl.n_sessions += 1
