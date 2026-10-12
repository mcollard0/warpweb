"""API contract tests without network calls or a real account password."""

import os
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app import main
from app import local_jobs
from app import __version__
from app.auth import SessionStore, make_password_hash, verify_password
from app.history import conversation_turns, pieces, turn_window
from app.warp_api import card
from app.warp_api import capabilities
from app.rendering import render_markdown


RUN_ID = "01a12746-f847-7364-9f4a-6ec4aaa6b3b4";
ORIGIN = { "Origin": "https://agents.example.com" };


@pytest.fixture
def client( monkeypatch ):
    monkeypatch.setenv( "WARP_API_HASH", make_password_hash( "example-only-password" ) );
    monkeypatch.setenv( "WARP_API_KEY", "test-key" );
    monkeypatch.setenv( "WARPWEB_PUBLIC_URL", "https://agents.example.com" );
    monkeypatch.setattr( main, "ORIGINS", { ORIGIN["Origin"] } );
    main.sessions = SessionStore();
    with TestClient( main.app, base_url="https://agents.example.com" ) as browser:
        yield browser;


def login( browser ):
    result = browser.post( "/api/login", json={ "password": "example-only-password" }, headers=ORIGIN );
    assert result.status_code == 200;
    return result;


def test_application_version():
    assert __version__ == "0.0.1" and main.app.version == __version__;


def test_public_favicon_is_linked_and_served( client ):
    page = client.get( "/" );
    assert page.status_code == 200;
    assert '<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg" sizes="any">' in page.text;
    icon = client.get( "/assets/favicon.svg" );
    assert icon.status_code == 200;
    assert icon.headers["content-type"].split( ";", 1 )[0] == "image/svg+xml";
    assert icon.headers["cache-control"] == "no-store";
    svg = ElementTree.fromstring( icon.content );
    assert svg.tag == "{http://www.w3.org/2000/svg}svg" and svg.attrib["viewBox"] == "0 0 32 32";
    assert client.get( "/api/session" ).json() == { "authenticated": False };


def test_password_hash_and_invalid_format( monkeypatch ):
    monkeypatch.setattr( "app.auth.HASH_ITERATIONS", 100_000 );
    stored = make_password_hash( "passphrase" );
    assert stored.startswith( "pbkdf2_sha3_256:" );
    assert verify_password( "passphrase", stored );
    assert not verify_password( "wrong", stored );
    assert not verify_password( "passphrase", "sha3_256:100000:invalid:hash" );


def test_login_fails_closed_without_password_hash( client, monkeypatch ):
    monkeypatch.delenv( "WARP_API_HASH" );
    assert client.post( "/api/login", json={ "password": "anything" }, headers=ORIGIN ).status_code == 503;
    assert client.get( "/api/runs" ).status_code == 401;

def test_public_https_redirects_and_security_headers( client, monkeypatch ):
    monkeypatch.setenv( "WARPWEB_PUBLIC_HTTPS", "1" );
    response = client.get( "http://untrusted.example/api/session?test=1", follow_redirects=False );
    assert response.status_code == 307;
    assert response.headers["location"] == "https://agents.example.com/api/session?test=1";
    assert response.headers["strict-transport-security"] == "max-age=31536000";
    assert client.get( "/api/session" ).status_code == 200;
    result = login( client );
    assert "secure" in result.headers["set-cookie"].lower();


def test_login_origin_cookie_logout_and_read_protection( client ):
    assert client.get( "/api/runs" ).status_code == 401;
    assert client.get( f"/api/runs/{RUN_ID}" ).status_code == 401;
    assert client.post( "/api/login", json={ "password": "example-only-password" } ).status_code == 403;
    assert client.post( "/api/login", json={ "password": "wrong" }, headers=ORIGIN ).status_code == 401;
    result = login( client );
    cookie = result.headers["set-cookie"].lower();
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie;
    assert client.get( "/api/session" ).json() == { "authenticated": True };
    assert client.post( "/api/logout" ).status_code == 403;
    assert client.post( "/api/logout", headers=ORIGIN ).status_code == 200;
    assert client.get( "/api/runs" ).status_code == 401;


