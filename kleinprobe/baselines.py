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
        notes      = ("6 sessions from Papers 1-6 (H=4.50±0.15). "
                      "Session 7 (2026-07-05, post-recal 13:26 UTC): H=2.878, inv=0.898. "
                      "Session 7 reflects a different calibration state — "
                      "H dropped 1.6 bits while inv changed by only 0.002, "
                      "consistent with H and I probing distinct aspects of "
                      "the effective hardware state. "
                      "Baseline (n=6) reflects pre-July-2026 calibration era. "
                      "Update after additional post-recalibration sessions. "
                      "Seed variation (5 seeds): H_spread=0.540, inv_spread=0.051."),
    ),

    "ibm_marrakesh": Baseline(
        backend    = "ibm_marrakesh",
        H_mean     = 3.296,
        H_std      = 0.210,
        inv_mean   = 0.809,
        inv_std    = 0.033,
        f_mean     = 0.494,
        f_std      = 0.035,
        n_sessions = 4,
        notes      = ("4 sessions spanning 4 calibration cycles: "
                      "S1 2026-07-03 (H=3.369), S2 2026-07-04 post-15:36 (H=2.971), "
                      "S3 2026-07-05 post-09:32 (H=3.292), S4 2026-07-05 post-13:03 (H=3.553). "
                      "H oscillates 2.97–3.55 with no stable trend. "
                      "Session-to-session variability is substantial; "
                      "approximately 8-10 calibration cycles planned before "
                      "treating baseline as operationally stable. "
                      "inv more stable than H across sessions (std=0.033 vs 0.210). "
                      "Seed variation (5 seeds): H_spread=0.891, inv_spread=0.176. "
                      "Baseline is seed=77 specific. "
                      "Physical qubits: d[11]→q29 (RO=0.100), s[0]→q39 (RO=0.030)."),
    ),

    "ibm_kingston": Baseline(
        backend    = "ibm_kingston",
        H_mean     = 1.869,
        H_std      = 0.823,
        inv_mean   = 0.917,
        inv_std    = 0.030,
        f_mean     = 0.720,
        f_std      = 0.100,
        n_sessions = 2,
        notes      = ("2 sessions showing calibration-induced regime transition: "
                      "2026-07-04 seed=77 H=1.047 (collapsed, 2^H≈2 patterns), "
                      "2026-07-05 seed=77 H=2.692 (mid-entropy, 2^H≈6 patterns). "
                      "ΔH=+1.645 bits between sessions — largest observed shift. "
                      "Demonstrates that entropy regime is not a static backend "
                      "property but depends on calibration state and execution "
                      "context (backend × calibration × layout × time). "
                      "H_std=0.823 reflects regime instability, not measurement noise; "
                      "not a meaningful drift threshold. "
                      "Kingston requires 5+ sessions before baseline is reliable. "
                      "Seed variation (5 seeds, July 5): H_spread=0.506, inv_spread=0.071."),
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
