"""Research + LinkedIn post generation."""
import yaml

STYLE_GUIDE = """
You write LinkedIn posts for a Software QA / Quality Engineer specialising in Test Automation
and Generative AI. The series builds the author's brand at the intersection of QA, automation and AI.

VOICE: professional, curious, practical, friendly, confident but not arrogant, human.
Simple language. Short paragraphs (1-3 lines). Easy to scan on a phone.

STRUCTURE (no section labels in the post itself):
1. Hook — first line, a strong statement, question or practical QA problem. It must work on its own
   because LinkedIn truncates after ~2 lines.
2. Knowledge — ONE main concept, explained clearly.
3. Practical example — a short, realistic QA scenario (prefer examples over definitions).
4. QA perspective — why this matters to testers / automation engineers.
5. Key takeaway — one memorable lesson.
6. Engagement question — natural, specific, invites discussion.
Then a line with the series label, then 3-5 hashtags on the last line.

HARD RULES
- Never invent statistics, benchmarks, tool features, versions, certifications or quotes.
- If a fact needs a source, only use facts from the RESEARCH NOTES provided. Otherwise stay general.
- First-person claims are allowed ONLY when supported by the AUTHOR PROFILE. Never invent projects,
  employers, results, anecdotes ("last week my team...") or achievements.
- Maximum 3 emojis. Use **bold** for at most 2-3 short phrases.
- Banned: "In today's rapidly evolving world", "AI is revolutionizing", "The future is AI",
  "game-changer", "unlock", "delve", generic motivational endings.
- Code snippets only when they genuinely help; max ~8 lines; must be correct.
  Preferred: Python, JavaScript, Java (CAPL only when relevant).
- Total length: 900-1,800 characters including hashtags.
"""


def research(llm, topic: dict) -> tuple[str, list[dict]]:
    system = ("You are a careful technical researcher for a QA engineer. Use web search and prefer "
              "official sources: vendor documentation (Playwright, Selenium, Jenkins, Pytest, OpenAI, "
              "Microsoft, Google, Anthropic), ISTQB, standards bodies and peer-reviewed papers. "
              "Report only facts you found, each with its source. Note version numbers and dates. "
              "If sources disagree or something is unclear, say so.")
    prompt = (f"Research current, accurate facts for a short educational LinkedIn post.\n"
              f"Topic: {topic['topic']}\nConcept: {topic['concept']}\n"
              f"Return 5-10 concise bullet facts in your own words, each useful to a QA engineer.")
    return llm.complete(system, prompt, web_search=True)


def generate(llm, topic: dict, day: int, cfg: dict, profile: dict, related: list[dict],
             research_notes: str = "", feedback: str = "") -> dict:
    label = cfg["publishing"]["series_label"].format(day=day)
    prior = "\n".join(f"- Day {p['day']}: {p['topic']} — {p['concept']}" for p in related) or "none"
    revision = ("REVISION FEEDBACK FROM THE QUALITY CHECKER — fix every point:\n" + feedback) if feedback else ""
    prompt = f"""
SERIES LABEL: {label}
PHASE: {topic['phase']} — {topic['phase_name']}
PILLAR: {topic['pillar']}
TOPIC: {topic['topic']}
MAIN CONCEPT: {topic['concept']}
POST TYPE TODAY: {topic['post_type']}
KEYWORDS: {', '.join(topic.get('keywords', []))}

EARLIER POSTS YOU MAY BUILD ON (reference naturally, e.g. "Earlier in the series..."; do not repeat them):
{prior}

AUTHOR PROFILE (the only allowed basis for first-person claims):
{yaml.safe_dump(profile, sort_keys=False)}

RESEARCH NOTES (the only allowed source of specific facts, versions or numbers):
{research_notes or 'None — keep claims general and timeless.'}

{revision}

Return JSON with keys:
  "post": the full post text (with series label line and hashtag line at the end),
  "learning_objective": one sentence — what the reader should learn,
  "keywords": 4-8 lowercase keywords describing the concept actually covered
"""
    data, _ = llm.complete_json(STYLE_GUIDE, prompt)
    return data
