FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN --mount=type=secret,id=ca_bundle,required=false if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; pip install --no-cache-dir --require-hashes -r requirements.lock
COPY workbench ./workbench
COPY dist ./dist
COPY templates ./templates
COPY profiles ./profiles
COPY recipes ./recipes
COPY examples ./examples
RUN useradd --uid 10001 --create-home frontier && mkdir -p /app/data /analysis && chown frontier:frontier /app/data /analysis

FROM base AS controller
USER frontier
ENV DATA_ROOT=/app/data EVIDENCE_ROOT=/evidence
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health')"
CMD ["uvicorn","workbench.api:app","--host","0.0.0.0","--port","8765","--workers","1"]

FROM base AS worker
COPY requirements-worker.lock ./
RUN --mount=type=secret,id=ca_bundle,required=false if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; pip install --no-cache-dir --require-hashes --no-binary=dissect-target -r requirements-worker.lock
RUN apt-get update && apt-get install -y --no-install-recommends ewf-tools sleuthkit file binutils rpm && rm -rf /var/lib/apt/lists/*
USER frontier
ENV EVIDENCE_ROOT=/evidence
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/health')"
CMD ["uvicorn","workbench.runner_api:app","--host","0.0.0.0","--port","8766","--workers","1"]

FROM worker AS test
USER root
COPY requirements-dev.lock ./
RUN --mount=type=secret,id=ca_bundle,required=false if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY tests ./tests
COPY pyproject.toml ./
USER frontier
CMD ["python","-m","pytest","-q"]
