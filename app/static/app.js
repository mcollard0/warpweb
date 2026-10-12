"use strict";

const $ = ( id ) => document.getElementById( id );
const state = { scope: "local", cursor: null, loading: false, generation: 0, selected: null, canSend: false, historyStart: 0, historyEnd: 0, older: false, busy: false, poll: null, job: null, run: null, epoch: 0, turns: [], pending: [], viewKey: null, submitting: false, usage: null, usageId: null, usageNote: "" };
const markdownCache = new Map();
const activeStates = new Set( [ "INPROGRESS", "RUNNING", "PENDING", "QUEUED", "CLAIMED", "BLOCKED" ] );

function show( view ) {
    for ( const id of [ "login-view", "chooser-view", "new-view", "session-view" ] ) $( id ).classList.toggle( "hidden", id !== view );
    $( "logout" ).classList.toggle( "hidden", view === "login-view" || view === "session-view" );
    $( "options-toggle" ).classList.toggle( "hidden", view === "login-view" );
    if ( view === "login-view" ) {
        $( "options-card" ).classList.add( "hidden" );
        $( "options-toggle" ).setAttribute( "aria-expanded", "false" );
    }
}
function turnCard( turn ) {
    const actor = [ "User", "Agent", "Tool", "System" ].includes( turn.actor ) ? turn.actor : "Agent";
    const card = document.createElement( "article" );
    card.className = `turn turn-${actor.toLowerCase()}`;
    card.dataset.key = turn.key || turn.block_id || "";
    const header = element( "header", "turn-header", "" );
    header.append( element( "strong", "", actor ) );
    if ( turn.title ) header.append( element( "span", "", turn.title ) );
    if ( turn.continued ) header.append( element( "span", "", "continued" ) );
    if ( turn.timestamp ) {
        const date = new Date( turn.timestamp );
        if ( !Number.isNaN( date.getTime() ) ) header.append( element( "time", "", date.toLocaleString() ) );
    }
    card.append( header );
    if ( turn.tool ) {
        if ( turn.kind === "action" || !turn.kind && turn.title?.endsWith( "call" ) ) {
            card.append( element( "pre", "turn-body tool-command", turn.body || "Command/input unavailable." ) );
            if ( turn.result !== undefined ) {
                const details = element( "details", "tool-output", "" );
                details.append( element( "summary", "", "Output" ), element( "pre", "turn-body", turn.result || "No output." ) );
                card.append( details );
            } else card.append( element( "small", "tool-waiting", "Output not available yet; any unpaired result is shown separately." ) );
        } else {
            const details = element( "details", "tool-output", "" );
            details.append( element( "summary", "", "Output" ), element( "pre", "turn-body", turn.body || "Output unavailable." ) );
            card.append( details );
        }
    } else {
        const body = element( "div", "turn-body prose-body", turn.body );
        card.append( body );
        const toggle = element( "button", "card-expand hidden", "Show more" );
        toggle.type = "button";
        toggle.setAttribute( "aria-expanded", "false" );
        toggle.addEventListener( "click", () => {
            const expanded = card.classList.toggle( "expanded" );
            toggle.textContent = expanded ? "Show less" : "Show more";
            toggle.setAttribute( "aria-expanded", String( expanded ) );
        } );
        card.append( toggle );
        const measure = () => {
            if ( !card.isConnected ) return;
            const limit = preferences.lines * 1.6 * parseFloat( getComputedStyle( body ).fontSize );
            toggle.classList.toggle( "hidden", body.scrollHeight <= limit + 2 );
        };
        card.measure = measure;
        requestAnimationFrame( measure );
        if ( !turn.partial && new TextEncoder().encode( turn.body || "" ).length <= 65536 ) {
            const source = turn.body || "";
            if ( !markdownCache.has( source ) ) {
                markdownCache.set( source, api( "/api/render", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify( { text: source } ) } ).catch( () => ( { html: null } ) ) );
                if ( markdownCache.size > 32 ) markdownCache.delete( markdownCache.keys().next().value );
            }
            markdownCache.get( source ).then( data => {
                if ( !card.isConnected || typeof data.html !== "string" ) return;
                const scroll = $( "history-scroll" );
                const height = scroll.scrollHeight;
                const nearBottom = height - scroll.scrollTop - scroll.clientHeight < 120;
                const aboveViewport = card.getBoundingClientRect().bottom < scroll.getBoundingClientRect().top;
                // HTML is emitted only by the authenticated, allowlist-sanitizing server.
                body.innerHTML = data.html;
                body.classList.add( "markdown" );
                measure();
                if ( nearBottom ) scroll.scrollTop = scroll.scrollHeight;
                else if ( aboveViewport ) scroll.scrollTop += scroll.scrollHeight - height;
            } );
        }
    }
    return card;
}

