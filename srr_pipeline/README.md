# proj_GENoAR pipeline

🔁 Pipeline run order

| Step | Script | What it does | Input | Output |
| --- | --- | --- | --- | --- |
| 1 | `make_directories.sh` | Moves each `.sra` file into an SRR directory | `SRRxxxxxx` file | `SRRxxxxxx/SRRxxxxxx` |
| 2 | `slurm_fastq_submit.sh` → `fastq_dump_job.sh` | Converts `.sra` to `.fastq` | `.sra` | `_1.fastq`, `_2.fastq` |
| 3 | `slurm_gzip_submit.sh` → `fastq_gz_job.sh` | Compresses `.fastq` to `.fastq.gz` | `.fastq` | `.fastq.gz` |
| 4 | `parse_fastq_logs.py` | Splits samples into succeeded/failed from the SLURM logs | `slurm-*.out` | `fastq_success.txt`, `fastq_failed.txt` |
| 5 | `move_success_dirs.sh` | Collects the successful sample directories | `fastq_success.txt` | `success/` directory |
| 6 | `check_counts.sh` | Buckets samples by how many FASTQ files they have | `.fastq.gz` | `fastq_2_files.tsv`, etc. |
| 7 | `rename_fastq.sh` | Renames the FASTQ files to the names Cell Ranger expects | `fastq_[2-4]_files.tsv` | `*_R1_001.fastq.gz`, `*_R2_001.fastq.gz` |
| 8 | `run_cellranger_hs.sh` → `Snakefile_hs.smk` | Runs Cell Ranger in parallel via Snakemake | Renamed FASTQ | `outs/possorted_genome_bam.bam` |
| 9 | `rename_move_failed_fastqs.py` | Swaps R1/R2 on the failed samples | Cell Ranger failures | Rearranged FASTQ files, ready for a retry |

Directory layout

```bash
Project_Root/
├── SRR1.sra
├── SRR2.sra
├── SRR3.sra
├── SRR4.sra
├── make_directories.sh
├── slurm_fastq_submit.sh
├── fastq_dump_job.sh
├── slurm_gzip_submit.sh
├── fastq_gz_job.sh
├── parse_fastq_logs.py
├── move_success_dirs.sh
└── ...
```

Step 1.  `make_directories.sh`

```bash
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
```

**What it does**:

For every SRA file named `SRRxxxxxx`, creates a directory of the same name and moves the SRA file into it.

**Input**:

- `.sra` files named `SRRxxxxxx` in the current directory

**Output**:

- A directory `/SRRxxxxxx/` per file
- The `.sra` file moved to `/SRRxxxxxx/SRRxxxxxx`

**Directory layout afterwards**:

```bash
Project_Root/
├── SRR1/
│   └── SRR1.sra
├── SRR2/
│   └── SRR2.sra
├── SRR3/
│   └── SRR3.sra
├── SRR4/
│   └── SRR4.sra
├── make_directories.sh
├── slurm_fastq_submit.sh
├── fastq_dump_job.sh
├── slurm_gzip_submit.sh
├── fastq_gz_job.sh
├── parse_fastq_logs.py
├── move_success_dirs.sh
└── ...
```

Step 2. `slurm_fastq_submit.sh` → `fastq_dump_job.sh` 

`slurm_fastq_submit.sh` 

```bash
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
sed "s/ARRAY_MAX/$array_max/" fastq_dump_job.sh > "$tmp_script"

# Submit the SLURM job array (passing the environment variable along)
echo "Submitting job array: 0-$array_max (Total SRR dirs: $num_dirs, Jobs: $num_jobs, Files/job: $files_per_job)..."
sbatch --export=FILES_PER_JOB=$files_per_job "$tmp_script"

# Remove the temporary script
rm "$tmp_script"
```

What it does:

- Computes the SLURM job array index range (`-array=0-XX`) from the number of `SRR*` directories in the current directory
- Writes a temporary script with `ARRAY_MAX` in `fastq_dump_job.sh` substituted for that range
- Submits the job array with `sbatch`, passing the `FILES_PER_JOB` environment variable along
- Deletes the temporary script once submitted

