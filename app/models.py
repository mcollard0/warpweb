"""Discover model IDs using the installed CLI, without starting an agent."""

import asyncio
import json
import os
import re
import time

from fastapi import HTTPException


MODEL_ID = re.compile( r"^[a-zA-Z0-9][a-zA-Z0-9._\[\]-]{0,127}$" );
cache: tuple[float, list[str]] = ( 0, [] );
DISCOVERY_TIMEOUT = 10;


async def catalog() -> list[str]:
    global cache;
    if cache[0] > time.monotonic():
        return list( cache[1] );
    process = None;
    try:
        process = await asyncio.create_subprocess_exec( os.getenv( "WARPWEB_CLI_PATH", "warp" ), "model", "list", "--output-format", "json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL );
        async def read():
            output = bytearray();
            while chunk := await process.stdout.read( 4096 ):
                output.extend( chunk );
                if len( output ) > 131072:
                    raise ValueError( "Model catalog too large" );
            if await process.wait() != 0:
                raise ValueError( "Model discovery failed" );
            entries = json.loads( output );
            if not isinstance( entries, list ) or len( entries ) > 1000:
                raise ValueError( "Invalid model catalog" );
            result = sorted( { item["id"] for item in entries if isinstance( item, dict ) and isinstance( item.get( "id" ), str ) and MODEL_ID.fullmatch( item["id"] ) } );
            if not result:
                raise ValueError( "Empty model catalog" );
            return result;
        result = await asyncio.wait_for( read(), timeout=DISCOVERY_TIMEOUT );
        cache = ( time.monotonic() + 300, result );
        return list( result );
    except ( OSError, ValueError, TimeoutError ):
        raise HTTPException( 503, "Model list unavailable; use Default or retry later" ) from None;
    finally:
        if process is not None and process.returncode is None:
            try: process.kill();
            except ProcessLookupError: pass;
            await process.wait();


async def validate( model: str | None ) -> str | None:
    if not model:
        return None;
    if not MODEL_ID.fullmatch( model ) or model not in await catalog():
        raise HTTPException( 400, "Choose a model from the available model list" );
    return model;
