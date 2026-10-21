"""Durable job processing with leases and atomic publication of index results."""
import logging
import re
import secrets
import time

from app.config import LEASE_SECONDS, MAX_ATTEMPTS
from app.database import connect

logger = logging.getLogger(__name__)


def chunk_text(text: str, size: int = 160, overlap: int = 30) -> list[str]:
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("Require size > overlap >= 0")
    words = re.findall(r"\S+", text)
    result = []
    for start in range(0, len(words), size - overlap):
        result.append(" ".join(words[start:start + size]))
        if start + size >= len(words):
            break
    return result


def claim_job(path: str, now: float | None = None):
    now = time.time() if now is None else now
    with connect(path) as db:
        # SQLite serializes claim transactions, so two workers cannot own the same lease.
        db.execute("BEGIN IMMEDIATE")
        exhausted = db.execute("SELECT document_id FROM jobs WHERE status='running' "
                               "AND lease_until<=? AND attempts>=?", (now, MAX_ATTEMPTS)).fetchall()
        for row in exhausted:
            db.execute("UPDATE documents SET status='failed' WHERE id=?", (row["document_id"],))
        db.execute("UPDATE jobs SET status='failed',error='Worker lease expired after maximum attempts',"
                   "lease_token=NULL,lease_until=NULL,updated_at=? WHERE status='running' "
                   "AND lease_until<=? AND attempts>=?", (now, now, MAX_ATTEMPTS))
        row = db.execute("SELECT j.*,d.text FROM jobs j JOIN documents d ON d.id=j.document_id "
                         "WHERE (j.status='queued' OR (j.status='running' AND j.lease_until<=?)) "
                         "AND j.attempts<? ORDER BY j.created_at,j.id LIMIT 1", (now, MAX_ATTEMPTS)).fetchone()
        if not row:
            return None
        job = dict(row)
        job["lease_token"] = secrets.token_hex(16)
        job["attempts"] += 1
        db.execute("UPDATE jobs SET status='running',attempts=?,lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                   (job["attempts"], job["lease_token"], now + LEASE_SECONDS, now, job["id"]))
        db.execute("UPDATE documents SET status='indexing' WHERE id=?", (job["document_id"],))
        return job


def publish_chunks(path: str, job: dict, chunks: list[str]) -> bool:
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        owner = db.execute("SELECT 1 FROM jobs WHERE id=? AND lease_token=? AND status='running'",
                           (job["id"], job["lease_token"])).fetchone()
        if not owner:
            return False  # Deleted, replaced, or reclaimed: never publish stale work.
        db.execute("DELETE FROM chunks WHERE document_id=?", (job["document_id"],))
        db.executemany("INSERT INTO chunks(document_id,chunk_number,text) VALUES(?,?,?)",
                       [(job["document_id"], number, text) for number, text in enumerate(chunks)])
        db.execute("UPDATE documents SET status='ready' WHERE id=?", (job["document_id"],))
        db.execute("UPDATE jobs SET status='succeeded',error=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                   (time.time(), job["id"]))
        return True


def fail_job(path: str, job: dict):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        owner = db.execute("SELECT attempts FROM jobs WHERE id=? AND lease_token=? AND status='running'",
                           (job["id"], job["lease_token"])).fetchone()
        if not owner:
            return
        exhausted = owner["attempts"] >= MAX_ATTEMPTS
        db.execute("UPDATE jobs SET status=?,error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                   ("failed" if exhausted else "queued", "Indexing failed; inspect worker logs", time.time(), job["id"]))
        db.execute("UPDATE documents SET status=? WHERE id=?", ("failed" if exhausted else "queued", job["document_id"]))


def process_one(path: str) -> bool:
    job = claim_job(path)
    if not job:
        return False
    try:
        publish_chunks(path, job, chunk_text(job["text"]))
    except Exception:
        logger.exception("Indexing job %s failed", job["id"])
        fail_job(path, job)
    return True
