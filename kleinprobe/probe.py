"""
kleinprobe/probe.py
===================
KleinProbe: circuit-aware hardware observability layer.

The central class. Runs a Klein 3×2 probe circuit on a backend,
extracts the circuit-conditioned calibration projection (which
physical qubits are actually used), and returns a Snapshot with
noise metrics (H, inv, f, Z) and drift alerts vs baseline.

Quick start:
    from qiskit_ibm_runtime import QiskitRuntimeService
    from kleinprobe import KleinProbe

    service = QiskitRuntimeService()
    backend = service.backend("ibm_marrakesh")

    probe = KleinProbe(backend)
    snap  = probe.run()
    print(snap.report())

    # Drift tracking
    tracker = probe.track()
    tracker.checkpoint()        # before experiment
    run_my_experiment()
    tracker.checkpoint()        # after experiment
    print(tracker.report())
"""

import time
import numpy as np
from datetime import datetime, timezone
from typing import Optional

from .circuit   import build_probe_circuit, N_DATA, N_SYN
from .snapshot  import Snapshot, CalibrationSlice
from .baselines import get_baseline
from .tracker   import DriftTracker

try:
    from qiskit_ibm_runtime import SamplerV2 as Sampler
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    QISKIT_AVAILABLE = True
except ImportError:
    QISKIT_AVAILABLE = False


P0 = 1 / 2**N_SYN   # flat baseline probability (1/64)


def _entropy(counts: dict, shots: int) -> float:
    H = 0.0
    for c in counts.values():
        p = c / shots
        if p > 0: H -= p * np.log2(p)
    return round(H, 4)


def _inv_fraction(counts: dict, shots: int) -> float:
    """Fraction of shots where syndrome bit 0 fires (Klein invariant)."""
    n = sum(v for k, v in counts.items() if k[-1] == '1')
    return round(n / shots, 4)


def _z_score(f: float, shots: int) -> float:
    return round((f - P0) / (P0 * (1 - P0) / shots)**0.5, 1)


def _extract_layout(transpiled_circuit):
    """
    Extract logical → physical qubit mapping from a transpiled circuit.

    Returns:
        data_map: {logical_data_idx: physical_qubit_idx}
        syn_map:  {logical_syn_idx:  physical_qubit_idx}
    """
    layout = getattr(transpiled_circuit, 'layout', None)
    if layout is None:
        return {}, {}

    initial = getattr(layout, 'initial_layout', None)
    if initial is None:
        return {}, {}

    data_map = {}
    syn_map  = {}

    try:
        for phys_idx, qubit in initial.items():
            if not hasattr(qubit, '_register'):
                continue
            reg  = qubit._register
            name = reg.name if hasattr(reg, 'name') else str(reg)
            log  = qubit._index if hasattr(qubit, '_index') else None

            if log is None:
                continue

            if name == 'd':
                data_map[log] = phys_idx
            elif name == 's':
                syn_map[log] = phys_idx
    except Exception:
        pass

    return data_map, syn_map


def _extract_calibration(backend, data_map: dict, syn_map: dict
                          ) -> CalibrationSlice:
    """
    Extract calibration data for the specific physical qubits used
    by the circuit. This is the circuit-conditioned calibration
    projection — the core of KleinProbe's value.
    """
    t1_us  = {}
    t2_us  = {}
    ro_err = {}

    all_phys = set(data_map.values()) | set(syn_map.values())

    try:
        props = backend.properties()
        for phys in all_phys:
            try:
                t1 = props.qubit_property(phys, 'T1')
                if t1 is not None:
                    t1_us[phys] = t1[0] if isinstance(t1, tuple) else t1

                t2 = props.qubit_property(phys, 'T2')
                if t2 is not None:
                    t2_us[phys] = t2[0] if isinstance(t2, tuple) else t2

                ro = props.qubit_property(phys, 'readout_error')
                if ro is not None:
                    ro_err[phys] = ro[0] if isinstance(ro, tuple) else ro
            except Exception:
                pass
    except Exception:
        pass

    return CalibrationSlice(
        physical_data_qubits = data_map,
        physical_syn_qubits  = syn_map,
        t1_us  = t1_us  or None,
        t2_us  = t2_us  or None,
        ro_err = ro_err or None,
    )


