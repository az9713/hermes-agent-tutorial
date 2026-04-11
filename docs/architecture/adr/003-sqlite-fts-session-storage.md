# ADR 003: SQLite with FTS5 for session storage

**Status:** Accepted

## Context

Every conversation Hermes has needs to be stored persistently so users can search past conversations, replay sessions for debugging, and use session data for RL training. The storage needs to support:

1. Full-text search over all conversation content
2. Session metadata (title, timestamp, platform, profile)
3. Fast retrieval of recent sessions
4. Zero additional infrastructure requirements (no server to run)
5. File portability (backup = copy a file)

## Decision

Use SQLite with the FTS5 extension for all session storage. The database lives at `~/.hermes/state.db` (follows `HERMES_HOME`).

## Alternatives considered

### Option A: JSON files per session

Store each session as a JSON file in `~/.hermes/sessions/`:

```
~/.hermes/sessions/session_20240101_120000_abc123.json
~/.hermes/sessions/session_20240101_130000_def456.json
```

**Pros:** Simple, human-readable, easy to back up, portable.
**Cons:** Full-text search requires loading and scanning every file. Slow for large session histories. No atomic updates. No efficient metadata indexing.

### Option B: PostgreSQL or similar server database

**Pros:** Scales to multi-user, multi-machine deployments. Excellent full-text search support.
**Cons:** Requires running a server process. Adds infrastructure complexity. Hermes targets "runs on a $5 VPS with no dependencies" — requiring Postgres would break this. Most users have a single-user, single-machine setup.

### Option C: SQLite + FTS5 (chosen)

SQLite is a file-based database that needs no server. FTS5 is a built-in SQLite extension that provides full-text search with BM25 ranking.

**Pros:**
- Zero additional infrastructure (SQLite ships with Python)
- FTS5 full-text search is fast and built in
- Single-file backup
- ACID transactions for data integrity
- Well-understood query model

**Cons:**
- Not suitable for multi-machine deployments (file-based)
- FTS5 is less featured than Elasticsearch or PostgreSQL's full-text search
- Concurrent write performance degrades at very high volume

## Rationale

The target deployment is single-user, single-machine. SQLite is the right database for this scale. FTS5 provides full-text search that's fast enough for the expected volume (thousands of sessions, not millions).

The "runs anywhere with zero dependencies" constraint was decisive. Every Python installation includes SQLite. No setup required.

Additionally, session search is a supplementary feature — users find it useful but the primary value is in recent sessions, which SQLite retrieves trivially via `ORDER BY created_at DESC LIMIT N`.

## Trade-offs

- **What we gave up:** Multi-machine deployments, concurrent write performance at scale.
- **What we accepted:** File-based limitations — `state.db` can't be shared across multiple Hermes instances without a sync layer.
- **What this makes harder:** Deploying Hermes in a distributed or HA setup.

## Consequences

- Session search (`/search`) is fast for thousands of sessions.
- Backup is `cp ~/.hermes/state.db backup.db`.
- Multi-machine deployments require either syncing the file or switching to a different storage backend (which would require code changes).
- Trajectory logs (JSON files in `~/.hermes/logs/`) complement SQLite for use cases that need raw message format (RL training, debugging). Both are written for every session.
