#!/usr/bin/env python3
"""
Basic Package Modules Test (No Ground Truth Required)
====================================================

This test suite validates all core package functionality that does not require
Ground Truth files. It tests data loading, preprocessing, UMLS integration,
and basic analysis capabilities.

Usage:
    # Default run (uses sample_crawl_output/META)
    python test_basic_modules.py

    # Point at a specific META directory
    python test_basic_modules.py --meta-dir /path/to/META
"""

import os
import sys
import argparse
import pandas as pd
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

import genoar_analysis as ga
from genoar_analysis.io.umls_readers import UMLSCSVReader
from genoar_analysis.pipelines.first_pass_pipeline import FirstPassPipeline

# Import path configuration from conftest
from conftest import (
    get_meta_dir,
    get_output_dir,
    get_umls_dir,
    require_meta_files,
    require_umls_files,
    run_test_suite,
)

# Global path variables (set by main())
_META_DIR = None
_UMLS_DIR = None

def test_package_imports():
    """Test all package imports."""
    print("[PKG] Testing Package Imports")
    print("-" * 50)
    
    # Test main modules
    modules = ['io', 'pp', 'tl', 'pl', 'core']
    for module in modules:
        assert hasattr(ga, module), f"Missing module: {module}"
        print(f"[OK] {module}: {len(dir(getattr(ga, module)))} functions")
    
    # Test key classes
    assert hasattr(ga, 'GenoarData'), "Missing GenoarData class"
    assert hasattr(ga.pl, 'VennDiagramPlotter'), "Missing VennDiagramPlotter"
    print("[OK] All core classes available")
    
    print("[DONE] Package imports test PASSED\n")

def test_data_loading():
    """Test META file loading and basic data operations."""
    print("[INFO] Testing Data Loading")
    print("-" * 50)

    meta_dir = _META_DIR if _META_DIR else get_meta_dir()

    # The crawled META files are an optional fixture: they are gitignored, so a
    # fresh checkout has none. Say "skipped" and mean it - returning None here
    # still reads as PASSED in the summary below.
    #
    # "None" includes the empty directory that `make setup` creates and
    # workflow/run.sh points GENOAR_META_DIR at: checking only that the
    # directory exists sends this test into read_crawled_meta(), which raises
    # FileNotFoundError over data that has simply not been crawled yet. The one
    # predicate lives in conftest so it cannot drift between these scripts.
    require_meta_files(meta_dir)

    # Test META file loading
    adata = ga.read_crawled_meta(str(meta_dir))
    assert adata.meta is not None, "Failed to load META data"
    assert len(adata.meta) > 0, "No samples loaded"
    print(f"[OK] Loaded {len(adata.meta)} samples from META files")
    
    # Test data structure
    assert 'Series' in adata.meta.columns, "Missing Series column"
    assert adata.meta['Series'].nunique() > 0, "No series found"
    print(f"[OK] Found {adata.meta['Series'].nunique()} unique series")
    
    # Test basic info
    print(f"[OK] Data shape: {adata.meta.shape}")
    print(f"[OK] Available columns: {len(adata.meta.columns)}")
    
    print("[DONE] Data loading test PASSED\n")
    return adata

def test_umls_integration():
    """Test UMLS CSV reader functionality."""
    print("[SEARCH] Testing UMLS Integration")
    print("-" * 50)

    umls_dir = _UMLS_DIR if _UMLS_DIR else get_umls_dir()

    # An empty all_query_results/ is as absent as a missing one: the query below
    # reads umls_tissue_df.csv and its FileNotFoundError would be reported as a
    # failing test rather than a fixture nobody downloaded.
    require_umls_files(umls_dir)

    # Test UMLS reader initialization
    umls_reader = UMLSCSVReader(str(umls_dir))
    print("[OK] UMLS reader initialized")
    
    # Test data loading for each field
    fields = ['cell_type', 'tissue', 'disease']
    for field in fields:
        try:
            data = umls_reader.load_umls_data(field)
            assert data is not None, f"Failed to load {field} data"
            print(f"[OK] {field}: {len(data)} UMLS entries")
        except Exception as e:
            print(f"[WARN] {field}: {e}")
    
    # Test query functionality
    test_terms = ['blood', 'brain', 'liver']
    results = umls_reader.query_terms(test_terms, 'tissue')
    assert len(results) > 0, "No UMLS matches found"
    print(f"[OK] Query test: {len(results)} matches for {test_terms}")
    
    print("[DONE] UMLS integration test PASSED\n")
    return umls_reader

def test_preprocessing():
    """Test data preprocessing functionality."""
    print("[STEP] Testing Preprocessing")
    print("-" * 50)
    
    # Create test data
    test_data = pd.DataFrame({
        'sample_id': ['S1', 'S2', 'S3'],
        'Series': ['GSE001', 'GSE001', 'GSE002'],
        'cell_type': ['T cell', None, 'B cell'],
        'tissue': ['blood', 'liver', 'spleen'],
        'sex': ['male', 'f', 'female'],
        'Organism': ['Homo sapiens', 'Homo sapiens', 'Mus musculus'],
        'LibrarySource': ['TRANSCRIPTOMIC', 'GENOMIC', 'TRANSCRIPTOMIC']
    })
    
    # Create GenoarData object for testing
    adata = ga.GenoarData(test_data)
    
    # Test organism filtering
    original_count = len(adata.meta)
    ga.pp.filter_organism(adata, organism="Homo sapiens")
    assert len(adata.meta) < original_count, "Organism filtering failed"
    print(f"[OK] Organism filtering: {original_count} → {len(adata.meta)} samples")
    
    # Test library source filtering
    adata = ga.GenoarData(test_data)  # Reset data
    ga.pp.filter_library_source(adata, source="TRANSCRIPTOMIC")
    filtered_count = len(adata.meta)
    print(f"[OK] Library source filtering: {original_count} → {filtered_count} samples")
    
    # Test missing value dropping
    adata = ga.GenoarData(test_data)  # Reset data
    ga.pp.drop_missing(adata, columns=['cell_type'])
    non_null_count = len(adata.meta)
    print(f"[OK] Drop missing values: {original_count} → {non_null_count} samples")
    
    # Test standard filtering pipeline
    adata = ga.GenoarData(test_data)  # Reset data
    ga.pp.apply_standard_filters(adata, required_fields=['tissue'])
    final_count = len(adata.meta)
    print(f"[OK] Standard filter pipeline: {original_count} → {final_count} samples")
    
    print("[DONE] Preprocessing test PASSED\n")

