-- Circle recommendations: relational schema (SQLite dialect; ports to Postgres as-is
-- apart from types noted inline). See docs/schema.md for the rationale.

PRAGMA foreign_keys = ON;

-- Topic taxonomy. Circles and members reference topics by slug, so slug is UNIQUE and
-- is what we join on (stable, human-readable, matches the source data).
CREATE TABLE topics (
    id      INTEGER PRIMARY KEY,
    slug    TEXT NOT NULL UNIQUE,
    label   TEXT NOT NULL,
    domain  TEXT NOT NULL DEFAULT 'content'
);

CREATE TABLE users (
    id                    INTEGER PRIMARY KEY,
    first_name            TEXT NOT NULL,
    last_name             TEXT NOT NULL,
    has_photo             INTEGER NOT NULL DEFAULT 0,      -- BOOLEAN in Postgres
    bio                   TEXT,
    job_title             TEXT,
    company               TEXT,
    industry              TEXT,
    location              TEXT,
    joined_at             TEXT NOT NULL,                    -- TIMESTAMPTZ in Postgres
    profile_completeness  REAL NOT NULL DEFAULT 0
);

-- Stated interests: many-to-many users <-> topics. Normalized (not a JSON array) so
-- "who is interested in X" and "what is user U interested in" are both index lookups.
CREATE TABLE user_topic_interests (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_slug  TEXT    NOT NULL REFERENCES topics(slug),
    PRIMARY KEY (user_id, topic_slug)
);
CREATE INDEX idx_uti_topic ON user_topic_interests(topic_slug);

CREATE TABLE circles (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    slug               TEXT NOT NULL UNIQUE,
    description        TEXT,
    topic_slug         TEXT REFERENCES topics(slug),        -- nullable: some Circles have no topic
    cover_image_url    TEXT,
    format             TEXT NOT NULL CHECK (format IN ('in_person','virtual','hybrid')),
    access             TEXT NOT NULL CHECK (access IN ('public','unlisted')),
    join_policy        TEXT NOT NULL CHECK (join_policy IN ('open','request')),
    max_members        INTEGER,                              -- NULL = uncapped
    member_count       INTEGER NOT NULL DEFAULT 0,           -- denormalized counter (see docs)
    created_at         TEXT NOT NULL,
    last_activity_at   TEXT,
    feed_posts_30d     INTEGER NOT NULL DEFAULT 0,           -- rolling aggregates, refreshed by a job
    chat_messages_30d  INTEGER NOT NULL DEFAULT 0
);
-- Candidate generation filters on discoverability first, then groups by topic.
CREATE INDEX idx_circles_access_topic ON circles(access, topic_slug);

-- Free-form tags. Kept separate from the canonical topic: tags are noisier, so the
-- recommender weights a tag match lower than a primary-topic match.
CREATE TABLE circle_tags (
    circle_id  INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    tag        TEXT    NOT NULL,
    PRIMARY KEY (circle_id, tag)
);
CREATE INDEX idx_circle_tags_tag ON circle_tags(tag);

-- Membership is the join table; leaders are members with role='leader'.
-- PK (user_id, circle_id) serves "which Circles is U in" (the exclusion step of every
-- request) and enforces one membership per pair. The reverse index serves
-- "members/leaders of C" (peer signals, leader lookup).
CREATE TABLE memberships (
    user_id    INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    circle_id  INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL CHECK (role IN ('member','leader')),
    joined_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, circle_id)
);
CREATE INDEX idx_memberships_circle_role ON memberships(circle_id, role);

-- Circle feed posts. Only engagement counts are used for ranking.
CREATE TABLE posts (
    id              INTEGER PRIMARY KEY,
    circle_id       INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    author_user_id  INTEGER NOT NULL REFERENCES users(id),
    body            TEXT    NOT NULL,
    reaction_count  INTEGER NOT NULL DEFAULT 0,
    comment_count   INTEGER NOT NULL DEFAULT 0,
    save_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);
-- "Engagement on posts in C during the last 30 days" is a range scan on this index.
CREATE INDEX idx_posts_circle_created ON posts(circle_id, created_at);

-- Derived signals (is the Circle full? how active is it?) are deliberately NOT stored.
-- They are computed by one parameterized query in app/repository.py, relative to an
-- "as of" timestamp (the seed data is a snapshot; judging it against the wall clock
-- would make every Circle look dead). In production this becomes a circle_stats table
-- refreshed by a scheduled job, so the request path never aggregates posts.
