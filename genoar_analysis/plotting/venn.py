"""Venn diagram rendering."""

import matplotlib.pyplot as plt
from matplotlib_venn import venn2, venn3
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Set, Tuple, Union
import logging

from ..core.data import GenoarData

logger = logging.getLogger(__name__)

class VennDiagramPlotter:
    """Draws 2- and 3-set Venn diagrams."""
    
    def __init__(self):
        """Initialize VennDiagramPlotter"""
        self.default_colors = ['#66b3ff', '#99ff99', '#ffcc99']
        self.default_figsize = (10, 10)
    
    def plot_series_overlap(self, 
                           set1: Set[str],
                           set2: Set[str],
                           set3: Optional[Set[str]] = None,
                           labels: List[str] = None,
                           title: str = None,
                           figsize: Tuple[int, int] = None,
                           colors: List[str] = None,
                           save_path: Optional[str] = None) -> None:
        """
        Create Venn diagram for series overlap analysis
        
        Parameters:
            set1: First set of series
            set2: Second set of series  
            set3: Third set of series (optional, for 3-way Venn)
            labels: Labels for the sets
            title: Plot title
            figsize: Figure size
            colors: Colors for the sets
            save_path: Path to save the figure
        """
        if figsize is None:
            figsize = self.default_figsize
        
        if colors is None:
            colors = self.default_colors
        
        plt.figure(figsize=figsize)
        
        # Create appropriate Venn diagram based on number of sets
        if set3 is None:
            # 2-way Venn diagram
            if labels is None:
                labels = [f'Set 1 ({len(set1)})', f'Set 2 ({len(set2)})']
            elif len(labels) < 2:
                labels.extend([f'Set {i+1}' for i in range(len(labels), 2)])
            
            venn_diagram = venn2([set1, set2], set_labels=labels)
            
        else:
            # 3-way Venn diagram
            if labels is None:
                labels = [f'Set 1 ({len(set1)})', f'Set 2 ({len(set2)})', f'Set 3 ({len(set3)})']
            elif len(labels) < 3:
                labels.extend([f'Set {i+1}' for i in range(len(labels), 3)])
            
            venn_diagram = venn3([set1, set2, set3], set_labels=labels)
        
        # Customize colors if diagram was created successfully
        if venn_diagram is not None:
            for i, patch in enumerate(venn_diagram.patches):
                if patch is not None and i < len(colors):
                    patch.set_facecolor(colors[i])
                    patch.set_alpha(0.7)
        
        if title:
            plt.title(title, fontsize=14, fontweight='bold')
        
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path, bbox_inches='tight', dpi=300)
            logger.info(f"Venn diagram saved to {save_path}")
        
        plt.show()
    
    
    def plot_field_comparison_venn(self,
                                  adata: GenoarData,
                                  field1: str,
                                  field2: str,
                                  title: str = None,
                                  figsize: Tuple[int, int] = None,
                                  save_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Create Venn diagram comparing two fields
        
        Parameters:
            adata: GenoarData object
            field1: First field to compare
            field2: Second field to compare
            title: Plot title
            figsize: Figure size
            save_path: Path to save the figure
            
        Returns:
            Dictionary with comparison results
        """
        if adata.meta is None:
            raise ValueError("No META data loaded")
        
        field1_series = adata.get_series_with_field(field1) if field1 in adata.meta.columns else set()
        field2_series = adata.get_series_with_field(field2) if field2 in adata.meta.columns else set()
        
        labels = [
            f'{field1.title()} Series\\n({len(field1_series)})',
            f'{field2.title()} Series\\n({len(field2_series)})'
        ]
        
        if title is None:
            title = f"Field Comparison - {field1.title()} vs {field2.title()}"
        
        # Create 2-way Venn diagram
        self.plot_series_overlap(
            field1_series, field2_series,
            labels=labels, title=title, figsize=figsize, save_path=save_path
        )
        
        # Calculate intersection statistics
        intersection = field1_series.intersection(field2_series)
        union = field1_series.union(field2_series)
        
        results = {
            'field1_series': field1_series,
            'field2_series': field2_series,
            'intersection': intersection,
            'union': union,
            'statistics': {
                'field1_count': len(field1_series),
                'field2_count': len(field2_series),
                'intersection_count': len(intersection),
                'union_count': len(union),
                'jaccard_index': len(intersection) / len(union) if union else 0,
                'field1_coverage': len(intersection) / len(field1_series) if field1_series else 0,
                'field2_coverage': len(intersection) / len(field2_series) if field2_series else 0
            }
        }
        
        logger.info(f"Field comparison '{field1}' vs '{field2}':")
        logger.info(f"  {field1} series: {len(field1_series)}")
        logger.info(f"  {field2} series: {len(field2_series)}")
        logger.info(f"  Intersection: {len(intersection)}")
        logger.info(f"  Jaccard index: {results['statistics']['jaccard_index']:.3f}")
        
        return results
    
    def plot_multiple_fields_venn(self,
                                 adata: GenoarData,
                                 fields: List[str],
                                 title: str = None,
                                 figsize: Tuple[int, int] = None,
                                 save_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Create Venn diagram for multiple fields (up to 3)
        
        Parameters:
            adata: GenoarData object
            fields: List of fields to compare (2 or 3 fields)
            title: Plot title
            figsize: Figure size
            save_path: Path to save the figure
            
        Returns:
            Dictionary with comparison results
        """
        if len(fields) < 2 or len(fields) > 3:
            raise ValueError("Can only compare 2 or 3 fields")
        
        if adata.meta is None:
            raise ValueError("No META data loaded")
        
        field_series = []
        labels = []
        
        for field in fields:
            if field in adata.meta.columns:
                series_set = adata.get_series_with_field(field)
            else:
                logger.warning(f"Field '{field}' not found in META data, using empty set")
                series_set = set()
            
            field_series.append(series_set)
            labels.append(f'{field.title()} Series\\n({len(series_set)})')
        
        if title is None:
            title = f"Multi-Field Comparison - {', '.join([f.title() for f in fields])}"
        
        # Create Venn diagram
        if len(fields) == 2:
            self.plot_series_overlap(
                field_series[0], field_series[1],
                labels=labels, title=title, figsize=figsize, save_path=save_path
            )
        else:  # len(fields) == 3
            self.plot_series_overlap(
                field_series[0], field_series[1], field_series[2],
                labels=labels, title=title, figsize=figsize, save_path=save_path
            )
        
        # Calculate statistics
        results = {
            'fields': fields,
            'field_series': {fields[i]: field_series[i] for i in range(len(fields))},
            'statistics': {}
        }
        
        # Add field counts
        for i, field in enumerate(fields):
            results['statistics'][f'{field}_count'] = len(field_series[i])
        
        # Add pairwise intersections
        for i in range(len(fields)):
            for j in range(i+1, len(fields)):
                intersection = field_series[i].intersection(field_series[j])
                key = f'{fields[i]}_{fields[j]}_intersection'
                results['statistics'][key] = len(intersection)
                results[key] = intersection
        
        # Add three-way intersection if applicable
        if len(fields) == 3:
            three_way_intersection = field_series[0].intersection(field_series[1]).intersection(field_series[2])
            results['three_way_intersection'] = three_way_intersection
            results['statistics']['three_way_intersection_count'] = len(three_way_intersection)
        
        logger.info(f"Multi-field comparison for {fields}:")
        for field, series_set in results['field_series'].items():
            logger.info(f"  {field} series: {len(series_set)}")
        
        return results
    
    def plot_sab_distribution_venn(self,
                                  umls_results: pd.DataFrame,
                                  sab_list: List[str],
                                  title: str = None,
                                  figsize: Tuple[int, int] = None,
                                  save_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Create Venn diagram showing SAB distribution overlap
        
        Parameters:
            umls_results: UMLS results DataFrame
            sab_list: List of SAB values to compare (2 or 3)
            title: Plot title
            figsize: Figure size
            save_path: Path to save the figure
            
        Returns:
            Dictionary with SAB overlap results
        """
        if len(sab_list) < 2 or len(sab_list) > 3:
            raise ValueError("Can only compare 2 or 3 SAB values")
        
        if umls_results.empty:
            raise ValueError("UMLS results DataFrame is empty")
        
        if 'SAB' not in umls_results.columns:
            raise ValueError("No 'SAB' column found in UMLS results")
        
        sab_terms = []
        labels = []
        
        for sab in sab_list:
            sab_df = umls_results[umls_results['SAB'] == sab]
            terms_set = set(sab_df['STR'].unique()) if not sab_df.empty else set()
            sab_terms.append(terms_set)
            labels.append(f'{sab}\\n({len(terms_set)} terms)')
        
        if title is None:
            title = f"SAB Distribution Overlap - {', '.join(sab_list)}"
        
        # Create Venn diagram
        if len(sab_list) == 2:
            self.plot_series_overlap(
                sab_terms[0], sab_terms[1],
                labels=labels, title=title, figsize=figsize, save_path=save_path
            )
        else:  # len(sab_list) == 3
            self.plot_series_overlap(
                sab_terms[0], sab_terms[1], sab_terms[2],
                labels=labels, title=title, figsize=figsize, save_path=save_path
            )
        
        # Calculate statistics
        results = {
            'sab_list': sab_list,
            'sab_terms': {sab_list[i]: sab_terms[i] for i in range(len(sab_list))},
            'statistics': {}
        }
        
        # Add SAB counts
        for i, sab in enumerate(sab_list):
            results['statistics'][f'{sab}_count'] = len(sab_terms[i])
        
        # Add pairwise intersections
        for i in range(len(sab_list)):
            for j in range(i+1, len(sab_list)):
                intersection = sab_terms[i].intersection(sab_terms[j])
                key = f'{sab_list[i]}_{sab_list[j]}_intersection'
                results['statistics'][key] = len(intersection)
                results[key] = intersection
        
        # Add three-way intersection if applicable
        if len(sab_list) == 3:
            three_way_intersection = sab_terms[0].intersection(sab_terms[1]).intersection(sab_terms[2])
            results['three_way_intersection'] = three_way_intersection
            results['statistics']['three_way_intersection_count'] = len(three_way_intersection)
        
        logger.info(f"SAB distribution comparison for {sab_list}:")
        for sab, terms_set in results['sab_terms'].items():
            logger.info(f"  {sab} terms: {len(terms_set)}")
        
        return results


# Convenience functions for easy usage


def plot_field_comparison(adata: GenoarData,
                         field1: str,
                         field2: str,
                         title: str = None,
                         figsize: Tuple[int, int] = None,
                         save_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Convenience function to create field comparison Venn diagram
    
    Parameters:
        adata: GenoarData object
        field1: First field to compare
        field2: Second field to compare
        title: Plot title
        figsize: Figure size
        save_path: Path to save the figure
        
    Returns:
        Dictionary with comparison results
    """
    plotter = VennDiagramPlotter()
    return plotter.plot_field_comparison_venn(adata, field1, field2, title, figsize, save_path)


def plot_multiple_fields(adata: GenoarData,
                        fields: List[str],
                        title: str = None,
                        figsize: Tuple[int, int] = None,
                        save_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Convenience function to create multi-field Venn diagram
    
    Parameters:
        adata: GenoarData object
        fields: List of fields to compare (2 or 3 fields)
        title: Plot title
        figsize: Figure size
        save_path: Path to save the figure
        
    Returns:
        Dictionary with comparison results
    """
    plotter = VennDiagramPlotter()
    return plotter.plot_multiple_fields_venn(adata, fields, title, figsize, save_path)