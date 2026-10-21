import hashlib
import re
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.config import MAX_DOCUMENTS, TOKEN_TTL
from app.schemas import (CollectionCreate, CollectionOut, Credentials, DocumentCreate, DocumentOut,
                         DocumentPage, IngestOut, JobOut, SearchOut, StatsOut, TokenOut, UserOut)
from app.security import DB, DUMMY_HASH, User, bearer, hash_password, token_hash, verify_password

router = APIRouter(prefix="/api/v1")


def collection_owned(db, collection_id, user_id):
    row = db.execute("SELECT * FROM collections WHERE id=? AND user_id=?", (collection_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Collection not found")
    return dict(row)


def document_owned(db, document_id, user_id):
    row = db.execute("SELECT d.*,(SELECT COUNT(*) FROM chunks WHERE document_id=d.id) AS chunk_count "
                     "FROM documents d JOIN collections c ON c.id=d.collection_id WHERE d.id=? AND c.user_id=?",
                     (document_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    return dict(row)


def job_owned(db, job_id, user_id):
    row = db.execute("SELECT j.* FROM jobs j JOIN documents d ON d.id=j.document_id "
                     "JOIN collections c ON c.id=d.collection_id WHERE j.id=? AND c.user_id=?", (job_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row)


@router.post("/auth/register", response_model=UserOut, status_code=201, tags=["Auth"])
def register(body: Credentials, db: DB):
    password_hash = hash_password(body.password)
    cursor = db.execute("INSERT INTO users(username,password_hash) VALUES(?,?)", (body.username, password_hash))
    return dict(db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone())


@router.post("/auth/login", response_model=TokenOut, tags=["Auth"])
def login(body: Credentials, db: DB):
    now = time.time()
    db.execute("BEGIN IMMEDIATE")
    db.execute("DELETE FROM login_attempts WHERE reset_at<=?", (now,))
    previous = db.execute("SELECT * FROM login_attempts WHERE username=?", (body.username,)).fetchone()
    if previous and previous["failures"] >= 5:
        raise HTTPException(429, "Too many failed logins; retry after the cooldown",
                            headers={"Retry-After": str(max(1, int(previous["reset_at"] - now) + 1))})
    row = db.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    valid = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not valid:
        db.execute("INSERT INTO login_attempts(username,failures,reset_at) VALUES(?,1,?) "
                   "ON CONFLICT(username) DO UPDATE SET failures=failures+1", (body.username, now + 60))
        db.commit()  # Keep failed-attempt accounting even though the HTTP request raises.
        raise HTTPException(401, "Invalid username or password", headers={"WWW-Authenticate": "Bearer"})
    db.execute("DELETE FROM login_attempts WHERE username=?", (body.username,))
    db.execute("DELETE FROM tokens WHERE expires_at<=?", (now,))
    # Keep at most ten active sessions; the oldest session is evicted on the next login.
    db.execute("DELETE FROM tokens WHERE token_hash IN (SELECT token_hash FROM tokens "
               "WHERE user_id=? ORDER BY expires_at DESC LIMIT -1 OFFSET 9)", (row["id"],))
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO tokens(token_hash,user_id,expires_at) VALUES(?,?,?)", (token_hash(token), row["id"], now + TOKEN_TTL))
    return {"access_token": token, "expires_in": TOKEN_TTL}


@router.get("/auth/me", response_model=UserOut, tags=["Auth"])
def me(user: User):
    return user


@router.post("/auth/logout", status_code=204, tags=["Auth"])
def logout(db: DB, user: User, credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    db.execute("DELETE FROM tokens WHERE token_hash=?", (token_hash(credentials.credentials),))
    return Response(status_code=204)


@router.post("/collections", response_model=CollectionOut, status_code=201, tags=["Collections"])
def create_collection(body: CollectionCreate, db: DB, user: User):
    db.execute("BEGIN IMMEDIATE")
    if db.execute("SELECT COUNT(*) FROM collections WHERE user_id=?", (user["id"],)).fetchone()[0] >= 50:
        raise HTTPException(409, "Limit of 50 collections per user reached")
    cursor = db.execute("INSERT INTO collections(user_id,name) VALUES(?,?)", (user["id"], body.name))
    return collection_owned(db, cursor.lastrowid, user["id"])


@router.get("/collections", response_model=list[CollectionOut], tags=["Collections"])
def list_collections(db: DB, user: User):
    return [dict(r) for r in db.execute("SELECT * FROM collections WHERE user_id=? ORDER BY id DESC", (user["id"],))]


@router.patch("/collections/{collection_id}", response_model=CollectionOut, tags=["Collections"])
def rename_collection(collection_id: int, body: CollectionCreate, db: DB, user: User):
    collection_owned(db, collection_id, user["id"])
    db.execute("UPDATE collections SET name=? WHERE id=? AND user_id=?", (body.name, collection_id, user["id"]))
    return collection_owned(db, collection_id, user["id"])


@router.delete("/collections/{collection_id}", status_code=204, tags=["Collections"])
def delete_collection(collection_id: int, db: DB, user: User):
    db.execute("BEGIN IMMEDIATE")
    collection_owned(db, collection_id, user["id"])
    db.execute("DELETE FROM collections WHERE id=? AND user_id=?", (collection_id, user["id"]))
    return Response(status_code=204)


@router.post("/collections/{collection_id}/documents", response_model=IngestOut, status_code=202, tags=["Documents"])
def ingest_document(collection_id: int, body: DocumentCreate, db: DB, user: User):
    db.execute("BEGIN IMMEDIATE")
    collection_owned(db, collection_id, user["id"])
    count = db.execute("SELECT COUNT(*) FROM documents d JOIN collections c ON c.id=d.collection_id WHERE c.user_id=?",
                       (user["id"],)).fetchone()[0]
    if count >= MAX_DOCUMENTS:
        raise HTTPException(409, "Limit of 200 documents per user reached")
    digest = hashlib.sha256(body.text.encode()).hexdigest()
    cursor = db.execute("INSERT INTO documents(collection_id,title,text,content_hash,status) VALUES(?,?,?,?,'queued')",
                        (collection_id, body.title, body.text, digest))
    document_id = cursor.lastrowid
    now = time.time()
    cursor = db.execute("INSERT INTO jobs(document_id,status,created_at,updated_at) VALUES(?,'queued',?,?)", (document_id, now, now))
    return {"document": document_owned(db, document_id, user["id"]), "job": job_owned(db, cursor.lastrowid, user["id"])}


@router.get("/collections/{collection_id}/documents", response_model=DocumentPage, tags=["Documents"])
def list_documents(collection_id: int, db: DB, user: User, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    collection_owned(db, collection_id, user["id"])
    total = db.execute("SELECT COUNT(*) FROM documents WHERE collection_id=?", (collection_id,)).fetchone()[0]
    rows = db.execute("SELECT d.*,(SELECT COUNT(*) FROM chunks WHERE document_id=d.id) AS chunk_count "
                      "FROM documents d WHERE collection_id=? ORDER BY d.id DESC LIMIT ? OFFSET ?", (collection_id, limit, offset))
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/documents/{document_id}", response_model=DocumentOut, tags=["Documents"])
def get_document(document_id: int, db: DB, user: User):
    return document_owned(db, document_id, user["id"])


@router.put("/documents/{document_id}", response_model=IngestOut, status_code=202, tags=["Documents"])
def replace_document(document_id: int, body: DocumentCreate, db: DB, user: User):
    db.execute("BEGIN IMMEDIATE")
    document_owned(db, document_id, user["id"])
    db.execute("UPDATE documents SET title=?,text=?,content_hash=?,status='queued',"
               "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
               (body.title, body.text, hashlib.sha256(body.text.encode()).hexdigest(), document_id))
    # Remove obsolete search results immediately and revoke any worker's old lease.
    db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
    db.execute("UPDATE jobs SET status='queued',attempts=0,error=NULL,lease_token=NULL,lease_until=NULL,updated_at=? "
               "WHERE document_id=?", (time.time(), document_id))
    job_id = db.execute("SELECT id FROM jobs WHERE document_id=?", (document_id,)).fetchone()[0]
    return {"document": document_owned(db, document_id, user["id"]), "job": job_owned(db, job_id, user["id"])}


@router.delete("/documents/{document_id}", status_code=204, tags=["Documents"])
def delete_document(document_id: int, db: DB, user: User):
    db.execute("BEGIN IMMEDIATE")
    document_owned(db, document_id, user["id"])
    db.execute("DELETE FROM documents WHERE id=?", (document_id,))
    return Response(status_code=204)


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["Jobs"])
def get_job(job_id: int, db: DB, user: User):
    return job_owned(db, job_id, user["id"])


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=202, tags=["Jobs"])
def retry_job(job_id: int, db: DB, user: User):
    db.execute("BEGIN IMMEDIATE")
    job = job_owned(db, job_id, user["id"])
    if job["status"] != "failed":
        raise HTTPException(409, "Only a failed job can be retried")
    db.execute("UPDATE jobs SET status='queued',attempts=0,error=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
               (time.time(), job_id))
    db.execute("UPDATE documents SET status='queued' WHERE id=?", (job["document_id"],))
    return job_owned(db, job_id, user["id"])


@router.get("/search", response_model=SearchOut, tags=["Search"])
def search(db: DB, user: User, q: str = Query(min_length=1, max_length=200),
           collection_id: int | None = Query(None, ge=1), mode: Literal["any", "all"] = "any",
           limit: int = Query(10, ge=1, le=50)):
    if collection_id is not None:
        collection_owned(db, collection_id, user["id"])
    terms = re.findall(r"[^\W_]+", q, flags=re.UNICODE)
    if not terms:
        raise HTTPException(422, "Search requires at least one letter or number")
    if len(terms) > 20:
        raise HTTPException(422, "Use at most 20 search terms")
    # Treat operators, quotes and punctuation as input text, not FTS5 query syntax.
    expression = (" OR " if mode == "any" else " AND ").join('"' + term + '"' for term in terms)
    where = " AND d.collection_id=?" if collection_id is not None else ""
    params = [expression, user["id"]]
    if collection_id is not None:
        params.append(collection_id)
    params.append(limit)
    rows = db.execute("SELECT d.id AS document_id,d.collection_id,d.title,ch.chunk_number,ch.text,"
                      "snippet(chunks_fts,0,'[',']',' … ',28) AS snippet,-bm25(chunks_fts) AS score "
                      "FROM chunks_fts JOIN chunks ch ON ch.id=chunks_fts.rowid "
                      "JOIN documents d ON d.id=ch.document_id JOIN collections c ON c.id=d.collection_id "
                      "WHERE chunks_fts MATCH ? AND c.user_id=? AND d.status='ready'" + where +
                      " ORDER BY bm25(chunks_fts),d.id,ch.chunk_number LIMIT ?", params)
    hits = [dict(r) for r in rows]
    return {"query": q, "mode": mode, "count": len(hits), "hits": hits}


@router.get("/stats", response_model=StatsOut, tags=["System"])
def stats(db: DB, user: User):
    statuses = {r["status"]: r["n"] for r in db.execute(
        "SELECT d.status,COUNT(*) AS n FROM documents d JOIN collections c ON c.id=d.collection_id "
        "WHERE c.user_id=? GROUP BY d.status", (user["id"],))}
    return {"collections": db.execute("SELECT COUNT(*) FROM collections WHERE user_id=?", (user["id"],)).fetchone()[0],
            "documents": sum(statuses.values()), "document_statuses": statuses,
            "indexed_chunks": db.execute("SELECT COUNT(*) FROM chunks ch JOIN documents d ON d.id=ch.document_id "
                                         "JOIN collections c ON c.id=d.collection_id WHERE c.user_id=?", (user["id"],)).fetchone()[0]}
