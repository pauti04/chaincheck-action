FROM python:3.11-slim

# Install chaincheck and git (needed for commit-messages mode)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install from GitHub until v0.6.0 is published to PyPI
# Switch to: pip install "chaincheck>=0.6.0" after `uv publish`
RUN pip install --no-cache-dir "git+https://github.com/pauti04/chaincheck.git"

# Pre-download NLI model at build time to avoid cold-start latency.
# Skipped if network is unavailable (non-fatal).
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/nli-deberta-v3-base')" \
    || echo "Model pre-download skipped — will download at runtime"

COPY entrypoint.py /entrypoint.py

ENTRYPOINT ["python", "/entrypoint.py"]
