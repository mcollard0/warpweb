"use strict";

const $ = ( id ) => document.getElementById( id );
const state = { scope: "local", cursor: null, loading: false, generation: 0, authEpoch: 0, selected: null, canSend: false, historyStart: 0, historyEnd: 0, older: false, busy: false, poll: null, jobId: null, jobs: new Map(), watchers: new Set(), sending: new Set(), stopping: new Set(), run: null, epoch: 0, turns: [], pending: [], viewKey: null, usage: null, usageId: null, usageNote: "", navigation: [], drafts: new Map(), models: [] };
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
function sessionKey() {
    return state.run?.conversation_id || currentJob()?.conversation_id || state.jobId || state.viewKey || state.selected;
}
function saveDraft() {
    const key = sessionKey();
    if ( key && !$( "session-view" ).classList.contains( "hidden" ) ) state.drafts.set( key, { prompt: $( "session-prompt" ).value, cwd: $( "session-cwd" ).value, model: $( "session-model" ).value } );
}
function restoreDraft( job = currentJob() ) {
    setTurnSettings( false );
    const key = sessionKey();
    const draft = state.drafts.get( key ) || state.drafts.get( job?.id );
    $( "session-prompt" ).value = draft?.prompt || "";
    $( "session-cwd" ).value = draft?.cwd || job?.requested_cwd || job?.cwd || $( "new-cwd" ).value || "~";
    setModel( "session-model", draft?.model ?? job?.model ?? "" );
}
function setTurnSettings( expanded ) {
    $( "turn-settings" ).classList.toggle( "hidden", !expanded );
    $( "turn-settings-toggle" ).setAttribute( "aria-expanded", String( expanded ) );
    $( "turn-settings-toggle" ).firstElementChild.textContent = expanded ? "▴" : "▾";
}
function setModel( id, model ) {
    const select = $( id );
    if ( model && ![ ...select.options ].some( option => option.value === model ) ) {
        const option = element( "option", "", `${model} (previous selection)` ); option.value = model; select.append( option );
    }
    select.value = model;
}
async function loadModels() {
    const authEpoch = state.authEpoch;
    try {
        const data = await api( "/api/models" );
        if ( authEpoch !== state.authEpoch ) return;
        state.models = Array.isArray( data.models ) ? data.models.filter( model => typeof model === "string" ) : [];
        for ( const id of [ "new-model", "session-model" ] ) {
            const value = $( id ).value;
            $( id ).replaceChildren( element( "option", "", "Default" ) );
            $( id ).firstChild.value = "";
            for ( const model of state.models ) { const option = element( "option", "", model ); option.value = model; $( id ).append( option ); }
            setModel( id, value );
        }
        $( "model-note" ).textContent = data.available ? "Model applies to the next CLI turn." : "Model list unavailable; Default remains available.";
    } catch { $( "model-note" ).textContent = "Model list unavailable; Default remains available."; }
}
function registerSession( item, job = false ) {
    const conversation = item.conversation_id;
    const existing = state.navigation.find( entry => conversation && entry.conversation === conversation || job && entry.job === item.id || !job && entry.run === item.id || job && item.run_id && entry.run === item.run_id );
    const entry = existing || { run: null, job: null, conversation: null };
    if ( job ) { entry.job = item.id; if ( item.run_id ) entry.run = item.run_id; }
    else if ( !entry.run ) entry.run = item.id;
    if ( conversation ) entry.conversation = conversation;
    if ( !existing ) state.navigation.push( entry );
    state.navigation = state.navigation.slice( -100 );
    updateNavigation();
}
function navigationIndex() {
    return state.navigation.findIndex( entry => state.jobId && entry.job === state.jobId || state.selected && entry.run === state.selected || sessionKey() && entry.conversation === sessionKey() );
}
function updateNavigation() {
    const index = navigationIndex();
    $( "previous-session" ).disabled = index <= 0;
    $( "next-session" ).disabled = index < 0 || index >= state.navigation.length - 1;
    $( "navigation-note" ).textContent = index < 0 ? "Swipe between sessions" : `${index + 1} / ${state.navigation.length} · swipe`;
}
async function navigateSession( step ) {
    const entry = state.navigation[ navigationIndex() + step ];
    if ( !entry ) return;
    if ( entry.job ) {
        try { return await openOwnedJob( await api( `/api/jobs/${encodeURIComponent( entry.job )}` ) ); }
        catch ( error ) { if ( !entry.run ) { $( "session-note" ).textContent = error.message; return; } }
    }
    if ( entry.run ) await selectRun( entry.run );
}
function updateComposer() {
    const job = currentJob();
    const pending = state.pending.find( item => pendingVisible( item ) && item.status === "Submitting" );
    const active = ownedActive( job );
    const resume = !!job?.can_resume;
    state.canSend = resume || !!state.run?.can_send && !activeStates.has( state.run?.state ) && !active;
    $( "composer" ).classList.toggle( "hidden", !job && !state.canSend && !pending );
    $( "send" ).disabled = !state.canSend || active || !!pending || state.sending.has( sessionKey() );
    $( "session-new" ).classList.toggle( "hidden", !job || active || resume || state.canSend || !!pending );
    updateNavigation();
}
function ownedActive( job ) {
    return !!job && [ "running", "stopping" ].includes( job.state );
}
function rememberJob( job ) {
    const previous = state.jobs.get( job.id );
    if ( previous && !ownedActive( previous ) && ownedActive( job ) ) return previous;
    if ( previous?.state === "stopping" && job.state === "running" ) return previous;
    state.jobs.set( job.id, job );
    return job;
}
function currentJob() {
    const pending = [ ...state.pending ].reverse().find( item => item.jobId && pendingVisible( item ) && ownedActive( state.jobs.get( item.jobId ) ) );
    const id = pending?.jobId || state.jobId || ( state.run?.id === state.selected ? state.run.local_job?.id : null );
    return state.jobs.get( id ) || ( state.run?.local_job?.id === id ? state.run.local_job : null );
}
function updateStop() {
    const job = currentJob();
    const stopping = job?.state === "stopping" || state.stopping.has( job?.id );
    $( "stop-agent" ).classList.toggle( "hidden", !ownedActive( job ) || !job.can_cancel && !stopping );
    $( "stop-agent" ).disabled = stopping || !job?.can_cancel;
    $( "stop-label" ).textContent = stopping ? "Stopping…" : "Stop";
    $( "stop-agent" ).setAttribute( "aria-label", stopping ? "Stopping agent" : "Stop agent" );
}
function recoverPending( job ) {
    let pending = state.pending.find( item => item.jobId === job.id );
    if ( !pending ) {
        const status = { completed: "Submitted", cancelled: "Cancelled", failed: "Failed", stopping: "Stopping", running: "Running" }[job.state] || "Status unavailable";
        pending = { key: `owned-${job.id}`, jobId: job.id, prompt: job.prompt || "", cwd: job.requested_cwd || job.cwd || "", timestamp: new Date().toISOString(), status, runs: job.run_id ? [ job.run_id ] : state.selected ? [ state.selected ] : [], originalRun: state.selected, refreshCost: false, counted: true, baseline: [] };
        state.pending.push( pending );
    }
    return pending;
}
async function openOwnedJob( job ) {
    saveDraft();
    job = rememberJob( job );
    registerSession( job, true );
    if ( job.run_id ) {
        recoverPending( job );
        return selectRun( job.run_id, true, job );
    }
    clearTimeout( state.poll );
    state.selected = null; state.run = null; state.epoch++;
    const pending = recoverPending( job );
    state.viewKey = pending.key; state.turns = []; state.jobId = job.id;
    history.replaceState( null, "", `?job=${encodeURIComponent( job.id )}` );
    show( "session-view" );
    $( "shared-session" ).classList.add( "hidden" );
    $( "session-source" ).textContent = "LOCAL · CLI";
    $( "session-title" ).textContent = "Running local agent";
    $( "session-status" ).textContent = job.state;
    restoreDraft( job ); redrawHistory( true ); updateStop(); updateComposer();
    $( "session-note" ).textContent = ownedActive( job ) ? "Draft your next command now; Send enables after this turn exits." : job.can_resume ? "Continue the same conversation." : "Conversation identity unavailable. Start a new session with your next command.";
    if ( ownedActive( job ) ) watchJob( job, pending ).catch( error => { if ( pendingVisible( pending ) ) $( "session-note" ).textContent = error.message; } );
}
function ownedJobTile( job ) {
    const tile = element( "button", "run-card owned-job-card", "" );
    tile.type = "button";
    tile.append( element( "span", "eyebrow", `APP-OWNED · ${job.state.toUpperCase()}` ), element( "h2", "", "Local agent session" ), element( "p", "summary", job.prompt || "Reopen to inspect or stop this local job." ) );
    tile.addEventListener( "click", () => openOwnedJob( job ) );
    return tile;
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
        const [ data, owned ] = await Promise.all( [
            api( `/api/runs?${params}` ),
            reset && state.scope !== "cloud" ? api( "/api/jobs" ).catch( error => { if ( [ 404, 405 ].includes( error.status ) ) return { jobs: [] }; throw error; } ) : Promise.resolve( { jobs: [] } ),
        ] );
        if ( generation !== state.generation ) return;
        if ( reset ) state.navigation = [];
        for ( const job of owned.jobs ) { state.jobs.set( job.id, job ); registerSession( job, true ); }
        for ( const run of data.runs ) registerSession( run );
        for ( const job of owned.jobs ) $( "run-list" ).append( ownedJobTile( job ) );
        const ownedRuns = new Set( owned.jobs.map( job => job.run_id ).filter( Boolean ) );
        for ( const run of data.runs ) if ( !ownedRuns.has( run.id ) ) $( "run-list" ).append( runCard( run ) );
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
    saveDraft();
    state.selected = null;
    state.jobId = null;
    state.viewKey = null;
    state.epoch++;
    clearTimeout( state.poll );
    history.replaceState( null, "", location.pathname );
    show( "chooser-view" );
    updateStop();
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
        if ( pending.output && !liveTools && [ "Running", "Stopping" ].includes( pending.status ) ) turns.push( { actor: "Agent", title: "CLI progress · waiting for tool history", body: pending.output, key: `${pending.key}-progress`, partial: true } );
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
        updateComposer();
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
            redrawHistory(); updateComposer();
            updateStop();
        }
    } catch ( error ) {
        if ( state.selected === id && epoch === state.epoch ) {
            if ( error.status === 409 ) await loadHistory( id, epoch );
            else $( "session-note" ).textContent = error.message;
        }
    }
}

