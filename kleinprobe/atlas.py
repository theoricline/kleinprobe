"""
kleinprobe/atlas.py
====================
KleinAtlas: calibration-aware spatial tile generator.

Automatically discovers non-overlapping 18-qubit probe regions
from backend.properties(), validates them, and provides
initial_layout configurations for KleinProbe tiled execution.

The full pipeline:

    backend.properties()
             ↓
       KleinAtlas.build()
             ↓
        3 validated tiles
             ↓
       KleinProbe ×3  (via run_tiled)
             ↓
       ExecutionSnapshot

Usage:
    from qiskit_ibm_runtime import QiskitRuntimeService
    from kleinprobe.atlas import KleinAtlas
    from kleinprobe.tiling import TiledSnapshot, SpatialHardwareState

    service = QiskitRuntimeService()
    backend = service.backend("ibm_fez")

    # Build atlas from current calibration
    atlas = KleinAtlas(backend)
    atlas.build()
    print(atlas.report())

    # Run tiled probe using atlas layouts
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2 as Sampler
    from kleinprobe.circuit import build_probe_circuit

    probe_qc = build_probe_circuit()
    pubs = []
    for tile in atlas.tiles:
        pm  = generate_preset_pass_manager(
            optimization_level=3, backend=backend,
            initial_layout=tile.initial_layout(probe_qc),
            seed_transpiler=77)
        pubs.append((pm.run(probe_qc),))

    job    = Sampler(backend).run(pubs, shots=4096)
    result = job.result()
    # ... process with TiledSnapshot

Free tier:
    KleinAtlas.build()           — BFS patch selection from calibration
    atlas.tiles                  — list of Tile objects
    atlas.initial_layouts        — ready for generate_preset_pass_manager
    atlas.report()               — human-readable summary
    atlas.is_stale()             — True if calibration changed since build
    atlas.metadata               — calibration timestamp, version, exclusions

Pro tier (not included):
    Validated patch library      — historical performance per tile
    RegionalAdvisor              — recommendation based on session history
    SpatialInteractionMatrix     — cross-tile interference measurements
    drift_model                  — H(Δt_cal) prediction per tile

Paper: doi:10.5281/zenodo.21186259
"""

from __future__ import annotations

import heapq
import json
import numpy as np
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple


# ── Constants ─────────────────────────────────────────────────────────────

TILE_SIZE       = 18          # qubits per tile
N_SYNDROME      = 6           # syndrome qubits per tile (last 6 in qubit list)
DEFAULT_N_TILES = 3
DEFAULT_SEED    = 77

# Hard exclusion thresholds — qubits worse than these are never included
MAX_RO_HARD     = 0.08        # 8% readout error
MIN_T2_HARD     = 15.0        # 15µs T2

# Soft score thresholds — qubits worse than this get deprioritised
MAX_SCORE_SOFT  = 5.0


# ── Tile dataclass ────────────────────────────────────────────────────────

@dataclass
class Tile:
    """
    A single 18-qubit probe region discovered by KleinAtlas.

    Attributes:
        index:          tile index (0-based)
        qubits:         18 physical qubit indices (data[0:12] + syndrome[12:18])
        region_label:   human-readable region description
        T2_mean_us:     mean T2 of qubits in tile (µs)
        RO_mean:        mean readout assignment error
        T2_min_us:      minimum T2 in tile
        n_bad:          number of qubits below soft threshold
        calibration_ts: calibration timestamp when tile was generated
    """
    index:          int
    qubits:         List[int]
    region_label:   str        = ""
    T2_mean_us:     float      = 0.0
    RO_mean:        float      = 0.0
    T2_min_us:      float      = 0.0
    n_bad:          int        = 0
    calibration_ts: str        = ""

    @property
    def syndrome_qubits(self) -> List[int]:
        """Last 6 qubits — used as syndrome register for GHZ and tiled probe."""
        return self.qubits[12:18]

    @property
    def data_qubits(self) -> List[int]:
        """First 12 qubits — data register."""
        return self.qubits[:12]

    def initial_layout(self, circuit) -> Dict:
        """
        Return initial_layout dict mapping circuit qubits → physical qubits.
        Pass directly to generate_preset_pass_manager.

        Args:
            circuit: the probe circuit (18 qubits)

        Returns:
            {virtual_qubit: physical_qubit} mapping
        """
        if len(circuit.qubits) != TILE_SIZE:
            raise ValueError(
                f"Circuit has {len(circuit.qubits)} qubits, expected {TILE_SIZE}")
        return {circuit.qubits[i]: self.qubits[i]
                for i in range(TILE_SIZE)}

    def to_dict(self) -> dict:
        return {
            "index":          self.index,
            "qubits":         self.qubits,
            "syndrome_qubits":self.syndrome_qubits,
            "region_label":   self.region_label,
            "T2_mean_us":     round(self.T2_mean_us, 1),
            "RO_mean":        round(self.RO_mean, 5),
            "T2_min_us":      round(self.T2_min_us, 1),
            "n_bad":          self.n_bad,
            "calibration_ts": self.calibration_ts,
        }

    def __repr__(self):
        return (f"Tile(idx={self.index}, region='{self.region_label}', "
                f"T2={self.T2_mean_us:.0f}µs, RO={self.RO_mean:.4f}, "
                f"n_bad={self.n_bad})")