`fastq_dump_job.sh`

```bash
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
```

What it does:

- Each task in the SLURM job array takes N of the SRR directories, so they run in parallel
- Converts the `.sra` file in each SRR directory with `fastq-dump` (`_1.fastq`, `_2.fastq`)

**Input:**

- `/SRRxxxxxx/SRRxxxxxx` (the .sra file)

**Output:**

- `/SRRxxxxxx/SRRxxxxxx_1.fastq`
- `/SRRxxxxxx/SRRxxxxxx_2.fastq`
- (1 to 4 files, depending on the sample)

Directory layout afterwards:

```bash
Project_Root/
├── SRR1/
│   ├── SRR1.sra
│   ├── SRR1_1.fastq
├── SRR2/
│   ├── SRR2.sra
│   ├── SRR2_1.fastq
│   └── SRR2_2.fastq
├── SRR3/
│   ├── SRR3.sra
│   ├── SRR3_1.fastq
│   └── SRR3_2.fastq
│   └── SRR3_3.fastq
├── SRR4/
│   ├── SRR4.sra
│   ├── SRR4_1.fastq
│   └── SRR4_2.fastq
│   └── SRR4_3.fastq
│   └── SRR4_4.fastq
├── slurm-<jobid>.out               ← SLURM log (one per job)
├── slurm_fastq_submit.sh
├── fastq_dump_job.sh
├── make_directories.sh
└── ... (the other scripts)

```

Step 3. `slurm_gzip_submit.sh` → `fastq_gz_job.sh`

`slurm_gzip_submit.sh`

```bash
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
```

**What `slurm_gzip_submit.sh` does:**

- Computes the SLURM job array index range from the number of `SRR*` directories that contain `.fastq` files
- Writes a temporary script with `ARRAY_MAX` in `fastq_gz_job.sh` substituted
- Submits the job array with `sbatch` (passing the `FILES_PER_JOB` environment variable)
- Deletes the temporary script once submitted

`fastq_gz_job.sh`

```bash
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

```

**What `fastq_gz_job.sh` does:**

- Each job handles its allotted number of directories
- `gzip`s the `_1.fastq` and `_2.fastq` files in each of them

**Input:**

- `_1.fastq`, `_2.fastq`

**Output:**

- `_1.fastq.gz`, `_2.fastq.gz`

Directory layout afterwards:

```bash
Project_Root/
├── SRR1/
│   ├── SRR1.sra
│   ├── SRR1_1.fastq.gz     ← gzip output
├── SRR2/
│   ├── SRR2.sra
│   ├── SRR2_1.fastq.gz
│   ├── SRR2_2.fastq.gz
├── SRR3/
│   ├── SRR3.sra
│   ├── SRR3_1.fastq.gz
│   ├── SRR3_2.fastq.gz
│   ├── SRR3_3.fastq.gz
├── SRR4/
│   ├── SRR4.sra
│   ├── SRR4_1.fastq.gz
│   ├── SRR4_2.fastq.gz
│   ├── SRR4_3.fastq.gz
│   ├── SRR4_4.fastq.gz
├── slurm-<jobid>.out        ← SLURM gzip log
├── slurm_fastq_submit.sh
├── fastq_dump_job.sh
├── slurm_gzip_submit.sh
├── fastq_gz_job.sh
├── make_directories.sh
└── ... (the other scripts)

```

Step 4. **`parse_fastq_logs.py`** 

```python
import os

success_file = open("fastq_success.txt", "w")
fail_file = open("fastq_failed.txt", "w")

for f in os.listdir():
    if f.startswith("slurm-") and f.endswith(".out"):
        with open(f) as log:
            content = log.read()
            if "gzip" in content and "error" not in content.lower():
                sample = f.split("-")[1].split(".")[0]
                success_file.write(f"{sample}\n")
            else:
                sample = f.split("-")[1].split(".")[0]
                fail_file.write(f"{sample}\n")

success_file.close()
fail_file.close()
```

**What it does:**

