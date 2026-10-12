"""Narrow, read-only REST access to Warp agent runs."""

import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException


BASE_URL = "https://app.warp.dev/api/v1/agent"
TERMINAL_STATES = { "SUCCEEDED", "FAILED", "ERROR", "CANCELLED" };


def capabilities( run: dict[str, Any] ) -> dict[str, Any]:
    local_cli = run.get( "source" ) == "CLI" and run.get( "execution_location" ) == "LOCAL";
    conversation = run.get( "conversation_id" );
    can_send = local_cli and isinstance( conversation, str ) and bool( re.fullmatch( r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", conversation ) ) and run.get( "state" ) in TERMINAL_STATES;
    link = run.get( "session_link" );
    safe_link = None;
    if isinstance( link, str ):
        try:
            parsed = urlsplit( link );
            if parsed.scheme == "https" and parsed.hostname in { "app.warp.dev", "oz.warp.dev" } and not parsed.username and not parsed.password and parsed.port in ( None, 443 ) and not any( char.isspace() for char in link ):
                safe_link = link;
        except ValueError:
            pass;
    return { "can_send": bool( can_send ), "session_link": safe_link, "conversation_id": conversation if local_cli else None };


def costs( run: dict[str, Any] ) -> dict[str, float | None]:
    usage = run.get( "request_usage" ) or {};
    if not isinstance( usage, dict ):
        usage = {};

    def summed( fields: tuple[str, ...] ) -> float | None:
        amounts = [ usage[key] for key in fields if isinstance( usage.get( key ), ( int, float ) ) ];
        return round( sum( amounts ), 4 ) if amounts else None;

    return {
        "credits": summed( ( "inference_cost", "compute_cost", "platform_cost" ) ),
        "usd_approx": summed( ( "inference_cost_usd", "compute_cost_usd", "platform_cost_usd" ) ),
    };


def card( run: dict[str, Any] ) -> dict[str, Any]:
    prompt = run.get( "prompt" );
    summary = " ".join( prompt.split() )[:160] if isinstance( prompt, str ) else "";
    return {
        "id": run.get( "run_id" ),
        "title": run.get( "title" ) or "Untitled run",
        "summary": summary,
        "source": run.get( "source" ),
        "location": run.get( "execution_location" ),
        "state": run.get( "state" ),
        "created_at": run.get( "created_at" ),
        "updated_at": run.get( "updated_at" ),
        "cost": costs( run ),
        **capabilities( run ),
    };


async def get_json( path: str, params: dict[str, Any] | None = None ) -> dict[str, Any]:
    key = os.environ.get( "WARP_API_KEY" );
    if not key:
        raise HTTPException( 503, "Warp API key is not configured" );
    try:
        async with httpx.AsyncClient( base_url=BASE_URL, timeout=15.0, follow_redirects=False ) as client:
            response = await client.get( path, params=params, headers={ "Authorization": f"Bearer {key}" } );
            response.raise_for_status();
            result = response.json();
            if not isinstance( result, dict ):
                raise ValueError( "Expected an object from Warp" );
            return result;
    except httpx.HTTPStatusError as error:
        if error.response.status_code in ( 401, 403 ):
            raise HTTPException( 502, "Warp API credentials lack access" ) from error;
        if error.response.status_code == 404:
            raise HTTPException( 404, "Run or conversation not found" ) from error;
        raise HTTPException( 502, "Warp API request failed" ) from error;
    except ( httpx.HTTPError, ValueError ) as error:
        raise HTTPException( 502, "Warp API is unavailable" ) from error;
