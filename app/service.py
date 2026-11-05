"""Turns a recommender Result into the API response shape."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import repository as repo
from .explain import explain
from .recommender import Scored, recommend

MAX_LIMIT = 20

NEXT_STEP = {
    "cold_start": "Pick a few topics you care about and we'll tailor these to you.",
    "inferred_interests": "These are based on the Circles you're in and your profile. "
                          "Add your interests for more personal picks.",
}


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


def _parse_as_of(value: str | None, conn: sqlite3.Connection) -> str:
    if not value:
        return repo.dataset_as_of(conn)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise BadRequest(f"as_of must be an ISO-8601 datetime, got {value!r}") from e
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _circle_payload(s: Scored, labels: dict[str, str]) -> dict:
    c = s.circle
    return {
        "id": c.id,
        "name": c.name,
        "slug": c.slug,
        "description": c.description,
        "topic": {"slug": c.topic, "label": labels.get(c.topic)} if c.topic else None,
        "cover_image_url": c.cover_image_url,
        "format": c.format,
        "join_policy": c.join_policy,
        "member_count": c.member_count,
        "max_members": c.max_members,
        "spots_left": c.seats_left,
        "leader": {"first_name": c.leader_first_name, "job_title": c.leader_job_title}
        if c.leader_first_name else None,
    }


def get_recommendations(conn: sqlite3.Connection, user_id: int, limit: int = 5,
                        as_of: str | None = None, debug: bool = False) -> dict:
    if not 1 <= limit <= MAX_LIMIT:
        raise BadRequest(f"limit must be between 1 and {MAX_LIMIT}")
    member = repo.get_member(conn, user_id)
    if member is None:
        raise NotFound(f"user {user_id} not found")
    as_of_ts = _parse_as_of(as_of, conn)
    labels = repo.topic_labels(conn)
    circles = repo.get_all_circles(conn, as_of_ts, member.industry)
    result = recommend(member, circles, limit)

    recs = []
    for rank, s in enumerate(result.recommendations, start=1):
        text, reasons = explain(s, labels, result.mode, member)
        item = {
            "rank": rank,
            "circle": _circle_payload(s, labels),
            "explanation": text,
            "reasons": reasons,
            "similar_circles": [{"id": c.id, "name": c.name} for c in s.similar],
        }
        if debug:
            item["score"] = {"total": s.score, "relevance": s.relevance, "activity": s.activity,
                             "availability": s.availability, "peers": s.peers}
        recs.append(item)

    body: dict = {
        "user_id": user_id,
        "as_of": as_of_ts,
        "personalization": result.mode,
        "recommendations": recs,
    }
    if result.mode in NEXT_STEP:
        body["next_step"] = NEXT_STEP[result.mode]
    if debug:
        body["debug"] = {
            "interest_profile": [
                {"topic": i.slug, "weight": i.weight, "source": i.source, "evidence": i.evidence}
                for i in sorted(result.profile.values(), key=lambda i: (-i.weight, i.slug))
            ],
            "excluded": [{"id": c.id, "name": c.name, "reasons": r} for c, r in result.excluded],
        }
    return body
