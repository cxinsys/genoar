"""Statistical analysis functions for GENOAR."""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple, Optional
import logging
from scipy import stats

from ..core.data import GenoarData
from ..core.config import Config

logger = logging.getLogger(__name__)

def rank_values(
    data: pd.Series,
    by: str = 'frequency',
    top_n: int = 20,
    ascending: bool = False
) -> pd.Series:
    """
    Rank values by frequency or alphabetically.
    
    Parameters:
        data: Pandas Series to rank
        by: Ranking method ('frequency' or 'alphabetical')
        top_n: Number of top values to return
        ascending: Sort order
    
    Returns:
        Ranked Series
    """
    if by == 'frequency':
        ranked = data.value_counts(ascending=ascending)
    elif by == 'alphabetical':
        ranked = data.value_counts().sort_index(ascending=ascending)
    else:
        raise ValueError(f"Unsupported ranking method: {by}")
    
    return ranked.head(top_n)

def calculate_coverage(
    adata: GenoarData,
    field: str,
    reference_data: Optional[pd.DataFrame] = None
) -> Dict[str, Any]:
    """
    Calculate coverage statistics for a field.
    
    Parameters:
        adata: GenoarData object
        field: Field to analyze
        reference_data: Optional reference data for comparison
    
    Returns:
        Dictionary with coverage statistics
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if field not in adata.meta.columns:
        raise ValueError(f"Field '{field}' not found in META data")
    
    # Basic coverage statistics
    total_samples = len(adata.meta)
    field_samples = adata.meta[field].notna().sum()
    coverage_rate = field_samples / total_samples if total_samples > 0 else 0
    
    # Series-level coverage
    total_series = len(adata.meta['Series'].unique())
    series_with_field = len(adata.get_series_with_field(field))
    series_coverage_rate = series_with_field / total_series if total_series > 0 else 0
    
    unique_values = adata.get_unique_values(field, dropna=True)
    unique_count = len(unique_values)
    
    results = {
        'field': field,
        'total_samples': total_samples,
        'field_samples': field_samples,
        'coverage_rate': coverage_rate,
        'total_series': total_series,
        'series_with_field': series_with_field,
        'series_coverage_rate': series_coverage_rate,
        'unique_values': unique_values,
        'unique_count': unique_count,
        'diversity_index': unique_count / field_samples if field_samples > 0 else 0
    }
    
    if reference_data is not None and field in reference_data.columns:
        ref_unique = set(reference_data[field].dropna().unique())
        crawled_unique = set(unique_values)
        
        intersection = crawled_unique.intersection(ref_unique)
        union = crawled_unique.union(ref_unique)
        
        results['reference_comparison'] = {
            'reference_unique_count': len(ref_unique),
            'intersection_count': len(intersection),
            'union_count': len(union),
            'jaccard_index': len(intersection) / len(union) if union else 0,
            'precision': len(intersection) / len(crawled_unique) if crawled_unique else 0,
            'recall': len(intersection) / len(ref_unique) if ref_unique else 0
        }
    
    logger.info(f"Coverage for {field}: {coverage_rate:.2%} samples, {series_coverage_rate:.2%} series")
    
    return results

def compare_datasets(
    adata: GenoarData,
    reference_data: pd.DataFrame,
    field: str,
    metric: str = 'overlap'
) -> Dict[str, Any]:
    """
    Compare datasets using various metrics.
    
    Parameters:
        adata: GenoarData object
        reference_data: Reference dataset
        field: Field to compare
        metric: Comparison metric ('overlap', 'similarity', 'diversity')
    
    Returns:
        Dictionary with comparison results
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if field not in adata.meta.columns:
        raise ValueError(f"Field '{field}' not found in META data")
    
    if field not in reference_data.columns:
        raise ValueError(f"Field '{field}' not found in reference data")
    
    crawled_values = set(adata.get_unique_values(field, dropna=True))
    reference_values = set(reference_data[field].dropna().unique())
    
    # Calculate basic overlap metrics
    intersection = crawled_values.intersection(reference_values)
    union = crawled_values.union(reference_values)
    
    overlap_metrics = {
        'crawled_count': len(crawled_values),
        'reference_count': len(reference_values),
        'intersection_count': len(intersection),
        'union_count': len(union),
        'jaccard_index': len(intersection) / len(union) if union else 0,
        'dice_coefficient': 2 * len(intersection) / (len(crawled_values) + len(reference_values)),
        'precision': len(intersection) / len(crawled_values) if crawled_values else 0,
        'recall': len(intersection) / len(reference_values) if reference_values else 0
    }
    
    # Calculate F1 score
    precision = overlap_metrics['precision']
    recall = overlap_metrics['recall']
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    overlap_metrics['f1_score'] = f1_score
    
    results = {
        'field': field,
        'metric': metric,
        'overlap_metrics': overlap_metrics,
        'crawled_only': list(crawled_values - reference_values),
        'reference_only': list(reference_values - crawled_values),
        'common_values': list(intersection)
    }
    
    # Additional metrics based on requested metric type
    if metric == 'similarity':
        results['similarity_metrics'] = calculate_similarity_metrics(
            crawled_values, reference_values
        )
    elif metric == 'diversity':
        results['diversity_metrics'] = calculate_diversity_metrics(
            adata.meta[field].dropna(), reference_data[field].dropna()
        )
    
    logger.info(f"Dataset comparison for {field}: Jaccard={overlap_metrics['jaccard_index']:.3f}, F1={f1_score:.3f}")
    
    return results