def test_brute_force_throttle( client ):
    for _ in range( 5 ):
        assert client.post( "/api/login", json={ "password": "wrong" }, headers=ORIGIN ).status_code == 401;
    assert client.post( "/api/login", json={ "password": "example-only-password" }, headers=ORIGIN ).status_code == 429;


def test_local_list_pagination_and_cost( client, monkeypatch ):
    observed = [];

    async def fake_warp( path, params=None ):
        observed.append( ( path, params ) );
        return { "runs": [ { "run_id": RUN_ID, "title": "Local task", "prompt": "First line\nprivate details", "source": "LOCAL", "execution_location": "LOCAL", "request_usage": { "inference_cost": 5.0, "compute_cost": 1.0, "inference_cost_usd": 0.12 }, "other_private_metadata": "not returned" } ], "page_info": { "next_cursor": "next-page" } };

    monkeypatch.setattr( main, "get_json", fake_warp );
    login( client );
    result = client.get( "/api/runs?limit=3&cursor=abc" );
    assert result.status_code == 200;
    assert observed == [ ( "/runs", { "limit": 3, "sort_by": "updated_at", "sort_order": "desc", "execution_location": "LOCAL", "cursor": "abc" } ) ];
    assert result.json()["next_cursor"] == "next-page";
    run = result.json()["runs"][0];
    assert run["summary"] == "First line private details";
    assert run["cost"] == { "credits": 6.0, "usd_approx": 0.12 };
    assert "other_private_metadata" not in run;
    assert client.get( "/api/runs?scope=cloud" ).status_code == 200;
    assert observed[-1][1]["execution_location"] == "REMOTE";
    assert client.get( "/api/runs?limit=501" ).status_code == 422;


def test_preview_fetches_local_conversation_without_exposing_messages( client, monkeypatch ):
    observed = [];

    async def fake_warp( path, params=None ):
        observed.append( path );
        if path.endswith( "/conversation" ):
            return { "conversation_id": "conversation", "steps": [ { "messages": [ { "content": "private transcript" } ] } ] };
        return { "run_id": RUN_ID, "title": "Local task", "prompt": "Test", "source": "LOCAL", "execution_location": "LOCAL", "conversation_id": "conversation" };

    monkeypatch.setattr( main, "get_json", fake_warp );
    login( client );
    result = client.get( f"/api/runs/{RUN_ID}" );
    assert result.status_code == 200;
    assert result.json()["conversation_available"] is True;
    assert result.json()["root_steps"] == 1;
    assert "private transcript" not in result.text;
    assert observed == [ f"/runs/{RUN_ID}", f"/runs/{RUN_ID}/conversation" ];
    assert client.get( "/api/runs/not-a-uuid" ).status_code == 400;
    assert client.post( f"/api/runs/{RUN_ID}" ).status_code == 405;


def test_missing_cost_is_not_zero():
    run = card( { "run_id": RUN_ID, "request_usage": None } );
    assert run["cost"] == { "credits": None, "usd_approx": None };


def test_nested_actor_turns_and_lossless_utf8_windows():
    conversation = { "steps": [ { "messages": [
        { "role": "user", "timestamp": "2026-01-01T00:00:00Z", "content": [ { "type": "text", "text": "Hello 😃" } ] },
        { "role": "assistant", "timestamp": "2026-01-01T00:00:02Z", "content": [ { "type": "action_result", "name": "read_files", "output": { "message": "result" } } ] },
    ], "steps": [ { "messages": [ { "role": "assistant", "timestamp": "2026-01-01T00:00:01Z", "content": [ { "type": "action", "name": "read_files", "input": { "path": "/tmp" } } ] } ] } ] } ] };
    turns = conversation_turns( conversation );
    assert [ part["actor"] for part in turns ] == [ "User", "Tool", "Tool" ];
    assert turns[1]["title"] == "read_files · call";
    assert "path: /tmp" in turns[1]["body"] and '{"path":' not in turns[1]["body"];
    assert "message: result" in turns[2]["body"];
    chunks = [];
    before = None;
    while True:
        part = turn_window( turns, 256, before=before );
        chunks[0:0] = part["turns"];
        if not part["older"]:
            break;
        before = part["start"];
    assert chunks == turns;
    assert turn_window( turns, 256, after=0 )["turns"] == turns[:turn_window( turns, 256, after=0 )["end"]];
    assert "".join( pieces( "😃" * 2000 ) ) == "😃" * 2000;
    with pytest.raises( ValueError ):
        turn_window( turns, 256, before=len( turns ) + 1 );