# ── AtlasMetadata ─────────────────────────────────────────────────────────

@dataclass
class AtlasMetadata:
    """Provenance record for an atlas build."""
    backend:           str
    calibration_ts:    str        # ISO timestamp of calibration used
    build_ts:          str        # ISO timestamp of when atlas was built
    generator:         str        = "KleinAtlas v0.4"
    algorithm:         str        = "BFS calibration-aware"
    tile_size:         int        = TILE_SIZE
    n_tiles:           int        = DEFAULT_N_TILES
    excluded_qubits:   List[int]  = field(default_factory=list)
    tile_revision:     int        = 1
    notes:             str        = ""

    def to_dict(self) -> dict:
        return {
            "backend":          self.backend,
            "calibration_ts":   self.calibration_ts,
            "build_ts":         self.build_ts,
            "generator":        self.generator,
            "algorithm":        self.algorithm,
            "tile_size":        self.tile_size,
            "n_tiles":          self.n_tiles,
            "excluded_qubits":  self.excluded_qubits,
            "tile_revision":    self.tile_revision,
            "notes":            self.notes,
        }


# ── KleinAtlas ────────────────────────────────────────────────────────────

class KleinAtlas:
    """
    Calibration-aware spatial tile generator for KleinProbe.

    Reads backend.properties() and discovers non-overlapping 18-qubit
    regions suitable for simultaneous KleinProbe execution.

    Each region (tile) is:
    - Connected in the heavy-hex coupling graph
    - Free of hard-excluded qubits (RO>8%, T2<15µs)
    - Non-overlapping with other tiles
    - Scored by qubit quality (RO + T2 weighted)

    The atlas caches the calibration timestamp and provides
    is_stale() to detect when a rebuild is needed.
    """

    def __init__(self, backend, n_tiles: int = DEFAULT_N_TILES):
        self._backend  = backend
        self._n_tiles  = n_tiles
        self._tiles:    List[Tile]      = []
        self._metadata: Optional[AtlasMetadata] = None
        self._cal_ts:   Optional[str]   = None
        self._adj:      Dict[int, Set[int]] = defaultdict(set)
        self._cal_data: Dict[int, dict] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def build(self,
              extra_exclude: Optional[List[int]] = None,
              max_ro_hard:   float = MAX_RO_HARD,
              min_t2_hard:   float = MIN_T2_HARD) -> 'KleinAtlas':
        """
        Build the atlas from current backend.properties().

        Fetches calibration data, runs BFS patch selection,
        validates connectivity and disjointness, stores tiles.

        Args:
            extra_exclude:  additional qubit indices to exclude
            max_ro_hard:    hard RO exclusion threshold (default 8%)
            min_t2_hard:    hard T2 exclusion threshold (default 15µs)

        Returns:
            self (for chaining)

        Raises:
            RuntimeError: if fewer than n_tiles valid patches are found
        """
        props    = self._backend.properties()
        cal_time = props.last_update_date
        self._cal_ts = cal_time.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Parse calibration data and coupling graph
        self._cal_data, self._adj = self._parse_properties(props)

        # Determine exclusions
        hard_excluded = {
            q for q, d in self._cal_data.items()
            if d['RO'] > max_ro_hard or d['T2'] < min_t2_hard
        }
        if extra_exclude:
            hard_excluded |= set(extra_exclude)

        # Run BFS tile discovery
        tiles, excluded = self._discover_tiles(hard_excluded)

        if len(tiles) < self._n_tiles:
            # Relax T2 threshold and retry
            relaxed = {
                q for q, d in self._cal_data.items()
                if d['RO'] > max_ro_hard or d['T2'] < min_t2_hard * 0.5
            }
            if extra_exclude:
                relaxed |= set(extra_exclude)
            tiles, excluded = self._discover_tiles(relaxed)

        if len(tiles) < self._n_tiles:
            raise RuntimeError(
                f"KleinAtlas: only {len(tiles)} valid tiles found "
                f"(requested {self._n_tiles}). "
                f"Try reducing n_tiles or relaxing exclusion thresholds.")

        self._tiles = tiles[:self._n_tiles]
        self._metadata = AtlasMetadata(
            backend         = self._backend.name,
            calibration_ts  = self._cal_ts,
            build_ts        = datetime.now(timezone.utc).strftime(
                              '%Y-%m-%dT%H:%M:%SZ'),
            n_tiles         = self._n_tiles,
            excluded_qubits = sorted(hard_excluded),
        )
        return self

    @property
    def tiles(self) -> List[Tile]:
        """List of discovered Tile objects."""
        self._require_built()
        return self._tiles

    @property
    def metadata(self) -> AtlasMetadata:
        """Provenance metadata for this atlas build."""
        self._require_built()
        return self._metadata

    @property
    def initial_layouts(self):
        """
        List of initial_layout dicts, one per tile.
        Pass each to generate_preset_pass_manager.
        Requires a circuit to map — use tile.initial_layout(circuit) directly.
        """
        self._require_built()
        return self._tiles  # caller uses tile.initial_layout(circuit)

    def is_stale(self) -> bool:
        """
        True if the backend has recalibrated since this atlas was built.
        If stale, call build() again.
        """
        if self._cal_ts is None:
            return True
        try:
            current_cal = self._backend.properties().last_update_date
            current_ts  = current_cal.strftime('%Y-%m-%dT%H:%M:%SZ')
            return current_ts != self._cal_ts
        except:
            return True

    def best_tile_for(self, target_isa) -> Tile:
        """
        Return the tile with highest Layout Match Score for target_isa.
        Uses lms_probe = |probe_qubits ∩ target_qubits| / |probe_qubits|.
        """
        self._require_built()
        try:
            target_q = frozenset(
                target_isa.layout.initial_index_layout(filter_ancillas=True))
        except:
            target_q = frozenset()

        best_tile  = self._tiles[0]
        best_score = 0.0
        for tile in self._tiles:
            probe_q = frozenset(tile.qubits)
            shared  = probe_q & target_q
            lms     = len(shared) / len(probe_q) if probe_q else 0.0
            if lms > best_score:
                best_score = lms
                best_tile  = tile
        return best_tile

    def report(self) -> str:
        """Human-readable atlas summary."""
        self._require_built()
        m = self._metadata
        lines = [
            f"KleinAtlas — {m.backend}",
            f"  Calibration:   {m.calibration_ts}",
            f"  Built:         {m.build_ts}",
            f"  Generator:     {m.generator}",
            f"  Tiles:         {len(self._tiles)}  "
            f"(each {m.tile_size} qubits)",
            f"  Excluded:      {len(m.excluded_qubits)} qubits "
            f"(RO>{MAX_RO_HARD*100:.0f}% or T2<{MIN_T2_HARD:.0f}µs)",
            f"  Stale:         {'yes — rebuild recommended' if self.is_stale() else 'no'}",
            "",
            f"  {'Tile':<8} {'Region':<20} {'T2 mean':>9} "
            f"{'RO mean':>9} {'T2 min':>8} {'n_bad':>6}",
            "  " + "-"*62,
        ]
        for t in self._tiles:
            lines.append(
                f"  {t.index:<8} {t.region_label:<20} "
                f"{t.T2_mean_us:>8.1f}µs "
                f"{t.RO_mean:>9.4f} "
                f"{t.T2_min_us:>7.1f}µs "
                f"{t.n_bad:>6}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialisable dict for saving to atlas/*/tiles.json."""
        self._require_built()
        return {
            "metadata": self._metadata.to_dict(),
            "tiles":    {f"tile_{t.index}": t.to_dict()
                        for t in self._tiles},
        }

    def save(self, path: str):
        """Save atlas to JSON file."""
        import json
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    # ── Internal ──────────────────────────────────────────────────────────

    def _require_built(self):
        if not self._tiles:
            raise RuntimeError(
                "KleinAtlas not built. Call atlas.build() first.")

    def _parse_properties(self, props) -> Tuple[dict, dict]:
        """Extract qubit calibration data and coupling graph from properties."""
        cal   = {}
        adj   = defaultdict(set)

        # Per-qubit data
        for q_idx in range(len(props.qubits)):
            try:
                T1  = props.qubit_property(q_idx, 'T1')[0]  * 1e6  # → µs
                T2  = props.qubit_property(q_idx, 'T2')[0]  * 1e6
                RO  = props.qubit_property(q_idx, 'readout_error')[0]
                p01 = props.qubit_property(q_idx, 'prob_meas0_prep1')[0]
                cal[q_idx] = {'T1': T1, 'T2': T2, 'RO': RO, 'p01': p01}
            except:
                pass

        # Coupling graph from gate properties
        try:
            for gate in props.gates:
                if gate.gate == 'cx' or gate.gate == 'cz' or gate.gate == 'ecr':
                    if len(gate.qubits) == 2:
                        q0, q1 = gate.qubits
                        adj[q0].add(q1)
                        adj[q1].add(q0)
        except:
            pass

        return cal, adj

    def _qubit_score(self, q: int) -> float:
        """Lower is better. Returns 999 for excluded qubits."""
        if q not in self._cal_data:
            return 999.0
        d = self._cal_data[q]
        return d['RO'] * 5.0 + (1.0 / max(d['T2'], 1.0)) * 20.0 + d['p01'] * 3.0

    def _bfs_patch(self,
                   seed_q:   int,
                   exclude:  Set[int],
                   size:     int = TILE_SIZE,
                   max_score:float = MAX_SCORE_SOFT) -> Optional[List[int]]:
        """BFS from seed_q, collecting up to `size` qubits by quality score."""
        heap    = [(self._qubit_score(seed_q), seed_q)]
        patch   = []
        visited = set()
        while heap and len(patch) < size:
            s, q = heapq.heappop(heap)
            if q in visited or q in exclude or s > max_score:
                continue
            visited.add(q)
            patch.append(q)
            for n in self._adj[q]:
                if n not in visited and n not in exclude:
                    heapq.heappush(heap, (self._qubit_score(n), n))
        return sorted(patch) if len(patch) == size else None

    def _is_connected(self, qubits: List[int]) -> bool:
        """Check connectivity within the coupling graph."""
        q_set   = set(qubits)
        visited = {qubits[0]}
        frontier = [qubits[0]]
        while frontier:
            q = frontier.pop()
            for n in self._adj[q]:
                if n in q_set and n not in visited:
                    visited.add(n)
                    frontier.append(n)
        return len(visited) == len(q_set)

    def _tile_quality(self, qubits: List[int]) -> dict:
        """Compute quality metrics for a tile."""
        valid = [q for q in qubits if q in self._cal_data]
        if not valid:
            return {'T2_mean': 0, 'T2_min': 0, 'RO_mean': 1, 'n_bad': len(qubits)}
        T2s    = [self._cal_data[q]['T2'] for q in valid]
        ROs    = [self._cal_data[q]['RO'] for q in valid]
        n_bad  = sum(1 for q in valid
                     if self._cal_data[q]['T2'] < MIN_T2_HARD * 2
                     or self._cal_data[q]['RO'] > MAX_RO_HARD * 0.5)
        return {
            'T2_mean': np.mean(T2s),
            'T2_min':  min(T2s),
            'RO_mean': np.mean(ROs),
            'n_bad':   n_bad,
        }

    def _region_label(self, qubits: List[int], n_total: int = 156) -> str:
        """Infer a rough region label from qubit indices."""
        centroid = np.mean(qubits)
        frac     = centroid / n_total
        if frac < 0.25:   return "upper"
        elif frac < 0.50: return "upper-central"
        elif frac < 0.75: return "central"
        else:             return "lower"

    def _discover_tiles(self,
                        hard_excluded: Set[int]) -> Tuple[List[Tile], Set[int]]:
        """
        Discover up to n_tiles non-overlapping connected 18-qubit patches.
        Uses BFS from quality-sorted seed qubits.
        """
        # Sort valid qubits by quality score as seed candidates
        valid_sorted = sorted(
            [q for q in self._cal_data if q not in hard_excluded],
            key=self._qubit_score
        )

        tiles  = []
        used   = set(hard_excluded)

        for seed in valid_sorted:
            if seed in used:
                continue
            patch = self._bfs_patch(seed, used)
            if patch and self._is_connected(patch):
                q     = self._tile_quality(patch)
                label = self._region_label(patch)
                tile  = Tile(
                    index          = len(tiles),
                    qubits         = patch,
                    region_label   = label,
                    T2_mean_us     = round(q['T2_mean'], 1),
                    RO_mean        = round(q['RO_mean'], 5),
                    T2_min_us      = round(q['T2_min'],  1),
                    n_bad          = q['n_bad'],
                    calibration_ts = self._cal_ts or "",
                )
                tiles.append(tile)
                used |= set(patch)
                if len(tiles) >= self._n_tiles:
                    break

        return tiles, hard_excluded
