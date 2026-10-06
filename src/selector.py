"""Pick the next topic from the roadmap, respecting progression and repetition rules."""
from datetime import date, timedelta

from . import similarity


def _topic_text(t: dict) -> str:
    return f"{t['topic']} {t['concept']} {' '.join(t.get('keywords', []))}"


def recent(history: list[dict], today: date, days: int) -> list[dict]:
    cutoff = today - timedelta(days=days)
    return [p for p in history if date.fromisoformat(p["date"]) >= cutoff]


def check_repetition(text: str, keywords: list[str], history: list[dict], today: date, cfg: dict) -> str | None:
    """Return a rejection reason, or None if the candidate is fresh enough."""
    rep = cfg["repetition"]
    window = recent(history, today, rep["cooldown_days"])
    for p in window:
        if similarity.jaccard(keywords, p["keywords"]) >= rep["keyword_overlap_reject"]:
            return f"keyword overlap with Day {p['day']} ({p['topic']})"
    docs = [f"{p['topic']} {p['concept']} {p.get('post') or ''}" for p in window]
    sim, idx = similarity.max_similarity(text, docs)
    if sim >= rep["similarity_reject"]:
        return f"semantic similarity {sim:.2f} with Day {window[idx]['day']} ({window[idx]['topic']})"
    return None


def select_next(roadmap: dict, history: list[dict], today: date, cfg: dict) -> tuple[dict, list[str]]:
    """Return (topic, skipped_reasons). Topics are consumed in roadmap order, so phases
    progress naturally; a topic that fails the repetition check is skipped and logged."""
    used = {p["roadmap_id"] for p in history if p.get("roadmap_id")}
    skipped = []
    for t in roadmap["topics"]:
        if t["id"] in used:
            continue
        reason = check_repetition(_topic_text(t), t.get("keywords", []), history, today, cfg)
        if reason:
            skipped.append(f"#{t['id']} {t['topic']}: {reason}")
            continue
        topic = dict(t)
        topic["phase_name"] = roadmap["phases"][t["phase"]]
        topic["post_type"] = cfg["rotation"][today.weekday()]
        # Recap/challenge topics carry their own format.
        lowered = t["topic"].lower()
        if "recap" in lowered:
            topic["post_type"] = "weekly recap / QA insight"
        elif "challenge" in lowered:
            topic["post_type"] = "practical challenge"
        return topic, skipped
    raise RuntimeError("Roadmap exhausted. Run `python -m src.main propose-topics` "
                       "and add the reviewed topics to roadmap.yaml.")


def build_on(topic: dict, history: list[dict], limit: int = 3) -> list[dict]:
    """Earlier posts the new one can reference (same pillar or shared keywords)."""
    related = [p for p in history
               if p["pillar"] == topic["pillar"] or similarity.jaccard(p["keywords"], topic["keywords"]) > 0]
    return related[-limit:]
