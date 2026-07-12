"""
kleinprobe/validity.py
=======================
KleinProbe execution environment model.

KleinProbe is a spatial execution environment sensor for
superconducting quantum processors. It measures the local
execution environment of a chip region through a structured
18-qubit probe circuit and returns a hardware snapshot.

STATES DESCRIBE DEVIATION FROM BASELINE — NOT APPLICATION OUTCOMES
-------------------------------------------------------------------
The deviation states (REFERENCE, DRIFTED, STRONGLY_DRIFTED,
PROBE_INVALID) describe how far the probe measurement has moved
from its calibrated reference baseline. They are NOT predictions
of application circuit success or failure.

This distinction is fundamental. A thermometer measures temperature;
it does not tell you whether a chemical reaction will succeed.
Similarly, KleinProbe measures the execution environment experienced
by the probe circuit. A different application circuit will experience
a related but not identical environment.

THREE MODES OF USE
------------------

1. Environmental monitor (single probe)
   Answers: "What is the execution environment of this region right now?"
   Output:  H, inv, Z — a hardware snapshot
   Analogy: weather station reading

2. Spatial routing (multi-tile probe)
   Answers: "Which region of this chip is currently quietest?"
   Output:  ranked spatial map — route to the lowest-H usable tile
   This is the strongest validated use case.
   Validated: Fez central region ranked #1 in 6/6 independent runs.
              Marrakesh upper-left ranked #1 in 4/4 independent runs.

3. Application-conditioned execution environment
   Answers: "How does the execution environment relate to my circuit?"
   Output:  H conditioned by Layout Match Score (LMS)
   Note:    The probe may correlate with application quality when
            spatial overlap between probe and circuit is high (LMS ≈ 1).
            This is not a prediction of application fidelity — it is
            an environment estimate conditioned by application overlap.

ABSOLUTE VS RELATIVE MEASUREMENTS
-----------------------------------
Absolute H values vary across calibration cycles because the hardware
operating point changes over time. However, neighbouring regions
experience much of this drift together. Consequently, the ordering
of regions by H is considerably more stable than the absolute H
values themselves.

KleinAtlas therefore uses H primarily as a relative spatial observable
rather than an absolute hardware-quality metric.

    Absolute H drifts.
    Relative ordering remains stable.

This is the core empirical finding: the spatial ranking of H is
reproducible across calibration cycles even as absolute H values shift
by up to 0.5 bits between sessions.

PRIMARY OUTPUT: H AND RANK
--------------------------
The primary output is H — the Shannon entropy of the syndrome
distribution. Lower H indicates a quieter execution environment.

The rank (position in the spatial ordering) is the derived quantity
used for routing decisions. Route to the lowest-H usable tile.

env_score and environment_shift are normalised display quantities
derived from H and inv. They summarise the deviation from the
probe's calibration baseline for display purposes. They are NOT
measures of user circuit success probability.

BACKWARD COMPATIBILITY
----------------------
Old state names kept as aliases:
    VALID_SPATIAL    = REFERENCE
    VALID_ANOMALOUS  = DRIFTED
    OPTIMAL          = REFERENCE
    ELEVATED         = DRIFTED
    CRITICAL         = STRONGLY_DRIFTED
    INVALID          = PROBE_INVALID

Paper: doi:10.5281/zenodo.21186259
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List

# ── Deviation state constants ──────────────────────────────────────────────

REFERENCE        = "REFERENCE"
DRIFTED          = "DRIFTED"
STRONGLY_DRIFTED = "STRONGLY_DRIFTED"
PROBE_INVALID    = "PROBE_INVALID"

# Backward compatibility aliases
VALID_SPATIAL    = REFERENCE
VALID_ANOMALOUS  = DRIFTED
OPTIMAL          = REFERENCE
ELEVATED         = DRIFTED
CRITICAL         = STRONGLY_DRIFTED
INVALID          = PROBE_INVALID

# ── Per-chip calibration reference ─────────────────────────────────────────
# Derived from baseline sessions. Update when a regime change is detected.

_CHIP_CONFIG = {
    "ibm_fez": {
        "H_ref":         2.977,   # Era2 mean, n=13 sessions
        "H_std":         0.215,
        "inv_ref":       0.889,
        "inv_std":       0.019,
        "inv_drifted":   0.851,   # ref - 2σ
        "inv_strongly":  0.794,   # ref - 5σ
    },
    "ibm_kingston": {
        "H_ref":         2.694,
        "H_std":         0.092,
        "inv_ref":       0.890,
        "inv_std":       0.010,
        "inv_drifted":   0.870,
        "inv_strongly":  0.750,
    },
    "ibm_marrakesh": {
        "H_ref":         3.128,
        "H_std":         0.252,
        "inv_ref":       0.870,
        "inv_std":       0.026,
        "inv_drifted":   0.818,
        "inv_strongly":  0.740,
    },
}

_FALLBACK_CONFIG = {
    "H_ref": 3.0, "H_std": 0.25,
    "inv_ref": 0.880, "inv_std": 0.025,
    "inv_drifted": 0.75, "inv_strongly": 0.65,
}


def _deviation_annotation(H: float, cfg: dict) -> str:
    """Deviation of H from calibration reference as arrow annotation."""
    sigma = (H - cfg["H_ref"]) / max(cfg["H_std"], 0.05)
    if sigma <= 1.0: return ""
    if sigma <= 2.0: return "▲ moderate"
    if sigma <= 4.0: return "▲▲ high"
    return "▲▲▲ very high"


def _env_label(env: float) -> str:
    if env >= 0.75: return "strong"
    if env >= 0.50: return "moderate"
    if env >= 0.25: return "weak"
    return "minimal"


# ── ProbeResult ────────────────────────────────────────────────────────────

@dataclass
class ProbeResult:
    """
    Hardware snapshot from a single KleinProbe tile measurement.

    Primary fields (the observable quantities):
        H:     Shannon entropy of syndrome distribution (bits)
               PRIMARY routing metric. Lower = quieter environment.
        inv:   Invariant fraction P(syndrome bit 0 = 1)
               Secondary signal, largely independent of H.
        Z:     Statistical significance vs flat baseline
        match: True if dominant syndrome == expected pattern

    Derived display fields:
        state:               Deviation state (REFERENCE/DRIFTED/
                             STRONGLY_DRIFTED/PROBE_INVALID)
        deviation:           Human-readable deviation annotation (▲▲ high)
        environment_shift:   Normalised drift from baseline [0-1]
                             0.0 = at reference, 1.0 = maximally drifted
                             Renamed from risk_index. Not a risk probability.
        env_score:           1 - environment_shift (display convenience)
                             Not a probability of circuit success.
        rank:                Position in spatial ranking (set by classify_tiled)
                             1 = lowest H = recommended tile
    """
    # Primary observables
    H:        float
    inv:      float
    Z:        float = 0.0
    S:        float = 0.0
    match:    bool  = True
    dominant: str   = "100001"

    # Deviation classification
    state:     str   = REFERENCE
    deviation: str   = ""

    # Normalised display quantities (NOT probability estimates)
    env_score:           float = 1.0
    environment_shift:   float = 0.0

    # Backward compat alias
    @property
    def risk_index(self) -> float:
        return self.environment_shift

    # Spatial rank (set by classify_tiled)
    rank:         Optional[int] = None
    region_label: str           = ""

    @property
    def is_usable(self) -> bool:
        """True for REFERENCE, DRIFTED, STRONGLY_DRIFTED. False for PROBE_INVALID."""
        return self.state != PROBE_INVALID

    def __repr__(self):
        rank_str = f" rank=#{self.rank}" if self.rank else ""
        return (f"ProbeResult({self.state}{rank_str}, "
                f"H={self.H:.3f}, inv={self.inv:.3f})")


# ── SpatialMap ────────────────────────────────────────────────────────────

@dataclass
class SpatialMap:
    """
    Spatial execution environment map across multiple probe tiles.

    The primary output is the ranked list of tiles by H.
    Route to the lowest-H usable tile.

    Deviation states and environment_shift provide context about
    how far each tile's execution environment has drifted from the
    probe's calibrated reference. They describe the environment —
    they do not predict application circuit outcomes.
    """
    tile_results: List[ProbeResult]
    backend:      str = ""

    @property
    def ranked(self) -> List[ProbeResult]:
        """Tiles sorted by H ascending. PROBE_INVALID tiles last."""
        valid   = [r for r in self.tile_results if r.is_usable]
        invalid = [r for r in self.tile_results if not r.is_usable]
        return sorted(valid, key=lambda r: r.H) + invalid

    @property
    def best(self) -> Optional[ProbeResult]:
        """
        Lowest-H usable tile — the routing recommendation.
        Route to the lowest-H usable tile.
        """
        valid = [r for r in self.tile_results if r.is_usable]
        return min(valid, key=lambda r: r.H) if valid else None

    @property
    def best_index(self) -> Optional[int]:
        best = self.best
        return self.tile_results.index(best) if best else None

    def spatial_map(self) -> str:
        """
        Ranking-first spatial environment map.

        H is the primary column. Rank follows directly from H.
        Deviation annotation provides calibration context.

        Example:
          SPATIAL ENVIRONMENT MAP — ibm_fez

            Rank  Region              H       Deviation
            ──────────────────────────────────────────
             #1   central           4.112   ▲▲▲ very high
             #2   upper-left-A      4.846   ▲▲▲ very high
             #3   lower-left        5.443   ▲▲▲ very high
             ──   upper-left-B      —       probe invalid

          Route to: central (#1)
          Baseline reference: H_ref = 2.977 ± 0.215 (Era2, n=13)

          Note: all tiles show very high deviation today.
          Absolute H drifts with calibration cycles.
          Relative ordering is the stable signal.
        """
        header = "SPATIAL ENVIRONMENT MAP"
        if self.backend:
            header += f" — {self.backend}"

        lines = [header, ""]
        lines.append(f"  {'Rank':<6} {'Region':<22} {'H':>7}   Deviation")
        lines.append("  " + "─" * 52)

        for r in self.ranked:
            i        = self.tile_results.index(r)
            rank_str = f"#{r.rank}" if r.is_usable and r.rank else "──"
            H_str    = f"{r.H:.3f}" if r.is_usable else "—"
            region   = r.region_label or f"tile_{i}"
            if not r.is_usable:
                dev_str = "probe invalid"
            elif r.deviation:
                dev_str = r.deviation
            else:
                dev_str = "within reference"
            lines.append(
                f"  {rank_str:<6} {region:<22} {H_str:>7}   {dev_str}")

        lines.append("")

        if self.best:
            b      = self.best
            region = b.region_label or f"tile_{self.best_index}"
            lines.append(f"  Route to: {region} (#{b.rank})")

        cfg = _CHIP_CONFIG.get(self.backend.lower(), _FALLBACK_CONFIG)
        lines.append(
            f"  Baseline reference: H_ref = {cfg['H_ref']:.3f} "
            f"± {cfg['H_std']:.3f}")

        # Note if all tiles are drifted
        n_ref = sum(1 for r in self.tile_results
                    if r.state == REFERENCE and r.is_usable)
        if n_ref == 0 and any(r.is_usable for r in self.tile_results):
            lines.append("")
            lines.append("  Note: all tiles drifted from reference today.")
            lines.append("  Absolute H drifts with calibration cycles.")
            lines.append("  Relative ordering is the stable signal.")

        return "\n".join(lines)

    # ── Legacy output for backward compatibility ──────────────────────────
    def execution_map(self, backend: str = "") -> str:
        return self.spatial_map()

    @property
    def overall_state(self) -> str:
        if any(r.state == PROBE_INVALID     for r in self.tile_results):
            return PROBE_INVALID
        if any(r.state == STRONGLY_DRIFTED  for r in self.tile_results):
            return STRONGLY_DRIFTED
        if any(r.state == DRIFTED           for r in self.tile_results):
            return DRIFTED
        return REFERENCE

    @property
    def anomaly_scope(self) -> str:
        n_non = sum(1 for r in self.tile_results if r.state != REFERENCE)
        n     = len(self.tile_results)
        if n_non == 0:       return "none"
        if n_non == n:       return "chipwide"
        if n_non >= n - 1:   return "regional"
        return "local"

    def __repr__(self):
        return (f"SpatialMap(best=tile_{self.best_index}, "
                f"n={len(self.tile_results)}, "
                f"backend={self.backend!r})")


# Backward compat alias
TiledValidityResult = SpatialMap
ValidityResult      = ProbeResult


# ── Classification ─────────────────────────────────────────────────────────

def classify_tile(
    match:         bool,
    H:             float,
    inv:           float,
    backend:       str            = "",
    region_label:  str            = "",
    H_ref:         Optional[float] = None,
    H_std:         Optional[float] = None,
    inv_threshold: Optional[float] = None,
) -> ProbeResult:
    """
    Classify a single probe tile measurement.

    The state describes deviation of the probe measurement from its
    calibrated reference baseline — NOT application circuit quality.

    Primary output: H (route to lowest-H usable tile).
    Secondary: state, deviation annotation, env_score (display).

    Args:
        match:         True if dominant syndrome == '100001'
        H:             Syndrome entropy (bits) — primary metric
        inv:           Invariant fraction
        backend:       Chip name for calibration reference
        region_label:  Tile label for display
        H_ref:         Override reference H mean
        H_std:         Override reference H std
        inv_threshold: Override DRIFTED inv boundary

    Returns:
        ProbeResult with H, state, deviation, rank (None until
        classify_tiled assigns spatial ranks).
    """
    bk  = backend.lower()
    cfg = _CHIP_CONFIG.get(bk, _FALLBACK_CONFIG)

    H_mean       = H_ref if H_ref is not None else cfg["H_ref"]
    H_sigma      = H_std if H_std is not None else max(cfg["H_std"], 0.05)
    inv_drifted  = inv_threshold if inv_threshold is not None else cfg["inv_drifted"]
    inv_strongly = cfg["inv_strongly"]

    H_drifted  = H_mean + max(1.0 * H_sigma, 0.30)
    H_strongly = H_mean + max(6.0 * H_sigma, 1.50)

    # env_score: display convenience, not a probability
    H_s = max(0.0, min(1.0, 1.0 - (H - H_mean) / (4.0 * H_sigma)))
    I_s = max(0.0, min(1.0, (inv - 0.5) / max(cfg["inv_ref"] - 0.5, 0.01)))
    env = round(0.5 * H_s + 0.5 * I_s, 3)
    shift = round(1.0 - env, 3)  # environment_shift

    dev = _deviation_annotation(H, cfg)

    def _make(state: str) -> ProbeResult:
        r = ProbeResult(
            H=H, inv=inv, match=match,
            dominant="100001" if match else "?",
            state=state, deviation=dev,
            env_score=env, environment_shift=shift,
        )
        r.region_label = region_label
        return r

    if not match:
        r = ProbeResult(
            H=H, inv=inv, match=False, dominant="?",
            state=PROBE_INVALID, deviation="probe invalid",
            env_score=0.0, environment_shift=1.0,
        )
        r.region_label = region_label
        return r

    if inv < inv_strongly or H > H_strongly:
        return _make(STRONGLY_DRIFTED)
    if inv < inv_drifted or H > H_drifted:
        return _make(DRIFTED)
    return _make(REFERENCE)


def classify_tiled(
    tile_results: List[dict],
    backend:      str = "",
    **kwargs,
) -> SpatialMap:
    """
    Classify all tiles and return a ranked SpatialMap.

    H is the primary ordering metric.
    Route to the lowest-H usable tile.

    Args:
        tile_results: list of dicts with keys:
                      match, H, inv, region_label
        backend:      chip name for calibration reference

    Returns:
        SpatialMap with tiles ranked by H.

    Example:
        tiles = [
            {"match": True, "H": 4.11, "inv": 0.69, "region_label": "central"},
            {"match": True, "H": 5.44, "inv": 0.62, "region_label": "lower-left"},
            {"match": True, "H": 5.47, "inv": 0.71, "region_label": "lower-central"},
        ]
        sm = classify_tiled(tiles, backend="ibm_fez")
        print(sm.spatial_map())
        # Route to: central (#1)
    """
    results = []
    for t in tile_results:
        r = classify_tile(
            match        = t.get("match", False),
            H            = t.get("H", 0.0),
            inv          = t.get("inv", 0.0),
            backend      = backend,
            region_label = t.get("region_label", ""),
            **kwargs,
        )
        results.append(r)

    # Assign spatial ranks by H (ascending, usable tiles only)
    valid = sorted([r for r in results if r.is_usable], key=lambda r: r.H)
    for i, r in enumerate(valid):
        r.rank = i + 1

    return SpatialMap(tile_results=results, backend=backend)


# ── Layout Match Score ─────────────────────────────────────────────────────

def compute_lms(
    probe_qubits:        list,
    app_physical_qubits: list,
) -> float:
    """
    Layout Match Score — confidence metric for mode 3.

    Answers: "How representative is this probe measurement
    of the target circuit's execution environment?"

    LMS = |probe ∩ app| / |app|

    Range [0.0, 1.0]:
      1.0 = probe fully covers app circuit footprint
      0.0 = probe and app share no physical qubits

    This is a coverage metric, not a quality metric.
    High LMS means the probe measured the same qubits the
    application will use. Low LMS means the probe measured
    a different part of the chip.

    Typical values by circuit width:
      6–18 qubits    →  0.90–1.00  →  direct coverage
      20–40 qubits   →  0.50–0.80  →  high coverage
      40–80 qubits   →  0.25–0.50  →  moderate coverage
      80–156 qubits  →  0.10–0.25  →  orientation only
    """
    if not app_physical_qubits:
        return 0.0
    return round(
        len(set(probe_qubits) & set(app_physical_qubits))
        / len(set(app_physical_qubits)), 3)


def lms_label(lms: float) -> str:
    """Human-readable LMS coverage label."""
    if lms >= 0.85: return "DIRECT"
    if lms >= 0.50: return "HIGH"
    if lms >= 0.25: return "MODERATE"
    if lms >= 0.10: return "LOW"
    return "MINIMAL"


def effective_score(env: float, lms: float) -> float:
    """
    Coverage-attenuated env_score for forced single-number ranking.

    R_eff = env * (0.5 + 0.5 * LMS)

    Use only when a single ranking number is required.
    Prefer exposing H, env_score, and LMS independently.
    """
    return round(env * (0.5 + 0.5 * lms), 3)


def routing_report(
    tile_name: str,
    region:    str,
    env:       float,
    lms:       float,
    state:     str,
) -> str:
    """Two-axis routing report: environment quality + probe coverage."""
    env_bar = "█" * int(env * 10) + "░" * (10 - int(env * 10))
    lms_bar = "█" * int(lms * 10) + "░" * (10 - int(lms * 10))
    lines = [
        f"  {tile_name} · {region}",
        f"  {'─'*41}",
        f"  Environment   [{env_bar}]  {env:.2f}",
        f"  Coverage      [{lms_bar}]  {lms:.2f}  {lms_label(lms)}",
        f"  State         {state}",
        f"  Effective     {effective_score(env, lms):.2f}",
    ]
    return "\n".join(lines)


# ── BaselineTracker ────────────────────────────────────────────────────────

class BaselineTracker:
    """
    Rolling baseline tracker for KleinProbe H and inv measurements.

    Replaces hardcoded _CHIP_CONFIG baselines with a self-updating
    reference derived from the user's own sessions. After N sessions
    the deviation annotations (▲ moderate / ▲▲ high) are calibrated
    to the user's chip in their current time period.

    IMPORTANT: The spatial RANKING (min H across tiles) requires no
    baseline at all — it is self-contained within a single run.
    BaselineTracker improves the deviation ANNOTATIONS only.

    Usage:
        tracker = BaselineTracker(backend="ibm_fez", window=10)

        # After each single-tile session:
        tracker.update(H=3.031, inv=0.877)
        tracker.update(H=2.622, inv=0.872)

        # Use tracker's baseline in classify_tile:
        r = classify_tile(
            match=True, H=4.11, inv=0.69,
            backend="ibm_fez",
            H_ref=tracker.H_ref,
            H_std=tracker.H_std,
        )

        # Or pass directly to classify_tiled:
        sm = classify_tiled(tile_data, backend="ibm_fez",
                            H_ref=tracker.H_ref,
                            H_std=tracker.H_std)

    Initialisation:
        If fewer than min_sessions have been collected, the tracker
        falls back to the hardcoded _CHIP_CONFIG defaults so the tool
        is immediately usable even for new users.

    Regime change detection:
        If a new H reading is more than regime_threshold standard
        deviations from the current rolling mean, the tracker flags
        it as a potential regime change. The user can then decide
        whether to reset the baseline.

    Persistence:
        Save/load the tracker state as JSON to persist baselines
        across Python sessions:
            tracker.save("baseline_ibm_fez.json")
            tracker = BaselineTracker.load("baseline_ibm_fez.json")
    """

    def __init__(
        self,
        backend:           str   = "",
        window:            int   = 10,
        min_sessions:      int   = 5,
        regime_threshold:  float = 5.0,
    ):
        """
        Args:
            backend:          Chip name — used for initial fallback defaults
            window:           Rolling window size (number of sessions)
            min_sessions:     Minimum sessions before overriding hardcoded defaults
            regime_threshold: σ threshold for regime change detection
        """
        self.backend          = backend.lower()
        self.window           = window
        self.min_sessions     = min_sessions
        self.regime_threshold = regime_threshold

        self._H_history:   List[float] = []
        self._inv_history: List[float] = []
        self._regime_flags: List[bool] = []

    # ── Update ─────────────────────────────────────────────────────────────

    def update(self, H: float, inv: float) -> dict:
        """
        Add a new session measurement to the rolling baseline.

        Args:
            H:   Syndrome entropy from a single-tile probe
            inv: Invariant fraction from the same probe

        Returns:
            dict with keys:
                regime_change: True if H is >regime_threshold σ from current mean
                sessions:      Number of sessions in current window
                H_ref:         Current rolling H mean
                H_std:         Current rolling H std
        """
        import math

        # Regime change check before updating
        regime_change = False
        if len(self._H_history) >= self.min_sessions:
            current_mean = sum(self._H_history[-self.window:]) / min(len(self._H_history), self.window)
            current_std  = self._std(self._H_history[-self.window:])
            if current_std > 0:
                z = abs(H - current_mean) / current_std
                regime_change = z > self.regime_threshold

        # Append
        self._H_history.append(H)
        self._inv_history.append(inv)
        self._regime_flags.append(regime_change)

        # Trim to window
        if len(self._H_history) > self.window * 2:
            self._H_history   = self._H_history[-self.window:]
            self._inv_history = self._inv_history[-self.window:]
            self._regime_flags= self._regime_flags[-self.window:]

        return {
            "regime_change": regime_change,
            "sessions":      len(self._H_history),
            "H_ref":         self.H_ref,
            "H_std":         self.H_std,
        }

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """True when enough sessions have been collected to override defaults."""
        return len(self._H_history) >= self.min_sessions

    @property
    def H_ref(self) -> float:
        """Current rolling H mean. Falls back to hardcoded default if not ready."""
        if self.is_ready:
            recent = self._H_history[-self.window:]
            return round(sum(recent) / len(recent), 4)
        cfg = _CHIP_CONFIG.get(self.backend, _FALLBACK_CONFIG)
        return cfg["H_ref"]

    @property
    def H_std(self) -> float:
        """Current rolling H std. Falls back to hardcoded default if not ready."""
        if self.is_ready:
            return round(max(self._std(self._H_history[-self.window:]), 0.05), 4)
        cfg = _CHIP_CONFIG.get(self.backend, _FALLBACK_CONFIG)
        return cfg["H_std"]

    @property
    def inv_ref(self) -> float:
        """Current rolling inv mean."""
        if self.is_ready:
            recent = self._inv_history[-self.window:]
            return round(sum(recent) / len(recent), 4)
        cfg = _CHIP_CONFIG.get(self.backend, _FALLBACK_CONFIG)
        return cfg["inv_ref"]

    @property
    def sessions(self) -> int:
        return len(self._H_history)

    @property
    def regime_changes(self) -> List[int]:
        """Indices of sessions flagged as potential regime changes."""
        return [i for i,f in enumerate(self._regime_flags) if f]

    def status(self) -> str:
        """Human-readable tracker status."""
        if not self.is_ready:
            n   = len(self._H_history)
            rem = self.min_sessions - n
            src = "hardcoded defaults" if n == 0 else f"hardcoded defaults ({n} sessions collected, {rem} more needed)"
            return (f"BaselineTracker({self.backend or '?'}) — "
                    f"not ready ({n}/{self.min_sessions} sessions). "
                    f"Using {src}.")
        rc = len(self.regime_changes)
        return (f"BaselineTracker({self.backend or '?'}) — "
                f"ready ({self.sessions} sessions, window={self.window}). "
                f"H_ref={self.H_ref:.3f} ± {self.H_std:.3f}"
                + (f"  ⚠ {rc} regime change(s) detected" if rc else ""))

    def reset(self):
        """Reset baseline history. Use after a confirmed regime change."""
        self._H_history    = []
        self._inv_history  = []
        self._regime_flags = []

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self, path: str):
        """Save tracker state to JSON."""
        import json
        data = {
            "backend":         self.backend,
            "window":          self.window,
            "min_sessions":    self.min_sessions,
            "regime_threshold":self.regime_threshold,
            "H_history":       self._H_history,
            "inv_history":     self._inv_history,
            "regime_flags":    self._regime_flags,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "BaselineTracker":
        """Load tracker state from JSON."""
        import json
        with open(path) as f:
            data = json.load(f)
        t = cls(
            backend          = data["backend"],
            window           = data["window"],
            min_sessions     = data["min_sessions"],
            regime_threshold = data["regime_threshold"],
        )
        t._H_history    = data["H_history"]
        t._inv_history  = data["inv_history"]
        t._regime_flags = data["regime_flags"]
        return t

    @classmethod
    def from_sessions(
        cls,
        sessions:  List[dict],
        backend:   str  = "",
        window:    int  = 10,
        **kwargs,
    ) -> "BaselineTracker":
        """
        Build a tracker from a list of session dicts.

        Args:
            sessions: list of dicts with keys 'H' and 'inv'
                      (matches the session JSON format in kleinatlas-data)
            backend:  chip name

        Example:
            import json, pathlib
            sessions = [
                json.loads(f.read_text())
                for f in pathlib.Path("sessions/ibm_fez").glob("*.json")
            ]
            tracker = BaselineTracker.from_sessions(sessions, backend="ibm_fez")
        """
        t = cls(backend=backend, window=window, **kwargs)
        for s in sessions:
            m = s.get("measurements", s)  # handle both formats
            H   = m.get("H")
            inv = m.get("inv")
            if H is not None and inv is not None:
                t.update(H=H, inv=inv)
        return t

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _std(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        var  = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return var ** 0.5
