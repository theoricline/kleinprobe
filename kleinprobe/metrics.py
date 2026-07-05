"""
kleinprobe/metrics.py
=====================
Canonical definitions of all KleinProbe metrics.

Single source of truth for constants, formulas, and metric
computation functions. All other modules import from here
rather than defining their own copies.

Paper reference: doi:10.5281/zenodo.21186260, Section 3.
"""

import numpy as np

# ── Constants ─────────────────────────────────────────────────────────────

N_SYN = 6          # syndrome register width (3×2 Klein code)
P0    = 1 / 2**N_SYN   # uniform baseline probability = 1/64
Z0    = 50.0       # Z_raw scaling constant: Z_raw = Z0 → S = 1.0

# ── Metric functions ──────────────────────────────────────────────────────

def syndrome_entropy(counts: dict, shots: int) -> float:
    """
    Shannon entropy H of the syndrome distribution.

    H = -Σ p_i log₂ p_i

    H = 0 in the ideal noiseless limit (single dominant pattern).
    H increases monotonically with noise.

    Paper: Eq. (3), doi:10.5281/zenodo.21186260
    """
    H = 0.0
    for c in counts.values():
        p = c / shots
        if p > 0:
            H -= p * np.log2(p)
    return round(H, 4)


def invariant_fraction(counts: dict, shots: int) -> float:
    """
    Klein invariant fraction I = P(bit₀ = 1).

    Fraction of shots in which syndrome bit 0 fires.
    In the ideal noiseless b-anyon sector, I → 1.
    Deviations reflect errors on the qubit hosting the antipodal edge.

    Paper: Eq. (4), doi:10.5281/zenodo.21186260
    """
    n = sum(v for k, v in counts.items() if k[-1] == '1')
    return round(n / shots, 4)


def dominant_frequency(counts: dict, shots: int) -> tuple:
    """
    Dominant pattern and its frequency f = max_i p_i.

    Returns (dominant_pattern, f).

    Paper: Eq. (5), doi:10.5281/zenodo.21186260
    """
    dom = max(counts, key=counts.get)
    f   = counts[dom] / shots
    return dom, round(f, 4)


def z_raw(f: float, shots: int) -> float:
    """
    Statistical significance of dominant frequency vs uniform baseline.

    Z_raw = (f - p₀) / sqrt(p₀(1-p₀) / N_shots)

    A normalised deviation score under a multinomial baseline model.
    Suitable for hypothesis testing (is f significantly above chance?).
    NOT interchangeable with S.

    Paper: Eq. (6), doi:10.5281/zenodo.21186260
    """
    return round((f - P0) / (P0 * (1 - P0) / shots) ** 0.5, 1)


def probe_signal_score(z: float) -> float:
    """
    Normalized probe signal score S = clip(Z_raw / Z₀, 0, 1).

    Engineering indicator of probe signal strength.
    S = 1.0 → strong topological signal (Z_raw ≥ Z0 = 50).
    S < 0.5 → degraded signal, probe conditions unreliable.

    S is a relative, layout-conditioned indicator.
    It should not be interpreted as absolute hardware quality.
    NOT interchangeable with Z_raw.

    Paper: Eq. (7), doi:10.5281/zenodo.21186260
    """
    return round(min(max(z / Z0, 0.0), 1.0), 3)


def compute_all(counts: dict, shots: int) -> dict:
    """
    Compute all five KleinProbe metrics from a syndrome count dict.

    Returns:
        {
            'dominant': str,   # most frequent syndrome pattern
            'f':        float, # dominant frequency
            'H':        float, # syndrome entropy
            'inv':      float, # invariant fraction
            'Z_raw':    float, # statistical significance
            'S':        float, # probe signal score
        }

    This is the canonical computation path. All callers
    (probe.py, snapshot.py) should use this function.
    """
    dom, f = dominant_frequency(counts, shots)
    H      = syndrome_entropy(counts, shots)
    inv    = invariant_fraction(counts, shots)
    Z      = z_raw(f, shots)
    S      = probe_signal_score(Z)
    return {
        'dominant': dom,
        'f':        f,
        'H':        H,
        'inv':      inv,
        'Z_raw':    Z,
        'S':        S,
    }
