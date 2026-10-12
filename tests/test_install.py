"""Installation contracts tested in temporary directories without live secrets."""

import os
import shlex
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from app.auth import verify_password
from app.config import validate_origin
from tools import install, hash_password, private_files
from tools.private_files import atomic_private_write, env_line


@pytest.mark.parametrize( "origin", [ "http://127.0.0.1:8797", "http://localhost:8797", "http://[::1]:8797", "https://agents.example.com", "https://agents.example.com:8443" ] )
def test_install_origins( origin ):
    assert validate_origin( origin + "/" ) == origin;


@pytest.mark.parametrize( "origin", [ "http://agents.example.com", "https://user:pass@agents.example.com", "https://agents.example.com/path", "https://agents.example.com?x=1", "https://agents.example.com#part", "https://agents.example.com:0", "https://agents.example.com:65536", "https://agents.example.com:bad", "https://agents.example.com/\n", "javascript:alert(1)", "https://agents.example.com\\@evil.test", "https://agents.example.com/%0a" ] )
def test_invalid_install_origins( origin ):
    with pytest.raises( ValueError ): validate_origin( origin );


def test_https_only_rejects_http_loopback():
    with pytest.raises( ValueError ): validate_origin( "http://127.0.0.1:8797", https_only=True );


def test_origin_matches_browser_normalization():
    assert validate_origin( "https://AGENTS.EXAMPLE.COM:443/" ) == "https://agents.example.com";
    assert validate_origin( "http://LOCALHOST:80" ) == "http://localhost";


def test_private_write_replaces_atomically_and_does_not_follow_symlink( tmp_path ):
    file = tmp_path / "private.env";
    atomic_private_write( file, env_line( "EXAMPLE", 'space "quotes" \\slash $literal %literal' ) );
    assert file.stat().st_mode & 0o777 == 0o600;
    assert shlex.split( file.read_text().strip().split( "=", 1 )[1] ) == [ 'space "quotes" \\slash $literal %literal' ];
    file.chmod( 0o644 );
    atomic_private_write( file, "changed\n" );
    assert file.stat().st_mode & 0o777 == 0o600;
    link = tmp_path / "link.env"; link.symlink_to( file );
    with pytest.raises( ValueError ): atomic_private_write( link, "unsafe" );
    assert file.read_text() == "changed\n";
    assert not list( tmp_path.glob( ".private.env.*" ) );


@pytest.mark.parametrize( "name,value", [ ( "INVALID-NAME", "value" ), ( "OK", "line\ninjection" ), ( "OK", "null\0byte" ), ( "OK", "\x7f" ) ] )
def test_environment_injection_is_rejected( name, value ):
    with pytest.raises( ValueError ): env_line( name, value );


def test_hidden_prompt_does_not_fall_back_to_echo( monkeypatch ):
    def cannot_hide( label ):
        warnings.warn( "No echo control", private_files.getpass.GetPassWarning );
        return "must-not-be-used";
    monkeypatch.setattr( private_files.getpass, "getpass", cannot_hide );
    with pytest.raises( ValueError, match="non-echoing" ): private_files.hidden_prompt( "Secret: " );


def test_hasher_env_file_preserves_settings_without_printing_verifier( tmp_path, monkeypatch, capsys ):
    file = tmp_path / "warpweb.env";
    atomic_private_write( file, env_line( "EXAMPLE", "preserved" ) + env_line( "WARP_API_HASH", "old-verifier" ) );
    answers = iter( [ "example-only-web-password", "example-only-web-password" ] );
    monkeypatch.setattr( hash_password, "hidden_prompt", lambda label: next( answers ) );
    monkeypatch.setattr( sys, "argv", [ "hash_password", "--env-file", str( file ) ] );
    hash_password.main();
    content = file.read_text();
    verifier = shlex.split( content.split( "WARP_API_HASH=", 1 )[1].strip() )[0];
    assert verify_password( "example-only-web-password", verifier );
    assert 'EXAMPLE="preserved"' in content and content.count( "WARP_API_HASH=" ) == 1;
    output = capsys.readouterr().out;
    assert "example-only-web-password" not in output and verifier not in output and "pbkdf2" not in output;
    assert file.stat().st_mode & 0o777 == 0o600;


