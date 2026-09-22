"""
GENOAR Analysis Package
=======================

A comprehensive package for analyzing GEO (Gene Expression Omnibus) crawled data
with UMLS matching and statistical analysis.

Main modules:
- io: Data input/output operations
- preprocessing: Data filtering and cleaning
- analysis: Core analysis tools (matching, stats, UMLS)
- plotting: Visualization functions
- core: Core data structures and configuration

Usage:
    import genoar_analysis as ga
    
    # Load data
    adata = ga.io.read_crawled_meta('crawl_output/META')
    umls_data = ga.io.read_umls_results('all_query_results')
    
    # Analyze
    ga.analysis.analyze_field_values(adata, field='cell_type')
    
    # Visualize
    ga.pl.plot_field_comparison(adata, 'cell_type', 'tissue')
"""

from . import io
from . import preprocessing as pp
from . import analysis as tl
from . import plotting as pl
from . import core

__version__ = "0.1.0"
__author__ = "GENOAR Team"

# Import key functions for convenience
from .core.data import GenoarData
from .io.readers import read_crawled_meta, read_umls_results

__all__ = [
    'io', 'pp', 'tl', 'pl', 'core',
    'GenoarData', 'read_crawled_meta', 'read_umls_results'
]