# KleinProbe

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21186260.svg)](https://doi.org/10.5281/zenodo.21186260)

**Circuit-conditioned hardware probing layer for IBM Quantum systems**

```
backend.properties()  →  KleinProbe  →  your experiment
[global device state]    [circuit-aware   [actual results]
                          snapshot]
```

---

## Overview

KleinProbe uses a fixed stabilizer circuit to sample syndrome statistics on the same physical qubits selected by the transpiler for a given target experiment.

The resulting metrics characterize the effective noise experienced by that specific circuit instance, including **layout-dependent and time-dependent effects not fully captured by static device calibration data alone**.

KleinProbe does not modify hardware, error correction schemes, or execution paths. It is a diagnostic layer that runs alongside quantum experiments.

---

## The problem

`backend.properties()` provides calibration data for all qubits on a device, typically updated on a daily timescale.

However:
- Your circuit is executed on a **subset of qubits selected dynamically by the transpiler**
- That subset changes across runs depending on optimization, routing, and circuit structure
- Calibration data is not conditioned on the actual circuit layout or execution instance

As a result, static calibration may not fully represent the **effective noise environment experienced by a specific circuit execution**.

---

## KleinProbe approach

KleinProbe achieves this by:
- Running a lightweight structured **probe circuit**
- Executing it on the **same physical qubits selected for your target circuit**
- Using the same transpilation context where possible
- Extracting syndrome-based statistical metrics from the hardware response

This produces a **circuit-conditioned noise snapshot** aligned with the actual execution environment of your experiment.

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

  H     = 3.3686 bits   (syndrome entropy)
  inv   = 0.8340         (Klein invariant fraction)
  Z_raw = 120.7          (statistical significance vs uniform baseline)
  S     = 1.000          (probe signal score)
```

---

## Drift tracking

Monitor calibration drift across a long experiment.

```python
tracker = probe.track()

tracker.checkpoint("before")
run_my_experiment_batch_1()
tracker.checkpoint("mid")
run_my_experiment_batch_2()
tracker.checkpoint("after")

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

  #      time        H      inv      S     ΔH    Δinv  match  alert
  ----------------------------------------------------------------
  1  16:14:52   3.3400   0.8410   1.000   -0.029  +0.007    ✓
  2  17:23:11   3.4100   0.8290   1.000   +0.041  -0.005    ✓
  3  18:45:03   3.8900   0.7710   0.780   +0.521  -0.063    ✓   ⚠️ ALERT

ALERTS:
  Checkpoint 3: Calibration drift detected:
    H=3.890 is higher than baseline 3.369±0.100 (Δ=+0.521)
```

---

## v2: Hardware state objects and drift analysis

Version 0.2.0 introduces structured state objects and a drift analysis layer, while keeping the core API fully backward compatible.

### HardwareState

A structured estimate of the circuit-conditioned hardware state Θ(L,t).

```python
from kleinprobe import HardwareState

state = HardwareState.from_snapshot(snap)

state.vector            # np.array([H, inv, f, Z_raw, S])
state.primary_vector    # np.array([H, inv, f])
state.regime            # 'high_entropy' | 'mid_entropy' | 'collapsed'
state.effective_patterns  # 2^H — effective number of syndrome patterns
state.is_healthy        # bool
```

### StateDelta

Difference between two HardwareState observations: Δθ = θ₂ − θ₁.

```python
delta = state2 - state1

delta.dH              # ΔH (bits)
delta.dinv            # Δ invariant fraction
delta.drift_score     # scalar 0-1 (>0.5 = significant drift)
delta.is_significant  # bool
delta.norm_l2         # L2 norm of primary change vector
delta.norm_inf        # L∞ norm
delta.direction       # unit vector of change direction
delta.relative_change # {'H': 0.12, 'inv': 0.03, 'f': 0.08}
delta.dominant_shift  # which component changed most
delta.summary()       # human-readable string
```

### HardwareTrajectory

Time-ordered sequence of HardwareState observations.

```python
from kleinprobe import HardwareTrajectory

traj = HardwareTrajectory(label="overnight_run")
traj.add(HardwareState.from_snapshot(probe.run()))
run_experiment_batch()
traj.add(HardwareState.from_snapshot(probe.run()))

traj.stability          # float 0-1 (1.0 = perfectly stable)
traj.max_drift          # StateDelta with highest drift score
traj.cumulative_delta   # total Δθ from first to last
traj.had_regime_change  # bool
traj.H_series           # list of H values
traj.duration           # seconds from first to last observation
traj.summary()          # human-readable report
```

### DriftAnalyzer (opt-in)

Interprets a trajectory. Not imported by default — keeps the core package a pure observer.

```python
from kleinprobe.analyzer import DriftAnalyzer

analyzer = DriftAnalyzer(baseline_backend="ibm_marrakesh")
analysis = analyzer.analyze(traj)

analysis.trend          # 'stable' | 'drifting' | 'degraded' | 'recovering'
analysis.H_trend        # 'rising' | 'falling' | 'flat'
analysis.stability      # float
analysis.alerts         # list of alert strings
analysis.summary()      # human-readable report
```

### QueueDriftTracker (opt-in)

Tracks the θ₁/θ₂/Δθ pattern for queued jobs. Measures hardware state at submission and again after execution to quantify how much the hardware changed while your job waited.

```python
from kleinprobe.analyzer import QueueDriftTracker

tracker = QueueDriftTracker(probe)

tracker.record_submission()     # run probe, store θ₁
job = sampler.run(my_circuits)  # submit your job
job.result()                    # wait for execution

result = tracker.record_execution()  # run probe, store θ₂
print(result.summary())

# QueueDriftResult:
#   was_stable:      bool
#   delta:           StateDelta (θ₂ − θ₁)
#   queue_time_s:    float (seconds in queue)
#   recommendation:  'proceed' | 'caution' | 'pause'
```

---

## What it measures

| Metric | Meaning | Sensitive to |
|--------|---------|-------------|
| `H` | Shannon entropy of syndrome distribution (bits) | Total noise — more noise = higher H |
| `inv` | Klein invariant fraction — P(bit₀=1) | Antipodal edge qubit quality (RO, T2) |
| `f` | Dominant pattern frequency | Topological signal strength |
| `Z_raw` | Statistical significance vs uniform baseline | `Z_raw = (f − p₀) / σ` — suitable for hypothesis testing |
| `S` | Probe signal score — `clip(Z_raw / 50, 0, 1)` | Engineering indicator: S=1.0 strong signal, S<0.5 degraded |

**Note:** `Z_raw` and `S` serve distinct roles and are not interchangeable. `Z_raw` is a statistical object; `S` is an engineering indicator. See [doi:10.5281/zenodo.21186260](https://doi.org/10.5281/zenodo.21186260) for formal definitions.

---

## Architecture

```
Measurement layer (core):
  circuit.py → snapshot.py → probe.py → tracker.py

State layer (v0.2):
  state.py → HardwareState, StateDelta, HardwareTrajectory

Metrics layer (canonical definitions):
  metrics.py → P0, Z0, all metric formulas

Interpretation layer (opt-in):
  analyzer.py → DriftAnalyzer, QueueDriftTracker

Policy layer (opt-in, interface only):
  policy.py → PolicyBase, NullPolicy (implementations: future)
```

KleinProbe measures. `analyzer.py` interprets. Users decide.

---

## Recommended workflow

```python
# 1. Measure — run the probe
snap  = probe.run()

# 2. Structure — wrap in a state object
from kleinprobe import HardwareState
state = HardwareState.from_snapshot(snap)
print(state.regime)            # 'high_entropy' | 'mid_entropy' | 'collapsed'

# 3. Track — build a trajectory over your experiment
from kleinprobe import HardwareTrajectory
traj = HardwareTrajectory(label="my_experiment")
traj.add(state)
run_experiment_batch()
traj.add(HardwareState.from_snapshot(probe.run()))

# 4. Interpret — analyse the trajectory (opt-in)
from kleinprobe.analyzer import DriftAnalyzer
analysis = DriftAnalyzer(baseline_backend="ibm_marrakesh").analyze(traj)
print(analysis.trend)          # 'stable' | 'drifting' | 'degraded'
print(analysis.alerts)         # list of alert strings
```

---

## Non-goals

KleinProbe explicitly does NOT:
- Modify hardware, transpiler settings, or execution parameters
- Perform automatic transpiler optimization or layout selection
- Perform quantum error correction
- Predict circuit fidelity as a primary objective
- Make execution decisions autonomously
- Replace `backend.properties()` — it complements it

---

```
Layer 1  backend.properties()   global device state     IBM provides
Layer 2  KleinProbe             circuit-aware snapshot  this module
Layer 3  your experiment        actual results          you run
```

KleinProbe provides a missing intermediate abstraction: circuit-conditioned hardware profiling.

---

## Known hardware baselines

| Backend | H (mean ± σ) | inv (mean ± σ) | Sessions | Regime |
|---------|-------------|----------------|----------|--------|
| `ibm_fez` | 4.50 ± 0.15 | 0.900 ± 0.020 | 6 | high_entropy |
| `ibm_marrakesh` | 3.37 ± 0.10 | 0.834 ± 0.020 | 2 | high_entropy |
| `ibm_kingston` | 1.05 ± 0.15 | 0.947 ± 0.020 | 1 | collapsed |

Snapshots deviating >2σ from baseline trigger an alert. Note that `ibm_kingston` operates in a **collapsed regime** (H≈1.05, ~2 effective syndrome patterns). Cross-backend H comparisons require per-backend normalisation: `H_norm = (H − μ) / σ`.

---

## Overhead

- **Circuit size:** 18 qubits, depth ~88 gates
- **Runtime:** ~15-30 seconds on IBM open plan
- **Cost:** 1 PUB, 1024 shots (negligible vs main experiment)
- **Can be submitted as a PUB alongside your main circuit** — zero additional queue wait time

---

## Background

KleinProbe is based on a structured stabilizer probe circuit derived from a non-orientable topological construction. The topology is used as a fixed, reproducible measurement kernel, enabling consistent sampling of syndrome statistics across hardware conditions. The probe circuit is used to extract circuit-conditioned noise signatures rather than to perform error correction.

Validated on IBM Fez, Marrakesh, and Kingston (Heron r2 processors) across multiple calibration sessions.

**Formalism paper:** L. Roma, "KleinProbe: A Circuit-Conditioned Statistical Estimator for Effective Quantum Hardware Noise", Zenodo (2026). [doi:10.5281/zenodo.21186260](https://doi.org/10.5281/zenodo.21186260)

**Research:** L. Roma, "Experimental Realization of the Klein Bottle Stabilizer Code on a Superconducting Processor", Zenodo (2026). [doi:10.5281/zenodo.19454514](https://doi.org/10.5281/zenodo.19454514)

---

## License

MIT © Leonardo Roma 2026