async function api( path, options = {} ) {
    const response = await fetch( path, { credentials: "same-origin", ...options } );
    let data = {};
    try { data = await response.json(); } catch { /* A network or upstream error has no JSON body. */ }
    if ( !response.ok ) {
        const error = new Error( typeof data.detail === "string" ? data.detail : `Request failed (${response.status})` );
        error.status = response.status;
        throw error;
    }
    return data;
}

function relativeAge( timestamp ) {
    const date = new Date( timestamp );
    if ( !timestamp || Number.isNaN( date.getTime() ) ) return "Date unavailable";
    const seconds = Math.max( 0, Math.round( ( Date.now() - date.getTime() ) / 1000 ) );
    if ( seconds < 60 ) return "just now";
    if ( seconds < 3600 ) return `${Math.floor( seconds / 60 )}m ago`;
    if ( seconds < 86400 ) return `${Math.floor( seconds / 3600 )}h ago`;
    return `${Math.floor( seconds / 86400 )}d ago`;
}

function dollars( value ) {
    return typeof value === "number" ? `≈ $${value.toFixed( 2 )}` : "Cost unavailable";
}

function element( tag, className, text ) {
    const node = document.createElement( tag );
    node.className = className;
    node.textContent = text;
    return node;
}

function newRunTile() {
    const tile = element( "button", "new-card", "" );
    tile.type = "button";
    tile.append( element( "span", "new-icon", "+" ) );
    const label = document.createElement( "div" );
    label.append( element( "strong", "", "New local session" ), element( "small", "", "Run a local Oz CLI agent" ) );
    tile.append( label );
    tile.addEventListener( "click", () => { show( "new-view" ); $( "new-error" ).textContent = ""; } );
    return tile;
}

function runCard( run ) {
    const button = element( "button", "run-card", "" );
    button.type = "button";
    button.setAttribute( "aria-label", `Select session: ${run.title}` );
    const top = element( "div", "card-top", "" );
    const status = element( "span", "status", "" );
    status.append( element( "span", `status-dot ${run.state === "INPROGRESS" ? "live" : ""}`, "" ), document.createTextNode( ( run.state || "Unknown" ).toLowerCase().replaceAll( "_", " " ) ) );
    top.append( status, element( "span", "location", run.location === "LOCAL" ? ( run.source === "CLI" ? ( run.can_send ? "Local CLI · continue" : "Local CLI · active" ) : "Local · read-only" ) : "Cloud" ) );
    const foot = element( "div", "card-foot", "" );
    foot.append( element( "span", "", relativeAge( run.created_at ) ), element( "strong", "", dollars( run.cost?.usd_approx ) ) );
    button.append( top, element( "h2", "", run.title ), element( "p", "summary", run.summary || "No description provided." ), foot );
    button.addEventListener( "click", () => selectRun( run.id ) );
    return button;
}

async function loadRuns( reset = false ) {
    if ( state.loading && !reset ) return;
    state.loading = true;
    const generation = state.generation;
    if ( reset ) {
        state.cursor = null;
        $( "run-list" ).replaceChildren( newRunTile() );
    }
    $( "list-caption" ).textContent = "Loading sessions…";
    $( "load-more" ).classList.add( "hidden" );
    try {
        const params = new URLSearchParams( { scope: state.scope, limit: "30" } );
        if ( state.cursor ) params.set( "cursor", state.cursor );
        const data = await api( `/api/runs?${params}` );
        if ( generation !== state.generation ) return;
        for ( const run of data.runs ) $( "run-list" ).append( runCard( run ) );
        state.cursor = data.next_cursor || null;
        $( "load-more" ).classList.toggle( "hidden", !state.cursor );
        $( "list-caption" ).textContent = `${$( "run-list" ).childElementCount - 1} session${$( "run-list" ).childElementCount === 2 ? "" : "s"} shown`;
        if ( $( "run-list" ).childElementCount === 1 ) $( "run-list" ).append( element( "div", "empty", "No sessions found in this view. Try another filter." ) );
    } catch ( error ) {
        if ( generation === state.generation ) {
            $( "list-caption" ).textContent = "Unable to load sessions";
            $( "run-list" ).append( element( "div", "empty", `${error.message} — use Refresh to try again.` ) );
        }
    } finally { if ( generation === state.generation ) state.loading = false; }
}