def calculate_similarity_metrics(
    set1: set,
    set2: set
) -> Dict[str, float]:
    """Calculate various similarity metrics between two sets."""
    if not set1 or not set2:
        return {
            'cosine_similarity': 0.0,
            'overlap_coefficient': 0.0,
            'tanimoto_coefficient': 0.0
        }
    
    intersection = set1.intersection(set2)
    union = set1.union(set2)
    
    # Cosine similarity (treating sets as binary vectors)
    cosine_sim = len(intersection) / np.sqrt(len(set1) * len(set2))
    
    # Overlap coefficient (Szymkiewicz–Simpson coefficient)
    overlap_coef = len(intersection) / min(len(set1), len(set2))
    
    # Tanimoto coefficient (same as Jaccard for sets)
    tanimoto_coef = len(intersection) / len(union)
    
    return {
        'cosine_similarity': cosine_sim,
        'overlap_coefficient': overlap_coef,
        'tanimoto_coefficient': tanimoto_coef
    }

def calculate_diversity_metrics(
    series1: pd.Series,
    series2: pd.Series
) -> Dict[str, float]:
    """Calculate diversity metrics for two series."""
    def shannon_entropy(series):
        counts = series.value_counts()
        probs = counts / counts.sum()
        return -np.sum(probs * np.log2(probs))
    
    def simpson_diversity(series):
        counts = series.value_counts()
        n = counts.sum()
        return 1 - np.sum((counts * (counts - 1)) / (n * (n - 1)))
    
    entropy1 = shannon_entropy(series1)
    entropy2 = shannon_entropy(series2)
    
    simpson1 = simpson_diversity(series1)
    simpson2 = simpson_diversity(series2)
    
    return {
        'shannon_entropy_1': entropy1,
        'shannon_entropy_2': entropy2,
        'entropy_ratio': entropy1 / entropy2 if entropy2 > 0 else 0,
        'simpson_diversity_1': simpson1,
        'simpson_diversity_2': simpson2,
        'diversity_ratio': simpson1 / simpson2 if simpson2 > 0 else 0
    }

