"use strict";

const preferenceDefaults = { mru: 20, history: 8, lines: 12, cost: 1 };
const preferenceBounds = { mru: [ 1, 100 ], history: [ 4, 16 ], lines: [ 4, 40 ], cost: [ 1, 100 ] };
const preferenceKey = "warpweb.preferences.v1";
let preferences = { ...preferenceDefaults };
let recentDirectories = [];
let promptCounters = {};

function loadPreferences() {
    try {
        const stored = JSON.parse( localStorage.getItem( preferenceKey ) || "{}" );
        for ( const key of Object.keys( preferenceDefaults ) ) {
            const value = stored.options?.[ key ];
            if ( Number.isInteger( value ) && value >= preferenceBounds[ key ][ 0 ] && value <= preferenceBounds[ key ][ 1 ] ) preferences[ key ] = value;
        }
        recentDirectories = Array.isArray( stored.directories ) ? [ ...new Set( stored.directories.filter( value => typeof value === "string" && value.length <= 1024 && value.trim() ) ) ].slice( 0, preferences.mru ) : [];
        if ( stored.counters && typeof stored.counters === "object" ) {
            for ( const [ key, value ] of Object.entries( stored.counters ).slice( -100 ) ) {
                if ( /^[0-9a-f-]{36}$/i.test( key ) && Number.isSafeInteger( value ) && value >= 0 ) promptCounters[ key ] = value;
            }
        }
    } catch { /* Invalid or unavailable storage uses the defaults. */ }
}

function savePreferences() {
    try { localStorage.setItem( preferenceKey, JSON.stringify( { options: preferences, directories: recentDirectories, counters: promptCounters } ) ); }
    catch { document.getElementById( "options-note" ).textContent = "Storage unavailable; options apply for this page only."; }
}

function updatePreferencesUI() {
    for ( const key of Object.keys( preferenceDefaults ) ) document.getElementById( `option-${key}` ).value = preferences[ key ];
    document.documentElement.style.setProperty( "--preview-height", `${preferences.lines * 1.6}em` );
    const list = document.getElementById( "recent-directories" );
    list.replaceChildren();
    for ( const directory of recentDirectories ) {
        const option = document.createElement( "option" );
        option.value = directory;
        list.append( option );
    }
}

function rememberWorkdir( directory, updateInputs = true ) {
    if ( !directory ) return;
    recentDirectories = [ directory, ...recentDirectories.filter( value => value !== directory ) ].slice( 0, preferences.mru );
    if ( updateInputs ) for ( const id of [ "new-cwd", "session-cwd" ] ) document.getElementById( id ).value = directory;
    savePreferences();
    updatePreferencesUI();
}

function countPrompt( conversation ) {
    if ( !conversation ) return true;
    promptCounters[ conversation ] = ( promptCounters[ conversation ] || 0 ) + 1;
    promptCounters = Object.fromEntries( Object.entries( promptCounters ).slice( -100 ) );
    savePreferences();
    return promptCounters[ conversation ] % preferences.cost === 0;
}

document.addEventListener( "DOMContentLoaded", () => {
    loadPreferences();
    updatePreferencesUI();
    if ( recentDirectories.length ) for ( const id of [ "new-cwd", "session-cwd" ] ) document.getElementById( id ).value = recentDirectories[ 0 ];
    document.getElementById( "options-toggle" ).addEventListener( "click", () => {
        const hidden = document.getElementById( "options-card" ).classList.toggle( "hidden" );
        document.getElementById( "options-toggle" ).setAttribute( "aria-expanded", String( !hidden ) );
    } );
    for ( const key of Object.keys( preferenceDefaults ) ) {
        document.getElementById( `option-${key}` ).addEventListener( "change", ( event ) => {
            const value = Number( event.target.value );
            if ( Number.isInteger( value ) && value >= preferenceBounds[ key ][ 0 ] && value <= preferenceBounds[ key ][ 1 ] ) preferences[ key ] = value;
            recentDirectories = recentDirectories.slice( 0, preferences.mru );
            savePreferences();
            updatePreferencesUI();
            document.dispatchEvent( new Event( "preferences-changed" ) );
        } );
    }
    document.getElementById( "options-reset" ).addEventListener( "click", () => {
        preferences = { ...preferenceDefaults };
        recentDirectories = recentDirectories.slice( 0, preferences.mru );
        savePreferences(); updatePreferencesUI();
        document.dispatchEvent( new Event( "preferences-changed" ) );
    } );
    document.getElementById( "mru-clear" ).addEventListener( "click", () => { recentDirectories = []; savePreferences(); updatePreferencesUI(); } );
} );
