"""
kleinprobe/validity.py
=======================
Three-state validity model for KleinProbe measurements.

A probe measurement has one of three validity states:

    VALID_SPATIAL
        match=True, inv within normal range.
        Suitable for spatial ranking (r(H, 1-F_GHZ) meaningful).

    VALID_ANOMALOUS
        match=True, inv degraded OR H significantly elevated.
        Probe is correctly measuring the hardware — it has detected
        a real execution environment anomaly. This is a successful
        detection, not a probe failure.
        Not suitable for spatial ranking.
        Log anomaly class (see anomaly_taxonomy.md).

    INVALID
        match=False. Dominant syndrome pattern changed.
        H loses semantic meaning. Discard measurement.
        This is the ONLY state that warrants discarding.

Reference: docs/validity_model.md, docs/anomaly_taxonomy.md
Paper: doi:10.5281/zenodo.21186259
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List


# ── Validity state ─────────────────────────────────────────────────────────

VALID_SPATIAL    = "VALID_SPATIAL"
VALID_ANOMALOUS  = "VALID_ANOMALOUS"
INVALID          = "INVALID"

# Default per-chip inv thresholds (mean - 2σ of nominal sessions)
# Update as dataset grows.
_DEFAULT_INV_THRESHOLDS = {
    "ibm_fez":       0.851,   # Era 2 mean=0.889, std=0.019
    "ibm_kingston":  0.870,   # mean=0.890, std=0.010
    "ibm_marrakesh": 0.818,   # nominal mean=0.870, std=0.026
}
_FALLBACK_INV_THRESHOLD = 0.75


@dataclass
class ValidityResult:
    """
    Validity classification for a single probe tile measurement.

    Attributes:
        state:          VALID_SPATIAL | VALID_ANOMALOUS | INVALID
        reason:         human-readable explanation
        match:          whether dominant syndrome matched prediction
        inv:            measured invariant fraction
        H:              measured syndrome entropy
        inv_threshold:  threshold used for inv classification
        anomaly_class:  I / II / III / IV — None if VALID_SPATIAL
    """
    state:          str
    reason:         str
    match:          bool
    inv:            float
    H:              float
    inv_threshold:  float
    anomaly_class:  Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.state != INVALID

    @property
    def is_spatial(self) -> bool:
        return self.state == VALID_SPATIAL

    @property
    def is_anomalous(self) -> bool:
        return self.state == VALID_ANOMALOUS

    def status_icon(self) -> str:
        if self.state == VALID_SPATIAL:   return "✓ GOOD"
        if self.state == VALID_ANOMALOUS: return "⚠ WARNING"
        return "⛔ INVALID"

    def __repr__(self):
        return (f"ValidityResult({self.state}, "
                f"match={self.match}, inv={self.inv:.3f}, "
                f"H={self.H:.3f})")


@dataclass
class TiledValidityResult:
    """
    Aggregated validity for a multi-tile probe run.

    Attributes:
        tile_results:   per-tile ValidityResult list
        overall_state:  most restrictive state across all tiles
        valid_for_spatial_comparison: True only if ALL tiles are VALID_SPATIAL
        anomaly_scope:  'none' | 'local' | 'regional' | 'chipwide'
    """
    tile_results:   List[ValidityResult]

    @property
    def overall_state(self) -> str:
        if any(r.state == INVALID          for r in self.tile_results): return INVALID
        if any(r.state == VALID_ANOMALOUS  for r in self.tile_results): return VALID_ANOMALOUS
        return VALID_SPATIAL

    @property
    def valid_for_spatial_comparison(self) -> bool:
        return all(r.state == VALID_SPATIAL for r in self.tile_results)

    @property
    def anomaly_scope(self) -> str:
        n_anom = sum(1 for r in self.tile_results
                     if r.state != VALID_SPATIAL)
        n_total = len(self.tile_results)
        if n_anom == 0:              return "none"
        if n_anom == n_total:        return "chipwide"
        if n_anom >= n_total - 1:    return "regional"
        return "local"

    @property
    def anomaly_class(self) -> Optional[str]:
        scope = self.anomaly_scope
        invalid = any(r.state == INVALID for r in self.tile_results)
        if invalid:          return "IV"
        if scope == "chipwide": return "III"
        if scope == "regional": return "II"
        if scope == "local":    return "I"
        return None

    def execution_map(self, backend: str = "") -> str:
        """
        KleinAtlas-style execution map.
        Shows per-tile status, H, inv, and overall verdict.
        """
        header = f"KLEINATLAS EXECUTION MAP"
        if backend:
            header += f" — {backend}"
        lines = [
            header,
            f"  {'Tile':<6} {'Region':<20} {'Status':<16} "
            f"{'H':>8} {'inv':>8}",
            "  " + "-"*62,
        ]
        for i, r in enumerate(self.tile_results):
            region = getattr(r, '_region_label', f"tile_{i}")
            lines.append(
                f"  {i:<6} {region:<20} {r.status_icon():<16} "
                f"{r.H:>8.4f} {r.inv:>8.4f}")

        lines.append("")
        lines.append(f"  Overall: {self.overall_state}")
        scope = self.anomaly_scope
        if scope != "none":
            cls = self.anomaly_class
            lines.append(f"  Anomaly: Class {cls} — {scope}")

        if self.valid_for_spatial_comparison:
            lines.append(f"  ✓ Suitable for spatial H ranking")
        else:
            n_ok = sum(1 for r in self.tile_results
                       if r.state == VALID_SPATIAL)
            lines.append(
                f"  ✗ Spatial ranking not valid "
                f"({n_ok}/{len(self.tile_results)} tiles nominal)")

        return "\n".join(lines)

    def __repr__(self):
        return (f"TiledValidityResult(overall={self.overall_state}, "
                f"scope={self.anomaly_scope})")


# ── Classification functions ───────────────────────────────────────────────

def classify_tile(
    match:          bool,
    H:              float,
    inv:            float,
    backend:        str    = "",
    H_baseline:     Optional[float] = None,
    H_std:          Optional[float] = None,
    inv_threshold:  Optional[float] = None,
    n_sigma:        float  = 2.0,
    region_label:   str    = "",
) -> ValidityResult:
    """
    Classify a single tile probe measurement.

    Args:
        match:         True if dominant syndrome == '100001'
        H:             measured syndrome entropy
        inv:           measured invariant fraction
        backend:       backend name (used for default thresholds)
        H_baseline:    expected H mean (from baseline)
        H_std:         expected H std (from baseline)
        inv_threshold: inv threshold below which tile is VALID_ANOMALOUS
                       (defaults to chip-specific value)
        n_sigma:       number of sigma for H alert (default 2.0)
        region_label:  tile region label for reporting

    Returns:
        ValidityResult
    """
    # Determine inv threshold
    if inv_threshold is None:
        inv_threshold = _DEFAULT_INV_THRESHOLDS.get(
            backend.lower(), _FALLBACK_INV_THRESHOLD)

    # INVALID: semantic failure
    if not match:
        r = ValidityResult(
            state         = INVALID,
            reason        = ("Pattern mismatch — dominant syndrome changed. "
                             "H loses semantic meaning. Discard measurement."),
            match         = match,
            inv           = inv,
            H             = H,
            inv_threshold = inv_threshold,
            anomaly_class = "IV",
        )
        r._region_label = region_label
        return r

    # Check inv
    if inv < inv_threshold:
        r = ValidityResult(
            state         = VALID_ANOMALOUS,
            reason        = (f"inv={inv:.3f} below threshold {inv_threshold:.3f}. "
                             f"Antipodal edge qubit degraded. "
                             f"Probe detecting real hardware anomaly."),
            match         = match,
            inv           = inv,
            H             = H,
            inv_threshold = inv_threshold,
        )
        r._region_label = region_label
        return r

    # Check H (optional — only if baseline provided)
    if H_baseline is not None and H_std is not None:
        H_thresh = H_baseline + n_sigma * max(H_std, 0.05)
        if H > H_thresh:
            r = ValidityResult(
                state         = VALID_ANOMALOUS,
                reason        = (f"H={H:.3f} above threshold {H_thresh:.3f} "
                                 f"({n_sigma}σ above baseline {H_baseline:.3f}). "
                                 f"Elevated syndrome entropy."),
                match         = match,
                inv           = inv,
                H             = H,
                inv_threshold = inv_threshold,
            )
            r._region_label = region_label
            return r

    # All checks passed
    r = ValidityResult(
        state         = VALID_SPATIAL,
        reason        = "Nominal — suitable for spatial comparison.",
        match         = match,
        inv           = inv,
        H             = H,
        inv_threshold = inv_threshold,
    )
    r._region_label = region_label
    return r


def classify_tiled(
    tile_results: List[dict],
    backend:      str   = "",
    **kwargs,
) -> TiledValidityResult:
    """
    Classify all tiles in a tiled probe run.

    Args:
        tile_results:  list of dicts with keys: match, H, inv, region_label
        backend:       backend name
        **kwargs:      passed to classify_tile (H_baseline, H_std, etc.)

    Returns:
        TiledValidityResult
    """
    results = []
    for t in tile_results:
        r = classify_tile(
            match        = t.get('match', False),
            H            = t.get('H', 0.0),
            inv          = t.get('inv', 0.0),
            backend      = backend,
            region_label = t.get('region_label', ''),
            **kwargs,
        )
        results.append(r)
    return TiledValidityResult(tile_results=results)
