# Technical design: Circle recommendations

## What I built
`GET /users/{id}/recommendations` returns up to 5 Circles, each with a short member-facing
explanation, structured `reasons`, and `similar_circles` (alternatives on the same topic).
Python standard library only (`http.server`, `sqlite3`, `unittest`), so it runs with no install.
Four steps, each a small pure function:

1. **Interest profile.** Stated interests (weight 1.0), topics she wrote about in her bio (0.6,
   conservative phrase list), and topics of Circles she's already in (0.5).
2. **Eligibility (hard filters).** Exclude Circles she's in, `unlisted`, **full**
   (`member_count >= max_members`), **inactive** (no activity for 45+ days, or no posts and
   fewer than 5 chat messages in 30 days), and identity/life-stage Circles (working motherhood,
   career breaks, being the only one, untagged) unless she opted in through a stated interest or
   her bio. `debug=true` lists every excluded Circle with all the rules it failed.
3. **Score.** `0.50·relevance + 0.30·activity + 0.15·availability + 0.05·peers`
   - *relevance*: primary-topic match = interest weight, tag match = half, +25% per extra matched interest
   - *activity*: posts/30d, log(chat/30d), avg engagement per recent post, recency (7-day half-life)
   - *availability*: open (1.0) vs request (0.75) × reachability (virtual/hybrid, or in-person
     where the leader's city matches hers)
   - *peers*: members who share her industry (light touch)
4. **Diversify.** One Circle per topic; the rest go into `similar_circles`. This collapses the
   near-duplicate Negotiation Circles without a brittle name-similarity rule.

**How the planted cases come out:** user 7 gets #5 Women Who Lead and #6 Speak Up first. User
15 gets one Negotiation Circle (#11), with #10 and #12 as alternatives. User 23 gets #22 Level
Up first, with #20 excluded as `full` and #21 as `inactive` (it's also unlisted). User 42 gets
five active Circles on five different topics plus a `next_step` asking her to add interests.
`tests/` asserts all of this, plus invariants across all 100 users.

## Signals I chose and why
- **Stated interests > bio > existing Circles.** Each is weaker evidence of intent than the one
  before. Explanations name the source ("one of your interests", "you mentioned 'executive
  presence' in your bio") so members can see why and correct it.
- **Activity as both a filter and a score.** A quiet Circle is the most disappointing thing to
  join. Posts, chat and recency all count, because some Circles are busy in chat but quiet on the feed.
- **Not used:** profile completeness and tenure. Neither says what she wants, and explaining a
  recommendation with them would feel like surveillance. Cover image and description are
  presentation, not fit.
- **Leader location as a proxy for where a Circle meets.** It's a soft signal only, because the
  data is sparse. It's the reason #5 (hybrid, Seattle leader) beats #1 (in person, New York
  leader) for a member in Seattle.

## Tradeoffs
- **Rules and weights instead of ML.** With 100 users and no outcome data (joins, retention),
  a learned model has nothing to learn from. Hand-set weights are easy to explain and tune.
  They're constants in one file, each with its reason next to it.
- **Snapshot time.** "Now" defaults to the dataset's latest timestamp (2026-09-21), not the
  wall clock, or every Circle would look inactive. `?as_of=` overrides it, and a test proves
  Circles go inactive when it's set later.
- **Score in Python, not SQL.** One query loads all 40 Circles; filtering in Python lets debug
  output say *why* each was left out. That won't scale, which is the first change below.
- **Identity Circles are opt-in.** Grace can't be shown "Working Mothers United" because of a
  stray tag. I'd rather miss a good match than make an assumption about someone's life.
- **Honest back-fill.** When her interests run out, filler Circles say "Outside your listed
  interests…" instead of implying a match.

## What I'd add for production scale
- **Precompute.** A `circle_stats` table (is_full, activity score, engagement) refreshed every
  few minutes by a job and kept current on join/leave events. Candidate generation moves into
  SQL by topic (`idx_circles_access_topic`, `idx_uti_topic`), reranking runs on a few hundred
  rows, and results are cached per user with a short TTL.
- **Data model.** A real location / meeting city for each Circle, a `community` or
  identity-type field instead of my hard-coded list, and duplicate detection run when a
  Circle is created (embeddings of name and description) so leaders get "a similar Circle
  already exists" before duplicates pile up.
- **Feedback loop.** Log impressions, clicks, join requests and 30-day retention per
  recommendation and reason code. Tune the weights with A/B tests, then try learning-to-rank once
  there's enough data. Track how evenly members are spread across Circles so the largest ones
  don't absorb everyone.
- **Members in control.** "Not interested" and "Show me more like this" controls, and an
  onboarding topic picker that feeds the cold-start path.
- **Ops.** Postgres, FastAPI/OpenAPI, auth (members read only their own), latency and empty-result metrics.

**With more time:** collaborative signals ("members in your Circles also joined…"), using post
text to tell apart Circles that share a topic, and testing explanation wording with real members.

## How I used AI
_During this assessment, I leveraged Claude as an interactive technical partner to accelerate my workflow, deepen my understanding of the problem space, and ensure clean architecture. Specifically, I utilized it across the following areas:

**How I worked with it**
- I gave it the seed dataset. Before writing code, it read the data
  generator (`generate.mjs`) to understand exactly how the planted scenarios were built.
  That surfaced things that weren't obvious from the README: timestamps are anchored to
  2026-09-21 (so "inactive" must be judged against the snapshot date, not today), some
  Circles are unlisted, and two have no topic.
- We built in small steps (schema, then the recommender, then explanations) and checked the
  output for all four planted users after each change, not just at the end.

**What changed after reviewing the output**
- *Location:* the first version ranked The Elevation Circle (in person, leader in New York)
  above Women Who Lead for Priya in Seattle. We added the leader's city as a soft signal
  for where in-person Circles meet.
- *Identity Circles:* it recommended "Working Mothers United" to Grace because of one tag.
  We made identity/life-stage Circles opt-in, because a wrong assumption about someone's
  life is worse than a missed match.
- *Explanation length:* the first explanations ran to five sentences. We cut them to three
  (why it fits, why now, one standout detail) and moved logistics into structured fields.
- *Honest filler:* when a member's interests run out, filler Circles now say
  "Outside your listed interests" instead of implying a match.
- *Reviewer experience:* when I ran it locally, the base URL looked blank in my editor's
  browser, so we added a landing page with links to each scenario.

**How I verified it**
I ran the server and called the API for the planted users myself, read the debug output
to confirm why Circles were excluded, and ran the test suite (planted scenarios plus
checks across all 100 users). I also had it explain the full design back to me in plain
language, so I understand the tradeoffs rather than just the code.