# SRR Pipeline Package Dockerfile
# Multi-stage plan with progressive targets; currently implements Step 1.

# Pinned Python base image for reproducibility
FROM python:3.11.9-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash coreutils findutils ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Common pipeline location, and every path the runners bind over.
# Docker creates a missing bind destination. Singularity does not: a SIF is
# read-only, so binding onto a path the image lacks needs overlay or underlay,
# and sites that disable both fail with "destination doesn't exist in
# container" before the pipeline starts. srr_pipeline_package/singularity/
# is a supported way to run stage 3, so the destinations ship in the image.
RUN mkdir -p /pipeline /pipeline_next /work/data/sra /work/logs /work/results /ref /opt/cellranger \
 && touch /work/config.yaml

# ---- Step 1 target ----
FROM base AS step1

COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
COPY srr_pipeline_package/pipeline/lib.sh /pipeline/lib.sh
COPY srr_pipeline_package/pipeline/make_directories.sh /pipeline/make_directories.sh

# Strip any CRLF that slipped in (e.g. checked out on Windows) so the
# Linux entrypoint resolves `#!/usr/bin/env bash` instead of `bash\r`.
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/run_docker_pipeline.sh /pipeline/make_directories.sh

ENTRYPOINT ["/pipeline/run_docker_pipeline.sh"]
CMD []

# ---- Step 2 target ----
FROM step1 AS step2

# Install tools for Step 2
RUN apt-get update && apt-get install -y --no-install-recommends \
    sra-toolkit parallel \
  && rm -rf /var/lib/apt/lists/*

# PyYAML (pinned) for config parsing
COPY srr_pipeline_package/requirements.base.txt /tmp/requirements.base.txt
RUN pip install --no-cache-dir -r /tmp/requirements.base.txt

COPY srr_pipeline_package/pipeline/fastq_dump_parallel.sh /pipeline/fastq_dump_parallel.sh
COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/fastq_dump_parallel.sh /pipeline/run_docker_pipeline.sh

# ---- Final pipeline target (will expand with steps 3–9 later) ----
# ---- Step 3 target ----
FROM step2 AS step3

RUN apt-get update && apt-get install -y --no-install-recommends \
    gzip pigz \
  && rm -rf /var/lib/apt/lists/*

COPY srr_pipeline_package/pipeline/fastq_gzip_parallel.sh /pipeline/fastq_gzip_parallel.sh
COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/fastq_gzip_parallel.sh /pipeline/run_docker_pipeline.sh

# ---- Final pipeline target (will expand with steps 4–9 later) ----
# ---- Step 4 target ----
FROM step3 AS step4

COPY srr_pipeline_package/pipeline/parse_fastq_logs.py /pipeline/parse_fastq_logs.py

# ---- Final pipeline target (will expand with steps 5–9 later) ----
# ---- Step 5 target ----
FROM step4 AS step5

COPY srr_pipeline_package/pipeline/move_success_dirs.sh /pipeline/move_success_dirs.sh
COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/move_success_dirs.sh /pipeline/run_docker_pipeline.sh

# ---- Final pipeline target (will expand with steps 6–9 later) ----
# ---- Step 6 target ----
FROM step5 AS step6

COPY srr_pipeline_package/pipeline/check_counts.sh /pipeline/check_counts.sh
COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/check_counts.sh /pipeline/run_docker_pipeline.sh

# ---- Final pipeline target (will expand with steps 7–9 later) ----
# ---- Step 7 target ----
FROM step6 AS step7

COPY srr_pipeline_package/pipeline/rename_fastq.sh /pipeline/rename_fastq.sh
COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/rename_fastq.sh /pipeline/run_docker_pipeline.sh

# ---- Step 8 target ----
FROM step7 AS step8

# Install build tools for Snakemake dependencies (datrie requires gcc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ python3-dev \
  && rm -rf /var/lib/apt/lists/*

# Install Snakemake (pinned)
COPY srr_pipeline_package/requirements.snakemake.txt /tmp/requirements.snakemake.txt
RUN pip install --no-cache-dir -r /tmp/requirements.snakemake.txt

COPY srr_pipeline_package/pipeline/Snakefile_hs.smk /pipeline/Snakefile_hs.smk
COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/run_docker_pipeline.sh

# ---- Final pipeline target (will expand with step 9 later) ----
# ---- Step 9 target ----
FROM step8 AS step9

COPY srr_pipeline_package/pipeline/rename_move_failed_fastqs.py /pipeline/rename_move_failed_fastqs.py
COPY srr_pipeline_package/pipeline/run_docker_pipeline.sh /pipeline/run_docker_pipeline.sh
RUN sed -i 's/\r$//' /pipeline/*.sh && chmod +x /pipeline/run_docker_pipeline.sh

# ---- Both pipelines, and the switch between them ----
#
# /pipeline is Stage 3 as it stood when a sample went through K-BDS and came
# back complete, plus the two fixes made by hand on the cluster to get it
# there. Frozen: a test fails if it changes, and it is kept so an earlier run
# can be reproduced.
#
# /pipeline_next is the same pipeline with everything found since -- the checks
# that were reporting nothing, the silent failures, the corpus path. It is what
# runs unless a config asks otherwise.
#
# `legacy_pipeline` in the config chooses, defaulting to the current one.
# Shipping both is what makes the choice a run-time decision rather than a
# rebuild, and what lets the same input be put through each and the answers
# compared.
COPY srr_pipeline_package/pipeline_next/ /pipeline_next/
COPY srr_pipeline_package/pipeline_entry.sh /pipeline_entry.sh
RUN sed -i 's/\r$//' /pipeline_next/*.sh /pipeline_entry.sh \
 && chmod +x /pipeline_next/*.sh /pipeline_entry.sh

# The entrypoint moves to the dispatcher. It reads `legacy_pipeline` (and the
# older aliases for compatibility), changes
# nothing else, and `exec`s the chosen tree with the arguments and environment
# exactly as they arrived -- so the frozen pipeline sees what it always saw.
ENTRYPOINT ["/pipeline_entry.sh"]
CMD []

# `pipeline` is the same image under the name the compose files use. Both
# trees and the dispatcher are in step9 above rather than here: `make
# build-srr` builds `--target step9`, so anything added only at this stage is
# absent from the image everybody actually runs -- which is what happened to
# the switch between the two pipelines the first time.
FROM step9 AS pipeline
