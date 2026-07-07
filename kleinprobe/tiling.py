"""
kleinprobe/tiling.py
====================
Multi-tile probe extension for KleinProbe. RESEARCH EXTENSION.

Scientific status:
  Mode 1 (single tile):  validated on three IBM Heron r2 processors.
  Mode 2 (tiled probe):  simulation-domain embedding feasibility
                         confirmed (FakeFez, 200 seeds tested).
                         Physical hardware validation required before
                         interpreting spatial variation in H and inv.

What "simulation-domain validated" means:
  ✓ Disjoint transpiler embedding configurations exist
  ✓ Depth consistency within CV threshold under routing
  ✗ Physical independence of simultaneously executing tiles
  ✗ Noise separation validity across tile boundaries
  ✗ Hardware stability under concurrent multi-tile execution

The correct term for the output is:
  "spatial execution-state map" — not "noise map"
  The measurements are circuit-conditioned execution-state estimates,
  not physical noise parameters (T1, T2, gate error).

Architecture:
    KleinProbe.run()           → Snapshot        (Mode 1, validated)
    KleinProbe.run_tiled(n)    → TiledSnapshot    (Mode 2, research)

    TiledSnapshot
      → per-tile Snapshots
      → SpatialHardwareState  (sampled field, not interpolated map)

Validated embedding configs (FakeFez simulation):
    2-tile: seeds=[39, 6]       CV=0.024
    3-tile: seeds=[39, 2, 175]  CV=0.101
    See: tiling_seed_config.json

Reference:
    L. Roma, "Twelve Logical Qubits via Six Simultaneous
    Non-Orientable Stabilizer Codes on a 156-Qubit Superconducting
    Processor" — demonstrated non-overlapping multi-code placement
    on IBM Fez, motivating this extension.

Paper: doi:10.5281/zenodo.21186259 (KleinProbe formalism)
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from .snapshot import Snapshot
from .state import HardwareState, StateDelta


# ── TileMetadata ──────────────────────────────────────────────────────────

@dataclass
class TileMetadata:
    """
    Physical placement information for one tile.

    Populated after transpilation. Used to verify that tiles
    are genuinely non-overlapping and have comparable depth.
    """
    tile_id:      int
    seed:         int
    circuit_depth: int
    physical_data_qubits: Dict[int, int]   # logical → physical
    physical_syn_qubits:  Dict[int, int]   # logical → physical

    @property
    def all_physical_qubits(self) -> set:
        return set(self.physical_data_qubits.values()) | \
               set(self.physical_syn_qubits.values())

    @property
    def antipodal_physical(self) -> Optional[int]:
        return self.physical_data_qubits.get(11)

    @property
    def invariant_syn_physical(self) -> Optional[int]:
        return self.physical_syn_qubits.get(0)


def check_tile_overlap(tiles: List[TileMetadata]) -> dict:
    """
    Verify that tiles do not share physical qubits.

    Returns:
        {'disjoint': bool, 'overlaps': list of (tile_i, tile_j, shared_qubits)}
    """
    overlaps = []
    for i in range(len(tiles)):
        for j in range(i+1, len(tiles)):
            shared = tiles[i].all_physical_qubits & tiles[j].all_physical_qubits
            if shared:
                overlaps.append((i, j, sorted(shared)))
    return {'disjoint': len(overlaps) == 0, 'overlaps': overlaps}


def check_depth_uniformity(tiles: List[TileMetadata],
                            tolerance: float = 0.30) -> dict:
    """
    Check that circuit depths are comparable across tiles.

    Tiles with very different depths (>30% relative variation)
    may have routing-induced distortion that confounds spatial
    comparison of H values.

    Returns:
        {'uniform': bool, 'depths': list, 'cv': coefficient of variation}
    """
    depths = [t.circuit_depth for t in tiles]
    if not depths:
        return {'uniform': True, 'depths': [], 'cv': 0.0}
    cv = np.std(depths) / np.mean(depths)
    return {
        'uniform': cv <= tolerance,
        'depths':  depths,
        'cv':      round(cv, 3),
        'min':     min(depths),
        'max':     max(depths),
    }


# ── TiledSnapshot ─────────────────────────────────────────────────────────

@dataclass
class TiledSnapshot:
    """
    Output of a multi-tile KleinProbe run.

    Contains one Snapshot per tile, plus validation checks
    confirming that the tiled measurement is interpretable.

    IMPORTANT: before interpreting spatial variation in H and inv
    across tiles, verify:
      self.validation['disjoint'] == True
      self.validation['depth_uniform'] == True

    If these checks fail, spatial comparison is unreliable.
    """
    tiles:      List[Snapshot]
    metadata:   List[TileMetadata]
    job_id:     str
    backend:    str
    timestamp:  str
    n_tiles:    int

    # Validation results (populated after construction)
    validation: dict = field(default_factory=dict)

    @classmethod
    def from_results(cls, snapshots: List[Snapshot],
                     metadata: List[TileMetadata],
                     job_id: str) -> 'TiledSnapshot':
        """Construct TiledSnapshot and run validation checks."""
        ts = cls(
            tiles     = snapshots,
            metadata  = metadata,
            job_id    = job_id,
            backend   = snapshots[0].backend if snapshots else '',
            timestamp = snapshots[0].timestamp if snapshots else '',
            n_tiles   = len(snapshots),
        )
        ts.validation = ts._run_validation()
        return ts

    def _run_validation(self) -> dict:
        overlap   = check_tile_overlap(self.metadata)
        depth     = check_depth_uniformity(self.metadata)
        match_rate = sum(s.match for s in self.tiles) / len(self.tiles) \
                     if self.tiles else 0.0
        return {
            'disjoint':      overlap['disjoint'],
            'overlaps':      overlap['overlaps'],
            'depth_uniform': depth['uniform'],
            'depth_cv':      depth['cv'],
            'depths':        depth['depths'],
            'match_rate':    round(match_rate, 3),
            'is_valid':      overlap['disjoint'] and depth['uniform'],
        }

    @property
    def is_valid(self) -> bool:
        """True if tiles are disjoint and depths are comparable."""
        return self.validation.get('is_valid', False)

    @property
    def H_values(self) -> List[float]:
        return [s.H for s in self.tiles]

    @property
    def inv_values(self) -> List[float]:
        return [s.inv for s in self.tiles]

    @property
    def H_mean(self) -> float:
        return round(float(np.mean(self.H_values)), 4)

    @property
    def H_std(self) -> float:
        return round(float(np.std(self.H_values)), 4)

    @property
    def inv_mean(self) -> float:
        return round(float(np.mean(self.inv_values)), 4)

    @property
    def inv_std(self) -> float:
        return round(float(np.std(self.inv_values)), 4)

    @property
    def spatial_variance(self) -> float:
        """
        L2 norm of per-tile deviations from spatial mean.
        Descriptive statistic computed from the user's own measurements.
        Near zero = spatially uniform execution environment.
        Large = heterogeneous execution environment across tiles.
        """
        H_arr   = np.array(self.H_values)
        inv_arr = np.array(self.inv_values)
        return round(float(np.sqrt(
            np.sum((H_arr - H_arr.mean())**2) +
            np.sum((inv_arr - inv_arr.mean())**2)
        )), 4)

    @property
    def failed_tiles(self) -> List[int]:
        """Tile indices where dominant pattern did not match prediction."""
        return [i for i, s in enumerate(self.tiles) if not s.match]

    def tile_match_scores(self, target_isa) -> list:
        """
        Compute Layout Match Score for each tile against a target circuit.

        Returns list of dicts with lms_probe, lms_target, and tile index,
        sorted by lms_probe descending (most relevant tile first).

        Example:
            scores = tsnap.tile_match_scores(my_transpiled_circuit)
            best   = scores[0]
            print(f"Best tile: {best['tile']}  LMS={best['lms_probe']:.3f}")
            print(f"  H={self.tiles[best['tile']].H:.4f}  "
                  f"inv={self.tiles[best['tile']].inv:.4f}")
        """
        from .state import layout_match_score

        results = []
        for i, meta in enumerate(self.metadata):
            # Reconstruct probe qubit set from metadata
            probe_q = frozenset(meta.all_physical_qubits)

            # Get target qubits
            try:
                target_q = frozenset(
                    target_isa.layout.initial_index_layout(filter_ancillas=True))
            except:
                try:
                    vbits = target_isa.layout.initial_layout.get_virtual_bits()
                    target_q = frozenset(p for q,p in vbits.items()
                                        if hasattr(q,'_register'))
                except:
                    target_q = frozenset()

            shared = probe_q & target_q
            lms_p  = round(len(shared)/len(probe_q), 3)  if probe_q  else 0.0
            lms_t  = round(len(shared)/len(target_q), 3) if target_q else 0.0

            results.append({
                'tile':        i,
                'seed':        meta.seed,
                'lms_probe':   lms_p,
                'lms_target':  lms_t,
                'n_shared':    len(shared),
                'shared':      sorted(shared),
                'H':           self.tiles[i].H,
                'inv':         self.tiles[i].inv,
                'relevance':   'HIGH' if lms_p>=0.8 else
                               'MODERATE' if lms_p>=0.5 else
                               'LOW' if lms_p>=0.2 else 'NEGLIGIBLE',
            })

        return sorted(results, key=lambda x: -x['lms_probe'])

    def best_match(self, target_isa) -> dict:
        """Return the tile with highest Layout Match Score for target_isa."""
        scores = self.tile_match_scores(target_isa)
        return scores[0] if scores else None
        """
        Per-tile execution-state summary.
        Returns dict keyed by tile_id with raw measurements.
        """
        result = {}
        for i, (snap, meta) in enumerate(zip(self.tiles, self.metadata)):
            result[i] = {
                'H':              snap.H,
                'inv':            snap.inv,
                'f':              snap.dominant_f,
                'Z_raw':          snap.Z_raw,
                'S':              snap.S,
                'match':          snap.match,
                'depth':          meta.circuit_depth,
                'antipodal_qubit': meta.antipodal_physical,
            }
        return result

    def report(self) -> str:
        lines = [
            f"TiledSnapshot — {self.backend}",
            f"  Tiles:      {self.n_tiles}",
            f"  Job:        {self.job_id}",
            f"  Valid:      {'✓' if self.is_valid else '✗ — see validation'}",
        ]

        if not self.validation.get('disjoint', True):
            lines.append(f"  ⚠ Overlapping tiles — "
                        f"spatial comparison invalid")
        if not self.validation.get('depth_uniform', True):
            lines.append(f"  ⚠ Depth CV={self.validation['depth_cv']:.2f} "
                        f"> 0.30 — H comparison may reflect depth")

        lines += [
            f"",
            f"  {'Tile':>5} {'H':>8} {'inv':>8} {'match':>7} {'depth':>7}",
            "  " + "-"*42,
        ]

        for i, (snap, meta) in enumerate(zip(self.tiles, self.metadata)):
            ok = '✓' if snap.match else '✗'
            lines.append(f"  {i:>5} {snap.H:>8.4f} {snap.inv:>8.4f} "
                        f"{ok:>7} {meta.circuit_depth:>7}")

        lines += [
            f"",
            f"  H:   {self.H_mean:.4f} ± {self.H_std:.4f}  "
            f"(range {min(self.H_values):.3f}–{max(self.H_values):.3f})",
            f"  inv: {self.inv_mean:.4f} ± {self.inv_std:.4f}  "
            f"(range {min(self.inv_values):.3f}–{max(self.inv_values):.3f})",
            f"  Spatial variance: {self.spatial_variance:.4f}",
        ]

        if self.failed_tiles:
            lines.append(f"  ⚠ Failed tiles: {self.failed_tiles}")
        else:
            lines.append(f"  ✓ All tiles matched prediction")

        return "\n".join(lines)


# ── SpatialHardwareState ──────────────────────────────────────────────────

@dataclass
class SpatialHardwareState:
    """
    Spatial execution-state estimate from a tiled probe.

    Represents the sampled spatial field {θ̂_1, ..., θ̂_n}
    across n non-overlapping chip regions.

    This is a sampled field, not a continuous interpolation.
    It provides raw per-region execution-state estimates and
    descriptive spatial statistics.

    FREE TIER (this class):
      - Per-tile state vectors (.states)
      - Descriptive statistics (.spatial_variance, .H_spread, .inv_spread)
      - Validation (.is_valid)
      - Basic summary (.summary())

    PRO TIER (not included):
      - hot_spot detection (requires baseline comparison data)
      - anomaly classification (requires calibration history)
      - pairwise_deltas with confidence scoring
      - SpatialHardwareTrajectory (longitudinal spatial tracking)
      - RegionalAdvisor (placement recommendations)
      - calibration-guided patch selection (tiling_geometry.py)
      - validated patch libraries per backend

    Paper: doi:10.5281/zenodo.21186259
    """
    states:         List[HardwareState]
    tiled_snapshot: TiledSnapshot

    @classmethod
    def from_tiled(cls, tsnap: TiledSnapshot) -> 'SpatialHardwareState':
        states = [HardwareState.from_snapshot(s) for s in tsnap.tiles]
        return cls(states=states, tiled_snapshot=tsnap)

    @property
    def n_tiles(self) -> int:
        return len(self.states)

    @property
    def is_valid(self) -> bool:
        return self.tiled_snapshot.is_valid

    @property
    def spatial_variance(self) -> float:
        """
        L2 norm of per-tile deviations from spatial mean.
        Descriptive statistic computed from the user's own measurements.
        Near zero = spatially uniform. Large = heterogeneous.
        """
        return self.tiled_snapshot.spatial_variance

    @property
    def H_spread(self) -> float:
        """Range of H values across tiles (max - min)."""
        H = self.tiled_snapshot.H_values
        return round(max(H) - min(H), 4)

    @property
    def inv_spread(self) -> float:
        """Range of inv values across tiles (max - min)."""
        inv = self.tiled_snapshot.inv_values
        return round(max(inv) - min(inv), 4)

    @property
    def is_spatially_uniform(self) -> bool:
        """
        Simple uniformity check based on spatial_variance.
        Threshold 0.5 is indicative only.
        For baseline-calibrated uniformity assessment, see Pro tier.
        """
        return self.spatial_variance < 0.5

    def summary(self) -> str:
        uniform = ("✓ low spatial variance"
                   if self.is_spatially_uniform
                   else f"⚠ spatial variance={self.spatial_variance:.3f}")
        lines = [
            f"SpatialHardwareState",
            f"  Tiles:       {self.n_tiles}",
            f"  Valid:       {'✓' if self.is_valid else '✗'}",
            f"  H spread:    {self.H_spread:.4f} bits",
            f"  inv spread:  {self.inv_spread:.4f}",
            f"  Spatial var: {self.spatial_variance:.4f}  {uniform}",
        ]
        for i, s in enumerate(self.states):
            lines.append(f"  Tile {i}: H={s.H:.4f}  inv={s.inv:.4f}  "
                        f"regime={s.regime}")
        return "\n".join(lines)
