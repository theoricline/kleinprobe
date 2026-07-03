# KleinProbe

**Circuit-aware hardware observability layer for IBM Quantum.**

```
backend.properties()  →  KleinProbe  →  your experiment
[global device state]    [circuit-aware   [actual results]
                          snapshot]
```

KleinProbe answers the question `backend.properties()` cannot:

> *"What is the noise of **this circuit** on **these qubits** right now?"*

---

## The problem

`backend.properties()` gives you yesterday's calibration for all 156 qubits.  
Your circuit runs on 18 of them — a different 18 every time the transpiler runs.  
The global calibration tells you almost nothing about what your specific circuit will experience.

KleinProbe fixes this. It runs a lightweight structured probe on the **same physical qubits your circuit uses**, in the **same transpilation context**, and returns noise metrics that are actually representative of your experiment's hardware conditions.

---

## Install

```bash
pip install kleinprobe
```

Or from source:
```bash
git clone https://github.com/theoricline/kleinprobe
cd kleinprobe
pip install -e .
```

---

## Quick start

```python
from qiskit_ibm_runtime import QiskitRuntimeService
from kleinprobe import KleinProbe

service = QiskitRuntimeService()
backend = service.backend("ibm_marrakesh")

probe = KleinProbe(backend)
snap  = probe.run()
print(snap.report())
```

Output:
```
KleinProbe Snapshot — 2026-04-08T16:14:52Z
  Backend:   ibm_marrakesh
  Job:       d93t5jcql68s73c8qg30
  δ=0  shots=1024  depth=87

  Predicted: '100001'
  Got:       '100001'  f=0.4836  ✓ MATCH

  H   = 3.3686 bits   (syndrome entropy)
  inv = 0.8340         (Klein invariant fraction)
  Z   = 242.0σ         (vs flat baseline)
```

---

## Drift tracking

Monitor calibration drift across a long experiment:

```python
tracker = probe.track()

tracker.checkpoint("before")      # probe run before your experiment
run_my_experiment_batch_1()
tracker.checkpoint("mid")          # probe run mid-experiment
run_my_experiment_batch_2()
tracker.checkpoint("after")        # probe run after experiment

print(tracker.report())
```

Output:
```
============================================================
KleinProbe Drift Report
Backend:     ibm_marrakesh
Checkpoints: 3
Match rate:  100%
H range:     3.34 – 3.89 bits
inv range:   0.771 – 0.841
============================================================

  #      time        H      inv      ΔH    Δinv  match  alert
  ----------------------------------------------------------------
  1  16:14:52   3.3400   0.8410   -0.029  +0.007    ✓
  2  17:23:11   3.4100   0.8290   +0.041  -0.005    ✓
  3  18:45:03   3.8900   0.7710   +0.521  -0.063    ✓   ⚠️ ALERT

ALERTS:
  Checkpoint 3: Calibration drift detected:
    H=3.890 is higher than baseline 3.369±0.100 (Δ=+0.521)
```

---

## What it measures

| Metric | Meaning | Sensitive to |
|--------|---------|-------------|
| `H` | Shannon entropy of syndrome distribution | Total noise — more noise = higher H |
| `inv` | Klein invariant fraction (P(bit₀=1)) | Antipodal edge qubit quality (RO, T2) |
| `f` | Dominant pattern frequency | Topological SNR |
| `Z` | Significance vs flat baseline | Circuit health — healthy chip: Z > 100σ |

These metrics are **circuit-conditioned**: they reflect the physical qubits your circuit actually uses, not the device average.

---

## Three-layer observability model

```
Layer 1  backend.properties()   global device state     IBM provides
Layer 2  KleinProbe             circuit-aware snapshot  this module
Layer 3  your experiment        actual results          you run
```

Layer 2 is the missing piece. IBM gives you Layer 1. You produce Layer 3.  
KleinProbe fills the gap between them.

---

## API

### `KleinProbe(backend, shots=1024, seed=77, delta=0)`

```python
probe = KleinProbe(backend)

# Single snapshot
snap = probe.run()
snap = probe.run(delta=1)      # use δ=1 for a different probe pattern

# Full characterisation (all 3 δ values, one job)
summary = probe.run_full()
print(summary['manifold_point'])   # (H_mean, inv_mean) across δ values

# Quick health check
status = probe.check()
# {'status': 'OK', 'H': 3.37, 'inv': 0.834, 'Z': 242.0, 'alert': None}

# Drift tracker
tracker = probe.track(baseline_sigma=2.0)
```

### `Snapshot`

```python
snap.H             # entropy
snap.inv           # Klein invariant fraction
snap.f             # dominant frequency
snap.Z             # Z-score
snap.match         # dominant == predicted pattern
snap.is_healthy    # True if match and Z > 50σ
snap.alert         # drift alert string or None
snap.noise_point   # (H, inv) tuple
snap.calibration   # CalibrationSlice with per-qubit T1/T2/RO
snap.report()      # human-readable string
snap.to_json()     # serialise to JSON
```

### `DriftTracker`

```python
tracker = probe.track()
tracker.checkpoint()           # run probe and store snapshot
tracker.has_drift              # True if any alert fired
tracker.H_series               # list of H values across checkpoints
tracker.inv_series             # list of inv values
tracker.report()               # full drift report string
tracker.to_dict()              # serialise to dict
```

---

## Known hardware baselines

KleinProbe ships with validated reference values from real hardware:

| Backend | H (mean ± σ) | inv (mean ± σ) | Sessions |
|---------|-------------|----------------|----------|
| `ibm_fez` | 4.50 ± 0.15 | 0.900 ± 0.020 | 6 |
| `ibm_marrakesh` | 3.37 ± 0.10 | 0.834 ± 0.020 | 1 |
| `ibm_kingston` | 3.80 ± 0.20 (est.) | 0.870 ± 0.030 (est.) | pending |

Snapshots that deviate >2σ from the baseline trigger an alert.

Add your own baseline:
```python
from kleinprobe import Baseline, register_baseline

register_baseline(Baseline(
    backend   = "ibm_torino",
    H_mean    = 3.50, H_std  = 0.12,
    inv_mean  = 0.85, inv_std = 0.025,
    f_mean    = 0.47, f_std   = 0.015,
    n_sessions = 1,
))
```

---

## Overhead

- **Circuit size:** 18 qubits, depth ~88 gates
- **Runtime:** ~15-30 seconds on IBM open plan
- **Cost:** 1 PUB, 1024 shots (≈ negligible vs main experiment)
- **Can be submitted as a PUB alongside your main circuit** — zero additional queue wait time

---

## Background

KleinProbe is based on the Klein bottle stabilizer code — a non-orientable
topological stabilizer code whose syndrome output is analytically predictable.
This makes it an effective probe: the topology tells you what *should* happen;
the hardware tells you what *does* happen; the gap characterises the noise.

The probe circuit uses the 3×2 Klein code (18 qubits) with boundary
parameter δ ∈ {0,1,2} selecting distinct predicted syndrome patterns.
Validated on IBM Fez, Marrakesh, and Kingston across multiple sessions.

**References:**
- L. Roma, "Experimental Realization of the Klein Bottle Stabilizer Code on a Superconducting Processor", Zenodo (2026). [doi:10.5281/zenodo.19454514](https://doi.org/10.5281/zenodo.19454514)
- Source: [github.com/theoricline/kleinprobe](https://github.com/theoricline/kleinprobe)
- Research repo: [github.com/theoricline/klein-bottle-qec](https://github.com/theoricline/klein-bottle-qec)

---

## License

MIT © Leonardo Roma 2026