- Parses the SLURM log files (`slurm-*.out`) to work out how `gzip` went for each sample
- Splits the sample names into two files by outcome
    - `fastq_success.txt`: samples that compressed successfully
    - `fastq_failed.txt`: samples that failed or are missing

**Input:**

- The SLURM log files (`slurm-*.out`)

**Output:**

- `fastq_success.txt`, `fastq_failed.txt`

Directory layout afterwards:

```bash
Project_Root/
├── SRR1/
│   ├── SRR1.sra
│   ├── SRR1_1.fastq.gz
├── SRR2/
│   ├── SRR2.sra
│   ├── SRR2_1.fastq.gz
│   ├── SRR2_2.fastq.gz
├── SRR3/
│   ├── SRR3.sra
│   ├── SRR3_1.fastq.gz
│   ├── SRR3_2.fastq.gz
│   ├── SRR3_3.fastq.gz
├── SRR4/
│   ├── SRR4.sra
│   ├── SRR4_1.fastq.gz
│   ├── SRR4_2.fastq.gz
│   ├── SRR4_3.fastq.gz
│   ├── SRR4_4.fastq.gz
├── fastq_success.txt               ← ✅ list of successful samples (e.g. SRR1\nSRR2\n...)
├── fastq_failed.txt                ← ❌ list of failed samples 
├── slurm-<jobid>.out
├── parse_fastq_logs.py
├── slurm_fastq_submit.sh
├── fastq_dump_job.sh
├── slurm_gzip_submit.sh
├── fastq_gz_job.sh
├── make_directories.sh
└── ...

```

Step 5. `move_success_dirs.sh`

```bash
#!/bin/bash

mkdir -p success

while read sample; do
  if [ -d "$sample" ]; then
    mv "$sample" success/
    echo "Moved $sample to success/"
  else
    echo "Sample $sample not found."
  fi
done < fastq_success.txt
```

**What it does:**

- Moves the sample directories listed in `fastq_success.txt` under `success/`

**Input:**

- `fastq_success.txt`
- Each `SRRxxxxxx/` directory

**Output:**

- Those directories, moved to `success/SRRxxxxxx/`

Directory layout afterwards:

```bash
Project_Root/
├── success/
│   ├── SRR1/
│   │   └── SRR1_1.fastq.gz                    ← 1 fastq
│   ├── SRR2/
│   │   ├── SRR2_1.fastq.gz                    ← 2 fastq
│   │   └── SRR2_2.fastq.gz
│   ├── SRR3/
│   │   ├── SRR3_1.fastq.gz                    ← 3 fastq
│   │   ├── SRR3_2.fastq.gz
│   │   └── SRR3_3.fastq.gz
│   ├── SRR4/
│   │   ├── SRR4_1.fastq.gz                    ← 4 fastq
│   │   ├── SRR4_2.fastq.gz
│   │   ├── SRR4_3.fastq.gz
│   │   └── SRR4_4.fastq.gz
│   ├── check_counts.sh                      ← [step 6] bucket by FASTQ count
│   ├── rename_fastq.sh                      ← [step 7] rename the files
│   ├── Snakefile_hs.smk                     ← [step 8] run Cell Ranger (Snakemake)
│   ├── run_cellranger_hs.sh                 ← [step 8] Snakemake submit script
│   └── rename_failed_fastqs.py              ← [step 9] swap R1/R2 on failed samples
├── fastq_success.txt
├── fastq_failed.txt
├── move_success_dirs.sh
└── ...
```

Step 6. **`check_counts.sh`**

```bash
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
```

What it does:

- Counts the `.fastq.gz` files each sample under `success/` has (1 to 4)
- Records the sample ID in `fastq_1_files.tsv`, `fastq_2_files.tsv`, and so on, according to that count

Directory layout afterwards:

