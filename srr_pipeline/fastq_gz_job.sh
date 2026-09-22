#!/bin/bash

#SBATCH --job-name=fastq_gz
#SBATCH --output=slurmGzip-%A_%a.out
#SBATCH --error=slurmGzip-%A_%a.err
#SBATCH --cpus-per-task=1
##SBATCH --mem=16G
#SBATCH --partition=<partition>
#SBATCH --array=0-ARRAY_MAX  # substituted by the submit script

# Record the start time
echo "Job started at: $(date)"

# How many directories each array task handles, read from the environment (default: 10)
files_per_job=${FILES_PER_JOB:-10}

# Collect the list of directories
base_dir="./"
dirs=($(ls -v -d "$base_dir"/SRR*fastq))

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
  fqfiles=("$dir/$dir"_*.fastq)
  if [ ${#fqfiles[@]} -gt 0 ]; then
    for fqfile in "${fqfiles[@]}"; do
      echo "[$i] gzip Processing $fqfile"
      #let's gzip
      gzip "fqfile"
      # example command: xxx
      if gzip "$fqfile"; then
        echo "$fqfile gzip completed successfully."
      else
        echo "Error gzip processing $fqfile" >&2
      fi
    done

  else
    echo "error: $dir has no fastq file." >&2
  fi
done


# Record the end time
echo "Job ended at: $(date)"