def test_long_actor_history_prepends_older_and_polls_newer():
    turns = [ { "actor": "Agent", "body": f"Message {index}: " + "x" * 1000 } for index in range( 35 ) ];
    latest = turn_window( turns, 8192 );
    assert latest["older"] and latest["end"] == len( turns );
    assert 0 < len( latest["turns"] ) < len( turns );
    previous = turn_window( turns, 8192, before=latest["start"] );
    assert previous["end"] == latest["start"];
    assert previous["turns"][-1] == turns[latest["start"] - 1];
    appended = turn_window( turns + [ { "actor": "User", "body": "new" } ], 8192, after=latest["end"] );
    assert appended["turns"] == [ { "actor": "User", "body": "new" } ];


def test_history_requires_auth_and_windows_payload( client, monkeypatch ):
    async def fake_warp( path, params=None ):
        assert path == f"/runs/{RUN_ID}/conversation";
        return { "steps": [ { "messages": [ { "role": "user", "content": [ { "type": "text", "text": "private response" } ] } ] } ] };
    monkeypatch.setattr( main, "get_json", fake_warp );
    assert client.get( f"/api/runs/{RUN_ID}/history" ).status_code == 401;
    login( client );
    result = client.get( f"/api/runs/{RUN_ID}/history" );
    assert result.status_code == 200;
    assert result.json()["turns"][0]["actor"] == "User";
    assert "private response" in result.json()["turns"][0]["body"];
    assert result.headers["cache-control"] == "no-store";
    assert client.get( f"/api/runs/{RUN_ID}/history?size=3000" ).status_code == 422;
    assert client.get( f"/api/runs/{RUN_ID}/history?before=0&after=0" ).status_code == 400;


def test_creation_is_origin_protected_and_never_falls_back_to_cloud( client, monkeypatch ):
    called = [];
    async def fake_warp( path, params=None ):
        if path.endswith( "/conversation" ):
            return { "steps": [] };
        return { "run_id": RUN_ID, "conversation_id": RUN_ID, "source": "LOCAL", "execution_location": "LOCAL", "state": "SUCCEEDED" };
    def fake_start( prompt, cwd=None, conversation_id=None ):
        called.append( ( prompt, cwd, conversation_id ) );
        return local_jobs.Job( id="test-job" );
    monkeypatch.setattr( main, "get_json", fake_warp );
    monkeypatch.setattr( local_jobs, "start", fake_start );
    assert client.post( "/api/jobs", json={ "prompt": "Test" }, headers=ORIGIN ).status_code == 401;
    login( client );
    assert client.post( "/api/jobs", json={ "prompt": "Test" } ).status_code == 403;
    assert client.post( "/api/jobs", json={ "prompt": "Test" }, headers=ORIGIN ).status_code == 200;
    assert client.post( f"/api/runs/{RUN_ID}/messages", json={ "prompt": "Test" }, headers=ORIGIN ).status_code == 409;
    assert len( called ) == 1;
    async def fake_cli( path, params=None ):
        return { "run_id": RUN_ID, "conversation_id": RUN_ID, "source": "CLI", "execution_location": "LOCAL", "state": "SUCCEEDED" };
    monkeypatch.setattr( main, "get_json", fake_cli );
    assert client.post( f"/api/runs/{RUN_ID}/messages", json={ "prompt": "Continue" }, headers=ORIGIN ).status_code == 200;
    assert called[-1] == ( "Continue", None, RUN_ID );


