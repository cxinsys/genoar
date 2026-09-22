"""Core data structure for GENOAR analysis."""

import pandas as pd
from typing import Dict, Optional, Set, List, Any
import logging

logger = logging.getLogger(__name__)

class GenoarData:
    """
    Main data structure for GENOAR analysis, inspired by AnnData.
    
    Attributes:
        meta (pd.DataFrame): Crawled metadata from META files
        umls (pd.DataFrame): UMLS matching results
        series_info (Dict): Information about Series data
        matching_results (Dict): Results from matching analyses
        stats (Dict): Statistical summaries
        analysis_log (List): Log of analysis steps performed
    """
    
    def __init__(self, meta: Optional[pd.DataFrame] = None):
        self.meta = meta
        self.umls: Optional[pd.DataFrame] = None
        
        # Analysis results storage
        self.series_info: Dict[str, Any] = {}
        self.matching_results: Dict[str, Any] = {}
        self.stats: Dict[str, Any] = {}
        self.analysis_log: List[str] = []
        
        # Configuration
        self.field_mappings = {
            'cell_type': 'cell_type',
            'tissue': 'tissue',
            'disease_state': 'disease_state'
        }
        
        if meta is not None:
            self._initialize_series_info()
    
    def _initialize_series_info(self):
        """Initialize series information from meta data."""
        if self.meta is not None:
            self.series_info['total_series'] = len(self.meta['Series'].unique())
            self.series_info['total_samples'] = len(self.meta)
            
            # Count non-null values for each field
            for field in self.field_mappings.values():
                if field in self.meta.columns:
                    non_null_count = self.meta[field].notna().sum()
                    self.series_info[f'{field}_samples'] = non_null_count
                    self.series_info[f'{field}_series'] = len(
                        self.meta[self.meta[field].notna()]['Series'].unique()
                    )
    
    def log_analysis(self, description: str):
        """Log an analysis step."""
        self.analysis_log.append(description)
        logger.info(f"Analysis: {description}")
    
    def get_unique_values(self, field: str, dropna: bool = True) -> List[str]:
        """Get unique values for a specific field."""
        if self.meta is None or field not in self.meta.columns:
            return []
        
        if dropna:
            return self.meta[field].dropna().unique().tolist()
        else:
            return self.meta[field].unique().tolist()
    
    def get_series_with_field(self, field: str) -> Set[str]:
        """Get Series IDs that have non-null values for a specific field."""
        if self.meta is None or field not in self.meta.columns:
            return set()
        
        return set(self.meta[self.meta[field].notna()]['Series'].unique())
    
    def filter_by_series(self, series_ids: List[str]) -> 'GenoarData':
        """Create a new GenoarData object filtered by Series IDs."""
        if self.meta is None:
            return GenoarData()
        
        filtered_meta = self.meta[self.meta['Series'].isin(series_ids)].copy()
        new_adata = GenoarData(filtered_meta)
        
        # Copy relevant attributes
        new_adata.umls = self.umls
        new_adata.field_mappings = self.field_mappings.copy()
        
        return new_adata
    
    def summary(self) -> Dict[str, Any]:
        """Get a summary of the data."""
        summary = {
            'meta_loaded': self.meta is not None,
            'umls_loaded': self.umls is not None,
            'series_info': self.series_info.copy(),
            'analysis_steps': len(self.analysis_log)
        }
        
        if self.meta is not None:
            summary['meta_shape'] = self.meta.shape
            summary['meta_columns'] = list(self.meta.columns)
        
        
        if self.umls is not None:
            summary['umls_shape'] = self.umls.shape
        
        return summary
    
    def __repr__(self) -> str:
        """String representation of GenoarData."""
        summary = self.summary()
        
        lines = ["GenoarData object"]
        
        if summary['meta_loaded']:
            lines.append(f"  meta: {summary['meta_shape']} (samples × features)")
            lines.append(f"  series: {summary['series_info'].get('total_series', 0)}")
        
        
        if summary['umls_loaded']:
            lines.append(f"  umls: {summary['umls_shape']}")
        
        if summary['analysis_steps'] > 0:
            lines.append(f"  analysis_steps: {summary['analysis_steps']}")
        
        return "\n".join(lines)