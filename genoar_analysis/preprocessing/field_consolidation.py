"""Metadata field consolidation and cleanup."""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Union
import logging

from ..core.data import GenoarData

logger = logging.getLogger(__name__)

class FieldConsolidator:
    """Merges related metadata fields into a single canonical column."""
    
    def __init__(self):
        """Initialize FieldConsolidator"""
        self.default_mappings = {
            'disease_state_modified': [
                'disease', 'disease_state', 'diagnosis', 
                'disease_diagnosis', 'patient_diagnosis', 'health_status'
            ],
            'sex': ['sex', 'gender'],
            'age': ['age', 'Age', 'patient_age', 'age_at_diagnosis'],
            'treatment': ['treatment', 'therapy', 'drug_treatment', 'medication'],
            'cell_type': ['cell_type', 'cell type', 'celltype', 'cell_line'],
            'tissue': ['tissue', 'organ', 'sample_source', 'biopsy_site']
        }
    
    def create_disease_state_modified(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Create disease_state_modified field by consolidating multiple disease-related fields.
        
        This recreates the functionality from legacy scripts where multiple disease fields
        are combined using fillna chains.
        
        Parameters:
            df: Input DataFrame
            
        Returns:
            DataFrame with disease_state_modified column added
        """
        df = df.copy()
        
        disease_fields = self.default_mappings['disease_state_modified']
        
        # Check which fields exist in the DataFrame
        existing_fields = [field for field in disease_fields if field in df.columns]
        
        if not existing_fields:
            logger.warning("No disease-related fields found in DataFrame")
            df['disease_state_modified'] = np.nan
            return df
        
        # Start with the first existing field
        df['disease_state_modified'] = df[existing_fields[0]]
        
        # Chain fillna for remaining fields
        for field in existing_fields[1:]:
            df['disease_state_modified'] = df['disease_state_modified'].fillna(df[field])
        
        # Log statistics
        non_null_count = df['disease_state_modified'].notna().sum()
        total_count = len(df)
        logger.info(f"Created disease_state_modified: {non_null_count}/{total_count} "
                   f"non-null values from {len(existing_fields)} source fields")
        
        return df
    
    def consolidate_sex_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Consolidate sex/gender fields into a single 'sex' field.
        
        Parameters:
            df: Input DataFrame
            
        Returns:
            DataFrame with consolidated sex field
        """
        df = df.copy()
        
        sex_fields = self.default_mappings['sex']
        existing_fields = [field for field in sex_fields if field in df.columns]
        
        if not existing_fields:
            logger.warning("No sex/gender fields found in DataFrame")
            return df
        
        # If 'sex' doesn't exist, create it
        if 'sex' not in df.columns:
            df['sex'] = np.nan
        
        for field in existing_fields:
            if field != 'sex':
                df['sex'] = df['sex'].fillna(df[field])
        
        # Standardize values
        df['sex'] = df['sex'].str.lower().str.strip()
        df['sex'] = df['sex'].replace({
            'm': 'male', 'f': 'female',
            'man': 'male', 'woman': 'female',
            'male ': 'male', 'female ': 'female'
        })
        
        logger.info(f"Consolidated sex field from {len(existing_fields)} source fields")
        
        return df
    
    def consolidate_age_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Consolidate age-related fields into a single 'Age' field.
        
        Parameters:
            df: Input DataFrame
            
        Returns:
            DataFrame with consolidated Age field
        """
        df = df.copy()
        
        age_fields = self.default_mappings['age']
        existing_fields = [field for field in age_fields if field in df.columns]
        
        if not existing_fields:
            logger.warning("No age fields found in DataFrame")
            return df
        
        # Create or use 'Age' field
        if 'Age' not in df.columns:
            df['Age'] = np.nan
        
        for field in existing_fields:
            if field != 'Age':
                df['Age'] = df['Age'].fillna(df[field])
        
        logger.info(f"Consolidated age field from {len(existing_fields)} source fields")
        
        return df
    
    def consolidate_multiple_fields(self, 
                                   df: pd.DataFrame, 
                                   field_mappings: Optional[Dict[str, List[str]]] = None,
                                   use_defaults: bool = True) -> pd.DataFrame:
        """
        Consolidate multiple fields based on provided or default mappings.
        
        Parameters:
            df: Input DataFrame
            field_mappings: Custom field mappings {target_field: [source_fields]}
            use_defaults: Whether to use default mappings in addition to custom ones
            
        Returns:
            DataFrame with consolidated fields
        """
        df = df.copy()
        
        # Combine default and custom mappings
        if use_defaults:
            mappings = self.default_mappings.copy()
            if field_mappings:
                mappings.update(field_mappings)
        else:
            mappings = field_mappings or {}
        
        consolidation_summary = []
        
        for target_field, source_fields in mappings.items():
            existing_fields = [field for field in source_fields if field in df.columns]
            
            if not existing_fields:
                logger.debug(f"No source fields found for {target_field}")
                continue
            
            # Create target field if it doesn't exist
            if target_field not in df.columns:
                df[target_field] = np.nan
            
            # Start with the target field or first source field
            if target_field in existing_fields:
                # Target field exists in source fields, start with it
                other_fields = [f for f in existing_fields if f != target_field]
                for field in other_fields:
                    df[target_field] = df[target_field].fillna(df[field])
            else:
                # Target field doesn't exist in source fields
                df[target_field] = df[existing_fields[0]]
                for field in existing_fields[1:]:
                    df[target_field] = df[target_field].fillna(df[field])
            
            # Log consolidation
            non_null_count = df[target_field].notna().sum()
            consolidation_summary.append(
                f"{target_field}: {non_null_count} non-null from {existing_fields}"
            )
        
        if consolidation_summary:
            logger.info(f"Field consolidation complete:\n" + "\n".join(consolidation_summary))
        else:
            logger.warning("No fields were consolidated")
        
        return df
    
    def apply_to_genoar_data(self, 
                            adata: GenoarData,
                            field_mappings: Optional[Dict[str, List[str]]] = None,
                            use_defaults: bool = True) -> None:
        """
        Apply field consolidation to GenoarData object.
        
        Parameters:
            adata: GenoarData object
            field_mappings: Custom field mappings
            use_defaults: Whether to use default mappings
        """
        if adata.meta is None:
            raise ValueError("No META data loaded in GenoarData object")
        
        # Apply consolidation
        adata.meta = self.consolidate_multiple_fields(
            adata.meta, 
            field_mappings, 
            use_defaults
        )
        
        adata.analysis_log.append("Applied field consolidation")
        
        logger.info("Field consolidation applied to GenoarData object")


# Convenience functions
def consolidate_disease_fields(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience function to create disease_state_modified field.
    
    Parameters:
        df: Input DataFrame
        
    Returns:
        DataFrame with disease_state_modified column
    """
    consolidator = FieldConsolidator()
    return consolidator.create_disease_state_modified(df)


def consolidate_demographic_fields(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience function to consolidate demographic fields (sex, age).
    
    Parameters:
        df: Input DataFrame
        
    Returns:
        DataFrame with consolidated demographic fields
    """
    consolidator = FieldConsolidator()
    df = consolidator.consolidate_sex_fields(df)
    df = consolidator.consolidate_age_fields(df)
    return df


def consolidate_all_fields(df: pd.DataFrame, 
                          custom_mappings: Optional[Dict[str, List[str]]] = None) -> pd.DataFrame:
    """
    Convenience function to apply all field consolidations.
    
    Parameters:
        df: Input DataFrame
        custom_mappings: Optional custom field mappings
        
    Returns:
        DataFrame with all fields consolidated
    """
    consolidator = FieldConsolidator()
    return consolidator.consolidate_multiple_fields(df, custom_mappings, use_defaults=True)


def create_disease_state_modified(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standalone convenience function to create disease_state_modified field.
    
    Parameters:
        df: Input DataFrame
        
    Returns:
        DataFrame with disease_state_modified column
    """
    consolidator = FieldConsolidator()
    return consolidator.create_disease_state_modified(df)