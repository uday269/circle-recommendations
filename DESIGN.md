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

Problem Breakdown & Deepening Understanding: I used Claude to analyze the assessment requirements and requirements edge cases, helping me map out a comprehensive approach before writing code.

Architectural & Design Recommendations: I asked for best-practice recommendations on design patterns, modularity, and scalability suited to the project scope.

Repository & File Organization: I consulted Claude for guidance on structuring the repository, separating concerns cleanly across files, and maintaining a logical directory layout for readability and maintainability.

Clear Explanations & Documentation: I used it to break down complex technical concepts and clarify implementation steps as I built out the solution.

What I Changed or Rejected
While Claude provided strong foundational layouts and recommendations, I actively reviewed and tailored them to fit the exact constraints of the assessment:

File Structure Adjustments: Some suggested file hierarchies were over-engineered for the scale of this project. I simplified and flattened the directory structure to keep the codebase lean, readable, and directly aligned with standard project conventions.

Code Refinement: Instead of accepting generated code blocks wholesale, I adapted the logic to match my preferred coding style, ensure optimal performance, and handle specific edge cases unique to this problem.