```bash
Project_Root/
├── success/
│   ├── SRR1/
│   │   └── SRR1_1.fastq.gz                    ← 1 fastq
│   ├── SRR2/
│   │   ├── SRR2_1.fastq.gz                    ← 2 fastq
│   │   └── SRR2_2.fastq.gz
│   ├── SRR3/
│   │   ├── SRR3_1.fastq.gz                    ← 3 fastq
│   │   ├── SRR3_2.fastq.gz
│   │   └── SRR3_3.fastq.gz
│   ├── SRR4/
│   │   ├── SRR4_1.fastq.gz                    ← 4 fastq
│   │   ├── SRR4_2.fastq.gz
│   │   ├── SRR4_3.fastq.gz
│   │   └── SRR4_4.fastq.gz
│   ├── check_counts.sh                      ← [step 6] bucket by FASTQ count
│   ├── fastq_1_files.tsv                       ← SRR1
│   ├── fastq_2_files.tsv                       ← SRR2
│   ├── fastq_3_files.tsv                       ← SRR3
│   ├── fastq_4_files.tsv                       ← SRR4
│   ├── rename_fastq.sh                      ← [step 7] rename the files
│   ├── Snakefile_hs.smk                     ← [step 8] run Cell Ranger (Snakemake)
│   ├── run_cellranger_hs.sh                 ← [step 8] Snakemake submit script
│   └── rename_failed_fastqs.py              ← [step 9] swap R1/R2 on failed samples
├── fastq_success.txt
├── fastq_failed.txt
├── move_success_dirs.sh
└── ...

```

Step 7. `rename_fastq.sh`

```bash
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

```

**What it does:**

- Reads `fastq_2_files.tsv`, `fastq_3_files.tsv` and `fastq_4_files.tsv` from the previous step, and
    
    renames the `.fastq.gz` files in each sample directory to the names Cell Ranger expects (`_R1_001.fastq.gz`, `_R2_001.fastq.gz`).
    
- Where a sample has 3 or 4 files, guesses which are `R1` and `R2` from their sizes.

Directory layout afterwards:

```bash
Project_Root/
├── success/
│   ├── SRR1/
│   │   └── SRR1_1.fastq.gz                     ← only 1 file, so ignored
│   ├── SRR2/
│   │   ├── SRR2_S1_R1_001.fastq.gz             ← _1.fastq.gz → R1
│   │   └── SRR2_S1_R2_001.fastq.gz             ← _2.fastq.gz → R2
│   ├── SRR3/
│   │   ├── SRR3_S1_R1_001.fastq.gz             ← middle size → R1
│   │   ├── SRR3_S1_R2_001.fastq.gz             ← largest → R2
│   │   └── SRR3_3.fastq.gz                     ← the rest is ignored
│   ├── SRR4/
│   │   ├── SRR4_S1_R1_001.fastq.gz             ← second largest → R1
│   │   ├── SRR4_S1_R2_001.fastq.gz             ← largest → R2
│   │   ├── SRR4_3.fastq.gz                     ← the rest is ignored
│   │   └── SRR4_4.fastq.gz                     ← the rest is ignored
│   │
│   ├── check_counts.sh                      ← [step 6] bucket by FASTQ count
│   ├── fastq_1_files.tsv                       
│   ├── fastq_2_files.tsv                       
│   ├── fastq_3_files.tsv                   
│   ├── fastq_4_files.tsv                
│   ├── rename_fastq.sh                      ← [step 7] rename the files
│   ├── Snakefile_hs.smk                     ← [step 8] run Cell Ranger (Snakemake)
│   ├── run_cellranger_hs.sh                 ← [step 8] Snakemake submit script
│   └── rename_failed_fastqs.py              ← [step 9] swap R1/R2 on failed samples
│
├── fastq_success.txt                           ← samples where fastq-dump succeeded
├── fastq_failed.txt                            ← samples where fastq-dump failed
├── move_success_dirs.sh                        ← script that moves the successful samples
├── ...

```

Step 8. `run_cellranger_hs.sh` + `Snakefile_hs.smk`

`run_cellranger_hs.sh` 

