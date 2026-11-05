# Circle Recommendations API

Recommends Lean In Circles a member should join, with a short explanation for each,
written for the member.

- **Design doc (1 page):** [DESIGN.md](DESIGN.md)
- **Database schema + indexing rationale:** [docs/schema.md](docs/schema.md) · DDL in [db/schema.sql](db/schema.sql)
- **Sample responses for the four planted scenarios:** [docs/examples/](docs/examples)

## Run it

Requires **Python 3.10+**. No packages to install: it uses only the standard library.

```bash
git clone <this repo> && cd circle-recs
python3 -m app.server            # builds circles.db from data/*.json, serves on :8000
```

Then open **http://127.0.0.1:8000** in a browser. It's a small landing page with clickable links to
each planted scenario. The recommendations themselves are at `/users/{user_id}/recommendations`.
(Use Chrome, Safari or Firefox. VS Code's built-in preview can show JSON as a blank page.)

Or from a terminal:

```bash
curl "localhost:8000/users/7/recommendations"
curl "localhost:8000/users/15/recommendations?limit=3"
curl "localhost:8000/users/23/recommendations?debug=true"   # + scores, interest profile, excluded Circles and why
curl "localhost:8000/users/42/recommendations"
```

Run the tests (planted scenarios, invariants across all 100 users, HTTP layer):

```bash
python3 -m unittest -v
```

Optional: `python3 -m app.db` rebuilds `circles.db` on its own, if you want to inspect it with `sqlite3`.

## API

`GET /users/{user_id}/recommendations`

| Query param | Default | |
|---|---|---|
| `limit` | 5 | 1–20 |
| `as_of` | dataset snapshot (`2026-09-21`) | ISO-8601. "Now" for activity checks. The seed data is a snapshot, so the wall clock would make every Circle look inactive. |
| `debug` | false | Adds score breakdown, the inferred interest profile, and every excluded Circle with the rules it failed |

Response (trimmed):

```json
{
  "user_id": 7,
  "as_of": "2026-09-21T00:00:00.000Z",
  "personalization": "stated_interests",
  "recommendations": [
    {
      "rank": 1,
      "circle": { "id": 5, "name": "Women Who Lead", "topic": {"slug": "leadership-skills", "label": "Leadership skills"},
                  "format": "hybrid", "join_policy": "open", "member_count": 24, "max_members": 31, "spots_left": 7,
                  "leader": {"first_name": "Ingrid", "job_title": "Recruiter"} },
      "explanation": "Focused on Leadership skills, one of your interests. Very active: 11 posts and 165 chat messages in the past month. Led by Ingrid, who's also in Seattle, WA.",
      "reasons": [{"code": "stated_interest_topic_match", "detail": "Leadership skills"}, "..."],
      "similar_circles": [{"id": 1, "name": "The Elevation Circle"}, {"id": 35, "name": "Board-Ready Women"}]
    }
  ]
}
```

- `personalization` is `stated_interests`, `inferred_interests` (from bio or current Circles) or
  `cold_start`. The last two include a `next_step` prompting the member to add interests.
- `reasons` are stable codes for the client and analytics. `explanation` is the prose to show.
- `similar_circles` are same-topic alternatives that were collapsed to keep the list varied.
- Errors: `404` for an unknown user or route, `400` for a bad `limit` or `as_of`.

## Planted scenarios

| Scenario | User | Result |
|---|---|---|
| Obvious matches | 7 | #5 Women Who Lead, #6 Speak Up Circle ranked 1–2 |
| Duplicates | 15 | One Negotiation Circle (#11), with #10 and #12 under `similar_circles` |
| Full / inactive | 23 | #22 Level Up Collective first. #20 excluded `full`, #21 excluded `inactive` (also unlisted) |
| Cold start | 42 | 5 active Circles on 5 different topics, no identity-specific Circles, plus a `next_step` nudge |

## Layout

```
app/
  db.py           schema + seed loader (data/*.json -> SQLite)
  repository.py   all SQL; derived signals (full, spots left, recency, engagement) computed per as_of
  recommender.py  interest profile -> eligibility filters -> scoring -> topic diversity (pure functions)
  explain.py      member-facing explanation + structured reasons
  service.py      response shape, validation
  server.py       stdlib HTTP server
db/schema.sql     DDL with indexes
tests/            unittest suite
data/             seed dataset (unchanged)
```
