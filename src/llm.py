"""Thin client for the Claude Messages API (no SDK dependency)."""
import json
import os
import re
import time

import requests

API_URL = "https://api.anthropic.com/v1/messages"


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg["llm"]
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def _post(self, body: dict) -> dict:
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        for attempt in range(4):
            r = requests.post(API_URL, headers=headers, json=body, timeout=180)
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(2 ** attempt * 5)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    def complete(self, system: str, prompt: str, model: str | None = None,
                 web_search: bool = False) -> tuple[str, list[dict]]:
        """Return (text, sources). Sources come from web-search citations."""
        body = {"model": model or self.cfg["model"], "max_tokens": self.cfg["max_tokens"],
                "system": system, "messages": [{"role": "user", "content": prompt}]}
        if web_search:
            body["tools"] = [{"type": self.cfg["web_search_tool"], "name": "web_search",
                              "max_uses": self.cfg["max_searches"]}]
        content = []
        for _ in range(5):  # server tools may pause long turns; continue them
            data = self._post(body)
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

    def complete_json(self, system: str, prompt: str, model: str | None = None,
                      web_search: bool = False) -> tuple[dict, list[dict]]:
        text, sources = self.complete(system + "\n\nRespond with ONLY a JSON object. "
                                      "No markdown fences, no commentary.", prompt, model, web_search)
        return parse_json(text), sources


def parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model output: {text[:200]}")
    return json.loads(text[start:end + 1])
