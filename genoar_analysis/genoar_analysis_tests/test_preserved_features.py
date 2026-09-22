#!/usr/bin/env python3
"""
Preserved Features Comprehensive Test
====================================

This test suite validates all preserved functionality after Ground Truth removal.
It tests UMLS matching, field analysis, visualization, and data manipulation
that work without Ground Truth files.

Usage:
    # Default run (uses sample_crawl_output/META)
    python test_preserved_features.py

    # Point at a specific META directory
    python test_preserved_features.py --meta-dir /path/to/META
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Set

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

import genoar_analysis as ga
from genoar_analysis.io.umls_readers import UMLSCSVReader
from genoar_analysis.plotting.venn import VennDiagramPlotter

# Import path configuration from conftest
from conftest import (
    get_meta_dir,
    get_output_dir,
    get_umls_dir,
    require_meta_dir,
    require_umls_files,
    run_test_suite,
)

# Global path variables (set by main())
_META_DIR = None
_UMLS_DIR = None

def test_genoardata_core_functionality():
    """Test GenoarData core functionality."""
    print("[TEST] Testing GenoarData Core Functionality")
    print("-" * 50)
    
    # Create test data
    test_meta = pd.DataFrame({
        'Series': ['GSE001', 'GSE002', 'GSE003', 'GSE004'],
        'Run': ['SRR001', 'SRR002', 'SRR003', 'SRR004'],
        'cell_type': ['T cell', 'B cell', None, 'T cell'],
        'tissue': ['blood', 'spleen', 'liver', 'blood'],
        'disease_state': ['healthy', 'cancer', None, 'healthy']
    })
    
    # Test GenoarData initialization
    adata = ga.GenoarData(test_meta)
    assert adata.meta is not None, "META data should be loaded"
    assert len(adata.meta) == 4, "Should have 4 samples"
    print("[OK] GenoarData initialization working")
    
    # Test series info initialization
    assert 'total_series' in adata.series_info, "Should have series info"
    assert adata.series_info['total_series'] == 4, "Should count 4 series"
    print(f"[OK] Series info: {adata.series_info['total_series']} series")
    
    # Test get_unique_values
    cell_types = adata.get_unique_values('cell_type')
    assert 'T cell' in cell_types, "Should find T cell"
    assert 'B cell' in cell_types, "Should find B cell"
    print(f"[OK] Unique values: {len(cell_types)} cell types")
    
    # Test get_series_with_field
    cell_type_series = adata.get_series_with_field('cell_type')
    assert isinstance(cell_type_series, set), "Should return set"
    assert 'GSE001' in cell_type_series, "Should include GSE001"
    print(f"[OK] Series with cell_type: {len(cell_type_series)} series")
    
    # Test filter_by_series
    filtered_adata = adata.filter_by_series(['GSE001', 'GSE002'])
    assert len(filtered_adata.meta) == 2, "Should have 2 filtered samples"
    print("[OK] Filter by series working")
    
    # Test summary
    summary = adata.summary()
    assert summary['meta_loaded'], "META should be loaded"
    assert not summary['umls_loaded'], "UMLS should not be loaded"
    print("[OK] Summary generation working")
    
    # Test string representation
    repr_str = str(adata)
    assert 'GenoarData object' in repr_str, "Should contain class name"
    print("[OK] String representation working")
    
    print("[DONE] GenoarData core functionality test PASSED\n")
    return adata

def test_field_analysis():
    """Test field analysis functionality."""
    print("[INFO] Testing Field Analysis")
    print("-" * 50)
    
    # Create diverse test data
    test_data = pd.DataFrame({
        'Series': [f'GSE{i:03d}' for i in range(1, 11)],
        'cell_type': ['T cell', 'B cell', 'NK cell', 'T cell', 'Monocyte',
                     'T cell', 'B cell', None, 'T cell', 'Plasma cell'],
        'tissue': ['blood', 'spleen', 'liver', 'blood', 'bone marrow',
                  'lymph node', 'spleen', 'blood', 'thymus', 'spleen'],
        'disease_state': ['healthy', 'cancer', 'inflammation', 'healthy', None,
                         'autoimmune', 'cancer', 'healthy', None, 'cancer']
    })
    
    adata = ga.GenoarData(test_data)
    
    # Test analyze_field_values
    cell_type_analysis = ga.tl.analyze_field_values(adata, 'cell_type')
    assert isinstance(cell_type_analysis, dict), "Should return dict"
    assert 'unique_values' in cell_type_analysis, "Should contain unique values"
    assert 'total_samples' in cell_type_analysis, "Should contain sample count"
    assert 'value_counts' in cell_type_analysis, "Should contain value counts"
    print(f"[OK] Cell type analysis: {cell_type_analysis['unique_count']} unique types")
    
    # Test field comparison
    comparison = ga.tl.compare_field_overlap(adata, 'cell_type', 'tissue')
    assert isinstance(comparison, dict), "Should return dict"
    assert 'jaccard_index' in comparison, "Should contain Jaccard index"
    assert 'intersection_count' in comparison, "Should contain intersection count"
    print(f"[OK] Field comparison: Jaccard = {comparison['jaccard_index']:.3f}")
    
    # Test with case sensitivity
    case_analysis = ga.tl.analyze_field_values(adata, 'cell_type', case_sensitive=True)
    assert case_analysis['case_sensitive'], "Should be case sensitive"
    print("[OK] Case-sensitive analysis working")
    
    # Verify results are stored in adata
    assert 'cell_type' in adata.matching_results, "Results should be stored"
    print("[OK] Analysis results storage working")
    
    print("[DONE] Field analysis test PASSED\n")
    return adata

def test_umls_integration():
    """Test UMLS integration functionality."""
    print("[SEARCH] Testing UMLS Integration")
    print("-" * 50)

    umls_dir = _UMLS_DIR if _UMLS_DIR else get_umls_dir()

    # Absent UMLS export: skip and say so. Building a mock DataFrame, never
    # looking at it, and reporting the test as COMPLETED tests nothing while
    # reading as if it had. An empty all_query_results/ counts as absent:
    # the assertion below would otherwise fail over a fixture nobody downloaded.
    require_umls_files(umls_dir)

    # Test UMLS reader initialization
    umls_reader = UMLSCSVReader(str(umls_dir))
    print("[OK] UMLS reader initialized")

    # Test loading UMLS data for each field. A single missing field file is
    # tolerated - the assertion below is what decides - but the reader raising
    # for every field is a failure, not a warning.
    fields = ['cell_type', 'tissue', 'disease']
    successful_loads = 0

    for field in fields:
        try:
            data = umls_reader.load_umls_data(field)
        except Exception as e:  # noqa: BLE001 - per-field tolerance, see assert
            print(f"[WARN] {field}: {e}")
            continue
        if data is not None and len(data) > 0:
            print(f"[OK] {field}: {len(data)} UMLS entries loaded")
            successful_loads += 1
        else:
            print(f"[WARN] {field}: Empty or no data")

    assert successful_loads > 0, "At least one UMLS file should load"

    # Test query functionality. The UMLS export is present at this point, so a
    # query that raises is a defect; only an empty result set is a warning.
    test_terms = ['blood', 'T cell', 'cancer']
    results = umls_reader.query_terms(test_terms, 'tissue')
    if len(results) > 0:
        print(f"[OK] Query test: {len(results)} matches found")
    else:
        print("[WARN] Query test: No matches found")

    # Test SAB priority selection
    if len(results) > 0:
        priority_results = umls_reader.get_sab_priority_selection(results, 'tissue')
        print(f"[OK] SAB priority selection: {len(priority_results)} prioritized results")

    print("[DONE] UMLS integration test PASSED\n")

def test_preprocessing_pipeline():
    """Test preprocessing pipeline functionality."""
    print("[STEP] Testing Preprocessing Pipeline")
    print("-" * 50)
    
    # Create comprehensive test data
    test_data = pd.DataFrame({
        'Series': ['GSE001', 'GSE002', 'GSE003', 'GSE004', 'GSE005'],
        'Run': ['SRR001', 'SRR002', 'SRR003', 'SRR004', 'SRR005'],
        'cell_type': ['T cell', 'B cell', None, 'NK cell', 'T cell'],
        'tissue': ['blood', None, 'liver', 'spleen', 'blood'],
        'Organism': ['Homo sapiens', 'Homo sapiens', 'Mus musculus', 'Homo sapiens', 'Homo sapiens'],
        'LibrarySource': ['TRANSCRIPTOMIC', 'GENOMIC', 'TRANSCRIPTOMIC', 'TRANSCRIPTOMIC', 'TRANSCRIPTOMIC']
    })
    
    # Test each filtering function
    adata = ga.GenoarData(test_data)
    
    # Test organism filtering
    original_count = len(adata.meta)
    ga.pp.filter_organism(adata, organism="Homo sapiens")
    human_count = len(adata.meta)
    assert human_count < original_count, "Organism filtering should reduce samples"
    print(f"[OK] Organism filter: {original_count} → {human_count} samples")
    
    # Reset for library source test
    adata = ga.GenoarData(test_data)
    ga.pp.filter_library_source(adata, source="TRANSCRIPTOMIC")
    transcriptomic_count = len(adata.meta)
    print(f"[OK] Library source filter: {original_count} → {transcriptomic_count} samples")
    
    # Test missing value handling
    adata = ga.GenoarData(test_data)
    ga.pp.drop_missing(adata, columns=['cell_type', 'tissue'], how='any')
    complete_count = len(adata.meta)
    print(f"[OK] Drop missing values: {original_count} → {complete_count} samples")
    
    # Test standard filtering pipeline
    adata = ga.GenoarData(test_data)
    ga.pp.apply_standard_filters(adata, required_fields=['cell_type'])
    filtered_count = len(adata.meta)
    print(f"[OK] Standard filters: {original_count} → {filtered_count} samples")
    
    # Test inplace vs copy behavior
    adata_original = ga.GenoarData(test_data)
    adata_copy = ga.pp.filter_organism(adata_original, inplace=False)
    assert len(adata_original.meta) == original_count, "Original should be unchanged"
    assert len(adata_copy.meta) != original_count, "Copy should be changed"
    print("[OK] Inplace vs copy behavior working")
    
    print("[DONE] Preprocessing pipeline test PASSED\n")

def test_visualization_functionality():
    """Test visualization functionality."""
    print("[INFO] Testing Visualization Functionality")
    print("-" * 50)
    
    # Create test data with overlapping fields
    test_data = pd.DataFrame({
        'Series': [f'GSE{i:03d}' for i in range(1, 11)],
        'cell_type': ['T cell', 'B cell', 'NK cell', 'T cell', 'Monocyte',
                     'T cell', 'B cell', 'NK cell', 'T cell', 'Plasma cell'],
        'tissue': ['blood', 'spleen', 'liver', 'blood', 'bone marrow',
                  'blood', 'spleen', 'liver', 'blood', 'spleen']
    })
    
    adata = ga.GenoarData(test_data)
    
    # Test VennDiagramPlotter initialization
    plotter = VennDiagramPlotter()
    assert plotter is not None, "Plotter should initialize"
    print("[OK] VennDiagramPlotter initialized")
    
    # Test series overlap preparation (without actual plotting)
    cell_type_series = adata.get_series_with_field('cell_type')
    tissue_series = adata.get_series_with_field('tissue')
    
    # Calculate overlap statistics
    intersection = cell_type_series.intersection(tissue_series)
    union = cell_type_series.union(tissue_series)
    jaccard = len(intersection) / len(union) if union else 0
    
    print(f"[OK] Field overlap calculation:")
    print(f"   Cell type series: {len(cell_type_series)}")
    print(f"   Tissue series: {len(tissue_series)}")
    print(f"   Intersection: {len(intersection)}")
    print(f"   Jaccard index: {jaccard:.3f}")
    
    # Test field comparison function (analysis part). This would normally create
    # a plot; we test the analysis part. Nothing here is allowed to fail
    # quietly - it is set arithmetic over data built in this function.
    field1_series = adata.get_series_with_field('cell_type')
    field2_series = adata.get_series_with_field('tissue')

    comparison_stats = {
        'field1_count': len(field1_series),
        'field2_count': len(field2_series),
        'intersection_count': len(field1_series.intersection(field2_series)),
        'union_count': len(field1_series.union(field2_series))
    }

    print(f"[OK] Field comparison analysis ready: {comparison_stats}")

    # Test multiple fields analysis
    fields = ['cell_type', 'tissue']
    field_sets = []
    
    for field in fields:
        series_set = adata.get_series_with_field(field)
        field_sets.append(series_set)
        print(f"[OK] {field} series set: {len(series_set)} series")
    
    print("[DONE] Visualization functionality test PASSED\n")

def test_data_io_operations():
    """Test data input/output operations."""
    print("[IO] Testing Data I/O Operations")
    print("-" * 50)
    
    # Create test data
    test_data = pd.DataFrame({
        'Series': ['GSE001', 'GSE002', 'GSE003'],
        'Run': ['SRR001', 'SRR002', 'SRR003'],
        'cell_type': ['T cell', 'B cell', 'NK cell'],
        'tissue': ['blood', 'spleen', 'liver']
    })
    
    adata = ga.GenoarData(test_data)
    
    # Add some analysis results
    ga.tl.analyze_field_values(adata, 'cell_type')
    adata.log_analysis("Test analysis step")
    
    # Test summary generation
    summary = adata.summary()
    assert 'meta_loaded' in summary, "Summary should contain meta status"
    assert 'analysis_steps' in summary, "Summary should contain analysis steps"
    print("[OK] Summary generation working")
    
    # Test analysis log
    assert len(adata.analysis_log) > 0, "Analysis log should have entries"
    print(f"[OK] Analysis log: {len(adata.analysis_log)} entries")
    
    # Test matching results storage
    assert 'cell_type' in adata.matching_results, "Results should be stored"
    print("[OK] Matching results storage working")
    
    # Test series info updates
    adata._initialize_series_info()
    assert 'total_series' in adata.series_info, "Series info should be updated"
    print("[OK] Series info updates working")
    
    print("[DONE] Data I/O operations test PASSED\n")

def test_first_pass_pipeline():
    """Test first-pass pipeline functionality (without Ground Truth)."""
    print("[PROC] Testing First-Pass Pipeline")
    print("-" * 50)

    meta_dir = _META_DIR if _META_DIR else get_meta_dir()
    umls_dir = _UMLS_DIR if _UMLS_DIR else get_umls_dir()

    # This test hands the directories to FirstPassPipeline and checks what the
    # class exposes; it reads neither, so the directory being there is all it
    # needs - require_meta_files() would skip it for want of data it never
    # touches. The predicate still comes from conftest, so "what counts as
    # absent" is written down once for every script here.
    require_meta_dir(meta_dir)

    # Test pipeline initialization (without ground truth). An import error or a
    # missing method here means the package is broken, not a "warning" after
    # which the test still calls itself COMPLETED.
    from genoar_analysis.pipelines.first_pass_pipeline import FirstPassPipeline
    pipeline = FirstPassPipeline(str(meta_dir), str(umls_dir))
    print("[OK] Pipeline initialized (without Ground Truth)")

    # Test data loading method exists
    assert hasattr(pipeline, 'load_and_preprocess_data'), "Should have load method"
    assert hasattr(pipeline, 'create_first_pass_table'), "Should have create method"
    print("[OK] Pipeline methods available")

    # Test convenience function (without ground truth parameter)
    from genoar_analysis.pipelines.first_pass_pipeline import create_first_pass_tables  # noqa: F401
    print("[OK] Convenience function import successful")

    print("[DONE] First-pass pipeline test PASSED\n")

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='GENOAR Analysis Package - Preserved Features Test',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Default run (uses sample_crawl_output/META)
    python test_preserved_features.py

    # Point at a specific META directory
    python test_preserved_features.py --meta-dir ~/genoar/crawl_output/META

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
    """Run all preserved feature tests."""
    global _META_DIR, _UMLS_DIR

    args = parse_args()

    # Set global paths
    _META_DIR = Path(args.meta_dir) if args.meta_dir else get_meta_dir()
    _UMLS_DIR = get_umls_dir()  # Always fixed

    print("[TEST] GENOAR Analysis Package - Preserved Features Test")
    print("=" * 70)
    print(f"[DIR] META directory: {_META_DIR}")
    print(f"[DIR] UMLS directory: {_UMLS_DIR} (fixed)")
    print("=" * 70)
    print("Testing all functionality that works without Ground Truth files\n")

    # As in test_basic_modules: the summary reports what happened, rather than
    # a fixed list of PASSED/COMPLETED lines printed regardless of outcome.
    status = run_test_suite("PRESERVED FEATURES TEST SUMMARY", [
        ("GenoarData core functionality", test_genoardata_core_functionality),
        ("Field analysis", test_field_analysis),
        ("UMLS integration", test_umls_integration),
        ("Preprocessing pipeline", test_preprocessing_pipeline),
        ("Visualization functionality", test_visualization_functionality),
        ("Data I/O operations", test_data_io_operations),
        ("First-pass pipeline", test_first_pass_pipeline),
    ])

    if status == 0:
        print("\n[LIST] Preserved features this suite covers "
              "(the summary above says which of them actually ran):")
        print("• UMLS matching and analysis")
        print("• Field-based statistical analysis")
        print("• Data filtering and preprocessing")
        print("• Venn diagram generation (field comparisons)")
        print("• First-pass pipeline (UMLS-matched tables)")
        print("• Data import/export operations")
    else:
        print("\nPreserved features are NOT all intact: see the failing tests above.")

    sys.exit(status)


if __name__ == "__main__":
    main()