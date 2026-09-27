"""LLM access layer.

Three things this module buys us, all of them required by the brief:

*Reproducibility* -- temperature 0, a fixed seed, a pinned model string, and a
content-addressed cache. Re-running the same submission with the same prompts
produces byte-identical output and costs nothing.

*Auditability* -- every call records its prompt hash, model, token usage and
latency into the run trace.

*Testability* -- the MockProvider runs the entire pipeline offline and
deterministically, so CI and reviewers need no API key.

The rest of the codebase depends only on `LLMClient.complete_json`, so swapping
provider is a constructor change, not a refactor.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# JSON-schema helpers
# ---------------------------------------------------------------------------


def _strictify(node: Any) -> Any:
    """Make a Pydantic-generated JSON schema acceptable to strict structured
    outputs: every object must forbid extra properties and list every property
    as required."""
    if isinstance(node, dict):
        node = {k: _strictify(v) for k, v in node.items()}
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        node.pop("default", None)
        return node
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    return node


def schema_for(model_cls: type[BaseModel]) -> dict[str, Any]:
    return _strictify(model_cls.model_json_schema())


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class Provider(ABC):
    name: str

    @abstractmethod
    def generate(self, prompt: str, schema: dict[str, Any], schema_name: str) -> tuple[str, dict[str, int]]:
        """Return (raw_json_text, usage)."""


class MockProvider(Provider):
    """Deterministic offline stand-in.

    It reads the `[chunk:ID]` markers the assess stage embeds in the prompt, so
    the evidence it returns points at chunks that genuinely exist. Scores are
    derived from a hash of the prompt: stable across runs, varied across
    dimensions, and obviously synthetic. This is a test harness, not a model.
    """

    name = "mock"

    def generate(self, prompt: str, schema: dict[str, Any], schema_name: str) -> tuple[str, dict[str, int]]:
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        rng = random.Random(int(digest[:12], 16))
        chunk_ids = re.findall(r"\[chunk:([A-Za-z0-9_.:-]+)\]", prompt)

        if schema_name == "DimensionAssessment":
            dim_match = re.search(r"DIMENSION UNDER ASSESSMENT:\s*(\S+)", prompt)
            dimension = dim_match.group(1) if dim_match else "unknown"
            score = rng.randint(15, 82)
            severity = (
                "low" if score < 25 else
                "moderate" if score < 45 else
                "elevated" if score < 65 else
                "high" if score < 85 else "critical"
            )
            picked = chunk_ids[: min(3, len(chunk_ids))]
            payload = {
                "dimension": dimension,
                "score": score,
                "severity": severity,
                "rationale": (
                    f"[MOCK PROVIDER] Synthetic assessment for '{dimension}'. "
                    f"Derived from {len(chunk_ids)} retrieved passages. "
                    "Run with ORION_LLM_PROVIDER=openai for a real judgement."
                ),
                "evidence": [{"chunk_id": c, "doc_id": None, "page": None, "quote": ""} for c in picked],
                "confidence": round(rng.uniform(0.45, 0.9), 2),
                "gaps": ["[MOCK] No audited financial statements located for the most recent year."]
                if score > 40 else [],
                "inconsistencies": ["[MOCK] Declared headcount differs from the figure in the operations manual."]
                if score > 60 else [],
            }
            return json.dumps(payload), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        if schema_name == "FollowUpQuestionList":
            gaps = re.findall(r"- \[(\w+)\] (?:MISSING|INCONSISTENT): (.+)", prompt)[:6]
            questions = [
                {
                    "question_id": f"Q{i + 1:02d}",
                    "dimension": dim,
                    "question": f"[MOCK] Please clarify: {text.rstrip('.')}?",
                    "reason": "Flagged as a gap or inconsistency during dimension assessment.",
                    "blocking": i == 0,
                }
                for i, (dim, text) in enumerate(gaps)
            ]
            return json.dumps({"questions": questions}), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        raise LLMError(f"MockProvider has no fixture for schema '{schema_name}'")


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str, model: str, temperature: float, seed: int,
                 max_output_tokens: int, timeout: float) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("pip install openai") from exc
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=api_key, timeout=timeout)
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_output_tokens = max_output_tokens

    def generate(self, prompt: str, schema: dict[str, Any], schema_name: str) -> tuple[str, dict[str, int]]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
        }
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception:
            # Older models / schema shapes may reject strict structured output.
            # Fall back to plain JSON mode; the Pydantic validation downstream
            # is what actually guarantees the contract either way.
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["messages"] = [
                {"role": "system", "content": f"Reply with JSON matching this schema:\n{json.dumps(schema)}"},
                {"role": "user", "content": prompt},
            ]
            resp = self._client.chat.completions.create(**kwargs)

        text = resp.choices[0].message.content or "{}"
        usage = {
            "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(resp.usage, "total_tokens", 0) or 0,
        }
        return text, usage


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LLMClient:
    def __init__(self, provider: Provider, cache_dir: Path, logger, max_retries: int = 3,
                 fingerprint: str = "") -> None:
        self.provider = provider
        self.cache_dir = Path(cache_dir) / "llm"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log = logger
        self.max_retries = max_retries
        self.fingerprint = fingerprint
        self.calls = 0
        self.cache_hits = 0
        self.total_tokens = 0

    def _cache_key(self, prompt: str, schema_name: str) -> str:
        blob = f"{self.provider.name}|{self.fingerprint}|{schema_name}|{prompt}"
        return hashlib.sha256(blob.encode()).hexdigest()

    def complete_json(self, prompt: str, model_cls: type[BaseModel], tag: str) -> BaseModel:
        """Call the model and return a validated instance of `model_cls`.

        On a validation failure we retry once with the error appended, which is
        cheaper and more honest than silently coercing a malformed response.
        """
        schema_name = model_cls.__name__
        schema = schema_for(model_cls)
        key = self._cache_key(prompt, schema_name)
        cache_path = self.cache_dir / f"{key}.json"

        if cache_path.exists():
            self.cache_hits += 1
            self.log.log("llm.cache_hit", tag=tag, key=key[:12])
            return model_cls.model_validate_json(cache_path.read_text(encoding="utf-8"))

        attempt_prompt = prompt
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            started = time.perf_counter()
            try:
                raw, usage = self.provider.generate(attempt_prompt, schema, schema_name)
            except Exception as exc:  # network / rate limit / transport
                last_error = exc
                wait = min(2 ** attempt, 8)
                self.log.warn("llm.transport_error", tag=tag, attempt=attempt, error=repr(exc), retry_in_s=wait)
                time.sleep(wait if self.provider.name != "mock" else 0)
                continue

            elapsed = (time.perf_counter() - started) * 1000
            self.calls += 1
            self.total_tokens += usage.get("total_tokens", 0)

            try:
                obj = model_cls.model_validate_json(raw)
            except ValidationError as exc:
                last_error = exc
                self.log.warn("llm.schema_invalid", tag=tag, attempt=attempt, errors=exc.error_count())
                attempt_prompt = (
                    f"{prompt}\n\nYour previous reply failed schema validation with:\n"
                    f"{exc}\n\nReturn corrected JSON only."
                )
                continue

            self.log.log(
                "llm.call",
                tag=tag,
                schema=schema_name,
                ms=round(elapsed, 1),
                tokens=usage.get("total_tokens", 0),
                key=key[:12],
            )
            cache_path.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
            return obj

        raise LLMError(f"LLM call '{tag}' failed after {self.max_retries} attempts: {last_error}")


def build_client(settings, logger) -> LLMClient:
    if settings.llm_provider == "mock":
        provider: Provider = MockProvider()
    elif settings.llm_provider == "openai":
        provider = OpenAIProvider(
            api_key=settings.openai_api_key or "",
            model=settings.model,
            temperature=settings.temperature,
            seed=settings.seed,
            max_output_tokens=settings.max_output_tokens,
            timeout=settings.request_timeout,
        )
    else:
        raise LLMError(f"unknown provider: {settings.llm_provider}")

    fingerprint = f"{settings.model}|t={settings.temperature}|seed={settings.seed}"
    return LLMClient(
        provider=provider,
        cache_dir=settings.cache_root,
        logger=logger,
        max_retries=settings.max_retries,
        fingerprint=fingerprint,
    )