def test_hasher_mismatch_leaves_existing_file_unchanged( tmp_path, monkeypatch ):
    file = tmp_path / "warpweb.env"; atomic_private_write( file, "EXAMPLE=old\n" );
    answers = iter( [ "one", "two" ] );
    monkeypatch.setattr( hash_password, "hidden_prompt", lambda label: next( answers ) );
    monkeypatch.setattr( sys, "argv", [ "hash_password", "--env-file", str( file ) ] );
    with pytest.raises( SystemExit ): hash_password.main();
    assert file.read_text() == "EXAMPLE=old\n";


def test_unit_has_no_secrets_and_escapes_paths( tmp_path ):
    root = tmp_path / 'repo with $money %percent "quotes"';
    text = install.service_text( root, tmp_path / "private.env", 8800 );
    assert "WARP_API_KEY" not in text and "WARP_API_HASH" not in text;
    assert "--host 127.0.0.1 --port 8800" in text and "--forwarded-allow-ips 127.0.0.1" in text;
    assert '$$money' in text and "%%percent" in text and '\\"quotes\\"' in text;
    with pytest.raises( ValueError ): install.unit_quote( "bad\nEnvironment=INJECTED" );

def test_service_rejects_unsafe_paths( tmp_path ):
    for value in ( "relative", "/tmp/control\nvalue", "/tmp/trailing ", "/tmp/trailing\\" ):
        with pytest.raises( ValueError ): install.service_text( Path( value ), tmp_path / "private.env", 8797 );
        with pytest.raises( ValueError ): install.service_text( tmp_path, Path( value ), 8797 );


def test_generated_service_passes_real_systemd_validation( tmp_path ):
    if not shutil.which( "systemd-analyze" ):
        pytest.skip( "systemd-analyze not installed" );
    root = tmp_path / 'repo with spaces $literal %literal "quotes"';
    executable = root / ".venv/bin/uvicorn"; executable.parent.mkdir( parents=True );
    executable.write_text( "#!/bin/sh\nexit 0\n" ); executable.chmod( 0o755 );
    env = root / "private.env"; atomic_private_write( env, "EXAMPLE=fixture\n" );
    unit = tmp_path / "warpweb-validation.service"; unit.write_text( install.service_text( root, env, 8797 ) );
    assert f'WorkingDirectory={str( root ).replace( "%", "%%" )}\n' in unit.read_text();
    assert f'EnvironmentFile={str( env ).replace( "%", "%%" )}\n' in unit.read_text();
    result = subprocess.run( [ "systemd-analyze", "--user", "verify", str( unit ) ], text=True, capture_output=True );
    assert result.returncode == 0, result.stderr;
    assert not result.stderr.strip(), result.stderr;


def test_settings_keep_http_local_and_https_secure( tmp_path ):
    local = install.settings( "http://127.0.0.1:8797", tmp_path / "warp", [ str( tmp_path ) ] );
    public = install.settings( "https://agents.example.com", tmp_path / "warp", [ str( tmp_path ) ] );
    assert local["WARPWEB_COOKIE_SECURE"] == local["WARPWEB_PUBLIC_HTTPS"] == "0";
    assert public["WARPWEB_COOKIE_SECURE"] == public["WARPWEB_PUBLIC_HTTPS"] == "1";
    assert public["WARPWEB_PUBLIC_URL"] == public["WARPWEB_ALLOWED_ORIGINS"];
    assert install.workdir_roots( [ str( tmp_path ), str( tmp_path ) ] ) == [ str( tmp_path ) ];
    with pytest.raises( ValueError ): install.workdir_roots( [ str( tmp_path / "missing" ) ] );