function closeSession() {
    state.selected = null;
    state.viewKey = null;
    state.epoch++;
    clearTimeout( state.poll );
    history.replaceState( null, "", location.pathname );
    show( "chooser-view" );
    state.generation++;
    loadRuns( true );
}

function historyURL( id, cursor = "" ) {
    return `/api/runs/${encodeURIComponent( id )}/history?size=${preferences.history * 1024}${cursor}`;
}

function groupedTurns() {
    const groups = [];
    for ( const turn of state.turns ) {
        const last = groups[ groups.length - 1 ];
        if ( turn.block_id && last?.block_id === turn.block_id && turn.fragment === last.lastFragment + 1 ) {
            last.body += turn.body;
            last.lastFragment = turn.fragment;
            last.partial = last.firstFragment !== 0 || turn.fragment + 1 !== turn.fragments;
        } else groups.push( { ...turn, firstFragment: turn.fragment || 0, lastFragment: turn.fragment || 0, partial: turn.fragments ? turn.fragment !== 0 || turn.fragments !== 1 : false, key: `${turn.block_id || groups.length}:${turn.fragment || 0}` } );
    }
    const paired = new Set();
    for ( const call of groups ) {
        if ( call.kind !== "action" || !call.action_id || call.partial ) continue;
        const result = groups.find( item => item.kind === "action_result" && item.action_id === call.action_id && !item.partial && !paired.has( item ) );
        if ( result ) { call.result = result.body; paired.add( result ); }
    }
    return groups.filter( item => !paired.has( item ) );
}

function pendingVisible( pending ) {
    return state.viewKey === pending.key || state.selected && pending.runs.includes( state.selected );
}

function redrawHistory( forceBottom = false ) {
    const container = $( "history-scroll" );
    const bottom = container.scrollHeight - container.scrollTop - container.clientHeight < 120;
    const top = container.scrollTop;
    const previous = new Map( [ ...$( "history-cards" ).children ].map( card => [ card.dataset.key, { expanded: card.classList.contains( "expanded" ), open: !!card.querySelector( "details[open]" ) } ] ) );
    const turns = groupedTurns();
    for ( const pending of state.pending.filter( pendingVisible ) ) {
        const confirmed = turns.some( turn => turn.actor === "User" && turn.body === pending.prompt && !pending.baseline?.includes( turn.key ) && ( !turn.timestamp || Date.parse( turn.timestamp ) >= Date.parse( pending.timestamp ) - 5000 ) );
        if ( !confirmed ) {
            const card = { actor: "User", title: pending.status, timestamp: pending.timestamp, body: pending.prompt, key: pending.key };
            const index = turns.findIndex( turn => turn.timestamp && Date.parse( turn.timestamp ) >= Date.parse( pending.timestamp ) );
            if ( index >= 0 ) turns.splice( index, 0, card );
            else if ( pending.runs.includes( state.selected ) && state.selected !== pending.originalRun ) turns.unshift( card );
            else turns.push( card );
        }
        const liveTools = turns.some( turn => turn.tool && ( turn.timestamp ? Date.parse( turn.timestamp ) >= Date.parse( pending.timestamp ) : !pending.baseline?.includes( turn.key ) ) );
        if ( pending.output && !liveTools && pending.status === "Running" ) turns.push( { actor: "Agent", title: "CLI progress · waiting for tool history", body: pending.output, key: `${pending.key}-progress`, partial: true } );
    }
    $( "history-cards" ).replaceChildren( ...( turns.length ? turns.map( turnCard ) : [ element( "div", "turn-empty", "No conversation is available yet." ) ] ) );
    for ( const card of $( "history-cards" ).children ) {
        const saved = previous.get( card.dataset.key );
        if ( saved?.expanded ) {
            card.classList.add( "expanded" );
            const toggle = card.querySelector( ".card-expand" );
            if ( toggle ) { toggle.textContent = "Show less"; toggle.setAttribute( "aria-expanded", "true" ); }
        }
        if ( saved?.open && card.querySelector( "details" ) ) card.querySelector( "details" ).open = true;
        const failed = state.pending.find( item => item.key === card.dataset.key && item.status === "Failed" && item.rejected );
        if ( failed ) {
            const retry = element( "button", "card-expand", "Edit and retry" );
            retry.type = "button";
            retry.addEventListener( "click", async () => {
                if ( failed.originalRun ) await selectRun( failed.originalRun );
                else show( "new-view" );
                $( failed.originalRun ? "session-prompt" : "new-prompt" ).value = failed.prompt;
                $( failed.originalRun ? "session-cwd" : "new-cwd" ).value = failed.cwd;
                $( failed.originalRun ? "session-prompt" : "new-prompt" ).focus();
            } );
            card.append( retry );
        }
    }
    container.scrollTop = forceBottom || bottom ? container.scrollHeight : top;
}

