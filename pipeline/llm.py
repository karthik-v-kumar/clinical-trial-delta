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

import gzip
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would silently turn the POST into a GET."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _decode(headers: dict, body: bytes) -> bytes:
    encoding = next((v for k, v in headers.items() if k.lower() == "content-encoding"), "").lower()
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    return body


def urllib_transport(url: str, headers: dict, body: bytes) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=120) as resp:
            resp_headers = dict(resp.headers.items())
            return resp.status, resp_headers, _decode(resp_headers, resp.read())
    except urllib.error.HTTPError as exc:
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
        return exc.code, resp_headers, _decode(resp_headers, exc.read() or b"")


def _content_type(headers: dict) -> str:
    return next((v for k, v in headers.items() if k.lower() == "content-type"), "")


def extract_content(headers: dict, payload: bytes) -> str:
    """The assistant message text from a chat-completions response (JSON or event stream)."""
    text = payload.decode("utf-8", "replace").strip()
    if text.startswith("data:"):  # server-sent events: join the streamed deltas
        parts = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[5:].strip())
                delta = chunk["choices"][0].get("delta") or chunk["choices"][0].get("message") or {}
                parts.append(delta.get("content") or "")
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue
        return "".join(parts)
    try:
        data = json.loads(text)
        return data["choices"][0]["message"]["content"] or ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        preview = text[:160].replace("\n", " ")
        raise LLMError(
            f"unexpected response ({type(exc).__name__}); content-type {_content_type(headers)!r}; body starts {preview!r}"
        ) from None


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
            "Accept": "application/json",
            "Accept-Encoding": "identity",
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
                return parse_json_object(extract_content(resp_headers, payload))
            if 300 <= status < 400:
                location = next((v for k, v in resp_headers.items() if k.lower() == "location"), "?")
                raise LLMUnavailable(f"{model}: endpoint redirected (HTTP {status} to {location}); check LLM_BASE_URL")
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


def probe(config: LLMConfig | None = None, transport: Transport = urllib_transport) -> int:
    """One request to the configured provider, printing what came back (never the key).
    Used by .github/workflows/llm-probe.yml to diagnose provider problems cheaply."""
    config = config or LLMConfig.from_env()
    if not config.configured:
        print("LLM_API_KEY is not set")
        return 1
    body = json.dumps(
        {
            "model": config.model,
            "messages": [{"role": "user", "content": 'Reply with the JSON object {"ok": true}.'}],
            "max_tokens": 20,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
    ).encode()
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "clinical-trial-delta/1.0",
    }
    url = f"{config.base_url}/chat/completions"
    status, resp_headers, payload = transport(url, headers, body)
    shown = {
        k: v
        for k, v in resp_headers.items()
        if k.lower()
        in (
            "content-type",
            "content-encoding",
            "location",
            "retry-after",
            "x-ratelimit-remaining-requests",
            "x-ratelimit-limit-requests",
            "x-ratelimit-remaining-tokens",
            "x-github-request-id",
        )
    }
    print(f"POST {url} model={config.model}")
    print(f"HTTP {status}")
    print(f"headers: {shown}")
    print(f"body ({len(payload)} bytes): {payload[:400]!r}")
    if status == 200:
        try:
            print("parsed:", parse_json_object(extract_content(resp_headers, payload)))
        except LLMError as exc:
            print("could not parse:", exc)
            return 1
        return 0
    return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        sys.exit(probe())
    print("usage: python -m pipeline.llm --probe")
    sys.exit(2)