def test_private_backups_are_bounded( tmp_path ):
    file = tmp_path / "warpweb.env"; atomic_private_write( file, "EXAMPLE=fixture\n" );
    folder = tmp_path / "backups";
    for _ in range( 27 ): install.backup_existing( file, folder );
    copies = list( folder.glob( "*.env" ) );
    assert len( copies ) == 25 and folder.stat().st_mode & 0o777 == 0o700;
    assert all( item.stat().st_mode & 0o777 == 0o600 for item in copies );


@pytest.mark.parametrize( "start", [ False, True ] )
def test_install_flow_in_isolated_home( tmp_path, monkeypatch, capsys, start ):
    root = tmp_path / "checkout"; root.mkdir();
    ( root / "requirements.txt" ).write_text( "fixture\n" );
    home = tmp_path / "home"; home.mkdir();
    calls = [];
    monkeypatch.setattr( install, "ROOT", root );
    monkeypatch.setattr( Path, "home", classmethod( lambda cls: home ) );
    monkeypatch.setattr( install, "prerequisites", lambda cli: Path( "/example/warp" ) );
    monkeypatch.setattr( install.os, "geteuid", lambda: 1000 );
    monkeypatch.setattr( sys.stdin, "isatty", lambda: True );
    monkeypatch.setattr( "builtins.input", lambda prompt: "yes" );
    example_token = "wk-" + "example-only" * 3;
    monkeypatch.setattr( install, "hidden_prompt", lambda label: example_token );
    answers = iter( [ "example-only-password", "example-only-password" ] );
    monkeypatch.setattr( hash_password, "hidden_prompt", lambda label: next( answers ) );
    monkeypatch.setattr( sys, "argv", [ "install", "--origin", "https://agents.example.com" ] + ( [] if start else [ "--no-start" ] ) );
    monkeypatch.setattr( install, "check_health", lambda port, https: calls.append( [ "health", str( port ), str( https ) ] ) );
    def fake_run( args, **options ):
        calls.append( args );
        if "venv" in args:
            path = root / ".venv/bin"; path.mkdir( parents=True ); ( path / "python" ).touch();
        if "tools.hash_password" in args:
            saved = sys.argv;
            try:
                sys.argv = [ "hash_password", "--env-file", args[-1] ];
                hash_password.main();
            finally: sys.argv = saved;
        return subprocess.CompletedProcess( args, 0 );
    monkeypatch.setattr( install.subprocess, "run", fake_run );
    install.main();
    env = home / ".config/warpweb/warpweb.env";
    unit = home / ".config/systemd/user/warpweb.service";
    assert env.exists() and unit.exists();
    assert env.stat().st_mode & 0o777 == 0o600 and env.parent.stat().st_mode & 0o777 == 0o700;
    assert example_token in env.read_text() and "WARP_API_HASH=" in env.read_text();
    assert example_token not in unit.read_text() and "WARP_API_HASH" not in unit.read_text();
    assert all( example_token not in " ".join( map( str, call ) ) for call in calls );
    assert any( "restart" in call for call in calls ) is start;
    assert any( "enable" in call for call in calls ) is start;
    assert any( "health" in call for call in calls ) is start;
    assert example_token not in capsys.readouterr().out;
    assert "WARP_API_KEY" not in os.environ and not list( env.parent.glob( ".setup-*" ) );


def test_existing_installation_declined_before_changes( tmp_path, monkeypatch ):
    home = tmp_path / "home"; home.mkdir();
    env = home / ".config/warpweb/warpweb.env"; atomic_private_write( env, "EXAMPLE=original\n" );
    monkeypatch.setattr( Path, "home", classmethod( lambda cls: home ) );
    monkeypatch.setattr( install, "prerequisites", lambda cli: Path( "/example/warp" ) );
    monkeypatch.setattr( install.os, "geteuid", lambda: 1000 );
    monkeypatch.setattr( sys.stdin, "isatty", lambda: True );
    monkeypatch.setattr( "builtins.input", lambda prompt: "no" );
    monkeypatch.setattr( sys, "argv", [ "install" ] );
    monkeypatch.setattr( install.subprocess, "run", lambda *args, **kwargs: pytest.fail( "No write/start commands allowed after declining" ) );
    install.main();
    assert env.read_text() == "EXAMPLE=original\n";
