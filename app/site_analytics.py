from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.analytics import BROWSER_PATTERN, GeoIPResolver, bot_reason_for_request, track_page_view
from app.settings import get_settings


class SitePageView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    occurred_at: AwareDatetime
    host: Literal["www.keepinglawsimple.org", "keepinglawsimple.org"]
    path: str = Field(min_length=1, max_length=1000, pattern=r"^/[^?#]*$")
    status_code: int = Field(ge=200, lt=300)
    user_agent: str = Field(default="", max_length=300)
    client_ip: str = Field(default="", max_length=64)
    referrer: str = Field(default="", max_length=300)
    language: str = Field(default="", max_length=200)
    fetch_mode: str = Field(default="", max_length=30)
    fetch_dest: str = Field(default="", max_length=30)


class SitePageViewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[SitePageView] = Field(min_length=1, max_length=16)


def site_analytics_router(resolver: GeoIPResolver) -> APIRouter:
    router = APIRouter()

    def record_events(batch: SitePageViewBatch) -> int:
        config = get_settings()
        inserted = 0
        for event in batch.events:
            if event.path.startswith(("//", "/api/", "/internal/", "/admin", "/beta", "/_")) or event.path == "/readyz":
                continue
            headers = {
                "host": event.host, "user-agent": event.user_agent,
                "accept-language": event.language, "sec-fetch-mode": event.fetch_mode,
                "sec-fetch-dest": event.fetch_dest, "referer": event.referrer,
                "x-real-ip": event.client_ip,
            }
            page_request = Request({
                "type": "http", "method": "GET", "path": event.path,
                "scheme": "https", "query_string": b"",
                "headers": [(k.encode(), v.encode("latin-1", errors="replace")) for k, v in headers.items()],
                "server": (event.host, 443), "client": (event.client_ip or "0.0.0.0", 0),
            })
            page_request.state.bot_reason = bot_reason_for_request(page_request)
            if not page_request.state.bot_reason and not BROWSER_PATTERN.search(event.user_agent):
                page_request.state.bot_reason = "unrecognized_user_agent"
            inserted += track_page_view(
                request=page_request, response=Response(status_code=event.status_code, media_type="text/html"),
                settings=config, resolver=resolver, occurred_at=event.occurred_at,
                tracking_source="site_server", event_id=str(event.event_id),
            )
        return inserted

    @router.post("/internal/site-page-views", include_in_schema=False)
    async def collect(request: Request) -> dict[str, int]:
        config = get_settings()
        token = request.headers.get("x-kls-analytics-token", "")
        if not config.site_analytics_token or not secrets.compare_digest(token.encode(), config.site_analytics_token.encode()):
            raise HTTPException(status_code=404)
        if not config.analytics_enabled:
            raise HTTPException(status_code=503, detail="Page logging is disabled")
        data = bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data) > 64 * 1024:
                raise HTTPException(status_code=413)
        try:
            batch = SitePageViewBatch.model_validate(json.loads(data))
        except (ValueError, ValidationError):
            raise HTTPException(status_code=422, detail="Invalid page-view batch") from None
        now = datetime.now(timezone.utc)
        if any(abs((now - event.occurred_at).total_seconds()) > 600 for event in batch.events):
            raise HTTPException(status_code=422, detail="Page-view timestamp is outside the collection window")
        return {"accepted": await run_in_threadpool(record_events, batch)}

    return router
