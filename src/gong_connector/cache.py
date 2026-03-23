"""SQLite cache with FTS5 full-text search for Gong transcripts."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = os.path.expanduser("~/.gong_connector")
DEFAULT_TTL_SECONDS = 3600  # 1 hour


class TranscriptCache:
    """Local SQLite cache for Gong calls, transcripts, and analytics."""

    def __init__(
        self,
        cache_dir: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.ttl = ttl_seconds
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        db_path = os.path.join(self.cache_dir, "cache.db")
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY,
                title TEXT,
                started TEXT,
                duration INTEGER,
                direction TEXT,
                parties_json TEXT,
                metadata_json TEXT,
                content_json TEXT,
                cached_at REAL
            );

            CREATE TABLE IF NOT EXISTS transcripts (
                call_id TEXT PRIMARY KEY,
                transcript_json TEXT,
                transcript_text TEXT,
                cached_at REAL,
                FOREIGN KEY (call_id) REFERENCES calls(call_id)
            );

            CREATE TABLE IF NOT EXISTS analytics (
                call_id TEXT PRIMARY KEY,
                analytics_json TEXT,
                cached_at REAL,
                FOREIGN KEY (call_id) REFERENCES calls(call_id)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                call_id,
                transcript_text,
                content=transcripts,
                content_rowid=rowid
            );

            CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
                INSERT INTO transcript_fts(rowid, call_id, transcript_text)
                VALUES (new.rowid, new.call_id, new.transcript_text);
            END;

            CREATE TRIGGER IF NOT EXISTS transcripts_ad AFTER DELETE ON transcripts BEGIN
                INSERT INTO transcript_fts(transcript_fts, rowid, call_id, transcript_text)
                VALUES ('delete', old.rowid, old.call_id, old.transcript_text);
            END;

            CREATE TRIGGER IF NOT EXISTS transcripts_au AFTER UPDATE ON transcripts BEGIN
                INSERT INTO transcript_fts(transcript_fts, rowid, call_id, transcript_text)
                VALUES ('delete', old.rowid, old.call_id, old.transcript_text);
                INSERT INTO transcript_fts(rowid, call_id, transcript_text)
                VALUES (new.rowid, new.call_id, new.transcript_text);
            END;
        """)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _is_fresh(self, cached_at: float | None) -> bool:
        if cached_at is None:
            return False
        return (time.time() - cached_at) < self.ttl

    # ── Calls ───────────────────────────────────────────────────────

    def upsert_call(self, call: dict[str, Any]) -> None:
        meta = call.get("metaData", {})
        call_id = meta.get("id", "")
        parties = call.get("parties", [])
        content = call.get("content", {})
        self.conn.execute(
            """INSERT OR REPLACE INTO calls
               (call_id, title, started, duration, direction, parties_json, metadata_json, content_json, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                call_id,
                meta.get("title", ""),
                meta.get("started", ""),
                meta.get("duration", 0),
                meta.get("direction", ""),
                json.dumps(parties),
                json.dumps(meta),
                json.dumps(content),
                time.time(),
            ),
        )
        self.conn.commit()

    def upsert_calls(self, calls: list[dict[str, Any]]) -> None:
        for call in calls:
            self.upsert_call(call)

    def get_call(self, call_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM calls WHERE call_id = ?", (call_id,)
        ).fetchone()
        if not row:
            return None
        if not self._is_fresh(row["cached_at"]):
            return None
        return self._row_to_call(row)

    def list_calls(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM calls WHERE 1=1"
        params: list[Any] = []
        if from_date:
            query += " AND started >= ?"
            params.append(from_date)
        if to_date:
            query += " AND started <= ?"
            params.append(to_date)
        query += " ORDER BY started DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_call(r) for r in rows]

    def _row_to_call(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "call_id": row["call_id"],
            "title": row["title"],
            "started": row["started"],
            "duration": row["duration"],
            "direction": row["direction"],
            "parties": json.loads(row["parties_json"]) if row["parties_json"] else [],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            "content": json.loads(row["content_json"]) if row["content_json"] else {},
        }

    # ── Transcripts ─────────────────────────────────────────────────

    def upsert_transcript(
        self, call_id: str, transcript: dict[str, Any]
    ) -> None:
        transcript_text = self._flatten_transcript(transcript)
        # Delete first to trigger FTS cleanup
        self.conn.execute(
            "DELETE FROM transcripts WHERE call_id = ?", (call_id,)
        )
        self.conn.execute(
            """INSERT INTO transcripts (call_id, transcript_json, transcript_text, cached_at)
               VALUES (?, ?, ?, ?)""",
            (call_id, json.dumps(transcript), transcript_text, time.time()),
        )
        self.conn.commit()

    def get_transcript(self, call_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM transcripts WHERE call_id = ?", (call_id,)
        ).fetchone()
        if not row or not self._is_fresh(row["cached_at"]):
            return None
        return json.loads(row["transcript_json"])

    def search_transcripts(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search across cached transcripts."""
        rows = self.conn.execute(
            """SELECT t.call_id, t.transcript_json,
                      snippet(transcript_fts, 1, '>>>', '<<<', '...', 64) as snippet,
                      c.title, c.started, c.parties_json
               FROM transcript_fts f
               JOIN transcripts t ON f.call_id = t.call_id
               LEFT JOIN calls c ON t.call_id = c.call_id
               WHERE transcript_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        results = []
        for row in rows:
            results.append({
                "call_id": row["call_id"],
                "title": row["title"] or "Unknown",
                "started": row["started"] or "",
                "parties": json.loads(row["parties_json"]) if row["parties_json"] else [],
                "snippet": row["snippet"],
            })
        return results

    def _flatten_transcript(self, transcript: dict[str, Any]) -> str:
        """Convert structured transcript to searchable plain text."""
        parts: list[str] = []
        for entry in transcript.get("transcript", []):
            speaker = entry.get("speakerName", entry.get("speakerId", "Unknown"))
            sentences = entry.get("sentences", [])
            text = " ".join(s.get("text", "") for s in sentences)
            if text.strip():
                parts.append(f"{speaker}: {text}")
        return "\n".join(parts)

    def last_sync_time(self) -> float | None:
        """Return the most recent cached_at timestamp from transcripts."""
        row = self.conn.execute(
            "SELECT MAX(cached_at) as latest FROM transcripts"
        ).fetchone()
        return row["latest"] if row and row["latest"] else None

    def has_any_transcripts(self) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM transcripts"
        ).fetchone()
        return row["cnt"] > 0 if row else False

    def get_cached_call_ids(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT call_id FROM transcripts WHERE ? - cached_at < ?",
            (time.time(), self.ttl),
        ).fetchall()
        return {r["call_id"] for r in rows}

    # ── Analytics ───────────────────────────────────────────────────

    def upsert_analytics(self, call_id: str, analytics: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO analytics (call_id, analytics_json, cached_at)
               VALUES (?, ?, ?)""",
            (call_id, json.dumps(analytics), time.time()),
        )
        self.conn.commit()

    def get_analytics(self, call_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM analytics WHERE call_id = ?", (call_id,)
        ).fetchone()
        if not row or not self._is_fresh(row["cached_at"]):
            return None
        return json.loads(row["analytics_json"])
