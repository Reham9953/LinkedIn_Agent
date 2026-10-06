# AI × QA — automated LinkedIn series

A daily pipeline that writes one educational LinkedIn post on QA, test automation and AI, checks it, and publishes it as part of the **AI × QA — Day NN** series.

```
GitHub Actions cron (Africa/Cairo window)
  → load history (data/content.db)
  → select next roadmap topic (phase order, 30-day cooldown, weekday rotation)
  → research with web search (Claude provider only; topics flagged research: true)
  → generate post (GitHub Models for free, or Claude API; style guide + your profile)
  → quality check: rule checks + LLM judge on 8 criteria, regenerate up to 3×
  → review gate (default) or auto-publish
  → publish via LinkedIn Posts API
  → save post, sources, scores; commit history back to the repo
```

## Files

| Path | Purpose |
|---|---|
| `config.yaml` | Schedule, mode (review/auto), model, thresholds, rotation, LinkedIn API version |
| `roadmap.yaml` | 90 topics across the 8 phases and 7 pillars, consumed in order |
| `profile.yaml` | **Your true facts.** The only basis allowed for first-person claims |
| `src/` | Pipeline code (`main.py` is the CLI) |
| `data/content.db` | History database; `data/content_history.csv` mirrors it for Excel/Sheets |
| `data/drafts/` | `day-NN.md` (editable post) and `day-NN.review.md` (scores, sources, preview) |
| `tests/test_offline.py` | Dry run with fake LLM and fake LinkedIn, no network |

## Setup

1. **Create a private GitHub repository** and push this folder.
2. **Fill in `profile.yaml` honestly.** Tools you've actually used, things you're exploring, completed certifications.
3. **AI model access — free by default.** `llm.provider: "github"` uses GitHub Models, which runs on the `GITHUB_TOKEN` that GitHub Actions creates automatically. No payment and no secret to add; the workflow already requests `models: read`. Run the `models` workflow command to list available model IDs if the defaults in `config.yaml` are ever retired. Free-tier limits are per day and per model; this pipeline makes roughly 2–7 calls a day.
   - Trade-off: GitHub Models has no web search, so topics marked `research: true` are written without version numbers, release dates or new-feature claims, and the review sheet reminds you to double-check them.
   - Optional upgrade: switch `provider` to `"anthropic"`, add an `ANTHROPIC_API_KEY` secret from a personal Claude Console account, and research with cited sources is enabled.
4. **LinkedIn app** at linkedin.com/developers:
   - Create an app (it must be associated with a LinkedIn Page; your own Page works).
   - Add the products *Share on LinkedIn* and *Sign In with LinkedIn using OpenID Connect*.
   - Run the 3-legged OAuth flow with scopes `openid profile w_member_social` to get a member access token.
   - Save it as secret `LINKEDIN_ACCESS_TOKEN`. Optionally save `urn:li:person:<id>` as `LINKEDIN_PERSON_URN` (otherwise it is looked up via `/v2/userinfo`).
   - Member tokens expire (typically after 60 days). Put a calendar reminder to renew it.
5. In **Settings → Actions → General**, allow workflows *Read and write* permissions (needed to save history).
6. Check `linkedin.api_version` in `config.yaml` is a currently supported `YYYYMM` version.
7. Run `python -m tests.test_offline` locally to confirm everything is wired up.

## Daily operation (review mode — default)

The 09:00 Cairo run publishes the oldest **Approved** post and then generates the next **Draft**. So each draft gets roughly a day of review time.

1. Open `data/drafts/day-NN.review.md` in GitHub to see the score, sources and a preview.
2. Edit `data/drafts/day-NN.md` directly if you want to change wording.
3. Actions → *AI x QA daily post* → Run workflow → `approve`, day `NN`.
   Or `reject` with a note; the topic goes back into the queue and the next run regenerates it.

If nothing is approved, nothing is published that day. The series number only advances when a post exists, so gaps never break numbering.

Locally the same commands work: `python -m src.main generate | approve --day N | reject --day N --note "..." | publish | status`.

## Auto mode

Set `publishing.mode: "auto"`. Posts that pass the quality check publish without review; posts that fail after 3 attempts are kept as drafts and not published.

## How the rules are enforced

- **Accuracy and fabrication:** specific facts may only come from the research notes, first-person claims only from `profile.yaml`. The judge caps accuracy at 5 for unsupported claims, and a post needs accuracy ≥ 8 to pass. A regex also blocks typical invented-experience phrasing ("last week my team reduced…").
- **Quality score:** all 8 criteria are scored 1–10 (max 80), normalised to 50; pass is ≥ 45/50.
- **Hard format rules:** ≤ 3 emojis, 3–5 hashtags, series label present, ending question, ≤ 2,900 characters, banned clichés.
- **Repetition:** keyword overlap (Jaccard) and TF-IDF cosine similarity against everything in the last 30 days, checked both for the roadmap topic and for the generated text.
- **LinkedIn formatting:** LinkedIn doesn't render markdown, so `**bold**` becomes Unicode bold (set `unicode_bold: false` for plain text, which is better for screen readers). Reserved characters are escaped in LinkedIn's "little" text format; unescaped parentheses can silently cut a post short. Hashtags stay clickable.

## Extending the roadmap

When the 90 topics run out, run `propose-topics`, review `data/proposed_topics.yaml`, and copy the ones you like into `roadmap.yaml` with new unique ids.
