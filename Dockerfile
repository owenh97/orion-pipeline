FROM python:3.11-slim

# Deterministic, non-root, no build toolchain left in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY tools/ ./tools/
COPY run.py .
COPY data/ ./data/

# Generate the sample document set at build time so `docker run` works with no
# further setup. Harmless if the documents are already present.
RUN python tools/make_sample_documents.py

RUN useradd --create-home --uid 1000 orion && chown -R orion:orion /app
USER orion

ENV ORION_LLM_PROVIDER=mock

ENTRYPOINT ["python", "run.py"]
CMD ["--submission", "data/submissions/ACME-2026-001"]
