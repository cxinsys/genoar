#!/usr/bin/env python3
"""
Stage 3 Preparer - Extract SRR IDs for future SRR pipeline execution

Supports two modes:
1. prepare_stage3(): Extract from raw SRR directory (Stage 1 output)
2. extract_srr_from_stage2_tables(): Extract from Stage 2 UMLS tables (quality filtered)
"""

import json
import logging
import csv
import re
from pathlib import Path
from typing import Dict, Any, List, Set, Optional

logger = logging.getLogger(__name__)

# The prefix Stage 3 can act on. The container finds its samples by it -
# `name.startswith("SRR")` in Snakefile_hs.smk, `success/SRR*` in
# run_docker_pipeline.sh - so an ENA (ERR) or DDBJ (DRR) run carried this far
# would be downloaded, staged, and then not seen: a job that completes having
# analysed nothing. Leaving them out is the right call; leaving them out
# without saying so is what made the corpus quietly smaller than the table it
# came from.
STAGE3_ACCESSION_PREFIX = "SRR"

# The run accessions the three INSDC archives issue, so a dropped ERR/DRR can
# be named as such instead of counted with malformed text.
RUN_ACCESSION_PATTERN = re.compile(r"^[SED]RR[0-9]+$")


def _record_skipped_accession(skipped: Dict[str, int], accession: str) -> None:
    """Bucket one accession Stage 3 cannot process, by archive."""
    key = accession[:3].upper() if RUN_ACCESSION_PATTERN.match(accession) else "unrecognised"
    skipped[key] = skipped.get(key, 0) + 1


def _report_skipped_accessions(skipped: Dict[str, int], source: str) -> None:
    """Say out loud how much of `source` did not reach the Stage 3 corpus."""
    if not skipped:
        return
    logger.warning(
        f"{source}: {sum(skipped.values())} run accession(s) are not "
        f"{STAGE3_ACCESSION_PREFIX}* and are not in the Stage 3 corpus "
        f"({', '.join(f'{k}={v}' for k, v in sorted(skipped.items()))}). "
        f"Stage 3 discovers its samples by that prefix; fetch these through "
        f"their own archive if they are needed."
    )


def _clear_stale_srr_list(output_dir: Path) -> None:
    """Drop an SRR list left by an earlier run when this one produced none.

    Stage 3 branches on srr_list.txt existing, so a leftover list would be
    downloaded again as though it belonged to the current cycle. Every prep exit
    that yields no ids goes through here, not just the common one.
    """
    stale = Path(output_dir) / "srr_list.txt"
    if stale.exists():
        logger.warning(f"Removing stale SRR list from a previous run: {stale}")
        stale.unlink()


