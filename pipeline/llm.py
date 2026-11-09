"""LLM client for the AI summary layer and the eval judge.

Default provider: GitHub Models (OpenAI-compatible chat completions at
https://models.github.ai/inference). In GitHub Actions the workflow passes its
own GITHUB_TOKEN (with `models: read`) as LLM_API_KEY, so there is no key to
create or store and no personal agent allowance is involved.

Configuration (environment):
    LLM_API_KEY     required. If absent, every LLM step reports "not run".
    LLM_BASE_URL    default https://models.github.ai/inference
    LLM_MODEL       summaries and grounded answers (default openai/gpt-4.1-mini)
    JUDGE_MODEL     eval judge (default openai/gpt-4.1)

The client is called only from the daily pipeline, never at page-view time.
Standard library only.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://models.github.ai/inference"
DEFAULT_MODEL = "openai/gpt-4.1-mini"
DEFAULT_JUDGE_MODEL = "openai/gpt-4.1"
PROVIDER_NAME = "GitHub Models"

# GitHub Models free limits (Copilot Free/Pro): "low" tier 15 requests/minute and
# 150/day; "high" tier 10/minute and 50/day; 8,000 input tokens per request.
# Pacing keeps us under the per-minute limits; per-run budgets keep us under the
# daily ones (see summarize.py and evals.py).
MIN_INTERVAL_SECONDS = {"low": 4.5, "high": 6.5}
HIGH_TIER_MODELS = {"openai/gpt-4.1", "openai/gpt-4o", "openai/o3", "openai/o4-mini", "openai/gpt-5"}
MAX_INPUT_CHARS = 24_000  # ~6,000 tokens: stays under the 8,000-token request limit


class LLMError(RuntimeError):
    """The model call failed (bad request, unusable output)."""


class LLMUnavailable(LLMError):
    """No provider is configured, or it is out of quota for today. Stop calling it."""


Transport = Callable[[str, dict, bytes], "tuple[int, dict[str, str], bytes]"]


def urllib_transport(url: str, headers: dict, body: bytes) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read() or b""


@dataclass
class LLMConfig:
    api_key: str | None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls, env: dict | None = None) -> LLMConfig:
        env = os.environ if env is None else env
        return cls(
            api_key=(env.get("LLM_API_KEY") or "").strip() or None,
            base_url=(env.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            model=(env.get("LLM_MODEL") or DEFAULT_MODEL).strip(),
            judge_model=(env.get("JUDGE_MODEL") or DEFAULT_JUDGE_MODEL).strip(),
        )


def tier(model: str) -> str:
    return "high" if model in HIGH_TIER_MODELS else "low"


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict:
    """The JSON object in a model reply (tolerates code fences and stray prose)."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("reply is not JSON") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"reply is not JSON: {exc}") from None
    if not isinstance(value, dict):
        raise LLMError("reply JSON is not an object")
    return value


@dataclass
class LLMClient:
    config: LLMConfig
    transport: Transport = urllib_transport
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    max_attempts: int = 4
    requests_made: dict[str, int] = field(default_factory=dict)
    _last_call: dict[str, float] = field(default_factory=dict)
    _exhausted: set[str] = field(default_factory=set)

    def _pace(self, model: str) -> None:
        interval = MIN_INTERVAL_SECONDS[tier(model)]
        last = self._last_call.get(model)
        if last is not None:
            wait = interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_call[model] = self.clock()

    def chat_json(self, model: str, system: str, user: str, max_tokens: int = 900) -> dict:
        """One chat completion that must return a JSON object."""
        if not self.config.configured:
            raise LLMUnavailable("no LLM provider configured (LLM_API_KEY is not set)")
        if model in self._exhausted:
            raise LLMUnavailable(f"{model}: daily request limit reached earlier in this run")
        if len(system) + len(user) > MAX_INPUT_CHARS:
            raise LLMError(f"prompt is {len(system) + len(user)} characters; limit is {MAX_INPUT_CHARS}")
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "User-Agent": "clinical-trial-delta/1.0",
        }
        url = f"{self.config.base_url}/chat/completions"
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            self._pace(model)
            self.requests_made[model] = self.requests_made.get(model, 0) + 1
            try:
                status, resp_headers, payload = self.transport(url, headers, body)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                status, resp_headers, payload, last_error = 0, {}, b"", f"{type(exc).__name__}: {exc}"
            if status == 200:
                try:
                    data = json.loads(payload)
                    content = data["choices"][0]["message"]["content"] or ""
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    raise LLMError(f"unexpected response shape: {exc}") from None
                return parse_json_object(content)
            text = payload[:300].decode("utf-8", "replace")
            if status == 429:
                retry_after = _retry_after(resp_headers)
                if retry_after is not None and retry_after <= 90 and attempt < self.max_attempts:
                    self.sleep(retry_after + 1)
                    continue
                # A long wait means the daily quota is used up: stop for this run.
                self._exhausted.add(model)
                raise LLMUnavailable(f"{model}: rate limited ({text})")
            if status in (401, 403):
                raise LLMUnavailable(f"{model}: not authorized (HTTP {status}: {text})")
            if status and status < 500:
                raise LLMError(f"{model}: HTTP {status}: {text}")
            last_error = last_error if status == 0 else f"HTTP {status}"
            if attempt < self.max_attempts:
                self.sleep(2.0**attempt + random.uniform(0, 0.5))
        raise LLMError(f"{model}: giving up after {self.max_attempts} attempts ({last_error})")


def _retry_after(headers: dict) -> float | None:
    for key, value in headers.items():
        if key.lower() in ("retry-after", "x-ratelimit-timeremaining"):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None
