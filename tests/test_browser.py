"""Headless UI regressions with mocked APIs; no live login or agent execution."""

import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest

from app.rendering import render_markdown

playwright = pytest.importorskip( "playwright.sync_api" );
STATIC = Path( __file__ ).resolve().parents[1] / "app" / "static";
RUN = "01a12746-f847-7364-9f4a-6ec4aaa6b3b4";
NEXT = "01a12746-f847-7364-9f4a-6ec4aaa6b3b5";
CONVERSATION = "01a12746-f847-7364-9f4a-6ec4aaa6b3b6";


@pytest.fixture( scope="module" )
def browser():
    executable = shutil.which( "chromium" );
    if not executable: pytest.skip( "Install Chromium for browser regressions" );
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch( executable_path=executable, headless=True );
        yield instance;
        instance.close();


@pytest.fixture
def ui( browser ):
    context = browser.new_context( viewport={ "width": 375, "height": 812 } );
    page = context.new_page();
    errors = [];
    page.on( "pageerror", lambda error: errors.append( str( error ) ) );
    control = { "posts": [], "held": [], "hold": False, "reject": False, "queries": [], "turns": [], "owned_jobs": [], "cancelled": [], "job": { "id": "test-job", "state": "running", "run_id": None, "output": "Working…", "cwd": "/home/michael/Programming", "error": None, "can_cancel": True }, "runs": {
        RUN: { "id": RUN, "title": "External CLI session", "source": "CLI", "location": "LOCAL", "state": "SUCCEEDED", "can_send": True, "conversation_id": CONVERSATION, "created_at": "2026-10-12T10:00:00Z", "cost": { "credits": 2, "usd_approx": .1 } },
        NEXT: { "id": NEXT, "title": "Follow-up session", "source": "CLI", "location": "LOCAL", "state": "SUCCEEDED", "can_send": True, "conversation_id": CONVERSATION, "created_at": "2026-10-12T10:00:00Z", "cost": { "credits": 4, "usd_approx": .2 } },
    } };

    def respond( route ):
        parsed = urlsplit( route.request.url ); path = parsed.path;
        if path == "/" or path.startswith( "/assets/" ):
            file = STATIC / ( "index.html" if path == "/" else path.removeprefix( "/assets/" ) );
            route.fulfill( status=200, body=file.read_text(), content_type={ ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[file.suffix], headers={ "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'" } );
            return;
        data = {};
        if path == "/api/session": data = { "authenticated": True };
        elif path == "/api/logout": data = { "authenticated": False };
        elif path == "/api/models": data = { "models": [] if control.get( "no_models" ) else [ "auto", "fixture-model" ], "available": not control.get( "no_models" ) };
        elif path == "/api/runs": data = { "runs": list( control["runs"].values() ) if control.get( "all_runs" ) else [ control["runs"][RUN] ], "next_cursor": None };
        elif path == "/api/jobs" and route.request.method == "GET":
            if control.get( "old_backend" ):
                route.fulfill( status=405, json={ "detail": "Method Not Allowed" } ); return;
            data = { "jobs": [ job for job in control["owned_jobs"] if job["state"] in ( "running", "stopping" ) ] };
        elif path.endswith( "/cancel" ):
            control["cancelled"].append( path.split( "/" )[3] );
            if control.get( "hold_cancel" ):
                control["held_cancel"] = route; return;
            if control.get( "reject_cancel" ):
                route.fulfill( status=503, json={ "detail": "Unable to signal the owned local agent" } ); return;
            current = next( ( job for job in control["owned_jobs"] if job["id"] == path.split( "/" )[3] ), control["job"] );
            current.update( { "state": "stopping", "can_cancel": False } ); data = current;
        elif path == "/api/render": data = { "html": render_markdown( route.request.post_data_json["text"] ) };
        elif path.endswith( "/metadata" ):
            if control.get( "fail_metadata" ):
                route.fulfill( status=503, json={ "detail": "Warp API is unavailable" } ); return;
            if control.get( "old_backend" ):
                route.fulfill( status=404, json={ "detail": "Not Found" } ); return;
            if control.get( "hold_metadata" ):
                control["held_metadata"] = route; return;
            data = control["runs"][path.split( "/" )[3]];
        elif path.startswith( "/api/runs/" ) and not path.endswith( ( "/history", "/messages" ) ): data = control["runs"][path.split( "/" )[3]];
        elif path.endswith( "/history" ):
            query = parse_qs( parsed.query ); control["queries"].append( query );
            start = int( query.get( "after", [ 0 ] )[0] );
            turns = control["turns"][start:];
            data = { "turns": turns, "start": start, "end": len( control["turns"] ), "older": False, "newer": False };
        elif path == "/api/jobs" or path.endswith( "/messages" ):
            control["posts"].append( route.request.post_data_json );
            if control["hold"]:
                control["held"].append( route ); return;
            if control["reject"]:
                route.fulfill( status=400, json={ "detail": "Directory is not allowed" } ); return;
            data = control["job"];
        elif path.startswith( "/api/jobs/" ):
            control.setdefault( "job_queries", [] ).append( path.split( "/" )[3] );
            data = next( ( job for job in control["owned_jobs"] if job["id"] == path.split( "/" )[3] ), control["job"] );
        else:
            route.fulfill( status=404, json={ "detail": "Mock route missing" } ); return;
        route.fulfill( status=200, json=data );

    page.route( "**/*", respond );
    page.goto( "https://warpweb.test/" );
    playwright.expect( page.locator( ".run-card" ) ).to_be_visible();
    yield page, control;
    assert not errors, errors;
    context.close();


def open_run( page ):
    page.locator( ".run-card" ).click();
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();


def owned_fixture( control, run_id=RUN, job_id="owned" ):
    job = { **control["job"], "id": job_id, "run_id": run_id, "conversation_id": CONVERSATION, "prompt": "example-only running task", "can_cancel": True };
    control["owned_jobs"].append( job );
    control["runs"][run_id].update( { "state": "INPROGRESS", "can_send": False, "local_job": job } );
    return job;


def test_stop_recovers_after_reload_and_preserves_cancelled_cards( ui ):
    page, control = ui;
    page.clock.install();
    job = owned_fixture( control );
    page.reload();
    page.locator( ".owned-job-card" ).click();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_visible();
    page.reload();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    assert page.evaluate( "state.pending.filter( item => item.jobId === 'owned' ).length" ) == 1;
    assert not control["posts"];
    page.locator( "#stop-agent" ).click();
    playwright.expect( page.locator( "#stop-label" ) ).to_have_text( "Stopping…" );
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_disabled();
    page.locator( "#stop-agent" ).dispatch_event( "click" );
    assert control["cancelled"] == [ "owned" ];
    job.update( { "state": "cancelled", "can_cancel": False } );
    control["runs"][RUN].update( { "state": "CANCELLED", "can_send": True } );
    page.clock.run_for( 3000 );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "cancelled locally" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "Cancelled" );
    playwright.expect( page.locator( "#stop-agent" ) ).not_to_be_visible();
    page.clock.run_for( 4000 );
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();
    assert page.evaluate( "document.documentElement.scrollWidth <= innerWidth" );


