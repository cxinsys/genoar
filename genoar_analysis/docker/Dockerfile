# GENOAR Analysis Docker Image
# Stage 2: UMLS-based analysis pipeline

FROM python:3.11-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy genoar_analysis package
COPY genoar_analysis/ /app/genoar_analysis/

# Copy entrypoint script
COPY genoar_analysis/docker/run_analysis.py /app/run_analysis.py
RUN chmod +x /app/run_analysis.py

# Set Python path
ENV PYTHONPATH=/app

# Default command
ENTRYPOINT ["python", "/app/run_analysis.py"]
CMD ["--help"]
