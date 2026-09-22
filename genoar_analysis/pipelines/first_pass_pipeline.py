"""First Pass Pipeline for creating HS_*_1st_pass_meta_table.csv files"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
import logging
import re

from ..core.config import Config
from ..core.data import GenoarData
from ..io.readers import read_crawled_meta
from ..io.umls_readers import UMLSCSVReader
from ..preprocessing.filters import filter_organism, filter_library_source

logger = logging.getLogger(__name__)

class FirstPassPipeline:
    """
    Pipeline for creating first-pass UMLS-matched metadata tables.

    This pipeline recreates the functionality of legacy scripts:
    - legacy_ground_truth_test_cell_type.py
    - legacy_ground_truth_test_disease.py
    - legacy_ground_truth_test_tissue.py
    """

    def __init__(self,
                 meta_dir: Union[str, Path],
                 umls_data_dir: str = "all_query_results",
                 organism: str = Config.DEFAULT_ORGANISM,
                 library_source: str = Config.DEFAULT_LIBRARY_SOURCE,
):
        """
        Initialize First Pass Pipeline.

        Parameters:
            meta_dir: Directory containing META files
            umls_data_dir: Directory containing UMLS CSV files
            organism: Organism the output tables are about. The default is the
                one this project was built around; another organism only needs
                this value, not a source edit.
            library_source: Library source substring the samples must carry.
        """
        self.meta_dir = Path(meta_dir)
        self.organism = organism
        self.library_source = library_source
        self.umls_reader = UMLSCSVReader(umls_data_dir)
        # Cache for preprocessed data to avoid redundant loading
        self._cached_data: Optional[pd.DataFrame] = None
        self._cached_consolidated_data: Optional[pd.DataFrame] = None
    
    def load_and_preprocess_data(self, use_cache: bool = True) -> pd.DataFrame:
        """
        Load META files and perform preprocessing.

        Parameters:
            use_cache: If True, return cached data if available (default: True)

        Returns:
            Preprocessed DataFrame
        """
        # Return cached data if available and caching is enabled
        if use_cache and self._cached_data is not None:
            logger.info("Using cached preprocessed data")
            return self._cached_data.copy()

        meta_files = list(self.meta_dir.glob("*_meta.txt"))
        if not meta_files:
            meta_files = list(self.meta_dir.glob("*.txt"))

        if not meta_files:
            raise FileNotFoundError(f"No META files found in {self.meta_dir}")

        logger.info(f"Found {len(meta_files)} META files")

        # Load and combine all META files
        dataframes = []
        for file_path in meta_files:
            try:
                # Extract Series ID from filename
                series_match = re.search(r'GSE\d+', file_path.name)
                if not series_match:
                    logger.warning(f"Could not extract GSE ID from {file_path.name}")
                    continue

                series_id = series_match.group()

                # Read file with proper separator detection
                try:
                    # Try tab-separated first (most common for META files)
                    df = pd.read_csv(file_path, sep="\t", encoding='utf-8', on_bad_lines='skip')
                except pd.errors.ParserError as pe:
                    logger.warning(f"Parser error with tab separator in {file_path}: {pe}")
                    # Fall back to comma-separated
                    try:
                        df = pd.read_csv(file_path, sep=",", encoding='utf-8', on_bad_lines='skip')
                    except pd.errors.ParserError as pe2:
                        logger.error(f"Could not parse {file_path} with either separator: {pe2}")
                        continue
                except pd.errors.EmptyDataError:
                    logger.warning(f"Empty file: {file_path}")
                    continue

                df['Series'] = series_id
                dataframes.append(df)

            except UnicodeDecodeError as ue:
                logger.error(f"Encoding error loading {file_path}: {ue}")
                continue
            except PermissionError as pe:
                logger.error(f"Permission denied loading {file_path}: {pe}")
                continue
            except OSError as oe:
                logger.error(f"OS error loading {file_path}: {oe}")
                continue

        if not dataframes:
            raise ValueError("No valid META files could be loaded")

        combined_df = pd.concat(dataframes, ignore_index=True, sort=False)

        # Clean NaN values (use pd.NA for consistency)
        combined_df = combined_df.replace(['nan', 'NaN', 'NA', 'N/A', 'null', 'None', ''], pd.NA)
        
        # A filter that cannot run is skipped rather than fatal: GEO's column
        # set varies, and a run over what is there is the intended behaviour.
        # Say what the skip costs, though - it widens the population rather
        # than leaving it alone, so the tables then describe more than their
        # name claims.
        if 'Organism' in combined_df.columns:
            combined_df = combined_df[combined_df['Organism'] == self.organism]
            logger.info(
                f"Filtered by Organism '{self.organism}': {len(combined_df)} samples remaining")
        else:
            logger.warning(
                "'Organism' column not found: skipping the organism filter, so "
                "these tables cover every organism present, not just "
                f"'{self.organism}'. Columns present: {sorted(combined_df.columns)}")

        if 'LibrarySource' in combined_df.columns:
            combined_df = combined_df[
                combined_df['LibrarySource'].str.contains(self.library_source, na=False)
            ]
            logger.info(
                f"Filtered by LibrarySource '{self.library_source}': "
                f"{len(combined_df)} samples remaining"
            )
        else:
            logger.warning(
                "'LibrarySource' column not found: skipping that filter, so these "
                f"tables cover more than '{self.library_source}'.")

        logger.info(f"Preprocessed data: {len(combined_df)} samples from {len(combined_df['Series'].unique())} series")

        # Cache the result
        self._cached_data = combined_df.copy()

        return combined_df
    
    def consolidate_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Consolidate fields (disease_state_modified, sex).
        
        Parameters:
            df: Input DataFrame
            
        Returns:
            DataFrame with consolidated fields
        """
        df = df.copy()
        
        # Create disease_state_modified
        disease_fields = ['disease', 'disease_state', 'diagnosis', 
                         'disease_diagnosis', 'patient_diagnosis', 'health_status']
        
        # Check which fields exist
        existing_disease_fields = [f for f in disease_fields if f in df.columns]
        
        if existing_disease_fields:
            df['disease_state_modified'] = df[existing_disease_fields[0]]
            for field in existing_disease_fields[1:]:
                df['disease_state_modified'] = df['disease_state_modified'].fillna(df[field])
            
            logger.info(f"Created disease_state_modified from {len(existing_disease_fields)} fields")
        
        # Consolidate sex field
        if 'sex' in df.columns and 'gender' in df.columns:
            df['sex'] = df['sex'].fillna(df['gender'])
        elif 'gender' in df.columns and 'sex' not in df.columns:
            df['sex'] = df['gender']
        
        return df
    
    def prepare_metadata(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare metadata by selecting required columns.
        
        Parameters:
            df: Input DataFrame
            
        Returns:
            DataFrame with selected columns
        """
        required_columns = [
            "Series", "Run", "BioSample", "Sample Name", 
            "Platform", "Instrument", "Organism", 
            "strain", "sex", "Age", "treatment", "genotype", 
            "tissue", "cell_type", "disease_state_modified"
        ]
        
        # Only keep columns that exist
        available_columns = [col for col in required_columns if col in df.columns]
        
        logger.info(f"Selecting {len(available_columns)} columns from {len(required_columns)} required")
        logger.info(f"Available columns: {available_columns}")
        logger.info(f"Missing columns: {[col for col in required_columns if col not in df.columns]}")
        logger.info(f"DataFrame columns: {list(df.columns)}")
        
        return df[available_columns]
    
    def perform_umls_matching(self, 
                            crawled_meta: pd.DataFrame, 
                            field: str) -> pd.DataFrame:
        """
        Perform UMLS matching for a specific field.
        
        Parameters:
            crawled_meta: Metadata DataFrame
            field: Field to match ('cell_type', 'tissue', or 'disease_state_modified')
            
        Returns:
            DataFrame with UMLS annotations
        """
        # Drop rows with null values in the target field
        crawled_meta_dropna = crawled_meta.dropna(subset=[field]).copy()
        
        logger.info(f"Processing {len(crawled_meta_dropna)} samples with non-null {field}")
        
        unique_values = crawled_meta_dropna[field].unique()
        logger.info(f"Found {len(unique_values)} unique {field} values")
        
        umls_results = self.umls_reader.query_terms(
            list(unique_values), 
            field if field != 'disease_state_modified' else 'disease',
            case_sensitive=False
        )
        
        # Apply SAB priority selection
        if not umls_results.empty:
            umls_results = self.umls_reader.get_sab_priority_selection(
                umls_results, 
                field if field != 'disease_state_modified' else 'disease'
            )
        
        # Create mapping dictionary
        umls_mapping = {}
        for _, row in umls_results.iterrows():
            key = row['STR'].lower() if not pd.isna(row['STR']) else ''
            umls_mapping[key] = {
                'CUI': row['CUI'],
                'STR': row['STR'],
                'SAB': row['SAB'],
                'STY': row['STY'] if 'STY' in row else np.nan
            }
        
        # Apply mapping to DataFrame
        def map_umls(value):
            if pd.isna(value):
                return pd.Series([np.nan, np.nan, np.nan, np.nan])
            
            value_lower = str(value).lower()
            if value_lower in umls_mapping:
                info = umls_mapping[value_lower]
                return pd.Series([info['CUI'], info['STR'], info['SAB'], info['STY']])
            else:
                return pd.Series([np.nan, np.nan, np.nan, np.nan])
        
        # Add UMLS columns
        umls_cols = crawled_meta_dropna[field].apply(map_umls)
        crawled_meta_dropna['CUI'] = umls_cols.iloc[:, 0]
        crawled_meta_dropna['STR'] = umls_cols.iloc[:, 1]
        crawled_meta_dropna['SAB'] = umls_cols.iloc[:, 2]
        crawled_meta_dropna['STY'] = umls_cols.iloc[:, 3]
        
        # Count successful matches
        matched_count = crawled_meta_dropna['CUI'].notna().sum()
        logger.info(f"UMLS matching: {matched_count}/{len(crawled_meta_dropna)} samples matched")
        
        return crawled_meta_dropna
    
    def create_first_pass_table(self, 
                               field: str, 
                               output_path: Optional[str] = None) -> pd.DataFrame:
        """
        Create first-pass table for a specific field.
        
        Parameters:
            field: Field to process ('cell_type', 'tissue', or 'disease_state_modified')
            output_path: Optional output path for CSV file
            
        Returns:
            First-pass DataFrame with UMLS matches
        """
        logger.info(f"Creating first-pass table for {field}")
        
        combined_df = self.load_and_preprocess_data()
        
        combined_df = self.consolidate_fields(combined_df)
        
        crawled_meta = self.prepare_metadata(combined_df)
        
        # Check if field exists
        if field not in crawled_meta.columns:
            raise ValueError(f"Field '{field}' not found in metadata")
        
        crawled_meta_matched = self.perform_umls_matching(crawled_meta, field)
        
        first_pass_df = crawled_meta_matched.dropna(subset=['CUI'])
        
        logger.info(f"First-pass table: {len(first_pass_df)} samples with UMLS matches")
        
        if output_path:
            first_pass_df.to_csv(output_path, index=False)
            logger.info(f"Saved first-pass table to {output_path}")
        else:
            # Default naming
            field_name = field.replace('_state_modified', '')
            default_path = f"HS_{field_name}_1st_pass_meta_table.csv"
            first_pass_df.to_csv(default_path, index=False)
            logger.info(f"Saved first-pass table to {default_path}")
        
        return first_pass_df
    
    def create_all_first_pass_tables(self, output_dir: Optional[str] = None) -> Dict[str, pd.DataFrame]:
        """
        Create first-pass tables for all three fields.
        
        Parameters:
            output_dir: Optional output directory
            
        Returns:
            Dictionary with field names as keys and DataFrames as values
        """
        results = {}
        
        fields = ['cell_type', 'tissue', 'disease_state_modified']
        
        for field in fields:
            try:
                # Prepare output path
                if output_dir:
                    output_dir_path = Path(output_dir)
                    output_dir_path.mkdir(parents=True, exist_ok=True)
                    field_name = field.replace('_state_modified', '')
                    output_path = output_dir_path / f"HS_{field_name}_1st_pass_meta_table.csv"
                else:
                    output_path = None
                
                first_pass_df = self.create_first_pass_table(field, output_path)
                results[field] = first_pass_df
                
            except Exception as e:
                # Keep going so one bad field does not hide the other two, but log the
                # exception type and the traceback: a bare str(e) turns a KeyError into
                # an unattributable one-word message.
                logger.error(
                    f"Error creating first-pass table for {field}: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
                results[field] = None
        
        return results
    
    def analyze_results(self, results: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        """
        Analyze the results of first-pass table creation.
        
        Parameters:
            results: Dictionary of field names to DataFrames
            
        Returns:
            Analysis summary
        """
        summary = {}
        
        for field, df in results.items():
            if df is None:
                summary[field] = {"status": "failed"}
                continue
            
            field_summary = {
                "status": "success",
                "total_samples": len(df),
                "unique_series": df['Series'].nunique() if 'Series' in df.columns else 0,
                "unique_values": df[field].nunique() if field in df.columns else 0,
                "sab_distribution": {}
            }
            
            if 'SAB' in df.columns:
                sab_counts = df['SAB'].value_counts().to_dict()
                field_summary['sab_distribution'] = sab_counts
            
            if 'STY' in df.columns and field == 'cell_type':
                sty_counts = df['STY'].value_counts().head(10).to_dict()
                field_summary['top_sty'] = sty_counts
            
            summary[field] = field_summary
        
        return summary


# Convenience function
def create_first_pass_tables(meta_dir: str, 
                           umls_data_dir: str = "all_query_results",
                           output_dir: Optional[str] = None,
                           organism: str = Config.DEFAULT_ORGANISM,
                           library_source: str = Config.DEFAULT_LIBRARY_SOURCE) -> Dict[str, pd.DataFrame]:
    """
    Convenience function to create all first-pass tables.
    
    This function recreates the core functionality of legacy scripts:
    - legacy_ground_truth_test_cell_type.py (lines 111-148)
    - legacy_ground_truth_test_disease.py (lines 111-146) 
    - legacy_ground_truth_test_tissue.py (lines 135-156)
    
    Parameters:
        meta_dir: Directory containing META files
        umls_data_dir: Directory containing UMLS CSV files (default: "all_query_results")
        output_dir: Optional output directory
        organism: Organism the tables are about
        library_source: Library source substring the samples must carry
        
    Returns:
        Dictionary with field names as keys and DataFrames as values
    """
    pipeline = FirstPassPipeline(meta_dir, umls_data_dir,
                                 organism=organism,
                                 library_source=library_source)
    results = pipeline.create_all_first_pass_tables(output_dir)
    
    summary = pipeline.analyze_results(results)
    
    for field, info in summary.items():
        if info['status'] == 'success':
            logger.info(f"\n{field} first-pass table:")
            logger.info(f"  Total samples: {info['total_samples']}")
            logger.info(f"  Unique series: {info['unique_series']}")
            logger.info(f"  Unique {field} values: {info['unique_values']}")
            if info['sab_distribution']:
                logger.info(f"  SAB distribution: {info['sab_distribution']}")
    
    return results