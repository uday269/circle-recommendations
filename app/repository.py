"""All SQL lives here. The recommender works on plain dataclasses and never touches the DB."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class Member:
    id: int
    first_name: str
    bio: str | None
    job_title: str | None
    industry: str | None
    location: str | None
    profile_completeness: float
    stated_interests: list[str]
    circle_ids: set[int]
    # topic slug -> names of the user's current Circles that cover it
    joined_circle_topics: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class CircleCandidate:
    id: int
    name: str
    slug: str
    description: str | None
    topic: str | None
    tags: list[str]
    cover_image_url: str | None
    format: str
    access: str
    join_policy: str
    max_members: int | None
    member_count: int
    feed_posts_30d: int
    chat_messages_30d: int
    last_activity_at: str | None
    # derived (computed in SQL relative to as_of)
    is_full: bool
    seats_left: int | None
    days_since_activity: float | None
    avg_engagement_30d: float
    same_industry_members: int
    leader_first_name: str | None
    leader_job_title: str | None
    leader_location: str | None


def dataset_as_of(conn: sqlite3.Connection) -> str:
    """The snapshot's 'now': the latest timestamp anywhere in the activity data."""
    row = conn.execute(
        """SELECT MAX(ts) FROM (
               SELECT MAX(last_activity_at) AS ts FROM circles
               UNION ALL SELECT MAX(created_at) FROM posts)"""
    ).fetchone()
    return row[0]


def topic_labels(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["slug"]: r["label"] for r in conn.execute("SELECT slug, label FROM topics")}


def get_member(conn: sqlite3.Connection, user_id: int) -> Member | None:
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if u is None:
        return None
    interests = [r[0] for r in conn.execute(
        "SELECT topic_slug FROM user_topic_interests WHERE user_id = ? ORDER BY topic_slug", (user_id,))]
    joined = conn.execute(
        """SELECT c.id, c.name, c.topic_slug FROM memberships m JOIN circles c ON c.id = m.circle_id
           WHERE m.user_id = ? ORDER BY c.id""", (user_id,)).fetchall()
    joined_topics: dict[str, list[str]] = {}
    for r in joined:
        if r["topic_slug"]:
            joined_topics.setdefault(r["topic_slug"], []).append(r["name"])
    return Member(
        id=u["id"], first_name=u["first_name"], bio=u["bio"], job_title=u["job_title"],
        industry=u["industry"], location=u["location"],
        profile_completeness=u["profile_completeness"], stated_interests=interests,
        circle_ids={r["id"] for r in joined}, joined_circle_topics=joined_topics,
    )


_CANDIDATES_SQL = """
WITH recent AS (
    SELECT circle_id,
           AVG(reaction_count + comment_count + save_count) AS avg_engagement
    FROM posts
    WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%fZ', :as_of, '-30 days')
      AND created_at <= :as_of
    GROUP BY circle_id
),
leaders AS (
    -- one leader per Circle in this data; ROW_NUMBER keeps it correct if there are several
    SELECT circle_id, first_name, job_title, location FROM (
        SELECT m.circle_id, u.first_name, u.job_title, u.location,
               ROW_NUMBER() OVER (PARTITION BY m.circle_id ORDER BY m.joined_at) AS rn
        FROM memberships m JOIN users u ON u.id = m.user_id
        WHERE m.role = 'leader')
    WHERE rn = 1
),
peers AS (
    SELECT m.circle_id, COUNT(*) AS n
    FROM memberships m JOIN users u ON u.id = m.user_id
    WHERE :industry IS NOT NULL AND u.industry = :industry
    GROUP BY m.circle_id
)
SELECT c.*,
       (c.max_members IS NOT NULL AND c.member_count >= c.max_members)       AS is_full,
       CASE WHEN c.max_members IS NULL THEN NULL
            ELSE MAX(c.max_members - c.member_count, 0) END                  AS seats_left,
       julianday(:as_of) - julianday(c.last_activity_at)                     AS days_since_activity,
       COALESCE(r.avg_engagement, 0)                                         AS avg_engagement_30d,
       COALESCE(p.n, 0)                                                      AS same_industry_members,
       l.first_name AS leader_first_name, l.job_title AS leader_job_title,
       l.location AS leader_location,
       (SELECT GROUP_CONCAT(tag, ',') FROM circle_tags t WHERE t.circle_id = c.id) AS tag_csv
FROM circles c
LEFT JOIN recent  r ON r.circle_id = c.id
LEFT JOIN leaders l ON l.circle_id = c.id
LEFT JOIN peers   p ON p.circle_id = c.id
ORDER BY c.id
"""


def get_all_circles(conn: sqlite3.Connection, as_of: str, industry: str | None) -> list[CircleCandidate]:
    """Every Circle with its derived health signals. Eligibility filtering happens in the
    recommender (not in SQL) so debug mode can report *why* a Circle was excluded."""
    rows = conn.execute(_CANDIDATES_SQL, {"as_of": as_of, "industry": industry}).fetchall()
    return [
        CircleCandidate(
            id=r["id"], name=r["name"], slug=r["slug"], description=r["description"],
            topic=r["topic_slug"], tags=r["tag_csv"].split(",") if r["tag_csv"] else [],
            cover_image_url=r["cover_image_url"], format=r["format"], access=r["access"],
            join_policy=r["join_policy"], max_members=r["max_members"],
            member_count=r["member_count"], feed_posts_30d=r["feed_posts_30d"],
            chat_messages_30d=r["chat_messages_30d"], last_activity_at=r["last_activity_at"],
            is_full=bool(r["is_full"]), seats_left=r["seats_left"],
            days_since_activity=r["days_since_activity"],
            avg_engagement_30d=float(r["avg_engagement_30d"]),
            same_industry_members=r["same_industry_members"],
            leader_first_name=r["leader_first_name"], leader_job_title=r["leader_job_title"],
            leader_location=r["leader_location"],
        )
        for r in rows
    ]
