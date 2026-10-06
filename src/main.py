"""AI × QA daily LinkedIn pipeline.

Scheduler → history → select topic → research → generate → quality check (regenerate)
→ [review] → publish → save → progress.

Usage:
  python -m src.main run [--force]        scheduled entry point
  python -m src.main generate             create the next draft now
  python -m src.main approve --day N      approve draft N (picks up edits to data/drafts/day-NN.md)
  python -m src.main reject --day N --note "why"
  python -m src.main publish [--day N]    publish an approved post now
  python -m src.main status               series progress
  python -m src.main propose-topics       suggest new roadmap topics for review
  python -m src.main models               list available GitHub Models IDs
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import db, generator, quality, selector
from .linkedin import LinkedInClient, render

ROOT = Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "data" / "drafts"


def load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def now_local(cfg):
    return datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))


def log(msg):
    print(msg, flush=True)


# ── generation ───────────────────────────────────────────────
def generate_draft(con, cfg, roadmap, profile, llm) -> dict:
    today = now_local(cfg).date()
    history = db.active_posts(con)
    day = db.next_day_number(con)
    topic, skipped = selector.select_next(roadmap, history, today, cfg)
    for s in skipped:
        log(f"  skipped {s}")
    log(f"Day {day}: [{topic['pillar']}] {topic['topic']} ({topic['post_type']})")

    notes, sources = "", []
    if topic.get("research"):
        if llm.can_search:
            log("  researching current information…")
            notes, sources = generator.research(llm, topic)
        else:
            log("  research needed but provider has no web search — writing without version-specific claims")
            notes = ("NO RESEARCH AVAILABLE. Do not mention version numbers, release dates, new features, "
                     "pricing or statistics. Stick to stable, well-established concepts.")

    related = selector.build_on(topic, history)
    label = cfg["publishing"]["series_label"].format(day=day)
    feedback, best = "", None
    for attempt in range(1, cfg["quality"]["max_attempts"] + 1):
        draft = generator.generate(llm, topic, day, cfg, profile, related, notes, feedback)
        post = draft["post"].strip()
        # Post-level originality check against real history, not just the roadmap entry.
        rep = selector.check_repetition(post, draft.get("keywords", []), history, today, cfg)
        result = quality.evaluate(llm, post, label, topic, notes, profile, history, cfg)
        if rep:
            result["rule_issues"].append(f"Too similar to an earlier post: {rep}")
            result["passed"] = False
        log(f"  attempt {attempt}: {result['total_50']}/50 "
            f"(acc {result['scores']['accuracy']}) rules={len(result['rule_issues'])} "
            f"→ {'PASS' if result['passed'] else 'retry'}")
        if best is None or result["total_50"] > best[1]["total_50"]:
            best = (draft, result)
        if result["passed"]:
            break
        feedback = "\n".join(f"- {i}" for i in result["rule_issues"] + result["issues"])

    draft, result = best
    rec = {
        "day": day, "date": today.isoformat(), "roadmap_id": topic["id"], "phase": topic["phase"],
        "pillar": topic["pillar"], "topic": topic["topic"], "concept": topic["concept"],
        "post_type": topic["post_type"],
        "keywords": draft.get("keywords") or topic.get("keywords", []),
        "post": draft["post"].strip(), "learning_objective": draft.get("learning_objective", ""),
        "sources": sources,
        "scores": {**result["scores"], "total_50": result["total_50"], "passed": result["passed"]},
        "status": "Draft",
        "notes": ("Research skipped (no web search) — double-check tool details before approving. "
                  if topic.get("research") and not sources else "") + ("" if result["passed"] else "Quality threshold not met: "
                 + "; ".join(result["rule_issues"] + result["issues"])[:1000]),
    }
    db.upsert(con, rec)
    write_review_files(rec, result, cfg)
    return rec


def write_review_files(rec, result, cfg):
    DRAFTS.mkdir(parents=True, exist_ok=True)
    (DRAFTS / f"day-{rec['day']:02d}.md").write_text(rec["post"] + "\n", encoding="utf-8")
    s = rec["scores"]
    lines = [f"# Day {rec['day']} — review sheet", "",
             f"**Topic:** {rec['topic']}  ", f"**Category:** {rec['pillar']} (phase {rec['phase']})  ",
             f"**Post type:** {rec['post_type']}  ",
             f"**Learning objective:** {rec['learning_objective']}  ", "",
             f"## Quality score: {s['total_50']}/50 — {'PASSED' if s['passed'] else 'NOT PASSED'}", ""]
    lines += [f"- {k}: {v}" for k, v in s.items() if k not in ("total_50", "passed")]
    if result.get("verdict"):
        lines += ["", f"Judge: {result['verdict']}"]
    issues = result.get("rule_issues", []) + result.get("issues", [])
    if issues:
        lines += ["", "## Remaining issues"] + [f"- {i}" for i in issues]
    if rec.get("notes"):
        lines += ["", f"> ⚠️ {rec['notes']}"]
    lines += ["", "## Sources"] + ([f"- [{x['title'] or x['url']}]({x['url']})" for x in rec["sources"]] or ["- none (no research required)"])
    lines += ["", "## Preview (as it will appear on LinkedIn)", "", "```", render(rec["post"], cfg), "```"]
    (DRAFTS / f"day-{rec['day']:02d}.review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── publishing ───────────────────────────────────────────────
def publish_post(con, cfg, rec) -> None:
    client = LinkedInClient(cfg)
    visible = render(rec["post"], cfg)
    post_id = client.publish(visible)
    rec.update(status="Published", linkedin_id=post_id, date=now_local(cfg).date().isoformat())
    db.upsert(con, rec)
    log(f"Published Day {rec['day']} ({post_id})")


def published_today(con, cfg) -> bool:
    today = now_local(cfg).date().isoformat()
    return any(p["status"] == "Published" and p["date"] == today for p in db.all_posts(con))


# ── commands ─────────────────────────────────────────────────
def cmd_run(args, con, cfg, roadmap, profile):
    from .llm import LLM
    local = now_local(cfg)
    start = cfg["schedule"]["publish_hour"]
    if not args.force and not start <= local.hour < start + cfg["schedule"].get("window_hours", 1):
        log(f"Not publish hour in {cfg['schedule']['timezone']} ({local:%H:%M}); nothing to do.")
        return
    if published_today(con, cfg):
        log("Already published today.")
        return
    mode = cfg["publishing"]["mode"]
    if mode == "auto":
        rec = generate_draft(con, cfg, roadmap, profile, LLM(cfg))
        if rec["scores"]["passed"]:
            publish_post(con, cfg, rec)
        else:
            log("Quality threshold not met after all attempts — kept as Draft, NOT published.")
        return
    # review mode: publish the oldest approved post, then prepare the next draft
    approved = [p for p in db.all_posts(con) if p["status"] == "Approved"]
    if approved:
        publish_post(con, cfg, approved[0])
    else:
        log("No approved post waiting — skipping today's publication.")
    if not any(p["status"] == "Draft" for p in db.all_posts(con)):
        rec = generate_draft(con, cfg, roadmap, profile, LLM(cfg))
        log(f"New draft ready for review: data/drafts/day-{rec['day']:02d}.md")


def cmd_generate(args, con, cfg, roadmap, profile):
    from .llm import LLM
    rec = generate_draft(con, cfg, roadmap, profile, LLM(cfg))
    log(f"Draft saved: data/drafts/day-{rec['day']:02d}.md (+ .review.md)")


def cmd_approve(args, con, cfg, *_):
    rec = db.get(con, args.day)
    if not rec or rec["status"] != "Draft":
        sys.exit(f"Day {args.day} is not a draft.")
    f = DRAFTS / f"day-{args.day:02d}.md"
    if f.exists():
        rec["post"] = f.read_text(encoding="utf-8").strip()   # keep your manual edits
    label = cfg["publishing"]["series_label"].format(day=args.day)
    issues = quality.rule_checks(rec["post"], label, cfg)
    for i in issues:
        log(f"  warning: {i}")
    if issues and not args.ignore_warnings:
        sys.exit("Fix the warnings or re-run with --ignore-warnings.")
    rec["status"] = "Approved"
    db.upsert(con, rec)
    log(f"Day {args.day} approved; it will be published at the next scheduled run.")


def cmd_reject(args, con, cfg, *_):
    rec = db.get(con, args.day)
    if not rec or rec["status"] == "Published":
        sys.exit(f"Day {args.day} cannot be rejected.")
    with open(ROOT / "data" / "rejections.log", "a", encoding="utf-8") as fh:
        fh.write(f"{now_local(cfg):%Y-%m-%d %H:%M} day={args.day} topic={rec['topic']!r} note={args.note!r}\n")
    db.delete(con, args.day)  # frees the day number and returns the topic to the queue
    for f in DRAFTS.glob(f"day-{args.day:02d}*.md"):
        f.unlink()
    log(f"Day {args.day} rejected. Next `generate` will retry this slot.")


def cmd_publish(args, con, cfg, *_):
    rec = db.get(con, args.day) if args.day else next(
        (p for p in db.all_posts(con) if p["status"] == "Approved"), None)
    if not rec or rec["status"] != "Approved":
        sys.exit("No approved post to publish (approve a draft first).")
    publish_post(con, cfg, rec)


def cmd_status(args, con, cfg, roadmap, _):
    posts = db.all_posts(con)
    pub = [p for p in posts if p["status"] == "Published"]
    by_status = {s: sum(p["status"] == s for p in posts) for s in ("Draft", "Approved", "Published")}
    phase = pub[-1]["phase"] if pub else 1
    log(f"Published: {len(pub)}/{len(roadmap['topics'])} roadmap topics | {by_status}")
    log(f"Current phase: {phase} — {roadmap['phases'][phase]}")
    for p in posts[-7:]:
        log(f"  Day {p['day']:>3} {p['date']} {p['status']:<9} {p['pillar']} {p['topic']} "
            f"({p['scores'].get('total_50', '-')}/50)")


def cmd_propose(args, con, cfg, roadmap, profile):
    from .llm import LLM
    used = "\n".join(f"- {t['topic']}" for t in roadmap["topics"])
    data, _ = LLM(cfg).complete_json(
        "You plan an educational LinkedIn series on QA, test automation and AI.",
        f"Existing topics:\n{used}\n\nPropose 30 NEW, non-overlapping topics continuing the progression "
        "(advanced QA + AI, quality engineering). JSON: {\"topics\": [{\"phase\": 8, \"pillar\": \"P1-P7\", "
        "\"topic\": str, \"concept\": str, \"keywords\": [str], \"research\": bool}]}")
    out = ROOT / "data" / "proposed_topics.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    log(f"Review {out} and copy the topics you like into roadmap.yaml (add unique ids).")


def cmd_models(args, con, cfg, *_):
    from .llm import list_github_models
    models = list_github_models()
    for m in models:
        log(m)
    for key in ("model", "judge_model"):
        ok = cfg["llm"][key] in models
        log(f"{key}: {cfg['llm'][key]} -> {'available' if ok else 'NOT FOUND, pick one from the list above'}")


def main():
    p = argparse.ArgumentParser(description="AI × QA LinkedIn pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--force", action="store_true")
    sub.add_parser("generate")
    a = sub.add_parser("approve"); a.add_argument("--day", type=int, required=True)
    a.add_argument("--ignore-warnings", action="store_true")
    j = sub.add_parser("reject"); j.add_argument("--day", type=int, required=True)
    j.add_argument("--note", default="")
    pb = sub.add_parser("publish"); pb.add_argument("--day", type=int)
    sub.add_parser("status")
    sub.add_parser("propose-topics")
    sub.add_parser("models")
    args = p.parse_args()

    cfg, roadmap, profile = load("config.yaml"), load("roadmap.yaml"), load("profile.yaml")
    con = db.connect()
    {"run": cmd_run, "generate": cmd_generate, "approve": cmd_approve, "reject": cmd_reject,
     "publish": cmd_publish, "status": cmd_status, "propose-topics": cmd_propose, "models": cmd_models}[args.cmd](
        args, con, cfg, roadmap, profile)


if __name__ == "__main__":
    main()
