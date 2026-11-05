"""Database setup: create the SQLite schema and load the seed JSON into it."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "db" / "schema.sql"
DATA_DIR = ROOT / "data"
DEFAULT_DB_PATH = ROOT / "circles.db"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: the HTTP server is threaded and the connection is only
    # ever read after seeding, which SQLite handles safely.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _load(name: str, data_dir: Path) -> list[dict]:
    with open(data_dir / name, encoding="utf-8") as f:
        return json.load(f)


def build_database(db_path: Path | str = DEFAULT_DB_PATH, data_dir: Path = DATA_DIR) -> sqlite3.Connection:
    """(Re)create the database from schema.sql + the seed JSON. Idempotent."""
    if str(db_path) != ":memory:":
        Path(db_path).unlink(missing_ok=True)
    conn = connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    topics = _load("topics.json", data_dir)
    users = _load("users.json", data_dir)
    circles = _load("circles.json", data_dir)
    memberships = _load("memberships.json", data_dir)
    posts = _load("activity.json", data_dir)

    with conn:
        conn.executemany(
            "INSERT INTO topics (id, slug, label, domain) VALUES (:id, :slug, :label, :domain)", topics
        )
        conn.executemany(
            """INSERT INTO users (id, first_name, last_name, has_photo, bio, job_title, company,
                                  industry, location, joined_at, profile_completeness)
               VALUES (:id, :first_name, :last_name, :has_photo, :bio, :job_title, :company,
                       :industry, :location, :joined_at, :profile_completeness)""",
            users,
        )
        conn.executemany(
            "INSERT INTO user_topic_interests (user_id, topic_slug) VALUES (?, ?)",
            [(u["id"], slug) for u in users for slug in dict.fromkeys(u["topic_interests"])],
        )
        conn.executemany(
            """INSERT INTO circles (id, name, slug, description, topic_slug, cover_image_url, format,
                                    access, join_policy, max_members, member_count, created_at,
                                    last_activity_at, feed_posts_30d, chat_messages_30d)
               VALUES (:id, :name, :slug, :description, :topic, :cover_image_url, :format, :access,
                       :join_policy, :max_members, :member_count, :created_at, :last_activity_at,
                       :feed_posts_30d, :chat_messages_30d)""",
            circles,
        )
        # Source tags contain occasional duplicates (e.g. circle 17); dict.fromkeys dedupes in order.
        conn.executemany(
            "INSERT INTO circle_tags (circle_id, tag) VALUES (?, ?)",
            [(c["id"], t) for c in circles for t in dict.fromkeys(c["tags"])],
        )
        conn.executemany(
            "INSERT INTO memberships (user_id, circle_id, role, joined_at) "
            "VALUES (:user_id, :circle_id, :role, :joined_at)",
            memberships,
        )
        conn.executemany(
            """INSERT INTO posts (id, circle_id, author_user_id, body, reaction_count, comment_count,
                                  save_count, created_at)
               VALUES (:id, :circle_id, :author_user_id, :body, :reaction_count, :comment_count,
                       :save_count, :created_at)""",
            posts,
        )
    return conn


if __name__ == "__main__":
    c = build_database()
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("topics", "users", "circles", "memberships", "posts")}
    print(f"Built {DEFAULT_DB_PATH.name}: {counts}")