def test_new_job_can_stop_before_run_verification( ui ):
    page, control = ui;
    page.clock.install();
    page.locator( ".new-card" ).click();
    page.locator( "#new-prompt" ).fill( "example-only task" );
    page.locator( "#new-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_visible();
    page.locator( "#stop-agent" ).click();
    assert control["cancelled"] == [ "test-job" ];
    control["job"].update( { "state": "cancelled", "can_cancel": False } );
    page.clock.run_for( 3000 );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "Cancelled" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "example-only task" );
    playwright.expect( page.locator( "#session-note" ) ).to_contain_text( "were not undone" );


def test_minimize_does_not_cancel_owned_agent( ui ):
    page, control = ui;
    page.clock.install();
    owned_fixture( control );
    page.reload();
    page.locator( ".owned-job-card" ).click();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_visible();
    page.locator( "#minimize" ).click();
    playwright.expect( page.locator( ".owned-job-card" ) ).to_be_visible();
    assert not control["cancelled"];
    page.locator( ".owned-job-card" ).click();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    assert not control["cancelled"];


def test_delayed_cancel_response_cannot_target_another_viewed_job( ui ):
    page, control = ui;
    page.clock.install();
    one = owned_fixture( control, RUN, "one" );
    two = owned_fixture( control, NEXT, "two" );
    control["all_runs"] = True;
    page.reload();
    page.locator( ".owned-job-card" ).first.click();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    control["hold_cancel"] = True;
    page.locator( "#stop-agent" ).click();
    page.evaluate( f"selectRun( '{NEXT}' )" );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    one.update( { "state": "stopping", "can_cancel": False } );
    control["held_cancel"].fulfill( status=202, json=one );
    playwright.expect( page.locator( "#stop-label" ) ).to_have_text( "Stop" );
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    assert page.evaluate( "currentJob().id" ) == "two" and two["state"] == "running";
    control["hold_cancel"] = False;
    page.locator( "#stop-agent" ).click();
    assert control["cancelled"] == [ "one", "two" ];