```bash
#!/bin/bash
#
#SBATCH --job-name=cellranger
#SBATCH --partition=<partition>
#SBATCH --time=120:00:00
#SBATCH --mem=100G
#SBATCH --ntasks=1
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --output=/path/to/logs/%x_%j.log

source activate snakemake

# Run Snakemake
snakemake -s Snakefile_hs.smk -j 16 --latency-wait 60 --keep-going --forceall \
  --executor cluster-generic \
  --cluster-generic-submit-cmd "sbatch --partition=<partition> --ntasks=1 --nodes=1 --mem=100G --cpus-per-task=16 --time=120:00:00 --output=/path/to/logs/%x_%j.log" \
  --verbose

```

`Snakefile_hs.smk`

```python
import os

FASTQ_PATH = os.getcwd() + "/"
fastq_samples = glob_wildcards(FASTQ_PATH + "{sample_dir}/{sample_dir}_S1_R1_001.fastq.gz").sample_dir

CELLRANGER = "/path/to/cellranger-8.0.1/cellranger"
REFERENCE = "/path/to/refdata-gex-GRCh38-2024-A"

rule all:
    input:
        expand(FASTQ_PATH + "{sample_dir}/outs/possorted_genome_bam.bam", sample_dir=fastq_samples)

rule cellranger_count:
    input:
        r1=FASTQ_PATH + "{sample_dir}/{sample_dir}_S1_R1_001.fastq.gz",
        r2=FASTQ_PATH + "{sample_dir}/{sample_dir}_S1_R2_001.fastq.gz"
    output:
        bam=FASTQ_PATH + "{sample_dir}/outs/possorted_genome_bam.bam"
    log:
        error = FASTQ_PATH + "{sample_dir}/logs/{sample_dir}.log"
    shell:
        """
        mkdir -p {FASTQ_PATH}{wildcards.sample_dir}/logs
        cd {FASTQ_PATH}{wildcards.sample_dir}

        {CELLRANGER} count \
            --id=cellranger_output \
            --transcriptome={REFERENCE} \
            --fastqs=. \
            --sample={wildcards.sample_dir} \
            --localcores=16 \
            --localmem=100 \
            --create-bam=true > {log.error} 2>&1

        mkdir -p outs
        mv cellranger_output/outs/* outs/
        rm -rf cellranger_output
        """

```

**What it does:**

- Uses `Snakemake` to run `cellranger count` in parallel over every sample under `success/`
- Within each sample directory:
    - Writes the run into a temporary `cellranger_output/` directory
    - Moves just the results to `outs/` afterwards
    - Keeps the logs in `logs/`

Directory layout afterwards:

```bash
Project_Root/
├── success/
│   ├── SRR1/
│   │   └── SRR1_1.fastq.gz                   ← ignored
│
│   ├── SRR2/
│   │   ├── SRR2_S1_R1_001.fastq.gz
│   │   ├── SRR2_S1_R2_001.fastq.gz
│   │   └── outs/                             ← succeeded
│   │       ├── filtered_feature_bc_matrix/
│   │       ├── possorted_genome_bam.bam
│   │       └── ...
│
│   ├── SRR3/
│   │   ├── SRR3_S1_R1_001.fastq.gz
│   │   ├── SRR3_S1_R2_001.fastq.gz
│   │   ├── SRR3_3.fastq.gz
│   │   └── outs/                             ← succeeded
│   │       ├── filtered_feature_bc_matrix/
│   │       ├── possorted_genome_bam.bam
│   │       └── ...
│
│   ├── SRR4/
│   │   ├── SRR4_S1_R1_001.fastq.gz             
│   │   ├── SRR4_S1_R2_001.fastq.gz
│   │   ├── SRR4_3.fastq.gz
│   │   ├── SRR4_4.fastq.gz
│   │   └── cellranger_output/                  ← failed (only traces of the run)
│   │       └── [only logs and intermediate artifacts]  
│   │       (❌ no `outs/` directory = treated as a failure)
│
│   ├── check_counts.sh
│   ├── fastq_1_files.tsv
│   ├── fastq_2_files.tsv
│   ├── fastq_3_files.tsv
│   ├── fastq_4_files.tsv
│   ├── rename_fastq.sh
│   ├── Snakefile_hs.smk
│   ├── run_cellranger_hs.sh
│   └── rename_failed_fastqs.py
│
├── fastq_success.txt
├── fastq_failed.txt
├── move_success_dirs.sh
├── ...

```

