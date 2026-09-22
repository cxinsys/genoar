# GENOAR Pipeline - Simplified Makefile
# A small, predictable entry point for running the pipeline.

# User settings from .env, if there is one. The leading dash means a missing
# .env is not an error. This must come before the ?= defaults below: whatever
# .env sets here counts as "already defined", so the default is skipped.
# Variables given on the command line still win over both, because Make ranks
# command-line variables above file assignments (make run PAGES=4).
# .env is also read by Docker Compose, so keep it to plain KEY=value lines.
-include .env

# Defaults, used only when neither .env nor the command line supplies a value.
PAGES ?= 100
# One worker by default. Each worker owns runs/<run_id>/workers/worker-<n>/ so
# that results, checkpoint.json, crawl_manifest.json and the browser download
# area are never shared: workers above 1 would otherwise overwrite each other's
# results and completion evidence. An aggregator writes the whole-range
# manifest only once every worker has exited
# cleanly - but the conservative default stands until parallel crawling has been
# exercised on a real multi-page run. .env and the command line both still work.
WORKERS ?= 1

# An inline comment in .env ("PAGES=47  # crawl less") leaves trailing blanks on
# the value, since Make strips the comment but not the whitespace before it.
# Trim them so the value we echo and pass on is exactly the number.
PAGES := $(strip $(PAGES))
WORKERS := $(strip $(WORKERS))

DOCKER_IMAGE = genoar:latest
SRR_DOCKER_IMAGE = genoar-srr:step9

# The Compose project. It is what keeps two runs on one host apart, and it is
# why the service pins no `container_name`: a container name is host-global and
# is not namespaced by the project, so a second `docker compose up` would
# recreate the first run's container instead of starting its own, killing a
# live crawl with exit 137. Compose derives the container name from the project
# instead, so a second run only needs a second project name:
#     make run COMPOSE_PROJECT_NAME=second
# Naming it COMPOSE_PROJECT_NAME rather than something of our own means .env
# sets it for both Make (through the -include above, so ?= leaves it alone) and
# Compose (which reads .env itself), and the two cannot drift apart.
COMPOSE_PROJECT_NAME ?= genoar
COMPOSE_PROJECT_NAME := $(strip $(COMPOSE_PROJECT_NAME))