def test_stop_error_is_retryable_and_external_runs_have_no_stop( ui ):
    page, control = ui;
    page.clock.install();
    owned_fixture( control );
    page.reload();
    page.locator( ".owned-job-card" ).click();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    control["reject_cancel"] = True;
    page.locator( "#stop-agent" ).click();
    playwright.expect( page.locator( "#session-note" ) ).to_contain_text( "Unable to signal" );
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    page.evaluate( f"selectRun( '{NEXT}' )" );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    playwright.expect( page.locator( "#stop-agent" ) ).not_to_be_visible();
    assert control["cancelled"] == [ "owned" ];


def test_owned_stop_survives_unavailable_warp_metadata( ui ):
    page, control = ui;
    page.clock.install();
    owned_fixture( control );
    control["fail_metadata"] = True;
    page.reload();
    page.locator( ".owned-job-card" ).click();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    playwright.expect( page.locator( "#session-title" ) ).to_contain_text( "history unavailable" );
    page.reload();
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    page.locator( "#stop-agent" ).click();
    assert control["cancelled"] == [ "owned" ];


@pytest.mark.parametrize( "cadence", [ 1, 2 ] )
def test_cancelled_job_usage_respects_prompt_cadence( ui, cadence ):
    page, control = ui;
    page.clock.install();
    open_run( page );
    page.evaluate( f"preferences.cost = {cadence}; savePreferences();" );
    control["job"].update( { "run_id": NEXT, "conversation_id": CONVERSATION } );
    control["runs"][NEXT].update( { "state": "INPROGRESS", "can_send": False, "local_job": control["job"] } );
    page.locator( "#session-prompt" ).fill( "example-only continuation" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    page.locator( "#stop-agent" ).click();
    control["job"].update( { "state": "cancelled", "can_cancel": False } );
    control["runs"][NEXT].update( { "state": "CANCELLED", "can_send": True, "cost": { "credits": 7, "usd_approx": .37 } } );
    page.clock.run_for( 3000 );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "$0.37" if cadence == 1 else "$0.10" );
    page.clock.run_for( 4000 );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "$0.37" if cadence == 1 else "$0.10" );


def test_favicon_is_linked_and_decodes( ui ):
    page, _ = ui;
    assert page.locator( 'link[rel="icon"]' ).get_attribute( "href" ) == "/assets/favicon.svg";
    dimensions = page.evaluate( """async () => { const icon = new Image(); icon.src = document.querySelector( 'link[rel="icon"]' ).href; await icon.decode(); return [ icon.naturalWidth, icon.naturalHeight ]; }""" );
    assert dimensions == [ 32, 32 ];


def test_ctrl_enter_pending_before_response_and_retry( ui ):
    page, control = ui;
    control["hold"] = True;
    open_run( page );
    page.locator( "#session-prompt" ).fill( "My visible prompt" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "My visible prompt" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "Submitting" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    assert len( control["posts"] ) == 1;
    control["held"][0].fulfill( status=400, json={ "detail": "Directory is not allowed" } );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "Failed" );
    assert page.locator( "#session-prompt" ).input_value() == "My visible prompt";
    page.get_by_role( "button", name="Edit and retry" ).click();
    playwright.expect( page.locator( "#session-prompt" ) ).to_have_value( "My visible prompt" );
    assert page.locator( "#recent-directories option" ).count() == 0;