Step 9. `rename_move_failed_samples.py`

```python
import os
import re
import shutil

def detect_failed_samples(base_dir="."):
    failed = []
    all_dirs = [d for d in os.listdir(base_dir)
                if os.path.isdir(os.path.join(base_dir, d)) and re.match(r"^SRR\d+$", d)]
    for sample in all_dirs:
        sample_path = os.path.join(base_dir, sample)
        if os.path.isdir(os.path.join(sample_path, "cellranger_output")) and \
           not os.path.isdir(os.path.join(sample_path, "outs")):
            failed.append(sample)
    return failed

def swap_fastq_names(sample_dir, sample_id):
    r1 = os.path.join(sample_dir, f"{sample_id}_S1_R1_001.fastq.gz")
    r2 = os.path.join(sample_dir, f"{sample_id}_S1_R2_001.fastq.gz")
    if not (os.path.isfile(r1) and os.path.isfile(r2)):
        print(f"⚠️  {sample_id}: R1 or R2 fastq file not found, skipping rename.")
        return
    temp_r1 = r1 + ".temp_swap"
    os.rename(r1, temp_r1)
    os.rename(r2, r1)
    os.rename(temp_r1, r2)
    print(f"🔁 Swapped R1 <-> R2 in {sample_id}")

def move_failed_sample(sample, base_dir=".", target_dir="./retry_failed"):
    src = os.path.join(base_dir, sample)
    dst = os.path.join(target_dir, sample)
    os.makedirs(target_dir, exist_ok=True)
    failed_output = os.path.join(src, "cellranger_output")
    if os.path.isdir(failed_output):
        shutil.rmtree(failed_output)
        print(f"🗑️   Removed cellranger_output/ from {sample}")
    if os.path.exists(dst):
        print(f"⚠️  {sample} already exists in {target_dir}, skipping move.")
        return False
    shutil.move(src, dst)
    print(f"📦 Moved {sample} → {target_dir}/")
    return True

def save_sample_list(sample_ids, output_path):
    with open(output_path, "w") as f:
        for sid in sample_ids:
            f.write(sid + "\n")
    print(f"\n📝 Saved moved sample list to: {output_path}")

def main(base_dir=".", retry_dir="./retry_failed"):
    failed_samples = detect_failed_samples(base_dir)
    if not failed_samples:
        print("✅ No failed samples found.")
        return
    print(f"🔍 Found {len(failed_samples)} failed samples. Processing...\n")
    moved_samples = []
    for sample in failed_samples:
        sample_path = os.path.join(base_dir, sample)
        swap_fastq_names(sample_path, sample)
        moved = move_failed_sample(sample, base_dir, retry_dir)
        if moved:
            moved_samples.append(sample)
    if moved_samples:
        save_sample_list(moved_samples, os.path.join(base_dir, "retry_failed_samples.txt"))
    else:
        print("⚠️  No samples were moved, nothing written to list.")
    print(f"\n✅ All failed samples processed and moved to {retry_dir}/")

if __name__ == "__main__":
    main()

```

**What it does:**

- Finds the failed samples: the ones that have a `cellranger_output/` but no `outs/`
- Swaps the R1 and R2 file names within each of them
- Moves them to `retry_failed/`, deleting the old `cellranger_output/` on the way
    - Leaving the previous run's cellranger_output in place makes the retry error out
- Writes the list of moved samples to `retry_failed_samples.txt`

Directory layout afterwards:

