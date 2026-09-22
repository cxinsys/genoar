#!/bin/bash

# Walk everything in the current directory whose name starts with SRR.
for file in SRR*; do
    # Only act on regular files.
    if [ -f "$file" ]; then
        # Rename it out of the way
        temp="tmp_$file"
        mv "$file" "$temp"
        
        # Create a directory named after the original file, if it does not exist yet.
        if [ ! -d "$file" ]; then
            mkdir "$file"
            echo "Directory '$file' created."
        else
            echo "Directory '$file' already exists."
        fi
        
        # Move the temp file into that directory, restoring its original name.
        mv "$temp" "$file/$file"
        echo "Moved '$file' file into '$file/' directory with its original name."
    fi
done

