"""Data filtering functions for GENOAR analysis."""

import pandas as pd
from typing import List, Union
import logging

from ..core.data import GenoarData
from ..core.config import Config

logger = logging.getLogger(__name__)

def filter_organism(
    adata: GenoarData,
    organism: str = Config.DEFAULT_ORGANISM,
    inplace: bool = True
) -> GenoarData:
    """
    Filter data by organism.
    
    Parameters:
        adata: GenoarData object
        organism: Organism name to filter by
        inplace: Whether to modify the object in place
    
    Returns:
        Filtered GenoarData object
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if 'Organism' not in adata.meta.columns:
        logger.warning("Organism column not found in META data")
        return adata if inplace else adata
    
    initial_count = len(adata.meta)
    filtered_meta = adata.meta[adata.meta['Organism'] == organism].copy()
    final_count = len(filtered_meta)
    
    logger.info(f"Filtered by organism '{organism}': {initial_count} → {final_count} samples")
    
    if inplace:
        adata.meta = filtered_meta
        adata._initialize_series_info()
        adata.log_analysis(f"Filtered by organism: {organism}")
        return adata
    else:
        new_adata = GenoarData(filtered_meta)
        new_adata.umls = adata.umls
        new_adata.log_analysis(f"Filtered by organism: {organism}")
        return new_adata

def filter_library_source(
    adata: GenoarData,
    source: str = Config.DEFAULT_LIBRARY_SOURCE,
    inplace: bool = True
) -> GenoarData:
    """
    Filter data by library source.
    
    Parameters:
        adata: GenoarData object
        source: Library source to filter by
        inplace: Whether to modify the object in place
    
    Returns:
        Filtered GenoarData object
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if 'LibrarySource' not in adata.meta.columns:
        logger.warning("LibrarySource column not found in META data")
        return adata if inplace else adata
    
    initial_count = len(adata.meta)
    filtered_meta = adata.meta[adata.meta['LibrarySource'].str.contains(source, na=False)].copy()
    final_count = len(filtered_meta)
    
    logger.info(f"Filtered by library source '{source}': {initial_count} → {final_count} samples")
    
    if inplace:
        adata.meta = filtered_meta
        adata._initialize_series_info()
        adata.log_analysis(f"Filtered by library source: {source}")
        return adata
    else:
        new_adata = GenoarData(filtered_meta)
        new_adata.umls = adata.umls
        new_adata.log_analysis(f"Filtered by library source: {source}")
        return new_adata

def drop_missing(
    adata: GenoarData,
    columns: Union[str, List[str]],
    how: str = 'any',
    inplace: bool = True
) -> GenoarData:
    """
    Drop rows with missing values in specified columns.
    
    Parameters:
        adata: GenoarData object
        columns: Column name(s) to check for missing values
        how: How to handle missing values ('any' or 'all')
        inplace: Whether to modify the object in place
    
    Returns:
        Filtered GenoarData object
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if isinstance(columns, str):
        columns = [columns]
    
    # Check if columns exist
    missing_cols = [col for col in columns if col not in adata.meta.columns]
    if missing_cols:
        logger.warning(f"Columns not found in META data: {missing_cols}")
        columns = [col for col in columns if col in adata.meta.columns]
    
    if not columns:
        logger.warning("No valid columns to check for missing values")
        return adata if inplace else adata
    
    initial_count = len(adata.meta)
    filtered_meta = adata.meta.dropna(subset=columns, how=how).copy()
    final_count = len(filtered_meta)
    
    logger.info(f"Dropped missing values in {columns}: {initial_count} → {final_count} samples")
    
    if inplace:
        adata.meta = filtered_meta
        adata._initialize_series_info()
        adata.log_analysis(f"Dropped missing values in: {columns}")
        return adata
    else:
        new_adata = GenoarData(filtered_meta)
        new_adata.umls = adata.umls
        new_adata.log_analysis(f"Dropped missing values in: {columns}")
        return new_adata

def apply_standard_filters(
    adata: GenoarData,
    organism: str = Config.DEFAULT_ORGANISM,
    library_source: str = Config.DEFAULT_LIBRARY_SOURCE,
    required_fields: List[str] = None,
    inplace: bool = True
) -> GenoarData:
    """
    Apply standard filtering pipeline.
    
    Parameters:
        adata: GenoarData object
        organism: Organism to filter by
        library_source: Library source to filter by
        required_fields: Fields that must be non-null
        inplace: Whether to modify the object in place
    
    Returns:
        Filtered GenoarData object
    """
    if not inplace:
        adata = GenoarData(adata.meta.copy())
        adata.umls = adata.umls
    
    # Apply filters in sequence
    adata = filter_organism(adata, organism, inplace=True)
    adata = filter_library_source(adata, library_source, inplace=True)
    
    if required_fields:
        adata = drop_missing(adata, required_fields, inplace=True)
    
    adata.log_analysis("Applied standard filtering pipeline")
    
    return adata