import os
from pathlib import Path


def database_path() -> str:
    return os.getenv("VAULT_DB_PATH") or str(Path(__file__).resolve().parents[1] / "data/vault.db")


MAX_DOCUMENTS = 200
MAX_BODY_BYTES = 1_048_576
TOKEN_TTL = 8 * 60 * 60
MAX_ATTEMPTS = 3
LEASE_SECONDS = 60
