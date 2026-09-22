"""Setup script for GENOAR Analysis Package."""

from pathlib import Path

from setuptools import find_packages, setup


def get_version() -> str:
    """Extract version from genoar_analysis/__init__.py."""
    init_file = Path(__file__).parent / "genoar_analysis" / "__init__.py"
    if init_file.exists():
        for line in init_file.read_text().splitlines():
            if line.startswith("__version__"):
                return line.split('"')[1]
    return "0.1.0"


setup(
    name="genoar-analysis",
    license="MIT",
    version=get_version(),
    # Only the analysis library is installable. cycle_test/ is the end-to-end
    # test harness, run from a checkout, and find_packages() otherwise ships it
    # and its fixtures as part of the distribution.
    packages=find_packages(include=["genoar_analysis", "genoar_analysis.*"]),
    python_requires=">=3.8",
    install_requires=[
        # Core data manipulation
        "pandas>=1.3.0",
        "numpy>=1.21.0",
        # Statistical analysis
        "scipy>=1.7.0",
        # Visualization
        "matplotlib>=3.5.0",
        "matplotlib-venn>=0.11.6",
        # Config parsing (cycle_test stage3 runner generates config.yaml)
        "pyyaml>=5.4.0",
    ],
    extras_require={
        "dev": [
            "pytest>=6.0.0",
            "pytest-cov>=2.10.0",
            "black>=21.0.0",
            "flake8>=3.8.0",
            "mypy>=0.910",
            "sphinx>=4.0.0",
            "sphinx-rtd-theme>=1.0.0",
        ],
        # No "plotting" or "jupyter" extra: nothing in this package imports
        # seaborn, plotly, bokeh or the notebook stack, so installing them
        # under an extra would promise a capability that is not here. The
        # plotting this package does have is matplotlib and matplotlib-venn,
        # and those are required, not optional.
    },
    zip_safe=False,
)
