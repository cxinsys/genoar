# Archived Scripts

This directory holds scripts that fell out of use when the pipeline was consolidated.

## Archived files

### run_workflow.sh
- **What it did**: standalone workflow launcher
- **Replaced by**: `workflow/run.sh` (runs inside the Docker container)
- **Why**: Docker Compose and the Makefile now provide a simpler interface

### entrypoint.sh (formerly docker/entrypoint.sh)
- **What it did**: Docker container entrypoint
- **Replaced by**: `workflow/run.sh`, which is the image's `ENTRYPOINT` and the Compose `command`
- **Why**: it duplicated the workflow script; a single entry point is easier to keep correct

## How to run the pipeline now

```bash
# One-time setup
make setup

# Run the pipeline
make run

# Run with custom settings
make run PAGES=50 WORKERS=2
```

See the README.md at the repository root for details.
