"""Interactive, user-scoped setup; --check and --help never change services."""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.config import validate_origin
from tools.private_files import atomic_private_write, env_line, hidden_prompt


ROOT = Path( __file__ ).resolve().parents[1];
UNIT_NAME = "warpweb.service";


def unit_quote( value: str, command: bool = False ) -> str:
    if any( ord( char ) < 32 or ord( char ) == 127 for char in value ):
        raise ValueError( "Installation paths must not contain control characters." );
    escaped = value.replace( "\\", "\\\\" ).replace( '"', '\\"' ).replace( "%", "%%" );
    if command:
        escaped = escaped.replace( "$", "$$" );
    return f'"{escaped}"';


def unit_path( path: Path ) -> str:
    value = str( path );
    if not path.is_absolute() or value != value.rstrip() or value.endswith( "\\" ) or any( ord( char ) < 32 or ord( char ) == 127 for char in value ):
        raise ValueError( "Systemd paths must be absolute, without control characters, trailing whitespace, or a trailing backslash." );
    return value.replace( "%", "%%" );


def service_text( root: Path, env_file: Path, port: int ) -> str:
    working_directory = unit_path( root );
    environment_file = unit_path( env_file );
    return "\n".join( [
        "[Unit]", "Description=Warpweb local-agent web client", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=simple", f"WorkingDirectory={working_directory}", f"EnvironmentFile={environment_file}",
        f"ExecStart=/usr/bin/env -- {unit_quote( str( root / '.venv/bin/uvicorn' ), command=True )} app.main:app --host 127.0.0.1 --port {port} --no-access-log --proxy-headers --forwarded-allow-ips 127.0.0.1",
        "Restart=on-failure", "RestartSec=5", "UMask=0077", "KillMode=control-group", "TimeoutStopSec=15", "StandardOutput=journal", "StandardError=journal", "",
        "[Install]", "WantedBy=default.target", "",
    ] );


def backup_existing( path: Path, folder: Path ) -> None:
    if path.is_symlink():
        raise ValueError( "Refusing to replace a symbolic-link configuration file." );
    if not path.exists():
        return;
    if folder.is_symlink():
        raise ValueError( "Refusing a symbolic-link backup directory." );
    folder.mkdir( parents=True, exist_ok=True, mode=0o700 );
    folder.chmod( 0o700 );
    stamp = datetime.now( timezone.utc ).strftime( "%Y%m%dT%H%M%S.%fZ" );
    target = folder / f"{path.stem}.{stamp}{path.suffix}";
    atomic_private_write( target, path.read_text( encoding="utf-8" ) );
    copies = sorted( folder.glob( f"{path.stem}.*{path.suffix}" ), key=lambda item: item.stat().st_mtime );
    for old in copies[:-25]:
        old.unlink();


def workdir_roots( values: list[str] ) -> list[str]:
    roots = [];
    for value in values:
        path = Path( value ).expanduser().resolve();
        if not path.is_dir() or "," in str( path ) or any( ord( char ) < 32 for char in str( path ) ):
            raise ValueError( "Workspace roots must be existing directories without commas/control characters." );
        if str( path ) not in roots:
            roots.append( str( path ) );
    return roots;


def settings( origin: str, cli: Path, roots: list[str] ) -> dict[str, str]:
    https = urlsplit( origin ).scheme == "https";
    return {
        "WARPWEB_ALLOWED_ORIGINS": origin,
        "WARPWEB_PUBLIC_URL": origin,
        "WARPWEB_PUBLIC_HTTPS": "1" if https else "0",
        "WARPWEB_COOKIE_SECURE": "1" if https else "0",
        "WARPWEB_WORKDIR_ROOTS": ",".join( roots ),
        "WARPWEB_CLI_PATH": str( cli ),
    };


