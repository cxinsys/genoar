"""Data reading functions for GENOAR analysis."""

import os
import re
import pandas as pd
from pathlib import Path
from typing import Optional, Union
import logging

from ..core.data import GenoarData
from ..core.config import Config

logger = logging.getLogger(__name__)

def read_crawled_meta(
    meta_dir: Union[str, Path],
    file_pattern: str = Config.META_FILE_PATTERN,
    encoding: str = 'utf-8'
) -> GenoarData:
    """
    Read crawled META files and create a GenoarData object.
    
    Parameters:
        meta_dir: Directory containing META files
        file_pattern: File pattern to match (e.g., "*_meta.txt")
        encoding: File encoding
    
    Returns:
        GenoarData object with loaded metadata
    """
    meta_dir = Path(meta_dir)
    
    if not meta_dir.exists():
        raise FileNotFoundError(f"META directory not found: {meta_dir}")
    
    meta_files = list(meta_dir.glob(file_pattern))
    
    if not meta_files:
        raise FileNotFoundError(f"No META files found in {meta_dir} with pattern {file_pattern}")
    
    logger.info(f"Found {len(meta_files)} META files")
    
    # Load and combine all META files
    dataframes = []
    for file_path in meta_files:
        try:
            # Extract Series ID from filename using regex
            series_match = re.search(r'GSE\d+', file_path.name)
            if not series_match:
                logger.warning(f"Could not extract Series ID from {file_path.name}")
                continue
            
            series_id = series_match.group()
            
            # Read the file with error handling for malformed CSV
            try:
                # Try tab-separated first (most common)
                df = pd.read_csv(file_path, sep="\t", encoding=encoding, on_bad_lines='skip')
            except pd.errors.EmptyDataError:
                logger.warning(f"Empty file: {file_path}")
                continue
            except pd.errors.ParserError as pe:
                logger.warning(f"Parser error in {file_path}: {pe}")
                # Try comma-separated as fallback
                try:
                    df = pd.read_csv(file_path, sep=",", encoding=encoding, on_bad_lines='skip')
                except pd.errors.ParserError as pe2:
                    logger.error(f"Could not parse {file_path} with either separator: {pe2}")
                    continue
                except pd.errors.EmptyDataError:
                    logger.warning(f"Empty file after fallback: {file_path}")
                    continue

            df['Series'] = series_id
            dataframes.append(df)

        except UnicodeDecodeError as ue:
            logger.error(f"Encoding error reading {file_path}: {ue}")
            continue
        except PermissionError as pe:
            logger.error(f"Permission denied reading {file_path}: {pe}")
            continue
        except OSError as oe:
            logger.error(f"OS error reading {file_path}: {oe}")
            continue
    
    if not dataframes:
        raise ValueError("No valid META files could be loaded")
    
    combined_df = pd.concat(dataframes, ignore_index=True, sort=False)
    
    combined_df = combined_df.replace('nan', pd.NA)
    
    logger.info(f"Loaded {len(combined_df)} samples from {len(dataframes)} META files")
    
    adata = GenoarData(combined_df)
    adata.log_analysis(f"Loaded META data from {len(meta_files)} files")
    
    return adata


def read_umls_results(
    umls_dir: Union[str, Path],
    field: Optional[str] = None
) -> Union[pd.DataFrame, dict]:
    """
    Read UMLS query results from CSV files.
    
    Parameters:
        umls_dir: Directory containing UMLS CSV files
        field: Specific field to load ('cell_type', 'tissue', 'disease_state')
               If None, loads all available files
    
    Returns:
        DataFrame (if field specified) or dict of DataFrames (if field is None)
    """
    umls_dir = Path(umls_dir)
    
    if not umls_dir.exists():
        raise FileNotFoundError(f"UMLS directory not found: {umls_dir}")
    
    file_mapping = Config.get_umls_file_mapping()
    
    if field is not None:
        # Load specific field
        if field not in file_mapping:
            raise ValueError(f"Unsupported field: {field}. Supported: {list(file_mapping.keys())}")
        
        filepath = umls_dir / file_mapping[field]
        if not filepath.exists():
            raise FileNotFoundError(f"UMLS file not found: {filepath}")
        
        try:
            df = pd.read_csv(filepath)
            # Remove index column if present
            if df.columns[0].startswith('Unnamed:'):
                df = df.drop(df.columns[0], axis=1)
            
            logger.info(f"Loaded UMLS data for {field}: {df.shape}")
            return df
            
        except Exception as e:
            logger.error(f"Error reading UMLS file {filepath}: {e}")
            raise
    
    else:
        # Load all available files
        umls_data = {}
        
        for field_name, filename in file_mapping.items():
            filepath = umls_dir / filename
            
            if filepath.exists():
                try:
                    df = pd.read_csv(filepath)
                    # Remove index column if present
                    if df.columns[0].startswith('Unnamed:'):
                        df = df.drop(df.columns[0], axis=1)

                    umls_data[field_name] = df
                    logger.info(f"Loaded UMLS data for {field_name}: {df.shape}")

                except pd.errors.ParserError as pe:
                    logger.error(f"Parser error reading UMLS file {filepath}: {pe}")
                    continue
                except pd.errors.EmptyDataError:
                    logger.warning(f"Empty UMLS file: {filepath}")
                    continue
                except UnicodeDecodeError as ue:
                    logger.error(f"Encoding error reading UMLS file {filepath}: {ue}")
                    continue
            else:
                logger.warning(f"UMLS file not found: {filepath}")
        
        if not umls_data:
            raise FileNotFoundError("No UMLS files could be loaded")
        
        return umls_data

def load_complete_dataset(
    meta_dir: Union[str, Path] = Config.get_default_paths()['meta_dir'],
    umls_dir: Union[str, Path] = Config.get_default_paths()['umls_dir']
) -> GenoarData:
    """
    Convenience function to load complete dataset.
    
    Parameters:
        meta_dir: Directory containing META files
        umls_dir: Directory containing UMLS files
    
    Returns:
        GenoarData object with all data loaded
    """
    # Load META data
    adata = read_crawled_meta(meta_dir)
    
    # Load UMLS data
    try:
        umls_data = read_umls_results(umls_dir)
        for field, df in umls_data.items():
            setattr(adata, f'umls_{field}', df)
        adata.log_analysis(f"Loaded UMLS data for {list(umls_data.keys())}")
    except Exception as e:
        logger.warning(f"Could not load UMLS data: {e}")
    
    return adata