"""Password verification and short-lived, server-side browser sessions."""

import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from collections import defaultdict, deque


HASH_ALGORITHM = "pbkdf2_sha3_256"
HASH_ITERATIONS = 600_000
SESSION_LIFETIME = 12 * 60 * 60
LOGIN_WINDOW = 15 * 60
LOGIN_LIMIT = 5


def make_password_hash( password: str ) -> str:
    salt = os.urandom( 24 );
    digest = hashlib.pbkdf2_hmac( "sha3_256", password.encode( "utf-8" ), salt, HASH_ITERATIONS );
    return f"{HASH_ALGORITHM}:{HASH_ITERATIONS}:{salt.hex()}:{digest.hex()}";


def verify_password( password: str, encoded: str ) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split( ":" );
        iterations = int( iterations_text );
        salt = bytes.fromhex( salt_text );
        expected = bytes.fromhex( digest_text );
        if algorithm != HASH_ALGORITHM or not ( 100_000 <= iterations <= 2_000_000 ) or len( salt ) < 16 or len( expected ) != 32:
            return False;
        actual = hashlib.pbkdf2_hmac( "sha3_256", password.encode( "utf-8" ), salt, iterations );
        return hmac.compare_digest( actual, expected );
    except ( TypeError, ValueError ):
        return False;


def client_ip( direct_ip: str, forwarded_ip: str | None ) -> str:
    if direct_ip in ( "127.0.0.1", "::1" ) and forwarded_ip:
        try:
            return str( ipaddress.ip_address( forwarded_ip ) );
        except ValueError:
            pass;
    return direct_ip;


class SessionStore:
    def __init__( self ) -> None:
        self.sessions: dict[str, float] = {};
        self.attempts: dict[str, deque[float]] = defaultdict( deque );

    def blocked( self, address: str ) -> bool:
        now = time.monotonic();
        attempts = self.attempts[address];
        while attempts and now - attempts[0] >= LOGIN_WINDOW:
            attempts.popleft();
        return len( attempts ) >= LOGIN_LIMIT;

    def failed( self, address: str ) -> None:
        self.attempts[address].append( time.monotonic() );

    def passed( self, address: str ) -> None:
        self.attempts.pop( address, None );

    def create( self ) -> str:
        now = time.monotonic();
        self.sessions = { key: expiry for key, expiry in self.sessions.items() if expiry > now };
        token = secrets.token_urlsafe( 32 );
        self.sessions[hashlib.sha256( token.encode() ).hexdigest()] = now + SESSION_LIFETIME;
        return token;

    def valid( self, token: str | None ) -> bool:
        if not token or len( token ) > 128:
            return False;
        expiry = self.sessions.get( hashlib.sha256( token.encode() ).hexdigest(), 0 );
        return expiry > time.monotonic();

    def remove( self, token: str | None ) -> None:
        if token:
            self.sessions.pop( hashlib.sha256( token.encode() ).hexdigest(), None );