function updateHeader( run, refreshUsage = false ) {
    const selectedJob = state.jobs.get( state.jobId );
    const keepJob = selectedJob && state.pending.some( item => item.jobId === selectedJob.id && pendingVisible( item ) );
    state.run = run;
    if ( run.local_job ) {
        run.local_job = rememberJob( run.local_job );
        state.jobId = keepJob ? selectedJob.id : run.local_job.id;
        registerSession( run.local_job, true );
    }
    if ( keepJob ) registerSession( selectedJob, true );
    registerSession( run );
    if ( refreshUsage ) {
        if ( run.cost?.credits != null || run.cost?.usd_approx != null || state.usageId !== run.id ) state.usage = run.cost;
        state.usageId = run.id;
        state.usageNote = run.cost?.credits == null && run.cost?.usd_approx == null ? " · usage pending" : "";
    }
    $( "session-source" ).textContent = `${run.location === "LOCAL" ? "LOCAL" : "CLOUD"} · ${run.source || "AGENT"}`;
    $( "session-title" ).textContent = run.title;
    const credits = typeof state.usage?.credits === "number" ? `${state.usage.credits.toFixed( 2 )} credits · ` : "";
    const usageLabel = state.usageId && state.usageId !== run.id ? "Last refreshed run cost" : "Run cost";
    const local = currentJob();
    const status = local?.state === "cancelled" ? "cancelled locally" : local?.state === "stopping" ? "stopping locally" : ( run.state || "Unknown" ).toLowerCase().replaceAll( "_", " " );
    $( "session-status" ).textContent = `${status} · ${relativeAge( run.created_at )} · ${usageLabel}: ${credits}${dollars( state.usage?.usd_approx )}${state.usageNote}`;
    const pending = state.pending.find( item => pendingVisible( item ) && [ "Submitting", "Running", "Stopping" ].includes( item.status ) );
    updateComposer();
    const shared = $( "shared-session" );
    shared.classList.toggle( "hidden", !run.session_link );
    if ( run.session_link ) shared.href = run.session_link;
    else shared.removeAttribute( "href" );
    const directoryNote = local?.directory_pending ? ` Requested directory ${local.requested_cwd} is being located; launch directory is ${local.cwd}.` : "";
    $( "session-note" ).textContent = ( local?.error || ( local?.state === "stopping" ? "Stopping local agent. Draft your next command; Send enables after cleanup." : pending?.status === "Submitting" ? "Submitting your task…" : ownedActive( local ) ? "Draft your next command while this turn runs. Stop cancels it; X only minimizes." : state.canSend ? ( local?.state === "cancelled" ? "Stopped. Previous file changes were not undone. Send your next command to the same conversation." : "Continue this local CLI conversation. Ctrl+Enter sends; Enter adds a newline." ) : local ? "Conversation identity unavailable. Start a new session with your next command." : "Read-only here. Active external CLI runs must finish first; desktop steering uses shared-session edit access." ) ) + directoryNote;
    updateStop();
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
                const prompt = state.pending.find( item => pendingVisible( item ) && item.runs.includes( run.id ) );
                if ( wasActive && !activeStates.has( run.state ) && !ownedActive( currentJob() ) && ( !prompt || prompt.refreshCost ) ) updateHeader( run, true );
                if ( !activeStates.has( run.state ) && !ownedActive( currentJob() ) ) return;
            } catch ( error ) { if ( epoch === state.epoch ) $( "session-note" ).textContent = error.message; }
        }
        if ( state.selected === id && state.epoch === epoch ) state.poll = setTimeout( schedule, 6000 );
    };
    state.poll = setTimeout( schedule, 6000 );
}

