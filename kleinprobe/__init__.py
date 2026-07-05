"""
kleinprobe
==========
Circuit-aware hardware observability layer for IBM Quantum.

KleinProbe answers the question backend.properties() cannot:
"What is the noise of *this circuit* on *these qubits* right now?"

It runs a lightweight Klein 3×2 topology probe (18 qubits, ~88 gates)
and returns circuit-conditioned noise metrics (H, inv, f, Z) with
drift detection vs known hardware baselines.

Quick start:
    from qiskit_ibm_runtime import QiskitRuntimeService
    from kleinprobe import KleinProbe

    service = QiskitRuntimeService()
    backend = service.backend("ibm_marrakesh")

    probe = KleinProbe(backend)

    # Single snapshot
    snap = probe.run()
    print(snap.report())

    # Drift tracking across a long experiment
    tracker = probe.track()
    tracker.checkpoint("before")
    run_my_experiment()
    tracker.checkpoint("after")
    print(tracker.report())

    # Quick health check
    status = probe.check()
    print(status)

Three-layer observability model:
    Layer 1  backend.properties()   global device state     (IBM provides)
    Layer 2  KleinProbe             circuit-aware snapshot  (this module)
    Layer 3  your experiment        actual results          (you run)

Layer 2 is the missing piece.

Validated hardware:
    ibm_fez:       H≈4.5, inv≈0.90  (6 sessions, Papers 1-6)
    ibm_marrakesh: H≈3.37, inv≈0.834 (1 session, job d93t5jcql68s73c8qg30)
    ibm_kingston:  pending

References:
    L. Roma, "Experimental Realization of the Klein Bottle Stabilizer Code
    on a Superconducting Processor", Zenodo (2026).
    doi:10.5281/zenodo.19454514
    https://github.com/theoricline/kleinprobe
"""

from .probe     import KleinProbe
from .snapshot  import Snapshot, CalibrationSlice
from .tracker   import DriftTracker
from .baselines import Baseline, BASELINES, get_baseline, register_baseline
from .circuit   import build_probe_circuit, PREDICTED, validate_prediction
from .state     import HardwareState, StateDelta, HardwareTrajectory

__version__ = "0.2.0"
__author__  = "Leonardo Roma"
__license__ = "MIT"

__all__ = [
    # v1 — unchanged
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
    # v2 — new
    "HardwareState",
    "StateDelta",
    "HardwareTrajectory",
]