async function loadHistory( id, epoch = state.epoch ) {
    const data = await api( historyURL( id ) );
    if ( state.selected !== id || epoch !== state.epoch ) return;
    state.turns = data.turns;
    state.historyStart = data.start;
    state.historyEnd = data.end;
    state.older = data.older;
    redrawHistory( true );
}

async function loadOlder() {
    if ( !state.selected || !state.older || state.busy ) return;
    state.busy = true;
    const id = state.selected;
    const epoch = state.epoch;
    const container = $( "history-scroll" );
    const height = container.scrollHeight;
    try {
        const data = await api( historyURL( id, `&before=${state.historyStart}` ) );
        if ( state.selected !== id || epoch !== state.epoch ) return;
        state.turns.unshift( ...data.turns );
        state.historyStart = data.start;
        state.older = data.older;
        redrawHistory();
        container.scrollTop += container.scrollHeight - height;
    } catch ( error ) { if ( epoch === state.epoch ) $( "session-note" ).textContent = error.message; }
    finally { state.busy = false; }
}

async function pollHistory( id, epoch = state.epoch ) {
    if ( state.selected !== id || document.hidden ) return;
    try {
        const data = await api( historyURL( id, `&after=${state.historyEnd}` ) );
        if ( state.selected !== id || epoch !== state.epoch ) return;
        if ( data.turns.length ) {
            state.turns.push( ...data.turns );
            state.historyEnd = data.end;
            redrawHistory();
        }
    } catch ( error ) {
        if ( state.selected === id && epoch === state.epoch ) {
            if ( error.status === 409 ) await loadHistory( id, epoch );
            else $( "session-note" ).textContent = error.message;
        }
    }
}

function updateHeader( run, refreshUsage = false ) {
    state.run = run;
    if ( refreshUsage ) {
        if ( run.cost?.credits != null || run.cost?.usd_approx != null || state.usageId !== run.id ) state.usage = run.cost;
        state.usageId = run.id;
        state.usageNote = run.cost?.credits == null && run.cost?.usd_approx == null ? " · usage pending" : "";
    }
    $( "session-source" ).textContent = `${run.location === "LOCAL" ? "LOCAL" : "CLOUD"} · ${run.source || "AGENT"}`;
    $( "session-title" ).textContent = run.title;
    const credits = typeof state.usage?.credits === "number" ? `${state.usage.credits.toFixed( 2 )} credits · ` : "";
    const usageLabel = state.usageId && state.usageId !== run.id ? "Last refreshed run cost" : "Run cost";
    $( "session-status" ).textContent = `${( run.state || "Unknown" ).toLowerCase().replaceAll( "_", " " )} · ${relativeAge( run.created_at )} · ${usageLabel}: ${credits}${dollars( state.usage?.usd_approx )}${state.usageNote}`;
    state.canSend = !!run.can_send && !activeStates.has( run.state );
    const pending = state.pending.find( item => pendingVisible( item ) && [ "Submitting", "Running" ].includes( item.status ) );
    $( "composer" ).classList.toggle( "hidden", !state.canSend || !!pending );
    $( "send" ).disabled = state.submitting || !!state.job;
    const shared = $( "shared-session" );
    shared.classList.toggle( "hidden", !run.session_link );
    if ( run.session_link ) shared.href = run.session_link;
    else shared.removeAttribute( "href" );
    $( "session-note" ).textContent = pending ? "Local agent is running. Minimize without stopping it." : state.canSend ? "Continue this local CLI conversation. Ctrl+Enter sends; Enter adds a newline." : "Read-only here. Active CLI runs must finish first; desktop steering uses shared-session edit access.";
}