def test_new_session_user_card_survives_progress_and_rocket( ui ):
    page, control = ui;
    control["hold"] = True;
    page.locator( ".new-card" ).click();
    assert page.locator( "#new-form svg.rocket" ).count() == 1;
    page.locator( "#new-prompt" ).fill( "First task" );
    page.locator( "#new-prompt" ).press( "Enter" );
    assert not control["posts"];
    page.locator( "#new-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "First task" );
    control["held"][0].fulfill( status=200, json=control["job"] );
    playwright.expect( page.locator( ".turn-agent" ) ).to_contain_text( "Working…" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "First task" );
    assert page.locator( "#recent-directories option" ).first.get_attribute( "value" ) == "/home/michael/Programming";
    assert page.locator( "#session-cwd" ).input_value() == "/home/michael/Programming";
    stored = json.loads( page.evaluate( "localStorage.getItem( preferenceKey )" ) );
    assert "First task" not in json.dumps( stored );


def test_options_persist_validate_and_trim_mru( ui ):
    page, _ = ui;
    page.locator( "#options-toggle" ).click();
    for key, value in [ ( "mru", "2" ), ( "history", "16" ), ( "lines", "4" ), ( "cost", "3" ) ]:
        page.locator( f"#option-{key}" ).fill( value );
        page.locator( f"#option-{key}" ).dispatch_event( "change" );
    page.evaluate( "rememberWorkdir( '/one' ); rememberWorkdir( '/two' ); rememberWorkdir( '/three' ); rememberWorkdir( '/two' );" );
    assert page.locator( "#recent-directories option" ).evaluate_all( "nodes => nodes.map( node => node.value )" ) == [ "/two", "/three" ];
    page.reload();
    page.locator( "#options-toggle" ).click();
    playwright.expect( page.locator( "#option-cost" ) ).to_have_value( "3" );
    page.locator( "#option-cost" ).fill( "0" ); page.locator( "#option-cost" ).dispatch_event( "change" );
    playwright.expect( page.locator( "#option-cost" ) ).to_have_value( "3" );
    page.locator( "#mru-clear" ).click();
    assert page.locator( "#recent-directories option" ).count() == 0;
    page.locator( "#options-reset" ).click();
    playwright.expect( page.locator( "#option-mru" ) ).to_have_value( "20" );


def test_markdown_tool_input_and_output_disclosure_mobile_layout( ui ):
    page, control = ui;
    control["turns"] = [
        { "actor": "Agent", "body": "# Heading\n\n**Bold**\n\n```python\nprint( '<script>' );\n```", "tool": False, "block_id": "a", "fragment": 0, "fragments": 1 },
        { "actor": "Tool", "title": "shell · call", "body": "command: pwd", "tool": True, "kind": "action", "action_id": "call", "block_id": "b" },
        { "actor": "Tool", "title": "shell · result", "body": "/home/michael", "tool": True, "kind": "action_result", "action_id": "call", "block_id": "c" },
    ];
    open_run( page );
    playwright.expect( page.locator( ".markdown h1" ) ).to_have_text( "Heading" );
    playwright.expect( page.locator( ".markdown strong" ) ).to_have_text( "Bold" );
    playwright.expect( page.locator( ".tool-command" ) ).to_have_text( "command: pwd" );
    assert page.locator( ".turn-tool" ).count() == 1;
    assert not page.locator( ".tool-output" ).evaluate( "node => node.open" );
    page.locator( ".tool-output summary" ).click();
    playwright.expect( page.locator( ".tool-output pre" ) ).to_be_visible();
    assert page.locator( "#send svg.rocket" ).count() == 1;
    assert page.evaluate( "document.documentElement.scrollWidth <= innerWidth" );
    assert page.locator( "#history-scroll" ).bounding_box()["height"] > 200;


def test_partial_fragments_stay_plain_until_complete_and_expand( ui ):
    page, control = ui;
    control["turns"] = [ { "actor": "Agent", "body": "print( 1 );\n" * 30 + "```", "tool": False, "block_id": "large", "fragment": 1, "fragments": 2 } ];
    open_run( page );
    assert page.locator( ".markdown" ).count() == 0;
    page.evaluate( "state.turns.unshift( { actor: 'Agent', body: '```python\\n', tool: false, block_id: 'large', fragment: 0, fragments: 2 } ); redrawHistory();" );
    playwright.expect( page.locator( ".markdown pre code" ) ).to_be_visible();
    playwright.expect( page.get_by_role( "button", name="Show more" ) ).to_be_visible();
    page.get_by_role( "button", name="Show more" ).click();
    assert page.locator( ".turn-agent" ).evaluate( "node => node.classList.contains( 'expanded' )" );


def test_cost_cadence_and_missing_usage( ui ):
    page, control = ui;
    open_run( page );
    page.evaluate( "preferences.cost = 2; savePreferences();" );
    control["job"].update( { "state": "completed", "run_id": NEXT, "output": "" } );
    page.locator( "#session-prompt" ).fill( "One" ); page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "Last refreshed run cost" );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "$0.10" );
    page.locator( "#session-prompt" ).fill( "Two" ); page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "$0.20" );
    assert len( control["posts"] ) == 2;
    page.evaluate( "updateHeader( { ...state.run, cost: { credits: null, usd_approx: null } }, true );" );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "$0.20" );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "usage pending" );