def test_local_job_uses_private_task_file_not_prompt_argv( tmp_path, monkeypatch ):
    monkeypatch.setenv( "WARPWEB_WORKDIR_ROOTS", str( tmp_path ) );
    monkeypatch.setattr( local_jobs, "JOB_ROOT", tmp_path / "jobs" );
    local_jobs.jobs.clear();
    captured = {};
    class FakeProcess:
        stdout = None
        def __init__( self, argv, **options ):
            captured["argv"] = argv;
            captured["options"] = options;
    async def fake_monitor( process, job, task_file ):
        captured["task_file"] = task_file;
    monkeypatch.setattr( local_jobs.subprocess, "Popen", FakeProcess );
    monkeypatch.setattr( local_jobs, "monitor", fake_monitor );

    async def launch():
        result = local_jobs.start( "sensitive sample task", str( tmp_path ), RUN_ID );
        await __import__( "asyncio" ).sleep( 0 );
        return result;

    job = __import__( "asyncio" ).run( launch() );
    argv = " ".join( captured["argv"] );
    assert "sensitive sample task" not in argv;
    assert "--conversation" in captured["argv"] and RUN_ID in captured["argv"];
    assert "--no-snapshot" in captured["argv"];
    assert captured["options"]["start_new_session"] is True;
    task_file = captured["task_file"];
    assert task_file.read_text() == "sensitive sample task";
    assert task_file.stat().st_mode & 0o777 == 0o600;
    assert job.public()["run_id"] is None;
    task_file.unlink();
    task_file.parent.rmdir();
    local_jobs.jobs.clear();
    with pytest.raises( __import__( "fastapi" ).HTTPException ):
        local_jobs.allowed_workdir( "/" );


@pytest.mark.parametrize( "state,allowed", [ ( "SUCCEEDED", True ), ( "FAILED", True ), ( "ERROR", True ), ( "CANCELLED", True ), ( "INPROGRESS", False ), ( "RUNNING", False ), ( "PENDING", False ), ( "QUEUED", False ), ( "CLAIMED", False ), ( "BLOCKED", False ), ( "UNKNOWN", False ) ] )
def test_continuation_capabilities_and_submission_agree( client, monkeypatch, state, allowed ):
    async def fake_warp( path, params=None ):
        return { "run_id": RUN_ID, "conversation_id": RUN_ID, "source": "CLI", "execution_location": "LOCAL", "state": state };
    monkeypatch.setattr( main, "get_json", fake_warp );
    monkeypatch.setattr( local_jobs, "start", lambda *args: local_jobs.Job( id="external-cli" ) );
    login( client );
    assert client.get( f"/api/runs/{RUN_ID}/metadata" ).json()["can_send"] is allowed;
    assert client.post( f"/api/runs/{RUN_ID}/messages", json={ "prompt": "Follow up" }, headers=ORIGIN ).status_code == ( 200 if allowed else 409 );


@pytest.mark.parametrize( "link,allowed", [ ( "https://app.warp.dev/s/123", True ), ( "https://oz.warp.dev/runs/123", True ), ( "https://app.warp.dev.evil.test/s", False ), ( "javascript:alert(1)", False ), ( "http://app.warp.dev/s", False ), ( "https://user:pass@app.warp.dev/s", False ), ( "https://app.warp.dev:bad/s", False ), ( "https://app.warp.dev:8443/s", False ), ( "https://app.warp.dev/\nfoo", False ) ] )
def test_shared_session_links_are_allowlisted( link, allowed ):
    result = capabilities( { "session_link": link } );
    assert result["session_link"] == ( link if allowed else None );
    assert result["can_send"] is False;


def test_metadata_is_authenticated_and_does_not_fetch_history( client, monkeypatch ):
    observed = [];
    async def fake_warp( path, params=None ):
        observed.append( path );
        return { "run_id": RUN_ID, "source": "LOCAL", "execution_location": "LOCAL", "state": "SUCCEEDED", "session_link": "https://app.warp.dev/s/test" };
    monkeypatch.setattr( main, "get_json", fake_warp );
    assert client.get( f"/api/runs/{RUN_ID}/metadata" ).status_code == 401;
    login( client );
    result = client.get( f"/api/runs/{RUN_ID}/metadata" );
    assert result.status_code == 200 and result.json()["can_send"] is False;
    assert observed == [ f"/runs/{RUN_ID}" ];
    assert client.get( "/api/runs/invalid/metadata" ).status_code == 400;


