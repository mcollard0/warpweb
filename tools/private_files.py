"""Atomic private-file writes shared by installation and password setup."""

import os
import re
import tempfile
import getpass
import warnings
from pathlib import Path


def hidden_prompt( label: str ) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter( "error", getpass.GetPassWarning );
        try:
            return getpass.getpass( label );
        except getpass.GetPassWarning:
            raise ValueError( "A terminal with non-echoing input is required for secrets." );


def env_line( name: str, value: str ) -> str:
    if not re.fullmatch( r"[A-Z][A-Z0-9_]*", name ) or any( ord( char ) < 32 or ord( char ) == 127 for char in value ):
        raise ValueError( "Environment names/values must not contain control characters." );
    escaped = value.replace( "\\", "\\\\" ).replace( '"', '\\"' );
    return f'{name}="{escaped}"\n';


def atomic_private_write( path: Path, text: str ) -> None:
    if path.is_symlink():
        raise ValueError( "Refusing to write through a symbolic link." );
    path.parent.mkdir( parents=True, exist_ok=True, mode=0o700 );
    descriptor, temporary = tempfile.mkstemp( prefix=f".{path.name}.", dir=path.parent );
    try:
        with os.fdopen( descriptor, "w", encoding="utf-8" ) as output:
            os.fchmod( output.fileno(), 0o600 );
            output.write( text );
            output.flush();
            os.fsync( output.fileno() );
        os.replace( temporary, path );
    finally:
        Path( temporary ).unlink( missing_ok=True );
