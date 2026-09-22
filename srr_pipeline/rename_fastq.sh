#!/bin/bash

# Renaming helper
rename_files() {
    local sample_dir="$1"
    shift
    local files=("$@")

    # Pair each file with its size
    declare -a sizes
    for f in "${files[@]}"; do
        sizes+=( "$(stat -c%s "$f"):$f" )
    done

    # Sort by size
    IFS=$'\n' sorted=($(sort -n <<<"${sizes[*]}"))
    unset IFS

    # Derive the sample name
    sample_id=$(basename "$sample_dir")

    case "${#files[@]}" in
        2)
            for f in "${files[@]}"; do
                if [[ "$f" =~ _1\.fastq\.gz$ ]]; then
                    mv "$f" "${sample_dir}/${sample_id}_S1_R1_001.fastq.gz"
                elif [[ "$f" =~ _2\.fastq\.gz$ ]]; then
                    mv "$f" "${sample_dir}/${sample_id}_S1_R2_001.fastq.gz"
                fi
            done
            ;;
        3)
            # Take the middle and the largest file
            mid="${sorted[1]#*:}"
            max="${sorted[2]#*:}"
            mv "$mid" "${sample_dir}/${sample_id}_S1_R1_001.fastq.gz"
            mv "$max" "${sample_dir}/${sample_id}_S1_R2_001.fastq.gz"
            ;;
        4)
            # Take the second-largest and the largest file
            mid="${sorted[2]#*:}"
            max="${sorted[3]#*:}"
            mv "$mid" "${sample_dir}/${sample_id}_S1_R1_001.fastq.gz"
            mv "$max" "${sample_dir}/${sample_id}_S1_R2_001.fastq.gz"
            ;;
    esac
}

echo "🚀 Renaming files..."

# Walk the 2-, 3- and 4-file lists
for n in 2 3 4; do
    tsv="fastq_${n}_files.tsv"
    if [[ ! -f "$tsv" ]]; then
        echo "⚠️  ${tsv} not found, skipping."
        continue
    fi

    while read -r sample_dir; do
        files=( $(find "$sample_dir" -maxdepth 1 -type f -name "*.fastq.gz" | sort) )

        if [[ ${#files[@]} -eq $n ]]; then
            rename_files "$sample_dir" "${files[@]}"
        fi
    done < "$tsv"
done
