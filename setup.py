from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name         = "kleinprobe",
    version      = "0.5.2",
    author       = "Leonardo Roma",
    author_email = "leonardo.roma@protonmail.com",
    description  = "Spatial execution environment sensor for IBM Quantum",
    long_description      = long_description,
    long_description_content_type = "text/markdown",
    url          = "https://github.com/theoricline/kleinprobe",
    packages     = find_packages(),
    python_requires = ">=3.9",
    install_requires = [
        "qiskit>=2.0",
        "qiskit-ibm-runtime>=0.20",
        "numpy>=1.24",
    ],
    extras_require = {
        "dev": ["pytest", "black", "ruff"],
    },
    classifiers = [
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Scientific/Engineering :: Physics",
        "Topic :: System :: Monitoring",
    ],
    keywords = [
        "quantum computing", "IBM Quantum", "Qiskit",
        "hardware observability", "calibration drift",
        "quantum error correction", "Klein bottle",
        "noise characterisation", "circuit-aware",
    ],
)
