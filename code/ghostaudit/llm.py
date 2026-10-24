"""Optional model access (edges only): request-intent classification and reading screenshots/PDFs.

Provider: Google Gemini through its REST API (free tier available from Google AI Studio).
  GEMINI_API_KEY   required to enable the model; without it everything falls back to offline rules + OCR
  GEMINI_MODEL     model name (default: gemini-2.5-flash; run `python code/main.py --check-model` to list the ones your key can use)
  LLM_PRICE_IN / LLM_PRICE_OUT   USD per 1M input / output tokens (default 0 - free tier)

Every response is cached on disk (.cache/llm) so re-runs are free and deterministic, and every call is
counted so usage_report.md reflects the real run. Keys are read from the environment only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .dataio import REPO_ROOT

log = logging.getLogger("ghostaudit.llm")
DEFAULT_MODEL = "gemini-2.5-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class Usage:
    provider: str = "Google Gemini (AI Studio)"
    model: str = ""
    calls: int = 0
    cache_hits: int = 0
    cached_input_tokens: int = 0       # tokens originally spent on responses re-used from the cache
    cached_output_tokens: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_purpose: dict = field(default_factory=dict)

    def add(self, purpose: str, i: int, o: int) -> None:
        self.calls += 1
        self.input_tokens += i
        self.output_tokens += o
        p = self.by_purpose.setdefault(purpose, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        p["calls"] += 1; p["input_tokens"] += i; p["output_tokens"] += o

    def cost(self) -> float:
        pi, po = float(os.environ.get("LLM_PRICE_IN", 0)), float(os.environ.get("LLM_PRICE_OUT", 0))
        return self.input_tokens / 1e6 * pi + self.output_tokens / 1e6 * po


class GeminiClient:
    def __init__(self, api_key: str, model: str | None = None, cache_dir: Path | None = None) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        self.cache_dir = cache_dir or REPO_ROOT / ".cache" / "llm"
        self.usage = Usage(model=self.model)
        self.disabled = False
        self.consecutive_failures = 0
        self.timeout = float(os.environ.get("LLM_TIMEOUT", 40))
        self.backoff = float(os.environ.get("LLM_BACKOFF", 6))

    @classmethod
    def from_env(cls) -> "GeminiClient | None":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        return cls(key) if key else None

    def _post(self, body: dict, attempts: int = 3) -> dict:
        """One request with bounded retries. Quota / key / model errors switch the model off for the rest of the
        run (the audit then continues with the offline rules) instead of stalling for minutes per call."""
        req = urllib.request.Request(ENDPOINT.format(model=self.model), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
        last = ""
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self.consecutive_failures = 0
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                try:
                    msg = json.loads(exc.read()).get("error", {}).get("message", "")
                except Exception:
                    msg = ""
                msg = msg.replace(self.api_key, "[REDACTED]").splitlines()[0][:200] if msg else ""
                last = f"HTTP {exc.code} from model API: {msg}"
                if exc.code in (400, 401, 403, 404):
                    self._disable(last)
                    break
                if exc.code == 429 and attempt == attempts - 1:
                    self._disable(last + " (quota exhausted - wait for the quota to reset or choose another model)")
                    break
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                last = f"model call failed ({type(exc).__name__}: {exc})"
            if attempt < attempts - 1:
                time.sleep(self.backoff * (attempt + 1))
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            self._disable(f"3 failed calls in a row; last: {last}")
        raise RuntimeError(last or "model call failed")

    def _disable(self, reason: str) -> None:
        if not self.disabled:
            log.warning("model switched off for the rest of this run: %s", reason)
        self.disabled = True

    def list_models(self) -> list[str]:
        """Model names this key can call generateContent on (used by --check-model)."""
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                     headers={"x-goog-api-key": self.api_key})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return sorted(m["name"].removeprefix("models/") for m in data.get("models", [])
                      if "generateContent" in m.get("supportedGenerationMethods", []))

    def generate(self, prompt: str, purpose: str, image: Path | None = None, json_mode: bool = True) -> str:
        parts: list[dict] = [{"text": prompt}]
        tag = hashlib.sha256((self.model + prompt).encode()).hexdigest()
        if image is not None:
            data = image.read_bytes()
            tag = hashlib.sha256((self.model + prompt).encode() + data).hexdigest()
            parts.append({"inline_data": {"mime_type": mimetypes.guess_type(image.name)[0] or "image/png",
                                          "data": base64.b64encode(data).decode()}})
        cache = self.cache_dir / f"{tag}.json"
        if self.disabled and not cache.exists():
            raise RuntimeError("model disabled after an earlier key/model error")
        if cache.exists():
            hit = json.loads(cache.read_text())
            self.usage.cache_hits += 1
            self.usage.cached_input_tokens += hit.get("input_tokens", 0)
            self.usage.cached_output_tokens += hit.get("output_tokens", 0)
            return hit["text"]
        gen_cfg = {"temperature": 0}
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        try:
            out = self._post({"contents": [{"parts": parts}], "generationConfig": gen_cfg})
            text = out["candidates"][0]["content"]["parts"][0]["text"]
            meta = out.get("usageMetadata", {})
            t_in = meta.get("promptTokenCount", 0)
            t_out = meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)   # thinking tokens are billed as output
            self.usage.add(purpose, t_in, t_out)
        except Exception as exc:
            self.usage.failures += 1
            raise RuntimeError(str(exc) if isinstance(exc, RuntimeError) else f"model call failed ({type(exc).__name__}: {exc})") from None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"text": text, "input_tokens": t_in, "output_tokens": t_out}))
        return text


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def classify_intent(client: GeminiClient, request_text: str) -> tuple[bool, str]:
    prompt = (PROMPTS / "intent_classifier.md").read_text().replace("{request_text}", request_text)
    data = json.loads(client.generate(prompt, "intent"))
    return bool(data["authorizes_deletion"]), str(data.get("reason", ""))


def read_screen(client: GeminiClient, path: Path) -> str:
    prompt = (PROMPTS / "screen_reader.md").read_text()
    return client.generate(prompt, "vision", image=path, json_mode=False)