async function fetchMetadata( id ) {
    try { return await api( `/api/runs/${encodeURIComponent( id )}/metadata` ); }
    catch ( error ) {
        if ( error.status !== 404 ) throw error;
        // Existing processes can serve updated assets before the backend is restarted.
        return api( `/api/runs/${encodeURIComponent( id )}` );
    }
}

async function refreshMetadata( id, refreshUsage = false, epoch = state.epoch ) {
    const run = await fetchMetadata( id );
    if ( state.selected === id && epoch === state.epoch ) updateHeader( run, refreshUsage );
    return run;
}

function schedulePolling( id, epoch ) {
    clearTimeout( state.poll );
    const schedule = async () => {
        if ( state.selected !== id || state.epoch !== epoch ) return;
        if ( !document.hidden ) {
            try {
                await pollHistory( id, epoch );
                const wasActive = activeStates.has( state.run?.state );
                const run = await refreshMetadata( id, false, epoch );
                if ( state.selected !== id || state.epoch !== epoch ) return;
                if ( wasActive && !activeStates.has( run.state ) && !state.job ) updateHeader( run, true );
                if ( !activeStates.has( run.state ) && !state.job ) return;
            } catch ( error ) { if ( epoch === state.epoch ) $( "session-note" ).textContent = error.message; }
        }
        if ( state.selected === id && state.epoch === epoch ) state.poll = setTimeout( schedule, 6000 );
    };
    state.poll = setTimeout( schedule, 6000 );
}

