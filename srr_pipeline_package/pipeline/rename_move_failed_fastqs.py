#!/usr/bin/env python3
import os
import shutil
from pathlib import Path
import sys

"""
Step 9: Handle failed Cell Ranger samples by swapping R1/R2 and moving to retry directory.

Criteria (containerized):
- In success_dir/<sample>/, if cellranger_output exists but outs/ is missing OR
  the expected BAM outs/possorted_genome_bam.bam is missing, treat as failed.

Actions for each failed sample:
- Swap filenames: <sample>_S1_R1_001.fastq.gz <-> <sample>_S1_R2_001.fastq.gz
- Remove cellranger_output/ (to avoid rerun conflicts)
- Move the whole sample directory to retry_dir/<sample>/
- Record sample names to retry_failed_samples.txt in output_dir
"""


def is_failed(sample_dir: Path) -> bool:
    cr = sample_dir / "cellranger_output"
    bam = cr / "outs" / "possorted_genome_bam.bam"
    # If cellranger_output exists but no outs/bam, consider failed
    if cr.exists() and not bam.exists():
        return True
    return False


def swap_fastqs(sample_dir: Path, sample: str) -> None:
    r1 = sample_dir / f"{sample}_S1_R1_001.fastq.gz"
    r2 = sample_dir / f"{sample}_S1_R2_001.fastq.gz"
    if r1.exists() and r2.exists():
        tmp = sample_dir / f".{sample}_swap_tmp.fastq.gz"
        r1.rename(tmp)
        r2.rename(r1)
        tmp.rename(r2)
    # If either missing, do nothing (leave as-is)


def remove_cellranger_output(sample_dir: Path) -> None:
    cr = sample_dir / "cellranger_output"
    if cr.exists():
        shutil.rmtree(cr, ignore_errors=True)


def list_samples(success_dir: Path):
    if not success_dir.exists():
        return []
    return sorted([p for p in success_dir.iterdir() if p.is_dir() and p.name.startswith("SRR")])


def main():
    success_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/work/results/success")
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/work/results")
    retry_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(output_dir / "retry_failed")
    log_path = Path(os.environ.get("LOG_FILE", "/work/logs/step9_retry_failed.log"))

    output_dir.mkdir(parents=True, exist_ok=True)
    retry_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    reports_dir = Path(os.environ.get("REPORTS_DIR", "/work/results/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    failed = []
    for sdir in list_samples(success_dir):
        sample = sdir.name
        if is_failed(sdir):
            failed.append(sample)

    if not failed:
        with open(log_path, "a") as log:
            log.write("[step9] No failed samples detected.\n")
        print("[step9] No failed samples detected.")
        return 0

    moved = []
    for sample in failed:
        sdir = success_dir / sample
        # swap R1/R2
        # swap R1/R2; if missing either, record failure but continue moving
        r1 = sdir / f"{sample}_S1_R1_001.fastq.gz"
        r2 = sdir / f"{sample}_S1_R2_001.fastq.gz"
        if not (r1.exists() and r2.exists()):
            with open(reports_dir / "step9_failed.txt", "a") as f:
                f.write(f"{sample}\n")
        swap_fastqs(sdir, sample)
        # cleanup previous outputs
        remove_cellranger_output(sdir)
        # move to retry dir
        dest = retry_dir / sample
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(sdir), str(dest))
        moved.append(sample)

    # write list file
    list_path = output_dir / "retry_failed_samples.txt"
    with open(list_path, "w") as f:
        for s in moved:
            f.write(s + "\n")

    with open(log_path, "a") as log:
        log.write(f"[step9] Moved {len(moved)} failed samples to {retry_dir}\n")
        log.write(f"[step9] Wrote list to {list_path}\n")

    print(f"[step9] Moved {len(moved)} failed samples to {retry_dir}")
    print(f"[step9] Wrote list to {list_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