def prerequisites( requested_cli: str | None ) -> Path:
    if sys.version_info < ( 3, 10 ):
        raise ValueError( "Python 3.10 or newer is required." );
    if not importlib.util.find_spec( "venv" ):
        raise ValueError( "Install Python's venv support first." );
    if not shutil.which( "systemctl" ):
        raise ValueError( "This installer requires Linux with systemd user services." );
    if not os.access( "/usr/bin/env", os.X_OK ):
        raise ValueError( "This installer requires /usr/bin/env (coreutils)." );
    result = subprocess.run( [ "systemctl", "--user", "--no-pager", "list-units", "--type=service", "--no-legend" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE );
    if result.returncode:
        raise ValueError( "A systemd user manager is required; run from your logged-in user session, not sudo." );
    candidate = requested_cli or shutil.which( "warp" ) or str( Path.home() / ".local/bin/warp" );
    cli = Path( candidate ).expanduser().absolute();
    if not cli.is_file() or not os.access( cli, os.X_OK ):
        raise ValueError( "Install the Warp CLI with local 'warp agent run' support, or provide --cli /absolute/path/to/warp." );
    result = subprocess.run( [ str( cli ), "agent", "run", "--help" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30 );
    if result.returncode or "--conversation" not in result.stdout or "ndjson" not in result.stdout:
        raise ValueError( "The CLI must support 'agent run', --conversation, and ndjson output." );
    return cli;


def create_credentials( venv_python: Path, env_file: Path, config: dict[str, str] ) -> None:
    print( "Open https://oz.warp.dev/settings -> Generate New Token -> choose Personal." );
    token = hidden_prompt( "Personal Warp token (hidden): " );
    if not token.startswith( "wk-" ) or len( token ) < 12 or len( token ) > 4096 or any( char.isspace() or ord( char ) < 32 or ord( char ) == 127 for char in token ):
        raise ValueError( "Expected a nonempty Personal API token starting with wk-, without whitespace." );
    os.environ["WARP_API_KEY"] = token;
    token = "";
    descriptor, temporary = tempfile.mkstemp( prefix=".setup-", suffix=".env", dir=env_file.parent );
    os.close( descriptor );
    try:
        temporary_path = Path( temporary );
        text = "".join( env_line( name, value ) for name, value in config.items() );
        atomic_private_write( temporary_path, text + env_line( "WARP_API_KEY", os.environ["WARP_API_KEY"] ) );
        subprocess.run( [ str( venv_python ), "-m", "tools.hash_password", "--env-file", temporary ], cwd=ROOT, check=True );
        atomic_private_write( env_file, temporary_path.read_text( encoding="utf-8" ) );
    finally:
        os.environ.pop( "WARP_API_KEY", None );
        Path( temporary ).unlink( missing_ok=True );


def check_health( port: int, https: bool ) -> None:
    headers = { "X-Forwarded-Proto": "https" } if https else {};
    for _ in range( 20 ):
        try:
            with urlopen( Request( f"http://127.0.0.1:{port}/api/session", headers=headers ), timeout=2 ) as response:
                if response.status == 200:
                    print( "Local health check passed. Log in with the web password you chose." );
                    return;
        except OSError:
            time.sleep( .5 );
    raise ValueError( "Service started but local health did not pass; inspect 'journalctl --user -u warpweb.service'." );


def main() -> None:
    parser = argparse.ArgumentParser( description="Install a private loopback Warpweb service for the current Linux user. Never run with sudo." );
    parser.add_argument( "--check", action="store_true", help="Check prerequisites/configuration only; do not install, prompt for secrets, or restart anything." );
    parser.add_argument( "--no-start", action="store_true", help="Install credentials/unit but do not enable, start, or restart the service." );
    parser.add_argument( "--port", type=int, default=8797, help="Loopback listener port (default: 8797)." );
    parser.add_argument( "--origin", help="Browser origin, e.g. https://agents.example.com; default: http://127.0.0.1:PORT." );
    parser.add_argument( "--workdir-root", action="append", help="Allowed working-directory root; repeat to add roots. Default: this checkout." );
    parser.add_argument( "--cli", help="Path to the installed Warp CLI." );
    args = parser.parse_args();
    if not 1024 <= args.port <= 65535:
        raise ValueError( "Choose an unprivileged port between 1024 and 65535." );
    origin = validate_origin( args.origin or f"http://127.0.0.1:{args.port}" );
    parsed = urlsplit( origin );
    if parsed.scheme == "http" and ( parsed.hostname not in ( "127.0.0.1", "localhost" ) or parsed.port != args.port ):
        raise ValueError( "Local HTTP origin must be http://127.0.0.1:PORT or http://localhost:PORT, matching --port." );
    roots = workdir_roots( args.workdir_root or [ str( ROOT ) ] );
    cli = prerequisites( args.cli );
    print( f"Prerequisites passed. Checkout: {ROOT}\nCLI: {cli}\nBrowser origin: {origin}\nListener: 127.0.0.1:{args.port}\nAllowed workspace roots: {', '.join( roots )}" );
    if args.check:
        print( "Check only: no credentials, dependencies, units, or services were changed." );
        return;
    if os.geteuid() == 0 or not sys.stdin.isatty():
        raise ValueError( "Run interactively as a normal logged-in user, without sudo or piped secret input." );
    config_dir = Path.home() / ".config/warpweb";
    env_file = config_dir / "warpweb.env";
    unit_file = Path.home() / ".config/systemd/user" / UNIT_NAME;
    if config_dir.is_symlink() or env_file.is_symlink() or unit_file.is_symlink():
        raise ValueError( "Refusing symbolic-link installation/credential paths." );
    unit_text = service_text( ROOT, env_file, args.port );
    exists = env_file.exists() or unit_file.exists();
    if exists:
        print( "Existing installation found. Replacing it rotates both secrets and can change its origin/workspace settings. Starting it will restart the service and terminate local jobs." );
        if input( "Type REPLACE to back up and replace this installation: " ) != "REPLACE":
            print( "Existing installation left unchanged." );
            return;
    elif input( "Install dependencies, private credentials, and user service? Type yes: " ).lower() != "yes":
        print( "Installation cancelled." );
        return;
    print( "Step 1/4: create/reuse .venv and install server dependencies." );
    venv_python = ROOT / ".venv/bin/python";
    if not venv_python.exists():
        subprocess.run( [ sys.executable, "-m", "venv", str( ROOT / ".venv" ) ], check=True );
    subprocess.run( [ str( venv_python ), "-m", "pip", "install", "-r", str( ROOT / "requirements.txt" ) ], check=True );
    print( "Step 2/4: back up existing configuration privately and prompt for new secrets." );
    config_dir.mkdir( parents=True, exist_ok=True, mode=0o700 );
    config_dir.chmod( 0o700 );
    backup_existing( env_file, config_dir / "backups" );
    backup_existing( unit_file, config_dir / "backups" );
    create_credentials( venv_python, env_file, settings( origin, cli, roots ) );
    print( "Step 3/4: install a mode-0600 user unit referencing the private environment file." );
    atomic_private_write( unit_file, unit_text );
    subprocess.run( [ "systemctl", "--user", "--no-pager", "daemon-reload" ], check=True );
    print( f"Credentials: {env_file} (0600)\nService unit: {unit_file}\nNo credentials are written into the unit or repository." );
    if args.no_start:
        print( "Step 4/4 skipped (--no-start). Start later with systemctl --user enable --now warpweb.service; restart an existing active unit to apply new credentials." );
        return;
    if input( "Step 4/4: enable and start/restart now? This clears logins and stops any existing local jobs. Type yes: " ).lower() != "yes":
        print( "Installed but not started/restarted. Use systemctl --user enable --now warpweb.service; restart if already active." );
        return;
    subprocess.run( [ "systemctl", "--user", "--no-pager", "enable", UNIT_NAME ], check=True );
    subprocess.run( [ "systemctl", "--user", "--no-pager", "restart", UNIT_NAME ], check=True );
    subprocess.run( [ "systemctl", "--user", "--no-pager", "show", UNIT_NAME, "--property=ActiveState,SubState,MainPID" ], check=True );
    check_health( args.port, parsed.scheme == "https" );
    print( f"Open {origin}. HTTPS mode requires your TLS proxy/tunnel to be configured separately." );


if __name__ == "__main__":
    try:
        main();
    except ( ValueError, OSError, subprocess.SubprocessError ) as error:
        raise SystemExit( f"Installation failed: {error}" );
    except ( KeyboardInterrupt, EOFError ):
        raise SystemExit( "Installation cancelled; no secret values were printed." );
