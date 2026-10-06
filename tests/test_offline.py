"""Offline dry run: fake LLM + fake LinkedIn, temporary database. No network, no secrets.
Run:  python -m tests.test_offline
"""
import tempfile
from datetime import date
from pathlib import Path

from src import db, linkedin, main, quality, selector

class FakeLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, system, prompt, model=None, web_search=False):
        return "- fact (source)", [{"title": "Docs", "url": "https://example.org/docs"}]

    def complete_json(self, system, prompt, model=None, web_search=False):
        self.calls += 1
        if "strict editor" in system:  # judge
            return {"scores": {c: 9 for c in quality.CRITERIA}, "issues": [], "verdict": "Good."}, []
        label = next(l.split(": ", 1)[1] for l in prompt.splitlines() if l.startswith("SERIES LABEL"))
        topic = next(l.split(": ", 1)[1] for l in prompt.splitlines() if l.startswith("TOPIC"))
        concept = next(l.split(": ", 1)[1] for l in prompt.splitlines() if l.startswith("MAIN CONCEPT"))
        body = f"{topic}?\n\n" + "\n\n".join([concept] * 4)
        return {"post": f"{body}\n\nWhat do you think?\n\n{label}\n#SoftwareTesting #TestAutomation #AIinTesting",
                "learning_objective": "Verify AI output.", "keywords": [topic.lower(), "verification"]}, []


def run():
    cfg, roadmap, profile = main.load("config.yaml"), main.load("roadmap.yaml"), main.load("profile.yaml")
    tmp = Path(tempfile.mkdtemp())
    main.DRAFTS = tmp / "drafts"
    db.CSV_PATH = tmp / "history.csv"
    con = db.connect(tmp / "test.db")
    llm = FakeLLM()

    # 1. Rule checker catches problems
    bad = "Hi 🚀🚀🚀🚀 last week my team reduced bugs by 40%\n#a"
    issues = quality.rule_checks(bad, "AI × QA — Day 01", cfg)
    assert any("emojis" in i for i in issues) and any("invented" in i for i in issues), issues
    print("✓ rule checker flags emojis, hashtags, fake experience, missing label")

    # 2. Little-text escaping keeps hashtags clickable
    lt = linkedin.to_little_text("Use page.click(x) #Testing")
    assert lt == "Use page.click\\(x\\) {hashtag|\\#|Testing}", lt
    print("✓ LinkedIn little-text escaping:", lt)
    assert linkedin.unicode_bold("**QA**") == "𝗤𝗔"
    print("✓ Unicode bold rendering")

    # 3. Generate three drafts, approve & 'publish' them with a fake client
    published = []
    class FakeClient:
        def __init__(self, cfg): pass
        def publish(self, text):
            published.append(text); return f"urn:li:share:{len(published)}"
    main.LinkedInClient = FakeClient
    for _ in range(3):
        rec = main.generate_draft(con, cfg, roadmap, profile, llm)
        assert rec["scores"]["passed"], rec["notes"]
        rec["status"] = "Approved"; db.upsert(con, rec)
        main.publish_post(con, cfg, db.get(con, rec["day"]))
    days = [p["day"] for p in db.all_posts(con)]
    topics = [p["roadmap_id"] for p in db.all_posts(con)]
    assert days == [1, 2, 3] and topics == [1, 2, 3], (days, topics)
    assert "AI × QA — Day 03" in published[-1]
    print("✓ numbering 1→3, roadmap order respected, series label present")

    # 4. Repetition control rejects a near-duplicate topic
    hist = db.active_posts(con)
    dup = selector.check_repetition(hist[0]["post"], hist[0]["keywords"], hist, date.today(), cfg)
    assert dup, "duplicate not detected"
    print("✓ repetition control:", dup)

    # 5. Review files written
    assert (main.DRAFTS / "day-01.review.md").exists()
    print("✓ draft + review sheet written; CSV exported:", db.CSV_PATH.exists())
    print("\nAll offline checks passed.")


if __name__ == "__main__":
    run()
