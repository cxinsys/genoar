"""Matching and intersection analysis functions."""

import pandas as pd
from typing import Set, Dict, Any, List
import logging

from ..core.data import GenoarData
from ..core.config import Config

logger = logging.getLogger(__name__)


def analyze_field_values(
    adata: GenoarData,
    field: str,
    case_sensitive: bool = False
) -> Dict[str, Any]:
    """
    Analyze unique values in a specific field.
    
    Parameters:
        adata: GenoarData object
        field: Field to analyze
        case_sensitive: Whether to consider case in string comparisons
    
    Returns:
        Dictionary with field value analysis
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if field not in adata.meta.columns:
        raise ValueError(f"Field '{field}' not found in META data")
    
    field_values = adata.meta[field].dropna()
    
    if not case_sensitive and field_values.dtype == 'object':
        field_values = field_values.str.lower()
    
    unique_values = field_values.unique().tolist()
    value_counts = field_values.value_counts()
    
    analysis = {
        'field': field,
        'total_samples': len(field_values),
        'unique_values': unique_values,
        'unique_count': len(unique_values),
        'value_counts': value_counts.to_dict(),
        'top_10_values': value_counts.head(10).to_dict(),
        'case_sensitive': case_sensitive
    }
    
    # Store in matching results if not already there
    if field not in adata.matching_results:
        adata.matching_results[field] = {}
    
    adata.matching_results[field]['field_analysis'] = analysis
    adata.log_analysis(f"Field value analysis for {field}")
    
    logger.info(f"Field analysis for {field}: {len(unique_values)} unique values")
    
    return analysis

def compare_field_overlap(
    adata: GenoarData,
    field1: str,
    field2: str
) -> Dict[str, Any]:
    """
    Compare overlap between two fields in terms of Series coverage.
    
    Parameters:
        adata: GenoarData object
        field1: First field to compare
        field2: Second field to compare
    
    Returns:
        Dictionary with overlap analysis
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    series1 = adata.get_series_with_field(field1)
    series2 = adata.get_series_with_field(field2)
    
    intersection = series1.intersection(series2)
    union = series1.union(series2)
    
    overlap = {
        'field1': field1,
        'field2': field2,
        'field1_series_count': len(series1),
        'field2_series_count': len(series2),
        'intersection_count': len(intersection),
        'union_count': len(union),
        'jaccard_index': len(intersection) / len(union) if union else 0,
        'field1_only': list(series1 - series2),
        'field2_only': list(series2 - series1),
        'both_fields': list(intersection)
    }
    
    logger.info(f"Field overlap {field1} vs {field2}: {len(intersection)} common series")
    
    return overlap