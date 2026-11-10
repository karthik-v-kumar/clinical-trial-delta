"""LLM client for the AI summary layer and the eval judge.

Any provider with an OpenAI-compatible chat-completions endpoint works. The
provider is chosen by repository settings, never by code: the key comes from
the GitHub Actions secret LLM_API_KEY and is passed in as an environment
variable at run time; nothing is stored in the repository.

Configuration (environment):
    LLM_API_KEY           secret. If absent, every LLM step reports "not run".
    LLM_BASE_URL          e.g. https://generativelanguage.googleapis.com/v1beta/openai
    LLM_MODEL             model that writes summaries and grounded answers
    JUDGE_MODEL           model that grades them (defaults to LLM_MODEL)
    LLM_PROVIDER          display name shown on the site (defaults to the URL's host)
    LLM_MIN_INTERVAL      seconds between requests to one model (default 6, i.e. 10 a minute)
    LLM_REASONING_EFFORT  optional, sent as reasoning_effort (for "thinking" models)

(GitHub Models, the original default, was retired by GitHub on 2026-07-30; its
endpoint now answers every request with a plain-text "OK".)

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
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_MIN_INTERVAL = 6.0  # 10 requests a minute: under the per-minute cap of common free tiers
PROBE_MAX_TOKENS = 1024  # room for a "thinking" model's reasoning before its reply
MAX_INPUT_CHARS = 24_000  # ~6,000 tokens: fits small context limits on free tiers
NOT_CONFIGURED = "no LLM provider configured"


class LLMError(RuntimeError):
    """The model call failed (bad request, unusable output)."""


class LLMUnavailable(LLMError):
    """No provider is configured, or it is out of quota for today. Stop calling it."""


class NotAChatCompletion(LLMError):
    """The endpoint answered, but not with a chat completion (wrong URL, or a retired service)."""


Transport = Callable[[str, dict, "bytes | None"], "tuple[int, dict[str, str], bytes]"]


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


def urllib_transport(url: str, headers: dict, body: bytes | None) -> tuple[int, dict[str, str], bytes]:
    """POST `body`, or GET when there is no body."""
    req = urllib.request.Request(url, data=body, headers=headers, method="GET" if body is None else "POST")
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
        choice = json.loads(text)["choices"][0]
        content = choice["message"].get("content") or ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError) as exc:
        preview = text[:160].replace("\n", " ")
        raise NotAChatCompletion(
            f"not a chat completion ({type(exc).__name__}); "
            f"content-type {_content_type(headers)!r}; body starts {preview!r}"
        ) from None
    if choice.get("finish_reason") == "length" and not content.rstrip().endswith("}"):
        raise LLMError("reply was cut off at max_tokens (a thinking model may need LLM_REASONING_EFFORT)")
    return content


@dataclass
class LLMConfig:
    api_key: str | None
    base_url: str | None = None
    model: str | None = None
    judge_model: str | None = None
    provider: str | None = None
    min_interval: float = DEFAULT_MIN_INTERVAL
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        self.judge_model = self.judge_model or self.model

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    @property
    def not_configured_reason(self) -> str:
        required = (("LLM_API_KEY", self.api_key), ("LLM_BASE_URL", self.base_url), ("LLM_MODEL", self.model))
        missing = [name for name, value in required if not value]
        return f"{NOT_CONFIGURED} ({', '.join(missing)} not set)" if missing else ""

    @property
    def provider_name(self) -> str | None:
        if self.provider:
            return self.provider
        return urllib.parse.urlparse(self.base_url).hostname if self.base_url else None

    @classmethod
    def from_env(cls, env: dict | None = None) -> LLMConfig:
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            return (env.get(name) or "").strip() or None

        try:
            interval = float(get("LLM_MIN_INTERVAL") or DEFAULT_MIN_INTERVAL)
        except ValueError:
            interval = DEFAULT_MIN_INTERVAL
        base_url = get("LLM_BASE_URL")
        return cls(
            api_key=get("LLM_API_KEY"),
            base_url=base_url.rstrip("/") if base_url else None,
            model=get("LLM_MODEL"),
            judge_model=get("JUDGE_MODEL"),
            provider=get("LLM_PROVIDER"),
            min_interval=max(interval, 0.0),
            reasoning_effort=get("LLM_REASONING_EFFORT"),
        )


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
    _no_json_mode: set[str] = field(default_factory=set)

    def _pace(self, model: str) -> None:
        last = self._last_call.get(model)
        if last is not None:
            wait = self.config.min_interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_call[model] = self.clock()

    def _body(self, model: str, system: str, user: str, max_tokens: int) -> bytes:
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if model not in self._no_json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.config.reasoning_effort:
            body["reasoning_effort"] = self.config.reasoning_effort
        return json.dumps(body).encode("utf-8")

    def chat_json(self, model: str, system: str, user: str, max_tokens: int = 900) -> dict:
        """One chat completion that must return a JSON object."""
        if not self.config.configured:
            raise LLMUnavailable(self.config.not_configured_reason)
        if model in self._exhausted:
            raise LLMUnavailable(f"{model}: daily request limit reached earlier in this run")
        if len(system) + len(user) > MAX_INPUT_CHARS:
            raise LLMError(f"prompt is {len(system) + len(user)} characters; limit is {MAX_INPUT_CHARS}")
        headers = request_headers(self.config.api_key)
        url = f"{self.config.base_url}/chat/completions"
        last_error = ""
        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            self._pace(model)
            self.requests_made[model] = self.requests_made.get(model, 0) + 1
            try:
                body = self._body(model, system, user, max_tokens)
                status, resp_headers, payload = self.transport(url, headers, body)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                status, resp_headers, payload, last_error = 0, {}, b"", f"{type(exc).__name__}: {exc}"
            if status == 200:
                try:
                    return parse_json_object(extract_content(resp_headers, payload))
                except NotAChatCompletion as exc:
                    raise LLMUnavailable(f"{model}: endpoint {exc}; check LLM_BASE_URL") from None
            if 300 <= status < 400:
                location = next((v for k, v in resp_headers.items() if k.lower() == "location"), "?")
                raise LLMUnavailable(f"{model}: endpoint redirected (HTTP {status} to {location}); check LLM_BASE_URL")
            text = payload[:300].decode("utf-8", "replace")
            if status == 429:
                retry_after = _retry_after(resp_headers, text)
                if retry_after is not None and retry_after <= 90 and attempt < self.max_attempts:
                    self.sleep(retry_after + 1)
                    continue
                # A long (or unstated) wait means the daily quota is used up: stop for this run.
                self._exhausted.add(model)
                raise LLMUnavailable(f"{model}: rate limited ({text})")
            if status in (401, 403):
                raise LLMUnavailable(f"{model}: not authorized (HTTP {status}: {text})")
            if status in (404, 410):
                raise LLMUnavailable(f"{model}: endpoint or model not available (HTTP {status}: {text})")
            if status == 400 and "response_format" in text and model not in self._no_json_mode:
                self._no_json_mode.add(model)  # JSON mode unsupported: ask for JSON in the prompt alone
                attempt -= 1
                continue
            if status and status < 500:
                raise LLMError(f"{model}: HTTP {status}: {text}")
            last_error = last_error if status == 0 else f"HTTP {status}"
            if attempt < self.max_attempts:
                self.sleep(2.0**attempt + random.uniform(0, 0.5))
        raise LLMError(f"{model}: giving up after {self.max_attempts} attempts ({last_error})")


def request_headers(api_key: str | None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "clinical-trial-delta/1.0",
    }


_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def _retry_after(headers: dict, body: str = "") -> float | None:
    for key, value in headers.items():
        if key.lower() in ("retry-after", "x-ratelimit-timeremaining"):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    match = _RETRY_DELAY.search(body)
    return float(match.group(1)) if match else None


def list_models(config: LLMConfig, transport: Transport = urllib_transport) -> list[str]:
    """The model IDs this key can use (OpenAI-compatible GET /models)."""
    status, _, payload = transport(f"{config.base_url}/models", request_headers(config.api_key), None)
    if status != 200:
        raise LLMError(f"HTTP {status}: {payload[:300].decode('utf-8', 'replace')}")
    try:
        rows = json.loads(payload)["data"]
        return sorted({str(row["id"]).removeprefix("models/") for row in rows})
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise NotAChatCompletion(f"unexpected model list ({type(exc).__name__}): {payload[:160]!r}") from None


def probe(config: LLMConfig | None = None, transport: Transport = urllib_transport) -> int:
    """Check a provider before relying on it (never prints the key).

    Lists the models the key can use, then sends one tiny request to LLM_MODEL
    and JUDGE_MODEL. Used by .github/workflows/llm-probe.yml; with only the key
    and LLM_BASE_URL set, it lists models so you can choose LLM_MODEL."""
    config = config or LLMConfig.from_env()
    if not (config.api_key and config.base_url):
        print(config.not_configured_reason)
        return 1
    print(f"Provider: {config.provider_name} ({config.base_url})")
    try:
        models = list_models(config, transport)
        print(f"Models this key can use ({len(models)}):")
        for model_id in models:
            print(f"  {model_id}")
    except LLMError as exc:
        print(f"Could not list models: {exc}")
    if not config.model:
        print("The key and URL work. Next: set the LLM_MODEL variable (and JUDGE_MODEL) and run this again.")
        return 0
    url = f"{config.base_url}/chat/completions"
    failures = 0
    for model in dict.fromkeys([config.model, config.judge_model]):
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": 'Reply with the JSON object {"ok": true}.'}],
            "max_tokens": PROBE_MAX_TOKENS,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if config.reasoning_effort:
            body["reasoning_effort"] = config.reasoning_effort
        status, resp_headers, payload = transport(url, request_headers(config.api_key), json.dumps(body).encode())
        shown = {
            k: v
            for k, v in resp_headers.items()
            if k.lower() in ("content-type", "content-encoding", "location", "retry-after")
            or k.lower().startswith("x-ratelimit")
        }
        print(f"\nPOST {url} model={model}")
        print(f"HTTP {status}")
        print(f"headers: {shown}")
        print(f"body ({len(payload)} bytes): {payload[:400]!r}")
        try:
            if status != 200:
                raise LLMError(f"HTTP {status}")
            print("parsed:", parse_json_object(extract_content(resp_headers, payload)))
            usage = json.loads(payload).get("usage") or {}
            print(f"tokens: {usage}")
            print(f"OK: {model} works.")
        except (LLMError, json.JSONDecodeError, AttributeError) as exc:
            print(f"FAILED: {model}: {exc}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        sys.exit(probe())
    print("usage: python -m pipeline.llm --probe")
    sys.exit(2)
