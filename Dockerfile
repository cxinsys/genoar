# GENOAR Complete Workflow Docker Image
# Multi-stage build for optimal image size and performance

# Stage 1: Base Python environment with system dependencies
#
# The browser is architecture-dependent, so this stage is split in three: one
# stage for the OS packages both architectures need, one browser stage per
# architecture, and a `FROM browser-${TARGETARCH}` that picks the right one.
# BuildKit only builds the stage it selects, so an arm64 build never touches
# the amd64 Chrome .deb -- which fails on Apple Silicon with apt exit 100 --
# and neither image carries both browsers.
FROM python:3.11-slim-bookworm AS base-common

# Install system dependencies for web automation and scientific computing
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    unzip \
    xvfb \
    ca-certificates \
    git \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# --- Browser, amd64: Google Chrome + the matching Chrome for Testing driver ---
# Unchanged from the single-architecture version of this file: same .deb, same
# driver resolution, same install locations.
FROM base-common AS browser-amd64

# Install Google Chrome - direct deb package
RUN wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

# Install ChromeDriver (Chrome for Testing API - Chrome 115+)
# /usr/bin, matching genoar_crawler/Dockerfile.amd64 and .arm64. The crawler
# looks there unless CHROME_*_PATH says otherwise, so the two images have to
# agree on the location.
RUN set -e; \
    CHROME_VERSION=$(google-chrome-stable --version | grep -oP '\d+' | head -1); \
    echo "Chrome major version: $CHROME_VERSION"; \
    DRIVER_URL="https://storage.googleapis.com/chrome-for-testing-public/$(curl -sS https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${CHROME_VERSION})/linux64/chromedriver-linux64.zip"; \
    wget -q -O /tmp/chromedriver.zip "$DRIVER_URL"; \
    unzip -j /tmp/chromedriver.zip "chromedriver-linux64/chromedriver" -d /usr/bin/; \
    chmod +x /usr/bin/chromedriver; \
    rm -rf /tmp/*

# Tell the crawler where they are, the same way the per-architecture crawler
# images do. It auto-detects when these are unset, but an image that knows
# should say so rather than let a search decide.
ENV CHROME_BINARY_PATH=/usr/bin/google-chrome-stable
ENV CHROME_DRIVER_PATH=/usr/bin/chromedriver

# --- Browser, arm64: Chromium + chromium-driver from Debian ---
# Google publishes no arm64 Chrome build, so Apple Silicon / Graviton use the
# distribution's Chromium, exactly as the verified genoar_crawler/Dockerfile.arm64
# does. Both land in /usr/bin, which is where the crawler looks first.
FROM base-common AS browser-arm64

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BINARY_PATH=/usr/bin/chromium
ENV CHROME_DRIVER_PATH=/usr/bin/chromedriver

# --- The browser stage this build's architecture selected ---
FROM browser-${TARGETARCH} AS base

# Verify installations - also proves CHROME_BINARY_PATH / CHROME_DRIVER_PATH
# name files that exist, since that is what the crawler will be handed.
RUN echo "=== Installed Versions ===" \
    && "$CHROME_BINARY_PATH" --version \
    && "$CHROME_DRIVER_PATH" --version

# Set working directory
WORKDIR /app

# Stage 2: Python dependencies installation
FROM base AS dependencies

# Declares PYTHONPATH for the build, so that `ENV PYTHONPATH="/app:${PYTHONPATH}"`
# down in the application stage is not a read of a variable BuildKit has never
# heard of:
#
#   UndefinedVar: Usage of undefined variable '$PYTHONPATH'
#
# The build always succeeded - the warning is cosmetic - but it is the first
# thing a new user sees on `make setup`, and it reads like something went wrong.
#
# It has to be declared *here* rather than next to the line that uses it: an ARG
# shadows an inherited ENV of the same name within its own stage, so declaring it
# in the application stage would drop a PYTHONPATH set by the base image instead
# of prepending to it. ARGs are stage-scoped and are not inherited through FROM,
# so this declaration satisfies the linter without being in scope where the
# expansion happens. python:3.11-slim-bookworm sets no PYTHONPATH, so the value
# stays exactly what it has always been, "/app:"; a base image that did set one
# would still be preserved as "/app:<inherited>".
ARG PYTHONPATH=

# Copy requirements files
COPY requirements.txt /app/
COPY genoar_crawler/requirements.txt /app/crawler_requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r crawler_requirements.txt

# Stage 3: Application files and setup
FROM dependencies AS application

# Copy the entire project
COPY . /app/

# Install the analysis package in development mode
RUN pip install -e .

# Create required directories
RUN mkdir -p /app/crawl_output/META /app/crawl_output/SMTX /app/crawl_output/SRR \
    && mkdir -p /app/first_pass_output \
    && mkdir -p /app/logs \
    && mkdir -p /app/workflow_output

# Set environment variables
ENV PYTHONPATH="/app:${PYTHONPATH}"
ENV DISPLAY=:99
ENV SELENIUM_HEADLESS=true
ENV SNAKEMAKE_CORES=4

# Copy workflow scripts
COPY workflow/ /app/workflow/
RUN chmod +x /app/workflow/run.sh

# Expose port for potential web interface
EXPOSE 8000

# Set default entrypoint
ENTRYPOINT ["/app/workflow/run.sh"]
CMD []