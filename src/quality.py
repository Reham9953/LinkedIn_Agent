"""Quality checker: deterministic rules first, then an LLM judge on the 8 criteria."""
import re

import yaml

CRITERIA = ["accuracy", "originality", "practical_value", "readability",
            "personal_branding", "engagement", "human_tone", "linkedin_quality"]

BANNED = ["in today's rapidly evolving", "revolutionizing", "revolutionising", "the future is ai",
          "game-changer", "game changer", "delve", "unlock the power"]

EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\u2B50\u2B55\u203C\u2049]")
HASHTAG = re.compile(r"(?<![\w#])#[A-Za-z][\w]*")
# Phrases that usually signal invented personal experience.
FAKE_EXPERIENCE = re.compile(r"\b(last (week|month|year)|at my (company|job|client)|my team (and I )?(built|shipped|cut|reduced)|"
                             r"in my (last|current) project|we (reduced|increased|cut) .{0,30}\d+ ?%)", re.I)


def rule_checks(post: str, label: str, cfg: dict) -> list[str]:
    q, issues = cfg["quality"], []
    if len(post) > cfg["publishing"]["max_chars"]:
        issues.append(f"Too long: {len(post)} chars (max {cfg['publishing']['max_chars']}).")
    n_emoji = len(EMOJI.findall(post))
    if n_emoji > q["max_emojis"]:
        issues.append(f"{n_emoji} emojis (max {q['max_emojis']}).")
    tags = HASHTAG.findall(post)
    if not q["hashtags_min"] <= len(tags) <= q["hashtags_max"]:
        issues.append(f"{len(tags)} hashtags (need {q['hashtags_min']}-{q['hashtags_max']}).")
    if label not in post:
        issues.append(f"Series label '{label}' missing.")
    low = post.lower()
    for b in BANNED:
        if b in low:
            issues.append(f"Banned phrase: '{b}'.")
    m = FAKE_EXPERIENCE.search(post)
    if m:
        issues.append(f"Possible invented personal experience: '{m.group(0)}'. Remove or generalise.")
    if not post.rstrip().split("\n")[-1].strip().startswith("#"):
        issues.append("Last line should be the hashtags.")
    if "?" not in post:
        issues.append("No engagement question.")
    return issues


JUDGE_SYSTEM = """You are a strict editor reviewing a LinkedIn post for a QA / Test Automation / GenAI engineer.
Score each criterion 1-10 (10 = excellent, publish as-is; 7 = fine but improvable; <6 = problem):
accuracy (every technical statement correct; any unsupported specific claim, statistic, version or feature
caps this at 5), originality (substantially different from the listed earlier posts), practical_value
(a QA engineer learns something usable), readability, personal_branding (reinforces QA + Automation + AI
without bragging), engagement (natural discussion question), human_tone (sounds like a real QA engineer,
not generic AI copy), linkedin_quality (scannable on mobile, strong first line).
Also flag any first-person claim not supported by the AUTHOR PROFILE as an accuracy issue."""


def judge(llm, post: str, topic: dict, research_notes: str, profile: dict,
          recent_posts: list[dict], cfg: dict) -> dict:
    earlier = "\n".join(f"- Day {p['day']}: {p['topic']} — {p['concept']}" for p in recent_posts[-30:]) or "none"
    prompt = f"""TOPIC: {topic['topic']} — {topic['concept']}
EARLIER POSTS:
{earlier}
AUTHOR PROFILE:
{yaml.safe_dump(profile, sort_keys=False)}
RESEARCH NOTES:
{research_notes or 'none'}

POST:
<<<
{post}
>>>

Return JSON: {{"scores": {{{', '.join(f'"{c}": int' for c in CRITERIA)}}},
"issues": [specific, actionable problems], "verdict": "one sentence"}}"""
    data, _ = llm.complete_json(JUDGE_SYSTEM, prompt, model=cfg["llm"]["judge_model"])
    scores = {c: int(data["scores"].get(c, 0)) for c in CRITERIA}
    total80 = sum(scores.values())
    return {"scores": scores, "total_80": total80,
            "total_50": round(total80 / 80 * 50, 1),
            "issues": data.get("issues", []), "verdict": data.get("verdict", "")}


def evaluate(llm, post, label, topic, research_notes, profile, recent_posts, cfg) -> dict:
    rules = rule_checks(post, label, cfg)
    result = judge(llm, post, topic, research_notes, profile, recent_posts, cfg)
    result["rule_issues"] = rules
    result["passed"] = (not rules
                        and result["total_50"] >= cfg["quality"]["threshold_out_of_50"]
                        and result["scores"]["accuracy"] >= 8)
    return result