class KleinProbe:
    """
    Circuit-aware hardware observability layer.

    Runs a Klein 3×2 topology probe on a backend and returns
    circuit-conditioned noise metrics (H, inv, f, Z) with
    drift detection vs known hardware baselines.

    The probe circuit uses 18 qubits (depth ~88) and adds
    approximately 15-30 seconds to any IBM Quantum job.
    It can be submitted as a PUB alongside the main experiment
    for zero additional wait time.
    """

    def __init__(self, backend, shots: int = 1024, seed: int = 77,
                 delta: int = 0, verbose: bool = False):
        """
        Args:
            backend:  IBM Quantum backend (from QiskitRuntimeService)
            shots:    Measurement shots (default 1024 — lightweight)
            seed:     Transpiler seed (default 77 — validated across chips)
            delta:    Default δ value (0, 1, or 2)
            verbose:  Print progress during run
        """
        if not QISKIT_AVAILABLE:
            raise ImportError(
                "KleinProbe requires qiskit and qiskit-ibm-runtime. "
                "Install with: pip install qiskit qiskit-ibm-runtime")

        self.backend = backend
        self.shots   = shots
        self.seed    = seed
        self.delta   = delta
        self.verbose = verbose
        self._pm     = None

    def _get_pm(self):
        if self._pm is None:
            self._pm = generate_preset_pass_manager(
                optimization_level=3,
                backend=self.backend,
                seed_transpiler=self.seed)
        return self._pm

    def run(self, delta: Optional[int] = None, wait: bool = True) -> Snapshot:
        """
        Run a single probe snapshot.

        Args:
            delta:  δ value override (uses self.delta if None)
            wait:   If True, block until job completes (default True)

        Returns:
            Snapshot with all noise metrics and layout info
        """
        d = delta if delta is not None else self.delta
        qc, predicted, flip_edge = build_probe_circuit(d)

        # Transpile
        pm  = self._get_pm()
        isa = pm.run(qc)

        if self.verbose:
            print(f"[KleinProbe] δ={d} depth={isa.depth()} "
                  f"→ {self.backend.name} ({self.shots} shots)")

        # Extract layout before submitting
        data_map, syn_map = _extract_layout(isa)
        cal = _extract_calibration(self.backend, data_map, syn_map)

        # Submit
        sampler = Sampler(self.backend)
        job     = sampler.run([(isa,)], shots=self.shots)
        ts      = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        if wait:
            t0 = time.time()
            while True:
                st = str(job.status())
                if "DONE"   in st: break
                if "ERROR"  in st or "CANCEL" in st:
                    raise RuntimeError(f"Job {job.job_id()} failed: {st}")
                time.sleep(3)
            if self.verbose:
                print(f"[KleinProbe] Done in {time.time()-t0:.0f}s "
                      f"(job {job.job_id()})")

        # Analyse
        result = job.result()
        counts = result[0].data.c.get_counts()

        dom = max(counts, key=counts.get)
        f   = counts[dom] / self.shots
        H   = _entropy(counts, self.shots)
        inv = _inv_fraction(counts, self.shots)
        Z   = _z_score(f, self.shots)

        snap = Snapshot(
            timestamp        = ts,
            backend          = self.backend.name,
            job_id           = job.job_id(),
            delta            = d,
            shots            = self.shots,
            seed             = self.seed,
            circuit_depth    = isa.depth(),
            predicted_pattern= predicted,
            flip_edge        = flip_edge,
            dominant_pattern = dom,
            dominant_f       = round(f, 4),
            match            = (dom == predicted),
            H                = H,
            inv              = inv,
            Z                = Z,
            calibration      = cal,
            counts           = counts,
        )

        # Baseline comparison
        bl = get_baseline(self.backend.name)
        if bl:
            snap.delta_H   = bl.delta_H(H)
            snap.delta_inv = bl.delta_inv(inv)
            snap.alert     = bl.alert_message(H, inv)

        return snap

    def run_full(self, wait: bool = True) -> list:
        """
        Run all 3 δ values in one job (3 PUBs).
        Returns a list of Snapshots and a summary dict.

        Useful for a richer characterisation of the noise manifold point.
        """
        snapshots = []
        for d in [0, 1, 2]:
            snap = self.run(delta=d, wait=wait)
            snapshots.append(snap)

        # Compute noise manifold point (centroid across δ values)
        H_vals   = [s.H   for s in snapshots]
        inv_vals = [s.inv for s in snapshots]

        summary = {
            'backend':         self.backend.name,
            'H_mean':          round(float(np.mean(H_vals)),   4),
            'inv_mean':        round(float(np.mean(inv_vals)), 4),
            'H_spread':        round(float(np.std(H_vals)),    4),
            'inv_spread':      round(float(np.std(inv_vals)),  4),
            'match_rate':      sum(s.match for s in snapshots) / 3,
            'any_alert':       any(s.alert for s in snapshots),
            'manifold_point':  (round(float(np.mean(H_vals)),   4),
                                round(float(np.mean(inv_vals)), 4)),
            'snapshots':       snapshots,
        }
        return summary

    def track(self, baseline_sigma: float = 2.0,
              auto_update_baseline: bool = False) -> DriftTracker:
        """
        Return a DriftTracker wrapping this probe for multi-checkpoint runs.

        Usage:
            tracker = probe.track()
            tracker.checkpoint("pre")
            run_experiment()
            tracker.checkpoint("post")
            print(tracker.report())
        """
        return DriftTracker(
            probe=self,
            baseline_sigma=baseline_sigma,
            auto_update_baseline=auto_update_baseline,
        )

    def check(self) -> dict:
        """
        Quick health check: run a single snapshot and return a
        human-readable status dict. Does not require wait=True.
        """
        snap = self.run()
        status = "OK" if snap.is_healthy else ("DRIFT" if snap.alert else "WARN")
        return {
            'status':  status,
            'backend': snap.backend,
            'match':   snap.match,
            'H':       snap.H,
            'inv':     snap.inv,
            'Z':       snap.Z,
            'alert':   snap.alert,
        }