def test_markdown_rendering_is_sanitized_and_bounded( client ):
    text = '# Heading\n\n**bold** and [safe](https://example.com)\n\n```python\nprint( "<script>" );\n```\n\n<script>alert(1)</script>\n\n![image](https://evil.test/x)\n\n[bad](javascript:alert(1))';
    assert client.post( "/api/render", json={ "text": text }, headers=ORIGIN ).status_code == 401;
    login( client );
    assert client.post( "/api/render", json={ "text": text } ).status_code == 403;
    result = client.post( "/api/render", json={ "text": text }, headers=ORIGIN );
    html = result.json()["html"];
    assert "<h1>Heading</h1>" in html and "<strong>bold</strong>" in html;
    assert "<pre><code>" in html and 'rel="noopener noreferrer"' in html;
    assert "<script" not in html and "<img" not in html and 'href="javascript:' not in html;
    assert render_markdown( "😃" * 20000 ) is None;
    assert client.post( "/api/render", json={ "text": "x" * 65537 }, headers=ORIGIN ).status_code == 422;
    assert result.headers["cache-control"] == "no-store";


def test_fragment_identity_and_empty_tool_visibility():
    text = "```python\n" + 'print( "😃" );\n' * 700 + "```";
    turns = conversation_turns( { "steps": [ { "messages": [ { "id": "message-a", "role": "assistant", "content": [ { "type": "text", "text": text }, { "type": "action", "name": "shell", "id": "call-a", "input": { "command": "pwd" } }, { "type": "action_result", "name": "shell", "action_id": "call-a", "output": None } ] } ] } ] } );
    fragments = [ turn for turn in turns if turn["kind"] == "text" ];
    assert "".join( turn["body"] for turn in fragments ) == text;
    assert len( { turn["block_id"] for turn in fragments } ) == 1;
    assert [ turn["fragment"] for turn in fragments ] == list( range( len( fragments ) ) );
    assert all( turn["fragments"] == len( fragments ) for turn in fragments );
    assert turns[-2]["action_id"] == turns[-1]["action_id"] == "call-a";
    assert turns[-1]["body"] == "Output unavailable.";


def test_job_id_is_not_exposed_until_verified_and_duplicate_conversation_is_blocked( tmp_path, monkeypatch ):
    job = local_jobs.Job( id="private-job", run_id=RUN_ID, conversation_id=RUN_ID );
    assert job.public()["run_id"] is None;
    job.verified = True;
    assert job.public()["run_id"] == RUN_ID;
    monkeypatch.setenv( "WARPWEB_WORKDIR_ROOTS", str( tmp_path ) );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
    with pytest.raises( __import__( "fastapi" ).HTTPException ) as error:
        local_jobs.start( "Duplicate", str( tmp_path ), RUN_ID );
    assert error.value.status_code == 409;


def test_monitor_verifies_a_live_run_before_completion( tmp_path, monkeypatch ):
    import asyncio
    import threading
    release = threading.Event();
    job = local_jobs.Job( id="live-job", run_id=RUN_ID );
    folder = tmp_path / "job"; folder.mkdir();
    task_file = folder / "task.txt"; task_file.write_text( "sample" );
    def fake_consume( process, current ):
        release.wait( 5 );
        return 0;
    async def fake_warp( path, params=None ):
        assert path == f"/runs/{RUN_ID}";
        return { "source": "CLI", "execution_location": "LOCAL" };
    monkeypatch.setattr( local_jobs, "consume", fake_consume );
    monkeypatch.setattr( local_jobs, "get_json", fake_warp );
    async def check():
        task = asyncio.create_task( local_jobs.monitor( object(), job, task_file ) );
        for _ in range( 50 ):
            if job.verified: break;
            await asyncio.sleep( .01 );
        try:
            assert job.state == "running" and job.public()["run_id"] == RUN_ID;
        finally:
            release.set();
            await task;
        assert job.state == "completed" and not task_file.exists();
    asyncio.run( check() );


