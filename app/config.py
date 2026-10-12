"""Validate deployment origins before using them in redirects or setup."""

import ipaddress
import re
from urllib.parse import urlsplit


def validate_origin( value: str, https_only: bool = False ) -> str:
    if any( ord( char ) < 33 or ord( char ) == 127 for char in value ) or "\\" in value:
        raise ValueError( "Use a clean HTTP(S) origin without whitespace or backslashes." );
    try:
        parsed = urlsplit( value );
        port = parsed.port;
    except ValueError as error:
        raise ValueError( "Invalid origin or port." ) from error;
    if parsed.scheme not in ( "http", "https" ) or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in ( "", "/" ) or parsed.query or parsed.fragment:
        raise ValueError( "Use an HTTP(S) origin with no credentials, path, query, or fragment." );
    if port is not None and not 1 <= port <= 65535:
        raise ValueError( "Origin port must be between 1 and 65535." );
    host = parsed.hostname;
    try:
        address = ipaddress.ip_address( host );
        loopback = address.is_loopback;
    except ValueError:
        if not re.fullmatch( r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host ):
            raise ValueError( "Use a valid ASCII hostname or IP address." );
        loopback = host == "localhost";
    if ( https_only or not loopback ) and parsed.scheme != "https":
        raise ValueError( "Non-loopback installations require HTTPS." );
    authority = f"[{host}]" if ":" in host else host;
    default_port = 443 if parsed.scheme == "https" else 80;
    return f"{parsed.scheme}://{authority}" + ( f":{port}" if port is not None and port != default_port else "" );