def extract_srr_from_stage2_tables(
    stage2_dir: Path,
    output_dir: Path
) -> Dict[str, Any]:
    """
    Extract SRR IDs from Stage 2 first-pass tables.

    This extracts SRR IDs that have been quality-filtered through UMLS matching,
    ensuring only Homo sapiens + TRANSCRIPTOMIC samples are included.

    Args:
        stage2_dir: Directory containing Stage 2 output CSV files
        output_dir: Output directory for stage3 preparation

    Returns:
        Dict with extraction statistics
    """
    result = {
        "success": False,
        "source": "stage2_tables",
        "total_srr_ids": 0,
        "unique_srr_ids": 0,
        "gse_count": 0,
        "files": {},
        "tables_processed": [],
        "srr_per_table": {},
        "skipped_accessions": {},
        "error": None
    }

    # Invalidate any list from a previous run at the start, so every exit from
    # here on, including an exception, is fail-closed: a stale list can never be
    # left as this run's input. A fresh list is written only on success below.
    _clear_stale_srr_list(output_dir)

    try:
        table_patterns = {
            "cell_type": "HS_cell_type_1st_pass_meta_table.csv",
            "tissue": "HS_tissue_1st_pass_meta_table.csv",
            "disease": "HS_disease_1st_pass_meta_table.csv"
        }

        all_srr_ids: List[str] = []
        gse_srr_mapping: Dict[str, Set[str]] = {}
        skipped_accessions: Dict[str, int] = {}

        for table_name, filename in table_patterns.items():
            table_file = stage2_dir / filename

            if not table_file.exists():
                logger.info(f"Table not found: {filename}")
                continue

            table_srr_count = 0

            try:
                with open(table_file, 'r', newline='', encoding='utf-8') as f:
                    reader = csv.DictReader(f)

                    if 'Run' not in reader.fieldnames or 'Series' not in reader.fieldnames:
                        logger.warning(f"Missing Run/Series columns in {filename}")
                        continue

                    for row in reader:
                        srr_id = row.get('Run', '').strip()
                        gse_id = row.get('Series', '').strip()

                        if not srr_id:
                            continue
                        if not srr_id.startswith(STAGE3_ACCESSION_PREFIX):
                            _record_skipped_accession(skipped_accessions, srr_id)
                            continue

                        all_srr_ids.append(srr_id)
                        table_srr_count += 1

                        if gse_id:
                            if gse_id not in gse_srr_mapping:
                                gse_srr_mapping[gse_id] = set()
                            gse_srr_mapping[gse_id].add(srr_id)

                result["tables_processed"].append(table_name)
                result["srr_per_table"][table_name] = table_srr_count
                logger.info(f"  {table_name}: {table_srr_count} SRR IDs")

            except Exception as e:
                logger.warning(f"Error reading {filename}: {e}")

        _report_skipped_accessions(skipped_accessions, "Stage 2 tables")
        result["skipped_accessions"] = dict(sorted(skipped_accessions.items()))

        if not all_srr_ids:
            logger.warning("No SRR IDs found in Stage 2 tables")
            result["success"] = True
            result["note"] = "No SRR IDs found in Stage 2 tables"
            return result

        gse_srr_mapping_list = {k: sorted(v) for k, v in gse_srr_mapping.items()}

        output_dir.mkdir(parents=True, exist_ok=True)

        unique_srr_ids = sorted(set(all_srr_ids))
        srr_list_file = output_dir / "srr_list.txt"
        with open(srr_list_file, 'w') as f:
            for srr_id in unique_srr_ids:
                f.write(f"{srr_id}\n")
        result["files"]["srr_list"] = str(srr_list_file)

        mapping_file = output_dir / "gse_srr_mapping.json"
        with open(mapping_file, 'w') as f:
            json.dump(gse_srr_mapping_list, f, indent=2, sort_keys=True)
        result["files"]["mapping"] = str(mapping_file)

        summary = {
            "source": "stage2_tables",
            "tables_processed": result["tables_processed"],
            "total_srr_ids": len(all_srr_ids),
            "unique_srr_ids": len(unique_srr_ids),
            "total_gse": len(gse_srr_mapping_list),
            "gse_list": sorted(gse_srr_mapping_list.keys()),
            "srr_per_table": result["srr_per_table"],
            "skipped_accessions": result["skipped_accessions"],
            "srr_count_per_gse": {k: len(v) for k, v in sorted(gse_srr_mapping_list.items())}
        }

        summary_file = output_dir / "stage3_prep_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        result["files"]["summary"] = str(summary_file)

        result.update({
            "success": True,
            "total_srr_ids": len(all_srr_ids),
            "unique_srr_ids": len(unique_srr_ids),
            "gse_count": len(gse_srr_mapping_list)
        })

        logger.info(
            f"Stage 3 prep (from Stage 2 tables): {len(unique_srr_ids)} unique SRR IDs "
            f"from {len(gse_srr_mapping_list)} GSE datasets"
        )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Stage 3 preparation failed: {e}", exc_info=True)

    return result


