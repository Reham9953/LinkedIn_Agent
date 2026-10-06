"""Content history database (SQLite) with CSV export for spreadsheet viewing."""
import csv
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "content.db"
CSV_PATH = DB_PATH.with_name("content_history.csv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    day          INTEGER PRIMARY KEY,   -- series number
    date         TEXT NOT NULL,         -- local publishing date (YYYY-MM-DD)
    roadmap_id   INTEGER,
    phase        INTEGER,
    pillar       TEXT,
    topic        TEXT,
    concept      TEXT,
    post_type    TEXT,
    keywords     TEXT,                  -- JSON list
    post         TEXT,
    learning_objective TEXT,
    sources      TEXT,                  -- JSON list of {title,url}
    scores       TEXT,                  -- JSON dict
    status       TEXT NOT NULL,         -- Draft / Approved / Published / Rejected
    linkedin_id  TEXT,
    engagement   TEXT,
    notes        TEXT
);
"""

JSON_FIELDS = ("keywords", "sources", "scores")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _decode(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in JSON_FIELDS:
        d[f] = json.loads(d[f]) if d.get(f) else ([] if f != "scores" else {})
    return d


def all_posts(con) -> list[dict]:
    return [_decode(r) for r in con.execute("SELECT * FROM posts ORDER BY day")]


def active_posts(con) -> list[dict]:
    """Posts that count for numbering and repetition (everything except rejected)."""
    return [p for p in all_posts(con) if p["status"] != "Rejected"]


def next_day_number(con) -> int:
    row = con.execute("SELECT MAX(day) FROM posts WHERE status != 'Rejected'").fetchone()
    return (row[0] or 0) + 1


def get(con, day: int) -> dict | None:
    r = con.execute("SELECT * FROM posts WHERE day = ?", (day,)).fetchone()
    return _decode(r) if r else None


def latest_with_status(con, status: str) -> dict | None:
    r = con.execute("SELECT * FROM posts WHERE status = ? ORDER BY day DESC LIMIT 1",
                    (status,)).fetchone()
    return _decode(r) if r else None


def upsert(con, rec: dict) -> None:
    rec = dict(rec)
    for f in JSON_FIELDS:
        if f in rec and not isinstance(rec[f], str):
            rec[f] = json.dumps(rec[f], ensure_ascii=False)
    cols = ", ".join(rec)
    marks = ", ".join("?" for _ in rec)
    updates = ", ".join(f"{c}=excluded.{c}" for c in rec if c != "day")
    con.execute(f"INSERT INTO posts ({cols}) VALUES ({marks}) "
                f"ON CONFLICT(day) DO UPDATE SET {updates}", list(rec.values()))
    con.commit()
    export_csv(con)


def delete(con, day: int) -> None:
    con.execute("DELETE FROM posts WHERE day = ?", (day,))
    con.commit()
    export_csv(con)


def export_csv(con, path: Path | None = None) -> None:
    path = path or CSV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = all_posts(con)
    header = ["day", "date", "pillar", "topic", "concept", "keywords", "post",
              "sources", "status", "engagement", "notes", "phase", "post_type",
              "learning_objective", "scores", "linkedin_id", "roadmap_id"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["keywords"] = ", ".join(r["keywords"])
            r["sources"] = " | ".join(s.get("url", "") for s in r["sources"])
            r["scores"] = json.dumps(r["scores"])
            w.writerow(r)
