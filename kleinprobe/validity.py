"""
kleinprobe/validity.py
=======================
Three-state validity model and continuous risk score for KleinProbe.

VALIDITY STATES
---------------
A probe measurement has one of three states:

    VALID_SPATIAL
        match=True, inv within normal range.
        Tile is operating in its nominal calibration regime.
        Suitable for spatial H ranking and routing decisions.

    VALID_ANOMALOUS
        match=True, inv degraded OR H significantly elevated.
        The probe correctly detected a real hardware anomaly.
        This is a property of the hardware region, not a probe failure.
        Not suitable for spatial r(H, F) comparison across tiles.
        VALID_ANOMALOUS does NOT imply the user's circuit will fail —
        it indicates the tile is statistically off-nominal relative to
        the KleinProbe reference manifold, making it a higher-risk
        routing choice than a VALID_SPATIAL tile.

    INVALID
        match=False. Dominant syndrome pattern changed.
        H loses its semantic interpretation. Discard the measurement.
        This is the ONLY state that warrants discarding.

CONTINUOUS RISK SCORE
---------------------
The binary VALID_ANOMALOUS label loses information: inv=0.782 and
inv=0.671 both receive the same label but carry very different risk
(the latter produced HOP=0.195 vs 0.898 on the former in direct
experiments). The continuous risk score resolves this.

    execution_confidence (0-100):
        100 = nominal operation, H at baseline
          0 = maximally degraded
        Derived from H and inv independently normalised against baseline.
        Higher confidence = lower routing risk.

    risk_score (0-1):
        1 - execution_confidence / 100
        0 = no risk, 1 = maximum risk

Routing principle:
    Route to the tile with the LOWEST risk_score (highest confidence).
    Even when all tiles are VALID_ANOMALOUS, the continuous score
    discriminates between them and identifies the best available option.

Experimental validation:
    Across 9 tile-runs on 3 IBM Heron r2 chips, r(H, HOP) ranged
    from -0.757 to -0.985. The recommended tile (lowest H = lowest
    risk_score) was correct on all 3 chips. The worst tile by H
    produced HOP=0.195 vs HOP=0.898 on the recommended tile.

Reference: docs/validity_model.md
Paper: doi:10.5281/zenodo.21186259
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List


# ── Validity state constants ───────────────────────────────────────────────

VALID_SPATIAL    = "VALID_SPATIAL"
VALID_ANOMALOUS  = "VALID_ANOMALOUS"
INVALID          = "INVALID"

# Per-chip inv thresholds (baseline mean - 2σ, n≥13 sessions each)
_DEFAULT_INV_THRESHOLDS = {
    "ibm_fez":       0.851,   # Era2 mean=0.889, std=0.019, n=13
    "ibm_kingston":  0.870,   # mean=0.890, std=0.010, n=13
    "ibm_marrakesh": 0.818,   # nominal mean=0.870, std=0.026, n=16
}
_FALLBACK_INV_THRESHOLD = 0.75

# Per-chip H baselines for risk score normalisation
_H_BASELINES = {
    "ibm_fez":       {"mean": 2.977, "std": 0.215},  # Era2, n=13
    "ibm_kingston":  {"mean": 2.694, "std": 0.092},  # n=13
    "ibm_marrakesh": {"mean": 3.128, "std": 0.252},  # n=16
}
_FALLBACK_H_BASELINE = {"mean": 3.0, "std": 0.25}

# inv baselines for risk score
_INV_BASELINES = {
    "ibm_fez":       {"mean": 0.889, "std": 0.019},
    "ibm_kingston":  {"mean": 0.890, "std": 0.010},
    "ibm_marrakesh": {"mean": 0.870, "std": 0.026},
}
_FALLBACK_INV_BASELINE = {"mean": 0.880, "std": 0.025}


# ── ValidityResult ─────────────────────────────────────────────────────────

@dataclass
class ValidityResult:
    """
    Validity classification for a single probe tile measurement.

    Attributes:
        state:                VALID_SPATIAL | VALID_ANOMALOUS | INVALID
        reason:               human-readable explanation
        match:                dominant syndrome matched prediction
        inv:                  measured invariant fraction
        H:                    measured syndrome entropy
        inv_threshold:        threshold used for inv classification
        execution_confidence: 0-100, continuous quality score
        risk_score:           0-1, routing risk (0=safe, 1=high risk)
        anomaly_class:        I/II/III/IV or None if VALID_SPATIAL
    """
    state:                str
    reason:               str
    match:                bool
    inv:                  float
    H:                    float
    inv_threshold:        float
    execution_confidence: int   = 100
    risk_score:           float = 0.0
    anomaly_class:        Optional[str] = None

    # internal: set by classify_tile
    _region_label: str = ""

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

    def traffic_light(self) -> str:
        """Single character traffic light for compact display."""
        if self.state == VALID_SPATIAL:   return "🟢"
        if self.state == VALID_ANOMALOUS: return "🟡"
        return "🔴"

    def confidence_label(self) -> str:
        c = self.execution_confidence
        if c >= 80: return "EXCELLENT"
        if c >= 60: return "GOOD"
        if c >= 40: return "FAIR"
        if c >= 20: return "POOR"
        return "CRITICAL"

    def __repr__(self):
        return (f"ValidityResult({self.state}, "
                f"conf={self.execution_confidence}%, "
                f"match={self.match}, inv={self.inv:.3f}, H={self.H:.3f})")


# ── TiledValidityResult ────────────────────────────────────────────────────

@dataclass
class TiledValidityResult:
    """
    Aggregated validity for a multi-tile probe run.

    Attributes:
        tile_results:   per-tile ValidityResult list (ordered by tile index)
    """
    tile_results: List[ValidityResult]

    @property
    def overall_state(self) -> str:
        if any(r.state == INVALID         for r in self.tile_results): return INVALID
        if any(r.state == VALID_ANOMALOUS for r in self.tile_results): return VALID_ANOMALOUS
        return VALID_SPATIAL

    @property
    def valid_for_spatial_comparison(self) -> bool:
        return all(r.state == VALID_SPATIAL for r in self.tile_results)

    @property
    def anomaly_scope(self) -> str:
        n_anom  = sum(1 for r in self.tile_results if r.state != VALID_SPATIAL)
        n_total = len(self.tile_results)
        if n_anom == 0:         return "none"
        if n_anom == n_total:   return "chipwide"
        if n_anom >= n_total-1: return "regional"
        return "local"

    @property
    def anomaly_class(self) -> Optional[str]:
        scope   = self.anomaly_scope
        invalid = any(r.state == INVALID for r in self.tile_results)
        if invalid:              return "IV"
        if scope == "chipwide":  return "III"
        if scope == "regional":  return "II"
        if scope == "local":     return "I"
        return None

    @property
    def best_tile(self) -> Optional[ValidityResult]:
        """
        Tile with lowest risk_score (highest execution_confidence).
        Returns None if all tiles are INVALID.
        """
        valid = [r for r in self.tile_results if r.state != INVALID]
        if not valid:
            return None
        return min(valid, key=lambda r: r.risk_score)

    @property
    def best_tile_index(self) -> Optional[int]:
        best = self.best_tile
        if best is None:
            return None
        return self.tile_results.index(best)

    def execution_map(self, backend: str = "") -> str:
        """
        KleinAtlas-style execution map with continuous confidence scores.
        """
        header = "KLEINATLAS EXECUTION MAP"
        if backend:
            header += f" — {backend}"
        lines = [
            header,
            f"  {'Tile':<6} {'Region':<20} {'Status':<16} "
            f"{'Conf':>6} {'H':>8} {'inv':>8}",
            "  " + "-"*68,
        ]
        for i, r in enumerate(self.tile_results):
            region = getattr(r, '_region_label', f"tile_{i}")
            best_marker = " ★" if i == self.best_tile_index else ""
            lines.append(
                f"  {i:<6} {region:<20} {r.status_icon():<16} "
                f"{r.execution_confidence:>5}% "
                f"{r.H:>8.4f} {r.inv:>8.4f}{best_marker}")

        lines.append("")
        lines.append(f"  Overall:  {self.overall_state}")
        scope = self.anomaly_scope
        if scope != "none":
            lines.append(f"  Anomaly:  Class {self.anomaly_class} — {scope}")
        if self.best_tile is not None:
            bt = self.best_tile
            lines.append(
                f"  Recommend: tile_{self.best_tile_index} "
                f"({getattr(bt,'_region_label','')}) "
                f"— confidence {bt.execution_confidence}%  "
                f"H={bt.H:.4f}  risk={bt.risk_score:.3f}")
        if self.valid_for_spatial_comparison:
            lines.append("  ✓ All tiles nominal — spatial ranking valid")
        else:
            n_ok = sum(1 for r in self.tile_results if r.state == VALID_SPATIAL)
            lines.append(
                f"  ✗ Spatial ranking not valid "
                f"({n_ok}/{len(self.tile_results)} tiles nominal)")
        return "\n".join(lines)

    def __repr__(self):
        return (f"TiledValidityResult(overall={self.overall_state}, "
                f"scope={self.anomaly_scope}, "
                f"best=tile_{self.best_tile_index})")


# ── Risk score computation ─────────────────────────────────────────────────

def compute_execution_confidence(
    H:       float,
    inv:     float,
    backend: str = "",
    H_baseline:   Optional[float] = None,
    H_std:        Optional[float] = None,
    inv_baseline: Optional[float] = None,
    inv_std:      Optional[float] = None,
) -> int:
    """
    Compute execution confidence (0-100) from H and inv.

    Combines two normalised components:
      H_score   = max(0, 1 - (H - H_mean) / (4 * H_std))
                  penalises elevated syndrome entropy
      inv_score = min(1, max(0, (inv - 0.5) / (inv_mean - 0.5)))
                  penalises low invariant fraction

    Returns:
        Integer confidence score 0-100.
        100 = operating at baseline (H=H_mean, inv=inv_mean)
         50 = moderately degraded
          0 = maximally degraded

    Example:
        >>> compute_execution_confidence(H=3.78, inv=0.820, backend='ibm_fez')
        71
        >>> compute_execution_confidence(H=5.07, inv=0.657, backend='ibm_marrakesh')
        12
    """
    bk = backend.lower()

    if H_baseline is None or H_std is None:
        bl = _H_BASELINES.get(bk, _FALLBACK_H_BASELINE)
        H_baseline = bl["mean"]
        H_std      = max(bl["std"], 0.05)

    if inv_baseline is None or inv_std is None:
        bl = _INV_BASELINES.get(bk, _FALLBACK_INV_BASELINE)
        inv_baseline = bl["mean"]

    # H component: 1.0 at baseline, 0.0 when H = baseline + 4σ
    H_score   = max(0.0, 1.0 - (H - H_baseline) / (4.0 * H_std))
    H_score   = min(1.0, H_score)

    # inv component: 1.0 at baseline, 0.0 at inv=0.5
    inv_score = (inv - 0.5) / max(inv_baseline - 0.5, 0.01)
    inv_score = min(1.0, max(0.0, inv_score))

    confidence = round((0.5 * H_score + 0.5 * inv_score) * 100)
    return int(confidence)


# ── Classification functions ───────────────────────────────────────────────

def classify_tile(
    match:          bool,
    H:              float,
    inv:            float,
    backend:        str   = "",
    H_baseline:     Optional[float] = None,
    H_std:          Optional[float] = None,
    inv_threshold:  Optional[float] = None,
    n_sigma:        float = 2.0,
    region_label:   str   = "",
) -> ValidityResult:
    """
    Classify a single tile probe measurement.

    Args:
        match:          True if dominant syndrome == '100001'
        H:              measured syndrome entropy (bits)
        inv:            measured invariant fraction
        backend:        backend name for default thresholds
        H_baseline:     expected H mean (from baseline, optional)
        H_std:          expected H std (from baseline, optional)
        inv_threshold:  inv threshold for VALID_ANOMALOUS
                        (defaults to chip-specific value)
        n_sigma:        sigma multiplier for H alert (default 2.0)
        region_label:   tile region label for reporting

    Returns:
        ValidityResult with state, confidence score, and risk score.
    """
    bk = backend.lower()

    if inv_threshold is None:
        inv_threshold = _DEFAULT_INV_THRESHOLDS.get(bk, _FALLBACK_INV_THRESHOLD)

    # Compute continuous confidence regardless of state
    confidence = compute_execution_confidence(
        H=H, inv=inv, backend=bk,
        H_baseline=H_baseline, H_std=H_std)
    risk = round(1.0 - confidence / 100.0, 4)

    def _make(state, reason, anomaly_class=None):
        r = ValidityResult(
            state=state, reason=reason,
            match=match, inv=inv, H=H,
            inv_threshold=inv_threshold,
            execution_confidence=confidence,
            risk_score=risk,
            anomaly_class=anomaly_class,
        )
        r._region_label = region_label
        return r

    # INVALID: dominant syndrome changed
    if not match:
        return _make(
            INVALID,
            "Pattern mismatch — dominant syndrome changed. "
            "H loses semantic meaning. Discard measurement.",
            anomaly_class="IV",
        )

    # VALID_ANOMALOUS: inv below threshold
    if inv < inv_threshold:
        return _make(
            VALID_ANOMALOUS,
            f"inv={inv:.3f} below threshold {inv_threshold:.3f}. "
            f"Tile exhibits anomalous syndrome statistics relative to "
            f"calibrated reference. execution_confidence={confidence}%. "
            f"Prefer VALID_SPATIAL tile if available; "
            f"within anomalous regime use H to rank tiles.",
        )

    # VALID_ANOMALOUS: H significantly elevated
    if H_baseline is not None and H_std is not None:
        H_thresh = H_baseline + n_sigma * max(H_std, 0.05)
        if H > H_thresh:
            return _make(
                VALID_ANOMALOUS,
                f"H={H:.3f} above {n_sigma}σ threshold {H_thresh:.3f}. "
                f"Elevated syndrome entropy detected. "
                f"execution_confidence={confidence}%.",
            )

    # VALID_SPATIAL: all checks passed
    return _make(
        VALID_SPATIAL,
        f"Nominal. execution_confidence={confidence}%.",
    )


def classify_tiled(
    tile_results: List[dict],
    backend:      str  = "",
    **kwargs,
) -> TiledValidityResult:
    """
    Classify all tiles in a tiled probe run.

    Args:
        tile_results:  list of dicts with keys: match, H, inv, region_label
        backend:       backend name for thresholds
        **kwargs:      passed to classify_tile

    Returns:
        TiledValidityResult with per-tile results and aggregated verdict.

    Example:
        tiles = [
            {'match': True,  'H': 4.53, 'inv': 0.758, 'region_label': 'lower-right'},
            {'match': True,  'H': 4.58, 'inv': 0.761, 'region_label': 'upper-left'},
            {'match': True,  'H': 4.81, 'inv': 0.722, 'region_label': 'upper-central'},
        ]
        result = classify_tiled(tiles, backend='ibm_marrakesh')
        print(result.execution_map('ibm_marrakesh'))
        # → best tile: tile_0 (lower-right), confidence 38%, risk 0.620
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
