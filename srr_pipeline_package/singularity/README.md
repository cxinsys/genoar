# Running Stage 3 (Cell Ranger) with Singularity

Some HPC centers (e.g. KISTI) disallow Docker and require Singularity/Apptainer.
This directory provides a Docker-free path to run the SRR pipeline (Stage 3 /
Cell Ranger) on such systems. The existing Docker workflow is unchanged. These
scripts are additional.

The batch scheduler is a separate question from the container runtime. The
handoff in `../hpc/` submits under Slurm, PBS Pro or Torque, and Slurm is the
default.

## What's here

| File | Purpose |
|------|---------|
| `build_sif.sh` | Convert the `genoar-srr:step9` Docker image into a `.sif` image |
| `run_singularity_pipeline.sh` | Run the pipeline via `singularity exec` (mirrors `make run-stage3`) |

## Why Stage 3 fits HPC

- The pipeline runs Snakemake locally (`--localcores`), so no scheduler is
  coupled to anything inside the container. The scheduler changes only the
  submit wrapper.
- The Cell Ranger binary and the reference genome are host-mounted, so they map
  to `--bind`.
- All writes go to `/work/*` (bind-mounted); the `.sif` stays read-only.
- No outbound internet is required. The `.sra` files are pre-staged (the pipeline
  does not download them).

## Workflow

### 1. Build the `.sif` (on a Linux host with Singularity/Apptainer)

Singularity does not run on macOS, and building a `.sif` usually needs root or
`--fakeroot`. If the HPC allows neither, build on another Linux host and copy the
`.sif` over.

```bash
# From the local Docker daemon (needs Docker on the build host)
srr_pipeline_package/singularity/build_sif.sh --from daemon --image genoar-srr:step9

# Or from a docker-archive tarball (no Docker daemon needed on the build host)
docker save genoar-srr:step9 -o genoar-srr_step9.tar
srr_pipeline_package/singularity/build_sif.sh --from archive --tar genoar-srr_step9.tar

# Preview the command without running it
srr_pipeline_package/singularity/build_sif.sh --dry-run
```

Output: `genoar-srr_step9.sif`.

### 2. Stage the inputs on the HPC

Compute nodes have no internet, so push these from outside via `scp` or Globus:

- `genoar-srr_step9.sif`
- `.sra` files, into an SRA input dir (e.g. `./data/sra`)
- reference genome (about 15 GB), e.g. `/scratch/<user>/ref`
- Cell Ranger install, e.g. `/scratch/<user>/cellranger`
- a `config.yaml` (copy from `srr_pipeline_package/configs/example.yaml`)

### 3. Run

Directly (on a node with Singularity):

```bash
srr_pipeline_package/singularity/run_singularity_pipeline.sh \
  --sif genoar-srr_step9.sif \
  --sra ./data/sra \
  --config ./config.yaml \
  --ref /scratch/<user>/ref \
  --cellranger /scratch/<user>/cellranger \
  --results ./results --logs ./logs

# Preview the generated command
srr_pipeline_package/singularity/run_singularity_pipeline.sh --dry-run
```

Or as a batch job. This directory carries no job template. Generate one with
`../hpc/prepare_hpc_handoff.py`, which fills the directives, the paths and the
run id from `hpc_config.yaml`:

```bash
python3 srr_pipeline_package/hpc/prepare_hpc_handoff.py \
  --sra-dir path/to/stage3_sra \
  --hpc-config hpc_config.yaml \
  --out hpc_handoff
```

The bundle holds `run_stage3.slurm` under Slurm and `run_stage3.pbs` under
`pbspro` or `torque`. It also holds the `config.yaml` and the
`run_singularity_pipeline.sh` that the job script calls, so the whole bundle
goes to the `remote_base` named in `hpc_config.yaml`. Submit it from there with
`sbatch` or `qsub`. The job script checks that the compute node sees the image,
the reference, Cell Ranger and the inputs before it starts the pipeline.

This directory once held a hand-edited `run_stage3.pbs`. It drifted from the
generated script and was removed. See [../hpc/README.md](../hpc/README.md).

## Config paths must match the bind targets

`run_singularity_pipeline.sh` binds:

| Host | Container |
|------|-----------|
| `--ref` dir | `/ref` |
| `--cellranger` dir | `/opt/cellranger` |

So your `config.yaml` should reference those container paths (this is what
`configs/example.yaml` uses):

```yaml
ref_path: /ref                              # host reference dir is bound to /ref
cellranger_bin: /opt/cellranger/cellranger  # or /opt/cellranger/bin/cellranger
```

Bind the host reference directory itself (the one containing `reference.json`,
`fasta/`, `genes/`, `star/`) to `/ref`. If the paths do not match the bind
targets, the launcher stops before starting the pipeline rather than reporting
a partial Stage 3 run as usable.
Bind the Cell Ranger installation root, not just its `bin/` directory.

## Items to confirm with the HPC

1. The scheduler and its dialect (`slurm`, `pbspro` or `torque`), and the partition or queue name
2. Singularity/Apptainer version, and how to bring in the `.sif` (registry pull, file copy, or `--fakeroot` build)
3. Whether writable `--bind` is allowed, and the scratch location and quota
4. Node memory limit (100 GB or more) and walltime cap
5. Data upload method (`scp` or Globus) for the `.sif`, `.sra`, and reference genome

## Orchestrator support (`cycle_test --runtime`)

`cycle_test` can also drive Singularity directly:

```bash
python cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5 \
  --run-stage3 --runtime singularity --sif-dir /path/to/sifs
```

`--runtime singularity` makes Stage 2 and Stage 3 run from `.sif` images in
`--sif-dir` instead of Docker. Docker remains the default and is unchanged.

The crawler (Stage 1) needs Chrome with relaxed privileges (SYS_ADMIN, seccomp)
that map to Docker. Under `--runtime singularity` it logs a warning; run it on
Docker or a permissive host rather than the HPC.

## Scope

This covers running Stage 3 on HPC (standalone scripts) and driving Singularity
from `cycle_test` (`--runtime`). Automating the cross-machine hand-off for a
split deployment (transfer, remote submit, retrieve) is handled separately in
the `../hpc/` directory.
