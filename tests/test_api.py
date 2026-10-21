import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.database import connect
from app.indexing import claim_job, chunk_text, fail_job, process_one, publish_chunks
from app.main import create_app
from app.security import token_hash


@pytest.fixture
def setup(tmp_path):
    path = str(tmp_path / "vault.db")
    with TestClient(create_app(path)) as client:
        yield client, path


def account(client, username="alice"):
    body = {"username": username, "password": "Example-password-123"}
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    response = client.post("/api/v1/auth/login", json=body)
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def collection(client, headers, name="Research"):
    response = client.post("/api/v1/collections", headers=headers, json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def ingest(client, headers, collection_id, text="FastAPI uses Python type hints for validation."):
    response = client.post(f"/api/v1/collections/{collection_id}/documents", headers=headers,
                           json={"title": "API notes", "text": text})
    assert response.status_code == 202, response.text
    return response.json()


def test_auth_password_storage_expiry_and_logout(setup):
    c, path = setup
    assert c.get("/api/v1/collections").status_code == 401
    h = account(c)
    assert c.get("/api/v1/auth/me", headers=h).json()["username"] == "alice"
    with connect(path) as db:
        stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
        assert stored.startswith("pbkdf2_sha256$") and "Example-password-123" not in stored
        token = h["Authorization"].split()[1]
        assert db.execute("SELECT token_hash FROM tokens").fetchone()[0] == token_hash(token)
    assert c.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert c.get("/api/v1/auth/me", headers=h).status_code == 401
    h = account(c, "bobby")
    with connect(path) as db:
        db.execute("UPDATE tokens SET expires_at=?", (time.time() - 1,))
    assert c.get("/api/v1/auth/me", headers=h).status_code == 401


def test_failed_login_rate_limit_and_duplicate_user(setup):
    c, path = setup
    account(c)
    assert c.post("/api/v1/auth/register", json={"username": "ALICE", "password": "Other-password-123"}).status_code == 409
    for _ in range(5):
        assert c.post("/api/v1/auth/login", json={"username": "alice", "password": "Incorrect-password"}).status_code == 401
    limited = c.post("/api/v1/auth/login", json={"username": "alice", "password": "Example-password-123"})
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    with connect(path) as db:
        db.execute("UPDATE login_attempts SET reset_at=0")
    assert c.post("/api/v1/auth/login", json={"username": "alice", "password": "Example-password-123"}).status_code == 200


def test_ingest_worker_search_update_delete(setup):
    c, path = setup
    h = account(c)
    cid = collection(c, h)
    entry = ingest(c, h, cid)
    did, jid = entry["document"]["id"], entry["job"]["id"]
    assert entry["document"]["status"] == "queued"
    assert c.get("/api/v1/search?q=Python", headers=h).json()["hits"] == []
    assert process_one(path)
    assert not process_one(path)
    assert c.get(f"/api/v1/jobs/{jid}", headers=h).json()["status"] == "succeeded"
    hit = c.get("/api/v1/search?q=Python", headers=h).json()["hits"][0]
    assert hit["document_id"] == did and "[Python]" in hit["snippet"]
    assert hit["score"] > 0
    assert c.get(f"/api/v1/collections/{cid}/documents?limit=1", headers=h).json()["total"] == 1
    assert c.put(f"/api/v1/documents/{did}", headers=h, json={"title": "Updated", "text": "SQLite transactions are atomic."}).status_code == 202
    assert c.get("/api/v1/search?q=Python", headers=h).json()["hits"] == []
    assert process_one(path)
    assert c.get("/api/v1/search?q=SQLite", headers=h).json()["hits"][0]["title"] == "Updated"
    assert c.delete(f"/api/v1/documents/{did}", headers=h).status_code == 204
    assert c.get("/api/v1/search?q=SQLite", headers=h).json()["hits"] == []
    assert c.get(f"/api/v1/jobs/{jid}", headers=h).status_code == 404


def test_tenant_isolation_on_every_resource(setup):
    c, path = setup
    alice, bob = account(c), account(c, "bobby")
    cid = collection(c, alice)
    entry = ingest(c, alice, cid, "Secretword confidential research")
    did, jid = entry["document"]["id"], entry["job"]["id"]
    process_one(path)
    for endpoint in [f"/collections/{cid}/documents", f"/documents/{did}", f"/jobs/{jid}", f"/search?q=Secretword&collection_id={cid}"]:
        assert c.get("/api/v1" + endpoint, headers=bob).status_code == 404
    for endpoint in [f"/collections/{cid}", f"/documents/{did}"]:
        assert c.delete("/api/v1" + endpoint, headers=bob).status_code == 404
    assert c.post(f"/api/v1/jobs/{jid}/retry", headers=bob).status_code == 404
    assert c.post(f"/api/v1/collections/{cid}/documents", headers=bob, json={"title": "bad", "text": "bad"}).status_code == 404
    assert c.put(f"/api/v1/documents/{did}", headers=bob, json={"title": "bad", "text": "bad"}).status_code == 404
    assert c.patch(f"/api/v1/collections/{cid}", headers=bob, json={"name": "bad"}).status_code == 404
    assert c.get("/api/v1/search?q=Secretword", headers=bob).json()["hits"] == []
    assert c.get("/api/v1/collections", headers=bob).json() == []
    assert c.get("/api/v1/stats", headers=bob).json()["documents"] == 0


def test_deduplication_and_rollback(setup):
    c, path = setup
    h = account(c)
    cid = collection(c, h)
    first = ingest(c, h, cid)
    second = ingest(c, h, cid, "A second unique document")
    process_one(path)
    process_one(path)
    original = "FastAPI uses Python type hints for validation."
    assert c.post(f"/api/v1/collections/{cid}/documents", headers=h, json={"title": "Duplicate", "text": original}).status_code == 409
    assert c.put(f'/api/v1/documents/{second["document"]["id"]}', headers=h,
                 json={"title": "Duplicate", "text": original}).status_code == 409
    assert c.get("/api/v1/stats", headers=h).json()["indexed_chunks"] == 2
    assert c.get(f'/api/v1/documents/{first["document"]["id"]}', headers=h).json()["status"] == "ready"


def test_search_modes_literal_syntax_and_collection_filter(setup):
    c, path = setup
    h = account(c)
    cid, other = collection(c, h), collection(c, h, "Other")
    ingest(c, h, cid, "Python FastAPI backend")
    ingest(c, h, other, "Python statistics")
    process_one(path)
    process_one(path)
    assert c.get("/api/v1/search?q=Python%20FastAPI&mode=all", headers=h).json()["count"] == 1
    assert c.get("/api/v1/search?q=Python%20FastAPI&mode=any", headers=h).json()["count"] == 2
    assert c.get(f"/api/v1/search?q=Python&collection_id={cid}", headers=h).json()["count"] == 1
    assert c.get('/api/v1/search', params={"q": '"Python" OR * - NOT'}, headers=h).status_code == 200
    assert c.get('/api/v1/search', params={"q": '* " -'}, headers=h).status_code == 422


def test_collection_cascade_and_stale_worker(setup):
    c, path = setup
    h = account(c)
    cid = collection(c, h)
    ingest(c, h, cid)
    job = claim_job(path)
    assert c.delete(f"/api/v1/collections/{cid}", headers=h).status_code == 204
    assert not publish_chunks(path, job, chunk_text(job["text"]))
    assert c.get("/api/v1/stats", headers=h).json()["documents"] == 0
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_replacement_revokes_inflight_lease(setup):
    c, path = setup
    h = account(c)
    entry = ingest(c, h, collection(c, h))
    old = claim_job(path)
    c.put(f'/api/v1/documents/{entry["document"]["id"]}', headers=h, json={"title": "New", "text": "Replacement text"})
    assert not publish_chunks(path, old, ["Old obsolete text"])
    process_one(path)
    assert c.get("/api/v1/search?q=Replacement", headers=h).json()["count"] == 1
    assert c.get("/api/v1/search?q=obsolete", headers=h).json()["count"] == 0


def test_crash_recovery_and_stale_lease_rejection(setup):
    c, path = setup
    h = account(c)
    ingest(c, h, collection(c, h))
    old = claim_job(path, now=100)
    assert claim_job(path, now=101) is None
    reclaimed = claim_job(path, now=161)
    assert reclaimed["id"] == old["id"] and reclaimed["attempts"] == 2
    assert not publish_chunks(path, old, ["obsolete"])
    assert publish_chunks(path, reclaimed, chunk_text(reclaimed["text"]))


def test_failed_jobs_retry_and_maximum_attempts(setup):
    c, path = setup
    h = account(c)
    entry = ingest(c, h, collection(c, h))
    jid = entry["job"]["id"]
    assert c.post(f"/api/v1/jobs/{jid}/retry", headers=h).status_code == 409
    for _ in range(3):
        job = claim_job(path)
        fail_job(path, job)
    assert c.get(f"/api/v1/jobs/{jid}", headers=h).json()["status"] == "failed"
    assert not process_one(path)
    assert c.post(f"/api/v1/jobs/{jid}/retry", headers=h).status_code == 202
    assert process_one(path)
    assert c.get(f"/api/v1/jobs/{jid}", headers=h).json()["attempts"] == 1


def test_expired_final_lease_is_marked_failed(setup):
    c, path = setup
    h = account(c)
    entry = ingest(c, h, collection(c, h))
    for now in [100, 161, 222]:
        assert claim_job(path, now=now)
    assert claim_job(path, now=283) is None
    assert c.get(f'/api/v1/jobs/{entry["job"]["id"]}', headers=h).json()["status"] == "failed"


def test_concurrent_claims_only_one_worker_wins(setup):
    c, path = setup
    h = account(c)
    ingest(c, h, collection(c, h))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_job(path), range(2)))
    assert sum(job is not None for job in claims) == 1