# The project name becomes a directory name below, so it has to be one path
# segment. Checked at parse time rather than in a recipe, because a name that
# cannot be a directory is wrong for every target, not only the one being run.
# Use Compose's own safe alphabet here too.  Waiting for Compose to reject a
# name is too late for targets such as `clean`: Make expands the same value in
# filesystem paths before Docker is involved.
ifeq ($(COMPOSE_PROJECT_NAME),)
$(error COMPOSE_PROJECT_NAME is empty. It names this run's Compose project and its output directory, so it needs a value: make run COMPOSE_PROJECT_NAME=second)
endif
ifneq ($(words $(COMPOSE_PROJECT_NAME)),1)
$(error COMPOSE_PROJECT_NAME='$(COMPOSE_PROJECT_NAME)' contains whitespace. It becomes a directory name, so it has to be a single word)
endif
ifneq ($(findstring /,$(COMPOSE_PROJECT_NAME)),)
$(error COMPOSE_PROJECT_NAME='$(COMPOSE_PROJECT_NAME)' contains '/'. It becomes a directory name under projects/, so it has to be one path segment)
endif
ifneq ($(filter .% -% _%,$(COMPOSE_PROJECT_NAME)),)
$(error COMPOSE_PROJECT_NAME='$(COMPOSE_PROJECT_NAME)' must start with a lowercase letter or digit)
endif

# GNU Make has no character-class predicate, so remove every permitted
# character and reject the name if anything remains.  This is intentionally
# shell-free: validating a value by interpolating it into a shell command would
# itself make characters such as ';', '$(' or backticks executable.
COMPOSE_PROJECT_NAME_INVALID := $(COMPOSE_PROJECT_NAME)
COMPOSE_PROJECT_NAME_INVALID := $(subst a,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst b,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst c,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst d,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst e,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst f,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst g,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst h,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst i,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst j,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst k,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst l,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst m,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst n,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst o,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst p,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst q,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst r,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst s,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst t,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst u,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst v,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst w,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst x,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst y,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst z,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 0,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 1,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 2,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 3,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 4,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 5,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 6,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 7,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 8,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst 9,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst _,,$(COMPOSE_PROJECT_NAME_INVALID))
COMPOSE_PROJECT_NAME_INVALID := $(subst -,,$(COMPOSE_PROJECT_NAME_INVALID))
ifneq ($(COMPOSE_PROJECT_NAME_INVALID),)
$(error COMPOSE_PROJECT_NAME='$(COMPOSE_PROJECT_NAME)' contains unsupported characters. Use only lowercase letters, digits, '-' and '_')
endif

COMPOSE = docker compose -p $(COMPOSE_PROJECT_NAME)
SRR_CONFIG = srr_pipeline_package/configs/example.yaml

# Where this project's results go, and the reason there is a variable at all.
#
# Removing `container_name:` and the pinned network name made the containers
# per-project, and this Makefile documented the consequence: run a second
# pipeline by giving it a second project name. That was true of the containers
# and false of their results - docker-compose.yml bind-mounted the same
# crawl_output/, first_pass_output/, logs/ and workflow_output/ out of the
# checkout whatever the project was called, so the second run shared the first's
# run directories, its top-level crawl_manifest.json (which every run deletes as
# it starts) and its analysis tables. Neither run was at fault and neither could
# tell, which made the documented instruction an instruction to corrupt a run.
#
# So each project gets its own output root, and `make run` is what supplies it -
# the documented command names only the project, so the project name is all the
# isolation may depend on. The alternative was to refuse concurrent runs from
# one checkout, which is honest but retracts something the project has already
# published, and the isolation costs four bind mounts under one variable.
#
# The default project keeps the checkout root exactly. Everything else in the
# repository names crawl_output/ there: the Snakefile, fetch-sra below, the
# crawler's own default output directory, both READMEs. Only a second,
# deliberately named project moves.
#
# Stage 3 (run-stage3, fetch-sra's sample_sra/, results/) is not project-scoped:
# it reads .sra files and writes Cell Ranger output, neither of which goes
# through Compose. Two projects running Stage 3 at once still collide there.
OUTPUT_ROOT := $(if $(filter-out genoar,$(COMPOSE_PROJECT_NAME)),./projects/$(COMPOSE_PROJECT_NAME),.)
CRAWL_OUTPUT := $(OUTPUT_ROOT)/crawl_output
FIRST_PASS_OUTPUT := $(OUTPUT_ROOT)/first_pass_output
LOGS_DIR := $(OUTPUT_ROOT)/logs
WORKFLOW_OUTPUT := $(OUTPUT_ROOT)/workflow_output

# Stage 2 -> Stage 3 bridge (see the fetch-sra target). The raw Stage 1 lists
# remain available only through an explicit SRA_SOURCE=raw-stage1 opt-in.
SRR_DIR = $(CRAWL_OUTPUT)/SRR
SRA_DIR = sample_sra
SELECTED_SRA_DIR ?= $(SRA_DIR)/.genoar_selected
STAGE3_RESULTS ?= results
SRA_SOURCE ?= stage2
# A full SRA download is enormous, so fetch-sra is capped by default.
# Raise it deliberately: make fetch-sra MAX_SAMPLES=20, or MAX_SAMPLES=all.
MAX_SAMPLES ?= 2
MAX_CONCURRENT ?= 4
MAX_SAMPLES := $(strip $(MAX_SAMPLES))
MAX_CONCURRENT := $(strip $(MAX_CONCURRENT))
SRA_SOURCE := $(strip $(SRA_SOURCE))

# Colors (for status tags).
# Print these with `printf '%b\n' "..."`, never `echo -e`. Make runs recipes
# with /bin/sh, which is dash on Ubuntu and in the Docker images, and bash in
# POSIX mode on macOS; neither builtin echo accepts -e, so both print the flag
# literally ("-e [0;34m[RUN]..."). Same for `echo -n`: use `printf '%s'`.
# printf is a shell builtin everywhere and renders identically on all three.
# The message goes in the %b argument, not the format string, so a literal %
# in a message stays a literal %.
BLUE = \033[0;34m
GREEN = \033[0;32m
YELLOW = \033[1;33m
RED = \033[0;31m
NC = \033[0m

# Default target
.DEFAULT_GOAL := help

# Help
.PHONY: help
help: ## Show the available commands
	@echo "GENOAR Pipeline runner"
	@echo "======================"
	@echo ""
	@echo "Usage:"
	@echo "  make [target] [options]"
	@echo ""
	@echo "Targets:"
	@echo "  make setup      - First-time setup (create directories, build the Docker image)"
	@echo "  make run        - Run the pipeline (Stage 1+2: crawl + analyze)"
	@echo "  make fetch-sra  - Download SRA files selected by Stage 2"
	@echo "  make build-srr  - Build the Stage 3 Docker image"
	@echo "  make run-stage3 - Run Stage 3 (SRR pipeline + Cell Ranger)"
	@echo "  make run-fetched-stage3 - Run Stage 3 on exactly the latest fetch-sra selection"
	@echo "  make run-full   - Run the whole pipeline (Stage 1+2+3)"
	@echo "  make logs       - Follow this run's container log"
	@echo "  make status     - Show run status and results"
	@echo "  make check-resources - Report this machine's CPU/memory (part of setup)"
	@echo "  make clean      - Remove output files"
	@echo "  make help       - Show this help"
	@echo ""
	@echo "Options (settable in .env or on the command line):"
	@echo "  PAGES=N          - Number of GEO pages to crawl (default: 100)"
	@echo "  WORKERS=N        - Number of parallel crawl workers (default: 1)"
	@echo "                     Defaults to 1; parallel crawling is run-scoped but newly reworked."
	@echo "  MAX_SAMPLES=N    - SRA files fetch-sra downloads, or 'all' (default: 2)"
	@echo "  MAX_CONCURRENT=N - Parallel downloads for fetch-sra (default: 4)"
	@echo "  SRA_SOURCE=X     - Accession source for fetch-sra: stage2 (default), or"
	@echo "                     raw-stage1 as an explicit unfiltered opt-in"
	@echo "  DRY_RUN=1        - fetch-sra lists what it would download and stops"
	@echo "  COMPOSE_PROJECT_NAME=X - Name this run's Compose project (default: genoar)."
	@echo "                     Give a second, concurrent run its own name. The two"
	@echo "                     then have separate containers, separate networks and"
	@echo "                     separate result directories: the default project"
	@echo "                     writes to ./crawl_output/ and the rest of the"
	@echo "                     checkout root, any other project to ./projects/<name>/."
	@echo "                     Pass the same name to make status/logs/clean to ask"
	@echo "                     about that run. Stage 3 (run-stage3) is not scoped"
	@echo "                     this way and is still one at a time per checkout."
	@echo ""
	@echo "This project: $(COMPOSE_PROJECT_NAME)   results under: $(OUTPUT_ROOT)/"
	@echo ""
	@echo "Watching a run (there is no fixed container name any more):"
	@echo "  make logs        - Follow this project's pipeline log"
	@echo "  docker compose -p $(COMPOSE_PROJECT_NAME) logs -f genoar"
	@echo "  docker compose -p $(COMPOSE_PROJECT_NAME) ps"
	@echo ""
	@echo "Examples:"
	@echo "  make setup"
	@echo "  make run"
	@echo "  make run PAGES=50"
	@echo "  make run COMPOSE_PROJECT_NAME=second   (a second run, side by side,"
	@echo "                                          writing to ./projects/second/)"
	@echo "  make status COMPOSE_PROJECT_NAME=second"
	@echo "  make fetch-sra MAX_SAMPLES=10"
	@echo "  make status"

# First-time setup
.PHONY: setup
setup: ## First-time setup - create directories and build the Docker image
	@printf '%b\n' "$(BLUE)[SETUP]$(NC) Starting first-time setup..."
	@# Create the directories the pipeline expects, under this project's own
	@# output root. all_query_results is an input and is shared by every
	@# project, so it stays in the checkout root.
	@mkdir -p $(CRAWL_OUTPUT)/META $(CRAWL_OUTPUT)/SMTX $(CRAWL_OUTPUT)/SRR
	@mkdir -p $(FIRST_PASS_OUTPUT)
	@mkdir -p $(LOGS_DIR)
	@mkdir -p $(WORKFLOW_OUTPUT)
	@mkdir -p all_query_results
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Directory layout created under $(OUTPUT_ROOT)/"
	@# Build the Docker image
	@printf '%b\n' "$(BLUE)[BUILD]$(NC) Building the Docker image..."
	@docker build -t $(DOCKER_IMAGE) .
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Docker image built: $(DOCKER_IMAGE)"
	@# Sanity-check the Compose file
	@printf '%b\n' "$(BLUE)[CHECK]$(NC) Validating the Docker Compose configuration..."
	@$(COMPOSE) config --quiet
	@# Report the machine we are on
	@echo ""
	@$(MAKE) --no-print-directory check-resources
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Setup finished."

# Resource check. Its own target so it can be run - and checked - on its own,
# on whatever platform the user is actually on: make check-resources
.PHONY: check-resources
check-resources: ## Report CPU/memory and warn when they are short
	@printf '%b\n' "$(BLUE)[SYSTEM]$(NC) Checking resources..."
	@# nproc and free are Linux-only. On macOS they are simply not there, so the
	@# old `nproc || echo 0` reported "CPU cores: 0" and then warned about the
	@# zero it had invented, and `free -m | awk ... || echo 0` never reached its
	@# fallback at all: `||` binds to the whole pipeline, whose status is awk's,
	@# and awk succeeds on empty input. MEM_MB came out empty, `[ "" -lt 8192 ]`
	@# errored into 2>/dev/null, and the low-memory warning could not fire on any
	@# platform without `free`. Each probe below is its own command with its own
	@# fallback, /proc/meminfo is read directly (procps is absent from slim
	@# images), and a probe that yields nothing usable prints "unknown" instead of
	@# a number nobody measured.
	@CPU_CORES=$$( { nproc || sysctl -n hw.ncpu || getconf _NPROCESSORS_ONLN; } 2>/dev/null ); \
	case "$$CPU_CORES" in ''|*[!0-9]*|0) CPU_CORES="" ;; esac; \
	MEM_MB=$$( { awk '/^MemTotal:/ {printf "%d", $$2 / 1024; exit}' /proc/meminfo; } 2>/dev/null ); \
	if [ -z "$$MEM_MB" ]; then \
		MEM_MB=$$( { sysctl -n hw.memsize | awk 'NF {printf "%d", $$1 / 1048576; exit}'; } 2>/dev/null ); \
	fi; \
	if [ -z "$$MEM_MB" ]; then \
		MEM_MB=$$( { free -m | awk '/^Mem:/ {print $$2; exit}'; } 2>/dev/null ); \
	fi; \
	case "$$MEM_MB" in ''|*[!0-9]*|0) MEM_MB="" ;; esac; \
	echo "  - CPU cores: $${CPU_CORES:-unknown}"; \
	if [ -n "$$MEM_MB" ]; then \
		echo "  - Memory:    $${MEM_MB}MB"; \
	else \
		echo "  - Memory:    unknown"; \
	fi; \
	if [ -n "$$CPU_CORES" ] && [ "$$CPU_CORES" -lt 4 ]; then \
		printf '%b\n' "$(YELLOW)[WARN]$(NC) Fewer than 4 CPU cores ($$CPU_CORES); the pipeline will be slow."; \
	fi; \
	if [ -n "$$MEM_MB" ] && [ "$$MEM_MB" -lt 8192 ]; then \
		printf '%b\n' "$(YELLOW)[WARN]$(NC) Less than 8GB of memory ($${MEM_MB}MB); Stage 3 may not run reliably."; \
	fi; \
	if [ -z "$$CPU_CORES" ] || [ -z "$$MEM_MB" ]; then \
		printf '%b\n' "$(BLUE)[NOTE]$(NC) An \"unknown\" above means this platform answered no probe we know; nothing is wrong with the machine, and that value was not checked. The pipeline wants 4+ cores and 8GB+."; \
	fi

# Run the pipeline
.PHONY: run
run: ## Run the pipeline (crawl + analyze)
	@printf '%b\n' "$(BLUE)[RUN]$(NC) Starting the GENOAR pipeline..."
	@printf '%b\n' "$(BLUE)[INFO]$(NC) Settings: PAGES=$(PAGES), WORKERS=$(WORKERS), COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT_NAME)"
	@printf '%b\n' "$(BLUE)[INFO]$(NC) Results go to $(OUTPUT_ROOT)/ - crawl_output/, first_pass_output/, logs/, workflow_output/"
	@# The container has no fixed name, so say how to reach this one. A pinned
	@# name would be host-global, and a second run under it would recreate the
	@# first run's container mid-crawl.
	@printf '%b\n' "$(BLUE)[INFO]$(NC) Follow this run:  docker compose -p $(COMPOSE_PROJECT_NAME) logs -f genoar   (or: make logs)"
	@printf '%b\n' "$(BLUE)[INFO]$(NC) A second, concurrent run needs its own project: make run COMPOSE_PROJECT_NAME=second (its results land in ./projects/second/)"
	@# Two runs of *this* project are the case the project name cannot separate:
	@# same containers, same output root, same run directories. Compose would
	@# recreate the running container rather than start a second one - which is
	@# how a live 90-minute crawl died with exit 137 - so this refuses instead.
	@running=$$($(COMPOSE) ps -q genoar 2>/dev/null); \
	if [ -n "$$running" ]; then \
		printf '%b\n' "$(RED)[BUSY]$(NC) Project '$(COMPOSE_PROJECT_NAME)' is already running here."; \
		printf '%b\n' "$(RED)[BUSY]$(NC) A second run of the same project shares its container, its output root ($(OUTPUT_ROOT)/) and its run directories, and Compose would recreate the running container rather than start another."; \
		printf '%b\n' "$(RED)[BUSY]$(NC) Give this one its own project:  make run COMPOSE_PROJECT_NAME=second"; \
		printf '%b\n' "$(RED)[BUSY]$(NC) Or watch the one that is running:  make logs"; \
		exit 2; \
	fi
	@# Create this project's output tree before Compose does. A bind mount whose
	@# host directory does not exist is created by the daemon, and on Linux it
	@# is created owned by root, which the next `make clean` then cannot remove.
	@mkdir -p $(CRAWL_OUTPUT)/META $(CRAWL_OUTPUT)/SMTX $(CRAWL_OUTPUT)/SRR \
		$(FIRST_PASS_OUTPUT) $(LOGS_DIR) $(WORKFLOW_OUTPUT)
	@# Say what asking for more than one crawl worker currently costs.
	@if [ "$(WORKERS)" != "1" ]; then \
		printf '%b\n' "$(YELLOW)[NOTE]$(NC) WORKERS=$(WORKERS): each worker gets its own run directory, checkpoint and downloads, and the whole-range manifest is written only after every worker exits cleanly. The default is 1 because that path is newly reworked - check runs/<run_id>/ afterwards."; \
	fi
	@# Build the image first if it is missing. Ask docker for repository:tag
	@# rather than grepping the table, whose columns differ between CLI versions.
	@if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -qx '$(DOCKER_IMAGE)'; then \
		printf '%b\n' "$(YELLOW)[WARN]$(NC) No Docker image found. Building it now..."; \
		make setup; \
	fi
	@# Hand off to Docker Compose.
	@#
	@# --exit-code-from genoar (which implies --abort-on-container-exit) is what
	@# makes a failing container a failing command: plain `docker compose up`
	@# returns 0 even when the service exits non-zero, so the workflow could
	@# fail correctly inside and still be reported as a successful run here.
	@#
	@# GNU Make turns a failed recipe into its own exit status, so `make run`
	@# promises only success = 0 / failure = non-zero, not the container's exact
	@# code. The code itself is printed below so it is not lost.
	@#
	@# -p pins the Compose project, which is what now separates one run from
	@# another. --remove-orphans only ever removes containers of *this*
	@# project, so with the project pinned it cannot reach a run started under
	@# another name.
	@# GENOAR_OUTPUT_ROOT is what the compose file's bind mounts hang off, and
	@# it is derived from the project name above, so `make run
	@# COMPOSE_PROJECT_NAME=second` is enough on its own - a second variable the
	@# user had to remember would leave the documented instruction untrue.
	@rc=0; \
	PAGES=$(PAGES) WORKERS=$(WORKERS) GENOAR_OUTPUT_ROOT=$(OUTPUT_ROOT) $(COMPOSE) up \
		--abort-on-container-exit --exit-code-from genoar --remove-orphans || rc=$$?; \
	if [ "$$rc" -ne 0 ]; then \
		printf '%b\n' "$(RED)[FAILED]$(NC) Pipeline run failed: the genoar container exited $$rc."; \
		printf '%b\n' "$(RED)[FAILED]$(NC) No results summary follows, because this run did not succeed. Check the container output above and $(LOGS_DIR)/."; \
		printf '%b\n' "$(RED)[FAILED]$(NC) Full log: docker compose -p $(COMPOSE_PROJECT_NAME) logs genoar"; \
		exit $$rc; \
	fi; \
	printf '%b\n' "$(GREEN)[DONE]$(NC) Pipeline run finished."
	@# Summarize the results
	@make --no-print-directory status

# Follow the running pipeline's log.
#
# This target exists because removing `container_name: genoar-pipeline` removed
# the command people had memorised. `docker logs genoar-pipeline` worked only
# because the name was host-global, which is the defect: a second run of that
# name recreated the first's container. Compose addresses the container by
# project and service instead, and that is a mouthful, so here it is once.
.PHONY: logs
logs: ## Follow this project's pipeline container log
	@printf '%b\n' "$(BLUE)[LOGS]$(NC) Following project '$(COMPOSE_PROJECT_NAME)', service 'genoar'. Ctrl+C stops watching, not the run."
	@$(COMPOSE) logs -f genoar

# Status
.PHONY: status
status: ## Show pipeline run status and results
	@printf '%b\n' "$(BLUE)[STATUS]$(NC) GENOAR pipeline status"
	@echo "=============================="
	@echo ""
	@# Which run this is about. Counting the default project's files after
	@# `make run COMPOSE_PROJECT_NAME=second` would report somebody else's
	@# results as this project's, which is the same class of false evidence as
	@# counting an earlier run's downloads as this run's.
	@echo "Project: $(COMPOSE_PROJECT_NAME)   (results under $(OUTPUT_ROOT)/)"
	@echo ""
	@# Crawled data
	@echo "Crawl results:"
	@printf '%s' "  - META files: "
	@find $(CRAWL_OUTPUT)/META -name "*.txt" 2>/dev/null | wc -l | xargs echo
	@printf '%s' "  - SMTX files: "
	@find $(CRAWL_OUTPUT)/SMTX -name "*.gz" 2>/dev/null | wc -l | xargs echo
	@printf '%s' "  - SRR files:  "
	@find $(CRAWL_OUTPUT)/SRR -name "*.txt" 2>/dev/null | wc -l | xargs echo
	@echo ""
	@# Analysis results
	@echo "Analysis results:"
	@if [ -f "$(FIRST_PASS_OUTPUT)/HS_cell_type_1st_pass_meta_table.csv" ]; then \
		rows=$$(tail -n +2 "$(FIRST_PASS_OUTPUT)/HS_cell_type_1st_pass_meta_table.csv" | wc -l); \
		echo "  - Cell type table: $$rows rows"; \
	else \
		echo "  - Cell type table: not generated"; \
	fi
	@if [ -f "$(FIRST_PASS_OUTPUT)/HS_tissue_1st_pass_meta_table.csv" ]; then \
		rows=$$(tail -n +2 "$(FIRST_PASS_OUTPUT)/HS_tissue_1st_pass_meta_table.csv" | wc -l); \
		echo "  - Tissue table: $$rows rows"; \
	else \
		echo "  - Tissue table: not generated"; \
	fi
	@if [ -f "$(FIRST_PASS_OUTPUT)/HS_disease_1st_pass_meta_table.csv" ]; then \
		rows=$$(tail -n +2 "$(FIRST_PASS_OUTPUT)/HS_disease_1st_pass_meta_table.csv" | wc -l); \
		echo "  - Disease table: $$rows rows"; \
	else \
		echo "  - Disease table: not generated"; \
	fi
	@echo ""
	@# Stage 3 owns its own run records outside the Compose project's output
	@# root. Surface the newest authoritative outcome without making status
	@# depend on jq or treating a missing/malformed record as a success.
	@python3 scripts/report_stage3_status.py --results-dir "$(STAGE3_RESULTS)"
	@echo ""
	@# Recent log output
	@if [ -d "$(LOGS_DIR)" ] && [ "$$(ls -A $(LOGS_DIR) 2>/dev/null)" ]; then \
		echo "Recent logs:"; \
		find $(LOGS_DIR) -name "*.log" -type f -exec tail -n 3 {} \; 2>/dev/null | head -n 10; \
	fi

# Clean up
.PHONY: clean
clean: ## Remove all output files (after taking a backup)
	@printf '%b\n' "$(YELLOW)[CLEAN]$(NC) Removing output files for project '$(COMPOSE_PROJECT_NAME)' ($(OUTPUT_ROOT)/)..."
	@# Back up first, and stop if that fails. A full disk is exactly when a
	@# cleanup is wanted and exactly when the copy cannot complete; deleting
	@# anyway would destroy the results the backup was meant to keep.
	@if [ -d "$(CRAWL_OUTPUT)" ] && [ "$$(ls -A $(CRAWL_OUTPUT))" ]; then \
		backup_dir="$(OUTPUT_ROOT)/backup_$$(date +%Y%m%d_%H%M%S)"; \
		printf '%b\n' "$(BLUE)[BACKUP]$(NC) Writing backup to $$backup_dir"; \
		mkdir -p "$$backup_dir" || { \
			printf '%b\n' "$(RED)[ERROR]$(NC) Could not create $$backup_dir. Nothing was removed."; \
			exit 1; \
		}; \
		cp -r $(CRAWL_OUTPUT) $(FIRST_PASS_OUTPUT) $(LOGS_DIR) "$$backup_dir/" || { \
			printf '%b\n' "$(RED)[ERROR]$(NC) Backup to $$backup_dir failed. Nothing was removed."; \
			printf '%b\n' "$(RED)[ERROR]$(NC) Check free disk space and permissions, then run 'make clean' again."; \
			exit 1; \
		}; \
	fi
	@# Remove the outputs. Only this project's: another project's tree is
	@# another run's results, and `make clean` has never been allowed to reach
	@# them.
	@# A removal that fails leaves output behind, so say so rather than
	@# printing [DONE] over it.
	@rm -rf $(CRAWL_OUTPUT)/* $(FIRST_PASS_OUTPUT)/* $(LOGS_DIR)/* $(WORKFLOW_OUTPUT)/* || { \
		printf '%b\n' "$(RED)[ERROR]$(NC) Some output could not be removed; it is still there. The backup above is intact."; \
		exit 1; \
	}
	@# Put the empty directory layout back
	@mkdir -p $(CRAWL_OUTPUT)/META $(CRAWL_OUTPUT)/SMTX $(CRAWL_OUTPUT)/SRR
	@mkdir -p $(FIRST_PASS_OUTPUT) $(LOGS_DIR) $(WORKFLOW_OUTPUT)
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Cleanup finished."

# Quick smoke test (10 pages, 1 worker)
.PHONY: test
test: ## Quick test run (small amount of data)
	@printf '%b\n' "$(BLUE)[TEST]$(NC) Running in test mode (10 pages, 1 worker)..."
	@make run PAGES=10 WORKERS=1

# Stage 2 -> Stage 3 bridge: turn filtered SRR accessions into local .sra files
.PHONY: fetch-sra
fetch-sra: ## Download SRA files for the accessions Stage 2 selected
	@printf '%b\n' "$(BLUE)[RUN]$(NC) Fetching SRA files for source '$(SRA_SOURCE)'..."
	@printf '%b\n' "$(BLUE)[INFO]$(NC) Settings: MAX_SAMPLES=$(MAX_SAMPLES), MAX_CONCURRENT=$(MAX_CONCURRENT)"
	@# --work-dir keeps the merged accession list inside this project's own
	@# crawl_output, next to the per-GSE lists it was merged from. Its default
	@# is the checkout root's, which a second project would then share with the
	@# first while reading a different set of accessions.
	@python3 scripts/fetch_sra.py \
		--source $(SRA_SOURCE) \
		--stage2-dir $(FIRST_PASS_OUTPUT) \
		--srr-dir $(SRR_DIR) \
		--output-dir $(SRA_DIR) \
		--selected-dir $(SELECTED_SRA_DIR) \
		--work-dir $(CRAWL_OUTPUT)/stage3_prep \
		--max-samples $(MAX_SAMPLES) \
		--max-concurrent $(MAX_CONCURRENT) \
		$(if $(DRY_RUN),--dry-run,)
	@if [ -n "$(DRY_RUN)" ]; then \
		printf '%b\n' "$(GREEN)[DONE]$(NC) Dry run complete; downloads and the prior Stage 3 selection were left unchanged."; \
	else \
		printf '%b\n' "$(GREEN)[DONE]$(NC) Downloads are cached in $(SRA_DIR)/; this selection alone is in $(SELECTED_SRA_DIR)/."; \
		printf '%b\n' "$(BLUE)[NEXT]$(NC) Run it with: make run-fetched-stage3"; \
	fi

# Stage 3: build the SRR pipeline Docker image
.PHONY: build-srr
build-srr: ## Build the Stage 3 Docker image
	@printf '%b\n' "$(BLUE)[BUILD]$(NC) Building the Stage 3 (SRR pipeline) Docker image..."
	@docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t $(SRR_DOCKER_IMAGE) .
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Stage 3 Docker image built: $(SRR_DOCKER_IMAGE)"

# The Stage 3 host prerequisites that cost nothing to check and hours to
# discover late: Cell Ranger and the reference genome. Its own target so
# run-full can ask before Stage 1 rather than after a crawl and a download.
.PHONY: check-stage3-tools
check-stage3-tools: ## Check the Stage 3 host prerequisites (architecture, Cell Ranger, reference)
	@# Architecture. Cell Ranger is distributed as an x86_64 binary only, so
	@# every check below passes on an ARM host - the launcher is there and it is
	@# executable - and the run only fails inside the container at step 0, after
	@# the crawl and the downloads. Ask first. GENOAR_ALLOW_NON_X86=1 is the way
	@# through for a host that runs the binary under emulation (Rosetta, qemu)
	@# and has confirmed the launcher reports a version for itself.
	@if [ -z "$(GENOAR_ALLOW_NON_X86)" ]; then \
		arch=$$(uname -m); \
		case "$$arch" in \
			x86_64|amd64) ;; \
			*) \
				printf '%b\n' "$(RED)[ERROR]$(NC) Stage 3 needs an x86_64 host. This one is $$arch."; \
				echo "  Cell Ranger is not distributed for ARM, and the launcher in cellranger/"; \
				echo "  is an x86_64 binary: it exists and it is executable here, but it cannot"; \
				echo "  run. Stage 3 belongs on a Linux or Windows x86_64 machine."; \
				echo "  Running it under emulation? Check that this works first:"; \
				echo "    ./cellranger/cellranger --version"; \
				echo "  then re-run with GENOAR_ALLOW_NON_X86=1."; \
				exit 1;; \
		esac; \
	fi
	@# Cell Ranger. The complete ./cellranger installation is mounted at
	@# /opt/cellranger. Releases place the launcher either at the installation
	@# root or under bin/; the pipeline supports both, so this host preflight
	@# must accept the same two layouts. A directory that merely exists (empty,
	@# or still holding the tarball) must still fail before Docker starts.
	@if [ ! -d "cellranger" ]; then \
		printf '%b\n' "$(RED)[ERROR]$(NC) No cellranger/ directory. Install Cell Ranger first."; \
		echo "  https://www.10xgenomics.com/support/software/cell-ranger/downloads"; \
		echo "  Extract the complete installation as ./cellranger/."; \
		echo "    tar -xzf cellranger-*.tar.gz && mv cellranger-*/ cellranger"; \
		exit 1; \
	fi
	@cr_bin=""; \
	for candidate in cellranger/cellranger cellranger/bin/cellranger; do \
		if [ -x "$$candidate" ]; then cr_bin="$$candidate"; break; fi; \
	done; \
	if [ -z "$$cr_bin" ]; then \
		present=""; \
		for candidate in cellranger/cellranger cellranger/bin/cellranger; do \
			if [ -f "$$candidate" ]; then present="$$candidate"; break; fi; \
		done; \
		if [ -n "$$present" ]; then \
			printf '%b\n' "$(RED)[ERROR]$(NC) $$present exists but is not executable."; \
			echo "  Fix it with: chmod +x $$present"; \
		else \
			printf '%b\n' "$(RED)[ERROR]$(NC) No Cell Ranger launcher found in cellranger/."; \
			echo "  Expected either ./cellranger/cellranger or ./cellranger/bin/cellranger."; \
			held=$$(ls -A cellranger 2>/dev/null | head -n 5 | tr '\n' ' '); \
			echo "  cellranger/ currently holds: $${held:-(nothing - the directory is empty)}"; \
			echo "  Extract the complete installation at cellranger/:"; \
			echo "    tar -xzf cellranger-*.tar.gz && mv cellranger-*/ cellranger"; \
		fi; \
		exit 1; \
	fi
	@# Reference genome. ./ref is mounted at /ref and handed to cellranger count
	@# as --transcriptome, which needs the reference laid out at the top of that
	@# directory, not one level down inside refdata-gex-*/.
	@if [ ! -d "ref" ]; then \
		printf '%b\n' "$(RED)[ERROR]$(NC) No ref/ directory. Download the reference genome first."; \
		echo "  wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz"; \
		echo "  tar -xzf refdata-gex-GRCh38-2024-A.tar.gz && mv refdata-gex-GRCh38-2024-A ref"; \
		exit 1; \
	fi
	@missing=""; \
	[ -s "ref/reference.json" ] || missing="$$missing reference.json(empty or absent)"; \
	for d in fasta genes star; do \
		[ -d "ref/$$d" ] || missing="$$missing $$d/"; \
	done; \
	if [ -n "$$missing" ]; then \
		printf '%b\n' "$(RED)[ERROR]$(NC) ref/ is not a usable Cell Ranger reference. Missing:$$missing"; \
		echo "  A complete reference has reference.json, fasta/, genes/ and star/ at its top level."; \
		held=$$(ls -A ref 2>/dev/null | head -n 5 | tr '\n' ' '); \
		echo "  ref/ currently holds: $${held:-(nothing - the directory is empty)}"; \
		echo "  If the tarball is still packed, or extracted one level down, flatten it:"; \
		echo "    tar -xzf refdata-gex-GRCh38-2024-A.tar.gz && mv refdata-gex-GRCh38-2024-A ref"; \
		exit 1; \
	fi

# Stage 3: run the SRR pipeline
.PHONY: run-stage3
run-stage3: check-stage3-tools ## Run Stage 3 (SRR pipeline + Cell Ranger)
	@printf '%b\n' "$(BLUE)[RUN]$(NC) Starting Stage 3 (SRR pipeline)..."
	@# Check the prerequisites
	@if [ ! -d "$(SRA_DIR)" ] || [ -z "$$(ls -A $(SRA_DIR) 2>/dev/null)" ]; then \
		printf '%b\n' "$(RED)[ERROR]$(NC) No SRA files in $(SRA_DIR)/."; \
		echo "  Run 'make fetch-sra' to download the accessions Stage 2 selected,"; \
		echo "  or drop your own .sra files into $(SRA_DIR)/."; \
		exit 1; \
	fi
	@if [ ! -f "$(SRR_CONFIG)" ]; then \
		printf '%b\n' "$(RED)[ERROR]$(NC) Config file not found: $(SRR_CONFIG)"; \
		exit 1; \
	fi
	@# Build the image first if it is missing
	@if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -qx '$(SRR_DOCKER_IMAGE)'; then \
		printf '%b\n' "$(YELLOW)[WARN]$(NC) No Docker image found. Building it now..."; \
		make build-srr; \
	fi
	@# Output directories
	@mkdir -p logs results
	@# Allocate a TTY only when there is one. "-it" is fatal in a cron job, a CI
	@# step or any non-interactive shell ("cannot attach stdin to a TTY-enabled
	@# container"), which is what automating Stage 3 requires.
	docker run --rm $$(test -t 0 && test -t 1 && printf '%s' '-it') \
		-v $$(pwd)/$(SRA_DIR):/work/data/sra \
		-v $$(pwd)/$(SRR_CONFIG):/work/config.yaml \
		-v $$(pwd)/ref:/ref \
		-v $$(pwd)/cellranger:/opt/cellranger \
		-v $$(pwd)/logs:/work/logs \
		-v $$(pwd)/results:/work/results \
		$(SRR_DOCKER_IMAGE)
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Stage 3 run finished."

# Stage 3 over the exact accession set chosen by the most recent fetch-sra.
# Direct `make run-stage3` deliberately keeps its old contract and processes
# user-managed inputs under sample_sra/. This target is the safe pipeline seam:
# stale raw/custom downloads remain cached but are not mounted as input.
.PHONY: run-fetched-stage3
run-fetched-stage3: ## Run Stage 3 on exactly the latest fetch-sra selection
	@python3 scripts/validate_sra_view.py \
		--source-dir $(SRA_DIR) \
		--view-dir $(SELECTED_SRA_DIR)
	@make --no-print-directory run-stage3 SRA_DIR=$(SELECTED_SRA_DIR)

# Whole pipeline (Stage 1+2+3)
.PHONY: run-full
run-full: ## Run the whole pipeline (Stage 1+2+3)
	@if [ -n "$(DRY_RUN)" ]; then \
		printf '%b\n' "$(RED)[ERROR]$(NC) run-full does not support DRY_RUN: fetch-sra dry runs preserve the prior Stage 3 selection."; \
		echo "  Refusing before Stage 1 so an earlier selection cannot be reused."; \
		echo "  Preview downloads with 'make fetch-sra DRY_RUN=1', then run 'make run-full' without DRY_RUN."; \
		exit 2; \
	fi
	@# Ask for Cell Ranger and the reference before Stage 1, not after it: they
	@# are Stage 3's prerequisites, but a machine without them would otherwise
	@# crawl for an hour and download gigabytes before hearing so.
	@make --no-print-directory check-stage3-tools
	@printf '%b\n' "$(BLUE)[RUN]$(NC) Starting the whole pipeline (Stage 1+2+3)..."
	@make --no-print-directory run
	@# This is the actual Stage 2 -> Stage 3 handoff. It is bounded by
	@# MAX_SAMPLES=2 unless the caller deliberately raises or removes the cap.
	@make --no-print-directory fetch-sra
	@make --no-print-directory run-fetched-stage3
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Whole pipeline finished."

# Docker cleanup
.PHONY: docker-clean
docker-clean: ## Remove the Docker containers and images
	@printf '%b\n' "$(YELLOW)[CLEAN]$(NC) Removing Docker resources for project '$(COMPOSE_PROJECT_NAME)'..."
	@# -p keeps this to one project's containers and its own network. Without
	@# it, and with the network's name pinned in docker-compose.yml, `down`
	@# went at a network another project was still attached to.
	@$(COMPOSE) down --remove-orphans 2>/dev/null || true
	@docker image rm $(DOCKER_IMAGE) 2>/dev/null || true
	@docker image rm $(SRR_DOCKER_IMAGE) 2>/dev/null || true
	@printf '%b\n' "$(GREEN)[DONE]$(NC) Docker cleanup finished."

# Full reset
.PHONY: reset
reset: clean docker-clean setup ## Reset the whole system
	@printf '%b\n' "$(GREEN)[DONE]$(NC) System reset."
