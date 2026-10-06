"""LLM client with two providers, no SDK dependency.

provider: "gemini"    -> Google Gemini API free tier (key from Google AI Studio,
                         no credit card). Uses Gemini's OpenAI-compatible endpoint.
provider: "anthropic" -> Claude API (paid; supports built-in web search for research)
"""
import json
import os
import re
import time

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODELS = "https://generativelanguage.googleapis.com/v1beta/models"
RETRY = (429, 500, 502, 503, 529)


class Overloaded(RuntimeError):
    """The provider's model is temporarily overloaded/unavailable (HTTP 5xx)."""


class ModelMissing(RuntimeError):
    """The model ID does not exist for this account (HTTP 404)."""


class RateLimited(RuntimeError):
    """Our quota or per-minute limit was hit (HTTP 429)."""


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg["llm"]
        self.provider = self.cfg.get("provider", "gemini")
        env = "GEMINI_API_KEY" if self.provider == "gemini" else "ANTHROPIC_API_KEY"
        self.key = os.environ.get(env)
        if not self.key:
            raise RuntimeError(f"{env} is not set")

    @property
    def can_search(self) -> bool:
        return self.provider == "anthropic"

    def _post(self, url: str, headers: dict, body: dict, attempts: int = 4) -> dict:
        code, text = 0, ""
        for attempt in range(attempts):
            r = requests.post(url, headers=headers, json=body, timeout=180)
            code, text = r.status_code, r.text[:800]
            # A hard quota (e.g. "limit: 0" on the free tier) won't recover by waiting.
            if code == 429 and ("limit: 0" in r.text or "PerDay" in r.text):
                raise RateLimited(f"No free quota left for {body.get('model')} today")
            if code in RETRY:
                wait = int(r.headers.get("retry-after", 0) or 0) or 2 ** attempt * 5
                kind = "rate limited" if code == 429 else "model busy"
                print(f"  {kind} (HTTP {code}) on {body.get('model')}, retrying in {wait}s…", flush=True)
                time.sleep(min(wait, 60))
                continue
            if code == 404:
                raise ModelMissing(f"Model {body.get('model')} not found (HTTP 404)")
            if code >= 400:
                raise RuntimeError(f"LLM API error {code}: {text}")
            try:
                return r.json()
            except ValueError:
                raise RuntimeError(f"LLM API returned non-JSON (HTTP {code}): {text[:200]!r}")
        if code == 429:
            raise RateLimited(f"Rate limit reached on {body.get('model')} (HTTP 429)")
        raise Overloaded(f"Model {body.get('model')} unavailable (HTTP {code}): {text[:300]}")

    # ── providers ────────────────────────────────────────────
    def _gemini(self, system, prompt, model):
        body = {"model": model, "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": prompt}]}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        data = self._post(GEMINI_URL, headers, body)
        return (data["choices"][0]["message"].get("content") or "").strip(), []

    def _anthropic(self, system, prompt, model, web_search):
        body = {"model": model, "max_tokens": self.cfg["max_tokens"], "system": system,
                "messages": [{"role": "user", "content": prompt}]}
        if web_search:
            body["tools"] = [{"type": self.cfg["web_search_tool"], "name": "web_search",
                              "max_uses": self.cfg["max_searches"]}]
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        content = []
        for _ in range(5):  # server tools may pause long turns; continue them
            data = self._post(ANTHROPIC_URL, headers, body)
            content.extend(data["content"])
            if data.get("stop_reason") != "pause_turn":
                break
            body["messages"] = body["messages"][:1] + [{"role": "assistant", "content": data["content"]}]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        sources, seen = [], set()
        for b in content:
            for c in b.get("citations") or []:
                url = c.get("url")
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"title": c.get("title", ""), "url": url})
        return text.strip(), sources

    # ── public API ───────────────────────────────────────────
    def complete(self, system: str, prompt: str, model: str | None = None,
                 web_search: bool = False) -> tuple[str, list[dict]]:
        """Try the requested model, then the configured fallbacks if it is overloaded."""
        first = model or self.cfg["model"]
        chain = [first] + [m for m in self.cfg.get("fallback_models", []) if m != first]
        last_err = None
        for i, m in enumerate(chain):
            if i:
                print(f"  switching to fallback model {m}", flush=True)
            try:
                if self.provider == "gemini":
                    return self._gemini(system, prompt, m)
                return self._anthropic(system, prompt, m, web_search)
            except (Overloaded, ModelMissing, RateLimited) as e:
                print(f"  {e}", flush=True)
                last_err = e
        raise RuntimeError(f"No model could answer ({', '.join(chain)}). If these are quota errors, check "
                           f"https://aistudio.google.com/usage for which models have free quota and put "
                           f"those in config.yaml. Last error: {last_err}")

    def complete_json(self, system: str, prompt: str, model: str | None = None,
                      web_search: bool = False) -> tuple[dict, list[dict]]:
        text, sources = self.complete(system + "\n\nRespond with ONLY a JSON object. "
                                      "No markdown fences, no commentary.", prompt, model, web_search)
        return parse_json(text), sources


def list_models() -> tuple[list[str], str]:
    """List Gemini model IDs that support text generation. Returns (ids, error)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return [], "GEMINI_API_KEY is not set"
    r = requests.get(GEMINI_MODELS, headers={"x-goog-api-key": key},
                     params={"pageSize": 1000}, timeout=30)
    try:
        data = r.json()
    except ValueError:
        return [], f"HTTP {r.status_code}, non-JSON body {r.text[:100]!r}"
    if r.status_code >= 400:
        return [], f"HTTP {r.status_code}: {str(data)[:200]}"
    ids = [m["name"].removeprefix("models/") for m in data.get("models", [])
           if "generateContent" in m.get("supportedGenerationMethods", [])]
    return sorted(ids), ""


def probe(cfg: dict, model: str) -> str:
    """Send a tiny request to check a model really works. Returns 'OK' or the error."""
    try:
        text, _ = LLM(cfg).complete("Reply with the single word: pong", "ping", model=model)
        return f"OK (replied: {text[:20]!r})"
    except Exception as e:  # noqa: BLE001 - report any failure to the user
        return f"FAILED: {e}"


def parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model output: {text[:200]}")
    return json.loads(text[start:end + 1])
