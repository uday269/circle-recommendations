"""Circle recommender: eligibility filters -> scoring -> topic diversity -> explanations.

Pure functions over dataclasses from repository.py, so every rule here is unit-testable
without a database. All tunables are module-level constants with the reasoning beside them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .repository import CircleCandidate, Member

# ---------------------------------------------------------------------------------------
# Eligibility (hard filters). A Circle that fails any of these is never recommended:
# recommending a Circle the member can't join, can't find, or that has gone quiet is a
# worse experience than recommending nothing.
# ---------------------------------------------------------------------------------------
INACTIVE_AFTER_DAYS = 45      # no activity of any kind for ~6 weeks = the group has stalled
MIN_RECENT_SIGNAL = 1         # need >=1 feed post OR >=5 chat messages in the last 30 days
MIN_RECENT_CHAT = 5

# ---------------------------------------------------------------------------------------
# Interest weights: how much we trust each source of "this member cares about topic X".
# ---------------------------------------------------------------------------------------
W_STATED = 1.0                # she picked it herself
W_BIO = 0.6                   # she wrote about it, but didn't select it
W_JOINED = 0.5                # she's in a Circle on it (could be incidental)
TAG_MATCH_FACTOR = 0.5        # tags are free-form and noisier than the canonical topic
EXTRA_MATCH_BONUS = 0.25      # a Circle covering a second interest is a better fit

# Final blend. Relevance dominates when we have interests; with none (cold start) the
# ranking falls through to activity/availability, i.e. "the healthiest Circles to try".
W_RELEVANCE, W_ACTIVITY, W_AVAILABILITY, W_PEERS = 0.50, 0.30, 0.15, 0.05

# Activity normalization caps (roughly the top of the observed range for active Circles).
POSTS_CAP, CHAT_CAP, ENGAGEMENT_CAP, RECENCY_HALF_LIFE_DAYS = 12, 150, 20, 7

# Diversity: show at most this many Circles per topic before back-filling.
MAX_PER_TOPIC = 1

# Identity / life-stage topics. Suggesting "Working Mothers United" to someone because of
# a stray tag, or to a brand-new member with no profile, presumes something personal about
# her. These Circles are only recommended when she opted in herself: a stated interest, or
# something she wrote in her own bio. The same goes for Circles with no topic at all (in
# this data: "The Latina Coalition", "Women of the 405"), where we can't say why it fits.
OPT_IN_TOPICS = {"working-motherhood", "career-breaks", "being-the-only-one"}
OPT_IN_SOURCES = {"stated", "bio"}

# Small, hand-written phrase -> topic map for bios. Deliberately conservative: an
# inferred interest that's wrong is more off-putting than a missing one.
BIO_PHRASES: list[tuple[str, str]] = [
    (r"\bleadership\b|\bleaders?\b", "leadership-skills"),
    (r"\bfirst-time manager\b|\bmanaging (?:a|my) team\b", "managing-a-team"),
    (r"\bmentor(?:ing|ship)?\b", "sponsorship-mentorship"),
    (r"\bcareer break\b|\breturning to work\b", "career-breaks"),
    (r"\bcomp conversation\b|\bpay equity\b|\bnegotiat\w*", "negotiation"),
    (r"\bpromotion\b|\bnext level\b", "getting-promoted"),
    (r"\bconfidence\b", "confidence"),
    (r"\bexecutive presence\b", "visibility-recognition"),
]


@dataclass
class Interest:
    slug: str
    weight: float
    source: str           # 'stated' | 'bio' | 'joined'
    evidence: str | None  # bio phrase or name of the Circle she's already in


@dataclass
class Scored:
    circle: CircleCandidate
    score: float
    relevance: float
    activity: float
    availability: float
    peers: float
    matched: list[tuple[Interest, str]]  # (interest, 'topic'|'tag')
    similar: list[CircleCandidate] = field(default_factory=list)


# ------------------------------------------------------------------ interest profile ---
def build_interest_profile(member: Member) -> dict[str, Interest]:
    profile: dict[str, Interest] = {}

    def add(i: Interest) -> None:
        if i.slug not in profile or profile[i.slug].weight < i.weight:
            profile[i.slug] = i

    for slug in member.stated_interests:
        add(Interest(slug, W_STATED, "stated", None))
    if member.bio:
        for pattern, slug in BIO_PHRASES:
            m = re.search(pattern, member.bio, flags=re.IGNORECASE)
            if m:
                add(Interest(slug, W_BIO, "bio", m.group(0)))
    for slug, circle_names in member.joined_circle_topics.items():
        add(Interest(slug, W_JOINED, "joined", circle_names[0]))
    return profile


def personalization_mode(profile: dict[str, Interest]) -> str:
    if any(i.source == "stated" for i in profile.values()):
        return "stated_interests"
    if profile:
        return "inferred_interests"
    return "cold_start"


# ------------------------------------------------------------------------ filtering ---
def exclusion_reasons(c: CircleCandidate, member: Member, profile: dict[str, Interest]) -> list[str]:
    """Every rule the Circle fails (all of them, so debug output tells the whole story)."""
    reasons = []
    if c.id in member.circle_ids:
        reasons.append("already_member")
    if c.access != "public":
        reasons.append("not_discoverable")   # unlisted Circles are invite/link-only by design
    if c.is_full:
        reasons.append("full")
    stale = c.days_since_activity is None or c.days_since_activity > INACTIVE_AFTER_DAYS
    quiet = c.feed_posts_30d < MIN_RECENT_SIGNAL and c.chat_messages_30d < MIN_RECENT_CHAT
    if stale or quiet:
        reasons.append("inactive")
    if c.topic is None:
        reasons.append("no_topic_to_match")
    elif c.topic in OPT_IN_TOPICS:
        interest = profile.get(c.topic)
        if interest is None or interest.source not in OPT_IN_SOURCES:
            reasons.append("identity_topic_not_opted_in")
    return reasons


def _city(loc: str | None) -> str | None:
    return None if not loc or loc.strip().lower() == "remote" else loc.strip()


def location_fit(c: CircleCandidate, member: Member) -> tuple[float, bool]:
    """(score, same_city). Circles have no location column; the leader's city is the best
    available proxy for where an in-person Circle meets. Used as a soft signal only."""
    mine, theirs = _city(member.location), _city(c.leader_location)
    same_city = bool(mine and theirs and mine == theirs)
    if c.format == "virtual":
        return 1.0, same_city
    if same_city:
        return 1.0, True
    if c.format == "hybrid":
        return 0.9, False            # can always attend online
    if mine and theirs:
        return 0.4, False            # in-person, and very likely in another city
    return 0.7, False                # in-person, location unknown on one side


# -------------------------------------------------------------------------- scoring ---
def relevance(c: CircleCandidate, profile: dict[str, Interest]) -> tuple[float, list[tuple[Interest, str]]]:
    hits: list[tuple[float, Interest, str]] = []
    for slug, interest in profile.items():
        if c.topic == slug:
            hits.append((interest.weight, interest, "topic"))
        elif slug in c.tags:
            hits.append((interest.weight * TAG_MATCH_FACTOR, interest, "tag"))
    if not hits:
        return 0.0, []
    hits.sort(key=lambda h: -h[0])
    score = hits[0][0] + EXTRA_MATCH_BONUS * sum(h[0] for h in hits[1:])
    return min(score, 1.0), [(i, kind) for _, i, kind in hits]


def activity(c: CircleCandidate) -> float:
    posts = min(c.feed_posts_30d / POSTS_CAP, 1.0)
    chat = min(math.log1p(c.chat_messages_30d) / math.log1p(CHAT_CAP), 1.0)
    engagement = min(c.avg_engagement_30d / ENGAGEMENT_CAP, 1.0)
    recency = 0.5 ** (max(c.days_since_activity or 0.0, 0.0) / RECENCY_HALF_LIFE_DAYS)
    return 0.35 * posts + 0.25 * chat + 0.20 * engagement + 0.20 * recency


def availability(c: CircleCandidate, member: Member) -> float:
    """Can she realistically join and attend? Open > request; reachable format/location."""
    join = 1.0 if c.join_policy == "open" else 0.75
    return join * location_fit(c, member)[0]


def peers(c: CircleCandidate, member: Member) -> float:
    return min(c.same_industry_members / 3, 1.0) if member.industry else 0.0


def score_circle(c: CircleCandidate, member: Member, profile: dict[str, Interest]) -> Scored:
    r, matched = relevance(c, profile)
    a, v, p = activity(c), availability(c, member), peers(c, member)
    total = W_RELEVANCE * r + W_ACTIVITY * a + W_AVAILABILITY * v + W_PEERS * p
    return Scored(c, round(total, 4), round(r, 4), round(a, 4), round(v, 4), round(p, 4), matched)


# ------------------------------------------------------------------------ diversity ---
def diversify(ranked: list[Scored], limit: int) -> list[Scored]:
    """Keep the best Circle per topic; attach the rest as 'similar' alternatives.

    Near-duplicate Circles (same topic, similar activity) otherwise crowd the list. If we
    run out of distinct topics before `limit`, back-fill with the next-best suppressed ones.
    """
    picked: list[Scored] = []
    per_topic: dict[str, Scored] = {}
    overflow: list[Scored] = []
    for s in ranked:
        key = s.circle.topic or f"circle:{s.circle.id}"  # topic-less Circles are their own cluster
        lead = per_topic.get(key)
        if lead is None:
            per_topic[key] = s
            picked.append(s)
        else:
            lead.similar.append(s.circle)
            overflow.append(s)
    result = picked[:limit]
    for s in overflow:
        if len(result) >= limit:
            break
        result.append(s)
    # A back-filled Circle shouldn't also be listed as an alternative.
    shown = {s.circle.id for s in result}
    for s in result:
        s.similar = [c for c in s.similar if c.id not in shown]
    return result


# ---------------------------------------------------------------------------- entry ---
@dataclass
class Result:
    mode: str
    recommendations: list[Scored]
    excluded: list[tuple[CircleCandidate, list[str]]]
    profile: dict[str, Interest]


def recommend(member: Member, circles: list[CircleCandidate], limit: int = 5) -> Result:
    profile = build_interest_profile(member)
    excluded, scored = [], []
    for c in circles:
        reasons = exclusion_reasons(c, member, profile)
        if reasons:
            excluded.append((c, reasons))
        else:
            scored.append(score_circle(c, member, profile))
    # Deterministic tie-break: score, then more recent activity, then id.
    scored.sort(key=lambda s: (-s.score, s.circle.days_since_activity or 0, s.circle.id))
    return Result(personalization_mode(profile), diversify(scored, limit), excluded, profile)
