"""Print a password verifier suitable for WARP_API_HASH; never print the password."""

import argparse
import re
from pathlib import Path

from app.auth import make_password_hash
from tools.private_files import atomic_private_write, env_line, hidden_prompt


def main() -> None:
    parser = argparse.ArgumentParser( description="Generate a web-password verifier without storing the password." );
    parser.add_argument( "--env-file", type=Path, help="Write/update WARP_API_HASH in a mode-0600 environment file without printing the verifier." );
    args = parser.parse_args();
    if args.env_file and args.env_file.is_symlink():
        raise SystemExit( "Refusing to update a symbolic-link environment file." );
    password = hidden_prompt( "New web password: " );
    confirm = hidden_prompt( "Confirm web password: " );
    if not password or password != confirm:
        raise SystemExit( "Passwords must be nonempty and match." );
    verifier = make_password_hash( password );
    if args.env_file:
        text = args.env_file.read_text( encoding="utf-8" ) if args.env_file.exists() else "";
        lines = [ line for line in text.splitlines( keepends=True ) if not re.match( r"^\s*WARP_API_HASH\s*=", line ) ];
        kept = "".join( lines );
        atomic_private_write( args.env_file, kept + ( "\n" if kept and not kept.endswith( "\n" ) else "" ) + env_line( "WARP_API_HASH", verifier ) );
        print( "Password verifier saved to the private environment file." );
    else:
        print( "WARP_API_HASH=" + verifier );


if __name__ == "__main__":
    main();
