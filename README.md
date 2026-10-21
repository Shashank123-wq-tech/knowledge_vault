# KnowledgeVault — Medium-weight FastAPI project

A multi-user document search backend: register an account, organize private text documents
into collections, index them through a separate worker, and retrieve ranked matching passages.

This builds directly on the lightweight PocketLedger project. It adds authentication,
authorization, relational data modeling, transactions across multiple tables, durable jobs,
crash recovery, full-text retrieval, and concurrency tests.

**Python application code only.** SQLite stores both data and the search index. No Node.js,
paid APIs, GPU, Redis, Celery, or model downloads are required. Swagger UI at `/docs` is the
interactive interface. Search is **BM25 keyword retrieval**, not embeddings, generated answers,
or semantic search. It is a useful backend foundation for a later RAG system.

## Requirements

Python **3.12 recommended** with SQLite FTS5 enabled; the app checks FTS5 at startup.
Validated with Python 3.12.14. Install packages with internet access. Swagger UI also loads
assets from a CDN; the API and Python demos themselves do not require external services.
Use `requirements-lock.txt` to reproduce the complete tested development environment,
or `requirements.txt` for runtime dependencies only.

## Run on Windows PowerShell

Extract the ZIP and open a terminal inside `knowledge_vault`.

**Terminal 1 — install and run the API:**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run.py
```

**Terminal 2 — same project folder, run the worker:**

```powershell
.\.venv\Scripts\python.exe worker.py
```

Open **http://127.0.0.1:8002/docs**. The API and worker share `data/vault.db` automatically.

**Terminal 3 — run the complete demo and tests:**

```powershell
.\.venv\Scripts\python.exe demo.py
.\.venv\Scripts\python.exe -m pytest -q
```

The demo creates a random account and collection, imports the three bundled notes, waits for
indexing, searches them, prints statistics, deletes the collection, and revokes the session.
An empty random demo account remains in the database. Tests use temporary databases.

## Run on macOS / Linux

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python run.py
```

In separate terminals in this folder, run `.venv/bin/python worker.py` and
`.venv/bin/python demo.py`. Run tests with `.venv/bin/python -m pytest -q`.
The worker also supports `python worker.py --once` to process at most one job and exit.

## Use Swagger manually

1. `POST /api/v1/auth/register` with the following example credentials.
2. `POST /api/v1/auth/login` with the same body.
3. Copy `access_token`. Click **Authorize** and paste only the token into the bearer field.
4. `POST /api/v1/collections` with `{"name":"My study notes"}`. Copy the returned ID.
5. `POST /api/v1/collections/{collection_id}/documents` with the sample document below.
6. Poll the returned `job.id` using `GET /api/v1/jobs/{job_id}` until `status` is `succeeded`.
7. Call `/api/v1/search?q=FastAPI%20validation&mode=all`.

Example account (choose your own password for personal use):

```json
{"username":"shashank","password":"Choose-a-long-password-123"}
```

Example document:

```json
{
  "title": "FastAPI revision notes",
  "text": "FastAPI uses Python type hints. Pydantic handles validation. Dependencies can supply database connections to routes."
}
```

Ingestion returns **202 Accepted** because the indexing work is queued. Documents become
searchable only after the worker succeeds. `/health` checks the API/database, not worker availability.

## Import your own text file

Create an account and collection first, then run:

```powershell
.\.venv\Scripts\python.exe import_document.py samples\fastapi.md --collection-id 1 --username shashank
```

Replace `1` with your actual collection ID. The CLI asks for the password without echoing it,
logs in, reads a UTF-8 `.txt` or `.md` file, sends its contents as JSON, and logs out.
No filesystem path is accepted by the server. PDF, Word, scanned images, and web crawling are not supported.

## API reference

