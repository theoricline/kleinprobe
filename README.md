# KleinProbe

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21186259.svg)](https://doi.org/10.5281/zenodo.21186259)

**A spatial execution environment sensor for IBM Quantum**

KleinProbe measures the local execution environment of chip regions
through a structured 18-qubit probe circuit. It answers:

> *"Which region of this chip is currently the quietest place to run my circuit?"*

Not: *"Will my circuit succeed?"* — that depends on the circuit.

```
backend.properties()        ← static calibration snapshot
         ↓
   KleinAtlas.build()       ← discovers spatial tiles
         ↓
    KleinProbe × N          ← tiled co-execution probe (~4 seconds, 0 queue overhead)
         ↓
   SpatialMap               ← ranked spatial environment map
         ↓
   Route to rank #1         ← lowest-H usable tile
```

---

## The core finding

**Absolute H values drift with calibration cycles.
Relative spatial ordering remains stable.**

Validated across three IBM Heron r2 processors:

- ibm_fez central region: **ranked #1 in 6/6 independent runs**
  despite H varying 0.54 bits across sessions
- ibm_marrakesh upper-left: **ranked #1 in 4/4 independent runs**
- ibm_kingston lower-left/middle: consistently best region

The spatial fingerprint of a chip is reproducible. Use it for routing.

---

## The gap that motivates this

`backend.properties()` tells you qubit T2, T1, and readout error
measured in isolation. It does not tell you:

- Which chip region has the lowest execution noise **right now**
- That the region with the best T2 can have the worst CX gate errors
  (the "patch_2 paradox" on ibm_fez — best T2, consistently worst H)
- That a chip can look normal in single-qubit metrics while
  3 of 8 spatial regions are in PROBE_INVALID state
- That a single-tile probe on the default seed-77 routing misses
  5–7 bits of spatial H variation visible across the full chip

---

## Install

```bash
pip install git+https://github.com/theoricline/kleinprobe.git
```

Requires: `qiskit >= 2.0`, `qiskit-ibm-runtime >= 0.20`

---

## Quick start — single probe

```python
from qiskit_ibm_runtime import QiskitRuntimeService
from kleinprobe import KleinProbe

service = QiskitRuntimeService()
backend = service.backend("ibm_fez")

snap = KleinProbe(backend).run()
print(snap.report())
# H=2.92  inv=0.901  dom='100001' ✓  Z=287.3
```

A hardware snapshot. `H` is the primary metric — lower is quieter.

---

## Spatial routing — the main use case

```python
from kleinprobe import classify_tiled
from kleinprobe.circuit import build_probe_circuit
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime import SamplerV2 as Sampler

probe_qc = build_probe_circuit()

# Transpile probe for each tile — optimization_level=0 preserves layout
pubs = []
for tile in atlas.tiles:
    pm = generate_preset_pass_manager(
        optimization_level=0, backend=backend,
        initial_layout={probe_qc.qubits[j]: tile["qubits"][j]
                        for j in range(18)},
        seed_transpiler=77)
    pubs.append((pm.run(probe_qc),))

# Run all tiles in one job — zero queue overhead
result = Sampler(backend).run(pubs, shots=4096).result()

# Classify and rank
tile_data = [
    {"match": True, "H": 4.11, "inv": 0.69, "region_label": "central"},
    {"match": True, "H": 4.85, "inv": 0.68, "region_label": "upper-left"},
    {"match": True, "H": 5.44, "inv": 0.62, "region_label": "lower-left"},
]
sm = classify_tiled(tile_data, backend="ibm_fez")
print(sm.spatial_map())
```

Output:

```
SPATIAL ENVIRONMENT MAP — ibm_fez

  Rank  Region              H       Deviation
  ──────────────────────────────────────────
   #1   central           4.112   ▲▲ high
   #2   upper-left        4.846   ▲▲ high
   #3   lower-left        5.443   ▲▲▲ very high

  Route to: central (#1)
  Baseline reference: H_ref = 2.977 ± 0.215
```

Route to rank #1. Done.

---

## Deviation states

States describe how far the probe measurement has drifted from its
calibrated reference baseline. They describe the **environment** —
they are **not predictions of application circuit success or failure**.

| State | Condition | Meaning |
|-------|-----------|---------|
| `REFERENCE` | H and inv within baseline | Environment at calibrated reference |
| `DRIFTED` | H or inv moderately above baseline | Environment has shifted |
| `STRONGLY_DRIFTED` | H or inv substantially above baseline | Environment well outside reference |
| `PROBE_INVALID` | match=False | Probe cannot characterise this region — discard |

Only `PROBE_INVALID` warrants discarding.
`DRIFTED` and `STRONGLY_DRIFTED` describe drift — not failure.

**When all tiles show deviation:** the note
*"Absolute H drifts with calibration cycles. Relative ordering is the stable signal."*
appears automatically in the output. Route to rank #1 regardless.

```python
from kleinprobe import (classify_tile, classify_tiled,
    REFERENCE, DRIFTED, STRONGLY_DRIFTED, PROBE_INVALID)

r = classify_tile(match=True, H=4.11, inv=0.69, backend="ibm_fez",
                  region_label="central")
print(r.state)      # STRONGLY_DRIFTED
print(r.deviation)  # ▲▲▲ very high
print(r.rank)       # None — set by classify_tiled
```

**Backward compatibility:** old state names are kept as aliases:
`VALID_SPATIAL = REFERENCE`, `VALID_ANOMALOUS = DRIFTED`,
`OPTIMAL = REFERENCE`, `ELEVATED = DRIFTED`,
`CRITICAL = STRONGLY_DRIFTED`, `INVALID = PROBE_INVALID`.

---

## What it measures

| Metric | Meaning | Role |
|--------|---------|------|
| `H` | Shannon entropy of syndrome distribution | **Primary** — lower = quieter |
| `inv` | P(syndrome bit 0 = 1) | Secondary — anomaly detection |
| `Z` | Statistical significance vs flat baseline | Confidence |
| `environment_shift` | Normalised drift from baseline [0–1] | Display |
| `env_score` | 1 − environment_shift | Display convenience |

`H` and `inv` are largely independent (r ≈ 0 on Fez and Marrakesh).
Both carry independent information about the execution environment.

---

## Layout Match Score

When using the probe to inform routing for a specific circuit,
the Layout Match Score quantifies how representative the probe is:

```python
from kleinprobe import compute_lms, lms_label

probe_qubits = tile["qubits"]           # 18 qubits
app_qubits   = [0, 1, 2, 3, 4, 5]      # after transpilation

lms = compute_lms(probe_qubits, app_qubits)
print(lms, lms_label(lms))             # 1.0  DIRECT
```

| Circuit width | Typical LMS | Coverage |
|--------------|-------------|---------|
| 6–18 qubits  | 0.90–1.00 | Direct measurement |
| 20–40 qubits | 0.50–0.80 | High coverage |
| 40–80 qubits | 0.25–0.50 | Moderate |
| 80–156 qubits | 0.10–0.25 | Orientation only |

LMS depends on WHERE the circuit qubits land, not just how many.

---

## Known hardware baselines

| Backend | Era | H_ref ± σ | inv_ref ± σ | Sessions |
|---------|-----|-----------|-------------|---------|
| `ibm_fez` | Era 2 (post-Jul 5 2026) | 2.977 ± 0.215 | 0.889 ± 0.019 | 13 |
| `ibm_fez` | Era 1 (pre-Jul 5 2026) | 4.500 ± 0.150 | 0.900 ± 0.020 | archived |
| `ibm_marrakesh` | — | 3.128 ± 0.252 | 0.870 ± 0.026 | 17 |
| `ibm_kingston` | — | 2.694 ± 0.092 | 0.890 ± 0.010 | 14 |

Fez underwent a calibration regime change ~2026-07-05: H dropped
from ~4.50 to ~2.97. Era 1 baseline is archived. Detect regime
changes by monitoring single-tile H across sessions.

---

## Overhead

| Mode | PUBs | Time | Queue overhead |
|------|------|------|---------------|
| Single probe | 1 | ~3s | 0 |
| 3-tile spatial | 3 | ~4s | 0 |
| 8-tile full scan | 8 | ~8s | 0 |
| Full scan + app circuit | 16 | ~10s | 0 |

All modes run as co-execution PUBs — zero additional queue slots.

---

## Background

KleinProbe uses a Klein 3×2 stabilizer code (18 qubits, δ=0) as a
structured probe. The Klein bottle topology provides a non-trivial
syndrome structure with a known expected output (`100001`), enabling
calibration-independent anomaly detection and spatial H comparison.

**Formalism paper:**
[doi:10.5281/zenodo.21186259](https://doi.org/10.5281/zenodo.21186259)

**Dataset:**
[github.com/theoricline/kleinatlas-data](https://github.com/theoricline/kleinatlas-data)
(private — available on request)

---

MIT License
