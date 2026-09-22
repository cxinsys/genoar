#!/bin/bash

# Count the SRR directories
num_dirs=$(ls -d ./SRR* 2>/dev/null | wc -l)

if [ "$num_dirs" -eq 0 ]; then
    echo "No SRR directories found. Exiting."
    exit 1
fi

# n directories per task -> how many array jobs we need
files_per_job=50
num_jobs=$(( (num_dirs + files_per_job - 1) / files_per_job ))
array_max=$((num_jobs - 1))

# Substitute the array range into the SLURM script
tmp_script="tmp_fastq_dump_job.sh"
sed "s/ARRAY_MAX/$array_max/" fastq_gz_job.sh > "$tmp_script"

# Submit the SLURM job array (passing the environment variable along)
echo "Submitting job array: 0-$array_max (Total SRR dirs: $num_dirs, Jobs: $num_jobs, Files/job: $files_per_job)..."
sbatch --export=FILES_PER_JOB=$files_per_job "$tmp_script"

# Remove the temporary script
rm "$tmp_script"