def test_analysis_functions():
    """Test preserved analysis functionality."""
    print("[STAT] Testing Analysis Functions")
    print("-" * 50)
    
    # Create test data for analysis
    test_data = pd.DataFrame({
        'Series': ['GSE1', 'GSE2', 'GSE3', 'GSE4'],
        'cell_type': ['T cell', 'B cell', 'NK cell', 'T cell'],
        'tissue': ['blood', 'blood', 'spleen', 'liver']
    })
    
    # Create GenoarData object
    adata = ga.GenoarData(test_data)
    
    # Test field value analysis
    field_analysis = ga.tl.analyze_field_values(adata, 'cell_type')
    assert isinstance(field_analysis, dict), "Field analysis should return dict"
    assert 'unique_values' in field_analysis, "Should contain unique_values"
    print(f"[OK] Field analysis: {len(field_analysis['unique_values'])} unique cell types")
    
    # Test field comparison
    field_comparison = ga.tl.compare_field_overlap(adata, 'cell_type', 'tissue')
    assert isinstance(field_comparison, dict), "Field comparison should return dict"
    assert 'jaccard_index' in field_comparison, "Should contain jaccard_index"
    print(f"[OK] Field comparison: Jaccard index = {field_comparison['jaccard_index']:.3f}")
    
    # Test series retrieval methods
    cell_type_series = adata.get_series_with_field('cell_type')
    assert isinstance(cell_type_series, set), "Should return set of series"
    print(f"[OK] Series with cell_type: {len(cell_type_series)} series")
    
    print("[DONE] Analysis functions test PASSED\n")

def test_plotting():
    """Test plotting functionality."""
    print("[INFO] Testing Plotting Functions")
    print("-" * 50)
    
    # Test VennDiagramPlotter initialization
    plotter = ga.pl.VennDiagramPlotter()
    print("[OK] VennDiagramPlotter initialized")
    
    # Create test data for plotting
    test_data = pd.DataFrame({
        'Series': ['GSE1', 'GSE2', 'GSE3', 'GSE4'],
        'cell_type': ['T cell', 'B cell', 'NK cell', 'T cell'],
        'tissue': ['blood', 'blood', 'spleen', 'liver']
    })
    adata = ga.GenoarData(test_data)
    
    # Test plotting functions exist and are callable
    assert callable(ga.pl.plot_field_comparison), "plot_field_comparison not callable"
    assert callable(ga.pl.plot_multiple_fields), "plot_multiple_fields not callable"
    print("[OK] All plotting functions available")
    
    # Test that VennDiagramPlotter methods work (without actually plotting).
    # No try/except here: this is plain set arithmetic on data created three
    # lines up, so anything it raises is a real defect and has to surface.
    set1 = adata.get_series_with_field('cell_type')
    set2 = adata.get_series_with_field('tissue')
    assert isinstance(set1, set) and isinstance(set2, set)
    print(f"[OK] Field sets ready for comparison: {len(set1)} vs {len(set2)} series")

    print("[DONE] Plotting test PASSED\n")

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='GENOAR Analysis Package - Basic Modules Test',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Default run (uses sample_crawl_output/META)
    python test_basic_modules.py

    # Point at a specific META directory
    python test_basic_modules.py --meta-dir ~/genoar/crawl_output/META

Note:
    UMLS data is always loaded from all_query_results/ (fixed path).
        """
    )

    parser.add_argument(
        '-m', '--meta-dir',
        type=str,
        default=None,
        help='Path to the directory of META files (default: sample_crawl_output/META)'
    )

    return parser.parse_args()


def main():
    """Run all basic module tests."""
    global _META_DIR, _UMLS_DIR

    args = parse_args()

    # Set global paths
    _META_DIR = Path(args.meta_dir) if args.meta_dir else get_meta_dir()
    _UMLS_DIR = get_umls_dir()  # Always fixed

    print("[TEST] GENOAR Analysis Package - Basic Modules Test")
    print("=" * 60)
    print(f"[DIR] META directory: {_META_DIR}")
    print(f"[DIR] UMLS directory: {_UMLS_DIR} (fixed)")
    print("=" * 60)
    print("Testing core functionality without Ground Truth files\n")

    # The summary is built from what the tests actually did. A fixed block of
    # "PASSED" lines printed after the last test returns claims a pass for
    # tests that skipped themselves.
    status = run_test_suite("BASIC MODULES TEST SUMMARY", [
        ("Package imports", test_package_imports),
        ("Data loading", test_data_loading),
        ("UMLS integration", test_umls_integration),
        ("Preprocessing", test_preprocessing),
        ("Analysis functions", test_analysis_functions),
        ("Plotting functions", test_plotting),
    ])

    if status == 0:
        print("No failures. Whatever the summary lists as SKIPPED was not tested.")
    else:
        print("The package is NOT ready: see the failing tests above.")

    sys.exit(status)


if __name__ == "__main__":
    main()