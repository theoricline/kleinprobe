"""
kleinprobe/circuit.py
=====================
Klein 3×2 probe circuit builder.

The 3×2 Klein bottle stabilizer code is used as a structured
probe circuit because:
  - Its syndrome output is analytically predictable (topology)
  - It is sensitive to the specific physical qubits it uses
  - It is lightweight: 18 qubits, depth ~88, runs in ~15 seconds
  - It has been validated across IBM Fez, Marrakesh, and Kingston

The circuit encodes boundary parameter δ ∈ {0,1,2} which selects
a distinct predicted syndrome pattern. Any deviation from the
predicted pattern measures how the hardware noise deforms the
topological structure.
"""

from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister

LX, LY  = 3, 2
N_DATA  = 2 * LX * LY   # 12 data qubits
N_SYN   = LX * LY       # 6 syndrome ancillas

# Predicted dominant syndrome patterns per δ (analytically derived)
PREDICTED = {
    0: '100001',   # fires syndromes {0, 5}
    1: '001001',   # fires syndromes {0, 3}
    2: '010001',   # fires syndromes {0, 4}
}


def _h(x, y):    return y * LX + (x % LX)
def _v(x, y):    return LX * LY + (y % LY) * LX + (x % LX)
def _vi(x, y):   return y * LX + (x % LX)


def klein_star(x, y, delta=0):
    """
    Star operator for Klein 3×2 code with boundary parameter δ.

    δ shifts the antipodal edge: v((Lx-1-x+δ)%Lx, Ly-1)
    This encodes a distinct syndrome fingerprint per δ value.
    """
    edges = [_h(x, y), _h(x - 1, y), _v(x, y)]
    if y == 0:
        edges.append(_v((LX - 1 - x + delta) % LX, LY - 1))
    else:
        edges.append(_v(x, y - 1))
    return list(set(edges))


def antipodal_edge(delta=0):
    """Return the data qubit index of the antipodal (b-anyon) edge for δ."""
    return _v((LX - 1 + delta) % LX, LY - 1)


def build_probe_circuit(delta=0):
    """
    Build the Klein 3×2 probe circuit for boundary parameter δ.

    The circuit prepares the b-anyon excitation (flips the antipodal
    edge) and measures the syndrome register. The predicted dominant
    pattern is PREDICTED[delta].

    Args:
        delta: boundary parameter ∈ {0, 1, 2}

    Returns:
        QuantumCircuit: 18-qubit circuit (12 data + 6 syndrome)
        predicted_pattern: expected dominant syndrome string
        flip_edge: data qubit index that was flipped
    """
    if delta not in PREDICTED:
        raise ValueError(f"δ must be in {list(PREDICTED.keys())}, got {delta}")

    flip_edge = antipodal_edge(delta)

    qr_d = QuantumRegister(N_DATA, 'd')
    qr_s = QuantumRegister(N_SYN,  's')
    cr   = ClassicalRegister(N_SYN, 'c')
    qc   = QuantumCircuit(qr_d, qr_s, cr)

    # b-anyon preparation: flip the antipodal edge
    qc.x(qr_d[flip_edge])
    qc.barrier()

    # Syndrome extraction
    for y in range(LY):
        for x in range(LX):
            anc = _vi(x, y)
            for e in klein_star(x, y, delta):
                qc.cx(qr_d[e], qr_s[anc])

    qc.measure(qr_s, cr)
    return qc, PREDICTED[delta], flip_edge


def validate_prediction(delta=0):
    """
    Analytically verify the predicted syndrome for δ.
    Returns (pattern, firing_syndromes).
    """
    flip_edge = antipodal_edge(delta)
    counts = {}
    for y in range(LY):
        for x in range(LX):
            if flip_edge in klein_star(x, y, delta):
                i = _vi(x, y)
                counts[i] = counts.get(i, 0) + 1
    firing = sorted(i for i, c in counts.items() if c % 2 == 1)
    pattern = ''.join('1' if i in firing else '0'
                      for i in reversed(range(N_SYN)))
    return pattern, firing
