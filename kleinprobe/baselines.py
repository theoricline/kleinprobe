"""
kleinprobe/baselines.py
=======================
Hardware reference baselines from validated experiments.

These baselines were collected from real IBM hardware runs
across multiple calibration sessions and are used to detect
calibration drift. All values are for the 3×2 Klein code, δ=0.

Sources:
  ibm_fez:       Papers 1-6 (pre-Jul5 era) + S7-S17 (post-Jul5 era)
  ibm_marrakesh: S1-S12, 2026-07-03 to 2026-07-09
  ibm_kingston:  S2-S10 post-transition, 2026-07-04 to 2026-07-08

IMPORTANT — FEZ REGIME CHANGE:
  Around 2026-07-05 ibm_fez underwent a calibration regime change.
  H dropped from ~4.50 to ~2.97 bits — a shift of ~1.5 bits.
  Two separate baselines are maintained:
    Era 1 (pre-Jul5):  H=4.50±0.15  — Papers 1-6
    Era 2 (post-Jul5): H=2.97±0.17  — current operational baseline
  Comparing current sessions against Era 1 would produce false alerts.
  The active baseline is Era 2.
"""

from dataclasses import dataclass, field
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
        H_mean     = 2.977,
        H_std      = 0.177,
        inv_mean   = 0.889,
        inv_std    = 0.019,
        f_mean     = 0.520,
        f_std      = 0.040,
        n_sessions = 11,
        notes      = ("ERA 2 baseline (post-Jul5 calibration regime). "
                      "n=11 sessions S7-S17, 2026-07-05 to 2026-07-09. "
                      "H range: 2.734–3.364. inv range: 0.800–0.910. "
                      "ERA 1 (Papers 1-6, pre-Jul5): H=4.50±0.15, inv=0.900±0.020 — "
                      "archived, no longer reflects current operational state. "
                      "Regime change detected ~2026-07-05: ΔH≈-1.5 bits. "
                      "S15 (2026-07-08): soft anomaly, inv=0.800, q91 T2=21.4µs. "
                      "S16 (2026-07-08): partial recovery, inv=0.872. "
                      "inv_std includes anomalous sessions — "
                      "clean session inv_std≈0.019. "
                      "Seed variation (5 seeds, Jul8): H_spread=0.289, F_spread=0.022. "
                      "Spatial variation (3 patches): H_spread≈0.97 bits. "
                      "Seed=77, optimization_level=3."),
    ),

    "ibm_fez_era1": Baseline(
        backend    = "ibm_fez",
        H_mean     = 4.500,
        H_std      = 0.150,
        inv_mean   = 0.900,
        inv_std    = 0.020,
        f_mean     = 0.470,
        f_std      = 0.015,
        n_sessions = 6,
        notes      = ("ERA 1 baseline (pre-Jul5 calibration regime). "
                      "n=6 sessions from Papers 1-6, doi:10.5281/zenodo.19454514. "
                      "ARCHIVED — not the current operational state of ibm_fez. "
                      "Use 'ibm_fez' baseline for current sessions."),
    ),

    "ibm_marrakesh": Baseline(
        backend    = "ibm_marrakesh",
        H_mean     = 3.133,
        H_std      = 0.269,
        inv_mean   = 0.855,
        inv_std    = 0.043,
        f_mean     = 0.490,
        f_std      = 0.050,
        n_sessions = 12,
        notes      = ("n=12 sessions S1-S12, 2026-07-03 to 2026-07-09. "
                      "H range: 2.779–3.621. High session-to-session variability. "
                      "inv range: 0.763–0.903 (all sessions including anomalous). "
                      "Nominal inv (inv>0.82): mean=0.870±0.026. "
                      "Anomalous events documented: "
                      "  2026-07-05 14:44 — q29 P(meas0|prep1)=0.097, inv=0.474 (Class 1). "
                      "  2026-07-09 — chip-wide inv depression, all tiles <0.70 (Class 3). "
                      "H_std=0.269 reflects genuine calibration-cycle variability. "
                      "inv_std=0.043 includes anomalous sessions — "
                      "use 0.026 for nominal-only comparison. "
                      "inv anomaly threshold: inv < 0.77 (baseline - 2σ nominal). "
                      "Seed variation (5 seeds, Jul4): H_spread=0.891, inv_spread=0.176. "
                      "Seed=77, optimization_level=3."),
    ),

    "ibm_kingston": Baseline(
        backend    = "ibm_kingston",
        H_mean     = 2.691,
        H_std      = 0.097,
        inv_mean   = 0.890,
        inv_std    = 0.010,
        f_mean     = 0.640,
        f_std      = 0.030,
        n_sessions = 9,
        notes      = ("n=9 post-transition sessions S2-S10, 2026-07-04 to 2026-07-08. "
                      "S1 (2026-07-04, H=1.047) excluded — collapsed regime, transient. "
                      "H range: 2.553–2.840. Most stable chip in dataset. "
                      "inv range: 0.879–0.906 — tightest spread of three chips. "
                      "inv_std=0.010 — excellent baseline signal for anomaly detection. "
                      "S5 (Δt_cal=10.55h, H=2.840): highest post-transition H — "
                      "tentative Δt_cal drift (7x smaller than spatial effect). "
                      "Generalization result (Jul8): patch_2 (middle) best execution "
                      "region — r(H,1-F_GHZ)=+0.949. "
                      "Spatial fingerprint differs from Fez (Fez: central best). "
                      "Seed=77, optimization_level=3."),
    ),

}


def get_baseline(backend: str) -> Optional[Baseline]:
    """
    Get baseline for a backend. Returns None if unknown.
    Fuzzy-matches partial names (e.g. 'fez' → 'ibm_fez').
    Always returns Era 2 for ibm_fez (current operational baseline).
    """
    backend_lower = backend.lower()
    # Era1 only returned if explicitly requested
    if 'era1' in backend_lower:
        return BASELINES.get("ibm_fez_era1")
    for key, bl in BASELINES.items():
        if key == "ibm_fez_era1": continue  # skip era1 in fuzzy match
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
