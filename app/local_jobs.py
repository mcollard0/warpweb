"""Launch local Oz CLI runs without putting user-provided prompts in argv."""

import asyncio
import json
import os
import re
import signal
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from app.warp_api import get_json


RUN_ID = re.compile( r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" );
JOB_ROOT = Path.home() / ".local" / "state" / "warpweb" / "jobs";
MAX_JOBS = 2;
TIMEOUT = 900;


@dataclass
class Job:
    id: str
    state: str = "running"
    run_id: str | None = None
    conversation_id: str | None = None
    error: str | None = None
    output: str = ""
    verified: bool = False
    cwd: str = ""
    prompt: str = ""

    def public( self ) -> dict:
        return { "id": self.id, "state": self.state, "run_id": self.run_id if self.verified else None, "conversation_id": self.conversation_id if self.verified else None, "output": self.output, "error": self.error, "cwd": self.cwd, "prompt": self.prompt };


jobs: dict[str, Job] = {};


def allowed_workdir( value: str | None ) -> Path:
    path = Path( value or str( Path.home() ) ).expanduser().resolve();
    roots = [ Path( item.strip() ).expanduser().resolve() for item in os.getenv( "WARPWEB_WORKDIR_ROOTS", str( Path.home() ) ).split( "," ) if item.strip() ];
    if not path.is_dir() or not any( path == root or root in path.parents for root in roots ):
        raise HTTPException( 400, "Choose an existing directory within an allowed local workspace" );
    return path;


def consume( process: subprocess.Popen, job: Job ) -> int:
    assert process.stdout is not None;
    while line := process.stdout.readline( 65536 ):
        try:
            event = json.loads( line );
        except ( ValueError, UnicodeDecodeError ):
            continue;
        if isinstance( event, dict ) and event.get( "type" ) == "system":
            if event.get( "event_type" ) == "run_started" and RUN_ID.fullmatch( str( event.get( "run_id" ) or "" ) ):
                job.run_id = event["run_id"];
            if event.get( "event_type" ) == "conversation_started" and RUN_ID.fullmatch( str( event.get( "conversation_id" ) or "" ) ):
                job.conversation_id = event["conversation_id"];
        if isinstance( event, dict ) and event.get( "type" ) == "agent" and isinstance( event.get( "text" ), str ):
            job.output = ( job.output + "\n" + event["text"] )[-16384:];
        if isinstance( event, dict ) and event.get( "type" ) == "tool_call":
            job.output = ( job.output + f"\nRunning tool: {str( event.get( 'tool' ) or 'unknown' )[:100]}\n" )[-16384:];
    return process.wait();


async def monitor( process: subprocess.Popen, job: Job, task_file: Path ) -> None:
    try:
        async def consume_and_verify() -> int:
            consumer = asyncio.create_task( asyncio.to_thread( consume, process, job ) );
            attempts = 0;
            try:
                while not consumer.done():
                    if job.run_id and not job.verified and attempts < 4:
                        attempts += 1;
                        try:
                            run = await get_json( f"/runs/{job.run_id}" );
                            if run.get( "source" ) == "CLI" and run.get( "execution_location" ) == "LOCAL":
                                job.verified = True;
                        except HTTPException:
                            pass;
                    await asyncio.wait( { consumer }, timeout=2 );
                return await consumer;
            finally:
                if not consumer.done():
                    consumer.cancel();
        result = await asyncio.wait_for( consume_and_verify(), timeout=TIMEOUT );
        if result != 0 or not job.run_id:
            job.error = "Local CLI run failed. Check the Warp desktop or service journal for details.";
        else:
            for attempt in range( 4 ):
                try:
                    run = await get_json( f"/runs/{job.run_id}" );
                    if run.get( "source" ) != "CLI" or run.get( "execution_location" ) != "LOCAL":
                        job.error = "CLI run was not verified as local; it was not connected to this client.";
                    else:
                        job.verified = True;
                        job.state = "completed";
                    break;
                except HTTPException:
                    if attempt == 3:
                        job.error = "Local CLI run could not be verified through Warp REST.";
                    else:
                        await asyncio.sleep( 2 );
    except TimeoutError:
        os.killpg( process.pid, signal.SIGTERM );
        job.error = "Local CLI run exceeded the 15-minute limit.";
    except Exception:
        job.error = "Local CLI run encountered an error.";
    finally:
        if job.state != "completed":
            job.state = "failed";
        task_file.unlink( missing_ok=True );
        task_file.parent.rmdir();


def start( prompt: str, cwd: str | None = None, conversation_id: str | None = None ) -> Job:
    directory = allowed_workdir( cwd );
    if not prompt.strip():
        raise HTTPException( 400, "Enter a task for the local agent" );
    if conversation_id and any( job.state == "running" and job.conversation_id == conversation_id for job in jobs.values() ):
        raise HTTPException( 409, "This conversation already has a local job running" );
    if len( jobs ) >= 100:
        for key in list( jobs ):
            if jobs[key].state != "running":
                del jobs[key];
                if len( jobs ) < 100:
                    break;
    if sum( job.state == "running" for job in jobs.values() ) >= MAX_JOBS:
        raise HTTPException( 429, "Two local agent jobs are already running" );
    job = Job( id=uuid.uuid4().hex, cwd=str( directory ), prompt=prompt, conversation_id=conversation_id );
    JOB_ROOT.mkdir( parents=True, exist_ok=True, mode=0o700 );
    folder = JOB_ROOT / job.id;
    folder.mkdir( mode=0o700 );
    task_file = folder / "task.txt";
    try:
        with task_file.open( "x", encoding="utf-8" ) as output:
            output.write( prompt );
        task_file.chmod( 0o600 );
        instruction = f"Read the complete task from {task_file} and carry it out. Do not modify that task file. Work in the requested local directory.";
        argv = [ os.getenv( "WARPWEB_CLI_PATH", "warp" ), "agent", "run", "--prompt", instruction, "--cwd", str( directory ), "--no-snapshot", "--output-format", "ndjson" ];
        if conversation_id:
            argv.extend( [ "--conversation", conversation_id ] );
        process = subprocess.Popen( argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=directory, start_new_session=True );
    except ( OSError, ValueError ) as error:
        task_file.unlink( missing_ok=True );
        folder.rmdir();
        raise HTTPException( 503, "Unable to start the local Warp CLI" ) from error;
    jobs[job.id] = job;
    asyncio.create_task( monitor( process, job, task_file ) );
    return job;
