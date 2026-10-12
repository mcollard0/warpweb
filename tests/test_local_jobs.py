"""Cancellation fixtures run harmless Python sleepers, never Warp agents."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import local_jobs


RUN = "11111111-1111-4111-8111-111111111111";


class FixtureProcess:
    pid = 123456
    def __init__( self, exited=False ):
        self.returncode = 0 if exited else None;
        self.alive = not exited;
    def poll( self ): return self.returncode;
    def wait( self ):
        assert self.returncode is not None;
        return self.returncode;


def test_cancel_is_idempotent_and_escalates_only_the_owned_group( monkeypatch ):
    async def check():
        process = FixtureProcess();
        job = local_jobs.Job( id="owned", process=process );
        monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
        monkeypatch.setattr( local_jobs, "STOP_GRACE", .01 );
        signals = [];
        def killpg( pid, value ):
            assert pid == process.pid;
            if not process.alive: raise ProcessLookupError();
            if value:
                signals.append( value );
                if value == signal.SIGKILL: process.alive = False; process.returncode = -9;
        monkeypatch.setattr( local_jobs.os, "killpg", killpg );
        monkeypatch.setattr( local_jobs, "group_alive", lambda current: current.alive );
        assert local_jobs.cancel( "owned" ) is job;
        task = job.stop_task;
        assert local_jobs.cancel( "owned" ).stop_task is task;
        assert job.state == "stopping" and not job.public()["can_cancel"];
        await task;
        assert signals == [ signal.SIGTERM, signal.SIGKILL ];
        assert job.state == "stopping";
    asyncio.run( check() );


@pytest.mark.parametrize( "directory_case", [ "inside", "root-typo", "relative", "tilde" ] )
def test_missing_directory_launches_from_allowed_ancestor_with_private_instructions( tmp_path, monkeypatch, directory_case ):
    monkeypatch.setenv( "WARPWEB_WORKDIR_ROOTS", str( tmp_path ) );
    monkeypatch.setattr( local_jobs, "JOB_ROOT", tmp_path / "jobs" );
    monkeypatch.setattr( local_jobs, "jobs", {} );
    captured = {};
    class Process:
        def __init__( self, argv, **kwargs ): captured.update( argv=argv, options=kwargs );
    async def monitor( process, job, task ): captured["task"] = task;
    monkeypatch.setattr( local_jobs.subprocess, "Popen", Process );
    monkeypatch.setattr( local_jobs, "monitor", monitor );
    monkeypatch.chdir( tmp_path );
    monkeypatch.setenv( "HOME", str( tmp_path ) );
    requested = { "inside": str( tmp_path / "misspeled" / "project" ), "root-typo": str( tmp_path.parent / ( tmp_path.name + "-misspeled" ) / "project" ), "relative": "misspeled/project", "tilde": "~/misspeled/project" }[directory_case];
    async def launch():
        job = local_jobs.start( "private task", requested, model="fixture-model" );
        await asyncio.sleep( 0 );
        return job;
    job = asyncio.run( launch() );
    assert job.directory_pending and job.cwd == str( tmp_path );
    assert job.requested_cwd == requested;
    assert captured["options"]["cwd"] == tmp_path;
    argv = captured["argv"];
    assert argv[argv.index( "--cwd" ) + 1] == str( tmp_path ) and argv[argv.index( "--model" ) + 1] == "fixture-model";
    assert "private task" not in " ".join( argv ) and "misspeled" not in " ".join( argv );
    task = captured["task"];
    assert "look for the intended directory" in task.read_text() and "ask the user" in task.read_text();
    assert json.dumps( requested ) in task.read_text() and json.dumps( [ str( tmp_path ) ] ) in task.read_text();
    assert task.stat().st_mode & 0o777 == 0o600;
    task.unlink(); task.parent.rmdir();
    assert not ( tmp_path / "misspeled" ).exists();


def test_directory_fallback_never_escapes_roots_or_accepts_files( tmp_path, monkeypatch ):
    root = tmp_path / "allowed"; root.mkdir();
    outside = tmp_path / "outside"; outside.mkdir();
    ( root / "escape" ).symlink_to( outside, target_is_directory=True );
    ( root / "file" ).write_text( "fixture" );
    monkeypatch.setenv( "WARPWEB_WORKDIR_ROOTS", str( root ) );
    for path in [ outside, root / ".." / "missing", root / "escape" / "missing", root / "escape", root / "file", root / "file" / "missing" ]:
        with pytest.raises( HTTPException ): local_jobs.launch_workdir( str( path ) );
    assert local_jobs.launch_workdir( str( outside / "missing" ) )[0] == root;
    assert local_jobs.launch_workdir( str( root / "typo" ) )[0] == root;
    assert local_jobs.launch_workdir( str( root ) )[0] == root;
    with pytest.raises( HTTPException ) as error: local_jobs.launch_workdir( "\0bad" );
    assert error.value.status_code == 400;

def test_directory_recovery_searches_all_existing_roots_and_requires_a_launch_base( tmp_path, monkeypatch ):
    one = tmp_path / "one"; one.mkdir();
    two = tmp_path / "two"; two.mkdir();
    missing = tmp_path / "missing";
    monkeypatch.setenv( "WARPWEB_WORKDIR_ROOTS", f"{missing},{one},{two}" );
    assert local_jobs.launch_workdir( str( one / "typo" ) ) == ( one, one / "typo", [ one, two ] );
    assert local_jobs.launch_workdir( str( tmp_path / "oen" / "project" ) )[0] == one;
    monkeypatch.setenv( "WARPWEB_WORKDIR_ROOTS", str( missing ) );
    with pytest.raises( HTTPException ) as error: local_jobs.launch_workdir( str( missing / "typo" ) );
    assert error.value.status_code == 400;


def test_post_stop_recovers_verified_conversation_without_signalling_another_job( tmp_path, monkeypatch ):
    async def check():
        process = FixtureProcess( exited=True );
        job = local_jobs.Job( id="recover", run_id=RUN, process=process, cancel_requested=True );
        monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
        folder = tmp_path / "job"; folder.mkdir(); task = folder / "task.txt"; task.write_text( "fixture" );
        monkeypatch.setattr( local_jobs, "consume", lambda *args: 1 );
        async def terminated( *args, **kwargs ): pass;
        async def detail( *args ):
            assert job.state == "stopping" and not job.public()["can_resume"];
            return { "source": "CLI", "execution_location": "LOCAL", "conversation_id": RUN };
        monkeypatch.setattr( local_jobs, "terminate_group", terminated );
        monkeypatch.setattr( local_jobs, "get_json", detail );
        await local_jobs.monitor( process, job, task );
        assert job.state == "cancelled" and job.public()["can_resume"] and not task.exists();
    asyncio.run( check() );


def test_identity_verification_rejects_mismatched_conversations():
    job = local_jobs.Job( id="owned", conversation_id=RUN );
    assert not local_jobs.accept_identity( job, { "source": "CLI", "execution_location": "LOCAL", "conversation_id": "22222222-2222-4222-8222-222222222222" } );
    assert not local_jobs.accept_identity( job, { "source": "CLI", "execution_location": "REMOTE", "conversation_id": RUN } );
    assert not job.verified;


def test_cancelled_followup_can_resume_trusted_conversation_without_a_new_run_id( monkeypatch ):
    job = local_jobs.Job( id="followup", conversation_id=RUN, conversation_verified=True, state="cancelled" );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
    assert job.public()["run_id"] is None and job.public()["conversation_id"] == RUN;
    assert job.public()["can_resume"];
    job.state = "stopping";
    assert not job.public()["can_resume"];
    job.state = "cancelled"; job.process = object();
    assert not job.public()["can_resume"];


def test_late_terminal_unknown_and_unowned_cancellation_never_signals( monkeypatch ):
    jobs = { "finished": local_jobs.Job( id="finished", state="completed", process=FixtureProcess( True ) ), "exited": local_jobs.Job( id="exited", process=FixtureProcess( True ) ), "external": local_jobs.Job( id="external" ) };
    monkeypatch.setattr( local_jobs, "jobs", jobs );
    monkeypatch.setattr( local_jobs.os, "killpg", lambda *args: pytest.fail( "No process may be signalled" ) );
    assert local_jobs.cancel( "finished" ) is jobs["finished"];
    for key, status in ( ( "unknown", 404 ), ( "external", 409 ), ( "exited", 409 ) ):
        with pytest.raises( HTTPException ) as error: local_jobs.cancel( key );
        assert error.value.status_code == status;


def test_kill_requires_live_group_exit_even_when_leader_has_exited( monkeypatch ):
    async def check():
        process = FixtureProcess( exited=True );
        signals = [];
        monkeypatch.setattr( local_jobs, "STOP_GRACE", 0 );
        monkeypatch.setattr( local_jobs, "group_alive", lambda current: True );
        monkeypatch.setattr( local_jobs.os, "killpg", lambda pid, value: signals.append( ( pid, value ) ) );
        monkeypatch.setattr( process, "wait", lambda: pytest.fail( "Leader exit cannot confirm child exit" ) );
        with pytest.raises( TimeoutError, match="Process-group termination" ):
            await local_jobs.terminate_group( process );
        assert signals == [ ( process.pid, signal.SIGTERM ), ( process.pid, signal.SIGKILL ) ];
    asyncio.run( check() );


def test_signal_failure_does_not_claim_cancellation( monkeypatch ):
    job = local_jobs.Job( id="owned", process=FixtureProcess() );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
    def denied( *args ): raise PermissionError();
    monkeypatch.setattr( local_jobs.os, "killpg", denied );
    with pytest.raises( HTTPException ) as error: local_jobs.cancel( job.id );
    assert error.value.status_code == 503 and job.state == "running" and not job.cancel_requested;


def test_stopping_jobs_hold_slots_and_conversation_locks( tmp_path, monkeypatch ):
    monkeypatch.setenv( "WARPWEB_WORKDIR_ROOTS", str( tmp_path ) );
    one = local_jobs.Job( id="one", state="stopping", conversation_id=RUN );
    two = local_jobs.Job( id="two", state="stopping" );
    monkeypatch.setattr( local_jobs, "jobs", { one.id: one, two.id: two } );
    with pytest.raises( HTTPException ) as error: local_jobs.start( "fixture", str( tmp_path ), RUN );
    assert error.value.status_code == 409;
    with pytest.raises( HTTPException ) as error: local_jobs.start( "fixture", str( tmp_path ) );
    assert error.value.status_code == 429;


def test_metadata_association_does_not_guess_unverified_run_ids( monkeypatch ):
    job = local_jobs.Job( id="owned", run_id=RUN, conversation_id=RUN, process=object() );
    monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
    assert local_jobs.for_run( RUN ) is None;
    assert local_jobs.for_run( "other", RUN )["run_id"] is None;
    job.verified = True;
    assert local_jobs.for_run( "other", RUN )["id"] == job.id;
    assert local_jobs.for_run( "other", "different-conversation" ) is None;
    assert local_jobs.for_run( RUN )["can_cancel"];
    job.state = "cancelled"; job.process = None;
    assert local_jobs.for_run( RUN )["state"] == "cancelled";


def test_cleanup_failure_does_not_release_ownership_or_claim_exit( tmp_path, monkeypatch ):
    async def check():
        process = FixtureProcess();
        job = local_jobs.Job( id="owned", process=process );
        folder = tmp_path / "job"; folder.mkdir(); task = folder / "task.txt"; task.write_text( "fixture" );
        monkeypatch.setattr( local_jobs, "consume", lambda *args: 1 );
        async def denied( *args, **kwargs ): raise PermissionError();
        monkeypatch.setattr( local_jobs, "terminate_group", denied );
        await local_jobs.monitor( process, job, task );
        assert job.state == "stopping" and job.process is process and task.exists();
        assert "Unable to confirm" in job.error;
    asyncio.run( check() );


def spawn_fixture( folder, ignore_term=False, child=False ):
    child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(60)";
    code = "import json,signal,subprocess,sys,time; from pathlib import Path\n";
    if ignore_term: code += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n";
    if child:
        code += f"child = subprocess.Popen([sys.executable, '-u', '-c', {child_code!r}], stdout=subprocess.PIPE, text=True)\nassert child.stdout.readline().strip() == 'ready'\nPath({str( folder / 'child.pid' )!r}).write_text(str(child.pid))\n";
    started = { "type": "system", "event_type": "run_started", "run_id": RUN };
    code += f"print(json.dumps({started!r}),flush=True)\nprint(json.dumps({{'type':'agent','text':'fixture-ready'}}),flush=True)\ntime.sleep(60)\n";
    return subprocess.Popen( [ sys.executable, "-u", "-c", code ], stdout=subprocess.PIPE, start_new_session=True );


async def ready( job ):
    for _ in range( 200 ):
        if "fixture-ready" in job.output: return;
        await asyncio.sleep( .01 );
    raise AssertionError( "Fixture process did not become ready" );


def cleanup_fixture( process ):
    try: os.killpg( process.pid, signal.SIGKILL );
    except ProcessLookupError: pass;
    process.wait( timeout=2 );
    process.stdout.close();


@pytest.mark.parametrize( "ignore_term,child", [ ( False, False ), ( True, False ), ( False, True ) ] )
def test_real_owned_group_cancellation_and_private_task_cleanup( tmp_path, monkeypatch, ignore_term, child ):
    async def check():
        monkeypatch.setattr( local_jobs, "STOP_GRACE", .15 );
        folder = tmp_path / "job"; folder.mkdir(); task = folder / "task.txt"; task.write_text( "fixture" );
        process = spawn_fixture( folder, ignore_term, child );
        job = local_jobs.Job( id="owned", process=process, run_id=RUN, verified=True );
        monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
        monitoring = asyncio.create_task( local_jobs.monitor( process, job, task ) );
        try:
            await ready( job );
            child_pid = int( ( folder / "child.pid" ).read_text() ) if child else None;
            if child: ( folder / "child.pid" ).unlink();
            local_jobs.cancel( job.id );
            assert job.state == "stopping" and task.exists();
            local_jobs.cancel( job.id );
            await asyncio.wait_for( monitoring, timeout=5 );
            assert job.state == "cancelled" and job.error is None and job.process is None;
            assert not folder.exists() and process.poll() is not None and not job.public()["can_cancel"];
            if child_pid:
                stat = Path( f"/proc/{child_pid}/stat" );
                assert not stat.exists() or stat.read_text().split( ")" )[1].split()[0] == "Z";
        finally:
            cleanup_fixture( process );
            if not monitoring.done(): monitoring.cancel(); await asyncio.gather( monitoring, return_exceptions=True );
    asyncio.run( check() );


def test_cancel_interrupts_inflight_remote_verification( tmp_path, monkeypatch ):
    async def check():
        started = asyncio.Event();
        async def unavailable( *args ):
            started.set();
            await asyncio.Event().wait();
        monkeypatch.setattr( local_jobs, "get_json", unavailable );
        monkeypatch.setattr( local_jobs, "STOP_GRACE", .1 );
        folder = tmp_path / "job"; folder.mkdir(); task = folder / "task.txt"; task.write_text( "fixture" );
        process = spawn_fixture( folder );
        job = local_jobs.Job( id="owned", process=process );
        monkeypatch.setattr( local_jobs, "jobs", { job.id: job } );
        monitoring = asyncio.create_task( local_jobs.monitor( process, job, task ) );
        try:
            await asyncio.wait_for( started.wait(), timeout=5 );
            local_jobs.cancel( job.id );
            await asyncio.wait_for( monitoring, timeout=3 );
            assert job.state == "cancelled" and not task.exists();
        finally:
            cleanup_fixture( process );
            if not monitoring.done(): monitoring.cancel(); await asyncio.gather( monitoring, return_exceptions=True );
    asyncio.run( check() );


def test_timeout_escalates_and_waits_before_cleanup( tmp_path, monkeypatch ):
    async def check():
        monkeypatch.setattr( local_jobs, "TIMEOUT", .25 );
        monkeypatch.setattr( local_jobs, "STOP_GRACE", .1 );
        folder = tmp_path / "job"; folder.mkdir(); task = folder / "task.txt"; task.write_text( "fixture" );
        process = spawn_fixture( folder, ignore_term=True );
        job = local_jobs.Job( id="owned", process=process, verified=True, run_id=RUN );
        try:
            await asyncio.wait_for( local_jobs.monitor( process, job, task ), timeout=5 );
            assert job.state == "failed" and "15-minute" in job.error and process.poll() is not None and not task.exists();
        finally: cleanup_fixture( process );
    asyncio.run( check() );