def calculate_distribution_stats(
    adata: GenoarData,
    field: str,
    percentiles: List[float] = [25, 50, 75, 90, 95]
) -> Dict[str, Any]:
    """
    Calculate distribution statistics for a field.
    
    Parameters:
        adata: GenoarData object
        field: Field to analyze
        percentiles: Percentiles to calculate
    
    Returns:
        Dictionary with distribution statistics
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    if field not in adata.meta.columns:
        raise ValueError(f"Field '{field}' not found in META data")
    
    value_counts = adata.meta[field].value_counts()
    
    # Basic statistics
    stats_dict = {
        'field': field,
        'total_values': len(adata.meta[field].dropna()),
        'unique_values': len(value_counts),
        'most_common': value_counts.index[0] if len(value_counts) > 0 else None,
        'most_common_count': value_counts.iloc[0] if len(value_counts) > 0 else 0,
        'least_common': value_counts.index[-1] if len(value_counts) > 0 else None,
        'least_common_count': value_counts.iloc[-1] if len(value_counts) > 0 else 0
    }
    
    # Frequency distribution statistics
    frequencies = value_counts.values
    if len(frequencies) > 0:
        stats_dict.update({
            'frequency_mean': np.mean(frequencies),
            'frequency_std': np.std(frequencies),
            'frequency_min': np.min(frequencies),
            'frequency_max': np.max(frequencies),
            'frequency_percentiles': {
                f'p{p}': np.percentile(frequencies, p) for p in percentiles
            }
        })
    
    # Concentration metrics
    if len(frequencies) > 1:
        # Gini coefficient
        sorted_freq = np.sort(frequencies)
        n = len(sorted_freq)
        cumsum = np.cumsum(sorted_freq)
        gini = (n + 1 - 2 * np.sum(cumsum) / cumsum[-1]) / n
        
        # Concentration ratio (top 10% share)
        top_10_pct = max(1, int(0.1 * len(frequencies)))
        concentration_ratio = np.sum(frequencies[:top_10_pct]) / np.sum(frequencies)
        
        stats_dict.update({
            'gini_coefficient': gini,
            'concentration_ratio_top10': concentration_ratio,
            'herfindahl_index': np.sum((frequencies / np.sum(frequencies)) ** 2)
        })
    
    logger.info(f"Distribution stats for {field}: {stats_dict['unique_values']} unique values")
    
    return stats_dict

def calculate_field_correlations(
    adata: GenoarData,
    fields: List[str],
    method: str = 'jaccard'
) -> Dict[str, Any]:
    """
    Calculate correlations between fields based on Series overlap.
    
    Parameters:
        adata: GenoarData object
        fields: List of fields to correlate
        method: Correlation method ('jaccard', 'dice', 'cosine')
    
    Returns:
        Dictionary with correlation matrix and statistics
    """
    if adata.meta is None:
        raise ValueError("No META data loaded")
    
    # Check if all fields exist
    missing_fields = [f for f in fields if f not in adata.meta.columns]
    if missing_fields:
        raise ValueError(f"Fields not found: {missing_fields}")
    
    field_series = {}
    for field in fields:
        field_series[field] = adata.get_series_with_field(field)
    
    # Calculate pairwise correlations
    correlation_matrix = {}
    
    for i, field1 in enumerate(fields):
        correlation_matrix[field1] = {}
        for j, field2 in enumerate(fields):
            if i == j:
                correlation_matrix[field1][field2] = 1.0
            else:
                set1 = field_series[field1]
                set2 = field_series[field2]
                
                if method == 'jaccard':
                    intersection = len(set1.intersection(set2))
                    union = len(set1.union(set2))
                    correlation = intersection / union if union > 0 else 0
                elif method == 'dice':
                    intersection = len(set1.intersection(set2))
                    correlation = 2 * intersection / (len(set1) + len(set2))
                elif method == 'cosine':
                    intersection = len(set1.intersection(set2))
                    correlation = intersection / np.sqrt(len(set1) * len(set2)) if set1 and set2 else 0
                else:
                    raise ValueError(f"Unsupported correlation method: {method}")
                
                correlation_matrix[field1][field2] = correlation
    
    # Convert to DataFrame for easier handling
    correlation_df = pd.DataFrame(correlation_matrix)
    
    results = {
        'fields': fields,
        'method': method,
        'correlation_matrix': correlation_matrix,
        'correlation_df': correlation_df,
        'field_series_counts': {field: len(series) for field, series in field_series.items()}
    }
    
    logger.info(f"Field correlations calculated using {method} method for {len(fields)} fields")
    
    return results