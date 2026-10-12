"""Bounded CLI catalog tests; no real Warp processes or credentials."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from app import models


class CatalogProcess:
    def __init__( self, data, code=0 ):
        self.data = data; self.code = code; self.returncode = None; self.killed = False; self.stdout = self;
    async def read( self, size ):
        chunk, self.data = self.data[:size], self.data[size:];
        return chunk;
    async def wait( self ): self.returncode = self.code; return self.code;
    def kill( self ): self.killed = True; self.returncode = -9;


def test_catalog_validates_deduplicates_and_caches( monkeypatch ):
    calls = [];
    async def launch( *args, **kwargs ):
        calls.append( args );
        return CatalogProcess( json.dumps( [ { "id": "auto" }, { "id": "fixture-model" }, { "id": "auto" }, { "id": "--bad" }, { "id": 5 } ] ).encode() );
    monkeypatch.setattr( models, "cache", ( 0, [] ) );
    monkeypatch.setattr( models.asyncio, "create_subprocess_exec", launch );
    async def check():
        assert await models.catalog() == [ "auto", "fixture-model" ];
        assert await models.validate( "fixture-model" ) == "fixture-model";
        assert await models.validate( None ) is None;
        with pytest.raises( HTTPException ): await models.validate( "unknown" );
        assert len( calls ) == 1 and calls[0][1:] == ( "model", "list", "--output-format", "json" );
    asyncio.run( check() );


def test_hung_catalog_is_killed_and_reaped( monkeypatch ):
    process = CatalogProcess( b"" );
    async def hang( size ): await asyncio.Event().wait();
    process.read = hang;
    async def launch( *args, **kwargs ): return process;
    monkeypatch.setattr( models, "cache", ( 0, [] ) );
    monkeypatch.setattr( models, "DISCOVERY_TIMEOUT", .01 );
    monkeypatch.setattr( models.asyncio, "create_subprocess_exec", launch );
    async def check():
        with pytest.raises( HTTPException ): await models.catalog();
        assert process.killed and process.returncode is not None;
    asyncio.run( check() );


@pytest.mark.parametrize( "data,code", [ ( b"invalid", 0 ), ( b"{}", 0 ), ( b"[]", 0 ), ( b"x" * 131073, 0 ), ( b'[ { "id": "auto" } ]', 1 ) ] )
def test_bad_or_oversized_catalog_fails_closed_and_reaps_process( monkeypatch, data, code ):
    process = CatalogProcess( data, code );
    async def launch( *args, **kwargs ): return process;
    monkeypatch.setattr( models, "cache", ( 0, [] ) );
    monkeypatch.setattr( models.asyncio, "create_subprocess_exec", launch );
    async def check():
        with pytest.raises( HTTPException ) as error: await models.catalog();
        assert error.value.status_code == 503;
        assert process.returncode is not None;
    asyncio.run( check() );
