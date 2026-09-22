#!/usr/bin/env python3
"""
First-Pass Workflow Complete Test
================================

This test suite validates the complete first-pass table generation workflow
following the exact process flow:

META files (GSE*.txt)
     ↓
1. Load and merge
     ↓
2. Filter (Homo sapiens, TRANSCRIPTOMIC)
     ↓
3. Consolidate fields (field_consolidation.py)
   - build disease_state_modified
   - merge the sex fields
     ↓
4. UMLS matching (umls_readers.py)
   - CSV-backed lookup
   - apply SAB priority order
     ↓
5. Keep only rows that got a CUI
     ↓
6. Write CSV output
   - HS_cell_type_1st_pass_meta_table.csv
   - HS_tissue_1st_pass_meta_table.csv
   - HS_disease_1st_pass_meta_table.csv

Usage:
    # Default run (uses sample_crawl_output/META)
    python test_first_pass_workflow.py

    # Point at a specific META directory
    python test_first_pass_workflow.py --meta-dir /path/to/META

    # Point at a specific META directory and output directory
    python test_first_pass_workflow.py --meta-dir /path/to/META --output-dir /path/to/output
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

import genoar_analysis as ga
from genoar_analysis.io.umls_readers import UMLSCSVReader
from genoar_analysis.pipelines.first_pass_pipeline import FirstPassPipeline
from genoar_analysis.core.config import Config

# Import path configuration from conftest
from conftest import (
    FixtureUnavailable,
    get_meta_dir,
    get_output_dir,
    get_umls_dir,
    require_meta_files,
    require_umls_files,
)


class FirstPassWorkflowTester:
    """Complete first-pass workflow tester."""

    def __init__(self, meta_dir: Path = None, umls_dir: Path = None, output_dir: Path = None):
        """
        Initialize workflow tester with configurable paths.

        Args:
            meta_dir: META files directory (default: from environment or relative path)
            umls_dir: UMLS data directory (default: from environment or relative path)
            output_dir: Output directory (default: from environment or relative path)
        """
        self.meta_dir = str(meta_dir or get_meta_dir())
        self.umls_dir = str(umls_dir or get_umls_dir())
        self.output_dir = str(output_dir or get_output_dir())

        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

        # Validate input directories exist
        if not Path(self.meta_dir).exists():
            print(f"[WARN] META directory not found: {self.meta_dir}")
            print("   Set GENOAR_META_DIR environment variable or ensure directory exists")
        if not Path(self.umls_dir).exists():
            print(f"[WARN] UMLS directory not found: {self.umls_dir}")
            print("   Set GENOAR_UMLS_DIR environment variable or ensure directory exists")

        self.results = {}
    
    def step_1_data_loading(self) -> pd.DataFrame:
        """Step 1: load and merge the META files."""
        print("[INFO] Step 1: Data Loading and Merging")
        print("-" * 50)
        
        # Find META files
        meta_files = list(Path(self.meta_dir).glob("*_meta.txt"))
        print(f"[SEARCH] Found {len(meta_files)} META files")
        
        # Load using pipeline method
        pipeline = FirstPassPipeline(self.meta_dir, self.umls_dir)
        combined_df = pipeline.load_and_preprocess_data()
        
        print(f"[OK] Combined data shape: {combined_df.shape}")
        print(f"[OK] Series count: {combined_df['Series'].nunique()}")
        print(f"[OK] Sample count: {len(combined_df)}")
        
        # Show available columns
        important_cols = ['tissue', 'cell_type', 'disease_state', 'disease', 'Organism', 'LibrarySource']
        available_important = [col for col in important_cols if col in combined_df.columns]
        print(f"[OK] Important columns available: {available_important}")
        
        print("[DONE] Step 1 COMPLETED\n")
        return combined_df
    
    def step_2_filtering(self, df: pd.DataFrame) -> pd.DataFrame:
        """Step 2: filter to Homo sapiens / TRANSCRIPTOMIC."""
        print("[STEP] Step 2: Filtering (Homo sapiens, TRANSCRIPTOMIC)")
        print("-" * 50)
        
        original_count = len(df)
        print(f"[INFO] Original sample count: {original_count}")
        
        # Filter by Organism
        if 'Organism' in df.columns:
            before_organism = len(df)
            df = df[df['Organism'] == 'Homo sapiens']
            after_organism = len(df)
            print(f"[OK] Organism filter: {before_organism} → {after_organism} samples")
        else:
            print("[WARN] No Organism column found - skipping organism filter")
        
        # Filter by LibrarySource
        if 'LibrarySource' in df.columns:
            before_library = len(df)
            df = df[df['LibrarySource'].str.contains('TRANSCRIPTOMIC', na=False)]
            after_library = len(df)
            print(f"[OK] LibrarySource filter: {before_library} → {after_library} samples")
        else:
            print("[WARN] No LibrarySource column found - skipping library filter")
        
        final_count = len(df)
        reduction_pct = (original_count - final_count) / original_count * 100
        print(f"[INFO] Final sample count: {final_count} ({reduction_pct:.1f}% reduction)")
        
        print("[DONE] Step 2 COMPLETED\n")
        return df
    
    def step_3_field_consolidation(self, df: pd.DataFrame) -> pd.DataFrame:
        """Step 3: consolidate fields (via the pipeline method)."""
        print("[STEP] Step 3: Field Consolidation")
        print("-" * 50)
        
        # Use the pipeline's consolidate_fields method
        pipeline = FirstPassPipeline(self.meta_dir, self.umls_dir)
        df = pipeline.consolidate_fields(df)
        
        # Check for disease_state_modified creation
        if 'disease_state_modified' in df.columns:
            disease_count = df['disease_state_modified'].notna().sum()
            print(f"[OK] disease_state_modified: {disease_count} non-null values")
        
        # Check sex field consolidation
        if 'sex' in df.columns:
            sex_count = df['sex'].notna().sum()
            print(f"[OK] sex field: {sex_count} non-null values")
        
        # Show available fields
        important_fields = ['tissue', 'cell_type', 'disease_state_modified', 'sex']
        available_fields = [f for f in important_fields if f in df.columns]
        print(f"[OK] Available analysis fields: {available_fields}")
        
        print("[DONE] Step 3 COMPLETED\n")
        return df
    
    def step_4_umls_matching(self, df: pd.DataFrame, field: str) -> pd.DataFrame:
        """Step 4: match field values against UMLS."""
        print(f"[SEARCH] Step 4: UMLS Matching for {field}")
        print("-" * 30)
        
        if field not in df.columns:
            print(f"[WARN] Field {field} not found in data")
            return pd.DataFrame()
        
        # Filter to samples with non-null field values
        field_df = df[df[field].notna()].copy()
        print(f"[INFO] Samples with {field}: {len(field_df)}")
        
        if len(field_df) == 0:
            print(f"[WARN] No samples with {field} values")
            return pd.DataFrame()
        
        # Get unique values
        unique_values = field_df[field].unique()
        print(f"[INFO] Unique {field} values: {len(unique_values)}")
        
        # Initialize UMLS reader
        umls_reader = UMLSCSVReader(self.umls_dir)
        
        # Map field to UMLS file
        field_mapping = {
            'cell_type': 'cell_type',
            'tissue': 'tissue', 
            'disease_state_modified': 'disease'
        }
        umls_field = field_mapping.get(field, field)
        
        try:
            # Load UMLS data
            umls_data = umls_reader.load_umls_data(umls_field)
            print(f"[OK] Loaded {len(umls_data)} UMLS entries for {umls_field}")
            
            # Perform matching
            matches = umls_reader.query_terms(unique_values.tolist(), umls_field)
            print(f"[OK] Found {len(matches)} UMLS matches")
            
            # Apply matches to dataframe
            # Create mapping dictionary
            match_dict = {}
            for _, row in matches.iterrows():
                match_dict[row['STR']] = {
                    'CUI': row['CUI'],
                    'STR': row['STR'],
                    'SAB': row['SAB'],
                    'STY': row['STY']
                }
            
            # Apply mapping
            def map_to_umls(value):
                if pd.isna(value):
                    return pd.Series([None, None, None, None])
                
                # Try exact match first
                if value in match_dict:
                    match = match_dict[value]
                    return pd.Series([match['CUI'], match['STR'], match['SAB'], match['STY']])
                
                # Try case-insensitive match
                for umls_str, match in match_dict.items():
                    if str(value).lower() == str(umls_str).lower():
                        return pd.Series([match['CUI'], match['STR'], match['SAB'], match['STY']])
                
                return pd.Series([None, None, None, None])
            
            # Apply mapping to create UMLS columns
            umls_cols = field_df[field].apply(map_to_umls)
            umls_cols.columns = ['CUI', 'STR', 'SAB', 'STY']
            
            # Combine with original data
            result_df = pd.concat([field_df.reset_index(drop=True), umls_cols.reset_index(drop=True)], axis=1)
            
            matched_count = result_df['CUI'].notna().sum()
            match_rate = matched_count / len(result_df) * 100
            print(f"[OK] UMLS matching complete: {matched_count}/{len(result_df)} ({match_rate:.1f}%)")
            
            return result_df
            
        except Exception as e:
            print(f"[FAIL] UMLS matching failed: {e}")
            return pd.DataFrame()
    
    def step_5_cui_filtering(self, df: pd.DataFrame, field: str) -> pd.DataFrame:
        """Step 5: keep only the rows that got a CUI."""
        print(f"[TARGET] Step 5: CUI Filtering for {field}")
        print("-" * 30)
        
        if 'CUI' not in df.columns:
            print("[WARN] No CUI column found - no filtering applied")
            return df
        
        original_count = len(df)
        filtered_df = df[df['CUI'].notna()].copy()
        final_count = len(filtered_df)
        
        print(f"[INFO] Original samples: {original_count}")
        print(f"[INFO] With CUI: {final_count}")
        print(f"[INFO] Filtered out: {original_count - final_count}")
        
        if final_count > 0:
            print(f"[OK] CUI filtering complete")
        else:
            print("[WARN] No samples with CUI found")
        
        return filtered_df
    
    def step_6_csv_output(self, df: pd.DataFrame, field: str) -> str:
        """Step 6: write the CSV output."""
        print(f"[IO] Step 6: CSV Output for {field}")
        print("-" * 30)
        
        if len(df) == 0:
            print(f"[WARN] No data to save for {field}")
            return ""
        
        # Generate filename
        field_name = 'disease' if field == 'disease_state_modified' else field
        filename = f"HS_{field_name}_1st_pass_meta_table.csv"
        output_path = os.path.join(self.output_dir, filename)
        
        # Save to CSV
        df.to_csv(output_path, index=False)
        
        file_size = os.path.getsize(output_path)
        print(f"[OK] Saved: {filename}")
        print(f"[INFO] Rows: {len(df)}")
        print(f"[INFO] Columns: {len(df.columns)}")
        print(f"[INFO] File size: {file_size:,} bytes")
        
        return output_path
    
    def test_complete_workflow_for_field(self, field: str):
        """Test complete workflow for a single field."""
        print(f"\n[PROC] TESTING COMPLETE WORKFLOW FOR: {field.upper()}")
        print("=" * 60)
        
        try:
            # Step 1: Load data (shared)
            if not hasattr(self, 'filtered_df'):
                raw_df = self.step_1_data_loading()
                self.filtered_df = self.step_2_filtering(raw_df)
                self.consolidated_df = self.step_3_field_consolidation(self.filtered_df)
            
            # Use pipeline method for field-specific processing
            try:
                pipeline = FirstPassPipeline(self.meta_dir, self.umls_dir)
                first_pass_df = pipeline.create_first_pass_table(field, 
                    output_path=os.path.join(self.output_dir, f"HS_{field.replace('_state_modified', '')}_1st_pass_meta_table.csv"))
                
                matched_df = first_pass_df  # The pipeline already handles all steps
                
                # Store results
                self.results[field] = {
                    'success': True,
                    'total_samples': len(self.consolidated_df),
                    'matched_samples': len(matched_df),
                    'match_rate': len(matched_df) / len(self.consolidated_df) * 100 if len(self.consolidated_df) > 0 else 0,
                    'output_file': os.path.join(self.output_dir, f"HS_{field.replace('_state_modified', '')}_1st_pass_meta_table.csv")
                }
                
                print(f"[DONE] {field.upper()} WORKFLOW COMPLETED SUCCESSFULLY!")
            
            except Exception as pipeline_error:
                # Fallback to manual processing
                print(f"[WARN] Pipeline method failed, trying manual processing: {pipeline_error}")
                matched_df = self.step_4_umls_matching(self.consolidated_df, field)
                
                if len(matched_df) > 0:
                    cui_filtered_df = self.step_5_cui_filtering(matched_df, field)
                    output_path = self.step_6_csv_output(cui_filtered_df, field)
                    
                    self.results[field] = {
                        'success': True,
                        'total_samples': len(matched_df),
                        'matched_samples': len(cui_filtered_df),
                        'match_rate': len(cui_filtered_df) / len(matched_df) * 100 if len(matched_df) > 0 else 0,
                        'output_file': output_path
                    }
                    print(f"[DONE] {field.upper()} WORKFLOW COMPLETED (Manual)!")
                else:
                    self.results[field] = {
                        'success': False,
                        'error': 'No UMLS matching data generated',
                        'total_samples': 0,
                        'matched_samples': 0,
                        'match_rate': 0,
                        'output_file': ''
                    }
                    print(f"[WARN] {field.upper()} WORKFLOW COMPLETED WITH WARNINGS")
                
        except Exception as e:
            self.results[field] = {
                'success': False,
                'error': str(e),
                'total_samples': 0,
                'matched_samples': 0,
                'match_rate': 0,
                'output_file': ''
            }
            print(f"[FAIL] {field.upper()} WORKFLOW FAILED: {e}")
    
    def run_all_tests(self) -> int:
        """
        Run complete workflow tests for all fields.

        Returns:
            0 if every field's workflow succeeded, 1 if any of them failed.
        """
        print("[TEST] GENOAR Analysis - Complete First-Pass Workflow Test")
        print("=" * 70)
        print("Testing the complete process flow from META files to CSV output\n")

        # Test each field
        fields = ['cell_type', 'tissue', 'disease_state_modified']

        for field in fields:
            self.test_complete_workflow_for_field(field)

        # Generate summary
        return self.print_summary()

    def print_summary(self) -> int:
        """Print test summary and return the exit status it implies."""
        print("\n" + "=" * 70)
        print("[INFO] WORKFLOW TEST SUMMARY")
        print("=" * 70)
        
        successful_fields = []
        failed_fields = []
        
        for field, result in self.results.items():
            field_display = field.replace('_', ' ').title()
            
            if result['success']:
                successful_fields.append(field)
                print(f"\n[OK] {field_display}:")
                print(f"   [FILE] File: {os.path.basename(result['output_file'])}")
                print(f"   [INFO] Total samples: {result['total_samples']:,}")
                print(f"   [TARGET] Matched samples: {result['matched_samples']:,}")
                print(f"   [STAT] Match rate: {result['match_rate']:.2f}%")
            else:
                failed_fields.append(field)
                print(f"\n[FAIL] {field_display}:")
                print(f"   [WARN] Error: {result.get('error', 'Unknown error')}")
        
        print(f"\n[DIR] Output directory: {self.output_dir}")
        print(f"[OK] Successful workflows: {len(successful_fields)}/{len(self.results)}")
        
        if successful_fields:
            print(f"[DONE] Successfully generated: {', '.join(successful_fields)}")
            print("\n[LIST] Generated Files:")
            for field in successful_fields:
                result = self.results[field]
                print(f"   • {os.path.basename(result['output_file'])}")
        
        if failed_fields:
            print(f"[FAIL] Failed workflows: {', '.join(failed_fields)}")

        # One failed field is a failed run: not "SUCCESS" whenever a single
        # field works, "PARTIAL SUCCESS" when none does, and "ready for
        # production use!" either way, with 0 returned regardless.
        if failed_fields:
            print(f"\n[TARGET] OVERALL RESULT: FAILED "
                  f"({len(failed_fields)}/{len(self.results)} workflows failed)")
            print("The first-pass workflow is NOT usable as it stands: "
                  "see the errors above.")
            return 1

        print(f"\n[TARGET] OVERALL RESULT: SUCCESS "
              f"({len(successful_fields)}/{len(self.results)} workflows completed)")
        print("\n[NOTE] Note: This workflow creates UMLS-matched metadata tables without Ground Truth comparison.")
        return 0

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='First-Pass Workflow Test - build UMLS-matched metadata tables',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Default run (uses sample_crawl_output/META)
    python test_first_pass_workflow.py

    # Point at a specific META directory
    python test_first_pass_workflow.py --meta-dir ~/genoar/crawl_output/META

    # Point at a specific META directory and output directory
    python test_first_pass_workflow.py -m ~/genoar/crawl_output/META -o ~/genoar/output

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

    parser.add_argument(
        '-o', '--output-dir',
        type=str,
        default=None,
        help='Path to the output directory (default: test_first_pass_output)'
    )

    return parser.parse_args()


def require_fixtures(meta_dir: Path, umls_dir: Path) -> None:
    """
    Refuse to run - explicitly, as a skip - when the inputs are not there.

    Both directories are optional fixtures: crawled META files are gitignored,
    and the UMLS export is a separate download. Without them there is no
    workflow to test. Running anyway produces the defect this guard exists
    for: three "WORKFLOW FAILED" blocks, "Successful workflows: 0/3", and then
    exit 0, so workflow/run.sh reports "All tests passed".

    Both checks come from conftest, which is where the sibling scripts get them
    too. Spelling them out here and nowhere else is what lets an empty
    crawl_output/META through: the sibling copies never get written.

    Raises:
        FixtureUnavailable: if META or UMLS inputs are missing.
    """
    require_meta_files(meta_dir)
    require_umls_files(umls_dir)


def main():
    """Run the complete first-pass workflow test."""
    args = parse_args()

    # Resolve paths
    meta_dir = Path(args.meta_dir) if args.meta_dir else get_meta_dir()
    output_dir = Path(args.output_dir) if args.output_dir else get_output_dir()
    umls_dir = get_umls_dir()  # Always fixed

    print("=" * 60)
    print("[TEST] First-Pass Workflow Test")
    print("=" * 60)
    print(f"[DIR] META directory: {meta_dir}")
    print(f"[DIR] UMLS directory: {umls_dir} (fixed)")
    print(f"[DIR] Output directory: {output_dir}")
    print("=" * 60)

    try:
        require_fixtures(meta_dir, umls_dir)
    except FixtureUnavailable as exc:
        print(f"\n[SKIP] First-pass workflow test skipped: {exc}")
        print("[SKIP] Nothing was tested. This is a skip, not a pass.")
        sys.exit(0)

    tester = FirstPassWorkflowTester(
        meta_dir=meta_dir,
        umls_dir=umls_dir,
        output_dir=output_dir
    )

    try:
        status = tester.run_all_tests()
    except Exception as e:
        print(f"\n[FAIL] WORKFLOW TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    sys.exit(status)


if __name__ == "__main__":
    main()