def test_body_and_schema_limits(setup):
    c, path = setup
    h = account(c)
    cid = collection(c, h)
    for body in [{"title": "", "text": "text"}, {"title": "Title", "text": "  "},
                 {"title": "Title", "text": "a" * 100001}, {"title": "Title", "text": "a\x00b"}]:
        assert c.post(f"/api/v1/collections/{cid}/documents", headers=h, json=body).status_code == 422
    assert c.post("/api/v1/auth/login", content=b"a" * 1_048_577).status_code == 413
    assert c.post("/api/v1/collections", headers=h, json={"name": "   "}).status_code == 422


def test_database_reopen_keeps_pending_jobs(tmp_path):
    path = str(tmp_path / "durable.db")
    with TestClient(create_app(path)) as c:
        h = account(c)
        entry = ingest(c, h, collection(c, h))
    process_one(path)
    with TestClient(create_app(path)) as c:
        assert c.get(f'/api/v1/jobs/{entry["job"]["id"]}', headers=h).json()["status"] == "succeeded"


def test_chunk_overlap_and_coverage():
    words = [f"word{i}" for i in range(350)]
    chunks = chunk_text(" ".join(words))
    assert len(chunks) == 3
    assert chunks[0].split()[-30:] == chunks[1].split()[:30]
    assert chunks[-1].split()[-1] == "word349"
    assert chunk_text(" \n ") == []
    with pytest.raises(ValueError):
        chunk_text("text", size=20, overlap=20)
