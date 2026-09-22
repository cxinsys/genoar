#!/bin/bash

# Truncate the output files
for i in 1 2 3 4; do
    echo -n > "fastq_${i}_files.tsv"
done

# Walk the SRR directories in the current directory
for sample_dir in SRR*/; do
    # Count the fastq.gz files
    count=$(find "$sample_dir" -maxdepth 1 -type f -name "*.fastq.gz" | wc -l)

    # Only bucket samples with 1 to 4 files
    if [[ $count -ge 1 && $count -le 4 ]]; then
        echo "${sample_dir%/}" >> "fastq_${count}_files.tsv"
    fi
done

