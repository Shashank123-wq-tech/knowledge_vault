# Verification record

Verified on 21 October 2026 in a Linux environment with Python 3.12.14.

| Check | Result |
|---|---|
| `python -m pytest -q` | **15 passed** |
| Start real Uvicorn API process | Passed |
| Start separate worker process | Passed |
| HTTP health check | Passed |
| Swagger HTML route | Passed |
| OpenAPI generation | Passed; snapshot in `docs/openapi.json` |
| Real HTTP demo | Passed: registration, login, ingestion, worker indexing, retrieval, cleanup, logout |
| Python source parsing | Passed |

Tests cover:

- Password hashing, token digests, expiration, logout, duplicate usernames, login cooldown.
- Document ingestion, ranked keyword retrieval, full replacement and deletion.
- Cross-user isolation for collections, documents, jobs, mutations, search, and statistics.
- Duplicate-content conflicts and rollback without losing the existing index.
- Query modes, collection filtering, literal query handling, and request-size limits.
- Collection deletion and invalidation of in-flight worker results.
- Reclaiming expired leases, preventing stale publication, retry exhaustion and manual retry.
- Concurrent job claims: one winner for one queued job.
- Queued work surviving application restart; overlapping chunk coverage.

The live demo used three independent processes: API, worker, and Python HTTP client. It
indexed the bundled notes in a fresh temporary database, retrieved a FastAPI passage, then
removed its collection and revoked its session. Output is in `docs/DEMO_OUTPUT.txt`.

One third-party test warning was emitted: Starlette 1.9.0 deprecates its `httpx` TestClient
integration in favor of `httpx2`. The pinned `httpx` integration worked and all tests passed.
No application warnings or test failures occurred. See `requirements-lock.txt` for the complete
tested dependency snapshot. Windows instructions are supplied but were not executed on Windows.

This verifies the included local workflow, not internet deployment, load capacity, or a production security audit.
