"""Launch local Oz CLI runs without putting user-provided prompts in argv."""

import asyncio
import json
import os
import re
import signal
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from app.warp_api import get_json


RUN_ID = re.compile( r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" );
JOB_ROOT = Path.home() / ".local" / "state" / "warpweb" / "jobs";
MAX_JOBS = 2;
TIMEOUT = 900;
STOP_GRACE = 5;
ACTIVE_STATES = { "running", "stopping" };


@dataclass
class Job:
    id: str
    state: str = "running"
    run_id: str | None = None
    conversation_id: str | None = None
    error: str | None = None
    output: str = ""
    verified: bool = False
    conversation_verified: bool = False
    cwd: str = ""
    prompt: str = ""
    requested_cwd: str = ""
    directory_pending: bool = False
    model: str | None = None
    process: subprocess.Popen | None = field( default=None, repr=False )
    stop_task: asyncio.Task | None = field( default=None, repr=False )
    cancel_requested: bool = False
    cancel_event: asyncio.Event = field( default_factory=asyncio.Event, repr=False )

    def public( self ) -> dict:
        return { "id": self.id, "state": self.state, "run_id": self.run_id if self.verified else None, "conversation_id": self.conversation_id if self.verified or self.conversation_verified else None, "output": self.output, "error": self.error, "cwd": self.cwd, "requested_cwd": self.requested_cwd or self.cwd, "directory_pending": self.directory_pending, "model": self.model, "prompt": self.prompt, "can_cancel": self.state == "running" and self.process is not None, "can_resume": resumable( self ) };


jobs: dict[str, Job] = {};
def resumable( job: Job ) -> bool:
    return ( job.verified or job.conversation_verified ) and bool( RUN_ID.fullmatch( job.conversation_id or "" ) ) and job.state in { "completed", "cancelled", "failed" } and job.process is None and not any( other.state in ACTIVE_STATES and other.conversation_id == job.conversation_id for other in jobs.values() );


def for_run( run_id: str, conversation_id: str | None = None ) -> dict | None:
    for job in reversed( list( jobs.values() ) ):
        if conversation_id and job.conversation_id == conversation_id and job.state in ACTIVE_STATES:
            return job.public();
        if ( job.verified and job.run_id == run_id ) or ( not job.verified and job.state in ACTIVE_STATES and conversation_id and job.conversation_id == conversation_id ):
            return job.public();
    return None;


def group_alive( process: subprocess.Popen ) -> bool:
    try:
        os.killpg( process.pid, 0 );
    except ProcessLookupError:
        return False;
    if not Path( "/proc" ).is_dir():
        return True;
    for entry in Path( "/proc" ).iterdir():
        if not entry.name.isdecimal(): continue;
        try:
            fields = ( entry / "stat" ).read_text().rpartition( ")" )[2].split();
            if len( fields ) > 2 and int( fields[2] ) == process.pid and fields[0] not in ( "Z", "X" ):
                return True;
        except FileNotFoundError:
            continue;
        except ( OSError, ValueError ):
            return True;
    return False;


async def terminate_group( process: subprocess.Popen, term_sent: bool = False ) -> None:
    if not term_sent:
        try: os.killpg( process.pid, signal.SIGTERM );
        except ProcessLookupError: pass;
    deadline = asyncio.get_running_loop().time() + STOP_GRACE;
    while group_alive( process ) and asyncio.get_running_loop().time() < deadline:
        process.poll();
        await asyncio.sleep( .1 );
    if group_alive( process ):
        try: os.killpg( process.pid, signal.SIGKILL );
        except ProcessLookupError: pass;
    deadline = asyncio.get_running_loop().time() + 2;
    while group_alive( process ):
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError( "Process-group termination could not be confirmed." );
        process.poll();
        await asyncio.sleep( .02 );
    await asyncio.wait_for( asyncio.to_thread( process.wait ), timeout=2 );


def cancel( job_id: str ) -> Job:
    job = jobs.get( job_id );
    if not job:
        raise HTTPException( 404, "Local job not found" );
    if job.state not in ACTIVE_STATES or job.cancel_requested:
        return job;
    process = job.process;
    if process is None:
        raise HTTPException( 409, "This job has no process owned by this service" );
    if process.poll() is not None:
        raise HTTPException( 409, "The local agent has already exited; refresh its status" );
    try:
        os.killpg( process.pid, signal.SIGTERM );
    except ProcessLookupError:
        raise HTTPException( 409, "The local agent has already exited; refresh its status" ) from None;
    except OSError:
        raise HTTPException( 503, "Unable to signal the owned local agent" ) from None;
    job.cancel_requested = True;
    job.cancel_event.set();
    job.state = "stopping";
    job.stop_task = asyncio.create_task( terminate_group( process, term_sent=True ) );
    return job;


async def verify_run( job: Job ) -> dict | None:
    request = asyncio.create_task( get_json( f"/runs/{job.run_id}" ) );
    cancelled = asyncio.create_task( job.cancel_event.wait() );
    try:
        done, _ = await asyncio.wait( { request, cancelled }, return_when=asyncio.FIRST_COMPLETED );
        return None if cancelled in done else await request;
    finally:
        request.cancel(); cancelled.cancel();
        await asyncio.gather( request, cancelled, return_exceptions=True );


def allowed_workdir( value: str | None ) -> Path:
    path = Path( value or str( Path.home() ) ).expanduser().resolve();
    roots = [ Path( item.strip() ).expanduser().resolve() for item in os.getenv( "WARPWEB_WORKDIR_ROOTS", str( Path.home() ) ).split( "," ) if item.strip() ];
    if not path.is_dir() or not any( path == root or root in path.parents for root in roots ):
        raise HTTPException( 400, "Choose an existing directory within an allowed local workspace" );
    return path;


def launch_workdir( value: str | None ) -> tuple[Path, Path, list[Path]]:
    try:
        if value and "\0" in value: raise ValueError( "Invalid directory" );
        lexical = Path( value or str( Path.home() ) ).expanduser().absolute();
        requested = lexical.resolve();
    except ( OSError, RuntimeError, ValueError ):
        raise HTTPException( 400, "Enter a valid local working directory" ) from None;
    roots = [ Path( item.strip() ).expanduser().resolve() for item in os.getenv( "WARPWEB_WORKDIR_ROOTS", str( Path.home() ) ).split( "," ) if item.strip() ];
    roots = [ root for root in roots if root.is_dir() ];
    allowed = [ root for root in roots if requested == root or root in requested.parents ];
    if requested.exists() and ( not allowed or not requested.is_dir() ):
        raise HTTPException( 400, "Choose a directory within an allowed local workspace" );
    if not allowed:
        if ".." in lexical.parts or any( lexical == root or root in lexical.parents for root in roots ):
            raise HTTPException( 400, "Choose a directory within an allowed local workspace" );
        if not roots:
            raise HTTPException( 400, "No existing allowed workspace is available for directory recovery" );
        return roots[0], requested, roots;
    directory = requested;
    while not directory.exists():
        directory = directory.parent;
    if not directory.is_dir() or not any( directory == root or root in directory.parents for root in allowed ):
        raise HTTPException( 400, "No existing allowed workspace is available for directory recovery" );
    return directory, requested, roots;


def accept_identity( job: Job, run: dict ) -> bool:
    if run.get( "source" ) != "CLI" or run.get( "execution_location" ) != "LOCAL":
        return False;
    conversation = run.get( "conversation_id" );
    if conversation is not None:
        if not isinstance( conversation, str ) or not RUN_ID.fullmatch( conversation ) or job.conversation_id and job.conversation_id != conversation:
            return False;
        job.conversation_id = conversation;
        job.conversation_verified = True;
    job.verified = True;
    return True;


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
            if event.get( "event_type" ) == "conversation_started" and RUN_ID.fullmatch( str( event.get( "conversation_id" ) or "" ) ) and ( not job.conversation_verified or job.conversation_id == event["conversation_id"] ):
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
                    if job.run_id and not job.verified and not job.cancel_requested and attempts < 4:
                        attempts += 1;
                        try:
                            run = await verify_run( job );
                            if run:
                                accept_identity( job, run );
                        except HTTPException:
                            pass;
                    await asyncio.wait( { consumer }, timeout=2 );
                return await consumer;
            finally:
                if not consumer.done():
                    consumer.cancel();
        result = await asyncio.wait_for( consume_and_verify(), timeout=TIMEOUT );
        if job.cancel_requested:
            pass;
        elif result != 0 or not job.run_id:
            job.error = "Local CLI run failed. Check the Warp desktop or service journal for details.";
        else:
            for attempt in range( 4 ):
                try:
                    run = await verify_run( job );
                    if run is None or job.cancel_requested:
                        break;
                    if not accept_identity( job, run ):
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
        job.error = "Local CLI run exceeded the 15-minute limit.";
    except asyncio.CancelledError:
        job.error = "Local CLI monitor was stopped.";
        raise;
    except Exception:
        job.error = "Local CLI run encountered an error.";
    finally:
        cleanup_confirmed = True;
        if job.cancel_requested or job.state != "completed":
            if job.stop_task is None:
                job.stop_task = asyncio.create_task( terminate_group( process ) );
            try:
                await asyncio.shield( job.stop_task );
            except ( OSError, TimeoutError ):
                job.state = "stopping";
                job.error = "Unable to confirm local process termination; inspect the service journal.";
                cleanup_confirmed = False;
        if cleanup_confirmed:
            terminal_state = "cancelled" if job.cancel_requested else "completed" if job.state == "completed" else "failed";
            if job.cancel_requested: job.error = None;
            job.process = None;
            job.stop_task = None;
            if job.run_id and ( not job.verified or not job.conversation_id ):
                job.state = "stopping";
                for attempt in range( 3 ):
                    try:
                        run = await asyncio.wait_for( get_json( f"/runs/{job.run_id}" ), timeout=.75 );
                        if accept_identity( job, run ) and job.conversation_id:
                            break;
                    except ( HTTPException, TimeoutError ):
                        pass;
                    if attempt < 2: await asyncio.sleep( .1 );
            try:
                task_file.unlink( missing_ok=True );
                task_file.parent.rmdir();
            except OSError:
                job.error = "The agent exited, but private task cleanup could not be completed.";
            job.state = terminal_state;


def start( prompt: str, cwd: str | None = None, conversation_id: str | None = None, model: str | None = None ) -> Job:
    directory, requested, roots = launch_workdir( cwd );
    if conversation_id and not RUN_ID.fullmatch( conversation_id ):
        raise HTTPException( 400, "Invalid conversation ID" );
    if not prompt.strip():
        raise HTTPException( 400, "Enter a task for the local agent" );
    if conversation_id and any( job.state in ACTIVE_STATES and job.conversation_id == conversation_id for job in jobs.values() ):
        raise HTTPException( 409, "This conversation already has a local job running" );
    if len( jobs ) >= 100:
        for key in list( jobs ):
            if jobs[key].state not in ACTIVE_STATES:
                del jobs[key];
                if len( jobs ) < 100:
                    break;
    if sum( job.state in ACTIVE_STATES for job in jobs.values() ) >= MAX_JOBS:
        raise HTTPException( 429, "Two local agent jobs are already running" );
    job = Job( id=uuid.uuid4().hex, cwd=str( directory ), requested_cwd=( cwd or str( requested ) ) if directory != requested else str( requested ), directory_pending=directory != requested, model=model, prompt=prompt, conversation_id=conversation_id, conversation_verified=bool( conversation_id ) );
    JOB_ROOT.mkdir( parents=True, exist_ok=True, mode=0o700 );
    folder = JOB_ROOT / job.id;
    folder.mkdir( mode=0o700 );
    task_file = folder / "task.txt";
    try:
        with task_file.open( "x", encoding="utf-8" ) as output:
            if directory != requested:
                output.write( f"Requested working directory exactly as entered (JSON string): {json.dumps( job.requested_cwd )}\nInterpreted path (JSON string): {json.dumps( str( requested ) )}\nThe directory does not exist as written; a workspace-root name may also be misspelled. Before performing the task, look for the intended directory within these allowed workspace roots: {json.dumps( [ str( root ) for root in roots ] )}. Change into it if unambiguous. Do not create a directory or work in the fallback directory just to bypass this request. If no match is clear, ask the user for clarification and finish this turn.\n\nUser task:\n" );
            output.write( prompt );
        task_file.chmod( 0o600 );
        instruction = f"Read the complete task from {task_file} and carry it out. Do not modify that task file. Work in the requested local directory.";
        argv = [ os.getenv( "WARPWEB_CLI_PATH", "warp" ), "agent", "run", "--prompt", instruction, "--cwd", str( directory ), "--no-snapshot", "--output-format", "ndjson" ];
        if conversation_id:
            argv.extend( [ "--conversation", conversation_id ] );
        if model:
            argv.extend( [ "--model", model ] );
        process = subprocess.Popen( argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=directory, start_new_session=True );
    except ( OSError, ValueError ) as error:
        task_file.unlink( missing_ok=True );
        folder.rmdir();
        raise HTTPException( 503, "Unable to start the local Warp CLI" ) from error;
    job.process = process;
    jobs[job.id] = job;
    asyncio.create_task( monitor( process, job, task_file ) );
    return job;