def test_configurable_public_redirect_and_fail_closed( client, monkeypatch ):
    monkeypatch.setenv( "WARPWEB_PUBLIC_HTTPS", "1" );
    monkeypatch.setenv( "WARPWEB_PUBLIC_URL", "https://agents.example.com/" );
    result = client.get( "http://untrusted.example/api/session?test=1", follow_redirects=False );
    assert result.status_code == 307 and result.headers["location"] == "https://agents.example.com/api/session?test=1";
    monkeypatch.setenv( "WARPWEB_PUBLIC_URL", "https://user:pass@evil.test" );
    result = client.get( "/api/session" );
    assert result.status_code == 503 and "user:pass" not in result.text;
    monkeypatch.delenv( "WARPWEB_PUBLIC_URL", raising=False );
    assert client.get( "/api/session" ).status_code == 503;


def test_cancellation_is_authenticated_origin_protected_and_scoped( client, monkeypatch ):
    observed = [];
    job = local_jobs.Job( id="owned-job", state="stopping" );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
    def fake_cancel( job_id ):
        observed.append( job_id );
        return job;
    monkeypatch.setattr( local_jobs, "cancel", fake_cancel );
    assert client.get( "/api/jobs" ).status_code == 401;
    assert client.post( "/api/jobs/owned-job/cancel", headers=ORIGIN ).status_code == 401;
    login( client );
    assert client.post( "/api/jobs/owned-job/cancel" ).status_code == 403;
    assert client.post( "/api/jobs/owned-job/cancel", headers={ "Origin": "https://evil.test" } ).status_code == 403;
    assert not observed;
    response = client.post( "/api/jobs/owned-job/cancel", headers=ORIGIN );
    assert response.status_code == 202 and response.json()["state"] == "stopping";
    assert observed == [ "owned-job" ];
    payload = client.get( "/api/jobs" ).json()["jobs"][0];
    assert payload["can_cancel"] is False;
    assert not { "process", "pid", "stop_task", "cancel_event" }.intersection( payload );


def test_active_job_discovery_and_metadata_restore_only_owned_local_jobs( client, monkeypatch ):
    job = local_jobs.Job( id="owned", run_id=RUN_ID, conversation_id=RUN_ID, verified=True, process=object() );
    completed = local_jobs.Job( id="finished", state="completed" );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job, completed.id: completed } );
    detail = { "run_id": RUN_ID, "conversation_id": RUN_ID, "source": "CLI", "execution_location": "LOCAL", "state": "INPROGRESS" };
    async def fake_warp( path, params=None ): return detail if not path.endswith( "/conversation" ) else { "steps": [] };
    monkeypatch.setattr( main, "get_json", fake_warp );
    login( client );
    assert [ item["id"] for item in client.get( "/api/jobs" ).json()["jobs"] ] == [ "owned" ];
    for suffix in ( "", "/metadata" ):
        payload = client.get( f"/api/runs/{RUN_ID}{suffix}" ).json();
        assert payload["local_job"]["id"] == "owned" and payload["local_job"]["can_cancel"];
    detail["source"] = "LOCAL";
    assert client.get( f"/api/runs/{RUN_ID}/metadata" ).json()["local_job"] is None;
    detail.update( { "source": "CLI", "execution_location": "REMOTE" } );
    assert client.get( f"/api/runs/{RUN_ID}/metadata" ).json()["local_job"] is None;
    monkeypatch.setattr( local_jobs, "jobs", {} );
    assert client.post( "/api/jobs/unknown/cancel", headers=ORIGIN ).status_code == 404;


