"""UMLS analysis functions for GENOAR."""

import pandas as pd
from typing import Dict, Any, List, Set, Tuple
import logging
from collections import defaultdict

from ..core.data import GenoarData
from ..core.config import Config

logger = logging.getLogger(__name__)

def umls_matching(
    adata: GenoarData,
    umls_data: pd.DataFrame,
    field: str,
    case_sensitive: bool = False,
    store_results: bool = True
) -> Dict[str, Any]:
    """
    Perform UMLS matching analysis for a specific field.
    
    Parameters:
        adata: GenoarData object
        umls_data: UMLS DataFrame with columns [CUI, STR, SAB, STY]
        field: Field to analyze ('cell_type', 'tissue', etc.)
        case_sensitive: Whether to consider case in matching
        store_results: Whether to store results in adata
    
    Returns:
        Dictionary with UMLS matching results
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if field not in adata.meta.columns:
        raise ValueError(f"Field '{field}' not found in META data")
    
    crawled_values = adata.get_unique_values(field, dropna=True)
    
    if not case_sensitive:
        crawled_values_lower = {val.lower() for val in crawled_values}
        umls_values_lower = {val.lower() for val in umls_data['STR'].unique()}
    else:
        crawled_values_lower = set(crawled_values)
        umls_values_lower = set(umls_data['STR'].unique())
    
    # Calculate matches
    matched_terms = crawled_values_lower.intersection(umls_values_lower)
    unmatched_terms = crawled_values_lower - umls_values_lower
    
    # Get detailed matching info
    matched_details = []
    for term in matched_terms:
        if case_sensitive:
            umls_matches = umls_data[umls_data['STR'] == term]
        else:
            umls_matches = umls_data[umls_data['STR'].str.lower() == term.lower()]
        
        for _, row in umls_matches.iterrows():
            matched_details.append({
                'original_term': term,
                'cui': row['CUI'],
                'str': row['STR'],
                'sab': row['SAB'],
                'sty': row['STY']
            })
    
    results = {
        'field': field,
        'total_crawled_terms': len(crawled_values),
        'total_umls_terms': len(umls_data['STR'].unique()),
        'matched_count': len(matched_terms),
        'unmatched_count': len(unmatched_terms),
        'matching_rate': len(matched_terms) / len(crawled_values) if crawled_values else 0,
        'matched_terms': list(matched_terms),
        'unmatched_terms': list(unmatched_terms),
        'matched_details': matched_details,
        'case_sensitive': case_sensitive
    }
    
    if store_results:
        if field not in adata.matching_results:
            adata.matching_results[field] = {}
        adata.matching_results[field]['umls_matching'] = results
        adata.log_analysis(f"UMLS matching for {field}: {len(matched_terms)} matched terms")
    
    logger.info(f"UMLS matching for {field}: {len(matched_terms)}/{len(crawled_values)} matched")
    
    return results

def analyze_sab_distribution(
    umls_data: pd.DataFrame,
    top_n: int = 20
) -> Dict[str, Any]:
    """
    Analyze SAB (Source Abbreviation) distribution in UMLS data.
    
    Parameters:
        umls_data: UMLS DataFrame
        top_n: Number of top SAB values to analyze
    
    Returns:
        Dictionary with SAB distribution analysis
    """
    if 'SAB' not in umls_data.columns:
        raise ValueError("SAB column not found in UMLS data")
    
    sab_counts = umls_data['SAB'].value_counts()
    top_sab = sab_counts.head(top_n)
    
    # Calculate percentages
    total_entries = len(umls_data)
    sab_percentages = (sab_counts / total_entries * 100).round(2)
    
    results = {
        'total_entries': total_entries,
        'unique_sab_count': len(sab_counts),
        'sab_counts': sab_counts.to_dict(),
        'sab_percentages': sab_percentages.to_dict(),
        'top_sab': top_sab.to_dict(),
        'top_sab_percentages': sab_percentages.head(top_n).to_dict()
    }
    
    logger.info(f"SAB distribution: {len(sab_counts)} unique SABs, top: {top_sab.index[0]} ({top_sab.iloc[0]})")
    
    return results

def analyze_sty_distribution(
    umls_data: pd.DataFrame,
    sab_filter: str = None,
    top_n: int = 20
) -> Dict[str, Any]:
    """
    Analyze STY (Semantic Type) distribution in UMLS data.
    
    Parameters:
        umls_data: UMLS DataFrame
        sab_filter: Filter by specific SAB value
        top_n: Number of top STY values to analyze
    
    Returns:
        Dictionary with STY distribution analysis
    """
    if 'STY' not in umls_data.columns:
        raise ValueError("STY column not found in UMLS data")
    
    if sab_filter:
        if 'SAB' not in umls_data.columns:
            raise ValueError("SAB column not found for filtering")
        filtered_data = umls_data[umls_data['SAB'] == sab_filter]
        if len(filtered_data) == 0:
            raise ValueError(f"No data found for SAB: {sab_filter}")
    else:
        filtered_data = umls_data
    
    sty_counts = filtered_data['STY'].value_counts()
    top_sty = sty_counts.head(top_n)
    
    # Calculate percentages
    total_entries = len(filtered_data)
    sty_percentages = (sty_counts / total_entries * 100).round(2)
    
    results = {
        'sab_filter': sab_filter,
        'total_entries': total_entries,
        'unique_sty_count': len(sty_counts),
        'sty_counts': sty_counts.to_dict(),
        'sty_percentages': sty_percentages.to_dict(),
        'top_sty': top_sty.to_dict(),
        'top_sty_percentages': sty_percentages.head(top_n).to_dict()
    }
    
    logger.info(f"STY distribution: {len(sty_counts)} unique STYs, top: {top_sty.index[0]} ({top_sty.iloc[0]})")
    
    return results

def analyze_sab_sty_relationship(
    umls_data: pd.DataFrame,
    top_sab: int = 10,
    top_sty: int = 10
) -> Dict[str, Any]:
    """
    Analyze the relationship between SAB and STY values.
    
    Parameters:
        umls_data: UMLS DataFrame
        top_sab: Number of top SAB values to include
        top_sty: Number of top STY values to include
    
    Returns:
        Dictionary with SAB-STY relationship analysis
    """
    if not {'SAB', 'STY'}.issubset(umls_data.columns):
        raise ValueError("SAB and STY columns required")
    
    top_sab_values = umls_data['SAB'].value_counts().head(top_sab).index.tolist()
    top_sty_values = umls_data['STY'].value_counts().head(top_sty).index.tolist()
    
    # Create cross-tabulation
    crosstab = pd.crosstab(umls_data['SAB'], umls_data['STY'])
    
    crosstab_filtered = crosstab.loc[top_sab_values, top_sty_values]
    
    # Calculate co-occurrence statistics
    cooccurrence_stats = {}
    for sab in top_sab_values:
        sab_data = umls_data[umls_data['SAB'] == sab]
        sty_dist = sab_data['STY'].value_counts()
        cooccurrence_stats[sab] = {
            'total_entries': len(sab_data),
            'unique_sty_count': len(sty_dist),
            'top_sty': sty_dist.head(3).to_dict()
        }
    
    results = {
        'top_sab_values': top_sab_values,
        'top_sty_values': top_sty_values,
        'crosstab': crosstab_filtered.to_dict(),
        'cooccurrence_stats': cooccurrence_stats
    }
    
    logger.info(f"SAB-STY relationship: {len(top_sab_values)} SABs × {len(top_sty_values)} STYs analyzed")
    
    return results

def calculate_umls_coverage(
    adata: GenoarData,
    umls_data: pd.DataFrame,
    field: str,
    top_sab_only: int = None
) -> Dict[str, Any]:
    """
    Calculate UMLS coverage for different SAB sources.
    
    Parameters:
        adata: GenoarData object
        umls_data: UMLS DataFrame
        field: Field to analyze
        top_sab_only: If specified, only consider top N SAB values
    
    Returns:
        Dictionary with coverage analysis
    """
    if field not in adata.meta.columns:
        raise ValueError(f"Field '{field}' not found in META data")
    
    crawled_values = set(val.lower() for val in adata.get_unique_values(field, dropna=True))
    
    coverage_results = {}
    
    if top_sab_only:
        sab_values = umls_data['SAB'].value_counts().head(top_sab_only).index.tolist()
    else:
        sab_values = umls_data['SAB'].unique().tolist()
    
    for sab in sab_values:
        sab_data = umls_data[umls_data['SAB'] == sab]
        sab_terms = set(term.lower() for term in sab_data['STR'].unique())
        
        matched = crawled_values.intersection(sab_terms)
        coverage = len(matched) / len(crawled_values) if crawled_values else 0
        
        coverage_results[sab] = {
            'total_sab_terms': len(sab_terms),
            'matched_terms': len(matched),
            'coverage_rate': coverage,
            'matched_list': list(matched)
        }
    
    # Overall coverage (any SAB)
    all_umls_terms = set(term.lower() for term in umls_data['STR'].unique())
    overall_matched = crawled_values.intersection(all_umls_terms)
    overall_coverage = len(overall_matched) / len(crawled_values) if crawled_values else 0
    
    results = {
        'field': field,
        'total_crawled_terms': len(crawled_values),
        'overall_coverage': overall_coverage,
        'overall_matched': len(overall_matched),
        'sab_coverage': coverage_results,
        'top_sab_only': top_sab_only
    }
    
    logger.info(f"UMLS coverage for {field}: {overall_coverage:.2%} overall coverage")
    
    return results