"""Password-gated mobile viewer and local CLI job launcher for Warp runs."""

import os
import re
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import SESSION_LIFETIME, SessionStore, client_ip, verify_password
from app.config import validate_origin
from app.history import conversation_turns, turn_window
from app.rendering import render_markdown
from app import local_jobs
from app import models
from app import __version__
from app.warp_api import card, get_json


STATIC = Path( __file__ ).parent / "static";
COOKIE = "warpweb_session";
RUN_ID = re.compile( r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" );
ORIGINS = { origin.strip() for origin in os.getenv( "WARPWEB_ALLOWED_ORIGINS", "http://127.0.0.1:8797" ).split( "," ) };
app = FastAPI( version=__version__, docs_url=None, redoc_url=None, openapi_url=None );
app.mount( "/assets", StaticFiles( directory=STATIC ), name="assets" );
sessions = SessionStore();


class LoginRequest( BaseModel ):
    password: str = Field( min_length=1, max_length=1024 );

class TaskRequest( BaseModel ):
    prompt: str = Field( min_length=1, max_length=4096 );
    cwd: str | None = Field( default=None, max_length=1024 );
    model: str | None = Field( default=None, max_length=128 );

class RenderRequest( BaseModel ):
    text: str = Field( max_length=65536 );


@app.middleware( "http" )
async def prevent_caching( request: Request, call_next ):
    public_https = os.getenv( "WARPWEB_PUBLIC_HTTPS", "0" ) == "1";
    try:
        public_origin = validate_origin( os.getenv( "WARPWEB_PUBLIC_URL", "" ), https_only=True ) if public_https else None;
    except ValueError:
        response = JSONResponse( { "detail": "Invalid public HTTPS configuration" }, status_code=503 );
    else:
        if public_origin and request.url.scheme != "https":
            response = RedirectResponse( public_origin + request.url.path + ( "?" + request.url.query if request.url.query else "" ), status_code=307 );
        else:
            response = await call_next( request );
    if public_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000";
    response.headers["Cache-Control"] = "no-store";
    response.headers["X-Content-Type-Options"] = "nosniff";
    response.headers["Referrer-Policy"] = "no-referrer";
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'";
    return response;


def check_origin( request: Request ) -> None:
    if request.headers.get( "origin" ) not in ORIGINS:
        raise HTTPException( 403, "Invalid origin" );


def require_auth( request: Request ) -> None:
    if not os.environ.get( "WARP_API_HASH" ) or not sessions.valid( request.cookies.get( COOKIE ) ):
        raise HTTPException( 401, "Authentication required" );


@app.get( "/" )
async def index() -> FileResponse:
    return FileResponse( STATIC / "index.html" );


@app.get( "/api/session" )
async def session( request: Request ) -> dict[str, bool]:
    return { "authenticated": bool( os.environ.get( "WARP_API_HASH" ) ) and sessions.valid( request.cookies.get( COOKIE ) ) };


@app.post( "/api/login" )
async def login( data: LoginRequest, request: Request, response: Response ) -> dict[str, bool]:
    check_origin( request );
    address = client_ip( request.client.host if request.client else "unknown", request.headers.get( "x-real-ip" ) );
    if sessions.blocked( address ):
        raise HTTPException( 429, "Too many login attempts; try again later" );
    password_hash = os.environ.get( "WARP_API_HASH" );
    if not password_hash:
        raise HTTPException( 503, "Set WARP_API_HASH to enable login" );
    if not verify_password( data.password, password_hash ):
        sessions.failed( address );
        raise HTTPException( 401, "Incorrect password" );
    sessions.passed( address );
    response.set_cookie( COOKIE, sessions.create(), max_age=SESSION_LIFETIME, secure=os.getenv( "WARPWEB_COOKIE_SECURE", "1" ) != "0", httponly=True, samesite="strict", path="/" );
    return { "authenticated": True };


@app.post( "/api/logout", dependencies=[Depends( require_auth )] )
async def logout( request: Request, response: Response ) -> dict[str, bool]:
    check_origin( request );
    sessions.remove( request.cookies.get( COOKIE ) );
    response.delete_cookie( COOKIE, path="/" );
    return { "authenticated": False };


@app.get( "/api/runs", dependencies=[Depends( require_auth )] )
async def runs( scope: Literal["local", "cloud", "all"] = "local", cursor: str | None = Query( default=None, max_length=2048 ), limit: int = Query( default=30, ge=1, le=50 ) ) -> dict:
    params: dict = { "limit": limit, "sort_by": "updated_at", "sort_order": "desc" };
    if scope != "all":
        params["execution_location"] = "LOCAL" if scope == "local" else "REMOTE";
    if cursor:
        params["cursor"] = cursor;
    result = await get_json( "/runs", params );
    return { "runs": [ card( item ) for item in result.get( "runs", [] ) ], "next_cursor": ( result.get( "page_info" ) or {} ).get( "next_cursor" ) };


def local_capabilities( detail: dict ) -> dict:
    result = card( detail );
    local = detail.get( "source" ) == "CLI" and detail.get( "execution_location" ) == "LOCAL";
    job = local_jobs.for_run( result["id"], detail.get( "conversation_id" ) ) if local else None;
    result["local_job"] = job;
    if job:
        result["can_send"] = job["can_resume"] or result["can_send"] and job["state"] not in local_jobs.ACTIVE_STATES;
    return result;


@app.get( "/api/models", dependencies=[Depends( require_auth )] )
async def available_models() -> dict:
    try:
        return { "models": await models.catalog(), "available": True };
    except HTTPException:
        return { "models": [], "available": False };


@app.get( "/api/runs/{run_id}", dependencies=[Depends( require_auth )] )
async def run_preview( run_id: str ) -> dict:
    if not RUN_ID.fullmatch( run_id ):
        raise HTTPException( 400, "Invalid run ID" );
    detail = await get_json( f"/runs/{run_id}" );
    preview = local_capabilities( detail );
    preview["conversation_available"] = False;
    preview["root_steps"] = 0;
    if detail.get( "conversation_id" ):
        try:
            conversation = await get_json( f"/runs/{run_id}/conversation" );
            preview["conversation_available"] = True;
            preview["root_steps"] = len( conversation.get( "steps", [] ) );
        except HTTPException as error:
            if error.status_code != 404:
                raise;
    return preview;


@app.get( "/api/runs/{run_id}/metadata", dependencies=[Depends( require_auth )] )
async def run_metadata( run_id: str ) -> dict:
    if not RUN_ID.fullmatch( run_id ):
        raise HTTPException( 400, "Invalid run ID" );
    detail = await get_json( f"/runs/{run_id}" );
    result = local_capabilities( detail );
    return result;


@app.post( "/api/render", dependencies=[Depends( require_auth )] )
async def render_prose( data: RenderRequest, request: Request ) -> dict:
    check_origin( request );
    return { "html": render_markdown( data.text ) };


@app.get( "/api/runs/{run_id}/history", dependencies=[Depends( require_auth )] )
async def run_history( run_id: str, size: int = Query( default=8192, ge=4096, le=16384 ), before: int | None = Query( default=None, ge=0 ), after: int | None = Query( default=None, ge=0 ) ) -> dict:
    if not RUN_ID.fullmatch( run_id ):
        raise HTTPException( 400, "Invalid run ID" );
    if before is not None and after is not None:
        raise HTTPException( 400, "Choose either before or after" );
    conversation = await get_json( f"/runs/{run_id}/conversation" );
    try:
        return turn_window( conversation_turns( conversation ), size, before=before, after=after );
    except ValueError as error:
        raise HTTPException( 409, str( error ) ) from error;


@app.get( "/api/jobs", dependencies=[Depends( require_auth )] )
async def active_jobs() -> dict:
    return { "jobs": [ job.public() for job in local_jobs.jobs.values() if job.state in local_jobs.ACTIVE_STATES ] };


@app.post( "/api/jobs/{job_id}/cancel", status_code=202, dependencies=[Depends( require_auth )] )
async def cancel_job( job_id: str, request: Request ) -> dict:
    check_origin( request );
    return local_jobs.cancel( job_id ).public();


@app.post( "/api/jobs", dependencies=[Depends( require_auth )] )
async def create_job( request: Request, task: TaskRequest ) -> dict:
    check_origin( request );
    model = await models.validate( task.model );
    return ( local_jobs.start( task.prompt, task.cwd, model=model ) if model else local_jobs.start( task.prompt, task.cwd ) ).public();


@app.post( "/api/jobs/{job_id}/messages", dependencies=[Depends( require_auth )] )
async def continue_job( job_id: str, request: Request, task: TaskRequest ) -> dict:
    check_origin( request );
    job = local_jobs.jobs.get( job_id );
    if not job:
        raise HTTPException( 404, "Local job not found; reopen a verified run or start a new session" );
    if not local_jobs.resumable( job ):
        raise HTTPException( 409, "Wait for cleanup and verified conversation identity before continuing" );
    model = await models.validate( task.model );
    cwd = task.cwd or job.requested_cwd or job.cwd;
    return ( local_jobs.start( task.prompt, cwd, job.conversation_id, model=model ) if model else local_jobs.start( task.prompt, cwd, job.conversation_id ) ).public();


@app.post( "/api/runs/{run_id}/messages", dependencies=[Depends( require_auth )] )
async def continue_run( run_id: str, request: Request, task: TaskRequest ) -> dict:
    check_origin( request );
    if not RUN_ID.fullmatch( run_id ):
        raise HTTPException( 400, "Invalid run ID" );
    detail = await get_json( f"/runs/{run_id}" );
    if not local_capabilities( detail )["can_send"]:
        raise HTTPException( 409, "Only finished local CLI conversations can be continued; active or blocked runs must finish first" );
    model = await models.validate( task.model );
    return ( local_jobs.start( task.prompt, task.cwd, detail["conversation_id"], model=model ) if model else local_jobs.start( task.prompt, task.cwd, detail["conversation_id"] ) ).public();


@app.get( "/api/jobs/{job_id}", dependencies=[Depends( require_auth )] )
async def job_status( job_id: str ) -> dict:
    job = local_jobs.jobs.get( job_id );
    if not job:
        raise HTTPException( 404, "Local job not found; refresh the session list" );
    return job.public();
