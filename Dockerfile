FROM python:3.11-slim

# Install chaincheck and git (needed for commit-messages mode)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "chaincheck>=0.6.0"

# Pre-download NLI model so first run is fast
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/nli-deberta-v3-base')" \
    || true   # non-fatal: model downloads at runtime if this fails

COPY entrypoint.py /entrypoint.py

ENTRYPOINT ["python", "/entrypoint.py"]
