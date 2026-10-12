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
    control = { "posts": [], "held": [], "hold": False, "reject": False, "queries": [], "turns": [], "job": { "id": "test-job", "state": "running", "run_id": None, "output": "Working…", "cwd": "/home/michael/Programming", "error": None }, "runs": {
        RUN: { "id": RUN, "title": "External CLI session", "source": "CLI", "location": "LOCAL", "state": "SUCCEEDED", "can_send": True, "conversation_id": CONVERSATION, "created_at": "2026-10-12T10:00:00Z", "cost": { "credits": 2, "usd_approx": .1 } },
        NEXT: { "id": NEXT, "title": "Follow-up session", "source": "CLI", "location": "LOCAL", "state": "SUCCEEDED", "can_send": True, "conversation_id": CONVERSATION, "created_at": "2026-10-12T10:00:00Z", "cost": { "credits": 4, "usd_approx": .2 } },
    } };

    def respond( route ):
        parsed = urlsplit( route.request.url ); path = parsed.path;
        if path == "/" or path.startswith( "/assets/" ):
            file = STATIC / ( "index.html" if path == "/" else path.removeprefix( "/assets/" ) );
            route.fulfill( status=200, body=file.read_text(), content_type={ ".html": "text/html", ".js": "text/javascript", ".css": "text/css" }[file.suffix], headers={ "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'" } );
            return;
        data = {};
        if path == "/api/session": data = { "authenticated": True };
        elif path == "/api/runs": data = { "runs": [ control["runs"][RUN] ], "next_cursor": None };
        elif path == "/api/render": data = { "html": render_markdown( route.request.post_data_json["text"] ) };
        elif path.endswith( "/metadata" ):
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
        elif path.startswith( "/api/jobs/" ): data = control["job"];
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


def test_previous_tool_history_does_not_hide_current_cli_progress( ui ):
    page, control = ui;
    control["turns"] = [ { "actor": "Tool", "title": "shell · call", "body": "command: previous", "tool": True, "kind": "action", "block_id": "old-tool", "fragment": 0, "fragments": 1 } ];
    open_run( page );
    playwright.expect( page.locator( ".tool-command" ) ).to_be_visible();
    page.locator( "#session-prompt" ).fill( "Current task" );
    page.locator( "#session-prompt" ).press( "Control+Enter" );
    playwright.expect( page.locator( ".turn-agent" ) ).to_contain_text( "Working…" );
    playwright.expect( page.locator( ".turn-user" ) ).to_contain_text( "Current task" );
