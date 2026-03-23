"""Gong REST API client with authentication, pagination, and rate limiting."""

from __future__ import annotations

import asyncio
import base64
import os
from datetime import datetime, timezone
from typing import Any

import httpx

BASE_URL = "https://api.gong.io/v2"
PAGE_SIZE = 100
MAX_RETRIES = 3
RATE_LIMIT_DELAY = 0.35  # ~3 requests/sec


class GongClientError(Exception):
    """Raised when the Gong API returns an error."""


class GongClient:
    """Async client for the Gong v2 REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("GONG_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("GONG_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise GongClientError(
                "GONG_API_KEY and GONG_API_SECRET environment variables are required."
            )
        token = base64.b64encode(
            f"{self.api_key}:{self.api_secret}".encode()
        ).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers=self._headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        await self._rate_limit()
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.request(method, path, json=json)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "2"))
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if attempt < MAX_RETRIES - 1 and exc.response.status_code >= 500:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise GongClientError(
                    f"Gong API error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise GongClientError(f"Request failed: {exc}") from exc
        raise GongClientError("Max retries exceeded")

    # ── List calls ──────────────────────────────────────────────────

    async def list_calls(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List calls with optional date range filter.

        Dates should be ISO-8601 format (e.g. '2024-01-01T00:00:00Z').
        Returns dict with 'records' and optional 'cursor' for pagination.
        """
        body: dict[str, Any] = {}
        filter_obj: dict[str, Any] = {}
        if from_date:
            filter_obj["fromDateTime"] = from_date
        if to_date:
            filter_obj["toDateTime"] = to_date
        if filter_obj:
            body["filter"] = filter_obj
        if cursor:
            body["cursor"] = cursor
        body["contentSelector"] = {
            "exposedFields": {
                "content": {
                    "structure": True,
                    "topics": True,
                    "trackers": True,
                    "brief": True,
                    "highlights": True,
                    "callOutcome": True,
                    "keyPoints": True,
                },
                "collaboration": {
                    "publicComments": True,
                },
                "parties": True,
            }
        }
        return await self._request("POST", "/calls/extensive", json=body)

    async def list_all_calls(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        max_calls: int = 20000,
    ) -> list[dict[str, Any]]:
        """Fetch all calls (paginated) up to max_calls."""
        all_calls: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(all_calls) < max_calls:
            result = await self.list_calls(from_date, to_date, cursor)
            calls = result.get("calls", [])
            all_calls.extend(calls)
            records_meta = result.get("records", {})
            cursor = records_meta.get("cursor")
            if not cursor or not calls:
                break
        return all_calls[:max_calls]

    # ── Transcripts ─────────────────────────────────────────────────

    async def get_transcripts(
        self,
        call_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Get transcripts for one or more call IDs."""
        body = {"filter": {"callIds": call_ids}}
        result = await self._request("POST", "/calls/transcript", json=body)
        return result.get("callTranscripts", [])

    async def get_transcript(self, call_id: str) -> dict[str, Any] | None:
        """Get transcript for a single call."""
        transcripts = await self.get_transcripts([call_id])
        return transcripts[0] if transcripts else None

    # ── Call details ────────────────────────────────────────────────

    async def get_call(self, call_id: str) -> dict[str, Any]:
        """Get detailed metadata for a single call."""
        # Use the extensive endpoint with a single call filter
        body = {
            "filter": {"callIds": [call_id]},
            "contentSelector": {
                "exposedFields": {
                    "content": {
                        "structure": True,
                        "topics": True,
                        "trackers": True,
                        "brief": True,
                        "highlights": True,
                        "callOutcome": True,
                        "keyPoints": True,
                    },
                    "collaboration": {
                        "publicComments": True,
                    },
                    "parties": True,
                }
            },
        }
        result = await self._request("POST", "/calls/extensive", json=body)
        calls = result.get("calls", [])
        if not calls:
            raise GongClientError(f"Call {call_id} not found")
        return calls[0]

    # ── Answered scorecards / analytics ─────────────────────────────

    async def get_call_interaction_stats(
        self, call_id: str
    ) -> dict[str, Any]:
        """Get interaction stats (talk ratio, patience, etc.) for a call.

        Uses the extensive call data which includes interaction stats.
        """
        call = await self.get_call(call_id)
        return {
            "metaData": call.get("metaData", {}),
            "parties": call.get("parties", []),
            "content": call.get("content", {}),
            "interaction": call.get("interaction", {}),
        }