def test_stopped_owned_job_can_continue_despite_stale_upstream_status( client, monkeypatch ):
    job = local_jobs.Job( id="stopped", run_id=RUN_ID, conversation_id=RUN_ID, verified=True, state="cancelled", cwd="/workspace" );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
    async def fake_warp( *args ): return { "run_id": RUN_ID, "conversation_id": RUN_ID, "source": "CLI", "execution_location": "LOCAL", "state": "INPROGRESS" };
    monkeypatch.setattr( main, "get_json", fake_warp );
    observed = [];
    def start( *args, **kwargs ):
        observed.append( ( args, kwargs ) );
        return local_jobs.Job( id="next" );
    monkeypatch.setattr( local_jobs, "start", start );
    assert client.post( "/api/jobs/stopped/messages", json={ "prompt": "Next" }, headers=ORIGIN ).status_code == 401;
    login( client );
    assert client.post( "/api/jobs/stopped/messages", json={ "prompt": "Next" } ).status_code == 403;
    assert client.get( f"/api/runs/{RUN_ID}/metadata" ).json()["can_send"];
    assert client.post( "/api/jobs/stopped/messages", json={ "prompt": "Next" }, headers=ORIGIN ).status_code == 200;
    assert observed[-1][0] == ( "Next", "/workspace", RUN_ID );
    assert client.post( f"/api/runs/{RUN_ID}/messages", json={ "prompt": "Next" }, headers=ORIGIN ).status_code == 200;
    job.process = object();
    assert not job.public()["can_resume"];
    assert client.post( "/api/jobs/stopped/messages", json={ "prompt": "Next" }, headers=ORIGIN ).status_code == 409;
    assert not client.get( f"/api/runs/{RUN_ID}/metadata" ).json()["can_send"];
    job.process = None; job.verified = False;
    assert client.post( "/api/jobs/stopped/messages", json={ "prompt": "Next" }, headers=ORIGIN ).status_code == 409;
    assert client.post( "/api/jobs/unknown/messages", json={ "prompt": "Next" }, headers=ORIGIN ).status_code == 404;


def test_owned_conversation_lock_applies_to_older_runs( client, monkeypatch ):
    next_id = "11111111-1111-4111-8111-111111111111";
    job = local_jobs.Job( id="next-turn", run_id=next_id, conversation_id=RUN_ID, verified=True, process=object() );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
    async def fake_warp( *args ): return { "run_id": RUN_ID, "conversation_id": RUN_ID, "source": "CLI", "execution_location": "LOCAL", "state": "SUCCEEDED" };
    monkeypatch.setattr( main, "get_json", fake_warp );
    login( client );
    data = client.get( f"/api/runs/{RUN_ID}/metadata" ).json();
    assert not data["can_send"] and data["local_job"]["id"] == job.id;
    assert client.post( f"/api/runs/{RUN_ID}/messages", json={ "prompt": "Duplicate" }, headers=ORIGIN ).status_code == 409;
    job.state = "stopping";
    assert not job.public()["can_resume"];


def test_model_catalog_and_per_turn_validation( client, monkeypatch ):
    async def catalog(): return [ "auto", "fixture-model" ];
    monkeypatch.setattr( main.models, "catalog", catalog );
    called = [];
    def start( *args, **kwargs ):
        called.append( kwargs );
        return local_jobs.Job( id="selected", model=kwargs.get( "model" ) );
    monkeypatch.setattr( local_jobs, "start", start );
    assert client.get( "/api/models" ).status_code == 401;
    login( client );
    assert client.get( "/api/models" ).json() == { "models": [ "auto", "fixture-model" ], "available": True };
    assert client.post( "/api/jobs", json={ "prompt": "Test", "model": "fixture-model" }, headers=ORIGIN ).json()["model"] == "fixture-model";
    assert called[-1] == { "model": "fixture-model" };
    for value in [ "--bad-flag", "unknown", "a\nb" ]:
        assert client.post( "/api/jobs", json={ "prompt": "Test", "model": value }, headers=ORIGIN ).status_code == 400;
    async def unavailable(): raise __import__( "fastapi" ).HTTPException( 503, "unavailable" );
    monkeypatch.setattr( main.models, "catalog", unavailable );
    assert client.get( "/api/models" ).json() == { "models": [], "available": False };
    assert client.post( "/api/jobs", json={ "prompt": "Default", "model": None }, headers=ORIGIN ).status_code == 200;
    assert client.post( "/api/jobs", json={ "prompt": "Test", "model": "auto" }, headers=ORIGIN ).status_code == 503;
