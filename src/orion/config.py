"""Central configuration.

Everything tunable lives here so that a reviewer can see, in one place, every
knob that affects the output. Values come from environment variables with
explicit defaults; nothing is read from the environment anywhere else in the
codebase.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- LLM -------------------------------------------------------------
    # "mock" runs the whole pipeline offline and deterministically. It exists
    # so the pipeline is testable in CI, reproducible without network access,
    # and reviewable without an API key.
    llm_provider: str = "mock"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    seed: int = 7
    max_output_tokens: int = 2000
    request_timeout: float = 60.0
    max_retries: int = 3
    openai_api_key: str | None = None

    # --- Retrieval -------------------------------------------------------
    # The document set can exceed 100 pages. We never send all of it to the
    # model: each risk dimension gets its own small, relevant slice.
    retrieval: str = "keyword"  # keyword | embedding
    embedding_model: str = "text-embedding-3-small"
    max_chunks_per_dimension: int = 8
    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 150

    # --- Paths -----------------------------------------------------------
    storage_root: Path = REPO_ROOT / "data"
    runs_root: Path = REPO_ROOT / "runs"
    cache_root: Path = REPO_ROOT / ".cache"

    # --- Output ----------------------------------------------------------
    review_api_url: str | None = None
    review_api_token: str | None = None
    emit_dry_run: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_provider=os.environ.get("ORION_LLM_PROVIDER", "mock"),
            model=os.environ.get("ORION_MODEL", "gpt-4o-mini"),
            temperature=float(os.environ.get("ORION_TEMPERATURE", "0.0")),
            seed=int(os.environ.get("ORION_SEED", "7")),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            retrieval=os.environ.get("ORION_RETRIEVAL", "keyword"),
            max_chunks_per_dimension=int(os.environ.get("ORION_MAX_CHUNKS", "8")),
            storage_root=Path(os.environ.get("ORION_STORAGE_ROOT", str(REPO_ROOT / "data"))),
            runs_root=Path(os.environ.get("ORION_RUNS_ROOT", str(REPO_ROOT / "runs"))),
            cache_root=Path(os.environ.get("ORION_CACHE_ROOT", str(REPO_ROOT / ".cache"))),
            review_api_url=os.environ.get("ORION_REVIEW_API_URL"),
            review_api_token=os.environ.get("ORION_REVIEW_API_TOKEN"),
            emit_dry_run=_env_bool("ORION_EMIT_DRY_RUN", True),
        )


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
