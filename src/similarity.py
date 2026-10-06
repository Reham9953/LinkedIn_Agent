"""Dependency-free repetition detection.

Two signals:
  * keyword overlap (Jaccard) between roadmap/generated keywords
  * TF-IDF cosine similarity between full texts (topic + concept + post)
"""
import math
import re
from collections import Counter

STOP = set("""a an the and or but if of to in on for with by is are was were be been it this that
these those as at from your you we our they their i my me not no do does can will just than then
so what why how when which who into about more most very also too only same such each any all
up out over under again new one two use using used vs""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z][a-z0-9\-]+", text.lower()) if t not in STOP and len(t) > 2]


def jaccard(a, b) -> float:
    a = {k.lower().strip() for k in a}
    b = {k.lower().strip() for k in b}
    return len(a & b) / len(a | b) if a and b else 0.0


def _tfidf(docs: list[list[str]]) -> list[dict]:
    n = len(docs)
    df = Counter(t for d in docs for t in set(d))
    vecs = []
    for d in docs:
        tf = Counter(d)
        vecs.append({t: (c / len(d)) * (math.log((1 + n) / (1 + df[t])) + 1) for t, c in tf.items()} if d else {})
    return vecs


def _cos(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(t, 0.0) for t, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def max_similarity(candidate: str, history: list[str]) -> tuple[float, int]:
    """Return (highest cosine similarity, index in history)."""
    if not history:
        return 0.0, -1
    vecs = _tfidf([tokens(candidate)] + [tokens(h) for h in history])
    sims = [_cos(vecs[0], v) for v in vecs[1:]]
    best = max(range(len(sims)), key=sims.__getitem__)
    return sims[best], best