```bash
Project_Root/
├── success/
│   ├── SRR1/
│   │   └── SRR1_1.fastq.gz
│   ├── SRR2/
│   │   ├── SRR2_S1_R1_001.fastq.gz
│   │   ├── SRR2_S1_R2_001.fastq.gz
│   │   └── outs/
│   ├── SRR3/
│   │   ├── SRR3_S1_R1_001.fastq.gz
│   │   ├── SRR3_S1_R2_001.fastq.gz
│   │   ├── SRR3_3.fastq.gz
│   │   └── outs/
│   ├── check_counts.sh
│   ├── fastq_1_files.tsv
│   ├── fastq_2_files.tsv
│   ├── fastq_3_files.tsv
│   ├── fastq_4_files.tsv
│   ├── rename_fastq.sh
│   ├── Snakefile_hs.smk
│   ├── run_cellranger_hs.sh
│   ├── retry_failed_samples.txt                 ← 📄 list of failed samples that were moved
│   └── rename_failed_fastqs.py

├── retry_failed/                                ← 🔁 failed samples moved here
│   └── SRR4/
│       ├── SRR4_S1_R1_001.fastq.gz              ← ✅ swapped in from what was R2
│       ├── SRR4_S1_R2_001.fastq.gz              ← ✅ swapped in from what was R1
│       ├── SRR4_3.fastq.gz
│       ├── SRR4_4.fastq.gz
│       └── (cellranger_output/ has been removed)

├── fastq_success.txt
├── fastq_failed.txt
├── move_success_dirs.sh
├── ...

```

Pipeline diagram

```bash
START
  │
  ▼
[1] make_directories.sh
  └─ move each .sra file into a directory of the same name
  └─ in:  ./SRRxxxxxx
  └─ out: ./SRRxxxxxx/SRRxxxxxx
  │
  ▼
[2] slurm_fastq_submit.sh → fastq_dump_job.sh
  └─ .sra → _1.fastq, _2.fastq (fastq-dump)
  └─ in:  ./SRRxxxxxx/SRRxxxxxx
  └─ out: ./SRRxxxxxx/SRRxxxxxx_1.fastq, _2.fastq
  └─ needs: [1] the directories and sra files it created
  │
  ▼
[3] slurm_gzip_submit.sh → fastq_gz_job.sh
  └─ .fastq → .fastq.gz
  └─ in:  ./SRRxxxxxx/*fastq
  └─ out: ./SRRxxxxxx/*fastq.gz
  └─ needs: [2] the fastq-dump output
  │
  ▼
[4] parse_fastq_logs.py
  └─ parse the SLURM logs → build the succeeded/failed lists
  └─ in:  ./slurm-*.out
  └─ out: fastq_success.txt, fastq_failed.txt
  └─ needs: [3] the SLURM submission logs
  │
  ▼
[5] move_success_dirs.sh
  └─ move the successful sample directories under ./success/
  └─ in:  fastq_success.txt, ./SRRxxxxxx/
  └─ out: ./success/SRRxxxxxx/
  └─ needs: [4] the samples judged successful
  │
  ▼
[6] check_counts.sh
  └─ bucket samples by fastq.gz count
  └─ in:  ./success/SRRxxxxxx/*.fastq.gz
  └─ out: fastq_1_files.tsv .. fastq_4_files.tsv
  └─ needs: [5] the collected, compressed FASTQ files
  │
  ▼
[7] rename_fastq.sh
  └─ rename to the analysis-ready names: *_S1_R1_001.fastq.gz, etc.
  └─ in:  fastq_2_files.tsv, fastq_3_files.tsv, ...
  └─ out: ./success/SRRxxxxxx/*_S1_R[1/2]_001.fastq.gz
  └─ needs: [6] the per-sample fastq counts
  │
  ▼
[8] run_cellranger_hs.sh → Snakefile_hs.smk
  └─ run Cell Ranger in parallel via Snakemake
  └─ in:  *_S1_R1_001.fastq.gz, *_S1_R2_001.fastq.gz
  └─ out: outs/possorted_genome_bam.bam, logs/
  └─ needs: [7] the renamed FASTQ files
  │
  ▼
[9] rename_move_failed_samples.py
  └─ handle the failed samples (no outs, only cellranger_output)
      - swap the R1/R2 names
      - delete cellranger_output
      - move to retry_failed/
  └─ in:  ./success/SRRxxxxxx/cellranger_output/
  └─ out: ./retry_failed/SRRxxxxxx/, retry_failed_samples.txt
  └─ needs: [8] the samples that failed the Cell Ranger run
  │
  ▼
END

```
