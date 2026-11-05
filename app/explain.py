"""Member-facing explanations.

Rules of thumb:
- Lead with *why it's for her* (her own words or choices), then *why it's worth joining now*
  (it's active), then *what joining looks like* (open/request, format, space left).
- Only state things that are true and verifiable from the data. No invented social proof.
- Never surface signals that would feel like surveillance (we don't mention profile
  completeness, tenure, or anything about specific other members).
"""

from __future__ import annotations

from .recommender import Interest, Scored, location_fit

FORMAT_TEXT = {"virtual": "meets virtually", "hybrid": "meets in person and online", "in_person": "meets in person"}


def _join_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _relevance_sentence(s: Scored, labels: dict[str, str], mode: str) -> tuple[str | None, list[dict]]:
    reasons: list[dict] = []
    if not s.matched:
        if mode == "cold_start":
            reasons.append({"code": "popular_starting_point", "detail": "Among the most active Circles"})
            return "A popular place to start.", reasons
        # Back-fill when her interests run out: say so plainly rather than imply a match.
        reasons.append({"code": "popular_outside_interests", "detail": "Among the most active Circles"})
        return "Outside your listed interests, but one of the liveliest Circles right now.", reasons

    def label(i: Interest) -> str:
        return labels.get(i.slug, i.slug.replace("-", " "))

    stated = [i for i, _ in s.matched if i.source == "stated"]
    for i, kind in s.matched:
        reasons.append({"code": f"{i.source}_interest_{kind}_match", "detail": label(i)})

    if len(stated) >= 2:
        return f"Covers two of your interests: {_join_list([label(i) for i in stated[:2]])}.", reasons
    top, kind = s.matched[0]
    if top.source == "stated":
        if kind == "topic":
            return f"Focused on {label(top)}, one of your interests.", reasons
        return f"Also covers {label(top)}, one of your interests.", reasons
    verb = "focuses on" if kind == "topic" else "touches on"
    if top.source == "bio":
        return f"You mentioned “{top.evidence}” in your bio, and this Circle {verb} {label(top)}.", reasons
    # joined
    return f"Like {top.evidence}, a Circle you're in, this one {verb} {label(top)}.", reasons


def _activity_sentence(s: Scored) -> tuple[str, dict]:
    c = s.circle
    posts, chat = c.feed_posts_30d, c.chat_messages_30d
    days = int(c.days_since_activity or 0)
    if posts >= 8 or chat >= 80:
        lead = "Very active"
    elif posts >= 3 or chat >= 20:
        lead = "Active"
    else:
        lead = "Small and steady"
    bits = [f"{posts} post{'s' if posts != 1 else ''}"]
    if chat:
        bits.append(f"{chat} chat messages")
    text = f"{lead}: {_join_list(bits)} in the past month"
    # Only mention recency when it's not obvious from the volume.
    text += f", last active {days} days ago." if days >= 7 else "."
    return text, {"code": "active_circle", "detail": text}


def _extra(s: Scored, member) -> tuple[str | None, list[dict]]:
    """At most one extra sentence: the single most persuasive concrete detail."""
    c = s.circle
    options: list[tuple[str, dict]] = []
    if location_fit(c, member)[1] and c.format != "virtual" and c.leader_first_name:
        options.append((f"Led by {c.leader_first_name}, who's also in {c.leader_location}.",
                        {"code": "leader_same_city", "detail": c.leader_location}))
    if c.seats_left is not None and c.seats_left <= 3:
        options.append((f"Only {c.seats_left} spot{'s' if c.seats_left != 1 else ''} left.",
                        {"code": "few_spots_left", "detail": f"{c.seats_left} spots left"}))
    if member.industry and c.same_industry_members >= 2:
        n = c.same_industry_members
        options.append((f"{n} members also work in {member.industry}.",
                        {"code": "industry_peers", "detail": f"{n} members in {member.industry}"}))
    if not options:
        return None, []
    return options[0][0], [r for _, r in options]


def explain(s: Scored, labels: dict[str, str], mode: str, member) -> tuple[str, list[dict]]:
    """Returns (explanation text, structured reasons).

    Text is capped at three short sentences: why it fits her, why now, one standout detail.
    Join policy / format / spots left are returned as structured fields for the UI to render
    as chips, so they don't need to be repeated in prose."""
    c = s.circle
    rel, reasons = _relevance_sentence(s, labels, mode)
    act, act_reason = _activity_sentence(s)
    extra, extra_reasons = _extra(s, member)
    text = " ".join(x for x in (rel, act, extra) if x)
    logistics = [
        {"code": f"join_{c.join_policy}", "detail": "Open to join" if c.join_policy == "open" else "Request to join"},
        {"code": f"format_{c.format}", "detail": FORMAT_TEXT[c.format]},
    ]
    return text, reasons + [act_reason] + extra_reasons + logistics
