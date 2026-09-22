#!/usr/bin/env python3
"""
GENOAR Data Integration Script
Analyzes and integrates crawled data from crawl_output/ directory
"""

import os
import sys
import json
import gzip
import pandas as pd
from pathlib import Path
from datetime import datetime
import logging
from collections import Counter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'integration_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DataIntegrator:
    def __init__(self, crawl_output_dir='crawl_output'):
        self.crawl_output_dir = Path(crawl_output_dir)
        self.smtx_dir = self.crawl_output_dir / 'SMTX'
        self.srr_dir = self.crawl_output_dir / 'SRR'
        self.meta_dir = self.crawl_output_dir / 'META'
        
        # Single-cell keywords (from genoar_crawler.py)
        self.keywords = [
            'chromium', '10x', 'cell ranger', 'single-cell rna', 
            '.h5', 'barcodes.tsv.gz', 'features.tsv.gz', 'matrix.mtx.gz'
        ]
        
        # Target rows for keyword search (from genoar_crawler.py)
        self.target_rows = [
            "!Series_title", "!Series_summary", "!Series_overall_design", 
            "!Series_supplementary_file", "!Sample_extract_protocol_ch1", 
            "!Sample_data_processing"
        ]
        
        self.results = []
        
    def validate_directories(self):
        """Validate that required directories exist"""
        if not self.crawl_output_dir.exists():
            raise FileNotFoundError(f"Crawl output directory not found: {self.crawl_output_dir}")
        
        for dir_path in [self.smtx_dir, self.srr_dir, self.meta_dir]:
            if not dir_path.exists():
                logger.warning(f"Directory not found: {dir_path}")
                dir_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Validated directories in {self.crawl_output_dir}")
    
    def scan_files(self):
        """Scan all files and extract GSE IDs"""
        logger.info("🔍 Scanning files in crawl_output directory...")
        
        # Get all GSE IDs from SMTX files (primary source)
        smtx_files = list(self.smtx_dir.glob("*_series_matrix.txt.gz"))
        gse_ids = set()
        
        for smtx_file in smtx_files:
            # Extract GSE ID from filename: GSE123456_series_matrix.txt.gz
            gse_id = smtx_file.stem.split('_series_matrix.txt')[0]
            gse_ids.add(gse_id)
        
        logger.info(f"Found {len(gse_ids)} unique GSE IDs from SMTX files")
        return sorted(gse_ids)
    
    def check_file_existence(self, gse_id):
        """Check existence of SMTX, SRR, and META files for a GSE ID"""
        smtx_file = self.smtx_dir / f"{gse_id}_series_matrix.txt.gz"
        srr_file = self.srr_dir / f"{gse_id}.txt"
        meta_file = self.meta_dir / f"{gse_id}_meta.txt"
        
        return {
            'smtx_exists': smtx_file.exists(),
            'srr_exists': srr_file.exists(),
            'meta_exists': meta_file.exists(),
            'smtx_path': smtx_file if smtx_file.exists() else None,
            'srr_path': srr_file if srr_file.exists() else None,
            'meta_path': meta_file if meta_file.exists() else None
        }
    
    def search_keywords_in_smtx(self, smtx_path):
        """Search for single-cell keywords in SMTX file"""
        if not smtx_path or not smtx_path.exists():
            return {keyword: 0 for keyword in self.keywords}
        
        keyword_presence = {keyword: 0 for keyword in self.keywords}
        
        try:
            with gzip.open(smtx_path, 'rt', encoding='utf-8', errors='ignore') as file:
                # Read only target rows to improve performance
                content_lines = []
                for line in file:
                    line = line.strip()
                    if any(line.startswith(target_row) for target_row in self.target_rows):
                        content_lines.append(line.lower())
                
                content = '\n'.join(content_lines)
                
                for keyword in self.keywords:
                    if keyword.lower() in content:
                        keyword_presence[keyword] = 1
                        logger.debug(f"Found keyword '{keyword}' in {smtx_path.name}")
                        
        except Exception as e:
            logger.error(f"Error reading {smtx_path.name}: {e}")
            
        return keyword_presence
    
    def analyze_single_gse(self, gse_id):
        """Analyze a single GSE entry"""
        logger.debug(f"Analyzing {gse_id}")
        
        file_status = self.check_file_existence(gse_id)
        
        keyword_presence = self.search_keywords_in_smtx(file_status['smtx_path'])
        
        # Calculate summary statistics
        total_keywords = sum(keyword_presence.values())
        has_any_keyword = total_keywords > 0
        is_complete = all([file_status['smtx_exists'], file_status['srr_exists'], file_status['meta_exists']])
        
        result = {
            'gse_id': gse_id,
            'smtx_exists': file_status['smtx_exists'],
            'srr_exists': file_status['srr_exists'],
            'meta_exists': file_status['meta_exists'],
            'is_complete': is_complete,
            'total_keywords': total_keywords,
            'has_any_keyword': has_any_keyword,
            **keyword_presence
        }
        
        return result
    
    def run_integration_analysis(self):
        """Run full integration analysis"""
        logger.info("🚀 Starting GENOAR data integration analysis...")
        
        self.validate_directories()
        
        gse_ids = self.scan_files()
        
        if not gse_ids:
            logger.warning("No GSE files found!")
            return
        
        # Analyze each GSE
        logger.info(f"📊 Analyzing {len(gse_ids)} GSE entries...")
        
        for i, gse_id in enumerate(gse_ids, 1):
            logger.info(f"[{i}/{len(gse_ids)}] Processing {gse_id}")
            result = self.analyze_single_gse(gse_id)
            self.results.append(result)
        
        logger.info("✅ Analysis complete!")
        
    def generate_reports(self):
        """Generate all analysis reports"""
        if not self.results:
            logger.error("No results to generate reports!")
            return
        
        logger.info("📋 Generating integration reports...")
        
        df = pd.DataFrame(self.results)
        
        # 1. Generate integration_report.csv
        report_path = self.crawl_output_dir / 'integration_report.csv'
        df.to_csv(report_path, index=False)
        logger.info(f"✅ Generated: {report_path}")
        
        # 2. Generate complete_datasets.txt
        complete_datasets = df[df['is_complete'] & df['has_any_keyword']]['gse_id'].tolist()
        complete_path = self.crawl_output_dir / 'complete_datasets.txt'
        with open(complete_path, 'w') as f:
            for gse_id in complete_datasets:
                f.write(f"{gse_id}\n")
        logger.info(f"✅ Generated: {complete_path} ({len(complete_datasets)} complete datasets)")
        
        # 3. Generate statistics_summary.json
        stats = self.generate_statistics(df)
        stats_path = self.crawl_output_dir / 'statistics_summary.json'
        with open(stats_path, 'w') as f:
            json.dump(stats, f, indent=2)
        logger.info(f"✅ Generated: {stats_path}")
        
        # 4. Generate keyword_analysis.csv
        keyword_analysis = self.generate_keyword_analysis(df)
        keyword_path = self.crawl_output_dir / 'keyword_analysis.csv'
        keyword_analysis.to_csv(keyword_path, index=False)
        logger.info(f"✅ Generated: {keyword_path}")
        
        # 5. Print summary to console
        self.print_summary(df, stats)
    
    def generate_statistics(self, df):
        """Generate comprehensive statistics"""
        total_gse = len(df)
        
        stats = {
            'analysis_timestamp': datetime.now().isoformat(),
            'total_gse_processed': total_gse,
            'file_existence': {
                'smtx_files': int(df['smtx_exists'].sum()),
                'srr_files': int(df['srr_exists'].sum()),
                'meta_files': int(df['meta_exists'].sum())
            },
            'completeness': {
                'complete_datasets': int(df['is_complete'].sum()),
                'complete_with_keywords': int(df[df['is_complete'] & df['has_any_keyword']].shape[0]),
                'incomplete_datasets': int((~df['is_complete']).sum())
            },
            'keyword_analysis': {
                'gse_with_keywords': int(df['has_any_keyword'].sum()),
                'gse_without_keywords': int((~df['has_any_keyword']).sum()),
                'avg_keywords_per_gse': float(df['total_keywords'].mean())
            },
            'success_rates': {
                'smtx_success_rate': float(df['smtx_exists'].mean()),
                'srr_success_rate': float(df['srr_exists'].mean()),
                'meta_success_rate': float(df['meta_exists'].mean()),
                'complete_success_rate': float(df['is_complete'].mean()),
                'usable_data_rate': float(df[df['is_complete'] & df['has_any_keyword']].shape[0] / total_gse)
            }
        }
        
        return stats
    
    def generate_keyword_analysis(self, df):
        """Generate keyword distribution analysis"""
        keyword_data = []
        
        for keyword in self.keywords:
            count = df[keyword].sum()
            percentage = (count / len(df)) * 100
            
            keyword_data.append({
                'keyword': keyword,
                'count': int(count),
                'percentage': round(percentage, 2),
                'description': self.get_keyword_description(keyword)
            })
        
        return pd.DataFrame(keyword_data).sort_values('count', ascending=False)
    
    def get_keyword_description(self, keyword):
        """Get description for each keyword"""
        descriptions = {
            'chromium': '10x Genomics Chromium platform',
            '10x': '10x Genomics technology',
            'cell ranger': 'Cell Ranger analysis pipeline',
            'single-cell rna': 'Single-cell RNA sequencing',
            '.h5': 'HDF5 format files',
            'barcodes.tsv.gz': 'Cell barcode files',
            'features.tsv.gz': 'Feature/gene annotation files',
            'matrix.mtx.gz': 'Expression matrix files'
        }
        return descriptions.get(keyword, 'Single-cell related term')
    
    def print_summary(self, df, stats):
        """Print comprehensive summary"""
        print("\n" + "="*60)
        print("🧬 GENOAR Integration Summary")
        print("="*60)
        
        print(f"📊 Total GSE Processed: {stats['total_gse_processed']}")
        print(f"📁 File Collection:")
        print(f"   - SMTX files: {stats['file_existence']['smtx_files']}")
        print(f"   - SRR files: {stats['file_existence']['srr_files']}")
        print(f"   - META files: {stats['file_existence']['meta_files']}")
        
        print(f"\n✅ Data Completeness:")
        print(f"   - Complete datasets: {stats['completeness']['complete_datasets']}")
        print(f"   - Complete + Keywords: {stats['completeness']['complete_with_keywords']}")
        print(f"   - Incomplete datasets: {stats['completeness']['incomplete_datasets']}")
        
        print(f"\n🔍 Keyword Analysis:")
        print(f"   - GSE with keywords: {stats['keyword_analysis']['gse_with_keywords']}")
        print(f"   - Average keywords per GSE: {stats['keyword_analysis']['avg_keywords_per_gse']:.2f}")
        
        print(f"\n📈 Success Rates:")
        print(f"   - SMTX collection: {stats['success_rates']['smtx_success_rate']:.1%}")
        print(f"   - SRR collection: {stats['success_rates']['srr_success_rate']:.1%}")
        print(f"   - META collection: {stats['success_rates']['meta_success_rate']:.1%}")
        print(f"   - Complete datasets: {stats['success_rates']['complete_success_rate']:.1%}")
        print(f"   - Usable data rate: {stats['success_rates']['usable_data_rate']:.1%}")
        
        print(f"\n🎯 Final Result:")
        usable_count = stats['completeness']['complete_with_keywords']
        print(f"   {usable_count} GSE entries are ready for single-cell analysis!")
        
        print("\n📋 Generated Files:")
        print(f"   - integration_report.csv: Complete analysis results")
        print(f"   - complete_datasets.txt: {usable_count} usable GSE IDs")
        print(f"   - statistics_summary.json: Detailed statistics")
        print(f"   - keyword_analysis.csv: Keyword distribution")
        
        print("="*60)


def main():
    """Main entry point"""
    crawl_output_dir = sys.argv[1] if len(sys.argv) > 1 else 'crawl_output'
    
    logger.info(f"🚀 Starting GENOAR Data Integration")
    logger.info(f"📁 Analysis directory: {crawl_output_dir}")
    
    integrator = DataIntegrator(crawl_output_dir)
    
    try:
        integrator.run_integration_analysis()
        integrator.generate_reports()
        logger.info("🎉 Integration complete!")
        
    except Exception as e:
        logger.error(f"Integration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()