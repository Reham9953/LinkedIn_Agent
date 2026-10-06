"""LinkedIn formatting + publishing via the Posts API (/rest/posts)."""
import os
import re

import requests

RESERVED = re.compile(r"[\\|{}@\[\]()<>#*_~]")
HASHTAG = re.compile(r"(?<![\w#])#([A-Za-z]\w*)")
BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)


def unicode_bold(text: str) -> str:
    """LinkedIn has no markdown: render **x** with Unicode sans-serif bold.
    Note: screen readers may read these characters poorly, so keep bold sparse."""
    def conv(ch):
        o = ord(ch)
        if 65 <= o <= 90:
            return chr(0x1D5D4 + o - 65)
        if 97 <= o <= 122:
            return chr(0x1D5EE + o - 97)
        if 48 <= o <= 57:
            return chr(0x1D7EC + o - 48)
        return ch
    return BOLD.sub(lambda m: "".join(conv(c) for c in m.group(1)), text)


def strip_bold(text: str) -> str:
    return BOLD.sub(lambda m: m.group(1), text)


def to_little_text(text: str) -> str:
    """Escape LinkedIn 'little' reserved characters; keep hashtags clickable via the template.
    Unescaped reserved characters can silently truncate the published post."""
    parts, last = [], 0
    for m in HASHTAG.finditer(text):
        parts.append(RESERVED.sub(lambda c: "\\" + c.group(0), text[last:m.start()]))
        parts.append("{hashtag|\\#|" + m.group(1) + "}")
        last = m.end()
    parts.append(RESERVED.sub(lambda c: "\\" + c.group(0), text[last:]))
    return "".join(parts)


def render(post: str, cfg: dict) -> str:
    """Final visible text (what readers will see)."""
    return unicode_bold(post) if cfg["publishing"]["unicode_bold"] else strip_bold(post)


class LinkedInClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg["linkedin"]
        self.token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
        if not self.token:
            raise RuntimeError("LINKEDIN_ACCESS_TOKEN is not set")
        self.author = os.environ.get("LINKEDIN_PERSON_URN") or self._whoami()

    def _whoami(self) -> str:
        r = requests.get("https://api.linkedin.com/v2/userinfo",
                         headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
        r.raise_for_status()
        return f"urn:li:person:{r.json()['sub']}"

    def publish(self, visible_text: str) -> str:
        body = {
            "author": self.author,
            "commentary": to_little_text(visible_text),
            "visibility": self.cfg["visibility"],
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                   "LinkedIn-Version": str(self.cfg["api_version"]),
                   "X-Restli-Protocol-Version": "2.0.0"}
        r = requests.post("https://api.linkedin.com/rest/posts", json=body, headers=headers, timeout=60)
        if r.status_code != 201:
            raise RuntimeError(f"LinkedIn publish failed ({r.status_code}): {r.text[:500]}")
        return r.headers.get("x-restli-id", "")