def test_active_to_finished_and_shared_session_link( ui ):
    page, control = ui;
    page.clock.install();
    control["runs"][RUN].update( { "state": "INPROGRESS", "can_send": False, "session_link": "https://app.warp.dev/s/test" } );
    page.locator( ".run-card" ).click();
    playwright.expect( page.locator( "#shared-session" ) ).to_be_visible();
    playwright.expect( page.locator( "#composer" ) ).not_to_be_visible();
    control["runs"][RUN].update( { "state": "SUCCEEDED", "can_send": True } );
    page.clock.fast_forward( 6100 );
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();
    assert all( query["size"] == [ "8192" ] for query in control["queries"] );


def test_minimized_job_does_not_replace_another_session( ui ):
    page, control = ui;
    open_run( page );
    control["hold"] = True;
    page.locator( "#session-prompt" ).fill( "Background prompt" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( ".turn-user" ) ).to_be_visible();
    page.locator( "#minimize" ).click();
    page.evaluate( f"selectRun( '{NEXT}' )" );
    control["job"].update( { "state": "completed", "run_id": RUN } );
    control["held"][0].fulfill( status=200, json=control["job"] );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    assert page.locator( ".turn-user" ).count() == 0;


def test_new_backend_metadata_falls_back_before_service_restart( ui ):
    page, control = ui;
    control["old_backend"] = True;
    open_run( page );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "External CLI session" );


def test_pending_card_remains_during_run_attachment_and_reconciles_once( ui ):
    page, control = ui;
    page.locator( ".new-card" ).click();
    control["hold_metadata"] = True;
    control["job"].update( { "run_id": NEXT } );
    page.locator( "#new-prompt" ).fill( "Original request" );
    page.locator( "#new-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "Original request" );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Loading session…" );
    assert page.locator( ".turn-user" ).count() == 1;
    control["hold_metadata"] = False;
    control["turns"] = [ { "actor": "User", "body": "Original request", "tool": False, "block_id": "canonical", "fragment": 0, "fragments": 1 } ];
    control["held_metadata"].fulfill( status=200, json=control["runs"][NEXT] );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    playwright.expect( page.locator( ".turn-user" ) ).to_have_count( 1 );


def test_same_prompt_as_previous_turn_is_not_mistaken_for_confirmation( ui ):
    page, control = ui;
    control["turns"] = [ { "actor": "User", "body": "Repeat", "tool": False, "block_id": "old", "fragment": 0, "fragments": 1 } ];
    control["hold"] = True;
    open_run( page );
    playwright.expect( page.locator( ".turn-user" ) ).to_have_count( 1 );
    page.locator( "#session-prompt" ).fill( "Repeat" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( ".turn-user" ) ).to_have_count( 2 );
    control["held"][0].fulfill( status=400, json={ "detail": "Rejected" } );
    playwright.expect( page.get_by_role( "button", name="Edit and retry" ) ).to_be_visible();


def test_invalid_storage_and_unavailable_storage_fall_back( ui ):
    page, _ = ui;
    page.evaluate( "localStorage.setItem( preferenceKey, 'null' )" );
    page.reload();
    page.locator( "#options-toggle" ).click();
    playwright.expect( page.locator( "#option-mru" ) ).to_have_value( "20" );
    page.evaluate( "Storage.prototype.setItem = () => { throw new Error( 'Disabled' ); }; rememberWorkdir( '/temporary' );" );
    playwright.expect( page.locator( "#options-note" ) ).to_contain_text( "Storage unavailable" );
    assert page.locator( "#recent-directories option" ).count() == 1;


def swipe_session( page, dx=-140, dy=0, selector="#history-scroll" ):
    page.locator( selector ).evaluate( """( node, delta ) => {
        const start = new Touch( { identifier: 1, target: node, clientX: 220, clientY: 300 } );
        const end = new Touch( { identifier: 1, target: node, clientX: 220 + delta.dx, clientY: 300 + delta.dy } );
        node.dispatchEvent( new TouchEvent( 'touchstart', { bubbles: true, touches: [ start ] } ) );
        node.dispatchEvent( new TouchEvent( 'touchend', { bubbles: true, touches: [], changedTouches: [ end ] } ) );
    }""", { "dx": dx, "dy": dy } );


