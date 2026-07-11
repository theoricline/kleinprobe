"""
kleinprobe
==========
Circuit-aware hardware observability layer for IBM Quantum.

KleinProbe answers the question backend.properties() cannot:
"What is the execution environment of THIS circuit on THESE qubits right now?"

The full KleinAtlas pipeline:

    backend.properties()
             ↓
       KleinAtlas.build()       — discovers calibration-aware spatial tiles
             ↓
        3 validated tiles
             ↓
       KleinProbe ×3            — tiled co-execution probe
             ↓
       ExecutionSnapshot        — H, inv, validity per tile
             ↓
       classify_tiled()         — VALID_SPATIAL / VALID_ANOMALOUS / INVALID

Quick start (single probe):
    from qiskit_ibm_runtime import QiskitRuntimeService
    from kleinprobe import KleinProbe

    service = QiskitRuntimeService()
    backend = service.backend("ibm_fez")

    probe = KleinProbe(backend)
    snap  = probe.run()
    print(snap.report())

Quick start (tiled atlas pipeline):
    from kleinprobe.atlas    import KleinAtlas
    from kleinprobe.validity import classify_tiled, VALID_SPATIAL
    from kleinprobe.circuit  import build_probe_circuit
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2 as Sampler

    atlas = KleinAtlas(backend)
    atlas.build()
    print(atlas.report())

    probe_qc = build_probe_circuit()
    pubs = []
    for tile in atlas.tiles:
        pm = generate_preset_pass_manager(
            optimization_level=3, backend=backend,
            initial_layout=tile.initial_layout(probe_qc),
            seed_transpiler=77)
        pubs.append((pm.run(probe_qc),))

    result = Sampler(backend).run(pubs, shots=4096).result()
    # ... extract H, inv, match per tile, then:
    validity = classify_tiled(tile_results, backend=backend.name)
    print(validity.execution_map(backend.name))

Three-layer observability model:
    Layer 1  backend.properties()   global device state     (IBM provides)
    Layer 2  KleinProbe             circuit-aware snapshot  (this module)
    Layer 3  your experiment        actual results          (you run)

Validity states:
    VALID_SPATIAL    — all tiles nominal, spatial ranking valid
    VALID_ANOMALOUS  — probe detected hardware anomaly (not a failure)
    INVALID          — dominant syndrome changed, H not interpretable

Validated hardware:
    ibm_fez:       Era2 H≈2.98±0.18  (n=11 sessions, post-Jul5)
    ibm_marrakesh: H≈3.13±0.27       (n=12 sessions)
    ibm_kingston:  H≈2.69±0.10       (n=9 sessions)

References:
    L. Roma, KleinProbe formalism, Zenodo (2026).
    doi:10.5281/zenodo.21186259
    https://github.com/theoricline/kleinprobe
"""

from .probe     import KleinProbe
from .snapshot  import Snapshot, CalibrationSlice
from .tracker   import DriftTracker
from .baselines import Baseline, BASELINES, get_baseline, register_baseline
from .circuit   import build_probe_circuit, PREDICTED, validate_prediction
from .state     import (HardwareState, StateDelta, HardwareTrajectory,
                        LayoutMatchResult, layout_match_score)
from .metrics   import (P0, Z0, N_SYN,
                        syndrome_entropy, invariant_fraction,
                        dominant_frequency, z_raw,
                        probe_signal_score, compute_all)
from .atlas     import KleinAtlas, Tile, AtlasMetadata
from .validity  import (ValidityResult, TiledValidityResult,
                        classify_tile, classify_tiled,
                        OPTIMAL, ELEVATED, CRITICAL, INVALID,
                        VALID_SPATIAL, VALID_ANOMALOUS)
from .validity  import (compute_lms, lms_label,
                        effective_score, routing_report)

__version__ = "0.4.1"
__author__  = "Leonardo Roma"
__license__ = "MIT"

__all__ = [
    # v1 — core probe
    "KleinProbe",
    "Snapshot",
    "CalibrationSlice",
    "DriftTracker",
    "Baseline",
    "BASELINES",
    "get_baseline",
    "register_baseline",
    "build_probe_circuit",
    "PREDICTED",
    "validate_prediction",
    # v2 — state layer
    "HardwareState",
    "StateDelta",
    "HardwareTrajectory",
    "LayoutMatchResult",
    "layout_match_score",
    # metrics — canonical definitions
    "P0", "Z0", "N_SYN",
    "syndrome_entropy",
    "invariant_fraction",
    "dominant_frequency",
    "z_raw",
    "probe_signal_score",
    "compute_all",
    # v0.4 — atlas + validity
    "KleinAtlas",
    "Tile",
    "AtlasMetadata",
    "ValidityResult",
    "TiledValidityResult",
    "classify_tile",
    "classify_tiled",
    # v0.4 hardware state model
    "OPTIMAL",
    "ELEVATED",
    "CRITICAL",
    "INVALID",
    # LMS and routing
    "compute_lms",
    "lms_label",
    "effective_score",
    "routing_report",
    # backward compat aliases
    "VALID_SPATIAL",
    "VALID_ANOMALOUS",
]
# opt-in modules:
#   from kleinprobe.analyzer import DriftAnalyzer, QueueDriftTracker
#   from kleinprobe.policy   import PolicyBase, NullPolicy
#   from kleinprobe.tiling   import TiledSnapshot, SpatialHardwareState
#   from kleinprobe.atlas    import KleinAtlas   (also available at top level)
#   from kleinprobe.validity import classify_tiled (also available at top level)
