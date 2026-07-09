# KleinProbe

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21186259.svg)](https://doi.org/10.5281/zenodo.21186259)

**Circuit-conditioned hardware observability layer for IBM Quantum**

```
backend.properties()
         ↓
   KleinAtlas.build()     discovers calibration-aware spatial tiles
         ↓
    KleinProbe ×3         tiled co-execution probe (~4 seconds)
         ↓
   ExecutionSnapshot      H, inv, validity per tile
         ↓
   classify_tiled()       VALID_SPATIAL / VALID_ANOMALOUS / INVALID
```

`backend.properties()` tells you qubit T2, T1, and readout error — measured in isolation.
KleinProbe tells you what actually happens when **your circuit runs on those qubits right now**.

---

## The gap

Static calibration metadata answers:
> *"What are the physical properties of each qubit under isolated characterisation?"*

KleinProbe answers:
> *"How does this hardware region behave when executing this circuit topology — including connectivity, simultaneous operations, and correlated multi-qubit effects?"*

These are different observables. The evidence:

- Two successive IBM calibration events produced **identical** T2/T1/RO tables while KleinProbe detected significant changes in syndrome entropy H and GHZ fidelity across all three spatial tiles.
- The chip region with the **best** T2 and lowest readout error consistently produced the **worst** circuit execution quality (the "patch_2 paradox" on ibm_fez).
- KleinProbe detected a readout bias anomaly (P(meas0|prep1)=0.097 on q29) that was not visible in `backend.properties()` at execution time.
- Pearson r(H_probe, 1−F_GHZ) = 0.725–0.999 across four independent runs on ibm_fez, and r=0.949 on ibm_kingston.

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

probe = KleinProbe(backend)
snap  = probe.run()
print(snap.report())
# H=2.92  inv=0.901  dom='100001' ✓  Z=287.3  S=1.0
```

**~3 seconds execution. Zero additional queue time (PUB mode).**

---

## KleinAtlas pipeline — tiled spatial probe

```python
from kleinprobe import KleinAtlas, classify_tiled, VALID_SPATIAL
from kleinprobe.circuit import build_probe_circuit
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime import SamplerV2 as Sampler

# 1. Build atlas from current calibration — no hardcoded patches
atlas = KleinAtlas(backend)
atlas.build()
print(atlas.report())
# KleinAtlas — ibm_fez
#   Calibration: 2026-07-08T16:03:08Z
#   Tiles: 3  (each 18 qubits)
#   Excluded: 0 qubits
#   Tile 0  central       T2=87.0µs  RO=0.0100
#   Tile 1  upper-left    T2=140.0µs RO=0.0082
#   Tile 2  middle        T2=85.0µs  RO=0.0120

# 2. Transpile probe circuit for each tile
probe_qc = build_probe_circuit()
pubs = []
for tile in atlas.tiles:
    pm = generate_preset_pass_manager(
        optimization_level=3, backend=backend,
        initial_layout=tile.initial_layout(probe_qc),
        seed_transpiler=77)
    pubs.append((pm.run(probe_qc),))

# 3. Run all tiles in one job (~4 seconds)
result = Sampler(backend).run(pubs, shots=4096).result()

# 4. Extract metrics and classify
tile_data = []
for i, tile in enumerate(atlas.tiles):
    counts = result[i].data.c.get_counts()
    # compute H, inv, match from counts...
    tile_data.append({
        'match': True, 'H': 3.78, 'inv': 0.820,
        'region_label': tile.region_label
    })

validity = classify_tiled(tile_data, backend=backend.name)
print(validity.execution_map(backend.name))
# KLEINATLAS EXECUTION MAP — ibm_fez
#   Tile   Region          Status            H      inv
#   0      central         ✓ GOOD       3.7800   0.8200
#   1      upper-left      ⚠ WARNING    4.7000   0.6150
#   2      middle          ✓ GOOD       4.3900   0.7800
#
#   Overall: VALID_ANOMALOUS
#   Anomaly: Class I — local
```

**~4 seconds. One job. Spatial snapshot of the chip's execution environment.**

---

## Validity model

KleinProbe measurements have three states:

| State | Condition | Meaning |
|-------|-----------|---------|
| `VALID_SPATIAL` | match=True, inv normal | Spatial ranking valid |
| `VALID_ANOMALOUS` | match=True, inv degraded | **Probe detected real hardware anomaly** |
| `INVALID` | match=False | Dominant syndrome changed — discard |

`VALID_ANOMALOUS` is not a probe failure. It is a **successful detection**.
Only `INVALID` (match=False) warrants discarding a measurement.

```python
from kleinprobe import classify_tile, VALID_SPATIAL, VALID_ANOMALOUS, INVALID

result = classify_tile(match=True, H=5.34, inv=0.61, backend='ibm_marrakesh')
print(result.state)          # VALID_ANOMALOUS
print(result.status_icon())  # ⚠ WARNING
print(result.reason)
# inv=0.610 below threshold 0.818. Antipodal edge qubit degraded.
# Probe detecting real hardware anomaly.
```

---

## Anomaly taxonomy

| Class | Scope | Example |
|-------|-------|---------|
| I — Local | Single tile | q91 T2=21µs collapse (Fez Jul8) |
| II — Regional | Adjacent tiles | Pending documentation |
| III — Chip-wide | All tiles simultaneously | Marrakesh Jul9 inv depression |
| IV — Semantic | match=False | Dominant syndrome changed |

Class IV is the only one that invalidates the probe.
Classes I-III are successful detections.

---

## Layout Match Score

Check how well the probe layout matches your circuit before running:

```python
from kleinprobe import layout_match_score

match = layout_match_score(probe_isa, target_isa)
print(match.report())
# Layout Match Score
#   Probe relevance:   0.833  (15/18 probe qubits shared)  [HIGH]
#   Target coverage:   0.300  (15/50 target qubits covered)

# With tiling — find which tile best matches your circuit
best = atlas.best_tile_for(target_isa)
```

---

## What it measures

| Metric | Formula | Meaning |
|--------|---------|---------|
| `H` | Shannon entropy of syndrome distribution | Global execution disorder — lower is quieter |
| `inv` | P(syndrome bit 0 = 1) | Antipodal edge integrity — sensitive to local qubit failures |
| `f` | P(dominant pattern) | Dominant pattern confidence |
| `Z_raw` | (f − P₀) / σ | Statistical significance vs flat baseline |
| `S` | clip(Z/Z₀, 0, 1) | Normalised signal score |

**H and inv measure different things:**
- H is a global execution quality signal — transfers to co-located circuits
- inv is a local antipodal qubit integrity signal — sensitive to single-qubit failures

---

## Known hardware baselines

| Backend | Era | H (mean ± σ) | inv (mean ± σ) | Sessions |
|---------|-----|-------------|----------------|---------|
| `ibm_fez` | Era 2 (post-Jul5) | 2.977 ± 0.177 | 0.889 ± 0.019 | 11 |
| `ibm_fez` | Era 1 (Papers 1-6) | 4.500 ± 0.150 | 0.900 ± 0.020 | 6 |
| `ibm_marrakesh` | — | 3.133 ± 0.269 | 0.855 ± 0.043 | 12 |
| `ibm_kingston` | — | 2.691 ± 0.097 | 0.890 ± 0.010 | 9 |

Fez underwent a calibration regime change ~2026-07-05: H dropped from ~4.50 to ~2.97 bits.
Era 1 baseline is archived. Era 2 is the current operational baseline.

---

## Overhead

| Mode | PUBs | Execution | Queue overhead |
|------|------|-----------|---------------|
| Single probe | 1 | ~3s | 0 |
| 3-tile atlas | 3 | ~4s | 0 |
| Atlas + user circuit | N+3 | ~4s | 0 |

Runs as PUBs in the same job as your experiment — zero additional queue time.

---

## Background

KleinProbe uses a Klein 3×2 stabilizer code circuit (18 qubits, δ=0) as a structured probe.
The Klein bottle topology provides a non-trivial syndrome structure with a known expected output
(`100001`), enabling calibration-independent anomaly detection.

Formalism paper: [doi:10.5281/zenodo.21186259](https://doi.org/10.5281/zenodo.21186259)

Dataset: [github.com/theoricline/kleinatlas-data](https://github.com/theoricline/kleinatlas-data) (private)

---

## License

MIT — see LICENSE.
