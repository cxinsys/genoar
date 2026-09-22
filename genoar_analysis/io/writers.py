"""Data writing functions for GENOAR analysis."""

import json
import pandas as pd
from pathlib import Path
from typing import Union, Dict, Any
import logging

from ..core.data import GenoarData

logger = logging.getLogger(__name__)

def write_analysis_results(
    adata: GenoarData,
    output_dir: Union[str, Path],
    include_raw_data: bool = False
) -> None:
    """
    Write complete analysis results to files.
    
    Parameters:
        adata: GenoarData object with analysis results
        output_dir: Output directory
        include_raw_data: Whether to include raw data files
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Write analysis summary
    summary = adata.summary()
    with open(output_dir / 'analysis_summary.json', 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    
    with open(output_dir / 'analysis_log.txt', 'w') as f:
        for step in adata.analysis_log:
            f.write(f"{step}\n")
    
    if adata.matching_results:
        for field, results in adata.matching_results.items():
            filename = f'matching_results_{field}.json'
            with open(output_dir / filename, 'w') as f:
                json.dump(results, f, indent=2, default=str)
    
    # Write statistics if available
    if adata.stats:
        with open(output_dir / 'statistics.json', 'w') as f:
            json.dump(adata.stats, f, indent=2, default=str)
    
    if include_raw_data:
        if adata.meta is not None:
            adata.meta.to_csv(output_dir / 'meta_data.csv', index=False)
        
        
        if adata.umls is not None:
            adata.umls.to_csv(output_dir / 'umls_data.csv', index=False)
    
    logger.info(f"Analysis results written to {output_dir}")

def export_matching_results(
    adata: GenoarData,
    field: str,
    output_path: Union[str, Path],
    format: str = 'csv'
) -> None:
    """
    Export matching results for a specific field.
    
    Parameters:
        adata: GenoarData object
        field: Field name ('cell_type', 'tissue', etc.)
        output_path: Output file path
        format: Output format ('csv', 'json', 'excel')
    """
    if field not in adata.matching_results:
        raise ValueError(f"No matching results found for field: {field}")
    
    results = adata.matching_results[field]
    output_path = Path(output_path)
    
    if format == 'csv':
        # Convert to DataFrame if possible
        if isinstance(results, dict) and 'intersection_data' in results:
            df = pd.DataFrame(results['intersection_data'])
            df.to_csv(output_path, index=False)
        else:
            # Fallback to JSON for complex structures
            with open(output_path.with_suffix('.json'), 'w') as f:
                json.dump(results, f, indent=2, default=str)
    
    elif format == 'json':
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
    
    elif format == 'excel':
        if isinstance(results, dict) and 'intersection_data' in results:
            df = pd.DataFrame(results['intersection_data'])
            df.to_excel(output_path, index=False)
        else:
            raise ValueError("Excel format requires DataFrame-compatible data")
    
    else:
        raise ValueError(f"Unsupported format: {format}")
    
    logger.info(f"Matching results for {field} exported to {output_path}")

def export_series_lists(
    adata: GenoarData,
    output_dir: Union[str, Path],
    field: str
) -> None:
    """
    Export Series ID lists for different categories.
    
    Parameters:
        adata: GenoarData object
        output_dir: Output directory
        field: Field name to analyze
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    series_with_field = adata.get_series_with_field(field)
    
    with open(output_dir / f'series_with_{field}.txt', 'w') as f:
        for series_id in sorted(series_with_field):
            f.write(f"{series_id}\n")
    
    
    logger.info(f"Series lists for {field} exported to {output_dir}")

def create_analysis_report(
    adata: GenoarData,
    output_path: Union[str, Path],
    field: str
) -> None:
    """
    Create a comprehensive analysis report.
    
    Parameters:
        adata: GenoarData object
        output_path: Output file path
        field: Field to focus the report on
    """
    output_path = Path(output_path)
    
    report_lines = []
    report_lines.append(f"# GENOAR Analysis Report - {field.title()}")
    report_lines.append("=" * 50)
    report_lines.append("")
    
    summary = adata.summary()
    report_lines.append("## Data Summary")
    report_lines.append(f"- META files loaded: {summary['meta_loaded']}")
    report_lines.append(f"- UMLS data loaded: {summary['umls_loaded']}")
    
    if 'meta_shape' in summary:
        report_lines.append(f"- Total samples: {summary['meta_shape'][0]}")
        report_lines.append(f"- Total series: {summary['series_info'].get('total_series', 'N/A')}")
    
    report_lines.append("")
    
    # Field-specific analysis
    if field in adata.matching_results:
        results = adata.matching_results[field]
        report_lines.append(f"## {field.title()} Analysis")
        
        if 'unique_values' in results:
            report_lines.append(f"- Unique {field} values: {len(results['unique_values'])}")
        
        
        if 'umls_matching' in results:
            umls_stats = results['umls_matching']
            report_lines.append(f"- UMLS matched terms: {umls_stats.get('matched_count', 'N/A')}")
            report_lines.append(f"- UMLS unmatched terms: {umls_stats.get('unmatched_count', 'N/A')}")
    
    report_lines.append("")
    
    report_lines.append("## Analysis Steps")
    for i, step in enumerate(adata.analysis_log, 1):
        report_lines.append(f"{i}. {step}")
    
    # Write report
    with open(output_path, 'w') as f:
        f.write("\n".join(report_lines))
    
    logger.info(f"Analysis report created: {output_path}")