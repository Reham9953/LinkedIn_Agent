"""LLM client with two providers, no SDK dependency.

provider: "github"    -> GitHub Models (free tier, uses the GITHUB_TOKEN that GitHub
                         Actions provides automatically; no payment, no extra key)
provider: "anthropic" -> Claude API (paid; supports built-in web search for research)
"""
import json
import os
import re
import time

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GITHUB_URL = "https://models.github.ai/inference/chat/completions"
GITHUB_CATALOG = "https://models.github.ai/catalog/models"
RETRY = (429, 500, 502, 503, 529)


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg["llm"]
        self.provider = self.cfg.get("provider", "github")
        env = "GITHUB_TOKEN" if self.provider == "github" else "ANTHROPIC_API_KEY"
        self.key = os.environ.get(env)
        if not self.key:
            raise RuntimeError(f"{env} is not set")

    @property
    def can_search(self) -> bool:
        return self.provider == "anthropic"

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        for attempt in range(5):
            r = requests.post(url, headers=headers, json=body, timeout=180)
            if r.status_code in RETRY:
                wait = int(r.headers.get("retry-after", 0) or 0) or 2 ** attempt * 5
                print(f"  rate limited ({r.status_code}), waiting {wait}s…", flush=True)
                time.sleep(min(wait, 120))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"LLM API error {r.status_code}: {r.text[:500]}")
            return r.json()
        raise RuntimeError("LLM API still rate limited after retries (daily free quota may be used up).")

    # ── providers ────────────────────────────────────────────
    def _github(self, system, prompt, model):
        body = {"model": model, "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": prompt}]}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        data = self._post(GITHUB_URL, headers, body)
        return data["choices"][0]["message"]["content"].strip(), []

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
        model = model or self.cfg["model"]
        if self.provider == "github":
            return self._github(system, prompt, model)
        return self._anthropic(system, prompt, model, web_search)

    def complete_json(self, system: str, prompt: str, model: str | None = None,
                      web_search: bool = False) -> tuple[dict, list[dict]]:
        text, sources = self.complete(system + "\n\nRespond with ONLY a JSON object. "
                                      "No markdown fences, no commentary.", prompt, model, web_search)
        return parse_json(text), sources


def list_github_models() -> list[str]:
    """List model IDs from the GitHub Models catalog (requires GITHUB_TOKEN with models: read)."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    r = requests.get(GITHUB_CATALOG, headers=headers, timeout=30)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Catalog request failed (HTTP {r.status_code}). "
                           f"Check the workflow has 'models: read' permission. Body: {r.text[:300]!r}")
    if r.status_code >= 400:
        raise RuntimeError(f"Catalog request failed (HTTP {r.status_code}): {str(data)[:300]}")
    items = data.get("models", data.get("data", [])) if isinstance(data, dict) else data
    return sorted(m.get("id") or m.get("name") for m in items if isinstance(m, dict))


def parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model output: {text[:200]}")
    return json.loads(text[start:end + 1])
