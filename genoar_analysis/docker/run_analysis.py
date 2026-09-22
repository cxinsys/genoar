#!/usr/bin/env python3
"""
GENOAR Analysis Docker Entrypoint

Runs the FirstPassPipeline to generate UMLS-matched tables.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="GENOAR Analysis Pipeline - Generate UMLS-matched tables"
    )
    parser.add_argument(
        "--meta-dir", type=Path, required=True,
        help="Directory containing META files from Stage 1"
    )
    parser.add_argument(
        "--umls-dir", type=Path, required=True,
        help="Directory containing UMLS CSV files"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Output directory for first-pass tables"
    )
    parser.add_argument(
        "--json-output", type=Path,
        help="Output JSON file for results summary"
    )
    # The population the tables describe. Left unset here so the one definition
    # of the default lives in Config, but open so a site with another organism
    # changes a flag rather than the source.
    parser.add_argument(
        "--organism", default=None,
        help="Organism to keep (default: Config.DEFAULT_ORGANISM)"
    )
    parser.add_argument(
        "--library-source", default=None,
        help="Library source substring to keep (default: Config.DEFAULT_LIBRARY_SOURCE)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    result = {
        "success": False,
        "meta_dir": str(args.meta_dir),
        "output_dir": str(args.output_dir),
        # Recorded beside the counts: the same META directory yields different
        # tables under a different organism, so the numbers do not stand alone.
        # None until the pipeline runs, which is where Config supplies the
        # defaults these flags leave unset.
        "organism": args.organism,
        "library_source": args.library_source,
        "tables": {},
        "error": None
    }

    try:
        # Validate inputs
        if not args.meta_dir.exists():
            raise FileNotFoundError(f"META directory not found: {args.meta_dir}")

        if not args.umls_dir.exists():
            raise FileNotFoundError(f"UMLS directory not found: {args.umls_dir}")

        meta_files = list(args.meta_dir.glob("*_meta.txt"))
        if not meta_files:
            meta_files = list(args.meta_dir.glob("*.txt"))

        if not meta_files:
            logger.warning(f"No META files found in {args.meta_dir}")
            result["success"] = True
            result["tables"] = {
                "cell_type": {"rows": 0},
                "tissue": {"rows": 0},
                "disease": {"rows": 0}
            }
        else:
            logger.info(f"Found {len(meta_files)} META files")

            # Import and run pipeline
            from genoar_analysis.core.config import Config
            from genoar_analysis.pipelines.first_pass_pipeline import FirstPassPipeline

            organism = args.organism or Config.DEFAULT_ORGANISM
            library_source = args.library_source or Config.DEFAULT_LIBRARY_SOURCE
            result["organism"] = organism
            result["library_source"] = library_source

            # Ensure output directory exists
            args.output_dir.mkdir(parents=True, exist_ok=True)

            # Run pipeline
            logger.info("Running FirstPassPipeline...")
            pipeline = FirstPassPipeline(
                meta_dir=str(args.meta_dir),
                umls_data_dir=str(args.umls_dir),
                organism=organism,
                library_source=library_source
            )

            results = pipeline.create_all_first_pass_tables(output_dir=str(args.output_dir))

            # Process results
            successful_tables = 0
            for field, df in results.items():
                field_name = field.replace('_state_modified', '')

                if df is not None and len(df) > 0:
                    output_file = args.output_dir / f"HS_{field_name}_1st_pass_meta_table.csv"

                    result["tables"][field_name] = {
                        "rows": len(df),
                        "file": str(output_file),
                        "unique_series": int(df['Series'].nunique()) if 'Series' in df.columns else 0,
                        "unique_runs": int(df['Run'].nunique()) if 'Run' in df.columns else 0
                    }
                    successful_tables += 1
                    logger.info(f"  {field_name}: {len(df)} samples, {result['tables'][field_name]['unique_series']} series")
                else:
                    result["tables"][field_name] = {
                        "rows": 0,
                        "note": "No matches found"
                    }
                    logger.info(f"  {field_name}: 0 samples")

            result["success"] = True
            result["successful_tables"] = successful_tables
            logger.info(f"Analysis completed: {successful_tables}/3 tables with data")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Analysis failed: {e}", exc_info=True)

    # Output results
    print("\n" + "=" * 50)
    print("ANALYSIS RESULTS")
    print("=" * 50)
    print(json.dumps(result, indent=2))

    if args.json_output:
        with open(args.json_output, 'w') as f:
            json.dump(result, f, indent=2)
        logger.info(f"Results saved to {args.json_output}")

    # Exit with appropriate code
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