async function selectRun( id, refreshUsage = true ) {
    const priorId = state.selected;
    const priorUsage = state.usage;
    state.selected = id;
    state.viewKey = null;
    const epoch = ++state.epoch;
    history.replaceState( null, "", `?run=${encodeURIComponent( id )}` );
    clearTimeout( state.poll );
    state.canSend = false;
    state.turns = [];
    state.busy = false;
    state.usage = refreshUsage ? null : priorUsage;
    state.usageNote = refreshUsage ? "" : ( priorId !== id ? " · last refresh; awaiting configured cadence" : state.usageNote );
    show( "session-view" );
    $( "composer" ).classList.add( "hidden" );
    $( "session-title" ).textContent = "Loading session…";
    $( "session-status" ).textContent = "Fetching conversation…";
    redrawHistory( true );
    $( "shared-session" ).classList.add( "hidden" );
    $( "session-note" ).textContent = "Minimizing does not stop the session.";
    try {
        const run = await fetchMetadata( id );
        if ( state.selected !== id || state.epoch !== epoch ) return;
        updateHeader( run, refreshUsage );
        await loadHistory( id, epoch );
        if ( state.selected !== id || state.epoch !== epoch ) return;
        if ( activeStates.has( run.state ) || state.job ) schedulePolling( id, epoch );
    } catch ( error ) {
        if ( state.selected !== id || state.epoch !== epoch ) return;
        $( "session-note" ).textContent = error.message;
        redrawHistory();
    }
}
async function watchJob( job, pending ) {
    state.job = job.id;
    let attached = false;
    let usageAttempts = 0;
    while ( state.job === job.id ) {
        pending.output = job.output;
        if ( job.run_id && !pending.runs.includes( job.run_id ) ) pending.runs.push( job.run_id );
        if ( !pending.counted && job.conversation_id ) {
            countPrompt( job.conversation_id );
            pending.counted = true;
        }
        const visible = pendingVisible( pending ) && !$( "session-view" ).classList.contains( "hidden" );
        if ( visible ) {
            if ( job.run_id && !attached ) {
                attached = true;
                await selectRun( job.run_id, pending.refreshCost );
                if ( !pending.counted && state.selected === job.run_id && state.run?.conversation_id ) {
                    countPrompt( state.run.conversation_id );
                    pending.counted = true;
                }
            } else {
                redrawHistory();
                if ( state.run ) updateHeader( state.run );
            }
            if ( job.run_id && pending.refreshCost && usageAttempts < 4 ) {
                usageAttempts++;
                try {
                    const run = await refreshMetadata( job.run_id, true );
                    if ( state.selected === job.run_id && run.cost?.usd_approx == null ) {
                        state.usageNote = " · usage pending";
                        updateHeader( run );
                    } else usageAttempts = 4;
                } catch {
                    if ( state.selected === job.run_id && state.run ) {
                        state.usageNote = " · usage refresh failed; last known value";
                        updateHeader( state.run );
                    }
                }
            }
        }
        if ( job.state !== "running" ) break;
        await new Promise( resolve => setTimeout( resolve, 2500 ) );
        if ( state.job !== job.id ) return;
        try { job = await api( `/api/jobs/${job.id}` ); }
        catch ( error ) {
            pending.status = "Status unavailable";
            if ( pendingVisible( pending ) ) { redrawHistory(); $( "session-note" ).textContent = `${error.message} — reopen the session to check its status; do not resend automatically.`; }
            state.job = null;
            return;
        }
    }
    if ( state.job !== job.id ) return;
    state.job = null;
    pending.status = job.state === "completed" ? "Submitted" : "Failed";
    $( "send" ).disabled = false;
    if ( job.state === "completed" && job.run_id ) {
        state.generation++;
        if ( !$( "session-view" ).classList.contains( "hidden" ) && pendingVisible( pending ) ) {
            await selectRun( job.run_id, pending.refreshCost );
            if ( pending.refreshCost && state.selected === job.run_id && state.usage?.usd_approx == null ) {
                state.usageNote = " · usage pending; reopen to refresh";
                updateHeader( state.run );
            }
        } else if ( !$( "chooser-view" ).classList.contains( "hidden" ) ) loadRuns( true );
    } else if ( pendingVisible( pending ) ) {
        if ( state.run ) updateHeader( state.run );
        redrawHistory();
        $( "session-note" ).textContent = job.error || "The local agent did not complete.";
    }
}

