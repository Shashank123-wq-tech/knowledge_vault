"""Password hashing and revocable, database-backed opaque bearer tokens."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import connect

bearer = HTTPBearer(auto_error=False)
ITERATIONS = 600_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    algorithm, rounds, salt, digest = stored.split("$")
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    return hmac.compare_digest(actual, digest)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def database(request: Request):
    with connect(request.app.state.db_path) as db:
        yield db


DB = Annotated[sqlite3.Connection, Depends(database)]


def current_user(db: DB, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials:
        row = db.execute("SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id "
                         "WHERE t.token_hash=? AND t.expires_at>?",
                         (token_hash(credentials.credentials), time.time())).fetchone()
        if row:
            return dict(row)
    raise HTTPException(401, "Missing, expired or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


User = Annotated[dict, Depends(current_user)]


# A fixed salt is used ONLY for dummy work when a username is unknown, never to store passwords.
DUMMY_HASH = hash_password("dummy-password-never-used", "00" * 16)
