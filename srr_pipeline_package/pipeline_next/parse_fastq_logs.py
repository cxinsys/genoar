#!/usr/bin/env python3
import os
import sys
from pathlib import Path

"""
Step 4: Parse fastq results and produce success/failed lists.

Containerized approach: rather than parsing SLURM logs, we determine success
by the presence of .fastq.gz files after Step 3. This is more robust and
does not depend on log formats.

Usage: parse_fastq_logs.py [base_dir] [output_dir]
 - base_dir: directory containing SRR* sample directories (default: /work/data/sra)
 - output_dir: directory to write fastq_success.txt and fastq_failed.txt (default: /work/results)
"""


def list_samples(base_dir: Path):
    if not base_dir.exists():
        return []
    return sorted([p for p in base_dir.iterdir() if p.is_dir() and p.name.startswith("SRR")])


def has_fastq_gz(sample_dir: Path) -> bool:
    for p in sample_dir.glob("*.fastq.gz"):
        return True
    return False


def main():
    base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/work/data/sra")
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/work/results")
    output_dir.mkdir(parents=True, exist_ok=True)

    success_path = output_dir / "fastq_success.txt"
    failed_path = output_dir / "fastq_failed.txt"

    success = []
    failed = []

    samples = list_samples(base_dir)
    if not samples:
        print(f"[step4] No SRR sample directories found in {base_dir}")
        # still create empty files for downstream steps
        success_path.write_text("")
        failed_path.write_text("")
        return 0

    for sdir in samples:
        if has_fastq_gz(sdir):
            success.append(sdir.name)
        else:
            failed.append(sdir.name)

    success_path.write_text("\n".join(success) + ("\n" if success else ""))
    failed_path.write_text("\n".join(failed) + ("\n" if failed else ""))

    print(f"[step4] Success: {len(success)} samples, Failed: {len(failed)} samples")
    print(f"[step4] Wrote: {success_path} and {failed_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
