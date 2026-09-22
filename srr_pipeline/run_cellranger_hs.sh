#!/bin/bash
#
#SBATCH --job-name=cellranger
#SBATCH --partition=<partition>
#SBATCH --time=120:00:00  # run for at most 120 hours
#SBATCH --mem=100G       # 100GB of memory
#SBATCH --ntasks=1       # number of tasks
#SBATCH --nodes=1        # number of nodes
#SBATCH --cpus-per-task=16  # CPU cores per task
#SBATCH --output=/path/to/logs/%x_%j.log  # log file

source activate snakemake

# Run Snakemake
snakemake -s Snakefile.smk -j 16 --latency-wait 60 --keep-going --forceall --executor cluster-generic --cluster-generic-submit-cmd "sbatch --partition=<partition> --ntasks=1 --nodes=1 --mem=100G --cpus-per-task=16 --time=120:00:00 --output=/path/to/logs/%x_%j.log" --verbose