$( "login-form" ).addEventListener( "submit", async ( event ) => {
    event.preventDefault();
    $( "login-error" ).textContent = "";
    try {
        await api( "/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify( { password: $( "password" ).value } ) } );
        $( "password" ).value = "";
        show( "chooser-view" );
        const requested = new URLSearchParams( location.search ).get( "run" );
        if ( requested && /^[0-9a-fA-F-]{36}$/.test( requested ) ) await selectRun( requested );
        else await loadRuns( true );
    } catch ( error ) { $( "login-error" ).textContent = error.message; }
} );

$( "logout" ).addEventListener( "click", async () => {
    try { await api( "/api/logout", { method: "POST" } ); } catch { /* The browser can still drop its current view. */ }
    state.job = null;
    state.selected = null;
    state.epoch++;
    state.pending = [];
    clearTimeout( state.poll );
    markdownCache.clear();
    $( "history-cards" ).replaceChildren();
    show( "login-view" );
} );

$( "refresh" ).addEventListener( "click", () => { state.generation++; loadRuns( true ); } );
$( "load-more" ).addEventListener( "click", () => loadRuns() );
$( "minimize" ).addEventListener( "click", closeSession );
$( "new-back" ).addEventListener( "click", () => show( "chooser-view" ) );
$( "history-scroll" ).addEventListener( "scroll", () => { if ( $( "history-scroll" ).scrollTop < 90 ) loadOlder(); } );
async function submitTask( isNew ) {
    if ( state.submitting || state.job || !isNew && ( !state.canSend || !state.selected ) ) return;
    const form = $( isNew ? "new-form" : "composer" );
    if ( !form.reportValidity() ) return;
    const promptField = $( isNew ? "new-prompt" : "session-prompt" );
    const cwd = $( isNew ? "new-cwd" : "session-cwd" ).value;
    const prompt = promptField.value;
    if ( !prompt.trim() ) return;
    const originalRun = isNew ? null : state.selected;
    const conversation = isNew ? null : state.run?.conversation_id;
    const pending = { key: `pending-${crypto.randomUUID()}`, prompt, cwd, timestamp: new Date().toISOString(), status: "Submitting", runs: originalRun ? [ originalRun ] : [], originalRun, refreshCost: true, counted: !!conversation, baseline: isNew ? [] : groupedTurns().map( turn => turn.key ) };
    state.pending = state.pending.filter( item => !( item.rejected && item.originalRun === originalRun && item.prompt === prompt ) ).slice( -99 );
    state.pending.push( pending );
    state.submitting = true;
    const button = form.querySelector( "button[type=submit]" );
    button.disabled = true;
    $( "new-error" ).textContent = "";
    if ( isNew ) {
        clearTimeout( state.poll );
        state.epoch++;
        state.selected = null;
        state.viewKey = pending.key;
        state.turns = [];
        state.run = null;
        state.usage = null;
        state.usageId = null;
        history.replaceState( null, "", location.pathname );
        show( "session-view" );
        $( "session-source" ).textContent = "LOCAL · CLI";
        $( "session-title" ).textContent = "Starting local agent…";
        $( "session-status" ).textContent = "Submitting task…";
        $( "shared-session" ).classList.add( "hidden" );
        $( "composer" ).classList.add( "hidden" );
    }
    $( "session-note" ).textContent = "Submitting your task…";
    redrawHistory( true );
    try {
        const job = await api( isNew ? "/api/jobs" : `/api/runs/${encodeURIComponent( originalRun )}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify( { prompt, cwd } ) } );
        pending.status = "Running";
        pending.refreshCost = countPrompt( conversation || originalRun );
        rememberWorkdir( job.cwd || cwd );
        if ( promptField.value === prompt ) promptField.value = "";
        if ( pendingVisible( pending ) ) {
            $( "composer" ).classList.add( "hidden" );
            redrawHistory();
            $( "session-note" ).textContent = "Local agent is running. Minimize without stopping it.";
            if ( state.run && pending.refreshCost ) {
                state.usageNote = " · updating";
                updateHeader( state.run );
            }
        }
        watchJob( job, pending ).catch( error => {
            state.job = null;
            if ( pendingVisible( pending ) ) $( "session-note" ).textContent = error.message;
        } );
    } catch ( error ) {
        pending.status = "Failed";
        pending.rejected = true;
        if ( pendingVisible( pending ) ) {
            redrawHistory();
            $( "session-note" ).textContent = error.message;
        }
        $( "new-error" ).textContent = error.message;
    } finally {
        state.submitting = false;
        button.disabled = false;
        $( "send" ).disabled = !!state.job;
    }
}

$( "new-form" ).addEventListener( "submit", event => { event.preventDefault(); submitTask( true ); } );
$( "composer" ).addEventListener( "submit", event => { event.preventDefault(); submitTask( false ); } );
for ( const id of [ "new-prompt", "session-prompt" ] ) {
    $( id ).addEventListener( "keydown", event => {
        if ( event.key === "Enter" && event.ctrlKey && !event.isComposing ) {
            event.preventDefault();
            $( id ).form.requestSubmit();
        }
    } );
}
document.addEventListener( "preferences-changed", () => {
    for ( const card of $( "history-cards" ).children ) card.measure?.();
} );
for ( const filter of document.querySelectorAll( ".filter" ) ) {
    filter.addEventListener( "click", () => {
        state.scope = filter.dataset.scope;
        state.generation++;
        for ( const button of document.querySelectorAll( ".filter" ) ) {
            const active = button === filter;
            button.classList.toggle( "active", active );
            button.setAttribute( "aria-pressed", String( active ) );
        }
        loadRuns( true );
    } );
}

api( "/api/session" ).then( ( data ) => {
    show( data.authenticated ? "chooser-view" : "login-view" );
    if ( data.authenticated ) {
        const requested = new URLSearchParams( location.search ).get( "run" );
        if ( requested && /^[0-9a-fA-F-]{36}$/.test( requested ) ) selectRun( requested );
        else loadRuns( true );
    }
} ).catch( () => show( "login-view" ) );