def test_stop_keeps_draft_and_enables_next_turn_despite_stale_warp_state( ui ):
    page, control = ui; page.clock.install();
    job = owned_fixture( control );
    page.reload(); page.locator( ".owned-job-card" ).click();
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();
    playwright.expect( page.locator( "#send" ) ).to_be_disabled();
    page.locator( "#session-prompt" ).fill( "Change direction after Stop" );
    page.locator( "#stop-agent" ).click();
    job.update( { "state": "cancelled", "can_cancel": False, "can_resume": True } );
    page.clock.run_for( 3000 );
    playwright.expect( page.locator( "#send" ) ).to_be_enabled();
    playwright.expect( page.locator( "#session-prompt" ) ).to_have_value( "Change direction after Stop" );
    assert control["runs"][RUN]["state"] == "INPROGRESS";
    page.reload();
    playwright.expect( page.locator( "#send" ) ).to_be_enabled();
    page.locator( "#session-prompt" ).fill( "Next after refresh" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    assert control["posts"][-1]["prompt"] == "Next after refresh";


def test_new_session_refresh_before_verification_recovers_job_and_next_input( ui ):
    page, control = ui; page.clock.install();
    page.locator( ".new-card" ).click();
    page.locator( "#new-prompt" ).fill( "Unverified task" );
    page.locator( "#new-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_visible();
    assert "?job=test-job" in page.url;
    control["owned_jobs"].append( control["job"] );
    page.reload();
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();
    playwright.expect( page.locator( "#send" ) ).to_be_disabled();
    assert len( control["posts"] ) == 1;
    page.locator( "#stop-agent" ).click();
    control["job"].update( { "state": "cancelled", "can_cancel": False, "can_resume": False } );
    page.clock.run_for( 3000 );
    playwright.expect( page.locator( "#session-new" ) ).to_be_visible();
    page.locator( "#session-prompt" ).fill( "Start again safely" );
    control["job"] = { **control["job"], "id": "another", "state": "running", "can_cancel": True };
    page.locator( "#session-new" ).click();
    assert len( control["posts"] ) == 2 and control["posts"][-1]["prompt"] == "Start again safely";


def test_stopped_followup_keeps_new_job_identity_over_previous_run_metadata( ui ):
    page, control = ui; page.clock.install();
    previous = owned_fixture( control );
    previous.update( { "state": "completed", "can_cancel": False, "can_resume": True } );
    control["runs"][RUN].update( { "state": "SUCCEEDED", "can_send": True } );
    page.reload(); page.locator( ".run-card" ).click();
    playwright.expect( page.locator( "#send" ) ).to_be_enabled();
    control["job"].update( { "conversation_id": CONVERSATION } );
    page.locator( "#session-prompt" ).fill( "Follow-up" ); page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#stop-agent" ) ).to_be_enabled();
    page.locator( "#session-prompt" ).fill( "After interruption" );
    page.locator( "#stop-agent" ).click();
    control["job"].update( { "state": "cancelled", "can_cancel": False, "can_resume": True } );
    page.clock.run_for( 3000 );
    playwright.expect( page.locator( "#session-status" ) ).to_contain_text( "cancelled locally" );
    playwright.expect( page.locator( "#send" ) ).to_be_enabled();
    playwright.expect( page.locator( "#session-prompt" ) ).to_have_value( "After interruption" );
    assert page.evaluate( "currentJob().id" ) == "test-job";


def test_two_active_sessions_swipe_preserves_drafts_models_and_watchers( ui ):
    page, control = ui; page.clock.install();
    one = owned_fixture( control, RUN, "one" ); two = owned_fixture( control, NEXT, "two" );
    two["conversation_id"] = NEXT; control["runs"][NEXT]["conversation_id"] = NEXT;
    control["all_runs"] = True;
    page.reload(); page.locator( ".owned-job-card" ).first.click();
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();
    page.locator( "#session-prompt" ).fill( "Draft one" );
    page.locator( "#turn-settings-toggle" ).click();
    page.locator( "#session-model" ).select_option( "fixture-model" );
    page.locator( "#session-cwd" ).fill( "/one" );
    swipe_session( page );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    page.locator( "#session-prompt" ).fill( "Draft two" );
    page.locator( "#turn-settings-toggle" ).click();
    page.locator( "#session-cwd" ).fill( "/two" );
    swipe_session( page, dx=140 );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "External CLI session" );
    playwright.expect( page.locator( "#session-prompt" ) ).to_have_value( "Draft one" );
    playwright.expect( page.locator( "#session-cwd" ) ).to_have_value( "/one" );
    playwright.expect( page.locator( "#session-model" ) ).to_have_value( "fixture-model" );
    playwright.expect( page.locator( "#turn-settings" ) ).not_to_be_visible();
    assert page.evaluate( "state.watchers.size" ) == 2;
    page.clock.run_for( 3000 );
    assert { "one", "two" }.issubset( set( control["job_queries"] ) );
    assert not control["cancelled"] and not control["posts"];
    page.locator( "#next-session" ).click();
    playwright.expect( page.locator( "#session-prompt" ) ).to_have_value( "Draft two" );
    one.update( { "state": "cancelled", "can_cancel": False, "can_resume": True } );
    page.clock.run_for( 3000 );
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );
    playwright.expect( page.locator( "#send" ) ).to_be_disabled();
    assert page.evaluate( "currentJob().id" ) == "two";
    assert page.evaluate( "document.documentElement.scrollWidth <= innerWidth" );


