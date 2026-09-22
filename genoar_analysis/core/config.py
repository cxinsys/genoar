"""Configuration settings for GENOAR analysis."""

from typing import Dict, List
from pathlib import Path
import os

class Config:
    """Configuration settings for GENOAR analysis."""

    # Fixed UMLS data directory (relative to project root)
    # This data is constant and should not be changed
    _UMLS_DIR_NAME = 'all_query_results'

    # Default file patterns
    META_FILE_PATTERN = "*_meta.txt"
    
    # Default filtering criteria
    DEFAULT_ORGANISM = "Homo sapiens"
    DEFAULT_LIBRARY_SOURCE = "TRANSCRIPTOMIC"
    
    # UMLS column mappings
    UMLS_COLUMNS = ['CUI', 'STR', 'SAB', 'STY']
    
    SUPPORTED_FIELDS = ['cell_type', 'tissue', 'disease_state']
    
    # Default visualization parameters
    VIZ_PARAMS = {
        'figure_size': (10, 8),
        'top_n_default': 20,
        'colors': {
            'crawled': '#ff9999',
            'matched': '#90CBFB',
            'unmatched': '#ff9999'
        }
    }
    
    
    @classmethod
    def get_umls_file_mapping(cls) -> Dict[str, str]:
        """Get mapping between fields and UMLS CSV files."""
        return {
            'cell_type': 'umls_celltype_df.csv',
            'tissue': 'umls_tissue_df.csv',
            'disease_state': 'umls_disease_df.csv'
        }
    
    @classmethod
    def validate_field(cls, field: str) -> bool:
        """Validate if a field is supported for analysis."""
        return field in cls.SUPPORTED_FIELDS
    
    @classmethod
    def get_project_root(cls) -> Path:
        """
        Get project root directory.

        Walks up from this file's location looking for project markers.
        """
        current = Path(__file__).resolve().parent
        for _ in range(5):
            if (current / 'README.md').exists() or \
               (current / '.git').exists() or \
               (current / 'pyproject.toml').exists():
                return current
            current = current.parent
        # Fallback: 3 levels up (core -> genoar_analysis -> genoar)
        return Path(__file__).resolve().parent.parent.parent

    @classmethod
    def get_umls_dir(cls) -> str:
        """
        Get UMLS data directory (FIXED path).

        UMLS data is constant and always located at {project_root}/all_query_results/
        This path is NOT configurable via environment variable.

        Returns:
            Absolute path to UMLS data directory
        """
        return str(cls.get_project_root() / cls._UMLS_DIR_NAME)

    @classmethod
    def get_default_paths(cls) -> Dict[str, str]:
        """
        Get default file paths.

        Path configuration:
        - meta_dir: Configurable via GENOAR_META_DIR env var
        - umls_dir: FIXED to {project_root}/all_query_results/ (not configurable)
        - output_dir: Configurable via GENOAR_OUTPUT_DIR env var

        Returns:
            Dictionary with 'meta_dir', 'umls_dir', 'output_dir' paths
        """
        return {
            'meta_dir': os.getenv('GENOAR_META_DIR', 'sample_crawl_output/META'),
            'umls_dir': cls.get_umls_dir(),  # FIXED path, not from env var
            'output_dir': os.getenv('GENOAR_OUTPUT_DIR', 'analysis_output')
        }