async function selectRun( id, refreshUsage = true, owned = null ) {
    saveDraft();
    const priorId = state.selected;
    const priorUsage = state.usage;
    state.selected = id;
    state.jobId = owned?.id || null;
    if ( owned ) rememberJob( owned );
    state.viewKey = null;
    const epoch = ++state.epoch;
    history.replaceState( null, "", `?run=${encodeURIComponent( id )}` );
    clearTimeout( state.poll );
    state.canSend = false;
    state.run = null;
    state.turns = [];
    state.busy = false;
    state.usage = refreshUsage ? null : priorUsage;
    state.usageNote = refreshUsage ? "" : ( priorId !== id ? " · last refresh; awaiting configured cadence" : state.usageNote );
    show( "session-view" );
    updateStop();
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
        if ( ownedActive( currentJob() ) ) {
            const local = currentJob();
            watchJob( local, recoverPending( local ), local.run_id === id ).catch( error => { if ( state.selected === id && state.epoch === epoch ) $( "session-note" ).textContent = error.message; } );
        }
        restoreDraft();
        await loadHistory( id, epoch );
        if ( state.selected !== id || state.epoch !== epoch ) return;
        if ( ownedActive( run.local_job ) && !state.watchers.has( run.local_job.id ) ) {
            const pending = recoverPending( run.local_job );
            watchJob( run.local_job, pending, run.local_job.run_id === id ).catch( error => { if ( pendingVisible( pending ) ) $( "session-note" ).textContent = error.message; } );
        }
        if ( activeStates.has( run.state ) || ownedActive( currentJob() ) ) schedulePolling( id, epoch );
    } catch ( error ) {
        if ( state.selected !== id || state.epoch !== epoch ) return;
        $( "session-note" ).textContent = error.message;
        try {
            const owned = await api( "/api/jobs" );
            if ( state.selected !== id || state.epoch !== epoch ) return;
            const job = owned.jobs.find( item => item.run_id === id ) || currentJob();
            if ( job ) {
                rememberJob( job ); state.jobId = job.id;
                const pending = recoverPending( job );
                $( "session-title" ).textContent = "Running local agent · history unavailable";
                updateStop();
                restoreDraft( job ); updateComposer();
                if ( ownedActive( job ) ) watchJob( job, pending, true ).catch( failure => { if ( pendingVisible( pending ) ) $( "session-note" ).textContent = failure.message; } );
            }
        } catch { /* An old/unavailable backend cannot restore owned jobs. */ }
        redrawHistory();
    }
}
async function watchJob( job, pending, alreadyAttached = false ) {
    if ( state.watchers.has( job.id ) ) return;
    const authEpoch = state.authEpoch;
    state.watchers.add( job.id );
    pending.jobId = job.id;
    let attached = alreadyAttached;
    let usageAttempts = 0;
    try {
        while ( authEpoch === state.authEpoch && state.watchers.has( job.id ) ) {
            job = rememberJob( job );
            registerSession( job, true );
            pending.status = job.state === "stopping" ? "Stopping" : job.state === "cancelled" ? "Cancelled" : job.state === "completed" ? "Submitted" : job.state === "failed" ? "Failed" : "Running";
            pending.output = job.output;
            if ( job.run_id && !pending.runs.includes( job.run_id ) ) pending.runs.push( job.run_id );
            if ( !pending.counted && job.conversation_id ) {
                countPrompt( job.conversation_id );
                pending.counted = true;
            }
            const visible = pendingVisible( pending ) && !$( "session-view" ).classList.contains( "hidden" );
            if ( visible ) {
                state.jobId = job.id;
                if ( job.run_id && !attached ) {
                    attached = true;
                    await selectRun( job.run_id, pending.refreshCost, job );
                    if ( !pending.counted && state.selected === job.run_id && state.run?.conversation_id ) {
                        countPrompt( state.run.conversation_id );
                        pending.counted = true;
                    }
                } else {
                    redrawHistory();
                    if ( state.run ) updateHeader( state.run );
                    else { $( "session-status" ).textContent = job.state; updateStop(); updateComposer(); }
                }
                if ( job.directory_pending && pendingVisible( pending ) ) $( "session-note" ).textContent = `Requested directory ${job.requested_cwd} is being located; launch directory is ${job.cwd}. Draft your next command; Send enables after this turn exits.`;
                if ( job.state === "running" && job.run_id && pending.refreshCost && usageAttempts < 4 ) {
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
            if ( !ownedActive( job ) ) break;
            await new Promise( resolve => setTimeout( resolve, 2500 ) );
            if ( !state.watchers.has( job.id ) ) return;
            try { job = await api( `/api/jobs/${job.id}` ); }
            catch ( error ) {
                pending.status = "Status unavailable";
                if ( pendingVisible( pending ) ) { redrawHistory(); $( "session-note" ).textContent = `${error.message} — reopen the session to check its status; do not resend automatically.`; }
                return;
            }
        }
        if ( authEpoch !== state.authEpoch || !state.watchers.has( job.id ) ) return;
        pending.status = job.state === "completed" ? "Submitted" : job.state === "cancelled" ? "Cancelled" : "Failed";
        if ( job.state === "completed" && job.run_id ) {
            state.generation++;
            if ( !$( "session-view" ).classList.contains( "hidden" ) && pendingVisible( pending ) ) {
                await selectRun( job.run_id, pending.refreshCost, job );
                if ( pending.refreshCost && state.selected === job.run_id && state.usage?.usd_approx == null ) {
                    state.usageNote = " · usage pending; reopen to refresh";
                    updateHeader( state.run );
                }
            } else if ( !$( "chooser-view" ).classList.contains( "hidden" ) ) loadRuns( true );
        } else if ( pendingVisible( pending ) ) {
            if ( state.run ) updateHeader( state.run );
            redrawHistory();
            $( "session-note" ).textContent = job.error || ( job.can_resume ? "Turn exited. File changes already made were not undone. Send your next command to the same conversation." : "Turn exited. File changes already made were not undone. Conversation identity unavailable; start a new session with your next command." );
            if ( !state.run ) $( "session-status" ).textContent = job.state === "cancelled" ? "cancelled locally" : job.state;
            updateStop(); updateComposer();
            if ( job.state === "cancelled" && pending.refreshCost && job.run_id && state.selected === job.run_id ) {
                const epoch = state.epoch;
                refreshMetadata( job.run_id, true, epoch ).catch( () => {
                    if ( state.epoch === epoch && state.selected === job.run_id && state.run ) {
                        state.usageNote = " · usage refresh failed; last known value";
                        updateHeader( state.run );
                    }
                } );
            }
        }
    } finally { if ( authEpoch === state.authEpoch ) state.watchers.delete( job.id ); }
}

$( "login-form" ).addEventListener( "submit", async ( event ) => {
    event.preventDefault();
    $( "login-error" ).textContent = "";
    try {
        await api( "/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify( { password: $( "password" ).value } ) } );
        state.authEpoch++;
        $( "password" ).value = "";
        show( "chooser-view" );
        await restoreSelection();
    } catch ( error ) { $( "login-error" ).textContent = error.message; }
} );

$( "logout" ).addEventListener( "click", async () => {
    state.authEpoch++; state.generation++;
    try { await api( "/api/logout", { method: "POST" } ); } catch { /* The browser can still drop its current view. */ }
    state.watchers.clear(); state.jobs.clear(); state.drafts.clear(); state.sending.clear(); state.navigation = []; state.jobId = null;
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
$( "turn-settings-toggle" ).addEventListener( "click", () => setTurnSettings( $( "turn-settings-toggle" ).getAttribute( "aria-expanded" ) !== "true" ) );
$( "session-cwd" ).addEventListener( "invalid", () => setTurnSettings( true ) );
$( "previous-session" ).addEventListener( "click", () => navigateSession( -1 ) );
$( "next-session" ).addEventListener( "click", () => navigateSession( 1 ) );
let swipe = null;
$( "history-scroll" ).addEventListener( "touchstart", event => {
    swipe = null;
    if ( event.touches.length !== 1 || event.target.closest( "input, textarea, select, button, a, details" ) ) return;
    for ( let node = event.target; node && node !== $( "history-scroll" ); node = node.parentElement ) {
        if ( node.scrollWidth > node.clientWidth + 4 ) return;
    }
    const touch = event.touches[0];
    swipe = { x: touch.clientX, y: touch.clientY, time: Date.now() };
}, { passive: true } );
$( "history-scroll" ).addEventListener( "touchend", event => {
    const start = swipe; swipe = null;
    if ( !start || event.changedTouches.length !== 1 || event.touches.length || Date.now() - start.time > 800 ) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - start.x, dy = touch.clientY - start.y;
    if ( Math.abs( dx ) >= 65 && Math.abs( dx ) > Math.abs( dy ) * 1.7 ) navigateSession( dx < 0 ? 1 : -1 );
}, { passive: true } );
$( "history-scroll" ).addEventListener( "touchcancel", () => { swipe = null; } );
$( "stop-agent" ).addEventListener( "click", async () => {
    const job = currentJob();
    if ( !job?.can_cancel || state.stopping.has( job.id ) ) return;
    const id = job.id;
    const authEpoch = state.authEpoch;
    state.stopping.add( id ); updateStop();
    try {
        const response = await api( `/api/jobs/${encodeURIComponent( id )}/cancel`, { method: "POST" } );
        if ( authEpoch !== state.authEpoch ) return;
        const stopped = rememberJob( response );
        const pending = state.pending.find( item => item.jobId === id );
        if ( pending ) pending.status = stopped.state === "cancelled" ? "Cancelled" : stopped.state === "stopping" ? "Stopping" : pending.status;
        if ( state.run?.local_job?.id === id ) state.run.local_job = stopped;
        if ( currentJob()?.id === id ) {
            if ( state.run ) updateHeader( state.run );
            else $( "session-note" ).textContent = "Stopping local agent and its process group…";
            redrawHistory();
        }
    } catch ( error ) {
        if ( currentJob()?.id === id ) $( "session-note" ).textContent = `${error.message} — refresh to check whether the agent has already exited.`;
    } finally { state.stopping.delete( id ); updateStop(); }
} );
$( "new-back" ).addEventListener( "click", () => show( "chooser-view" ) );
$( "history-scroll" ).addEventListener( "scroll", () => { if ( $( "history-scroll" ).scrollTop < 90 ) loadOlder(); } );
async function submitTask( isNew, restartNew = false ) {
    const authEpoch = state.authEpoch;
    const sourceKey = isNew ? "new" : sessionKey();
    const owned = isNew ? null : currentJob();
    if ( state.sending.has( sourceKey ) || !isNew && ( ownedActive( owned ) || !restartNew && !state.canSend ) ) return;
    const form = $( isNew ? "new-form" : "composer" );
    if ( !form.reportValidity() ) return;
    const promptField = $( isNew ? "new-prompt" : "session-prompt" );
    const cwd = $( isNew ? "new-cwd" : "session-cwd" ).value;
    const model = $( isNew ? "new-model" : "session-model" ).value || null;
    const prompt = promptField.value;
    if ( !prompt.trim() ) return;
    const newSession = isNew || restartNew;
    const originalRun = newSession ? null : state.selected;
    const conversation = newSession ? null : state.run?.conversation_id || owned?.conversation_id;
    const pending = { key: `pending-${crypto.randomUUID()}`, prompt, cwd, timestamp: new Date().toISOString(), status: "Submitting", runs: originalRun ? [ originalRun ] : [], originalRun, refreshCost: true, counted: !!conversation, baseline: isNew ? [] : groupedTurns().map( turn => turn.key ) };
    state.pending = state.pending.filter( item => !( item.rejected && item.originalRun === originalRun && item.prompt === prompt ) ).slice( -99 );
    state.pending.push( pending );
    state.sending.add( sourceKey );
    saveDraft();
    const button = form.querySelector( "button[type=submit]" );
    button.disabled = true;
    $( "new-error" ).textContent = "";
    if ( newSession ) {
        clearTimeout( state.poll );
        state.epoch++;
        state.selected = null;
        state.jobId = null;
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
        $( "session-prompt" ).value = ""; $( "session-cwd" ).value = cwd; setModel( "session-model", model || "" );
    }
    $( "session-note" ).textContent = "Submitting your task…";
    redrawHistory( true );
    try {
        const path = newSession ? "/api/jobs" : owned?.can_resume ? `/api/jobs/${encodeURIComponent( owned.id )}/messages` : `/api/runs/${encodeURIComponent( originalRun )}/messages`;
        const job = await api( path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify( { prompt, cwd, model } ) } );
        if ( authEpoch !== state.authEpoch ) return;
        pending.status = "Running";
        pending.jobId = job.id; rememberJob( job ); registerSession( job, true );
        pending.refreshCost = countPrompt( conversation || originalRun );
        if ( !job.directory_pending ) rememberWorkdir( job.cwd || cwd, false );
        const draft = state.drafts.get( sourceKey );
        if ( draft?.prompt === prompt ) draft.prompt = "";
        if ( pendingVisible( pending ) ) {
            state.jobId = job.id;
            if ( !job.run_id && !state.selected ) history.replaceState( null, "", `?job=${encodeURIComponent( job.id )}` );
            if ( promptField.value === prompt ) promptField.value = "";
            $( "session-cwd" ).value = job.requested_cwd || job.cwd || cwd;
            setModel( "session-model", model || "" );
            saveDraft(); updateComposer();
            redrawHistory();
            $( "session-note" ).textContent = "Local agent is running. Minimize without stopping it.";
            if ( state.run && pending.refreshCost ) {
                state.usageNote = " · updating";
                updateHeader( state.run );
            }
        }
        watchJob( job, pending ).catch( error => {
            if ( pendingVisible( pending ) ) $( "session-note" ).textContent = error.message;
        } );
    } catch ( error ) {
        if ( authEpoch !== state.authEpoch ) return;
        pending.status = "Failed";
        pending.rejected = true;
        if ( pendingVisible( pending ) ) {
            redrawHistory();
            $( "session-note" ).textContent = error.message;
        }
        $( "new-error" ).textContent = error.message;
    } finally {
        if ( authEpoch === state.authEpoch ) {
            state.sending.delete( sourceKey );
            if ( isNew || pendingVisible( pending ) ) button.disabled = false;
            updateComposer();
        }
    }
}

$( "new-form" ).addEventListener( "submit", event => { event.preventDefault(); submitTask( true ); } );
$( "composer" ).addEventListener( "submit", event => { event.preventDefault(); submitTask( false ); } );
$( "session-new" ).addEventListener( "click", () => submitTask( false, true ) );
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

async function restoreSelection() {
    const epoch = state.epoch, authEpoch = state.authEpoch;
    const params = new URLSearchParams( location.search );
    const requested = params.get( "run" ), job = params.get( "job" );
    loadModels();
    await loadRuns( true );
    if ( state.epoch !== epoch || state.authEpoch !== authEpoch ) return;
    if ( requested && /^[0-9a-fA-F-]{36}$/.test( requested ) ) await selectRun( requested );
    else if ( job && /^[0-9a-z-]{1,64}$/i.test( job ) ) {
        try { await openOwnedJob( await api( `/api/jobs/${encodeURIComponent( job )}` ) ); }
        catch ( error ) { $( "list-caption" ).textContent = `${error.message}. Choose a session or start a new one.`; }
    }
}
api( "/api/session" ).then( ( data ) => {
    show( data.authenticated ? "chooser-view" : "login-view" );
    if ( data.authenticated ) {
        restoreSelection();
    }
} ).catch( () => show( "login-view" ) );
