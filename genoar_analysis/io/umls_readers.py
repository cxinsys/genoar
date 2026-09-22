"""CSV-backed UMLS lookup."""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
import logging
import warnings

from ..core.data import GenoarData

logger = logging.getLogger(__name__)

class UMLSCSVReader:
    """Queries UMLS concepts out of the exported CSV files."""
    
    def __init__(self, data_dir: str = "all_query_results"):
        """
        Initialize UMLS CSV Reader
        
        Parameters:
            data_dir: Directory containing UMLS CSV files
        """
        self.data_dir = Path(data_dir)
        self.field_mappings = {
            'cell_type': 'umls_celltype_df.csv',
            'tissue': 'umls_tissue_df.csv', 
            'disease': 'umls_disease_df.csv',
            'disease_state': 'umls_disease_df.csv',
            'disease_state_modified': 'umls_disease_df.csv'
        }
        self._cache = {}
        
        # Preferred source vocabulary (SAB) order, carried over from the legacy scripts
        self.sab_priority_orders = {
            'disease': ['ICD10CM', 'ICD9CM', 'ICD10', 'SNOMEDCT_US', 'SNM', 'MSH', 'OMIM'],
            'cell_type': ['MSH', 'NCI', 'SNOMEDCT_US', 'SNM', 'MEDCIN', 'CHV', 'CPT', 'LCH_NW', 'LNC'],
            'tissue': ['MSH', 'SNOMEDCT_US', 'SNM', 'UWDA', 'FMA', 'NCI', 'AOD', 'CSP']
        }
    
    def load_umls_data(self, field_type: str) -> pd.DataFrame:
        """
        Load UMLS data for specific field type
        
        Parameters:
            field_type: Type of field ('cell_type', 'tissue', 'disease', etc.)
            
        Returns:
            DataFrame with UMLS data
        """
        normalized_field = self._normalize_field_type(field_type)
        
        if normalized_field in self._cache:
            logger.info(f"Using cached UMLS data for {normalized_field}")
            return self._cache[normalized_field]
        
        if normalized_field not in self.field_mappings:
            raise ValueError(f"Unsupported field type: {field_type}. "
                           f"Supported types: {list(self.field_mappings.keys())}")
        
        file_path = self.data_dir / self.field_mappings[normalized_field]
        
        if not file_path.exists():
            raise FileNotFoundError(f"UMLS data file not found: {file_path}")
        
        try:
            df = pd.read_csv(file_path)
            
            required_columns = ['CUI', 'STR', 'SAB', 'STY']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise ValueError(f"Missing required columns in {file_path}: {missing_columns}")
            
            # Clean data
            df = df.dropna(subset=['CUI', 'STR', 'SAB'])
            df['STR'] = df['STR'].astype(str).str.strip()
            
            logger.info(f"Loaded {len(df)} UMLS entries for {normalized_field}")
            self._cache[normalized_field] = df
            
            return df
            
        except Exception as e:
            logger.error(f"Error loading UMLS data from {file_path}: {e}")
            raise
    
    def _normalize_field_type(self, field_type: str) -> str:
        """Normalize field type to standard names"""
        field_map = {
            'disease_state': 'disease',
            'disease_state_modified': 'disease'
        }
        return field_map.get(field_type, field_type)
    
    def query_terms(self, terms: List[str], 
                   field_type: str,
                   case_sensitive: bool = False) -> pd.DataFrame:
        """
        Query terms against UMLS data
        
        Parameters:
            terms: List of terms to query
            field_type: Type of field
            case_sensitive: Whether to perform case-sensitive matching
            
        Returns:
            DataFrame with matching UMLS entries
        """
        if not terms:
            logger.warning("No terms provided for query")
            return pd.DataFrame()
        
        umls_df = self.load_umls_data(field_type)
        
        if not case_sensitive:
            # Create lowercase mapping for case-insensitive matching
            terms_lower = [str(term).lower().strip() for term in terms if pd.notna(term)]
            umls_df_lower = umls_df.copy()
            umls_df_lower['STR_lower'] = umls_df_lower['STR'].str.lower().str.strip()
            
            matched_df = umls_df_lower[umls_df_lower['STR_lower'].isin(terms_lower)]
            matched_df = matched_df.drop(columns=['STR_lower'])
        else:
            terms_clean = [str(term).strip() for term in terms if pd.notna(term)]
            matched_df = umls_df[umls_df['STR'].isin(terms_clean)]
        
        logger.info(f"Found {len(matched_df)} UMLS matches for {len(terms)} terms")
        
        return matched_df
    
    def query_single_term(self, term: str, 
                         field_type: str,
                         case_sensitive: bool = False) -> pd.DataFrame:
        """
        Query a single term against UMLS data
        
        Parameters:
            term: Term to query
            field_type: Type of field
            case_sensitive: Whether to perform case-sensitive matching
            
        Returns:
            DataFrame with matching UMLS entries
        """
        return self.query_terms([term], field_type, case_sensitive)
    
    def get_sab_priority_selection(self, df: pd.DataFrame, 
                                  field_type: str,
                                  custom_priority: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Select entries based on SAB priority
        
        Parameters:
            df: DataFrame with UMLS results
            field_type: Type of field to get default priority order
            custom_priority: Custom SAB priority order
            
        Returns:
            DataFrame with priority-selected entries
        """
        if df.empty:
            return df
        
        # Get priority order
        normalized_field = self._normalize_field_type(field_type)
        if custom_priority:
            priority_order = custom_priority
        else:
            priority_order = self.sab_priority_orders.get(normalized_field, [])
        
        if not priority_order:
            logger.warning(f"No SAB priority order defined for {field_type}, returning first entry per term")
            return df.groupby('STR').first().reset_index()
        
        def select_priority_sab(term, group):
            """Return the label of the group row whose SAB ranks highest"""
            for sab in priority_order:
                sab_matches = group[group['SAB'] == sab]
                if not sab_matches.empty:
                    if len(sab_matches) > 1:
                        # Multiple entries with same SAB, issue warning and take first
                        found_sabs = list(group['SAB'].unique())
                        warnings.warn(f"Multiple entries found for '{term}'. "
                                    f"Found SABs: {found_sabs}. Using entry with SAB: '{sab}'.")
                    return sab_matches.index[0]

            # No priority SAB found, return first entry
            return group.index[0]

        # Iterate the groups instead of using DataFrameGroupBy.apply: pandas >= 3.0
        # hands the callback a frame with the grouping column ('STR') removed, while
        # older versions keep it. Group frames obtained by iteration carry every
        # column on both, so the selection logic does not depend on the version.
        # Rows are picked by label and taken from the original frame, which also
        # preserves the input column order and dtypes.
        indexed = df.reset_index(drop=True)
        selected_labels = [
            select_priority_sab(term, group)
            for term, group in indexed.groupby('STR', sort=True)
        ]
        result = indexed.loc[selected_labels].reset_index(drop=True)

        logger.info(f"Selected {len(result)} priority entries from {len(df)} total matches")
        
        return result
    
    def match_field_to_umls(self, adata: GenoarData, 
                           field: str,
                           case_sensitive: bool = False,
                           use_sab_priority: bool = True,
                           store_results: bool = True) -> Dict[str, Any]:
        """
        Match a field from GenoarData to UMLS
        
        Parameters:
            adata: GenoarData object
            field: Field name to match
            case_sensitive: Whether to perform case-sensitive matching
            use_sab_priority: Whether to apply SAB priority selection
            store_results: Whether to store results in adata
            
        Returns:
            Dictionary with matching results
        """
        if adata.meta is None:
            raise ValueError("No META data loaded in GenoarData object")
        
        if field not in adata.meta.columns:
            raise ValueError(f"Field '{field}' not found in META data")
        
        unique_terms = adata.get_unique_values(field, dropna=True)
        
        if not unique_terms:
            logger.warning(f"No non-null values found in field '{field}'")
            return {
                'field': field,
                'total_terms': 0,
                'matched_terms': 0,
                'matching_rate': 0.0,
                'umls_results': pd.DataFrame()
            }
        
        umls_results = self.query_terms(unique_terms, field, case_sensitive)
        
        if use_sab_priority and not umls_results.empty:
            umls_results = self.get_sab_priority_selection(umls_results, field)
        
        # Calculate matching statistics
        total_terms = len(unique_terms)
        matched_terms_set = set(umls_results['STR'].unique()) if not umls_results.empty else set()
        
        if not case_sensitive:
            # For case-insensitive matching, compare lowercase versions
            unique_terms_lower = {str(term).lower().strip() for term in unique_terms}
            matched_terms_lower = {str(term).lower().strip() for term in matched_terms_set}
            actual_matched = len(unique_terms_lower.intersection(matched_terms_lower))
        else:
            unique_terms_clean = {str(term).strip() for term in unique_terms}
            matched_terms_clean = {str(term).strip() for term in matched_terms_set}
            actual_matched = len(unique_terms_clean.intersection(matched_terms_clean))
        
        matching_rate = actual_matched / total_terms if total_terms > 0 else 0.0
        
        results = {
            'field': field,
            'total_terms': total_terms,
            'matched_terms': actual_matched,
            'matching_rate': matching_rate,
            'umls_results': umls_results,
            'unique_terms': unique_terms,
            'matched_terms_list': list(matched_terms_set)
        }
        
        if store_results:
            if not hasattr(adata, 'umls_matching_results'):
                adata.umls_matching_results = {}
            adata.umls_matching_results[field] = results
            
            # Also store UMLS dataframe in adata for future use
            if adata.umls is None:
                adata.umls = umls_results
            else:
                # Append new results
                adata.umls = pd.concat([adata.umls, umls_results], ignore_index=True)
                adata.umls = adata.umls.drop_duplicates(['CUI', 'STR', 'SAB']).reset_index(drop=True)
        
        logger.info(f"UMLS matching for '{field}': {actual_matched}/{total_terms} terms matched "
                   f"({matching_rate:.2%})")
        
        return results
    
    def add_umls_annotations(self, adata: GenoarData, 
                           field: str,
                           umls_results: Optional[pd.DataFrame] = None) -> None:
        """
        Add UMLS annotations (CUI, SAB, STY) to the main dataframe
        
        Parameters:
            adata: GenoarData object
            field: Field that was matched
            umls_results: UMLS results dataframe (if None, will use stored results)
        """
        if adata.meta is None:
            raise ValueError("No META data loaded")
        
        if umls_results is None:
            if not hasattr(adata, 'umls_matching_results') or field not in adata.umls_matching_results:
                raise ValueError(f"No UMLS results found for field '{field}'. "
                               "Run match_field_to_umls first.")
            umls_results = adata.umls_matching_results[field]['umls_results']
        
        if umls_results.empty:
            logger.warning(f"No UMLS results to add for field '{field}'")
            return
        
        # Create mapping dictionary
        umls_mapping = umls_results.set_index('STR')[['CUI', 'SAB', 'STY']].to_dict('index')
        
        # Add UMLS columns
        cui_col = f'{field}_CUI'
        sab_col = f'{field}_SAB'
        sty_col = f'{field}_STY'
        
        def map_umls_info(term):
            if pd.isna(term):
                return pd.Series([np.nan, np.nan, np.nan])
            
            term_str = str(term).strip()
            if term_str in umls_mapping:
                info = umls_mapping[term_str]
                return pd.Series([info['CUI'], info['SAB'], info['STY']])
            else:
                return pd.Series([np.nan, np.nan, np.nan])
        
        umls_info = adata.meta[field].apply(map_umls_info)
        adata.meta[cui_col] = umls_info.iloc[:, 0]
        adata.meta[sab_col] = umls_info.iloc[:, 1] 
        adata.meta[sty_col] = umls_info.iloc[:, 2]
        
        logger.info(f"Added UMLS annotations for field '{field}': {cui_col}, {sab_col}, {sty_col}")
    
    def get_sab_distribution(self, umls_results: pd.DataFrame) -> Dict[str, int]:
        """
        Get distribution of SAB (Source Abbreviation) values
        
        Parameters:
            umls_results: UMLS results dataframe
            
        Returns:
            Dictionary with SAB counts
        """
        if umls_results.empty:
            return {}
        
        sab_counts = umls_results['SAB'].value_counts().to_dict()
        
        logger.info(f"SAB distribution: {sab_counts}")
        
        return sab_counts
    
    def get_sty_distribution(self, umls_results: pd.DataFrame) -> Dict[str, int]:
        """
        Get distribution of STY (Semantic Type) values
        
        Parameters:
            umls_results: UMLS results dataframe
            
        Returns:
            Dictionary with STY counts
        """
        if umls_results.empty:
            return {}
        
        sty_counts = umls_results['STY'].value_counts().to_dict()
        
        logger.info(f"STY distribution: {sty_counts}")
        
        return sty_counts


def load_umls_data(field_type: str, data_dir: str = "all_query_results") -> pd.DataFrame:
    """
    Convenience function to load UMLS data
    
    Parameters:
        field_type: Type of field ('cell_type', 'tissue', 'disease')
        data_dir: Directory containing UMLS CSV files
        
    Returns:
        DataFrame with UMLS data
    """
    reader = UMLSCSVReader(data_dir)
    return reader.load_umls_data(field_type)


def query_umls_terms(terms: List[str], 
                    field_type: str, 
                    data_dir: str = "all_query_results",
                    case_sensitive: bool = False,
                    use_sab_priority: bool = True) -> pd.DataFrame:
    """
    Convenience function to query UMLS terms
    
    Parameters:
        terms: List of terms to query
        field_type: Type of field
        data_dir: Directory containing UMLS CSV files
        case_sensitive: Whether to perform case-sensitive matching
        use_sab_priority: Whether to apply SAB priority selection
        
    Returns:
        DataFrame with matching UMLS entries
    """
    reader = UMLSCSVReader(data_dir)
    results = reader.query_terms(terms, field_type, case_sensitive)
    
    if use_sab_priority and not results.empty:
        results = reader.get_sab_priority_selection(results, field_type)
    
    return results