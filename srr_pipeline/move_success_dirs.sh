#!/bin/bash

# Working directories
base_dir="./"
success_list="$base_dir/fastq_success.txt"
success_dir="$base_dir/success"

# Bail out if the list file is missing; create the target directory if it is not there
if [ ! -f "$success_list" ]; then
    echo "fastq_success.txt does not exist: $success_list"
    exit 1
fi

if [ ! -d "$success_dir" ]; then
    echo "success directory does not exist, creating it: $success_dir"
    mkdir -p "$success_dir"
fi

# Start moving
echo "Moving successful SRR directories into success/..."

moved_count=0
skipped_count=0

while read -r srr; do
    src="$base_dir/$srr"
    dest="$success_dir/$srr"

    if [ -d "$src" ]; then
        mv "$src" "$dest"
        echo "Moved: $srr"
        ((moved_count++))
    else
        echo "No such directory: $srr"
        ((skipped_count++))
    fi
done < "$success_list"

echo -e "\n Done: moved $moved_count directories, skipped $skipped_count that did not exist"

