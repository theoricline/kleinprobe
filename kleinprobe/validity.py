"""
kleinprobe/validity.py
=======================
Four-state hardware execution environment model for KleinProbe.

KleinProbe is a hardware state estimator, not a circuit correctness
validator. The states below describe the local execution environment
of a chip region — not whether a user's circuit will succeed or fail.
Application fidelity is one downstream consequence of the execution
environment, not the quantity directly measured.

HARDWARE STATE MODEL
--------------------

    OPTIMAL
        match=True, inv within normal range, H near baseline.
        The execution environment is operating within its
        calibrated reference range. Preferred routing target.

    ELEVATED
        match=True, inv slightly below threshold OR H above
        baseline + 1σ. The execution environment has drifted
        from its calibrated reference. Still a valid routing
        target when no OPTIMAL tile is available. Application
        circuits often execute well in this state — the probe
        is detecting a shift in the hardware environment before
        it becomes visible in circuit outcomes.

    CRITICAL
        match=True, inv significantly below threshold OR H
        above baseline + 3σ. The execution environment is
        substantially shifted from its calibrated reference.
        Avoid if alternatives exist. Application circuits show
        measurably degraded outcomes in this state.

    INVALID
        match=False. The dominant syndrome pattern has changed.
        H loses its interpretation as a hardware state estimator.
        Discard this tile measurement. This is the only state
        that warrants discarding.

ROUTING PRINCIPLE
-----------------
Route to the tile with the lowest risk_index (highest env_score).
State priority: OPTIMAL > ELEVATED > CRITICAL > INVALID.
Within the same state, use H to rank tiles — lower H = better
execution environment. This ranking holds even when all tiles
are ELEVATED or CRITICAL (Marrakesh experiments: r(H,HOP) = -0.985).

PREDICTIVE MONITORING
---------------------
H rising across sessions is a leading indicator of execution
environment degradation — it detects drift before application
fidelity becomes unacceptable. This mirrors predictive maintenance
in classical systems: the probe signal warns before failure.

BACKWARD COMPATIBILITY
----------------------
The old three-state names are kept as aliases:
    VALID_SPATIAL   = OPTIMAL
    VALID_ANOMALOUS = ELEVATED   (approximate — loses CRITICAL distinction)
    INVALID         = INVALID

Paper: doi:10.5281/zenodo.21186259
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List

# ── State constants ────────────────────────────────────────────────────────

OPTIMAL  = "OPTIMAL"
ELEVATED = "ELEVATED"
CRITICAL = "CRITICAL"
INVALID  = "INVALID"

# Backward compatibility aliases
VALID_SPATIAL   = OPTIMAL
VALID_ANOMALOUS = ELEVATED   # approximate

# ── Per-chip thresholds ────────────────────────────────────────────────────
# inv thresholds: baseline mean - 2σ (ELEVATED boundary)
# inv_critical:   baseline mean - 4σ (CRITICAL boundary)
# H baselines for confidence score and ELEVATED/CRITICAL split

_CHIP_CONFIG = {
    "ibm_fez": {
        "inv_elevated": 0.851,   # mean=0.889, std=0.019, n=13 — mean-2σ
        "inv_critical": 0.794,   # mean-5σ
        "H_mean":       2.977,
        "H_std":        0.215,
        "inv_mean":     0.889,
    },
    "ibm_kingston": {
        "inv_elevated": 0.870,   # mean=0.890, std=0.010, n=13
        "inv_critical": 0.750,   # hard floor — Kingston inv varies little
        "H_mean":       2.694,
        "H_std":        0.092,
        "inv_mean":     0.890,
    },
    "ibm_marrakesh": {
        "inv_elevated": 0.818,   # nominal mean=0.870, std=0.026, n=16
        "inv_critical": 0.740,   # mean-5σ, boundary between ELEVATED and CRITICAL
        "H_mean":       3.128,
        "H_std":        0.252,
        "inv_mean":     0.870,
    },
}

_FALLBACK_CONFIG = {
    "inv_elevated": 0.75,
    "inv_critical": 0.65,
    "H_mean":       3.0,
    "H_std":        0.25,
    "inv_mean":     0.880,
}

# ── ValidityResult ─────────────────────────────────────────────────────────

@dataclass
class ValidityResult:
    """
    Hardware execution environment state for a single probe tile.

    Attributes:
        state:                OPTIMAL | ELEVATED | CRITICAL | INVALID
        reason:               human-readable explanation
        match:                dominant syndrome matched prediction
        inv:                  measured invariant fraction
        H:                    measured syndrome entropy (bits)
        inv_threshold:        ELEVATED boundary threshold used
        env_score: 0-100 continuous quality score
        risk_index:           0.0-1.0 routing risk (0=optimal, 1=critical)
    """
    state:                str
    reason:               str
    match:                bool
    inv:                  float
    H:                    float
    inv_threshold:        float
    env_score: float = 1.0
    risk_index:           float = 0.0
    _region_label:        str   = ""

    @property
    def is_usable(self) -> bool:
        """True for OPTIMAL, ELEVATED, CRITICAL — any state with valid H."""
        return self.state != INVALID

    @property
    def is_optimal(self) -> bool:
        return self.state == OPTIMAL

    @property
    def is_elevated(self) -> bool:
        return self.state == ELEVATED

    @property
    def is_critical(self) -> bool:
        return self.state == CRITICAL

    # ── Legacy compatibility ───────────────────────────────────────────
    @property
    def is_valid(self) -> bool:
        return self.is_usable

    @property
    def is_spatial(self) -> bool:
        return self.is_optimal

    @property
    def is_anomalous(self) -> bool:
        return self.state in (ELEVATED, CRITICAL)

    def status_icon(self) -> str:
        return {
            OPTIMAL:  "✓ OPTIMAL",
            ELEVATED: "~ ELEVATED",
            CRITICAL: "⚠ CRITICAL",
            INVALID:  "⛔ INVALID",
        }.get(self.state, "?")

    def traffic_light(self) -> str:
        return {
            OPTIMAL: "🟢", ELEVATED: "🟡",
            CRITICAL: "🔴", INVALID: "⬛",
        }.get(self.state, "?")

    def env_label(self) -> str:
        c = int(self.env_score * 100)
        if c >= 80: return "EXCELLENT"
        if c >= 60: return "GOOD"
        if c >= 40: return "FAIR"
        if c >= 20: return "POOR"
        return "CRITICAL"

    def __repr__(self):
        return (f"ValidityResult({self.state}, "
                f"env={self.env_score:.2f}, "
                f"H={self.H:.3f}, inv={self.inv:.3f})")


# ── TiledValidityResult ────────────────────────────────────────────────────

@dataclass
class TiledValidityResult:
    """Aggregated hardware state for a multi-tile probe run."""
    tile_results: List[ValidityResult]

    @property
    def overall_state(self) -> str:
        """Most severe state across all tiles."""
        if any(r.state == INVALID   for r in self.tile_results): return INVALID
        if any(r.state == CRITICAL  for r in self.tile_results): return CRITICAL
        if any(r.state == ELEVATED  for r in self.tile_results): return ELEVATED
        return OPTIMAL

    @property
    def scope(self) -> str:
        """How many tiles are non-OPTIMAL."""
        n_non = sum(1 for r in self.tile_results if r.state != OPTIMAL)
        n     = len(self.tile_results)
        if n_non == 0:       return "none"
        if n_non == n:       return "chipwide"
        if n_non >= n - 1:   return "regional"
        return "local"

    @property
    def best_tile(self) -> Optional[ValidityResult]:
        """Tile with lowest risk_score. None only if all INVALID."""
        usable = [r for r in self.tile_results if r.state != INVALID]
        if not usable:
            return None
        return min(usable, key=lambda r: r.risk_index)

    @property
    def best_tile_index(self) -> Optional[int]:
        best = self.best_tile
        return self.tile_results.index(best) if best is not None else None

    # Legacy
    @property
    def valid_for_spatial_comparison(self) -> bool:
        return all(r.state == OPTIMAL for r in self.tile_results)

    @property
    def anomaly_class(self) -> Optional[str]:
        scope   = self.scope
        invalid = any(r.state == INVALID for r in self.tile_results)
        if invalid:             return "IV"
        if scope == "chipwide": return "III"
        if scope == "regional": return "II"
        if scope == "local":    return "I"
        return None

    def execution_map(self, backend: str = "") -> str:
        """KleinAtlas execution map with hardware state per tile."""
        header = "KLEINATLAS EXECUTION MAP"
        if backend:
            header += f" — {backend}"
        lines = [
            header,
            f"  {'Tile':<6} {'Region':<20} {'State':<16} "
            f"{'Env':>6} {'H':>8} {'inv':>8}",
            "  " + "-"*68,
        ]
        for i, r in enumerate(self.tile_results):
            region = getattr(r, '_region_label', f"tile_{i}")
            star   = " ★" if i == self.best_tile_index else ""
            lines.append(
                f"  {i:<6} {region:<20} {r.status_icon():<16} "
                f"{r.env_score:>6.2f} "
                f"{r.H:>8.4f} {r.inv:>8.4f}{star}")

        lines.append("")
        lines.append(f"  Overall:  {self.overall_state}  ({self.scope})")
        if self.anomaly_class:
            lines.append(f"  Anomaly class: {self.anomaly_class}")
        if self.best_tile is not None:
            bt = self.best_tile
            lines.append(
                f"  Recommend: tile_{self.best_tile_index} "
                f"({getattr(bt,'_region_label','')})  "
                f"env={bt.env_score:.2f}  "
                f"H={bt.H:.4f}  risk={bt.risk_index:.3f}")
        return "\n".join(lines)

    def __repr__(self):
        return (f"TiledValidityResult(overall={self.overall_state}, "
                f"scope={self.scope}, best=tile_{self.best_tile_index})")


# ── Confidence score ───────────────────────────────────────────────────────

def compute_env_score(
    H:       float,
    inv:     float,
    backend: str   = "",
    H_baseline:   Optional[float] = None,
    H_std:        Optional[float] = None,
    inv_baseline: Optional[float] = None,
) -> int:
    """
    Compute environment score (0.0-1.0).

    1.0 = execution environment at calibrated reference (OPTIMAL)
    0.6 = ELEVATED regime
    0.3 = CRITICAL regime
    0.0 = maximally shifted

    Components:
      H_score   = max(0, 1 - (H - H_mean) / (4 * H_std))
      inv_score = min(1, (inv - 0.5) / (inv_mean - 0.5))
      env_score  = round(0.5 * H_score + 0.5 * inv_score, 3)
    """
    bk  = backend.lower()
    cfg = _CHIP_CONFIG.get(bk, _FALLBACK_CONFIG)

    H_mean   = H_baseline   if H_baseline   is not None else cfg["H_mean"]
    H_sigma  = H_std        if H_std        is not None else max(cfg["H_std"], 0.05)
    inv_mean = inv_baseline if inv_baseline is not None else cfg["inv_mean"]

    H_score   = max(0.0, min(1.0, 1.0 - (H - H_mean) / (4.0 * H_sigma)))
    inv_score = max(0.0, min(1.0, (inv - 0.5) / max(inv_mean - 0.5, 0.01)))

    return round(0.5 * H_score + 0.5 * inv_score, 3)


# ── Classification ─────────────────────────────────────────────────────────

def classify_tile(
    match:         bool,
    H:             float,
    inv:           float,
    backend:       str   = "",
    H_baseline:    Optional[float] = None,
    H_std:         Optional[float] = None,
    inv_threshold: Optional[float] = None,
    region_label:  str   = "",
) -> ValidityResult:
    """
    Classify the hardware execution environment of a single probe tile.

    State boundaries (probe metrics only — no circuit outcome required):

        OPTIMAL:   match=True  AND  inv ≥ inv_elevated  AND  H ≤ mean+1σ
        ELEVATED:  match=True  AND  inv ≥ inv_critical  AND  (inv < inv_elevated
                                                               OR H > mean+1σ)
        CRITICAL:  match=True  AND  (inv < inv_critical OR H > mean+3σ)
        INVALID:   match=False

    Args:
        match:          True if dominant syndrome == '100001'
        H:              syndrome entropy (bits)
        inv:            invariant fraction
        backend:        chip name for default thresholds
        H_baseline:     override H mean
        H_std:          override H std
        inv_threshold:  override ELEVATED boundary (default: chip-specific)
        region_label:   tile region label for execution map display

    Returns:
        ValidityResult with state, confidence, and risk_score.
    """
    bk  = backend.lower()
    cfg = _CHIP_CONFIG.get(bk, _FALLBACK_CONFIG)

    inv_elevated = inv_threshold if inv_threshold is not None else cfg["inv_elevated"]
    inv_critical = cfg["inv_critical"]
    H_mean       = H_baseline if H_baseline is not None else cfg["H_mean"]
    H_sigma      = H_std      if H_std      is not None else max(cfg["H_std"], 0.05)
    H_1sigma     = H_mean + max(1.0 * H_sigma, 0.30)  # min 0.3 bits above mean
    H_3sigma     = H_mean + max(6.0 * H_sigma, 1.50)  # min 1.5 bits above mean

    env_score  = compute_env_score(H, inv, bk, H_baseline, H_std)
    risk       = round(1.0 - env_score, 3)

    def _r(state, reason):
        r = ValidityResult(
            state=state, reason=reason,
            match=match, inv=inv, H=H,
            inv_threshold=inv_elevated,
            env_score=env_score,
            risk_index=risk,
        )
        r._region_label = region_label
        return r

    # INVALID
    if not match:
        return _r(INVALID,
            "Pattern mismatch — dominant syndrome changed. "
            "H is not interpretable as a hardware state estimator. "
            "Discard this tile.")

    # CRITICAL: inv far below threshold OR H >> baseline
    if inv < inv_critical or H > H_3sigma:
        return _r(CRITICAL,
            f"Execution environment significantly shifted from reference. "
            f"inv={inv:.3f} (critical threshold {inv_critical:.3f}) "
            f"H={H:.3f} (3σ threshold {H_3sigma:.3f}). "
            f"Avoid if alternatives exist. env={env_score:.2f}.")

    # ELEVATED: inv slightly below threshold OR H > 1σ above baseline
    if inv < inv_elevated or H > H_1sigma:
        return _r(ELEVATED,
            f"Execution environment drifted from calibrated reference. "
            f"inv={inv:.3f} (elevated threshold {inv_elevated:.3f}) "
            f"H={H:.3f} (1σ threshold {H_1sigma:.3f}). "
            f"Usable; lower priority than OPTIMAL. env={env_score:.2f}.")

    # OPTIMAL
    return _r(OPTIMAL,
        f"Execution environment within calibrated reference range. "
        f"env={env_score:.2f}.")


def classify_tiled(
    tile_results: List[dict],
    backend:      str  = "",
    **kwargs,
) -> TiledValidityResult:
    """
    Classify all tiles in a tiled probe run.

    Args:
        tile_results: list of dicts with keys: match, H, inv, region_label
        backend:      chip name
        **kwargs:     passed to classify_tile

    Returns:
        TiledValidityResult

    Example:
        tiles = [
            {'match':True, 'H':4.529, 'inv':0.758, 'region_label':'lower-right'},
            {'match':True, 'H':4.584, 'inv':0.761, 'region_label':'upper-left'},
            {'match':True, 'H':4.807, 'inv':0.722, 'region_label':'upper-central'},
        ]
        result = classify_tiled(tiles, backend='ibm_marrakesh')
        print(result.execution_map('ibm_marrakesh'))
        # Recommend: tile_0 (lower-right) — conf=35%  H=4.5290  risk=0.650
    """
    results = [
        classify_tile(
            match        = t.get('match', False),
            H            = t.get('H', 0.0),
            inv          = t.get('inv', 0.0),
            backend      = backend,
            region_label = t.get('region_label', ''),
            **kwargs,
        )
        for t in tile_results
    ]
    return TiledValidityResult(tile_results=results)


# ── Layout Match Score ─────────────────────────────────────────────────────

def compute_lms(
    probe_qubits: list,
    app_physical_qubits: list,
) -> float:
    """
    Layout Match Score — confidence metric, not quality metric.

    Answers: "How representative is this probe measurement
    of the target circuit's execution environment?"
    NOT: "How good is the hardware?"

    LMS = |probe ∩ app| / |app|

    Range [0.0, 1.0]:
      1.0 = probe fully covers app circuit footprint (direct measurement)
      0.0 = probe and app share no physical qubits (not representative)

    LMS is a property of the circuit layout, not the hardware state.
    The same probe tile gives different LMS values for different circuits.

    Interpretation by circuit width (approximate):
      6–18 qubits    →  LMS 0.90–1.00  →  Direct measurement
      20–40 qubits   →  LMS 0.50–0.80  →  High-confidence estimate
      40–80 qubits   →  LMS 0.25–0.50  →  Moderate regional estimate
      80–156 qubits  →  LMS 0.10–0.25  →  Global orientation only

    Note: LMS depends on WHERE qubits are located, not just how many.
    A 60-qubit circuit concentrated in one region may have higher LMS
    than a 30-qubit circuit spread across the chip.
    """
    if not app_physical_qubits:
        return 0.0
    probe_set = set(probe_qubits)
    app_set   = set(app_physical_qubits)
    return round(len(probe_set & app_set) / len(app_set), 3)


def lms_label(lms: float) -> str:
    """Human-readable LMS confidence label."""
    if lms >= 0.85: return "DIRECT"
    if lms >= 0.50: return "HIGH"
    if lms >= 0.25: return "MODERATE"
    if lms >= 0.10: return "LOW"
    return "MINIMAL"


def effective_score(env: float, lms: float) -> float:
    """
    Confidence-attenuated environment score for forced tile ranking.

    R_eff = env * (0.5 + 0.5 * LMS)

    Use only when a single ranking number is required.
    Prefer exposing env_score and LMS independently in reports —
    they answer different questions and should not be collapsed.

    Behaviour:
      LMS = 1.0  →  R_eff = env          (full confidence)
      LMS = 0.6  →  R_eff = 0.80 * env  (moderate attenuation)
      LMS = 0.2  →  R_eff = 0.60 * env  (conservative)
      LMS = 0.0  →  R_eff = 0.50 * env  (probe never fully discarded)
    """
    return round(env * (0.5 + 0.5 * lms), 3)


def _env_label(env: float) -> str:
    if env >= 0.75: return "EXCELLENT"
    if env >= 0.55: return "GOOD"
    if env >= 0.35: return "FAIR"
    if env >= 0.15: return "POOR"
    return "CRITICAL"


def routing_report(
    tile_name: str,
    region:    str,
    env:       float,
    lms:       float,
    state:     str,
) -> str:
    """
    Weather-forecast style routing report.

    Exposes env_score and LMS as two independent axes:
      Environment  — how good is this hardware region?
      Coverage     — how representative is this probe of your circuit?

    Example:
      tile_0 · upper-left
      ─────────────────────────────────────────
      Environment   [████░░░░░░]  0.41  FAIR
      Coverage      [██████████]  1.00  DIRECT
      State         ELEVATED
      Effective     0.41
    """
    env_bar = "█" * int(env * 10) + "░" * (10 - int(env * 10))
    lms_bar = "█" * int(lms * 10) + "░" * (10 - int(lms * 10))
    eff     = effective_score(env, lms)
    lines   = [
        f"  {tile_name} · {region}",
        f"  {'─'*41}",
        f"  Environment   [{env_bar}]  {env:.2f}  {_env_label(env)}",
        f"  Coverage      [{lms_bar}]  {lms:.2f}  {lms_label(lms)}",
        f"  State         {state}",
        f"  Effective     {eff:.2f}",
    ]
    return "\n".join(lines)
