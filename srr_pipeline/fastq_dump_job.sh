#!/bin/bash

#SBATCH --job-name=fastq_dump
#SBATCH --output=slurm-%A_%a.out
#SBATCH --error=slurm-%A_%a.err
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --partition=<partition>
#SBATCH --array=0-ARRAY_MAX  # substituted by the submit script

# Record the start time
echo "Job started at: $(date)"

# How many directories each array task handles, read from the environment (default: 10)
files_per_job=${FILES_PER_JOB:-10}

# Collect the list of directories
base_dir="./"
dirs=($(ls -v -d "$base_dir"/SRR*))

# Work out the index range this SLURM task is responsible for
start=$((SLURM_ARRAY_TASK_ID * files_per_job))
end=$((start + files_per_job - 1))

# Total number of directories
total=${#dirs[@]}

# Clamp the range to the end of the list
if [ "$end" -ge "$total" ]; then
  end=$((total - 1))
fi

# Process the directories assigned to this task
for ((i = start; i <= end; i++)); do
  dir=${dirs[$i]}
  srr_file=$(basename "$dir")
  echo "[$i] Processing $srr_file"
  fastq-dump --split-files "$dir/$srr_file" --outdir "$dir"

  if [ $? -eq 0 ]; then
      echo "$srr_file fastq-dump completed successfully."
  else
      echo "Error processing $srr_file"
  fi
done

# Record the end time
echo "Job ended at: $(date)"