def test_second_session_can_submit_while_first_runs_and_background_response_keeps_draft( ui ):
    page, control = ui; page.clock.install();
    one = owned_fixture( control, RUN, "one" );
    control["runs"][NEXT]["conversation_id"] = NEXT; control["all_runs"] = True;
    page.reload(); page.locator( ".owned-job-card" ).click();
    playwright.expect( page.locator( "#send" ) ).to_be_disabled();
    page.locator( "#next-session" ).click();
    playwright.expect( page.locator( "#send" ) ).to_be_enabled();
    control["hold"] = True;
    page.locator( "#session-prompt" ).fill( "Second parallel command" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    assert len( control["posts"] ) == 1;
    page.locator( "#previous-session" ).click();
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "External CLI session" );
    page.locator( "#session-prompt" ).fill( "Keep this draft" );
    second = { **control["job"], "id": "second", "conversation_id": NEXT, "run_id": None };
    control["owned_jobs"].append( second );
    control["held"][0].fulfill( status=200, json=second );
    playwright.expect( page.locator( "#session-prompt" ) ).to_have_value( "Keep this draft" );
    assert page.evaluate( "state.watchers.size" ) == 2;
    page.clock.run_for( 3000 );
    assert one["state"] == "running" and not control["cancelled"];


def test_new_and_next_turn_models_and_missing_catalog_fallback( ui ):
    page, control = ui; page.clock.install();
    page.locator( ".new-card" ).click();
    page.locator( "#new-model" ).select_option( "fixture-model" );
    page.locator( "#new-prompt" ).fill( "Selected model" );
    control["job"].update( { "run_id": NEXT, "conversation_id": CONVERSATION, "state": "completed", "can_resume": True, "model": "fixture-model" } );
    page.locator( "#new-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#send" ) ).to_be_enabled();
    assert control["posts"][-1]["model"] == "fixture-model";
    page.locator( "#turn-settings-toggle" ).click();
    page.locator( "#session-model" ).select_option( "auto" );
    page.locator( "#session-prompt" ).fill( "Other model" ); page.locator( "#session-prompt" ).press( "Control+Enter" );
    assert control["posts"][-1]["model"] == "auto";
    control["no_models"] = True;
    page.goto( "https://warpweb.test/" ); page.locator( ".new-card" ).click();
    playwright.expect( page.locator( "#model-note" ) ).to_contain_text( "Default remains available" );
    assert page.locator( "#new-model" ).input_value() == "";


def test_next_turn_settings_open_above_rocket_and_keep_hidden_selections( ui ):
    page, control = ui; open_run( page );
    toggle = page.get_by_role( "button", name="Next-turn directory and model", exact=True );
    playwright.expect( toggle ).to_have_attribute( "aria-expanded", "false" );
    playwright.expect( page.locator( "#session-cwd" ) ).not_to_be_visible();
    playwright.expect( page.locator( "#session-model" ) ).not_to_be_visible();
    arrow = toggle.bounding_box(); rocket = page.locator( "#send" ).bounding_box();
    assert arrow["y"] + arrow["height"] <= rocket["y"] and arrow["x"] == rocket["x"];
    toggle.press( "Enter" );
    playwright.expect( toggle ).to_have_attribute( "aria-expanded", "true" );
    playwright.expect( page.locator( "#session-model" ) ).to_be_visible();
    settings = page.locator( "#turn-settings" ).bounding_box(); row = page.locator( ".compose-row" ).bounding_box();
    assert settings["y"] + settings["height"] <= row["y"];
    page.locator( "#session-cwd" ).fill( "/requested/project" );
    page.locator( "#session-model" ).select_option( "fixture-model" );
    toggle.click();
    page.locator( "#session-prompt" ).fill( "Hidden settings are applied" );
    page.locator( "#send" ).click();
    assert control["posts"][-1] == { "prompt": "Hidden settings are applied", "cwd": "/requested/project", "model": "fixture-model" };
    assert page.evaluate( "document.documentElement.scrollWidth <= innerWidth" );


