"""MCP server exposing Gong call data to Claude."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from .cache import TranscriptCache
from .gong_client import GongClient, GongClientError

mcp = FastMCP("Gong Connector")

# Lazily initialized singletons
_client: GongClient | None = None
_cache: TranscriptCache | None = None


def _get_client() -> GongClient:
    global _client
    if _client is None:
        _client = GongClient()
    return _client


def _get_cache() -> TranscriptCache:
    global _cache
    if _cache is None:
        _cache = TranscriptCache()
    return _cache


async def _sync_recent_calls(
    days: int = 365,
    from_date: str | None = None,
    to_date: str | None = None,
    max_calls: int = 20000,
) -> int:
    """Sync recent calls and their transcripts into the cache.

    Returns the number of calls synced.
    """
    client = _get_client()
    cache = _get_cache()

    if not from_date:
        from_dt = datetime.now(timezone.utc) - timedelta(days=days)
        from_date = from_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not to_date:
        to_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    calls = await client.list_all_calls(from_date=from_date, to_date=to_date, max_calls=max_calls)
    cache.upsert_calls(calls)

    # Fetch transcripts for calls we don't have cached
    cached_ids = cache.get_cached_call_ids()
    call_ids = [c["metaData"]["id"] for c in calls if c.get("metaData", {}).get("id")]
    missing_ids = [cid for cid in call_ids if cid not in cached_ids]

    # Fetch transcripts in batches of 10
    for i in range(0, len(missing_ids), 10):
        batch = missing_ids[i : i + 10]
        try:
            transcripts = await client.get_transcripts(batch)
            for t in transcripts:
                cid = t.get("callId", "")
                if cid:
                    cache.upsert_transcript(cid, t)
        except GongClientError:
            # Continue with remaining batches even if one fails
            pass

    return len(calls)


def _format_parties(parties: list[dict[str, Any]]) -> list[str]:
    """Extract readable names from parties list."""
    names = []
    for p in parties:
        name = p.get("name") or p.get("emailAddress") or p.get("speakerId", "Unknown")
        names.append(name)
    return names


def _format_duration(seconds: int | float) -> str:
    """Format seconds into human-readable duration."""
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    if minutes >= 60:
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def _format_transcript_text(transcript: dict[str, Any]) -> str:
    """Format a transcript dict into readable speaker-labeled text."""
    lines: list[str] = []
    for entry in transcript.get("transcript", []):
        speaker = entry.get("speakerName", entry.get("speakerId", "Unknown"))
        sentences = entry.get("sentences", [])
        text = " ".join(s.get("text", "") for s in sentences)
        if text.strip():
            lines.append(f"**{speaker}**: {text}")
    return "\n\n".join(lines)


# ── MCP Tools ───────────────────────────────────────────────────────


@mcp.tool()
async def list_calls(
    from_date: str = "",
    to_date: str = "",
    limit: int = 20,
) -> str:
    """List recent Gong calls with metadata.

    Args:
        from_date: Start date filter in ISO-8601 format (e.g. '2024-01-01'). Defaults to last 365 days.
        to_date: End date filter in ISO-8601 format (e.g. '2024-12-31'). Defaults to now.
        limit: Maximum number of calls to return (default 20, max 100).
    """
    cache = _get_cache()
    limit = min(limit, 100)

    # Normalize dates
    fd = from_date or None
    td = to_date or None
    if fd and "T" not in fd:
        fd = f"{fd}T00:00:00Z"
    if td and "T" not in td:
        td = f"{td}T23:59:59Z"

    # Try cache first
    cached = cache.list_calls(from_date=fd, to_date=td, limit=limit)
    if not cached:
        # Sync from API
        await _sync_recent_calls(from_date=fd, to_date=td)
        cached = cache.list_calls(from_date=fd, to_date=td, limit=limit)

    if not cached:
        return "No calls found for the specified date range."

    results: list[str] = [f"Found {len(cached)} calls:\n"]
    for call in cached:
        parties = _format_parties(call.get("parties", []))
        duration = _format_duration(call.get("duration", 0))
        results.append(
            f"- **{call['title'] or 'Untitled'}** (ID: `{call['call_id']}`)\n"
            f"  Date: {call['started'][:10] if call.get('started') else 'Unknown'} | "
            f"Duration: {duration} | "
            f"Participants: {', '.join(parties)}"
        )
    return "\n".join(results)


@mcp.tool()
async def get_call_details(call_id: str) -> str:
    """Get the full transcript and metadata for a specific Gong call.

    Args:
        call_id: The Gong call ID. Use list_calls to find call IDs.
    """
    client = _get_client()
    cache = _get_cache()

    # Get call metadata
    call = cache.get_call(call_id)
    if not call:
        try:
            api_call = await client.get_call(call_id)
            cache.upsert_call(api_call)
            call = cache.get_call(call_id)
        except GongClientError as e:
            return f"Error fetching call: {e}"

    if not call:
        return f"Call {call_id} not found."

    # Get transcript
    transcript = cache.get_transcript(call_id)
    if not transcript:
        try:
            api_transcript = await client.get_transcript(call_id)
            if api_transcript:
                cache.upsert_transcript(call_id, api_transcript)
                transcript = api_transcript
        except GongClientError as e:
            return f"Error fetching transcript: {e}"

    # Format output
    parties = _format_parties(call.get("parties", []))
    duration = _format_duration(call.get("duration", 0))
    content = call.get("content", {})

    output_parts = [
        f"# {call['title'] or 'Untitled Call'}",
        f"**Date:** {call['started'][:10] if call.get('started') else 'Unknown'}",
        f"**Duration:** {duration}",
        f"**Participants:** {', '.join(parties)}",
        f"**Call ID:** `{call_id}`",
    ]

    # Add topics if available
    topics = content.get("topics", [])
    if topics:
        topic_names = [t.get("name", "") for t in topics if t.get("name")]
        if topic_names:
            output_parts.append(f"**Topics:** {', '.join(topic_names)}")

    # Add trackers if available
    trackers = content.get("trackers", [])
    if trackers:
        tracker_names = [t.get("name", "") for t in trackers if t.get("name")]
        if tracker_names:
            output_parts.append(f"**Trackers:** {', '.join(tracker_names)}")

    # Add brief/summary if available
    brief = content.get("brief")
    if brief:
        output_parts.append(f"\n## Summary\n{brief}")

    # Add key points
    key_points = content.get("keyPoints", [])
    if key_points:
        output_parts.append("\n## Key Points")
        for kp in key_points:
            output_parts.append(f"- {kp}")

    # Add transcript
    if transcript:
        output_parts.append("\n## Transcript")
        output_parts.append(_format_transcript_text(transcript))
    else:
        output_parts.append("\n*Transcript not available.*")

    return "\n".join(output_parts)


@mcp.tool()
async def search_transcripts(
    query: str,
    speaker: str = "",
    from_date: str = "",
    to_date: str = "",
    limit: int = 20,
) -> str:
    """Search across all Gong call transcripts by keyword.

    Uses full-text search to find relevant transcript excerpts across calls.

    Args:
        query: Search keyword or phrase (e.g. 'pricing', 'onboarding', 'competitor').
        speaker: Optional speaker name to filter results.
        from_date: Optional start date filter (ISO-8601).
        to_date: Optional end date filter (ISO-8601).
        limit: Maximum results to return (default 20).
    """
    cache = _get_cache()

    # If cache is empty, sync recent calls first
    if not cache.has_any_transcripts():
        synced = await _sync_recent_calls(days=30)
        if synced == 0:
            return "No calls found to search. Try specifying a date range."

    results = cache.search_transcripts(query=query, limit=min(limit, 50))

    if not results:
        # Try syncing more data and searching again
        fd = from_date if from_date else None
        td = to_date if to_date else None
        if fd and "T" not in fd:
            fd = f"{fd}T00:00:00Z"
        if td and "T" not in td:
            td = f"{td}T23:59:59Z"
        await _sync_recent_calls(from_date=fd, to_date=td)
        results = cache.search_transcripts(query=query, limit=min(limit, 50))

    if not results:
        return f"No transcript matches found for '{query}'."

    # Filter by speaker if specified
    if speaker:
        speaker_lower = speaker.lower()
        results = [
            r for r in results
            if speaker_lower in r.get("snippet", "").lower()
            or any(speaker_lower in _format_parties([p])[0].lower() for p in r.get("parties", []))
        ]

    # Filter by date if specified
    if from_date:
        results = [r for r in results if r.get("started", "") >= from_date]
    if to_date:
        results = [r for r in results if r.get("started", "") <= to_date + "Z"]

    if not results:
        return f"No transcript matches found for '{query}' with the specified filters."

    output = [f"Found {len(results)} matches for '{query}':\n"]
    for r in results:
        parties = _format_parties(r.get("parties", []))
        output.append(
            f"### {r['title']} (ID: `{r['call_id']}`)\n"
            f"**Date:** {r['started'][:10] if r.get('started') else 'Unknown'} | "
            f"**Participants:** {', '.join(parties)}\n"
            f"**Excerpt:** ...{r['snippet']}...\n"
        )
    return "\n".join(output)


@mcp.tool()
async def get_call_analytics(call_id: str) -> str:
    """Get analytics and interaction data for a specific Gong call.

    Returns trackers, topics, talk ratios, key points, and other analytics.

    Args:
        call_id: The Gong call ID. Use list_calls to find call IDs.
    """
    client = _get_client()
    cache = _get_cache()

    # Check cache
    analytics = cache.get_analytics(call_id)
    if not analytics:
        try:
            analytics = await client.get_call_interaction_stats(call_id)
            cache.upsert_analytics(call_id, analytics)
        except GongClientError as e:
            return f"Error fetching analytics: {e}"

    meta = analytics.get("metaData", {})
    parties = analytics.get("parties", [])
    content = analytics.get("content", {})
    interaction = analytics.get("interaction", {})

    output_parts = [
        f"# Analytics: {meta.get('title', 'Untitled Call')}",
        f"**Date:** {meta.get('started', 'Unknown')[:10] if meta.get('started') else 'Unknown'}",
        f"**Duration:** {_format_duration(meta.get('duration', 0))}",
    ]

    # Participants with roles
    if parties:
        output_parts.append("\n## Participants")
        for p in parties:
            name = p.get("name") or p.get("emailAddress") or "Unknown"
            affiliation = p.get("affiliation", "")
            role_label = f" ({affiliation})" if affiliation else ""
            output_parts.append(f"- {name}{role_label}")

    # Topics
    topics = content.get("topics", [])
    if topics:
        output_parts.append("\n## Topics Discussed")
        for t in topics:
            name = t.get("name", "")
            duration = t.get("duration", 0)
            if name:
                output_parts.append(f"- {name} ({_format_duration(duration)})" if duration else f"- {name}")

    # Trackers
    trackers = content.get("trackers", [])
    if trackers:
        output_parts.append("\n## Trackers")
        for t in trackers:
            name = t.get("name", "")
            count = t.get("count", 0)
            phrases = t.get("phrases", [])
            if name:
                phrase_text = f' — phrases: {", ".join(p.get("text", "") for p in phrases[:3])}' if phrases else ""
                output_parts.append(f"- **{name}** (mentioned {count}x){phrase_text}")

    # Call outcome
    outcome = content.get("callOutcome")
    if outcome:
        output_parts.append(f"\n## Call Outcome\n{outcome}")

    # Key points
    key_points = content.get("keyPoints", [])
    if key_points:
        output_parts.append("\n## Key Points")
        for kp in key_points:
            output_parts.append(f"- {kp}")

    # Highlights
    highlights = content.get("highlights", [])
    if highlights:
        output_parts.append("\n## Highlights")
        for h in highlights:
            text = h.get("text", "")
            if text:
                output_parts.append(f"- {text}")

    # Interaction stats
    if interaction:
        output_parts.append("\n## Interaction Stats")
        for key, value in interaction.items():
            label = key.replace("_", " ").title()
            output_parts.append(f"- **{label}:** {value}")

    # Brief
    brief = content.get("brief")
    if brief:
        output_parts.append(f"\n## Summary\n{brief}")

    return "\n".join(output_parts)


def main() -> None:
    """Entry point for the MCP server."""
    import os
    import sys

    missing = []
    if not os.environ.get("GONG_API_KEY"):
        missing.append("GONG_API_KEY")
    if not os.environ.get("GONG_API_SECRET"):
        missing.append("GONG_API_SECRET")
    if missing:
        print(
            f"Error: Missing required environment variables: {', '.join(missing)}\n"
            f"Set them in your Claude MCP config's \"env\" block, or export them:\n"
            f"  export GONG_API_KEY=your-key\n"
            f"  export GONG_API_SECRET=your-secret",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp.run()