All paths below have the prefix `/api/v1`. Except register/login, all require a bearer token.

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register` | Create account; 201 |
| POST | `/auth/login` | Issue an 8-hour session |
| GET | `/auth/me` | Current user |
| POST | `/auth/logout` | Revoke this session; 204 |
| POST / GET | `/collections` | Create/list your collections |
| PATCH / DELETE | `/collections/{id}` | Rename or delete with all contained documents |
| POST | `/collections/{id}/documents` | Submit title/text and queue indexing; 202 |
| GET | `/collections/{id}/documents` | Paginated document summaries |
| GET | `/documents/{id}` | Original text and metadata |
| PUT | `/documents/{id}` | Replace title/text and queue reindexing; 202 |
| DELETE | `/documents/{id}` | Remove document, chunks, and job; 204 |
| GET | `/jobs/{id}` | Read status/attempts/error |
| POST | `/jobs/{id}/retry` | Retry a failed job; 202 |
| GET | `/search` | Ranked matching chunks |
| GET | `/stats` | Your collection/document/chunk counts |

`GET /health` and `GET /openapi.json` are public. A generated OpenAPI snapshot is also in `docs/openapi.json`.

Document listing accepts `limit` (1–100, default 20) and `offset` (default 0).
Search accepts `q` (1–200 characters, up to 20 words), `mode=any|all`, optional
`collection_id`, and `limit` (1–50, default 10). `count` is the number of returned chunks,
not a total-document count. Multiple matching chunks may belong to the same document.

Each search hit includes original `document_id`, `title`, `collection_id`, zero-based
`chunk_number`, full chunk `text`, highlighted plain-text `snippet`, and `score`.
Higher scores rank first; scores are relative BM25 values, not probabilities.

## Rules and limits

- Usernames: 3–40 ASCII letters/digits/underscores, normalized to lowercase.
- Passwords: 10–128 characters; stored as salted PBKDF2-HMAC-SHA256 hashes.
- Sessions: random opaque tokens; only SHA-256 token hashes are stored. Up to ten sessions per user.
- Login cooldown: after five failed attempts for a username within 60 seconds, further attempts get 429 until the window resets.
- Up to 50 collections and 200 documents per user; up to 100,000 characters per document.
- HTTP bodies are capped at 1 MiB before JSON parsing, including chunked requests.
- Exact duplicate text in the same collection returns 409; titles are not used for deduplication.
- Collections and documents are private. Access to another user's resource returns 404.
- Updates replace both title and text, immediately remove old search chunks, and invalidate old worker leases.
- Deleting a collection deletes its documents, jobs, and search chunks in one transaction.
- Unknown fields and invalid values return 422. Authentication failures return 401.

## Search and worker behavior

The worker splits text on whitespace into 160-word chunks with 30-word overlap. SQLite FTS5
indexes chunk text using its `unicode61` tokenizer. Document titles are metadata, not indexed text.
Query punctuation is removed and words are safely quoted; raw FTS operators are not supported.
`mode=all` requires all query words within one chunk. The index does not provide stemming,
synonyms, spelling correction, embeddings, multilingual semantic understanding, or reranking.
BM25 corpus statistics are shared across the local index; the SQL ownership filter restricts returned content.

Workers claim jobs under `BEGIN IMMEDIATE` using a random 60-second lease. If a worker dies,
another worker can reclaim the job after the lease expires. Only the current lease holder may
publish. The chunk index and success status commit atomically. Errors retry automatically up
to three attempts; after that, the owner can call the retry endpoint. A deleted or replaced
document cannot be resurrected by an old worker.

This is at-least-once processing with guarded, idempotent publication. A task can be computed
more than once after a crash, while stale results cannot overwrite a newer version.

## Configuration and persistence

`VAULT_DB_PATH` sets the SQLite file location. By default it is anchored to the project folder,
so both processes use the same file even when launched from different working directories.
The app reads environment variables directly; `.env` files are not automatically loaded.

```powershell
$env:VAULT_DB_PATH = "C:\my-project-data\vault.db"
```

Set that variable consistently in both API and worker terminals. For Bash:
`export VAULT_DB_PATH='/absolute/path/vault.db'`.

For a small server, install runtime dependencies, configure one persistent local database file,
run `python run.py --host 0.0.0.0 --port 8002` and `python worker.py` under supervision.
Both must share the same local disk. Add HTTPS at the hosting boundary. Stop both processes
before copying the entire `data` directory for a simple backup, or use SQLite's backup API.
Do not copy only a live `.db` file without its WAL state. This project has not been hosted online.

This version targets local learning and modest single-machine use. It has no distributed queue,
refresh tokens, password reset, email verification, account deletion, file encryption, or automated
schema migrations. Public registration and the per-username cooldown are not a complete abuse
prevention system; a public service needs edge rate limits and account lifecycle controls.
SQLite serializes writes, so move to a server database and external queue if workload demands it.

## Source guide

| File | Responsibility |
|---|---|
| `app/main.py` | App factory, lifespan, middleware, health, error handling |
| `app/routes.py` | Auth, collections, documents, jobs, search, stats |
| `app/schemas.py` | Validated request and response contracts |
| `app/security.py` | Passwords, token digests, current-user dependency |
| `app/database.py` | Tables, foreign keys, FTS index, synchronization triggers |
| `app/indexing.py` | Chunking, job claim, lease checks, publication, failures |
| `app/middleware.py` | Bounded request-body buffering |
| `worker.py` | Separate worker process and polling loop |
| `client.py`, `demo.py`, `import_document.py` | Python-only API clients |
| `tests/test_api.py` | Authentication, ownership, retrieval, lifecycle, worker recovery |
| `docs/WALKTHROUGH.md` | Architecture, code reading order, learning milestones |
| `VERIFICATION.md` | Tests and real-server demo results |

## Troubleshooting

| Symptom | Check |
|---|---|
| Documents stay queued | Start `worker.py`; confirm both processes use the same database |
| Jobs remain running after restart | Wait up to 60 seconds for the old lease to expire |
| 401 after a few hours | Log in again; sessions expire after 8 hours |
| 409 on repeated import | Identical text already exists in this collection |
| Empty search results | Check job success, spelling, chunk-level `all` matching, and collection ID |
| No FTS5 module | Install a Python build with SQLite FTS5 enabled |
| Port already in use | `python run.py --port 8012`; pass matching `--base-url` to clients |
| Import cannot decode text | Save the input file as UTF-8 |

## Official references

- https://fastapi.tiangolo.com/advanced/events/
- https://fastapi.tiangolo.com/tutorial/testing/
- https://fastapi.tiangolo.com/tutorial/dependencies/
- https://www.sqlite.org/fts5.html
- https://docs.python.org/3/library/sqlite3.html
- https://docs.python.org/3/library/hashlib.html