@pytest.mark.parametrize( "keyboard", [ False, True ] )
def test_invalid_hidden_directory_opens_settings_before_validation_focus( ui, keyboard ):
    page, control = ui; open_run( page );
    page.locator( "#turn-settings-toggle" ).click();
    page.locator( "#session-cwd" ).fill( "" );
    page.locator( "#turn-settings-toggle" ).click();
    page.locator( "#session-prompt" ).fill( "Requires a directory" );
    if keyboard: page.locator( "#session-prompt" ).press( "Control+Enter" );
    else: page.locator( "#send" ).click();
    playwright.expect( page.locator( "#turn-settings" ) ).to_be_visible();
    playwright.expect( page.locator( "#turn-settings-toggle" ) ).to_have_attribute( "aria-expanded", "true" );
    playwright.expect( page.locator( "#session-cwd" ) ).to_be_focused();
    assert not control["posts"];


def test_missing_directory_feedback_does_not_record_fallback_as_resolution( ui ):
    page, control = ui;
    control["job"].update( { "requested_cwd": "/home/michael/Programing", "directory_pending": True } );
    page.locator( ".new-card" ).click(); page.locator( "#new-cwd" ).fill( "/home/michael/Programing" );
    page.locator( "#new-prompt" ).fill( "Find project" ); page.locator( "#new-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( "#session-note" ) ).to_contain_text( "is being located" );
    playwright.expect( page.locator( "#session-cwd" ) ).to_have_value( "/home/michael/Programing" );
    assert page.locator( "#recent-directories option" ).count() == 0;


def test_vertical_and_interactive_swipes_do_not_change_session( ui ):
    page, control = ui;
    control["runs"][NEXT]["conversation_id"] = NEXT; control["all_runs"] = True;
    control["turns"] = [ { "actor": "Tool", "tool": True, "kind": "action_result", "body": "example", "block_id": "output" } ];
    page.reload(); page.locator( ".run-card" ).first.click();
    playwright.expect( page.locator( "#composer" ) ).to_be_visible();
    swipe_session( page, dx=-80, dy=120 );
    assert page.evaluate( "state.selected" ) == RUN;
    swipe_session( page, selector=".tool-output summary" );
    assert page.evaluate( "state.selected" ) == RUN;
    page.locator( "#next-session" ).click();
    playwright.expect( page.locator( "#session-title" ) ).to_have_text( "Follow-up session" );


def test_delayed_submission_after_logout_cannot_restore_jobs_or_drafts( ui ):
    page, control = ui; page.clock.install(); open_run( page );
    control["hold"] = True;
    page.locator( "#session-prompt" ).fill( "Private draft" ); page.locator( "#session-prompt" ).press( "Control+Enter" );
    page.locator( "#minimize" ).click(); page.locator( "#logout" ).click();
    playwright.expect( page.locator( "#login-view" ) ).to_be_visible();
    control["held"][0].fulfill( status=200, json=control["job"] );
    page.clock.run_for( 3000 );
    assert page.evaluate( "state.jobs.size + state.watchers.size + state.drafts.size" ) == 0;
    assert not control["cancelled"];


def test_previous_tool_history_does_not_hide_current_cli_progress( ui ):
    page, control = ui;
    control["turns"] = [ { "actor": "Tool", "title": "shell · call", "body": "command: previous", "tool": True, "kind": "action", "block_id": "old-tool", "fragment": 0, "fragments": 1 } ];
    open_run( page );
    playwright.expect( page.locator( ".tool-command" ) ).to_be_visible();
    page.locator( "#session-prompt" ).fill( "Current task" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( ".turn-agent" ) ).to_contain_text( "Working…" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "Current task" );
