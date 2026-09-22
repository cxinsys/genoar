FROM python:3.12-slim

WORKDIR /app

# System deps for faiss-cpu (OpenMP)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[test]"

COPY . .

RUN pip install --no-cache-dir -e .

# Where sentence-transformers keeps its models. The path is under /app, which
# compose bind-mounts from the host, so a model fetched on first start stays on
# the host and later containers find it already there.
#
# Nothing is pre-downloaded here, and nothing can usefully be: that same bind
# mount hides whatever the build writes under /app, so a model baked in at build
# time is not there at run time. A build-time fetch of PubMedBERT would be wrong
# twice over — the service embeds with SapBERT and all-MiniLM-L6-v2, and asks
# for neither of them from this path.
ENV HF_HOME=/app/.cache/huggingface

EXPOSE 8000

# No --reload. It is a development flag and this is the image the deployments
# run: it starts a file-watching subprocess, pins the server to a single worker,
# and — because compose bind-mounts the host's ./backend over /app — reloads on
# any write to the source tree on the host, including ones nobody meant as a
# deploy. docker-compose.override.yml.example adds it back for local work.
# --no-server-header: the `server: uvicorn` banner names the software for
# anyone who asks, and nothing needs it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