def prepare_stage3(
    srr_dir: Path,
    output_dir: Path,
    complete_datasets_file: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Prepare Stage 3 by extracting SRR IDs from crawled data.

    This creates the necessary files for later SRR pipeline execution:
    - srr_list.txt: List of all SRR IDs
    - gse_srr_mapping.json: Mapping of GSE -> SRR IDs

    Args:
        srr_dir: Directory containing SRR files (GSE*.txt with SRR IDs)
        output_dir: Output directory for stage3 preparation
        complete_datasets_file: Optional path to complete_datasets.txt for filtering

    Returns:
        Dict with extraction statistics
    """
    result = {
        "success": False,
        "total_srr_ids": 0,
        "gse_count": 0,
        "files": {},
        "skipped_accessions": {},
        "error": None
    }

    # See extract_srr_from_stage2_tables: invalidate first so every exit is
    # fail-closed and no stale list survives an error.
    _clear_stale_srr_list(output_dir)

    try:
        complete_gse: Set[str] = set()
        if complete_datasets_file and complete_datasets_file.exists():
            with open(complete_datasets_file) as f:
                complete_gse = {line.strip() for line in f if line.strip()}
            logger.info(f"Filtering by {len(complete_gse)} complete datasets")

        if not srr_dir.exists():
            logger.warning(f"SRR directory not found: {srr_dir}")
            result["success"] = True  # Not an error, just no SRR data
            result["note"] = "No SRR directory found"
            return result

        srr_files = list(srr_dir.glob("*.txt"))
        if not srr_files:
            logger.info("No SRR files found")
            result["success"] = True
            result["note"] = "No SRR files found"
            return result

        all_srr_ids: List[str] = []
        gse_srr_mapping: Dict[str, List[str]] = {}
        skipped_accessions: Dict[str, int] = {}

        for srr_file in srr_files:
            gse_id = srr_file.stem  # e.g., "GSE301787"

            if complete_gse and gse_id not in complete_gse:
                continue

            try:
                srr_ids = []
                with open(srr_file) as f:
                    for line in f:
                        accession = line.strip()
                        if not accession:
                            continue
                        if accession.startswith(STAGE3_ACCESSION_PREFIX):
                            srr_ids.append(accession)
                        else:
                            _record_skipped_accession(skipped_accessions, accession)

                if srr_ids:
                    gse_srr_mapping[gse_id] = srr_ids
                    all_srr_ids.extend(srr_ids)

            except Exception as e:
                logger.warning(f"Error reading {srr_file}: {e}")

        _report_skipped_accessions(skipped_accessions, str(srr_dir))
        result["skipped_accessions"] = dict(sorted(skipped_accessions.items()))

        output_dir.mkdir(parents=True, exist_ok=True)

        srr_list_file = output_dir / "srr_list.txt"
        with open(srr_list_file, 'w') as f:
            for srr_id in sorted(set(all_srr_ids)):  # Unique and sorted
                f.write(f"{srr_id}\n")
        result["files"]["srr_list"] = str(srr_list_file)

        mapping_file = output_dir / "gse_srr_mapping.json"
        with open(mapping_file, 'w') as f:
            json.dump(gse_srr_mapping, f, indent=2, sort_keys=True)
        result["files"]["mapping"] = str(mapping_file)

        summary = {
            "total_srr_ids": len(all_srr_ids),
            "unique_srr_ids": len(set(all_srr_ids)),
            "total_gse": len(gse_srr_mapping),
            "gse_list": sorted(gse_srr_mapping.keys()),
            "srr_count_per_gse": {k: len(v) for k, v in sorted(gse_srr_mapping.items())},
            "skipped_accessions": result["skipped_accessions"],
        }

        summary_file = output_dir / "stage3_prep_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        result["files"]["summary"] = str(summary_file)

        readme_content = f"""# Stage 3 Preparation

## Contents
- srr_list.txt: {len(set(all_srr_ids))} unique SRR IDs from {len(gse_srr_mapping)} GSE datasets
- gse_srr_mapping.json: GSE to SRR mapping
- stage3_prep_summary.json: Detailed statistics

## Next Steps (Manual)
1. Download SRA files using prefetch:
   ```bash
   cat srr_list.txt | xargs -I {{}} prefetch {{}}
   ```

2. Run SRR pipeline:
   ```bash
   docker run --rm -it \\
     -v /path/to/sra_files:/work/data/sra \\
     -v /path/to/config.yaml:/work/config.yaml \\
     genoar-srr:step9
   ```

Generated: {len(all_srr_ids)} total SRR IDs, {len(gse_srr_mapping)} GSE datasets
"""
        readme_file = output_dir / "README.md"
        with open(readme_file, 'w') as f:
            f.write(readme_content)

        result.update({
            "success": True,
            "total_srr_ids": len(all_srr_ids),
            "unique_srr_ids": len(set(all_srr_ids)),
            "gse_count": len(gse_srr_mapping)
        })

        logger.info(
            f"Stage 3 prep: {len(set(all_srr_ids))} unique SRR IDs "
            f"from {len(gse_srr_mapping)} GSE datasets"
        )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Stage 3 preparation failed: {e}", exc_info=True)

    return result
